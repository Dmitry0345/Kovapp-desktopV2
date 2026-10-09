# -*- coding: utf-8 -*-
import asyncio
import gzip
import mimetypes
import functools
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import HTTPException

from . import media, file_guard, blobs, gzip_tools, workers
from .config import FILE_STORAGE_ROOT, logger
from .chunked import ChunkError

# ── Единый приём файла: сеанс, обычная отправка или ссылка на копию ──
THUMB_LIMIT = 128 * 1024
_store = None


def chunk_store():
    global _store
    if _store is None:
        from .chunked import ChunkStore
        root = Path(os.getenv("UPLOAD_SESSIONS_DIR") or (Path(FILE_STORAGE_ROOT) / ".sessions"))
        idle_minutes = float(os.getenv("UPLOAD_SESSION_IDLE_MIN") or 60)
        _store = ChunkStore(root, max_total=int(os.getenv("MAX_UPLOAD_BYTES", str(2 * 1024 ** 3))),
                            ttl=max(10.0, idle_minutes) * 60)
    return _store


def parse_items(files, thumbs, meta_json: Optional[str]) -> list:
    files = list(files or [])
    thumbs = list(thumbs or [])
    if not meta_json:
        return [{"upload": f, "meta": {}} for f in files]
    try:
        described = json.loads(meta_json)
        assert isinstance(described, list) and len(described) <= 200
    except Exception:
        raise HTTPException(status_code=400, detail="Описание файлов повреждено")
    items = []
    for m in described:
        if not isinstance(m, dict):
            raise HTTPException(status_code=400, detail="Описание файлов повреждено")
        item = {"meta": m}
        src = m.get("src") or "file"
        if src == "file":
            k = int(m.get("file", -1))
            if not 0 <= k < len(files):
                raise HTTPException(status_code=400, detail="Нет файла для описания")
            item["upload"] = files[k]
        elif src == "session":
            item["session"] = str(m.get("session") or "")
        elif src == "dedup":
            item["token"] = str(m.get("token") or "")
        else:
            raise HTTPException(status_code=400, detail="Неизвестный источник файла")
        t = m.get("thumb")
        if isinstance(t, int) and 0 <= t < len(thumbs):
            item["thumb"] = thumbs[t]
        items.append(item)
    return items


def describe(item: dict) -> dict:
    meta = item.get("meta") or {}
    upload = item.get("upload")
    name = Path(str(meta.get("name") or (upload.filename if upload else "") or "file")).name
    size = int(meta.get("size") or (getattr(upload, "size", 0) if upload else 0) or 0)
    return {"name": name, "size": size, "kind": file_guard._kind_of(Path(name).suffix.lower())}


def _jpeg_size(data: bytes):
    if data[:3] != b"\xff\xd8\xff":
        return None
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            return None
        m = data[i + 1]
        if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
            i += 2
            continue
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        if m in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        i += 2 + length
    return None


async def _read_thumb(upload) -> Optional[bytes]:
    if upload is None:
        return None
    data = await upload.read(THUMB_LIMIT + 1)
    if len(data) > THUMB_LIMIT:
        return None
    size = _jpeg_size(data)
    if not size or max(size) > 640 or min(size) < 1:
        return None
    return data


async def _to_temp(upload, folder: Path, name: str) -> Path:
    temp = folder / f".{uuid.uuid4().hex}{Path(name).suffix}.uploading"
    with open(temp, "wb") as buffer:
        while chunk := await upload.read(1024 * 1024):
            buffer.write(chunk)
    return temp


def _isolated_image(folder: Path, name: str, temp: Path, verdict) -> media.StoredFile:
    out = folder / f".{uuid.uuid4().hex}.iso"
    limit = int(max(256 * 1024 * 1024, min(verdict.estimate.get("mem", 0) * 2, 3 * 1024 ** 3)))

    def quota():
        try:
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
            os.nice(10)
        except Exception:
            pass

    cmd = [sys.executable, str(Path(__file__).with_name("isolated_image.py")), str(temp), str(out),
           str(media.IMAGE_MAX_SIDE), str(media.IMAGE_WEBP_QUALITY), str(media.IMAGE_JPEG_QUALITY), "1"]
    root = str(Path(__file__).resolve().parent.parent.parent)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180, cwd=root, preexec_fn=quota)
    if proc.returncode != 0 or not out.exists():
        raise HTTPException(status_code=422, detail="Изображение не удалось обработать")
    ext = proc.stdout.strip() or ".webp"
    target = folder / f"{uuid.uuid4()}{ext}"
    shutil.move(str(out), str(target))
    try:
        media.make_image_thumbnail(target)
    except Exception:
        pass
    return media.StoredFile(media._relative(target.resolve()), Path(Path(name).stem + ext).name, ext[1:], target.stat().st_size, "image")


