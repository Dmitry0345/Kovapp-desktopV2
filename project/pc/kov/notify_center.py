# -*- coding: utf-8 -*-
"""Центр уведомлений ПК-версии — те же правила, что у сайта (js/notify/notify-center.js).

  событие                     карточка  уведомление Windows (окно не активно)  звук
  message / grouped_message      да*            да                                да
  member_add (меня добавили)     да             да                                да
  removed_from_chat/group_deleted да            нет                               нет
  notification.new               да             да                                да
  * — не показываем, если этот чат сейчас открыт в мессенджере.

Настройки (раздел «Настройки» → «Уведомления») хранятся на сервере под
ключом web.notifications.pc — как у сайта на компьютере.
Звук — короткий сигнал без файлов: чат — два восходящих тона, уведомление —
два одинаковых пониже (как WebAudio сайта).
"""

import math
import os
import struct
import tempfile
import time

from PySide6.QtCore import QObject, QUrl

DEFAULT_SETTINGS = {"toasts": True, "chatToasts": True, "sound": True, "os": True}
DEDUP_WINDOW = 5.0
PREVIEW_LIMIT = 120
SOUND_THROTTLE = 1.5


def preview(text, files=0):
    import re
    text = str(text or "").strip()
    if re.match(r"^\[\[sticker:[a-z]+/[a-z]+\]\]$", text):
        return "Стикер"
    if re.match(r"^\[\[gif:https://[^\s\]]+\]\]$", text):
        return "GIF"
    if text:
        return text if len(text) <= PREVIEW_LIMIT else text[:PREVIEW_LIMIT - 1] + "…"
    if files > 1:
        return "Файлы: %d" % files
    if files == 1:
        return "Файл"
    return "Новое сообщение"


def decide(evt, ctx):
    """Чистая функция: событие + обстановка → план или None (проверяется тестами)."""
    if not evt or not evt.get("type"):
        return None
    s = ctx["settings"]
    os_allowed = (not ctx["visible"]) and bool(s.get("os"))
    kind = evt["type"]
    if kind in ("message", "grouped_message"):
        try:
            if int(evt.get("sender_id") or 0) == ctx["user_id"]:
                return None
            chat_id = int(evt.get("chat_id") or 0)
        except (TypeError, ValueError):
            return None
        if ctx.get("active_chat") == chat_id:
            return None
        info = ctx["chats"].get(chat_id)
        is_group = bool(info and info.get("is_group"))
        files = (evt.get("file_count") or len(evt.get("files") or [])) if kind == "grouped_message" else (1 if evt.get("file_path") else 0)
        last, first = evt.get("sender_last_name") or "", evt.get("sender_first_name") or ""
        return {"kind": "chat", "chat_id": chat_id, "needs_name": info is None, "is_group": is_group,
                "title": evt.get("sender_name") or "Сотрудник",
                "where": ("в группе «%s»" % (info.get("name") or "без названия")) if is_group else "",
                "body": preview(evt.get("text"), files), "initials": ((last[:1] + first[:1]).upper() or None),
                "route": ("messenger", {"chat_id": chat_id}), "tag": "chat-%s" % chat_id,
                "dedup": "%s:%s" % (kind, evt.get("id") or evt.get("group_id") or evt.get("parent_id") or evt.get("text")),
                "in_app": False, "toast": bool(s.get("toasts") and s.get("chatToasts")), "os": os_allowed,
                "sound": bool(s.get("sound"))}
    if kind == "special_message":
        meta = evt.get("metadata") or {}
        if evt.get("special") != "member_add":
            return None
        try:
            if int(meta.get("user_id") or 0) != ctx["user_id"] or int(evt.get("sender_id") or 0) == ctx["user_id"]:
                return None
            chat_id = int(evt.get("chat_id") or 0)
        except (TypeError, ValueError):
            return None
        return {"kind": "group", "chat_id": chat_id, "needs_name": True, "title": "Вас добавили в группу",
                "body": (meta.get("actor_name") + " добавил(а) вас") if meta.get("actor_name") else "",
                "route": ("messenger", {"chat_id": chat_id}), "tag": "chat-%s" % chat_id,
                "dedup": "member_add:%s" % evt.get("id"), "in_app": False, "toast": bool(s.get("toasts")),
                "os": os_allowed, "sound": bool(s.get("sound"))}
    if kind in ("removed_from_chat", "group_deleted"):
        return {"kind": "warning", "title": "Мессенджер",
                "body": evt.get("message") or ("Вас удалили из группы" if kind == "removed_from_chat" else "Группа удалена"),
                "route": ("messenger", {}), "tag": "chat-%s" % evt.get("chat_id"), "dedup": "%s:%s" % (kind, evt.get("chat_id")),
                "in_app": False, "toast": bool(s.get("toasts")), "os": False, "sound": False}
    if kind in ("notification", "notifications.new", "notification.created", "notification.new"):
        from kov.views.notifications import target_route
        return {"kind": "notification", "title": "Уведомление", "body": preview(evt.get("message")),
                "route": target_route(evt) or ("notifications", {}), "tag": "notification-%s" % (evt.get("notification_id") or "x"),
                "dedup": "notification:%s" % (evt.get("notification_id") or evt.get("message")),
                "in_app": True, "toast": bool(s.get("toasts")), "os": os_allowed, "sound": bool(s.get("sound"))}
    return None


