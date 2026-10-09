import time
from typing import Dict, List

from .api import Api, ApiError
from .config import env, load_yaml

MARK = "Автотест"


# ===== роли и учётные записи =====

class Account:
    def __init__(self, key: str, spec: dict, login: str, password: str):
        self.key = key
        self.spec = spec
        self.login = login
        self.password = password
        self.id = None
        self.created = False

    @property
    def title(self) -> str:
        return self.spec.get("title", self.key)

    @property
    def department(self) -> str:
        return self.spec.get("department", "")

    @property
    def status(self) -> str:
        return self.spec.get("status", "")


class Accounts:
    def __init__(self, world):
        self.world = world
        self.settings = world.settings
        self.log = world.log
        self.specs: Dict[str, dict] = load_yaml("roles.yaml")["roles"]
        self.items: Dict[str, Account] = {}
        worker = int(env("WORKER_INDEX", "0") or 0)
        self.suffix = "_w%d" % (worker + 1) if worker > 0 else ""
        self.run_tag = time.strftime("%m%d%H%M") + ("w%d" % (worker + 1) if worker > 0 else "")

    def keys(self) -> List[str]:
        return list(self.specs.keys())

    def select(self, group: str = "all") -> List[str]:
        if group in self.specs:
            return [group]
        return [k for k, v in self.specs.items() if group in (v.get("groups") or [])]

    def get(self, key: str) -> Account:
        return self.items[key]

    def by_department(self, department: str) -> List[Account]:
        return [a for a in self.items.values() if a.department == department]

    def _login_for(self, key: str, spec: dict) -> str:
        if spec.get("builtin"):
            return self.settings.admin_login
        if self.settings.mode == "full":
            return spec["login"] + self.suffix
        if self.settings.create_accounts:
            return "at%s_%s" % (self.run_tag, key)
        return env("ROLE_%s_LOGIN" % key.upper(), spec["login"])

    def _password_for(self, key: str, spec: dict) -> str:
        if spec.get("builtin"):
            return self.settings.admin_password
        if self.settings.mode != "full" and not self.settings.create_accounts:
            return env("ROLE_%s_PASSWORD" % key.upper(), self.settings.account_password)
        return self.settings.account_password

    def prepare(self) -> None:
        for key, spec in self.specs.items():
            self.items[key] = Account(key, spec, self._login_for(key, spec), self._password_for(key, spec))
        admin = self.world.api("admin")
        if self.settings.mode == "external" and self.settings.create_accounts:
            if env("AUTOTEST_SKIP_LEFTOVERS", "0") != "1":
                self.remove_leftovers(admin)
            for key, account in self.items.items():
                if account.spec.get("builtin"):
                    continue
                self._create(admin, account)
        for key, account in self.items.items():
            api = self.world.api(key)
            account.id = api.user_id
        self.log.info("учётные записи: " + ", ".join("%s=%s(id %s)" % (k, a.login, a.id) for k, a in self.items.items()))

    def _create(self, admin: Api, account: Account) -> None:
        spec = account.spec
        payload = {
            "username": account.login, "password": account.password,
            "last_name": MARK, "first_name": spec.get("title", account.key), "middle_name": account.key + self.suffix,
            "position": spec.get("position"), "status": spec.get("status"),
            "department": spec.get("department"),
            "phone_number": "+7 (900) 000-00-00",
            "email": "%s@kovapp-autotest.ru" % account.login,
            "profile": "Временная учётная запись автотестов",
        }
        body = admin.call("POST", "employees", json=payload)
        account.id = body["id"]
        account.created = True

    def remove_leftovers(self, admin: Api) -> None:
        try:
            people = admin.call("GET", "employees")
        except ApiError as e:
            self.log.warn("список сотрудников не получен: %s" % e)
            return
        for person in people or []:
            if (person.get("last_name") or "") == MARK:
                try:
                    admin.call("DELETE", "employees/%s" % person["id"])
                    self.log.info("удалена оставшаяся учётная запись автотестов id=%s" % person["id"])
                except ApiError as e:
                    self.log.warn("не удалось удалить оставшуюся запись %s: %s" % (person["id"], e))

    def teardown(self) -> None:
        if not (self.settings.mode == "external" and self.settings.create_accounts and self.settings.cleanup):
            return
        admin = self.world.api("admin")
        for account in self.items.values():
            if account.created and account.id:
                try:
                    admin.call("DELETE", "employees/%s" % account.id)
                except ApiError as e:
                    self.log.warn("учётная запись %s не удалена: %s" % (account.login, e))
