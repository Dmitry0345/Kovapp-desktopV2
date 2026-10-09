# -*- coding: utf-8 -*-
"""Раздел «Проекты».

Проект создаёт руководитель отдела (или директор/администратор с указанием
ответственного), у проекта есть организация, дедлайн, задачи (через
tasks.project_id) и три файловых раздела: contracts («Договоры»),
project («Проект»), delivery («Сдача»). Раздел «Фото» не хранится отдельно —
это фото и видео задач проекта, сгруппированные по задачам.

Столбцы таблицы раздела строит клиент: GET /projects/heads даёт список
руководителей отделов, GET /projects — проекты с полями column_owner_id.
"""

import asyncio
import mimetypes
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import asc, desc, func, or_
from sqlalchemy.orm import Session, joinedload

from server.database.models import (Employee, Notification, Project, ProjectFile, Task, TaskAssignment,
                                    TaskFile, TaskStageDetails, TaskStageFile, TaskStageTextSpan,
                                    task_responsible_associations, task_stage_participants)

from .. import media
from ..config import FILE_STORAGE_ROOT, logger
from ..core import (get_current_user_id, get_current_user_id_from_header_or_query, get_db,
                    user_has_full_task_access)
from ..media import ensure_thumbnail
from ..project_permissions import (can_complete_project, can_create_project, can_delete_project, can_edit_project,
                                   can_manage_project_files, can_view_project, get_employee,
                                   is_department_head, is_director, is_management, valid_responsible,
                                   visible_projects_filter)
from ..realtime import manager
from ..schemas import (PROJECT_SECTIONS, ProjectCreate, ProjectFileOut, ProjectOut,
                       ProjectPhotoFolder, ProjectTaskOut, ProjectUpdate, UploaderShort)
from ..sheets import sheet_json
from ..storage import (office_preview_response, get_decompressed_file_response, get_decompressed_file_response_with_range,
                       purge_quarantined_files, quarantine_files_for_deletion, resolve_stored_path,
                       restore_quarantined_files)

router = APIRouter()

PROJECT_SORTS = {
    "deadline_desc", "deadline_asc", "name_asc", "name_desc", "created_desc", "created_asc",
}
TASK_SORTS = {
    "deadline_asc", "deadline_desc", "id_asc", "id_desc", "created_asc", "created_desc",
    "name_asc", "name_desc",
}
SECTION_TITLES = {"contracts": "Договоры", "project": "Проект", "delivery": "Сдача"}


# ── вспомогательное ──────────────────────────────────────────────────────────

def _short(emp) -> Optional[dict]:
    if emp is None:
        return None
    return {"id": emp.id, "last_name": emp.last_name, "first_name": emp.first_name,
            "middle_name": emp.middle_name, "department": emp.department}


def _project_out(db: Session, project: Project, actor: Employee, counts=None, upload: Optional[bool] = None,
                 unfinished: Optional[int] = None) -> dict:
    total, active, files_total = counts or _count_for(db, project.id)
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "organization": project.organization,
        "responsible_id": project.responsible_id,
        "creator_id": project.creator_id,
        "deadline": project.deadline,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        "responsible": _short(project.responsible),
        "creator": _short(project.creator),
        "tasks_total": total,
        "tasks_active": active,
        "files_total": files_total,
        "can_edit": can_edit_project(actor, project) and not getattr(project, "is_archived", False),   # завершённый — только чтение
        "can_delete": can_delete_project(actor, project) and not getattr(project, "is_archived", False),
        "can_complete": can_complete_project(actor, project),
        "is_archived": bool(getattr(project, "is_archived", False)),
        "archived_at": getattr(project, "archived_at", None),
        "archived_by": _short(getattr(project, "archived_by", None)),
        "tasks_unfinished": unfinished if unfinished is not None else _unfinished(db, project.id),
        "can_upload": (upload if upload is not None else can_manage_project_files(db, actor, project))
                      and not getattr(project, "is_archived", False),
    }


