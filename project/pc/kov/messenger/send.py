# -*- coding: utf-8 -*-
"""Окно отправки файлов в чат — как Kov.SendDialog сайта (js/views/send-dialog.js).

Список с превью, подпись, «Добавить» и приём перетаскиванием прямо в окно.
Текст из строки ввода переезжает сюда, при отмене возвращается обратно.

Фото правится на компьютере: отражение, поворот, обрезка, рисование,
ластик, размытие и отмена. Итог — новый файл (исходник не трогается),
«Сбросить» возвращает исходник. Для видео выбираются точки обрезки по
времени, рамка кадра и поворот; применяются они при сжатии тем же
проходом FFmpeg (kov.media.video), так что лишней работы нет.
"""

import os
import subprocess
import tempfile

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QImage, QImageReader, QPainter, QPainterPath, QPen, QPixmap, QTransform)
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QSizePolicy, QSlider,
                             QToolButton, QVBoxLayout, QWidget)

from general_functions import theme
from kov import icons, jobs
from kov.dialogs import Modal, field
from kov.messenger.util import is_image as _img_name
from kov.ui import Button, border, clear_layout, fit_scroll, font, label, rem, scroll_area, text_qss, toast

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".heif")
VIDEO_EXT = (".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".3gp")
CANCELLED = object()            # «Отмена» в редакторе — оставить как было
AMBER = "#F5B301"


def kind_of(name):
    n = str(name or "").lower()
    if n.endswith(IMAGE_EXT):
        return "image"
    if n.endswith(VIDEO_EXT):
        return "video"
    return "file"


def fmt_size(b):
    b = int(b or 0)
    if b < 1024:
        return "%d Б" % b
    if b < 1048576:
        return "%.0f КБ" % (b / 1024.0)
    return "%.1f МБ" % (b / 1048576.0)


def fmt_time(sec):
    sec = max(0, int(round(sec or 0)))
    return "%d:%02d" % (sec // 60, sec % 60)


def load_image(path, side=None):
    """Снимок с учётом поворота из EXIF; side — уменьшить при чтении (быстро даже для больших JPEG)."""
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    if side:
        size = reader.size()
        if size.isValid() and max(size.width(), size.height()) > side:
            # размер — до поворота по EXIF: Qt уменьшает кадр, потом поворачивает
            k = float(side) / max(size.width(), size.height())
            reader.setScaledSize(QSize(max(1, int(size.width() * k)), max(1, int(size.height() * k))))
    img = reader.read()
    return img if not img.isNull() else None


def _upright(frame, img):
    """Кадр проигрывателя Qt 6 — как его показывают: toImage() не учитывает поворот и зеркало
    (метку телефона в файле), они задаются отдельно — в формате кадра и в самом кадре."""
    def angle(rotation):
        try:
            return int(rotation.value)
        except AttributeError:
            return int(rotation or 0)
    try:
        fmt = frame.surfaceFormat()
        turn = (angle(fmt.rotation()) + angle(frame.rotation())) % 360
        mirror = bool(fmt.isMirrored()) != bool(frame.mirrored())
    except Exception:
        return img
    if mirror:
        img = img.mirrored(True, False)
    if turn:
        img = img.transformed(QTransform().rotate(turn))
    return img


def thumb_for(path, kind, side=640):
    """В фоне: превью файла (фото — само, видео — кадр FFmpeg)."""
    if kind == "image":
        return load_image(path, side)
    if kind == "video":
        try:
            from kov.media import video as vp
            data = vp.poster(path, side=side)
        except Exception:
            data = None
        if data:
            img = QImage()
            img.loadFromData(data)
            return img if not img.isNull() else None
    return None


def _vh():
    """Высота окна приложения (vh сайта): размеры поля редактора считаются от неё."""
    from PySide6.QtWidgets import QApplication, QMainWindow
    for w in QApplication.topLevelWidgets():
        if isinstance(w, QMainWindow) and w.isVisible():
            return w.height()
    return QApplication.primaryScreen().availableGeometry().height()


class _Wrap(QWidget):
    """Строка с переносом (flex-wrap сайта): элементы по центру строки, лишнее — на следующую."""

    def __init__(self, spacing_rem=0.6):
        super().__init__()
        self.items = []
        self.sp = rem(spacing_rem)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)

    def add(self, w):
        w.setParent(self)
        self.items.append(w)
        self.relayout()

    def _lines(self, width):
        lines, line, x = [], [], 0
        for w in self.items:
            if w.isHidden():
                continue
            hint = w.sizeHint().expandedTo(w.minimumSize())
            if line and x + hint.width() > width:
                lines.append(line)
                line, x = [], 0
            line.append((w, hint))
            x += hint.width() + self.sp
        if line:
            lines.append(line)
        return lines

    def heightForWidth(self, width):
        lines = self._lines(width)
        return sum(max(h.height() for _, h in ln) for ln in lines) + self.sp * max(0, len(lines) - 1)

    def hasHeightForWidth(self):
        return True

    def sizeHint(self):
        return QSize(self.width() or rem(30), self.heightForWidth(self.width() or rem(30)))

    def relayout(self):
        y = 0
        for ln in self._lines(max(1, self.width())):
            lh = max(h.height() for _, h in ln)
            x = 0
            for w, h in ln:
                w.setGeometry(x, y + (lh - h.height()) // 2, h.width(), h.height())
                x += h.width() + self.sp
            y += lh + self.sp
        self.setMinimumHeight(self.heightForWidth(max(1, self.width())))
        self.updateGeometry()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.relayout()


def _btn_on(btn, on):
    """Нажатая кнопка инструмента: голубая подложка и рамка (.btn.on)."""
    btn.setProperty("on", bool(on))
    btn.setStyleSheet(_tool_qss())
    btn.setIcon(icons.icon(btn.glyph, rem(1.05), theme.INFO if on else theme.BTN_GHOST_TEXT))


def _tool_qss():
    b = border()
    return (f"QToolButton {{ background: {theme.BTN_GHOST_BG}; border: {b}px solid {theme.BTN_GHOST_BORDER};"
            f" border-radius: {rem(0.5)}px; }}"
            f"QToolButton:hover {{ background: {theme.BTN_GHOST_HOVER}; }}"
            f"QToolButton[on=\"true\"] {{ background: {theme.INFO_SOFT}; border-color: {theme.INFO}; }}")


def tool_button(glyph, tip, handler, w=2.6, h=2.4):
    b = QToolButton()
    b.glyph = glyph
    b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    b.setFixedSize(rem(w), rem(h))
    b.setIconSize(QSize(rem(1.05), rem(1.05)))
    _btn_on(b, False)
    b.clicked.connect(lambda _=False: handler())
    return b


def _equal_foot(m):
    """Редакторы: «Сбросить · Отмена · Готово» — одной строкой равной ширины."""
    lay = m.foot_layout
    for i in reversed(range(lay.count())):
        if lay.itemAt(i).spacerItem() is not None:
            lay.takeAt(i)
    lay.setSpacing(rem(0.4))
    for b in m.buttons:
        b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        lay.setStretchFactor(b, 1)


# ══════════════════════════════════════════════════════════════════════════
# Окно отправки
# ══════════════════════════════════════════════════════════════════════════

class _Thumb(QLabel):
    """Превью 3,6rem (.send-thumb): фото, кадр видео со значком или значок файла; «изменено» снизу."""

    clicked = Signal()

    def __init__(self, item):
        super().__init__()
        self.item = item
        self.side = rem(3.6)
        self.setFixedSize(self.side, self.side)
        if item["kind"] != "file":
            self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        r = QRectF(0, 0, self.side, self.side)
        path = QPainterPath()
        path.addRoundedRect(r, rem(0.55), rem(0.55))
        p.setClipPath(path)
        p.fillPath(path, QColor(theme.CHIP_BG))
        img = self.item.get("thumb")
        if img is not None and self.item["kind"] != "file":
            pm = QPixmap.fromImage(img).scaled(self.side, self.side, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            p.drawPixmap((self.side - pm.width()) // 2, (self.side - pm.height()) // 2, pm)
        if self.item["kind"] == "video":
            p.fillRect(r, QColor(19, 42, 50, 77))
            s = rem(1.2)
            p.drawPixmap(int((self.side - s) / 2), int((self.side - s) / 2), icons.pixmap("play", s, "#FFFFFF"))
        elif self.item["kind"] == "file" or img is None:
            s = rem(1.4)
            glyph = "file" if self.item["kind"] == "file" else self.item["kind"]
            p.drawPixmap(int((self.side - s) / 2), int((self.side - s) / 2), icons.pixmap(glyph, s, theme.ACCENT))
        if self.item.get("edit"):
            h = rem(0.95)
            band = QRectF(0, self.side - h, self.side, h)
            p.fillRect(band, QColor(245, 179, 1, 230))
            p.setPen(QColor("#132A32"))
            p.setFont(font(0.58, bold=True))
            p.drawText(band, Qt.AlignCenter, "изменено")
        p.end()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.item["kind"] != "file":
            self.clicked.emit()


class _Row(QFrame):
    def __init__(self, dlg, i, item):
        super().__init__()
        self.setObjectName("sendRow")
        self.setStyleSheet(f"QFrame#sendRow {{ border-radius: {rem(0.5)}px; background: transparent; }}"
                           f"QFrame#sendRow:hover {{ background: {theme.CHIP_BG}; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(rem(0.4), rem(0.35), rem(0.4), rem(0.35))
        lay.setSpacing(rem(0.6))
        th = _Thumb(item)
        th.clicked.connect(lambda: dlg.edit(i))
        lay.addWidget(th)
        who = QVBoxLayout()
        who.setSpacing(0)
        name = label(item["name"], 0.86, theme.TEXT_VALUE, bold=True)
        name.setMinimumWidth(rem(4))
        name.setToolTip(item["name"])
        who.addStretch(1)
        who.addWidget(name)
        extra = " · обрезано" if item["kind"] == "video" and item.get("edit") else ""
        who.addWidget(label(fmt_size(item["size"]) + extra, 0.74, theme.TEXT_LABEL))
        who.addStretch(1)
        lay.addLayout(who, 1)
        if item["kind"] != "file":
            ed = Button("", "ghost", "edit", tooltip="Редактировать фото" if item["kind"] == "image" else "Обрезать видео")
            ed.clicked.connect(lambda: dlg.edit(i))
            lay.addWidget(ed)
        rm = Button("", "ghost", "close", tooltip="Убрать")
        rm.clicked.connect(lambda: dlg.remove(i))
        lay.addWidget(rm)
        QTimer.singleShot(0, lambda: self._elide(name))

    @staticmethod
    def _elide(lab):
        from PySide6.QtGui import QFontMetrics
        try:
            fm = QFontMetrics(lab.font())
            lab.setText(fm.elidedText(lab.toolTip(), Qt.ElideRight, max(rem(4), lab.width())))
        except RuntimeError:
            pass


class _DropVeil(QLabel):
    """Подсветка окна при перетаскивании файлов (.modal.drag-on)."""

    def __init__(self, parent):
        super().__init__("Отпустите файлы — они добавятся к отправке", parent)
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self.setFont(font(1.05, bold=True))
        self.setStyleSheet(f"color: #FFFFFF; background: rgba(74,144,164,224); border: {max(2, rem(0.2))}px dashed {theme.INFO};"
                           f" border-radius: {rem(1)}px;")
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.hide()


class SendDialog(Modal):
    """open({files, text, max}) сайта: run() → (пути, подпись, правки видео, превью) или None."""

    def __init__(self, host, paths, text="", max_files=100):
        super().__init__(host, "Отправка файлов", "upload", width_rem=52)
        self.items = []
        self.max = max_files
        self.result = None
        self.setAcceptDrops(True)
        head = QHBoxLayout()
        head.setSpacing(rem(0.5))
        self.counter = label("", 0.75, theme.TEXT_LABEL)
        head.addWidget(self.counter)
        head.addStretch(1)
        add = Button("Добавить", "ghost", "plus")
        add.clicked.connect(self.pick)
        head.addWidget(add)
        self.body.addLayout(head)
        self.holder = QWidget()
        self.holder.setStyleSheet("background: transparent;")
        self.list = QVBoxLayout(self.holder)
        self.list.setContentsMargins(0, 0, 0, 0)
        self.list.setSpacing(rem(0.3))
        self.area = scroll_area(self.holder)
        self.area.setMaximumHeight(rem(20))
        self.body.addWidget(self.area)
        self.caption = QPlainTextEdit()
        self.caption.setPlaceholderText("Подпись (необязательно)")
        self.caption.setPlainText(text or "")
        self.caption.setFont(font(0.875))
        self.caption.setMinimumHeight(rem(4.5))
        self.caption.setMaximumHeight(rem(4.5))
        self.caption.setStyleSheet(text_qss())
        self.body.addWidget(field("Подпись", self.caption))
        self.add_button("Отмена", "ghost")
        self.add_button("Отправить", "primary", "send", self._send)
        self.veil = _DropVeil(self.card)
        self.body.setSpacing(rem(0.6))
        self.add_files(paths)

    # ── список ───────────────────────────────────────────────────────────────

    def add_files(self, paths):
        room = self.max - len(self.items)
        incoming = [p for p in paths or [] if os.path.isfile(p)]
        if not incoming:
            return
        if room <= 0:
            toast(self, "Не больше %d файлов за раз" % self.max, "warning")
            return
        if len(incoming) > room:
            toast(self, "Добавлено %d из %d — не больше %d за раз" % (room, len(incoming), self.max), "warning")
        for p in incoming[:room]:
            name = os.path.basename(p)
            item = {"path": p, "name": name, "kind": kind_of(name), "edit": None, "original": None, "thumb": None,
                    "size": os.path.getsize(p)}
            self.items.append(item)
            self._load_thumb(item)
        self.draw()

    def _load_thumb(self, item):
        if item["kind"] == "file":
            return
        path = item["path"]

        def done(img, it=item, p=path):
            if img is not None and it["path"] == p:
                it["thumb"] = img
                try:
                    self.draw()
                except RuntimeError:
                    pass
        jobs.run(thumb_for, path, item["kind"], done=done, failed=lambda e: None, owner=self)

    def draw(self):
        clear_layout(self.list)
        for i, it in enumerate(self.items):
            self.list.addWidget(_Row(self, i, it))
        if not self.items:
            self.list.addWidget(label("Файлы не выбраны", 0.85, theme.TEXT_MUTED, align=Qt.AlignCenter))
        self.list.addStretch(1)
        self.counter.setText("%d из %d" % (len(self.items), self.max))
        fit_scroll(self.area, 20, self._center)

    def pick(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Добавить файлы", "", "Все файлы (*)")
        if paths:
            self.add_files(paths)

    def remove(self, i):
        if 0 <= i < len(self.items):
            self.items.pop(i)
            self.draw()

    def edit(self, i):
        if not 0 <= i < len(self.items):
            return
        it = self.items[i]
        if it["kind"] == "image":
            result = ImageEditor(self, it["path"]).run()
            if result is CANCELLED:
                return
            if result is None:                  # «Сбросить» или правок не было — исходник
                if it["original"]:
                    it["path"], it["original"] = it["original"], None
                    it["size"] = os.path.getsize(it["path"])
                    it["thumb"] = None
                    self._load_thumb(it)
                it["edit"] = None
                self.draw()
                return
            if not it["original"]:
                it["original"] = it["path"]
            it["path"] = result
            it["name"] = os.path.basename(result)
            it["size"] = os.path.getsize(result)
            it["edit"] = {"photo": True}
            it["thumb"] = None
            self._load_thumb(it)
            self.draw()
        elif it["kind"] == "video":
            result = VideoEditor(self, it["path"], it["edit"]).run()
            if result is CANCELLED:
                return
            it["edit"] = result or None
            self.draw()

    # ── итог ─────────────────────────────────────────────────────────────────

    def caption_text(self):
        return self.caption.toPlainText()

    def _send(self, _m):
        if not self.items:
            toast(self, "Добавьте хотя бы один файл", "warning")
            return False
        edits = {}
        for i, it in enumerate(self.items):
            if it["kind"] == "video" and it["edit"]:
                edits[str(i)] = it["edit"]
        thumbs = {it["path"]: it["thumb"] for it in self.items if it.get("thumb") is not None}
        self.result = ([it["path"] for it in self.items], self.caption.toPlainText().strip(), edits, thumbs)
        return True

    def run(self):
        self.exec()
        return self.result

    # ── перетаскивание прямо в окно ──────────────────────────────────────────

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self.veil.setGeometry(self.card.rect().adjusted(rem(0.35), rem(0.35), -rem(0.35), -rem(0.35)))
            self.veil.show()
            self.veil.raise_()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragLeaveEvent(self, _e):
        self.veil.hide()

    def dropEvent(self, e):
        self.veil.hide()
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            e.acceptProposedAction()
            self.add_files(paths)


# ══════════════════════════════════════════════════════════════════════════
# Рамка обрезки — общая для фото и видео (cropFrame сайта)
# ══════════════════════════════════════════════════════════════════════════

class CropFrame:
    """Восемь ручек, перенос за середину, затемнение вокруг. Область — в долях кадра."""

    MIN = 0.08

    def __init__(self):
        self.r = [0.0, 0.0, 1.0, 1.0]
        self.ratio = None
        self.mode = None
        self.start = None
        self.point = None

    def rect(self):
        x, y, w, h = self.r
        return None if (w > 0.995 and h > 0.995) else {"x": x, "y": y, "w": w, "h": h}

    def reset(self):
        self.r = [0.0, 0.0, 1.0, 1.0]
        self.ratio = None

    def set_ratio(self, value, frame_ratio):
        self.ratio = value
        if not value:
            return
        w, h = 1.0, (1.0 / value) * frame_ratio
        if h > 1:
            h, w = 1.0, value / frame_ratio
        self.r = [(1 - w) / 2, (1 - h) / 2, w, h]

    def box(self, view):
        x, y, w, h = self.r
        return QRectF(view.x() + x * view.width(), view.y() + y * view.height(), w * view.width(), h * view.height())

    def handles(self, view):
        b = self.box(view)
        c, e, s = rem(1.0), rem(2.2), rem(0.5)
        cx, cy = b.center().x(), b.center().y()
        return {
            "nw": QRectF(b.left() - c / 2, b.top() - c / 2, c, c),
            "ne": QRectF(b.right() - c / 2, b.top() - c / 2, c, c),
            "se": QRectF(b.right() - c / 2, b.bottom() - c / 2, c, c),
            "sw": QRectF(b.left() - c / 2, b.bottom() - c / 2, c, c),
            "n": QRectF(cx - e / 2, b.top() - s / 2, e, s),
            "s": QRectF(cx - e / 2, b.bottom() - s / 2, e, s),
            "w": QRectF(b.left() - s / 2, cy - e / 2, s, e),
            "e": QRectF(b.right() - s / 2, cy - e / 2, s, e),
        }

    def hit(self, pos, view):
        for k, r in self.handles(view).items():
            if r.adjusted(-4, -4, 4, 4).contains(pos):
                return k
        if self.box(view).contains(pos):
            return "move"
        return None

    def cursor(self, mode):
        return {"nw": Qt.SizeFDiagCursor, "se": Qt.SizeFDiagCursor, "ne": Qt.SizeBDiagCursor, "sw": Qt.SizeBDiagCursor,
                "n": Qt.SizeVerCursor, "s": Qt.SizeVerCursor, "e": Qt.SizeHorCursor, "w": Qt.SizeHorCursor,
                "move": Qt.SizeAllCursor}.get(mode, Qt.ArrowCursor)

    def begin(self, mode, pos, view):
        self.mode = mode
        self.start = list(self.r)
        self.point = ((pos.x() - view.x()) / view.width(), (pos.y() - view.y()) / view.height())

    def move(self, pos, view):
        if not self.mode:
            return
        p = ((pos.x() - view.x()) / view.width(), (pos.y() - view.y()) / view.height())
        dx, dy = p[0] - self.point[0], p[1] - self.point[1]
        x, y, w, h = self.start
        clamp = lambda v, lo, hi: max(lo, min(hi, v))  # noqa: E731
        n = [x, y, w, h]
        if self.mode == "move":
            n[0] = clamp(x + dx, 0, 1 - w)
            n[1] = clamp(y + dy, 0, 1 - h)
        else:
            if "w" in self.mode:
                nx = clamp(x + dx, 0, x + w - self.MIN)
                n[2] = w + (x - nx)
                n[0] = nx
            if "e" in self.mode:
                n[2] = clamp(w + dx, self.MIN, 1 - x)
            if "n" in self.mode:
                ny = clamp(y + dy, 0, y + h - self.MIN)
                n[3] = h + (y - ny)
                n[1] = ny
            if "s" in self.mode:
                n[3] = clamp(h + dy, self.MIN, 1 - y)
            if self.ratio:
                px, py = view.width(), view.height()
                wpx = n[2] * px
                hpx = wpx / self.ratio
                if hpx / py + n[1] > 1:
                    hpx = (1 - n[1]) * py
                    wpx = hpx * self.ratio
                n[2], n[3] = wpx / px, hpx / py
        self.r = n

    def end(self):
        self.mode = None

    def paint(self, p, view):
        b = self.box(view)
        shade = QPainterPath()
        shade.addRect(view)
        hole = QPainterPath()
        hole.addRect(b)
        p.fillPath(shade.subtracted(hole), QColor(19, 42, 50, 140))
        # сетка третей: светлая линия с тёмной тенью
        for k in (1, 2):
            x = b.left() + b.width() * k / 3.0
            y = b.top() + b.height() * k / 3.0
            p.setPen(QPen(QColor(15, 32, 39, 115), 1))
            p.drawLine(QPointF(x + 1, b.top()), QPointF(x + 1, b.bottom()))
            p.drawLine(QPointF(b.left(), y + 1), QPointF(b.right(), y + 1))
            p.setPen(QPen(QColor(255, 255, 255, 191), 1))
            p.drawLine(QPointF(x, b.top()), QPointF(x, b.bottom()))
            p.drawLine(QPointF(b.left(), y), QPointF(b.right(), y))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(15, 32, 39, 217), 1.5))
        p.drawRect(b.adjusted(-1.75, -1.75, 1.75, 1.75))
        p.setPen(QPen(QColor(255, 255, 255, 245), 2))
        p.drawRect(b)
        p.setPen(QPen(QColor(15, 32, 39, 140), 1.5))
        p.drawRect(b.adjusted(1.75, 1.75, -1.75, -1.75))
        for r in self.handles(view).values():
            p.setPen(QPen(QColor(15, 32, 39, 217), 1.5))
            p.setBrush(QColor(AMBER))
            p.drawRoundedRect(r, 2, 2)


# ══════════════════════════════════════════════════════════════════════════
# Редактор фото
# ══════════════════════════════════════════════════════════════════════════

MAX_SIDE = 2560
COLORS = ["#F5B301", "#E53935", "#43A047", "#1E88E5", "#132A32", "#FFFFFF"]


def _blank(w, h):
    img = QImage(max(1, int(w)), max(1, int(h)), QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    return img


def _blurred(src, radius):
    """Размытая копия: сцена Qt с эффектом размытия (как canvas filter: blur())."""
    from PySide6.QtWidgets import QGraphicsBlurEffect, QGraphicsPixmapItem, QGraphicsScene
    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(QPixmap.fromImage(src))
    eff = QGraphicsBlurEffect()
    eff.setBlurRadius(radius)
    eff.setBlurHints(QGraphicsBlurEffect.QualityHint)
    item.setGraphicsEffect(eff)
    scene.addItem(item)
    out = _blank(src.width(), src.height())
    p = QPainter(out)
    scene.render(p, QRectF(0, 0, src.width(), src.height()), QRectF(0, 0, src.width(), src.height()))
    p.end()
    return out


class _Stage(QWidget):
    """Поле снимка: клетка под прозрачностью, снимок по центру, кисть и рамка обрезки."""

    def __init__(self, ed):
        super().__init__()
        self.ed = ed
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.dot = None

    def view(self):
        pad = rem(0.85) + 4
        avail = self.rect().adjusted(pad, pad, -pad, -pad)
        b = self.ed.base
        s = min(avail.width() / float(b.width()), avail.height() / float(b.height()), 1.0)
        w, h = max(1, round(b.width() * s)), max(1, round(b.height() * s))
        return QRectF(self.width() / 2.0 - w / 2.0, self.height() / 2.0 - h / 2.0, w, h)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), rem(0.5), rem(0.5))
        p.setClipPath(path)
        sq = rem(0.6)
        a, b = QColor(theme.STAGE_CHECK_A), QColor(theme.STAGE_CHECK_B)
        for yy in range(0, self.height(), sq):
            for xx in range(0, self.width(), sq):
                p.fillRect(xx, yy, sq, sq, a if ((xx // sq) + (yy // sq)) % 2 == 0 else b)
        v = self.view()
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.drawImage(v, self.ed.base)
        if self.ed.tool == "crop":
            self.ed.frame.paint(p, v)
        if self.dot is not None:
            d = self.dot_size()
            c = QColor("#E53935") if self.ed.tool == "erase" else QColor("#37474F") if self.ed.tool == "blur" \
                else QColor(self.ed.color)
            p.setPen(QPen(QColor(0, 0, 0, 64), 1))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(self.dot), d / 2.0 + 1, d / 2.0 + 1)
            p.setPen(QPen(c, 2))
            p.setBrush(QColor(120, 144, 156, 89) if self.ed.tool == "blur" else QColor(255, 255, 255, 64))
            p.drawEllipse(QPointF(self.dot), d / 2.0, d / 2.0)
        p.end()

    def dot_size(self):
        return max(4.0, self.ed.size * (1.0 if self.ed.tool == "draw" else 1.6))

    # ── мышь ─────────────────────────────────────────────────────────────────

    def to_base(self, pos):
        v = self.view()
        b = self.ed.base
        return QPointF((pos.x() - v.x()) * b.width() / v.width(), (pos.y() - v.y()) * b.height() / v.height())

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        v = self.view()
        if self.ed.tool == "crop":
            mode = self.ed.frame.hit(e.position(), v)
            if mode:
                self.ed.frame.begin(mode, e.position(), v)
            return
        if self.ed.tool in ("draw", "erase", "blur"):
            self.ed.stroke_begin(self.to_base(e.position()))

    def mouseMoveEvent(self, e):
        v = self.view()
        if self.ed.tool == "crop":
            if self.ed.frame.mode:
                self.ed.frame.move(e.position(), v)
                self.update()
            else:
                self.setCursor(self.ed.frame.cursor(self.ed.frame.hit(e.position(), v)))
            return
        if self.ed.tool in ("draw", "erase", "blur"):
            self.dot = e.position().toPoint() if v.contains(e.position()) else None
            self.setCursor(Qt.BlankCursor if self.dot is not None else Qt.ArrowCursor)
            if e.buttons() & Qt.LeftButton:
                self.ed.stroke_to(self.to_base(e.position()))
            self.update()
        else:
            self.setCursor(Qt.ArrowCursor)

    def mouseReleaseEvent(self, e):
        if self.ed.tool == "crop":
            self.ed.frame.end()
        else:
            self.ed.stroke_end()

    def leaveEvent(self, _e):
        self.dot = None
        self.update()


class _Swatch(QToolButton):
    def __init__(self, color, blur=False):
        super().__init__()
        self.color, self.blur = color, blur
        self.on = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(rem(1.5) + 8, rem(1.5) + 8)
        self.setStyleSheet("QToolButton { border: none; background: transparent; }")

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        d = rem(1.5) * (1.18 if self.on and not self.blur else 1.0)
        c = QPointF(self.width() / 2.0, self.height() / 2.0)
        if self.on:
            p.setPen(QPen(QColor(theme.INFO), 2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c, d / 2.0 + 3, d / 2.0 + 3)
        path = QPainterPath()
        path.addEllipse(c, d / 2.0, d / 2.0)
        if self.blur:
            p.save()
            p.setClipPath(path)
            p.fillPath(path, QColor("#FFFFFF"))
            q = d / 4.0
            for i in range(-2, 3):
                for j in range(-2, 3):
                    if (i + j) % 2 == 0:
                        p.fillRect(QRectF(c.x() + i * q, c.y() + j * q, q, q), QColor(19, 42, 50, 150))
            p.fillPath(path, QColor(255, 255, 255, 90))
            p.restore()
        else:
            p.fillPath(path, QColor(self.color))
        p.setPen(QPen(QColor(theme.TEXT_VALUE if self.on else theme.CARD_BORDER), 2))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(c, d / 2.0, d / 2.0)
        p.end()


class _SizeDot(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(30, 30)
        self.d, self.color, self.blur = 6, theme.ACCENT, False

    def set(self, d, color, blur):
        self.d, self.color, self.blur = min(28, d), color, blur
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#90A4AE") if self.blur else QColor(self.color))
        p.drawEllipse(QPointF(15, 15), self.d / 2.0, self.d / 2.0)
        p.end()


def _slider_qss():
    b = border()
    return (f"QSlider {{ background: transparent; min-height: {rem(1.4)}px; }}"
            f"QSlider::groove:horizontal {{ height: {rem(0.4)}px; border-radius: {rem(0.2)}px; background: {theme.CHIP_BG}; }}"
            f"QSlider::sub-page:horizontal {{ background: {theme.ACCENT}; border-radius: {rem(0.2)}px; }}"
            f"QSlider::handle:horizontal {{ width: {rem(1.1)}px; height: {rem(1.1)}px; margin: -{rem(0.35)}px 0;"
            f" border-radius: {rem(0.55)}px; background: {theme.KNOB_BG}; border: {2 * b}px solid {theme.ACCENT}; }}")


class ImageEditor(Modal):
    """Правка снимка: два слоя — clean (снимок после поворотов и обрезки) и base
    (он же с рисунком). Ластик возвращает пиксели clean, размытие берёт картинку из base."""

    def __init__(self, parent, path):
        super().__init__(parent, "Редактирование фото", "image", width_rem=52)
        self.path = path
        self.result = CANCELLED
        img = load_image(path, MAX_SIDE)
        if img is None:
            toast(parent, "Не удалось открыть изображение", "warning")
            img = _blank(16, 16)
        self.base = img.convertToFormat(QImage.Format_ARGB32_Premultiplied)
        self.clean = self.base.copy()
        self.origin = self.base.copy()
        self.history = []
        self.tool = "none"
        self.color = COLORS[0]
        self.size = 6
        self.frame = CropFrame()
        self.drawing = False
        self.last = None
        self.started = False
        self.blur_src = self.blur_mask = None
        self.stage = _Stage(self)
        self.stage.setFixedHeight(min(rem(32), int(_vh() * 0.58)))
        self.body.addWidget(self.stage)
        panel = QFrame()
        panel.setObjectName("edPanel")
        panel.setStyleSheet(f"QFrame#edPanel {{ border: none; border-top: {border()}px solid {theme.CARD_BORDER};"
                            f" background: transparent; }}")
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(rem(0.2), rem(0.5), rem(0.2), 0)
        self.wrap = _Wrap(0.6)
        outer.addWidget(self.wrap)
        tools_box = QWidget()
        tools_box.setStyleSheet("background: transparent;")
        tools = QHBoxLayout(tools_box)
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(rem(0.3))
        self.tool_btns = {}
        for key, glyph, tip, fn in (
                ("draw", "brush", "Рисовать", lambda: self.set_tool("none" if self.tool == "draw" else "draw")),
                ("erase", "eraser", "Ластик — убрать нарисованное", lambda: self.set_tool("none" if self.tool == "erase" else "erase")),
                ("crop", "crop", "Обрезать", lambda: self.set_tool("none" if self.tool == "crop" else "crop")),
                (None, "flip", "Отразить слева направо", self.mirror_h),
                (None, "flip_v", "Перевернуть сверху вниз", self.mirror_v),
                (None, "rotate", "Повернуть на 90°", self.rotate),
                (None, "undo", "Отменить последнее действие", self.undo)):
            b = tool_button(glyph, tip, fn)
            tools.addWidget(b)
            if key:
                self.tool_btns[key] = b
        self.wrap.add(tools_box)
        self.colors = QWidget()
        self.colors.setStyleSheet("background: transparent;")
        cl = QHBoxLayout(self.colors)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(max(0, rem(0.4) - 8))
        self.swatches = []
        for c in COLORS:
            s = _Swatch(c)
            s.setToolTip("Цвет кисти")
            s.clicked.connect(lambda _=False, c=c: self.pick_color(c))
            cl.addWidget(s)
            self.swatches.append(s)
        cl.addSpacing(rem(0.35))
        self.blur_swatch = _Swatch("#FFFFFF", blur=True)
        self.blur_swatch.setToolTip("Размытие — закрасить номера, лица, подписи")
        self.blur_swatch.clicked.connect(lambda: self.set_tool("none" if self.tool == "blur" else "blur"))
        cl.addWidget(self.blur_swatch)
        self.wrap.add(self.colors)
        self.size_box = QWidget()
        self.size_box.setStyleSheet("background: transparent;")
        sl = QHBoxLayout(self.size_box)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(rem(0.6))
        self.size_name = label("Кисть", 0.74, theme.TEXT_LABEL, bold=True)
        self.size_name.setMinimumWidth(rem(4.6))
        sl.addWidget(self.size_name)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(2, 40)
        self.slider.setValue(self.size)
        self.slider.setFixedWidth(rem(9))
        self.slider.setToolTip("Толщина линии")
        self.slider.setStyleSheet(_slider_qss())
        self.slider.valueChanged.connect(self._size_changed)
        sl.addWidget(self.slider)
        self.size_dot = _SizeDot()
        sl.addWidget(self.size_dot)
        self.wrap.add(self.size_box)
        self.body.addWidget(panel)
        self.body.setSpacing(rem(0.6))
        self.add_button("Сбросить", "ghost", "refresh", self._reset)
        self.add_button("Отмена", "ghost", None, self._cancel)
        self.add_button("Готово", "primary", "check", self._done)
        _equal_foot(self)
        self.set_tool("none")

    def run(self):
        self.exec()
        return self.result

    # ── слои и история ───────────────────────────────────────────────────────

    def push(self, paint_only=False):
        self.history.append((self.base.copy(), None if paint_only else self.clean.copy()))
        if len(self.history) > 14:
            self.history.pop(0)

    def transform(self, fn):
        self.push()
        self.base = fn(self.base)
        self.clean = fn(self.clean)
        self.frame.reset()
        self.stage.update()

    def mirror_h(self):
        self.transform(lambda im: im.mirrored(True, False))

    def mirror_v(self):
        self.transform(lambda im: im.mirrored(False, True))

    def rotate(self):
        self.transform(lambda im: im.transformed(QTransform().rotate(90), Qt.SmoothTransformation)
                       .convertToFormat(QImage.Format_ARGB32_Premultiplied))

    def undo(self):
        if not self.history:
            return
        base, clean = self.history.pop()
        self.base = base
        if clean is not None:
            self.clean = clean
        self.frame.reset()
        self.stage.update()

    def apply_crop(self):
        r = self.frame.rect()
        if not r:
            return

        def cut(im):
            x, y = round(im.width() * r["x"]), round(im.height() * r["y"])
            w, h = max(1, round(im.width() * r["w"])), max(1, round(im.height() * r["h"]))
            return im.copy(x, y, w, h)
        self.transform(cut)

    def changed(self):
        return bool(self.history) or bool(self.frame.rect())

    # ── инструменты ──────────────────────────────────────────────────────────

    def set_tool(self, t):
        if t != "crop" and self.tool == "crop":
            self.frame.reset()     # ушли с обрезки — рамка не «выстрелит» при сохранении
        if t == "blur" and self.tool != "blur" and self.size < 16:
            self.size = 20         # тонкой кистью номер не закрасишь
            self.slider.blockSignals(True)
            self.slider.setValue(self.size)
            self.slider.blockSignals(False)
        self.tool = t
        for k, b in self.tool_btns.items():
            _btn_on(b, k == t)
        for s in self.swatches:
            s.on = t == "draw" and s.color == self.color
            s.update()
        self.blur_swatch.on = t == "blur"
        self.blur_swatch.update()
        self.colors.setVisible(t in ("draw", "blur"))
        self.size_box.setVisible(t in ("draw", "erase", "blur"))
        self.wrap.relayout()
        self.stage.dot = None
        self._paint_size()
        self.stage.update()
        QTimer.singleShot(0, self._refit)

    def _refit(self):
        try:
            self._center()
        except RuntimeError:
            pass

    def pick_color(self, c):
        self.color = c
        self.set_tool("draw")

    def _size_changed(self, v):
        self.size = int(v)
        self._paint_size()

    def _paint_size(self):
        self.size_name.setText("Ластик" if self.tool == "erase" else "Размытие" if self.tool == "blur" else "Кисть")
        d = max(4, self.size * (1 if self.tool == "draw" else 1.6))
        self.size_dot.set(d, "#E53935" if self.tool == "erase" else self.color, self.tool == "blur")

    def scale_k(self):
        v = self.stage.view()
        return self.base.width() / max(1.0, v.width())

    # ── кисть, ластик, размытие ──────────────────────────────────────────────

    def stroke_begin(self, pt):
        self.drawing = True
        self.started = False
        self.last = pt
        self.stroke_to(QPointF(pt.x() + 0.01, pt.y()))       # точка от одиночного нажатия

    def stroke_to(self, pt):
        if not self.drawing:
            return
        k = self.scale_k()
        if not self.started:
            self.push(paint_only=True)
            self.started = True
            if self.tool == "blur":
                self._blur_begin(k)
        if self.tool == "blur":
            width = max(2.0, self.size * k * 1.6)
            p = QPainter(self.blur_mask)
            p.setRenderHint(QPainter.Antialiasing, True)
            p.setPen(QPen(QColor("#FFFFFF"), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawLine(self.last, pt)
            p.end()
            self._blur_paint(self._dirty(self.last, pt, width))
            self.last = pt
            self.stage.update()
            return
        if self.tool == "erase":
            width = max(1.5, self.size * k * 1.6)
        else:
            width = max(1.0, self.size * k)
        p = QPainter(self.base)
        p.setRenderHint(QPainter.Antialiasing, True)
        if self.tool == "erase":
            p.setCompositionMode(QPainter.CompositionMode_DestinationOut)
            p.setPen(QPen(QColor(0, 0, 0, 255), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        else:
            p.setPen(QPen(QColor(self.color), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawLine(self.last, pt)
        p.end()
        if self.tool == "erase":
            # под следом ластика снова снимок без рисунка
            r = self._dirty(self.last, pt, width)
            q = QPainter(self.base)
            q.setCompositionMode(QPainter.CompositionMode_DestinationOver)
            q.drawImage(r, self.clean, r)
            q.end()
        self.last = pt
        self.stage.update()

    def stroke_end(self):
        if self.drawing and self.tool == "blur":
            self.blur_src = self.blur_mask = None
        self.drawing = False
        self.last = None

    def _dirty(self, a, b, width):
        m = width / 2.0 + 2
        r = QRectF(QPointF(min(a.x(), b.x()) - m, min(a.y(), b.y()) - m),
                   QPointF(max(a.x(), b.x()) + m, max(a.y(), b.y()) + m)).toAlignedRect()
        return r.intersected(self.base.rect())

    def _blur_begin(self, k):
        # размытие считаем на уменьшенной копии — как сайт
        f = min(1.0, 1400.0 / max(self.base.width(), self.base.height()))
        small = self.base.scaled(max(1, round(self.base.width() * f)), max(1, round(self.base.height() * f)),
                                 Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        radius = max(3.0, self.size * k * f * 1.15) * 2.0
        blurred = _blurred(small, radius)
        self.blur_src = blurred.scaled(self.base.width(), self.base.height(), Qt.IgnoreAspectRatio,
                                       Qt.SmoothTransformation).convertToFormat(QImage.Format_ARGB32_Premultiplied)
        self.blur_mask = _blank(self.base.width(), self.base.height())

    def _blur_paint(self, r):
        if self.blur_src is None or r.isEmpty():
            return
        tmp = self.blur_src.copy(r)
        p = QPainter(tmp)
        p.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        p.drawImage(0, 0, self.blur_mask, r.x(), r.y(), r.width(), r.height())
        p.end()
        q = QPainter(self.base)
        q.drawImage(r.topLeft(), tmp)
        q.end()

    # ── кнопки ───────────────────────────────────────────────────────────────

    def _reset(self, _m):
        self.result = None
        return True

    def _cancel(self, _m):
        self.result = CANCELLED
        return True

    def _done(self, _m):
        if not self.changed():
            self.result = None                 # правок нет — уйдёт исходный файл
            return True
        self.apply_crop()                      # обрезка применяется при сохранении
        png = self.path.lower().endswith(".png")
        stem = os.path.splitext(os.path.basename(self.path))[0]
        folder = tempfile.mkdtemp(prefix="kovapp-edit-")
        out = os.path.join(folder, stem + (".png" if png else ".jpg"))
        img = self.base if png else self.base.convertToFormat(QImage.Format_RGB32)
        if not img.save(out, "PNG" if png else "JPG", -1 if png else 92):
            toast(self, "Не удалось собрать изображение", "warning")
            return False
        self.result = out
        return True


# ══════════════════════════════════════════════════════════════════════════
# Обрезка видео
# ══════════════════════════════════════════════════════════════════════════

def _frame_at(path, t, side=960):
    """Кадр видео на секунде t (FFmpeg из комплекта), с учётом поворота телефона."""
    from kov.media import video as vp
    exe = vp.ffmpeg_path()
    if not exe:
        return None
    vf = "scale='min(%d,iw)':'min(%d,ih)':force_original_aspect_ratio=decrease" % (side, side)
    cmd = [exe, "-hide_banner", "-nostdin", "-loglevel", "error", "-ss", "%.3f" % max(0.0, t), "-i", path,
           "-frames:v", "1", "-vf", vf, "-q:v", "4", "-f", "image2pipe", "-c:v", "mjpeg", "pipe:1"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=60, **vp._popen_kwargs())
    except Exception:
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    img = QImage()
    img.loadFromData(proc.stdout)
    return img if not img.isNull() else None


def _video_info(path):
    from kov.media import video as vp
    data = vp.probe(path)
    stream = next((s for s in data.get("streams") or [] if s.get("codec_type") == "video"), {}) or {}
    w, h = int(stream.get("width") or 0), int(stream.get("height") or 0)
    if vp.rotation_of(stream) in (90, 270):
        w, h = h, w
    return {"duration": vp.duration_of(data), "width": w, "height": h}


class _VideoStage(QWidget):
    """Кадр видео на тёмном поле; поворот; рамка кадра поверх."""

    def __init__(self, ed):
        super().__init__()
        self.ed = ed
        self.img = None
        self.message = ""
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def frame_rect(self):
        """Прямоугольник показанного (повёрнутого) кадра."""
        pad = rem(0.85)
        avail = self.rect().adjusted(pad, pad, -pad, -pad)
        vw, vh = self.ed.dims()
        if not vw or not vh:
            return QRectF(avail)
        if self.ed.rotate % 180:
            vw, vh = vh, vw
        s = min(avail.width() / float(vw), avail.height() / float(vh), 1.0)
        w, h = max(1, round(vw * s)), max(1, round(vh * s))
        return QRectF(self.width() / 2.0 - w / 2.0, self.height() / 2.0 - h / 2.0, w, h)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), rem(0.5), rem(0.5))
        p.fillPath(path, QColor(theme.STAGE_DARK))
        r = self.frame_rect()
        if self.img is not None:
            p.save()
            p.translate(r.center())
            p.rotate(self.ed.rotate)
            w, h = (r.height(), r.width()) if self.ed.rotate % 180 else (r.width(), r.height())
            p.drawImage(QRectF(-w / 2.0, -h / 2.0, w, h), self.img)
            p.restore()
        if self.ed.crop_on:
            self.ed.frame.paint(p, r)
        if self.message:
            box = QRectF(rem(0.75), self.height() - rem(0.75) - rem(2.6), self.width() - rem(1.5), rem(2.6))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 178))
            p.drawRoundedRect(box, 10, 10)
            p.setPen(QColor("#FFFFFF"))
            p.setFont(font(0.85))
            p.drawText(box, Qt.AlignCenter | Qt.TextWordWrap, self.message)
        p.end()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        r = self.frame_rect()
        if self.ed.crop_on:
            mode = self.ed.frame.hit(e.position(), r)
            if mode:
                self.ed.frame.begin(mode, e.position(), r)
                return
        if r.contains(e.position()):
            self.ed.toggle_play()

    def mouseMoveEvent(self, e):
        r = self.frame_rect()
        if self.ed.crop_on and self.ed.frame.mode:
            self.ed.frame.move(e.position(), r)
            self.update()
        elif self.ed.crop_on:
            mode = self.ed.frame.hit(e.position(), r)
            self.setCursor(self.ed.frame.cursor(mode) if mode else Qt.PointingHandCursor)
        else:
            self.setCursor(Qt.PointingHandCursor if r.contains(e.position()) else Qt.ArrowCursor)

    def mouseReleaseEvent(self, _e):
        self.ed.frame.end()


class _Track(QWidget):
    """Лента кадров с двумя ручками (как в Telegram) и бегунком."""

    def __init__(self, ed):
        super().__init__()
        self.ed = ed
        self.frames = []
        self.drag = None
        self.setFixedHeight(rem(3.6))
        self.setMouseTracking(True)

    def pos_of(self, x):
        return max(0.0, min(1.0, x / float(max(1, self.width()))))

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        r = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(r, rem(0.5), rem(0.5))
        p.setClipPath(path)
        p.fillPath(path, QColor(theme.STAGE_DARK))
        n = len(self.frames)
        if n:
            w = r.width() / n
            for i, img in enumerate(self.frames):
                if img is None:
                    continue
                cell = QRectF(i * w, 0, w, r.height())
                pm = QPixmap.fromImage(img).scaled(int(w) + 1, int(r.height()) + 1, Qt.KeepAspectRatioByExpanding,
                                                   Qt.SmoothTransformation)
                p.drawPixmap(cell, pm, QRectF((pm.width() - cell.width()) / 2, (pm.height() - cell.height()) / 2,
                                              cell.width(), cell.height()))
        d = self.ed.duration
        if d:
            a, b = self.ed.start / d, (self.ed.end or d) / d
            p.fillRect(QRectF(0, 0, a * r.width(), r.height()), QColor(15, 32, 39, 173))
            p.fillRect(QRectF(b * r.width(), 0, (1 - b) * r.width(), r.height()), QColor(15, 32, 39, 173))
            amber = QColor(AMBER)
            t = 3
            p.fillRect(QRectF(a * r.width(), 0, (b - a) * r.width(), t), amber)
            p.fillRect(QRectF(a * r.width(), r.height() - t, (b - a) * r.width(), t), amber)
            hw = rem(1.0)
            for x in (a * r.width(), b * r.width()):
                hr = QRectF(x - hw / 2, 0, hw, r.height())
                p.setPen(Qt.NoPen)
                p.setBrush(amber)
                p.drawRoundedRect(hr, rem(0.3), rem(0.3))
                grip = QRectF(x - rem(0.09), r.height() / 2 - rem(0.6), rem(0.18), rem(1.2))
                p.setBrush(QColor(19, 42, 50, 140))
                p.drawRoundedRect(grip, 1, 1)
            ph = self.ed.position / d * r.width()
            p.fillRect(QRectF(ph - 1, 0, 2, r.height()), QColor(0, 0, 0, 90))
            p.fillRect(QRectF(ph - 1, 0, 2, r.height()), QColor("#FFFFFF"))
        p.end()

    def mousePressEvent(self, e):
        d = self.ed.duration
        if not d or e.button() != Qt.LeftButton:
            return
        a, b = self.ed.start / d * self.width(), (self.ed.end or d) / d * self.width()
        hw = rem(1.0) / 2 + 3
        if abs(e.position().x() - a) <= hw:
            self.drag = "left"
        elif abs(e.position().x() - b) <= hw:
            self.drag = "right"
        else:
            self.drag = None
            self.ed.seek(self.pos_of(e.position().x()) * d)

    def mouseMoveEvent(self, e):
        d = self.ed.duration
        if not d:
            return
        if self.drag:
            t = self.pos_of(e.position().x()) * d
            gap = min(0.5, d / 20.0)
            if self.drag == "left":
                self.ed.start = min(t, (self.ed.end or d) - gap)
                self.ed.seek(self.ed.start)
            else:
                self.ed.end = max(t, self.ed.start + gap)
                self.ed.seek(self.ed.end)
            self.ed.paint_track()
        else:
            a, b = self.ed.start / d * self.width(), (self.ed.end or d) / d * self.width()
            near = min(abs(e.position().x() - a), abs(e.position().x() - b)) <= rem(1.0) / 2 + 3
            self.setCursor(Qt.SizeHorCursor if near else Qt.PointingHandCursor)

    def mouseReleaseEvent(self, _e):
        self.drag = None


class VideoEditor(Modal):
    """Обрезка по времени, рамка кадра и поворот. run() → правки, None (без правок) или CANCELLED."""

    def __init__(self, parent, path, current=None):
        super().__init__(parent, "Обрезка видео", "video", width_rem=52)
        current = current or {}
        self.path = path
        self.result = CANCELLED
        self.info = {}
        self.duration = 0.0
        self.position = 0.0
        self.start = float(current.get("trim_start") or 0)
        self.end = float(current.get("trim_end") or 0)
        self.rotate = int(current.get("rotate") or 0) % 360
        self.frame = CropFrame()
        self.crop_on = bool(current.get("crop"))
        if current.get("crop"):
            self._restore_crop(current["crop"])
        self.player = None
        self._gen = 0
        self.stage = _VideoStage(self)
        self.stage.setFixedHeight(min(rem(26), int(_vh() * 0.46)))
        self.body.addWidget(self.stage)
        panel = QHBoxLayout()
        panel.setSpacing(rem(0.4))
        self.play_btn = tool_button("play", "Просмотр фрагмента", self.toggle_play)
        self.crop_btn = tool_button("crop", "Рамка кадра", self.toggle_crop)
        self.rotate_btn = tool_button("rotate", "Повернуть на 90°", self.turn)
        for b in (self.play_btn, self.crop_btn, self.rotate_btn):
            panel.addWidget(b)
        panel.addStretch(1)
        self.time = label("0:00", 0.78, theme.TEXT_LABEL, bold=True)
        panel.addWidget(self.time)
        self.body.addLayout(panel)
        self.track = _Track(self)
        self.body.addWidget(self.track)
        self.ratios = QWidget()
        self.ratios.setStyleSheet("background: transparent;")
        rl = QHBoxLayout(self.ratios)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(rem(0.35))
        self.ratio_btns = []
        for key, text, value in (("free", "Свободно", None), ("1:1", "1:1", 1.0), ("4:3", "4:3", 4 / 3.0),
                                 ("16:9", "16:9", 16 / 9.0), ("9:16", "9:16", 9 / 16.0)):
            b = QToolButton()
            b.setText(text)
            b.setCursor(Qt.PointingHandCursor)
            b.setFont(font(0.75))
            b.clicked.connect(lambda _=False, v=value, btn=b: self.pick_ratio(v, btn))
            rl.addWidget(b)
            self.ratio_btns.append(b)
        rl.addStretch(1)
        self._paint_ratios(self.ratio_btns[0])
        self.body.addWidget(self.ratios)
        self.body.addWidget(label("Обрезка и поворот применяются при сжатии видео — тем же проходом FFmpeg.",
                                  0.75, theme.TEXT_MUTED, wrap=True))
        self.body.setSpacing(rem(0.5))
        self.add_button("Сбросить", "ghost", "refresh", self._reset)
        self.add_button("Отмена", "ghost", None, self._cancel)
        self.add_button("Готово", "primary", "check", self._done)
        _equal_foot(self)
        self.ratios.setVisible(self.crop_on)
        _btn_on(self.crop_btn, self.crop_on)
        _btn_on(self.rotate_btn, self.rotate != 0)
        self._seek_timer = QTimer(self)
        self._seek_timer.setSingleShot(True)
        self._seek_timer.timeout.connect(self._grab)
        self._tick = QTimer(self)
        self._tick.setInterval(50)
        self._tick.timeout.connect(self._sync_player)
        jobs.run(_video_info, path, done=self._loaded, failed=self._failed, owner=self)

    def run(self):
        self.exec()
        self._stop_player()
        return self.result

    def dims(self):
        return self.info.get("width") or 0, self.info.get("height") or 0

    # ── загрузка ─────────────────────────────────────────────────────────────

    def _failed(self, _e=None):
        self.stage.message = "Видео не открылось в редакторе — его можно отправить без правок"
        self.stage.update()

    def _loaded(self, info):
        self.info = info or {}
        self.duration = float(self.info.get("duration") or 0)
        if not self.duration:
            self._failed()
            return
        if not self.end or self.end > self.duration:
            self.end = self.duration
        self.paint_track()
        self.seek(self.start)
        self._strip()

    def _strip(self):
        count = min(12, max(6, round(self.track.width() / 90.0))) or 8
        self.track.frames = [None] * count
        d = self.duration

        def grab(i):
            return i, _frame_at(self.path, d * (i + 0.5) / count, side=160)

        def done(res):
            i, img = res
            if i < len(self.track.frames):
                self.track.frames[i] = img
                self.track.update()
        for i in range(count):
            jobs.run(grab, i, done=done, failed=lambda e: None, owner=self)

    def seek(self, t):
        self.position = max(0.0, min(self.duration or 0.0, t))
        if self.player is not None and self._playing():
            self.player.setPosition(int(self.position * 1000))
        self.track.update()
        self._seek_timer.start(60)

    def _grab(self):
        self._gen += 1
        gen = self._gen

        def done(img):
            if gen == self._gen and img is not None:
                self.stage.img = img
                self.stage.update()
        jobs.run(_frame_at, self.path, self.position, done=done, failed=lambda e: None, owner=self)

    def paint_track(self):
        d = self.duration or 0
        end = self.end or d
        self.time.setText("%s – %s  ·  длительность %s" % (fmt_time(self.start), fmt_time(end), fmt_time(end - self.start)))
        self.track.update()

    # ── просмотр (системный проигрыватель, если он умеет этот формат) ─────────

    def _playing(self):
        try:
            from PySide6.QtMultimedia import QMediaPlayer
            return self.player is not None and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        except Exception:
            return False

    def toggle_play(self):
        if self._playing():
            self.player.pause()
            self._tick.stop()
            self._paint_play(False)
            self._grab()
            return
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
            from PySide6.QtCore import QUrl
        except Exception:
            self.stage.message = "Просмотр недоступен — правки всё равно применятся при сжатии"
            self.stage.update()
            return
        if self.player is None:
            self.player = QMediaPlayer(self)
            self.audio = QAudioOutput(self)
            self.player.setAudioOutput(self.audio)
            self.sink = QVideoSink(self)              # кадры проигрывателя → сцена редактора (поверх — рамка)
            self.sink.videoFrameChanged.connect(self._frame)
            self.player.setVideoSink(self.sink)
            self.player.errorOccurred.connect(self._player_error)
            self.player.setSource(QUrl.fromLocalFile(self.path))
        stop = self.end or self.duration
        if self.position < self.start - 0.05 or self.position >= stop - 0.05:
            self.position = self.start
        self.player.setPosition(int(self.position * 1000))
        self.player.play()
        self._tick.start()
        self._paint_play(True)

    def _frame(self, frame):
        try:
            img = frame.toImage()
        except Exception:
            return
        if not img.isNull():
            self.stage.img = _upright(frame, img)
            self.stage.update()

    def _player_error(self, *_a):
        self._tick.stop()
        self._paint_play(False)
        self.stage.message = "Видео не воспроизводится на этом компьютере — обрезка и поворот всё равно применятся"
        self.stage.update()

    def _sync_player(self):
        if self.player is None:
            return
        self.position = self.player.position() / 1000.0
        if self.position >= (self.end or self.duration):
            self.player.pause()
            self._tick.stop()
            self._paint_play(False)
        self.track.update()

    def _stop_player(self):
        try:
            if self.player is not None:
                self.player.stop()
        except RuntimeError:
            pass

    def _paint_play(self, playing):
        self.play_btn.glyph = "pause" if playing else "play"
        self.play_btn.setToolTip("Пауза" if playing else "Просмотр фрагмента")
        _btn_on(self.play_btn, playing)

    # ── рамка и поворот ──────────────────────────────────────────────────────

    def toggle_crop(self):
        self.crop_on = not self.crop_on
        if not self.crop_on:
            self.frame.reset()
        _btn_on(self.crop_btn, self.crop_on)
        self.ratios.setVisible(self.crop_on)
        self.stage.update()
        QTimer.singleShot(0, self._center)

    def pick_ratio(self, value, btn):
        self._paint_ratios(btn)
        self.crop_on = True
        _btn_on(self.crop_btn, True)
        self.ratios.setVisible(True)
        r = self.stage.frame_rect()
        self.frame.set_ratio(value, r.width() / max(1.0, r.height()))
        self.stage.update()

    def _paint_ratios(self, current):
        for x in self.ratio_btns:
            on = x is current
            bg, fg = (theme.INFO_SOFT, theme.INFO) if on else (theme.CHIP_BG, theme.BTN_GHOST_TEXT)
            x.setStyleSheet(f"QToolButton {{ background: {bg}; color: {fg}; border: none; border-radius: {rem(0.6)}px;"
                            f" padding: {rem(0.2)}px {rem(0.65)}px; }}")

    def turn(self):
        self.rotate = (self.rotate + 90) % 360
        _btn_on(self.rotate_btn, self.rotate != 0)
        self.frame.reset()
        self.stage.update()

    def _restore_crop(self, crop):
        """Сохранённая рамка — в показанный (повёрнутый) кадр."""
        try:
            x, y, w, h = (float(crop.get(k, 0)) for k in ("x", "y", "w", "h"))
        except (TypeError, ValueError):
            return
        rot = self.rotate
        if rot == 90:
            self.frame.r = [1 - y - h, x, h, w]
        elif rot == 180:
            self.frame.r = [1 - x - w, 1 - y - h, w, h]
        elif rot == 270:
            self.frame.r = [y, 1 - x - w, h, w]
        else:
            self.frame.r = [x, y, w, h]

    def _source_crop(self):
        """Рамка в кадре до поворота: FFmpeg сначала обрезает, потом поворачивает (как сервер)."""
        r = self.frame.rect()
        if not r:
            return None
        u, v, w, h = r["x"], r["y"], r["w"], r["h"]
        rot = self.rotate
        if rot == 90:
            x, y, cw, ch = v, 1 - u - w, h, w
        elif rot == 180:
            x, y, cw, ch = 1 - u - w, 1 - v - h, w, h
        elif rot == 270:
            x, y, cw, ch = 1 - v - h, u, h, w
        else:
            x, y, cw, ch = u, v, w, h
        return {"x": round(x, 4), "y": round(y, 4), "w": round(cw, 4), "h": round(ch, 4)}

    # ── кнопки ───────────────────────────────────────────────────────────────

    def _reset(self, _m):
        self.result = None           # «Сбросить» снимает все правки: файл уйдёт как есть
        return True

    def _cancel(self, _m):
        self.result = CANCELLED
        return True

    def _done(self, _m):
        rect = self._source_crop() if self.crop_on else None
        d = self.duration
        trimmed = self.start > 0.05 or (self.end and d and self.end < d - 0.05)
        if not trimmed and not rect and not self.rotate:
            self.result = None
            return True
        self.result = {"trim_start": round(self.start, 3),
                       "trim_end": round(self.end, 3) if self.end and d and self.end < d else 0,
                       "crop": rect, "rotate": self.rotate}
        return True




def is_image(name):
    return _img_name(name)
