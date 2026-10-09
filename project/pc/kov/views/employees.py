# -*- coding: utf-8 -*-
"""Раздел «Работники» и профиль сотрудника — как js/views/employees.js.

Таблица ФИО / Должность / Телефон / Отдел, поиск по выбранному полю,
фильтр по должностям, «Показать профиль», «Добавить работника»
(руководство). Профиль выезжает панелью справа: шапка-градиент с фото,
карточки «Личные данные», «Работа», «Контакты», «О сотруднике».

Правка: руководство меняет карточку целиком, сотрудник — только свои
телефон и почту. Перед записью показывается, что именно изменится.
"""

import re
import secrets

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit,
                             QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

from general_functions import theme
from kov import icons, jobs
from kov.api import client, human_error
from kov.dialogs import Modal, confirm, field
from kov.people import PHOTOS, full_name
from kov.store import store
from kov.ui import (Avatar, Button, DataTable, InfoCard, InfoRow, SearchInput, Select, TableEmpty, border, checkbox_qss,
                    clear_layout, font, initials, label, line_edit, muted, rem, round_button, scroll_area, text_qss, toast)
from kov.views.base import Section

STANDARD_POSITIONS = ["Директор", "Администратор", "Старший сотрудник", "Сотрудник", "Главный бухгалтер", "Бухгалтер",
                      "Старший монтажник", "Монтажник", "Старший инженер", "Инженер"]
STANDARD_DEPARTMENTS = ["Монтажный отдел", "Бухгалтерия", "Отдел ТО", "Договорной отдел", "Склад", "Руководство"]
HIDDEN_DEPARTMENTS = ["Администрация"]
FIELD_TITLES = {"last_name": "фамилия", "first_name": "имя", "middle_name": "отчество", "position": "должность",
                "department": "отдел", "status": "права", "phone_number": "телефон", "email": "почта",
                "profile": "о себе"}
SEARCH_MODES = [("fio", "ФИО"), ("department", "Отдел"), ("position", "Должность"), ("phone", "Номер телефона")]
OTHER = "__other__"
STATUS_OPTIONS = [("работник", "Работник"), ("руководитель отдела", "Руководитель отдела"),
                  ("директор", "Директор"), ("администратор", "Администратор")]
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_STATE = {"search": "", "mode": "fio", "positions": set()}


