# -*- coding: utf-8 -*-
import base64
import hashlib
import json
from types import SimpleNamespace
import os
import random
import secrets
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from .chunked import tree_hash_file
from .config import FILE_STORAGE_ROOT, logger
from . import shared

# ── Проверка копий по содержимому ──
WINDOW = 64 * 1024
WINDOWS = 6
MIN_SIZE = 256 * 1024
_lock = threading.Lock()
_challenges = {}
_tokens = {}


def _model():
    from server.database.models import FileBlob
    return FileBlob


def sample_windows(path, size: int, count: int = WINDOWS, length: int = WINDOW) -> list:
    if size <= length:
        return []
    rnd = random.SystemRandom()
    out = []
    with open(path, "rb") as fh:
        for _ in range(count):
            offset = rnd.randrange(0, size - length)
            fh.seek(offset)
            out.append({"o": offset, "l": length, "h": hashlib.sha256(fh.read(length)).hexdigest()})
    return out


SAMPLE_WINDOWS = 9   # образец: 9 участков по WINDOW от начала до конца + размер (как hash.js и FileHasher.java)


def sample_of(path) -> Optional[str]:
    """Образец файла: дёшево (≈0,6 МБ чтения) отличает разные файлы; полный отпечаток — только для кандидатов."""
    size = os.path.getsize(path)
    if size <= 0:
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for i in range(SAMPLE_WINDOWS):
            pos = 0 if size <= WINDOW else (size - WINDOW) * i // (SAMPLE_WINDOWS - 1)
            f.seek(pos)
            h.update(pos.to_bytes(8, "big"))
            h.update(f.read(min(WINDOW, size - pos)))
    h.update(size.to_bytes(8, "big"))
    return h.hexdigest()


def source_digest(path) -> dict:
    d = tree_hash_file(path)
    d["windows"] = sample_windows(path, d["size"])
    d["sample"] = sample_of(path)
    return d


def link_or_copy(src: Path, dst: Path) -> str:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
        return "link"
    except OSError:
        shutil.copy2(src, dst)
        return "copy"


def _abs(relative: str) -> Path:
    return Path(FILE_STORAGE_ROOT) / relative


def clean_meta(meta: Optional[dict]) -> dict:
    meta = meta or {}
    out = {}
    th = str(meta.get("thumb_hash") or "")
    if th and len(th) <= 64:
        try:
            if 5 <= len(base64.b64decode(th, validate=True)) <= 48:
                out["thumb_hash"] = th
        except Exception:
            pass
    for key in ("width", "height"):
        try:
            v = int(meta.get(key) or 0)
            if 0 < v <= 20000:
                out[key] = v
        except (TypeError, ValueError):
            pass
    try:
        dur = float(meta.get("duration") or 0)
        if 0 < dur <= 86400:
            out["duration"] = round(dur, 3)
    except (TypeError, ValueError):
        pass
    return out


_table_state = {"ok": None, "at": 0.0}


def available(db) -> bool:
    """Таблица file_blobs на месте (при необходимости создаётся здесь же).

    Поиск копий — ускорение, а не обязательный шаг: если таблицы нет и создать
    её не вышло, файлы принимаются как обычно, без поиска копий.
    """
    now = time.time()
    if _table_state["ok"] or (_table_state["ok"] is False and now - _table_state["at"] < 300):
        return bool(_table_state["ok"])
    try:
        from .config import ensure_dialect_ready
        ensure_dialect_ready(db.get_bind())
        _model().__table__.create(bind=db.get_bind(), checkfirst=True)
        _table_state.update(ok=True, at=now)
    except Exception:
        logger.exception("Таблица file_blobs недоступна — поиск копий файлов отключён на 5 минут")
        _table_state.update(ok=False, at=now)
    return bool(_table_state["ok"])


