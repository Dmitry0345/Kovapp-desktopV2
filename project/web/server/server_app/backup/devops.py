# devops.py

import os
import re
import sys
from datetime import timedelta, datetime, date, timezone
from typing import List, Optional, Dict, Literal, Callable, Tuple
from pathlib import Path
import shutil
import uuid
import json
import logging
import hashlib
import socket
import threading
import time
from dataclasses import dataclass, asdict, field as dc_field

from dotenv import load_dotenv

from server.database.models import *
from ..envtools import env_float, env_int


try:
    import resource
except ImportError:
    resource = None


def get_app_root() -> Path:

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent

    return Path(__file__).resolve().parents[3]


APP_ROOT = get_app_root()


ENV_FILE = APP_ROOT / ".env"

load_dotenv(ENV_FILE)


BACKUP_ROOT_SETTING = (
    os.getenv("BACKUP_ROOT") or ""
).strip()


DEFAULT_BACKUP_DIR_NAME = "backups"


BACKUP_ROOT = (
    Path(BACKUP_ROOT_SETTING)
    .expanduser()
    .resolve()
    if BACKUP_ROOT_SETTING
    else APP_ROOT / DEFAULT_BACKUP_DIR_NAME
)


try:
    os.makedirs(BACKUP_ROOT, exist_ok=True)
except OSError:
    pass


BACKUP_DUMPS_DIR = BACKUP_ROOT / "dumps"
BACKUP_BINLOG_DIR = BACKUP_ROOT / "binlogs"
BACKUP_STATE_DIR = BACKUP_ROOT / "state"
BACKUP_INCIDENTS_DIR = BACKUP_ROOT / "incidents"
BACKUP_LOG_DIR = BACKUP_ROOT / "logs"

BACKUP_STATE_FILE = BACKUP_STATE_DIR / "backup_state.json"


BACKUP_ENABLED = os.getenv("BACKUP_ENABLED", "1") == "1"


BACKUP_HOUR = env_int("BACKUP_HOUR", 3)


BACKUP_MINUTE = env_int("BACKUP_MINUTE", 0)


BACKUP_KEEP_DUMPS = env_int("BACKUP_KEEP_DUMPS", 3)              # не более 3 дампов


BACKUP_KEEP_GENERATIONS = env_int("BACKUP_KEEP_GENERATIONS", 2)  # текущий + предыдущий журнал


BACKUP_ZSTD_LEVEL = env_int("BACKUP_ZSTD_LEVEL", 15)


BACKUP_ZSTD_THREADS = 1                                                    # T1: память предсказуема


BACKUP_COMPRESSOR_MEM_MB = env_int("BACKUP_COMPRESSOR_MEM_MB", 300)  # потолок для zstd


BACKUP_DUMPER_MEM_MB = env_int("BACKUP_DUMPER_MEM_MB", 180)          # потолок для mysqldump


BACKUP_PIPE_CHUNK = 1024 * 1024                                            # чанк чтения/хеширования


BACKUP_TIMEOUT_SEC = env_int("BACKUP_TIMEOUT_SEC", 2 * 3600)   # не дольше 2 часов


BACKUP_MIN_FREE_MB = env_int("BACKUP_MIN_FREE_MB", 2048)


BACKUP_DISK_SAFETY = 2.0                                                   # запас места к прошлому дампу


BINLOG_STREAM_ENABLED = os.getenv("BINLOG_STREAM_ENABLED", "1") == "1"


BINLOG_SERVER_ID = env_int("BINLOG_SERVER_ID", 99887766)  # id псевдо-реплики для mysqlbinlog


BINLOG_RESTART_BACKOFF = (2, 5, 15, 30, 60)


BACKUP_AUTO_TUNE_DURABILITY = os.getenv("BACKUP_AUTO_TUNE_DURABILITY", "1") == "1"


WATCHDOG_ENABLED = os.getenv("WATCHDOG_ENABLED", "1") == "1"


WATCHDOG_INTERVAL_OK = env_int("WATCHDOG_INTERVAL_OK", 30)       # обычный интервал опроса


WATCHDOG_INTERVAL_SUSPECT = env_int("WATCHDOG_INTERVAL_SUSPECT", 5)  # при подозрении


WATCHDOG_INTERVAL_DOWN_MAX = 60                                           # потолок при недоступной БД


