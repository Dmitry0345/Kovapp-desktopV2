from PySide6.QtWidgets import (QLabel, QListWidget, QScrollArea, QTextEdit, QLineEdit, QSpacerItem, QSizePolicy)
from PySide6.QtCore import Qt, QUrl, Signal, QPoint, QTimer, QMimeData
from PySide6.QtGui import (QDesktopServices, QFont, QTextCursor)
import re, os
import sys
import urllib.parse
from pathlib import Path
from admin_windows1.scaler import *
import scaler


class ClickableLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event):
        self.clicked.emit()


class PhoneLineEdit(QLineEdit):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.digits = ''
        self.setFont(scale_value(QFont("Arial", scaler.scale(13))))
        self.setText(self.format_text())
        self.setCursorPosition(self.get_cursor_position())
        self.setMaxLength(scaler.scale(18))
        self.textEdited.connect(self.on_text_edited)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        QTimer.singleShot(0, lambda: self.setCursorPosition(self.get_cursor_position()))

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        QTimer.singleShot(0, lambda: self.setCursorPosition(self.get_cursor_position()))

    def keyPressEvent(self, event):
        key = event.key()
        if Qt.Key_0 <= key <= Qt.Key_9:
            if len(self.digits) < 10:
                self.digits += event.text()
        elif key in (Qt.Key_Backspace, Qt.Key_Delete):
            self.digits = self.digits[:-1]
        super().keyPressEvent(event)
        self.setText(self.format_text())
        self.setCursorPosition(self.get_cursor_position())

    def on_text_edited(self, _):
        self.setText(self.format_text())
        self.setCursorPosition(self.get_cursor_position())

    def format_text(self):
        padded = self.digits.ljust(10, '_')
        formatted = f'+7 ({padded[0:3]}) {padded[3:6]}-{padded[6:8]}-{padded[8:10]}'
        return formatted

    def get_cursor_position(self):
        txt = self.format_text()
        return txt.find('_')

    def get_phone_number(self):
        return self.format_text()

def _format_text_with_links(text):
    if not text:
        return ""
    html_text = text.replace('\n', '<br/>')
    url_pattern = r"(https?:\/\/\S+?)(?=[\s,;!'\<\>\[\]\{\}]|$)"
    formatted_html = re.sub(url_pattern, r"<a href='\1' style='color: blue; text-decoration: underline;'>\1</a>",
                            html_text)
    return formatted_html

class ClickableTextEdit(QTextEdit):
    # Сигналы: insert(position, text), delete_range(start,end), paste(position,text), mouse moved (pos)
    charInserted = Signal(int, str)         # позиция, текст
    rangeDeleted = Signal(int, int)         # start, end
    textPasted = Signal(int, str)           # position, text
    mouseMoved = Signal(object)             # локальная позиция QPoint

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLineWrapMode(QTextEdit.WidgetWidth)
        self.setTextInteractionFlags(Qt.TextBrowserInteraction | Qt.LinksAccessibleByMouse)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setFont(scale_value(QFont("Arial", scaler.scale(13))))

        # Нужна для корректной работы mouseMoveEvent даже когда курсор не захвачен
        self.setMouseTracking(True)

    def setEditableMode(self, editable):
        if editable:
            self.setTextInteractionFlags(Qt.TextEditorInteraction | Qt.LinksAccessibleByMouse)
            self.setReadOnly(False)
        else:
            self.setTextInteractionFlags(Qt.TextBrowserInteraction | Qt.LinksAccessibleByMouse)
            self.setReadOnly(True)

    def mousePressEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            anchor = self.anchorAt(event.position().toPoint())
            if anchor:
                QDesktopServices.openUrl(QUrl(anchor))
                return
            else:
                cursor = self.cursorForPosition(event.position().toPoint())
                line_text = cursor.block().text()
                url_pattern = r'(https?://[^\s]+)'
                match = re.search(url_pattern, line_text)

                if match:
                    detected_url = match.group(0)
                    QDesktopServices.openUrl(QUrl(detected_url))
                    return
                else:
                    cursor.select(QTextCursor.WordUnderCursor)
                    selected_text = cursor.selectedText().strip()

                    if selected_text:
                        encoded_query = urllib.parse.quote_plus(selected_text)
                        search_url = f"https://yandex.ru/search/?text={encoded_query}"
                        QDesktopServices.openUrl(QUrl(search_url))
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        # Эмитим координату мыши, чтобы виджет-родитель мог отреагировать (hover)
        try:
            self.mouseMoved.emit(event)
        except Exception:
            pass
        super().mouseMoveEvent(event)

    def keyPressEvent(self, event):
        cursor = self.textCursor()
        # Запомним положение и выделение до обработки
        pos_before = cursor.position()
        has_selection = cursor.hasSelection()
        sel_start = cursor.selectionStart() if has_selection else None
        sel_end = cursor.selectionEnd() if has_selection else None

        key = event.key()
        text = event.text()

        # Обработка Backspace / Delete: эмитим rangeDeleted
        if key in (Qt.Key_Backspace, Qt.Key_Delete):
            # После базовой обработки вычислим фактический диапазон удалённых символов
            super().keyPressEvent(event)
            cursor_after = self.textCursor()
            pos_after = cursor_after.position()
            # Простой heuristic:
            if key == Qt.Key_Backspace:
                # удалили символ слева: [pos_after, pos_before)
                start = min(pos_after, pos_before)
                end = max(pos_after, pos_before)
            else:
                # Delete: удалили символ справа: [pos_before, pos_after)
                start = min(pos_before, pos_after)
                end = max(pos_before, pos_after)
            if start != end:
                self.rangeDeleted.emit(start, end)
            return

        # Если ввод обычного символа и была выделена область -> это replacement: сначала rangeDeleted
        if text and has_selection:
            # удаляем выделение
            self.rangeDeleted.emit(sel_start, sel_end)

        # Если это обычный ввод символа (printable) — обработаем, emit insert
        super().keyPressEvent(event)

        # После вызова супер() — текст уже вставлен. Вычислим вставленный кусок.
        if text:
            # если была выделение — вставка длина может быть len(text)
            inserted_len = len(text)
            # position of insertion is sel_start if there was selection else pos_before
            insert_pos = sel_start if has_selection else pos_before
            if inserted_len > 0:
                self.charInserted.emit(insert_pos, text)

    def insertFromMimeData(self, source: QMimeData) -> None:
        # Обрабатываем paste
        text = source.text()
        if not text:
            super().insertFromMimeData(source)
            return

        cursor = self.textCursor()
        pos_before = cursor.position()
        has_selection = cursor.hasSelection()
        sel_start = cursor.selectionStart() if has_selection else None
        sel_end = cursor.selectionEnd() if has_selection else None

        # Если есть выделение — emit удаления
        if has_selection:
            self.rangeDeleted.emit(sel_start, sel_end)

        super().insertFromMimeData(source)

        # Позиция вставки
        insert_pos = sel_start if has_selection else pos_before
        self.textPasted.emit(insert_pos, text)


