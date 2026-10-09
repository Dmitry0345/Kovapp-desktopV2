# -*- coding: utf-8 -*-

import asyncio
import time
from typing import List, Optional, Dict, Literal
from fastapi import FastAPI, Depends, HTTPException, status, Query, Response, UploadFile, File, Form, WebSocket, \
    WebSocketDisconnect
from sqlalchemy.orm import Session
from sqlalchemy.orm import joinedload, selectinload
from jose import JWTError, jwt

from server.database.models import *

from .chat_models import ChatParticipantModel
from .config import ALGORITHM, SECRET_KEY, logger


class UserSnapshot:
    """
    Снимок полей сотрудника.
    """

    __slots__ = ("id", "last_name", "first_name", "name", "profile")

    def __init__(self, employee):
        self.id = getattr(employee, "id", None)
        self.last_name = getattr(employee, "last_name", None)
        self.first_name = getattr(employee, "first_name", None)
        self.name = getattr(employee, "name", None)
        self.profile = getattr(employee, "profile", None)


MEMBERS_TTL = 5.0
MEMBERS_CHANGED = ("task.participants_updated", "task.updated", "task.created", "stage.participants_updated",
                   "stage.updated", "task.stages_updated")


class ConnectionManager:
    def __init__(self):
        self.user_connections: Dict[int, set] = {}
        self.active_connections: Dict[int, set] = {}
        # Комнаты задач: task_id -> сокеты тех, кто сейчас открыл её детали.
        # Нужны потому, что «кто видит задачу» шире, чем состав задачи:
        # руководство имеет доступ к любой задаче, не будучи в её списках.
        self.task_rooms: Dict[int, set] = {}
        self.socket_tasks: Dict[WebSocket, set] = {}
        self.socket_users: Dict[WebSocket, int] = {}
        self._lock = asyncio.Lock()

        self.heartbeat_task = None

    async def connect(self, websocket: WebSocket, user_id: int):
        # Соединение уже принято точкой входа: она делает это до проверки,
        # чтобы получить токен первым сообщением. Второй accept на том же
        # сокете — ошибка протокола.
        async with self._lock:
            if user_id not in self.user_connections:
                self.user_connections[user_id] = set()
            self.user_connections[user_id].add(websocket)
            self.socket_users[websocket] = user_id

        try:
            await websocket.send_json({
                "type": "connection_status",
                "status": "connected",
                "user_id": user_id
            })
        except Exception as e:
            logger.error(f"Ошибка отправки статуса подключения: {e}")
            await self.disconnect(websocket, user_id)

    async def heartbeat(self):
        """Улучшенный heartbeat механизм"""
        while True:
            await asyncio.sleep(20)
            disconnected = []

            async with self._lock:  # Используем async with
                for user_id, sockets in list(self.user_connections.items()):
                    for ws in list(sockets):
                        try:
                            # Улучшенная проверка состояния соединения
                            if hasattr(ws, 'client_state') and ws.client_state.value == 3:  # DISCONNECTED
                                disconnected.append((ws, user_id))
                                continue

                            if hasattr(ws, 'application_state') and ws.application_state.value == 2:  # CLOSED
                                disconnected.append((ws, user_id))
                                continue

                            # Проверяем, не закрыто ли соединение
                            try:
                                # Отправляем ping через текстовое сообщение
                                await ws.send_json({"type": "ping"})
                            except (WebSocketDisconnect, RuntimeError) as e:
                                # Фильтруем ожидаемые ошибки закрытого соединения
                                if "Unexpected ASGI message 'websocket.send'" not in str(e):
                                    logger.debug(f"Соединение закрыто в heartbeat: {e}")
                                disconnected.append((ws, user_id))
                                continue
                            except Exception as e:
                                # Логируем только неожиданные ошибки
                                if "after sending 'websocket.close'" not in str(e):
                                    logger.debug(f"Ошибка отправки ping: {e}")
                                disconnected.append((ws, user_id))
                                continue

                        except Exception as e:
                            # Фильтруем ошибку блокировки
                            if "'Lock' object does not support the context manager protocol" not in str(e):
                                logger.debug(f"Heartbeat ошибка для user {user_id}: {e}")
                            disconnected.append((ws, user_id))

            # Удаляем отключенные соединения асинхронно
            for ws, user_id in disconnected:
                try:
                    await self.disconnect(ws, user_id)
                except Exception as e:
                    # Фильтруем ошибку блокировки
                    if "'Lock' object does not support the context manager protocol" not in str(e):
                        logger.debug(f"Ошибка при отключении в heartbeat: {e}")

    def _drop_socket_locked(self, websocket: WebSocket, user_id: int):
        """Снимает сокет со всех структур. Вызывать только под self._lock."""
        sockets = self.user_connections.get(user_id)
        if sockets is not None:
            sockets.discard(websocket)
            if not sockets:
                del self.user_connections[user_id]

        for task_id in self.socket_tasks.pop(websocket, set()):
            room = self.task_rooms.get(task_id)
            if room is None:
                continue
            room.discard(websocket)
            if not room:
                del self.task_rooms[task_id]

        self.socket_users.pop(websocket, None)

    async def join_task(self, websocket: WebSocket, task_id: int):
        """Пользователь открыл детали задачи."""
        async with self._lock:
            self.task_rooms.setdefault(task_id, set()).add(websocket)
            self.socket_tasks.setdefault(websocket, set()).add(task_id)

    async def leave_task(self, websocket: WebSocket, task_id: int):
        """Пользователь ушёл из деталей задачи."""
        async with self._lock:
            room = self.task_rooms.get(task_id)
            if room is not None:
                room.discard(websocket)
                if not room:
                    del self.task_rooms[task_id]
            opened = self.socket_tasks.get(websocket)
            if opened is not None:
                opened.discard(task_id)
                if not opened:
                    del self.socket_tasks[websocket]

    async def task_room_user_ids(self, task_id: int) -> set:
        async with self._lock:
            return {self.socket_users[ws]
                    for ws in self.task_rooms.get(task_id, set())
                    if ws in self.socket_users}

    async def disconnect(self, websocket: WebSocket, user_id: int):
        try:
            async with self._lock:  # Используем async with
                self._drop_socket_locked(websocket, user_id)
                logger.debug(f"WebSocket отключен для user {user_id}")
        except Exception as e:
            # Фильтруем ошибку блокировки, не логируем её
            if "'Lock' object does not support the context manager protocol" not in str(e):
                logger.debug(f"Ошибка при отключении user {user_id}: {e}")

    async def send_personal_message(self, message: dict, user_id: int):
        """Отправка с обработкой ошибок"""
        async with self._lock:  # Синхронизируем доступ
            if user_id not in self.user_connections:
                logger.debug(f"User {user_id} не подключен, пропуск сообщения")
                return

            disconnected = []
            for ws in list(self.user_connections[user_id]):
                try:
                    # Проверяем состояние соединения перед отправкой
                    if ws.client_state.value == 3:  # DISCONNECTED
                        disconnected.append(ws)
                        continue

                    # Дополнительная проверка
                    if ws.application_state.value == 2:  # CLOSED
                        disconnected.append(ws)
                        continue

                    await ws.send_json(message)
                except Exception as e:
                    # обычно сокет уже закрыт: вкладку закрыли или пропала сеть
                    logger.info("Сокет пользователя %s закрыт: %s%s", user_id, type(e).__name__, (": %s" % e) if str(e) else "")
                    disconnected.append(ws)

            # Снимаем мёртвые сокеты здесь же: self._lock уже занят, а
            # asyncio.Lock не реентрантный — вызов disconnect() встал бы намертво
            for ws in disconnected:
                self._drop_socket_locked(ws, user_id)

    async def broadcast_to_chat(self, chat_id: int, message: dict, db: Session):
        participants = db.query(ChatParticipantModel).filter(ChatParticipantModel.chat_id == chat_id).all()
        for p in participants:
            await self.send_personal_message(message, p.user_id)

    async def broadcast(self, message: dict, target_user_ids: Optional[List[int]] = None):
        """
        Отправить сообщение всем подключенным пользователям или конкретным пользователям
        """
        try:
            if target_user_ids:
                # Отправляем только указанным пользователям
                for user_id in target_user_ids:
                    await self.send_personal_message(message, user_id)
            else:
                # Отправляем всем подключенным пользователям
                all_user_ids = list(self.user_connections.keys())
                for user_id in all_user_ids:
                    await self.send_personal_message(message, user_id)
        except Exception as e:
            logger.error(f"Ошибка при broadcast: {e}")

    async def broadcast_to_task_participants(self, task_id: int, message: dict, db: Session,
                                             exclude_user_id: Optional[int] = None):
        """
        Отправить уведомление всем, кто относится к задаче.

        Состав задачи (автор, ответственные, исполнители) — не полный список:
        участники этапа набираются целым отделом, а руководство имеет доступ
        к любой задаче вообще без участия. Поэтому к составу добавляются
        участники этапов и все, кто прямо сейчас открыл детали этой задачи.
        """
        try:
            # Собираем всех пользователей, которым нужно отправить уведомление
            user_ids = await self.task_room_user_ids(task_id)
            user_ids |= self._task_members(task_id, db, fresh=str(message.get("type") or "") in MEMBERS_CHANGED)

            user_ids.discard(exclude_user_id)

            # Отправляем каждому пользователю
            for user_id in user_ids:
                await self.send_personal_message(message, user_id)

        except Exception as e:
            logger.error(f"Ошибка при broadcast_to_task_participants: {e}")

    def _task_members(self, task_id: int, db: Session, fresh: bool = False) -> set:
        """Состав задачи: автор, ответственные, исполнители, участники этапов.

        Запоминается на MEMBERS_TTL секунд: при массовой загрузке файлов событие
        о каждом файле иначе заново собирало бы состав задачи из базы. После
        событий, меняющих состав, берётся свежий.
        """
        cache = self.__dict__.setdefault("_members_cache", {})
        hit = cache.get(task_id)
        if hit and not fresh and hit[0] > time.monotonic():
            return set(hit[1])
        task = db.query(Task).options(
            selectinload(Task.responsible_persons),
            selectinload(Task.assigned_employees),
            selectinload(Task.stage_details).selectinload(TaskStageDetails.participants)
        ).filter(Task.id == task_id).first()
        members = set()
        if task:
            if task.creator_id:
                members.add(task.creator_id)
            members.update(emp.id for emp in task.responsible_persons)
            members.update(emp.id for emp in task.assigned_employees)
            for stage in (task.stage_details or []):
                members.update(emp.id for emp in (stage.participants or []))
        else:
            logger.error(f"Задача {task_id} не найдена для broadcast")
        cache[task_id] = (time.monotonic() + MEMBERS_TTL, members)
        if len(cache) > 2000:
            for key in [k for k, v in cache.items() if v[0] <= time.monotonic()]:
                cache.pop(key, None)
        return set(members)

    async def cleanup_dead_connections(self):
        """Очистка мертвых соединений"""
        async with self._lock:
            for user_id in list(self.user_connections.keys()):
                dead_sockets = []
                for ws in self.user_connections[user_id]:
                    try:
                        # Проверяем состояние соединения
                        if ws.client_state.value == 3:  # DISCONNECTED
                            dead_sockets.append(ws)
                    except:
                        dead_sockets.append(ws)

                # Удаляем мертвые соединения
                for ws in dead_sockets:
                    self.user_connections[user_id].remove(ws)

                # Если больше нет соединений, удаляем запись
                if not self.user_connections[user_id]:
                    del self.user_connections[user_id]

    async def periodic_cleanup(self):
        """Периодическая очистка соединений"""
        while True:
            await asyncio.sleep(60)  # Каждую минуту
            await self.cleanup_dead_connections()


