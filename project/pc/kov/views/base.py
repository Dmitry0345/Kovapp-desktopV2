# -*- coding: utf-8 -*-
"""Основа раздела: поля как у .content сайта, ссылка на оболочку."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget

from general_functions import theme
from kov.ui import rem


class Section(QWidget):
    """Раздел в области содержимого. shell — главное окно (переходы, сокет)."""

    title = ""
    key = ""

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self.shell = shell
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setObjectName("section")
        self.setStyleSheet(f"QWidget#section {{ background: {theme.PAGE_BG}; }}")
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(rem(1.0), rem(0.9), rem(1.0), rem(0.9))
        self.root.setSpacing(rem(0.75))

    # переопределяют разделы
    def on_event(self, event):
        pass

    def on_reconnect(self):
        pass

    def shutdown(self):
        pass
