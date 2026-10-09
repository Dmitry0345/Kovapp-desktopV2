# -*- coding: utf-8 -*-
"""Люди в разделах: аватары с фото, чипы участников, список строго
заданной высоты с «+N», окно полного состава с поиском, выбор
ответственных — как в task-view.js и common.css веб-версии."""

from PySide6.QtCore import QObject, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from general_functions import theme
from kov import icons, jobs
from kov.api import client
from kov.dialogs import Modal
from kov.store import store
from kov.ui import Avatar, SearchInput, border, clear_layout, empty, font, label, muted, rem, scroll_area


def full_name(p, with_middle=False):
    if not p:
        return ""
    parts = [p.get("last_name"), p.get("first_name")] + ([p.get("middle_name")] if with_middle else [])
    text = " ".join(x for x in parts if x)
    return text or p.get("name") or p.get("full_name") or ""


def person_of(p):
    """В задаче приходят только id и ФИО — отдел и должность берём из справочника."""
    if p is None:
        return {}
    if not isinstance(p, dict):
        p = {"id": p}
    found = next((e for e in (store._employees or []) if e.get("id") == p.get("id")), None)
    out = dict(p)
    if found:
        out.update(found)
    return out


# ── фото сотрудников ─────────────────────────────────────────────────────────

class _Photos(QObject):
    ready = Signal(int, QImage)

    def __init__(self):
        super().__init__()
        self.cache = {}
        self.waiting = set()

    def get(self, emp_id):
        return self.cache.get(emp_id)

    def request(self, emp_id):
        if emp_id in self.cache or emp_id in self.waiting or emp_id not in store.photo_ids:
            return
        self.waiting.add(emp_id)

        def load():
            res = client.request("GET", "employees/%d/photo" % emp_id, raw=True, timeout=(10, 30))
            img = QImage()
            img.loadFromData(res.content)
            if not img.isNull() and max(img.width(), img.height()) > 256:
                img = img.scaled(256, 256, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            return img

        def done(img):
            self.waiting.discard(emp_id)
            if img is not None and not img.isNull():
                self.cache[emp_id] = img
                self.ready.emit(emp_id, img)

        def failed(_e):
            self.waiting.discard(emp_id)
        jobs.run(load, done=done, failed=failed)

    def forget(self, emp_id):
        self.cache.pop(emp_id, None)


PHOTOS = _Photos()


def avatar(person, size_rem=2.4):
    """Кружок с фото (если есть) или инициалами."""
    person = person or {}
    a = Avatar(person, size_rem)
    a.setToolTip(full_name(person, True))
    emp_id = person.get("id")
    if isinstance(emp_id, int):
        img = PHOTOS.get(emp_id)
        if img is not None:
            a.set_photo(QPixmap.fromImage(img))
        elif emp_id in store.photo_ids:
            def ready(i, im, w=a, want=emp_id):
                if i == want:
                    try:
                        w.set_photo(QPixmap.fromImage(im))
                    except RuntimeError:
                        pass
            PHOTOS.ready.connect(ready)
            PHOTOS.request(emp_id)
    return a


class PersonChip(QFrame):
    """Чип сотрудника: аватар и «Фамилия Имя», подсказка — ФИО, отдел, должность."""

    clicked = Signal(object)

    def __init__(self, p, max_name_rem=11, parent=None, height_rem=2.3):
        super().__init__(parent)
        e = person_of(p)
        self.person = e
        self.setObjectName("personChip")
        self.setStyleSheet(f"QFrame#personChip {{ background: {theme.CARD_BG}; border: {border()}px solid {theme.CARD_BORDER};"
                           f" border-radius: {rem(height_rem) // 2}px; }}")
        self.setFixedHeight(rem(height_rem))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(rem(0.2), rem(0.2), rem(0.55), rem(0.2))
        lay.setSpacing(rem(0.4))
        lay.addWidget(avatar(e, 1.7))
        name = full_name(e) or ("сотрудник №%s" % e.get("id"))
        fm = QFontMetrics(font(0.8))
        lab = label(fm.elidedText(name, Qt.ElideRight, rem(max_name_rem)), 0.8)
        lay.addWidget(lab)
        self.setToolTip(" · ".join(x for x in (full_name(e, True), e.get("department"), e.get("position")) if x))
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.person)
        super().mouseReleaseEvent(e)


