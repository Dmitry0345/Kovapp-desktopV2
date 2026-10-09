# -*- coding: utf-8 -*-
"""Раздел «Оборудование» — как js/views/equipment.js веб-версии (компьютер).

Ведомость предприятия: у каждой позиции своя карточка — снимок, название,
количество, «на руках» и «осталось», полоса занятости, люди, за кем
числится оборудование, и журнал выдач и правок. Изменения приходят по
сокету (equipment.row / equipment.removed) и меняют одну карточку.

Права (объект access с сервера): название и количество ведут бухгалтерия
и руководство (can_edit_goods), выдают и принимают возврат — склад и
руководство (can_issue, can_accept_return).
"""

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy,
                             QVBoxLayout, QWidget)

from general_functions import theme
from kov import icons, jobs
from kov.api import client, human_error
from kov.dialogs import Modal, confirm, field
from kov.fmt import fmt_date
from kov.journal import open_journal
from kov.people import avatar, employee_picker, full_name
from kov.store import store
from kov.ui import (Button, SearchInput, Segmented, border, clear_layout, count_label, font, fs, label, line_edit,
                    loading, rem, scroll_area, toast)
from kov.views.base import Section

FILTERS = [("all", "Всё", None), ("out", "На руках", None), ("mine", "У меня", None), ("wait", "Ждут ответа", None)]
_STATE = {"search": "", "filter": "all"}


def num(v):
    if v is None:
        return "—"
    v = round(float(v) * 1000) / 1000
    return ("%d" % v) if v == int(v) else ("%g" % v)


def unit_of(row):
    return (" " + row["unit"]) if row.get("unit") else ""


def parse_num(text):
    t = str(text or "").strip().replace(",", ".")
    if t == "":
        return None
    try:
        return float(t)
    except ValueError:
        return float("nan")


# ══════════════════════════════════════════════════════════════════════════
# Части карточки
# ══════════════════════════════════════════════════════════════════════════

class _Gauge(QWidget):
    """Полоса занятости: доля выданного (зелёная, с 70 % — жёлтая, вся — красная)."""

    def __init__(self, share):
        super().__init__()
        self.share = max(0, min(100, share))
        self.setFixedHeight(rem(0.45))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        rad = rem(0.25)
        p.setPen(QColor(theme.CARD_BORDER))
        p.setBrush(QColor(theme.SURFACE_3))
        p.drawRoundedRect(r, rad, rad)
        if self.share > 0:
            color = theme.CRITICAL if self.share >= 100 else (theme.WARNING if self.share >= 70 else theme.SUCCESS)
            fill = QRectF(r.left(), r.top(), r.width() * self.share / 100.0, r.height())
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(color))
            p.drawRoundedRect(fill, rem(0.2), rem(0.2))
        p.end()


