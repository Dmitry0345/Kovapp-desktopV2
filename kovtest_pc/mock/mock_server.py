#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тестовый сервер «Ковчега» — для проверки приложения на ПК без настоящей базы.

Отдаёт веб-версию (/app/) и ответы API в тех же форматах, что настоящий
сервер (server_app/schemas.py и роутеры), с сотрудниками всех ролей из
docs/permissions.md. Изменения не сохраняются: сервер для просмотра и
автотестов. Уведомления приложения идут через настоящий код сервера
(server_app/native_push.py): очередь и протокол канала те же.

    python mobile/tests/mock/mock_server.py --port 8000

Вход: логин — ключ роли (director, admin, head, head_project, head_to,
worker, montage, to, contract, stock, accountant), пароль — test.
"""

import argparse
import asyncio
import datetime as dt
import json
import mimetypes
import os
import re
import secrets
import sys
import threading
import time
import types
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
MOBILE = os.path.abspath(os.path.join(HERE, "..", ".."))
ROOT = os.path.dirname(MOBILE)
WEB = os.environ.get("KOVCHEG_WEB") or os.path.join(ROOT, "web_app")   # KOVCHEG_WEB — другая копия интерфейса
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import wsproto  # noqa: E402

mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("application/wasm", ".wasm")

PASSWORD = "test"


def server_csp() -> str:
    """Политика содержимого — дословно из server_app/core.py."""
    path = os.path.join(ROOT, "server", "server_app", "core.py")
    if not os.path.isfile(path):        # копия в модуле автотестов (kovtest_pc/mock): сайт она не раздаёт
        return "default-src 'self'"
    core = open(path, encoding="utf-8").read()
    start = core.index("DEFAULT_CSP = (")
    block = core[start:core.index("\n)\n", start)]
    csp = "".join(re.findall(r'f?"([^"]*)"', block))
    return (csp.replace("{KLIPY_MEDIA}", "https://static.klipy.com https://static1.klipy.com https://static2.klipy.com")
               .replace("{KLIPY_API}", "https://api.klipy.com"))


CSP = server_csp()
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), microphone=(self), camera=(self)",
    "Content-Security-Policy": CSP,
}

# ── сотрудники: все роли из docs/permissions.md ──────────────────────────────
DEPARTMENTS = ["Руководство", "Проектный отдел", "Монтажный отдел", "Отдел ТО", "Договорной отдел", "Склад", "Бухгалтерия"]
# ключ роли, id, фамилия, имя, отчество, должность, статус, отдел
PEOPLE = [
    ("director", 1, "Орлов", "Андрей", "Викторович", "Директор", "директор", "Руководство"),
    ("admin", 2, "Служебный", "Администратор", "", "Администратор", "администратор", ""),
    ("head", 3, "Смирнов", "Павел", "Олегович", "Руководитель монтажного отдела", "руководитель отдела", "Монтажный отдел"),
    ("head_project", 4, "Лебедева", "Анна", "Игоревна", "Главный инженер проекта", "руководитель отдела", "Проектный отдел"),
    ("worker", 5, "Кузнецова", "Мария", "Сергеевна", "Инженер-проектировщик", "работник", "Проектный отдел"),
    ("montage", 6, "Иванов", "Сергей", "Петрович", "Монтажник", "работник", "Монтажный отдел"),
    ("to", 7, "Петров", "Игорь", "Андреевич", "Техник ТО", "работник", "Отдел ТО"),
    ("contract", 8, "Соколова", "Елена", "Дмитриевна", "Специалист по договорам", "работник", "Договорной отдел"),
    ("stock", 9, "Васильев", "Дмитрий", "Николаевич", "Кладовщик", "работник", "Склад"),
    ("accountant", 10, "Морозова", "Ольга", "Викторовна", "Бухгалтер", "работник", "Бухгалтерия"),
    ("head_to", 11, "Новиков", "Роман", "Сергеевич", "Руководитель отдела ТО", "руководитель отдела", "Отдел ТО"),
    ("deputy", 12, "Ковалёв", "Илья", "Маркович", "Заместитель руководителя", "руководитель отдела", "Монтажный отдел"),
    ("montage2", 13, "Фёдоров", "Антон", "Витальевич", "Монтажник", "работник", "Монтажный отдел"),
    ("head_buh", 14, "Зайцева", "Нина", "Павловна", "Главный бухгалтер", "руководитель отдела", "Бухгалтерия"),
    ("contract2", 15, "Белова", "Ирина", "Юрьевна", "Юрист", "работник", "Договорной отдел"),
]
# логины ролей системы автотестов (kovcheg-autotest, config/roles.yaml)
LOGIN_ALIASES = {"at_director": 1, "at_head_mont": 3, "at_deputy_mont": 12, "at_worker_mont": 6, "at_worker_mont2": 13,
                 "at_head_to": 11, "at_worker_to": 7, "at_head_buh": 14, "at_worker_buh": 10, "at_contract1": 8,
                 "at_contract2": 15, "at_stock": 9}
EMP = {}
ROLE_USER = {}
for role, i, ln, fn, mn, pos, status, dep in PEOPLE:
    EMP[i] = {"id": i, "name": f"{ln} {fn}", "last_name": ln, "first_name": fn, "middle_name": mn, "position": pos,
              "status": status, "phone_number": "+7 900 000-%02d-%02d" % (i, i * 3 % 100),
              "email": f"{role}@kovcheg.test", "profile": "", "department": dep}
    ROLE_USER[role] = i

MANAGEMENT = {"директор", "администратор"}


def is_management(uid):
    return EMP[uid]["status"] in MANAGEMENT


def is_head(uid):
    return EMP[uid]["status"] == "руководитель отдела"


def dept(uid):
    return EMP[uid]["department"]


def short(uid):
    e = EMP.get(uid)
    return None if e is None else {"id": e["id"], "last_name": e["last_name"], "first_name": e["first_name"], "department": e["department"]}


def uploader(uid):
    e = EMP.get(uid)
    return None if e is None else {"id": e["id"], "last_name": e["last_name"], "first_name": e["first_name"],
                                   "middle_name": e["middle_name"], "department": e["department"]}


def person(uid):
    e = EMP.get(uid)
    return None if e is None else {"id": e["id"], "last_name": e["last_name"], "first_name": e["first_name"],
                                   "name": f'{e["last_name"]} {e["first_name"]}', "department": e["department"]}


# ── данные разделов ──────────────────────────────────────────────────────────
TODAY = dt.date.today()


def day(n):
    return (TODAY + dt.timedelta(days=n)).isoformat()


def moment(days, hour=10):
    return dt.datetime.combine(TODAY + dt.timedelta(days=days), dt.time(hour, 15)).isoformat()


PROJECTS = [
    {"id": 1, "name": "Объект «Северный»", "description": "Видеонаблюдение и СКУД жилого комплекса", "organization": "ООО «Северный дом»",
     "responsible_id": 3, "creator_id": 1, "deadline": day(40), "created_at": moment(-30), "updated_at": moment(-2)},
    {"id": 2, "name": "Офис «Альфа»", "description": "Пожарная сигнализация офиса", "organization": "АО «Альфа-Строй»",
     "responsible_id": 4, "creator_id": 8, "deadline": day(75), "created_at": moment(-12), "updated_at": moment(-1)},
]

# этапы: номер, отдел, участники, ответственные, завершён, описание
TASKS = [
    {"id": 101, "name": "Монтаж видеонаблюдения на объекте «Северный»", "description": "Смонтировать 24 камеры, коммутаторы и регистратор.",
     "deadline": day(5), "creator_id": 1, "responsible": [3], "assigned": [6, 5], "project_id": 1, "customer": "ООО «Северный дом»",
     "stages": [(1, "Проектный отдел", [5], [4], True, "Схема размещения камер согласована."),
                (2, "Монтажный отдел", [6], [3], False, "Прокладка кабеля, установка камер.")]},
    {"id": 102, "name": "Проект СКУД для офиса «Альфа»", "description": "Подготовить проект системы контроля доступа.",
     "deadline": day(12), "creator_id": 4, "responsible": [4], "assigned": [5], "project_id": 2, "customer": "АО «Альфа-Строй»",
     "stages": [(1, "Проектный отдел", [5], [4], False, "Спецификация оборудования и чертежи.")]},
    {"id": 103, "name": "Плановое ТО пожарной сигнализации", "description": "Ежеквартальное обслуживание, акт по форме.",
     "deadline": day(-1), "creator_id": 1, "responsible": [11], "assigned": [7], "project_id": None, "customer": "ТСЖ «Лесное»",
     "stages": [(1, "Отдел ТО", [7], [11], False, "Проверить извещатели и приборы.")]},
    {"id": 104, "name": "Договор с АО «Альфа-Строй»", "description": "Согласовать договор и приложения.",
     "deadline": day(3), "creator_id": 1, "responsible": [8], "assigned": [8], "project_id": 2, "customer": "АО «Альфа-Строй»",
     "stages": [(1, "Договорной отдел", [8], [8], False, "Протокол разногласий.")]},
    {"id": 105, "name": "Инвентаризация склада", "description": "Сверить остатки с учётом.",
     "deadline": day(20), "creator_id": 1, "responsible": [9], "assigned": [9, 10], "project_id": None, "customer": "",
     "stages": [(1, "Склад", [9], [], False, "Пересчёт остатков."), (2, "Бухгалтерия", [10], [], False, "Сверка с учётом.")]},
]

ORGANIZATIONS = [
    {"id": 1, "name": "ООО «Северный дом»", "description": "Застройщик ЖК «Северный»", "who_create": "Орлов Андрей",
     "when_create": moment(-90), "who_edit": None, "when_edit": None,
     "phones": [{"id": 1, "phone_number": "+7 812 555-01-01", "description": "Приёмная", "organization_id": 1}],
     "emails": [{"id": 1, "email": "info@sevdom.test", "description": "Общая почта", "organization_id": 1}]},
    {"id": 2, "name": "АО «Альфа-Строй»", "description": "Генподрядчик", "who_create": "Соколова Елена",
     "when_create": moment(-40), "who_edit": "Соколова Елена", "when_edit": moment(-5),
     "phones": [{"id": 2, "phone_number": "+7 812 555-02-02", "description": "Снабжение", "organization_id": 2}],
     "emails": []},
    {"id": 3, "name": "ТСЖ «Лесное»", "description": "", "who_create": "Орлов Андрей", "when_create": moment(-200),
     "who_edit": None, "when_edit": None, "phones": [], "emails": [{"id": 3, "email": "tsg@lesnoe.test", "description": "Председатель", "organization_id": 3}]},
]

EQUIPMENT = [
    {"id": 1, "name": "Перфоратор Makita HR2470", "quantity": 4.0, "unit": "шт", "issued": 1.0},
    {"id": 2, "name": "Лестница трёхсекционная", "quantity": 2.0, "unit": "шт", "issued": 0.0},
    {"id": 3, "name": "Кабель UTP cat.5e", "quantity": 305.0, "unit": "м", "issued": 120.0},
]
UNITS = [{"id": 1, "name": "шт", "full_name": "Штука"}, {"id": 2, "name": "м", "full_name": "Метр"},
         {"id": 3, "name": "уп", "full_name": "Упаковка"}]

TEMPLATES = [{"id": 1, "template_name": "Проект и монтаж", "num_stages": 2, "departments": "Проектный отдел,Монтажный отдел"},
             {"id": 2, "template_name": "Плановое ТО", "num_stages": 1, "departments": "Отдел ТО"}]


def notifications_for(uid):
    base = [
        ("Вам назначена задача «Монтаж видеонаблюдения на объекте «Северный»»", "task_view", 101, False, -0.1),
        ("Этап «Проектный отдел» завершён", "task_view", 101, True, -1),
        ("Выдано оборудование: Перфоратор Makita HR2470", "equipment", 1, True, -3),
    ]
    out = []
    for n, (msg, section, target, read, days) in enumerate(base, 1):
        out.append({"id": uid * 100 + n, "message": msg, "is_read": read, "created_at": moment(int(days), 9 + n),
                    "target_section": section, "target_id": target})
    return out


def private_chat_id(a, b):
    lo, hi = sorted((a, b))
    return 10 + lo * 20 + hi


def chat_members(chat_id):
    if chat_id == 900:
        return set(EMP)
    if chat_id in GROUPS:
        return set(GROUPS[chat_id]["members"])
    for a in EMP:
        for b in EMP:
            if a < b and private_chat_id(a, b) == chat_id:
                return {a, b}
    return set()


def _last(chat_id, row, text, sender, when, hm):
    m = live.last_message(chat_id)
    if m is not None:
        text = m["text"] or ("Фото" if m["files"] and m["files"][0]["is_image"] else ("Файл" if m["files"] else ""))
        sender = (m["sender_first_name"] or m["sender_name"])
        ts = dt.datetime.fromisoformat(m["timestamp"])
        row.update(last_message=text, last_message_sender=sender, last_message_time=ts.strftime("%H:%M"),
                   timestamp=ts.timestamp(), sort_datetime=m["timestamp"])
    else:
        row.update(last_message=text, last_message_sender=sender, last_message_time=hm, timestamp=when)
    return row


def chats_for(uid):
    others = [e for e in (1, 3, 5, 6) if e != uid][:2]
    rows = []
    for k, other in enumerate(others):
        e = EMP[other]
        cid = private_chat_id(uid, other)
        rows.append(_last(cid, {"id": cid, "name": f'{e["last_name"]} {e["first_name"]}', "last_name": e["last_name"],
                                "first_name": e["first_name"], "is_group": False, "other_user_id": other, "image_path": None,
                                "avatar_url": None, "has_messages": True, "sort_datetime": moment(0, 10 - k),
                                "time_source": "message", "role": "member", "message_count": 12 - k,
                                "created_at": moment(-20), "unread_count": 2 if k == 0 else 0},
                          "Документы по объекту отправил" if k == 0 else "Хорошо, завтра в 9:00",
                          e["first_name"], time.time() - 3600 * (k + 1), "10:%02d" % (15 + k)))
    rows.append(_last(900, {"id": 900, "name": "Монтаж «Северный»", "last_name": "", "first_name": "", "is_group": True,
                            "other_user_id": None, "image_path": None, "avatar_url": None, "has_messages": True,
                            "sort_datetime": moment(0, 9), "time_source": "message", "role": "member", "message_count": 40,
                            "created_at": moment(-15), "unread_count": 0},
                      "Фото с объекта", "Иванов Сергей", time.time() - 7200 * 3, "09:40"))
    for gid, group in sorted(GROUPS.items()):
        if uid in group["members"]:
            rows.append(_last(gid, {"id": gid, "name": group["name"], "last_name": "", "first_name": "", "is_group": True,
                                    "other_user_id": None, "image_path": None, "avatar_url": None, "has_messages": False,
                                    "sort_datetime": moment(0, 8), "time_source": "created", "role": "owner" if group["owner"] == uid else "member",
                                    "message_count": 0, "created_at": moment(0, 8), "unread_count": 0},
                              "", "", time.time() - 7200 * 5, "08:00"))
    return rows


def notification_url(target_section, target_id):
    """Куда ведёт уведомление раздела — как server_app.push.notification_url."""
    try:
        target_id = int(target_id) if target_id not in (None, "") else None
    except (TypeError, ValueError):
        target_id = None
    if target_id:
        if target_section in ("task_view", "task_complete"):
            return "#/tasks/%d" % target_id
        if target_section == "warehouse":
            return "#/projects/%d/warehouse" % target_id
        if target_section == "project":
            return "#/projects/%d" % target_id
        if target_section == "equipment":
            return "#/equipment"
    return "#/notifications"


def _chat_sockets(user_ids):
    with SOCK_LOCK:
        return [(u, s) for u in user_ids for s in list(CHAT_SOCKETS.get(u, []))]


def broadcast_chat(chat_id, payload):
    """Событие всем участникам чата, как manager.broadcast_to_chat сервера."""
    text = json.dumps(payload, ensure_ascii=False)
    for _, sock in _chat_sockets(chat_members(chat_id)):
        try:
            sock.send_text(text)
        except Exception:
            pass


def broadcast_all(payload):
    """Событие всем подключённым, как manager.broadcast / _broadcast_all сервера (события файлов)."""
    text = json.dumps(payload, ensure_ascii=False)
    with SOCK_LOCK:
        socks = [s for lst in CHAT_SOCKETS.values() for s in list(lst)]
    for sock in socks:
        try:
            sock.send_text(text)
        except Exception:
            pass


def chats_updated(user_ids):
    for u, sock in _chat_sockets(user_ids):
        try:
            sock.send_text(json.dumps({"type": "chats_updated", "chats": chats_for(u)}, ensure_ascii=False))
        except Exception:
            pass


def push_chat(chat_id, sender, text, files):
    author = EMP[sender]
    body = (text or "")[:120] or ("Фото" if files == 1 else "Файлы: %d" % files if files else "")
    notify_native(sorted(chat_members(chat_id) - {sender}), "%s %s" % (author["last_name"], author["first_name"]), body,
                  "#/messenger/%d" % chat_id, "chat-%d" % chat_id, {"kind": "chat", "chat_id": chat_id}, 3600)


# ── права (как на сервере; см. docs/permissions.md) ──────────────────────────
def task_participants(t):
    ids = {t["creator_id"], *t["responsible"], *t["assigned"]}
    for _, _, parts, leads, _, _ in t["stages"]:
        ids.update(parts)
        ids.update(leads)
    return ids


def can_see_task(uid, t):
    return is_management(uid) or uid in task_participants(t)


def stage_leads(stage):
    number, department, parts, leads, done, text = stage
    if leads:
        return list(leads)
    heads = [e["id"] for e in EMP.values() if e["department"] == department and e["status"] == "руководитель отдела"]
    return heads or [e["id"] for e in EMP.values() if e["department"] == department]


def stage_out(t, stage, uid, full=False):
    number, department, parts, leads, done, text = stage
    sid = t["id"] * 10 + number
    out = {"id": sid, "stage_number": number, "text_description": text, "is_completed": done,
           "completion_timestamp": moment(-1) if done else None, "completed_by_employee_id": parts[0] if done and parts else None,
           "department_name": department, "participants": [short(p) for p in parts], "files": live.task_files(t["id"], sid)}
    if full:
        lead_ids = stage_leads(stage)
        out.update({"spans": [], "leads": lead_ids, "i_lead": is_management(uid) or uid in lead_ids})
    return out


def project_short(pid):
    p = next((x for x in PROJECTS if x["id"] == pid), None)
    return None if p is None else {"id": p["id"], "name": p["name"], "deadline": p["deadline"], "organization": p["organization"]}


def current_stage(t):
    """Номер текущего этапа, как Task.state на сервере: первый незавершённый, иначе последний."""
    stages = sorted(t["stages"], key=lambda s: s[0])
    return next((s[0] for s in stages if not s[4]), stages[-1][0] if stages else 1)


def priority_key(t, uid):
    """Порядок «по важности», как task_priority_order на сервере."""
    cur = next((s for s in t["stages"] if s[0] == current_stage(t)), None)
    mine = cur is not None and cur[1] == dept(uid) and not cur[4]
    deadline = dt.date.fromisoformat(t["deadline"]) if t.get("deadline") else None
    late = 2 if deadline is None else (0 if deadline < dt.date.today() else 1 if deadline == dt.date.today() else 2)
    return ((0 if mine else 3) + late, deadline is None, deadline or dt.date.max, t["id"])


TASK_FILTERS = {}      # (сотрудник, архив) → сохранённый фильтр списка задач

# журнал задачи — как server_app/task_journal.py: подписи полей и строки «кто, когда, что»
JOURNAL_TITLES = {"stage_done": "Завершение этапа", "stage_resumed": "Этап возобновлён",
                  "task_file": "Файл задачи", "stage_file": "Файл этапа"}
HISTORY = {}           # задача → строки журнала, новые первыми


def journal_note(task_id, field, text, uid):
    HISTORY.setdefault(task_id, []).insert(0, {
        "field": field, "field_title": JOURNAL_TITLES.get(field, field), "old": "—", "new": text,
        "who": EMP[uid]["name"] if uid in EMP else "неизвестно", "at": dt.datetime.now().isoformat()})


def stage_action(t, stage, uid, action):
    """Завершить или возобновить этап — как routers/stages.py: порядок этапов, журнал, событие сокета."""
    number, department, parts, leads, done, text = stage
    allowed = is_management(uid) or uid in parts or uid in stage_leads(stage) or \
        (is_head(uid) and dept(uid) == department)
    if not allowed:
        raise ApiError(403, "Этапом распоряжаются его участники и ответственные")
    idx = t["stages"].index(stage)
    label = "этап %s (%s)" % (number, department or "—")
    if action == "complete":
        if done:
            raise ApiError(400, "Этап уже завершен")
        prev = next((x for x in t["stages"] if x[0] == number - 1), None)
        if prev is not None and not prev[4]:
            raise ApiError(400, "Предыдущий этап не завершен")
        journal_note(t["id"], "stage_done", label, uid)
    else:
        if not done:
            raise ApiError(400, "Этап не завершен")
        journal_note(t["id"], "stage_resumed", label, uid)
    t["stages"][idx] = (number, department, parts, leads, action == "complete", text)
    broadcast_all({"type": "stage.completed" if action == "complete" else "stage.resumed", "task_id": t["id"],
                   "stage_id": t["id"] * 10 + number, "stage_number": number, "is_completed": action == "complete",
                   "sender_id": uid})
    return stage_out(t, t["stages"][idx], uid, full=True)


# склад проекта «Северный»: строки ведомости с состоянием на складе (поле количества в ячейке)
WAREHOUSE = {1: [
    {"row_no": 1, "name": "Камера IP 4 Мп купольная", "quantity": 24.0, "unit": "шт", "issued": 6.0, "pending": 0,
     "stock_state": "enough", "stock_qty": 30.0},
    {"row_no": 2, "name": "Кабель UTP cat.5e", "quantity": 1500.0, "unit": "м", "issued": 0.0, "pending": 0,
     "stock_state": "short", "stock_qty": 900.0},
    {"row_no": 3, "name": "Коммутатор PoE на 16 портов", "quantity": 4.0, "unit": "шт", "issued": 0.0, "pending": 0,
     "stock_state": "", "stock_qty": None},
] + [{"row_no": n, "name": "", "quantity": None, "unit": "", "issued": 0.0, "pending": 0, "stock_state": "",
      "stock_qty": None} for n in range(4, 13)]}


def task_out(t, uid, archived=False):
    return {"id": t["id"], "name": t["name"], "description": t["description"], "deadline": t["deadline"],
            "state": str(current_stage(t)), "activity": "Архивная" if archived else "Активная", "creator_id": t["creator_id"],
            "customer_organization": t["customer"], "project_id": t["project_id"],
            "responsible_persons": [short(i) for i in t["responsible"]], "assigned_employees": [short(i) for i in t["assigned"]],
            "creator": short(t["creator_id"]), "stage_details": [stage_out(t, s, uid) for s in t["stages"]],
            "task_files": live.task_files(t["id"]), "archived_at": None, "who_archived_employee": None, "project": project_short(t["project_id"])}


def project_out(p, uid):
    tasks = [t for t in TASKS if t["project_id"] == p["id"]]
    own = p["creator_id"] == uid or EMP[uid]["status"] == "директор"
    return dict(p, responsible=uploader(p["responsible_id"]), creator=uploader(p["creator_id"]), tasks_total=len(tasks),
                tasks_active=len(tasks), files_total=0, can_edit=own, can_delete=own, can_upload=True)


def equipment_access(uid):
    m = is_management(uid)
    return {"can_view": True, "can_edit_goods": m or dept(uid) == "Бухгалтерия", "can_issue": m or dept(uid) == "Склад",
            "can_accept_return": m or dept(uid) == "Склад", "is_management": m, "user_id": uid}


def warehouse_access(uid):
    m = is_management(uid)
    view = m or dept(uid) in ("Бухгалтерия", "Монтажный отдел", "Склад")
    return {"can_view": view, "can_edit_goods": m, "can_edit_stock": m or dept(uid) == "Склад",
            "can_issue": m or dept(uid) == "Склад", "is_management": m, "user_id": uid}


# ── уведомления приложения: настоящий код сервера ────────────────────────────
def load_native():
    """server_app.native_push и роутер канала; FastAPI и SQLAlchemy не нужны."""
    for name, attrs in (("fastapi", None), ("sqlalchemy", None), ("sqlalchemy.orm", {"Session": object})):
        try:
            __import__(name)
        except Exception:
            mod = types.ModuleType(name)
            if name == "fastapi":
                class _Router:
                    def __init__(self, *a, **k): pass
                    def _d(self, *a, **k): return lambda fn: fn
                    get = post = delete = put = websocket = _d

                class _HTTPException(Exception):
                    def __init__(self, status_code=500, detail=""):
                        super().__init__(detail)
                        self.status_code, self.detail = status_code, detail
                mod.__dict__.update(APIRouter=_Router, Depends=lambda x=None: None, HTTPException=_HTTPException,
                                    Request=object, WebSocket=object, WebSocketDisconnect=type("WebSocketDisconnect", (Exception,), {}))
            if attrs:
                mod.__dict__.update(attrs)
            sys.modules[name] = mod
    if "server.server_app.core" not in sys.modules:
        core = types.ModuleType("server.server_app.core")
        core.get_current_user_id = lambda: None
        core.get_db = lambda: None
        sys.modules["server.server_app.core"] = core
    from server.server_app import native_push as np
    from server.server_app.routers import native_push as router
    return np, router


NP, NP_ROUTER = load_native()
DEVICES = {}          # device_id → {"user_id", "provider", "secret_hash", "payload_key"}
PROBES = {}
LOOP = asyncio.new_event_loop()
threading.Thread(target=LOOP.run_forever, name="mock-async", daemon=True).start()


def run_async(coro, timeout=10):
    return asyncio.run_coroutine_threadsafe(coro, LOOP).result(timeout)


def native_loader(user_ids):
    return [{"device_id": d, "user_id": v["user_id"], "provider": "direct", "fcm_token": None, "payload_key": v["payload_key"]}
            for d, v in DEVICES.items() if v["user_id"] in user_ids]


def native_auth(device_id, secret):
    d = DEVICES.get(device_id)
    if d is None or not NP.secret_matches(d["secret_hash"], secret):
        return None
    return {"device_id": device_id, "user_id": d["user_id"], "provider": "direct"}


def notify_native(user_ids, title, body, url, tag, extra=None, ttl=3600):
    payload = {"title": title, "body": body[:120], "url": url, "tag": tag, "ts": dt.datetime.now().isoformat()}
    payload.update(extra or {})
    return run_async(NP.notify(user_ids, payload, ttl, loader=native_loader))


class AsyncSocket:
    """Обёртка сокета для serve_socket сервера: методы как у WebSocket FastAPI."""

    def __init__(self, ws):
        self.ws = ws
        self.inbox = asyncio.Queue()

    async def accept(self):
        return None

    async def receive_text(self):
        item = await self.inbox.get()
        if item is None:
            raise sys.modules["fastapi"].WebSocketDisconnect()
        return item

    async def send_text(self, text):
        self.ws.send_text(text)

    async def close(self, code=1000):
        self.ws.close(code)


# ── HTTP ─────────────────────────────────────────────────────────────────────
TOKENS = {}
PREFS = {}
CHAT_SOCKETS = {}      # user_id → сокеты /ws/chat (для событий в тестах)
import live_store as live   # живая переписка и файлы (в памяти, пока сервер запущен)

MOCK_CFG = {"upload_delay_ms": 0}   # настройки тестов: задержка приёма файлов
EXTRA_NOTES = {}       # уведомления, созданные через API
GROUPS = {}            # группы чата, созданные через API
SOCK_LOCK = threading.Lock()


class ApiError(Exception):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status, self.detail = status, detail


class Handler(BaseHTTPRequestHandler):
    server_version = "KovchegMock/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if self.server.verbose:
            sys.stderr.write("[mock] " + (fmt % args) + "\n")

    # ── ответы ──
    def _send(self, status, body: bytes, ctype: str, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def json(self, obj, status=200):
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json")

    # ── запрос ──
    def body_raw(self):
        if not hasattr(self, "_raw_body"):
            n = int(self.headers.get("Content-Length") or 0)
            self._raw_body = self.rfile.read(n) if n else b""
        return self._raw_body

    def send_blob(self, blob, attachment=False):
        """Файл из живого хранилища; видео — частями (Range), как у сервера."""
        data, ctype = blob["data"], blob["mime"]
        extra = {"Cache-Control": "no-store", "Accept-Ranges": "bytes"}
        if attachment:
            extra["Content-Disposition"] = "attachment; filename*=UTF-8''" + urllib.parse.quote(blob["name"])
        rng = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if rng and data:
            start = int(rng.group(1) or 0)
            end = min(len(data) - 1, int(rng.group(2)) if rng.group(2) else len(data) - 1)
            extra["Content-Range"] = "bytes %d-%d/%d" % (start, end, len(data))
            return self._send(206, data[start:end + 1], ctype, extra)
        return self._send(200, data, ctype, extra)

    def body_json(self):
        raw = self.body_raw()
        ctype = self.headers.get("Content-Type", "")
        if "application/json" in ctype:
            try:
                return json.loads(raw.decode("utf-8") or "null")
            except ValueError:
                raise ApiError(400, "Некорректный JSON")
        if "application/x-www-form-urlencoded" in ctype:
            return {k: v[0] for k, v in urllib.parse.parse_qs(raw.decode("utf-8")).items()}
        return raw

    def user(self, required=True):
        auth = self.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        if not token:
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            token = (q.get("token") or [""])[0]
        uid = TOKENS.get(token)
        if uid is None and token.startswith("file."):
            uid = TOKENS.get(token[5:])
        if uid is None and required:
            raise ApiError(401, "Could not validate credentials")
        return uid

    def do_GET(self):
        self.route("GET")

    def do_HEAD(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PUT(self):
        self.route("PUT")

    def do_PATCH(self):
        self.route("PATCH")

    def do_DELETE(self):
        self.route("DELETE")

    def route(self, method):
        # обработчик один на соединение: при keep-alive тело прошлого запроса не должно достаться новому
        self.__dict__.pop("_raw_body", None)
        # тело читается всегда, даже если ответ его не использует: иначе его байты
        # остаются в соединении и склеиваются со следующим запросом («501»)
        self.body_raw()
        parts = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parts.path)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parts.query).items()}
        try:
            if path in ("/ws/chat", "/ws/native-push"):
                return self.websocket(path)
            if path == "/app" or path.startswith("/app/"):
                return self.static(path[len("/app"):] or "/")
            if path == "/":
                self.send_response(302)
                self.send_header("Location", "/app/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            result = self.api(method, path.strip("/"), query)
            if result is None:
                return
            if isinstance(result, tuple):
                self.json(result[1], result[0])
            else:
                self.json(result)
        except ApiError as e:
            self.json({"detail": e.detail}, e.status)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ── веб-версия ──
    def static(self, rel):
        rel = rel.lstrip("/") or "index.html"
        full = os.path.normpath(os.path.join(WEB, rel))
        if not full.startswith(WEB + os.sep) or not os.path.isfile(full):
            if os.path.isdir(full) and os.path.isfile(os.path.join(full, "index.html")):
                full = os.path.join(full, "index.html")
            else:
                raise ApiError(404, "Файл не найден")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/json", "image/svg+xml", "application/manifest+json"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            body = f.read()
        extra = {"Cache-Control": "no-cache, must-revalidate"} if full.endswith((".html", ".js", ".css", ".webmanifest")) else {}
        self._send(200, body, ctype, extra)

    # ── сокеты ──
    def websocket(self, path):
        if not wsproto.handshake(self):
            raise ApiError(400, "Ожидалось подключение WebSocket")
        self.close_connection = True
        ws = wsproto.Socket(self.rfile, self.wfile)
        if path == "/ws/chat":
            first = ws.receive()                      # {"type":"auth","token":...}
            try:
                token = json.loads(first or "{}").get("token", "")
            except ValueError:
                token = ""
            if token not in TOKENS:
                ws.close(1008)
                return
            uid = TOKENS[token]
            with SOCK_LOCK:
                CHAT_SOCKETS.setdefault(uid, []).append(ws)
            while True:
                msg = ws.receive()
                if msg is None:
                    with SOCK_LOCK:
                        CHAT_SOCKETS.get(uid, []).remove(ws) if ws in CHAT_SOCKETS.get(uid, []) else None
                    return
                if msg == "ping":
                    ws.send_text("pong")
                    continue
                try:
                    data = json.loads(msg)
                except ValueError:
                    continue
                if isinstance(data, dict) and data.get("type") == "ping":
                    ws.send_text(json.dumps({"type": "pong"}))
                elif isinstance(data, dict) and data.get("type") == "message":
                    cid = int(data.get("chat_id") or 0)
                    members = chat_members(cid)
                    if uid not in members:
                        ws.send_text(json.dumps({"type": "error", "message": "Вы не участник этого чата"}, ensure_ascii=False))
                        continue
                    live.seed(cid, members, EMP)
                    msg = live.add_text(cid, uid, EMP, data.get("text"), data.get("reply_to"))
                    broadcast_chat(cid, dict(msg, type="message", client_id=data.get("client_id"), reply_to_message=None))
                    chats_updated(members)
                    push_chat(cid, uid, msg["text"], 0)
                elif isinstance(data, dict) and data.get("type") == "edit":
                    cid = int(data.get("chat_id") or 0)
                    if live.edit_text(cid, uid, int(data.get("message_id") or 0), data.get("new_text")):
                        broadcast_chat(cid, {"type": "edit", "message_id": int(data["message_id"]),
                                             "new_text": data.get("new_text"), "chat_id": cid})
                        chats_updated(chat_members(cid))
                elif isinstance(data, dict) and data.get("type") == "delete":
                    cid = live.delete_message(uid, int(data.get("message_id") or 0))
                    if cid is not None:
                        broadcast_chat(cid, {"type": "delete", "message_id": int(data["message_id"]), "chat_id": cid})
                        chats_updated(chat_members(cid))
        # канал уведомлений приложения — настоящий serve_socket сервера
        adapter = AsyncSocket(ws)
        future = asyncio.run_coroutine_threadsafe(NP_ROUTER.serve_socket(adapter, NP.HUB, native_auth), LOOP)
        while True:
            msg = ws.receive()
            LOOP.call_soon_threadsafe(adapter.inbox.put_nowait, msg)
            if msg is None:
                break
        try:
            future.result(5)
        except Exception:
            pass

    # ── API ──
    def api(self, method, path, q):  # noqa: C901 — одна таблица маршрутов
        seg = path.split("/")
        if method == "POST" and path == "login":
            form = self.body_json()
            login = str(form.get("username", "")).strip()
            uid = ROLE_USER.get(login) or LOGIN_ALIASES.get(login)
            if uid is None or form.get("password") != PASSWORD:
                raise ApiError(401, "Неверный логин или пароль")
            token = "mock." + secrets.token_hex(16)
            TOKENS[token] = uid
            return {"access_token": token, "token_type": "bearer", "user_id": uid}
        if method == "POST" and path.startswith("reset-password/"):
            return {"success": False, "message": "На тестовом сервере восстановление пароля отключено", "attempts_left": None}
        if method == "POST" and path == "push/ack":
            data = self.body_json() or {}
            probe = PROBES.get(str(data.get("probe") or ""))
            if not probe:
                raise ApiError(404, "Метка проверки не найдена или устарела")
            probe["acks"].append({"subscription_id": None, "user_agent": self.headers.get("User-Agent", "")[:300],
                                  "delay_ms": int((time.time() - probe["at"]) * 1000), "endpoint": str(data.get("endpoint", ""))[:80]})
            return {"status": "ok"}
        if method == "POST" and path in ("push/native/token", "push/native/unregister"):
            data = self.body_json() or {}
            d = DEVICES.get(str(data.get("device_id", "")))
            if d is None or not NP.secret_matches(d["secret_hash"], str(data.get("secret", ""))):
                raise ApiError(403, "Устройство не найдено")
            if path.endswith("unregister"):
                DEVICES.pop(str(data["device_id"]), None)
                return {"status": "removed"}
            return {"status": "ok"}

        uid = self.user()

        if seg[0] == "mock" and len(seg) == 3 and seg[1] == "file":     # файл для проверки скачивания
            body = bytes((i * 7 + 3) % 256 for i in range(300 * 1024 + 17))
            self._send(200, body, "application/octet-stream",
                       {"Content-Disposition": 'attachment; filename="%s"' % urllib.parse.quote(seg[2])})
            return None
        if method == "POST" and path == "files/access-token":
            token = self.headers.get("Authorization", "")[7:]
            return {"access_token": "file." + token, "token_type": "bearer", "expires_in": 1200}
        # сотрудники
        if path == "employees" and method == "GET":
            return list(EMP.values())
        if path == "employees/departments":
            return DEPARTMENTS[1:]
        if path == "employees/positions":
            return sorted({e["position"] for e in EMP.values()})
        if path == "employees/photo/ids":
            return []
        if len(seg) == 2 and seg[0] == "employees" and seg[1].isdigit() and method == "GET":
            e = EMP.get(int(seg[1]))
            if e is None:
                raise ApiError(404, "Сотрудник не найден")
            return e
        if len(seg) == 3 and seg[0] == "employees" and seg[2] == "notifications":
            if int(seg[1]) != uid:
                raise ApiError(403, "Можно читать только свои уведомления")
            return EXTRA_NOTES.get(uid, []) + notifications_for(uid)
        if len(seg) == 3 and seg[0] == "employees" and seg[2] == "photo":
            raise ApiError(404, "Фото нет")
        if path == "notifications/mark-as-read":
            return {"status": "ok"}
        if path == "notifications" and method == "POST":
            data = self.body_json() or {}
            made = []
            for target in [int(u) for u in data.get("user_ids", [])]:
                note = {"id": 5000 + len(EXTRA_NOTES), "message": str(data.get("message", ""))[:1000], "is_read": False,
                        "created_at": dt.datetime.now().isoformat(), "target_section": data.get("target_section") or "notifications",
                        "target_id": data.get("target_id")}
                EXTRA_NOTES.setdefault(target, []).insert(0, note)
                made.append(note)
                notify_native([target], "Уведомление", note["message"], notification_url(note["target_section"], note["target_id"]),
                              "notification-%s" % (note["target_id"] or "general"),
                              {"kind": "notification", "target_section": note["target_section"], "target_id": note["target_id"]}, 86400)
                # открытое приложение получает событие сразу, как от настоящего сервера
                event = json.dumps({"type": "notification.new", "notification_id": note["id"], "message": note["message"],
                                    "target_section": note["target_section"], "target_id": note["target_id"]}, ensure_ascii=False)
                for _, sock in _chat_sockets([target]):
                    try:
                        sock.send_text(event)
                    except Exception:
                        pass
            return made
        # настройки
        if path == "preferences" and method == "GET":
            return [{"key": k, "value": v, "updated_at": None} for (u, k), v in PREFS.items() if u == uid]
        if seg[0] == "preferences" and len(seg) == 2:
            if method == "PUT":
                PREFS[(uid, seg[1])] = (self.body_json() or {}).get("value")
            if (uid, seg[1]) not in PREFS:
                return {"key": seg[1], "value": None, "updated_at": None}     # как настоящий сервер
            return {"key": seg[1], "value": PREFS[(uid, seg[1])], "updated_at": moment(0)}
        # push
        if path == "push/config":
            return {"enabled": False, "public_key": ""}
        if path == "push/native/config":
            return {"enabled": True, "fcm": False, "direct": True, "ws_path": NP.WS_PATH, "ping": NP.PING_SECONDS, "version": NP.PROTOCOL_VERSION}
        if path == "push/native/register":
            data = self.body_json() or {}
            device_id = str(data.get("device_id", ""))
            if not NP.DEVICE_ID_RE.fullmatch(device_id):
                raise ApiError(400, "device_id — 32 шестнадцатеричных знака")
            if method == "DELETE":
                gone = DEVICES.get(device_id, {}).get("user_id") == uid and DEVICES.pop(device_id, None) is not None
                return {"status": "removed" if gone else "not_found"}
            secret, key = NP.new_secret(), NP.new_payload_key()
            previous = DEVICES.get(device_id, {}).get("user_id")
            DEVICES[device_id] = {"user_id": uid, "provider": "direct", "secret_hash": NP.hash_secret(secret), "payload_key": key,
                                  "model": str(data.get("model", ""))[:120]}
            if previous not in (None, uid):
                run_async(NP.HUB.forget(device_id))
            return {"device_id": device_id, "secret": secret, "payload_key": key, "provider": "direct", "ping": NP.PING_SECONDS}
        if path == "push/test" and method == "POST":
            data = self.body_json() or {}
            probe = str((data or {}).get("probe") or "") if isinstance(data, dict) else ""
            extra = None
            if probe:
                if not re.fullmatch(r"[0-9a-f]{32}", probe):
                    raise ApiError(400, "Метка проверки — 32 шестнадцатеричных знака")
                PROBES[probe] = {"user_id": uid, "at": time.time(), "acks": []}
                extra = {"probe": probe}
            sent = notify_native([uid], "Проверка", "Push-уведомления работают", "#/notifications", "push-test", extra)
            return {"status": "sent", "probe": probe or None, "native": sent}
        if len(seg) == 3 and seg[:2] == ["push", "test"]:
            item = PROBES.get(seg[2])
            if not item or item["user_id"] != uid:
                raise ApiError(404, "Метка проверки не найдена")
            return {"probe": seg[2], "acks": item["acks"]}
        if path == "push/native/devices":            # только для тестов стенда
            return [{"device_id": k, **{x: v[x] for x in ("user_id", "provider", "model")}} for k, v in DEVICES.items()]
        if path == "mock/config" and method == "POST":       # только для тестов: поведение сервера
            MOCK_CFG.update(self.body_json() or {})
            return MOCK_CFG
        if path == "upload" and method == "POST":            # файлы задачи и этапа — в живое хранилище
            raw = self.body_raw()
            time.sleep(min(15.0, MOCK_CFG.get("upload_delay_ms", 0) / 1000.0))
            fields, parts = live.parse_multipart(raw, self.headers.get("Content-Type", ""))
            stage_id = int(fields["stage_detail_id"]) if fields.get("stage_detail_id") else None
            task_id = int(fields.get("task_id") or 0) or (stage_id // 10 if stage_id else 0)
            t = next((x for x in TASKS if x["id"] == task_id), None)
            if t is None or not can_see_task(uid, t):
                raise ApiError(403, "Нет доступа к задаче")
            try:
                items = live.resolve_items(json.loads(fields.get("meta") or "[]"), parts)
            except ValueError as e:
                raise ApiError(400, str(e))
            created = [live.add_task_file(task_id, stage_id, it["name"], it["data"], it["thumb"], it["meta"], short(uid)) for it in items]
            if created:
                journal_note(task_id, "stage_file" if stage_id else "task_file",
                             ("добавлены в этап: " if stage_id else "добавлены: ") +
                             ", ".join("«%s»" % (c.get("filename") or c.get("name") or "") for c in created), uid)
            # как сервер: событие о файле и общее «файлы задачи изменились»
            if created:
                broadcast_all({"type": "stage.file_added" if stage_id else "task.file_added", "task_id": task_id,
                               "stage_id": stage_id, "file_id": created[0].get("id"), "sender_id": uid})
                broadcast_all({"type": "task.files_changed", "task_id": task_id})
            return created
        if path == "uploads/dedup/probe" and method == "POST":
            return {"candidate": False}   # как сервер без похожих файлов
        if path == "uploads/dedup/check" and method == "POST":
            return {"id": uuid.uuid4().hex, "windows": []}
        if path == "uploads/dedup/answer" and method == "POST":
            return {"token": None}                                # совпадений нет — файл отправляется
        if seg[0] == "uploads" and len(seg) >= 2 and seg[1] == "sessions":
            if len(seg) == 2 and method == "POST":
                return live.session_open(uid, self.body_json() or {})
            sess = live.session_get(seg[2], uid) if len(seg) >= 3 else None
            if sess is None:
                raise ApiError(404, "Сессия докачки не найдена")
            if len(seg) == 3 and method == "GET":
                return {"received": len(sess["data"])}
            if len(seg) == 3 and method == "PUT":
                chunk = self.body_raw()
                if int(q.get("offset", 0)) != len(sess["data"]):
                    return (409, {"detail": "Смещение не совпадает", "received": len(sess["data"])})
                sess["data"] += chunk
                return {"received": len(sess["data"])}
            if len(seg) == 4 and seg[3] == "complete":
                import hashlib
                data = bytes(sess["data"]); leaf = 4 * 1024 * 1024; leaves = hashlib.sha256()
                for at in range(0, len(data), leaf):
                    leaves.update(hashlib.sha256(data[at:at + leaf]).digest())
                leaves.update(len(data).to_bytes(8, "big"))
                return {"id": seg[2], "size": len(data), "status": "complete", "tree": leaves.hexdigest(), "sha256": hashlib.sha256(data).hexdigest()}
            if len(seg) == 3 and method == "DELETE":
                live.SESSIONS.pop(seg[2], None)
                return {"status": "deleted"}
        if seg[0] in ("task", "stages") and len(seg) >= 4 and seg[1] == "files" and seg[2].isdigit():
            rec = live.TASK_FILES.get(int(seg[2]))
            blob = live.BLOBS.get("taskfile/%s" % seg[2])
            if rec is None or blob is None:
                raise ApiError(404, "Файл не найден")
            t = next((x for x in TASKS if x["id"] == rec["task_id"]), None)
            if t is None or not can_see_task(uid, t):
                raise ApiError(403, "Нет доступа к файлу")
            if seg[3] == "thumb":
                if blob.get("thumb"):
                    self._send(200, blob["thumb"], "image/jpeg", {"Cache-Control": "no-store"})
                    return None
                if not live.is_image(rec["filename"]):
                    raise ApiError(404, "Миниатюры нет")
            self.send_blob(blob, attachment=seg[3] == "download")
            return None
        if seg[0] == "files" and len(seg) == 3 and seg[1].isdigit() and seg[2] == "view":
            blob = live.BLOBS.get("taskfile/%s" % seg[1])
            if blob is None:
                raise ApiError(404, "Файл не найден")
            self.send_blob(blob)
            return None
        if seg[0] == "files" and len(seg) == 3 and seg[1] in ("task", "stage") and seg[2].isdigit() and method == "DELETE":
            rec = live.TASK_FILES.get(int(seg[2])) or {}
            try:
                live.delete_task_file(int(seg[2]), uid, is_management(uid))
            except PermissionError as e:
                raise ApiError(403, str(e))
            # как сервер: событие об удалении и общее «файлы задачи изменились»
            if rec:
                tid = rec.get("task_id"); sid = rec.get("stage_detail_id")
                broadcast_all({"type": "stage.file_deleted" if seg[1] == "stage" else "task.file_deleted", "task_id": tid,
                               "stage_id": sid, "file_id": int(seg[2]), "sender_id": uid})
                broadcast_all({"type": "task.files_changed", "task_id": tid})
            return {"status": "deleted"}
        if seg[0] == "file_storage" and len(seg) >= 2:
            blob = live.blob_for_storage("/".join(seg[1:]))
            if blob is None:
                raise ApiError(404, "Файл не найден")
            self.send_blob(blob)
            return None
        if path == "mock/slow-upload" and method == "POST":  # только для тестов: долгая отправка файла
            self.body_raw()
            time.sleep(min(10.0, int(q.get("ms", "3000")) / 1000.0))
            return []
        if path == "mock/ws-drop" and method == "POST":      # только для тестов: оборвать сокет (как потеря связи)
            data = self.body_json() or {}
            dropped = 0
            with SOCK_LOCK:
                for u in data.get("user_ids", []):
                    for sock in list(CHAT_SOCKETS.get(int(u), [])):
                        sock.close(1001)
                        dropped += 1
            return {"dropped": dropped}
        if path == "mock/ws-event" and method == "POST":   # только для тестов: событие сокета, как у сервера
            data = self.body_json() or {}
            sent = 0
            with SOCK_LOCK:
                for u in data.get("user_ids", []):
                    for sock in list(CHAT_SOCKETS.get(int(u), [])):
                        try:
                            sock.send_text(json.dumps(data.get("event", {}), ensure_ascii=False))
                            sent += 1
                        except Exception:
                            pass
            return {"sent": sent}
        if path == "mock/task-participant" and method == "POST":   # только для тестов: добавить в задачу
            data = self.body_json() or {}
            t = next((x for x in TASKS if x["id"] == int(data.get("task_id", 0))), None)
            if t is None:
                raise ApiError(404, "Задача не найдена")
            if int(data["user_id"]) not in t["assigned"]:
                t["assigned"].append(int(data["user_id"]))
            return {"assigned": t["assigned"]}
        if path == "mock/notify" and method == "POST":   # только для тестов: разослать уведомление
            data = self.body_json() or {}
            return notify_native([int(u) for u in data.get("user_ids", [uid])], data.get("title", "Уведомление"),
                                 data.get("body", ""), data.get("url", "#/notifications"), data.get("tag", "notification-1"),
                                 data.get("extra"), int(data.get("ttl", 3600)))
        # задачи
        if path in ("tasks", "tasks/light") and method == "GET":
            if str(q.get("is_archived", "false")).lower() == "true":
                return []
            rows = [t for t in TASKS if can_see_task(uid, t)]
            sort = (TASK_FILTERS.get((uid, False)) or {}).get("sort_by") or "priority"
            if sort == "priority":       # порядок по умолчанию считает сервер
                rows.sort(key=lambda t: priority_key(t, uid))
            return [task_out(t, uid) for t in rows]
        if seg[0] == "task-filters" and len(seg) >= 2:
            archived = seg[1] == "true"
            opts = ["id_asc", "id_desc", "deadline_asc", "deadline_desc", "created_asc", "created_desc"]
            opts += ["archived_asc", "archived_desc"] if archived else ["priority"]
            # работающие задачи — без явной сортировки: так сервер отдаёт их по важности
            default = {"sort_by": "archived_desc" if archived else None, "stage_count": None,
                       "min_created_date": None, "max_created_date": None}
            saved = TASK_FILTERS.setdefault((uid, archived), dict(default))
            if len(seg) == 3 and seg[2] == "is-default":
                norm = lambda f: dict(f, sort_by=f.get("sort_by") or ("" if archived else "priority"))   # noqa: E731
                return norm(saved) == norm(default)
            if len(seg) == 3 and seg[2] == "reset" and method == "POST":
                saved.clear(); saved.update(default)
            elif len(seg) == 2 and method == "PUT":
                body = self.body_json() or {}
                saved.update({k: body.get(k) for k in default})
            return dict(saved, id=uid * 10 + int(archived), user_id=uid, is_archived=archived, sort_options=opts,
                        created_at=moment(-10), updated_at=None)
        if seg[0] == "tasks" and len(seg) >= 2 and seg[1].isdigit():
            t = next((x for x in TASKS if x["id"] == int(seg[1])), None)
            if t is None:
                raise ApiError(404, "Задача не найдена")
            if not can_see_task(uid, t):
                raise ApiError(403, "Нет доступа к задаче")
            if len(seg) == 2 and method == "GET":
                return task_out(t, uid)
            if len(seg) == 3 and seg[2] == "history":
                return list(HISTORY.get(t["id"], []))
            if len(seg) == 3 and seg[2] == "files":
                return live.task_files(t["id"])
            if len(seg) == 3 and seg[2] == "completion":
                return {"request": None, "can_decide": is_management(uid),
                        "can_request": is_management(uid) or uid in stage_leads(t["stages"][-1])}
            if len(seg) == 3 and seg[2] == "stages":
                return [stage_out(t, s, uid) for s in t["stages"]]
            if len(seg) >= 4 and seg[2] == "stages" and seg[3].isdigit():
                st = next((s for s in t["stages"] if t["id"] * 10 + s[0] == int(seg[3])), None)
                if st is None:
                    raise ApiError(404, "Этап не найден")
                if len(seg) == 5 and seg[4] == "files":
                    return live.task_files(t["id"], int(seg[3]))
                if len(seg) == 5 and seg[4] in ("complete", "resume") and method == "POST":
                    return stage_action(t, st, uid, seg[4])
                if len(seg) == 4 and method == "GET":
                    return stage_out(t, st, uid, full=True)
        if path == "stage-leads":
            department = q.get("department", "")
            heads = [e for e in EMP.values() if e["department"] == department and e["status"] == "руководитель отдела"]
            people = heads or [e for e in EMP.values() if e["department"] == department]
            return {"department": department, "heads_exist": bool(heads),
                    "people": [{"id": e["id"], "last_name": e["last_name"], "first_name": e["first_name"],
                                "position": e["position"], "is_head": e in heads} for e in people]}
        if path == "templates" and method == "GET":
            return TEMPLATES
        if seg[0] == "templates" and method in ("POST", "PUT", "DELETE"):
            if not is_management(uid):
                raise ApiError(403, "Только администратор может менять шаблоны")
            data = self.body_json() or {}
            if method == "POST" and len(seg) == 1:
                tpl = {"id": max([x["id"] for x in TEMPLATES] or [0]) + 1, "template_name": str(data.get("template_name", "")),
                       "num_stages": int(data.get("num_stages") or 1), "departments": str(data.get("departments", ""))}
                TEMPLATES.append(tpl)
                return (201, tpl)
            tpl = next((x for x in TEMPLATES if len(seg) == 2 and str(x["id"]) == seg[1]), None)
            if tpl is None:
                raise ApiError(404, "Шаблон не найден")
            if method == "DELETE":
                TEMPLATES.remove(tpl)
                return {"status": "success", "message": "Шаблон успешно удален"}
            tpl.update(template_name=str(data.get("template_name", tpl["template_name"])),
                       num_stages=int(data.get("num_stages") or tpl["num_stages"]),
                       departments=str(data.get("departments", tpl["departments"])))
            return tpl
        # проекты
        if path == "projects" and method == "GET":
            return [project_out(p, uid) for p in PROJECTS]
        if path in ("projects/heads", "projects/responsible-candidates"):
            return [uploader(e["id"]) for e in EMP.values() if e["status"] in ("руководитель отдела", "директор")]
        if path == "projects/organizations":
            return [o["name"] for o in ORGANIZATIONS]
        if seg[0] == "projects" and len(seg) >= 2 and seg[1].isdigit():
            p = next((x for x in PROJECTS if x["id"] == int(seg[1])), None)
            if p is None:
                raise ApiError(404, "Проект не найден")
            if len(seg) == 2:
                return project_out(p, uid)
            if seg[2] == "tasks":
                return [{"id": t["id"], "name": t["name"], "deadline": t["deadline"], "activity": "Активная", "state": "В работе",
                         "creation_date": moment(-7), "customer_organization": t["customer"], "total_stages": len(t["stages"]),
                         "completed_stages": sum(1 for s in t["stages"] if s[4]), "departments": [s[1] for s in t["stages"]]}
                        for t in TASKS if t["project_id"] == p["id"]]
            if seg[2] in ("files", "photos"):
                return []
            if seg[2] == "warehouse":
                acc = warehouse_access(uid)
                if not acc["can_view"]:
                    raise ApiError(403, "Склад доступен бухгалтерии, монтажному отделу, отделу склада, директору и администратору")
                return {"rows": [dict(r) for r in WAREHOUSE.get(p["id"], [])], "units": UNITS, "access": acc,
                        "pending": []}
        # организации
        if path == "organizations" and method == "GET":
            return ORGANIZATIONS
        if path == "organizations/search":
            text = q.get("query", q.get("q", "")).lower()
            return [o for o in ORGANIZATIONS if text in o["name"].lower()]
        if seg[0] == "organizations" and len(seg) == 2 and seg[1].isdigit() and method == "GET":
            o = next((x for x in ORGANIZATIONS if x["id"] == int(seg[1])), None)
            if o is None:
                raise ApiError(404, "Организация не найдена")
            return o
        # оборудование и склад
        if path == "equipment" and method == "GET":
            rows = [{"id": e["id"], "name": e["name"], "quantity": e["quantity"], "unit": e["unit"], "has_photo": False,
                     "issued": e["issued"], "left": round(e["quantity"] - e["issued"], 4), "ready": True,
                     "updated_at": moment(-4), "updated_by": person(10), "issues": []} for e in EQUIPMENT]
            return {"rows": rows, "units": UNITS, "access": equipment_access(uid)}
        if path in ("equipment/issues/pending", "warehouse/issues/pending"):
            return []
        if seg[0] == "equipment" and len(seg) == 3 and seg[2] == "history":
            return []
        if path in ("warehouse/units", "warehouse/units/full"):
            return UNITS
        if path == "warehouse/editors" and method == "GET":
            return {"user_ids": [], "people": [], "can_manage": is_management(uid)}
        # мессенджер
        if path == "chat/list":
            return chats_for(uid)
        if path == "chat/group" and method == "POST":
            gid = 7000 + len(GROUPS)
            GROUPS[gid] = {"owner": uid, "name": str((self.body_json() or {}).get("name", "Группа")), "members": {uid}}
            return {"id": gid, "name": GROUPS[gid]["name"], "is_group": True}
        if seg[0] == "chat" and len(seg) == 3 and seg[1] in ("group", "info") and seg[2].isdigit() and method == "GET":
            cid = int(seg[2])
            members = chat_members(cid)
            if uid not in members:
                raise ApiError(404, "Чат или группа не найдена")
            group = cid == 900 or cid in GROUPS
            people = [{"id": m, "name": EMP[m]["name"], "full_name": EMP[m]["name"], "last_name": EMP[m]["last_name"],
                       "first_name": EMP[m]["first_name"], "avatar_url": None,
                       "role": "owner" if (cid in GROUPS and GROUPS[cid]["owner"] == m) else "member"} for m in sorted(members)]
            if seg[1] == "group" or group:     # как на сервере: метаданные группы
                name = "Монтаж «Северный»" if cid == 900 else (GROUPS[cid]["name"] if cid in GROUPS else "")
                return {"chat_id": cid, "name": name, "image_path": None, "my_role": "member", "participants": people,
                        "type": "group" if group else "private", "participant_count": len(people), "is_group": group,
                        "status": "success"}
            other = next((m for m in members if m != uid), uid)
            e = EMP[other]
            return {"chat_id": cid, "other_user_id": other, "user_id": other, "has_chat": True, "is_group": False,
                    "full_name": e["name"], "last_name": e["last_name"], "first_name": e["first_name"],
                    "phone_number": e["phone_number"], "email": e["email"], "position": e["position"],
                    "department": e["department"], "avatar_url": None}
        if seg[0] == "chat" and len(seg) >= 3 and seg[1] == "group" and seg[2].isdigit():
            group = GROUPS.get(int(seg[2]))
            if group is None:
                raise ApiError(404, "Группа не найдена")
            if len(seg) == 4 and seg[3] == "participants" and method == "POST":
                group["members"].update(int(u) for u in (self.body_json() or {}).get("user_ids", []))
                return {"status": "ok", "members": sorted(group["members"])}
            if len(seg) == 3 and method == "DELETE":
                GROUPS.pop(int(seg[2]), None)
                return {"status": "deleted"}
        if seg[0] == "chat" and len(seg) >= 3 and seg[1] == "history":
            cid = int(seg[2])
            members = chat_members(cid)
            if uid not in members:
                raise ApiError(403, "Вы не участник этого чата")
            live.seed(cid, members, EMP)
            return live.history(cid, q, EMP)
        if path == "chat/grouped-message" and method == "POST":
            fields, parts = live.parse_multipart(self.body_raw(), self.headers.get("Content-Type", ""))
            cid = int(fields.get("chat_id") or 0)
            members = chat_members(cid)
            if uid not in members:
                raise ApiError(403, "Вы не участник этого чата")
            live.seed(cid, members, EMP)
            try:
                items = live.resolve_items(json.loads(fields.get("meta") or "[]"), parts)
            except ValueError as e:
                raise ApiError(400, str(e))
            reply = int(fields["reply_to_id"]) if fields.get("reply_to_id") else None
            resp = live.grouped(cid, uid, EMP, fields.get("captions") or None, reply, items)
            broadcast_chat(cid, dict(resp, type="grouped_message", file_count=len(resp["files"]), reply_to_message=None))
            chats_updated(members)
            push_chat(cid, uid, resp.get("text"), len(resp["files"]))
            return resp
        if seg[0] == "chat" and len(seg) == 3 and seg[2] == "read" and method == "POST":
            return {"status": "ok"}
        if seg[0] == "chat" and len(seg) == 4 and seg[1] == "message" and seg[3] == "reaction" and method == "POST":
            cid, m = live.find_message(int(seg[2])) if seg[2].isdigit() else (None, None)
            if m is None:
                raise ApiError(404, "Сообщение не найдено")
            if uid not in chat_members(cid):
                raise ApiError(403, "Вы не участник этого чата")
            reactions, detailed = live.react(m["id"], uid, str((self.body_json() or {}).get("reaction_type") or ""), EMP)
            broadcast_chat(cid, {"type": "reaction", "message_id": m["id"], "reactions": reactions,
                                 "reactions_detailed": detailed, "chat_id": cid, "user_id": uid})
            return {"status": "success", "reactions": reactions, "reactions_detailed": detailed}
        if seg[0] == "chat" and len(seg) == 3 and seg[1] == "download" and seg[2].isdigit():
            cid, m = live.find_message(int(seg[2]))
            if m is None or uid not in chat_members(cid) or not m["files"]:
                raise ApiError(404, "Файл не найден")
            blob = live.blob_for_storage(m["files"][0]["path"])
            if blob is None:
                raise ApiError(404, "Файл не найден")
            self.send_blob(blob, attachment=True)
            return None
        if seg[0] == "chat" and len(seg) >= 2 and seg[-1] in ("read-state", "summary"):
            return {}
        # служебное
        if seg[0] in ("health", "admin"):
            raise ApiError(403, "Недоступно на тестовом сервере")
        if method != "GET":
            raise ApiError(405, "Тестовый сервер: изменения не сохраняются")
        raise ApiError(404, "Нет такого адреса на тестовом сервере: /" + path)


def make_server(host="127.0.0.1", port=8000, verbose=False):
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    srv.verbose = verbose
    return srv


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1", help="адрес (0.0.0.0 — доступ из локальной сети)")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--verbose", action="store_true", help="печатать каждый запрос")
    a = ap.parse_args()
    srv = make_server(a.host, a.port, a.verbose)
    print("Тестовый сервер: http://%s:%d/app/  (логины: %s; пароль: %s)" % (a.host, a.port, ", ".join(ROLE_USER), PASSWORD))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