async def get_current_user_ws(token: str, db: Session):
    try:
        # Удаляем префикс "Bearer ", если он есть
        if token.startswith("Bearer "):
            token = token[7:]

        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id_str = payload.get("sub")
        if user_id_str is None:
            logger.warning("Токен не содержит sub (user_id)")
            return None

        user_id = int(user_id_str)

        # Проверяем, существует ли пользователь
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            logger.warning(f"Пользователь с ID {user_id} не найден")
            return None

        # Получаем информацию о сотруднике
        employee = db.query(Employee).filter(Employee.id == user_id).first()
        if not employee:
            logger.warning(f"Сотрудник с ID {user_id} не найден")
            return None

        return employee

    except jwt.ExpiredSignatureError:
        logger.warning("Токен истек")
        return None
    except jwt.JWTError as e:
        logger.warning(f"Ошибка JWT: {e}")
        return None
    except Exception as e:
        logger.error(f"Ошибка аутентификации: {e}")
        return None


# ── Разделение на процессы (KOVAPP_ROLE): all — всё в одном процессе, как раньше;
# realtime — только WebSocket; api — всё остальное, события передаёт процессу realtime ──
import hashlib
import json
import os

ROLE = (os.getenv("KOVAPP_ROLE") or "all").strip().lower()
RT_BUS_HOST = "127.0.0.1"
RT_BUS_PORT = int(os.getenv("RT_BUS_PORT") or 8790)
_BUS_TOKEN = hashlib.sha256(("kovapp-rt-bus:" + str(SECRET_KEY)).encode()).hexdigest()


