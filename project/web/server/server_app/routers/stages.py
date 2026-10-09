# -*- coding: utf-8 -*-

from datetime import timedelta, datetime, date
from typing import List, Optional, Dict, Literal
from sqlalchemy import or_, and_, func, select
from fastapi import FastAPI, Depends, HTTPException, status, Query, Response, UploadFile, File, Form, WebSocket, \
    WebSocketDisconnect
from sqlalchemy.orm import Session
from sqlalchemy.orm import joinedload

from server.database.models import *

from fastapi import APIRouter

from .. import stage_leads as leads
from .. import task_journal as journal
from ..core import (ensure_stage_edit, ensure_task_access, get_current_user_id, get_db,
                    is_admin_user, user_can_create_tasks)
from ..employee_cleanup import UNKNOWN_EMPLOYEE_NAME
from ..helpers import normalize_spans, prepare_stage_response
from ..config import logger
from ..realtime import manager
from ..schemas import SpanCreate, SpanDelete, SpanOut, TaskStageDetailsCreate, TaskStageDetailsFullOut, TaskStageDetailsOut, TaskStageDetailsUpdate
from ..storage import (purge_quarantined_files, quarantine_files_for_deletion,
                       restore_quarantined_files)


router = APIRouter()


def _task_and_stage(db: Session, task_id: int, stage_id: int):
    """Этап вместе с его задачей. Раньше здесь не было ни одной проверки прав:
    любой авторизованный мог переписать описание, завершить этап или сменить
    его состав в чужой задаче."""
    stage = db.query(TaskStageDetails).filter(
        TaskStageDetails.id == stage_id, TaskStageDetails.task_id == task_id).first()
    if not stage:
        raise HTTPException(status_code=404, detail=f"Этап с ID {stage_id} не найден")
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Задача этапа не найдена")
    return task, stage


async def notify_stage_roster(db: Session, task, stage, added, removed, actor_id: int) -> None:
    """Уведомления о добавлении в задачу и выводе из неё.

    Постоянное уведомление ложится в раздел, всплывающее приходит сокетом.
    Всплывающее делаем действующим: в нём есть номер задачи и этапа, поэтому
    нажатие открывает именно тот этап, куда человека добавили.
    """
    if not added and not removed:
        return
    title = (task.name or "задача")[:120]
    stage_no = getattr(stage, "stage_number", None) or getattr(stage, "id", None)
    pairs = [(added, "Вас добавили в задачу «%s», этап %s" % (title, stage_no)),
             (removed, "Вас вывели из этапа %s задачи «%s»" % (stage_no, title))]
    for ids, message in pairs:
        for user_id in ids:
            if not user_id or user_id == actor_id:
                continue
            try:
                note = Notification(employee_id=user_id, message=message,
                                    target_section="task_view", target_id=task.id,
                                    created_at=datetime.now())
                db.add(note)
                db.commit()
                db.refresh(note)
            except Exception as e:
                db.rollback()
                logger.error("Этап: уведомление не создано: %s", e)
                continue
            try:
                await manager.send_personal_message({
                    "type": "notification.new", "notification_id": note.id,
                    "message": message, "target_section": "task_view",
                    "target_id": task.id, "stage_id": getattr(stage, "id", None),
                    "stage_number": stage_no}, user_id)
            except Exception as e:
                logger.debug("Этап: сокет %s: %s", user_id, e)

    # Состав задачи поменялся — обновляем списки у всех, кто её открыл
    try:
        await manager.broadcast_to_task_participants(task.id, {
            "type": "task.participants_updated", "task_id": task.id,
            "stage_id": getattr(stage, "id", None), "sender_id": actor_id,
            "added": list(added), "removed": list(removed)}, db)
    except Exception as e:
        logger.debug("Этап: рассылка состава: %s", e)


