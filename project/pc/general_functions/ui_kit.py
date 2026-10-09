# -*- coding: utf-8 -*-
"""Общие элементы интерфейса поверх theme + vector_icons.

Сюда вынесено то, что переиспользуется несколькими разделами: кнопка
уведомлений со счётчиком, маркеры приоритета в таблице задач, единые
стили кнопок, полей и таблиц. Модуль чата это не использует.
"""

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFontMetrics, QIcon, QPainter, QPainterPath,
                         QPen, QPixmap)
from PySide6.QtWidgets import (QComboBox, QLineEdit, QListView, QPushButton,
                             QSizePolicy, QWidget)

import shiboken6

_LEADING = getattr(QLineEdit, "LeadingPosition", 0)

import scaler
from general_functions import theme
from general_functions.vector_icons import icon_pixmap, make_icon

__all__ = [
    "NotificationButton", "accent_bar_pixmap", "priority_icon",
    "primary_button_qss", "ghost_button_qss", "danger_button_qss",
    "input_qss", "table_qss", "combo_qss", "section_title_qss", "tooltip_qss",
    "apply_button_style", "BADGE_LIMIT",
    "section_qss", "dialog_qss", "list_qss", "card_qss", "segmented_qss",
    "section_header", "style_search_input", "chip_qss",
    "ThemedComboBox", "style_combo", "SegmentedButton", "segmented_group",
    "checklist_qss", "style_form_dialog", "style_button_box", "equalize_buttons", "checkbox_qss", "LoadingIndicator",
    "is_deleted", "deferred", "shutdown_tree",
]

BADGE_LIMIT = 99          # больше показываем как «99+»


# ══════════════════════════════════════════════════════════════════════════════
# Безопасная работа с уже удалёнными виджетами
# ══════════════════════════════════════════════════════════════════════════════

def is_deleted(obj):
    """True, если C++-часть объекта уже уничтожена (или объекта нет).

    Python-ссылка на виджет переживает свой C++-объект, и любое обращение к
    ней потом бросает RuntimeError. Проверка нужна везде, где виджет мог быть
    удалён между постановкой отложенного вызова и его выполнением.
    """
    if obj is None:
        return True
    try:
        return not shiboken6.isValid(obj)
    except (TypeError, RuntimeError):
        return True


def deferred(owner, callback, delay=0):
    """QTimer.singleShot, который молча отменяется, если виджет уже удалён."""

    def run():
        if is_deleted(owner):
            return
        try:
            callback()
        except RuntimeError:
            # Виджет умер между проверкой и вызовом — для отложенной
            # доводки интерфейса это уже не важно
            pass

    QTimer.singleShot(int(delay), run)


