import time

from kovtest import case
from kovtest.api import ApiError, detail_of
from kovtest.config import load_yaml

MATRIX = load_yaml("permissions.yaml")
MONT = "Монтажный отдел"


# ===== разбор матрицы =====

def expand(names):
    groups = MATRIX["groups"]
    out = []
    for name in names or []:
        for role in groups.get(name, [name]):
            if role not in out:
                out.append(role)
    return out


def own_department(t, role):
    department = t.account(role).department
    return MONT if department in ("", "Руководство") else department


class Outcome:
    def __init__(self, status, detail=""):
        self.status = status
        self.detail = detail


def outcome(response):
    return Outcome(response.status_code, "" if response.status_code < 400 else detail_of(response))


def other(role, first="worker_mont", second="head_mont"):
    return second if role == first else first


# ===== действия =====

def a_task_create_own(t, role):
    api = t.api(role)
    body = {"name": t.data.name("Своя задача " + role), "deadline": t.data.deadline(),
            "responsible_persons_ids": [], "assigned_employees_ids": [],
            "stages": [{"department_name": own_department(t, role), "text_description": ""}]}
    response = api.post("tasks", json=body)
    if response.status_code == 201:
        t.data.tasks.append(response.json()["id"])
    return outcome(response)


def a_task_create_foreign(t, role):
    department = "Отдел ТО" if t.account(role).department == MONT else MONT
    body = {"name": t.data.name("Чужой отдел " + role), "deadline": t.data.deadline(),
            "stages": [{"department_name": department, "text_description": ""}]}
    response = t.api(role).post("tasks", json=body)
    if response.status_code == 201:
        t.data.tasks.append(response.json()["id"])
    return outcome(response)


def a_task_create_two(t, role):
    department = own_department(t, role)
    body = {"name": t.data.name("Два этапа " + role), "deadline": t.data.deadline(),
            "stages": [{"department_name": department, "text_description": ""},
                       {"department_name": department, "text_description": ""}]}
    response = t.api(role).post("tasks", json=body)
    if response.status_code == 201:
        t.data.tasks.append(response.json()["id"])
    return outcome(response)


def a_task_from_template(t, role):
    tpl = t.data.cache.get("tpl_mont") or t.data.template((MONT,))
    t.data.cache["tpl_mont"] = tpl
    body = {"name": t.data.name("Из шаблона " + role), "deadline": t.data.deadline()}
    response = t.api(role).post("tasks/from-template/%d" % tpl["id"], json=body)
    if response.status_code == 201:
        t.data.tasks.append(response.json()["id"])
    return outcome(response)


def a_task_view(t, role):
    return outcome(t.api(role).get("tasks/%d" % t.data.task("mont")["id"]))


def a_task_history(t, role):
    return outcome(t.api(role).get("tasks/%d/history" % t.data.task("mont")["id"]))


def a_task_edit(t, role):
    task = t.data.task("mont")
    return outcome(t.api(role).put("tasks/%d" % task["id"], json={"description": "Правка автотеста: " + role}))


def a_task_edit_multi(t, role):
    task = t.data.task("multi")
    return outcome(t.api(role).put("tasks/%d" % task["id"], json={"description": "Правка автотеста: " + role}))


def a_task_edit_foreign_stage(t, role):
    task = t.data.create_task("director", "Передача этапа " + role, [MONT], responsible=t.data.ids("head_mont"))
    stage = task["stage_details"][0]
    body = {"stages": [{"stage_number": stage["stage_number"], "department_name": "Отдел ТО"}]}
    return outcome(t.api(role).put("tasks/%d" % task["id"], json=body))


def a_task_archive(t, role):
    task = t.data.create_task("director", "Архив " + role, [MONT], responsible=t.data.ids("head_mont"))
    first = t.api(role).post("tasks/%d/archive" % task["id"])
    if first.status_code == 200:
        back = t.api(role).post("tasks/%d/unarchive" % task["id"])
        t.check(back.status_code == 200, "%s: задача ушла в архив, но вернуть её не удалось (%s)" % (role, back.status_code))
    return outcome(first)


def a_task_delete(t, role):
    task = t.data.create_task("director", "Удаление " + role, [MONT], responsible=t.data.ids("head_mont"))
    return outcome(t.api(role).delete("tasks/%d" % task["id"]))


def a_task_extend(t, role):
    task = t.data.create_task("director", "Продление " + role, [MONT], responsible=t.data.ids("head_mont"))
    body = {"department_name": "Отдел ТО", "text_description": "", "responsible_ids": t.data.ids("head_to")}
    return outcome(t.api(role).post("tasks/%d/stages" % task["id"], json=body))


def a_stage_edit_text(t, role):
    task = t.data.task("mont")
    stage = task["stage_details"][0]
    body = {"text_description": "Описание от " + role, "spans": []}
    return outcome(t.api(role).put("tasks/%d/stages/%d" % (task["id"], stage["id"]), json=body))


