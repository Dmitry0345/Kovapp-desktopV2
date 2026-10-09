# -*- coding: utf-8 -*-
"""Форма задачи (создание и правка) — как js/views/task-edit.js веб-версии:
название, заказчик из справочника, дата окончания, описание, этапы с
отделами, ответственные и исполнители, шаблоны задач. Задача проекта
создаётся из окна проекта — организация тогда берётся из проекта."""

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QPlainTextEdit, QRadioButton,
                             QSpinBox, QVBoxLayout, QWidget)

from general_functions import theme
from kov import jobs, notify, rights
from kov.api import client, human_error
from kov.dialogs import Modal, confirm, field
from kov.people import avatar, full_name
from kov.store import store
from kov.ui import (Button, Card, Chip, DateField, Flow, PersonChip, PickerSelect, SearchInput, Select, border,
                    checkbox_qss, clear_layout, empty, font, gate, input_qss, label, line_edit, loading, muted, rem,
                    scroll_area, text_qss, toast)
from kov.views.base import Section

BASE_DEPARTMENTS = ["Монтажный отдел", "Бухгалтерия", "Отдел ТО", "Договорной отдел", "Руководство"]
NON_TASK_DEPARTMENTS = ("Администрация", "Склад")
TEMPLATES_LOCKED = "Шаблоны задач ведут директор и администратор"


def can_be_responsible(e):
    return store.can_manage_work(e)


def allowed_for(key, e):
    return can_be_responsible(e) if key == "responsible" else not can_be_responsible(e)


def spin(value, maximum, minimum=1):
    from kov.ui import SpinBox
    s = SpinBox()
    s.setRange(minimum, maximum)
    s.setValue(value)
    return s


