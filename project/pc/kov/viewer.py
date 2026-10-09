# -*- coding: utf-8 -*-
"""Просмотр фото и видео «Свет кадра» — как viewer() из js/core/files.js:
фон светится цветами снимка, управление белое и тихое, лента снимков снизу,
сведения справа (клавиша I), приближение колесом и двойным щелчком,
перетаскивание приближенного снимка; у видео — своё управление (шкала
в цвет кадра, время, звук, скорость, во весь экран).

Видео играет QtMultimedia (Qt 6: свой FFmpeg из PySide6, без кодеков Windows).
"""

import colorsys

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
                         QPixmap, QRadialGradient)
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QToolButton, QVBoxLayout, QWidget

from general_functions import theme
from kov import icons, jobs
from kov.api import client
from kov.fmt import parse_date
from kov.ui import font, rem

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget
    MULTIMEDIA = True
except Exception:  # без QtMultimedia видео открывается программой компьютера
    MULTIMEDIA = False

MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
          "ноября", "декабря")


def _when(at):
    d = parse_date(at)
    if not d:
        return ""
    from datetime import date
    t = d.strftime("%H:%M")
    if d.date() == date.today():
        return "сегодня в " + t
    return "%d %s в %s" % (d.day, MONTHS[d.month - 1], t)


def _hue(img):
    """Цвет кадра: средний, насыщенные точки весомее (как frameHue)."""
    try:
        small = img.scaled(24, 24, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).convertToFormat(QImage.Format_RGB32)
        r = g = b = w = 0.0
        for y in range(small.height()):
            for x in range(small.width()):
                c = QColor(small.pixel(x, y))
                cr, cg, cb = c.red(), c.green(), c.blue()
                wt = 1 + 4 * (max(cr, cg, cb) - min(cr, cg, cb)) / 255.0
                r += cr * wt
                g += cg * wt
                b += cb * wt
                w += wt
        r, g, b = r / (w * 255), g / (w * 255), b / (w * 255)
        if max(r, g, b) == min(r, g, b):
            return None
        h, l, s = colorsys.rgb_to_hls(r, g, b)
        return int(h * 360), max(0.6, s)
    except Exception:
        return None


def _hsl(h, s, l):
    r, g, b = colorsys.hls_to_rgb((h % 360) / 360.0, l, s)
    return QColor(int(r * 255), int(g * 255), int(b * 255))


class _RoundBtn(QToolButton):
    """Белая кнопка на тёмном фоне: круг при наведении."""

    def __init__(self, glyph, tip, size_rem=2.5, icon_rem=1.35, parent=None, fill=False):
        super().__init__(parent)
        self._glyph, self._fill = glyph, fill
        self.setToolTip(tip)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(rem(size_rem), rem(size_rem))
        self.setIconSize(QSize(rem(icon_rem), rem(icon_rem)))
        self.setIcon(icons.icon(glyph, rem(icon_rem), "#FFFFFF", fill=fill))
        r = rem(size_rem) // 2
        self.setStyleSheet(f"QToolButton {{ border: none; background: transparent; border-radius: {r}px; }}"
                           f"QToolButton:hover {{ background: rgba(255,255,255,36); }}")

    def set_glyph(self, glyph, fill=False):
        self._glyph = glyph
        self.setIcon(icons.icon(glyph, self.iconSize().width(), "#FFFFFF", fill=fill))


