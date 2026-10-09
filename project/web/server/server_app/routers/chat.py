# -*- coding: utf-8 -*-

import mimetypes
import os
import asyncio
import contextlib
import traceback
from datetime import timedelta, datetime, date
from typing import List, Optional, Dict, Literal
import gzip
from sqlalchemy import or_, and_, func, select
from fastapi import FastAPI, Depends, HTTPException, status, Query, Response, UploadFile, File, Form, WebSocket, \
    WebSocketDisconnect
from fastapi import BackgroundTasks
from fastapi import Request
from sqlalchemy.orm import Session
from sqlalchemy.orm import joinedload
from sqlalchemy import asc, desc
from pathlib import Path
from server.database.init_database import SessionLocal
import shutil
import uuid
from server.database.init_database_messenger import SessionLocalChat, BaseChat, engine_chat
import json
from sqlalchemy import desc, text, func, true, false

from server.database.models import *

from fastapi import APIRouter

from ..chat_models import ChatModel, ChatParticipantModel, MessageModel, MessageReactionModel
from ..config import FILE_STORAGE_ROOT, MESSENGER_STORAGE_ROOT, logger
from ..core import (get_chat_db, get_current_user_id,
                    get_current_user_id_from_header_or_query, get_db,
                    hidden_account_ids, is_hidden_account)
from ..helpers import message_preview, _last_sender_label, push_chat_event, push_group_added, _assert_chat_member, _broadcast_chats_updated, _count_unread, _media_category, _pick_new_group_owner, _purge_group_chat, build_message_reactions, build_reactions_map, create_special_message, get_chats_for_user, get_employee_full_name, parse_timestamp_safe, schedule_chat_lists
from ..employee_cleanup import UNKNOWN_EMPLOYEE_NAME
from ..photos import employee_avatar_url, employee_ids_with_photo, employee_photo_url
from ..realtime import manager
from .files import parse_edits
from ..schemas import AddParticipants, ForwardMessageRequest, GroupUpdate, ReactionUpdate, RoleUpdate
from ..storage import _storage_path, get_decompressed_file_response, process_video_sync, resolve_stored_path
from .. import media
from .. import file_access
from ..config import GZIP_LEVEL


router = APIRouter()


@router.get("/chat/download/{message_id}")
def download_chat_file(
        message_id: int,
        request: Request,
        db: Session = Depends(get_chat_db),
        current_user_id: int = Depends(get_current_user_id_from_header_or_query)
):
    try:
        msg = db.query(MessageModel).filter(MessageModel.id == message_id).first()
        if not msg or not msg.file_path:
            raise HTTPException(status_code=404, detail="Файл не найден")
        file_access.check_chat_member(db, current_user_id, msg.chat_id, "chat/download/{message_id}")

        # Используем существующую функцию распаковки для отдачи файла
        # Это решает проблему путей и GZIP
        return get_decompressed_file_response(msg.file_path, msg.file_name or "file", download=True,
                                              request=request)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Ошибка скачивания файла чата: %s", e)
        raise HTTPException(status_code=500, detail="Ошибка скачивания файла")


@router.put("/chat/group/{chat_id}")
async def update_group_info(
        chat_id: int,
        data: GroupUpdate,
        current_user_id: int = Depends(get_current_user_id),
        db: Session = Depends(get_chat_db),
        main_db: Session = Depends(get_db)
):
    participant = db.query(ChatParticipantModel).filter(
        ChatParticipantModel.chat_id == chat_id,
        ChatParticipantModel.user_id == current_user_id,
        ChatParticipantModel.role.in_(['admin', 'owner'])
    ).first()

    if not participant:
        raise HTTPException(status_code=403, detail="Только администратор может изменять настройки группы")

    chat = db.query(ChatModel).filter(ChatModel.id == chat_id).first()
    if not chat:
        raise HTTPException(status_code=404, detail="Чат не найден")

    renamed = False
    if data.name and data.name != chat.name:
        chat.name = data.name
        renamed = True
    if data.image_path:
        # Убедимся, что путь начинается с /file_storage
        if not data.image_path.startswith('/file_storage'):
            data.image_path = f"/file_storage{data.image_path}"
        chat.image_path = data.image_path

    special_msg = None
    if renamed:
        special_msg = await create_special_message(
            chat_id=chat_id,
            sender_id=current_user_id,
            special_type="group_rename",
            metadata={"new_name": chat.name},
            main_db=main_db
        )
        db.add(special_msg)

    db.commit()
    db.refresh(chat)

    # Отправляем уведомление об обновлении группы
    await manager.broadcast_to_chat(chat_id, {
        "type": "group_updated",
        "chat_id": chat_id,
        "name": chat.name,
        "image_path": chat.image_path
    }, db)

    if special_msg is not None:
        db.refresh(special_msg)
        await manager.broadcast_to_chat(chat_id, {
            "type": "special_message",
            "id": special_msg.id,
            "chat_id": chat_id,
            "sender_id": current_user_id,
            "sender_name": (special_msg.metadata_json or {}).get('actor_name', ''),
            "text": special_msg.text,
            "special": "group_rename",
            "metadata": special_msg.metadata_json or {},
            "timestamp": special_msg.timestamp.isoformat()
        }, db)
        await _broadcast_chats_updated(chat_id, db, main_db)

    return {"id": chat.id, "name": chat.name, "image_path": chat.image_path}


@router.get("/chat/list")
def get_chats(
        current_user_id: int = Depends(get_current_user_id),
        db: Session = Depends(get_chat_db),
        main_db: Session = Depends(get_db)
):
    try:
        # все чаты пользователя — несколькими запросами на всё сразу, а не по нескольку на каждый чат
        from ..photos import employee_ids_with_photo, employee_photo_url
        participations = [p for p in db.query(ChatParticipantModel).filter(
            ChatParticipantModel.user_id == current_user_id
        ).options(joinedload(ChatParticipantModel.chat)).all() if p.chat]
        if not participations:
            return []
        chat_ids = [p.chat.id for p in participations]

        last_ids = db.query(MessageModel.chat_id, func.max(MessageModel.id).label("mid")).filter(
            MessageModel.chat_id.in_(chat_ids)).group_by(MessageModel.chat_id).subquery()
        last_by_chat = {m.chat_id: m for m in db.query(MessageModel).join(last_ids, MessageModel.id == last_ids.c.mid).all()}
        counts = dict(db.query(MessageModel.chat_id, func.count(MessageModel.id)).filter(
            MessageModel.chat_id.in_(chat_ids)).group_by(MessageModel.chat_id).all())

        private_ids = [p.chat.id for p in participations if not p.chat.is_group]
        other_by_chat = {}
        if private_ids:
            for other in db.query(ChatParticipantModel).filter(
                    ChatParticipantModel.chat_id.in_(private_ids),
                    ChatParticipantModel.user_id != current_user_id).order_by(ChatParticipantModel.id).all():
                other_by_chat.setdefault(other.chat_id, other)

        unread_by_chat = {}
        conditions = [and_(MessageModel.chat_id == p.chat.id, MessageModel.id > (p.last_read_message_id or 0))
                      for p in participations if p.chat.id in last_by_chat]
        if conditions:
            unread_by_chat = dict(db.query(MessageModel.chat_id, func.count(MessageModel.id)).filter(
                or_(*conditions), MessageModel.sender_id != current_user_id,
                or_(MessageModel.special.is_(None), MessageModel.special != 'file_item')
            ).group_by(MessageModel.chat_id).all())

        people_ids = {o.user_id for o in other_by_chat.values()}
        people_ids.update(m.sender_id for m in last_by_chat.values() if m.sender_id and int(m.sender_id) != int(current_user_id))
        people = {e.id: e for e in main_db.query(Employee).filter(Employee.id.in_(people_ids)).all()} if people_ids else {}
        with_photo = employee_ids_with_photo(main_db, [o.user_id for o in other_by_chat.values() if o.user_id in people])

        result = []
        for p in participations:
            chat = p.chat
            last_msg = last_by_chat.get(chat.id)
            chat_name = chat.name
            chat_last_name = ""
            chat_first_name = ""
            other_user_id = None
            avatar_url = None
            if not chat.is_group:
                other_p = other_by_chat.get(chat.id)
                if other_p:
                    employee = people.get(other_p.user_id)
                    if employee:
                        chat_last_name = employee.last_name or ""
                        chat_first_name = employee.first_name or ""
                        chat_name = f"{employee.last_name or ''} {employee.first_name or ''}".strip()
                        if not chat_name and employee.name:
                            chat_name = employee.name
                        other_user_id = employee.id
                        avatar_url = employee_photo_url(employee.id) if employee.id in with_photo else None
                    else:
                        chat_name = UNKNOWN_EMPLOYEE_NAME
                else:
                    chat_name = "Избранное"

            last_msg_text = ""
            last_msg_time = ""
            last_msg_datetime = None
            if last_msg:
                if last_msg.text:
                    last_msg_text = message_preview(last_msg.text, 50)
                elif last_msg.file_path:
                    last_msg_text = "📄 Файл"
                if last_msg.timestamp:
                    try:
                        if isinstance(last_msg.timestamp, datetime):
                            last_msg_datetime = last_msg.timestamp
                            last_msg_time = last_msg.timestamp.strftime("%H:%M")
                        else:
                            ts_str = str(last_msg.timestamp)
                            if ":" in ts_str:
                                parts = ts_str.split(":")
                                if len(parts) >= 2:
                                    last_msg_time = f"{parts[0]}:{parts[1]}"
                            last_msg_datetime = parse_timestamp_safe(last_msg.timestamp)
                    except Exception as e:
                        logger.debug("Список чатов: время последнего сообщения не разобралось: %s", e)

            if last_msg_datetime:
                sort_time, time_source = last_msg_datetime, "message"
            elif chat.created_at:
                sort_time, time_source = chat.created_at, "creation"
            else:
                sort_time, time_source = datetime.now(), "creation_default"

            sender = ""
            if last_msg and last_msg.sender_id:
                if int(last_msg.sender_id) == int(current_user_id):
                    sender = "Вы"
                else:
                    emp = people.get(last_msg.sender_id)
                    if emp:
                        last = (emp.last_name or "").strip()
                        first = (emp.first_name or "").strip()
                        sender = f"{last} {first[0]}." if last and first else (last or first or (emp.name or ""))

            result.append({
                "id": chat.id,
                "name": chat_name,
                "last_name": chat_last_name,
                "first_name": chat_first_name,
                "is_group": chat.is_group,
                "other_user_id": other_user_id,
                "image_path": chat.image_path,
                "avatar_url": avatar_url,
                "last_message": last_msg_text,
                "last_message_sender": sender,
                "last_message_time": last_msg_time,
                "timestamp": sort_time.timestamp(),
                "has_messages": bool(last_msg),
                "sort_datetime": sort_time.isoformat() if sort_time else None,
                "time_source": time_source,
                "role": p.role,
                "message_count": counts.get(chat.id, 0),
                "created_at": chat.created_at.isoformat() if chat.created_at else None,
                "unread_count": unread_by_chat.get(chat.id, 0) if last_msg else 0,
            })

        result.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        return result

    except Exception as e:
        logger.exception("Список чатов не собран: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/chat/group/{chat_id}")
