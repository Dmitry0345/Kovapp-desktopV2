# -*- coding: utf-8 -*-
"""Сокет событий — как js/core/ws.js: вход первым сообщением, ping раз
в 25 секунд, переподключение с ростом паузы, комната открытой задачи.

Токен в адресе не передаётся: он оседает в журналах сервера и прокси.
"""

import json

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QAbstractSocket
from PySide6.QtWebSockets import QWebSocket

from kov.api import client


class EventSocket(QObject):
    received = Signal(object)       # любое событие сервера (не «event»: так зовётся метод QObject)
    status = Signal(bool)         # соединение есть / нет
    reconnected = Signal()        # вернулись после обрыва: данные стоит перечитать

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ws = None
        self._want = False
        self._backoff = 1000
        self._had = False
        self._task = None
        self._ping = QTimer(self)
        self._ping.setInterval(25000)
        self._ping.timeout.connect(lambda: self.send({"type": "ping"}))
        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.timeout.connect(self._connect)

    @property
    def connected(self):
        return bool(self._ws and self._ws.state() == QAbstractSocket.ConnectedState)

    def start(self):
        self._want = True
        self._connect()

    def stop(self):
        self._want = False
        self._retry.stop()
        self._ping.stop()
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
        self._ws = None

    def send(self, obj):
        if self.connected:
            self._ws.sendTextMessage(json.dumps(obj, ensure_ascii=False))
            return True
        return False

    def subscribe_task(self, task_id):
        task_id = task_id or None
        if self._task and self._task != task_id:
            self.send({"type": "task.unsubscribe", "task_id": self._task})
        self._task = task_id
        if task_id:
            self.send({"type": "task.subscribe", "task_id": task_id})

    def unsubscribe_task(self):
        self.subscribe_task(None)

    # ── внутреннее ───────────────────────────────────────────────────────────

    def _connect(self):
        if not self._want or not client.token:
            return
        self._retry.stop()
        if self._ws is not None:
            try:
                self._ws.disconnected.disconnect()
            except (TypeError, RuntimeError):
                pass
            self._ws.abort()
            self._ws.deleteLater()
        ws = QWebSocket()
        ws.connected.connect(self._on_open)
        ws.disconnected.connect(self._on_close)
        ws.textMessageReceived.connect(self._on_text)
        self._ws = ws
        ws.open(QUrl(client.ws_url()))

    def _on_open(self):
        self.send({"type": "auth", "token": client.token})
        self._backoff = 1000
        self.status.emit(True)
        if self._task:
            self.send({"type": "task.subscribe", "task_id": self._task})
        if self._had:
            self.reconnected.emit()
        self._had = True
        self._ping.start()

    def _on_close(self):
        self._ping.stop()
        self.status.emit(False)
        if self._want:
            self._retry.start(self._backoff)
            self._backoff = min(self._backoff * 2, 30000)

    def _on_text(self, text):
        if text in ("ping", "pong"):
            if text == "ping":
                self._ws.sendTextMessage("pong")
            return
        try:
            data = json.loads(text)
        except ValueError:
            return
        if not isinstance(data, dict) or not data.get("type"):
            return
        kind = data["type"]
        if kind == "ping":
            self.send({"type": "pong"})
            return
        if kind in ("pong", "connection_status"):
            return
        self.received.emit(data)
