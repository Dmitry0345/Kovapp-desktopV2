from kovtest import case
from kovtest.flows import wait_for


def ready_project(t):
    project = t.data.project("main")
    if "wh_ready" not in t.data.cache:
        t.api("director").call("PATCH", "projects/%d/warehouse/1" % project["id"],
                               json={"fields": ["name", "quantity", "unit"], "name": "Кабель автотеста",
                                     "quantity": 500, "unit": "м"})
        t.data.cache["wh_ready"] = True
    return project


# ===== сервер =====

@case("Склад: выдача товара, подтверждение получателем, остаток", layer="api", order=1)
def issue_flow(t):
    project = ready_project(t)
    stock = t.api("stock")
    before = stock.call("GET", "projects/%d/warehouse" % project["id"])
    row = [r for r in before["rows"] if r["row_no"] == 1][0]
    issue = stock.call("POST", "projects/%d/warehouse/1/issue" % project["id"],
                       json={"quantity": 5, "receiver_id": t.account("worker_mont").id}, expect=(200, 201))
    pending = t.api("worker_mont").call("GET", "warehouse/issues/pending")
    items = pending.get("items") if isinstance(pending, dict) else pending
    t.check(any((i.get("id") == issue.get("id")) for i in items or []), "получатель не видит ожидающую выдачу")
    t.api("worker_mont").call("POST", "warehouse/issues/%d/confirm" % issue["id"], json={})
    after = stock.call("GET", "projects/%d/warehouse" % project["id"])
    row_after = [r for r in after["rows"] if r["row_no"] == 1][0]
    t.check((row_after.get("issued") or 0) >= (row.get("issued") or 0) + 5, "выданное количество не выросло")
    t.expect_status(stock.post("projects/%d/warehouse/1/issue" % project["id"],
                               json={"quantity": 1, "receiver_id": t.account("stock").id}), 400, "выдача самому себе")


@case("Склад: права бухгалтерии через список «Кто правит товары»", layer="api", order=2)
def editors_flow(t):
    project = ready_project(t)
    director = t.api("director")
    current = director.call("GET", "warehouse/editors")
    ids = list(current.get("user_ids") or [])
    buh = t.account("worker_buh").id
    t.expect_status(t.api("worker_buh").patch("projects/%d/warehouse/3" % project["id"],
                                              json={"fields": ["name"], "name": "Без права"}), 403, "бухгалтер без права")
    director.call("PUT", "warehouse/editors", json={"user_ids": ids + [buh]})
    t.expect_status(t.api("worker_buh").patch("projects/%d/warehouse/3" % project["id"],
                                              json={"fields": ["name"], "name": "С правом"}), 200, "бухгалтер с правом")
    director.call("PUT", "warehouse/editors", json={"user_ids": ids})


# ===== интерфейс =====

@case("Склад проекта по ролям", layer="ui", matrix={"role": ["director", "stock", "worker_buh", "head_mont"]}, order=10)
def warehouse_ui(t):
    project = ready_project(t)
    role = t.params["role"]
    page = t.page(role, "light", "desktop", path="#/projects/%d/warehouse" % project["id"])
    t.require(wait_for(page, "() => !!document.querySelector('.content table, .content .wh-card')", 15), "склад не открылся")
    editors = page.visible_button("Кто правит товары")
    t.check((editors is not None) == (role == "director"), "кнопка «Кто правит товары» у роли %s: %s" % (role, editors is not None))
    page.audit("warehouse_%s" % role)
    page.assert_clean("склад")
