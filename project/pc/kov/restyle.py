# -*- coding: utf-8 -*-
"""Смена темы и размера интерфейса на ходу — без перезапуска приложения.

Как у сайта (js/core/theme.js): новая тема наплывает кругом от нажатой
кнопки за 420 мс. Окно снимается картинкой, тут же пересобирается в новой
теме, а снимок прежнего вида лежит сверху и уходит, открывая новый вид
расширяющимся кругом. Размер интерфейса меняется так же, только снимок
плавно гаснет. Раньше окно для этого перезапускалось целиком — отсюда
мигание и вылет, если перезапуск не удавался.

Тот же снимок — и при переходе между разделами (Cover): прежний раздел
остаётся на экране, пока новый подгружает данные, затем плавно уступает ему.
"""

import time

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget

import scaler
from general_functions import theme
from kov import jobs

SWEEP_MS = 420          # --theme-anim сайта
FADE_MS = 260
WAIT_MS = 300           # сколько ждать данных нового вида, прежде чем открывать его


def _curve():
    """cubic-bezier(.4, 0, .2, 1) — как анимация темы на сайте."""
    c = QEasingCurve(QEasingCurve.BezierSpline)
    c.addCubicBezierSegment(QPointF(0.4, 0.0), QPointF(0.2, 1.0), QPointF(1.0, 1.0))
    return c


def section_loading(window):
    """Раздел окна ещё ждёт ответов сервера (его фоновые запросы не закончились)."""
    cur = getattr(window, "current", None)
    try:
        return cur is not None and jobs.pending_for(cur) > 0
    except RuntimeError:
        return False


class Reveal(QWidget):
    """Снимок прежнего вида поверх виджета-хозяина: открывает новый вид кругом от origin
    или плавно гаснет (origin=None). Удаляет себя сам."""

    def __init__(self, host, pixmap, origin=None, rect=None, bar=False):
        super().__init__(host)
        self.pm = pixmap
        self.origin = QPointF(origin) if origin is not None else None
        self.t = 0.0
        self._buf = None
        self._anim = None
        self._bar = bar                 # полоска подгрузки сверху, пока новый вид ждёт данных
        self._bar_t0 = None
        self._bar_timer = None
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setGeometry(rect if rect is not None else host.rect())
        if self.origin is not None:
            w, h = self.width(), self.height()
            x, y = self.origin.x(), self.origin.y()
            self.radius = max((x ** 2 + y ** 2) ** 0.5, ((w - x) ** 2 + y ** 2) ** 0.5,
                              (x ** 2 + (h - y) ** 2) ** 0.5, ((w - x) ** 2 + (h - y) ** 2) ** 0.5) + 2
        self.show()
        self.raise_()

    def start(self, loading=None, wait_ms=WAIT_MS, duration=None):
        """Подождать, пока новый вид подгрузится (loading() → False) или выйдет время, и открыть его."""
        t0 = time.monotonic()

        def check():
            try:
                if not self.isVisible():
                    return
                busy = bool(loading()) if loading is not None else False
            except RuntimeError:
                return
            except Exception:
                busy = False
            waited = (time.monotonic() - t0) * 1000
            if busy and waited < wait_ms:
                if self._bar and waited > 120 and self._bar_timer is None:
                    self._bar_t0 = time.monotonic()
                    self._bar_timer = QTimer(self)
                    self._bar_timer.setInterval(16)
                    self._bar_timer.timeout.connect(self.update)
                    self._bar_timer.start()
                QTimer.singleShot(30, check)
            else:
                self._animate(duration)
        QTimer.singleShot(30, check)       # хотя бы один кадр: новый вид успевает разложиться под снимком

    def finish_now(self):
        if self._anim is not None:
            self._anim.stop()
        if self._bar_timer is not None:
            self._bar_timer.stop()
        self.hide()
        self.deleteLater()

    def _paint_bar(self, p):
        """Бегущая полоска цвета акцента у верхнего края — «раздел подгружается»."""
        if self._bar_t0 is None:
            return
        w = self.width()
        h = max(2, int(round(3 * scaler.get_setting("scale_factor", 1.0))))
        phase = ((time.monotonic() - self._bar_t0) / 0.9) % 1.0
        seg = w * 0.32
        x = -seg + (w + seg) * phase
        c = QColor(theme.ACCENT_FILL)
        p.setOpacity(1.0)
        p.fillRect(QRectF(0, 0, w, h), QColor(c.red(), c.green(), c.blue(), 46))
        p.fillRect(QRectF(x, 0, seg, h), c)

    def _animate(self, duration=None):
        try:
            self.raise_()
        except RuntimeError:
            return
        anim = QVariantAnimation(self)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(duration or (SWEEP_MS if self.origin is not None else FADE_MS))
        anim.setEasingCurve(_curve() if self.origin is not None else QEasingCurve.OutCubic)
        anim.valueChanged.connect(self._step)
        anim.finished.connect(self.finish_now)
        self._anim = anim
        if self._bar_timer is not None:
            self._bar_timer.stop()
        self._bar_t0 = None
        anim.start()

    def _step(self, v):
        self.t = float(v)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        if self.origin is None:
            p.setOpacity(max(0.0, 1.0 - self.t))
            p.drawPixmap(0, 0, self.pm)
            self._paint_bar(p)
            p.end()
            return
        dpr = self.pm.devicePixelRatio()
        if self._buf is None or self._buf.size() != self.pm.size():
            self._buf = QImage(self.pm.size(), QImage.Format_ARGB32_Premultiplied)
            self._buf.setDevicePixelRatio(dpr)
        self._buf.fill(Qt.transparent)
        bp = QPainter(self._buf)
        bp.drawPixmap(0, 0, self.pm)
        r = self.radius * self.t
        if r > 0.5:
            bp.setRenderHint(QPainter.Antialiasing, True)
            bp.setCompositionMode(QPainter.CompositionMode_DestinationOut)
            bp.setPen(Qt.NoPen)
            bp.setBrush(QColor(0, 0, 0, 255))
            bp.drawEllipse(QRectF(self.origin.x() - r, self.origin.y() - r, 2 * r, 2 * r))
        bp.end()
        p.drawImage(0, 0, self._buf)
        p.end()


def switch(window, mode=None, scale=None, origin=None):
    """Тема ('light' | 'dark' | 'auto') и/или размер (множитель) — сразу, в этом же окне.

    window — главное окно или окно входа: у него есть rebuild(), пересобирающий содержимое.
    origin — точка окна, от которой наплывает новая тема (центр нажатой кнопки).
    Возвращает True, если вид поменялся."""
    from kov.ui import apply_app_theme
    themed = mode is not None and theme.resolve(mode) != theme.MODE
    scaled = scale is not None and abs(scaler.get_manager().clamp_multiplier(scale) - scaler.scale_multiplier()) > 0.004
    # снимок — до любых перемен: часть окна рисует цвета темы прямо при отрисовке
    shot = None
    if themed or scaled:
        try:
            if window.isVisible() and not window.isMinimized():
                shot = window.grab()
        except RuntimeError:
            shot = None
    if mode is not None:
        scaler.set_setting("theme", mode)
        theme.apply(mode)
    if scale is not None:
        scaler.set_setting("scale_multiplier", scale)
    scaler.save_settings()
    if not (themed or scaled):
        return False
    apply_app_theme(QApplication.instance())
    window.rebuild()
    if shot is not None and not shot.isNull():
        cover = Reveal(window, shot, origin if (themed and not scaled) else None)
        cover.start(lambda: section_loading(window))
    return True