class TaskEdit(Section):
    title = "Новая задача"
    key = "tasks"

    def __init__(self, shell, task_id=None, project_id=None, parent=None):
        super().__init__(shell, parent)
        self.task_id = int(task_id) if task_id else None
        self.project_id = project_id
        self.title = "Редактирование задачи" if self.task_id else "Новая задача"
        self.root.addWidget(loading("Загружаем задачу…" if self.task_id else "Готовим форму…", big=True), 1)
        jobs.run(self._load, done=self._build, failed=self._failed, owner=self)

    def _load(self):
        task = client.get("tasks/%d" % self.task_id) if self.task_id else None
        try:
            orgs = client.get("organizations") or []
        except Exception:
            orgs = []
        employees = store.pickable()
        store.load_photo_ids()
        store.lists()
        project = None
        if self.project_id and not task:
            try:
                project = client.get("projects/%s" % self.project_id)
            except Exception:
                project = {"id": self.project_id}
        return task, orgs, employees, project

    def _failed(self, e):
        clear_layout(self.root)
        self.root.addWidget(empty("Не удалось открыть форму: " + human_error(e)), 1)

    def _back(self):
        if self.task_id:
            self.shell.replace("task", task_id=self.task_id)
        elif self.project_id:
            self.shell.replace("project", project_id=self.project_id)
        else:
            self.shell.replace("tasks")

    def _build(self, res):
        task, orgs, employees, project = res
        self.task, self.employees = task, employees
        clear_layout(self.root)
        from_server = [d for d in (store.departments or []) if d]
        all_deps = [d for d in (from_server or BASE_DEPARTMENTS) if d not in NON_TASK_DEPARTMENTS]
        self.departments = rights.departments_for_new_task(all_deps)
        self.max_stages = min(10, rights.max_stages_on_create())
        if task and not rights.can_manage_task(task):
            self.root.addWidget(empty("Править эту задачу может руководство или руководитель отдела, "
                                      "к которому относятся все её этапы"))
            b = Button("К задаче", "ghost", "arrow_back")
            b.clicked.connect(self._back)
            self.root.addWidget(b, 0, Qt.AlignHCenter)
            self.root.addStretch(1)
            return
        if not self.departments:
            self.root.addWidget(empty("Ваш отдел не ведёт этапы задач — создать задачу не получится"))
            b = Button("К списку задач", "ghost", "arrow_back")
            b.clicked.connect(lambda: self.shell.replace("tasks"))
            self.root.addWidget(b, 0, Qt.AlignHCenter)
            self.root.addStretch(1)
            return

        self.preset = project if (project and not task) else None
        locked_org = (self.preset or {}).get("organization") if self.preset else \
            ((task.get("project") or {}).get("organization") if task and isinstance(task.get("project"), dict) else None)
        self.locked_org = locked_org or None

        card = Card(name="formCard", padding=(0.9, 1.0), spacing=0.8)
        self.name = line_edit("Название задачи", (task or {}).get("name") or "")
        org_opts = [(None, "— Не указана —")] + sorted([(o.get("name"), o.get("name")) for o in orgs if o.get("name")],
                                                      key=lambda x: x[1].lower())
        cur_org = (task or {}).get("customer_organization")
        if task and cur_org and not any(v == cur_org for v, _ in org_opts):
            org_opts.insert(1, (cur_org, cur_org + " (нет в справочнике)"))
        if self.locked_org and not any(v == self.locked_org for v, _ in org_opts):
            org_opts.insert(1, (self.locked_org, self.locked_org))
        self.org = PickerSelect(org_opts, self.locked_org or cur_org, "— Не указана —",
                                "Поиск по названию организации…", "Организация не найдена")
        if self.locked_org:
            self.org.setEnabled(False)
            self.org.setToolTip("Организация берётся из проекта. Снимите привязку к проекту, чтобы её изменить")
        grid = QGridLayout()
        grid.setHorizontalSpacing(rem(0.75))
        grid.addWidget(field("Название", self.name, True), 0, 0)
        grid.addWidget(field("Заказчик", self.org), 0, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        card.body.addLayout(grid)
        note = None
        if self.preset:
            note = "Задача будет привязана к проекту «%s»" % (self.preset.get("name") or self.preset.get("id")) + \
                (" · организация: " + self.locked_org if self.locked_org else "")
        elif task and isinstance(task.get("project"), dict):
            note = "Проект: " + (task["project"].get("name") or "") + (" · организация: " + self.locked_org if self.locked_org else "")
        if note:
            card.body.addWidget(Chip(note, "info"), 0, Qt.AlignLeft)
        dl = None
        if task and task.get("deadline"):
            dl = QDate.fromString(str(task["deadline"])[:10], "yyyy-MM-dd")
        self.deadline = DateField(dl, None if task else QDate.currentDate())
        card.body.addWidget(field("Дата окончания", self.deadline, True))
        self.description = QPlainTextEdit((task or {}).get("description") or "")
        self.description.setPlaceholderText("Описание задачи")
        self.description.setFont(font(0.875))
        self.description.setStyleSheet(text_qss())
        self.description.setFixedHeight(rem(7))      # textarea сайта: min-height 7rem
        card.body.addWidget(field("Описание", self.description))

        self.existing = sorted((task or {}).get("stage_details") or [], key=lambda s: s.get("stage_number") or 0)
        n0 = len(self.existing) or 1
        self.count = spin(n0, 10 if task else max(1, self.max_stages))
        if task or self.max_stages == 1:
            self.count.setEnabled(False)
        if not task and self.max_stages == 1:
            self.count.setToolTip("Руководитель отдела заводит задачу с одним этапом своего отдела — "
                                  "другие отделы подключаются продлением задачи")
        crow = QHBoxLayout()
        crow.setSpacing(rem(0.5))
        crow.addWidget(self.count)
        if task:
            crow.addWidget(muted("Число этапов меняется кнопкой «Продолжить задачу» в окне задачи", 0.75))
        else:
            tb = Button("Шаблоны задач", "ghost", "file")
            tb.clicked.connect(self.templates_dialog)
            crow.addWidget(tb)
        crow.addStretch(1)
        cw = QWidget()
        cw.setLayout(crow)
        crow.setContentsMargins(0, 0, 0, 0)
        card.body.addWidget(field("Количество этапов", cw))
        self.stages_box = QVBoxLayout()
        self.stages_box.setSpacing(rem(0.45))
        card.body.addLayout(self.stages_box)
        self.stage_selects = []
        self.count.valueChanged.connect(lambda _v: self.rebuild_stages())
        self.rebuild_stages([s.get("department_name") for s in self.existing])

        self.selected = {"responsible": set(p.get("id") for p in (task or {}).get("responsible_persons") or []),
                         "assigned": set(p.get("id") for p in (task or {}).get("assigned_employees") or [])}
        self.people_rows = {}
        card.body.addWidget(self._people_field("responsible", "Ответственные"))
        card.body.addWidget(self._people_field("assigned", "Исполнители"))
        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = Button("Отмена", "ghost")
        cancel.clicked.connect(self._back)
        save = Button("Сохранить", "primary", "save")
        save.clicked.connect(self.save)
        self.save_btn = save
        btns.addWidget(cancel)
        btns.addWidget(save)
        card.body.addLayout(btns)
        holder = QWidget()
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(rem(0.75))
        hl.addWidget(card)
        if not task:
            hl.addWidget(label("Файлы можно будет прикрепить сразу после создания — в окне задачи.", 0.75, theme.TEXT_LABEL))
        hl.addStretch(1)
        self.root.addWidget(scroll_area(holder), 1)

    # ── этапы ────────────────────────────────────────────────────────────────

    def stage_deps(self):
        return [s.value() for s in self.stage_selects]

    def rebuild_stages(self, deps=None):
        n = min(10 if self.task else self.max_stages, max(1, self.count.value()))
        current = self.stage_deps()
        clear_layout(self.stages_box)
        self.stage_selects = []
        for i in range(1, n + 1):
            want = (deps[i - 1] if deps and i - 1 < len(deps) else None) or \
                (current[i - 1] if i - 1 < len(current) else None) or \
                (self.existing[i - 1].get("department_name") if i - 1 < len(self.existing) else None)
            sel = Select([(d, d) for d in self.departments])
            if want in self.departments:
                sel.set_value(want)
            row = QHBoxLayout()
            row.setSpacing(rem(0.5))
            row.addWidget(Chip("Этап %d" % i))
            row.addWidget(sel, 1)
            self.stages_box.addLayout(row)
            self.stage_selects.append(sel)

    # ── люди ─────────────────────────────────────────────────────────────────

    def _people_field(self, key, title):
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(rem(0.4))
        chips = Flow()
        self.people_rows[key] = chips
        lay.addWidget(chips)
        pick = Button("Выбрать", "ghost", "users")
        pick.clicked.connect(lambda: self.pick_people(key, title))
        lay.addWidget(pick, 0, Qt.AlignLeft)
        self._render_chips(key)
        return field(title, box)

    def _render_chips(self, key):
        flow = self.people_rows[key]
        flow.clear()
        people = [e for e in self.employees if e.get("id") in self.selected[key]]
        if not people:
            flow.add(muted("Никто не выбран", 0.875))
            return
        for e in people:
            chip = PersonChip(e, removable=True)
            chip.removed.connect(lambda p, k=key: (self.selected[k].discard(p.get("id")), self._render_chips(k)))
            flow.add(chip)

    def pick_people(self, key, title):
        temp = set(self.selected[key])
        only_heads = key == "responsible"
        stage_set = set(d for d in self.stage_deps() if d)
        by_stages = [e for e in self.employees if e.get("department") in stage_set] if stage_set else list(self.employees)
        pool = [e for e in by_stages if allowed_for(key, e) or e.get("id") in self.selected[key]]
        m = Modal(self, title, "users", width_rem=41.4)
        hint = ("Сначала выберите отделы этапов" if not stage_set else
                ("Ответственными могут быть только старшие и главные сотрудники, директор и администратор. "
                 "Отделы этапов: " if only_heads else
                 "Исполнителями могут быть только рядовые сотрудники — старшие, главные, директор и администратор "
                 "идут в ответственные. Отделы этапов: ") + ", ".join(sorted(stage_set)))
        m.body.addWidget(label(hint, 0.75, theme.TEXT_LABEL, wrap=True))
        bar = QHBoxLayout()
        search = SearchInput("Поиск по ФИО...")
        bar.addWidget(search, 1)
        deps = sorted(set(e.get("department") for e in pool if e.get("department")) - {"Администрация"})
        dep_pick = Select([(None, "Выбрать весь отдел…")] + [(d, d) for d in deps], min_width_rem=14)
        dep_pick.setToolTip("Добавить старших и главных сотрудников отдела" if only_heads else
                            "Добавить рядовых сотрудников отдела")
        bar.addWidget(dep_pick)
        m.body.addLayout(bar)
        holder = QFrame()
        holder.setObjectName("checklist")
        holder.setStyleSheet(f"QFrame#checklist {{ border: {border()}px solid {theme.CARD_BORDER}; border-radius: {rem(0.5)}px;"
                             f" background: {theme.CARD_BG}; }}")
        lst = QVBoxLayout(holder)
        lst.setContentsMargins(rem(0.4), rem(0.4), rem(0.4), rem(0.4))
        lst.setSpacing(rem(0.25))
        area = scroll_area(holder)
        area.setMinimumHeight(rem(14))
        area.setMaximumHeight(rem(23))
        m.body.addWidget(area)

        def render():
            clear_layout(lst)
            needle = search.text().strip().lower()
            rows = [e for e in pool if not needle or needle in full_name(e, True).lower()]
            for e in rows:
                allowed = allowed_for(key, e)
                row = QFrame()
                row.setObjectName("chkRow")
                row.setStyleSheet(f"QFrame#chkRow {{ border-radius: {rem(0.35)}px; }}"
                                  + (f"QFrame#chkRow:hover {{ background: {theme.CHIP_BG}; }}" if allowed else ""))
                rl = QHBoxLayout(row)
                rl.setContentsMargins(rem(0.4), rem(0.35), rem(0.4), rem(0.35))
                rl.setSpacing(rem(0.5))
                cb = QCheckBox()
                cb.setStyleSheet(checkbox_qss())
                cb.setChecked(e.get("id") in temp)
                cb.setEnabled(allowed or e.get("id") in temp)
                cb.toggled.connect(lambda on, pid=e.get("id"): temp.add(pid) if on else temp.discard(pid))
                rl.addWidget(cb)
                rl.addWidget(avatar(e, 1.7))
                txt = "%s <span style='color:%s; font-size:%dpx'> — %s</span>" % (
                    full_name(e, True), theme.TEXT_MUTED, rem(0.75),
                    ", ".join(x for x in (e.get("department"), e.get("position")) if x))
                lab = label("", 0.875, theme.TEXT_VALUE)
                lab.setTextFormat(Qt.RichText)
                lab.setText(txt)
                rl.addWidget(lab, 1)
                if not allowed:
                    rl.addWidget(Chip("не по должности", "warning"))
                    row.setEnabled(e.get("id") in temp)
                    if e.get("id") not in temp:
                        lab.setStyleSheet(f"color: {theme.TEXT_MUTED}; background: transparent;")
                lst.addWidget(row)
            if not rows:
                lst.addWidget(muted("В выбранных отделах нет старших и главных сотрудников" if only_heads else
                                    "В выбранных отделах нет рядовых сотрудников", 0.85))
            lst.addStretch(1)
        search.textChanged.connect(lambda _t: render())

        def dep_chosen(_i):
            dep = dep_pick.value()
            if not dep:
                return
            add = [e for e in pool if e.get("department") == dep and allowed_for(key, e)]
            for e in add:
                temp.add(e.get("id"))
            dep_pick.blockSignals(True)
            dep_pick.setCurrentIndex(0)
            dep_pick.blockSignals(False)
            render()
            toast(m, "Добавлено из отдела: %d" % len(add) if add else
                  "В отделе «%s» нет подходящих для этой роли сотрудников" % dep, "success" if add else "warning")
        dep_pick.currentIndexChanged.connect(dep_chosen)
        render()

        def done(_m):
            self.selected[key] = set(temp)
            self._render_chips(key)
            return True
        m.add_button("Отмена", "ghost")
        m.add_button("Готово", "primary", "check", done)
        m.exec()

    # ── шаблоны ──────────────────────────────────────────────────────────────

    def templates_dialog(self):
        m = Modal(self, "Шаблоны задач", "file", width_rem=52)
        holder = QFrame()
        holder.setObjectName("checklist")
        holder.setStyleSheet(f"QFrame#checklist {{ border: {border()}px solid {theme.CARD_BORDER}; border-radius: {rem(0.5)}px; }}")
        lst = QVBoxLayout(holder)
        lst.setContentsMargins(rem(0.4), rem(0.4), rem(0.4), rem(0.4))
        lst.setSpacing(rem(0.25))
        area = scroll_area(holder)
        area.setMinimumHeight(rem(10))
        area.setMaximumHeight(rem(16))
        m.body.addWidget(area)
        state = {"list": [], "chosen": None}
        group = QButtonGroup(m)
        manage = rights.can_manage_templates()

        def render():
            clear_layout(lst)
            for b in group.buttons():
                group.removeButton(b)
            if not state["list"]:
                lst.addWidget(muted("Шаблонов пока нет — создайте первый", 0.85))
            for t in state["list"]:
                row = QHBoxLayout()
                rb = QRadioButton()
                rb.setStyleSheet(checkbox_qss().replace("QCheckBox", "QRadioButton"))
                group.addButton(rb)
                rb.toggled.connect(lambda on, tt=t: state.__setitem__("chosen", tt) if on else None)
                row.addWidget(rb)
                txt = label("", 0.875, theme.TEXT_VALUE)
                txt.setTextFormat(Qt.RichText)
                txt.setText("%s <span style='color:%s'> — этапов: %s (%s)</span>" % (
                    t.get("template_name"), theme.TEXT_MUTED, t.get("num_stages"), t.get("departments")))
                row.addWidget(txt, 1)
                eb = Button("", "ghost", "edit", "Изменить шаблон")
                eb.clicked.connect(lambda _=False, tt=t: template_form(tt))
                gate(eb, manage, TEMPLATES_LOCKED)
                db = Button("", "danger", "trash", "Удалить шаблон")
                db.clicked.connect(lambda _=False, tt=t: delete(tt))
                gate(db, manage, TEMPLATES_LOCKED)
                row.addWidget(eb)
                row.addWidget(db)
                w = QWidget()
                w.setLayout(row)
                row.setContentsMargins(0, 0, 0, 0)
                lst.addWidget(w)
            lst.addStretch(1)

        def reload():
            def done(lst_):
                state["list"] = lst_ or []
                state["chosen"] = None
                render()
            jobs.run(client.get, "templates", done=done, failed=lambda e: done([]), owner=m)

        def delete(t):
            if not confirm(m, "Удаление шаблона", "Удалить шаблон «%s»?" % t.get("template_name"), "Удалить", danger=True):
                return
            jobs.run(client.delete, "templates/%s" % t.get("id"), done=lambda _r: reload(),
                     failed=lambda e: toast(m, "Не удалось удалить: " + human_error(e), "error"), owner=m)

        def template_form(tpl):
            f = Modal(m, "Изменение шаблона" if tpl else "Новый шаблон", "file", width_rem=34)
            name = line_edit("Название шаблона", (tpl or {}).get("template_name") or "")
            start = [x.strip() for x in str((tpl or {}).get("departments") or "").split(",") if x.strip()] if tpl \
                else [self.departments[0]]
            cnt = spin(max(1, len(start)), 10)
            f.body.addWidget(field("Название", name, True))
            f.body.addWidget(field("Количество этапов", cnt))
            box = QVBoxLayout()
            box.setSpacing(rem(0.4))
            f.body.addLayout(box)
            sels = []

            def rebuild(deps=None):
                n = min(10, max(1, cnt.value()))
                cur = [s.value() for s in sels]
                clear_layout(box)
                sels.clear()
                for i in range(1, n + 1):
                    v = (deps[i - 1] if deps and i - 1 < len(deps) else None) or (cur[i - 1] if i - 1 < len(cur) else None) \
                        or self.departments[0]
                    sel = Select([(d, d) for d in self.departments])
                    sel.set_value(v if v in self.departments else self.departments[0])
                    row = QHBoxLayout()
                    row.addWidget(Chip("Этап %d" % i))
                    row.addWidget(sel, 1)
                    box.addLayout(row)
                    sels.append(sel)
            cnt.valueChanged.connect(lambda _v: rebuild())
            rebuild(start)

            def save(_f):
                title = name.text().strip()
                if not title:
                    toast(f, "Укажите название шаблона", "warning")
                    return False
                deps = [s.value() for s in sels]
                payload = {"template_name": title, "num_stages": len(deps), "departments": ", ".join(deps)}
                try:
                    if tpl:
                        client.put("templates/%s" % tpl.get("id"), json_body=payload)
                    else:
                        client.post("templates", json_body=payload)
                except Exception as e:
                    toast(f, "Не удалось сохранить: " + human_error(e), "error")
                    return False
                toast(m, "Шаблон сохранён", "success")
                reload()
                return True
            f.add_button("Отмена", "ghost")
            f.add_button("Сохранить", "primary", "save", save)
            f.exec()

        def create(_m):
            if not manage:
                toast(m, TEMPLATES_LOCKED, "info")
                return False
            template_form(None)
            return False

        def from_current(_m):
            if not manage:
                toast(m, TEMPLATES_LOCKED, "info")
                return False
            deps = self.stage_deps()
            name = self.name.text().strip() or "Шаблон " + QDate.currentDate().toString("dd.MM.yyyy")
            try:
                client.post("templates", json_body={"template_name": name, "num_stages": len(deps),
                                                    "departments": ", ".join(deps)})
                toast(m, "Шаблон сохранён", "success")
                reload()
            except Exception as e:
                toast(m, "Не удалось сохранить шаблон: " + human_error(e), "error")
            return False

        def apply(_m):
            t = state["chosen"]
            if not t:
                toast(m, "Выберите шаблон", "warning")
                return False
            self.name.setText(t.get("template_name") or "")
            deps = [x.strip() for x in str(t.get("departments") or "").split(",") if x.strip()]
            wanted = min(10, max(1, int(t.get("num_stages") or len(deps) or 1)))
            self.count.blockSignals(True)
            self.count.setValue(min(wanted, self.max_stages))
            self.count.blockSignals(False)
            self.rebuild_stages(deps)
            if wanted > self.max_stages or any(d not in self.departments for d in deps):
                toast(self, "Шаблон применён частично: руководитель отдела заводит задачу с одним этапом своего отдела",
                      "warning")
            return True
        b1 = m.add_button("Создать шаблон", "ghost", "plus", create)
        b2 = m.add_button("Из текущей задачи", "ghost", "save", from_current)
        m.add_button("Применить", "primary", "check", apply)
        if not manage:
            b1.set_locked(True, TEMPLATES_LOCKED)
            b2.set_locked(True, TEMPLATES_LOCKED)
        lst.addWidget(loading("Загружаем…"))
        reload()
        m.exec()

    # ── сохранение ───────────────────────────────────────────────────────────

    def _fill_participants(self, saved, only=None):
        for st in (saved or {}).get("stage_details") or []:
            if only is not None and st.get("id") not in only:
                continue
            ids = [e.get("id") for e in self.employees if e.get("department") == st.get("department_name")]
            if not ids:
                continue
            try:
                client.put("stages/%s/participants" % st.get("id"), json_body=ids)
            except Exception:
                pass

    def save(self):
        title = self.name.text().strip()
        if not title:
            toast(self, "Укажите название задачи", "warning")
            return
        d = self.deadline.value()
        if d is None:
            toast(self, "Укажите дату окончания — без неё задачу сохранить нельзя", "warning")
            self.deadline.setFocus()
            return
        if not self.task and d < QDate.currentDate():
            toast(self, "Дата окончания не может быть раньше сегодняшнего дня", "warning")
            return
        deps = self.stage_deps()
        payload = {"name": title, "description": self.description.toPlainText().strip() or None,
                   "deadline": self.deadline.iso(), "customer_organization": self.locked_org or self.org.value(),
                   "responsible_persons_ids": sorted(self.selected["responsible"]),
                   "assigned_employees_ids": sorted(self.selected["assigned"])}
        task = self.task
        was = [p.get("id") for p in ((task or {}).get("responsible_persons") or []) + ((task or {}).get("assigned_employees") or [])]
        people = list(self.selected["responsible"]) + list(self.selected["assigned"]) + was + [client.user_id]

        def work():
            if task:
                payload["stages"] = [{"stage_number": s.get("stage_number"),
                                      "department_name": (deps[i] if i < len(deps) else None) or s.get("department_name")}
                                     for i, s in enumerate(self.existing)]
                moved = set(s.get("id") for i, s in enumerate(self.existing)
                            if i < len(deps) and deps[i] and deps[i] != s.get("department_name"))
                client.put("tasks/%d" % task["id"], json_body=payload)
                if moved:
                    try:
                        self._fill_participants(client.get("tasks/%d" % task["id"]), moved)
                    except Exception:
                        pass
                notify.send(people, "Задача «%s» изменена" % title, "task_view", task["id"],
                            "Вы изменили задачу «%s»" % title)
                return task["id"], False
            if self.preset:
                payload["project_id"] = self.preset.get("id")
            payload["stages"] = [{"department_name": dd, "text_description": ""} for dd in deps]
            created = client.post("tasks", json_body=payload)
            self._fill_participants(created)
            notify.send(people, "Вам назначена задача «%s»" % title, "task_view", created["id"],
                        "Вы создали задачу «%s»" % title)
            return created["id"], True

        def done(res):
            tid, new = res
            toast(self, "Задача создана" if new else "Задача сохранена", "success")
            self.shell.replace("task", task_id=tid)

        def failed(e):
            self.save_btn.setEnabled(True)
            toast(self, "Не удалось сохранить: " + human_error(e), "error")
        self.save_btn.setEnabled(False)
        jobs.run(work, done=done, failed=failed, owner=self)
