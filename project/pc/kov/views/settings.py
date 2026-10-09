# -*- coding: utf-8 -*-
"""Раздел «Настройки» — как js/views/settings.js веб-версии (компьютер).

Три карточки: «Внешний вид» (тема и размер интерфейса с предпросмотром),
«Уведомления» (переключатели) и «Это компьютер» (режим окна). Тема и
масштаб закрепляются на сервере под ключом web.settings.pc — общим с сайтом
на компьютере — и применяются сразу, без перезапуска: новая тема наплывает кругом от
нажатой кнопки, как на сайте (kov/restyle.py); уведомления — кнопкой «Сохранить».
"""

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import (QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget)

import scaler
from general_functions import theme
from kov import icons, jobs
from kov.api import client, human_error
from kov.notify_center import DEFAULT_SETTINGS, center
from kov.store import store
from kov.ui import (Button, Segmented, Switch, border, font, label, rem, scroll_area, toast)
from kov.views.base import Section

PRESETS = [(0.85, "Маленький"), (1.0, "Стандартный"), (1.25, "Увеличенный")]
SCALE_MIN, SCALE_MAX = 0.75, 1.25


class _Head(QFrame):
    """Шапка карточки настроек: градиент surface-head, кружок-значок, название и пояснение."""

    def __init__(self, glyph, title, note):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(rem(1.0), rem(0.85), rem(1.0), rem(0.85))
        lay.setSpacing(rem(0.7))
        ic = QLabel()
        d = rem(2.4)
        ic.setFixedSize(d, d)
        ic.setAlignment(Qt.AlignCenter)
        ic.setStyleSheet(f"background: {theme.INFO_SOFT}; border-radius: {rem(0.7)}px;")
        ic.setPixmap(icons.pixmap(glyph, rem(1.25), theme.ACCENT))
        lay.addWidget(ic)
        tx = QVBoxLayout()
        tx.setSpacing(rem(0.1))
        t = label(title, 1.0, theme.TEXT_VALUE, bold=True)
        t.setFont(font(1.0, weight=QFont.ExtraBold, spacing=0.01))
        tx.addWidget(t)
        if note:
            tx.addWidget(label(note, 0.76, theme.TEXT_LABEL))
        lay.addLayout(tx, 1)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0, QColor(theme.SURFACE_HEAD_A))
        g.setColorAt(1, QColor(theme.SURFACE_HEAD_B))
        # верхние углы — по скруглению карточки, иначе шапка выступает за рамку
        r = max(0, rem(0.75) - border())
        rect = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(rect, r, r)
        path.addRect(QRectF(rect.left(), rect.top() + r, rect.width(), rect.height() - r))
        path.setFillRule(Qt.WindingFill)
        p.fillPath(path.simplified(), g)
        p.setRenderHint(QPainter.Antialiasing, False)
        p.setPen(QColor(theme.CARD_BORDER))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        p.end()


def set_card(glyph, title, note):
    """Карточка (.set-card): шапка и тело с отступами .9rem 1rem 1rem."""
    card = QFrame()
    card.setObjectName("setCard")
    card.setStyleSheet(f"QFrame#setCard {{ background: {theme.CARD_BG}; border: {border()}px solid {theme.CARD_BORDER};"
                       f" border-radius: {rem(0.75)}px; }}")
    col = QVBoxLayout(card)
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(0)
    head = _Head(glyph, title, note)
    col.addWidget(head)
    body = QVBoxLayout()
    body.setContentsMargins(rem(1.0), rem(0.9), rem(1.0), rem(1.0))
    body.setSpacing(rem(0.8))
    col.addLayout(body)
    card.body = body
    return card


def sub_title(text):
    lab = label(text.upper(), 0.74, theme.TITLE_FG, bold=True)
    lab.setFont(font(0.74, bold=True, spacing=0.06))
    return lab


def divider():
    d = QFrame()
    d.setFixedHeight(border())
    d.setStyleSheet(f"background: {theme.CARD_BORDER};")
    return d


