# -*- coding: utf-8 -*-
"""Картинки установщика: фон окна входа Kovapp (login_window._Backdrop) и фирменный знак.

Запуск из папки pc (нужен PySide6, например из .venv после build.cmd):
    .venv\\Scripts\\python installer\\make_images.py
Готовые PNG уже лежат в installer\\images — запускать нужно, только если поменялись знак или цвета.
Размеры — из справки Inno Setup 6.7 (области картинок при масштабе экрана 100–250 %).
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QByteArray, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen, QRadialGradient  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

from general_functions import theme  # noqa: E402  (до QGuiApplication: scaler ставит атрибут Qt)
from kov import icons  # noqa: E402

APP = QGuiApplication.instance() or QGuiApplication(sys.argv)

OUT = os.path.join(HERE, "images")
BACK = [(596, 432), (796, 576), (994, 720), (1193, 864), (1272, 922), (1471, 1066), (1630, 1148)]    # фон окна
WIZARD = [(202, 386), (269, 515), (336, 643), (403, 772), (430, 824), (498, 953), (534, 1022)]      # знак слева
SMALL = [58, 77, 97, 116, 124, 143, 159]                                                               # знак справа вверху
GRID = 0.75          # сетка чуть тише, чем у окна входа: за ней текст


def palette(dark):
    return theme.DARK if dark else theme.LIGHT


def backdrop(img, pal, rem):
    """Три мягких пятна и еле заметная сетка в середине — как фон окна входа."""
    w, h, r = img.width(), img.height(), rem
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.fillRect(img.rect(), QColor(pal["PAGE_BG"]))
    blobs = ((-10 * r, -12 * r, 38 * r, (0.35, 0.35), pal["LOGIN_BLOB_1"]),
             (w + 8 * r - 32 * r, h + 10 * r - 32 * r, 32 * r, (0.6, 0.4), pal["LOGIN_BLOB_2"]),
             (w * 0.82 - 22 * r, h * 0.08, 22 * r, (0.5, 0.5), pal["LOGIN_BLOB_3"]))
    p.setOpacity(pal["LOGIN_BLOB_OPACITY"])
    for x, y, d, (fx, fy), color in blobs:
        cx, cy = x + d * fx, y + d * fy
        far = d * max(fx, 1 - fx) * 2 ** 0.5
        g = QRadialGradient(QPointF(cx, cy), far * 0.7)
        c, c0 = QColor(color), QColor(color)
        c0.setAlpha(0)
        g.setColorAt(0.0, c)
        g.setColorAt(1.0, c0)
        p.fillRect(img.rect(), g)
    p.setOpacity(1.0)
    grid = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    grid.fill(Qt.transparent)
    gp = QPainter(grid)
    line = QColor(pal["LOGIN_GRID"])
    line.setAlphaF(line.alphaF() * GRID)
    gp.setPen(QPen(line, max(1.0, w / 800.0)))
    step, t = 3.5 * r, 0.0
    while t < max(w, h):
        gp.drawLine(QPointF(t, 0), QPointF(t, h))
        gp.drawLine(QPointF(0, t), QPointF(w, t))
        t += step
    gp.setCompositionMode(QPainter.CompositionMode_DestinationIn)
    gx, gy = w * 0.5, h * 0.43
    far = ((0.7 * w) ** 2 + (gy + 0.2 * h) ** 2) ** 0.5
    mask = QRadialGradient(QPointF(gx, gy), far)
    mask.setColorAt(0.0, QColor(0, 0, 0, 255))
    mask.setColorAt(0.15, QColor(0, 0, 0, 255))
    mask.setColorAt(0.68, QColor(0, 0, 0, 0))
    gp.fillRect(grid.rect(), mask)
    gp.end()
    p.drawImage(0, 0, grid)
    p.end()


def draw_svg(img, svg, rect):
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(p, rect)
    p.end()


def mark_square(ink, accent):
    """Знак «Контур» без полей: кольцо, K и янтарный угол."""
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="30 10 100 100">'
            '<path fill="none" stroke="%s" stroke-width="8" d="%s"/>'
            '<path fill="none" stroke="%s" stroke-width="8" stroke-linecap="round" d="%s"/>%s</svg>'
            % (ink, icons._RING, accent, icons._CORNER, icons._kay(ink)))


def back(dark, w, h):
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    backdrop(img, palette(dark), w / 100.0)        # пропорции окна входа: 1 rem = 1/100 ширины
    return img


def wizard(dark, w, h):
    """Знак с названием на прозрачном поле — лежит поверх фона на первой и последней странице."""
    pal = palette(dark)
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    lw = w * 0.76
    lh = lw * 120 / 370
    draw_svg(img, icons.lockup_svg(pal["MARK_INK"], pal["MARK_ACCENT"]), QRectF((w - lw) / 2, h * 0.40 - lh / 2, lw, lh))
    return img


def small(dark, n):
    pal = palette(dark)
    img = QImage(n, n, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    pad = n * 0.06
    draw_svg(img, mark_square(pal["MARK_INK"], pal["MARK_ACCENT"]), QRectF(pad, pad, n - 2 * pad, n - 2 * pad))
    return img


def main():
    os.makedirs(OUT, exist_ok=True)
    for dark in (False, True):
        tag = "dark_" if dark else ""
        for w, h in BACK:
            back(dark, w, h).save(os.path.join(OUT, "back_%s%d.png" % (tag, w)))
        for w, h in WIZARD:
            wizard(dark, w, h).save(os.path.join(OUT, "wizard_%s%d.png" % (tag, w)))
        for n in SMALL:
            small(dark, n).save(os.path.join(OUT, "small_%s%d.png" % (tag, n)))
    print("Готово:", OUT)


if __name__ == "__main__":
    main()