def register(db, stored, *, source: Optional[dict] = None, meta: Optional[dict] = None,
             uploader_id: Optional[int] = None, verified: bool = False):
    if not available(db):
        return None
    Blob = _model()
    info = clean_meta(meta)
    row = Blob(stored_path=stored.relative_path, kind=stored.kind, extension=stored.extension,
               display_name=stored.display_name, size=stored.size, uploader_id=uploader_id,
               source_tree=(source or {}).get("tree"), source_size=(source or {}).get("size"),
               source_sample=(source or {}).get("sample"),
               verified=bool(verified and source and source.get("windows")),
               windows=json.dumps((source or {}).get("windows") or []), **info)
    db.add(row)
    return row


_VIDEO_EXT = {"mp4", "mov", "m4v", "webm", "mkv"}
_IMAGE_EXT = {"jpg", "jpeg", "png", "webp", "gif", "heic", "heif", "bmp"}


def register_upload(db, upload, path, rel: str, user_id: int) -> bool:
    """Файл чата — в реестр копий по отпечатку исходника, который посчитал телефон или браузер:
    повторная отправка того же файла этим человеком пройдёт мгновенно. Запись «своя» (verified=False):
    по ней находит только тот же человек."""
    meta = getattr(upload, "kov_meta", None) or {}
    tree = str(meta.get("tree") or "")[:64].lower()
    try:
        size = int(meta.get("source_size") or 0)
    except (TypeError, ValueError):
        size = 0
    p = Path(path)
    if not p.exists() and Path(str(p) + ".gz").exists():
        p, rel = Path(str(p) + ".gz"), str(rel) + ".gz"      # документ чата хранится сжатым: имя.расширение.gz
    if len(tree) != 64 or size < MIN_SIZE or not p.exists() or not available(db):
        return False
    ext = (p.with_suffix("") if p.name.endswith(".gz") else p).suffix.lstrip(".").lower()
    stored = SimpleNamespace(relative_path=str(rel).replace("\\", "/"), extension=ext, size=p.stat().st_size,
                             kind="video" if ext in _VIDEO_EXT else "image" if ext in _IMAGE_EXT else "document",
                             display_name=str(getattr(upload, "filename", "") or p.name))
    register(db, stored, source={"tree": tree, "size": size, "sample": str(meta.get("sample") or "")[:64].lower() or None},
             meta=meta, uploader_id=user_id, verified=False)
    return True


def candidates(db, tree: str, size: int, user_id: int) -> list:
    if not available(db):
        return []
    Blob = _model()
    try:
        rows = db.query(Blob).filter(Blob.source_tree == tree, Blob.source_size == size).order_by(Blob.id.desc()).limit(20).all()
    except Exception:
        logger.warning("Поиск копий не выполнен", exc_info=True)
        return []
    return [r for r in rows if _abs(r.stored_path).exists() and (r.verified or r.uploader_id == user_id)]


_probe_hits = {}       # человек → время недавних быстрых проверок
PROBE_LIMIT = 240      # быстрых проверок в минуту на человека


def probe(db, user_id: int, size: int, sample: str) -> dict:
    """Быстрая проверка: есть ли кандидат с тем же размером и образцом — среди своих или подтверждённых файлов.
    Пропуск всё равно выдаётся только после испытания (issue_challenge / answer_challenge)."""
    now = time.time()
    with _lock:
        hits = [t for t in _probe_hits.get(user_id, ()) if now - t < 60]
        if len(hits) >= PROBE_LIMIT:
            _probe_hits[user_id] = hits
            return {"candidate": False}
        hits.append(now)
        _probe_hits[user_id] = hits
    sample = str(sample or "").lower()
    if len(sample) != 64 or any(c not in "0123456789abcdef" for c in sample) or int(size or 0) < MIN_SIZE or not available(db):
        return {"candidate": False}
    Blob = _model()
    try:
        rows = db.query(Blob).filter(Blob.source_size == int(size), Blob.source_sample == sample).order_by(Blob.id.desc()).limit(20).all()
        if not rows:   # записи до появления образцов: кандидат по размеру, сверит полный отпечаток
            rows = (db.query(Blob).filter(Blob.source_size == int(size), Blob.source_sample.is_(None), Blob.source_tree.isnot(None))
                    .order_by(Blob.id.desc()).limit(20).all())
    except Exception:
        logger.warning("Быстрая проверка копии не выполнена", exc_info=True)
        return {"candidate": False}
    return {"candidate": any(_abs(r.stored_path).exists() and (r.verified or r.uploader_id == user_id) for r in rows)}


