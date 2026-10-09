# -*- coding: utf-8 -*-
"""Значки Kovapp для Windows: программы (app_icon.ico/.png) и установщика (installer_icon.ico).

Знак «Контур» плиткой во весь квадрат картинки, углы — прозрачные: плотная тёмная плитка,
белая K и янтарная дуга (как значок приложения Android). Каждый размер рисуется отдельно
из векторов, у мелких (16–24 px) линии чуть толще — чтобы знак читался в трее и в списках.
У установщика в правом нижнем углу — янтарный кружок со стрелкой «скачать».

Запуск из папки pc (нужен PySide6): python tools/make_icons.py
Готовые файлы уже лежат в images — запускать, только если поменялся знак.
"""

import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

APP = QGuiApplication.instance() or QGuiApplication(sys.argv)

INK_TOP, INK_BOTTOM = "#1E3C46", "#0F232A"      # плитка: знак #132A32 с лёгким объёмом сверху вниз
KNOCK, ACCENT = "#F5F7F6", "#F5B301"
SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
RING = "M98 16A26 26 0 0 1 124 42"              # янтарная дуга (kov/icons.py: _CORNER)
KAY = "M95 38L73 60L95 82"


def tile_svg(n, badge=False):
    small = n <= 24
    stem, arc = (13.0, 10.0) if small else (11.0, 8.0)
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="32 12 96 96">',
        '<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="%s"/><stop offset="1" stop-color="%s"/></linearGradient></defs>' % (INK_TOP, INK_BOTTOM),
        '<rect x="32" y="12" width="96" height="96" rx="30" fill="url(#g)"/>',
        # тонкий светлый край — плитка не теряется на тёмных обоях
        '<rect x="32.6" y="12.6" width="94.8" height="94.8" rx="29.4" fill="none" stroke="#FFFFFF" stroke-opacity="%s" '
        'stroke-width="1.2"/>' % ("0" if small else "0.10"),
        '<path fill="none" stroke="%s" stroke-width="%s" stroke-linecap="round" d="%s"/>' % (ACCENT, arc, RING),
        '<rect fill="%s" x="%s" y="36" width="%s" height="48" rx="%s"/>' % (KNOCK, 64.5 - stem / 2, stem, stem / 2),
        '<path fill="none" stroke="%s" stroke-width="%s" stroke-linecap="round" stroke-linejoin="round" d="%s"/>'
        % (KNOCK, stem, KAY),
    ]
    if badge and n >= 32:
        # кружок «установить»: янтарный, со стрелкой вниз, отделён от плитки её цветом
        parts += [
            '<circle cx="111" cy="91" r="16.5" fill="%s"/>' % INK_BOTTOM,
            '<circle cx="111" cy="91" r="13.5" fill="%s"/>' % ACCENT,
            '<path fill="none" stroke="%s" stroke-width="3.6" stroke-linecap="round" stroke-linejoin="round" '
            'd="M111 83.5V96.5M105.5 91.5L111 97L116.5 91.5"/>' % INK_BOTTOM,
        ]
    parts.append("</svg>")
    return "".join(parts)


def render(n, badge=False):
    img = QImage(n, n, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    QSvgRenderer(QByteArray(tile_svg(n, badge).encode("utf-8"))).render(p, QRectF(0, 0, n, n))
    p.end()
    return img


def png_bytes(img):
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def write_ico(path, images):
    """ICO с картинками PNG внутри (Windows Vista и новее читает так все размеры)."""
    blobs = [png_bytes(img) for img in images]
    head = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    for img, blob in zip(images, blobs):
        w = img.width()
        entries += struct.pack("<BBBBHHII", 0 if w >= 256 else w, 0 if w >= 256 else w, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    with open(path, "wb") as f:
        f.write(head + entries + b"".join(blobs))


def main():
    out = os.path.join(ROOT, "images")
    os.makedirs(out, exist_ok=True)
    write_ico(os.path.join(out, "app_icon.ico"), [render(n) for n in SIZES])
    write_ico(os.path.join(out, "installer_icon.ico"), [render(n, badge=True) for n in SIZES])
    render(256).save(os.path.join(out, "app_icon.png"))
    render(512).save(os.path.join(out, "app_icon_512.png"))
    print("Готово:", out)


if __name__ == "__main__":
    main()
