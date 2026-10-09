# -*- coding: utf-8 -*-
"""Оборудование предприятия: ведомость, выдача людям и возврат на склад.

Чем отличается от склада проекта. Складская строка привязана к объекту, а
оборудование числится за предприятием и выдаётся сотруднику без задачи и
проекта. Поэтому здесь свой список, а не сотня заранее заведённых строк:
позиции добавляют и убирают по мере надобности.

Права:
  * видеть раздел — все;
  * править «Оборудование» и «Количество» — отдел «Бухгалтерия», директор,
    администратор;
  * выдавать и принимать возврат — отдел «Склад», директор, администратор;
  * «Осталось» не правится вовсе: это количество минус выданное.

Путь выдачи: склад создаёт запись → получатель подтверждает, что взял →
позже он же нажимает «Вернуть» → любой сотрудник склада принимает. Пока
возврат не принят, оборудование числится за человеком. Уведомление о
возврате уходит тому, кто выдавал, — остальным складским видно в разделе.
"""
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from server.database.models import (Employee, EquipmentIssue, EquipmentItem, EquipmentLog,
                                    Notification, WarehouseUnit)

from ..config import FILE_STORAGE_ROOT, logger
from ..core import MANAGEMENT_STATUSES, get_current_user_id, get_current_user_id_from_header_or_query, get_db
from ..project_permissions import get_employee, is_management
from ..push import push_notification
from ..realtime import manager

router = APIRouter()

GOODS_DEPARTMENT = "Бухгалтерия"          # правит наименование и количество
STOCK_DEPARTMENT = "Склад"                # выдаёт и принимает возврат
DEFAULT_UNITS = (("шт", "Штука"), ("компл", "Комплект"), ("м", "Метр"),
                 ("кг", "Килограмм"), ("уп", "Упаковка"))

# Пока возврат не принят, вещь числится за человеком
HELD_STATUSES = ("issued", "return_pending")

PHOTO_DIR = "equipment"                   # папка внутри хранилища файлов
MAX_PHOTO_BYTES = 6 * 1024 * 1024
PHOTO_TYPES = {b"\xff\xd8\xff": ("image/jpeg", ".jpg"),
               b"\x89PNG": ("image/png", ".png"),
               b"RIFF": ("image/webp", ".webp")}


def photo_kind(data: bytes):
    """Тип картинки по её первым байтам: расширению из имени не доверяем"""
    for sign, kind in PHOTO_TYPES.items():
        if data.startswith(sign):
            return kind
    return None


# ── права ────────────────────────────────────────────────────────────────

class Access:
    def __init__(self, db: Session, user_id: int):
        self.emp = get_employee(db, user_id)
        self.user_id = user_id
        self.management = is_management(self.emp)
        department = ((self.emp.department if self.emp else "") or "").strip()
        self.department = department
        self.can_view = True                       # раздел открыт всем
        self.can_edit_goods = self.management or department == GOODS_DEPARTMENT
        self.can_issue = self.management or department == STOCK_DEPARTMENT
        # Возврат принимает любой складской, а не тот, кто выдавал
        self.can_accept_return = self.can_issue

    def as_dict(self) -> dict:
        return {"can_view": True, "can_edit_goods": self.can_edit_goods,
                "can_issue": self.can_issue, "can_accept_return": self.can_accept_return,
                "is_management": self.management, "user_id": self.user_id}


def require_goods(access: Access) -> None:
    if not access.can_edit_goods:
        raise HTTPException(status_code=403,
                            detail="«Оборудование» и «Количество» ведёт бухгалтерия, "
                                   "директор и администратор")


def require_stock(access: Access) -> None:
    if not access.can_issue:
        raise HTTPException(status_code=403,
                            detail="Выдавать и принимать оборудование может отдел склада, "
                                   "директор и администратор")


# ── представление данных ─────────────────────────────────────────────────

