# -*- coding: utf-8 -*-

import os
import re
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Depends, HTTPException, status, Query, Request, Response, UploadFile, File, Form, \
    WebSocket, WebSocketDisconnect
from fastapi.middleware.gzip import GZipMiddleware

from .config import GZIP_DYNAMIC_LEVEL
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig
import string
from fastapi.security import OAuth2PasswordRequestForm, OAuth2PasswordBearer
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError, InterfaceError, InternalError
from jose import JWTError, jwt
from server.database.init_database import SessionLocal
from server.database.init_database_messenger import SessionLocalChat, BaseChat, engine_chat
from fastapi.middleware.cors import CORSMiddleware

from server.database.models import *

from .config import (ALGORITHM, CORS_ALLOW_ORIGINS, DOCS_ENABLED, FILE_STORAGE_ROOT,
                     RESET_CODE_LENGTH, SECRET_KEY, TRUSTED_LOCAL_HOSTS, logger)
from .envtools import env_int, env_str


def _fast_json():
    """orjson собирает ответы в разы быстрее встроенного json; нет библиотеки — обычный ответ."""
    try:
        import orjson  # noqa: F401
        from fastapi.responses import ORJSONResponse
        return ORJSONResponse
    except Exception:
        from fastapi.responses import JSONResponse
        return JSONResponse


app = FastAPI(
    default_response_class=_fast_json(),
    docs_url="/docs" if DOCS_ENABLED else None,
    redoc_url="/redoc" if DOCS_ENABLED else None,
    openapi_url="/openapi.json" if DOCS_ENABLED else None,
)


# ── завершённый проект — только чтение ────────────────────────────────────────
# Изменения задач, этапов, файлов и склада завершённого проекта отклоняются здесь,
# одним местом для всех адресов: проект определяется по номеру из адреса.
import re as _re
from fastapi.responses import JSONResponse as _JSONResponse

_ARCHIVED_DETAIL = "Проект завершён — действия недоступны. Сначала верните его в работу."
_LOCK_RULES = [
    (_re.compile(r"^/projects/(\d+)(?:/(?!complete$|reopen$).*)?$"), "project"),
    (_re.compile(r"^/projects/files/(\d+)$"), "project_file"),
    (_re.compile(r"^/tasks/(\d+)(?:/.*)?$"), "task"),
    (_re.compile(r"^/stages/(\d+)/participants$"), "stage"),
    (_re.compile(r"^/files/task/(\d+)$"), "task_file"),
    (_re.compile(r"^/files/stage/(\d+)$"), "stage_file"),
    (_re.compile(r"^/warehouse/issues/(\d+)/(?:confirm|cancel)$"), "warehouse_issue"),
]


def _locked_project(kind: str, obj_id: int) -> bool:
    from server.database.models import (Project, ProjectFile, Task, TaskFile, TaskStageDetails,
                                        TaskStageFile, WarehouseIssue)
    db = SessionLocal()
    try:
        pid = None
        if kind == "project":
            pid = obj_id
        elif kind == "project_file":
            pid = db.query(ProjectFile.project_id).filter(ProjectFile.id == obj_id).scalar()
        elif kind == "warehouse_issue":
            pid = db.query(WarehouseIssue.project_id).filter(WarehouseIssue.id == obj_id).scalar()
        else:
            task_id = obj_id if kind == "task" else None
            if kind == "task_file":
                task_id = db.query(TaskFile.task_id).filter(TaskFile.id == obj_id).scalar()
            elif kind in ("stage", "stage_file"):
                stage_id = obj_id if kind == "stage" else \
                    db.query(TaskStageFile.stage_detail_id).filter(TaskStageFile.id == obj_id).scalar()
                task_id = db.query(TaskStageDetails.task_id).filter(TaskStageDetails.id == stage_id).scalar() if stage_id else None
            pid = db.query(Task.project_id).filter(Task.id == task_id).scalar() if task_id else None
        if not pid:
            return False
        return bool(db.query(Project.is_archived).filter(Project.id == pid).scalar())
    except Exception:
        return False   # проверка не должна ронять запрос — обработчик всё равно проверит права
    finally:
        db.close()


