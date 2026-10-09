# -*- coding: utf-8 -*-
"""Свод прав — то же, что js/core/rights.js веб-версии.

Здесь решается только, что показывать и что помечать замком; настоящая
проверка на сервере. Завершённый проект — «только просмотр»: пока открыт
он сам, его разделы или его задачи, изменения недоступны.
"""

from kov.store import store, _get

CONTRACT = "Договорной отдел"
NO_PROJECTS_FOR_WORKERS = ("Монтажный отдел", "Отдел ТО")

_ro = {"on": False, "text": ""}


def set_read_only(on, text=""):
    _ro["on"] = bool(on)
    _ro["text"] = text or ""


def read_only():
    return _ro["on"]


def read_only_text():
    return _ro["text"] or "Проект завершён — только просмотр"


def _dept(user=None):
    return store.department(user)


def management(user=None):
    return store.is_management(user)


def head(user=None):
    return store.is_department_head(user)


def contract(user=None):
    return _dept(user) == CONTRACT


# ── задачи ───────────────────────────────────────────────────────────────────

def can_create_task():
    if read_only():
        return False
    return management() or head()


def max_stages_on_create():
    return 10 if management() else 1


def departments_for_new_task(all_departments):
    if management():
        return list(all_departments)
    own = _dept()
    return [d for d in all_departments if d == own]


def task_departments(task):
    if not task:
        return []
    stages = _get(task, "stage_details")
    if isinstance(stages, list):
        seen = []
        for s in stages:
            d = _get(s, "department_name")
            d = None if d is None else str(d)
            if d not in seen:
                seen.append(d)
        return seen
    deps = _get(task, "departments")
    if isinstance(deps, list):
        out = []
        for d in deps:
            if str(d) not in out:
                out.append(str(d))
        return out
    return []


def head_for_task(task):
    if not head():
        return False
    own = _dept()
    deps = task_departments(task)
    return len(deps) == 1 and deps[0] not in (None, "") and deps[0] == own


def can_manage_task(task):
    if read_only():
        return False
    return management() or head_for_task(task)


def can_manage_templates():
    return management()


def in_stage_department(stage):
    if not stage:
        return False
    if management():
        return True
    return str(_get(stage, "department_name") or "").strip() == _dept()


def can_open_stage(stage):
    return in_stage_department(stage)


def can_edit_stage(stage):
    if read_only():
        return False
    return in_stage_department(stage)


def leads_stage(stage):
    if management():
        return True
    if not stage:
        return False
    lead = _get(stage, "i_lead")
    if isinstance(lead, bool):
        return lead
    if not in_stage_department(stage):
        return False
    return head() or contract()


def can_complete_stage(stage):
    if read_only():
        return False
    return in_stage_department(stage)


def can_extend_task(stage):
    if read_only():
        return False
    return management() or head() or leads_stage(stage)


def can_edit_stage_roster(stage):
    if read_only():
        return False
    return leads_stage(stage)


def can_request_completion(stage, task):
    if read_only():
        return False
    return management() or head_for_task(task) or leads_stage(stage)


def can_decide_completion():
    if read_only():
        return False
    return management()


# ── организации ──────────────────────────────────────────────────────────────

def can_view_organizations():
    return True


def can_edit_organizations():
    return management() or head() or contract()


# ── проекты ──────────────────────────────────────────────────────────────────

def can_view_projects():
    if management() or head():
        return True
    return _dept() not in NO_PROJECTS_FOR_WORKERS


def can_create_project():
    return management() or head() or contract()


def can_edit_project(project):
    if read_only():
        return False
    if not project:
        return can_create_project()
    flag = _get(project, "can_edit")
    if isinstance(flag, bool):
        return flag
    return store.is_director() or _get(project, "creator_id") == _get(store.me, "id")


SECTIONS = {
    "notifications": lambda: True,
    "tasks": lambda: True,
    "projects": can_view_projects,
    "organizations": can_view_organizations,
    "equipment": lambda: True,
    "employees": lambda: True,
    "csv": management,
    "profile": lambda: True,
    "settings": lambda: True,
    "messenger": lambda: True,
}


def can_open_section(key):
    rule = SECTIONS.get(key)
    return bool(rule()) if rule else True
