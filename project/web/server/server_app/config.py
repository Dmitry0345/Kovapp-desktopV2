# -*- coding: utf-8 -*-

import os
import mimetypes
import tempfile
from pathlib import Path
from sqlalchemy.pool import QueuePool
from fastapi.staticfiles import StaticFiles
import logging


# Числовые настройки читаются через envtools: пустая строка в .env не должна
# ронять сервер на импорте (см. пояснение в самом модуле).
from .envtools import env_bool, env_float, env_int, env_str  # noqa: F401


MAX_CONCURRENT_DB_CONNECTIONS = 100


POOL_SIZE = MAX_CONCURRENT_DB_CONNECTIONS


MAX_OVERFLOW = 20


POOL_TIMEOUT = 30


POOL_RECYCLE = 1800


POOL_PRE_PING = True


ENGINE_POOL_KWARGS = {
    "poolclass": QueuePool,
    "pool_size": POOL_SIZE,
    "max_overflow": MAX_OVERFLOW,
    "pool_timeout": POOL_TIMEOUT,
    "pool_recycle": POOL_RECYCLE,
    "pool_pre_ping": POOL_PRE_PING,
}


def resolve_engine(obj):
    if obj is None:
        return None
    if hasattr(obj, "pool") and hasattr(obj, "dispose"):
        return obj
    kw = getattr(obj, "kw", None)
    if isinstance(kw, dict):
        bind = kw.get("bind")
        if bind is not None:
            return resolve_engine(bind)
    get_bind = getattr(obj, "get_bind", None)
    if callable(get_bind):
        try:
            return resolve_engine(get_bind())
        except Exception:
            return None
    return None


_pool_logger = logging.getLogger(__name__)


def ensure_pool_capacity(engine_or_factory,
                         pool_size=POOL_SIZE,
                         max_overflow=MAX_OVERFLOW,
                         timeout=POOL_TIMEOUT,
                         recycle=POOL_RECYCLE,
                         pre_ping=POOL_PRE_PING,
                         label=""):
    engine = resolve_engine(engine_or_factory)
    if engine is None:
        return False
    pool = getattr(engine, "pool", None)
    if pool is None:
        return False
    if not isinstance(pool, QueuePool):
        return False
    try:
        current_size = pool.size()
    except Exception:
        current_size = 0
    if current_size >= pool_size:
        return True
    dialect = getattr(pool, "_dialect", None)
    if dialect is None:
        # Без диалекта новый пул не сможет обслуживать соединения
        _pool_logger.info("Пул %s: расширение пропущено (диалект недоступен)", label)
        return False
    old_pool = engine.pool
    try:
        new_pool = QueuePool(
            pool._creator,
            pool_size=pool_size,
            max_overflow=max_overflow,
            timeout=timeout,
            recycle=recycle,
            pre_ping=pre_ping,
            dialect=dialect,
            _dispatch=pool.dispatch,  # без событий SQLAlchemy не узнаёт имя базы и версию сервера
        )
        engine.pool = new_pool
        # Проверяем новый пул на живом соединении: если он нерабочий,
        # возвращаем прежний, иначе сломается всё, что идёт следом
        with engine.connect() as probe:
            probe.exec_driver_sql("SELECT 1")
    except Exception as e:
        engine.pool = old_pool
        try:
            new_pool.dispose()
        except Exception:
            pass
        _pool_logger.warning("Пул %s: расширение не удалось (%s: %s), оставлен прежний",
                             label, type(e).__name__, e or "без текста")
        return False
    old_pool.dispose()
    _pool_logger.info("Пул %s: расширен до %d соединений", label, pool_size)
    return True


def pool_status(engine_or_factory):
    engine = resolve_engine(engine_or_factory)
    if engine is None:
        return {"available": False, "reason": "engine not resolved"}
    pool = getattr(engine, "pool", None)
    if pool is None:
        return {"available": False, "reason": "no pool"}
    status = {
        "available": True,
        "pool_class": type(pool).__name__,
    }
    for key, method in (("pool_size", "size"),
                        ("checked_out", "checkedout"),
                        ("checked_in", "checkedin"),
                        ("overflow", "overflow")):
        fn = getattr(pool, method, None)
        if callable(fn):
            try:
                status[key] = fn()
            except Exception:
                pass
    status["max_overflow"] = int(getattr(pool, "_max_overflow", MAX_OVERFLOW))
    if "pool_size" in status and "checked_out" in status:
        capacity = status["pool_size"] + status["max_overflow"]
        status["capacity"] = capacity
        status["free"] = max(0, capacity - status["checked_out"])
    return status


