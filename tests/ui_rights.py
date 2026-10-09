from kovtest import case
from kovtest.config import load_yaml
from kovtest.flows import hash_of

RIGHTS = load_yaml("ui_rights.yaml")["checks"]
MATRIX = load_yaml("permissions.yaml")


# ===== подготовка =====

def expand(names):
    out = []
    for name in names or []:
        for role in MATRIX["groups"].get(name, [name]):
            if role not in out:
                out.append(role)
    return out


def done_task(t):
    if "ui_task_done" not in t.data.cache:
        task = t.data.create_task("director", "Кнопки завершённой задачи", ["Монтажный отдел"],
                                  responsible=t.data.ids("head_mont"), assigned=t.data.ids("worker_mont"))
        t.data.complete_stage("director", task, 1)
        t.data.cache["ui_task_done"] = task
    return t.data.cache["ui_task_done"]


def open_task(t):
    if "ui_task_open" not in t.data.cache:
        t.data.cache["ui_task_open"] = t.data.create_task("director", "Кнопки задачи в работе", ["Монтажный отдел"],
                                                          responsible=t.data.ids("head_mont"),
                                                          assigned=t.data.ids("worker_mont"))
    return t.data.cache["ui_task_open"]


def route_for(t, spec):
    route = spec["route"]
    if "{project}" in route:
        route = route.replace("{project}", str(t.data.project("main")["id"]))
    if "{task_done}" in route:
        route = route.replace("{task_done}", str(done_task(t)["id"]))
    if "{task_open}" in route:
        route = route.replace("{task_open}", str(open_task(t)["id"]))
    if spec.get("needs") == "equipment" and "ui_eq" not in t.data.cache:
        t.data.cache["ui_eq"] = t.data.equipment_item("Кнопки оборудования", 4)
    return route


def state_of(page, spec):
    if spec.get("zone"):
        zone = page.raw.locator(".content [data-key='%s']" % spec["zone"])
        if zone.count():
            zone.first.click()
            page.raw.wait_for_timeout(400)
    if spec.get("before"):
        target = page.raw.locator(".content " + spec["before"])
        if target.count():
            target.first.click()
            page.raw.wait_for_timeout(300)
    labels = spec["button"] if isinstance(spec["button"], list) else [spec["button"]]
    button = None
    for label in labels:
        button = page.visible_button(label, ".content")
        if button is not None:
            break
    if button is None:
        return "нет кнопки"
    if page.is_locked(button) or button.is_disabled():
        return "под замком"
    return "доступна"


# ===== проверка =====

def check(t, key):
    spec = RIGHTS[key]
    allowed = set(expand(spec.get("allow")))
    table = []
    for role in expand(["all"]):
        route = route_for(t, spec)
        page = t.page(role, "light", "desktop", path=route)
        state = state_of(page, spec)
        table.append("%-13s %s (%s)" % (role, state, hash_of(page)))
        if role in allowed and state != "доступна":
            t.fail("%s: %s — %s, а должна быть доступна" % (role, spec["title"], state))
        if role not in allowed and state == "доступна":
            t.fail("%s: %s доступна, а не должна — лишняя возможность в интерфейсе" % (role, spec["title"]))
        t.world.browsers.release(page)
        t.pages.remove(page)
    t.log.info("кнопки «%s»:\n%s" % (spec["title"], "\n".join(table)))


def register(key, order):
    @case("%s по ролям (%s)" % (RIGHTS[key]["title"], key), layer="ui", order=order)
    def _check(t, _key=key):
        check(t, _key)
    return _check


for _index, _key in enumerate(RIGHTS):
    register(_key, _index)
