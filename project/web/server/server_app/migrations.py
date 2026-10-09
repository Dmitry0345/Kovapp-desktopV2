# -*- coding: utf-8 -*-
"""Миграция схемы до версии 2 (проекты, владельцы файлов, push, настройки).

Выполняется при старте сервера и отдельно командой
    python -m server.migrate
Все шаги идемпотентны: повторный запуск ничего не ломает и ничего не дублирует.
Данные не удаляются и не переписываются — только добавляются колонки, таблицы
и индексы. Исключение — разовые шаги DATA_STEPS: каждый выполняется один раз,
отметка о нём хранится в таблице server_migrations.
"""

import logging
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from server.database.models import (Base, FileBlob, Project, ProjectFile, PushSubscription,
                                    NativePushDevice, UserPreference,
                                    WarehouseUnit, WarehouseItem, WarehouseCellLog,
                                    WarehouseIssue, WarehouseEditor, TaskChangeLog)

logger = logging.getLogger(__name__)

# (таблица, колонка, определение для ALTER TABLE ... ADD COLUMN)
NEW_COLUMNS = (
    ("task_files", "uploaded_by_id", "INT NULL"),
    ("task_files", "created_at", "DATETIME NULL"),
    ("task_stage_files", "uploaded_by_id", "INT NULL"),
    ("task_stage_files", "created_at", "DATETIME NULL"),
    ("tasks", "project_id", "INT NULL"),
    # полное название единицы: «Метр» рядом с кратким «м»
    ("warehouse_units", "full_name", "VARCHAR(64) NULL"),
    # маркер прав сотрудника вместо разбора названия должности
    ("employees", "status", "VARCHAR(32) NULL"),
    # образец исходника для быстрой проверки копии
    ("file_blobs", "source_sample", "VARCHAR(64) NULL"),
    # ночное пережатие видео: отметка «уже проверено»
    ("file_blobs", "night_checked_at", "DATETIME NULL"),
    # проба перед ночным сжатием: оценка выигрыша и сбои полного сжатия
    ("file_blobs", "night_estimate", "FLOAT NULL"),
    ("file_blobs", "night_attempts", "INTEGER NULL"),
    ("projects", "is_archived", "BOOLEAN NOT NULL DEFAULT 0"),
    ("projects", "archived_at", "DATETIME NULL"),
    ("projects", "archived_by_id", "INTEGER NULL"),
)

# Внешние ключи добавляются отдельно: колонка могла существовать без них
NEW_FOREIGN_KEYS = (
    ("task_files", "fk_task_files_uploader", "uploaded_by_id", "employees", "id", "SET NULL"),
    ("task_stage_files", "fk_task_stage_files_uploader", "uploaded_by_id", "employees", "id", "SET NULL"),
    ("tasks", "fk_tasks_project", "project_id", "projects", "id", "SET NULL"),
)

# Индексы, ускоряющие самые частые выборки (основная БД)
NEW_INDEXES_MAIN = (
    # Колонки с внешним ключом InnoDB индексирует сам — здесь только составные
    # и «строковые» индексы, которых ORM не создаёт
    ("tasks", "ix_tasks_activity", "activity(20)"),
    ("file_blobs", "ix_file_blobs_size_sample", "source_size, source_sample"),
    ("tasks", "ix_tasks_deadline", "deadline"),
    ("notifications", "ix_notifications_employee_read", "employee_id, is_read"),
    ("notifications", "ix_notifications_created", "created_at"),
    ("project_files", "ix_project_files_section", "project_id, section"),
    ("projects", "ix_projects_deadline", "deadline"),
    ("task_files", "ix_task_files_filepath", "filepath(191)"),
    ("task_stage_files", "ix_task_stage_files_filepath", "filepath(191)"),
    ("project_files", "ix_project_files_filepath", "filepath(191)"),
    ("equipment_items", "ix_equipment_items_photo_path", "photo_path(191)"),
)

# Индексы в базе мессенджера
NEW_INDEXES_CHAT = (
    ("messages", "ix_messages_chat_ts", "chat_id, timestamp"),
    ("messages", "ix_messages_chat_id2", "chat_id, id"),
    ("chat_participants", "ix_participants_user", "user_id"),
    ("chat_participants", "ix_participants_chat_user", "chat_id, user_id"),
    ("messages", "ix_messages_file_path", "file_path(191)"),
    ("chats", "ix_chats_image_path", "image_path(191)"),
)


def _is_mysql(db: Session) -> bool:
    try:
        return db.get_bind().dialect.name in ("mysql", "mariadb")
    except Exception:
        return False


def _column_exists(db: Session, table: str, column: str) -> bool:
    return bool(db.execute(text("""
        SELECT COUNT(*) FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c
    """), {"t": table, "c": column}).scalar())


def _table_exists(db: Session, table: str) -> bool:
    return bool(db.execute(text("""
        SELECT COUNT(*) FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t
    """), {"t": table}).scalar())


def _constraint_exists(db: Session, table: str, name: str) -> bool:
    return bool(db.execute(text("""
        SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND CONSTRAINT_NAME = :n
    """), {"t": table, "n": name}).scalar())


def _index_exists(db: Session, table: str, name: str) -> bool:
    return bool(db.execute(text("""
        SELECT COUNT(*) FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND INDEX_NAME = :n
    """), {"t": table, "n": name}).scalar())


