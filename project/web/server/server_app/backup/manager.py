# -*- coding: utf-8 -*-

import os
import subprocess
import re
from typing import List, Optional, Dict, Literal
import gzip
from pathlib import Path
import shutil
import json
import logging
import threading
import time
from dataclasses import dataclass, asdict, field as dc_field
from logging.handlers import RotatingFileHandler
from typing import Callable, Tuple

from sqlalchemy import text

from ..backup.devops import AlertIncident, BACKUP_AUTO_TUNE_DURABILITY, BACKUP_BINLOG_DIR, BACKUP_COMPRESSOR_MEM_MB, BACKUP_DISK_SAFETY, BACKUP_DUMPER_MEM_MB, BACKUP_DUMPS_DIR, BACKUP_ENABLED, BACKUP_HOUR, BACKUP_INCIDENTS_DIR, BACKUP_KEEP_DUMPS, BACKUP_KEEP_GENERATIONS, BACKUP_LOG_DIR, BACKUP_MINUTE, BACKUP_MIN_FREE_MB, BACKUP_PIPE_CHUNK, BACKUP_ROOT, BACKUP_STATE_DIR, BACKUP_STATE_FILE, BACKUP_TIMEOUT_SEC, BACKUP_ZSTD_LEVEL, BACKUP_ZSTD_THREADS, BINLOG_RESTART_BACKOFF, BINLOG_SERVER_ID, BINLOG_STREAM_ENABLED, DevOpsNotifier, _COORD_RE, _LOGICAL_END_MARKERS, _atomic_write_json, _binlog_index, _child_env, _child_limits, _find_binary, _fsync_dir, _sha256_file, _utc_iso, _utc_stamp, backup_logger, register_tool_dir, watchdog_logger


@dataclass
class BackupServerTarget:
    """Один физический сервер СУБД и список живущих на нём баз."""
    key: str
    host: str
    port: int
    user: str
    password: str
    databases: List[str] = dc_field(default_factory=list)


class BackupToolbox:
    """Определение доступных бинарников и поддерживаемых ими флагов."""

    def __init__(self):
        self._dump_help: Optional[str] = None
        self._binlog_help: Optional[str] = None
        self.has_pyzstd = False
        try:
            import zstandard  # noqa: F401
            self.has_pyzstd = True
        except ImportError:
            pass
        self.rescan()

    def rescan(self):
        """Повторный поиск: каталог утилит мог стать известен только после
        подключения к БД (basedir), а PATH у службы часто пустой."""
        self.dump = _find_binary("mysqldump", "mariadb-dump")
        self.binlog = _find_binary("mysqlbinlog", "mariadb-binlog")
        self.client = _find_binary("mysql", "mariadb")
        self.zstd = _find_binary("zstd")
        self._dump_help = None
        self._binlog_help = None
        self._zstd_help = None
        return self

    def _help(self, binary: Optional[str]) -> str:
        if not binary:
            return ""
        try:
            p = subprocess.run([binary, "--help"], capture_output=True, text=True, timeout=20)
            return (p.stdout or "") + (p.stderr or "")
        except Exception:
            return ""

    def zstd_supports(self, flag: str) -> bool:
        # полный список ключей zstd — в «-H» (короткая справка --help их не перечисляет); asyncio — как «--[no-]asyncio»
        if getattr(self, "_zstd_help", None) is None:
            try:
                p = subprocess.run([self.zstd, "-H"], capture_output=True, text=True, timeout=20) if self.zstd else None
                self._zstd_help = ((p.stdout or "") + (p.stderr or "")) if p else ""
            except Exception:
                self._zstd_help = ""
        return flag in self._zstd_help or flag.replace("--no-", "--[no-]") in self._zstd_help

    def dump_supports(self, flag: str) -> bool:
        if self._dump_help is None:
            self._dump_help = self._help(self.dump)
        return flag in self._dump_help

    def binlog_supports(self, flag: str) -> bool:
        if self._binlog_help is None:
            self._binlog_help = self._help(self.binlog)
        return flag in self._binlog_help

    @property
    def archive_ext(self) -> str:
        """Расширение выбирается заранее, чтобы проверка целостности знала формат."""
        return ".zst" if (self.zstd or self.has_pyzstd) else ".gz"


