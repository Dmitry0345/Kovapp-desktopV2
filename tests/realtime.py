import time
from urllib.parse import urlparse

from kovtest import SkipCase, case
from kovtest.flows import tile_thumb_painted, wait_for, wait_tile


def engines(t):
    names = t.settings.browsers
    return names[0], names[1 % len(names)]


def live(page):
    if not wait_for(page, "() => !!(window.Kov && Kov.WS && Kov.WS.connected)", 15):
        page.t.warn("соединение обновлений у %s не установилось за 15 с" % page.who)
    page.raw.wait_for_timeout(700)
    return page


def pair_page(t, role, theme, device, path, browser):
    first, second = engines(t)
    engine = first if browser == "A" else second
    try:
        return live(t.page(role, theme, device, path=path, browser=browser, engine=engine))
    except SkipCase as e:
        if engine == first:
            raise
        t.warn("второй браузер %s недоступен (%s) — оба пользователя в %s" % (engine, e, first))
        return live(t.page(role, theme, device, path=path, browser=browser, engine=first))


def seen(page, js, arg, timeout):
    started = time.time()
    ok = wait_for(page, js, timeout, arg)
    return ok, time.time() - started


def report(t, ok, delay, what):
    t.log.info("%s: %s за %.1f с" % (what, "появилось" if ok else "не появилось", delay))
    t.check(ok, "%s: у второго пользователя не появилось за %.0f с" % (what, t.settings.realtime_timeout))


# ===== два браузера одновременно =====

@case("Чат: сообщение и фото появляются у собеседника сразу", layer="realtime",
      matrix={"device": "devices"}, order=1)
def chat_live(t):
    group = t.data.group_chat("director", ["worker_mont"], "Живой чат %s" % t.params["device"])
    a = pair_page(t, "director", "light", t.params["device"], "#/messenger/%d" % group["id"], "A")
    b = pair_page(t, "worker_mont", "dark", t.params["device"], "#/messenger/%d" % group["id"], "B")
    for page in (a, b):
        t.require(wait_for(page, "() => !!document.querySelector('#messageInput')", 15), "чат не открылся у %s" % page.role)
    text = "Живое сообщение %d" % (int(time.time()) % 100000)
    a.raw.fill("#messageInput", text)
    a.raw.keyboard.press("Enter")
    ok, delay = seen(b, "x => [...document.querySelectorAll('.msg-text')].some(e => e.textContent.includes(x))", text,
                     t.settings.realtime_timeout)
    report(t, ok, delay, "текстовое сообщение")
    t.data.send_chat_files("worker_mont", group["id"], ("jpg",))
    ok, delay = seen(a, "() => [...document.querySelectorAll('.msg-media img, .msg-bubble img')].some(i => i.complete && i.naturalWidth > 0)",
                     None, t.settings.realtime_timeout + 5)
    report(t, ok, delay, "фото в чате")
    a.audit("chat_live_a")
    b.audit("chat_live_b")


@case("Задача: файл, описание и завершение этапа видны второму участнику", layer="realtime", order=2)
def task_live(t):
    task = t.data.create_task("director", "Живая задача", ["Монтажный отдел"],
                              responsible=t.data.ids("head_mont"), assigned=t.data.ids("worker_mont"))
    stage = task["stage_details"][0]
    watcher = pair_page(t, "head_mont", "light", "desktop", "#/tasks/%d" % task["id"], "B")
    watcher.raw.click(".content [data-key='stage']")
    watcher.raw.wait_for_timeout(300)
    watcher.raw.click(".file-block [data-key='images']")
    t.data.upload("worker_mont", ("jpg",), task_id=task["id"], stage_id=stage["id"])
    ok, delay = seen(watcher, "() => document.querySelectorAll('.file-tile').length > 0", None, t.settings.realtime_timeout)
    report(t, ok, delay, "файл этапа")
    if ok:
        t.check(tile_thumb_painted(watcher, "photo-autotest"), "миниатюра нового файла не отрисовалась у второго участника")
    watcher.raw.click(".content [data-key='desc']")
    text = "Живое описание %d" % (int(time.time()) % 100000)
    t.api("worker_mont").call("PUT", "tasks/%d/stages/%d" % (task["id"], stage["id"]),
                              json={"text_description": text, "spans": []})
    ok, delay = seen(watcher, "x => (document.querySelector('.desc-input') || {}).value?.includes(x) || document.querySelector('.content').innerText.includes(x)",
                     text, t.settings.realtime_timeout)
    report(t, ok, delay, "описание этапа")
    t.api("worker_mont").call("POST", "tasks/%d/stages/%d/complete" % (task["id"], stage["id"]))
    ok, delay = seen(watcher, "() => [...document.querySelectorAll('button')].some(b => b.textContent.includes('Возобновить этап') || b.textContent.includes('Продолжить задачу'))",
                     None, t.settings.realtime_timeout)
    report(t, ok, delay, "завершение этапа")