logger = logging.getLogger(__name__)


mimetypes.add_type('video/webm', '.webm')


mimetypes.add_type('video/mp4', '.mp4')


mimetypes.add_type('image/webp', '.webp')


FILE_STORAGE_ROOT = "/file_storage"


LOGIN_ADMIN = (os.getenv("LOGIN_ADMIN") or "").strip()


def _default_storage_root() -> str:
    """Папка файлов: из .env, иначе та из привычных мест, где файлы уже лежат.

    Раньше путь считался только от папки запуска: сервер, запущенный из
    другого места, «терял» все файлы (скачивание и миниатюры отвечали 404).
    """
    configured = (os.getenv("FILE_STORAGE_ROOT") or "").strip()
    if configured:
        return os.path.abspath(configured)
    package_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for candidate in (os.path.abspath("file_storage"), os.path.join(package_dir, "file_storage"),
                      os.path.join(os.path.dirname(package_dir), "file_storage")):
        try:
            with os.scandir(candidate) as entries:
                if any(True for _ in entries):
                    return candidate
        except OSError:
            continue
    return os.path.abspath("file_storage")


FILE_STORAGE_ROOT = _default_storage_root()
# Дополнительные папки со старыми файлами (только чтение), несколько — через «;»
FILE_STORAGE_FALLBACKS = [os.path.abspath(p.strip()) for p in (os.getenv("FILE_STORAGE_FALLBACKS") or "").split(";") if p.strip()]


MESSENGER_STORAGE_ROOT = Path(FILE_STORAGE_ROOT) / "messenger"


# Каталоги создаются ДО StaticFiles: он проверяет наличие папки в конструкторе
# и падает с RuntimeError, обрывая импорт модуля раньше makedirs.
os.makedirs(FILE_STORAGE_ROOT, exist_ok=True)


os.makedirs(MESSENGER_STORAGE_ROOT, exist_ok=True)


video_static_app = StaticFiles(directory=FILE_STORAGE_ROOT)


# ── предпросмотр документов ──────────────────────────────────────────────
PASSWORD_ADMIN = (os.getenv("PASSWORD_ADMIN") or "").strip()


def _env_flag(name: str, default: str = "0") -> bool:
    return (os.getenv(name, default) or "").strip().lower() in ("1", "true", "yes", "on")


# Ключ подписи JWT берётся только из окружения: зашитый в исходники ключ
# позволяет кому угодно выпустить себе токен любого сотрудника.
SECRET_KEY = (os.getenv("JWT_SECRET_KEY") or "").strip()

_WEAK_SECRETS = {"", "secret_key", "secret", "changeme", "change_me", "please_change_me"}

if SECRET_KEY.lower() in _WEAK_SECRETS or len(SECRET_KEY) < 32:
    raise RuntimeError(
        "JWT_SECRET_KEY не задан, слишком короткий (нужно >= 32 символов) или "
        "оставлен типовым. Сгенерируйте: openssl rand -hex 64 и пропишите в .env. "
        "Напомним: .env читается из каталога НА УРОВЕНЬ ВЫШЕ пакета server/."
    )


ALGORITHM = "HS256"


ACCESS_TOKEN_EXPIRE_MINUTES = env_int("ACCESS_TOKEN_EXPIRE_MINUTES", 10080)


# Аварийный вход по логину/паролю из .env мимо базы. По умолчанию выключен.
ADMIN_FALLBACK_ENABLED = _env_flag("ADMIN_FALLBACK_ENABLED", "0")

_WEAK_ADMIN = {"", "1234", "12345", "123456", "admin", "administrator",
               "password", "qwerty", "0000", "root"}

