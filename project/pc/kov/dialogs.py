# -*- coding: utf-8 -*-
"""Окна в оформлении сайта (Kov.UI.modal / confirm): шапка-градиент со
значком и заголовком, тело, подвал с кнопками справа."""

from PySide6.QtCore import QDate, QPoint, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import (QApplication, QDateEdit, QDialog, QFrame, QHBoxLayout,
                             QLabel, QLineEdit, QScrollArea, QTableView, QToolButton, QVBoxLayout, QWidget)

from general_functions import theme
from kov import icons
from kov.ui import Button, font, label, rem, border


class _Head(QFrame):
    def __init__(self, radius, parent=None):
        super().__init__(parent)
        self._r = radius

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect())
        path = QPainterPath()
        path.moveTo(r.left(), r.bottom())
        path.lineTo(r.left(), r.top() + self._r)
        path.quadTo(r.left(), r.top(), r.left() + self._r, r.top())
        path.lineTo(r.right() - self._r, r.top())
        path.quadTo(r.right(), r.top(), r.right(), r.top() + self._r)
        path.lineTo(r.right(), r.bottom())
        path.closeSubpath()
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        g.setColorAt(0.0, QColor(theme.HEADER_TOP))
        g.setColorAt(0.6, QColor(theme.HEADER_MID))
        g.setColorAt(1.0, QColor(theme.HEADER_BOTTOM))
        p.fillPath(path, g)
        p.end()