async def _broadcast_description(db: Session, task_id: int, stage_id: int, sender_id: int):
    """Описание изменилось — рассылаем текст и авторство всем по задаче.

    Раньше рассылку делал только PUT этапа, а точечные операции со spans
    молчали: правка из настольной версии не долетала до открытых окон.
    """
    try:
        stage = db.query(TaskStageDetails).options(
            joinedload(TaskStageDetails.text_spans).joinedload(TaskStageTextSpan.author)
        ).filter(TaskStageDetails.id == stage_id).first()
        if not stage:
            return
        spans = []
        for sp in (stage.text_spans or []):
            author = sp.author
            spans.append({
                "id": sp.id, "start": sp.start, "end": sp.end, "author_id": sp.author_id,
                "author": ({"id": author.id, "last_name": author.last_name,
                            "first_name": author.first_name, "middle_name": author.middle_name}
                           if author else None),
            })
        await manager.broadcast_to_task_participants(task_id, {
            "type": "stage.description_updated",
            "task_id": task_id, "stage_id": stage_id,
            "stage_number": stage.stage_number, "sender_id": sender_id,
            "description": stage.text_description or "", "spans": spans,
        }, db)
    except Exception:
        logger.exception("Не удалось разослать изменение описания этапа")


def _task_of_stage(db: Session, stage_id: int):
    stage = db.query(TaskStageDetails).filter(TaskStageDetails.id == stage_id).first()
    if not stage:
        raise HTTPException(status_code=404, detail="Этап не найден")
    task = db.query(Task).filter(Task.id == stage.task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Задача этапа не найдена")
    return task, stage


@router.put("/tasks/{task_id}/stages/{stage_id}", response_model=TaskStageDetailsOut)
async def update_stage_description(
        stage_data: TaskStageDetailsUpdate,
        task_id: int,
        stage_id: int,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    db_task, db_stage = _task_and_stage(db, task_id, stage_id)
    ensure_stage_edit(db, current_user_id, db_task, db_stage)
    try:
        # 1. обновляем текст
        db_stage.text_description = stage_data.text_description

        # Нормализуем входные spans
        text_len = len(db_stage.text_description or "")
        normalized = normalize_spans(stage_data.spans, text_len)

        # 2. удаляем старые spans
        db.query(TaskStageTextSpan).filter(
            TaskStageTextSpan.stage_detail_id == stage_id
        ).delete()

        # 3. создаём новые spans из нормализованных
        for s in normalized:
            db.add(TaskStageTextSpan(
                stage_detail_id=stage_id,
                author_id=s["author_id"],
                start=s["start"],
                end=s["end"]
            ))

        db.commit()

        # Перезагружаем db_stage с joinedload для spans и authors
        db_stage = db.query(TaskStageDetails).options(
            joinedload(TaskStageDetails.text_spans).joinedload(TaskStageTextSpan.author)
        ).filter(
            TaskStageDetails.id == stage_id
        ).first()

        # Подготавливаем spans в формате как в GET (с id, author etc.)
        raw_spans = getattr(db_stage, "text_spans", []) or []
        normalized_out = normalize_spans(raw_spans, text_len)
        spans_out = []
        for s in normalized_out:
            # найдём ORM-объект-пара соответствия в raw_spans (если он там есть)
            orm_match = None
            for r in raw_spans:
                try:
                    if isinstance(r, dict):
                        r_start = int(r.get("start", 0))
                        r_end = int(r.get("end", 0))
                        r_author = r.get("author_id")
                    else:
                        r_start = int(getattr(r, "start", 0))
                        r_end = int(getattr(r, "end", 0))
                        r_author = getattr(r, "author_id", None)
                    if r_start == s["start"] and r_end == s["end"] and r_author == s["author_id"]:
                        orm_match = r
                        break
                except Exception:
                    continue
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
                # Пустой author_id — фрагмент удалённого сотрудника
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
                if not isinstance(orm_match, dict):
                    span_id = getattr(orm_match, "id", None)
                else:
                    span_id = orm_match.get("id", None)
            if span_id is None:
                span_id = -1
            spans_out.append({
                "id": int(span_id) if span_id is not None else -1,
                "start": s["start"],
                "end": s["end"],
                "author_id": s["author_id"],
                "author": author_data
            })

        # 4. websocket-уведомление
        try:
            await manager.broadcast_to_task_participants(task_id, {
                "type": "stage.description_updated",
                "task_id": task_id,
                "stage_id": stage_id,
                "stage_number": db_stage.stage_number,
                "sender_id": current_user_id,
                "description": db_stage.text_description,
                "spans": spans_out
            }, db)
        except Exception as e:
            logger.debug("Этапы: рассылка описания не ушла: %s", e)

        db.refresh(db_stage)
        return db_stage

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при обновлении этапа: {str(e)}"
        )


@router.get("/tasks/{task_id}/stages/{stage_id}", response_model=TaskStageDetailsFullOut)
def get_task_stage(
        task_id: int,
        stage_id: int,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    task, _ = _task_and_stage(db, task_id, stage_id)
    ensure_task_access(db, current_user_id, task)
    try:
        # Загрузим stage вместе с participants, files и text_spans->author
        stage = db.query(TaskStageDetails).options(
            joinedload(TaskStageDetails.participants),
            joinedload(TaskStageDetails.files),
            joinedload(TaskStageDetails.text_spans).joinedload(TaskStageTextSpan.author),
            joinedload(TaskStageDetails.completed_by_employee)
        ).filter(
            TaskStageDetails.id == stage_id,
            TaskStageDetails.task_id == task_id
        ).first()

        if not stage:
            raise HTTPException(
                status_code=404,
                detail=f"Этап с ID {stage_id} не найден в задаче {task_id}"
            )

        # подготовим ответ (нормализация spans внутри)
        response = prepare_stage_response(stage)
        # Кто отвечает за этап и вправе ли смотрящий им распоряжаться —
        # клиент по этому решает, показывать ли правку состава и продление
        named = leads.lead_ids(db, stage_id)
        response["leads"] = sorted(named)
        response["i_lead"] = leads.leads_the_stage(db, current_user_id, stage)

        return response

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при получении этапа: {str(e)}"
        )


@router.post("/tasks/{task_id}/stages/{stage_id}/complete", response_model=TaskStageDetailsOut)
async def complete_task_stage(
        task_id: int,
        stage_id: int,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    _task, _stage = _task_and_stage(db, task_id, stage_id)
    ensure_stage_edit(db, current_user_id, _task, _stage)
    try:
        db_stage = db.query(TaskStageDetails).filter(
            TaskStageDetails.id == stage_id,
            TaskStageDetails.task_id == task_id
        ).first()

        if not db_stage:
            raise HTTPException(
                status_code=404,
                detail=f"Этап с ID {stage_id} не найден в задаче {task_id}"
            )

        if db_stage.is_completed:
            raise HTTPException(
                status_code=400,
                detail="Этап уже завершен"
            )

        journal.note(db, task_id, "stage_done",
                     "этап %s (%s)" % (db_stage.stage_number, db_stage.department_name or "—"),
                     current_user_id)

        task = db.query(Task).filter(Task.id == task_id).first()
        next_stage = db_stage.stage_number + 1
        previous_stage = db_stage.stage_number - 1

        has_next_stage = db.query(TaskStageDetails).filter(
            TaskStageDetails.task_id == task_id,
            TaskStageDetails.stage_number == next_stage
        ).count() > 0

        pre_stage = db.query(TaskStageDetails).filter(
            TaskStageDetails.task_id == task_id,
            TaskStageDetails.stage_number == previous_stage,
            TaskStageDetails.is_completed == 0
        ).count() > 0

        if pre_stage:
            raise HTTPException(
                status_code=400,
                detail="Предыдущий этап не завершен"
            )

        if has_next_stage:
            task.state = TaskState(next_stage)

        db_stage.is_completed = True
        db_stage.completed_by_employee_id = current_user_id
        db_stage.completion_timestamp = datetime.now()

        db.commit()
        db.refresh(db_stage)

        db_stage = db.query(TaskStageDetails).options(
            joinedload(TaskStageDetails.participants),
            joinedload(TaskStageDetails.files)
        ).filter(TaskStageDetails.id == stage_id).first()
        try:
            await manager.broadcast_to_task_participants(task_id, {
                "type": "stage.completed",
                "task_id": task_id,
                "stage_id": stage_id,
                "stage_number": db_stage.stage_number,
                "is_completed": True,
                "completed_by_employee_id": db_stage.completed_by_employee_id,
                "completion_timestamp": (db_stage.completion_timestamp.isoformat() if db_stage.completion_timestamp else None),
                "sender_id": current_user_id
            }, db)
        except Exception as e:
            logger.debug("Этапы: рассылка о завершении не ушла: %s", e)
        return db_stage
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при завершении этапа: {str(e)}"
        )


@router.post("/tasks/{task_id}/stages/{stage_id}/resume", response_model=TaskStageDetailsOut)
async def resume_task_stage(
        task_id: int,
        stage_id: int,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    _task, _stage = _task_and_stage(db, task_id, stage_id)
    ensure_stage_edit(db, current_user_id, _task, _stage)
    try:
        db_stage = db.query(TaskStageDetails).options(
            joinedload(TaskStageDetails.task).joinedload(Task.assigned_employees),
            joinedload(TaskStageDetails.task).joinedload(Task.responsible_persons)).filter(
            TaskStageDetails.id == stage_id,
            TaskStageDetails.task_id == task_id
        ).first()

        if not db_stage:
            raise HTTPException(
                status_code=404,
                detail=f"Этап с ID {stage_id} не найден в задаче {task_id}"
            )

        if not db_stage.is_completed:
            raise HTTPException(
                status_code=400,
                detail="Этап не завершен"
            )

        # возобновление — в журнал задачи, как завершение (раньше строки не было)
        journal.note(db, task_id, "stage_resumed",
                     "этап %s (%s)" % (db_stage.stage_number, db_stage.department_name or "—"),
                     current_user_id)

        db_stage.is_completed = False
        db_stage.completed_by_employee_id = None
        db_stage.completion_timestamp = None

        task = db.query(Task).filter(Task.id == task_id).first()
        pre_stage = db_stage.stage_number

        task.state = TaskState(pre_stage)

        db.commit()
        db.refresh(db_stage)

        db_stage = db.query(TaskStageDetails).options(
            joinedload(TaskStageDetails.participants),
            joinedload(TaskStageDetails.files)
        ).filter(TaskStageDetails.id == stage_id).first()
        try:
            await manager.broadcast_to_task_participants(task_id, {
                "type": "stage.resumed",
                "task_id": task_id,
                "stage_id": stage_id,
                "stage_number": db_stage.stage_number,
                "is_completed": False,
                "completed_by_employee_id": None,
                "completion_timestamp": None,
                "sender_id": current_user_id
            }, db)
        except Exception as e:
            logger.debug("Этапы: рассылка о возобновлении не ушла: %s", e)
        return db_stage
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при возобновлении этапа: {str(e)}"
        )


@router.get("/stage-leads")
def stage_lead_candidates(department: str, db: Session = Depends(get_db),
                          current_user_id: int = Depends(get_current_user_id)):
    """Кого можно назначить ответственным за этап выбранного отдела.

    Обычно это руководители отдела. Если руководителей нет — весь отдел:
    иначе в «Договорном отделе» продлить задачу было бы не на кого. Признак
    heads_exist нужен клиенту, чтобы честно подписать список.
    """
    people = leads.lead_candidates(db, department)
    heads = leads.department_heads(db, department)
    return {
        "department": department,
        "heads_exist": bool(heads),
        "people": [{"id": e.id, "last_name": e.last_name, "first_name": e.first_name,
                    "position": e.position or "",
                    "is_head": e.id in {h.id for h in heads}} for e in people],
    }


@router.post("/tasks/{task_id}/stages", response_model=TaskStageDetailsOut)
async def create_task_stage(
        stage_data: TaskStageDetailsCreate,
        task_id: int,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"Задача с ID {task_id} не найдена")
    current = db.query(TaskStageDetails).filter(
        TaskStageDetails.task_id == task_id).order_by(
        TaskStageDetails.stage_number.desc()).first()
    # Продлевать вправе тот, кто вообще заводит задачи, и ответственный за
    # текущий этап: в отделе без руководителей (договорной) иначе некому
    if not (user_can_create_tasks(db, current_user_id)
            or (current is not None and leads.leads_the_stage(db, current_user_id, current))):
        raise HTTPException(status_code=403,
                            detail="Продлить задачу может ответственный за текущий этап, "
                                   "руководитель отдела, директор или администратор")
    ensure_task_access(db, current_user_id, task)
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise HTTPException(
                status_code=404,
                detail=f"Задача с ID {task_id} не найдена"
            )

        max_stage_number = db.query(func.max(TaskStageDetails.stage_number)).filter(
            TaskStageDetails.task_id == task_id
        ).scalar() or 0

        new_stage = TaskStageDetails(
            task_id=task_id,
            stage_number=max_stage_number + 1,
            department_name=stage_data.department_name,
            text_description=stage_data.text_description,
            is_completed=False
        )
        # Состав нового этапа определяют названные ответственные: одного
        # хватает, чтобы этап был только его; никого не назвали — идёт весь
        # отдел, а ответственными становятся его руководители
        roster = leads.resolve_roster(db, stage_data.department_name, stage_data.responsible_ids)
        employees_in_new_department = db.query(Employee).filter(
            Employee.id.in_(sorted(roster["participants"]) or [0])).all()
        new_stage.participants = employees_in_new_department

        task.state = TaskState(new_stage.stage_number)

        db.add(new_stage)
        db.flush()
        leads.set_leads(db, new_stage.id, roster["leads"])
        journal.note(db, task_id, "stage_lead",
                     "этап %s — %s" % (new_stage.stage_number,
                                       journal.names(db, sorted(roster["leads"]))),
                     current_user_id)
        journal.note(db, task_id, "stage_added",
                     "этап %s — %s" % (new_stage.stage_number, stage_data.department_name or "—"),
                     current_user_id)
        # Люди нового этапа сразу становятся участниками задачи
        journal.sync_participants(db, task_id, current_user_id)
        db.commit()
        db.refresh(new_stage)

        await notify_stage_roster(db, task, new_stage,
                                  [e.id for e in employees_in_new_department], [],
                                  current_user_id)
        return new_stage
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при создании этапа: {str(e)}"
        )


