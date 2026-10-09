# -*- coding: utf-8 -*-
"""Проверка уведомлений для приложения Kovapp и того, что Web Push не изменился.

Запуск из корня проекта (папка, где лежат server/ и web_app/):
    python -m server.tests.native_push_tests

Нужны только стандартная библиотека и cryptography. Если FastAPI или
SQLAlchemy не установлены (например, на ПК разработчика), вместо них
подставляются простые заглушки — проверяется логика модуля, а не библиотек.
"""

import asyncio
import base64
import importlib.util
import json
import os
import sys
import threading
import time
import types
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.dirname(HERE)
ROOT = os.path.dirname(SERVER)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  OK   " if cond else "  FAIL ") + name + ("" if cond else "  — " + str(detail)))


# ── заглушки внешних библиотек (только если их нет) ──────────────────────────

def _stub_module(name, **attrs):
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    sys.modules[name] = mod
    return mod


def install_stubs():
    try:
        import fastapi  # noqa: F401
    except Exception:
        class APIRouter:
            def __init__(self, *a, **k): self.routes = []
            def _deco(self, *a, **k):
                def wrap(fn):
                    self.routes.append((a, fn))
                    return fn
                return wrap
            get = post = delete = put = websocket = _deco

        class HTTPException(Exception):
            def __init__(self, status_code=500, detail=""):
                super().__init__(detail)
                self.status_code, self.detail = status_code, detail

        class WebSocketDisconnect(Exception):
            pass

        _stub_module("fastapi", APIRouter=APIRouter, Depends=lambda x=None: None, HTTPException=HTTPException,
                     Request=object, WebSocket=object, WebSocketDisconnect=WebSocketDisconnect)
    try:
        import sqlalchemy.orm  # noqa: F401
    except Exception:
        _stub_module("sqlalchemy")
        _stub_module("sqlalchemy.orm", Session=object)
    # core сервера тянет базу и приложение — для проверки роутера хватает двух функций
    if "server.server_app.core" not in sys.modules:
        _stub_module("server.server_app.core", get_current_user_id=lambda: 1, get_db=lambda: None)


# ── 1. шифрование и векторы для Java ─────────────────────────────────────────

def test_crypto(np):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = np.new_payload_key()
    payload = {"id": "a1", "uid": 7, "title": "Иван", "body": "Привет", "url": "#/messenger/12", "tag": "chat-12"}
    enc = np.encrypt_payload(key, payload)
    pad = lambda s: s + "=" * (-len(s) % 4)
    nonce = base64.urlsafe_b64decode(pad(enc["n"]))
    plain = AESGCM(base64.b64decode(key)).decrypt(nonce, base64.urlsafe_b64decode(pad(enc["c"])), None)
    check("шифрование: расшифровывается тем же ключом", json.loads(plain.decode()) == payload)
    check("шифрование: nonce 12 байт, формат v=1", len(nonce) == 12 and enc["v"] == "1")
    check("шифрование: каждый раз новый nonce", np.encrypt_payload(key, payload)["n"] != enc["n"])
    import datetime as _dt
    now = _dt.datetime(2026, 9, 25, 12, 0)
    cut = np.binding_cutoff(now)
    check("привязка телефона: не дольше срока входа (7 суток) + сутки", (now - cut).days == 8 and np.BINDING_DAYS == 8)
    secret = np.new_secret()
    check("секрет: хранится только хеш и сверяется", np.secret_matches(np.hash_secret(secret), secret)
          and not np.secret_matches(np.hash_secret(secret), secret + "x"))
    check("тема (collapse_key) как у Web Push", np.topic("chat-12") == "chat-12" and np.topic("a b/c") == "abc"
          and np.topic("") == "kovcheg")


