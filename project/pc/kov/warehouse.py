# -*- coding: utf-8 -*-
"""Склад проекта — как js/views/warehouse.js: ведомость на 100 строк.

Столбцы: Товар · Количество · Единица · Выдано · Осталось · На складе.
«Выдано» руками не правится — это сумма подтверждённых выдач. Права
(объект access) приходят с сервера: чужие ячейки только для чтения.
Вставка из Excel/1С (Ctrl+V в столбце «Товар») — с предпросмотром.
"""

import re

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QKeySequence, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QPushButton, QStyle, QStyledItemDelegate, QTableWidget, QTableWidgetItem, QVBoxLayout,
                             QWidget)

from general_functions import theme
from kov import icons, jobs
from kov.api import client, human_error
from kov.dialogs import Modal, confirm, field
from kov.fmt import fmt_datetime
from kov.people import avatar, employee_picker, full_name
from kov.store import store
from kov.ui import (Button, Chip, round_view, SearchInput, Select, border, clear_layout, empty, font, fs, input_qss, label, line_edit,
                    loading, muted, rem, scroll_area, scroll_qss, select_qss, toast)

STATES = [("", "—"), ("enough", "Достаточно"), ("short", "Недостаточно")]
ROWS_LIMIT = 100
COLS = ("no", "name", "quantity", "unit", "issued", "left", "stock", "act")


def show(v):
    if v is None or v == "":
        return ""
    v = round(float(v) * 1000) / 1000
    return ("%d" % v) if v == int(v) else ("%g" % v)


def num(text):
    t = str(text or "").strip().replace(",", ".")
    if t == "":
        return None
    try:
        return float(t)
    except ValueError:
        return float("nan")


def unit_title(u):
    if not u:
        return ""
    return u["name"] + (" — " + u["full_name"] if u.get("full_name") else "")


def row_flags(r):
    filled = bool((r.get("name") or "").strip())
    left = None if r.get("quantity") is None else round((r["quantity"] - (r.get("issued") or 0)) * 1000) / 1000
    return {"empty": not filled, "done": filled and left is not None and left <= 0,
            "short": filled and r.get("stock_state") == "short"}


def cell_hint(r, fld):
    info = (r.get("by") or {}).get(fld)
    if not info:
        return ""
    return "Изменил: %s" % info.get("who") + (" · " + fmt_datetime(info.get("at")) if info.get("at") else "")


class _NoDelegate(QStyledItemDelegate):
    """№ строки: полоса слева — «всё выдано» (зелёная) или «не хватает» (красная)."""

    def __init__(self, block):
        super().__init__(block.table)
        self.block = block

    def paint(self, painter, option, index):
        if index.data(Qt.UserRole + 1) == "empty":
            super().paint(painter, option, index)
            return
        painter.save()
        painter.fillRect(option.rect, QColor(theme.SURFACE_HEAD_A))
        r = self.block.row_at(index.row())
        if r is not None:
            f = row_flags(r)
            bar = QColor(theme.CRITICAL) if f["short"] else (QColor(theme.SUCCESS) if f["done"] else None)
            if bar is not None:
                painter.fillRect(QRect(option.rect.left(), option.rect.top(), max(2, rem(0.22)), option.rect.height()), bar)
        painter.setPen(QColor(theme.TEXT_MUTED if (r and row_flags(r)["empty"]) else theme.TEXT_LABEL))
        painter.setFont(font(0.74))
        painter.drawText(option.rect, Qt.AlignCenter, index.data() or "")
        painter.setPen(QColor(theme.CARD_BORDER))
        painter.drawLine(option.rect.topRight(), option.rect.bottomRight())
        painter.restore()


def row_height():
    """Строка ведомости: поле 2.4rem и отступы ячейки .55rem сверху и снизу (как у сайта)."""
    return rem(2.4) + 2 * rem(0.55) + border()


CELL_PAD = 1.1      # отступ текста в ячейке: .6rem ячейки + .5rem поля


class _UnitDelegate(QStyledItemDelegate):
    """«Единица»: значение слева и галочка справа — как кнопка .wh-unit-btn."""

    def __init__(self, block):
        super().__init__(block.table)
        self.block = block

    def paint(self, painter, option, index):
        if index.data(Qt.UserRole + 1) == "empty":
            super().paint(painter, option, index)
            return
        painter.save()
        r = option.rect
        editable = bool(self.block.access.get("can_edit_goods"))
        if editable and option.state & QStyle.State_MouseOver:
            box = r.adjusted(rem(0.6), (r.height() - rem(2.3)) // 2, -rem(0.6), -(r.height() - rem(2.3)) // 2)
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(QColor(theme.CARD_BORDER))
            painter.setBrush(QColor(theme.CARD_BG))
            painter.drawRoundedRect(box, rem(0.35), rem(0.35))
        text = index.data() or "—"
        painter.setFont(font(0.82))
        painter.setPen(QColor(theme.TEXT_VALUE if index.data() else theme.TEXT_MUTED))
        painter.drawText(r.adjusted(rem(CELL_PAD), 0, -rem(2.0), 0), Qt.AlignVCenter | Qt.AlignLeft, text)
        if editable:
            size = rem(0.9)
            painter.drawPixmap(r.right() - rem(1.0) - size, r.top() + (r.height() - size) // 2,
                               icons.pixmap("chevron_down", size, theme.TEXT_MUTED))
        painter.restore()


class _IssuedDelegate(QStyledItemDelegate):
    """«Выдано»: число по центру и жёлтый значок «+N» — ждёт подтверждения получателем."""

    def __init__(self, block):
        super().__init__(block.table)
        self.block = block

    def paint(self, painter, option, index):
        if index.data(Qt.UserRole + 1) == "empty":
            super().paint(painter, option, index)
            return
        painter.save()
        r = option.rect
        clickable = bool(index.data(Qt.UserRole + 2))
        if clickable and option.state & QStyle.State_MouseOver:
            painter.fillRect(r, QColor(theme.INFO_SOFT))
            painter.setPen(QColor(theme.INFO))
            painter.drawRect(r.adjusted(0, 0, -1, -1))
        value = index.data() or "0"
        pending = index.data(Qt.UserRole + 3) or ""
        f = font(0.82)
        fm = QFontMetrics(f)
        cf = font(0.66, bold=True)
        cfm = QFontMetrics(cf)
        chip_w = cfm.horizontalAdvance(pending) + 2 * rem(0.35) if pending else 0
        total = fm.horizontalAdvance(value) + (rem(0.3) + chip_w if pending else 0)
        x = r.left() + (r.width() - total) // 2
        painter.setFont(f)
        painter.setPen(QColor(theme.TEXT_VALUE))
        painter.drawText(QRect(x, r.top(), fm.horizontalAdvance(value) + 2, r.height()), Qt.AlignVCenter | Qt.AlignLeft, value)
        if pending:
            cx = x + fm.horizontalAdvance(value) + rem(0.3)
            ch = cfm.height() + 2 * rem(0.05)
            cy = r.top() + (r.height() - ch) // 2
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(theme.WARNING_SOFT))
            painter.drawRoundedRect(QRect(cx, cy, chip_w, ch), ch / 2, ch / 2)
            painter.setFont(cf)
            painter.setPen(QColor(theme.WARNING_TEXT))
            painter.drawText(QRect(cx, cy, chip_w, ch), Qt.AlignCenter, pending)
        painter.restore()