@router.get("/tasks/{task_id}/stages", response_model=List[TaskStageDetailsOut])
def get_task_stages(
        task_id: int,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"Задача с ID {task_id} не найдена")
    ensure_task_access(db, current_user_id, task)
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise HTTPException(
                status_code=404,
                detail=f"Задача с ID {task_id} не найдена"
            )

        stages = db.query(TaskStageDetails).options(
            joinedload(TaskStageDetails.participants),
            joinedload(TaskStageDetails.files)
        ).filter(
            TaskStageDetails.task_id == task_id
        ).order_by(TaskStageDetails.stage_number).all()

        return stages
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при получении этапов задачи: {str(e)}"
        )


@router.delete("/tasks/{task_id}/stages/{stage_id}")
async def delete_task_stage(
        task_id: int,
        stage_id: int,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id),
        is_admin: bool = Depends(is_admin_user)
):
    if not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Только администратор может удалять этапы"
        )

    quarantine = []
    try:
        db_stage = db.query(TaskStageDetails).filter(
            TaskStageDetails.id == stage_id,
            TaskStageDetails.task_id == task_id
        ).first()

        if not db_stage:
            raise HTTPException(
                status_code=404,
                detail=f"Этап с ID {stage_id} не найден в задаче {task_id}"
            )

        stage_files = db.query(TaskStageFile).filter(
            TaskStageFile.stage_detail_id == stage_id
        ).all()
        quarantine = quarantine_files_for_deletion([
            file.filepath for file in stage_files if file.filepath
        ])
        for stage_file in stage_files:
            db.delete(stage_file)

        db.execute(task_stage_participants.delete().where(
            task_stage_participants.c.stage_detail_id == stage_id
        ))
        db.query(TaskStageTextSpan).filter(
            TaskStageTextSpan.stage_detail_id == stage_id
        ).delete(synchronize_session=False)

        db.delete(db_stage)
        db.commit()
        purge_quarantined_files(quarantine)

        return {"status": "success", "message": "Этап успешно удален"}
    except HTTPException:
        db.rollback()
        restore_quarantined_files(quarantine)
        raise
    except Exception as e:
        db.rollback()
        restore_quarantined_files(quarantine)
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при удалении этапа: {str(e)}"
        )


