# -*- coding: utf-8 -*-
"""Элементы интерфейса в оформлении веб-версии (css/common.css).

Размеры заданы в rem сайта (scaler.rem), цвета — из general_functions.theme,
поэтому одна и та же вёрстка работает в светлой и тёмной теме и при любом
масштабе. Кнопка с замком ведёт себя как Kov.Rights.lock: остаётся на месте,
показывает ключ и подсказку, а при нажатии объясняет, почему нельзя.
"""

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap, QRegion
from PySide6.QtWidgets import (QAbstractButton, QSpinBox, QAbstractItemView, QApplication, QComboBox, QFrame, QHBoxLayout, QHeaderView,
                             QLabel, QLineEdit, QListView, QPushButton, QSizePolicy, QStyle, QStyledItemDelegate,
                             QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget)

import scaler
from general_functions import theme
from kov import icons

rem = scaler.rem


def px(value):
    return scaler.scale(value)


_DPI = []


def _dpi():
    if not _DPI:
        app = QApplication.instance()
        scr = app.primaryScreen() if app else None
        _DPI.append(float(scr.logicalDotsPerInchY()) if scr else 96.0)
    return _DPI[0] or 96.0


def px_exact(size_rem):
    """Размер в rem без округления до целого пикселя (как в браузере)."""
    return 16.0 * size_rem * scaler.get_setting("scale_factor", 1.0)


def set_px(f, pixels):
    """Дробный размер шрифта в пикселях: через пункты с учётом DPI экрана."""
    f.setPointSizeF(max(1.0, pixels) * 72.0 / _dpi())
    return f


def fs(size_rem):
    """font-size для листа стилей: дробные пункты, как font()."""
    return "%.2fpt" % (max(1.0, px_exact(size_rem)) * 72.0 / _dpi())


def font(size_rem=0.875, bold=False, weight=None, spacing=0.0):
    f = QFont(theme.FONT_FAMILY)
    # на Windows это включает DirectWrite: дробный размер и ширина букв как в браузере
    f.setHintingPreference(QFont.PreferVerticalHinting)
    set_px(f, px_exact(size_rem))
    if weight is not None:
        f.setWeight(weight)
    elif bold:
        f.setBold(True)
    if spacing:
        f.setLetterSpacing(QFont.AbsoluteSpacing, spacing * px_exact(size_rem))
    return f


def mix(a, b, t):
    """Цвет между a и b (t=0 → a, t=1 → b)."""
    ca, cb = QColor(a), QColor(b)
    r = int(ca.red() + (cb.red() - ca.red()) * t)
    g = int(ca.green() + (cb.green() - ca.green()) * t)
    bl = int(ca.blue() + (cb.blue() - ca.blue()) * t)
    return QColor(r, g, bl).name()


def border(width=1):
    return max(1, int(round(width * scaler.get_setting("scale_factor", 1.0))))


# ══════════════════════════════════════════════════════════════════════════
# Общие стили приложения
# ══════════════════════════════════════════════════════════════════════════

def scroll_qss():
    w = rem(0.55)
    return f"""
        QScrollBar:vertical {{ background: transparent; width: {w}px; margin: 0; }}
        QScrollBar:horizontal {{ background: transparent; height: {w}px; margin: 0; }}
        QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
            background: {theme.BTN_GHOST_BORDER}; border-radius: {rem(0.27)}px; min-height: {rem(1.6)}px; min-width: {rem(1.6)}px; }}
        QScrollBar::handle:hover {{ background: {theme.ACCENT_SOFT}; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
    """


def tooltip_qss():
    """Подсказка: тёмная плашка и светлый текст в обеих темах."""
    return (f"QToolTip {{ background-color: {theme.TOOLTIP_BG}; color: {theme.TOOLTIP_FG};"
            f" border: {border()}px solid {theme.TOOLTIP_BORDER}; border-radius: {rem(0.3)}px;"
            # отступ подсказки Qt складывает дважды (поле метки + padding), поэтому padding маленький
            f" padding: 1px {max(2, rem(0.18))}px; font-size: {fs(0.72)}; }}")


_TIP_MARK = "/*kov-tip*/"


def _with_tooltip(sheet):
    """Лист стилей виджета + правило подсказки. Подсказка берёт стили у виджета и его
    родителей, и «background: transparent» контейнера делал её фон прозрачным, а текст —
    белым на светлом. Лист без селектора Qt читает как «* {…}» — так и оборачиваем,
    иначе дописанное правило потерялось бы."""
    if not sheet or not sheet.strip():
        return sheet
    body = sheet if "{" in sheet else "* { %s }" % sheet
    return body + "\n" + _TIP_MARK + " " + tooltip_qss()


def install_tooltip_guard():
    """Каждый лист стилей получает правило подсказки (один раз на запуск).
    styleSheet() отдаёт лист без добавки: код, дописывающий к нему строки, не ломается."""
    if getattr(QWidget, "_kov_tip_guard", False):
        return
    plain_set, plain_get = QWidget.setStyleSheet, QWidget.styleSheet

    def set_style(self, sheet):
        sheet = sheet or ""
        self.setProperty("kovQss", sheet)
        plain_set(self, _with_tooltip(sheet))

    def get_style(self):
        value = self.property("kovQss")
        return value if isinstance(value, str) else plain_get(self)
    QWidget.setStyleSheet = set_style
    QWidget.styleSheet = get_style
    QWidget._kov_tip_guard = True


def app_qss():
    """Общий лист стилей приложения: подсказки, меню, прокрутка, календарь."""
    b = border()
    return scroll_qss() + tooltip_qss() + f"""
        QMenu {{ background: {theme.CARD_BG}; color: {theme.TEXT_VALUE}; border: {b}px solid {theme.CARD_BORDER};
                 border-radius: {rem(0.5)}px; padding: {rem(0.3)}px; font-size: {fs(0.85)}; }}
        QMenu::item {{ padding: {rem(0.45)}px {rem(0.9)}px; border-radius: {rem(0.35)}px; }}
        QMenu::item:selected {{ background: {theme.CHIP_BG}; }}
        QMenu::separator {{ height: {b}px; background: {theme.CARD_BORDER}; margin: {rem(0.25)}px 0; }}
        QCalendarWidget QWidget {{ alternate-background-color: {theme.CARD_BG}; }}
        QCalendarWidget QToolButton {{ color: #FFFFFF; background-color: {theme.ACCENT_FILL}; border: none;
                                       border-radius: {rem(0.35)}px; padding: 0 {rem(0.5)}px;
                                       font-size: {fs(0.8)}; min-height: {rem(1.8)}px; }}
        QCalendarWidget QWidget#qt_calendar_navigationbar {{ background-color: {theme.ACCENT_FILL}; }}
        QCalendarWidget QAbstractItemView {{ background-color: {theme.CARD_BG}; color: {theme.TEXT_VALUE};
                                             selection-background-color: {theme.ACCENT_FILL}; selection-color: #FFFFFF;
                                             font-size: {fs(0.8)}; outline: none; }}
        QCalendarWidget QAbstractItemView:disabled {{ color: {theme.TEXT_MUTED}; }}
        QMessageBox {{ background: {theme.CARD_BG}; }}
    """


def apply_app_theme(app):
    install_tooltip_guard()
    app.setPalette(theme.qpalette())
    app.setFont(font(0.875))
    app.setStyleSheet(app_qss())


# ══════════════════════════════════════════════════════════════════════════
# Кнопки
# ══════════════════════════════════════════════════════════════════════════

KINDS = ("primary", "ghost", "danger", "link", "plain")


def _btn_colors(kind):
    if kind == "ghost":
        return (theme.BTN_GHOST_BG, theme.BTN_GHOST_BORDER, theme.BTN_GHOST_TEXT,
                theme.BTN_GHOST_HOVER, theme.BTN_GHOST_PRESSED)
    if kind == "danger":
        return (theme.DANGER, theme.DANGER_HOVER, "#FFFFFF", theme.DANGER_HOVER, theme.DANGER_PRESSED)
    if kind == "on":           # выбранный сегмент
        return (theme.ACCENT_FILL, theme.ACCENT_FILL, "#FFFFFF", theme.BTN_HOVER, theme.BTN_PRESSED)
    if kind == "off":          # невыбранный сегмент
        return (theme.BTN_GHOST_BG, theme.BTN_GHOST_BORDER, theme.ACCENT, theme.BTN_GHOST_HOVER, theme.BTN_GHOST_PRESSED)
    return (theme.BTN_BG, theme.BTN_BORDER, "#FFFFFF", theme.BTN_HOVER, theme.BTN_PRESSED)


