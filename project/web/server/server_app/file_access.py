import logging
import re
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from server.database.models import (Employee, EquipmentItem, Project, ProjectFile, Task, TaskFile,
                                    TaskStageDetails, TaskStageFile)

from .chat_models import ChatModel, ChatParticipantModel, MessageModel
from .core import is_management_status, user_can_read_task_files
from .envtools import env_bool, env_int
from .project_permissions import can_view_project

logger = logging.getLogger("kovcheg.file_access")


# ===== настройки =====

STRICT = env_bool("FILE_ACCESS_STRICT", False)
DECISION_TTL = max(0, env_int("FILE_ACCESS_CACHE_SECONDS", 60))
CHAT_OWNER_TTL = 60
OWNER_CACHE_SIZE = 50000
DECISION_CACHE_SIZE = 20000
DERIVED_SUFFIXES = (".thumb.jpg", ".preview.pdf")
CHAT_FOLDER = re.compile(r"^messenger/(?:avatars/)?chat_(\d+)/", re.IGNORECASE)
PROJECT_FOLDER = re.compile(r"^projects/(\d+)/", re.IGNORECASE)


# ===== владелец файла =====

@dataclass(frozen=True)
class Owner:
    kind: str
    ids: Tuple[int, ...] = ()

    @property
    def key(self) -> str:
        return self.kind + ":" + ",".join(str(i) for i in self.ids)

    def describe(self) -> str:
        titles = {"task": "задача", "project": "проект", "chat": "чат",
                  "equipment": "оборудование", "none": "не найден"}
        title = titles.get(self.kind, self.kind)
        return title + (" №" + ", №".join(str(i) for i in self.ids) if self.ids else "")


NOBODY = Owner("none")


def normalize(path) -> str:
    raw = str(path or "").replace("\\", "/").split("?", 1)[0].strip()
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if parts and parts[0].lower() == "file_storage":
        parts = parts[1:]
    if not parts or any(p == ".." for p in parts):
        raise ValueError("Некорректный путь к файлу")
    return "/".join(parts)


def source_of(path: str) -> str:
    lowered = path.lower()
    for suffix in DERIVED_SUFFIXES:
        if lowered.endswith(suffix) and len(path) > len(suffix):
            return path[:-len(suffix)]
    return path


def variants(path: str) -> list:
    base = path[:-3] if path.lower().endswith(".gz") else path
    out = []
    for item in (base, base + ".gz"):
        for form in (item, item.replace("/", "\\")):
            if form not in out:
                out.append(form)
    return out


class _Lru:
    def __init__(self, size: int):
        self.size = size
        self.data = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            item = self.data.get(key)
            if item is None:
                return None
            value, expires = item
            if expires is not None and expires < time.monotonic():
                self.data.pop(key, None)
                return None
            self.data.move_to_end(key)
            return value

    def put(self, key, value, ttl: Optional[float]):
        with self.lock:
            expires = None if ttl is None else time.monotonic() + ttl
            self.data[key] = (value, expires)
            self.data.move_to_end(key)
            while len(self.data) > self.size:
                self.data.popitem(last=False)

    def clear(self):
        with self.lock:
            self.data.clear()

    def __len__(self):
        return len(self.data)


_owners = _Lru(OWNER_CACHE_SIZE)
_decisions = _Lru(DECISION_CACHE_SIZE)
_stats = {"checks": 0, "allowed": 0, "denied": 0, "would_deny": 0,
          "owner_cache_hits": 0, "decision_cache_hits": 0}
_recent = deque(maxlen=50)
_stats_lock = threading.Lock()


def _count(name: str) -> None:
    with _stats_lock:
        _stats[name] = _stats.get(name, 0) + 1


def _chat_owner(chat_db: Session, source: str) -> Owner:
    ids = set()
    folder = CHAT_FOLDER.match(source)
    if folder:
        ids.add(int(folder.group(1)))
    forms = variants(source)
    rows = chat_db.query(MessageModel.chat_id).filter(MessageModel.file_path.in_(forms)).distinct().all()
    ids.update(r[0] for r in rows if r and r[0])
    if source.lower().startswith("messenger/avatars/"):
        avatar_forms = forms + ["/file_storage/" + f for f in forms if "\\" not in f]
        rows = chat_db.query(ChatModel.id).filter(ChatModel.image_path.in_(avatar_forms)).all()
        ids.update(r[0] for r in rows if r and r[0])
    return Owner("chat", tuple(sorted(ids))) if ids else NOBODY


def _project_owner(db: Session, source: str) -> Owner:
    folder = PROJECT_FOLDER.match(source)
    if folder:
        project_id = int(folder.group(1))
        if db.query(Project.id).filter(Project.id == project_id).first():
            return Owner("project", (project_id,))
    row = db.query(ProjectFile.project_id).filter(ProjectFile.filepath.in_(variants(source))).first()
    return Owner("project", (row[0],)) if row else NOBODY


def _equipment_owner(db: Session, source: str) -> Owner:
    row = db.query(EquipmentItem.id).filter(EquipmentItem.photo_path.in_(variants(source))).first()
    return Owner("equipment", (row[0],)) if row else NOBODY


