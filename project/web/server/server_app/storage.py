# -*- coding: utf-8 -*-

import subprocess
import re
import os
import mimetypes
import gzip
from urllib.parse import quote as _quote

from . import gzip_tools
import tempfile
import threading
import logging
from urllib.parse import quote
from fastapi import FastAPI, Depends, HTTPException, status, Query, Response, UploadFile, File, Form, WebSocket, \
    WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from pathlib import Path
from typing import Optional
import shutil
import uuid
import json
from fastapi import Request

from .config import FILE_STORAGE_ROOT


logger = logging.getLogger(__name__)
VIDEO_EXTENSIONS = frozenset({
    ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".mkv", ".ts",
})


def is_video_filename(filename: str) -> bool:
    return Path(str(filename or "")).suffix.lower() in VIDEO_EXTENSIONS


def get_task_folder_name(task_id: int, task_name: str) -> str:
    safe_name = re.sub(r'[<>:"/\\|?*]', '_', task_name.strip())
    return f"{safe_name}-{task_id}"


def save_compressed_file_to_disk(task_id: int, task_name: str, original_filename: str, compressed_content: bytes) -> \
tuple[str, str]:
    task_folder = get_task_folder_name(task_id, task_name)
    storage_path = Path(FILE_STORAGE_ROOT) / task_folder
    storage_path.mkdir(parents=True, exist_ok=True)

    file_ext = Path(original_filename).suffix.lower()
    unique_filename = f"{uuid.uuid4()}{file_ext}.gz"
    file_path = storage_path / unique_filename

    with open(file_path, "wb") as buffer:
        buffer.write(compressed_content)

    return str(file_path.relative_to(FILE_STORAGE_ROOT)), file_ext[1:] if file_ext else ""


def _storage_path(filepath: str) -> Path:
    root = Path(FILE_STORAGE_ROOT).resolve()
    raw_path = Path(str(filepath or ""))
    full_path = raw_path.resolve() if raw_path.is_absolute() else (root / raw_path).resolve()
    for allowed in _storage_roots():
        try:
            full_path.relative_to(allowed)
            return full_path
        except ValueError:
            continue
    raise ValueError("Путь к файлу выходит за пределы хранилища")


def _stored_file_candidates(filepath: str) -> list[Path]:
    full_path = _storage_path(filepath)
    candidates = [full_path]
    if full_path.suffix.lower() == ".gz":
        candidates.append(full_path.with_suffix(""))
    else:
        candidates.append(Path(f"{full_path}.gz"))
    # Миниатюра лежит рядом и должна уходить вместе с файлом
    base = full_path.with_suffix("") if full_path.suffix.lower() == ".gz" else full_path
    candidates.append(base.with_name(base.name + ".thumb.jpg"))
    return list(dict.fromkeys(candidates))


# ── предпросмотр документов ──────────────────────────────────────────────
# LibreOffice на сервере больше не используется (beta 3): PDF, Word и PowerPoint
# показывает устройство пользователя, старые форматы (doc, odt, rtf, ppt, odp)
# скачиваются. Функции оставлены, чтобы прежние адреса отвечали «нет предпросмотра».
OFFICE_EXTENSIONS = frozenset({
    ".doc", ".docx", ".odt", ".rtf", ".xls", ".xlsx", ".ods", ".csv",
    ".ppt", ".pptx", ".odp",
})


def office_converter(refresh: bool = False) -> Optional[str]:
    return None


def office_status() -> dict:
    """Состояние предпросмотра — для диагностики через /health/office."""
    return {"available": False, "disabled": True, "extensions": sorted(OFFICE_EXTENSIONS),
            "hint": "Не нужен: документы показывает устройство, старые форматы скачиваются"}


def is_office_name(name: str) -> bool:
    return Path(str(name or "")).suffix.lower() in OFFICE_EXTENSIONS


def office_preview_pdf(stored: Path, display_name: str) -> Optional[Path]:
    return None


def _storage_roots() -> list:
    """Основное хранилище и дополнительные папки со старыми файлами."""
    from . import config as _cfg
    roots = [Path(FILE_STORAGE_ROOT).resolve()]
    for extra in list(getattr(_cfg, "FILE_STORAGE_FALLBACKS", []) or []):
        try:
            p = Path(extra).resolve()
        except OSError:
            continue
        if p not in roots:
            roots.append(p)
    return roots