def _purge(now: float) -> None:
    for store in (_challenges, _tokens):
        for key in [k for k, v in store.items() if v["exp"] < now]:
            store.pop(key, None)


def issue_challenge(db, user_id: int, tree: str, size: int) -> dict:
    tree = str(tree or "").lower()
    rows = candidates(db, tree, int(size or 0), user_id) if len(tree) == 64 and int(size or 0) >= MIN_SIZE else []
    own = next((r for r in rows if r.uploader_id == user_id), None)
    verified = next((r for r in rows if r.verified), None)
    windows = []
    if verified:
        try:
            stored = json.loads(verified.windows or "[]")
            windows = random.SystemRandom().sample(stored, min(2, len(stored)))
        except Exception:
            windows = []
    if not windows:
        rnd = random.SystemRandom()
        span = max(1, int(size or 0) - WINDOW)
        windows = [{"o": rnd.randrange(0, span), "l": min(WINDOW, int(size or 0) or WINDOW)} for _ in range(2)]
    cid = secrets.token_hex(12)
    # в общем хранилище: ответ может прийти в другой процесс приёма загрузок
    shared.put("dedup-ch", cid, {"user": user_id, "exp": time.time() + 600, "own": own.id if own else None,
                                 "verified": verified.id if verified else None,
                                 "expect": [w.get("h") for w in windows]}, 600)
    return {"id": cid, "windows": [{"o": w["o"], "l": w["l"]} for w in windows]}


def answer_challenge(user_id: int, cid: str, hashes: list) -> Optional[str]:
    ch = shared.take("dedup-ch", str(cid or ""))
    if not ch or ch["user"] != user_id or ch["exp"] < time.time():
        return None
    blob_id = None
    if ch["verified"] and all(e and h and secrets.compare_digest(str(e), str(h).lower())
                              for e, h in zip(ch["expect"], list(hashes or []) + [None] * 2)):
        blob_id = ch["verified"]
    elif ch["own"]:
        blob_id = ch["own"]
    if not blob_id:
        return None
    token = secrets.token_hex(16)
    shared.put("dedup-tok", token, {"user": user_id, "blob": blob_id, "exp": time.time() + 3600}, 3600)
    return token


def consume_token(db, user_id: int, token: str):
    item = shared.take("dedup-tok", str(token or ""))
    if not item or item["user"] != user_id or item["exp"] < time.time():
        return None
    row = db.query(_model()).get(item["blob"])
    return row if row is not None and _abs(row.stored_path).exists() else None


def clone_into(db, row, folder: Path, display_name: str, uploader_id: int):
    from .media import StoredFile, _thumb_path_for, _relative
    src = _abs(row.stored_path)
    suffix = "".join(src.suffixes[-2:]) if src.name.endswith(".gz") else src.suffix
    target = folder / f"{uuid.uuid4()}{suffix}"
    link_or_copy(src, target)
    thumb = _thumb_path_for(src)
    if thumb.exists():
        link_or_copy(thumb, _thumb_path_for(target))
    stored = StoredFile(_relative(target.resolve()), Path(display_name or row.display_name).name,
                        row.extension or "", int(row.size or target.stat().st_size), row.kind or "document")
    meta = {"thumb_hash": row.thumb_hash, "width": row.width, "height": row.height, "duration": row.duration}
    register(db, stored, source={"tree": row.source_tree, "size": row.source_size,
                                 "windows": json.loads(row.windows or "[]"), "sample": row.source_sample},
             meta=meta, uploader_id=uploader_id, verified=bool(row.verified))
    logger.info("Копия файла по содержимому: %s → %s", row.stored_path, stored.relative_path)
    return stored, meta


