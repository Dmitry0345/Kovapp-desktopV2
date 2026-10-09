import subprocess
import time
import uuid

from kovtest import case
from kovtest.config import env

MAIN_DB = "kovcheg"
CHAT_DB = "kovcheg_messenger"
COMPRESSED = {MAIN_DB: ("notifications", "task_change_log", "warehouse_cell_log", "equipment_log", "warehouse_issues",
                        "equipment_issues", "task_stage_text_spans", "task_files", "task_stage_files", "project_files"),
              CHAT_DB: ("messages", "message_reactions")}


def connect(t):
    host = env("DB_STATUS_HOST", "")
    if not host or t.settings.mode != "full":
        t.skip("база доступна тестам только в полном режиме (сервер в Docker)")
    import pymysql
    return pymysql.connect(host=host, user="root", password=env("DB_STATUS_PASSWORD", ""), connect_timeout=10,
                           autocommit=True, charset="utf8mb4")


def options(conn, schema):
    with conn.cursor() as cur:
        cur.execute("SELECT TABLE_NAME, CREATE_OPTIONS FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'", (schema,))
        return {name: (opts or "") for name, opts in cur.fetchall()}


def compressed(opts):
    low = opts.lower().replace(" ", "")
    return "page_compressed" in low and "page_compressed='off'" not in low


def counts(conn, schema):
    out = {}
    with conn.cursor() as cur:
        for name in options(conn, schema):
            cur.execute("SELECT COUNT(*) FROM `%s`.`%s`" % (schema, name))
            out[name] = cur.fetchone()[0]
    return out


def send_marker(t, text):
    from kovtest.ws import Socket
    group = t.data.group_chat("director", ["worker_mont"], "Сжатые таблицы")
    sock = Socket(t.settings.base_url, t.api("director").token, t.log, who="director", quiet=True)
    try:
        time.sleep(1)
        sock.send({"type": "message", "chat_id": group["id"], "text": text, "client_id": "s" + uuid.uuid4().hex[:10]})
        deadline = time.time() + 15
        while time.time() < deadline:
            history = t.api("worker_mont").call("GET", "chat/history/%d" % group["id"])
            items = history.get("messages") if isinstance(history, dict) else history
            if any(text in str(m.get("text") or "") for m in items or []):
                return group
            time.sleep(0.5)
    finally:
        sock.close()
    t.require(False, "сообщение-метка не сохранилось в сжатой таблице сообщений")


@case("Таблицы с историей сжаты на диске средствами MariaDB", layer="api", order=1)
def tables_compressed(t):
    conn = connect(t)
    status = t.api("admin").call("POST", "admin/db-compression/run")
    t.check(status.get("enabled"), "сжатие таблиц выключено на сервере (DB_PAGE_COMPRESSION=0)")
    try:
        for schema, names in COMPRESSED.items():
            present = options(conn, schema)
            for name in names:
                if name in present:
                    t.check(compressed(present[name]), "таблица %s.%s не сжата: %s" % (schema, name, present[name] or "без параметров"))
    finally:
        conn.close()
    states = {k: v.get("state") for k, v in (status.get("tables") or {}).items()}
    t.log.info("состояние таблиц: %s; алгоритм %s" % (states, status.get("algorithm")))
    t.check(not status.get("errors"), "ошибки при сжатии: %s" % status.get("errors"))


@case("Сжатые таблицы читаются и пишутся как обычно", layer="api", order=2)
def compressed_tables_work(t):
    text = "проверка сжатой таблицы %s" % uuid.uuid4().hex[:8]
    send_marker(t, text)
    task = t.data.create_task("director", "Сжатые журналы", ["Монтажный отдел"], responsible=t.data.ids("head_mont"))
    t.check(task.get("id"), "задача не создалась: записи в сжатые журналы не прошли")
    notes = t.api("head_mont").call("GET", "employees/%d/notifications" % t.account("head_mont").id)
    t.check(isinstance(notes, (list, dict)), "уведомления не читаются из сжатой таблицы")


