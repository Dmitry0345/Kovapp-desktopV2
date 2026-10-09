# -*- coding: utf-8 -*-

from typing import List, Optional, Dict, Literal
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey, Boolean, JSON
from sqlalchemy import desc, text, func, true, false
import threading
import time
from typing import Callable, Tuple
from sqlalchemy.pool import NullPool
from sqlalchemy.exc import OperationalError, DBAPIError, SQLAlchemyError

from ..backup.devops import AlertIncident, DevOpsNotifier, WATCHDOG_CONFIRM_FAILS, WATCHDOG_ENABLED, WATCHDOG_INTERVAL_DOWN_MAX, WATCHDOG_INTERVAL_OK, WATCHDOG_INTERVAL_SUSPECT, WATCHDOG_LATENCY_WARN, WATCHDOG_LIVENESS_TIMEOUT, WATCHDOG_RW_EVERY, _utc_iso, classify_db_error, watchdog_logger


class DatabaseWatchdog(threading.Thread):
    """
    Контроль живучести БД. Работает в отдельном потоке на изолированном
    подключении (NullPool), поэтому не отбирает соединения у рабочего пула и
    его собственные сбои не отравляют пул приложения.

    Экономия ресурсов: обычный опрос раз в 30 с (два тривиальных запроса),
    проверка записи — раз в N циклов, состояние пула читается из памяти
    процесса без обращения к БД. При подозрении на сбой интервал временно
    сокращается, при подтверждённом отказе — наоборот растёт, чтобы не
    добивать перегруженную базу.
    """

    def __init__(self, engines: Dict[str, object], pool_status_provider: Callable[[], Dict],
                 notifier: DevOpsNotifier):
        super().__init__(name="db-watchdog", daemon=True)
        self._engines = {k: v for k, v in engines.items() if v is not None}
        self._pool_status = pool_status_provider
        self.notifier = notifier
        self._stop_event = threading.Event()  # переименовано: Thread._stop занят внутри threading
        self._probe_engines: Dict[str, object] = {}
        self._flavor: Dict[str, str] = {}          # label -> mysql | mariadb
        self._cycle = 0
        self._fails: Dict[str, int] = {}           # label -> подряд неудачных проверок
        self._latency_ewma: Dict[str, float] = {}  # сглаженная латентность, для тренда
        self._state: Dict[str, Dict] = {}          # последний снимок по каждой БД
        self._check_lock = threading.Lock()
        self.status_overall = "unknown"

    # ── ИЗОЛИРОВАННОЕ ПОДКЛЮЧЕНИЕ ───────────────────────────────────────────
    def _connect_args(self, engine) -> Dict:
        driver = ""
        try:
            driver = engine.dialect.driver
        except Exception:
            pass
        t = int(max(1, WATCHDOG_LIVENESS_TIMEOUT))
        if driver in ("pymysql", "mysqldb", "mysqlclient"):
            return {"connect_timeout": t, "read_timeout": t, "write_timeout": t}
        if driver == "mysqlconnector":
            return {"connection_timeout": t}
        return {"connect_timeout": t}

    def _probe_engine(self, label: str):
        eng = self._probe_engines.get(label)
        if eng is not None:
            return eng
        src = self._engines[label]
        # future=False в SQLAlchemy 2.0 запрещён, а прежний except TypeError
        # его не ловил (летит ArgumentError) — вотчдог падал каждый цикл
        try:
            eng = create_engine(src.url, poolclass=NullPool, connect_args=self._connect_args(src),
                                pool_pre_ping=False)
        except Exception:
            eng = create_engine(src.url, poolclass=NullPool)
        self._probe_engines[label] = eng
        return eng

    def _detect_flavor(self, label: str, conn) -> str:
        if label in self._flavor:
            return self._flavor[label]
        try:
            v = str(conn.execute(text("SELECT VERSION()")).scalar() or "")
        except Exception:
            v = ""
        self._flavor[label] = "mariadb" if "mariadb" in v.lower() else "mysql"
        return self._flavor[label]

    def _liveness_sql(self, flavor: str) -> str:
        """Жёсткий таймаут на стороне СУБД в дополнение к таймауту сокета."""
        ms = int(WATCHDOG_LIVENESS_TIMEOUT * 1000)
        if flavor == "mariadb":
            return f"SET STATEMENT max_statement_time={WATCHDOG_LIVENESS_TIMEOUT} FOR SELECT 1"
        return f"SELECT /*+ MAX_EXECUTION_TIME({ms}) */ 1"

    # ── ПРОВЕРКИ ────────────────────────────────────────────────────────────
    def _check_database(self, label: str, do_rw: bool) -> Dict:
        report: Dict = {"label": label, "checked_at": _utc_iso(), "ok": False,
                        "liveness": False, "read_write": None, "connect_ms": None,
                        "latency_ms": None, "latency_ewma_ms": None, "degraded": False}
        engine = self._probe_engine(label)
        t0 = time.monotonic()
        try:
            with engine.connect() as conn:
                report["connect_ms"] = round((time.monotonic() - t0) * 1000, 1)
                flavor = self._detect_flavor(label, conn)

                # 1. Liveness: простейший запрос с жёстким таймаутом
                t1 = time.monotonic()
                conn.execute(text(self._liveness_sql(flavor))).scalar()
                latency = time.monotonic() - t1
                report["liveness"] = True
                report["latency_ms"] = round(latency * 1000, 1)

                # 3. Деградация отклика: сглаженное среднее показывает тренд, а не всплеск
                prev = self._latency_ewma.get(label, latency)
                ewma = prev * 0.7 + latency * 0.3
                self._latency_ewma[label] = ewma
                report["latency_ewma_ms"] = round(ewma * 1000, 1)
                report["degraded"] = latency > WATCHDOG_LATENCY_WARN

                # 2. Возможность записи: сначала дешёвые флаги, затем реальная транзакция
                if do_rw:
                    report["read_write"] = self._check_read_write(conn, report)
            report["ok"] = report["liveness"] and (report["read_write"] is not False) and not report["degraded"]
            return report
        except (OperationalError, DBAPIError, SQLAlchemyError) as e:
            info = classify_db_error(e)
            report["error"] = info
            return report
        except Exception as e:
            report["error"] = {"code": None, "name": "PYTHON_ERROR", "severity": "critical",
                               "category": "db_error", "title": "Сбой при проверке БД",
                               "explanation": "Проверка не смогла выполниться на уровне приложения.",
                               "causes": [], "actions": [], "raw": str(e)[:500]}
            return report

    def _check_read_write(self, conn, report: Dict) -> bool:
        """
        При переполнении диска или сбое репликации база читает, но не пишет.
        Флаги read_only ловят это мгновенно, временная таблица проверяет
        реальный путь записи.
        """
        try:
            row = conn.execute(text("SELECT @@read_only, @@innodb_read_only")).fetchone()
            if row and (str(row[0]) in ("1", "ON") or str(row[1]) in ("1", "ON")):
                report["readonly_flags"] = {"read_only": row[0], "innodb_read_only": row[1]}
                return False
        except Exception:
            pass
        try:
            conn.execute(text("CREATE TEMPORARY TABLE _healthcheck (id INT)"))
            conn.execute(text("INSERT INTO _healthcheck VALUES (1)"))
            conn.execute(text("DROP TABLE _healthcheck"))
            try:
                conn.commit()
            except Exception:
                pass
            return True
        except Exception as e:
            report["read_write_error"] = classify_db_error(e)
            try:
                conn.execute(text("DROP TABLE IF EXISTS _healthcheck"))
            except Exception:
                pass
            return False

    def _check_pool(self) -> Dict:
        """4. Голодание пула: данные берутся из памяти процесса, БД не трогаем."""
        out: Dict = {"ok": True, "pools": {}}
        try:
            pools = self._pool_status() or {}
        except Exception as e:
            return {"ok": True, "error": str(e), "pools": {}}
        for name, st in pools.items():
            if not isinstance(st, dict) or not st.get("available"):
                continue
            capacity = st.get("capacity")
            checked_out = st.get("checked_out")
            free = st.get("free")
            if capacity is None or checked_out is None:
                continue
            usage = checked_out / max(1, capacity)
            level = "ok"
            if free == 0 and checked_out >= capacity:
                level = "starved"
                out["ok"] = False
            elif usage >= 0.9:
                level = "near_limit"
            out["pools"][name] = {"checked_out": checked_out, "capacity": capacity,
                                  "free": free, "usage": round(usage, 2), "level": level}
        return out

    # ── ЦИКЛ И ЭСКАЛАЦИЯ ────────────────────────────────────────────────────
    def check_once(self, force_rw: bool = False) -> Dict:
        with self._check_lock:
            self._cycle += 1
            do_rw = force_rw or (self._cycle % max(1, WATCHDOG_RW_EVERY) == 1)
            snapshot: Dict = {"checked_at": _utc_iso(), "databases": {}, "pool": self._check_pool()}
            worst = "ok"
            for label in self._engines:
                report = self._check_database(label, do_rw)
                self._state[label] = report
                snapshot["databases"][label] = report
                worst = self._evaluate(label, report, worst)
            self._evaluate_pool(snapshot["pool"])
            snapshot["status"] = worst
            self.status_overall = worst
            return snapshot

    def _evaluate(self, label: str, report: Dict, worst: str) -> str:
        fails = self._fails.get(label, 0)
        if "error" in report or not report["liveness"]:
            fails += 1
            self._fails[label] = fails
            if fails >= WATCHDOG_CONFIRM_FAILS:
                info = report.get("error") or {}
                self.notifier.notify(AlertIncident(
                    key=f"db_unavailable:{label}",
                    severity=info.get("severity", "critical"),
                    category=info.get("category", "db_down"),
                    title=f"[{label}] {info.get('title', 'База данных не отвечает')}",
                    explanation=info.get("explanation", "База данных не отвечает на контрольный запрос."),
                    probable_causes=info.get("causes", []),
                    actions=info.get("actions", []) or ["Проверить статус сервиса СУБД",
                                                        "Смотреть error log СУБД", "Проверить /health/db"],
                    error_code=info.get("code"), error_name=info.get("name"),
                    raw_error=info.get("raw", ""),
                    metrics={"consecutive_fails": fails, "connect_ms": report.get("connect_ms")}))
            return "down"

        if report.get("read_write") is False:
            self._fails[label] = 0
            info = report.get("read_write_error") or {}
            self.notifier.notify(AlertIncident(
                key=f"db_readonly:{label}",
                severity="critical", category=info.get("category", "readonly"),
                title=f"[{label}] База читает, но не принимает запись",
                explanation=info.get("explanation",
                                     "Контрольная транзакция не прошла: сообщения и изменения задач сейчас "
                                     "не сохраняются, хотя интерфейс продолжает показывать старые данные."),
                probable_causes=info.get("causes", []) or ["Переполнен диск под datadir",
                                                           "Включён read_only / super_read_only",
                                                           "Сбой репликации перевёл узел в реплику"],
                actions=info.get("actions", []) or ["df -h на разделе datadir",
                                                    "SELECT @@read_only, @@super_read_only",
                                                    "Проверить свободное место и права на запись"],
                error_code=info.get("code"), error_name=info.get("name"), raw_error=info.get("raw", ""),
                metrics={"readonly_flags": report.get("readonly_flags")}))
            return "degraded" if worst == "ok" else worst
        self.notifier.resolve(f"db_readonly:{label}")

        if report.get("degraded"):
            self.notifier.notify(AlertIncident(
                key=f"db_latency:{label}", severity="warning", category="latency",
                title=f"[{label}] Отклик БД деградировал",
                explanation=f"Контрольный запрос выполняется {report['latency_ms']} мс при пороге "
                            f"{int(WATCHDOG_LATENCY_WARN * 1000)} мс. Пользователи ощущают это как зависание "
                            "чата и списков задач.",
                probable_causes=["Тяжёлые запросы или отчёты грузят процессор",
                                 "Исчерпан лимит дисковых операций (IOPS)",
                                 "Глобальные блокировки таблиц",
                                 "Своп из-за нехватки памяти"],
                actions=["SHOW FULL PROCESSLIST — найти долгие запросы",
                         "SHOW ENGINE INNODB STATUS — проверить блокировки",
                         "iostat/vmstat на хосте СУБД", "Проверить /health/db-pool"],
                metrics={"latency_ms": report["latency_ms"], "ewma_ms": report["latency_ewma_ms"]}))
            self._fails[label] = 0
            return "degraded" if worst == "ok" else worst

        if self._fails.get(label):
            self.notifier.resolve(f"db_unavailable:{label}", f"[{label}] База снова отвечает штатно.")
        self._fails[label] = 0
        self.notifier.resolve(f"db_latency:{label}")
        return worst

    def _evaluate_pool(self, pool_report: Dict):
        starved = [n for n, p in pool_report.get("pools", {}).items() if p.get("level") == "starved"]
        near = [n for n, p in pool_report.get("pools", {}).items() if p.get("level") == "near_limit"]
        if starved:
            self.notifier.notify(AlertIncident(
                key="db_pool_starvation", severity="critical", category="pool",
                title="Исчерпан пул соединений с БД",
                explanation="Все соединения заняты и свободных нет: новые запросы пользователей встают в "
                            "очередь и отваливаются по таймауту, хотя сама база жива.",
                probable_causes=["Соединения удерживаются дольше необходимого (утечка сессий)",
                                 "Долгие запросы держат соединения занятыми",
                                 "Всплеск нагрузки выше расчётного", "Пул меньше реальной потребности"],
                actions=["Смотреть /health/db-pool в динамике",
                         "SHOW FULL PROCESSLIST — найти зависшие соединения",
                         "Проверить, что все сессии закрываются (LazySession.release)",
                         "При устойчивой картине поднять POOL_SIZE и max_connections согласованно"],
                metrics=pool_report))
        elif near:
            self.notifier.notify(AlertIncident(
                key="db_pool_near_limit", severity="warning", category="pool",
                title="Пул соединений близок к пределу",
                explanation="Занято более 90% соединений. Это ещё не отказ, но запас исчерпан и любой всплеск "
                            "нагрузки приведёт к очереди.",
                actions=["Проверить /health/db-pool", "Найти долгие запросы"], metrics=pool_report))
        else:
            self.notifier.resolve("db_pool_starvation", "Пул соединений освободился.")
            self.notifier.resolve("db_pool_near_limit")

    def run(self):
        if not self._engines:
            watchdog_logger.error("Вотчдог не запущен: нет пригодных подключений")
            return
        watchdog_logger.info("Вотчдог БД запущен, интервал %s с", WATCHDOG_INTERVAL_OK)
        down_streak = 0
        while not self._stop_event.is_set():
            try:
                snap = self.check_once()
                status = snap.get("status")
                if status == "down":
                    down_streak += 1
                    # при подтверждённом отказе интервал растёт, чтобы не добивать базу
                    delay = (WATCHDOG_INTERVAL_SUSPECT if down_streak <= WATCHDOG_CONFIRM_FAILS
                             else min(WATCHDOG_INTERVAL_DOWN_MAX, WATCHDOG_INTERVAL_SUSPECT * 2 ** (down_streak - 2)))
                elif status == "degraded":
                    down_streak = 0
                    delay = WATCHDOG_INTERVAL_SUSPECT   # подозрение: опрашиваем чаще для подтверждения
                else:
                    down_streak = 0
                    delay = WATCHDOG_INTERVAL_OK
            except Exception as e:
                watchdog_logger.error("Сбой цикла вотчдога: %s", e)
                delay = WATCHDOG_INTERVAL_OK
            self._stop_event.wait(delay)

    def stop(self):
        self._stop_event.set()
        for eng in self._probe_engines.values():
            try:
                eng.dispose()
            except Exception:
                pass

    def snapshot(self) -> Dict:
        """Кешированное состояние: эндпоинт не создаёт нагрузки на БД."""
        return {
            "status": self.status_overall,
            "enabled": WATCHDOG_ENABLED,
            "interval_sec": {"ok": WATCHDOG_INTERVAL_OK, "suspect": WATCHDOG_INTERVAL_SUSPECT},
            "thresholds": {"liveness_timeout_sec": WATCHDOG_LIVENESS_TIMEOUT,
                           "latency_warn_sec": WATCHDOG_LATENCY_WARN,
                           "confirm_fails": WATCHDOG_CONFIRM_FAILS},
            "databases": self._state,
            "pool": self._check_pool(),
            "open_incidents": self.notifier.open_incidents(),
            "notifications_channel": "настроен" if self.notifier.has_transport else "не настроен (outbox)",
        }
