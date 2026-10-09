# -*- coding: utf-8 -*-

import asyncio
import re
import os
from datetime import timedelta, datetime, date
from typing import List, Optional, Dict, Literal
from sqlalchemy import or_, and_, func, select
from fastapi import FastAPI, Depends, HTTPException, status, Query, Response, UploadFile, File, Form, WebSocket, \
    WebSocketDisconnect
from sqlalchemy.orm import Session
from sqlalchemy.orm import aliased, joinedload
from sqlalchemy import asc, case, desc, exists
from pathlib import Path
from server.database.init_database import SessionLocal
import shutil
import uuid

from server.database.models import *

from .chat_models import ChatParticipantModel, MessageModel, MessageReactionModel
from .config import FILE_STORAGE_ROOT, MESSENGER_STORAGE_ROOT, logger
from .employee_cleanup import UNKNOWN_EMPLOYEE_NAME
from .photos import employee_avatar_url, employee_ids_with_photo, employee_photo_url
from .realtime import manager


def cleanup_old_notifications():
    db = SessionLocal()
    try:
        two_weeks_ago = datetime.now() - timedelta(weeks=2)
        deleted_count = db.query(Notification).filter(
            Notification.created_at < two_weeks_ago
        ).delete(synchronize_session=False)
        db.commit()
        print(f"Удалено {deleted_count} старых уведомлений")
    except Exception as e:
        db.rollback()
        print(f"Ошибка при очистке старых уведомлений: {e}")
    finally:
        db.close()


TASK_SORT_DEFAULT_ACTIVE = "priority"
TASK_SORT_DEFAULT_ARCHIVED = "archived_desc"
TASK_SORT_ORDERS = {
    "id_asc": lambda: asc(Task.id), "id_desc": lambda: desc(Task.id),
    "deadline_asc": lambda: asc(Task.deadline), "deadline_desc": lambda: desc(Task.deadline),
    "archived_asc": lambda: asc(Task.archived_at), "archived_desc": lambda: desc(Task.archived_at),
}


def task_priority_order(user_id: int, today: Optional[date] = None):
    """Порядок «по важности» одним выражением ORDER BY, без лишних запросов.

    0 — текущий незавершённый этап у отдела пользователя, срок прошёл;
    1 — то же, срок сегодня; 2 — то же, остальные; 3 — чужие просроченные;
    4 — чужие со сроком сегодня; 5 — все остальные. Внутри группы — по сроку.
    Отдел берётся подзапросом, этап — EXISTS по индексу task_id: один проход базы.
    """
    today = today or date.today()
    dept = select(Employee.department).where(Employee.id == user_id).scalar_subquery()
    state_no = case(*[(Task.state == s, s.value) for s in TaskState], else_=0)
    cur = aliased(TaskStageDetails)
    mine = exists().where(and_(cur.task_id == Task.id, cur.stage_number == state_no,
                               cur.department_name == dept,
                               or_(cur.is_completed.is_(None), cur.is_completed == False)))  # noqa: E712
    group = case((mine, 0), else_=3) + case((Task.deadline < today, 0), (Task.deadline == today, 1), else_=2)
    return [group, Task.deadline.is_(None), asc(Task.deadline), asc(Task.id)]


def apply_user_task_filter(query, db: Session, user_id: int, is_archived: bool = False):
    user_filter = db.query(TaskFilter).filter_by(
        user_id=user_id,
        is_archived=is_archived
    ).first()

    if user_filter:
        if user_filter.stage_count is not None:
            query = query.filter(Task.total_stages == user_filter.stage_count)
        if user_filter.min_created_date:
            query = query.filter(Task.creation_date >= user_filter.min_created_date)
        if user_filter.max_created_date:
            query = query.filter(Task.creation_date <= user_filter.max_created_date)

    codes = [c.strip() for c in ((user_filter.sort_by if user_filter else None) or "").split(",") if c.strip()]
    orders = [TASK_SORT_ORDERS[c]() for c in codes if c in TASK_SORT_ORDERS]
    if orders:
        query = query.order_by(*orders)
    elif not is_archived:
        # сортировка не выбрана или выбрана «по важности» — порядок по умолчанию для работающих задач
        query = query.order_by(*task_priority_order(user_id))
    return query


