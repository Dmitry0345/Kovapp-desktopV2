# -*- coding: utf-8 -*-

import asyncio
import os
import time
from server.database.init_database import SessionLocal
from server.database.init_database_messenger import SessionLocalChat, BaseChat, engine_chat
from sqlalchemy import desc, text, func, true, false
import threading

from .backup.devops import WATCHDOG_ENABLED, backup_logger, watchdog_logger
from .backup.services import backup_manager, db_watchdog
from .config import ensure_dialect_ready, ensure_pool_capacity, logger
from .core import app, scheduler
from .employee_cleanup import ensure_nullable_author_columns
from .helpers import cleanup_old_notifications
from .migrations import run_all as run_migrations
from .push import push_available
from .realtime import manager


def _quiet_windows_disconnects() -> None:
    """Обрыв соединения браузером на Windows asyncio пишет как ошибку
    (WinError 10054). Это не сбой сервера: такие записи — в отладочный уровень."""
    if os.name != "nt":
        return
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()

    def handler(lp, context):
        if isinstance(context.get("exception"), ConnectionResetError):
            logger.debug("Клиент оборвал соединение: %s", context.get("message"))
            return
        if previous is not None:
            previous(lp, context)
        else:
            lp.default_exception_handler(context)
    loop.set_exception_handler(handler)


def _check_file_storage() -> None:
    """Пишет в журнал, где сервер ищет файлы, и ищет пропавшие старые файлы."""
    from . import config as cfg
    try:
        from .storage import resolve_stored_path
        from server.database.models import TaskFile
        db = SessionLocal()
        try:
            paths = [r[0] for r in db.query(TaskFile.filepath).order_by(TaskFile.id.desc()).limit(200).all() if r[0]]
        finally:
            db.close()
    except Exception:
        logger.warning("Хранилище файлов: %s (проверка не выполнена)", cfg.FILE_STORAGE_ROOT, exc_info=True)
        return
    missing = []
    for p in paths:
        try:
            if not resolve_stored_path(p).is_file():
                missing.append(p)
        except Exception:
            missing.append(p)
    logger.info("Хранилище файлов: %s — из последних %d файлов задач на месте %d",
                cfg.FILE_STORAGE_ROOT, len(paths), len(paths) - len(missing))
    if missing:
        logger.warning("Нет на диске %d файлов задач, например: %s", len(missing), "; ".join(map(str, missing[:3])))
        threading.Thread(target=_discover_old_files, args=(missing[:50],), name="storage-discovery", daemon=True).start()


def _blob_sweeper() -> None:
    """Раз в сутки убирает записи file_blobs о файлах, которых нет на диске."""
    from . import blobs
    time.sleep(120)
    while True:
        blobs.sweep_missing()
        time.sleep(24 * 3600)


def _session_sweeper() -> None:
    """Раз в 10 минут удаляет куски брошенных загрузок."""
    from . import intake
    while True:
        time.sleep(600)
        try:
            removed = intake.chunk_store().cleanup()
            if removed:
                logger.info("Удалены куски брошенных загрузок: %d", removed)
        except Exception:
            logger.warning("Очистка брошенных загрузок не удалась", exc_info=True)


def _discover_old_files(missing) -> None:
    """Ищет, куда делись старые файлы, и подключает найденную папку для чтения."""
    from . import config as cfg
    from .storage import find_storage_roots
    try:
        roots = find_storage_roots(missing)
    except Exception:
        logger.warning("Поиск пропавших файлов не удался", exc_info=True)
        return
    if not roots:
        logger.error("Пропавшие файлы не найдены ни в папке file_storage в корне диска, ни рядом с сервером, "
                     "ни в папке проекта. Скорее всего, база перенесена с другого компьютера без папки файлов "
                     "или папка была удалена при обновлении (проверьте корзину). Скопируйте папку file_storage "
                     "оттуда, где работал прежний сервер, и укажите её в .env: FILE_STORAGE_FALLBACKS=<путь>")
        return
    for root, _ in roots:
        if root not in cfg.FILE_STORAGE_FALLBACKS:
            cfg.FILE_STORAGE_FALLBACKS.append(root)
    best, hits = roots[0]
    logger.warning("Старые файлы найдены в %s (совпало %d из %d) и подключены для чтения до перезапуска. "
                   "Чтобы закрепить, добавьте в .env строку FILE_STORAGE_FALLBACKS=%s", best, hits, len(missing), best)


