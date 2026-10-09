# -*- coding: utf-8 -*-
"""Склад проекта: ведомость товаров, выдача под подтверждение, история правок.

Правила доступа (по постановке заказчика):
  * видеть раздел — «Бухгалтерия», «Монтажный отдел», «Склад», директор,
    администратор;
  * править «Товар» и «Количество» — директор, администратор и те, кого они
    внесли в общий список (WarehouseEditor, один на все проекты);
  * править «Выдано» и «Количество на складе» — отдел «Склад»; выдача идёт
    только через заявку с указанием получателя и ждёт его подтверждения;
  * директор и администратор могут всё.

«Выдано» отдельным полем не хранится: это сумма подтверждённых выдач, иначе
цифра и история разъезжаются. «Осталось» — количество минус выдано.
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from server.database.models import (Employee, Notification, Project, WarehouseCellLog,
                                    WarehouseEditor, WarehouseIssue, WarehouseItem, WarehouseUnit)

from ..config import logger
from ..core import MANAGEMENT_STATUSES, get_current_user_id, get_db
from ..project_permissions import get_employee, is_management
from ..push import push_notification
from ..realtime import manager

router = APIRouter()

ROWS = 100                       # строк в ведомости, как в бумажной
# Право выдавать раньше определялось названием должности «Кладовщик»:
# переименование должности молча забирало права, а опечатка — выдавала.
# Теперь решает отдел сотрудника, как и в остальных проверках.
WAREHOUSE_DEPARTMENT = "Склад"
ALLOWED_DEPARTMENTS = ("Бухгалтерия", "Монтажный отдел", WAREHOUSE_DEPARTMENT)
DEFAULT_UNITS = (("шт", "Штука"), ("м", "Метр"), ("м²", "Квадратный метр"), ("кг", "Килограмм"),
                 ("л", "Литр"), ("компл", "Комплект"), ("уп", "Упаковка"))


# ── права ────────────────────────────────────────────────────────────────

class Access:
    def __init__(self, db: Session, user_id: int):
        self.emp = get_employee(db, user_id)
        self.user_id = user_id
        self.management = is_management(self.emp)
        department = ((self.emp.department if self.emp else "") or "").strip()
        self.storekeeper = department == WAREHOUSE_DEPARTMENT
        self.can_view = self.management or department in ALLOWED_DEPARTMENTS
        self.can_edit_goods = self.management or bool(
            db.query(WarehouseEditor.id).filter(WarehouseEditor.user_id == user_id).first())
        self.can_issue = self.management or self.storekeeper

    def as_dict(self) -> dict:
        return {"can_view": self.can_view, "can_edit_goods": self.can_edit_goods,
                "can_issue": self.can_issue, "is_management": self.management,
                "is_storekeeper": self.storekeeper, "user_id": self.user_id}


def require_view(db: Session, user_id: int, project_id: int) -> Access:
    if not db.query(Project.id).filter(Project.id == project_id).first():
        raise HTTPException(status_code=404, detail="Проект не найден")
    access = Access(db, user_id)
    if not access.can_view:
        raise HTTPException(status_code=403,
                            detail="Склад доступен бухгалтерии, монтажному отделу, "
                                   "отделу склада, директору и администратору")
    return access


def unit_out(u: WarehouseUnit) -> dict:
    return {"id": u.id, "name": u.name, "full_name": u.full_name or ""}


def person(emp: Optional[Employee]) -> Optional[dict]:
    if not emp:
        return None
    return {"id": emp.id, "last_name": emp.last_name, "first_name": emp.first_name,
            "name": " ".join(x for x in [emp.last_name, emp.first_name] if x).strip()}


# ── схемы ────────────────────────────────────────────────────────────────

class CellUpdate(BaseModel):
    name: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    stock_state: Optional[str] = None
    stock_qty: Optional[float] = None
    fields: List[str] = Field(default_factory=list)   # какие поля правим на самом деле


class UnitIn(BaseModel):
    name: str
    full_name: Optional[str] = None


class IssueIn(BaseModel):
    quantity: float
    receiver_id: int


class EditorsIn(BaseModel):
    user_ids: List[int]


class BulkRow(BaseModel):
    name: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None


class BulkIn(BaseModel):
    start_row: int
    rows: List[BulkRow]


class UnitRename(BaseModel):
    name: Optional[str] = None
    full_name: Optional[str] = None


# ── вспомогательное ──────────────────────────────────────────────────────

def ensure_rows(db: Session, project_id: int) -> List[WarehouseItem]:
    """Ведомость всегда полной длины: недостающие строки создаём пустыми."""
    items = db.query(WarehouseItem).filter(WarehouseItem.project_id == project_id).all()
    have = {i.row_no: i for i in items}
    missing = [n for n in range(1, ROWS + 1) if n not in have]
    if missing:
        for n in missing:
            row = WarehouseItem(project_id=project_id, row_no=n)
            db.add(row)
            have[n] = row
        db.commit()
        for n in missing:
            db.refresh(have[n])
    return [have[n] for n in range(1, ROWS + 1)]


def issued_map(db: Session, project_id: int) -> dict:
    """По каждой строке: сколько подтверждено и сколько ждёт подтверждения."""
    rows = db.query(WarehouseIssue.item_id, WarehouseIssue.quantity, WarehouseIssue.status) \
        .filter(WarehouseIssue.project_id == project_id,
                WarehouseIssue.status.in_(("confirmed", "pending"))).all()
    out: dict = {}
    for item_id, qty, state in rows:
        slot = out.setdefault(item_id, {"issued": 0.0, "pending": 0.0})
        slot["issued" if state == "confirmed" else "pending"] += float(qty or 0)
    return out


def last_editors(db: Session, item_ids: List[int]) -> dict:
    """По каждой ячейке — кто и когда её menял последним.

    Берём максимальный id записи журнала на пару (строка, поле) — одним
    запросом на весь проект, иначе на сто строк ушло бы шестьсот выборок.
    """
    if not item_ids:
        return {}
    latest = db.query(func.max(WarehouseCellLog.id)) \
        .filter(WarehouseCellLog.item_id.in_(item_ids)) \
        .group_by(WarehouseCellLog.item_id, WarehouseCellLog.field).all()
    ids = [row[0] for row in latest if row and row[0]]
    if not ids:
        return {}
    rows = db.query(WarehouseCellLog).filter(WarehouseCellLog.id.in_(ids)).all()
    people = {e.id: person(e) for e in db.query(Employee).filter(
        Employee.id.in_([r.user_id for r in rows if r.user_id] or [0])).all()}
    out: dict = {}
    for r in rows:
        who = people.get(r.user_id)
        out.setdefault(r.item_id, {})[r.field] = {
            "who": (who or {}).get("name") or "неизвестно",
            "at": r.created_at.isoformat() if r.created_at else None,
        }
    return out


def item_out(item: WarehouseItem, stats: dict, editors: Optional[dict] = None) -> dict:
    issued = float(stats.get("issued", 0) or 0)
    quantity = float(item.quantity) if item.quantity is not None else None
    return {
        "row_no": item.row_no, "id": item.id,
        "name": item.name or "", "quantity": quantity, "unit": item.unit or "",
        "issued": issued,
        "pending": float(stats.get("pending", 0) or 0),
        "left": None if quantity is None else round(quantity - issued, 3),
        "stock_state": item.stock_state, "stock_qty": item.stock_qty,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        "by": editors or {},
    }


def viewers(db: Session) -> set:
    """Кто вообще видит склад: им и рассылаем изменения."""
    try:
        rows = db.query(Employee.id).filter(
            (Employee.department.in_(ALLOWED_DEPARTMENTS))
            | (func.lower(func.coalesce(Employee.status, "")).in_(MANAGEMENT_STATUSES))).all()
        return {r[0] for r in rows if r and r[0]}
    except Exception as e:
        logger.debug("Склад: список зрителей не собран: %s", e)
        return set()


async def send_all(payload: dict, targets) -> None:
    for user_id in {t for t in targets if t}:
        try:
            await manager.send_personal_message(payload, user_id)
        except Exception as e:
            logger.debug("Склад: сокет %s: %s", user_id, e)


def fire(coro) -> None:
    """Отправка из обычного (не асинхронного) обработчика.

    Обработчики правки ячеек синхронные: FastAPI выполняет их в рабочем
    потоке. Отправить оттуда в сокет напрямую нельзя, поэтому передаём
    корутину в основной цикл. Если это почему-то не выходит, изменение всё
    равно сохранено — просто соседи увидят его при следующем чтении.
    """
    try:
        import anyio.from_thread as aft
        aft.run(lambda: coro)          # мы в рабочем потоке FastAPI: отдаём в его цикл
        return
    except Exception as e:
        logger.debug("Склад: рассылка через поток не прошла, пробуем цикл: %s", e)
    try:
        import asyncio
        loop = _MAIN_LOOP
        if loop and not loop.is_closed():
            asyncio.run_coroutine_threadsafe(coro, loop)
            return
    except Exception as e:
        logger.debug("Склад: рассылку не удалось поставить в очередь: %s", e)
    try:
        coro.close()
    except Exception as e:
        logger.debug("Склад: рассылка не поставлена в очередь: %s", e)


_MAIN_LOOP = None


def remember_loop(loop) -> None:
    """Основной цикл событий запоминается при старте приложения."""
    global _MAIN_LOOP
    _MAIN_LOOP = loop


def broadcast_change(db: Session, project_id: Optional[int], what: str, actor_id: int, **extra) -> None:
    """Сообщает всем, кто смотрит склад, что данные изменились.

    Полный список строк не шлём: клиент перечитывает таблицу сам. Так у всех
    гарантированно одинаковые цифры, а не собранные из кусочков события.
    """
    payload = {"type": "warehouse.updated", "what": what, "project_id": project_id,
               "actor_id": actor_id}
    payload.update(extra)
    fire(send_all(payload, viewers(db) | {actor_id}))


async def push_event(db: Session, issue: WarehouseIssue, action: str, actor_id: int) -> None:
    """Сообщаем о выдаче по сокету: у обеих сторон таблица и список ожидающих
    обновляются сами, без перезагрузки страницы.

    Кому шлём: участникам выдачи и тем, кто вообще видит склад (бухгалтерия,
    монтажный отдел, руководство) — они смотрят ту же ведомость.
    """
    item = db.query(WarehouseItem).filter(WarehouseItem.id == issue.item_id).first()
    payload = {
        "type": "warehouse.issue", "action": action,      # created | confirmed | cancelled
        "issue_id": issue.id, "project_id": issue.project_id,
        "row_no": item.row_no if item else None,
        "name": (item.name if item else "") or "",
        "quantity": float(issue.quantity or 0),
        "issuer_id": issue.issuer_id, "receiver_id": issue.receiver_id,
        "actor_id": actor_id,
    }
    targets = {issue.issuer_id, issue.receiver_id}
    try:
        watchers = db.query(Employee.id).filter(
            (Employee.department.in_(ALLOWED_DEPARTMENTS))
            | (func.lower(func.coalesce(Employee.status, "")).in_(MANAGEMENT_STATUSES))).all()
        targets.update(w[0] for w in watchers)
    except Exception as e:
        logger.debug("Склад: список зрителей не собран: %s", e)
    for user_id in {t for t in targets if t}:
        try:
            await manager.send_personal_message(payload, user_id)
        except Exception as e:
            logger.debug("Склад: сокет %s: %s", user_id, e)


async def notify(db: Session, user_id: int, message: str, target_id: Optional[int]) -> None:
    """Уведомление получателю: запись в разделе, сокет и push — как везде."""
    try:
        note = Notification(employee_id=user_id, message=message,
                            target_section="warehouse", target_id=target_id,
                            created_at=datetime.now())
        db.add(note)
        db.commit()
        db.refresh(note)
    except Exception as e:
        db.rollback()
        logger.error("Склад: уведомление не создано: %s", e)
        return
    try:
        await manager.send_personal_message({
            "type": "notification.new", "notification_id": note.id, "message": note.message,
            "target_section": note.target_section, "target_id": note.target_id}, user_id)
    except Exception as e:
        logger.debug("Склад: сокет %s", e)
    try:
        await push_notification(user_id, message, "warehouse", target_id)
    except Exception as e:
        logger.debug("Склад: push %s", e)


def log_cell(db: Session, item: WarehouseItem, field: str, old, new, user_id: int) -> None:
    db.add(WarehouseCellLog(item_id=item.id, field=field,
                            old_value=None if old is None else str(old)[:255],
                            new_value=None if new is None else str(new)[:255],
                            user_id=user_id, created_at=datetime.now()))


# ── таблица ──────────────────────────────────────────────────────────────


def _readonly_if_archived(db: Session, project_id: int, access: dict) -> dict:
    """У завершённого проекта все права склада выключены — только просмотр."""
    from server.database.models import Project as _Project
    if db.query(_Project.is_archived).filter(_Project.id == project_id).scalar():
        return {k: (False if k.startswith("can_") else v) for k, v in access.items()}
    return access

@router.get("/projects/{project_id}/warehouse")
def get_warehouse(project_id: int, db: Session = Depends(get_db),
                  current_user_id: int = Depends(get_current_user_id)):
    access = require_view(db, current_user_id, project_id)
    items = ensure_rows(db, project_id)
    stats = issued_map(db, project_id)
    rows_units = db.query(WarehouseUnit).order_by(WarehouseUnit.name).all()
    if not rows_units:
        for short, full in DEFAULT_UNITS:
            db.add(WarehouseUnit(name=short, full_name=full, created_by=current_user_id))
        db.commit()
        rows_units = db.query(WarehouseUnit).order_by(WarehouseUnit.name).all()
    units = [unit_out(u) for u in rows_units]

    pending = db.query(WarehouseIssue).filter(WarehouseIssue.project_id == project_id,
                                              WarehouseIssue.status == "pending") \
        .order_by(WarehouseIssue.created_at.desc()).all()
    people = {e.id: e for e in db.query(Employee).filter(
        Employee.id.in_([i for issue in pending for i in (issue.issuer_id, issue.receiver_id) if i])).all()}
    rows_by_id = {i.id: i for i in items}
    editors = last_editors(db, [i.id for i in items])
    return {
        "rows": [item_out(i, stats.get(i.id, {}), editors.get(i.id)) for i in items],
        "units": units,
        "access": _readonly_if_archived(db, project_id, access.as_dict()),   # завершённый проект — склад только для просмотра
        "pending": [{
            "id": p.id, "row_no": rows_by_id[p.item_id].row_no if p.item_id in rows_by_id else None,
            "item_id": p.item_id, "quantity": float(p.quantity or 0),
            "issuer": person(people.get(p.issuer_id)), "receiver": person(people.get(p.receiver_id)),
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "mine": p.receiver_id == current_user_id,
        } for p in pending],
    }


@router.patch("/projects/{project_id}/warehouse/{row_no}")
def update_cell(project_id: int, row_no: int, payload: CellUpdate,
                db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user_id)):
    access = require_view(db, current_user_id, project_id)
    if row_no < 1 or row_no > ROWS:
        raise HTTPException(status_code=400, detail="Строки с таким номером нет")
    ensure_rows(db, project_id)
    item = db.query(WarehouseItem).filter(WarehouseItem.project_id == project_id,
                                          WarehouseItem.row_no == row_no).first()
    if not item:
        raise HTTPException(status_code=404, detail="Строка не найдена")

    goods = {"name", "quantity", "unit"}
    stock = {"stock_state", "stock_qty"}
    fields = set(payload.fields) or set()
    if not fields:
        raise HTTPException(status_code=400, detail="Не указано, какие поля меняются")
    unknown = fields - goods - stock
    if unknown:
        raise HTTPException(status_code=400, detail=f"Неизвестные поля: {', '.join(sorted(unknown))}")
    if fields & goods and not access.can_edit_goods:
        raise HTTPException(status_code=403,
                            detail="«Товар» и «Количество» правят директор, администратор и назначенные ими сотрудники")
    if fields & stock and not access.can_issue:
        raise HTTPException(status_code=403, detail="«Количество на складе» ведёт отдел склада")

    if "stock_state" in fields and payload.stock_state not in (None, "", "enough", "short"):
        raise HTTPException(status_code=400, detail="Состояние склада: «Достаточно» или «Недостаточно»")

    changed = []
    for field in fields:
        new = getattr(payload, field)
        if field == "name":
            new = (new or "").strip()[:255]
        if field == "unit":
            new = (new or "").strip()[:32]
            if new and not db.query(WarehouseUnit.id).filter(WarehouseUnit.name == new).first():
                db.add(WarehouseUnit(name=new, created_by=current_user_id))
        if field in ("quantity", "stock_qty") and new is not None and float(new) < 0:
            raise HTTPException(status_code=400, detail="Количество не может быть отрицательным")
        old = getattr(item, field)
        if (old or None) == (new or None):
            continue
        log_cell(db, item, field, old, new, current_user_id)
        setattr(item, field, new)
        changed.append(field)

    if changed:
        item.updated_by = current_user_id
        item.updated_at = datetime.now()
        db.commit()
        db.refresh(item)
    if changed:
        broadcast_change(db, project_id, "cell", current_user_id, row_no=row_no, fields=changed)
    stats = issued_map(db, project_id).get(item.id, {})
    return {"row": item_out(item, stats, last_editors(db, [item.id]).get(item.id)), "changed": changed}


@router.post("/projects/{project_id}/warehouse/bulk")
def bulk_update(project_id: int, payload: BulkIn, db: Session = Depends(get_db),
                current_user_id: int = Depends(get_current_user_id)):
    """Вставка из буфера: пачка строк начиная с указанной.

    Проверки те же, что у одиночной правки, но одним запросом — иначе двадцать
    строк дали бы двадцать запросов и половинчатый результат при обрыве связи.
    """
    access = require_view(db, current_user_id, project_id)
    if not access.can_edit_goods:
        raise HTTPException(status_code=403,
                            detail="«Товар» и «Количество» правят директор, администратор и назначенные ими сотрудники")
    start = int(payload.start_row or 1)
    if start < 1 or start > ROWS:
        raise HTTPException(status_code=400, detail="Строки с таким номером нет")
    if not payload.rows:
        raise HTTPException(status_code=400, detail="Вставлять нечего")
    if start + len(payload.rows) - 1 > ROWS:
        raise HTTPException(status_code=400,
                            detail=f"Не помещается: со строки {start} свободно {ROWS - start + 1} строк, а вставляется {len(payload.rows)}")

    ensure_rows(db, project_id)
    items = {i.row_no: i for i in db.query(WarehouseItem)
             .filter(WarehouseItem.project_id == project_id,
                     WarehouseItem.row_no.between(start, start + len(payload.rows) - 1)).all()}
    known_units = {u.name for u in db.query(WarehouseUnit).all()}
    touched = []
    for offset, incoming in enumerate(payload.rows):
        item = items.get(start + offset)
        if not item:
            continue
        for field in ("name", "quantity", "unit"):
            new = getattr(incoming, field)
            if new is None:
                continue
            if field in ("name", "unit"):
                new = str(new).strip()[:255 if field == "name" else 32]
            if field == "quantity" and float(new) < 0:
                raise HTTPException(status_code=400,
                                    detail=f"Строка {start + offset}: количество не может быть отрицательным")
            if field == "unit" and new and new not in known_units:
                db.add(WarehouseUnit(name=new, created_by=current_user_id))
                known_units.add(new)
            old = getattr(item, field)
            if (old or None) == (new or None):
                continue
            log_cell(db, item, field, old, new, current_user_id)
            setattr(item, field, new)
        item.updated_by = current_user_id
        item.updated_at = datetime.now()
        touched.append(item)
    db.commit()
    broadcast_change(db, project_id, "bulk", current_user_id, count=len(touched))
    stats = issued_map(db, project_id)
    editors = last_editors(db, [i.id for i in touched])
    return {"rows": [item_out(i, stats.get(i.id, {}), editors.get(i.id)) for i in touched],
            "count": len(touched)}


# ── единицы измерения ────────────────────────────────────────────────────

@router.get("/warehouse/units")
def list_units(db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user_id)):
    return [unit_out(u) for u in db.query(WarehouseUnit).order_by(WarehouseUnit.name).all()]


@router.get("/warehouse/units/full")
def list_units_full(db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user_id)):
    """Единицы вместе с числом строк, где они уже стоят: удалять можно только неиспользуемые."""
    access = Access(db, current_user_id)
    if not access.can_view:
        raise HTTPException(status_code=403, detail="Нет доступа к складу")
    units = db.query(WarehouseUnit).order_by(WarehouseUnit.name).all()
    used = dict(db.query(WarehouseItem.unit, func.count(WarehouseItem.id))
                .filter(WarehouseItem.unit.isnot(None), WarehouseItem.unit != "")
                .group_by(WarehouseItem.unit).all())
    return {"units": [{"id": u.id, "name": u.name, "full_name": u.full_name or "",
                       "used": int(used.get(u.name, 0))} for u in units],
            "can_manage": access.management,
            "can_add": access.can_edit_goods or access.can_issue}


@router.patch("/warehouse/units/{unit_id}")
def rename_unit(unit_id: int, payload: UnitRename, db: Session = Depends(get_db),
                current_user_id: int = Depends(get_current_user_id)):
    access = Access(db, current_user_id)
    if not access.management:
        raise HTTPException(status_code=403, detail="Справочник единиц ведут директор и администратор")
    unit = db.query(WarehouseUnit).filter(WarehouseUnit.id == unit_id).first()
    if not unit:
        raise HTTPException(status_code=404, detail="Единица не найдена")
    moved = 0
    if payload.full_name is not None:
        unit.full_name = (payload.full_name or "").strip()[:64] or None
    if payload.name is not None:
        name = (payload.name or "").strip()[:32]
        if not name:
            raise HTTPException(status_code=400, detail="Краткое обозначение пустое")
        if name != unit.name:
            if db.query(WarehouseUnit.id).filter(WarehouseUnit.name == name).first():
                raise HTTPException(status_code=400, detail=f"Единица «{name}» уже есть в справочнике")
            old = unit.name
            # переименование тянет за собой все строки, иначе в таблице останется старое слово
            moved = db.query(WarehouseItem).filter(WarehouseItem.unit == old).update({"unit": name})
            unit.name = name
            logger.info("Склад: единица «%s» переименована в «%s», строк: %s", old, name, moved)
    db.commit()
    broadcast_change(db, None, "units", current_user_id, unit=unit.name)
    return {"id": unit.id, "name": unit.name, "full_name": unit.full_name or "", "moved": int(moved or 0)}


@router.delete("/warehouse/units/{unit_id}")
def delete_unit(unit_id: int, db: Session = Depends(get_db),
                current_user_id: int = Depends(get_current_user_id)):
    access = Access(db, current_user_id)
    if not access.management:
        raise HTTPException(status_code=403, detail="Справочник единиц ведут директор и администратор")
    unit = db.query(WarehouseUnit).filter(WarehouseUnit.id == unit_id).first()
    if not unit:
        raise HTTPException(status_code=404, detail="Единица не найдена")
    used = db.query(func.count(WarehouseItem.id)).filter(WarehouseItem.unit == unit.name).scalar() or 0
    if used:
        raise HTTPException(status_code=400,
                            detail=f"Единица стоит в {used} строк(ах) — сначала замените её там")
    name = unit.name
    db.delete(unit)
    db.commit()
    broadcast_change(db, None, "units", current_user_id, unit=name)
    return {"deleted": unit_id}


@router.post("/warehouse/units", status_code=status.HTTP_201_CREATED)
def add_unit(payload: UnitIn, db: Session = Depends(get_db),
             current_user_id: int = Depends(get_current_user_id)):
    access = Access(db, current_user_id)
    if not (access.can_edit_goods or access.can_issue):
        raise HTTPException(status_code=403, detail="Справочник пополняют те, кто заполняет таблицу")
    name = (payload.name or "").strip()[:32]
    if not name:
        raise HTTPException(status_code=400, detail="Краткое обозначение пустое")
    exists = db.query(WarehouseUnit).filter(WarehouseUnit.name == name).first()
    if exists:
        raise HTTPException(status_code=400, detail=f"Единица «{name}» уже есть в справочнике")
    db.add(WarehouseUnit(name=name, full_name=(payload.full_name or "").strip()[:64] or None,
                         created_by=current_user_id))
    db.commit()
    broadcast_change(db, None, "units", current_user_id, unit=name)
    return {"name": name, "units": [unit_out(u) for u in db.query(WarehouseUnit).order_by(WarehouseUnit.name).all()]}


# ── выдача товара ────────────────────────────────────────────────────────

@router.post("/projects/{project_id}/warehouse/{row_no}/issue", status_code=status.HTTP_201_CREATED)
async def create_issue(project_id: int, row_no: int, payload: IssueIn,
                       db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user_id)):
    access = require_view(db, current_user_id, project_id)
    if not access.can_issue:
        raise HTTPException(status_code=403, detail="Выдавать товар может отдел склада")
    ensure_rows(db, project_id)
    item = db.query(WarehouseItem).filter(WarehouseItem.project_id == project_id,
                                          WarehouseItem.row_no == row_no).first()
    if not item or not (item.name or "").strip():
        raise HTTPException(status_code=400, detail="В этой строке ещё нет товара")
    qty = float(payload.quantity or 0)
    if qty <= 0:
        raise HTTPException(status_code=400, detail="Количество выдачи должно быть больше нуля")
    receiver = db.query(Employee).filter(Employee.id == payload.receiver_id).first()
    if not receiver:
        raise HTTPException(status_code=404, detail="Получатель не найден")
    if receiver.id == current_user_id:
        raise HTTPException(status_code=400, detail="Выдачу подтверждает получатель, себе выдать нельзя")

    stats = issued_map(db, project_id).get(item.id, {})
    busy = float(stats.get("issued", 0)) + float(stats.get("pending", 0))
    if item.quantity is not None and busy + qty > float(item.quantity) + 1e-6:
        raise HTTPException(status_code=400,
                            detail=f"Осталось выдать {round(float(item.quantity) - busy, 3)} — больше нельзя")

    issue = WarehouseIssue(item_id=item.id, project_id=project_id, quantity=qty,
                           issuer_id=current_user_id, receiver_id=receiver.id,
                           status="pending", created_at=datetime.now())
    db.add(issue)
    db.commit()
    db.refresh(issue)

    await push_event(db, issue, "created", current_user_id)
    issuer = get_employee(db, current_user_id)
    project = db.query(Project).filter(Project.id == project_id).first()
    who = person(issuer) or {}
    await notify(db, receiver.id,
                 f"{who.get('name') or 'Сотрудник склада'} выдаёт вам «{item.name}» — {qty:g} {item.unit or ''}".strip()
                 + f". Подтвердите получение в разделе «Склад» проекта «{project.name if project else ''}»",
                 project_id)
    return {"id": issue.id, "status": issue.status}


@router.get("/warehouse/issues/pending")
def my_pending(db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user_id)):
    """Выдачи, ждущие меня, по всем проектам сразу: и те, что я подтверждаю,
    и те, что я выдал и жду подтверждения."""
    access = Access(db, current_user_id)
    if not access.can_view:
        return {"items": []}
    query = db.query(WarehouseIssue).filter(WarehouseIssue.status == "pending")
    if not access.management:
        query = query.filter((WarehouseIssue.receiver_id == current_user_id)
                             | (WarehouseIssue.issuer_id == current_user_id))
    issues = query.order_by(WarehouseIssue.created_at.desc()).limit(200).all()
    if not issues:
        return {"items": []}
    items = {i.id: i for i in db.query(WarehouseItem)
             .filter(WarehouseItem.id.in_([x.item_id for x in issues])).all()}
    projects = {p.id: p for p in db.query(Project)
                .filter(Project.id.in_([x.project_id for x in issues])).all()}
    people = {e.id: person(e) for e in db.query(Employee).filter(Employee.id.in_(
        [i for x in issues for i in (x.issuer_id, x.receiver_id) if i])).all()}
    return {"items": [{
        "id": x.id, "project_id": x.project_id,
        "project": projects[x.project_id].name if x.project_id in projects else "",
        "row_no": items[x.item_id].row_no if x.item_id in items else None,
        "name": (items[x.item_id].name if x.item_id in items else "") or "",
        "unit": (items[x.item_id].unit if x.item_id in items else "") or "",
        "quantity": float(x.quantity or 0),
        "issuer": people.get(x.issuer_id), "receiver": people.get(x.receiver_id),
        "created_at": x.created_at.isoformat() if x.created_at else None,
        "mine": x.receiver_id == current_user_id,
    } for x in issues]}


@router.post("/warehouse/issues/{issue_id}/confirm")
async def confirm_issue(issue_id: int, db: Session = Depends(get_db),
                        current_user_id: int = Depends(get_current_user_id)):
    issue = db.query(WarehouseIssue).filter(WarehouseIssue.id == issue_id).first()
    if not issue:
        raise HTTPException(status_code=404, detail="Выдача не найдена")
    if issue.status != "pending":
        raise HTTPException(status_code=400, detail="Эта выдача уже закрыта")
    access = Access(db, current_user_id)
    if issue.receiver_id != current_user_id and not access.management:
        raise HTTPException(status_code=403, detail="Подтвердить получение может только получатель")
    issue.status = "confirmed"
    issue.confirmed_at = datetime.now()
    db.commit()

    await push_event(db, issue, "confirmed", current_user_id)
    item = db.query(WarehouseItem).filter(WarehouseItem.id == issue.item_id).first()
    receiver = person(get_employee(db, issue.receiver_id)) or {}
    if issue.issuer_id:
        await notify(db, issue.issuer_id,
                     f"{receiver.get('name') or 'Сотрудник'} подтвердил получение "
                     f"«{item.name if item else ''}» — {float(issue.quantity):g}", issue.project_id)
    return {"id": issue.id, "status": issue.status}


@router.post("/warehouse/issues/{issue_id}/cancel")
async def cancel_issue(issue_id: int, db: Session = Depends(get_db),
                       current_user_id: int = Depends(get_current_user_id)):
    issue = db.query(WarehouseIssue).filter(WarehouseIssue.id == issue_id).first()
    if not issue:
        raise HTTPException(status_code=404, detail="Выдача не найдена")
    if issue.status != "pending":
        raise HTTPException(status_code=400, detail="Отменить можно только неподтверждённую выдачу")
    access = Access(db, current_user_id)
    if current_user_id not in (issue.issuer_id, issue.receiver_id) and not access.management:
        raise HTTPException(status_code=403, detail="Отменить выдачу может тот, кто её начал, или получатель")
    issue.status = "cancelled"
    issue.cancelled_at = datetime.now()
    issue.cancelled_by = current_user_id
    db.commit()

    await push_event(db, issue, "cancelled", current_user_id)
    item = db.query(WarehouseItem).filter(WarehouseItem.id == issue.item_id).first()
    who = person(get_employee(db, current_user_id)) or {}
    other = issue.receiver_id if current_user_id == issue.issuer_id else issue.issuer_id
    if other:
        await notify(db, other, f"{who.get('name') or 'Сотрудник'} отменил выдачу "
                                f"«{item.name if item else ''}» — {float(issue.quantity):g}", issue.project_id)
    return {"id": issue.id, "status": issue.status}


# ── история строки ───────────────────────────────────────────────────────

FIELD_TITLES = {"name": "Товар", "quantity": "Количество", "unit": "Единица",
                "stock_state": "Количество на складе", "stock_qty": "Количество на складе"}
STATE_TITLES = {"enough": "Достаточно", "short": "Недостаточно"}


@router.get("/projects/{project_id}/warehouse/{row_no}/history")
def row_history(project_id: int, row_no: int, db: Session = Depends(get_db),
                current_user_id: int = Depends(get_current_user_id)):
    require_view(db, current_user_id, project_id)
    item = db.query(WarehouseItem).filter(WarehouseItem.project_id == project_id,
                                          WarehouseItem.row_no == row_no).first()
    if not item:
        return {"edits": [], "issues": []}
    logs = db.query(WarehouseCellLog).filter(WarehouseCellLog.item_id == item.id) \
        .order_by(WarehouseCellLog.created_at.desc()).limit(100).all()
    issues = db.query(WarehouseIssue).filter(WarehouseIssue.item_id == item.id) \
        .order_by(WarehouseIssue.created_at.desc()).limit(100).all()
    ids = {l.user_id for l in logs} | {i for issue in issues for i in (issue.issuer_id, issue.receiver_id)}
    people = {e.id: person(e) for e in db.query(Employee).filter(Employee.id.in_([i for i in ids if i])).all()}

    def value(field, raw):
        if field == "stock_state":
            return STATE_TITLES.get(raw, raw)
        return raw

    return {
        "edits": [{
            "field": l.field, "field_title": FIELD_TITLES.get(l.field, l.field),
            "old": value(l.field, l.old_value), "new": value(l.field, l.new_value),
            "who": people.get(l.user_id), "at": l.created_at.isoformat() if l.created_at else None,
        } for l in logs],
        "issues": [{
            "id": i.id, "quantity": float(i.quantity or 0), "status": i.status,
            "issuer": people.get(i.issuer_id), "receiver": people.get(i.receiver_id),
            "at": i.created_at.isoformat() if i.created_at else None,
            "confirmed_at": i.confirmed_at.isoformat() if i.confirmed_at else None,
            "cancelled_at": i.cancelled_at.isoformat() if i.cancelled_at else None,
        } for i in issues],
    }


# ── кому доверено править «Товар» и «Количество» ─────────────────────────

@router.get("/warehouse/editors")
def list_editors(db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user_id)):
    access = Access(db, current_user_id)
    if not access.can_view:
        raise HTTPException(status_code=403, detail="Нет доступа к складу")
    rows = db.query(WarehouseEditor).all()
    people = {e.id: person(e) for e in db.query(Employee).filter(
        Employee.id.in_([r.user_id for r in rows] or [0])).all()}
    return {"user_ids": [r.user_id for r in rows],
            "people": [people[r.user_id] for r in rows if r.user_id in people],
            "can_manage": access.management}


@router.put("/warehouse/editors")
def set_editors(payload: EditorsIn, db: Session = Depends(get_db),
                current_user_id: int = Depends(get_current_user_id)):
    access = Access(db, current_user_id)
    if not access.management:
        raise HTTPException(status_code=403, detail="Список доверенных ведут директор и администратор")
    wanted = {int(i) for i in payload.user_ids}
    known = {e.id for e in db.query(Employee.id).filter(Employee.id.in_(wanted or {0})).all()}
    missing = wanted - known
    if missing:
        raise HTTPException(status_code=404, detail=f"Сотрудники не найдены: {sorted(missing)}")
    current = {r.user_id: r for r in db.query(WarehouseEditor).all()}
    for user_id in wanted - set(current):
        db.add(WarehouseEditor(user_id=user_id, added_by=current_user_id, created_at=datetime.now()))
    for user_id in set(current) - wanted:
        db.delete(current[user_id])
    db.commit()
    broadcast_change(db, None, "editors", current_user_id)
    return list_editors(db, current_user_id)