class _MoreChip(QPushButton):
    def __init__(self, parent=None, height_rem=2.3):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Показать весь список")
        self.setFixedHeight(rem(height_rem))
        self.setFont(font(0.8, bold=True))
        self.setStyleSheet(f"QPushButton {{ background: {theme.CHIP_BG}; color: {theme.ACCENT}; border: {border()}px dashed {theme.ACCENT_SOFT};"
                           f" border-radius: {rem(height_rem) // 2}px; padding: 0 {rem(0.7)}px; }}"
                           f"QPushButton:hover {{ background: {theme.CARD_BG}; border-style: solid; border-color: {theme.ACCENT}; }}")


class ClampedPeople(QWidget):
    """Чипы людей строго в rows строк; что не влезло — «+N» (открывает весь состав)."""

    def __init__(self, people, rows=2, on_more=None, parent=None, height_rem=2.3, on_person=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        self.rows = rows
        self.gap = rem(0.35)
        self.row_h = rem(height_rem)
        self.chips = []
        for p in people or []:
            c = PersonChip(p, height_rem=height_rem)
            c.setParent(self)
            if on_person:
                c.clicked.connect(on_person)
                c.setCursor(Qt.PointingHandCursor)
            self.chips.append(c)
        self.more = _MoreChip(self, height_rem)
        self.more.hide()
        if on_more:
            self.more.clicked.connect(on_more)
        self.empty = None
        if not self.chips:
            self.empty = muted("Не назначены", 0.85)
            self.empty.setParent(self)
        self.setFixedHeight(self.rows * self.row_h + (self.rows - 1) * self.gap if self.chips else rem(1.4))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def _place(self, count, width):
        """Раскладка первых count чипов (+N при необходимости); True — влезли."""
        x = y = 0
        row = 0
        boxes = []
        items = self.chips[:count] + ([self.more] if count < len(self.chips) else [])
        if count < len(self.chips):
            self.more.setText("+%d" % (len(self.chips) - count))
            self.more.adjustSize()
        for w in items:
            hint = w.sizeHint()
            cw = min(hint.width(), width)
            if x and x + cw > width:
                x = 0
                row += 1
                y += self.row_h + self.gap
            if row >= self.rows:
                return False, boxes
            boxes.append((w, x, y, cw))
            x += cw + self.gap
        return True, boxes

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.relayout()

    def relayout(self):
        if self.empty is not None:
            self.empty.setGeometry(0, 0, self.width(), rem(1.4))
            return
        width = max(rem(6), self.width())
        for count in range(len(self.chips), -1, -1):
            ok, boxes = self._place(count, width)
            if ok:
                for c in self.chips:
                    c.hide()
                self.more.hide()
                for w, x, y, cw in boxes:
                    w.setGeometry(x, y, cw, self.row_h)
                    w.show()
                return


def people_dialog(owner, title, groups, shell=None):
    """Окно полного состава: groups — [(заголовок, список)]; щелчок — профиль."""
    m = Modal(owner, title, "users", width_rem=52)
    search = SearchInput("Поиск по ФИО, отделу, должности…")
    m.body.addWidget(search)
    holder = QWidget()
    box = QVBoxLayout(holder)
    box.setContentsMargins(0, 0, 0, 0)
    box.setSpacing(rem(0.1))
    area = scroll_area(holder)
    area.setMinimumHeight(rem(18))
    area.setMaximumHeight(rem(26))
    m.body.addWidget(area)
    m.body.addWidget(label("Нажмите на сотрудника, чтобы открыть его профиль.", 0.8, theme.TEXT_MUTED))

    def render():
        clear_layout(box)
        needle = search.text().strip().lower()
        shown = 0
        for caption, lst in groups:
            lst = lst or []
            people = [person_of(p) for p in lst]
            people = [p for p in people if not needle or needle in (" ".join(
                [full_name(p, True), p.get("department") or "", p.get("position") or ""])).lower()]
            shown += len(people)
            head = QHBoxLayout()
            cap = label(caption.upper(), 0.72, theme.TEXT_LABEL, bold=True)
            cap.setFont(font(0.72, bold=True, spacing=0.08))
            head.addWidget(cap)
            head.addStretch(1)
            head.addWidget(label(("%d из %d" % (len(people), len(lst))) if needle else str(len(lst)), 0.78, theme.TEXT_MUTED))
            hw = QWidget()
            hw.setLayout(head)
            head.setContentsMargins(rem(0.45), rem(0.6), rem(0.45), rem(0.3))
            box.addWidget(hw)
            if not people:
                box.addWidget(label("Совпадений нет" if needle else "Никто не назначен", 0.8, theme.TEXT_MUTED))
                continue
            for p in people:
                row = QPushButton()
                row.setCursor(Qt.PointingHandCursor)
                row.setStyleSheet(f"QPushButton {{ border: none; background: transparent; border-radius: {rem(0.5)}px;"
                                  f" text-align: left; padding: {rem(0.4)}px {rem(0.45)}px; }}"
                                  f"QPushButton:hover {{ background: {theme.CHIP_BG}; }}")
                rl = QHBoxLayout(row)
                rl.setContentsMargins(rem(0.45), rem(0.4), rem(0.45), rem(0.4))
                rl.setSpacing(rem(0.6))
                rl.addWidget(avatar(p, 2.2))
                who = QVBoxLayout()
                who.setSpacing(0)
                who.addWidget(label(full_name(p, True) or ("сотрудник №%s" % p.get("id")), 0.88, theme.TEXT_VALUE, bold=True))
                who.addWidget(label(" · ".join(x for x in (p.get("department"), p.get("position")) if x)
                                    or "Данные сотрудника недоступны", 0.75, theme.TEXT_LABEL))
                rl.addLayout(who, 1)
                row.setMinimumHeight(rem(3.0))

                def go(_=False, pid=p.get("id")):
                    m.accept()
                    if shell is not None and pid:
                        shell.open_profile(pid)
                row.clicked.connect(go)
                box.addWidget(row)
        if not shown and needle:
            box.addWidget(empty("Никого не найдено"))
        box.addStretch(1)
    search.textChanged.connect(lambda _t: render())
    render()
    m.foot.hide()
    m.exec()


class LeadRow(QPushButton):
    """Строка выбора человека: аватар, ФИО, подпись и кружок-отметка."""

    def __init__(self, p, sub="", on=False, parent=None):
        super().__init__(parent)
        self.person = p
        self.setCheckable(True)
        self.setChecked(on)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(rem(3.0))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(rem(0.55), rem(0.4), rem(0.55), rem(0.4))
        lay.setSpacing(rem(0.55))
        lay.addWidget(avatar(p, 2.0))
        who = QVBoxLayout()
        who.setSpacing(0)
        self.name = label(full_name(p, True), 0.87, theme.TEXT_VALUE, bold=True)
        who.addWidget(self.name)
        if sub:
            who.addWidget(label(sub, 0.75, theme.TEXT_MUTED))
        lay.addLayout(who, 1)
        self.mark = QLabel()
        self.mark.setFixedSize(rem(1.35), rem(1.35))
        self.mark.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.mark)
        self.toggled.connect(lambda _v: self.paint())
        self.paint()

    def paint(self):
        on = self.isChecked()
        b = border()
        self.setStyleSheet(f"QPushButton {{ text-align: left; background: {theme.SURFACE_2};"
                           f" border: {b}px solid {theme.ACCENT if on else theme.CARD_BORDER}; border-radius: {rem(0.5)}px;"
                           f" {'border-left: ' + str(rem(0.18)) + 'px solid ' + theme.ACCENT + ';' if on else ''} }}"
                           f"QPushButton:hover {{ background: {theme.BTN_GHOST_HOVER}; }}")
        self.name.setStyleSheet(f"color: {theme.ACCENT if on else theme.TEXT_VALUE}; background: transparent;")
        d = rem(1.35)
        self.mark.setStyleSheet(f"background: {theme.ACCENT_FILL if on else theme.CARD_BG};"
                                f" border: {b}px solid {theme.ACCENT_FILL if on else theme.CARD_BORDER}; border-radius: {d // 2}px;")
        self.mark.setPixmap(icons.pixmap("check" if on else "plus", rem(0.8), "#FFFFFF" if on else theme.TEXT_LABEL, 2.2))