def person(emp: Optional[Employee]) -> Optional[dict]:
    if not emp:
        return None
    name = " ".join(x for x in ((emp.last_name or ""), (emp.first_name or "")) if x).strip()
    return {"id": emp.id, "last_name": emp.last_name, "first_name": emp.first_name,
            "name": name or (emp.name or ""), "department": emp.department or ""}


def people_map(db: Session, ids) -> dict:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {e.id: e for e in db.query(Employee).filter(Employee.id.in_(ids)).all()}


def issue_out(issue: EquipmentIssue, who: dict, user_id: int) -> dict:
    return {
        "id": issue.id, "item_id": issue.item_id, "quantity": float(issue.quantity or 0),
        "status": issue.status,
        "issuer": person(who.get(issue.issuer_id)),
        "receiver": person(who.get(issue.receiver_id)),
        "returned_to": person(who.get(issue.returned_to)),
        "created_at": issue.created_at.isoformat() if issue.created_at else None,
        "confirmed_at": issue.confirmed_at.isoformat() if issue.confirmed_at else None,
        "return_requested_at": issue.return_requested_at.isoformat() if issue.return_requested_at else None,
        "mine": issue.receiver_id == user_id,
        "issued_by_me": issue.issuer_id == user_id,
    }


def issued_totals(db: Session) -> dict:
    """Сколько числится за людьми по каждой позиции"""
    rows = db.query(EquipmentIssue.item_id, func.sum(EquipmentIssue.quantity)) \
        .filter(EquipmentIssue.status.in_(HELD_STATUSES)) \
        .group_by(EquipmentIssue.item_id).all()
    return {r[0]: float(r[1] or 0) for r in rows}


def item_out(item: EquipmentItem, issued: float, issues: list, who: dict, user_id: int) -> dict:  # noqa: C901
    quantity = float(item.quantity) if item.quantity is not None else None
    left = None if quantity is None else round(quantity - issued, 4)
    return {
        "id": item.id,
        "name": item.name or "",
        "quantity": quantity,
        "unit": item.unit or "",
        "has_photo": bool(item.photo_path),
        "issued": round(issued, 4),
        "left": left,
        # Кнопка выдачи появляется, только когда строка заполнена и есть остаток
        "ready": bool((item.name or "").strip()) and quantity is not None and quantity > 0,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        "updated_by": person(who.get(item.updated_by)),
        "issues": [issue_out(i, who, user_id) for i in issues],
    }


LOG_TITLES = {
    "issued": "выдал", "confirmed": "принял", "declined": "отказался принимать",
    "cancelled": "отменил выдачу", "return_requested": "вернул",
    "returned": "принял возврат", "return_declined": "отклонил возврат",
}
FIELD_TITLES = {"name": "Оборудование", "quantity": "Количество", "unit": "Единица"}