def shutdown_tree(widget):
    """Гасит фоновые потоки виджета и всех его потомков перед удалением.

    Обходит дерево целиком: раньше shutdown() звали только у виджета верхнего
    уровня, и вложенные в QTabWidget панели файлов уходили в деструктор с
    живыми потоками миниатюр — отсюда падение приложения при смене этапа.
    """
    if is_deleted(widget):
        return
    try:
        children = widget.findChildren(QWidget)
    except RuntimeError:
        return
    for target in list(children) + [widget]:
        if is_deleted(target):
            continue
        handler = getattr(target, "shutdown", None)
        if callable(handler):
            try:
                handler()
            except Exception as exc:
                # Разрушение интерфейса не должно падать из-за уборки
                print(f"[SHUTDOWN] {type(target).__name__}: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# Кнопка уведомлений: конверт + красный счётчик непрочитанных
# ══════════════════════════════════════════════════════════════════════════════

class NotificationButton(QPushButton):
    """Конверт двух видов плюс кружок с числом непрочитанных.

    Ничего не грузит с диска: и конверт, и счётчик рисуются вектором,
    поэтому остаются чёткими при любом коэффициенте масштабирования.
    """

    def __init__(self, parent=None, icon_size=None, color="#FFFFFF"):
        super().__init__(parent)
        self._unread = 0
        self._color = color
        self._icon_size = int(icon_size or scaler.scale(30))
        self.setText("")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self._refresh()

    # ── данные ────────────────────────────────────────────────────────────────

    def set_unread_count(self, count):
        """Счётчик непрочитанных; 0 — обычный конверт без кружка."""
        try:
            count = max(int(count), 0)
        except (TypeError, ValueError):
            count = 0
        if count == self._unread:
            return
        self._unread = count
        self._refresh()

    def unread_count(self):
        return self._unread

    def set_icon_color(self, color):
        self._color = color
        self._refresh()

    # ── отрисовка ─────────────────────────────────────────────────────────────

    def _refresh(self):
        pixmap = self._compose()
        self.setIcon(QIcon(pixmap))
        self.setIconSize(QSize(pixmap.width(), pixmap.height()))
        self.setToolTip(
            "Уведомления" if not self._unread
            else f"Непрочитанных уведомлений: {self._unread}"
        )
        self.update()

    def _badge_text(self):
        return str(self._unread) if self._unread <= BADGE_LIMIT else f"{BADGE_LIMIT}+"

    def _compose(self):
        """Конверт и поверх него — кружок счётчика в правом верхнем углу."""
        # Холст ровно по размеру значка: раньше он был шире на запас под
        # кружок, из-за чего конверт уезжал влево и ужимался кнопкой
        size = self._icon_size
        canvas = QPixmap(size, size)
        canvas.fill(Qt.transparent)

        painter = QPainter(canvas)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

            glyph = icon_pixmap(
                "mail_filled" if self._unread else "mail",
                size, self._color, 1.9,
            )
            painter.drawPixmap(0, 0, glyph)

            if self._unread:
                self._paint_badge(painter, canvas.width(), 0)
        finally:
            painter.end()
        return canvas

    def _paint_badge(self, painter, canvas_width, pad):
        text = self._badge_text()
        font = theme.font(max(int(self._icon_size * 0.34), 8), bold=True)
        metrics = QFontMetrics(font)
        try:
            text_w = metrics.horizontalAdvance(text)
        except AttributeError:
            text_w = metrics.width(text)

        diameter = max(int(self._icon_size * 0.46), 11)
        width = max(diameter, text_w + int(diameter * 0.55))
        height = diameter
        left = canvas_width - width
        top = 0

        capsule = QPainterPath()
        capsule.addRoundedRect(QRectF(left, top, width, height),
                               height / 2.0, height / 2.0)

        # Белая обводка отделяет кружок от конверта на любом фоне
        ring = QPainterPath()
        ring.addRoundedRect(
            QRectF(left - 1.5, top - 1.5, width + 3.0, height + 3.0),
            (height + 3.0) / 2.0, (height + 3.0) / 2.0,
        )
        painter.fillPath(ring, QColor(255, 255, 255, 230))
        painter.fillPath(capsule, QColor(theme.CRITICAL))

        painter.setFont(font)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(QRectF(left, top, width, height), Qt.AlignCenter, text)


# ══════════════════════════════════════════════════════════════════════════════
# Маркеры строк в таблице задач
# ══════════════════════════════════════════════════════════════════════════════

def accent_bar_pixmap(color, height=None, thickness=None):
    """Вертикальная полоса-акцент слева в ячейке — «строка выделена»."""
    height = int(height or scaler.scale(22))
    thickness = int(thickness or max(scaler.scale(4), 3))
    pixmap = QPixmap(thickness, height)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.Antialiasing, True)
        path = QPainterPath()
        radius = thickness / 2.0
        path.addRoundedRect(QRectF(0, 0, thickness, height), radius, radius)
        painter.fillPath(path, QColor(color))
    finally:
        painter.end()
    return pixmap


