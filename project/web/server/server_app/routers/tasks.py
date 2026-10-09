# -*- coding: utf-8 -*-

from datetime import timedelta, datetime, date
from pathlib import Path
from typing import List, Optional, Dict, Literal
from sqlalchemy import or_, and_, func, select
from fastapi import FastAPI, Depends, HTTPException, status, Query, Response, UploadFile, File, Form, WebSocket, \
    WebSocketDisconnect
from sqlalchemy.orm import noload, selectinload, Session
from sqlalchemy.orm import joinedload

from server.database.models import *

from fastapi import APIRouter

from .. import task_journal as journal
from ..core import (get_current_user_id, get_db, is_admin_user, user_can_access_task,
                    user_can_create_tasks, user_has_full_task_access,
                    user_is_department_head_for_task)
from ..helpers import TASK_SORT_DEFAULT_ACTIVE, TASK_SORT_DEFAULT_ARCHIVED, apply_user_task_filter
from ..config import FILE_STORAGE_ROOT, logger
from ..realtime import manager
from ..storage import (get_task_folder_name, purge_quarantined_files,
                       quarantine_files_for_deletion, restore_quarantined_files)
from ..schemas import TaskCreate, TaskFilterOut, TaskFilterUpdate, TaskListLightOut, TaskUpdate, TaskWithRelationsOut
from ..project_permissions import ensure_project_exists
from .. import stage_leads as leads


router = APIRouter()

OWN_DEPARTMENT_ONLY = ("Руководитель отдела заводит задачу с одним этапом своего отдела — "
                       "другие отделы подключаются продлением задачи")


def _own_department(db: Session, user_id: int) -> str:
    employee = db.query(Employee).filter(Employee.id == user_id).first()
    return ((employee.department if employee else "") or "").strip()


def _ensure_own_department_stages(db: Session, user_id: int, departments) -> None:
    names = [(d or "").strip() for d in departments]
    own = _own_department(db, user_id)
    if len(names) > 1 or any(not own or name != own for name in names):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=OWN_DEPARTMENT_ONLY)


def _filter_tasks(query, db, current_user_id, is_admin, is_archived, search_text, search_mode):
    """Общие условия списка задач: архив, доступ, личный фильтр, поиск."""
    if is_archived is not None:
        if is_archived:
            query = query.filter(Task.activity == "Архивная")
        else:
            query = query.filter(Task.activity == "Активная")

    # join(Task.assigned_employees) здесь был лишним: INNER JOIN отсекал
    # задачи без исполнителей и дублировал строки. Условий any() достаточно
    if not (is_admin or user_has_full_task_access(db, current_user_id)):
        query = query.filter(
            or_(
                Task.creator_id == current_user_id,
                Task.assigned_employees.any(id=current_user_id),
                Task.responsible_persons.any(id=current_user_id)
            )
        )

    query = apply_user_task_filter(query, db, current_user_id, is_archived)

    if search_text:
        if search_mode == "contains":
            query = query.filter(Task.name.contains(search_text))
        elif search_mode == "startswith":
            query = query.filter(Task.name.startswith(search_text))
        elif search_mode == "endswith":
            query = query.filter(Task.name.endswith(search_text))
    return query


def _deadline_not_past(value) -> None:
    """При создании срок — не раньше сегодняшнего дня."""
    if value is None:
        return
    from datetime import date as _d, datetime as _dt
    day = value.date() if isinstance(value, _dt) else value
    if isinstance(day, _d) and day < _dt.now().date():
        raise HTTPException(status_code=400, detail="Дата окончания не может быть раньше сегодняшнего дня")


def _project_closed(db: Session, project_id) -> None:
    """В завершённом проекте задачи не создаются."""
    if not project_id:
        return
    from server.database.models import Project as _Project
    if db.query(_Project.is_archived).filter(_Project.id == project_id).scalar():
        raise HTTPException(status_code=409, detail="Проект завершён — действия недоступны. Сначала верните его в работу.")

