# -*- coding: utf-8 -*-
"""Логи с ограничением по размеру.

Файл растёт до LOG_MAX_MB, затем переименовывается в app.log.1 и начинается
заново; хранится LOG_BACKUPS штук. Больше LOG_MAX_MB * (LOG_BACKUPS + 1)
логи занять не могут — диск не заполнят.

print() из старого кода тоже попадает в этот файл. В systemd-журнал уходят
только предупреждения и ошибки: там место общее на всю систему.
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from .envtools import env_int


_configured = False


class _StreamToLogger:
    """Подменяет stdout/stderr, чтобы print() писался в тот же файл."""

    def __init__(self, logger, level):
        self._logger = logger
        self._level = level
        self._buffer = ""

    def write(self, message):
        self._buffer += str(message)
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                self._logger.log(self._level, line.rstrip())

    def flush(self):
        if self._buffer.strip():
            self._logger.log(self._level, self._buffer.strip())
        self._buffer = ""

    def isatty(self):
        return False


def setup_logging(app_root: Path = None):
    global _configured
    if _configured:
        return

    level = getattr(logging, (os.getenv("LOG_LEVEL") or "INFO").upper(), logging.INFO)
    max_mb = env_int("LOG_MAX_MB", 10)
    backups = env_int("LOG_BACKUPS", 5)

    log_dir = os.getenv("LOG_DIR")
    if not log_dir:
        base = app_root or Path.cwd()
        log_dir = str(Path(base) / "logs")
    os.makedirs(log_dir, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        os.path.join(log_dir, "app.log"),
        maxBytes=max_mb * 1024 * 1024,
        backupCount=backups,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(fmt)

    # Реальный stderr сохраняем до подмены, иначе получится петля
    console = logging.StreamHandler(sys.__stderr__)
    # На экран по умолчанию только предупреждения и ошибки: подробности идут
    # в файл. CONSOLE_LOG_LEVEL=INFO в .env выводит на экран и обычные строки,
    # включая «Uvicorn running on ...» — удобно при локальном запуске.
    console_level = getattr(logging, (os.getenv("CONSOLE_LOG_LEVEL") or "WARNING").upper(),
                            logging.WARNING)
    console.setLevel(console_level)
    console.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(file_handler)
    root.addHandler(console)

    # Служебный шум SQLAlchemy и планировщика в файл не тащим
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

    sys.stdout = _StreamToLogger(logging.getLogger("stdout"), logging.INFO)
    sys.stderr = _StreamToLogger(logging.getLogger("stderr"), logging.ERROR)

    _configured = True
    logging.getLogger(__name__).info(
        f"Логи: {log_dir}/app.log, до {max_mb} МБ x {backups + 1} файлов"
    )