def normalize_spans(raw_spans, text_len: int):
    """
    Привести spans к корректному виду:
    - отрезать по границам [0, text_len]
    - отбросить пустые
    - отсортировать по start
    - объединить соседние spans одного автора (a.end == b.start and same author)
    Возвращает список объектов/ORM-инстансов или простых dict-структур.
    """
    spans = []
    for s in raw_spans:
        # Защита: если поля невалидны - приводим
        try:
            st = int(s.start)
            en = int(s.end)
            author = s.author_id
        except Exception:
            continue

        # clip to bounds
        st = max(0, min(st, text_len))
        en = max(0, min(en, text_len))
        if st >= en:
            continue
        spans.append({"start": st, "end": en, "author_id": author, "orm": s})

    # sort
    spans.sort(key=lambda x: x["start"])

    # merge adjacent spans with same author
    merged = []
    for sp in spans:
        if not merged:
            merged.append(sp)
            continue
        prev = merged[-1]
        if prev["end"] >= sp["start"]:  # overlap or adjacency
            # new end is max(prev.end, sp.end)
            # If overlapping authors differ, we keep prev then try to adjust (we'll prefer earlier span)
            if prev["author_id"] == sp["author_id"]:
                prev["end"] = max(prev["end"], sp["end"])
            else:
                # If overlapping authors differ, trim the later span's start to prev.end (so no overlap)
                if sp["end"] > prev["end"]:
                    sp["start"] = prev["end"]
                    if sp["start"] < sp["end"]:
                        merged.append(sp)
                # otherwise (later fully covered) skip sp
        else:
            merged.append(sp)

    # produce final list without orm wrappers
    result = [{"start": m["start"], "end": m["end"], "author_id": m["author_id"]} for m in merged]
    return result


def prepare_stage_response(stage):
    text = stage.text_description or ""
    text_len = len(text)

    raw_spans = getattr(stage, "text_spans", []) or []
    normalized = normalize_spans(raw_spans, text_len)

    # собрать авторов (запрос не нужен, если в joinedload были authors)
    spans_out = []
    for s in normalized:
        # найдём ORM-объект-пара соответствия в raw_spans (если он там есть)
        orm_match = None
        for r in raw_spans:
            try:
                r_start = getattr(r, "start", None) if not isinstance(r, dict) else r.get("start")
                r_end = getattr(r, "end", None) if not isinstance(r, dict) else r.get("end")
                r_author = getattr(r, "author_id", None) if not isinstance(r, dict) else (
                        r.get("author_id") or r.get("author_id"))
            except Exception:
                r_start = r_end = r_author = None

            if r_start == s["start"] and r_end == s["end"] and r_author == s["author_id"]:
                orm_match = r
                break

        author_data = None
        if orm_match is not None and getattr(orm_match, "author", None):
            a = orm_match.author
            author_data = {
                "id": a.id,
                "first_name": a.first_name or "",
                "last_name": a.last_name or "",
                "middle_name": a.middle_name or "",
                "position": a.position or "",
                "department": a.department or "",
                "email": a.email or ""
            }
        else:
            # author_id пуст только у фрагментов удалённого сотрудника: по id
            # его уже не найти, поэтому подписываем явно. Если id есть, имена
            # оставляем пустыми — клиент дотянет их по своему кэшу сотрудников.
            author_data = {
                "id": s["author_id"],
                "first_name": "",
                "last_name": "" if s["author_id"] else UNKNOWN_EMPLOYEE_NAME,
                "middle_name": "",
                "position": "",
                "department": "",
                "email": ""
            }

        # Получаем id span'а — из ORM, либо из dict, либо fallback -1
        span_id = None
        if orm_match is not None:
            # orm_match может быть ORM-объект или dict
            if not isinstance(orm_match, dict):
                span_id = getattr(orm_match, "id", None)
            else:
                span_id = orm_match.get("id", None)

        if span_id is None:
            # попытка найти id в normalized/raw_spans (на всякий случай)
            # если ничего не найдено — ставим -1 (поле обязано присутствовать)
            span_id = -1

        spans_out.append({
            "id": int(span_id) if span_id is not None else -1,
            "start": s["start"],
            "end": s["end"],
            "author_id": s["author_id"],
            "author": author_data
        })

    participants = []
    for p in getattr(stage, "participants", []) or []:
        participants.append({
            "id": p.id,
            "first_name": p.first_name or "",
            "last_name": p.last_name or "",
            "department": p.department or ""
        })
    # uvicorn server:app --reload
    files = []
    for f in getattr(stage, "files", []) or []:
        uploader = getattr(f, "uploaded_by", None)
        files.append({
            "id": f.id,
            "filename": f.filename,
            "filepath": f.filepath,
            "size": f.size,
            "extension": f.extension,
            "created_at": getattr(f, "created_at", None),
            "uploaded_by_id": getattr(f, "uploaded_by_id", None),
            "uploaded_by": ({
                "id": uploader.id,
                "last_name": uploader.last_name or "",
                "first_name": uploader.first_name or "",
                "middle_name": uploader.middle_name or "",
                "department": uploader.department or "",
            } if uploader is not None else None),
        })

    res = {
        "id": stage.id,
        "stage_number": stage.stage_number,
        "text_description": stage.text_description,
        "is_completed": stage.is_completed,
        "completion_timestamp": stage.completion_timestamp,
        "completed_by_employee_id": stage.completed_by_employee_id,
        "department_name": stage.department_name,
        "participants": participants,
        "files": files,
        "spans": spans_out
    }
    return res


