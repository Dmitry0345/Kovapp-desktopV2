# -*- coding: utf-8 -*-
"""Единый шаблон диалогового окна приложения.

`AppMessageBox` повторяет публичный API QMessageBox, поэтому подменяется
импортом без правки вызовов:

    from general_functions.app_dialog import AppMessageBox as QMessageBox

Окно безрамочное: карточка со скруглением, цветная шапка с векторной иконкой
типа сообщения, тело с текстом и панель кнопок. Все размеры идут через
scaler.scale, ширина подбирается по объёму текста в пределах min/max.
Модуль чата это оформление не использует — у него собственное.
"""

from PySide6.QtCore import QEvent, QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QFontMetrics, QIcon, QLinearGradient, QPainter, QPainterPath, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QDialog, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
    QPushButton, QSizePolicy, QTextEdit, QVBoxLayout, QWidget,
)

import scaler
from general_functions import theme
from general_functions.vector_icons import icon_pixmap, make_icon

__all__ = [
    "AppDialog", "AppMessageBox", "Toast",
    "info", "success", "warning", "error", "question", "confirm", "show_toast",
]


# ══════════════════════════════════════════════════════════════════════════════
# Типы сообщений: иконка + цвет шапки
# ══════════════════════════════════════════════════════════════════════════════

KIND_NONE = "none"
KIND_INFO = "info"
KIND_SUCCESS = "success"
KIND_WARNING = "warning"
KIND_ERROR = "error"
KIND_QUESTION = "question"

_KIND_STYLE = {
    KIND_NONE: ("info", theme.HEADER_MID, theme.HEADER_BOTTOM),
    KIND_INFO: ("info", theme.HEADER_TOP, theme.HEADER_BOTTOM),
    KIND_SUCCESS: ("success", "#4CAF7D", "#2E7D57"),
    KIND_WARNING: ("warning", "#E0A33A", "#B47A16"),
    KIND_ERROR: ("error", "#E4635F", "#B3312D"),
    KIND_QUESTION: ("question", theme.HEADER_TOP, theme.HEADER_BOTTOM),
}

# Роли кнопок определяют внешний вид: главная, обычная, опасная
BTN_PRIMARY = "primary"
BTN_GHOST = "ghost"
BTN_DANGER = "danger"


# ══════════════════════════════════════════════════════════════════════════════
# Значок типа сообщения
# ══════════════════════════════════════════════════════════════════════════════

class _KindBadge(QWidget):
    """Круглая подложка с векторным глифом типа сообщения."""

    def __init__(self, diameter, glyph, parent=None):
        super().__init__(parent)
        self._diameter = max(int(diameter), 16)
        self._glyph = glyph
        self.setFixedSize(self._diameter, self._diameter)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def set_glyph(self, glyph):
        self._glyph = glyph
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

            circle = QPainterPath()
            circle.addEllipse(QRectF(0, 0, self._diameter, self._diameter))
            painter.fillPath(circle, QColor(255, 255, 255, 48))

            ring = QPainterPath()
            inset = max(self._diameter * 0.045, 1.0)
            ring.addEllipse(QRectF(inset, inset,
                                   self._diameter - inset * 2,
                                   self._diameter - inset * 2))
            painter.fillPath(ring, QColor(255, 255, 255, 34))

            glyph_size = int(self._diameter * 0.58)
            pixmap = icon_pixmap(self._glyph, glyph_size, "#FFFFFF", 2.0)
            painter.drawPixmap(
                int((self._diameter - pixmap.width()) / 2),
                int((self._diameter - pixmap.height()) / 2),
                pixmap,
            )
        finally:
            painter.end()


class _HeaderFrame(QFrame):
    """Шапка карточки: градиент и скруглённые только верхние углы."""

    def __init__(self, radius, top_color, bottom_color, parent=None):
        super().__init__(parent)
        self._radius = radius
        self._top = QColor(top_color)
        self._bottom = QColor(bottom_color)
        self.setAttribute(Qt.WA_StyledBackground, False)

    def set_colors(self, top_color, bottom_color):
        self._top = QColor(top_color)
        self._bottom = QColor(bottom_color)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            rect = QRectF(self.rect())

            path = QPainterPath()
            path.moveTo(rect.left(), rect.bottom())
            path.lineTo(rect.left(), rect.top() + self._radius)
            path.quadTo(rect.left(), rect.top(), rect.left() + self._radius, rect.top())
            path.lineTo(rect.right() - self._radius, rect.top())
            path.quadTo(rect.right(), rect.top(), rect.right(), rect.top() + self._radius)
            path.lineTo(rect.right(), rect.bottom())
            path.closeSubpath()

            gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            gradient.setColorAt(0.0, self._top)
            gradient.setColorAt(1.0, self._bottom)
            painter.fillPath(path, gradient)
        finally:
            painter.end()