@app.on_event("startup")
async def startup_event():
    from . import lanes
    # пул соединений с базой: по мощности машины, не меньше 100, в пределах, которые разрешает MariaDB
    lanes.check_database(SessionLocal)
    lanes.configure_runtime()
    ensure_pool_capacity(SessionLocal, pool_size=lanes.PLAN["db_pool"], max_overflow=lanes.PLAN["db_overflow"],
                         label="основная БД")
    ensure_pool_capacity(engine_chat, pool_size=lanes.PLAN["db_pool"], max_overflow=lanes.PLAN["db_overflow"],
                         label="БД чата")
    for target, label in ((SessionLocal, "основная БД"), (engine_chat, "БД чата")):
        try:
            ensure_dialect_ready(target, label=label)
        except Exception:
            logger.exception("Пул %s: не удалось получить параметры базы", label)
    _quiet_windows_disconnects()

    from . import versions
    from server.database.init_database import engine_main
    versions.watch(engine_main, "main")
    versions.watch(engine_chat, "chat")

    from .realtime import ROLE, serve_bus
    if ROLE in ("reader", "uploader"):
        # процессы чтения и приёма загрузок: только отвечают на запросы; фоновые задачи ведёт API
        logger.info("Процесс %s запущен (KOVAPP_ROLE=%s)", "чтения" if ROLE == "reader" else "приёма загрузок", ROLE)
        return
    if ROLE == "realtime":
        # процесс переписки: только WebSocket и рассылка событий; фоновые задачи ведёт процесс API
        asyncio.create_task(manager.heartbeat())
        asyncio.create_task(manager.periodic_cleanup())
        asyncio.create_task(_watch_event_loop())
        asyncio.create_task(serve_bus())
        logger.info("Процесс переписки запущен (KOVAPP_ROLE=realtime)")
        return

    # Схема версии 2 (проекты, владельцы файлов, push, настройки). Идемпотентно.
    try:
        run_migrations(SessionLocal, SessionLocalChat)
    except Exception as e:
        logger.exception("Миграция схемы не выполнена: %r", e)
    await asyncio.to_thread(_check_file_storage)
    threading.Thread(target=_blob_sweeper, name="blob-sweep", daemon=True).start()
    threading.Thread(target=_session_sweeper, name="upload-sweep", daemon=True).start()
    logger.info("Web Push: %s", "включён" if push_available() else "выключен (нет VAPID-ключей или pywebpush)")
    from .config import VAPID_SUBJECT
    if push_available() and ("example.com" in VAPID_SUBJECT or "localhost" in VAPID_SUBJECT):
        # Apple (iPhone, Mac) отклоняет push с адресом-заглушкой в подписи VAPID
        logger.warning("Web Push: VAPID_SUBJECT=%s — укажите в .env настоящий адрес (mailto:почта или https://сайт), "
                       "иначе на iPhone и Mac уведомления могут не доходить", VAPID_SUBJECT)
    try:
        from .native_push import status as native_status
        st = native_status()
        logger.info("Уведомления приложения Kovapp: %s, через Google (FCM): %s",
                    "включены" if st["enabled"] else "выключены",
                    "да" if st["fcm"] else ("нет — " + (st["fcm_error"] or "не настроено")))
    except Exception as e:
        logger.warning("Уведомления приложения Kovapp: %s", e)

    scheduler.add_job(cleanup_old_notifications, 'cron', hour=3)
    from .config import VIDEO_NIGHT_ENABLED, VIDEO_NIGHT_HOUR
    if VIDEO_NIGHT_ENABLED:
        from . import night_video   # ночью — пережатие видео за сутки (днём они принимаются как есть)
        scheduler.add_job(night_video.run_once, 'cron', hour=VIDEO_NIGHT_HOUR, id='night-video',
                          max_instances=1, coalesce=True, replace_existing=True)
    scheduler.start()

    # Инициализация бэкапа вынесена в поток: проверка архива журналов после
    # некорректной остановки может занять время, старт сервера её не ждёт.
    # сжатие таблиц с историей на диске (MariaDB PAGE_COMPRESSED), только в процессе API
    from . import table_compression
    table_compression.start([("main", engine_main, table_compression.MAIN_TABLES),
                             ("chat", engine_chat, table_compression.CHAT_TABLES)])
    threading.Thread(target=backup_manager.start, args=(scheduler,),
                     daemon=True, name="backup-init").start()
    if WATCHDOG_ENABLED:
        db_watchdog.start()

    asyncio.create_task(manager.heartbeat())
    asyncio.create_task(manager.periodic_cleanup())
    asyncio.create_task(_watch_event_loop())
    # склад рассылает изменения из обычных обработчиков — ему нужен этот цикл
    try:
        from .routers.warehouse import remember_loop
        remember_loop(asyncio.get_running_loop())
    except Exception as e:
        logger.debug("Склад: цикл событий не запомнен: %s", e)

    # Ссылки на автора должны допускать NULL, иначе удаление сотрудника
    # упирается в NOT NULL и разметку текста этапов пришлось бы стирать
    db_main = SessionLocal()
    try:
        ensure_nullable_author_columns(db_main)
    finally:
        db_main.close()
    
    # Идемпотентный SQL для добавления колонки last_read_message_id
    db_chat = SessionLocalChat()
    try:
                
        # MySQL/MariaDB specific check for column existence since ADD COLUMN IF NOT EXISTS is not standard until MariaDB 10.6+
        check_col = db_chat.execute(text("""
            SELECT count(*) 
            FROM information_schema.COLUMNS 
            WHERE TABLE_SCHEMA = DATABASE() 
            AND TABLE_NAME = 'chat_participants' 
            AND COLUMN_NAME = 'last_read_message_id'
        """)).scalar()
        
        if not check_col:
            db_chat.execute(text("ALTER TABLE chat_participants ADD COLUMN last_read_message_id INTEGER DEFAULT 0"))

        db_chat.commit()
    except Exception as e:
        logger.error(f"Не удалось добавить колонку last_read_message_id: {e}")
        db_chat.rollback()
    finally:
        db_chat.close()


@app.on_event("shutdown")
async def shutdown_backup_services():
    """Штатная остановка: помечает состояние как чистое, иначе следующий старт
    запустит глубокую проверку журналов как после сбоя питания."""
    try:
        db_watchdog.stop()
    except Exception as e:
        watchdog_logger.error("Ошибка остановки вотчдога: %s", e)
    try:
        backup_manager.stop()
    except Exception as e:
        backup_logger.error("Ошибка остановки подсистемы бэкапа: %s", e)


async def _watch_event_loop():
    """Задержка цикла событий — самый честный признак, что приложению плохо.

    Спим ровно секунду и смотрим, насколько опоздали. Если опоздание растёт,
    значит запросы уже ждут своей очереди, и распорядитель нагрузки урежет
    обработку файлов, не дожидаясь, пока встанет весь сервер.
    """
    from .load_governor import governor
    smooth = 0.0
    while True:
        started = time.monotonic()
        await asyncio.sleep(1.0)
        lag = max(0.0, time.monotonic() - started - 1.0)
        smooth = lag if lag > smooth else smooth * 0.7 + lag * 0.3   # вверх сразу, вниз плавно
        try:
            governor().note_lag(smooth)
        except Exception:
            return                       # распорядитель недоступен — наблюдение не нужно