class SetRow(QFrame):
    """Строка настройки (.set-row): значок, название с пояснением, справа переключатель; нажимается вся строка."""

    def __init__(self, glyph, title, note, control, sub=False, first=False):
        super().__init__()
        self.control = control
        self.setObjectName("setRow")
        self.setAttribute(Qt.WA_Hover, True)
        self.first = first
        self._off = False
        lay = QHBoxLayout(self)
        lay.setContentsMargins(rem(0.6) + (rem(1.6) if sub else 0), rem(0.55), rem(0.6), rem(0.55))
        lay.setSpacing(rem(0.75))
        self.ico = QLabel()
        self.ico.setFixedSize(rem(1.7), rem(1.7))
        self.ico.setAlignment(Qt.AlignCenter)
        self.ico.setPixmap(icons.pixmap(glyph, rem(1.15), theme.ACCENT_SOFT))
        lay.addWidget(self.ico)
        tx = QVBoxLayout()
        tx.setSpacing(rem(0.1))
        self.t = label(title, 0.88, theme.TEXT_VALUE, bold=True)
        tx.addWidget(self.t)
        self.n = label(note, 0.74, theme.TEXT_LABEL, wrap=True) if note else None
        if self.n:
            tx.addWidget(self.n)
        lay.addLayout(tx, 1)
        if control is not None:
            lay.addWidget(control)
        self._style()

    def _style(self):
        b = border()
        top = "" if self.first else f"border-top: {b}px solid {theme.CARD_BORDER};"
        self.setStyleSheet(f"QFrame#setRow {{ border: none; {top} border-radius: 0; background: transparent; }}"
                           + ("" if self._off else f"QFrame#setRow:hover {{ background: {theme.CHIP_BG}; border-radius: {rem(0.5)}px; }}"))

    def set_off(self, off):
        """Недоступная строка: бледная и не нажимается (.set-row.off)."""
        self._off = bool(off)
        self.setEnabled(not off)
        from PySide6.QtWidgets import QGraphicsOpacityEffect
        eff = QGraphicsOpacityEffect(self)
        eff.setOpacity(0.45 if off else 1.0)
        self.setGraphicsEffect(eff if off else None)
        self._style()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and isinstance(self.control, Switch) and self.control.isEnabled():
            self.control.toggle()


class _Preview(QFrame):
    """Предпросмотр размера: текст, кнопка и метка в выбранном масштабе (без перезапуска)."""

    def __init__(self):
        super().__init__()
        self.setObjectName("preview")
        self.setStyleSheet(f"QFrame#preview {{ background: {theme.PAGE_BG}; border: {border()}px solid {theme.CARD_BORDER};"
                           f" border-radius: {rem(0.75)}px; }}")
        self.lay = QHBoxLayout(self)
        self.k = 1.0
        self.render(1.0)

    def render(self, k):
        from kov.ui import clear_layout
        self.k = k
        clear_layout(self.lay)
        r = lambda v: max(1, int(round(rem(v) * k)))
        self.lay.setContentsMargins(r(1.0), r(1.0), r(1.0), r(1.0))
        self.lay.setSpacing(r(1.0))
        t = QLabel("Пример текста")
        f = font(0.875)
        f.setPointSizeF(f.pointSizeF() * k)
        t.setFont(f)
        t.setStyleSheet(f"color: {theme.TEXT_VALUE}; background: transparent;")
        self.lay.addWidget(t)
        b = QLabel("Пример кнопки")
        bf = font(0.875, bold=True)
        bf.setPointSizeF(bf.pointSizeF() * k)
        b.setFont(bf)
        b.setAlignment(Qt.AlignCenter)
        b.setFixedHeight(r(2.375))
        b.setStyleSheet(f"background: {theme.BTN_BG}; color: #FFFFFF; border-radius: {r(0.5)}px; padding: 0 {r(1.0)}px;")
        self.lay.addWidget(b)
        c = QLabel("Пример метки")
        cf = font(0.75)
        cf.setPointSizeF(cf.pointSizeF() * k)
        c.setFont(cf)
        c.setStyleSheet(f"background: {theme.INFO_SOFT}; color: {theme.INFO}; border-radius: {r(0.7)}px;"
                        f" padding: {r(0.15)}px {r(0.6)}px;")
        self.lay.addWidget(c)
        self.lay.addStretch(1)