@router.get("/tasks", response_model=List[TaskWithRelationsOut])
def get_tasks(
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id),
        is_admin: bool = Depends(is_admin_user),
        is_archived: bool = Query(False, description="Фильтр по архивным задачам"),
        search_text: Optional[str] = Query(None),
        search_mode: Optional[str] = Query("contains")
):
    try:
        # этапы, их участники и файлы, файлы задачи и авторы файлов — по одному запросу на всё,
        # а не по нескольку на каждую задачу при сборке ответа
        # коллекции (ответственные, исполнители) — отдельными запросами: через JOIN строки задач
        # размножались на каждую комбинацию, и база просматривала тысячи строк на один список
        query = db.query(Task).options(
            selectinload(Task.responsible_persons),
            selectinload(Task.assigned_employees),
            joinedload(Task.creator),
            joinedload(Task.who_archived_employee),
            joinedload(Task.project),
            selectinload(Task.stage_details).selectinload(TaskStageDetails.participants),
            selectinload(Task.stage_details).selectinload(TaskStageDetails.files).joinedload(TaskStageFile.uploaded_by),
            selectinload(Task.task_files).joinedload(TaskFile.uploaded_by),
        )

        query = _filter_tasks(query, db, current_user_id, is_admin, is_archived, search_text, search_mode)

        tasks = query.all()
        return tasks
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при получении задач: {str(e)}"
        )


@router.get("/tasks/light", response_model=List[TaskListLightOut])
def get_tasks_light(
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id),
        is_admin: bool = Depends(is_admin_user),
        is_archived: bool = Query(False, description="Фильтр по архивным задачам"),
        search_text: Optional[str] = Query(None),
        search_mode: Optional[str] = Query("contains")
):
    """Список задач для браузера: без списков файлов, только их число.

    Полный /tasks отдаёт у каждой задачи все её файлы — у задачи с сотней фото
    это сотня записей в каждом ответе. Браузеру в списке нужны этапы, а файлы
    он берёт при открытии задачи. Настольное приложение пользуется /tasks, как раньше.
    """
    try:
        query = db.query(Task).options(
            selectinload(Task.responsible_persons),
            selectinload(Task.assigned_employees),
            joinedload(Task.creator),
            joinedload(Task.who_archived_employee),
            joinedload(Task.project),
            selectinload(Task.stage_details).selectinload(TaskStageDetails.participants),
            selectinload(Task.stage_details).noload(TaskStageDetails.files),
            noload(Task.task_files),
        )
        query = _filter_tasks(query, db, current_user_id, is_admin, is_archived, search_text, search_mode)
        tasks = query.all()
        task_ids = [t.id for t in tasks]
        stage_ids = [st.id for t in tasks for st in (t.stage_details or [])]
        stage_counts = dict(db.query(TaskStageFile.stage_detail_id, func.count(TaskStageFile.id)).filter(
            TaskStageFile.stage_detail_id.in_(stage_ids)).group_by(TaskStageFile.stage_detail_id).all()) if stage_ids else {}
        task_counts = dict(db.query(TaskFile.task_id, func.count(TaskFile.id)).filter(
            TaskFile.task_id.in_(task_ids)).group_by(TaskFile.task_id).all()) if task_ids else {}
        for t in tasks:
            t.task_files_count = task_counts.get(t.id, 0)
            for st in (t.stage_details or []):
                st.files_count = stage_counts.get(st.id, 0)
        return tasks
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при получении задач: {str(e)}"
        )


def _remove_task_folder(task_id: int, task_name: str) -> None:
    """Убирает папку задачи из хранилища, если она опустела."""
    try:
        folder = Path(FILE_STORAGE_ROOT) / get_task_folder_name(task_id, task_name)
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    except Exception:
        logger.exception(f"Не удалось удалить папку задачи {task_id}")