def video_origin(meta: dict) -> str:
    """Для журнала: сжато ли видео на устройстве, а если нет — почему (причину присылает устройство)."""
    meta = meta or {}
    if meta.get("prep") == "device":
        return "сжато на устройстве"
    why = str(meta.get("prep_reason") or "").strip()[:160]
    head, _, tail = why.partition(":")
    reasons = {"ready": "уже подходит — устройство отправило как есть",
               "slow": "сжатие на устройстве заняло бы слишком долго (оценка %s)" % tail.strip() if tail else
                       "сжатие на устройстве заняло бы слишком долго",
               "no-webcodecs": "в этом браузере нет сжатия видео (WebCodecs)",
               "decoder": "браузер не умеет читать этот кодек: %s" % tail.strip(),
               "encoder": "браузер не умеет сжимать в H.264",
               "audio": "звук в формате, который браузер не читает: %s" % tail.strip(),
               "demux": "браузер не разобрал файл: %s" % tail.strip()}
    text = reasons.get(why) or reasons.get(head) or why
    return "исходник, не сжатый на устройстве" + (" (" + text + ")" if why else " (устройство причину не сообщило)")


def _poster_usable(data: Optional[bytes]) -> bool:
    """Обложку видео с устройства берём как есть (сервер не тратит процессор), если это не почти чёрный кадр."""
    if not data:
        return False
    try:
        import io
        from PIL import Image, ImageStat
        with Image.open(io.BytesIO(data)) as im:
            im.draft("L", (64, 64))
            return ImageStat.Stat(im.convert("L")).mean[0] >= 16
    except Exception:
        return True


def _write_thumb(stored: media.StoredFile, data: bytes) -> None:
    path = media._thumb_path_for(Path(FILE_STORAGE_ROOT) / stored.relative_path)
    tmp = path.with_suffix(".part")
    tmp.write_bytes(data)
    os.replace(tmp, path)


