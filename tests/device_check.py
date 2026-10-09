"""Готовность: эмулятор, установка приложения, страница внутри, связь с сервером, ключи Firebase."""
from kovtest import case


def dev(t):
    if t.world.device is None:
        t.skip("только для тестов на устройстве (run.cmd device)")
    t.world.device.ensure_app(t)
    return t.world.device


@case("Эмулятор подключён, приложение установлено начисто", layer="device", order=1)
def connected(t):
    d = dev(t)
    t.step("устройство: %s, Android %s (API %d), сервисы Google: %s" % (d.model, d.release, d.sdk, "есть" if d.gms else "нет"))
    t.step("приложение: версия %s" % (d.app_version or "не определена"))
    t.check(bool(d.app_version), "приложение не установилось на эмулятор")


@case("Приложение на экране, страница внутри доступна тестам", layer="device", order=2)
def starts(t):
    d = dev(t)
    d.restart_app()
    t.check(d.app_on_screen(), "на экране не приложение: %s" % d.resumed())
    t.check(d.wait_js("document.querySelector('.login-card, .app .content')", 40), "интерфейс приложения не появился")


@case("Приложение достаёт до сервера", layer="device", order=3)
def reaches_server(t):
    d = dev(t)
    d.login("director", "#/tasks")
    status = d.fetch_status("/push/native/config")
    t.check(status == 200, "запрос к серверу из приложения: %s" % (status or "нет связи"))


@case("Ключи Firebase: Google принимает ключ, отправка разрешена", layer="device", order=4)
def firebase_keys(t):
    d = dev(t)
    keys = d.keys
    if not keys.get("present"):
        t.skip("в папке keys нет файлов google_key и google-services — доставка через Google не проверяется")
    problems = "; ".join(keys.get("problems") or ["причина не записана"])
    if not keys.get("checked", True):
        t.fail("ключи не удалось проверить: %s" % problems)
    else:
        t.check(keys.get("valid"), "ключи не работают: %s" % problems)


@case("Приложение не падало", layer="device", order=99)
def no_crashes(t):
    crashes = dev(t).crashes()
    t.check(not crashes, "приложение падало: %s" % (crashes[0][:600] if crashes else ""))