@router.delete("/tasks/{task_id}")
async def delete_task(
        task_id: int,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id),
        is_admin: bool = Depends(is_admin_user)
):
    task = db.query(Task).options(
        joinedload(Task.stage_details)
    ).filter(Task.id == task_id).first()

    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Задача с ID {task_id} не найдена"
        )

    if not (is_admin or user_is_department_head_for_task(db, current_user_id, task)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Удалять задачу из может только администратор или "
                   "руководитель отдела, к которому относятся все этапы задачи"
        )
    quarantine = []
    try:
        if not task:
            raise HTTPException(status_code=404, detail="Задача не найдена")

        # Получателей и пути к файлам собираем ДО удаления: после commit
        # участников уже нет, и рассылать оповещение стало бы некому
        task_name = task.name
        recipient_ids = set()
        if task.creator_id:
            recipient_ids.add(task.creator_id)
        for employee in list(task.responsible_persons):
            recipient_ids.add(employee.id)
        for employee in list(task.assigned_employees):
            recipient_ids.add(employee.id)

        stored_paths = [f.filepath for f in
                        db.query(TaskFile).filter(TaskFile.task_id == task_id).all()
                        if f.filepath]
        stage_id_list = [stage.id for stage in task.stage_details]
        if stage_id_list:
            stored_paths += [f.filepath for f in
                             db.query(TaskStageFile).filter(
                                 TaskStageFile.stage_detail_id.in_(stage_id_list)).all()
                             if f.filepath]
        quarantine = quarantine_files_for_deletion(stored_paths)

        db.execute(task_stage_participants.delete().where(
            task_stage_participants.c.stage_detail_id.in_(
                select(TaskStageDetails.id).where(TaskStageDetails.task_id == task_id)
            )
        ))
        stage_ids = [stage.id for stage in task.stage_details]

        if stage_ids:
            db.query(TaskStageTextSpan).filter(
                TaskStageTextSpan.stage_detail_id.in_(stage_ids)
            ).delete(synchronize_session=False)

        for stage in task.stage_details:
            db.query(TaskStageFile).filter(TaskStageFile.stage_detail_id == stage.id).delete()

        db.query(TaskStageDetails).filter(TaskStageDetails.task_id == task_id).delete()
        db.expire(task, ["stage_details"])

        db.query(TaskFile).filter(TaskFile.task_id == task_id).delete()

        db.execute(task_responsible_associations.delete().where(
            task_responsible_associations.c.task_id == task_id
        ))
        db.expire(task, ["responsible_persons"])
        db.execute(TaskAssignment.__table__.delete().where(
            TaskAssignment.__table__.c.task_id == task_id
        ))
        db.expire(task, ["assigned_employees"])

        db.query(Notification).filter(
            Notification.target_section == "task_view",
            Notification.target_id == task_id
        ).delete()

        # журнал правок: каскад в базе есть, но полагаться на него не станем
        db.query(TaskChangeLog).filter(TaskChangeLog.task_id == task_id).delete()

        db.delete(task)
        db.commit()

        purge_quarantined_files(quarantine)
        _remove_task_folder(task_id, task_name)

        # Рассылаем по заранее собранному списку — задачи в базе уже нет
        for user_id in recipient_ids:
            try:
                await manager.send_personal_message({
                    "type": "task.deleted",
                    "task_id": task_id
                }, user_id)
            except Exception:
                logger.exception(f"Не удалось оповестить пользователя {user_id} об удалении задачи {task_id}")

        await announce_tasks_changed("deleted", task_id)
        return {"message": f"Задача с ID {task_id} и все связанные элементы успешно удалены"}
    except HTTPException:
        db.rollback()
        restore_quarantined_files(quarantine)
        raise
    except Exception as e:
        db.rollback()
        restore_quarantined_files(quarantine)
        raise HTTPException(status_code=500, detail=f"Ошибка при удалении задачи: {str(e)}")


@router.post("/tasks/{task_id}/archive", response_model=TaskWithRelationsOut)
async def archive_task(
        task_id: int,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id),
        is_admin: bool = Depends(is_admin_user)
):
    try:
        db_task = db.query(Task).options(
            joinedload(Task.stage_details)
        ).filter(Task.id == task_id).first()

        if not db_task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Задача с ID {task_id} не найдена"
            )

        if not (is_admin or user_is_department_head_for_task(db, current_user_id, db_task)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Завершать задачу может только администратор или руководитель "
                       "отдела, к которому относятся все этапы задачи"
            )

        db_task.archived_at = datetime.now()
        db_task.activity = "Архивная"
        db_task.who_archive = current_user_id
        db.commit()
        db.refresh(db_task)

        db_task = db.query(Task).options(
            joinedload(Task.responsible_persons),
            joinedload(Task.assigned_employees),
            joinedload(Task.creator)
        ).filter(Task.id == task_id).first()
        try:
            await manager.broadcast_to_task_participants(db_task.id, {
                "type": "task.archived", "task_id": db_task.id
            }, db)
        except Exception as e:
            logger.debug("Задачи: рассылка об отправке в архив не ушла: %s", e)
        await announce_tasks_changed("archived", db_task.id)
        return db_task

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при архивировании задачи: {str(e)}"
        )