def button_height(icon_only=False, compact=False):
    """Высота кнопки сайта (.btn: 2.375rem, мелкая — 2rem, значок — 2.25rem)."""
    return rem(2.25 if icon_only else (2.0 if compact else 2.375))


def button_qss(kind="primary", icon_only=False, compact=False):
    b = border()
    if kind in ("link", "plain"):
        color = theme.LINK if kind == "link" else theme.TEXT_VALUE
        return f"""
            QPushButton {{ background: transparent; border: none; color: {color};
                           padding: {rem(0.2)}px; font-size: {fs(0.875)};
                           {'text-decoration: underline;' if kind == 'link' else ''} }}
            QPushButton:hover {{ color: {theme.ACCENT}; }}
            QPushButton:disabled {{ color: {_half(color)}; }}
        """
    bg, bd, fg, hover, pressed = _btn_colors(kind)
    h = button_height(icon_only, compact)
    pad = "0px" if icon_only else f"0px {rem(0.75 if compact else 1)}px"
    # отключённая — свои цвета с прозрачностью 0,5 (.btn:disabled { opacity: .5 }); «Удалить» — тихая, как ghost
    dis_bg, dis_bd, dis_fg = (_half(x) for x in _disabled_colors(kind))
    return f"""
        QPushButton {{ background-color: {bg}; border: {b}px solid {bd}; color: {fg};
                       border-radius: {rem(0.5)}px; padding: {pad}; min-height: {h - 2 * b}px; max-height: {h - 2 * b}px;
                       font-size: {fs(0.875)}; font-weight: 600; text-align: center; }}
        QPushButton:hover {{ background-color: {hover}; }}
        QPushButton:pressed {{ background-color: {pressed}; }}
        QPushButton:disabled {{ background-color: {dis_bg}; border-color: {dis_bd}; color: {dis_fg}; }}
        QPushButton:focus {{ outline: none; }}
    """


def _disabled_colors(kind):
    return _btn_colors("ghost" if kind == "danger" else kind)[:3]


def _half(color):
    c = QColor(color)
    return "rgba(%d, %d, %d, %d)" % (c.red(), c.green(), c.blue(), round(c.alpha() * 0.5))


def _icon_color(kind, enabled=True):
    if not enabled:          # значок отключённой: как её текст с прозрачностью 0,5 поверх фона страницы
        bg, _bd, fg = _disabled_colors(kind) if kind not in ("link", "plain") else (theme.PAGE_BG, None, theme.ACCENT)
        return mix(mix(theme.PAGE_BG, bg, 0.5), fg, 0.5)
    return _btn_colors(kind)[2] if kind not in ("link", "plain") else theme.ACCENT


class Button(QPushButton):
    """Кнопка сайта: primary / ghost / danger / link; значок, замок, подсказка."""

    locked_click = Signal(str)

    def __init__(self, text="", kind="primary", icon=None, tooltip=None, parent=None,
                 icon_only=False, compact=False, on_click=None):
        super().__init__(text, parent)
        self._kind = kind
        self._glyph = icon
        self._icon_only = icon_only or (icon and not text)
        self._compact = compact
        self._fixed_h = None
        self._locked = False
        self._reason = ""
        self._base_tip = tooltip or ""
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFont(font(0.875, weight=QFont.DemiBold))
        if tooltip:
            self.setToolTip(tooltip)
        self.refresh()
        if self._icon_only:
            side = rem(2.25)
            self.setFixedSize(side, side)
        if on_click is not None:
            self.clicked.connect(lambda _=False: on_click())

    def kind(self):
        return self._kind

    def set_kind(self, kind):
        self._kind = kind
        self.refresh()

    def set_glyph(self, glyph):
        self._glyph = glyph
        self.refresh()

    def _sheet(self):
        """Лист кнопки; заданная руками высота (мелкая кнопка-значок) — и в листе:
        иначе min-height из QSS при показе возвращал высоту 2.25rem, и кнопка вылезала из рамки."""
        if self._locked and not self._icon_only:
            qss = button_qss(self._kind) + f"QPushButton {{ padding-right: {rem(2.0)}px; }}"
        else:
            qss = button_qss(self._kind, self._icon_only, self._compact)
        if self._fixed_h:
            inner = max(1, self._fixed_h - 2 * border())
            qss += f"QPushButton {{ min-height: {inner}px; max-height: {inner}px; padding-top: 0; padding-bottom: 0; }}"
        return qss

    def setFixedSize(self, *args):
        super().setFixedSize(*args)
        self._fixed_h = self.maximumHeight()
        if hasattr(self, "_reason"):
            self.refresh()

    def setFixedHeight(self, h):
        super().setFixedHeight(h)
        self._fixed_h = self.maximumHeight()
        if hasattr(self, "_reason"):
            self.refresh()

    def refresh(self):
        self.setStyleSheet(self._sheet())
        if self._glyph:
            size = rem(1.05 if self._icon_only else 1.1)
            col = _icon_color(self._kind, True)
            self.setIcon(icons.icon(self._glyph, size, col, disabled=_icon_color(self._kind, False)))
            self.setIconSize(QSize(size, size))
        else:
            self.setIcon(icons.QIcon())

    def changeEvent(self, event):
        if event.type() == QEvent.EnabledChange:
            self.refresh()
        super().changeEvent(event)

    # ── замок ────────────────────────────────────────────────────────────────

    def set_locked(self, locked, reason=""):
        self._locked = bool(locked)
        self._reason = reason or "Действие недоступно"
        self.setToolTip(self._reason if self._locked else self._base_tip)
        self.refresh()
        self.update()

    def is_locked(self):
        return self._locked

    def lock_reason(self):
        return self._reason

    def mouseReleaseEvent(self, event):
        if self._locked and event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.setDown(False)
            toast(self, self._reason, "info")
            self.locked_click.emit(self._reason)
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if self._locked and event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            toast(self, self._reason, "info")
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._locked:
            size = rem(0.9)
            color = _icon_color(self._kind, self.isEnabled())
            pm = icons.pixmap("key", size, color)
            painter = QPainter(self)
            x = self.width() - size - (rem(0.25) if self._icon_only else rem(0.6))
            y = (self.height() - size) // 2 if not self._icon_only else rem(0.15)
            painter.drawPixmap(x, y, pm)
            painter.end()


def round_button(btn, side, bg, border_color, hover):
    """Круглая кнопка-значок: размер, радиус половина стороны и высота без ограничений QSS кнопки."""
    b = border()
    btn.setFixedSize(side, side)
    btn.setStyleSheet(btn.styleSheet() + f"QPushButton {{ border-radius: {side // 2}px; background: {bg};"
                                         f" border: {b}px solid {border_color}; min-height: {side - 2 * b}px;"
                                         f" max-height: {side - 2 * b}px; padding: 0; }}"
                                         f"QPushButton:hover {{ background: {hover}; }}")
    return btn


def gate(btn, allowed, reason):
    """Как Kov.Rights.gate: доступна — обычная, иначе с замком."""
    btn.set_locked(not allowed, reason)
    return btn


# ══════════════════════════════════════════════════════════════════════════
# Переключатель-сегмент («Активные / Архивные»)
# ══════════════════════════════════════════════════════════════════════════

class Segmented(QWidget):
    changed = Signal(str)

    def __init__(self, items, current=None, parent=None, compact=False):
        """items: [(ключ, подпись, значок или None)]"""
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(rem(0.35))
        self._buttons = {}
        self._current = current if current is not None else (items[0][0] if items else None)
        for key, label, glyph in items:
            b = Button(label, "off", glyph, compact=compact)
            b.clicked.connect(lambda _=False, k=key: self.set_current(k, True))
            lay.addWidget(b)
            self._buttons[key] = b
        lay.addStretch(0)
        self._paint()

    def button(self, key):
        return self._buttons.get(key)

    def current(self):
        return self._current

    def set_current(self, key, emit=False):
        if key not in self._buttons:
            return
        changed = key != self._current
        self._current = key
        self._paint()
        if emit and changed:
            self.changed.emit(key)

    def _paint(self):
        for key, b in self._buttons.items():
            b.set_kind("on" if key == self._current else "off")


# ══════════════════════════════════════════════════════════════════════════
# Поля
# ══════════════════════════════════════════════════════════════════════════

def input_qss(widget="QLineEdit"):
    b = border()
    return f"""
        {widget} {{ background: {theme.CARD_BG}; color: {theme.TEXT_VALUE};
                   border: {b}px solid {theme.FIELD_BORDER}; border-radius: {rem(0.45)}px;
                   padding: {rem(0.4)}px {rem(0.7)}px; font-size: {fs(0.875)};
                   selection-background-color: {theme.ACCENT_FILL}; selection-color: #FFFFFF; }}
        {widget}:hover {{ border-color: {theme.ACCENT_SOFT}; }}
        {widget}:focus {{ border-color: {theme.ACCENT}; }}
        {widget}:disabled {{ background: {theme.CHIP_BG}; color: {theme.TEXT_MUTED}; }}
        {widget}[readOnly="true"] {{ background: {theme.SURFACE_2}; }}
    """


