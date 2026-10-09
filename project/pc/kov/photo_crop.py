# -*- coding: utf-8 -*-
"""Кадрирование снимка — как Kov.PhotoCrop сайта (js/core/photo-crop.js).

Человек двигает снимок мышью, приближает колесом, кнопками или ползунком
и сразу видит итог. Приближение 1…5: меньше единицы в кадр попали бы
пустые поля. Результат — готовый квадрат JPEG (качество 92), сторона 512:
сервер получает уже кадрированное, и снимок везде выглядит одинаково.

pick(owner, title, round_mask) → (имя файла, байты JPEG) или None.
"""

import os

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QSlider, QWidget

from general_functions import theme
from kov import icons
from kov.dialogs import Modal
from kov.ui import Button, label, rem, toast

MIN_ZOOM = 1.0
MAX_ZOOM = 5.0
OUT_SIZE = 512
MAX_INPUT = 12 * 1024 * 1024
FILTER = "Изображения (*.jpg *.jpeg *.png *.webp *.bmp *.gif *.heic *.heif);;Все файлы (*)"


class _Stage(QWidget):
    """Квадратная сцена: снимок, рамка-кружок или квадрат, перетаскивание и колесо."""

    def __init__(self, image, round_mask, on_zoom):
        super().__init__()
        self.img = image
        self.round = round_mask
        self.zoom = 1.0
        self.dx = self.dy = 0.0
        self._drag = None
        self._on_zoom = on_zoom
        side = rem(17)
        self.setFixedSize(side, side)
        self.setCursor(Qt.OpenHandCursor)

    def base(self):
        """Наименьший масштаб, при котором снимок закрывает кадр целиком."""
        return max(self.width() / self.img.width(), self.height() / self.img.height())

    def clamp(self):
        k = self.base() * self.zoom
        mx = max(0.0, (self.img.width() * k - self.width()) / 2)
        my = max(0.0, (self.img.height() * k - self.height()) / 2)
        self.dx = min(mx, max(-mx, self.dx))
        self.dy = min(my, max(-my, self.dy))

    def set_zoom(self, value, anchor=None):
        before = self.zoom
        self.zoom = min(MAX_ZOOM, max(MIN_ZOOM, value))
        if anchor is not None and before:
            k = self.zoom / before
            self.dx = anchor.x() + (self.dx - anchor.x()) * k
            self.dy = anchor.y() + (self.dy - anchor.y()) * k
        self.clamp()
        self.update()
        self._on_zoom(self.zoom)

    def reset(self):
        self.dx = self.dy = 0.0
        self.set_zoom(1.0)

    def _draw(self, p, side):
        k = self.base() * self.zoom * side / self.width()
        w, h = self.img.width() * k, self.img.height() * k
        sx = side / self.width()
        target = QRectF((side - w) / 2 + self.dx * sx, (side - h) / 2 + self.dy * sx, w, h)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.drawImage(target, self.img)

    def paintEvent(self, e):
        self.clamp()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QColor(theme.SURFACE_3))
        self._draw(p, self.width())
        r = QRectF(self.rect())
        if self.round:
            # всё вне кружка затемнено: в кружке ровно то, что увидят остальные
            path = QPainterPath()
            path.addRect(r)
            hole = QPainterPath()
            hole.addEllipse(r.adjusted(1, 1, -1, -1))
            p.fillPath(path.subtracted(hole), QColor(0, 0, 0, 115))
            p.setPen(QPen(QColor(255, 255, 255, 230), 2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(r.adjusted(1, 1, -1, -1))
        else:
            p.setPen(QPen(QColor(255, 255, 255, 230), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(r.adjusted(1, 1, -1, -1))
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.position() - QPointF(self.dx, self.dy)
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            d = e.position() - self._drag
            self.dx, self.dy = d.x(), d.y()
            self.update()

    def mouseReleaseEvent(self, e):
        self._drag = None
        self.setCursor(Qt.OpenHandCursor)

    def wheelEvent(self, e):
        step = 1.12 if e.angleDelta().y() > 0 else 1 / 1.12
        c = QPointF(self.width() / 2, self.height() / 2)
        pos = e.position()
        self.set_zoom(self.zoom * step, QPointF(self.dx - (pos.x() - c.x()), self.dy - (pos.y() - c.y())))

    def result(self, side=OUT_SIZE):
        out = QImage(side, side, QImage.Format_RGB32)
        out.fill(QColor("#FFFFFF"))
        p = QPainter(out)
        self._draw(p, side)
        p.end()
        return out


def load_image(path):
    """Снимок с учётом поворота из EXIF (как браузер)."""
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    img = reader.read()
    return img if not img.isNull() else None


def crop(owner, path, title="Кадрирование фото", round_mask=True):
    """Окно кадрирования для файла. → байты JPEG или None."""
    try:
        if os.path.getsize(path) > MAX_INPUT:
            toast(owner, "Фото больше 12 МБ — выберите поменьше", "warning")
            return None
    except OSError:
        return None
    img = load_image(path)
    if img is None:
        toast(owner, "Не удалось прочитать изображение", "error")
        return None
    slider = QSlider(Qt.Horizontal)
    slider.setRange(int(MIN_ZOOM * 100), int(MAX_ZOOM * 100))
    slider.setValue(100)
    slider.setStyleSheet(
        f"QSlider::groove:horizontal {{ height: {rem(0.35)}px; border-radius: {rem(0.18)}px; background: {theme.SURFACE_3}; }}"
        f"QSlider::sub-page:horizontal {{ background: {theme.ACCENT}; border-radius: {rem(0.18)}px; }}"
        f"QSlider::handle:horizontal {{ width: {rem(1.1)}px; height: {rem(1.1)}px; margin: -{rem(0.38)}px 0;"
        f" border-radius: {rem(0.55)}px; background: {theme.CARD_BG}; border: 2px solid {theme.ACCENT}; }}")

    def zoomed(z):
        slider.blockSignals(True)
        slider.setValue(int(round(z * 100)))
        slider.blockSignals(False)
    stage = _Stage(img, round_mask, zoomed)
    m = Modal(owner, title, "crop", width_rem=22)
    hint = QHBoxLayout()
    ic = label("")
    ic.setPixmap(icons.pixmap("info", rem(1.0), theme.ACCENT))
    hint.addWidget(ic, 0, Qt.AlignTop)
    hint.addWidget(label("Двигайте снимок и приближайте — " + ("в кружке" if round_mask else "в кадре") +
                         " будет ровно то, что видно", 0.8, theme.TEXT_MUTED, wrap=True), 1)
    m.body.addLayout(hint)
    m.body.addWidget(stage, 0, Qt.AlignHCenter)
    tools = QHBoxLayout()
    tools.setSpacing(rem(0.5))
    out_btn = Button("", "ghost", "minus", "Отдалить")
    in_btn = Button("", "ghost", "plus", "Приблизить")
    reset = Button("Сбросить", "ghost", "refresh", "Вернуть исходное положение")
    out_btn.clicked.connect(lambda: stage.set_zoom(stage.zoom / 1.2))
    in_btn.clicked.connect(lambda: stage.set_zoom(stage.zoom * 1.2))
    reset.clicked.connect(stage.reset)
    slider.valueChanged.connect(lambda v: stage.set_zoom(v / 100.0))
    tools.addWidget(out_btn)
    tools.addWidget(slider, 1)
    tools.addWidget(in_btn)
    tools.addWidget(reset)
    m.body.addLayout(tools)
    result = {}

    def save(_m):
        buf = QByteArray()
        dev = QBuffer(buf)
        dev.open(QIODevice.WriteOnly)
        stage.result().save(dev, "JPEG", 92)
        dev.close()
        result["data"] = bytes(buf)
        return True
    m.add_button("Отмена", "ghost")
    m.add_button("Сохранить", "primary", "check", save)
    m.exec()
    return result.get("data")


def pick(owner, title="Кадрирование фото", round_mask=True):
    """Выбор файла и кадрирование. → (имя.jpg, байты) или None."""
    path, _ = QFileDialog.getOpenFileName(owner, "Выберите снимок", "", FILTER)
    if not path:
        return None
    data = crop(owner, path, title, round_mask)
    if not data:
        return None
    return os.path.splitext(os.path.basename(path))[0] + ".jpg", data
