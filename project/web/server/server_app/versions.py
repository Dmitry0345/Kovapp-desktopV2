# -*- coding: utf-8 -*-
"""Метки версий данных, общие для всех процессов сервера: любая запись в базу меняет метку.

По метке сервер отвечает «не изменилось» (304) на повторный запрос списка, не собирая его заново.
"""
import os
import re
import tempfile
import time

_DIR = os.getenv("KOVAPP_STATE_DIR") or ("/dev/shm" if os.path.isdir("/dev/shm") else tempfile.gettempdir())
_PREFIX = "kovapp-ver-%s-" % (os.getenv("SERVER_PORT") or "0")
_WRITE = re.compile(r"^\s*(insert|update|delete|replace|alter|create|drop|truncate)\b", re.I)
_SKIP = ("file_blobs", "push_subscriptions", "upload_sessions", "login_attempts")


def _path(name: str) -> str:
    return os.path.join(_DIR, _PREFIX + name)


def bump(name: str) -> None:
    try:
        tmp = "%s.%d" % (_path(name), os.getpid())
        with open(tmp, "w", encoding="ascii") as fh:
            fh.write(str(time.time_ns()))
        os.replace(tmp, _path(name))
    except OSError:
        pass


def current(name: str) -> str:
    try:
        with open(_path(name), "r", encoding="ascii") as fh:
            return fh.read().strip() or "0"
    except OSError:
        return "0"


def watch(engine, name: str) -> None:
    """Следить за записями через этот движок базы: после фиксации изменений метка меняется."""
    from sqlalchemy import event

    @event.listens_for(engine, "before_cursor_execute")
    def _mark(conn, cursor, statement, parameters, context, executemany):
        if statement and _WRITE.match(statement) and not any(t in statement for t in _SKIP):
            conn.info["kov_dirty"] = True

    @event.listens_for(engine, "commit")
    def _commit(conn):
        if conn.info.pop("kov_dirty", False):
            bump(name)

    @event.listens_for(engine, "rollback")
    def _rollback(conn):
        conn.info.pop("kov_dirty", None)
    if current(name) == "0":
        bump(name)