def log_out(row: EquipmentLog, who: dict) -> dict:
    return {
        "id": row.id, "kind": row.kind, "action": row.action,
        "field": row.field, "field_title": FIELD_TITLES.get(row.field or "", row.field or ""),
        "action_title": LOG_TITLES.get(row.action or "", row.action or ""),
        "old_value": row.old_value, "new_value": row.new_value,
        "user": person(who.get(row.user_id)), "target": person(who.get(row.target_id)),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


# ── журнал и рассылка ────────────────────────────────────────────────────

def write_log(db: Session, item_id: int, kind: str, user_id: int, **extra) -> None:
    try:
        db.add(EquipmentLog(item_id=item_id, kind=kind, user_id=user_id,
                            created_at=datetime.now(), **extra))
    except Exception as e:
        logger.error("Оборудование: запись журнала не создана: %s", e)


def stock_people(db: Session) -> set:
    """Складские и руководство — им видны заявки на возврат"""
    try:
        rows = db.query(Employee.id).filter(
            (Employee.department == STOCK_DEPARTMENT)
            | (func.lower(func.coalesce(Employee.status, "")).in_(MANAGEMENT_STATUSES))).all()
        return {r[0] for r in rows if r and r[0]}
    except Exception as e:
        logger.debug("Оборудование: список складских не собран: %s", e)
        return set()


async def broadcast(kind: str, actor_id: int, **payload) -> None:
    """Раздел открыт всем, поэтому изменения расходятся всем подключённым.

    Клиент по этому событию обновляет одну строку, а не перечитывает список:
    так у всех, кто смотрит раздел, цифры меняются на месте и одновременно.
    """
    try:
        await manager.broadcast({"type": "equipment." + kind, "actor_id": actor_id, **payload})
    except Exception as e:
        logger.debug("Оборудование: рассылка %s: %s", kind, e)


async def notify(db: Session, user_id: Optional[int], message: str, item_id: Optional[int]) -> None:
    """Постоянное уведомление, всплывающее и push — как в остальных разделах"""
    if not user_id:
        return
    try:
        note = Notification(employee_id=user_id, message=message,
                            target_section="equipment", target_id=item_id,
                            created_at=datetime.now())
        db.add(note)
        db.commit()
        db.refresh(note)
    except Exception as e:
        db.rollback()
        logger.error("Оборудование: уведомление не создано: %s", e)
        return
    try:
        await manager.send_personal_message({
            "type": "notification.new", "notification_id": note.id, "message": note.message,
            "target_section": note.target_section, "target_id": note.target_id}, user_id)
    except Exception as e:
        logger.debug("Оборудование: сокет %s: %s", user_id, e)
    try:
        await push_notification(user_id, message, "equipment", item_id)
    except Exception as e:
        logger.debug("Оборудование: push %s: %s", user_id, e)


def full_item(db: Session, item: EquipmentItem, user_id: int) -> dict:
    """Строка со всеми выдачами — то, что клиент кладёт на место старой"""
    issues = db.query(EquipmentIssue).filter(
        EquipmentIssue.item_id == item.id,
        EquipmentIssue.status.in_(("pending",) + HELD_STATUSES)) \
        .order_by(EquipmentIssue.created_at.desc()).all()
    who = people_map(db, [item.updated_by] + [i for issue in issues
                                              for i in (issue.issuer_id, issue.receiver_id,
                                                        issue.returned_to)])
    issued = sum(float(i.quantity or 0) for i in issues if i.status in HELD_STATUSES)
    return item_out(item, issued, issues, who, user_id)


def load_item(db: Session, item_id: int) -> EquipmentItem:
    item = db.query(EquipmentItem).filter(EquipmentItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Позиция не найдена")
    return item


def load_issue(db: Session, issue_id: int) -> EquipmentIssue:
    issue = db.query(EquipmentIssue).filter(EquipmentIssue.id == issue_id).first()
    if not issue:
        raise HTTPException(status_code=404, detail="Выдача не найдена")
    return issue


def name_of(item: EquipmentItem) -> str:
    return (item.name or "").strip() or "позиция №%d" % item.id


def num(value: float) -> str:
    return ("%g" % float(value or 0))


# ── тела запросов ────────────────────────────────────────────────────────

class ItemIn(BaseModel):
    name: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    fields: List[str] = Field(default_factory=list)


class IssueIn(BaseModel):
    receiver_id: int
    quantity: float


# ── ведомость ────────────────────────────────────────────────────────────

@router.get("/equipment")
def get_equipment(db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user_id)):
    access = Access(db, current_user_id)
    items = db.query(EquipmentItem).order_by(EquipmentItem.id).all()
    issues = db.query(EquipmentIssue).filter(
        EquipmentIssue.status.in_(("pending",) + HELD_STATUSES)) \
        .order_by(EquipmentIssue.created_at.desc()).all()

    by_item = {}
    for issue in issues:
        by_item.setdefault(issue.item_id, []).append(issue)
    who = people_map(db, [i.updated_by for i in items]
                     + [x for issue in issues
                        for x in (issue.issuer_id, issue.receiver_id, issue.returned_to)])
    totals = issued_totals(db)

    units = db.query(WarehouseUnit).order_by(WarehouseUnit.name).all()
    if not units:
        for short, full in DEFAULT_UNITS:
            db.add(WarehouseUnit(name=short, full_name=full, created_by=current_user_id))
        db.commit()
        units = db.query(WarehouseUnit).order_by(WarehouseUnit.name).all()

    return {
        "rows": [item_out(i, totals.get(i.id, 0.0), by_item.get(i.id, []), who, current_user_id)
                 for i in items],
        "units": [{"id": u.id, "name": u.name, "full_name": u.full_name or ""} for u in units],
        "access": access.as_dict(),
    }


@router.post("/equipment", status_code=status.HTTP_201_CREATED)
async def add_item(payload: ItemIn, db: Session = Depends(get_db),
                   current_user_id: int = Depends(get_current_user_id)):
    access = Access(db, current_user_id)
    require_goods(access)
    item = EquipmentItem(name=(payload.name or "").strip()[:255],
                         quantity=payload.quantity, unit=(payload.unit or "").strip()[:32],
                         created_by=current_user_id, updated_by=current_user_id)
    db.add(item)
    db.commit()
    db.refresh(item)
    write_log(db, item.id, "cell", current_user_id, action="created",
              field="name", new_value=item.name or "")
    db.commit()
    row = full_item(db, item, current_user_id)
    await broadcast("row", current_user_id, item_id=item.id, row=row, added=True)
    return row


@router.patch("/equipment/{item_id}")
async def update_item(item_id: int, payload: ItemIn, db: Session = Depends(get_db),
                      current_user_id: int = Depends(get_current_user_id)):
    access = Access(db, current_user_id)
    require_goods(access)
    item = load_item(db, item_id)

    allowed = {"name", "quantity", "unit"}
    fields = set(payload.fields or [])
    if not fields:
        raise HTTPException(status_code=400, detail="Не указано, какие поля меняются")
    unknown = fields - allowed
    if unknown:
        raise HTTPException(status_code=400, detail="Неизвестные поля: " + ", ".join(sorted(unknown)))

    issued = issued_totals(db).get(item.id, 0.0)
    changed = []
    for field in sorted(fields):
        new = getattr(payload, field)
        if field == "name":
            new = (new or "").strip()[:255]
        elif field == "unit":
            new = (new or "").strip()[:32]
            if new and not db.query(WarehouseUnit.id).filter(WarehouseUnit.name == new).first():
                db.add(WarehouseUnit(name=new, created_by=current_user_id))
        elif field == "quantity" and new is not None:
            new = float(new)
            if new < 0:
                raise HTTPException(status_code=400, detail="Количество не может быть отрицательным")
            if new < issued:
                raise HTTPException(
                    status_code=409,
                    detail="Сейчас на руках %s — количество нельзя сделать меньше" % num(issued))
        old = getattr(item, field)
        if (old if old not in ("", None) else None) == (new if new not in ("", None) else None):
            continue
        write_log(db, item.id, "cell", current_user_id, action="changed", field=field,
                  old_value=None if old is None else str(old), new_value=None if new is None else str(new))
        setattr(item, field, new)
        changed.append(field)

    if changed:
        item.updated_by = current_user_id
        item.updated_at = datetime.now()
        db.commit()
        db.refresh(item)
    row = full_item(db, item, current_user_id)
    if changed:
        await broadcast("row", current_user_id, item_id=item.id, row=row, fields=changed)
    return {"row": row, "changed": changed}


@router.delete("/equipment/{item_id}")
async def delete_item(item_id: int, db: Session = Depends(get_db),
                      current_user_id: int = Depends(get_current_user_id)):
    access = Access(db, current_user_id)
    require_goods(access)
    item = load_item(db, item_id)
    held = db.query(EquipmentIssue.id).filter(
        EquipmentIssue.item_id == item_id,
        EquipmentIssue.status.in_(("pending",) + HELD_STATUSES)).first()
    if held:
        raise HTTPException(status_code=409,
                            detail="Позиция на руках или ждёт подтверждения — сначала верните её")
    db.delete(item)
    db.commit()
    await broadcast("removed", current_user_id, item_id=item_id)
    return {"ok": True}


@router.post("/equipment/{item_id}/photo")
async def set_photo(item_id: int, file: UploadFile = File(...), db: Session = Depends(get_db),
                    current_user_id: int = Depends(get_current_user_id)):
    """Фотография позиции. Меняет тот же, кто ведёт наименование и количество."""
    access = Access(db, current_user_id)
    require_goods(access)
    item = load_item(db, item_id)

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Файл пустой")
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=413, detail="Фото больше 6 МБ")
    kind = photo_kind(data)
    if not kind:
        raise HTTPException(status_code=400, detail="Это не изображение")

    folder = Path(FILE_STORAGE_ROOT) / PHOTO_DIR
    folder.mkdir(parents=True, exist_ok=True)
    name = "%d_%s%s" % (item_id, datetime.now().strftime("%Y%m%d%H%M%S"), kind[1])
    (folder / name).write_bytes(data)

    old = item.photo_path
    item.photo_path = "%s/%s" % (PHOTO_DIR, name)
    item.updated_by = current_user_id
    item.updated_at = datetime.now()
    write_log(db, item.id, "cell", current_user_id, action="changed", field="photo",
              old_value="была" if old else "", new_value="обновлена")
    db.commit()
    # Прежний снимок больше не нужен: позиция показывает только последний
    if old:
        try:
            (Path(FILE_STORAGE_ROOT) / old).unlink(missing_ok=True)
        except Exception as e:
            logger.debug("Оборудование: старое фото не удалено: %s", e)

    row = full_item(db, item, current_user_id)
    await broadcast("row", current_user_id, item_id=item.id, row=row, fields=["photo"])
    return row