def _unfinished(db: Session, project_id: int) -> int:
    """Незавершённые задачи проекта: всё, что не в архиве."""
    return db.query(func.count(Task.id)).filter(Task.project_id == project_id, Task.activity != "Архивная").scalar() or 0


def _count_for(db: Session, project_id: int):
    total = db.query(func.count(Task.id)).filter(Task.project_id == project_id).scalar() or 0
    active = db.query(func.count(Task.id)).filter(
        Task.project_id == project_id, Task.activity == "Активная").scalar() or 0
    files_total = db.query(func.count(ProjectFile.id)).filter(
        ProjectFile.project_id == project_id).scalar() or 0
    return total, active, files_total


def _load_project(db: Session, project_id: int) -> Project:
    project = db.query(Project).options(
        joinedload(Project.responsible), joinedload(Project.creator)
    ).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail=f"Проект с ID {project_id} не найден")
    return project


def _actor(db: Session, user_id: int) -> Employee:
    emp = get_employee(db, user_id)
    if not emp:
        raise HTTPException(status_code=401, detail="Сотрудник не найден")
    return emp


def _require_view(db: Session, actor: Employee, project: Project) -> None:
    if not can_view_project(db, actor, project):
        raise HTTPException(status_code=403, detail="Нет доступа к проекту")


def _section(value: str) -> str:
    value = (value or "").strip().lower()
    if value not in PROJECT_SECTIONS:
        raise HTTPException(status_code=400,
                            detail="Раздел должен быть одним из: contracts, project, delivery")
    return value


def _project_folder(project_id: int, section: str) -> str:
    return f"projects/{project_id}/{section}"


async def _broadcast_all(payload: dict) -> None:
    try:
        await manager.broadcast(payload)
    except Exception:
        logger.exception("Не удалось разослать событие проекта")


# ── руководители отделов (столбцы таблицы) ───────────────────────────────────


def _deadline_not_past(value) -> None:
    """При создании срок — не раньше сегодняшнего дня."""
    if value is None:
        return
    from datetime import date as _d, datetime as _dt
    day = value.date() if isinstance(value, _dt) else value
    if isinstance(day, _d) and day < _dt.now().date():
        raise HTTPException(status_code=400, detail="Дата окончания не может быть раньше сегодняшнего дня")

@router.get("/projects/heads", response_model=List[UploaderShort])
def get_department_heads(db: Session = Depends(get_db),
                               current_user_id: int = Depends(get_current_user_id)):
    """Руководители отделов без директоров и администратора — по одному столбцу на каждого."""
    employees = db.query(Employee).order_by(Employee.last_name, Employee.first_name).all()
    return [e for e in employees if is_department_head(e)]


@router.get("/projects/responsible-candidates", response_model=List[UploaderShort])
def get_responsible_candidates(db: Session = Depends(get_db),
                                     current_user_id: int = Depends(get_current_user_id)):
    """Кого можно назначить ответственным: руководители отделов и директора."""
    actor = _actor(db, current_user_id)
    if is_department_head(actor):
        return [actor]
    employees = db.query(Employee).order_by(Employee.last_name, Employee.first_name).all()
    return [e for e in employees if is_department_head(e) or is_director(e)]


# ── список и CRUD ────────────────────────────────────────────────────────────