def text_qss():
    return input_qss("QTextEdit") + input_qss("QPlainTextEdit")


class SearchInput(QLineEdit):
    """Поле поиска со значком лупы слева."""

    def __init__(self, placeholder="Поиск…", parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setFont(font(0.875))
        self.setClearButtonEnabled(True)
        self.setMinimumHeight(rem(2.375))
        self.setStyleSheet(input_qss() + f"QLineEdit {{ padding-left: {rem(2.1)}px; }}")

    def paintEvent(self, event):
        super().paintEvent(event)
        size = rem(1.0)
        p = QPainter(self)
        p.drawPixmap(rem(0.7), (self.height() - size) // 2, icons.pixmap("search", size, theme.TEXT_LABEL))
        p.end()


def line_edit(placeholder="", text="", parent=None):
    e = QLineEdit(parent)
    e.setPlaceholderText(placeholder)
    e.setText(text or "")
    e.setFont(font(0.875))
    e.setMinimumHeight(rem(2.375))
    e.setStyleSheet(input_qss())
    return e


def select_qss():
    b = border()
    return f"""
        QComboBox {{ background: {theme.CARD_BG}; color: {theme.TEXT_VALUE};
                     border: {b}px solid {theme.FIELD_BORDER}; border-radius: {rem(0.45)}px;
                     padding: {rem(0.35)}px {rem(2.0)}px {rem(0.35)}px {rem(0.7)}px; font-size: {fs(0.875)}; }}
        QComboBox:hover {{ border-color: {theme.ACCENT_SOFT}; }}
        QComboBox:focus {{ border-color: {theme.ACCENT}; }}
        QComboBox:disabled {{ background: {theme.CHIP_BG}; color: {theme.TEXT_MUTED}; }}
        QComboBox::drop-down {{ border: none; width: 0px; }}
        QComboBox::down-arrow {{ image: none; width: 0; height: 0; }}
        QComboBox QAbstractItemView {{ background: {theme.CARD_BG}; color: {theme.TEXT_VALUE};
            border: {b}px solid {theme.CARD_BORDER}; outline: none; padding: {rem(0.25)}px;
            selection-background-color: {theme.CHIP_BG}; selection-color: {theme.TEXT_VALUE}; }}
        QComboBox QAbstractItemView::item {{ min-height: {rem(1.9)}px; padding: {rem(0.2)}px {rem(0.5)}px; }}
    """


class Select(QComboBox):
    """Выпадающий список сайта: стрелка-галочка справа, необязательная подпись слева."""

    def __init__(self, items=None, prefix="", parent=None, min_width_rem=None):
        super().__init__(parent)
        self._prefix = prefix
        self.setView(QListView())
        self.setFont(font(0.875))
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(rem(2.375))
        self.setStyleSheet(select_qss() + (f"QComboBox {{ padding-left: {self._prefix_width() + rem(0.7)}px; }}" if prefix else ""))
        self.view().setFont(font(0.875))
        if min_width_rem:
            self.setMinimumWidth(rem(min_width_rem))
        for item in (items or []):
            if isinstance(item, (tuple, list)):
                self.addItem(str(item[1]), item[0])
            else:
                self.addItem(str(item), item)

    def _prefix_width(self):
        if not self._prefix:
            return 0
        return QFontMetrics(font(0.78)).horizontalAdvance(self._prefix) + rem(0.45)

    # QComboBox отводит тексту на 2–4 px меньше его ширины — последняя буква обрезалась
    def sizeHint(self):
        s = super().sizeHint()
        return QSize(s.width() + rem(0.3), s.height())

    def minimumSizeHint(self):
        s = super().minimumSizeHint()
        return QSize(s.width() + rem(0.3), s.height())

    def value(self):
        return self.currentData()

    def set_value(self, value):
        idx = self.findData(value)
        if idx >= 0:
            self.setCurrentIndex(idx)

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        size = rem(0.9)
        color = theme.TEXT_MUTED if not self.isEnabled() else theme.ACCENT
        p.drawPixmap(self.width() - rem(0.6) - size, (self.height() - size) // 2,
                     icons.pixmap("chevron_down", size, color, 2.2))
        if self._prefix:
            p.setFont(font(0.78))
            p.setPen(QColor(theme.TEXT_LABEL))
            p.drawText(QRect(rem(0.7), 0, self._prefix_width(), self.height()), Qt.AlignVCenter | Qt.AlignLeft, self._prefix)
        p.end()


class SpinBox(QSpinBox):
    """Число со стрелками (input type=number сайта): стрелки — галочки справа."""

    def __init__(self, parent=None, width_rem=4.5):
        super().__init__(parent)
        self.setFont(font(0.875))
        self.setMinimumHeight(rem(2.375))
        self.setFixedWidth(rem(width_rem))
        b = rem(1.4)
        self.setStyleSheet(input_qss("QSpinBox") + f"QSpinBox {{ padding-right: {b}px; }}"
                           f"QSpinBox::up-button, QSpinBox::down-button {{ width: {b}px; border: none;"
                           f" background: transparent; subcontrol-origin: border; }}"
                           f"QSpinBox::up-button {{ subcontrol-position: top right; }}"
                           f"QSpinBox::down-button {{ subcontrol-position: bottom right; }}"
                           f"QSpinBox::up-arrow, QSpinBox::down-arrow {{ image: none; width: 0; height: 0; }}")

    def paintEvent(self, event):
        super().paintEvent(event)
        from PySide6.QtGui import QTransform
        p = QPainter(self)
        size = rem(0.8)
        color = theme.TEXT_MUTED if not self.isEnabled() else theme.TEXT_LABEL
        down = icons.pixmap("chevron_down", size, color, 2.2)
        up = down.transformed(QTransform().rotate(180))
        x = self.width() - rem(0.35) - size
        p.drawPixmap(x, self.height() // 2 - size + rem(0.1), up)
        p.drawPixmap(x, self.height() // 2 - rem(0.1), down)
        p.end()


class Switch(QAbstractButton):
    """Переключатель сайта (.switch): дорожка 3.1×1.7rem, ручка с пружинкой, галочка при включении."""

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(bool(checked))
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFixedSize(rem(3.1), rem(1.7))
        self._pos = 1.0 if checked else 0.0
        from PySide6.QtCore import QVariantAnimation
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(320)
        self._anim.setEasingCurve(QEasingCurve.OutBack)
        self._anim.valueChanged.connect(self._moved)
        self.toggled.connect(self._go)

    def _moved(self, v):
        self._pos = float(v)
        self.update()

    def _go(self, on):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def set_quiet(self, on):
        """Поставить без сигнала и без анимации."""
        self.blockSignals(True)
        self.setChecked(bool(on))
        self.blockSignals(False)
        self._anim.stop()
        self._pos = 1.0 if on else 0.0
        self.update()

    def sizeHint(self):
        return QSize(rem(3.1), rem(1.7))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect())
        on = self.isChecked()
        enabled = self.isEnabled()
        track = theme.SUCCESS if (on and enabled) else (theme.CHIP_BG if not enabled else theme.BTN_GHOST_BORDER)
        p.setPen(Qt.NoPen)
        if self.hasFocus():
            p.setBrush(QColor(theme.INFO_SOFT))
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setBrush(QColor(track))
        p.drawRoundedRect(r.adjusted(1, 1, -1, -1), r.height() / 2 - 1, r.height() / 2 - 1)
        pad = rem(0.18)
        d = rem(1.34)
        x = pad + rem(1.4) * max(-0.1, min(1.1, self._pos))
        knob = QRectF(x, pad, d, d)
        if enabled:
            p.setBrush(QColor(0, 0, 0, 60))
            p.drawEllipse(knob.translated(0, rem(0.08)))
        p.setBrush(QColor(theme.KNOB_BG if enabled else theme.BTN_GHOST_BORDER))
        p.drawEllipse(knob)
        if on and self._pos > 0.4:
            size = int(rem(0.8))
            pm = icons.pixmap("check", size, theme.SUCCESS, 3.0)
            p.setOpacity(min(1.0, (self._pos - 0.4) / 0.6))
            p.drawPixmap(int(knob.center().x() - size / 2), int(knob.center().y() - size / 2), pm)
        p.end()


def translucent_ok():
    """Можно ли окну быть прозрачным по краям (тень, скруглённые углы).

    На Windows — всегда. На Linux — только при работающем композиторе: без него
    прозрачные края заливаются чёрным, и окна рисуются без тени, с маской."""
    import os as _os
    if _os.environ.get("KOVAPP_OPAQUE_POPUPS"):
        return False
    app = QApplication.instance()
    if app is None or app.platformName() != "xcb":
        return True
    try:
        from PySide6.QtX11Extras import QX11Info
        return bool(QX11Info.isCompositingManagerRunning())
    except Exception:
        return False


_SHADOWS = {}


def soft_shadow(size, card, radius, blur, offset_y, rgba, dpr=1.0):
    """Мягкая тень под карточкой окна — готовой картинкой (QImage во весь размер окна).

    Раньше тень давал QGraphicsDropShadowEffect: внутри прозрачного окна Windows он
    перерисовывал карточку не целиком, и после вложенного окна или наведения на кнопки
    часть карточки оставалась затемнённой. Картинка рисуется в paintEvent самого окна."""
    from PIL import Image, ImageDraw, ImageFilter
    from PySide6.QtGui import QImage
    key = (size.width(), size.height(), card.x(), card.y(), card.width(), card.height(), radius, blur, offset_y,
           tuple(rgba), round(dpr, 2))
    img = _SHADOWS.get(key)
    if img is not None:
        return img
    k = 4.0                                  # размываем в четверть размера: тень мягкая, так в 16 раз быстрее
    w, h = max(1, int(size.width() * dpr)), max(1, int(size.height() * dpr))
    sw, sh = max(1, int(w / k)), max(1, int(h / k))
    s = dpr / k
    mask = Image.new("L", (sw, sh), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [card.x() * s, (card.y() + offset_y) * s, (card.x() + card.width()) * s, (card.y() + card.height() + offset_y) * s],
        radius=max(1, radius * s), fill=int(rgba[3]))
    mask = mask.filter(ImageFilter.GaussianBlur(max(0.5, blur * s))).resize((w, h), Image.BILINEAR)
    layer = Image.new("RGBA", (w, h), (int(rgba[0]), int(rgba[1]), int(rgba[2]), 0))
    layer.putalpha(mask)
    img = QImage(layer.tobytes("raw", "RGBA"), w, h, QImage.Format_RGBA8888).copy()
    img.setDevicePixelRatio(dpr)
    if len(_SHADOWS) > 24:
        _SHADOWS.clear()
    _SHADOWS[key] = img
    return img


def rounded_mask(widget, radius):
    """Скруглённые углы окна без прозрачности (маска)."""
    from PySide6.QtGui import QRegion
    path = QPainterPath()
    path.addRoundedRect(QRectF(widget.rect()), radius, radius)
    widget.setMask(QRegion(path.toFillPolygon().toPolygon()))


def mark_png(glyph, side, color="#FFFFFF"):
    """Значок для листа стилей (image: url(...)): PNG во временной папке, обычный и @2x —
    Qt сам берёт чёткий на экране с увеличением. Возвращает путь с прямыми слешами."""
    import os
    import tempfile
    from PySide6.QtCore import QByteArray
    from PySide6.QtGui import QImage
    from PySide6.QtSvg import QSvgRenderer
    folder = os.path.join(tempfile.gettempdir(), "kovapp-ui")
    name = "%s-v1-%d-%s" % (glyph, side, color.lstrip("#").lower())
    base = os.path.join(folder, name + ".png")
    try:
        os.makedirs(folder, exist_ok=True)
        for k, path in ((1, base), (2, os.path.join(folder, name + "@2x.png"))):
            if os.path.isfile(path):
                continue
            img = QImage(side * k, side * k, QImage.Format_ARGB32_Premultiplied)
            img.fill(Qt.transparent)
            p = QPainter(img)
            p.setRenderHint(QPainter.Antialiasing, True)
            QSvgRenderer(QByteArray(icons.svg_text(glyph, color, 3.2).encode("utf-8"))).render(
                p, QRectF(0, 0, img.width(), img.height()))
            p.end()
            img.save(path)
    except OSError:
        return ""
    return base.replace("\\", "/")


def checkbox_qss():
    """Флажок как у сайта (accent-color): квадрат цвета акцента и белая галочка."""
    box = rem(1.15)
    b = max(2, border(2))
    check = mark_png("check", box)
    dash = mark_png("minus", box)
    return f"""
        QCheckBox {{ spacing: {rem(0.55)}px; color: {theme.TEXT_VALUE}; background: transparent; font-size: {fs(0.875)}; }}
        QCheckBox::indicator {{ width: {box}px; height: {box}px; border: {b}px solid {theme.FIELD_BORDER};
                                border-radius: {rem(0.25)}px; background: {theme.CARD_BG}; }}
        QCheckBox::indicator:hover {{ border-color: {theme.ACCENT}; }}
        QCheckBox::indicator:checked {{ background: {theme.ACCENT_FILL}; border-color: {theme.ACCENT_FILL};
                                        image: url("{check}"); }}
        QCheckBox::indicator:checked:hover {{ background: {theme.BTN_HOVER}; border-color: {theme.BTN_HOVER}; }}
        QCheckBox::indicator:indeterminate {{ background: {theme.ACCENT_FILL}; border-color: {theme.ACCENT_FILL};
                                              image: url("{dash}"); }}
        QCheckBox[row="true"] {{ padding: {rem(0.35)}px {rem(0.4)}px; border-radius: {rem(0.35)}px; }}
        QCheckBox[row="true"]:hover {{ background: {theme.CHIP_BG}; }}
        QCheckBox::indicator:disabled {{ border-color: {theme.CARD_BORDER}; background: {theme.CHIP_BG}; }}
        QCheckBox::indicator:checked:disabled {{ border-color: {theme.ACCENT_SOFT}; background: {theme.ACCENT_SOFT}; }}
    """


def radio_qss():
    """Круглый переключатель (radio сайта): кольцо, у выбранного — точка цвета акцента."""
    box = rem(0.85)
    b = max(1, border(1.5))
    r = (box + 2 * b) // 2
    dot = (f"qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, stop:0 {theme.ACCENT_FILL},"
           f" stop:0.5 {theme.ACCENT_FILL}, stop:0.6 {theme.CARD_BG}, stop:1 {theme.CARD_BG})")
    return f"""
        QRadioButton {{ spacing: {rem(0.55)}px; color: {theme.TEXT_VALUE}; background: transparent; font-size: {fs(0.875)}; }}
        QRadioButton::indicator {{ width: {box}px; height: {box}px; border: {b}px solid {theme.FIELD_BORDER};
                                   border-radius: {r}px; background: {theme.CARD_BG}; }}
        QRadioButton::indicator:hover {{ border-color: {theme.ACCENT}; }}
        QRadioButton::indicator:checked {{ border-color: {theme.ACCENT_FILL}; background: {dot}; }}
        QRadioButton::indicator:disabled {{ border-color: {theme.CARD_BORDER}; background: {theme.CHIP_BG}; }}
    """


# ══════════════════════════════════════════════════════════════════════════
# Карточки, подписи, чипы
# ══════════════════════════════════════════════════════════════════════════

def card_qss(name="card", radius=0.75, bg=None):
    return (f"QFrame#{name} {{ background: {bg or theme.CARD_BG}; border: {border()}px solid {theme.CARD_BORDER};"
            f" border-radius: {rem(radius)}px; }}")


class Card(QFrame):
    def __init__(self, parent=None, name="card", padding=(0.9, 1.0), spacing=0.55, radius=0.75, bg=None):
        super().__init__(parent)
        self.setObjectName(name)
        self.setStyleSheet(card_qss(name, radius, bg))
        self.body = QVBoxLayout(self)
        pv, ph = padding
        self.body.setContentsMargins(rem(ph), rem(pv), rem(ph), rem(pv))
        self.body.setSpacing(rem(spacing))


def label(text="", size=0.875, color=None, bold=False, wrap=False, parent=None, selectable=False, align=None):
    lab = QLabel(text, parent)
    lab.setFont(font(size, bold=bold))
    lab.setStyleSheet(f"color: {color or theme.TEXT_VALUE}; background: transparent; border: none;")
    lab.setWordWrap(wrap)
    if selectable:
        lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
    if align is not None:
        lab.setAlignment(align)
    return lab


def muted(text="", size=0.875, wrap=False):
    return label(text, size, theme.TEXT_LABEL, wrap=wrap)


class IconBadge(QLabel):
    """Кружок с значком (подпись карточки, папка раздела)."""

    def __init__(self, glyph, diameter=1.7, glyph_size=0.95, color=None, bg=None, parent=None):
        super().__init__(parent)
        d = rem(diameter)
        self.setFixedSize(d, d)
        self.setAlignment(Qt.AlignCenter)
        self.setPixmap(icons.pixmap(glyph, rem(glyph_size), color or theme.ACCENT))
        self.setStyleSheet(f"background: {bg or theme.CHIP_BG}; border-radius: {d // 2}px;")


class InfoCard(Card):
    """Карточка с подписью: кружок-значок, ЗАГОЛОВОК и (необязательно) пометка справа."""

    def __init__(self, caption, glyph="info", right_text="", parent=None):
        super().__init__(parent, name="infoCard")
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(rem(0.5))
        head.addWidget(IconBadge(glyph))
        self.caption = label(caption.upper(), 0.72, theme.TEXT_LABEL, bold=True)
        self.caption.setFont(font(0.72, bold=True, spacing=0.08))
        head.addWidget(self.caption)
        head.addStretch(1)
        self.right = label(right_text.upper(), 0.72, theme.TEXT_LABEL, bold=True)
        self.right.setFont(font(0.72, bold=True, spacing=0.08))
        head.addWidget(self.right)
        self.body.addLayout(head)
        # показывать — только когда у пометки уже есть родитель, иначе она на миг становится отдельным окном
        self.right.setVisible(bool(right_text))

    def set_right(self, text):
        self.right.setText((text or "").upper())
        self.right.setVisible(bool(text))


class InfoRow(QWidget):
    """Строка «подпись — значение» (значение жирное справа)."""

    def __init__(self, key, value="", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(rem(0.9))
        self.key = label(key, 0.875, theme.TEXT_LABEL)
        self.key.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.value = label(value, 0.875, theme.TEXT_VALUE, bold=True, wrap=True)
        self.value.setAlignment(Qt.AlignRight | Qt.AlignTop)
        self.value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.key, 0, Qt.AlignTop)
        lay.addWidget(self.value, 1)

    def set_value(self, text, color=None):
        self.value.setText(text or "—")
        self.value.setStyleSheet(f"color: {color or theme.TEXT_VALUE}; background: transparent; border: none;")


class SectionHeader(QWidget):
    """ЗАГОЛОВОК раздела со значком и линией до края."""

    def __init__(self, title, glyph=None, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, rem(0.6))
        lay.setSpacing(rem(0.6))
        if glyph:
            ic = QLabel()
            ic.setPixmap(icons.pixmap(glyph, rem(1.375), theme.ACCENT))
            lay.addWidget(ic)
        t = label(title.upper(), 0.78, theme.TITLE_FG, bold=True)
        t.setFont(font(0.78, bold=True, spacing=0.08))
        lay.addWidget(t)
        rule = QFrame()
        rule.setFixedHeight(border())
        rule.setStyleSheet(f"background: {theme.CARD_BORDER}; border: none;")
        lay.addWidget(rule, 1, Qt.AlignVCenter)


CHIP_KINDS = {
    "default": lambda: (theme.CHIP_BG, theme.BTN_GHOST_TEXT),
    "success": lambda: (theme.SUCCESS_SOFT, theme.SUCCESS_TEXT),
    "warning": lambda: (theme.WARNING_SOFT, theme.WARNING_TEXT),
    "danger": lambda: (theme.CRITICAL_SOFT, theme.CRITICAL_TEXT),
    "info": lambda: (theme.INFO_SOFT, theme.INFO),
}


class Chip(QLabel):
    def __init__(self, text, kind="default", parent=None, size=0.75, bold=False):
        super().__init__(text, parent)
        bg, fg = CHIP_KINDS.get(kind, CHIP_KINDS["default"])()
        self.setFont(font(size, bold=bold))
        self.setStyleSheet(f"background: {bg}; color: {fg}; border-radius: {rem(0.6)}px;"
                           f" padding: {rem(0.2)}px {rem(0.65)}px;")
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)


def initials(person):
    get = (lambda k: person.get(k)) if isinstance(person, dict) else (lambda k: getattr(person, k, None))
    last = (get("last_name") or "").strip()
    first = (get("first_name") or "").strip()
    text = (last[:1] + first[:1]).upper()
    return text or "?"


class Avatar(QLabel):
    """Круг с фото сотрудника или инициалами на цветной подложке."""

    def __init__(self, person=None, size_rem=2.4, parent=None):
        super().__init__(parent)
        self._d = rem(size_rem)
        self.setFixedSize(self._d, self._d)
        self._person = person or {}
        self._photo = None
        self.update()

    def set_photo(self, pixmap):
        self._photo = pixmap if (pixmap is not None and not pixmap.isNull()) else None
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        path = QPainterPath()
        path.addEllipse(QRectF(0, 0, self._d, self._d))
        if self._photo is not None:
            p.setClipPath(path)
            scaled = self._photo.scaled(self._d, self._d, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            p.drawPixmap((self._d - scaled.width()) // 2, (self._d - scaled.height()) // 2, scaled)
        else:
            get = (lambda k: self._person.get(k)) if isinstance(self._person, dict) else (lambda k: getattr(self._person, k, None))
            key = "%s %s" % (get("last_name") or "", get("first_name") or "")
            p.fillPath(path, QColor(theme.avatar_color(key.strip())))
            f = QFont(theme.FONT_FAMILY)
            f.setPixelSize(max(6, int(self._d * 0.36)))
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor("#FFFFFF"))
            p.drawText(QRect(0, 0, self._d, self._d), Qt.AlignCenter, initials(self._person))
        p.end()


class PersonChip(QFrame):
    """Сотрудник в строке участников: аватар и «Фамилия Имя»."""

    removed = Signal(object)

    def __init__(self, person, removable=False, parent=None, extra=""):
        super().__init__(parent)
        self.person = person
        self.setObjectName("personChip")
        self.setStyleSheet(f"QFrame#personChip {{ background: {theme.CARD_BG}; border: {border()}px solid {theme.CARD_BORDER};"
                           f" border-radius: {rem(1.0)}px; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(rem(0.2), rem(0.2), rem(0.45), rem(0.2))
        lay.setSpacing(rem(0.4))
        lay.addWidget(Avatar(person, 1.7))
        get = (lambda k: person.get(k)) if isinstance(person, dict) else (lambda k: getattr(person, k, None))
        name = ("%s %s" % (get("last_name") or "", get("first_name") or "")).strip() or "Без имени"
        lay.addWidget(label(name + (" · " + extra if extra else ""), 0.8))
        if removable:
            x = QToolButton()
            x.setText("×")
            x.setCursor(Qt.PointingHandCursor)
            x.setStyleSheet(f"QToolButton {{ border: none; background: transparent; color: {theme.TEXT_MUTED};"
                            f" font-size: {fs(1.0)}; padding: 0 {rem(0.15)}px; }}"
                            f"QToolButton:hover {{ color: {theme.DANGER_TEXT}; }}")
            x.clicked.connect(lambda: self.removed.emit(self.person))
            lay.addWidget(x)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)


class FlowLayout(QHBoxLayout):
    pass


class Flow(QWidget):
    """Перенос элементов по строкам (чипы участников)."""

    def __init__(self, parent=None, spacing_rem=0.35):
        super().__init__(parent)
        self._items = []
        self._sp = rem(spacing_rem)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)

    def add(self, widget):
        widget.setParent(self)
        widget.show()
        self._items.append(widget)
        self._relayout()

    def clear(self):
        for w in self._items:
            w.setParent(None)
            w.deleteLater()
        self._items = []
        self._relayout()

    def widgets(self):
        return list(self._items)

    def relayout(self):
        """Пересчитать после смены содержимого (размеры элементов изменились)."""
        self._relayout()

    @staticmethod
    def _hint(w):
        # sizeHint с учётом минимума (QSS min-height) и максимума
        return w.sizeHint().expandedTo(w.minimumSize()).boundedTo(w.maximumSize())

    def _relayout(self, width=None):
        width = width or max(self.width(), 50)
        x = y = 0
        line_h = 0
        for w in self._items:
            if w.isHidden():
                continue
            hint = self._hint(w)
            if x and x + hint.width() > width:
                x = 0
                y += line_h + self._sp
                line_h = 0
            w.setGeometry(x, y, hint.width(), hint.height())
            x += hint.width() + self._sp
            line_h = max(line_h, hint.height())
        self._height = y + line_h
        self.setMinimumHeight(self._height)
        self.updateGeometry()

    def resizeEvent(self, event):
        self._relayout(event.size().width())
        super().resizeEvent(event)

    def sizeHint(self):
        return QSize(self.width(), getattr(self, "_height", 0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        x = y = line_h = 0
        for w in self._items:
            if w.isHidden():
                continue
            hint = self._hint(w)
            if x and x + hint.width() > width:
                x = 0
                y += line_h + self._sp
                line_h = 0
            x += hint.width() + self._sp
            line_h = max(line_h, hint.height())
        return y + line_h


def count_label(text=""):
    return label(text, 0.78, theme.TEXT_LABEL)


def empty(text):
    lab = label(text, 0.875, theme.TEXT_MUTED, wrap=True)
    lab.setAlignment(Qt.AlignCenter)
    lab.setContentsMargins(rem(1), rem(2), rem(1), rem(2))
    return lab


class TableEmpty(QLabel):
    """«…нет» в самой таблице, под заголовками — как div.empty внутри .table-wrap сайта."""

    def __init__(self, table, text):
        super().__init__(text, table.viewport())
        self.setFont(font(0.875))
        self.setStyleSheet(f"color: {theme.TEXT_MUTED}; background: transparent; border: none;")
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.setContentsMargins(rem(1), rem(2), rem(1), rem(2))
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hide()
        table.viewport().installEventFilter(self)

    def _fit(self):
        w = self.parentWidget().width()
        self.setGeometry(0, 0, w, self.heightForWidth(w))

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Resize:
            self._fit()
        return False

    def setVisible(self, visible):
        super().setVisible(visible)
        if visible:
            self._fit()


class StageDots(QWidget):
    """Кружки этапов: завершённые зелёные, текущий с ободком."""

    def __init__(self, stages=None, current=None, parent=None, size_rem=1.1):
        super().__init__(parent)
        self._d = rem(size_rem)
        self._gap = max(1, rem(0.15))
        self.set_stages(stages or [], current)

    def set_stages(self, stages, current=None):
        self._stages = [bool(s) for s in stages]
        self._current = current
        n = max(1, len(self._stages))
        ring = rem(0.12)
        self.setFixedSize(n * self._d + (n - 1) * self._gap + 2 * ring, self._d + 2 * ring)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        ring = rem(0.12)
        for i, done in enumerate(self._stages):
            x = ring + i * (self._d + self._gap)
            rect = QRectF(x + 0.5, ring + 0.5, self._d - 1, self._d - 1)
            if self._current is not None and i == self._current and not done:
                p.setPen(QPen(QColor(theme.ACCENT_SOFT), ring))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(rect.adjusted(-ring / 2, -ring / 2, ring / 2, ring / 2))
            p.setPen(QPen(QColor(theme.STAGE_DONE_BORDER if done else theme.STAGE_TODO_BORDER), 1))
            p.setBrush(QColor(theme.STAGE_DONE if done else theme.STAGE_TODO))
            p.drawEllipse(rect)
        p.end()


# ══════════════════════════════════════════════════════════════════════════
# Таблица сайта (table.data)
# ══════════════════════════════════════════════════════════════════════════

def table_qss():
    b = border()
    return f"""
        QTableWidget {{ background: {theme.CARD_BG}; border: {b}px solid {theme.CARD_BORDER};
                        border-radius: {rem(0.75)}px; gridline-color: transparent; font-size: {fs(0.875)};
                        color: {theme.TEXT_VALUE}; selection-background-color: {theme.INFO_SOFT};
                        selection-color: {theme.TEXT_VALUE}; outline: none; }}
        QTableWidget::item {{ border-bottom: {b}px solid {theme.CARD_BORDER}; padding: {rem(0.3)}px {rem(0.6)}px; }}
        QTableWidget::item:selected {{ background: {theme.INFO_SOFT}; color: {theme.TEXT_VALUE}; }}
        QTableWidget::item:hover {{ background: {theme.SURFACE_2}; }}
        QHeaderView::section {{ background: {theme.CHIP_BG}; color: {theme.BTN_GHOST_TEXT}; font-weight: bold;
                                padding: {rem(0.55)}px {rem(0.5)}px; border: none;
                                border-bottom: {b}px solid {theme.CARD_BORDER}; font-size: {fs(0.78)}; }}
        QTableCornerButton::section {{ background: {theme.CHIP_BG}; border: none; }}
    """ + scroll_qss()


class _RoundView(QObject):
    """Шапка и строки таблицы не выступают за скруглённую рамку (у сайта — overflow: hidden)."""

    def __init__(self, view, radius):
        super().__init__(view)
        self.view, self.r = view, max(0, radius - border())
        for w in (view.viewport(), view.horizontalHeader(), view.verticalScrollBar()):
            w.installEventFilter(self)
        self.apply()

    def _mask(self, w, top, bottom):
        rect = QRectF(w.rect())
        if rect.isEmpty():
            return
        r = self.r
        path = QPainterPath()
        path.addRoundedRect(rect, r, r)
        if not top:                    # верхние углы прямые
            path.addRect(QRectF(rect.left(), rect.top(), rect.width(), min(r, rect.height())))
        if not bottom:                 # нижние углы прямые
            path.addRect(QRectF(rect.left(), rect.bottom() - min(r, rect.height()) + 1, rect.width(), min(r, rect.height())))
        path.setFillRule(Qt.WindingFill)
        w.setMask(QRegion(path.simplified().toFillPolygon().toPolygon()))

    def apply(self):
        hdr = self.view.horizontalHeader()
        head = hdr.isVisible() or not hdr.isHidden()
        self._mask(hdr, True, False)
        self._mask(self.view.viewport(), not head, True)
        self._mask(self.view.verticalScrollBar(), True, True)   # полоса прокрутки — в углах справа

    def eventFilter(self, obj, ev):
        if ev.type() in (QEvent.Resize, QEvent.Show, QEvent.Hide):
            self.apply()
        return False


def round_view(view, radius):
    """Скруглить углы таблицы вместе с её шапкой и строками."""
    if not hasattr(view, "_round"):
        view._round = _RoundView(view, radius)
    return view._round


class _RowDelegate(QStyledItemDelegate):
    """Подложка строки (просрочено, на сегодня): стиль таблицы её не рисует."""

    def paint(self, painter, option, index):
        bg = index.data(Qt.BackgroundRole)
        if bg is not None and not (option.state & QStyle.State_Selected):
            painter.save()
            painter.fillRect(option.rect, bg)
            painter.restore()
        super().paint(painter, option, index)


class DataTable(QTableWidget):
    """Таблица в стиле сайта: светлая шапка, строка выделяется целиком."""

    row_activated = Signal(int)        # двойной щелчок / Enter

    def __init__(self, headers, parent=None, stretch=None, left_columns=(), text_rows=False):
        super().__init__(0, len(headers), parent)
        self.setHorizontalHeaderLabels(headers)
        self._left = set(left_columns)
        self.verticalHeader().setVisible(False)
        self.setShowGrid(False)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setWordWrap(True)
        self.setStyleSheet(table_qss())
        round_view(self, rem(0.75))
        self.setItemDelegate(_RowDelegate(self))
        hdr = self.horizontalHeader()
        hdr.setFont(font(0.78, bold=True))
        # шапка как th сайта: отступы .6rem сверху и снизу
        hdr.setFixedHeight(QFontMetrics(font(0.78, bold=True)).height() + 2 * rem(0.6) + border())
        hdr.setDefaultAlignment(Qt.AlignCenter)
        hdr.setHighlightSections(False)
        hdr.setSectionResizeMode(QHeaderView.ResizeToContents)
        self._stretch = list(stretch or [])
        self._apply_modes()
        # строка только с текстом: td padding .55rem; со значками этапов — как в списке задач
        rh = QFontMetrics(font(0.875)).height() + 2 * rem(0.55) + border() if text_rows else rem(2.43)
        self.verticalHeader().setDefaultSectionSize(rh)
        self.doubleClicked.connect(lambda idx: self.row_activated.emit(idx.row()))

    def _apply_modes(self):
        hdr = self.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Fixed)
        self.fit_columns()

    def setColumnCount(self, n):
        super().setColumnCount(n)
        self._apply_modes()

    def fit_columns(self):
        """Ширина столбцов как у таблицы сайта: по содержимому, а свободное место
        делится пропорционально; если тесно — сжимается растягиваемый столбец."""
        n = self.columnCount()
        if not n:
            return
        hdr = self.horizontalHeader()
        pad = rem(1.4)
        widths = []
        for c in range(n):
            w = max(self.sizeHintForColumn(c), hdr.sectionSizeHint(c))
            for r in range(min(self.rowCount(), 400)):
                cw = self.cellWidget(r, c)
                if cw is not None:
                    w = max(w, cw.sizeHint().width() + pad)
            widths.append(max(w, rem(4)))
        avail = max(1, self.viewport().width())
        total = float(sum(widths))
        if total <= avail:
            extra = avail - total
            widths = [w + extra * w / total for w in widths]
        else:
            fixed = sum(w for c, w in enumerate(widths) if c not in self._stretch)
            flex = [c for c in self._stretch if c < n]
            room = avail - fixed
            if flex and room > rem(8) * len(flex):
                for c in flex:
                    widths[c] = room / len(flex)
        acc = 0
        for c, w in enumerate(widths):
            ww = int(round(w)) if c < n - 1 else max(1, avail - acc)
            hdr.resizeSection(c, ww)
            acc += ww

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_columns()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.currentRow() >= 0:
            self.row_activated.emit(self.currentRow())
            return
        super().keyPressEvent(event)

    def cell(self, text, col, bold=False, color=None, bg=None, data=None, tooltip=None):
        it = QTableWidgetItem(text if text is not None else "")
        it.setFont(font(0.875, bold=bold))
        it.setTextAlignment((Qt.AlignLeft if col in self._left else Qt.AlignHCenter) | Qt.AlignVCenter)
        if color:
            it.setForeground(QColor(color))
        if bg:
            it.setBackground(QColor(bg))
        if data is not None:
            it.setData(Qt.UserRole, data)
        if tooltip:
            it.setToolTip(tooltip)
        return it

    def selected_data(self, col=0):
        row = self.currentRow()
        if row < 0 or not self.selectionModel().hasSelection():
            return None
        it = self.item(row, col)
        return it.data(Qt.UserRole) if it is not None else None


# ══════════════════════════════════════════════════════════════════════════
# Всплывающие сообщения
# ══════════════════════════════════════════════════════════════════════════

def toast(host, text, kind="info", timeout=3200):
    """Короткое сообщение о действии — карточкой справа сверху, как Kov.UI.toast сайта."""
    if not text:
        return None
    from kov import notify_ui
    kinds = {"success": "success", "warning": "warning", "error": "warning", "info": "notification"}
    win = None
    try:
        win = host.window() if host is not None else None
    except RuntimeError:
        win = None
    return notify_ui.show(text, kind=kinds.get(kind, "notification"), ttl=timeout, anchor=win)


# ══════════════════════════════════════════════════════════════════════════
# Раскладки
# ══════════════════════════════════════════════════════════════════════════

def hbox(*widgets, spacing=0.5, margins=0, stretch_at=None):
    w = QWidget()
    lay = QHBoxLayout(w)
    m = rem(margins) if not isinstance(margins, (tuple, list)) else None
    if m is not None:
        lay.setContentsMargins(m, m, m, m)
    else:
        lay.setContentsMargins(*[rem(x) for x in margins])
    lay.setSpacing(rem(spacing))
    for i, item in enumerate(widgets):
        if item is None:
            continue
        if item == "stretch":
            lay.addStretch(1)
        elif isinstance(item, QWidget):
            lay.addWidget(item, 1 if stretch_at == i else 0)
        else:
            lay.addLayout(item)
    w.setStyleSheet("background: transparent;") if False else None
    return w


def clear_layout(layout):
    if layout is None:
        return
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.hide()
            w.setParent(None)
            w.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


def transparent(widget):
    widget.setAttribute(Qt.WA_StyledBackground, True)
    widget.setStyleSheet("background: transparent;")
    return widget


# ══════════════════════════════════════════════════════════════════════════
# Выбор из длинного списка с поиском (Kov.UI.pickerSelect)
# ══════════════════════════════════════════════════════════════════════════

class _PickerPanel(QFrame):
    chosen = Signal(object)

    def __init__(self, owner, options, current, placeholder, empty_text):
        super().__init__(owner.window(), Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._options = options
        self._current = current
        self._empty = empty_text
        card = QFrame(self)
        card.setObjectName("pickerCard")
        card.setStyleSheet(f"QFrame#pickerCard {{ background: {theme.CARD_BG}; border: {border()}px solid {theme.CARD_BORDER};"
                           f" border-radius: {rem(0.6)}px; }}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(rem(0.45), rem(0.45), rem(0.45), rem(0.45))
        lay.setSpacing(rem(0.35))
        self.search = SearchInput(placeholder)
        self.search.textChanged.connect(lambda _t: self.fill())
        lay.addWidget(self.search)
        from PySide6.QtWidgets import QScrollArea
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.NoFrame)
        self.area.setStyleSheet("QScrollArea { background: transparent; border: none; }" + scroll_qss())
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")
        self.rows = QVBoxLayout(holder)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(rem(0.1))
        self.area.setWidget(holder)
        lay.addWidget(self.area, 1)
        self.fill()

    def fill(self):
        clear_layout(self.rows)
        needle = self.search.text().strip().lower()
        shown = [o for o in self._options if not needle or needle in str(o[1] or "").lower()
                 or (len(o) > 2 and needle in str(o[2] or "").lower())]
        if not shown:
            self.rows.addWidget(muted(self._empty, 0.8))
        for o in shown:
            val, text = o[0], o[1]
            sub = o[2] if len(o) > 2 else ""
            active = str(val) == str(self._current)
            b = QPushButton(text + ("\n" + sub if sub else ""))
            b.setCursor(Qt.PointingHandCursor)
            b.setFont(font(0.85, bold=active))
            b.setStyleSheet(f"QPushButton {{ text-align: left; border: none; border-radius: {rem(0.4)}px;"
                            f" padding: {rem(0.42)}px {rem(0.6)}px; color: {theme.ACCENT if active else theme.TEXT_VALUE};"
                            f" background: {theme.INFO_SOFT if active else 'transparent'}; }}"
                            f"QPushButton:hover {{ background: {theme.CHIP_BG}; }}")
            b.clicked.connect(lambda _=False, v=val: (self.chosen.emit(v), self.close()))
            self.rows.addWidget(b)
        self.rows.addStretch(1)


class PickerSelect(QPushButton):
    """Кнопка-список: подпись выбранного, по нажатию — панель с поиском."""

    changed = Signal(object)

    def __init__(self, options, value=None, placeholder="—", search_placeholder="Поиск…",
                 empty_text="Ничего не найдено", parent=None, min_width_rem=12):
        super().__init__(parent)
        self._options = list(options or [])
        self._value = value
        self._placeholder = placeholder
        self._search_ph = search_placeholder
        self._empty = empty_text
        self.setCursor(Qt.PointingHandCursor)
        self.setFont(font(0.875))
        self.setMinimumHeight(rem(2.375))
        self.setMinimumWidth(rem(min_width_rem))
        b = border()
        self.setStyleSheet(f"QPushButton {{ background: {theme.CARD_BG}; color: {theme.TEXT_VALUE}; text-align: left;"
                           f" border: {b}px solid {theme.FIELD_BORDER}; border-radius: {rem(0.45)}px;"
                           f" padding: {rem(0.35)}px {rem(2.0)}px {rem(0.35)}px {rem(0.7)}px; }}"
                           f"QPushButton:hover {{ border-color: {theme.ACCENT_SOFT}; }}"
                           f"QPushButton:focus {{ border-color: {theme.ACCENT}; outline: none; }}"
                           f"QPushButton:disabled {{ background: {theme.CHIP_BG}; color: {theme.TEXT_MUTED}; }}")
        self.clicked.connect(self.open)
        self._paint()

    def set_options(self, options, value=None):
        self._options = list(options or [])
        self._value = value
        self._paint()

    def value(self):
        return self._value

    def set_value(self, value, emit=False):
        self._value = value
        self._paint()
        if emit:
            self.changed.emit(value)

    def _paint(self):
        found = next((o for o in self._options if str(o[0]) == str(self._value)), None)
        text = found[1] if found else self._placeholder
        fm = QFontMetrics(self.font())
        self.setText(fm.elidedText(str(text), Qt.ElideRight, max(rem(4), self.width() - rem(2.8))))
        self.setToolTip(str(text))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._paint()

    def open(self):
        panel = _PickerPanel(self, self._options, self._value, self._search_ph, self._empty)
        panel.chosen.connect(lambda v: self.set_value(v, emit=True))
        w = max(self.width(), rem(16))
        rows = len(self._options)
        h = min(rem(22), rem(3.4) + rows * rem(2.1) + rem(1))
        pos = self.mapToGlobal(QPoint(0, self.height() + rem(0.25)))
        scr = QApplication.screenAt(pos) or QApplication.primaryScreen()
        g = scr.availableGeometry()
        if pos.y() + h > g.bottom():
            pos = self.mapToGlobal(QPoint(0, -h - rem(0.25)))
        panel.setGeometry(pos.x(), pos.y(), w, h)
        panel.show()
        panel.search.setFocus()

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        size = rem(0.9)
        p.drawPixmap(self.width() - rem(0.6) - size, (self.height() - size) // 2,
                     icons.pixmap("chevron_down", size, theme.ACCENT if self.isEnabled() else theme.TEXT_MUTED, 2.2))
        p.end()


# ══════════════════════════════════════════════════════════════════════════
# Полоса хода и «загрузка…»
# ══════════════════════════════════════════════════════════════════════════

class ProgressLine(QWidget):
    """Полоса загрузки: плавный ход и бегущий блик, пока идёт работа."""

    def __init__(self, parent=None, height_rem=0.55, flow=True):
        super().__init__(parent)
        self._value = 0.0
        self._shown = 0.0
        self._phase = 0.0
        self._flow = flow
        self.setFixedHeight(rem(height_rem))
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)

    def set_value(self, v):
        self._value = max(0.0, min(1.0, float(v or 0)))
        if not self._timer.isActive():
            self._timer.start()

    def reset(self):
        self._value = self._shown = 0.0
        self.update()

    def _tick(self):
        self._shown += (self._value - self._shown) * 0.25
        if abs(self._value - self._shown) < 0.001:
            self._shown = self._value
        self._phase = (self._phase + 0.025) % 1.0
        self.update()
        if not self.isVisible():
            self._timer.stop()

    def paintEvent(self, e):
        from PySide6.QtGui import QLinearGradient
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect())
        rad = r.height() / 2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.CHIP_BG))
        p.drawRoundedRect(r, rad, rad)
        w = r.width() * self._shown
        if w > 0.5:
            fill = QRectF(0, 0, w, r.height())
            g = QLinearGradient(-self._phase * 2 * r.width(), 0, (2 - self._phase * 2) * r.width(), 0)
            if self._flow:
                for k in range(5):
                    g.setColorAt(k / 4.0, QColor(theme.ACCENT if k % 2 == 0 else theme.INFO))
            else:
                g.setColorAt(0, QColor(theme.ACCENT))
                g.setColorAt(1, QColor(theme.ACCENT))
            p.setBrush(g)
            p.drawRoundedRect(fill, rad, rad)
        p.end()


