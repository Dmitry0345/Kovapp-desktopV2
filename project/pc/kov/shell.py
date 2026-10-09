# -*- coding: utf-8 -*-
"""Главное окно — оболочка как у сайта (js/views/shell.js): белая шапка
(кнопка меню, название раздела, колокольчик со счётчиком), меню слева,
панель профиля, полоска «нет связи», область раздела.

Переходы между разделами — shell.go(маршрут, параметры); история нужна
для «назад» (Alt+← и боковая кнопка мыши).
"""

import os

from PySide6.QtCore import (QEasingCurve, QEvent, QPoint, QPropertyAnimation, QRect, QSize, Qt, QTimer,
                          QVariantAnimation, Signal)
from PySide6.QtGui import QColor, QFontMetrics, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton,
                               QStackedLayout, QSystemTrayIcon, QVBoxLayout, QWidget)

import scaler
from general_functions import theme
from general_functions.Trifle import resource_path
from kov import icons, jobs, rights
from kov.api import client, human_error
from kov.store import store
from kov.ui import border, font, label, rem, toast
from kov.ws import EventSocket

MENU = [
    ("notifications", "Уведомления", "bell"),
    ("tasks", "Задачи", "flag"),
    ("projects", "Проекты", "project"),
    ("organizations", "Организации", "building"),
    ("equipment", "Оборудование", "archive"),
    ("employees", "Работники", "users"),
    ("csv", "Файлы CSV", "table"),
    ("profile", "Профиль", "id_card"),
    ("settings", "Настройки", "settings"),
    ("messenger", "Мессенджер", "chat"),
]
# пункт меню, к которому относится маршрут
ROUTE_SECTION = {"task": "tasks", "task_new": "tasks", "task_edit": "tasks", "project": "projects",
                 "project_new": "projects", "project_edit": "projects", "warehouse": "projects",
                 "organization_new": "organizations", "organization_edit": "organizations"}


def app_icon():
    """Значок программы: из .ico — все размеры (16–256), чёткий и в трее, и на панели задач."""
    here = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "images")
    for name in ("app_icon.ico", "app_icon.png"):
        for path in (resource_path(os.path.join("images", name)), os.path.join(here, name)):
            if os.path.exists(path):
                icon = QIcon(path)
                if not icon.isNull():
                    return icon
    return QIcon()


class _TopButton(QPushButton):
    """Кнопка шапки: значок, при наведении — подложка; у колокольчика счётчик."""

    def __init__(self, glyph, size_rem, icon_rem, parent=None):
        super().__init__(parent)
        self._glyph = glyph
        self._badge = 0
        self._icon_rem = icon_rem
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(rem(size_rem[0]), rem(size_rem[1]))
        self.setFocusPolicy(Qt.StrongFocus)
        self.setStyleSheet(f"QPushButton {{ border: none; background: transparent; border-radius: {rem(0.6)}px; }}"
                           f"QPushButton:hover {{ background: {theme.CHIP_BG}; }}")
        self._hover = False

    def set_badge(self, count):
        self._badge = max(0, int(count or 0))
        self.setToolTip("Уведомления" if not self._badge else "Непрочитанных уведомлений: %d" % self._badge)
        self.update()

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
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        size = rem(self._icon_rem)
        color = theme.ACCENT if self._hover else theme.TOP_BAR_FG
        p.drawPixmap((self.width() - size) // 2, (self.height() - size) // 2,
                     icons.pixmap(self._glyph, size, color, 2.1))
        if self._badge:
            text = "99+" if self._badge > 99 else str(self._badge)
            f = font(0.7, bold=True)
            p.setFont(f)
            fm = QFontMetrics(f)
            h = rem(1.15)
            w = max(h, fm.horizontalAdvance(text) + rem(0.6))
            x = self.width() - w - rem(0.3)
            y = rem(0.3)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.BADGE_BG))
            p.drawRoundedRect(x, y, w, h, h / 2, h / 2)
            p.setPen(QColor("#FFFFFF"))
            p.drawText(QRect(int(x), int(y), int(w), int(h)), Qt.AlignCenter, text)
        p.end()


