# -*- coding: utf-8 -*-
"""Установка пароля пользователю прямо в базе.

Запуск с сервера, из каталога рядом с пакетом server/:

    /opt/kovcheg/venv/bin/python -m server.set_password admin
    /opt/kovcheg/venv/bin/python -m server.set_password admin --show-users

Пароль вводится скрытно и в историю команд не попадает.
"""

import getpass
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

APP_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(APP_ROOT / ".env")

import bcrypt  # noqa: E402

from server.database.init_database import SessionLocal  # noqa: E402
from server.database.models import Employee, User  # noqa: E402


try:
    MIN_LENGTH = int(float((os.getenv("MIN_PASSWORD_LENGTH") or "8").strip() or 8))
except ValueError:
    MIN_LENGTH = 8


def list_users(db):
    rows = (db.query(User, Employee)
              .outerjoin(Employee, Employee.id == User.id)
              .order_by(User.id)
              .all())
    print(f"{'id':>5}  {'логин':<20} {'должность':<20} фамилия")
    for user, emp in rows:
        print(f"{user.id:>5}  {user.username or '':<20} "
              f"{(emp.position if emp else '') or '':<20} "
              f"{(emp.last_name if emp else '') or ''}")


def main():
    args = [a for a in sys.argv[1:]]

    db = SessionLocal()
    try:
        if "--show-users" in args or not args:
            list_users(db)
            if not args:
                print("\nУкажите логин: python -m server.set_password ЛОГИН")
            return 0

        username = args[0]
        user = db.query(User).filter_by(username=username).first()
        if not user:
            print(f"Пользователь «{username}» не найден. "
                  f"Список: python -m server.set_password --show-users")
            return 1

        password = getpass.getpass("Новый пароль: ")
        if len(password) < MIN_LENGTH:
            print(f"Пароль короче {MIN_LENGTH} символов — отклонён")
            return 1
        if password != getpass.getpass("Повторите пароль: "):
            print("Пароли не совпали")
            return 1

        user.password = bcrypt.hashpw(password.encode("utf-8"),
                                      bcrypt.gensalt()).decode("utf-8")
        user.reset_code = None
        db.commit()

        emp = db.query(Employee).filter_by(id=user.id).first()
        position = (emp.position if emp else "") or "должность не задана"
        print(f"Пароль для «{username}» обновлён ({position})")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
