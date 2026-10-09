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

from ..config import logger

from ..realtime import manager
from ..core import get_current_user_id, get_db, is_admin_user, user_can_manage_organizations
from ..schemas import Organ, OrganizationCreate, OrganizationUpdate


router = APIRouter()


async def _notify_orgs(kind: str, org_id: int, sender_id: int):
    """Справочник организаций общий — изменения показываем всем сразу."""
    try:
        await manager.broadcast({"type": kind, "organization_id": org_id, "sender_id": sender_id})
    except Exception as e:
        logger.debug("Организации: рассылка не ушла: %s", e)



@router.get("/organizations", response_model=List[Organ])
def get_organizations(
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    try:
        organizations = db.query(Organization).options(
            joinedload(Organization.phones),
            joinedload(Organization.emails)
        ).all()
        return organizations
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при получении организаций: {str(e)}"
        )


@router.get("/organizations/search", response_model=List[Organ])
def search_organizations(
        query: str,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    try:
        organizations = db.query(Organization).options(
            joinedload(Organization.phones),
            joinedload(Organization.emails)
        ).filter(
            Organization.name.ilike(f"%{query}%")
        ).all()
        return organizations
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при поиске организаций: {str(e)}"
        )


@router.get("/organizations/{org_id}", response_model=Organ)
def get_organization(
        org_id: int,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    try:
        org = db.query(Organization).options(
            joinedload(Organization.phones),
            joinedload(Organization.emails)
        ).filter(Organization.id == org_id).first()

        if not org:
            raise HTTPException(status_code=404, detail="Организация не найдена")
        return org
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при получении организации: {str(e)}"
        )


@router.post("/organizations", response_model=Organ, status_code=201)
async def create_organization(
        org_data: OrganizationCreate,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id),
        is_admin: bool = Depends(is_admin_user)
):
    # Организации ведут администратор, директор, старшие и главные
    # сотрудники и любой сотрудник Договорного отдела
    if not (is_admin or user_can_manage_organizations(db, current_user_id)):
        raise HTTPException(status_code=403,
                            detail="Изменять справочник организаций могут руководство, "
                                   "старшие и главные сотрудники и Договорной отдел")

    try:
        existing_org = db.query(Organization).filter(
            func.lower(Organization.name) == func.lower(org_data.name)
        ).first()
        if existing_org:
            raise HTTPException(status_code=400, detail="Организация с таким названием уже существует")

        new_org = Organization(
            name=org_data.name,
            description=org_data.description,
            who_create=current_user_id,
            when_create=datetime.now()
        )
        db.add(new_org)
        db.flush()

        for phone_data in org_data.phones:
            new_phone = OrganizationPhone(
                organization_id=new_org.id,
                phone_number=phone_data.phone_number,
                description=phone_data.description
            )
            db.add(new_phone)

        for email_data in org_data.emails:
            new_email = OrganizationEmail(
                organization_id=new_org.id,
                email=email_data.email,
                description=email_data.description
            )
            db.add(new_email)

        db.commit()
        await _notify_orgs("organization.created", new_org.id, current_user_id)
        db.refresh(new_org)
        return new_org

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка при создании организации: {str(e)}")


@router.put("/organizations/{org_id}", response_model=Organ)
async def update_organization(
        org_id: int,
        org_data: OrganizationUpdate,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id),
        is_admin: bool = Depends(is_admin_user)
):
    if not (is_admin or user_can_manage_organizations(db, current_user_id)):
        raise HTTPException(
            status_code=403,
            detail="Изменять справочник организаций могут руководство, "
                   "старшие и главные сотрудники и Договорной отдел"
        )

    try:
        org = db.query(Organization).options(
            joinedload(Organization.phones),
            joinedload(Organization.emails)
        ).filter(Organization.id == org_id).first()

        if not org:
            raise HTTPException(status_code=404, detail="Организация не найдена")

        if org_data.name is not None:
            existing = db.query(Organization).filter(
                func.lower(Organization.name) == func.lower(org_data.name),
                Organization.id != org_id
            ).first()
            if existing:
                raise HTTPException(
                    status_code=400,
                    detail="Организация с таким названием уже существует"
                )
            org.name = org_data.name

        if org_data.description is not None:
            org.description = org_data.description

        org.who_edit = current_user_id
        org.when_edit = datetime.now()

        if org_data.phones is not None:
            current_phones = {(p.phone_number, p.description) for p in org.phones}
            new_phones = {(p.phone_number, p.description) for p in org_data.phones}

            for phone in org.phones:
                if (phone.phone_number, phone.description) not in new_phones:
                    db.delete(phone)

            for phone_data in org_data.phones:
                if (phone_data.phone_number, phone_data.description) not in current_phones:
                    new_phone = OrganizationPhone(
                        organization_id=org_id,
                        phone_number=phone_data.phone_number,
                        description=phone_data.description
                    )
                    db.add(new_phone)

        if org_data.emails is not None:
            current_emails = {(e.email, e.description) for e in org.emails}
            new_emails = {(e.email, e.description) for e in org_data.emails}

            for email in org.emails:
                if (email.email, email.description) not in new_emails:
                    db.delete(email)

            for email_data in org_data.emails:
                if (email_data.email, email_data.description) not in current_emails:
                    new_email = OrganizationEmail(
                        organization_id=org_id,
                        email=email_data.email,
                        description=email_data.description
                    )
                    db.add(new_email)

        db.commit()
        await _notify_orgs("organization.updated", org_id, current_user_id)
        db.refresh(org)
        return org

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при обновлении организации: {str(e)}"
        )


@router.delete("/organizations/{org_id}")
async def delete_organization(
        org_id: int,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id),
        is_admin: bool = Depends(is_admin_user)
):
    if not (is_admin or user_can_manage_organizations(db, current_user_id)):
        raise HTTPException(
            status_code=403,
            detail="Изменять справочник организаций могут руководство, "
                   "старшие и главные сотрудники и Договорной отдел"
        )

    try:
        org = db.query(Organization).filter(Organization.id == org_id).first()
        if not org:
            raise HTTPException(status_code=404, detail="Организация не найдена")

        db.query(OrganizationPhone).filter(
            OrganizationPhone.organization_id == org_id
        ).delete()

        db.query(OrganizationEmail).filter(
            OrganizationEmail.organization_id == org_id
        ).delete()

        db.delete(org)
        db.commit()
        await _notify_orgs("organization.deleted", org_id, current_user_id)

        return {"status": "success", "message": "Организация удалена"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при удалении организации: {str(e)}"
        )
