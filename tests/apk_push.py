import json
import time
import uuid

from kovtest import case
from kovtest.config import env_int
from kovtest.native_channel import NativeDevice
from kovtest.ws import Socket

WAIT = float(env_int("APK_PUSH_WAIT_S", 20))


def apk(t):
    if t.settings.target != "apk":
        t.skip("раздел мобильной версии: запуск run.cmd mobile")
    return t.world.apk


def phone(t, role, connect=True):
    """Телефон сотрудника с включёнными уведомлениями приложения."""
    device = NativeDevice(t.api(role), t.log, t.settings.base_url, who=role)
    device.register()
    t.later(device.close)
    t.later(lambda: _quiet(device.unregister))
    if connect:
        t.require(device.connect(), "канал уведомлений не принял телефон %s: %s" % (role, device.errors))
    return device


def _quiet(fn):
    try:
        fn()
    except Exception:
        pass


def notify(t, role, text):
    t.api("director").call("POST", "notifications", json={"user_ids": [t.account(role).id], "message": text,
                                                          "target_section": "notifications"}, expect=(200, 201))


def has(text):
    return lambda p: text[:40] in json.dumps(p, ensure_ascii=False)


# ===== сервер и канал =====

@case("Сервер включает уведомления для приложения", layer="mobile", order=1)
def server_config(t):
    apk(t)
    body = t.api("worker_to").call("GET", "push/native/config")
    t.check(body.get("enabled") is True, "на сервере выключены уведомления приложения (модуль native_push): %s" % body)
    t.check(body.get("direct") is True, "у сервера нет собственного канала для телефонов без Google")
    t.check(30 <= int(body.get("ping") or 0) <= 900, "странный интервал проверки связи: %s" % body.get("ping"))
    t.log.info("уведомления через Google (FCM) на сервере: %s" % ("да" if body.get("fcm") else "нет"))


@case("Уведомление раздела приходит в приложение по собственному каналу", layer="mobile", order=2)
def section_notification(t):
    apk(t)
    device = phone(t, "worker_to")
    text = t.data.name("Уведомление в приложение %d" % int(time.time()))
    notify(t, "worker_to", text)
    got = device.wait(has(text), WAIT)
    t.require(got, "уведомление не пришло в приложение за %d с" % WAIT)
    t.check(got.get("uid") == t.account("worker_to").id, "уведомление без получателя или чужое: uid=%s" % got.get("uid"))
    t.check((got.get("url") or "").startswith("#/"), "уведомление ведёт не на раздел приложения: %s" % got.get("url"))
    t.check(got.get("title") and got.get("tag") and got.get("id"), "у уведомления нет заголовка, метки или номера: %s" % got)


@case("Сообщение чата: приложению участника, но не автору и не постороннему", layer="mobile", order=3)
def chat_message(t):
    apk(t)
    group = t.data.group_chat("director", ["worker_mont"], "Чат приложения")
    member, author, outsider = phone(t, "worker_mont"), phone(t, "director"), phone(t, "worker_to")
    text = "Проверка канала %d" % (int(time.time()) % 100000)
    socket_ = Socket(t.settings.base_url, t.api("director").token, t.log)
    try:
        socket_.send({"type": "message", "chat_id": group["id"], "text": text, "client_id": "c" + uuid.uuid4().hex[:10]})
        got = member.wait(has(text), WAIT)
    finally:
        socket_.close()
    t.require(got, "участник не получил уведомление о сообщении за %d с" % WAIT)
    t.check(str(group["id"]) in (got.get("url") or ""), "уведомление ведёт не в тот чат: %s" % got.get("url"))
    t.check(got.get("kind") == "chat" and (got.get("tag") or "").startswith("chat-"), "уведомление чата без признака чата: %s" % got)
    time.sleep(3)
    t.check(not author.wait(has(text), 0.2), "автор сообщения получил уведомление о своём сообщении")
    t.check(not outsider.wait(has(text), 0.2), "посторонний получил уведомление из чужого чата")