def build_reactions_map(db_chat: Session, main_db: Session, message_ids) -> Dict[int, Dict]:
    """
    Реакции для набора сообщений: {message_id: {"reactions", "reactions_detailed"}}.

    reactions          — старый формат {user_id: тип}, оставлен для совместимости;
    reactions_detailed — кто именно поставил реакцию, с именем и фотографией:
                         по этим данным клиент рисует лицо автора рядом с эмодзи.
    """
    ids = set()
    for value in message_ids or []:
        try:
            ids.add(int(value))
        except (TypeError, ValueError):
            continue

    result = {mid: {"reactions": {}, "reactions_detailed": []} for mid in ids}
    if not ids:
        return result

    try:
        rows = db_chat.query(MessageReactionModel).filter(
            MessageReactionModel.message_id.in_(ids)
        ).all()
    except Exception as e:
        logger.warning(f"Не удалось получить реакции: {e}")
        return result

    if not rows:
        return result

    user_ids = {r.user_id for r in rows if r.user_id is not None}
    employees = {}
    photo_ids = set()
    if user_ids and main_db is not None:
        try:
            employees = {
                e.id: e for e in main_db.query(Employee).filter(Employee.id.in_(user_ids)).all()
            }
            photo_ids = employee_ids_with_photo(main_db, user_ids)
        except Exception as e:
            logger.warning(f"Не удалось получить авторов реакций: {e}")

    for r in rows:
        entry = result.setdefault(r.message_id, {"reactions": {}, "reactions_detailed": []})
        entry["reactions"][str(r.user_id)] = r.reaction_type

        emp = employees.get(r.user_id)
        last_name = (emp.last_name or "") if emp else ""
        first_name = (emp.first_name or "") if emp else ""
        name = f"{last_name} {first_name}".strip()
        if not name:
            name = (emp.name if emp and emp.name else f"Сотрудник #{r.user_id}")

        entry["reactions_detailed"].append({
            "user_id": r.user_id,
            "reaction_type": r.reaction_type,
            "name": name,
            "last_name": last_name,
            "first_name": first_name,
            "avatar_url": employee_photo_url(r.user_id) if r.user_id in photo_ids else None,
        })

    return result


def build_message_reactions(db_chat: Session, main_db: Session, message_id):
    """Реакции одного сообщения: (словарь совместимости, подробный список)."""
    data = build_reactions_map(db_chat, main_db, [message_id]).get(
        message_id, {"reactions": {}, "reactions_detailed": []}
    )
    return data["reactions"], data["reactions_detailed"]


def parse_timestamp_safe(time_str):
    """
    Надежный парсер времени. Пытается угадать формат, чтобы не вернуть ошибку.
    Возвращает объект datetime или None.
    """
    if not time_str:
        return None

    # 1. Если уже число или объект
    if isinstance(time_str, (int, float)):
        return datetime.fromtimestamp(time_str)
    if isinstance(time_str, datetime):
        return time_str

    # 2. Если строка
    if isinstance(time_str, str):
        # Очищаем от лишнего
        clean = time_str.replace('Z', '').strip()

        # Список форматов, которые будем пробовать по очереди
        formats = [
            "%Y-%m-%dT%H:%M:%S.%f",  # ISO с микросекундами (2023-10-10T10:00:00.123)
            "%Y-%m-%dT%H:%M:%S",  # ISO без микросекунд (2023-10-10T10:00:00)
            "%Y-%m-%d %H:%M:%S.%f",  # SQL с пробелом и мсек (2023-10-10 10:00:00.123)
            "%Y-%m-%d %H:%M:%S",  # SQL с пробелом (2023-10-10 10:00:00)
            "%Y-%m-%d"  # Только дата
        ]

        for fmt in formats:
            try:
                return datetime.strptime(clean, fmt)
            except ValueError:
                continue

    return None