def write_java_vectors(np, out):
    """Векторы для Java-теста приложения — зашифрованы кодом сервера."""
    key = base64.b64encode(bytes(range(32))).decode()
    vectors = []
    for payload in [
        {"id": "n1", "uid": 7, "title": "Иван Петров", "body": "Привет! Как дела?", "url": "#/messenger/12", "tag": "chat-12", "kind": "chat", "chat_id": 12},
        {"id": "n2", "uid": 7, "title": "Уведомление", "body": "x" * 300, "url": "javascript:alert(1)", "tag": "notification-5", "kind": "notification"},
        {"id": "n3", "uid": 9, "title": "Проверка", "body": "Push-уведомления работают", "url": "#/notifications", "tag": "push-test", "probe": "0123456789abcdef0123456789abcdef"},
    ]:
        vectors.append({"data": np.encrypt_payload(key, payload), "payload": payload})
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"key": key, "vectors": vectors}, f, ensure_ascii=False)


# ── 2. FCM против локального «Google» ────────────────────────────────────────

class FakeGoogle(BaseHTTPRequestHandler):
    state = {}

    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        st = FakeGoogle.state
        if self.path == "/token":
            form = urllib.parse.parse_qs(raw.decode())
            st.setdefault("assertions", []).append(form["assertion"][0])
            st["tokens_issued"] = st.get("tokens_issued", 0) + 1
            return self._json(200, {"access_token": "tok%d" % st["tokens_issued"], "expires_in": 3600})
        if self.path.startswith("/v1/projects/demo/messages:send"):
            auth = self.headers.get("Authorization", "")
            msg = json.loads(raw.decode())
            st.setdefault("messages", []).append((auth, msg))
            if st.pop("reject_once", False):
                return self._json(401, {"error": {"status": "UNAUTHENTICATED"}})
            token = msg["message"]["token"]
            if token == "dead-token":
                return self._json(404, {"error": {"status": "NOT_FOUND", "details": [{"errorCode": "UNREGISTERED"}]}})
            if token == "flaky-token":
                return self._json(503, {"error": {"status": "UNAVAILABLE"}})
            return self._json(200, {"name": "projects/demo/messages/1"})
        self._json(404, {})


def test_fcm(np):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    srv = HTTPServer(("127.0.0.1", 0), FakeGoogle)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % srv.server_address[1]
    info = {"client_email": "push@demo.iam.gserviceaccount.com", "private_key": pem, "project_id": "demo",
            "private_key_id": "k1", "token_uri": base + "/token"}
    sender = np.FcmSender(endpoint_base=base, info=info)
    check("FCM: ключ сервисного аккаунта загружен", sender.available, sender.error)
    FakeGoogle.state.clear()
    res = sender.send("good-token", {"v": "1", "n": "x", "c": "y"}, 3600, "chat-12")
    check("FCM: отправка успешна", res == "ok", res)
    st = FakeGoogle.state
    assertion = st["assertions"][0]
    head, claims, sig = assertion.split(".")
    pad = lambda s: s + "=" * (-len(s) % 4)
    claims = json.loads(base64.urlsafe_b64decode(pad(claims)))
    try:
        key.public_key().verify(base64.urlsafe_b64decode(pad(sig)), (head + "." + assertion.split(".")[1]).encode(),
                                padding.PKCS1v15(), hashes.SHA256())
        signed = True
    except Exception:
        signed = False
    check("FCM: запрос токена подписан ключом аккаунта (RS256)", signed)
    check("FCM: права только на отправку уведомлений", claims["scope"] == np.FcmSender.SCOPE
          and claims["aud"] == info["token_uri"] and claims["exp"] - claims["iat"] == 3600)
    auth, msg = st["messages"][0]
    m = msg["message"]
    check("FCM: сообщение только с данными, высокий приоритет, TTL и тема",
          auth == "Bearer tok1" and "notification" not in m and m["android"] == {"priority": "HIGH", "ttl": "3600s", "collapse_key": "chat-12"}
          and m["data"] == {"v": "1", "n": "x", "c": "y"}, msg)
    sender.send("good-token", {"v": "1", "n": "x", "c": "y"}, 60, "t")
    check("FCM: токен доступа переиспользуется", st.get("tokens_issued") == 1)
    st["reject_once"] = True
    res = sender.send("good-token", {"v": "1", "n": "x", "c": "y"}, 60, "t")
    check("FCM: на 401 берётся новый токен и запрос повторяется", res == "ok" and st.get("tokens_issued") == 2, (res, st.get("tokens_issued")))
    check("FCM: удалённое приложение распознаётся", sender.send("dead-token", {}, 60, "t") == "dead")
    check("FCM: временный сбой не удаляет устройство", sender.send("flaky-token", {}, 60, "t") == "error")
    srv.shutdown()
    return sender


