# -*- coding: utf-8 -*-
"""Настройки пользователя в JSON: сортировки и фильтры разделов, параметры веб-версии.

Ключи — произвольные строки до 64 символов, например:
  projects.list   — {"sort": "deadline_desc", "organization": null, "head_id": null}
  projects.tasks  — {"state": "active", "sort": "deadline_asc", ...}
  web.settings    — {"scale": 1.0, "theme": "light"}
"""

import re
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from server.database.models import UserPreference
from ..core import get_current_user_id, get_db
from ..schemas import PreferenceIn, PreferenceOut

router = APIRouter()
_KEY_RE = re.compile(r"^[a-zA-Z0-9_.\-]{1,64}$")


def _check_key(key: str) -> str:
    if not _KEY_RE.match(key or ""):
        raise HTTPException(status_code=400, detail="Недопустимый ключ настройки")
    return key


@router.get("/preferences", response_model=List[PreferenceOut])
def list_preferences(db: Session = Depends(get_db),
                           current_user_id: int = Depends(get_current_user_id)):
    rows = db.query(UserPreference).filter(UserPreference.user_id == current_user_id).all()
    return [{"key": r.key, "value": r.value, "updated_at": r.updated_at} for r in rows]


@router.get("/preferences/{key}", response_model=PreferenceOut)
def get_preference(key: str, db: Session = Depends(get_db),
                         current_user_id: int = Depends(get_current_user_id)):
    key = _check_key(key)
    row = db.query(UserPreference).filter(UserPreference.user_id == current_user_id,
                                          UserPreference.key == key).first()
    if not row:
        return {"key": key, "value": None, "updated_at": None}
    return {"key": row.key, "value": row.value, "updated_at": row.updated_at}


@router.put("/preferences/{key}", response_model=PreferenceOut)
async def put_preference(key: str, data: PreferenceIn, db: Session = Depends(get_db),
                         current_user_id: int = Depends(get_current_user_id)):
    key = _check_key(key)
    row = db.query(UserPreference).filter(UserPreference.user_id == current_user_id,
                                          UserPreference.key == key).first()
    if not row:
        row = UserPreference(user_id=current_user_id, key=key)
        db.add(row)
    row.value = data.value
    row.updated_at = datetime.now()
    db.commit()
    db.refresh(row)
    return {"key": row.key, "value": row.value, "updated_at": row.updated_at}


@router.delete("/preferences/{key}")
async def delete_preference(key: str, db: Session = Depends(get_db),
                            current_user_id: int = Depends(get_current_user_id)):
    key = _check_key(key)
    db.query(UserPreference).filter(UserPreference.user_id == current_user_id,
                                    UserPreference.key == key).delete(synchronize_session=False)
    db.commit()
    return {"status": "deleted"}
