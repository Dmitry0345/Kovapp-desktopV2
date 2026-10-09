import time

from kovtest import case
from kovtest.flows import hash_of, upload_through_ui, wait_for, wait_tile


# ===== сервер =====

@case("Проект: создание, правка, задачи, разделы файлов, удаление", layer="api", order=1)
def project_api(t):
    api = t.api("head_to")
    project = t.data.create_project("head_to", "Проект отдела ТО")
    t.check(project.get("responsible_id") == api.user_id, "руководитель не стал ответственным своего проекта")
    t.check(project.get("can_edit") is True, "создатель не может править свой проект")
    changed = api.call("PUT", "projects/%d" % project["id"], json={"description": "Изменено автотестом"})
    t.check(changed.get("description") == "Изменено автотестом", "описание проекта не изменилось")
    task = t.data.create_task("head_to", "Задача проекта ТО", ["Отдел ТО"], responsible=t.data.ids("head_to"),
                              project_id=project["id"])
    tasks = api.call("GET", "projects/%d/tasks" % project["id"])
    t.check(any(x["id"] == task["id"] for x in tasks), "задача не видна в проекте")
    for section in ("contracts", "project", "delivery", "extra"):
        items = t.data.project_upload("head_to", project["id"], section, ("pdf",))
        listed = api.call("GET", "projects/%d/files" % project["id"], params={"section": section})
        t.check(any(x["id"] == items[0]["id"] for x in listed), "файл раздела %s не показан" % section)
        t.expect_status(api.get("projects/files/%d/download" % items[0]["id"]), 200, "скачивание файла раздела " + section)
        t.expect_status(api.get("projects/files/%d/view" % items[0]["id"]), 200, "просмотр PDF раздела " + section)
    t.expect_status(api.get("projects/%d/files" % project["id"], params={"section": "wrong"}), 400, "несуществующий раздел")
    heads = api.call("GET", "projects/heads")
    t.check(isinstance(heads, list) and heads, "список руководителей для столбцов пуст")
    t.data.delete_task(task["id"])
    t.expect_status(api.delete("projects/%d" % project["id"]), 200, "удаление проекта")


@case("Удаление проекта вместе с задачами", layer="api", order=2)
def delete_with_tasks(t):
    project = t.data.create_project("director", "Проект с задачами на удаление", "head_mont")
    task = t.data.create_task("director", "Задача удаляемого проекта", ["Монтажный отдел"],
                              responsible=t.data.ids("head_mont"), project_id=project["id"])
    t.data.upload("director", ("txt",), task_id=task["id"])
    t.expect_status(t.api("director").delete("projects/%d" % project["id"]), 200, "удаление проекта с задачами")
    t.expect_status(t.api("director").get("tasks/%d" % task["id"]), 404, "задача удалённого проекта")


# ===== интерфейс =====

@case("Кнопка «Создать проект» по ролям", layer="ui",
      matrix={"role": ["director", "head_buh", "contract1", "worker_buh", "stock"]}, order=10)
def create_button(t):
    role = t.params["role"]
    page = t.page(role, "light", "desktop", path="#/projects")
    present = page.visible_button("Создать проект") is not None
    allowed = role in ("director", "head_buh", "contract1")
    t.check(present == allowed, "«Создать проект» у роли %s: %s" % (role, present))


@case("Договорной отдел создаёт проект через форму", layer="ui", matrix={"device": "devices"}, order=11)
def create_project_ui(t):
    page = t.page("contract1", "light", t.params["device"], path="#/projects")
    raw = page.raw
    raw.locator(".content button", has_text="Создать проект").click()
    raw.wait_for_selector("input[placeholder='Название проекта']")
    title = t.data.name("Проект из формы %s %d" % (t.params["device"], int(time.time())))
    raw.fill("input[placeholder='Название проекта']", title)
    page.audit("project_form")
    raw.locator("button", has_text="Сохранить").last.click()
    t.check(wait_for(page, "() => !document.querySelector(\"input[placeholder='Название проекта']\")", 15),
            "форма проекта не закрылась после сохранения")
    projects = t.api("contract1").call("GET", "projects")
    created = [p for p in projects if p["name"] == title]
    t.require(created, "проект не появился на сервере")
    t.data.projects.append(created[0]["id"])
    page.assert_clean("создание проекта")


@case("Проект: разделы, загрузка договора, склад", layer="ui", matrix={"device": "devices"}, order=12)
def project_sections_ui(t):
    project = t.data.project("main")
    page = t.page("director", "light", t.params["device"], path="#/projects/%d" % project["id"])
    cards = page.raw.eval_on_selector_all(".section-card", "els => els.map(e => e.innerText.split('\\n')[0].trim())")
    for title in ("Договоры", "Медиаконтент", "Проект", "Сдача", "Склад"):
        t.check(any(title in c for c in cards), "нет раздела «%s» в проекте (есть: %s)" % (title, cards))
    page.audit("project_view")
    page.goto("#/projects/%d/contracts" % project["id"])
    name, data, ctype = t.world.media.get("pdf")
    stamp = "договор-%s-%d.pdf" % (t.params["device"], int(time.time()) % 100000)
    t.require(upload_through_ui(page, [(stamp, data, ctype)]), "загрузка договора не завершилась")
    t.check(wait_tile(page, stamp[:-4]), "загруженный договор не показан")
    page.audit("project_contracts")
    page.goto("#/projects/%d/warehouse" % project["id"])
    t.check(wait_for(page, "() => !!document.querySelector('.content table, .content .wh-card, .content .wh-table')", 15),
            "склад проекта не открылся")
    page.audit("project_warehouse")
    page.assert_clean("разделы проекта")