def a_stage_complete(t, role):
    task = t.data.task("mont")
    stage = task["stage_details"][0]
    done = t.api(role).post("tasks/%d/stages/%d/complete" % (task["id"], stage["id"]))
    if done.status_code == 200:
        back = t.api(role).post("tasks/%d/stages/%d/resume" % (task["id"], stage["id"]))
        if back.status_code != 200:
            t.fail("%s: этап завершён, но возобновить его тем же сотрудником не удалось (%s)" % (role, back.status_code))
            t.api("director").post("tasks/%d/stages/%d/resume" % (task["id"], stage["id"]))
    return outcome(done)


def a_stage_roster(t, role):
    task = t.data.task("mont")
    stage = task["stage_details"][0]
    ids = [p["id"] for p in stage.get("participants") or []]
    return outcome(t.api(role).put("stages/%d/participants" % stage["id"], json=ids))


def contract_task(t, title):
    task = t.data.create_task("director", title, [MONT], responsible=t.data.ids("head_mont"),
                              assigned=t.data.ids("worker_mont"))
    t.data.complete_stage("director", task, 1)
    t.data.extend("director", task, "Договорной отдел", t.data.ids("contract1"))
    return t.data.task_by_id(task["id"])


def a_contract_roster(t, role):
    task = contract_task(t, "Состав договорного этапа " + role)
    stage = t.data.stage(task, 2)
    return outcome(t.api(role).put("stages/%d/participants" % stage["id"], json=t.data.ids("contract1", "contract2")))


def a_contract_edit_text(t, role):
    task = t.data.task("contract")
    stage = t.data.stage(task, 2)
    body = {"text_description": "Договорной этап: " + role, "spans": []}
    return outcome(t.api(role).put("tasks/%d/stages/%d" % (task["id"], stage["id"]), json=body))


def a_contract_extend(t, role):
    task = contract_task(t, "Продление договорной " + role)
    body = {"department_name": MONT, "text_description": "", "responsible_ids": t.data.ids("head_mont")}
    return outcome(t.api(role).post("tasks/%d/stages" % task["id"], json=body))


def a_contract_request(t, role):
    task = contract_task(t, "Завершение " + role)
    return outcome(t.api(role).post("tasks/%d/completion" % task["id"]))


def a_completion_decide(t, role):
    task = contract_task(t, "Решение по завершению " + role)
    t.api("contract1").call("POST", "tasks/%d/completion" % task["id"], expect=201)
    return outcome(t.api(role).post("tasks/%d/completion/decline" % task["id"], json={"comment": "автотест"}))


def mont_image(t):
    if "mont_image" not in t.data.cache:
        task = t.data.task("mont")
        files = t.data.upload("director", ("jpg",), task_id=task["id"])
        t.data.cache["mont_image"] = files[0]
    return t.data.cache["mont_image"]


def a_file_read(t, role):
    item = mont_image(t)
    api = t.api(role)
    first = api.get("task/files/%d/download" % item["id"])
    if first.status_code == 200:
        for path in ("task/files/%d/thumb" % item["id"], "files/%d/view?is_stage=false" % item["id"]):
            extra = api.get(path)
            t.check(extra.status_code == 200, "%s: %s → %s" % (role, path, extra.status_code))
    return outcome(first)


def a_file_upload_task(t, role):
    task = t.data.task("mont")
    name, content, ctype = t.world.media.get("txt")
    response = t.api(role).post("upload", data={"task_id": str(task["id"])},
                                files=[("files", ("task-" + role + ".txt", content, ctype))])
    if response.status_code == 200:
        for item in response.json():
            t.api("director").delete("files/task/%d" % item["id"])
    return outcome(response)


def a_file_upload_stage(t, role):
    task = t.data.task("mont")
    stage = task["stage_details"][0]
    name, content, ctype = t.world.media.get("txt")
    response = t.api(role).post("upload", data={"task_id": str(task["id"]), "stage_detail_id": str(stage["id"])},
                                files=[("files", ("stage-" + role + ".txt", content, ctype))])
    if response.status_code == 200:
        for item in response.json():
            t.api("director").delete("files/stage/%d" % item["id"])
    return outcome(response)


def a_file_upload_archived(t, role):
    task = t.data.task("archived")
    stage = task["stage_details"][0]
    name, content, ctype = t.world.media.get("txt")
    fields = {"task_id": str(task["id"])}
    if t.account(role).department == MONT:
        fields["stage_detail_id"] = str(stage["id"])
    return outcome(t.api(role).post("upload", data=fields, files=[("files", ("archived.txt", content, ctype))]))


def a_file_delete_task(t, role):
    task = t.data.task("mont")
    name, content, ctype = t.world.media.get("txt")
    item = t.api("director").call("POST", "upload", data={"task_id": str(task["id"])},
                                  files=[("files", ("del-" + role + ".txt", content, ctype))])[0]
    response = t.api(role).delete("files/task/%d" % item["id"])
    if response.status_code != 200:
        t.api("director").delete("files/task/%d" % item["id"])
    return outcome(response)


