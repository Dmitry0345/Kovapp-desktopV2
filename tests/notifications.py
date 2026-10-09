import time

from kovtest import case
from kovtest.flows import wait_for


# ===== сервер =====

@case("Уведомление доходит адресату и отмечается прочитанным", layer="api", order=1)
def notify_api(t):
    sender = t.api("director")
    target = t.api("worker_to")
    text = t.data.name("Проверка уведомления %d" % int(time.time()))
    sender.call("POST", "notifications", json={"user_ids": [target.user_id], "message": text,
                                               "target_section": "notifications"}, expect=(200, 201))
    items = target.call("GET", "employees/%d/notifications" % target.user_id)
    found = [n for n in items if n.get("message") == text]
    t.require(found, "адресат не получил уведомление")
    t.check(found[0].get("is_read") is False, "новое уведомление уже отмечено прочитанным")
    target.call("POST", "notifications/mark-as-read", json=[found[0]["id"]])
    items = target.call("GET", "employees/%d/notifications" % target.user_id)
    again = [n for n in items if n.get("id") == found[0]["id"]]
    t.check(again and again[0].get("is_read") is True, "уведомление не отметилось прочитанным")


# ===== интерфейс =====

@case("Создание уведомления через окно «Создать уведомление»", layer="ui", matrix={"device": "devices"}, order=2)
def notify_ui(t):
    target = t.account("worker_to")
    person = t.api("director").call("GET", "employees/%d" % target.id)
    page = t.page("director", "light", t.params["device"], path="#/notifications")
    raw = page.raw
    raw.locator(".content button", has_text="Создать уведомление").click()
    raw.wait_for_selector(".modal-root .notif-message")
    page.audit("notify_dialog")
    text = t.data.name("Уведомление из окна %d" % int(time.time()))
    raw.fill(".modal-root .notif-message", text)
    raw.click(".modal-root [data-key='emp']")
    unique = person.get("middle_name") or person["first_name"]
    raw.fill(".modal-root .toolbar input", unique)
    raw.wait_for_timeout(300)
    matches = raw.locator(".modal-root .checklist label", has_text=unique)
    t.require(matches.count() == 1, "в окне найдено %d сотрудников по «%s», ожидался один" % (matches.count(), unique))
    matches.first.locator("input").check()
    raw.locator(".modal-root .modal-foot button", has_text="Отправить").click()
    t.check(wait_for(page, "() => !document.querySelector('.modal-root')", 10), "окно не закрылось после отправки")
    items = t.api("worker_to").call("GET", "employees/%d/notifications" % target.id)
    t.check(any(n.get("message") == text for n in items), "адресат не получил уведомление из окна")
    page.assert_clean("уведомления")


@case("Кнопка «Создать уведомление» есть только у руководителей и руководства", layer="ui",
      matrix={"role": ["director", "head_to", "worker_mont", "contract1", "stock"]}, order=3)
def create_button_by_role(t):
    role = t.params["role"]
    page = t.page(role, "light", "desktop", path="#/notifications")
    present = page.visible_button("Создать уведомление") is not None
    allowed = role in ("director", "head_to")
    t.check(present == allowed, "кнопка «Создать уведомление» %s, а должна %s" % (
        "есть" if present else "отсутствует", "быть" if allowed else "отсутствовать"))
