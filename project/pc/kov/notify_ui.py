# -*- coding: utf-8 -*-
"""Всплывающие карточки — как Kov.NotifyUI сайта (js/notify/notify-ui.js, css/notify.css).

Карточка справа сверху под шапкой окна: полоса слева цвета вида события,
кружок со значком или аватаром, заголовок, строка «в группе «…»», текст,
подсказка «Нажмите, чтобы открыть» при наведении и крестик. Не больше
четырёх сразу: новая вытесняет самую старую; та же метка (tag) заменяет
прежнюю карточку. Пока указатель над карточкой, она не исчезает.

Ею же показываются короткие сообщения о действиях (kov.ui.toast).
"""

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from general_functions import theme
from kov import icons

MAX_TOASTS = 4
DEFAULT_TTL = 6000
_STACK = []
_ANCHOR = {"window": None}
KIND_ICON = {"chat": "chat", "notification": "bell", "group": "users", "warning": "warning", "success": "check"}


def set_anchor(window):
    """Главное окно: карточки встают у его правого верхнего угла."""
    _ANCHOR["window"] = window


def _accent(kind):
    return {"chat": theme.ACCENT, "notification": theme.INFO, "group": theme.SUCCESS, "warning": theme.WARNING,
            "success": theme.SUCCESS}.get(kind, theme.ACCENT)