class _ImageStage(QWidget):
    """Снимок по центру (вписан), приближение 1–6×, перетаскивание."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pm = None
        self.s = 1.0
        self.tx = self.ty = 0.0
        self._drag = None
        self.message = ""
        self.setMouseTracking(True)

    def set_image(self, img):
        self.pm = QPixmap.fromImage(img) if img is not None else None
        self.s, self.tx, self.ty = 1.0, 0.0, 0.0
        self.message = ""
        self.update()

    def set_message(self, text):
        self.pm = None
        self.message = text
        self.update()

    def _fit_rect(self):
        if self.pm is None:
            return QRectF()
        w, h = self.pm.width(), self.pm.height()
        k = min(self.width() / max(1, w), self.height() / max(1, h))
        dw, dh = w * k, h * k
        return QRectF((self.width() - dw) / 2, (self.height() - dh) / 2, dw, dh)

    def _clamp(self):
        r = self._fit_rect()
        mx = max(0.0, (r.width() * self.s - self.width()) / 2)
        my = max(0.0, (r.height() * self.s - self.height()) / 2)
        self.tx = max(-mx, min(mx, self.tx))
        self.ty = max(-my, min(my, self.ty))

    def zoom_at(self, ns, pos):
        ns = max(1.0, min(6.0, ns))
        cx, cy = self.width() / 2, self.height() / 2
        px, py = pos.x() - cx, pos.y() - cy
        k = ns / self.s
        self.tx = px - (px - self.tx) * k
        self.ty = py - (py - self.ty) * k
        self.s = ns
        if self.s <= 1.001:
            self.s, self.tx, self.ty = 1.0, 0.0, 0.0
        self._clamp()
        self.update()

    def zoomed(self):
        return self.s > 1.001

    def wheelEvent(self, e):
        if self.pm is None:
            return
        self.zoom_at(self.s * (1.2 if e.angleDelta().y() > 0 else 1 / 1.2), e.position().toPoint())

    def mouseDoubleClickEvent(self, e):
        if self.pm is not None:
            self.zoom_at(1.0 if self.zoomed() else 2.5, e.position().toPoint())

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.zoomed():
            self._drag = (e.position().toPoint(), self.tx, self.ty)
            self.setCursor(Qt.ClosedHandCursor)
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            start, tx, ty = self._drag
            self.tx = tx + e.position().toPoint().x() - start.x()
            self.ty = ty + e.position().toPoint().y() - start.y()
            self._clamp()
            self.update()
        else:
            self.setCursor(Qt.OpenHandCursor if self.zoomed() else Qt.ArrowCursor)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag = None
        self.setCursor(Qt.OpenHandCursor if self.zoomed() else Qt.ArrowCursor)
        super().mouseReleaseEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.setRenderHint(QPainter.Antialiasing, True)
        if self.pm is None:
            p.setPen(QColor("#ECEFF1"))
            p.setFont(font(0.95))
            p.drawText(self.rect().adjusted(rem(1), 0, -rem(1), 0), Qt.AlignCenter | Qt.TextWordWrap,
                       self.message or "Загрузка…")
            p.end()
            return
        r = self._fit_rect()
        cx, cy = self.width() / 2, self.height() / 2
        p.translate(cx + self.tx, cy + self.ty)
        p.scale(self.s, self.s)
        p.translate(-cx, -cy)
        p.drawPixmap(r, self.pm, QRectF(self.pm.rect()))
        p.end()


class _Scrub(QWidget):
    """Шкала перемотки: буфер, заполненная часть в цвет кадра, бегунок."""

    seek = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(rem(1.5))
        self.setCursor(Qt.PointingHandCursor)
        self.pos_ = 0.0
        self.colors = (QColor("#ffb86b"), QColor("#ff7aa8"))
        self._down = False

    def set_pos(self, p):
        self.pos_ = max(0.0, min(1.0, p))
        self.update()

    def _at(self, x):
        return max(0.0, min(1.0, x / float(max(1, self.width()))))

    def mousePressEvent(self, e):
        self._down = True
        self.seek.emit(self._at(e.position().toPoint().x()))

    def mouseMoveEvent(self, e):
        if self._down:
            self.seek.emit(self._at(e.position().toPoint().x()))

    def mouseReleaseEvent(self, e):
        self._down = False

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        h = max(3, rem(0.32))
        y = (self.height() - h) / 2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 56))
        p.drawRoundedRect(QRectF(0, y, self.width(), h), h / 2, h / 2)
        w = self.width() * self.pos_
        if w > 0:
            g = QLinearGradient(0, 0, self.width(), 0)
            g.setColorAt(0, self.colors[0])
            g.setColorAt(1, self.colors[1])
            p.setBrush(QBrush(g))
            p.drawRoundedRect(QRectF(0, y, w, h), h / 2, h / 2)
        k = rem(0.85)
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QRectF(w - k / 2, (self.height() - k) / 2, k, k))
        p.end()


class _VideoStage(QWidget):
    """Видео и его управление (шкала, время, звук, скорость, во весь экран)."""

    fullscreen = Signal()
    activity = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(rem(0.5))
        self.video = QVideoWidget()
        self.video.setStyleSheet("background: #000;")
        self.video.installEventFilter(self)
        lay.addWidget(self.video, 1)
        self.error = QLabel("")
        self.error.setAlignment(Qt.AlignCenter)
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #ECEFF1; background: transparent;")
        self.error.setFont(font(0.95))
        self.error.hide()
        lay.addWidget(self.error, 1)
        ctl = QWidget()
        ctl.setStyleSheet("background: transparent;")
        cl = QVBoxLayout(ctl)
        cl.setContentsMargins(rem(1.1), 0, rem(1.1), 0)
        cl.setSpacing(rem(0.15))
        self.scrub = _Scrub()
        self.scrub.seek.connect(self._seek)
        cl.addWidget(self.scrub)
        row = QHBoxLayout()
        row.setSpacing(rem(0.4))
        self.play_btn = _RoundBtn("pause", "Пауза (пробел)", 2.5, 1.3, fill=True)
        self.play_btn.clicked.connect(self.toggle)
        row.addWidget(self.play_btn)
        self.mute_btn = _RoundBtn("volume", "Звук (M)", 2.5, 1.3)
        self.mute_btn.clicked.connect(self.toggle_mute)
        row.addWidget(self.mute_btn)
        self.speed = QToolButton()
        self.speed.setText("1×")
        self.speed.setCursor(Qt.PointingHandCursor)
        self.speed.setFont(font(0.8, bold=True))
        self.speed.setToolTip("Скорость")
        self.speed.setStyleSheet(f"QToolButton {{ color: #fff; background: rgba(255,255,255,40); border: none;"
                                 f" border-radius: {rem(0.8)}px; padding: {rem(0.25)}px {rem(0.7)}px; }}"
                                 f"QToolButton:hover {{ background: rgba(255,255,255,70); }}")
        self.speed.clicked.connect(self._speed)
        row.addWidget(self.speed)
        self.times = QLabel("0:00")
        self.times.setFont(font(0.8))
        self.times.setStyleSheet("color: rgba(255,255,255,220); background: transparent;")
        row.addWidget(self.times)
        row.addStretch(1)
        self.full_btn = _RoundBtn("zoom", "Во весь экран (F)", 2.5, 1.3)
        self.full_btn.clicked.connect(self.fullscreen.emit)
        row.addWidget(self.full_btn)
        cl.addLayout(row)
        lay.addWidget(ctl)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)          # Qt 6: звук — отдельный выход
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.positionChanged.connect(self._sync)
        self.player.durationChanged.connect(self._sync)
        self.player.playbackStateChanged.connect(self._state)
        self.player.errorOccurred.connect(self._failed)
        self._speeds = [1.0, 1.5, 2.0, 0.5]
        self.on_fail = None

    def eventFilter(self, obj, e):
        if obj is self.video:
            if e.type() == QEvent.MouseButtonDblClick:
                self.fullscreen.emit()
                return True
            if e.type() == QEvent.MouseButtonRelease and e.button() == Qt.LeftButton:
                self.toggle()
                return True
            if e.type() == QEvent.MouseMove:
                self.activity.emit()
        return super().eventFilter(obj, e)

    def load(self, link):
        self.error.hide()
        self.video.show()
        self.player.setSource(QUrl(link))
        self.player.play()

    def stop(self):
        try:
            self.player.stop()
            self.player.setSource(QUrl())
        except Exception:
            pass

    def toggle(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()
        self.activity.emit()

    def toggle_mute(self):
        self.audio.setMuted(not self.audio.isMuted())
        self.mute_btn.set_glyph("close" if self.audio.isMuted() else "volume")
        self.activity.emit()

    def seek_by(self, sec):
        self.player.setPosition(max(0, min(self.player.duration(), self.player.position() + int(sec * 1000))))

    def _seek(self, frac):
        d = self.player.duration()
        if d > 0:
            self.player.setPosition(int(frac * d))
        self.activity.emit()

    def _speed(self):
        cur = self.player.playbackRate() or 1.0
        k = (self._speeds.index(cur) + 1) % len(self._speeds) if cur in self._speeds else 0
        self.player.setPlaybackRate(self._speeds[k])
        self.speed.setText(("%g" % self._speeds[k]).replace(".", ",") + "×")
        self.activity.emit()

    @staticmethod
    def _fmt(ms):
        s = max(0, int(ms // 1000))
        return "%d:%02d" % (s // 60, s % 60)

    def _sync(self, *_):
        d, pos = self.player.duration(), self.player.position()
        self.scrub.set_pos(pos / float(d) if d > 0 else 0.0)
        self.times.setText(self._fmt(pos) + ("  /  −" + self._fmt(d - pos) if d > 0 else ""))

    def _state(self, st):
        playing = st == QMediaPlayer.PlaybackState.PlayingState
        self.play_btn.set_glyph("pause" if playing else "play", fill=True)
        self.play_btn.setToolTip("Пауза (пробел)" if playing else "Смотреть (пробел)")
        self.activity.emit()

    def _failed(self, *_):
        text = self.player.errorString() or "формат не поддерживается"
        self.video.hide()
        self.error.setText("Видео не открылось (%s).\nСкачайте его или откройте программой компьютера." % text)
        self.error.show()
        if self.on_fail:
            self.on_fail()

    def playing(self):
        return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState


class MediaViewer(QWidget):
    """Просмотр поверх окна. Открывать: MediaViewer.show_list(владелец, файлы, номер)."""

    closed = Signal()

    @classmethod
    def show_list(cls, owner, files, index=0, actions=None):
        win = owner.window()
        host = win.centralWidget() if hasattr(win, "centralWidget") and win.centralWidget() is not None else win
        v = cls(host, files, index, actions)
        v.setGeometry(host.rect())
        v.show()
        v.raise_()
        v.setFocus()
        return v

    def __init__(self, host, files, index=0, actions=None):
        super().__init__(host)
        from kov import files as kfiles
        self.kf = kfiles
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.list = list(files or [])
        self.i = max(0, min(len(self.list) - 1, int(index or 0)))
        self.actions = actions or {}
        self.amb = None
        self.colors = (QColor("#ffb86b"), QColor("#ff7aa8"))
        self.info_on = False
        self.idle = False
        self._gen = 0
        host.installEventFilter(self)

        # ── шапка ────────────────────────────────────────────────────────────
        self.top = QWidget(self)
        self.top.setStyleSheet("background: transparent;")
        tl = QHBoxLayout(self.top)
        tl.setContentsMargins(rem(1.0), 0, rem(1.0), 0)
        tl.setSpacing(rem(0.6))
        self.back = _RoundBtn("arrow_back", "Закрыть (Esc)")
        self.back.clicked.connect(self.close_viewer)
        tl.addWidget(self.back)
        who = QVBoxLayout()
        who.setSpacing(0)
        self.who = QLabel()
        self.who.setFont(font(0.94, bold=True))
        self.who.setStyleSheet("color: #fff; background: transparent;")
        self.when = QLabel()
        self.when.setFont(font(0.78))
        self.when.setStyleSheet("color: rgba(255,255,255,190); background: transparent;")
        who.addWidget(self.who)
        who.addWidget(self.when)
        tl.addLayout(who, 1)
        self.counter = QLabel()
        self.counter.setFont(font(0.78))
        # «2 из 5» — просто текстом, без подложки: на светлом кадре плашка выглядела выделением
        self.counter.setStyleSheet(f"color: rgba(255,255,255,225); background: transparent; padding: 0 {rem(0.4)}px;")
        tl.addWidget(self.counter)
        self.info_btn = _RoundBtn("info", "Сведения (I)")
        self.info_btn.clicked.connect(self.toggle_info)
        tl.addWidget(self.info_btn)
        if self.actions.get("reply"):
            b = _RoundBtn("undo", "Ответить")
            b.clicked.connect(lambda: self._act("reply"))
            tl.addWidget(b)
        if self.actions.get("forward"):
            b = _RoundBtn("send", "Переслать")
            b.clicked.connect(lambda: self._act("forward"))
            tl.addWidget(b)
        self.dl_btn = _RoundBtn("download", "Скачать")
        self.dl_btn.clicked.connect(self.download)
        tl.addWidget(self.dl_btn)
        self.del_btn = None
        if self.actions.get("remove"):
            self.del_btn = _RoundBtn("trash", "Удалить")
            self.del_btn.clicked.connect(lambda: self._act("remove"))
            tl.addWidget(self.del_btn)
        self.close_btn = _RoundBtn("close", "Закрыть (Esc)")
        self.close_btn.clicked.connect(self.close_viewer)
        tl.addWidget(self.close_btn)

        # ── сцена ────────────────────────────────────────────────────────────
        self.image = _ImageStage(self)
        self.video = _VideoStage(self) if MULTIMEDIA else None
        if self.video is not None:
            self.video.hide()
            self.video.fullscreen.connect(self.toggle_fullscreen)
            self.video.activity.connect(self.wake)
        self.prev = _RoundBtn("chevron_left", "Предыдущий (←)", 3.5, 1.6)
        self.prev.setParent(self)
        self.prev.clicked.connect(lambda: self.show_at(self.i - 1))
        self.next = _RoundBtn("chevron_right", "Следующий (→)", 3.5, 1.6)
        self.next.setParent(self)
        self.next.clicked.connect(lambda: self.show_at(self.i + 1))
        for b in (self.prev, self.next):
            b.setStyleSheet(b.styleSheet().replace("background: transparent;", "background: rgba(255,255,255,36);"))

        # ── лента ────────────────────────────────────────────────────────────
        self.strip = QScrollArea(self)
        self.strip.setFrameShape(QFrame.NoFrame)
        self.strip.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.strip.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.strip.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.strip_inner = QWidget()
        self.strip_inner.setStyleSheet("background: transparent;")
        self.strip_lay = QHBoxLayout(self.strip_inner)
        self.strip_lay.setContentsMargins(rem(1), rem(0.45), rem(1), rem(0.45))
        self.strip_lay.setSpacing(rem(0.5))
        self.strip.setWidget(self.strip_inner)
        self.strip.setWidgetResizable(True)
        self.thumbs = []

        # ── сведения ─────────────────────────────────────────────────────────
        self.info = QFrame(self)
        self.info.setStyleSheet("QFrame { background: rgba(10,12,18,170); }")
        il = QVBoxLayout(self.info)
        il.setContentsMargins(rem(1.6), rem(5.2), rem(1.6), rem(1.5))
        il.setSpacing(rem(0.5))
        self.info_name = QLabel()
        self.info_name.setFont(font(1.0, bold=True))
        self.info_when = QLabel()
        self.info_cap = QLabel()
        self.info_file = QLabel()
        self.info_dims = QLabel()
        for lab, size in ((self.info_name, 1.0), (self.info_when, 0.8), (self.info_cap, 0.9),
                          (self.info_file, 0.85), (self.info_dims, 0.8)):
            if lab is not self.info_name:
                lab.setFont(font(size))
            lab.setWordWrap(True)
            lab.setStyleSheet("color: #fff; background: transparent;")
            lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
            il.addWidget(lab)
        il.addStretch(1)
        self.info.hide()

        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(self._go_idle)
        self._fill_strip()
        self.show_at(self.i)

    # ── раскладка ────────────────────────────────────────────────────────────

    def eventFilter(self, obj, e):
        if obj is self.parentWidget() and e.type() == QEvent.Resize:
            self.setGeometry(self.parentWidget().rect())
        return super().eventFilter(obj, e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._relayout()

    def _relayout(self):
        W, H = self.width(), self.height()
        top_h = rem(4.5)
        self.top.setGeometry(0, 0, W, top_h)
        info_w = rem(22.25) if self.info_on else 0
        self.info.setGeometry(W - rem(22.25), 0, rem(22.25), H)
        has_strip = len(self.list) > 1
        bottom = rem(6.0)
        side = rem(6.5)
        stage = QRect(side, top_h, W - 2 * side - (info_w - rem(1.5) if info_w else 0), H - top_h - bottom)
        self.image.setGeometry(stage)
        if self.video is not None:
            self.video.setGeometry(QRect(side, top_h, stage.width(), H - top_h - rem(1.0) - (rem(4.4) if has_strip else 0)))
        nav = rem(3.5)
        self.prev.move(rem(1.5), (H - nav) // 2)
        self.next.move(W - rem(1.5) - nav - (info_w if info_w else 0), (H - nav) // 2)
        self.strip.setGeometry(0, H - rem(4.6), W - info_w, rem(4.4))
        self.prev.setVisible(has_strip and not self.idle)
        self.next.setVisible(has_strip and not self.idle)
        self.strip.setVisible(has_strip and not self.idle)
        self.top.setVisible(not self.idle)
        self.info.raise_()
        self.top.raise_()

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#000"))
        if self.amb is not None:
            p.setOpacity(0.85)
            W, H = self.width(), self.height()
            pm = self.amb.scaled(int(W * 1.36), int(H * 1.36), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            p.drawPixmap((W - pm.width()) // 2, (H - pm.height()) // 2, pm)
            p.setOpacity(1.0)
        g = QRadialGradient(QPointF(self.width() / 2, self.height() / 2), max(self.width(), self.height()) * 0.7)
        g.setColorAt(0, QColor(0, 0, 0, 31))
        g.setColorAt(1, QColor(0, 0, 0, 153))
        p.fillRect(self.rect(), QBrush(g))
        p.end()

    # ── листание ─────────────────────────────────────────────────────────────

    def _fill_strip(self):
        if len(self.list) < 2:
            return
        for k, f in enumerate(self.list):
            b = QToolButton()
            b.setFixedSize(rem(3.375), rem(3.375))
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip("Снимок %d" % (k + 1))
            b.clicked.connect(lambda _=False, n=k: self.show_at(n))
            b.setStyleSheet(f"QToolButton {{ border: none; border-radius: {rem(0.6)}px; background: #1d2433; }}")
            self.strip_lay.addWidget(b)
            self.thumbs.append(b)
            self._thumb_into(b, f)
        self.strip_lay.insertStretch(0, 1)
        self.strip_lay.addStretch(1)

    def _thumb_into(self, b, f):
        side = rem(3.375)

        def put(img):
            if img is None or img.isNull():
                return
            from kov.files import _rounded
            b.setIcon(QPixmap(_rounded(QPixmap.fromImage(img), side, side, rem(0.6))))
            b.setIconSize(QSize(side, side))
        if f.get("src"):
            link = f.get("thumb") or (f["src"] if not self.kf.is_video(self.kf.file_name(f)) else None)
            if link:
                jobs.run(self._fetch_image, link, done=put, owner=self)
            return
        cached = self.kf.THUMBS.get(f)
        if cached is not None:
            put(cached)
            return
        key = self.kf.THUMBS.key(f)

        def ready(k, img):
            if k == key:
                put(img)
        self.kf.THUMBS.ready.connect(ready)
        self.kf.THUMBS.request(f)

    @staticmethod
    def _fetch_image(link):
        import requests
        res = requests.get(link, timeout=(10, 60), headers={"Authorization": "Bearer " + client.token} if client.token else {})
        res.raise_for_status()
        img = QImage()
        img.loadFromData(res.content)
        return img

    def show_at(self, n):
        if not self.list:
            return
        self.i = n % len(self.list)
        self._gen += 1
        gen = self._gen
        f = self.list[self.i]
        name = self.kf.file_name(f)
        video = self.kf.is_video(name)
        if self.video is not None:
            self.video.stop()
        who = f.get("who") or self.kf.uploader_name(f) or ""
        at = f.get("at") or f.get("created_at")
        self.who.setText(who or self.kf.safe_name(name))
        self.when.setText(_when(at) or (self.kf.safe_name(name) if who else ""))
        self.counter.setText("%d из %d" % (self.i + 1, len(self.list)))
        self.counter.setVisible(len(self.list) > 1)
        cap = str(f.get("caption") or "").strip()
        if cap.lower() in ("null", "none", "undefined"):
            cap = ""
        self.info_name.setText(who or "Файл")
        self.info_when.setText(_when(at))
        self.info_cap.setText(cap)
        self.info_cap.setVisible(bool(cap))
        self.info_file.setText(self.kf.safe_name(name))
        self.info_dims.setText(self.kf.fmt_size(f.get("size")) if f.get("size") else "")
        if self.del_btn is not None:
            can = self.actions.get("can_remove")
            self.del_btn.setVisible(not can or bool(can(f)))
        for k, b in enumerate(self.thumbs):
            on = k == self.i
            b.setStyleSheet(f"QToolButton {{ border: {'2px solid #fff' if on else 'none'}; border-radius: {rem(0.6)}px;"
                            f" background: #1d2433; }}")
            if on:
                self.strip.ensureWidgetVisible(b, rem(6), 0)
        if video and self.video is not None:
            self.image.hide()
            self.video.show()
            self.video.on_fail = lambda: None
            link = self.kf.url(f, "stream")
            self.video.load(link)
            poster = f.get("thumb") if f.get("src") else None
            if poster:
                jobs.run(self._fetch_image, poster, done=lambda img, g=gen: self._set_amb(img, g), owner=self)
            else:
                cached = self.kf.THUMBS.get(f) if not f.get("src") else None
                if cached is not None:
                    self._set_amb(cached, gen)
        elif video:
            self.image.show()
            self.image.set_message("Видео открывается программой компьютера…")
            self.kf.open_external(self, f)
        else:
            if self.video is not None:
                self.video.hide()
            self.image.show()
            self.image.set_message("Загрузка…")
            sources = [self.kf.url(f, "view"), self.kf.url(f, "download")] if not f.get("src") else [f["src"]]

            def load():
                last = None
                for link in sources:
                    try:
                        img = self._fetch_image(link)
                        if not img.isNull():
                            return img
                    except Exception as e:
                        last = e
                raise last or RuntimeError("пустой ответ")

            def done(img, g=gen):
                if g != self._gen:
                    return
                self.image.set_image(img)
                self._set_amb(img, g)
                self.info_dims.setText("%d × %d%s" % (img.width(), img.height(),
                                                      (", " + self.kf.fmt_size(f.get("size"))) if f.get("size") else ""))

            def fail(_e, g=gen):
                if g == self._gen:
                    self.image.set_message("Файл не открылся. Попробуйте скачать его.")
            jobs.run(load, done=done, failed=fail, owner=self)
        self._relayout()
        self.wake()

    def _set_amb(self, img, gen):
        if gen != self._gen or img is None or img.isNull():
            return
        small = img.scaled(32, 32, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        self.amb = QPixmap.fromImage(small)
        hue = _hue(img)
        if hue:
            h, s = hue
            self.colors = (_hsl(h, s, 0.62), _hsl(h + 38, s, 0.68))
            if self.video is not None:
                self.video.scrub.colors = self.colors
        self.update()

    # ── действия ─────────────────────────────────────────────────────────────

    def current(self):
        return self.list[self.i] if self.list else None

    def download(self):
        f = self.current()
        if f is not None:
            self.kf.download(self.parentWidget(), f)

    def _act(self, key):
        f = self.current()
        fn = self.actions.get(key)
        self.close_viewer()
        if fn and f is not None:
            fn(f)

    def toggle_info(self):
        self.info_on = not self.info_on
        self.info.setVisible(self.info_on)
        self._relayout()

    def toggle_fullscreen(self):
        win = self.window()
        if win.isFullScreen():
            win.showNormal()
        else:
            win.showFullScreen()

    def wake(self):
        if self.idle:
            self.idle = False
            self._relayout()
        self._idle_timer.stop()
        if self.video is not None and self.video.isVisible() and self.video.playing():
            self._idle_timer.start(2600)

    def _go_idle(self):
        if self.video is not None and self.video.isVisible() and self.video.playing():
            self.idle = True
            self._relayout()

    def mouseMoveEvent(self, e):
        self.wake()
        super().mouseMoveEvent(e)

    def mousePressEvent(self, e):
        # щелчок по пустому полю вокруг снимка закрывает просмотр
        if e.button() == Qt.LeftButton and not self.image.geometry().contains(e.position().toPoint()) and \
                not (self.video is not None and self.video.isVisible() and self.video.geometry().contains(e.position().toPoint())) \
                and not self.strip.geometry().contains(e.position().toPoint()) and not self.top.geometry().contains(e.position().toPoint()) \
                and not (self.info_on and self.info.geometry().contains(e.position().toPoint())):
            self.close_viewer()
            return
        super().mousePressEvent(e)

    def keyPressEvent(self, e):
        k = e.key()
        txt = e.text().lower()
        if k == Qt.Key_Escape:
            if self.window().isFullScreen() and self.video is not None and self.video.isVisible():
                self.window().showNormal()
            else:
                self.close_viewer()
        elif k == Qt.Key_Left and len(self.list) > 1:
            self.show_at(self.i - 1)
        elif k == Qt.Key_Right and len(self.list) > 1:
            self.show_at(self.i + 1)
        elif k == Qt.Key_Space and self.video is not None and self.video.isVisible():
            self.video.toggle()
        elif txt in ("m", "ь") and self.video is not None and self.video.isVisible():
            self.video.toggle_mute()
        elif txt in ("f", "а") and self.video is not None and self.video.isVisible():
            self.toggle_fullscreen()
        elif txt in ("i", "ш"):
            self.toggle_info()
        else:
            super().keyPressEvent(e)
            return
        self.wake()

    def close_viewer(self):
        if self.video is not None:
            self.video.stop()
        try:
            self.parentWidget().removeEventFilter(self)
        except Exception:
            pass
        self.hide()
        self.closed.emit()
        self.deleteLater()

