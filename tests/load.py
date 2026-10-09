import json
import random
import statistics
import threading
import time
import uuid

from kovtest import case
from kovtest.api import Api
from kovtest.config import env, env_int
from kovtest.media import device_photo, device_video, raw_video, uniquify
from kovtest.ws import Socket

USERS = env_int("LOAD_USERS", 50)
CHAT_SECONDS = env_int("LOAD_CHAT_SECONDS", 60)
CHAT_P95_MS = env_int("LOAD_CHAT_P95_MS", 1500)
FILES_PER_USER = env_int("LOAD_FILES_PER_USER", 2)
RAW_SHARE = max(0.0, min(1.0, env_int("LOAD_RAW_PERCENT", 10) / 100.0))
UPLOAD_P95_S = env_int("LOAD_UPLOAD_P95_S", 45)
SMALL_P95_S = env_int("LOAD_SMALL_P95_S", 5)
VIDEO_P95_S = env_int("LOAD_VIDEO_P95_S", 20)
API_P95_MS = env_int("LOAD_API_P95_MS", 2500)
CONNECT_P95_MS = env_int("LOAD_CONNECT_P95_MS", 3000)
LOOP_LAG_P95_MS = env_int("LOAD_LOOP_LAG_P95_MS", 500)
RAM_MIN_FREE_PERCENT = env_int("LOAD_RAM_MIN_FREE_PERCENT", 10)


# ===== участники нагрузки =====

def load_users(t):
    if "load_users" in t.data.cache:
        return t.data.cache["load_users"]
    if t.settings.mode != "full" and env("LOAD_ALLOW_EXTERNAL", "0") != "1":
        t.skip("нагрузочная проверка идёт только на сервере в Docker; для внешнего нужен LOAD_ALLOW_EXTERNAL=1")
    password = env("FULL_ACCOUNT_PASSWORD", "Autotest-2026!") if t.settings.mode == "full" else env("LOAD_PASSWORD", "")
    users = []
    for n in range(1, USERS + 1):
        login = "at_load%02d" % n
        api = Api(t.settings.base_url, t.log, who=login)
        try:
            api.login(login, password)
        except Exception as e:
            t.skip("нет учётной записи %s — сервер в Docker заводит их сам по LOAD_USERS (%s)" % (login, e))
        users.append((login, api))
    t.data.cache["load_users"] = users
    return users


def percentile(values, share):
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(share * (len(ordered) - 1))))]


def describe(values, unit=1.0):
    if not values:
        return "нет данных"
    return "медиана %.2f, 95%% %.2f, максимум %.2f" % (statistics.median(values) * unit, percentile(values, 0.95) * unit,
                                                       max(values) * unit)