WATCHDOG_CONFIRM_FAILS = env_int("WATCHDOG_CONFIRM_FAILS", 3)    # подтверждений до инцидента


WATCHDOG_LIVENESS_TIMEOUT = env_float("WATCHDOG_LIVENESS_TIMEOUT", 2.0)   # жёсткий таймаут SELECT 1


WATCHDOG_LATENCY_WARN = env_float("WATCHDOG_LATENCY_WARN", 5.0)    # деградация отклика


WATCHDOG_RW_EVERY = env_int("WATCHDOG_RW_EVERY", 10)             # проверка записи раз в N циклов


INCIDENT_COOLDOWN = env_int("INCIDENT_COOLDOWN", 900)            # антиспам по одной проблеме


INCIDENT_OUTBOX_LIMIT = env_int("INCIDENT_OUTBOX_LIMIT", 500)


backup_logger = logging.getLogger("kovcheg.backup")


watchdog_logger = logging.getLogger("kovcheg.watchdog")


_LOGICAL_END_MARKERS = (b"-- Dump completed", b"-- Dump finished")


_COORD_RE = re.compile(
    rb"(?:MASTER_LOG_FILE|SOURCE_LOG_FILE)\s*=\s*'([^']+)'\s*,\s*"
    rb"(?:MASTER_LOG_POS|SOURCE_LOG_POS)\s*=\s*(\d+)", re.I)


_BINLOG_IDX_RE = re.compile(r"\.(\d+)$")


SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _binlog_index(name: str) -> int:
    m = _BINLOG_IDX_RE.search(name)
    return int(m.group(1)) if m else -1


# Каталоги, найденные во время работы (например, basedir у самой СУБД)
_RUNTIME_BIN_DIRS: List[Path] = []


def register_tool_dir(path) -> bool:
    """Добавляет каталог в поиск утилит. Принимает и basedir, и basedir/bin."""
    if not path:
        return False
    try:
        base = Path(str(path)).expanduser()
    except Exception:
        return False
    added = False
    for candidate in (base / "bin", base):
        if candidate.is_dir() and candidate not in _RUNTIME_BIN_DIRS:
            _RUNTIME_BIN_DIRS.append(candidate)
            added = True
    return added


def _glob_dirs(root, patterns) -> List[Path]:
    if not root:
        return []
    try:
        base = Path(root)
    except Exception:
        return []
    found = []
    for pattern in patterns:
        try:
            found.extend(p for p in base.glob(pattern) if p.is_dir())
        except Exception:
            continue
    return found


def _candidate_bin_dirs() -> List[Path]:
    """Типовые места установки MySQL/MariaDB и zstd — PATH бывает не настроен."""
    dirs: List[Path] = list(_RUNTIME_BIN_DIRS)

    for var in ("MYSQL_BIN_DIR", "MARIADB_BIN_DIR", "BACKUP_TOOLS_DIR"):
        value = os.getenv(var)
        if value:
            for part in value.split(os.pathsep):
                if part.strip():
                    dirs.append(Path(part.strip()).expanduser())

    if os.name == "nt":
        patterns = [
            "MySQL/MySQL Server */bin",
            "MariaDB */bin",
            "MariaDB/*/bin",
            "zstd", "zstd/bin",
        ]
        for root in (os.getenv("ProgramFiles"), os.getenv("ProgramW6432"),
                     os.getenv("ProgramFiles(x86)"), os.getenv("LOCALAPPDATA")):
            dirs.extend(_glob_dirs(root, patterns))
        # Сборки «всё в одном» ставят СУБД к себе внутрь
        for root in ("C:/", "D:/", os.getenv("SystemDrive", "C:") + "/"):
            dirs.extend(_glob_dirs(root, [
                "xampp/mysql/bin",
                "laragon/bin/mysql/*/bin",
                "wamp*/bin/mysql/*/bin",
                "Program Files/MySQL/MySQL Server */bin",
                "MySQL/MySQL Server */bin",
                "mysql*/bin",
                "mariadb*/bin",
            ]))
    else:
        dirs.extend(Path(p) for p in (
            "/usr/bin", "/usr/local/bin", "/bin", "/snap/bin",
            "/usr/local/mysql/bin", "/opt/mysql/bin", "/opt/mariadb/bin",
            "/opt/homebrew/bin", "/opt/homebrew/opt/mysql-client/bin",
            "/usr/local/opt/mysql-client/bin",
        ))

    unique: List[Path] = []
    for d in dirs:
        try:
            if d.is_dir() and d not in unique:
                unique.append(d)
        except Exception:
            continue
    return unique