from starlette.concurrency import run_in_threadpool  # noqa: E402


@app.middleware("http")
async def _archived_project_lock(request, call_next):
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        path = request.url.path
        for rx, kind in _LOCK_RULES:
            m = rx.match(path)
            if m:
                # запрос к базе — в пуле потоков: цикл событий не ждёт MariaDB
                if await run_in_threadpool(_locked_project, kind, int(m.group(1))):
                    return _JSONResponse({"detail": _ARCHIVED_DETAIL}, status_code=409)
                break
    return await call_next(request)



app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS or ["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Range", "Accept"],
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length", "Content-Disposition",
                    "Content-Encoding", "X-Original-Length"],
    max_age=86400,
)


class ConditionalGZipMiddleware:
    """
    gzip-сжатие для всех ответов, КРОМЕ потоков файлов/медиа.

    Range/206-ответы и видео сжимать нельзя (это ломает потоковую отдачу и
    декодирование в плеере), а файлы под /file_storage уже хранятся в своём
    gzip и распаковываются на лету. Поэтому /file_storage/ и /chat/download/
    отдаём «как есть», всё остальное — через стандартный GZipMiddleware.
    """

    def __init__(self, app, minimum_size: int = 500, compresslevel: int = 9):
        self.app = app
        self._gzip = GZipMiddleware(app, minimum_size=minimum_size, compresslevel=compresslevel)

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope.get("type") == "http" and (FILE_ROUTES.match(path) or self._precompressed(path)):
            await self.app(scope, receive, send)
            return
        await self._gzip(scope, receive, send)

    @staticmethod
    def _precompressed(path: str) -> bool:
        """Для статики есть свежая готовая .gz — сжимать заново не нужно."""
        if not path.startswith("/app"):
            return False
        try:
            from . import gzip_tools
            from .config import WEB_APP_DIR
            if not WEB_APP_DIR:
                return False
            rel = path[4:].lstrip("/")
            if not rel or rel.endswith("/"):
                rel += "index.html"
            src = os.path.join(WEB_APP_DIR, rel)
            return gzip_tools.fresh(os.stat(src), os.stat(src + ".gz"))
        except (OSError, ValueError, ImportError):
            return False


FILE_ROUTES = re.compile(
    r"^/(?:file_storage/|chat/download/|file_preview/"
    r"|(?:task|stages)/files/\d+/(?:download|thumb|stream/)"
    r"|files/\d+/view"
    r"|projects/files/\d+/(?:download|view|thumb|stream/)"
    r"|employees/\d+/photo|equipment/\d+/photo)"
)


# за nginx сжатие делает он (APP_GZIP=0): Python не тратит на это время
if (os.getenv("APP_GZIP") or "1").strip() != "0":
    app.add_middleware(ConditionalGZipMiddleware, minimum_size=1000, compresslevel=GZIP_DYNAMIC_LEVEL)


# ── заголовки безопасности ───────────────────────────────────────────────
# Они не чинят уязвимости, а ограничивают ущерб, если что-то всё-таки
# просочилось: браузер отказывается выполнять чужой скрипт, отправлять данные
# на посторонний адрес и показывать приложение внутри чужой страницы.
#
# Политика содержимого подобрана под то, что приложение реально использует:
#   script-src 'self'          — встроенных скриптов в разметке не осталось;
#   'wasm-unsafe-eval'         — только WebAssembly (картинки JPEG 2000 в PDF),
#                                произвольный код по-прежнему запрещён;
#   style-src  'unsafe-inline' — стили задаются атрибутом style у элементов,
#                                без этого разъедется вся вёрстка;
#   connect-src 'self' ws/wss  — API и сокет с того же адреса;
#   img/media   data: и blob:  — миниатюры и предпросмотр перед отправкой.
# Значение можно переопределить переменной CONTENT_SECURITY_POLICY.
# Адреса KLIPY нужны разделу GIF: по условиям сервиса картинки грузятся
# напрямую с их доставки, без нашего посредничества.
KLIPY_MEDIA = "https://static.klipy.com https://static1.klipy.com https://static2.klipy.com"
KLIPY_API = "https://api.klipy.com"

