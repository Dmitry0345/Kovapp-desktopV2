# -*- coding: utf-8 -*-
"""Раздел «Проекты» — как js/views/projects.js веб-версии (раскладка компьютера).

Список: таблица со столбцами по создателям проектов (по четыре на страницу,
стрелки и точки), поиск, сортировка, фильтры по организации и создателю,
«Активные / Архивные». Окно проекта: слева задачи проекта с фильтрами,
справа сведения и разделы «Договоры / Медиаконтент / Проект / Сдача»
и широкая карточка «Склад». Завершённый проект — только для просмотра.
"""

from PySide6.QtCore import QDate, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics
from PySide6.QtWidgets import (QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                             QRadioButton, QSizePolicy, QVBoxLayout, QWidget)

from general_functions import theme
from kov import files as kfiles
from kov import icons, jobs, rights
from kov.api import client, human_error
from kov.dialogs import Modal, confirm, field
from kov.fmt import deadline_state, fmt_date, parse_date
from kov.people import avatar, full_name
from kov.store import store
from kov.ui import (Button, Card, Chip, DataTable, DateField, Flow, IconBadge, InfoCard, PersonChip, PickerSelect,
                    SearchInput, SectionHeader, Segmented, Select, border, checkbox_qss, clear_layout, count_label,
                    empty, font, gate, label, line_edit, loading, muted, rem, scroll_area, text_qss, toast)
from kov.views.base import Section

SORTS = [("deadline_desc", "Дата окончания: сначала поздние"), ("deadline_asc", "Дата окончания: сначала ранние"),
         ("name_asc", "Название: А → Я"), ("name_desc", "Название: Я → А"), ("created_desc", "Созданы: сначала новые"),
         ("created_asc", "Созданы: сначала старые")]
TASK_SORTS = [("deadline_asc", "Дата окончания: по возрастанию"), ("deadline_desc", "Дата окончания: по убыванию"),
              ("id_asc", "ID: по возрастанию"), ("id_desc", "ID: по убыванию"), ("created_desc", "Созданы: сначала новые"),
              ("created_asc", "Созданы: сначала старые"), ("name_asc", "Название: А → Я")]
SECTIONS = [("contracts", "Договоры", "file", "Договоры и соглашения по проекту"),
            ("photos", "Медиаконтент", "image", "Фото и видео задач проекта"),
            ("project", "Проект", "folder", "Проектная документация"),
            ("delivery", "Сдача", "check", "Акты, сдача и приёмка")]
WAREHOUSE = ("warehouse", "Склад", "archive", "Ведомость товаров, выдача и остатки")
COLS_PER_PAGE = 4
TASK_LOCKED = "Изменять задачу может руководство или руководитель отдела, к которому относятся все её этапы"


def person_name(p):
    return full_name(p) if p else ""


_LIST_STATE = {"prefs": {"sort": "deadline_desc", "organization": None, "head_id": None}, "loaded": False,
               "archived": False}
_TASK_PREFS = {"prefs": {"state": "all", "sort": "deadline_asc", "department": None, "stage_count": None},
               "loaded": False}


# ══════════════════════════════════════════════════════════════════════════
# Список проектов
# ══════════════════════════════════════════════════════════════════════════

class _ProjectCell(QFrame):
    clicked = Signal(object)
    opened = Signal(object)

    def __init__(self, p, parent=None):
        super().__init__(parent)
        self.p = p
        self.on = False
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("projCell")
        st = deadline_state(p.get("deadline"))
        self.bg = theme.CRITICAL_SOFT if st == "overdue" else (theme.WARNING_SOFT if st == "urgent" else "transparent")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(rem(0.6), rem(0.55), rem(0.6), rem(0.55))
        lay.setSpacing(rem(0.05))
        t = label(p.get("name") or "", 0.875, theme.TEXT_VALUE, bold=True, wrap=True, align=Qt.AlignHCenter)
        lay.addWidget(t)
        if p.get("deadline"):
            color = theme.CRITICAL if st == "overdue" else (theme.WARNING_TEXT if st == "urgent" else theme.TEXT_LABEL)
            lay.addWidget(label("до " + fmt_date(p.get("deadline")), 0.75, color, align=Qt.AlignHCenter))
        tip = [p.get("name")]
        if p.get("organization"):
            tip.append("Организация: " + p["organization"])
        if p.get("responsible"):
            tip.append("Ответственный: " + person_name(p["responsible"]))
        if p.get("creator"):
            tip.append("Создал: " + person_name(p["creator"]))
        tip.append("Задач: %s (активных %s) · файлов: %s" % (p.get("tasks_total") or 0, p.get("tasks_active") or 0,
                                                               p.get("files_total") or 0))
        self.setToolTip("\n".join(x for x in tip if x))
        self.paint()

    def paint(self):
        self.setStyleSheet(f"QFrame#projCell {{ background: {theme.INFO_SOFT if self.on else self.bg}; border: none; }}"
                           f"QFrame#projCell:hover {{ background: {theme.INFO_SOFT if self.on else theme.SURFACE_2}; }}")

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self)

    def mouseDoubleClickEvent(self, e):
        self.opened.emit(self.p)


