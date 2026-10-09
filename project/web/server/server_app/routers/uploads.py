# -*- coding: utf-8 -*-
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.requests import ClientDisconnect

from ..config import logger
from ..core import get_db, get_current_user_id
from ..chunked import ChunkError
from .. import intake, blobs

router = APIRouter()


class SessionIn(BaseModel):
    name: str = "file"
    size: Optional[int] = None
    kind: Optional[str] = ""
    meta: Optional[dict] = None


class CompleteIn(BaseModel):
    size: Optional[int] = None


class ProbeIn(BaseModel):
    size: int
    sample: str


class CheckIn(BaseModel):
    tree: str
    size: int
    name: Optional[str] = None


class AnswerIn(BaseModel):
    id: str
    hashes: list = []


def _fail(e: ChunkError):
    raise HTTPException(status_code=e.status, detail={"detail": e.detail, "received": e.received})


# ── Сеансы порционной загрузки ──
@router.post("/uploads/sessions")
async def create_session(body: SessionIn, user_id: int = Depends(get_current_user_id)):
    try:
        return intake.chunk_store().create(user_id, body.name, body.size, body.kind or "", body.meta or {})
    except ChunkError as e:
        _fail(e)


@router.get("/uploads/sessions/{sid}")
def session_status(sid: str, user_id: int = Depends(get_current_user_id)):
    try:
        return intake.chunk_store().status(user_id, sid)
    except ChunkError as e:
        _fail(e)


@router.put("/uploads/sessions/{sid}")
async def session_append(sid: str, offset: int, request: Request, user_id: int = Depends(get_current_user_id)):
    store = intake.chunk_store()
    data = bytearray()
    try:
        async for part in request.stream():
            data += part
            if len(data) > store.chunk_max:
                raise HTTPException(status_code=413, detail="Слишком большая порция")
    except ClientDisconnect:
        # клиент пропал посреди порции: её просто не засчитываем, клиент дошлёт заново
        logger.info("Порция загрузки %s оборвалась на %d байт", sid, len(data))
        raise HTTPException(status_code=400, detail="Передача порции оборвалась")
    crc = request.headers.get("x-chunk-crc32")
    import asyncio
    try:
        return await asyncio.to_thread(store.append, user_id, sid, offset, bytes(data), int(crc) if crc else None)
    except ChunkError as e:
        _fail(e)


@router.post("/uploads/sessions/{sid}/complete")
async def session_complete(sid: str, body: CompleteIn, user_id: int = Depends(get_current_user_id)):
    import asyncio
    try:
        return await asyncio.to_thread(intake.chunk_store().complete, user_id, sid, body.size)
    except ChunkError as e:
        _fail(e)


@router.delete("/uploads/sessions/{sid}")
async def session_abort(sid: str, user_id: int = Depends(get_current_user_id)):
    try:
        intake.chunk_store().abort(user_id, sid)
    except ChunkError:
        pass
    return {"ok": True}


# ── Проверка копии по содержимому ──
@router.post("/uploads/dedup/check")
async def dedup_check(body: CheckIn, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    return blobs.issue_challenge(db, user_id, body.tree, body.size)


@router.post("/uploads/dedup/probe")
def dedup_probe(body: ProbeIn, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    # быстрая проверка по образцу: полный отпечаток нужен, только если кандидат есть; в потоке — не держит цикл
    return blobs.probe(db, user_id, body.size, body.sample)


@router.post("/uploads/dedup/answer")
async def dedup_answer(body: AnswerIn, user_id: int = Depends(get_current_user_id)):
    return {"token": blobs.answer_challenge(user_id, body.id, body.hashes)}
