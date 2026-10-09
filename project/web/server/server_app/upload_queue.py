"""Очередь обработки загружаемых файлов и реестр прогресса.

Зачем. Раньше каждый запрос на загрузку обрабатывал свои файлы сам, без
оглядки на остальных: двадцать видео от нескольких человек одновременно
запускали двадцать перекодировок, память кончалась, и следом падала база.

Как устроено.
* Один общий семафор на тяжёлую обработку: одновременно считается не больше
  PROCESS_MAX_PARALLEL файлов (видео дополнительно ограничено своим семафором
  в media.py). Всё, что сверх, ждёт в очереди — запрос при этом жив, а клиент
  видит «В очереди, перед вами N файлов».
* Реестр прогресса: клиент придумывает upload_id, кладёт его в форму и раз в
  полсекунды спрашивает GET /upload/progress/{id}. Сервер отдаёт по каждому
  файлу состояние (в очереди / сжатие / готово / ошибка) и общий процент,
  где доля обработки подсчитана по факту, а не угадана.
* Записи живут в памяти и чистятся через PROGRESS_TTL секунд после конца.
"""
from __future__ import annotations

import asyncio
import math
import os
import threading
import time
from typing import Dict, List, Optional
from .envtools import env_int

# 0 — сколько задач идёт разом решает распорядитель нагрузки по живым
# замерам; число — жёсткий потолок вручную.
PROCESS_MAX_PARALLEL = max(0, env_int("PROCESS_MAX_PARALLEL", 0))   # то же, что PROCESS_MAX_PARALLEL_HARD в config
PROGRESS_TTL = env_int("UPLOAD_PROGRESS_TTL", 600)

# Веса этапов в общем проценте: сеть — первая часть, обработка — вторая.
# Для видео обработка занимает большую часть времени, для документов — малую.
WEIGHTS = {"video": 0.35, "image": 0.6, "document": 0.8}   # доля сетевой отправки

_lock = threading.Lock()
_jobs: Dict[str, dict] = {}
def _governor():
    from .load_governor import governor
    return governor()
_waiting = 0


def _now() -> float:
    return time.time()


def _cleanup() -> None:
    dead = [k for k, v in _jobs.items() if v.get("finished_at") and _now() - v["finished_at"] > PROGRESS_TTL]
    for k in dead:
        _jobs.pop(k, None)


def start(upload_id: Optional[str], files: List[dict]) -> Optional[str]:
    """Регистрирует загрузку: files — [{name, size, kind}], kind ∈ video|image|document."""
    if not upload_id:
        return None
    with _lock:
        _cleanup()
        _jobs[upload_id] = {
            "created_at": _now(),
            "finished_at": None,
            "files": [
                {"index": i, "name": f.get("name") or f"file{i}", "size": int(f.get("size") or 0),
                 "kind": f.get("kind") or "document", "state": "received", "percent": 0, "note": "",
                 "started_at": None}
                for i, f in enumerate(files)
            ],
        }
        _publish(upload_id, force=True)
    return upload_id


_published: Dict[str, float] = {}


def _publish(upload_id: str, force: bool = False) -> None:
    """Копия хода загрузки в общем хранилище: опрос может прийти в другой процесс (вызывать под _lock)."""
    job = _jobs.get(upload_id)
    if not job:
        return
    now = _now()
    if not force and now - _published.get(upload_id, 0) < 0.3:
        return
    _published[upload_id] = now
    from . import shared
    shared.put("progress", upload_id, job, PROGRESS_TTL + 3600)


# Сколько примерно длится обработка одного файла, сек. Числа нужны не для
# точности, а чтобы полоса ползла, пока ffmpeg молча считает: без этого
# двадцать файлов давали двадцать рывков и долгие замирания между ними.
TYPICAL = {"video": 22.0, "image": 2.5, "document": 1.0}