def _binary_names(name: str) -> List[str]:
    if os.name != "nt":
        return [name]
    exts = [e.lower() for e in os.getenv("PATHEXT", ".EXE;.BAT;.CMD").split(os.pathsep) if e]
    return [name + e for e in exts] + [name]


def _find_binary(*names: str) -> Optional[str]:
    """Ищет утилиту по явной переменной окружения, затем в PATH, затем в типовых каталогах."""
    for n in names:
        override = os.getenv(f"{n.replace('-', '_').upper()}_PATH")
        if override and Path(override).is_file():
            return str(Path(override))

    for n in names:
        p = shutil.which(n)
        if p:
            return p

    for directory in _candidate_bin_dirs():
        for n in names:
            for filename in _binary_names(n):
                candidate = directory / filename
                try:
                    if candidate.is_file():
                        return str(candidate)
                except Exception:
                    continue
    return None


def _child_limits(mem_mb: int):
    """Жёсткий потолок адресного пространства дочернего процесса."""
    if resource is None or os.name != "posix" or mem_mb <= 0:
        return None

    def _apply():
        b = mem_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (b, b))

    return _apply


def _child_env() -> dict:
    """Окружение дочерних программ копирования. MALLOC_ARENA_MAX=1: новые zstd (1.5.3+) заводят отдельные
    потоки ввода-вывода, и каждый поток резервирует себе 64 МБ памяти — под потолком адресного пространства
    (_child_limits) это «not enough memory», хотя по-настоящему zstd нужно около 100 МБ."""
    env = dict(os.environ)
    env.setdefault("MALLOC_ARENA_MAX", "1")
    return env


def _fsync_dir(path: Path):
    try:
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except Exception:
        pass


