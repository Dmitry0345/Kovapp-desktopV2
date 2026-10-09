# -*- coding: utf-8 -*-
"""Журнал действий задачи и состав её участников.

Журнал вёлся только в одном месте — при сохранении задачи. Всё остальное,
что человек делает внутри задачи, мимо него проходило: прикрепил файл,
удалил файл, поправил описание этапа, сменил состав, завершил этап. В
результате открывший журнал видел пустоту, хотя в задаче кипела работа.
Здесь запись вынесена в общий модуль, и им пользуются все точки входа.

Здесь же живёт правило состава: участники задачи — это объединение
участников всех её этапов. Раньше списки жили порознь, человек мог вести
этап и не числиться в задаче.
"""
from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from server.database.models import (Employee, Task, TaskAssignment, TaskChangeLog,
                                    TaskStageDetails, task_stage_participants)

from .config import logger

# Подписи полей журнала. Ключ — то, что лежит в базе, значение — что видит
# человек. Пополняется вместе с новыми видами записей.
TITLES = {
    "name": "Название", "description": "Описание", "deadline": "Дата окончания",
    "activity": "Состояние", "customer_organization": "Организация",
    "project_id": "Проект", "responsible": "Ответственные",
    "assigned": "Исполнители", "stage_department": "Отдел этапа",
    "stage_completed": "Этап", "state": "Текущий этап",
    # действия внутри задачи
    "task_file": "Файл задачи", "stage_file": "Файл этапа",
    "stage_text": "Описание этапа", "stage_participants": "Участники этапа",
    "stage_added": "Новый этап", "stage_removed": "Этап удалён",
    "stage_done": "Завершение этапа", "stage_resumed": "Этап возобновлён",
    "task_participants": "Участники задачи",
    "stage_lead": "Ответственный за этап",
    "task_completion": "Завершение задачи",
    "task_completed": "Задача завершена",
}


def names(db: Session, ids: Iterable[int]) -> str:
    ids = [i for i in (ids or []) if i]
    if not ids:
        return "никого"
    rows = db.query(Employee).filter(Employee.id.in_(ids)).all()
    got = sorted(" ".join(x for x in [e.last_name, e.first_name] if x).strip() for e in rows)
    return ", ".join(got) if got else "никого"


def log(db: Session, task_id: Optional[int], field: str, old, new, user_id: int) -> None:
    """Строка журнала. Пишется, только если значение действительно изменилось.

    Commit не делается: запись кладётся в ту же сессию, что и само действие,
    и уходит в базу вместе с ним. Если действие откатится, не останется и
    записи о нём.
    """
    if not task_id:
        return
    old_s = "" if old is None else str(old)
    new_s = "" if new is None else str(new)
    if old_s == new_s:
        return
    try:
        db.add(TaskChangeLog(task_id=task_id, field=field, old_value=old_s[:500],
                             new_value=new_s[:500], user_id=user_id, created_at=datetime.now()))
    except Exception as e:
        logger.error("Журнал задачи %s: запись не создана: %s", task_id, e)


def note(db: Session, task_id: Optional[int], field: str, text: str, user_id: int) -> None:
    """Событие без «было → стало»: прикрепление файла, завершение этапа"""
    log(db, task_id, field, "", text, user_id)


# ── состав участников ────────────────────────────────────────────────────

def stage_participant_ids(db: Session, task_id: int) -> set:
    """Все, кто числится хотя бы в одном этапе задачи"""
    rows = db.query(task_stage_participants.c.employee_id).join(
        TaskStageDetails,
        TaskStageDetails.id == task_stage_participants.c.stage_detail_id).filter(
        TaskStageDetails.task_id == task_id).all()
    return {r[0] for r in rows if r and r[0]}


def task_assigned_ids(db: Session, task_id: int) -> set:
    rows = db.query(TaskAssignment.employee_id).filter(TaskAssignment.task_id == task_id).all()
    return {r[0] for r in rows if r and r[0]}


def sync_participants(db: Session, task_id: int, user_id: int,
                      removed: Optional[Iterable[int]] = None) -> dict:
    """Приводит список участников задачи в согласие с этапами.

    Участник этапа обязан числиться и в задаче — иначе человек ведёт работу,
    но в её составе не значится, не получает уведомлений и не видит задачу
    в своём списке. Убираем только тех, кого явно вывели из этапа и кто не
    остался ни в одном другом: назначенных вручную это не трогает.
    """
    if not task_id:
        return {"added": [], "removed": []}
    in_stages = stage_participant_ids(db, task_id)
    assigned = task_assigned_ids(db, task_id)

    add = sorted(in_stages - assigned)
    for emp_id in add:
        db.add(TaskAssignment(task_id=task_id, employee_id=emp_id))

    drop = sorted({i for i in (removed or []) if i} & assigned - in_stages)
    if drop:
        db.query(TaskAssignment).filter(
            TaskAssignment.task_id == task_id,
            TaskAssignment.employee_id.in_(drop)).delete(synchronize_session=False)

    if add or drop:
        parts = []
        if add:
            parts.append("добавлены: " + names(db, add))
        if drop:
            parts.append("убраны: " + names(db, drop))
        note(db, task_id, "task_participants", "; ".join(parts), user_id)
    return {"added": add, "removed": drop}


def task_id_of_stage(db: Session, stage_id: int) -> Optional[int]:
    row = db.query(TaskStageDetails.task_id).filter(TaskStageDetails.id == stage_id).first()
    return row[0] if row else None


def task_watchers(db: Session, task_id: int) -> set:
    """Кому рассылать изменения состава: участники задачи и её ответственные"""
    out = task_assigned_ids(db, task_id)
    task = db.query(Task).filter(Task.id == task_id).first()
    if task:
        try:
            out |= {p.id for p in (task.responsible_persons or [])}
        except Exception as e:
            logger.debug("Журнал задачи: ответственные не прочитались: %s", e)
    return {i for i in out if i}
