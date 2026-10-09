from kovtest import case
from kovtest.config import load_yaml
from kovtest.flows import hash_of, wait_for

UI = load_yaml("ui.yaml")
ACCESS = UI["access"]


def expected_menu(role):
    keys = [m["key"] for m in UI["menu"]]
    if role in ACCESS["projects_hidden_for"]:
        keys.remove("projects")
    if role not in ACCESS["csv_only"]:
        keys.remove("csv")
    return keys


# ===== меню =====

@case("Меню показывает ровно разделы своей роли", layer="ui", matrix={"role": "roles:all"}, order=1)
def menu_by_role(t):
    role = t.params["role"]
    page = t.page(role, "light", "desktop", path="#/tasks")
    got = page.menu_keys()
    want = expected_menu(role)
    t.check(got == want, "пункты меню %s, ожидались %s" % (got, want))


@case("Переход по каждому пункту меню", layer="ui", matrix={"device": "devices"}, order=2)
def menu_navigation(t):
    page = t.page("director", "light", t.params["device"], path="#/tasks")
    for item in UI["menu"]:
        page.menu(item["key"])
        landed = hash_of(page)
        t.check(landed.startswith("#/" + item["key"]), "пункт «%s» открыл %s" % (item["label"], landed))
        if item["key"] == "profile":
            t.check(wait_for(page, "() => !!document.querySelector('.side-panel.open')", 5), "профиль не выехал")
            page.raw.keyboard.press("Escape")
            page.raw.wait_for_timeout(400)
        else:
            title = page.text(".title-chip")
            t.check(title, "у раздела «%s» пустой заголовок" % item["label"])
    page.assert_clean("навигация по меню")


@case("Меню открывается и закрывается кнопкой, фоном и Escape", layer="ui", matrix={"device": "devices"}, order=3)
def menu_open_close(t):
    page = t.page("worker_mont", "light", t.params["device"], path="#/tasks")
    raw = page.raw
    raw.click(".menu-btn")
    t.check(wait_for(page, "() => !!document.querySelector('.side-menu.open')", 3), "меню не открылось")
    page.audit("menu_open")
    raw.keyboard.press("Escape")
    t.check(wait_for(page, "() => !document.querySelector('.side-menu.open')", 3), "Escape не закрыл меню")
    raw.click(".menu-btn")
    wait_for(page, "() => !!document.querySelector('.side-menu.open')", 3)
    spot = raw.evaluate("""() => { const m = document.querySelector('.side-menu.open').getBoundingClientRect();
        return { x: Math.min(innerWidth - 8, Math.max(m.right + 12, innerWidth - 20)), y: Math.round(innerHeight / 2) }; }""")
    raw.mouse.click(spot["x"], spot["y"])
    t.check(wait_for(page, "() => !document.querySelector('.side-menu.open')", 3), "нажатие мимо меню его не закрыло")
    raw.click(".menu-btn")
    wait_for(page, "() => !!document.querySelector('.side-menu.open')", 3)
    covered = raw.evaluate("""() => { const b = document.querySelector('.menu-btn').getBoundingClientRect();
        const hit = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
        return !(hit && hit.closest('.menu-btn')); }""")
    if covered:
        t.log.info("открытое меню закрывает кнопку меню — повторное нажатие на неё невозможно, закрываем Escape")
        raw.keyboard.press("Escape")
    else:
        raw.click(".menu-btn")
        t.check(wait_for(page, "() => !document.querySelector('.side-menu.open')", 3), "повторное нажатие на кнопку меню его не закрыло")


@case("Колокольчик ведёт в уведомления, недоступный раздел — на задачи", layer="ui", order=4)
def bell_and_guards(t):
    page = t.page("worker_mont", "light", "desktop", path="#/tasks")
    page.raw.click(".bell-btn")
    page.wait_ready()
    t.check(hash_of(page).startswith("#/notifications"), "колокольчик открыл " + hash_of(page))
    page.goto("#/projects")
    t.check(hash_of(page).startswith("#/tasks"), "монтажнику открылся раздел «Проекты»: " + hash_of(page))
    page.goto("#/csv")
    t.check(hash_of(page).startswith("#/tasks"), "не руководству открылся раздел «Файлы CSV»: " + hash_of(page))
