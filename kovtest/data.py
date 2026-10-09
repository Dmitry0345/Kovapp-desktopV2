import os
import json
import time
from datetime import date, timedelta
from typing import Dict, List, Optional

from .api import ApiError


# ===== тестовые данные =====

class Data:
    def __init__(self, world):
        self.world = world
        self.log = world.log
        worker = int(os.environ.get("WORKER_INDEX", "0") or 0)
        self.tag = "АТ" + time.strftime("%d%H%M%S") + ("-%d" % (worker + 1) if worker > 0 else "")
        self.cache: Dict[str, dict] = {}
        self.tasks: List[int] = []
        self.projects: List[int] = []
        self.organizations: List[int] = []
        self.templates: List[int] = []
        self.equipment: List[int] = []
        self.groups: Dict[int, str] = {}

    def name(self, text: str) -> str:
        return "[%s] %s" % (self.tag, text)

    def deadline(self, days: int = 30) -> str:
        return (date.today() + timedelta(days=days)).isoformat()

    def ids(self, *roles) -> List[int]:
        return [self.world.accounts.get(r).id for r in roles]

    def staff_ids(self, department: str) -> List[int]:
        return [a.id for a in self.world.accounts.by_department(department) if a.id]

    # ===== задачи =====

    def create_task(self, role: str, title: str, departments, responsible=(), assigned=(),
                    project_id: Optional[int] = None, fill: bool = True, deadline: Optional[str] = None) -> dict:
        api = self.world.api(role)
        payload = {
            "name": self.name(title), "description": "Создано автотестом",
            "deadline": deadline or self.deadline(), "customer_organization": None,
            "responsible_persons_ids": list(responsible), "assigned_employees_ids": list(assigned),
            "stages": [{"department_name": d, "text_description": ""} for d in departments],
        }
        if project_id:
            payload["project_id"] = project_id
        task = api.call("POST", "tasks", json=payload, expect=201)
        self.tasks.append(task["id"])
        if fill:
            for stage in task.get("stage_details") or []:
                staff = self.staff_ids(stage.get("department_name") or "")
                if staff:
                    api.call("PUT", "stages/%d/participants" % stage["id"], json=staff)
        return self.task_by_id(task["id"])

    def task_by_id(self, task_id: int, role: str = "director") -> dict:
        return self.world.api(role).call("GET", "tasks/%d" % task_id)

    def stage(self, task: dict, number: int) -> dict:
        for stage in task.get("stage_details") or []:
            if stage["stage_number"] == number:
                return stage
        raise KeyError("в задаче %s нет этапа %s" % (task.get("id"), number))

    def complete_stage(self, role: str, task: dict, number: int) -> dict:
        stage = self.stage(task, number)
        return self.world.api(role).call("POST", "tasks/%d/stages/%d/complete" % (task["id"], stage["id"]))

    def extend(self, role: str, task: dict, department: str, responsible_ids=None) -> dict:
        body = {"department_name": department, "text_description": "", "responsible_ids": responsible_ids}
        return self.world.api(role).call("POST", "tasks/%d/stages" % task["id"], json=body)

    def task(self, key: str) -> dict:
        if key in self.cache:
            return self.task_by_id(self.cache[key]["id"])
        mont = "Монтажный отдел"
        if key == "mont":
            task = self.create_task("head_mont", "Задача монтажа", [mont],
                                    responsible=self.ids("head_mont"), assigned=self.ids("worker_mont"))
        elif key == "multi":
            task = self.create_task("director", "Задача двух отделов", [mont, "Отдел ТО"],
                                    responsible=self.ids("head_mont", "head_to"),
                                    assigned=self.ids("worker_mont", "worker_to"))
        elif key == "contract":
            task = self.create_task("director", "Задача с договорным этапом", [mont],
                                    responsible=self.ids("head_mont"), assigned=self.ids("worker_mont"))
            self.complete_stage("director", task, 1)
            self.extend("director", task, "Договорной отдел", self.ids("contract1"))
            task = self.task_by_id(task["id"])
        elif key == "archived":
            task = self.create_task("head_mont", "Задача в архиве", [mont],
                                    responsible=self.ids("head_mont"), assigned=self.ids("worker_mont"))
            self.world.api("director").call("POST", "tasks/%d/archive" % task["id"])
            task = self.task_by_id(task["id"])
        elif key == "to":
            task = self.create_task("head_to", "Задача отдела ТО", ["Отдел ТО"],
                                    responsible=self.ids("head_to"), assigned=self.ids("worker_to"))
        else:
            raise KeyError(key)
        self.cache[key] = task
        return task

    def delete_task(self, task_id: int) -> None:
        try:
            self.world.api("director").call("DELETE", "tasks/%d" % task_id, expect=(200, 404))
        except ApiError as e:
            self.log.warn("задача %s не удалена: %s" % (task_id, e))

    # ===== файлы =====

    def upload(self, role: str, kinds=("jpg",), task_id: Optional[int] = None,
               stage_id: Optional[int] = None, expect=(200,)) -> list:
        files = []
        for kind in kinds:
            name, content, ctype = self.world.media.get(kind)
            files.append(("files", (name, content, ctype)))
        form = {}
        if task_id:
            form["task_id"] = str(task_id)
        if stage_id:
            form["stage_detail_id"] = str(stage_id)
        return self.world.api(role).call("POST", "upload", data=form, files=files, expect=expect, timeout=600)

    def project_upload(self, role: str, project_id: int, section: str, kinds=("pdf",)) -> list:
        files = []
        for kind in kinds:
            name, content, ctype = self.world.media.get(kind)
            files.append(("files", (name, content, ctype)))
        return self.world.api(role).call("POST", "projects/%d/files" % project_id, data={"section": section},
                                         files=files, timeout=600)

    # ===== проекты =====

    def create_project(self, role: str, title: str, responsible_role: Optional[str] = None) -> dict:
        payload = {"name": self.name(title), "organization": self.name("Организация"),
                   "deadline": self.deadline(60), "description": "Создано автотестом"}
        if responsible_role:
            payload["responsible_id"] = self.world.accounts.get(responsible_role).id
        project = self.world.api(role).call("POST", "projects", json=payload, expect=201)
        self.projects.append(project["id"])
        return project

    def project(self, key: str = "main") -> dict:
        if key not in self.cache:
            if key == "main":
                self.cache[key] = self.create_project("director", "Проект автотеста", "head_mont")
            elif key == "contract":
                self.cache[key] = self.create_project("contract1", "Проект договорного отдела", "head_mont")
            else:
                raise KeyError(key)
        return self.world.api("director").call("GET", "projects/%d" % self.cache[key]["id"])

    # ===== справочники =====

    def organization(self, role: str = "director", title: str = "Организация") -> dict:
        payload = {"name": self.name(title) + " " + str(len(self.organizations) + 1),
                   "description": "Создано автотестом",
                   "phones": [{"phone_number": "+7 (812) 000-00-01", "description": "Приёмная"}],
                   "emails": [{"email": "office@kovapp-autotest.ru", "description": "Общая"}]}
        org = self.world.api(role).call("POST", "organizations", json=payload, expect=201)
        self.organizations.append(org["id"])
        return org

    def template(self, departments=("Монтажный отдел",)) -> dict:
        payload = {"template_name": self.name("Шаблон") + " " + str(len(self.templates) + 1),
                   "num_stages": len(departments), "departments": ", ".join(departments)}
        tpl = self.world.api("director").call("POST", "templates", json=payload, expect=201)
        self.templates.append(tpl["id"])
        return tpl

    def equipment_item(self, title: str = "Перфоратор", quantity: float = 5) -> dict:
        row = self.world.api("director").call("POST", "equipment", json={
            "name": self.name(title), "quantity": quantity, "unit": "шт"}, expect=201)
        self.equipment.append(row["id"])
        return row

    # ===== чаты =====

    def group_chat(self, owner: str, members, title: str = "Группа") -> dict:
        api = self.world.api(owner)
        chat = api.call("POST", "chat/group", json={"name": self.name(title)})
        self.groups[chat["id"]] = owner
        ids = [self.world.accounts.get(m).id for m in members]
        if ids:
            api.call("POST", "chat/group/%d/participants" % chat["id"], json={"user_ids": ids})
        return chat

    def private_chat(self, role: str, other: str) -> dict:
        chat = self.world.api(role).call("POST", "chat/private",
                                         json={"target_user_id": self.world.accounts.get(other).id})
        return chat.get("chat") if isinstance(chat.get("chat"), dict) else chat

    def send_chat_files(self, role: str, chat_id: int, kinds=("jpg",), text: str = "") -> list:
        files = []
        for kind in kinds:
            name, content, ctype = self.world.media.get(kind)
            files.append(("files", (name, content, ctype)))
        form = {"chat_id": str(chat_id)}
        if text:
            form["captions"] = json.dumps([text])
        return self.world.api(role).call("POST", "chat/grouped-message", data=form, files=files, timeout=600)

    # ===== уборка =====

    def cleanup(self) -> None:
        # прогон остановился раньше, чем создались тестовые сотрудники — убирать нечего
        if not (self.tasks or self.projects or getattr(self, "chats", None)) and "director" not in getattr(self.world.accounts, "items", {}):
            return
        director = self.world.api("director")
        for task_id in reversed(self.tasks):
            self.delete_task(task_id)
        for project_id in reversed(self.projects):
            try:
                director.call("DELETE", "projects/%d" % project_id, expect=(200, 404))
            except ApiError as e:
                self.log.warn("проект %s не удалён: %s" % (project_id, e))
        for org_id in self.organizations:
            try:
                director.call("DELETE", "organizations/%d" % org_id, expect=(200, 404))
            except ApiError as e:
                self.log.warn("организация %s не удалена: %s" % (org_id, e))
        for tpl_id in self.templates:
            try:
                director.call("DELETE", "templates/%d" % tpl_id, expect=(200, 404))
            except ApiError as e:
                self.log.warn("шаблон %s не удалён: %s" % (tpl_id, e))
        for item_id in self.equipment:
            try:
                director.call("DELETE", "equipment/%d" % item_id, expect=(200, 404, 409))
            except ApiError as e:
                self.log.warn("позиция оборудования %s не удалена: %s" % (item_id, e))
        for chat_id, owner in self.groups.items():
            try:
                self.world.api(owner).call("DELETE", "chat/group/%d" % chat_id, expect=(200, 404))
            except ApiError as e:
                self.log.warn("группа %s не удалена: %s" % (chat_id, e))
