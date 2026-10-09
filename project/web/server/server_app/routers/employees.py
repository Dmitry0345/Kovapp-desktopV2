# -*- coding: utf-8 -*-

from typing import List, Optional, Dict, Literal
import bcrypt
import base64
from fastapi import FastAPI, Depends, HTTPException, status, Query, Response, UploadFile, File, Form, WebSocket, \
    WebSocketDisconnect
from sqlalchemy.orm import Session
from sqlalchemy import desc, text, func, true, false

from server.database.models import *

from fastapi import APIRouter

from ..config import logger
from .. import media
from ..realtime import manager
from ..core import (EMPLOYEE_STATUSES, STATUS_WORKER, get_current_user_id,
                    get_current_user_id_from_header_or_query, get_db, is_admin_user)
from ..employee_cleanup import detach_employee_references, ensure_nullable_author_columns
from ..photos import MAX_PHOTO_BYTES, _detect_image_mime, _load_employee_photo, _photo_column_info
from ..schemas import EmployeeCreate, EmployeeOut, EmployeeUpdate


router = APIRouter()


async def _notify_employees(kind: str, employee_id: int, sender_id: int):
    """Справочник сотрудников открыт у всех — изменения рассылаем сразу."""
    try:
        await manager.broadcast({"type": kind, "employee_id": employee_id,
                                 "sender_id": sender_id})
    except Exception as e:
        logger.debug("Справочник сотрудников: рассылка не ушла: %s", e)



@router.get("/employees", response_model=list[EmployeeOut])
def get_employees(
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    try:
        employees = db.query(Employee).all()
        return employees
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при получении списка сотрудников: {str(e)}"
        )


@router.get("/employees/departments", response_model=List[str])
def get_unique_departments(
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    try:
        departments = db.query(Employee.department).distinct().all()
        return [dept[0] for dept in departments if dept[0] is not None]
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при получении списка отделов: {str(e)}"
        )


@router.get("/employees/positions", response_model=List[str])
def get_unique_positions(
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    try:
        positions = db.query(Employee.position).distinct().all()
        return [pos[0] for pos in positions if pos[0] is not None]
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при получении списка должностей: {str(e)}"
        )


@router.get("/employees/{employee_id}", response_model=EmployeeOut)
def get_employee(
        employee_id: int,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    employee = db.query(Employee).filter(Employee.id == employee_id).first()
    if not employee:
        raise HTTPException(
            status_code=404,
            detail=f"Сотрудник с ID {employee_id} не найден"
        )
    return employee


@router.post("/employees/{employee_id}/photo")
async def upload_employee_photo(
        employee_id: int,
        file: UploadFile = File(...),
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id),
        is_admin: bool = Depends(is_admin_user)
):
    """Сохраняет фотографию сотрудника.

    Свою фотографию меняет сам сотрудник, чужую — только администратор.
    """
    if current_user_id != employee_id and not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Менять чужую фотографию может только администратор"
        )

    employee = db.query(Employee).filter(Employee.id == employee_id).first()
    if not employee:
        raise HTTPException(
            status_code=404,
            detail=f"Сотрудник с ID {employee_id} не найден"
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Файл пустой")

    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Файл слишком большой: {len(data)} байт при пределе {MAX_PHOTO_BYTES}"
        )

    if _detect_image_mime(data) == "application/octet-stream":
        raise HTTPException(
            status_code=400,
            detail="Поддерживаются только изображения PNG, JPEG, GIF, BMP и WEBP"
        )

    # Аватар тоже пережимаем: в колонке Photo лежал исходник с телефона,
    # из-за чего он не влезал в BLOB и грузился медленно
    try:
        data, _ = media.optimize_image(data, "." + (_detect_image_mime(data).split("/")[-1] or "jpg"))
    except Exception:
        logger.exception("Не удалось пережать фотографию — сохраняем как есть")

    is_binary, max_length = _photo_column_info(db)
    payload = data if is_binary else base64.b64encode(data).decode("ascii")

    if max_length is not None and len(payload) > max_length:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Фотография не помещается в колонку Photo: нужно {len(payload)}, "
                f"а колонка вмещает {max_length}. Увеличьте тип колонки "
                f"(например, до MEDIUMBLOB) или загрузите изображение меньше."
            )
        )

    try:
        db.execute(
            text("UPDATE employees SET `Photo` = :photo WHERE id = :employee_id"),
            {"photo": payload, "employee_id": employee_id},
        )
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при сохранении фотографии: {str(e)}"
        )
    await _notify_employees("employee.photo", employee_id, current_user_id)

    return {
        "status": "ok",
        "size": len(data),
        "content_type": _detect_image_mime(data),
    }


@router.get("/employees/photo/ids")
def get_employee_ids_with_photo(
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    """
    Список id сотрудников, у которых есть фотография.

    Клиент запрашивает его один раз перед показом списка сотрудников и
    загружает изображения только для тех, у кого они действительно есть.
    """
    try:
        rows = db.execute(text(
            "SELECT id FROM employees WHERE `Photo` IS NOT NULL AND LENGTH(`Photo`) > 0"
        )).all()
        return {"ids": [int(r[0]) for r in rows]}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при получении списка фотографий: {str(e)}"
        )


