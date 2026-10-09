import time

from kovtest import case
from kovtest.flows import wait_for


# ===== сервер =====

@case("Оборудование: позиция, фото, выдача, получение, возврат", layer="api", order=1)
def equipment_flow(t):
    buh = t.api("worker_buh")
    item = buh.call("POST", "equipment", json={"name": t.data.name("Шуруповёрт %d" % int(time.time())),
                                                "quantity": 4, "unit": "шт"}, expect=201)
    t.data.equipment.append(item["id"])
    name, content, ctype = t.world.media.get("jpg")
    buh.call("POST", "equipment/%d/photo" % item["id"], files=[("file", (name, content, ctype))])
    t.expect_status(buh.get_file("equipment/%d/photo" % item["id"]), 200, "фото позиции")
    stock = t.api("stock")
    issue = stock.call("POST", "equipment/%d/issue" % item["id"], json={"receiver_id": t.account("worker_mont").id,
                                                                         "quantity": 2}, expect=201)
    t.expect_status(t.api("director").post("equipment/issues/%d/confirm" % issue["id"]), 403, "подтверждение не получателем")
    t.api("worker_mont").call("POST", "equipment/issues/%d/confirm" % issue["id"])
    rows = stock.call("GET", "equipment")["rows"]
    row = [r for r in rows if r["id"] == item["id"]][0]
    t.check(row.get("issued") == 2, "на руках не 2 шт. после получения: %s" % row.get("issued"))
    t.api("worker_mont").call("POST", "equipment/issues/%d/return" % issue["id"])
    stock.call("POST", "equipment/issues/%d/return/accept" % issue["id"])
    rows = stock.call("GET", "equipment")["rows"]
    row = [r for r in rows if r["id"] == item["id"]][0]
    t.check(not row.get("issued"), "после возврата оборудование всё ещё на руках: %s" % row.get("issued"))
    history = stock.call("GET", "equipment/%d/history" % item["id"])
    t.check(history is not None, "журнал позиции не получен")


# ===== интерфейс =====

@case("Кнопки раздела «Оборудование» по ролям", layer="ui",
      matrix={"role": ["director", "worker_buh", "stock", "worker_mont"]}, order=10)
def equipment_buttons(t):
    role = t.params["role"]
    if "eq_ui_item" not in t.data.cache:
        t.data.cache["eq_ui_item"] = t.data.equipment_item("Нивелир", 3)
    page = t.page(role, "light", "desktop", path="#/equipment")
    add = page.visible_button("Добавить")
    t.check((add is not None) == (role in ("director", "worker_buh")), "кнопка «Добавить» у роли %s: %s" % (role, add is not None))
    issue = page.visible_button("Выдать")
    t.check((issue is not None) == (role in ("director", "stock")), "кнопка «Выдать» у роли %s: %s" % (role, issue is not None))
    page.audit("equipment_%s" % role)


@case("Добавление позиции оборудования через окно", layer="ui", matrix={"device": "devices"}, order=11)
def add_item_ui(t):
    page = t.page("worker_buh", "light", t.params["device"], path="#/equipment")
    raw = page.raw
    page.visible_button("Добавить").click()
    raw.wait_for_selector(".modal-root input")
    title = t.data.name("Лазерный дальномер %s %d" % (t.params["device"], int(time.time()) % 100000))
    raw.locator(".modal-root input").first.fill(title)
    numbers = raw.locator(".modal-root input[type=number], .modal-root input[inputmode=decimal]")
    if numbers.count():
        numbers.first.fill("2")
    page.audit("equipment_add_dialog")
    raw.locator(".modal-root .modal-foot button").last.click()
    t.check(wait_for(page, "() => !document.querySelector('.modal-root')", 10), "окно добавления не закрылось")
    rows = t.api("worker_buh").call("GET", "equipment")["rows"]
    created = [r for r in rows if r["name"] == title]
    t.require(created, "позиция не появилась на сервере")
    t.data.equipment.append(created[0]["id"])
    t.check(wait_for(page, "n => document.querySelector('.content').innerText.includes(n)", 10, title),
            "позиция не видна в списке")
    page.assert_clean("оборудование")