# ── 3. очередь собственного канала ───────────────────────────────────────────

class FakeSocket:
    """Имитация WebSocket FastAPI: входящие сообщения из очереди, исходящие — в список."""

    def __init__(self, incoming=()):
        self.inbox = asyncio.Queue()
        for m in incoming:
            self.inbox.put_nowait(m)
        self.sent = []
        self.closed = None
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def receive_text(self):
        item = await self.inbox.get()
        if item is None:
            from fastapi import WebSocketDisconnect
            raise WebSocketDisconnect()
        return item

    async def send_text(self, text):
        if self.closed is not None:
            raise RuntimeError("closed")
        self.sent.append(json.loads(text))

    async def close(self, code=1000):
        self.closed = code

    def types(self):
        return [m["type"] for m in self.sent]


async def test_hub(np):
    hub = np.DirectHub(queue_limit=3)
    for i in range(5):
        await hub.deliver("d1", "m%d" % i, {"n": i}, 3600)
    check("очередь: пока телефон офлайн, хранится не больше лимита (старые вытесняются)",
          list(hub.queues["d1"].keys()) == ["m2", "m3", "m4"])
    ws = FakeSocket()
    conn, old = hub.attach("d1", ws)
    sent = await hub.flush("d1", conn)
    check("очередь: при подключении досылается по порядку, затем flushed",
          sent == 3 and [m.get("id") for m in ws.sent] == ["m2", "m3", "m4", None] and ws.sent[-1]["type"] == "flushed")
    hub.ack("d1", "m2")
    hub.ack("d1", "m3")
    check("очередь: подтверждённое удаляется, неподтверждённое остаётся", list(hub.queues["d1"].keys()) == ["m4"])
    await hub.deliver("d1", "m5", {"n": 5}, 3600)
    check("очередь: на связи — отправляется сразу", ws.sent[-1] == {"type": "push", "id": "m5", "payload": {"n": 5}})
    hub.queues["d1"]["old"] = ({"n": -1}, time.time() - 1)
    hub._prune("d1")
    check("очередь: просроченное (TTL) выбрасывается", "old" not in hub.queues["d1"])
    ws2 = FakeSocket()
    conn2, old2 = hub.attach("d1", ws2)
    check("очередь: второе подключение того же телефона заменяет первое", old2 is conn and hub.conns["d1"] is conn2)
    hub.detach("d1", conn)
    check("очередь: отключение старого не трогает новое", hub.conns.get("d1") is conn2)
    await hub.forget("d1")
    check("очередь: отвязка стирает очередь и закрывает связь", "d1" not in hub.queues and ws2.closed == 1008)


async def test_socket(np, router):
    np.PING_SECONDS = 1            # чтобы проверить ping за секунду
    hub = np.DirectHub()
    devices = {"a" * 32: ("s3cret", 7)}

    def auth(device_id, secret):
        d = devices.get(device_id)
        return {"device_id": device_id, "user_id": d[1], "provider": "direct"} if d and d[0] == secret else None

    ws = FakeSocket([json.dumps({"type": "hello", "device_id": "a" * 32, "secret": "wrong"})])
    await router.serve_socket(ws, hub, auth)
    check("сокет: чужой секрет — error unauthorized и закрытие", ws.types() == ["error"] and ws.sent[0]["reason"] == "unauthorized" and ws.closed == 1008)

    ws = FakeSocket([json.dumps({"type": "subscribe"})])
    await router.serve_socket(ws, hub, auth)
    check("сокет: без hello соединение закрывается", ws.closed == 1008 and not ws.sent)

    await hub.deliver("a" * 32, "q1", {"title": "в очереди"}, 3600)
    ws = FakeSocket([json.dumps({"type": "hello", "device_id": "a" * 32, "secret": "s3cret"})])
    task = asyncio.ensure_future(router.serve_socket(ws, hub, auth))
    await asyncio.sleep(0.05)
    check("сокет: welcome, накопленное и flushed", ws.types()[:3] == ["welcome", "push", "flushed"] and ws.sent[1]["id"] == "q1", ws.sent)
    await hub.deliver("a" * 32, "live", {"title": "сразу"}, 3600)
    await asyncio.sleep(0.05)
    check("сокет: новое уведомление приходит сразу", ws.sent[-1]["id"] == "live")
    ws.inbox.put_nowait(json.dumps({"type": "ack", "id": "q1"}))
    ws.inbox.put_nowait(json.dumps({"type": "ack", "id": "live"}))
    await asyncio.sleep(0.05)
    check("сокет: подтверждения очищают очередь", not hub.queues["a" * 32])
    await asyncio.sleep(1.2)
    check("сокет: без входящих сервер шлёт ping", "ping" in ws.types())
    ws.inbox.put_nowait(None)          # телефон отключился
    await asyncio.wait_for(task, 2)
    check("сокет: после отключения телефон не числится на связи", not hub.connected("a" * 32))


