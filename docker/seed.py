import os
import sys
import time
from pathlib import Path

import pymysql
import yaml

APP = Path("/opt/kovcheg")
sys.path.insert(0, str(APP))
os.chdir(APP)


# ===== ожидание базы =====

def wait_database(timeout: int = 240) -> None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            pymysql.connect(host=os.environ["DB_HOST"], port=int(os.environ.get("DB_PORT", "3306")),
                            user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"]).close()
            return
        except Exception as e:
            last = e
            time.sleep(2)
    raise SystemExit("База данных не ответила: %s" % last)


# ===== учётные записи ролей =====

def seed_roles() -> None:
    from server.database.init_database import SessionMain, hash_password
    from server.database.models import Employee, User
    roles = yaml.safe_load(open("/opt/autotest/config/roles.yaml", encoding="utf-8"))["roles"]
    password = os.environ.get("AUTOTEST_ACCOUNT_PASSWORD", "Autotest-2026!")
    workers = max(1, int(os.environ.get("AUTOTEST_WORKERS") or 1))
    db = SessionMain()
    try:
        for worker in range(workers):
            suffix = "" if worker == 0 else "_w%d" % (worker + 1)
            for key, spec in roles.items():
                if spec.get("builtin"):
                    continue
                login = spec["login"] + suffix
                if db.query(User).filter(User.username == login).first():
                    continue
                employee = Employee(first_name=spec.get("title", key), last_name="Автотест", middle_name=key + suffix,
                                    position=spec.get("position"), status=spec.get("status"),
                                    department=spec.get("department"), phone_number="+7 (900) 000-00-00",
                                    email="%s@kovapp-autotest.ru" % login, profile="Учётная запись автотестов")
                db.add(employee)
                db.flush()
                db.add(User(id=employee.id, username=login, password=hash_password(password)))
        load_users = max(0, int(os.environ.get("AUTOTEST_LOAD_USERS") or 0))
        for number in range(1, load_users + 1):
            login = "at_load%02d" % number
            if db.query(User).filter(User.username == login).first():
                continue
            employee = Employee(first_name="Нагрузка %02d" % number, last_name="Автотест", middle_name=login,
                                position="Монтажник", status="работник", department="Монтажный отдел",
                                phone_number="+7 (900) 000-00-00", email="%s@kovapp-autotest.ru" % login,
                                profile="Учётная запись нагрузочной проверки")
            db.add(employee)
            db.flush()
            db.add(User(id=employee.id, username=login, password=hash_password(password)))
        db.commit()
    finally:
        db.close()
    print("Учётные записи ролей созданы: %d × %d" % (len(roles) - 1, workers), flush=True)


if __name__ == "__main__":
    wait_database()
    from server.database import init_database as init
    init.recreate_main_database()
    init.recreate_chat_database()
    init.create_admin()
    seed_roles()
