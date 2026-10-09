# -*- coding: utf-8 -*-
"""Небольшое общее хранилище для нескольких процессов сервера (файлы в /dev/shm или во временной папке).

Ход загрузки, разрешения на повтор файла — всё, что раньше жило в памяти одного
процесса, а теперь может понадобиться другому процессу приёма загрузок.
"""
import json
import os
import re
import time

from .versions import _DIR

_ROOT = os.path.join(_DIR, "kovapp-state-%s" % (os.getenv("SERVER_PORT") or "0"))
_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


def _path(kind: str, key: str) -> str:
    folder = os.path.join(_ROOT, kind)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, _SAFE.sub("_", str(key))[:120])


def put(kind: str, key: str, value, ttl: float) -> None:
    try:
        path = _path(kind, key)
        tmp = "%s.%d.tmp" % (path, os.getpid())
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"exp": time.time() + ttl, "value": value}, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        pass


def _load(path: str):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if data.get("exp", 0) < time.time():
        try:
            os.remove(path)
        except OSError:
            pass
        return None
    return data.get("value")


def get(kind: str, key: str):
    return _load(_path(kind, key))


def take(kind: str, key: str):
    """Забрать и удалить: из нескольких процессов значение получит ровно один."""
    path = _path(kind, key)
    grabbed = "%s.%d.taken" % (path, os.getpid())
    try:
        os.replace(path, grabbed)
    except OSError:
        return None
    try:
        return _load(grabbed)
    finally:
        try:
            os.remove(grabbed)
        except OSError:
            pass


def sweep(kind: str) -> None:
    folder = os.path.join(_ROOT, kind)
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        if not name.endswith((".tmp", ".taken")):
            _load(os.path.join(folder, name))