def a_file_delete_stage(t, role):
    task = t.data.task("mont")
    stage = task["stage_details"][0]
    name, content, ctype = t.world.media.get("txt")
    item = t.api("worker_mont").call("POST", "upload", data={"task_id": str(task["id"]), "stage_detail_id": str(stage["id"])},
                                     files=[("files", ("del-stage-" + role + ".txt", content, ctype))])[0]
    response = t.api(role).delete("files/stage/%d" % item["id"])
    if response.status_code != 200:
        t.api("director").delete("files/stage/%d" % item["id"])
    return outcome(response)


def a_file_by_path(t, role):
    item = mont_image(t)
    return outcome(t.api(role).get_file("file_storage/" + item["filepath"]))


def chat_fixture(t):
    if "chat" not in t.data.cache:
        chat = t.data.group_chat("director", ["worker_mont"], "Проверка прав")
        sent = t.data.send_chat_files("director", chat["id"], ("jpg",))
        files = (sent or {}).get("files") or [] if isinstance(sent, dict) else []
        t.data.cache["chat"] = {"chat": chat, "file": files[0] if files else None}
    return t.data.cache["chat"]


def a_chat_by_path(t, role):
    item = chat_fixture(t)["file"]
    t.require(item is not None, "сервер не вернул вложение отправленного сообщения")
    return outcome(t.api(role).get_file("file_storage/" + item["filepath"]))


def a_chat_by_message(t, role):
    item = chat_fixture(t)["file"]
    t.require(item is not None, "сервер не вернул вложение отправленного сообщения")
    return outcome(t.api(role).get_file("chat/download/%d" % item["id"]))


def a_project_create(t, role):
    body = {"name": t.data.name("Проект " + role), "organization": t.data.name("Заказчик"),
            "deadline": t.data.deadline(45), "responsible_id": t.account("head_mont").id}
    response = t.api(role).post("projects", json=body)
    if response.status_code == 201:
        t.data.projects.append(response.json()["id"])
    return outcome(response)


def a_project_view(t, role):
    return outcome(t.api(role).get("projects/%d" % t.data.project("main")["id"]))


def head_project(t):
    if "head_project" not in t.data.cache:
        t.data.cache["head_project"] = t.data.create_project("head_mont", "Проект руководителя монтажа")
    return t.data.cache["head_project"]


def a_project_edit(t, role):
    project = head_project(t)
    return outcome(t.api(role).put("projects/%d" % project["id"], json={"description": "Правка " + role}))


def a_project_delete(t, role):
    project = t.data.create_project("head_mont", "Удаление проекта " + role)
    return outcome(t.api(role).delete("projects/%d" % project["id"]))


def a_project_files(t, role):
    project = t.data.project("main")
    name, content, ctype = t.world.media.get("txt")
    response = t.api(role).post("projects/%d/files" % project["id"], data={"section": "contracts"},
                                files=[("files", ("proj-" + role + ".txt", content, ctype))])
    if response.status_code == 200:
        for item in response.json():
            removed = t.api(role).delete("projects/files/%d" % item["id"])
            t.check(removed.status_code == 200, "%s: файл проекта загружен, но не удаляется (%s)" % (role, removed.status_code))
    return outcome(response)


def warehouse_project(t):
    project = t.data.project("main")
    if "wh_ready" not in t.data.cache:
        t.api("director").call("PATCH", "projects/%d/warehouse/1" % project["id"],
                               json={"fields": ["name", "quantity", "unit"], "name": "Кабель автотеста",
                                     "quantity": 500, "unit": "м"})
        t.data.cache["wh_ready"] = True
    return project


def a_wh_view(t, role):
    return outcome(t.api(role).get("projects/%d/warehouse" % warehouse_project(t)["id"]))


def a_wh_goods(t, role):
    project = warehouse_project(t)
    return outcome(t.api(role).patch("projects/%d/warehouse/2" % project["id"],
                                     json={"fields": ["name"], "name": "Товар от " + role}))


def a_wh_stock(t, role):
    project = warehouse_project(t)
    return outcome(t.api(role).patch("projects/%d/warehouse/1" % project["id"],
                                     json={"fields": ["stock_state"], "stock_state": "enough"}))


def a_wh_issue(t, role):
    project = warehouse_project(t)
    receiver = t.account(other(role)).id
    response = t.api(role).post("projects/%d/warehouse/1/issue" % project["id"],
                                json={"quantity": 1, "receiver_id": receiver})
    if response.status_code in (200, 201):
        issue_id = (response.json() or {}).get("id")
        if issue_id:
            t.api(role).post("warehouse/issues/%d/cancel" % issue_id, json={})
    return outcome(response)


def a_wh_editors(t, role):
    current = t.api("director").call("GET", "warehouse/editors")
    ids = current.get("user_ids") if isinstance(current, dict) else []
    return outcome(t.api(role).put("warehouse/editors", json={"user_ids": ids or []}))