def _existing_variant(full_path: Path):
    """Файл как есть или его вариант с .gz / без .gz, если такой есть на диске."""
    if full_path.is_file():
        return full_path
    alt = full_path.with_suffix("") if full_path.suffix.lower() == ".gz" else Path(f"{full_path}.gz")
    return alt if alt.is_file() else None


def _relative_parts(filepath) -> list:
    """Путь внутри хранилища: для старых абсолютных путей — часть после file_storage."""
    raw = str(filepath or "")
    parts = [p for p in raw.replace("\\", "/").split("/") if p and p != "."]
    low = [p.lower() for p in parts]
    if "file_storage" in low:
        parts = parts[len(low) - low[::-1].index("file_storage"):]
    elif parts and (re.match(r"^[A-Za-z]:$", parts[0]) or raw.startswith(("/", "\\"))):
        try:
            parts = list(Path(raw).resolve().relative_to(Path(FILE_STORAGE_ROOT).resolve()).parts)
        except ValueError:
            return []
    return [] if ".." in parts else parts


def _fallback_variants(filepath) -> list:
    """Тот же файл в дополнительных папках хранилища."""
    roots = _storage_roots()
    parts = _relative_parts(filepath) if len(roots) > 1 else []
    return [root.joinpath(*parts) for root in roots[1:]] if parts else []


def find_storage_roots(missing, bases=None, budget: float = 5.0) -> list:
    """Где лежат файлы из базы, которых нет в хранилище: [(папка, совпадений)].

    Ищет по именам в папке file_storage в корне диска, рядом с сервером
    и в дереве папок проекта — не дольше budget секунд.
    """
    import time
    wanted = {}
    for value in missing:
        parts = _relative_parts(value) or [p for p in str(value or "").replace("\\", "/").split("/") if p][-1:]
        if not parts:
            continue
        rel = "/".join(parts)
        rel = rel[:-3] if rel.lower().endswith(".gz") else rel
        wanted.setdefault(rel.rsplit("/", 1)[-1].lower(), []).append(rel.lower())
    if not wanted:
        return []
    here = Path(__file__).resolve().parent.parent
    ups = list(here.parents)
    if bases is None:
        bases = [Path(os.path.abspath(os.sep)) / "file_storage"] + [p / "file_storage" for p in ups[:4]]
        bases.append(ups[2] if len(ups) > 2 else here)
    main = Path(FILE_STORAGE_ROOT).resolve()
    skip = {"node_modules", ".git", "__pycache__", "$recycle.bin", "windows", "program files",
            "program files (x86)", "appdata", "programdata"}
    hits, seen, deadline = {}, set(), time.monotonic() + budget
    for base in bases:
        if not Path(base).is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            if time.monotonic() > deadline:
                break
            dirnames[:] = [d for d in dirnames if d.lower() not in skip]
            if dirpath in seen:
                dirnames[:] = []
                continue
            seen.add(dirpath)
            for fn in filenames:
                key = (fn[:-3] if fn.lower().endswith(".gz") else fn).lower()
                if key not in wanted:
                    continue
                full = (Path(dirpath) / fn).as_posix()
                full = full[:-3] if full.lower().endswith(".gz") else full
                for rel in wanted[key]:
                    if full.lower().endswith("/" + rel):
                        root = Path(full[: len(full) - len(rel) - 1])
                        if root.resolve() != main:
                            hits[str(root)] = hits.get(str(root), 0) + 1
    return sorted(hits.items(), key=lambda kv: -kv[1])


def storage_relative(full_path) -> Path:
    """Путь файла относительно той папки хранилища, где он лежит."""
    for root in _storage_roots():
        try:
            return Path(full_path).resolve().relative_to(root)
        except ValueError:
            continue
    raise ValueError("Путь к файлу выходит за пределы хранилища")


def resolve_stored_path(filepath: str) -> Path:
    """Путь к реальному файлу хранилища.

    С версии 2 фото и уже сжатые документы лежат без .gz, а старые файлы —
    с .gz. Клиенты чата дописывают .gz к адресу картинки, десктоп берёт путь
    из БД как есть, поэтому принимаем оба варианта и отдаём тот, что существует.
    """
    try:
        full_path = _storage_path(filepath)
    except ValueError:
        # старый абсолютный путь вне хранилища: тот же файл в дополнительных папках
        for other in _fallback_variants(filepath):
            found = _existing_variant(other)
            if found is not None:
                return found
        raise
    found = _existing_variant(full_path)
    if found is not None:
        return found
    for other in _fallback_variants(filepath):
        found = _existing_variant(other)
        if found is not None:
            return found
    return full_path