@router.post("/tasks/{task_id}/unarchive", response_model=TaskWithRelationsOut)
async def unarchive_task(
        task_id: int,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id),
        is_admin: bool = Depends(is_admin_user)
):
    try:
        db_task = db.query(Task).options(
            joinedload(Task.stage_details)
        ).filter(Task.id == task_id).first()

        if not db_task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Задача с ID {task_id} не найдена"
            )

        if not (is_admin or user_is_department_head_for_task(db, current_user_id, db_task)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Возвращать задачу из архива может только администратор или "
                       "руководитель отдела, к которому относятся все этапы задачи"
            )

        db_task.archived_at = None
        if db_task.project_archived:   # проект завершён — сначала вернуть в работу его
            raise HTTPException(status_code=400, detail="Задача относится к завершённому проекту — сначала верните проект в работу")
        db_task.activity = "Активная"
        db_task.who_archive = None
        db.commit()
        db.refresh(db_task)

        db_task = db.query(Task).options(
            joinedload(Task.responsible_persons),
            joinedload(Task.assigned_employees),
            joinedload(Task.creator)
        ).filter(Task.id == task_id).first()
        try:
            await manager.broadcast_to_task_participants(db_task.id, {
                "type": "task.unarchived", "task_id": db_task.id
            }, db)
        except Exception as e:
            logger.debug("Задачи: рассылка о возврате из архива не ушла: %s", e)
        await announce_tasks_changed("unarchived", db_task.id)
        return db_task

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при возврате задачи из архива: {str(e)}"
        )


async def announce_tasks_changed(what: str, task_id: int = 0) -> None:
    """Лёгкий сигнал всем подключённым: список задач изменился.

    Раньше о новой задаче узнавали только её участники, и у остальных
    список молчал до перезагрузки. Сама задача сюда не кладётся: клиент
    перечитает список сам и покажет ровно то, что ему положено видеть.
    """
    try:
        await manager.broadcast({"type": "tasks.changed", "what": what, "task_id": task_id})
    except Exception as e:
        logger.debug("Не удалось разослать сигнал об изменении задач: %s", e)


# ── журнал правок задачи ─────────────────────────────────────────────────

# Подписи живут в общем модуле: журнал теперь пишут и файлы, и этапы,
# и держать два списка врозь — верный способ их рассинхронизировать
TASK_LOG_TITLES = journal.TITLES


def _people_names(db: Session, ids) -> str:
    ids = [i for i in (ids or [])]
    if not ids:
        return "никого"
    rows = db.query(Employee).filter(Employee.id.in_(ids)).all()
    names = sorted(" ".join(x for x in [e.last_name, e.first_name] if x).strip() for e in rows)
    return ", ".join(names) if names else "никого"


def _log_task(db: Session, task_id: int, field: str, old, new, user_id: int) -> None:
    """Пишет строку журнала, если значение действительно изменилось."""
    old_s = "" if old is None else str(old)
    new_s = "" if new is None else str(new)
    if old_s == new_s:
        return
    db.add(TaskChangeLog(task_id=task_id, field=field, old_value=old_s[:500],
                         new_value=new_s[:500], user_id=user_id, created_at=datetime.now()))


