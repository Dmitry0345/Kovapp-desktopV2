# -*- coding: utf-8 -*-
"""Отдельные процессы для обработки загружаемых файлов: работа идёт на других ядрах,
основной процесс сервера только принимает запросы. Не получилось — выполняем в потоке."""
import asyncio
import functools
import os
import time

from .config import logger

_POOL = None
_BROKEN = False


def _size() -> int:
    raw = os.getenv("UPLOAD_PROCESSES")
    if raw in (None, "") and (os.getenv("KOVAPP_ROLE") or "").strip().lower() == "uploader":
        # процесс приёма загрузок сам отдельный: крупные файлы обрабатывает в своих потоках
        return 0
    if raw not in (None, ""):
        try:
            return max(0, int(raw))
        except ValueError:
            return 0
    try:
        from .lanes import PLAN
        return max(1, int(PLAN["cores"]) - 1)
    except Exception:
        return 1


def _pool():
    global _POOL
    if _POOL is None:
        import multiprocessing as mp
        from concurrent.futures import ProcessPoolExecutor
        os.environ["KOVAPP_WORKER"] = "1"
        _POOL = ProcessPoolExecutor(max_workers=_size(), mp_context=mp.get_context("spawn"))
        logger.info("Обработка файлов: отдельных процессов %d", _size())
    return _POOL


class _Failure:
    def __init__(self, error):
        self.error = error


def _measured(fn, args, kwargs, in_process):
    """Выполняет fn и меряет процессорное время только этой работы; ошибку fn возвращает, а не бросает."""
    clock = time.process_time if in_process else time.thread_time
    began = clock()
    try:
        result = fn(*args, **kwargs)
    except Exception as e:
        return _Failure(e), clock() - began
    return result, clock() - began


async def run(fn, *args, **kwargs):
    """Результат fn и её процессорное время. fn — функция уровня модуля (её передают в другой процесс)."""
    global _BROKEN, _POOL
    if not _BROKEN and _size() > 0:
        try:
            loop = asyncio.get_running_loop()
            result, cpu = await loop.run_in_executor(_pool(), functools.partial(_measured, fn, args, kwargs, True))
        except Exception as e:
            _BROKEN = True
            _POOL = None
            logger.warning("Отдельные процессы обработки файлов недоступны (%r) — обработка в потоках", e)
        else:
            if isinstance(result, _Failure):
                raise result.error
            return result, cpu
    result, cpu = await asyncio.to_thread(_measured, fn, args, kwargs, False)
    if isinstance(result, _Failure):
        raise result.error
    return result, cpu


async def run_here(fn, *args, **kwargs):
    """То же, что run, но в потоке этого процесса: для мелкой работы передача в другой процесс дороже самой работы."""
    result, cpu = await asyncio.to_thread(_measured, fn, args, kwargs, False)
    if isinstance(result, _Failure):
        raise result.error
    return result, cpu


def inspect(temp_path, name, digest):
    """Проверка ResGuard и, если нужно, отпечаток — одной передачей в рабочий процесс."""
    from . import blobs, file_guard
    verdict = file_guard.assess(temp_path, name)
    source = blobs.source_digest(temp_path) if digest and verdict.outcome != "reject" else None
    return verdict, source


def store_image_path(folder, name, temp_path, thumb_ready):
    from . import media
    with open(temp_path, "rb") as fh:
        return media.store_image(folder, name, fh.read(), thumb_ready=thumb_ready)


def store_document_path(folder, name, temp_path, compress):
    from . import media
    return media.store_document_path(folder, name, temp_path, compress=compress)


def assess(temp_path, name):
    from . import file_guard
    return file_guard.assess(temp_path, name)


def source_digest(temp_path):
    from . import blobs
    return blobs.source_digest(temp_path)


def inflate_to_temp(temp_path, size):
    from . import gzip_tools
    return gzip_tools.inflate_to_temp(temp_path, size)