@case("Резервная копия сжатых таблиц проходит проверку и восстанавливается целиком", layer="api", order=3)
def backup_restores(t):
    conn = connect(t)
    admin = t.api("admin")
    marker = "метка резервной копии %s" % uuid.uuid4().hex[:8]
    send_marker(t, marker)
    restored = {MAIN_DB: MAIN_DB + "_rt", CHAT_DB: CHAT_DB + "_rt"}
    try:
        before = {schema: counts(conn, schema) for schema in COMPRESSED}
        started = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 2))
        answer = admin.call("POST", "admin/backup/run")
        t.check(answer.get("status") == "started", "резервное копирование не запустилось: %s" % answer)
        state = {}
        deadline = time.time() + 300
        while time.time() < deadline:
            state = admin.call("GET", "health/backup")
            run = state.get("last_run") or {}
            if run.get("finished_at") and str(run.get("started_at") or "") >= started:
                break
            time.sleep(2)
        run = state.get("last_run") or {}
        t.require(run.get("finished_at"), "резервное копирование не закончилось за 5 минут")
        t.require(run.get("status") == "ok", "резервное копирование с ошибкой: %s" % run)
        dump = state.get("last_dump") or {}
        t.require(dump.get("verified"), "дамп не прошёл проверку целостности: %s" % dump.get("verify"))
        t.log.info("дамп %s: %d КБ сжато, %s КБ исходно, сжатие в %s раз" % (
            dump.get("file"), (dump.get("size_compressed") or 0) // 1024, (dump.get("size_raw") or 0) // 1024, dump.get("ratio")))
        after = {schema: counts(conn, schema) for schema in COMPRESSED}
        with conn.cursor() as cur:
            for name in restored.values():
                cur.execute("DROP DATABASE IF EXISTS `%s`" % name)
        unpack = subprocess.Popen(["zstd", "-dc", dump["path"]], stdout=subprocess.PIPE)
        load = subprocess.Popen(["mariadb", "-h", env("DB_STATUS_HOST", "db"), "-u", "root",
                                 "-p" + env("DB_STATUS_PASSWORD", ""), "--default-character-set=utf8mb4"],
                                stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        for line in unpack.stdout:
            if line.startswith((b"CREATE DATABASE", b"USE `")):
                for source, target in restored.items():
                    line = line.replace(b"`%s`" % source.encode(), b"`%s`" % target.encode())
            load.stdin.write(line)
        load.stdin.close()
        error = load.stderr.read().decode("utf-8", "replace")
        t.require(unpack.wait() == 0 and load.wait() == 0, "дамп не восстановился: %s" % error[-600:])
        for schema, copy in restored.items():
            got = counts(conn, copy)
            t.check(set(got) == set(after[schema]), "в копии %s другой набор таблиц: %s" % (
                schema, sorted(set(after[schema]) ^ set(got))))
            for name, rows in got.items():
                low = min(before[schema].get(name, rows), after[schema].get(name, rows))
                high = max(before[schema].get(name, rows), after[schema].get(name, rows))
                t.check(low <= rows <= high, "%s.%s: в копии %d строк, в базе было %d–%d" % (schema, name, rows, low, high))
            copied = options(conn, copy)
            for name in COMPRESSED[schema]:
                if name in copied:
                    t.check(compressed(copied[name]), "после восстановления %s.%s не сжата" % (schema, name))
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM `%s`.`messages` WHERE text = %%s" % restored[CHAT_DB], (marker,))
            t.check(cur.fetchone()[0] == 1, "сообщение-метка не найдено в восстановленной копии")
    finally:
        try:
            with conn.cursor() as cur:
                for name in restored.values():
                    cur.execute("DROP DATABASE IF EXISTS `%s`" % name)
        finally:
            conn.close()
