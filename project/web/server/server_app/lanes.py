# -*- coding: utf-8 -*-
"""Полосы запросов: лёгкие (списки, чат) не ждут тяжёлых (загрузки файлов)."""
import asyncio
import collections
import hashlib
import os
import re
import time

from .config import logger

MB = 1024 * 1024


def _env_int(name, default):
    try:
        return int(float(os.getenv(name) or default))
    except (TypeError, ValueError):
        return default


def _cores():
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except Exception:
        return max(1, os.cpu_count() or 1)


SAFETY = min(1.0, max(0.3, float(os.getenv("LIMITS_SAFETY") or 0.8)))


# ── пределы по мощности машины ──
def _capacity():
    try:
        from .load_governor import detect_capacity
        cap = detect_capacity()
        return max(1, cap.cores), cap.ram_total, cap.ram_reserve
    except Exception:
        return _cores(), 2048 * MB, 768 * MB


def plan(capacity=None) -> dict:
    """Пределы из ядер и памяти (с учётом ограничений контейнера), с запасом SAFETY.

    Потоки лёгких запросов: 32 на ядро, не больше памяти (4 МБ на поток).
    Пул соединений с базой: не меньше DB_POOL_MIN (по умолчанию 100) и не меньше
    числа потоков с запасом, чтобы поток никогда не ждал соединения.
    Тяжёлые одновременно: 4 на ядро, не больше памяти (32 МБ на загрузку).
    Объём тяжёлых в работе: 15% памяти, оставшейся после неприкосновенного запаса.
    """
    cores, ram, reserve = capacity or _capacity()
    usable_mb = max(256.0, (ram - reserve) / MB)
    threads = max(16, int(SAFETY * min(cores * 32, usable_mb / 4)))
    threads = _env_int("THREADPOOL_SIZE", threads)
    heavy = _env_int("HEAVY_CONCURRENCY", max(2, int(SAFETY * min(cores * 4, usable_mb / 32))))
    db_pool = max(_env_int("DB_POOL_MIN", 100), threads + heavy + 16)
    role = (os.getenv("KOVAPP_ROLE") or "").strip().lower()
    small_pool = {"realtime": 30, "reader": 40, "uploader": 40}.get(role)
    if small_pool:
        # переписка и процессы чтения держат свои, меньшие пулы
        db_pool = small_pool
    # процессы чтения и приёма загрузок — сколько позволяют ядра и память (около PROCESS_MB на процесс);
    # если база на этой же машине, её память (DB_LOCAL_MB) сюда не входит
    local_db = (os.getenv("DB_HOST") or "127.0.0.1").strip() in ("127.0.0.1", "localhost", "::1")
    app_mb = usable_mb - (_env_int("DB_LOCAL_MB", 400) if local_db else 0)
    slots = max(2, int(app_mb // max(64, _env_int("PROCESS_MB", 200))))
    # сначала процессы чтения (списки открывают все и постоянно), второй процесс приёма — если хватает памяти
    readers = _env_int("API_READERS", max(1, min(cores, slots - 1)))
    uploaders = _env_int("API_UPLOADERS", max(1, min(2, slots - readers)))
    return {
        "threads": threads,
        "heavy": heavy,
        "budget_mb": _env_int("HEAVY_BUDGET_MB", max(32, int(SAFETY * usable_mb * 0.15))),
        "db_pool": db_pool,
        "db_overflow": 10 if small_pool else max(20, db_pool // 5),
        "readers": readers,
        "uploaders": uploaders,
        "cores": cores, "ram_mb": int(ram / MB), "reserve_mb": int(reserve / MB),
    }


def fit_to_database(max_connections: int) -> None:
    """Пул не должен просить у MariaDB больше соединений, чем она разрешает (две базы + запас)."""
    allowed = max(10, (int(max_connections) - 20) // 2)
    if PLAN["db_pool"] + PLAN["db_overflow"] <= allowed:
        return
    PLAN["db_overflow"] = max(5, allowed // 6)
    PLAN["db_pool"] = max(5, allowed - PLAN["db_overflow"])
    logger.warning("MariaDB разрешает %d соединений — пул базы уменьшен до %d + %d. Поднимите max_connections "
                   "(см. docs/mariadb), чтобы сервер мог использовать всю мощность машины",
                   max_connections, PLAN["db_pool"], PLAN["db_overflow"])
    _limit_threads_by_pool()


def check_database(session_factory) -> None:
    """При запуске: узнать у MariaDB её предел соединений и подогнать под него пул."""
    try:
        from sqlalchemy import text
        db = session_factory()
        try:
            row = db.execute(text("SHOW VARIABLES LIKE 'max_connections'")).first()
        finally:
            db.close()
        if row:
            fit_to_database(int(row[1]))
    except Exception as e:
        logger.info("Предел соединений MariaDB не прочитан (%s) — пул по расчёту", e)
    _limit_threads_by_pool()


def refresh_capacity() -> bool:
    """Мощность машины изменилась (добавили ядра или память) — пересчитать пределы без перезапуска."""
    global PLAN, THREADPOOL_SIZE, HEAVY_CONCURRENCY, HEAVY_BUDGET, SMALL_SLOTS
    cores, ram, reserve = _capacity()
    if cores == PLAN["cores"] and abs(ram / MB - PLAN["ram_mb"]) < 64:
        return False
    old = PLAN
    new = plan((cores, ram, reserve))
    pool_later = (new["db_pool"], new["db_overflow"]) != (old["db_pool"], old["db_overflow"])
    new["db_pool"], new["db_overflow"] = old["db_pool"], old["db_overflow"]
    new["threads"] = min(new["threads"], new["db_pool"] + new["db_overflow"])
    PLAN = new
    THREADPOOL_SIZE, HEAVY_CONCURRENCY, HEAVY_BUDGET = new["threads"], new["heavy"], new["budget_mb"] * MB
    SMALL_SLOTS = max(4, cores * 4)
    try:
        from anyio import to_thread
        limiter = to_thread.current_default_thread_limiter()
        limiter.total_tokens = THREADPOOL_SIZE
        APPLIED["threads"] = limiter.total_tokens
    except Exception:
        pass
    try:
        from concurrent.futures import ThreadPoolExecutor
        asyncio.get_running_loop().set_default_executor(
            ThreadPoolExecutor(max_workers=HEAVY_CONCURRENCY + 4, thread_name_prefix="kov-heavy"))
    except Exception:
        pass
    try:
        from .load_governor import detect_capacity, governor
        governor().capacity = detect_capacity()
    except Exception:
        pass
    if GATE is not None:
        GATE.limit = min(GATE.limit, HEAVY_CONCURRENCY)
        GATE.reason = "мощность машины изменилась"
    logger.info("Мощность машины изменилась: ядер %d → %d, память %d → %d МБ. Новые пределы: потоков %d, "
                "тяжёлых до %d, в работе до %d МБ%s", old["cores"], cores, old["ram_mb"], new["ram_mb"],
                THREADPOOL_SIZE, HEAVY_CONCURRENCY, new["budget_mb"],
                "; пул базы пересчитается при перезапуске сервера" if pool_later else "")
    return True


def _limit_threads_by_pool() -> None:
    global THREADPOOL_SIZE
    THREADPOOL_SIZE = PLAN["threads"] = min(PLAN["threads"], PLAN["db_pool"] + PLAN["db_overflow"])


PLAN = plan()
THREADPOOL_SIZE = PLAN["threads"]
HEAVY_CONCURRENCY = PLAN["heavy"]
HEAVY_BUDGET = PLAN["budget_mb"] * MB
SMALL_SLOTS = max(8, PLAN["cores"] * 8)
ETAGS = (os.getenv("LIST_ETAGS") or "1").strip() != "0"
LIST_PATHS = re.compile(r"^/(tasks|tasks/light|projects|chat/list|employees|departments|notifications|employees/\d+/notifications)/?$")
LIGHT_SKIP = ("/login", "/token", "/health", "/file_storage/", "/download", "/thumb", "/view", "/preview", "/sheet",
              "/photo", "/stream")
HEAVY_MIN_BYTES = _env_int("HEAVY_MIN_KB", 256) * 1024
HEAVY_WAIT_S = _env_int("HEAVY_WAIT_S", 600)
UPLOAD_SPOOL = _env_int("UPLOAD_SPOOL_MB", 2) * MB
LOOP_SLOW_MS = _env_int("LOOP_SLOW_MS", 0)


# ── настройка при старте ──
APPLIED = {"threads": None}


def configure_runtime() -> None:
    """Вызывается при запуске сервера, внутри цикла событий."""
    try:
        from anyio import to_thread
        limiter = to_thread.current_default_thread_limiter()
        limiter.total_tokens = THREADPOOL_SIZE
        APPLIED["threads"] = limiter.total_tokens
    except Exception as e:
        logger.warning("Пул потоков не расширен: %s", e)
    try:
        from starlette.formparsers import MultiPartParser
        for name in ("spool_max_size", "max_file_size"):
            if isinstance(getattr(MultiPartParser, name, None), int):
                setattr(MultiPartParser, name, max(getattr(MultiPartParser, name), UPLOAD_SPOOL))
    except Exception as e:
        logger.warning("Размер буфера загрузок не изменён: %s", e)
    _fix_rollover()
    loop = asyncio.get_running_loop()
    try:
        from concurrent.futures import ThreadPoolExecutor
        loop.set_default_executor(ThreadPoolExecutor(max_workers=HEAVY_CONCURRENCY + 4, thread_name_prefix="kov-heavy"))
    except Exception as e:
        logger.warning("Пул фоновых потоков не изменён: %s", e)
    if LOOP_SLOW_MS > 0:
        loop.slow_callback_duration = LOOP_SLOW_MS / 1000.0
        loop.set_debug(True)
        logger.info("Журнал долгих шагов цикла событий: дольше %d мс", LOOP_SLOW_MS)
    loop.create_task(_adapt_forever())
    logger.info("Полосы запросов (ядер %d, память %d МБ, запас %d МБ, коэффициент %.2f): потоков для лёгких %d, "
                "тяжёлых одновременно до %d, в работе до %d МБ, файлы до %d МБ держатся в памяти; "
                "пул базы %d + %d", PLAN["cores"], PLAN["ram_mb"], PLAN["reserve_mb"], SAFETY, THREADPOOL_SIZE,
                HEAVY_CONCURRENCY, HEAVY_BUDGET // MB, UPLOAD_SPOOL // MB, PLAN["db_pool"], PLAN["db_overflow"])


def _fix_rollover() -> None:
    """Starlette до 0.47.2 сбрасывал большой файл на диск прямо в цикле событий (CVE-2025-54121)."""
    try:
        import starlette
        from starlette.concurrency import run_in_threadpool
        from starlette.datastructures import UploadFile
        version = tuple(int(x) for x in starlette.__version__.split(".")[:3] if x.isdigit())
        if version >= (0, 47, 2):
            return

        async def write(self, data: bytes) -> None:
            if self.size is not None:
                self.size += len(data)
            rolled = getattr(self.file, "_rolled", True)
            limit = getattr(self.file, "_max_size", 0) or 0
            if not rolled and (not limit or self.file.tell() + len(data) <= limit):
                self.file.write(data)
            else:
                await run_in_threadpool(self.file.write, data)
        UploadFile.write = write
    except Exception as e:
        logger.warning("Исправление записи загрузок не применено: %s", e)


# ── очередь тяжёлых запросов ──
class HeavyGate:
    """ASGI-прослойка: тяжёлые запросы идут по очереди, остальные — сразу."""

    def __init__(self, app):
        self.app = app
        self.active = 0
        self.bytes = 0
        self.queue = collections.deque()
        self.admitted = 0
        self.max_wait = 0.0
        self.limit = HEAVY_CONCURRENCY
        self.reason = "старт"
        self.light = collections.deque(maxlen=600)
        self.small_active = 0

    @staticmethod
    def _cost(scope):
        if scope.get("type") != "http" or scope.get("method") not in ("POST", "PUT", "PATCH"):
            return 0
        headers = dict(scope.get("headers") or [])
        try:
            size = int(headers.get(b"content-length") or 0)
        except ValueError:
            size = 0
        kind = headers.get(b"content-type", b"").lower()
        path = scope.get("path") or ""
        if size >= HEAVY_MIN_BYTES or kind.startswith(b"multipart/") or path.startswith("/uploads/sessions/"):
            return max(size, HEAVY_MIN_BYTES)
        return 0

    @staticmethod
    def _etag(scope, path):
        """Метка «версия данных + кто спрашивает + что спрашивает» для списков; раз в минуту меняется сама."""
        if not ETAGS or scope.get("method") != "GET" or not LIST_PATHS.match(path):
            return None
        from . import versions
        headers = dict(scope.get("headers") or [])
        who = hashlib.sha1(headers.get(b"authorization", b"") + path.encode() + b"?" + (scope.get("query_string") or b"")).hexdigest()[:16]
        return 'W/"%s.%s.%d.%s"' % (versions.current("main"), versions.current("chat"), int(time.time() // 60), who)

    @staticmethod
    def _small(cost):
        return cost <= UPLOAD_SPOOL

    def _fits(self, cost):
        """Мелкие загрузки (до UPLOAD_SPOOL) идут своей полосой и не ждут крупных; крупных — по пределу."""
        if self.active == 0:
            return True
        if self.bytes + cost > HEAVY_BUDGET:
            return False
        if self._small(cost):
            return self.small_active < SMALL_SLOTS
        return self.active - self.small_active < self.limit

    def _take(self, cost):
        self.active += 1
        self.bytes += cost
        if self._small(cost):
            self.small_active += 1

    def _admit_waiting(self):
        blocked = set()
        keep = collections.deque()
        while self.queue:
            cost, future = self.queue.popleft()
            if future.done():
                continue
            lane = self._small(cost)
            if lane not in blocked and self._fits(cost):
                self._take(cost)
                future.set_result(True)
            else:
                blocked.add(lane)
                keep.append((cost, future))
        self.queue = keep

    async def __call__(self, scope, receive, send):
        cost = self._cost(scope)
        if not cost:
            if scope.get("type") != "http":
                return await self.app(scope, receive, send)
            path = scope.get("path") or ""
            etag = self._etag(scope, path)
            if etag and dict(scope.get("headers") or []).get(b"if-none-match", b"").decode("latin-1") == etag:
                await send({"type": "http.response.start", "status": 304,
                            "headers": [(b"etag", etag.encode("latin-1")), (b"cache-control", b"private, no-cache")]})
                await send({"type": "http.response.body", "body": b""})
                return
            skip = any(mark in path for mark in LIGHT_SKIP)
            began = time.monotonic()

            async def timed_send(message):
                if message.get("type") == "http.response.start":
                    spent = time.monotonic() - began
                    if not skip:
                        self.light.append((time.monotonic(), spent))
                    headers = list(message.get("headers") or [])
                    headers.append((b"server-timing", ("app;dur=%.1f" % (spent * 1000)).encode("latin-1")))
                    if etag and message.get("status") == 200:
                        headers += [(b"etag", etag.encode("latin-1")), (b"cache-control", b"private, no-cache")]
                    message = dict(message, headers=headers)
                await send(message)
            return await self.app(scope, receive, timed_send)
        began = time.monotonic()
        lane = self._small(cost)
        if any(self._small(c) == lane and not f.done() for c, f in self.queue) or not self._fits(cost):
            future = asyncio.get_running_loop().create_future()
            self.queue.append((cost, future))
            try:
                await asyncio.wait_for(asyncio.shield(future), HEAVY_WAIT_S)
            except asyncio.TimeoutError:
                if future.done() and future.result():
                    self._release(cost)
                else:
                    future.cancel()
                return await self._busy(send)
            except asyncio.CancelledError:
                if future.done() and not future.cancelled():
                    self._release(cost)
                else:
                    future.cancel()
                raise
        else:
            self._take(cost)
        waited = time.monotonic() - began
        self.admitted += 1
        self.max_wait = max(self.max_wait, waited)
        if waited > 10:
            logger.info("Тяжёлый запрос %s ждал очереди %.1f с", scope.get("path"), waited)
        # место в очереди освобождается, как только тело запроса получено целиком:
        # дальше файл обрабатывается под присмотром распорядителя нагрузки
        released = False

        async def gated_receive():
            nonlocal released
            message = await receive()
            if not released and (message.get("type") == "http.disconnect" or (
                    message.get("type") == "http.request" and not message.get("more_body", False))):
                released = True
                self._release(cost)
            return message
        try:
            await self.app(scope, gated_receive, send)
        finally:
            if not released:
                self._release(cost)

    def _release(self, cost):
        self.active = max(0, self.active - 1)
        self.bytes = max(0, self.bytes - cost)
        if self._small(cost):
            self.small_active = max(0, self.small_active - 1)
        self._admit_waiting()

    @staticmethod
    async def _busy(send):
        body = "{\"detail\": \"Сервер занят загрузками, повторите через несколько секунд\"}".encode("utf-8")
        await send({"type": "http.response.start", "status": 503,
                    "headers": [(b"content-type", b"application/json; charset=utf-8"), (b"retry-after", b"5"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

    def light_p95(self, window=30.0):
        now = time.monotonic()
        recent = sorted(d for at, d in list(self.light) if now - at <= window)
        if len(recent) < 10:
            return None
        return recent[min(len(recent) - 1, int(0.95 * (len(recent) - 1)))]

    def snapshot(self):
        p95 = self.light_p95()
        return {"active": self.active, "small_active": self.small_active, "small_slots": SMALL_SLOTS,
                "waiting": sum(1 for _, f in self.queue if not f.done()),
                "bytes_mb": round(self.bytes / MB, 1), "limit": self.limit, "cap": HEAVY_CONCURRENCY,
                "budget_mb": HEAVY_BUDGET // MB, "admitted": self.admitted, "max_wait_s": round(self.max_wait, 1),
                "light_p95_ms": round(p95 * 1000) if p95 is not None else None, "reason": self.reason}


# ── подстройка под нагрузку: уменьшаем быстро, прибавляем по одному ──
LAG_BAD, LAG_GOOD = 1.0, 0.3
LIGHT_BAD, LIGHT_GOOD = 2.0, 1.2
CPU_GOOD = 0.80
MEM_BAD, MEM_GOOD = 0.5, 0.7


def adapt(gate, lag, cpu_busy, mem_available, reserve, light_p95):
    """Один шаг подстройки предела крупных загрузок.

    Снижаем на 30% только при настоящей беде: сервер сам отвечает с задержкой,
    памяти меньше половины неприкосновенного запаса или лёгкие запросы ждут
    дольше 2 с. Прибавляем по одной, пока есть очередь, процессор не загружен
    и лёгкие запросы отвечают нормально.
    """
    waiting = any(not f.done() and not gate._small(c) for c, f in gate.queue)
    light = "?" if light_p95 is None else round(light_p95 * 1000)
    uploading = gate.active - gate.small_active > 0 or waiting
    if mem_available < reserve * MEM_BAD or (uploading and (lag > LAG_BAD or (light_p95 is not None and light_p95 > LIGHT_BAD))):
        gate.reason = "тяжело: задержка %.0f мс, лёгкие %s мс, свободно %d МБ" % (lag * 1000, light, mem_available // MB)
        return max(1, int(gate.limit * 0.7))
    if waiting and gate.limit < HEAVY_CONCURRENCY and lag < LAG_GOOD and cpu_busy < CPU_GOOD \
            and mem_available > reserve * MEM_GOOD and (light_p95 is None or light_p95 < LIGHT_GOOD):
        gate.reason = "запас есть, в очереди крупные загрузки — прибавляем (процессор %.0f%%, лёгкие %s мс)" % (
            cpu_busy * 100, light)
        return gate.limit + 1
    return gate.limit


async def _adapt_forever(period=2.0):
    ticks = 0
    while True:
        await asyncio.sleep(period)
        ticks += 1
        if ticks % 30 == 0:
            try:
                refresh_capacity()
            except Exception as e:
                logger.debug("Проверка мощности машины: %s", e)
        gate = GATE
        if gate is None:
            continue
        try:
            from .load_governor import governor
            g = governor()
            snap = g.snapshot()
            if not snap.get("ram_available_mb") or snap.get("measured") is False:
                # замеров ещё нет — не решаем вслепую (раньше «0 МБ свободно» снижало предел зря)
                continue
            lag = (snap.get("loop_lag_ms") or 0) / 1000.0
            busy = float(snap.get("cpu_busy") or 0)
            free = snap["ram_available_mb"] * MB
            new = adapt(gate, lag, busy, free, g.capacity.ram_reserve, gate.light_p95())
            if new != gate.limit:
                grew = new > gate.limit
                gate.limit = new
                if grew:
                    gate._admit_waiting()
        except Exception as e:
            logger.debug("Подстройка полос: %s", e)


GATE = None


def install(app) -> None:
    """Ставит прослойку снаружи всех остальных: ожидающий запрос ничего не занимает."""
    app.add_middleware(_GateHolder)


class _GateHolder:
    def __init__(self, app):
        global GATE
        GATE = HeavyGate(app)
        self.gate = GATE

    async def __call__(self, scope, receive, send):
        await self.gate(scope, receive, send)