@router.get("/projects", response_model=List[ProjectOut])
def list_projects(
        organization: Optional[str] = Query(None),
        head_id: Optional[int] = Query(None, description="Фильтр по руководителю (столбцу)"),
        search: Optional[str] = Query(None),
        sort: str = Query("deadline_desc"),
        is_archived: bool = Query(False, description="Завершённые (архивные) проекты"),
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)):
    actor = _actor(db, current_user_id)
    if sort not in PROJECT_SORTS:
        sort = "deadline_desc"

    query = db.query(Project).options(joinedload(Project.responsible), joinedload(Project.creator),
                                      joinedload(Project.archived_by))
    query = visible_projects_filter(db, actor, query)
    query = query.filter(Project.is_archived == is_archived)   # «Активные» / «Архивные», как у задач
    if organization:
        query = query.filter(Project.organization == organization)
    if head_id:
        # Столбцы списка строятся по создателю, поэтому и фильтр по нему:
        # иначе выбранный человек давал пустую таблицу
        query = query.filter(Project.creator_id == head_id)
    if search:
        query = query.filter(Project.name.contains(search.strip()))

    # Проекты без дедлайна — всегда в конце, независимо от направления
    if sort.startswith("deadline"):
        direction = desc if sort.endswith("desc") else asc
        query = query.order_by(Project.deadline.is_(None), direction(Project.deadline), desc(Project.id))
    elif sort.startswith("name"):
        query = query.order_by((desc if sort.endswith("desc") else asc)(Project.name))
    else:
        query = query.order_by((desc if sort.endswith("desc") else asc)(Project.created_at))

    projects = query.all()
    if not projects:
        return []

    ids = [p.id for p in projects]
    totals = dict(db.query(Task.project_id, func.count(Task.id))
                  .filter(Task.project_id.in_(ids)).group_by(Task.project_id).all())
    actives = dict(db.query(Task.project_id, func.count(Task.id))
                   .filter(Task.project_id.in_(ids), Task.activity == "Активная")
                   .group_by(Task.project_id).all())
    files = dict(db.query(ProjectFile.project_id, func.count(ProjectFile.id))
                 .filter(ProjectFile.project_id.in_(ids)).group_by(ProjectFile.project_id).all())
    # незавершённые задачи — одним запросом на весь список, а не по запросу на проект
    unfinished = dict(db.query(Task.project_id, func.count(Task.id))
                      .filter(Task.project_id.in_(ids), Task.activity != "Архивная").group_by(Task.project_id).all())
    # всё, что прошло visible_projects_filter, пользователь видит, а значит, может и загружать:
    # отдельная проверка на каждый проект здесь лишь повторяла бы запрос к базе
    return [_project_out(db, p, actor, (totals.get(p.id, 0), actives.get(p.id, 0), files.get(p.id, 0)), upload=True,
                         unfinished=unfinished.get(p.id, 0))
            for p in projects]


@router.get("/projects/organizations", response_model=List[str])
def project_organizations(db: Session = Depends(get_db),
                                current_user_id: int = Depends(get_current_user_id)):
    """Организации, у которых есть хотя бы один проект — для фильтра раздела."""
    rows = db.query(Project.organization).filter(Project.organization.isnot(None)).distinct().all()
    return sorted({r[0] for r in rows if r[0]})


