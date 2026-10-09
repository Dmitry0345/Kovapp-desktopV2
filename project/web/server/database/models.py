import enum
from sqlalchemy import Column, Integer, String, Date, ForeignKey, Enum, Boolean, LargeBinary, DateTime, Table, Text, JSON, Float
from sqlalchemy.orm import declarative_base, relationship, column_property, deferred
from datetime import datetime
from sqlalchemy import UniqueConstraint, select, func

BaseChat = declarative_base()

Base = declarative_base()

task_responsible_associations = Table(
    'task_responsible_associations',
    Base.metadata,
    Column('task_id', Integer, ForeignKey('tasks.id'), primary_key=True),
    Column('employee_id', Integer, ForeignKey('employees.id'), primary_key=True)
)

task_stage_participants = Table(
    'task_stage_participants',
    Base.metadata,
    Column('stage_detail_id', Integer, ForeignKey('task_stage_details.id'), primary_key=True),
    Column('employee_id', Integer, ForeignKey('employees.id'), primary_key=True)
)


class TaskState(enum.Enum):
    FIRST = 1
    SECOND = 2
    THIRD = 3
    FOURTH = 4
    FIFTH = 5
    SIXTH = 6
    SEVENTH = 7
    EIGHTH = 8
    NINTH = 9
    TENTH = 10


class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, ForeignKey('employees.id'), primary_key=True)
    username = Column(String(50), unique=True, nullable=False)
    password = Column(String(255), nullable=True)
    employee = relationship("Employee", back_populates="user_account", uselist=False)
    reset_code = Column(String(4), nullable=True)
    token = Column(String(20), nullable=True)

class TaskAssignment(Base):
    __tablename__ = 'task_assignments'
    task_id = Column(Integer, ForeignKey('tasks.id'), primary_key=True)
    employee_id = Column(Integer, ForeignKey('employees.id'), primary_key=True)


class Employee(Base):
    __tablename__ = 'employees'
    id = Column(Integer, primary_key=True)
    name = Column(String(255))
    last_name = Column(String(255))
    first_name = Column(String(255))
    middle_name = Column(String(255))
    position = Column(String(255))
    # Права определяются этим полем, а не словом в названии должности:
    # «работник», «руководитель отдела», «директор», «администратор».
    # Пусто у старых записей — тогда действует прежнее правило по должности.
    status = Column(String(32), nullable=True)
    phone_number = Column(String(20))
    email = Column(String(255))
    profile = Column(String(1000))
    department = Column(String(255))
    # Фото до 10 МБ грузится только по явному запросу. Раньше колонка ехала
    # в каждом запросе, где есть сотрудник: задача с десятком участников и
    # объединёнными загрузками давала выборку в сотни мегабайт, и сервер
    # падал с MemoryError прямо при чтении ответа базы.
    photo = deferred(Column(LargeBinary(length=10485760), nullable=True))
    user_account = relationship("User", back_populates="employee", uselist=False)
    tasks_responsible = relationship("Task", secondary=task_responsible_associations,
                                     back_populates="responsible_persons")
    tasks_assigned = relationship("Task", secondary=TaskAssignment.__table__, back_populates="assigned_employees")
    archived_tasks = relationship("Task", back_populates="who_archived_employee", foreign_keys='[Task.who_archive]')
    user_notifications = relationship("Notification", back_populates="employee")
    tasks_stage_participated = relationship("TaskStageDetails", secondary=task_stage_participants,
                                            back_populates="participants")
    stages_completed = relationship("TaskStageDetails", back_populates="completed_by_employee",
                                    foreign_keys='[TaskStageDetails.completed_by_employee_id]')
    created_tasks = relationship("Task", back_populates="creator", foreign_keys='[Task.creator_id]')





class TaskStageDetails(Base):
    __tablename__ = 'task_stage_details'
    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey('tasks.id'))
    stage_number = Column(Integer, nullable=False)
    text_description = Column(Text)
    is_completed = Column(Boolean, default=False)
    completed_by_employee_id = Column(Integer, ForeignKey('employees.id'), nullable=True)
    department_name = Column(String(255), nullable=True)
    completion_timestamp = Column(DateTime, nullable=True)

    task = relationship("Task", back_populates="stage_details")
    files = relationship("TaskStageFile", back_populates="stage_detail", cascade="all, delete-orphan")
    participants = relationship("Employee", secondary=task_stage_participants,
                                back_populates="tasks_stage_participated")
    completed_by_employee = relationship("Employee", back_populates="stages_completed",
                                         foreign_keys=[completed_by_employee_id])

    __table_args__ = (UniqueConstraint('task_id', 'stage_number', name='_task_stage_uc'),)