DEFAULT_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'wasm-unsafe-eval'; "
    "style-src 'self' 'unsafe-inline'; "
    f"img-src 'self' data: blob: {KLIPY_MEDIA}; "
    f"media-src 'self' blob: {KLIPY_MEDIA}; "
    "font-src 'self' data:; "
    f"connect-src 'self' ws: wss: {KLIPY_API}; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)


def _csp_with_host(policy: str) -> str:
    """Если веб-версия открывается с одного адреса, а API отвечает с другого,
    в разрешения нужно добавить адрес сервера — иначе браузер оборвёт запросы.
    Адрес берём из HOST, он же используется клиентом."""
    host = env_str("HOST", "")
    if not host:
        return policy
    ws_host = host.replace("https://", "wss://").replace("http://", "ws://")
    return (policy
            .replace("connect-src 'self'", f"connect-src 'self' {host} {ws_host}")
            .replace("img-src 'self'", f"img-src 'self' {host}")
            .replace("media-src 'self'", f"media-src 'self' {host}"))


CONTENT_SECURITY_POLICY = env_str("CONTENT_SECURITY_POLICY", _csp_with_host(DEFAULT_CSP))


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    headers = response.headers
    # Отдача файлов ставит свои, более строгие значения — их не трогаем
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("X-Frame-Options", "DENY")
    headers.setdefault("Referrer-Policy", "no-referrer")
    headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(self), camera=(self)")   # без payment: встроенный браузер Android его не знает и пишет ошибку
    if CONTENT_SECURITY_POLICY:
        headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    return response


scheduler = BackgroundScheduler()


os.makedirs(FILE_STORAGE_ROOT, exist_ok=True)


mail_conf = ConnectionConfig(
    MAIL_USERNAME=os.getenv("MAIL_USERNAME"),
    MAIL_PASSWORD=os.getenv("MAIL_PASSWORD"),
    MAIL_FROM=os.getenv("MAIL_FROM"),
    MAIL_PORT=env_int("MAIL_PORT", 587),
    MAIL_SERVER=os.getenv("MAIL_SERVER", "smtp.gmail.com"),
    MAIL_STARTTLS=os.getenv("MAIL_STARTTLS", "True").lower() == "true",
    MAIL_SSL_TLS=os.getenv("MAIL_SSL_TLS", "False").lower() == "true",
    USE_CREDENTIALS=True,
    VALIDATE_CERTS=True
)


fm = FastMail(mail_conf)


def _close_session_safely(db, broken: bool) -> None:
    """Закрывает сессию так, чтобы сломанное соединение не вернулось в пул.

    После MemoryError или обрыва протокола соединение MySQL остаётся в
    непредсказуемом состоянии («Packet sequence number wrong»); обычный
    close() пытается сделать ROLLBACK на нём же и падает второй раз, а
    соединение всё равно возвращается в пул и портит следующий запрос.
    """
    try:
        if broken:
            try:
                db.invalidate()      # соединение выбрасывается, пул откроет новое
            except Exception:
                pass
        db.close()
    except Exception:
        try:
            db.invalidate()
        except Exception:
            pass


def get_db():
    db = SessionLocal()
    broken = False
    try:
        yield db
    except (MemoryError, OperationalError, InterfaceError, InternalError):
        broken = True
        raise
    finally:
        _close_session_safely(db, broken)


class LazySession:
    """
    Прокси над сессией SQLAlchemy.
    """

    __slots__ = ("_factory", "_session", "_label")

    def __init__(self, factory, label=""):
        self._factory = factory
        self._session = None
        self._label = label

    def _ensure(self):
        if self._session is None:
            self._session = self._factory()
        return self._session

    def __getattr__(self, name):
        # Вызывается только для того, чего нет в __slots__,
        # то есть для любого настоящего метода/атрибута сессии
        return getattr(self._ensure(), name)

    def release(self):
        """Закрывает сессию и возвращает соединение в пул. Безопасно повторять."""
        if self._session is None:
            return
        session = self._session
        self._session = None
        try:
            session.close()
        except Exception as e:
            logger.debug(f"Не удалось закрыть сессию {self._label}: {e}")