@router.delete("/equipment/{item_id}/photo")
async def drop_photo(item_id: int, db: Session = Depends(get_db),
                     current_user_id: int = Depends(get_current_user_id)):
    access = Access(db, current_user_id)
    require_goods(access)
    item = load_item(db, item_id)
    if not item.photo_path:
        return {"ok": True}
    try:
        (Path(FILE_STORAGE_ROOT) / item.photo_path).unlink(missing_ok=True)
    except Exception as e:
        logger.debug("Оборудование: фото не удалено с диска: %s", e)
    write_log(db, item.id, "cell", current_user_id, action="changed", field="photo",
              old_value="была", new_value="удалена")
    item.photo_path = None
    item.updated_by = current_user_id
    item.updated_at = datetime.now()
    db.commit()
    row = full_item(db, item, current_user_id)
    await broadcast("row", current_user_id, item_id=item.id, row=row, fields=["photo"])
    return row


@router.get("/equipment/{item_id}/photo")
def get_photo(item_id: int, db: Session = Depends(get_db),
              current_user_id: int = Depends(get_current_user_id_from_header_or_query)):
    """Отдаёт фотографию позиции. Раздел открыт всем, значит и снимок тоже."""
    item = load_item(db, item_id)
    if not item.photo_path:
        raise HTTPException(status_code=404, detail="Фотографии нет")
    path = Path(FILE_STORAGE_ROOT) / item.photo_path
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Файл фотографии не найден")
    data = path.read_bytes()
    kind = photo_kind(data)
    return Response(content=data, media_type=(kind[0] if kind else "application/octet-stream"),
                    headers={"Cache-Control": "private, max-age=300"})


