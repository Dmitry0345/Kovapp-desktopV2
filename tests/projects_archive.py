"""Проекты: завершение, «Активные / Архивные», только чтение, задачи проекта, файлы этапов, срок при создании (проект v59+)."""
from datetime import date, timedelta

from kovtest import case

LOCKED = 409   # ответ сервера на изменение в завершённом проекте


def _ids(items):
    return {x["id"] for x in (items or [])}


def _fails_with(t, call, status, what):
    """Вызов должен упасть с этим кодом: помощники модуля бросают исключение на неожиданный ответ."""
    try:
        call()
    except Exception as e:   # noqa: BLE001 — текст ошибки содержит код ответа
        t.check(str(status) in str(e), "%s: ожидали ответ %d, получили: %s" % (what, status, str(e)[:200]))
        return
    t.check(False, "%s: сервер принял запрос, а должен был отказать (%d)" % (what, status))


@case("Проект: завершить нельзя, пока есть незавершённые задачи; завершает создатель, директор, администратор", layer="api", order=1)
def complete_rules(t):
    head = t.api("head_to")
    project = t.data.create_project("head_to", "Завершение: правила")
    pid = project["id"]
    t.data.create_task("head_to", "Незавершённая задача", ["Отдел ТО"], responsible=t.data.ids("head_to"), project_id=pid)
    fresh = head.call("GET", "projects/%d" % pid)
    t.check(fresh.get("can_complete") is True, "создатель проекта не может его завершать (can_complete)")
    t.check((fresh.get("tasks_unfinished") or 0) >= 1, "не посчитаны незавершённые задачи (tasks_unfinished)")
    t.expect_status(head.post("projects/%d/complete" % pid), 400, "завершение при незавершённой задаче")
    t.expect_status(t.api("worker_to").post("projects/%d/complete" % pid), 403, "завершение сотрудником (не создатель и не директор)")
    t.check(head.call("GET", "projects/%d" % pid).get("is_archived") is False, "проект завершился, хотя не должен был")


@case("Проект: завершение — «Архивные», кто и когда, только чтение, возврат в работу", layer="api", order=2)
def complete_flow(t):
    director = t.api("director")
    project = t.data.create_project("director", "Завершение: весь путь")
    pid = project["id"]
    task = t.data.create_task("director", "Задача, которую завершим", ["Отдел ТО"], responsible=t.data.ids("head_to"), project_id=pid)
    t.data.complete_stage("head_to", task, 1)
    if t.data.task_by_id(task["id"]).get("activity") != "Архивная":
        director.call("POST", "tasks/%d/archive" % task["id"])
    t.check(t.data.task_by_id(task["id"]).get("activity") == "Архивная", "задача не ушла в архив — завершать проект нечем")
    done = director.call("POST", "projects/%d/complete" % pid)
    t.check(done.get("is_archived") is True, "проект не завершился")
    t.check((done.get("archived_by") or {}).get("id") == director.user_id, "не сохранено, кто завершил проект (archived_by)")
    t.check(bool(done.get("archived_at")), "не сохранено, когда завершён проект (archived_at)")
    t.check(pid in _ids(director.call("GET", "projects", params={"is_archived": "true"})), "завершённого проекта нет в «Архивных»")
    t.check(pid not in _ids(director.call("GET", "projects")), "завершённый проект остался в «Активных»")
    t.check(done.get("can_edit") is False and done.get("can_upload") is False, "у завершённого проекта остались права правки и отправки файлов")

    # только чтение: всё, что меняет проект, его задачи, файлы и склад
    t.expect_status(director.put("projects/%d" % pid, json={"description": "правка завершённого"}), LOCKED, "правка завершённого проекта")
    t.expect_status(director.post("projects/%d/files" % pid, data={"section": "contracts"}), LOCKED, "файл в раздел завершённого проекта")
    t.expect_status(director.patch("projects/%d/warehouse/1" % pid, json={"field": "name", "value": "x"}), LOCKED, "правка склада завершённого проекта")
    t.expect_status(director.put("tasks/%d" % task["id"], json={"name": "правка"}), LOCKED, "правка задачи завершённого проекта")
    t.expect_status(director.post("tasks/%d/unarchive" % task["id"]), LOCKED, "возврат задачи завершённого проекта в работу")
    t.expect_status(director.delete("tasks/%d" % task["id"]), LOCKED, "удаление задачи завершённого проекта")
    t.data.upload("director", ("jpg",), task_id=task["id"], expect=(LOCKED,))
    _fails_with(t, lambda: t.data.create_task("director", "Новая задача в завершённом", ["Отдел ТО"], responsible=t.data.ids("head_to"), project_id=pid),
                LOCKED, "новая задача в завершённом проекте")
    access = director.call("GET", "projects/%d/warehouse" % pid).get("access") or {}
    t.check(not any(v for k, v in access.items() if k.startswith("can_")), "склад завершённого проекта можно править: %s" % access)

    # вернуть в работу — те же люди; запись о завершении остаётся
    t.expect_status(t.api("worker_to").post("projects/%d/reopen" % pid), 403, "возврат проекта в работу сотрудником")
    back = director.call("POST", "projects/%d/reopen" % pid)
    t.check(back.get("is_archived") is False and pid in _ids(director.call("GET", "projects")), "проект не вернулся в «Активные»")
    t.check((back.get("archived_by") or {}).get("id") == director.user_id, "после возврата в работу пропала запись, кто завершал")
    t.expect_status(director.put("projects/%d" % pid, json={"description": "снова в работе"}), 200, "правка проекта после возврата в работу")