def get_user_id_from_token_sync(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise JWTError()
        return int(user_id)
    except JWTError:
        return None


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")
optional_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login", auto_error=False)


async def get_current_user_id(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
        # файловый токен годится только для ссылок на файлы
        if payload.get("scope") == FILE_TOKEN_SCOPE:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="Этот токен выдан только для чтения файлов")
        return int(user_id)
    except JWTError:
        raise credentials_exception


FILE_TOKEN_SCOPE = "files"
FILE_TOKEN_MINUTES = 20


def issue_file_token(user_id: int) -> dict:
    """Короткоживущий токен только для ссылок на файлы.

    Адрес картинки или видео нельзя снабдить заголовком Authorization, поэтому
    токен уходит в строку запроса — а она попадает в логи прокси, историю
    браузера и заголовок Referer. Раньше туда уходил основной токен сессии:
    утёкшее значение открывало весь API. Теперь выдаётся отдельный токен: он
    живёт 20 минут и годится только для чтения файлов.
    """
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "exp": now + timedelta(minutes=FILE_TOKEN_MINUTES), "iat": now,
               "scope": FILE_TOKEN_SCOPE}
    return {"access_token": jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM),
            "expires_in": FILE_TOKEN_MINUTES * 60}


def _decode(token: str):
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None


async def get_current_user_id_from_header_or_query(
        bearer_token: Optional[str] = Depends(optional_oauth2_scheme),
        query_token: Optional[str] = Query(None, alias="token"),
):
    """Файловые адреса: обычный токен в заголовке или файловый в строке запроса."""
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    for token in (bearer_token, query_token):
        if not token:
            continue
        payload = _decode(token)
        if not payload or payload.get("sub") is None:
            continue
        return int(payload["sub"])
    raise unauthorized

ACCESS = ("Директор", "Администратор")      # прежнее правило по должности — запасное

# ── Права по маркеру в базе ───────────────────────────────────────────────
# Раньше права выводились из слова в названии должности: «Старший инженер» —
# руководитель отдела, «Директор» — руководство. Опечатка в должности молча
# меняла права, а переименование должности их теряло. Теперь решает поле
# employees.status, а разбор должности остаётся только как запасной путь для
# записей, заведённых до перехода.
STATUS_WORKER = "работник"
STATUS_HEAD = "руководитель отдела"
STATUS_DIRECTOR = "директор"
STATUS_ADMIN = "администратор"
EMPLOYEE_STATUSES = (STATUS_WORKER, STATUS_HEAD, STATUS_DIRECTOR, STATUS_ADMIN)
MANAGEMENT_STATUSES = (STATUS_DIRECTOR, STATUS_ADMIN)


def employee_status(employee) -> str:
    """Маркер прав сотрудника в нижнем регистре.

    Пусто — выводим из должности по старому правилу: до выполнения переходного
    скрипта права не должны ни пропасть, ни расшириться.
    """
    if employee is None:
        return ""
    value = (getattr(employee, "status", None) or "").strip().lower()
    if value in EMPLOYEE_STATUSES:
        return value
    position = (getattr(employee, "position", None) or "").strip()
    if position == "Директор":
        return STATUS_DIRECTOR
    if position == "Администратор":
        return STATUS_ADMIN
    if is_department_head_position(position):
        return STATUS_HEAD
    return STATUS_WORKER


def is_management_status(employee) -> bool:
    """Директор или администратор — полный доступ."""
    return employee_status(employee) in MANAGEMENT_STATUSES


def is_hidden_account(employee) -> bool:
    """Служебная запись администратора — её не предлагают в выборах.

    В переписке имя остаётся: скрывать автора уже отправленных сообщений
    нельзя. Прячется именно выбор — новый собеседник, участник группы,
    исполнитель. Клиент фильтрует списки, сервер закрывает обход клиента.
    """
    return employee_status(employee) == STATUS_ADMIN