class _MenuItem(QPushButton):
    def __init__(self, key, text, glyph, parent=None):
        super().__init__(text, parent)
        self.key = key
        self._count = 0
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)     # щелчок не забирает фокус: иначе после закрытия меню его ловила страница чата
        self.setIcon(icons.icon(glyph, rem(1.2), theme.ACCENT))
        self.setIconSize(QSize(rem(1.2), rem(1.2)))
        self.setFont(font(0.8125))
        self.set_active(False)

    def set_active(self, on):
        b = border()
        self.setFont(font(0.8125, bold=on))
        self.setStyleSheet(
            f"QPushButton {{ text-align: left; border: none; border-bottom: {b}px solid {theme.MENU_DIVIDER};"
            f" border-radius: {rem(0.25)}px; padding: {rem(0.8)}px; color: {theme.ACCENT if on else theme.BTN_GHOST_TEXT};"
            f" background: {theme.INFO_SOFT if on else 'transparent'}; }}"
            f"QPushButton:hover {{ background: {theme.INFO_SOFT if on else theme.MENU_HOVER}; }}"
            f"QPushButton:pressed {{ background: {theme.MENU_ACTIVE}; }}")

    def set_count(self, n):
        self._count = max(0, int(n or 0))
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        if not self._count:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        f = font(0.72, bold=True)
        p.setFont(f)
        text = str(self._count)
        h = rem(1.1)
        w = max(h, QFontMetrics(f).horizontalAdvance(text) + rem(0.9))
        x = self.width() - w - rem(0.6)
        y = (self.height() - h) / 2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.COUNT_BG))
        p.drawRoundedRect(int(x), int(y), int(w), int(h), h / 2, h / 2)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(QRect(int(x), int(y), int(w), int(h)), Qt.AlignCenter, text)
        p.end()


SLIDE_MS = 500        # --anim сайта: 500 мс, cubic-bezier(.65, 0, .35, 1)


class _Scrim(QWidget):
    clicked = Signal()
    level = 1.0           # 0…1 — плавное появление затемнения (.scrim сайта)

    def paintEvent(self, e):
        c = QColor(theme.OVERLAY)
        c.setAlphaF(c.alphaF() * max(0.0, min(1.0, self.level)))
        p = QPainter(self)
        p.fillRect(self.rect(), c)
        p.end()

    def mousePressEvent(self, e):
        self.clicked.emit()


