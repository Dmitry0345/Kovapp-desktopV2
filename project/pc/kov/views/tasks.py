# -*- coding: utf-8 -*-
"""Раздел «Задачи» — как js/views/tasks.js веб-версии.

Список активных или архивных задач, поиск по выбранному полю, окно
«Фильтрация задач» (хранится на сервере: /task-filters/{архив}).
Порядок «по важности» (по умолчанию) считает сервер одним запросом:
сначала задачи, где сейчас этап вашего отдела, — просроченные, на сегодня,
остальные; затем остальные задачи в том же порядке.
"""

import json

from PySide6.QtCore import QDate, QSize, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QWidget

from general_functions import theme
from kov import icons, jobs, rights
from kov.api import client, human_error
from kov.dialogs import Modal, confirm, date_edit, field
from kov.fmt import deadline_state, fmt_date, fmt_datetime, full_name, parse_date
from kov.store import store
from kov.ui import (Button, DataTable, Segmented, SearchInput, Select, StageDots, TableEmpty, count_label, font,
                    label, rem, toast)
from kov.views.base import Section

SEARCH_MODES = [("name", "Название"), ("organization", "Организация"),
                ("responsible", "Ответственный"), ("department", "Отдел")]
ORDER_CODES = ("id_asc", "id_desc", "deadline_asc", "deadline_desc", "archived_asc", "archived_desc")
TASK_LOCKED = "Изменять задачу может руководство или руководитель отдела, к которому относятся все её этапы"


def active_stage(task):
    state = task.get("state")
    if state is None:
        return None
    try:
        num = int(state)
    except (TypeError, ValueError):
        return None
    for s in task.get("stage_details") or []:
        if s.get("stage_number") == num:
            return s
    return None


def is_mine(task):
    dep = store.department()
    stage = active_stage(task)
    return bool(dep and stage and stage.get("department_name") == dep and not stage.get("is_completed"))


def stage_flags(task):
    stages = sorted(task.get("stage_details") or [], key=lambda s: s.get("stage_number") or 0)
    return [bool(s.get("is_completed")) for s in stages]


