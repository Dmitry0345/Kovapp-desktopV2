# -*- coding: utf-8 -*-

import csv
import secrets
import traceback
from io import StringIO, BytesIO
from datetime import timedelta, datetime, date
from typing import List, Optional, Dict, Literal
import bcrypt
from fastapi import FastAPI, Depends, HTTPException, status, Query, Response, UploadFile, File, Form, WebSocket, \
    WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy.orm import joinedload

from server.database.models import *

from fastapi import APIRouter

from ..core import get_current_user_id, get_db, is_admin_user


router = APIRouter()


@router.post("/csv/users/import", response_model=Dict[str, str])
async def import_users_csv(
        file: UploadFile = File(...),
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id),
        is_admin: bool = Depends(is_admin_user)
):
    if not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Только администратор может импортировать пользователей"
        )

    try:
        contents = await file.read()
        try:
            decoded = contents.decode('utf-8-sig')
        except UnicodeDecodeError:
            decoded = contents.decode('cp1251')

        csv_data = StringIO(decoded)
        reader = csv.DictReader(csv_data)

        imported_count = 0
        skipped_count = 0
        errors = []
        required_fields = ['username', 'last_name', 'first_name', 'position', 'department']

        for row_num, row in enumerate(reader, start=2):
            try:
                if not row.get('username'):
                    continue

                missing_fields = [f for f in required_fields if not row.get(f)]
                if missing_fields:
                    raise ValueError(f"Отсутствуют обязательные поля: {', '.join(missing_fields)}")

                existing_user = db.query(User).filter_by(username=row['username']).first()
                if existing_user:
                    skipped_count += 1
                    continue

                password = row.get('password') or secrets.token_urlsafe(8)
                hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())

                with db.begin_nested():
                    db_user = User(
                        username=row['username'],
                        password=hashed_password.decode('utf-8'),
                        employee=Employee(
                            name='',
                            first_name=row['first_name'],
                            last_name=row['last_name'],
                            middle_name=row.get('middle_name', ''),
                            position=row['position'],
                            phone_number=row.get('phone_number', ''),
                            email=row.get('email', ''),
                            profile=row.get('profile', ''),
                            department=row['department']
                        )
                    )
                    db.add(db_user)

                imported_count += 1

            except Exception as e:
                errors.append(f"Строка {row_num}: {str(e)}")
                continue

        db.commit()

        result_msg = f"Импорт пользователей завершён. Импортировано: {imported_count}"
        if skipped_count:
            result_msg += f". Пропущено (уже существуют): {skipped_count}"
        if errors:
            result_msg += f". Ошибок: {len(errors)}"

        return {"message": result_msg, "errors": '\n'.join(errors) if errors else ''}

    except Exception as e:
        traceback_text = traceback.format_exc()
        print("=== Ошибка при импорте пользователей ===")
        print(traceback_text)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка на сервере: {str(e)}"
        )


@router.post("/csv/organizations/import", response_model=Dict[str, str])
async def import_organizations_csv(
        file: UploadFile = File(...),
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id),
        is_admin: bool = Depends(is_admin_user)
):
    if not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Только администратор может импортировать организации"
        )

    try:
        contents = await file.read()

        # Пробуем декодировать сначала как UTF-8, затем CP1251
        try:
            decoded = contents.decode('utf-8-sig')
        except UnicodeDecodeError:
            decoded = contents.decode('cp1251')

        csv_data = StringIO(decoded)
        reader = csv.DictReader(csv_data)

        imported_count = 0
        errors = []

        allowed_fields = {'name', 'description', 'phones', 'phone_descriptions', 'emails', 'email_descriptions'}

        for row_num, row in enumerate(reader, start=2):
            try:
                cleaned_row = {k.strip().lower(): v.strip() for k, v in row.items() if k}
                row = {k: v for k, v in cleaned_row.items() if k in allowed_fields}

                if not any(row.values()) or not row.get('name'):
                    break

                with db.begin_nested():
                    org = Organization(
                        name=row['name'],
                        description=row.get('description', ''),
                        who_create=current_user_id,
                        when_create=datetime.now()
                    )
                    db.add(org)
                    db.flush()

                    phones = [p.strip() for p in row.get('phones', '').split(';') if p.strip()]
                    phone_descriptions = [d.strip() for d in row.get('phone_descriptions', '').split(';')] if row.get(
                        'phone_descriptions') else []
                    for i, phone in enumerate(phones):
                        description = phone_descriptions[i] if i < len(phone_descriptions) else 'Основной'
                        db.add(OrganizationPhone(organization_id=org.id, phone_number=phone, description=description))

                    emails = [e.strip() for e in row.get('emails', '').split(';') if e.strip()]
                    email_descriptions = [d.strip() for d in row.get('email_descriptions', '').split(';')] if row.get(
                        'email_descriptions') else []
                    for i, email in enumerate(emails):
                        description = email_descriptions[i] if i < len(email_descriptions) else 'Основной'
                        db.add(OrganizationEmail(organization_id=org.id, email=email, description=description))

                imported_count += 1

            except Exception as e:
                errors.append(f"Строка {row_num}: {str(e)}")
                continue

        db.commit()

        return {
            "message": f"Импорт организаций завершён. Импортировано: {imported_count}, ошибок: {len(errors)}"
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Ошибка при обработке CSV файла: {str(e)}"
        )


@router.get("/csv/organizations/export")
def export_organizations_csv(
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id),
        is_admin: bool = Depends(is_admin_user)
):
    if not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Только администратор может экспортировать организации"
        )

    try:
        organizations = db.query(Organization).options(
            joinedload(Organization.phones),
            joinedload(Organization.emails)
        ).all()

        output = StringIO()
        writer = csv.writer(output)

        writer.writerow([
            'id', 'name', 'description',
            'phones', 'phone_descriptions',
            'emails', 'email_descriptions',
            'created_by', 'created_at', 'updated_by', 'updated_at'
        ])

        for org in organizations:
            phones = [p.phone_number for p in org.phones]
            phone_descriptions = [p.description for p in org.phones]
            emails = [e.email for e in org.emails]
            email_descriptions = [e.description for e in org.emails]

            writer.writerow([
                org.id,
                org.name,
                org.description or '',
                ';'.join(phones),
                ';'.join(phone_descriptions),
                ';'.join(emails),
                ';'.join(email_descriptions),
                org.who_create or '',
                org.when_create.isoformat() if org.when_create else '',
                org.who_edit or '',
                org.when_edit.isoformat() if org.when_edit else ''
            ])

        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=organizations_export.csv",
                "Content-Type": "text/csv; charset=utf-8"
            }
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при экспорте организаций: {str(e)}"
        )


@router.get("/csv/users/export")
def export_users_csv(
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id),
        is_admin: bool = Depends(is_admin_user)
):
    if not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Только администратор может экспортировать пользователей"
        )

    try:
        users = db.query(User).options(joinedload(User.employee)).all()

        output = StringIO()
        writer = csv.writer(output)

        writer.writerow([
            'username', 'password',
            'last_name', 'first_name', 'middle_name',
            'position', 'phone_number', 'email',
            'profile', 'department'
        ])

        for user in users:
            emp = user.employee
            writer.writerow([
                user.username,
                '',
                emp.last_name or '',
                emp.first_name or '',
                emp.middle_name or '',
                emp.position or '',
                emp.phone_number or '',
                emp.email or '',
                emp.profile or '',
                emp.department or ''
            ])

        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=users_export.csv",
                "Content-Type": "text/csv; charset=utf-8"
            }
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при экспорте пользователей: {str(e)}"
        )