def _count_unread(db_chat: Session, chat_id: int, user_id: int, last_read_message_id):
    """
    Считает непрочитанные сообщения чата для конкретного пользователя.

    Два фильтра, которых здесь раньше не было:

    1. sender_id != user_id — собственные сообщения непрочитанными не бывают.
       Раньше их не исключали, а указатель last_read_message_id двигался
       только при открытии чата, поэтому после отправки своего сообщения оно
       оставалось "непрочитанным" и всплывало при повторном заходе.

    2. special != 'file_item' — сообщение с вложениями хранится как одна
       родительская запись плюс по строке на каждый файл. Без фильтра
       сообщение с пятью файлами давало счётчик 6.
    """
    return db_chat.query(MessageModel).filter(
        MessageModel.chat_id == chat_id,
        MessageModel.id > (last_read_message_id or 0),
        MessageModel.sender_id != user_id,
        or_(MessageModel.special.is_(None), MessageModel.special != 'file_item')
    ).count()


# Пометки стикера и GIF: в тексте сообщения лежит служебная строка вида
# [[sticker:cat/happy]] или [[gif:https://…]].
_STICKER_MARK = re.compile(r"^\[\[sticker:([a-z]+)/([a-z]+)\]\]$")
_GIF_MARK = re.compile(r"^\[\[gif:(https://[^\s\]]+)\]\]$")