# ── выдача ───────────────────────────────────────────────────────────────

@router.post("/equipment/{item_id}/issue", status_code=status.HTTP_201_CREATED)
async def issue(item_id: int, payload: IssueIn, db: Session = Depends(get_db),
                current_user_id: int = Depends(get_current_user_id)):
    access = Access(db, current_user_id)
    require_stock(access)
    item = load_item(db, item_id)
    if not (item.name or "").strip() or item.quantity is None:
        raise HTTPException(status_code=409, detail="Сначала заполните оборудование и количество")

    qty = float(payload.quantity or 0)
    if qty <= 0:
        raise HTTPException(status_code=400, detail="Количество выдачи должно быть больше нуля")
    receiver = db.query(Employee).filter(Employee.id == payload.receiver_id).first()
    if not receiver:
        raise HTTPException(status_code=404, detail="Получатель не найден")

    issued = issued_totals(db).get(item.id, 0.0)
    pending = db.query(func.sum(EquipmentIssue.quantity)).filter(
        EquipmentIssue.item_id == item.id, EquipmentIssue.status == "pending").scalar() or 0
    left = float(item.quantity) - issued - float(pending)
    if qty > left + 1e-9:
        raise HTTPException(status_code=409, detail="Свободно только %s" % num(left))

    row = EquipmentIssue(item_id=item.id, quantity=qty, issuer_id=current_user_id,
                         receiver_id=receiver.id, status="pending", created_at=datetime.now())
    db.add(row)
    db.commit()
    db.refresh(row)
    write_log(db, item.id, "issue", current_user_id, action="issued", issue_id=row.id,
              target_id=receiver.id, new_value=num(qty))
    db.commit()

    issuer = get_employee(db, current_user_id)
    await notify(db, receiver.id,
                 "%s выдаёт вам «%s» — %s %s. Подтвердите получение в разделе «Оборудование»"
                 % ((person(issuer) or {}).get("name") or "Сотрудник склада",
                    name_of(item), num(qty), item.unit or ""),
                 item.id)
    await broadcast("row", current_user_id, item_id=item.id,
                    row=full_item(db, item, current_user_id))
    return {"id": row.id, "status": row.status}


