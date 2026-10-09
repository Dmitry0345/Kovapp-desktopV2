# -*- coding: utf-8 -*-
"""Права на проекты.

Правила пока не утверждены заказчиком, поэтому есть переключатель
PROJECT_ACCESS_MODE в .env:

  open   — (по умолчанию) все авторизованные видят все проекты и их файлы;
           создавать могут директора, администратор, руководители отделов
           и сотрудники договорного отдела;
           редактировать/удалять — только создатель проекта и любой Директор.
  strict — просмотр: директора, администратор, руководители отделов — всё;
           остальные — только проекты, к задачам которых они относятся;
           файлы: те же, кто видит проект; редактирование — как в open.

Все проверки собраны здесь, чтобы окончательные правила менялись в одном месте.
"""

from typing import Optional

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from server.database.models import Employee, Project, Task, TaskStageDetails
from .config import PROJECT_ACCESS_MODE
from .core import (CONTRACT_DEPARTMENT, STATUS_ADMIN, STATUS_DIRECTOR, employee_status,
                   is_head_status, is_management_status)

DIRECTOR = "Директор"
ADMIN = "Администратор"


def get_employee(db: Session, user_id: int) -> Optional[Employee]:
    return db.query(Employee).filter(Employee.id == user_id).first()


def is_director(emp: Optional[Employee]) -> bool:
    return employee_status(emp) == STATUS_DIRECTOR


def is_admin(emp: Optional[Employee]) -> bool:
    return employee_status(emp) == STATUS_ADMIN


def is_management(emp: Optional[Employee]) -> bool:
    """Директор или администратор — полный доступ к задачам и проектам."""
    return is_management_status(emp)


def is_department_head(emp: Optional[Employee]) -> bool:
    """Руководитель отдела: маркер в базе, не слово в должности."""
    return is_head_status(emp)


def can_create_project(emp: Optional[Employee]) -> bool:
    if is_management(emp) or is_department_head(emp):
        return True
    return bool(emp) and (emp.department or "").strip() == CONTRACT_DEPARTMENT


def can_edit_project(emp: Optional[Employee], project: Project) -> bool:
    """Только создатель и любой директор — по требованию заказчика."""
    if not emp:
        return False
    return project.creator_id == emp.id or is_director(emp)


def can_delete_project(emp: Optional[Employee], project: Project) -> bool:
    return can_edit_project(emp, project)


def can_complete_project(emp: Optional[Employee], project: Project) -> bool:
    """Завершить или вернуть в работу: создатель, директор или администратор."""
    if not emp:
        return False
    return project.creator_id == emp.id or is_director(emp) or is_admin(emp)


def _participates_in_project_tasks(db: Session, user_id: int, project_id: int) -> bool:
    return db.query(Task.id).filter(
        Task.project_id == project_id,
        or_(Task.creator_id == user_id,
            Task.assigned_employees.any(id=user_id),
            Task.responsible_persons.any(id=user_id),
            Task.stage_details.any(TaskStageDetails.participants.any(id=user_id)))
    ).first() is not None


def can_view_project(db: Session, emp: Optional[Employee], project: Project) -> bool:
    if not emp:
        return False
    if PROJECT_ACCESS_MODE != "strict":
        return True
    if is_management(emp) or is_department_head(emp):
        return True
    if project.creator_id == emp.id or project.responsible_id == emp.id:
        return True
    return _participates_in_project_tasks(db, emp.id, project.id)


def can_manage_project_files(db: Session, emp: Optional[Employee], project: Project) -> bool:
    """Загрузка и удаление файлов разделов проекта."""
    return can_view_project(db, emp, project)


def visible_projects_filter(db: Session, emp: Employee, query):
    """Сужает выборку проектов в strict-режиме; в open возвращает запрос как есть."""
    if PROJECT_ACCESS_MODE != "strict" or is_management(emp) or is_department_head(emp):
        return query
    return query.filter(or_(
        Project.creator_id == emp.id,
        Project.responsible_id == emp.id,
        Project.tasks.any(or_(
            Task.creator_id == emp.id,
            Task.assigned_employees.any(id=emp.id),
            Task.responsible_persons.any(id=emp.id),
        )),
    ))


def ensure_project_exists(db: Session, project_id: int) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail=f"Проект с ID {project_id} не найден")
    return project


def valid_responsible(db: Session, actor: Employee, responsible_id: Optional[int]) -> Optional[int]:
    """Проверяет выбор ответственного по правилам создания проекта.

    Руководитель отдела всегда назначается сам; директор/администратор
    выбирают из руководителей отделов и директоров.
    """
    if is_department_head(actor):
        return actor.id
    if responsible_id is None:
        return None
    target = get_employee(db, responsible_id)
    if not target:
        raise HTTPException(status_code=404, detail="Ответственный сотрудник не найден")
    if not (is_department_head(target) or is_director(target)):
        raise HTTPException(status_code=400,
                            detail="Ответственным может быть только руководитель отдела или директор")
    return target.id