class TaskCompletionRequest(Base):
    """Заявка на завершение задачи, ждущая подтверждения директора.

    Раньше руководитель отдела отправлял задачу в архив сам, и никто этого не
    видел. Теперь завершение — это заявка: она уходит всем директорам, и
    завершившим в базе записывается тот из них, кто подтвердил.
    """
    __tablename__ = 'task_completion_requests'
    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey('tasks.id', ondelete='CASCADE'), nullable=False)
    requested_by = Column(Integer, ForeignKey('employees.id', ondelete='SET NULL'), nullable=True)
    requested_at = Column(DateTime, default=datetime.now)
    status = Column(String(16), nullable=False, default='pending')   # pending | approved | declined
    decided_by = Column(Integer, ForeignKey('employees.id', ondelete='SET NULL'), nullable=True)
    decided_at = Column(DateTime, nullable=True)
    comment = Column(String(500), nullable=True)


class TaskStageLead(Base):
    """Ответственный за этап — выбирается при продлении задачи.

    Раньше этап вёл целый отдел, и «главного» в нём не было: продлевать и
    завершать мог любой руководитель, а состав набирался всем отделом. Теперь
    при продлении можно назвать одного или нескольких — они и отвечают за этап.
    Отдельная таблица, а не поле: ответственных бывает несколько.
    """
    __tablename__ = 'task_stage_leads'
    stage_detail_id = Column(Integer, ForeignKey('task_stage_details.id', ondelete='CASCADE'),
                             primary_key=True)
    employee_id = Column(Integer, ForeignKey('employees.id', ondelete='CASCADE'), primary_key=True)


class TaskStageFile(Base):
    __tablename__ = 'task_stage_files'
    id = Column(Integer, primary_key=True)
    stage_detail_id = Column(Integer, ForeignKey('task_stage_details.id'))
    filename = Column(String(255), nullable=False)
    filepath = Column(String(512), nullable=False)  # Теперь храним путь к файлу
    size = Column(Integer)
    extension = Column(String(10))  # Добавляем поле для расширения файла
    # Кто прикрепил файл и когда: нужны для подписи плитки и фильтра по отправителю
    uploaded_by_id = Column(Integer, ForeignKey('employees.id'), nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=True)
    stage_detail = relationship("TaskStageDetails", back_populates="files")
    uploaded_by = relationship("Employee", foreign_keys=[uploaded_by_id])

class TaskFile(Base):
    __tablename__ = 'task_files'
    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey('tasks.id'))
    filename = Column(String(255), nullable=False)
    filepath = Column(String(512), nullable=False)  # Теперь храним путь к файлу
    size = Column(Integer)
    extension = Column(String(10))  # Добавляем поле для расширения файла
    uploaded_by_id = Column(Integer, ForeignKey('employees.id'), nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=True)
    task = relationship("Task", back_populates="task_files")
    uploaded_by = relationship("Employee", foreign_keys=[uploaded_by_id])