if ADMIN_FALLBACK_ENABLED:
    if (not LOGIN_ADMIN or not PASSWORD_ADMIN
            or LOGIN_ADMIN.strip().lower() in _WEAK_ADMIN
            or PASSWORD_ADMIN.strip().lower() in _WEAK_ADMIN
            or len(PASSWORD_ADMIN) < 16):
        raise RuntimeError(
            "ADMIN_FALLBACK_ENABLED=1 требует стойких LOGIN_ADMIN и PASSWORD_ADMIN "
            "(пароль от 16 символов, не из списка типовых)"
        )


if ADMIN_FALLBACK_ENABLED:
    # Сам логин в журнал не пишем: файл живёт долго и попадает в резервные
    # копии, а это половина аварийных учётных данных
    logger.warning("Аварийный вход ВКЛЮЧЁН (длина логина %d, длина пароля %d)",
                   len(LOGIN_ADMIN), len(PASSWORD_ADMIN))
else:
    logger.info("Аварийный вход выключен (ADMIN_FALLBACK_ENABLED=0)")


# Под каким сотрудником работает аварийный вход. Должность этой записи
# определяет реальные права: проверки ролей смотрят в базу, а не в токен.
ADMIN_FALLBACK_USER_ID = env_int("ADMIN_FALLBACK_USER_ID", 1)


# Сброс пароля
RESET_CODE_LENGTH = env_int("RESET_CODE_LENGTH", 6)


RESET_CODE_TTL_MINUTES = env_int("RESET_CODE_TTL_MINUTES", 15)


RESET_CODE_MAX_ATTEMPTS = env_int("RESET_CODE_MAX_ATTEMPTS", 5)


RESET_REQUEST_COOLDOWN_SEC = env_int("RESET_REQUEST_COOLDOWN_SEC", 60)


MIN_PASSWORD_LENGTH = env_int("MIN_PASSWORD_LENGTH", 8)


# Swagger и схема OpenAPI. В проде выключены.
DOCS_ENABLED = _env_flag("DOCS_ENABLED", "0")


# Пусто = разрешить любой Origin. Токен ходит в заголовке, куки не
# используются (allow_credentials=False), поэтому чужая страница чужой
# токен не получит. Значение оставлено настраиваемым, а список методов
# и заголовков сужен до реально нужных.
CORS_ALLOW_ORIGINS = [o.strip() for o in (os.getenv("CORS_ALLOW_ORIGINS") or "").split(",") if o.strip()]


# Адреса, с которых служебные /health/* доступны без токена
TRUSTED_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


# ── Версия 2: проекты, медиа, push ───────────────────────────────────────────

# open — временно все видят все проекты (по требованию заказчика);
# strict — правила из project_permissions.py
PROJECT_ACCESS_MODE = (os.getenv("PROJECT_ACCESS_MODE", "open") or "open").strip().lower()

# Обработка изображений при загрузке.
# IMAGE_KEEP_ORIGINAL=1 — файл сохраняется байт в байт (только миниатюра рядом);
# IMAGE_MAX_SIDE=0 — не уменьшать, только перекодировать с IMAGE_JPEG_QUALITY
IMAGE_KEEP_ORIGINAL = (os.getenv("IMAGE_KEEP_ORIGINAL", "0") or "0").strip().lower() in ("1", "true", "yes", "on")
IMAGE_MAX_SIDE = env_int("IMAGE_MAX_SIDE", 3200)       # длинная сторона, px
IMAGE_JPEG_QUALITY = env_int("IMAGE_JPEG_QUALITY", 88)
THUMB_MAX_SIDE = env_int("THUMB_MAX_SIDE", 360)        # миниатюры плиток
THUMB_JPEG_QUALITY = env_int("THUMB_JPEG_QUALITY", 78)

