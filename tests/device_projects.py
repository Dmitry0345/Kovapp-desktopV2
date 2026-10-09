"""Проекты на настоящем Android — касаниями: создание, правка, разделы и склад, удаление."""
import json
import time

from kovtest import case

STATE = {}


def dev(t):
    if t.world.device is None:
        t.skip("только для тестов на устройстве (run.cmd device)")
    t.world.device.ensure_app(t)
    return t.world.device


CARD = ("[...document.querySelectorAll('.project-card, table.data td')]"
        ".find(e => (e.textContent || '').includes(arguments[0]))")


def select_project(d, name: str) -> bool:
    """Выделить проект удержанием (касание его открывает) — как это делает сотрудник."""
    already = d.js("return [...document.querySelectorAll('.project-card.on, table.data td.selected')]"
                   ".some(e => (e.textContent || '').includes(arguments[0]));", name)
    if not already:
        rect = d._rect(CARD, name)
        if not rect:
            return False
        d.hold(rect)
    return d.wait_js("[...document.querySelectorAll('.content button')].some(b => "
                     "(b.textContent || '').includes('Редактировать') && !b.disabled)", 5)


def wait_project(t, role: str, name: str, present: bool = True, timeout: float = 15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = [p for p in t.api(role).call("GET", "projects") if p["name"] == name]
        if bool(found) == present:
            return found[0] if found else True
        time.sleep(1)
    return None


@case("Создание проекта через форму на телефоне", layer="device", order=1)
def create_project(t):
    d = dev(t)
    d.login("contract1", "#/projects")
    t.require(d.click_text("Создать проект"), "у договорного отдела нет кнопки «Создать проект»")
    t.require(d.wait_js("document.querySelector(\"input[placeholder='Название проекта']\")", 15), "форма проекта не открылась")
    d.audit(t, "device_project_form")
    title = t.data.name("Проект с телефона %d" % (int(time.time()) % 100000))
    d.fill("input[placeholder='Название проекта']", title)
    t.require(d.click_text("Сохранить", last=True), "в форме нет кнопки «Сохранить»")
    t.check(d.wait_js("!document.querySelector(\"input[placeholder='Название проекта']\")", 15),
            "форма проекта не закрылась после сохранения")
    project = wait_project(t, "contract1", title)
    t.require(project, "проект не появился на сервере")
    t.data.projects.append(project["id"])
    STATE["project"] = (project["id"], title)
    t.check(d.wait_js("document.querySelector('.content').innerText.includes(%r)" % title, 10), "нового проекта нет в списке")
    d.audit(t, "device_projects_list")


@case("Список проектов на телефоне: касание открывает проект, удержание — выделяет", layer="device", order=2)
def tap_opens_project(t):
    d = dev(t)
    project = t.data.project("main")
    d.login("director", "#/projects")
    t.require(d.wait_js("document.querySelector('.project-card, table.data td.project-cell')", 15), "список проектов не открылся")
    rect = d._rect(CARD, project["name"])
    t.require(rect, "проекта «%s» нет в списке" % project["name"])
    d.hold(rect)
    time.sleep(0.8)
    t.check(d.js("return location.hash;") == "#/projects", "удержание открыло проект, а должно было выделить")
    t.check(d.js("return !!document.querySelector('.project-card.on, table.data td.selected');"), "удержание не выделило проект")
    d._tap(d._rect(CARD, project["name"]))
    t.check(d.wait_hash(r"^#/projects/%d$" % project["id"], 10), "касание не открыло проект (адрес: %s)"
            % d.js("return location.hash;"))


@case("Правка проекта на телефоне: удержание → «Редактировать» → новое название", layer="device", order=3)
def edit_project(t):
    d = dev(t)
    if "project" not in STATE:
        p = t.data.create_project("contract1", "Правка проекта на телефоне")
        STATE["project"] = (p["id"], p["name"])
    project_id, name = STATE["project"]
    d.login("contract1", "#/projects")
    t.require(select_project(d, name), "проекта «%s» нет в списке" % name)
    t.require(d.button_enabled("Редактировать"), "после выбора проекта «Редактировать» заперта")
    t.require(d.click_text("Редактировать"), "нет кнопки «Редактировать»")
    t.require(d.wait_js("document.querySelector(\"input[placeholder='Название проекта']\")", 10), "форма правки проекта не открылась")
    title = t.data.name("Проект исправлен %d" % (int(time.time()) % 100000))
    d.fill("input[placeholder='Название проекта']", title)
    t.require(d.click_text("Сохранить", last=True), "в форме нет кнопки «Сохранить»")
    t.check(wait_project(t, "contract1", title), "после правки на сервере нет проекта «%s»" % title)
    STATE["project"] = (project_id, title)


@case("Проект на телефоне: вкладка «Файлы проекта», разделы и склад", layer="device", order=4)
def project_sections(t):
    d = dev(t)
    project = t.data.project("main")
    d.login("director", "#/projects/%d" % project["id"])
    t.require(d.wait_js("document.querySelector('.project-layout')", 20), "страница проекта не открылась")
    d.audit(t, "device_project_tasks_tab")
    # на телефоне колонки не помещаются рядом: разделы — на вкладке «Файлы проекта»
    tabs = d.js("const e = document.querySelector('.project-tabs'); return !!e && e.getBoundingClientRect().height > 0;")
    if tabs:
        t.require(d.click(".project-tabs [data-key='files']"), "нет вкладки «Файлы проекта»")
    visible = """return [...document.querySelectorAll('.section-card')].filter(e => {
        const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; })
        .map(e => e.innerText.split('\\n')[0].trim());"""
    t.require(d.wait_js("[...document.querySelectorAll('.section-card')].some(e => e.getBoundingClientRect().height > 0)", 10),
              "разделы проекта не видны на экране")
    cards = d.js(visible) or []
    for title in ("Договоры", "Медиаконтент", "Проект", "Сдача", "Склад"):
        t.check(any(title in c for c in cards), "раздел «%s» не виден на экране (видны: %s)" % (title, cards))
    d.audit(t, "device_project_files_tab")
    # на телефоне каждый раздел — широкая строка, поэтому «Склад» ищется по названию
    t.require(d.click_text("Склад", within=".project-right"), "карточка «Склад» не видна на экране")
    t.require(d.wait_hash(r"^#/projects/%d/warehouse$" % project["id"], 10), "касание «Склад» не открыло склад (адрес: %s)"
              % d.js("return location.hash;"))
    t.check(d.wait_js("document.querySelector('.content .wh-card, .content .wh-table')", 20), "склад проекта не загрузился")
    d.audit(t, "device_project_warehouse")


@case("Удаление проекта на телефоне: удержание → «Удалить» → подтверждение", layer="device", order=5)
def delete_project(t):
    d = dev(t)
    if "project" not in STATE:
        p = t.data.create_project("contract1", "Удаление проекта на телефоне")
        STATE["project"] = (p["id"], p["name"])
    project_id, name = STATE["project"]
    d.login("contract1", "#/projects")
    t.require(select_project(d, name), "проекта «%s» нет в списке" % name)
    t.require(d.button_enabled("Удалить"), "после выбора проекта «Удалить» заперта")
    t.require(d.click_text("Удалить"), "нет кнопки «Удалить»")
    t.require(d.confirm("Удалить"), "не появилось подтверждение удаления")
    t.check(wait_project(t, "contract1", name, present=False), "проект остался на сервере")
    t.check(d.wait_js("!document.querySelector('.content').innerText.includes(%r)" % name, 10), "проект остался в списке")
    if project_id in t.data.projects:
        t.data.projects.remove(project_id)
    STATE.pop("project", None)


@case("Фильтр проектов на телефоне: поиск организации в списке и выбор — остаются только её проекты",
      layer="device", order=6)
def filter_by_organization(t):
    d = dev(t)
    stamp = "%05d" % (int(time.time()) % 100000)
    org = t.data.name("Организация для фильтра " + stamp)
    project = t.api("director").call("POST", "projects", json={
        "name": t.data.name("Проект для фильтра " + stamp), "organization": org,
        "deadline": t.data.deadline(30), "description": "Проверка фильтра"}, expect=201)
    t.data.projects.append(project["id"])
    d.login("director", "#/projects")
    picker = "button.picker[title='Организация']"
    # телефон: фильтры — в шторке «Сортировка и фильтры», выбор организации — своей шторкой с поиском;
    # планшет и компьютер — фильтры в строке, список выпадает под кнопкой
    touch = bool(d.wait_js("document.querySelector('button.filter-btn')", 10))
    panel = ".picker-sheet-root .picker-sheet" if touch else ".picker-panel:not([hidden])"

    def open_filters():
        if touch and not d.js("const b = document.querySelector(arguments[0]); return !!b && b.getBoundingClientRect().height > 0;", picker):
            d.click("button.filter-btn")

    open_filters()
    t.require(d.wait_js("document.querySelector(%s)" % json.dumps(picker), 15), "в фильтрах нет выбора организации с поиском")

    def choose(text):
        open_filters()
        t.require(d.click(picker), "фильтр «Организация» не нажимается")
        t.require(d.wait_js("document.querySelector(%s)" % json.dumps(panel + " .picker-search input"), 5),
                  "у фильтра нет поля поиска")
        d.fill(panel + " .picker-search input", text)
        time.sleep(0.4)
        return d.click_text(text, within=panel, timeout=5)

    t.later(lambda: choose("Все организации"))      # выбор фильтра запоминается — вернуть как было
    t.require(d.click(picker), "фильтр «Организация» не нажимается")
    t.require(d.wait_js("document.querySelector(%s)" % json.dumps(panel), 5), "список организаций не открылся")
    edges = d.js("const r = document.querySelector(arguments[0]).getBoundingClientRect(); return [r.left, r.right, innerWidth];", panel)
    t.check(edges[0] >= 0 and edges[1] <= edges[2], "список организаций выходит за край экрана: %s" % edges)
    d.fill(panel + " .picker-search input", stamp)
    time.sleep(0.4)
    found = d.js("return [...document.querySelectorAll(arguments[0] + ' .picker-row .t')].map(e => e.textContent);", panel)
    t.check(found == [org], "поиск по «%s» нашёл %s, а должен — только «%s»" % (stamp, found, org))
    t.require(d.click_text(org, within=panel, timeout=5), "найденную организацию не выбрать касанием")
    if touch:      # шторку фильтров закрываем, как человек: «×»
        d.js("const x = [...document.querySelectorAll('.kov-sheet-root .kov-sheet-x')].pop(); if (x) x.click(); return true;")
    only = ("[...document.querySelectorAll('.project-card, table.data td.project-cell')].length > 0 && "
            "[...document.querySelectorAll('.project-card, table.data td.project-cell')].every(e => e.textContent.includes(%s))")
    t.check(d.wait_js(only % json.dumps(project["name"]), 10), "после выбора организации в списке не только её проект")