@router.post("/equipment/issues/{issue_id}/confirm")
async def confirm(issue_id: int, db: Session = Depends(get_db),
                  current_user_id: int = Depends(get_current_user_id)):
    row = load_issue(db, issue_id)
    if row.receiver_id != current_user_id:
        raise HTTPException(status_code=403, detail="Подтвердить получение может только получатель")
    if row.status != "pending":
        raise HTTPException(status_code=409, detail="Эта выдача уже обработана")
    row.status = "issued"
    row.confirmed_at = datetime.now()
    db.commit()
    item = load_item(db, row.item_id)
    write_log(db, item.id, "issue", current_user_id, action="confirmed", issue_id=row.id,
              target_id=row.issuer_id, new_value=num(row.quantity))
    db.commit()
    me = get_employee(db, current_user_id)
    await notify(db, row.issuer_id,
                 "%s принял «%s» — %s %s" % ((person(me) or {}).get("name") or "Сотрудник",
                                             name_of(item), num(row.quantity), item.unit or ""),
                 item.id)
    await broadcast("row", current_user_id, item_id=item.id,
                    row=full_item(db, item, current_user_id))
    return {"ok": True, "status": row.status}


@router.post("/equipment/issues/{issue_id}/decline")
async def decline(issue_id: int, db: Session = Depends(get_db),
                  current_user_id: int = Depends(get_current_user_id)):
    """Отказ получателя или отмена выдачи тем, кто её создал"""
    row = load_issue(db, issue_id)
    access = Access(db, current_user_id)
    if row.receiver_id != current_user_id and row.issuer_id != current_user_id and not access.management:
        raise HTTPException(status_code=403, detail="Отменить выдачу может получатель или тот, кто выдал")
    if row.status != "pending":
        raise HTTPException(status_code=409, detail="Эта выдача уже обработана")
    mine = row.receiver_id == current_user_id
    row.status = "cancelled"
    row.cancelled_at = datetime.now()
    row.cancelled_by = current_user_id
    db.commit()
    item = load_item(db, row.item_id)
    write_log(db, item.id, "issue", current_user_id,
              action="declined" if mine else "cancelled", issue_id=row.id,
              target_id=row.issuer_id if mine else row.receiver_id, new_value=num(row.quantity))
    db.commit()
    me = (person(get_employee(db, current_user_id)) or {}).get("name") or "Сотрудник"
    await notify(db, row.issuer_id if mine else row.receiver_id,
                 "%s %s выдачу «%s» — %s %s"
                 % (me, "отклонил" if mine else "отменил", name_of(item),
                    num(row.quantity), item.unit or ""), item.id)
    await broadcast("row", current_user_id, item_id=item.id,
                    row=full_item(db, item, current_user_id))
    return {"ok": True, "status": row.status}


