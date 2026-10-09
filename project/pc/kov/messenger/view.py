# -*- coding: utf-8 -*-
"""Окно мессенджера: браузерный движок Qt со страницей чата и мост к Python.

Окно одно на всё время работы приложения (как #kovChat сайта): раздел
«Мессенджер» берёт его к себе, а уходя — откладывает в сторону. Так
открытый чат, прокрутка и поиск сохраняются, а события сокета доходят до
переписки, даже когда на экране другой раздел.
"""

import os

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import QVBoxLayout, QWidget

import scaler
from general_functions import theme
from kov import jobs
from kov.api import client

try:
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView
    ENGINE = True
except Exception:              # сборка без веб-движка — раздел покажет пояснение
    ENGINE = False

DEBUG = bool(os.environ.get("KOVAPP_DEBUG"))


if ENGINE:
    class ChatPage(QWebEnginePage):
        """Страница чата: ссылки — во внешний браузер, сама страница никуда не уходит."""

        def acceptNavigationRequest(self, url, nav_type, is_main_frame):
            if nav_type == QWebEnginePage.NavigationTypeLinkClicked:
                if url.scheme() in ("http", "https", "mailto"):
                    QDesktopServices.openUrl(url)
                return False
            return super().acceptNavigationRequest(url, nav_type, is_main_frame)

        def createWindow(self, _type):
            return _Outside(self.profile(), self)

        def javaScriptConsoleMessage(self, level, message, line, source):
            if DEBUG or level == QWebEnginePage.ErrorMessageLevel:
                print("[чат js] %s:%s %s" % (os.path.basename(source or ""), line, message))

    class _Outside(QWebEnginePage):
        """Окно target=_blank: адрес уходит во внешний браузер, сама страница не открывается."""

        def acceptNavigationRequest(self, url, nav_type, is_main_frame):
            if url.scheme() in ("http", "https", "mailto"):
                QDesktopServices.openUrl(url)
            self.deleteLater()
            return False

    class ChatView(QWebEngineView):
        """Браузер чата: файлы, брошенные в окно открытого чата, идут в окно отправки."""

        def __init__(self, host):
            super().__init__(host)
            self.host = host
            self.setContextMenuPolicy(Qt.NoContextMenu)
            self.setAcceptDrops(True)

        def _files(self, e):
            md = e.mimeData()
            return [u.toLocalFile() for u in md.urls() if u.isLocalFile()] if md.hasUrls() else []

        def dragEnterEvent(self, e):
            if self._files(e) and self.host.backend.active_chat_id:
                super().dragEnterEvent(e)       # подсветку области рисует сама страница
                e.acceptProposedAction()
            else:
                super().dragEnterEvent(e)

        def dragMoveEvent(self, e):
            super().dragMoveEvent(e)
            if self._files(e) and self.host.backend.active_chat_id:
                e.acceptProposedAction()

        def dropEvent(self, e):
            files = self._files(e)
            if files and self.host.backend.active_chat_id:
                e.acceptProposedAction()
                self.host.run_js("document.body.classList.remove('drag-active'); if (typeof dragCounter !== 'undefined') dragCounter = 0;")
                self.host.backend.drop_files(files)
                return
            super().dropEvent(e)


