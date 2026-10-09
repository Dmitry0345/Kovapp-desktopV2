# -*- coding: utf-8 -*-
"""Фоновые запросы: окно не замирает, пока сервер отвечает.

run(fn, *args, done=..., failed=..., owner=виджет) выполняет fn в пуле
потоков и возвращает результат в поток интерфейса. Если виджет-владелец
уже удалён (раздел закрыли), ответ молча отбрасывается.
"""

import traceback

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

import shiboken6

_POOL = QThreadPool.globalInstance()
_POOL.setMaxThreadCount(max(4, min(8, _POOL.maxThreadCount())))
_ALIVE = set()                 # держим ссылки, пока задание не закончилось
_OWNER = {}                    # задание → виджет-владелец: раздел ещё подгружается (pending_for)


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(object)


class _Job(QRunnable):
    def __init__(self, fn, args, kwargs):
        super().__init__()
        self.setAutoDelete(False)
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = _Signals()

    def run(self):
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:      # ошибка уходит в интерфейс, не роняет поток
            exc.trace = traceback.format_exc()
            emit(self.signals.failed, exc)
        else:
            emit(self.signals.done, result)


def emit(signal, value):
    """Ответ в поток интерфейса. Программу уже закрыли — PySide6 удалил сигналы вместе с окнами: ответ не нужен."""
    try:
        signal.emit(value)
    except RuntimeError:
        pass


def _alive(owner):
    if owner is None:
        return True
    try:
        return shiboken6.isValid(owner)
    except Exception:
        return False


def pending_for(widget):
    """Сколько фоновых запросов ещё ждут этот виджет и его потомков — раздел ещё подгружается."""
    n = 0
    for owner in list(_OWNER.values()):
        try:
            if owner is widget or (_alive(owner) and _alive(widget) and widget.isAncestorOf(owner)):
                n += 1
        except Exception:
            pass
    return n


def run(fn, *args, done=None, failed=None, owner=None, **kwargs):
    job = _Job(fn, args, kwargs)
    _ALIVE.add(job)
    if owner is not None:
        _OWNER[job] = owner

    def finish(callback, value):
        _ALIVE.discard(job)
        _OWNER.pop(job, None)
        if callback is not None and _alive(owner):
            callback(value)

    job.signals.done.connect(lambda v: finish(done, v))
    job.signals.failed.connect(lambda e: finish(failed if failed else _log, e))
    _POOL.start(job)
    return job


def _log(exc):
    print("[фоновый запрос] %s" % getattr(exc, "trace", exc))
