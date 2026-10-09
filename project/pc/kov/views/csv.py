# -*- coding: utf-8 -*-
"""Раздел «Файлы CSV» (руководство) — как js/views/csv.js веб-версии.

Импорт и экспорт пользователей и организаций: режим, тип данных, поле
для перетаскивания файла и кнопка «Импортировать» / «Скачать CSV».
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QFileDialog, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from general_functions import theme
from kov import icons, jobs
from kov.api import client, human_error
from kov.ui import Button, Card, Segmented, border, label, rem, toast
from kov.views.base import Section


class _Drop(QFrame):
    """Поле «Перетащите файл сюда» (.csv-drop): пунктирная рамка, нажатие — выбор файла."""

    def __init__(self, on_file, on_click):
        super().__init__()
        self.on_file = on_file
        self.on_click = on_click
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.setObjectName("csvDrop")
        self.setMinimumHeight(rem(11))
        self._paint(False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(rem(1), rem(1), rem(1), rem(1))
        lay.setSpacing(rem(0.5))
        lay.addStretch(1)
        ic = QLabel()
        ic.setPixmap(icons.pixmap("upload", rem(2.4), theme.ACCENT_SOFT))
        ic.setAlignment(Qt.AlignCenter)
        ic.setStyleSheet("background: transparent; border: none;")
        lay.addWidget(ic)
        self.text = label("Перетащите файл сюда", 0.875, theme.TEXT_VALUE, align=Qt.AlignCenter)
        lay.addWidget(self.text)
        lay.addSpacing(rem(0.5))
        lay.addWidget(label("или нажмите, чтобы выбрать", 0.75, theme.TEXT_LABEL, align=Qt.AlignCenter))
        lay.addStretch(1)

    def _paint(self, active):
        b = max(2, round(rem(0.12)))
        self.setStyleSheet(f"QFrame#csvDrop {{ border: {b}px dashed {theme.ACCENT if active else theme.CARD_BORDER};"
                           f" border-radius: {rem(0.75)}px; background: transparent; }}"
                           f"QFrame#csvDrop:hover {{ border-color: {theme.ACCENT_SOFT}; background: {theme.SURFACE_2}; }}")

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.on_click()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._paint(True)

    def dragLeaveEvent(self, e):
        self._paint(False)

    def dropEvent(self, e):
        self._paint(False)
        urls = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if urls:
            self.on_file(urls[0])


class CsvSection(Section):
    title = "Файлы CSV"
    key = "csv"

    def __init__(self, shell, parent=None):
        super().__init__(shell, parent)
        self.mode = "import"
        self.type = "users"
        self.file = None
        card = Card(name="csvCard", padding=(0.9, 1.0), spacing=0.8)
        card.body.addWidget(label("Режим", 0.875, theme.TEXT_VALUE, bold=True))
        self.mode_seg = Segmented([("import", "Импорт", "upload"), ("export", "Экспорт", "download")], "import")
        self.mode_seg.changed.connect(self._mode)
        card.body.addWidget(self.mode_seg, 0, Qt.AlignLeft)
        card.body.addWidget(label("Тип данных", 0.875, theme.TEXT_VALUE, bold=True))
        self.type_seg = Segmented([("users", "Пользователи", "users"), ("organizations", "Организации", "building")], "users")
        self.type_seg.changed.connect(lambda k: setattr(self, "type", k))
        card.body.addWidget(self.type_seg, 0, Qt.AlignLeft)
        self.drop = _Drop(self.set_file, self.choose)
        card.body.addWidget(self.drop)
        acts = QHBoxLayout()
        acts.setSpacing(rem(0.5))
        acts.addStretch(1)
        self.pick = Button("Выбрать файл", "ghost", "folder")
        self.pick.clicked.connect(self.choose)
        acts.addWidget(self.pick)
        self.run_btn = Button("Импортировать", "primary", "upload")
        self.run_btn.clicked.connect(self.run)
        acts.addWidget(self.run_btn)
        card.body.addLayout(acts)
        self.result = label("", 0.8, theme.TEXT_MUTED, wrap=True)
        self.result.hide()
        card.body.addWidget(self.result)
        self.root.addWidget(card)
        self.root.addStretch(1)

    def _mode(self, key):
        self.mode = key
        imp = key == "import"
        self.drop.setVisible(imp)
        self.pick.setVisible(imp)
        self.run_btn.setText("Импортировать" if imp else "Скачать CSV")
        self.run_btn.set_glyph("upload" if imp else "download")

    def choose(self):
        path, _ = QFileDialog.getOpenFileName(self, "Файл CSV", "", "Файлы CSV (*.csv);;Все файлы (*)")
        if path:
            self.set_file(path)

    def set_file(self, path):
        self.file = path
        self.drop.text.setText(os.path.basename(path) if path else "Перетащите файл сюда")

    def run(self):
        self.result.hide()
        kind = self.type
        if self.mode == "import":
            if not self.file:
                toast(self, "Выберите CSV-файл", "warning")
                return
            path = self.file

            def work():
                with open(path, "rb") as f:
                    return client.post("csv/%s/import" % kind, files={"file": (os.path.basename(path), f, "text/csv")},
                                       timeout=(10, 300))

            def done(r):
                self.result.setText(" ".join(str(v) for v in r.values()) if isinstance(r, dict) else str(r or ""))
                self.result.setVisible(bool(self.result.text()))
                toast(self, "Импорт завершён", "success")
            jobs.run(work, done=done, failed=lambda e: toast(self, "Ошибка: " + human_error(e), "error"), owner=self)
            return
        name = "users.csv" if kind == "users" else "organizations.csv"
        dest, _ = QFileDialog.getSaveFileName(self, "Сохранить CSV", name, "Файлы CSV (*.csv)")
        if not dest:
            return

        def work():
            return client.download("csv/%s/export" % kind, dest)

        def done(_):
            toast(self, "Файл сохранён: " + os.path.basename(dest), "success")
        jobs.run(work, done=done, failed=lambda e: toast(self, "Ошибка: " + human_error(e), "error"), owner=self)