@router.post("/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(data: ProjectCreate, db: Session = Depends(get_db),
                         current_user_id: int = Depends(get_current_user_id)):
    actor = _actor(db, current_user_id)
    _deadline_not_past(data.deadline)
    if not can_create_project(actor):
        raise HTTPException(status_code=403,
                            detail="Создавать проекты могут директора, администратор, руководители отделов "
                                   "и договорной отдел")
    name = (data.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Укажите название проекта")

    project = Project(
        name=name,
        description=(data.description or None),
        organization=(data.organization or "").strip() or None,
        responsible_id=valid_responsible(db, actor, data.responsible_id),
        creator_id=actor.id,
        deadline=data.deadline,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db.add(project)
    db.commit()
    project = _load_project(db, project.id)
    out = _project_out(db, project, actor)
    await _broadcast_all({"type": "project.created", "project_id": project.id,
                          "name": project.name, "sender_id": actor.id})
    return out


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, db: Session = Depends(get_db),
                      current_user_id: int = Depends(get_current_user_id)):
    actor = _actor(db, current_user_id)
    project = _load_project(db, project_id)
    _require_view(db, actor, project)
    return _project_out(db, project, actor)


@router.put("/projects/{project_id}", response_model=ProjectOut)
async def update_project(project_id: int, data: ProjectUpdate, db: Session = Depends(get_db),
                         current_user_id: int = Depends(get_current_user_id)):
    actor = _actor(db, current_user_id)
    project = _load_project(db, project_id)
    if not can_edit_project(actor, project):
        raise HTTPException(status_code=403,
                            detail="Редактировать проект может только его создатель или директор")

    fields_set = getattr(data, "model_fields_set", None) or getattr(data, "__fields_set__", set())
    if data.name is not None:
        name = data.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Название проекта не может быть пустым")
        project.name = name
    if "description" in fields_set:
        project.description = data.description or None
    if "organization" in fields_set:
        project.organization = (data.organization or "").strip() or None
    if "deadline" in fields_set:
        project.deadline = data.deadline
    if "responsible_id" in fields_set:
        project.responsible_id = valid_responsible(db, actor, data.responsible_id)
    project.updated_at = datetime.now()
    db.commit()

    project = _load_project(db, project_id)
    out = _project_out(db, project, actor)
    await _broadcast_all({"type": "project.updated", "project_id": project.id,
                          "name": project.name, "sender_id": actor.id})
    return out


def _delete_task_rows(db: Session, task_id: int):
    """Полное удаление задачи со всеми связями — тем же порядком, что и DELETE /tasks/{id}."""
    stage_ids = [x.id for x in db.query(TaskStageDetails.id).filter(TaskStageDetails.task_id == task_id).all()]
    if stage_ids:
        db.execute(task_stage_participants.delete().where(
            task_stage_participants.c.stage_detail_id.in_(stage_ids)))
        db.query(TaskStageTextSpan).filter(
            TaskStageTextSpan.stage_detail_id.in_(stage_ids)).delete(synchronize_session=False)
        db.query(TaskStageFile).filter(
            TaskStageFile.stage_detail_id.in_(stage_ids)).delete(synchronize_session=False)
    db.query(TaskStageDetails).filter(TaskStageDetails.task_id == task_id).delete(synchronize_session=False)
    db.query(TaskFile).filter(TaskFile.task_id == task_id).delete(synchronize_session=False)
    db.execute(task_responsible_associations.delete().where(
        task_responsible_associations.c.task_id == task_id))
    db.execute(TaskAssignment.__table__.delete().where(TaskAssignment.__table__.c.task_id == task_id))
    db.query(Notification).filter(Notification.target_section == "task_view",
                                  Notification.target_id == task_id).delete(synchronize_session=False)
    db.query(Task).filter(Task.id == task_id).delete(synchronize_session=False)


@router.post("/projects/{project_id}/complete", response_model=ProjectOut)
async def complete_project(project_id: int, db: Session = Depends(get_db),
                           current_user_id: int = Depends(get_current_user_id)):
    """Завершить проект: создатель, директор или администратор; все задачи проекта завершены."""
    actor = _actor(db, current_user_id)
    project = _load_project(db, project_id)
    if not can_complete_project(actor, project):
        raise HTTPException(status_code=403, detail="Завершить проект может только его создатель, директор или администратор")
    left = _unfinished(db, project.id)
    if left:
        raise HTTPException(status_code=400, detail="Проект нельзя завершить: незавершённых задач — %d" % left)
    project.is_archived = True
    project.archived_at = datetime.now()
    project.archived_by_id = actor.id   # кто завершил — сохраняется
    db.commit()
    db.refresh(project)
    return _project_out(db, project, actor)


@router.post("/projects/{project_id}/reopen", response_model=ProjectOut)
async def reopen_project(project_id: int, db: Session = Depends(get_db),
                         current_user_id: int = Depends(get_current_user_id)):
    """Вернуть завершённый проект в работу — те же люди, что могут завершить."""
    actor = _actor(db, current_user_id)
    project = _load_project(db, project_id)
    if not can_complete_project(actor, project):
        raise HTTPException(status_code=403, detail="Вернуть проект в работу может только его создатель, директор или администратор")
    project.is_archived = False   # кто и когда завершал в последний раз — остаётся в истории
    db.commit()
    db.refresh(project)
    return _project_out(db, project, actor)


@router.delete("/projects/{project_id}")
async def delete_project(project_id: int, db: Session = Depends(get_db),
                         current_user_id: int = Depends(get_current_user_id)):
    actor = _actor(db, current_user_id)
    project = _load_project(db, project_id)
    if not can_delete_project(actor, project):
        raise HTTPException(status_code=403,
                            detail="Удалить проект может только его создатель или директор")

    quarantine = []
    try:
        paths = [f.filepath for f in db.query(ProjectFile).filter(ProjectFile.project_id == project_id).all()]
        # Задачи проекта удаляются вместе с ним — вместе со своими файлами
        task_ids = [t.id for t in db.query(Task.id).filter(Task.project_id == project_id).all()]
        if task_ids:
            stage_ids = [x.id for x in db.query(TaskStageDetails.id)
                         .filter(TaskStageDetails.task_id.in_(task_ids)).all()]
            paths += [f.filepath for f in db.query(TaskFile).filter(TaskFile.task_id.in_(task_ids)).all() if f.filepath]
            if stage_ids:
                paths += [f.filepath for f in db.query(TaskStageFile)
                          .filter(TaskStageFile.stage_detail_id.in_(stage_ids)).all() if f.filepath]
        quarantine = quarantine_files_for_deletion(paths)

        for task_id in task_ids:
            _delete_task_rows(db, task_id)
        db.query(ProjectFile).filter(ProjectFile.project_id == project_id).delete(synchronize_session=False)
        db.delete(project)
        db.commit()
        purge_quarantined_files(quarantine)
        for task_id in task_ids:
            await _broadcast_all({"type": "task.deleted", "task_id": task_id,
                                  "sender_id": current_user_id})
        for section in PROJECT_SECTIONS:
            folder = Path(FILE_STORAGE_ROOT) / _project_folder(project_id, section)
            try:
                if folder.is_dir() and not any(folder.iterdir()):
                    folder.rmdir()
            except Exception as e:
                logger.debug("Проекты: рассылка об удалении задачи не ушла: %s", e)
        parent = Path(FILE_STORAGE_ROOT) / "projects" / str(project_id)
        try:
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
        except Exception as e:
            logger.debug("Проекты: пустая папка не удалена: %s", e)
    except HTTPException:
        db.rollback()
        restore_quarantined_files(quarantine)
        raise
    except Exception as e:
        db.rollback()
        restore_quarantined_files(quarantine)
        raise HTTPException(status_code=500, detail=f"Ошибка при удалении проекта: {e}")

    await _broadcast_all({"type": "project.deleted", "project_id": project_id, "sender_id": actor.id})
    return {"message": "Проект удалён"}


# ── задачи проекта ───────────────────────────────────────────────────────────

def _task_row(task: Task) -> dict:
    stages = task.stage_details or []
    return {
        "id": task.id,
        "name": task.name,
        "deadline": task.deadline,
        "activity": task.activity,
        "state": task.state.name if task.state else None,
        "creation_date": task.creation_date,
        "customer_organization": task.customer_organization,
        "total_stages": len(stages),
        "completed_stages": sum(1 for s in stages if s.is_completed),
        "departments": sorted({s.department_name for s in stages if s.department_name}),
    }


def _visible_tasks_query(db: Session, user_id: int, query):
    if user_has_full_task_access(db, user_id):
        return query
    return query.filter(or_(
        Task.creator_id == user_id,
        Task.assigned_employees.any(id=user_id),
        Task.responsible_persons.any(id=user_id),
        Task.stage_details.any(TaskStageDetails.participants.any(id=user_id)),
    ))


@router.get("/projects/{project_id}/tasks", response_model=List[ProjectTaskOut])
def project_tasks(
        project_id: int,
        state: str = Query("all", description="all | active | archived"),
        search: Optional[str] = Query(None),
        sort: str = Query("deadline_asc"),
        department: Optional[str] = Query(None),
        stage_count: Optional[int] = Query(None, ge=1, le=10),
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)):
    actor = _actor(db, current_user_id)
    project = _load_project(db, project_id)
    _require_view(db, actor, project)

    query = db.query(Task).options(joinedload(Task.stage_details)).filter(Task.project_id == project_id)
    # видны все задачи проекта; открыть — только доступные (can_open)
    allowed = {tid for (tid,) in _visible_tasks_query(db, current_user_id, db.query(Task.id).filter(Task.project_id == project_id)).all()}
    if state == "active":
        query = query.filter(Task.activity == "Активная")
    elif state == "archived":
        query = query.filter(Task.activity == "Архивная")
    if search:
        query = query.filter(Task.name.contains(search.strip()))
    if department:
        query = query.filter(Task.stage_details.any(TaskStageDetails.department_name == department))
    if stage_count is not None:
        query = query.filter(Task.total_stages == stage_count)

    if sort not in TASK_SORTS:
        sort = "deadline_asc"
    direction = desc if sort.endswith("desc") else asc
    if sort.startswith("deadline"):
        query = query.order_by(Task.deadline.is_(None), direction(Task.deadline), asc(Task.id))
    elif sort.startswith("created"):
        query = query.order_by(direction(Task.creation_date))
    elif sort.startswith("name"):
        query = query.order_by(direction(Task.name))
    else:
        query = query.order_by(direction(Task.id))

    return [dict(_task_row(t), can_open=t.id in allowed) for t in query.all()]


