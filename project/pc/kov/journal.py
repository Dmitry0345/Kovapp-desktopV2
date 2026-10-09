# -*- coding: utf-8 -*-
"""Единое окно журнала действий — как js/core/journal.js: разделы «Выдача»
и «Редактирование», поиск по сотруднику (и автору, и тому, кого запись
касается), число записей. Для задач, склада и оборудования."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from general_functions import theme
from kov import icons, jobs
from kov.api import human_error
from kov.dialogs import Modal
from kov.fmt import fmt_datetime
from kov.ui import SearchInput, border, clear_layout, font, label, loading, rem, scroll_area

GLYPH = {"issued": "send", "confirmed": "check", "declined": "close", "cancelled": "close",
         "return_requested": "undo", "returned": "check", "return_declined": "close",
         "pending": "clock", "edit": "edit", "created": "plus", "changed": "edit"}
TONE = {"confirmed": "ok", "returned": "ok", "issued": "wait", "pending": "wait",
        "return_requested": "wait", "cancelled": "bad", "declined": "bad", "return_declined": "bad"}


def name_of(person):
    if not person:
        return ""
    if isinstance(person, str):
        return person
    return person.get("name") or " ".join(x for x in (person.get("last_name"), person.get("first_name")) if x)


def _matches(entry, needle):
    if not needle:
        return True
    hay = " ".join(name_of(x) for x in (entry.get("who"), entry.get("target")) if x).lower() + " " + \
        str(entry.get("title") or "").lower() + " " + str(entry.get("text") or "").lower()
    return needle in hay


def _tone_colors(tone):
    return {"ok": (theme.SUCCESS_SOFT, theme.SUCCESS_TEXT), "wait": (theme.WARNING_SOFT, theme.WARNING_TEXT),
            "bad": (theme.CRITICAL_SOFT, theme.CRITICAL_TEXT)}.get(tone, (theme.INFO_SOFT, theme.INFO))


def _row(entry):
    action = entry.get("action")
    tone = TONE.get(action) or ("wait" if entry.get("kind") == "issue" else "edit")
    bg, fg = _tone_colors(tone)
    w = QFrame()
    w.setObjectName("jrRow")
    w.setStyleSheet(f"QFrame#jrRow {{ background: {theme.SURFACE_2}; border: {border()}px solid {theme.CARD_BORDER};"
                    f" border-radius: {rem(0.5)}px; }}")
    lay = QHBoxLayout(w)
    lay.setContentsMargins(rem(0.6), rem(0.5), rem(0.6), rem(0.5))
    lay.setSpacing(rem(0.6))
    circ = QLabel()
    d = rem(1.9)
    circ.setFixedSize(d, d)
    circ.setAlignment(Qt.AlignCenter)
    circ.setStyleSheet(f"background: {bg}; border-radius: {d // 2}px;")
    glyph = entry.get("glyph") or GLYPH.get(action) or ("send" if entry.get("kind") == "issue" else "edit")
    circ.setPixmap(icons.pixmap(glyph, rem(0.95), fg))
    lay.addWidget(circ, 0, Qt.AlignTop)
    body = QVBoxLayout()
    body.setSpacing(rem(0.1))
    body.addWidget(label(entry.get("title") or "", 0.88, theme.TEXT_VALUE, bold=True, wrap=True))
    if entry.get("text"):
        body.addWidget(label(str(entry["text"]), 0.82, theme.TEXT_VALUE, wrap=True))
    when = " · ".join(x for x in (name_of(entry.get("who")), fmt_datetime(entry.get("at"), "") if entry.get("at") else "") if x)
    body.addWidget(label(when, 0.74, theme.TEXT_MUTED))
    lay.addLayout(body, 1)
    return w


def open_journal(owner, title, subtitle, load, always_issues=False):
    """load() → {'issues': [...], 'edits': [...]} — вызывается в фоне."""
    m = Modal(owner, title or "Журнал действий", "clock", width_rem=52)
    m.body.setContentsMargins(0, 0, 0, 0)
    m.body.setSpacing(0)
    if subtitle:
        st = label(subtitle, 1.0, theme.TEXT_VALUE, bold=True, wrap=True)
        st.setContentsMargins(rem(1.1), rem(0.8), rem(1.1), rem(0.2))
        m.body.addWidget(st)
    head = QFrame()
    head.setObjectName("jrHead")
    head.setStyleSheet(f"QFrame#jrHead {{ border: none; border-bottom: {border()}px solid {theme.CARD_BORDER}; }}")
    hl = QHBoxLayout(head)
    hl.setContentsMargins(rem(1.1), rem(0.7), rem(1.1), rem(0.7))
    hl.setSpacing(rem(0.6))
    tabs_box = QHBoxLayout()
    tabs_box.setSpacing(rem(0.3))
    hl.addLayout(tabs_box)
    search = SearchInput("Поиск по сотруднику…")
    hl.addWidget(search, 1)
    count = label("", 0.76, theme.TEXT_MUTED)
    hl.addWidget(count)
    m.body.addWidget(head)
    holder = QWidget()
    lst = QVBoxLayout(holder)
    lst.setContentsMargins(rem(1.1), rem(0.7), rem(1.1), rem(1.0))
    lst.setSpacing(rem(0.35))
    area = scroll_area(holder)
    area.setMinimumHeight(rem(16))
    area.setMaximumHeight(rem(28))
    m.body.addWidget(area, 1)
    lst.addWidget(loading("Читаем журнал…"))
    state = {"tab": "issues", "data": None}
    tab_buttons = {}

    def paint():
        clear_layout(lst)
        rows = (state["data"] or {}).get(state["tab"]) or []
        needle = search.text().strip().lower()
        found = [r for r in rows if _matches(r, needle)]
        if not found:
            e = label("По этому запросу ничего нет" if rows else "Записей пока нет", 0.85, theme.TEXT_MUTED)
            e.setAlignment(Qt.AlignCenter)
            e.setContentsMargins(rem(0.6), rem(1.6), rem(0.6), rem(1.6))
            lst.addWidget(e)
        for r in found:
            lst.addWidget(_row(r))
        lst.addStretch(1)
        count.setText(("найдено: %d из %d" % (len(found), len(rows))) if needle else ("записей: %d" % len(rows)))

    def style_tab(b, on, n):
        b.setText("%s  %d" % (b.property("label"), n))
        b.setStyleSheet(f"QPushButton {{ border: {border()}px solid {theme.ACCENT_FILL if on else 'transparent'};"
                        f" background: {theme.ACCENT_FILL if on else 'transparent'}; color: {'#FFFFFF' if on else theme.TEXT_MUTED};"
                        f" border-radius: {rem(0.5)}px; padding: {rem(0.38)}px {rem(0.7)}px; font-weight: 600; }}"
                        f"QPushButton:hover {{ background: {theme.ACCENT_FILL if on else theme.BTN_GHOST_HOVER}; }}")

    def build_tabs():
        data = state["data"]
        kinds = []
        if data.get("issues") or always_issues:
            kinds.append(("issues", "Выдача"))
        kinds.append(("edits", "Редактирование"))
        if len(kinds) < 2:
            state["tab"] = kinds[0][0]
            return
        if state["tab"] not in [k for k, _ in kinds]:
            state["tab"] = kinds[0][0]
        for key, text in kinds:
            b = QPushButton()
            b.setProperty("label", text)
            b.setCursor(Qt.PointingHandCursor)
            b.setFont(font(0.85, bold=True))
            tab_buttons[key] = b

            def pick(_=False, k=key):
                state["tab"] = k
                for kk, bb in tab_buttons.items():
                    style_tab(bb, kk == k, len(data.get(kk) or []))
                paint()
            b.clicked.connect(pick)
            style_tab(b, key == state["tab"], len(data.get(key) or []))
            tabs_box.addWidget(b)

    search.textChanged.connect(lambda _t: paint() if state["data"] is not None else None)

    def done(data):
        state["data"] = {"issues": (data or {}).get("issues") or [], "edits": (data or {}).get("edits") or []}
        build_tabs()
        paint()

    def failed(e):
        clear_layout(lst)
        lst.addWidget(label(human_error(e), 0.85, theme.TEXT_MUTED, wrap=True))
    jobs.run(load, done=done, failed=failed, owner=m)
    m.foot.hide()
    m.exec()
