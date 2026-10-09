# init_database.py
import os
import random
import secrets
import sys
import bcrypt

from pathlib import Path

from dotenv import load_dotenv

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from server.database.models import (
    Base,
    BaseChat,
    Employee,
    User,
)


def get_app_root() -> Path:
    """
    Возвращает корневую папку приложения.

    При сборке:
        KovServer/
            .env
            InitDatabase/
                InitDatabase.exe

        => KovServer/

    При запуске из исходников:
        project/
            server/
                database/
                    init_database.py

        => project/
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent

    return Path(__file__).resolve().parent.parent.parent


# =====================================================
# ENV
# =====================================================

APP_ROOT = get_app_root()
ENV_FILE = APP_ROOT / ".env"

load_dotenv(ENV_FILE)


# =====================================================
# Настройки
# =====================================================

DB_TYPE = os.getenv("DB_TYPE", "mysql")
DB_DRIVER = os.getenv("DB_DRIVER", "pymysql")

DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = os.getenv("DB_PORT", "3306")


MAIN_DB_NAME = os.getenv(
    "DB_NAME",
    "kovcheg"
)

CHAT_DB_NAME = "kovcheg_messenger"


def make_url(db_name):

    return (
        f"{DB_TYPE}+{DB_DRIVER}://"
        f"{DB_USER}:{DB_PASSWORD}@"
        f"{DB_HOST}:{DB_PORT}/"
        f"{db_name}"
    )


MAIN_DATABASE_URL = make_url(MAIN_DB_NAME)
CHAT_DATABASE_URL = make_url(CHAT_DB_NAME)


print("Загружен .env:")
print(ENV_FILE)

print("\nОсновная БД:")
print(
    MAIN_DATABASE_URL.replace(
        DB_PASSWORD,
        "***"
    )
)

print("БД чата:")
print(
    CHAT_DATABASE_URL.replace(
        DB_PASSWORD,
        "***"
    )
)


# =====================================================
# Engines
# =====================================================

engine_main = create_engine(
    MAIN_DATABASE_URL,
    echo=False,
    pool_pre_ping=True
)


engine_chat = create_engine(
    CHAT_DATABASE_URL,
    echo=False,
    pool_pre_ping=True
)


SessionMain = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine_main
)

SessionLocal = SessionMain

# Старый engine основной БД
engine = engine_main

# Старый URL основной БД
DATABASE_URL = MAIN_DATABASE_URL
# =====================================================
# Создание базы чата если её нет
# =====================================================

def create_chat_database_if_not_exists():

    server_url = (
        f"{DB_TYPE}+{DB_DRIVER}://"
        f"{DB_USER}:{DB_PASSWORD}@"
        f"{DB_HOST}:{DB_PORT}/"
    )

    server_engine = create_engine(
        server_url
    )

    with server_engine.connect() as conn:

        conn.execute(
            text(
                f"""
                CREATE DATABASE IF NOT EXISTS
                `{CHAT_DB_NAME}`
                CHARACTER SET utf8mb4
                COLLATE utf8mb4_unicode_ci
                """
            )
        )

        conn.commit()

    server_engine.dispose()


# =====================================================
# Очистка и создание таблиц
# =====================================================

def recreate_main_database():

    print("\n=== Основная БД ===")

    print("Удаление таблиц...")

    Base.metadata.drop_all(
        bind=engine_main
    )


    print("Создание таблиц...")

    Base.metadata.create_all(
        bind=engine_main
    )

    print("Основная БД готова")


def recreate_chat_database():

    print("\n=== БД Чата ===")

    create_chat_database_if_not_exists()


    print("Удаление таблиц чата...")

    BaseChat.metadata.drop_all(
        bind=engine_chat
    )


    print("Создание таблиц чата...")

    BaseChat.metadata.create_all(
        bind=engine_chat
    )

    print("БД чата готова")


# =====================================================
# Пользователи
# =====================================================

def hash_password(password):

    return bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt()
    ).decode("utf-8")


def create_admin():

    db = SessionMain()

    try:

        employee = (
            db.query(Employee)
            .filter_by(id=1)
            .first()
        )


        if not employee:

            employee = Employee(
                id=1,
                first_name="Системы",
                last_name="Администратор",
                middle_name="",
                position="Администратор",
                status="администратор",
                phone_number="+7 (999) 999-99-99",
                email="admin@example.com",
                profile="Администратор системы",
                department="Руководство"
            )

            db.add(employee)
            db.commit()



        user = (
            db.query(User)
            .filter_by(id=1)
            .first()
        )


        if not user:

            # LOGIN_ADMIN/PASSWORD_ADMIN теперь отвечают за аварийный вход
            # и обычно пустые, поэтому первого админа заводим отдельными
            # переменными, а без них — со случайным паролем
            login = (os.getenv("INIT_ADMIN_LOGIN") or "").strip() or "admin"
            password = (os.getenv("INIT_ADMIN_PASSWORD") or "").strip()
            generated = not password

            if generated:
                password = secrets.token_urlsafe(12)

            user = User(
                id=employee.id,
                username=login,
                password=hash_password(password)
            )

            db.add(user)
            db.commit()

            print("\n" + "=" * 52)
            print("Учётная запись администратора создана")
            print(f"  логин:  {login}")
            if generated:
                print(f"  пароль: {password}")
                print("  Пароль сгенерирован и больше нигде не хранится —")
                print("  запишите его сейчас или смените командой")
                print("  python -m server.set_password " + login)
            else:
                print("  пароль: из INIT_ADMIN_PASSWORD")
            print("=" * 52)


        print(
            "Администратор создан"
        )


    finally:

        db.close()



def populate_employees():

    db = SessionMain()

    try:

        departments = [
            "Монтажный отдел",
            "Бухгалтерия",
            "Отдел ТО",
            "Договорной отдел"
        ]


        positions = [
            "Директор",
            "Старший сотрудник",
            "Сотрудник",
            "Главный бухгалтер",
            "Бухгалтер",
            "Старший монтажник",
            "Монтажник"
        ]


        current = db.query(
            Employee
        ).count()


        need = 30 - current


        for i in range(need):

            number = current + i + 1


            employee = Employee(
                first_name=f"Имя_{number}",
                last_name=f"Фамилия_{number}",
                middle_name="",
                position=random.choice(
                    positions
                ),
                phone_number=f"+7 (999) 000-{number:04}",
                email=f"user{number}@example.com",
                profile="Тестовый сотрудник",
                department=random.choice(
                    departments
                )
            )


            db.add(employee)
            db.flush()


            user = User(
                id=employee.id,
                username=f"user_{number}",
                password=hash_password(
                    "password123"
                )
            )

            db.add(user)


        db.commit()


        print(
            f"Добавлено сотрудников: {max(need,0)}"
        )


    finally:

        db.close()



# =====================================================
# MAIN
# =====================================================

def _confirm_wipe() -> bool:
    """Скрипт удаляет ВСЕ таблицы обеих баз. Спрашиваем явно."""
    if (os.getenv("INIT_DB_FORCE") or "").strip().lower() in ("1", "true", "yes"):
        return True
    if "--yes" in sys.argv:
        return True

    print("\n" + "!" * 60)
    print("ВНИМАНИЕ: будут удалены ВСЕ таблицы и данные в базах:")
    print(f"  основная: {MAIN_DB_NAME}")
    print(f"  чат:      {CHAT_DB_NAME}")
    print("Операция необратима. Для первой установки это нормально,")
    print("для работающей системы — нет.")
    print("!" * 60)

    return input('Введите УДАЛИТЬ для подтверждения: ').strip().upper() == "УДАЛИТЬ"


def main():
    success = False

    if not _confirm_wipe():
        print("Отменено, ничего не изменено.")
        return

    try:
        recreate_main_database()
        recreate_chat_database()
        create_admin()

        print("\n====================")
        print("Базы успешно пересозданы")
        print("====================")

        success = True

    except Exception:
        import traceback

        print("\n====================")
        print("ОШИБКА ИНИЦИАЛИЗАЦИИ")
        print("====================")

        traceback.print_exc()

    finally:
        engine_main.dispose()
        engine_chat.dispose()

        print("\nПрограмма завершена.")

        if success:
            print("Нажмите Enter, чтобы закрыть окно...")
        else:
            print("Произошла ошибка.")
            print("Проверьте сообщение выше.")
            print("Нажмите Enter, чтобы закрыть окно...")

        input()


if __name__ == "__main__":
    main()