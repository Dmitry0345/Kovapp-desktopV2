# -*- coding: utf-8 -*-
"""Web Push (VAPID) — уведомления на телефон/ПК даже при закрытом сайте.

Как это работает (кратко):
  1. Браузер регистрирует service worker и просит у пользователя разрешение.
  2. Он получает у своего push-сервиса (Google/Mozilla/Apple) адрес подписки
     и присылает его нам: POST /push/subscribe.
  3. Когда происходит событие (сообщение в чате, уведомление, добавление
     в группу), сервер шлёт короткий зашифрованный JSON на этот адрес, а
     push-сервис будит браузер, и service worker показывает уведомление.

Ограничения: работает только по HTTPS (secure context); на iOS — только
для сайта, добавленного на экран «Домой». Без ключей VAPID модуль просто
молчит — остальное приложение не зависит от него.

Отправка идёт в пуле потоков: pywebpush синхронный, а обработчики — async.
"""

import asyncio
import hashlib
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from server.database.init_database import SessionLocal
from server.database.models import PushSubscription
from .config import PUSH_ENABLED, VAPID_PRIVATE_KEY, VAPID_SUBJECT

logger = logging.getLogger(__name__)

try:
    from pywebpush import WebPushException, webpush
    PYWEBPUSH_AVAILABLE = True
except Exception:  # библиотека не установлена
    webpush = None
    WebPushException = Exception
    PYWEBPUSH_AVAILABLE = False

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="webpush")

# Сколько символов текста показывать в уведомлении
PREVIEW_LIMIT = 120


def push_available() -> bool:
    return PUSH_ENABLED and PYWEBPUSH_AVAILABLE


def any_push_available() -> bool:
    """Есть ли вообще чем доставлять: Web Push или приложение Kovapp."""
    if push_available():
        return True
    try:
        from .native_push import native_push_available
        return native_push_available()
    except Exception:
        return False