# ── возврат ──────────────────────────────────────────────────────────────

@router.post("/equipment/issues/{issue_id}/return")
async def request_return(issue_id: int, db: Session = Depends(get_db),
                         current_user_id: int = Depends(get_current_user_id)):
    """Получатель возвращает оборудование: ждём приёмки складом"""
    row = load_issue(db, issue_id)
    if row.receiver_id != current_user_id:
        raise HTTPException(status_code=403, detail="Вернуть может тот, за кем числится оборудование")
    if row.status != "issued":
        raise HTTPException(status_code=409, detail="Это оборудование сейчас нельзя вернуть")
    row.status = "return_pending"
    row.return_requested_at = datetime.now()
    db.commit()
    item = load_item(db, row.item_id)
    write_log(db, item.id, "issue", current_user_id, action="return_requested", issue_id=row.id,
              target_id=row.issuer_id, new_value=num(row.quantity))
    db.commit()
    me = (person(get_employee(db, current_user_id)) or {}).get("name") or "Сотрудник"
    # Уведомление — тому, кто выдавал. Остальные складские видят заявку
    # в самом разделе: слать её всем было бы шумно.
    await notify(db, row.issuer_id,
                 "%s возвращает «%s» — %s %s. Примите оборудование в разделе «Оборудование»"
                 % (me, name_of(item), num(row.quantity), item.unit or ""), item.id)
    await broadcast("row", current_user_id, item_id=item.id,
                    row=full_item(db, item, current_user_id))
    return {"ok": True, "status": row.status}


@router.post("/equipment/issues/{issue_id}/return/accept")
async def accept_return(issue_id: int, db: Session = Depends(get_db),
                        current_user_id: int = Depends(get_current_user_id)):
    """Приёмка возврата: подойдёт любой сотрудник склада, не только выдававший"""
    access = Access(db, current_user_id)
    require_stock(access)
    row = load_issue(db, issue_id)
    if row.status != "return_pending":
        raise HTTPException(status_code=409, detail="Возврат по этой записи не запрашивали")
    row.status = "returned"
    row.returned_at = datetime.now()
    row.returned_to = current_user_id
    db.commit()
    item = load_item(db, row.item_id)
    write_log(db, item.id, "issue", current_user_id, action="returned", issue_id=row.id,
              target_id=row.receiver_id, new_value=num(row.quantity))
    db.commit()
    me = (person(get_employee(db, current_user_id)) or {}).get("name") or "Сотрудник склада"
    await notify(db, row.receiver_id,
                 "%s принял возврат «%s» — %s %s" % (me, name_of(item), num(row.quantity),
                                                     item.unit or ""), item.id)
    if row.issuer_id and row.issuer_id != current_user_id:
        await notify(db, row.issuer_id,
                     "%s принял возврат «%s» — %s %s" % (me, name_of(item), num(row.quantity),
                                                         item.unit or ""), item.id)
    await broadcast("row", current_user_id, item_id=item.id,
                    row=full_item(db, item, current_user_id))
    return {"ok": True, "status": row.status}