class MessengerHost(QWidget):
    """Браузер с чатом и мостом; создаётся один раз (shell.messenger_host())."""

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self.shell = shell
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"background: {theme.PAGE_BG};")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        from kov.messenger.backend import Backend
        self.backend = Backend(self)
        self.view = None
        self.loaded = False
        self.attached = False
        self.owner = None              # разметка раздела, в которой окно сейчас
        if not ENGINE:
            from kov.ui import empty
            lay.addWidget(empty("Мессенджеру нужен компонент Qt WebEngine — переустановите приложение"))
            return
        self.view = ChatView(self)
        self.page = ChatPage(self.view)
        self.view.setPage(self.page)
        self.page.setBackgroundColor(QColor(theme.PAGE_BG))
        s = self.page.settings()
        s.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)   # GIF из сервиса klipy
        s.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        s.setAttribute(QWebEngineSettings.ErrorPageEnabled, False)
        s.setAttribute(QWebEngineSettings.PluginsEnabled, False)
        # sans-serif страницы — шрифт приложения (как у браузера на Windows), а не системный
        # шрифт со своими чёрно-белыми смайликами: эмодзи берутся из цветного шрифта
        s.setFontFamily(QWebEngineSettings.SansSerifFont, theme.FONT_FAMILY)
        s.setFontFamily(QWebEngineSettings.StandardFont, theme.FONT_FAMILY)
        self.channel = QWebChannel(self.page)
        self.channel.registerObject("backend", self.backend)
        self.page.setWebChannel(self.channel)
        self.view.loadFinished.connect(self._loaded)
        lay.addWidget(self.view)
        self.load()

    # ── страница ─────────────────────────────────────────────────────────────

    def zoom(self):
        """Чат размечен в пикселях: увеличение — по экрану, шрифты — по множителю (как у сайта)."""
        try:
            return float(scaler.base_scale_factor()) or 1.0
        except Exception:
            return 1.0

    def load(self, force_compat=None):
        if self.view is None:
            return
        if force_compat is None:
            force_compat = bool(os.environ.get("KOVAPP_CHAT_COMPAT"))
        from kov.messenger import page

        def build(token):
            mult = float(scaler.get_setting("scale_multiplier", 1.0) or 1.0)
            html = page.build(theme.MODE, mult, 16.0 * mult, client.user_id, client.host, token,
                              force_compat=force_compat)
            self.backend.token = token or self.backend.token
            self.loaded = False
            self.backend.ready = False
            self.view.setZoomFactor(self.zoom())
            self.page.setHtml(html, page.base_url())
        jobs.run(client.media_token, done=build, failed=lambda e: build(client.token), owner=self)

    def _loaded(self, ok):
        self.loaded = bool(ok)
        if self.view is not None:
            self.view.setZoomFactor(self.zoom())
            if ok:
                self.restyle()        # тему могли сменить, пока страница грузилась

    def restyle(self):
        """Новая тема или размер интерфейса: страница та же (открытый чат, прокрутка, черновик) —
        меняются только её цвета и размер шрифта, как у сайта при переключении темы."""
        self.setStyleSheet(f"background: {theme.PAGE_BG};")
        if self.view is None:
            return
        import json
        self.page.setBackgroundColor(QColor(theme.PAGE_BG))
        self.view.setZoomFactor(self.zoom())
        mult = float(scaler.get_setting("scale_multiplier", 1.0) or 1.0)
        self.run_js("(function(){var h=document.documentElement;h.setAttribute('data-theme',%s);"
                    "h.style.fontSize='%.3fpx';h.style.setProperty('--user-scale','%.2f');})()"
                    % (json.dumps("dark" if theme.is_dark() else "light"), 16.0 * mult, mult))

    def run_js(self, code, callback=None):
        if self.view is None:
            if callback:
                callback(None)
            return
        if callback is None:
            self.page.runJavaScript(code)
        else:
            self.page.runJavaScript(code, callback)

    # ── место на экране ──────────────────────────────────────────────────────

    def on_screen(self):
        return bool(self.attached and self.isVisible())

    def attach(self, layout):
        """Раздел «Мессенджер» открылся — окно в его разметку."""
        layout.addWidget(self)
        self.attached = True
        self.owner = layout
        self.show()
        self.backend._set_center(self.backend.active_chat_id)
        self.backend.push_chats()
        if self.view is not None:
            self.view.setFocus()
            # фокус, пришедший в страницу «по Tab» при смене раздела, Chromium отдаёт первой кнопке страницы
            # (свернуть список чатов) с рамкой фокуса — снимаем его; работа с клавиатуры внутри чата не меняется
            for ms in (0, 400):
                QTimer.singleShot(ms, lambda: self.run_js(
                    "(function(){var a=document.activeElement;if(a&&a.id==='railBtn')a.blur();})()"))

    def detach(self, park, layout=None):
        """Раздел закрыли — окно откладывается (страница и чат остаются живыми).
        layout — разметка закрываемого раздела: если окно уже забрал новый раздел
        «Мессенджер» (переход из мессенджера в мессенджер), его не трогаем."""
        if not self.attached or (layout is not None and getattr(self, "owner", None) is not layout):
            return
        self.attached = False
        self.owner = None
        self.hide()
        self.setParent(park)
        self.backend._set_center(None)

    def event(self, e):
        if e.type() == QEvent.WindowActivate:
            self.backend._set_center(self.backend.active_chat_id)
        return super().event(e)