class _Photo(QFrame):
    """Снимок позиции 8rem × 8rem: нажатие на готовый снимок — просмотр, кнопка в углу — заменить."""
    clicked = Signal()

    def __init__(self, row, can_edit, on_change):
        super().__init__()
        self.row = row
        self.img = None
        side = rem(8)
        self.setFixedSize(side, side)
        self.setObjectName("eqPhoto")
        self.setToolTip("Открыть снимок" if row.get("has_photo") else "Снимка нет")
        self.setCursor(Qt.PointingHandCursor if row.get("has_photo") else Qt.ArrowCursor)
        if can_edit:
            b = Button("", "ghost", "camera", "Заменить снимок" if row.get("has_photo") else "Добавить снимок", self)
            b.setFixedSize(rem(1.9), rem(1.9))
            b.move(side - rem(1.9) - rem(0.4), side - rem(1.9) - rem(0.4))
            b.clicked.connect(on_change)
        if row.get("has_photo"):
            PHOTO_CACHE.request(row, self._got)

    def _got(self, img):
        self.img = img
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        rad = rem(0.5)
        path = QPainterPath()
        path.addRoundedRect(r, rad, rad)
        p.fillPath(path, QColor(theme.SURFACE_3))
        if self.img is not None and not self.img.isNull():
            p.setClipPath(path)
            sc = self.img.scaled(self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            p.drawImage((self.width() - sc.width()) // 2, (self.height() - sc.height()) // 2, sc)
            p.setClipping(False)
        else:
            pen = p.pen()
            pen.setColor(QColor(theme.CARD_BORDER))
            pen.setStyle(Qt.DashLine)
            p.setPen(pen)
            p.drawPath(path)
            size = rem(1.8)
            p.drawPixmap((self.width() - size) // 2, (self.height() - size) // 2,
                         icons.pixmap("image", size, theme.TEXT_LABEL))
        p.end()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.row.get("has_photo"):
            self.clicked.emit()


class _PhotoCache:
    """Снимки позиций: один запрос на снимок и версию строки."""

    def __init__(self):
        self.cache = {}

    def key(self, row):
        return (row.get("id"), row.get("updated_at"))

    def request(self, row, cb):
        k = self.key(row)
        if k in self.cache:
            cb(self.cache[k])
            return

        def load():
            res = client.request("GET", "equipment/%d/photo" % row["id"], raw=True, timeout=(10, 30))
            img = QImage()
            img.loadFromData(res.content)
            if not img.isNull() and max(img.width(), img.height()) > 512:
                img = img.scaled(512, 512, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            return img

        def done(img):
            self.cache[k] = img
            try:
                cb(img)
            except RuntimeError:
                pass
        jobs.run(load, done=done, failed=lambda e: None)


PHOTO_CACHE = _PhotoCache()


class _Tiles(QWidget):
    """Плитки выдач по столбцам, как grid auto-fill minmax(19rem, 1fr): столбцов столько, сколько влезает."""

    def __init__(self, tiles, min_rem=19.0, gap_rem=0.4):
        super().__init__()
        self.setStyleSheet("background: transparent;")
        self.tiles = tiles
        self.min_w = rem(min_rem)
        self.gap = rem(gap_rem)
        for t in tiles:
            t.setParent(self)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._h = 0

    def _cols(self, width):
        return max(1, int((width + self.gap) // (self.min_w + self.gap)))

    def _place(self, width, apply=True, height=0):
        cols = self._cols(width)
        cw = (width - self.gap * (cols - 1)) / cols
        y = 0
        single = len(self.tiles) <= cols
        for start in range(0, len(self.tiles), cols):
            row = self.tiles[start:start + cols]
            for t in row:
                t.fit(int(cw))
            rh = max(t.heightForWidth(int(cw)) for t in row)
            if single and apply:
                rh = max(rh, height)        # одна строка плиток тянется на всю высоту (align-self: stretch)
            for k, t in enumerate(row):
                if apply:
                    t.setGeometry(int(k * (cw + self.gap)), y, int(cw), rh)
            y += rh + self.gap
        return max(0, y - self.gap)

    def resizeEvent(self, e):
        self._place(e.size().width(), height=e.size().height())
        self._h = self._place(e.size().width(), apply=False)
        self.setMinimumHeight(self._h)
        super().resizeEvent(e)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._place(w, apply=False)

    def sizeHint(self):
        return QSize(self.min_w, self._h or self._place(self.min_w, apply=False))


class _IssueTile(QFrame):
    """Строка выдачи: аватар, кто и сколько, кнопки. Узкая — кнопки уходят строкой ниже, вправо."""

    def __init__(self, avatar_w, body_w, actions_w):
        super().__init__()
        self.av, self.body, self.acts = avatar_w, body_w, actions_w
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(rem(0.55), rem(0.45), rem(0.55), rem(0.45))
        self.grid.setHorizontalSpacing(rem(0.5))
        self.grid.setVerticalSpacing(rem(0.4))
        self.wrapped = None
        self.fit(10 ** 6)

    def fit(self, width):
        if self.acts is None:
            wrap = False
        else:
            need = (rem(0.55) * 2 + self.av.sizeHint().width() + rem(0.5) + rem(9) + rem(0.5) +
                    self.acts.sizeHint().width())
            wrap = width < need
        if wrap == self.wrapped:
            return
        self.wrapped = wrap
        for w in (self.av, self.body, self.acts):
            if w is not None:
                self.grid.removeWidget(w)
        self.grid.addWidget(self.av, 0, 0, Qt.AlignVCenter)
        self.grid.addWidget(self.body, 0, 1)
        self.grid.setColumnStretch(1, 1)
        if self.acts is not None:
            if wrap:
                self.grid.addWidget(self.acts, 1, 0, 1, 2, Qt.AlignRight)
            else:
                self.grid.addWidget(self.acts, 0, 2, Qt.AlignVCenter)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        self.fit(w)
        return self.grid.totalHeightForWidth(w) if self.grid.hasHeightForWidth() else self.grid.totalSizeHint().height()


class _Editable(QPushButton):
    """Значение, которое правится по нажатию (.eq-value.is-editable): пунктир и карандаш при наведении."""

    def __init__(self, text, size_rem, color=None, empty=False):
        super().__init__(text)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Изменить")
        self.setFont(font(size_rem, bold=not empty))
        b = border()
        style = "font-style: italic;" if empty else ""
        self.setStyleSheet(f"QPushButton {{ text-align: left; background: transparent; border: {b}px dashed transparent;"
                           f" border-radius: {rem(0.35)}px; padding: {rem(0.1)}px {rem(1.2)}px {rem(0.1)}px {rem(0.3)}px;"
                           f" min-height: {rem(1.75)}px; color: {color or (theme.TEXT_MUTED if empty else theme.TEXT_VALUE)}; {style} }}"
                           f"QPushButton:hover {{ background: {theme.BTN_GHOST_HOVER}; border-color: {theme.CARD_BORDER}; }}")
        self._hover = False

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._hover:
            p = QPainter(self)
            size = rem(0.8)
            p.drawPixmap(self.width() - size - rem(0.3), (self.height() - size) // 2,
                         icons.pixmap("edit", size, theme.ACCENT))
            p.end()


def _small(btn):
    """Мелкая кнопка (.eq-issue-actions .btn, .eq-card-foot .btn): высота 1.9rem, шрифт .78rem."""
    btn.setFont(font(0.78, bold=True))
    btn.setStyleSheet(btn.styleSheet() + f"QPushButton {{ min-height: {rem(1.9) - 2}px; max-height: {rem(1.9) - 2}px;"
                                         f" padding: 0 {rem(0.6)}px; font-size: {fs(0.78)}; }}")
    return btn


# ══════════════════════════════════════════════════════════════════════════
# Раздел
# ══════════════════════════════════════════════════════════════════════════

class EquipmentSection(Section):
    title = "Оборудование"
    key = "equipment"

    def __init__(self, shell, parent=None):
        super().__init__(shell, parent)
        self.rows = []
        self.units = []
        self.access = {}
        self.busy = set()
        self.cards = {}
        bar = QHBoxLayout()
        bar.setSpacing(rem(0.6))
        self.search = SearchInput("Поиск по названию…")
        self.search.setText(_STATE["search"])
        self.search.textChanged.connect(self._searched)
        bar.addWidget(self.search, 1)
        self.filter = Segmented(FILTERS, _STATE["filter"])
        self.filter.changed.connect(self._filtered)
        bar.addWidget(self.filter)
        self.count = count_label("")
        self.count.setStyleSheet(f"color: {theme.TEXT_MUTED}; background: transparent;")
        self.count.setFont(font(0.8))
        bar.addWidget(self.count)
        self.add_btn = Button("Добавить", "primary", "plus")
        self.add_btn.clicked.connect(self.add_dialog)
        self.add_btn.hide()
        bar.addWidget(self.add_btn)
        self.root.addLayout(bar)
        self.empty = label("", 0.8, theme.TEXT_MUTED, align=Qt.AlignCenter)
        self.empty.setContentsMargins(rem(1), rem(2.5), rem(1), rem(2.5))
        self.empty.hide()
        self.root.addWidget(self.empty)
        self.holder = QWidget()
        self.holder.setStyleSheet("background: transparent;")
        self.list = QVBoxLayout(self.holder)
        self.list.setContentsMargins(0, 0, 0, 0)
        self.list.setSpacing(rem(0.75))
        self.list.addWidget(loading("Загружаем оборудование…", big=True))
        self.list.addStretch(1)
        self.area = scroll_area(self.holder)
        self.root.addWidget(self.area, 1)
        self.load(first=True)

    # ── данные ───────────────────────────────────────────────────────────────

    def can(self, flag):
        return bool(self.access.get(flag))

    def load(self, first=False, flash=None):
        def done(data):
            data = data or {}
            self.rows = [self.mark_mine(r) for r in data.get("rows") or []]
            self.units = data.get("units") or []
            self.access = data.get("access") or {}
            self.add_btn.setVisible(self.can("can_edit_goods"))
            self.render(flash)

        def failed(e):
            clear_layout(self.list)
            self.list.addStretch(1)
            self.empty.setText("Не загрузилось: " + human_error(e))
            self.empty.show()
        jobs.run(client.get, "equipment", done=done, failed=failed if first else (lambda e: None), owner=self)

    @staticmethod
    def mark_mine(row):
        me = client.user_id
        for i in row.get("issues") or []:
            i["mine"] = bool(i.get("receiver") and i["receiver"].get("id") == me)
            i["issued_by_me"] = bool(i.get("issuer") and i["issuer"].get("id") == me)
        return row

    def act(self, key, fn, ok_text=None):
        if key in self.busy:
            return
        self.busy.add(key)
        self.render()

        def done(_):
            self.busy.discard(key)
            if ok_text:
                toast(self, ok_text, "success")
            self.load()

        def failed(e):
            self.busy.discard(key)
            toast(self, human_error(e), "error")
            self.load()
        jobs.run(fn, done=done, failed=failed, owner=self)

    def on_event(self, evt):
        kind = evt.get("type") or ""
        if not kind.startswith("equipment."):
            return
        if kind == "equipment.removed":
            self.rows = [r for r in self.rows if r.get("id") != evt.get("item_id")]
            self.render()
            return
        row = evt.get("row")
        if kind != "equipment.row" or not row:
            return
        row = self.mark_mine(row)
        at = next((i for i, r in enumerate(self.rows) if r.get("id") == row.get("id")), -1)
        if at < 0:
            self.rows.append(row)
        else:
            self.rows[at] = row
        self.render(row.get("id") if evt.get("actor_id") != client.user_id else None)

    def on_reconnect(self):
        self.load()

    # ── список ───────────────────────────────────────────────────────────────

    def _searched(self, text):
        _STATE["search"] = text
        self.render()

    def _filtered(self, key):
        _STATE["filter"] = key
        self.render()

    def visible_rows(self):
        q = _STATE["search"].strip().lower()
        f = _STATE["filter"]
        out = []
        for r in self.rows:
            if q and q not in (r.get("name") or "").lower():
                continue
            issues = r.get("issues") or []
            if f == "out" and not (r.get("issued") or 0) > 0:
                continue
            if f == "mine" and not any(i.get("mine") and i.get("status") != "pending" for i in issues):
                continue
            if f == "wait" and not any(i.get("status") in ("pending", "return_pending") for i in issues):
                continue
            out.append(r)
        return out

    def sign(self, row):
        issues = ",".join("%s-%s-%s-%s" % (i.get("id"), i.get("status"), i.get("quantity"), 1 if i.get("mine") else 0)
                          for i in row.get("issues") or [])
        return "|".join(str(x) for x in (row.get("name"), row.get("quantity"), row.get("unit"), row.get("issued"),
                                         row.get("left"), row.get("ready"), row.get("has_photo"), row.get("updated_at"),
                                         issues, ",".join(sorted(self.busy))))

    def render(self, flash=None):
        rows = self.visible_rows()
        # карточки сверяются по номеру позиции: неизменённые не пересобираются
        keep = {}
        while self.list.count():
            it = self.list.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                if getattr(w, "row_id", None) is None:
                    w.setParent(None)
                    w.deleteLater()
        for r in rows:
            old = self.cards.get(r.get("id"))
            sig = self.sign(r)
            if old is not None and old.sig == sig and r.get("id") != flash:
                keep[r["id"]] = old
            else:
                if old is not None:
                    old.setParent(None)
                    old.deleteLater()
                keep[r["id"]] = self.card(r, sig, flash == r.get("id"))
        for rid, w in self.cards.items():
            if rid not in keep:
                w.setParent(None)
                w.deleteLater()
        self.cards = keep
        for r in rows:
            w = self.cards[r["id"]]
            self.list.addWidget(w)
            w.show()
        self.list.addStretch(1)
        self.empty.setVisible(not rows)
        self.empty.setText("По этому запросу ничего нет" if self.rows else
                           "Список пуст" + (" — добавьте первую позицию" if self.can("can_edit_goods") else ""))
        mine = sum(1 for r in self.rows for i in r.get("issues") or [] if i.get("mine") and i.get("status") != "pending")
        self.count.setText("Позиций: %d" % len(self.rows) + (" · за вами %d" % mine if mine else ""))

    # ── карточка ─────────────────────────────────────────────────────────────

    def card(self, row, sig, flash=False):
        issues = row.get("issues") or []
        held = [i for i in issues if i.get("status") != "pending"]
        waiting = [i for i in issues if i.get("status") == "pending"]
        needs_me = any((i.get("status") == "pending" and i.get("mine")) or
                       (i.get("status") == "return_pending" and self.can("can_accept_return")) for i in issues)
        el = QFrame()
        el.row_id = row.get("id")
        el.sig = sig
        el.setObjectName("eqCard")
        b = border()
        accent = theme.ACCENT if (needs_me or flash) else theme.CARD_BORDER
        el.setStyleSheet(f"QFrame#eqCard {{ background: {theme.CARD_BG}; border: {b}px solid {accent};"
                         f" border-radius: {rem(0.75)}px; }}"
                         + (f"QFrame#eqCard {{ border-left: {max(2, rem(0.2))}px solid {theme.ACCENT}; }}" if needs_me else ""))
        if flash:
            QTimer.singleShot(1200, lambda: self._unflash(el, needs_me))
        grid = QGridLayout(el)
        grid.setContentsMargins(rem(0.95), rem(0.85), rem(0.95), rem(0.85))
        grid.setHorizontalSpacing(rem(1.0))
        grid.setVerticalSpacing(rem(0.5))
        photo = _Photo(row, self.can("can_edit_goods"), lambda: self.upload_photo(row))
        photo.clicked.connect(lambda: self.view_photo(row))
        grid.addWidget(photo, 0, 0, 3, 1, Qt.AlignTop)
        # шапка: название и кнопки
        head = QHBoxLayout()
        head.setSpacing(rem(0.5))
        name = row.get("name") or ""
        if self.can("can_edit_goods"):
            t = _Editable(name or "Без названия", 1.02, empty=not name)
            t.clicked.connect(lambda: self.edit_field(row, "name", "Оборудование", "Например, перфоратор"))
        else:
            t = label(name or "Без названия", 1.02, theme.TEXT_MUTED if not name else theme.TEXT_VALUE, bold=bool(name),
                      wrap=True)
        t.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        head.addWidget(t, 0, Qt.AlignTop)
        head.addStretch(1)
        acts = QHBoxLayout()
        acts.setSpacing(rem(0.35))
        if self.can("can_issue") and row.get("ready") and (row.get("left") is None or row.get("left") > 0):
            give = Button("Выдать", "primary", "send")
            give.clicked.connect(lambda: self.issue_dialog(row))
            acts.addWidget(give)
        elif self.can("can_issue"):
            text = "Строка не заполнена" if not row.get("ready") else "Всё на руках"
            tip = ("Кнопка выдачи появится, когда заполнены оборудование и количество" if not row.get("ready")
                   else "Свободных единиц нет — выдавать нечего")
            lock = QLabel()
            lock.setToolTip(tip)
            lock.setText(text)
            lock.setFont(font(0.72))
            lock.setStyleSheet(f"color: {theme.TEXT_MUTED}; background: {theme.CHIP_BG}; border-radius: {rem(0.35)}px;"
                               f" padding: {rem(0.2)}px {rem(0.45)}px {rem(0.2)}px {rem(1.35)}px;")
            ic = QLabel(lock)
            ic.setPixmap(icons.pixmap("key", rem(0.85), theme.TEXT_MUTED))
            ic.move(rem(0.4), rem(0.22))
            acts.addWidget(lock)
        if self.can("can_edit_goods"):
            ed = Button("Изменить", "ghost", "edit", "Название, количество и единица")
            ed.clicked.connect(lambda: self.edit_dialog(row))
            acts.addWidget(ed)
            dl = Button("", "ghost", "trash", "Удалить позицию")
            dl.clicked.connect(lambda: self.delete_row(row))
            acts.addWidget(dl)
        head.addLayout(acts)
        grid.addLayout(head, 0, 1, 1, 2)
        # числа
        nums = QGridLayout()
        nums.setHorizontalSpacing(rem(0.4))
        nums.setVerticalSpacing(rem(0.1))
        qty_text = num(row.get("quantity"))
        if self.can("can_edit_goods"):
            qv = _Editable(qty_text, 1.15)
            qv.clicked.connect(lambda: self.edit_field(row, "quantity", "Количество", ""))
        else:
            qv = label(qty_text, 1.15, theme.TEXT_VALUE, bold=True)
        nums.addWidget(qv, 0, 0, Qt.AlignLeft | Qt.AlignBottom)
        nums.addWidget(self._num_label("Количество" + (", " + row["unit"] if row.get("unit") else "")), 1, 0)
        nums.addWidget(label(num(row.get("issued")), 1.15, theme.WARNING_TEXT, bold=True), 0, 1, Qt.AlignLeft | Qt.AlignBottom)
        nums.addWidget(self._num_label("На руках"), 1, 1)
        left = row.get("left")
        nums.addWidget(label(num(left), 1.15, theme.CRITICAL_TEXT if left == 0 else theme.SUCCESS_TEXT, bold=True), 0, 2,
                       Qt.AlignLeft | Qt.AlignBottom)
        nums.addWidget(self._num_label("Осталось"), 1, 2)
        for c in range(3):
            nums.setColumnStretch(c, 1)
        nw = QWidget()
        nw.setLayout(nums)
        nums.setContentsMargins(0, 0, 0, 0)
        grid.addWidget(nw, 1, 1)
        total = row.get("quantity") or 0
        share = min(100, round(((row.get("issued") or 0) / total) * 100)) if total > 0 else 0
        gauge = _Gauge(share)
        gauge.setToolTip("На руках %s из %s" % (num(row.get("issued")), num(total)))
        grid.addWidget(gauge, 2, 1, Qt.AlignVCenter)
        # люди или пустое состояние
        if held or waiting:
            tiles = _Tiles([self.issue_line(row, i) for i in waiting + held])
            grid.addWidget(tiles, 1, 2, 2, 1)
        else:
            grid.addWidget(self.idle(row), 1, 2, 2, 1)
        grid.setColumnMinimumWidth(1, rem(15))
        grid.setColumnStretch(1, 0)
        grid.setColumnStretch(2, 1)
        # подвал: журнал и кто правил
        foot = QFrame()
        foot.setObjectName("eqFoot")
        foot.setStyleSheet(f"QFrame#eqFoot {{ border: none; border-top: {b}px solid {theme.CARD_BORDER}; background: transparent; }}")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(0, rem(0.5), 0, 0)
        fl.setSpacing(rem(0.5))
        jb = _small(Button("Журнал", "ghost", "clock", "Кто и когда выдавал и менял строку"))
        jb.clicked.connect(lambda: self.open_journal(row))
        fl.addWidget(jb)
        if row.get("updated_by"):
            fl.addWidget(label("правил %s · %s" % (row["updated_by"].get("name") or "", fmt_date(row.get("updated_at"))),
                               0.8, theme.TEXT_MUTED))
        fl.addStretch(1)
        grid.addWidget(foot, 3, 0, 1, 3)
        nw.setFixedWidth(rem(22))
        return el

    def _unflash(self, el, needs_me):
        try:
            if needs_me:
                return
            el.setStyleSheet(el.styleSheet().replace(theme.ACCENT, theme.CARD_BORDER, 1))
        except RuntimeError:
            pass

    @staticmethod
    def _num_label(text):
        lab = label(text.upper(), 0.68, theme.TEXT_MUTED)
        lab.setFont(font(0.68, spacing=0.02))
        return lab

    def idle(self, row):
        box = QFrame()
        box.setObjectName("eqIdle")
        b = border()
        box.setStyleSheet(f"QFrame#eqIdle {{ border: {b}px dashed {theme.CARD_BORDER}; border-radius: {rem(0.5)}px;"
                          f" background: {theme.SURFACE_2}; }}")
        box.setMinimumHeight(rem(3.4))
        lay = QHBoxLayout(box)
        lay.setContentsMargins(rem(0.8), rem(0.6), rem(0.8), rem(0.6))
        lay.setSpacing(rem(0.7))
        draft = not row.get("ready")
        d = rem(2.1)
        circ = QLabel()
        circ.setFixedSize(d, d)
        circ.setAlignment(Qt.AlignCenter)
        bg, fg = (theme.WARNING_SOFT, theme.WARNING_TEXT) if draft else (theme.SUCCESS_SOFT, theme.SUCCESS_TEXT)
        circ.setStyleSheet(f"background: {bg}; border-radius: {d // 2}px; border: none;")
        circ.setPixmap(icons.pixmap("warning" if draft else "archive", rem(1.05), fg))
        lay.addWidget(circ)
        tx = QVBoxLayout()
        tx.setSpacing(rem(0.1))
        if draft:
            title = "Позиция не заполнена"
            sub = ("Укажите название и количество — после этого её можно будет выдавать" if self.can("can_edit_goods")
                   else "Название и количество заполняет бухгалтерия")
        else:
            title = "Всё на месте" + (" — " + num(row.get("quantity")) + unit_of(row) if row.get("quantity") else "")
            sub = "Никому не выдано. Нажмите «Выдать», чтобы передать сотруднику" if self.can("can_issue") else "Никому не выдано"
        tx.addWidget(label(title, 0.87, theme.TEXT_VALUE, bold=True))
        tx.addWidget(label(sub, 0.76, theme.TEXT_LABEL if theme.is_dark() else theme.TEXT_MUTED, wrap=True))
        lay.addLayout(tx, 1)
        return box

    def issue_line(self, row, issue):
        st = issue.get("status")
        b = border()
        bd, bg = theme.CARD_BORDER, theme.SURFACE_2
        if st == "pending":
            bd, bg = theme.WARNING, theme.WARNING_SOFT
        elif st == "return_pending":
            bd, bg = theme.INFO, theme.INFO_SOFT
        body = QWidget()
        body.setStyleSheet("background: transparent;")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(rem(0.1))
        who = QHBoxLayout()
        who.setSpacing(rem(0.35))
        who.addWidget(label((issue.get("receiver") or {}).get("name") or "сотрудник", 0.85, theme.TEXT_VALUE, bold=True))
        if issue.get("mine"):
            tag = QLabel("ВЫ")
            tag.setFont(font(0.62, bold=True))
            tag.setStyleSheet(f"background: {theme.ACCENT_FILL}; color: #FFFFFF; border-radius: {rem(0.3)}px;"
                              f" padding: 0 {rem(0.3)}px;")
            who.addWidget(tag)
        who.addStretch(1)
        bl.addLayout(who)
        state = {"pending": "ждёт подтверждения", "issued": "на руках",
                 "return_pending": "возвращает — нужна приёмка"}.get(st, "")
        html = "<span style='color:%s'>%s</span>" % (theme.TEXT_MUTED, num(issue.get("quantity")) + unit_of(row))
        if state:
            html += "&nbsp;&nbsp;<b style='color:%s'>%s</b>" % (theme.TEXT_VALUE, state)
        if issue.get("issuer"):
            html += "&nbsp;&nbsp;<span style='color:%s'>выдал %s</span>" % (theme.TEXT_MUTED, issue["issuer"].get("name") or "")
        m = QLabel(html)
        m.setTextFormat(Qt.RichText)
        m.setWordWrap(True)
        m.setFont(font(0.74))
        m.setStyleSheet("background: transparent;")
        bl.addWidget(m)
        acts_w = QWidget()
        acts_w.setStyleSheet("background: transparent;")
        acts = QHBoxLayout(acts_w)
        acts.setContentsMargins(0, 0, 0, 0)
        acts.setSpacing(rem(0.3))
        iid = issue.get("id")

        def add(text, kind, glyph, key, path):
            btn = _small(Button(text, kind, glyph))
            if key in self.busy:
                btn.setEnabled(False)
            btn.clicked.connect(lambda: self.act(key, lambda: client.post("equipment/issues/%s/%s" % (iid, path))))
            acts.addWidget(btn)
        if st == "pending" and issue.get("mine"):
            add("Принять", "primary", "check", "c%s" % iid, "confirm")
            add("Отказаться", "ghost", "close", "d%s" % iid, "decline")
        elif st == "pending" and (issue.get("issued_by_me") or self.can("is_management")):
            add("Отменить", "ghost", "close", "d%s" % iid, "decline")
        if st == "issued" and issue.get("mine"):
            add("Вернуть", "primary", "undo", "r%s" % iid, "return")
        if st == "return_pending" and self.can("can_accept_return"):
            add("Принять возврат", "primary", "check", "ra%s" % iid, "return/accept")
            add("Отклонить", "ghost", "close", "rd%s" % iid, "return/decline")
        tile = _IssueTile(avatar(issue.get("receiver") or {}, 2.0), body, acts_w if acts.count() else None)
        tile.setObjectName("eqIssue")
        tile.setStyleSheet(f"QFrame#eqIssue {{ background: {bg}; border: {b}px solid {bd}; border-radius: {rem(0.5)}px; }}")
        return tile

    # ── действия ─────────────────────────────────────────────────────────────

    def patch_row(self, row, fields, values):
        body = dict(values)
        body["fields"] = fields
        return client.patch("equipment/%d" % row["id"], json_body=body)

    def edit_field(self, row, fld, title, placeholder):
        is_num = fld == "quantity"
        value = row.get(fld)
        inp = line_edit(placeholder, "" if value is None else (num(value) if is_num else str(value)))
        m = Modal(self, title or "Изменение", "edit", width_rem=26)
        m.body.addWidget(field(title, inp))
        if is_num and (row.get("issued") or 0) > 0:
            m.body.addWidget(self._hint("На руках %s — меньше поставить нельзя" % num(row.get("issued"))))

        def save(_m):
            raw = inp.text().strip()
            v = parse_num(raw) if is_num else raw
            if is_num and v is not None and not (v >= 0):
                toast(m, "Количество не может быть отрицательным", "warning")
                return False
            self.act("edit%s%s" % (row["id"], fld), lambda: self.patch_row(row, [fld], {fld: v}))
            return True
        m.add_button("Отмена", "ghost")
        m.add_button("Сохранить", "primary", "check", save)
        inp.returnPressed.connect(lambda: save(m) and m.accept())
        m.exec()

    @staticmethod
    def _hint(text):
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(rem(0.4))
        ic = QLabel()
        ic.setPixmap(icons.pixmap("info", rem(1.0), theme.ACCENT))
        lay.addWidget(ic, 0, Qt.AlignTop)
        lay.addWidget(label(text, 0.8, theme.TEXT_MUTED, wrap=True), 1)
        return w

    def issue_dialog(self, row):
        free = (row.get("quantity") or 0) - (row.get("issued") or 0)
        chosen = {"p": None}
        m = Modal(self, "Выдача «%s»" % (row.get("name") or ""), "users", width_rem=30)
        m.body.addWidget(self._hint("Свободно %s%s. Получатель подтвердит получение у себя в разделе" % (num(free), unit_of(row))))
        who_row = QHBoxLayout()
        who = QHBoxLayout()
        who.setSpacing(rem(0.4))
        who_w = QWidget()
        who_w.setLayout(who)
        who.setContentsMargins(0, 0, 0, 0)
        who.addWidget(label("Сотрудник не выбран", 0.875, theme.TEXT_MUTED))
        who_row.addWidget(who_w, 1)
        pick = Button("Выбрать сотрудника", "ghost", "users")
        who_row.addWidget(pick)
        box = QWidget()
        box.setLayout(who_row)
        who_row.setContentsMargins(0, 0, 0, 0)
        m.body.addWidget(field("Кому", box))
        qty = line_edit("", "1")
        m.body.addWidget(field("Сколько", qty))

        def picked(ids):
            p = store.employee(ids[0]) or {"id": ids[0]}
            chosen["p"] = p
            clear_layout(who)
            who.addWidget(avatar(p, 1.8))
            sub = ", ".join(x for x in (p.get("department"), p.get("position")) if x)
            lab = QLabel("%s <span style='color:%s'>%s</span>" % (full_name(p, True), theme.TEXT_MUTED, (" — " + sub) if sub else ""))
            lab.setFont(font(0.875))
            lab.setStyleSheet(f"color: {theme.TEXT_VALUE}; background: transparent;")
            who.addWidget(lab, 1)
        pick.clicked.connect(lambda: employee_picker(m, "Кому выдаём оборудование", picked, ok_text="Выбрать"))

        def go(_m):
            v = parse_num(qty.text())
            if not chosen["p"]:
                toast(m, "Выберите получателя", "warning")
                return False
            if v is None or not (v > 0):
                toast(m, "Количество должно быть больше нуля", "warning")
                return False
            if v > free + 1e-9:
                toast(m, "Свободно только " + num(free), "warning")
                return False
            rid = chosen["p"]["id"]
            self.act("issue%s" % row["id"], lambda: client.post("equipment/%d/issue" % row["id"],
                                                                json_body={"receiver_id": rid, "quantity": v}),
                     "Выдача отправлена на подтверждение")
            return True
        m.add_button("Отмена", "ghost")
        m.add_button("Выдать", "primary", "check", go)
        m.exec()

    def open_journal(self, row):
        unit = unit_of(row)

        def load():
            data = client.get("equipment/%d/history" % row["id"]) or {}
            issues = []
            for r in data.get("issues") or []:
                issues.append({"kind": "issue", "action": r.get("action"), "who": r.get("user"), "target": r.get("target"),
                               "at": r.get("created_at"),
                               "title": ((r.get("new_value") + unit + " · ") if r.get("new_value") else "") + (r.get("action_title") or ""),
                               "text": " ".join(x for x in ((r.get("user") or {}).get("name"),
                                                            ("→ " + r["target"]["name"]) if r.get("target") else None) if x)})
            edits = []
            for r in data.get("cells") or []:
                created = r.get("action") == "created"
                old = r.get("old_value")
                new = r.get("new_value")
                edits.append({"kind": "edit", "action": r.get("action"), "who": r.get("user"), "at": r.get("created_at"),
                              "title": "Позиция заведена" if created else (r.get("field_title") or "Изменение"),
                              "text": "" if created else ("%s → %s" % ("—" if old in (None, "") else old,
                                                                         "—" if new in (None, "") else new))})
            return {"issues": issues, "edits": edits}
        open_journal(self, "Журнал позиции", row.get("name") or "Без названия", load, always_issues=True)

    def view_photo(self, row):
        from kov.viewer import MediaViewer
        src = client.file_url("equipment/%d/photo" % row["id"], {"v": row.get("updated_at") or ""})
        MediaViewer.show_list(self, [{"src": src, "filename": row.get("name") or "Оборудование", "download": src}], 0)

    def upload_photo(self, row):
        from kov.photo_crop import pick
        got = pick(self, "Снимок «%s»" % (row.get("name") or "позиция"), round_mask=False)
        if not got:
            return
        name, data = got
        self.act("photo%s" % row["id"], lambda: client.post("equipment/%d/photo" % row["id"],
                                                            files={"file": (name, data, "image/jpeg")}), "Снимок обновлён")

    def delete_row(self, row):
        if not confirm(self, "Удалить позицию?", "Строка «%s» и её журнал будут удалены." % (row.get("name") or ""),
                       "Удалить", danger=True):
            return
        self.act("del%s" % row["id"], lambda: client.delete("equipment/%d" % row["id"]), "Позиция удалена")

    # ── окна правки и добавления ─────────────────────────────────────────────

    def _photo_picker(self, row):
        """Снимок в окне: превью 5.5rem, «камера» в углу — выбрать, «Убрать» — удалить."""
        state = {"file": None, "drop": False}
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(rem(0.6))
        prev = QLabel()
        side = rem(5.5)
        prev.setFixedSize(side, side)
        prev.setAlignment(Qt.AlignCenter)
        lay.addWidget(prev)
        cam = Button("", "ghost", "camera", "Выбрать снимок", prev)
        cam.setFixedSize(rem(1.8), rem(1.8))
        cam.move(side - rem(1.8) - rem(0.3), side - rem(1.8) - rem(0.3))
        drop = Button("Убрать", "ghost", "trash")
        lay.addWidget(drop, 0, Qt.AlignVCenter)
        lay.addStretch(1)

        def paint(img=None):
            has = state["file"] is not None or (row is not None and row.get("has_photo") and not state["drop"])
            b = border()
            prev.setStyleSheet(f"QLabel {{ background: {theme.SURFACE_3}; border-radius: {rem(0.5)}px;"
                               f" border: {b}px {'solid transparent' if has else 'dashed ' + theme.CARD_BORDER}; }}")
            if img is not None and not img.isNull():
                pm = QPixmap.fromImage(img.scaled(side, side, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
                prev.setPixmap(pm.copy((pm.width() - side) // 2, (pm.height() - side) // 2, side, side))
            elif not has:
                prev.setPixmap(icons.pixmap("image", rem(1.5), theme.TEXT_LABEL))
            drop.setVisible(bool(has))

        def choose():
            from kov.photo_crop import pick
            got = pick(self, "Снимок оборудования", round_mask=False)
            if not got:
                return
            state["file"], state["drop"] = got, False
            img = QImage()
            img.loadFromData(got[1])
            paint(img)

        def remove():
            state["file"], state["drop"] = None, True
            prev.clear()
            paint()
        cam.clicked.connect(choose)
        drop.clicked.connect(remove)
        paint()
        if row is not None and row.get("has_photo"):
            PHOTO_CACHE.request(row, lambda img: (not state["drop"] and state["file"] is None) and paint(img))
        w.state = state
        return w

    def save_photo(self, item_id, picker):
        st = getattr(picker, "state", None) or {}
        try:
            if st.get("file"):
                name, data = st["file"]
                client.post("equipment/%d/photo" % item_id, files={"file": (name, data, "image/jpeg")})
            elif st.get("drop"):
                client.delete("equipment/%d/photo" % item_id)
        except Exception as e:
            raise RuntimeError("Снимок не сохранён: " + human_error(e))

    def edit_dialog(self, row):
        from kov.warehouse import UnitPicker
        name = line_edit("Например, перфоратор", row.get("name") or "")
        qty = line_edit("", "" if row.get("quantity") is None else num(row.get("quantity")))
        unit = UnitPicker(self.units, row.get("unit") or "")
        picker = self._photo_picker(row)
        m = Modal(self, "Изменение позиции", "edit", width_rem=30)
        if (row.get("issued") or 0) > 0:
            m.body.addWidget(self._hint("На руках %s%s — меньше этого количество поставить нельзя" % (num(row["issued"]), unit_of(row))))
        m.body.addWidget(field("Оборудование", name, True))
        m.body.addWidget(field("Количество", qty, True))
        m.body.addWidget(field("Единица", unit))
        m.body.addWidget(field("Снимок", picker))

        def save(_m):
            title = name.text().strip()
            if not title:
                toast(m, "Укажите название", "warning")
                return False
            v = parse_num(qty.text())
            if v is not None and not (v >= 0):
                toast(m, "Количество не может быть отрицательным", "warning")
                return False
            if v is not None and v < (row.get("issued") or 0):
                toast(m, "На руках %s — меньше поставить нельзя" % num(row.get("issued")), "warning")
                return False
            nxt = {"name": title, "quantity": v, "unit": unit.value()}

            def same(k):
                a, b2 = nxt[k], row.get(k)
                if k == "quantity":
                    return (a is None and b2 is None) or (a is not None and b2 is not None and float(a) == float(b2))
                return str(a or "") == str(b2 or "")
            fields = [k for k in nxt if not same(k)]
            photo_changed = picker.state["file"] is not None or picker.state["drop"]
            if not fields and not photo_changed:
                toast(self, "Ничего не изменилось", "info")
                return True

            def work():
                if fields:
                    self.patch_row(row, fields, nxt)
                self.save_photo(row["id"], picker)
            self.act("edit%s" % row["id"], work, "Позиция изменена")
            return True
        m.add_button("Отмена", "ghost")
        m.add_button("Сохранить", "primary", "check", save)
        m.exec()

    def add_dialog(self):
        from kov.warehouse import UnitPicker
        name = line_edit("Например, перфоратор")
        qty = line_edit("", "1")
        unit = UnitPicker(self.units, (self.units[0]["name"] if self.units else "шт"))
        picker = self._photo_picker(None)
        m = Modal(self, "Новая позиция", "plus", width_rem=30)
        m.body.addWidget(field("Оборудование", name, True))
        m.body.addWidget(field("Количество", qty, True))
        m.body.addWidget(field("Единица", unit))
        m.body.addWidget(field("Снимок", picker))

        def add(_m):
            title = name.text().strip()
            if not title:
                toast(m, "Укажите название", "warning")
                return False
            v = parse_num(qty.text())
            if v is not None and not (v >= 0):
                toast(m, "Количество не может быть отрицательным", "warning")
                return False
            u = unit.value()

            def work():
                created = client.post("equipment", json_body={"name": title, "quantity": v, "unit": u}) or {}
                if created.get("id"):
                    self.save_photo(created["id"], picker)
            self.act("add", work, "Позиция добавлена")
            return True
        m.add_button("Отмена", "ghost")
        m.add_button("Добавить", "primary", "check", add)
        m.exec()


# ══════════════════════════════════════════════════════════════════════════
# Панель для «Уведомлений»: ждут вашего подтверждения
# ══════════════════════════════════════════════════════════════════════════

def pending_items():
    data = client.get("equipment/issues/pending") or {}
    return data.get("items") or []