def _task_owner(db: Session, source: str) -> Owner:
    forms = variants(source)
    ids = {r[0] for r in db.query(TaskFile.task_id).filter(TaskFile.filepath.in_(forms)).all() if r[0]}
    rows = db.query(TaskStageDetails.task_id).join(
        TaskStageFile, TaskStageFile.stage_detail_id == TaskStageDetails.id).filter(
        TaskStageFile.filepath.in_(forms)).all()
    ids.update(r[0] for r in rows if r[0])
    return Owner("task", tuple(sorted(ids))) if ids else NOBODY


def resolve_owner(db: Session, chat_db: Optional[Session], path: str) -> Owner:
    source = source_of(normalize(path))
    head = source.split("/", 1)[0].lower()
    if head == "messenger":
        return _chat_owner(chat_db, source) if chat_db is not None else NOBODY
    if head == "projects":
        return _project_owner(db, source)
    if head == "equipment":
        return _equipment_owner(db, source)
    return _task_owner(db, source)


def owner_of(db: Session, chat_db: Optional[Session], source: str) -> Owner:
    cached = _owners.get(source)
    if cached is not None:
        _count("owner_cache_hits")
        return cached
    owner = resolve_owner(db, chat_db, source)
    if owner.kind == "chat":
        _owners.put(source, owner, CHAT_OWNER_TTL)
    elif owner.kind != "none":
        _owners.put(source, owner, None)
    return owner


# ===== решение =====

def _employee(db: Session, user_id: int):
    return db.query(Employee).filter(Employee.id == user_id).first()


def _allowed(db: Session, chat_db: Optional[Session], user_id: int, owner: Owner) -> bool:
    if owner.kind == "equipment":
        return True
    if owner.kind == "chat":
        if chat_db is None or not owner.ids:
            return False
        return chat_db.query(ChatParticipantModel.id).filter(
            ChatParticipantModel.chat_id.in_(list(owner.ids)),
            ChatParticipantModel.user_id == user_id).first() is not None
    if owner.kind == "project":
        employee = _employee(db, user_id)
        for project_id in owner.ids:
            project = db.query(Project).filter(Project.id == project_id).first()
            if project is not None and can_view_project(db, employee, project):
                return True
        return False
    if owner.kind == "task":
        if is_management_status(_employee(db, user_id)):
            return True
        for task in db.query(Task).filter(Task.id.in_(list(owner.ids))).all():
            if user_can_read_task_files(db, user_id, task):
                return True
        return False
    return False


def decide(db: Session, chat_db: Optional[Session], user_id: int, owner: Owner):
    key = (user_id, owner.key)
    cached = _decisions.get(key)
    if cached is not None:
        _count("decision_cache_hits")
        return cached, False
    allowed = _allowed(db, chat_db, user_id, owner)
    if DECISION_TTL:
        _decisions.put(key, allowed, DECISION_TTL)
    return allowed, True


def _refuse(user_id: int, what: str, owner: Owner, route: str, fresh: bool) -> None:
    if STRICT:
        _count("denied")
        if fresh:
            logger.info("Доступ к файлу: отказ — сотрудник %s, %s, владелец: %s, маршрут %s",
                        user_id, what, owner.describe(), route)
        raise HTTPException(status_code=403, detail="Нет доступа к этому файлу")
    _count("would_deny")
    if fresh:
        logger.warning("Доступ к файлу: отказ бы был — сотрудник %s, %s, владелец: %s, маршрут %s",
                       user_id, what, owner.describe(), route)
        _recent.append({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "user_id": user_id,
                        "file": what, "owner": owner.describe(), "route": route})


def check_path(db: Session, chat_db: Optional[Session], user_id: int, path: str, route: str = "") -> str:
    try:
        source = source_of(normalize(path))
    except ValueError:
        raise HTTPException(status_code=400, detail="Некорректный путь к файлу")
    _count("checks")
    owner = owner_of(db, chat_db, source)
    allowed, fresh = decide(db, chat_db, user_id, owner)
    if allowed:
        _count("allowed")
        return source
    _refuse(user_id, "файл " + source, owner, route, fresh)
    return source


def check_chat_member(chat_db: Session, user_id: int, chat_id: int, route: str = "") -> None:
    _count("checks")
    owner = Owner("chat", (int(chat_id),)) if chat_id else NOBODY
    allowed, fresh = decide(None, chat_db, user_id, owner)
    if allowed:
        _count("allowed")
        return
    _refuse(user_id, "вложение чата", owner, route, fresh)


def forget() -> None:
    _owners.clear()
    _decisions.clear()


def snapshot() -> dict:
    with _stats_lock:
        stats = dict(_stats)
    return {
        "strict": STRICT,
        "mode": "strict" if STRICT else "soft",
        "decision_ttl_seconds": DECISION_TTL,
        "owner_cache": len(_owners),
        "decision_cache": len(_decisions),
        "stats": stats,
        "recent_would_deny": list(_recent),
    }