def endpoint_hash(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()


def register_subscription(db: Session, user_id: int, endpoint: str, p256dh: str, auth: str,
                          user_agent: Optional[str]) -> PushSubscription:
    """Сохраняет подписку; тот же endpoint у другого пользователя переезжает к нему."""
    h = endpoint_hash(endpoint)
    row = db.query(PushSubscription).filter(PushSubscription.endpoint_hash == h).first()
    if row is None:
        row = PushSubscription(user_id=user_id, endpoint=endpoint, endpoint_hash=h,
                               p256dh=p256dh, auth=auth, user_agent=(user_agent or "")[:255],
                               created_at=datetime.now(), last_seen_at=datetime.now())
        db.add(row)
    else:
        row.user_id = user_id
        row.p256dh = p256dh
        row.auth = auth
        row.user_agent = (user_agent or row.user_agent or "")[:255]
        row.last_seen_at = datetime.now()
    db.commit()
    db.refresh(row)
    return row


def remove_subscription(db: Session, user_id: int, endpoint: str) -> bool:
    h = endpoint_hash(endpoint)
    deleted = db.query(PushSubscription).filter(
        PushSubscription.endpoint_hash == h, PushSubscription.user_id == user_id
    ).delete(synchronize_session=False)
    db.commit()
    return bool(deleted)


def _topic(tag: str) -> str:
    """Заголовок Topic: пока телефон офлайн, push-сервис хранит только последнее
    сообщение с той же темой. Допустимы только [A-Za-z0-9_-], не длиннее 32."""
    return re.sub(r"[^A-Za-z0-9_-]", "", tag or "kovcheg")[:32] or "kovcheg"


def _send_sync(sub: dict, payload: dict, ttl: int) -> Optional[int]:
    """Возвращает HTTP-код ошибки push-сервиса (404/410 = подписка мертва) или None при успехе."""
    try:
        webpush(
            subscription_info={"endpoint": sub["endpoint"],
                               "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims={"sub": VAPID_SUBJECT},
            ttl=ttl,
            # high — телефон будят сразу даже в режиме энергосбережения
            headers={"Urgency": "high", "Topic": _topic(payload.get("tag", ""))},
        )
        return None
    except WebPushException as e:
        response = getattr(e, "response", None)
        code = getattr(response, "status_code", None)
        logger.warning("Web Push не доставлен (%s): %s", code, e)
        return code or -1
    except Exception as e:
        logger.warning("Web Push: ошибка отправки: %s", e)
        return -1


def _deliver_to_users(user_ids: Iterable[int], payload: dict, ttl: int = 3600) -> None:
    """Синхронная часть: читает подписки, шлёт, чистит мёртвые. Выполняется в потоке."""
    ids = {int(u) for u in user_ids if u}
    if not ids:
        return
    db = SessionLocal()
    try:
        rows = db.query(PushSubscription).filter(PushSubscription.user_id.in_(ids)).all()
        dead = []
        for row in rows:
            code = _send_sync({"endpoint": row.endpoint, "p256dh": row.p256dh, "auth": row.auth}, payload, ttl)
            if code in (404, 410):
                dead.append(row.id)
        if dead:
            db.query(PushSubscription).filter(PushSubscription.id.in_(dead)).delete(synchronize_session=False)
            db.commit()
            logger.info("Web Push: удалено мёртвых подписок: %d", len(dead))
    except Exception as e:
        logger.error("Web Push: сбой доставки: %s", e)
        db.rollback()
    finally:
        db.close()


async def notify_users(user_ids: Iterable[int], title: str, body: str,
                       url: str = "/", tag: Optional[str] = None, extra: Optional[dict] = None,
                       ttl: int = 3600) -> None:
    """Асинхронно отправляет push перечисленным пользователям. Ошибки не всплывают.

    ttl — сколько секунд push-сервис хранит сообщение для телефона, который
    сейчас офлайн: для чата час (потом сообщение всё равно прочитают в чате),
    для уведомлений раздела — сутки.
    """
    user_ids = list(user_ids)
    payload = {"title": title, "body": (body or "")[:PREVIEW_LIMIT], "url": url.lstrip("/"),
               "tag": tag or "kovcheg", "ts": datetime.now().isoformat()}
    if extra:
        payload.update(extra)
    # Приложение Kovapp (Android): отдельной задачей, чтобы не задерживать Web Push
    _start_native(user_ids, payload, ttl)
    if not push_available():
        return
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(_executor, _deliver_to_users, list(user_ids), payload, ttl)
    except Exception as e:
        logger.error("Web Push: не удалось поставить отправку: %s", e)


_native_tasks: set = set()


def _start_native(user_ids: list, payload: dict, ttl: int) -> None:
    try:
        from .native_push import native_push_available, notify as native_notify
        if not native_push_available():
            return
        task = asyncio.get_running_loop().create_task(native_notify(user_ids, dict(payload), ttl))
        _native_tasks.add(task)
        task.add_done_callback(_native_done)
    except Exception as e:
        logger.warning("Уведомления приложения: не поставлены: %s", e)


def _native_done(task) -> None:
    _native_tasks.discard(task)
    if not task.cancelled() and task.exception() is not None:
        logger.warning("Уведомления приложения: сбой: %s", task.exception())


# ── Готовые события ──────────────────────────────────────────────────────────

def _preview(text: Optional[str], files_count: int = 0) -> str:
    text = (text or "").strip()
    if text:
        return text if len(text) <= PREVIEW_LIMIT else text[:PREVIEW_LIMIT - 1] + "…"
    if files_count:
        return f"Файлы: {files_count}" if files_count > 1 else "Файл"
    return "Новое сообщение"


async def push_chat_message(chat_id: int, sender_id: int, sender_name: str,
                            chat_name: Optional[str], is_group: bool,
                            recipient_ids: Iterable[int], text: Optional[str],
                            files_count: int = 0) -> None:
    """Сообщение в личном или групповом чате — всем участникам, кроме автора."""
    recipients = [uid for uid in recipient_ids if uid and uid != sender_id]
    if not recipients:
        return
    if is_group:
        title = chat_name or "Групповой чат"
        body = f"{sender_name}: {_preview(text, files_count)}"
    else:
        title = sender_name or "Новое сообщение"
        body = _preview(text, files_count)
    await notify_users(recipients, title, body, url=f"#/messenger/{chat_id}",
                       tag=f"chat-{chat_id}", extra={"kind": "chat", "chat_id": chat_id})


async def push_added_to_group(chat_id: int, chat_name: Optional[str], actor_name: str,
                              added_user_ids: Iterable[int]) -> None:
    await notify_users(added_user_ids, "Вас добавили в группу",
                       f"{actor_name} добавил(а) вас в «{chat_name or 'группу'}»",
                       url=f"#/messenger/{chat_id}", tag=f"chat-{chat_id}",
                       extra={"kind": "group_added", "chat_id": chat_id})


def notification_url(target_section: Optional[str], target_id: Optional[int]) -> str:
    """Куда ведёт уведомление раздела — как в списке уведомлений веб-версии
    (web_app/js/views/notifications.js, targetHash)."""
    if target_id:
        if target_section in ("task_view", "task_complete"):
            return f"#/tasks/{target_id}"
        if target_section == "warehouse":
            return f"#/projects/{target_id}/warehouse"
        if target_section == "project":
            return f"#/projects/{target_id}"
        if target_section == "equipment":
            return "#/equipment"
    return "#/notifications"


async def push_notification(user_id: int, message: str, target_section: Optional[str],
                            target_id: Optional[int]) -> None:
    """Обычное уведомление раздела «Уведомления»."""
    # Адреса относительные (без ведущего «/»): service worker разрешает их
    # относительно своей области, поэтому работают и /app/, и корень сайта
    url = notification_url(target_section, target_id)
    await notify_users([user_id], "Уведомление", message, url=url,
                       tag=f"notification-{target_id or 'general'}",
                       extra={"kind": "notification", "target_section": target_section,
                              "target_id": target_id},
                       ttl=86400)