@case("Телефон без связи получает накопленное при подключении, подтверждённое не повторяется", layer="mobile", order=4)
def offline_queue(t):
    apk(t)
    device = phone(t, "worker_to", connect=False)
    text = t.data.name("Пока телефон без связи %d" % int(time.time()))
    notify(t, "worker_to", text)
    time.sleep(1.5)
    t.require(device.connect(), "канал не принял телефон")
    t.check(device.wait(has(text), WAIT), "накопленное уведомление не пришло при подключении")
    device.flushed.wait(5)
    device.close()
    again = NativeDevice(t.api("worker_to"), t.log, t.settings.base_url, who="worker_to")
    again.device_id, again.secret = device.device_id, device.secret
    t.later(again.close)
    t.require(again.connect(), "повторное подключение не принято")
    again.flushed.wait(5)
    t.check(not again.wait(has(text), 1.5), "подтверждённое уведомление пришло повторно")


@case("Чужой секрет — отказ; телефон, перешедший к другому сотруднику, прежний секрет теряет", layer="mobile", order=5)
def device_secrets(t):
    apk(t)
    device = phone(t, "worker_to", connect=False)
    wrong = NativeDevice(t.api("worker_to"), t.log, t.settings.base_url, who="чужой секрет")
    wrong.device_id = device.device_id
    t.later(wrong.close)
    t.check(not wrong.connect(secret=device.secret + "x") and "unauthorized" in wrong.errors,
            "канал принял телефон с чужим секретом: %s" % wrong.errors)
    old_secret = device.secret
    device.register(t.api("worker_mont"))             # тот же телефон, другой сотрудник
    stale = NativeDevice(t.api("worker_to"), t.log, t.settings.base_url, who="прежний владелец")
    stale.device_id = device.device_id
    t.later(stale.close)
    t.check(not stale.connect(secret=old_secret), "после перехода телефона к другому сотруднику прежний секрет ещё действует")
    t.later(lambda: _quiet(lambda: device.unregister(t.api("worker_mont"))))


@case("Выход из учётной записи отвязывает телефон: уведомления больше не приходят", layer="mobile", order=6)
def logout_unbinds(t):
    apk(t)
    device = phone(t, "worker_to")
    device.unregister()
    text = t.data.name("После выхода %d" % int(time.time()))
    notify(t, "worker_to", text)
    time.sleep(3)
    t.check(not device.wait(has(text), 0.5), "отвязанный телефон получил уведомление")


@case("Контрольное уведомление: телефон подтверждает получение", layer="mobile", order=7)
def probe_ack(t):
    apk(t)
    device = phone(t, "worker_to")
    probe = uuid.uuid4().hex
    api = t.api("worker_to")
    api.call("POST", "push/test", json={"probe": probe})
    got = device.wait(lambda p: p.get("probe") == probe, WAIT)
    t.require(got, "контрольное уведомление не пришло в приложение")
    device.ack_probe(t.anon(), probe)
    acks = api.call("GET", "push/test/%s" % probe).get("acks") or []
    t.check(acks, "сервер не записал подтверждение от приложения")
    t.log.info("подтверждения контрольного уведомления: %s" % acks)


# ===== приложение целиком: симулятор телефона =====

@case("Симулятор: уведомления при свёрнутом и закрытом приложении, нажатие открывает раздел", layer="mobile", order=10)
def simulator_flow(t):
    env = apk(t)
    from tests.apk_sim import SimPhone
    sim = SimPhone(t, env)
    sim.login("worker_to")
    sim.enable_notifications()
    text = t.data.name("Приложение свёрнуто %d" % int(time.time()))
    sim.home()
    notify(t, "worker_to", text)
    note = sim.wait_shade(text, WAIT)
    t.require(note, "уведомление не появилось в шторке свёрнутого приложения")
    sim.tap_notification(text)
    t.check(sim.wait_hash("#/notifications", 10), "нажатие на уведомление не открыло раздел: %s" % sim.hash())
    sim.close_app()
    text2 = t.data.name("Приложение закрыто %d" % int(time.time()))
    notify(t, "worker_to", text2)
    t.require(sim.wait_shade(text2, WAIT), "уведомление не пришло при закрытом приложении")
    sim.tap_notification(text2)
    t.check(sim.wait_hash("#/notifications", 15), "холодный запуск из уведомления открыл не тот раздел: %s" % sim.hash())
    sim.assert_clean("уведомления в симуляторе")