class TasksSection(Section):
    title = "Задачи"
    key = "tasks"

    def __init__(self, shell, archived=False, parent=None):
        super().__init__(shell, parent)
        self.archived = archived
        self.tasks = []
        self.filter = None
        self.sig = None
        self.selected = None
        self.rows = []

        bar = QHBoxLayout()
        bar.setSpacing(rem(0.5))
        self.search = SearchInput("Поиск по задачам…")
        self.search.textChanged.connect(lambda _t: self.render())
        bar.addWidget(self.search, 1)
        self.mode = Select(SEARCH_MODES, prefix="Искать в:", min_width_rem=14)
        self.mode.setToolTip("Поле, по которому идёт поиск")
        self.mode.currentIndexChanged.connect(lambda _i: self.render())
        bar.addWidget(self.mode)
        self.filter_btn = Button("Фильтры", "ghost", "filter", "Сортировка, число этапов, дата создания")
        self.filter_btn.clicked.connect(self.filter_dialog)
        bar.addWidget(self.filter_btn)
        self.root.addLayout(bar)

        self.table = DataTable([], stretch=[0], left_columns=(0,))
        self.table.setIconSize(QSize(rem(0.95), rem(0.95)))      # флажок «мой этап»: не от DPI экрана
        self.table.itemSelectionChanged.connect(self._on_select)
        self.table.row_activated.connect(self._open_row)
        self.root.addWidget(self.table, 1)
        self.empty = TableEmpty(self.table, "Задач нет")

        bottom = QHBoxLayout()
        bottom.setSpacing(rem(0.5))
        self.seg = Segmented([("active", "Активные", "flag"), ("archived", "Архивные", "archive")],
                             "archived" if archived else "active")
        self.seg.changed.connect(self._switch)
        bottom.addWidget(self.seg)
        self.summary = count_label("")
        bottom.addWidget(self.summary)
        bottom.addStretch(1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(rem(0.5))
        bottom.addLayout(self.actions)
        self.root.addLayout(bottom)

        self._reload = QTimer(self)
        self._reload.setSingleShot(True)
        self._reload.timeout.connect(lambda: self.refresh(quiet=True))
        self._poll = QTimer(self)
        self._poll.setInterval(60000)
        self._poll.timeout.connect(lambda: self.refresh(quiet=True))
        self._poll.start()
        self._build_actions()
        self.refresh()

    # ── данные ───────────────────────────────────────────────────────────────

    def _load(self, archived):
        try:
            tasks = client.get("tasks/light", query={"is_archived": str(archived).lower()})
        except Exception as e:
            if getattr(e, "status", None) in (404, 422):
                tasks = client.get("tasks", query={"is_archived": str(archived).lower()})
            else:
                raise
        try:
            flt = client.get("task-filters/%s" % str(archived).lower(), query={"force_create": "true"})
        except Exception:
            flt = None
        return archived, tasks or [], flt

    def refresh(self, quiet=False):
        def done(result):
            archived, tasks, flt = result
            if archived != self.archived:
                return
            sig = json.dumps([tasks, flt], sort_keys=True, default=str)
            changed = sig != self.sig
            self.sig = sig
            self.tasks, self.filter = tasks, flt
            if quiet and not changed:
                return
            self.render(keep=quiet)

        def failed(exc):
            if not quiet:
                toast(self, "Не удалось загрузить задачи: " + human_error(exc), "error")
        jobs.run(self._load, self.archived, done=done, failed=failed, owner=self)

    def sort_codes(self):
        raw = (self.filter or {}).get("sort_by") or ""
        return [c.strip() for c in str(raw).split(",") if c.strip()]

    def filter_active(self):
        f = self.filter
        if not f:
            return False
        if f.get("stage_count") or f.get("min_created_date") or f.get("max_created_date"):
            return True
        codes = self.sort_codes()
        default = "archived_desc" if self.archived else "priority"
        return not (not codes or (len(codes) == 1 and codes[0] == default))

    def sorted_tasks(self, tasks):
        far = 253402214400.0   # 31.12.9999
        codes = [c for c in self.sort_codes() if c in ORDER_CODES]
        if not codes and self.archived:
            codes = ["archived_desc"]
        if not codes:
            return list(tasks)          # «по важности»: порядок задал сервер
        result = list(tasks)
        for code in reversed(codes):
            field_name, _, direction = code.rpartition("_")

            def key(t, f=field_name):
                if f == "deadline":
                    d = parse_date(t.get("deadline"))
                    return d.timestamp() if d else far
                if f == "id":
                    return t.get("id") or 0
                if f == "archived":
                    d = parse_date(t.get("archived_at"))
                    return d.timestamp() if d else far
                return 0
            result.sort(key=key, reverse=(direction == "desc"))
        return result

    def matches(self, task):
        needle = self.search.text().strip().lower()
        if not needle:
            return True
        mode = self.mode.value()
        if mode == "organization":
            return needle in (task.get("customer_organization") or "").lower()
        if mode == "responsible":
            return any(needle in full_name(p).lower() for p in task.get("responsible_persons") or [])
        if mode == "department":
            return any(needle in (s.get("department_name") or "").lower() for s in task.get("stage_details") or [])
        return needle in (task.get("name") or "").lower()

    # ── отрисовка ────────────────────────────────────────────────────────────

    def headers(self):
        if self.archived:
            return ["Название", "Организация", "Дата архивации", "Кто архивировал", "Прогресс"]
        return ["Название", "Организация", "Дата окончания", "Отделы", "Прогресс"]

    def render(self, keep=False):
        keep_id = self.selected.get("id") if (keep and self.selected) else None
        rows = self.sorted_tasks([t for t in self.tasks if self.matches(t)])
        self.rows = rows
        t = self.table
        t.blockSignals(True)
        t.clear()
        t.setColumnCount(5)
        t.setHorizontalHeaderLabels(self.headers())
        t.setRowCount(len(rows))
        for r, task in enumerate(rows):
            active = task.get("activity") == "Активная"
            state = deadline_state(task.get("deadline"), active)
            bg = theme.CRITICAL_SOFT if state == "overdue" else (theme.WARNING_SOFT if state == "urgent" else None)
            mine = is_mine(task)
            name = t.cell(task.get("name") or "", 0, bold=mine, bg=bg, data=task.get("id"))
            if mine:
                glyph, color, hint = (("warning", theme.CRITICAL, "просрочена") if state == "overdue" else
                                      ("clock", theme.WARNING, "меньше суток до даты окончания") if state == "urgent" else
                                      ("flag", theme.ACCENT, "в работе"))
                name.setIcon(icons.icon(glyph, rem(0.95), color, stroke=2.2))
                name.setToolTip("Текущий этап ведёт ваш отдел — " + hint)
            t.setItem(r, 0, name)
            t.setItem(r, 1, t.cell(task.get("customer_organization") or "Не указана", 1, bg=bg))
            if self.archived:
                t.setItem(r, 2, t.cell(fmt_datetime(task.get("archived_at")), 2, bg=bg))
                who = task.get("who_archived_employee")
                t.setItem(r, 3, t.cell(full_name(who) if who else "Неизвестный", 3, bg=bg))
            else:
                deadline = task.get("deadline")
                text = fmt_date(deadline) if deadline else "Не указана"
                item = t.cell("", 2, bg=bg)
                t.setItem(r, 2, item)
                lab = QLabel()
                lab.setAlignment(Qt.AlignCenter)
                lab.setStyleSheet("background: transparent;")
                if state == "overdue":
                    lab.setText(f"<div style='color:{theme.DEADLINE_OVERDUE}; font-weight:700'>{text}</div>"
                                f"<div style='color:{theme.DEADLINE_OVERDUE}; font-size:{rem(0.68)}px; font-weight:700'>просрочено</div>")
                    lab.setToolTip("Дата окончания просрочена")
                elif state == "urgent":
                    lab.setText(f"<b style='color:{theme.WARNING_TEXT}'>{text}</b>")
                    lab.setToolTip("До даты окончания меньше суток")
                else:
                    lab.setText(f"<span style='color:{theme.TEXT_VALUE}'>{text}</span>")
                lab.setFont(font(0.875))
                t.setCellWidget(r, 2, lab)
                deps = sorted({s.get("department_name") for s in task.get("stage_details") or [] if s.get("department_name")})
                t.setItem(r, 3, t.cell(", ".join(deps) or "Нет отделов", 3, bg=bg))
            t.setItem(r, 4, t.cell("", 4, bg=bg))
            holder = QWidget()
            holder.setStyleSheet("background: transparent;")
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(0, 0, 0, 0)
            flags = stage_flags(task)
            cur = None
            stage = active_stage(task)
            if stage is not None and flags:
                ordered = sorted(task.get("stage_details") or [], key=lambda s: s.get("stage_number") or 0)
                cur = next((i for i, s in enumerate(ordered) if s is stage), None)
            hl.addStretch(1)
            hl.addWidget(StageDots(flags, cur))
            hl.addStretch(1)
            t.setCellWidget(r, 4, holder)
        t.fit_columns()
        t.resizeRowsToContents()
        for r in range(t.rowCount()):
            t.setRowHeight(r, max(t.rowHeight(r), rem(2.43)))
        t.blockSignals(False)
        self.selected = None
        if keep_id is not None:
            for r, task in enumerate(rows):
                if task.get("id") == keep_id:
                    t.selectRow(r)
                    self.selected = task
                    break
        self.summary.setText(("Задач: %d" % len(rows) + (" (по фильтру)" if self.search.text().strip() else "")) if rows else "")
        self.empty.setVisible(not rows)
        self._update_buttons()

    # ── кнопки ───────────────────────────────────────────────────────────────

    def _build_actions(self):
        while self.actions.count():
            w = self.actions.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self.edit_btn = self.restore_btn = self.del_btn = None
        if not store.can_manage_work():
            return
        if not self.archived:
            self.edit_btn = Button("Редактировать", "ghost", "edit")
            self.edit_btn.clicked.connect(self._edit)
            self.del_btn = Button("Удалить", "danger", "trash")
            self.del_btn.clicked.connect(self._delete)
            new_btn = Button("Новая задача", "primary", "plus")
            new_btn.clicked.connect(lambda: self.shell.go("task_new"))
            for b in (self.edit_btn, self.del_btn, new_btn):
                self.actions.addWidget(b)
        else:
            self.restore_btn = Button("Вернуть в работу", "ghost", "refresh")
            self.restore_btn.clicked.connect(self._restore)
            self.del_btn = Button("Удалить", "danger", "trash")
            self.del_btn.clicked.connect(self._delete)
            self.actions.addWidget(self.restore_btn)
            self.actions.addWidget(self.del_btn)
        self._update_buttons()

    def _update_buttons(self):
        has = self.selected is not None
        allowed = has and rights.can_manage_task(self.selected)
        for b in (self.edit_btn, self.restore_btn, self.del_btn):
            if b is None:
                continue
            b.setEnabled(has)
            b.set_locked(has and not allowed, TASK_LOCKED)
        on = self.filter_active()
        self.filter_btn.setStyleSheet(self.filter_btn.styleSheet() if not on else
                                      self.filter_btn.styleSheet() +
                                      f"QPushButton {{ background-color: {theme.WARNING_SOFT}; border-color: {theme.WARNING}; }}")
        if not on:
            self.filter_btn.refresh()

    def _on_select(self):
        row = self.table.currentRow()
        self.selected = self.rows[row] if (0 <= row < len(self.rows) and self.table.selectionModel().hasSelection()) else None
        self._update_buttons()

    def _open_row(self, row):
        if 0 <= row < len(self.rows):
            self.shell.go("task", task_id=self.rows[row]["id"])

    def _switch(self, key):
        self.archived = key == "archived"
        self.selected = None
        self.sig = None
        self._build_actions()
        self.tasks = []
        self.render()
        self.refresh()

    def _edit(self):
        if self.selected:
            self.shell.go("task_edit", task_id=self.selected["id"])

    def _restore(self):
        t = self.selected
        if not t:
            return
        if not confirm(self, "Вернуть в работу", "Вернуть задачу «%s» в работу?" % t.get("name"), "Вернуть", glyph="refresh"):
            return
        if t.get("project_archived"):
            toast(self, "Задача относится к завершённому проекту — сначала верните проект в работу", "warning")
            return

        def ok(_):
            toast(self, "Задача возвращена в работу", "success")
            self.refresh()
        jobs.run(client.post, "tasks/%d/unarchive" % t["id"], done=ok,
                 failed=lambda e: toast(self, "Не удалось: " + human_error(e), "error"), owner=self)

    def _delete(self):
        t = self.selected
        if not t:
            return
        warn = ("Удалить задачу «%s» без возможности восстановления?\n\n"
                "Вместе с ней уйдут: все этапы и их описания, файлы задачи и этапов вместе с папкой на сервере, "
                "участники, ответственные и уведомления по этой задаче." % t.get("name"))
        if t.get("activity") == "Активная":
            warn += "\n\nЗадача сейчас в работе — возможно, её достаточно отправить в архив."
        if not confirm(self, "Удаление задачи", warn, "Удалить навсегда", danger=True):
            return

        def ok(_):
            toast(self, "Задача удалена", "success")
            self.refresh()
        jobs.run(client.delete, "tasks/%d" % t["id"], done=ok,
                 failed=lambda e: toast(self, "Не удалось удалить: " + human_error(e), "error"), owner=self)

    # ── окно «Фильтрация задач» (прежний диалог ПК) ─────────────────────────

    def filter_dialog(self):
        f = self.filter or {}
        codes = self.sort_codes()

        def pick(prefix):
            return next((c for c in codes if c.startswith(prefix + "_")), None)
        none = [(None, "—")]
        id_sel = Select(none + [("id_asc", "По возрастанию"), ("id_desc", "По убыванию")])
        id_sel.set_value(pick("id"))
        dl_sel = Select(none + [("deadline_asc", "По возрастанию"), ("deadline_desc", "По убыванию")])
        dl_sel.set_value(pick("deadline"))
        ar_sel = Select(none + [("archived_asc", "Сначала старые"), ("archived_desc", "Сначала новые")])
        ar_sel.set_value(pick("archived"))
        stages = Select(none + [(i, str(i)) for i in range(1, 11)])
        stages.set_value(f.get("stage_count"))
        from kov.ui import checkbox_qss
        use_dates = QCheckBox("Фильтр по дате создания")
        use_dates.setFont(font(0.875))
        use_dates.setStyleSheet(checkbox_qss())
        use_dates.setChecked(bool(f.get("min_created_date") or f.get("max_created_date")))
        d_from = parse_date(f.get("min_created_date"))
        d_to = parse_date(f.get("max_created_date"))
        from_e = date_edit(QDate(d_from.year, d_from.month, d_from.day) if d_from else QDate.currentDate().addYears(-1))
        to_e = date_edit(QDate(d_to.year, d_to.month, d_to.day) if d_to else QDate.currentDate())
        dates = QWidget()
        dl = QHBoxLayout(dates)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(rem(0.75))
        dl.addWidget(field("Создана с", from_e))
        dl.addWidget(field("Создана по", to_e))
        use_dates.toggled.connect(dates.setVisible)

        m = Modal(self, "Фильтрация задач", "filter", width_rem=32)
        m.body.addWidget(field("Сортировка по ID", id_sel))
        m.body.addWidget(field("Сортировка по дате окончания", dl_sel))
        if self.archived:
            m.body.addWidget(field("Сортировка по дате архивации", ar_sel))
        else:
            hint = label("Если сортировка не выбрана, задачи идут по важности: сначала те, где сейчас этап вашего "
                         "отдела (просроченные, на сегодня, остальные), затем чужие просроченные и на сегодня, "
                         "затем все остальные.", 0.75, theme.TEXT_LABEL, wrap=True)
            m.body.addWidget(hint)
        m.body.addWidget(field("Количество этапов", stages))
        m.body.addWidget(use_dates)
        m.body.addWidget(dates)
        dates.setVisible(use_dates.isChecked())       # уже в окне: без родителя виджет на миг стал бы отдельным окном
        archived = str(self.archived).lower()

        def reset(_m):
            try:
                client.post("task-filters/%s/reset" % archived)
            except Exception as e:
                toast(self, "Не удалось сбросить: " + human_error(e), "error")
                return False
            self.refresh()
            return True

        def apply(_m):
            sort_by = ",".join(x for x in (id_sel.value(), dl_sel.value(),
                                           ar_sel.value() if self.archived else None) if x) or None
            mn = mx = None
            if use_dates.isChecked():
                if from_e.date() > to_e.date():
                    toast(self, "Начальная дата позже конечной", "warning")
                    return False
                mn = from_e.date().toString("yyyy-MM-dd") + "T00:00:00"
                mx = to_e.date().toString("yyyy-MM-dd") + "T23:59:59"
            try:
                client.put("task-filters/%s" % archived, json_body={
                    "sort_by": sort_by, "stage_count": stages.value(),
                    "min_created_date": mn, "max_created_date": mx})
            except Exception as e:
                toast(self, "Не удалось сохранить фильтр: " + human_error(e), "error")
                return False
            self.refresh()
            return True
        m.add_button("Сбросить", "ghost", "refresh", reset, left=True)
        m.add_button("Отмена", "ghost")
        m.add_button("Применить", "primary", "check", apply)
        m.exec()

    # ── события ──────────────────────────────────────────────────────────────

    def on_event(self, evt):
        kind = evt.get("type") or ""
        if "file" in kind:
            return
        if kind.startswith("task.") or kind == "tasks.changed" or (kind.startswith("stage.") and kind != "stage.description_updated"):
            self._reload.start(300)

    def on_reconnect(self):
        self._reload.start(400)