# Видео: VP9/WebM остаётся ради QtWebEngine, но кодируем заметно быстрее
VIDEO_MAX_SIDE = env_int("VIDEO_MAX_SIDE", 1920)       # 1920 = Full HD без потерь разрешения
VIDEO_LONG_S = env_float("VIDEO_LONG_S", 60.0)         # видео длиннее — сжимаются до VIDEO_LONG_SIDE (как телефон и браузер)
VIDEO_LONG_SIDE = env_int("VIDEO_LONG_SIDE", 1280)     # 1280 = HD: файл в ~2,25 раза меньше Full HD; 0 — не уменьшать
# днём сервер сжимает по тем же правилам, что устройства; 1280 — режим экономии: всё, что пережимает сервер, не больше 1280
VIDEO_SERVER_MAX_SIDE = env_int("VIDEO_SERVER_MAX_SIDE", 0)
# ночное пережатие: днём видео принимаются как есть, ночью сервер пережимает видео, принятые без сжатия
VIDEO_NIGHT_ENABLED = env_bool("VIDEO_NIGHT_ENABLED", True)
VIDEO_NIGHT_HOUR = env_int("VIDEO_NIGHT_HOUR", 3)               # начало, час по времени сервера
VIDEO_NIGHT_END_HOUR = env_int("VIDEO_NIGHT_END_HOUR", 7)       # после этого часа новые файлы не берутся
VIDEO_NIGHT_MIN_GAIN = env_float("VIDEO_NIGHT_MIN_GAIN", 0.15)  # заменять, только если файл меньше хотя бы на 15 %
VIDEO_NIGHT_LOOKBACK_DAYS = env_int("VIDEO_NIGHT_LOOKBACK_DAYS", 7)  # недоделанное за прошлые ночи берётся до N дней
# проба перед полным ночным сжатием: N секунд из середины пережимаются теми же настройками (0 — без пробы)
VIDEO_NIGHT_SAMPLE_S = env_float("VIDEO_NIGHT_SAMPLE_S", 10.0)
VIDEO_NIGHT_SAMPLE_MIN_S = env_float("VIDEO_NIGHT_SAMPLE_MIN_S", 30.0)   # короче — сразу полное сжатие
VIDEO_NIGHT_SAMPLE_MARGIN = env_float("VIDEO_NIGHT_SAMPLE_MARGIN", 0.05)  # пропуск, только если проба ниже порога с запасом
VIDEO_NIGHT_MAX_ATTEMPTS = env_int("VIDEO_NIGHT_MAX_ATTEMPTS", 3)       # сбоев полного сжатия до отказа от видео
VIDEO_CRF = env_int("VIDEO_CRF", 32)                   # как в версии 1; меньше = лучше и крупнее
VIDEO_CPU_USED = env_int("VIDEO_CPU_USED", 4)          # 0..8, больше = быстрее
VIDEO_MAX_FPS = env_int("VIDEO_MAX_FPS", 30)
VIDEO_AUDIO_BITRATE = os.getenv("VIDEO_AUDIO_BITRATE", "64k")
# Кодек для хранения видео: h264 (быстро, MP4) или vp9 (медленно, WebM — как в версии 1)
VIDEO_CODEC = (os.getenv("VIDEO_CODEC", "h264") or "h264").strip().lower()
VIDEO_H264_CRF = env_int("VIDEO_H264_CRF", 26)         # 18 лучше/крупнее, 28 хуже/мельче
VIDEO_H264_PRESET = os.getenv("VIDEO_H264_PRESET", "veryfast")  # быстрее fast примерно в полтора раза, файл крупнее на ~10%
# Целевой поток: бит на пиксель кадра. 0.06 даёт ≈3.7 Мбит/с для 1080p30
# и ≈1.7 Мбит/с для 720p30 — этого хватает для съёмки объектов.
VIDEO_TARGET_BPP = env_float("VIDEO_TARGET_BPP", 0.06)
# Во сколько раз исходник может превышать цель и всё ещё считаться экономным:
# такой файл только переупаковывается, без нагрузки на процессор.
VIDEO_REMUX_SLACK = env_float("VIDEO_REMUX_SLACK", 1.25)
# Доля процессора, которую разрешено занять перекодированию. Используется
# только как запасной вариант: обычно число потоков считает распорядитель
# нагрузки (load_governor) по живым замерам.
VIDEO_CPU_PERCENT = env_int("VIDEO_CPU_PERCENT", 50)
# Сколько видео перекодируется одновременно. 0 — решает распорядитель,
# число — жёсткий потолок вручную (распорядитель выше него не поднимется).
VIDEO_MAX_PARALLEL_HARD = env_int("VIDEO_MAX_PARALLEL", 0)
VIDEO_MAX_PARALLEL = VIDEO_MAX_PARALLEL_HARD or 1     # совместимость со старым кодом
# Столько же для фото и прочих быстрых задач: 0 — решает распорядитель нагрузки,
# число — жёсткий потолок вручную.
PROCESS_MAX_PARALLEL_HARD = env_int("PROCESS_MAX_PARALLEL", 0)
# Насколько понизить приоритет процесса ffmpeg (0 — как у всех, 19 — минимум)
VIDEO_NICE = env_int("VIDEO_NICE", 19)