class Spinner(QWidget):
    """Крутящееся кольцо (Kov.UI.loading)."""

    def __init__(self, size_rem=1.6, parent=None):
        super().__init__(parent)
        d = rem(size_rem)
        self.setFixedSize(d, d)
        self._a = 0
        self._t = QTimer(self)
        self._t.setInterval(30)
        self._t.timeout.connect(self._step)
        self._t.start()

    def _step(self):
        self._a = (self._a + 12) % 360
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w = max(2, rem(0.16))
        r = QRectF(w, w, self.width() - 2 * w, self.height() - 2 * w)
        p.setPen(QPen(QColor(theme.CHIP_BG), w))
        p.drawEllipse(r)
        pen = QPen(QColor(theme.ACCENT), w)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(r, -self._a * 16, 100 * 16)
        p.end()


def loading(text="Загрузка…", big=False):
    w = QWidget()
    w.setStyleSheet("background: transparent;")
    lay = QHBoxLayout(w) if not big else QVBoxLayout(w)
    lay.setContentsMargins(rem(1), rem(2 if big else 0.6), rem(1), rem(2 if big else 0.6))
    lay.setSpacing(rem(0.6))
    if big:
        lay.addStretch(1)
    sp = Spinner(2.2 if big else 1.3)
    lab = label(text, 0.9 if big else 0.85, theme.TEXT_LABEL)
    if big:
        lay.addWidget(sp, 0, Qt.AlignHCenter)
        lab.setAlignment(Qt.AlignCenter)
        lay.addWidget(lab)
        lay.addStretch(1)
    else:
        lay.addStretch(1)
        lay.addWidget(sp)
        lay.addWidget(lab)
        lay.addStretch(1)
    return w


