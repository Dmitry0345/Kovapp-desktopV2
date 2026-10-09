import base64
import time
from pathlib import Path

from kovtest import case
from kovtest.flows import hash_of, wait_for


def apk(t):
    if t.settings.target != "apk":
        t.skip("раздел мобильной версии: запуск run.cmd mobile")
    return t.world.apk


# ===== надстройка APK на страницах =====

@case("Надстройка приложения подключена на всех страницах", layer="mobile", order=1)
def overlay_everywhere(t):
    env = apk(t)
    page = t.page("worker_mont", "light", "mobile", path="#/tasks")
    info = page.raw.evaluate("""() => ({ bridge: !!window.KovNative, port: !!window.kovNativePort,
        notif: typeof Notification === 'function' ? Notification.permission : 'нет',
        level: Kov.NotifyMode && Kov.NotifyMode.level, registration: Kov.PushClient && Kov.PushClient.registration })""")
    t.check(info["bridge"] and info["port"], "мост приложения не поднялся: %s" % info)
    t.check(info["level"] == "push", "режим уведомлений не приложения: %s" % info["level"])
    t.check(info["registration"] is None, "у приложения не должно быть service worker для push")
    t.check("hello" in env.commands(page), "страница не представилась телефону (hello)")
    overlay = env.overlay()
    scripts = overlay.get("head_scripts", []) + overlay.get("tail_scripts", [])
    for name in ("index.html", "doc.html", "sheet.html"):
        html = env.served(name)
        missing = [s for s in scripts if '<script src="%s">' % s not in html]
        t.check(not missing, "%s: в странице APK нет скриптов надстройки %s" % (name, missing))
        original = (Path(t.settings.project_dir) / "web_app" / name).read_text(encoding="utf-8")
        stripped = html.replace('<script src="native/dev-bridge.js"></script>', "")   # эмулятор моста — только на ПК
        for s in scripts:
            stripped = stripped.replace('<script src="%s"></script>' % s, "")
        t.check(stripped == original, "%s: надстройка изменила страницу не только вставкой скриптов" % name)
    page.assert_clean("надстройка приложения")


@case("Тексты «про браузер» заменены текстами приложения", layer="mobile", order=2)
def text_patches(t):
    env = apk(t)
    root = Path(t.settings.project_dir) / "web_app"
    for patch in env.patches():
        original = (root / patch["file"]).read_text(encoding="utf-8")
        served = env.served(patch["file"])
        t.check(original.count(patch["find"]) == 1,
                "%s: фрагмент веб-версии для правки не найден ровно один раз — правка устарела (%s)" % (patch["file"], patch["find"][:60]))
        t.check(patch["replace"] in served and patch["find"] not in served,
                "%s: приложение показывает текст веб-версии (%s)" % (patch["file"], patch["find"][:60]))
    # на телефоне настройки — разделами: проверяем текст каждого раздела
    page = t.page("director", "light", "mobile", path="#/settings/look")
    text = ""
    for part in ("look", "notify", "device"):
        page.raw.evaluate("p => { location.hash = '#/settings/' + p; }", part)
        page.wait_ready()
        text += "\n" + page.raw.evaluate("() => document.querySelector('.content').innerText")
    bad = [line for line in text.split("\n") if "браузер" in line.lower() and "сервер" not in line.lower()]
    t.check(not bad, "в «Настройках» приложения упоминается браузер: %s" % bad[:3])
    page.audit("apk_settings")
    page.assert_clean("настройки приложения")


@case("Цвет строки состояния и панели навигации — из темы страницы", layer="mobile", matrix={"theme": "themes"}, order=3)
def bars_color(t):
    env = apk(t)
    page = t.page("worker_mont", t.params["theme"], "mobile", path="#/tasks")
    t.require(wait_for(page, "() => __kovDev.calls('ui.theme').length > 0", 5), "страница не сообщила телефону цвет панелей")
    reported = env.calls(page, "ui.theme")[-1]["color"]
    meta = page.raw.evaluate("() => (document.querySelector('meta[name=\"theme-color\"]') || {}).content || ''")
    t.check(reported.lower() == meta.lower(), "цвет панелей %s, а у страницы %s" % (reported, meta))
    digits = reported.lstrip("#")
    if len(digits) == 6:
        light = sum(int(digits[i:i + 2], 16) for i in (0, 2, 4)) / 765 > 0.5
        t.check(light == (t.params["theme"] == "light"), "цвет панелей %s не подходит к теме %s" % (reported, t.params["theme"]))


