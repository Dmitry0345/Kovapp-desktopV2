"""Приложение на настоящем Android: вход, разделы на реальном экране, «Назад», поворот."""
import time

from kovtest import case

ROUTES = [("tasks", "#/tasks"), ("projects", "#/projects"), ("messenger", "#/messenger"),
          ("notifications", "#/notifications"), ("employees", "#/employees"), ("equipment", "#/equipment"),
          ("settings", "#/settings"), ("settings_look", "#/settings/look"), ("settings_notify", "#/settings/notify"),
          ("settings_device", "#/settings/device")]
MENU_OPEN = "document.querySelector('.side-menu') && document.querySelector('.side-menu').classList.contains('open')"


def dev(t):
    if t.world.device is None:
        t.skip("только для тестов на устройстве (run.cmd device)")
    t.world.device.ensure_app(t)
    return t.world.device


@case("Вход через экран входа приложения", layer="device", order=1)
def login_screen(t):
    d = dev(t)
    d.clear_app()                               # как после установки: входа ещё не было
    d.activate()
    t.require(d.wait_js("document.querySelector('.login-card')", 40), "экран входа не появился")
    account = t.account("director")
    d.fill(".login-card input[placeholder='Логин']", account.login)
    d.fill(".login-card input[type=password]", account.password)
    d.press_enter()
    t.check(d.wait_js("document.querySelector('.app .content')", 40), "после входа не открылся интерфейс")
    d.screenshot(str(t.world.log.dir / "screenshots" / "device_after_login.png"))


@case("Разделы на экране телефона: вёрстка", layer="device", order=2)
def sections(t):
    d = dev(t)
    d.login("director", "#/tasks")
    for key, route in ROUTES:
        t.step("раздел %s" % route)
        d.go(route)
        t.check(d.wait_js("document.querySelector('.app .content')", 20), "раздел %s не открылся" % route)
        d.audit(t, "device_" + key)


@case("Кнопка «Назад» телефона: закрывает меню; на первом экране — сворачивает", layer="device", order=3)
def back_button(t):
    d = dev(t)
    d.login("director", "#/tasks")
    t.require(d.click(".menu-btn"), "нет кнопки меню")
    t.require(d.wait_js(MENU_OPEN, 5), "меню не открылось")
    d.back()
    time.sleep(1)
    t.check(not d.js("return !!(%s);" % MENU_OPEN), "«Назад» не закрыл меню")
    t.check(d.app_on_screen(), "«Назад» при открытом меню свернул приложение")
    # начало истории — приложение только что открыто: «Назад» должен его свернуть
    d.restart_app()
    t.require(d.wait_js("document.querySelector('.app .content')", 40), "после перезапуска интерфейс не появился")
    where = d.js("return location.hash;")
    d.back()
    time.sleep(1.5)
    if d.app_on_screen():
        t.fail("«Назад» на первом экране после запуска не свернул приложение (был %s, стал %s)"
               % (where, d.js("return location.hash;")))
    d.activate()
    t.check(d.wait_js("document.querySelector('.app .content')", 20), "после возврата интерфейс не на месте")


@case("Поворот экрана", layer="device", order=4)
def rotation(t):
    d = dev(t)
    d.login("director", "#/tasks")
    t.later(lambda: d.rotate(False))
    d.rotate(True)
    d.audit(t, "device_landscape_tasks")
    d.go("#/messenger")
    d.audit(t, "device_landscape_messenger")
    d.rotate(False)
    d.go("#/tasks")
    d.audit(t, "device_portrait_again")
