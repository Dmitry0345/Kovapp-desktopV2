# -*- coding: utf-8 -*-
"""Фото перед отправкой — как js/media/image-prep.js веб-версии.

Поворот по EXIF, длинная сторона до 3200, WebP качества 80; миниатюра
плитки 320 JPEG качества 60 и заглушка ThumbHash. Без пересжатия уходят:
SVG, GIF (может быть анимацией), огромные (больше 100 Мп), уже компактные
(до 300 КБ или не больше 0,35 байта на точку у JPEG/WebP) и те, что после
сжатия не стали меньше хотя бы на 5 %.
"""

import io
import os
import uuid

from kov.media import thumbhash

MAX_SIDE = 3200
THUMB_SIDE = 320
WEBP_Q = 80
WEBP_METHOD = 4
THUMB_Q = 60
PIXEL_LIMIT = 100e6
COMPACT_BYTES = 300 * 1024
LIGHT_BPP = 0.35

try:
    from PIL import Image, ImageOps
    PIL_OK = True
except Exception:  # без Pillow фото уходят как есть, сервер сожмёт сам
    Image = ImageOps = None
    PIL_OK = False


def fit(w, h, side):
    if max(w, h) <= side:
        return w, h
    r = side / float(max(w, h))
    return max(1, int(round(w * r))), max(1, int(round(h * r)))


def renamed(name, ext):
    base = os.path.splitext(os.path.basename(name or "photo"))[0] or "photo"
    return base + ext


def _orientation(img):
    try:
        return int(img.getexif().get(0x0112) or 1)
    except Exception:
        return 1


def placeholder(img):
    """(base64 ThumbHash, есть ли прозрачность) по уменьшенной до 100 копии."""
    w, h = fit(img.width, img.height, 100)
    small = img.convert("RGBA").resize((w, h), Image.BILINEAR)
    data = small.tobytes()
    alpha = any(data[i] < 250 for i in range(3, len(data), 4))
    return thumbhash.to_base64(thumbhash.encode(w, h, data)), alpha


def thumbnail(img):
    """Миниатюра плитки: JPEG на белом, длинная сторона до 320."""
    w, h = fit(img.width, img.height, THUMB_SIDE)
    src = img.convert("RGBA").resize((w, h), Image.BILINEAR)
    flat = Image.new("RGB", (w, h), (255, 255, 255))
    flat.paste(src, mask=src.split()[3])
    buf = io.BytesIO()
    flat.save(buf, "JPEG", quality=THUMB_Q)
    return buf.getvalue(), w, h


def thumbs_from_bytes(data):
    """Заглушка и миниатюра из готового кадра (обложка видео)."""
    if not PIL_OK or not data:
        return None, None
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        ph, _ = placeholder(img)
        th, _, _ = thumbnail(img)
        return ph, th
    except Exception:
        return None, None


def prepare(path, name=None, out_dir=None):
    """Возвращает словарь: path (что отправлять), name, changed, width, height,
    thumb (JPEG-байты), thumb_hash, reason."""
    name = name or os.path.basename(path)
    res = {"path": path, "name": name, "changed": False, "width": 0, "height": 0,
           "thumb": None, "thumb_hash": "", "reason": ""}
    low = name.lower()
    if low.endswith(".svg"):
        res["reason"] = "svg"
        return res
    if not PIL_OK:
        res["reason"] = "decode"
        return res
    size = os.path.getsize(path)
    try:
        img = Image.open(path)
        fmt = (img.format or "").upper()
        w0, h0 = img.size
    except Exception:
        res["reason"] = "decode"
        return res
    if w0 * h0 > PIXEL_LIMIT:
        res["reason"] = "too-large"
        return res
    turned = _orientation(img) >= 5
    natural = (h0, w0) if turned else (w0, h0)
    target = fit(natural[0], natural[1], MAX_SIDE)
    shrink = target != natural
    scaled = False
    try:
        if shrink and fmt == "JPEG":
            # быстрая распаковка сразу в меньшем размере (как у сервера)
            want = (target[1], target[0]) if turned else target
            img.draft("RGB", want)
            scaled = img.size != (w0, h0)
        img.load()
        img = ImageOps.exif_transpose(img)
    except Exception:
        res["reason"] = "decode"
        return res
    if img.width * img.height > PIXEL_LIMIT:
        res["reason"] = "too-large"
        return res
    try:
        res["thumb_hash"], alpha = placeholder(img)
    except Exception:
        alpha = img.mode in ("RGBA", "LA", "PA")
    try:
        res["thumb"], res["thumb_w"], res["thumb_h"] = thumbnail(img)
    except Exception:
        res["thumb"] = None
    w, h = fit(img.width, img.height, MAX_SIDE)
    res["width"], res["height"] = w, h
    if fmt == "GIF":
        res["width"], res["height"] = img.width, img.height
        res["reason"] = "gif"
        return res
    resized = (w, h) != (img.width, img.height) or scaled
    light = fmt in ("JPEG", "WEBP") and size / float(max(1, img.width * img.height)) <= LIGHT_BPP
    if not resized and (size <= COMPACT_BYTES or light):
        res["width"], res["height"] = img.width, img.height
        res["reason"] = "already-compact"
        return res
    try:
        frame = img
        if (w, h) != (img.width, img.height):
            frame = img.resize((w, h), Image.LANCZOS)
        if alpha:
            frame = frame.convert("RGBA")
        elif frame.mode not in ("RGB", "L"):
            frame = frame.convert("RGB")
        buf = io.BytesIO()
        frame.save(buf, "WEBP", quality=WEBP_Q, method=WEBP_METHOD)
        blob = buf.getvalue()
    except Exception:
        res["reason"] = "encode"
        return res
    if not resized and len(blob) >= size * 0.95:
        res["width"], res["height"] = img.width, img.height
        res["reason"] = "not-smaller"
        return res
    out_name = renamed(name, ".webp")
    folder = out_dir or os.path.dirname(path)
    out_path = os.path.join(folder, "prep-%s-%s" % (uuid.uuid4().hex[:8], out_name))
    with open(out_path, "wb") as f:
        f.write(blob)
    res.update(path=out_path, name=out_name, changed=True)
    return res