@router.get("/employees/{employee_id}/photo")
def get_employee_photo(
        employee_id: int,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id_from_header_or_query)
):
    """Отдаёт фотографию сотрудника из БД."""
    try:
        photo = _load_employee_photo(db, employee_id)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при получении фотографии сотрудника: {str(e)}"
        )

    if not photo:
        raise HTTPException(
            status_code=404,
            detail=f"Фотография сотрудника с ID {employee_id} не найдена"
        )

    return Response(
        content=photo,
        media_type=_detect_image_mime(photo),
        headers={"Cache-Control": "no-cache"},
    )


@router.delete("/employees/{employee_id}")
async def delete_employee(
        employee_id: int,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id),
        is_admin: bool = Depends(is_admin_user)
):
    if not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Только администратор может удалять сотрудников"
        )

    if current_user_id == employee_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Нельзя удалить самого себя"
        )

    try:
        employee = db.query(Employee).filter(Employee.id == employee_id).first()
        if not employee:
            raise HTTPException(status_code=404, detail="Сотрудник не найден")

        employee_name = f"{employee.last_name or ''} {employee.first_name or ''}".strip()

        # DDL в MySQL неявно фиксирует транзакцию, поэтому миграция колонок
        # выполняется до начала работы с данными
        ensure_nullable_author_columns(db)

        detach_employee_references(db, employee_id)

        db.commit()
        await _notify_employees("employee.deleted", employee_id, current_user_id)

        return {
            "status": "success",
            "message": f"Сотрудник {employee_name} полностью удалён",
            "employee_name": employee_name,
            "employee_id": employee_id
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при удалении сотрудника: {str(e)}"
        )


def normalized_status(value, default=None):
    """Маркер прав из запроса. Чужое значение не принимаем: права решает он.

    Именно поэтому проверка строгая — опечатка или произвольная строка не
    должны молча превратиться в «работника» и тем более в «директора».
    """
    if value is None:
        return default
    clean = str(value).strip().lower()
    if clean in EMPLOYEE_STATUSES:
        return clean
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Статус должен быть одним из: " + ", ".join(EMPLOYEE_STATUSES))


@router.put("/employees/{employee_id}", response_model=EmployeeOut)
async def update_employee(
        employee_id: int,
        employee_data: EmployeeUpdate,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id),
        is_admin: bool = Depends(is_admin_user)
):
    if not is_admin:
        if employee_id != current_user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Только администратор может редактировать сотрудников"
            )
        fields_set = getattr(employee_data, "model_fields_set", None) or getattr(employee_data, "__fields_set__", set())
        extra = {name for name in fields_set if getattr(employee_data, name, None) is not None} - {"phone_number", "email"}
        if extra:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Изменить можно только свои телефон и почту"
            )

    try:
        db_employee = db.query(Employee).filter(Employee.id == employee_id).first()
        if not db_employee:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Сотрудник с ID {employee_id} не найден"
            )

        if employee_data.first_name is not None:
            db_employee.first_name = employee_data.first_name
        if employee_data.last_name is not None:
            db_employee.last_name = employee_data.last_name
        if employee_data.middle_name is not None:
            db_employee.middle_name = employee_data.middle_name
        if employee_data.position is not None:
            db_employee.position = employee_data.position
        if employee_data.status is not None:
            db_employee.status = normalized_status(employee_data.status)
        if employee_data.phone_number is not None:
            db_employee.phone_number = employee_data.phone_number
        if employee_data.email is not None:
            db_employee.email = employee_data.email
        if employee_data.profile is not None:
            db_employee.profile = employee_data.profile
        if employee_data.department is not None:
            db_employee.department = employee_data.department

        db.commit()
        await _notify_employees("employee.updated", employee_id, current_user_id)
        db.refresh(db_employee)
        return db_employee

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при обновлении сотрудника: {str(e)}"
        )


@router.post("/employees", response_model=EmployeeOut)
async def create_employee(
        employee_data: EmployeeCreate,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id),
        is_admin: bool = Depends(is_admin_user)
):
    if not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Только администратор может создавать работников"
        )
    try:
        hashed_password = bcrypt.hashpw(employee_data.password.encode('utf-8'), bcrypt.gensalt())
        users = db.query(User).all()
        name = employee_data.username
        names_list = [user.username for user in users]
        if employee_data.username in names_list:
            max_num = 0
            for username in names_list:
                if username.startswith("user"):
                    try:
                        num = int(username[4:])
                        if num > max_num:
                            max_num = num
                    except ValueError:
                        continue
            name = f"user{max_num + 1}"
        db_user = User(
            username=name,
            password=hashed_password.decode('utf-8'),
            employee=Employee(
                first_name=employee_data.first_name,
                last_name=employee_data.last_name,
                middle_name=employee_data.middle_name,
                position=employee_data.position,
                status=normalized_status(employee_data.status, STATUS_WORKER),
                phone_number=employee_data.phone_number,
                email=employee_data.email,
                profile=employee_data.profile,
                department=employee_data.department
            )
        )

        db.add(db_user)
        db.commit()
        await _notify_employees("employee.created", db_user.id, current_user_id)
        db.refresh(db_user)

        return db_user.employee
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail=f"Ошибка при создании сотрудника: {str(e)}"
        )
