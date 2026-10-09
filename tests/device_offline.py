"""Связь с сервером на настоящем Android: сервер пропал — интерфейс на месте, вернулся — работа продолжается."""
import time

from kovtest import case


def dev(t):
    if t.world.device is None:
        t.skip("только для тестов на устройстве (run.cmd device)")
    t.world.device.ensure_app(t)
    return t.world.device


@case("Сервер недоступен: интерфейс на месте; вернулся — приложение снова получает данные", layer="device", order=1)
def server_lost_and_back(t):
    d = dev(t)
    d.login("director", "#/tasks")
    t.later(lambda: d.link(True))
    d.link(False)
    time.sleep(2)
    t.check(d.fetch_status("/push/native/config") == 0, "сервер «пропал», а запрос всё равно прошёл")
    d.js("location.hash = '#/notifications';")
    time.sleep(3)
    t.check(d.js("return !!document.querySelector('.app .content');"), "без сервера интерфейс пропал")
    t.check(d.app_on_screen(), "без сервера приложение закрылось")
    d.link(True)
    code = 0
    for _ in range(20):
        code = d.fetch_status("/push/native/config")
        if code == 200:
            break
        time.sleep(1)
    t.check(code == 200, "после возврата сервера запросы не проходят: %s" % (code or "нет связи"))
    d.go("#/tasks")
    t.check(d.wait_js("document.querySelector('.app .content')", 20), "после возврата связи раздел не открылся")