def office_preview_response(filepath: str, filename: str):
    """Готовый ответ с PDF-предпросмотром документа или None."""
    if not is_office_name(filename):
        return None
    try:
        stored = resolve_stored_path(filepath)
    except ValueError:
        return None
    if not stored.is_file():
        return None
    made = office_preview_pdf(stored, filename)
    if not made:
        return None
    shown = Path(filename).stem + ".pdf"
    return FileResponse(
        str(made), media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(shown, safe='')}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=600",
        },
    )


def _remove_empty_parent(path: Path) -> None:
    root = Path(FILE_STORAGE_ROOT).resolve()
    parent = path.parent
    if parent != root and parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()


def delete_file_from_disk(filepath: str) -> bool:
    removed = False
    try:
        for candidate in _stored_file_candidates(filepath):
            if candidate.is_file():
                candidate.unlink()
                removed = True
                _remove_empty_parent(candidate)
        return removed
    except Exception:
        logger.exception("Не удалось удалить файл из хранилища: %s", filepath)
        return False


def quarantine_files_for_deletion(filepaths: list[str]) -> list[tuple[Path, Path]]:
    moves: list[tuple[Path, Path]] = []
    seen: set[Path] = set()
    try:
        for filepath in filepaths:
            for source in _stored_file_candidates(filepath):
                if source in seen or not source.is_file():
                    continue
                seen.add(source)
                target = source.with_name(f".{source.name}.{uuid.uuid4().hex}.deleting")
                source.replace(target)
                moves.append((source, target))
    except Exception:
        restore_quarantined_files(moves)
        raise
    return moves


def restore_quarantined_files(moves: list[tuple[Path, Path]]) -> None:
    for source, target in reversed(moves):
        try:
            if target.exists() and not source.exists():
                target.replace(source)
        except Exception:
            logger.exception("Не удалось восстановить файл после отката: %s", source)


def purge_quarantined_files(moves: list[tuple[Path, Path]]) -> bool:
    success = True
    for _, target in moves:
        try:
            if target.exists():
                target.unlink()
            _remove_empty_parent(target)
        except Exception:
            success = False
            logger.exception("Не удалось окончательно удалить файл: %s", target)
    _forget_blobs(source for source, _ in moves)  # запись file_blobs уходит вместе с файлом
    return success


def _forget_blobs(paths) -> None:
    try:
        from . import blobs
        blobs.forget(paths=list(paths))
    except Exception:
        logger.debug("file_blobs: записи не удалены", exc_info=True)


def delete_stored_file(filepath) -> bool:
    """Стирает файл хранилища с вариантом .gz, миниатюрой и записью в file_blobs."""
    rel = str(filepath or "").replace("\\", "/").lstrip("/")
    if rel.lower().startswith("file_storage/"):
        rel = rel[len("file_storage/"):]
    if not rel:
        return False
    removed = delete_file_from_disk(rel)
    _forget_blobs([rel])
    return removed


# Типы, которые браузеру можно показывать прямо на странице. Всё остальное
# уходит вложением: файл с расширением .html, .svg или .xml, показанный
# встроенно, выполняется как страница на нашем же адресе — а там лежит токен
# сотрудника. Список намеренно короткий и белый: что не перечислено — качаем.
INLINE_SAFE_TYPES = frozenset({
    "image/jpeg", "image/png", "image/gif", "image/webp", "image/avif", "image/bmp",
    "video/mp4", "video/webm", "video/ogg",
    "audio/mpeg", "audio/ogg", "audio/wav", "audio/webm", "audio/mp4",
    "application/pdf", "text/plain",
})


def safe_content_type(filename: str) -> str:
    """Тип для заголовка: неизвестное и опасное отдаём как поток байтов."""
    guessed = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return guessed if guessed in INLINE_SAFE_TYPES else "application/octet-stream"


def disposition_for(filename: str, download: bool) -> str:
    """Встроенно показываем только заведомо безопасные типы."""
    inline = (not download) and safe_content_type(filename) in INLINE_SAFE_TYPES
    return f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(filename, safe='')}"