class _PickRow(QFrame):
    """Строка выбора: нажатие в любом месте строки — как по флажку (label сайта)."""

    def __init__(self, box):
        super().__init__()
        self.box = box
        if box.isEnabled():
            self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.box.isEnabled() and self.rect().contains(e.position().toPoint()):
            self.box.click()
        super().mouseReleaseEvent(e)


def employee_picker(owner, title, on_pick, multi=False, ok_text=None, with_self=False, taken=None):
    """Выбор сотрудников (как employeePicker веб-версии): поиск, флажки или
    переключатели, «уже добавлен» — неактивны. on_pick([id, …])."""
    import html as _html
    from PySide6.QtWidgets import QButtonGroup, QCheckBox, QGraphicsOpacityEffect, QRadioButton
    from kov.ui import Chip, checkbox_qss, fit_scroll, fs, radio_qss
    taken = {int(t["id"]): t.get("note") or "уже добавлен" for t in (taken or [])}
    selected = set()
    m = Modal(owner, title, "users", width_rem=41.4)
    search = SearchInput("Поиск по ФИО, отделу, должности…")
    m.body.addWidget(search)
    counter = label("", 0.75, theme.TEXT_LABEL)
    m.body.addWidget(counter)
    # .checklist сайта: рамка, высота по содержимому, не больше 20rem
    frame = QFrame()
    frame.setObjectName("checklist")
    frame.setStyleSheet(f"QFrame#checklist {{ border: {border()}px solid {theme.CARD_BORDER}; border-radius: {rem(0.5)}px;"
                        f" background: {theme.CARD_BG}; }}")
    fl = QVBoxLayout(frame)
    fl.setContentsMargins(border(), border(), border(), border())
    holder = QWidget()
    holder.setStyleSheet("background: transparent;")
    lst = QVBoxLayout(holder)
    lst.setContentsMargins(rem(0.4), rem(0.4), rem(0.4), rem(0.4))
    lst.setSpacing(rem(0.25))
    area = scroll_area(holder)
    fl.addWidget(area)
    m.body.addWidget(frame)
    people = []
    radios = QButtonGroup(m)        # один выбранный (name="kovChatPick" сайта)
    radios.setExclusive(True)


    def update_counter():
        free = sum(1 for e in people if int(e.get("id") or 0) not in taken)
        if multi:
            counter.setText("Выбрано: %d · доступно: %d" % (len(selected), free) +
                            (" · уже в списке: %d" % len(taken) if taken else ""))
        else:
            counter.setText("С отмеченными сотрудниками чат уже создан" if taken else "")
        counter.setVisible(bool(counter.text()))

    def render():
        for b in radios.buttons():
            radios.removeButton(b)
        clear_layout(lst)
        needle = search.text().strip().lower()
        rows = [e for e in people if not needle or needle in (" ".join(
            [full_name(e, True), e.get("department") or "", e.get("position") or ""])).lower()]
        for e in rows:
            note = taken.get(int(e.get("id") or 0))
            box = QCheckBox() if multi else QRadioButton()
            box.setStyleSheet(checkbox_qss() if multi else radio_qss())
            box.setChecked(e.get("id") in selected)
            box.setEnabled(not note)
            row = _PickRow(box)
            row.setObjectName("chkRow")
            row.setStyleSheet(f"QFrame#chkRow {{ border-radius: {rem(0.35)}px; }}"
                              + ("" if note else f"QFrame#chkRow:hover {{ background: {theme.CHIP_BG}; }}"))
            rl = QHBoxLayout(row)
            rl.setContentsMargins(rem(0.4), rem(0.35), rem(0.4), rem(0.35))
            rl.setSpacing(rem(0.5))

            def toggled(on, pid=e.get("id")):
                if not multi and on:
                    selected.clear()
                (selected.add if on else selected.discard)(pid)
                update_counter()
            box.toggled.connect(toggled)
            if not multi:
                radios.addButton(box)
            rl.addWidget(box, 0, Qt.AlignVCenter)
            rl.addWidget(avatar(e, 1.7), 0, Qt.AlignVCenter)
            desc = ", ".join(x for x in (e.get("department"), e.get("position")) if x)
            lab = QLabel(_html.escape(full_name(e, True)) + (
                "<span style='color:%s; font-size:%s'> — %s</span>" % (theme.TEXT_LABEL, fs(0.75), _html.escape(desc)) if desc else ""))
            lab.setTextFormat(Qt.RichText)
            lab.setWordWrap(True)
            lab.setFont(font(0.875))
            lab.setStyleSheet(f"color: {theme.TEXT_VALUE}; background: transparent;")
            lab.setMinimumWidth(rem(6))
            rl.addWidget(lab, 1)
            if note:
                rl.addWidget(Chip(note, "info"), 0, Qt.AlignVCenter)
                # .checklist label.off: строка бледнее, выбрать нельзя
                dim = QGraphicsOpacityEffect(row)
                dim.setOpacity(0.6)
                row.setGraphicsEffect(dim)
            lst.addWidget(row)
        if not rows:
            lst.addWidget(muted("Никого не найдено", 0.85))
        lst.addStretch(1)
        update_counter()
        fit_scroll(area, 20, m.refit)
    search.textChanged.connect(lambda _t: render())
    lst.addWidget(muted("Загрузка…", 0.85))

    def loaded(lst_):
        people[:] = [e for e in (lst_ or []) if with_self or e.get("id") != client.user_id]
        render()
    jobs.run(store.pickable, done=loaded, failed=lambda e: loaded([]), owner=m)

    def ok(_m):
        if not selected:
            from kov.ui import toast
            toast(m, "Никто не выбран", "warning")
            return False
        on_pick(sorted(selected))
        return True
    m.add_button("Отмена", "ghost")
    m.add_button(ok_text or ("Добавить" if multi else "Открыть чат"), "primary", "check", ok)
    m.exec()