async def store_item(folder_name: str, item: dict, upload_id: Optional[str], index: int,
                     edit: Optional[dict], user_id: int, db):
    from . import upload_queue as queue
    folder = Path(FILE_STORAGE_ROOT) / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    meta = item.get("meta") or {}
    upload = item.get("upload")
    name = Path(str(meta.get("name") or (upload.filename if upload else "") or "file")).name
    if item.get("token"):
        row = blobs.consume_token(db, user_id, item["token"])
        if row is None:
            raise HTTPException(status_code=409, detail="Файл нужно отправить заново")
        stored, info = blobs.clone_into(db, row, folder, name, user_id)
        queue.update(upload_id, index, "done", 100, "Готово")
        return stored, info
    thumb = await _read_thumb(item.get("thumb"))
    packed = None
    enc = str(meta.get("enc") or "")
    queue.update(upload_id, index, "receiving", 5, "Приём файла")
    if item.get("session"):
        store = chunk_store()
        try:
            data_path, smeta = store.take(user_id, item["session"])
        except ChunkError as e:
            raise HTTPException(status_code=e.status, detail=e.detail)
        temp = folder / f".{uuid.uuid4().hex}{Path(name).suffix}.uploading"
        shutil.move(str(data_path), str(temp))
        store.release(item["session"])
    else:
        temp = await _to_temp(upload, folder, name)
    started = time.time()
    cpu = 0.0
    try:
        # крупное — в отдельный процесс одной передачей, мелкое — прямо здесь в потоке
        run = workers.run if temp.stat().st_size > QUICK_BYTES else workers.run_here
        if enc == "gzip":
            # устройство прислало gzip: проверяем по распакованной копии, храним присланное
            try:
                plain, used = await run(workers.inflate_to_temp, temp, int(meta.get("size") or 0))
                cpu += used
            except ValueError as e:
                raise HTTPException(status_code=422, detail=str(e))
            packed, temp = temp, plain
        prep = str(meta.get("prep") or "")
        original = prep not in ("image", "device")
        (verdict, digest), used = await run(workers.inspect, temp, name, original)
        cpu += used
        if verdict.outcome == "reject":
            file_guard.journal(verdict)
            raise HTTPException(status_code=422, detail=verdict.message or "Файл не принят")
        source = None
        if original:
            source = digest
            reuse = blobs.candidates(db, source["tree"], source["size"], user_id) if source["size"] >= blobs.MIN_SIZE else []
            if reuse:
                stored, info = blobs.clone_into(db, reuse[0], folder, name, user_id)
                queue.update(upload_id, index, "done", 100, "Готово")
                return stored, info
        else:
            source = {"tree": str(meta.get("tree") or "")[:64] or None, "size": meta.get("source_size"),
                      "sample": str(meta.get("sample") or "")[:64].lower() or None}
        queue.update(upload_id, index, "queued", 10)
        effective_edit = {} if meta.get("edits_applied") else (edit or {})
        kind = verdict.kind
        if kind == "video":
            def tick(pct, _i=index):
                queue.update(upload_id, _i, "processing", pct, "Подготовка видео")
            slack = 1.6 if prep == "device" else None
            # kind="video": место выбирает сама обработка — лёгкое для готового видео, тяжёлое для перекодирования
            stored = await queue.run_processing(upload_id, index, media.store_video_from_temp, folder, name, temp,
                                                effective_edit, tick, slack, _poster_usable(thumb),
                                                video_origin(meta), note="Подготовка видео", kind="video")
        elif kind == "image":
            if verdict.outcome == "isolate":
                stored = await queue.run_processing(upload_id, index, _isolated_image, folder, name, temp, verdict,
                                                    note="Сжатие фото")
            elif prep == "image" and temp.stat().st_size <= QUICK_BYTES:
                # фото уже сжато на телефоне: только сохранить, очередь тяжёлой обработки не нужна
                stored, used = await workers.run_here(workers.store_image_path, folder, name, temp, bool(thumb))
                cpu += used
            else:
                stored = await queue.run_processing(upload_id, index, lambda: media.store_image(folder, name, temp.read_bytes(),
                                                                                                 thumb_ready=bool(thumb)),
                                                    note="Сжатие фото")
        elif packed is not None:
            stored = await asyncio.to_thread(media.store_document_packed, folder, name, packed, temp.stat().st_size)
        elif temp.stat().st_size <= QUICK_BYTES * 4:
            stored, used = await workers.run_here(workers.store_document_path, folder, name, temp, enc != "identity")
            cpu += used
        else:
            stored = await queue.run_processing(upload_id, index, functools.partial(media.store_document_path, compress=enc != "identity"),
                                                folder, name, temp, note="Сохранение")
        # у видео своя обложка сервера есть, только если присланная не годилась (почти чёрный кадр) или её не было
        own_poster = stored.kind == "video" and stored.thumb_path
        if thumb and not own_poster and (stored.kind in ("image", "video") or name.lower().endswith(".pdf")):
            try:
                await asyncio.to_thread(_write_thumb, stored, thumb)
            except Exception:
                logger.warning("Миниатюра «%s» не сохранена", name, exc_info=True)
        info = blobs.clean_meta(meta)
        blobs.register(db, stored, source=source, meta=info, uploader_id=user_id, verified=original)
        file_guard.journal(verdict, {"cpu": round(cpu, 3), "wall": round(time.time() - started, 2)})
        queue.update(upload_id, index, "done", 100, "Готово")
        return stored, info
    except HTTPException:
        queue.update(upload_id, index, "error", None, "Ошибка")
        raise
    except Exception as e:
        queue.update(upload_id, index, "error", None, "Ошибка")
        logger.exception("Файл «%s» не обработан: %s", name, e)
        raise HTTPException(status_code=422, detail="Не удалось обработать файл «%s»" % name)
    finally:
        for leftover in (temp, packed):
            try:
                if leftover is not None and leftover.exists():
                    leftover.unlink()
            except Exception:
                pass


