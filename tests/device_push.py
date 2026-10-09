"""Уведомления на настоящем Android: разрешение, канал (Google или собственный), шторка, переход."""
import time
import uuid

from kovtest import case
from kovtest.device import SERVICE_NOTIFICATION_ID
from kovtest.ws import Socket

WAIT = 60
STATUS = """const done = arguments[arguments.length - 1];
            Kov.PushClient.status().then(done).catch(e => done({ error: String(e) }));"""
MODE = "return ((window.KovNative && KovNative.state && KovNative.state.push) || {}).mode || '';"
NOTE = "return (document.querySelector('.set-card .set-note') || {}).textContent || '';"


def dev(t):
    if t.world.device is None:
        t.skip("только для тестов на устройстве (run.cmd device)")
    t.world.device.ensure_app(t)
    return t.world.device


def status(d) -> dict:
    return d.js_async(STATUS) or {}


def click_text(d, text) -> bool:
    return d.click_text(text, timeout=5)


@case("Разрешение: отозвали → предложение «Включить» → системный запрос → разрешено", layer="device", order=1)
def permission_flow(t):
    d = dev(t)
    if d.sdk < 33:
        t.skip("до Android 13 разрешения на уведомления нет — его не спрашивают")
    d.login("worker_mont", "#/tasks")
    d.set_permission(False)                     # Android при этом закрывает приложение
    d.reset_permission_flags()                  # как у нового приложения: запрос может появиться снова
    d.restart_app()
    d.js("sessionStorage.setItem('kov.autotest.offer', '1'); localStorage.removeItem('kov.notify.offer'); location.reload();")
    t.require(d.wait_js("document.querySelector('.kov-perm-offer')", 20),
              "после отзыва разрешения приложение не предложило включить уведомления")
    t.require(click_text(d, "Включить") or click_text(d, "Разрешить"), "в предложении нет кнопки «Включить»")
    if not d.allow_permission_dialog():
        t.fail("системный запрос разрешения не появился (на экране: %s)" % d.resumed())
        return
    t.check(d.wait_permission(), "в системном запросе нажато «Разрешить», но разрешение не выдано")


@case("Уведомления включаются; канал выбран по телефону и ключам", layer="device", order=2)
def enable_and_channel(t):
    d = dev(t)
    d.set_permission(True)
    d.ensure_app(t)                             # выдача разрешения может перезапустить приложение
    d.login("worker_mont", "#/settings/device")
    keys = d.keys
    want = "fcm" if (d.gms and keys.get("present")) else "direct"
    state = status(d)
    if not state.get("subscribed"):
        t.require(click_text(d, "Включить на этом устройстве"), "нет кнопки «Включить на этом устройстве»: %s" % state)
        time.sleep(3)
        dialog = d.system_dialog()
        if dialog:
            d.answer_system_dialog()
            if want == "fcm":
                t.fail("доставка через Google возможна, а приложение открыло системное окно %s — "
                       "«работа в фоне» нужна только собственному каналу" % dialog)
            else:
                t.step("Android спросил разрешение на работу в фоне — разрешено")
        time.sleep(5)
        state = status(d)
    t.require(state.get("subscribed"), "устройство не подписалось на уведомления: %s" % state)
    t.step("сервисы Google: %s; ключи: %s" % ("есть" if d.gms else "нет",
           "в порядке" if keys.get("valid") else ("не проверены" if keys.get("present") and not keys.get("checked", True)
                                                  else ("с ошибками" if keys.get("present") else "нет"))))
    # приложение выбирает Google, если на телефоне есть сервисы Google, а у приложения и сервера — ключи.
    # У только что установленного приложения Google выдаёт адрес не сразу: сначала работает
    # собственный канал, затем приложение переходит на Google — ждём до 90 с
    started = time.time()
    push = d.push_state()
    while push.get("mode") != want and time.time() - started < 180:
        time.sleep(3)
        push = d.push_state()
    mode = push.get("mode") or "не определён"
    if mode == want:
        t.step("канал: %s%s" % (mode, (" — перешло на Google через %d с после включения" % (time.time() - started))
                                 if want == "fcm" and time.time() - started > 3 else ""))
    else:
        t.fail("ожидался канал «%s», а за 3 мин остался «%s». Приложение пишет: %s"
               % (want, mode, d.js(NOTE).strip() or push.get("reason") or "—"))
    # постоянное уведомление: Android показывает его с задержкой до 10 с — ждём до 20 с
    deadline = time.time() + 20
    service = [n for n in d.notifications() if n["id"] == SERVICE_NOTIFICATION_ID]
    while mode == "direct" and not service and time.time() < deadline:
        time.sleep(2)
        service = [n for n in d.notifications() if n["id"] == SERVICE_NOTIFICATION_ID]
    while mode == "fcm" and service and time.time() < deadline:
        time.sleep(2)
        service = [n for n in d.notifications() if n["id"] == SERVICE_NOTIFICATION_ID]
    if mode == "direct":
        t.check(service, "собственный канал работает, но нет уведомления «Kovapp на связи» — Android остановит соединение")
    elif mode == "fcm":
        t.check(not service, "доставка через Google, а постоянное уведомление «Kovapp на связи» не убрано")


