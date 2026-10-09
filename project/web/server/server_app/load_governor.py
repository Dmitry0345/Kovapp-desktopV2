# -*- coding: utf-8 -*-
"""Сколько сервера можно отдать обработке фото и видео.

Задача. Перекодирование — самая тяжёлая работа на сервере, и она не должна
мешать людям пользоваться приложением. При этом очередь обязана двигаться
всегда: даже под нагрузкой один файл должен обрабатываться, иначе загрузки
зависнут навсегда.

Как это решается. Раз в несколько секунд снимается состояние машины, и по
нему пересчитывается, сколько задач разрешено одновременно. Правило взято
из проверенной практики (так же работает адаптивный ограничитель Gitaly в
GitLab): плавно прибавлять по одной задаче, пока всё спокойно, и резко
делить пополам при первых признаках беды. Рост осторожный, отступление
быстрое — сервер успевает отпустить нагрузку раньше, чем начнёт задыхаться.

Что считается бедой:
  * свободной памяти меньше запаса — самое опасное, ffmpeg на 4К съедает
    сотни мегабайт, и нехватка памяти кладёт весь процесс, а не задачу;
  * чужая загрузка процессора выше порога — «чужая» это всё, кроме нашей
    собственной обработки: три пользователя и пятьдесят дают разную картину;
  * задержка цикла событий приложения — прямой признак, что запросы уже
    отвечают медленно. Этот признак важнее прочих: он измеряет не машину, а
    то, что чувствует пользователь.

Нижняя граница — одна задача с одним потоком. Она не опускается никогда,
поэтому очередь ползёт даже на перегруженном сервере.

Модуль работает без сторонних библиотек. Если установлен psutil, замеры
точнее (видно нашу собственную долю нагрузки); без него используются
/proc в Linux и системные вызовы в Windows.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

try:  # необязательная зависимость: с ней замеры точнее
    import psutil  # type: ignore
except Exception:  # pragma: no cover - зависит от окружения
    psutil = None

MB = 1024 * 1024
PHOTO_MEMORY = 320 * MB
LIGHT_LIMIT = 4
PAUSE_MAX = 45.0
RUN_MIN = 8.0


# ── что за машина ────────────────────────────────────────────────────────

@dataclass
class Capacity:
    cores: int                 # сколько ядер реально доступно процессу
    ram_total: int             # байт
    source: str                # откуда взяли: cgroup, psutil, os

    @property
    def ram_reserve(self) -> int:
        """Неприкосновенный запас памяти: остальным программам и ядру.

        Пятая часть памяти, но не меньше 768 МБ и не больше 2 ГБ — на
        маленькой машине жёсткие 2 ГБ съели бы половину всего.
        """
        return int(min(max(self.ram_total * 0.2, 768 * MB), 2048 * MB))


def _read_int(path: str) -> Optional[int]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return int(fh.read().strip())
    except Exception:
        return None


def _cgroup_cores() -> Optional[float]:
    """Доля процессора, отведённая контейнеру. os.cpu_count внутри Docker
    показывает ядра всей машины и потому обманывает."""
    try:  # cgroup v2
        with open("/sys/fs/cgroup/cpu.max", "r", encoding="utf-8") as fh:
            quota, period = fh.read().split()
            if quota != "max":
                return max(0.1, int(quota) / int(period))
    except Exception:
        pass
    quota = _read_int("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")   # cgroup v1
    period = _read_int("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
    if quota and period and quota > 0:
        return max(0.1, quota / period)
    return None


def _cgroup_memory() -> Optional[int]:
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = fh.read().strip()
            if raw == "max":
                continue
            value = int(raw)
            # cgroup v1 без ограничения пишет заведомо огромное число
            if 0 < value < (1 << 62):
                return value
        except Exception:
            continue
    return None


def _total_memory() -> int:
    limit = _cgroup_memory()
    if limit:
        return limit
    if psutil:
        try:
            return int(psutil.virtual_memory().total)
        except Exception:
            pass
    try:  # Linux
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except Exception:
        pass
    try:  # Windows без psutil
        import ctypes

        class _Status(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        status = _Status()
        status.dwLength = ctypes.sizeof(_Status)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return int(status.ullTotalPhys)
    except Exception:
        return 4 * 1024 * MB          # осторожная оценка: считаем машину слабой


def detect_capacity() -> Capacity:
    source = "os"
    cores = None
    quota = _cgroup_cores()
    if quota:
        cores, source = max(1, int(quota)), "cgroup"
    if cores is None and hasattr(os, "sched_getaffinity"):
        try:
            cores, source = len(os.sched_getaffinity(0)), "affinity"
        except Exception:
            cores = None
    if cores is None:
        cores = os.cpu_count() or 2
    return Capacity(cores=max(1, int(cores)), ram_total=_total_memory(), source=source)


# ── что происходит прямо сейчас ──────────────────────────────────────────

@dataclass
class Sample:
    at: float = 0.0
    cpu_busy: float = 0.0        # 0..1 — вся машина
    own_cpu: float = 0.0         # 0..1 — наша обработка
    mem_available: int = 0
    loop_lag: float = 0.0        # секунды задержки цикла событий
    ok: bool = False             # удалось ли вообще что-то измерить

    @property
    def foreign_cpu(self) -> float:
        """Загрузка, созданная не нами: приложение, база, чужие программы."""
        return max(0.0, self.cpu_busy - self.own_cpu)


class _CpuProbe:
    """Загрузка процессора без psutil: /proc/stat в Linux, GetSystemTimes в Windows."""

    def __init__(self):
        self._prev = None

    def busy(self) -> Optional[float]:
        if psutil:
            try:
                return float(psutil.cpu_percent(interval=None)) / 100.0
            except Exception:
                pass
        total_idle = self._raw()
        if not total_idle:
            return None
        total, idle = total_idle
        prev, self._prev = self._prev, (total, idle)
        if not prev:
            return None
        d_total, d_idle = total - prev[0], idle - prev[1]
        if d_total <= 0:
            return None
        return max(0.0, min(1.0, 1.0 - d_idle / d_total))

    @staticmethod
    def _raw():
        try:  # Linux
            with open("/proc/stat", "r", encoding="utf-8") as fh:
                parts = [float(x) for x in fh.readline().split()[1:]]
            return sum(parts), parts[3] + (parts[4] if len(parts) > 4 else 0.0)
        except Exception:
            pass
        try:  # Windows
            import ctypes
            from ctypes import wintypes

            idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
            if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel),
                                                         ctypes.byref(user)):
                return None
            to_int = lambda ft: (ft.dwHighDateTime << 32) | ft.dwLowDateTime
            idle_t, kernel_t, user_t = to_int(idle), to_int(kernel), to_int(user)
            return kernel_t + user_t, idle_t     # kernel уже включает idle
        except Exception:
            return None


def _cgroup_available() -> Optional[int]:
    """Свободная память внутри ограничения контейнера: лимит минус занятое (без файлового кэша)."""
    limit = _cgroup_memory()
    if not limit:
        return None
    for current, stat in (("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory.stat"),
                          ("/sys/fs/cgroup/memory/memory.usage_in_bytes", "/sys/fs/cgroup/memory/memory.stat")):
        used = _read_int(current)
        if used is None:
            continue
        cache = 0
        try:
            # файловый кэш (и недавно записанные файлы) система освобождает сама — это не занятая память
            found = {}
            with open(stat, "r", encoding="utf-8") as fh:
                for line in fh:
                    key, _, value = line.partition(" ")
                    if key in ("file", "total_cache", "cache", "inactive_file", "total_inactive_file"):
                        found[key] = int(value)
            cache = found.get("file") or found.get("total_cache") or found.get("cache") \
                or found.get("inactive_file") or found.get("total_inactive_file") or 0
        except Exception:
            pass
        return max(0, int(limit) - max(0, used - cache))
    return None


def _available_memory(total: int) -> int:
    inside = _cgroup_available()
    if inside is not None:
        return inside
    if psutil:
        try:
            return int(psutil.virtual_memory().available)
        except Exception:
            pass
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except Exception:
        pass
    try:
        import ctypes

        class _Status(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        status = _Status()
        status.dwLength = ctypes.sizeof(_Status)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return int(status.ullAvailPhys)
    except Exception:
        return total // 2            # ничего не знаем — считаем, что половина свободна


# ── сам распорядитель ────────────────────────────────────────────────────

@dataclass
class Limits:
    media: int = 1               # фото и прочие быстрые задачи
    video: int = 1               # перекодирование
    threads: int = 1             # ядер на один ffmpeg
    reason: str = "старт"


class Governor:
    """Решает, сколько задач пускать. Один экземпляр на процесс."""

    # пороги подобраны так, чтобы отступать заметно раньше, чем сервер встанет
    CPU_TROUBLE = 0.85           # чужая загрузка, при которой сжимаемся
    CPU_CALM = 0.60              # ниже этого можно прибавлять
    LAG_TROUBLE = 0.25           # секунды: приложение уже отвечает с задержкой
    LAG_CALM = 0.10
    PERIOD = 5.0                 # как часто снимаем показания
    RECALC = 15.0                # как часто меняем лимиты

    def __init__(self, capacity: Optional[Capacity] = None, hard_media: int = 0, hard_video: int = 0):
        self.capacity = capacity or detect_capacity()
        cores = self.capacity.cores
        ram_gb = self.capacity.ram_total / (1024 * MB)
        # Потолки от железа: на одно ядро видео не ставим два процесса, а
        # память под 4К-перекодирование считаем по гигабайту на задачу
        self.max_video = hard_video or max(1, min(3, cores // 3, int(ram_gb // 2) or 1))
        spare = max(0, self.capacity.ram_total - self.capacity.ram_reserve)
        self.max_media = hard_media or max(1, min(6, cores, int(spare // PHOTO_MEMORY) or 1))
        self.min_limit = 1
        self._limits = Limits(media=max(1, self.max_media // 2), video=1, reason="старт")
        self._sample = Sample()
        self._probe = _CpuProbe()
        self._proc = None            # наш процесс, между замерами не пересоздаётся
        self._kids = {}              # pid → процесс ffmpeg
        self._lag = 0.0
        self._lock = threading.Lock()
        self._active = {"media": 0, "video": 0, "light": 0}
        self._waiting = {"media": 0, "video": 0, "light": 0}
        self._heavy = {}
        self._paused_at = {}
        self._paused_total = {}
        self._ran_since = {}
        self._cond = threading.Condition(self._lock)
        self._last_calc = 0.0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._history = []

    # ── замеры ──
    def start(self) -> None:
        if self._thread:
            return
        self._probe.busy()                      # первый вызов только заводит счётчик
        self._thread = threading.Thread(target=self._loop, name="load-governor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._cond:
            for pid in list(self._paused_at):
                self._signal(pid, False)
            self._paused_at.clear()

    def _loop(self) -> None:
        last = 0.0
        while not self._stop.wait(1.0):
            self.tick()
            if time.time() - last < self.PERIOD:
                continue
            last = time.time()
            try:
                self.probe()
            except Exception as e:                     # замер никогда не должен ронять сервер
                logger.debug("Распорядитель: замер не удался: %s", e)

    def probe(self) -> Sample:
        busy = self._probe.busy()
        own = self._own_cpu()
        sample = Sample(at=time.time(),
                        cpu_busy=busy if busy is not None else 0.0,
                        own_cpu=own,
                        mem_available=_available_memory(self.capacity.ram_total),
                        loop_lag=self._lag,
                        ok=busy is not None)
        with self._lock:
            self._sample = sample
            if time.time() - self._last_calc >= self.RECALC:
                self._apply(self.decide(sample, self._limits))
                self._last_calc = time.time()
            self._cond.notify_all()
        return sample

    def _own_cpu(self) -> float:
        """Наша доля нагрузки: сам сервер и его дочерние ffmpeg.

        Объекты процессов держим между замерами: psutil считает загрузку как
        разницу с прошлым обращением, и новый объект на каждый замер всегда
        возвращал бы ноль. Тогда собственное перекодирование выглядело бы
        чужой нагрузкой, и распорядитель душил бы сам себя.
        """
        if psutil:
            try:
                if self._proc is None:
                    self._proc = psutil.Process()
                    self._proc.cpu_percent(interval=None)
                total = self._proc.cpu_percent(interval=None)
                alive = {}
                for child in self._proc.children(recursive=True):
                    known = self._kids.get(child.pid)
                    if known is None:
                        known = child
                        known.cpu_percent(interval=None)    # заводим счётчик
                    try:
                        total += known.cpu_percent(interval=None)
                    except Exception:
                        continue
                    alive[child.pid] = known
                self._kids = alive
                return max(0.0, min(1.0, total / 100.0 / self.capacity.cores))
            except Exception:
                pass
        # Без psutil считаем по-простому: каждая наша задача занимает
        # выделенные ей потоки. Оценка грубая, но лучше, чем ничего.
        with self._lock:
            busy_threads = self._active["video"] * self._limits.threads + self._active["media"]
        return max(0.0, min(1.0, busy_threads / self.capacity.cores))

    def note_lag(self, seconds: float) -> None:
        """Задержку цикла событий сообщает приложение (bootstrap)."""
        self._lag = max(0.0, float(seconds))

    # ── решение ──
    def decide(self, sample: Sample, current: Limits) -> Limits:
        """Чистая функция: по состоянию и текущим лимитам даёт новые.

        Прибавляем по одной, отнимаем половину — так принято в адаптивных
        ограничителях и так безопаснее: разгон медленный, торможение резкое.
        """
        reserve = self.capacity.ram_reserve
        low_memory = sample.mem_available < reserve
        very_low_memory = sample.mem_available < reserve * 0.6
        hot = sample.foreign_cpu > self.CPU_TROUBLE
        laggy = sample.loop_lag > self.LAG_TROUBLE
        calm = (sample.foreign_cpu < self.CPU_CALM and sample.loop_lag < self.LAG_CALM
                and sample.mem_available > reserve * 1.5)

        video, media = current.video, current.media
        if very_low_memory:
            return Limits(media=self.min_limit, video=self.min_limit, threads=1,
                          reason="памяти почти нет")
        if low_memory or hot or laggy:
            reason = "мало памяти" if low_memory else ("занят процессор" if hot else "приложение тормозит")
            video = max(self.min_limit, video // 2)
            media = max(self.min_limit, media // 2)
            # В беде ужимаем и число потоков: каждый поток ffmpeg держит свои
            # буферы, и при нехватке памяти наращивать их — последнее, что нужно
            threads = min(self._threads(sample, video), max(1, self.capacity.cores // 2))
            if low_memory:
                threads = min(threads, max(1, self.capacity.cores // 4))
            return Limits(media=media, video=video, threads=max(1, threads), reason=reason)
        if not sample.ok:
            # Замерить не вышло (нет ни psutil, ни /proc): держимся середины
            return Limits(media=max(1, self.max_media // 2), video=1, threads=max(1, self.capacity.cores // 2),
                          reason="замеры недоступны")
        ceil_media, ceil_video = self._ceiling(sample)
        if calm:
            video = min(self.max_video, video + 1)
            media = min(self.max_media, media + 1)
            capped = video >= self.max_video and media >= self.max_media
            reason = "потолок железа" if capped else "есть запас"
        else:
            reason = "держим"
        # свободная мощность — прямое ограничение поверх шагов
        if video > ceil_video or media > ceil_media:
            video, media = min(video, ceil_video), min(media, ceil_media)
            reason = "мало свободной мощности"
        return Limits(media=media, video=video, threads=self._threads(sample, video), reason=reason)

    def _ceiling(self, sample: Sample) -> tuple:
        """Потолок по свободной мощности прямо сейчас.

        Шаги «плюс один — пополам» реагируют не сразу, а нам нужно ещё и
        прямое правило: чем больше сервер занят чужой работой, тем меньше
        задач мы вправе держать. Три пользователя — работаем в полную силу,
        пятьдесят — на том, что осталось.
        """
        free_cores = self.capacity.cores * max(0.0, 1.0 - sample.foreign_cpu)
        media = max(self.min_limit, min(self.max_media, int(round(free_cores))))
        video = max(self.min_limit, min(self.max_video, int(round(free_cores / 3))))
        # память тоже ограничивает: одна тяжёлая задача — примерно гигабайт
        free_mem = max(0, sample.mem_available - self.capacity.ram_reserve)
        by_memory = max(self.min_limit, int(free_mem // (1024 * MB)))
        return min(media, by_memory * 2), min(video, by_memory)

    def _threads(self, sample: Sample, video_limit: int) -> int:
        """Ядер на один ffmpeg: делим то, что не занято чужой работой."""
        free = self.capacity.cores * max(0.15, 1.0 - sample.foreign_cpu)
        per_job = free / max(1, video_limit)
        # одно ядро всегда оставляем приложению
        return max(1, min(int(per_job), max(1, self.capacity.cores - 1)))

    def _apply(self, limits: Limits) -> None:
        if (limits.video, limits.media, limits.threads) != (self._limits.video, self._limits.media,
                                                            self._limits.threads):
            # число потоков меняется каждые 15 с; в журнал — только смена слотов видео и фото
            level = logging.INFO if (limits.video, limits.media) != (self._limits.video, self._limits.media) else logging.DEBUG
            logger.log(level, "Распорядитель: видео %s→%s, фото %s→%s, потоков %s→%s (%s)",
                        self._limits.video, limits.video, self._limits.media, limits.media,
                        self._limits.threads, limits.threads, limits.reason)
        self._limits = limits
        self._history.append((time.time(), limits.video, limits.media, limits.reason))
        del self._history[:-50]

    # ── допуск задач ──
    def limits(self) -> Limits:
        with self._lock:
            return self._limits

    def threads(self) -> int:
        with self._lock:
            return max(1, self._limits.threads)

    def _room(self, kind: str, need_bytes: int) -> bool:
        if kind == "light":
            return self._active["light"] < max(LIGHT_LIMIT, self.capacity.cores * 2)
        limit = self._limits.video if kind == "video" else self._limits.media
        active = self._active[kind]
        # Ни одной задачи не идёт — пускаем всегда, иначе очередь встанет
        # намертво: пока ничего не работает, память сама не освободится.
        if active == 0:
            return True
        if active >= limit:
            return False
        if need_bytes and self._sample.at:
            free = self._sample.mem_available - self.capacity.ram_reserve
            if free < need_bytes:
                return False
        return True

    @contextmanager
    def slot(self, kind: str = "media", need_bytes: int = 0, timeout: float = 0.0):
        kind = kind if kind in ("video", "light") else "media"
        with self._cond:
            self._waiting[kind] += 1
            self._preempt()
            try:
                while not self._room(kind, need_bytes):
                    self._cond.wait(timeout=1.0)
                    self._preempt()
            finally:
                self._waiting[kind] -= 1
            self._active[kind] += 1
            self._preempt()
            if kind == "light":
                shared_limit = max(LIGHT_LIMIT, self.capacity.cores * 2)
            else:
                shared_limit = self._limits.video if kind == "video" else self._limits.media
        machine = MachineSlot(kind, shared_limit)
        try:
            # предел на всю машину: у каждого процесса приёма загрузок свой распорядитель,
            # и без общего предела два процесса запускали бы вдвое больше обработки, чем выдерживают ядра
            machine.acquire()
            yield self
        finally:
            machine.release()
            with self._cond:
                self._active[kind] = max(0, self._active[kind] - 1)
                self._preempt()
                self._cond.notify_all()

    # ── вытеснение тяжёлых задач ──
    def register_heavy(self, proc) -> None:
        pid = getattr(proc, "pid", None)
        if not pid:
            return
        with self._cond:
            self._heavy[pid] = proc
            self._paused_total[pid] = 0.0
            self._ran_since[pid] = time.time()
            self._preempt()

    def unregister_heavy(self, proc) -> None:
        pid = getattr(proc, "pid", None)
        with self._cond:
            if pid in self._paused_at:
                self._signal(pid, False)
            for table in (self._heavy, self._paused_at, self._paused_total, self._ran_since):
                table.pop(pid, None)
            self._cond.notify_all()

    def paused_seconds(self, pid: int) -> float:
        with self._lock:
            total = self._paused_total.get(pid, 0.0)
            since = self._paused_at.get(pid)
            return total + (time.time() - since if since else 0.0)

    def is_paused(self, pid: int) -> bool:
        with self._lock:
            return pid in self._paused_at

    def _signal(self, pid: int, stop: bool) -> bool:
        try:
            if psutil is not None:
                proc = psutil.Process(pid)
                proc.suspend() if stop else proc.resume()
            else:
                import signal
                os.kill(pid, signal.SIGSTOP if stop else signal.SIGCONT)
            return True
        except Exception as e:
            logger.debug("Распорядитель: не удалось %s процесс %s: %s",
                         "приостановить" if stop else "возобновить", pid, e)
            return False

    def _preempt(self) -> None:
        if not self._heavy:
            return
        now = time.time()
        demand = (self._waiting["light"] + self._waiting["media"]
                  + self._active["light"] + self._active["media"])
        for pid in list(self._heavy):
            since = self._paused_at.get(pid)
            if since is not None:
                if demand == 0 or now - since >= PAUSE_MAX:
                    if self._signal(pid, False):
                        self._paused_total[pid] = self._paused_total.get(pid, 0.0) + now - since
                        self._paused_at.pop(pid, None)
                        self._ran_since[pid] = now
                continue
            if demand and now - self._ran_since.get(pid, 0.0) >= RUN_MIN:
                if self._signal(pid, True):
                    self._paused_at[pid] = now

    def tick(self) -> None:
        with self._cond:
            self._preempt()

    def snapshot(self) -> dict:
        with self._lock:
            s, l = self._sample, self._limits
            return {
                "cores": self.capacity.cores,
                "ram_total_mb": round(self.capacity.ram_total / MB),
                "ram_available_mb": round(s.mem_available / MB) if s.at else None,
                "ram_reserve_mb": round(self.capacity.ram_reserve / MB),
                "source": self.capacity.source,
                "cpu_busy": round(s.cpu_busy, 3),
                "cpu_foreign": round(s.foreign_cpu, 3),
                "loop_lag_ms": round(s.loop_lag * 1000),
                "limit_video": l.video, "limit_media": l.media, "threads": l.threads,
                "max_video": self.max_video, "max_media": self.max_media,
                "active": dict(self._active),
                "waiting": dict(self._waiting),
                "heavy": len(self._heavy), "paused": len(self._paused_at),
                "reason": l.reason,
                "psutil": bool(psutil),
                "measured": s.ok,
            }


_governor: Optional[Governor] = None
_governor_lock = threading.Lock()


class MachineSlot:
    """Одно из limit мест на всю машину: файлы-замки в /dev/shm, общие для всех процессов сервера.

    Место освобождается и при падении процесса — замок снимает система.
    Без fcntl (Windows) предел действует только внутри процесса, как раньше.
    """

    def __init__(self, kind: str, limit: int):
        self.kind = kind
        self.limit = max(1, int(limit or 1))
        self.fh = None

    @staticmethod
    def _folder() -> str:
        from .versions import _DIR
        folder = os.path.join(_DIR, "kovapp-slots-%s" % (os.getenv("SERVER_PORT") or "0"))
        os.makedirs(folder, exist_ok=True)
        return folder

    def acquire(self) -> None:
        try:
            import fcntl
        except ImportError:
            return
        folder = self._folder()
        delay = 0.05
        while True:
            for i in range(self.limit):
                fh = open(os.path.join(folder, "%s-%d.lock" % (self.kind, i)), "a+")
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.fh = fh
                    return
                except OSError:
                    fh.close()
            time.sleep(delay)
            delay = min(0.5, delay * 1.5)

    def release(self) -> None:
        if self.fh is None:
            return
        try:
            import fcntl
            fcntl.flock(self.fh, fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        finally:
            try:
                self.fh.close()
            except OSError:
                pass
            self.fh = None


def governor() -> Governor:
    """Общий распорядитель процесса; создаётся при первом обращении."""
    global _governor
    if _governor is None:
        with _governor_lock:
            if _governor is None:
                # Потолки вручную необязательны. Если настроек нет, работаем
                # без них: сорванный импорт не должен останавливать обработку
                # файлов — это ровно то, чем он однажды и обернулся.
                hard_media = hard_video = 0
                try:
                    from . import config as _cfg
                    hard_media = int(getattr(_cfg, "PROCESS_MAX_PARALLEL_HARD", 0) or 0)
                    hard_video = int(getattr(_cfg, "VIDEO_MAX_PARALLEL_HARD", 0) or 0)
                except Exception as e:
                    logger.warning("Распорядитель: настройки потолков недоступны (%s), решаю сам", e)
                g = Governor(hard_media=hard_media, hard_video=hard_video)
                g.start()
                logger.info("Распорядитель нагрузки: ядер %s (%s), память %s МБ, "
                            "потолок видео %s, фото %s",
                            g.capacity.cores, g.capacity.source,
                            round(g.capacity.ram_total / MB), g.max_video, g.max_media)
                _governor = g
    return _governor


def estimate_video_memory(width: int = 0, height: int = 0, bits: int = 8) -> int:
    pixels = max(1, (width or 1920) * (height or 1080))
    depth = 2 if (bits or 8) > 8 else 1
    return int((200 * MB + pixels / 1e6 * 12.6 * MB * depth) * 1.3)