@router.post("/stages/{stage_id}/participants", response_model=TaskStageDetailsOut)
async def add_stage_participants(
        stage_id: int,
        employee_ids: List[int],
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    _task, _stage = _task_of_stage(db, stage_id)
    ensure_task_access(db, current_user_id, _task)
    # Состав меняет ответственный за этап, а не любой сотрудник его отдела
    leads.ensure_stage_roster(db, current_user_id, _stage)
    try:
        stage = db.query(TaskStageDetails).filter(TaskStageDetails.id == stage_id).first()
        if not stage:
            raise HTTPException(
                status_code=404,
                detail=f"Этап с ID {stage_id} не найден"
            )

        existing_employees = db.query(Employee.id).filter(Employee.id.in_(employee_ids)).all()
        existing_ids = {e.id for e in existing_employees}
        missing_ids = set(employee_ids) - existing_ids

        if missing_ids:
            raise HTTPException(
                status_code=404,
                detail=f"Сотрудники с ID {missing_ids} не найдены"
            )

        before_ids = {r[0] for r in db.query(task_stage_participants.c.employee_id).filter(
            task_stage_participants.c.stage_detail_id == stage_id).all()}
        added_ids = sorted(set(employee_ids) - before_ids)
        for emp_id in added_ids:
            db.execute(
                task_stage_participants.insert().values(
                    stage_detail_id=stage_id,
                    employee_id=emp_id
                )
            )
        if added_ids:
            journal.log(db, _task.id, "stage_participants",
                        journal.names(db, sorted(before_ids)),
                        journal.names(db, sorted(before_ids | set(added_ids))), current_user_id)
        journal.sync_participants(db, _task.id, current_user_id)

        db.commit()

        stage = db.query(TaskStageDetails).options(
            joinedload(TaskStageDetails.participants),
            joinedload(TaskStageDetails.files)
        ).filter(TaskStageDetails.id == stage_id).first()

        await notify_stage_roster(db, _task, stage, added_ids, [], current_user_id)
        return stage
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при добавлении участников этапа: {str(e)}"
        )