def paint_avatar(p, rect, person, image=None):
    """Кружок: фото или инициалы на цветной подложке (как .avatar сайта)."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    path = QPainterPath()
    path.addEllipse(QRectF(rect))
    if image is not None and not image.isNull():
        p.setClipPath(path)
        sc = image.scaled(rect.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        p.drawImage(rect.left() + (rect.width() - sc.width()) // 2, rect.top() + (rect.height() - sc.height()) // 2, sc)
    else:
        key = ("%s %s" % (person.get("last_name") or "", person.get("first_name") or "")).strip()
        p.fillPath(path, QColor(theme.avatar_color(key)))
        f = QFont(theme.FONT_FAMILY)
        f.setPixelSize(max(6, int(rect.height() * 0.36)))
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(rect, Qt.AlignCenter, initials(person))
    p.restore()


class _NameDelegate(QStyledItemDelegate):
    """Ячейка ФИО: аватар 2.4rem и имя (.name-cell)."""

    def __init__(self, view):
        super().__init__(view.table)
        self.view = view

    def paint(self, painter, option, index):
        e = index.data(Qt.UserRole + 1)
        if not isinstance(e, dict):
            super().paint(painter, option, index)
            return
        painter.save()
        bg = None
        if option.state & QStyle.State_Selected:
            bg = theme.INFO_SOFT
        elif option.state & QStyle.State_MouseOver:
            bg = theme.SURFACE_2
        if bg:
            painter.fillRect(option.rect, QColor(bg))
        r = option.rect
        d = rem(2.4)
        av = QRect(r.left() + rem(0.6), r.top() + (r.height() - d) // 2, d, d)
        img = PHOTOS.get(e.get("id")) if isinstance(e.get("id"), int) else None
        if img is None and e.get("id") in store.photo_ids:
            PHOTOS.request(e["id"])
        paint_avatar(painter, av, e, img)
        painter.setFont(font(0.875))
        painter.setPen(QColor(theme.TEXT_VALUE))
        text_rect = QRect(av.right() + rem(0.45), r.top(), r.right() - av.right() - rem(1.0), r.height())
        name = full_name(e, True) or "Имя не указано"
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft,
                         QFontMetrics(font(0.875)).elidedText(name, Qt.ElideRight, text_rect.width()))
        painter.setPen(QColor(theme.CARD_BORDER))      # граница строки, как у остальных ячеек
        painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
        painter.restore()

    def sizeHint(self, option, index):
        from PySide6.QtCore import QSize
        e = index.data(Qt.UserRole + 1)
        name = (full_name(e, True) or "Имя не указано") if isinstance(e, dict) else ""
        w = QFontMetrics(font(0.875)).horizontalAdvance(name) + rem(0.6) * 2 + rem(2.4) + rem(0.45)
        return QSize(w, rem(2.4) + 2 * rem(0.55))


class EmployeesSection(Section):
    title = "Работники"
    key = "employees"

    def __init__(self, shell, parent=None):
        super().__init__(shell, parent)
        self.people = []
        self.rows = []
        self.selected = None
        bar = QHBoxLayout()
        bar.setSpacing(rem(0.5))
        self.search = SearchInput("Поиск по работникам…")
        self.search.setText(_STATE["search"])
        self.search.textChanged.connect(self._searched)
        bar.addWidget(self.search, 1)
        self.mode = Select(SEARCH_MODES, min_width_rem=11)
        self.mode.set_value(_STATE["mode"])
        self.mode.setToolTip("Поле, по которому идёт поиск")
        self.mode.currentIndexChanged.connect(self._mode)
        bar.addWidget(self.mode)
        self.filter_btn = Button("Должности", "ghost", "filter", "Фильтр по должности")
        self.filter_btn.clicked.connect(self.position_filter)
        bar.addWidget(self.filter_btn)
        self.root.addLayout(bar)
        self.table = DataTable(["ФИО", "Должность", "Телефон", "Отдел"], stretch=[0], left_columns=(0,))
        self.table.verticalHeader().setDefaultSectionSize(rem(2.4) + 2 * rem(0.55) + border())
        self.table.setItemDelegateForColumn(0, _NameDelegate(self))
        self.table.itemSelectionChanged.connect(self._picked)
        self.table.row_activated.connect(lambda r: 0 <= r < len(self.rows) and self.open_profile(self.rows[r]["id"]))
        self.root.addWidget(self.table, 1)
        self.empty = TableEmpty(self.table, "Никого не найдено")
        acts = QHBoxLayout()
        acts.setSpacing(rem(0.5))
        acts.addStretch(1)
        self.profile_btn = Button("Показать профиль", "ghost", "id_card")
        self.profile_btn.setEnabled(False)
        self.profile_btn.clicked.connect(lambda: self.selected and self.open_profile(self.selected["id"]))
        acts.addWidget(self.profile_btn)
        if store.is_management():
            add = Button("Добавить работника", "primary", "plus")
            add.clicked.connect(lambda: add_dialog(self, lambda: self.refresh(True)))
            acts.addWidget(add)
        self.root.addLayout(acts)
        PHOTOS.ready.connect(self._photo_ready)
        self.refresh()

    def _photo_ready(self, *_a):
        try:
            self.table.viewport().update()
        except RuntimeError:
            pass

    def _searched(self, text):
        _STATE["search"] = text
        self.render()

    def _mode(self, _i):
        _STATE["mode"] = self.mode.value()
        self.render()

    def refresh(self, force=False):
        def load():
            store.load_photo_ids(force)
            return store.pickable(force)

        def done(lst):
            self.people = lst or []
            self.render()
        jobs.run(load, done=done, failed=lambda e: toast(self, "Не удалось загрузить работников: " + human_error(e), "error"),
                 owner=self)

    def matches(self, e):
        if _STATE["positions"] and (e.get("position") or "") not in _STATE["positions"]:
            return False
        needle = _STATE["search"].strip().lower()
        if not needle:
            return True
        mode = _STATE["mode"]
        value = (e.get("department") if mode == "department" else e.get("position") if mode == "position"
                 else e.get("phone_number") if mode == "phone" else full_name(e, True))
        return needle in (value or "").lower()

    def render(self):
        rows = sorted([e for e in self.people if self.matches(e)], key=lambda e: full_name(e, True).lower())
        self.rows = rows
        t = self.table
        t.blockSignals(True)
        t.setRowCount(0)
        t.setRowCount(len(rows))
        for r, e in enumerate(rows):
            it = t.cell("", 0, data=e.get("id"))
            it.setData(Qt.UserRole + 1, e)
            it.setToolTip(full_name(e, True))
            t.setItem(r, 0, it)
            t.setItem(r, 1, t.cell(e.get("position") or "Не указана", 1, color=None if e.get("position") else theme.TEXT_MUTED))
            t.setItem(r, 2, t.cell(e.get("phone_number") or "Не указан", 2,
                                   color=None if e.get("phone_number") else theme.TEXT_MUTED))
            t.setItem(r, 3, t.cell(e.get("department") or "Не указан", 3, color=None if e.get("department") else theme.TEXT_MUTED))
        t.blockSignals(False)
        t.fit_columns()
        self.selected = None
        self.profile_btn.setEnabled(False)
        self.empty.setVisible(not rows)
        self.filter_btn.setStyleSheet(self.filter_btn.styleSheet() +
                                      (f"QPushButton {{ background: {theme.WARNING_SOFT}; }}" if _STATE["positions"] else ""))
        if not _STATE["positions"]:
            self.filter_btn.refresh()

    def _picked(self):
        row = self.table.currentRow()
        ok = 0 <= row < len(self.rows) and self.table.selectionModel().hasSelection()
        self.selected = self.rows[row] if ok else None
        self.profile_btn.setEnabled(bool(self.selected))

    def open_profile(self, emp_id):
        self.shell.open_profile(emp_id)

    def position_filter(self):
        positions = sorted(set(e.get("position") or "" for e in self.people if e.get("position")))
        m = Modal(self, "Фильтр по должности", "filter", width_rem=36)
        m.body.addWidget(muted("Выберите должности:", 0.875))
        holder = QFrame()
        holder.setObjectName("checklist")
        holder.setStyleSheet(f"QFrame#checklist {{ border: {border()}px solid {theme.CARD_BORDER}; border-radius: {rem(0.5)}px;"
                             f" background: {theme.CARD_BG}; }}")
        lst = QVBoxLayout(holder)
        lst.setContentsMargins(rem(0.4), rem(0.4), rem(0.4), rem(0.4))
        lst.setSpacing(rem(0.25))
        boxes = []
        for pos in positions:
            cb = QCheckBox(pos)
            cb.setProperty("row", True)
            cb.setStyleSheet(checkbox_qss())
            cb.setFont(font(0.875))
            cb.setChecked(pos in _STATE["positions"])
            lst.addWidget(cb)
            boxes.append(cb)
        lst.addStretch(1)
        area = scroll_area(holder)
        area.setMaximumHeight(rem(16))
        area.setMinimumHeight(min(rem(16), rem(2.3) * max(1, len(positions)) + rem(1)))
        m.body.addWidget(area)

        def reset(_m):
            _STATE["positions"] = set()
            self.render()
            return True

        def apply(_m):
            _STATE["positions"] = set(cb.text() for cb in boxes if cb.isChecked())
            self.render()
            return True
        m.add_button("Сбросить", "ghost", None, reset)
        m.add_button("Применить", "primary", "check", apply)
        m.exec()

    def on_event(self, evt):
        if str(evt.get("type") or "").startswith("employee."):
            self.refresh(True)

    def on_reconnect(self):
        self.refresh(True)


# ══════════════════════════════════════════════════════════════════════════
# Профиль — панель справа
# ══════════════════════════════════════════════════════════════════════════

class _ProfileHead(QFrame):
    """Шапка профиля: градиент цветов приложения, фото, имя, должность и отдел."""

    def paintEvent(self, e):
        from PySide6.QtGui import QLinearGradient
        p = QPainter(self)
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0.0, QColor(theme.HEADER_TOP))
        g.setColorAt(0.55, QColor(theme.HEADER_MID))
        g.setColorAt(1.0, QColor(theme.HEADER_BOTTOM))
        p.fillRect(self.rect(), g)
        p.end()


class _BigAvatar(QWidget):
    """Аватар 8rem с белой рамкой и тенью; нажатие на фото — просмотр."""

    def __init__(self, person, on_open=None):
        super().__init__()
        self.person = person
        self.img = None
        self.on_open = on_open
        d = rem(8)
        self.setFixedSize(d + rem(0.8), d + rem(0.8))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        d = rem(8)
        off = rem(0.4)
        # тень
        for k in range(6, 0, -1):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 10))
            p.drawEllipse(QRectF(off - k * 0.5, off + rem(0.25) - k * 0.5 + 2, d + k, d + k))
        ring = rem(0.22)
        p.setBrush(QColor(255, 255, 255, 230))
        p.drawEllipse(QRectF(off, off, d, d))
        paint_avatar(p, QRect(int(off + ring), int(off + ring), int(d - 2 * ring), int(d - 2 * ring)), self.person, self.img)
        p.end()

    def mouseReleaseEvent(self, e):
        if self.img is not None and self.on_open:
            self.on_open()


class ProfilePanel(QWidget):
    def __init__(self, shell, employee_id):
        super().__init__()
        self.shell = shell
        self.emp_id = int(employee_id)
        self.e = {"id": self.emp_id}
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setObjectName("profilePanel")
        self.setStyleSheet(f"QWidget#profilePanel {{ background: {theme.PAGE_BG}; }}")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(0)
        self.lay.addWidget(muted("Загрузка профиля…", 0.875), 0, Qt.AlignCenter)
        self.load()

    def load(self):
        def work():
            try:
                e = client.get("employees/%d" % self.emp_id)
            except Exception:
                e = None
            store.load_photo_ids(True)
            if not e:
                e = dict(store.me or {}, id=self.emp_id)
            return e

        def done(e):
            self.e = e or {"id": self.emp_id}
            self.render()
        jobs.run(work, done=done, owner=self)

    def render(self):
        e = self.e
        clear_layout(self.lay)
        own = e.get("id") == client.user_id
        management = store.is_management()
        head = _ProfileHead()
        hl = QVBoxLayout(head)
        hl.setContentsMargins(rem(0.9), rem(0.7), rem(0.9), rem(1.4))
        hl.setSpacing(0)
        row = QHBoxLayout()
        row.setSpacing(rem(0.4))
        row.addWidget(label("Мой профиль" if own else "Профиль", 1.0, "#FFFFFF", bold=True))
        row.addStretch(1)

        def head_btn(text, glyph, tip, handler):
            b = Button(text, "ghost", glyph, tip)
            b.setStyleSheet(b.styleSheet() + "QPushButton { background: rgba(255,255,255,41); border-color: rgba(255,255,255,77);"
                                             " color: #FFFFFF; } QPushButton:hover { background: rgba(255,255,255,66); }")
            b.setIcon(icons.icon(glyph, rem(1.05 if not text else 1.1), "#FFFFFF"))
            b.clicked.connect(handler)
            row.addWidget(b)
        if management:
            head_btn("", "edit", "Изменить данные", lambda: edit_dialog(self, e, self.saved))
        elif own:
            head_btn("", "edit", "Изменить телефон и почту", lambda: contacts_dialog(self, e, self.saved))
        if management and not own:
            head_btn("", "trash", "Удалить сотрудника", self.delete_employee)
        if own:
            head_btn("Выход", "logout", "", lambda: self.shell.logout())
        head_btn("", "close", "Закрыть", self.shell.close_panel)
        hl.addLayout(row)
        hl.addSpacing(rem(1.0) - rem(0.4))
        av = _BigAvatar(e, self.open_photo)
        wrap = QWidget()
        wrap.setFixedSize(av.size())
        av.setParent(wrap)
        if own or management:
            cam = Button("", "ghost", "camera", "Сменить фото", wrap)
            side = rem(2.4)
            round_button(cam, side, theme.CARD_BG, theme.CARD_BG, theme.CHIP_BG)
            cam.setIcon(icons.icon("camera", rem(1.05), theme.ACCENT))
            cam.move(av.width() - side - rem(0.3), av.height() - side - rem(0.3))
            cam.clicked.connect(self.pick_photo)
        hl.addWidget(wrap, 0, Qt.AlignHCenter)
        self.avatar = av
        img = PHOTOS.get(e.get("id"))
        if img is not None:
            av.img = img
            av.setCursor(Qt.PointingHandCursor)
            av.setToolTip("Открыть фото")
        elif e.get("id") in store.photo_ids:
            PHOTOS.ready.connect(self._photo_ready)
            PHOTOS.request(e["id"])
        hl.addSpacing(rem(0.7) - rem(0.4))
        hl.addWidget(label(full_name(e, True) or "Имя не указано", 1.3, "#FFFFFF", bold=True, align=Qt.AlignHCenter,
                           wrap=True))
        sub = " • ".join(x for x in (e.get("position"), e.get("department")) if x)
        if sub:
            hl.addSpacing(rem(0.25))
            hl.addWidget(label(sub, 0.85, "rgba(255,255,255,209)", align=Qt.AlignHCenter, wrap=True))
        self.lay.addWidget(head)
        body = QWidget()
        body.setStyleSheet(f"background: {theme.PAGE_BG};")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(rem(1.0), rem(1.0), rem(1.0), rem(1.0))
        bl.setSpacing(rem(0.75))

        def card(glyph, caption, rows):
            c = InfoCard(caption, glyph)
            for key, value, copy in rows:
                if not key:
                    c.body.addWidget(label(value or "", 0.875, theme.TEXT_VALUE, wrap=True, selectable=True))
                    continue
                r = InfoRow(key, value or "—")
                if copy and value:
                    r.value.setText("<span style='text-decoration: underline dotted'>%s</span>" % value)
                    r.value.setTextFormat(Qt.RichText)
                    r.value.setCursor(Qt.PointingHandCursor)
                    r.value.setToolTip("Скопировать")
                    r.value.mousePressEvent = lambda _e, v=value: self.copy(v)
                c.body.addWidget(r)
            return c
        bl.addWidget(card("id_card", "Личные данные", [("Фамилия", e.get("last_name"), False),
                                                        ("Имя", e.get("first_name"), False),
                                                        ("Отчество", e.get("middle_name") or "—", False)]))
        bl.addWidget(card("flag", "Работа", [("Отдел", e.get("department") or "—", False),
                                              ("Должность", e.get("position") or "—", False)]))
        bl.addWidget(card("phone", "Контакты", [("Номер телефона", e.get("phone_number"), True),
                                                 ("Электронная почта", e.get("email"), True)]))
        if e.get("profile"):
            bl.addWidget(card("note", "О сотруднике", [("", e["profile"], False)]))
        bl.addStretch(1)
        self.lay.addWidget(scroll_area(body), 1)

    def _photo_ready(self, emp_id, img):
        if emp_id != self.e.get("id"):
            return
        try:
            self.avatar.img = img
            self.avatar.setCursor(Qt.PointingHandCursor)
            self.avatar.setToolTip("Открыть фото")
            self.avatar.update()
        except RuntimeError:
            pass

    def copy(self, value):
        QApplication.clipboard().setText(value)
        toast(self, "Скопировано", "success")

    def open_photo(self):
        from kov.viewer import MediaViewer
        src = client.file_url("employees/%d/photo" % self.e["id"])
        MediaViewer.show_list(self.shell, [{"src": src, "filename": "Фото сотрудника", "download": src}], 0)

    def pick_photo(self):
        from kov.photo_crop import pick
        got = pick(self, "Фото сотрудника", round_mask=True)
        if not got:
            return
        name, data = got

        def work():
            client.post("employees/%d/photo" % self.e["id"], files={"file": (name, data, "image/jpeg")})
            store.load_photo_ids(True)

        def done(_):
            PHOTOS.forget(self.e["id"])
            toast(self, "Фото обновлено", "success")
            self.render()
            cur = self.shell.current
            if isinstance(cur, EmployeesSection):
                cur.refresh(True)
        jobs.run(work, done=done, failed=lambda err: toast(self, "Не удалось загрузить фото: " + human_error(err), "error"),
                 owner=self)

    def saved(self, updated):
        self.e.update(updated or {})
        if self.e.get("id") == client.user_id and store.me is not None:
            store.me.update(updated or {})
        self.render()
        cur = self.shell.current
        if isinstance(cur, EmployeesSection):
            cur.refresh(True)

    def delete_employee(self):
        e = self.e
        if not confirm(self, "Удаление сотрудника", "Удалить сотрудника «%s»? Его задачи и файлы останутся, учётная запись "
                                                     "будет закрыта." % full_name(e, True), "Удалить", danger=True):
            return

        def done(_):
            toast(self.shell, "Сотрудник удалён", "success")
            self.shell.close_panel()
            cur = self.shell.current
            if isinstance(cur, EmployeesSection):
                cur.refresh(True)
        jobs.run(client.delete, "employees/%d" % e["id"], done=done,
                 failed=lambda err: toast(self, "Не удалось удалить: " + human_error(err), "error"), owner=self)


# ══════════════════════════════════════════════════════════════════════════
# Формы
# ══════════════════════════════════════════════════════════════════════════

def _row(*widgets):
    w = QWidget()
    w.setStyleSheet("background: transparent;")
    g = QGridLayout(w)
    g.setContentsMargins(0, 0, 0, 0)
    g.setHorizontalSpacing(rem(0.75))
    for i, x in enumerate(widgets):
        g.addWidget(x, 0, i, Qt.AlignTop)
        g.setColumnStretch(i, 1)
    return w


class PersonForm:
    def __init__(self, values=None, credentials=False):
        values = values or {}
        positions = list(dict.fromkeys(STANDARD_POSITIONS + list(store.positions or [])))
        departments = [d for d in dict.fromkeys([d for d in (store.departments or []) if d] + STANDARD_DEPARTMENTS)
                       if d not in HIDDEN_DEPARTMENTS]
        self.w = QWidget()
        self.w.setStyleSheet("background: transparent;")
        col = QVBoxLayout(self.w)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(rem(0.7))
        self.f = {}
        f = self.f
        f["last_name"] = line_edit("Фамилия", values.get("last_name") or "")
        f["first_name"] = line_edit("Имя", values.get("first_name") or "")
        f["middle_name"] = line_edit("Отчество", values.get("middle_name") or "")
        f["department"] = Select([(d, d) for d in departments])
        f["department"].set_value(values.get("department") if values.get("department") in departments else departments[0])
        f["position"] = Select([(p, p) for p in positions] + [(OTHER, "Ввести другую")])
        pos = values.get("position")
        f["position"].set_value(pos if pos in positions else (OTHER if pos else positions[0]))
        f["status"] = Select(STATUS_OPTIONS)
        f["status"].set_value(store.status_of(values))
        f["other_position"] = line_edit("Название должности", pos if pos and pos not in positions else "")
        f["phone_number"] = line_edit("Номер телефона", values.get("phone_number") or "")
        f["email"] = line_edit("example@domain.com", values.get("email") or "")
        f["profile"] = QPlainTextEdit(values.get("profile") or "")
        f["profile"].setPlaceholderText("Профиль (описание)")
        f["profile"].setFont(font(0.875))
        f["profile"].setStyleSheet(text_qss())
        f["profile"].setFixedHeight(rem(5.5))
        if credentials:
            f["username"] = line_edit("Оставьте пустым — создастся автоматически")
            f["password"] = line_edit("Оставьте пустым — сгенерируется")
            col.addWidget(_row(field("Логин:", f["username"]), field("Пароль:", f["password"])))
        col.addWidget(_row(field("Фамилия:", f["last_name"], True), field("Имя:", f["first_name"], True),
                           field("Отчество:", f["middle_name"])))
        col.addWidget(_row(field("Отдел:", f["department"]), field("Должность:", f["position"])))
        self.other = field("Другая должность:", f["other_position"])
        col.addWidget(self.other)
        note = label("Руководитель отдела ведёт задачи своего отдела, директор и администратор — все", 0.8, theme.TEXT_MUTED,
                     wrap=True)
        nb = QWidget()
        nb.setStyleSheet("background: transparent;")
        nl = QVBoxLayout(nb)
        nl.setContentsMargins(0, 0, 0, rem(0.4))
        nl.addStretch(1)
        nl.addWidget(note)
        rights_row = _row(field("Права:", f["status"]), nb)
        rights_row.layout().setAlignment(nb, Qt.AlignBottom)
        col.addWidget(rights_row)
        col.addWidget(_row(field("Номер телефона:", f["phone_number"]), field("Электронная почта:", f["email"])))
        col.addWidget(field("Профиль (Описание):", f["profile"]))
        f["position"].currentIndexChanged.connect(lambda _i: self.sync())
        self.sync()

    def sync(self):
        self.other.setVisible(self.f["position"].value() == OTHER)

    def collect(self):
        f = self.f
        position = f["other_position"].text().strip() if f["position"].value() == OTHER else f["position"].value()
        return {"last_name": f["last_name"].text().strip(), "first_name": f["first_name"].text().strip(),
                "middle_name": f["middle_name"].text().strip() or None, "department": f["department"].value(),
                "position": position, "status": f["status"].value(),
                "phone_number": f["phone_number"].text().strip() or None, "email": f["email"].text().strip() or None,
                "profile": f["profile"].toPlainText().strip() or None}

    @staticmethod
    def validate(data):
        if not data["last_name"] or not data["first_name"]:
            return "Фамилия и имя обязательны"
        if not data["position"]:
            return "Поле 'Другая должность' не может быть пустым"
        if data["email"] and not EMAIL_RE.match(data["email"]):
            return "Проверьте адрес электронной почты"
        return None


def generate_password(length=8):
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(chars) for _ in range(length))


def add_dialog(owner, on_done=None):
    store.lists()
    form = PersonForm({}, credentials=True)
    m = Modal(owner, "Добавить работника", "user", width_rem=52, scroll=False)
    m.body.addWidget(form.w)

    def create(_m):
        data = form.collect()
        err = form.validate(data)
        if err:
            toast(m, err, "warning")
            return False
        import time
        username = form.f["username"].text().strip() or ("user%d" % int(time.time()))
        password = form.f["password"].text().strip() or generate_password(8)
        try:
            client.post("employees", json_body=dict({"username": username, "password": password}, **data))
        except Exception as e:
            toast(m, "Не удалось создать: " + human_error(e), "error")
            return False
        m.accept()
        credentials_dialog(owner, username, password)
        if on_done:
            on_done()
        return True
    m.add_button("Отмена", "ghost")
    m.add_button("Создать", "primary", "check", create)
    m.exec()


def credentials_dialog(owner, username, password):
    m = Modal(owner, "Учетные данные нового пользователя", "key", width_rem=30)
    m.body.addWidget(muted("Передайте сотруднику. Пароль показывается только сейчас.", 0.875))
    c = InfoCard("Вход", "key")

    def copy(v):
        QApplication.clipboard().setText(v)
        toast(m, "Скопировано", "success")
    for k, v in (("Логин", username), ("Пароль", password)):
        r = InfoRow(k, v)
        r.value.setCursor(Qt.PointingHandCursor)
        r.value.setToolTip("Скопировать")
        r.value.mousePressEvent = lambda _e, val=v: copy(val)
        c.body.addWidget(r)
    m.body.addWidget(c)
    m.add_button("Скопировать оба", "ghost", "copy", lambda _m: (copy("Логин: %s\nПароль: %s" % (username, password)), False)[1])
    m.add_button("Готово", "primary", "check", lambda _m: True)
    m.exec()


def contacts_dialog(owner, e, on_saved):
    phone = line_edit("+7 (999) 000-00-00", e.get("phone_number") or "")
    email = line_edit("name@example.com", e.get("email") or "")
    m = Modal(owner, "Мои контакты", "id_card", width_rem=30)
    m.body.addWidget(label("Изменить можно только телефон и почту. Должность, отдел и права меняет директор или "
                           "администратор.", 0.8, theme.TEXT_MUTED, wrap=True))
    m.body.addWidget(field("Телефон", phone))
    m.body.addWidget(field("Электронная почта", email))

    def save(_m):
        data = {"phone_number": phone.text().strip(), "email": email.text().strip()}
        if data["email"] and not EMAIL_RE.match(data["email"]):
            toast(m, "Проверьте адрес почты", "warning")
            return False
        changes = []
        if data["phone_number"] != (e.get("phone_number") or ""):
            changes.append("телефон: %s → %s" % (e.get("phone_number") or "—", data["phone_number"] or "—"))
        if data["email"] != (e.get("email") or ""):
            changes.append("почта: %s → %s" % (e.get("email") or "—", data["email"] or "—"))
        if not changes:
            toast(owner, "Ничего не изменилось", "info")
            return True
        if not confirm(m, "Сохранить изменения?", "\n".join(changes), "Сохранить", glyph="save"):
            return False
        try:
            updated = client.put("employees/%d" % e["id"], json_body=data)
        except Exception as err:
            toast(m, "Не удалось сохранить: " + human_error(err), "error")
            return False
        toast(owner, "Контакты обновлены", "success")
        on_saved(updated if isinstance(updated, dict) else data)
        return True
    m.add_button("Отмена", "ghost")
    m.add_button("Сохранить", "primary", "save", save)
    m.exec()


def edit_dialog(owner, e, on_saved):
    store.lists()
    form = PersonForm(e)
    m = Modal(owner, "Редактировать профиль", "edit", width_rem=52)
    m.body.addWidget(form.w)

    def save(_m):
        data = form.collect()
        err = form.validate(data)
        if err:
            toast(m, err, "warning")
            return False
        diff = [k for k in data if str(data[k] if data[k] is not None else "") != str(e.get(k) if e.get(k) is not None else "")]
        if not diff:
            toast(owner, "Ничего не изменилось", "info")
            return True
        if not confirm(m, "Сохранить изменения?", "Будут изменены поля: " + ", ".join(FIELD_TITLES.get(k, k) for k in diff),
                       "Сохранить", glyph="save"):
            return False
        try:
            updated = client.put("employees/%d" % e["id"], json_body=data)
        except Exception as ex:
            toast(m, "Не удалось сохранить: " + human_error(ex), "error")
            return False
        toast(owner, "Данные профиля успешно обновлены.", "success")
        on_saved(updated if isinstance(updated, dict) else data)
        return True
    m.add_button("Отмена", "ghost")
    m.add_button("Сохранить", "primary", "save", save)
    m.exec()