def unit_id_from(body, name):
    for unit in (body or {}).get("units") or []:
        if unit.get("name") == name:
            return unit.get("id")
    return (body or {}).get("id")


def a_wh_units_add(t, role):
    name = "ат" + str(int(time.time() * 1000))[-7:]
    response = t.api(role).post("warehouse/units", json={"name": name, "full_name": "Единица " + role})
    if response.status_code in (200, 201):
        unit_id = unit_id_from(response.json(), name)
        if unit_id:
            t.api("director").delete("warehouse/units/%d" % unit_id)
    return outcome(response)


def a_wh_units_manage(t, role):
    if "unit" not in t.data.cache:
        name = "ед" + str(int(time.time() * 1000))[-6:]
        body = t.api("director").call("POST", "warehouse/units", json={"name": name, "full_name": "Проверочная"})
        t.data.cache["unit"] = {"id": unit_id_from(body, name), "name": name}
    unit = t.data.cache["unit"]
    t.require(unit["id"], "сервер не вернул номер созданной единицы измерения")
    return outcome(t.api(role).patch("warehouse/units/%d" % unit["id"], json={"full_name": "Проверочная " + role}))


def a_eq_view(t, role):
    return outcome(t.api(role).get("equipment"))


def a_eq_edit(t, role):
    api = t.api(role)
    created = api.post("equipment", json={"name": t.data.name("Позиция " + role), "quantity": 3, "unit": "шт"})
    if created.status_code == 201:
        item_id = created.json()["id"]
        t.data.equipment.append(item_id)
        changed = api.patch("equipment/%d" % item_id, json={"fields": ["quantity"], "quantity": 4})
        t.check(changed.status_code == 200, "%s: позиция создана, но не правится (%s)" % (role, changed.status_code))
        removed = api.delete("equipment/%d" % item_id)
        t.check(removed.status_code == 200, "%s: позиция создана, но не удаляется (%s)" % (role, removed.status_code))
    return outcome(created)


def equipment_stock(t):
    if "eq_item" not in t.data.cache:
        t.data.cache["eq_item"] = t.data.equipment_item("Лестница", 200)
    return t.data.cache["eq_item"]


def a_eq_issue(t, role):
    item = equipment_stock(t)
    response = t.api(role).post("equipment/%d/issue" % item["id"],
                                json={"receiver_id": t.account(other(role)).id, "quantity": 1})
    if response.status_code == 201:
        t.api(role).post("equipment/issues/%d/decline" % response.json()["id"])
    return outcome(response)


def a_eq_confirm(t, role):
    item = equipment_stock(t)
    issue = t.api("stock").call("POST", "equipment/%d/issue" % item["id"],
                                json={"receiver_id": t.account("worker_mont").id, "quantity": 1}, expect=201)
    response = t.api(role).post("equipment/issues/%d/confirm" % issue["id"])
    if response.status_code == 200:
        t.api("worker_mont").post("equipment/issues/%d/return" % issue["id"])
        t.api("stock").post("equipment/issues/%d/return/accept" % issue["id"])
    else:
        t.api("stock").post("equipment/issues/%d/decline" % issue["id"])
    return outcome(response)


def a_eq_accept_return(t, role):
    item = equipment_stock(t)
    issue = t.api("stock").call("POST", "equipment/%d/issue" % item["id"],
                                json={"receiver_id": t.account("worker_mont").id, "quantity": 1}, expect=201)
    t.api("worker_mont").call("POST", "equipment/issues/%d/confirm" % issue["id"])
    t.api("worker_mont").call("POST", "equipment/issues/%d/return" % issue["id"])
    response = t.api(role).post("equipment/issues/%d/return/accept" % issue["id"])
    if response.status_code != 200:
        t.api("stock").post("equipment/issues/%d/return/accept" % issue["id"])
    return outcome(response)


def a_org_view(t, role):
    api = t.api(role)
    first = api.get("organizations")
    if first.status_code == 200:
        search = api.get("organizations/search", params={"query": "а"})
        t.check(search.status_code == 200, "%s: поиск организаций ответил %s" % (role, search.status_code))
    return outcome(first)


def a_org_edit(t, role):
    api = t.api(role)
    body = {"name": t.data.name("Организация " + role), "description": "",
            "phones": [], "emails": []}
    created = api.post("organizations", json=body)
    if created.status_code == 201:
        org_id = created.json()["id"]
        changed = api.put("organizations/%d" % org_id, json={"description": "Изменено " + role})
        t.check(changed.status_code == 200, "%s: организация создана, но не правится (%s)" % (role, changed.status_code))
        removed = api.delete("organizations/%d" % org_id)
        t.check(removed.status_code == 200, "%s: организация создана, но не удаляется (%s)" % (role, removed.status_code))
        if removed.status_code != 200:
            t.data.organizations.append(org_id)
    return outcome(created)


def a_tpl_view(t, role):
    return outcome(t.api(role).get("templates"))


