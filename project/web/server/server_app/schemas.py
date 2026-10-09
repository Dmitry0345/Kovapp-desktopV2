# -*- coding: utf-8 -*-

from datetime import timedelta, datetime, date
from typing import List, Optional, Dict, Literal, Any
import warnings
# pydantic 2 сам переводит orm_mode в from_attributes; ключ оставлен ради pydantic 1
warnings.filterwarnings("ignore", message="Valid config keys have changed in V2")
from pydantic import BaseModel, field_validator, model_validator, Field


class EmployeeShort(BaseModel):
    id: int
    last_name: str
    first_name: str
    department: str

    class Config:
        orm_mode = True


class UploaderShort(BaseModel):
    """Кто прикрепил файл. Все поля необязательны: сотрудник мог быть удалён."""
    id: Optional[int] = None
    last_name: Optional[str] = None
    first_name: Optional[str] = None
    middle_name: Optional[str] = None
    department: Optional[str] = None

    class Config:
        orm_mode = True


class ProjectShort(BaseModel):
    id: int
    name: str
    deadline: Optional[date] = None
    organization: Optional[str] = None

    class Config:
        orm_mode = True


class SpanAuthorOut(BaseModel):
    """Автор фрагмента текста. Отдельно от EmployeeShort ради отчества:
    подсказка над текстом показывает полное ФИО.

    id пуст, если сотрудник удалён: сам фрагмент сохраняется, а автор
    показывается как «Неизвестный»."""
    id: Optional[int] = None
    last_name: Optional[str] = None
    first_name: Optional[str] = None
    middle_name: Optional[str] = None
    position: Optional[str] = None
    department: Optional[str] = None

    class Config:
        orm_mode = True


class SpanOut(BaseModel):
    id: int
    start: int
    end: int
    # пусто у фрагментов, автор которых удалён
    author_id: Optional[int] = None
    author: Optional[SpanAuthorOut] = None
    created_at: Optional[datetime] = None

    class Config:
        orm_mode = True


class SpanCreate(BaseModel):
    operation: Literal["insert"] = "insert"
    position: int
    text: str


class SpanDelete(BaseModel):
    operation: Literal["delete"] = "delete"
    start: int
    end: int


class TaskStageTextSpanIn(BaseModel):
    start: int
    end: int
    # пусто у фрагментов, автор которых удалён
    author_id: Optional[int] = None


class TaskStageDetailsUpdate(BaseModel):
    text_description: str
    spans: List[TaskStageTextSpanIn]

    class Config:
        orm_mode = True


class TaskStageDetailsCreate(BaseModel):
    department_name: str
    text_description: str
    # Кого назначили ответственным за этап. Пусто — возьмём всех
    # руководителей отдела (см. server_app/stage_leads.py)
    responsible_ids: Optional[List[int]] = None

    class Config:
        orm_mode = True


class NotificationCreate(BaseModel):
    employee_id: Optional[int] = None
    user_ids: Optional[List[int]] = None
    message: str
    target_section: Optional[str] = None
    target_id: Optional[int] = None

    @model_validator(mode='after')
    def check_recipients(self):
        # Проверка по всей модели: пофайловый валидатор смотрел info.data, а
        # employee_id объявлен первым — user_ids там ещё нет, и запрос с явным
        # employee_id: null отклонялся с 422, хотя получатели были указаны.
        if not self.employee_id and not (self.user_ids or []):
            raise ValueError("Необходимо указать либо employee_id, либо user_ids")
        return self


class TaskStageFileCreate(BaseModel):
    filename: str
    filepath: bytes


class FileInfo(BaseModel):
    id: int
    filename: str
    filepath: str
    size: int
    extension: str
    task_id: Optional[int] = None
    stage_detail_id: Optional[int] = None
    created_at: Optional[str] = None
    content_type: Optional[str] = None
    uploaded_by_id: Optional[int] = None
    uploaded_by: Optional[UploaderShort] = None

    class Config:
        orm_mode = True
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }


class MessageUpdate(BaseModel):
    text: Optional[str] = None
    special: Optional[str] = None
    metadata: Optional[Dict] = None


class GroupedFileCreate(BaseModel):
    filename: str
    filepath: str
    size: int
    is_image: Optional[bool] = False


class GroupedMessageCreate(BaseModel):
    chat_id: int
    text: Optional[str] = None
    files: List[GroupedFileCreate] = []


class GroupedMessageOut(BaseModel):
    id: int
    chat_id: int
    sender_id: int
    sender_name: str
    text: Optional[str] = None
    timestamp: str
    is_read: bool
    is_edited: bool
    special: Optional[str] = None
    group_id: Optional[str] = None
    parent_id: Optional[int] = None
    metadata: Optional[Dict] = None
    files: List[FileInfo] = []
    reactions: Dict[str, str] = {}

    class Config:
        orm_mode = True


