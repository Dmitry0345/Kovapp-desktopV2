"""Задачи на настоящем Android — касаниями, как у сотрудника: создание, карточка, этапы, правка, удаление.

На сенсорном экране одно касание строки открывает задачу, а удержание — выделяет её
(для «Изменить» и «Удалить»); тесты делают именно так.
"""
import time

from kovtest import case
from kovtest.device_views import open_stage_desc

ROW = "table.data tr.row[data-key='%s']"
STATE = {}                                     # задача, созданная через форму, — для правки и удаления


def dev(t):
    if t.world.device is None:
        t.skip("только для тестов на устройстве (run.cmd device)")
    t.world.device.ensure_app(t)
    return t.world.device


def has_button(d, text: str, timeout: float = 10) -> bool:
    return d.wait_js("[...document.querySelectorAll('button')].some(b => b.textContent.includes(%r))" % text, timeout)


def confirm_if_asked(d) -> None:
    if d.wait_js("document.querySelector('.modal-root .modal')", 3):
        d.click_text("", within=".modal-root .modal-foot", last=True)


@case("Создание задачи через форму на телефоне", layer="device", order=1)
def create_task(t):
    d = dev(t)
    d.login("head_mont", "#/tasks")
    t.require(d.click_text("Новая задача"), "в списке задач нет кнопки «Новая задача»")
    t.require(d.wait_js("document.querySelector('.content input.input')", 15), "форма задачи не открылась")
    d.audit(t, "device_task_form")
    title = t.data.name("Задача с телефона %d" % (int(time.time()) % 100000))
    d.fill(".content input.input", title)
    t.require(d.wait_js("document.querySelector('.content input[type=date]')", 15), "в форме нет поля «Дата окончания»")
    d.set_value(".content input[type=date]", t.data.deadline())
    t.require(d.click_text("Сохранить", last=True), "в форме нет кнопки «Сохранить»")
    where = d.wait_hash(r"^#/tasks/\d+$", 20)
    t.require(where, "после сохранения карточка задачи не открылась (адрес: %s)" % d.js("return location.hash;"))
    task_id = int(where.split("/")[-1])
    t.data.tasks.append(task_id)
    STATE["task"] = task_id
    task = t.api("head_mont").call("GET", "tasks/%d" % task_id)
    t.check(task.get("name") == title, "на сервере название «%s», а вводили «%s»" % (task.get("name"), title))
    departments = [s["department_name"] for s in task.get("stage_details") or []]
    t.check(departments == ["Монтажный отдел"], "у новой задачи неверные этапы: %s" % departments)
    t.require(d.wait_js("document.querySelector('.content')", 10), "карточка задачи пустая")
    d.audit(t, "device_task_created")


@case("Карточка задачи у сотрудника: этапы, описание, завершение и возобновление этапа", layer="device", order=2)
def task_card(t):
    d = dev(t)
    task = t.data.create_task("director", "Карточка на телефоне", ["Монтажный отдел"],
                              responsible=t.data.ids("head_mont"), assigned=t.data.ids("worker_mont"))
    d.login("worker_mont", "#/tasks/%d" % task["id"])
    t.require(d.wait_js("document.querySelector('.stage-nav button[data-stage], .content .stage-actions')", 20),
              "карточка задачи не открылась")
    # на телефоне название — в заголовке сверху (.title-chip), а не в самой карточке
    shown = d.js("return ((document.querySelector('.title-chip') || {}).textContent || '') + ' ' + document.body.innerText;") or ""
    t.check(task["name"] in shown, "на экране нет названия задачи «%s»" % task["name"])
    d.audit(t, "device_task_card")
    # описание этапа сохраняется само, без кнопки; на телефоне поле открывает окно письма («Готово»)
    t.require(open_stage_desc(d), "в карточке нет вкладки описания этапа")
    t.require(d.wait_js("document.querySelector('.desc-input')", 10), "поле описания этапа не открылось")
    text = "Описание с телефона %d" % (int(time.time()) % 100000)
    d.fill(".desc-input", text)
    shown = d.js("return (document.querySelector('.desc-input') || {}).value || '';") or ""
    t.check(text in shown, "введённый текст не попал в поле описания этапа")
    saved, deadline = "", time.time() + 10
    while time.time() < deadline:
        saved = t.api("worker_mont").call("GET", "tasks/%d/stages/%d" % (task["id"], task["stage_details"][0]["id"])).get(
            "text_description") or ""
        if text in saved:
            break
        time.sleep(1)
    t.check(text in saved, "описание этапа не сохранилось на сервере")
    # завершить и возобновить этап
    t.require(d.click_text("Завершить этап"), "у сотрудника отдела нет кнопки «Завершить этап»")
    confirm_if_asked(d)
    t.check(has_button(d, "Возобновить этап"), "после завершения не появилась кнопка «Возобновить этап»")
    state = t.api("worker_mont").call("GET", "tasks/%d" % task["id"])
    t.check(state["stage_details"][0]["is_completed"], "этап не завершён на сервере")
    t.require(d.click_text("Возобновить этап"), "нет кнопки «Возобновить этап»")
    confirm_if_asked(d)
    t.check(has_button(d, "Завершить этап"), "этап не возобновился")
    state = t.api("worker_mont").call("GET", "tasks/%d" % task["id"])
    t.check(not state["stage_details"][0]["is_completed"], "на сервере этап остался завершённым")


