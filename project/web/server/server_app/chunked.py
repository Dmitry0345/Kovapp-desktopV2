# -*- coding: utf-8 -*-
import hashlib
import json
import os
import re
import secrets
import shutil
import threading
import time
import zlib
from pathlib import Path
from typing import Optional

# ── Порционная загрузка с докачкой ──
CHUNK_MAX = 8 * 1024 * 1024
LEAF = 4 * 1024 * 1024
_SID = re.compile(r"^[0-9a-f]{32}$")


class _SessionLock:
    def __init__(self, inner, path):
        self.inner = inner
        self.path = path
        self.fh = None

    def __enter__(self):
        self.inner.acquire()
        try:
            import fcntl
            if self.path.parent.is_dir():
                self.fh = open(self.path, "a+")
                fcntl.flock(self.fh, fcntl.LOCK_EX)
        except (ImportError, OSError):
            self.fh = None
        return self

    def __exit__(self, *exc):
        try:
            if self.fh is not None:
                import fcntl
                fcntl.flock(self.fh, fcntl.LOCK_UN)
                self.fh.close()
        except (ImportError, OSError):
            pass
        finally:
            self.fh = None
            self.inner.release()
        return False


class ChunkError(Exception):
    def __init__(self, status: int, detail: str, received: Optional[int] = None):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.received = received


def tree_hash_file(path, leaf: int = LEAF) -> dict:
    whole = hashlib.sha256()
    leaves = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        while True:
            block = fh.read(leaf)
            if not block:
                break
            size += len(block)
            whole.update(block)
            leaves.update(hashlib.sha256(block).digest())
    leaves.update(size.to_bytes(8, "big"))
    return {"tree": leaves.hexdigest(), "sha256": whole.hexdigest(), "size": size}


