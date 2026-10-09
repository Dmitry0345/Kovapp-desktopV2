# -*- coding: utf-8 -*-

from typing import List, Optional, Dict, Literal
import base64
from sqlalchemy.orm import Session
from pathlib import Path
from sqlalchemy import desc, text, func, true, false

from .config import logger


_IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)


def _detect_image_mime(data: bytes) -> str:
    """Определяет тип изображения по сигнатуре файла."""
    for signature, mime in _IMAGE_SIGNATURES:
        if data.startswith(signature):
            return mime
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def _decode_photo_value(value) -> Optional[bytes]:
    """Приводит значение колонки `Photo` к байтам изображения.

    Поддерживаются три способа хранения:
      * BLOB с самим изображением;
      * строка base64 (в том числе data-URI);
      * путь к файлу на диске сервера.
    """
    if value is None:
        return None

    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, bytearray):
        value = bytes(value)

    if isinstance(value, bytes):
        if not value:
            return None
        # Уже готовое изображение
        if _detect_image_mime(value) != "application/octet-stream":
            return value
        # BLOB может хранить base64 или путь в текстовом виде
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return value

    if isinstance(value, str):
        text_value = value.strip()
        if not text_value:
            return None

        if text_value.startswith("data:"):
            _, _, tail = text_value.partition(",")
            text_value = tail.strip()
        else:
            try:
                candidate = Path(text_value)
                if candidate.is_file():
                    return candidate.read_bytes()
            except OSError as e:
                logger.debug("Фото: путь не разобрался: %s", e)

        try:
            decoded = base64.b64decode(text_value, validate=False)
        except Exception:
            return None
        return decoded or None

    return None


def _load_employee_photo(db: Session, employee_id: int) -> Optional[bytes]:
    row = db.execute(
        text("SELECT `Photo` FROM employees WHERE id = :employee_id"),
        {"employee_id": employee_id},
    ).first()
    if row is None:
        return None
    return _decode_photo_value(row[0])


def employee_photo_url(employee_id) -> Optional[str]:
    """Адрес фотографии сотрудника для клиента."""
    if employee_id is None:
        return None
    try:
        return f"/employees/{int(employee_id)}/photo"
    except (TypeError, ValueError):
        return None


def employee_ids_with_photo(db: Session, employee_ids) -> set:
    """
    Множество id сотрудников, у которых заполнена колонка `Photo`.

    Один запрос на весь список: без него клиент получал бы адрес фотографии
    для каждого сотрудника и бомбил сервер запросами, отдающими 404.
    """
    ids = set()
    for value in employee_ids or []:
        try:
            ids.add(int(value))
        except (TypeError, ValueError):
            continue

    if not ids:
        return set()

    id_list = ",".join(str(i) for i in ids)
    try:
        rows = db.execute(text(
            f"SELECT id FROM employees "
            f"WHERE `Photo` IS NOT NULL AND LENGTH(`Photo`) > 0 AND id IN ({id_list})"
        )).all()
        return {int(r[0]) for r in rows}
    except Exception as e:
        logger.warning(f"Не удалось определить наличие фотографий сотрудников: {e}")
        return set()


def employee_avatar_url(db: Session, employee_id) -> Optional[str]:
    """Адрес фотографии одного сотрудника или None, если фотографии нет."""
    if employee_id is None:
        return None
    try:
        employee_id = int(employee_id)
    except (TypeError, ValueError):
        return None
    if employee_id in employee_ids_with_photo(db, [employee_id]):
        return employee_photo_url(employee_id)
    return None


_PHOTO_COLUMN_INFO = None


MAX_PHOTO_BYTES = 8 * 1024 * 1024


def _photo_column_info(db: Session):
    """(колонка двоичная?, предельная длина) для employees.Photo."""
    global _PHOTO_COLUMN_INFO
    if _PHOTO_COLUMN_INFO is not None:
        return _PHOTO_COLUMN_INFO

    is_binary, max_length = True, None
    try:
        row = db.execute(text("""
            SELECT DATA_TYPE, CHARACTER_MAXIMUM_LENGTH
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'employees'
              AND COLUMN_NAME = 'Photo'
        """)).first()
        if row is not None:
            data_type = str(row[0] or "").lower()
            is_binary = data_type in (
                "blob", "tinyblob", "mediumblob", "longblob", "binary", "varbinary"
            )
            max_length = int(row[1]) if row[1] is not None else None
    except Exception as e:
        logger.warning(f"Не удалось определить тип колонки Photo: {e}")

    _PHOTO_COLUMN_INFO = (is_binary, max_length)
    return _PHOTO_COLUMN_INFO
