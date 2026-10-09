import time

from kovtest import case
from kovtest.browser import EngineUnavailable

# шум от имитации «нет сети» — не ошибки приложения
NETWORK_NOISE = ("ERR_EMPTY_RESPONSE", "handshake response", "ERR_CONNECTION", "Failed to fetch", "WebSocket",
                 "ERR_INTERNET_DISCONNECTED", "status of 40")


def apk(t):
    if t.settings.target != "apk":
        t.skip("раздел мобильной версии: запуск run.cmd mobile")
    return t.world.apk


# ===== управление симулятором телефона (mobile/tools/simulator) =====

class SimPhone:
    """Симулятор телефона из проекта: приложение в рамке, шторка уведомлений,
    кнопки «Назад», «Домой», «Недавние», сеть. Роль Android-части играет
    страница симулятора, уведомления идут по настоящему каналу сервера."""

    def __init__(self, t, env):
        self.t = t
        engine = t.settings.browsers[0]
        try:
            browser = t.world.browsers.get("S", engine)
        except EngineUnavailable as e:
            t.skip(str(e))
        self.context = browser.new_context(viewport={"width": 1440, "height": 900}, locale="ru-RU",
                                           timezone_id="Europe/Moscow", reduced_motion="reduce")
        self.context.set_default_timeout(t.settings.timeout_ms)
        t.later(self.context.close)
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("console", lambda m: self.errors.append(m.text[:300])
                     if m.type == "error" and not any(n in m.text for n in NETWORK_NOISE) else None)
        self.page.on("pageerror", lambda e: self.errors.append("ошибка скрипта: %s" % e))
        self.page.goto(env.sim_url)
        self.page.wait_for_selector("#appArea iframe.main", timeout=15000)

    def frame(self):
        # рамку берём из самого элемента: под нагрузкой она появляется раньше,
        # чем начинает грузить приложение, и поиск по адресу её ещё не видит
        element = self.page.query_selector("#appArea iframe.main")
        return element.content_frame() if element else None

    def login(self, role):
        account = self.t.account(role)
        f = self.frame()
        f.wait_for_selector(".login-card input[placeholder='Логин']", timeout=15000)
        f.fill(".login-card input[placeholder='Логин']", account.login)
        f.fill(".login-card input[type=password]", account.password)
        f.press(".login-card input[type=password]", "Enter")
        f.wait_for_selector(".app .top-bar .menu-btn", timeout=20000)
        self.page.wait_for_timeout(800)

    def allow_dialogs(self, timeout=6.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.page.locator("#sysdialog:not([hidden])").count():
                self.page.click("#sysdialog button:has-text('Разрешить')")
                self.page.wait_for_timeout(300)
                continue
            if self.page.inner_text("#chan") == "на связи":
                return True
            self.page.wait_for_timeout(200)
        return self.page.inner_text("#chan") == "на связи"

    def enable_notifications(self):
        self.frame().evaluate("() => { Kov.PushClient.enable(); }")
        self.t.require(self.allow_dialogs(15), "симулятор: уведомления не включились, канал: %s" % self.page.inner_text("#chan"))

    def home(self):
        self.page.click("#navbar button[data-k=home]")
        self.page.wait_for_timeout(300)

    def back(self):
        self.page.click("#navbar button[data-k=back]")
        self.page.wait_for_timeout(300)

    def close_app(self):
        self.page.click("#navbar button[data-k=recent]")
        self.page.click("#recents button")
        self.page.wait_for_timeout(300)

    def launch(self):
        self.page.click("#launcher")
        self.page.wait_for_timeout(500)

    def network(self, mode):
        self.page.click("#net button[data-v=%s]" % mode)
        self.page.wait_for_timeout(500)

    def wait_shade(self, text, timeout):
        try:
            self.page.wait_for_function("x => [...document.querySelectorAll('#shade .note')].some(n => n.innerText.includes(x))",
                                        arg=text[:40], timeout=timeout * 1000)
            return True
        except Exception:
            return False

    def tap_notification(self, text):
        self.page.locator("#shade .note", has_text=text[:40]).first.click()
        self.page.wait_for_timeout(800)

    def hash(self):
        f = self.frame()
        try:
            return f.evaluate("() => location.hash") if f else ""
        except Exception:
            return ""

    def wait_hash(self, prefix, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.hash().startswith(prefix):
                return True
            self.page.wait_for_timeout(250)
        return False

    def assert_clean(self, name):
        for message in dict.fromkeys(self.errors):
            self.t.fail("%s: ошибка в консоли — %s" % (name, message))
        self.errors.clear()

    def shot(self, name):
        target = self.t.log.dir / "screenshots" / (name + ".png")
        try:
            self.page.screenshot(path=str(target))
            self.t.result.screenshots.append("screenshots/" + target.name)
        except Exception:
            pass


# ===== жизненный цикл приложения =====

@case("Симулятор: «Домой», «Недавние», закрытие и холодный запуск", layer="mobile", order=1)
def lifecycle(t):
    env = apk(t)
    sim = SimPhone(t, env)
    sim.login("worker_mont")
    sim.home()
    t.check(sim.frame().evaluate("() => document.visibilityState") == "hidden", "свёрнутое приложение осталось видимым")
    sim.launch()
    t.check(sim.frame().evaluate("() => document.visibilityState") == "visible", "развёрнутое приложение осталось невидимым")
    sim.close_app()
    t.check(sim.frame() is None, "после «Смахнуть» приложение осталось запущенным")
    sim.launch()
    sim.frame().wait_for_selector(".app .top-bar .menu-btn", timeout=20000)
    t.check(sim.wait_hash("#/tasks", 10), "холодный запуск открыл не раздел задач: %s" % sim.hash())
    sim.shot("sim_lifecycle")
    sim.assert_clean("жизненный цикл")


@case("Симулятор: «Назад» закрывает окна, затем сворачивает приложение", layer="mobile", order=2)
def back_flow(t):
    env = apk(t)
    sim = SimPhone(t, env)
    sim.login("director")
    f = sim.frame()
    f.evaluate("() => Kov.UI.modal({ title: 'Проверка «Назад»' })")
    sim.back()
    t.check(not f.locator(".modal-root").count(), "«Назад» не закрыло окно")
    f.evaluate("() => window.open(location.pathname.replace('index.html', 'doc.html'), '_blank')")
    sim.page.wait_for_timeout(1000)
    t.check(sim.page.locator("#appArea iframe.child").count() == 1, "окно документа не открылось поверх приложения")
    sim.back()
    t.check(sim.page.locator("#appArea iframe.child").count() == 0, "«Назад» не закрыло окно документа")
    # дальше «Назад» идёт по истории разделов, а в её начале сворачивает приложение — как на телефоне
    for _ in range(5):
        sim.back()
        if not sim.page.locator("#home[hidden]").count():
            break
    t.check(not sim.page.locator("#home[hidden]").count(), "«Назад» в начале истории не свернуло приложение")
    sim.assert_clean("«Назад» в симуляторе")


@case("Симулятор: без сети интерфейс остаётся, запросы и канал восстанавливаются", layer="mobile", order=3)
def network_flow(t):
    env = apk(t)
    sim = SimPhone(t, env)
    sim.login("worker_to")
    sim.enable_notifications()
    sim.page.click("#clearLog")
    sim.network("offline")
    f = sim.frame()
    t.check(f.evaluate("() => Kov.Api.get('employees/%d').then(() => 'ответ', () => 'ошибка')" % t.account("worker_to").id) == "ошибка",
            "без сети запрос к серверу выполнился")
    t.check(f.evaluate("() => navigator.onLine") is False, "без сети navigator.onLine = true")
    t.check(f.locator(".app").count() == 1, "без сети пропал интерфейс")
    sim.network("online")
    t.require(sim.allow_dialogs(20), "канал уведомлений не восстановился после возвращения сети")
    sim.page.wait_for_timeout(2500)
    t.check(f.evaluate("() => Kov.Api.get('employees/%d').then(() => 'ответ', () => 'ошибка')" % t.account("worker_to").id) == "ответ",
            "после возвращения сети запросы не идут")
    reconnects = len([line for line in sim.page.inner_text("#log").split("\n") if "на связи" in line])
    t.check(reconnects == 1, "после возвращения сети канал переподключился %d раз (ожидалось 1)" % reconnects)
    sim.assert_clean("сеть в симуляторе")


@case("Симулятор: скачанный файл появляется в «Загрузках»", layer="mobile", order=4)
def downloads_flow(t):
    env = apk(t)
    sim = SimPhone(t, env)
    sim.login("worker_mont")
    # файл со своего адреса: у симулятора он свой, чужой адрес политика безопасности не пустит
    sim.frame().evaluate("() => Kov.Downloads.download(location.pathname.replace('index.html', 'icons/icon-512.png'), 'icon-512.png')")
    try:
        sim.page.wait_for_function("() => document.querySelector('#downloads').innerText.includes('icon-512.png')", timeout=15000)
        ok = True
    except Exception:
        ok = False
    t.check(ok, "файл не появился в «Загрузках» симулятора")
    sim.shot("sim_downloads")
    sim.assert_clean("загрузки в симуляторе")
