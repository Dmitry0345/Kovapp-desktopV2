# -*- coding: utf-8 -*-
"""«Живые» данные тестового сервера: переписка, вложения, файлы задач.

Живут в памяти, пока тестовый сервер запущен: отправили фото — оно в чате
и у собеседника; перезапустили сервер — всё как в начале. Форматы ответов и
событий сокета — как у настоящего сервера (routers/chat.py, chat_ws.py,
files.py), поэтому веб-версия и APK работают с ними без поправок.
Только стандартная библиотека Python.
"""
import datetime as dt
import gzip
import re
import struct
import threading
import uuid
import zlib

LOCK = threading.RLock()
MESSAGES = {}          # chat_id → [сообщение] по возрастанию id
BLOBS = {}             # путь в хранилище → {"data", "mime", "thumb", "name"}
SESSIONS = {}          # докачка частями: id → {"data": bytearray, "owner", "name"}
TASK_FILES = {}        # id → файл задачи или этапа
REACTIONS = {}         # message_id → {user_id: тип реакции}
_SEQ = {"msg": 100000, "file": 9000}

IMAGE_EXT = {"png", "jpg", "jpeg", "gif", "bmp", "webp", "heic", "heif"}
MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp",
        "bmp": "image/bmp", "mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime", "pdf": "application/pdf",
        "txt": "text/plain; charset=utf-8", "log": "text/plain; charset=utf-8", "csv": "text/csv; charset=utf-8",
        "md": "text/markdown; charset=utf-8",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


def next_id(kind):
    with LOCK:
        _SEQ[kind] += 1
        return _SEQ[kind]


def ext_of(name):
    m = re.search(r"\.([A-Za-z0-9]+)$", name or "")
    return m.group(1).lower() if m else ""


def mime_of(name):
    return MIME.get(ext_of(name), "application/octet-stream")


def is_image(name):
    return ext_of(name) in IMAGE_EXT


# ===== картинки для истории переписки (PNG без сторонних библиотек) =====

def png(width, height, painter):
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            rows.extend(painter(x, y))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b""))


def scheme_png():
    """«Схема объекта»: план помещений с точками камер."""
    rooms = [(40, 40, 200, 150), (200, 40, 440, 150), (40, 150, 260, 280), (260, 150, 440, 280)]
    cams = [(70, 70), (410, 70), (70, 250), (410, 250), (230, 150)]

    def paint(x, y):
        for cx, cy in cams:
            if (x - cx) ** 2 + (y - cy) ** 2 <= 49:
                return (217, 83, 79)
        for x0, y0, x1, y1 in rooms:
            if x0 <= x <= x1 and y0 <= y <= y1 and (x in (x0, x0 + 1, x1, x1 - 1) or y in (y0, y0 + 1, y1, y1 - 1)):
                return (38, 50, 56)
        if x % 20 == 0 or y % 20 == 0:
            return (226, 234, 238)
        return (250, 252, 253)
    return png(480, 320, paint)