class Task(Base):
    __tablename__ = 'tasks'
    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    deadline = Column(Date)
    state = Column(Enum(TaskState), default=TaskState.FIRST, nullable=False)
    activity = Column(String(1000))
    who_archive = Column(Integer, ForeignKey('employees.id'), nullable=True)
    creation_date = Column(DateTime, default=datetime.now)
    creator_id = Column(Integer, ForeignKey('employees.id'), nullable=True)
    archived_at = Column(DateTime, nullable=True)
    customer_organization = Column(String(100), nullable=True)
    # Проект необязателен: задача может жить и без него
    project_id = Column(Integer, ForeignKey('projects.id', ondelete='SET NULL'), nullable=True)

    total_stages = column_property(
        select(func.count(TaskStageDetails.id))
        .where(TaskStageDetails.task_id == id)
        .correlate_except(TaskStageDetails)
        .scalar_subquery())

    responsible_persons = relationship("Employee", secondary=task_responsible_associations,
                                       back_populates="tasks_responsible")
    assigned_employees = relationship("Employee", secondary=TaskAssignment.__table__, back_populates="tasks_assigned")
    stage_details = relationship("TaskStageDetails", back_populates="task", cascade="all, delete-orphan")
    task_files = relationship("TaskFile", back_populates="task", cascade="all, delete-orphan")
    who_archived_employee = relationship("Employee", back_populates="archived_tasks", foreign_keys=[who_archive])
    creator = relationship("Employee", back_populates="created_tasks", foreign_keys=[creator_id])
    project = relationship("Project", back_populates="tasks", foreign_keys=[project_id])

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "state": self.state.name if self.state else None,
            "activity": self.activity,
            "creator_id": self.creator_id,
            "responsible_persons": [{
                "id": emp.id,
                "name": f"{emp.last_name} {emp.first_name}"
            } for emp in self.responsible_persons],
            "assigned_employees": [{
                "id": emp.id,
                "name": f"{emp.last_name} {emp.first_name}"
            } for emp in self.assigned_employees]
        }



    @property
    def project_archived(self) -> bool:
        """Задача в завершённом проекте — вернуть её в работу нельзя."""
        return bool(self.project is not None and getattr(self.project, "is_archived", False))
class Notification(Base):
    __tablename__ = 'notifications'
    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey('employees.id'))
    message = Column(String(500), nullable=False)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.now)
    target_section = Column(String(255), nullable=True)
    target_id = Column(Integer, nullable=True)

    employee = relationship("Employee", back_populates="user_notifications")


class Template(Base):
    __tablename__ = 'templates'
    id = Column(Integer, primary_key=True)
    template_name = Column(String(255), nullable=False, unique=True)
    num_stages = Column(Integer, nullable=False)
    departments = Column(String(1000))

from sqlalchemy import DateTime

class TaskFilter(Base):
    __tablename__ = "task_filters"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    is_archived = Column(Boolean, default=False)

    sort_by = Column(String(255))
    stage_count = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    min_created_date = Column(DateTime, nullable=True)
    max_created_date = Column(DateTime, nullable=True)


class Organization(Base):
    __tablename__ = 'organizations'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    who_create = Column(String(100))
    when_create = Column(DateTime, default=datetime.now)
    who_edit = Column(String(100), nullable=True)
    when_edit = Column(DateTime, nullable=True)

    phones = relationship("OrganizationPhone", back_populates="organization", cascade="all, delete-orphan")
    emails = relationship("OrganizationEmail", back_populates="organization", cascade="all, delete-orphan")


class OrganizationPhone(Base):
    __tablename__ = 'organization_phones'
    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey('organizations.id'), nullable=False)
    phone_number = Column(String(50), nullable=False)
    description = Column(String(255), nullable=False)
    organization = relationship("Organization", back_populates="phones")


class OrganizationEmail(Base):
    __tablename__ = 'organization_emails'
    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey('organizations.id'), nullable=False)
    email = Column(String(100), nullable=False)
    description = Column(String(255), nullable=False)
    organization = relationship("Organization", back_populates="emails")


class Project(Base):
    """Проект руководителя отдела: объединяет задачи и хранит свои файлы."""
    __tablename__ = 'projects'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    # Организация — строкой, как customer_organization у задачи
    organization = Column(String(100), nullable=True)
    responsible_id = Column(Integer, ForeignKey('employees.id'), nullable=True)
    creator_id = Column(Integer, ForeignKey('employees.id'), nullable=True)
    deadline = Column(Date, nullable=True)
    is_archived = Column(Boolean, default=False, nullable=False)   # завершён — в «Архивных»
    archived_at = Column(DateTime, nullable=True)
    archived_by_id = Column(Integer, ForeignKey('employees.id'), nullable=True)   # кто завершил
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    responsible = relationship("Employee", foreign_keys=[responsible_id])
    creator = relationship("Employee", foreign_keys=[creator_id])
    archived_by = relationship("Employee", foreign_keys=[archived_by_id])
    tasks = relationship("Task", back_populates="project", foreign_keys='[Task.project_id]')
    files = relationship("ProjectFile", back_populates="project", cascade="all, delete-orphan")