def fit_scroll(area, max_rem, after=None):
    """Высота списка в прокрутке — по его строкам, но не больше max_rem (max-height сайта).
    Считается после показа строк: только что добавленные виджеты раскладка до показа не
    учитывает, а sizeHint самой QScrollArea запоминается и после смены строк устаревает.
    after — что сделать потом (обычно окно заново по размеру)."""
    def run():
        try:
            w = area.widget()
            lay = w.layout() if w is not None else None
            if lay is None:
                return
            vw = area.viewport().width()
            want = lay.totalHeightForWidth(vw) if lay.hasHeightForWidth() and vw > 0 else lay.totalSizeHint().height()
            want += 2 * area.frameWidth() + (2 * w.frameWidth() if isinstance(w, QFrame) else 0)
            area.setFixedHeight(min(rem(max_rem), want))
            if after is not None:
                after()
        except RuntimeError:
            pass
    QTimer.singleShot(0, run)


def scroll_area(widget=None, horizontal=False):
    from PySide6.QtWidgets import QScrollArea
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    if not horizontal:
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setStyleSheet("QScrollArea { background: transparent; border: none; }"
                       "QScrollArea > QWidget > QWidget { background: transparent; }" + scroll_qss())
    if widget is not None:
        area.setWidget(widget)
    return area