def priority_icon(color, glyph=None, size=None):
    """Полоса-акцент и, при необходимости, значок статуса рядом с ней."""
    size = int(size or scaler.scale(22))
    bar = accent_bar_pixmap(color, size)
    if not glyph:
        return QIcon(bar)

    gap = max(scaler.scale(5), 3)
    glyph_px = icon_pixmap(glyph, int(size * 0.82), color, 2.1)
    canvas = QPixmap(bar.width() + gap + glyph_px.width(), size)
    canvas.fill(Qt.transparent)

    painter = QPainter(canvas)
    try:
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawPixmap(0, 0, bar)
        painter.drawPixmap(bar.width() + gap,
                           (size - glyph_px.height()) // 2, glyph_px)
    finally:
        painter.end()
    return QIcon(canvas)


# ══════════════════════════════════════════════════════════════════════════════
# Единые стили
# ══════════════════════════════════════════════════════════════════════════════

def tooltip_qss():
    return f"""
        QToolTip {{
            background-color: {theme.HEADER_BOTTOM};
            color: #FFFFFF;
            border: {max(scaler.scale(1), 1)}px solid {theme.HEADER_MID};
            border-radius: {scaler.scale(4)}px;
            padding: {scaler.scale(5)}px {scaler.scale(8)}px;
            font-size: {scaler.scale(13)}px;
        }}
    """


def _button_qss(bg, border, fg, hover, pressed, radius=None, pad=None):
    radius = scaler.scale(8) if radius is None else radius
    pad_v = scaler.scale(8) if pad is None else pad
    pad_h = scaler.scale(16)
    return f"""
        QPushButton {{
            background-color: {bg};
            color: {fg};
            border: {max(scaler.scale(1), 1)}px solid {border};
            border-radius: {radius}px;
            padding: {pad_v}px {pad_h}px;
            font-size: {scaler.scale(13)}px;
            font-weight: bold;
        }}
        QPushButton:hover   {{ background-color: {hover};   border-color: {hover}; }}
        QPushButton:pressed {{ background-color: {pressed}; border-color: {pressed}; }}
        QPushButton:disabled {{
            background-color: {theme.CHIP_BG};
            color: {theme.TEXT_MUTED};
            border-color: {theme.CARD_BORDER};
        }}
    """ + tooltip_qss()


def primary_button_qss():
    return _button_qss(theme.BTN_BG, theme.BTN_BORDER, "#FFFFFF",
                       theme.BTN_HOVER, theme.BTN_PRESSED)


def ghost_button_qss():
    return _button_qss(theme.BTN_GHOST_BG, theme.BTN_GHOST_BORDER, theme.BTN_GHOST_TEXT,
                       theme.BTN_GHOST_HOVER, theme.BTN_GHOST_PRESSED)


def danger_button_qss():
    return _button_qss(theme.DANGER, theme.DANGER_HOVER, "#FFFFFF",
                       theme.DANGER_HOVER, theme.DANGER_PRESSED)


def apply_button_style(button, kind="primary", icon=None, icon_size=None,
                       tooltip=None, text=None):
    """Приводит кнопку к общему виду: стиль, вектор-иконка, подсказка."""
    qss = {"primary": primary_button_qss,
           "ghost": ghost_button_qss,
           "danger": danger_button_qss}.get(kind, primary_button_qss)()
    button.setStyleSheet(qss)
    button.setCursor(Qt.PointingHandCursor)
    if text is not None:
        button.setText(text)
    if icon:
        size = int(icon_size or scaler.scale(20))
        color = theme.BTN_GHOST_TEXT if kind == "ghost" else "#FFFFFF"
        button.setIcon(make_icon(icon, size, color))
        button.setIconSize(QSize(size, size))
    if tooltip:
        button.setToolTip(tooltip)
    return button


def input_qss():
    radius = scaler.scale(7)
    return f"""
        QLineEdit, QTextEdit, QPlainTextEdit, QDateEdit, QSpinBox {{
            background-color: {theme.CARD_BG};
            color: {theme.TEXT_VALUE};
            border: {max(scaler.scale(1), 1)}px solid {theme.CARD_BORDER};
            border-radius: {radius}px;
            padding: {scaler.scale(7)}px {scaler.scale(10)}px;
            font-size: {scaler.scale(13)}px;
            selection-background-color: {theme.ACCENT_SOFT};
        }}
        QLineEdit:hover, QTextEdit:hover, QDateEdit:hover, QSpinBox:hover {{
            border-color: {theme.ACCENT_SOFT};
        }}
        QLineEdit:focus, QTextEdit:focus, QDateEdit:focus, QSpinBox:focus {{
            border-color: {theme.ACCENT};
        }}
        QLineEdit:disabled, QTextEdit:disabled {{
            background-color: {theme.CHIP_BG};
            color: {theme.TEXT_MUTED};
        }}
    """


def combo_qss():
    radius = scaler.scale(7)
    return f"""
        QComboBox {{
            background-color: {theme.CARD_BG};
            color: {theme.TEXT_VALUE};
            border: {max(scaler.scale(1), 1)}px solid {theme.CARD_BORDER};
            border-radius: {radius}px;
            padding: {scaler.scale(6)}px {scaler.scale(28)}px {scaler.scale(6)}px {scaler.scale(10)}px;
            font-size: {scaler.scale(13)}px;
            min-height: {scaler.scale(26)}px;
        }}
        QComboBox:hover {{ border-color: {theme.ACCENT_SOFT}; }}
        QComboBox:focus {{ border-color: {theme.ACCENT}; }}
        QComboBox::drop-down {{
            border: none;
            width: {scaler.scale(26)}px;
        }}
        QComboBox::down-arrow {{ image: none; width: 0px; height: 0px; }}
        QComboBox QAbstractItemView {{
            background-color: {theme.CARD_BG};
            border: {max(scaler.scale(1), 1)}px solid {theme.CARD_BORDER};
            border-radius: {radius}px;
            selection-background-color: #D7E1E6;
            selection-color: {theme.TEXT_VALUE};
            color: {theme.TEXT_VALUE};
            outline: none;
            padding: {scaler.scale(4)}px;
        }}
        QComboBox QAbstractItemView::item {{
            min-height: {scaler.scale(30)}px;
            padding: {scaler.scale(4)}px {scaler.scale(8)}px;
            border-radius: {scaler.scale(6)}px;
        }}
        QComboBox QAbstractItemView::item:hover {{ background-color: {theme.CHIP_BG}; }}
    """


def table_qss():
    return f"""
        QTableWidget {{
            background-color: {theme.CARD_BG};
            alternate-background-color: #F7F9FA;
            gridline-color: #EAEEF0;
            border: {max(scaler.scale(1), 1)}px solid {theme.CARD_BORDER};
            border-radius: {scaler.scale(10)}px;
            font-size: {scaler.scale(13)}px;
            selection-background-color: #D7E1E6;
            selection-color: {theme.TEXT_VALUE};
        }}
        QTableWidget::item {{
            padding: {scaler.scale(8)}px {scaler.scale(6)}px;
            border: none;
        }}
        QTableWidget::item:selected {{
            background-color: #D7E1E6;
            color: {theme.TEXT_VALUE};
        }}
        QHeaderView::section {{
            background-color: {theme.HEADER_MID};
            color: #FFFFFF;
            padding: {scaler.scale(10)}px {scaler.scale(6)}px;
            /* Со стилем Qt выравнивает заголовок влево, а содержимое ячеек
               стоит по центру — без этой строки шапка «уезжала» от колонок */
            text-align: center;
            border: none;
            border-right: {max(scaler.scale(1), 1)}px solid {theme.HEADER_BOTTOM};
            font-size: {scaler.scale(13)}px;
            font-weight: bold;
        }}
        QHeaderView::section:last {{ border-right: none; }}
        QTableCornerButton::section {{ background-color: {theme.HEADER_MID}; border: none; }}
        QScrollBar:vertical {{
            background: transparent; width: {scaler.scale(10)}px; margin: 0px;
        }}
        QScrollBar::handle:vertical {{
            background: {theme.TEXT_MUTED};
            border-radius: {scaler.scale(5)}px;
            min-height: {scaler.scale(30)}px;
        }}
        QScrollBar::handle:vertical:hover {{ background: {theme.ACCENT_SOFT}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
    """


def section_title_qss():
    return (f"color: {theme.TEXT_VALUE}; background: transparent; "
            f"font-size: {scaler.scale(18)}px; font-weight: bold;")


# ══════════════════════════════════════════════════════════════════════════════
# Оформление разделов целиком
# ══════════════════════════════════════════════════════════════════════════════

def list_qss():
    radius = scaler.scale(9)
    return f"""
        QListWidget {{
            background-color: {theme.CARD_BG};
            border: {max(scaler.scale(1), 1)}px solid {theme.CARD_BORDER};
            border-radius: {radius}px;
            padding: {scaler.scale(4)}px;
            font-size: {scaler.scale(13)}px;
            outline: none;
        }}
        QListWidget::item {{
            padding: {scaler.scale(8)}px {scaler.scale(9)}px;
            border-radius: {scaler.scale(6)}px;
            color: {theme.TEXT_VALUE};
        }}
        QListWidget::item:hover    {{ background-color: {theme.CHIP_BG}; }}
        QListWidget::item:selected {{ background-color: #D7E1E6; color: {theme.TEXT_VALUE}; }}
    """


def card_qss(object_name="themedCard"):
    return f"""
        QFrame#{object_name}, QWidget#{object_name} {{
            background-color: {theme.CARD_BG};
            border: {max(scaler.scale(1), 1)}px solid {theme.CARD_BORDER};
            border-radius: {scaler.scale(12)}px;
        }}
    """


def chip_qss():
    """Плашка-подпись: отдел, статус, счётчик."""
    return f"""
        background-color: {theme.CHIP_BG};
        color: {theme.ACCENT};
        border: {max(scaler.scale(1), 1)}px solid {theme.CARD_BORDER};
        border-radius: {scaler.scale(11)}px;
        padding: {scaler.scale(3)}px {scaler.scale(10)}px;
        font-size: {scaler.scale(12)}px;
        font-weight: bold;
    """


def segmented_qss(active):
    """Пара переключателей вида «Активные / Архивные»."""
    if active:
        bg, fg, bd = theme.ACCENT, "#FFFFFF", theme.ACCENT
        hover, pressed = theme.HEADER_TOP, theme.HEADER_BOTTOM
    else:
        bg, fg, bd = theme.CARD_BG, theme.ACCENT, theme.CARD_BORDER
        hover, pressed = theme.CHIP_BG, theme.BTN_GHOST_PRESSED
    return f"""
        QPushButton {{
            background-color: {bg};
            color: {fg};
            border: {max(scaler.scale(1), 1)}px solid {bd};
            border-radius: {scaler.scale(8)}px;
            padding: {scaler.scale(8)}px {scaler.scale(18)}px;
            font-size: {scaler.scale(13)}px;
            font-weight: bold;
        }}
        QPushButton:hover   {{ background-color: {hover}; }}
        QPushButton:pressed {{ background-color: {pressed}; }}
    """


def section_qss():
    """Единый стиль содержимого раздела.

    Вешается на контейнер, а не на каждый виджет: пока у элемента нет
    собственного setStyleSheet, он получает оформление отсюда. Кнопкам
    с ролью задаётся свойство kind = ghost / danger.
    """
    radius = scaler.scale(8)
    border = max(scaler.scale(1), 1)
    return f"""
        QWidget {{ font-size: {scaler.scale(13)}px; color: {theme.TEXT_VALUE}; }}
        QLabel {{ background: transparent; border: none; }}

        QPushButton {{
            background-color: {theme.BTN_BG};
            color: #FFFFFF;
            border: {border}px solid {theme.BTN_BORDER};
            border-radius: {radius}px;
            padding: {scaler.scale(8)}px {scaler.scale(16)}px;
            font-weight: bold;
        }}
        QPushButton:hover   {{ background-color: {theme.BTN_HOVER};   border-color: {theme.BTN_HOVER}; }}
        QPushButton:pressed {{ background-color: {theme.BTN_PRESSED}; border-color: {theme.BTN_PRESSED}; }}
        QPushButton:disabled {{
            background-color: {theme.CHIP_BG};
            color: {theme.TEXT_MUTED};
            border-color: {theme.CARD_BORDER};
        }}
        QPushButton[kind="ghost"] {{
            background-color: {theme.BTN_GHOST_BG};
            color: {theme.BTN_GHOST_TEXT};
            border-color: {theme.BTN_GHOST_BORDER};
        }}
        QPushButton[kind="ghost"]:hover   {{ background-color: {theme.BTN_GHOST_HOVER}; }}
        QPushButton[kind="ghost"]:pressed {{ background-color: {theme.BTN_GHOST_PRESSED}; }}
        QPushButton[kind="danger"] {{
            background-color: {theme.DANGER};
            color: #FFFFFF;
            border-color: {theme.DANGER_HOVER};
        }}
        QPushButton[kind="danger"]:hover   {{ background-color: {theme.DANGER_HOVER}; }}
        QPushButton[kind="danger"]:pressed {{ background-color: {theme.DANGER_PRESSED}; }}

        QCheckBox, QRadioButton {{ background: transparent; spacing: {scaler.scale(8)}px; }}
        QCheckBox::indicator, QRadioButton::indicator {{
            width: {scaler.scale(16)}px; height: {scaler.scale(16)}px;
        }}

        QGroupBox {{
            background-color: {theme.CARD_BG};
            border: {border}px solid {theme.CARD_BORDER};
            border-radius: {scaler.scale(10)}px;
            margin-top: {scaler.scale(14)}px;
            padding: {scaler.scale(12)}px;
            font-weight: bold;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: {scaler.scale(12)}px;
            padding: 0 {scaler.scale(6)}px;
            color: {theme.ACCENT};
        }}
    """ + input_qss() + combo_qss() + table_qss() + list_qss() + tooltip_qss()


def dialog_qss():
    """То же оформление для отдельных окон — фон страницы плюс контролы."""
    return f"""
        QDialog {{ background-color: {theme.PAGE_BG}; }}
    """ + section_qss()


def style_search_input(line_edit, placeholder="Поиск…"):
    line_edit.setPlaceholderText(placeholder)
    line_edit.setFont(theme.font(scaler.scale(13)))
    line_edit.setStyleSheet(input_qss())
    line_edit.setMinimumHeight(scaler.scale(36))
    line_edit.setClearButtonEnabled(True)
    line_edit.addAction(
        make_icon("search", scaler.scale(16), theme.TEXT_LABEL),
        _LEADING,
    )
    return line_edit


def section_header(title, glyph=None, parent=None):
    """Заголовок раздела: значок, название и разделительная линия."""
    from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

    holder = QWidget(parent)
    holder.setStyleSheet("background: transparent;")
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, scaler.scale(10))
    layout.setSpacing(scaler.scale(10))

    if glyph:
        icon_size = scaler.scale(22)
        badge = QLabel(holder)
        badge.setPixmap(icon_pixmap(glyph, icon_size, theme.ACCENT, 2.0))
        badge.setFixedSize(icon_size, icon_size)
        badge.setStyleSheet("background: transparent;")
        layout.addWidget(badge, 0, Qt.AlignVCenter)

    label = QLabel(title, holder)
    label.setStyleSheet(section_title_qss())
    layout.addWidget(label, 0, Qt.AlignVCenter)

    rule = QFrame(holder)
    rule.setFrameShape(QFrame.HLine)
    rule.setFixedHeight(max(scaler.scale(1), 1))
    rule.setStyleSheet(f"background-color: {theme.CARD_BORDER}; border: none;")
    layout.addWidget(rule, 1, Qt.AlignVCenter)

    return holder