@router.get("/tasks/{task_id}/history")
def task_history(task_id: int, db: Session = Depends(get_db),
                 current_user_id: int = Depends(get_current_user_id)):
    """Кто и когда менял задачу. Открывается кнопкой в шапке задачи."""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    if not user_can_access_task(db, current_user_id, task):
        raise HTTPException(status_code=403, detail="Задача недоступна: вы не входите в её состав")
    rows = db.query(TaskChangeLog).filter(TaskChangeLog.task_id == task_id) \
        .order_by(TaskChangeLog.created_at.desc(), TaskChangeLog.id.desc()).limit(200).all()
    people = {e.id: " ".join(x for x in [e.last_name, e.first_name] if x).strip()
              for e in db.query(Employee).filter(Employee.id.in_([r.user_id for r in rows if r.user_id] or [0])).all()}
    projects = {}
    project_ids = [r.old_value for r in rows if r.field == "project_id"] + \
                  [r.new_value for r in rows if r.field == "project_id"]
    project_ids = [int(x) for x in project_ids if x and str(x).isdigit()]
    if project_ids:
        projects = {str(p.id): p.name for p in db.query(Project).filter(Project.id.in_(project_ids)).all()}

    def show(field, value):
        if value in (None, ""):
            return "—"
        if field == "project_id":
            return projects.get(str(value), "проект №" + str(value))
        return str(value)

    return [{
        "field": r.field, "field_title": TASK_LOG_TITLES.get(r.field, r.field),
        "old": show(r.field, r.old_value), "new": show(r.field, r.new_value),
        "who": people.get(r.user_id) or "неизвестно",
        "at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]


@router.put("/tasks/{task_id}", response_model=TaskWithRelationsOut)
async def update_task(
        task_id: int,
        task_data: TaskUpdate,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id),
        is_admin: bool = Depends(is_admin_user)
):
    db_task = db.query(Task).options(
        joinedload(Task.stage_details)
    ).filter(Task.id == task_id).first()

    if not db_task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Задача с ID {task_id} не найдена"
        )

    if not (is_admin or user_is_department_head_for_task(db, current_user_id, db_task)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Редактировать задачу может только администратор или "
                   "руководитель отдела, к которому относятся все этапы задачи"
        )
    if task_data.stages is not None and not is_admin:
        known = {s.stage_number for s in db_task.stage_details}
        if {s.stage_number for s in task_data.stages} != known:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="Этапы добавляются продлением задачи, а удаляет их руководство")
        own = _own_department(db, current_user_id)
        if any(s.department_name is not None and s.department_name.strip() != own for s in task_data.stages):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=OWN_DEPARTMENT_ONLY)

    try:
        # Журнал: сравниваем со значениями, которые были до правки
        for field in ("name", "description", "deadline", "state", "activity", "customer_organization"):
            incoming = getattr(task_data, field, None)
            if incoming is not None:
                _log_task(db, task_id, field, getattr(db_task, field), incoming, current_user_id)

        if task_data.name is not None:
            db_task.name = task_data.name
        if task_data.description is not None:
            db_task.description = task_data.description
        if task_data.deadline is not None:
            db_task.deadline = task_data.deadline
        if task_data.state is not None:
            db_task.state = task_data.state
        if task_data.activity is not None:
            db_task.activity = task_data.activity
        if task_data.customer_organization is not None:
            db_task.customer_organization = task_data.customer_organization

        fields_set = getattr(task_data, "model_fields_set", None) or getattr(task_data, "__fields_set__", set())
        if "project_id" in fields_set:
            if task_data.project_id is not None:
                ensure_project_exists(db, task_data.project_id)
            _log_task(db, task_id, "project_id", db_task.project_id, task_data.project_id, current_user_id)
            db_task.project_id = task_data.project_id

        if task_data.responsible_persons_ids is not None:
            _log_task(db, task_id, "responsible",
                      _people_names(db, [e.id for e in db_task.responsible_persons]),
                      _people_names(db, task_data.responsible_persons_ids), current_user_id)
            db.execute(
                task_responsible_associations.delete().where(
                    task_responsible_associations.c.task_id == task_id
                )
            )
            for emp_id in task_data.responsible_persons_ids:
                db.execute(
                    task_responsible_associations.insert().values(
                        task_id=task_id,
                        employee_id=emp_id
                    )
                )

        if task_data.assigned_employees_ids is not None:
            _log_task(db, task_id, "assigned",
                      _people_names(db, [e.id for e in db_task.assigned_employees]),
                      _people_names(db, task_data.assigned_employees_ids), current_user_id)
            db.execute(
                TaskAssignment.__table__.delete().where(
                    TaskAssignment.__table__.c.task_id == task_id
                )
            )
            for emp_id in task_data.assigned_employees_ids:
                db.execute(
                    TaskAssignment.__table__.insert().values(
                        task_id=task_id,
                        employee_id=emp_id
                    )
                )

        if task_data.stages is not None:
            current_stages = {s.stage_number: s for s in db_task.stage_details}

            for stage_update in task_data.stages:
                stage = current_stages.get(stage_update.stage_number)

                if stage:
                    if stage_update.department_name is not None:
                        _log_task(db, task_id, "stage_department",
                                  f"этап {stage.stage_number}: {stage.department_name or '—'}",
                                  f"этап {stage.stage_number}: {stage_update.department_name}", current_user_id)
                        if (stage.department_name or "") != stage_update.department_name:
                            leads.set_leads(db, stage.id, [])
                        stage.department_name = stage_update.department_name
                    if stage_update.text_description is not None:
                        stage.text_description = stage_update.text_description
                    if stage_update.is_completed is not None:
                        _log_task(db, task_id, "stage_completed",
                                  f"этап {stage.stage_number}: {'завершён' if stage.is_completed else 'в работе'}",
                                  f"этап {stage.stage_number}: {'завершён' if stage_update.is_completed else 'в работе'}",
                                  current_user_id)
                        stage.is_completed = stage_update.is_completed
                else:
                    new_stage = TaskStageDetails(
                        task_id=task_id,
                        stage_number=stage_update.stage_number,
                        department_name=stage_update.department_name,
                        text_description=stage_update.text_description,
                        is_completed=stage_update.is_completed or False
                    )
                    db.add(new_stage)

            updated_stage_numbers = {s.stage_number for s in task_data.stages}
            for stage_number, stage in current_stages.items():
                if stage_number not in updated_stage_numbers:
                    db.delete(stage)

        db.commit()

        db_task = db.query(Task).options(
            joinedload(Task.responsible_persons),
            joinedload(Task.assigned_employees),
            joinedload(Task.creator),
            joinedload(Task.project),
            joinedload(Task.stage_details)
        ).filter(Task.id == task_id).first()
        try:
            await manager.broadcast_to_task_participants(db_task.id, {
                "type": "task.updated",
                "task_id": db_task.id,
                "title": db_task.name,
                "activity": db_task.activity,
                "description": db_task.description,
                "project_id": db_task.project_id,
                "sender_id": current_user_id
            }, db)
        except Exception as e:
            logger.debug("Не удалось разослать правку задачи участникам: %s", e)
        await announce_tasks_changed("updated", task_id)
        return db_task

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при обновлении задачи: {str(e)}"
        )