class SettingsSection(Section):
    title = "Настройки"
    key = "settings"

    def __init__(self, shell, parent=None):
        super().__init__(shell, parent)
        self.dirty = False
        self.state = dict(center.settings)
        self.scale = scaler.scale_multiplier()
        self.applied_scale = self.scale
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 0, rem(0.2), rem(0.5))
        col.setSpacing(rem(0.9))
        grid = QGridLayout()
        grid.setHorizontalSpacing(rem(0.9))
        grid.setVerticalSpacing(rem(0.9))
        grid.addWidget(self.look_card(), 0, 0, Qt.AlignTop)
        grid.addWidget(self.notify_card(), 0, 1, Qt.AlignTop)
        grid.addWidget(self.device_card(), 1, 0, 1, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        col.addLayout(grid)
        col.addStretch(1)
        self._page = page
        self.scroll = scroll_area(page)
        self.root.addWidget(self.scroll, 1)
        bar_row = QHBoxLayout()
        bar_row.setContentsMargins(0, 0, rem(0.2), 0)      # край — по карточкам (у страницы справа место под полосу прокрутки)
        bar_row.addWidget(self.save_bar())
        self.root.addLayout(bar_row)
        self.root.addStretch(0)     # .set-save { position: sticky }: панель сразу под карточками, без места — внизу
        self._scale_timer = QTimer(self)
        self._scale_timer.setSingleShot(True)
        self._scale_timer.timeout.connect(self.persist_scale)

    # ── внешний вид ──────────────────────────────────────────────────────────

    def look_card(self):
        card = set_card("sun", "Внешний вид", "Как приложение выглядит на этом и других устройствах")
        b = card.body
        b.addWidget(sub_title("Тема"))
        self.theme_pick = Segmented([("light", "Светлая", "sun"), ("dark", "Тёмная", "moon"),
                                     ("auto", "Как в системе", "device")], getattr(theme, "CHOSEN", theme.MODE))
        self.theme_pick.changed.connect(self.set_theme)
        b.addWidget(self.theme_pick, 0, Qt.AlignLeft)
        b.addWidget(label("Тема запоминается за сотрудником: на другом устройстве включится та же.", 0.8, theme.TEXT_MUTED,
                          wrap=True))
        b.addWidget(divider())
        b.addWidget(sub_title("Размер интерфейса"))
        vrow = QHBoxLayout()
        vrow.setSpacing(rem(0.5))
        self.scale_value = QLabel()
        self.scale_value.setFont(font(1.6, weight=QFont.ExtraBold))
        self.scale_value.setStyleSheet(f"color: {theme.TEXT_VALUE}; background: transparent;")
        vrow.addWidget(self.scale_value, 0, Qt.AlignBottom)
        vrow.addWidget(label("размер интерфейса", 0.78, theme.TEXT_LABEL), 0, Qt.AlignBottom)
        vrow.addStretch(1)
        self.apply_btn = Button("Применить", "primary", "check", "Применить новый размер интерфейса")
        self.apply_btn.clicked.connect(self.apply_scale)
        self.apply_btn.hide()
        vrow.addWidget(self.apply_btn)
        b.addLayout(vrow)
        self.presets = Segmented([(str(v), name, None) for v, name in PRESETS], None)
        self.presets.changed.connect(lambda key: self.set_scale(float(key)))
        b.addWidget(self.presets, 0, Qt.AlignLeft)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(int(SCALE_MIN * 100), int(SCALE_MAX * 100))
        self.slider.setStyleSheet(
            f"QSlider {{ background: transparent; min-height: {rem(1.6)}px; }}"
            f"QSlider::groove:horizontal {{ height: {rem(0.4)}px; border-radius: {rem(0.2)}px; background: {theme.SURFACE_3};"
            f" border: {border()}px solid {theme.CARD_BORDER}; }}"
            f"QSlider::sub-page:horizontal {{ background: {theme.ACCENT_FILL}; border-radius: {rem(0.2)}px; }}"
            f"QSlider::handle:horizontal {{ width: {rem(1.0)}px; height: {rem(1.0)}px; margin: -{rem(0.35)}px 0;"
            f" border-radius: {rem(0.5)}px; background: {theme.ACCENT_FILL}; border: none; }}")
        self.slider.valueChanged.connect(lambda v: self.set_scale(v / 100.0, from_slider=True))
        b.addWidget(self.slider)
        ticks = QHBoxLayout()
        for i, t in enumerate(("мельче", "стандарт", "крупнее")):
            ticks.addWidget(label(t, 0.68, theme.TEXT_MUTED, align=(Qt.AlignLeft, Qt.AlignHCenter, Qt.AlignRight)[i]), 1)
        b.addLayout(ticks)
        b.addWidget(label("Предпросмотр", 0.8, theme.TEXT_MUTED))
        self.preview = _Preview()
        b.addWidget(self.preview)
        self.set_scale(self.scale, initial=True)
        return card

    def set_theme(self, key):
        """Тема включается сразу — кругом от нажатой кнопки, как на сайте; на сервер уходит в фоне.
        Окно не перезапускается: раньше перезапуск при сбое закрывал приложение."""
        shell = self.shell
        btn = self.theme_pick.button(key)
        origin = btn.mapTo(shell, btn.rect().center()) if btn is not None else None
        scale = self.applied_scale

        def work():
            current = store.pref("web.settings") or {}
            store.set_pref("web.settings", dict(current, theme=key, scale=scale, scaleBase=2))

        def failed(e):
            toast(shell, "Тема включена, но на сервере не сохранилась: " + human_error(e), "warning")
        jobs.run(work, failed=failed, owner=shell)
        from kov import restyle
        QTimer.singleShot(0, lambda: restyle.switch(shell, mode=key, origin=origin))

    def set_scale(self, v, from_slider=False, initial=False):
        v = round(min(SCALE_MAX, max(SCALE_MIN, float(v))), 2)
        self.scale = v
        self.scale_value.setText("%.2f×" % v)
        if not from_slider:
            self.slider.blockSignals(True)
            self.slider.setValue(int(round(v * 100)))
            self.slider.blockSignals(False)
        exact = next((p for p, _n in PRESETS if abs(p - v) < 0.005), None)
        self.presets.set_current(str(exact) if exact is not None else None)
        if exact is None:
            for btn in self.presets._buttons.values():
                btn.set_kind("off")
        self.preview.render(v / max(0.01, self.applied_scale))
        self.apply_btn.setVisible(abs(v - self.applied_scale) > 0.004)
        if not initial:
            self._scale_timer.start(700)

    def persist_scale(self):
        """Масштаб закрепляется на сервере с задержкой — как ползунок сайта."""
        v = self.scale

        def work():
            current = store.pref("web.settings") or {}
            store.set_pref("web.settings", dict(current, scale=v, scaleBase=2,
                                                theme=getattr(theme, "CHOSEN", theme.MODE)))
        jobs.run(work, failed=lambda e: None, owner=self)

    def apply_scale(self):
        """Новый размер — сразу, без перезапуска: окно пересобирается и плавно сменяет прежнее."""
        self._scale_timer.stop()
        v = self.scale
        shell = self.shell

        def work():
            current = store.pref("web.settings") or {}
            store.set_pref("web.settings", dict(current, scale=v, scaleBase=2, theme=getattr(theme, "CHOSEN", theme.MODE)))
        jobs.run(work, failed=lambda e: None, owner=shell)
        from kov import restyle
        QTimer.singleShot(0, lambda: restyle.switch(shell, scale=v))

    # ── уведомления ──────────────────────────────────────────────────────────

    def notify_card(self):
        card = set_card("bell", "Уведомления", "Что показывать, пока приложение открыто")
        b = card.body
        b.setSpacing(0)
        self.sw = {}

        def make(key):
            sw = Switch(self.state.get(key, DEFAULT_SETTINGS[key]))
            sw.toggled.connect(lambda on, k=key: self.toggled(k, on))
            self.sw[key] = sw
            return sw
        self.toast_row = SetRow("info", "Всплывающие уведомления", "Карточка в углу экрана о новых событиях", make("toasts"),
                                first=True)
        self.chat_row = SetRow("chat", "В том числе о сообщениях чата", "Без этого чат будет виден только счётчиком",
                               make("chatToasts"), sub=True)
        self.sound_row = SetRow("volume", "Звук", "Короткий сигнал вместе с уведомлением", make("sound"))
        self.os_row = SetRow("device", "Уведомления Windows",
                             "Когда окно свёрнуто, уведомление показывает сама система", make("os"))
        for r in (self.toast_row, self.chat_row, self.sound_row, self.os_row):
            b.addWidget(r)
        tray_ok = self.shell.tray is not None
        note = ("Значок Kovapp в области уведомлений рядом с часами: через него приходят уведомления Windows."
                if tray_ok else "Область уведомлений Windows недоступна — уведомления видны только в окне приложения.")
        b.addSpacing(rem(0.8))
        b.addWidget(label(note, 0.76, theme.TEXT_LABEL, wrap=True))
        if not tray_ok:
            self.os_row.set_off(True)
        self.chat_row.set_off(not self.state.get("toasts", True))
        return card

    def toggled(self, key, on):
        self.state[key] = bool(on)
        if key == "toasts":
            self.chat_row.set_off(not on)
        if key == "sound" and on:
            center.chime("notification")
        self.mark_dirty()

    # ── это компьютер ────────────────────────────────────────────────────────

    def device_card(self):
        card = set_card("device", "Это компьютер", "Окно приложения")
        b = card.body
        grid = QGridLayout()
        grid.setHorizontalSpacing(rem(1.5))
        grid.setVerticalSpacing(rem(0.8))
        grid.addWidget(sub_title("Окно"), 0, 0)
        mode = "fullscreen" if self.shell.isFullScreen() else "windowed"
        self.window_pick = Segmented([("windowed", "В окне", "copy"), ("fullscreen", "Во весь экран", "zoom")], mode)
        self.window_pick.changed.connect(self.set_window_mode)
        grid.addWidget(self.window_pick, 1, 0, Qt.AlignLeft)
        grid.addWidget(label("Клавиша F11 переключает режим в любой момент.", 0.76, theme.TEXT_LABEL, wrap=True), 2, 0)
        # адрес сервера встроен в программу (scaler.DEFAULT_HOST), сжатие видео — тоже: в настройках их нет
        grid.setColumnStretch(0, 1)
        b.addLayout(grid)
        return card

    def set_window_mode(self, key):
        if key == "fullscreen" and not self.shell.isFullScreen():
            self.shell.showFullScreen()
        elif key == "windowed" and self.shell.isFullScreen():
            self.shell.showNormal()
        scaler.set_setting("window_mode", key)
        scaler.save_settings()

    # ── сохранение ───────────────────────────────────────────────────────────

    def save_bar(self):
        bar = QFrame()
        bar.setObjectName("setSave")
        bar.setStyleSheet(f"QFrame#setSave {{ background: {theme.CARD_BG}; border: {border()}px solid {theme.CARD_BORDER};"
                          f" border-radius: {rem(0.75)}px; }}")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(rem(0.9), rem(0.7), rem(0.9), rem(0.7))
        lay.setSpacing(rem(0.7))
        self.hint = label("Оформление сохраняется само, уведомления — кнопкой справа", 0.78, theme.TEXT_LABEL)
        lay.addWidget(self.hint, 1)
        self.save_btn = Button("Сохранить", "primary", "save")
        self.save_btn.clicked.connect(self.save)
        lay.addWidget(self.save_btn)
        return bar

    def mark_dirty(self):
        if self.dirty:
            return
        self.dirty = True
        self.hint.setText("Есть несохранённые изменения в уведомлениях")

    def save(self):
        settings = dict(self.state)

        def work():
            store.set_pref("web.notifications", settings)
            return settings

        def done(_):
            center.set_settings(settings)
            self.dirty = False
            self.hint.setText("✓  Сохранено")
            self.hint.setStyleSheet(f"color: {theme.SUCCESS}; background: transparent; font-weight: 700;")
            self.save_btn.setStyleSheet(self.save_btn.styleSheet() + f"QPushButton {{ background: {theme.SUCCESS};"
                                        f" border-color: {theme.SUCCESS}; }}")      # .btn.saved
            QTimer.singleShot(1600, self._saved_text)
            if abs(self.scale - self.applied_scale) > 0.004:
                self.apply_scale()

        def failed(e):
            toast(self, "Не удалось сохранить: " + human_error(e), "error")
        jobs.run(work, done=done, failed=failed, owner=self)

    def _fit_page(self):
        """Высота прокрутки — по карточкам: панель «Сохранить» встаёт сразу под ними."""
        lay = self._page.layout()
        w = self.scroll.viewport().width()
        h = lay.totalHeightForWidth(w) if lay.hasHeightForWidth() else lay.totalSizeHint().height()
        self.scroll.setMaximumHeight(max(rem(6), h + 2 * self.scroll.frameWidth()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._fit_page)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._fit_page)

    def _saved_text(self):
        try:
            self.save_btn.refresh()
            if not self.dirty:
                self.hint.setStyleSheet(f"color: {theme.TEXT_LABEL}; background: transparent;")
                self.hint.setText("Настройки сохранены на сервере — они переедут на другие устройства")
        except RuntimeError:
            pass
