"""gzip без нагрузки на процессор сервера.

Статика приложения сжимается один раз (уровень 9) и дальше отдаётся готовой.
Документы сжимает устройство; сервер лишь проверяет, что внутри то, что
заявлено, хранит присланный gzip как есть и отдаёт его браузеру с
Content-Encoding: gzip — распаковывает сам браузер.
"""
import gzip
import logging
import os
import zlib
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

STATIC_EXT = {".html", ".js", ".mjs", ".css", ".svg", ".json", ".webmanifest", ".map", ".txt",
              ".xml", ".wasm", ".bcmap", ".pfb", ".ttf", ".otf", ".ico"}
STATIC_MIN = 1024
PLAIN_LIMIT = 2 * 1024 ** 3


def static_compressible(path: str) -> bool:
    return os.path.splitext(str(path))[1].lower() in STATIC_EXT


def fresh(src_stat, gz_stat) -> bool:
    """Сжатая копия соответствует файлу: при создании ей ставится то же время
    изменения. Любая замена файла (новее или старше) делает копию негодной."""
    return int(gz_stat.st_mtime) == int(src_stat.st_mtime)


# ── Статика: сжать один раз ──────────────────────────────────────────────
def precompress_tree(root, level: int = 9) -> dict:
    """Кладёт рядом с файлами статики их .gz. Пересжимает только изменённые."""
    made = kept = 0
    for dirpath, _, names in os.walk(root):
        for name in names:
            path = os.path.join(dirpath, name)
            if name.endswith(".gz") or not static_compressible(path):
                continue
            try:
                st = os.stat(path)
                if st.st_size < STATIC_MIN:
                    continue
                target = path + ".gz"
                try:
                    if fresh(st, os.stat(target)):
                        kept += 1
                        continue
                except OSError:
                    pass
                with open(path, "rb") as fh:
                    data = fh.read()
                packed = gzip.compress(data, compresslevel=level, mtime=0)
                if len(packed) > len(data) * 0.95:
                    continue
                tmp = "%s.%d.tmp" % (target, os.getpid())
                with open(tmp, "wb") as fh:
                    fh.write(packed)
                os.replace(tmp, target)
                os.utime(target, (st.st_mtime, st.st_mtime))
                made += 1
            except OSError as e:
                logger.warning("Статика: не удалось подготовить сжатую копию %s (%s)", path, e)
                return {"made": made, "kept": kept, "error": str(e)}
    return {"made": made, "kept": kept}


# ── Файлы, сжатые на устройстве ──────────────────────────────────────────
def inflate_to_temp(src: Path, declared: int, limit: int = PLAIN_LIMIT) -> Path:
    """Распаковывает присланный gzip во временный файл для проверок.

    Распаковка в разы дешевле сжатия. Останавливается, как только данных
    стало больше заявленного, — «бомба» дальше первых мегабайт не пройдёт.
    Контрольную сумму и длину из хвоста gzip проверяет сам zlib.
    """
    src = Path(src)
    cap = min(limit, declared) if declared > 0 else limit
    dst = Path(str(src) + ".plain")
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    total = 0
    try:
        with open(src, "rb") as fin, open(dst, "wb") as fout:
            while not d.eof:
                chunk = fin.read(1 << 20)
                if not chunk:
                    raise ValueError("сжатые данные оборваны")
                while chunk and not d.eof:
                    out = d.decompress(chunk, 1 << 22)
                    total += len(out)
                    if total > cap:
                        raise ValueError("распакованный объём больше заявленного")
                    fout.write(out)
                    chunk = d.unconsumed_tail
            if d.unused_data.strip(b"\0") or fin.read(1).strip(b"\0"):
                raise ValueError("после сжатых данных есть лишнее")
        if declared > 0 and total != declared:
            raise ValueError("размер не совпал с заявленным")
        return dst
    except (zlib.error, ValueError, OSError) as e:
        dst.unlink(missing_ok=True)
        raise ValueError("файл, сжатый на устройстве, не прошёл проверку: %s" % e)


def original_size(path) -> Optional[int]:
    """Размер исходных данных из хвоста gzip (для файлов до 4 ГиБ)."""
    try:
        with open(path, "rb") as fh:
            fh.seek(-4, os.SEEK_END)
            return int.from_bytes(fh.read(4), "little")
    except OSError:
        return None


def wants_encoded(ce, accept_encoding, range_header) -> bool:
    """Отдать хранимый gzip без распаковки: только веб-клиенту, который об этом
    попросил (ce=1), принимает gzip и не просит кусок файла."""
    return str(ce or "") == "1" and "gzip" in (accept_encoding or "").lower() and not range_header