# ── фото (файлы задач проекта по папкам) ─────────────────────────────────────

@router.get("/projects/{project_id}/photos", response_model=List[ProjectPhotoFolder])
def project_photos(
        project_id: int,
        search: Optional[str] = Query(None, description="По названию задачи"),
        deadline_from: Optional[date] = Query(None),
        deadline_to: Optional[date] = Query(None),
        state: str = Query("all"),
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)):
    actor = _actor(db, current_user_id)
    project = _load_project(db, project_id)
    _require_view(db, actor, project)

    query = db.query(Task).options(
        joinedload(Task.task_files).joinedload(TaskFile.uploaded_by),
        joinedload(Task.stage_details).joinedload(TaskStageDetails.files).joinedload(TaskStageFile.uploaded_by),
    ).filter(Task.project_id == project_id)
    query = _visible_tasks_query(db, current_user_id, query)
    if state == "active":
        query = query.filter(Task.activity == "Активная")
    elif state == "archived":
        query = query.filter(Task.activity == "Архивная")
    if search:
        query = query.filter(Task.name.contains(search.strip()))
    if deadline_from:
        query = query.filter(Task.deadline >= deadline_from)
    if deadline_to:
        query = query.filter(Task.deadline <= deadline_to)
    query = query.order_by(Task.deadline.is_(None), asc(Task.deadline), asc(Task.id))

    full = user_has_full_task_access(db, current_user_id)
    mine = (getattr(actor, "department", "") or "").strip()
    folders = []
    for task in query.all():
        files = []
        for f in task.task_files or []:
            if media.is_media_name(f.filename) or media.is_video_name(f.filepath):
                files.append({"id": f.id, "filename": f.filename, "size": f.size,
                              "extension": f.extension, "created_at": f.created_at,
                              "is_stage_file": False, "stage_number": None,
                              "uploaded_by": _short(f.uploaded_by)})
        for stage in sorted(task.stage_details or [], key=lambda s: s.stage_number):
            if not full and (stage.department_name or "").strip() != mine:
                continue   # этап другого отдела — его файлы закрыты, как и сам этап
            for f in stage.files or []:
                if media.is_media_name(f.filename) or media.is_video_name(f.filepath):
                    files.append({"id": f.id, "filename": f.filename, "size": f.size,
                                  "extension": f.extension, "created_at": f.created_at,
                                  "is_stage_file": True, "stage_number": stage.stage_number,
                                  "uploaded_by": _short(f.uploaded_by)})
        files.sort(key=lambda x: (x["created_at"] or datetime.min, x["id"]))
        folders.append({"task_id": task.id, "task_name": task.name, "deadline": task.deadline,
                        "activity": task.activity, "files": files})
    return folders