# ══════════════════════════════════════════════════════════════════════════
# Поле даты: пустое значение «дд.мм.гггг» и календарь по нажатию
# ══════════════════════════════════════════════════════════════════════════

class DateField(QPushButton):
    """Как <input type=date> сайта: может быть пустым, минимум — по желанию."""

    changed = Signal(object)

    def __init__(self, value=None, minimum=None, placeholder="дд.мм.гггг", clearable=False, parent=None):
        super().__init__(parent)
        from PySide6.QtCore import QDate
        self._value = value if (value is not None and value.isValid()) else None
        self._min = minimum
        self._placeholder = placeholder
        self._clearable = clearable
        self.setCursor(Qt.PointingHandCursor)
        self.setFont(font(0.875))
        self.setMinimumHeight(rem(2.375))
        self.setStyleSheet(input_qss("QPushButton") + f"QPushButton {{ text-align: left; padding-right: {rem(2.2)}px; }}")
        self.clicked.connect(self.open)
        self._paint()

    def value(self):
        return self._value

    def iso(self):
        return self._value.toString("yyyy-MM-dd") if self._value is not None else None

    def set_value(self, value, emit=False):
        self._value = value if (value is not None and value.isValid()) else None
        self._paint()
        if emit:
            self.changed.emit(self._value)

    def _paint(self):
        if self._value is None:
            self.setText(self._placeholder)
            self.setStyleSheet(input_qss("QPushButton") + f"QPushButton {{ text-align: left; padding-right: {rem(2.2)}px;"
                               f" color: {theme.TEXT_MUTED}; }}")
        else:
            self.setText(self._value.toString("dd.MM.yyyy"))
            self.setStyleSheet(input_qss("QPushButton") + f"QPushButton {{ text-align: left; padding-right: {rem(2.2)}px; }}")

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        size = rem(1.0)
        glyph = "close" if (self._clearable and self._value is not None) else "calendar"
        p.drawPixmap(self.width() - rem(0.7) - size, (self.height() - size) // 2,
                     icons.pixmap(glyph, size, theme.TEXT_LABEL if glyph == "close" else theme.ACCENT))
        p.end()

    def mouseReleaseEvent(self, e):
        if self._clearable and self._value is not None and e.position().toPoint().x() > self.width() - rem(2.1):
            self.set_value(None, emit=True)
            return
        super().mouseReleaseEvent(e)

    def open(self):
        from PySide6.QtCore import QDate
        from PySide6.QtWidgets import QCalendarWidget, QTableView
        pop = QFrame(self.window(), Qt.Popup | Qt.FramelessWindowHint)
        pop.setObjectName("datePop")
        pop.setStyleSheet(f"QFrame#datePop {{ background: {theme.CARD_BG}; border: {border()}px solid {theme.CARD_BORDER};"
                          f" border-radius: {rem(0.5)}px; }}")
        lay = QVBoxLayout(pop)
        lay.setContentsMargins(rem(0.3), rem(0.3), rem(0.3), rem(0.3))
        cal = QCalendarWidget()
        cal.setGridVisible(False)
        cal.setFirstDayOfWeek(Qt.Monday)
        cal.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        cal.setFont(font(0.8))
        if self._min is not None:
            cal.setMinimumDate(self._min)
        cal.setSelectedDate(self._value or (self._min if self._min is not None and self._min > QDate.currentDate()
                                            else QDate.currentDate()))
        cal.setMinimumSize(rem(20), rem(16))
        table = cal.findChild(QTableView)
        if table is not None:
            table.verticalHeader().setDefaultSectionSize(rem(1.8))
        lay.addWidget(cal)

        def pick(d):
            self.set_value(d, emit=True)
            pop.close()
        cal.clicked.connect(pick)
        cal.activated.connect(pick)
        pos = self.mapToGlobal(QPoint(0, self.height() + rem(0.25)))
        pop.move(pos)
        pop.show()
        cal.setFocus()
