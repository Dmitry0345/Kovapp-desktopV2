# -*- coding: utf-8 -*-
"""Уведомления для приложения Kovapp (Android) — без браузера.

Зачем отдельно от Web Push: внутри приложения нет браузерного push-сервиса,
поэтому сервер доставляет уведомления сам, двумя путями:

  fcm    — через Firebase Cloud Messaging (телефоны с сервисами Google).
           Текст шифруется ключом устройства (AES-256-GCM): Google видит
           только зашифрованные байты;
  direct — собственный канал: приложение держит WebSocket /ws/native-push,
           сервер шлёт по нему. Для Huawei, Honor и любых телефонов без Google.
           Пока телефон не на связи, уведомления ждут в очереди (в памяти
           процесса; сервер работает одним процессом, как и чат).

Модуль не требует новых библиотек: шифрование — cryptography (уже стоит
вместе с pywebpush), запросы к Google — стандартный urllib.
Настройки (.env):
  NATIVE_PUSH_ENABLED=1            — включить (по умолчанию включено)
  FCM_SERVICE_ACCOUNT_FILE=путь    — ключ сервисного аккаунта Firebase (JSON);
                                     пусто — только собственный канал
  NATIVE_PUSH_PING_SECONDS=240     — как часто проверять связь с телефоном
  NATIVE_PUSH_QUEUE=100            — сколько уведомлений хранить на телефон
  NATIVE_PUSH_MAX_DEVICES=10       — телефонов на одного сотрудника
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

from .envtools import env_bool, env_int, env_str

logger = logging.getLogger(__name__)

NATIVE_PUSH_ENABLED = env_bool("NATIVE_PUSH_ENABLED", True)
FCM_SERVICE_ACCOUNT_FILE = env_str("FCM_SERVICE_ACCOUNT_FILE", "")
PING_SECONDS = max(30, min(900, env_int("NATIVE_PUSH_PING_SECONDS", 240)))
QUEUE_LIMIT = max(10, env_int("NATIVE_PUSH_QUEUE", 100))
MAX_DEVICES_PER_USER = max(1, env_int("NATIVE_PUSH_MAX_DEVICES", 10))
# Привязка телефона действует не дольше срока входа (+ сутки): приложение
# перерегистрируется при каждом запуске с действующим входом, а потерянный
# телефон перестаёт получать уведомления сам, даже если его не отвязали
BINDING_DAYS = env_int("ACCESS_TOKEN_EXPIRE_MINUTES", 10080) / 1440.0 + 1
PROTOCOL_VERSION = 1
WS_PATH = "ws/native-push"

DEVICE_ID_RE = re.compile(r"[0-9a-f]{32}")

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="nativepush")


def native_push_available() -> bool:
    return NATIVE_PUSH_ENABLED


# ── мелкие помощники ─────────────────────────────────────────────────────────

def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def new_secret() -> str:
    """Секрет устройства: 256 случайных бит. Сервер хранит только его хеш."""
    return secrets.token_urlsafe(32)


def hash_secret(secret: str) -> str:
    return hashlib.sha256((secret or "").encode("utf-8")).hexdigest()


def secret_matches(stored_hash: str, secret: str) -> bool:
    return bool(stored_hash) and hmac.compare_digest(stored_hash, hash_secret(secret))


def new_payload_key() -> str:
    return base64.b64encode(os.urandom(32)).decode("ascii")


def encrypt_payload(key_b64: str, payload: dict) -> Dict[str, str]:
    """AES-256-GCM, случайный nonce 12 байт: {"v":"1","n":nonce,"c":шифртекст+тег}."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = base64.b64decode(key_b64)
    if len(key) != 32:
        raise ValueError("ключ устройства должен быть 32 байта")
    nonce = os.urandom(12)
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return {"v": "1", "n": b64url(nonce), "c": b64url(AESGCM(key).encrypt(nonce, data, None))}


def topic(tag: str) -> str:
    """Как Topic у Web Push: пока телефон офлайн, Google хранит только последнее
    уведомление той же темы (например, одного чата)."""
    return re.sub(r"[^A-Za-z0-9_-]", "", tag or "kovcheg")[:32] or "kovcheg"