# ── файлы разделов ───────────────────────────────────────────────────────────

@router.get("/projects/{project_id}/files", response_model=List[ProjectFileOut])
def project_files(project_id: int, section: str = Query(...),
                        db: Session = Depends(get_db),
                        current_user_id: int = Depends(get_current_user_id)):
    actor = _actor(db, current_user_id)
    project = _load_project(db, project_id)
    _require_view(db, actor, project)
    section = _section(section)
    files = db.query(ProjectFile).options(joinedload(ProjectFile.uploaded_by)).filter(
        ProjectFile.project_id == project_id, ProjectFile.section == section
    ).order_by(asc(ProjectFile.created_at), asc(ProjectFile.id)).all()
    return files


@router.post("/projects/{project_id}/files", response_model=List[ProjectFileOut])
async def upload_project_files(
        project_id: int,
        files: List[UploadFile] = File(None),
        section: str = Form(...),
        thumbs: List[UploadFile] = File(None),
        meta: Optional[str] = Form(None),
        upload_id: Optional[str] = Form(None),
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)):
    actor = _actor(db, current_user_id)
    project = _load_project(db, project_id)
    if not can_manage_project_files(db, actor, project):
        raise HTTPException(status_code=403, detail="Нет прав на загрузку файлов проекта")
    section = _section(section)

    folder = Path(FILE_STORAGE_ROOT) / _project_folder(project_id, section)
    created = []
    created_paths = []
    try:
        from .. import intake, upload_queue as queue
        items = intake.parse_items(files, thumbs, meta)
        if not items:
            raise HTTPException(status_code=400, detail="Нет файлов для загрузки")
        queue.start(upload_id, [intake.describe(i) for i in items])
        infos = []
        for index, item in enumerate(items):
            stored, info = await intake.store_item(_project_folder(project_id, section), item, upload_id, index,
                                                   None, current_user_id, db)
            infos.append(info)
            created_paths.append(stored.relative_path)
            row = ProjectFile(project_id=project_id, section=section,
                              filename=stored.display_name, filepath=stored.relative_path,
                              size=stored.size, extension=stored.extension,
                              uploaded_by_id=current_user_id, created_at=datetime.now())
            db.add(row)
            created.append(row)
        db.flush()
        for row in created:
            db.refresh(row)
        db.commit()
        for row, info in zip(created, infos):
            intake.apply_meta(row, info)
    except HTTPException:
        db.rollback()
        from ..storage import delete_file_from_disk
        for path in created_paths:
            delete_file_from_disk(path)
        raise
    except Exception as e:
        db.rollback()
        from ..storage import delete_file_from_disk
        for path in created_paths:
            delete_file_from_disk(path)
        raise HTTPException(status_code=500, detail=f"Ошибка при загрузке файлов проекта: {e}")

    rows = db.query(ProjectFile).options(joinedload(ProjectFile.uploaded_by)).filter(
        ProjectFile.id.in_([r.id for r in created])).all()
    for row in rows:
        await _broadcast_all({"type": "project.file_added", "project_id": project_id,
                              "section": section, "file_id": row.id, "filename": row.filename,
                              "sender_id": current_user_id})
    return rows