class _FullDelegate(QStyledItemDelegate):
    """Столбцы со встроенными полями: поле на всю ячейку, без отступа текста ячейки."""

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


def _cell_qss(widget):
    """Поле прямо в ячейке (.wh-input/.wh-select): рамка видна только под указателем и в правке."""
    b = border()
    return (f"{widget} {{ background: transparent; border: {b}px solid transparent; border-radius: {rem(0.35)}px;"
            f" color: {theme.TEXT_VALUE}; padding: 0 {rem(0.5)}px; font-size: {fs(0.82)}; }}"
            f"{widget}:hover {{ border-color: {theme.CARD_BORDER}; background: {theme.CARD_BG}; }}"
            f"{widget}:focus {{ border-color: {theme.ACCENT}; background: {theme.CARD_BG}; }}")


class _CellSelect(Select):
    """Выпадающий список в ячейке: без рамки, галочка серая (.wh-select)."""

    def __init__(self, items, color=None, bold=False):
        super().__init__(items)
        self.setFixedHeight(rem(2.3))
        self.setFont(font(0.82, weight=QFont.DemiBold if bold else None))
        self.setStyleSheet(select_qss() + _cell_qss("QComboBox") + f"QComboBox {{ padding-right: 0px;"
                           f" color: {color or theme.TEXT_VALUE}; }}"
                           f"QComboBox::drop-down {{ border: none; width: {rem(1.5)}px; }}")

    def paintEvent(self, event):
        QComboBox.paintEvent(self, event)
        p = QPainter(self)
        size = rem(1.0)
        p.drawPixmap(self.width() - rem(0.35) - size, (self.height() - size) // 2,
                     icons.pixmap("chevron_down", size, theme.TEXT_LABEL, 2.0))
        p.end()


class UnitPicker(QPushButton):
    """Выбор единицы (.wh-unit-btn.wide): значение и галочка, по нажатию — список с поиском.

    Обычный выпадающий список тут не годится: единиц со временем много,
    в нём нельзя ни искать, ни показать полное название."""
    changed = Signal(str)

    def __init__(self, units, value="", parent=None):
        super().__init__(parent)
        self.units = list(units or [])
        self._value = value or ""
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Выбрать единицу измерения")
        self.setFont(font(0.875))
        self.setFixedHeight(rem(2.4))
        self.setMinimumWidth(rem(8))
        b = border()
        self.setStyleSheet(f"QPushButton {{ text-align: left; background: {theme.CARD_BG}; color: {theme.TEXT_VALUE};"
                           f" border: {b}px solid {theme.CARD_BORDER}; border-radius: {rem(0.35)}px;"
                           f" padding: 0 {rem(1.6)}px 0 {rem(0.5)}px; }}"
                           f"QPushButton:hover {{ border-color: {theme.ACCENT_SOFT}; }}"
                           f"QPushButton:focus {{ border-color: {theme.ACCENT}; outline: none; }}")
        self.setText(self._value or "—")
        self.clicked.connect(self.open)

    def value(self):
        return self._value

    def set_value(self, v):
        self._value = v or ""
        self.setText(self._value or "—")

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        size = rem(0.9)
        p.drawPixmap(self.width() - rem(0.4) - size, (self.height() - size) // 2,
                     icons.pixmap("chevron_down", size, theme.TEXT_MUTED))
        p.end()

    def open(self):
        pop = QFrame(self.window(), Qt.Popup | Qt.FramelessWindowHint)
        pop.setObjectName("unitPop")
        pop.setStyleSheet(f"QFrame#unitPop {{ background: {theme.CARD_BG}; border: {border()}px solid {theme.CARD_BORDER};"
                          f" border-radius: {rem(0.75)}px; }}")
        lay = QVBoxLayout(pop)
        lay.setContentsMargins(rem(0.4), rem(0.4), rem(0.4), rem(0.4))
        lay.setSpacing(rem(0.3))
        search = line_edit("Поиск единицы…")
        lay.addWidget(search)
        holder = QWidget()
        lst = QVBoxLayout(holder)
        lst.setContentsMargins(0, 0, 0, 0)
        lst.setSpacing(rem(0.1))
        area = scroll_area(holder)
        area.setFixedHeight(rem(12))
        lay.addWidget(area)
        first = {}

        def pick(name):
            pop.close()
            if name != self._value:
                self.set_value(name)
                self.changed.emit(name)

        def render():
            clear_layout(lst)
            needle = search.text().strip().lower()
            found = [u for u in self.units if not needle or needle in u["name"].lower()
                     or needle in (u.get("full_name") or "").lower()]
            opts = [("", "—", "без единицы")] + [(u["name"], u["name"], u.get("full_name") or "") for u in found]
            first["v"] = opts[1][0] if needle and len(opts) > 1 else opts[0][0]
            for val, short, full in opts:
                on = val == self._value
                b = QPushButton()
                b.setCursor(Qt.PointingHandCursor)
                b.setFont(font(0.85))
                b.setText("%s   %s" % (short, full))
                b.setStyleSheet(f"QPushButton {{ text-align: left; border: none; border-radius: {rem(0.35)}px;"
                                f" padding: {rem(0.4)}px {rem(0.55)}px; color: {theme.TEXT_VALUE}; font-weight: {700 if on else 400};"
                                f" background: {theme.INFO_SOFT if on else 'transparent'}; }}"
                                f"QPushButton:hover {{ background: {theme.CHIP_BG}; }}")
                b.clicked.connect(lambda _=False, v=val: pick(v))
                lst.addWidget(b)
            if not found and needle:
                lst.addWidget(muted("Ничего не найдено", 0.8))
            lst.addStretch(1)
        search.textChanged.connect(lambda _t: render())
        search.returnPressed.connect(lambda: pick(first.get("v", "")))
        render()
        pop.setFixedWidth(max(self.width(), rem(12.5)))
        pos = self.mapToGlobal(QPoint(0, self.height() + rem(0.25)))
        scr = QApplication.screenAt(pos) or QApplication.primaryScreen()
        pop.adjustSize()
        if pos.y() + pop.height() > scr.availableGeometry().bottom():
            pos = self.mapToGlobal(QPoint(0, -pop.height() - rem(0.25)))
        pop.move(pos)
        pop.show()
        search.setFocus()


