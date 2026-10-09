# -*- coding: utf-8 -*-
"""Отвязка сотрудника от его записей перед удалением профиля.

Правило: удаляется только сам сотрудник, его учётная запись и его личные
настройки. Всё, что он создал — сообщения чата, файлы задач, разметка текста
этапов, разосланные им уведомления — остаётся в базе. Ссылки на удалённого
сотрудника обнуляются, а в интерфейсе такие записи показываются как
«Неизвестный».

Сложность в том, что часть внешних ключей объявлена NOT NULL: обнулить их
нельзя, пока колонка не станет nullable. Поэтому перед обнулением выполняется
идемпотентная миграция — она меняет тип колонки один раз и при повторных
вызовах ничего не делает.
"""

import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from datetime import datetime

from server.database.models import (
    Employee,
    EquipmentIssue,
    EquipmentLog,
    Notification,
    Project,
    ProjectFile,
    PushSubscription,
    NativePushDevice,
    Task,
    TaskAssignment,
    TaskFile,
    TaskFilter,
    TaskStageDetails,
    TaskStageFile,
    TaskStageTextSpan,
    User,
    UserPreference,
    TaskStageLead,
    WarehouseIssue,
    task_responsible_associations,
    task_stage_participants,
)


logger = logging.getLogger(__name__)


UNKNOWN_EMPLOYEE_NAME = "Неизвестный"

# Колонки, которые должны допускать NULL, чтобы запись пережила удаление автора
NULLABLE_AUTHOR_COLUMNS = (
    ("task_stage_text_spans", "author_id", "INTEGER NULL"),
)

# Таблицы, чьи ссылки на employees/users разбираются явным кодом ниже.
# Всё, что появится в схеме сверх этого списка, ловит _sweep_unhandled_references.
HANDLED_TABLES = frozenset({
    "users",
    "task_filters",
    "notifications",
    "task_assignments",
    "task_responsible_associations",
    "task_stage_participants",
    "task_stage_details",
    "task_stage_text_spans",
    "tasks",
    "employees",
    # версия 2
    "task_files",
    "task_stage_files",
    "projects",
    "project_files",
    "push_subscriptions",
    "native_push_devices",
    "user_preferences",
})


def ensure_nullable_author_columns(db: Session) -> None:
    """Делает NOT NULL-ссылки на сотрудника обнуляемыми. Идемпотентно."""
    for table, column, definition in NULLABLE_AUTHOR_COLUMNS:
        try:
            is_nullable = db.execute(text("""
                SELECT IS_NULLABLE
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = :table
                  AND COLUMN_NAME = :column
            """), {"table": table, "column": column}).scalar()

            if is_nullable is None:
                continue  # колонки нет — миграция не нужна
            if str(is_nullable).upper() == "YES":
                continue

            db.execute(text(f"ALTER TABLE `{table}` MODIFY `{column}` {definition}"))
            db.commit()
            logger.info("Колонка %s.%s переведена в NULL-допустимую", table, column)
        except Exception as e:
            db.rollback()
            logger.error("Не удалось сделать %s.%s обнуляемой: %s", table, column, e)


# Таблицы, где запись о сотруднике не обнуляется, а удаляется вместе с ним.
# Это личные права и подписки: они бессмысленны без человека, а колонка
# объявлена NOT NULL и обнулению не поддаётся.
DROP_ROW_REFERENCES = (
    ("warehouse_editors", "user_id"),      # право заполнять склад
    # Ответственность за этап — личная роль, она уходит вместе с человеком.
    # Колонка входит в первичный ключ и обнулению не поддаётся, поэтому
    # строка удаляется целиком. Этап при этом не остаётся без хозяина:
    # без названных ответственных им распоряжается руководитель ведущего
    # отдела, а в отделе без руководителей — любой его сотрудник.
    ("task_stage_leads", "employee_id"),
)