def _encoded_file_response(path: Path, filename: str, content_type: str, download: bool) -> Response:
    """Хранимый gzip уходит как есть: распаковывает браузер, процессор сервера не занят."""
    ascii_name = filename.encode("ascii", "ignore").decode().replace('"', "") or "file"
    headers = {
        "Content-Encoding": "gzip",
        "Vary": "Accept-Encoding",
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "Content-Disposition": "%s; filename=\"%s\"; filename*=UTF-8''%s" % (
            "attachment" if download else "inline", ascii_name, _quote(filename)),
    }
    size = gzip_tools.original_size(path)
    if size is not None:
        headers["X-Original-Length"] = str(size)
    return FileResponse(str(path), media_type=content_type, headers=headers)


def get_decompressed_file_response_with_range(request: Request, filepath: str, filename: str, download: bool = False,
                                              file_storage_root: str = None) -> Response:
    # Handle preflight OPTIONS request
    if request.method == "OPTIONS":
        return Response(status_code=200, headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
            "Access-Control-Allow-Headers": "Range, Content-Type, Authorization, token",
            "Access-Control-Expose-Headers": "Content-Range, Accept-Ranges, Content-Length",
            "Access-Control-Max-Age": "86400"
        })

    if not file_storage_root or os.path.abspath(file_storage_root) == os.path.abspath(FILE_STORAGE_ROOT):
        # общий путь: варианты с .gz и без, дополнительные папки со старыми файлами
        try:
            full_path = resolve_stored_path(filepath)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Некорректный путь к файлу") from exc
    elif Path(filepath).is_absolute():
        full_path = _storage_path(filepath)
    elif file_storage_root:
        full_path = (Path(file_storage_root).resolve() / filepath).resolve()
        try:
            full_path.relative_to(Path(file_storage_root).resolve())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Некорректный путь к файлу") from exc
        if not full_path.is_file():
            alt = full_path.with_suffix("") if full_path.suffix.lower() == ".gz" else Path(f"{full_path}.gz")
            if alt.is_file():
                full_path = alt
    else:
        full_path = resolve_stored_path(filepath)
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="Файл не найден на диске")

    try:
        # Для правильного Range Requests мы должны отдавать именно стрим
        # Чтобы не грузить всё в память, используем GzipFile для потокового чтения
        with open(full_path, "rb") as f:
            magic = f.read(2)
            is_gzip = (magic == b'\x1f\x8b')

        media_name = full_path.name if full_path.suffix.lower() in VIDEO_EXTENSIONS else filename
        content_type = safe_content_type(media_name)
        if is_gzip and gzip_tools.wants_encoded(request.query_params.get("ce"), request.headers.get("accept-encoding"),
                                                request.headers.get("range")):
            try:
                return _encoded_file_response(full_path, filename, content_type, download)
            except Exception:
                logger.exception("Выдача без распаковки не удалась, отдаю распакованным: %s", full_path)
        if media_name.lower().endswith(".mp4"):
            content_type = "video/mp4"
        elif media_name.lower().endswith(".webm"):
            content_type = "video/webm"
        elif media_name.lower().endswith(".ogg"):
            content_type = "video/ogg"

        # FORCING CORS JUST IN CASE ALTHOUGH MIDDLEWARE DOES IT
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
            "Access-Control-Allow-Headers": "Range, Content-Type, Authorization",
            "Access-Control-Expose-Headers": "Content-Range, Accept-Ranges, Content-Length",
            "Content-Disposition": disposition_for(filename, download),
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        }

        # Определяем размер
        file_size = 0
        if is_gzip:
            # Быстрый способ узнать размер gzip - прочитать последние 4 байта
            with open(full_path, 'rb') as f:
                f.seek(-4, 2)
                file_size = int.from_bytes(f.read(), 'little')
        else:
            file_size = full_path.stat().st_size

        range_header = request.headers.get("range")
        start_byte = 0
        end_byte = file_size - 1

        if range_header:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', range_header.strip())
            if match and file_size:
                sb = match.group(1)
                eb = match.group(2)

                if sb and eb:
                    # Запрос конкретного диапазона: bytes=100-500
                    start_byte = int(sb)
                    end_byte = int(eb)
                elif sb:
                    # Запрос от байта и до конца: bytes=500-
                    start_byte = int(sb)
                    end_byte = file_size - 1
                elif eb:
                    # Запрос последних N байт: bytes=-500
                    suffix_length = int(eb)
                    start_byte = max(0, file_size - suffix_length)
                    end_byte = file_size - 1

                if not sb and not eb:
                    match = None
                elif not sb and int(eb) <= 0:
                    match = None

                if match and end_byte >= file_size:
                    end_byte = file_size - 1

            if not match or start_byte >= file_size or start_byte > end_byte:
                return Response(
                    status_code=416,
                    content="Requested Range Not Satisfiable",
                    headers={"Content-Range": f"bytes */{file_size}", "Accept-Ranges": "bytes"}
                )

        chunk_size = end_byte - start_byte + 1
        headers["Content-Range"] = f"bytes {start_byte}-{end_byte}/{file_size}"
        headers["Content-Length"] = str(chunk_size)

        # Chromium checks Content-Type for WebM specifically
        if media_name.lower().endswith(".webm"):
            content_type = "video/webm"

        # Disable identity encoding logic which may break Safari/Chrome native players
        if "identity" in headers.get("Content-Encoding", ""):
            del headers["Content-Encoding"]

        def iter_file():
            with open(full_path, "rb") as f:
                if is_gzip:
                    stream = gzip.GzipFile(fileobj=f, mode='rb')
                    if start_byte > 0:
                        # Для gzip оставляем старый медленный метод
                        bytes_to_skip = start_byte
                        while bytes_to_skip > 0:
                            read_size = min(1024 * 1024, bytes_to_skip)
                            chunk = stream.read(read_size)
                            if not chunk:
                                break
                            bytes_to_skip -= len(chunk)
                else:
                    stream = f
                    if start_byte > 0:
                        stream.seek(start_byte)

                bytes_left = chunk_size
                while bytes_left > 0:
                    read_size = min(1024 * 1024, bytes_left)
                    chunk = stream.read(read_size)
                    if not chunk:
                        break
                    bytes_left -= len(chunk)
                    yield chunk

        status_code = 206 if range_header else 200
        if not range_header:
            del headers["Content-Range"]

        # VERY IMPORTANT for video streaming over FastAPI
        # Without setting Accept-Ranges to bytes, Safari/Chrome may fail
        headers["Accept-Ranges"] = "bytes"
        # Prevent GzipMiddleware from breaking the stream
        headers["Content-Encoding"] = "identity"

        return StreamingResponse(iter_file(), status_code=status_code, media_type=content_type, headers=headers)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка при обработке файла: {str(e)}")


