# -*- coding: utf-8 -*-
"""Раздел «Организации» — как js/views/organizations.js веб-версии.

Список с поиском (крупные строки со значком здания), просмотр в панели
справа, создание и правка отдельной страницей: название, описание,
телефоны и почта с описаниями. Править справочник могут руководство,
руководители отделов и Договорной отдел — как решает сервер.
"""

from PySide6.QtCore import QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import (QApplication, QGridLayout, QHBoxLayout, QPlainTextEdit, QStyle, QStyledItemDelegate,
                             QVBoxLayout, QWidget)

from general_functions import theme
from kov import icons, jobs, rights
from kov.api import client, human_error
from kov.dialogs import confirm, field
from kov.fmt import fmt_datetime
from kov.people import full_name
from kov.store import store
from kov.ui import (Button, Card, DataTable, InfoCard, InfoRow, SearchInput, TableEmpty, border, clear_layout, empty, font, fs, label,
                    line_edit, loading, muted, rem, scroll_area, text_qss, toast)
from kov.views.base import Section

_STATE = {"search": ""}


class _NameDelegate(QStyledItemDelegate):
    """Название с значком здания (.name-cell), строки «roomy»: шрифт 1.02rem."""

    def paint(self, painter, option, index):
        painter.save()
        bg = theme.INFO_SOFT if option.state & QStyle.State_Selected else (
            theme.SURFACE_2 if option.state & QStyle.State_MouseOver else None)
        if bg:
            painter.fillRect(option.rect, QColor(bg))
        r = option.rect
        size = rem(1.25)
        x = r.left() + rem(0.7)
        painter.drawPixmap(x, r.top() + (r.height() - size) // 2, icons.pixmap("building", size, theme.ACCENT))
        painter.setFont(font(1.02))
        painter.setPen(QColor(theme.TEXT_VALUE))
        tr = QRect(x + size + rem(0.45), r.top(), r.right() - x - size - rem(1.0), r.height())
        painter.drawText(tr, Qt.AlignVCenter | Qt.AlignLeft,
                         QFontMetrics(font(1.02)).elidedText(index.data() or "", Qt.ElideRight, tr.width()))
        painter.setPen(QColor(theme.CARD_BORDER))      # граница строки, как у остальных ячеек
        painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
        painter.restore()

    def sizeHint(self, option, index):
        w = QFontMetrics(font(1.02)).horizontalAdvance(index.data() or "") + rem(0.7) * 2 + rem(1.7)
        return QSize(w, QFontMetrics(font(1.02)).height() + 2 * rem(0.8))


class OrganizationsSection(Section):
    title = "Организации"
    key = "organizations"

    def __init__(self, shell, parent=None):
        super().__init__(shell, parent)
        self.list = []
        self.rows = []
        self.selected = None
        self.search = SearchInput("Поиск по организациям…")
        self.search.setText(_STATE["search"])
        self.search.textChanged.connect(self._searched)
        self.root.addWidget(self.search)
        self.table = DataTable(["Название организации"], stretch=[0], left_columns=(0,))
        hdr = self.table.horizontalHeader()
        hdr.setFont(font(0.86, bold=True))
        hdr.setFixedHeight(QFontMetrics(font(0.86, bold=True)).height() + 2 * rem(0.7) + border())
        self.table.setStyleSheet(self.table.styleSheet() + f"QHeaderView::section {{ font-size: {fs(0.86)}; }}")
        self.table.verticalHeader().setDefaultSectionSize(QFontMetrics(font(1.02)).height() + 2 * rem(0.8) + border())
        self.table.setItemDelegateForColumn(0, _NameDelegate(self.table))
        self.table.itemSelectionChanged.connect(self._picked)
        self.table.row_activated.connect(lambda r: 0 <= r < len(self.rows) and open_view(self.shell, self.rows[r]["id"]))
        self.root.addWidget(self.table, 1)
        self.empty = TableEmpty(self.table, "Организаций нет")
        self.edit_btn = self.del_btn = None
        if rights.can_edit_organizations():
            acts = QHBoxLayout()
            acts.setSpacing(rem(0.5))
            acts.addStretch(1)
            self.edit_btn = Button("Редактировать", "ghost", "edit")
            self.edit_btn.clicked.connect(lambda: self.selected and self.shell.go("organization_edit",
                                                                                  organization_id=self.selected["id"]))
            self.del_btn = Button("Удалить", "danger", "trash")
            self.del_btn.clicked.connect(self.delete_selected)
            add = Button("Добавить организацию", "primary", "plus")
            add.clicked.connect(lambda: self.shell.go("organization_new"))
            for b in (self.edit_btn, self.del_btn, add):
                acts.addWidget(b)
            self.root.addLayout(acts)
        self._soft = QTimer(self)
        self._soft.setSingleShot(True)
        self._soft.timeout.connect(lambda: self.refresh(keep=True))
        self._poll = QTimer(self)
        self._poll.setInterval(60000)
        self._poll.timeout.connect(lambda: self._soft.start(400))
        self._poll.start()
        self._update_buttons()
        self.refresh()

    def _searched(self, text):
        _STATE["search"] = text
        self.render()

    def refresh(self, keep=False):
        def done(lst):
            self.list = lst or []
            self.render(keep)
        jobs.run(client.get, "organizations", done=done,
                 failed=(lambda e: None) if keep else
                 (lambda e: toast(self, "Не удалось отобразить организации: " + human_error(e), "error")), owner=self)

    def render(self, keep=False):
        needle = _STATE["search"].strip().lower()
        rows = sorted([o for o in self.list if not needle or needle in (o.get("name") or "").lower()],
                      key=lambda o: (o.get("name") or "").lower())
        keep_id = self.selected.get("id") if (keep and self.selected) else None
        self.rows = rows
        t = self.table
        t.blockSignals(True)
        t.setRowCount(0)
        t.setRowCount(len(rows))
        for r, o in enumerate(rows):
            it = t.cell(o.get("name") or "", 0, data=o.get("id"))
            t.setItem(r, 0, it)
            if o.get("id") == keep_id:
                t.selectRow(r)
        t.blockSignals(False)
        t.fit_columns()
        if keep_id is None:
            self.selected = None
        self._update_buttons()
        self.empty.setVisible(not rows)

    def _picked(self):
        row = self.table.currentRow()
        ok = 0 <= row < len(self.rows) and self.table.selectionModel().hasSelection()
        self.selected = self.rows[row] if ok else None
        self._update_buttons()

    def _update_buttons(self):
        for b in (self.edit_btn, self.del_btn):
            if b is not None:
                b.setEnabled(bool(self.selected))

    def delete_selected(self):
        o = self.selected
        if not o:
            return
        if not confirm(self, "Удаление организации", "Удалить организацию «%s»?" % o.get("name"), "Удалить", danger=True):
            return

        def done(_):
            toast(self, "Организация удалена", "success")
            self.refresh()
        jobs.run(client.delete, "organizations/%s" % o["id"], done=done,
                 failed=lambda e: toast(self, "Не удалось удалить: " + human_error(e), "error"), owner=self)

    def on_event(self, evt):
        if str(evt.get("type") or "").startswith("organization."):
            self._soft.start(400)

    def on_reconnect(self):
        self._soft.start(400)


def _person_name(value):
    """Сервер хранит автора числом — показываем фамилию и имя."""
    if value in (None, ""):
        return None
    if not str(value).strip().isdigit():
        return str(value)
    try:
        p = next((e for e in store.employees() if e.get("id") == int(value)), None)
        return full_name(p, True) if p else "Сотрудник №%s" % value
    except Exception:
        return "Сотрудник №%s" % value


def open_view(shell, org_id):
    """Просмотр в панели справа."""
    def load():
        o = client.get("organizations/%s" % org_id)
        return o, _person_name(o.get("who_create")), _person_name(o.get("who_edit"))

    def done(res):
        o, created_by, edited_by = res
        panel = QWidget()
        panel.setStyleSheet(f"background: {theme.CARD_BG};")
        col = QVBoxLayout(panel)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        head = QWidget()
        head.setObjectName("panelHead")
        head.setStyleSheet(f"QWidget#panelHead {{ background: {theme.CARD_BG}; border-bottom: {border()}px solid {theme.CARD_BORDER}; }}")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(rem(1.0), rem(0.8), rem(1.0), rem(0.8))
        hl.setSpacing(rem(0.6))
        back = Button("", "ghost", "arrow_back", "Закрыть")
        back.clicked.connect(shell.close_panel)
        hl.addWidget(back)
        hl.addWidget(label("Просмотр организации", 1.0, theme.TEXT_VALUE, bold=True), 1)
        if rights.can_edit_organizations():
            ed = Button("", "ghost", "edit", "Редактировать")
            ed.clicked.connect(lambda: (shell.close_panel(), shell.go("organization_edit", organization_id=o["id"])))
            hl.addWidget(ed)
        col.addWidget(head)
        body = QWidget()
        body.setStyleSheet(f"background: {theme.CARD_BG};")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(rem(1.0), rem(1.0), rem(1.0), rem(1.0))
        bl.setSpacing(rem(0.8))

        def copy(v):
            QApplication.clipboard().setText(v)
            toast(shell, "Скопировано", "success")

        def card(glyph, caption, rows):
            c = InfoCard(caption, glyph)
            for key, value, can_copy in rows:
                r = InfoRow(key, value or "—")
                if can_copy and value:
                    r.value.setText("<span style='text-decoration: underline dotted'>%s</span>" % value)
                    r.value.setTextFormat(Qt.RichText)
                    r.value.setCursor(Qt.PointingHandCursor)
                    r.value.setToolTip("Скопировать")
                    r.value.mousePressEvent = lambda _e, v=value: copy(v)
                c.body.addWidget(r)
            if not rows:
                c.body.addWidget(muted("Нет", 0.875))
            return c
        bl.addWidget(card("building", "Организация", [("Название", o.get("name"), False),
                                                     ("Описание", o.get("description"), False)]))
        bl.addWidget(card("phone", "Номера телефонов", [(p.get("description") or "Телефон", p.get("phone_number"), True)
                                                        for p in o.get("phones") or []]))
        bl.addWidget(card("mail", "Email адреса", [(m.get("description") or "E-mail", m.get("email"), True)
                                                  for m in o.get("emails") or []]))
        bl.addWidget(card("info", "Информация о записи", [
            ("Создал", created_by, False), ("Создано", fmt_datetime(o.get("when_create")) if o.get("when_create") else None, False),
            ("Изменил", edited_by, False), ("Изменено", fmt_datetime(o.get("when_edit")) if o.get("when_edit") else None, False)]))
        row = QHBoxLayout()
        row.addStretch(1)
        fold = Button("Свернуть", "ghost", "arrow_right")
        fold.clicked.connect(shell.close_panel)
        row.addWidget(fold)
        bl.addLayout(row)
        bl.addStretch(1)
        col.addWidget(scroll_area(body), 1)
        shell.open_panel(panel)
    jobs.run(load, done=done, failed=lambda e: toast(shell, "Не удалось открыть: " + human_error(e), "error"), owner=shell)


class OrganizationEdit(Section):
    """Создание и правка организации — отдельная страница (как на сайте)."""
    key = "organizations"

    def __init__(self, shell, org_id=None, parent=None):
        super().__init__(shell, parent)
        self.org_id = org_id
        self.title = "Редактирование организации" if org_id else "Создание новой организации"
        self.root.addWidget(loading("Готовим форму…", big=True), 1)
        if org_id:
            jobs.run(client.get, "organizations/%s" % org_id, done=self.build,
                     failed=lambda e: (toast(self, "Не удалось загрузить: " + human_error(e), "error"),
                                       self.shell.replace("organizations")), owner=self)
        else:
            QTimer.singleShot(0, lambda: self.build({"name": "", "description": "", "phones": [], "emails": []}))

    def build(self, o):
        clear_layout(self.root)
        card = Card(name="formCard", padding=(0.9, 1.0), spacing=0.8)
        self.name = line_edit("Название организации", o.get("name") or "")
        card.body.addWidget(field("Название организации:", self.name, True))
        self.description = QPlainTextEdit(o.get("description") or "")
        self.description.setPlaceholderText("Описание")
        self.description.setFont(font(0.875))
        self.description.setStyleSheet(text_qss())
        self.description.setFixedHeight(rem(5.5))
        card.body.addWidget(field("Описание:", self.description))
        self.phones = QVBoxLayout()
        self.phones.setSpacing(rem(0.5))
        self.emails = QVBoxLayout()
        self.emails.setSpacing(rem(0.5))
        self.lines = {"phone": [], "email": []}
        pw = QWidget()
        pw.setLayout(self.phones)
        self.phones.setContentsMargins(0, 0, 0, 0)
        ew = QWidget()
        ew.setLayout(self.emails)
        self.emails.setContentsMargins(0, 0, 0, 0)
        card.body.addWidget(field("Номера телефонов:", pw))
        add_p = Button("Добавить телефон", "ghost", "plus")
        add_p.clicked.connect(lambda: self.add_line("phone", {}))
        card.body.addWidget(add_p, 0, Qt.AlignLeft)
        card.body.addWidget(field("Email адреса:", ew))
        add_e = Button("Добавить Email", "ghost", "plus")
        add_e.clicked.connect(lambda: self.add_line("email", {}))
        card.body.addWidget(add_e, 0, Qt.AlignLeft)
        for p in o.get("phones") or []:
            self.add_line("phone", p)
        for m in o.get("emails") or []:
            self.add_line("email", m)
        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = Button("Отмена", "ghost")
        cancel.clicked.connect(lambda: self.shell.replace("organizations"))
        self.save_btn = Button("Сохранить", "primary", "save")
        self.save_btn.clicked.connect(self.save)
        btns.addWidget(cancel)
        btns.addWidget(self.save_btn)
        card.body.addLayout(btns)
        self.root.addWidget(card)
        self.root.addStretch(1)

    def add_line(self, kind, value):
        line = QWidget()
        line.setStyleSheet("background: transparent;")
        g = QGridLayout(line)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(rem(0.4))
        main = line_edit("Номер телефона" if kind == "phone" else "example@domain.com",
                         (value.get("phone_number") if kind == "phone" else value.get("email")) or "")
        desc = line_edit("Описание (мобильный, приёмная…)" if kind == "phone" else "Описание", value.get("description") or "")
        rm = Button("", "danger", "trash", "Убрать")
        g.addWidget(main, 0, 0)
        g.addWidget(desc, 0, 1)
        g.addWidget(rm, 0, 2)
        g.setColumnStretch(0, 1)
        g.setColumnStretch(1, 1)
        box = self.phones if kind == "phone" else self.emails
        box.addWidget(line)
        rec = (line, main, desc)
        self.lines[kind].append(rec)

        def remove():
            self.lines[kind].remove(rec)
            box.removeWidget(line)
            line.deleteLater()
        rm.clicked.connect(remove)

    def collect(self, kind):
        out = []
        for _line, main, desc in self.lines[kind]:
            v = main.text().strip()
            if v:
                out.append({("phone_number" if kind == "phone" else "email"): v, "description": desc.text().strip()})
        return out

    def save(self):
        value = self.name.text().strip()
        if not value:
            toast(self, "Укажите название организации", "warning")
            return
        payload = {"name": value, "description": self.description.toPlainText().strip() or None,
                   "phones": self.collect("phone"), "emails": self.collect("email")}
        oid = self.org_id

        def work():
            if oid:
                return client.put("organizations/%s" % oid, json_body=payload)
            return client.post("organizations", json_body=payload)

        def done(_):
            toast(self.shell, "Организация сохранена", "success")
            self.shell.replace("organizations")

        def failed(e):
            self.save_btn.setEnabled(True)
            toast(self, "Не удалось сохранить: " + human_error(e), "error")
        self.save_btn.setEnabled(False)
        jobs.run(work, done=done, failed=failed, owner=self)
