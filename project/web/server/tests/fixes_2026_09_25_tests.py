# -*- coding: utf-8 -*-
"""Проверка серверных исправлений 25.09.2026 (docs/changes-2026-09-25.md).

Каждая проверка выполняет настоящую функцию из исходника сервера с
заглушками базы и FastAPI — поэтому тест работает и без установленных
библиотек. С ключом --original DIR те же сценарии прогоняются на старом
коде, чтобы убедиться, что тест ловит прежнюю ошибку.

    python -m server.tests.fixes_2026_09_25_tests [--original путь/к/старому/server]
"""

import ast
import asyncio
import logging
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.dirname(HERE)
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(("  OK   " if ok else "  FAIL ") + name + ("" if ok else "  — " + str(detail)[:300]))


def load_function(path, name, cls=None):
    """Исходный текст функции (или метода класса) — для выполнения с заглушками."""
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    nodes = tree.body
    if cls:
        nodes = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls).body
    node = next(n for n in nodes if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    code = ast.get_source_segment(src, node)
    pad = node.col_offset                   # отступ определения: у метода 4, у функции 0
    lines = code.split("\n")
    return "\n".join([lines[0]] + [l[pad:] if l[:pad].strip() == "" else l for l in lines[1:]])


class HTTPException(Exception):
    def __init__(self, status_code=500, detail=""):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


class Col:
    def __eq__(self, other):
        return True

    __hash__ = object.__hash__


class Query:
    def __init__(self, result):
        self.result = result

    def filter(self, *a):
        return self

    def first(self):
        return self.result


class LogCatcher(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


# ── 1. посторонний отправляет файлы в группу ────────────────────────────────
def test_group_upload(server_dir, label):
    path = os.path.join(server_dir, "server_app", "routers", "chat.py")
    finished = []
    queue = types.ModuleType("server.server_app.upload_queue")
    queue.finish = lambda uid: finished.append(uid)
    queue.start = lambda *a, **k: None
    sys.modules["server.server_app.upload_queue"] = queue
    taken = []
    intake = types.ModuleType("server.server_app.intake")

    async def as_uploads(items, user_id, db):
        taken.append(items)                      # «приём файлов»: забираются сессии докачки
        return list(items)
    intake.as_uploads = as_uploads
    intake.parse_items = lambda files, thumbs, meta: ["сессия-1"]
    intake.release_uploads = lambda files: None
    sys.modules["server.server_app.intake"] = intake
    ns = {"__package__": "server.server_app.routers", "__name__": "server.server_app.routers.chat",
          "HTTPException": HTTPException, "logger": logging.getLogger("t"), "traceback": __import__("traceback"),
          "ChatParticipantModel": types.SimpleNamespace(chat_id=Col(), user_id=Col()), "json": __import__("json"),
          "Optional": __import__("typing").Optional, "List": __import__("typing").List, "UploadFile": object, "BackgroundTasks": object, "Session": object,
          "File": lambda *a, **k: None, "Form": lambda *a, **k: None, "Depends": lambda *a, **k: None,
          "get_current_user_id": None, "get_chat_db": None, "get_db": None}
    exec(load_function(path, "_create_grouped_message"), ns)
    exec(load_function(path, "create_grouped_message"), ns)
    db = types.SimpleNamespace(query=lambda *a: Query(None), rollback=lambda: None)
    try:
        asyncio.run(ns["create_grouped_message"](None, 12, [], None, None, None, None, "up1", None, 99, db, db))
        outcome = "ответ без отказа"
    except HTTPException as e:
        outcome = "HTTP %s" % e.status_code
    except Exception as e:
        outcome = type(e).__name__
    check("%s: посторонний в группе получает 403, а не 500" % label, outcome == "HTTP 403", outcome)
    check("%s: у постороннего файлы не принимаются (сессии докачки не забраны)" % label, not taken, taken)


# ── 4. обрыв связи посреди куска ─────────────────────────────────────────────
def test_chunk_disconnect(server_dir, label):
    path = os.path.join(server_dir, "server_app", "routers", "uploads.py")
    src = open(path, encoding="utf-8").read()

    class ClientDisconnect(Exception):
        pass

    class Request:
        headers = {}

        async def stream(self):
            yield b"12345"
            raise ClientDisconnect()

    catcher = LogCatcher()
    log = logging.getLogger("fixes.uploads")
    log.handlers = [catcher]
    log.setLevel(logging.DEBUG)
    ns = {"HTTPException": HTTPException, "logger": log, "Request": Request, "Depends": lambda *a, **k: None,
          "ClientDisconnect": ClientDisconnect,
          "get_current_user_id": None, "ChunkError": type("ChunkError", (Exception,), {}),
          "intake": types.SimpleNamespace(chunk_store=lambda: types.SimpleNamespace(chunk_max=1 << 20)),
          "_fail": lambda e: None}
    if "def _is_disconnect" in src:
        exec(load_function(path, "_is_disconnect"), ns)
    exec(load_function(path, "session_append"), ns)
    try:
        asyncio.run(ns["session_append"]("sid1", 0, Request(), 5))
        outcome = "без ошибки"
    except HTTPException as e:
        outcome = "HTTP %s" % e.status_code
    except Exception as e:
        outcome = "авария %s" % type(e).__name__
    calm = [r for r in catcher.records if r.levelno == logging.INFO and not r.exc_info]
    loud = [r for r in catcher.records if r.levelno >= logging.ERROR]
    check("%s: обрыв посреди куска — спокойный ответ клиенту, не авария" % label, outcome == "HTTP 400", outcome)
    check("%s: в журнале одна спокойная строка без трассировки" % label, len(calm) == 1 and not loud,
          [r.getMessage() for r in catcher.records])


# ── 5. удаление задачи: этапы удаляются один раз ─────────────────────────────
def test_task_delete(server_dir, label):
    code = load_function(os.path.join(server_dir, "server_app", "routers", "tasks.py"), "delete_task")
    import re
    bulk = code.find("db.query(TaskStageDetails).filter(TaskStageDetails.task_id == task_id).delete(")
    expire = code.find('db.expire(task, ["stage_details"])')
    m = re.search(r"^\s+db\.delete\(task\)\s*$", code, re.M)     # строка кода, не упоминание в комментарии
    final = m.start() if m else -1
    ok = bulk >= 0 and bulk < expire < final
    check("%s: после массового удаления этапов коллекция сброшена до db.delete(task)" % label, ok,
          {"массовое удаление": bulk, "сброс": expire, "удаление задачи": final})


# ── 6. закрытый сокет в журнале ──────────────────────────────────────────────
def test_socket_log(server_dir, label):
    path = os.path.join(server_dir, "server_app", "realtime.py")
    catcher = LogCatcher()
    log = logging.getLogger("fixes.realtime")
    log.handlers = [catcher]
    log.setLevel(logging.DEBUG)
    ns = {"logger": log}
    exec(load_function(path, "send_personal_message", cls="ConnectionManager"), ns)

    class Socket:
        client_state = types.SimpleNamespace(value=1)
        application_state = types.SimpleNamespace(value=1)

        async def send_json(self, message):
            raise RuntimeError('Cannot call "send" once a close message has been sent.')

    dropped = []
    me = types.SimpleNamespace(_lock=asyncio.Lock(), user_connections={7: [Socket()]},
                               _drop_socket_locked=lambda ws, uid: dropped.append(uid))
    asyncio.run(ns["send_personal_message"](me, {"type": "x"}, 7))
    msgs = [(r.levelname, r.getMessage()) for r in catcher.records]
    ok = any(level == "INFO" and m.startswith("Сокет пользователя 7 закрыт: RuntimeError") for level, m in msgs) \
        and not any(level == "ERROR" for level, m in msgs)
    check("%s: закрытый сокет — обычное событие «Сокет пользователя N закрыт: <тип>»" % label, ok and dropped == [7], msgs)


def main():
    orig = None
    if "--original" in sys.argv:
        orig = sys.argv[sys.argv.index("--original") + 1]
    print("Исправления 25.09.2026 — сервер")
    for server_dir, label in [(SERVER, "исправленный")] + ([(orig, "исходный")] if orig else []):
        for test in (test_group_upload, test_chunk_disconnect, test_task_delete, test_socket_log):
            try:
                test(server_dir, label)
            except Exception as e:
                check("%s: %s" % (label, test.__name__), False, "%s: %s" % (type(e).__name__, e))
    failed = [n for n, ok in RESULTS if not ok and not n.startswith("исходный")]
    print("Итого по исправленному коду: %d проверок, ошибок: %d" % (sum(1 for n, _ in RESULTS if n.startswith("исправленный")), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