def _step(db: Session, sql: str, what: str) -> None:
    try:
        db.execute(text(sql))
        db.commit()
        logger.info("Миграция: %s", what)
    except Exception as e:
        db.rollback()
        logger.error("Миграция: не удалось %s: %s", what, e)


def migrate_main(db: Session) -> None:
    """Основная БД: новые таблицы, колонки, внешние ключи, индексы."""
    engine = db.get_bind()

    # Новые таблицы — по одной: сбой одной (например, из-за внешнего ключа)
    # раньше обрывал всю миграцию. file_blobs нужна приёму файлов — первой.
    wanted = {Project.__table__, ProjectFile.__table__, PushSubscription.__table__, NativePushDevice.__table__,
              UserPreference.__table__, WarehouseUnit.__table__, WarehouseItem.__table__,
              WarehouseCellLog.__table__, WarehouseIssue.__table__, WarehouseEditor.__table__,
              TaskChangeLog.__table__}
    try:
        from .config import ensure_dialect_ready
        ensure_dialect_ready(engine, label="миграции")
    except Exception:
        logger.exception("Миграция: не удалось получить параметры базы")
    for table in [FileBlob.__table__] + [t for t in Base.metadata.sorted_tables if t in wanted]:
        try:
            table.create(bind=engine, checkfirst=True)
        except Exception:
            logger.exception("Миграция: таблица %s не создана", table.name)

    if not _is_mysql(db):
        # SQLite и прочее: колонок через ALTER не добавляем, create_all уже
        # создал новые таблицы; старые таблицы должны быть пересозданы моделями
        logger.info("Миграция: диалект %s, пропуск ALTER TABLE", engine.dialect.name)
        return

    for table, column, definition in NEW_COLUMNS:
        if not _table_exists(db, table) or _column_exists(db, table, column):
            continue
        _step(db, f"ALTER TABLE `{table}` ADD COLUMN `{column}` {definition}",
              f"колонка {table}.{column}")

    for table, name, column, ref_table, ref_column, on_delete in NEW_FOREIGN_KEYS:
        if not _table_exists(db, table) or _constraint_exists(db, table, name):
            continue
        _step(db,
              f"ALTER TABLE `{table}` ADD CONSTRAINT `{name}` FOREIGN KEY (`{column}`) "
              f"REFERENCES `{ref_table}`(`{ref_column}`) ON DELETE {on_delete}",
              f"внешний ключ {name}")

    for table, name, columns in NEW_INDEXES_MAIN:
        if not _table_exists(db, table) or _index_exists(db, table, name):
            continue
        _step(db, f"CREATE INDEX `{name}` ON `{table}` ({columns})", f"индекс {name}")


def migrate_chat(db: Session) -> None:
    """БД мессенджера: только индексы (схема сообщений не меняется)."""
    if not _is_mysql(db):
        return
    for table, name, columns in NEW_INDEXES_CHAT:
        if not _table_exists(db, table) or _index_exists(db, table, name):
            continue
        _step(db, f"CREATE INDEX `{name}` ON `{table}` ({columns})", f"индекс {name}")


# Разовые шаги с данными: (имя, таблица, SQL). Имя записывается в server_migrations —
# повторный запуск шаг пропускает, и выбор, сделанный пользователем позже, не затирается
DATA_STEPS = (
    # порядок задач по умолчанию — «по важности» (06.10.2026): он действует, когда сортировка
    # не выбрана. Прежнее значение по умолчанию «по сроку» стояло у всех, кто не менял фильтр
    ("task_filters_priority_default", "task_filters",
     "UPDATE task_filters SET sort_by = NULL WHERE is_archived = 0 "
     "AND (sort_by = '' OR sort_by = 'deadline_asc' OR sort_by = 'priority')"),
)


def migrate_data(db: Session) -> None:
    """Разовые шаги с данными (любая база: MariaDB и SQLite)."""
    try:
        db.execute(text("CREATE TABLE IF NOT EXISTS server_migrations "
                        "(name VARCHAR(64) NOT NULL PRIMARY KEY, applied_at DATETIME NULL)"))
        db.commit()
        tables = set(inspect(db.get_bind()).get_table_names())
    except Exception as e:
        db.rollback()
        logger.error("Миграция: таблица server_migrations недоступна: %s", e)
        return
    for name, table, sql in DATA_STEPS:
        try:
            if table not in tables or db.execute(text("SELECT COUNT(*) FROM server_migrations WHERE name = :n"),
                                                 {"n": name}).scalar():
                continue
            changed = db.execute(text(sql)).rowcount
            db.execute(text("INSERT INTO server_migrations (name, applied_at) VALUES (:n, CURRENT_TIMESTAMP)"), {"n": name})
            db.commit()
            logger.info("Миграция: разовый шаг %s, строк изменено: %s", name, changed)
        except Exception as e:
            db.rollback()
            logger.error("Миграция: разовый шаг %s не выполнен: %s", name, e)


def run_all(main_session_factory, chat_session_factory) -> None:
    db = main_session_factory()
    try:
        migrate_main(db)
        migrate_data(db)
    finally:
        db.close()
    db_chat = chat_session_factory()
    try:
        migrate_chat(db_chat)
    finally:
        db_chat.close()