class Modal(QDialog):
    """Окно сайта. buttons: [(текст, вид, значок, обработчик(modal) → bool|None)].

    Обработчик, вернувший False, оставляет окно открытым (например, ошибка
    проверки полей); без обработчика кнопка закрывает окно с отказом.
    """

    def __init__(self, parent=None, title="", glyph=None, width_rem=36, buttons=None, scroll=False):
        super().__init__(parent.window() if parent is not None else None)
        from kov.ui import translucent_ok
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self._glass = translucent_ok()
        if self._glass:
            self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self.result_value = None
        self._drag = None
        self._scrim = None
        margin = rem(1.0) if self._glass else 0
        outer = QVBoxLayout(self)
        outer.setContentsMargins(margin, margin, margin, margin)
        self.card = QFrame(self)
        self.card.setObjectName("modalCard")
        radius = rem(1.0)
        self.card.setStyleSheet(f"QFrame#modalCard {{ background: {theme.CARD_BG}; border-radius: {radius}px;"
                                f" border: {border()}px solid {theme.CARD_BORDER}; }}")
        # тень рисует само окно (paintEvent), без QGraphicsDropShadowEffect: с ним в прозрачном окне
        # Windows карточка перерисовывалась кусками и оставалась затемнённой после вложенного окна
        self._radius = radius
        outer.addWidget(self.card)
        col = QVBoxLayout(self.card)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        self.head = _Head(radius)
        hl = QHBoxLayout(self.head)
        hl.setContentsMargins(rem(1.0), rem(0.7), rem(0.7), rem(0.7))
        hl.setSpacing(rem(0.6))
        if glyph:
            ic = QLabel()
            ic.setPixmap(icons.pixmap(glyph, rem(1.4), "#FFFFFF"))
            ic.setStyleSheet("background: transparent;")
            hl.addWidget(ic)
        self.title = label(title, 0.98, "#FFFFFF", bold=True)
        hl.addWidget(self.title, 1)
        self.head_layout = hl
        close = QToolButton()
        self.close_btn = close
        close.setIcon(icons.icon("close", rem(1.0), "#FFFFFF"))
        close.setIconSize(QSize(rem(1.0), rem(1.0)))      # не от DPI экрана (масштаб Windows 125–150 %)
        close.setCursor(Qt.PointingHandCursor)
        close.setStyleSheet(f"QToolButton {{ border: none; background: transparent; border-radius: {rem(1)}px;"
                            f" width: {rem(2)}px; height: {rem(2)}px; }}"
                            f"QToolButton:hover {{ background: rgba(255,255,255,46); }}")
        close.clicked.connect(self.reject)
        hl.addWidget(close)
        col.addWidget(self.head)

        self.body_widget = QWidget()
        self.body_widget.setStyleSheet("background: transparent;")
        self.body = QVBoxLayout(self.body_widget)
        self.body.setContentsMargins(rem(1.2), rem(1.0), rem(1.2), rem(0.4))
        self.body.setSpacing(rem(0.7))
        self._area = None
        if scroll:
            area = QScrollArea()
            self._area = area
            area.setWidgetResizable(True)
            area.setFrameShape(QFrame.NoFrame)
            area.setStyleSheet("QScrollArea { background: transparent; border: none; }")
            area.setWidget(self.body_widget)
            col.addWidget(area, 1)
        else:
            col.addWidget(self.body_widget, 1)

        self.foot = QFrame()
        self.foot.setObjectName("modalFoot")
        self.foot.setStyleSheet(f"QFrame#modalFoot {{ background: {theme.SURFACE_FOOT}; border-top: {border()}px solid {theme.CARD_BORDER};"
                                f" border-bottom-left-radius: {radius}px; border-bottom-right-radius: {radius}px; }}")
        self.foot_layout = QHBoxLayout(self.foot)
        self.foot_layout.setContentsMargins(rem(1.2), rem(0.75), rem(1.2), rem(0.75))
        self.foot_layout.setSpacing(rem(0.5))
        self.foot_layout.addStretch(1)
        self._foot_gap = rem(0.8)
        col.addSpacing(self._foot_gap)
        self._col = col
        col.addWidget(self.foot)

        self.setFixedWidth(rem(width_rem) + 2 * margin)
        self.buttons = []
        for spec in (buttons or []):
            self.add_button(*spec)

    def add_head_button(self, glyph, tip, handler, color="#FFFFFF"):
        """Кнопка в шапке окна (как headButtons сайта): полупрозрачная, перед крестиком."""
        b = QToolButton()
        size = rem(1.05)
        b.setIcon(icons.icon(glyph, size, color))
        b.setIconSize(QSize(size, size))
        b.setToolTip(tip)
        b.setCursor(Qt.PointingHandCursor)
        side = rem(2.25)
        b.setFixedSize(side, side)
        b.setStyleSheet(f"QToolButton {{ background: rgba(255,255,255,41); border: {border()}px solid rgba(255,255,255,77);"
                        f" border-radius: {rem(0.5)}px; }} QToolButton:hover {{ background: rgba(255,255,255,71); }}")
        b.clicked.connect(lambda _=False: handler())
        self.head_layout.insertWidget(self.head_layout.indexOf(self.close_btn), b)
        return b

    def hide_foot(self):
        """Окно без кнопок внизу — подвала нет (как у сайта)."""
        self.foot.hide()
        for i in range(self._col.count()):
            item = self._col.itemAt(i)
            if item is not None and item.spacerItem() is not None:
                item.spacerItem().changeSize(0, 0)
        self._col.invalidate()

    def add_button(self, text, kind="primary", glyph=None, handler=None, left=False):
        b = Button(text, kind, glyph)

        def clicked():
            if handler is None:
                self.reject()
                return
            ok = handler(self)
            if ok is not False:
                self.accept()
        b.clicked.connect(clicked)
        if left:
            self.foot_layout.insertWidget(0, b)
        else:
            self.foot_layout.addWidget(b)
        self.buttons.append(b)
        return b

    def showEvent(self, event):
        super().showEvent(event)
        self._dim(True)
        QTimer.singleShot(0, self._center)
        QTimer.singleShot(0, self._soft_focus)

    def _soft_focus(self):
        """Как softFocus сайта: курсор в конце поля, текст не выделен (Qt выделяет весь при показе окна)."""
        try:
            fw = self.focusWidget()
            if isinstance(fw, QLineEdit) and fw.hasSelectedText() and fw.selectedText() == fw.text():
                fw.deselect()
                fw.end(False)
        except RuntimeError:
            pass

    def hideEvent(self, event):
        self._dim(False)
        super().hideEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._glass:
            from kov.ui import rounded_mask
            rounded_mask(self, self._radius)

    def paintEvent(self, event):
        if self._glass:
            from kov.ui import soft_shadow
            p = QPainter(self)
            p.drawImage(0, 0, soft_shadow(self.size(), self.card.geometry(), self._radius, rem(1.0), rem(0.4),
                                          theme.SHADOW, self.devicePixelRatioF()))
            p.end()
        super().paintEvent(event)

    def _main_window(self):
        """Главное окно приложения — и для окна, открытого из другого окна."""
        w = self.parentWidget()
        while w is not None:
            top = w.window()
            if not isinstance(top, QDialog):
                return top
            w = top.parentWidget()
        return None

    def _dim(self, on):
        """Затемнение под окном (.modal-root сайта: фон var(--overlay)). Окно поверх
        другого окна затемняет и его — как второй слой затемнения у сайта."""
        if on:
            if self._scrim is not None:
                return
            self._scrim = []
            c = theme.OVERLAY
            css = "background: rgba(%d,%d,%d,%d);" % (c.red(), c.green(), c.blue(), c.alpha())
            targets = []
            main = self._main_window()
            cw = main.centralWidget() if main is not None and hasattr(main, "centralWidget") else None
            if cw is not None:
                targets.append((cw, 0))
            under = self.parentWidget()
            if isinstance(under, Modal):
                targets.append((under.card, under._radius))
            for w, radius in targets:
                scrim = QWidget(w)
                scrim.setAttribute(Qt.WA_StyledBackground, True)
                scrim.setStyleSheet(css + (" border-radius: %dpx;" % radius if radius else ""))
                scrim.setGeometry(w.rect())
                scrim.show()
                scrim.raise_()
                self._scrim.append(scrim)
        elif self._scrim is not None:
            hosts = []
            for scrim in self._scrim:
                try:
                    hosts.append(scrim.parentWidget())
                    scrim.hide()
                    scrim.deleteLater()
                except RuntimeError:
                    pass
            self._scrim = None
            # окно под этим — перерисовать целиком: иначе затемнение оставалось там, где ничего не менялось
            for host in hosts:
                try:
                    if host is not None:
                        host.update()
                        host.window().update()
                except RuntimeError:
                    pass

    def _center(self):
        """По центру окна приложения и не выше него (max-height сайта); высота — по содержимому."""
        main = self._main_window()
        anchor = main.geometry() if main is not None and main.isVisible() else \
            QApplication.primaryScreen().availableGeometry()
        limit = anchor.height() - rem(2)
        lay = self.layout()
        if self._area is not None:
            # прокручиваемое тело — во всю высоту содержимого, но окно не выше экрана;
            # sizeHint самой QScrollArea запоминается и после смены содержимого устаревает
            self._area.setFixedHeight(0)
            self._refresh(lay)
            chrome = max(self._total_height(lay), lay.totalMinimumSize().height())
            bl = self.body_widget.layout()
            width = self._area.width() - 2 * self._area.frameWidth()
            want = bl.totalHeightForWidth(width) if bl.hasHeightForWidth() and width > 0 else bl.totalSizeHint().height()
            room = limit - chrome if limit > 0 else want
            self._area.setFixedHeight(max(0, min(want, room)))
        self._refresh(lay)
        h = max(self._total_height(lay), lay.totalMinimumSize().height())
        if limit > 0:
            h = min(h, limit)
        self.resize(self.width(), h)
        h = self.height()          # не меньше минимума раскладки
        y = anchor.top() if h > anchor.height() else anchor.center().y() - h // 2
        self.move(anchor.center().x() - self.width() // 2, y)

    def _refresh(self, lay):
        """Размеры раскладок — сразу, не дожидаясь очереди событий (снизу вверх)."""
        from PySide6.QtCore import QCoreApplication, QEvent
        for w in (self.body_widget, self._area, self.card, self):
            if w is not None and w.layout() is not None:
                w.layout().invalidate()
                w.layout().activate()
        for _ in range(2):
            QCoreApplication.sendPostedEvents(None, QEvent.LayoutRequest)
        lay.activate()

    def _total_height(self, lay):
        if lay.hasHeightForWidth():
            return lay.totalHeightForWidth(self.width())
        return lay.totalSizeHint().height()

    def refit(self):
        """Содержимое изменилось (пришли данные) — заново по размеру и по центру."""
        try:
            self._center()
        except RuntimeError:
            pass

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.head.geometry().contains(self.card.mapFrom(self, e.position().toPoint())):
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag = None
        super().mouseReleaseEvent(e)


def field(caption, widget, required=False):
    """Подпись над полем, как .field сайта."""
    w = QWidget()
    w.setStyleSheet("background: transparent;")
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(rem(0.3))
    cap = label(caption, 0.78, theme.TEXT_LABEL)
    if required:
        import html as _html
        cap.setTextFormat(Qt.RichText)
        cap.setText("%s <span style='color:%s'>*</span>" % (_html.escape(caption), theme.DANGER))
    lay.addWidget(cap)
    lay.addWidget(widget)
    return w


def date_edit(value=None, minimum=None):
    """Поле даты с календарём (дд.мм.гггг)."""
    from kov.ui import input_qss
    e = QDateEdit()
    e.setCalendarPopup(True)
    e.setDisplayFormat("dd.MM.yyyy")
    e.setFont(font(0.875))
    e.setMinimumHeight(rem(2.375))
    e.setStyleSheet(input_qss("QDateEdit") + f"QDateEdit::drop-down {{ border: none; width: {rem(1.6)}px; }}")
    if minimum is not None:
        e.setMinimumDate(minimum)
    e.setDate(value if value is not None else QDate.currentDate())
    cal = e.calendarWidget()
    if cal is not None:
        cal.setMinimumSize(rem(20), rem(16))
        table = cal.findChild(QTableView)
        if table is not None:
            table.verticalHeader().setDefaultSectionSize(rem(1.8))
    return e


def confirm(parent, title, text, ok_text="ОК", cancel_text="Отмена", danger=False, glyph=None):
    m = Modal(parent, title, glyph or ("trash" if danger else "question"), width_rem=30)
    msg = label(text, 0.9, theme.TEXT_VALUE, wrap=True)
    m.body.addWidget(msg)
    m.add_button(cancel_text, "ghost")
    m.add_button(ok_text, "danger" if danger else "primary", None, lambda _m: True)
    return m.exec() == QDialog.Accepted


def alert(parent, title, text, glyph="info"):
    m = Modal(parent, title, glyph, width_rem=28)
    m.body.addWidget(label(text, 0.9, theme.TEXT_VALUE, wrap=True))
    m.add_button("ОК", "primary", None, lambda _m: True)
    m.exec()