def a_tpl_edit(t, role):
    api = t.api(role)
    body = {"template_name": t.data.name("Шаблон " + role), "num_stages": 1, "departments": MONT}
    created = api.post("templates", json=body)
    if created.status_code == 201:
        tpl_id = created.json()["id"]
        changed = api.put("templates/%d" % tpl_id, json=dict(body, template_name=body["template_name"] + " изм."))
        t.check(changed.status_code == 200, "%s: шаблон создан, но не правится (%s)" % (role, changed.status_code))
        removed = api.delete("templates/%d" % tpl_id)
        t.check(removed.status_code == 200, "%s: шаблон создан, но не удаляется (%s)" % (role, removed.status_code))
    return outcome(created)


def a_emp_view(t, role):
    api = t.api(role)
    first = api.get("employees")
    if first.status_code == 200:
        card = api.get("employees/%d" % t.account("worker_to").id)
        t.check(card.status_code == 200, "%s: карточка сотрудника ответила %s" % (role, card.status_code))
    return outcome(first)


def temp_employee_payload(t, role):
    login = "attmp%s%s" % (int(time.time() * 1000) % 10 ** 8, role[:4])
    return {"username": login, "password": "Autotest-Temp-1!", "last_name": "Автотест",
            "first_name": "Временный " + role, "middle_name": "tmp", "position": "Монтажник",
            "status": "работник", "department": MONT, "phone_number": "+7 (900) 111-11-11",
            "email": login + "@kovapp-autotest.ru"}


def a_emp_create(t, role):
    response = t.api(role).post("employees", json=temp_employee_payload(t, role))
    if response.status_code == 200:
        t.api("admin").delete("employees/%d" % response.json()["id"])
    return outcome(response)


def a_emp_edit_other(t, role):
    target = t.account("worker_to")
    return outcome(t.api(role).put("employees/%d" % target.id, json={"profile": "Правка карточки от " + role}))


def a_emp_own_contacts(t, role):
    api = t.api(role)
    me = api.call("GET", "employees/%d" % api.user_id)
    return outcome(api.put("employees/%d" % api.user_id,
                           json={"phone_number": me.get("phone_number") or "", "email": me.get("email") or ""}))


def a_emp_own_position(t, role):
    api = t.api(role)
    me = api.call("GET", "employees/%d" % api.user_id)
    return outcome(api.put("employees/%d" % api.user_id, json={"position": me.get("position") or "Сотрудник"}))


def a_emp_photo_own(t, role):
    api = t.api(role)
    name, content, ctype = t.world.media.get("png")
    return outcome(api.post("employees/%d/photo" % api.user_id, files=[("file", (name, content, ctype))]))


def a_emp_photo_other(t, role):
    name, content, ctype = t.world.media.get("png")
    return outcome(t.api(role).post("employees/%d/photo" % t.account(other(role, "worker_to", "head_to")).id,
                                    files=[("file", (name, content, ctype))]))


def a_emp_delete(t, role):
    created = t.api("admin").call("POST", "employees", json=temp_employee_payload(t, role))
    response = t.api(role).delete("employees/%d" % created["id"])
    if response.status_code != 200:
        t.api("admin").delete("employees/%d" % created["id"])
    return outcome(response)


def a_notif_own(t, role):
    api = t.api(role)
    return outcome(api.get("employees/%d/notifications" % api.user_id))


def a_notif_others(t, role):
    return outcome(t.api(role).get("employees/%d/notifications" % t.account(other(role, "worker_to", "head_to")).id))


def a_notif_send_any(t, role):
    target = t.account(other(role, "worker_to", "head_to")).id
    return outcome(t.api(role).post("notifications", json={"user_ids": [target], "message": "Автотест: проверка рассылки",
                                                           "target_section": "notifications"}))


def a_notif_send_task(t, role):
    task = t.data.task("mont")
    target = t.account(other(role, "worker_mont2", "worker_mont")).id
    return outcome(t.api(role).post("notifications", json={"user_ids": [target], "message": "Автотест: действие в задаче",
                                                           "target_section": "task_view", "target_id": task["id"]}))


def a_csv_export(t, role):
    api = t.api(role)
    first = api.get("csv/users/export")
    if first.status_code == 200:
        second = api.get("csv/organizations/export")
        t.check(second.status_code == 200, "%s: выгрузка организаций ответила %s" % (role, second.status_code))
    return outcome(first)


def a_health(t, role):
    return outcome(t.api(role).get("health/file-access"))


def a_chat_group_info(t, role):
    return outcome(t.api(role).get("chat/group/%d" % chat_fixture(t)["chat"]["id"]))


def a_chat_history(t, role):
    return outcome(t.api(role).get("chat/history/%d" % chat_fixture(t)["chat"]["id"]))


def a_chat_upload(t, role):
    name, content, ctype = t.world.media.get("txt")
    return outcome(t.api(role).post("chat/upload", data={"chat_id": str(chat_fixture(t)["chat"]["id"])},
                                    files=[("file", ("chat-" + role + ".txt", content, ctype))]))


