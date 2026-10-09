# -*- coding: utf-8 -*-

from server.database.init_database import SessionLocal
from server.database.init_database_messenger import SessionLocalChat, BaseChat, engine_chat

from ..backup.devops import BACKUP_INCIDENTS_DIR, DevOpsNotifier
from ..backup.manager import BackupManager
from ..backup.watchdog import DatabaseWatchdog
from ..config import pool_status, resolve_engine


devops_notifier = DevOpsNotifier(BACKUP_INCIDENTS_DIR, service="kovcheg-server")


backup_manager = BackupManager(
    engines={"main": resolve_engine(SessionLocal), "chat": resolve_engine(engine_chat)},
    notifier=devops_notifier)


db_watchdog = DatabaseWatchdog(
    engines={"main": resolve_engine(SessionLocal), "chat": resolve_engine(engine_chat)},
    pool_status_provider=lambda: {"main": pool_status(SessionLocal), "chat": pool_status(engine_chat)},
    notifier=devops_notifier)