class BinlogArchiver(threading.Thread):
    """
    Непрерывная выгрузка журнала транзакций: mysqlbinlog подключается к серверу
    как реплика и пишет сырые binlog-файлы в текущее поколение по мере записи.
    При обрыве перезапускается с последнего файла, поэтому незакрытый хвост
    перекачивается заново и отключение питания не оставляет дыр.
    """

    def __init__(self, target: BackupServerTarget, tools: BackupToolbox,
                 gen_dir_getter, defaults_file_getter, notifier: DevOpsNotifier):
        super().__init__(name=f"binlog-{target.key}", daemon=True)
        self.target = target
        self.tools = tools
        self._gen_dir = gen_dir_getter
        self._defaults = defaults_file_getter
        self.notifier = notifier
        self._stop_event = threading.Event()  # переименовано: Thread._stop занят внутри threading
        self._restart = threading.Event()
        self._proc: Optional[subprocess.Popen] = None
        self.last_error: Optional[str] = None
        self.running = False
        self.started_from: Optional[str] = None

    def request_restart(self):
        self._restart.set()
        self._kill_proc()

    def stop(self):
        self._stop_event.set()
        self._kill_proc()

    def _kill_proc(self):
        p = self._proc
        if p and p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=10)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

    def _server_binlogs(self) -> List[str]:
        if not self.tools.client:
            return []
        try:
            out = subprocess.run([self.tools.client, f"--defaults-extra-file={self._defaults()}",
                                  "-N", "-B", "-e", "SHOW BINARY LOGS;"],
                                 capture_output=True, text=True, timeout=30)
            if out.returncode != 0:
                self.last_error = (out.stderr or "").strip()[:400]
                return []
            return [line.split("\t")[0] for line in out.stdout.splitlines() if line.strip()]
        except Exception as e:
            self.last_error = str(e)
            return []

    def _resume_point(self, gen_dir: Path) -> Optional[str]:
        """Продолжаем с последнего скачанного файла, иначе с самого раннего на сервере."""
        local = sorted([p.name for p in gen_dir.glob("*") if _binlog_index(p.name) >= 0],
                       key=_binlog_index)
        available = self._server_binlogs()
        if not available:
            return None
        if local and local[-1] in available:
            return local[-1]
        if local:
            backup_logger.warning("Файл %s уже удалён на сервере, старт с самого раннего доступного", local[-1])
        return available[0]

    def run(self):
        attempt = 0
        while not self._stop_event.is_set():
            self._restart.clear()
            gen_dir = self._gen_dir()
            gen_dir.mkdir(parents=True, exist_ok=True)
            start_file = self._resume_point(gen_dir)
            if not start_file:
                self.running = False
                if attempt == 0:
                    backup_logger.warning("Список binlog недоступен (%s). Проверьте log_bin и права REPLICATION.",
                                          self.last_error)
                attempt = min(attempt + 1, len(BINLOG_RESTART_BACKOFF) - 1)
                self._stop_event.wait(BINLOG_RESTART_BACKOFF[attempt])
                continue

            cmd = [self.tools.binlog, f"--defaults-extra-file={self._defaults()}",
                   "--read-from-remote-server", "--raw", "--stop-never",
                   f"--result-file={gen_dir}{os.sep}"]
            if self.tools.binlog_supports("--connection-server-id"):
                cmd.append(f"--connection-server-id={BINLOG_SERVER_ID}")
            elif self.tools.binlog_supports("--stop-never-slave-server-id"):
                cmd.append(f"--stop-never-slave-server-id={BINLOG_SERVER_ID}")
            if self.tools.binlog_supports("--verify-binlog-checksum"):
                cmd.append("--verify-binlog-checksum")
            cmd.append(start_file)
            self.started_from = start_file

            try:
                self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                              start_new_session=True, preexec_fn=_child_limits(64), env=_child_env())
                self.running = True
                backup_logger.info("Архивация binlog запущена с %s -> %s", start_file, gen_dir)
                _, err = self._proc.communicate()
                self.running = False
                if self._stop_event.is_set():
                    break
                self.last_error = (err or b"").decode("utf-8", "replace").strip()[:400]
                if self._restart.is_set():
                    attempt = 0
                    continue
                attempt = min(attempt + 1, len(BINLOG_RESTART_BACKOFF) - 1)
                backup_logger.error("Поток binlog оборвался (%s), перезапуск через %s с",
                                    self.last_error or self._proc.returncode, BINLOG_RESTART_BACKOFF[attempt])
                if attempt >= 3:
                    self.notifier.notify(AlertIncident(
                        key="binlog_stream_down", severity="critical", category="pitr",
                        title="Прервана непрерывная архивация журнала транзакций",
                        explanation="Поток mysqlbinlog не удерживается. Ночной дамп продолжает делаться, но "
                                    "восстановление на точку отказа сейчас невозможно: изменения после "
                                    "последнего дампа не архивируются.",
                        probable_causes=["Выключен log_bin на сервере СУБД",
                                         "У пользователя нет привилегии REPLICATION SLAVE",
                                         "СУБД недоступна или перегружена",
                                         "Конфликт server-id с другой репликой"],
                        actions=["SHOW VARIABLES LIKE 'log_bin' — должно быть ON",
                                 "GRANT REPLICATION SLAVE, REPLICATION CLIENT пользователю бэкапа",
                                 f"Проверить уникальность BINLOG_SERVER_ID={BINLOG_SERVER_ID}",
                                 f"Смотреть {BACKUP_LOG_DIR / 'backup.log'}"],
                        raw_error=self.last_error or ""))
                self._stop_event.wait(BINLOG_RESTART_BACKOFF[attempt])
            except Exception as e:
                self.running = False
                self.last_error = str(e)
                attempt = min(attempt + 1, len(BINLOG_RESTART_BACKOFF) - 1)
                self._stop_event.wait(BINLOG_RESTART_BACKOFF[attempt])
        self.running = False