class ProjectFile(Base):
    """Файл раздела проекта: contracts («Договоры»), project («Проект»), delivery («Сдача»)."""
    __tablename__ = 'project_files'
    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
    section = Column(String(20), nullable=False)
    filename = Column(String(255), nullable=False)
    filepath = Column(String(512), nullable=False)
    size = Column(Integer)
    extension = Column(String(10))
    uploaded_by_id = Column(Integer, ForeignKey('employees.id'), nullable=True)
    created_at = Column(DateTime, default=datetime.now)

    project = relationship("Project", back_populates="files")
    uploaded_by = relationship("Employee", foreign_keys=[uploaded_by_id])


class PushSubscription(Base):
    """Подписка браузера на Web Push. endpoint длинный, поэтому уникальность — по его SHA-256."""
    __tablename__ = 'push_subscriptions'
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('employees.id', ondelete='CASCADE'), nullable=False)
    endpoint = Column(Text, nullable=False)
    endpoint_hash = Column(String(64), nullable=False, unique=True)
    p256dh = Column(String(255), nullable=False)
    auth = Column(String(255), nullable=False)
    user_agent = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    last_seen_at = Column(DateTime, default=datetime.now)


class NativePushDevice(Base):
    """Телефон с приложением Kovapp для Android: куда слать уведомления без браузера.

    Секрет устройства хранится только в виде SHA-256; ключ payload_key шифрует
    текст уведомлений, идущих через Google (FCM), чтобы Google видел лишь
    зашифрованные байты."""
    __tablename__ = 'native_push_devices'
    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(String(32), nullable=False, unique=True)
    user_id = Column(Integer, ForeignKey('employees.id', ondelete='CASCADE'), nullable=False, index=True)
    provider = Column(String(16), nullable=False, default='direct')     # fcm | direct
    secret_hash = Column(String(64), nullable=False)
    payload_key = Column(String(64), nullable=False)
    fcm_token = Column(Text, nullable=True)
    model = Column(String(120), nullable=True)
    app_version = Column(String(32), nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    # последняя регистрация с действующим входом: привязка живёт не дольше срока входа
    registered_at = Column(DateTime, default=datetime.now)
    last_seen_at = Column(DateTime, default=datetime.now)


class UserPreference(Base):
    """Произвольные настройки пользователя (сортировки, фильтры разделов) в JSON."""
    __tablename__ = 'user_preferences'
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('employees.id', ondelete='CASCADE'), nullable=False)
    key = Column(String(64), nullable=False)
    value = Column(JSON, nullable=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (UniqueConstraint('user_id', 'key', name='uniq_user_pref'),)


class TaskChangeLog(Base):
    """Журнал правок задачи: кто перенёс срок, сменил ответственного, отдел этапа.

    Пишется в момент сохранения задачи, до commit: значения «было» берутся из
    объекта в сессии, поэтому в журнал попадает ровно то, что видел автор.
    """
    __tablename__ = 'task_change_log'
    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey('tasks.id', ondelete='CASCADE'), nullable=False)
    field = Column(String(48), nullable=False)
    old_value = Column(String(500), nullable=True)
    new_value = Column(String(500), nullable=True)
    user_id = Column(Integer, ForeignKey('employees.id', ondelete='SET NULL'), nullable=True)
    created_at = Column(DateTime, default=datetime.now)


# ── Склад проекта ────────────────────────────────────────────────────────
class WarehouseUnit(Base):
    """Справочник единиц измерения: шт, м, кг… Пополняется прямо из таблицы."""
    __tablename__ = 'warehouse_units'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(32), nullable=False, unique=True)      # краткое: «м»
    full_name = Column(String(64), nullable=True)               # полное: «Метр»
    created_by = Column(Integer, ForeignKey('employees.id', ondelete='SET NULL'), nullable=True)
    created_at = Column(DateTime, default=datetime.now)