@router.get("/tasks/{task_id}", response_model=TaskWithRelationsOut)
def get_task(task_id: int, db: Session = Depends(get_db),
                   current_user_id: int = Depends(get_current_user_id),
                   is_admin: bool = Depends(is_admin_user)):
    task = db.query(Task).options(
        # Коллекции — отдельными запросами (selectinload): вложенные joinedload
        # перемножали строки участников, этапов и файлов в одну гигантскую выборку
        selectinload(Task.responsible_persons),
        selectinload(Task.assigned_employees),
        joinedload(Task.creator),
        joinedload(Task.who_archived_employee),
        joinedload(Task.project),
        selectinload(Task.stage_details).options(
            selectinload(TaskStageDetails.participants),
            selectinload(TaskStageDetails.files).joinedload(TaskStageFile.uploaded_by)
        ),
        selectinload(Task.task_files).joinedload(TaskFile.uploaded_by)
    ).filter(Task.id == task_id).first()

    if not task:
        raise HTTPException(
            status_code=404,
            detail=f"Задача с ID {task_id} не найдена"
        )

    if not (is_admin or user_can_access_task(db, current_user_id, task)):
        raise HTTPException(
            status_code=403,
            detail="Задача недоступна: вы не входите в её состав"
        )
    return task


def _stored_default_sort(is_archived: bool):
    """Что хранится в новом и сброшенном фильтре. Работающие задачи — без явной сортировки:
    такой список сервер отдаёт по важности, и так же его понимает приложение для ПК."""
    return TASK_SORT_DEFAULT_ARCHIVED if is_archived else None


def _sort_options(is_archived: bool) -> list:
    """Коды сортировки списка; priority — по важности (порядок по умолчанию для работающих задач)."""
    options = ["id_asc", "id_desc", "deadline_asc", "deadline_desc", "created_asc", "created_desc"]
    return options + (["archived_asc", "archived_desc"] if is_archived else [TASK_SORT_DEFAULT_ACTIVE])


@router.get("/task-filters/{is_archived}", response_model=TaskFilterOut)
def get_task_filter(
        is_archived: bool,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    try:
        task_filter = db.query(TaskFilter).filter(
            TaskFilter.user_id == current_user_id,
            TaskFilter.is_archived == is_archived
        ).first()

        if not task_filter:
            task_filter = TaskFilter(
                user_id=current_user_id,
                is_archived=is_archived,
                sort_by=_stored_default_sort(is_archived),
                created_at=datetime.now()
            )
            db.add(task_filter)
            db.commit()
            db.refresh(task_filter)

        task_filter.sort_options = _sort_options(is_archived)

        return task_filter
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при получении фильтра: {str(e)}"
        )