class StageUpdateInTask(BaseModel):
    stage_number: int
    department_name: Optional[str] = None
    text_description: Optional[str] = None
    is_completed: Optional[bool] = None

    class Config:
        orm_mode = True


class TaskFileCreate(BaseModel):
    filename: str
    filepath: bytes


class OrganizationPhoneBase(BaseModel):
    phone_number: str
    description: str
    organization_id: Optional[int] = None


class OrganizationPhoneOut(OrganizationPhoneBase):
    id: int

    class Config:
        orm_mode = True


class OrganizationEmailBase(BaseModel):
    email: str
    description: str
    organization_id: Optional[int] = None


class OrganizationEmailOut(OrganizationEmailBase):
    id: int

    class Config:
        orm_mode = True


class OrganizationBase(BaseModel):
    name: str
    description: Optional[str] = None


class OrganizationCreate(OrganizationBase):
    phones: List[OrganizationPhoneBase] = []
    emails: List[OrganizationEmailBase] = []


class OrganizationUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    phones: Optional[List[OrganizationPhoneBase]] = None
    emails: Optional[List[OrganizationEmailBase]] = None


class Organ(OrganizationBase):
    id: int
    who_create: Optional[str] = None
    when_create: Optional[datetime] = None
    who_edit: Optional[str] = None
    when_edit: Optional[datetime] = None
    phones: List[OrganizationPhoneOut] = []
    emails: List[OrganizationEmailOut] = []

    class Config:
        orm_mode = True


class TaskFileOut(BaseModel):
    id: int
    filename: str
    filepath: str
    extension: str
    task_id: Optional[int] = None
    stage_detail_id: Optional[int] = None
    size: int

    created_at: Optional[datetime] = None
    uploaded_by_id: Optional[int] = None
    uploaded_by: Optional[UploaderShort] = None

    class Config:
        orm_mode = True


class FileOut(BaseModel):
    id: int
    filename: str
    filepath: str
    extension: str
    task_id: Optional[int] = None
    stage_detail_id: Optional[int] = None
    size: int
    # Дата прикрепления: по ней клиент сортирует списки файлов
    created_at: Optional[datetime] = None
    uploaded_by_id: Optional[int] = None
    uploaded_by: Optional[UploaderShort] = None

    thumb_hash: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[float] = None

    class Config:
        orm_mode = True


class TaskStageFileOut(BaseModel):
    id: int
    filename: str
    filepath: str
    extension: str
    task_id: Optional[int] = None
    stage_detail_id: Optional[int] = None
    size: int

    created_at: Optional[datetime] = None
    uploaded_by_id: Optional[int] = None
    uploaded_by: Optional[UploaderShort] = None

    class Config:
        orm_mode = True


class TaskUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    deadline: Optional[date] = None
    state: Optional[str] = None
    activity: Optional[str] = None
    responsible_persons_ids: Optional[List[int]] = None
    assigned_employees_ids: Optional[List[int]] = None
    stages: Optional[List[StageUpdateInTask]] = None
    customer_organization: Optional[str] = None
    # None означает «снять проект», отсутствие поля — «не трогать» (см. model_fields_set)
    project_id: Optional[int] = None

    class Config:
        orm_mode = True


class TaskCreate(BaseModel):
    name: str
    description: Optional[str] = None
    deadline: Optional[date] = None
    state: Optional[str] = None
    activity: Optional[str] = None
    creator_id: Optional[int] = None
    responsible_persons_ids: List[int] = []
    assigned_employees_ids: List[int] = []
    stages: List[TaskStageDetailsCreate] = []
    customer_organization: Optional[str] = None
    project_id: Optional[int] = None

    class Config:
        orm_mode = True


class EmployeeCreate(BaseModel):
    username: str
    password: str
    last_name: str
    first_name: str
    middle_name: Optional[str] = None
    position: Optional[str] = None
    status: Optional[str] = None      # работник | руководитель отдела | директор | администратор
    phone_number: Optional[str] = None
    email: Optional[str] = None
    profile: Optional[str] = None
    department: Optional[str] = None


class EmployeeUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    middle_name: Optional[str] = None
    position: Optional[str] = None
    status: Optional[str] = None      # работник | руководитель отдела | директор | администратор
    phone_number: Optional[str] = None
    email: Optional[str] = None
    profile: Optional[str] = None
    department: Optional[str] = None


class EmployeeOut(BaseModel):
    id: int
    name: Optional[str] = None
    last_name: Optional[str] = None
    first_name: Optional[str] = None
    middle_name: Optional[str] = None
    position: Optional[str] = None
    status: Optional[str] = None      # работник | руководитель отдела | директор | администратор
    phone_number: Optional[str] = None
    email: Optional[str] = None
    profile: Optional[str] = None
    department: Optional[str] = None

    class Config:
        orm_mode = True
        exclude = {'photo'}


