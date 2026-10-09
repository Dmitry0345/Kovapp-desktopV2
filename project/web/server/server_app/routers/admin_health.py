# -*- coding: utf-8 -*-

import asyncio
from fastapi import FastAPI, Depends, HTTPException, status, Query, Response, UploadFile, File, Form, WebSocket, \
    WebSocketDisconnect
from server.database.init_database import SessionLocal
from server.database.init_database_messenger import SessionLocalChat, BaseChat, engine_chat

from fastapi import APIRouter

from ..backup.services import backup_manager, db_watchdog, devops_notifier
from ..config import MAX_CONCURRENT_DB_CONNECTIONS, pool_status
from ..core import is_admin_user, require_admin_or_local
from ..realtime import manager


router = APIRouter()


def _planned_pool() -> int:
    from .. import lanes
    return lanes.PLAN["db_pool"] + lanes.PLAN["db_overflow"]


@router.get("/health/db-pool")
def db_pool_health(_: bool = Depends(require_admin_or_local)):
    """
    Состояние пулов соединений с БД.

    Полезно, чтобы под нагрузкой видеть, сколько соединений реально занято:
    если checked_out упирается в capacity, значит пул мал либо где-то
    соединения удерживаются дольше, чем нужно.
    """
    return {
        "target_concurrent_connections": _planned_pool(),
        "main_db": pool_status(SessionLocal),
        "chat_db": pool_status(engine_chat),
        "active_websockets": sum(len(s) for s in manager.user_connections.values()),
    }


@router.get("/health/load")
def load_health(_: bool = Depends(require_admin_or_local)):
    """Что сейчас видит распорядитель нагрузки и какие лимиты выставил.

    Сюда стоит смотреть, если кажется, что обработка идёт слишком медленно
    или, наоборот, мешает работе: видно и замеры, и причину решения.
    """
    from ..load_governor import governor
    from .. import lanes
    data = dict(governor().snapshot())
    data["heavy"] = lanes.GATE.snapshot() if lanes.GATE else None
    data["threads"] = lanes.THREADPOOL_SIZE
    data["threads_applied"] = lanes.APPLIED.get("threads")
    data["plan"] = dict(lanes.PLAN, safety=lanes.SAFETY)
    return data


@router.get("/health/file-access")
def file_access_health(_: bool = Depends(require_admin_or_local)):
    from .. import file_access
    return file_access.snapshot()


@router.get("/health/db")
def db_health(_: bool = Depends(require_admin_or_local)):
    """Состояние БД по данным вотчдога. Читается из памяти, базу не нагружает."""
    return db_watchdog.snapshot()


@router.get("/health/office")
async def office_health(_: bool = Depends(require_admin_or_local)):
    """Готов ли предпросмотр документов: найден ли LibreOffice и какой версии.

    Сюда стоит смотреть, если документы в чате не открываются вкладкой, а
    сразу скачиваются: видно, ищет ли сервер преобразователь там, где он
    установлен, и доступна ли ему папка профиля.
    """
    from ..storage import office_status
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, office_status)


@router.post("/health/db/check")
async def db_health_check_now(is_admin: bool = Depends(is_admin_user)):
    """Внеплановая полная проверка, включая контроль записи."""
    if not is_admin:
        raise HTTPException(status_code=403, detail="Доступно только администратору")
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, db_watchdog.check_once, True)


@router.get("/health/db-compression")
def db_compression_health(_: bool = Depends(require_admin_or_local)):
    """Какие таблицы с историей сжаты на диске и сколько занимают."""
    from .. import table_compression
    return table_compression.STATUS


@router.post("/admin/db-compression/run")
async def admin_run_db_compression(is_admin: bool = Depends(is_admin_user)):
    """Проверить и сжать таблицы сейчас, не дожидаясь ночи."""
    if not is_admin:
        raise HTTPException(status_code=403, detail="Только для администратора")
    import asyncio
    from .. import table_compression
    from server.database.init_database import engine_main
    from server.database.init_database_messenger import engine_chat
    engines = [("main", engine_main, table_compression.MAIN_TABLES), ("chat", engine_chat, table_compression.CHAT_TABLES)]
    return await asyncio.get_running_loop().run_in_executor(None, table_compression.run_pass, engines, True)


@router.get("/health/backup")
def backup_health(_: bool = Depends(require_admin_or_local)):
    """Статус резервного копирования и непрерывной архивации журналов."""
    return backup_manager.status()


@router.post("/admin/backup/run")
async def admin_run_backup(is_admin: bool = Depends(is_admin_user)):
    if not is_admin:
        raise HTTPException(status_code=403, detail="Доступно только администратору")
    if not backup_manager.trigger_async("manual"):
        return {"status": "already_running"}
    return {"status": "started", "note": "Прогресс — GET /health/backup"}


@router.post("/admin/backup/verify")
async def admin_verify_archive(deep: bool = Query(False), is_admin: bool = Depends(is_admin_user)):
    """Проверка целостности архива журналов; deep=true сканирует все файлы."""
    if not is_admin:
        raise HTTPException(status_code=403, detail="Доступно только администратору")
    loop = asyncio.get_running_loop()
    result = {}
    for key in backup_manager.targets:
        result[key] = await loop.run_in_executor(None, backup_manager.verify_binlog_archive, key, deep)
    return result


@router.get("/admin/backup/restore-hint")
def admin_restore_hint(is_admin: bool = Depends(is_admin_user)):
    """Готовая последовательность восстановления: дамп + накат журналов."""
    if not is_admin:
        raise HTTPException(status_code=403, detail="Доступно только администратору")
    return backup_manager.restore_hint()


@router.get("/admin/incidents")
def admin_incidents(is_admin: bool = Depends(is_admin_user)):
    if not is_admin:
        raise HTTPException(status_code=403, detail="Доступно только администратору")
    return {"open": devops_notifier.open_incidents(),
            "channel": "настроен" if devops_notifier.has_transport else "не настроен",
            "outbox": len(list(devops_notifier.outbox_dir.glob("*.json")))}