@case("Проект: в списке видны все задачи, недоступные помечены и не открываются", layer="api", order=3)
def all_tasks_visible(t):
    project = t.data.create_project("head_to", "Задачи проекта: видимость")
    task = t.data.create_task("head_to", "Задача только отдела ТО", ["Отдел ТО"], responsible=t.data.ids("head_to"), project_id=project["id"])
    viewer = t.api("head_mont")   # видит проект в любом режиме доступа, но в задаче не участвует
    rows = viewer.call("GET", "projects/%d/tasks" % project["id"])
    row = next((x for x in rows if x["id"] == task["id"]), None)
    t.check(row is not None, "задача проекта не видна в списке тому, кто в ней не участвует")
    t.check(row is not None and row.get("can_open") is False, "недоступная задача не помечена can_open=false: %s" % (row or {}).get("can_open"))
    t.expect_status(viewer.get("tasks/%d" % task["id"]), 403, "открыть недоступную задачу")
    own = t.api("head_to").call("GET", "projects/%d/tasks" % project["id"])
    t.check(any(x["id"] == task["id"] and x.get("can_open") is True for x in own), "своя задача помечена как недоступная")


@case("Медиаконтент и файлы этапа: этап другого отдела закрыт", layer="api", order=4)
def stage_files_by_department(t):
    project = t.data.create_project("director", "Медиаконтент: доступ по этапам")
    task = t.data.create_task("director", "Задача двух отделов", ["Отдел ТО", "Монтажный отдел"],
                              responsible=t.data.ids("head_to") + t.data.ids("head_mont"),
                              assigned=t.data.ids("worker_to") + t.data.ids("worker_mont"), project_id=project["id"])
    stage_to, stage_mont = t.data.stage(task, 1), t.data.stage(task, 2)
    to_file = t.data.upload("head_to", ("jpg",), task_id=task["id"], stage_id=stage_to["id"])[0]
    mont_file = t.data.upload("head_mont", ("jpg",), task_id=task["id"], stage_id=stage_mont["id"])[0]
    worker = t.api("worker_mont")
    folders = worker.call("GET", "projects/%d/photos" % project["id"])
    seen = {f["id"] for folder in folders for f in (folder.get("files") or [])}
    t.check(to_file["id"] not in seen, "монтажник видит в медиаконтенте файл этапа отдела ТО")
    t.check(mont_file["id"] in seen, "монтажник не видит в медиаконтенте файл своего этапа")
    t.expect_status(worker.get("stages/files/%d/thumb" % to_file["id"]), 403, "миниатюра файла этапа другого отдела по прямой ссылке")
    t.expect_status(worker.get("stages/files/%d/thumb" % mont_file["id"]), 200, "миниатюра файла своего этапа")
    t.expect_status(t.api("director").get("stages/files/%d/thumb" % to_file["id"]), 200, "руководство видит файлы всех этапов")


@case("Срок при создании задачи и проекта — не раньше сегодняшнего дня", layer="api", order=5)
def deadline_not_past(t):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    t.expect_status(t.api("director").post("projects", json={"name": "Проект со сроком в прошлом", "deadline": yesterday}), 400,
                    "проект со сроком в прошлом")
    original = t.data.deadline
    t.data.deadline = lambda days=30: yesterday
    try:
        _fails_with(t, lambda: t.data.create_task("director", "Задача со сроком в прошлом", ["Отдел ТО"], responsible=t.data.ids("head_to")),
                    400, "задача со сроком в прошлом")
    finally:
        t.data.deadline = original