def get_decompressed_file_response(filepath: str, filename: str, download: bool = False,
                                   request: Request = None) -> Response:
    if request:
        return get_decompressed_file_response_with_range(request, filepath, filename, download, FILE_STORAGE_ROOT)

    try:
        full_path = resolve_stored_path(filepath)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Некорректный путь к файлу") from exc
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="Файл не найден на диске")

    try:
        with open(full_path, "rb") as f:
            compressed_content = f.read()

        content = gzip.decompress(compressed_content)
    except gzip.BadGzipFile:
        with open(full_path, "rb") as f:
            content = f.read()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка при распаковке файла: {str(e)}")

    content_type = safe_content_type(filename)

    headers = {
        "Content-Disposition": disposition_for(filename, download),
        # Без этого браузер может «угадать» тип вопреки заголовку и всё-таки
        # выполнить содержимое как страницу
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
    }

    return Response(content=content, media_type=content_type, headers=headers)


def _probe_media(path: str) -> dict:
    from .media import probe_media
    return probe_media(path)


def _assert_browser_playable_webm(path: str) -> None:
    from .media import assert_browser_playable_webm
    assert_browser_playable_webm(path)


def process_video_sync(input_path: str, output_path: str, edit: Optional[dict] = None,
                       on_progress=None, origin: str = "") -> None:
    """Совместимая обёртка: сама конвертация теперь в media.py (быстрые настройки VP9).

    Третий аргумент — правки из окна отправки ({trim_start, trim_end, crop, rotate}).
    Без него вызов из чата падал с TypeError, и видео не сохранялось вовсе.
    """
    from .media import process_video_sync as _convert
    if callable(edit):        # старый вызов с колбэком — правок нет
        edit = None
    _convert(input_path, output_path, edit or {}, on_progress, None, origin)
