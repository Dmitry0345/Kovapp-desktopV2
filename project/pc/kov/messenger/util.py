# -*- coding: utf-8 -*-
"""Чистые функции мессенджера — те же, что в js/views/messenger.js сайта:
адреса файлов, сборка сообщений группы файлов, медиа открытого чата.
Без Qt: их проверяют программные тесты."""

import datetime as dt
import re
import urllib.parse

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".heic")
VIDEO_EXT = (".mp4", ".avi", ".mov", ".mkv", ".webm", ".ts")
MAX_ATTACHMENTS = 10        # файлов в одном сообщении
MAX_PENDING = 100           # за одну отправку: больше 10 — несколько сообщений по 10

ROLE_RU = {"owner": "владелец", "admin": "администратор", "administrator": "администратор",
           "member": "участник", "participant": "участник", "user": "участник"}


def role_label(role):
    return ROLE_RU.get(str(role or "").strip().lower(), "участник")


def file_name(f):
    return str((f or {}).get("filename") or (f or {}).get("file_name") or "")


def is_image(name):
    return str(name or "").lower().endswith(IMAGE_EXT)


def is_video(name):
    return str(name or "").lower().endswith(VIDEO_EXT)


def is_media(name):
    return is_image(name) or is_video(name)


def _num(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None


class Urls:
    """Адреса файлов сервера: host — адрес, token() — файловый токен для ссылок."""

    def __init__(self, host, token=lambda: ""):
        self.host = (host or "").rstrip("/")
        self.token = token

    def storage_path(self, p):
        clean = str(p or "").replace("\\", "/")
        if not clean:
            return ""
        if re.match(r"^https?://", clean, re.I) or clean.startswith("data:"):
            return clean
        if not clean.startswith("/"):
            clean = "/" + clean
        if not clean.startswith("/file_storage"):
            clean = "/file_storage" + clean
        return self.host + clean

    def format_urls(self, msg):
        if not msg:
            return msg
        for f in msg.get("files") or []:
            if isinstance(f, dict):
                u = f.get("url") or f.get("filepath") or f.get("file_path") or ""
                if u:
                    f["url"] = self.storage_path(u)
        if msg.get("file_path"):
            msg["url"] = self.storage_path(msg.get("url") or msg.get("file_path"))
        return msg

    def with_token(self, url):
        if not url or not re.match(r"^https?:", url, re.I):
            return url
        tok = self.token() or ""
        return url + ("&" if "?" in url else "?") + "token=" + urllib.parse.quote(tok, safe="")

    def media_src(self, f):
        """Картинки и видео сервер отдаёт распакованными по адресу файла; видео у него всегда .webm/.mp4."""
        u = self.storage_path(f.get("url") or f.get("file_path") or "")
        u = re.sub(r"\.gz$", "", u)
        if is_video(file_name(f)):
            u = re.sub(r"\.(avi|mov|mkv|ts|wmv|flv)$", ".webm", u, flags=re.I)
        return self.with_token(u)

    def file_href(self, f):
        return self.with_token(self.storage_path(f.get("url") or f.get("file_path") or ""))

    def avatar_src(self, path):
        """Фото сотрудника — адрес обработчика (/employees/7/photo), картинка группы — путь в хранилище."""
        if not path:
            return None
        clean = str(path).replace("\\", "/")
        if re.match(r"^https?://", clean, re.I) or clean.startswith("data:"):
            return clean
        if re.match(r"^/?employees/\d+/photo$", clean, re.I):
            return self.with_token(self.host + "/" + clean.lstrip("/"))
        return self.with_token(self.storage_path(clean))

    def thumb_src(self, f):
        """Миниатюра вложения: своя у фото, у видео — кадр сервера (.thumb.jpg)."""
        name = file_name(f)
        if is_video(name):
            u = re.sub(r"\.gz$", "", f.get("url") or "")
            u = re.sub(r"\.(avi|mov|mkv|ts|wmv|flv)$", ".webm", u, flags=re.I)
            return self.with_token(self.storage_path(u) + ".thumb.jpg") if u else ""
        t = f.get("thumb_url")
        if t:
            t = self.storage_path(t)
            if "/file_storage/" in t and not t.endswith(".gz"):
                t += ".gz"
            return self.with_token(t)
        return ""


def _when(m):
    s = str((m or {}).get("timestamp") or "")
    if not s:
        return 0.0
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d.timestamp()
    except ValueError:
        return 0.0


def aggregate(messages, urls):
    """Как aggregate() сайта: сообщения одной группы файлов → одно с files[]."""
    groups, order = {}, []
    for m in messages or []:
        gid = m.get("group_id")
        key = str(gid) if gid not in (None, "") and str(gid) not in ("", "0", "null") else "__single_%s" % m.get("id")
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(m)
    out = []
    for key in order:
        lst = groups[key]
        ids = set(_num(m.get("id")) for m in lst)
        parent = (next((m for m in lst if m.get("special") == "file_group"), None)
                  or next((m for m in lst if not m.get("parent_id") or _num(m.get("parent_id")) not in ids), None)
                  or next((m for m in lst if m.get("text")), None) or lst[0])
        files = []
        for m in lst:
            if m.get("file_path") or m.get("url"):
                name = file_name(m) or "Файл"
                info = {"id": m.get("id"), "filename": name, "file_name": name, "file_path": m.get("file_path"),
                        "url": urls.storage_path(m.get("url") or m.get("file_path")), "size": m.get("file_size") or 0,
                        "is_image": m.get("is_image") or is_image(name)}
                if not any(f.get("url") == info["url"] for f in files):
                    files.append(info)
            for f in m.get("files") or []:
                if not any(x.get("url") == f.get("url") for x in files):
                    files.append(f)
        merged = dict(parent)
        if merged.get("parent_id") and _num(merged.get("parent_id")) in ids:
            del merged["parent_id"]
        if not merged.get("text"):
            with_text = next((m for m in lst if m.get("text")), None)
            if with_text:
                merged["text"] = with_text["text"]
        if files:
            merged["files"] = files
        elif not merged.get("files"):
            merged["files"] = []
        if len(lst) > 1:
            merged["special"] = merged.get("special") or "grouped_message"
            merged["group_id"] = merged.get("group_id") or parent.get("group_id")
        out.append(merged)
    out.sort(key=_when)
    return out


def chat_media(messages, urls, me):
    """Фото и видео загруженной переписки — от старых к новым (для просмотра)."""
    def key(m):
        return (_when(m), _num(m.get("id")) or 0)
    out = []
    for msg in sorted([m for m in messages or [] if isinstance(m, dict)], key=key):
        files = msg.get("files") or ([msg] if (msg.get("file_path") or msg.get("url")) else [])
        mine = str(msg.get("sender_id")) == str(me)
        text = msg.get("text")
        caption = text if text and text != "null" else ""
        for f in files:
            name = file_name(f)
            if not is_media(name):
                continue
            out.append({"src": urls.media_src(f), "filename": name, "id": f.get("id") or msg.get("id"),
                        "msg": str(msg.get("id")), "who": msg.get("sender_name") or "", "at": msg.get("timestamp") or "",
                        "caption": caption, "preview": msg.get("__preview") or "", "mine": mine,
                        "thumb": urls.thumb_src(f), "size": f.get("file_size") or f.get("size") or 0})
    return out


def preview_text(text):
    """Как previewText chat.js: стикер и GIF — словом."""
    t = str(text or "").strip()
    if re.match(r"^\[\[sticker:[a-z]+/[a-z]+\]\]$", t):
        return "Стикер"
    if re.match(r"^\[\[gif:https://[^\s\]]+\]\]$", t):
        return "GIF"
    return str(text or "")


def unread_total(chats, active_chat=None, visible=True):
    """Сумма непрочитанных по чатам; открытый на экране чат не считается."""
    total = 0
    for c in chats or []:
        if visible and active_chat is not None and str(c.get("id")) == str(active_chat):
            continue
        try:
            total += int(c.get("unread_count") or 0)
        except (TypeError, ValueError):
            pass
    return total


def parts_of(paths, size=MAX_ATTACHMENTS):
    """Отправка больше десяти файлов — несколькими сообщениями по десять."""
    return [list(paths[i:i + size]) for i in range(0, len(paths), size)]


def split_edits(edits, start, count):
    out = {}
    for k, v in (edits or {}).items():
        try:
            i = int(k) - start
        except (TypeError, ValueError):
            continue
        if 0 <= i < count:
            out[str(i)] = v
    return out
