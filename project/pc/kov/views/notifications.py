# -*- coding: utf-8 -*-
"""Раздел «Уведомления» — как js/views/notifications.js веб-версии.

Сверху — то, что ждёт подтверждения сотрудника: выдачи со склада проектов
и оборудование (выдача, возврат, приёмка). Ниже — список уведомлений
сервера (непрочитанные сверху, с синей полосой слева), внизу — «Отметить
все как прочитанные» и «Создать уведомление» (руководство и руководители
отделов). Нажатие на уведомление отмечает его прочитанным и открывает
раздел, о котором оно.
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QSizePolicy, QVBoxLayout,
                             QWidget)

from general_functions import theme
from kov import icons, jobs
from kov.api import ApiError, client, human_error
from kov.dialogs import Modal, confirm, field
from kov.fmt import fmt_datetime, parse_date
from kov.notify import send
from kov.people import avatar, full_name
from kov.store import store
from kov.ui import (Button, Chip, SearchInput, Segmented, border, checkbox_qss, clear_layout, font, fs, label, loading,
                    muted, rem, scroll_area, text_qss, toast)
from kov.views.base import Section

SECTION_GLYPH = {"task_view": "flag", "warehouse": "archive", "equipment": "archive", "project": "project",
                 "task_complete": "check"}
LIST_PATHS = [lambda me: "employees/%s/notifications" % me, lambda me: "notifications/employee/%s" % me,
              lambda me: "notifications/my", lambda me: "notifications"]
_PATH = {"ok": None}


def as_list(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("items", "notifications", "results", "data"):
            if isinstance(data.get(key), list):
                return data[key]
    return None


def fetch_list():
    """Основной адрес и запасные: старый сервер мог отдавать список по другому пути."""
    me = client.user_id
    order = ([_PATH["ok"]] + [f for f in LIST_PATHS if f is not _PATH["ok"]]) if _PATH["ok"] else LIST_PATHS
    first = None
    for make in order:
        try:
            lst = as_list(client.get(make(me)))
            if lst is not None:
                _PATH["ok"] = make
                return lst
        except ApiError as e:
            first = first or e
            if e.status not in (404, 405):
                raise
    raise first or RuntimeError("Список уведомлений недоступен")


def sort_items(items):
    def key(n):
        d = parse_date(n.get("created_at"))
        return (1 if n.get("is_read") else 0, -(d.timestamp() if d else 0))
    return sorted(items, key=key)


def target_route(n):
    """Куда ведёт уведомление: (маршрут, параметры) или None."""
    tid = n.get("target_id")
    sec = n.get("target_section")
    if not tid:
        return ("equipment", {}) if sec == "equipment" else None
    if sec in ("task_view", "task_complete"):
        return "task", {"task_id": int(tid)}
    if sec == "warehouse":
        return "warehouse", {"project_id": int(tid)}
    if sec == "equipment":
        return "equipment", {}
    if sec == "project":
        return "project", {"project_id": int(tid)}
    return None


def pending_card(title, sub, mine, glyph, buttons, project_line=None, on_project=None):
    """Карточка «ждёт подтверждения» (.wh-pending-card): кружок, что и от кого, кнопки."""
    card = QFrame()
    card.setObjectName("whPending")
    bd, bg = (theme.INFO, theme.INFO_SOFT) if mine else (theme.WARNING, theme.WARNING_SOFT)
    card.setStyleSheet(f"QFrame#whPending {{ background: {bg}; border: {border()}px solid {bd};"
                       f" border-left: {rem(0.25)}px solid {bd}; border-radius: {rem(0.5)}px; }}")
    lay = QHBoxLayout(card)
    lay.setContentsMargins(rem(0.7), rem(0.55), rem(0.7), rem(0.55))
    lay.setSpacing(rem(0.7))
    circ = QLabel()
    d = rem(2.0)
    circ.setFixedSize(d, d)
    circ.setAlignment(Qt.AlignCenter)
    circ.setStyleSheet(f"background: {theme.CARD_BG}; border-radius: {d // 2}px; border: none;")
    circ.setPixmap(icons.pixmap(glyph, rem(1.05), theme.ACCENT))
    lay.addWidget(circ)
    who = QVBoxLayout()
    who.setSpacing(0)
    who.addWidget(label(title, 0.86, theme.TEXT_VALUE, bold=True))
    who.addWidget(label(sub, 0.74, theme.TEXT_LABEL))
    if project_line:
        link = QLabel("<a href='#' style='color:%s; text-decoration:none'>%s</a>" % (theme.TEXT_MUTED, project_line))
        link.setFont(font(0.8))
        link.setStyleSheet("background: transparent;")
        link.setCursor(Qt.PointingHandCursor)
        if on_project:
            link.linkActivated.connect(lambda _h: on_project())
        who.addWidget(link, 0, Qt.AlignLeft)
    lay.addLayout(who, 1)
    for text, kind, gl, handler in buttons:
        b = Button(text, kind, gl)
        b.clicked.connect(handler)
        lay.addWidget(b)
    return card


class _PendingPanel(QFrame):
    """«Склад / Оборудование: ждут вашего подтверждения» — рамка с заголовком и счётчиком."""

    def __init__(self, view, title):
        super().__init__()
        self.view = view
        self.setObjectName("whGlobal")
        self.setStyleSheet(f"QFrame#whGlobal {{ border: {border()}px solid {theme.CARD_BORDER}; border-radius: {rem(0.75)}px;"
                           f" background: {theme.CARD_BG}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(rem(0.7), rem(0.6), rem(0.7), rem(0.6))
        lay.setSpacing(rem(0.5))
        head = QHBoxLayout()
        head.setSpacing(rem(0.5))
        ic = QLabel()
        ic.setPixmap(icons.pixmap("archive", rem(1.05), theme.ACCENT))
        head.addWidget(ic)
        t = label(title, 0.9, theme.TEXT_VALUE, bold=True)
        head.addWidget(t)
        head.addStretch(1)
        self.count = QLabel("")
        self.count.setFont(font(0.72, bold=True))
        self.count.setAlignment(Qt.AlignCenter)
        h = rem(1.3)
        self.count.setMinimumSize(h, h)
        self.count.setStyleSheet(f"background: {theme.WARNING}; color: #FFFFFF; border-radius: {h // 2}px;"
                                 f" padding: 0 {rem(0.35)}px;")
        head.addWidget(self.count)
        lay.addLayout(head)
        self.list = QVBoxLayout()
        self.list.setSpacing(rem(0.4))
        lay.addLayout(self.list)
        self.hide()

    def fill(self, cards):
        clear_layout(self.list)
        for c in cards:
            self.list.addWidget(c)
        self.count.setText(str(len(cards)) if cards else "")
        self.setVisible(bool(cards))


def num(v):
    v = round(float(v or 0) * 1000) / 1000
    return ("%d" % v) if v == int(v) else ("%g" % v)


class NotificationsSection(Section):
    title = "Уведомления"
    key = "notifications"

    def __init__(self, shell, parent=None):
        super().__init__(shell, parent)
        self.items = []
        self.error = None
        self.known = None
        self.loader = loading("Загрузка…")
        self.root.addWidget(self.loader)
        self.wh = _PendingPanel(self, "Склад: ждут вашего подтверждения")
        self.eq = _PendingPanel(self, "Оборудование: ждут вашего подтверждения")
        self.root.addWidget(self.wh)
        self.root.addWidget(self.eq)
        self.holder = QWidget()
        self.holder.setStyleSheet("background: transparent;")
        self.list = QVBoxLayout(self.holder)
        self.list.setContentsMargins(0, 0, rem(0.2), 0)
        self.list.setSpacing(rem(0.5))
        self.area = scroll_area(self.holder)
        self.area.hide()
        self.root.addWidget(self.area, 1)
        bar = QFrame()
        bar.setObjectName("notifBar")
        bar.setStyleSheet(f"QFrame#notifBar {{ border: none; border-top: {border()}px solid {theme.CARD_BORDER};"
                          f" background: {theme.PAGE_BG}; }}")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(0, rem(0.6), 0, 0)
        bl.setSpacing(rem(0.5))
        mark = Button("Отметить все как прочитанные", "ghost", "check")
        mark.clicked.connect(self.mark_all)
        bl.addWidget(mark)
        bl.addStretch(1)
        if store.can_manage_work():
            create = Button("Создать уведомление", "primary", "plus")
            create.clicked.connect(lambda: create_dialog(self))
            bl.addWidget(create)
        self.bar = bar
        bar.hide()
        self.root.addWidget(bar)
        self._soft = QTimer(self)
        self._soft.setSingleShot(True)
        self._soft.timeout.connect(self.refresh)
        self._poll = QTimer(self)
        self._poll.setInterval(60000)
        self._poll.timeout.connect(self.refresh)
        self._poll.start()
        self.refresh(first=True)

    # ── данные ───────────────────────────────────────────────────────────────

    def refresh(self, first=False):
        def load():
            try:
                items = sort_items(fetch_list())
                err = None
            except Exception as e:
                items, err = None, e
            try:
                wh = (client.get("warehouse/issues/pending") or {}).get("items") or []
            except Exception:
                wh = []
            try:
                eq = (client.get("equipment/issues/pending") or {}).get("items") or []
            except Exception:
                eq = []
            return items, err, wh, eq

        def done(res):
            items, err, wh, eq = res
            if items is not None:
                self.items = items
            self.error = err
            if first:
                self.loader.hide()
                self.area.show()
                self.bar.show()
            self.render()
            self.render_pending(wh, eq)
            self.shell.bell.set_badge(sum(1 for n in self.items if not n.get("is_read")))
        jobs.run(load, done=done, owner=self)

    def render_pending(self, wh, eq):
        cards = []
        for p in wh:
            mine = p.get("mine")
            who = ("от " + ((p.get("issuer") or {}).get("name") or "склада")) if mine else \
                ("для " + ((p.get("receiver") or {}).get("name") or "сотрудника"))
            btns = []
            if mine:
                btns.append(("Подтвердить", "primary", "check", lambda _=False, pp=p: self.wh_act("confirm", pp)))
            btns.append(("Отказаться" if mine else "Отменить", "ghost", "close", lambda _=False, pp=p: self.wh_act("cancel", pp)))
            pid = p.get("project_id")
            cards.append(pending_card(p.get("name") or ("Строка %s" % p.get("row_no")),
                                      "%s %s · %s" % (num(p.get("quantity")), p.get("unit") or "", who), mine,
                                      "download" if mine else "clock", btns,
                                      "Проект: " + (p.get("project") or "№%s" % pid),
                                      lambda pid=pid: self.shell.go("warehouse", project_id=pid)))
        self.wh.fill(cards)
        cards = []
        titles = {"receive": ("download", lambda p: "от " + ((p.get("issuer") or {}).get("name") or "склада")),
                  "return": ("undo", lambda p: "возвращает " + ((p.get("receiver") or {}).get("name") or "сотрудник")),
                  "sent": ("clock", lambda p: "для " + ((p.get("receiver") or {}).get("name") or "сотрудника"))}
        for p in eq:
            glyph, say = titles.get(p.get("kind"), titles["sent"])
            if p.get("kind") == "receive":
                btns = [("Подтвердить", "primary", "check", lambda _=False, pp=p: self.eq_act(pp, "confirm", "Получение подтверждено")),
                        ("Отказаться", "ghost", "close", lambda _=False, pp=p: self.eq_act(pp, "decline", "Выдача отклонена"))]
            elif p.get("kind") == "return":
                btns = [("Принять возврат", "primary", "check", lambda _=False, pp=p: self.eq_act(pp, "return/accept", "Возврат принят")),
                        ("Отклонить", "ghost", "close", lambda _=False, pp=p: self.eq_act(pp, "return/decline", "Возврат отклонён"))]
            else:
                btns = [("Отменить", "ghost", "close", lambda _=False, pp=p: self.eq_act(pp, "decline", "Выдача отменена"))]
            unit = (" " + p["unit"]) if p.get("unit") else ""
            cards.append(pending_card(p.get("name") or "", "%s%s · %s" % (num(p.get("quantity")), unit, say(p)),
                                      p.get("kind") == "receive", glyph, btns, "Раздел «Оборудование»",
                                      lambda: self.shell.go("equipment")))
        self.eq.fill(cards)

    def wh_act(self, what, p):
        if what == "cancel" and not confirm(self, "Отмена выдачи", "Отменить выдачу «%s» до подтверждения?" % (p.get("name") or ""),
                                            "Отменить выдачу", danger=True):
            return
        jobs.run(client.post, "warehouse/issues/%s/%s" % (p["id"], what), json_body={},
                 done=lambda _: (toast(self, "Получение подтверждено" if what == "confirm" else "Выдача отменена", "success"),
                                 self.refresh()),
                 failed=lambda e: toast(self, human_error(e), "error"), owner=self)

    def eq_act(self, p, path, ok):
        jobs.run(client.post, "equipment/issues/%s/%s" % (p["id"], path),
                 done=lambda _: (toast(self, ok, "success"), self.refresh()),
                 failed=lambda e: toast(self, human_error(e), "error"), owner=self)

    # ── список ───────────────────────────────────────────────────────────────

    def render(self):
        clear_layout(self.list)
        if self.error is not None and not self.items:
            self.list.addWidget(self.empty_state("warning", "Список не загрузился",
                                                 human_error(self.error) + (" (HTTP %s)" % self.error.status
                                                                            if getattr(self.error, "status", None) else "")))
            self.list.addStretch(1)
            return
        if not self.items:
            self.list.addWidget(self.empty_state("bell", "Уведомлений пока нет",
                                                 "Здесь появляются сообщения о назначении на задачи, изменениях, завершении и "
                                                 "возобновлении этапов и переводе задач в архив."))
            self.list.addStretch(1)
            return
        for n in self.items:
            self.list.addWidget(self.item(n))
        self.list.addStretch(1)

    def empty_state(self, glyph, title, text):
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(rem(1.5), rem(3), rem(1.5), rem(3))
        lay.setSpacing(rem(0.5))
        circ = QLabel()
        d = rem(4.5)
        circ.setFixedSize(d, d)
        circ.setAlignment(Qt.AlignCenter)
        circ.setStyleSheet(f"background: {theme.CHIP_BG}; border-radius: {d // 2}px;")
        circ.setPixmap(icons.pixmap(glyph, rem(2.2), theme.ACCENT_SOFT))
        lay.addWidget(circ, 0, Qt.AlignHCenter)
        lay.addWidget(label(title, 1.05, theme.BTN_GHOST_TEXT, bold=True, align=Qt.AlignHCenter))
        t = label(text, 0.85, theme.TEXT_LABEL, wrap=True, align=Qt.AlignHCenter)
        t.setMaximumWidth(rem(28))
        lay.addWidget(t, 0, Qt.AlignHCenter)
        rb = Button("Обновить", "ghost", "refresh")
        rb.clicked.connect(self.refresh)
        lay.addWidget(rb, 0, Qt.AlignHCenter)
        return w

    def item(self, n):
        unread = not n.get("is_read")
        el = QFrame()
        el.setObjectName("notifItem")
        el.setCursor(Qt.PointingHandCursor)
        b = border()
        el.setStyleSheet(f"QFrame#notifItem {{ background: {theme.SURFACE_3 if unread else theme.CARD_BG};"
                         f" border: {b}px solid {theme.CARD_BORDER}; border-left: {rem(0.3)}px solid {theme.INFO if unread else theme.CARD_BORDER};"
                         f" border-radius: {rem(0.75)}px; }}")
        lay = QHBoxLayout(el)
        lay.setContentsMargins(rem(0.9), rem(0.7), rem(0.9), rem(0.7))
        lay.setSpacing(rem(0.7))
        ic = QLabel()
        ic.setPixmap(icons.pixmap(SECTION_GLYPH.get(n.get("target_section"), "bell"), rem(1.1), theme.ACCENT))
        ic.setStyleSheet("background: transparent; border: none;")
        lay.addWidget(ic, 0, Qt.AlignTop)
        tx = QVBoxLayout()
        tx.setSpacing(0)
        msg = label(n.get("message") or "", 0.875, theme.TEXT_VALUE, wrap=True)
        tx.addWidget(msg)
        tx.addWidget(label(fmt_datetime(n.get("created_at")), 0.75, theme.TEXT_LABEL))
        acts = self.completion_actions(n)
        if acts is not None:
            tx.addWidget(acts)
        lay.addLayout(tx, 1)
        el.mouseReleaseEvent = lambda e, nn=n: e.button() == Qt.LeftButton and self.open(nn)
        return el

    def completion_actions(self, n):
        if n.get("target_section") != "task_complete" or not store.is_management():
            return None
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, rem(0.45), 0, 0)
        lay.setSpacing(rem(0.4))
        btns = []

        def run(path, ok):
            for x in btns:
                x.setEnabled(False)

            def done(_):
                toast(self, ok, "success")
                clear_layout(lay)
                ic = QLabel()
                ic.setPixmap(icons.pixmap("check", rem(0.9), theme.SUCCESS_TEXT))
                lay.addWidget(ic)
                lay.addWidget(label(ok, 0.78, theme.SUCCESS_TEXT))
                lay.addStretch(1)
                QTimer.singleShot(600, self.refresh)

            def failed(e):
                toast(self, human_error(e), "error")
                for x in btns:
                    x.setEnabled(True)
            jobs.run(client.post, "tasks/%s/completion/%s" % (n.get("target_id"), path), done=done, failed=failed, owner=self)
        for text, kind, glyph, path, ok in (("Подтвердить", "primary", "check", "approve", "Задача завершена"),
                                            ("Отклонить", "ghost", "close", "decline", "Завершение отклонено")):
            bt = Button(text, kind, glyph)
            bt.setFont(font(0.78, bold=True))
            bt.setStyleSheet(bt.styleSheet() + f"QPushButton {{ min-height: {rem(1.9) - 2}px; max-height: {rem(1.9) - 2}px;"
                                               f" padding: 0 {rem(0.7)}px; font-size: {fs(0.78)}; }}")
            bt.clicked.connect(lambda _=False, p=path, o=ok: run(p, o))
            btns.append(bt)
            lay.addWidget(bt)
        lay.addStretch(1)
        return row

    def open(self, n):
        def go():
            target = target_route(n)
            if target:
                self.shell.go(target[0], **target[1])
            else:
                self.render()
        if not n.get("is_read"):
            def done(_):
                n["is_read"] = True
                self.shell.bell.set_badge(sum(1 for x in self.items if not x.get("is_read")))
                go()
            jobs.run(client.post, "notifications/mark-as-read", json_body=[n["id"]], done=done,
                     failed=lambda e: go(), owner=self)
        else:
            go()

    def mark_all(self):
        ids = [n["id"] for n in self.items if not n.get("is_read")]
        if not ids:
            toast(self, "Непрочитанных нет", "info")
            return

        def done(_):
            for n in self.items:
                n["is_read"] = True
            self.render()
            self.shell.bell.set_badge(0)
        jobs.run(client.post, "notifications/mark-as-read", json_body=ids, done=done,
                 failed=lambda e: toast(self, "Не удалось отметить: " + human_error(e), "error"), owner=self)

    def on_event(self, evt):
        kind = str(evt.get("type") or "")
        if kind.startswith("notification") or kind in ("warehouse.issue",) or kind.startswith("equipment."):
            self._soft.start(300)

    def on_reconnect(self):
        self._soft.start(300)


# ══════════════════════════════════════════════════════════════════════════
# Окно «Создать уведомление»
# ══════════════════════════════════════════════════════════════════════════

def create_dialog(view):
    """Два режима выбора получателей: «По отделам» и «Сотрудникам»; ниже — текст."""
    state = {"mode": "dept", "people": []}
    selected = set()
    m = Modal(view, "Создать уведомление", "bell", width_rem=52)
    top = QHBoxLayout()
    top.setSpacing(rem(0.5))
    switch = Segmented([("dept", "По отделам", "building"), ("emp", "Сотрудникам", "users")], "dept")
    top.addWidget(switch)
    top.addStretch(1)
    counter = label("Выбрано: 0", 0.8, theme.TEXT_MUTED)
    top.addWidget(counter)
    clear_btn = Button("Снять выбор", "ghost", "close", "Убрать всех получателей")
    top.addWidget(clear_btn)
    m.body.addLayout(top)
    wrap = QWidget()
    wrap.setFixedHeight(rem(14.5))
    wrap.setStyleSheet("background: transparent;")
    wl = QVBoxLayout(wrap)
    wl.setContentsMargins(0, 0, 0, 0)
    wl.setSpacing(rem(0.5))
    search = SearchInput("Поиск по ФИО, отделу, должности…")
    wl.addWidget(search)

    def box():
        holder = QFrame()
        holder.setObjectName("checklist")
        holder.setStyleSheet(f"QFrame#checklist {{ border: {border()}px solid {theme.CARD_BORDER}; border-radius: {rem(0.5)}px;"
                             f" background: {theme.CARD_BG}; }}")
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(rem(0.4), rem(0.4), rem(0.4), rem(0.4))
        lay.setSpacing(rem(0.15))
        return holder, lay
    dept_holder, dept_lay = box()
    emp_holder, emp_lay = box()
    dept_area = scroll_area(dept_holder)
    emp_area = scroll_area(emp_holder)
    wl.addWidget(dept_area, 1)
    wl.addWidget(emp_area, 1)
    m.body.addWidget(wrap)
    message = QPlainTextEdit()
    message.setPlaceholderText("Текст уведомления…")
    message.setFont(font(0.875))
    message.setStyleSheet(text_qss())
    message.setMinimumHeight(rem(8.5))
    m.body.addWidget(field("Текст уведомления", message, True))

    def update_counter():
        n = len(selected)
        counter.setText("Выбрано: %d" % n + (" из %d" % len(state["people"]) if n else ""))
        clear_btn.setEnabled(bool(n))

    def row_widget(cb, text_widget, chip=None, bold=False, line=False):
        w = QFrame()
        w.setObjectName("chkRow")
        w.setStyleSheet(f"QFrame#chkRow {{ border: none; {'border-bottom: %dpx solid %s;' % (border(), theme.CARD_BORDER) if line else ''}"
                        f" border-radius: {0 if line else rem(0.35)}px; }}"
                        f"QFrame#chkRow:hover {{ background: {theme.CHIP_BG}; }}")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(rem(0.4), rem(0.35), rem(0.4), rem(0.35 if not line else 0.5))
        lay.setSpacing(rem(0.5))
        lay.addWidget(cb)
        lay.addWidget(text_widget, 1)
        if chip is not None:
            lay.addWidget(chip)
        return w

    def check(on, part=False):
        cb = QCheckBox()
        cb.setStyleSheet(checkbox_qss())
        cb.setTristate(False)
        if part:
            cb.setCheckState(Qt.PartiallyChecked)
        else:
            cb.setChecked(on)
        return cb

    def render_departments():
        clear_layout(dept_lay)
        people = state["people"]
        everyone = bool(people) and all(e["id"] in selected for e in people)
        cb = check(everyone, part=not everyone and bool(selected))
        cb.clicked.connect(lambda _c=False: (([selected.add(e["id"]) for e in people] if not everyone
                                              else [selected.discard(e["id"]) for e in people]), render_all()))
        dept_lay.addWidget(row_widget(cb, label("Всем отделам", 0.875, theme.TEXT_VALUE, bold=True),
                                      Chip("сотрудников: %d" % len(people), "info" if everyone else "default"), line=True))
        names = sorted(set(e.get("department") for e in people if e.get("department")), key=str.lower)
        if not names:
            dept_lay.addWidget(muted("Отделы не найдены", 0.85))
        for d in names:
            staff = [e for e in people if e.get("department") == d]
            picked = sum(1 for e in staff if e["id"] in selected)
            c = check(picked == len(staff), part=0 < picked < len(staff))
            full = picked == len(staff)
            c.clicked.connect(lambda _c=False, st=staff, f=full: (([selected.discard(e["id"]) for e in st] if f
                                                                   else [selected.add(e["id"]) for e in st]), render_all()))
            chip = Chip(("%d из %d" % (picked, len(staff))) if picked else "сотрудников: %d" % len(staff),
                        "info" if picked else "default")
            dept_lay.addWidget(row_widget(c, label(d, 0.875, theme.TEXT_VALUE), chip))
        dept_lay.addStretch(1)

    def render_employees():
        clear_layout(emp_lay)
        needle = search.text().strip().lower()
        rows = [e for e in state["people"] if not needle or needle in (
            full_name(e, True) + " " + (e.get("department") or "") + " " + (e.get("position") or "")).lower()]
        for e in rows:
            c = check(e["id"] in selected)

            def toggled(on, eid=e["id"]):
                (selected.add if on else selected.discard)(eid)
                update_counter()
                render_departments()
            c.toggled.connect(toggled)
            me = " (вы)" if e["id"] == client.user_id else ""
            sub = ", ".join(x for x in (e.get("department"), e.get("position")) if x)
            t = QLabel("%s%s <span style='color:%s'> — %s</span>" % (full_name(e, True), me, theme.TEXT_MUTED, sub))
            t.setFont(font(0.875))
            t.setStyleSheet(f"color: {theme.TEXT_VALUE}; background: transparent;")
            rw = row_widget(c, t)
            rw.layout().insertWidget(1, avatar(e, 1.7))
            emp_lay.addWidget(rw)
        if not rows:
            emp_lay.addWidget(muted("Никого не найдено", 0.85))
        emp_lay.addStretch(1)

    def render_all():
        render_departments()
        render_employees()
        update_counter()

    def apply_mode(key):
        state["mode"] = key
        dept_area.setVisible(key == "dept")
        search.setVisible(key == "emp")
        emp_area.setVisible(key == "emp")
        render_all()
    switch.changed.connect(apply_mode)
    search.textChanged.connect(lambda _t: render_employees())
    clear_btn.clicked.connect(lambda: (selected.clear(), render_all()))

    def loaded(lst):
        state["people"] = [e for e in (lst or []) if isinstance(e, dict) and e.get("id") is not None]
        render_all()
    apply_mode("dept")
    jobs.run(lambda: (store.load_photo_ids(), store.employees())[1], done=loaded, failed=lambda e: loaded([]), owner=m)

    def go(_m):
        text = message.toPlainText().strip()
        if not selected:
            toast(m, "Выберите получателей", "warning")
            return False
        if not text:
            toast(m, "Введите текст уведомления", "warning")
            return False
        try:
            send(sorted(selected), text, "notifications", None)
        except Exception as e:
            toast(m, "Уведомление не отправлено: " + human_error(e), "error")
            return False
        toast(view, "Уведомление отправлено получателям: %d" % len(selected), "success")
        if client.user_id in selected:
            QTimer.singleShot(300, view.refresh)
        return True
    m.add_button("Отмена", "ghost")
    m.add_button("Отправить", "primary", "mail", go)
    m.exec()