async def delete_group_chat(
        chat_id: int,
        db: Session = Depends(get_chat_db),
        current_user_id: int = Depends(get_current_user_id)
):
    """
    Удаление группового чата.
    Только владелец (owner) может удалить группу.
    """
    try:
        # Находим групповой чат
        chat = db.query(ChatModel).filter(
            ChatModel.id == chat_id,
            ChatModel.is_group == true()
        ).first()

        if not chat:
            raise HTTPException(status_code=404, detail="Групповой чат не найден")

        # Проверяем, является ли текущий пользователь владельцем
        participant = db.query(ChatParticipantModel).filter(
            ChatParticipantModel.chat_id == chat_id,
            ChatParticipantModel.user_id == current_user_id,
            ChatParticipantModel.role == 'owner'
        ).first()

        if not participant:
            raise HTTPException(
                status_code=403,
                detail="Только владелец группы может удалить группу"
            )

        # Получаем всех участников чата для нотификации
        participants = db.query(ChatParticipantModel).filter(
            ChatParticipantModel.chat_id == chat_id
        ).all()
        participant_ids = [p.user_id for p in participants]

        # Запоминаем имя группы для сообщения
        group_name = chat.name

        # Файлы чата; пересланные в другие чаты остаются
        from ..helpers import _purge_chat_files
        _purge_chat_files(db, chat_id)

        # Удаляем папку аватара группы
        avatar_folder = MESSENGER_STORAGE_ROOT / "avatars" / f"chat_{chat_id}"
        if avatar_folder.exists() and avatar_folder.is_dir():
            try:
                shutil.rmtree(avatar_folder, ignore_errors=True)
                logger.info(f"Папка аватара чата {chat_id} удалена: {avatar_folder}")
            except Exception as e:
                logger.error(f"Ошибка удаления папки аватара чата {chat_id}: {e}")

        # Удаляем все реакции сообщений
        messages = db.query(MessageModel).filter(MessageModel.chat_id == chat_id).all()
        for msg in messages:
            db.query(MessageReactionModel).filter(
                MessageReactionModel.message_id == msg.id
            ).delete()

        # Удаляем все сообщения чата
        db.query(MessageModel).filter(MessageModel.chat_id == chat_id).delete()

        # Удаляем всех участников
        db.query(ChatParticipantModel).filter(
            ChatParticipantModel.chat_id == chat_id
        ).delete()

        # Удаляем сам чат
        db.delete(chat)
        db.commit()

        # Это заставит клиент обновить список чатов
        for user_id in participant_ids:
            try:
                await manager.send_personal_message({
                    "type": "group_deleted",
                    "chat_id": chat_id,
                    "message": f"Группа '{group_name}' была удалена"
                }, user_id)
            except Exception as e:
                logger.error(f"Ошибка отправки уведомления об удалении группы пользователю {user_id}: {e}")

        # Также отправляем обновление списка чатов для всех участников
        try:
            for user_id in participant_ids:
                # Получаем актуальный список чатов для пользователя
                db_main = SessionLocal()
                db_chat = SessionLocalChat()
                try:
                    updated_chats = await get_chats_for_user(user_id, db_chat, db_main)
                    await manager.send_personal_message({
                        "type": "chats_updated",
                        "chats": updated_chats
                    }, user_id)
                finally:
                    db_main.close()
                    db_chat.close()
        except Exception as e:
            logger.error(f"Ошибка отправки обновления чатов: {e}")

        logger.info(f"Группа {chat_id} удалена пользователем {current_user_id}")

        return {
            "status": "success",
            "message": f"Группа '{group_name}' успешно удалена",
            "chat_id": chat_id
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Ошибка при удалении группы {chat_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при удалении группы: {str(e)}"
        )


@router.delete("/chat/group/{chat_id}/leave")
async def leave_group_chat(
        chat_id: int,
        current_user_id: int = Depends(get_current_user_id),
        db: Session = Depends(get_chat_db),
        main_db: Session = Depends(get_db)
):
    """
    Выход из группы.

    Раньше владелец получал 400 Bad Request: выйти ему не давали, предлагая
    только удалить группу. Но кнопка выхода показывалась всем, поэтому для
    владельца она просто молча не работала. Теперь владелец тоже может выйти —
    права переходят самому давнему администратору (если админов нет —
    самому давнему участнику), а если уходить больше некому, группа удаляется.
    """
    chat = db.query(ChatModel).filter(ChatModel.id == chat_id).first()
    if not chat:
        raise HTTPException(status_code=404, detail="Чат не найден")

    if not chat.is_group:
        raise HTTPException(status_code=400, detail="Из личного чата нельзя выйти")

    target = db.query(ChatParticipantModel).filter(
        ChatParticipantModel.chat_id == chat_id,
        ChatParticipantModel.user_id == current_user_id
    ).first()

    if not target:
        raise HTTPException(status_code=404, detail="Вы не состоите в этой группе")

    group_name = chat.name
    new_owner = None
    is_owner = (target.role == 'owner')

    if is_owner:
        new_owner = _pick_new_group_owner(db, chat_id, current_user_id)

    # --- Случай 1: уходит последний участник — группы больше нет ---
    remaining = db.query(ChatParticipantModel).filter(
        ChatParticipantModel.chat_id == chat_id,
        ChatParticipantModel.user_id != current_user_id
    ).count()

    if remaining == 0:
        try:
            _purge_group_chat(db, chat)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"Ошибка удаления группы {chat_id} при выходе последнего участника: {e}")
            raise HTTPException(status_code=500, detail="Не удалось выйти из группы")

        try:
            await manager.send_personal_message({
                "type": "group_deleted",
                "chat_id": chat_id,
                "message": f"Группа «{group_name}» удалена: вы были последним участником"
            }, current_user_id)
        except Exception as e:
            logger.error(f"Не удалось уведомить пользователя {current_user_id} об удалении группы: {e}")

        logger.info(f"Пользователь {current_user_id} вышел из группы {chat_id}; группа удалена (участников не осталось)")
        return {
            "status": "success",
            "message": "Вы вышли из группы. Группа удалена, так как участников не осталось.",
            "group_deleted": True
        }

    # --- Случай 2: обычный выход (в том числе владельца с передачей прав) ---
    try:
        metadata = {}
        if new_owner is not None:
            metadata["new_owner_id"] = new_owner.user_id
            metadata["new_owner_name"] = get_employee_full_name(main_db, new_owner.user_id)

        # Сервисное сообщение "<Фамилия Имя> вышел из группы"
        special_msg = await create_special_message(
            chat_id=chat_id,
            sender_id=current_user_id,
            special_type="member_leave",
            metadata=metadata,
            main_db=main_db
        )
        db.add(special_msg)

        owner_msg = None
        if new_owner is not None:
            new_owner.role = 'owner'
            db.add(new_owner)

            owner_msg = await create_special_message(
                chat_id=chat_id,
                sender_id=current_user_id,
                special_type="owner_transfer",
                metadata={
                    "user_id": new_owner.user_id,
                    "user_name": metadata.get("new_owner_name")
                },
                main_db=main_db
            )
            db.add(owner_msg)

        db.delete(target)
        db.commit()
        db.refresh(special_msg)
        if owner_msg is not None:
            db.refresh(owner_msg)
    except Exception as e:
        db.rollback()
        logger.error(f"Ошибка выхода пользователя {current_user_id} из группы {chat_id}: {e}")
        raise HTTPException(status_code=500, detail="Не удалось выйти из группы")

    await manager.broadcast_to_chat(chat_id, {
        "type": "special_message",
        "id": special_msg.id,
        "chat_id": chat_id,
        "sender_id": current_user_id,
        "sender_name": (special_msg.metadata_json or {}).get('actor_name', ''),
        "text": special_msg.text,
        "special": "member_leave",
        "metadata": special_msg.metadata_json or {},
        "timestamp": special_msg.timestamp.isoformat()
    }, db)

    if owner_msg is not None:
        await manager.broadcast_to_chat(chat_id, {
            "type": "special_message",
            "id": owner_msg.id,
            "chat_id": chat_id,
            "sender_id": current_user_id,
            "sender_name": (owner_msg.metadata_json or {}).get('actor_name', ''),
            "text": owner_msg.text,
            "special": "owner_transfer",
            "metadata": owner_msg.metadata_json or {},
            "timestamp": owner_msg.timestamp.isoformat()
        }, db)

        # Чтобы у нового владельца сразу появились права в открытой карточке
        await manager.broadcast_to_chat(chat_id, {
            "type": "group_role_changed",
            "chat_id": chat_id,
            "user_id": new_owner.user_id,
            "role": "owner"
        }, db)

    await _broadcast_chats_updated(chat_id, db, main_db, extra_user_ids=[current_user_id])

    logger.info(f"Пользователь {current_user_id} вышел из группы {chat_id}"
                + (f"; владельцем стал {new_owner.user_id}" if new_owner is not None else ""))

    result = {"status": "success", "message": "Вы вышли из группы", "group_deleted": False}
    if new_owner is not None:
        result["new_owner_id"] = new_owner.user_id
        result["new_owner_name"] = metadata.get("new_owner_name")
    return result


@router.delete("/chat/group/{chat_id}/participants/{user_id}")
async def remove_group_participant(
        chat_id: int,
        user_id: int,
        current_user_id: int = Depends(get_current_user_id),
        db: Session = Depends(get_chat_db),
        main_db: Session = Depends(get_db)
):
    # Проверка прав
    admin = db.query(ChatParticipantModel).filter(
        ChatParticipantModel.chat_id == chat_id,
        ChatParticipantModel.user_id == current_user_id,
        ChatParticipantModel.role.in_(['admin', 'owner'])  # Обновлено
    ).first()

    if not admin:
        raise HTTPException(status_code=403, detail="Нет прав для удаления участников")

    chat = db.query(ChatModel).filter(ChatModel.id == chat_id).first()
    group_name = chat.name if chat else ""

    target = db.query(ChatParticipantModel).filter(
        ChatParticipantModel.chat_id == chat_id,
        ChatParticipantModel.user_id == user_id
    ).first()

    if not target:
        raise HTTPException(status_code=404, detail="Участник не найден")

    # Нельзя удалить владельца группы
    if target.role == 'owner':
        raise HTTPException(status_code=400, detail="Нельзя удалить владельца группы")

    # Админ не может удалить другого админа
    if admin.role == 'admin' and target.role == 'admin':
        raise HTTPException(status_code=403, detail="Администратор не может удалить другого администратора")

    # Создаем сервисное сообщение ДО удаления, пока участник ещё в базе
    special_msg = await create_special_message(
        chat_id=chat_id,
        sender_id=current_user_id,
        special_type="member_remove",
        metadata={"user_id": user_id},
        main_db=main_db
    )
    db.add(special_msg)

    db.delete(target)

    db.commit()
    db.refresh(special_msg)

    payload = {
        "type": "special_message",
        "id": special_msg.id,
        "chat_id": chat_id,
        "sender_id": current_user_id,
        "sender_name": (special_msg.metadata_json or {}).get('actor_name', ''),
        "text": special_msg.text,
        "special": "member_remove",
        "metadata": special_msg.metadata_json or {},
        "timestamp": special_msg.timestamp.isoformat()
    }

    # Оповещаем оставшихся участников чата
    await manager.broadcast_to_chat(chat_id, payload, db)

    try:
        await manager.send_personal_message({
            "type": "removed_from_chat",
            "chat_id": chat_id,
            "chat_name": group_name,
            "message": (f"Вас удалили из группы «{group_name}»"
                        if group_name else "Вас удалили из группы")
        }, user_id)
    except Exception as e:
        logger.error(f"Не удалось уведомить пользователя {user_id} об исключении из группы {chat_id}: {e}")

    # Обновляем списки чатов: у оставшихся — новый последний текст,
    # у удалённого — чат должен пропасть из списка
    await _broadcast_chats_updated(chat_id, db, main_db, extra_user_ids=[user_id])

    # Если после удаления в группе никого не осталось — удаляем и её саму.
    # Раньше пустая группа висела в базе: очистка была только на выходе.
    left = db.query(ChatParticipantModel).filter(
        ChatParticipantModel.chat_id == chat_id).count()
    if left == 0:
        try:
            _purge_group_chat(db, chat)
            db.commit()
            logger.info("Группа %s удалена: после исключения участников не осталось", chat_id)
        except Exception as e:
            db.rollback()
            logger.error("Не удалось удалить опустевшую группу %s: %s", chat_id, e)

    return {"status": "success", "message": "Участник удален"}