def _sweep_unhandled_references(db: Session, employee_id: int) -> list:
    """Обнуляет ссылки на сотрудника в таблицах, не разобранных явно.

    Страховка на будущее: если в схему добавят таблицу с внешним ключом на
    employees/users, удаление не упадёт с IntegrityError. Возвращает список
    таблиц, которые обнулить не удалось (колонка NOT NULL).
    """
    blocking = []
    try:
        rows = db.execute(text("""
            SELECT k.TABLE_NAME, k.COLUMN_NAME, c.IS_NULLABLE
            FROM information_schema.KEY_COLUMN_USAGE k
            JOIN information_schema.COLUMNS c
              ON c.TABLE_SCHEMA = k.TABLE_SCHEMA
             AND c.TABLE_NAME = k.TABLE_NAME
             AND c.COLUMN_NAME = k.COLUMN_NAME
            WHERE k.TABLE_SCHEMA = DATABASE()
              AND k.REFERENCED_TABLE_NAME IN ('employees', 'users')
              AND k.REFERENCED_COLUMN_NAME = 'id'
        """)).all()
    except Exception as e:
        logger.error("Не удалось прочитать список внешних ключей: %s", e)
        return blocking

    for table_name, column_name, is_nullable in rows:
        if table_name in HANDLED_TABLES:
            continue
        if (table_name, column_name) in DROP_ROW_REFERENCES:
            # Личное право уходит вместе с сотрудником: обнулять там нечего
            try:
                db.execute(
                    text(f"DELETE FROM `{table_name}` WHERE `{column_name}` = :employee_id"),
                    {"employee_id": employee_id},
                )
                logger.info("Удалены записи %s по сотруднику %s", table_name, employee_id)
            except Exception as e:
                logger.error("Не удалось убрать записи %s: %s", table_name, e)
                blocking.append(f"{table_name}.{column_name}")
            continue
        if str(is_nullable).upper() != "YES":
            blocking.append(f"{table_name}.{column_name}")
            continue
        try:
            db.execute(
                text(f"UPDATE `{table_name}` SET `{column_name}` = NULL "
                     f"WHERE `{column_name}` = :employee_id"),
                {"employee_id": employee_id},
            )
            logger.info("Обнулена ссылка %s.%s на сотрудника %s",
                        table_name, column_name, employee_id)
        except Exception as e:
            logger.error("Не удалось обнулить %s.%s: %s", table_name, column_name, e)
            blocking.append(f"{table_name}.{column_name}")

    return blocking


def _close_open_issues(db: Session, employee_id: int) -> None:
    """Закрывает незавершённые выдачи уходящего сотрудника.

    Обнулить ссылку мало. Выдача со статусом «на руках» продолжает занимать
    остаток, а вернуть её невозможно: возврат разрешён получателю, а его уже
    нет. Позиция зависала бы в недостаче навсегда. Поэтому такие выдачи
    закрываем, количество возвращается в остаток, а в журнале остаётся след,
    что произошло это при удалении сотрудника.
    """
    now = datetime.now()

    rows = db.query(EquipmentIssue).filter(
        ((EquipmentIssue.receiver_id == employee_id) | (EquipmentIssue.issuer_id == employee_id)),
        EquipmentIssue.status.in_(("pending", "issued", "return_pending"))).all()
    for row in rows:
        # Выдачу, которую человек только оформил, но не получал, закрываем
        # лишь если он же и получатель: чужая выдача переживёт своего автора
        if row.receiver_id != employee_id and row.status != "pending":
            continue
        row.status = "cancelled"
        row.cancelled_at = now
        try:
            db.add(EquipmentLog(item_id=row.item_id, kind="issue", action="cancelled",
                                issue_id=row.id, new_value=("%g" % float(row.quantity or 0)),
                                created_at=now))
        except Exception as e:
            logger.error("Журнал оборудования: запись не создана: %s", e)
    if rows:
        logger.info("Оборудование: закрыто выдач по сотруднику %s: %s", employee_id, len(rows))

    # То же самое на складе проекта: там выдача устроена так же
    wh = db.query(WarehouseIssue).filter(
        WarehouseIssue.receiver_id == employee_id,
        WarehouseIssue.status == "pending").all()
    for row in wh:
        row.status = "cancelled"
        row.cancelled_at = now
    if wh:
        logger.info("Склад: отменено ожидающих выдач по сотруднику %s: %s", employee_id, len(wh))


def _warn_orphan_stages(db: Session, employee_id: int) -> None:
    """Предупреждает об этапах, оставшихся без названного ответственного.

    Ответственность уходит вместе с человеком. Этап не осиротеет — им будет
    распоряжаться руководитель ведущего отдела, — но знать об этом полезно.
    """
    try:
        mine = {r[0] for r in db.query(TaskStageLead.stage_detail_id).filter(
            TaskStageLead.employee_id == employee_id).all()}
        if not mine:
            return
        others = {r[0] for r in db.query(TaskStageLead.stage_detail_id).filter(
            TaskStageLead.stage_detail_id.in_(mine),
            TaskStageLead.employee_id != employee_id).all()}
        alone = sorted(mine - others)
        if alone:
            logger.info("Этапы остались без названного ответственного (решает "
                        "руководитель отдела): %s", ", ".join(str(x) for x in alone))
    except Exception as e:
        logger.debug("Проверка ответственных за этапы: %s", e)