@router.put("/task-filters/{is_archived}", response_model=TaskFilterOut)
async def update_task_filter(
        is_archived: bool,
        filter_data: TaskFilterUpdate,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    try:
        task_filter = db.query(TaskFilter).filter(
            TaskFilter.user_id == current_user_id,
            TaskFilter.is_archived == is_archived
        ).first()

        if not task_filter:
            task_filter = TaskFilter(
                user_id=current_user_id,
                is_archived=is_archived,
                created_at=datetime.now()
            )
            db.add(task_filter)

        for field, value in filter_data.dict().items():
            setattr(task_filter, field, value)

        task_filter.updated_at = datetime.now()
        db.commit()
        db.refresh(task_filter)

        task_filter.sort_options = _sort_options(is_archived)

        return task_filter
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при обновлении фильтра: {str(e)}"
        )


@router.get("/task-filters/{is_archived}/is-default", response_model=bool)
def is_default_task_filter(
        is_archived: bool,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    try:
        task_filter = db.query(TaskFilter).filter(
            TaskFilter.user_id == current_user_id,
            TaskFilter.is_archived == is_archived
        ).first()

        if not task_filter:
            return True

        default_sort = TASK_SORT_DEFAULT_ARCHIVED if is_archived else TASK_SORT_DEFAULT_ACTIVE

        # без сортировки — тот же порядок по важности, что и выбранный явно
        sort_now = task_filter.sort_by or ("" if is_archived else TASK_SORT_DEFAULT_ACTIVE)
        is_default = (
                sort_now == default_sort and
                task_filter.stage_count is None and
                task_filter.min_created_date is None and
                task_filter.max_created_date is None
        )

        return is_default

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при проверке фильтра: {str(e)}"
        )


@router.post("/task-filters/{is_archived}/reset", response_model=TaskFilterOut)
async def reset_task_filter(
        is_archived: bool,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    try:
        task_filter = db.query(TaskFilter).filter(
            TaskFilter.user_id == current_user_id,
            TaskFilter.is_archived == is_archived
        ).first()

        if not task_filter:
            task_filter = TaskFilter(
                user_id=current_user_id,
                is_archived=is_archived
            )
            db.add(task_filter)

        task_filter.sort_by = _stored_default_sort(is_archived)
        task_filter.stage_count = None
        task_filter.min_created_date = None
        task_filter.max_created_date = None

        db.commit()
        db.refresh(task_filter)
        return task_filter
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при сбросе фильтра: {str(e)}"
        )


@router.post("/tasks", response_model=TaskWithRelationsOut, status_code=status.HTTP_201_CREATED)
async def create_task(
        task_data: TaskCreate,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id),
        is_admin: bool = Depends(is_admin_user)
):
    _deadline_not_past(task_data.deadline)
    _project_closed(db, getattr(task_data, 'project_id', None))
    # Раньше is_admin запрашивался, но не проверялся — задачу создавал любой
    if not (is_admin or user_can_create_tasks(db, current_user_id)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Создавать задачи могут старшие и главные сотрудники, директор и администратор")
    if not is_admin:
        _ensure_own_department_stages(db, current_user_id, [s.department_name for s in task_data.stages])
    try:
        all_employee_ids = set(task_data.responsible_persons_ids + task_data.assigned_employees_ids)

        if all_employee_ids:
            existing_employees = db.query(Employee.id).filter(Employee.id.in_(all_employee_ids)).all()
            existing_ids = {e.id for e in existing_employees}
            missing_ids = all_employee_ids - existing_ids

            if missing_ids:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Сотрудники с ID {missing_ids} не найдены"
                )

        if task_data.project_id is not None:
            ensure_project_exists(db, task_data.project_id)

        db_task = Task(
            name=task_data.name,
            description=task_data.description,
            deadline=task_data.deadline,
            state=TaskState.FIRST,
            activity="Активная",
            creator_id=current_user_id,
            creation_date=datetime.now(),
            customer_organization=task_data.customer_organization,
            project_id=task_data.project_id
        )

        db.add(db_task)
        db.flush()

        for emp_id in task_data.responsible_persons_ids:
            db.execute(
                task_responsible_associations.insert().values(
                    task_id=db_task.id,
                    employee_id=emp_id
                )
            )

        for emp_id in task_data.assigned_employees_ids:
            db.execute(
                TaskAssignment.__table__.insert().values(
                    task_id=db_task.id,
                    employee_id=emp_id
                )
            )

        for i, stage_data in enumerate(task_data.stages, start=1):
            db_stage = TaskStageDetails(
                task_id=db_task.id,
                stage_number=i,
                department_name=stage_data.department_name,
                text_description=stage_data.text_description,
                is_completed=False
            )
            db.add(db_stage)

        db.commit()
        db.refresh(db_task)

        db_task = db.query(Task).options(
            joinedload(Task.responsible_persons),
            joinedload(Task.assigned_employees),
            joinedload(Task.creator),
            joinedload(Task.project),
            joinedload(Task.stage_details)
        ).filter(Task.id == db_task.id).first()
        try:
            await manager.broadcast_to_task_participants(db_task.id, {
                "type": "task.created",
                "task_id": db_task.id,
                "title": db_task.name,
                "activity": db_task.activity,
                "creator_id": db_task.creator_id,
                "project_id": db_task.project_id
            }, db)
        except Exception as e:
            logger.debug("Задачи: рассылка о создании не ушла: %s", e)
        await announce_tasks_changed("created", db_task.id)
        return db_task

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при создании задачи: {str(e)}"
        )


