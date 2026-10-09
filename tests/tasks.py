import time
from datetime import date, timedelta

from kovtest import case
from kovtest.flows import hash_of, row_exists, segmented, select_row, wait_for


# ===== сервер =====

@case("Задача видна своему составу и не видна посторонним", layer="api", order=1)
def list_visibility(t):
    task = t.data.task("mont")
    for role in ("head_mont", "worker_mont", "director"):
        ids = [x["id"] for x in t.api(role).call("GET", "tasks", params={"is_archived": "false"})]
        t.check(task["id"] in ids, "%s не видит свою задачу в списке" % role)
    for role in ("head_to", "worker_buh", "contract2"):
        ids = [x["id"] for x in t.api(role).call("GET", "tasks", params={"is_archived": "false"})]
        t.check(task["id"] not in ids, "%s видит чужую задачу в списке" % role)


@case("Участники этапа входят в состав задачи", layer="api", order=2)
def stage_members_synced(t):
    task = t.data.task("mont")
    assigned = {p["id"] for p in task.get("assigned_employees") or []}
    for stage in task["stage_details"]:
        for person in stage.get("participants") or []:
            t.check(person["id"] in assigned, "участник этапа %s не числится в задаче" % person["id"])


@case("Архив: задача уходит в архив и возвращается", layer="api", order=3)
def archive_cycle(t):
    task = t.data.create_task("head_mont", "Архивирование", ["Монтажный отдел"], responsible=t.data.ids("head_mont"))
    api = t.api("head_mont")
    api.call("POST", "tasks/%d/archive" % task["id"])
    archived = [x["id"] for x in api.call("GET", "tasks", params={"is_archived": "true"})]
    t.check(task["id"] in archived, "задача не попала в архив")
    api.call("POST", "tasks/%d/unarchive" % task["id"])
    active = [x["id"] for x in api.call("GET", "tasks", params={"is_archived": "false"})]
    t.check(task["id"] in active, "задача не вернулась из архива")


@case("Журнал задачи и фильтры списка", layer="api", order=4)
def history_and_filters(t):
    task = t.data.task("mont")
    history = t.api("head_mont").call("GET", "tasks/%d/history" % task["id"])
    t.check(isinstance(history, list), "журнал задачи — не список")
    flt = t.api("worker_mont").call("GET", "task-filters/false", params={"force_create": "true"})
    t.check(isinstance(flt, dict) and "sort_by" in flt, "фильтр списка задач не получен")


STATE_NAMES = ["FIRST", "SECOND", "THIRD", "FOURTH", "FIFTH", "SIXTH", "SEVENTH", "EIGHTH", "NINTH", "TENTH"]


def _stage_no(task):
    s = str(task.get("state") or "")
    if s.isdigit():
        return int(s)
    s = s.split(".")[-1].upper()
    return STATE_NAMES.index(s) + 1 if s in STATE_NAMES else 0


def _rank(task, department, today):
    """Группа задачи в порядке «по важности»: 0–2 — сейчас этап моего отдела, 3–5 — нет;
    внутри — просрочена, срок сегодня, остальные."""
    no = _stage_no(task)
    cur = next((s for s in task.get("stage_details") or [] if s.get("stage_number") == no), None)
    mine = bool(cur and cur.get("department_name") == department and not cur.get("is_completed"))
    dl = task.get("deadline")
    when = 0 if dl and dl < today else 1 if dl == today else 2
    return (0 if mine else 3) + when


