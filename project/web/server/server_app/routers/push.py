# -*- coding: utf-8 -*-
"""Подписки Web Push и проверка доставки."""

import re
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from server.database.models import PushSubscription
from ..config import PUSH_ENABLED, VAPID_PUBLIC_KEY
from ..core import get_current_user_id, get_db
from ..push import (any_push_available, endpoint_hash, notify_users, push_available,
                    register_subscription, remove_subscription)
from ..schemas import PushSubscribeIn, PushUnsubscribeIn

router = APIRouter()


@router.get("/push/config")
def push_config(current_user_id: int = Depends(get_current_user_id)):
    """Публичный VAPID-ключ для браузера; enabled=false — клиент не пытается подписаться."""
    return {"enabled": push_available(), "public_key": VAPID_PUBLIC_KEY if PUSH_ENABLED else ""}


@router.post("/push/subscribe")
async def push_subscribe(data: PushSubscribeIn, request: Request,
                         db: Session = Depends(get_db),
                         current_user_id: int = Depends(get_current_user_id)):
    if not push_available():
        raise HTTPException(status_code=503, detail="Push-уведомления на сервере не настроены")
    endpoint = (data.endpoint or "").strip()
    if not endpoint.startswith("https://"):
        raise HTTPException(status_code=400, detail="Некорректный endpoint подписки")
    ua = data.user_agent or request.headers.get("user-agent", "")
    row = register_subscription(db, current_user_id, endpoint, data.keys.p256dh, data.keys.auth, ua)
    return {"id": row.id, "status": "subscribed"}


@router.delete("/push/subscribe")
async def push_unsubscribe(data: PushUnsubscribeIn, db: Session = Depends(get_db),
                           current_user_id: int = Depends(get_current_user_id)):
    removed = remove_subscription(db, current_user_id, (data.endpoint or "").strip())
    return {"status": "removed" if removed else "not_found"}


@router.get("/push/subscriptions")
def my_subscriptions(db: Session = Depends(get_db),
                           current_user_id: int = Depends(get_current_user_id)):
    rows = db.query(PushSubscription).filter(PushSubscription.user_id == current_user_id).all()
    return [{"id": r.id, "user_agent": r.user_agent, "created_at": r.created_at,
             "last_seen_at": r.last_seen_at} for r in rows]


# ===== контрольные уведомления: устройство подтверждает получение =====
_PROBES: dict = {}
_PROBE_TTL = 3600


def _drop_old_probes() -> None:
    now = time.time()
    for key in [k for k, v in _PROBES.items() if now - v["at"] > _PROBE_TTL]:
        _PROBES.pop(key, None)


@router.post("/push/test")
async def push_test(request: Request, current_user_id: int = Depends(get_current_user_id)):
    """Тестовое уведомление самому себе; probe — метка, по которой устройство подтвердит получение."""
    if not any_push_available():
        raise HTTPException(status_code=503, detail="Push-уведомления на сервере не настроены")
    try:
        body = await request.json()
    except Exception:
        body = {}
    probe = str((body or {}).get("probe") or "")
    extra = None
    if probe:
        if not re.fullmatch(r"[0-9a-f]{32}", probe):
            raise HTTPException(status_code=400, detail="Метка проверки — 32 шестнадцатеричных знака")
        _drop_old_probes()
        _PROBES[probe] = {"user_id": current_user_id, "at": time.time(), "acks": []}
        extra = {"probe": probe}
    await notify_users([current_user_id], "Проверка", "Push-уведомления работают",
                       url="#/notifications", tag="push-test", extra=extra)
    return {"status": "sent", "probe": probe or None}


@router.post("/push/ack")
async def push_ack(request: Request, db: Session = Depends(get_db)):
    """Устройство получило контрольное уведомление. Без входа: знает метку только получатель."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    probe = _PROBES.get(str((body or {}).get("probe") or ""))
    if not probe:
        raise HTTPException(status_code=404, detail="Метка проверки не найдена или устарела")
    endpoint = str((body or {}).get("endpoint") or "")[:2048]
    row = None
    if endpoint:
        row = db.query(PushSubscription).filter(PushSubscription.endpoint_hash == endpoint_hash(endpoint)).first()
    if len(probe["acks"]) < 50:
        probe["acks"].append({"subscription_id": row.id if row else None,
                              "user_agent": (row.user_agent if row else request.headers.get("user-agent", ""))[:300],
                              "delay_ms": int((time.time() - probe["at"]) * 1000)})
    return {"status": "ok"}


@router.get("/push/test/{probe}")
def push_probe_state(probe: str, current_user_id: int = Depends(get_current_user_id)):
    item = _PROBES.get(probe)
    if not item or item["user_id"] != current_user_id:
        raise HTTPException(status_code=404, detail="Метка проверки не найдена")
    return {"probe": probe, "acks": item["acks"]}
