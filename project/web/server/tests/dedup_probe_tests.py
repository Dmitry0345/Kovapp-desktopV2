"""Быстрая проверка копии по образцу: совпадение образца с телефоном и страницей, пределы и права — на настоящем blobs.py."""
import logging
import os
import sys
import tempfile
import types

root = tempfile.mkdtemp(prefix="blobs-")
cfg = types.ModuleType("server.server_app.config")      # blobs.py без базы и настроек сервера
cfg.FILE_STORAGE_ROOT = root
cfg.logger = logging.getLogger("dedup-probe-tests")
sys.modules.setdefault("server.server_app.config", cfg)

from server.server_app import blobs  # noqa: E402

results = []


def check(name, ok, extra=""):
    results.append(ok)
    print(("  ok   " if ok else "  FAIL ") + name + (("  — %s" % extra) if extra and not ok else ""))


class Row:
    def __init__(self, path, uploader, verified):
        self.stored_path, self.uploader_id, self.verified = path, uploader, verified


class Col:
    """Поле модели для упрощённой базы: сравнение и сортировка не ломают запрос."""
    def __eq__(self, other): return True
    def __hash__(self): return 0
    def desc(self): return self
    def is_(self, other): return True
    def isnot(self, other): return True


class SeqDb:
    """Первый запрос (по образцу) — пусто, второй (старые записи) — строки."""
    def __init__(self, *answers): self.answers = list(answers)
    def query(self, *_): return self
    def filter(self, *_): return self
    def order_by(self, *_): return self
    def limit(self, *_): return self
    def all(self): return self.answers.pop(0) if self.answers else []


class FakeDb:
    def __init__(self, rows): self.rows = rows
    def query(self, *_): return self
    def filter(self, *_): return self
    def order_by(self, *_): return self
    def limit(self, *_): return self
    def all(self): return self.rows


def main():
    vectors = {1_000_003: "4a145cef8f4818566691a9dcef90ade4bb237f064ec3e88e2e3492c0007a11af",
               50_000: "9963c1f3c7c5caf752719aaa74fbed94aaf33bfbfdff768c6c4b3d918822441d"}
    for n, want in vectors.items():
        p = os.path.join(root, "v%d.bin" % n)
        open(p, "wb").write(bytes((i * 31 + 7) & 255 for i in range(n)))
        check("образец %d байт совпадает с телефоном и страницей" % n, blobs.sample_of(p) == want)

    stored = os.path.join(root, "kept.mp4")
    open(stored, "wb").write(b"x" * 1024)
    blobs._abs = lambda rel: blobs.Path(os.path.join(root, rel))
    blobs.available = lambda db: True
    blobs._model = lambda: type("Blob", (), {"source_size": Col(), "source_sample": Col(), "source_tree": Col(), "id": Col()})
    s = "a" * 64
    size = blobs.MIN_SIZE + 10
    own = FakeDb([Row("kept.mp4", 7, False)])
    check("свой сжатый файл — кандидат", blobs.probe(own, 7, size, s)["candidate"] is True)
    check("чужой неподтверждённый — не кандидат", blobs.probe(own, 8, size, s)["candidate"] is False)
    check("чужой подтверждённый (принят без сжатия) — кандидат", blobs.probe(FakeDb([Row("kept.mp4", 7, True)]), 8, size, s)["candidate"] is True)
    check("запись без файла на диске — не кандидат", blobs.probe(FakeDb([Row("gone.mp4", 7, True)]), 7, size, s)["candidate"] is False)
    check("неверный образец отклоняется", blobs.probe(own, 7, size, "zz")["candidate"] is False
          and blobs.probe(own, 7, size, "g" * 64)["candidate"] is False)
    check("слишком маленький файл не проверяется", blobs.probe(own, 7, 10, s)["candidate"] is False)
    blobs._probe_hits.clear()
    mine9 = FakeDb([Row("kept.mp4", 9, False)])
    got = [blobs.probe(mine9, 9, size, s)["candidate"] for _ in range(blobs.PROBE_LIMIT + 5)]
    check("не больше %d быстрых проверок в минуту на человека" % blobs.PROBE_LIMIT,
          all(got[:blobs.PROBE_LIMIT]) and not any(got[blobs.PROBE_LIMIT:]), (got.count(True), got.count(False)))
    blobs._probe_hits.clear()
    check("старая запись без образца — кандидат по размеру (сверит полный отпечаток)",
          blobs.probe(SeqDb([], [Row("kept.mp4", 7, False)]), 7, size, s)["candidate"] is True)
    check("старая чужая неподтверждённая — не кандидат", blobs.probe(SeqDb([], [Row("kept.mp4", 7, False)]), 8, size, s)["candidate"] is False)
    check("у другого человека свой предел", blobs.probe(FakeDb([Row("kept.mp4", 10, False)]), 10, size, s)["candidate"] is True)

    # чат: файл записывается в реестр копий по отпечатку исходника от телефона
    from types import SimpleNamespace as NS
    added = []
    class AddDb:
        def add(self, row): added.append(row)
    class FakeBlob:
        def __init__(self, **kw): self.__dict__.update(kw)
    blobs._model = lambda: FakeBlob
    big = os.path.join(root, "chat.mp4")
    open(big, "wb").write(b"v" * (blobs.MIN_SIZE + 100))
    up = NS(filename="обход.mp4", kov_meta={"tree": "A" * 64, "source_size": 76_900_000, "sample": "b" * 64, "prep": "device"})
    okk = blobs.register_upload(AddDb(), up, big, "chat/1/chat.mp4", 7)
    r = added[0] if added else None
    check("файл чата записан в реестр копий как свой", okk and r is not None and r.source_tree == "a" * 64 and r.source_size == 76_900_000
          and r.source_sample == "b" * 64 and r.uploader_id == 7 and r.verified is False and r.kind == "video" and r.stored_path == "chat/1/chat.mp4")
    added.clear()
    check("без отпечатка — не записывается", blobs.register_upload(AddDb(), NS(filename="x.mp4", kov_meta={}), big, "chat/x.mp4", 7) is False and not added)
    check("файла нет на диске — не записывается", blobs.register_upload(AddDb(), up, os.path.join(root, "нет.mp4"), "chat/нет.mp4", 7) is False and not added)
    blobs._model = lambda: type("Blob", (), {"source_size": Col(), "source_sample": Col(), "source_tree": Col(), "id": Col()})
    blobs._probe_hits.clear()
    check("записанный файл находит быстрая проверка того же человека", blobs.probe(FakeDb([Row("chat/1/chat.mp4" if False else "kept.mp4", 7, False)]), 7, size, s)["candidate"] is True)

    bad = results.count(False)
    print("Итого: %d проверок, ошибок: %d" % (len(results), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