def detach_employee_references(db: Session, employee_id: int) -> None:
    """Отвязывает записи от сотрудника, сохраняя их содержимое.

    Вызывать внутри транзакции удаления, до DELETE самого сотрудника.
    """
    # ── Удаляем только личное: настройки и собственные входящие ──────────────

    # Настройки фильтра задач принадлежат учётной записи и без неё бессмысленны
    db.query(TaskFilter).filter(TaskFilter.user_id == employee_id).delete(
        synchronize_session=False)

    # notifications.employee_id — ПОЛУЧАТЕЛЬ уведомления, а не автор.
    # Здесь чистится личный «входящий» список удаляемого; уведомления, которые
    # он разослал другим, лежат отдельными строками с чужим employee_id
    # и остаются нетронутыми.
    db.query(Notification).filter(Notification.employee_id == employee_id).delete(
        synchronize_session=False)

    # ── Снимаем участие в задачах, сами задачи и этапы остаются ──────────────

    db.execute(task_stage_participants.delete().where(
        task_stage_participants.c.employee_id == employee_id
    ))

    db.execute(task_responsible_associations.delete().where(
        task_responsible_associations.c.employee_id == employee_id
    ))

    db.execute(TaskAssignment.__table__.delete().where(
        TaskAssignment.__table__.c.employee_id == employee_id
    ))

    # ── Обнуляем авторство, содержимое сохраняется ───────────────────────────

    db.query(Task).filter(Task.creator_id == employee_id).update(
        {"creator_id": None}, synchronize_session=False)

    db.query(Task).filter(Task.who_archive == employee_id).update(
        {"who_archive": None}, synchronize_session=False)

    db.query(TaskStageDetails).filter(
        TaskStageDetails.completed_by_employee_id == employee_id
    ).update({"completed_by_employee_id": None}, synchronize_session=False)

    # Разметка авторства в тексте этапа: сам текст лежит в
    # task_stage_details.text_description и не трогается, а фрагменты
    # удаляемого автора остаются без автора и показываются как «Неизвестный»
    db.query(TaskStageTextSpan).filter(
        TaskStageTextSpan.author_id == employee_id
    ).update({"author_id": None}, synchronize_session=False)

    # ── Версия 2: файлы, проекты, push, настройки ────────────────────────────

    # Личное удаляется вместе с учётной записью
    db.query(PushSubscription).filter(PushSubscription.user_id == employee_id).delete(
        synchronize_session=False)
    db.query(NativePushDevice).filter(NativePushDevice.user_id == employee_id).delete(
        synchronize_session=False)
    db.query(UserPreference).filter(UserPreference.user_id == employee_id).delete(
        synchronize_session=False)

    # Файлы и проекты остаются, отправитель/создатель показывается как «Неизвестный»
    db.query(TaskFile).filter(TaskFile.uploaded_by_id == employee_id).update(
        {"uploaded_by_id": None}, synchronize_session=False)
    db.query(TaskStageFile).filter(TaskStageFile.uploaded_by_id == employee_id).update(
        {"uploaded_by_id": None}, synchronize_session=False)
    db.query(ProjectFile).filter(ProjectFile.uploaded_by_id == employee_id).update(
        {"uploaded_by_id": None}, synchronize_session=False)
    db.query(Project).filter(Project.creator_id == employee_id).update(
        {"creator_id": None}, synchronize_session=False)
    db.query(Project).filter(Project.responsible_id == employee_id).update(
        {"responsible_id": None}, synchronize_session=False)

    # Незавершённые выдачи закрываем до обнуления ссылок: после него уже не
    # понять, чьи они были
    _close_open_issues(db, employee_id)
    _warn_orphan_stages(db, employee_id)

    blocking = _sweep_unhandled_references(db, employee_id)
    if blocking:
        raise RuntimeError(
            "Удаление невозможно: на сотрудника ссылаются обязательные поля "
            + ", ".join(blocking)
        )

    # ── Учётная запись и профиль ─────────────────────────────────────────────

    db.query(User).filter(User.id == employee_id).delete(synchronize_session=False)
    db.query(Employee).filter(Employee.id == employee_id).delete(synchronize_session=False)
