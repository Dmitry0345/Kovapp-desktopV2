# -*- coding: utf-8 -*-
"""Мост мессенджера: объект Python, который chat.js видит как backend.

То же, что JS-объект backend в js/views/messenger.js сайта: те же методы и
сигналы, те же запросы к серверу и события сокета. Окна, которые на сайте
сделаны внутри страницы (новый чат, карточки, пересылка, отправка файлов),
здесь — окна приложения (kov.messenger.dialogs, kov.messenger.send).
"""

import json
import os
import random
import threading
import time

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from kov import jobs
from kov.api import client, human_error
from kov.messenger import util
from kov.messenger.util import MAX_PENDING, Urls, aggregate, file_name, is_image, is_media, is_video
from kov.store import store

# сообщения открытого чата для просмотра медиа: с подписью-превью (стикер, GIF — словом)
JS_CHAT_MESSAGES = r"""(function () {
  try {
    var list = (typeof chatMessages !== 'undefined' && Array.isArray(chatMessages)) ? chatMessages : [];
    return JSON.stringify(list.map(function (m) {
      var t = m.text && m.text !== 'null' ? m.text : '';
      return { id: m.id, sender_id: m.sender_id, sender_name: m.sender_name, timestamp: m.timestamp, text: m.text,
        file_path: m.file_path, url: m.url, filename: m.filename, file_name: m.file_name, file_size: m.file_size,
        thumb_url: m.thumb_url, files: m.files, __preview: typeof previewText === 'function' ? previewText(t) : t };
    }));
  } catch (e) { return '[]'; }
})()"""

# текст из строки ввода переезжает в окно отправки, при отмене возвращается
JS_TAKE_INPUT = r"""(function () {
  var i = document.getElementById('messageInput');
  var t = i ? i.value : '';
  if (i) i.value = '';
  var r = (typeof replyToMsgId !== 'undefined' && replyToMsgId) ? String(replyToMsgId) : '';
  return JSON.stringify({ text: t, reply: r });
})()"""


def _js(value):
    return json.dumps(value, ensure_ascii=False)


def _to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


class _Upload(object):
    """Идущая отправка одной части (до 10 файлов) — по чату."""

    def __init__(self, text, previews):
        self.text = text
        self.previews = previews
        self.percent = 0
        self.label = ""
        self.detail = None
        self.task = None