def a_chat_avatar(t, role):
    name, content, ctype = t.world.media.get("png")
    return outcome(t.api(role).post("chat/upload", data={"chat_id": str(chat_fixture(t)["chat"]["id"]), "is_avatar": "true"},
                                    files=[("file", (name, content, ctype))]))


def a_chat_add_members(t, role):
    chat_id = chat_fixture(t)["chat"]["id"]
    target = t.account("contract2").id
    response = t.api(role).post("chat/group/%d/participants" % chat_id, json={"user_ids": [target]})
    if response.status_code == 200:
        t.api("director").delete("chat/group/%d/participants/%d" % (chat_id, target))
    return outcome(response)


def a_chat_private_admin(t, role):
    return outcome(t.api(role).post("chat/private", json={"target_user_id": t.account("admin").id}))


def a_upload_foreign_session(t, role):
    if "session" not in t.data.cache:
        t.data.cache["session"] = t.api("director").call("POST", "uploads/sessions",
                                                          json={"name": "foreign.bin", "size": 10, "kind": "file"})
    return outcome(t.api(role).get("uploads/sessions/%s" % t.data.cache["session"]["id"]))


def a_upload_foreign_token(t, role):
    import json
    import os
    task = t.data.task("mont")
    stage = task["stage_details"][0]
    if "dedup_data" not in t.data.cache:
        data = os.urandom(300 * 1024)
        t.api("director").call("POST", "upload", data={"task_id": str(task["id"])},
                               files=[("files", ("perm-dedup.bin", data, "application/octet-stream"))])
        from tests.files import tree_hash
        t.data.cache["dedup_data"] = (tree_hash(data), len(data))
    tree, size = t.data.cache["dedup_data"]
    director = t.api("director")
    challenge = director.call("POST", "uploads/dedup/check", json={"tree": tree, "size": size})
    token = director.call("POST", "uploads/dedup/answer", json={"id": challenge["id"], "hashes": []}).get("token")
    t.require(token, "директор не получил разрешение на повтор своего файла")
    meta = json.dumps([{"src": "dedup", "token": token, "name": "perm-dedup-%s.bin" % role}])
    fields = {"task_id": str(task["id"]), "meta": meta}
    if t.account(role).department == MONT and role not in ("director", "admin"):
        fields["stage_detail_id"] = str(stage["id"])
    response = t.api(role).post("upload", data=fields)
    if response.status_code == 200:
        for item in response.json():
            t.api("director").delete("files/%s/%d" % ("stage" if "stage_detail_id" in fields else "task", item["id"]))
    return outcome(response)


def a_chat_send_files(t, role):
    name, content, ctype = t.world.media.get("txt")
    response = t.api(role).post("chat/grouped-message", data={"chat_id": str(chat_fixture(t)["chat"]["id"])},
                                files=[("files", ("send-" + role + ".txt", content, ctype))])
    return outcome(response)


# ===== правила, добавленные по permissions.md (раздел за разделом) =====

def a_emp_delete_self(t, role):
    """Удалить самого себя нельзя никому (руководству — 400, остальным — 403)."""
    return outcome(t.api(role).delete("employees/%d" % t.account(role).id))


def a_wh_issue_self(t, role):
    """Выдать товар самому себе нельзя: выдачу подтверждает получатель."""
    project = warehouse_project(t)
    response = t.api(role).post("projects/%d/warehouse/1/issue" % project["id"],
                                json={"quantity": 1, "receiver_id": t.account(role).id})
    if response.status_code in (200, 201) and (response.json() or {}).get("id"):
        t.api(role).post("warehouse/issues/%d/cancel" % response.json()["id"], json={})
    return outcome(response)


def wh_issue_to_worker(t):
    project = warehouse_project(t)
    return t.api("stock").call("POST", "projects/%d/warehouse/1/issue" % project["id"],
                               json={"quantity": 1, "receiver_id": t.account("worker_mont").id})


def a_wh_issue_confirm(t, role):
    """Подтвердить получение товара: получатель или руководство."""
    issue = wh_issue_to_worker(t)
    response = t.api(role).post("warehouse/issues/%d/confirm" % issue["id"], json={})
    if response.status_code != 200:
        t.api("stock").post("warehouse/issues/%d/cancel" % issue["id"], json={})
    return outcome(response)


def a_wh_issue_cancel(t, role):
    """Отменить неподтверждённую выдачу: тот, кто выдал, получатель, руководство."""
    issue = wh_issue_to_worker(t)
    response = t.api(role).post("warehouse/issues/%d/cancel" % issue["id"], json={})
    if response.status_code != 200:
        t.api("stock").post("warehouse/issues/%d/cancel" % issue["id"], json={})
    return outcome(response)


def eq_issue_to_worker(t):
    item = equipment_stock(t)
    return t.api("stock").call("POST", "equipment/%d/issue" % item["id"],
                               json={"receiver_id": t.account("worker_mont").id, "quantity": 1}, expect=201)