class Token(BaseModel):
    access_token: str
    token_type: str
    user_id: int


class UserLogin(BaseModel):
    username: str
    password: str


class TemplateCreate(BaseModel):
    template_name: str
    num_stages: int
    departments: str


class TemplateOut(BaseModel):
    id: int
    template_name: str
    num_stages: int
    departments: str

    class Config:
        orm_mode = True


class NotificationOut(BaseModel):
    id: int
    message: str
    is_read: bool
    created_at: Optional[datetime] = None
    target_section: Optional[str] = None
    target_id: Optional[int] = None

    class Config:
        orm_mode = True


class TaskOut(BaseModel):
    id: int
    name: Optional[str] = None
    description: Optional[str] = None
    deadline: Optional[date] = None
    state: Optional[str] = None
    activity: Optional[str] = None
    creator_id: Optional[int] = None
    customer_organization: Optional[str] = None
    project_id: Optional[int] = None

    class Config:
        orm_mode = True


class TaskStageDetailsOut(BaseModel):
    id: int
    stage_number: int
    text_description: Optional[str] = None
    is_completed: bool
    completion_timestamp: Optional[datetime] = None
    completed_by_employee_id: Optional[int] = None
    department_name: Optional[str] = None
    participants: List[EmployeeShort] = []
    files: List[TaskStageFileOut] = []

    class Config:
        orm_mode = True


class ReactionUpdate(BaseModel):
    message_id: int
    reaction_type: str  # 'like', 'dislike', 'sad', 'trophy', 'confetti'


class RoleUpdate(BaseModel):
    user_id: int
    # Клиенты слали только user_id и получали 422: роль подставляем сами,
    # а сам маршрут всё равно решает, повышение это или понижение.
    role: Optional[str] = None


class TaskStageDetailsFullOut(TaskStageDetailsOut):
    completion_timestamp: Optional[datetime] = None
    completed_by_employee_id: Optional[int] = None
    spans: List[SpanOut] = []
    # Ответственные за этап и признак «я им распоряжаюсь»: по ним клиент
    # решает, показывать ли правку состава и продление задачи
    leads: List[int] = []
    i_lead: bool = False

    class Config:
        orm_mode = True


class TaskWithRelationsOut(TaskOut):
    responsible_persons: List[EmployeeShort] = []
    assigned_employees: List[EmployeeShort] = []
    creator: Optional[EmployeeShort] = None
    stage_details: List[TaskStageDetailsOut] = []
    task_files: List[TaskFileOut] = []
    archived_at: Optional[datetime] = None
    who_archived_employee: Optional[EmployeeShort] = None
    project: Optional[ProjectShort] = None

    class Config:
        orm_mode = True
    project_archived: bool = False   # проект задачи завершён

class TaskStageLightOut(TaskStageDetailsOut):
    files_count: int = 0


class TaskListLightOut(TaskWithRelationsOut):
    """Строка списка задач без списков файлов — только их число."""
    stage_details: List[TaskStageLightOut] = []
    task_files_count: int = 0


class TaskStageDetailsCreateOut(TaskStageDetailsOut):
    class Config:
        orm_mode = True


class PasswordResetRequest(BaseModel):
    username: str


class PasswordResetConfirm(BaseModel):
    username: str
    reset_code: str
    new_password: str


class PasswordResetVerify(BaseModel):
    username: str
    reset_code: str


class PasswordResetResponse(BaseModel):
    success: bool
    message: str
    # Сколько попыток ввода кода осталось. Показывается пользователю;
    # злоумышленнику это ничего не даёт — он и так считает свои попытки.
    attempts_left: Optional[int] = None


class TaskStageDetailsOut(BaseModel):
    id: int
    stage_number: int
    text_description: Optional[str] = None
    is_completed: bool
    completion_timestamp: Optional[datetime] = None
    completed_by_employee_id: Optional[int] = None
    department_name: Optional[str] = None
    participants: List[EmployeeShort] = []
    files: List[TaskStageFileOut] = []

    class Config:
        orm_mode = True


class TaskFilterCreate(BaseModel):
    is_archived: bool
    sort_by: Optional[str] = None
    stage_count: Optional[int] = None
    min_created_date: Optional[datetime] = None
    max_created_date: Optional[datetime] = None


class TaskFilterUpdate(BaseModel):
    sort_by: Optional[str] = None
    stage_count: Optional[int] = None
    min_created_date: Optional[datetime] = None
    max_created_date: Optional[datetime] = None