# ══════════════════════════════════════════════════════════════════════════════
# Выпадающий список и переключатель-сегмент
# ══════════════════════════════════════════════════════════════════════════════

class ThemedComboBox(QComboBox):
    """Выпадающий список в оформлении приложения.

    Системная стрелка отключена в combo_qss и рисуется здесь вектором:
    так она остаётся чёткой при любом масштабе и не превращается
    в чёрный прямоугольник, когда стиль Fusion не находит своей отрисовки.
    """

    def __init__(self, parent=None, min_width=None):
        super().__init__(parent)
        self.setView(QListView())          # свой view — иначе стиль не применяется
        self.setStyleSheet(combo_qss())
        self.view().setStyleSheet(list_qss())
        self.view().setSpacing(max(scaler.scale(1), 1))
        self.setCursor(Qt.PointingHandCursor)
        self.setFont(theme.font(scaler.scale(13)))
        self.setMinimumHeight(scaler.scale(36))
        self.setMinimumWidth(int(min_width or scaler.scale(180)))
        self.setMaxVisibleItems(12)

    def paintEvent(self, event):
        super().paintEvent(event)
        size = max(scaler.scale(14), 10)
        right = scaler.scale(9)
        color = theme.TEXT_MUTED if not self.isEnabled() else theme.ACCENT
        glyph = icon_pixmap("chevron_down", size, color, 2.2)
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.drawPixmap(self.width() - right - size,
                               (self.height() - size) // 2, glyph)
        finally:
            painter.end()


def style_combo(combo, min_width=None):
    """Приводит уже созданный QComboBox к общему виду."""
    combo.setView(QListView())
    combo.setStyleSheet(combo_qss())
    combo.view().setStyleSheet(list_qss())
    combo.setCursor(Qt.PointingHandCursor)
    combo.setFont(theme.font(scaler.scale(13)))
    combo.setMinimumHeight(scaler.scale(36))
    if min_width:
        combo.setMinimumWidth(int(min_width))
    return combo


class SegmentedButton(QPushButton):
    """Кнопка-вариант выбора вместо радиокнопки.

    Радиокнопка со стилем обычной кнопки рисовала только точку и текст,
    поэтому выбранный вариант терялся на светлом фоне. Здесь состояние
    видно заливкой: активный вариант тёмный, остальные светлые.
    """

    def __init__(self, text="", parent=None, glyph=None):
        super().__init__(text, parent)
        self._glyph = glyph
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(scaler.scale(38))
        self.setFocusPolicy(Qt.NoFocus)
        self.refresh()
        self.toggled.connect(lambda _checked: self.refresh())

    def nextCheckState(self):
        if not self.isChecked():
            self.setChecked(True)

    def refresh(self):
        active = self.isChecked()
        self.setStyleSheet(segmented_qss(active))
        if self._glyph:
            size = scaler.scale(18)
            color = "#FFFFFF" if active else theme.ACCENT
            self.setIcon(make_icon(self._glyph, size, color))
            self.setIconSize(QSize(size, size))


def segmented_group(*buttons):
    """Связывает сегменты в группу: активным остаётся ровно один."""
    def make_handler(chosen):
        def handler(checked):
            if not checked:
                return
            for other in buttons:
                if other is not chosen:
                    other.setChecked(False)
        return handler

    for btn in buttons:
        btn.toggled.connect(make_handler(btn))
    return buttons


# ══════════════════════════════════════════════════════════════════════════════
# Окна-формы: единое оформление полей, списков и кнопок диалога
# ══════════════════════════════════════════════════════════════════════════════

def checklist_qss():
    """Список с галочками — заметный маркер вместо системного квадратика."""
    box = scaler.scale(17)
    return f"""
        QListWidget {{
            background-color: {theme.CARD_BG};
            border: {max(scaler.scale(1), 1)}px solid {theme.CARD_BORDER};
            border-radius: {scaler.scale(10)}px;
            padding: {scaler.scale(6)}px;
            outline: none;
            font-size: {scaler.scale(13)}px;
        }}
        QListWidget::item {{
            padding: {scaler.scale(7)}px {scaler.scale(8)}px;
            margin: {scaler.scale(2)}px 0;
            border-radius: {scaler.scale(7)}px;
            color: {theme.TEXT_VALUE};
        }}
        QListWidget::item:hover {{ background-color: {theme.CHIP_BG}; }}
        QListWidget::item:selected {{ background-color: transparent; color: {theme.TEXT_VALUE}; }}
        QListWidget::indicator {{
            width: {box}px; height: {box}px;
            border: {max(scaler.scale(2), 2)}px solid {theme.ACCENT_SOFT};
            border-radius: {scaler.scale(5)}px;
            background-color: {theme.CARD_BG};
        }}
        QListWidget::indicator:hover {{ border-color: {theme.ACCENT}; }}
        QListWidget::indicator:checked {{
            background-color: {theme.ACCENT};
            border-color: {theme.ACCENT};
        }}
    """ + _scrollbar_qss()


def _scrollbar_qss():
    return f"""
        QScrollBar:vertical {{
            background: transparent; width: {scaler.scale(10)}px; margin: 0px;
        }}
        QScrollBar::handle:vertical {{
            background: {theme.TEXT_MUTED};
            border-radius: {scaler.scale(5)}px;
            min-height: {scaler.scale(30)}px;
        }}
        QScrollBar::handle:vertical:hover {{ background: {theme.ACCENT_SOFT}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
    """


def style_form_dialog(dialog, combo_min_width=None, text_edit_height=None):
    """Приводит окно-форму (создание/редактирование записи) к общему виду.

    Одна функция на все такие окна: пока оформление задавалось в каждом
    классе отдельно, поля и списки расходились по виду между окнами.
    """
    from PySide6.QtWidgets import QLabel, QLineEdit, QPlainTextEdit, QTextEdit

    dialog.setStyleSheet(dialog_qss())

    label_font = theme.font(scaler.scale(13))
    for label in dialog.findChildren(QLabel):
        label.setFont(label_font)

    for edit in dialog.findChildren(QLineEdit):
        edit.setFont(label_font)
        edit.setMinimumHeight(scaler.scale(38))
        edit.setStyleSheet(input_qss())

    for combo in dialog.findChildren(QComboBox):
        style_combo(combo, combo_min_width or scaler.scale(190))

    height = text_edit_height or scaler.scale(90)
    for text_edit in list(dialog.findChildren(QTextEdit)) + list(dialog.findChildren(QPlainTextEdit)):
        text_edit.setFont(label_font)
        text_edit.setStyleSheet(input_qss())
        text_edit.setMinimumHeight(height)

    return dialog


def style_button_box(button_box, accept_text=None, reject_text=None,
                     accept_icon="check", reject_icon=None):
    """Кнопки QDialogButtonBox в общем стиле: главная — тёмная, остальные светлые."""
    from PySide6.QtWidgets import QDialogButtonBox

    if button_box is None:
        return button_box

    accept_roles = (QDialogButtonBox.AcceptRole, QDialogButtonBox.YesRole,
                    QDialogButtonBox.ApplyRole)
    for button in button_box.buttons():
        role = button_box.buttonRole(button)
        if role in accept_roles:
            if accept_text:
                button.setText(accept_text)
            apply_button_style(button, "primary", icon=accept_icon)
        elif role == QDialogButtonBox.DestructiveRole:
            apply_button_style(button, "danger", icon="trash")
        else:
            if reject_text and role == QDialogButtonBox.RejectRole:
                button.setText(reject_text)
            apply_button_style(button, "ghost", icon=reject_icon)
        button.setMinimumHeight(scaler.scale(38))
    return button_box


def equalize_buttons(*buttons, min_width=None):
    """Выравнивает ширину кнопок по самой широкой.

    Кнопка без значка получалась заметно уже соседних — здесь всем
    задаётся общий минимум.
    """
    buttons = [b for b in buttons if b is not None]
    if not buttons:
        return buttons

    widest = int(min_width or 0)
    for button in buttons:
        widest = max(widest, button.sizeHint().width())
    widest = max(widest, scaler.scale(120))

    for button in buttons:
        button.setMinimumWidth(widest)
        button.setMinimumHeight(scaler.scale(40))
    return buttons


def checkbox_qss():
    """Флажок с явным квадратиком и галочкой при включении."""
    box = scaler.scale(19)
    return f"""
        QCheckBox {{
            spacing: {scaler.scale(9)}px;
            color: {theme.TEXT_VALUE};
            background: transparent;
        }}
        QCheckBox::indicator {{
            width: {box}px;
            height: {box}px;
            border: {max(scaler.scale(2), 2)}px solid {theme.ACCENT_SOFT};
            border-radius: {scaler.scale(5)}px;
            background-color: {theme.CARD_BG};
        }}
        QCheckBox::indicator:hover {{ border-color: {theme.ACCENT}; }}
        QCheckBox::indicator:checked {{
            background-color: {theme.ACCENT};
            border-color: {theme.ACCENT};
            image: none;
        }}
        QCheckBox::indicator:disabled {{ border-color: {theme.CARD_BORDER}; }}
    """


class LoadingIndicator(QWidget):
    """Крутящаяся дуга с подписью — общий индикатор ожидания.

    Рисуется вектором, поэтому масштабируется вместе с интерфейсом.
    Таймер живёт только пока виджет виден.
    """

    def __init__(self, text="Загрузка…", parent=None, diameter=None):
        super().__init__(parent)
        self._angle = 0
        self._text = text
        self._diameter = int(diameter or scaler.scale(56))
        self._progress = None          # None -> бесконечное вращение

        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._advance)

        self.setMinimumHeight(self._diameter + scaler.scale(60))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_text(self, text):
        self._text = text or ""
        self.update()

    def set_progress(self, percent):
        """Показывает долю выполнения; None возвращает бесконечное вращение."""
        self._progress = None if percent is None else max(0, min(100, int(percent)))
        self.update()

    def _advance(self):
        self._angle = (self._angle + 6) % 360
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._timer.start()

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        side = self._diameter
        cx = self.width() // 2
        cy = self.height() // 2 - scaler.scale(14)
        rect = QRectF(cx - side / 2, cy - side / 2, side, side)
        thickness = max(scaler.scale(5), 3)

        painter.setPen(QPen(QColor(theme.CARD_BORDER), thickness, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(rect, 0, 360 * 16)

        painter.setPen(QPen(QColor(theme.ACCENT), thickness, Qt.SolidLine, Qt.RoundCap))
        if self._progress is None:
            painter.drawArc(rect, -self._angle * 16, 110 * 16)
        else:
            painter.drawArc(rect, 90 * 16, -int(360 * self._progress / 100) * 16)
            painter.setPen(QColor(theme.ACCENT))
            painter.setFont(theme.font(scaler.scale(13), bold=True))
            painter.drawText(rect, Qt.AlignCenter, f"{self._progress}%")

        if self._text:
            painter.setPen(QColor(theme.TEXT_LABEL))
            painter.setFont(theme.font(scaler.scale(14)))
            text_rect = QRectF(0, cy + side / 2 + scaler.scale(12),
                               self.width(), scaler.scale(30))
            painter.drawText(text_rect, Qt.AlignHCenter | Qt.AlignTop, self._text)
        painter.end()