class WarehouseBlock(QWidget):
    """Ведомость склада проекта. load(soft) — перечитать; on_event — события сокета."""

    def __init__(self, project_id, parent=None):
        super().__init__(parent)
        self.project_id = project_id
        self.rows = []
        self.units = []
        self.access = {}
        self.pending = []
        self.only_filled = False
        self._visible = []
        self._loading = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(rem(0.6))
        bar = QHBoxLayout()
        bar.setSpacing(rem(0.5))
        self.search = SearchInput("Поиск по товару…")
        self.search.setMaximumWidth(rem(26))
        self.search.textChanged.connect(lambda _t: self.draw())
        bar.addWidget(self.search, 1)
        self.filled_btn = Button("Только заполненные", "ghost", "filter")
        self.filled_btn.clicked.connect(self._toggle_filled)
        bar.addWidget(self.filled_btn)
        units = Button("Единицы", "ghost", "table", "Справочник единиц измерения")
        units.clicked.connect(self.units_dialog)
        bar.addWidget(units)
        self.editors_btn = Button("Кто правит товары", "ghost", "users")
        self.editors_btn.clicked.connect(self.editors_dialog)
        self.editors_btn.hide()
        bar.addWidget(self.editors_btn)
        bar.addStretch(1)
        self.legend = label("", 0.75, theme.TEXT_LABEL)
        bar.addWidget(self.legend)
        lay.addLayout(bar)
        self.pending_box = QVBoxLayout()
        self.pending_box.setSpacing(rem(0.4))
        lay.addLayout(self.pending_box)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(["№", "ТОВАР", "КОЛИЧЕСТВО", "ЕДИНИЦА", "ВЫДАНО", "ОСТАЛОСЬ",
                                              "КОЛИЧЕСТВО НА СКЛАДЕ", ""])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(True)        # вертикальные разделители, как td + td у сайта
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.SelectedClicked | QAbstractItemView.DoubleClicked |
                                   QAbstractItemView.EditKeyPressed | QAbstractItemView.AnyKeyPressed |
                                   QAbstractItemView.CurrentChanged)
        self.table.setFocusPolicy(Qt.StrongFocus)
        self.table.setWordWrap(False)
        b = border()
        self.table.setStyleSheet(
            f"QTableWidget {{ background: {theme.CARD_BG}; border: {b}px solid {theme.CARD_BORDER}; border-radius: {rem(0.5)}px;"
            f" color: {theme.TEXT_VALUE}; outline: none; gridline-color: {theme.CARD_BORDER}; }}"
            f"QTableWidget::item {{ border: none; padding: 0 {rem(CELL_PAD)}px; }}"
            f"QTableWidget::item:hover {{ background: {theme.SURFACE_2}; }}"
            f"QTableWidget::item:focus {{ background: {theme.CARD_BG}; border: {b}px solid {theme.ACCENT}; }}"
            f"QHeaderView::section {{ background: {theme.SURFACE_HEAD_B}; color: {theme.TITLE_FG}; border: none;"
            f" border-left: {b}px solid {theme.CARD_BORDER};"
            f" border-bottom: {b}px solid {theme.CARD_BORDER}; padding: {rem(0.5)}px {rem(0.55)}px; font-weight: bold; }}"
            f"QHeaderView::section:first {{ border-left: none; }}"
            f"QLineEdit {{ background: {theme.CARD_BG}; color: {theme.TEXT_VALUE}; border: {b}px solid {theme.ACCENT};"
            f" border-radius: {rem(0.35)}px; padding: 0 {rem(0.4)}px; }}" + scroll_qss())
        round_view(self.table, rem(0.5))
        hdr = self.table.horizontalHeader()
        hdr.setFont(font(0.72, bold=True, spacing=0.04))
        hdr.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hdr.setHighlightSections(False)
        hdr.setFixedHeight(QFontMetrics(font(0.72, bold=True)).height() + 2 * rem(0.5) + border())
        hdr.setSectionResizeMode(QHeaderView.Fixed)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        for c, w in ((0, 2.6), (2, 6.5), (3, 7.0), (4, 6.5), (5, 6.5), (6, 14.5), (7, 9.0)):
            self.table.setColumnWidth(c, rem(w))
        self.table.verticalHeader().setDefaultSectionSize(row_height())
        self.table.setMouseTracking(True)
        self.table.setFont(font(0.82))
        self.table.setItemDelegateForColumn(0, _NoDelegate(self))
        self.table.setItemDelegateForColumn(3, _UnitDelegate(self))
        self.table.setItemDelegateForColumn(4, _IssuedDelegate(self))
        self.table.setItemDelegateForColumn(6, _FullDelegate(self.table))
        self.table.setItemDelegateForColumn(7, _FullDelegate(self.table))
        self.table.itemChanged.connect(self._item_changed)
        self.table.cellClicked.connect(self._cell_clicked)
        self.table.installEventFilter(self)
        lay.addWidget(self.table, 1)
        self.empty = empty("")
        self.empty.hide()
        lay.addWidget(self.empty, 1, Qt.AlignTop)     # вместо таблицы: сразу под панелью, как у сайта
        self._reload = QTimer(self)
        self._reload.setSingleShot(True)
        self._reload.timeout.connect(lambda: self.load(soft=True))
        self.load()

    # ── данные ───────────────────────────────────────────────────────────────

    def load(self, soft=False):
        if self._loading:
            return
        self._loading = True

        def done(data):
            self._loading = False
            data = data or {}
            self.rows = data.get("rows") or []
            self.units = data.get("units") or []
            self.access = data.get("access") or {}
            self.pending = data.get("pending") or []
            self.editors_btn.setVisible(bool(self.access.get("is_management")))
            self.draw()
            self.draw_pending()

        def failed(e):
            self._loading = False
            if soft:
                return
            self.table.hide()
            self.empty.setText(human_error(e))
            self.empty.show()
        jobs.run(client.get, "projects/%s/warehouse" % self.project_id, done=done, failed=failed, owner=self)

    def row_at(self, i):
        return self._visible[i] if 0 <= i < len(self._visible) else None

    def visible_rows(self):
        needle = self.search.text().strip().lower()
        out = []
        for r in self.rows:
            if self.only_filled and not (r.get("name") or "").strip():
                continue
            if needle and needle not in (r.get("name") or "").lower():
                continue
            out.append(r)
        return out

    def _toggle_filled(self):
        self.only_filled = not self.only_filled
        self.filled_btn.set_kind("on" if self.only_filled else "ghost")
        self.draw()

    # ── таблица ──────────────────────────────────────────────────────────────

    def draw(self):
        rows = self.visible_rows()
        self._visible = rows
        filled = sum(1 for r in self.rows if (r.get("name") or "").strip())
        self.legend.setText("Заполнено строк: %d из %d" % (filled, len(self.rows)))
        t = self.table
        t.blockSignals(True)
        t.clearSpans()
        t.clearContents()
        t.setRowCount(len(rows) or 1)
        goods = bool(self.access.get("can_edit_goods"))
        stock = bool(self.access.get("can_issue"))
        for i, r in enumerate(rows):
            t.setRowHeight(i, row_height())
            self._fill_row(i, r, goods, stock)
        if not rows:
            # как у сайта: строка во всю ширину с «Ничего не найдено» (div.empty, отступ 2rem)
            t.setSpan(0, 0, 1, len(COLS))
            it = self._item("Ничего не найдено", False, Qt.AlignHCenter, color=theme.TEXT_MUTED)
            it.setData(Qt.UserRole + 1, "empty")
            t.setItem(0, 0, it)
            t.setRowHeight(0, QFontMetrics(font(0.82)).height() + 2 * rem(2))
        t.blockSignals(False)
        self.empty.hide()
        t.show()

    def _item(self, text, editable, align=Qt.AlignLeft, tip="", color=None, bold=False):
        it = QTableWidgetItem(text)
        it.setTextAlignment(align | Qt.AlignVCenter)
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if editable:
            flags |= Qt.ItemIsEditable
        it.setFlags(flags)
        if tip:
            it.setToolTip(tip)
        if color:
            it.setForeground(QColor(color))
        if bold:
            f = font(0.82, bold=True)
            it.setFont(f)
        return it

    def _fill_row(self, i, r, goods, stock):
        t = self.table
        no = self._item(str(r.get("row_no")), False, Qt.AlignCenter)
        no.setData(Qt.UserRole, r.get("row_no"))
        t.setItem(i, 0, no)
        name = r.get("name") or ""
        t.setItem(i, 1, self._item(name if (name or goods) else "—", goods, tip=cell_hint(r, "name"),
                                   color=None if (name or goods) else theme.TEXT_MUTED))
        q = r.get("quantity")
        t.setItem(i, 2, self._item(show(q) if q is not None else ("" if goods else "—"), goods, Qt.AlignRight,
                                   cell_hint(r, "quantity")))
        unit = r.get("unit") or ""
        u_item = self._item(unit, False, tip=cell_hint(r, "unit") or
                            ("Выбрать единицу измерения" if goods else unit_title(self._unit(unit))))
        t.setItem(i, 3, u_item)
        clickable = stock and bool(name.strip())
        iss = self._item(show(r.get("issued")) or "0", False, Qt.AlignHCenter,
                         "Нажмите, чтобы выдать товар" if clickable else "")
        iss.setData(Qt.UserRole + 2, clickable)
        iss.setData(Qt.UserRole + 3, ("+" + show(r.get("pending"))) if r.get("pending") else "")
        t.setItem(i, 4, iss)
        if q is None:
            t.setItem(i, 5, self._item("—", False, Qt.AlignHCenter, color=theme.TEXT_MUTED))
        else:
            left = round((q - (r.get("issued") or 0)) * 1000) / 1000
            t.setItem(i, 5, self._item(show(left), False, Qt.AlignHCenter, color=theme.SUCCESS if left <= 0 else None,
                                       bold=left <= 0))
        t.setItem(i, 6, self._item("", False))
        t.setCellWidget(i, 6, self._stock_cell(r, stock))
        t.setItem(i, 7, self._item("", False))
        t.setCellWidget(i, 7, self._actions(r, stock))
        f = row_flags(r)
        tip = "На складе недостаточно товара" if f["short"] else ("Выдано полностью" if f["done"] else "")
        if tip:
            for c in (0, 2, 4, 5):
                it = t.item(i, c)
                if it is not None and not it.toolTip():
                    it.setToolTip(tip)

    def _unit(self, name):
        return next((u for u in self.units if u.get("name") == name), {"name": name} if name else None)

    def _stock_cell(self, r, allowed):
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(rem(0.3), 0, rem(0.3), 0)
        lay.setSpacing(rem(0.3))
        state = r.get("stock_state") or ""
        if not allowed:
            lay.setContentsMargins(rem(0.6), 0, rem(0.6), 0)
        if not allowed:
            text = dict(STATES).get(state, "—")
            kind = "success" if state == "enough" else ("danger" if state == "short" else "default")
            lay.addWidget(Chip(text, kind))
            if r.get("stock_qty") is not None:
                lay.addWidget(label(show(r["stock_qty"]), 0.82))
            lay.addStretch(1)
            w.setToolTip(cell_hint(r, "stock_state"))
            return w
        col = theme.SUCCESS if state == "enough" else (theme.CRITICAL if state == "short" else theme.TEXT_VALUE)
        sel = _CellSelect(STATES, col, bold=bool(state))
        sel.set_value(state)
        sel.setFixedWidth(rem(8.4))
        qty = QLineEdit(show(r.get("stock_qty")) if r.get("stock_qty") is not None else "")
        qty.setPlaceholderText("сколько")
        qty.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        qty.setFont(font(0.82))
        qty.setStyleSheet(_cell_qss("QLineEdit"))
        qty.setFixedHeight(rem(2.3))
        qty.setMinimumWidth(rem(3.2))
        sel.setToolTip(cell_hint(r, "stock_state"))

        def changed(_i):
            v = sel.value() or None
            qty.setVisible(bool(v))
            self.save(r, {"stock_state": v}, ["stock_state"])
        sel.currentIndexChanged.connect(changed)
        before = {"v": qty.text()}

        def done():
            if qty.text() == before["v"]:
                return
            parsed = num(qty.text())
            if parsed is not None and (parsed != parsed or parsed < 0):
                toast(self, "Количество — неотрицательное число", "warning")
                qty.setText(before["v"])
                return
            before["v"] = qty.text()
            self.save(r, {"stock_qty": parsed}, ["stock_qty"])
        qty.editingFinished.connect(done)
        lay.addWidget(sel)
        lay.addWidget(qty, 1)
        lay.addStretch(0)       # без количества список остаётся слева
        # видимость — после того как поле в ячейке: поле без родителя на миг становилось пустым окном
        qty.setVisible(bool(state))
        return w

    def _actions(self, r, stock):
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, rem(0.3), 0)
        lay.setSpacing(rem(0.25))
        lay.addStretch(1)
        if stock and (r.get("name") or "").strip():
            give = Button("Выдать", "ghost", "download", "Выдать товар сотруднику")
            give.setFont(font(0.74, weight=QFont.DemiBold))
            give.setStyleSheet(give.styleSheet() + f"QPushButton {{ padding: 0 {rem(0.5)}px; font-size: {fs(0.74)}; }}")
            give.clicked.connect(lambda: self.issue_dialog(r))
            lay.addWidget(give)
        hist = Button("", "ghost", "clock", "Кто и когда менял строку")
        hist.clicked.connect(lambda: self.history_dialog(r))
        lay.addWidget(hist)
        return w

    def _item_changed(self, item):
        r = self.row_at(item.row())
        if r is None:
            return
        col = item.column()
        if col == 1:
            new = item.text().strip()
            if new == (r.get("name") or ""):
                return
            self.save(r, {"name": new}, ["name"])
        elif col == 2:
            parsed = num(item.text())
            if parsed is not None and (parsed != parsed or parsed < 0):
                toast(self, "Количество — неотрицательное число", "warning")
                self.table.blockSignals(True)
                item.setText(show(r.get("quantity")))
                self.table.blockSignals(False)
                return
            if parsed == r.get("quantity"):
                return
            self.save(r, {"quantity": parsed}, ["quantity"])

    def _cell_clicked(self, row, col):
        r = self.row_at(row)
        if r is None:
            return
        if col == 3 and self.access.get("can_edit_goods"):
            rect = self.table.visualItemRect(self.table.item(row, col))
            self.unit_popup(r, self.table.viewport().mapToGlobal(rect.bottomLeft()))
        elif col == 4 and self.access.get("can_issue") and (r.get("name") or "").strip():
            self.issue_dialog(r)

    def save(self, r, patch, fields):
        body = dict(patch)
        body["fields"] = fields

        def done(res):
            r.update((res or {}).get("row") or {})
            self._refresh(r)

        def failed(e):
            toast(self, human_error(e), "error")
            self._refresh(r)
        jobs.run(client.patch, "projects/%s/warehouse/%s" % (self.project_id, r.get("row_no")), json_body=body,
                 done=done, failed=failed, owner=self)

    def _refresh(self, r):
        try:
            i = self._visible.index(r)
        except ValueError:
            self.draw()
            return
        self.table.blockSignals(True)
        self._fill_row(i, r, bool(self.access.get("can_edit_goods")), bool(self.access.get("can_issue")))
        self.table.blockSignals(False)
        filled = sum(1 for x in self.rows if (x.get("name") or "").strip())
        self.legend.setText("Заполнено строк: %d из %d" % (filled, len(self.rows)))

    # ── ожидающие выдачи ─────────────────────────────────────────────────────

    def draw_pending(self):
        clear_layout(self.pending_box)
        for p in self.pending or []:
            mine = p.get("mine")
            row = next((x for x in self.rows if x.get("row_no") == p.get("row_no")), None)
            who = ("от " + ((p.get("issuer") or {}).get("name") or "склада")) if mine else \
                ("для " + ((p.get("receiver") or {}).get("name") or "сотрудника"))
            card = pending_card((row or {}).get("name") or "Строка %s" % p.get("row_no"),
                                "%s %s · %s" % (show(p.get("quantity")), (row or {}).get("unit") or "", who), mine,
                                lambda what, pp=p: self.act(what, pp))
            self.pending_box.addWidget(card)

    def act(self, what, p):
        if what == "cancel" and not confirm(self, "Отмена выдачи", "Отменить выдачу до подтверждения?",
                                            "Отменить выдачу", danger=True):
            return

        def done(_):
            toast(self, "Получение подтверждено" if what == "confirm" else "Выдача отменена", "success")
            self.load()
        jobs.run(client.post, "warehouse/issues/%s/%s" % (p.get("id"), what), json_body={}, done=done,
                 failed=lambda e: toast(self, human_error(e), "error"), owner=self)

    # ── единица измерения ────────────────────────────────────────────────────

    def unit_popup(self, r, pos):
        pop = QFrame(self.window(), Qt.Popup | Qt.FramelessWindowHint)
        pop.setObjectName("unitPop")
        pop.setStyleSheet(f"QFrame#unitPop {{ background: {theme.CARD_BG}; border: {border()}px solid {theme.CARD_BORDER};"
                          f" border-radius: {rem(0.5)}px; }}")
        lay = QVBoxLayout(pop)
        lay.setContentsMargins(rem(0.4), rem(0.4), rem(0.4), rem(0.4))
        lay.setSpacing(rem(0.3))
        search = line_edit("Поиск единицы…")
        lay.addWidget(search)
        holder = QWidget()
        lst = QVBoxLayout(holder)
        lst.setContentsMargins(0, 0, 0, 0)
        lst.setSpacing(rem(0.1))
        area = scroll_area(holder)
        area.setFixedHeight(rem(13))
        lay.addWidget(area)
        add = Button("Новая единица — в справочнике", "link", "plus")
        add.clicked.connect(lambda: (pop.close(), self.units_dialog()))
        lay.addWidget(add)

        def pick(name):
            pop.close()
            if name == (r.get("unit") or ""):
                return
            self.save(r, {"unit": name}, ["unit"])

        def render():
            clear_layout(lst)
            needle = search.text().strip().lower()
            opts = [("", "—", "без единицы")] + [(u["name"], u["name"], u.get("full_name") or "") for u in self.units
                                                if not needle or needle in u["name"].lower()
                                                or needle in (u.get("full_name") or "").lower()]
            for val, short, full in opts:
                on = val == (r.get("unit") or "")
                b = QPushButton("%s   %s" % (short, full))
                b.setCursor(Qt.PointingHandCursor)
                b.setFont(font(0.82, bold=on))
                b.setStyleSheet(f"QPushButton {{ text-align: left; border: none; border-radius: {rem(0.35)}px;"
                                f" padding: {rem(0.35)}px {rem(0.5)}px; color: {theme.TEXT_VALUE};"
                                f" background: {theme.INFO_SOFT if on else 'transparent'}; }}"
                                f"QPushButton:hover {{ background: {theme.CHIP_BG}; }}")
                b.clicked.connect(lambda _=False, v=val: pick(v))
                lst.addWidget(b)
            if len(opts) == 1 and needle:
                lst.addWidget(muted("Ничего не найдено", 0.8))
            lst.addStretch(1)
        search.textChanged.connect(lambda _t: render())
        search.returnPressed.connect(lambda: pick(next((u["name"] for u in self.units if search.text().strip().lower()
                                                        in u["name"].lower()), r.get("unit") or "")))
        render()
        pop.setFixedWidth(rem(16))
        scr = QApplication.screenAt(pos) or QApplication.primaryScreen()
        g = scr.availableGeometry()
        pop.adjustSize()
        if pos.y() + pop.height() > g.bottom():
            pos = QPoint(pos.x(), pos.y() - pop.height() - rem(2.6))
        pop.move(pos)
        pop.show()
        search.setFocus()

    # ── вставка из буфера ────────────────────────────────────────────────────

    def eventFilter(self, obj, e):
        if obj is self.table and e.type() == QEvent.KeyPress and e.matches(QKeySequence.Paste):
            cur = self.table.currentItem()
            if cur is not None and cur.column() == 1 and self.access.get("can_edit_goods"):
                text = QApplication.clipboard().text()
                if re.search(r"[\t\n;]", text or ""):
                    parsed = parse_paste(text)
                    if parsed.get("error"):
                        toast(self, parsed["error"], "warning")
                    else:
                        r = self.row_at(cur.row())
                        self.paste_dialog(int((r or {}).get("row_no") or 1), parsed)
                    return True
        return super().eventFilter(obj, e)

    def paste_dialog(self, start, parsed):
        free = len(self.rows) - start + 1
        fits = len(parsed["rows"]) <= free
        m = Modal(self, "Вставка из буфера", "table", width_rem=52)
        head = QHBoxLayout()
        head.addWidget(label("Будет заполнено строк: %d" % len(parsed["rows"]), 1.0, theme.TEXT_VALUE, bold=True))
        head.addWidget(muted("начиная со строки %d" % start, 0.875))
        head.addStretch(1)
        m.body.addLayout(head)
        cols = parsed.get("columns") or 1
        m.body.addWidget(label("Столбцы распознаны так: товар" + (", количество" if cols > 1 else "") +
                               (", единица" if cols > 2 else "") + ". Существующие значения в этих строках будут заменены.",
                               0.75, theme.TEXT_MUTED, wrap=True))
        prev = QFrame()
        prev.setObjectName("pastePrev")
        prev.setStyleSheet(f"QFrame#pastePrev {{ border: {border()}px solid {theme.CARD_BORDER}; border-radius: {rem(0.5)}px; }}")
        pl = QVBoxLayout(prev)
        pl.setContentsMargins(rem(0.35), rem(0.35), rem(0.35), rem(0.35))
        pl.setSpacing(rem(0.15))
        for k, row in enumerate(parsed["rows"][:8]):
            rl = QHBoxLayout()
            rl.addWidget(muted(str(start + k), 0.82))
            rl.addWidget(label(row["name"], 0.82), 1)
            rl.addWidget(label("—" if row["quantity"] is None else show(row["quantity"]), 0.82))
            rl.addWidget(muted(row.get("unit") or "", 0.82))
            pl.addLayout(rl)
        if len(parsed["rows"]) > 8:
            pl.addWidget(muted("…и ещё %d строк" % (len(parsed["rows"]) - 8), 0.75))
        m.body.addWidget(prev)
        probs = parsed.get("problems") or []
        if probs or not fits:
            box = QLabel()
            box.setWordWrap(True)
            box.setFont(font(0.8))
            box.setStyleSheet(f"background: {theme.WARNING_SOFT}; color: {theme.WARNING_TEXT}; border-radius: {rem(0.5)}px;"
                              f" padding: {rem(0.5)}px {rem(0.7)}px;")
            text = ""
            if probs:
                text += "<b>Пропущено строк: %d</b><br>" % len(probs) + "<br>".join(probs[:6])
                if len(probs) > 6:
                    text += "<br>…и ещё %d" % (len(probs) - 6)
            if not fits:
                text += ("<br>" if text else "") + "Не помещается: со строки %d свободно %d строк" % (start, free)
            box.setText(text)
            m.body.addWidget(box)

        def go(_m):
            if not fits:
                toast(m, "Выберите строку выше — не помещается", "warning")
                return False
            try:
                res = client.post("projects/%s/warehouse/bulk" % self.project_id,
                                  json_body={"start_row": start, "rows": parsed["rows"]})
            except Exception as e:
                toast(m, human_error(e), "error")
                return False
            toast(self, "Вставлено строк: %s" % ((res or {}).get("count") or len(parsed["rows"])), "success")
            self.load()
            return True
        m.add_button("Отмена", "ghost")
        m.add_button("Вставить", "primary", "check", go)
        m.exec()

    # ── выдача ───────────────────────────────────────────────────────────────

    def issue_dialog(self, r):
        q = r.get("quantity")
        left = None if q is None else round((q - (r.get("issued") or 0) - (r.get("pending") or 0)) * 1000) / 1000
        m = Modal(self, "Выдача товара", "download", width_rem=36)
        head = QHBoxLayout()
        head.addWidget(label(r.get("name") or "Строка %s" % r.get("row_no"), 1.0, theme.TEXT_VALUE, bold=True))
        if left is not None:
            head.addWidget(muted("Можно выдать: %s %s" % (show(left), r.get("unit") or ""), 0.875))
        head.addStretch(1)
        m.body.addLayout(head)
        qty = line_edit("Сколько выдаём")
        m.body.addWidget(field("Количество", qty, True))
        state = {"receiver": None}
        who_box = QHBoxLayout()
        who = QHBoxLayout()
        who.addWidget(muted("Сотрудник не выбран", 0.875))
        who_w = QWidget()
        who_w.setLayout(who)
        who.setContentsMargins(0, 0, 0, 0)
        who_box.addWidget(who_w, 1)
        pick = Button("Выбрать сотрудника", "ghost", "users")

        def picked(ids):
            emp = next((e for e in store.employees() if e.get("id") == ids[0]), {"id": ids[0]})
            state["receiver"] = emp
            clear_layout(who)
            who.addWidget(avatar(emp, 2.0))
            lab = QLabel("%s <span style='color:%s'> — %s</span>" % (
                full_name(emp, True), theme.TEXT_MUTED, ", ".join(x for x in (emp.get("department"), emp.get("position")) if x)))
            lab.setFont(font(0.875))
            who.addWidget(lab, 1)
        pick.clicked.connect(lambda: employee_picker(m, "Кому выдаём товар", picked, False, "Выбрать"))
        who_box.addWidget(pick)
        bw = QWidget()
        bw.setLayout(who_box)
        who_box.setContentsMargins(0, 0, 0, 0)
        m.body.addWidget(field("Кому выдаём", bw, True))
        m.body.addWidget(label("Выдача попадёт в таблицу только после того, как сотрудник подтвердит получение. "
                               "До подтверждения её можно отменить.", 0.75, theme.TEXT_MUTED, wrap=True))

        def go(_m):
            value = num(qty.text())
            if not value or value != value or value <= 0:
                toast(m, "Укажите количество больше нуля", "warning")
                return False
            if not state["receiver"]:
                toast(m, "Выберите сотрудника, которому выдаёте", "warning")
                return False
            try:
                client.post("projects/%s/warehouse/%s/issue" % (self.project_id, r.get("row_no")),
                            json_body={"quantity": value, "receiver_id": state["receiver"]["id"]})
            except Exception as e:
                toast(m, human_error(e), "error")
                return False
            toast(self, "Выдача отправлена на подтверждение", "success")
            self.load()
            return True
        m.add_button("Отмена", "ghost")
        m.add_button("Выдать", "primary", "check", go)
        qty.setFocus()
        m.exec()

    def history_dialog(self, r):
        from kov.journal import open_journal
        unit = (" " + r["unit"]) if r.get("unit") else ""
        states = {"confirmed": "подтверждено", "cancelled": "отменено", "pending": "ждёт подтверждения"}

        def load():
            data = client.get("projects/%s/warehouse/%s/history" % (self.project_id, r.get("row_no"))) or {}
            issues = []
            for i in data.get("issues") or []:
                text = "%s → %s" % ((i.get("issuer") or {}).get("name") or "—", (i.get("receiver") or {}).get("name") or "—")
                if i.get("confirmed_at"):
                    text += " · принято " + fmt_datetime(i["confirmed_at"])
                if i.get("cancelled_at"):
                    text += " · отменено " + fmt_datetime(i["cancelled_at"])
                issues.append({"kind": "issue", "action": i.get("status"), "who": i.get("issuer"), "target": i.get("receiver"),
                               "at": i.get("at"), "title": show(i.get("quantity")) + unit + " · " +
                               states.get(i.get("status"), i.get("status") or ""), "text": text})
            edits = [{"kind": "edit", "action": "changed", "who": e.get("who"), "at": e.get("at"), "title": e.get("field_title"),
                      "text": "%s → %s" % (e.get("old") or "—", e.get("new") or "—")} for e in data.get("edits") or []]
            return {"issues": issues, "edits": edits}
        open_journal(self, "Журнал строки № %s" % r.get("row_no"), r.get("name") or "Без названия", load, always_issues=True)

    # ── справочник единиц ────────────────────────────────────────────────────

    def units_dialog(self):
        try:
            data = client.get("warehouse/units/full") or {}
        except Exception as e:
            toast(self, human_error(e), "error")
            return
        m = Modal(self, "Единицы измерения", "table", width_rem=52)
        state = {"data": data}
        if data.get("can_add"):
            box = QVBoxLayout()
            box.addWidget(label("Новая единица", 0.78, theme.TEXT_LABEL))
            row = QHBoxLayout()
            new_short = line_edit("м³")
            new_short.setMaxLength(32)
            new_short.setFixedWidth(rem(9))
            new_full = line_edit("Кубический метр")
            new_full.setMaxLength(64)
            add = Button("Добавить", "primary", "plus")
            row.addWidget(new_short)
            row.addWidget(new_full, 1)
            row.addWidget(add)
            box.addLayout(row)
            m.body.addLayout(box)

            def do_add():
                name = new_short.text().strip()
                if not name:
                    toast(m, "Введите краткое обозначение", "warning")
                    return
                try:
                    client.post("warehouse/units", json_body={"name": name, "full_name": new_full.text().strip()})
                except Exception as e:
                    toast(m, human_error(e), "error")
                    return
                new_short.clear()
                new_full.clear()
                toast(m, "Единица добавлена", "success")
                refresh()
            add.clicked.connect(do_add)
            new_short.returnPressed.connect(new_full.setFocus)
            new_full.returnPressed.connect(do_add)
        head = QHBoxLayout()
        for text, w in (("Кратко", 9), ("Полное название", 0), ("Где стоит", 8)):
            lab = label(text.upper(), 0.72, theme.TEXT_LABEL, bold=True)
            if w:
                lab.setFixedWidth(rem(w))
            head.addWidget(lab, 0 if w else 1)
        head.addSpacing(rem(4.6))
        m.body.addLayout(head)
        holder = QWidget()
        lst = QVBoxLayout(holder)
        lst.setContentsMargins(0, 0, 0, 0)
        lst.setSpacing(rem(0.35))
        area = scroll_area(holder)
        area.setMinimumHeight(rem(10))
        area.setMaximumHeight(rem(22))
        m.body.addWidget(area)
        if data.get("can_manage"):
            m.body.addWidget(label("Переименование меняет единицу во всех строках всех проектов. Удалить можно только свободную.",
                                   0.75, theme.TEXT_MUTED, wrap=True))

        def refresh():
            try:
                self.units = client.get("warehouse/units") or self.units
                state["data"] = client.get("warehouse/units/full") or state["data"]
            except Exception:
                pass
            fill()
            self.draw()

        def fill():
            clear_layout(lst)
            d = state["data"]
            if not d.get("units"):
                lst.addWidget(empty("Пока ни одной единицы"))
            for u in d.get("units") or []:
                row = QHBoxLayout()
                short = line_edit("", u.get("name") or "")
                short.setMaxLength(32)
                short.setFixedWidth(rem(9))
                short.setEnabled(bool(d.get("can_manage")))
                full = line_edit("Полное название", u.get("full_name") or "")
                full.setMaxLength(64)
                full.setEnabled(bool(d.get("can_manage")))
                used = u.get("used")
                chip = Chip("в строках: %s" % used if used else "свободна", "default" if used else "info")
                chip.setFixedWidth(rem(8))
                save = Button("", "ghost", "check", "Сохранить")
                save.setEnabled(False)
                dele = Button("", "ghost", "trash", "Стоит в строках — сначала замените её там" if used else "Удалить")
                dele.setEnabled(bool(d.get("can_manage")) and not used)

                def dirty(_t=None, s=short, f=full, b=save, uu=u):
                    b.setEnabled(s.text().strip() != uu.get("name") or f.text().strip() != (uu.get("full_name") or ""))
                short.textChanged.connect(dirty)
                full.textChanged.connect(dirty)

                def do_save(_=False, s=short, f=full, uu=u):
                    ns, nf = s.text().strip(), f.text().strip()
                    if not ns:
                        toast(m, "Краткое обозначение пустое", "warning")
                        s.setText(uu.get("name") or "")
                        return
                    try:
                        res = client.patch("warehouse/units/%s" % uu.get("id"), json_body={"name": ns, "full_name": nf}) or {}
                    except Exception as e:
                        toast(m, human_error(e), "error")
                        s.setText(uu.get("name") or "")
                        f.setText(uu.get("full_name") or "")
                        return
                    toast(m, "Сохранено, обновлено строк: %s" % res["moved"] if res.get("moved") else "Сохранено", "success")
                    refresh()
                save.clicked.connect(do_save)

                def do_del(_=False, uu=u):
                    if not confirm(m, "Удаление единицы", "Убрать «%s» из справочника?" % uu.get("name"), "Удалить", danger=True):
                        return
                    try:
                        client.delete("warehouse/units/%s" % uu.get("id"))
                    except Exception as e:
                        toast(m, human_error(e), "error")
                        return
                    toast(m, "Единица удалена", "success")
                    refresh()
                dele.clicked.connect(do_del)
                for w_ in (short,):
                    row.addWidget(w_)
                row.addWidget(full, 1)
                row.addWidget(chip)
                row.addWidget(save)
                row.addWidget(dele)
                lst.addLayout(row)
            lst.addStretch(1)
        fill()
        m.foot.hide()
        m.exec()

    # ── кто правит товары ────────────────────────────────────────────────────

    def editors_dialog(self):
        try:
            data = client.get("warehouse/editors") or {}
        except Exception as e:
            toast(self, human_error(e), "error")
            return
        data.setdefault("people", [])
        data.setdefault("user_ids", [])
        m = Modal(self, "Кто заполняет «Товар» и «Количество»", "users", width_rem=36)
        note = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(icons.pixmap("info", rem(1.0), theme.INFO))
        note.addWidget(ic)
        note.addWidget(label("Список общий для всех проектов — и нынешних, и будущих", 0.8, theme.TEXT_LABEL, wrap=True), 1)
        m.body.addLayout(note)
        lst = QVBoxLayout()
        lst.setSpacing(rem(0.35))
        m.body.addLayout(lst)

        def fill():
            clear_layout(lst)
            if not data["people"]:
                lst.addWidget(empty("Никто пока не добавлен"))
            for p in data["people"]:
                row = QFrame()
                row.setObjectName("whEditor")
                row.setStyleSheet(f"QFrame#whEditor {{ border: {border()}px solid {theme.CARD_BORDER}; border-radius: {rem(0.5)}px; }}")
                rl = QHBoxLayout(row)
                rl.setContentsMargins(rem(0.4), rem(0.3), rem(0.4), rem(0.3))
                rl.addWidget(avatar({"id": p.get("id"), "last_name": p.get("last_name"), "first_name": p.get("first_name")}, 1.9))
                rl.addWidget(label(p.get("name") or "", 0.875), 1)
                x = Button("", "ghost", "close", "Убрать")

                def drop(_=False, pid=p.get("id")):
                    data["user_ids"] = [i for i in data["user_ids"] if i != pid]
                    data["people"] = [q for q in data["people"] if q.get("id") != pid]
                    fill()
                x.clicked.connect(drop)
                rl.addWidget(x)
                lst.addWidget(row)

        def added(ids):
            all_ = store.employees()
            for i in ids:
                if i in data["user_ids"]:
                    continue
                e = next((x for x in all_ if x.get("id") == i), {})
                data["user_ids"].append(i)
                data["people"].append({"id": i, "name": full_name(e, True), "last_name": e.get("last_name"),
                                       "first_name": e.get("first_name")})
            fill()
        add = Button("Добавить сотрудника", "ghost", "plus")
        add.clicked.connect(lambda: employee_picker(m, "Кому разрешить правку товаров", added, True, "Добавить", True))
        m.body.addWidget(add, 0, Qt.AlignLeft)
        fill()

        def save(_m):
            try:
                client.put("warehouse/editors", json_body={"user_ids": data["user_ids"]})
            except Exception as e:
                toast(m, human_error(e), "error")
                return False
            toast(self, "Список сохранён", "success")
            self.load()
            return True
        m.add_button("Отмена", "ghost")
        m.add_button("Сохранить", "primary", "save", save)
        m.exec()

    # ── события ──────────────────────────────────────────────────────────────

    def on_event(self, evt):
        kind = evt.get("type")
        own = evt.get("actor_id") is not None and evt.get("actor_id") == self.access.get("user_id")
        if kind == "warehouse.updated":
            glob = evt.get("what") in ("units", "editors")
            if not glob and str(evt.get("project_id")) != str(self.project_id):
                return
            if own:
                return
            self._reload.start(400)
            return
        if kind != "warehouse.issue" or str(evt.get("project_id")) != str(self.project_id):
            return
        self._reload.start(250)
        if not own:
            what = "«%s»" % evt["name"] if evt.get("name") else "товар"
            uid = self.access.get("user_id")
            if evt.get("action") == "created" and evt.get("receiver_id") == uid:
                toast(self, "Вам выдают %s — подтвердите получение" % what, "info")
            elif evt.get("action") == "confirmed" and evt.get("issuer_id") == uid:
                toast(self, "Получение %s подтверждено" % what, "success")
            elif evt.get("action") == "cancelled":
                toast(self, "Выдача %s отменена" % what, "warning")