def style_message_box(msg_box):
    # Новый шаблон диалога уже оформлен — старая доводка ему не нужна
    from general_functions.app_dialog import AppDialog
    if isinstance(msg_box, AppDialog):
        return
    msg_box.setFont(scale_value(QFont("Arial", scaler.scale(13))))
    layout = msg_box.layout()
    if layout:
        spacer = QSpacerItem(
            scaler.scale(450),
            scaler.scale(1),
            QSizePolicy.Fixed,
            QSizePolicy.Fixed
        )
        # Добавляем её в самый низ внутренней сетки виджета
        layout.addItem(spacer, layout.rowCount(), 0, 1, layout.columnCount())
    # -----------------------------------------------------------

    for button in msg_box.buttons():
        button.setFont(scale_value(QFont("Arial", scaler.scale(13))))
        button.setContentsMargins(
            scale_value(15), scale_value(5), scale_value(15), scale_value(5)
        )
        button.setStyleSheet(f"""
            QPushButton {{
                background-color: #546E7A;
                color: white;
                border: 1px solid #37474F;
                border-radius: {scale(5)}px;
                padding: {scale(5)}px {scale(15)}px;
                min-width: {scale(80)}px;
                min-height: {scale(30)}px;
            }}
            QPushButton:hover {{
                background-color: #607D8B;
            }}
            QPushButton:pressed {{
                background-color: #455A64;
            }}
        """)


class DraggableScrollMixin:
    def __init__(self):
        self._drag_pos = None
        self.setMouseTracking(True)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.position().toPoint()
            self.setCursor(Qt.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None:
            delta = event.position().toPoint() - self._drag_pos
            self._drag_pos = event.position().toPoint()
            if hasattr(self, "verticalScrollBar"):
                self.verticalScrollBar().setValue(
                    self.verticalScrollBar().value() - delta.y()
                )
            if hasattr(self, "horizontalScrollBar"):
                self.horizontalScrollBar().setValue(
                    self.horizontalScrollBar().value() - delta.x()
                )
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = None
            self.setCursor(Qt.ArrowCursor)
        super().mouseReleaseEvent(event)

class DraggableListWidget(DraggableScrollMixin, QListWidget):
    def __init__(self, parent=None):
        QListWidget.__init__(self, parent)
        DraggableScrollMixin.__init__(self)
        self.setFont(scale_value(QFont("Arial", scaler.scale(13))))


class DraggableScrollArea(DraggableScrollMixin, QScrollArea):
    def __init__(self, parent=None):
        QScrollArea.__init__(self, parent)
        DraggableScrollMixin.__init__(self)


def resource_path(relative_path):
    """Путь к файлу приложения: в сборке — рядом с программой, иначе —
    от папки приложения (а не от текущей папки запуска)."""
    if hasattr(sys, '_MEIPASS'):                         # PyInstaller
        return os.path.join(sys._MEIPASS, relative_path)
    if "__compiled__" in globals():                      # Nuitka: данные лежат рядом с Kovapp.exe
        return os.path.join(os.path.dirname(sys.executable), relative_path)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, relative_path)
