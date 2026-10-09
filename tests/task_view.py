import time

from kovtest import case
from kovtest.flows import wait_for


def stage_buttons(page):
    return page.raw.eval_on_selector_all(".stage-actions button, .content .btn-row.stage-actions button",
                                         "els => els.map(e => e.textContent.trim())")


# ===== этапы =====

@case("Этап чужого отдела заперт, свой — открыт", layer="ui", matrix={"device": "devices"}, order=1)
def foreign_stage_locked(t):
    task = t.data.task("multi")
    page = t.page("worker_mont", "light", t.params["device"], path="#/tasks/%d" % task["id"])
    stages = page.raw.evaluate("""() => [...document.querySelectorAll('.stage-nav button[data-stage]')]
        .map(e => ({ n: e.dataset.stage, dep: (e.querySelector('.dep') || {}).textContent || '', locked: e.classList.contains('is-locked') }))""")
    t.log.info("кнопки этапов: %s" % stages)
    t.require(stages, "полоса этапов не найдена")
    for item in stages:
        foreign = item["dep"].strip() != "Монтажный отдел"
        t.check(item["locked"] == foreign, "этап %s (%s): замок %s, а должен %s" % (
            item["n"], item["dep"], "есть" if item["locked"] else "отсутствует", "быть" if foreign else "отсутствовать"))
    page.audit("stages_foreign")
    page.assert_clean("этапы")


@case("Описание этапа сохраняется автоматически", layer="ui", order=2)
def description_autosave(t):
    task = t.data.task("mont")
    page = t.page("worker_mont", "light", "desktop", path="#/tasks/%d" % task["id"])
    raw = page.raw
    raw.click(".content [data-key='desc']")
    raw.wait_for_selector(".desc-input")
    text = "Проверка описания %d" % int(time.time())
    raw.fill(".desc-input", text)
    raw.wait_for_timeout(2500)
    stage = t.api("worker_mont").call("GET", "tasks/%d/stages/%d" % (task["id"], task["stage_details"][0]["id"]))
    t.check(text in (stage.get("text_description") or ""), "описание не сохранилось на сервере")
    page.assert_clean("описание этапа")


@case("Завершение и возобновление этапа сотрудником отдела", layer="ui", order=3)
def complete_resume(t):
    task = t.data.create_task("director", "Завершение этапа в окне", ["Монтажный отдел"],
                              responsible=t.data.ids("head_mont"), assigned=t.data.ids("worker_mont"))
    page = t.page("worker_mont", "light", "desktop", path="#/tasks/%d" % task["id"])
    t.require(page.visible_button("Завершить этап") is not None, "нет кнопки «Завершить этап» у сотрудника отдела")
    page.visible_button("Завершить этап").click()
    if page.raw.locator(".modal-root").count():
        page.raw.locator(".modal-root .modal-foot button").last.click()
    t.check(wait_for(page, "() => [...document.querySelectorAll('button')].some(b => b.textContent.includes('Возобновить этап'))", 10),
            "после завершения не появилась кнопка «Возобновить этап»")
    state = t.api("worker_mont").call("GET", "tasks/%d" % task["id"])
    t.check(state["stage_details"][0]["is_completed"], "этап не завершён на сервере")
    page.visible_button("Возобновить этап").click()
    if page.raw.locator(".modal-root").count():
        page.raw.locator(".modal-root .modal-foot button").last.click()
    t.check(wait_for(page, "() => [...document.querySelectorAll('button')].some(b => b.textContent.includes('Завершить этап'))", 10),
            "этап не возобновился")
    # оба действия — в журнале задачи (возобновление раньше не записывалось)
    fields = [h.get("field") for h in t.api("worker_mont").call("GET", "tasks/%d/history" % task["id"]) or []]
    t.check("stage_done" in fields, "завершение этапа не записано в журнал задачи")
    t.check("stage_resumed" in fields, "возобновление этапа не записано в журнал задачи")
    page.assert_clean("завершение этапа")


@case("«Продолжить задачу»: открыто руководителю, заперто работнику", layer="ui", order=4)
def extend_gate(t):
    task = t.data.create_task("director", "Продление в окне", ["Монтажный отдел"],
                              responsible=t.data.ids("head_mont"), assigned=t.data.ids("worker_mont"))
    t.data.complete_stage("director", task, 1)
    head = t.page("head_mont", "light", "desktop", path="#/tasks/%d" % task["id"])
    button = head.visible_button("Продолжить задачу")
    t.require(button is not None, "у руководителя нет кнопки «Продолжить задачу»")
    t.check(not head.is_locked(button), "у руководителя отдела продление заперто")
    button.click()
    t.check(wait_for(head, "() => !!document.querySelector('.modal-root .modal')", 5), "окно продления не открылось")
    head.audit("extend_dialog")
    head.close_modal()
    worker = t.page("worker_mont", "light", "desktop", path="#/tasks/%d" % task["id"], browser="B")
    button = worker.visible_button("Продолжить задачу")
    t.require(button is not None, "у работника кнопка «Продолжить задачу» пропала, а должна быть с замком")
    t.check(worker.is_locked(button), "у работника продление не заперто")


@case("Кнопка журнала изменений открывает историю", layer="ui", order=5)
def history_dialog(t):
    task = t.data.task("mont")
    page = t.page("head_mont", "light", "desktop", path="#/tasks/%d" % task["id"])
    page.raw.click(".content button[title='Кто и когда менял задачу']")
    t.check(wait_for(page, "() => !!document.querySelector('.modal-root .modal')", 5), "журнал задачи не открылся")
    page.audit("history_dialog")
    page.close_modal()
    page.assert_clean("журнал задачи")