def _load_project_file(db: Session, file_id: int) -> ProjectFile:
    row = db.query(ProjectFile).options(joinedload(ProjectFile.uploaded_by)).filter(
        ProjectFile.id == file_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Файл проекта не найден")
    return row


def _effective_name(row: ProjectFile) -> str:
    suffix = Path(str(row.filepath or "")).suffix.lower()
    if suffix in {".webm", ".mp4"}:
        return f"{Path(row.filename).stem}{suffix}"
    return row.filename


@router.delete("/projects/files/{file_id}")
async def delete_project_file(file_id: int, db: Session = Depends(get_db),
                              current_user_id: int = Depends(get_current_user_id)):
    actor = _actor(db, current_user_id)
    row = _load_project_file(db, file_id)
    project = _load_project(db, row.project_id)
    if not can_manage_project_files(db, actor, project):
        raise HTTPException(status_code=403, detail="Нет прав на удаление файлов проекта")
    quarantine = []
    try:
        quarantine = quarantine_files_for_deletion([row.filepath])
        project_id, section = row.project_id, row.section
        db.delete(row)
        db.commit()
        purge_quarantined_files(quarantine)
    except Exception as e:
        db.rollback()
        restore_quarantined_files(quarantine)
        raise HTTPException(status_code=500, detail=f"Ошибка при удалении файла: {e}")
    await _broadcast_all({"type": "project.file_deleted", "project_id": project_id,
                          "section": section, "file_id": file_id, "sender_id": current_user_id})
    return {"message": "Файл удалён"}


@router.get("/projects/files/{file_id}/download")
def download_project_file(file_id: int, request: Request, db: Session = Depends(get_db),
                                current_user_id: int = Depends(get_current_user_id_from_header_or_query)):
    actor = _actor(db, current_user_id)
    row = _load_project_file(db, file_id)
    _require_view(db, actor, _load_project(db, row.project_id))
    name = _effective_name(row)
    if media.is_video_name(name):
        return get_decompressed_file_response_with_range(request, row.filepath, name, download=True)
    return get_decompressed_file_response(row.filepath, name, download=True, request=request)


@router.get("/projects/files/{file_id}/view")
def view_project_file(file_id: int, request: Request, db: Session = Depends(get_db),
                            current_user_id: int = Depends(get_current_user_id_from_header_or_query)):
    actor = _actor(db, current_user_id)
    row = _load_project_file(db, file_id)
    _require_view(db, actor, _load_project(db, row.project_id))
    name = _effective_name(row)
    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if media.is_video_name(name):
        return get_decompressed_file_response_with_range(request, row.filepath, name, download=False)
    if not any(content_type.startswith(t) for t in ("image/", "text/", "application/pdf")):
        # документ превращаем в PDF на сервере — как в мессенджерах
        preview = office_preview_response(row.filepath, name)
        if preview is not None:
            return preview
        raise HTTPException(status_code=415,
                            detail="Этот тип файла нельзя показать в браузере — файл можно скачать")
    return get_decompressed_file_response(row.filepath, name, download=False, request=request)


@router.get("/projects/files/{file_id}/sheet")
def sheet_project_file(file_id: int, db: Session = Depends(get_db),
                             current_user_id: int = Depends(get_current_user_id_from_header_or_query)):
    """Таблица проекта настоящими ячейками — разбор тот же, что у файлов задач"""
    actor = _actor(db, current_user_id)
    row = _load_project_file(db, file_id)
    _require_view(db, actor, _load_project(db, row.project_id))
    name = _effective_name(row)
    try:
        stored = resolve_stored_path(row.filepath)
    except ValueError:
        raise HTTPException(status_code=400, detail="Некорректный путь к файлу")
    data = sheet_json(stored, name)
    if data is None:
        raise HTTPException(status_code=415, detail="Этот файл не удалось прочитать как таблицу")
    return data


@router.get("/projects/files/{file_id}/stream/{filename:path}")
def stream_project_file(file_id: int, filename: str, request: Request,
                              db: Session = Depends(get_db),
                              current_user_id: int = Depends(get_current_user_id_from_header_or_query)):
    actor = _actor(db, current_user_id)
    row = _load_project_file(db, file_id)
    _require_view(db, actor, _load_project(db, row.project_id))
    name = _effective_name(row)
    if not media.is_video_name(name):
        raise HTTPException(status_code=400, detail="Файл не является видео")
    return get_decompressed_file_response_with_range(request, row.filepath, name, download=False)


@router.get("/projects/files/{file_id}/thumb")
def project_file_thumbnail(file_id: int, db: Session = Depends(get_db),
                                 current_user_id: int = Depends(get_current_user_id_from_header_or_query)):
    actor = _actor(db, current_user_id)
    row = _load_project_file(db, file_id)
    _require_view(db, actor, _load_project(db, row.project_id))
    try:
        stored = resolve_stored_path(row.filepath)
    except ValueError:
        raise HTTPException(status_code=400, detail="Некорректный путь к файлу")
    thumb = ensure_thumbnail(stored, _effective_name(row))
    if thumb is None:
        raise HTTPException(status_code=404, detail="Миниатюра недоступна")
    return FileResponse(str(thumb), media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=86400",
                                 "X-Content-Type-Options": "nosniff"})