class TaskFilterOut(BaseModel):
    id: int
    user_id: int
    is_archived: bool
    sort_by: Optional[str] = Field(
        None,
        description="Параметры сортировки через запятую (например: 'deadline_asc,id_desc')"
    )
    sort_options: List[str] = Field(
        default_factory=list,
        description="Доступные варианты сортировки для интерфейса"
    )
    stage_count: Optional[int] = Field(
        None,
        description="Фильтр по количеству этапов",
        ge=1, le=10
    )
    min_created_date: Optional[datetime] = Field(
        None,
        description="Минимальная дата создания задачи"
    )
    max_created_date: Optional[datetime] = Field(
        None,
        description="Максимальная дата создания задачи"
    )
    created_at: datetime = Field(
        ...,
        description="Дата создания фильтра"
    )
    updated_at: Optional[datetime] = Field(
        None,
        description="Дата последнего обновления фильтра"
    )

    class Config:
        orm_mode = True
        json_schema_extra = {
            "example": {
                "id": 1,
                "user_id": 123,
                "is_archived": False,
                "sort_by": "deadline_asc,id_desc",
                "sort_options": [
                    "id_asc", "id_desc",
                    "deadline_asc", "deadline_desc",
                    "created_asc", "created_desc"
                ],
                "stage_count": 3,
                "min_created_date": "2023-01-01T00:00:00",
                "max_created_date": "2023-12-31T23:59:59",
                "created_at": "2023-01-15T10:30:00",
                "updated_at": "2023-01-20T14:45:00"
            }
        }


class GroupUpdate(BaseModel):
    name: Optional[str] = None
    image_path: Optional[str] = None


class AddParticipants(BaseModel):
    user_ids: List[int]


class ForwardMessageRequest(BaseModel):
    """Тело запроса на пересылку: список чатов, куда копируем сообщение."""
    chat_ids: List[int] = Field(default_factory=list)
    file_ids: Optional[List[int]] = None   # часть группы: только эти файлы


# ── Проекты ──────────────────────────────────────────────────────────────────

# extra — «Дополнительные файлы»: постоянная папка, есть у любого проекта
PROJECT_SECTIONS = ("contracts", "project", "delivery", "extra")


class ProjectCreate(BaseModel):
    name: str
    organization: Optional[str] = None
    responsible_id: Optional[int] = None
    deadline: Optional[date] = None
    description: Optional[str] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    organization: Optional[str] = None
    responsible_id: Optional[int] = None
    deadline: Optional[date] = None
    description: Optional[str] = None


class ProjectOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    organization: Optional[str] = None
    responsible_id: Optional[int] = None
    creator_id: Optional[int] = None
    deadline: Optional[date] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    responsible: Optional[UploaderShort] = None
    creator: Optional[UploaderShort] = None
    tasks_total: int = 0
    tasks_active: int = 0
    files_total: int = 0
    # Что разрешено текущему пользователю — клиент по этому включает кнопки
    can_edit: bool = False
    can_complete: bool = False   # завершить / вернуть в работу
    is_archived: bool = False
    archived_at: Optional[datetime] = None
    archived_by: Optional[UploaderShort] = None   # кто завершил
    tasks_unfinished: int = 0
    can_delete: bool = False
    can_upload: bool = False

    class Config:
        orm_mode = True


class ProjectFileOut(BaseModel):
    id: int
    project_id: int
    section: str
    filename: str
    filepath: str
    size: Optional[int] = None
    extension: Optional[str] = None
    created_at: Optional[datetime] = None
    uploaded_by_id: Optional[int] = None
    uploaded_by: Optional[UploaderShort] = None

    thumb_hash: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[float] = None

    class Config:
        orm_mode = True


class ProjectTaskOut(BaseModel):
    """Компактная строка задачи для списка внутри проекта."""
    id: int
    name: str
    deadline: Optional[date] = None
    activity: Optional[str] = None
    state: Optional[str] = None
    creation_date: Optional[datetime] = None
    customer_organization: Optional[str] = None
    total_stages: int = 0
    completed_stages: int = 0
    departments: List[str] = []
    can_open: bool = True   # задачу можно открыть (видна всем в проекте)


class ProjectPhotoFile(BaseModel):
    id: int
    filename: str
    size: Optional[int] = None
    extension: Optional[str] = None
    created_at: Optional[datetime] = None
    is_stage_file: bool = False
    stage_number: Optional[int] = None
    uploaded_by: Optional[UploaderShort] = None


class ProjectPhotoFolder(BaseModel):
    task_id: int
    task_name: str
    deadline: Optional[date] = None
    activity: Optional[str] = None
    files: List[ProjectPhotoFile] = []


# ── Push и настройки ─────────────────────────────────────────────────────────

class PushKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscribeIn(BaseModel):
    endpoint: str
    keys: PushKeys
    user_agent: Optional[str] = None


class PushUnsubscribeIn(BaseModel):
    endpoint: str


class PreferenceIn(BaseModel):
    value: Optional[Any] = None


class PreferenceOut(BaseModel):
    key: str
    value: Optional[Any] = None
    updated_at: Optional[datetime] = None
