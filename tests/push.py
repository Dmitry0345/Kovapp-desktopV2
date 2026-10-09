import json
import time
import uuid

from kovtest import case
from kovtest.config import env, env_int
from kovtest.flows import wait_for
from kovtest.push_receiver import Keys, payload_of
from kovtest.ws import Socket


# ===== подготовка =====

def receiver(t):
    if t.settings.mode != "full" and not env("PUSH_RECEIVER_HOST"):
        t.skip("доставку проверяет тестовый push-сервис внутри Docker — только в полном режиме")
    return t.world.push_receiver


def subscribe(t, role, label):
    keys = Keys()
    box = "%s-%s" % (label, uuid.uuid4().hex[:8])
    subscription = keys.subscription(receiver(t).endpoint(box))
    t.api(role).call("POST", "push/subscribe", json=dict(subscription, user_agent="Kovapp autotest " + label))
    t.data.cache.setdefault("push_subs", []).append((role, subscription["endpoint"]))
    return keys, box, subscription["endpoint"]


def unsubscribe_all(t):
    for role, endpoint in t.data.cache.pop("push_subs", []):
        t.api(role).delete("push/subscribe", json={"endpoint": endpoint})


def only_message(t, box, what, timeout=20.0):
    got = t.world.push_receiver.wait(box, 1, timeout)
    t.require(got, "%s: push не пришёл за %.0f с" % (what, timeout))
    return got[0]


# ===== сервер =====

@case("Push включён и отдаёт открытый ключ", layer="api", order=1)
def push_config(t):
    config = t.api("worker_to").call("GET", "push/config")
    if t.settings.mode != "full" and not config.get("enabled"):
        t.skip("на сервере push выключен: нет ключей VAPID или pywebpush")
    t.check(config.get("enabled") is True, "push выключен")
    t.check(len(config.get("public_key") or "") > 40, "нет открытого ключа VAPID")


@case("Подписка принимается только на защищённый адрес", layer="api", order=2)
def subscribe_rules(t):
    keys = Keys()
    bad = t.api("worker_to").post("push/subscribe", json=keys.subscription("http://example.org/push/1"))
    t.expect_status(bad, (400, 422, 503), "подписка на адрес без https")
    t.expect_status(t.anon().post("push/subscribe", json=keys.subscription("https://example.org/push/1"), auth=False),
                    401, "подписка без входа")


@case("Уведомление раздела приходит push-сообщением адресату", layer="api", order=3)
def notification_push(t):
    try:
        keys, box, _ = subscribe(t, "worker_to", "notif")
        text = t.data.name("Push-уведомление %d" % int(time.time()))
        t.api("director").call("POST", "notifications", json={"user_ids": [t.account("worker_to").id], "message": text,
                                                              "target_section": "notifications"}, expect=(200, 201))
        message = only_message(t, box, "уведомление раздела")
        headers = {k.lower(): v for k, v in message["headers"].items()}
        t.check(headers.get("content-encoding") == "aes128gcm", "сообщение не зашифровано по стандарту: %s" % headers.get("content-encoding"))
        t.check((headers.get("authorization") or "").startswith("vapid "), "нет подписи сервера VAPID")
        t.check(int(headers.get("ttl") or 0) > 0, "не задан срок хранения сообщения (TTL)")
        payload = payload_of(keys, message)
        t.log.info("push: %s" % payload)
        t.check(text[:40] in json.dumps(payload, ensure_ascii=False), "в push нет текста уведомления: %s" % payload)
        t.check(payload.get("title"), "у push нет заголовка")
    finally:
        unsubscribe_all(t)


@case("Сообщение чата: push участникам, но не автору и не посторонним", layer="api", order=4)
def chat_push(t):
    try:
        group = t.data.group_chat("director", ["worker_mont"], "Push-чат")
        member_keys, member_box, _ = subscribe(t, "worker_mont", "member")
        _, author_box, _ = subscribe(t, "director", "author")
        _, outsider_box, _ = subscribe(t, "worker_to", "outsider")
        text = "Проверка push %d" % (int(time.time()) % 100000)
        socket_ = Socket(t.settings.base_url, t.api("director").token, t.log)
        try:
            socket_.send({"type": "message", "chat_id": group["id"], "text": text, "client_id": "c" + uuid.uuid4().hex[:10]})
            message = only_message(t, member_box, "сообщение чата")
        finally:
            socket_.close()
        payload = payload_of(member_keys, message)
        t.check(text in (payload.get("body") or ""), "в push нет текста сообщения: %s" % payload)
        t.check(str(group["id"]) in (payload.get("url") or ""), "push ведёт не в тот чат: %s" % payload.get("url"))
        time.sleep(3)
        t.check(not t.world.push_receiver.wait(author_box, 1, 0.1), "автор сообщения получил push о своём сообщении")
        t.check(not t.world.push_receiver.wait(outsider_box, 1, 0.1), "посторонний получил push из чужого чата")
    finally:
        unsubscribe_all(t)