async def test_notify(np, sender):
    devices = [
        {"device_id": "b" * 32, "user_id": 7, "provider": "direct", "fcm_token": None, "payload_key": np.new_payload_key()},
        {"device_id": "c" * 32, "user_id": 9, "provider": "fcm", "fcm_token": "good-token", "payload_key": np.new_payload_key()},
    ]

    class Recorder:
        def __init__(self): self.calls = []
        def send(self, token, data, ttl, collapse):
            self.calls.append((token, data, ttl, collapse))
            return "ok"

    rec = Recorder()
    hub = np.HUB = np.DirectHub()
    payload = {"title": "Уведомление", "body": "Задача назначена", "url": "#/tasks/5", "tag": "notification-5", "ts": "t"}
    res = await np.notify([7, 9, 9, None], payload, 86400, loader=lambda ids: [d for d in devices if d["user_id"] in ids], sender=rec)
    check("рассылка: каждому телефону своим путём", res == {"direct": 1, "fcm": 1, "dead": 0}, res)
    queued = list(hub.queues["b" * 32].values())[0][0]
    check("рассылка: копия с id и uid, остальное как у Web Push",
          queued["uid"] == 7 and len(queued["id"]) == 32 and {k: queued[k] for k in payload} == payload)
    token, data, ttl, collapse = rec.calls[0]
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    pad = lambda s: s + "=" * (-len(s) % 4)
    plain = json.loads(AESGCM(base64.b64decode(devices[1]["payload_key"])).decrypt(
        base64.urlsafe_b64decode(pad(data["n"])), base64.urlsafe_b64decode(pad(data["c"])), None))
    check("рассылка: через Google уходит только шифртекст, внутри — uid получателя",
          set(data) == {"v", "n", "c"} and plain["uid"] == 9 and plain["title"] == "Уведомление")
    check("рассылка: TTL как у Web Push; склейка по хешу метки — номер задачи Google не видит",
          ttl == 86400 and collapse == np.private_topic(devices[1]["payload_key"], "notification-5")
          and "notification" not in collapse and len(collapse) <= 32)
    other = np.new_payload_key()
    check("рассылка: у разных телефонов разный ключ склейки для одной метки",
          np.private_topic(other, "chat-12") != np.private_topic(devices[1]["payload_key"], "chat-12"))
    check("рассылка: исходный payload не изменён (Web Push получает прежний)", "id" not in payload and "uid" not in payload)


# ── 3а. приоритет Google и работа в нескольких процессах (KOVAPP_ROLE=split) ─────