@router.put("/stages/{stage_id}/participants", response_model=TaskStageDetailsOut)
async def update_stage_participants(
        stage_id: int,
        employee_ids: List[int],
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    _task, _stage = _task_of_stage(db, stage_id)
    ensure_task_access(db, current_user_id, _task)
    leads.ensure_stage_roster(db, current_user_id, _stage)
    leads.ensure_keeps_a_lead(db, current_user_id, _stage, employee_ids)
    employee_ids = list(dict.fromkeys(employee_ids))
    try:
        stage = db.query(TaskStageDetails).filter(TaskStageDetails.id == stage_id).first()
        if not stage:
            raise HTTPException(
                status_code=404,
                detail=f"Этап с ID {stage_id} не найден"
            )

        existing_employees = db.query(Employee.id).filter(Employee.id.in_(employee_ids)).all()
        existing_ids = {e.id for e in existing_employees}
        missing_ids = set(employee_ids) - existing_ids

        if missing_ids:
            raise HTTPException(
                status_code=404,
                detail=f"Сотрудники с ID {missing_ids} не найдены"
            )

        before_ids = {r[0] for r in db.query(task_stage_participants.c.employee_id).filter(
            task_stage_participants.c.stage_detail_id == stage_id).all()}

        db.execute(
            task_stage_participants.delete().where(
                task_stage_participants.c.stage_detail_id == stage_id
            )
        )

        for emp_id in employee_ids:
            db.execute(
                task_stage_participants.insert().values(
                    stage_detail_id=stage_id,
                    employee_id=emp_id
                )
            )

        # Журнал и состав задачи: участник этапа обязан числиться и в задаче,
        # иначе он ведёт работу, но в её списках не значится и не получает
        # уведомлений. Пишем до commit — вместе с самим изменением.
        now_ids = set(employee_ids)
        if before_ids != now_ids:
            journal.log(db, _task.id, "stage_participants",
                        journal.names(db, sorted(before_ids)), journal.names(db, sorted(now_ids)),
                        current_user_id)
        journal.sync_participants(db, _task.id, current_user_id,
                                  removed=before_ids - now_ids)
        db.commit()

        stage = db.query(TaskStageDetails).options(
            joinedload(TaskStageDetails.participants),
            joinedload(TaskStageDetails.files)
        ).filter(TaskStageDetails.id == stage_id).first()

        await notify_stage_roster(db, _task, stage, sorted(now_ids - before_ids),
                                  sorted(before_ids - now_ids), current_user_id)
        return stage
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при обновлении участников этапа: {str(e)}"
        )