class ChunkStore:
    def __init__(self, root, max_total: int, chunk_max: int = CHUNK_MAX,
                 ttl: float = 24 * 3600, max_active: int = 40):
        self.root = Path(root)
        self.max_total = int(max_total)
        self.chunk_max = int(chunk_max)
        self.ttl = float(ttl)
        self.max_active = int(max_active)
        self._guard = threading.Lock()
        self._locks = {}
        self.root.mkdir(parents=True, exist_ok=True)

    def _lock(self, sid: str) -> "_SessionLock":
        """Замок сеанса и в этом процессе, и между процессами приёма загрузок (через файл-замок)."""
        with self._guard:
            inner = self._locks.setdefault(sid, threading.Lock())
        return _SessionLock(inner, self._dir(sid) / ".lock")

    def _dir(self, sid: str) -> Path:
        if not _SID.match(str(sid or "")):
            raise ChunkError(404, "Сеанс загрузки не найден")
        return self.root / sid

    def _read(self, sid: str) -> dict:
        path = self._dir(sid) / "meta.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ChunkError(404, "Сеанс загрузки не найден или устарел")

    def _write(self, sid: str, meta: dict) -> None:
        folder = self._dir(sid)
        tmp = folder / "meta.json.tmp"
        tmp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, folder / "meta.json")

    def _own(self, meta: dict, user_id: int) -> None:
        if int(meta.get("user_id") or 0) != int(user_id):
            raise ChunkError(404, "Сеанс загрузки не найден")

    def _public(self, meta: dict) -> dict:
        return {"id": meta["id"], "received": meta["received"], "size": meta.get("size"),
                "done": meta.get("done", False), "chunk_max": self.chunk_max}

    def create(self, user_id: int, name: str, size: Optional[int] = None, kind: str = "",
               meta: Optional[dict] = None) -> dict:
        self.cleanup()
        if size is not None and (int(size) < 0 or int(size) > self.max_total):
            raise ChunkError(413, "Файл больше допустимого размера")
        active = 0
        for folder in self.root.iterdir():
            try:
                m = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
                if int(m.get("user_id") or 0) == int(user_id) and not m.get("taken"):
                    active += 1
            except Exception:
                continue
        if active >= self.max_active:
            raise ChunkError(429, "Слишком много незавершённых загрузок — дождитесь окончания")
        sid = secrets.token_hex(16)
        folder = self.root / sid
        folder.mkdir(parents=True)
        (folder / "data.part").touch()
        now = time.time()
        record = {"id": sid, "user_id": int(user_id), "name": Path(str(name or "file")).name[:255],
                  "size": int(size) if size is not None else None, "kind": str(kind or "")[:16],
                  "received": 0, "done": False, "taken": False, "created": now, "updated": now,
                  "client": meta or {}}
        self._write(sid, record)
        return self._public(record)

    def status(self, user_id: int, sid: str) -> dict:
        meta = self._read(sid)
        self._own(meta, user_id)
        return self._public(meta)

    def append(self, user_id: int, sid: str, offset: int, data: bytes, crc: Optional[int] = None) -> dict:
        if len(data) > self.chunk_max:
            raise ChunkError(413, "Слишком большая порция")
        if crc is not None and (zlib.crc32(data) & 0xFFFFFFFF) != (int(crc) & 0xFFFFFFFF):
            raise ChunkError(400, "Порция повреждена при передаче — повторите")
        with self._lock(sid):
            meta = self._read(sid)
            self._own(meta, user_id)
            if meta.get("done"):
                raise ChunkError(409, "Загрузка уже завершена", meta["received"])
            received = int(meta["received"])
            offset = int(offset)
            if offset < 0:
                raise ChunkError(400, "Неверное смещение порции", received)
            if offset > received:
                raise ChunkError(409, "Пропущена часть файла", received)
            skip = received - offset
            if skip >= len(data):
                return self._public(meta)
            tail = data[skip:]
            if meta.get("size") is not None and received + len(tail) > meta["size"]:
                raise ChunkError(400, "Данных больше, чем заявлено")
            if received + len(tail) > self.max_total:
                raise ChunkError(413, "Файл больше допустимого размера")
            with open(self._dir(sid) / "data.part", "r+b") as fh:
                fh.seek(received)
                fh.write(tail)
                fh.truncate(received + len(tail))
            meta["received"] = received + len(tail)
            meta["updated"] = time.time()
            self._write(sid, meta)
            return self._public(meta)

    def complete(self, user_id: int, sid: str, size: Optional[int] = None) -> dict:
        with self._lock(sid):
            meta = self._read(sid)
            self._own(meta, user_id)
            expected = meta.get("size") if meta.get("size") is not None else size
            if expected is None or int(expected) != int(meta["received"]):
                raise ChunkError(409, "Файл получен не полностью", meta["received"])
            if not meta.get("done"):
                digest = tree_hash_file(self._dir(sid) / "data.part")
                meta.update({"done": True, "size": digest["size"], "tree": digest["tree"],
                             "sha256": digest["sha256"], "updated": time.time()})
                self._write(sid, meta)
            return dict(self._public(meta), tree=meta["tree"], sha256=meta["sha256"])

    def take(self, user_id: int, sid: str):
        with self._lock(sid):
            meta = self._read(sid)
            self._own(meta, user_id)
            if not meta.get("done"):
                raise ChunkError(409, "Файл получен не полностью", meta["received"])
            if meta.get("taken"):
                raise ChunkError(409, "Файл уже использован")
            meta["taken"] = True
            self._write(sid, meta)
            return self._dir(sid) / "data.part", meta

    def release(self, sid: str) -> None:
        try:
            shutil.rmtree(self._dir(sid), ignore_errors=True)
        except ChunkError:
            pass
        with self._guard:
            self._locks.pop(sid, None)

    def abort(self, user_id: int, sid: str) -> None:
        meta = self._read(sid)
        self._own(meta, user_id)
        self.release(sid)

    def cleanup(self, now: Optional[float] = None) -> int:
        now = now or time.time()
        removed = 0
        for folder in list(self.root.iterdir()):
            try:
                meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
                stale = now - float(meta.get("updated") or 0) > self.ttl
            except Exception:
                stale = now - folder.stat().st_mtime > self.ttl
            if stale:
                shutil.rmtree(folder, ignore_errors=True)
                removed += 1
        return removed