# ── звук ─────────────────────────────────────────────────────────────────────

def chime_wav(kind, rate=22050):
    """Сигнал как у сайта: синус, быстрая атака и экспоненциальное затухание за 0.38 с."""
    notes = [(880.0, 0.0), (1174.7, 0.10)] if kind == "chat" else [(659.3, 0.0), (659.3, 0.16)]
    total = int(rate * 0.62)
    buf = [0.0] * total
    for freq, delay in notes:
        start = int(rate * delay)
        for i in range(int(rate * 0.4)):
            t = i / rate
            if t < 0.015:
                g = 0.0001 * (0.22 / 0.0001) ** (t / 0.015)
            else:
                g = 0.22 * (0.0001 / 0.22) ** min(1.0, (t - 0.015) / 0.365)
            k = start + i
            if k < total:
                buf[k] += g * math.sin(2 * math.pi * freq * t)
    data = b"".join(struct.pack("<h", max(-32767, min(32767, int(v * 32767)))) for v in buf)
    head = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    return head + b"data" + struct.pack("<I", len(data)) + data


class NotifyCenter(QObject):
    def __init__(self):
        super().__init__()
        self.settings = dict(DEFAULT_SETTINGS)
        self.active_chat = None
        self.chats = {}
        self.recent = {}
        self.shell = None
        self._last_sound = 0.0
        self._effects = {}
        self._tray_route = None

    # ── настройка ────────────────────────────────────────────────────────────

    def attach(self, shell):
        self.shell = shell
        if shell.tray is not None:
            try:
                shell.tray.messageClicked.connect(self._tray_clicked)
            except Exception:
                pass

    def load_settings(self):
        """В фоне: настройки сотрудника с сервера (как getSettings сайта)."""
        from kov.store import store
        try:
            saved = store.pref("web.notifications")
            if isinstance(saved, dict):
                self.settings.update({k: bool(saved[k]) for k in DEFAULT_SETTINGS if k in saved})
        except Exception:
            pass
        return self.settings

    def set_settings(self, patch):
        self.settings.update(patch or {})

    def set_chats(self, chats):
        for c in chats or []:
            try:
                self.chats[int(c.get("id"))] = {"name": c.get("name"), "is_group": bool(c.get("is_group"))}
            except (TypeError, ValueError):
                pass

    # ── события ──────────────────────────────────────────────────────────────

    def context(self):
        from kov.api import client
        visible = False
        if self.shell is not None:
            try:
                visible = self.shell.isActiveWindow() and not self.shell.isMinimized()
            except RuntimeError:
                visible = False
        return {"user_id": client.user_id, "visible": visible, "active_chat": self.active_chat, "settings": self.settings,
                "chats": self.chats}

    def duplicate(self, plan):
        now = time.monotonic()
        for k in [k for k, t in self.recent.items() if now - t > DEDUP_WINDOW]:
            self.recent.pop(k, None)
        keys = [plan.get("dedup"), "tb:%s|%s" % (plan.get("tag"), plan.get("body"))]
        if any(k in self.recent for k in keys):
            return True
        for k in keys:
            self.recent[k] = now
        return False

    def handle(self, evt):
        if evt.get("type") == "chats_updated" and isinstance(evt.get("chats"), list):
            self.set_chats(evt["chats"])
            return
        plan = decide(evt, self.context())
        if plan:
            self.execute(plan)

    def execute(self, plan):
        if self.duplicate(plan):
            return
        if plan.get("needs_name") and plan.get("chat_id") in self.chats:
            info = self.chats[plan["chat_id"]]
            if plan["kind"] == "chat" and info.get("is_group"):
                plan["where"] = "в группе «%s»" % (info.get("name") or "без названия")
            elif plan["kind"] == "group" and info.get("name"):
                plan["body"] = "«%s»" % info["name"] + ((" — " + plan["body"]) if plan.get("body") else "")
        # окно свёрнуто или не в фокусе — только системное уведомление (со звуком Windows), без карточки
        # и своего сигнала; нет области уведомлений — карточка и сигнал, как в открытом окне
        shown_os = bool(plan.get("os")) and self.show_os(plan)
        if plan.get("toast") and not shown_os:
            from kov import notify_ui
            route = plan.get("route")
            notify_ui.show(plan["title"], plan.get("body"), plan.get("where"), plan["kind"],
                           on_click=(lambda r=route: self.open(r)) if route else None, tag=plan.get("tag"),
                           initials=plan.get("initials"))
        if plan.get("sound") and not shown_os:
            self.chime(plan["kind"])

    def open(self, route):
        if self.shell is None or not route:
            return
        try:
            self.shell.showNormal() if self.shell.isMinimized() else None
            self.shell.activateWindow()
            self.shell.go(route[0], **dict(route[1]))
        except RuntimeError:
            pass

    def show_os(self, plan):
        """Системное уведомление Windows; True — показано."""
        tray = getattr(self.shell, "tray", None) if self.shell is not None else None
        if tray is None:
            return False
        body = ((plan.get("where") + " · ") if plan.get("where") else "") + (plan.get("body") or "")
        self._tray_route = plan.get("route")
        try:
            from kov.shell import app_icon
            if not (tray.isVisible() and tray.supportsMessages()):
                return False
            tray.showMessage(plan["title"], body[:240], app_icon(), 8000)
            return True
        except Exception:
            return False

    def _tray_clicked(self):
        if self._tray_route:
            self.open(self._tray_route)

    def chime(self, kind):
        now = time.monotonic()
        if now - self._last_sound < SOUND_THROTTLE:
            return
        self._last_sound = now
        key = "chat" if kind == "chat" else "note"
        try:
            from PySide6.QtMultimedia import QSoundEffect
        except Exception:
            return
        eff = self._effects.get(key)
        if eff is None:
            path = os.path.join(tempfile.gettempdir(), "kovapp_chime_%s.wav" % key)
            try:
                if not os.path.exists(path):
                    with open(path, "wb") as f:
                        f.write(chime_wav(kind))
                eff = QSoundEffect(self)
                eff.setSource(QUrl.fromLocalFile(path))
                eff.setVolume(0.9)
                self._effects[key] = eff
            except Exception:
                return
        try:
            eff.play()
        except Exception:
            pass


center = NotifyCenter()
