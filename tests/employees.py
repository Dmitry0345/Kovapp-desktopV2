from kovtest import case
from kovtest.flows import wait_for


# ===== сервер =====

@case("Сотрудники: служебная запись администратора скрыта из списка на экране", layer="ui", order=1)
def admin_hidden(t):
    admin = t.api("director").call("GET", "employees/%d" % t.account("admin").id)
    name = " ".join(x for x in (admin.get("last_name"), admin.get("first_name")) if x)
    page = t.page("worker_mont", "light", "desktop", path="#/employees")
    page.raw.wait_for_timeout(800)
    rows = page.raw.eval_on_selector_all("table.data tr.row", "els => els.map(e => e.innerText)")
    t.require(rows, "список сотрудников пуст")
    t.check(not any(name in r for r in rows), "служебная запись администратора «%s» видна в списке сотрудников" % name)
    t.expect_status(t.api("worker_mont").post("chat/private", json={"target_user_id": t.account("admin").id}), 403,
                    "личная переписка со служебной записью")


@case("Свои телефон и почту сотрудник меняет сам, остальное — нет", layer="api", order=2)
def own_contacts_api(t):
    api = t.api("worker_mont2")
    me = api.call("GET", "employees/%d" % api.user_id)
    new_phone = "+7 (900) 555-44-33"
    changed = api.call("PUT", "employees/%d" % api.user_id, json={"phone_number": new_phone, "email": me.get("email")})
    t.check(changed.get("phone_number") == new_phone, "телефон не сохранился")
    t.expect_status(api.put("employees/%d" % api.user_id, json={"department": "Бухгалтерия"}), 403, "смена своего отдела")
    api.call("PUT", "employees/%d" % api.user_id, json={"phone_number": me.get("phone_number") or "", "email": me.get("email")})


# ===== интерфейс =====

@case("Профиль: сотрудник меняет свой телефон через окно", layer="ui", matrix={"device": "devices"}, order=3)
def own_contacts_ui(t):
    role = "worker_mont2"
    api = t.api(role)
    before = api.call("GET", "employees/%d" % api.user_id)
    page = t.page(role, "light", t.params["device"], path="#/profile")
    raw = page.raw
    t.require(wait_for(page, "() => !!document.querySelector('.side-panel.open')", 10), "профиль не открылся")
    page.audit("profile_own")
    raw.locator(".side-panel.open button[title='Изменить телефон и почту']").click()
    raw.wait_for_selector(".modal-root input")
    phone = "+7 (900) 123-45-67"
    raw.fill(".modal-root input[placeholder='+7 (999) 000-00-00']", phone)
    raw.locator(".modal-root .modal-foot button", has_text="Сохранить").last.click()
    t.check(wait_for(page, "() => [...document.querySelectorAll('.modal-root h2')].some(h => h.textContent.includes('Сохранить изменения'))", 5),
            "нет подтверждения «Сохранить изменения?»")
    page.audit("contacts_confirm")
    raw.locator(".modal-root .modal-foot button", has_text="Сохранить").last.click()
    t.check(wait_for(page, "() => !document.querySelector('.modal-root')", 10), "окно правки не закрылось")
    after = api.call("GET", "employees/%d" % api.user_id)
    t.check(after.get("phone_number") == phone, "телефон не сохранился: %s" % after.get("phone_number"))
    api.call("PUT", "employees/%d" % api.user_id, json={"phone_number": before.get("phone_number") or "", "email": before.get("email")})
    page.assert_clean("профиль")


@case("Кнопки карточки сотрудника по ролям", layer="ui", matrix={"role": ["director", "head_mont", "worker_mont"]}, order=4)
def profile_buttons(t):
    role = t.params["role"]
    other = t.account("worker_to").id
    page = t.page(role, "light", "desktop", path="#/employees/%d" % other)
    t.require(wait_for(page, "() => !!document.querySelector('.side-panel.open')", 10), "карточка не открылась")
    has_edit = page.raw.locator(".side-panel.open button[title='Изменить данные']").count() > 0
    has_delete = page.raw.locator(".side-panel.open button[title='Удалить сотрудника']").count() > 0
    management = role == "director"
    t.check(has_edit == management, "«Изменить данные» у чужой карточки: %s" % has_edit)
    t.check(has_delete == management, "«Удалить сотрудника» у чужой карточки: %s" % has_delete)
    page.audit("profile_other")


@case("Кнопка «Добавить работника» только у руководства", layer="ui", matrix={"role": ["director", "head_mont", "worker_buh"]}, order=5)
def add_employee_button(t):
    role = t.params["role"]
    page = t.page(role, "light", "desktop", path="#/employees")
    present = page.visible_button("Добавить работника") is not None
    t.check(present == (role == "director"), "кнопка «Добавить работника» у роли %s: %s" % (role, present))
