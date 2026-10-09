# -*- coding: utf-8 -*-
"""Состояние сеанса — как js/core/store.js: текущий сотрудник, кэш людей,
маркеры прав и личные настройки на сервере.

Права определяет маркер employees.status (работник / руководитель отдела /
директор / администратор); разбор должности — запасной путь для старых
записей. Настройки ПК-приложения хранятся под ключом «…​.pc» — тем же, что
у сайта на компьютере, поэтому тема и масштаб у них общие.
"""

import time

from kov.api import client

STATUSES = {"worker": "работник", "head": "руководитель отдела",
            "director": "директор", "admin": "администратор"}
DEVICE_SUFFIX = ".pc"


class Store:
    def __init__(self):
        self.reset()

    def reset(self):
        self.me = None
        self._employees = []
        self._employees_at = 0.0
        self.photo_ids = set()
        self._photo_at = 0.0
        self.prefs = {}
        self._all_prefs = None
        self.departments = []
        self.positions = []

    # ── статус и роли ────────────────────────────────────────────────────────

    def status_of(self, user=None):
        me = user if user is not None else (self.me or {})
        marked = str(_get(me, "status") or "").strip().lower()
        if marked in STATUSES.values():
            return marked
        p = str(_get(me, "position") or "").strip()
        if p == "Директор":
            return STATUSES["director"]
        if p == "Администратор":
            return STATUSES["admin"]
        if p and ("Старший" in p or "Главный" in p):
            return STATUSES["head"]
        return STATUSES["worker"]

    def is_management(self, user=None):
        return self.status_of(user) in (STATUSES["director"], STATUSES["admin"])

    def is_director(self, user=None):
        return self.status_of(user) == STATUSES["director"]

    def is_admin(self, user=None):
        return self.status_of(user) == STATUSES["admin"]

    def is_department_head(self, user=None):
        return self.status_of(user) == STATUSES["head"]

    def can_manage_work(self, user=None):
        return self.is_management(user) or self.is_department_head(user)

    def is_hidden_account(self, person):
        """Служебная запись администратора не видна остальным в списках выбора."""
        if not person or self.is_admin(self.me):
            return False
        return self.status_of(person) == STATUSES["admin"] and _get(person, "id") != client.user_id

    def visible(self, people):
        return [p for p in (people or []) if not self.is_hidden_account(p)]

    def department(self, user=None):
        return str(_get(user if user is not None else (self.me or {}), "department") or "").strip()

    # ── данные ───────────────────────────────────────────────────────────────

    def load_me(self):
        self.me = client.get("employees/%d" % client.user_id)
        return self.me

    def employees(self, force=False):
        if not force and self._employees and time.time() - self._employees_at < 60:
            return self._employees
        self._employees = client.get("employees") or []
        self._employees_at = time.time()
        return self._employees

    def pickable(self, force=False):
        return self.visible(self.employees(force))

    def employee(self, emp_id):
        for e in self._employees:
            if e.get("id") == emp_id:
                return e
        return client.get("employees/%d" % emp_id)

    def load_photo_ids(self, force=False):
        if not force and time.time() - self._photo_at < 60:
            return self.photo_ids
        try:
            data = client.get("employees/photo/ids")
            if isinstance(data, dict):
                data = data.get("ids") or data.get("employee_ids") or []
            self.photo_ids = set(int(x) for x in (data or []))
        except Exception:
            self.photo_ids = set()
        self._photo_at = time.time()
        return self.photo_ids

    def lists(self):
        try:
            self.departments = client.get("employees/departments") or []
        except Exception:
            pass
        try:
            self.positions = client.get("employees/positions") or []
        except Exception:
            pass
        return self.departments, self.positions

    # ── личные настройки (таблица user_preferences) ─────────────────────────

    def _load_all_prefs(self):
        if self._all_prefs is None:
            try:
                rows = client.get("preferences") or []
                self._all_prefs = {r.get("key"): r.get("value") for r in rows if isinstance(r, dict)}
            except Exception:
                self._all_prefs = {}
        return self._all_prefs

    def pref(self, key):
        if key in self.prefs:
            return self.prefs[key]
        all_prefs = self._load_all_prefs()
        own = all_prefs.get(key + DEVICE_SUFFIX)
        value = own if own is not None else all_prefs.get(key)
        self.prefs[key] = value
        return value

    def set_pref(self, key, value):
        self.prefs[key] = value
        if self._all_prefs is not None:
            self._all_prefs[key + DEVICE_SUFFIX] = value
        client.put("preferences/" + key + DEVICE_SUFFIX, json_body={"value": value})


def _get(obj, name, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def fio(person, short=False):
    """«Фамилия Имя Отчество» (или «Фамилия И.О.»)."""
    last = (_get(person, "last_name") or "").strip()
    first = (_get(person, "first_name") or "").strip()
    middle = (_get(person, "middle_name") or "").strip()
    if short:
        initials = "".join(x[0] + "." for x in (first, middle) if x)
        return (last + " " + initials).strip() or "Без имени"
    return " ".join(x for x in (last, first, middle) if x) or "Без имени"


store = Store()
