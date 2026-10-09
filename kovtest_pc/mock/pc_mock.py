# -*- coding: utf-8 -*-
"""Тестовый сервер для ПК-версии.

Основа — общий тестовый сервер проекта (android: mobile/tests/mock; копия
лежит рядом — mock_server.py, live_store.py, wsproto.py). Здесь добавлено то,
чего ему не хватает для проверки ПК-приложения: склад проекта с товарами и
выдачами, файлы разделов проекта, медиаконтент, создание и правка проектов,
группы мессенджера. Форматы ответов и событий — как у настоящего сервера
(server_app/routers/projects.py, warehouse.py, chat.py).

Канал уведомлений телефона (native push) ПК-версии не нужен: если кода сервера
рядом нет, вместо него ставится заглушка.

Запуск вручную:  python kovtest_pc/mock/pc_mock.py --port 8000
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import threading
import urllib.parse
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SKIP = object()
ROWS = 100


def find_mock_dir(explicit=None):
    """Где лежит mock_server.py: явно, из переменной или рядом с папкой Android."""
    pc = os.path.dirname(HERE)
    for cand in (explicit, os.environ.get("KOVAPP_MOCK_DIR"), HERE,
                 os.path.join(pc, "..", "android", "mobile", "tests", "mock"),
                 os.path.join(pc, "..", "..", "android", "mobile", "tests", "mock"),
                 os.path.join(pc, "..", "mobile", "tests", "mock")):
        if cand and os.path.exists(os.path.join(cand, "mock_server.py")):
            return os.path.abspath(cand)
    raise FileNotFoundError("Не найден общий тестовый сервер (mock_server.py). Укажите папку: --mock-dir "
                            "или переменная KOVAPP_MOCK_DIR (android: mobile/tests/mock)")


class State:
    """Данные, которых нет у общего тестового сервера."""

    def __init__(self, M, L):
        self.M, self.L = M, L
        self.lock = threading.RLock()
        self.rows = {}          # project_id → [строка склада]
        self.issues = []        # выдачи склада
        self.logs = []          # журнал правок ячеек
        self.editors = set()    # кому доверено править «Товар» и «Количество»
        self.files = {}         # id → файл раздела проекта
        self.archived = {}      # project_id → (кто, когда)
        self.seq = {"issue": 1, "pfile": 7000, "unit": 100, "project": 50, "log": 1, "eq": 10, "eqi": 500}
        self.seeded = False
        self.eq = None           # оборудование: id → позиция
        self.eq_issues = []      # выдачи оборудования
        self.eq_log = []         # журнал оборудования
        self.eq_photos = {}      # id → байты снимка
        self.emp_photos = {}     # сотрудник → байты фото
        self.private = set()     # личные чаты, созданные через API: (a, b)
        self.roles = {}          # (чат, сотрудник) → роль в группе
        self.group_images = {}   # чат → путь картинки группы

    def next(self, kind):
        with self.lock:
            self.seq[kind] += 1
            return self.seq[kind]

    # ── склад ────────────────────────────────────────────────────────────────
    def warehouse(self, pid):
        with self.lock:
            if pid not in self.rows:
                rows = [{"row_no": n, "id": pid * 1000 + n, "name": "", "quantity": None, "unit": "",
                         "stock_state": None, "stock_qty": None, "updated_at": None} for n in range(1, ROWS + 1)]
                if pid == 1:
                    demo = [("Кабель UTP cat.5e, бухта 305 м", 4, "уп", "enough", 6),
                            ("Камера IP 4 Мп купольная", 12, "шт", "enough", 12),
                            ("Коммутатор PoE 8 портов", 2, "шт", "short", 1),
                            ("Видеорегистратор 16 каналов", 1, "шт", "enough", 1),
                            ("Жёсткий диск 4 ТБ", 2, "шт", None, None)]
                    for i, (name, qty, unit, st, sq) in enumerate(demo):
                        rows[i].update(name=name, quantity=float(qty), unit=unit, stock_state=st, stock_qty=sq,
                                       updated_at=self.M.moment(-2))
                    # выдано: 1 бухта и все 12 камер; регистратор ждёт подтверждения
                    self.issues += [
                        {"id": self.next("issue"), "project_id": 1, "row_no": 1, "quantity": 1.0, "status": "confirmed",
                         "issuer_id": 9, "receiver_id": 6, "created_at": self.M.moment(-3), "confirmed_at": self.M.moment(-3)},
                        {"id": self.next("issue"), "project_id": 1, "row_no": 2, "quantity": 12.0, "status": "confirmed",
                         "issuer_id": 9, "receiver_id": 3, "created_at": self.M.moment(-2), "confirmed_at": self.M.moment(-2)},
                        {"id": self.next("issue"), "project_id": 1, "row_no": 4, "quantity": 1.0, "status": "pending",
                         "issuer_id": 9, "receiver_id": 1, "created_at": self.M.moment(0, 9), "confirmed_at": None}]
                self.rows[pid] = rows
            return self.rows[pid]

    def stats(self, pid, row_no):
        issued = pending = 0.0
        for i in self.issues:
            if i["project_id"] == pid and i["row_no"] == row_no:
                if i["status"] == "confirmed":
                    issued += i["quantity"]
                elif i["status"] == "pending":
                    pending += i["quantity"]
        return issued, pending

    def editors_of(self, pid, row_no):
        out = {}
        for log in self.logs:
            if log["project_id"] == pid and log["row_no"] == row_no:
                who = self.M.person(log["user_id"]) or {}
                out[log["field"]] = {"who": who.get("name") or "неизвестно", "at": log["at"]}
        return out

    def row_out(self, pid, r):
        issued, pending = self.stats(pid, r["row_no"])
        q = r["quantity"]
        return dict(r, issued=issued, pending=pending, left=None if q is None else round(q - issued, 3),
                    by=self.editors_of(pid, r["row_no"]))

    def access(self, uid, pid):
        acc = dict(self.M.warehouse_access(uid))
        acc["can_edit_goods"] = acc["can_edit_goods"] or uid in self.editors
        if pid in self.archived:
            acc = {k: (False if k.startswith("can_") and k != "can_view" else v) for k, v in acc.items()}
        return acc

    # ── оборудование ─────────────────────────────────────────────────────────
    def equipment(self):
        with self.lock:
            if self.eq is None:
                M = self.M
                self.eq = {}
                for e in M.EQUIPMENT:
                    self.eq[e["id"]] = {"id": e["id"], "name": e["name"], "quantity": e["quantity"], "unit": e["unit"],
                                        "updated_at": M.moment(-4), "updated_by": 10}
                now = M.moment(-1)
                # перфоратор у монтажника; кабель: часть на руках, часть возвращают; лестницу выдают директору
                self.eq_issues = [
                    {"id": self.next("eqi"), "item_id": 1, "quantity": 1.0, "status": "issued", "issuer_id": 9, "receiver_id": 6,
                     "created_at": now, "confirmed_at": now, "return_requested_at": None, "returned_to": None},
                    {"id": self.next("eqi"), "item_id": 3, "quantity": 100.0, "status": "issued", "issuer_id": 9,
                     "receiver_id": 6, "created_at": now, "confirmed_at": now, "return_requested_at": None, "returned_to": None},
                    {"id": self.next("eqi"), "item_id": 3, "quantity": 20.0, "status": "return_pending", "issuer_id": 9,
                     "receiver_id": 13, "created_at": now, "confirmed_at": now, "return_requested_at": M.moment(0, 9),
                     "returned_to": None},
                    {"id": self.next("eqi"), "item_id": 2, "quantity": 1.0, "status": "pending", "issuer_id": 9, "receiver_id": 1,
                     "created_at": M.moment(0, 8), "confirmed_at": None, "return_requested_at": None, "returned_to": None}]
            return self.eq

    def eq_out(self, item, uid):
        M = self.M
        live = [i for i in self.eq_issues if i["item_id"] == item["id"] and i["status"] in ("pending", "issued", "return_pending")]
        issued = sum(i["quantity"] for i in live if i["status"] in ("issued", "return_pending"))
        q = item["quantity"]
        return {"id": item["id"], "name": item["name"] or "", "quantity": q, "unit": item["unit"] or "",
                "has_photo": item["id"] in self.eq_photos, "issued": round(issued, 4),
                "left": None if q is None else round(q - issued, 4),
                "ready": bool((item["name"] or "").strip()) and q is not None and q > 0,
                "updated_at": item["updated_at"], "updated_by": M.person(item["updated_by"]),
                "issues": [{"id": i["id"], "item_id": i["item_id"], "quantity": i["quantity"], "status": i["status"],
                            "issuer": M.person(i["issuer_id"]), "receiver": M.person(i["receiver_id"]),
                            "returned_to": M.person(i["returned_to"]) if i["returned_to"] else None,
                            "created_at": i["created_at"], "confirmed_at": i["confirmed_at"],
                            "return_requested_at": i["return_requested_at"], "mine": i["receiver_id"] == uid,
                            "issued_by_me": i["issuer_id"] == uid}
                           for i in sorted(live, key=lambda x: x["created_at"], reverse=True)]}

    def eq_write(self, item_id, kind, uid, **extra):
        self.eq_log.append(dict({"id": self.next("log"), "item_id": item_id, "kind": kind, "user_id": uid,
                                 "created_at": dt.datetime.now().isoformat()}, **extra))

    # ── файлы разделов проекта ───────────────────────────────────────────────
    def seed_files(self):
        """Пара файлов в разделах проекта 1 и фото в задаче 101 — чтобы было что посмотреть."""
        with self.lock:
            if self.seeded:
                return
            self.seeded = True
        L = self.L
        scheme = L.scheme_png()
        site = L.site_png()
        self.add_file(1, "contracts", "Договор подряда №17.pdf", b"%PDF-1.4\n% demo\n" + b"0" * 4000, None, {}, 1)
        self.add_file(1, "contracts", "Смета.xlsx", b"PK\x03\x04" + b"0" * 9000, None, {}, 8)
        self.add_file(1, "project", "Схема размещения камер.png", scheme, None, {"width": 480, "height": 320}, 4)
        self.add_file(1, "extra", "Фасад.png", site, None, {"width": 480, "height": 320}, 3)
        L.add_task_file(101, 1012, "Монтаж камеры, подъезд 1.png", site, None, {"width": 480, "height": 320},
                        self.M.short(6))
        L.add_task_file(101, 1011, "Схема этажа.png", scheme, None, {"width": 480, "height": 320}, self.M.short(5))

    def add_file(self, pid, section, name, data, thumb, meta, uid):
        fid = self.next("pfile")
        rec = {"id": fid, "project_id": pid, "section": section, "filename": name,
               "filepath": "projects/%d/%s/%s" % (pid, section, name), "size": len(data),
               "extension": "." + self.L.ext_of(name) if self.L.ext_of(name) else None,
               "created_at": dt.datetime.now().isoformat(), "uploaded_by_id": uid, "uploaded_by": self.M.uploader(uid),
               "thumb_hash": meta.get("thumb_hash"), "width": meta.get("width"), "height": meta.get("height"),
               "duration": meta.get("duration")}
        with self.lock:
            self.L.store_blob("projectfile/%d" % fid, data, name, thumb)
            self.files[fid] = rec
        return rec


def _num(v):
    if v is None or v == "":
        return None
    return float(v)


def pc_api(h, S, method, path, q):  # noqa: C901 — одна таблица маршрутов
    M, L = S.M, S.L
    seg = path.split("/")
    if method == "POST" and path == "login":
        return SKIP
    if not path or seg[0] not in ("projects", "warehouse", "equipment", "employees", "organizations", "chat"):
        return SKIP
    uid = h.user()
    if seg[0] == "chat":
        return chat_api(h, S, uid, seg, method, q)
    if seg[0] == "organizations":
        return organizations_api(h, S, uid, seg[1:], method)
    if seg[0] == "employees":
        return employees_api(h, S, uid, seg[1:], method)
    S.seed_files()
    if seg[0] == "equipment":
        return equipment_api(h, S, uid, seg[1:], method)

    # ── проекты: список с фильтрами, создание, правка, завершение ────────────
    if path == "projects" and method == "GET":
        arch = str(q.get("is_archived", "false")).lower() == "true"
        out = [project_out(S, p, uid) for p in M.PROJECTS if (p["id"] in S.archived) == arch]
        if q.get("search"):
            out = [p for p in out if q["search"].lower() in p["name"].lower()]
        if q.get("organization"):
            out = [p for p in out if p.get("organization") == q["organization"]]
        if q.get("head_id"):
            out = [p for p in out if str(p.get("creator_id")) == str(q["head_id"])]
        key = q.get("sort") or "deadline_desc"
        field = {"deadline": "deadline", "name": "name", "created": "created_at"}.get(key.split("_")[0], "deadline")
        out.sort(key=lambda p: str(p.get(field) or ""), reverse=key.endswith("_desc"))
        return out
    if path == "projects" and method == "POST":
        data = h.body_json() or {}
        if not str(data.get("name") or "").strip():
            raise M.ApiError(400, "Укажите название проекта")
        p = {"id": S.next("project"), "name": data["name"].strip(), "description": data.get("description"),
             "organization": data.get("organization"), "responsible_id": data.get("responsible_id") or uid,
             "creator_id": uid, "deadline": data.get("deadline"), "created_at": dt.datetime.now().isoformat(),
             "updated_at": dt.datetime.now().isoformat()}
        M.PROJECTS.append(p)
        M.broadcast_all({"type": "project.created", "project_id": p["id"], "sender_id": uid})
        return (201, project_out(S, p, uid))
    if seg[0] == "projects" and len(seg) >= 2 and seg[1].isdigit():
        pid = int(seg[1])
        p = next((x for x in M.PROJECTS if x["id"] == pid), None)
        if p is None:
            raise M.ApiError(404, "Проект не найден")
        if len(seg) == 2 and method == "GET":
            return project_out(S, p, uid)
        if len(seg) == 2 and method == "PUT":
            data = h.body_json() or {}
            for k in ("name", "description", "organization", "responsible_id", "deadline"):
                if k in data:
                    p[k] = data[k]
            p["updated_at"] = dt.datetime.now().isoformat()
            M.broadcast_all({"type": "project.updated", "project_id": pid, "sender_id": uid})
            return project_out(S, p, uid)
        if len(seg) == 2 and method == "DELETE":
            M.PROJECTS.remove(p)
            M.broadcast_all({"type": "project.deleted", "project_id": pid, "sender_id": uid})
            return {"status": "deleted"}
        if len(seg) == 3 and seg[2] in ("complete", "reopen") and method == "POST":
            if seg[2] == "complete":
                S.archived[pid] = (uid, dt.datetime.now().isoformat())
            else:
                S.archived.pop(pid, None)
            M.broadcast_all({"type": "project.updated", "project_id": pid, "sender_id": uid})
            return project_out(S, p, uid)
        if len(seg) == 3 and seg[2] == "files":
            if method == "GET":
                section = q.get("section") or ""
                rows = [f for f in S.files.values() if f["project_id"] == pid and f["section"] == section]
                return sorted(rows, key=lambda f: (f["created_at"], f["id"]))
            if method == "POST":
                fields, parts = L.parse_multipart(h.body_raw(), h.headers.get("Content-Type", ""))
                section = fields.get("section") or ""
                if section not in ("contracts", "project", "delivery", "extra", "photos"):
                    raise M.ApiError(400, "Неизвестный раздел")
                try:
                    items = L.resolve_items(json.loads(fields.get("meta") or "[]"), parts)
                except ValueError as e:
                    raise M.ApiError(400, str(e))
                created = [S.add_file(pid, section, it["name"], it["data"], it["thumb"], it["meta"], uid) for it in items]
                if created:
                    M.broadcast_all({"type": "project.file_added", "project_id": pid, "section": section,
                                     "file_id": created[0]["id"], "sender_id": uid})
                return created
        if len(seg) == 3 and seg[2] == "photos" and method == "GET":
            return photos(S, pid, uid)
        if len(seg) >= 3 and seg[2] == "warehouse":
            return warehouse_api(h, S, uid, pid, seg[3:], method)
        return SKIP
    if seg[0] == "projects" and len(seg) >= 3 and seg[1] == "files" and seg[2].isdigit():
        fid = int(seg[2])
        rec = S.files.get(fid)
        blob = L.BLOBS.get("projectfile/%d" % fid)
        if rec is None or blob is None:
            raise M.ApiError(404, "Файл не найден")
        if len(seg) == 3 and method == "DELETE":
            with S.lock:
                S.files.pop(fid, None)
                L.BLOBS.pop("projectfile/%d" % fid, None)
            M.broadcast_all({"type": "project.file_deleted", "project_id": rec["project_id"], "file_id": fid,
                             "sender_id": uid})
            return {"status": "deleted"}
        if len(seg) >= 4 and seg[3] == "thumb":
            if blob.get("thumb"):
                h._send(200, blob["thumb"], "image/jpeg", {"Cache-Control": "no-store"})
                return None
            if not L.is_image(rec["filename"]):
                raise M.ApiError(404, "Миниатюры нет")
        if len(seg) >= 4 and seg[3] in ("download", "view", "thumb", "stream"):
            h.send_blob(blob, attachment=seg[3] == "download")
            return None
        return SKIP

    # ── склад: общие маршруты ────────────────────────────────────────────────
    if path == "warehouse/issues/pending" and method == "GET":
        mine = [i for i in S.issues if i["status"] == "pending" and (M.is_management(uid) or uid in (i["issuer_id"], i["receiver_id"]))]
        out = []
        for i in mine:
            row = S.warehouse(i["project_id"])[i["row_no"] - 1]
            proj = next((x for x in M.PROJECTS if x["id"] == i["project_id"]), {})
            out.append({"id": i["id"], "project_id": i["project_id"], "project": proj.get("name") or "", "row_no": i["row_no"],
                        "name": row["name"], "unit": row["unit"], "quantity": i["quantity"],
                        "issuer": M.person(i["issuer_id"]), "receiver": M.person(i["receiver_id"]),
                        "created_at": i["created_at"], "mine": i["receiver_id"] == uid})
        return {"items": out}
    if seg[0] == "warehouse" and len(seg) == 4 and seg[1] == "issues" and seg[2].isdigit() and method == "POST":
        i = next((x for x in S.issues if x["id"] == int(seg[2])), None)
        if i is None:
            raise M.ApiError(404, "Выдача не найдена")
        if i["status"] != "pending":
            raise M.ApiError(400, "Эта выдача уже закрыта")
        if seg[3] == "confirm":
            if i["receiver_id"] != uid and not M.is_management(uid):
                raise M.ApiError(403, "Подтвердить получение может только получатель")
            i.update(status="confirmed", confirmed_at=dt.datetime.now().isoformat())
        elif seg[3] == "cancel":
            if uid not in (i["issuer_id"], i["receiver_id"]) and not M.is_management(uid):
                raise M.ApiError(403, "Отменить выдачу может тот, кто её начал, или получатель")
            i.update(status="cancelled")
        else:
            return SKIP
        issue_event(S, i, "confirmed" if seg[3] == "confirm" else "cancelled", uid)
        return {"id": i["id"], "status": i["status"]}
    if path == "warehouse/editors":
        if method == "GET":
            return {"user_ids": sorted(S.editors), "people": [M.person(u) for u in sorted(S.editors)],
                    "can_manage": M.is_management(uid)}
        if method == "PUT":
            if not M.is_management(uid):
                raise M.ApiError(403, "Список доверенных ведут директор и администратор")
            S.editors = {int(x) for x in (h.body_json() or {}).get("user_ids") or []}
            return {"user_ids": sorted(S.editors)}
    if path == "warehouse/units/full" and method == "GET":
        used = {}
        for rows in S.rows.values():
            for r in rows:
                if r["unit"]:
                    used[r["unit"]] = used.get(r["unit"], 0) + 1
        return {"units": [dict(u, used=used.get(u["name"], 0)) for u in M.UNITS], "can_manage": M.is_management(uid)}
    if path == "warehouse/units" and method == "POST":
        data = h.body_json() or {}
        name = str(data.get("name") or "").strip()[:32]
        if not name:
            raise M.ApiError(400, "Укажите сокращение")
        if not any(u["name"] == name for u in M.UNITS):
            M.UNITS.append({"id": S.next("unit"), "name": name, "full_name": str(data.get("full_name") or "")})
        return (201, {"name": name, "units": M.UNITS})
    if seg[0] == "warehouse" and len(seg) == 3 and seg[1] == "units" and seg[2].isdigit():
        u = next((x for x in M.UNITS if x["id"] == int(seg[2])), None)
        if u is None:
            raise M.ApiError(404, "Единица не найдена")
        if method == "PATCH":
            data = h.body_json() or {}
            u.update(name=str(data.get("name") or u["name"]), full_name=str(data.get("full_name") or ""))
            return dict(u, moved=0)
        if method == "DELETE":
            M.UNITS.remove(u)
            return {"deleted": u["id"]}
    return SKIP



# ── мессенджер: то, чего нет у общего тестового сервера ─────────────────────
# Как server_app/routers/chat.py: личный чат по сотруднику, роли в группе,
# переименование и картинка, участники, выход, вложения чата, поиск и пересылка.

def _chat_row(M, uid, other, cid):
    e = M.EMP[other]
    return {"id": cid, "name": "%s %s" % (e["last_name"], e["first_name"]), "last_name": e["last_name"],
            "first_name": e["first_name"], "is_group": False, "other_user_id": other, "image_path": None,
            "avatar_url": None, "has_messages": False, "sort_datetime": M.moment(0, 7), "time_source": "created",
            "role": "member", "message_count": 0, "created_at": M.moment(0, 7), "unread_count": 0,
            "last_message": "", "last_message_sender": "", "last_message_time": "", "timestamp": 0}


def chat_list(S, uid):
    M = S.M
    rows = M.chats_for(uid)
    have = set(r["id"] for r in rows)
    for a, b in sorted(S.private):
        if uid in (a, b):
            other = b if uid == a else a
            cid = M.private_chat_id(a, b)
            if cid not in have:
                rows.append(M._last(cid, _chat_row(M, uid, other, cid), "", "", 0, ""))
                have.add(cid)
    for r in rows:
        if r.get("is_group") and r["id"] in S.group_images:
            r["image_path"] = S.group_images[r["id"]]
        if r.get("is_group") and r["id"] in M.GROUPS:
            r["name"] = M.GROUPS[r["id"]]["name"]
    return rows


def chat_members(S, cid):
    M = S.M
    members = M.chat_members(cid)
    if not members:
        for a, b in S.private:
            if M.private_chat_id(a, b) == cid:
                return {a, b}
    return members


def group_role(S, cid, uid):
    M = S.M
    if (cid, uid) in S.roles:
        return S.roles[(cid, uid)]
    if cid in M.GROUPS and M.GROUPS[cid]["owner"] == uid:
        return "owner"
    if cid == 900 and uid == 1:
        return "owner"
    return "member"


def _push_lists(S, users):
    M = S.M
    for u, sock in M._chat_sockets(users):
        try:
            sock.send_text(json.dumps({"type": "chats_updated", "chats": chat_list(S, u)}, ensure_ascii=False))
        except Exception:
            pass


def _media_category(f):
    name = str(f.get("file_name") or f.get("filename") or "").lower()
    if re.search(r"\.(png|jpe?g|gif|bmp|webp|heic|heif)$", name):
        return "photo"
    if re.search(r"\.(mp4|mov|avi|mkv|webm|m4v|3gp)$", name):
        return "video"
    return "file"


def chat_api(h, S, uid, seg, method, q):  # noqa: C901 — одна таблица маршрутов
    M, L = S.M, S.L
    path = "/".join(seg)
    if path == "chat/list" and method == "GET":
        return chat_list(S, uid)
    if path == "chat/private" and method == "POST":
        target = int((h.body_json() or {}).get("target_user_id") or 0)
        if target not in M.EMP or target == uid:
            raise M.ApiError(400, "Нельзя создать чат с этим сотрудником")
        cid = M.private_chat_id(uid, target)
        S.private.add(tuple(sorted((uid, target))))
        _push_lists(S, [uid, target])
        e = M.EMP[target]
        return {"id": cid, "name": "%s %s" % (e["last_name"], e["first_name"]), "is_group": False}
    if len(seg) < 3:
        return SKIP
    if seg[1] in ("info", "group") and len(seg) == 3 and seg[2].isdigit() and method == "GET":
        cid = int(seg[2])
        members = chat_members(S, cid)
        if uid not in members:
            raise M.ApiError(404, "Чат или группа не найдена")
        group = cid == 900 or cid in M.GROUPS
        if not group:
            if seg[1] == "group":
                raise M.ApiError(404, "Группа не найдена")
            other = next((m for m in members if m != uid), uid)
            e = M.EMP[other]
            return {"chat_id": cid, "other_user_id": other, "user_id": other, "has_chat": True, "is_group": False,
                    "full_name": e["name"], "last_name": e["last_name"], "first_name": e["first_name"],
                    "phone_number": e["phone_number"], "email": e["email"], "position": e["position"],
                    "department": e["department"], "avatar_url": None}
        people = [{"id": m, "name": M.EMP[m]["name"], "full_name": M.EMP[m]["name"],
                   "last_name": M.EMP[m]["last_name"], "first_name": M.EMP[m]["first_name"], "avatar_url": None,
                   "role": group_role(S, cid, m)} for m in sorted(members)]
        name = "Монтаж «Северный»" if cid == 900 else M.GROUPS[cid]["name"]
        return {"chat_id": cid, "name": name, "image_path": S.group_images.get(cid), "my_role": group_role(S, cid, uid),
                "participants": people, "type": "group", "participant_count": len(people), "is_group": True,
                "status": "success"}
    if seg[1] == "group" and seg[2].isdigit():
        cid = int(seg[2])
        group = M.GROUPS.get(cid)
        members = chat_members(S, cid)
        if uid not in members:
            raise M.ApiError(404, "Группа не найдена")
        role = group_role(S, cid, uid)
        manage = role in ("owner", "admin")
        if len(seg) == 3 and method == "PUT":
            if not manage:
                raise M.ApiError(403, "Недостаточно прав")
            data = h.body_json() or {}
            if data.get("name") and group is not None:
                group["name"] = str(data["name"]).strip()
            if data.get("image_path"):
                S.group_images[cid] = str(data["image_path"]).replace("/file_storage/", "", 1)
            M.broadcast_chat(cid, {"type": "group_updated", "chat_id": cid})
            _push_lists(S, members)
            return {"status": "ok"}
        if len(seg) == 4 and seg[3] in ("promote", "demote") and method == "POST":
            if role != "owner":
                raise M.ApiError(403, "Права администратора раздаёт только владелец")
            data = h.body_json() or {}
            target = int(data.get("user_id") or 0)
            if "role" not in data:
                raise M.ApiError(422, "Не указана роль")
            S.roles[(cid, target)] = "admin" if seg[3] == "promote" else "member"
            M.broadcast_chat(cid, {"type": "group_role_changed", "chat_id": cid, "user_id": target})
            return {"status": "ok"}
        if len(seg) == 5 and seg[3] == "participants" and method == "DELETE":
            if not manage:
                raise M.ApiError(403, "Недостаточно прав")
            target = int(seg[4])
            if group is not None:
                group["members"].discard(target)
            M.broadcast_chat(cid, {"type": "member_remove", "chat_id": cid, "user_id": target})
            for u, sock in M._chat_sockets([target]):
                try:
                    sock.send_text(json.dumps({"type": "removed_from_chat", "chat_id": cid}, ensure_ascii=False))
                except Exception:
                    pass
            _push_lists(S, members)
            return {"status": "ok"}
        if len(seg) == 4 and seg[3] == "leave" and method == "DELETE":
            if group is None:
                raise M.ApiError(403, "Из этого чата выйти нельзя")
            group["members"].discard(uid)
            if group["owner"] == uid and group["members"]:
                group["owner"] = sorted(group["members"])[0]
            if not group["members"]:
                M.GROUPS.pop(cid, None)
            _push_lists(S, members)
            return {"status": "ok", "group_deleted": cid not in M.GROUPS}
        if len(seg) == 4 and seg[3] == "participants" and method == "POST":
            if not manage:
                raise M.ApiError(403, "Недостаточно прав")
            ids = [int(u) for u in (h.body_json() or {}).get("user_ids", [])]
            if group is not None:
                group["members"].update(ids)
            M.broadcast_chat(cid, {"type": "member_add", "chat_id": cid})
            _push_lists(S, chat_members(S, cid))
            return {"status": "ok", "members": sorted(chat_members(S, cid))}
        if len(seg) == 3 and method == "DELETE":
            if role != "owner":
                raise M.ApiError(403, "Удалить группу может только владелец")
            M.GROUPS.pop(cid, None)
            M.broadcast_chat(cid, {"type": "group_deleted", "chat_id": cid})
            _push_lists(S, members)
            return {"status": "deleted"}
        return SKIP
    if path == "chat/upload" and method == "POST":
        fields, files = L.parse_multipart(h.body_raw(), h.headers.get("Content-Type", ""))
        part = (files.get("file") or [None])[0]
        if part is None:
            raise M.ApiError(400, "Нет файла")
        name = part["filename"] or "image.jpg"
        p = "messenger/avatars/%s_%s" % (S.next("pfile"), name)
        L.store_blob(p, part["data"], name)
        return {"url": "/file_storage/" + p, "file_path": p, "filename": name}
    if seg[1].isdigit() and len(seg) >= 3:
        cid = int(seg[1])
        if uid not in chat_members(S, cid):
            raise M.ApiError(403, "Вы не участник этого чата")
        L.seed(cid, chat_members(S, cid), M.EMP)
        with L.LOCK:
            msgs = list(L.MESSAGES.get(cid) or [])
        if seg[2] == "media" and len(seg) == 4 and seg[3] == "summary":
            counts = {"photo": 0, "video": 0, "file": 0}
            for m in msgs:
                for f in m.get("files") or []:
                    if m.get("special") != "file_group":
                        counts[_media_category(f)] += 1
            return {"chat_id": cid, "counts": counts}
        if seg[2] == "media" and len(seg) == 3:
            want = q.get("category") or "photo"
            items = []
            for m in reversed(msgs):
                for f in m.get("files") or []:
                    if m.get("special") == "file_group" or _media_category(f) != want:
                        continue
                    items.append({"message_id": m["id"], "group_id": m.get("group_id"), "chat_id": cid,
                                  "filename": f.get("file_name") or f.get("filename"), "url": f.get("url"),
                                  "file_path": f.get("file_path"), "size": f.get("size"), "timestamp": m["timestamp"]})
            limit = int(q.get("limit") or 200)
            return {"chat_id": cid, "category": want, "total": len(items), "items": items[:limit]}
        if seg[2] == "search":
            needle = str(q.get("query") or "").lower()
            out = [dict(m) for m in reversed(msgs) if needle and needle in str(m.get("text") or "").lower()
                   and not m.get("special")]
            return out[:int(q.get("limit") or 50)]
    if seg[1] == "message" and len(seg) == 4 and seg[3] == "forward" and method == "POST":
        src_chat, m = L.find_message(int(seg[2])) if seg[2].isdigit() else (None, None)
        if m is None or uid not in chat_members(S, src_chat):
            raise M.ApiError(404, "Сообщение не найдено")
        data = h.body_json() or {}
        count = 0
        for target in data.get("chat_ids") or []:
            target = int(target)
            members = chat_members(S, target)
            if uid not in members:
                continue
            L.seed(target, members, M.EMP)
            copy = L.add_text(target, uid, M.EMP, m.get("text"))
            origin = M.EMP.get(m["sender_id"]) or {}
            copy["metadata"] = {"forwarded_from": {"user_id": m["sender_id"], "name": origin.get("name"),
                                                   "last_name": origin.get("last_name"),
                                                   "first_name": origin.get("first_name")}}
            if m.get("files"):
                copy["files"] = [dict(f) for f in m["files"]]
                copy.update(file_path=m.get("file_path"), file_name=m.get("file_name"), file_size=m.get("file_size"),
                            url=m.get("url"))
            M.broadcast_chat(target, dict(copy, type="message", reply_to_message=None))
            _push_lists(S, members)
            count += 1
        return {"status": "ok", "count": count}
    return SKIP


def organizations_api(h, S, uid, rest, method):
    """Запись справочника организаций (как routers/organizations.py)."""
    M = S.M
    if method == "GET":
        return SKIP
    allowed = M.is_management(uid) or M.is_head(uid) or M.dept(uid) == "Договорной отдел"
    if not allowed:
        raise M.ApiError(403, "Справочник организаций правят руководство и Договорной отдел")
    now = dt.datetime.now().isoformat()
    who = M.EMP[uid]["name"]

    def fill(o, data):
        o["name"] = str(data.get("name") or "").strip()
        o["description"] = data.get("description") or ""
        o["phones"] = [{"id": k + 1, "phone_number": p.get("phone_number"), "description": p.get("description") or "",
                        "organization_id": o["id"]} for k, p in enumerate(data.get("phones") or [])]
        o["emails"] = [{"id": k + 1, "email": m.get("email"), "description": m.get("description") or "",
                        "organization_id": o["id"]} for k, m in enumerate(data.get("emails") or [])]
    if not rest and method == "POST":
        data = h.body_json() or {}
        if not str(data.get("name") or "").strip():
            raise M.ApiError(400, "Укажите название организации")
        o = {"id": max([x["id"] for x in M.ORGANIZATIONS] + [0]) + 1, "who_create": who, "when_create": now,
             "who_edit": None, "when_edit": None}
        fill(o, data)
        M.ORGANIZATIONS.append(o)
        M.broadcast_all({"type": "organization.created", "organization_id": o["id"]})
        return (201, o)
    if len(rest) == 1 and rest[0].isdigit():
        o = next((x for x in M.ORGANIZATIONS if x["id"] == int(rest[0])), None)
        if o is None:
            raise M.ApiError(404, "Организация не найдена")
        if method == "PUT":
            fill(o, h.body_json() or {})
            o.update(who_edit=who, when_edit=now)
            M.broadcast_all({"type": "organization.updated", "organization_id": o["id"]})
            return o
        if method == "DELETE":
            M.ORGANIZATIONS.remove(o)
            M.broadcast_all({"type": "organization.deleted", "organization_id": o["id"]})
            return {"status": "deleted"}
    return SKIP


def employees_api(h, S, uid, rest, method):  # noqa: C901 — запись карточек и фото сотрудников
    M, L = S.M, S.L
    if rest == ["photo", "ids"] and method == "GET":
        return sorted(S.emp_photos)
    if not rest and method == "POST":
        if not M.is_management(uid):
            raise M.ApiError(403, "Добавлять сотрудников может директор или администратор")
        data = h.body_json() or {}
        if not str(data.get("last_name") or "").strip() or not str(data.get("first_name") or "").strip():
            raise M.ApiError(422, "Фамилия и имя обязательны")
        new_id = max(M.EMP) + 1
        M.EMP[new_id] = {"id": new_id, "name": "%s %s" % (data["last_name"], data["first_name"]), "last_name": data["last_name"],
                         "first_name": data["first_name"], "middle_name": data.get("middle_name") or "",
                         "position": data.get("position") or "", "status": data.get("status") or "работник",
                         "phone_number": data.get("phone_number") or "", "email": data.get("email") or "",
                         "profile": data.get("profile") or "", "department": data.get("department") or ""}
        M.ROLE_USER[str(data.get("username") or "user%d" % new_id)] = new_id
        M.broadcast_all({"type": "employee.created", "employee_id": new_id})
        return (201, M.EMP[new_id])
    if rest and rest[0].isdigit():
        eid = int(rest[0])
        e = M.EMP.get(eid)
        if e is None:
            raise M.ApiError(404, "Сотрудник не найден")
        if len(rest) == 1 and method == "PUT":
            data = h.body_json() or {}
            own = eid == uid
            allowed = set(data) if M.is_management(uid) else ({"phone_number", "email"} if own else set())
            if not allowed:
                raise M.ApiError(403, "Менять карточку может директор или администратор")
            for k in allowed & {"last_name", "first_name", "middle_name", "position", "department", "status", "phone_number",
                                "email", "profile"}:
                e[k] = data[k] if data[k] is not None else ""
            e["name"] = "%s %s" % (e["last_name"], e["first_name"])
            M.broadcast_all({"type": "employee.updated", "employee_id": eid})
            return e
        if len(rest) == 1 and method == "DELETE":
            if not M.is_management(uid) or eid == uid:
                raise M.ApiError(403, "Удалять сотрудников может директор или администратор")
            M.EMP.pop(eid, None)
            M.broadcast_all({"type": "employee.deleted", "employee_id": eid})
            return {"status": "deleted"}
        if rest[1:] == ["photo"]:
            if method == "GET":
                data = S.emp_photos.get(eid)
                if data is None:
                    raise M.ApiError(404, "Фото нет")
                h._send(200, data, "image/jpeg", {"Cache-Control": "no-store"})
                return None
            if method == "POST":
                if eid != uid and not M.is_management(uid):
                    raise M.ApiError(403, "Фото меняет сам сотрудник или руководство")
                _fields, parts = L.parse_multipart(h.body_raw(), h.headers.get("Content-Type", ""))
                part = (parts.get("file") or [None])[0]
                if not part or not part["data"]:
                    raise M.ApiError(400, "Файл пустой")
                S.emp_photos[eid] = part["data"]
                M.broadcast_all({"type": "employee.updated", "employee_id": eid})
                return {"status": "ok"}
    return SKIP


EQ_TITLES = {"issued": "выдал", "confirmed": "принял", "declined": "отказался принимать", "cancelled": "отменил выдачу",
             "return_requested": "вернул", "returned": "принял возврат", "return_declined": "отклонил возврат"}
EQ_FIELDS = {"name": "Оборудование", "quantity": "Количество", "unit": "Единица"}


def equipment_api(h, S, uid, rest, method):  # noqa: C901 — как server_app/routers/equipment.py
    M, L = S.M, S.L
    items = S.equipment()
    acc = M.equipment_access(uid)

    def goods():
        if not acc["can_edit_goods"]:
            raise M.ApiError(403, "«Оборудование» и «Количество» ведёт бухгалтерия, директор и администратор")

    def stock():
        if not acc["can_issue"]:
            raise M.ApiError(403, "Выдавать и принимать оборудование может отдел склада, директор и администратор")

    def row_event(item, **extra):
        M.broadcast_all(dict({"type": "equipment.row", "actor_id": uid, "item_id": item["id"], "row": S.eq_out(item, uid)},
                             **extra))

    if not rest and method == "GET":
        return {"rows": [S.eq_out(i, uid) for i in sorted(items.values(), key=lambda x: x["id"])], "units": M.UNITS,
                "access": acc}
    if not rest and method == "POST":
        goods()
        data = h.body_json() or {}
        item = {"id": S.next("eq"), "name": str(data.get("name") or "").strip(), "quantity": _num(data.get("quantity")),
                "unit": str(data.get("unit") or "").strip(), "updated_at": dt.datetime.now().isoformat(), "updated_by": uid}
        items[item["id"]] = item
        S.eq_write(item["id"], "cell", uid, action="created", field="name", old_value=None, new_value=item["name"])
        row_event(item, added=True)
        return (201, S.eq_out(item, uid))
    if rest == ["issues", "pending"] and method == "GET":
        def out(i, kind):
            it = items.get(i["item_id"]) or {}
            return {"id": i["id"], "kind": kind, "item_id": i["item_id"], "name": it.get("name") or "позиция",
                    "unit": it.get("unit") or "", "quantity": i["quantity"], "issuer": M.person(i["issuer_id"]),
                    "receiver": M.person(i["receiver_id"])}
        mine = [out(i, "receive") for i in S.eq_issues if i["status"] == "pending" and i["receiver_id"] == uid]
        back = [out(i, "return") for i in S.eq_issues if i["status"] == "return_pending"] if acc["can_accept_return"] else []
        sent = [out(i, "sent") for i in S.eq_issues if i["status"] == "pending" and i["issuer_id"] == uid and i["receiver_id"] != uid]
        return {"items": mine + back + sent}
    if len(rest) >= 2 and rest[0] == "issues" and rest[1].isdigit() and method == "POST":
        i = next((x for x in S.eq_issues if x["id"] == int(rest[1])), None)
        if i is None:
            raise M.ApiError(404, "Выдача не найдена")
        act = "/".join(rest[2:])
        item = items.get(i["item_id"])
        if act == "confirm":
            if i["receiver_id"] != uid:
                raise M.ApiError(403, "Подтвердить получение может только получатель")
            if i["status"] != "pending":
                raise M.ApiError(409, "Эта выдача уже обработана")
            i.update(status="issued", confirmed_at=dt.datetime.now().isoformat())
            log = "confirmed"
        elif act == "decline":
            if uid not in (i["receiver_id"], i["issuer_id"]) and not acc["is_management"]:
                raise M.ApiError(403, "Отменить выдачу может получатель или тот, кто выдал")
            if i["status"] != "pending":
                raise M.ApiError(409, "Эта выдача уже обработана")
            log = "declined" if i["receiver_id"] == uid else "cancelled"
            i.update(status="cancelled")
        elif act == "return":
            if i["receiver_id"] != uid:
                raise M.ApiError(403, "Вернуть может тот, за кем числится оборудование")
            if i["status"] != "issued":
                raise M.ApiError(409, "Это оборудование сейчас нельзя вернуть")
            i.update(status="return_pending", return_requested_at=dt.datetime.now().isoformat())
            log = "return_requested"
        elif act in ("return/accept", "return/decline"):
            stock()
            if i["status"] != "return_pending":
                raise M.ApiError(409, "Возврат по этой записи не запрашивали")
            if act == "return/accept":
                i.update(status="returned", returned_to=uid)
                log = "returned"
            else:
                i.update(status="issued", return_requested_at=None)
                log = "return_declined"
        else:
            return SKIP
        S.eq_write(i["item_id"], "issue", uid, action=log, target_id=i["receiver_id"], new_value=num_str(i["quantity"]))
        if item:
            row_event(item)
        return {"ok": True, "status": i["status"]}
    if rest and rest[0].isdigit():
        item = items.get(int(rest[0]))
        if item is None:
            raise M.ApiError(404, "Позиция не найдена")
        sub = rest[1:]
        if not sub and method == "PATCH":
            goods()
            data = h.body_json() or {}
            fields = set(data.get("fields") or [])
            if not fields:
                raise M.ApiError(400, "Не указано, какие поля меняются")
            issued = sum(i["quantity"] for i in S.eq_issues if i["item_id"] == item["id"] and i["status"] in ("issued", "return_pending"))
            changed = []
            for f in sorted(fields):
                new = data.get(f)
                if f == "quantity":
                    new = _num(new)
                    if new is not None and new < issued:
                        raise M.ApiError(409, "Сейчас на руках %s — количество нельзя сделать меньше" % num_str(issued))
                else:
                    new = str(new or "").strip()
                if (item[f] if item[f] not in ("", None) else None) == (new if new not in ("", None) else None):
                    continue
                S.eq_write(item["id"], "cell", uid, action="changed", field=f, old_value=None if item[f] is None else str(item[f]),
                           new_value=None if new is None else str(new))
                item[f] = new
                changed.append(f)
            if changed:
                item.update(updated_at=dt.datetime.now().isoformat(), updated_by=uid)
                row_event(item, fields=changed)
            return {"row": S.eq_out(item, uid), "changed": changed}
        if not sub and method == "DELETE":
            goods()
            if any(i["item_id"] == item["id"] and i["status"] in ("pending", "issued", "return_pending") for i in S.eq_issues):
                raise M.ApiError(409, "Позиция на руках или ждёт подтверждения — сначала верните её")
            items.pop(item["id"], None)
            M.broadcast_all({"type": "equipment.removed", "actor_id": uid, "item_id": item["id"]})
            return {"ok": True}
        if sub == ["photo"]:
            if method == "GET":
                data = S.eq_photos.get(item["id"])
                if data is None:
                    raise M.ApiError(404, "Фотографии нет")
                h._send(200, data, "image/jpeg", {"Cache-Control": "no-store"})
                return None
            goods()
            if method == "POST":
                _fields, parts = L.parse_multipart(h.body_raw(), h.headers.get("Content-Type", ""))
                part = (parts.get("file") or [None])[0]
                if not part or not part["data"]:
                    raise M.ApiError(400, "Файл пустой")
                S.eq_photos[item["id"]] = part["data"]
            elif method == "DELETE":
                S.eq_photos.pop(item["id"], None)
            item.update(updated_at=dt.datetime.now().isoformat(), updated_by=uid)
            row_event(item, fields=["photo"])
            return S.eq_out(item, uid)
        if sub == ["issue"] and method == "POST":
            stock()
            data = h.body_json() or {}
            qty = float(data.get("quantity") or 0)
            if not (item["name"] or "").strip() or item["quantity"] is None:
                raise M.ApiError(409, "Сначала заполните оборудование и количество")
            if qty <= 0:
                raise M.ApiError(400, "Количество выдачи должно быть больше нуля")
            rid = int(data.get("receiver_id") or 0)
            if rid not in M.EMP:
                raise M.ApiError(404, "Получатель не найден")
            busy = sum(i["quantity"] for i in S.eq_issues if i["item_id"] == item["id"] and i["status"] in ("pending", "issued", "return_pending"))
            if qty > item["quantity"] - busy + 1e-9:
                raise M.ApiError(409, "Свободно только %s" % num_str(item["quantity"] - busy))
            i = {"id": S.next("eqi"), "item_id": item["id"], "quantity": qty, "status": "pending", "issuer_id": uid,
                 "receiver_id": rid, "created_at": dt.datetime.now().isoformat(), "confirmed_at": None,
                 "return_requested_at": None, "returned_to": None}
            S.eq_issues.append(i)
            S.eq_write(item["id"], "issue", uid, action="issued", target_id=rid, new_value=num_str(qty))
            row_event(item)
            return (201, {"id": i["id"], "status": i["status"]})
        if sub == ["history"] and method == "GET":
            rows = sorted([r for r in S.eq_log if r["item_id"] == item["id"]], key=lambda r: r["id"], reverse=True)

            def out(r):
                return {"id": r["id"], "kind": r["kind"], "action": r.get("action"), "field": r.get("field"),
                        "field_title": EQ_FIELDS.get(r.get("field") or "", r.get("field") or ""),
                        "action_title": EQ_TITLES.get(r.get("action") or "", r.get("action") or ""),
                        "old_value": r.get("old_value"), "new_value": r.get("new_value"), "user": M.person(r["user_id"]),
                        "target": M.person(r.get("target_id")) if r.get("target_id") else None, "created_at": r["created_at"]}
            return {"issues": [out(r) for r in rows if r["kind"] == "issue"],
                    "cells": [out(r) for r in rows if r["kind"] != "issue"]}
    return SKIP


def num_str(v):
    return "%g" % float(v or 0)


def project_out(S, p, uid):
    out = S.M.project_out(p, uid)
    arch = S.archived.get(p["id"])
    files = sum(1 for f in S.files.values() if f["project_id"] == p["id"])
    tasks = [t for t in S.M.TASKS if t["project_id"] == p["id"]]
    out.update(files_total=files, is_archived=bool(arch), archived_at=arch[1] if arch else None,
               archived_by=S.M.uploader(arch[0]) if arch else None, can_complete=out.get("can_edit"),
               tasks_unfinished=0 if arch else len(tasks))
    if arch:
        out.update(can_upload=False)
    return out


def photos(S, pid, uid):
    M, L = S.M, S.L
    folders = []
    for t in sorted([t for t in M.TASKS if t["project_id"] == pid], key=lambda t: t["deadline"] or ""):
        if not M.can_see_task(uid, t):
            continue
        files = []
        with L.LOCK:
            recs = [dict(r) for r in L.TASK_FILES.values() if r["task_id"] == t["id"]]
        for r in recs:
            name = r["filename"]
            if not (L.is_image(name) or L.ext_of(name) in ("mp4", "mov", "webm", "avi", "mkv")):
                continue
            sid = r.get("stage_detail_id")
            files.append({"id": r["id"], "filename": name, "size": r["size"], "extension": "." + L.ext_of(name),
                          "created_at": r["created_at"], "is_stage_file": bool(sid),
                          "stage_number": (sid % 10) if sid else None, "uploaded_by": r.get("uploaded_by")})
        files.sort(key=lambda x: (x["created_at"], x["id"]))
        folders.append({"task_id": t["id"], "task_name": t["name"], "deadline": t["deadline"], "activity": "Активная",
                        "files": files})
    return folders


def issue_event(S, i, action, uid):
    row = S.warehouse(i["project_id"])[i["row_no"] - 1]
    S.M.broadcast_all({"type": "warehouse.issue", "action": action, "issue_id": i["id"], "project_id": i["project_id"],
                       "row_no": i["row_no"], "name": row["name"], "quantity": i["quantity"],
                       "issuer_id": i["issuer_id"], "receiver_id": i["receiver_id"], "actor_id": uid})


def warehouse_api(h, S, uid, pid, rest, method):  # noqa: C901
    M = S.M
    acc = S.access(uid, pid)
    if not acc["can_view"]:
        raise M.ApiError(403, "Склад доступен бухгалтерии, монтажному отделу, отделу склада, директору и администратору")
    rows = S.warehouse(pid)
    if not rest and method == "GET":
        pending = []
        for i in S.issues:
            if i["project_id"] == pid and i["status"] == "pending":
                pending.append({"id": i["id"], "row_no": i["row_no"], "item_id": pid * 1000 + i["row_no"],
                                "quantity": i["quantity"], "issuer": M.person(i["issuer_id"]),
                                "receiver": M.person(i["receiver_id"]), "created_at": i["created_at"],
                                "mine": i["receiver_id"] == uid})
        return {"rows": [S.row_out(pid, r) for r in rows], "units": M.UNITS, "access": acc, "pending": pending}
    if rest == ["bulk"] and method == "POST":
        if not acc["can_edit_goods"]:
            raise M.ApiError(403, "«Товар» и «Количество» правят директор, администратор и назначенные ими сотрудники")
        data = h.body_json() or {}
        start = int(data.get("start_row") or 1)
        incoming = data.get("rows") or []
        if not incoming:
            raise M.ApiError(400, "Вставлять нечего")
        if start < 1 or start + len(incoming) - 1 > ROWS:
            raise M.ApiError(400, "Не помещается: со строки %d свободно %d строк, а вставляется %d" % (
                start, ROWS - start + 1, len(incoming)))
        touched = []
        for k, inc in enumerate(incoming):
            r = rows[start + k - 1]
            for f in ("name", "quantity", "unit"):
                if inc.get(f) is None:
                    continue
                new = _num(inc[f]) if f == "quantity" else str(inc[f]).strip()
                if f == "unit" and new and not any(u["name"] == new for u in M.UNITS):
                    M.UNITS.append({"id": S.next("unit"), "name": new, "full_name": ""})
                if (r[f] or None) != (new or None):
                    S.logs.append({"project_id": pid, "row_no": r["row_no"], "field": f, "old": r[f], "new": new,
                                   "user_id": uid, "at": dt.datetime.now().isoformat()})
                    r[f] = new
            r["updated_at"] = dt.datetime.now().isoformat()
            touched.append(r)
        M.broadcast_all({"type": "warehouse.updated", "what": "bulk", "project_id": pid, "actor_id": uid,
                         "count": len(touched)})
        return {"rows": [S.row_out(pid, r) for r in touched], "count": len(touched)}
    if len(rest) >= 1 and rest[0].isdigit():
        n = int(rest[0])
        if n < 1 or n > ROWS:
            raise M.ApiError(400, "Строки с таким номером нет")
        r = rows[n - 1]
        if len(rest) == 1 and method == "PATCH":
            data = h.body_json() or {}
            fields = set(data.get("fields") or [])
            if not fields:
                raise M.ApiError(400, "Не указано, какие поля меняются")
            if fields & {"name", "quantity", "unit"} and not acc["can_edit_goods"]:
                raise M.ApiError(403, "«Товар» и «Количество» правят директор, администратор и назначенные ими сотрудники")
            if fields & {"stock_state", "stock_qty"} and not acc["can_issue"]:
                raise M.ApiError(403, "«Количество на складе» ведёт отдел склада")
            changed = []
            for f in fields:
                new = data.get(f)
                if f in ("quantity", "stock_qty"):
                    new = _num(new)
                    if new is not None and new < 0:
                        raise M.ApiError(400, "Количество не может быть отрицательным")
                elif f in ("name", "unit"):
                    new = str(new or "").strip()
                if (r[f] or None) == (new or None):
                    continue
                S.logs.append({"project_id": pid, "row_no": n, "field": f, "old": r[f], "new": new, "user_id": uid,
                               "at": dt.datetime.now().isoformat()})
                r[f] = new
                changed.append(f)
            if changed:
                r["updated_at"] = dt.datetime.now().isoformat()
                M.broadcast_all({"type": "warehouse.updated", "what": "cell", "project_id": pid, "actor_id": uid,
                                 "row_no": n, "fields": changed})
            return {"row": S.row_out(pid, r), "changed": changed}
        if len(rest) == 2 and rest[1] == "issue" and method == "POST":
            if not acc["can_issue"]:
                raise M.ApiError(403, "Выдавать товар может отдел склада")
            if not (r["name"] or "").strip():
                raise M.ApiError(400, "В этой строке ещё нет товара")
            data = h.body_json() or {}
            qty = float(data.get("quantity") or 0)
            if qty <= 0:
                raise M.ApiError(400, "Количество выдачи должно быть больше нуля")
            rid = int(data.get("receiver_id") or 0)
            if rid not in M.EMP:
                raise M.ApiError(404, "Получатель не найден")
            if rid == uid:
                raise M.ApiError(400, "Выдачу подтверждает получатель, себе выдать нельзя")
            issued, pending = S.stats(pid, n)
            if r["quantity"] is not None and issued + pending + qty > r["quantity"] + 1e-6:
                raise M.ApiError(400, "Осталось выдать %g — больше нельзя" % round(r["quantity"] - issued - pending, 3))
            i = {"id": S.next("issue"), "project_id": pid, "row_no": n, "quantity": qty, "status": "pending",
                 "issuer_id": uid, "receiver_id": rid, "created_at": dt.datetime.now().isoformat(), "confirmed_at": None}
            S.issues.append(i)
            issue_event(S, i, "created", uid)
            return (201, {"id": i["id"], "status": i["status"]})
        if len(rest) == 2 and rest[1] == "history" and method == "GET":
            titles = {"name": "Товар", "quantity": "Количество", "unit": "Единица", "stock_state": "Количество на складе",
                      "stock_qty": "Количество на складе"}
            edits = [{"field": l["field"], "field_title": titles.get(l["field"], l["field"]), "old": l["old"], "new": l["new"],
                      "who": M.person(l["user_id"]), "at": l["at"]}
                     for l in reversed(S.logs) if l["project_id"] == pid and l["row_no"] == n]
            issues = [{"id": i["id"], "quantity": i["quantity"], "status": i["status"], "issuer": M.person(i["issuer_id"]),
                       "receiver": M.person(i["receiver_id"]), "at": i["created_at"], "confirmed_at": i.get("confirmed_at"),
                       "cancelled_at": None}
                      for i in reversed(S.issues) if i["project_id"] == pid and i["row_no"] == n]
            return {"edits": edits, "issues": issues}
    return SKIP


def _native_stub():
    """Заглушка server_app.native_push и его роутера — если кода сервера рядом нет."""
    try:
        import server.server_app.native_push  # noqa: F401
        import server.server_app.routers.native_push  # noqa: F401
        return
    except Exception:
        pass
    import hashlib
    import secrets
    import types
    for name in ("server", "server.server_app", "server.server_app.routers"):
        sys.modules.setdefault(name, types.ModuleType(name))
    np = types.ModuleType("server.server_app.native_push")
    np.WS_PATH, np.PING_SECONDS, np.PROTOCOL_VERSION = "/ws/native", 25, 1
    np.DEVICE_ID_RE = re.compile(r"[A-Za-z0-9_-]{8,128}")
    np.new_secret = np.new_payload_key = lambda: secrets.token_urlsafe(24)
    np.hash_secret = lambda s: hashlib.sha256(str(s).encode()).hexdigest()
    np.secret_matches = lambda h, s: h == np.hash_secret(s)

    class _Hub:
        async def forget(self, device_id):
            return None
    np.HUB = _Hub()

    async def notify(user_ids, payload, ttl, loader=None):
        return {"sent": 0}
    np.notify = notify
    router = types.ModuleType("server.server_app.routers.native_push")

    async def serve_socket(adapter, hub, auth):
        return None
    router.serve_socket = serve_socket
    sys.modules["server.server_app.native_push"] = np
    sys.modules["server.server_app.routers.native_push"] = router
    sys.modules["server.server_app"].native_push = np
    sys.modules["server.server_app.routers"].native_push = router


def build(mock_dir=None):
    """Класс обработчика запросов и состояние: общий сервер + дополнения ПК."""
    d = find_mock_dir(mock_dir)
    if d not in sys.path:
        sys.path.insert(0, d)
    _native_stub()
    import live_store as L
    import mock_server as M
    S = State(M, L)

    class PcHandler(M.Handler):
        def api(self, method, path, q):
            res = pc_api(self, S, method, path, q)
            if res is SKIP:
                return super().api(method, path, q)
            return res

    return PcHandler, S


def make_server(host="127.0.0.1", port=0, mock_dir=None):
    handler, state = build(mock_dir)
    srv = ThreadingHTTPServer((host, port), handler)
    srv.daemon_threads = True
    srv.verbose = False
    srv.state = state
    return srv


def start(mock_dir=None):
    """Сервер в фоне этого же процесса: (сервер, адрес)."""
    srv = make_server("127.0.0.1", 0, mock_dir)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--mock-dir", default=None)
    a = ap.parse_args()
    srv = make_server(a.host, a.port, a.mock_dir)
    import mock_server as M
    print("Тестовый сервер ПК-версии: http://%s:%d  (логины: %s; пароль: %s)" % (
        a.host, a.port, ", ".join(M.ROLE_USER), M.PASSWORD))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