@case("Уведомление: значок колокольчика и всплывашка у адресата", layer="realtime", order=3)
def notification_live(t):
    target = pair_page(t, "worker_to", "light", "desktop", "#/tasks", "B")
    before = target.raw.evaluate("() => { const b = document.querySelector('.bell-btn .badge'); return b && !b.hidden ? Number(b.textContent) || 0 : 0; }")
    text = t.data.name("Живое уведомление %d" % (int(time.time()) % 100000))
    t.api("director").call("POST", "notifications", json={"user_ids": [t.account("worker_to").id], "message": text,
                                                          "target_section": "notifications"}, expect=(200, 201))
    ok, delay = seen(target, "n => { const b = document.querySelector('.bell-btn .badge'); return b && !b.hidden && (Number(b.textContent) || 0) > n; }",
                     before, t.settings.realtime_timeout)
    report(t, ok, delay, "счётчик на колокольчике")
    ok, delay = seen(target, "x => [...document.querySelectorAll('#kov-toasts .kov-toast')].some(e => e.innerText.includes(x.slice(0, 20)))",
                     text, t.settings.realtime_timeout)
    report(t, ok, delay, "всплывающее уведомление")


@case("Оборудование: изменение количества видно кладовщику", layer="realtime", order=4)
def equipment_live(t):
    item = t.data.equipment_item("Живая позиция", 3)
    watcher = pair_page(t, "stock", "light", "desktop", "#/equipment", "B")
    t.require(wait_for(watcher, "n => document.querySelector('.content').innerText.includes(n)", 10, item["name"]),
              "позиция не видна кладовщику")
    t.api("worker_buh").call("PATCH", "equipment/%d" % item["id"], json={"fields": ["quantity"], "quantity": 17})
    ok, delay = seen(watcher, "() => document.querySelector('.content').innerText.includes('17')", None, t.settings.realtime_timeout)
    report(t, ok, delay, "новое количество оборудования")


def watch_list(page, path):
    got = []

    def keep(response):
        # список задач браузер берёт лёгким адресом /tasks/light (на старом сервере — /tasks)
        tail = urlparse(response.url).path.rstrip("/")
        if response.request.method == "GET" and (tail.endswith("/" + path) or tail.endswith("/" + path + "/light")):
            got.append((time.time(), response))
    page.raw.on("response", keep)
    return got


def refreshed_with(page, got, since, new_id, timeout):
    deadline = time.time() + timeout
    checked = set()
    while time.time() < deadline:
        for index, (at, response) in enumerate(list(got)):
            if at < since or index in checked:
                continue
            checked.add(index)
            try:
                body = response.json()
            except Exception:
                continue
            rows = body if isinstance(body, list) else (body.get("items") or body.get("rows") or [])
            if any(isinstance(row, dict) and row.get("id") == new_id for row in rows):
                return True, at - since
        page.raw.wait_for_timeout(200)
    return False, time.time() - since


@case("Проект и организация появляются в списках у других", layer="realtime", order=5)
def lists_live(t):
    projects = pair_page(t, "director", "light", "desktop", "#/projects", "B")
    got = watch_list(projects, "projects")
    since = time.time()
    project = t.data.create_project("contract1", "Живой проект")
    ok, delay = refreshed_with(projects, got, since, project["id"], t.settings.realtime_timeout)
    report(t, ok, delay, "список проектов перезапрошен и содержит новый проект")
    orgs = pair_page(t, "head_mont", "light", "desktop", "#/organizations", "A")
    org = t.data.organization("contract2", "Живая организация")
    ok, delay = seen(orgs, "n => document.querySelector('.content').innerText.includes(n)", org["name"],
                     t.settings.realtime_timeout)
    report(t, ok, delay, "новая организация в списке")


@case("Список задач: новая задача появляется у участника", layer="realtime", order=6)
def tasks_list_live(t):
    watcher = pair_page(t, "worker_mont2", "light", "mobile", "#/tasks", "B")
    got = watch_list(watcher, "tasks")
    since = time.time()
    task = t.data.create_task("head_mont", "Живой список", ["Монтажный отдел"], responsible=t.data.ids("head_mont"),
                              assigned=t.data.ids("worker_mont2"))
    ok, delay = refreshed_with(watcher, got, since, task["id"], t.settings.realtime_timeout)
    report(t, ok, delay, "список задач перезапрошен и содержит новую задачу")