@case("Список задач по умолчанию: сначала этап своего отдела, затем сроки", layer="api", order=5)
def default_priority_order(t):
    role = "worker_mont"
    api = t.api(role)
    department = t.account(role).department
    flt = api.call("POST", "task-filters/false/reset")
    # по умолчанию явной сортировки нет — так сервер отдаёт работающие задачи по важности
    t.check(flt.get("sort_by") in (None, "", "priority"), "после сброса осталась сортировка %s" % flt.get("sort_by"))
    t.check(api.call("GET", "task-filters/false/is-default") is True, "после сброса фильтр не считается исходным")
    opts = api.call("GET", "task-filters/false").get("sort_options") or []
    t.check("priority" in opts, "в вариантах сортировки нет «по важности»: %s" % opts)
    day = lambda n: (date.today() + timedelta(days=n)).isoformat()   # noqa: E731
    mont, other = department, "Отдел ТО"
    who = dict(responsible=t.data.ids("head_mont", "head_to"), assigned=t.data.ids(role, "worker_to"))
    # по одной задаче на группу; ожидаемый порядок — как в списке ниже
    made = []
    for title, deps, n in (
            ("Порядок: свой этап, просрочена", [mont], -3),
            ("Порядок: свой этап, срок сегодня", [mont], 0),
            ("Порядок: свой этап, срок не скоро", [mont], 20),
            ("Порядок: этап другого отдела, просрочена", [other, mont], -2),
            ("Порядок: этап другого отдела, срок сегодня", [other, mont], 0),
            ("Порядок: этап другого отдела, срок не скоро", [other, mont], 15)):
        # прошедший срок при создании сервер не принимает — просроченной задача становится правкой срока
        task = t.data.create_task("director", title, deps, deadline=day(max(n, 0)), **who)
        if n < 0:
            t.api("director").call("PUT", "tasks/%d" % task["id"], json={"deadline": day(n)})
        made.append(task)
    rows = api.call("GET", "tasks", params={"is_archived": "false"})
    ids = [x["id"] for x in rows]
    pos = [ids.index(m["id"]) if m["id"] in ids else -1 for m in made]
    t.require(-1 not in pos, "монтажник не видит созданные задачи: %s" % pos)
    t.check(pos == sorted(pos), "созданные задачи идут не по важности: места %s" % pos)

    def ordered(today):
        ranks = [_rank(x, department, today) for x in rows]
        if ranks != sorted(ranks):
            return False
        for a, b in zip(rows, rows[1:]):          # внутри группы — по сроку, без срока — в конце
            if _rank(a, department, today) == _rank(b, department, today) and a.get("deadline") and b.get("deadline") \
                    and a["deadline"] > b["deadline"]:
                return False
        return True
    # дата сервера может отличаться на сутки (часовой пояс внешнего сервера)
    t.check(any(ordered(day(k)) for k in (0, 1, -1)),
            "порядок списка нарушен: %s" % [(x["id"], _rank(x, department, day(0)), x.get("deadline")) for x in rows][:12])
    # явная сортировка по-прежнему работает
    api.call("PUT", "task-filters/false", json={"sort_by": "id_desc"})
    desc = [x["id"] for x in api.call("GET", "tasks", params={"is_archived": "false"})]
    t.check(desc == sorted(desc, reverse=True), "сортировка «сначала новые» не применилась")
    api.call("POST", "task-filters/false/reset")


# ===== интерфейс =====

@case("Список задач: своя задача видна, чужая — нет", layer="ui", matrix={"device": "devices"}, order=10)
def list_ui(t):
    task = t.data.task("mont")
    page = t.page("worker_mont", "light", t.params["device"], path="#/tasks")
    t.check(wait_for(page, "k => !!document.querySelector(\"table.data tr.row[data-key='\" + k + \"']\")", 10, str(task["id"])),
            "своя задача не показана в списке")
    other = t.page("head_to", "light", t.params["device"], path="#/tasks", browser="B")
    t.check(not row_exists(other, task["id"]), "чужая задача видна руководителю ТО")
    page.assert_clean("список задач")


@case("Кнопка «Новая задача» только у руководителей и руководства", layer="ui",
      matrix={"role": ["director", "head_mont", "head_buh", "worker_mont", "contract1", "stock"]}, order=11)
def new_task_button(t):
    role = t.params["role"]
    page = t.page(role, "light", "desktop", path="#/tasks")
    present = page.visible_button("Новая задача") is not None
    allowed = role in ("director", "head_mont", "head_buh")
    t.check(present == allowed, "«Новая задача» у роли %s: %s" % (role, present))


@case("Форма задачи руководителя: один этап своего отдела, шаблоны под замком", layer="ui",
      matrix={"device": "devices"}, order=12)
def head_task_form(t):
    page = t.page("head_mont", "light", t.params["device"], path="#/tasks/new")
    raw = page.raw
    count = raw.locator(".content input[type=number]").first
    t.check(count.is_disabled(), "руководитель может менять число этапов")
    t.check(count.input_value() == "1", "число этапов у руководителя не 1: %s" % count.input_value())
    options = raw.eval_on_selector_all(".content select option, .content .stage-select option",
                                       "els => [...new Set(els.map(e => e.textContent.trim()))]")
    foreign = [o for o in options if o in ("Отдел ТО", "Бухгалтерия", "Договорной отдел")]
    t.check(not foreign, "в выборе отдела у руководителя есть чужие отделы: %s" % foreign)
    page.audit("task_form_head")
    raw.locator(".content button", has_text="Шаблоны задач").click()
    raw.wait_for_selector(".modal-root .modal")
    locked = raw.locator(".modal-root .modal-foot button.is-locked").count()
    t.check(locked >= 2, "кнопки управления шаблонами у руководителя не заперты")
    page.audit("templates_dialog_head")
    page.close_modal()