def site_png():
    """«Фото с объекта»: здание с окнами на фоне неба."""
    def paint(x, y):
        if 120 <= x <= 360 and 90 <= y <= 280:
            if (x - 120) % 40 in range(12, 30) and (y - 90) % 45 in range(12, 32):
                return (245, 179, 1) if (x // 40 + y // 45) % 3 == 0 else (120, 160, 180)
            return (96, 110, 120)
        if y > 280:
            return (84, 110, 90)
        k = y / 280.0
        return (int(120 + 90 * k), int(170 + 60 * k), int(225 + 20 * k))
    return png(480, 320, paint)


# ===== сообщения =====

def _emp_names(emp, uid):
    e = emp.get(uid) or {}
    last, first = e.get("last_name") or "", e.get("first_name") or ""
    return ("%s %s" % (last, first)).strip() or "Неизвестный", last, first


def store_blob(path, data, name, thumb=None):
    BLOBS[path] = {"data": data, "mime": mime_of(name), "thumb": thumb, "name": name}


def make_message(chat_id, sender, emp, text=None, ts=None, file=None, special=None, group_id=None, parent_id=None):
    """Сообщение в формате истории настоящего сервера. file = (имя, байты, миниатюра)."""
    mid = next_id("msg")
    full, last, first = _emp_names(emp, sender)
    msg = {"id": mid, "chat_id": chat_id, "sender_id": sender, "sender_name": full, "sender_last_name": last,
           "sender_first_name": first, "sender_avatar_url": None, "text": text,
           "timestamp": (ts or dt.datetime.now()).isoformat(), "is_read": False, "is_edited": False,
           "special": special, "metadata": {}, "group_id": group_id, "parent_id": parent_id, "files": [],
           "reactions": {}, "reactions_detailed": [], "is_grouped": bool(group_id),
           "file_path": None, "file_name": None, "file_size": None, "url": None}
    if file:
        name, data, thumb = file
        path = "messenger/%d/%s_%s" % (chat_id, uuid.uuid4().hex[:8], name)
        store_blob(path, data, name, thumb)
        info = {"id": mid, "path": path, "name": name, "filepath": path, "filename": name, "file_name": name,
                "file_path": path, "size": len(data), "file_size": len(data), "is_image": is_image(name),
                "url": "/file_storage/" + path}
        msg["files"] = [info]
        msg.update(file_path=path, file_name=name, file_size=len(data), url=info["url"])
    return msg


def seed(chat_id, members, emp):
    """История переписки при первом открытии чата — чтобы было что посмотреть."""
    with LOCK:
        if chat_id in MESSAGES:
            return
        now = dt.datetime.now().replace(second=0, microsecond=0)

        def at(days, h, m):
            return (now - dt.timedelta(days=days)).replace(hour=h, minute=m)
        msgs = []
        people = sorted(members)
        if chat_id == 900:
            boss, head, worker = 1, 3, 6
            msgs += [make_message(chat_id, boss, emp, "Коллеги, завтра начинаем монтаж на «Северном». Сбор в 8:30 на объекте.", at(1, 9, 0)),
                     make_message(chat_id, head, emp, "Принято. Кабель и крепёж со склада забираем сегодня.", at(1, 9, 5)),
                     make_message(chat_id, worker, emp, None, at(0, 9, 38), file=("Фото_с_объекта.png", site_png(), None)),
                     make_message(chat_id, worker, emp, "Фото с объекта", at(0, 9, 40))]
        elif len(people) == 2:
            a, b = people
            msgs += [make_message(chat_id, b, emp, "Добрый день! Скиньте, пожалуйста, схему по объекту.", at(2, 16, 20)),
                     make_message(chat_id, a, emp, "Добрый, сейчас пришлю.", at(2, 16, 32)),
                     make_message(chat_id, a, emp, None, at(2, 16, 33), file=("Схема_объекта.png", scheme_png(), None)),
                     make_message(chat_id, b, emp, "Спасибо, получил 👍", at(2, 16, 40)),
                     make_message(chat_id, a, emp, None, at(0, 10, 14),
                                  file=("Смета_по_объекту.csv", "Позиция;Кол-во;Цена\nКамера IP 4Мп;8;7200\nКабель UTP, м;300;38\n".encode("utf-8"), None)),
                     make_message(chat_id, a, emp, "Документы по объекту отправил", at(0, 10, 15))]
        for m in msgs:
            m["is_read"] = True
        MESSAGES[chat_id] = msgs


def last_message(chat_id):
    with LOCK:
        msgs = MESSAGES.get(chat_id) or []
        return msgs[-1] if msgs else None


def history(chat_id, q, emp=None):
    """Как GET chat/history/{id}: от новых к старым, с пагинацией и реакциями."""
    with LOCK:
        msgs = list(MESSAGES.get(chat_id) or [])
    limit = max(1, min(1000, int(q.get("limit", 50))))
    if q.get("before_id"):
        page = [m for m in msgs if m["id"] < int(q["before_id"])][-limit:]
    elif q.get("after_id"):
        page = [m for m in msgs if m["id"] > int(q["after_id"])][:limit]
    elif q.get("around_id"):
        ids = [m["id"] for m in msgs]
        pos = ids.index(int(q["around_id"])) if int(q["around_id"]) in ids else len(ids) - 1
        page = msgs[max(0, pos - limit // 2):max(0, pos - limit // 2) + limit]
    else:
        offset = int(q.get("offset", 0))
        end = len(msgs) - offset
        page = msgs[max(0, end - limit):max(0, end)]
    out = []
    for m in reversed(page):
        row = dict(m)
        row["reactions"], row["reactions_detailed"] = reactions_of(m["id"], emp or {})
        out.append(row)
    return out


def reactions_of(message_id, emp):
    """Как build_message_reactions сервера: {user_id: тип} и подробный список с именами."""
    with LOCK:
        mine = dict(REACTIONS.get(message_id) or {})
    detailed = []
    for user_id, kind in mine.items():
        full, last, first = _emp_names(emp, user_id)
        detailed.append({"user_id": user_id, "reaction_type": kind, "name": full, "last_name": last,
                         "first_name": first, "avatar_url": None})
    return {str(u): k for u, k in mine.items()}, detailed


def react(message_id, user_id, kind, emp):
    """Та же реакция второй раз — снять, другая — заменить (как POST …/reaction)."""
    with LOCK:
        mine = REACTIONS.setdefault(message_id, {})
        if not kind:
            pass
        elif mine.get(user_id) == kind:
            mine.pop(user_id, None)
        else:
            mine[user_id] = kind
    return reactions_of(message_id, emp)


def add_text(chat_id, sender, emp, text, reply_to=None):
    with LOCK:
        msg = make_message(chat_id, sender, emp, text, parent_id=reply_to)
        MESSAGES.setdefault(chat_id, []).append(msg)
        return msg


def edit_text(chat_id, sender, message_id, new_text):
    with LOCK:
        for m in MESSAGES.get(chat_id) or []:
            if m["id"] == message_id and m["sender_id"] == sender:
                m["text"], m["is_edited"] = new_text, True
                return m
    return None


def find_message(message_id):
    with LOCK:
        for chat_id, msgs in MESSAGES.items():
            for m in msgs:
                if m["id"] == message_id:
                    return chat_id, m
    return None, None


def delete_message(sender, message_id):
    with LOCK:
        chat_id, m = find_message(message_id)
        if m is None or m["sender_id"] != sender:
            return None
        MESSAGES[chat_id] = [x for x in MESSAGES[chat_id] if x["id"] != message_id and x.get("group_id") != m["id"]]
        return chat_id


# ===== приём файлов (форма multipart, как у веб-версии) =====

def parse_multipart(body, content_type):
    m = re.search(r'boundary="?([^";]+)"?', content_type or "")
    if not m:
        raise ValueError("нет границы multipart")
    boundary = b"--" + m.group(1).encode()
    fields, files = {}, {}
    for part in body.split(boundary)[1:]:
        if part.startswith(b"--"):
            break
        if part.startswith(b"\r\n"):
            part = part[2:]
        head, _, data = part.partition(b"\r\n\r\n")
        if data.endswith(b"\r\n"):
            data = data[:-2]
        headers = head.decode("utf-8", "replace")
        name = re.search(r'(?<![A-Za-z])name="([^"]*)"', headers)
        fname = re.search(r'filename="([^"]*)"', headers)
        if not name:
            continue
        if fname is not None:
            files.setdefault(name.group(1), []).append({"filename": fname.group(1), "data": data})
        else:
            fields[name.group(1)] = data.decode("utf-8", "replace")
    return fields, files


def resolve_items(meta, files):
    """Список файлов из формы: содержимое из части формы или из сессии докачки."""
    parts, thumbs = files.get("files", []), files.get("thumbs", [])
    if not meta:
        meta = [{"name": p["filename"], "src": "file", "file": i} for i, p in enumerate(parts)]
    items = []
    for m in meta:
        src = m.get("src") or "file"
        if src == "file":
            index = int(m.get("file", 0))
            if index >= len(parts):
                continue
            part = parts[index]
            data = part["data"]
            name = part["filename"] if part["filename"] not in ("", "blob", "undefined") else m.get("name", "file")
        elif src == "session":
            with LOCK:
                s = SESSIONS.pop(m.get("session"), None)
            if s is None:
                raise ValueError("сессия докачки не найдена — отправьте файл заново")
            data, name = bytes(s["data"]), m.get("name") or s["name"]
            if name.lower().endswith((".mov", ".avi", ".mkv", ".3gp", ".m4v")) and m.get("prep") == "device":
                name = re.sub(r"\.[^.]+$", ".mp4", name)       # сжатое телефоном видео — mp4
        else:
            continue                                            # токены дубликатов тестовый сервер не выдаёт
        if m.get("enc") == "gzip":
            data = gzip.decompress(data)
            name = m.get("name") or name
        thumb = None
        if m.get("thumb") is not None and int(m["thumb"]) < len(thumbs):
            thumb = thumbs[int(m["thumb"])]["data"]
        items.append({"name": name, "data": data, "thumb": thumb, "meta": m})
    return items


def grouped(chat_id, sender, emp, text, reply_to, items):
    """Как POST chat/grouped-message: родительское сообщение и по сообщению на файл."""
    with LOCK:
        group_id = str(uuid.uuid4())
        now = dt.datetime.now()
        parent = make_message(chat_id, sender, emp, text or None, now, special="file_group" if items else None,
                              group_id=group_id, parent_id=reply_to)
        created = [parent]
        saved = []
        for it in items:
            msg = make_message(chat_id, sender, emp, None, now, file=(it["name"], it["data"], it["thumb"]),
                               special="file_item", group_id=group_id, parent_id=reply_to)
            meta = it["meta"]
            for key in ("width", "height", "duration", "thumb_hash"):
                if meta.get(key) is not None:
                    msg["files"][0][key] = meta[key]
            created.append(msg)
            saved.append(msg["files"][0])
        MESSAGES.setdefault(chat_id, []).extend(created)
        response = dict(parent, metadata={"file_count": len(saved)} if saved else None, files=saved, failed=[])
        return response


# ===== докачка частями и проверка дубликатов =====

def session_open(owner, body):
    sid = uuid.uuid4().hex
    with LOCK:
        SESSIONS[sid] = {"data": bytearray(), "owner": owner, "name": str(body.get("name") or "file")}
    return {"id": sid, "received": 0}


def session_get(sid, owner):
    with LOCK:
        s = SESSIONS.get(sid)
        if s is None or s["owner"] != owner:
            return None
        return s


# ===== файлы задач и этапов =====

def add_task_file(task_id, stage_id, name, data, thumb, meta, uploader):
    fid = next_id("file")
    rec = {"id": fid, "filename": name, "file_name": name, "size": len(data), "file_size": len(data),
           "created_at": dt.datetime.now().isoformat(), "uploaded_by": uploader, "uploaded_by_id": uploader.get("id"),
           "task_id": task_id, "stage_detail_id": stage_id, "file_kind": "stage" if stage_id else "task",
           "thumb_hash": meta.get("thumb_hash"), "width": meta.get("width"), "height": meta.get("height")}
    with LOCK:
        store_blob("taskfile/%d" % fid, data, name, thumb)
        TASK_FILES[fid] = rec
    return rec


def task_files(task_id, stage_id=None):
    with LOCK:
        rows = [dict(r) for r in TASK_FILES.values()
                if r["task_id"] == task_id and (r["stage_detail_id"] == stage_id)]
    return sorted(rows, key=lambda r: r["id"], reverse=True)


def delete_task_file(fid, uid, allowed):
    with LOCK:
        rec = TASK_FILES.get(fid)
        if rec is None:
            return False
        if rec["uploaded_by_id"] != uid and not allowed:
            raise PermissionError("Удалить файл может только тот, кто его прикрепил")
        TASK_FILES.pop(fid, None)
        BLOBS.pop("taskfile/%d" % fid, None)
        return True


# ===== отдача файлов =====

def blob_for_storage(path):
    """/file_storage/... : клиент добавляет .gz к картинкам и .thumb.jpg к миниатюрам."""
    path = path.split("?", 1)[0]
    thumb = path.endswith(".thumb.jpg")
    if thumb:
        path = path[:-len(".thumb.jpg")]
    if path.endswith(".gz"):
        path = path[:-3]
    with LOCK:
        blob = BLOBS.get(path)
    if blob is None:
        return None
    if thumb and blob.get("thumb"):
        return {"data": blob["thumb"], "mime": "image/jpeg", "name": blob["name"] + ".jpg"}
    return blob
