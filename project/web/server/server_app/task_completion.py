# -*- coding: utf-8 -*-
"""Завершение задачи: заявка руководителя и подтверждение директора.

Раньше руководитель отдела отправлял задачу в архив сам, и увидеть это можно
было только по пропавшей строке в списке. Теперь завершение состоит из двух
шагов: руководитель просит закрыть задачу, директор подтверждает. Завершившим
в базе записывается тот директор, который подтвердил, — не тот, кто попросил.

Подтвердить можно двумя путями: кнопкой прямо в уведомлении и внутри самой
задачи. Оба ведут в одну и ту же точку, поэтому расхождений между ними нет.
Пока заявка висит, задача остаётся в работе и её видно всем как обычно.
"""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from server.database.models import (Employee, Notification, Task, TaskCompletionRequest)

from . import task_journal as journal
from .config import logger
from .core import (STATUS_DIRECTOR, employee_status, ensure_task_access, get_current_user_id,
                   get_db, is_management_status, user_is_department_head_for_task)
from .stage_leads import leads_the_stage
from .push import push_notification
from .realtime import manager

router = APIRouter()

SECTION = "task_complete"       # по этому разделу клиент рисует кнопки в уведомлении


class DecisionIn(BaseModel):
    comment: Optional[str] = None


def person_name(emp: Optional[Employee]) -> str:
    if not emp:
        return "неизвестно"
    return " ".join(x for x in [emp.last_name, emp.first_name] if x).strip() or (emp.name or "")


def directors(db: Session) -> List[Employee]:
    """Все директора. Администраторы в список не входят: подтверждение
    завершения — решение руководства предприятия, а не обслуживание системы."""
    return [e for e in db.query(Employee).all() if employee_status(e) == STATUS_DIRECTOR]


def can_decide(db: Session, user_id: int) -> bool:
    emp = db.query(Employee).filter(Employee.id == user_id).first()
    return bool(emp) and is_management_status(emp)


def can_request(db: Session, user_id: int, task: Task) -> bool:
    me = db.query(Employee).filter(Employee.id == user_id).first()
    if is_management_status(me) or user_is_department_head_for_task(db, user_id, task):
        return True
    stages = sorted(task.stage_details or [], key=lambda s: s.stage_number)
    return bool(stages) and leads_the_stage(db, user_id, stages[-1])


def pending_of(db: Session, task_id: int) -> Optional[TaskCompletionRequest]:
    return db.query(TaskCompletionRequest).filter(
        TaskCompletionRequest.task_id == task_id,
        TaskCompletionRequest.status == "pending").order_by(
        TaskCompletionRequest.id.desc()).first()


def request_out(db: Session, row: Optional[TaskCompletionRequest]) -> Optional[dict]:
    if not row:
        return None
    who = {e.id: e for e in db.query(Employee).filter(
        Employee.id.in_([i for i in (row.requested_by, row.decided_by) if i] or [0])).all()}
    return {
        "id": row.id, "task_id": row.task_id, "status": row.status,
        "requested_by": row.requested_by,
        "requested_by_name": person_name(who.get(row.requested_by)),
        "requested_at": row.requested_at.isoformat() if row.requested_at else None,
        "decided_by": row.decided_by,
        "decided_by_name": person_name(who.get(row.decided_by)) if row.decided_by else None,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "comment": row.comment or "",
    }


async def notify(db: Session, user_id: int, message: str, task_id: int,
                 section: str, needs_action: bool = False) -> None:
    """Постоянное уведомление плюс всплывающее.

    Всплывающее делаем действующим: в нём есть номер задачи и признак
    needs_action, поэтому клиент рисует в нём кнопки подтверждения, а нажатие
    на само уведомление открывает задачу.
    """
    if not user_id:
        return
    try:
        note = Notification(employee_id=user_id, message=message,
                            target_section=section, target_id=task_id,
                            created_at=datetime.now())
        db.add(note)
        db.commit()
        db.refresh(note)
    except Exception as e:
        db.rollback()
        logger.error("Завершение задачи: уведомление не создано: %s", e)
        return
    try:
        await manager.send_personal_message({
            "type": "notification.new", "notification_id": note.id, "message": message,
            "target_section": section, "target_id": task_id,
            "needs_action": needs_action}, user_id)
    except Exception as e:
        logger.debug("Завершение задачи: сокет %s: %s", user_id, e)
    try:
        await push_notification(user_id, message, section, task_id)
    except Exception as e:
        logger.debug("Завершение задачи: push %s: %s", user_id, e)


async def spread(db: Session, task_id: int, payload: dict) -> None:
    """Событие расходится и по составу задачи, и всем директорам: решение
    видят обе стороны сразу, без перезагрузки раздела."""
    try:
        await manager.broadcast_to_task_participants(task_id, payload, db)
    except Exception as e:
        logger.debug("Завершение задачи: рассылка по задаче: %s", e)
    for d in directors(db):
        try:
            await manager.send_personal_message(payload, d.id)
        except Exception as e:
            logger.debug("Завершение задачи: сокет директора %s: %s", d.id, e)