def a_eq_decline(t, role):
    """Отказаться от выдачи оборудования: получатель, тот, кто выдал, руководство."""
    issue = eq_issue_to_worker(t)
    response = t.api(role).post("equipment/issues/%d/decline" % issue["id"])
    if response.status_code != 200:
        t.api("stock").post("equipment/issues/%d/decline" % issue["id"])
    return outcome(response)


def a_eq_return(t, role):
    """Вернуть оборудование может только получатель."""
    issue = eq_issue_to_worker(t)
    t.api("worker_mont").call("POST", "equipment/issues/%d/confirm" % issue["id"])
    response = t.api(role).post("equipment/issues/%d/return" % issue["id"])
    if response.status_code != 200:
        t.api("worker_mont").post("equipment/issues/%d/return" % issue["id"])
    t.api("stock").post("equipment/issues/%d/return/accept" % issue["id"])
    return outcome(response)


def a_completion_approve(t, role):
    """Подтвердить завершение задачи: руководство (задача уходит в архив)."""
    task = contract_task(t, "Подтверждение завершения " + role)
    t.api("contract1").call("POST", "tasks/%d/completion" % task["id"], expect=201)
    return outcome(t.api(role).post("tasks/%d/completion/approve" % task["id"], json={"comment": "автотест"}))


def completed_stage_task(t):
    if "completed_stage_task" not in t.data.cache:
        task = t.data.create_task("director", "Завершённый этап", [MONT], responsible=t.data.ids("head_mont"),
                                  assigned=t.data.ids("worker_mont"))
        t.data.complete_stage("director", task, 1)
        t.data.cache["completed_stage_task"] = t.data.task_by_id(task["id"])
    return t.data.cache["completed_stage_task"]


def a_file_stage_completed(t, role):
    """В завершённый этап файлы не добавляются никем (409 — «возобновите этап»)."""
    task = completed_stage_task(t)
    stage = task["stage_details"][0]
    name, content, ctype = t.world.media.get("txt")
    response = t.api(role).post("upload", data={"task_id": str(task["id"]), "stage_detail_id": str(stage["id"])},
                                files=[("files", ("completed-" + role + ".txt", content, ctype))])
    if response.status_code == 200:
        for item in response.json():
            t.api("director").delete("files/stage/%d" % item["id"])
    return outcome(response)


def archived_task_file(t):
    if "archived_file" not in t.data.cache:
        task = t.data.create_task("director", "Файл в архиве", [MONT], responsible=t.data.ids("head_mont"),
                                  assigned=t.data.ids("worker_mont"))
        name, content, ctype = t.world.media.get("txt")
        item = t.api("director").call("POST", "upload", data={"task_id": str(task["id"])},
                                      files=[("files", ("archived-file.txt", content, ctype))])[0]
        t.api("director").call("POST", "tasks/%d/archive" % task["id"])
        t.data.cache["archived_file"] = item
    return t.data.cache["archived_file"]


def a_file_delete_archived(t, role):
    """В задаче в архиве файлы не удаляются никем."""
    item = archived_task_file(t)
    return outcome(t.api(role).delete("files/task/%d" % item["id"]))


def a_chat_promote(t, role):
    """Назначить администратора группы может только её создатель."""
    chat_id = chat_fixture(t)["chat"]["id"]
    target = t.account("worker_mont").id
    response = t.api(role).post("chat/group/%d/promote" % chat_id, json={"user_id": target})
    if response.status_code == 200:
        t.api("director").post("chat/group/%d/demote" % chat_id, json={"user_id": target})
    return outcome(response)