# ══════════════════════════════════════════════════════════════════════════════
# Базовое диалоговое окно
# ══════════════════════════════════════════════════════════════════════════════

class AppDialog(QDialog):
    """Универсальная карточка-диалог.

    Годится и для двух вариантов ответа, и для одного («ОК»): панель кнопок
    строится из того, что добавили через add_button().
    """

    MIN_TEXT_WIDTH = 260
    MAX_TEXT_WIDTH = 620

    def __init__(self, parent=None, kind=KIND_INFO, title="", text="",
                 informative="", closable=True):
        super().__init__(parent)

        self._kind = kind if kind in _KIND_STYLE else KIND_INFO
        self._buttons = []            # [(QPushButton, result_value, role)]
        self._default_button = None
        self._escape_button = None
        self._clicked_button = None
        self._drag_origin = None
        self._detailed_text = ""

        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)

        self._build(title, text, informative, closable)
        self._apply_kind()

    # ── построение ────────────────────────────────────────────────────────────

    def _build(self, title, text, informative, closable):
        self._margin = scaler.scale(18)      # запас под тень
        self._radius = scaler.scale(14)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(self._margin, self._margin, self._margin, self._margin)
        outer.setSpacing(0)

        self.card = QFrame(self)
        self.card.setObjectName("appDialogCard")
        self.card.setStyleSheet(
            "QFrame#appDialogCard {"
            f" background-color: {theme.CARD_BG};"
            f" border-radius: {self._radius}px;"
            f" border: {max(scaler.scale(1), 1)}px solid {theme.CARD_BORDER};"
            "}"
        )
        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(scaler.scale(34))
        shadow.setOffset(0, scaler.scale(7))
        shadow.setColor(QColor(38, 50, 56, 120))
        self.card.setGraphicsEffect(shadow)
        outer.addWidget(self.card)

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        card_layout.addWidget(self._build_header(title, closable))
        card_layout.addWidget(self._build_body(text, informative), 1)
        card_layout.addWidget(self._build_footer())

    def _build_header(self, title, closable):
        header_h = scaler.scale(74)
        pad = scaler.scale(18)

        self.header = _HeaderFrame(self._radius, theme.HEADER_TOP, theme.HEADER_BOTTOM, self.card)
        self.header.setFixedHeight(header_h)

        layout = QHBoxLayout(self.header)
        layout.setContentsMargins(pad, 0, pad, 0)
        layout.setSpacing(scaler.scale(14))

        self.badge = _KindBadge(scaler.scale(42), "info", self.header)
        layout.addWidget(self.badge, 0, Qt.AlignVCenter)

        self.title_label = QLabel(title or "Сообщение", self.header)
        self.title_label.setFont(theme.font(scaler.scale(16), bold=True))
        self.title_label.setStyleSheet("color: #FFFFFF; background: transparent;")
        self.title_label.setWordWrap(False)
        layout.addWidget(self.title_label, 1, Qt.AlignVCenter)

        close_size = scaler.scale(30)
        self.close_button = QPushButton(self.header)
        self.close_button.setFixedSize(close_size, close_size)
        self.close_button.setIcon(make_icon("close", scaler.scale(15), "#FFFFFF"))
        self.close_button.setIconSize(QSize(scaler.scale(15), scaler.scale(15)))
        self.close_button.setCursor(Qt.PointingHandCursor)
        self.close_button.setFocusPolicy(Qt.NoFocus)
        self.close_button.setStyleSheet(
            "QPushButton {"
            " background-color: rgba(255,255,255,0.16);"
            " border: none; padding: 0px; margin: 0px;"
            f" border-radius: {close_size // 2}px;"
            "}"
            "QPushButton:hover { background-color: rgba(255,255,255,0.30); }"
            "QPushButton:pressed { background-color: rgba(255,255,255,0.42); }"
        )
        self.close_button.clicked.connect(self._on_close_clicked)
        self.close_button.setVisible(bool(closable))
        layout.addWidget(self.close_button, 0, Qt.AlignVCenter)

        return self.header

    def _build_body(self, text, informative):
        body = QFrame(self.card)
        body.setObjectName("appDialogBody")
        body.setStyleSheet("QFrame#appDialogBody { background: transparent; border: none; }")

        pad_h = scaler.scale(24)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(pad_h, scaler.scale(20), pad_h, scaler.scale(18))
        layout.setSpacing(scaler.scale(9))

        self.text_label = QLabel(text or "", body)
        self.text_label.setFont(theme.font(scaler.scale(14)))
        self.text_label.setStyleSheet(
            f"color: {theme.TEXT_VALUE}; background: transparent; border: none;")
        self.text_label.setWordWrap(True)
        self.text_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.text_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        layout.addWidget(self.text_label)

        self.informative_label = QLabel(informative or "", body)
        self.informative_label.setFont(theme.font(scaler.scale(13)))
        self.informative_label.setStyleSheet(
            f"color: {theme.TEXT_LABEL}; background: transparent; border: none;")
        self.informative_label.setWordWrap(True)
        self.informative_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.informative_label.setVisible(bool(informative))
        layout.addWidget(self.informative_label)

        self.details_view = QTextEdit(body)
        self.details_view.setReadOnly(True)
        self.details_view.setFont(theme.font(scaler.scale(12)))
        self.details_view.setFixedHeight(scaler.scale(120))
        self.details_view.setStyleSheet(
            "QTextEdit {"
            f" background-color: {theme.CHIP_BG};"
            f" color: {theme.TEXT_VALUE};"
            f" border: {max(scaler.scale(1), 1)}px solid {theme.CARD_BORDER};"
            f" border-radius: {scaler.scale(7)}px;"
            f" padding: {scaler.scale(8)}px;"
            "}"
        )
        self.details_view.setVisible(False)
        layout.addWidget(self.details_view)

        self.body_layout = layout
        return body

    def _build_footer(self):
        footer = QFrame(self.card)
        footer.setObjectName("appDialogFooter")
        footer.setStyleSheet(
            "QFrame#appDialogFooter {"
            f" background-color: {theme.PAGE_BG};"
            f" border-top: {max(scaler.scale(1), 1)}px solid {theme.CARD_BORDER};"
            f" border-bottom-left-radius: {self._radius}px;"
            f" border-bottom-right-radius: {self._radius}px;"
            "}"
        )

        layout = QHBoxLayout(footer)
        layout.setContentsMargins(
            scaler.scale(20), scaler.scale(14), scaler.scale(20), scaler.scale(16))
        layout.setSpacing(scaler.scale(10))
        layout.addStretch(1)

        self.footer = footer
        self.footer_layout = layout
        return footer

    # ── оформление по типу сообщения ─────────────────────────────────────────

    def _apply_kind(self):
        glyph, top, bottom = _KIND_STYLE.get(self._kind, _KIND_STYLE[KIND_INFO])
        self.header.set_colors(top, bottom)
        self.badge.set_glyph(glyph)

    def set_kind(self, kind):
        self._kind = kind if kind in _KIND_STYLE else KIND_INFO
        self._apply_kind()

    def kind(self):
        return self._kind

    # ── содержимое ────────────────────────────────────────────────────────────

    def set_title(self, title):
        self.title_label.setText(title or "Сообщение")

    def set_text(self, text):
        self.text_label.setText(text or "")

    def set_informative_text(self, text):
        self.informative_label.setText(text or "")
        self.informative_label.setVisible(bool(text))

    def set_detailed_text(self, text):
        self._detailed_text = text or ""
        self.details_view.setPlainText(self._detailed_text)
        self.details_view.setVisible(bool(self._detailed_text))

    # ── кнопки ────────────────────────────────────────────────────────────────

    def add_button(self, text, result=None, style=BTN_GHOST, default=False, escape=False):
        """Добавляет кнопку в подвал и возвращает её."""
        button = QPushButton(text, self.footer)
        button.setCursor(Qt.PointingHandCursor)
        button.setFont(theme.font(scaler.scale(13), bold=True))
        button.setMinimumHeight(scaler.scale(38))
        button.setMinimumWidth(scaler.scale(112))
        button.setFocusPolicy(Qt.StrongFocus)
        self._style_button(button, style)

        value = result if result is not None else len(self._buttons)
        button.clicked.connect(lambda _=False, b=button: self._on_button_clicked(b))

        self.footer_layout.addWidget(button)
        self._buttons.append([button, value, style])

        if default:
            self.set_default_button(button)
        if escape:
            self._escape_button = button
        return button

    def _style_button(self, button, style):
        radius = scaler.scale(8)
        pad_v, pad_h = scaler.scale(8), scaler.scale(18)
        border = max(scaler.scale(1), 1)

        if style == BTN_PRIMARY:
            bg, bd, fg = theme.BTN_BG, theme.BTN_BORDER, "#FFFFFF"
            hover, pressed = theme.BTN_HOVER, theme.BTN_PRESSED
        elif style == BTN_DANGER:
            bg, bd, fg = theme.DANGER, theme.DANGER_HOVER, "#FFFFFF"
            hover, pressed = theme.DANGER_HOVER, theme.DANGER_PRESSED
        else:
            bg, bd, fg = theme.BTN_GHOST_BG, theme.BTN_GHOST_BORDER, theme.BTN_GHOST_TEXT
            hover, pressed = theme.BTN_GHOST_HOVER, theme.BTN_GHOST_PRESSED

        # Стиль вешаем на сам виджет: глобальное правило QPushButton из
        # MainWindow иначе перебивает оформление диалога
        button.setStyleSheet(f"""
            QPushButton {{
                background-color: {bg};
                color: {fg};
                border: {border}px solid {bd};
                border-radius: {radius}px;
                padding: {pad_v}px {pad_h}px;
                font-weight: bold;
            }}
            QPushButton:hover {{ background-color: {hover}; border-color: {hover}; }}
            QPushButton:pressed {{ background-color: {pressed}; border-color: {pressed}; }}
            QPushButton:focus {{ outline: none; }}
            QPushButton:disabled {{
                background-color: {theme.CHIP_BG};
                color: {theme.TEXT_MUTED};
                border-color: {theme.CARD_BORDER};
            }}
        """)

    def set_default_button(self, button):
        self._default_button = button
        for entry in self._buttons:
            entry[0].setDefault(entry[0] is button)
            entry[0].setAutoDefault(entry[0] is button)
        if button is not None:
            button.setFocus()

    def default_button(self):
        return self._default_button

    def set_escape_button(self, button):
        self._escape_button = button

    def clicked_button(self):
        return self._clicked_button

    def dialog_buttons(self):
        return [entry[0] for entry in self._buttons]

    def _on_button_clicked(self, button):
        self._clicked_button = button
        for widget, value, _style in self._buttons:
            if widget is button:
                self.done(int(value) if isinstance(value, int) else 0)
                return
        self.done(0)

    def _on_close_clicked(self):
        if self._escape_button is not None:
            self._on_button_clicked(self._escape_button)
        else:
            self.reject()

    # ── размеры и позиционирование ───────────────────────────────────────────

    @staticmethod
    def _advance(metrics, text):
        try:
            return metrics.horizontalAdvance(text)
        except AttributeError:
            return metrics.width(text)

    def _preferred_text_width(self):
        """Ширина по объёму текста — короткие сообщения не растягиваются."""
        metrics = QFontMetrics(self.text_label.font())
        longest = 0
        for chunk in (self.text_label.text() or "").split("\n"):
            longest = max(longest, self._advance(metrics, chunk))

        info_metrics = QFontMetrics(self.informative_label.font())
        for chunk in (self.informative_label.text() or "").split("\n"):
            longest = max(longest, self._advance(info_metrics, chunk))

        title_metrics = QFontMetrics(self.title_label.font())
        title_width = self._advance(title_metrics, self.title_label.text() or "")
        title_width += scaler.scale(42) + scaler.scale(30) + scaler.scale(60)

        buttons_width = scaler.scale(40)
        for widget, _value, _style in self._buttons:
            buttons_width += max(widget.sizeHint().width(), widget.minimumWidth())
            buttons_width += scaler.scale(10)

        # Длинный текст переносим: берём ~78 % от максимума как рабочую ширину
        if longest > scaler.scale(self.MAX_TEXT_WIDTH):
            longest = int(scaler.scale(self.MAX_TEXT_WIDTH) * 0.78)

        content = max(longest, title_width, buttons_width, scaler.scale(self.MIN_TEXT_WIDTH))
        content = min(content, scaler.scale(self.MAX_TEXT_WIDTH))
        return max(content, int(getattr(self, "_explicit_min_px", 0)) - scaler.scale(48))

    def _finalize_geometry(self):
        width = self._preferred_text_width() + scaler.scale(48) + self._margin * 2
        self.setFixedWidth(int(width))

        self.text_label.setMinimumWidth(int(width - scaler.scale(48) - self._margin * 2))
        self.adjustSize()
        self.setFixedHeight(self.sizeHint().height())

        self._center_on_parent()

    def _center_on_parent(self):
        parent = self.parentWidget()
        if parent is not None and parent.window() is not None and parent.window().isVisible():
            anchor = parent.window().geometry()
        else:
            screen = QApplication.primaryScreen()
            anchor = screen.availableGeometry() if screen else None
        if anchor is None:
            return
        self.move(
            anchor.center().x() - self.width() // 2,
            anchor.center().y() - self.height() // 2,
        )

    # ── перетаскивание за шапку ──────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._on_header(event.position().toPoint()):
            self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_origin is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_origin)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_origin = None
        super().mouseReleaseEvent(event)

    def _on_header(self, pos):
        if not hasattr(self, "header"):
            return False
        top_left = self.header.mapTo(self, QPoint(0, 0))
        rect = self.header.rect().translated(top_left)
        return rect.contains(pos)

    # ── клавиатура ───────────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._on_close_clicked()
            event.accept()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            target = self._default_button
            if target is None and self._buttons:
                target = self._buttons[-1][0]
            if target is not None:
                self._on_button_clicked(target)
                event.accept()
                return
        super().keyPressEvent(event)

    # ── запуск ───────────────────────────────────────────────────────────────

    def exec(self):
        if not self._buttons:
            self.add_button("ОК", result=1, style=BTN_PRIMARY, default=True, escape=True)
        self._finalize_geometry()
        return super().exec()

    def exec_(self):             # прежнее имя (Qt 5)
        return self.exec()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._center_on_parent)