@router.post("/chat/private")
async def create_private_chat(
        data: dict,
        current_user_id: int = Depends(get_current_user_id),
        db: Session = Depends(get_chat_db),
        main_db: Session = Depends(get_db)  # Добавляем основную БД
):
    target_id = data.get("target_user_id")
    if not target_id:
        raise HTTPException(status_code=400, detail="Не указан target_user_id")

    # Проверяем, существует ли сотрудник с таким ID
    employee = main_db.query(Employee).filter(Employee.id == target_id).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Сотрудник не найден")
    # Служебная запись администратора в собеседники не предлагается и в
    # обход клиента тоже не заводится. Уже созданные чаты работают как были.
    if is_hidden_account(employee) and target_id != current_user_id:
        raise HTTPException(status_code=403, detail="С этим сотрудником нельзя начать переписку")

    # Формируем имя для отображения
    employee_name = f"{employee.last_name or ''} {employee.first_name or ''}".strip()
    if not employee_name and employee.name:
        employee_name = employee.name

    # Проверка на существование чата
    my_chats = db.query(ChatParticipantModel.chat_id).join(ChatModel).filter(
        ChatParticipantModel.user_id == current_user_id,
        ChatModel.is_group == False
    ).subquery()

    common_chat = db.query(ChatParticipantModel).filter(
        ChatParticipantModel.user_id == target_id,
        ChatParticipantModel.chat_id.in_(select(my_chats))
    ).first()

    if common_chat:
        chat_obj = db.query(ChatModel).filter(ChatModel.id == common_chat.chat_id).first()
        if chat_obj and not chat_obj.is_group:
            return {
                "id": chat_obj.id,
                "name": employee_name,
                "is_group": False,
                "target_user_id": target_id
            }

    # Создаем новый чат
    new_chat = ChatModel(is_group=False)
    db.add(new_chat)
    db.commit()

    db.add(ChatParticipantModel(chat_id=new_chat.id, user_id=current_user_id))
    db.add(ChatParticipantModel(chat_id=new_chat.id, user_id=target_id))
    db.commit()

    # Оба участника получают обновлённый список чатов сразу, без перезахода:
    # у создателя чат встаёт в список, у собеседника появляется впервые
    await _broadcast_chats_updated(new_chat.id, db, main_db,
                                   extra_user_ids=[current_user_id, target_id])

    return {
        "id": new_chat.id,
        "name": employee_name,
        "is_group": False,
        "target_user_id": target_id
    }


@router.post("/chat/group")
async def create_group_chat(
    data: dict, 
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_chat_db),
    main_db: Session = Depends(get_db)
):
    name = data.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")

    new_chat = ChatModel(name=name, is_group=True)
    db.add(new_chat)
    db.commit()

    # Создатель получает роль owner
    db.add(ChatParticipantModel(
        chat_id=new_chat.id,
        user_id=current_user_id,
        role='owner'
    ))
    db.commit()

    # Оповещаем создателя (и его открытые сессии) об обновлении списка чатов
    await _broadcast_chats_updated(new_chat.id, db, main_db, extra_user_ids=[current_user_id])

    return {"id": new_chat.id, "name": name, "is_group": True, "role": "owner"}


@router.delete("/chat/message/{message_id}")
async def delete_chat_message(
        message_id: int,
        db: Session = Depends(get_chat_db),
        current_user_id: int = Depends(get_current_user_id)
):
    try:
        # Найти сообщение
        msg = db.query(MessageModel).filter(MessageModel.id == message_id).first()
        if not msg:
            raise HTTPException(status_code=404, detail="Сообщение не найдено")

        # Проверить права доступа (только отправитель может удалить)
        if msg.sender_id != current_user_id:
            raise HTTPException(status_code=403, detail="Нельзя удалить чужое сообщение")

        _assert_chat_member(db, msg.chat_id, current_user_id)

        # Отвязываем ответы
        replies = db.query(MessageModel).filter(
            MessageModel.parent_id == message_id,
            MessageModel.special != 'file_item'
        ).all()
        for reply in replies:
            reply.parent_id = None
        db.flush()

        # Удаляем прикрепленные файлы
        file_items = db.query(MessageModel).filter(
            MessageModel.parent_id == message_id,
            MessageModel.special == 'file_item'
        ).all()
        _gone_paths = []  # стираются после проверки пересылок
        for f_item in file_items:
            if f_item.file_path:
                _gone_paths.append(f_item.file_path)
            db.delete(f_item)
        db.flush()

        # Удалить основной файл, если есть
        if msg.file_path:
            _gone_paths.append(msg.file_path)
        # файл стирается, только если на него не ссылаются другие сообщения (пересылка)
        from ..storage import delete_stored_file
        for _path in dict.fromkeys(_gone_paths):
            if not db.query(MessageModel.id).filter(MessageModel.file_path == _path, MessageModel.id != msg.id).first():
                delete_stored_file(_path)

        # Удалить реакции, связанные с сообщением
        db.query(MessageReactionModel).filter(
            MessageReactionModel.message_id == message_id
        ).delete()

        # ФИЗИЧЕСКОЕ УДАЛЕНИЕ сообщения из БД
        db.delete(msg)
        db.commit()

        # Отправить уведомление об удалении через WebSocket
        await manager.broadcast_to_chat(msg.chat_id, {
            "type": "delete",
            "message_id": message_id,
            "chat_id": msg.chat_id
        }, db)

        return {"message": "Сообщение удалено"}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Ошибка при удалении сообщения: {e}")
        raise HTTPException(status_code=500, detail="Ошибка при удалении сообщения")


async def _video_poster(path: Path) -> None:
    """Обложка видео рядом с файлом (<файл>.thumb.jpg), как миниатюра у фото: чат показывает её до воспроизведения."""
    def make():
        # одна обложка — лёгкая работа, но в общем пределе обработки машины
        try:
            slot = media._governor().slot("light")
        except Exception:
            slot = contextlib.nullcontext()
        with slot:
            return media.make_video_poster(str(path))
    try:
        thumb = await asyncio.to_thread(make)
        if thumb:
            Path(str(path) + media.THUMB_SUFFIX).write_bytes(thumb)
        else:
            logger.warning("Обложка видео не создана (ffmpeg не выдал кадр): %s", path.name)
    except Exception:
        logger.warning("Обложка видео не создана: %s", path.name, exc_info=True)


@router.post("/chat/upload")
async def upload_chat_file(
        background_tasks: BackgroundTasks,
        file: UploadFile = File(...),
        chat_id: int = Form(...),
        is_avatar: Optional[bool] = Form(False),
        current_user_id: int = Depends(get_current_user_id),
        db: Session = Depends(get_chat_db),
        main_db: Session = Depends(get_db)
):
    member = db.query(ChatParticipantModel).filter(
        ChatParticipantModel.chat_id == chat_id,
        ChatParticipantModel.user_id == current_user_id).first()
    if not member:
        raise HTTPException(status_code=403, detail="Вы не участник этого чата")
    if is_avatar and (member.role or "") not in ("admin", "owner"):
        raise HTTPException(status_code=403, detail="Только администратор может изменять настройки группы")
    try:
        # Оборачиваем чтение в try для отлова ClientDisconnect
        try:
            content = await file.read()
        except Exception as e:
            logger.error(f"Upload aborted: {e}")
            raise HTTPException(status_code=400, detail="Upload aborted")

        filename_lower = file.filename.lower()
        is_video = filename_lower.endswith(('.mp4', '.mov', '.avi', '.mkv', '.webm'))
        # Фото хранится без gzip: оптимизируется (EXIF-поворот, до IMAGE_MAX_SIDE) и
        # получает миниатюру; документы — gzip уровня GZIP_LEVEL
        is_optimizable_image = media.is_image_name(file.filename)

        if is_video or is_optimizable_image:
            compressed_content = content
        else:
            compressed_content = gzip.compress(content, compresslevel=GZIP_LEVEL)

        # Для аватаров используем специальную папку
        if is_avatar:
            avatar_folder = MESSENGER_STORAGE_ROOT / "avatars" / f"chat_{chat_id}"
            avatar_folder.mkdir(parents=True, exist_ok=True)

            chat = db.query(ChatModel).filter(ChatModel.id == chat_id).first()
            if chat and chat.image_path:
                old_path = Path(FILE_STORAGE_ROOT) / chat.image_path
                old_gz_path = Path(f"{old_path}.gz")

                # Удаляем старый файл с расширением .gz
                if old_gz_path.exists():
                    old_gz_path.unlink()
                    logger.info(f"Старый аватар удален: {old_gz_path}")

                # Также удаляем файл без .gz (если существует)
                if old_path.exists():
                    old_path.unlink()
                    logger.info(f"Старый аватар удален: {old_path}")

            # Генерируем уникальное имя файла
            file_ext = Path(file.filename).suffix.lower() or ".jpg"

            if is_video:
                unique_filename = f"avatar_{uuid.uuid4()}{media.video_suffix()}"
                final_file_path = avatar_folder / unique_filename
                temp_input_path = avatar_folder / f"temp_{uuid.uuid4()}_{file.filename}"

                with open(temp_input_path, "wb") as buffer:
                    buffer.write(compressed_content)

                try:
                    await asyncio.to_thread(process_video_sync, str(temp_input_path), str(final_file_path))
                except Exception as e:
                    logger.exception(f"Ошибка конвертации видео аватара: {e}")
                    # НЕ сохраняем исходник под именем .webm — такой файл не играется.
                    if final_file_path.exists():
                        try:
                            final_file_path.unlink()
                        except Exception:
                            logger.exception(f"Не удалось удалить битый файл: {final_file_path}")
                    raise HTTPException(
                        status_code=422,
                        detail=("Не удалось подготовить видео к воспроизведению. "
                                "Проверьте, что на сервере установлен ffmpeg c кодеками "
                                "libvpx-vp9/libopus.")
                    )
                finally:
                    if os.path.exists(temp_input_path):
                        try:
                            os.remove(temp_input_path)
                        except Exception:
                            logger.exception(f"Не удалось удалить временный файл: {temp_input_path}")

                gz_file_path = final_file_path
            elif is_optimizable_image:
                optimized, new_ext = await asyncio.to_thread(media.optimize_image, content, file_ext)
                unique_filename = f"avatar_{uuid.uuid4()}{new_ext}"
                file_path = avatar_folder / unique_filename
                file_path.write_bytes(optimized)
                thumb = await asyncio.to_thread(media.make_image_thumbnail, optimized)
                if thumb:
                    Path(str(file_path) + media.THUMB_SUFFIX).write_bytes(thumb)
                gz_file_path = file_path
            else:
                unique_filename = f"avatar_{uuid.uuid4()}{file_ext}"
                file_path = avatar_folder / unique_filename
                gz_file_path = Path(f"{file_path}.gz")

                with open(gz_file_path, "wb") as buffer:
                    buffer.write(compressed_content)

            # Возвращаем путь относительно корня хранилища БЕЗ .gz
            relative_path = gz_file_path.relative_to(FILE_STORAGE_ROOT)

            # Обновляем информацию о группе
            if chat:
                # Сохраняем путь без .gz
                chat.image_path = str(relative_path).replace('\\', '/')
                
                # Создаем специальное сообщение
                special_msg = await create_special_message(
                    chat_id=chat_id,
                    sender_id=current_user_id,
                    special_type="avatar_change",
                    metadata={},
                    main_db=main_db
                )
                db.add(special_msg)
                db.commit()
                db.refresh(special_msg)

                # Оповещаем чат
                await manager.broadcast_to_chat(chat_id, {
                    "type": "special_message",
                    "id": special_msg.id,
                    "chat_id": chat_id,
                    "sender_id": current_user_id,
                    "sender_name": (special_msg.metadata_json or {}).get('actor_name', ''),
                    "text": special_msg.text,
                    "special": "avatar_change",
                    "metadata": special_msg.metadata_json or {},
                    "timestamp": special_msg.timestamp.isoformat()
                }, db)

                # Список чатов слева должен подхватить новую картинку у всех
                await _broadcast_chats_updated(chat_id, db, main_db)

            return {
                "url": f"/file_storage/{relative_path}",
                "filename": file.filename,
                "filepath": str(relative_path).replace('\\', '/'),  # Без .gz
                "size": len(content),
                "is_image": True
            }
        else:
            # Обычная загрузка файла для сообщений
            chat_folder_name = f"chat_{chat_id}"
            chat_storage_path = MESSENGER_STORAGE_ROOT / chat_folder_name
            chat_storage_path.mkdir(parents=True, exist_ok=True)

            file_ext = Path(file.filename).suffix.lower()
            if is_video:
                base_name = os.path.splitext(file.filename)[0]
                new_filename = f"{base_name}{media.video_suffix()}"
                unique_filename = f"{uuid.uuid4()}{media.video_suffix()}"
                final_file_path = chat_storage_path / unique_filename
                temp_input_path = chat_storage_path / f"temp_{uuid.uuid4()}_{file.filename}"

                with open(temp_input_path, "wb") as buffer:
                    buffer.write(compressed_content)

                try:
                    await asyncio.to_thread(process_video_sync, str(temp_input_path), str(final_file_path))
                except Exception as e:
                    logger.exception(f"Ошибка конвертации видео: {e}")
                    if final_file_path.exists():
                        try:
                            final_file_path.unlink()
                        except Exception:
                            logger.exception(f"Не удалось удалить битый файл: {final_file_path}")
                    raise HTTPException(
                        status_code=422,
                        detail=("Не удалось подготовить видео к воспроизведению. "
                                "Проверьте, что на сервере установлен ffmpeg c кодеками "
                                "libvpx-vp9/libopus.")
                    )
                finally:
                    if os.path.exists(temp_input_path):
                        try:
                            os.remove(temp_input_path)
                        except Exception:
                            logger.exception(f"Не удалось удалить временный файл: {temp_input_path}")
                await _video_poster(final_file_path)

                full_relative_path = final_file_path.relative_to(FILE_STORAGE_ROOT)
                file_url = f"/file_storage/{full_relative_path}"

                return {
                    "url": file_url,
                    "filename": new_filename,
                    "filepath": str(full_relative_path).replace('\\', '/'),
                    "size": len(content),
                    "is_image": False
                }
            else:
                shown_name = file.filename
                unique_filename = f"{uuid.uuid4()}{file_ext}"
                file_path = chat_storage_path / unique_filename

                if is_optimizable_image:
                    # Картинка перекодируется, а не просто пакуется в gzip:
                    # в мессенджере это самый частый вид вложений
                    optimized, new_ext = await asyncio.to_thread(media.optimize_image, content, file_ext)
                    if new_ext != file_ext:
                        unique_filename = f"{uuid.uuid4()}{new_ext}"
                        file_path = chat_storage_path / unique_filename
                        shown_name = Path(file.filename or "image").stem + new_ext
                    file_path.write_bytes(optimized)
                    thumb = await asyncio.to_thread(media.make_image_thumbnail, optimized)
                    if thumb:
                        Path(str(file_path) + media.THUMB_SUFFIX).write_bytes(thumb)
                else:
                    gz_file_path = Path(f"{file_path}.gz")
                    with open(gz_file_path, "wb") as buffer:
                        buffer.write(compressed_content)

                # Возвращаем путь относительно FILE_STORAGE_ROOT БЕЗ .gz
                full_relative_path = file_path.relative_to(FILE_STORAGE_ROOT)
                file_url = f"/file_storage/{full_relative_path}"

                return {
                    "url": file_url,
                    "filename": shown_name,
                    "filepath": str(full_relative_path).replace('\\', '/'),  # Без .gz
                    "size": len(content),
                    "is_image": (getattr(file, "content_type", None) or "").startswith("image/") if getattr(file, "content_type", None) else file_ext in ['.png',
                                                                                                              '.jpg',
                                                                                                              '.jpeg',
                                                                                                              '.gif',
                                                                                                              '.bmp',
                                                                                                              '.webp']
                }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка загрузки файла: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat/group/{chat_id}/members")