def message_preview(text: str, limit: int = 30) -> str:
    """Строка последнего сообщения для списка чатов.

    Стикер и GIF подписываем словом. Раньше пометка резалась по длине вместе
    со всем текстом, и у GIF адрес не помещался: клиент переставал узнавать
    пометку и показывал обрубок «[[gif:https://media…». Поэтому вид сообщения
    определяем здесь, до обрезки.
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    if _STICKER_MARK.match(raw):
        return "Стикер"
    if _GIF_MARK.match(raw):
        return "GIF"
    return raw[:limit] + "..." if len(raw) > limit else raw


def _last_sender_label(db_main, last_msg, viewer_id):
    """Короткая подпись автора последнего сообщения для списка чатов."""
    if not last_msg or not getattr(last_msg, "sender_id", None):
        return ""
    if int(last_msg.sender_id) == int(viewer_id):
        return "Вы"
    try:
        emp = db_main.query(Employee).filter(Employee.id == last_msg.sender_id).first()
    except Exception:
        emp = None
    if not emp:
        return ""
    last = (emp.last_name or "").strip()
    first = (emp.first_name or "").strip()
    if last and first:
        return f"{last} {first[0]}."
    return last or first or (emp.name or "")


def _chats_for_user_sync(user_id: int, db_chat: Session, db_main: Session):
    """Список чатов пользователя за 5–6 запросов на все чаты сразу, а не по нескольку на каждый чат."""
    participations = db_chat.query(ChatParticipantModel).filter(
        ChatParticipantModel.user_id == user_id
    ).options(joinedload(ChatParticipantModel.chat)).all()
    rows = [p for p in participations if p.chat]
    if not rows:
        return []
    chat_ids = [p.chat.id for p in rows]

    last_ids = db_chat.query(MessageModel.chat_id, func.max(MessageModel.id).label("mid")).filter(
        MessageModel.chat_id.in_(chat_ids)).group_by(MessageModel.chat_id).subquery()
    last_by_chat = {m.chat_id: m for m in db_chat.query(MessageModel).join(
        last_ids, MessageModel.id == last_ids.c.mid).all()}

    private_ids = [p.chat.id for p in rows if not p.chat.is_group]
    other_by_chat = {}
    if private_ids:
        for other in db_chat.query(ChatParticipantModel).filter(
                ChatParticipantModel.chat_id.in_(private_ids),
                ChatParticipantModel.user_id != user_id).order_by(ChatParticipantModel.id).all():
            other_by_chat.setdefault(other.chat_id, other)

    unread_by_chat = {}
    conditions = [and_(MessageModel.chat_id == p.chat.id, MessageModel.id > (p.last_read_message_id or 0))
                  for p in rows if p.chat.id in last_by_chat]
    if conditions:
        for chat_id, count in db_chat.query(MessageModel.chat_id, func.count(MessageModel.id)).filter(
                or_(*conditions),
                MessageModel.sender_id != user_id,
                or_(MessageModel.special.is_(None), MessageModel.special != 'file_item')
        ).group_by(MessageModel.chat_id).all():
            unread_by_chat[chat_id] = count

    people_ids = {o.user_id for o in other_by_chat.values()}
    people_ids.update(m.sender_id for m in last_by_chat.values() if m.sender_id and int(m.sender_id) != int(user_id))
    people = {e.id: e for e in db_main.query(Employee).filter(Employee.id.in_(people_ids)).all()} if people_ids else {}
    with_photo = employee_ids_with_photo(db_main, [o.user_id for o in other_by_chat.values() if o.user_id in people])

    result = []
    for p in rows:
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
                    chat_name = f"Сотрудник #{other_p.user_id}"
            else:
                chat_name = "Избранное"
        sender = ""
        if last_msg and last_msg.sender_id:
            if int(last_msg.sender_id) == int(user_id):
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
            "last_message": message_preview(last_msg.text if last_msg else ""),
            "last_message_sender": sender,
            "last_message_time": last_msg.timestamp.strftime("%H:%M") if last_msg else "",
            "timestamp": last_msg.timestamp.timestamp() if last_msg else chat.created_at.timestamp(),
            "role": p.role,
            "unread_count": unread_by_chat.get(chat.id, 0) if last_msg else 0,
        })
    result.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
    return result


async def get_chats_for_user(user_id: int, db_chat: Session, db_main: Session):
    """Список чатов пользователя. Вызывается через await во всех местах кода."""
    try:
        return _chats_for_user_sync(user_id, db_chat, db_main)
    except Exception as e:
        logger.error(f"Ошибка получения чатов для пользователя {user_id}: {e}")
        return []


# ── Обновление списков чатов: склеиваем частые изменения и считаем вне цикла событий ──
LIST_DELAY = 0.4
_pending_lists: Dict[int, "asyncio.Task"] = {}


def _chats_in_thread(user_id: int):
    from server.database.init_database_messenger import SessionLocalChat
    db_chat, db_main = SessionLocalChat(), SessionLocal()
    try:
        return _chats_for_user_sync(user_id, db_chat, db_main)
    finally:
        db_chat.close()
        db_main.close()


async def _send_chat_list_later(user_id: int):
    await asyncio.sleep(LIST_DELAY)
    _pending_lists.pop(user_id, None)
    try:
        from starlette.concurrency import run_in_threadpool
        chats = await run_in_threadpool(_chats_in_thread, user_id)
        await manager.send_personal_message({"type": "chats_updated", "chats": chats}, user_id)
    except Exception as e:
        logger.error(f"Не удалось обновить список чатов для пользователя {user_id}: {e}")


def schedule_chat_lists(user_ids) -> None:
    """Обновить списки чатов этих людей: одно обновление на человека за LIST_DELAY секунд."""
    loop = asyncio.get_running_loop()
    for uid in {int(u) for u in user_ids if u}:
        task = _pending_lists.get(uid)
        if task is None or task.done():
            _pending_lists[uid] = loop.create_task(_send_chat_list_later(uid))


async def _broadcast_chats_updated(chat_id: int, db_chat: Session, db_main: Session,
                                   extra_user_ids: Optional[List[int]] = None):
    """
    Рассылает актуальный список чатов участникам чата.
    extra_user_ids — те, кого уже нет среди участников (например, удалённый
    из группы), но кому список тоже надо обновить.
    """
    try:
        user_ids = {p.user_id for p in db_chat.query(ChatParticipantModel).filter(
            ChatParticipantModel.chat_id == chat_id
        ).all()}
        if extra_user_ids:
            user_ids.update(uid for uid in extra_user_ids if uid)

        schedule_chat_lists(user_ids)
    except Exception as e:
        logger.error(f"Ошибка рассылки chats_updated для чата {chat_id}: {e}")


def _pick_new_group_owner(db: Session, chat_id: int, leaving_user_id: int):
    """
    Выбирает, кому передать группу, когда её покидает владелец.

    Приоритет: самый давний администратор, иначе самый давний участник.
    Возвращает ChatParticipantModel или None, если участников больше нет.
    """
    candidates = db.query(ChatParticipantModel).filter(
        ChatParticipantModel.chat_id == chat_id,
        ChatParticipantModel.user_id != leaving_user_id
    ).all()

    if not candidates:
        return None

    def sort_key(p):
        # admin вперёд, дальше — по дате вступления (самые ранние первыми)
        return (0 if p.role == 'admin' else 1, p.joined_at or datetime.min)

    return sorted(candidates, key=sort_key)[0]


def _purge_chat_files(db: Session, chat_id: int) -> None:
    """Стирает файлы чата вместе с записями file_blobs.

    Пересылка ссылается на тот же файл, поэтому файлы, пересланные
    в другие чаты, остаются на месте.
    """
    from .storage import delete_stored_file, storage_relative
    from . import blobs
    folder = MESSENGER_STORAGE_ROOT / f"chat_{chat_id}"
    head = f"messenger/chat_{chat_id}/"
    try:
        rows = db.query(MessageModel.file_path).filter(
            MessageModel.chat_id != chat_id,
            MessageModel.file_path.contains(f"chat_{chat_id}", autoescape=True)).all()
    except Exception:
        logger.exception("Чат %s: не удалось проверить пересланные файлы, файлы оставлены", chat_id)
        return
    keep, kept = set(), set()
    for (fp,) in rows:
        key = blobs._blob_key(fp)
        if key.startswith(head):
            base = key[:-3] if key.lower().endswith(".gz") else key
            kept.add(base)
            keep.update((base, base + ".gz", base + ".thumb.jpg"))
    if not kept:
        if folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)
        blobs.forget(prefixes=[head])
        return
    for f in sorted(folder.rglob("*"), reverse=True):  # файлы раньше своих папок
        if f.is_file():
            try:
                key = storage_relative(f).as_posix()
            except ValueError:
                continue
            if key not in keep:
                delete_stored_file(key)
        elif f.is_dir() and not any(f.iterdir()):
            f.rmdir()
    logger.info("Чат %s удалён; файлов, пересланных в другие чаты, оставлено: %d", chat_id, len(kept))


def _purge_group_chat(db: Session, chat: "ChatModel"):
    """
    Полностью удаляет чат: файлы, сообщения, реакции, участников.

    Вынесено отдельно, потому что нужно и при удалении группы владельцем,
    и когда группу покидает последний участник.
    """
    chat_id = chat.id

    _purge_chat_files(db, chat_id)  # пересланные в другие чаты файлы остаются

    avatar_folder = MESSENGER_STORAGE_ROOT / "avatars" / f"chat_{chat_id}"
    if avatar_folder.exists() and avatar_folder.is_dir():
        try:
            shutil.rmtree(avatar_folder, ignore_errors=True)
        except Exception as e:
            logger.error(f"Ошибка удаления папки аватара чата {chat_id}: {e}")

    messages = db.query(MessageModel).filter(MessageModel.chat_id == chat_id).all()
    for msg in messages:
        db.query(MessageReactionModel).filter(
            MessageReactionModel.message_id == msg.id
        ).delete()

    db.query(MessageModel).filter(MessageModel.chat_id == chat_id).delete()
    db.query(ChatParticipantModel).filter(
        ChatParticipantModel.chat_id == chat_id
    ).delete()
    db.delete(chat)


async def save_compressed_chat_file(chat_id: int, original_filename: str, compressed_content: bytes) -> tuple[str, int]:
    """
    Сохраняет сжатый файл чата.
    """
    chat_folder_name = f"chat_{chat_id}"
    chat_storage_path = MESSENGER_STORAGE_ROOT / chat_folder_name
    chat_storage_path.mkdir(parents=True, exist_ok=True)

    file_ext = Path(original_filename).suffix.lower()
    unique_filename = f"{uuid.uuid4()}{file_ext}.gz"
    file_path = chat_storage_path / unique_filename

    with open(file_path, "wb") as buffer:
        buffer.write(compressed_content)

    # Возвращаем путь относительно FILE_STORAGE_ROOT
    full_relative_path = file_path.relative_to(FILE_STORAGE_ROOT)
    return str(full_relative_path).replace(os.sep, '/'), len(compressed_content)


MEDIA_IMAGE_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'}


MEDIA_VIDEO_EXT = {'.mp4', '.mov', '.avi', '.mkv', '.webm'}


def _media_category(file_name: Optional[str], is_image: Optional[bool]) -> str:
    """Категория файла для медиатеки: photo / video / file."""
    ext = Path(file_name or "").suffix.lower()
    if ext in MEDIA_VIDEO_EXT:
        return "video"
    if is_image or ext in MEDIA_IMAGE_EXT:
        return "photo"
    return "file"


def _assert_chat_member(db: Session, chat_id: int, user_id: int) -> None:
    """Медиатека доступна только участнику чата."""
    member = db.query(ChatParticipantModel).filter(
        ChatParticipantModel.chat_id == chat_id,
        ChatParticipantModel.user_id == user_id,
    ).first()
    if not member:
        raise HTTPException(status_code=403, detail="Нет доступа к этому чату")


async def get_chat_history_direct(chat_id: int, limit: int = 50, offset: int = 0, db: Session = None,
                                  main_db: Session = None):
    """Прямой запрос истории сообщений для WebSocket"""
    try:
        if offset == 0:
            messages = db.query(MessageModel).filter(
                MessageModel.chat_id == chat_id
            ).order_by(desc(MessageModel.id)).limit(limit).all()
        else:
            subquery = db.query(MessageModel.id).filter(
                MessageModel.chat_id == chat_id
            ).order_by(desc(MessageModel.id)).offset(offset).limit(limit).subquery()

            messages = db.query(MessageModel).filter(
                MessageModel.id.in_(subquery)
            ).order_by(desc(MessageModel.id)).all()

        result = []
        for msg in messages:

            sender_name = "Неизвестный"
            if main_db:
                try:
                    employee = main_db.query(Employee).filter(Employee.id == msg.sender_id).first()
                    if employee:
                        sender_name = f"{employee.last_name or ''} {employee.first_name or ''}".strip()
                        if not sender_name and employee.name:
                            sender_name = employee.name
                except Exception as e:
                    logger.error(f"Ошибка получения имени отправителя {msg.sender_id}: {e}")
                    sender_name = f"Сотрудник #{msg.sender_id}"

            reactions_query = db.query(MessageReactionModel).filter(
                MessageReactionModel.message_id == msg.id
            ).all()

            reactions_dict = {}
            for r in reactions_query:
                reactions_dict[str(r.user_id)] = r.reaction_type

            files = []
            if msg.file_path:
                files.append({
                    "id": msg.id,
                    "filepath": msg.file_path,
                    "filename": msg.file_name,
                    "size": msg.file_size,
                    "is_image": msg.is_image,
                    "url": f"/file_storage/{msg.file_path}" if msg.file_path else None
                })

            msg_data = {
                "id": msg.id,
                "chat_id": msg.chat_id,
                "sender_id": msg.sender_id,
                "sender_name": sender_name,
                "sender_last_name": (employee.last_name or "") if employee else "",
                "sender_first_name": (employee.first_name or "") if employee else "",
                "text": msg.text,
                "file_path": msg.file_path,
                "file_name": msg.file_name,
                "file_size": msg.file_size,
                "is_image": msg.is_image,
                "timestamp": msg.timestamp.isoformat() if msg.timestamp else None,
                "is_read": msg.is_read,
                "is_edited": msg.is_edited,
                "special": msg.special,
                "group_id": msg.group_id,
                "parent_id": msg.parent_id,
                "files": files,
                "reactions": reactions_dict,
                "is_grouped": bool(msg.group_id) and msg.group_id not in ['', '0', 'null']
            }
            result.append(msg_data)

        return result
    except Exception as e:
        logger.error(f"Error in get_chat_history_direct: {e}")
        return []


async def generate_group_id() -> str:
    """Генерация уникального ID для группы сообщений"""
    return str(uuid.uuid4())


def get_employee_full_name(main_db: Session, user_id: int) -> str:
    """Возвращает 'Фамилия Имя' сотрудника для текстов сервисных сообщений.

    Если сотрудник удалён, запись остаётся, а автор показывается как
    «Неизвестный» — так же во всех остальных разделах.
    """
    if not main_db or not user_id:
        return UNKNOWN_EMPLOYEE_NAME
    try:
        emp = main_db.query(Employee).filter(Employee.id == user_id).first()
        if not emp:
            return UNKNOWN_EMPLOYEE_NAME
        full_name = f"{emp.last_name or ''} {emp.first_name or ''}".strip()
        return full_name or (emp.name or UNKNOWN_EMPLOYEE_NAME)
    except Exception as e:
        logger.error(f"Не удалось получить имя сотрудника {user_id}: {e}")
        return UNKNOWN_EMPLOYEE_NAME


async def create_special_message(chat_id: int, sender_id: int, special_type: str,
                                 metadata: Dict = None, main_db: Session = None) -> MessageModel:
    """
    Создание сервисного сообщения-маркера (как в Telegram).

    Текст собирается здесь и сохраняется в БД, чтобы при перезагрузке истории
    маркер выглядел так же, как в момент события.

    metadata может содержать:
      user_id   — id участника, к которому относится действие
      user_name — его ФИО (если уже известно)
      new_name  — новое название группы для group_rename
    """
    metadata = dict(metadata or {})

    # Кто выполнил действие
    actor_name = metadata.get('actor_name') or get_employee_full_name(main_db, sender_id)
    if not actor_name:
        actor_name = f"Пользователь {sender_id}"
    metadata['actor_name'] = actor_name

    # Над кем выполнено действие
    target_name = metadata.get('user_name')
    if not target_name:
        target_name = get_employee_full_name(main_db, metadata.get('user_id'))
    if target_name:
        metadata['user_name'] = target_name

    if special_type == "group_rename":
        message_text = f"{actor_name} переименовал группу в «{metadata.get('new_name', '')}»"
    elif special_type == "avatar_change":
        message_text = f"{actor_name} сменил аватар группы"
    elif special_type == "member_add":
        message_text = f"{target_name or actor_name} был добавлен в группу"
    elif special_type == "member_remove":
        message_text = f"{actor_name} удалил из группы {target_name}".strip()
    elif special_type == "member_leave":
        message_text = f"{actor_name} вышел из группы"
    elif special_type == "owner_transfer":
        message_text = f"{target_name or 'Участник'} стал владельцем группы"
    else:
        message_text = ""

    return MessageModel(
        chat_id=chat_id,
        sender_id=sender_id,
        text=message_text,
        special=special_type,
        metadata_json=metadata if metadata else None
    )


async def update_chats_list_for_participants(chat_id: int, db_chat: Session, db_main: Session):
    """Обновляет список чатов для всех участников"""
    participants = db_chat.query(ChatParticipantModel).filter(
        ChatParticipantModel.chat_id == chat_id
    ).all()

    for participant in participants:
        updated_chats = await get_chats_for_user(participant.user_id, db_chat, db_main)
        await manager.send_personal_message({
            "type": "chats_updated",
            "chats": updated_chats
        }, participant.user_id)


# ── Web Push для событий чата ────────────────────────────────────────────────

async def push_chat_event(db_chat: Session, db_main, chat_id: int, sender_id: int,
                          sender_name: str, text: Optional[str], files_count: int = 0) -> None:
    """Push всем участникам чата, кроме автора. Любая ошибка глушится: доставка
    сообщения по WebSocket не должна зависеть от push-сервисов."""
    try:
        from .push import any_push_available, push_chat_message
        from .chat_models import ChatModel as _Chat
        if not any_push_available():
            return
        chat = db_chat.query(_Chat).filter(_Chat.id == chat_id).first()
        if not chat:
            return
        recipients = [p.user_id for p in db_chat.query(ChatParticipantModel).filter(
            ChatParticipantModel.chat_id == chat_id).all()]
        # отправка push не задерживает доставку сообщения: идёт в фоне
        _background(push_chat_message(chat_id, sender_id, sender_name or "Сотрудник",
                                      chat.name, bool(chat.is_group), recipients, text, files_count))
    except Exception as e:
        logger.debug(f"push_chat_event: {e}")


_background_tasks = set()


def _background(coro) -> None:
    task = asyncio.get_running_loop().create_task(coro)
    _background_tasks.add(task)

    def done(t):
        _background_tasks.discard(t)
        if not t.cancelled() and t.exception():
            logger.debug(f"фоновая отправка push: {t.exception()}")
    task.add_done_callback(done)


async def push_group_added(db_chat: Session, db_main, chat_id: int, actor_id: int,
                           added_user_ids) -> None:
    try:
        from .push import any_push_available, push_added_to_group
        from .chat_models import ChatModel as _Chat
        if not any_push_available() or not added_user_ids:
            return
        chat = db_chat.query(_Chat).filter(_Chat.id == chat_id).first()
        actor_name = get_employee_full_name(db_main, actor_id) if db_main is not None else "Сотрудник"
        await push_added_to_group(chat_id, chat.name if chat else None, actor_name, list(added_user_ids))
    except Exception as e:
        logger.debug(f"push_group_added: {e}")