# ══════════════════════════════════════════════════════════════════════════════
# Совместимость с QMessageBox
# ══════════════════════════════════════════════════════════════════════════════

class AppMessageBox(AppDialog):
    """Тот же шаблон, но с интерфейсом QMessageBox.

    Позволяет заменить стандартные окна одной строкой импорта, не переписывая
    вызовы: setIcon / setText / setStandardButtons / button() / exec_() и
    статические information() и прочие работают как раньше.
    """

    # Типы значков
    NoIcon = 0
    Information = 1
    Warning = 2
    Critical = 3
    Question = 4

    # Стандартные кнопки (значения совместимы с QMessageBox)
    NoButton = 0x00000000
    Ok = 0x00000400
    Save = 0x00000800
    SaveAll = 0x00001000
    Open = 0x00002000
    Yes = 0x00004000
    YesToAll = 0x00008000
    No = 0x00010000
    NoToAll = 0x00020000
    Abort = 0x00040000
    Retry = 0x00080000
    Ignore = 0x00100000
    Close = 0x00200000
    Cancel = 0x00400000
    Discard = 0x00800000
    Help = 0x01000000
    Apply = 0x02000000
    Reset = 0x04000000
    RestoreDefaults = 0x08000000

    # Роли (для addButton)
    InvalidRole = -1
    AcceptRole = 0
    RejectRole = 1
    DestructiveRole = 2
    ActionRole = 3
    HelpRole = 4
    YesRole = 5
    NoRole = 6
    ResetRole = 7
    ApplyRole = 8

    _BUTTON_TEXT = {
        Ok: "ОК", Save: "Сохранить", SaveAll: "Сохранить всё", Open: "Открыть",
        Yes: "Да", YesToAll: "Да для всех", No: "Нет", NoToAll: "Нет для всех",
        Abort: "Прервать", Retry: "Повторить", Ignore: "Пропустить",
        Close: "Закрыть", Cancel: "Отмена", Discard: "Не сохранять",
        Help: "Справка", Apply: "Применить", Reset: "Сбросить",
        RestoreDefaults: "По умолчанию",
    }

    # Порядок отрисовки: подтверждающая кнопка всегда правее отменяющей
    _BUTTON_ORDER = (
        Help, Reset, RestoreDefaults, Discard, Abort, Close, Cancel, No, NoToAll,
        Ignore, Retry, Apply, Open, Save, SaveAll, Yes, YesToAll, Ok,
    )

    _AFFIRMATIVE = (Ok, Yes, YesToAll, Save, SaveAll, Open, Apply, Retry)
    _DESTRUCTIVE = (Discard, Abort, Reset, RestoreDefaults)
    _ESCAPE = (Cancel, Close, No, Abort, Ignore)

    _ICON_TO_KIND = {
        NoIcon: KIND_NONE,
        Information: KIND_INFO,
        Warning: KIND_WARNING,
        Critical: KIND_ERROR,
        Question: KIND_QUESTION,
    }

    def __init__(self, parent=None, *_args, **_kwargs):
        super().__init__(parent, kind=KIND_INFO, title="", text="")
        self._icon = self.NoIcon
        self._standard_buttons = self.NoButton
        self._button_map = {}         # standard flag -> QPushButton
        self._custom_roles = {}       # QPushButton -> role
        self._pending_default = None
        self._built = False

    # ── setters в стиле QMessageBox ──────────────────────────────────────────

    def setIcon(self, icon):
        self._icon = icon
        self.set_kind(self._ICON_TO_KIND.get(icon, KIND_INFO))

    def icon(self):
        return self._icon

    def setWindowTitle(self, title):
        self.set_title(title)
        super().setWindowTitle(title or "")

    def setText(self, text):
        self.set_text(text)

    def text(self):
        return self.text_label.text()

    def setInformativeText(self, text):
        self.set_informative_text(text)

    def informativeText(self):
        return self.informative_label.text()

    def setDetailedText(self, text):
        self.set_detailed_text(text)

    def detailedText(self):
        return self._detailed_text

    def setTextFormat(self, fmt):
        self.text_label.setTextFormat(fmt)

    def setTextInteractionFlags(self, flags):
        self.text_label.setTextInteractionFlags(flags)

    def setStandardButtons(self, buttons):
        self._standard_buttons = int(buttons)
        self._rebuild_standard_buttons()

    def standardButtons(self):
        return self._standard_buttons

    def setDefaultButton(self, button):
        if isinstance(button, int):
            self._pending_default = button
            widget = self._button_map.get(button)
            if widget is not None:
                self.set_default_button(widget)
        else:
            self.set_default_button(button)

    def defaultButton(self):
        return self._default_button

    def setEscapeButton(self, button):
        if isinstance(button, int):
            self.set_escape_button(self._button_map.get(button))
        else:
            self.set_escape_button(button)

    def button(self, which):
        """Кнопка по стандартному флагу (как QMessageBox.button)."""
        if which not in self._button_map and which & self._standard_buttons:
            self._rebuild_standard_buttons()
        return self._button_map.get(which)

    def buttons(self):
        return self.dialog_buttons()

    def buttonRole(self, button):
        for flag, widget in self._button_map.items():
            if widget is button:
                if flag in self._AFFIRMATIVE:
                    return self.YesRole if flag in (self.Yes, self.YesToAll) else self.AcceptRole
                if flag in self._DESTRUCTIVE:
                    return self.DestructiveRole
                return self.NoRole if flag in (self.No, self.NoToAll) else self.RejectRole
        return self._custom_roles.get(button, self.InvalidRole)

    def addButton(self, *args):
        """addButton(text, role) либо addButton(StandardButton)."""
        if len(args) == 1 and isinstance(args[0], int):
            flag = args[0]
            self._standard_buttons |= flag
            self._rebuild_standard_buttons()
            return self._button_map.get(flag)

        if len(args) >= 2 and isinstance(args[0], str):
            text, role = args[0], args[1]
            style = BTN_GHOST
            if role in (self.AcceptRole, self.YesRole, self.ApplyRole):
                style = BTN_PRIMARY
            elif role == self.DestructiveRole:
                style = BTN_DANGER
            button = self.add_button(text, result=len(self._buttons) + 1, style=style)
            self._custom_roles[button] = role
            if role in (self.RejectRole, self.NoRole) and self._escape_button is None:
                self.set_escape_button(button)
            if style == BTN_PRIMARY and self._default_button is None:
                self.set_default_button(button)
            return button

        if len(args) == 1:
            button = args[0]
            if isinstance(button, QPushButton):
                self._style_button(button, BTN_GHOST)
                button.setParent(self.footer)
                self.footer_layout.addWidget(button)
                button.clicked.connect(lambda _=False, b=button: self._on_button_clicked(b))
                self._buttons.append([button, len(self._buttons) + 1, BTN_GHOST])
                return button
        return None

    def removeButton(self, button):
        for entry in list(self._buttons):
            if entry[0] is button:
                self._buttons.remove(entry)
                self.footer_layout.removeWidget(button)
                button.setParent(None)
        for flag, widget in list(self._button_map.items()):
            if widget is button:
                self._button_map.pop(flag, None)
        self._custom_roles.pop(button, None)

    def _rebuild_standard_buttons(self):
        """Пересобирает набор стандартных кнопок в нужном порядке."""
        for flag, widget in list(self._button_map.items()):
            for entry in list(self._buttons):
                if entry[0] is widget:
                    self._buttons.remove(entry)
            self.footer_layout.removeWidget(widget)
            widget.setParent(None)
        self._button_map.clear()

        for flag in self._BUTTON_ORDER:
            if not (self._standard_buttons & flag):
                continue
            if flag in self._AFFIRMATIVE:
                style = BTN_PRIMARY
            elif flag in self._DESTRUCTIVE:
                style = BTN_DANGER
            else:
                style = BTN_GHOST
            widget = self.add_button(self._BUTTON_TEXT.get(flag, "ОК"),
                                     result=flag, style=style)
            self._button_map[flag] = widget

        for flag in self._ESCAPE:
            if flag in self._button_map:
                self.set_escape_button(self._button_map[flag])
                break

        if self._pending_default is not None and self._pending_default in self._button_map:
            self.set_default_button(self._button_map[self._pending_default])
        elif self._default_button is None:
            for flag in reversed(self._BUTTON_ORDER):
                if flag in self._button_map and flag in self._AFFIRMATIVE:
                    self.set_default_button(self._button_map[flag])
                    break

    # ── совместимость с прежним оформлением ──────────────────────────────────

    def setStyleSheet(self, sheet):
        # Прежние вызовы вида setStyleSheet("font-size: 13px") игнорируем:
        # оформление задаёт шаблон. Стили карточки при этом не ломаются.
        return None

    def layout(self):
        # style_message_box() дописывал распорку в сетку QMessageBox —
        # шаблону это не нужно, отдаём None, чтобы вызов ничего не делал
        return None

    def setFont(self, font):
        return None

    def setMinimumWidth(self, width):
        # Приходит уже в пикселях устройства — держим отдельно от MIN_TEXT_WIDTH,
        # который задан в базовых единицах и масштабируется позже
        self._explicit_min_px = max(int(getattr(self, "_explicit_min_px", 0)), int(width))

    # Порядок кнопок в подвале одинаков во всём приложении:
    # отказ слева, подтверждение справа
    _ROLE_RANK = {
        HelpRole: 0, ResetRole: 0,
        RejectRole: 1, NoRole: 1,
        ActionRole: 2,
        DestructiveRole: 3, ApplyRole: 3, AcceptRole: 3, YesRole: 3,
    }

    def _reorder_buttons(self):
        def rank(entry):
            return self._ROLE_RANK.get(self.buttonRole(entry[0]), 2)

        ordered = sorted(enumerate(self._buttons), key=lambda pair: (rank(pair[1]), pair[0]))
        self._buttons = [entry for _index, entry in ordered]
        for widget, _value, _style in self._buttons:
            self.footer_layout.removeWidget(widget)
        for widget, _value, _style in self._buttons:
            self.footer_layout.addWidget(widget)

    def _refine_kind(self):
        """«Успех» с информационным значком показываем зелёным вариантом."""
        if self._icon != self.Information:
            return
        title = (self.title_label.text() or "").lower()
        if "успех" in title or "успешно" in title or "готово" in title:
            self.set_kind(KIND_SUCCESS)

    def exec(self):
        if not self._buttons:
            self.setStandardButtons(self.Ok)
        self._refine_kind()
        self._reorder_buttons()
        return super().exec()

    def exec_(self):             # прежнее имя (Qt 5)
        return self.exec()

    # ── статические вызовы QMessageBox ───────────────────────────────────────

    @classmethod
    def _static(cls, parent, title, text, icon, buttons, default):
        box = cls(parent)
        box.setIcon(icon)
        box.setWindowTitle(title)
        box.setText(text)
        box.setStandardButtons(buttons)
        if default:
            box.setDefaultButton(default)
        return box.exec()

    @classmethod
    def information(cls, parent, title, text, buttons=Ok, default=NoButton):
        return cls._static(parent, title, text, cls.Information, buttons, default)

    @classmethod
    def warning(cls, parent, title, text, buttons=Ok, default=NoButton):
        return cls._static(parent, title, text, cls.Warning, buttons, default)

    @classmethod
    def critical(cls, parent, title, text, buttons=Ok, default=NoButton):
        return cls._static(parent, title, text, cls.Critical, buttons, default)

    @classmethod
    def question(cls, parent, title, text, buttons=(Yes | No), default=NoButton):
        return cls._static(parent, title, text, cls.Question, buttons, default)

    @classmethod
    def about(cls, parent, title, text):
        return cls._static(parent, title, text, cls.Information, cls.Ok, cls.NoButton)