@router.post("/tasks/{task_id}/stages/{stage_id}/spans", response_model=List[SpanOut])
async def insert_span_op(
        task_id: int,
        stage_id: int,
        op: SpanCreate,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    _task, _stage = _task_and_stage(db, task_id, stage_id)
    ensure_stage_edit(db, current_user_id, _task, _stage)
    """
    Операция вставки: сдвигает все spans после позиции, создаёт новый span для вставленного текста,
    объединяет соседние spans того же автора.
    """
    try:
        stage = db.query(TaskStageDetails).filter(TaskStageDetails.id == stage_id,
                                                  TaskStageDetails.task_id == task_id).with_for_update().first()
        if not stage:
            raise HTTPException(status_code=404, detail="Этап не найден")

        if stage.text_description is None:
            stage.text_description = ""

        pos = int(op.position)
        ins_text = op.text or ""
        L = len(ins_text)

        # защита границ
        pos = max(0, min(pos, len(stage.text_description)))

        # 1) обновляем сам текст
        new_text = stage.text_description[:pos] + ins_text + stage.text_description[pos:]
        stage.text_description = new_text

        # 2) сдвигаем spans, которые начинаются >= pos
        db.query(TaskStageTextSpan).filter(
            TaskStageTextSpan.stage_detail_id == stage_id,
            TaskStageTextSpan.start >= pos
        ).update({
            TaskStageTextSpan.start: TaskStageTextSpan.start + L,
            TaskStageTextSpan.end: TaskStageTextSpan.end + L
        }, synchronize_session=False)

        # 3) создаём span для вставленного текста
        new_span = TaskStageTextSpan(
            stage_detail_id=stage_id,
            author_id=current_user_id,
            start=pos,
            end=pos + L,
            created_at=datetime.now()
        )
        db.add(new_span)
        db.flush()  # получить id

        # 4) пытаемся объединить с соседними spans (prev/next) если автор тот же
        # prev: span, где end == pos
        prev_span = db.query(TaskStageTextSpan).filter(
            TaskStageTextSpan.stage_detail_id == stage_id,
            TaskStageTextSpan.end == pos
        ).order_by(TaskStageTextSpan.start.desc()).first()

        if prev_span and prev_span.author_id == current_user_id:
            # расширяем prev_span
            prev_span.end = new_span.end
            db.delete(new_span)
            new_span = prev_span

        else:
            # next: span где start == new_span.end
            next_span = db.query(TaskStageTextSpan).filter(
                TaskStageTextSpan.stage_detail_id == stage_id,
                TaskStageTextSpan.start == new_span.end
            ).order_by(TaskStageTextSpan.start).first()
            if next_span and next_span.author_id == current_user_id:
                # расширяем next_span: сдвинем его start в start нового span
                next_span.start = new_span.start
                db.delete(new_span)
                new_span = next_span

        # ещё можно объединить prev и next если они теперь рядом и одного автора (опционально)

        db.commit()

        spans = db.query(TaskStageTextSpan).filter(TaskStageTextSpan.stage_detail_id == stage_id).order_by(
            TaskStageTextSpan.start).all()
        await _broadcast_description(db, task_id, stage_id, current_user_id)
        return spans

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка вставки span: {e}")


@router.post("/tasks/{task_id}/stages/{stage_id}/spans/delete", response_model=List[SpanOut])
async def delete_span_op(
        task_id: int,
        stage_id: int,
        op: SpanDelete,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    _task, _stage = _task_and_stage(db, task_id, stage_id)
    ensure_stage_edit(db, current_user_id, _task, _stage)
    """
    Удаление диапазона [start, end) в тексте. Логика:
    - изменяем текст (удаляем кусок)
    - корректируем spans:
        * spans, полностью внутри удаляемого диапазона -> удаляются
        * spans, частично пересекающиеся -> обрезаются
        * spans, после диапазона -> сдвигаются влево на длину удаления
    """
    try:
        stage = db.query(TaskStageDetails).filter(TaskStageDetails.id == stage_id,
                                                  TaskStageDetails.task_id == task_id).with_for_update().first()
        if not stage:
            raise HTTPException(status_code=404, detail="Этап не найден")

        if stage.text_description is None:
            stage.text_description = ""

        start = int(op.start)
        end = int(op.end)
        if start < 0 or end < start:
            raise HTTPException(status_code=400, detail="Неверные границы удаления")

        # clip to bounds
        start = max(0, min(start, len(stage.text_description)))
        end = max(0, min(end, len(stage.text_description)))
        if start >= end:
            return db.query(TaskStageTextSpan).filter(TaskStageTextSpan.stage_detail_id == stage_id).order_by(
                TaskStageTextSpan.start).all()

        L = end - start

        # 1) обновляем текст
        new_text = stage.text_description[:start] + stage.text_description[end:]
        stage.text_description = new_text

        # 2) обработка spans:
        spans = db.query(TaskStageTextSpan).filter(TaskStageTextSpan.stage_detail_id == stage_id).order_by(
            TaskStageTextSpan.start).with_for_update().all()

        for s in spans:
            # полностью до удаления — ничего не делаем
            if s.end <= start:
                continue
            # полностью после удаления — сдвигаем
            if s.start >= end:
                s.start = s.start - L
                s.end = s.end - L
                continue
            # частичное пересечение слева
            if s.start < start < s.end <= end:
                s.end = start
                continue
            # частичное пересечение справа
            if start <= s.start < end < s.end:
                s.start = start
                s.end = s.end - L
                # сдвинуть start вниз на L? мы уже установили start = start, а далее сдвигаем правую часть
                continue
            # span покрывает удаление целиком (s.start < start and s.end > end)
            if s.start < start and s.end > end:
                # сокращаем span, уменьшаем end на L
                s.end = s.end - L
                continue
            # span полностью внутри удаления -> удалить
            if s.start >= start and s.end <= end:
                db.delete(s)
                continue

        # после корректировок можно попытаться объединить соседние spans, у которых автора тот же
        all_spans = db.query(TaskStageTextSpan).filter(TaskStageTextSpan.stage_detail_id == stage_id).order_by(
            TaskStageTextSpan.start).all()
        i = 0
        while i + 1 < len(all_spans):
            a = all_spans[i]
            b = all_spans[i + 1]
            if a.end == b.start and a.author_id == b.author_id:
                a.end = b.end
                db.delete(b)
                all_spans.pop(i + 1)
                # не инкрементируем i — проверим новое соседство
            else:
                i += 1

        db.commit()
        spans = db.query(TaskStageTextSpan).filter(TaskStageTextSpan.stage_detail_id == stage_id).order_by(
            TaskStageTextSpan.start).all()
        await _broadcast_description(db, task_id, stage_id, current_user_id)
        return spans

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка удаления span: {e}")
