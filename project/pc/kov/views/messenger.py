# -*- coding: utf-8 -*-
"""Раздел «Мессенджер» — тот же чат, что у сайта (chat.js), в окне приложения."""

from PySide6.QtCore import QTimer

from kov.views.base import Section


class MessengerSection(Section):
    title = "Мессенджер"
    key = "messenger"

    def __init__(self, shell, chat_id=None, parent=None):
        super().__init__(shell, parent)
        self.host = shell.messenger_host()
        self.host.attach(self.root)
        self._closed = False
        if chat_id:
            QTimer.singleShot(0, lambda: self.host.backend.open_chat(chat_id))
        elif self.host.backend.ready:
            self.host.backend.load_chats()

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        if getattr(self.host, "owner", None) is self.root:      # окно ещё здесь, а не в новом разделе
            self.root.removeWidget(self.host)
            self.host.detach(self.shell.messenger_park(), self.root)