class WarehouseItem(Base):
    """Строка склада проекта. Строк ровно STOCK_ROWS на проект, пустые тоже
    хранятся: у таблицы фиксированная нумерация, как в бумажной ведомости."""
    __tablename__ = 'warehouse_items'
    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
    row_no = Column(Integer, nullable=False)
    name = Column(String(255), nullable=True)
    quantity = Column(Float, nullable=True)
    unit = Column(String(32), nullable=True)
    # «Выдано» складывается из подтверждённых выдач, отдельным полем не правится
    stock_state = Column(String(16), nullable=True)      # enough | short
    stock_qty = Column(Float, nullable=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    updated_by = Column(Integer, ForeignKey('employees.id', ondelete='SET NULL'), nullable=True)

    __table_args__ = (UniqueConstraint('project_id', 'row_no', name='uniq_warehouse_row'),)


class WarehouseCellLog(Base):
    """Кто и когда менял ячейку: фамилия, имя, дата и время правки."""
    __tablename__ = 'warehouse_cell_log'
    id = Column(Integer, primary_key=True, autoincrement=True)
    item_id = Column(Integer, ForeignKey('warehouse_items.id', ondelete='CASCADE'), nullable=False)
    field = Column(String(32), nullable=False)
    old_value = Column(String(255), nullable=True)
    new_value = Column(String(255), nullable=True)
    user_id = Column(Integer, ForeignKey('employees.id', ondelete='SET NULL'), nullable=True)
    created_at = Column(DateTime, default=datetime.now)


class WarehouseIssue(Base):
    """Выдача товара. Пока получатель не подтвердил, выдача не считается
    состоявшейся и её можно отменить."""
    __tablename__ = 'warehouse_issues'
    id = Column(Integer, primary_key=True, autoincrement=True)
    item_id = Column(Integer, ForeignKey('warehouse_items.id', ondelete='CASCADE'), nullable=False)
    project_id = Column(Integer, ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
    quantity = Column(Float, nullable=False)
    issuer_id = Column(Integer, ForeignKey('employees.id', ondelete='SET NULL'), nullable=True)
    receiver_id = Column(Integer, ForeignKey('employees.id', ondelete='SET NULL'), nullable=True)
    status = Column(String(16), nullable=False, default='pending')   # pending | confirmed | cancelled
    created_at = Column(DateTime, default=datetime.now)
    confirmed_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    cancelled_by = Column(Integer, ForeignKey('employees.id', ondelete='SET NULL'), nullable=True)


class WarehouseEditor(Base):
    """Кому директор разрешил править «Товар» и «Количество».
    Список общий на все проекты — так его и просили."""
    __tablename__ = 'warehouse_editors'
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('employees.id', ondelete='CASCADE'), nullable=False, unique=True)
    added_by = Column(Integer, ForeignKey('employees.id', ondelete='SET NULL'), nullable=True)
    created_at = Column(DateTime, default=datetime.now)


# ── Оборудование ──────────────────────────────────────────────────────────
# Отдельно от склада проекта: оборудование числится за предприятием, а не за
# объектом, и выдаётся людям без привязки к задаче. Отсюда и своя таблица:
# у складской строки обязателен project_id, а здесь его нет и быть не должно.


class EquipmentItem(Base):
    """Позиция оборудования. Список свободный: строки добавляют и убирают."""
    __tablename__ = 'equipment_items'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=True)
    quantity = Column(Float, nullable=True)
    unit = Column(String(32), nullable=True)
    # Путь к фотографии внутри хранилища. Показывать оборудование картинкой
    # нагляднее, чем строкой: на складе одинаковых названий много.
    photo_path = Column(String(500), nullable=True)
    # «Выдано» отдельным полем не хранится: это сумма подтверждённых выдач,
    # иначе цифра и журнал разъезжаются. «Осталось» — количество минус выдано.
    created_by = Column(Integer, ForeignKey('employees.id', ondelete='SET NULL'), nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_by = Column(Integer, ForeignKey('employees.id', ondelete='SET NULL'), nullable=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class EquipmentIssue(Base):
    """Выдача оборудования и его возврат.

    Путь записи: pending (ждём получателя) → issued (числится за человеком)
    → return_pending (получатель вернул, ждём приёмки складом) → returned.
    Отказ на любом шаге — cancelled, и количество сразу возвращается в остаток.
    """
    __tablename__ = 'equipment_issues'
    id = Column(Integer, primary_key=True, autoincrement=True)
    item_id = Column(Integer, ForeignKey('equipment_items.id', ondelete='CASCADE'), nullable=False)
    quantity = Column(Float, nullable=False)
    issuer_id = Column(Integer, ForeignKey('employees.id', ondelete='SET NULL'), nullable=True)
    receiver_id = Column(Integer, ForeignKey('employees.id', ondelete='SET NULL'), nullable=True)
    status = Column(String(16), nullable=False, default='pending')
    created_at = Column(DateTime, default=datetime.now)
    confirmed_at = Column(DateTime, nullable=True)
    return_requested_at = Column(DateTime, nullable=True)
    returned_at = Column(DateTime, nullable=True)
    returned_to = Column(Integer, ForeignKey('employees.id', ondelete='SET NULL'), nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    cancelled_by = Column(Integer, ForeignKey('employees.id', ondelete='SET NULL'), nullable=True)


class EquipmentLog(Base):
    """Журнал строки: и выдачи с возвратами, и правки значений.

    Одна таблица на оба вида записей — в разделе они показываются вместе,
    выдачи сверху, изменения значений ниже, и сортировать удобнее по одному
    полю времени.
    """
    __tablename__ = 'equipment_log'
    id = Column(Integer, primary_key=True, autoincrement=True)
    item_id = Column(Integer, ForeignKey('equipment_items.id', ondelete='CASCADE'), nullable=False)
    kind = Column(String(16), nullable=False)        # issue | cell
    action = Column(String(32), nullable=True)       # issued | confirmed | returned…
    field = Column(String(32), nullable=True)        # для правок значений
    old_value = Column(String(255), nullable=True)
    new_value = Column(String(255), nullable=True)
    issue_id = Column(Integer, nullable=True)
    user_id = Column(Integer, ForeignKey('employees.id', ondelete='SET NULL'), nullable=True)
    target_id = Column(Integer, ForeignKey('employees.id', ondelete='SET NULL'), nullable=True)
    created_at = Column(DateTime, default=datetime.now)


class TaskStageTextSpan(Base):
    __tablename__ = "task_stage_text_spans"

    id = Column(Integer, primary_key=True)
    stage_detail_id = Column(Integer, ForeignKey("task_stage_details.id"), nullable=False)

    author_id = Column(Integer, ForeignKey("employees.id"), nullable=False)

    start = Column(Integer, nullable=False)
    end = Column(Integer, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)

    stage_detail = relationship("TaskStageDetails", backref="text_spans")
    author = relationship("Employee")


MessengerBase = declarative_base()


class Chat(MessengerBase):
    __tablename__ = 'chats'
    __table_args__ = {'mysql_engine': 'InnoDB'}

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=True)  # Название чата (NULL для личных)
    image_path = Column(String(512), nullable=True)  # Аватарка группы
    is_group = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    # Relationships
    participants = relationship("ChatParticipant", back_populates="chat", cascade="all, delete-orphan")
    messages = relationship("Message", back_populates="chat", cascade="all, delete-orphan")


class ChatParticipant(MessengerBase):
    __tablename__ = 'chat_participants'

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(Integer, ForeignKey('chats.id', ondelete='CASCADE'), nullable=False)
    user_id = Column(Integer, nullable=False)  # Здесь храним id из основной БД kovcheg
    role = Column(String(50), default='participant')  # 'admin' или 'participant'
    joined_at = Column(DateTime, default=datetime.now)

    # Relationships
    chat = relationship("Chat", back_populates="participants")

    __table_args__ = (
        UniqueConstraint('chat_id', 'user_id', name='uniq_chat_user'),
        {'mysql_engine': 'InnoDB'}
    )


class Message(MessengerBase):
    __tablename__ = 'messages'
    __table_args__ = (
        {'mysql_engine': 'InnoDB'}
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(Integer, ForeignKey('chats.id', ondelete='CASCADE'), nullable=False)
    sender_id = Column(Integer, nullable=False)  # ID отправителя из основной БД kovcheg
    text = Column(Text, nullable=True)

    # Файловые атрибуты
    file_path = Column(String(512), nullable=True)
    file_name = Column(String(255), nullable=True)
    file_size = Column(Integer, nullable=True)
    is_image = Column(Boolean, default=False)

    # Временные метки и статусы
    timestamp = Column(DateTime, default=datetime.now)
    is_read = Column(Boolean, default=False)
    is_edited = Column(Boolean, default=False)
    is_deleted = Column(Boolean, default=False)

    # Relationships
    chat = relationship("Chat", back_populates="messages")
    reactions = relationship("MessageReaction", back_populates="message", cascade="all, delete-orphan")


class MessageReaction(MessengerBase):
    __tablename__ = 'message_reactions'
    id = Column(Integer, primary_key=True)
    message_id = Column(Integer, ForeignKey('messages.id', ondelete='CASCADE'), nullable=False)
    user_id = Column(Integer, nullable=False)  # ID пользователя из основной БД kovcheg
    reaction_type = Column(String(20))
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    message = relationship("Message", back_populates="reactions")

class ChatModel(BaseChat):
    __tablename__ = "chats"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=True)
    image_path = Column(String(512), nullable=True)  # Аватарка группы
    is_group = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    participants = relationship("ChatParticipantModel", back_populates="chat", cascade="all, delete-orphan")
    messages = relationship("MessageModel", back_populates="chat", cascade="all, delete-orphan")

class ChatParticipantModel(BaseChat):
    __tablename__ = "chat_participants"
    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(Integer, ForeignKey("chats.id", ondelete="CASCADE"))
    user_id = Column(Integer)  # ID сотрудника из основной БД
    role = Column(String(50), default='participant')  # 'owner', 'admin', 'participant'
    joined_at = Column(DateTime, default=datetime.utcnow)

    chat = relationship("ChatModel", back_populates="participants")

class MessageModel(BaseChat):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(Integer, ForeignKey("chats.id", ondelete="CASCADE"))
    sender_id = Column(Integer)
    text = Column(Text, nullable=True)

    # Поля для файлов (должны быть в БД)
    file_path = Column(String(512), nullable=True)
    file_name = Column(String(255), nullable=True)
    file_size = Column(Integer, nullable=True)
    is_image = Column(Boolean, default=False)

    timestamp = Column(DateTime, default=datetime.utcnow)
    is_read = Column(Boolean, default=False)

    # Поля статусов
    is_edited = Column(Boolean, default=False)

    # НОВЫЕ ПОЛЯ ДЛЯ ГРУППИРОВКИ И СПЕЦИАЛЬНЫХ СООБЩЕНИЙ
    special = Column(String(255),
                     nullable=True)  # 'file_group', 'group_update', 'avatar_change', 'member_add', 'member_remove'
    group_id = Column(String(36), nullable=True)  # UUID для группировки сообщений
    parent_id = Column(Integer, ForeignKey("messages.id", ondelete="CASCADE"),
                       nullable=True)  # Для группировки файлов
    metadata_json = Column(JSON, nullable=True)  # Дополнительные данные в JSON

    # Связь с реакциями
    reactions_obj = relationship("MessageReactionModel",
                                 back_populates="message",
                                 cascade="all, delete-orphan")

    chat = relationship("ChatModel", back_populates="messages")

class MessageReactionModel(BaseChat):
    __tablename__ = "message_reactions"
    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(Integer, ForeignKey("messages.id", ondelete="CASCADE"))
    user_id = Column(Integer)  # ID сотрудника, который поставил реакцию
    reaction_type = Column(String(20))  # 'like', 'dislike', 'sad', 'trophy', 'confetti'
    created_at = Column(DateTime, default=datetime.utcnow)

    message = relationship("MessageModel", back_populates="reactions_obj")


# ── Сведения о содержимом файлов: копии, миниатюры с устройства ──
from sqlalchemy import BigInteger as _BigInteger


class FileBlob(Base):
    __tablename__ = 'file_blobs'
    id = Column(Integer, primary_key=True, autoincrement=True)
    stored_path = Column(String(512), nullable=False, unique=True, index=True)
    source_tree = Column(String(64), nullable=True, index=True)
    source_size = Column(_BigInteger, nullable=True)
    # образец исходника: 9 участков по 64 КБ + размер — быстрый поиск кандидатов без полного отпечатка
    source_sample = Column(String(64), nullable=True)
    size = Column(_BigInteger, nullable=True)
    kind = Column(String(16), nullable=True)
    extension = Column(String(16), nullable=True)
    display_name = Column(String(255), nullable=True)
    uploader_id = Column(Integer, nullable=True, index=True)
    verified = Column(Boolean, default=False)
    windows = Column(Text, nullable=True)
    thumb_hash = Column(String(64), nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    duration = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    night_checked_at = Column(DateTime, nullable=True)   # ночное пережатие видео: уже проверено
    night_estimate = Column(Float, nullable=True)        # проба показала выигрыш: полное сжатие обязательно, вне очереди по дате
    night_attempts = Column(Integer, nullable=True)      # сбои полного сжатия (после VIDEO_NIGHT_MAX_ATTEMPTS — отказ)