def private_topic(key_b64: str, tag: str) -> str:
    """Ключ склейки для Google: хеш метки с ключом телефона. Google по-прежнему
    склеивает уведомления одного чата, но номер чата («chat-12») не видит."""
    digest = hmac.new(base64.b64decode(key_b64), topic(tag).encode("utf-8"), hashlib.sha256).hexdigest()
    return "k" + digest[:23]


def binding_cutoff(now: Optional[datetime] = None) -> datetime:
    """Регистрации старше этой даты считаются недействительными."""
    from datetime import timedelta
    return (now or datetime.now()) - timedelta(days=BINDING_DAYS)


def clip(value, limit: int) -> Optional[str]:
    text = str(value or "").strip()
    return text[:limit] if text else None


# ── Firebase Cloud Messaging (HTTP v1) ───────────────────────────────────────

class FcmSender:
    """Отправка через FCM HTTP v1 с ключом сервисного аккаунта.

    Токен доступа Google получается по подписанному ключом аккаунта запросу
    (JWT RS256) и живёт час; обновляется заранее, за минуту до истечения.
    """

    SCOPE = "https://www.googleapis.com/auth/firebase.messaging"

    def __init__(self, path: str = "", endpoint_base: str = "https://fcm.googleapis.com", info: Optional[dict] = None):
        self.path = path
        self.endpoint_base = endpoint_base.rstrip("/")
        self.info = None
        self.error = ""
        self._key = None
        self._token = ""
        self._expires = 0.0
        self._lock = threading.Lock()
        self._load(info)

    def _load(self, info: Optional[dict]) -> None:
        try:
            if info is None:
                if not self.path:
                    self.error = "не задан FCM_SERVICE_ACCOUNT_FILE"
                    return
                with open(self.path, encoding="utf-8") as f:
                    info = json.load(f)
            for key in ("client_email", "private_key", "project_id"):
                if not info.get(key):
                    raise ValueError(f"в ключе нет поля {key}")
            from cryptography.hazmat.primitives import serialization
            self._key = serialization.load_pem_private_key(info["private_key"].encode("utf-8"), password=None)
            info.setdefault("token_uri", "https://oauth2.googleapis.com/token")
            self.info = info
        except Exception as e:
            self.error = str(e)
            logger.warning("FCM: ключ сервисного аккаунта не загружен: %s", e)

    @property
    def available(self) -> bool:
        return self.info is not None

    def assertion(self, now: Optional[int] = None) -> str:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        now = int(now if now is not None else time.time())
        header = {"alg": "RS256", "typ": "JWT"}
        if self.info.get("private_key_id"):
            header["kid"] = self.info["private_key_id"]
        claims = {"iss": self.info["client_email"], "scope": self.SCOPE, "aud": self.info["token_uri"],
                  "iat": now, "exp": now + 3600}
        signing_input = (b64url(json.dumps(header, separators=(",", ":")).encode()) + "."
                         + b64url(json.dumps(claims, separators=(",", ":")).encode()))
        signature = self._key.sign(signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
        return signing_input + "." + b64url(signature)

    def access_token(self, force: bool = False) -> str:
        with self._lock:
            if not force and self._token and time.time() < self._expires - 60:
                return self._token
            body = urllib.parse.urlencode({
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": self.assertion(),
            }).encode("ascii")
            req = urllib.request.Request(self.info["token_uri"], data=body, method="POST",
                                         headers={"Content-Type": "application/x-www-form-urlencoded"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            self._token = data["access_token"]
            self._expires = time.time() + int(data.get("expires_in", 3600))
            return self._token

    def message(self, token: str, data: Dict[str, str], ttl: int, collapse: str) -> dict:
        # только data: уведомление показывает само приложение, расшифровав текст
        return {"message": {"token": token, "data": data,
                            "android": {"priority": "HIGH", "ttl": f"{int(ttl)}s", "collapse_key": collapse}}}

    def send(self, token: str, data: Dict[str, str], ttl: int, collapse: str) -> str:
        """'ok' | 'dead' (токен больше недействителен) | 'error' (временный сбой)."""
        url = f"{self.endpoint_base}/v1/projects/{self.info['project_id']}/messages:send"
        body = json.dumps(self.message(token, data, ttl, collapse)).encode("utf-8")
        pauses = [1, 3, 6]                       # сбой связи (DNS, таймаут, обрыв) — до трёх повторов
        attempt = 0
        while True:
            attempt += 1
            try:
                req = urllib.request.Request(url, data=body, method="POST", headers={
                    "Authorization": "Bearer " + self.access_token(force=attempt == 2),
                    "Content-Type": "application/json; charset=utf-8"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    resp.read()
                return "ok"
            except urllib.error.HTTPError as e:
                text = ""
                try:
                    text = e.read().decode("utf-8", "replace")
                except Exception:
                    pass
                if e.code == 401 and attempt == 1:
                    continue                    # токен доступа устарел — берём новый
                if e.code in (500, 502, 503, 504) and pauses:
                    time.sleep(pauses.pop(0))   # временный сбой у Google — повторяем
                    continue
                if e.code == 404 or "UNREGISTERED" in text or (e.code == 400 and "registration token" in text.lower()):
                    return "dead"
                logger.warning("FCM: код %s: %s", e.code, text[:300])
                return "error"
            except (urllib.error.URLError, OSError) as e:
                if pauses:
                    pause = pauses.pop(0)
                    logger.info("FCM: нет связи (%s), повтор через %d с", e, pause)
                    time.sleep(pause)
                    continue
                logger.warning("FCM: сбой отправки после повторов: %s", e)
                return "error"
            except Exception as e:
                logger.warning("FCM: сбой отправки: %s", e)
                return "error"


_fcm: Optional[FcmSender] = None
_fcm_lock = threading.Lock()


def fcm_sender() -> Optional[FcmSender]:
    """FCM настроен и ключ загружен — отправитель, иначе None."""
    global _fcm
    with _fcm_lock:
        if _fcm is None:
            _fcm = FcmSender(FCM_SERVICE_ACCOUNT_FILE) if FCM_SERVICE_ACCOUNT_FILE else FcmSender("")
    return _fcm if _fcm.available else None


# ── собственный канал: соединения и очереди ──────────────────────────────────

class DirectHub:
    """Телефоны, подключённые к /ws/native-push, и очереди для тех, кто не на связи.

    Уведомление остаётся в очереди, пока телефон не подтвердит его (ack):
    при обрыве оно придёт повторно, а телефон отбросит дубль по id.
    """

    class Conn:
        def __init__(self, websocket):
            self.websocket = websocket
            self.lock = asyncio.Lock()
            self.since = time.time()

    def __init__(self, queue_limit: int = QUEUE_LIMIT):
        self.queue_limit = queue_limit
        self.queues: Dict[str, "OrderedDict[str, Tuple[dict, float]]"] = {}
        self.conns: Dict[str, "DirectHub.Conn"] = {}

    def attach(self, device_id: str, websocket) -> Tuple["DirectHub.Conn", Optional["DirectHub.Conn"]]:
        conn = DirectHub.Conn(websocket)
        old = self.conns.get(device_id)
        self.conns[device_id] = conn
        return conn, old

    def detach(self, device_id: str, conn: "DirectHub.Conn") -> None:
        if self.conns.get(device_id) is conn:
            del self.conns[device_id]

    def connected(self, device_id: str) -> bool:
        return device_id in self.conns

    def _prune(self, device_id: str) -> "OrderedDict[str, Tuple[dict, float]]":
        q = self.queues.setdefault(device_id, OrderedDict())
        now = time.time()
        for msg_id in [k for k, (_, exp) in q.items() if exp < now]:
            del q[msg_id]
        while len(q) > self.queue_limit:
            q.popitem(last=False)
        return q

    async def _send(self, conn: "DirectHub.Conn", obj: dict) -> bool:
        try:
            async with conn.lock:
                await conn.websocket.send_text(json.dumps(obj, ensure_ascii=False))
            return True
        except Exception as e:
            logger.debug("Канал приложения: отправка не удалась: %s", e)
            return False

    async def deliver(self, device_id: str, msg_id: str, payload: dict, ttl: int) -> bool:
        q = self._prune(device_id)
        q[msg_id] = (payload, time.time() + max(60, int(ttl)))
        self._prune(device_id)
        conn = self.conns.get(device_id)
        if conn is None:
            return False
        return await self._send(conn, {"type": "push", "id": msg_id, "payload": payload})

    async def flush(self, device_id: str, conn: "DirectHub.Conn") -> int:
        q = self._prune(device_id)
        sent = 0
        for msg_id, (payload, _) in list(q.items()):
            if not await self._send(conn, {"type": "push", "id": msg_id, "payload": payload}):
                return sent
            sent += 1
        await self._send(conn, {"type": "flushed"})
        return sent

    def ack(self, device_id: str, msg_id: str) -> bool:
        q = self.queues.get(device_id)
        return bool(q is not None and q.pop(str(msg_id), None) is not None)

    async def forget(self, device_id: str) -> None:
        """Устройство отвязано: очередь прежнего пользователя стирается, связь закрывается."""
        self.queues.pop(device_id, None)
        conn = self.conns.pop(device_id, None)
        if conn is not None:
            try:
                await conn.websocket.close(code=1008)
            except Exception:
                pass

    def stats(self) -> dict:
        return {"connected": len(self.conns), "queued": sum(len(q) for q in self.queues.values())}


HUB = DirectHub()


def _remote():
    """Процесс без сокетов телефонов (KOVAPP_ROLE=split: api, reader, uploader) — менеджер переписки,
    который передаёт доставку процессу переписки; там висят /ws/native-push. Иначе — None."""
    try:
        from .realtime import ROLE, manager
    except Exception:
        return None
    return manager if ROLE in ("api", "reader", "uploader") else None


async def deliver_direct(device_id: str, msg_id: str, payload: dict, ttl: int) -> bool:
    """Собственный канал: в одном процессе — сразу в очередь, при разделении на процессы — процессу переписки."""
    remote = _remote()
    if remote is not None:
        await remote.native_deliver(device_id, msg_id, payload, ttl)
        return True
    return await HUB.deliver(device_id, msg_id, payload, ttl)


async def forget_device(device_id: str) -> None:
    remote = _remote()
    if remote is not None:
        await remote.native_forget(device_id)
    else:
        await HUB.forget(device_id)


# ── база данных ──────────────────────────────────────────────────────────────

def _model():
    from server.database.models import NativePushDevice
    return NativePushDevice


def _session():
    from server.database.init_database import SessionLocal
    return SessionLocal()


def register_device(db, user_id: int, device_id: str, provider: str, fcm_token: Optional[str],
                    model: Optional[str], app_version: Optional[str]) -> Tuple[str, str, Optional[int]]:
    """Привязывает телефон к сотруднику (перепривязывает, если он был чужим).
    Каждый раз выдаются новые секрет и ключ. Возвращает (секрет, ключ, прежний_владелец)."""
    Device = _model()
    secret, key = new_secret(), new_payload_key()
    row = db.query(Device).filter(Device.device_id == device_id).first()
    previous = row.user_id if row is not None else None
    now = datetime.now()
    if row is None:
        row = Device(device_id=device_id, user_id=user_id, created_at=now)
        db.add(row)
    row.user_id = user_id
    row.provider = provider
    row.secret_hash = hash_secret(secret)
    row.payload_key = key
    row.fcm_token = fcm_token if provider == "fcm" else None
    row.model = model
    row.app_version = app_version
    row.registered_at = now
    row.last_seen_at = now
    db.flush()
    # у одного сотрудника не больше MAX_DEVICES_PER_USER телефонов: старые забываются
    rows = (db.query(Device).filter(Device.user_id == user_id, Device.device_id != device_id)
            .order_by(Device.last_seen_at.desc()).all())
    for extra in rows[MAX_DEVICES_PER_USER - 1:]:
        db.delete(extra)
    db.commit()
    return secret, key, previous


def unregister_device(db, user_id: int, device_id: str) -> bool:
    Device = _model()
    deleted = db.query(Device).filter(Device.device_id == device_id, Device.user_id == user_id).delete(synchronize_session=False)
    db.commit()
    return bool(deleted)


def device_by_secret(db, device_id: str, secret: str):
    Device = _model()
    row = db.query(Device).filter(Device.device_id == device_id).first()
    if row is None or not secret_matches(row.secret_hash, secret):
        return None
    if row.registered_at is not None and row.registered_at < binding_cutoff():
        return None          # давно не входили с этого телефона — канал закрыт до нового входа
    return row


def unregister_with_secret(db, device_id: str, secret: str) -> bool:
    row = device_by_secret(db, device_id, secret)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def update_token(db, device_id: str, secret: str, fcm_token: str) -> bool:
    row = device_by_secret(db, device_id, secret)
    if row is None:
        return False
    row.fcm_token = fcm_token
    row.last_seen_at = datetime.now()
    db.commit()
    return True


def auth_device(device_id: str, secret: str) -> Optional[dict]:
    """Проверка при подключении к каналу; отдельная короткая сессия БД."""
    db = _session()
    try:
        row = device_by_secret(db, device_id, secret)
        if row is None:
            return None
        row.last_seen_at = datetime.now()
        db.commit()
        return {"device_id": row.device_id, "user_id": row.user_id, "provider": row.provider}
    except Exception as e:
        logger.warning("Канал приложения: проверка устройства: %s", e)
        db.rollback()
        return None
    finally:
        db.close()


def load_devices(user_ids: List[int]) -> List[dict]:
    Device = _model()
    db = _session()
    try:
        rows = db.query(Device).filter(Device.user_id.in_(user_ids),
                                       Device.registered_at >= binding_cutoff()).all()
        return [{"device_id": r.device_id, "user_id": r.user_id, "provider": r.provider,
                 "fcm_token": r.fcm_token, "payload_key": r.payload_key} for r in rows]
    finally:
        db.close()


def drop_devices(device_ids: List[str]) -> None:
    Device = _model()
    db = _session()
    try:
        db.query(Device).filter(Device.device_id.in_(device_ids)).delete(synchronize_session=False)
        db.commit()
        logger.info("Уведомления приложения: удалено устройств с недействительным токеном: %d", len(device_ids))
    except Exception as e:
        db.rollback()
        logger.warning("Уведомления приложения: не удалось удалить устройства: %s", e)
    finally:
        db.close()


# ── отправка ─────────────────────────────────────────────────────────────────

def _send_fcm_batch(sender: FcmSender, items: List[Tuple[dict, dict]], ttl: int) -> List[str]:
    dead = []
    for device, body in items:
        try:
            data = encrypt_payload(device["payload_key"], body)
        except Exception as e:
            logger.warning("Уведомления приложения: не зашифровано: %s", e)
            continue
        if sender.send(device["fcm_token"], data, ttl, private_topic(device["payload_key"], body.get("tag", ""))) == "dead":
            dead.append(device["device_id"])
    return dead


async def notify(user_ids: Iterable[int], payload: dict, ttl: int = 3600,
                 loader=None, sender: Optional[FcmSender] = None) -> dict:
    """Разослать уведомление телефонам сотрудников. payload — тот же, что у Web Push.

    Каждый телефон получает свою копию с id (для подтверждения и защиты от
    дублей) и uid получателя: телефон, перешедший к другому сотруднику, чужое
    не покажет. loader и sender подменяются в тестах."""
    result = {"direct": 0, "fcm": 0, "dead": 0}
    if not NATIVE_PUSH_ENABLED:
        return result
    ids = sorted({int(u) for u in user_ids if u})
    if not ids:
        return result
    loop = asyncio.get_running_loop()
    devices = await loop.run_in_executor(_executor, loader or load_devices, ids)
    sender = sender if sender is not None else fcm_sender()
    via_fcm = []
    for device in devices:
        body = dict(payload)
        body["id"] = uuid.uuid4().hex
        body["uid"] = device["user_id"]
        if device["provider"] == "fcm" and device.get("fcm_token") and sender is not None:
            via_fcm.append((device, body))
        else:
            await deliver_direct(device["device_id"], body["id"], body, ttl)
            result["direct"] += 1
    if via_fcm:
        dead = await loop.run_in_executor(_executor, _send_fcm_batch, sender, via_fcm, ttl)
        result["fcm"] = len(via_fcm) - len(dead)
        result["dead"] = len(dead)
        if dead and loader is None:
            await loop.run_in_executor(_executor, drop_devices, dead)
    return result


def status() -> dict:
    sender = fcm_sender()
    return {"enabled": NATIVE_PUSH_ENABLED, "fcm": sender is not None,
            "fcm_error": "" if sender is not None else (_fcm.error if _fcm else ""),
            "ping": PING_SECONDS, **HUB.stats()}