def _atomic_write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(BACKUP_PIPE_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class AlertIncident:
    key: str                                       # ключ дедупликации (одна проблема = один ключ)
    severity: str                                  # info | warning | critical
    category: str                                  # класс проблемы для маршрутизации
    title: str
    explanation: str                               # пояснение: что это значит на практике
    probable_causes: List[str] = dc_field(default_factory=list)
    actions: List[str] = dc_field(default_factory=list)
    error_code: Optional[int] = None
    error_name: Optional[str] = None
    raw_error: str = ""
    metrics: Dict = dc_field(default_factory=dict)
    service: str = "kovcheg-server"
    host: str = dc_field(default_factory=socket.gethostname)
    incident_id: str = dc_field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: str = dc_field(default_factory=_utc_iso)
    resolved: bool = False

    def to_dict(self) -> Dict:
        return asdict(self)

    def to_text(self) -> str:
        """Готовое к отправке сообщение — одинаково читается в почте и в мессенджере."""
        mark = {"info": "[INFO]", "warning": "[WARN]", "critical": "[CRIT]"}.get(self.severity, "[?]")
        head = f"[OK] ВОССТАНОВЛЕНО: {self.title}" if self.resolved else f"{mark} {self.title}"
        lines = [head,
                 f"Сервис: {self.service} ({self.host})",
                 f"Класс проблемы: {self.category}",
                 f"Время: {self.created_at}",
                 "",
                 f"Что произошло: {self.explanation}"]
        if self.error_code is not None:
            lines.append(f"Код драйвера: {self.error_code} ({self.error_name or 'unknown'})")
        if self.raw_error:
            lines.append(f"Текст ошибки: {self.raw_error[:600]}")
        if self.metrics:
            lines.append("Метрики: " + json.dumps(self.metrics, ensure_ascii=False, default=str))
        if self.probable_causes:
            lines.append("")
            lines.append("Вероятные причины:")
            lines += [f"  - {c}" for c in self.probable_causes]
        if self.actions:
            lines.append("")
            lines.append("Что делать:")
            lines += [f"  {i}. {a}" for i, a in enumerate(self.actions, 1)]
        lines += ["", f"ID инцидента: {self.incident_id}"]
        return "\n".join(lines)


class DevOpsNotifier:
    """
    Единая точка доставки инцидентов. Канал подключается позже через
    register_transport(); пока его нет, инциденты копятся в outbox на диске
    и будут досланы автоматически, как только транспорт появится.
    """

    def __init__(self, incidents_dir: Path, service: str = "kovcheg-server"):
        self.service = service
        self.incidents_dir = Path(incidents_dir)
        self.outbox_dir = self.incidents_dir / "outbox"     # неотправленные (нет живого канала)
        self.archive_dir = self.incidents_dir / "archive"   # отправленные / исторические
        for d in (self.incidents_dir, self.outbox_dir, self.archive_dir):
            d.mkdir(parents=True, exist_ok=True)
        self._transports: List[Callable[[AlertIncident], None]] = []
        self._lock = threading.RLock()
        self._last_sent: Dict[str, float] = {}      # key -> время последней отправки (monotonic)
        self._last_severity: Dict[str, int] = {}    # key -> severity последней ОТПРАВКИ (для эскалации)
        self._open: Dict[str, AlertIncident] = {}   # key -> актуальное состояние проблемы

    def register_transport(self, transport: Callable[[AlertIncident], None], flush: bool = True):
        with self._lock:
            self._transports.append(transport)
        watchdog_logger.info("Зарегистрирован транспорт уведомлений: %s",
                             getattr(transport, "__name__", transport))
        if flush:
            self.flush_outbox()

    @property
    def has_transport(self) -> bool:
        return bool(self._transports)

    def notify(self, incident: AlertIncident, force: bool = False) -> bool:
        incident.service = incident.service or self.service
        now = time.monotonic()
        with self._lock:
            last = self._last_sent.get(incident.key)
            escalated = (SEVERITY_ORDER.get(incident.severity, 0) >
                         self._last_severity.get(incident.key, -1))
            # состояние обновляем всегда, чтобы снимок был актуальным даже при подавлении дубля
            self._open[incident.key] = incident
            if not force and not escalated and last is not None and (now - last) < INCIDENT_COOLDOWN:
                return False
            self._last_sent[incident.key] = now
            self._last_severity[incident.key] = SEVERITY_ORDER.get(incident.severity, 0)
        self._deliver(incident)
        return True

    def resolve(self, key: str, note: str = ""):
        """Отбой по ранее заведённому инциденту."""
        with self._lock:
            incident = self._open.pop(key, None)
            self._last_sent.pop(key, None)
            self._last_severity.pop(key, None)
        if incident is None:
            return
        done = AlertIncident(key=key, severity="info", category=incident.category,
                             title=incident.title, service=incident.service, metrics=incident.metrics,
                             explanation=note or "Проблема больше не воспроизводится, состояние в норме.")
        done.resolved = True
        self._deliver(done)

    def _deliver(self, incident: AlertIncident):
        payload = incident.to_dict()
        payload["message"] = incident.to_text()
        delivered = False
        with self._lock:
            transports = list(self._transports)
        for t in transports:
            try:
                t(incident)
                delivered = True
            except Exception as e:
                watchdog_logger.error("Транспорт уведомлений упал: %s", e)
        self._persist(self.archive_dir if delivered else self.outbox_dir, incident, payload)
        log_fn = (watchdog_logger.critical if incident.severity == "critical"
                  else watchdog_logger.warning if incident.severity == "warning"
                  else watchdog_logger.info)
        log_fn("ИНЦИДЕНТ%s\n%s", "" if delivered else " (канал не настроен, сохранён в outbox)",
               incident.to_text())

    def _persist(self, directory: Path, incident: AlertIncident, payload: Dict):
        path = directory / f"{_utc_stamp()}-{incident.severity}-{incident.incident_id}.json"
        try:
            _atomic_write_json(path, payload)
            files = sorted(directory.glob("*.json"))
            if len(files) > INCIDENT_OUTBOX_LIMIT:
                for extra in files[:-INCIDENT_OUTBOX_LIMIT]:
                    extra.unlink(missing_ok=True)
        except Exception as e:
            watchdog_logger.error("Не удалось сохранить инцидент на диск: %s", e)

    def flush_outbox(self) -> int:
        """Досылает всё, что накопилось, когда канал наконец подключён."""
        if not self.has_transport:
            return 0
        sent = 0
        for path in sorted(self.outbox_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                data.pop("message", None)
                incident = AlertIncident(**{k: v for k, v in data.items()
                                            if k in AlertIncident.__dataclass_fields__})
                for t in list(self._transports):
                    t(incident)
                path.replace(self.archive_dir / path.name)
                sent += 1
            except Exception as e:
                watchdog_logger.error("Не удалось дослать инцидент %s: %s", path.name, e)
        return sent

    def open_incidents(self) -> List[Dict]:
        with self._lock:
            return [i.to_dict() for i in self._open.values()]


def devops_log_transport(incident: AlertIncident) -> None:
    """Временный транспорт-заглушка: инцидент уходит только в лог и на диск."""
    watchdog_logger.error("DEVOPS >>>\n%s", incident.to_text())


MYSQL_ERROR_CLASSIFICATION: Dict[int, Dict] = {
    2006: {"name": "CR_SERVER_GONE_ERROR", "severity": "critical", "category": "db_down",
           "title": "Процесс СУБД оборвал соединение",
           "explanation": "Сервер БД закрыл соединение на середине запроса: обычно процесс упал, был "
                          "перезапущен или его убил OOM Killer из-за нехватки памяти.",
           "causes": ["OOM Killer завершил mysqld", "Аварийный рестарт СУБД",
                      "Превышен wait_timeout / max_allowed_packet"],
           "actions": ["dmesg -T | grep -i oom", "Проверить error log СУБД и время последнего старта",
                       "Сверить innodb_buffer_pool_size с реальным объёмом RAM"]},
    2013: {"name": "CR_SERVER_LOST", "severity": "critical", "category": "db_down",
           "title": "Соединение с СУБД потеряно во время запроса",
           "explanation": "Разрыв произошёл до получения ответа: сеть между сервисом и БД или сама БД "
                          "прервали обмен.",
           "causes": ["Сетевой сбой или разрыв туннеля", "Убит долгий запрос", "Рестарт СУБД"],
           "actions": ["Проверить сетевую связность и потери пакетов", "Смотреть SHOW PROCESSLIST на предмет killed"]},
    1021: {"name": "ER_DISK_FULL", "severity": "critical", "category": "storage",
           "title": "На накопителе СУБД закончилось место",
           "explanation": "Запись невозможна: новые сообщения и задачи не сохраняются, база фактически "
                          "работает только на чтение. Это состояние ломает и binlog.",
           "causes": ["Разросся binlog или ibdata", "Не чистятся временные файлы", "Мал раздел под datadir"],
           "actions": ["df -h на разделе datadir", "PURGE BINARY LOGS BEFORE NOW() - INTERVAL 3 DAY",
                       "Расширить раздел или перенести binlog на отдельный том"]},
    1213: {"name": "ER_LOCK_DEADLOCK", "severity": "warning", "category": "contention",
           "title": "Взаимная блокировка транзакций",
           "explanation": "Две транзакции заблокировали друг друга, одна была откачена. Единичный случай "
                          "нормален, но серия означает конкуренцию за одни и те же строки.",
           "causes": ["Разный порядок блокировки строк в коде", "Долгие транзакции в мессенджере",
                      "Массовые обновления задач"],
           "actions": ["SHOW ENGINE INNODB STATUS — секция LATEST DETECTED DEADLOCK",
                       "Сократить длительность транзакций, единый порядок обновления таблиц"]},
    1194: {"name": "ER_TABLE_CORRUPT", "severity": "critical", "category": "corruption",
           "title": "Обнаружено физическое повреждение таблицы",
           "explanation": "Таблица помечена как повреждённая, часть данных может быть нечитаема. Это прямой "
                          "повод разворачивать резервную копию и накатывать журнал транзакций.",
           "causes": ["Сбой диска или контроллера", "Аварийное отключение питания",
                      "Повреждение файловой системы"],
           "actions": ["CHECK TABLE <имя> EXTENDED", "Проверить SMART диска",
                       "Восстановление: дамп + накат binlog (GET /admin/backup/restore-hint)"]},
    1040: {"name": "ER_CON_COUNT_ERROR", "severity": "critical", "category": "pool",
           "title": "Исчерпан лимит соединений СУБД",
           "explanation": "Сервер БД отказывает в новых подключениях: max_connections на стороне СУБД "
                          "меньше, чем суммарно просят пулы приложения.",
           "causes": ["max_connections меньше суммы пулов", "Утечка соединений", "Всплеск нагрузки"],
           "actions": ["SHOW VARIABLES LIKE 'max_connections'",
                       "Сверить с POOL_SIZE + MAX_OVERFLOW всех сервисов", "Проверить /health/db-pool"]},
    1205: {"name": "ER_LOCK_WAIT_TIMEOUT", "severity": "warning", "category": "contention",
           "title": "Истекло ожидание блокировки строки",
           "explanation": "Транзакция слишком долго ждала освобождения строки. Пользователи видят это как "
                          "зависшие действия в задаче или чате.",
           "causes": ["Долгая незакрытая транзакция", "Тяжёлый отчёт держит блокировки"],
           "actions": ["SHOW ENGINE INNODB STATUS", "Найти долгие транзакции в information_schema.innodb_trx"]},
    1290: {"name": "ER_OPTION_PREVENTS_STATEMENT", "severity": "critical", "category": "readonly",
           "title": "СУБД работает в режиме только для чтения",
           "explanation": "Чтение проходит, запись отклоняется. Пользователи читают старые сообщения, но "
                          "ничего не могут отправить или изменить.",
           "causes": ["read_only / super_read_only включены", "Сервер переведён в роль реплики",
                      "Аварийный режим после сбоя репликации"],
           "actions": ["SELECT @@read_only, @@super_read_only", "SET GLOBAL read_only=OFF после проверки роли"]},
    1114: {"name": "ER_RECORD_FILE_FULL", "severity": "critical", "category": "storage",
           "title": "Таблица не может расти дальше",
           "explanation": "Достигнут предел размера таблицы или закончилось место в табличном пространстве.",
           "causes": ["Нет места в datadir", "Ограничение размера MEMORY-таблицы",
                      "Исчерпан autoextend ibdata"],
           "actions": ["df -h datadir", "Проверить innodb_data_file_path и autoextend"]},
    1045: {"name": "ER_ACCESS_DENIED_ERROR", "severity": "critical", "category": "auth",
           "title": "Отказано в доступе к СУБД",
           "explanation": "Учётные данные не приняты: сервис не может подключиться вообще.",
           "causes": ["Сменили пароль пользователя", "Пользователь удалён", "Подключение с неразрешённого хоста"],
           "actions": ["Проверить креды в переменных окружения", "SHOW GRANTS FOR текущего пользователя"]},
    1049: {"name": "ER_BAD_DB_ERROR", "severity": "critical", "category": "config",
           "title": "База данных не существует",
           "explanation": "Указанной схемы нет на сервере — сервис смотрит не туда либо база была удалена.",
           "causes": ["Опечатка в строке подключения", "Подключение к другому серверу", "База удалена"],
           "actions": ["SHOW DATABASES", "Сверить строку подключения с окружением"]},
    1927: {"name": "ER_CONNECTION_KILLED", "severity": "warning", "category": "db_down",
           "title": "Соединение принудительно закрыто сервером",
           "explanation": "Соединение убито администратором или механизмом защиты СУБД.",
           "causes": ["KILL CONNECTION вручную", "Срабатывание max_statement_time"],
           "actions": ["Проверить, кто выполняет KILL", "Смотреть error log СУБД"]},
}


_GENERIC_DB_ERROR = {"name": "UNKNOWN", "severity": "critical", "category": "db_error",
                     "title": "Необработанная ошибка СУБД",
                     "explanation": "Код ошибки не входит в известный набор — требуется ручной разбор.",
                     "causes": ["Неожиданное состояние СУБД или драйвера"],
                     "actions": ["Смотреть полный текст ошибки", "Проверить error log СУБД"]}


def classify_db_error(exc: BaseException) -> Dict:
    """Достаёт код драйвера из исключения SQLAlchemy и возвращает разбор проблемы."""
    code = None
    orig = getattr(exc, "orig", None) or exc
    args = getattr(orig, "args", ())
    if args and isinstance(args[0], int):
        code = args[0]
    else:
        m = re.search(r"\((\d{4})[,)]", str(orig))
        if m:
            code = int(m.group(1))
    info = dict(MYSQL_ERROR_CLASSIFICATION.get(code, _GENERIC_DB_ERROR))
    info["code"] = code
    info["raw"] = str(orig)[:800]
    return info
