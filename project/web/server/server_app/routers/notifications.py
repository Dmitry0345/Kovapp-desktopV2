# -*- coding: utf-8 -*-

from datetime import timedelta, datetime, date
from typing import List, Optional, Dict, Literal
from fastapi import FastAPI, Depends, HTTPException, status, Query, Response, UploadFile, File, Form, WebSocket, \
    WebSocketDisconnect
from sqlalchemy.orm import Session

from server.database.models import *

from fastapi import APIRouter

from ..core import get_current_user_id, get_db, user_can_access_task, user_can_create_tasks
from ..config import logger
from ..realtime import manager
from ..push import push_notification
from ..schemas import NotificationCreate, NotificationOut


router = APIRouter()


def _task_members_notice(db: Session, user_id: int, data: NotificationCreate, others) -> bool:
    if (data.target_section or "") != "task_view" or not data.target_id:
        return False
    task = db.query(Task).filter(Task.id == data.target_id).first()
    if not task or not user_can_access_task(db, user_id, task):
        return False
    members = {task.creator_id}
    members.update(e.id for e in (task.responsible_persons or []))
    members.update(e.id for e in (task.assigned_employees or []))
    for stage in task.stage_details or []:
        members.update(p.id for p in (stage.participants or []))
    return set(others) <= members


@router.post("/notifications", response_model=List[NotificationOut],
             status_code=status.HTTP_201_CREATED)
async def create_notification(
        notification_data: NotificationCreate,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    try:
        if not notification_data.message:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Сообщение уведомления не может быть пустым"
            )

        recipient_ids = []
        if notification_data.employee_id:
            recipient_ids = [notification_data.employee_id]
        elif notification_data.user_ids:
            recipient_ids = notification_data.user_ids

        if not recipient_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Не указаны получатели уведомления"
            )

        # Рассылать другим вправе те же, кто ведёт задачи. Себе — кто угодно:
        # на этом держатся отметки о собственных действиях в разделе.
        others = [i for i in recipient_ids if i != current_user_id]
        if others and not user_can_create_tasks(db, current_user_id) \
                and not _task_members_notice(db, current_user_id, notification_data, others):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Рассылать уведомления могут старшие и главные сотрудники, директор и администратор"
            )

        existing_ids = {e.id for e in db.query(Employee.id).filter(Employee.id.in_(recipient_ids)).all()}
        missing_ids = set(recipient_ids) - existing_ids

        if missing_ids:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Сотрудники с ID {missing_ids} не найдены"
            )

        created_notifications = []
        for user_id in recipient_ids:
            db_notification = Notification(
                employee_id=user_id,
                message=notification_data.message,
                target_section=notification_data.target_section,
                target_id=notification_data.target_id,
                created_at=datetime.now()
            )
            db.add(db_notification)
            created_notifications.append(db_notification)

        db.commit()

        for notification in created_notifications:
            db.refresh(notification)
            try:
                await manager.send_personal_message({
                    "type": "notification.new",
                    "notification_id": notification.id,
                    "message": notification.message,
                    "target_section": notification.target_section,
                    "target_id": notification.target_id
                }, notification.employee_id)
            except Exception as e:
                print(f"WS error sending notification to user {notification.employee_id}: {e}")
            try:
                await push_notification(notification.employee_id, notification.message,
                                        notification.target_section, notification.target_id)
            except Exception as e:
                logger.debug(f"push_notification: {e}")

        return created_notifications

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при создании уведомлений: {str(e)}"
        )


@router.get("/employees/{employee_id}/notifications", response_model=List[NotificationOut])
def get_employee_notifications(
        employee_id: int,
        is_read: Optional[int] = None,
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    try:
        # Читать чужие уведомления нельзя: раньше подставлялся любой id
        if employee_id != current_user_id:
            raise HTTPException(status_code=403, detail="Можно смотреть только свои уведомления")

        query = db.query(Notification).filter(
            Notification.employee_id == employee_id
        )

        if is_read is not None:
            query = query.filter(Notification.is_read == is_read)

        notifications = query.order_by(Notification.created_at.desc()).all()
        return notifications
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при получении уведомлений: {str(e)}"
        )


@router.post("/notifications/mark-as-read")
async def mark_notifications_as_read(
        notification_ids: List[int],
        db: Session = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    try:
        # Отметить прочитанным можно только своё: раньше проходил любой id
        db.query(Notification).filter(
            Notification.id.in_(notification_ids),
            Notification.employee_id == current_user_id
        ).update({"is_read": True}, synchronize_session=False)
        db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при обновлении уведомлений: {str(e)}"
        )
