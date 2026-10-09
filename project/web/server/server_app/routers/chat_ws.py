# -*- coding: utf-8 -*-

import os
import asyncio
import traceback
from datetime import timedelta, datetime, date
from fastapi import FastAPI, Depends, HTTPException, status, Query, Response, UploadFile, File, Form, WebSocket, \
    WebSocketDisconnect
from server.database.init_database import SessionLocal
import uuid
from server.database.init_database_messenger import SessionLocalChat, BaseChat, engine_chat
import json

from server.database.models import *

from fastapi import APIRouter

from ..chat_models import ChatParticipantModel, MessageModel, MessageReactionModel
from ..config import FILE_STORAGE_ROOT, logger
from ..core import user_can_access_task, LazySession
from ..employee_cleanup import UNKNOWN_EMPLOYEE_NAME
from ..helpers import build_message_reactions, create_special_message, get_chats_for_user, push_chat_event, \
    schedule_chat_lists
from ..photos import employee_avatar_url
from ..realtime import UserSnapshot, get_current_user_ws, manager
from ..routers.chat import delete_group_chat
from ..file_access import normalize as normalize_storage_path


router = APIRouter()


def _own_chat_file(chat_id, filepath) -> bool:
    try:
        clean = normalize_storage_path(filepath)
    except ValueError:
        return False
    try:
        return clean.lower().startswith("messenger/chat_%d/" % int(chat_id))
    except (TypeError, ValueError):
        return False


# Сколько ждём вход первым сообщением, прежде чем закрыть соединение
AUTH_TIMEOUT = 10


async def _read_token(websocket: WebSocket, from_query) -> str:
    """Берёт токен для сокета.

    Основной путь — первое сообщение после подключения: {"type":"auth",
    "token":"…"}. В адресе токен передавать нельзя, он оседает в журналах
    сервера и обратного прокси целиком, а это готовая чужая сессия.

    Адрес всё же читаем, если токен пришёл в нём: у человека может быть
    открыта вкладка со старой версией приложения, и обрывать ей связь
    посреди рабочего дня незачем. Как только все обновятся, эту ветку
    можно убрать.
    """
    if from_query:
        logger.info("Сокет: токен пришёл в адресе — вкладка со старой версией")
        return from_query
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=AUTH_TIMEOUT)
    except asyncio.TimeoutError:
        logger.info("Сокет: вход не прислан за %s секунд", AUTH_TIMEOUT)
        return ""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        logger.info("Сокет: первое сообщение не разобралось")
        return ""
    if not isinstance(data, dict) or data.get("type") != "auth":
        logger.info("Сокет: первым сообщением ждали вход, пришло %r",
                    (data.get("type") if isinstance(data, dict) else type(data).__name__))
        return ""
    return str(data.get("token") or "")