@case("Список задач: касание строки открывает задачу", layer="device", order=3)
def tap_opens(t):
    d = dev(t)
    task_id = STATE.get("task") or t.data.create_task("head_mont", "Открытие касанием", ["Монтажный отдел"],
                                                      responsible=t.data.ids("head_mont"))["id"]
    d.login("head_mont", "#/tasks")
    t.require(d.click(ROW % task_id), "задачи нет в списке")
    t.check(d.wait_hash(r"^#/tasks/%d$" % task_id, 10), "касание строки не открыло задачу (адрес: %s)"
            % d.js("return location.hash;"))


@case("Правка задачи на телефоне: удержание строки → «Изменить» → новое название", layer="device", order=4)
def edit_task(t):
    d = dev(t)
    task_id = STATE.get("task") or t.data.create_task("head_mont", "Правка на телефоне", ["Монтажный отдел"],
                                                      responsible=t.data.ids("head_mont"))["id"]
    d.login("head_mont", "#/tasks")
    t.require(d.long_press(ROW % task_id), "задачи нет в списке")
    t.require(d.wait_js("document.querySelector(%r).classList.contains('selected')" % (ROW % task_id), 5),
              "удержание не выделило строку")
    t.require(d.button_enabled("Редактировать") or d.button_enabled("Изменить"), "после выделения «Изменить» заперта")
    t.require(d.click_text("Редактировать"), "нет кнопки «Изменить»")
    t.require(d.wait_hash(r"^#/tasks/%d/edit$" % task_id, 10), "форма правки не открылась")
    t.require(d.wait_js("document.querySelector('.content input.input')", 15), "в форме правки нет поля названия")
    title = t.data.name("Задача исправлена %d" % (int(time.time()) % 100000))
    d.fill(".content input.input", title)
    t.require(d.click_text("Сохранить", last=True), "в форме правки нет кнопки «Сохранить»")
    deadline, name = time.time() + 15, ""
    while time.time() < deadline:
        name = t.api("head_mont").call("GET", "tasks/%d" % task_id).get("name")
        if name == title:
            break
        time.sleep(1)
    t.check(name == title, "после правки на сервере название «%s», а вводили «%s»" % (name, title))


@case("Удаление задачи на телефоне: удержание → «Удалить» → подтверждение", layer="device", order=5)
def delete_task(t):
    d = dev(t)
    task_id = STATE.get("task") or t.data.create_task("head_mont", "Удаление на телефоне", ["Монтажный отдел"],
                                                      responsible=t.data.ids("head_mont"))["id"]
    d.login("head_mont", "#/tasks")
    t.require(d.long_press(ROW % task_id), "задачи нет в списке")
    t.require(d.wait_js("document.querySelector(%r).classList.contains('selected')" % (ROW % task_id), 5),
              "удержание не выделило строку")
    t.require(d.click_text("Удалить"), "нет кнопки «Удалить»")
    t.require(d.confirm("Удалить"), "не появилось подтверждение удаления")
    t.check(d.wait_js("!document.querySelector(%r)" % (ROW % task_id), 10), "задача осталась в списке")
    ids = [x["id"] for x in t.api("head_mont").call("GET", "tasks", params={"is_archived": "false"})]
    ids += [x["id"] for x in t.api("head_mont").call("GET", "tasks", params={"is_archived": "true"})]
    t.check(task_id not in ids, "задача осталась на сервере")
    if task_id not in ids and task_id in t.data.tasks:
        t.data.tasks.remove(task_id)
    STATE.pop("task", None)