# ══════════════════════════════════════════════════════════════════════════════
# Всплывающее уведомление (toast)
# ══════════════════════════════════════════════════════════════════════════════

class Toast(QWidget):
    """Ненавязчивое сообщение поверх окна, само исчезает через timeout."""

    _active = []

    def __init__(self, parent, text, kind=KIND_INFO, timeout=2600):
        super().__init__(parent.window() if parent is not None else None)
        self.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        glyph, top, _bottom = _KIND_STYLE.get(kind, _KIND_STYLE[KIND_INFO])
        radius = scaler.scale(10)

        card = QFrame(self)
        card.setObjectName("toastCard")
        card.setStyleSheet(
            "QFrame#toastCard {"
            f" background-color: {theme.HEADER_BOTTOM};"
            f" border-radius: {radius}px;"
            f" border: {max(scaler.scale(1), 1)}px solid {top};"
            "}"
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(scaler.scale(6), scaler.scale(6),
                                 scaler.scale(6), scaler.scale(6))
        outer.setSpacing(0)
        outer.addWidget(card)

        inner = QHBoxLayout(card)
        inner.setContentsMargins(scaler.scale(15), scaler.scale(11),
                                 scaler.scale(18), scaler.scale(11))
        inner.setSpacing(scaler.scale(10))

        icon_size = scaler.scale(20)
        icon_label = QLabel(card)
        icon_label.setPixmap(icon_pixmap(glyph, icon_size, "#FFFFFF", 2.0))
        icon_label.setFixedSize(icon_size, icon_size)
        icon_label.setStyleSheet("background: transparent;")
        inner.addWidget(icon_label, 0, Qt.AlignVCenter)

        message = QLabel(text, card)
        message.setFont(theme.font(scaler.scale(13), bold=True))
        message.setStyleSheet("color: #FFFFFF; background: transparent;")
        message.setWordWrap(False)
        inner.addWidget(message, 1, Qt.AlignVCenter)

        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(scaler.scale(24))
        shadow.setOffset(0, scaler.scale(5))
        shadow.setColor(QColor(0, 0, 0, 130))
        card.setGraphicsEffect(shadow)

        self.adjustSize()
        self._reposition()

        Toast._active.append(self)
        QTimer.singleShot(max(int(timeout), 600), self._dismiss)

    def _reposition(self):
        host = self.parentWidget()
        if host is None:
            screen = QApplication.primaryScreen()
            if screen is None:
                return
            geo = screen.availableGeometry()
            x = geo.center().x() - self.width() // 2
            y = geo.bottom() - self.height() - scaler.scale(60)
            self.move(x, y)
            return

        offset = scaler.scale(28) + len(Toast._active) * (self.height() + scaler.scale(8))
        top_left = host.mapToGlobal(QPoint(0, 0))
        x = top_left.x() + (host.width() - self.width()) // 2
        y = top_left.y() + host.height() - self.height() - offset
        self.move(x, y)

    def _dismiss(self):
        if self in Toast._active:
            Toast._active.remove(self)
        self.close()
        self.deleteLater()


def show_toast(parent, text, kind=KIND_INFO, timeout=2600):
    toast = Toast(parent, text, kind, timeout)
    toast.show()
    return toast


# ══════════════════════════════════════════════════════════════════════════════
# Короткие вызовы
# ══════════════════════════════════════════════════════════════════════════════

def _simple(parent, title, text, kind, informative=""):
    dialog = AppDialog(parent, kind=kind, title=title, text=text, informative=informative)
    dialog.add_button("ОК", result=1, style=BTN_PRIMARY, default=True, escape=True)
    return dialog.exec()


def info(parent, title, text, informative=""):
    return _simple(parent, title, text, KIND_INFO, informative)


def success(parent, title, text, informative=""):
    return _simple(parent, title, text, KIND_SUCCESS, informative)


def warning(parent, title, text, informative=""):
    return _simple(parent, title, text, KIND_WARNING, informative)


def error(parent, title, text, informative=""):
    return _simple(parent, title, text, KIND_ERROR, informative)


def question(parent, title, text, yes_text="Да", no_text="Нет", informative="", danger=False):
    """True — выбран утвердительный вариант."""
    dialog = AppDialog(parent, kind=KIND_QUESTION, title=title,
                       text=text, informative=informative)
    no_button = dialog.add_button(no_text, result=0, style=BTN_GHOST, escape=True)
    dialog.add_button(yes_text, result=1,
                      style=BTN_DANGER if danger else BTN_PRIMARY, default=True)
    dialog.set_escape_button(no_button)
    return dialog.exec() == 1


def confirm(parent, title, text, ok_text="ОК", cancel_text="Отмена", informative="", danger=False):
    return question(parent, title, text, ok_text, cancel_text, informative, danger)