@router.websocket("/ws/chat")
async def websocket_endpoint(websocket: WebSocket, token: str = Query(None)):
    user_id = None
    # Ленивые сессии: соединение к БД берётся из пула только на время
    # обработки конкретного сообщения и сразу возвращается обратно.
    db_main = LazySession(SessionLocal, label="main")
    db_chat = LazySession(SessionLocalChat, label="chat")

    try:
        # Соединение принимаем до проверки: иначе первое сообщение с токеном
        # прислать некуда. Ничего, кроме входа, до проверки не обрабатывается.
        await websocket.accept()

        # Аутентификация
        try:
            ticket = await _read_token(websocket, token)
            if not ticket:
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return
            user = await get_current_user_ws(ticket, db_main)
            if not user:
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return
            user_id = user.id
            # Забираем нужные поля до закрытия сессии, иначе дальше
            # обращения к user.last_name дадут DetachedInstanceError
            user = UserSnapshot(user)
        except Exception as e:
            logger.error(f"Ошибка аутентификации: {e}")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        finally:
            # Соединение к основной БД больше не нужно до первого сообщения
            db_main.release()

        # Подключение
        await manager.connect(websocket, user_id)
        logger.info(f"WebSocket подключен для user {user_id}")

        # Основной цикл
        while True:
            try:
                # Освобождаем сессию перед ожиданием следующего сообщения.
                db_chat.release()
                db_main.release()

                # Проверяем состояние соединения перед чтением
                if websocket.client_state.value == 3:  # DISCONNECTED
                    logger.info(f"Соединение уже отключено для user {user_id}")
                    break

                data = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=60.0
                )

                # Обработка ping
                if data == "ping":
                    await websocket.send_text("pong")
                    continue

                # Обработка JSON сообщений
                try:
                    msg_data = json.loads(data)
                except json.JSONDecodeError:
                    logger.error(f"Некорректный JSON от user {user_id}")
                    continue

                msg_type = msg_data.get("type")

                # Обработка ping в JSON формате
                if msg_type == "ping":
                    await websocket.send_json({"type": "pong"})
                    continue

                # Клиент вошёл в детали задачи / вышел из них
                if msg_type in ("task.subscribe", "task.unsubscribe"):
                    try:
                        room_task_id = int(msg_data.get("task_id"))
                    except (TypeError, ValueError):
                        continue
                    if msg_type == "task.subscribe":
                        # Раньше в комнату пускали кого угодно: по чужому id
                        # прилетали описания этапов и имена файлов
                        task = db_main.query(Task).filter(Task.id == room_task_id).first()
                        if not task or not user_can_access_task(db_main, user_id, task):
                            await websocket.send_json({"type": "task.access_denied",
                                                       "task_id": room_task_id})
                            continue
                        await manager.join_task(websocket, room_task_id)
                    else:
                        await manager.leave_task(websocket, room_task_id)
                    continue

                if msg_type == "message":
                    chat_id = msg_data.get("chat_id")
                    text = msg_data.get("text")
                    files = msg_data.get("files", [])  # Новый формат для батча файлов
                    reply_to = msg_data.get("reply_to")

                    if not chat_id:
                        logger.error("Отсутствует chat_id в сообщении")
                        continue

                    participant = db_chat.query(ChatParticipantModel).filter(
                        ChatParticipantModel.chat_id == chat_id,
                        ChatParticipantModel.user_id == user_id
                    ).first()

                    if not participant:
                        logger.error(f"Пользователь {user_id} не участник чата {chat_id}")
                        await websocket.send_json({
                            "type": "error",
                            "message": "Вы не участник этого чата"
                        })
                        continue

                    # Создаем сообщение
                    new_msg = MessageModel(
                        chat_id=chat_id,
                        sender_id=user_id,
                        text=text,
                        timestamp=datetime.now(),
                        parent_id=reply_to
                    )

                    # Если есть файлы, сохраняем только первый (для обратной совместимости)
                    if files and len(files) > 0:
                        first_file = files[0]
                        if _own_chat_file(chat_id, first_file.get('filepath')):
                            new_msg.file_path = first_file.get('filepath')
                            new_msg.file_name = first_file.get('filename')
                            new_msg.file_size = first_file.get('size')
                            new_msg.is_image = first_file.get('is_image', False)
                        else:
                            logger.warning("Сокет: файл вне папки чата %s отклонён: %r", chat_id, first_file.get('filepath'))

                    db_chat.add(new_msg)
                    db_chat.commit()
                    db_chat.refresh(new_msg)

                    # Формируем данные для ответа
                    file_url = None
                    if new_msg.file_path:
                        clean_path = new_msg.file_path.replace('\\', '/')
                        file_url = f"/file_storage/{clean_path}"

                    reply_to_message = None
                    if reply_to:
                        p_msg = db_chat.query(MessageModel).filter(MessageModel.id == reply_to).first()
                        if p_msg:
                            p_emp = db_main.query(Employee).filter(
                                Employee.id == p_msg.sender_id).first() if db_main else None
                            p_sender = f"{p_emp.last_name or ''} {p_emp.first_name or ''}".strip() or p_emp.name if p_emp else 'Неизвестный'
                            reply_to_message = {
                                'id': p_msg.id,
                                'sender_name': p_sender,
                                'text': p_msg.text if p_msg.text else (
                                    'Файл' if p_msg.file_path or p_msg.special == 'file_group' else '')
                            }

                    # Данные для отправки сообщения
                    response_data = {
                        "type": "message",
                        "id": new_msg.id,
                        "chat_id": chat_id,
                        "text": new_msg.text,
                        "sender_id": user_id,
                        "sender_name": f"{user.last_name or ''} {user.first_name or ''}".strip() or (user.name or UNKNOWN_EMPLOYEE_NAME),
                        # Фамилия и имя по отдельности: клиент кэширует эту пару
                        # и считает по ней цвет аватарки без фотографии
                        "sender_last_name": user.last_name or "",
                        "sender_first_name": user.first_name or "",
                        "sender_avatar_url": employee_avatar_url(db_main, user_id),
                        "timestamp": new_msg.timestamp.isoformat(),
                        "file_path": new_msg.file_path,
                        "file_name": new_msg.file_name,
                        "file_size": new_msg.file_size,
                        "url": file_url,
                        "files": files,
                        "parent_id": reply_to,
                        "reply_to_message": reply_to_message,
                        "reactions": {},
                        "reactions_detailed": [],
                        # Метка отправителя возвращается как есть: по ней клиент
                        # заменяет своё временное сообщение настоящим, не создавая
                        # второй пузырь
                        "client_id": msg_data.get("client_id")
                    }

                    # 1. Отправляем само сообщение всем участникам чата
                    await manager.broadcast_to_chat(chat_id, response_data, db_chat)
                    # Push на телефоны тем, у кого сайт закрыт
                    await push_chat_event(db_chat, db_main, chat_id, user_id,
                                          response_data["sender_name"], text, len(files or []))

                    # списки чатов участников: одно обновление на человека за 0,4 с, считается вне цикла событий
                    participants = db_chat.query(ChatParticipantModel.user_id).filter(
                        ChatParticipantModel.chat_id == chat_id
                    ).all()
                    schedule_chat_lists([row.user_id for row in participants])

                elif msg_type == "edit":
                    msg_id = msg_data.get("message_id")
                    new_text = msg_data.get("new_text")

                    if msg_id and new_text:
                        msg = db_chat.query(MessageModel).filter(
                            MessageModel.id == msg_id,
                            MessageModel.sender_id == user_id
                        ).first()

                        if not msg:
                            continue

                        editor_participant = db_chat.query(ChatParticipantModel).filter(
                            ChatParticipantModel.chat_id == msg.chat_id,
                            ChatParticipantModel.user_id == user_id
                        ).first()

                        if not editor_participant:
                            logger.warning(
                                f"Пользователь {user_id} пытается изменить сообщение {msg_id} "
                                f"в чате {msg.chat_id}, из которого он удалён"
                            )
                            await websocket.send_json({
                                "type": "removed_from_chat",
                                "chat_id": msg.chat_id,
                                "message": "Вы больше не участник этого чата"
                            })
                            continue

                        if (msg.metadata_json or {}).get("forwarded_from"):
                            await websocket.send_json({
                                "type": "action_rejected",
                                "chat_id": msg.chat_id,
                                "message": "Пересланное сообщение нельзя редактировать"
                            })
                            continue

                        msg.text = new_text
                        msg.is_edited = True
                        db_chat.commit()

                        await manager.broadcast_to_chat(msg.chat_id, {
                            "type": "edit",
                            "message_id": msg_id,
                            "new_text": new_text,
                            "chat_id": msg.chat_id
                        }, db_chat)

                        participants = db_chat.query(ChatParticipantModel.user_id).filter(
                            ChatParticipantModel.chat_id == msg.chat_id
                        ).all()
                        schedule_chat_lists([row.user_id for row in participants])

                elif msg_type == "reaction":
                    msg_id = msg_data.get("message_id")
                    reaction_type = msg_data.get("reaction_type")
                    chat_id = msg_data.get("chat_id")
                    action = msg_data.get("action", "toggle")

                    if msg_id and reaction_type and chat_id:
                        msg = db_chat.query(MessageModel).filter(MessageModel.id == msg_id).first()

                        reactor_participant = db_chat.query(ChatParticipantModel).filter(
                            ChatParticipantModel.chat_id == chat_id,
                            ChatParticipantModel.user_id == user_id
                        ).first()

                        if not reactor_participant:
                            await websocket.send_json({
                                "type": "removed_from_chat",
                                "chat_id": chat_id,
                                "message": "Вы больше не участник этого чата"
                            })
                            continue

                        if msg and msg.chat_id == chat_id:
                            # Ищем существующую реакцию этого пользователя на это сообщение
                            existing_reaction = db_chat.query(MessageReactionModel).filter(
                                MessageReactionModel.message_id == msg_id,
                                MessageReactionModel.user_id == user_id
                            ).first()

                            # Логика обработки действия
                            if action == 'remove':
                                # Если клиент просит удалить, и реакция есть и совпадает - удаляем
                                if existing_reaction and existing_reaction.reaction_type == reaction_type:
                                    db_chat.delete(existing_reaction)

                            elif action == 'add':
                                # Если клиент просит добавить
                                if existing_reaction:
                                    # Если уже есть реакция, но другая - меняем тип
                                    if existing_reaction.reaction_type != reaction_type:
                                        existing_reaction.reaction_type = reaction_type
                                else:
                                    # Если реакции нет - создаем новую
                                    new_reaction = MessageReactionModel(
                                        message_id=msg_id,
                                        user_id=user_id,
                                        reaction_type=reaction_type
                                    )
                                    db_chat.add(new_reaction)

                            else:  # 'toggle' - старое поведение
                                if existing_reaction:
                                    if existing_reaction.reaction_type == reaction_type:
                                        db_chat.delete(existing_reaction)
                                    else:
                                        existing_reaction.reaction_type = reaction_type
                                else:
                                    new_reaction = MessageReactionModel(
                                        message_id=msg_id,
                                        user_id=user_id,
                                        reaction_type=reaction_type
                                    )
                                    db_chat.add(new_reaction)

                            db_chat.commit()

                            reactions_dict, reactions_detailed = build_message_reactions(
                                db_chat, db_main, msg_id
                            )

                            # Отправляем уведомление всем участникам чата
                            await manager.broadcast_to_chat(chat_id, {
                                "type": "reaction",
                                "message_id": msg_id,
                                "reactions": reactions_dict,
                                "reactions_detailed": reactions_detailed,
                                "chat_id": chat_id,
                                "user_id": user_id
                            }, db_chat)



                elif msg_type == "delete":

                    msg_id = msg_data.get("message_id")

                    if msg_id:

                        msg = db_chat.query(MessageModel).filter(

                            MessageModel.id == msg_id,

                            MessageModel.sender_id == user_id

                        ).first()

                        if not msg:
                            continue

                        deleter_participant = db_chat.query(ChatParticipantModel).filter(
                            ChatParticipantModel.chat_id == msg.chat_id,
                            ChatParticipantModel.user_id == user_id
                        ).first()

                        if not deleter_participant:
                            logger.warning(
                                f"Пользователь {user_id} пытается удалить сообщение {msg_id} "
                                f"в чате {msg.chat_id}, из которого он удалён"
                            )
                            await websocket.send_json({
                                "type": "removed_from_chat",
                                "chat_id": msg.chat_id,
                                "message": "Вы больше не участник этого чата"
                            })
                            continue

                        if msg:

                            chat_id = msg.chat_id

                            # Отвязываем ответы
                            replies = db_chat.query(MessageModel).filter(
                                MessageModel.parent_id == msg_id,
                                MessageModel.special != 'file_item'
                            ).all()
                            for reply in replies:
                                reply.parent_id = None
                            db_chat.flush()

                            # Удаляем прикрепленные файлы
                            file_items = db_chat.query(MessageModel).filter(
                                MessageModel.parent_id == msg_id,
                                MessageModel.special == 'file_item'
                            ).all()
                            _gone_paths = []  # стираются после проверки пересылок
                            for f_item in file_items:
                                if f_item.file_path:
                                    _gone_paths.append(f_item.file_path)
                                db_chat.delete(f_item)
                            db_chat.flush()

                            # Удалить основной файл, если есть
                            if msg.file_path:
                                _gone_paths.append(msg.file_path)
                            # файл стирается, только если на него не ссылаются другие сообщения (пересылка)
                            from ..storage import delete_stored_file
                            for _path in dict.fromkeys(_gone_paths):
                                if not db_chat.query(MessageModel.id).filter(MessageModel.file_path == _path, MessageModel.id != msg.id).first():
                                    delete_stored_file(_path)

                            # Удалить реакции, связанные с сообщением
                            db_chat.query(MessageReactionModel).filter(
                                MessageReactionModel.message_id == msg_id
                            ).delete()

                            # файл группы: запомнить группу до удаления — опустевшую уберём следом
                            item_parent = msg.parent_id if msg.special == 'file_item' else None
                            # ФИЗИЧЕСКОЕ УДАЛЕНИЕ сообщения из БД
                            db_chat.delete(msg)
                            db_chat.commit()

                            await manager.broadcast_to_chat(chat_id, {

                                "type": "delete",

                                "message_id": msg_id,

                                "chat_id": chat_id

                            }, db_chat)
                            if item_parent:
                                parent = db_chat.query(MessageModel).filter(MessageModel.id == item_parent).first()
                                left = db_chat.query(MessageModel.id).filter(MessageModel.parent_id == item_parent,
                                                                             MessageModel.special == 'file_item').first()
                                if parent and not left and not (parent.text or '').strip():
                                    db_chat.query(MessageReactionModel).filter(MessageReactionModel.message_id == parent.id).delete()
                                    gone = parent.id
                                    db_chat.delete(parent)
                                    db_chat.commit()
                                    await manager.broadcast_to_chat(chat_id, {"type": "delete", "message_id": gone, "chat_id": chat_id}, db_chat)

                elif msg_type == "delete_group":
                    chat_id = msg_data.get("chat_id")
                    if chat_id:
                        # Вызываем функцию удаления группы
                        await delete_group_chat(chat_id, db_chat, user_id)





                elif msg_type == "grouped_message":

                    chat_id = msg_data.get("chat_id")

                    text = msg_data.get("text")

                    files = msg_data.get("files", [])  # список уже загруженных файлов
                    reply_to = msg_data.get("reply_to")

                    if not chat_id:
                        logger.error("Отсутствует chat_id в группированном сообщении")

                        continue

                    # --- 1. Проверка прав ---

                    participant = db_chat.query(ChatParticipantModel).filter(

                        ChatParticipantModel.chat_id == chat_id,

                        ChatParticipantModel.user_id == user_id

                    ).first()

                    if not participant:
                        logger.error(f"Пользователь {user_id} не участник чата {chat_id}")

                        await websocket.send_json({

                            "type": "error",

                            "message": "Вы не участник этого чата"

                        })

                        continue

                    # --- 2. Подготовка данных ---

                    group_id = str(uuid.uuid4())

                    timestamp = datetime.now()

                    # --- 3. Создание родительского сообщения (с текстом или без) ---

                    parent_msg = MessageModel(
                        chat_id=chat_id,
                        sender_id=user_id,
                        text=text if text else None,
                        group_id=group_id,
                        timestamp=timestamp,
                        parent_id=reply_to,
                        special="file_group" if files else None
                    )
                    db_chat.add(parent_msg)
                    db_chat.flush()  # получаем ID

                    # --- 4. Создание записей для каждого файла (дочерние сообщения) ---

                    saved_files = []

                    for file_data in files:

                        filepath = file_data.get('filepath')

                        if not filepath:
                            logger.error(f"Пропущен файл: нет filepath")

                            continue

                        # Проверка, что файл уже загружен (путь должен начинаться с messenger/)

                        if not _own_chat_file(chat_id, filepath):
                            logger.error(f"Некорректный путь файла: {filepath}")

                            continue

                        file_msg = MessageModel(

                            chat_id=chat_id,

                            sender_id=user_id,

                            text=None,

                            file_path=filepath,

                            file_name=file_data.get('filename'),

                            file_size=file_data.get('size'),

                            is_image=file_data.get('is_image', False),

                            group_id=group_id,

                            parent_id=parent_msg.id,  # связь с родительским сообщением

                            special="file_item",

                            timestamp=timestamp

                        )

                        db_chat.add(file_msg)

                        db_chat.flush()

                        saved_files.append({

                            "id": file_msg.id,

                            "filename": file_msg.file_name,

                            "filepath": file_msg.file_path,

                            "size": file_msg.file_size,

                            "is_image": file_msg.is_image,

                            "url": "/file_storage/" + file_msg.file_path.replace("\\", "/")

                        })

                    # --- 5. Фиксация всех изменений в БД ---

                    db_chat.commit()

                    db_chat.refresh(parent_msg)

                    # --- 6. Имя отправителя ---

                    sender_name = "Неизвестный"

                    if user:

                        sender_name = f"{user.last_name or ''} {user.first_name or ''}".strip()

                        if not sender_name and user.name:
                            sender_name = user.name

                    if not sender_name:
                        sender_name = UNKNOWN_EMPLOYEE_NAME

                    # --- 7. Формирование одного сообщения для WebSocket ---

                    # --- Формируем сниппет оригинального сообщения для рендера ответа ---
                    reply_to_message = None
                    if reply_to:
                        p_msg = db_chat.query(MessageModel).filter(MessageModel.id == reply_to).first()
                        if p_msg:
                            p_emp = db_main.query(Employee).filter(
                                Employee.id == p_msg.sender_id).first() if db_main else None
                            p_sender = f"{p_emp.last_name or ''} {p_emp.first_name or ''}".strip() or p_emp.name if p_emp else 'Неизвестный'
                            reply_to_message = {
                                'id': p_msg.id,
                                'sender_name': p_sender,
                                'text': p_msg.text if p_msg.text else (
                                    'Файл' if p_msg.file_path or p_msg.special == 'file_group' else '')
                            }

                    
                    sender_avatar_url = employee_avatar_url(db_main, user_id) if user else None

                    message_data = {
                        "type": "grouped_message",
                        "id": parent_msg.id,
                        "chat_id": chat_id,
                        "sender_id": user_id,
                        "sender_name": sender_name,
                        "sender_last_name": (user.last_name or "") if user else "",
                        "sender_first_name": (user.first_name or "") if user else "",
                        "sender_avatar_url": sender_avatar_url,
                        "text": parent_msg.text,
                        "timestamp": parent_msg.timestamp.isoformat(),
                        "group_id": group_id,
                        "special": parent_msg.special,
                        "parent_id": reply_to,  # Передаем фронтенду, что это ответ
                        "reply_to_message": reply_to_message,
                        "files": saved_files,
                        "reactions": {},
                        "reactions_detailed": []
                    }

                    # --- 8. Безопасная отправка всем участникам чата ---

                    participants = db_chat.query(ChatParticipantModel).filter(

                        ChatParticipantModel.chat_id == chat_id

                    ).all()

                    for participant in participants:

                        try:

                            await manager.send_personal_message(message_data, participant.user_id)

                        except Exception as e:

                            logger.error(
                                f"Не удалось отправить группированное сообщение пользователю {participant.user_id}: {e}")

                    await push_chat_event(db_chat, db_main, chat_id, user_id, sender_name,
                                          parent_msg.text, len(saved_files))

                    # --- 9. Отправка подтверждения отправителю (опционально) ---

                    try:

                        await websocket.send_json({

                            "type": "grouped_message_sent",

                            "group_id": group_id,

                            "chat_id": chat_id,

                            "message_id": parent_msg.id

                        })

                    except Exception as e:

                        logger.error(f"Ошибка отправки подтверждения отправителю: {e}")

                    # --- 10. Обновление списка чатов для всех участников ---
                    schedule_chat_lists([p.user_id for p in participants])

                elif msg_type == "special_message":
                    # Обработка специальных сообщений
                    chat_id = msg_data.get("chat_id")
                    special_type = msg_data.get("special_type")
                    metadata = msg_data.get("metadata", {})

                    # Создаем специальное сообщение
                    special_msg = await create_special_message(
                        chat_id=chat_id,
                        sender_id=user_id,
                        special_type=special_type,
                        metadata=metadata,
                        main_db=db_main
                    )

                    db_chat.add(special_msg)
                    db_chat.commit()
                    db_chat.refresh(special_msg)

                    # Отправляем уведомление
                    await manager.broadcast_to_chat(chat_id, {
                        "type": "special_message",
                        "id": special_msg.id,
                        "chat_id": chat_id,
                        "sender_id": user_id,
                        "sender_name": (special_msg.metadata_json or {}).get('actor_name', ''),
                        "text": special_msg.text,
                        "special": special_type,
                        "metadata": special_msg.metadata_json or {},
                        "timestamp": special_msg.timestamp.isoformat()
                    }, db_chat)

            except asyncio.TimeoutError:
                # Просто продолжаем слушать
                continue

            except RuntimeError as e:
                if "WebSocket is not connected" in str(e):
                    logger.warning(f"Попытка чтения из отключенного WebSocket для user {user_id}")
                    break
                else:
                    logger.error(f"RuntimeError в WebSocket для user {user_id}: {e}")
                    break
            except WebSocketDisconnect as e:
                logger.info(f"WebSocket отключен для user {user_id}: {e}")
                break
            except Exception as e:
                logger.error(f"Ошибка обработки WebSocket сообщения: {e}")
                traceback.print_exc()
                try:
                    # Пытаемся отправить сообщение об ошибке
                    if websocket.client_state.value != 3:  # Если еще не отключен
                        await websocket.send_json({
                            "type": "error",
                            # Пользователю — короткая фраза, подробности в логе
                            "message": "Не удалось обработать сообщение",
                            "detail": type(e).__name__
                        })
                except Exception as send_error:
                    logger.debug("Сокет: сообщение об ошибке не доставлено: %s", send_error)

            finally:
                # Сообщение обработано (или упало) — отдаём соединения в пул.
                # Выполняется на любом пути, включая continue и break.
                db_chat.release()
                db_main.release()


    except Exception as e:
        logger.error(f"Критическая ошибка WebSocket: {e}")
        traceback.print_exc()

    finally:
        if user_id:
            await manager.disconnect(websocket, user_id)
        db_main.release()
        db_chat.release()