def load_task(db: Session, task_id: int) -> Task:
    task = db.query(Task).options(joinedload(Task.stage_details)).filter(
        Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return task


def archive(db: Session, task: Task, decided_by: int) -> None:
    """Собственно перенос в архив. Завершившим пишется подтвердивший."""
    task.archived_at = datetime.now()
    task.activity = "Архивная"
    task.who_archive = decided_by


# ── заявка ───────────────────────────────────────────────────────────────

@router.get("/tasks/{task_id}/completion")
def get_completion(task_id: int, db: Session = Depends(get_db),
                   current_user_id: int = Depends(get_current_user_id)):
    """Состояние завершения задачи: висит ли заявка и вправе ли я решать"""
    task = load_task(db, task_id)
    ensure_task_access(db, current_user_id, task)
    row = pending_of(db, task_id)
    if row is None:
        row = db.query(TaskCompletionRequest).filter(
            TaskCompletionRequest.task_id == task_id).order_by(
            TaskCompletionRequest.id.desc()).first()
    return {
        "request": request_out(db, row),
        "can_decide": can_decide(db, current_user_id),
        "can_request": can_request(db, current_user_id, task),
    }


@router.post("/tasks/{task_id}/completion", status_code=status.HTTP_201_CREATED)
async def request_completion(task_id: int, db: Session = Depends(get_db),
                             current_user_id: int = Depends(get_current_user_id)):
    """Просьба закрыть задачу. Уходит всем директорам на подтверждение."""
    task = load_task(db, task_id)
    ensure_task_access(db, current_user_id, task)
    me = db.query(Employee).filter(Employee.id == current_user_id).first()
    if not can_request(db, current_user_id, task):
        raise HTTPException(status_code=403,
                            detail="Запросить завершение может руководитель отдела задачи, "
                                   "ответственный за последний этап, директор или администратор")
    if task.archived_at:
        raise HTTPException(status_code=409, detail="Задача уже в архиве")
    if pending_of(db, task_id):
        raise HTTPException(status_code=409, detail="Заявка на завершение уже отправлена")

    row = TaskCompletionRequest(task_id=task_id, requested_by=current_user_id,
                                requested_at=datetime.now(), status="pending")
    db.add(row)
    db.flush()

    # Директор и администратор — сами подтверждающая сторона: заставлять их
    # ждать собственного подтверждения бессмысленно
    if is_management_status(me):
        row.status = "approved"
        row.decided_by = current_user_id
        row.decided_at = datetime.now()
        archive(db, task, current_user_id)
        journal.note(db, task_id, "task_completed",
                     "задача завершена (%s)" % person_name(me), current_user_id)
        db.commit()
        await spread(db, task_id, {"type": "task.archived", "task_id": task_id,
                                   "by": person_name(me), "sender_id": current_user_id})
        return {"status": "archived", "request": request_out(db, row)}

    journal.note(db, task_id, "task_completion",
                 "запрошено завершение (%s)" % person_name(me), current_user_id)
    db.commit()

    message = "«%s» просит завершить задачу «%s». Подтвердите завершение" % (
        person_name(me), (task.name or "без названия")[:120])
    for d in directors(db):
        await notify(db, d.id, message, task_id, SECTION, needs_action=True)
    await spread(db, task_id, {"type": "task.completion_requested", "task_id": task_id,
                               "by": person_name(me), "sender_id": current_user_id})
    return {"status": "pending", "request": request_out(db, row)}


@router.post("/tasks/{task_id}/completion/approve")
async def approve_completion(task_id: int, payload: DecisionIn = DecisionIn(),
                             db: Session = Depends(get_db),
                             current_user_id: int = Depends(get_current_user_id)):
    """Подтверждение директора: задача уходит в архив, завершившим пишется он"""
    if not can_decide(db, current_user_id):
        raise HTTPException(status_code=403, detail="Подтвердить завершение может директор")
    task = load_task(db, task_id)
    row = pending_of(db, task_id)
    if not row:
        raise HTTPException(status_code=409, detail="Заявки на завершение нет")

    me = db.query(Employee).filter(Employee.id == current_user_id).first()
    row.status = "approved"
    row.decided_by = current_user_id
    row.decided_at = datetime.now()
    row.comment = (payload.comment or "")[:500]
    archive(db, task, current_user_id)
    journal.note(db, task_id, "task_completed",
                 "завершение подтвердил %s" % person_name(me), current_user_id)
    db.commit()

    await notify(db, row.requested_by,
                 "%s подтвердил завершение задачи «%s»" % (person_name(me), task.name or ""),
                 task_id, "task_view")
    await spread(db, task_id, {"type": "task.archived", "task_id": task_id,
                               "by": person_name(me), "sender_id": current_user_id})
    return {"status": "archived", "request": request_out(db, row)}


@router.post("/tasks/{task_id}/completion/decline")
async def decline_completion(task_id: int, payload: DecisionIn = DecisionIn(),
                             db: Session = Depends(get_db),
                             current_user_id: int = Depends(get_current_user_id)):
    """Отказ: задача остаётся в работе, просивший узнаёт причину"""
    if not can_decide(db, current_user_id):
        raise HTTPException(status_code=403, detail="Решение по завершению принимает директор")
    task = load_task(db, task_id)
    row = pending_of(db, task_id)
    if not row:
        raise HTTPException(status_code=409, detail="Заявки на завершение нет")

    me = db.query(Employee).filter(Employee.id == current_user_id).first()
    row.status = "declined"
    row.decided_by = current_user_id
    row.decided_at = datetime.now()
    row.comment = (payload.comment or "")[:500]
    journal.note(db, task_id, "task_completion",
                 "завершение отклонил %s%s" % (person_name(me),
                                               (": " + row.comment) if row.comment else ""),
                 current_user_id)
    db.commit()

    tail = (". Причина: " + row.comment) if row.comment else ""
    await notify(db, row.requested_by,
                 "%s не подтвердил завершение задачи «%s»%s" % (person_name(me), task.name or "", tail),
                 task_id, "task_view")
    await spread(db, task_id, {"type": "task.completion_declined", "task_id": task_id,
                               "by": person_name(me), "comment": row.comment,
                               "sender_id": current_user_id})
    return {"status": "declined", "request": request_out(db, row)}