class RemoteManager:
    """В процессе API: рассылки по WebSocket передаются процессу переписки по внутреннему каналу."""

    def __init__(self):
        self.user_connections: Dict[int, list] = {}
        self._writer = None
        self._lock = None

    async def _connection(self):
        if self._writer is not None and not self._writer.is_closing():
            return self._writer
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._writer is None or self._writer.is_closing():
                _, self._writer = await asyncio.open_connection(RT_BUS_HOST, RT_BUS_PORT)
        return self._writer

    async def _send(self, op: str, **fields):
        line = (json.dumps(dict(fields, op=op, token=_BUS_TOKEN), ensure_ascii=False, default=str) + "\n").encode("utf-8")
        for attempt in range(3):
            try:
                writer = await self._connection()
                writer.write(line)
                await writer.drain()
                return
            except Exception as e:
                self._writer = None
                if attempt == 2:
                    logger.warning("Событие %s не передано процессу переписки: %s", op, e)
                await asyncio.sleep(0.2 * (attempt + 1))

    async def send_personal_message(self, message: dict, user_id: int):
        await self._send("personal", message=message, user_id=user_id)

    async def broadcast(self, message: dict, target_user_ids: Optional[List[int]] = None):
        await self._send("broadcast", message=message, users=target_user_ids)

    async def broadcast_to_chat(self, chat_id: int, message: dict, db: Session = None):
        await self._send("chat", message=message, chat_id=chat_id)

    async def broadcast_to_task_participants(self, task_id: int, message: dict, db: Session = None,
                                             exclude_user_id: Optional[int] = None):
        await self._send("task", message=message, task_id=task_id, exclude=exclude_user_id)

    async def native_deliver(self, device_id: str, msg_id: str, payload: dict, ttl: int):
        """Уведомление приложению по собственному каналу: очередь и сокеты телефонов — в процессе переписки."""
        await self._send("native", device_id=device_id, msg_id=msg_id, payload=payload, ttl=ttl)

    async def native_forget(self, device_id: str):
        await self._send("native_forget", device_id=device_id)

    async def heartbeat(self):
        return

    async def periodic_cleanup(self):
        return