class Backend(QObject):
    chatsUpdated = Signal(str)
    historyLoaded = Signal(str, int, bool)
    messageReceived = Signal(str)
    messageUpdated = Signal(str)
    messageDeleted = Signal(str)
    reactionsUpdated = Signal(str)
    groupInfoLoaded = Signal(str)
    chatCardLoaded = Signal(str)
    searchResultsReceived = Signal(str)
    uploadProgress = Signal(int)
    scrollRequested = Signal(str, str)
    readState = Signal(str)

    def __init__(self, host):
        super().__init__(host)
        self.host = host                  # MessengerHost: окно с браузером, run_js()
        self.ready = False                # chat.js подключился к мосту
        self.chats = []
        self.active_chat_id = None
        self.pending_open = None          # чат из перехода «messenger, chat_id=N»
        self.token = client.file_token or client.token
        self.urls = Urls(client.host, lambda: self.token)
        self.pending_grouped = {}         # id сообщения → {"msg", "gen"} — ждём file_item
        self.recent_groups = {}           # уже показанные группы: id → сообщение
        self.orphan_files = {}            # parent_id → [file_item]
        self.uploads = {}                 # чат → _Upload
        self.max_seen = {}                # чат → наибольший номер сообщения
        self.goto_after = None            # (чат, сообщение, группа) — после загрузки истории
        self.card = None                  # открытая карточка группы: (chat_id, окно)
        self._gen = 0
        self._flush = {}                  # отложенные сигналы хода отправки
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.timeout.connect(self._flush_progress)
        self._token_timer = QTimer(self)
        self._token_timer.setInterval(4 * 60 * 1000)
        self._token_timer.timeout.connect(self.refresh_token)
        self._token_timer.start()

    # ── служебное ────────────────────────────────────────────────────────────

    @property
    def shell(self):
        return getattr(self.host, "shell", None)

    def run_js(self, code, callback=None):
        self.host.run_js(code, callback)

    def toast(self, text, kind="info"):
        from kov.ui import toast
        toast(self.host, text, kind)

    def refresh_token(self):
        """Файловый токен для ссылок на картинки живёт минуты — обновляем заранее."""
        def done(tok):
            if tok and tok != self.token:
                self.token = tok
                self.run_js("if (typeof setApiToken === 'function') setApiToken(%s);" % _js(tok))
        jobs.run(client.media_token, done=done, failed=lambda e: None, owner=self)

    def _here(self, chat_id):
        return str(self.active_chat_id) == str(chat_id)

    def _track(self, chat_id, msgs):
        try:
            cid = int(chat_id)
        except (TypeError, ValueError):
            return
        for m in msgs or []:
            n = _to_int(m.get("id"))
            if n and n > self.max_seen.get(cid, 0):
                self.max_seen[cid] = n

    # ── страница ─────────────────────────────────────────────────────────────

    @Slot()
    def page_ready(self):
        self.ready = True
        self.refresh_token()
        self.load_chats(self._open_pending)

    def open_chat(self, chat_id):
        """Открыть чат по номеру (переход из уведомления, карточки, поиска)."""
        self.pending_open = _to_int(chat_id)
        if self.ready and self.chats:
            self._open_pending()
        elif self.ready:
            self.load_chats(self._open_pending)

    def _open_pending(self):
        if self.pending_open:
            cid, self.pending_open = self.pending_open, None
            if not self.open_by_id(cid):
                self.toast("Чат не найден", "warning")

    def open_by_id(self, chat_id):
        chat = next((c for c in self.chats if str(c.get("id")) == str(chat_id)), None)
        if chat is None or not self.ready:
            return False
        payload = {"id": chat.get("id"), "name": chat.get("name"), "is_group": chat.get("is_group"),
                   "avatar_path": chat.get("image_path") if chat.get("is_group") else chat.get("avatar_url"),
                   "last_name": chat.get("last_name"), "first_name": chat.get("first_name")}
        self.run_js("openChatFromBackend(%s)" % _js(_js(payload)))
        return True

    # ── список чатов и счётчик ───────────────────────────────────────────────

    def push_chats(self):
        self.chatsUpdated.emit(_js(self.chats))
        try:
            from kov.notify_center import center
            center.set_chats(self.chats)
        except Exception:
            pass
        shell = self.shell
        if shell is not None and hasattr(shell, "set_chat_count"):
            visible = getattr(self.host, "on_screen", lambda: True)()
            shell.set_chat_count(util.unread_total(self.chats, self.active_chat_id, visible))

    def load_chats(self, then=None):
        def ok(lst):
            self.chats = lst if isinstance(lst, list) else []
            self.push_chats()
            if then:
                then()

        def bad(_e):
            self.push_chats()
            if then:
                then()
        jobs.run(lambda: client.get("chat/list") or [], done=ok, failed=bad, owner=self)

    # ── открытие и закрытие чата ─────────────────────────────────────────────

    @Slot()
    def chat_closed(self):
        self.active_chat_id = None
        self._set_center(None)
        self.push_chats()

    def _set_center(self, chat_id):
        try:
            from kov.notify_center import center
            center.active_chat = chat_id if getattr(self.host, "on_screen", lambda: True)() else None
        except Exception:
            pass

    @Slot(str)
    def mark_chat_as_read(self, chat_id):
        cid = _to_int(chat_id)
        if cid is None:
            return
        self.active_chat_id = cid
        self._set_center(cid)
        jobs.run(client.post, "chat/%d/read" % cid, done=lambda _r: None, failed=lambda e: None, owner=self)
        try:
            from kov import notify_ui
            notify_ui.dismiss_tag("chat-%d" % cid)     # прочитано — карточка об этом чате не нужна
        except Exception:
            pass
        self.load_read_state(cid)
        chat = next((c for c in self.chats if str(c.get("id")) == str(cid)), None)
        if chat is not None and chat.get("unread_count"):
            chat["unread_count"] = 0
        self.push_chats()

    def load_read_state(self, chat_id):
        def ok(data):
            if isinstance(data, dict) and self._here(chat_id):
                self.readState.emit(_js({"chat_id": chat_id, "upto": data.get("upto") or 0}))
        jobs.run(client.get, "chat/%d/read-state" % chat_id, done=ok, failed=lambda e: None, owner=self)

    # ── история ──────────────────────────────────────────────────────────────

    def _history(self, chat_id, query, newer):
        cid = _to_int(chat_id)
        if cid is None:
            return

        def work():
            return client.get("chat/history/%d" % cid, query=query) or []

        def ok(lst):
            first = not newer and not query.get("before_id")
            if first and str(cid) not in self.uploads:
                self.run_js("if (typeof tempUploadData !== 'undefined' && tempUploadData) tempUploadData = null;")
            msgs = aggregate([self.urls.format_urls(m) for m in (lst if isinstance(lst, list) else [])], self.urls)
            self._track(cid, msgs)
            self.historyLoaded.emit(_js(msgs), cid, bool(newer))
            if first:
                QTimer.singleShot(0, lambda: self.restore_upload(cid))
                if self.goto_after and self.goto_after[0] == cid:
                    _, m, g = self.goto_after
                    self.goto_after = None
                    QTimer.singleShot(30, lambda: self._goto(m, g))

        def bad(e):
            print("История чата не загрузилась: %s" % human_error(e, str(e)))
            self.historyLoaded.emit("[]", cid, bool(newer))
        jobs.run(work, done=ok, failed=bad, owner=self)

    @Slot(str, int, int)
    def load_history(self, chat_id, limit, offset):
        self._history(chat_id, {"limit": limit or 30, "offset": offset or 0}, False)

    @Slot(str, str)
    def load_older_history(self, chat_id, before_id):
        self._history(chat_id, {"limit": 30, "before_id": _to_int(before_id)}, False)

    @Slot(str, str)
    def load_newer_history(self, chat_id, after_id):
        self._history(chat_id, {"limit": 30, "after_id": _to_int(after_id)}, True)

    @Slot(str, str, str)
    def go_to_message(self, msg_id, group_id, chat_id):
        target = _to_int(chat_id)
        if target and target != self.active_chat_id:
            # переход к сообщению другого чата: сначала открываем чат, потом — переход
            if not self.open_by_id(target):
                self.toast("Чат не найден", "warning")
                return
            self.goto_after = (target, msg_id, group_id)
            return
        self._goto(msg_id, group_id)

    def _goto(self, msg_id, group_id):
        cid = self.active_chat_id
        if not cid:
            return

        def ok(lst):
            msgs = aggregate([self.urls.format_urls(m) for m in (lst or [])], self.urls)
            self._track(cid, msgs)
            self.run_js("if (typeof onHistoryAroundLoaded === 'function') onHistoryAroundLoaded(%s, %s, %s);"
                        % (_js(_js(msgs)), _js(str(msg_id)), _js(str(group_id or ""))))
        jobs.run(client.get, "chat/history/%d" % cid, query={"around_id": _to_int(msg_id), "limit": 31}, done=ok,
                 failed=lambda e: self.toast("Не удалось перейти к сообщению", "warning"), owner=self)

    @Slot(str)
    def get_group_info(self, chat_id):
        cid = _to_int(chat_id)
        if cid:
            jobs.run(client.get, "chat/group/%d" % cid, done=lambda info: self.groupInfoLoaded.emit(_js(info)),
                     failed=lambda e: None, owner=self)

    @Slot(str)
    def get_chat_card(self, chat_id):
        cid = _to_int(chat_id)
        if cid:
            jobs.run(client.get, "chat/info/%d" % cid, done=lambda info: self.chatCardLoaded.emit(_js(info)),
                     failed=lambda e: None, owner=self)

    @Slot(str)
    def search_messages(self, query):
        cid = self.active_chat_id
        if not cid:
            return

        def ok(res):
            lst = res if isinstance(res, list) else ((res or {}).get("results") or [])
            self.searchResultsReceived.emit(_js([self.urls.format_urls(m) for m in lst]))
        jobs.run(client.get, "chat/%d/search" % cid, query={"query": query, "limit": 50}, done=ok,
                 failed=lambda e: self.searchResultsReceived.emit("[]"), owner=self)

    # ── сообщения ────────────────────────────────────────────────────────────

    @Slot(str, str)
    def send_text_message(self, text, reply_to):
        cid = self.active_chat_id
        if not cid:
            return
        # метка отправителя: сервер вернёт её в рассылке, и временное сообщение заменится настоящим
        token = "c%s%s" % (_b36(int(time.time() * 1000)), _b36(random.randint(0, 36 ** 5))[-5:])
        payload = {"type": "message", "chat_id": cid, "text": text, "client_id": token}
        if _to_int(reply_to):
            payload["reply_to"] = _to_int(reply_to)
        ws = getattr(self.shell, "ws", None)
        if ws is not None:
            ws.send(payload)
        me = store.me or {}
        self.emit_message({
            "id": "tmp:" + token, "client_id": token, "pending": True, "chat_id": cid, "text": text,
            "sender_id": me.get("id") or client.user_id,
            "sender_name": " ".join(x for x in (me.get("last_name"), me.get("first_name"), me.get("middle_name")) if x),
            "sender_last_name": me.get("last_name"), "sender_first_name": me.get("first_name"),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + ".%03dZ" % (int(time.time() * 1000) % 1000),
            "parent_id": _to_int(reply_to), "files": [], "reactions": {}, "reactions_detailed": []})

    @Slot(str, str)
    def edit_message(self, msg_id, new_text):
        if self.active_chat_id and getattr(self.shell, "ws", None) is not None:
            self.shell.ws.send({"type": "edit", "chat_id": self.active_chat_id, "message_id": _to_int(msg_id),
                                "new_text": new_text})

    @Slot(str)
    def delete_message(self, msg_id):
        if getattr(self.shell, "ws", None) is not None:
            self.shell.ws.send({"type": "delete", "message_id": _to_int(msg_id)})

    @Slot(str, str)
    def confirm_delete_message(self, msg_id, preview):
        from kov.dialogs import confirm
        text = str(preview or "").strip()
        short = text[:87] + "…" if len(text) > 90 else text
        msg = ("Удалить сообщение «%s»? Отменить это будет нельзя." % short) if short else \
            "Удалить это сообщение? Отменить это будет нельзя."
        if confirm(self.host, "Удаление сообщения", msg, "Удалить", danger=True):
            self.delete_message(msg_id)

    @Slot(str)
    def confirm_delete_messages(self, ids_json):
        from kov.dialogs import confirm
        try:
            ids = [x for x in json.loads(ids_json or "[]")]
        except ValueError:
            ids = []
        if not ids:
            return
        if confirm(self.host, "Удаление сообщений",
                   "Удалить выбранные сообщения (%d)? Отменить это будет нельзя." % len(ids), "Удалить", danger=True):
            for i in ids:                       # сервер всё равно удалит только свои
                self.delete_message(str(i))

    @Slot(str, str)
    def send_reaction(self, msg_id, reaction_type):
        mid = _to_int(msg_id)
        if mid is None:
            return
        jobs.run(client.post, "chat/message/%d/reaction" % mid,
                 json_body={"message_id": mid, "reaction_type": reaction_type}, done=lambda _r: None,
                 failed=lambda e: self.toast("Реакция не отправлена", "warning"), owner=self)

    @Slot(str)
    def show_toast(self, text):
        self.toast(text, "info")

    @Slot(str)
    def copy_text(self, text):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(str(text or ""))
        self.toast("Скопировано", "success")

    # ── файлы: выбор, перетаскивание, отправка ───────────────────────────────

    @Slot()
    def open_file_dialog(self):
        if not self.active_chat_id:
            return
        from PySide6.QtWidgets import QFileDialog
        paths, _ = QFileDialog.getOpenFileNames(self.host.window(), "Файлы для отправки", "", "Все файлы (*)")
        if paths:
            self.open_send_dialog(paths)

    def drop_files(self, paths):
        """Файлы брошены в окно чата (Qt) — в окно отправки."""
        self.open_send_dialog([p for p in paths if os.path.isfile(p)])

    def open_send_dialog(self, paths):
        chat_id = self.active_chat_id
        if not chat_id or not paths:
            return

        def got(raw):
            try:
                state = json.loads(raw or "{}")
            except (TypeError, ValueError):
                state = {}
            from kov.messenger.send import SendDialog
            dlg = SendDialog(self.host, paths, state.get("text") or "", MAX_PENDING)
            result = dlg.run()
            if result is None:
                restored = dlg.caption_text()
                self.run_js("(function (t) { var i = document.getElementById('messageInput'); if (!i) return;"
                            " i.value = t; i.dispatchEvent(new Event('input')); Kov.UI.softFocus(i); })(%s);"
                            % _js(restored))
                return
            files, caption, edits, thumbs = result
            reply = _to_int(state.get("reply"))
            self.run_js("if (typeof clearInfoPanel === 'function') clearInfoPanel();")
            self.send_in_groups(chat_id, files, caption, reply, edits, thumbs)
        self.run_js(JS_TAKE_INPUT, got)

    @Slot(str, str, str)
    def send_message_with_files(self, text, files_json, reply_to):
        """Старый путь chat.js: файлы, приложенные под строкой ввода (у ПК это настоящие пути)."""
        if not self.active_chat_id:
            return
        try:
            items = json.loads(files_json or "[]") or []
        except ValueError:
            items = []
        paths = [i.get("filepath") for i in items if isinstance(i, dict) and i.get("filepath")
                 and os.path.isfile(i.get("filepath"))]
        if not paths:
            if text:
                self.send_text_message(text, reply_to)
            return
        self.send_in_groups(self.active_chat_id, paths, text, _to_int(reply_to), {}, {})

    def send_in_groups(self, chat_id, paths, text, reply_to, edits, thumbs):
        """Одно сообщение — не больше 10 файлов: остальное уходит следующими сообщениями по порядку."""
        parts = util.parts_of(list(paths))
        queue = []
        for k, part in enumerate(parts):
            start = k * util.MAX_ATTACHMENTS
            first = k == 0
            queue.append((part, text if first else "", reply_to if first else None,
                          util.split_edits(edits, start, len(part))))

        def step():
            if not queue:
                return
            part, t, r, e = queue.pop(0)
            self.send_group(chat_id, part, t, r, e, thumbs, step)
        step()

    def _previews(self, paths, thumbs):
        """Плитки временного сообщения: миниатюры из окна отправки (PNG в base64)."""
        import base64
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice
        out = []
        for p in paths:
            name = os.path.basename(p)
            item = {"filename": name, "size": _size(p), "is_image": is_image(name)}
            img = (thumbs or {}).get(p)
            if img is not None and not img.isNull() and (is_image(name) or is_video(name)):
                ba = QByteArray()
                buf = QBuffer(ba)
                buf.open(QIODevice.WriteOnly)
                img.save(buf, "PNG")
                item["preview_data"] = base64.b64encode(bytes(ba)).decode("ascii")
                item["is_image"] = True          # кадр видео — картинкой
            out.append(item)
        return out

    def send_group(self, chat_id, paths, text, reply_to, edits, thumbs, after=None):
        """Одна отправка на группу файлов: сжатие на компьютере, ход — на плитках временного сообщения."""
        from kov.files import UploadTask
        last_before = self.max_seen.get(int(chat_id), 0)
        previews = self._previews(paths, thumbs)
        up = _Upload(text, previews)
        self.uploads[str(chat_id)] = up
        if self._here(chat_id):
            self.run_js("showTempUploadMessage(%s, %s);" % (_js(text or ""), _js(previews)))
        self._emit_progress(chat_id, 0)
        fields = {"chat_id": chat_id}
        if text:
            fields["captions"] = text
        if reply_to:
            fields["reply_to_id"] = int(reply_to)
        if edits:
            fields["edits"] = json.dumps(edits, ensure_ascii=False)
        task = UploadTask([(p, os.path.basename(p)) for p in paths], "chat/grouped-message", fields)
        up.task = task

        def progress(p, label, detail):
            up.percent = min(99, int(round(p * 100)))
            up.label = label
            if detail:
                up.detail = detail
            if self._here(chat_id):
                self._flush[str(chat_id)] = (up.percent, label, up.detail)
                if not self._flush_timer.isActive():
                    self._flush_timer.start(90)

        def done(created):
            self.uploads.pop(str(chat_id), None)
            self._flush.pop(str(chat_id), None)
            self._emit_progress(chat_id, 100)

            def finish():
                QTimer.singleShot(3500, lambda: self._emit_progress(chat_id, -2))
                self.apply_sent(chat_id, created, len(paths))
                if after:
                    after()
            # полосу видно на сотне: сразу подставить сообщение — человек не увидит конца загрузки
            QTimer.singleShot(650, finish)

        def failed(kind, text_):
            self.uploads.pop(str(chat_id), None)
            self._flush.pop(str(chat_id), None)
            if kind == "aborted":
                self._emit_progress(chat_id, -2)
                self.toast("Отправка файлов отменена", "info")
                if after:
                    after()
                return
            if kind in ("network", "stalled"):
                names = [os.path.basename(p) for p in paths]

                def found(saved):
                    if saved:
                        self._emit_progress(chat_id, 100)
                        QTimer.singleShot(1500, lambda: self._emit_progress(chat_id, -2))
                        self.apply_sent(chat_id, saved, len(paths))
                    else:
                        self._emit_progress(chat_id, -2)
                        self.toast("Не удалось отправить файлы: " + text_, "error")
                    if after:
                        after()
                jobs.run(self.find_sent, chat_id, names, last_before, done=found,
                         failed=lambda e: found(None), owner=self)
                return
            self._emit_progress(chat_id, -2)
            self.toast("Не удалось отправить файлы: " + (text_ or ""), "error")
            if after:
                after()
        task.progress.connect(progress)
        task.finished.connect(done)
        task.failed.connect(failed)
        task.start()

    def _emit_progress(self, chat_id, value):
        if self._here(chat_id):
            self.uploadProgress.emit(int(value))

    def _flush_progress(self):
        for chat_id, (percent, label, detail) in list(self._flush.items()):
            self._flush.pop(chat_id, None)
            if not self._here(chat_id):
                continue
            self.run_js("if (typeof tempUploadData !== 'undefined' && tempUploadData) tempUploadData.label = %s;" % _js(label))
            self.uploadProgress.emit(percent)
            if detail:
                self.run_js("if (typeof setTempDetail === 'function') setTempDetail(%s);" % _js(detail))

    def restore_upload(self, chat_id):
        """Вернулись в чат, где идёт отправка, — пузырь с ходом загрузки снова на месте."""
        up = self.uploads.get(str(chat_id))
        if up is None or not self._here(chat_id):
            return
        self.run_js("if (typeof tempUploadData !== 'undefined' && !tempUploadData) { showTempUploadMessage(%s, %s);"
                    " tempUploadData.label = %s; }" % (_js(up.text or ""), _js(up.previews), _js(up.label)))
        self.uploadProgress.emit(up.percent)
        if up.detail:
            self.run_js("if (typeof setTempDetail === 'function') setTempDetail(%s);" % _js(up.detail))

    @Slot()
    def cancel_upload(self):
        up = self.uploads.get(str(self.active_chat_id))
        if up is not None and up.task is not None:
            up.task.cancel()

    def find_sent(self, chat_id, names, last_before):
        """В фоне: своё сообщение с этими файлами после обрыва связи (ответ сервера мог потеряться)."""
        me = str(client.user_id)
        stem = lambda n: os.path.splitext(str(n or ""))[0].lower()  # noqa: E731
        want = set(stem(n) for n in names)
        for delay in (0, 1.5, 3, 4):
            if delay:
                time.sleep(delay)
            try:
                lst = client.get("chat/history/%d" % int(chat_id), query={"limit": 20}) or []
            except Exception:
                continue
            for m in aggregate([self.urls.format_urls(x) for x in lst], self.urls):
                if str(m.get("sender_id")) == me and (_to_int(m.get("id")) or 0) > last_before and \
                        any(stem(file_name(f)) in want for f in m.get("files") or []):
                    return m
        return None

    def apply_sent(self, chat_id, created, expected):
        """Ответ сервера на отправку рисуется сразу; не хватает файлов — добираем из истории."""
        if not isinstance(created, dict) or created.get("id") is None:
            return
        if not self._here(chat_id):
            return
        failed = created.get("failed") if isinstance(created.get("failed"), list) else []
        if failed:
            self.toast("Не удалось сохранить: " + ", ".join(str(x) for x in failed), "warning")
        mid = str(created["id"])
        self.pending_grouped.pop(mid, None)
        msg = dict(created)
        if not isinstance(msg.get("files"), list):
            msg["files"] = []
        msg.setdefault("chat_id", chat_id)
        if not msg.get("chat_id"):
            msg["chat_id"] = chat_id
        self.emit_message(msg)
        if len(msg["files"]) >= max(0, expected - len(failed)):
            return

        def retry(delays):
            if not delays or not self._here(chat_id):
                return

            def ok(lst):
                fresh = next((m for m in aggregate([self.urls.format_urls(x) for x in (lst or [])], self.urls)
                              if str(m.get("id")) == mid), None)
                if fresh and len(fresh.get("files") or []) > len(msg["files"]):
                    self.emit_message(fresh)
                    return
                retry(delays[1:])
            QTimer.singleShot(delays[0], lambda: jobs.run(
                client.get, "chat/history/%d" % int(chat_id), query={"around_id": int(mid), "limit": 5},
                done=ok, failed=lambda e: retry(delays[1:]), owner=self))
        retry([900, 3000])

    # ── просмотр и скачивание ────────────────────────────────────────────────

    @Slot(str, str)
    def open_media(self, msg_id, media_json):
        try:
            item = json.loads(media_json)
        except (TypeError, ValueError):
            return
        self.run_js(JS_CHAT_MESSAGES, lambda raw: self._open_media(msg_id, item, raw))

    def _open_media(self, msg_id, item, raw):
        try:
            msgs = json.loads(raw or "[]")
        except (TypeError, ValueError):
            msgs = []
        files = item.get("files") or [item]
        clicked = file_name(item)
        everything = util.chat_media(msgs, self.urls, client.user_id)
        at = next((i for i, m in enumerate(everything) if m["msg"] == str(msg_id) and m["filename"] == clicked), -1)
        if at >= 0:
            media, idx = everything, at
        else:
            media = [{"src": self.urls.media_src(f), "filename": file_name(f), "msg": str(msg_id), "id": f.get("id")}
                     for f in files if is_media(file_name(f))]
            idx = max(0, next((i for i, m in enumerate(media) if m["filename"] == clicked), 0))
        if not media:
            return

        def preview(m):
            return util.preview_text(m.get("caption") or "") or "Файл"
        actions = {
            "reply": lambda m: self.run_js("if (typeof prepareReply === 'function') prepareReply(%s, %s);"
                                           % (_js(m.get("msg")), _js(preview(m)))) if m.get("msg") else None,
            "forward": lambda m: self.forward_message(m.get("msg"), preview(m)) if m.get("msg") else None,
            "remove": lambda m: self.confirm_delete_message(m.get("msg"), preview(m)) if m.get("mine") else None,
            "can_remove": lambda m: bool(m.get("mine")),
        }
        from kov.viewer import MediaViewer
        MediaViewer.show_list(self.host, media, idx, actions)

    @Slot(str, str)
    def open_file(self, msg_id, file_json):
        try:
            f = json.loads(file_json)
        except (TypeError, ValueError):
            return
        name = file_name(f) or "Файл"
        if is_media(name):
            self.open_media(msg_id, file_json)
            return
        from kov import files as kfiles
        kfiles.open_external(self.host, {"src": self.urls.file_href(f), "filename": name,
                                         "id": f.get("id") or msg_id})

    @Slot(str, str)
    def download_file(self, msg_id, file_json):
        try:
            f = json.loads(file_json)
        except (TypeError, ValueError):
            return
        files = f.get("files") or [f]
        from kov import files as kfiles
        for i, one in enumerate(files):
            spec = {"src": self.urls.file_href(one), "filename": file_name(one) or "file",
                    "id": one.get("id") or msg_id}
            QTimer.singleShot(i * 300, lambda s=spec: kfiles.download(self.host, s))

    # ── окна мессенджера ─────────────────────────────────────────────────────

    @Slot(str, str)
    def forward_message(self, msg_id, preview):
        from kov.messenger import dialogs
        if _to_int(msg_id):
            dialogs.forward_dialog(self, [int(msg_id)], preview)

    @Slot(str)
    def forward_messages(self, ops_json):
        from kov.messenger import dialogs
        try:
            ops = json.loads(ops_json or "[]") or []
        except ValueError:
            ops = []
        if ops:
            dialogs.forward_dialog(self, ops, "Сообщений: %d" % len(ops))

    @Slot()
    def show_add_menu(self):
        from kov.messenger import dialogs
        dialogs.add_menu(self)

    @Slot(str)
    def request_user_info_card(self, user_id):
        from kov.messenger import dialogs
        if _to_int(user_id):
            dialogs.user_card(self, int(user_id))

    @Slot(str)
    def request_chat_info_card(self, chat_id):
        from kov.messenger import dialogs
        if _to_int(chat_id):
            dialogs.info_card(self, int(chat_id))

    @Slot(str)
    def open_group_settings(self, chat_id):
        self.request_chat_info_card(chat_id)

    # ── события сокета ───────────────────────────────────────────────────────

    def emit_message(self, msg):
        parent = str(msg.get("id"))
        if parent in self.orphan_files:
            msg["files"] = list(msg.get("files") or []) + self.orphan_files.pop(parent)
        if msg.get("group_id"):
            self.recent_groups[parent] = msg
            QTimer.singleShot(120000, lambda: self.recent_groups.pop(parent, None)
                              if self.recent_groups.get(parent) is msg else None)
        self._track(msg.get("chat_id"), [msg])
        self.messageReceived.emit(_js(self.urls.format_urls(msg)))

    def on_event(self, evt):
        kind = evt.get("type") or ""
        if kind == "chat.read":
            # кто-то прочитал чат — у отправителя появляется вторая галочка
            if self._here(evt.get("chat_id")) and str(evt.get("user_id")) != str(client.user_id):
                self.readState.emit(_js({"chat_id": evt.get("chat_id"), "upto": evt.get("upto") or 0}))
            return
        if not self.ready:
            if kind == "chats_updated" and isinstance(evt.get("chats"), list):
                self.chats = evt["chats"]
            return
        if kind == "file_item" or evt.get("special") == "file_item":
            parent = str(evt.get("parent_id") or evt.get("group_id") or "")
            name = evt.get("file_name") or evt.get("filename") or "Файл"
            item = {"id": evt.get("id"), "file_name": name, "filename": name, "file_path": evt.get("file_path"),
                    "url": evt.get("url"), "size": evt.get("size") or evt.get("file_size"),
                    "is_image": evt.get("is_image") or is_media(name)}
            if parent and parent in self.pending_grouped:
                self.pending_grouped[parent]["msg"]["files"].append(item)
            elif parent and parent in self.recent_groups:
                msg = self.recent_groups[parent]          # сообщение уже нарисовано — дополняем его файлом
                msg["files"] = list(msg.get("files") or []) + [item]
                self.emit_message(msg)
            elif parent:
                self.orphan_files.setdefault(parent, []).append(item)
            else:
                self.emit_message(dict(evt))
            return
        if kind in ("grouped_message", "message"):
            mid = str(evt.get("id"))
            if kind == "grouped_message" or evt.get("special") == "grouped_message" or evt.get("is_grouped"):
                msg = dict(evt)
                msg["files"] = list(evt.get("files") or [])
                self._gen += 1
                gen = self._gen
                self.pending_grouped[mid] = {"msg": msg, "gen": gen}

                def flush(m=mid, g=gen):
                    pend = self.pending_grouped.get(m)
                    if pend and pend["gen"] == g:
                        self.pending_grouped.pop(m, None)
                        self.emit_message(pend["msg"])
                QTimer.singleShot(500, flush)
                return
            self.emit_message(dict(evt))
            return
        if kind == "edit":
            self.messageUpdated.emit(_js(evt))
            return
        if kind == "delete":
            self.messageDeleted.emit(_js(evt))
            return
        if kind == "reaction":
            self.reactionsUpdated.emit(_js(evt))
            return
        if kind == "special_message":
            self.emit_message(dict(evt))
            return
        if kind == "chats_updated":
            self.chats = evt.get("chats") or []
            self.push_chats()
            return
        if kind in ("group_role_changed", "member_remove", "member_add", "member_leave", "owner_transfer",
                    "group_deleted", "participant_removed", "participants_added"):
            # права в группе изменились — открытая карточка перестраивается
            from kov.messenger import dialogs
            dialogs.refresh_card(self, evt.get("chat_id"), deleted=kind == "group_deleted")
            self.load_chats()
            return
        if kind == "group_updated":
            if self.active_chat_id and self._here(evt.get("chat_id")):
                self.get_group_info(str(self.active_chat_id))
            self.load_chats()
            return
        if kind in ("action_rejected", "error"):
            if evt.get("message"):
                self.toast(evt["message"], "warning")
            return
        if kind == "removed_from_chat":
            if self.active_chat_id and self._here(evt.get("chat_id")):
                self.run_js("if (typeof clearChat === 'function') clearChat();")
                self.run_js("var k = document.getElementById('kovChat'); if (k) k.classList.remove('chat-open');")
                self.chat_closed()
            self.toast("Вас удалили из чата", "warning")
            self.load_chats()

    def on_reconnect(self):
        """Связь вернулась: события за время обрыва не пришли — перечитываем список и открытый чат."""
        if not self.ready:
            return
        self.load_chats()
        if self.active_chat_id:
            n = self.max_seen.get(int(self.active_chat_id))
            if n:
                self.load_newer_history(str(self.active_chat_id), str(n))


def _b36(n):
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    n = int(n)
    if n == 0:
        return "0"
    out = []
    while n:
        n, r = divmod(n, 36)
        out.append(chars[r])
    return "".join(reversed(out))


def _size(p):
    try:
        return os.path.getsize(p)
    except OSError:
        return 0


# поток для фоновых вызовов без Qt (тестам — проверка, что модуль грузится без окна)
_LOCK = threading.Lock()