@case("Отозванная подписка удаляется после ответа 410", layer="api", order=5)
def dead_subscription(t):
    try:
        _, box, endpoint = subscribe(t, "head_to", "dead")
        t.world.push_receiver.answer(box, 410)
        t.api("director").call("POST", "notifications", json={"user_ids": [t.account("head_to").id],
                                                              "message": "Автотест: мёртвая подписка",
                                                              "target_section": "notifications"}, expect=(200, 201))
        only_message(t, box, "уведомление на мёртвую подписку")
        gone = False
        for _ in range(20):
            rows = t.api("head_to").call("GET", "push/subscriptions")
            if not any("dead" in (r.get("user_agent") or "") for r in rows):
                gone = True
                break
            time.sleep(0.5)
        t.check(gone, "сервер не удалил подписку, отозванную push-сервисом (410)")
    finally:
        unsubscribe_all(t)


@case("Контрольное уведомление и подтверждение получения", layer="api", order=6)
def probe_flow(t):
    try:
        keys, box, endpoint = subscribe(t, "worker_buh", "probe")
        probe = uuid.uuid4().hex
        t.api("worker_buh").call("POST", "push/test", json={"probe": probe})
        payload = payload_of(keys, only_message(t, box, "контрольное уведомление"))
        t.check(payload.get("probe") == probe, "в контрольном уведомлении нет метки проверки")
        t.expect_status(t.anon().post("push/ack", json={"probe": probe, "endpoint": endpoint}, auth=False), 200,
                        "подтверждение от устройства")
        state = t.api("worker_buh").call("GET", "push/test/%s" % probe)
        t.check(state.get("acks") and state["acks"][0].get("subscription_id"), "подтверждение не привязалось к подписке")
        t.expect_status(t.api("worker_to").get("push/test/%s" % probe), 404, "чужая проверка")
        t.expect_status(t.anon().post("push/ack", json={"probe": uuid.uuid4().hex}, auth=False), 404,
                        "подтверждение с выдуманной меткой")
    finally:
        unsubscribe_all(t)


# ===== service worker в браузере =====

def sw_page(t):
    engine = t.params.get("engine") or t.settings.browsers[0]
    if engine not in ("chrome", "chromium", "msedge"):
        t.skip("имитировать доставку push можно только в движке Chrome")
    page = t.page("worker_mont", "light", "desktop", path="#/tasks", engine=engine)
    page.context.grant_permissions(["notifications"], origin=t.settings.base_url)
    ready = wait_for(page, "() => navigator.serviceWorker && navigator.serviceWorker.getRegistration().then(r => !!r)", 8)
    if not ready:
        page.raw.evaluate("() => navigator.serviceWorker.register('sw.js', { scope: './' })")
    t.require(wait_for(page, "() => navigator.serviceWorker.getRegistration().then(r => !!(r && r.active))", 15),
              "service worker не зарегистрировался — без HTTPS браузер его не пускает (TREAT_AS_SECURE=1)")
    cdp = page.context.new_cdp_session(page.raw)
    registrations = {}
    cdp.on("ServiceWorker.workerRegistrationUpdated",
           lambda e: registrations.update({r["scopeURL"]: r["registrationId"] for r in e.get("registrations", []) if not r.get("isDeleted")}))
    cdp.send("ServiceWorker.enable")
    deadline = time.time() + 10
    while not registrations and time.time() < deadline:
        page.raw.wait_for_timeout(200)
    t.require(registrations, "браузер не сообщил о регистрации service worker")
    scope, registration = next(iter(registrations.items()))
    origin = t.settings.base_url.rstrip("/")
    return page, cdp, origin, registration


def deliver(cdp, origin, registration, payload):
    cdp.send("ServiceWorker.deliverPushMessage", {"origin": origin, "registrationId": registration,
                                                  "data": json.dumps(payload, ensure_ascii=False)})


@case("Открытая вкладка показывает push всплывающим сообщением", layer="ui", matrix={"engine": "engines"}, order=10)
def sw_in_app(t):
    page, cdp, origin, registration = sw_page(t)
    text = "Всплывающее из push %d" % (int(time.time()) % 100000)
    deliver(cdp, origin, registration, {"title": "Автотест", "body": text, "url": "#/notifications", "tag": "autotest"})
    t.check(wait_for(page, "x => [...document.querySelectorAll('#kov-toasts .kov-toast')].some(e => e.innerText.includes(x))", 10, text),
            "push не показан на открытой вкладке")
    page.assert_clean("push на открытой вкладке")


@case("Без открытой вкладки push становится системным уведомлением и ведёт в нужный раздел", layer="ui",
      matrix={"engine": "engines"}, order=11)