def apply_meta(obj, info: Optional[dict]):
    for k, v in (info or {}).items():
        try:
            setattr(obj, k, v)
        except Exception:
            pass
    return obj


# ── Совместимость со старыми обработчиками: сеанс выглядит как обычный файл ──
QUICK_BYTES = 2 * 1024 * 1024


class LocalUpload:
    """Файл, уже лежащий на диске (собран из частей или взят копией), с теми же полями, что у UploadFile.
    inflate — на диске gzip-обёртка хранилища (имя.расширение.gz): читается исходный файл."""
    def __init__(self, path: Path, filename: str, cleanup: bool, session: Optional[str] = None, inflate: bool = False):
        self.path = Path(path)
        self.filename = filename
        self.cleanup = cleanup
        self.session = session
        self.inflate = inflate
        plain = gzip_tools.original_size(self.path) if inflate else None
        self.size = plain if plain is not None else self.path.stat().st_size
        self.content_type = mimetypes.guess_type(filename or "")[0] or "application/octet-stream"
        self.headers = {}
        self._fh = None

    async def read(self, n: int = -1) -> bytes:
        if self._fh is None:
            self._fh = gzip.open(self.path, "rb") if self.inflate else open(self.path, "rb")
        return await asyncio.to_thread(self._fh.read, n)

    def close(self):
        try:
            if self._fh:
                self._fh.close()
        except Exception:
            pass
        if self.session:
            chunk_store().release(self.session)
        elif self.cleanup:
            try:
                self.path.unlink()
            except Exception:
                pass


def _with_meta(upload, meta: dict):
    # описание от телефона (отпечаток и образец исходника) — при файле: по нему чат записывает его в реестр копий
    try:
        upload.kov_meta = meta
    except Exception:
        pass
    return upload


async def as_uploads(items: list, user_id: int, db) -> list:
    out = []
    for item in items:
        meta = item.get("meta") or {}
        if item.get("upload") is not None:
            out.append(_with_meta(item["upload"], meta))
            continue
        name = Path(str(meta.get("name") or "file")).name
        if item.get("session"):
            try:
                data_path, session = chunk_store().take(user_id, item["session"])
            except ChunkError as e:
                release_uploads(out)
                raise HTTPException(status_code=e.status, detail=e.detail)
            if not meta.get("tree") and session.get("tree") and meta.get("prep") not in ("image", "device"):
                # исходник пришёл кусками без отпечатка от клиента: отпечаток всего файла уже посчитан при сборке
                meta = dict(meta, tree=session["tree"], source_size=session.get("size"))
            out.append(_with_meta(LocalUpload(data_path, name, cleanup=False, session=item["session"]), meta))
        elif item.get("token"):
            row = blobs.consume_token(db, user_id, item["token"])
            if row is None:
                release_uploads(out)
                raise HTTPException(status_code=409, detail="Файл нужно отправить заново")
            src = Path(FILE_STORAGE_ROOT) / row.stored_path
            # документ хранится обёрнутым в gzip: чату нужен сам файл — иначе он сжал бы его второй раз,
            # и при скачивании вместо файла приходил бы gzip (повтор файла в чат был на 983 байта больше)
            inflate = _storage_gzip(src, getattr(row, "extension", "") or "")
            out.append(_with_meta(LocalUpload(src, name, cleanup=False, inflate=inflate), meta))
    return out


def _storage_gzip(path: Path, ext: str) -> bool:
    """gzip-обёртка хранилища: <uuid>.<расширение>.gz, у документа без расширения — <uuid>.gz.
    Свой .gz пользователя хранится как есть (<uuid>.gz, расширение файла gz) — его не распаковываем."""
    if path.suffix.lower() != ".gz":
        return False
    if len(path.suffixes) < 2 and ext.lower().lstrip(".") == "gz":
        return False
    try:
        with open(path, "rb") as fh:
            return fh.read(2) == b"\x1f\x8b"
    except OSError:
        return False


def release_uploads(uploads) -> None:
    for u in uploads or []:
        if isinstance(u, LocalUpload):
            u.close()
