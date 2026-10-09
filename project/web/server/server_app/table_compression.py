# -*- coding: utf-8 -*-
"""Сжатие таблиц с историей средствами MariaDB (PAGE_COMPRESSED).

База сама сжимает страницы таблицы при записи на диск и распаковывает при
чтении; запросы и код приложения не меняются. Сжимаются таблицы, которые
копят историю (сообщения, уведомления, журналы, записи о файлах): основной их
объём — старые записи. Маленькие таблицы переводятся сразу после запуска,
крупные — только в ночные часы (перестройка таблицы нагружает диск).
"""
import logging
import os
import threading
import time
from datetime import datetime

from sqlalchemy import text

logger = logging.getLogger(__name__)

MAIN_TABLES = ("notifications", "task_change_log", "warehouse_cell_log", "equipment_log", "warehouse_issues",
               "equipment_issues", "task_stage_text_spans", "task_files", "task_stage_files", "project_files")
CHAT_TABLES = ("messages", "message_reactions")


def _env_int(name, default):
    try:
        return int(os.getenv(name) or default)
    except ValueError:
        return default


ENABLED = (os.getenv("DB_PAGE_COMPRESSION") or "1").strip() != "0"
LEVEL = max(1, min(9, _env_int("DB_COMPRESS_LEVEL", 6)))
ONLINE_MB = _env_int("DB_COMPRESS_ONLINE_MB", 64)
DELAY_S = _env_int("DB_COMPRESS_DELAY_S", 120)
NIGHT = (os.getenv("DB_COMPRESS_HOURS") or "1-3").strip()   # с 01:00 до 03:00; потом ночное сжатие видео

STATUS = {"enabled": ENABLED, "level": LEVEL, "tables": {}, "last_pass": None, "errors": []}
_lock = threading.Lock()


def _night(now=None) -> bool:
    hour = (now or datetime.now()).hour
    try:
        start, end = (int(x) for x in NIGHT.split("-", 1))
    except ValueError:
        start, end = 1, 3
    # конец не входит: «1-3» — с 01:00 до 03:00 (так же считается окно ночного сжатия видео)
    return start <= hour < end if start < end else (hour >= start or hour < end)


def busy() -> bool:
    """Идёт проход сжатия (перестройка таблицы может длиться минуты)."""
    return _lock.locked()


def is_compressed(options: str) -> bool:
    low = (options or "").lower().replace(" ", "")
    return "page_compressed" in low and "page_compressed='off'" not in low and "page_compressed=0" not in low


def _tables(conn, names):
    rows = conn.execute(text(
        "SELECT TABLE_NAME, ENGINE, CREATE_OPTIONS, COALESCE(DATA_LENGTH, 0) + COALESCE(INDEX_LENGTH, 0) "
        "FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE'")).fetchall()
    return [r for r in rows if r[0] in names]


def _disk_bytes(conn, table):
    """Сколько таблица реально занимает на диске (сжатые страницы — меньше логического размера)."""
    try:
        row = conn.execute(text(
            "SELECT FILE_SIZE, ALLOCATED_SIZE FROM information_schema.INNODB_SYS_TABLESPACES "
            "WHERE NAME = CONCAT(DATABASE(), '/', :t)"), {"t": table}).fetchone()
        return (int(row[0]), int(row[1])) if row else (None, None)
    except Exception:
        return None, None


def _alter(conn, table, night):
    base = "ALTER TABLE `%s` PAGE_COMPRESSED=1, PAGE_COMPRESSION_LEVEL=%d" % (table, LEVEL)
    try:
        conn.execute(text(base + ", ALGORITHM=INPLACE, LOCK=NONE"))
        return "online"
    except Exception as e:
        if not night:
            raise RuntimeError("перестройка без блокировки недоступна, перенесено на ночь: %s" % e)
        conn.execute(text(base))
        return "copy"


def run_pass(engines, force=False) -> dict:
    """Один проход: что ещё не сжато — сжать (крупное — только ночью или по force)."""
    if not ENABLED:
        return STATUS
    night = force or _night()
    with _lock:
        for label, engine, names in engines:
            if engine is None or engine.dialect.name not in ("mysql", "mariadb"):
                continue
            try:
                with engine.connect() as conn:
                    for name, eng, options, size in _tables(conn, names):
                        key = "%s.%s" % (label, name)
                        entry = STATUS["tables"].setdefault(key, {})
                        entry.update({"logical_mb": round(size / 1048576, 1), "engine": eng})
                        if (eng or "").lower() != "innodb":
                            entry["state"] = "не InnoDB — пропущена"
                            continue
                        if is_compressed(options):
                            entry["state"] = "сжата"
                        elif size > ONLINE_MB * 1048576 and not night:
                            entry["state"] = "ждёт ночи (%s ч)" % NIGHT
                        else:
                            before = _disk_bytes(conn, name)[1]
                            started = time.time()
                            try:
                                entry["mode"] = _alter(conn, name, night)
                                conn.commit()
                                entry["state"] = "сжата"
                                entry["seconds"] = round(time.time() - started, 1)
                                entry["disk_before_mb"] = round(before / 1048576, 1) if before else None
                                logger.info("Таблица %s сжата (%s, %.1f с)", key, entry["mode"], entry["seconds"])
                            except Exception as e:
                                entry["state"] = "ошибка"
                                entry["error"] = str(e)[:300]
                                STATUS["errors"] = (STATUS["errors"] + ["%s: %s" % (key, str(e)[:200])])[-10:]
                                logger.warning("Сжатие таблицы %s не выполнено: %s", key, e)
                        file_size, allocated = _disk_bytes(conn, name)
                        if allocated is not None:
                            entry["disk_mb"] = round(allocated / 1048576, 1)
                            entry["file_mb"] = round(file_size / 1048576, 1)
                    try:
                        STATUS["algorithm"] = conn.execute(text("SELECT @@innodb_compression_algorithm")).scalar()
                    except Exception:
                        pass
            except Exception as e:
                STATUS["errors"] = (STATUS["errors"] + ["%s: %s" % (label, str(e)[:200])])[-10:]
                logger.warning("Проверка сжатия таблиц базы %s не удалась: %s", label, e)
        STATUS["last_pass"] = datetime.now().isoformat(timespec="seconds")
    return STATUS


def start(engines):
    """Фоновый поток: проход после запуска, затем раз в 30 минут, пока не сжато всё."""
    if not ENABLED:
        logger.info("Сжатие таблиц отключено (DB_PAGE_COMPRESSION=0)")
        return

    def loop():
        time.sleep(DELAY_S)
        while True:
            run_pass(engines)
            pending = [k for k, v in STATUS["tables"].items() if v.get("state") not in ("сжата", "не InnoDB — пропущена")]
            time.sleep(1800 if pending else 6 * 3600)

    threading.Thread(target=loop, name="table-compression", daemon=True).start()