def sw_system(t):
    page, cdp, origin, registration = sw_page(t)
    text = "Системное из push %d" % (int(time.time()) % 100000)
    target = "#/notifications"
    page.raw.goto("about:blank")
    deliver(cdp, origin, registration, {"title": "Автотест", "body": text, "url": target, "tag": "autotest-system"})
    page.raw.wait_for_timeout(1500)
    page.raw.goto(t.settings.app_url + "#/tasks")
    page.wait_ready()
    found = page.raw.evaluate("""x => navigator.serviceWorker.ready.then(r => r.getNotifications()).then(ns => ns
        .filter(n => n.body.includes(x)).map(n => ({ title: n.title, body: n.body, tag: n.tag, url: (n.data || {}).url })))""", text)
    t.require(found, "системное уведомление не показано")
    t.check(found[0]["url"] == target, "уведомление ведёт не туда: %s" % found[0]["url"])
    t.check(found[0]["tag"] == "autotest-system", "у уведомления потерялась метка (повторы не схлопнутся)")
    workers = page.context.service_workers
    t.require(workers, "не удалось обратиться к service worker")
    workers[0].evaluate("url => self.clients.matchAll({ type: 'window' }).then(cs => cs.forEach(c => c.postMessage({ type: 'navigate', url })))",
                        target)
    t.check(wait_for(page, "x => location.hash.startsWith(x)", 10, target), "нажатие на уведомление не открыло раздел %s" % target)
    page.assert_clean("системное уведомление")


@case("Приглашение разрешить уведомления: показывается, «Позже» убирает, «Разрешить» включает", layer="ui",
      matrix={"engine": "engines", "device": "devices"}, order=12)
def permission_offer(t):
    page = t.page("worker_to", "light", t.params["device"], path="#/tasks")
    state = page.raw.evaluate("() => typeof Notification === 'undefined' ? 'none' : Notification.permission")
    if state != "default":
        t.skip("в этом браузере уведомления %s — приглашение не показывается" % ("недоступны" if state == "none" else "уже " + state))
    page.raw.evaluate("() => { sessionStorage.setItem('kov.autotest.offer', '1'); localStorage.removeItem('kov.notify.offer'); }")
    page.raw.reload()
    page.wait_ready()
    t.require(wait_for(page, "() => !!document.querySelector('.kov-perm-offer')", 10), "приглашение разрешить уведомления не показано")
    page.raw.click(".kov-perm-offer__later")
    t.check(wait_for(page, "() => !document.querySelector('.kov-perm-offer')", 5), "«Позже» не убрало приглашение")
    page.raw.reload()
    page.wait_ready()
    page.raw.wait_for_timeout(1500)
    t.check(not page.raw.locator(".kov-perm-offer").count(), "после «Позже» приглашение показано снова сразу")
    if page.engine in ("chrome", "chromium"):
        page.context.grant_permissions(["notifications"], origin=t.settings.base_url)
        page.raw.evaluate("() => localStorage.removeItem('kov.notify.offer')")
        page.context.clear_permissions()
        page.raw.reload()
        page.wait_ready()
        if wait_for(page, "() => !!document.querySelector('.kov-perm-offer')", 10):
            page.context.grant_permissions(["notifications"], origin=t.settings.base_url)
            page.raw.click(".kov-perm-offer__allow")
            t.check(wait_for(page, "() => !document.querySelector('.kov-perm-offer')", 5), "«Разрешить» не убрало приглашение")
            t.check(page.raw.evaluate("() => Notification.permission") == "granted", "после «Разрешить» уведомления не разрешены")
    page.forget_network_noise()
    page.assert_clean("приглашение разрешить уведомления")


# ===== настоящие устройства =====

@case("Контрольные уведомления на настоящих устройствах", layer="api", order=20)
def real_devices(t):
    login, password = env("PUSH_DEVICE_LOGIN"), env("PUSH_DEVICE_PASSWORD")
    if not (login and password):
        t.skip("не заданы PUSH_DEVICE_LOGIN и PUSH_DEVICE_PASSWORD — устройства для проверки не назначены")
    api = t.anon()
    api.login(login, password)
    rows = api.call("GET", "push/subscriptions")
    t.require(rows, "у %s нет ни одного устройства с включёнными уведомлениями" % login)
    probe = uuid.uuid4().hex
    api.call("POST", "push/test", json={"probe": probe})
    wait_s = env_int("PUSH_DEVICE_WAIT_S", 60)
    deadline = time.time() + wait_s
    acks = []
    while time.time() < deadline:
        acks = api.call("GET", "push/test/%s" % probe).get("acks") or []
        if len({a.get("subscription_id") for a in acks}) >= len(rows):
            break
        time.sleep(2)
    confirmed = {a.get("subscription_id"): a for a in acks}
    for row in rows:
        name = (row.get("user_agent") or "устройство %s" % row["id"])[:120]
        if row["id"] in confirmed:
            t.log.info("устройство подтвердило за %.1f с: %s" % (confirmed[row["id"]]["delay_ms"] / 1000, name))
        else:
            t.fail("устройство не подтвердило получение за %d с: %s" % (wait_s, name))