# ===== кнопка «Назад» =====

@case("Кнопка «Назад»: окна, меню, выпадающие списки, экран входа", layer="mobile", matrix={"device": "devices"}, order=4)
def back_button(t):
    apk(t)
    page = t.page("director", "light", t.params["device"], path="#/tasks")
    raw = page.raw
    raw.evaluate("() => { Kov.UI.modal({ title: 'Первое окно' }); Kov.UI.modal({ title: 'Второе окно' }); }")
    raw.wait_for_timeout(250)
    first = raw.evaluate("() => KovNative.onBack()")
    left = raw.evaluate("() => [...document.querySelectorAll('.modal-root h2')].map(h => h.textContent)")
    t.check(first is True and left == ["Первое окно"], "«Назад» должно закрыть только верхнее окно: %s, осталось %s" % (first, left))
    raw.evaluate("() => KovNative.onBack()")
    raw.wait_for_timeout(200)
    t.check(not raw.locator(".modal-root").count(), "«Назад» не закрыло последнее окно")
    raw.click(".menu-btn")
    wait_for(page, "() => !!document.querySelector('.side-menu.open')", 3)
    t.check(raw.evaluate("() => KovNative.onBack()") is True and wait_for(page, "() => !document.querySelector('.side-menu.open')", 3),
            "«Назад» не закрыло боковое меню")
    picker = raw.locator(".content button.select.picker")
    if picker.count() and picker.first.is_visible():
        picker.first.click()
        if wait_for(page, "() => [...document.querySelectorAll('.picker-panel')].some(p => !p.hidden)", 3):
            raw.evaluate("() => KovNative.onBack()")
            t.check(wait_for(page, "() => ![...document.querySelectorAll('.picker-panel')].some(p => !p.hidden)", 3),
                    "«Назад» не закрыло выпадающий список")
    t.check(raw.evaluate("() => KovNative.onBack()") is False, "без открытых слоёв «Назад» должно идти по истории")
    login = t.world.browsers.open_login("light", t.params["device"], "B", t)
    t.check(login.raw.evaluate("() => KovNative.onBack()") == "exit", "«Назад» на экране входа должно сворачивать приложение")
    page.assert_clean("кнопка «Назад»")


# ===== «Загрузки» телефона =====

@case("Скачивание: файл целиком попадает в «Загрузки» телефона", layer="mobile", order=5)
def downloads(t):
    env = apk(t)
    page = t.page("worker_mont", "light", "mobile", path="#/tasks")
    icon = (Path(t.settings.project_dir) / "web_app" / "icons" / "icon-512.png").read_bytes()
    page.raw.evaluate("u => Kov.Downloads.download(u, 'icon-512.png')", env.app_url + "icons/icon-512.png")
    t.require(wait_for(page, "() => Object.values(__kovDev.state.blobs).some(b => b.done)", 20),
              "файл, собранный страницей, не попал в «Загрузки»")
    got = page.raw.evaluate("""() => { const b = Object.values(__kovDev.state.blobs).find(x => x.done);
        return { name: b.name, data: b.parts.join('|') }; }""")
    data = b"".join(base64.b64decode(p) for p in got["data"].split("|"))
    t.check(got["name"] == "icon-512.png" and data == icon, "файл в «Загрузках» отличается от исходного (%d из %d байт)" % (len(data), len(icon)))
    name, size = env.download_via(page, lambda: page.raw.evaluate("""u => { const a = document.createElement('a');
        a.href = u; a.download = 'icon-192.png'; document.body.append(a); a.click(); a.remove(); }""", env.app_url + "icons/icon-192.png"))
    t.check(name == "icon-192.png" and size == len((Path(t.settings.project_dir) / "web_app" / "icons" / "icon-192.png").read_bytes()),
            "прямая ссылка скачивания: %s, %d байт" % (name, size))
    page.assert_clean("скачивание")


# ===== сеанс =====