class _Badge(QWidget):
    """Кружок 2.2rem: значок вида события, аватар отправителя или инициалы."""

    def __init__(self, kind, initials=None, image=None):
        super().__init__()
        from kov.ui import rem
        self.kind, self.initials, self.image = kind, initials, image
        d = rem(2.2)
        self.setFixedSize(d, d)

    def paintEvent(self, e):
        from kov.ui import rem
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        path = QPainterPath()
        path.addEllipse(QRectF(self.rect()))
        if self.image is not None and not self.image.isNull():
            p.setClipPath(path)
            sc = self.image.scaled(self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            p.drawImage((self.width() - sc.width()) // 2, (self.height() - sc.height()) // 2, sc)
        elif self.initials:
            p.fillPath(path, QColor(theme.avatar_color(self.initials)))
            f = QFont(theme.FONT_FAMILY)
            f.setPixelSize(max(6, int(self.height() * 0.38)))
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor("#FFFFFF"))
            p.drawText(self.rect(), Qt.AlignCenter, self.initials)
        else:
            p.fillPath(path, QColor(theme.CHIP_BG))
            size = rem(1.25)
            p.drawPixmap((self.width() - size) // 2, (self.height() - size) // 2,
                         icons.pixmap(KIND_ICON.get(self.kind, "bell"), size, theme.ACCENT))
        p.end()


class ToastCard(QWidget):
    def __init__(self, title, body=None, where=None, kind="notification", on_click=None, tag=None, ttl=DEFAULT_TTL,
                 initials=None, image=None):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        from kov.ui import border, font, label, rem, translucent_ok
        self._glass = translucent_ok()
        if self._glass:
            self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.tag = tag
        self.on_click = on_click
        self.remaining = ttl
        self.closed = False
        self.setCursor(Qt.PointingHandCursor if on_click else Qt.ArrowCursor)
        outer = QVBoxLayout(self)
        m = rem(0.6) if self._glass else 0       # место под тень
        self.m = m
        outer.setContentsMargins(m, m // 2, m, m)
        card = QFrame()
        card.setObjectName("kovToast")
        card.setStyleSheet(f"QFrame#kovToast {{ background: {theme.CARD_BG}; border: {border()}px solid {theme.CARD_BORDER};"
                           f" border-left: {rem(0.3)}px solid {_accent(kind)}; border-radius: {rem(0.75)}px; }}")
        # тень рисует само окно (paintEvent): эффект тени в прозрачном окне перерисовывал карточку кусками
        self._card = card
        self._shadow_rgba = (0, 0, 0, 150) if theme.is_dark() else (38, 50, 56, 46)
        outer.addWidget(card)
        lay = QHBoxLayout(card)
        lay.setContentsMargins(rem(0.9), rem(0.7), rem(0.8), rem(0.7))
        lay.setSpacing(rem(0.7))
        lay.addWidget(_Badge(kind, initials, image), 0, Qt.AlignTop)
        tx = QVBoxLayout()
        tx.setSpacing(rem(0.15))
        t = label(title or "", 0.95, theme.TEXT_VALUE, bold=True, wrap=True)
        tx.addWidget(t)
        if where:
            w = QHBoxLayout()
            w.setSpacing(rem(0.3))
            ic = QLabel()
            ic.setPixmap(icons.pixmap("users", rem(0.85), theme.ACCENT))
            ic.setStyleSheet("background: transparent;")
            w.addWidget(ic)
            wl = label(where, 0.76, theme.ACCENT, bold=True, wrap=True)
            w.addWidget(wl, 1)
            tx.addLayout(w)
        if body:
            b = label(body, 0.88, "#A1A1A6" if theme.is_dark() else "#546E7A", wrap=True)
            tx.addWidget(b)
        self.hint = None
        if on_click:
            self.hint = label("Нажмите, чтобы открыть", 0.7, theme.ACCENT)
            self.hint.setVisible(False)
            tx.addWidget(self.hint)
        lay.addLayout(tx, 1)
        x = QToolButton()
        x.setText("×")
        x.setCursor(Qt.PointingHandCursor)
        x.setToolTip("Закрыть")
        x.setFont(font(1.2))
        d = rem(1.6)
        x.setFixedSize(d, d)
        x.setStyleSheet(f"QToolButton {{ border: none; border-radius: {d // 2}px; background: transparent; color: {theme.TEXT_MUTED}; }}"
                        f"QToolButton:hover {{ background: {theme.CHIP_BG}; color: {theme.BTN_GHOST_TEXT}; }}")
        x.clicked.connect(lambda: self.dismiss())
        lay.addWidget(x, 0, Qt.AlignTop)
        self.setFixedWidth(min(rem(27), max(rem(18), self._screen_width() - rem(2))) + 2 * m)
        self.adjustSize()
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.dismiss)
        self._started = None

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if not self._glass:
            from kov.ui import rem, rounded_mask
            rounded_mask(self, rem(0.75))

    def paintEvent(self, e):
        if self._glass:
            from kov.ui import rem, soft_shadow
            p = QPainter(self)
            p.drawImage(0, 0, soft_shadow(self.size(), self._card.geometry(), rem(0.75), rem(0.7), rem(0.4),
                                          self._shadow_rgba, self.devicePixelRatioF()))
            p.end()
        super().paintEvent(e)

    @staticmethod
    def _screen_width():
        scr = QApplication.primaryScreen()
        return scr.availableGeometry().width() if scr else 1600

    def start(self):
        from time import monotonic
        self._started = monotonic()
        self.timer.start(max(800, int(self.remaining)))

    def enterEvent(self, e):
        from time import monotonic
        if self.timer.isActive() and self._started is not None:
            self.timer.stop()
            self.remaining = max(1500, self.remaining - int((monotonic() - self._started) * 1000))
        if self.hint is not None:
            self.hint.setVisible(True)
            self.adjustSize()
            layout_stack()
        super().enterEvent(e)

    def leaveEvent(self, e):
        if self.hint is not None:
            self.hint.setVisible(False)
            self.adjustSize()
            layout_stack()
        if not self.closed:
            self.start()
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            cb = self.on_click
            self.dismiss()
            if cb:
                cb()

    def dismiss(self):
        if self.closed:
            return
        self.closed = True
        self.timer.stop()
        if self in _STACK:
            _STACK.remove(self)
        self.close()
        layout_stack()


def _origin():
    """Правый верхний угол области под шапкой главного окна (или окна, откуда пришло сообщение)."""
    from kov.ui import rem
    for key in ("window", "fallback"):
        win = _ANCHOR.get(key)
        try:
            if win is not None and win.isVisible() and not win.isMinimized():
                g = win.geometry()
                top = rem(5.6) if key == "window" else rem(1.0)
                return QPoint(g.right() - rem(1.0), g.top() + top - rem(0.3))
        except RuntimeError:
            _ANCHOR[key] = None
    scr = QApplication.primaryScreen().availableGeometry()
    return QPoint(scr.right() - rem(1.0), scr.top() + rem(1.0))


def layout_stack():
    from kov.ui import rem
    o = _origin()
    y = o.y()
    for t in list(_STACK):
        try:
            t.move(o.x() - t.width() + t.m, y)
            y += int(t.height() - t.m * 1.5 + rem(0.5))
        except RuntimeError:
            _STACK.remove(t)


def show(title, body=None, where=None, kind="notification", on_click=None, tag=None, ttl=DEFAULT_TTL,
         initials=None, image=None, anchor=None):
    """Показать карточку. Возвращает её (можно закрыть: card.dismiss())."""
    if anchor is not None:
        _ANCHOR["fallback"] = anchor
    if tag:
        for t in list(_STACK):
            if t.tag == tag:
                t.dismiss()
    guard = 0
    while len(_STACK) >= MAX_TOASTS and guard < 20:
        guard += 1
        _STACK[0].dismiss()
    card = ToastCard(title, body, where, kind, on_click, tag, ttl, initials, image)
    _STACK.append(card)
    layout_stack()
    card.show()
    card.raise_()
    card.start()
    return card


def clear():
    for t in list(_STACK):
        t.dismiss()


def dismiss_tag(tag):
    """Убрать карточки с этой меткой (чат прочитан — его уведомление не нужно)."""
    for t in list(_STACK):
        if t.tag == tag:
            t.dismiss()