class Shell(QMainWindow):
    logged_out = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Kovapp")
        self.setWindowIcon(app_icon())
        self.current = None
        self.current_key = None
        self.current_route = None
        self.history = []
        self._menu_open = False
        self._panel_open = False
        self._messenger = None      # окно мессенджера живёт всё время работы
        self._park = None
        self.tray = None
        self._notified = set()
        self._cover = None          # снимок прежнего раздела на время перехода
        self._quiet = False         # пересборка окна: переход без снимка (его делает restyle)
        self._badge_n = 0
        self._chat_n = 0
        self._link_down = False

        self._build_ui()

        # ── сокет и счётчики ─────────────────────────────────────────────────
        self._down_timer = QTimer(self)
        self._down_timer.setSingleShot(True)
        self._down_timer.timeout.connect(lambda: self._set_link("down"))
        self._link_hide = QTimer(self)
        self._link_hide.setSingleShot(True)
        self._link_hide.timeout.connect(lambda: self.link.hide())
        self.ws = EventSocket(self)
        self.ws.received.connect(self._on_event)
        self.ws.status.connect(self._on_ws_status)
        self.ws.reconnected.connect(self._on_reconnect)
        self._badge_timer = QTimer(self)
        self._badge_timer.setSingleShot(True)
        self._badge_timer.timeout.connect(self.refresh_counters)
        self._counters = QTimer(self)
        self._counters.setInterval(60000)
        self._counters.timeout.connect(self.refresh_counters)

        QShortcut(QKeySequence(Qt.Key_F11), self, activated=self.toggle_fullscreen)
        QShortcut(QKeySequence("Alt+Left"), self, activated=self.back)
        # Esc закрывает меню и панель, только пока они открыты: иначе клавиша нужна разделу (чату)
        self._esc = QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self.close_overlays)
        self._esc.setEnabled(False)

    def _build_ui(self):
        """Шапка, область раздела, меню, панель профиля, полоска связи — в цветах текущей темы.
        Вызывается при создании окна и при смене темы или размера (rebuild)."""
        central = QWidget()
        central.setObjectName("shellRoot")
        central.setStyleSheet(f"QWidget#shellRoot {{ background: {theme.PAGE_BG}; }}")
        self.setCentralWidget(central)
        col = QVBoxLayout(central)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        # ── шапка ────────────────────────────────────────────────────────────
        self.top = QFrame()
        self.top.setObjectName("topBar")
        self.top.setFixedHeight(rem(4.875))
        self.top.setStyleSheet(f"QFrame#topBar {{ background: {theme.CARD_BG}; border: none;"
                               f" border-bottom: {border()}px solid {theme.CARD_BORDER}; }}")
        tl = QHBoxLayout(self.top)
        tl.setContentsMargins(rem(0.75), 0, rem(0.75), 0)
        tl.setSpacing(rem(0.75))
        self.menu_btn = _TopButton("menu", (4.25, 3.25), 2.2)
        self.menu_btn.setToolTip("Меню")
        self.menu_btn.clicked.connect(self.toggle_menu)
        tl.addWidget(self.menu_btn)
        tl.addStretch(1)
        self.title_chip = QLabel("Kovapp")
        self.title_chip.setFont(font(1.05, bold=True))
        chip_h = QFontMetrics(self.title_chip.font()).height() + 2 * rem(0.45) + 2 * border()
        self.title_chip.setFixedHeight(chip_h)
        self.title_chip.setStyleSheet(f"QLabel {{ background: {theme.CHIP_BG}; border: {border()}px solid {theme.CARD_BORDER};"
                                      f" color: {theme.ACCENT}; border-radius: {chip_h // 2}px;"
                                      f" padding: 0 {rem(1.2)}px; }}")
        tl.addWidget(self.title_chip, 0, Qt.AlignVCenter)
        tl.addStretch(1)
        self.bell = _TopButton("bell", (3.25, 3.25), 2.0)
        self.bell.set_badge(0)
        self.bell.clicked.connect(lambda: self.go("notifications"))
        tl.addWidget(self.bell)
        col.addWidget(self.top)

        # ── область раздела ──────────────────────────────────────────────────
        self.content = QWidget()
        self.content.setObjectName("content")
        self.content.setStyleSheet(f"QWidget#content {{ background: {theme.PAGE_BG}; }}")
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(0)
        col.addWidget(self.content, 1)

        # ── поверх: затемнение, меню, панель профиля, полоска связи ─────────
        self.scrim = _Scrim(central)
        self.scrim.hide()
        self.scrim.clicked.connect(self.close_overlays)
        self.menu = QFrame(central)
        self.menu.setObjectName("sideMenu")
        self.menu.setStyleSheet(f"QFrame#sideMenu {{ background: {theme.CARD_BG}; border: none;"
                                f" border-right: {border()}px solid {theme.CARD_BORDER}; }}")
        ml = QVBoxLayout(self.menu)
        ml.setContentsMargins(rem(0.6), rem(0.4), rem(0.6), rem(0.4))
        ml.setSpacing(0)
        self.menu_items = {}
        for key, text, glyph in MENU:
            item = _MenuItem(key, text, glyph)
            item.clicked.connect(lambda _=False, k=key: self._menu_go(k))
            ml.addWidget(item)
            self.menu_items[key] = item
        ml.addStretch(1)
        self.menu.hide()

        # панель профиля выезжает справа (.side-panel сайта, ширина 46.875rem)
        self.panel = QFrame(central)
        self.panel.setObjectName("sidePanel")
        self.panel.setStyleSheet(f"QFrame#sidePanel {{ background: {theme.CARD_BG}; border: none;"
                                 f" border-left: {border()}px solid {theme.CARD_BORDER}; }}")
        self.panel_layout = QVBoxLayout(self.panel)
        self.panel_layout.setContentsMargins(0, 0, 0, 0)
        self.panel.hide()

        self.link = QLabel(central)
        self.link.setFont(font(0.8))
        self.link.hide()
        self.bell.set_badge(self._badge_n)
        self.menu_items["messenger"].set_count(self._chat_n)

    def rebuild(self):
        """Новая тема или размер: окно собирается заново на месте, без перезапуска.
        Сокет, история переходов, значок в трее и открытый чат мессенджера сохраняются."""
        route = self.current_route
        history = list(self.history)
        self.close_overlays(instant=True)
        if self._cover is not None:
            self._cover.finish_now()
            self._cover = None
        old = self.current
        if old is not None:
            try:
                old.shutdown()
            except Exception as e:
                print("[раздел] shutdown: %s" % e)
            from general_functions import ui_kit
            ui_kit.shutdown_tree(old)
        self.current = None
        host = self._messenger
        if host is not None:                     # страница чата — та же, меняются только её цвета
            host.detach(self.messenger_park())
        self._park = None
        from kov import files as kfiles
        downloads = kfiles.take_panel(self)
        old_central = self.takeCentralWidget()
        self._build_ui()
        if host is not None:                     # прямо в новое место: без промежуточного «окна без родителя»
            host.setParent(self.messenger_park())
            host.hide()
            host.restyle()
        if old_central is not None:
            old_central.hide()
            old_central.deleteLater()
        if downloads is not None:
            kfiles.give_panel(self, downloads)
        self._build_menu()
        self.history = history
        self._quiet = True
        try:
            if route is not None:
                self.go(route[0], remember=False, **dict(route[1]))
        finally:
            self._quiet = False

    # ── запуск ───────────────────────────────────────────────────────────────

    def start(self, route=None):
        """route — куда открыть после перезапуска: ["settings", {}] и т. п."""
        self._build_menu()
        client.on_unauthorized = lambda: QTimer.singleShot(0, self._unauthorized)
        self.ws.start()
        self._counters.start()
        self.refresh_counters()
        try:
            if QSystemTrayIcon.isSystemTrayAvailable():
                self.tray = QSystemTrayIcon(app_icon(), self)
                self.tray.setToolTip("Kovapp")
                self.tray.activated.connect(lambda *_: (self.showNormal(), self.activateWindow()))
                self.tray.show()
        except Exception:
            self.tray = None
        from kov import notify_ui
        from kov.notify_center import center
        notify_ui.set_anchor(self)
        center.attach(self)
        jobs.run(center.load_settings, owner=self)
        name, params = "tasks", {}
        if isinstance(route, (list, tuple)) and route:
            name = str(route[0])
            params = dict(route[1]) if len(route) > 1 and isinstance(route[1], dict) else {}
        elif isinstance(route, str) and route:
            name = route
        try:
            self.go(name, remember=False, **params)
        except Exception as e:
            print("[запуск] маршрут %s: %s" % (name, e))
            self.go("tasks", remember=False)

    def _build_menu(self):
        for key, item in self.menu_items.items():
            visible = rights.can_open_section(key)
            item.setVisible(visible)

    # ── переходы ─────────────────────────────────────────────────────────────

    def go(self, route, remember=True, **params):
        section_key = ROUTE_SECTION.get(route, route)
        if route == "profile":
            self.open_profile(client.user_id)
            return
        if route == "employee":
            self.open_profile(params.get("employee_id"))
            return
        if not rights.can_open_section(section_key):
            toast(self, "Раздел недоступен", "warning")
            return
        widget = self._make(route, params)
        if widget is None:
            return
        if remember and self.current_route is not None:
            self.history.append((self.current_route[0], dict(self.current_route[1])))
            self.history = self.history[-30:]
        self._show(section_key, widget)
        self.current_route = (route, dict(params))
        if route != "task":
            self.ws.unsubscribe_task()

    def back(self):
        if self.history:
            route, params = self.history.pop()
            self.go(route, remember=False, **params)

    def replace(self, route, **params):
        """Переход без записи в историю (после сохранения формы)."""
        self.go(route, remember=False, **params)

    def _make(self, route, p):
        rights.set_read_only(False)
        if route == "tasks":
            from kov.views.tasks import TasksSection
            return TasksSection(self, archived=p.get("archived", False))
        if route == "task":
            from kov.views.task_view import TaskView
            self.ws.subscribe_task(p["task_id"])
            return TaskView(self, p["task_id"], read_only_text=p.get("read_only_text"), back=p.get("back"))
        if route in ("task_new", "task_edit"):
            from kov.views.task_edit import TaskEdit
            return TaskEdit(self, p.get("task_id"), project_id=p.get("project_id"))
        if route == "projects":
            from kov.views.projects import ProjectsSection
            return ProjectsSection(self)
        if route == "project":
            from kov.views.projects import ProjectView
            return ProjectView(self, p["project_id"], section=p.get("section"))
        if route in ("project_new", "project_edit"):
            from kov.views.projects import ProjectEdit
            return ProjectEdit(self, p.get("project_id"))
        if route == "warehouse":
            from kov.views.projects import ProjectView
            return ProjectView(self, p["project_id"], section="warehouse")
        if route == "equipment":
            from kov.views.equipment import EquipmentSection
            return EquipmentSection(self)
        if route == "employees":
            from kov.views.employees import EmployeesSection
            return EmployeesSection(self)
        if route == "organizations":
            from kov.views.organizations import OrganizationsSection
            return OrganizationsSection(self)
        if route in ("organization_new", "organization_edit"):
            from kov import rights as _r
            if not _r.can_edit_organizations():
                toast(self, "Раздел доступен только руководству", "warning")
                return None
            from kov.views.organizations import OrganizationEdit
            return OrganizationEdit(self, p.get("organization_id"))
        if route == "notifications":
            from kov.views.notifications import NotificationsSection
            return NotificationsSection(self)
        if route == "settings":
            from kov.views.settings import SettingsSection
            return SettingsSection(self)
        if route == "csv":
            from kov.views.csv import CsvSection
            return CsvSection(self)
        if route == "messenger":
            from kov.views.messenger import MessengerSection
            return MessengerSection(self, chat_id=p.get("chat_id"))
        return None

    def _show(self, key, widget):
        self.close_overlays()
        old = self.current
        # Плавный переход, как у веб-приложения: прежний раздел остаётся снимком поверх, пока новый
        # подгружает данные (до полусекунды, с полоской подгрузки), затем гаснет, открывая готовый раздел
        shot = None
        if old is not None and not self._quiet and self._cover is None and self.isVisible():
            try:
                shot = self.content.grab()
            except RuntimeError:
                shot = None
        if old is not None:
            try:
                old.shutdown()
            except Exception as e:
                print("[раздел] shutdown: %s" % e)
            from general_functions import ui_kit
            ui_kit.shutdown_tree(old)
            self.content_layout.removeWidget(old)
            w = QApplication.focusWidget()
            if w is not None and old.isAncestorOf(w):      # фокус — в окно, а не «по Tab» в новый раздел
                self.centralWidget().setFocus(Qt.OtherFocusReason)
            old.hide()
            old.deleteLater()
        self.current = widget
        self.current_key = key
        self.content_layout.addWidget(widget)
        widget.show()
        for k, item in self.menu_items.items():
            item.set_active(k == key)
        self.set_title(getattr(widget, "title", "") or "Kovapp")
        if self._cover is not None:              # новый переход во время прежнего: снимок тот же, ждём уже этот раздел
            self._cover.raise_()
        elif shot is not None and not shot.isNull():
            from kov.restyle import Reveal
            cover = Reveal(self.content, shot, bar=True)
            self._cover = cover
            cover.destroyed.connect(lambda *_a: self._cover_gone(cover))
            cover.start(lambda: self.current is not None and jobs.pending_for(self.current) > 0, wait_ms=500)

    def _cover_gone(self, cover):
        if self._cover is cover:
            self._cover = None

    def set_title(self, text):
        text = (text or "").strip() or "Kovapp"
        fm = QFontMetrics(self.title_chip.font())
        limit = max(rem(10), int(self.width() * 0.55) - rem(2.4))
        self.title_chip.setText(fm.elidedText(text, Qt.ElideRight, limit))
        self.title_chip.setToolTip(text)
        self.setWindowTitle(text + " — Kovapp")

    # ── меню и панели ────────────────────────────────────────────────────────

    def _menu_go(self, key):
        self.close_overlays()
        self.go(key)

    def toggle_menu(self):
        if self._menu_open:
            self.close_overlays()
        else:
            start = self.menu.x() if self.menu.isVisible() else -self.menu.width()   # уезжало — едет обратно с места
            self._menu_open = True           # затемнение остаётся: панель меняется на меню
            self.close_panel(instant=True)
            self._esc.setEnabled(True)
            self._layout_overlays()
            self._fade_scrim(True)
            self.menu.show()
            self.menu.raise_()
            self._slide(self.menu, start, 0)

    def open_panel(self, widget):
        if self._menu_open:
            self._menu_open = False
            self._stop_slide(self.menu)
            self.menu.hide()
        while self.panel_layout.count():
            w = self.panel_layout.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        self.panel_layout.addWidget(widget)
        w = self.centralWidget().width()
        start = self.panel.x() if self.panel.isVisible() else w
        self._panel_open = True
        self._esc.setEnabled(True)
        self._layout_overlays()
        self._fade_scrim(True)
        self.panel.show()
        self.panel.raise_()
        self._slide(self.panel, start, w - self.panel.width())

    def close_panel(self, instant=False):
        if self._panel_open:
            self._release_focus()
            self._panel_open = False
            w = self.centralWidget().width()
            if instant:
                self._stop_slide(self.panel)
                self.panel.hide()
            else:
                self._slide(self.panel, self.panel.x(), w, self.panel.hide)
            if not self._menu_open:
                self._fade_scrim(False, instant)
        self._esc.setEnabled(self._menu_open)

    def _release_focus(self):
        """Фокус из уезжающего меню или панели — в окно, без «перехода по Tab»: иначе Qt передаёт его дальше по
        цепочке, и страница чата выделяет первую кнопку рамкой фокуса."""
        w = QApplication.focusWidget()
        if w is not None and (self.menu.isAncestorOf(w) or self.panel.isAncestorOf(w)):
            self.centralWidget().setFocus(Qt.OtherFocusReason)

    def close_overlays(self, instant=False):
        self._release_focus()
        if self._menu_open:
            self._menu_open = False
            if instant:
                self._stop_slide(self.menu)
                self.menu.hide()
            else:
                self._slide(self.menu, self.menu.x(), -self.menu.width(), self.menu.hide)
        self.close_panel(instant)
        if instant or self.scrim.isVisible() and getattr(self, "_scrim_target", 0.0) > 0:
            self._fade_scrim(False, instant)
        self._esc.setEnabled(False)

    def _fade_scrim(self, show, instant=False):
        """Затемнение появляется и гаснет плавно, вместе с выездом меню или панели."""
        self._scrim_target = 1.0 if show else 0.0
        anim = getattr(self, "_scrim_anim", None)
        if anim is not None:
            try:
                anim.stop()
            except RuntimeError:
                pass
            self._scrim_anim = None
        if instant:
            self.scrim.level = self._scrim_target
            self.scrim.setVisible(show)
            self.scrim.update()
            return
        if show:
            if not self.scrim.isVisible():
                self.scrim.level = 0.0
            self.scrim.show()
            self.scrim.raise_()
        a = QVariantAnimation(self)
        a.setStartValue(float(self.scrim.level))
        a.setEndValue(self._scrim_target)
        a.setDuration(SLIDE_MS)
        a.setEasingCurve(QEasingCurve.InOutCubic)

        def step(v):
            self.scrim.level = float(v)
            self.scrim.update()

        def done():
            if self._scrim_anim is a:
                self._scrim_anim = None
            if self._scrim_target == 0.0:
                self.scrim.hide()
        a.valueChanged.connect(step)
        a.finished.connect(done)
        self._scrim_anim = a
        a.start(QVariantAnimation.DeleteWhenStopped)

    # ── мессенджер ───────────────────────────────────────────────────────────

    def messenger_host(self):
        """Окно чата: создаётся при первом открытии раздела и дальше не пересоздаётся."""
        if self._messenger is None:
            from kov.messenger.view import MessengerHost
            self._messenger = MessengerHost(self, self.messenger_park())
        return self._messenger

    def messenger_park(self):
        """Скрытое место для окна чата, пока открыт другой раздел."""
        if self._park is None:
            self._park = QWidget(self.centralWidget())
            self._park.setGeometry(0, 0, 0, 0)
            self._park.hide()
        return self._park

    def set_chat_count(self, n):
        """Непрочитанные сообщения — на пункте «Мессенджер» (как setChatCount сайта)."""
        self._chat_n = n
        self.menu_items["messenger"].set_count(n)

    def _stop_slide(self, frame):
        anim = getattr(self, "_slides", {}).pop(id(frame), None)
        if anim is not None:
            try:
                anim.stop()
            except RuntimeError:
                pass

    def _slide(self, frame, start_x, end_x, after=None):
        """Выезд и уезд меню или панели — как transform у сайта (500 мс, InOutCubic)."""
        self._stop_slide(frame)
        anim = QPropertyAnimation(frame, b"pos", frame)
        anim.setDuration(SLIDE_MS)
        anim.setStartValue(QPoint(start_x, frame.y()))
        anim.setEndValue(QPoint(end_x, frame.y()))
        anim.setEasingCurve(QEasingCurve.InOutCubic)
        if not hasattr(self, "_slides"):
            self._slides = {}
        self._slides[id(frame)] = anim

        def done():
            if self._slides.get(id(frame)) is anim:
                del self._slides[id(frame)]
            if after is not None:
                after()
        anim.finished.connect(done)
        anim.start(QPropertyAnimation.DeleteWhenStopped)

    def _layout_overlays(self):
        top = self.top.height()
        w, h = self.centralWidget().width(), self.centralWidget().height()
        self.scrim.setGeometry(0, top, w, h - top)
        self.menu.setGeometry(0, top, min(rem(16.875), int(w * 0.8)), h - top)
        pw = min(rem(46.875), int(w * 0.8))
        self.panel.setGeometry(w - pw, top, pw, h - top)
        if self.link.isVisible():
            self.link.adjustSize()
            self.link.move((w - self.link.width()) // 2, top + rem(0.4))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._layout_overlays()
        if self.current is not None:
            self.set_title(self.title_chip.toolTip())

    def mousePressEvent(self, e):
        if e.button() == Qt.BackButton:
            self.back()
            return
        super().mousePressEvent(e)

    # ── профиль ──────────────────────────────────────────────────────────────

    def open_profile(self, employee_id):
        from kov.views.employees import ProfilePanel
        self.open_panel(ProfilePanel(self, int(employee_id)))

    # ── связь ────────────────────────────────────────────────────────────────

    def _on_ws_status(self, connected):
        self._down_timer.stop()
        if connected:
            self._set_link("up")
        else:
            self._down_timer.start(1500)

    def _set_link(self, state):
        b = border()
        if state == "down":
            self._link_down = True
            self.link.setText("●  Нет связи с сервером — переподключаемся")
            self.link.setStyleSheet(f"QLabel {{ background: {theme.WARNING_SOFT}; color: {theme.WARNING_TEXT};"
                                    f" border: {b}px solid {theme.WARNING}; border-radius: {rem(1)}px;"
                                    f" padding: {rem(0.35)}px {rem(0.9)}px; }}")
            self.link.show()
            self.link.raise_()
            self._layout_overlays()
            return
        if not self._link_down:
            self.link.hide()
            return
        self._link_down = False
        self.link.setText("✓  Связь восстановлена")
        self.link.setStyleSheet(f"QLabel {{ background: {theme.SUCCESS_SOFT}; color: {theme.SUCCESS_TEXT};"
                                f" border: {b}px solid {theme.SUCCESS}; border-radius: {rem(1)}px;"
                                f" padding: {rem(0.35)}px {rem(0.9)}px; }}")
        self.link.show()
        self.link.raise_()
        self._layout_overlays()
        self._link_hide.start(2400)

    def _on_reconnect(self):
        if self.current is not None and hasattr(self.current, "on_reconnect"):
            self.current.on_reconnect()
        if self._messenger is not None:
            self._messenger.backend.on_reconnect()
        self.refresh_counters()

    def _on_event(self, evt):
        kind = evt.get("type") or ""
        if kind.startswith("employee."):
            jobs.run(store.employees, True, owner=self)
            if kind == "employee.updated" and str(evt.get("employee_id")) == str(client.user_id):
                jobs.run(store.load_me, owner=self)
        if kind.startswith("notification") or kind.startswith("chat") or kind in ("message", "chats_updated"):
            self._badge_timer.start(400)
        try:
            from kov.notify_center import center
            center.handle(evt)
        except Exception as e:
            print("[уведомления] %s: %s" % (kind, e))
        if self._messenger is not None:
            try:
                self._messenger.backend.on_event(evt)      # переписка получает события и вне раздела
            except Exception as e:
                print("[мессенджер] %s: %s" % (kind, e))
        if self.current is not None:
            try:
                self.current.on_event(evt)
            except Exception as e:
                print("[событие] %s: %s" % (kind, e))

    def refresh_counters(self):
        def load():
            unread = 0
            try:
                items = client.get("employees/%d/notifications" % client.user_id) or []
                unread = sum(1 for n in items if isinstance(n, dict) and not n.get("is_read"))
            except Exception:
                pass
            chats = 0
            try:
                lst = client.get("chat/list") or []
                from kov.messenger.util import unread_total
                from kov.notify_center import center
                center.set_chats(lst)
                chats = unread_total(lst, center.active_chat, True)     # открытый на экране чат не считается
            except Exception:
                pass
            return unread, chats

        def done(res):
            self._badge_n, self._chat_n = res[0], res[1]
            self.bell.set_badge(res[0])
            self.menu_items["messenger"].set_count(res[1])
        jobs.run(load, done=done, owner=self)

    # ── прочее ───────────────────────────────────────────────────────────────

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            scaler.set_setting("window_mode", "windowed")
        else:
            self.showFullScreen()
            scaler.set_setting("window_mode", "fullscreen")
        scaler.save_settings()

    def _unauthorized(self):
        if getattr(self, "_logging_out", False):
            return
        self._logging_out = True
        toast(self, "Сессия истекла. Войдите заново.", "warning")
        self.logout(ask=False)

    def logout(self, ask=True):
        from kov.dialogs import confirm
        if ask and not confirm(self, "Выход", "Выйти из учётной записи?", "Выйти", glyph="logout"):
            return
        self.ws.stop()
        from kov import notify_ui
        from kov.notify_center import center
        notify_ui.clear()
        center.active_chat = None
        client.clear()
        store.reset()
        self.logged_out.emit()
        self.close()

    def closeEvent(self, e):
        try:
            if self.current is not None:
                self.current.shutdown()
        except Exception:
            pass
        self.ws.stop()
        if self.tray is not None:
            self.tray.hide()
        super().closeEvent(e)