def meta_for_paths(db, paths) -> dict:
    paths = [p for p in set(paths or []) if p]
    if not paths:
        return {}
    Blob = _model()
    out = {}
    try:
        for i in range(0, len(paths), 500):
            for r in db.query(Blob).filter(Blob.stored_path.in_(paths[i:i + 500])).all():
                out[r.stored_path] = {"thumb_hash": r.thumb_hash, "width": r.width, "height": r.height, "duration": r.duration}
    except Exception as e:
        logger.debug("Метаданные файлов недоступны: %s", e)
    return out


def attach(db, objects, attr: str = "filepath"):
    objects = list(objects or [])
    meta = meta_for_paths(db, [getattr(o, attr, None) for o in objects])
    for o in objects:
        m = meta.get(getattr(o, attr, None))
        if m:
            for k, v in m.items():
                try:
                    setattr(o, k, v)
                except Exception:
                    pass
    return objects


def _blob_key(path) -> str:
    """Путь, как он записан в file_blobs: относительно хранилища, через «/»."""
    raw = str(path or "")
    if os.path.isabs(raw):
        try:
            from .storage import storage_relative
            return storage_relative(raw).as_posix()
        except ValueError:
            pass
    rel = raw.replace("\\", "/").lstrip("/")
    return rel[len("file_storage/"):] if rel.lower().startswith("file_storage/") else rel


def forget(paths=(), prefixes=()) -> int:
    """Удаляет записи о стёртых файлах (paths) и о файлах стёртых папок (prefixes).

    Сбой не мешает удалению самих файлов: оставшееся уберёт sweep_missing.
    """
    if _table_state["ok"] is False:
        return 0
    wanted = set()
    for p in paths or ():
        key = _blob_key(p)
        if not key or key.endswith(".thumb.jpg"):
            continue
        base = key[:-3] if key.lower().endswith(".gz") else key
        wanted.update((base, base + ".gz"))
    heads = [k.rstrip("/") + "/" for k in (_blob_key(p) for p in prefixes or ()) if k.strip("/")]
    if not wanted and not heads:
        return 0
    from server.database.init_database import SessionLocal
    Blob = _model()
    db = SessionLocal()
    try:
        n, items = 0, sorted(wanted)
        for i in range(0, len(items), 500):
            n += db.query(Blob).filter(Blob.stored_path.in_(items[i:i + 500])).delete(synchronize_session=False)
        for head in heads:
            n += db.query(Blob).filter(Blob.stored_path.startswith(head, autoescape=True)).delete(synchronize_session=False)
        db.commit()
        if n:
            logger.info("file_blobs: удалено записей об удалённых файлах — %d", n)
        return n
    except Exception as e:
        db.rollback()
        logger.warning("file_blobs: записи об удалённых файлах не удалены (%s), их уберёт ежедневная очистка", e)
        return 0
    finally:
        db.close()


def sweep_missing(max_rows: int = 50000) -> int:
    """Страховка: удаляет записи о файлах, которых нет на диске (стёрты вручную).

    Если пропала половина и больше, вероятно, недоступен диск хранилища: не трогаем.
    """
    from server.database.init_database import SessionLocal
    from .storage import resolve_stored_path
    db = SessionLocal()
    try:
        if not available(db):
            return 0
        Blob = _model()
        rows = db.query(Blob.id, Blob.stored_path).order_by(Blob.id).limit(max_rows).all()
        gone = []
        for rid, stored in rows:
            try:
                if not resolve_stored_path(stored).is_file():
                    gone.append(rid)
            except ValueError:
                gone.append(rid)
        if not gone:
            return 0
        if len(rows) >= 20 and len(gone) * 2 >= len(rows):
            logger.warning("file_blobs: нет на диске %d файлов из %d — похоже, хранилище недоступно, очистка отложена",
                           len(gone), len(rows))
            return 0
        for i in range(0, len(gone), 500):
            db.query(Blob).filter(Blob.id.in_(gone[i:i + 500])).delete(synchronize_session=False)
        db.commit()
        logger.info("file_blobs: удалено записей о файлах, которых нет на диске — %d", len(gone))
        return len(gone)
    except Exception:
        db.rollback()
        logger.warning("file_blobs: очистка не выполнена", exc_info=True)
        return 0
    finally:
        db.close()
