# -*- coding: utf-8 -*-
"""Отпечаток содержимого (js/media/hash.js, сервер: blobs.tree_hash_file).

Дерево: SHA-256 каждого куска по 4 МиБ подряд, затем размер — 8 байт,
старший байт первым, и SHA-256 от всего этого. Образец: 9 участков по
64 КБ от начала до конца файла, перед каждым — его смещение (8 байт),
в конце — размер; ≈0,6 МБ чтения вместо всего файла.
"""

import hashlib
import os

LEAF = 4 * 1024 * 1024
SAMPLE_WINDOW = 64 * 1024
SAMPLE_WINDOWS = 9


def tree(path, progress=None, cancelled=None):
    size = os.path.getsize(path)
    top = hashlib.sha256()
    done = 0
    with open(path, "rb") as f:
        while True:
            part = f.read(LEAF)
            if not part:
                break
            top.update(hashlib.sha256(part).digest())
            done += len(part)
            if progress and size:
                progress(min(1.0, done / size))
            if cancelled and cancelled():
                raise InterruptedError("Отменено")
    top.update(size.to_bytes(8, "big"))
    return top.hexdigest()


class TreeHasher:
    """Тот же отпечаток по ходу записи (для сжимаемого видео)."""

    def __init__(self):
        self._top = hashlib.sha256()
        self._buf = bytearray()
        self.size = 0

    def update(self, data):
        self.size += len(data)
        self._buf += data
        while len(self._buf) >= LEAF:
            self._top.update(hashlib.sha256(bytes(self._buf[:LEAF])).digest())
            del self._buf[:LEAF]

    def hexdigest(self):
        top = self._top.copy()
        if self._buf:
            top.update(hashlib.sha256(bytes(self._buf)).digest())
        top.update(self.size.to_bytes(8, "big"))
        return top.hexdigest()


def range_hash(path, offset, length):
    with open(path, "rb") as f:
        f.seek(offset)
        return hashlib.sha256(f.read(length)).hexdigest()


def sample(path):
    size = os.path.getsize(path)
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for i in range(SAMPLE_WINDOWS):
            pos = 0 if size <= SAMPLE_WINDOW else (size - SAMPLE_WINDOW) * i // (SAMPLE_WINDOWS - 1)
            length = min(SAMPLE_WINDOW, size - pos)
            f.seek(pos)
            h.update(pos.to_bytes(8, "big"))
            h.update(f.read(length))
    h.update(size.to_bytes(8, "big"))
    return h.hexdigest()