def hidden_account_ids(db, ids=None) -> set:
    """Номера служебных записей: пусто, если таких сотрудников нет"""
    from server.database.models import Employee
    query = db.query(Employee.id, Employee.status, Employee.position)
    if ids:
        query = query.filter(Employee.id.in_(list(ids)))
    out = set()
    for row in query.all():
        stub = type("E", (), {"status": row.status, "position": row.position})
        if is_hidden_account(stub):
            out.add(row.id)
    return out


def is_head_status(employee) -> bool:
    """Руководитель отдела: права в пределах своего отдела."""
    return employee_status(employee) == STATUS_HEAD


def can_lead_status(employee) -> bool:
    """Руководство или руководитель отдела."""
    return employee_status(employee) in (STATUS_HEAD,) + MANAGEMENT_STATUSES


async def is_admin_user(user_id: int = Depends(get_current_user_id), db: Session = Depends(get_db)):
    employee = db.query(Employee).filter_by(id=user_id).first()
    return is_management_status(employee)


def _is_local_request(request: Request) -> bool:
    """Запрос пришёл прямо с сервера, а не через прокси.

    Проверять только remote_addr нельзя: за nginx он у всех 127.0.0.1.
    Прокси всегда проставляет X-Forwarded-For, поэтому его наличие и
    отличает внешнего клиента от локального curl.
    """
    if request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip"):
        return False
    client = request.client.host if request.client else ""
    return client in TRUSTED_LOCAL_HOSTS


async def require_admin_or_local(
        request: Request,
        token: Optional[str] = Depends(optional_oauth2_scheme),
        db: Session = Depends(get_db),
):
    """Служебные эндпоинты: админский токен либо запрос с самого сервера."""
    if _is_local_request(request):
        return True

    user_id = get_user_id_from_token_sync(token) if token else None
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется авторизация",
            headers={"WWW-Authenticate": "Bearer"},
        )

    employee = db.query(Employee).filter_by(id=user_id).first()
    if not employee or not is_management_status(employee):
        raise HTTPException(status_code=403, detail="Доступно только администратору")
    return True



# Должности с полным доступом: видят все задачи независимо от участия и отдела.
# Отдельной должности «Руководитель» больше нет — руководят директора,
# а принадлежность к отделу «Руководство» сама по себе прав не даёт.
FULL_ACCESS_POSITIONS = ("Директор", "Администратор")


def user_has_full_task_access(db: Session, user_id: int) -> bool:
    employee = db.query(Employee).filter_by(id=user_id).first()
    return is_management_status(employee)


# Старший по отделу — это старший или главный специалист: слово в названии
# должности, чтобы правило работало и для «Старший инженер», и для
# «Главный энергетик». Отдельной должности «Руководитель» нет.
DEPARTMENT_HEAD_POSITIONS = ()
DEPARTMENT_HEAD_MARKERS = ("Старший", "Главный")


def is_department_head_position(position: Optional[str]) -> bool:
    position = (position or "").strip()
    if not position:
        return False
    if position in DEPARTMENT_HEAD_POSITIONS:
        return True
    return any(marker in position for marker in DEPARTMENT_HEAD_MARKERS)


def user_is_department_head_for_task(db: Session, user_id: int, task: Task) -> bool:
    """Руководитель отдела вправе управлять задачей, все этапы которой
    относятся к его отделу.

    Если этапы принадлежат разным отделам или отдел этапа не указан —
    прав нет: задача выходит за пределы зоны ответственности.
    """
    employee = db.query(Employee).filter(Employee.id == user_id).first()
    if not employee or not employee.department:
        return False
    if not is_head_status(employee):
        return False

    stage_departments = {stage.department_name for stage in (task.stage_details or [])}
    if len(stage_departments) != 1:
        return False

    (task_department,) = stage_departments
    return task_department is not None and task_department == employee.department


