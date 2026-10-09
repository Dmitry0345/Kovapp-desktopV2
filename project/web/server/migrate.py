# -*- coding: utf-8 -*-
"""Ручной запуск миграции схемы: python -m server.migrate (из каталога с .env)."""
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

APP_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(APP_ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

from server.database.init_database import SessionLocal  # noqa: E402
from server.database.init_database_messenger import SessionLocalChat  # noqa: E402
from server.server_app.migrations import run_all  # noqa: E402

if __name__ == "__main__":
    run_all(SessionLocal, SessionLocalChat)
    print("Миграция завершена")
    sys.exit(0)