@case("Сообщение, пока приложение свёрнуто: шторка → нажатие открывает чат", layer="device", order=3)
def chat_notification_tap(t):
    d = dev(t)
    group = t.data.group_chat("director", ["worker_mont"], "Чат устройства")
    d.login("worker_mont", "#/tasks")
    t.require(status(d).get("subscribed"), "уведомления на устройстве не включены (раздел выше)")
    mode = d.js(MODE)
    d.home()
    text = "Проверка устройства %d" % (int(time.time()) % 100000)
    socket_ = Socket(t.settings.base_url, t.api("director").token, t.log)
    try:
        socket_.send({"type": "message", "chat_id": group["id"], "text": text, "client_id": "c" + uuid.uuid4().hex[:10]})
        got = d.wait_notification(lambda n: text in (n["text"] or ""), WAIT)
    finally:
        socket_.close()
    t.require(got, "уведомление о сообщении не пришло на телефон за %d с (канал: %s)" % (WAIT, mode or "не определён"))
    t.step("доставлено через %s" % ("Google" if mode == "fcm" else "собственный канал"))
    d.open_shade()
    t.later(lambda: d.close_shade() if not d.app_on_screen() else None)
    t.require(d.tap_native([text[:24]], 10), "уведомление не нашлось в шторке")
    time.sleep(3)
    t.check(d.app_on_screen(), "нажатие на уведомление не открыло приложение: %s" % d.resumed())
    route = d.js("return location.hash;")
    t.check(route == "#/messenger/%d" % group["id"], "нажатие открыло %s, а не чат #/messenger/%d" % (route, group["id"]))


@case("Значок «Kovapp на связи»: при Google настройки нет; при собственном канале «Убрать значок» работает", layer="device", order=4)
def hide_icon_button(t):
    d = dev(t)
    d.login("worker_mont", "#/settings/device")
    push = d.push_state()
    button = "return [...document.querySelectorAll('button')].some(b => b.textContent.includes('Убрать значок') && b.getBoundingClientRect().height > 0);"
    if push.get("mode") == "fcm" or push.get("fcmExpected"):
        # доставка через Google: постоянного уведомления нет — и настройки для него быть не должно
        time.sleep(2)
        t.check(not d.js(button), "доставка через Google, а в «Это устройство» показана настройка «Убрать значок»")
        service = [n for n in d.notifications() if n["id"] == SERVICE_NOTIFICATION_ID]
        t.check(not service, "доставка через Google, а постоянное уведомление «Kovapp на связи» висит в шторке")
        t.step("канал: %s — настройки значка нет, постоянного уведомления нет" % (push.get("mode") or "—"))
        return
    if push.get("mode") != "direct":
        t.skip("уведомления на устройстве не включены (канал: %s)" % (push.get("mode") or "не определён"))
    t.require(click_text(d, "Убрать значок"), "в «Это устройство» нет кнопки «Убрать значок»")
    time.sleep(2.5)
    t.check(d.resumed().startswith("com.android.settings"), "не открылись настройки категории: %s" % d.resumed())
    d.back()
    time.sleep(2)
    t.check(d.app_on_screen(), "«Назад» из настроек не вернул в приложение: %s" % d.resumed())