def user_can_access_task(db: Session, user_id: int, task) -> bool:
    """Задача доступна автору, исполнителям, ответственным и руководству.

    Сотрудник, убранный из состава, теряет доступ: задача не отдаётся
    ни списком, ни по прямому id.
    """
    if user_has_full_task_access(db, user_id):
        return True
    if getattr(task, "creator_id", None) == user_id:
        return True
    if any(e.id == user_id for e in (task.assigned_employees or [])):
        return True
    if any(e.id == user_id for e in (task.responsible_persons or [])):
        return True
    return False


def user_can_read_task_files(db: Session, user_id: int, task) -> bool:
    if user_can_access_task(db, user_id, task):
        return True
    stage_ids = [s.id for s in (getattr(task, "stage_details", None) or [])]
    if not stage_ids:
        return False
    if db.query(task_stage_participants.c.employee_id).filter(
            task_stage_participants.c.stage_detail_id.in_(stage_ids),
            task_stage_participants.c.employee_id == user_id).first():
        return True
    return db.query(TaskStageLead.employee_id).filter(
        TaskStageLead.stage_detail_id.in_(stage_ids),
        TaskStageLead.employee_id == user_id).first() is not None


def task_is_archived(task) -> bool:
    return bool(getattr(task, "archived_at", None)) or (getattr(task, "activity", "") or "") == "Архивная"


# Создавать и вести задачи вправе старшие и главные сотрудники, директор и
# администратор. Правило одно для всех клиентов, проверка — на сервере.
def user_can_create_tasks(db: Session, user_id: int) -> bool:
    employee = db.query(Employee).filter_by(id=user_id).first()
    if not employee:
        return False
    return can_lead_status(employee)


def user_leads_stage(db: Session, user_id: int, stage) -> bool:
    """Этап ведёт отдел, указанный в самом этапе."""
    employee = db.query(Employee).filter_by(id=user_id).first()
    if not employee:
        return False
    if is_management_status(employee):
        return True
    department = getattr(stage, "department_name", None)
    return bool(department and employee.department == department)


def ensure_task_access(db: Session, user_id: int, task) -> None:
    """Читать задачу вправе её состав и руководство."""
    if not user_can_access_task(db, user_id, task):
        raise HTTPException(status_code=403, detail="Нет доступа к этой задаче")


def ensure_task_manage(db: Session, user_id: int, task) -> None:
    """Менять состав задачи вправе руководство и руководитель её отдела."""
    if user_has_full_task_access(db, user_id):
        return
    if user_is_department_head_for_task(db, user_id, task):
        return
    raise HTTPException(status_code=403,
                        detail="Изменять задачу может руководство или руководитель её отдела")


def ensure_stage_edit(db: Session, user_id: int, task, stage) -> None:
    """Правка этапа: состав задачи плюс принадлежность к ведущему отделу."""
    ensure_task_access(db, user_id, task)
    if not user_leads_stage(db, user_id, stage):
        raise HTTPException(status_code=403,
                            detail="Этап ведёт другой отдел — изменения недоступны")


# Справочник организаций ведут администратор, директор, старшие и главные
# сотрудники, а также любой сотрудник Договорного отдела.
CONTRACT_DEPARTMENT = "Договорной отдел"


def user_can_manage_organizations(db: Session, user_id: int) -> bool:
    employee = db.query(Employee).filter_by(id=user_id).first()
    if not employee:
        return False
    if can_lead_status(employee):
        return True
    return (employee.department or "").strip() == CONTRACT_DEPARTMENT


def verify_password(plain_password: str, hashed_password: str) -> bool:
    if not hashed_password:
        return False
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))


def get_user(db: Session, username: str):
    return db.query(User).filter(User.username == username).first()


def generate_reset_code():
    return ''.join(secrets.choice(string.digits) for _ in range(RESET_CODE_LENGTH))


def get_chat_db():
    db = SessionLocalChat()
    broken = False
    try:
        yield db
    except (MemoryError, OperationalError, InterfaceError, InternalError):
        broken = True
        raise
    finally:
        _close_session_safely(db, broken)


# ── полосы запросов: самая внешняя прослойка ──
from .lanes import install as _install_lanes  # noqa: E402
_install_lanes(app)