def update(upload_id: Optional[str], index: int, state: str, percent: Optional[float] = None,
           note: str = "") -> None:
    if not upload_id:
        return
    with _lock:
        job = _jobs.get(upload_id)
        if not job or index >= len(job["files"]):
            return
        f = job["files"][index]
        if state == "processing" and f.get("state") != "processing":
            f["started_at"] = _now()
        f["state"] = state
        if percent is not None:
            f["percent"] = max(0, min(100, int(percent)))
        if note:
            f["note"] = note
        if state in ("done", "error"):
            f["percent"] = 100 if state == "done" else f["percent"]
        _publish(upload_id, force=state in ("done", "error"))


def finish(upload_id: Optional[str]) -> None:
    if not upload_id:
        return
    with _lock:
        job = _jobs.get(upload_id)
        if job:
            job["finished_at"] = _now()
            _publish(upload_id, force=True)
            _published.pop(upload_id, None)


def _live_percent(f: dict) -> float:
    """Процент файла с учётом того, что он прямо сейчас считается.

    Пока идёт сжатие, точной доли нет — ffmpeg о ней не сообщает. Вместо
    рывков 0 → 100 отдаём плавную кривую по времени: она быстро растёт в
    начале и притормаживает у 92%, а до 100 доводит настоящее завершение.
    """
    state = f.get("state")
    if state in ("done", "error"):
        return 100.0
    if state == "processing":
        # ffmpeg присылает настоящий процент — он точнее любой оценки
        real = float(f.get("percent") or 0)
        if real > 0:
            return max(8.0, min(99.0, real))
        started = f.get("started_at")
        if not started:
            return 8.0
        tau = TYPICAL.get(f.get("kind") or "document", 1.0)
        elapsed = max(0.0, _now() - started)
        return 8.0 + 84.0 * (1.0 - math.exp(-elapsed / tau))
    if state == "queued":
        return 4.0
    return float(f.get("percent") or 0)


def snapshot(upload_id: str) -> Optional[dict]:
    with _lock:
        job = _jobs.get(upload_id)
        if not job:
            from . import shared
            job = shared.get("progress", upload_id)
        if not job:
            return None
        files = [dict(f) for f in job["files"]]
        for f in files:
            f["percent"] = round(_live_percent(f), 1)
        total = len(files) or 1
        overall = sum(f["percent"] for f in files) / total
        return {
            "upload_id": upload_id,
            "files": files,
            "percent": round(overall, 1),
            "done": all(f["state"] in ("done", "error") for f in files),
            "waiting_ahead": _waiting,
            "parallel": _governor().limits().media,
        }


class Slot:
    """Место в очереди обработки: `with queue.Slot(upload_id, index):`."""

    def __init__(self, upload_id: Optional[str], index: int, kind: str = "media", need_bytes: int = 0):
        self.upload_id = upload_id
        self.index = index
        self.kind = kind
        self.need_bytes = need_bytes
        self._slot = None

    def __enter__(self):
        global _waiting
        with _lock:
            _waiting += 1
            ahead = _waiting - 1
        update(self.upload_id, self.index, "queued", None,
               f"В очереди, перед вами {ahead}" if ahead else "В очереди")
        # Место выдаёт распорядитель: он смотрит на живую нагрузку и память,
        # но одну задачу пропускает всегда — очередь не должна вставать
        if self.kind != "video":
            self._slot = _governor().slot(self.kind, self.need_bytes)
            self._slot.__enter__()
        with _lock:
            _waiting -= 1
        update(self.upload_id, self.index, "processing")
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._slot:
            self._slot.__exit__(exc_type, exc, tb)
        return False


async def run_processing(upload_id: Optional[str], index: int, fn, *args, note: str = "Обработка",
                         kind: str = "media", need_bytes: int = 0):
    """Выполняет тяжёлую функцию в потоке под присмотром распорядителя."""
    def _job():
        with Slot(upload_id, index, kind, need_bytes):
            update(upload_id, index, "processing", None, note)
            return fn(*args)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_pool(), _job)


_executor = None


def _pool():
    global _executor
    if _executor is None:
        from concurrent.futures import ThreadPoolExecutor
        _executor = ThreadPoolExecutor(max_workers=max(8, env_int("UPLOAD_WORKERS", 16)),
                                       thread_name_prefix="upload-work")
    return _executor