class BackupManager:
    """Оркестратор: ночной дамп, архив binlog, проверка целостности, ротация."""

    def __init__(self, engines: Dict[str, object], notifier: DevOpsNotifier):
        self._raw_engines = engines
        self.tools = BackupToolbox()
        self.notifier = notifier
        self.targets: Dict[str, BackupServerTarget] = {}
        self.archivers: Dict[str, BinlogArchiver] = {}
        self._defaults_files: Dict[str, Path] = {}
        self._gen_dirs: Dict[str, Path] = {}
        self._run_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self.state: Dict = {}
        self.last_result: Dict = {}
        self._started = False

    def _discover_tool_dirs(self):
        """Спрашиваем у самой СУБД, где она установлена.

        Самый надёжный способ: PATH у службы часто пустой, а путь установки
        различается на каждой машине. basedir указывает на каталог сервера,
        рядом с ним лежит bin/ с mysqldump и mysqlbinlog.
        """
        found = False
        for label, engine in self._raw_engines.items():
            if engine is None:
                continue
            try:
                with engine.connect() as conn:
                    basedir = conn.execute(text("SELECT @@basedir")).scalar()
            except Exception as e:
                backup_logger.debug("Не удалось получить basedir у БД %s: %s", label, e)
                continue
            if register_tool_dir(basedir):
                backup_logger.info("Каталог утилит СУБД: %s", basedir)
                found = True
        if found:
            self.tools.rescan()
        backup_logger.info("Утилиты бэкапа: mysqldump=%s, mysqlbinlog=%s, mysql=%s, zstd=%s, python-zstandard=%s",
                           self.tools.dump, self.tools.binlog, self.tools.client,
                           self.tools.zstd, self.tools.has_pyzstd)

    # ── ЖИЗНЕННЫЙ ЦИКЛ ──────────────────────────────────────────────────────
    def start(self, scheduler=None):
        if not BACKUP_ENABLED:
            backup_logger.warning("Резервное копирование отключено (BACKUP_ENABLED=0)")
            return
        try:
            self._ensure_dirs()
            self._setup_logging()
            self._discover_tool_dirs()
            self._build_targets()
        except Exception as e:
            backup_logger.error("Подсистема бэкапа не инициализирована: %s", e)
            return
        if not self.targets:
            backup_logger.error("Параметры подключения к БД не определены — бэкап не запущен")
            return
        if not self.tools.dump or not (self.tools.zstd or self.tools.has_pyzstd):
            self._alert_missing_tools()

        self.state = self._load_state()
        unclean = not self.state.get("clean_shutdown", True)
        self.state["clean_shutdown"] = False
        self.state["last_start"] = _utc_iso()
        self._save_state()

        self._prepare_durability()
        self._recover_after_crash(deep=unclean)

        if BINLOG_STREAM_ENABLED and self.tools.binlog:
            for key in self.targets:
                self._gen_dirs[key] = self._current_generation(key, create=True)
                arch = BinlogArchiver(self.targets[key], self.tools,
                                      gen_dir_getter=(lambda k=key: self._gen_dirs[k]),
                                      defaults_file_getter=(lambda k=key: self._defaults_files[k]),
                                      notifier=self.notifier)
                self.archivers[key] = arch
                arch.start()

        if scheduler is not None:
            scheduler.add_job(self.run_backup, "cron", hour=BACKUP_HOUR, minute=BACKUP_MINUTE,
                              id="db_backup_nightly", replace_existing=True,
                              max_instances=1, coalesce=True, misfire_grace_time=3600)
            backup_logger.info("Ночной бэкап запланирован на %02d:%02d", BACKUP_HOUR, BACKUP_MINUTE)
        self._started = True

    def stop(self):
        for a in self.archivers.values():
            a.stop()
        try:
            with self._state_lock:
                self.state["clean_shutdown"] = True
                self.state["last_stop"] = _utc_iso()
                self._save_state()
        except Exception:
            pass
        for path in self._defaults_files.values():
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    def _ensure_dirs(self):
        for d in (BACKUP_ROOT, BACKUP_DUMPS_DIR, BACKUP_BINLOG_DIR, BACKUP_STATE_DIR,
                  BACKUP_INCIDENTS_DIR, BACKUP_LOG_DIR):
            d.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(BACKUP_ROOT, 0o700)
        except Exception:
            pass

    def _setup_logging(self):
        if any(isinstance(h, RotatingFileHandler) for h in backup_logger.handlers):
            return
        h = RotatingFileHandler(BACKUP_LOG_DIR / "backup.log", maxBytes=5 * 1024 * 1024,
                                backupCount=3, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        for lg in (backup_logger, watchdog_logger):
            lg.addHandler(h)
            lg.setLevel(logging.INFO)

    # ── ПАРАМЕТРЫ ПОДКЛЮЧЕНИЯ ───────────────────────────────────────────────
    def _build_targets(self):
        for label, engine in self._raw_engines.items():
            if engine is None:
                continue
            try:
                url = engine.url
                backend = url.get_backend_name()
            except Exception:
                continue
            if backend not in ("mysql", "mariadb"):
                backup_logger.error("Диалект %s не поддерживается этим модулем (ожидается MySQL/MariaDB)", backend)
                continue
            host = url.host or "127.0.0.1"
            port = int(url.port or 3306)
            key = f"{host}:{port}"
            t = self.targets.get(key)
            if t is None:
                t = BackupServerTarget(key=key, host=host, port=port,
                                       user=url.username or "root", password=url.password or "")
                self.targets[key] = t
                self._defaults_files[key] = self._write_defaults_file(t)
            if url.database and url.database not in t.databases:
                t.databases.append(url.database)
        backup_logger.info("Цели резервного копирования: %s", {k: v.databases for k, v in self.targets.items()})

    def _write_defaults_file(self, target: BackupServerTarget) -> Path:
        """Пароль не уходит в argv (виден в ps) — только через файл с правами 0600."""
        path = BACKUP_STATE_DIR / f".my-{target.host}-{target.port}.cnf"
        content = (f"[client]\nhost={target.host}\nport={target.port}\n"
                   f"user={target.user}\npassword=\"{target.password}\"\n")
        fd = os.open(str(path), os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def _sql(self, key: str, sql: str, timeout: int = 30) -> List[List[str]]:
        if not self.tools.client:
            return []
        try:
            p = subprocess.run([self.tools.client, f"--defaults-extra-file={self._defaults_files[key]}",
                                "-N", "-B", "-e", sql],
                               capture_output=True, text=True, timeout=timeout)
            if p.returncode != 0:
                backup_logger.warning("SQL '%s' завершился ошибкой: %s", sql[:60], (p.stderr or "").strip()[:300])
                return []
            return [line.split("\t") for line in p.stdout.splitlines() if line.strip()]
        except Exception as e:
            backup_logger.warning("SQL '%s' не выполнен: %s", sql[:60], e)
            return []

    # ── НАДЁЖНОСТЬ ЗАПИСИ ЖУРНАЛА ───────────────────────────────────────────
    def _prepare_durability(self):
        """
        Проверяем, что журнал транзакций вообще ведётся, и включаем режим, при
        котором внезапное отключение питания не съедает подтверждённые транзакции.
        """
        for key in self.targets:
            rows = self._sql(key, "SELECT @@log_bin, @@sync_binlog, @@innodb_flush_log_at_trx_commit;")
            if not rows:
                continue
            log_bin, sync_binlog, flush_trx = (rows[0] + ["", "", ""])[:3]
            if str(log_bin) not in ("1", "ON"):
                self._write_recommended_cnf()
                self.notifier.notify(AlertIncident(
                    key=f"binlog_disabled:{key}", severity="critical", category="pitr",
                    title=f"На сервере {key} выключен журнал транзакций (log_bin=OFF)",
                    explanation="Без binlog восстановление возможно только на момент ночного дампа: всё, что "
                                "сотрудники написали за день, при крахе будет потеряно. Включается правкой "
                                "конфигурации и рестартом СУБД.",
                    probable_causes=["В my.cnf не заданы log_bin / server_id"],
                    actions=[f"Применить готовый конфиг: {BACKUP_STATE_DIR / 'recommended-my.cnf'}",
                             "Перезапустить СУБД в согласованное окно",
                             "Выдать пользователю бэкапа REPLICATION SLAVE, REPLICATION CLIENT"]))
                continue
            if BACKUP_AUTO_TUNE_DURABILITY and (str(sync_binlog) != "1" or str(flush_trx) != "1"):
                self._sql(key, "SET GLOBAL sync_binlog=1; SET GLOBAL innodb_flush_log_at_trx_commit=1;")
                backup_logger.info("На %s включён durable-режим журнала (sync_binlog=1, flush_at_trx_commit=1)", key)

    def _write_recommended_cnf(self):
        (BACKUP_STATE_DIR / "recommended-my.cnf").write_text(
            "[mysqld]\n"
            "server_id                       = 1\n"
            "log_bin                         = /var/lib/mysql/binlog/mysql-bin\n"
            "binlog_format                   = ROW\n"
            "binlog_row_image                = FULL\n"
            "binlog_expire_logs_seconds      = 259200\n"
            "sync_binlog                     = 1\n"
            "innodb_flush_log_at_trx_commit  = 1\n"
            "binlog_checksum                 = CRC32\n"
            "max_binlog_size                 = 256M\n", encoding="utf-8")

    # ── СОСТОЯНИЕ ───────────────────────────────────────────────────────────
    def _load_state(self) -> Dict:
        default = {"version": 1, "clean_shutdown": True, "dumps": [], "history": []}
        if not BACKUP_STATE_FILE.exists():
            return default
        try:
            data = json.loads(BACKUP_STATE_FILE.read_text(encoding="utf-8"))
            for k, v in default.items():
                data.setdefault(k, v)
            return data
        except Exception as e:
            backup_logger.error("Состояние повреждено (%s), пересобираю по содержимому диска", e)
            default["dumps"] = self._scan_dump_metas()
            return default

    def _scan_dump_metas(self) -> List[Dict]:
        metas = []
        for p in BACKUP_DUMPS_DIR.glob("*.meta.json"):
            try:
                metas.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                continue
        return sorted(metas, key=lambda m: m.get("created_at", ""))

    def _save_state(self):
        with self._state_lock:
            _atomic_write_json(BACKUP_STATE_FILE, self.state)

    # ── ПОКОЛЕНИЯ BINLOG ────────────────────────────────────────────────────
    def _server_dir(self, key: str) -> Path:
        return BACKUP_BINLOG_DIR / key.replace(":", "_")

    def _current_generation(self, key: str, create: bool = False) -> Path:
        root = self._server_dir(key)
        root.mkdir(parents=True, exist_ok=True)
        gens = sorted([p for p in root.glob("gen-*") if p.is_dir()])
        if gens:
            return gens[-1]
        gen = root / f"gen-{_utc_stamp()}"
        if create:
            gen.mkdir(parents=True, exist_ok=True)
        return gen

    def _open_new_generation(self, key: str) -> Path:
        gen = self._server_dir(key) / f"gen-{_utc_stamp()}"
        gen.mkdir(parents=True, exist_ok=True)
        self._gen_dirs[key] = gen
        arch = self.archivers.get(key)
        if arch:
            arch.request_restart()
        return gen

    def _generation_last_index(self, gen: Path) -> int:
        idx = [_binlog_index(p.name) for p in gen.glob("*") if _binlog_index(p.name) >= 0]
        return max(idx) if idx else -1

    # ── ПРОВЕРКА ЦЕЛОСТНОСТИ АРХИВА ЖУРНАЛОВ ────────────────────────────────
    def verify_binlog_archive(self, key: str, deep: bool = False) -> Dict:
        """
        При обрыве питания последний журнал почти всегда обрезан. Тогда находим
        позицию последнего целого события и сохраняем восстановимый префикс,
        чтобы накатить максимум возможного, а повреждённый хвост отделяем.
        """
        result = {"checked": 0, "ok": 0, "repaired": [], "corrupt": [], "checked_at": _utc_iso()}
        if not self.tools.binlog:
            result["error"] = "mysqlbinlog не найден"
            return result
        for gen in sorted([p for p in self._server_dir(key).glob("gen-*") if p.is_dir()]):
            files = sorted([p for p in gen.glob("*") if _binlog_index(p.name) >= 0],
                           key=lambda p: _binlog_index(p.name))
            if not files:
                continue
            for f in (files if deep else files[-1:]):
                result["checked"] += 1
                ok, last_pos, err = self._scan_binlog(f)
                if ok:
                    result["ok"] += 1
                    continue
                backup_logger.error("Повреждён журнал %s: %s (последняя целая позиция %s)", f.name, err, last_pos)
                size = f.stat().st_size
                if 0 < last_pos < size:
                    recovered = f.with_name(f.name + ".recovered")
                    with open(f, "rb") as src, open(recovered, "wb") as dst:
                        remaining = last_pos
                        while remaining > 0:
                            chunk = src.read(min(BACKUP_PIPE_CHUNK, remaining))
                            if not chunk:
                                break
                            dst.write(chunk)
                            remaining -= len(chunk)
                        dst.flush()
                        os.fsync(dst.fileno())
                    f.rename(f.with_name(f.name + ".corrupt"))
                    result["repaired"].append({"file": f.name, "recovered_bytes": last_pos})
                    arch = self.archivers.get(key)
                    if arch:
                        arch.request_restart()  # целый файл будет перекачан с сервера, если он там ещё есть
                else:
                    result["corrupt"].append(f.name)
        if result["repaired"] or result["corrupt"]:
            self.notifier.notify(AlertIncident(
                key=f"binlog_corrupt:{key}",
                severity="critical" if result["corrupt"] else "warning", category="pitr",
                title=f"Обнаружены повреждённые журналы транзакций на {key}",
                explanation="Скорее всего сервер был выключен некорректно (питание или OOM). Целая часть "
                            "журнала сохранена и пригодна для наката, повреждённый хвост отделён в .corrupt.",
                probable_causes=["Внезапное отключение питания", "Аварийное завершение процесса СУБД",
                                 "Проблемы файловой системы на диске бэкапов"],
                actions=["Проверить файлы .recovered в архиве журналов",
                         "Убедиться, что sync_binlog=1 на сервере СУБД",
                         "Проверить SMART и свободное место на диске бэкапов"],
                metrics=result))
        return result

    def _scan_binlog(self, path: Path) -> Tuple[bool, int, str]:
        """Возвращает (целостность, позиция последнего целого события, текст ошибки)."""
        cmd = [self.tools.binlog, "--force-if-open"]
        if self.tools.binlog_supports("--verify-binlog-checksum"):
            cmd.append("--verify-binlog-checksum")
        cmd.append(str(path))
        last_pos = 0
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 preexec_fn=_child_limits(96), env=_child_env())
            get_err = self._drain(p.stderr)
            for raw in p.stdout:
                m = re.search(rb"end_log_pos\s+(\d+)", raw)
                if m:
                    last_pos = int(m.group(1))
            err = get_err().decode("utf-8", "replace")
            p.stdout.close()
            p.wait(timeout=120)
            return p.returncode == 0, last_pos, err.strip()[:300]
        except Exception as e:
            return False, last_pos, str(e)

    # ── ВОССТАНОВЛЕНИЕ ПОСЛЕ СБОЯ ───────────────────────────────────────────
    def _recover_after_crash(self, deep: bool):
        for part in BACKUP_DUMPS_DIR.glob("*.part"):
            backup_logger.warning("Удаляю незавершённый дамп после сбоя: %s", part.name)
            part.unlink(missing_ok=True)
        if deep:
            backup_logger.warning("Предыдущая остановка была некорректной — глубокая проверка архива журналов")
        for key in self.targets:
            self.verify_binlog_archive(key, deep=deep)
        last = self._latest_dump()
        if last and Path(last["path"]).exists():
            check = self._verify_dump(Path(last["path"]))
            if not check.get("frame_ok"):
                self.notifier.notify(AlertIncident(
                    key="last_dump_broken", severity="critical", category="backup",
                    title="Последний дамп не проходит проверку целостности",
                    explanation="Архив повреждён, восстановиться из него не получится. Журналы транзакций "
                                "удалять нельзя до появления нового корректного дампа.",
                    actions=["Запустить внеочередной бэкап: POST /admin/backup/run",
                             "Проверить диск бэкапов (свободное место, SMART)"],
                    metrics=check))

    # ── ОСНОВНОЙ ЦИКЛ ───────────────────────────────────────────────────────
    def run_backup(self, reason: str = "cron") -> Dict:
        if not self._run_lock.acquire(blocking=False):
            backup_logger.warning("Бэкап уже выполняется, запуск (%s) пропущен", reason)
            return {"status": "skipped", "reason": "already_running"}
        started = time.time()
        summary = {"status": "ok", "reason": reason, "started_at": _utc_iso(), "servers": {}}
        try:
            for key, target in self.targets.items():
                summary["servers"][key] = self._backup_server(key, target)
            failed = [k for k, v in summary["servers"].items() if v.get("status") != "ok"]
            summary["status"] = "ok" if not failed else "failed"
        except Exception as e:
            backup_logger.exception("Бэкап аварийно завершился")
            summary["status"] = "failed"
            summary["error"] = str(e)
            self.notifier.notify(AlertIncident(
                key="backup_failed", severity="critical", category="backup",
                title="Ночное резервное копирование не выполнено",
                explanation="Процесс упал с необработанной ошибкой, актуальной копии за сегодня нет.",
                raw_error=str(e),
                actions=[f"Смотреть {BACKUP_LOG_DIR / 'backup.log'}",
                         "Запустить вручную: POST /admin/backup/run"]))
        finally:
            summary["duration_sec"] = round(time.time() - started, 1)
            summary["finished_at"] = _utc_iso()
            self.last_result = summary
            with self._state_lock:
                self.state.setdefault("history", []).append(summary)
                self.state["history"] = self.state["history"][-20:]
                self._save_state()
            self._run_lock.release()
        if summary["status"] == "ok":
            self.notifier.resolve("backup_failed", "Резервное копирование снова проходит успешно.")
        return summary

    def trigger_async(self, reason: str = "manual") -> bool:
        if self._run_lock.locked():
            return False
        threading.Thread(target=self.run_backup, args=(reason,), daemon=True, name="backup-manual").start()
        return True

    def _backup_server(self, key: str, target: BackupServerTarget) -> Dict:
        res: Dict = {"status": "failed", "databases": list(target.databases)}
        if not self._check_disk_space():
            res["error"] = "недостаточно места на диске бэкапов"
            return res
        self._warn_non_transactional(key, target)

        # Ротация журнала до дампа: новое поколение начинается ровно с нового файла
        self._sql(key, "FLUSH BINARY LOGS;")
        time.sleep(2)
        prev_gen = self._gen_dirs.get(key) or self._current_generation(key, create=True)
        if self.archivers.get(key):
            self._open_new_generation(key)

        pre_coord = self._show_master_status(key)
        base = "-".join(target.databases) or "all"
        final_path = BACKUP_DUMPS_DIR / f"{base}-{_utc_stamp()}.sql{self.tools.archive_ext}"
        part_path = final_path.with_name(final_path.name + ".part")

        ok, dump_info = self._dump_and_compress(key, target, part_path)
        if not ok:
            part_path.unlink(missing_ok=True)
            res["error"] = dump_info.get("error", "dump failed")
            self.notifier.notify(AlertIncident(
                key=f"dump_failed:{key}", severity="critical", category="backup",
                title=f"Не удалось снять дамп базы на {key}",
                explanation="Ночная копия не создана. Журналы транзакций НЕ удаляются, поэтому данные пока "
                            "защищены, но при потере сервера восстановление обопрётся на предыдущий дамп.",
                raw_error=str(dump_info.get("error", ""))[:600],
                probable_causes=["Нет прав на часть таблиц", "СУБД недоступна или перегружена",
                                 "Нет места на диске бэкапов", "Не найден mysqldump или zstd"],
                actions=[f"Проверить {BACKUP_LOG_DIR / 'backup.log'}",
                         f"Проверить свободное место на {BACKUP_ROOT}"]))
            return res

        os.replace(part_path, final_path)
        _fsync_dir(BACKUP_DUMPS_DIR)

        verify = self._verify_dump(final_path)
        coord = dump_info.get("coord") or pre_coord
        size = final_path.stat().st_size
        meta = {
            "path": str(final_path), "file": final_path.name, "server": key,
            "databases": list(target.databases), "created_at": _utc_iso(),
            "size_compressed": size, "size_raw": verify.get("raw_size"),
            "ratio": round((verify.get("raw_size") or 0) / max(1, size), 2),
            "sha256": _sha256_file(final_path),
            "compressor": dump_info.get("compressor"),
            "binlog_file": coord[0] if coord else None,
            "binlog_pos": coord[1] if coord else None,
            "generation": prev_gen.name,
            "verify": verify,
            "verified": bool(verify.get("frame_ok") and verify.get("logical_complete")),
        }
        _atomic_write_json(BACKUP_DUMPS_DIR / (final_path.name + ".meta.json"), meta)

        if not meta["verified"]:
            res["error"] = "дамп не прошёл проверку целостности"
            res["meta"] = meta
            self.notifier.notify(AlertIncident(
                key=f"dump_unverified:{key}", severity="critical", category="backup",
                title=f"Дамп {final_path.name} не прошёл проверку",
                explanation="Архив создан, но целостность не подтверждена. Журналы транзакций оставлены на "
                            "месте — удалять их в таком состоянии нельзя.",
                metrics=verify,
                actions=["Запустить внеочередной бэкап", "Проверить диск и оперативную память сервера"]))
            return res

        # Чистка журналов и старых копий — строго после подтверждённой проверки
        with self._state_lock:
            self.state.setdefault("dumps", []).append(meta)
            self.state["dumps"] = self.state["dumps"][-50:]
            self._save_state()

        res.update({"status": "ok", "meta": meta,
                    "purged_binlogs": self._purge_binlogs(key, meta),
                    "removed_dumps": self._rotate_dumps()})
        backup_logger.info("Бэкап %s готов: %s МБ, сжатие x%s, проверка пройдена",
                           key, round(size / 1048576, 1), meta["ratio"])
        return res

    def _show_master_status(self, key: str) -> Optional[Tuple[str, int]]:
        rows = self._sql(key, "SHOW MASTER STATUS;")
        if rows and len(rows[0]) >= 2:
            try:
                return rows[0][0], int(rows[0][1])
            except Exception:
                return None
        return None

    def _warn_non_transactional(self, key: str, target: BackupServerTarget):
        if not target.databases:
            return
        in_list = ",".join(f"'{d}'" for d in target.databases)
        rows = self._sql(key, "SELECT CONCAT(TABLE_SCHEMA,'.',TABLE_NAME) FROM information_schema.TABLES "
                              f"WHERE TABLE_SCHEMA IN ({in_list}) AND ENGINE IS NOT NULL "
                              "AND ENGINE <> 'InnoDB' LIMIT 20;")
        if rows:
            self.notifier.notify(AlertIncident(
                key=f"non_innodb:{key}", severity="warning", category="backup",
                title="Не все таблицы транзакционные",
                explanation="Для таблиц вне InnoDB режим --single-transaction не даёт согласованного снимка: "
                            "они попадают в дамп в состоянии на момент чтения, а не на момент старта бэкапа.",
                actions=["ALTER TABLE ... ENGINE=InnoDB для перечисленных таблиц"],
                metrics={"tables": [r[0] for r in rows]}))

    def _check_disk_space(self) -> bool:
        try:
            usage = shutil.disk_usage(BACKUP_ROOT)
        except Exception:
            return True
        free_mb = usage.free // 1048576
        last = self._latest_dump()
        need_mb = BACKUP_MIN_FREE_MB
        if last and last.get("size_compressed"):
            need_mb = max(need_mb, int(last["size_compressed"] * BACKUP_DISK_SAFETY // 1048576))
        if free_mb < need_mb:
            self.notifier.notify(AlertIncident(
                key="backup_disk_full", severity="critical", category="storage",
                title="На диске бэкапов недостаточно места",
                explanation=f"Свободно {free_mb} МБ, для безопасного запуска нужно минимум {need_mb} МБ. Бэкап "
                            "не запускался, чтобы не забить диск наполовину записанным архивом.",
                actions=["Расширить том или перенести BACKUP_ROOT на диск большего объёма",
                         "Проверить, не осталось ли лишних поколений журналов"],
                metrics={"free_mb": free_mb, "need_mb": need_mb, "path": str(BACKUP_ROOT)}))
            return False
        return True

    # ── ПОТОКОВЫЙ ДАМП И СЖАТИЕ ─────────────────────────────────────────────
    def _dump_cmd(self, key: str, target: BackupServerTarget) -> List[str]:
        cmd = [self.tools.dump, f"--defaults-extra-file={self._defaults_files[key]}",
               "--single-transaction",   # согласованный снимок без блокировок: сервис не останавливается
               "--quick",                # построчная выдача, таблица не буферизуется в памяти
               "--skip-lock-tables",
               "--routines", "--triggers", "--events",
               "--hex-blob", "--default-character-set=utf8mb4",
               "--max-allowed-packet=64M", "--net-buffer-length=16384"]
        if self.tools.dump_supports("--source-data"):
            cmd.append("--source-data=2")   # координата binlog внутри дампа = точка старта наката
        elif self.tools.dump_supports("--master-data"):
            cmd.append("--master-data=2")
        if self.tools.dump_supports("--no-tablespaces"):
            cmd.append("--no-tablespaces")
        if self.tools.dump_supports("--set-gtid-purged"):
            cmd.append("--set-gtid-purged=COMMENTED")
        if self.tools.dump_supports("--column-statistics"):
            cmd.append("--column-statistics=0")
        if target.host not in ("127.0.0.1", "localhost", "::1") and self.tools.dump_supports("--compress"):
            cmd.append("--compress")
        cmd.append("--databases")
        cmd.extend(target.databases)
        return cmd

    def _dump_and_compress(self, key: str, target: BackupServerTarget, out_path: Path) -> Tuple[bool, Dict]:
        info: Dict = {}
        dumper = None
        try:
            with open(out_path, "wb") as fh:
                dumper = subprocess.Popen(self._dump_cmd(key, target), stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE, start_new_session=True,
                                          preexec_fn=_child_limits(BACKUP_DUMPER_MEM_MB), env=_child_env())
                get_dump_err = self._drain(dumper.stderr)
                compressor, comp_name = self._spawn_compressor(dumper.stdout, fh)
                if compressor is None:
                    dumper.kill()
                    return False, {"error": "не найден ни zstd, ни python-zstandard, ни gzip"}
                info["compressor"] = comp_name
                comp_err = b""
                if isinstance(compressor, subprocess.Popen):
                    dumper.stdout.close()   # дескриптор остаётся только у сжимающего процесса
                    _, comp_err = compressor.communicate(timeout=BACKUP_TIMEOUT_SEC)
                    comp_rc = compressor.returncode
                else:
                    comp_rc = compressor()
                    dumper.stdout.close()
                dump_err = get_dump_err()
                dumper.wait(timeout=300)
                fh.flush()
                os.fsync(fh.fileno())
        except subprocess.TimeoutExpired:
            self._kill_pipeline(dumper)
            return False, {"error": f"превышен лимит времени {BACKUP_TIMEOUT_SEC} с"}
        except Exception as e:
            self._kill_pipeline(dumper)
            return False, {"error": str(e)}

        err_text = (dump_err or b"").decode("utf-8", "replace").strip()
        if dumper.returncode != 0 or comp_rc != 0:
            return False, {"error": f"mysqldump rc={dumper.returncode}, компрессор rc={comp_rc}: "
                                    f"{err_text} {(comp_err or b'').decode('utf-8', 'replace')[:300]}"}
        if err_text:
            backup_logger.warning("mysqldump предупреждения: %s", err_text[:500])
        info["coord"] = self._extract_coord(out_path)
        return True, info

    @staticmethod
    def _drain(stream) -> Callable[[], bytes]:
        """Читает stderr в отдельном потоке: переполнение буфера трубы иначе вешает пайплайн."""
        buf: List[bytes] = []
        t = threading.Thread(target=lambda: buf.append(stream.read()), daemon=True)
        t.start()

        def _get(timeout: int = 30) -> bytes:
            t.join(timeout)
            return buf[0] if buf else b""

        return _get

    def _kill_pipeline(self, proc):
        try:
            if proc and proc.poll() is None:
                proc.kill()
        except Exception:
            pass

    def _spawn_compressor(self, stdin_pipe, out_fh):
        """Приоритет: CLI zstd -> python-zstandard -> gzip. Все варианты потоковые."""
        if self.tools.zstd:
            # без потоков асинхронного ввода-вывода (zstd 1.5.3+): меньше памяти под потолком _child_limits
            extra = ["--no-asyncio"] if self.tools.zstd_supports("--no-asyncio") else []
            p = subprocess.Popen([self.tools.zstd, f"-{BACKUP_ZSTD_LEVEL}", f"-T{BACKUP_ZSTD_THREADS}",
                                  "-c", "-q", "--no-progress", "--check", *extra],
                                 stdin=stdin_pipe, stdout=out_fh, stderr=subprocess.PIPE,
                                 start_new_session=True,
                                 preexec_fn=_child_limits(BACKUP_COMPRESSOR_MEM_MB), env=_child_env())
            return p, f"zstd -{BACKUP_ZSTD_LEVEL} -T{BACKUP_ZSTD_THREADS}"
        if self.tools.has_pyzstd:
            import zstandard as _zstd

            def _run_py() -> int:
                deadline = time.time() + BACKUP_TIMEOUT_SEC
                cctx = _zstd.ZstdCompressor(level=BACKUP_ZSTD_LEVEL, threads=0, write_checksum=True)
                with cctx.stream_writer(out_fh, closefd=False) as w:
                    while True:
                        chunk = stdin_pipe.read(BACKUP_PIPE_CHUNK)
                        if not chunk:
                            break
                        w.write(chunk)
                        if time.time() > deadline:
                            raise subprocess.TimeoutExpired("zstandard", BACKUP_TIMEOUT_SEC)
                return 0

            return _run_py, f"python-zstandard level={BACKUP_ZSTD_LEVEL}"

        def _run_gzip() -> int:
            deadline = time.time() + BACKUP_TIMEOUT_SEC
            with gzip.GzipFile(fileobj=out_fh, mode="wb", compresslevel=9) as w:
                while True:
                    chunk = stdin_pipe.read(BACKUP_PIPE_CHUNK)
                    if not chunk:
                        break
                    w.write(chunk)
                    if time.time() > deadline:
                        raise subprocess.TimeoutExpired("gzip", BACKUP_TIMEOUT_SEC)
            return 0

        return _run_gzip, "gzip -9 (запасной вариант)"

    def _decompress_proc(self, path: Path) -> Optional[subprocess.Popen]:
        if path.suffix == ".zst" and self.tools.zstd:
            return subprocess.Popen([self.tools.zstd, "-dcq", str(path)], stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, preexec_fn=_child_limits(BACKUP_COMPRESSOR_MEM_MB), env=_child_env())
        return None

    def _verify_dump(self, path: Path) -> Dict:
        """
        Один потоковый проход: распаковщик сам сверяет контрольную сумму содержимого,
        параллельно считаем распакованный размер и ищем маркер завершения дампа.
        Память при проверке — один чанк, размер архива значения не имеет.
        """
        out: Dict = {"frame_ok": False, "logical_complete": False, "raw_size": 0, "checked_at": _utc_iso()}
        proc = self._decompress_proc(path)
        try:
            if proc is not None:
                stream = proc.stdout
            elif path.suffix == ".gz":
                stream = gzip.open(path, "rb")
            elif path.suffix == ".zst" and self.tools.has_pyzstd:
                import zstandard as _zstd
                stream = _zstd.ZstdDecompressor().stream_reader(open(path, "rb"))
            else:
                out["error"] = "нет доступного распаковщика для проверки"
                return out
            tail = b""
            while True:
                chunk = stream.read(BACKUP_PIPE_CHUNK)
                if not chunk:
                    break
                out["raw_size"] += len(chunk)
                tail = (tail + chunk)[-4096:]
            stream.close()
            if proc is not None:
                proc.wait(timeout=BACKUP_TIMEOUT_SEC)
                out["frame_ok"] = proc.returncode == 0
            else:
                out["frame_ok"] = True
            out["logical_complete"] = any(m in tail for m in _LOGICAL_END_MARKERS)
        except Exception as e:
            out["error"] = str(e)
            self._kill_pipeline(proc)
        return out

    def _extract_coord(self, path: Path) -> Optional[Tuple[str, int]]:
        """Координата binlog лежит комментарием в шапке дампа — читаем только первые килобайты."""
        head = b""
        proc = self._decompress_proc(path)
        try:
            if proc is not None:
                head = proc.stdout.read(256 * 1024)
            elif path.suffix == ".gz":
                with gzip.open(path, "rb") as f:
                    head = f.read(256 * 1024)
            elif self.tools.has_pyzstd:
                import zstandard as _zstd
                with open(path, "rb") as raw:
                    head = _zstd.ZstdDecompressor().stream_reader(raw).read(256 * 1024)
        except Exception as e:
            backup_logger.warning("Не удалось прочитать координату binlog: %s", e)
        finally:
            if proc is not None:
                try:
                    proc.stdout.close()
                    proc.terminate()
                    proc.wait(timeout=10)
                except Exception:
                    pass
        m = _COORD_RE.search(head or b"")
        return (m.group(1).decode(), int(m.group(2))) if m else None

    # ── РОТАЦИЯ ─────────────────────────────────────────────────────────────
    def _latest_dump(self) -> Optional[Dict]:
        dumps = [d for d in self.state.get("dumps", []) if d.get("verified")]
        return dumps[-1] if dumps else None

    def _rotate_dumps(self) -> List[str]:
        """Не более BACKUP_KEEP_DUMPS копий на каждый сервер СУБД."""
        removed: List[str] = []
        by_server: Dict[str, List[Dict]] = {}
        for meta in self._scan_dump_metas():
            by_server.setdefault(meta.get("server", "?"), []).append(meta)
        keep_names = set()
        for metas in by_server.values():
            metas.sort(key=lambda m: m.get("created_at", ""))
            for m in metas[-BACKUP_KEEP_DUMPS:]:
                keep_names.add(m["file"])
                keep_names.add(m["file"] + ".meta.json")
        for f in BACKUP_DUMPS_DIR.iterdir():
            if f.name in keep_names or f.name.endswith(".part"):
                continue
            try:
                f.unlink()
                removed.append(f.name)
            except Exception as e:
                backup_logger.warning("Не удалось удалить старый дамп %s: %s", f.name, e)
        with self._state_lock:
            self.state["dumps"] = self.state.get("dumps", [])[-(BACKUP_KEEP_DUMPS * 2):]
            self._save_state()
        return removed

    def _purge_binlogs(self, key: str, dump_meta: Dict) -> Dict:
        """
        Журналы удаляются ТОЛЬКО после проверенного дампа и только те, чьи данные
        гарантированно вошли внутрь этого дампа — сравнение по координате binlog.
        Это и есть условие «все данные перенесены в базу на 100%».
        """
        result: Dict = {"generations_removed": [], "server_purged_to": None, "kept": []}
        if not dump_meta.get("verified"):
            return result
        dump_idx = _binlog_index(dump_meta.get("binlog_file") or "")
        gens = sorted([p for p in self._server_dir(key).glob("gen-*") if p.is_dir()])
        current = self._gen_dirs.get(key)

        survivors = set(gens[-BACKUP_KEEP_GENERATIONS:])
        if current:
            survivors.add(current)
        for gen in gens:
            if gen in survivors:
                result["kept"].append(gen.name)
                continue
            last_idx = self._generation_last_index(gen)
            covered = dump_idx >= 0 and last_idx >= 0 and last_idx < dump_idx
            if not covered:
                result["kept"].append(gen.name)
                backup_logger.warning("Поколение %s не удаляется: не доказано, что все его данные вошли в дамп",
                                      gen.name)
                continue
            try:
                shutil.rmtree(gen)
                result["generations_removed"].append(gen.name)
            except Exception as e:
                backup_logger.warning("Не удалось удалить поколение %s: %s", gen.name, e)

        oldest_needed = None
        for gen in survivors:
            files = sorted([p.name for p in gen.glob("*") if _binlog_index(p.name) >= 0], key=_binlog_index)
            if files:
                oldest_needed = files[0] if oldest_needed is None else min(oldest_needed, files[0],
                                                                          key=_binlog_index)
        if oldest_needed:
            self._sql(key, f"PURGE BINARY LOGS TO '{oldest_needed}';")   # чистим журналы и на самой СУБД
            result["server_purged_to"] = oldest_needed
        return result

    # ── ОТЧЁТНОСТЬ ──────────────────────────────────────────────────────────
    def _alert_missing_tools(self):
        self.notifier.notify(AlertIncident(
            key="backup_tools_missing", severity="critical", category="backup",
            title="На сервере нет утилит резервного копирования",
            explanation="Отсутствует mysqldump и/или zstd — полноценный бэкап невозможен, будет использован "
                        "запасной путь сжатия, если он доступен.",
            actions=["Linux: apt install mysql-client zstd (или mariadb-client)",
                     "Windows: утилиты лежат в каталоге MySQL, например "
                     "C:\\Program Files\\MySQL\\MySQL Server 8.0\\bin",
                     "Указать каталог напрямую: переменная окружения MYSQL_BIN_DIR",
                     "Либо конкретный файл: MYSQLDUMP_PATH / MYSQLBINLOG_PATH / ZSTD_PATH"],
            metrics={"mysqldump": self.tools.dump, "mysqlbinlog": self.tools.binlog,
                     "zstd": self.tools.zstd, "python-zstandard": self.tools.has_pyzstd}))

    def status(self) -> Dict:
        pitr = {}
        for key in self.targets:
            root = self._server_dir(key)
            pitr[key] = {
                "generations": [g.name for g in sorted(root.glob("gen-*")) if g.is_dir()],
                "current": self._gen_dirs[key].name if key in self._gen_dirs else None,
                "streaming": self.archivers[key].running if key in self.archivers else False,
                "stream_error": self.archivers[key].last_error if key in self.archivers else None,
            }
        try:
            usage = shutil.disk_usage(BACKUP_ROOT)
            disk = {"free_mb": usage.free // 1048576, "total_mb": usage.total // 1048576}
        except Exception:
            disk = {}
        return {
            "enabled": BACKUP_ENABLED and self._started,
            "root": str(BACKUP_ROOT),
            "schedule": f"{BACKUP_HOUR:02d}:{BACKUP_MINUTE:02d} ежедневно",
            "retention": {"dumps": BACKUP_KEEP_DUMPS, "binlog_generations": BACKUP_KEEP_GENERATIONS},
            "compression": f"zstd -{BACKUP_ZSTD_LEVEL} (поток, T{BACKUP_ZSTD_THREADS})",
            "memory_budget_mb": BACKUP_DUMPER_MEM_MB + BACKUP_COMPRESSOR_MEM_MB,
            "last_dump": self._latest_dump(),
            "last_run": self.last_result,
            "pitr": pitr,
            "disk": disk,
            "tools": {"mysqldump": self.tools.dump, "mysqlbinlog": self.tools.binlog, "zstd": self.tools.zstd},
        }

    def restore_hint(self) -> Dict:
        """Готовая последовательность восстановления по последнему проверенному дампу."""
        last = self._latest_dump()
        if not last:
            return {"error": "нет проверенных дампов"}
        files: List[str] = []
        for key in self.targets:
            for gen in sorted(self._server_dir(key).glob("gen-*")):
                files += [str(p) for p in sorted(gen.glob("*"), key=lambda x: _binlog_index(x.name))
                          if _binlog_index(p.name) >= 0]
        return {
            "step_1_restore_dump": f"zstd -dc {last['path']} | mysql --defaults-extra-file=<cnf>",
            "step_2_apply_binlogs": f"mysqlbinlog --start-position={last.get('binlog_pos')} "
                                    f"{' '.join(files)} | mysql --defaults-extra-file=<cnf>",
            "start_coordinate": {"file": last.get("binlog_file"), "pos": last.get("binlog_pos")},
            "sha256": last.get("sha256"),
            "note": "Файлы .recovered — восстановленные префиксы повреждённых журналов, накатывать последними.",
        }
