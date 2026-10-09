import time

from kovtest import case
from kovtest.flows import wait_for


# ===== сервер =====

@case("Организация: создание, поиск, правка, дубль названия, удаление", layer="api", order=1)
def org_api(t):
    api = t.api("contract1")
    name = t.data.name("Организация API %d" % int(time.time()))
    org = api.call("POST", "organizations", json={"name": name, "description": "Описание",
                                                   "phones": [{"phone_number": "+7 (812) 111-22-33", "description": "Приёмная"}],
                                                   "emails": [{"email": "info@kovapp-autotest.ru", "description": "Общая"}]},
                   expect=201)
    t.data.organizations.append(org["id"])
    found = api.call("GET", "organizations/search", params={"query": name[:12]})
    t.check(any(o["id"] == org["id"] for o in found), "поиск не нашёл созданную организацию")
    t.expect_status(api.post("organizations", json={"name": name.upper(), "phones": [], "emails": []}), 400,
                    "повтор названия в другом регистре")
    changed = api.call("PUT", "organizations/%d" % org["id"], json={"description": "Новое описание"})
    t.check(changed.get("description") == "Новое описание", "описание не изменилось")
    t.expect_status(api.get("organizations/999999999"), 404, "несуществующая организация")
    t.expect_status(api.delete("organizations/%d" % org["id"]), 200, "удаление")


# ===== интерфейс =====

@case("Организация через интерфейс: добавить, открыть, удалить", layer="ui", matrix={"device": "devices"}, order=2)
def org_ui(t):
    page = t.page("head_buh", "light", t.params["device"], path="#/organizations")
    raw = page.raw
    raw.locator(".content button", has_text="Добавить организацию").click()
    t.require(wait_for(page, "() => !!document.querySelector(\".content input[placeholder='Название организации']\")", 10),
              "форма новой организации не открылась")
    name = t.data.name("Организация UI %s %d" % (t.params["device"], int(time.time())))
    raw.fill(".content input[placeholder='Название организации']", name)
    raw.fill(".content textarea", "Создана автотестом")
    page.audit("org_form")
    raw.locator(".content button", has_text="Сохранить").click()
    t.check(wait_for(page, "() => !document.querySelector(\".content input[placeholder='Название организации']\")", 10),
            "после сохранения форма не закрылась")
    orgs = t.api("head_buh").call("GET", "organizations")
    created = [o for o in orgs if o["name"] == name]
    t.require(created, "организация не появилась на сервере")
    t.data.organizations.append(created[0]["id"])
    page.goto("#/organizations")
    t.check(wait_for(page, "n => document.querySelector('.content').innerText.includes(n)", 10, name),
            "организация не видна в списке")
    page.assert_clean("организации")


@case("Кнопка «Добавить организацию» по ролям", layer="ui",
      matrix={"role": ["director", "head_mont", "contract2", "worker_mont", "stock"]}, order=3)
def org_button_by_role(t):
    role = t.params["role"]
    page = t.page(role, "light", "desktop", path="#/organizations")
    present = page.visible_button("Добавить организацию") is not None
    allowed = role in ("director", "head_mont", "contract2")
    t.check(present == allowed, "кнопка «Добавить организацию» %s у роли %s" % ("есть" if present else "отсутствует", role))