@router.post("/tasks/from-template/{template_id}", response_model=TaskWithRelationsOut,
          status_code=status.HTTP_201_CREATED)
async def create_task_from_template(
        template_id: int,
        task_data: TaskCreate,
        responsible_persons_ids: List[int] = Query(None),
        assigned_employees_ids: List[int] = Query(None),
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id),
        is_admin: bool = Depends(is_admin_user)
):
    _deadline_not_past(task_data.deadline)
    _project_closed(db, getattr(task_data, 'project_id', None))
    if not (is_admin or user_can_create_tasks(db, current_user_id)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Создавать задачи могут старшие и главные сотрудники, директор и администратор")
    try:
        template = db.query(Template).filter(Template.id == template_id).first()
        if not template:
            raise HTTPException(
                status_code=404,
                detail=f"Шаблон с ID {template_id} не найден"
            )
        if not is_admin:
            planned = [d.strip() for d in (template.departments or "").split(",")]
            planned = [planned[i] if i < len(planned) else "" for i in range(template.num_stages or 0)]
            _ensure_own_department_stages(db, current_user_id, planned)

        all_employee_ids = set()
        if responsible_persons_ids:
            all_employee_ids.update(responsible_persons_ids)
        if assigned_employees_ids:
            all_employee_ids.update(assigned_employees_ids)

        if all_employee_ids:
            existing_employees = db.query(Employee.id).filter(Employee.id.in_(all_employee_ids)).all()
            existing_ids = {e.id for e in existing_employees}
            missing_ids = all_employee_ids - existing_ids

            if missing_ids:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Сотрудники с ID {missing_ids} не найдены"
                )

        if task_data.project_id is not None:
            ensure_project_exists(db, task_data.project_id)

        db_task = Task(
            name=task_data.name,
            description=task_data.description,
            deadline=task_data.deadline,
            state=TaskState.FIRST,
            activity="Активная",
            creator_id=current_user_id,
            creation_date=datetime.now(),
            customer_organization=task_data.customer_organization,
            project_id=task_data.project_id
        )

        db.add(db_task)
        db.flush()

        if responsible_persons_ids:
            for emp_id in responsible_persons_ids:
                db.execute(
                    task_responsible_associations.insert().values(
                        task_id=db_task.id,
                        employee_id=emp_id
                    )
                )

        if assigned_employees_ids:
            for emp_id in assigned_employees_ids:
                db.execute(
                    TaskAssignment.__table__.insert().values(
                        task_id=db_task.id,
                        employee_id=emp_id
                    )
                )

        departments = template.departments.split(',') if template.departments else []
        for i in range(1, template.num_stages + 1):
            department = departments[i - 1] if i <= len(departments) else None
            db_stage = TaskStageDetails(
                task_id=db_task.id,
                stage_number=i,
                department_name=department,
                text_description=f"Этап {i}",
                is_completed=False
            )
            db.add(db_stage)

        db.commit()
        db.refresh(db_task)

        db_task = db.query(Task).options(
            joinedload(Task.responsible_persons),
            joinedload(Task.assigned_employees),
            joinedload(Task.creator),
            joinedload(Task.stage_details)
        ).filter(Task.id == db_task.id).first()

        await announce_tasks_changed("created", db_task.id)
        return db_task

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при создании задачи из шаблона: {str(e)}"
        )