def parse_paste(text):
    raw = re.sub(r"\r\n?", "\n", str(text or "")).rstrip("\n")
    if not raw.strip():
        return {"error": "Буфер пуст"}
    lines = [ln for ln in raw.split("\n") if ln.strip() != ""]
    if len(lines) > ROWS_LIMIT:
        return {"error": "За раз можно вставить не больше %d строк" % ROWS_LIMIT}
    sep = "\t" if "\t" in lines[0] else (";" if any(";" in ln for ln in lines) else None)
    rows, problems = [], []
    for i, line in enumerate(lines):
        cells = [c.strip().strip('"') for c in (line.split(sep) if sep else [line])]
        if len(cells) > 3:
            problems.append("Строка %d: больше трёх столбцов" % (i + 1))
            continue
        name = cells[0] if cells else ""
        if not name:
            problems.append("Строка %d: нет названия товара" % (i + 1))
            continue
        if len(name) > 255:
            problems.append("Строка %d: название длиннее 255 знаков" % (i + 1))
            continue
        quantity = None
        if len(cells) > 1 and cells[1] != "":
            cleaned = re.sub(r"[\s ]", "", cells[1]).replace(",", ".", 1)
            try:
                quantity = float(cleaned)
            except ValueError:
                problems.append("Строка %d: «%s» — не число" % (i + 1, cells[1]))
                continue
            if quantity < 0:
                problems.append("Строка %d: количество отрицательное" % (i + 1))
                continue
        unit = cells[2][:32] if len(cells) > 2 else None
        rows.append({"name": name, "quantity": quantity, "unit": unit or None})
    if not rows:
        return {"error": problems[0] if problems else "Ничего подходящего не нашлось", "problems": problems}
    return {"rows": rows, "problems": problems, "columns": min(3, len(lines[0].split(sep))) if sep else 1}


def pending_card(title, sub, mine, act, project_line=None, on_project=None):
    """Карточка выдачи, ждущей подтверждения (склад и «Уведомления»)."""
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
    circ.setPixmap(icons.pixmap("download" if mine else "clock", rem(1.05), theme.ACCENT))
    lay.addWidget(circ)
    who = QVBoxLayout()
    who.setSpacing(0)
    who.addWidget(label(title, 0.86, theme.TEXT_VALUE, bold=True))
    who.addWidget(label(sub, 0.74, theme.TEXT_LABEL))
    if project_line:
        link = Button(project_line, "link")
        link.setStyleSheet(link.styleSheet().replace("text-decoration: underline;", ""))
        if on_project:
            link.clicked.connect(on_project)
        who.addWidget(link, 0, Qt.AlignLeft)
    lay.addLayout(who, 1)
    if mine:
        ok = Button("Подтвердить получение" if not project_line else "Подтвердить", "primary", "check")
        ok.clicked.connect(lambda: act("confirm"))
        lay.addWidget(ok)
    no = Button("Отказаться" if mine else "Отменить", "ghost", "close")
    no.clicked.connect(lambda: act("cancel"))
    lay.addWidget(no)
    return card