@case("Создание задачи руководителем через форму", layer="ui", order=13)
def create_task_ui(t):
    page = t.page("head_mont", "light", "desktop", path="#/tasks/new")
    raw = page.raw
    title = t.data.name("Задача из формы %d" % int(time.time()))
    raw.locator(".content input.input").first.fill(title)
    raw.fill(".content input[type=date]", t.data.deadline())
    raw.locator(".content button", has_text="Сохранить").click()
    t.require(wait_for(page, "() => /^#\\/tasks\\/\\d+$/.test(location.hash)", 15), "после сохранения окно задачи не открылось")
    task_id = int(hash_of(page).split("/")[-1])
    t.data.tasks.append(task_id)
    task = t.api("head_mont").call("GET", "tasks/%d" % task_id)
    t.check([s["department_name"] for s in task["stage_details"]] == ["Монтажный отдел"],
            "у новой задачи неверные этапы: %s" % [s["department_name"] for s in task["stage_details"]])
    page.wait_ready()
    page.assert_clean("создание задачи")


@case("Чужая задача: правка и удаление под замком", layer="ui", order=14)
def foreign_task_locked(t):
    task = t.data.task("multi")
    page = t.page("head_mont", "light", "desktop", path="#/tasks")
    t.require(wait_for(page, "k => !!document.querySelector(\"table.data tr.row[data-key='\" + k + \"']\")", 10, str(task["id"])),
              "задача двух отделов не показана руководителю монтажа")
    select_row(page, task["id"])
    for label in ("Редактировать", "Удалить"):
        button = page.visible_button(label)
        t.require(button is not None, "нет кнопки «%s»" % label)
        t.check(page.is_locked(button), "кнопка «%s» у чужой задачи не заперта" % label)
    page.visible_button("Удалить").click()
    page.raw.wait_for_timeout(400)
    t.check(page.raw.locator(".modal-root").count() == 0, "запертая кнопка «Удалить» открыла подтверждение")
    own = t.data.task("mont")
    select_row(page, own["id"])
    t.check(not page.is_locked(page.visible_button("Редактировать")), "у своей задачи правка заперта")


@case("Фильтры и архив в списке задач", layer="ui", matrix={"device": "devices"}, order=15)
def filters_and_archive(t):
    archived = t.data.task("archived")
    page = t.page("director", "light", t.params["device"], path="#/tasks")
    page.raw.locator(".content button", has_text="Фильтры").click()
    if t.params["device"] == "desktop":
        t.check(wait_for(page, "() => !!document.querySelector('.modal-root .modal')", 5), "окно фильтров не открылось")
    else:   # телефон: «Сортировка и фильтры» — шторкой снизу
        t.check(wait_for(page, "() => !!document.querySelector('.kov-sheet-root .bottom-sheet')", 5), "шторка фильтров не открылась")
    page.audit("tasks_filters")
    page.close_overlays()
    segmented(page, "archived")
    page.wait_ready()
    t.check(wait_for(page, "k => !!document.querySelector(\"table.data tr.row[data-key='\" + k + \"']\")", 10, str(archived["id"])),
            "архивная задача не показана во вкладке «Архивные»")
    page.audit("tasks_archive")


@case("Список задач не перерисовывается, пока в нём ничего не изменилось", layer="ui",
      matrix={"engine": "engines"}, order=40)
def list_stays_still(t):
    task = t.data.create_task("director", "Неподвижный список", ["Монтажный отдел"], responsible=t.data.ids("head_mont"))
    page = t.page("director", "light", "desktop", path="#/tasks")
    t.require(wait_for(page, "() => document.querySelectorAll('.table-wrap tbody tr').length > 0", 20), "список задач не показан")
    page.raw.wait_for_timeout(1500)
    page.raw.evaluate("() => document.querySelectorAll('.table-wrap tbody tr').forEach((r, i) => { r.dataset.still = String(i); })")
    name, data, ctype = t.world.media.fresh("pdf5")
    t.api("director").call("POST", "upload", data={"task_id": str(task["id"])}, files=[("files", (name, data, ctype))])
    page.raw.wait_for_timeout(3000)
    kept = page.raw.evaluate("() => { const rows = [...document.querySelectorAll('.table-wrap tbody tr')]; return [rows.length, rows.filter(r => r.dataset.still !== undefined).length]; }")
    t.check(kept[0] > 0 and kept[0] == kept[1], "после загрузки файла в задачу список перерисовался: строк %d, из них прежних %d" % tuple(kept))
    page.assert_clean("неподвижный список задач")
