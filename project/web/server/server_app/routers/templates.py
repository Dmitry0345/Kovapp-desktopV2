# -*- coding: utf-8 -*-

from typing import List, Optional, Dict, Literal
from fastapi import FastAPI, Depends, HTTPException, status, Query, Response, UploadFile, File, Form, WebSocket, \
    WebSocketDisconnect
from sqlalchemy.orm import Session

from server.database.models import *

from fastapi import APIRouter

from ..core import get_current_user_id, get_db, is_admin_user
from ..schemas import TemplateCreate, TemplateOut


router = APIRouter()


@router.get("/templates/{template_id}", response_model=TemplateOut)
def get_template(
        template_id: int,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    try:
        template = db.query(Template).filter(Template.id == template_id).first()
        if not template:
            raise HTTPException(
                status_code=404,
                detail=f"Шаблон с ID {template_id} не найден"
            )
        return template
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при получении шаблона: {str(e)}"
        )


@router.get("/templates", response_model=List[TemplateOut])
def get_templates(
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    try:
        templates = db.query(Template).all()
        return templates
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при получении шаблонов: {str(e)}"
        )


@router.post("/templates", response_model=TemplateOut, status_code=status.HTTP_201_CREATED)
async def create_template(
        template_data: TemplateCreate,
        db: Session = Depends(get_db),
        is_admin: bool = Depends(is_admin_user)
):
    if not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Только администратор может создавать шаблоны"
        )

    try:
        db_template = Template(
            template_name=template_data.template_name,
            num_stages=template_data.num_stages,
            departments=template_data.departments
        )
        db.add(db_template)
        db.commit()
        db.refresh(db_template)
        return db_template
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail=f"Ошибка при создании шаблона: {str(e)}"
        )


@router.put("/templates/{template_id}", response_model=TemplateOut)
async def update_template(
        template_id: int,
        template_data: TemplateCreate,
        db: Session = Depends(get_db),
        is_admin: bool = Depends(is_admin_user)
):
    if not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Только администратор может обновлять шаблоны"
        )

    try:
        db_template = db.query(Template).filter(Template.id == template_id).first()
        if not db_template:
            raise HTTPException(status_code=404, detail="Шаблон не найден")

        db_template.template_name = template_data.template_name
        db_template.num_stages = template_data.num_stages
        db_template.departments = template_data.departments

        db.commit()
        db.refresh(db_template)
        return db_template
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail=f"Ошибка при обновлении шаблона: {str(e)}"
        )


@router.delete("/templates/{template_id}")
async def delete_template(
        template_id: int,
        db: Session = Depends(get_db),
        is_admin: bool = Depends(is_admin_user)
):
    if not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Только администратор может удалять шаблоны"
        )

    try:
        db_template = db.query(Template).filter(Template.id == template_id).first()
        if not db_template:
            raise HTTPException(status_code=404, detail="Шаблон не найден")

        db.delete(db_template)
        db.commit()
        return {"status": "success", "message": "Шаблон успешно удален"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail=f"Ошибка при удалении шаблона: {str(e)}"
        )
