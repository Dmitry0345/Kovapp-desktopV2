"""Повтор файла в чат по отпечатку: intake.as_uploads отдаёт сам файл, а не gzip-обёртку хранилища.

Раньше чат получал обёртку (имя.расширение.gz) как исходник, сжимал её второй раз, и при скачивании
вместо файла приходил gzip. Запуск из папки веб-версии: python -m server.tests.dedup_repeat_gzip_tests
"""
import asyncio
import gzip
import logging
import os
import sys
import tempfile
import types
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="intake-"))
cfg = types.ModuleType("server.server_app.config")      # intake.py без базы и настроек сервера
cfg.FILE_STORAGE_ROOT = str(root)
cfg.logger = logging.getLogger("dedup-repeat-tests")
sys.modules.setdefault("server.server_app.config", cfg)
for _name in ("media", "file_guard", "workers"):         # тяжёлые модули: приёму копии по отпечатку не нужны
    sys.modules.setdefault("server.server_app." + _name, types.ModuleType("server.server_app." + _name))
sys.modules["server.server_app.media"].__dict__.setdefault("StoredFile", object)
try:
    import fastapi  # noqa: F401
except ImportError:                                       # проверка и без FastAPI: нужен только HTTPException
    _fa = types.ModuleType("fastapi")
    _fa.HTTPException = type("HTTPException", (Exception,), {"__init__": lambda self, status_code=500, detail="":
                                                            Exception.__init__(self, detail)})
    sys.modules["fastapi"] = _fa

from server.server_app import blobs, intake  # noqa: E402

ROW = types.SimpleNamespace(stored_path="", extension="")
blobs.consume_token = lambda db, user, token: ROW
results = []


def check(name, ok, extra=""):
    results.append(ok)
    print(("  ok   " if ok else "  FAIL ") + name + (("  — %s" % extra) if extra and not ok else ""))


async def repeat(rel, ext):
    ROW.stored_path, ROW.extension = rel, ext
    [up] = await intake.as_uploads([{"token": "t", "meta": {"name": "x"}}], 1, None)
    parts = []
    while True:
        chunk = await up.read(1024 * 1024)
        if not chunk:
            break
        parts.append(chunk)
    up.close()
    return up.size, b"".join(parts)


def main():
    (root / "chat").mkdir()
    original = os.urandom(3 * 1024 * 1024 + 4321)
    (root / "chat" / "a.zip.gz").write_bytes(gzip.compress(original, 6))
    size, got = asyncio.run(repeat("chat/a.zip.gz", "zip"))
    check("документ в обёртке (имя.zip.gz) — читается сам файл", got == original and size == len(original),
          "%d байт из %d" % (len(got), len(original)))
    plain = b"no extension\n" * 1000
    (root / "chat" / "b.gz").write_bytes(gzip.compress(plain))
    size, got = asyncio.run(repeat("chat/b.gz", ""))
    check("документ без расширения (<uuid>.gz) — читается сам файл", got == plain and size == len(plain))
    own = gzip.compress(b"user archive " * 500)
    (root / "chat" / "c.gz").write_bytes(own)
    size, got = asyncio.run(repeat("chat/c.gz", "gz"))
    check("свой .gz пользователя, лежащий как есть, — без распаковки", got == own and size == len(own))
    (root / "chat" / "d.gz.gz").write_bytes(gzip.compress(own))
    size, got = asyncio.run(repeat("chat/d.gz.gz", "gz"))
    check("свой .gz в обёртке чата (<uuid>.gz.gz) — снимается одна обёртка", got == own)
    raw = original[:200000]
    (root / "chat" / "e.mp4").write_bytes(raw)
    size, got = asyncio.run(repeat("chat/e.mp4", "mp4"))
    check("несжатый файл (видео) — как есть", got == raw and size == len(raw))
    print("ИТОГ: %d из %d" % (sum(results), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