@router.post("/equipment/issues/{issue_id}/return/decline")
async def decline_return(issue_id: int, db: Session = Depends(get_db),
                         current_user_id: int = Depends(get_current_user_id)):
    """Отказ принять возврат: оборудование остаётся за получателем"""
    access = Access(db, current_user_id)
    require_stock(access)
    row = load_issue(db, issue_id)
    if row.status != "return_pending":
        raise HTTPException(status_code=409, detail="Возврат по этой записи не запрашивали")
    row.status = "issued"
    row.return_requested_at = None
    db.commit()
    item = load_item(db, row.item_id)
    write_log(db, item.id, "issue", current_user_id, action="return_declined", issue_id=row.id,
              target_id=row.receiver_id, new_value=num(row.quantity))
    db.commit()
    me = (person(get_employee(db, current_user_id)) or {}).get("name") or "Сотрудник склада"
    await notify(db, row.receiver_id,
                 "%s не принял возврат «%s» — оборудование пока числится за вами"
                 % (me, name_of(item)), item.id)
    await broadcast("row", current_user_id, item_id=item.id,
                    row=full_item(db, item, current_user_id))
    return {"ok": True, "status": row.status}


@router.get("/equipment/issues/pending")
def pending_issues(db: Session = Depends(get_db),
                   current_user_id: int = Depends(get_current_user_id)):
    """Что ждёт лично этого человека — для панели в разделе «Уведомления».

    Сюда попадает и своё («вам выдают, подтвердите»), и складское («человек
    возвращает, примите»). Так же устроен склад проекта, и человек находит
    подтверждение там, куда и так идёт смотреть, что от него ждут.
    """
    access = Access(db, current_user_id)
    query = db.query(EquipmentIssue).filter(
        EquipmentIssue.status.in_(("pending", "return_pending")))
    rows = query.order_by(EquipmentIssue.created_at.desc()).all()

    mine = [r for r in rows if r.status == "pending" and r.receiver_id == current_user_id]
    returns = [r for r in rows if r.status == "return_pending"] if access.can_accept_return else []
    # Выдачу, которую этот человек сам создал, тоже показываем: её можно отменить
    issued = [r for r in rows if r.status == "pending" and r.issuer_id == current_user_id
              and r.receiver_id != current_user_id]

    picked = mine + returns + issued
    items = {i.id: i for i in db.query(EquipmentItem).filter(
        EquipmentItem.id.in_([r.item_id for r in picked] or [0])).all()}
    who = people_map(db, [x for r in picked for x in (r.issuer_id, r.receiver_id)])

    def out(row, kind):
        item = items.get(row.item_id)
        return {
            "id": row.id, "kind": kind, "item_id": row.item_id,
            "name": (item.name if item else "") or ("позиция №%d" % row.item_id),
            "unit": (item.unit if item else "") or "",
            "quantity": float(row.quantity or 0),
            "issuer": person(who.get(row.issuer_id)),
            "receiver": person(who.get(row.receiver_id)),
        }

    return {"items": [out(r, "receive") for r in mine]
                     + [out(r, "return") for r in returns]
                     + [out(r, "sent") for r in issued]}


# ── журнал строки ────────────────────────────────────────────────────────

@router.get("/equipment/{item_id}/history")
def history(item_id: int, db: Session = Depends(get_db),
            current_user_id: int = Depends(get_current_user_id)):
    """Журнал позиции: сначала выдачи и возвраты, ниже правки значений"""
    load_item(db, item_id)
    rows = db.query(EquipmentLog).filter(EquipmentLog.item_id == item_id) \
        .order_by(EquipmentLog.created_at.desc(), EquipmentLog.id.desc()).limit(300).all()
    who = people_map(db, [r.user_id for r in rows] + [r.target_id for r in rows])
    return {
        "issues": [log_out(r, who) for r in rows if r.kind == "issue"],
        "cells": [log_out(r, who) for r in rows if r.kind != "issue"],
    }