ACTIONS = {
    "task.create_own": a_task_create_own, "task.create_foreign": a_task_create_foreign,
    "task.create_two_stages": a_task_create_two, "task.create_from_template": a_task_from_template,
    "task.view": a_task_view, "task.history": a_task_history, "task.edit": a_task_edit,
    "task.edit_multi": a_task_edit_multi, "task.edit_foreign_stage": a_task_edit_foreign_stage,
    "task.archive": a_task_archive, "task.delete": a_task_delete, "task.extend": a_task_extend,
    "stage.edit_text": a_stage_edit_text, "stage.complete": a_stage_complete, "stage.roster": a_stage_roster,
    "contract.roster": a_contract_roster, "contract.edit_text": a_contract_edit_text,
    "contract.extend": a_contract_extend, "contract.request_completion": a_contract_request,
    "completion.decide": a_completion_decide,
    "file.read": a_file_read, "file.upload_task": a_file_upload_task, "file.upload_stage": a_file_upload_stage,
    "file.upload_archived": a_file_upload_archived, "file.delete_task": a_file_delete_task,
    "file.delete_stage": a_file_delete_stage, "file.by_path": a_file_by_path,
    "file.chat_by_path": a_chat_by_path, "file.chat_by_message": a_chat_by_message,
    "project.create": a_project_create, "project.view": a_project_view, "project.edit": a_project_edit,
    "project.delete": a_project_delete, "project.files": a_project_files,
    "wh.view": a_wh_view, "wh.edit_goods": a_wh_goods, "wh.edit_stock": a_wh_stock, "wh.issue": a_wh_issue,
    "wh.editors": a_wh_editors, "wh.units_add": a_wh_units_add, "wh.units_manage": a_wh_units_manage,
    "eq.view": a_eq_view, "eq.edit": a_eq_edit, "eq.issue": a_eq_issue, "eq.confirm": a_eq_confirm,
    "eq.accept_return": a_eq_accept_return,
    "org.view": a_org_view, "org.edit": a_org_edit, "tpl.view": a_tpl_view, "tpl.edit": a_tpl_edit,
    "emp.view": a_emp_view, "emp.create": a_emp_create, "emp.edit_other": a_emp_edit_other,
    "emp.own_contacts": a_emp_own_contacts, "emp.own_position": a_emp_own_position,
    "emp.photo_own": a_emp_photo_own, "emp.photo_other": a_emp_photo_other, "emp.delete": a_emp_delete,
    "notif.own": a_notif_own, "notif.others": a_notif_others, "notif.send_any": a_notif_send_any,
    "notif.send_task_members": a_notif_send_task, "csv.export": a_csv_export, "health.file_access": a_health,
    "chat.group_info": a_chat_group_info, "chat.history": a_chat_history, "chat.upload": a_chat_upload,
    "chat.avatar": a_chat_avatar, "chat.add_members": a_chat_add_members,
    "chat.private_with_admin": a_chat_private_admin,
    "upload.foreign_session": a_upload_foreign_session, "upload.foreign_token": a_upload_foreign_token,
    "chat.send_files": a_chat_send_files,
    "emp.delete_self": a_emp_delete_self, "wh.issue_self": a_wh_issue_self,
    "wh.issue_confirm": a_wh_issue_confirm, "wh.issue_cancel": a_wh_issue_cancel,
    "eq.decline": a_eq_decline, "eq.return": a_eq_return, "completion.approve": a_completion_approve,
    "file.stage_completed": a_file_stage_completed, "file.delete_archived": a_file_delete_archived,
    "chat.promote": a_chat_promote,
}

FILE_PATH_ACTIONS = ("file.by_path", "file.chat_by_path", "file.chat_by_message")


# ===== проверка одного действия для всех ролей =====

def check_action(t, key):
    spec = MATRIX["actions"][key]
    attempt = ACTIONS[key]
    roles = [r for r in expand(["all"]) if r not in (spec.get("skip") or [])]
    allowed = set(expand(spec.get("allow")))
    soft = key in FILE_PATH_ACTIONS and t.world.facts.get("file_mode") == "soft"
    if soft:
        t.warn("сервер в мягком режиме FILE_ACCESS_STRICT=0: посторонние получат файл, отказ только записывается в журнал")
        allowed = set(roles)
    deny_codes = set(spec.get("deny_codes") or [401, 403])
    ok_codes = set(spec.get("ok_codes") or [200, 201])
    table = []
    for role in roles:
        try:
            result = attempt(t, role)
        except ApiError as e:
            t.fail("%s: подготовка проверки не удалась — %s" % (role, e))
            continue
        if result.status in ok_codes:
            verdict = "разрешено"
        elif result.status in deny_codes:
            verdict = "запрещено"
        else:
            t.fail("%s: неожиданный ответ %s%s" % (role, result.status, (" — " + result.detail) if result.detail else ""))
            continue
        table.append("%-13s %-9s %s" % (role, verdict, result.status))
        if role in allowed and verdict != "разрешено":
            t.fail("%s: должно быть разрешено, сервер отказал (%s%s)" % (
                role, result.status, (": " + result.detail) if result.detail else ""))
        if role not in allowed and verdict != "запрещено":
            t.fail("%s: должно быть запрещено, сервер разрешил (%s) — лишняя возможность" % (role, result.status))
    t.log.info("права «%s»:\n%s" % (spec["title"], "\n".join(table)))


def register(key, order):
    spec = MATRIX["actions"][key]

    @case("%s (%s)" % (spec["title"], key), layer="api", order=order)
    def _check(t, _key=key):
        check_action(t, _key)
    _check.__name__ = "perm_" + key.replace(".", "_")
    return _check


for _index, _key in enumerate(MATRIX["actions"]):
    if _key in ACTIONS:
        register(_key, _index)


@case("Матрица прав покрывает все проверки", layer="api", order=-1)
def matrix_is_complete(t):
    missing = [k for k in MATRIX["actions"] if k not in ACTIONS]
    unused = [k for k in ACTIONS if k not in MATRIX["actions"]]
    t.check(not missing, "в permissions.yaml есть действия без проверки: " + ", ".join(missing))
    t.check(not unused, "проверки без строки в permissions.yaml: " + ", ".join(unused))
    for key, spec in MATRIX["actions"].items():
        for name in spec.get("allow") or []:
            t.check(name in MATRIX["groups"] or name in expand(["all"]),
                    "%s: неизвестная роль или группа «%s»" % (key, name))
