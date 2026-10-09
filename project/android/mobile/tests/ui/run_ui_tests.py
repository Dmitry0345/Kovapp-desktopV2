#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Проверка приложения на ПК: внешний вид, права по ролям, надстройка.

Сравниваются два варианта одного интерфейса на одном тестовом сервере:
  web — веб-версия как её отдаёт сервер (/app/);
  apk — те же файлы с надстройкой APK (tools/devserver.py + эмулятор моста).

    python mobile/tests/ui/run_ui_tests.py [overlay] [fixes] [sim] [live] [rights] [latest] [parity] [--quick]
    (без наборов — все; latest — последние версии v45–v67)

Отчёт: mobile/tests/ui/report/ (report.md, report.json, снимки расхождений).
Нужен Python-пакет playwright с Chromium (pip install playwright;
python -m playwright install chromium).
"""

import base64
import datetime as dt
import io
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MOBILE = os.path.abspath(os.path.join(HERE, "..", ".."))
ROOT = os.path.dirname(MOBILE)
REPORT = os.path.join(HERE, "report")
FIXTURES = os.path.join(MOBILE, "tests", "fixtures")
sys.path.insert(0, os.path.join(MOBILE, "tests", "mock"))
sys.path.insert(0, os.path.join(MOBILE, "tools"))

from PIL import Image, ImageChops  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

import devserver  # noqa: E402
import mock_server  # noqa: E402

MOCK_PORT = DEV_PORT = 0            # свободные порты выбираются при запуске
WEB = APK = APK_HTTP = SIM = ""
NOW = dt.datetime.combine(dt.date.today(), dt.time(10, 0))

RESULTS = []
OBSERVATIONS = []


def check(suite, name, ok, detail=""):
    RESULTS.append({"suite": suite, "name": name, "ok": bool(ok), "detail": detail if not ok else ""})
    print(("  OK   " if ok else "  FAIL ") + name + ("" if ok else "  — " + str(detail)[:300]))
    return ok


# ── серверы ──────────────────────────────────────────────────────────────────
def start_servers():
    global MOCK_PORT, DEV_PORT, WEB, APK, APK_HTTP, SIM
    mock = mock_server.make_server("127.0.0.1", 0)
    MOCK_PORT = mock.server_address[1]
    threading.Thread(target=mock.serve_forever, daemon=True).start()
    dev, _ = devserver.make_server(["--port", "0", "--upstream", "http://127.0.0.1:%d" % MOCK_PORT, "--bridge", "--web", mock_server.WEB])
    DEV_PORT = dev.server_address[1]
    threading.Thread(target=dev.serve_forever, daemon=True).start()
    WEB = "http://127.0.0.1:%d/app/index.html" % MOCK_PORT
    APK = "http://127.0.0.1:%d/app/index.html" % DEV_PORT
    APK_HTTP = "http://kovapp.test:%d/app/index.html" % DEV_PORT      # не «защищённый» адрес: запасные пути
    sim, _ = devserver.make_server(["--port", "0", "--upstream", "http://127.0.0.1:%d" % MOCK_PORT, "--sim",
                                    "--upstream-is-mock", "--web", mock_server.WEB])
    threading.Thread(target=sim.serve_forever, daemon=True).start()
    SIM = "http://127.0.0.1:%d/__sim/" % sim.server_address[1]
    return mock, dev


def login(role):
    data = urllib.parse.urlencode({"username": role, "password": "test"}).encode()
    req = urllib.request.Request("http://127.0.0.1:%d/login" % MOCK_PORT, data=data, method="POST")
    r = json.load(urllib.request.urlopen(req))
    return r["access_token"], r["user_id"]


def new_page(browser, role, theme="light", viewport=(390, 844), dpr=1):
    ctx = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]}, device_scale_factor=dpr,
                              locale="ru-RU", timezone_id="Europe/Moscow", color_scheme="dark" if theme == "dark" else "light")
    script = "localStorage.setItem('kov.theme', %s); localStorage.setItem('kov.notify.offer', String(Date.now()));" % json.dumps(theme)
    if role:
        token, uid = login(role)
        script += ("localStorage.setItem('kov.auth', JSON.stringify({host: location.origin, token: %s, userId: %d}));"
                   % (json.dumps(token), uid))
    ctx.add_init_script(script)
    page = ctx.new_page()
    page.clock.set_fixed_time(NOW)
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)[:300]))
    page.on("console", lambda m: page.errors.append(m.text[:300]) if m.type == "error" and "status of 40" not in m.text
            and "Cross-Origin-Opener-Policy header has been ignored" not in m.text else None)
    return ctx, page


def settle(page):
    page.wait_for_load_state("networkidle")
    try:
        page.wait_for_function("() => { const b = document.querySelector('.link-bar'); return !b || b.hidden; }", timeout=4000)
    except Exception:
        pass
    page.evaluate("() => document.fonts ? document.fonts.ready : null")
    page.wait_for_timeout(350)


def open_route(page, base, route):
    page.goto(base + route)
    settle(page)
    if route:   # после входа — дождаться применения сохранённых темы и масштаба, а не полагаться на паузу
        try:
            page.wait_for_function("() => document.documentElement.dataset.settings === 'applied'", timeout=6000)
            page.wait_for_timeout(150)
        except Exception:
            pass


# ── 1. внешний вид: веб-версия и APK побайтно одинаковы ──────────────────────
SCREENS = [("login", None, ""), ("tasks", "director", "#/tasks"), ("task", "director", "#/tasks/101"),
           ("projects", "director", "#/projects"), ("project", "director", "#/projects/1"),
           ("organizations", "director", "#/organizations"), ("equipment", "director", "#/equipment"),
           ("employees", "director", "#/employees"), ("notifications", "director", "#/notifications"),
           ("messenger", "director", "#/messenger"), ("csv", "director", "#/csv"),
           ("tasks", "worker", "#/tasks"), ("task", "worker", "#/tasks/101"), ("equipment", "worker", "#/equipment"),
           ("notifications", "worker", "#/notifications"), ("organizations", "worker", "#/organizations")]
VIEWPORTS = [(320, 568), (360, 800), (390, 844), (412, 915), (768, 1024), (844, 390), (1280, 800)]


def screenshot_pair(browser, role, route, theme, vp, dpr=1):
    shots = []
    for base in (WEB, APK):
        ctx, page = new_page(browser, role, theme, vp, dpr)
        open_route(page, base, route)
        shots.append((page.screenshot(full_page=False, animations="disabled", caret="hide"), list(page.errors)))
        ctx.close()
    return shots


def compare(a_png, b_png):
    """(число отличающихся пикселей, наибольшая разница канала, картинки)."""
    a = Image.open(io.BytesIO(a_png)).convert("RGB")
    b = Image.open(io.BytesIO(b_png)).convert("RGB")
    if a.size != b.size:
        return None, 255, a, b
    diff = ImageChops.difference(a, b)
    if diff.getbbox() is None:
        return 0, 0, a, b
    worst = max(hi for lo, hi in diff.getextrema())
    count = diff.convert("L").point(lambda v: 255 if v else 0).histogram()[255]
    return count, worst, a, b


# Одна и та же страница в Chromium, снятая дважды, иногда расходится на единицу
# яркости в паре десятков пикселей сглаживания краёв (проверено: веб-версия сама
# с собой). Такое совпадение считается полным, но показывается отдельно.
NOISE_MAX_DELTA = 2
NOISE_MAX_PIXELS = 200
# сглаживание краёв текста и значков: несколько точек, разница меньше 16/255 — глазом не видно;
# настоящее влияние APK даёт сотни точек или крупную разницу
AA_MAX_DELTA = 16
AA_MAX_PIXELS = 20
# край скруглённой полосы дробной ширины (полоска «Количество» в оборудовании) даёт до 3–4/255
# в паре пикселей; расхождение вёрстки — это сотни пикселей с большой разницей
EDGE_MAX_DELTA = 4
EDGE_MAX_PIXELS = 10


def suite_parity(browser, quick):
    print("\n1. Внешний вид: веб-версия и APK на одном сервере")
    os.makedirs(os.path.join(REPORT, "parity"), exist_ok=True)
    viewports = VIEWPORTS[:3] + VIEWPORTS[-1:] if quick else VIEWPORTS
    total = same = 0
    noise = []
    for theme in ("light", "dark"):
        for name, role, route in SCREENS:
            for vp in viewports:
                (a, ea), (b, eb) = screenshot_pair(browser, role, route, theme, vp)
                count, worst, ia, ib = compare(a, b)
                total += 1
                label = "%s/%s %s %dx%d" % (role or "вход", name, theme, vp[0], vp[1])
                if count is not None and count and ((worst <= NOISE_MAX_DELTA and count <= NOISE_MAX_PIXELS) or (worst <= AA_MAX_DELTA and count <= AA_MAX_PIXELS)
                                                    or (worst <= EDGE_MAX_DELTA and count <= EDGE_MAX_PIXELS)):
                    noise.append("%s: %d пикс., разница %d/255" % (label, count, worst))
                    count = 0
                if count == 0:
                    same += 1
                    if vp == (390, 844) or (vp == (1280, 800) and theme == "light"):
                        ia.save(os.path.join(REPORT, "parity", "%s_%s_%s_%dx%d.png" % (role or "login", name, theme, vp[0], vp[1])))
                else:
                    stem = os.path.join(REPORT, "parity", "DIFF_%s_%s_%s_%dx%d" % (role or "login", name, theme, vp[0], vp[1]))
                    ia.save(stem + "_web.png")
                    ib.save(stem + "_apk.png")
                    check("parity", label, False, "отличаются пикселей: %s, наибольшая разница %s/255" % (count, worst))
                if ea or eb:
                    check("parity", label + ": без ошибок скриптов", not (ea or eb), (ea or eb)[:2])
    check("parity", "снимков совпало: %d из %d (из них с шумом сглаживания: %d)" % (same, total, len(noise)), same == total)
    for n in noise:
        OBSERVATIONS.append("шум сглаживания (не влияние APK): " + n)
    # экран «Настройки»: отличия ровно в текстах про браузер (overlay/web-patches.json) и описании режима уведомлений
    texts = {}
    for label, base in (("web", WEB), ("apk", APK)):
        ctx, page = new_page(browser, "director")
        texts[label] = ""
        for part in ("look", "notify", "device"):      # на телефоне настройки — разделами
            open_route(page, base, "#/settings/" + part)
            texts[label] += "\n" + page.evaluate("() => document.querySelector('.content').innerText")
        ctx.close()
    web_lines, apk_lines = set(texts["web"].split("\n")), set(texts["apk"].split("\n"))
    only_web, only_apk = sorted(web_lines - apk_lines), sorted(apk_lines - web_lines)
    browserish = [l for l in only_apk if "браузер" in l.lower() and "сервер" not in l.lower()]
    check("parity", "настройки: в APK нет упоминаний браузера (%d строк отличаются)" % len(only_apk), not browserish, browserish)
    with open(os.path.join(REPORT, "settings-diff.txt"), "w", encoding="utf-8") as f:
        f.write("Только в веб-версии:\n" + "\n".join(only_web) + "\n\nТолько в APK:\n" + "\n".join(only_apk) + "\n")


# ── 2. права по ролям (docs/permissions.md) ──────────────────────────────────
ROLES = ["director", "admin", "head", "head_project", "head_to", "worker", "montage", "to", "contract", "stock", "accountant"]
MANAGEMENT = {"director", "admin"}
HEADS = {"head", "head_project", "head_to"}
ALL_MENU = ["Уведомления", "Задачи", "Проекты", "Организации", "Оборудование", "Работники", "Файлы CSV", "Профиль", "Настройки", "Мессенджер"]


def expected(role):
    m, h = role in MANAGEMENT, role in HEADS
    menu = [x for x in ALL_MENU if not (x == "Проекты" and role in ("montage", "to")) and not (x == "Файлы CSV" and not m)]
    return {
        "menu": menu,
        "create_task": m or h,
        "add_org": m or h or role == "contract",
        "create_project": m or h or role == "contract",
        "add_employee": m,
        "equipment_add": m or role == "accountant",
        "equipment_issue": m or role == "stock",
        # склад проекта открыт монтажному отделу (permissions.md), но рядовому монтажнику
        # закрыт раздел «Проекты» — через проект до склада он не дойдёт (выдачи он
        # подтверждает в «Уведомлениях»); так и в веб-версии
        "warehouse": m or role in ("head", "stock", "accountant"),
        "notify_create": m or h,
        "csv": m,
    }


INVENTORY_JS = """() => {
  const vis = el => { if (!el.isConnected) return false; const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const root = document.querySelector('.content') || document.body;
  const out = [...root.querySelectorAll('button, a.btn, [role=button]')].filter(vis).map(b => {
    // доступное имя: aria-label (у кнопок с короткой подписью для телефона), иначе текст или подсказка
    const label = b.getAttribute('aria-label') || (b.textContent || '').replace(/\\s+/g, ' ').trim() || b.getAttribute('title') || '';
    return label + (b.classList.contains('is-locked') ? ' [замок]' : '');
  });
  return [...new Set(out)].sort();
}"""
MENU_JS = """() => [...document.querySelectorAll('.side-menu nav a, .side-menu nav button')].map(a => a.textContent.replace(/\\s+/g, ' ').replace(/\\s*\\d+$/, '').trim()).filter(Boolean)"""


def role_facts(browser, base, role):
    ctx, page = new_page(browser, role, viewport=(1280, 800))
    facts = {}
    open_route(page, base, "#/tasks")
    facts["menu"] = page.evaluate(MENU_JS)
    facts["inv_tasks"] = page.evaluate(INVENTORY_JS)
    for key, route in (("inv_orgs", "#/organizations"), ("inv_projects", "#/projects"), ("inv_employees", "#/employees"),
                       ("inv_equipment", "#/equipment"), ("inv_notifications", "#/notifications")):
        open_route(page, base, route)
        facts[key] = page.evaluate(INVENTORY_JS)
    open_route(page, base, "#/tasks/new")
    facts["create_task"] = page.evaluate("() => location.hash") == "#/tasks/new" and "Новая задача" in page.evaluate("() => document.querySelector('.top-bar').innerText")
    open_route(page, base, "#/csv")
    facts["csv"] = page.evaluate("() => location.hash") == "#/csv"
    if "Проекты" in facts["menu"]:
        open_route(page, base, "#/projects/1/warehouse")
        page.wait_for_timeout(500)
        text = page.evaluate("() => document.querySelector('.content').innerText")
        facts["warehouse"] = "Нет доступа" not in text and "403" not in text
    else:
        facts["warehouse"] = False
    # отказ сервера в складе у ролей без права — ожидаемый путь, а не ошибка скрипта
    facts["errors"] = [e for e in page.errors if "Нет доступа к складу проекта" not in e]
    ctx.close()
    return facts


def derive(f):
    has = lambda inv, label: any(x == label for x in f[inv])
    return {
        "menu": f["menu"],
        "create_task": f["create_task"],
        "add_org": has("inv_orgs", "Добавить организацию"),
        "create_project": has("inv_projects", "Создать проект"),
        "add_employee": has("inv_employees", "Добавить работника"),
        "equipment_add": has("inv_equipment", "Добавить"),
        "equipment_issue": has("inv_equipment", "Выдать"),
        "warehouse": f["warehouse"],
        "notify_create": has("inv_notifications", "Создать уведомление"),
        "csv": f["csv"],
    }


def suite_rights(browser):
    print("\n2. Права по ролям (docs/permissions.md), веб-версия и APK")
    table = {}
    for role in ROLES:
        web, apk = role_facts(browser, WEB, role), role_facts(browser, APK, role)
        same = {k: web[k] for k in web if k != "errors"} == {k: apk[k] for k in apk if k != "errors"}
        check("rights", "%s: веб-версия и APK показывают одинаковые разделы и кнопки" % role, same,
              {k: (web[k], apk[k]) for k in web if k != "errors" and web[k] != apk[k]})
        got, want = derive(apk), expected(role)
        wrong = {k: (got[k], want[k]) for k in want if got[k] != want[k]}
        check("rights", "%s: соответствует permissions.md" % role, not wrong, wrong)
        if web["errors"] or apk["errors"]:
            check("rights", "%s: без ошибок скриптов" % role, False, (web["errors"] or apk["errors"])[:2])
        table[role] = got
    with open(os.path.join(REPORT, "rights.json"), "w", encoding="utf-8") as f:
        json.dump(table, f, ensure_ascii=False, indent=1)


# ── 3. надстройка APK ────────────────────────────────────────────────────────
def dev(page, js, arg=None):
    if js.lstrip().startswith("async"):
        js = "(arg) => Promise.race([(%s)(arg), new Promise((_, no) => setTimeout(() => no(new Error('шаг теста не завершился за 60 с')), 60000))])" % js
    return page.evaluate(js, arg)


def suite_overlay(browser):
    print("\n3. Надстройка приложения (эмулятор моста Android)")
    # вход через экран входа
    ctx, page = new_page(browser, None)
    open_route(page, APK, "")
    check("overlay", "мост поднят, браузерные уведомления заменены", dev(page, "() => !!window.KovNative && Notification.permission === 'default' && Kov.NotifyMode.level === 'push'"))
    check("overlay", "«Назад» на экране входа — выход из приложения", dev(page, "() => KovNative.onBack()") == "exit")
    page.fill(".login-screen input[type=text], .login-screen input:not([type])", "worker")
    page.fill(".login-screen input[type=password]", "test")
    page.keyboard.press("Enter")
    page.wait_for_function("() => !!document.querySelector('.app')", timeout=8000)
    settle(page)
    s = dev(page, "() => __kovDev.state.session")
    check("overlay", "после входа приложение получило сеанс своего сервера", s and s.get("userId") == 5 and s.get("host", "").startswith("http://127.0.0.1"), s)
    check("overlay", "цвет системных панелей передан из theme-color", "#FFFFFF" in dev(page, "() => __kovDev.state.themes"))

    # «Назад»: окно, меню, чат
    dev(page, "() => Kov.UI.modal({ title: 'Проверка', body: () => document.createTextNode('текст') })")
    page.wait_for_timeout(100)
    closed = dev(page, "() => { const r = KovNative.onBack(); return [r, document.querySelectorAll('.modal-root').length]; }")
    check("overlay", "«Назад» закрывает верхнее окно", closed == [True, 0], closed)
    dev(page, "() => Kov.UI.modal({ title: 'Первое' }) && Kov.UI.modal({ title: 'Второе' })")
    page.wait_for_timeout(100)
    two = dev(page, "() => { KovNative.onBack(); return [...document.querySelectorAll('.modal-root h2')].map(h => h.textContent); }")
    check("overlay", "«Назад» закрывает только верхнее из двух окон", two == ["Первое"], two)
    dev(page, "() => KovNative.onBack()")
    page.click(".menu-btn")
    page.wait_for_timeout(200)
    menu = dev(page, "() => { const r = KovNative.onBack(); return [r, !!document.querySelector('.side-menu.open')]; }")
    check("overlay", "«Назад» закрывает боковое меню", menu == [True, False], menu)
    check("overlay", "«Назад» без открытых слоёв отдаётся истории", dev(page, "() => KovNative.onBack()") is False)

    # уведомления: включение, входящее при открытом приложении, переход по нажатию
    st = dev(page, "() => Kov.PushClient.status()")
    check("overlay", "состояние уведомлений: поддерживаются, сервер готов, не включены",
          st.get("supported") and st.get("serverEnabled") and not st.get("subscribed"), st)
    r = dev(page, "() => Kov.PushClient.enable()")
    calls = dev(page, "() => __kovDev.log.map(m => m.cmd)")
    check("overlay", "включение: системный запрос разрешения, затем регистрация устройства",
          r.get("push") and calls.index("notify.request") < calls.index("push.enable"), (r, calls[-6:]))
    st = dev(page, "() => Kov.PushClient.status()")
    check("overlay", "после включения устройство подписано", st.get("subscribed") is True, st)
    # телефон выдал разрешение сам (при установке), а к серверу не привязан: вход в приложение привязывает сразу
    calls = dev(page, """async () => { Kov.PushClient.disable && await Kov.PushClient.disable(); localStorage.removeItem('kov.push.off');
        __kovDev.state.permission = 'granted'; __kovDev.state.push.enabled = false; __kovDev.state.push.registered = false;
        __kovDev.log.length = 0; await Kov.PushClient.sync(); return __kovDev.log.map(m => m.cmd); }""")
    check("overlay", "разрешение уже есть, телефон не привязан — привязывается сам (push.enable), переключатель не врёт",
          "push.enable" in calls, calls)
    calls = dev(page, """async () => { await Kov.PushClient.disable(); __kovDev.state.push.enabled = false; __kovDev.log.length = 0;
        await Kov.PushClient.sync(); return __kovDev.log.map(m => m.cmd); }""")
    check("overlay", "выключил уведомления сам — при следующем входе они сами не включаются", "push.enable" not in calls, calls)
    dev(page, "() => { localStorage.removeItem('kov.push.off'); return Kov.PushClient.enable(); }")
    # Google на телефоне отказал (SERVICE_NOT_AVAILABLE, Huawei с HMS): собственный канал постоянный —
    # видна понятная причина и как убрать значок «Kovapp на связи»
    dev(page, """() => { Object.assign(__kovDev.state.push, { enabled: true, registered: true, mode: 'direct', serverSupported: true,
        gms: true, fcmApp: true, fcmServer: true, fcmExpected: false, fcmError: 'SERVICE_NOT_AVAILABLE', serviceHidden: false,
        battery: false, vendor: 'huawei' }); return true; }""")
    open_route(page, APK, "#/settings/device")
    page.wait_for_timeout(1500)
    txt = dev(page, "() => document.querySelector('.content').innerText")
    check("overlay", "Google отказал на телефоне: понятная причина и шаги «Убрать значок» и «работа в фоне»",
          "не подключаются к службе уведомлений Google" in txt and "Убрать значок" in txt and "Запуск приложений" in txt, txt[-500:])
    dev(page, """() => __kovDev.emit('push', { payload: { title: 'Орлов Андрей', body: 'Проверка связи', url: '#/messenger/35',
                  tag: 'chat-35', kind: 'chat', chat_id: 35, ts: new Date().toISOString() } })""")
    page.wait_for_timeout(600)
    check("overlay", "уведомление при открытом приложении показано внутри (как service worker)",
          "Проверка связи" in dev(page, "() => document.body.innerText"))
    dev(page, "() => new Notification('Задача', { body: 'Срок завтра', tag: 'notification-7', data: { url: '#/tasks/101' } })")
    page.wait_for_timeout(200)
    shown = dev(page, "() => __kovDev.state.shown.slice(-1)[0]")
    check("overlay", "системное уведомление: заголовок, текст, метка и адрес как у веб-версии",
          shown and shown["tag"] == "notification-7" and shown["url"] == "#/tasks/101" and shown["kind"] == "notification", shown)
    dev(page, "() => __kovDev.emit('navigate', { url: '#/tasks/102', tag: 'none' })")
    page.wait_for_timeout(400)
    check("overlay", "нажатие на уведомление открывает нужный раздел", dev(page, "() => location.hash") == "#/tasks/102")
    dev(page, "() => __kovDev.emit('navigate', { url: 'javascript:alert(1)' })")
    page.wait_for_timeout(200)
    check("overlay", "небезопасный адрес из уведомления отбрасывается", dev(page, "() => location.hash") == "#/tasks/102")

    # скачивание
    dev(page, "() => Kov.Downloads.download(Kov.Api.fileUrl('mock/file/report.bin'), 'отчёт.bin')")
    page.wait_for_function("() => Object.values(__kovDev.state.blobs).some(b => b.done)", timeout=10000)
    blob = dev(page, "() => { const b = Object.values(__kovDev.state.blobs).find(x => x.done); return { name: b.name, size: b.size, data: b.parts.join('|') }; }")
    raw = b"".join(base64.b64decode(p) for p in blob["data"].split("|"))
    want = bytes((i * 7 + 3) % 256 for i in range(300 * 1024 + 17))
    check("overlay", "скачивание: файл пришёл в «Загрузки» частями и побайтно цел", blob["name"] == "отчёт.bin" and raw == want,
          (blob["name"], len(raw), len(want)))
    dev(page, "() => { const a = document.createElement('a'); a.href = Kov.Api.fileUrl('mock/file/x.bin'); a.download = 'x.bin'; document.body.append(a); a.click(); a.remove(); }")
    page.wait_for_timeout(200)
    dl = dev(page, "() => __kovDev.state.downloads.slice(-1)[0]")
    check("overlay", "прямая ссылка скачивания уходит системному загрузчику", dl and dl["name"] == "x.bin" and "/mock/file/x.bin?token=" in dl["url"], dl)

    # фото, которое браузер распаковать не может (HEIC) — сжимает телефон
    r = dev(page, """async () => {
      const file = new File([new Uint8Array([0,0,0,24,102,116,121,112,104,101,105,99,0,0,0,0])], 'IMG_0001.HEIC', { type: 'image/heic' });
      const handle = __kovDev.pick(file);
      const canvas = document.createElement('canvas'); canvas.width = 640; canvas.height = 480;
      const g = canvas.getContext('2d'); g.fillStyle = '#2F6F7E'; g.fillRect(0, 0, 640, 480); g.fillStyle = '#F5B301'; g.fillRect(100, 100, 200, 150);
      const png = await new Promise(r => canvas.toBlob(r, 'image/png'));
      const webp = await new Promise(r => canvas.toBlob(r, 'image/webp', 0.8));
      const previewUrl = await __kovDev.temp(png), fileUrl = await __kovDev.temp(webp);
      __kovDev.handlers['media.image'] = a => a.handle === handle ? { changed: true, width: 3200, height: 2400, mime: 'image/webp', name: 'IMG_0001.webp',
        reason: '', file: { url: fileUrl, size: webp.size }, preview: { url: previewUrl, width: 640, height: 480 } } : Promise.reject(new Error('чужой файл'));
      const out = await Kov.ImagePrep.prepare(file);
      await new Promise(r => setTimeout(r, 50));      // команды моста асинхронны
      return { changed: out.changed, type: out.file.type, name: out.file.name, size: out.file.size, w: out.width, h: out.height,
               thumbHash: !!out.thumbHash, thumb: out.thumb ? out.thumb.size : 0, thumbW: out.thumbWidth, released: __kovDev.state.released.length, webp: webp.size };
    }""")
    check("overlay", "HEIC: сжат телефоном, миниатюра и заглушка построены кодом веб-версии",
          r["changed"] and r["type"] == "image/webp" and r["name"] == "IMG_0001.webp" and r["size"] == r["webp"]
          and r["thumbHash"] and r["thumb"] > 0 and r["thumbW"] == 320 and r["released"] >= 2, r)

    # видео, которое страница сжать не может, — кодек телефона
    fixture = base64.b64encode(open(os.path.join(FIXTURES, "mux_js.mp4"), "rb").read()).decode()
    source = base64.b64encode(open(os.path.join(FIXTURES, "h264_aac_rot.mp4"), "rb").read()).decode()
    r = dev(page, """async ([b64, src64]) => {
      const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
      const file = new File([Uint8Array.from(atob(src64), c => c.charCodeAt(0))], 'VID_0001.mp4', { type: 'video/mp4' });
      // телефон, чей браузерный движок не умеет этот декодер: страница решит «на сервер»
      if (window.VideoDecoder) VideoDecoder.isConfigSupported = async cfg => ({ supported: false, config: cfg });
      const handle = __kovDev.pick(file);
      const cut = Math.floor(bytes.length / 3);
      const segs = [bytes.slice(0, cut), bytes.slice(cut, 2 * cut), bytes.slice(2 * cut)];
      const urls = [];
      for (const s of segs) urls.push(await __kovDev.temp(new Blob([s])));
      const c = document.createElement('canvas'); c.width = 64; c.height = 36; c.getContext('2d').fillRect(0, 0, 64, 36);
      const poster = await __kovDev.temp(await new Promise(r => c.toBlob(r, 'image/jpeg')));
      __kovDev.handlers['media.videoPlan'] = a => a.handle === handle ? { mode: 'device', job: 'j77', width: 1280, height: 720, fps: 30, estimate: 12, side: 1280, duration: 4.2 } : { mode: 'server', reason: 'x' };
      __kovDev.handlers['media.videoRun'] = async a => {
        __kovDev.emit('media.progress', { job: a.job, p: 0.5 });
        __kovDev.emit('media.segment', { job: a.job, index: 1, url: urls[1], size: segs[1].length });   // не по порядку
        __kovDev.emit('media.poster', { job: a.job, url: poster, width: 1280, height: 720 });
        __kovDev.emit('media.segment', { job: a.job, index: 0, url: urls[0], size: segs[0].length });
        __kovDev.emit('media.segment', { job: a.job, index: 2, url: urls[2], size: segs[2].length });
        return { frames: 126, encoded: 126, segments: 3 };
      };
      // в приложении видео сначала сжимает телефон: план и длительность — из его разбора файла
      const plan = await Kov.VideoPrep.plan(file, { edits: null });
      if (plan.mode !== 'device') return { plan };
      const got = []; let progress = 0, posterSize = null;
      const res = await Kov.VideoPrep.compress(file, plan, chunk => { got.push(chunk); }, {
        onProgress: p => { progress = p; }, onPoster: canvas => { posterSize = [canvas.width, canvas.height]; } });
      const total = got.reduce((n, c) => n + c.length, 0);
      const joined = new Uint8Array(total); let o = 0; for (const c of got) { joined.set(c, o); o += c.length; }
      await new Promise(r => setTimeout(r, 50));
      return { plan: { mode: plan.mode, native: plan.native, width: plan.width, duration: plan.analysis.duration }, res, progress, posterSize,
               same: joined.length === bytes.length && joined.every((v, i) => v === bytes[i]), released: __kovDev.state.released.length };
    }""", [fixture, source])
    check("overlay", "видео: сначала план телефона, страница — запасной путь (длительность — из разбора на телефоне)",
          r["plan"].get("mode") == "device" and r["plan"].get("native") == "j77" and abs(r["plan"].get("duration", 0) - 4.2) < 0.05, r["plan"])
    check("overlay", "видео: куски файла собраны по порядку, обложка и прогресс переданы",
          r.get("same") and r.get("res", {}).get("frames") == 126 and r.get("posterSize") == [1280, 720] and r.get("progress") == 0.5, r)

    # выход: телефон отвязывается от сотрудника
    before_logout = list(page.errors)
    dev(page, "() => { Kov.App.logout(); }")           # спросит подтверждение — жмём «Выйти», как человек
    page.wait_for_selector(".modal-root button:has-text('Выйти')", timeout=5000)
    page.click(".modal-root button:has-text('Выйти')")
    page.wait_for_selector(".login-screen", timeout=5000)
    page.wait_for_timeout(300)
    cleared = dev(page, "() => __kovDev.state.cleared")
    check("overlay", "выход из учётной записи: приложение получает прежний токен для отвязки телефона",
          cleared and cleared.get("token", "").startswith("mock.") and dev(page, "() => __kovDev.state.session") is None, cleared)
    # после выхода веб-версия сама (и без надстройки) успевает запросить список задач без токена — это 401 в консоли
    check("overlay", "без ошибок скриптов на всём пути до выхода", not before_logout, before_logout[:3])
    ctx.close()

    # обычный HTTP (не localhost): запасные пути
    print("   — адрес без HTTPS (запасные пути)")
    ctx, page = new_page(browser, "worker")
    open_route(page, APK_HTTP, "#/tasks")
    info = dev(page, "() => ({ secure: window.isSecureContext, fast: Kov.FileHash.fast(), webcodecs: typeof VideoEncoder !== 'undefined' })")
    check("overlay", "HTTP: страница не «защищённая», WebCodecs и crypto.subtle недоступны",
          info == {"secure": False, "fast": False, "webcodecs": False}, info)
    r = dev(page, """async () => {
      const file = new File([new Uint8Array(1024 * 1024).fill(7)], 'big.bin');
      const handle = __kovDev.pick(file);
      let progress = [];
      __kovDev.handlers['media.hashTree'] = a => { __kovDev.emit('media.progress', { job: a.job, p: 1 }); return a.handle === handle ? 'ab'.repeat(32) : Promise.reject(new Error('x')); };
      __kovDev.handlers['media.hashRange'] = a => 'cd'.repeat(32);
      const tree = await Kov.FileHash.tree(file, p => progress.push(p));
      const range = await Kov.FileHash.range(file, 0, 4096);
      const other = new File([new Uint8Array(1000).fill(1)], 'small.bin');
      const soft = await Kov.FileHash.tree(other);
      return { tree, range, progress, soft: typeof soft === 'string' && soft.length > 0 && soft !== tree,
               worth: Kov.FileHash.worthIt(file.size), cmds: __kovDev.log.map(m => m.cmd).filter(c => c.startsWith('media.hash')) };
    }""")
    check("overlay", "HTTP: отпечаток выбранного файла считает телефон, остальных — страница",
          r["tree"] == "ab" * 32 and r["range"] == "cd" * 32 and r["progress"] == [1] and r["soft"] and r["worth"], r)
    fixture = base64.b64encode(open(os.path.join(FIXTURES, "h264_aac_rot.mp4"), "rb").read()).decode()
    r = dev(page, """async (b64) => {
      const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
      const file = new File([bytes], 'clip.mp4', { type: 'video/mp4' });
      __kovDev.pick(file);
      __kovDev.handlers['media.videoPlan'] = a => ({ mode: 'server', reason: 'slow:900s' });
      const plan = await Kov.VideoPrep.plan(file, { edits: null });
      return { mode: plan.mode, reason: plan.reason, asked: __kovDev.calls('media.videoPlan').length };
    }""", fixture)
    check("overlay", "HTTP: видео без WebCodecs — решение спрашивается у телефона",
          r["asked"] == 1 and r["mode"] == "server" and r["reason"].startswith("no-webcodecs") and "телефон: slow" in r["reason"], r)
    check("overlay", "HTTP: без ошибок скриптов", not page.errors, page.errors[:3])
    ctx.close()


# ── 4. исправления 25.09.2026 (docs/changes-2026-09-25.md) ─────────────────────
def mock_call(path, data, token):
    req = urllib.request.Request("http://127.0.0.1:%d/%s" % (MOCK_PORT, path), data=json.dumps(data).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + token}, method="POST")
    return json.load(urllib.request.urlopen(req))


def open_task_files(page):
    """«Файлы задачи»: на телефоне — вкладка «Файлы», затем переключатель; на компьютере — кнопка зоны."""
    if page.locator(".tv-tabbar").count():
        page.click(".tv-tab[data-k=files]"); page.wait_for_timeout(300)
        page.click(".tv-sw[data-k=task]")
    else:
        page.click(".content button:has-text('Файлы задачи')")


def grid_upload(page, name="note.txt"):
    """Файл через плитку «Файлы задачи»: вкладка, «Добавить файлы», окно выбора — как человек.
    Имя уникальное: тестовый сервер помнит прежние файлы, и сайт спросил бы «заменить?»."""
    stem, dot, ext = name.rpartition(".")
    name = "%s-%d.%s" % (stem, int(time.time() * 1000) % 10000000, ext)
    if page.locator(".tv-tabbar").count():          # телефон: вкладка «Файлы», затем «Файлы задачи»
        page.click(".tv-tab[data-k=files]"); page.wait_for_timeout(300)
        page.click(".tv-sw[data-k=task]")
    else:
        page.click(".content button:has-text('Файлы задачи')")
    page.wait_for_timeout(300)
    # на телефоне «Добавить файлы» открывает шторку вложений — дальше «Файлы» и системный выбор
    page.click(".content button:has-text('Добавить файлы')")
    page.wait_for_timeout(400)
    with page.expect_file_chooser() as fc:
        if page.locator(".attach-root .attach-tab").count():
            page.locator(".attach-root .attach-tab").nth(1).click()
        else:
            page.click(".content button:has-text('Добавить файлы')")
    fc.value.set_files([{"name": name, "mimeType": "text/plain", "buffer": "проверка докачки".encode("utf-8")}])
    page.wait_for_function("() => Kov.Files.uploadsActive()", timeout=5000)


def suite_fixes(browser):
    print("\n4. Исправления 25.09.2026 (docs/changes-2026-09-25.md) — веб-версия и APK")
    director, _ = login("director")
    cases = iter([(102, 7, "task.participants_updated"), (104, 6, "task.updated"),
                  (102, 10, "task.participants_updated"), (104, 9, "task.updated")])
    for label, base in (("веб", WEB), ("APK", APK)):
        # сотрудника добавили в задачу позже — список обновляется сразу
        for _ in range(2):
            task_id, user, kind = next(cases)
            role = {7: "to", 6: "montage", 10: "accountant", 9: "stock"}[user]
            name = next(t["name"] for t in mock_server.TASKS if t["id"] == task_id)
            ctx, page = new_page(browser, role, viewport=(390, 844))
            open_route(page, base, "#/tasks")
            before = name in page.evaluate("() => document.querySelector('.content').innerText")
            mock_call("mock/task-participant", {"task_id": task_id, "user_id": user}, director)
            event = {"type": kind, "task_id": task_id}
            if kind == "task.participants_updated":
                event.update({"added": [user], "removed": []})
            mock_call("mock/ws-event", {"user_ids": [user], "event": event}, director)
            t0 = time.time()
            seen = False
            while time.time() - t0 < 3:
                if name in page.evaluate("() => document.querySelector('.content').innerText"):
                    seen = True
                    break
                page.wait_for_timeout(100)
            check("fixes", "%s: добавили в задачу — список обновился сразу (%s)" % (label, kind),
                  not before and seen and time.time() - t0 < 2, {"было": before, "появилось": seen, "секунд": round(time.time() - t0, 1)})
            ctx.close()

        # обрыв связи посреди отправки файла задачи — раздел не перерисовывается, файл доходит
        mock_call("mock/config", {"upload_delay_ms": 4000}, director)
        ctx, page = new_page(browser, "director", viewport=(390, 844))
        open_route(page, base, "#/tasks/101")
        grid_upload(page)
        dev(page, "() => { window.__sec = document.querySelector('.content .section'); window.__reconnects = 0; Kov.WS.onReconnect(() => { window.__reconnects++; }); }")
        mock_call("mock/ws-drop", {"user_ids": [1]}, director)
        page.wait_for_function("() => window.__reconnects > 0", timeout=8000)
        page.wait_for_timeout(300)
        mid = dev(page, "() => ({ same: window.__sec === document.querySelector('.content .section'), active: Kov.Files.uploadsActive() })")
        check("fixes", "%s: связь вернулась во время отправки — раздел не перерисован, отправка идёт" % label,
              mid == {"same": True, "active": True}, mid)
        page.wait_for_function("() => !Kov.Files.uploadsActive()", timeout=15000)
        page.wait_for_timeout(700)
        end = dev(page, "() => ({ ok: document.body.innerText.includes('Файл загружен'), cancelled: document.body.innerText.includes('Загрузка отменена'), redrawn: window.__sec !== document.querySelector('.content .section') })")
        check("fixes", "%s: файл дошёл, раздел перечитан после окончания отправки" % label,
              end == {"ok": True, "cancelled": False, "redrawn": True}, end)

        if base == APK:
            # мобильное: уведомление и «Назад» во время отправки — сначала вопрос, а не обрыв
            open_route(page, base, "#/tasks/101")
            grid_upload(page, "second.txt")
            dev(page, "() => __kovDev.emit('navigate', { url: '#/notifications' })")
            page.wait_for_timeout(300)
            asked = dev(page, "() => ({ modal: [...document.querySelectorAll('.modal-root')].map(m => m.innerText).join(' '), hash: location.hash })")
            check("fixes", "APK: нажатие на уведомление во время отправки — вопрос вместо обрыва",
                  "Идёт отправка файла" in asked["modal"] and asked["hash"] == "#/tasks/101", asked)
            page.click(".modal-root button:has-text('Отмена')")
            page.wait_for_timeout(200)
            back = dev(page, "() => KovNative.onBack()")
            page.wait_for_timeout(200)
            asked = dev(page, "() => [...document.querySelectorAll('.modal-root')].map(m => m.innerText).join(' ')")
            check("fixes", "APK: «Назад» во время отправки — вопрос вместо обрыва", back is True and "Идёт отправка файла" in asked, (back, asked[:80]))
            page.click(".modal-root button:has-text('Отмена')")
            page.wait_for_function("() => !Kov.Files.uploadsActive()", timeout=15000)
            page.wait_for_timeout(500)
            check("fixes", "APK: после «Отмена» файл дошёл до конца",
                  "Загрузка отменена" not in dev(page, "() => document.body.innerText"))
        check("fixes", "%s: без ошибок скриптов" % label, not page.errors, page.errors[:3])
        ctx.close()

        # отправка из мессенджера (Kov.Files.upload напрямую, мимо плиток файлов)
        ctx, page = new_page(browser, "worker", viewport=(390, 844))
        open_route(page, base, "#/messenger")
        start_send = """() => { window.__up = 'идёт';
          Kov.Files.upload([new File(['x'], 'photo.txt')], 'mock/slow-upload?ms=4000', { chat_id: 35 }, () => {})
            .then(() => { window.__up = 'дошла'; }, e => { window.__up = e && e.aborted ? 'отменена' : 'ошибка'; }); }"""
        dev(page, "() => { window.__reconnects = 0; Kov.WS.onReconnect(() => { window.__reconnects++; }); }")
        dev(page, start_send)
        page.wait_for_timeout(300)
        mock_call("mock/ws-drop", {"user_ids": [5]}, director)
        page.wait_for_function("() => window.__reconnects > 0", timeout=8000)
        page.wait_for_function("() => window.__up !== 'идёт'", timeout=10000)
        check("fixes", "%s: отправка из мессенджера при восстановлении связи доходит" % label,
              dev(page, "() => window.__up") == "дошла", dev(page, "() => window.__up"))
        if base == APK:
            dev(page, start_send)
            page.wait_for_timeout(300)
            dev(page, "() => __kovDev.emit('navigate', { url: '#/tasks' })")
            page.wait_for_timeout(300)
            asked = dev(page, "() => ({ modal: [...document.querySelectorAll('.modal-root')].map(m => m.innerText).join(' '), hash: location.hash })")
            check("fixes", "APK: уведомление во время отправки в чат — тоже вопрос вместо обрыва",
                  "Идёт отправка файла" in asked["modal"] and asked["hash"] == "#/messenger", asked)
            page.click(".modal-root button:has-text('Отмена')")
            page.wait_for_function("() => window.__up !== 'идёт'", timeout=10000)
            check("fixes", "APK: после «Отмена» отправка в чат дошла", dev(page, "() => window.__up") == "дошла")
        check("fixes", "%s: мессенджер — без ошибок скриптов" % label, not page.errors, page.errors[:3])
        ctx.close()
    mock_call("mock/config", {"upload_delay_ms": 0}, director)
    for o in OBSERVATIONS:
        print("  НАБЛЮДЕНИЕ " + o)


# ── 5. симулятор телефона (tools/simulator) ──────────────────────────────────
def suite_sim(browser):
    print("\n5. Симулятор телефона на ПК")
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="ru-RU")
    page = ctx.new_page()
    errors = []
    expected = ("ERR_EMPTY_RESPONSE", "handshake response", "status of 40", "ERR_CONNECTION")   # имитация «нет сети»
    page.on("console", lambda m: errors.append(m.text[:200]) if m.type == "error" and not any(x in m.text for x in expected) else None)
    page.on("pageerror", lambda e: errors.append(str(e)[:200]))
    app = lambda: next(f for f in page.frames if "/app/index.html" in f.url)
    page.goto(SIM)
    page.wait_for_timeout(1500)
    f = app()
    f.fill(".login-screen input[type=text], .login-screen input:not([type])", "worker")
    f.fill(".login-screen input[type=password]", "test")
    f.press(".login-screen input[type=password]", "Enter")
    f.wait_for_selector(".app", timeout=8000)
    page.wait_for_timeout(1800)
    f.click("button:has-text('Разрешить')")
    page.wait_for_selector("#sysdialog:not([hidden])", timeout=5000)
    check("sim", "приглашение веб-версии вызывает системный запрос разрешения", "отправлять вам уведомления" in page.inner_text("#sysdialog"))
    page.click("#sysdialog button:has-text('Разрешить')")
    page.wait_for_selector("#sysdialog:not([hidden])", timeout=5000)
    page.click("#sysdialog button:has-text('Разрешить')")        # работа без ограничений батареи
    page.wait_for_function("() => document.querySelector('#chan').textContent === 'на связи'", timeout=10000)
    check("sim", "уведомления включены: телефон зарегистрирован, канал на связи", "сотрудник 5" in page.inner_text("#devid"))
    page.click("#testPush")
    page.wait_for_timeout(3500)
    check("sim", "приложение открыто: проверочное уведомление внутри, доставка подтверждена",
          "Push-уведомления работают" in f.inner_text("body") and page.locator("#shade .note").count() == 0
          and "доставлено на устройство" in page.inner_text("#log"))
    page.click("#navbar button[data-k=home]")
    page.wait_for_timeout(300)
    check("sim", "«Домой»: страница становится невидимой, как у WebView", f.evaluate("document.visibilityState") == "hidden")
    page.click("#colleague")
    page.wait_for_selector("#shade .note", timeout=5000)
    check("sim", "свёрнуто: уведомление всплывает и ложится в шторку", page.locator("#headsup:not([hidden])").count() == 1
          and page.inner_text("#shade .note b") == "Орлов Андрей")
    page.click("#shade .note")
    page.wait_for_timeout(1200)
    check("sim", "нажатие на уведомление открывает нужный чат", f.evaluate("location.hash") == "#/messenger/35"
          and f.evaluate("document.visibilityState") == "visible")
    page.click("#navbar button[data-k=recent]")
    page.click("#recents button")
    page.wait_for_timeout(300)
    check("sim", "«Недавние» → смахнуть: приложение закрыто", not [x for x in page.frames if "/app/" in x.url])
    page.click("#colleague")
    page.wait_for_selector("#shade .note", timeout=5000)
    page.click("#shade .note")
    page.wait_for_timeout(3000)
    f = app()
    check("sim", "приложение закрыто: уведомление приходит, нажатие — холодный запуск в нужный чат",
          f.evaluate("location.hash") == "#/messenger/35" and "Мессенджер" in f.evaluate("document.querySelector('.top-bar').innerText"))
    f.evaluate("Kov.UI.modal({ title: 'Проверка' })")
    page.wait_for_timeout(200)
    page.click("#navbar button[data-k=back]")
    page.wait_for_timeout(200)
    check("sim", "«Назад» закрывает окно в приложении", f.locator(".modal-root").count() == 0)
    page.click("#clearLog")
    page.click("#net button[data-v=offline]")
    page.wait_for_timeout(800)
    off = f.evaluate("Kov.Api.get('tasks').then(() => 'ответ', () => 'ошибка сети')")
    check("sim", "«Нет сети»: сервер недоступен, navigator.onLine = false, интерфейс на месте",
          off == "ошибка сети" and f.evaluate("navigator.onLine") is False and f.locator(".app").count() == 1)
    page.click("#net button[data-v=online]")
    page.wait_for_function("() => document.querySelector('#chan').textContent === 'на связи'", timeout=10000)
    page.wait_for_timeout(3000)
    on = f.evaluate("Kov.Api.get('tasks').then(() => 'ответ', () => 'ошибка сети')")
    reconnects = len([l for l in page.inner_text("#log").split("\n") if "на связи" in l])
    check("sim", "сеть вернулась: запросы идут, канал переподключился один раз", on == "ответ" and reconnects == 1, (on, reconnects))
    f.evaluate("Kov.Downloads.download(Kov.Api.fileUrl('mock/file/otchet.bin'), 'otchet.bin')")
    page.wait_for_function("() => document.querySelector('#downloads').innerText.includes('otchet.bin')", timeout=8000)
    check("sim", "скачанный файл появляется в «Загрузках»", "300 КБ" in page.inner_text("#downloads"))
    f.evaluate("window.open(location.pathname.replace('index.html', 'doc.html'), '_blank')")
    page.wait_for_timeout(1200)
    opened = page.locator("#appArea iframe.child").count()
    page.click("#navbar button[data-k=back]")
    page.wait_for_timeout(400)
    check("sim", "окно документа открывается поверх приложения и закрывается «Назад»",
          opened == 1 and page.locator("#appArea iframe.child").count() == 0)
    check("sim", "без ошибок скриптов", not errors, errors[:3])
    page.screenshot(path=os.path.join(REPORT, "simulator.png"))
    ctx.close()


# ── 6. живая переписка и файлы тестового сервера ─────────────────────────────
def suite_live(browser):
    print("\n6. Переписка и файлы на тестовом сервере (APK и сайт)")
    import live_store
    phone_ctx, phone = new_page(browser, "worker", viewport=(390, 844))
    site_ctx, site = new_page(browser, "director", viewport=(1280, 800))
    open_route(phone, APK, "#/messenger/35")
    open_route(site, WEB, "#/messenger/35")
    phone.wait_for_timeout(1500)
    history = phone.inner_text("#kovChat")
    images = lambda pg: pg.evaluate("() => [...document.querySelectorAll('#kovChat img')].filter(i => i.naturalWidth > 0).length")
    check("live", "в чате есть история: сообщения, картинка и документ",
          "Скиньте, пожалуйста, схему" in history and "Смета_по_объекту.csv" in history and images(phone) >= 1, history[:120])
    phone.fill("#messageInput", "Проверка переписки из APK")
    phone.click("#btnSend")
    site.wait_for_function("() => document.querySelector('#kovChat').innerText.includes('Проверка переписки из APK')", timeout=8000)
    check("live", "текст из APK сразу виден собеседнику на сайте", True)
    before = images(site)
    phone.click("#btnAttach")
    phone.wait_for_timeout(300)
    # шторка вложений: «Галерея» (системный выбор) → выбранное в сетке → «Прикрепить (1)» → окно отправки
    with phone.expect_file_chooser() as fc:
        phone.locator('.attach-root .attach-tab').nth(0).click()
    fc.value.set_files([{"name": "Фото_из_APK.png", "mimeType": "image/png", "buffer": live_store.site_png()}])
    phone.click('.attach-root .attach-sendbtn, .attach-root .attach-send')   # чат на телефоне: круглая кнопка, отправка сразу
    try:
        site.wait_for_function("n => [...document.querySelectorAll('#kovChat img')].filter(i => i.naturalWidth > 0).length > n", arg=before, timeout=15000)
        ok = True
    except Exception:
        ok = False
    # у отправителя: в переписке два сообщения с картинкой, последнее отрисовано
    # (картинки в чате грузятся лениво: ушедшая за экран старая может быть ещё не загружена)
    sender = "() => { const all = [...document.querySelectorAll('#kovChat img')]; const last = all[all.length - 1]; return { all: all.length, last: !!(last && last.naturalWidth > 0) }; }"
    try:
        phone.wait_for_function("() => { const all = [...document.querySelectorAll('#kovChat img')]; const last = all[all.length - 1]; return all.length >= 2 && last && last.naturalWidth > 0; }", timeout=10000)
    except Exception:
        pass
    mine = phone.evaluate(sender)
    check("live", "фото из APK сразу видно собеседнику на сайте и отправителю", ok and mine["all"] >= 2 and mine["last"], (before, images(site), mine))
    check("live", "переписка без ошибок скриптов", not phone.errors and not site.errors, (phone.errors + site.errors)[:3])
    phone_ctx.close()
    site_ctx.close()
    boss_ctx, boss = new_page(browser, "director", viewport=(390, 844))
    open_route(boss, APK, "#/tasks/101")
    open_task_files(boss)
    boss.wait_for_timeout(300)
    boss.click(".content button:has-text('Добавить файлы')")
    boss.wait_for_timeout(400)
    with boss.expect_file_chooser() as fc:
        if boss.locator(".attach-root .attach-tab").count():      # телефон: шторка вложений → «Файлы»
            boss.locator(".attach-root .attach-tab").nth(1).click()
        else:
            boss.click(".content button:has-text('Добавить файлы')")
    fc.value.set_files([{"name": "Схема_щита_APK.png", "mimeType": "image/png", "buffer": live_store.scheme_png()}])
    boss.wait_for_function("() => document.body.innerText.includes('Файл загружен')", timeout=20000)
    mont_ctx, mont = new_page(browser, "montage", viewport=(1280, 800))
    open_route(mont, WEB, "#/tasks/101")
    open_task_files(mont)
    mont.wait_for_timeout(300)
    mont.click(".content button:has-text('Изображения')")
    mont.wait_for_timeout(1500)
    thumbs = mont.evaluate("() => [...document.querySelectorAll('.content img')].filter(i => i.naturalWidth > 0).length")
    check("live", "файл, прикреплённый к задаче в APK, виден участнику на сайте с миниатюрой",
          "Схема_щита_APK" in mont.inner_text(".content") and thumbs >= 1, thumbs)
    check("live", "файлы задач без ошибок скриптов", not boss.errors and not mont.errors, (boss.errors + mont.errors)[:3])
    boss_ctx.close()
    mont_ctx.close()



# ── 6. Последние версии (v45–v67): просмотр, выбор сообщений, этапы, меню, шапка ──
VID_FIXTURE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'fixtures', 'vp9.mp4')
L_PICS = """async () => { const pic = (w, h, a, b) => new Promise(r => { const c = document.createElement('canvas'); c.width = w; c.height = h; const g = c.getContext('2d');
  const gr = g.createLinearGradient(0, 0, w, h); gr.addColorStop(0, a); gr.addColorStop(1, b); g.fillStyle = gr; g.fillRect(0, 0, w, h); c.toBlob(x => r(URL.createObjectURL(x)), 'image/jpeg', .85); });
  window.__l = []; for (let k = 0; k < 6; k++) window.__l.push({ src: await pic(k % 2 ? 900 : 1600, k % 2 ? 1600 : 900, ['#2a1b5c', '#06263a', '#5a1a3a'][k % 3], ['#ff8f7a', '#43c6ac', '#ffd27a'][k % 3]), filename: 'p' + k + '.jpg', who: 'Тест', at: new Date().toISOString() });
  window.__l.push({ src: URL.createObjectURL(await (await fetch(location.origin + '/test/clip.mp4')).blob()), filename: 'v.mp4', who: 'Тест', at: new Date().toISOString() }); }"""
L_GROUP = """() => { const now = Date.now(), iso = k => new Date(now - k * 60000).toISOString();
  const pic = id => ({ id, message_id: id, file_name: 'ф' + id + '.jpg', file_path: 'messenger/x/p' + id + '.jpg', is_image: true, file_size: 120000 });
  chatMessages.push({ id: 99001, sender_id: -5, sender_name: 'Чужой', text: 'Чужое: смета готова', timestamp: iso(3) });
  chatMessages.push({ id: 99002, sender_id: Number(currentUserId), sender_name: 'Я', text: '', timestamp: iso(1), group_id: 'g', special: 'file_group', files: [pic(99011), pic(99012), pic(99013)] });
  renderMessages(chatMessages); window.__ops = null; window.__del = null; window.__fw = [];
  backend.forward_messages = o => { window.__ops = o; }; backend.confirm_delete_messages = i => { window.__del = i; }; backend.forward_message = (i, p) => window.__fw.push(i); }"""
L_BUB = "(id) => [...document.querySelectorAll('#messagesList .msg-bubble')].find(x => x.__msg && x.__msg.id === id)"
L_STATE = """() => { const b = (""" + L_BUB + """)(99002), bar = document.getElementById('selBar'), t = b ? [...b.querySelectorAll('*')].filter(e => e.__file) : [];
  const c = b && b.parentElement.querySelector(':scope > .sel-circle');
  return { on: !!bar && !bar.hidden, count: bar ? bar.querySelector('.sel-count').textContent : '', del: bar ? !bar.querySelector('.sel-del').hidden : null,
    files: t.map(e => e.classList.contains('sel-file') ? 1 : 0).join(''), circle: !c || c.hidden ? '-' : c.classList.contains('on') ? 'v' : c.classList.contains('part') ? '/' : 'o', viewer: !!document.querySelector('.mv-root') }; }"""
L_LONG = """async (k) => { const b = (""" + L_BUB + """)(99002), el = [...b.querySelectorAll('*')].filter(e => e.__file)[k]; el.scrollIntoView({ block: 'center' });
  const r = el.getBoundingClientRect(), t = [new Touch({ identifier: 1, target: el, clientX: r.left + 10, clientY: r.top + 10 })];
  el.dispatchEvent(new TouchEvent('touchstart', { touches: t, changedTouches: t, bubbles: true, cancelable: true })); await new Promise(z => setTimeout(z, 600));
  el.dispatchEvent(new TouchEvent('touchend', { touches: [], changedTouches: t, bubbles: true, cancelable: true }));
  el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true })); }"""
L_TAP = "(k) => { const b = (" + L_BUB + ")(99002); [...b.querySelectorAll('*')].filter(e => e.__file)[k].dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true })); }"
L_TAPMSG = "(id) => { const b = (" + L_BUB + ")(id); const t = [...b.querySelectorAll('*')].find(e => !e.__file && e.textContent.trim() && !e.children.length) || b; t.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true })); }"
L_CIRCLE = "(id) => (" + L_BUB + ")(id).parentElement.querySelector(':scope > .sel-circle').click()"


def suite_latest(browser):
    print("\n6. Последние версии (v45–v67): просмотр, выбор сообщений, этапы, меню, шапка")
    S = "latest"   # ключ набора: по нему отчёт заменяет прежние результаты
    cid = mock_server.private_chat_id(1, 3)
    vid = open(VID_FIXTURE, 'rb').read() if os.path.exists(VID_FIXTURE) else b''

    def guard(name, fn):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            check(S, name, False, "сбой теста: " + str(e).splitlines()[0][:200])

    for phone in (True, False):
        dev_name = "телефон" if phone else "компьютер"
        ctx, page = new_page(browser, "director", viewport=(360, 740) if phone else (1280, 800))
        if phone:
            ctx.close()
            ctx = browser.new_context(has_touch=True, is_mobile=True, viewport={"width": 360, "height": 740}, locale="ru-RU")
            tok, uid = login("director")
            ctx.add_init_script("localStorage.setItem('kov.auth', JSON.stringify({host: location.origin, token: %s, userId: %d}));localStorage.setItem('kov.notify.offer', String(Date.now()));" % (json.dumps(tok), uid))
            page = ctx.new_page()
        page.route("**/test/clip.mp4", lambda r: r.fulfill(body=vid, content_type="video/mp4"))
        open_route(page, WEB, "#/messenger/%d" % cid)
        page.wait_for_timeout(1500)

        def viewer():
            page.evaluate(L_PICS)
            page.evaluate("() => localStorage.setItem('kov.viewer.info', '1')")   # прежние версии запоминали открытые сведения
            page.evaluate("() => Kov.Files.viewer(window.__l, 5, { actions: { reply() {}, forward() {}, remove() {}, canRemove: () => true } })")
            page.wait_for_timeout(900)
            check(S, "просмотр (%s): сведения о файле сразу не открыты — только по кнопке" % dev_name,
                  not page.evaluate("() => document.querySelector('.mv-root').classList.contains('mv-info-on')"))
            pos = "() => { const r = document.querySelector('.mv-root'), t = document.querySelector('.mv-top').getBoundingClientRect(); return r.scrollLeft === 0 && Math.abs(t.right - innerWidth) <= 1; }"
            first = page.evaluate(pos)
            page.evaluate("() => { window.__t2 = document.querySelector('.mv-thumb[data-k=\"2\"]'); }")
            for k in range(5):
                page.evaluate("(k) => document.querySelector('.mv-thumb[data-k=\"' + k + '\"]').click()", 4 - k)
                page.wait_for_timeout(200)
            page.wait_for_timeout(400)
            check(S, "просмотр (%s): у последнего и первого снимка ничего не сдвигается" % dev_name, first and page.evaluate(pos))
            check(S, "просмотр (%s): лента при листании не перестраивается" % dev_name,
                  page.evaluate("() => window.__t2 === document.querySelector('.mv-thumb[data-k=\"2\"]') && window.__t2.isConnected"))
            img = page.locator(".mv-media").bounding_box()
            cx, cy = img['x'] + img['width'] / 2, img['y'] + img['height'] / 2
            if phone:
                page.evaluate("""([x, y]) => { const s = document.querySelector('.mv-stage'), im = document.querySelector('.mv-media'), t = (i, p) => new Touch({ identifier: i, target: im, clientX: p, clientY: y });
                  s.dispatchEvent(new TouchEvent('touchstart', { touches: [t(1, x - 30), t(2, x + 30)], changedTouches: [t(2, x + 30)], bubbles: true, cancelable: true }));
                  s.dispatchEvent(new TouchEvent('touchmove', { touches: [t(1, x - 90), t(2, x + 90)], bubbles: true, cancelable: true }));
                  s.dispatchEvent(new TouchEvent('touchend', { touches: [], changedTouches: [t(1, x - 90), t(2, x + 90)], bubbles: true, cancelable: true })); }""", [cx, cy])
            else:
                page.mouse.move(cx, cy)
                page.mouse.wheel(0, -400)
                page.wait_for_timeout(150)
                page.mouse.wheel(0, -400)
            page.wait_for_timeout(300)
            tr = page.evaluate("() => document.querySelector('.mv-media').style.transform")
            check(S, "просмотр (%s): фото приближается %s" % (dev_name, "щипком" if phone else "колесом"), 'scale(' in tr, tr)
            if vid:
                page.evaluate("() => document.querySelector('.mv-thumb[data-k=\"6\"]').click()")
                page.wait_for_timeout(1300)
                check(S, "просмотр (%s): у паузы видео нет кольца хода" % dev_name, page.evaluate("() => getComputedStyle(document.querySelector('.mv-play')).backgroundImage") == 'none')
                if not phone:
                    page.evaluate("() => document.querySelector('.mv-media').pause()")
                    off = "() => { const p = document.querySelector('.mv-play').getBoundingClientRect(), v = document.querySelector('.mv-media').getBoundingClientRect(); return Math.max(Math.abs(p.left + p.width / 2 - v.left - v.width / 2), Math.abs(p.top + p.height / 2 - v.top - v.height / 2)); }"
                    o1 = page.evaluate(off)
                    page.keyboard.press("i")
                    page.wait_for_timeout(450)
                    o2 = page.evaluate(off)
                    page.keyboard.press("i")
                    page.wait_for_timeout(450)
                    page.locator(".mv-ctl button[aria-label^='Во весь']").click()
                    page.wait_for_timeout(600)
                    o3 = page.evaluate(off)
                    check(S, "просмотр (компьютер): кнопка воспроизведения по центру видео — обычно, со сведениями и во весь экран",
                          max(o1, o2, o3) <= 2, "смещение, точек: %s" % [round(o1), round(o2), round(o3)])
                    check(S, "просмотр (компьютер): видео во весь экран и в границах экрана",
                          page.evaluate("() => { const r = document.querySelector('.mv-media').getBoundingClientRect(); return document.fullscreenElement === document.querySelector('.mv-root') && r.right <= innerWidth + 1 && r.bottom <= innerHeight + 1; }"))
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(300)
            page.keyboard.press("Escape")
            page.evaluate("() => { const v = document.querySelector('.mv-root'); if (v) v.remove(); }")
        guard("просмотр (%s)" % dev_name, viewer)

        def select():
            page.evaluate(L_GROUP)
            page.wait_for_timeout(400)
            if not phone:
                items = page.evaluate("() => { const m = chatMessages.find(x => x.id === 99001); showContextMenu(300, 300, m, false); return [...document.querySelectorAll('.context-menu-item')].map(e => e.textContent.trim()); }")
                check(S, "компьютер: в меню сообщения есть «Выбрать»", any('Выбрать' in x for x in items), str(items))
                page.evaluate("() => { document.querySelectorAll('.context-menu').forEach(m => m.remove()); const m = chatMessages.find(x => x.id === 99002); showContextMenu(300, 300, m, true, m.files[1]); }")
                page.locator(".context-menu-item:has-text('Переслать файл')").click()
                check(S, "компьютер: «Переслать файл» на плитке группы — только этот файл", page.evaluate("() => window.__fw") == ['99012'], str(page.evaluate("() => window.__fw")))
                return
            page.evaluate(L_LONG, 1)
            page.wait_for_timeout(250)
            s1 = page.evaluate(L_STATE)
            check(S, "выбор (телефон): долгое нажатие на фото — выбран только он, просмотр не открылся",
                  s1['on'] and s1['count'] == 'Выбрано: 1' and s1['files'] == '010' and s1['circle'] == '/' and not s1['viewer'], str(s1))
            look = page.evaluate("""() => { const b = (""" + L_BUB + """)(99002), c = b.parentElement.querySelector(':scope > .sel-circle');
              const br = b.getBoundingClientRect(), cr = c.getBoundingClientRect(), cs = getComputedStyle(b);
              return { gap: Math.round(br.left - cr.right), shadow: cs.boxShadow, tap: cs.webkitTapHighlightColor }; }""")
            check(S, "выбор (телефон): кружок вплотную к своему сообщению, а не через весь экран", 0 <= look['gap'] <= 20, str(look))
            check(S, "выбор (телефон): часть группы — без синей рамки и подсветки касания у всего сообщения",
                  look['shadow'] == 'none' and look['tap'] in ('rgba(0, 0, 0, 0)', 'transparent'), str(look))
            page.evaluate(L_CIRCLE, 99002)
            page.wait_for_timeout(150)
            s2 = page.evaluate(L_STATE)
            check(S, "выбор (телефон): кружок у сообщения — вся группа сразу", s2['files'] == '111' and s2['circle'] == 'v' and s2['count'] == 'Выбрано: 3', str(s2))
            page.evaluate(L_TAP, 1)
            page.evaluate(L_TAP, 2)
            page.evaluate(L_TAPMSG, 99001)
            page.wait_for_timeout(200)
            s3 = page.evaluate(L_STATE)
            check(S, "выбор (телефон): касаниями — часть группы и чужое; «Удалить» скрыто", s3['files'] == '100' and s3['count'] == 'Выбрано: 2' and s3['del'] is False, str(s3))
            page.locator("#selBar .sel-fwd").click()
            ops = page.evaluate("() => window.__ops")
            check(S, "выбор (телефон): пересылка — чужое целиком и выбранный файл группы, по порядку", ops == [{'id': 99001, 'files': None}, {'id': 99002, 'files': [99011]}], str(ops))
            page.evaluate(L_LONG, 2)
            page.wait_for_timeout(200)
            page.locator("#selBar .sel-del").click()
            check(S, "выбор (телефон): удаление части своей группы — номер именно этого файла", page.evaluate("() => window.__del") == ['99013'], str(page.evaluate("() => window.__del")))
            page.evaluate(L_TAPMSG, 99001)
            page.wait_for_timeout(350)
            items = page.evaluate("() => [...document.querySelectorAll('.context-menu-item')].map(e => e.textContent.trim())")
            check(S, "выбор (телефон): в меню сообщения нет «Выбрать» (выбор — долгим нажатием)", bool(items) and not any('Выбрать' in x for x in items), str(items))
            page.mouse.click(5, 700)
        guard("выбор (%s)" % dev_name, select)

        if phone:
            def top_bar():
                bar = "() => { const t = document.querySelector('.app > .top-bar'); return !!t && getComputedStyle(t).display !== 'none'; }"
                open_route(page, WEB, "#/messenger")
                page.wait_for_timeout(1200)
                page.evaluate("() => { const k = document.getElementById('kovChat'), b = document.getElementById('kovChatBack'); if (k && b && k.classList.contains('chat-open')) b.click(); }")
                page.wait_for_timeout(600)
                had = page.evaluate(bar)
                page.locator("#chatListContainer .chat-item").first.click()
                page.wait_for_timeout(1000)
                inside = page.evaluate(bar)
                page.locator("#kovChatBack").click()
                page.wait_for_timeout(500)
                check(S, "мессенджер (телефон): в открытом чате нет верхней строки, после «‹» она на месте", had and not inside and page.evaluate(bar))
            guard("шапка мессенджера", top_bar)

            def sheet():
                def drag(dist):
                    page.evaluate("() => { window.__s = Kov.UI.bottomSheet('Проба', document.createTextNode('меню')); }")
                    page.wait_for_timeout(450)
                    page.evaluate("""(d) => { const hd = window.__s.root.querySelector('.kov-sheet-handle'), r = hd.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + 2;
                      const t = yy => [new Touch({ identifier: 1, target: hd, clientX: x, clientY: yy })];
                      hd.dispatchEvent(new TouchEvent('touchstart', { touches: t(y), changedTouches: t(y), bubbles: true, cancelable: true }));
                      for (let k = 1; k <= 6; k++) window.dispatchEvent(new TouchEvent('touchmove', { touches: t(y + d * k / 6), changedTouches: t(y + d * k / 6), bubbles: true, cancelable: true }));
                      hd.dispatchEvent(new TouchEvent('touchend', { touches: [], changedTouches: t(y + d), bubbles: true, cancelable: true })); }""", dist)
                    page.wait_for_timeout(550)
                    return page.evaluate("() => window.__s.root.isConnected")
                closed, kept = drag(220), drag(18)
                check(S, "меню с полоской: протянуть вниз — свернулось, короткое движение — осталось", closed is False and kept is True, str((closed, kept)))
            guard("меню с полоской", sheet)

            def task_filter_phone():
                # проба «меню с полоской» оставляет открытой одну шторку — убрать, иначе она накрывает кнопку
                page.evaluate("() => { try { window.__s && window.__s.close && window.__s.close(); } catch (e) {} document.querySelectorAll('.kov-sheet-root').forEach(r => r.remove()); }")
                open_route(page, WEB, "#/tasks")
                page.wait_for_timeout(1500)
                page.locator("button[title*='ильтр'], button:has-text('Фильтр')").first.click()
                page.wait_for_timeout(600)
                t = page.evaluate("() => { const s = document.querySelector('.kov-sheet-root .bottom-sheet'); return s && !document.querySelector('.modal-root') ? s.querySelector('.kov-sheet-head b').textContent : null; }")
                check(S, "фильтр задач (телефон) — выезжающее снизу меню «Сортировка и фильтры»", t == 'Сортировка и фильтры', str(t))
                page.locator(".kov-sheet-root .sheet-select").first.click()
                page.wait_for_timeout(500)
                o = page.evaluate("() => { const r = [...document.querySelectorAll('.kov-sheet-root')].pop(); return { option: r.classList.contains('option-root'), search: !!r.querySelector('input'), title: (r.querySelector('b') || {}).textContent }; }")
                check(S, "фильтр задач (телефон): «Сортировка» — короткий список по высоте, без поиска (как в проектах)",
                      o["option"] and not o["search"] and o["title"] == "Сортировка", str(o))
                page.evaluate("() => document.querySelectorAll('.kov-sheet-root').forEach(r => r.remove())")
                # форма задачи на телефоне: отдел этапа выбирается шторкой (.sheet-select), этап уходит на сервер
                sent = []
                grab = lambda r: sent.append(r.post_data) if r.method == "POST" and r.url.rstrip("/").endswith("/tasks") else None
                page.on("request", grab)
                open_route(page, WEB, "#/tasks/new")
                page.fill(".content input.input", "Этап с телефона")
                page.evaluate("() => { const d = document.querySelector('.content input[type=date]'); d.value = new Date(Date.now() + 5 * 864e5).toISOString().slice(0, 10); d.dispatchEvent(new Event('change', { bubbles: true })); }")
                page.locator(".content button:has-text('Сохранить')").last.click()
                page.wait_for_timeout(1500)
                page.remove_listener("request", grab)
                body = next((x for x in sent if x), "") or "{}"
                stages = [x.get("department_name") for x in (json.loads(body).get("stages") or [])]
                check(S, "форма задачи (телефон): отдел этапа из шторки уходит на сервер — задача создаётся с этапом", bool(stages) and all(stages), str(stages))
                open_route(page, WEB, "#/employees")
                page.wait_for_timeout(1500)
                page.locator("button:has-text('Должности')").first.click()
                page.wait_for_timeout(600)
                t = page.evaluate("() => { const s = document.querySelector('.kov-sheet-root .bottom-sheet'); return s && !document.querySelector('.modal-root') ? s.querySelector('.kov-sheet-head b').textContent : null; }")
                check(S, "фильтр по должности (телефон) — выезжающее снизу меню", t == 'Фильтр по должности', str(t))
                page.evaluate("() => document.querySelectorAll('.kov-sheet-root').forEach(r => r.remove())")
            guard("фильтр задач (телефон)", task_filter_phone)
        else:
            def stages():
                tid = page.evaluate("async () => (await Kov.Api.get('tasks?is_archived=false'))[0].id")

                def patch_task(route):
                    resp = route.fetch()
                    data = resp.json()
                    key = next((k for k, v in data.items() if isinstance(v, list) and v and isinstance(v[0], dict) and 'stage_number' in v[0]), None)
                    if key:
                        st = data[key]
                        data[key] = [dict(st[0], stage_number=1, is_completed=False), dict(st[0], stage_number=2, is_completed=False)] + [dict(x, stage_number=k + 3) for k, x in enumerate(st[1:])]
                    data['state'] = 1
                    route.fulfill(response=resp, body=json.dumps(data), headers={**resp.headers, "content-type": "application/json"})
                page.route(re.compile(r".*/tasks/%d(\?.*)?$" % tid), patch_task)
                page.evaluate("() => { window.__orig = Kov.Rights.canOpenStage; Kov.Rights.canOpenStage = s => s.stage_number !== 1; location.hash = '#/tasks/' + arguments[0]; }".replace("arguments[0]", str(tid)))
                page.wait_for_timeout(2000)
                on = page.evaluate("() => { const b = document.querySelector('.stage-nav .btn.on'); return b ? b.dataset.stage : null; }")
                check(S, "задача: текущий этап чужого отдела — открывается первый свой", on == '2', str(on))
                page.evaluate("() => { Kov.Rights.canOpenStage = () => false; location.hash = '#/tasks'; }")
                page.wait_for_timeout(700)
                page.evaluate("(t) => { location.hash = '#/tasks/' + t; }", tid)
                page.wait_for_timeout(2000)
                txt = page.evaluate("() => [...document.querySelectorAll('.empty')].map(e => e.textContent).join(' ')")
                check(S, "задача: своих этапов нет — содержимое закрыто, пояснение", 'другие отделы' in txt, txt[:80])
                page.evaluate("() => { Kov.Rights.canOpenStage = window.__orig; }")
            guard("этапы задачи", stages)

            def v62():
                page.evaluate("""() => Kov.Files.viewer([{ id: 501, filename: 'фото.jpg', task_id: 101, size: 120000, created_at: '2026-10-03T12:34:00',
                  uploaded_by: { first_name: 'Автотест', last_name: 'Кладовщик' } }], 0)""")
                page.wait_for_timeout(900)
                txt = page.evaluate("() => (document.querySelector('.mv-who') || {}).textContent + ' ' + ((document.querySelector('.mv-info') || {}).textContent || '')")
                check(S, "просмотр файла задачи: в сведениях нет «null», есть время", 'null' not in txt and '12:34' in txt, txt[:90])
                page.keyboard.press("Escape")
                page.evaluate("() => { const v = document.querySelector('.mv-root'); if (v) v.remove(); location.hash = '#/tasks'; }")
                page.wait_for_timeout(1500)
                page.locator("button[title*='ильтр'], button:has-text('Фильтр')").first.click()
                page.wait_for_timeout(600)
                # beta 3: на компьютере — прежнее окно «Фильтрация задач», шторка — только на телефоне
                t1 = page.evaluate("() => { const m = document.querySelector('.modal-root'); return m && !document.querySelector('.kov-sheet-root') ? m.textContent : null; }")
                check(S, "фильтр задач (компьютер) — прежнее окно «Фильтрация задач», не шторка", bool(t1) and 'Фильтрация задач' in t1, str(t1)[:60])
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
                page.evaluate("() => document.querySelectorAll('.modal-root, .kov-sheet-root').forEach(r => r.remove())")
                page.evaluate("() => { location.hash = '#/employees'; }")
                page.wait_for_timeout(1500)
                page.locator("button:has-text('Должности')").first.click()
                page.wait_for_timeout(500)
                t2 = page.evaluate("() => { const m = document.querySelector('.modal-root'); return m && !document.querySelector('.kov-sheet-root') ? m.textContent : null; }")
                check(S, "фильтр по должности (компьютер) — прежнее окно, не шторка", bool(t2) and 'Фильтр по должности' in t2, str(t2)[:60])
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
                page.evaluate("() => document.querySelectorAll('.modal-root, .kov-sheet-root').forEach(r => r.remove())")
                pid = page.evaluate("async () => (await Kov.Api.get('projects'))[0].id")

                def patch_ptasks(route):
                    resp = route.fetch()
                    data = resp.json()
                    if data:
                        data[0]['can_open'] = False
                    route.fulfill(response=resp, body=json.dumps(data), headers={**resp.headers, "content-type": "application/json"})
                page.route(re.compile(r".*/projects/%d/tasks(\?.*)?$" % pid), patch_ptasks)
                page.evaluate("(p) => { location.hash = '#/projects/' + p; }", pid)
                page.wait_for_timeout(2000)
                before = page.evaluate("() => location.hash")
                page.locator(".ptask.no-access, tr.no-access").first.dblclick()
                page.wait_for_timeout(600)
                check(S, "проект: задача без доступа видна, приглушена и не открывается",
                      page.evaluate("() => location.hash") == before and page.evaluate("() => document.querySelectorAll('.ptask.no-access, tr.no-access').length") >= 1)
                keys = []
                page.on("request", lambda r: keys.append(r.url.split('/preferences/')[1]) if '/preferences/' in r.url and r.method == 'PUT' else None)
                page.evaluate("async () => { const cur = await Kov.Store.pref('web.settings'); await Kov.Store.setPref('web.settings', cur || {}); }")   # содержимое не меняем — проверяем только ключ
                page.wait_for_timeout(300)
                check(S, "настройки компьютера хранятся отдельно (ключ .pc)", bool(keys) and keys[-1] == 'web.settings.pc', str(keys))
            guard("v62", v62)

            def v63():
                def patch_project(route):
                    resp = route.fetch()
                    d = resp.json()
                    if isinstance(d, dict):
                        d.update(can_complete=True, tasks_unfinished=1, is_archived=False)
                    route.fulfill(response=resp, body=json.dumps(d), headers={**resp.headers, "content-type": "application/json"})
                page.unroute(re.compile(r".*/projects/\d+/tasks(\?.*)?$"))
                page.route(re.compile(r".*/projects/\d+(\?.*)?$"), patch_project)
                page.evaluate("() => { location.hash = '#/projects'; }")
                page.wait_for_timeout(1500)
                r = page.evaluate("() => { const a = document.querySelector('.list-actions'), s = a && a.querySelector('.proj-arch'); return !!s && a.firstElementChild.contains(s) && !document.body.innerText.includes('null'); }")
                check(S, "проекты: «Активные / Архивные» — в левом углу строки кнопок", r)
                pid2 = page.evaluate("async () => (await Kov.Api.get('projects'))[0].id")
                page.evaluate("(p) => { location.hash = '#/projects/' + p; }", pid2)
                page.wait_for_timeout(2000)
                r = page.evaluate("""() => { const i = document.querySelector('.project-right > .proj-info'), b = document.querySelector('.proj-top .proj-done button');
                  return { right: !!i && !document.querySelector('.proj-head .proj-info'), btn: !!b && b.disabled, hint: (document.querySelector('.proj-top .proj-done-hint') || {}).textContent || '' }; }""")
                check(S, "проект: сведения справа над подразделами, «Завершить проект» недоступна при незавершённых задачах",
                      r['right'] and r['btn'] and 'незавершённых задач: 1' in r['hint'], str(r))
                page.evaluate("() => { const t = document.querySelector('input[type=date]'); return 0; }")
                page.evaluate("() => { location.hash = '#/tasks/new'; }")
                page.wait_for_timeout(1500)
                mn = page.evaluate("() => (document.querySelector('input[type=date]') || {}).min")
                check(S, "новая задача: срок не раньше сегодняшнего дня", mn == page.evaluate("() => Kov.UI.isoDate(new Date())"), str(mn))
            guard("v63", v63)

            def v65():
                RO = "() => { const s = document.querySelector('.content > .section'); return { on: document.documentElement.classList.contains('kov-readonly') && Kov.Rights.readOnly(), text: s ? getComputedStyle(s, '::before').content : '' }; }"

                def archived_project(route):
                    resp = route.fetch()
                    d = resp.json()
                    if isinstance(d, dict):
                        d.update(is_archived=True, can_complete=True, can_edit=False, can_upload=False, tasks_unfinished=0)
                    route.fulfill(response=resp, body=json.dumps(d), headers={**resp.headers, "content-type": "application/json"})
                page.unroute(re.compile(r".*/projects/\d+(\?.*)?$"))
                page.route(re.compile(r".*/projects/\d+(\?.*)?$"), archived_project)
                pid3 = page.evaluate("async () => (await Kov.Api.get('projects'))[0].id")
                page.evaluate("(p) => { location.hash = '#/projects/' + p; }", pid3)
                page.wait_for_timeout(2000)
                r = page.evaluate(RO)
                check(S, "завершённый проект: только просмотр — пояснение сверху, кнопок задач нет",
                      r['on'] and 'Проект завершён' in r['text'] and bool(page.evaluate("() => document.querySelector('.ro-note')")), str(r)[:160])
                page.evaluate("() => { location.hash = '#/tasks'; }")
                page.wait_for_timeout(1200)
                r = page.evaluate(RO)
                check(S, "после ухода из завершённого проекта режим «только просмотр» снят", not r['on'] and 'Проект завершён' not in r['text'], str(r)[:160])
                tid = page.evaluate("async () => (await Kov.Api.get('tasks', { query: { is_archived: false } }))[0].id")

                def archived_task(route):
                    resp = route.fetch()
                    d = resp.json()
                    if isinstance(d, dict):
                        d.update(project_archived=True)
                    route.fulfill(response=resp, body=json.dumps(d), headers={**resp.headers, "content-type": "application/json"})
                page.route(re.compile(r".*/tasks/%d(\?.*)?$" % tid), archived_task)
                page.evaluate("(t) => { location.hash = '#/tasks/' + t; }", tid)
                page.wait_for_timeout(2000)
                r = page.evaluate(RO)
                check(S, "задача завершённого проекта: только просмотр, управлять задачей нельзя",
                      r['on'] and 'задача только для просмотра' in r['text'] and page.evaluate("() => Kov.Rights.canManageTask({ creator_id: 0 }) === false"), str(r)[:160])
                page.unroute(re.compile(r".*/tasks/%d(\?.*)?$" % tid))
            guard("v65", v65)

            def v66():
                r = page.evaluate("""() => { const was = document.documentElement.dataset.theme; document.documentElement.dataset.theme = 'dark';
                  const cs = getComputedStyle(document.documentElement), v = k => cs.getPropertyValue(k).trim().toLowerCase();
                  const out = { chat: v('--col-bg-chat'), side: v('--col-bg-sidebar'), other: v('--msg-other-bg'), app: v('--card-bg') };
                  document.documentElement.dataset.theme = was; return out; }""")
                check(S, "тёмная тема: мессенджер в палитре приложения (панель как карточки #1C1C1E), лента не чисто чёрная (#17171A), пузыри #2C2C30",
                      r['chat'] == '#17171a' and r['side'] == r['app'] == '#1c1c1e' and r['other'] == '#2c2c30', str(r))
            guard("v66", v66)

            def chat_fixes():
                open_route(page, WEB, "#/messenger")
                page.wait_for_timeout(2000)
                page.locator(".chat-item").first.click()
                page.wait_for_timeout(1500)
                r = page.evaluate("""() => {
                  const bubble = document.querySelector('#kovChat .msg-bubble'); const w = bubble.parentElement;
                  const msg = Object.assign({}, bubble.__msg, { reactions_detailed: [{ reaction_type: 'like', user_id: 999, name: 'Т' }] });
                  patchMessageLight(w, msg, !!bubble.__isMine); const first = bubble.__reactionsEl;
                  patchMessageLight(w, Object.assign({}, msg), !!bubble.__isMine);
                  return bubble.__reactionsEl === first; }""")
                check(S, "реакции: при обновлении сообщения (новое сообщение, галочки) строка реакций не пересоздаётся — эмодзи не мигают", r)
                page.locator("#btnEmoji").click()
                page.wait_for_timeout(600)
                page.locator(".media-tab[data-tab='stickers']").first.click()
                page.wait_for_timeout(600)
                d = page.evaluate("""() => {
                  const img = document.querySelector('.sticker-cell img');
                  const target = document.querySelector('#kovChat .messages-list, #messagesList') || document.querySelector('#kovChat');
                  img.dispatchEvent(new DragEvent('dragstart', { bubbles: true }));
                  const dt = new DataTransfer(); dt.items.add(new File(['<svg/>'], 'sticker.svg', { type: 'image/svg+xml' }));
                  ['dragenter', 'dragover', 'drop'].forEach(t => target.dispatchEvent(new DragEvent(t, { bubbles: true, cancelable: true, dataTransfer: dt })));
                  document.dispatchEvent(new DragEvent('dragend', { bubbles: true }));
                  return { draggable: img.draggable, dialog: !!document.querySelector('.modal-root') }; }""")
                check(S, "стикер не перетаскивается, перенос изнутри страницы в чат ничего не отправляет", not d["draggable"] and not d["dialog"], str(d))
                page.evaluate("() => document.querySelectorAll('.modal-root').forEach(m => m.remove())")
            guard("чат: реакции и перенос стикеров", chat_fixes)
        ctx.close()


def write_report(started, suites):
    """Отчёт общий для всех запусков: результаты блоков, не запускавшихся сейчас, сохраняются."""
    os.makedirs(REPORT, exist_ok=True)
    path = os.path.join(REPORT, "report.json")
    if os.path.isfile(path):
        try:
            old = json.load(open(path, encoding="utf-8"))
            kept = [r for r in old.get("results", []) if r["suite"] not in suites]
            RESULTS[:0] = kept
            OBSERVATIONS[:0] = [o for o in old.get("observations", []) if o not in OBSERVATIONS
                                and not ("parity" in suites and o.startswith("шум")) and not ("fixes" in suites and "мессенджер" in o)]
        except (ValueError, KeyError):
            pass
    ok = sum(1 for r in RESULTS if r["ok"])
    with open(os.path.join(REPORT, "report.json"), "w", encoding="utf-8") as f:
        json.dump({"date": dt.datetime.now().isoformat(timespec="seconds"), "passed": ok, "total": len(RESULTS), "results": RESULTS,
                   "observations": OBSERVATIONS},
                  f, ensure_ascii=False, indent=1)
    lines = ["# Проверка приложения на ПК", "", "Дата: %s, длительность: %.0f с" % (dt.datetime.now().strftime("%d.%m.%Y %H:%M"), time.time() - started),
             "", "Итого: **%d из %d** проверок пройдено." % (ok, len(RESULTS)), ""]
    for suite, title in (("parity", "Внешний вид (веб-версия и APK)"), ("rights", "Права по ролям"), ("overlay", "Надстройка приложения"),
                         ("fixes", "Исправления 25.09.2026"), ("sim", "Симулятор телефона"), ("live", "Переписка и файлы"), ("latest", "Последние версии (v45–v67)")):
        rows = [r for r in RESULTS if r["suite"] == suite]
        if not rows:
            continue
        lines += ["## " + title, ""]
        lines += ["- %s %s%s" % ("✅" if r["ok"] else "❌", r["name"], "" if r["ok"] else " — " + str(r["detail"])[:200]) for r in rows]
        lines.append("")
    if OBSERVATIONS:
        lines += ["## Наблюдения", ""] + ["- " + o for o in OBSERVATIONS] + [""]
    with open(os.path.join(REPORT, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return ok == len(RESULTS)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    quick = "--quick" in sys.argv
    suites = args or ["overlay", "fixes", "sim", "live", "rights", "latest", "parity"]
    started = time.time()
    os.makedirs(REPORT, exist_ok=True)
    start_servers()
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--host-resolver-rules=MAP kovapp.test 127.0.0.1"])
        if "overlay" in suites:
            suite_overlay(browser)
        if "fixes" in suites:
            suite_fixes(browser)
        if "sim" in suites:
            suite_sim(browser)
        if "live" in suites:
            suite_live(browser)
        if "rights" in suites:
            suite_rights(browser)
        if "latest" in suites:
            suite_latest(browser)
        if "parity" in suites:
            suite_parity(browser, quick)
        browser.close()
    good = write_report(started, suites)
    print("\nИтого: %d из %d. Отчёт: %s" % (sum(r["ok"] for r in RESULTS), len(RESULTS), os.path.join(REPORT, "report.md")))
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
