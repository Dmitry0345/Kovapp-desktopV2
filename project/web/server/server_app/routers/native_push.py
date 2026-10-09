# -*- coding: utf-8 -*-
"""Регистрация телефонов с приложением Kovapp и собственный канал уведомлений.

  GET    /push/native/config      — что умеет сервер (вход обязателен)
  POST   /push/native/register    — привязать телефон к себе (вход обязателен)
  DELETE /push/native/register    — отвязать свой телефон (вход обязателен)
  POST   /push/native/token       — новый токен FCM (по секрету устройства)
  POST   /push/native/unregister  — отвязать без входа (по секрету устройства)
  WS     /ws/native-push          — канал для телефонов без сервисов Google

Секрет устройства выдаётся при регистрации и хранится на сервере только в
виде хеша, как пароль. Ни токен входа, ни секрет не передаются в адресе.
"""

import asyncio
import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from ..core import get_current_user_id, get_db
from .. import native_push as np

logger = logging.getLogger(__name__)
router = APIRouter()

# Сколько ждём первое сообщение (hello) после подключения
HELLO_TIMEOUT = 10


async def _body(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        data = None
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Ожидался JSON-объект")
    return data


def _device_id(data: dict) -> str:
    device_id = str(data.get("device_id") or "")
    if not np.DEVICE_ID_RE.fullmatch(device_id):
        raise HTTPException(status_code=400, detail="device_id — 32 шестнадцатеричных знака")
    return device_id


@router.get("/push/native/config")
async def native_config(current_user_id: int = Depends(get_current_user_id)):
    return {"enabled": np.native_push_available(), "fcm": np.fcm_sender() is not None, "direct": True,
            "ws_path": np.WS_PATH, "ping": np.PING_SECONDS, "version": np.PROTOCOL_VERSION}


@router.post("/push/native/register")
async def native_register(request: Request, db: Session = Depends(get_db),
                          current_user_id: int = Depends(get_current_user_id)):
    if not np.native_push_available():
        raise HTTPException(status_code=503, detail="Уведомления для приложения выключены на сервере (NATIVE_PUSH_ENABLED)")
    data = await _body(request)
    device_id = _device_id(data)
    provider = str(data.get("provider") or "direct")
    if provider not in ("fcm", "direct"):
        raise HTTPException(status_code=400, detail="provider — fcm или direct")
    fcm_token = np.clip(data.get("fcm_token"), 4096)
    if provider == "fcm" and (not fcm_token or np.fcm_sender() is None):
        provider = "direct"                  # FCM на сервере не настроен — собственный канал
    secret, key, previous = np.register_device(db, current_user_id, device_id, provider, fcm_token,
                                               np.clip(data.get("model"), 120), np.clip(data.get("app_version"), 32))
    if previous is not None and previous != current_user_id:
        await np.forget_device(device_id)       # в очереди могли остаться уведомления прежнего владельца
    logger.info("Уведомления приложения: устройство %s… привязано к %s (%s)", device_id[:8], current_user_id, provider)
    return {"device_id": device_id, "secret": secret, "payload_key": key, "provider": provider, "ping": np.PING_SECONDS}


@router.delete("/push/native/register")
async def native_unregister(request: Request, db: Session = Depends(get_db),
                            current_user_id: int = Depends(get_current_user_id)):
    device_id = _device_id(await _body(request))
    removed = np.unregister_device(db, current_user_id, device_id)
    if removed:
        await np.forget_device(device_id)
    return {"status": "removed" if removed else "not_found"}


@router.post("/push/native/token")
async def native_token(request: Request, db: Session = Depends(get_db)):
    data = await _body(request)
    device_id = _device_id(data)
    fcm_token = np.clip(data.get("fcm_token"), 4096)
    if not fcm_token:
        raise HTTPException(status_code=400, detail="Нет fcm_token")
    if not np.update_token(db, device_id, str(data.get("secret") or ""), fcm_token):
        raise HTTPException(status_code=403, detail="Устройство не найдено")
    return {"status": "ok"}


@router.post("/push/native/unregister")
async def native_unregister_by_secret(request: Request, db: Session = Depends(get_db)):
    data = await _body(request)
    device_id = _device_id(data)
    removed = np.unregister_with_secret(db, device_id, str(data.get("secret") or ""))
    if removed:
        await np.forget_device(device_id)
    return {"status": "removed" if removed else "not_found"}


async def _send(websocket: WebSocket, obj: dict) -> None:
    await websocket.send_text(json.dumps(obj, ensure_ascii=False))


@router.websocket("/ws/native-push")
async def native_socket(websocket: WebSocket):
    """Канал телефона. Первое сообщение — {"type":"hello","device_id","secret"}.
    Сервер отвечает welcome, досылает накопленное (push…, flushed) и дальше шлёт
    уведомления по мере появления; раз в ping секунд — {"type":"ping"}."""
    await serve_socket(websocket, np.HUB, np.auth_device)


async def serve_socket(websocket, hub: "np.DirectHub", authenticate) -> None:
    """Работа канала; hub и authenticate отдельно — чтобы проверять без базы."""
    await websocket.accept()
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=HELLO_TIMEOUT)
        hello = json.loads(raw)
    except Exception:
        await websocket.close(code=1008)
        return
    if not isinstance(hello, dict) or hello.get("type") != "hello":
        await websocket.close(code=1008)
        return
    device_id = str(hello.get("device_id") or "")
    secret = str(hello.get("secret") or "")
    device = None
    if np.native_push_available() and np.DEVICE_ID_RE.fullmatch(device_id) and secret:
        device = await asyncio.get_running_loop().run_in_executor(None, authenticate, device_id, secret)
    if device is None:
        # телефон по этому ответу забывает регистрацию и попросит новую при входе
        try:
            await _send(websocket, {"type": "error", "reason": "unauthorized"})
            await websocket.close(code=1008)
        except Exception:
            pass
        return
    conn, old = hub.attach(device_id, websocket)
    if old is not None:
        try:
            await old.websocket.close(code=1000)     # второе подключение того же телефона — старое не нужно
        except Exception:
            pass
    try:
        await hub._send(conn, {"type": "welcome", "ping": np.PING_SECONDS})
        await hub.flush(device_id, conn)
        last_seen = time.monotonic()
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=np.PING_SECONDS)
            except asyncio.TimeoutError:
                if time.monotonic() - last_seen > np.PING_SECONDS * 2 + 30:
                    logger.info("Канал приложения: %s… не отвечает, закрываю", device_id[:8])
                    break
                if not await hub._send(conn, {"type": "ping"}):
                    break
                continue
            last_seen = time.monotonic()
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            kind = msg.get("type")
            if kind == "ack":
                hub.ack(device_id, str(msg.get("id") or ""))
            elif kind == "ping":
                await hub._send(conn, {"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("Канал приложения: %s", e)
    finally:
        hub.detach(device_id, conn)