@case("Вход и выход: приложение получает сеанс и отвязывает телефон", layer="mobile", order=6)
def session_flow(t):
    env = apk(t)
    page = t.world.browsers.open_login("light", "mobile", "A", t)
    account = t.account("worker_to")
    raw = page.raw
    raw.fill(".login-card input[placeholder='Логин']", account.login)
    raw.fill(".login-card input[type=password]", account.password)
    raw.keyboard.press("Enter")
    t.require(wait_for(page, "() => !!document.querySelector('.app .top-bar .menu-btn')", 15), "вход в приложение не удался")
    page.wait_ready()
    state = env.state(page)
    session = state.get("session") or {}
    t.check(session.get("userId") == account.id, "телефон не получил сеанс сотрудника: %s" % session.get("userId"))
    t.check((session.get("host") or "").rstrip("/") == env.origin, "сеанс указывает на чужой адрес: %s" % session.get("host"))
    token = session.get("token")
    raw.evaluate("() => { Kov.App.logout(); }")
    raw.locator(".modal-root .modal-foot button", has_text="Выйти").click()
    t.require(wait_for(page, "() => !!document.querySelector('.login-screen .login-card')", 10), "после выхода не показан экран входа")
    cleared = env.state(page).get("cleared") or {}
    t.check(cleared.get("token") == token, "телефон не получил прежний токен для отвязки уведомлений")
    t.check(env.state(page).get("session") is None, "после выхода у телефона остался сеанс")
    page.console.clear()
    page.http_errors.clear()


# ===== уведомления в открытом приложении =====

@case("Push на открытой странице показывается всплывающим, нажатие открывает раздел", layer="mobile", order=7)
def push_in_app(t):
    env = apk(t)
    page = t.page("worker_mont", "light", "mobile", path="#/tasks")
    text = "Всплывающее в приложении %d" % (int(time.time()) % 100000)
    env.emit(page, "push", {"payload": {"title": "Автотест", "body": text, "url": "#/notifications", "tag": "autotest-apk",
                                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}})
    t.check(wait_for(page, "x => [...document.querySelectorAll('#kov-toasts .kov-toast')].some(e => e.innerText.includes(x))", 10, text),
            "уведомление не показано в открытом приложении")
    env.emit(page, "navigate", {"url": "#/notifications", "tag": "none"})
    t.check(wait_for(page, "() => location.hash.startsWith('#/notifications')", 10), "нажатие на уведомление не открыло раздел")
    env.emit(page, "navigate", {"url": "javascript:alert(1)"})
    page.raw.wait_for_timeout(400)
    t.check(hash_of(page).startswith("#/notifications"), "небезопасный адрес из уведомления не отброшен: %s" % hash_of(page))
    page.raw.evaluate("() => { __kovDev.state.permission = 'granted'; }")
    env.emit(page, "permission", "granted")          # как при возвращении в приложение после настроек телефона
    page.raw.wait_for_timeout(200)
    page.raw.evaluate("() => new Notification('Задача', { body: 'Срок завтра', tag: 'notification-7', data: { url: '#/tasks/7' } })")
    t.require(wait_for(page, "() => __kovDev.state.shown.length > 0", 5), "системное уведомление не передано телефону")
    shown = env.state(page)["shown"][-1]
    t.check(shown["tag"] == "notification-7" and shown["url"] == "#/tasks/7" and shown["kind"] == "notification",
            "уведомление телефону передано не так, как у сайта: %s" % shown)
    page.assert_clean("уведомления в приложении")


@case("Приглашение включить уведомления: телефон спрашивает разрешение, «Разрешить» включает", layer="mobile",
      matrix={"device": "devices"}, order=8)
def permission_offer(t):
    env = apk(t)
    page = t.page("worker_to", "light", t.params["device"], path="#/tasks")
    page.raw.evaluate("() => { sessionStorage.setItem('kov.autotest.offer', '1'); localStorage.removeItem('kov.notify.offer'); }")
    page.raw.reload()
    page.wait_ready()
    t.require(wait_for(page, "() => !!document.querySelector('.kov-perm-offer')", 10), "приглашение включить уведомления не показано")
    t.screenshot(page, "apk_perm_offer_%s" % t.params["device"])
    page.raw.click(".kov-perm-offer__allow")
    t.check(wait_for(page, "() => !document.querySelector('.kov-perm-offer')", 5), "«Разрешить» не убрало приглашение")
    t.check(wait_for(page, "() => __kovDev.calls('push.enable').length > 0", 5), "после разрешения приложение не включило уведомления")
    order = env.commands(page)
    t.check(order.index("notify.request") < order.index("push.enable"), "уведомления включены раньше, чем телефон спросил разрешение")
    t.check(page.raw.evaluate("() => Notification.permission") == "granted", "после «Разрешить» уведомления не разрешены")
    page.assert_clean("приглашение включить уведомления")