async def test_priority_and_split(np):
    fcm_dev = {"device_id": "d" * 32, "user_id": 5, "provider": "fcm", "fcm_token": "tok", "payload_key": np.new_payload_key()}
    direct_dev = {"device_id": "e" * 32, "user_id": 6, "provider": "direct", "fcm_token": None, "payload_key": np.new_payload_key()}
    loader = lambda ids: [d for d in (fcm_dev, direct_dev) if d["user_id"] in ids]

    class Rec:
        def __init__(self): self.calls = []
        def send(self, token, data, ttl, collapse): self.calls.append(token); return "ok"

    payload = {"title": "Проверка", "body": "b", "url": "#/", "tag": "t"}
    np.HUB = np.DirectHub()
    rec = Rec()
    res = await np.notify([5], payload, 60, loader=loader, sender=rec)
    check("приоритет: телефон с Google и сервер с Google — доставка через Google", res["fcm"] == 1 and res["direct"] == 0
          and rec.calls == ["tok"] and not np.HUB.queues)
    res = await np.notify([5], payload, 60, loader=loader, sender=None)
    check("запасной путь: Google на сервере не настроен — тот же телефон получает по собственному каналу",
          res["direct"] == 1 and res["fcm"] == 0 and fcm_dev["device_id"] in np.HUB.queues)

    class Remote:
        def __init__(self): self.sent, self.forgot = [], []
        async def native_deliver(self, device_id, msg_id, body, ttl): self.sent.append((device_id, body["uid"], ttl))
        async def native_forget(self, device_id): self.forgot.append(device_id)

    remote, saved = Remote(), np._remote
    np.HUB = np.DirectHub()
    np._remote = lambda: remote
    try:
        res = await np.notify([5, 6], payload, 90, loader=loader, sender=Rec())
        check("несколько процессов: собственный канал уходит процессу переписки, Google — как обычно",
              res == {"direct": 1, "fcm": 1, "dead": 0} and remote.sent == [(direct_dev["device_id"], 6, 90)]
              and not np.HUB.queues)
        await np.forget_device("e" * 32)
        check("несколько процессов: «забыть телефон» тоже передаётся процессу переписки", remote.forgot == ["e" * 32])
    finally:
        np._remote = saved
    np.HUB = np.DirectHub()
    await np.HUB.deliver("f" * 32, "m1", {"x": 1}, 60)
    await np.forget_device("f" * 32)
    check("один процесс (Windows, KOVAPP_ROLE=all): очередь в этом же процессе, «забыть» её очищает",
          "f" * 32 not in np.HUB.queues)


# ── 4. Web Push не изменился ─────────────────────────────────────────────────

def load_push(path, name, config):
    """push.py с заглушками базы и pywebpush; возвращает модуль и список вызовов webpush."""
    calls = []

    class Col:
        def __init__(self, name): self.name = name
        def in_(self, values): return ("in", self.name, set(values))
        def __eq__(self, other): return ("eq", self.name, other)
        __hash__ = object.__hash__

    class PushSubscription:
        id = Col("id"); user_id = Col("user_id"); endpoint_hash = Col("endpoint_hash")
        def __init__(self, **kw): self.__dict__.update(kw)

    rows = [PushSubscription(id=1, user_id=7, endpoint="https://push.example/a", p256dh="p1", auth="a1"),
            PushSubscription(id=2, user_id=9, endpoint="https://push.example/gone", p256dh="p2", auth="a2"),
            PushSubscription(id=3, user_id=11, endpoint="https://push.example/c", p256dh="p3", auth="a3")]
    deleted = []

    class Query:
        def __init__(self): self.preds = []
        def filter(self, *preds): self.preds += preds; return self
        def _match(self, r):
            for kind, col, val in self.preds:
                v = getattr(r, col)
                if kind == "in" and v not in val: return False
                if kind == "eq" and v != val: return False
            return True
        def all(self): return [r for r in rows if self._match(r)]
        def first(self): a = self.all(); return a[0] if a else None
        def delete(self, synchronize_session=False):
            gone = self.all(); deleted.extend(r.id for r in gone); return len(gone)

    class Session:
        def query(self, model): return Query()
        def commit(self): pass
        def rollback(self): pass
        def close(self): pass
        def add(self, r): pass
        def refresh(self, r): pass

    class WebPushException(Exception):
        def __init__(self, msg, response=None):
            super().__init__(msg); self.response = response

    def webpush(**kw):
        calls.append(kw)
        if kw["subscription_info"]["endpoint"].endswith("/gone"):
            raise WebPushException("gone", response=types.SimpleNamespace(status_code=410))

    _stub_module("pywebpush", webpush=webpush, WebPushException=WebPushException)
    _stub_module("server.database.init_database", SessionLocal=Session)
    _stub_module("server.database.models", PushSubscription=PushSubscription)
    _stub_module("server.server_app.config", **config)
    spec = importlib.util.spec_from_file_location("server.server_app." + name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)

    class FixedDateTime:
        @staticmethod
        def now():
            import datetime as _d
            return _d.datetime(2026, 9, 24, 12, 0, 0)
    mod.datetime = FixedDateTime
    return mod, calls, deleted