class ProjectsSection(Section):
    title = "Проекты"
    key = "projects"

    def __init__(self, shell, parent=None):
        super().__init__(shell, parent)
        self.projects = []
        self.heads = []
        self.selected = None
        self.col_page = 0
        self.sig = None
        S = _LIST_STATE
        bar = QHBoxLayout()
        bar.setSpacing(rem(0.5))
        self.search = SearchInput("Поиск по названию проекта…")
        self.search.textChanged.connect(lambda _t: self._search_timer.start(250))
        bar.addWidget(self.search, 3)
        self.sort = Select(SORTS)
        self.sort.set_value(S["prefs"].get("sort") or "deadline_desc")
        self.sort.currentIndexChanged.connect(lambda _i: self._pref("sort", self.sort.value()))
        bar.addWidget(self.sort, 0)
        self.org = PickerSelect([(None, "Все организации")], S["prefs"].get("organization"), "Все организации",
                                "Поиск организации…")
        self.org.setToolTip("Организация")
        self.org.changed.connect(lambda v: self._pref("organization", v))
        bar.addWidget(self.org, 1)
        self.head = PickerSelect([(None, "Все создатели")], S["prefs"].get("head_id"), "Все создатели",
                                 "Поиск сотрудника…")
        self.head.setToolTip("Создатель проекта")
        self.head.changed.connect(lambda v: self._pref("head_id", v))
        bar.addWidget(self.head, 1)
        self.root.addLayout(bar)
        self.table_card = QFrame()
        self.table_card.setObjectName("projTable")
        self.table_card.setStyleSheet(f"QFrame#projTable {{ background: {theme.CARD_BG}; border: {border()}px solid {theme.CARD_BORDER};"
                                      f" border-radius: {rem(0.75)}px; }}")
        self.table_lay = QVBoxLayout(self.table_card)
        self.table_lay.setContentsMargins(0, 0, 0, 0)
        self.table_lay.setSpacing(0)
        self.table_lay.addWidget(loading("Загружаем проекты…", big=True))
        self.root.addWidget(self.table_card, 1)
        acts = QHBoxLayout()
        acts.setSpacing(rem(0.5))
        self.arch = Segmented([("active", "Активные", "flag"), ("archived", "Архивные", "archive")],
                              "archived" if S["archived"] else "active")
        self.arch.changed.connect(self._switch_arch)
        acts.addWidget(self.arch)
        self.count = count_label("")
        acts.addWidget(self.count)
        acts.addStretch(1)
        self.edit_btn = self.del_btn = None
        if rights.can_create_project():
            self.edit_btn = Button("Редактировать", "ghost", "edit")
            self.edit_btn.clicked.connect(lambda: self.selected and self.shell.go("project_edit", project_id=self.selected["id"]))
            self.del_btn = Button("Удалить", "danger", "trash")
            self.del_btn.clicked.connect(self.delete_selected)
            new = Button("Создать проект", "primary", "plus")
            new.clicked.connect(lambda: self.shell.go("project_new"))
            for b in (self.edit_btn, self.del_btn, new):
                acts.addWidget(b)
        self.root.addLayout(acts)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self.reload)
        self._ws = QTimer(self)
        self._ws.setSingleShot(True)
        self._ws.timeout.connect(lambda: self.reload(quiet=True))
        self._update_buttons()
        self.reload()

    def _pref(self, key, value):
        _LIST_STATE["prefs"][key] = value
        jobs.run(store.set_pref, "projects.list", dict(_LIST_STATE["prefs"]), failed=lambda e: None)
        self.reload()

    def _switch_arch(self, key):
        _LIST_STATE["archived"] = key == "archived"
        self.selected = None
        self.reload()

    def reload(self, quiet=False):
        S = _LIST_STATE
        search = self.search.text().strip()

        def load():
            if not S["loaded"]:
                try:
                    saved = store.pref("projects.list")
                    if isinstance(saved, dict):
                        S["prefs"].update(saved)
                except Exception:
                    pass
                S["loaded"] = True
            p = S["prefs"]
            try:
                heads = client.get("projects/heads") or []
            except Exception:
                heads = []
            projects = client.get("projects", query={"sort": p.get("sort"), "organization": p.get("organization"),
                                                     "head_id": p.get("head_id"), "search": search or None,
                                                     "is_archived": str(bool(S["archived"])).lower()}) or []
            try:
                orgs = client.get("projects/organizations") or []
            except Exception:
                orgs = []
            return heads, projects, orgs

        def done(res):
            heads, projects, orgs = res
            import json
            sig = json.dumps(projects, sort_keys=True, default=str)
            if quiet and sig == self.sig:
                return
            self.sig = sig
            self.heads, self.projects = heads, projects
            p = S["prefs"]
            self.sort.blockSignals(True)
            self.sort.set_value(p.get("sort") or "deadline_desc")
            self.sort.blockSignals(False)
            self.org.set_options([(None, "Все организации")] + [(o, o) for o in orgs], p.get("organization"))
            creators = {}
            for pr in projects:
                cid = pr.get("creator_id")
                if cid is not None and cid not in creators:
                    creators[cid] = person_name(pr.get("creator")) or "Сотрудник №%s" % cid
            for h in heads:
                if h.get("id") in creators:
                    creators[h["id"]] = person_name(h)
            if p.get("head_id") is not None and p["head_id"] not in creators:
                kept = next((h for h in heads if h.get("id") == p["head_id"]), None)
                creators[p["head_id"]] = person_name(kept) if kept else "Выбранный сотрудник"
            opts = sorted(creators.items(), key=lambda x: str(x[1]).lower())
            self.head.set_options([(None, "Все создатели")] + opts, p.get("head_id"))
            self.count.setText("Проектов: %d" % len(projects))
            self.render_table()

        def failed(e):
            toast(self, "Проекты не загружены: " + human_error(e), "error")
            self.projects = []
            self.render_table()
        jobs.run(load, done=done, failed=failed, owner=self)

    def _owner_title(self, p):
        who = p.get("creator") or next((h for h in self.heads if h.get("id") == p.get("creator_id")), None)
        last = (who or {}).get("last_name") or ""
        first = (who or {}).get("first_name") or ""
        if last or first:
            return last + "\n" + first
        return "Сотрудник №%s" % p.get("creator_id") if p.get("creator_id") else "Без создателя"

    def columns(self):
        sel = _LIST_STATE["prefs"].get("head_id")
        by = {}
        for p in self.projects:
            owner = p.get("creator_id") if p.get("creator_id") is not None else 0
            if sel is not None and owner != sel:
                continue
            if owner not in by:
                by[owner] = {"owner": owner, "title": self._owner_title(p), "items": []}
            by[owner]["items"].append(p)
        return sorted(by.values(), key=lambda c: c["title"].lower())

    def render_table(self):
        clear_layout(self.table_lay)
        allc = self.columns()
        pages = max(1, (len(allc) + COLS_PER_PAGE - 1) // COLS_PER_PAGE)
        self.col_page = max(0, min(self.col_page, pages - 1))
        cols = allc[self.col_page * COLS_PER_PAGE:(self.col_page + 1) * COLS_PER_PAGE] if pages > 1 else allc
        self.selected = None
        self._cells = []
        if pages > 1:
            pager = QFrame()
            pager.setObjectName("colPager")
            pager.setStyleSheet(f"QFrame#colPager {{ border: none; border-bottom: {border()}px solid {theme.CARD_BORDER}; }}")
            pl = QHBoxLayout(pager)
            pl.setContentsMargins(rem(0.5), rem(0.4), rem(0.5), rem(0.4))
            prev = Button("", "ghost", "chevron_left", "Предыдущие руководители")
            prev.setEnabled(self.col_page > 0)
            prev.clicked.connect(lambda: self._page(self.col_page - 1))
            pl.addWidget(prev)
            frm = self.col_page * COLS_PER_PAGE + 1
            to = min(len(allc), (self.col_page + 1) * COLS_PER_PAGE)
            t = label("Руководители %d–%d из %d" % (frm, to, len(allc)), 0.78, theme.TEXT_LABEL, bold=True,
                      align=Qt.AlignCenter)
            pl.addWidget(t, 1)
            for i in range(pages):
                dot = QPushButton()
                dot.setCursor(Qt.PointingHandCursor)
                dot.setFixedSize(rem(1.75), rem(1.75))
                d = rem(0.45 * (1.5 if i == self.col_page else 1))
                dot.setToolTip("Показать руководителей %d–%d" % (i * COLS_PER_PAGE + 1, min(len(allc), (i + 1) * COLS_PER_PAGE)))
                dot.setStyleSheet(f"QPushButton {{ border: none; background: transparent; }}")
                dot.setIcon(icons.icon("star", d, theme.INFO if i == self.col_page else theme.CARD_BORDER, fill=True))
                dot.clicked.connect(lambda _=False, k=i: self._page(k))
                pl.addWidget(dot)
            nxt = Button("", "ghost", "chevron_right", "Следующие руководители")
            nxt.setEnabled(self.col_page < pages - 1)
            nxt.clicked.connect(lambda: self._page(self.col_page + 1))
            pl.addWidget(nxt)
            self.table_lay.addWidget(pager)
        if not cols:
            self.table_lay.addWidget(empty("Проектов пока нет"), 1)
            self._update_buttons()
            return
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")
        grid = QGridLayout(holder)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        b = border()
        head_style = (f"background: {theme.CHIP_BG}; color: {theme.BTN_GHOST_TEXT}; border: none;"
                      f" border-bottom: {b}px solid {theme.CARD_BORDER};")
        no = label("№", 0.78, theme.BTN_GHOST_TEXT, bold=True, align=Qt.AlignCenter)
        no.setStyleSheet(head_style)
        no.setFixedWidth(rem(3))
        no.setMinimumHeight(rem(3.1))
        grid.addWidget(no, 0, 0)
        for c, col in enumerate(cols):
            h = label(col["title"], 0.78, theme.BTN_GHOST_TEXT, bold=True, align=Qt.AlignCenter)
            h.setStyleSheet(head_style)
            h.setMinimumHeight(rem(3.1))
            grid.addWidget(h, 0, c + 1)
        rows = max([len(col["items"]) for col in cols] + [0])
        for r in range(rows):
            n = label(str(r + 1), 0.875, theme.TEXT_MUTED, align=Qt.AlignCenter)
            n.setStyleSheet(f"color: {theme.TEXT_MUTED}; border: none; border-bottom: {b}px solid {theme.CARD_BORDER};"
                            f" background: transparent;")
            n.setMinimumHeight(rem(3.1))
            grid.addWidget(n, r + 1, 0)
            for c, col in enumerate(cols):
                p = col["items"][r] if r < len(col["items"]) else None
                wrap = QFrame()
                wrap.setObjectName("projTd")
                wrap.setStyleSheet(f"QFrame#projTd {{ border: none; border-bottom: {b}px solid {theme.CARD_BORDER}; }}")
                wl = QVBoxLayout(wrap)
                wl.setContentsMargins(0, 0, 0, 0)
                if p is not None:
                    cell = _ProjectCell(p)
                    cell.clicked.connect(self._select)
                    cell.opened.connect(lambda pp: self.shell.go("project", project_id=pp["id"]))
                    wl.addWidget(cell)
                    self._cells.append(cell)
                grid.addWidget(wrap, r + 1, c + 1)
        for c in range(len(cols)):
            grid.setColumnStretch(c + 1, 1)
        grid.setRowStretch(rows + 1, 1)
        self.table_lay.addWidget(scroll_area(holder), 1)
        self._update_buttons()

    def _page(self, k):
        self.col_page = k
        self.render_table()

    def _select(self, cell):
        for c in self._cells:
            c.on = c is cell
            c.paint()
        self.selected = cell.p
        self._update_buttons()

    def _update_buttons(self):
        if self.edit_btn is not None:
            self.edit_btn.setEnabled(bool(self.selected and self.selected.get("can_edit")))
        if self.del_btn is not None:
            self.del_btn.setEnabled(bool(self.selected and self.selected.get("can_delete")))

    def delete_selected(self):
        p = self.selected
        if not p:
            return
        if not confirm(self, "Удаление проекта", "Удалить проект «%s»?\nФайлы разделов будут удалены, задачи останутся "
                                                 "без проекта." % p.get("name"), "Удалить", danger=True):
            return

        def ok(_):
            toast(self, "Проект удалён", "success")
            self.reload()
        jobs.run(client.delete, "projects/%s" % p["id"], done=ok,
                 failed=lambda e: toast(self, "Не удалось удалить: " + human_error(e), "error"), owner=self)

    def on_event(self, evt):
        kind = evt.get("type") or ""
        if (kind.startswith("project.") or kind.startswith("task.")) and "file" not in kind:
            self._ws.start(300)

    def on_reconnect(self):
        self._ws.start(400)


# ══════════════════════════════════════════════════════════════════════════
# Форма проекта
# ══════════════════════════════════════════════════════════════════════════

class ProjectEdit(Section):
    title = "Новый проект"
    key = "projects"

    def __init__(self, shell, project_id=None, parent=None):
        super().__init__(shell, parent)
        self.project_id = project_id
        self.title = "Редактирование проекта" if project_id else "Новый проект"
        self.root.addWidget(loading("Готовим форму…", big=True), 1)
        jobs.run(self._load, done=self._build, failed=lambda e: self._fail(e), owner=self)

    def _fail(self, e):
        clear_layout(self.root)
        self.root.addWidget(empty("Не удалось открыть форму: " + human_error(e)), 1)

    def _load(self):
        try:
            cands = client.get("projects/responsible-candidates") or []
        except Exception:
            cands = []
        try:
            orgs = client.get("organizations") or []
        except Exception:
            orgs = []
        cur = client.get("projects/%s" % self.project_id) if self.project_id else {}
        store.load_photo_ids()
        return cands, orgs, cur or {}

    def _back(self):
        if self.project_id:
            self.shell.replace("project", project_id=self.project_id)
        else:
            self.shell.replace("projects")

    def _build(self, res):
        cands, orgs, cur = res
        self.cands, self.cur = cands, cur
        clear_layout(self.root)
        card = Card(name="formCard", padding=(0.9, 1.0), spacing=0.8)
        self.name = line_edit("Название проекта", cur.get("name") or "")
        names = sorted([o.get("name") for o in orgs if o.get("name")], key=str.lower)
        opts = [(None, "— Не указана —")] + [(n, n) for n in names]
        value = cur.get("organization") if cur.get("organization") in names else None
        if cur.get("organization") and cur.get("organization") not in names:
            opts.insert(1, (cur["organization"], cur["organization"] + " (нет в справочнике)"))
            value = cur["organization"]
        self.org = PickerSelect(opts, value, "— Не указана —", "Поиск по названию организации…", "Организация не найдена")
        self.org_hint = label("", 0.75, theme.TEXT_MUTED)
        self.org.changed.connect(lambda _v: self._org_hint())
        self._org_hint()
        org_box = QWidget()
        ol = QVBoxLayout(org_box)
        ol.setContentsMargins(0, 0, 0, 0)
        ol.setSpacing(rem(0.3))
        ol.addWidget(self.org)
        ol.addWidget(self.org_hint)
        g1 = QGridLayout()
        g1.setHorizontalSpacing(rem(0.75))
        g1.addWidget(field("Название", self.name, True), 0, 0, Qt.AlignTop)     # .form-grid: align-items start
        g1.addWidget(field("Организация", org_box), 0, 1, Qt.AlignTop)
        g1.setColumnStretch(0, 1)
        g1.setColumnStretch(1, 1)
        card.body.addLayout(g1)
        self.resp_id = cur.get("responsible_id")
        self.resp_locked = False
        resp_box = QWidget()
        rl = QVBoxLayout(resp_box)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(rem(0.4))
        self.resp_row = QHBoxLayout()
        rl.addLayout(self.resp_row)
        self.resp_btn = Button("Выбрать", "ghost", "users")
        self.resp_btn.clicked.connect(self.pick_responsible)
        rl.addWidget(self.resp_btn, 0, Qt.AlignLeft)
        if store.is_department_head() and not store.is_management():
            self.resp_id = client.user_id
            self.resp_locked = True
            self.resp_btn.setEnabled(False)
            self.resp_btn.setToolTip("Старший по отделу назначается ответственным автоматически")
        self._draw_resp()
        dl = parse_date(cur.get("deadline"))
        start = QDate(dl.year, dl.month, dl.day) if dl else QDate.currentDate().addDays(30)
        self.deadline = DateField(start, None if cur.get("id") else QDate.currentDate())
        g2 = QGridLayout()
        g2.setHorizontalSpacing(rem(0.75))
        g2.addWidget(field("Ответственный", resp_box), 0, 0, Qt.AlignTop)
        g2.addWidget(field("Дата окончания", self.deadline, True), 0, 1, Qt.AlignTop)
        g2.setColumnStretch(0, 1)
        g2.setColumnStretch(1, 1)
        card.body.addLayout(g2)
        self.description = QPlainTextEdit(cur.get("description") or "")
        self.description.setPlaceholderText("Кратко о проекте (необязательно)")
        self.description.setFont(font(0.875))
        self.description.setStyleSheet(text_qss())
        self.description.setFixedHeight(rem(5.5))      # .textarea: min-height 5.5rem
        card.body.addWidget(field("Описание", self.description))
        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = Button("Отмена", "ghost")
        cancel.clicked.connect(self._back)
        self.save_btn = Button("Сохранить", "primary", "save")
        self.save_btn.clicked.connect(self.save)
        btns.addWidget(cancel)
        btns.addWidget(self.save_btn)
        card.body.addLayout(btns)
        self.root.addWidget(card)
        self.root.addStretch(1)

    def _org_hint(self):
        self.org_hint.setText("Организация из справочника" if self.org.value() else "Можно оставить пустым")

    def _draw_resp(self):
        clear_layout(self.resp_row)
        p = next((c for c in self.cands if c.get("id") == self.resp_id), None)
        if not p:
            self.resp_row.addWidget(muted("Не назначен", 0.875))
        else:
            chip = PersonChip(p, removable=not self.resp_locked)
            chip.setToolTip(" · ".join(x for x in (person_name(p), p.get("position"), p.get("department")) if x))
            chip.removed.connect(lambda _p: self._set_resp(None))
            self.resp_row.addWidget(chip)
        self.resp_row.addStretch(1)

    def _set_resp(self, v):
        self.resp_id = v
        self._draw_resp()

    def pick_responsible(self):
        chosen = {"v": self.resp_id}
        m = Modal(self, "Ответственный за проект", "users", width_rem=41.4)
        m.body.addWidget(label("Ответственным может быть руководитель отдела или директор", 0.75, theme.TEXT_MUTED))
        bar = QHBoxLayout()
        search = SearchInput("Поиск по ФИО, отделу, должности…")
        bar.addWidget(search, 1)
        deps = sorted(set(c.get("department") for c in self.cands if c.get("department")))
        dep = Select([(None, "Все отделы")] + [(d, d) for d in deps], min_width_rem=12)
        dep.setToolTip("Показать только один отдел")
        bar.addWidget(dep)
        m.body.addLayout(bar)
        holder = QFrame()
        holder.setObjectName("checklist")
        holder.setStyleSheet(f"QFrame#checklist {{ border: {border()}px solid {theme.CARD_BORDER}; border-radius: {rem(0.5)}px;"
                             f" background: {theme.CARD_BG}; }}")
        lst = QVBoxLayout(holder)
        lst.setContentsMargins(rem(0.4), rem(0.4), rem(0.4), rem(0.4))
        area = scroll_area(holder)
        area.setMinimumHeight(rem(12))
        area.setMaximumHeight(rem(23))
        m.body.addWidget(area)

        def render():
            clear_layout(lst)
            needle = search.text().strip().lower()
            d = dep.value()
            rows = [c for c in self.cands if (not d or c.get("department") == d) and (not needle or needle in (
                person_name(c) + " " + (c.get("department") or "") + " " + (c.get("position") or "")).lower())]
            for c in rows:
                row = QHBoxLayout()
                rb = QRadioButton()
                rb.setStyleSheet(checkbox_qss().replace("QCheckBox", "QRadioButton"))
                rb.setAutoExclusive(False)
                rb.setChecked(chosen["v"] == c.get("id"))

                def pick(on, cid=c.get("id")):
                    if on:
                        chosen["v"] = cid
                        render()
                rb.toggled.connect(pick)
                row.addWidget(rb)
                row.addWidget(avatar(c, 1.7))
                lab = QLabel("%s <span style='color:%s'> — %s</span>" % (
                    person_name(c), theme.TEXT_MUTED, ", ".join(x for x in (c.get("position"), c.get("department")) if x)))
                lab.setFont(font(0.875))
                row.addWidget(lab, 1)
                w = QWidget()
                w.setLayout(row)
                row.setContentsMargins(rem(0.4), rem(0.2), rem(0.4), rem(0.2))
                lst.addWidget(w)
            if not rows:
                lst.addWidget(muted("Никого не найдено", 0.85))
            lst.addStretch(1)
        search.textChanged.connect(lambda _t: render())
        dep.currentIndexChanged.connect(lambda _i: render())
        render()
        m.add_button("Снять", "ghost", "close", lambda _m: (self._set_resp(None), True)[1])
        m.add_button("Готово", "primary", "check", lambda _m: (self._set_resp(chosen["v"]), True)[1])
        m.exec()

    def save(self):
        title = self.name.text().strip()
        if not title:
            toast(self, "Укажите название проекта", "warning")
            return
        d = self.deadline.value()
        if d is None:
            toast(self, "Укажите дату окончания — без неё проект сохранить нельзя", "warning")
            return
        if not self.cur.get("id") and d < QDate.currentDate():
            toast(self, "Дата окончания не может быть раньше сегодняшнего дня", "warning")
            return
        payload = {"name": title, "organization": self.org.value() or None,
                   "responsible_id": client.user_id if self.resp_locked else self.resp_id,
                   "deadline": self.deadline.iso(), "description": self.description.toPlainText().strip() or None}
        pid = self.project_id

        def work():
            if pid:
                return client.put("projects/%s" % pid, json_body=payload)
            return client.post("projects", json_body=payload)

        def done(_saved):
            toast(self, "Проект сохранён" if pid else "Проект создан", "success")
            if pid:
                self.shell.replace("project", project_id=pid)
            else:
                self.shell.replace("projects")

        def failed(e):
            self.save_btn.setEnabled(True)
            toast(self, "Не удалось сохранить: " + human_error(e), "error")
        self.save_btn.setEnabled(False)
        jobs.run(work, done=done, failed=failed, owner=self)


# ══════════════════════════════════════════════════════════════════════════
# Окно проекта
# ══════════════════════════════════════════════════════════════════════════

class _SectionCard(QFrame):
    """Карточка раздела проекта (.section-card): рамка, кружок со значком, подпись, счётчик файлов."""
    clicked = Signal()

    def __init__(self, glyph, title, sub, wide=False, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("secCard")
        self.setAttribute(Qt.WA_Hover, True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(title)
        b = border()
        self.setStyleSheet(f"QFrame#secCard {{ background: {theme.CARD_BG}; border: {b}px solid {theme.CARD_BORDER};"
                           f" border-radius: {rem(0.9)}px; }}"
                           f"QFrame#secCard:hover, QFrame#secCard:focus {{ border-color: {theme.ACCENT_SOFT};"
                           f" background: {theme.SURFACE_2}; }}")
        d = rem(2.8 if wide else 3.2)
        circ = QLabel()
        circ.setFixedSize(d, d)
        circ.setAlignment(Qt.AlignCenter)
        circ.setStyleSheet(f"background: {theme.INFO_SOFT}; border-radius: {d // 2}px; border: none;")
        circ.setPixmap(icons.pixmap(glyph, rem(1.5), theme.ACCENT))
        t = label(title, 1.05, theme.TEXT_VALUE, bold=True)
        s = label(sub, 0.78, theme.TEXT_LABEL, wrap=True)
        self.cnt = Chip("", "default")
        self.cnt.hide()
        self.go_icon = None
        if wide:
            lay = QHBoxLayout(self)
            lay.setContentsMargins(rem(1.0), rem(0.9), rem(1.0), rem(0.9))
            lay.setSpacing(rem(0.9))
            lay.addWidget(circ)
            body = QVBoxLayout()
            body.setSpacing(rem(0.15))
            body.addWidget(t)
            body.addWidget(s)
            lay.addLayout(body, 1)
            self.go_icon = QLabel()
            self.go_icon.setPixmap(icons.pixmap("chevron_right", rem(1.2), theme.TEXT_MUTED))
            lay.addWidget(self.go_icon)
        else:
            lay = QVBoxLayout(self)
            lay.setContentsMargins(rem(1.0), rem(1.0), rem(1.0), rem(1.0))
            lay.setSpacing(rem(0.5))
            lay.addWidget(circ)
            lay.addWidget(t)
            lay.addWidget(s)
            lay.addStretch(1)
            lay.addWidget(self.cnt, 0, Qt.AlignLeft)
            self.setMinimumHeight(rem(9))
        for w in self.findChildren(QLabel):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
            if w is not circ and w is not self.cnt:
                w.setStyleSheet((w.styleSheet() or "") + "background: transparent; border: none;")

    def enterEvent(self, e):
        if self.go_icon is not None:
            self.go_icon.setPixmap(icons.pixmap("chevron_right", rem(1.2), theme.ACCENT))
        super().enterEvent(e)

    def leaveEvent(self, e):
        if self.go_icon is not None:
            self.go_icon.setPixmap(icons.pixmap("chevron_right", rem(1.2), theme.TEXT_MUTED))
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(e)

    def set_count(self, n):
        self.cnt.setText("Файлов: %d" % n)
        self.cnt.show()


class ProjectView(Section):
    title = "Проект"
    key = "projects"

    def __init__(self, shell, project_id, section=None, parent=None):
        super().__init__(shell, parent)
        self.project_id = int(project_id)
        self.project = None
        self.tasks = []
        self.selected_task = None
        self.section_key = None
        self.reload_section = None
        self.leave_section = None
        self.wh = None
        self.initial_section = section
        self.tasks_sig = None
        self._ws = QTimer(self)
        self._ws.setSingleShot(True)
        self._ws.timeout.connect(self._ws_reload)
        self._ws_files = False
        self._tasks_timer = QTimer(self)
        self._tasks_timer.setSingleShot(True)
        self._tasks_timer.timeout.connect(self.reload_tasks)
        self.root.addWidget(loading("Открываем проект…", big=True), 1)
        self.open()

    def open(self, section=None):
        def load():
            p = client.get("projects/%d" % self.project_id)
            if not _TASK_PREFS["loaded"]:
                try:
                    saved = store.pref("projects.tasks")
                    if isinstance(saved, dict):
                        _TASK_PREFS["prefs"].update(saved)
                except Exception:
                    pass
                _TASK_PREFS["loaded"] = True
            return p

        def done(p):
            self.project = p
            self.render()
            self.reload_tasks()
            key = section or self.initial_section
            self.initial_section = None
            if key and (key == WAREHOUSE[0] or any(s[0] == key for s in SECTIONS)):
                self.open_section(key)

        def failed(e):
            clear_layout(self.root)
            self.root.addWidget(empty("Проект не открыт: " + human_error(e)), 1)
            b = Button("К проектам", "ghost", "arrow_back")
            b.clicked.connect(lambda: self.shell.go("projects"))
            self.root.addWidget(b, 0, Qt.AlignHCenter)
        jobs.run(load, done=done, failed=failed, owner=self)

    # ── отрисовка ────────────────────────────────────────────────────────────

    def render(self):
        p = self.project
        self._leave()
        clear_layout(self.root)
        self.title = p.get("name") or "Проект"
        self.shell.set_title(self.title)
        rights.set_read_only(bool(p.get("is_archived")),
                             "Проект завершён — изменения недоступны. Вернуть его в работу может создатель, директор или "
                             "администратор.")
        self.head = QWidget()
        hl = QHBoxLayout(self.head)
        hl.setContentsMargins(0, 0, 0, rem(0.8))       # .proj-head: margin-bottom .8rem
        hl.setSpacing(rem(0.75))
        back = Button("К проектам", "ghost", "arrow_back")
        back.clicked.connect(lambda: self.shell.go("projects"))
        hl.addWidget(back)
        t = label(p.get("name") or "Проект", 1.3, theme.TEXT_VALUE, bold=True)
        hl.addWidget(t, 1)
        done = self._complete_control(p)
        if done is not None:
            hl.addWidget(done)
        self.root.addWidget(self.head)

        self.layout_box = QHBoxLayout()
        self.layout_box.setSpacing(rem(0.75))
        # левая колонка — задачи проекта
        self.left = InfoCard("Задачи проекта", "list")
        prefs = _TASK_PREFS["prefs"]
        self.task_search = SearchInput("Поиск по названию…")
        self.task_search.textChanged.connect(lambda _t: self._tasks_timer.start(250))
        self.left.body.addWidget(self.task_search)
        frow = self.t_filters = Flow(spacing_rem=0.5)
        self.t_sort = Select(TASK_SORTS)
        self.t_sort.set_value(prefs.get("sort") or "deadline_asc")
        self.t_sort.currentIndexChanged.connect(lambda _i: self._tpref("sort", self.t_sort.value()))
        self.t_dep = Select([(None, "Все отделы")])
        self.t_dep.currentIndexChanged.connect(lambda _i: self._tpref("department", self.t_dep.value()))
        self.t_stages = Select([(None, "Этапов: любое")] + [(i, "Этапов: %d" % i) for i in range(1, 11)])
        self.t_stages.set_value(prefs.get("stage_count"))
        self.t_stages.currentIndexChanged.connect(lambda _i: self._tpref("stage_count", self.t_stages.value()))
        for w in (self.t_sort, self.t_dep, self.t_stages):
            frow.add(w)
        self.left.body.addWidget(frow)
        self.t_seg = Segmented([("all", "Все", "list"), ("active", "Активные", "flag"), ("archived", "Архивные", "archive")],
                               prefs.get("state") or "all")
        self.t_seg.changed.connect(lambda k: self._tpref("state", k))
        self.left.body.addWidget(self.t_seg)
        self.t_table = DataTable(["Название", "Дата окончания"], stretch=[0], left_columns=(0,), text_rows=True)
        self.t_table.itemSelectionChanged.connect(self._task_selected)
        self.t_table.row_activated.connect(self._open_task_row)
        self.left.body.addWidget(self.t_table, 1)
        self.t_summary = label("", 0.75, theme.TEXT_LABEL)
        self.left.body.addWidget(self.t_summary)
        self.left.body.addLayout(self._task_buttons(p))
        self.layout_box.addWidget(self.left, 2)
        # правая колонка — сведения и разделы
        self.right_w = QWidget()
        self.right_w.setStyleSheet("background: transparent;")
        self.right = QVBoxLayout(self.right_w)
        self.right.setContentsMargins(0, 0, rem(0.2), 0)
        self.right.setSpacing(rem(0.75))
        self.right_area = scroll_area(self.right_w)
        self.layout_box.addWidget(self.right_area, 3)
        self.root.addLayout(self.layout_box, 1)
        self.render_cards()

    def _complete_control(self, p):
        if not p.get("can_complete"):
            return None
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(rem(0.6))
        if p.get("is_archived"):
            by = " · ".join(x for x in (fmt_date(p.get("archived_at"), "") if p.get("archived_at") else "",
                                        person_name(p.get("archived_by"))) if x)
            lay.addWidget(Chip("Завершён" + (" " + by if by else "")))
            b = Button("Вернуть в работу", "ghost", "refresh")
            b.clicked.connect(lambda: self.set_archived(False))
            lay.addWidget(b)
            return w
        left = int(p.get("tasks_unfinished") if p.get("tasks_unfinished") is not None else (p.get("tasks_active") or 0))
        if left:
            hint = label("Завершить нельзя — незавершённых задач: %d" % left, 0.8, theme.TEXT_MUTED)
            lay.addWidget(hint)
        b = Button("Завершить проект", "primary", "check")
        b.setEnabled(left == 0)
        if left:
            b.setToolTip("Завершить нельзя — незавершённых задач: %d" % left)
        b.clicked.connect(lambda: self.set_archived(True))
        lay.addWidget(b)
        return w

    def set_archived(self, done):
        p = self.project
        if done and not confirm(self, "Завершение проекта", "Завершить проект «%s»? Он перейдёт в «Архивные»." %
                                (p.get("name") or ""), "Завершить", glyph="check"):
            return

        def ok(_):
            toast(self, "Проект завершён и перенесён в «Архивные»" if done else "Проект возвращён в работу", "success")
            self.open()
        jobs.run(client.post, "projects/%d/%s" % (self.project_id, "complete" if done else "reopen"), done=ok,
                 failed=lambda e: toast(self, ("Не удалось завершить проект: " if done else "Не удалось вернуть проект: ")
                                        + human_error(e), "error"), owner=self)

    def _info_card(self, p):
        st = deadline_state(p.get("deadline"))
        card = QFrame()
        card.setObjectName("projInfo")
        b = border()
        card.setStyleSheet(f"QFrame#projInfo {{ background: {theme.CARD_BG}; border: {b}px solid {theme.CARD_BORDER};"
                           f" border-radius: {rem(1.0)}px; }}")
        grid = QGridLayout(card)
        grid.setContentsMargins(rem(0.35), rem(0.25), rem(0.35), rem(0.25))
        grid.setHorizontalSpacing(0)
        grid.setVerticalSpacing(0)

        def cell(glyph, title, value_widget, first=False):
            w = QFrame()
            w.setObjectName("projCellI")
            w.setStyleSheet(f"QFrame#projCellI {{ border: none; {'' if first else f'border-left: {b}px solid {theme.CARD_BORDER};'} }}")
            l = QHBoxLayout(w)
            l.setContentsMargins(rem(0.8), rem(0.6), rem(0.8), rem(0.6))
            l.setSpacing(rem(0.7))
            ico = QLabel()
            ico.setFixedSize(rem(2), rem(2))
            ico.setAlignment(Qt.AlignCenter)
            ico.setStyleSheet(f"background: {theme.INFO_SOFT}; border-radius: {rem(0.6)}px; border: none;")
            ico.setPixmap(icons.pixmap(glyph, rem(1.05), theme.ACCENT))
            l.addWidget(ico, 0, Qt.AlignVCenter)
            tx = QVBoxLayout()
            tx.setSpacing(rem(0.1))
            lb = label(title.upper(), 0.7, theme.TEXT_LABEL)
            lb.setFont(font(0.7, spacing=0.04))
            tx.addWidget(lb)
            tx.addWidget(value_widget)
            l.addLayout(tx, 1)
            return w
        cells = []
        if p.get("organization"):
            cells.append(("building", "Организация", label(p["organization"], 0.92, theme.TEXT_VALUE, wrap=True)))
        if p.get("responsible"):
            cells.append(("user", "Ответственный", label(person_name(p["responsible"]), 0.92, theme.TEXT_VALUE)))
        if p.get("deadline"):
            txt = "до " + fmt_date(p["deadline"]) + {"overdue": " · просрочен", "urgent": " · меньше суток"}.get(st, "")
            bg, fg = {"overdue": (theme.CRITICAL_SOFT, theme.CRITICAL), "urgent": (theme.WARNING_SOFT, theme.WARNING_TEXT)}.get(
                st, (theme.INFO_SOFT, theme.INFO))
            due = QLabel(txt)
            due.setFont(font(0.85, bold=True))
            due.setStyleSheet(f"background: {bg}; color: {fg}; border-radius: {rem(0.6)}px; padding: {rem(0.1)}px {rem(0.55)}px;")
            due.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            cells.append(("calendar", "Срок", due))
        else:
            cells.append(("calendar", "Срок", label("не задан", 0.92, theme.TEXT_MUTED)))
        for i, (g, t, v) in enumerate(cells):
            grid.addWidget(cell(g, t, v, first=(i == 0)), 0, i)
        if p.get("description"):
            d = cell("note", "Описание", label(p["description"], 0.88, theme.TEXT_LABEL, wrap=True), first=True)
            d.setStyleSheet(f"QFrame#projCellI {{ border: none; border-top: {b}px solid {theme.CARD_BORDER}; }}")
            grid.addWidget(d, 1, 0, 1, max(1, len(cells)))
        for i in range(len(cells)):
            grid.setColumnStretch(i, 1)
        return card

    def render_cards(self):
        self._leave()
        self.section_key = None
        self.left.show()
        clear_layout(self.right)
        p = self.project
        self.right.addWidget(self._info_card(p))
        self.right.addSpacing(14)
        grid = QGridLayout()
        grid.setSpacing(rem(0.75))
        self.cards = {}
        for i, (key, title, glyph, sub) in enumerate(SECTIONS):
            c = _SectionCard(glyph, title, sub)
            c.clicked.connect(lambda _=False, k=key: self.open_section(k))
            grid.addWidget(c, i // 2, i % 2)
            self.cards[key] = c
        self.right.addLayout(grid)
        wide = _SectionCard(WAREHOUSE[2], WAREHOUSE[1], WAREHOUSE[3], wide=True)
        wide.clicked.connect(lambda: self.open_section("warehouse"))
        self.right.addWidget(wide)
        self.right.addStretch(1)

        def counts():
            out = {}
            for key, *_ in SECTIONS:
                try:
                    if key == "photos":
                        lst = client.get("projects/%d/photos" % self.project_id) or []
                        total = sum(len(f.get("files") or []) for f in lst)
                        try:
                            extra = client.get("projects/%d/files" % self.project_id, query={"section": "extra"}) or []
                        except Exception:
                            extra = []
                        out[key] = total + len(extra)
                    else:
                        out[key] = len(client.get("projects/%d/files" % self.project_id, query={"section": key}) or [])
                except Exception:
                    pass
            return out

        def done(out):
            for k, n in out.items():
                c = self.cards.get(k)
                if c is not None:
                    try:
                        c.set_count(n)
                    except RuntimeError:
                        pass
        jobs.run(counts, done=done, failed=lambda e: None, owner=self)

    def _leave(self):
        if self.leave_section:
            try:
                self.leave_section()
            except Exception:
                pass
            self.leave_section = None
        self.reload_section = None
        self.wh = None

    def _section_head(self, title, glyph):
        row = QHBoxLayout()
        row.setSpacing(rem(0.75))
        back = Button("Разделы", "ghost", "arrow_back")
        back.clicked.connect(self.render_cards)
        row.addWidget(back)
        row.addWidget(SectionHeader(title, glyph), 1)
        return row

    def open_section(self, key):
        self._leave()
        self.section_key = key
        clear_layout(self.right)
        meta = WAREHOUSE if key == "warehouse" else next(s for s in SECTIONS if s[0] == key)
        head = self._section_head(meta[1], meta[2])
        if key == "warehouse":
            from kov.warehouse import WarehouseBlock
            self.left.hide()
            self.head.hide()
            self.right.addLayout(head)
            card = Card(name="whCard", padding=(0.6, 0.6))
            self.wh = WarehouseBlock(self.project_id)
            card.body.addWidget(self.wh, 1)
            self.right.addWidget(card, 1)
            self.reload_section = lambda soft=False: self.wh.load(soft)
            self.leave_section = lambda: (self.left.show(), self.head.show())
            return
        if key == "photos":
            self.right.addLayout(head)
            media = MediaContent(self)
            self.right.addWidget(media, 1)
            self.reload_section = media.load
            return
        state = {"files": [], "earliest": True}
        sort_btn = Button("Сначала ранние", "ghost", "clock", "Порядок по дате прикрепления")
        head.addWidget(sort_btn)
        self.right.addLayout(head)
        can_upload = self.project.get("can_upload") is not False and not rights.read_only()
        block = kfiles.CategoryBlock(lambda: state["files"], can_upload=can_upload, can_delete=lambda f: can_upload,
                                     upload_path="projects/%d/files" % self.project_id, upload_fields={"section": key},
                                     on_changed=lambda: load(), sort_earliest_first=True, no_tabs=True,
                                     on_uploaded=lambda created: state.__setitem__(
                                         "files", [dict(f, file_kind="project") for f in created] + state["files"]))

        def flip():
            state["earliest"] = not state["earliest"]
            sort_btn.setText("Сначала ранние" if state["earliest"] else "Сначала новые")
            block.sort_earliest_first = state["earliest"]
            block.render()
        sort_btn.clicked.connect(flip)
        card = Card(name="secFiles", padding=(0.6, 0.6))
        card.body.addWidget(block, 1)
        self.right.addWidget(card, 1)

        def load(soft=False):
            def got(files):
                state["files"] = [dict(f, file_kind="project") for f in (files or [])]
                try:
                    block.render()
                except RuntimeError:
                    pass

            def bad(e):
                if not soft:
                    toast(self, "Файлы не загружены: " + human_error(e), "error")
                state["files"] = []
                try:
                    block.render()
                except RuntimeError:
                    pass
            jobs.run(client.get, "projects/%d/files" % self.project_id, query={"section": key}, done=got, failed=bad,
                     owner=self)
        self.reload_section = load
        load()

    # ── задачи проекта ───────────────────────────────────────────────────────

    def _task_buttons(self, p):
        row = QHBoxLayout()
        row.setSpacing(rem(0.5))
        self.t_edit = self.t_del = None
        if p.get("is_archived"):
            row.addWidget(label("Задачи, файлы и склад завершённого проекта — только для просмотра", 0.75, theme.TEXT_MUTED))
            return row
        if not store.can_manage_work():
            row.addWidget(label("Изменение задач доступно руководителям", 0.75, theme.TEXT_MUTED))
            return row
        row.addStretch(1)
        self.t_edit = Button("Редактировать", "ghost", "edit")
        self.t_edit.clicked.connect(lambda: self.selected_task and self.shell.go("task_edit", task_id=self.selected_task["id"]))
        self.t_del = Button("Удалить", "danger", "trash")
        self.t_del.clicked.connect(self._delete_task)
        create = Button("Новая задача проекта", "primary", "plus")
        create.clicked.connect(lambda: self.shell.go("task_new", project_id=self.project_id))
        for b in (self.t_edit, self.t_del, create):
            row.addWidget(b)
        self._update_task_buttons()
        return row

    def _update_task_buttons(self):
        t = self.selected_task
        locked = t is not None and not rights.can_manage_task(t)
        if self.t_edit is not None:
            self.t_edit.setEnabled(bool(t and t.get("activity") == "Активная"))
            self.t_edit.set_locked(locked and self.t_edit.isEnabled(), TASK_LOCKED)
        if self.t_del is not None:
            self.t_del.setEnabled(bool(t))
            self.t_del.set_locked(locked, TASK_LOCKED)

    def _delete_task(self):
        t = self.selected_task
        if not t:
            return
        if not confirm(self, "Удаление задачи", "Удалить задачу «%s» без возможности восстановления?" % t.get("name"),
                       "Удалить", danger=True):
            return

        def ok(_):
            toast(self, "Задача удалена", "success")
            self.selected_task = None
            self.reload_tasks()
        jobs.run(client.delete, "tasks/%s" % t["id"], done=ok,
                 failed=lambda e: toast(self, "Не удалось удалить: " + human_error(e), "error"), owner=self)

    def _tpref(self, key, value):
        _TASK_PREFS["prefs"][key] = value
        jobs.run(store.set_pref, "projects.tasks", dict(_TASK_PREFS["prefs"]), failed=lambda e: None)
        self.reload_tasks()

    def reload_tasks(self, quiet=False):
        if self.project is None:
            return
        prefs = _TASK_PREFS["prefs"]
        q = {"state": prefs.get("state") or "all", "search": self.task_search.text().strip() or None,
             "sort": prefs.get("sort"), "department": prefs.get("department"), "stage_count": prefs.get("stage_count")}

        def done(tasks):
            import json
            sig = json.dumps(tasks, sort_keys=True, default=str)
            if quiet and sig == self.tasks_sig:
                return
            self.tasks_sig = sig
            self.tasks = tasks or []
            deps = sorted(set(d for t in self.tasks for d in (t.get("departments") or []) if d))
            self.t_dep.blockSignals(True)
            self.t_dep.clear()
            self.t_dep.addItem("Все отделы", None)
            for d in deps:
                self.t_dep.addItem(d, d)
            self.t_dep.set_value(prefs.get("department"))
            self.t_dep.blockSignals(False)
            self.t_filters.relayout()
            self.draw_tasks()
        jobs.run(client.get, "projects/%d/tasks" % self.project_id, query=q, done=done,
                 failed=lambda e: done([]), owner=self)

    def draw_tasks(self):
        t = self.t_table
        lst = self.tasks
        t.blockSignals(True)
        t.setRowCount(0)
        t.setRowCount(len(lst) or 1)
        if not lst:
            t.setItem(0, 0, t.cell("Задач нет", 0, color=theme.TEXT_MUTED))
            t.setItem(0, 1, t.cell("", 1))
        for r, task in enumerate(lst):
            active = task.get("activity") == "Активная"
            st = deadline_state(task.get("deadline"), active)
            bg = theme.CRITICAL_SOFT if st == "overdue" else (theme.WARNING_SOFT if st == "urgent" else None)
            name = t.cell(task.get("name") or "", 0, color=None if active else theme.TEXT_LABEL, bg=bg, data=task.get("id"))
            name.setToolTip("Нет доступа к задаче" if task.get("can_open") is False else
                            "Этапов: %s из %s" % (task.get("completed_stages") or 0, task.get("total_stages") or 0) +
                            ("" if active else " · архивная"))
            if task.get("can_open") is False:
                name.setForeground(QColor(theme.TEXT_MUTED))
            t.setItem(r, 0, name)
            color = theme.CRITICAL if st == "overdue" else (theme.WARNING_TEXT if st == "urgent" else None)
            t.setItem(r, 1, t.cell(fmt_date(task.get("deadline")) if task.get("deadline") else "—", 1, bold=bool(st),
                                   color=color, bg=bg))
        t.fit_columns()
        t.blockSignals(False)
        self.selected_task = None
        self._update_task_buttons()
        active = sum(1 for x in lst if x.get("activity") == "Активная")
        self.t_summary.setText("Задач: %d · активных: %d" % (len(lst), active))

    def _task_selected(self):
        row = self.t_table.currentRow()
        self.selected_task = self.tasks[row] if 0 <= row < len(self.tasks) and self.t_table.selectionModel().hasSelection() else None
        self._update_task_buttons()

    def _open_task_row(self, row):
        if not (0 <= row < len(self.tasks)):
            return
        t = self.tasks[row]
        if t.get("can_open") is False:
            toast(self, "Нет доступа к этой задаче: её этапы и файлы открыты только участникам", "warning")
            return
        self.shell.go("task", task_id=t["id"], back=("project", {"project_id": self.project_id}))

    # ── события ──────────────────────────────────────────────────────────────

    def _ws_reload(self):
        if not self._ws_files:
            self.reload_tasks(quiet=True)
        if self.section_key == "photos" and self.reload_section:
            self.reload_section(True)
        self._ws_files = False

    def on_event(self, evt):
        kind = evt.get("type") or ""
        if self.project is None:
            return
        pid = evt.get("project_id")
        same = pid is not None and str(pid) == str(self.project_id)
        if kind == "project.deleted" and same:
            toast(self, "Проект удалён другим пользователем", "warning")
            self.shell.go("projects")
            return
        if kind == "project.updated" and same:
            self.open(section=self.section_key)
            return
        if kind in ("project.file_added", "project.file_deleted") and same:
            if self.section_key and self.reload_section:
                self.reload_section(True)
            elif not self.section_key:
                self.render_cards()
            return
        if kind.startswith("warehouse.") and self.wh is not None:
            self.wh.on_event(evt)
            return
        if kind.startswith("task.") or kind.startswith("stage."):
            self._ws_files = "file" in kind
            self._ws.start(300)

    def on_reconnect(self):
        if self.section_key and self.reload_section:
            self.reload_section(True)

    def shutdown(self):
        self._leave()
        rights.set_read_only(False)


# ══════════════════════════════════════════════════════════════════════════
# Медиаконтент: папки задач и «Дополнительные файлы»
# ══════════════════════════════════════════════════════════════════════════

class _Folder(QFrame):
    """Раскрывающаяся папка: шапка со стрелкой, тело собирается при первом раскрытии."""

    def __init__(self, title, extra_widgets=(), accent=False, sub=False, parent=None, build=None):
        super().__init__(parent)
        self.build = build
        self.built = False
        self.setObjectName("subFolder" if sub else "folderCard")
        b = border()
        if sub:
            self.setStyleSheet(f"QFrame#subFolder {{ border: {b}px solid {theme.CARD_BORDER}; border-radius: {rem(0.5)}px;"
                               f" background: {theme.CARD_BG}; }}")
        else:
            self.setStyleSheet(f"QFrame#folderCard {{ background: {theme.CARD_BG}; border: {b}px solid {theme.CARD_BORDER};"
                               f" border-radius: {rem(0.75)}px; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.head = QPushButton()
        self.head.setCursor(Qt.PointingHandCursor)
        self.head.setStyleSheet(f"QPushButton {{ border: none; text-align: left; border-radius: {rem(0.5 if sub else 0.75)}px;"
                                f" background: {theme.BTN_GHOST_BG if sub else 'transparent'}; }}"
                                f"QPushButton:hover {{ background: {theme.BTN_GHOST_HOVER if sub else theme.CHIP_BG}; }}")
        hl = QHBoxLayout(self.head)
        hl.setContentsMargins(rem(0.7 if sub else 0.9), rem(0.5 if sub else 0.75), rem(0.7 if sub else 0.9),
                              rem(0.5 if sub else 0.75))
        hl.setSpacing(rem(0.5 if sub else 0.6))
        self.arrow = QLabel()
        self.arrow.setPixmap(icons.pixmap("chevron_down", rem(1.0), theme.TEXT_LABEL))
        hl.addWidget(self.arrow)
        fold = QLabel()
        fold.setPixmap(icons.pixmap("folder", rem(1.05 if sub else 1.15), theme.ACCENT))
        hl.addWidget(fold)
        self.title = label(title, 0.85 if sub else 0.875, theme.ACCENT if accent else theme.TEXT_VALUE, bold=True)
        hl.addWidget(self.title, 1)
        for w in extra_widgets:
            hl.addWidget(w)
        for w in self.head.findChildren(QLabel):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.head.setMinimumHeight(rem(2.4 if sub else 2.9))
        lay.addWidget(self.head)
        self.body = QWidget()
        self.body.setStyleSheet("background: transparent;")
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(rem(0.6 if sub else 0.9), rem(0.2 if not sub else 0.6), rem(0.6 if sub else 0.9),
                                         rem(0.8 if not sub else 0.6))
        self.body_lay.setSpacing(rem(0.4))
        self.body.hide()
        lay.addWidget(self.body)
        self.head.clicked.connect(lambda: self.toggle())

    def is_open(self):
        return self.body.isVisible()

    def toggle(self, force=None):
        show = (not self.body.isVisible()) if force is None else force
        self.body.setVisible(show)
        from PySide6.QtGui import QTransform
        pm = icons.pixmap("chevron_down", rem(1.0), theme.TEXT_LABEL)
        if show:
            pm = pm.transformed(QTransform().rotate(180))
        self.arrow.setPixmap(pm)
        if show and not self.built and self.build:
            self.built = True
            self.build(self)
        return show


class MediaContent(QWidget):
    """Раздел «Медиаконтент»: фото и видео задач проекта по папкам."""

    def __init__(self, view):
        super().__init__()
        self.view = view
        self.pid = view.project_id
        self.folders = []
        self.extra = []
        self.uploader = None
        self.opened = set()
        self.stage_names = {}
        self.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(rem(0.75))
        bar = QHBoxLayout()
        bar.setSpacing(rem(0.5))
        self.search = SearchInput("Поиск по названию задачи…")
        self.search.textChanged.connect(lambda _t: self.draw())
        bar.addWidget(self.search, 2)
        self.filter = PickerSelect([(None, "Все отправители")], None, "Все отправители", "Поиск по ФИО…", "Никого не найдено")
        self.filter.changed.connect(self._uploader)
        bar.addWidget(self.filter, 1)
        self.use_dl = QCheckBox("Фильтрация по дате окончания")
        self.use_dl.setFont(font(0.875))
        self.use_dl.setStyleSheet(checkbox_qss())
        bar.addWidget(self.use_dl)
        self.d_from = DateField(QDate.currentDate().addDays(-90))
        self.d_to = DateField(QDate.currentDate().addDays(90))
        for d in (self.d_from, self.d_to):
            d.hide()
            d.changed.connect(lambda _v: self.draw())
            bar.addWidget(d)
        self.use_dl.toggled.connect(lambda on: (self.d_from.setVisible(on), self.d_to.setVisible(on), self.draw()))
        lay.addLayout(bar)
        self.holder = QWidget()
        self.holder.setStyleSheet("background: transparent;")
        self.lst = QVBoxLayout(self.holder)
        self.lst.setContentsMargins(0, 0, 0, 0)
        self.lst.setSpacing(rem(0.75))
        lay.addWidget(self.holder, 1)
        self.lst.addWidget(loading("Собираем медиафайлы…", big=True))
        self.load()

    def _uploader(self, v):
        self.uploader = v
        self.draw()

    def all_files(self):
        return self.extra + [f for fo in self.folders for f in fo.get("files") or []]

    def load(self, soft=False):
        def work():
            try:
                got = client.get("projects/%d/photos" % self.pid) or []
            except Exception as e:
                got = None
                err = e
            try:
                extra = client.get("projects/%d/files" % self.pid, query={"section": "extra"}) or []
            except Exception:
                extra = []
            return got, extra

        def done(res):
            got, extra = res
            if got is None:
                toast(self, "Медиафайлы не загружены", "error")
                got = []
            self.folders = got
            self.extra = [dict(f, file_kind="project") for f in extra]
            people = {}
            for f in self.all_files():
                pid, nm = kfiles.uploader_id(f), kfiles.uploader_name(f)
                if pid and nm:
                    people.setdefault(pid, nm)
            if self.uploader is not None and self.uploader not in people:
                self.uploader = None
            self.filter.set_options([(None, "Все отправители")] + sorted(people.items(), key=lambda x: x[1]), self.uploader)
            self.filter.setVisible(bool(people))
            self.draw()
        jobs.run(work, done=done, owner=self)

    def _by_uploader(self, f):
        return self.uploader is None or kfiles.uploader_id(f) == self.uploader

    def draw(self):
        clear_layout(self.lst)
        needle = self.search.text().strip().lower()
        lst = [f for f in self.folders if any(self._by_uploader(x) for x in f.get("files") or [])]
        if needle:
            lst = [f for f in lst if needle in (f.get("task_name") or "").lower()]
        if self.use_dl.isChecked():
            lo = self.d_from.value()
            hi = self.d_to.value()

            def inside(f):
                d = parse_date(f.get("deadline"))
                if not d:
                    return False
                qd = QDate(d.year, d.month, d.day)
                return (lo is None or qd >= lo) and (hi is None or qd <= hi)
            lst = [f for f in lst if inside(f)]
        self.lst.addWidget(self._extra_folder())
        for f in lst:
            self.lst.addWidget(self._task_folder(f))
        if not lst:
            self.lst.addWidget(empty("В задачах проекта пока нет фото и видео"))
        self.lst.addStretch(1)

    def _extra_folder(self):
        can_upload = self.view.project.get("can_upload") is not False and not rights.read_only()
        cnt = label("файлов: %d" % len([f for f in self.extra if self._by_uploader(f)]), 0.75, theme.TEXT_MUTED)

        def build(folder):
            block = kfiles.CategoryBlock(lambda: [f for f in self.extra if self._by_uploader(f)], can_upload=can_upload,
                                         can_delete=lambda f: can_upload, upload_hint="Загрузка в этот раздел недоступна",
                                         upload_path="projects/%d/files" % self.pid, upload_fields={"section": "extra"},
                                         on_changed=lambda: self.load(True), sort_earliest_first=True, no_tabs=True,
                                         accept="media",          # медиаконтент: только фото и видео
                                         on_uploaded=lambda created: self.extra.__setitem__(
                                             slice(0, 0), [dict(x, file_kind="project") for x in created]))
            block.setMinimumHeight(rem(14))
            folder.body_lay.addWidget(block)
        f = _Folder("Дополнительные файлы", [cnt], accent=True, build=build)
        f.toggled_key = "extra"
        f.head.clicked.connect(lambda: self._remember("extra", f))
        if "extra" in self.opened:
            f.toggle(True)
        return f

    def _remember(self, key, folder):
        if folder.is_open():
            self.opened.add(key)
        else:
            self.opened.discard(key)

    def _task_folder(self, folder):
        st = deadline_state(folder.get("deadline"), folder.get("activity") == "Активная")
        files = sorted([dict(f, file_kind="stage" if f.get("is_stage_file") else "task")
                        for f in folder.get("files") or [] if self._by_uploader(f)],
                       key=lambda f: (parse_date(f.get("created_at")).timestamp() if parse_date(f.get("created_at")) else 0))
        color = theme.CRITICAL if st == "overdue" else (theme.WARNING_TEXT if st == "urgent" else theme.TEXT_LABEL)
        dl = label("до " + fmt_date(folder.get("deadline")), 0.75, color)
        cnt = label("файлов: %d" % len(files), 0.75, theme.TEXT_MUTED)
        tid = folder.get("task_id")

        def build(fw):
            fw.body_lay.addWidget(muted("Собираем файлы…", 0.8))

            def deps():
                if tid in self.stage_names:
                    return self.stage_names[tid]
                m = {}
                try:
                    task = client.get("tasks/%s" % tid)
                    for s in task.get("stage_details") or []:
                        m[s.get("stage_number")] = s.get("department_name") or ""
                except Exception:
                    pass
                self.stage_names[tid] = m
                return m

            def ready(m):
                clear_layout(fw.body_lay)
                by_stage, task_level = {}, []
                for f in files:
                    if f.get("is_stage_file") and f.get("stage_number") is not None:
                        by_stage.setdefault(f["stage_number"], []).append(f)
                    else:
                        task_level.append(f)
                wanted = [("Файлы этапа %s" % n + (" — " + m[n] if m.get(n) else ""), by_stage[n]) for n in sorted(by_stage)]
                if task_level:
                    wanted.append(("Файлы задачи", task_level))
                for title, lst in wanted:
                    fw.body_lay.addWidget(self._sub(title, lst))
                if not wanted:
                    fw.body_lay.addWidget(muted("Файлов нет", 0.8))
            jobs.run(deps, done=ready, owner=fw)
        f = _Folder(folder.get("task_name") or "Задача", [dl, cnt], build=build)
        key = "task-%s" % tid
        f.head.clicked.connect(lambda: self._remember(key, f))
        if key in self.opened:
            f.toggle(True)
        return f

    def _sub(self, title, files):
        cnt = label("файлов: %d" % len(files), 0.75, theme.TEXT_MUTED)

        def build(fw):
            grid = kfiles.FileGrid(lambda f: False, "Файлов нет")
            grid.open_requested.connect(lambda f: kfiles.open_file(self, f, files))
            grid.download_requested.connect(lambda f: kfiles.download(self, f))
            grid.area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            grid.fill(files)
            rows = (len(files) + 2) // 3
            grid.setMinimumHeight(rows * rem(7.2))
            fw.body_lay.addWidget(grid)
        return _Folder(title, [cnt], sub=True, build=build)