# Фото: WebP жмёт заметно плотнее JPEG при той же картинке и почти не грузит
# процессор. method=4 — разумная середина между скоростью и плотностью.
IMAGE_WEBP_QUALITY = env_int("IMAGE_WEBP_QUALITY", 80)
IMAGE_WEBP_METHOD = env_int("IMAGE_WEBP_METHOD", 4)
IMAGE_PREFER_WEBP = (os.getenv("IMAGE_PREFER_WEBP", "1") or "1").strip() == "1"
VIDEO_H264_AUDIO_BITRATE = os.getenv("VIDEO_H264_AUDIO_BITRATE", "128k")
# Готовый H.264 не перекодируется, а только переупаковывается (секунда вместо минут)
VIDEO_REMUX_READY = (os.getenv("VIDEO_REMUX_READY", "1") or "1").strip() == "1"

# Уровень gzip для документов: 6 почти не уступает 9 по размеру, но в разы быстрее
GZIP_LEVEL = env_int("GZIP_LEVEL", 6)
# Ответы API сжимаются на лету; статика — один раз при запуске (см. gzip_tools)
GZIP_DYNAMIC_LEVEL = env_int("GZIP_DYNAMIC_LEVEL", 9)
STATIC_PRECOMPRESS = os.getenv("STATIC_PRECOMPRESS", "1").lower() not in ("0", "false", "no")

# Web Push (VAPID). Пусто — push выключен, остальное работает
VAPID_PUBLIC_KEY = (os.getenv("VAPID_PUBLIC_KEY") or "").strip()
VAPID_PRIVATE_KEY = (os.getenv("VAPID_PRIVATE_KEY") or "").strip()
VAPID_SUBJECT = (os.getenv("VAPID_SUBJECT") or "mailto:admin@example.com").strip()


def _auto_vapid():
    if VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY:
        return VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY
    if not env_bool("PUSH_AUTO_KEYS", True):
        return VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY
    try:
        import base64
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        pem_path = Path(os.getenv("VAPID_KEY_FILE") or (Path(__file__).resolve().parent.parent.parent / "vapid_private.pem"))
        if pem_path.is_file():
            key = serialization.load_pem_private_key(pem_path.read_bytes(), password=None)
        else:
            key = ec.generate_private_key(ec.SECP256R1())
            pem_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                                   serialization.PrivateFormat.PKCS8,
                                                   serialization.NoEncryption()))
            try:
                os.chmod(pem_path, 0o600)
            except Exception:
                pass
            logging.getLogger(__name__).info("Ключи VAPID созданы автоматически: %s", pem_path)
        raw = key.public_key().public_bytes(serialization.Encoding.X962,
                                            serialization.PublicFormat.UncompressedPoint)
        public = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
        return public, str(pem_path)
    except Exception as e:
        logging.getLogger(__name__).warning("Ключи VAPID не созданы автоматически: %s", e)
        return VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY


VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY = _auto_vapid()
PUSH_ENABLED = bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY)

# Каталог собранного веб-приложения; пусто — сервер его не раздаёт (раздаёт nginx)
WEB_APP_DIR = (os.getenv("WEB_APP_DIR") or "").strip()


def ensure_dialect_ready(engine_or_factory, label: str = "") -> None:
    """Диалект узнаёт имя базы и версию сервера при первом подключении пула.

    Раньше пул заменялся без событий, это не происходило, и создание таблиц
    падало на проверке имени базы (AssertionError без текста).
    """
    engine = resolve_engine(engine_or_factory)
    dialect = getattr(engine, "dialect", None)
    if dialect is None or getattr(dialect, "name", "") not in ("mysql", "mariadb"):
        return
    if getattr(dialect, "default_schema_name", None) is not None:
        return
    with engine.connect() as conn:
        dialect.initialize(conn)
    _pool_logger.info("Пул %s: параметры базы получены (%s)", label, dialect.default_schema_name)