async def run_push_scenarios(mod):
    await mod.push_chat_message(12, 5, "Иван Петров", "Монтаж", True, [5, 7, 9, 11], "Привет всем! " * 20, 0)
    await mod.push_chat_message(13, 7, "Анна", None, False, [7, 11], "", 3)
    await mod.push_added_to_group(14, "Склад", "Анна", [9, 11])
    await mod.push_notification(11, "Задача завершена", "task_view", 55)
    await mod.notify_users((u for u in [7, 11]), "Проверка", "Push-уведомления работают",
                           url="#/notifications", tag="push-test", extra={"probe": "ab" * 16})
    await asyncio.sleep(0.05)


async def test_webpush_regression():
    original = os.path.join("/home/claude/src/kovcheg/server/server_app/push.py")
    if not os.path.isfile(original):
        original = os.environ.get("KOVCHEG_ORIGINAL_PUSH", "")
    current = os.path.join(SERVER, "server_app", "push.py")
    config = dict(PUSH_ENABLED=True, VAPID_PRIVATE_KEY="key.pem", VAPID_SUBJECT="mailto:a@b.c", VAPID_PUBLIC_KEY="pub")
    if not original or not os.path.isfile(original):
        check("Web Push: исходный push.py для сравнения не найден (KOVCHEG_ORIGINAL_PUSH)", False)
        return
    old_mod, old_calls, old_deleted = load_push(original, "push_before", config)
    await run_push_scenarios(old_mod)

    native = []

    async def fake_notify(user_ids, payload, ttl):
        native.append((list(user_ids), payload, ttl))
    _stub_module("server.server_app.native_push", native_push_available=lambda: True, notify=fake_notify)
    new_mod, new_calls, new_deleted = load_push(current, "push_after", config)
    await run_push_scenarios(new_mod)
    check("Web Push: те же вызовы с теми же данными, заголовками и TTL", old_calls == new_calls and len(new_calls) > 0,
          (len(old_calls), len(new_calls)))
    check("Web Push: мёртвые подписки удаляются так же", old_deleted == new_deleted == [2, 2], (old_deleted, new_deleted))
    web_payloads = [json.loads(c["data"]) for c in new_calls]
    same = all(any(p == wp for wp in web_payloads) for _, p, _ in native)
    check("приложение получает те же данные, что и Web Push (включая probe)", len(native) == 5 and same, native[:1])
    check("приложение: генератор получателей не «съеден» до Web Push",
          native[-1][0] == [7, 11] and any(c["subscription_info"]["endpoint"].endswith("/c") and json.loads(c["data"]).get("probe") for c in new_calls))

    # Web Push выключен (нет ключей): вызовов webpush нет, приложение уведомления получает
    config_off = dict(config, PUSH_ENABLED=False)
    off_mod, off_calls, _ = load_push(current, "push_off", config_off)
    native.clear()
    await run_push_scenarios(off_mod)
    check("без VAPID: браузерам ничего, приложению — всё", not off_calls and len(native) == 5)
    check("any_push_available: работает и без VAPID", off_mod.any_push_available() is True and off_mod.push_available() is False)


def main():
    install_stubs()
    from server.server_app import native_push as np
    from server.server_app.routers import native_push as router
    print("Уведомления приложения Kovapp — серверная часть")
    test_crypto(np)
    out = os.environ.get("KOVAPP_VECTORS")
    if out:
        write_java_vectors(np, out)
        print("  векторы для Java записаны:", out)
    sender = test_fcm(np)
    asyncio.run(test_hub(np))
    asyncio.run(test_socket(np, router))
    asyncio.run(test_notify(np, sender))
    asyncio.run(test_priority_and_split(np))
    asyncio.run(test_webpush_regression())
    failed = [r for r in RESULTS if not r[1]]
    print("Итого: %d проверок, ошибок: %d" % (len(RESULTS), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
