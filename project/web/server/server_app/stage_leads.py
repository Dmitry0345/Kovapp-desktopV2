# -*- coding: utf-8 -*-
"""Ответственные за этап: кто ведёт этап и кто вправе менять его состав.

Раньше этап вёл целый отдел: продлевать и завершать мог любой его
руководитель, а состав набирался всем отделом сразу. Теперь при продлении
задачи называется ответственный — один или несколько, — и правила такие:

  * никого не выбрали — ответственными становятся все руководители отдела,
    а в состав, как и прежде, попадает весь отдел;
  * выбрали одного — только он и попадает на этап, без исполнителей;
  * выбрали нескольких — они плюс сотрудники отдела.

Отдельный случай — «Договорной отдел»: руководителей в нём нет и не
предвидится. Поэтому там ответственным может быть назван любой сотрудник
отдела, и внутри своего этапа он получает те же права, что руководитель в
своём: правит содержимое, меняет состав, продлевает и завершает задачу.
"""
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from server.database.models import Employee, TaskStageLead

from .core import STATUS_HEAD, employee_status, is_management_status

CONTRACT_DEPARTMENT = "Договорной отдел"


def department_staff(db: Session, department: str) -> List[Employee]:
    if not department:
        return []
    return db.query(Employee).filter(Employee.department == department).all()


def department_heads(db: Session, department: str) -> List[Employee]:
    """Руководители отдела. Пусто — значит их в отделе нет."""
    return [e for e in department_staff(db, department)
            if employee_status(e) == STATUS_HEAD]


def lead_candidates(db: Session, department: str) -> List[Employee]:
    """Кого предлагать в ответственные за этап.

    Обычно это руководители отдела. Если руководителей нет — предлагаем весь
    отдел: иначе в «Договорном отделе» продлить задачу было бы не на кого.
    """
    heads = department_heads(db, department)
    return heads if heads else department_staff(db, department)


def lead_ids(db: Session, stage_id: int) -> set:
    rows = db.query(TaskStageLead.employee_id).filter(
        TaskStageLead.stage_detail_id == stage_id).all()
    return {r[0] for r in rows if r and r[0]}


def set_leads(db: Session, stage_id: int, employee_ids) -> None:
    db.query(TaskStageLead).filter(
        TaskStageLead.stage_detail_id == stage_id).delete(synchronize_session=False)
    for emp_id in sorted({i for i in employee_ids if i}):
        db.add(TaskStageLead(stage_detail_id=stage_id, employee_id=emp_id))


def resolve_roster(db: Session, department: str, chosen: Optional[List[int]]) -> dict:
    """Состав нового этапа по выбранным ответственным.

    Возвращает {leads, participants}. Правила — в шапке модуля.
    """
    staff = department_staff(db, department)
    staff_ids = {e.id for e in staff}
    chosen = [i for i in (chosen or []) if i in staff_ids]

    if not chosen:
        leads = {e.id for e in lead_candidates(db, department)}
        # Никого не назвали — на этап идёт отдел целиком, как было раньше
        return {"leads": leads, "participants": staff_ids or leads}

    if len(chosen) == 1:
        # Один ответственный ведёт этап в одиночку: исполнителей он добавит
        # сам, если понадобятся
        return {"leads": set(chosen), "participants": set(chosen)}

    return {"leads": set(chosen), "participants": staff_ids | set(chosen)}


def leads_the_stage(db: Session, user_id: int, stage) -> bool:
    """Вправе ли человек распоряжаться этим этапом.

    Руководство — всегда. Названный ответственный — всегда. Если
    ответственных не назначали (этапы, созданные до этой доработки),
    действует прежнее правило: руководитель ведущего отдела, а в отделе без
    руководителей — любой его сотрудник.
    """
    employee = db.query(Employee).filter(Employee.id == user_id).first()
    if not employee:
        return False
    if is_management_status(employee):
        return True

    stage_id = getattr(stage, "id", None)
    named = lead_ids(db, stage_id) if stage_id else set()
    if named:
        return user_id in named

    department = (getattr(stage, "department_name", None) or "").strip()
    if not department or (employee.department or "").strip() != department:
        return False
    heads = department_heads(db, department)
    return not heads or user_id in {h.id for h in heads}


def ensure_stage_roster(db: Session, user_id: int, stage) -> None:
    """Менять состав этапа вправе тот, кто им распоряжается"""
    if not leads_the_stage(db, user_id, stage):
        raise HTTPException(
            status_code=403,
            detail="Состав этапа меняет ответственный за него или руководитель отдела")


def ensure_keeps_a_lead(db: Session, user_id: int, stage, new_ids) -> None:
    """Себя из этапа не выводят, пока не назначен другой ответственный.

    Иначе этап остаётся без хозяина: продлить и завершить его будет некому,
    а вернуть себя обратно человек уже не сможет — прав не останется.
    """
    employee = db.query(Employee).filter(Employee.id == user_id).first()
    if not employee or is_management_status(employee):
        return
    new_ids = set(new_ids or [])
    if user_id in new_ids:
        return
    stage_id = getattr(stage, "id", None)
    named = lead_ids(db, stage_id) if stage_id else set()
    if named and user_id not in named:
        return
    others = (named - {user_id}) & new_ids
    if not others:
        department = (getattr(stage, "department_name", None) or "").strip()
        can_lead = {e.id for e in lead_candidates(db, department)} - {user_id}
        others = can_lead & new_ids
    if not others:
        raise HTTPException(
            status_code=409,
            detail="Сначала назначьте другого ответственного за этап — "
                   "без него этап останется без хозяина")