def groups_of(t, users, title, size=5):
    director = t.api("director")
    out = []
    for start in range(0, len(users), size):
        members = users[start:start + size]
        chat = director.call("POST", "chat/group", json={"name": t.data.name("%s %d" % (title, start // size + 1))})
        t.data.groups[chat["id"]] = "director"
        director.call("POST", "chat/group/%d/participants" % chat["id"], json={"user_ids": [api.user_id for _, api in members]})
        out.append((chat["id"], members))
    return out


# ===== замер процессора и памяти сервера =====

def db_connection():
    host = env("DB_STATUS_HOST", "")
    if not host:
        return None
    try:
        import pymysql
        return pymysql.connect(host=host, user="root", password=env("DB_STATUS_PASSWORD", ""), connect_timeout=5,
                               autocommit=True)
    except Exception:
        return None


DB_KEYS = ("Questions", "Threads_running", "Threads_connected", "Slow_queries",
           "Innodb_buffer_pool_reads", "Innodb_buffer_pool_read_requests")


def db_status(conn):
    with conn.cursor() as cur:
        cur.execute("SHOW GLOBAL STATUS WHERE Variable_name IN (%s)" % ",".join("'%s'" % k for k in DB_KEYS))
        return {name: int(value) for name, value in cur.fetchall()}


class Monitor:
    def __init__(self, t):
        self.t = t
        self.api = t.api("admin")
        self.samples = []
        self.db = []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.db_thread = threading.Thread(target=self.run_db, daemon=True)

    def __enter__(self):
        self.thread.start()
        self.db_thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join(timeout=5)
        self.db_thread.join(timeout=5)

    def run_db(self):
        conn = db_connection()
        if conn is None:
            return
        try:
            while not self.stop.is_set():
                try:
                    self.db.append((time.time(), db_status(conn)))
                except Exception:
                    break
                self.stop.wait(1.0)
        finally:
            conn.close()

    def db_report(self, what):
        if len(self.db) < 2:
            return
        (t0, first), (t1, last) = self.db[0], self.db[-1]
        span = max(1e-6, t1 - t0)
        qps = [(b["Questions"] - a["Questions"]) / max(1e-6, tb - ta) for (ta, a), (tb, b) in zip(self.db, self.db[1:])]
        reads = last["Innodb_buffer_pool_reads"] - first["Innodb_buffer_pool_reads"]
        requests = last["Innodb_buffer_pool_read_requests"] - first["Innodb_buffer_pool_read_requests"]
        self.t.log.info("%s — база: запросов в секунду в среднем %.0f, пик %.0f; одновременно выполнялось до %d; соединений до %d; "
                        "медленных запросов (дольше 0,5 с) %d; чтений с диска %.2f%% от обращений; замеров %d" % (
                            what, (last["Questions"] - first["Questions"]) / span, max(qps) if qps else 0,
                            max(s["Threads_running"] for _, s in self.db), max(s["Threads_connected"] for _, s in self.db),
                            last["Slow_queries"] - first["Slow_queries"], 100.0 * reads / requests if requests else 0.0,
                            len(self.db)))

    def run(self):
        while not self.stop.is_set():
            try:
                response = self.api.get("health/load", timeout=10)
                if response.status_code == 200:
                    self.samples.append(response.json())
            except Exception:
                pass
            self.stop.wait(1.0)

    def report(self, what):
        self.db_report(what)
        s = [x for x in self.samples if x.get("measured", True)]
        if not s:
            self.t.warn("%s: сервер не отдал замеры /health/load" % what)
            return
        total = max(x.get("ram_total_mb") or 0 for x in s)
        free = [x.get("ram_available_mb") for x in s if x.get("ram_available_mb") is not None]
        busy = [float(x.get("cpu_busy") or 0) for x in s]
        lag = [float(x.get("loop_lag_ms") or 0) for x in s]
        waiting = [sum((x.get("waiting") or {}).values()) for x in s]
        active = [sum((x.get("active") or {}).values()) for x in s]
        used = [total - f for f in free] if total and free and max(free) <= total else []
        heavy = [x.get("heavy") for x in s if isinstance(x.get("heavy"), dict)]
        if heavy:
            self.t.log.info("%s — мелкие загрузки: одновременно до %d из %d" % (
                what, max(h.get("small_active", 0) for h in heavy), heavy[-1].get("small_slots", 0)))
            self.t.log.info("%s — очередь тяжёлых запросов: одновременно до %d, предел менялся от %d до %d (потолок %d), "
                            "ждали в очереди до %d, дольше всех ждал %.1f с; лёгкие запросы: 95%% за %s мс; последнее решение: %s" % (
                                what, max(h.get("active", 0) for h in heavy), min(h.get("limit", 0) for h in heavy),
                                max(h.get("limit", 0) for h in heavy), heavy[-1].get("cap", 0),
                                max(h.get("waiting", 0) for h in heavy), max(h.get("max_wait_s", 0) for h in heavy),
                                max((h.get("light_p95_ms") or 0) for h in heavy), heavy[-1].get("reason")))
        self.t.log.info("%s — сервер: ядер %s, память %s МБ; процессор занят: в среднем %.0f%%, пик %.0f%%; "
                        "память занята: пик %s МБ (свободно минимум %s МБ); задержка обработки запросов: 95%% %.0f мс, "
                        "максимум %.0f мс; обработка файлов: одновременно до %d, в очереди до %d; замеров %d" % (
                            what, s[-1].get("cores"), total, 100 * statistics.mean(busy), 100 * max(busy),
                            max(used) if used else "?", min(free) if free else "?", percentile(lag, 0.95), max(lag),
                            max(active), max(waiting), len(s)))
        self.t.check(percentile(lag, 0.95) <= LOOP_LAG_P95_MS,
                     "%s: сервер отвечал с задержкой %.0f мс у 95%% замеров, допустимо %d мс" % (what, percentile(lag, 0.95), LOOP_LAG_P95_MS))
        if total and free and max(free) <= total:
            self.t.check(min(free) >= total * RAM_MIN_FREE_PERCENT / 100.0,
                         "%s: свободной памяти оставалось всего %d МБ из %d" % (what, min(free), total))

    def summary(self) -> dict:
        """Показатели сервера и базы за окно замера — числами, для сравнения ступеней."""
        out = {}
        s = [x for x in self.samples if x.get("measured", True)]
        if s:
            total = max(x.get("ram_total_mb") or 0 for x in s)
            free = [x.get("ram_available_mb") for x in s if x.get("ram_available_mb") is not None]
            busy = [float(x.get("cpu_busy") or 0) for x in s]
            lag = [float(x.get("loop_lag_ms") or 0) for x in s]
            out.update(cpu_avg=round(100 * statistics.mean(busy)), cpu_peak=round(100 * max(busy)),
                       ram_total_mb=total or None, ram_min_free_mb=min(free) if free else None,
                       loop_lag_p95=round(percentile(lag, 0.95)), loop_lag_max=round(max(lag)), samples=len(s))
            heavy = [x.get("heavy") for x in s if isinstance(x.get("heavy"), dict)]
            if heavy:
                out.update(heavy_active_max=max(h.get("active", 0) for h in heavy), heavy_waiting_max=max(h.get("waiting", 0) for h in heavy),
                           heavy_wait_max_s=max(h.get("max_wait_s", 0) for h in heavy))
        if len(self.db) >= 2:
            (t0, first), (t1, last) = self.db[0], self.db[-1]
            out.update(db_qps=round((last["Questions"] - first["Questions"]) / max(1e-6, t1 - t0)),
                       db_running_max=max(x["Threads_running"] for _, x in self.db),
                       db_slow=last["Slow_queries"] - first["Slow_queries"])
        return out


def same_preview(a, b):
    a, b = (a or "").rstrip(".").strip(), (b or "").rstrip(".").strip()
    return a.startswith(b) or b.startswith(a)


# ===== пределы сервера по мощности машины =====

def parse_mb(text):
    text = str(text or "").strip().lower()
    units = {"k": 1 / 1024.0, "m": 1.0, "g": 1024.0}
    if text and text[-1] in units:
        return float(text[:-1]) * units[text[-1]]
    return float(text or 0) / (1024 * 1024)


def expected_plan(cores, ram_mb):
    safety = min(1.0, max(0.3, float(env("LIMITS_SAFETY", "0.8") or 0.8)))
    reserve = min(max(ram_mb * 0.2, 768), 2048)
    usable = max(256.0, ram_mb - reserve)
    threads = max(16, int(safety * min(cores * 32, usable / 4)))
    heavy = max(2, int(safety * min(cores * 4, usable / 32)))
    pool = max(int(env("DB_POOL_MIN", "100") or 100), threads + heavy + 16)
    return {"threads": threads, "heavy": heavy, "budget_mb": max(32, int(safety * usable * 0.15)),
            "db_pool": pool, "db_overflow": max(20, pool // 5)}


@case("Сервер посчитал пределы по мощности рабочей машины, а не компьютера с тестами", layer="api", order=0)
def capacity_plan(t):
    cpus, memory = env("LOAD_EXPECT_CPUS", ""), env("LOAD_EXPECT_MEMORY", "")
    if not cpus or not memory:
        t.skip("сервер запущен без ограничений мощности — эта проверка идёт в run.cmd load")
    data = t.api("admin").call("GET", "health/load")
    plan = data.get("plan") or {}
    cores, ram = max(1, int(float(cpus))), parse_mb(memory)
    t.log.info("сервер видит: ядер %s, память %s МБ; пределы: %s" % (plan.get("cores"), plan.get("ram_mb"), plan))
    t.check(plan.get("cores") == cores, "сервер видит %s ядер вместо %d — считает по компьютеру с тестами" % (plan.get("cores"), cores))
    t.check(abs((plan.get("ram_mb") or 0) - ram) <= ram * 0.03,
            "сервер видит %s МБ памяти вместо %.0f — считает по компьютеру с тестами" % (plan.get("ram_mb"), ram))
    want = expected_plan(cores, ram)
    max_conn = None
    conn = db_connection()
    if conn is not None:
        try:
            with conn.cursor() as cur:
                cur.execute("SHOW VARIABLES LIKE 'max_connections'")
                max_conn = int(cur.fetchone()[1])
        finally:
            conn.close()
    if max_conn is not None and want["db_pool"] + want["db_overflow"] > (max_conn - 20) // 2:
        allowed = max(10, (max_conn - 20) // 2)
        want["db_overflow"] = max(5, allowed // 6)
        want["db_pool"] = max(5, allowed - want["db_overflow"])
    want["threads"] = min(want["threads"], want["db_pool"] + want["db_overflow"])
    for key in ("threads", "heavy", "budget_mb", "db_pool", "db_overflow"):
        t.check(plan.get(key) == want[key], "предел %s: сервер посчитал %s, по формуле %s" % (key, plan.get(key), want[key]))
    t.check(data.get("threads_applied") == plan.get("threads"),
            "пул потоков не применён: действует %s вместо %s" % (data.get("threads_applied"), plan.get("threads")))
    pools = t.api("admin").call("GET", "health/db-pool")
    for name in ("main_db", "chat_db"):
        pool = pools.get(name) or {}
        t.check(pool.get("pool_size") == plan.get("db_pool") and pool.get("max_overflow") == plan.get("db_overflow"),
                "пул %s: действует %s + %s вместо %s + %s" % (name, pool.get("pool_size"), pool.get("max_overflow"),
                                                           plan.get("db_pool"), plan.get("db_overflow")))
    if max_conn is not None:
        t.check(max_conn >= 2 * (plan.get("db_pool", 0) + plan.get("db_overflow", 0)) + 20,
                "MariaDB разрешает %d соединений — пулы сервера туда не помещаются" % max_conn)


# ===== одновременное подключение и бурная переписка =====

# ===== файлы с телефонов: почти все сжаты на устройстве =====

def payloads(t):
    if "load_media" not in t.data.cache:
        photo, photo_thumb, photo_size = device_photo()
        video, video_thumb, video_size, video_len = device_video()
        t.data.cache["load_media"] = {
            "photo": (photo, photo_thumb, photo_size), "video": (video, video_thumb, video_size, video_len),
            "raw_video": raw_video(),
        }
    return t.data.cache["load_media"]


def item_for(t, number, login):
    media = payloads(t)
    raw = random.random() < RAW_SHARE
    kind = ("photo", "video", "document")[number % 3]
    if kind == "photo":
        if raw:
            name, data, ctype = t.world.media.fresh("camera")
            return "photo-raw", name, data, ctype, {"prep": "original"}, None
        data, thumb, size = media["photo"]
        return "photo", "%s-photo.webp" % login, uniquify("x.webp", data), "image/webp", \
            {"prep": "image", "width": size[0], "height": size[1]}, thumb
    if kind == "video":
        if raw and media["raw_video"]:
            return "video-raw", "%s-camera.mp4" % login, uniquify("x.mp4", media["raw_video"]), "video/mp4", {"prep": "original"}, None
        data, thumb, size, length = media["video"]
        return "video", "%s-clip.mp4" % login, uniquify("x.mp4", data), "video/mp4", \
            {"prep": "device", "width": size[0], "height": size[1], "duration": length}, thumb
    name, data, ctype = t.world.media.fresh(("pdf5", "docx", "xlsx")[number % 3])
    return "document", "%s-%s" % (login, name), data, ctype, {"prep": "original"}, None


def send(api, path, fields, name, data, ctype, meta, thumb):
    item = dict({"src": "file", "file": 0, "name": name, "size": len(data)}, **meta)
    files = [("files", (name, data, ctype))]
    if thumb:
        item["thumb"] = 0
        files.append(("thumbs", ("thumb.jpg", thumb, "image/jpeg")))
    return api.post(path, data=dict(fields, meta=json.dumps([item])), files=files, timeout=900)


# ===== нагрузка по ступеням: от LOAD_USERS человек до LOAD_USERS_MIN =====

USERS_MIN = max(1, env_int("LOAD_USERS_MIN", 2))
WARMUP_S = env_int("LOAD_WARMUP_S", 30)
STEP_S = env_int("LOAD_STEP_S", 180)
COOLDOWN_S = env_int("LOAD_COOLDOWN_S", 60)
PHONE_SHARE = max(0.0, min(1.0, env_int("LOAD_PHONE_PERCENT", 30) / 100.0))
BROWSER_PROBE = env("LOAD_BROWSER_PROBE", "1") != "0"
PAGE_P95_MS = env_int("LOAD_PAGE_P95_MS", 4000)
LIST_PATHS = {"задачи": "tasks/light?is_archived=false", "проекты": "projects", "уведомления": None,
              "чаты": "chat/list", "история чата": None, "задача": None}
PROBE_ROUTES = (("задачи", "#/tasks"), ("проекты", "#/projects"), ("мессенджер", "#/messenger"),
                ("уведомления", "#/notifications"), ("задача", None))


def steps_plan():
    """Ступени по убыванию: LOAD_STEPS или 50, 40, 30, 20, 10, 5, 2 в пределах LOAD_USERS…LOAD_USERS_MIN."""
    raw = [x.strip() for x in env("LOAD_STEPS", "").split(",") if x.strip()]
    if raw:
        values = [int(x) for x in raw if x.isdigit()]
    else:
        values = [USERS, 40, 30, 20, 10, 5, USERS_MIN]
    values = sorted({v for v in values if USERS_MIN <= v <= USERS}, reverse=True)
    return values or [USERS]


class _Quiet:
    """Журнал для сотен однотипных событий: в full.log они не пишутся."""
    def info(self, *a, **k): pass
    def warn(self, *a, **k): pass
    def debug(self, *a, **k): pass
    def error(self, *a, **k): pass


def phone_channel(t, login, api):
    """«Телефон без Google» сотрудника: собственный канал уведомлений приложения; время прихода каждого уведомления."""
    from kovtest.native_channel import NativeDevice

    class Timed(NativeDevice):
        def _handle(self, text):
            try:
                msg = json.loads(text)
                if msg.get("type") == "push":
                    with self._lock:
                        self.stamps.append((time.time(), msg.get("payload") or {}))
            except Exception:
                pass
            super()._handle(text)

    key = "load_phone_%s" % login
    dev = t.data.cache.get(key)
    if dev is None:
        dev = Timed(api, _Quiet(), t.settings.base_url, who=login)
        dev.stamps = []
        dev.register()
        t.data.cache[key] = dev
    return dev


def summarize(values, unit=1.0):
    if not values:
        return None
    return {"n": len(values), "p50": round(statistics.median(values) * unit, 1),
            "p95": round(percentile(values, 0.95) * unit, 1), "max": round(max(values) * unit, 1)}


def run_step(t, n, users, groups, task, stage):
    """Одна ступень: n человек переписываются, листают списки, шлют файлы; часть — «с телефонов»."""
    active = users[:n]
    logins = {login for login, _ in active}
    lock = threading.Lock()
    measuring = threading.Event()
    stop = threading.Event()
    out = {"users": n, "errors": [], "lists": {}, "lists_server": {}, "uploads": {}, "pages": {}}
    connect_times, sockets, phones = [], {}, {}
    sent, list_times, server_times, upload_times = {}, {}, {}, {}
    not_modified = [0, 0]
    chat_of = {login: chat_id for chat_id, members in groups for login, _ in members}

    def connect(login, api):
        began = time.time()
        try:
            sock = Socket(t.settings.base_url, api.token, t.log, who=login, quiet=True)
            with lock:
                sockets[login] = sock
                connect_times.append(time.time() - began)
        except Exception as e:
            with lock:
                out["errors"].append("%s: сокет переписки не открылся: %s" % (login, e))

    phone_count = int(round(n * PHONE_SHARE))
    for login, api in active[:phone_count]:
        try:
            dev = phone_channel(t, login, api)
            with dev._lock:
                dev.stamps.clear()
            if dev.connect():
                phones[login] = dev
            else:
                out["errors"].append("%s: канал уведомлений приложения не принят" % login)
        except Exception as e:
            out["errors"].append("%s: канал уведомлений приложения: %s" % (login, e))
    threads = [threading.Thread(target=connect, args=(login, api), daemon=True) for login, api in active]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=60)

    def talker(login, sock):
        number = 0
        chat_id = chat_of.get(login)
        while not stop.is_set():
            mark = "%d-%s-%d" % (n, login, number)
            number += 1
            if measuring.is_set():
                with lock:
                    sent[mark] = (chat_id, login, time.time())
            try:
                sock.send({"type": "message", "chat_id": chat_id, "text": "нагрузка " + mark,
                           "client_id": "c" + uuid.uuid4().hex[:10]})
            except Exception as e:
                with lock:
                    out["errors"].append("%s: сообщение не отправлено: %s" % (login, e))
            stop.wait(random.uniform(1.0, 2.5))

    def reader(login, api):
        own = {"задачи": LIST_PATHS["задачи"], "проекты": "projects", "уведомления": "employees/%d/notifications" % api.user_id,
               "чаты": "chat/list", "история чата": "chat/history/%d" % chat_of.get(login), "задача": "tasks/%d" % task["id"]}
        tags = {}
        while not stop.is_set():
            name = random.choice(list(own))
            path = own[name]
            began = time.time()
            status, server_ms = 0, None
            try:
                headers = {"If-None-Match": tags[path]} if path in tags else {}
                response = api.get(path, timeout=60, headers=headers)
                status = response.status_code
                if response.headers.get("ETag"):
                    tags[path] = response.headers["ETag"]
                timing = response.headers.get("Server-Timing") or ""
                if "dur=" in timing:
                    server_ms = float(timing.split("dur=")[1].split(";")[0].split(",")[0])
            except Exception:
                status = 0
            spent = time.time() - began
            if measuring.is_set():
                with lock:
                    list_times.setdefault(name, []).append(spent)
                    if server_ms is not None:
                        server_times.setdefault(name, []).append(server_ms / 1000.0)
                    not_modified[1] += 1
                    if status == 304:
                        not_modified[0] += 1
                    if status >= 500 or status == 0:
                        out["errors"].append("%s: GET %s → %s" % (login, path, status))
            stop.wait(random.uniform(2.0, 4.0))

    def uploader(index, login, api):
        # файлы — в случайные моменты окна замера, одинаково на каждой ступени
        moments = sorted(random.uniform(0, max(1.0, STEP_S - 20)) for _ in range(FILES_PER_USER))
        measuring.wait(WARMUP_S + 60)
        begin = time.time()
        for number, at in enumerate(moments):
            if stop.wait(max(0.0, begin + at - time.time())):
                return
            kind, name, data, ctype, meta, thumb = item_for(t, index + number, login)
            began = time.time()
            try:
                response = send(api, "upload", {"task_id": str(task["id"]), "stage_detail_id": str(stage["id"])},
                                name, data, ctype, meta, thumb)
                ok = response.status_code == 200
                detail = "" if ok else "%s %s" % (response.status_code, response.text[:120])
            except Exception as e:
                ok, detail = False, str(e)[:160]
            with lock:
                if ok:
                    upload_times.setdefault(kind, []).append(time.time() - began)
                else:
                    out["errors"].append("%s: файл (%s) не принят: %s" % (login, kind, detail))

    workers = [threading.Thread(target=talker, args=(login, sock), daemon=True) for login, sock in sockets.items()]
    workers += [threading.Thread(target=reader, args=(login, api), daemon=True) for login, api in active]
    workers += [threading.Thread(target=uploader, args=(i, login, api), daemon=True) for i, (login, api) in enumerate(active)]
    probe_page = t.data.cache.get("load_probe_page")
    with Monitor(t) as monitor:
        for th in workers:
            th.start()
        time.sleep(WARMUP_S)
        monitor.samples.clear()
        monitor.db.clear()
        measuring.set()
        window_end = time.time() + STEP_S
        # живой браузер в основном потоке: время открытия страниц глазами человека
        while time.time() < window_end:
            if probe_page is not None:
                for name, route in PROBE_ROUTES:
                    if time.time() >= window_end:
                        break
                    route = route or "#/tasks/%d" % task["id"]
                    began = time.time()
                    try:
                        probe_page.goto(route, wait=True)
                        out["pages"].setdefault(name, []).append(max(0.0, time.time() - began - 0.25))
                    except Exception as e:
                        out["errors"].append("браузер: страница %s не открылась: %s" % (route, str(e)[:120]))
                    time.sleep(1.0)
            else:
                time.sleep(1.0)
        measuring.clear()
        stop.set()
        for th in workers:
            th.join(timeout=120)
        # доставка: ждём, пока поток событий затихнет (не дольше 60 с)
        quiet, last, deadline = time.time(), -1, time.time() + 60
        while time.time() < deadline and time.time() - quiet < 5:
            count = sum(len(s.events) for s in sockets.values())
            if count != last:
                last, quiet = count, time.time()
            time.sleep(1)
        out["server"] = monitor.summary()
    # задержка доставки сообщений по WebSocket: от отправки до получения каждым участником чата
    latencies, delivered, expected = [], set(), 0
    for mark, (chat_id, sender, at) in sent.items():
        expected += sum(1 for login in sockets if chat_of.get(login) == chat_id and login != sender)
    for login, sock in sockets.items():
        with sock._lock:
            events = list(sock.events)
        for stamp, event in events:
            if not isinstance(event, dict) or event.get("type") != "message":
                continue
            text = event.get("text") or ""
            mark = text[len("нагрузка "):] if text.startswith("нагрузка ") else ""
            origin = sent.get(mark)
            if not origin or origin[1] == login or (login, mark) in delivered:
                continue
            delivered.add((login, mark))
            latencies.append(stamp - origin[2])
    # уведомления «телефонам» по собственному каналу приложения
    push_lat = []
    for login, dev in phones.items():
        with dev._lock:
            stamps = list(dev.stamps)
        for stamp, payload in stamps:
            body = str(payload.get("body") or "")
            for mark, (chat_id, sender, at) in list(sent.items()):
                if mark in body and sender != login:
                    push_lat.append(stamp - at)
                    break
        try:
            dev.close()
        except Exception:
            pass
    for sock in sockets.values():
        try:
            sock.close()
        except Exception:
            pass
    out["connect"] = summarize(connect_times, 1000)
    out["chat"] = summarize(latencies, 1000)
    out["chat_lost"] = max(0, expected - len(delivered))
    out["chat_expected"] = expected
    out["push"] = summarize(push_lat, 1000)
    out["phones"] = len(phones)
    out["lists"] = {k: summarize(v, 1000) for k, v in list_times.items()}
    out["lists_server"] = {k: summarize(v, 1000) for k, v in server_times.items()}
    out["not_modified"] = not_modified[0]
    out["list_requests"] = not_modified[1]
    out["uploads"] = {k: summarize(v) for k, v in upload_times.items()}
    out["pages"] = {k: summarize(v, 1000) for k, v in out["pages"].items()}
    return out


def cooldown(t, last_cpu=None):
    """Пауза между ступенями: очередь загрузок пуста и процессор отдыхает (не дольше LOAD_COOLDOWN_S)."""
    api, deadline = t.api("admin"), time.time() + COOLDOWN_S
    while time.time() < deadline:
        try:
            s = api.get("health/load", timeout=10).json()
            heavy = s.get("heavy") if isinstance(s.get("heavy"), dict) else {}
            if float(s.get("cpu_busy") or 0) < 0.3 and not heavy.get("waiting") and not heavy.get("active"):
                return
        except Exception:
            pass
        time.sleep(3)


# ===== отчёт: таблица и графики =====

def _svg_chart(title, unit, series, xs):
    """Линейный график без библиотек: x — число людей, y — время; series = {название: [значение по x]}."""
    w, h, left, bottom, top, right = 640, 300, 56, 34, 28, 150
    vals = [v for vs in series.values() for v in vs if v is not None]
    if not vals or not xs:
        return "<p class='muted'>%s: нет данных</p>" % title
    ymax = max(vals) * 1.15 or 1
    xmin, xmax = min(xs), max(xs)
    sx = lambda x: left + (0.5 if xmax == xmin else (x - xmin) / (xmax - xmin)) * (w - left - right)
    sy = lambda y: top + (1 - y / ymax) * (h - top - bottom)
    colors = ["#2B5278", "#F5B301", "#2E9D63", "#C0392B", "#7B4EA3", "#1F8FA6", "#8A6D3B"]
    parts = ["<svg viewBox='0 0 %d %d' class='chart' role='img' aria-label='%s'>" % (w, h, title),
             "<text x='%d' y='18' class='t'>%s</text>" % (left, title)]
    for k in range(5):
        y = ymax * k / 4
        parts.append("<line x1='%d' x2='%d' y1='%.1f' y2='%.1f' class='g'/><text x='%d' y='%.1f' class='a' text-anchor='end'>%.0f</text>"
                     % (left, w - right, sy(y), sy(y), left - 6, sy(y) + 4, y))
    for x in xs:
        parts.append("<text x='%.1f' y='%d' class='a' text-anchor='middle'>%d</text>" % (sx(x), h - 12, x))
    parts.append("<text x='%d' y='%d' class='a'>человек</text><text x='4' y='%d' class='a'>%s</text>" % (w - right + 4, h - 12, top - 8, unit))
    for i, (name, vs) in enumerate(series.items()):
        c = colors[i % len(colors)]
        pts = [(sx(x), sy(v)) for x, v in zip(xs, vs) if v is not None]
        if len(pts) > 1:
            parts.append("<polyline fill='none' stroke='%s' stroke-width='2.5' points='%s'/>" % (c, " ".join("%.1f,%.1f" % p for p in pts)))
        for px, py in pts:
            parts.append("<circle cx='%.1f' cy='%.1f' r='3.5' fill='%s'/>" % (px, py, c))
        parts.append("<rect x='%d' y='%d' width='12' height='12' fill='%s' rx='2'/><text x='%d' y='%d' class='a'>%s</text>"
                     % (w - right + 12, top + 18 * i, c, w - right + 30, top + 18 * i + 10, name))
    parts.append("</svg>")
    return "".join(parts)


def write_report(t, results):
    rows = sorted(results, key=lambda r: r["users"])
    xs = [r["users"] for r in rows]
    p95 = lambda d: (d or {}).get("p95")
    names = lambda key: sorted({k for r in rows for k in (r.get(key) or {})})
    charts = [
        _svg_chart("Доставка сообщений чата по WebSocket, 95%", "мс", {"сообщения": [p95(r.get("chat")) for r in rows],
                   "уведомления «телефонам»": [p95(r.get("push")) for r in rows]}, xs),
        _svg_chart("Открытие списков и карточек (запрос к серверу), 95%", "мс",
                   {n: [p95((r.get("lists") or {}).get(n)) for r in rows] for n in names("lists")}, xs),
        _svg_chart("Открытие страниц в браузере, 95%", "мс", {n: [p95((r.get("pages") or {}).get(n)) for r in rows] for n in names("pages")}, xs),
        _svg_chart("Отправка файлов: от начала до ответа сервера, 95%", "с",
                   {n: [p95((r.get("uploads") or {}).get(n)) for r in rows] for n in names("uploads")}, xs),
        _svg_chart("Сервер: процессор и задержка обработки", "% / мс",
                   {"процессор, в среднем %": [(r.get("server") or {}).get("cpu_avg") for r in rows],
                    "процессор, пик %": [(r.get("server") or {}).get("cpu_peak") for r in rows],
                    "задержка цикла, 95% мс": [(r.get("server") or {}).get("loop_lag_p95") for r in rows]}, xs),
    ]
    head = "".join("<th>%d чел.</th>" % r["users"] for r in rows)
    cell = lambda d: "—" if not d else "%s / %s" % (d.get("p50"), d.get("p95"))
    lines = [("Подключение к переписке, мс", [cell(r.get("connect")) for r in rows]),
             ("Сообщение в чате (WebSocket), мс", [cell(r.get("chat")) for r in rows]),
             ("Уведомление «телефону», мс", [cell(r.get("push")) for r in rows])]
    lines += [("Список «%s», мс" % n, [cell((r.get("lists") or {}).get(n)) for r in rows]) for n in names("lists")]
    lines += [("Страница «%s» в браузере, мс" % n, [cell((r.get("pages") or {}).get(n)) for r in rows]) for n in names("pages")]
    lines += [("Файл «%s», с" % n, [cell((r.get("uploads") or {}).get(n)) for r in rows]) for n in names("uploads")]
    lines += [("Процессор: в среднем / пик, %", ["%s / %s" % ((r.get("server") or {}).get("cpu_avg"), (r.get("server") or {}).get("cpu_peak")) for r in rows]),
              ("Свободной памяти, минимум МБ", [str((r.get("server") or {}).get("ram_min_free_mb")) for r in rows]),
              ("Не дошло сообщений", [str(r.get("chat_lost")) for r in rows]),
              ("Ошибок", [str(len(r.get("errors") or [])) for r in rows])]
    table = "".join("<tr><td>%s</td>%s</tr>" % (name, "".join("<td>%s</td>" % v for v in values)) for name, values in lines)
    html = """<!doctype html><html lang="ru"><meta charset="utf-8"><title>Нагрузка по ступеням — Kovapp</title>
<style>body{font:14px/1.45 system-ui,Segoe UI,Roboto,sans-serif;margin:24px;color:#132A32;background:#F1F4F6}
h1{font-size:22px}table{border-collapse:collapse;background:#fff;margin:16px 0}td,th{border:1px solid #D5DDE2;padding:6px 10px;text-align:right}
td:first-child,th:first-child{text-align:left}th{background:#E8EEF1}.chart{background:#fff;border:1px solid #D5DDE2;border-radius:10px;margin:8px 12px 8px 0;width:640px;max-width:100%%}
.t{font-weight:600;font-size:13px}.a{font-size:11px;fill:#55666E}.g{stroke:#E3E9EC}.muted{color:#55666E}</style>
<h1>Нагрузка по ступеням: от %d до %d человек</h1>
<p class="muted">Сервер урезан до размеров рабочей машины. В ячейках — медиана / 95%% (у 95%% операций время не больше).
На каждой ступени %d с разгона и %d с замера; «телефонов» — %d%% людей. Пороги из .env проверяются на самой большой ступени.</p>
<table><tr><th>Показатель</th>%s</tr>%s</table>%s</html>""" % (max(xs), min(xs), WARMUP_S, STEP_S, round(PHONE_SHARE * 100), head, table, "".join(charts))
    path = t.log.dir / "load-report.html"
    path.write_text(html, encoding="utf-8")
    (t.log.dir / "load-steps.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


@case("Нагрузка по ступеням: от LOAD_USERS человек до LOAD_USERS_MIN — время ответа на каждой ступени", layer="api", order=1)
def stepped_load(t):
    plan = steps_plan()
    users = load_users(t)[:max(plan)]
    payloads(t)
    groups = groups_of(t, users, "Нагрузка")
    director = t.api("director")
    task = t.data.create_task("director", "Нагрузка по ступеням", ["Монтажный отдел"], responsible=t.data.ids("head_mont"))
    stage = task["stage_details"][0]
    director.call("PUT", "stages/%d/participants" % stage["id"], json=[api.user_id for _, api in users])
    if BROWSER_PROBE:
        try:
            t.data.cache["load_probe_page"] = t.page("director", "light", "desktop", path="#/tasks")
        except Exception as e:
            t.warn("живой браузер не открылся — страницы меряются только запросами: %s" % e)
    t.log.info("ступени: %s человек; на каждой %d с разгона и %d с замера; «телефонов» %d%%" % (
        ", ".join(map(str, plan)), WARMUP_S, STEP_S, round(PHONE_SHARE * 100)))
    results = []
    for index, n in enumerate(plan):
        if index:
            cooldown(t)
        r = run_step(t, n, users, groups, task, stage)
        results.append(r)
        s = r.get("server") or {}
        t.log.info("ступень %d чел.: сообщения %s мс (не дошло %d), уведомления «телефонам» %s мс; списки 95%%: %s; "
                   "страницы 95%%: %s; файлы 95%%, с: %s; процессор %s%% (пик %s%%); ошибок %d" % (
                       n, (r.get("chat") or {}).get("p95"), r.get("chat_lost"), (r.get("push") or {}).get("p95"),
                       ", ".join("%s %s" % (k, (v or {}).get("p95")) for k, v in sorted((r.get("lists") or {}).items())),
                       ", ".join("%s %s" % (k, (v or {}).get("p95")) for k, v in sorted((r.get("pages") or {}).items())),
                       ", ".join("%s %s" % (k, (v or {}).get("p95")) for k, v in sorted((r.get("uploads") or {}).items())),
                       s.get("cpu_avg"), s.get("cpu_peak"), len(r.get("errors") or [])))
        write_report(t, results)          # отчёт после каждой ступени: оборвался прогон — видно, что успело пройти
    report = write_report(t, results)
    t.log.info("отчёт со сравнением ступеней и графиками: %s" % report.name)
    # на каждой ступени: ничего не теряется и сервер не отвечает ошибками
    for r in results:
        t.check(r.get("chat_lost") == 0, "%d чел.: не дошли %d сообщений из %d" % (r["users"], r.get("chat_lost"), r.get("chat_expected")))
        for item in (r.get("errors") or [])[:5]:
            t.fail("%d чел.: %s" % (r["users"], item))
    # пороги — на самой большой ступени, как в рабочий час пик
    top = results[0]
    s = top.get("server") or {}
    t.check(((top.get("connect") or {}).get("p95") or 0) <= CONNECT_P95_MS,
            "%d чел.: 95%% подключений за %s мс, допустимо %d" % (top["users"], (top.get("connect") or {}).get("p95"), CONNECT_P95_MS))
    t.check(((top.get("chat") or {}).get("p95") or 0) <= CHAT_P95_MS,
            "%d чел.: 95%% сообщений доходят за %s мс, допустимо %d" % (top["users"], (top.get("chat") or {}).get("p95"), CHAT_P95_MS))
    for name, d in (top.get("lists") or {}).items():
        t.check((d or {}).get("p95", 0) <= API_P95_MS, "%d чел.: список «%s» — 95%% за %s мс, допустимо %d" % (top["users"], name, d.get("p95"), API_P95_MS))
    for name, d in (top.get("pages") or {}).items():
        t.check((d or {}).get("p95", 0) <= PAGE_P95_MS, "%d чел.: страница «%s» — 95%% за %s мс, допустимо %d" % (top["users"], name, d.get("p95"), PAGE_P95_MS))
    up = top.get("uploads") or {}
    small = [(up.get(k) or {}).get("p95", 0) for k in ("photo", "document")]
    t.check(max(small or [0]) <= SMALL_P95_S, "%d чел.: 95%% фото и документов за %s с, допустимо %d" % (top["users"], max(small or [0]), SMALL_P95_S))
    t.check(((up.get("video") or {}).get("p95") or 0) <= VIDEO_P95_S,
            "%d чел.: 95%% видео с телефона за %s с, допустимо %d" % (top["users"], (up.get("video") or {}).get("p95"), VIDEO_P95_S))
    raw = [(up.get(k) or {}).get("max", 0) for k in up if k.endswith("-raw")]
    t.check(not raw or max(raw) <= UPLOAD_P95_S * 2, "%d чел.: несжатый файл обрабатывался %s с — дольше %d с" % (top["users"], max(raw) if raw else 0, UPLOAD_P95_S * 2))
    if s.get("loop_lag_p95") is not None:
        t.check(s["loop_lag_p95"] <= LOOP_LAG_P95_MS, "%d чел.: задержка обработки запросов %s мс у 95%% замеров, допустимо %d" % (
            top["users"], s["loop_lag_p95"], LOOP_LAG_P95_MS))
    if s.get("ram_total_mb") and s.get("ram_min_free_mb") is not None:
        t.check(s["ram_min_free_mb"] >= s["ram_total_mb"] * RAM_MIN_FREE_PERCENT / 100.0,
                "%d чел.: свободной памяти оставалось %s МБ из %s" % (top["users"], s["ram_min_free_mb"], s["ram_total_mb"]))


@case("Медленные запросы базы за время нагрузки", layer="api", order=9)
def slow_queries(t):
    conn = db_connection()
    if conn is None:
        t.skip("нет доступа к базе для чтения журнала медленных запросов (только в полном режиме)")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT start_time, query_time, rows_examined, db, LEFT(sql_text, 600) FROM mysql.slow_log "
                        "ORDER BY query_time DESC LIMIT 30")
            rows = cur.fetchall()
    finally:
        conn.close()
    report = t.log.dir / "db-slow-queries.txt"
    with open(report, "w", encoding="utf-8") as out:
        for start, spent, examined, db, text in rows:
            out.write("%s  %s с  строк просмотрено %s  база %s\n%s\n\n" % (start, spent, examined, db, text))
    t.log.info("медленных запросов в журнале базы: %d, подробности — %s" % (len(rows), report.name))
    for start, spent, examined, db, text in rows[:5]:
        t.warn("медленный запрос %s с (%s строк): %s" % (spent, examined, (text or "")[:160]))