async def serve_bus():
    """В процессе переписки: принимает события от процесса API и рассылает их подключённым."""
    from server.database.init_database import SessionLocal
    from server.database.init_database_messenger import SessionLocalChat

    async def handle(reader, writer):
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if data.get("token") != _BUS_TOKEN:
                    logger.warning("Внутренний канал: чужое сообщение отклонено")
                    break
                op, message = data.get("op"), data.get("message") or {}
                try:
                    if op == "personal":
                        await manager.send_personal_message(message, int(data["user_id"]))
                    elif op == "broadcast":
                        await manager.broadcast(message, data.get("users"))
                    elif op == "chat":
                        db = SessionLocalChat()
                        try:
                            await manager.broadcast_to_chat(int(data["chat_id"]), message, db)
                        finally:
                            db.close()
                    elif op == "native":
                        from . import native_push as np
                        await np.HUB.deliver(str(data["device_id"]), str(data["msg_id"]), data.get("payload") or {},
                                             int(data.get("ttl") or 3600))
                    elif op == "native_forget":
                        from . import native_push as np
                        await np.HUB.forget(str(data["device_id"]))
                    elif op == "task":
                        db = SessionLocal()
                        try:
                            await manager.broadcast_to_task_participants(int(data["task_id"]), message, db,
                                                                         exclude_user_id=data.get("exclude"))
                        finally:
                            db.close()
                except Exception as e:
                    logger.warning("Внутренний канал: событие %s не разослано: %s", op, e)
        finally:
            writer.close()

    server = await asyncio.start_server(handle, RT_BUS_HOST, RT_BUS_PORT)
    logger.info("Процесс переписки принимает события на %s:%d", RT_BUS_HOST, RT_BUS_PORT)
    async with server:
        await server.serve_forever()


manager = RemoteManager() if ROLE in ("api", "reader", "uploader") else ConnectionManager()