async def add_group_members(
        chat_id: int,
        user_ids: List[int],
        db: Session = Depends(get_chat_db),
        main_db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    # Добавлять в группу может только тот, кто сам в ней состоит
    member = db.query(ChatParticipantModel).filter_by(
        chat_id=chat_id, user_id=current_user_id
    ).first()
    if not member:
        raise HTTPException(status_code=403, detail="Вы не участник этого чата")

    # Служебную запись администратора в группу не добавляют
    blocked = hidden_account_ids(main_db, user_ids) - {current_user_id}
    if blocked:
        raise HTTPException(status_code=403, detail="Этого сотрудника нельзя добавить в беседу")

    added = []
    for uid in user_ids:
        exists = db.query(ChatParticipantModel).filter_by(chat_id=chat_id, user_id=uid).first()
        if not exists:
            db.add(ChatParticipantModel(chat_id=chat_id, user_id=uid))
            added.append(uid)
    db.commit()
    if added:
        from server.database.init_database import SessionLocal as _MainSession
        main_db = _MainSession()
        try:
            await push_group_added(db, main_db, chat_id, current_user_id, added)
        finally:
            main_db.close()
    return {"status": "ok"}


@router.get("/chat/history/{chat_id}")
def get_chat_history(
        chat_id: int,
        limit: int = Query(50, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        around_id: Optional[int] = Query(None, description="Загрузить сообщения вокруг указанного ID"),
        before_id: Optional[int] = Query(None, description="Загрузить сообщения старше указанного ID"),
        after_id: Optional[int] = Query(None, description="Загрузить сообщения новее указанного ID"),
        db: Session = Depends(get_chat_db),
        main_db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    """Получение истории сообщений с поддержкой пагинации и загрузки вокруг конкретного сообщения"""
    try:
        # 1. Проверка, является ли пользователь участником чата
        participant = db.query(ChatParticipantModel).filter(
            ChatParticipantModel.chat_id == chat_id,
            ChatParticipantModel.user_id == current_user_id
        ).first()
        if not participant:
            raise HTTPException(
                status_code=403,
                detail="Вы не участник этого чата"
            )

        # 2. Базовый запрос к сообщениям
        base_query = db.query(MessageModel).filter(MessageModel.chat_id == chat_id)

        # 3. Определяем тип пагинации
        if around_id is not None:
            target_msg = base_query.filter(MessageModel.id == around_id).first()
            if not target_msg:
                raise HTTPException(
                    status_code=404,
                    detail=f"Сообщение с ID {around_id} не найдено в этом чате"
                )
            count_before = base_query.filter(MessageModel.timestamp > target_msg.timestamp).count()
            offset = max(0, count_before - limit // 2)
            messages_query = base_query.order_by(desc(MessageModel.timestamp)) \
                .offset(offset).limit(limit).all()

        elif before_id is not None:
            # Сообщения старше указанного ID (пагинация назад)
            target_msg = base_query.filter(MessageModel.id == before_id).first()
            if not target_msg:
                raise HTTPException(404, "Опорное сообщение не найдено")
            messages_query = base_query.filter(MessageModel.id < before_id) \
                .order_by(desc(MessageModel.id)).limit(limit).all()

        elif after_id is not None:
            # Сообщения новее указанного ID (пагинация вперёд)
            target_msg = base_query.filter(MessageModel.id == after_id).first()
            if not target_msg:
                raise HTTPException(404, "Опорное сообщение не найдено")
            messages_query = base_query.filter(MessageModel.id > after_id) \
                .order_by(asc(MessageModel.id)).limit(limit).all()
            # Переворачиваем, чтобы вернуть от новых к старым (как ожидает клиент)
            messages_query = list(reversed(messages_query))

        else:
            # Стандартная пагинация по offset
            messages_query = base_query.order_by(desc(MessageModel.timestamp)) \
                .offset(offset).limit(limit).all()

        # 4. Формируем результат (логика из оригинального эндпоинта)
        # Реакции и наличие фотографий берём пакетно на всю страницу истории,
        # иначе получалось по два запроса к БД на каждое сообщение
        reactions_map = build_reactions_map(db, main_db, [m.id for m in messages_query])
        sender_photo_ids = employee_ids_with_photo(
            main_db, [m.sender_id for m in messages_query]
        )

        result = []
        for msg in messages_query:
            # Имя отправителя
            sender_name = "Неизвестный"
            employee = main_db.query(Employee).filter(Employee.id == msg.sender_id).first()
            if employee:
                sender_name = f"{employee.last_name or ''} {employee.first_name or ''}".strip()
                if not sender_name and employee.name:
                    sender_name = employee.name

            # Файлы
            files = []
            if msg.file_path:
                files.append({
                    "id": msg.id,
                    "path": msg.file_path,
                    "name": msg.file_name,
                    "filepath": msg.file_path,
                    "filename": msg.file_name,
                    "size": msg.file_size,
                    "is_image": msg.is_image,
                    "url": f"/file_storage/{msg.file_path}" if msg.file_path else None
                })

            reactions_data = reactions_map.get(
                msg.id, {"reactions": {}, "reactions_detailed": []}
            )

            msg_dict = {
                "id": msg.id,
                "chat_id": msg.chat_id,
                "sender_id": msg.sender_id,
                "sender_name": sender_name,
                # Пара "фамилия / имя" для расчёта цвета аватарки на клиенте
                "sender_last_name": (employee.last_name or "") if employee else "",
                "sender_first_name": (employee.first_name or "") if employee else "",
                "sender_avatar_url": (employee_photo_url(msg.sender_id)
                                      if msg.sender_id in sender_photo_ids else None),
                "text": msg.text,
                "timestamp": msg.timestamp,
                "is_read": msg.is_read,
                "is_edited": msg.is_edited,
                "special": msg.special,
                "metadata": msg.metadata_json or {},
                "group_id": msg.group_id,
                "parent_id": msg.parent_id,
                "files": files,
                "reactions": reactions_data["reactions"],
                "reactions_detailed": reactions_data["reactions_detailed"],
                "is_grouped": bool(msg.group_id) and msg.group_id not in ['', '0', 'null']
            }
            result.append(msg_dict)

        # 5. Сортировка по времени (от новых к старым)
        def get_sort_key(item):
            ts = item['timestamp']
            if isinstance(ts, str):
                try:
                    return datetime.fromisoformat(ts.replace('Z', '+00:00'))
                except:
                    return datetime.min
            if ts is None:
                return datetime.min
            return ts

        result.sort(key=get_sort_key, reverse=True)

        # 6. Сериализация дат в строки
        for item in result:
            if isinstance(item['timestamp'], datetime):
                item['timestamp'] = item['timestamp'].isoformat()

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting chat history: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Ошибка получения истории")


@router.get("/chat/group/{chat_id}")
def get_chat_group_info(
        chat_id: int,
        db: Session = Depends(get_chat_db),
        main_db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    """
    Возвращает метаданные о группе/чате.
    """
    try:
        chat = db.query(ChatModel).filter(ChatModel.id == chat_id).first()
        if not chat:
            raise HTTPException(status_code=404, detail="Чат или группа не найдена")
        _assert_chat_member(db, chat_id, current_user_id)

        participants_query = db.query(ChatParticipantModel).filter(
            ChatParticipantModel.chat_id == chat_id
        ).all()

        my_role = 'participant'
        participants_data = []
        photo_ids = employee_ids_with_photo(main_db, [p.user_id for p in participants_query])
        for p in participants_query:
            if p.user_id == current_user_id:
                my_role = p.role

            emp = main_db.query(Employee).filter(Employee.id == p.user_id).first()
            emp_name = "Unknown"
            if emp:
                emp_name = f"{emp.last_name or ''} {emp.first_name or ''}".strip() or emp.name or f"User #{p.user_id}"

            participants_data.append({
                "id": p.user_id,
                "name": emp_name,
                "first_name": emp.first_name if emp else "",
                "last_name": emp.last_name if emp else "",
                "avatar_url": employee_photo_url(p.user_id) if p.user_id in photo_ids else None,
                "role": p.role
            })

        return {
            "chat_id": chat.id,
            "name": chat.name,
            "image_path": chat.image_path,
            "my_role": my_role,
            "participants": participants_data,
            "type": "group" if chat.is_group else "private",
            "participant_count": len(participants_data),
            "status": "success"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/chat/info/{target_id}")
def get_chat_info_card(
        target_id: int,
        as_user: bool = Query(False, description="Трактовать target_id строго как id сотрудника"),
        db: Session = Depends(get_chat_db),
        main_db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    """
    Возвращает метаданные для карточки (chat info card).
    Универсальный эндпоинт: обрабатывает как chat_id, так и напрямую user_id,
    а также имеет fallback на message_id, если фронтенд передал его по ошибке.

    as_user=true используется при клике на аватарку рядом с сообщением: там
    target_id — это заведомо id сотрудника, и трактовать его как chat_id нельзя
    (иначе при совпадении номеров откроется карточка чужого чата).
    """
    try:
        # 1. Проверяем, является ли target_id чатом, в котором состоит текущий пользователь
        chat = None
        participant = None
        if not as_user:
            chat = db.query(ChatModel).filter(ChatModel.id == target_id).first()
            if chat:
                participant = db.query(ChatParticipantModel).filter(
                    ChatParticipantModel.chat_id == target_id,
                    ChatParticipantModel.user_id == current_user_id
                ).first()

        if chat and participant:
            # Для групп
            if chat.is_group:
                participants_query = db.query(ChatParticipantModel).filter(
                    ChatParticipantModel.chat_id == target_id
                ).all()

                participants_data = []
                photo_ids = employee_ids_with_photo(main_db, [p.user_id for p in participants_query])
                for p in participants_query:
                    emp = main_db.query(Employee).filter(Employee.id == p.user_id).first()
                    emp_name = "Unknown"
                    if emp:
                        emp_name = f"{emp.last_name or ''} {emp.first_name or ''}".strip() or emp.name or f"User #{p.user_id}"

                    participants_data.append({
                        "id": p.user_id,
                        "full_name": emp_name,
                        # Пара для расчёта цвета аватарки в карточке чата
                        "last_name": (emp.last_name or "") if emp else "",
                        "first_name": (emp.first_name or "") if emp else "",
                        "role": p.role,
                        "avatar_url": employee_photo_url(p.user_id) if p.user_id in photo_ids else None
                    })

                return {
                    "chat_id": chat.id,
                    "is_group": True,
                    "name": chat.name,
                    "image_path": chat.image_path,
                    "participants": participants_data,
                    "my_role": participant.role
                }
            else:
                # Для личного чата
                other_participant = db.query(ChatParticipantModel).filter(
                    ChatParticipantModel.chat_id == target_id,
                    ChatParticipantModel.user_id != current_user_id
                ).first()

                # Обработка чата "Избранное" (с самим собой)
                if not other_participant:
                    other_participant = db.query(ChatParticipantModel).filter(
                        ChatParticipantModel.chat_id == target_id,
                        ChatParticipantModel.user_id == current_user_id
                    ).first()

                if other_participant:
                    emp = main_db.query(Employee).filter(Employee.id == other_participant.user_id).first()
                    if emp:
                        full_name = f"{emp.last_name or ''} {emp.first_name or ''}".strip() or emp.name or f"User #{emp.id}"
                        return {
                            "chat_id": chat.id,
                            # id собеседника: без него клиент принимал номер
                            # чата за номер сотрудника и открывал чужую карточку
                            "other_user_id": emp.id,
                            "user_id": emp.id,
                            "has_chat": True,
                            "is_group": False,
                            "full_name": full_name,
                            "last_name": emp.last_name or "",
                            "first_name": emp.first_name or "",
                            "phone_number": emp.phone_number,
                            "email": emp.email,
                            "position": emp.position,
                            "department": emp.department,
                            "avatar_url": employee_avatar_url(main_db, emp.id)
                        }

        # 2. Если чат не найден или мы в нём не состоим, возможно это user_id
        emp = main_db.query(Employee).filter(Employee.id == target_id).first()
        if emp:
            full_name = f"{emp.last_name or ''} {emp.first_name or ''}".strip() or emp.name or f"User #{emp.id}"
            return {
                "chat_id": target_id,  # Оставляем поле для консистентности на фронтенде
                "other_user_id": emp.id,
                "user_id": emp.id,
                "has_chat": False,
                "is_group": False,
                "full_name": full_name,
                "last_name": emp.last_name or "",
                "first_name": emp.first_name or "",
                "phone_number": emp.phone_number,
                "email": emp.email,
                "position": emp.position,
                "department": emp.department,
                "avatar_url": employee_avatar_url(main_db, emp.id)
            }

        # 3. Резервный вариант: если фронтенд передал message_id (например, при клике на аватар в сообщении)
        msg = db.query(MessageModel).filter(MessageModel.id == target_id).first()
        if msg:
            emp = main_db.query(Employee).filter(Employee.id == msg.sender_id).first()
            if emp:
                full_name = f"{emp.last_name or ''} {emp.first_name or ''}".strip() or emp.name or f"User #{emp.id}"
                return {
                    "chat_id": msg.sender_id,
                    "is_group": False,
                    "full_name": full_name,
                    "last_name": emp.last_name or "",
                    "first_name": emp.first_name or "",
                    "phone_number": emp.phone_number,
                    "email": emp.email,
                    "position": emp.position,
                    "department": emp.department,
                    "avatar_url": employee_avatar_url(main_db, emp.id)
                }

        # 4. Если ничего не подошло
        raise HTTPException(status_code=404, detail="Чат или собеседник не найден")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка получения инфо-карточки: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/chat/group/details/{group_id}")
def get_group_details(
        group_id: str,
        db: Session = Depends(get_chat_db),
        main_db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    """
    Получение деталей сгруппированного сообщения (например, альбома).
    Клиент запрашивает это методом GET.
    """
    try:
        # Проверяем валидность group_id
        if not group_id or group_id == '':
            raise HTTPException(status_code=400, detail="Неверный group_id")

        # Ищем все сообщения с таким group_id
        messages = db.query(MessageModel).filter(
            MessageModel.group_id == group_id
        ).order_by(MessageModel.timestamp.asc()).all()

        if not messages:
            raise HTTPException(status_code=404, detail="Группа сообщений не найдена")
        for chat_id in sorted({m.chat_id for m in messages}):
            _assert_chat_member(db, chat_id, current_user_id)

        # Определяем родительское сообщение
        # 1. Ищем сообщение с parent_id == None (это родительское)
        parent_msg = next((m for m in messages if m.parent_id is None), None)

        # 2. Если не нашли, берем первое сообщение
        if not parent_msg:
            parent_msg = messages[0]

        # Получаем имя отправителя из основной БД
        sender_name = "Неизвестный"
        employee = main_db.query(Employee).filter(Employee.id == parent_msg.sender_id).first()
        if employee:
            sender_name = f"{employee.last_name or ''} {employee.first_name or ''}".strip()
            if not sender_name and employee.name:
                sender_name = employee.name

        # Собираем файлы из всех сообщений группы
        files = []
        for msg in messages:
            if msg.file_path:  # Только сообщения с файлами
                # Используем вашу структуру данных
                file_info = {
                    "id": msg.id,
                    "filepath": msg.file_path,
                    "filename": msg.file_name,
                    "size": msg.file_size,
                    "is_image": msg.is_image,
                    "url": f"/file_storage/{msg.file_path}" if msg.file_path else None
                }
                files.append(file_info)

        reactions, reactions_detailed = build_message_reactions(db, main_db, parent_msg.id)

        # Формируем итоговый ответ в соответствии с вашей структурой
        return {
            "id": parent_msg.id,
            "group_id": group_id,
            "parent_id": parent_msg.parent_id,
            "chat_id": parent_msg.chat_id,
            "sender_id": parent_msg.sender_id,
            "sender_name": sender_name,
            "text": parent_msg.text,
            "timestamp": parent_msg.timestamp.isoformat() if parent_msg.timestamp else None,
            "is_read": parent_msg.is_read,
            "is_edited": parent_msg.is_edited,
            "special": parent_msg.special,
            "metadata": parent_msg.metadata_json if parent_msg.metadata_json else {},
            "files": files,
            "reactions": reactions,
            "reactions_detailed": reactions_detailed,
            "is_grouped": True
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting group details: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Ошибка получения данных группы")


@router.post("/chat/group/{chat_id}/participants")
async def add_group_participants(
        chat_id: int,
        data: AddParticipants,
        current_user_id: int = Depends(get_current_user_id),
        db: Session = Depends(get_chat_db),
        main_db: Session = Depends(get_db)
):
    # Проверка прав (только админы и owner могут добавлять участников)
    admin = db.query(ChatParticipantModel).filter(
        ChatParticipantModel.chat_id == chat_id,
        ChatParticipantModel.user_id == current_user_id,
        ChatParticipantModel.role.in_(['admin', 'owner'])
    ).first()

    if not admin:
        raise HTTPException(status_code=403, detail="Нет прав для добавления участников")

    added_count = 0
    created_messages = []

    for uid in data.user_ids:
        exists = db.query(ChatParticipantModel).filter(
            ChatParticipantModel.chat_id == chat_id,
            ChatParticipantModel.user_id == uid
        ).first()

        if not exists:
            new_participant = ChatParticipantModel(
                chat_id=chat_id,
                user_id=uid,
                role='participant'  # Новые участники получают роль participant по умолчанию
            )
            db.add(new_participant)

            # Создаем специальное сообщение
            special_msg = await create_special_message(
                chat_id=chat_id,
                sender_id=current_user_id,
                special_type="member_add",
                metadata={"user_id": uid},
                main_db=main_db
            )
            db.add(special_msg)
            created_messages.append(special_msg)

            added_count += 1

    db.commit()

    for special_msg in created_messages:
        db.refresh(special_msg)
        await manager.broadcast_to_chat(chat_id, {
            "type": "special_message",
            "id": special_msg.id,
            "chat_id": chat_id,
            "sender_id": current_user_id,
            "sender_name": (special_msg.metadata_json or {}).get('actor_name', ''),
            "text": special_msg.text,
            "special": "member_add",
            "metadata": special_msg.metadata_json or {},
            "timestamp": special_msg.timestamp.isoformat()
        }, db)

    if added_count:
        await _broadcast_chats_updated(chat_id, db, main_db)
        await push_group_added(db, main_db, chat_id, current_user_id,
                               [m.metadata_json.get("user_id") for m in created_messages
                                if m.metadata_json and m.metadata_json.get("user_id")])

    return {"status": "success", "added": added_count}


@router.post("/chat/message/{message_id}/reaction")
async def update_message_reaction(
        message_id: int,
        reaction_data: ReactionUpdate,
        current_user_id: int = Depends(get_current_user_id),
        db: Session = Depends(get_chat_db),
        main_db: Session = Depends(get_db)
):
    """Добавление/удаление реакции на сообщение"""
    try:
        msg = db.query(MessageModel).filter(MessageModel.id == message_id).first()
        if not msg:
            raise HTTPException(status_code=404, detail="Сообщение не найдено")
        _assert_chat_member(db, msg.chat_id, current_user_id)

        # Ищем существующую реакцию пользователя
        existing_reaction = db.query(MessageReactionModel).filter(
            MessageReactionModel.message_id == message_id,
            MessageReactionModel.user_id == current_user_id
        ).first()

        if existing_reaction:
            if existing_reaction.reaction_type == reaction_data.reaction_type:
                # Удаляем реакцию (отмена)
                db.delete(existing_reaction)
            else:
                # Изменяем тип реакции
                existing_reaction.reaction_type = reaction_data.reaction_type
        else:
            # Создаем новую реакцию
            new_reaction = MessageReactionModel(
                message_id=message_id,
                user_id=current_user_id,
                reaction_type=reaction_data.reaction_type
            )
            db.add(new_reaction)

        db.commit()

        reactions_dict, reactions_detailed = build_message_reactions(db, main_db, message_id)

        # Отправляем уведомление через WebSocket
        await manager.broadcast_to_chat(msg.chat_id, {
            "type": "reaction",
            "message_id": message_id,
            "reactions": reactions_dict,
            "reactions_detailed": reactions_detailed,
            "chat_id": msg.chat_id,
            "user_id": current_user_id
        }, db)

        return {
            "status": "success",
            "reactions": reactions_dict,
            "reactions_detailed": reactions_detailed
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка обновления реакции: {str(e)}")


@router.post("/chat/group/{chat_id}/promote")
async def promote_group_participant(
        chat_id: int,
        role_data: RoleUpdate,
        db: Session = Depends(get_chat_db),
        current_user_id: int = Depends(get_current_user_id)
):
    """Назначение участника администратором группы"""
    try:
        # Проверяем права текущего пользователя
        current_participant = db.query(ChatParticipantModel).filter(
            ChatParticipantModel.chat_id == chat_id,
            ChatParticipantModel.user_id == current_user_id
        ).first()

        if not current_participant:
            raise HTTPException(status_code=403, detail="Вы не участник группы")

        # Только owner может назначать администраторов
        if current_participant.role != 'owner':
            raise HTTPException(status_code=403, detail="Только создатель группы может назначать администраторов")

        # Находим участника для повышения
        target_participant = db.query(ChatParticipantModel).filter(
            ChatParticipantModel.chat_id == chat_id,
            ChatParticipantModel.user_id == role_data.user_id
        ).first()

        if not target_participant:
            raise HTTPException(status_code=404, detail="Участник не найден")

        # Повышаем до администратора
        target_participant.role = 'admin'
        target_participant.is_admin = True
        db.commit()

        # Уведомляем через WebSocket
        await manager.broadcast_to_chat(chat_id, {
            "type": "group_role_changed",
            "chat_id": chat_id,
            "user_id": role_data.user_id,
            "role": 'admin'
        }, db)

        return {"status": "success", "message": f"Пользователь {role_data.user_id} назначен администратором"}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка назначения администратора: {str(e)}")


@router.post("/chat/group/{chat_id}/demote")
async def demote_group_participant(
        chat_id: int,
        role_data: RoleUpdate,
        db: Session = Depends(get_chat_db),
        current_user_id: int = Depends(get_current_user_id)
):
    """Снятие прав администратора"""
    try:
        # Проверяем права текущего пользователя
        current_participant = db.query(ChatParticipantModel).filter(
            ChatParticipantModel.chat_id == chat_id,
            ChatParticipantModel.user_id == current_user_id
        ).first()

        if not current_participant:
            raise HTTPException(status_code=403, detail="Вы не участник группы")

        # Только owner может снимать администраторов
        if current_participant.role != 'owner':
            raise HTTPException(status_code=403, detail="Только создатель группы может снимать администраторов")

        # Находим участника
        target_participant = db.query(ChatParticipantModel).filter(
            ChatParticipantModel.chat_id == chat_id,
            ChatParticipantModel.user_id == role_data.user_id
        ).first()

        if not target_participant:
            raise HTTPException(status_code=404, detail="Участник не найден")

        # Нельзя снять права у owner
        if target_participant.role == 'owner':
            raise HTTPException(status_code=400, detail="Нельзя изменить роль создателя группы")

        # Снимаем права администратора
        target_participant.role = 'participant'
        target_participant.is_admin = False
        db.commit()

        # Уведомляем через WebSocket
        await manager.broadcast_to_chat(chat_id, {
            "type": "group_role_changed",
            "chat_id": chat_id,
            "user_id": role_data.user_id,
            "role": 'participant'
        }, db)

        return {"status": "success", "message": f"Пользователь {role_data.user_id} снят с должности администратора"}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка снятия прав: {str(e)}")


@router.get("/chat/download/{file_path:path}")
def download_chat_file_by_path(
        file_path: str,
        request: Request,
        current_user_id: int = Depends(get_current_user_id_from_header_or_query),
        db: Session = Depends(get_chat_db),
        main_db: Session = Depends(get_db)
):
    """
    Скачивание файлов чата с автоматической распаковкой GZIP.
    """
    try:
        file_access.check_path(main_db, db, current_user_id, file_path, "chat/download/{path}")
        try:
            full_path = resolve_stored_path(file_path)
        except ValueError:
            raise HTTPException(status_code=400, detail="Некорректный путь к файлу")

        if not full_path.exists():
            raise HTTPException(status_code=404, detail="Файл не найден")

        # Определяем имя файла из пути
        filename = Path(file_path).name
        # Убираем .gz расширение если есть
        if filename.endswith('.gz'):
            original_filename = filename[:-3]
        else:
            original_filename = filename

        return get_decompressed_file_response(file_path, original_filename, download=True, request=request)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка скачивания файла чата: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка скачивания файла: {str(e)}")


@router.get("/chat/{chat_id}/media/summary")
def get_chat_media_summary(
        chat_id: int,
        db: Session = Depends(get_chat_db),
        current_user_id: int = Depends(get_current_user_id),
):
    """Количество вложений чата по категориям для карточки чата."""
    try:
        _assert_chat_member(db, chat_id, current_user_id)

        # Только имя файла и признак картинки — этого хватает для категории
        rows = db.query(MessageModel.file_name, MessageModel.is_image).filter(
            MessageModel.chat_id == chat_id,
            MessageModel.file_path.isnot(None),
            MessageModel.file_path != "",
        ).all()

        counts = {"photo": 0, "video": 0, "file": 0}
        for file_name, is_image in rows:
            counts[_media_category(file_name, is_image)] += 1

        counts["total"] = sum(v for k, v in counts.items() if k != "total")
        return counts

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка подсчёта медиатеки чата {chat_id}: {e}")
        raise HTTPException(status_code=500, detail="Ошибка получения списка вложений")


@router.get("/chat/{chat_id}/media")
def get_chat_media(
        chat_id: int,
        category: str = Query("photo", description="photo | video | file"),
        limit: int = Query(300, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        db: Session = Depends(get_chat_db),
        main_db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id),
):
    """
    Список вложений чата одной категории — новые сверху.

    Возвращаются только метаданные (имя, размер, время, ссылка);
    файлы с диска здесь не читаются.
    """
    try:
        category = (category or "photo").lower()
        if category not in ("photo", "video", "file"):
            raise HTTPException(status_code=400, detail="Неизвестная категория")

        _assert_chat_member(db, chat_id, current_user_id)

        messages = db.query(MessageModel).filter(
            MessageModel.chat_id == chat_id,
            MessageModel.file_path.isnot(None),
            MessageModel.file_path != "",
        ).order_by(desc(MessageModel.timestamp), desc(MessageModel.id)).all()

        selected = [m for m in messages if _media_category(m.file_name, m.is_image) == category]
        page = selected[offset:offset + limit]

        names = {}
        for m in page:
            if m.sender_id not in names:
                names[m.sender_id] = get_employee_full_name(main_db, m.sender_id)

        items = []
        for m in page:
            file_path = (m.file_path or "").replace("\\", "/")
            items.append({
                "id": m.id,
                "message_id": m.id,
                "chat_id": m.chat_id,
                "group_id": m.group_id,
                "category": category,
                "filename": m.file_name,
                "file_name": m.file_name,
                "filepath": m.file_path,
                "file_path": m.file_path,
                "size": m.file_size,
                "file_size": m.file_size,
                "is_image": bool(m.is_image) or category == "photo",
                "url": f"/file_storage/{file_path}" if file_path else None,
                "timestamp": m.timestamp.isoformat() if m.timestamp else None,
                "sender_id": m.sender_id,
                "sender_name": names.get(m.sender_id, ""),
            })

        return {"chat_id": chat_id, "category": category, "total": len(selected), "items": items}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка получения медиатеки чата {chat_id}: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Ошибка получения списка вложений")


@router.get("/chat/{chat_id}/search")
def search_chat_messages(
        chat_id: int,
        query: str = Query(..., description="Текст для поиска"),
        limit: int = Query(50, ge=1, le=200),
        db: Session = Depends(get_chat_db),
        current_user_id: int = Depends(get_current_user_id),
        main_db: Session = Depends(get_db)
):
    """
    Поиск сообщений в чате только по тексту сообщения.
    (Исключён поиск по названиям файлов)
    """
    try:
        # Проверка, является ли пользователь участником чата
        participant = db.query(ChatParticipantModel).filter(
            ChatParticipantModel.chat_id == chat_id,
            ChatParticipantModel.user_id == current_user_id
        ).first()

        if not participant:
            logger.warning(f"Пользователь {current_user_id} не является участником чата {chat_id}")
            raise HTTPException(
                status_code=403,
                detail="Вы не участник этого чата"
            )

        safe_query = query.replace('%', '\\%').replace('_', '\\_')

        # Поиск только по полю text (убрано условие по file_name)
        messages = db.query(MessageModel).filter(
            MessageModel.chat_id == chat_id,
            func.lower(MessageModel.text).like(f"%{safe_query.lower()}%")
        ).order_by(desc(MessageModel.timestamp)).limit(limit).all()

        result = []
        for msg in messages:
            # Имя отправителя
            sender_name = "Неизвестный"
            employee = main_db.query(Employee).filter(Employee.id == msg.sender_id).first()
            if employee:
                sender_name = f"{employee.last_name or ''} {employee.first_name or ''}".strip()
                if not sender_name and employee.name:
                    sender_name = employee.name
                elif not sender_name:
                    sender_name = UNKNOWN_EMPLOYEE_NAME

            msg_data = {
                "id": msg.id,
                "group_id": msg.group_id,
                "chat_id": msg.chat_id,
                "sender_id": msg.sender_id,
                "sender_name": sender_name,
                "text": msg.text,
                "file_path": msg.file_path,
                "file_name": msg.file_name,
                "timestamp": msg.timestamp.isoformat() if msg.timestamp else None,
                "is_edited": msg.is_edited,
                "is_image": msg.is_image,
                "file_size": msg.file_size
            }
            result.append(msg_data)

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка поиска в чате {chat_id}: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при поиске сообщений: {str(e)}"
        )


@router.post("/chat/grouped-message")
async def create_grouped_message(
        background_tasks: BackgroundTasks,
        chat_id: int = Form(...),
        files: List[UploadFile] = File(None),
        thumbs: List[UploadFile] = File(None),
        meta: Optional[str] = Form(None),
        captions: Optional[str] = Form(None),  # JSON список или строка
        reply_to_id: Optional[int] = Form(None),
        upload_id: Optional[str] = Form(None),
        edits: Optional[str] = Form(None),
        current_user_id: int = Depends(get_current_user_id),
        db: Session = Depends(get_chat_db),
        main_db: Session = Depends(get_db)
):
    """
    Гибридная версия (исправленная по образцу upload_chat_file):
    - Логика группировки: СТАРАЯ версия (Parent Message + Files Array).
    - Логика путей и сохранения: ИДЕНТИЧНА upload_chat_file (без лишних resolve).
    """
    from .. import intake as _intake
    # постороннему отказываем до приёма файлов: ничего не сохраняем зря
    if not db.query(ChatParticipantModel).filter(ChatParticipantModel.chat_id == chat_id,
                                                  ChatParticipantModel.user_id == current_user_id).first():
        raise HTTPException(status_code=403, detail="Вы не участник этого чата")
    files = await _intake.as_uploads(_intake.parse_items(files, thumbs, meta), current_user_id, main_db)
    try:
        return await _create_grouped_message(background_tasks, chat_id, files, captions, reply_to_id, upload_id,
                                             edits, current_user_id, db, main_db)
    finally:
        _intake.release_uploads(files)


async def _create_grouped_message(background_tasks, chat_id, files, captions, reply_to_id, upload_id, edits,
                                  current_user_id, db, main_db):
    from .. import upload_queue as queue
    try:
        # --- 1. Проверка прав ---
        participant = db.query(ChatParticipantModel).filter(
            ChatParticipantModel.chat_id == chat_id,
            ChatParticipantModel.user_id == current_user_id
        ).first()

        if not participant:
            raise HTTPException(status_code=403, detail="Вы не участник этого чата")

        # --- 2. Подготовка текста ---
        text_content = None
        if captions:
            try:
                parsed = json.loads(captions)
                if isinstance(parsed, list) and len(parsed) > 0:
                    text_content = parsed[0]
                elif isinstance(parsed, str):
                    text_content = parsed
            except json.JSONDecodeError:
                text_content = captions

        if text_content and not text_content.strip():
            text_content = None

        group_id = str(uuid.uuid4())
        created_messages = []
        timestamp = datetime.now()

        parent_message = MessageModel(
            chat_id=chat_id,
            sender_id=current_user_id,
            text=text_content,
            group_id=group_id,
            timestamp=timestamp,
            parent_id=reply_to_id,
            special="file_group" if files else None
        )
        db.add(parent_message)
        db.commit()
        db.refresh(parent_message)
        created_messages.append(parent_message)

        # --- 4. Сохранение файлов (Алгоритм 1-в-1 из upload_chat_file) ---

        # Используем MESSENGER_STORAGE_ROOT напрямую, как в рабочей функции
        chat_folder_name = f"chat_{chat_id}"
        chat_storage_path = MESSENGER_STORAGE_ROOT / chat_folder_name
        chat_storage_path.mkdir(parents=True, exist_ok=True)

        saved_files_data = []
        failed_files = []

        # Файлы идут через общую очередь обработки: одновременно сжимается
        # ограниченное число, остальные ждут. Ход виден клиенту по upload_id.
        from .. import upload_queue as queue
        queue.start(upload_id, [{"name": f.filename, "size": getattr(f, "size", 0) or 0,
                                 "kind": ("video" if f.filename.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.webm'))
                                          else "image" if media.is_image_name(f.filename) else "document")}
                                for f in files])
        edit_map = parse_edits(edits)

        for index, file in enumerate(files):
            file_msg = None
            try:
                filename_lower = file.filename.lower()
                is_video = filename_lower.endswith(('.mp4', '.mov', '.avi', '.mkv', '.webm'))
                is_optimizable_image = media.is_image_name(file.filename)
                file_ext = Path(file.filename).suffix.lower()

                # Приём кусками во временный файл — не держим весь файл в памяти
                queue.update(upload_id, index, "receiving", 5, "Приём файла")
                temp_input_path = chat_storage_path / f"temp_{uuid.uuid4()}{file_ext or '.bin'}"
                received = 0
                try:
                    with open(temp_input_path, "wb") as buffer:
                        while chunk := await file.read(1024 * 1024):
                            buffer.write(chunk)
                            received += len(chunk)
                except Exception as e:
                    logger.error(f"Upload aborted: {e}")
                    queue.update(upload_id, index, "error", None, "Обрыв")
                    raise HTTPException(status_code=400, detail="Upload aborted")
                queue.update(upload_id, index, "queued", 10)
                content_size = received

                if is_video:
                    base_name = os.path.splitext(file.filename)[0]
                    new_filename = f"{base_name}{media.video_suffix()}"
                    unique_filename = f"{uuid.uuid4()}{media.video_suffix()}"
                    final_file_path = chat_storage_path / unique_filename
                    try:
                        # ffmpeg сам сообщает, сколько секунд ролика готово —
                        # клиент видит настоящий процент, а не оценку по времени
                        def _tick(pct, _i=index):
                            queue.update(upload_id, _i, "processing", pct, "Перекодировка видео")

                        from ..intake import video_origin
                        await queue.run_processing(upload_id, index, process_video_sync,
                                                   str(temp_input_path), str(final_file_path),
                                                   edit_map.get(str(index)) or {}, _tick,
                                                   "чат: " + video_origin(getattr(file, "kov_meta", None) or {}),
                                                   note="Перекодировка видео")
                    except Exception as e:
                        logger.exception(f"Ошибка конвертации видео: {e}")
                        queue.update(upload_id, index, "error", None, "Ошибка перекодировки")
                        if final_file_path.exists():
                            try:
                                final_file_path.unlink()
                            except Exception:
                                logger.exception(f"Не удалось удалить битый файл: {final_file_path}")
                        raise HTTPException(
                            status_code=422,
                            detail=("Не удалось подготовить видео к воспроизведению. "
                                    "Проверьте, что на сервере установлен ffmpeg c кодеками "
                                    "libvpx-vp9/libopus.")
                        )
                    finally:
                        if os.path.exists(temp_input_path):
                            try:
                                os.remove(temp_input_path)
                            except Exception:
                                logger.exception(f"Не удалось удалить временный файл: {temp_input_path}")
                    file_path = final_file_path
                    await _video_poster(final_file_path)
                    file.filename = new_filename  # update for db record
                    content_size = final_file_path.stat().st_size if final_file_path.exists() else content_size
                elif is_optimizable_image:
                    # Фото пережимается (WebP), как и в одиночной загрузке, а не
                    # пакуется в gzip — раньше групповая отправка шла мимо сжатия
                    def _img(tmp=temp_input_path, ext=file_ext):
                        data = tmp.read_bytes()
                        return media.optimize_image(data, ext)
                    try:
                        optimized, new_ext = await queue.run_processing(upload_id, index, _img, note="Сжатие фото")
                    finally:
                        if temp_input_path.exists():
                            try:
                                temp_input_path.unlink()
                            except Exception as e:
                                logger.debug("Список чатов: время последнего сообщения не разобралось: %s", e)
                    unique_filename = f"{uuid.uuid4()}{new_ext}"
                    file_path = chat_storage_path / unique_filename
                    file_path.write_bytes(optimized)
                    thumb = media.make_image_thumbnail(optimized)
                    if thumb:
                        Path(str(file_path) + media.THUMB_SUFFIX).write_bytes(thumb)
                    if new_ext != file_ext:
                        file.filename = Path(file.filename).stem + new_ext
                    content_size = len(optimized)
                else:
                    def _doc(tmp=temp_input_path):
                        return gzip.compress(tmp.read_bytes(), compresslevel=GZIP_LEVEL)
                    try:
                        compressed_content = await queue.run_processing(upload_id, index, _doc, note="Архивация")
                    finally:
                        if temp_input_path.exists():
                            try:
                                temp_input_path.unlink()
                            except Exception as e:
                                logger.warning("Вложение чата: изображение не пережалось, кладём как есть: %s", e)
                    unique_filename = f"{uuid.uuid4()}{file_ext}"
                    file_path = chat_storage_path / unique_filename
                    gz_file_path = Path(f"{file_path}.gz")
                    with open(gz_file_path, "wb") as buffer:
                        buffer.write(compressed_content)
                queue.update(upload_id, index, "done", 100, "Готово")

                # Вычисляем относительный путь для БД точно так же, как в upload_chat_file
                full_relative_path = file_path.relative_to(FILE_STORAGE_ROOT)
                db_file_path = str(full_relative_path).replace('\\', '/')

                # реестр копий: повторная отправка этого фото или видео пройдёт мгновенно (раньше чат его не вёл)
                try:
                    from ..blobs import register_upload
                    if register_upload(main_db, file, file_path, db_file_path, current_user_id):
                        main_db.commit()
                except Exception:
                    main_db.rollback()
                    logger.warning("Вложение чата не записано в реестр копий", exc_info=True)

                # Тип файла
                ctype = getattr(file, "content_type", None) or mimetypes.guess_type(file.filename or "")[0] or ""
                is_image = ctype.startswith("image/")
                if not is_image:
                    is_image = file_ext in ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp']

                file_msg = MessageModel(
                    chat_id=chat_id,
                    sender_id=current_user_id,
                    text=None,
                    file_path=db_file_path,
                    file_name=file.filename,
                    file_size=content_size,
                    is_image=is_image,
                    timestamp=timestamp,
                    group_id=group_id,
                    parent_id=reply_to_id, # Как у родительского сообщения (ответ на цитируемое сообщение)
                    special="file_item"
                )

                db.add(file_msg)
                db.flush()
                db.refresh(file_msg)

                created_messages.append(file_msg)

                saved_files_data.append({
                    "id": file_msg.id,
                    "filename": file.filename,
                    "file_name": file.filename,
                    "filepath": db_file_path,
                    "file_path": db_file_path,
                    # content_size: размер готового файла. Раньше здесь стояло
                    # len(content) от старой версии — переменной уже нет, и
                    # NameError оставлял запись в БД, но не в ответе: в чате
                    # появлялся пустой пузырь, а файл всплывал после обновления
                    "size": content_size,
                    "file_size": content_size,
                    "is_image": is_image,
                    "url": f"/file_storage/{db_file_path}"
                })

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Ошибка сохранения файла {getattr(file, 'filename', '')}: {e}")
                logger.error(traceback.format_exc())
                queue.update(upload_id, index, "error", None, "Ошибка")
                failed_files.append(getattr(file, "filename", "") or f"файл {index + 1}")
                # Незавершённую запись не оставляем: иначе она попадёт в общий
                # commit и файл появится в чате только после перезагрузки
                try:
                    if file_msg is not None:
                        if getattr(file_msg, "id", None):
                            db.delete(file_msg)
                        else:
                            db.expunge(file_msg)
                except Exception:
                    logger.exception("Не удалось убрать незавершённую запись файла")
                continue

        # --- 5. Финализация ---
        if parent_message and len(saved_files_data) > 1:
            parent_message.special = "file_group"

        if parent_message and parent_message.text and not saved_files_data:
            parent_message.special = None

        # Ни одного файла и текста нет — сообщению в чате взяться не из чего.
        # Раньше такая отправка оставляла пустой пузырь без всякого объяснения.
        if not saved_files_data and not text_content:
            try:
                db.delete(parent_message)
                db.commit()
            except Exception:
                db.rollback()
                logger.exception("Не удалось убрать пустое сообщение группы")
            queue.finish(upload_id)
            raise HTTPException(
                status_code=422,
                detail="Не удалось сохранить файлы: " + (", ".join(failed_files[:3]) or "неизвестная ошибка"))

        db.commit()

        # Данные отправителя
        sender_name = "Неизвестный"
        employee = main_db.query(Employee).filter(Employee.id == current_user_id).first()
        if employee:
            sender_name = f"{employee.last_name or ''} {employee.first_name or ''}".strip() or employee.name or f"Сотрудник #{current_user_id}"

        reactions = {}

        # --- 6. Ответ клиенту ---
        main_msg = parent_message if parent_message else (created_messages[0] if created_messages else None)

        if not main_msg:
            return {"error": "Не удалось создать сообщение", "status": "failed"}

        response_data = {
            "id": main_msg.id,
            "chat_id": chat_id,
            "sender_id": current_user_id,
            "sender_name": sender_name,
            "sender_last_name": (employee.last_name or "") if employee else "",
            "sender_first_name": (employee.first_name or "") if employee else "",
            "sender_avatar_url": employee_avatar_url(main_db, current_user_id),
            "text": main_msg.text,
            "timestamp": main_msg.timestamp.isoformat(),
            "is_read": False,
            "is_edited": False,
            "special": main_msg.special,
            "group_id": group_id,
            "parent_id": main_msg.parent_id,
            "metadata": {"file_count": len(saved_files_data)} if saved_files_data else None,
            "files": saved_files_data,
            "failed": failed_files,
            "reactions": reactions,
            "reactions_detailed": []
        }

        # --- 7. WebSocket ---

        reply_to_message = None
        if reply_to_id:
            p_msg = db.query(MessageModel).filter(MessageModel.id == reply_to_id).first()
            if p_msg:
                p_emp = main_db.query(Employee).filter(Employee.id == p_msg.sender_id).first() if main_db else None
                p_sender = f"{p_emp.last_name or ''} {p_emp.first_name or ''}".strip() or p_emp.name if p_emp else 'Неизвестный'
                reply_to_message = {
                    'id': p_msg.id,
                    'sender_name': p_sender,
                    'text': p_msg.text if p_msg.text else ('Файл' if p_msg.file_path or p_msg.special == 'file_group' else '')
                }
                
        await manager.broadcast_to_chat(chat_id, {
            "type": "grouped_message",
            # id обязателен: без него клиент считает сообщение из сокета и то же
            # сообщение из истории разными и рисует файлы дважды
            "id": main_msg.id,
            "group_id": group_id,
            "parent_id": response_data["parent_id"],
            "chat_id": chat_id,
            "sender_id": current_user_id,
            "sender_name": sender_name,
            "sender_last_name": response_data.get("sender_last_name", ""),
            "sender_first_name": response_data.get("sender_first_name", ""),
            "sender_avatar_url": response_data.get("sender_avatar_url"),
            "text": response_data["text"],
            "timestamp": response_data["timestamp"],
            "special": main_msg.special,
            "metadata": response_data["metadata"],
            "files": saved_files_data,
            "file_count": len(saved_files_data),
            "reactions": {},
            "reactions_detailed": [],
            "reply_to_message": reply_to_message
        }, db)

        # Обновление списков чатов: одно на человека за 0,4 с, вне цикла событий
        participants = db.query(ChatParticipantModel.user_id).filter(
            ChatParticipantModel.chat_id == chat_id
        ).all()
        schedule_chat_lists([row.user_id for row in participants])

        await push_chat_event(db, main_db, chat_id, current_user_id, sender_name,
                              response_data.get("text"), len(saved_files_data))

        logger.info(f"Группированное сообщение создано: group_id={group_id}, файлов={len(saved_files_data)}"
                    + (f", не сохранено={len(failed_files)}" if failed_files else ""))
        queue.finish(upload_id)
        return response_data

    except HTTPException:
        # 403/422 должны дойти до клиента как есть: раньше они превращались в 500
        queue.finish(upload_id)
        raise
    except Exception as e:
        db.rollback()
        queue.finish(upload_id)
        logger.error(f"Critical error in create_grouped_message: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat/{chat_id}/read")
async def mark_chat_read(
        chat_id: int,
        db: Session = Depends(get_chat_db),
        current_user_id: int = Depends(get_current_user_id)
):
    participant = db.query(ChatParticipantModel).filter(
        ChatParticipantModel.chat_id == chat_id,
        ChatParticipantModel.user_id == current_user_id
    ).first()

    if not participant:
        raise HTTPException(status_code=404, detail="Participant not found")

    last_msg = db.query(MessageModel).filter(MessageModel.chat_id == chat_id).order_by(desc(MessageModel.id)).first()
    if last_msg:
        current = participant.last_read_message_id or 0
        if last_msg.id > current:
            participant.last_read_message_id = last_msg.id
            db.commit()
            # Сообщаем остальным: их сообщения до этого номера прочитаны.
            # По этому событию у отправителя появляется вторая галочка.
            try:
                await manager.broadcast_to_chat(chat_id, {
                    "type": "chat.read", "chat_id": chat_id,
                    "user_id": current_user_id, "upto": last_msg.id,
                }, db)
            except Exception as e:
                logger.debug("Отметка о прочтении не разослана: %s", e)

    return {"status": "success"}


@router.get("/chat/{chat_id}/read-state")
def chat_read_state(
        chat_id: int,
        db: Session = Depends(get_chat_db),
        current_user_id: int = Depends(get_current_user_id)
):
    """До какого сообщения чат прочитан другими участниками.

    По этому числу клиент рисует вторую галочку на своих сообщениях при
    открытии чата: живые события приходят потом, а начальное состояние
    неоткуда взять.
    """
    me = db.query(ChatParticipantModel).filter(
        ChatParticipantModel.chat_id == chat_id,
        ChatParticipantModel.user_id == current_user_id).first()
    if not me:
        raise HTTPException(status_code=404, detail="Participant not found")
    others = db.query(ChatParticipantModel).filter(
        ChatParticipantModel.chat_id == chat_id,
        ChatParticipantModel.user_id != current_user_id).all()
    # В группе достаточно, чтобы прочитал любой — берём наибольшее значение
    upto = max([p.last_read_message_id or 0 for p in others], default=0)
    return {"chat_id": chat_id, "upto": upto}


@router.post("/chat/message/{message_id}/forward")
async def forward_message(
        message_id: int,
        data: ForwardMessageRequest,
        db: Session = Depends(get_chat_db),
        main_db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    """
    Пересылка сообщения в другие чаты.

    Автор оригинала сохраняется в metadata_json['forwarded_from'] — отдельной
    колонки для этого не заводим, поэтому миграция БД не нужна. Клиент рисует
    по этим данным подпись «Переслано от: Фамилия Имя» над сообщением, причём
    ВСЕГДА, даже когда пересылают собственное сообщение.

    Сообщение с вложениями хранится как родительская запись плюс строка на
    каждый файл (special='file_item'), поэтому копируем всю структуру целиком.
    """
    if not data.chat_ids:
        raise HTTPException(status_code=400, detail="Не указаны чаты для пересылки")

    source_msg = db.query(MessageModel).filter(MessageModel.id == message_id).first()
    if not source_msg:
        raise HTTPException(status_code=404, detail="Сообщение не найдено")

    # Пересылать сервисные маркеры ("вышел из группы" и т.п.) бессмысленно
    if source_msg.special in ('avatar_change', 'member_add', 'member_remove',
                              'member_leave', 'group_rename', 'owner_transfer'):
        raise HTTPException(status_code=400, detail="Служебное сообщение нельзя переслать")

    # Право читать оригинал: пользователь должен быть участником исходного чата
    source_participant = db.query(ChatParticipantModel).filter(
        ChatParticipantModel.chat_id == source_msg.chat_id,
        ChatParticipantModel.user_id == current_user_id
    ).first()
    if not source_participant:
        raise HTTPException(status_code=403, detail="Вы не участник исходного чата")

    # Автор оригинала. Если сообщение уже пересланное — сохраняем ПЕРВОГО
    # автора, а не того, кто переслал: иначе цепочка пересылок теряет источник.
    source_meta = source_msg.metadata_json or {}
    origin = source_meta.get('forwarded_from')

    if not origin:
        author = main_db.query(Employee).filter(Employee.id == source_msg.sender_id).first()
        author_name = ""
        author_last = ""
        author_first = ""
        if author:
            author_last = author.last_name or ""
            author_first = author.first_name or ""
            author_name = f"{author_last} {author_first}".strip() or (author.name or "")
        if not author_name:
            author_name = f"Сотрудник #{source_msg.sender_id}"

        origin = {
            "sender_id": source_msg.sender_id,
            "sender_name": author_name,
            "last_name": author_last,
            "first_name": author_first,
            "message_id": source_msg.id,
            "chat_id": source_msg.chat_id,
            "timestamp": source_msg.timestamp.isoformat() if source_msg.timestamp else None,
        }

    # Файлы оригинала: и «одиночное» вложение, и дочерние file_item
    source_files = []
    if source_msg.file_path:
        source_files.append(source_msg)
    if source_msg.group_id and source_msg.special != 'file_item':   # один файл группы — только он, без соседей
        child_items = db.query(MessageModel).filter(
            MessageModel.group_id == source_msg.group_id,
            MessageModel.special == 'file_item'
        ).order_by(asc(MessageModel.id)).all()
        source_files.extend(child_items)
    # часть группы: только выбранные файлы, без подписи группы
    if data.file_ids:
        wanted = {int(x) for x in data.file_ids}
        source_files = [f for f in source_files if f.id in wanted]
        if not source_files:
            raise HTTPException(status_code=400, detail="Выбранные файлы не найдены")

    forwarded = []

    for target_chat_id in dict.fromkeys(data.chat_ids):   # убираем дубли, порядок сохраняем
        target_participant = db.query(ChatParticipantModel).filter(
            ChatParticipantModel.chat_id == target_chat_id,
            ChatParticipantModel.user_id == current_user_id
        ).first()
        if not target_participant:
            logger.warning(f"Пересылка в чат {target_chat_id}: пользователь {current_user_id} не участник")
            continue

        timestamp = datetime.now()
        new_group_id = str(uuid.uuid4()) if source_files else None

        parent_msg = MessageModel(
            chat_id=target_chat_id,
            sender_id=current_user_id,
            text=None if data.file_ids else source_msg.text,
            timestamp=timestamp,
            group_id=new_group_id,
            special="file_group" if source_files else None,
            metadata_json={"forwarded_from": origin},
        )

        # Одиночное вложение дублируем и в родителя — так же, как это делает
        # обычная отправка, иначе старые клиенты не увидят файл
        if len(source_files) == 1 and source_files[0].file_path:
            single = source_files[0]
            parent_msg.file_path = single.file_path
            parent_msg.file_name = single.file_name
            parent_msg.file_size = single.file_size
            parent_msg.is_image = single.is_image

        db.add(parent_msg)
        db.flush()

        saved_files = []
        for f in source_files:
            if not f.file_path:
                continue
            file_msg = MessageModel(
                chat_id=target_chat_id,
                sender_id=current_user_id,
                file_path=f.file_path,
                file_name=f.file_name,
                file_size=f.file_size,
                is_image=f.is_image,
                group_id=new_group_id,
                parent_id=parent_msg.id,
                special="file_item",
                timestamp=timestamp,
            )
            db.add(file_msg)
            db.flush()

            saved_files.append({
                "id": file_msg.id,
                "filename": file_msg.file_name,
                "file_name": file_msg.file_name,
                "filepath": file_msg.file_path,
                "file_path": file_msg.file_path,
                "size": file_msg.file_size,
                "file_size": file_msg.file_size,
                "is_image": file_msg.is_image,
                "url": "/file_storage/" + (file_msg.file_path or "").replace("\\", "/"),
            })

        db.commit()
        db.refresh(parent_msg)

        sender = main_db.query(Employee).filter(Employee.id == current_user_id).first()
        sender_last = (sender.last_name or "") if sender else ""
        sender_first = (sender.first_name or "") if sender else ""
        sender_name = f"{sender_last} {sender_first}".strip()
        if not sender_name and sender:
            sender_name = sender.name or ""
        if not sender_name:
            sender_name = f"Сотрудник #{current_user_id}"

        payload = {
            "type": "grouped_message" if saved_files else "message",
            "id": parent_msg.id,
            "chat_id": target_chat_id,
            "sender_id": current_user_id,
            "sender_name": sender_name,
            "sender_last_name": sender_last,
            "sender_first_name": sender_first,
            "sender_avatar_url": employee_avatar_url(main_db, current_user_id),
            "text": parent_msg.text,
            "timestamp": parent_msg.timestamp.isoformat(),
            "group_id": new_group_id,
            "special": parent_msg.special,
            "file_path": parent_msg.file_path,
            "file_name": parent_msg.file_name,
            "file_size": parent_msg.file_size,
            "files": saved_files,
            "metadata": parent_msg.metadata_json or {},
            "reactions": {},
            "reactions_detailed": [],
        }

        await manager.broadcast_to_chat(target_chat_id, payload, db)
        await _broadcast_chats_updated(target_chat_id, db, main_db)
        await push_chat_event(db, main_db, target_chat_id, current_user_id, sender_name,
                              parent_msg.text, len(saved_files))

        forwarded.append({"chat_id": target_chat_id, "message_id": parent_msg.id})

    if not forwarded:
        raise HTTPException(status_code=403, detail="Нет доступа ни к одному из выбранных чатов")

    return {"status": "success", "forwarded": forwarded, "count": len(forwarded)}
