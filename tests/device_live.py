"""Реальное время (WebSocket): действие на телефоне сразу видно у второго участника — без обновления страницы.

Второй участник — второй телефон (run.cmd device-pair) или сайт в браузере (обычный run.cmd device).
Для каждого события считается задержка: дольше SLOW_S — предупреждение, дольше LIVE_S — ошибка.
"""
import json
import time

from kovtest import case
from kovtest.device_views import open_stage_desc

LIVE_S = 10
SLOW_S = 3
ROW = "table.data tr.row[data-key='%s']"
MSG = "[...document.querySelectorAll('.msg-text')].some(e => e.textContent.includes(%s))"
MSG_COUNT = "return [...document.querySelectorAll('.msg-text')].filter(e => e.textContent.includes(arguments[0])).length;"
HAS_BUTTON = "[...document.querySelectorAll('button')].some(b => b.textContent.includes(%s) && b.getBoundingClientRect().height > 0)"


class SiteScreen:
    """Второй участник — сайт в браузере тестов, с теми же действиями, что у телефона."""
    label = "сайт в браузере"

    def __init__(self, t):
        self.t = t
        self.raw = None

    def ensure_app(self, t=None):
        pass

    def login(self, role, route="#/tasks", theme="light"):
        self.raw = self.t.page(role, theme, "phone", path=route, variant="web").raw
        if not self.wait_js("document.querySelector('.app .content')", 40):
            raise AssertionError("сайт: после входа ролью %s интерфейс не появился" % role)

    def js(self, script, *args):
        return self.raw.evaluate("(__a) => (function(){ %s }).apply(null, __a)" % script, list(args))

    def wait_js(self, expression, timeout=30):
        try:
            self.raw.wait_for_function("() => !!(%s)" % expression, timeout=timeout * 1000, polling=250)
            return True
        except Exception:
            return False

    def fill(self, selector, text):
        self.raw.fill(selector, text)

    def click(self, selector, timeout=15):
        try:
            self.raw.click(selector, timeout=timeout * 1000)
            return True
        except Exception:
            return False

    def click_text(self, text, timeout=15, within="", last=False):
        found = self.raw.locator((within + " " if within else "") + "button", has_text=text)
        try:
            (found.last if last else found.first).click(timeout=timeout * 1000)
            return True
        except Exception:
            return False


def pair(t):
    d = t.world.device
    if d is None:
        t.skip("только для тестов на устройстве (run.cmd device / device-pair)")
    d.ensure_app(t)
    other = t.world.device2 or SiteScreen(t)
    other.ensure_app(t)
    t.step("второй участник: %s" % other.label)
    return d, other


def live(t, screen, expression, what):
    """Ждать, пока у screen появится то, что описывает expression; записать задержку."""
    start = time.time()
    if not screen.wait_js(expression, LIVE_S):
        t.fail("%s: «%s» не появилось за %d с без обновления страницы" % (screen.label, what, LIVE_S))
        return False
    spent = time.time() - start
    t.step("%s: «%s» — через %.1f с" % (screen.label, what, spent))
    if spent > SLOW_S:
        t.warn("%s увидел(а) «%s» только через %.1f с" % (screen.label, what, spent))
    return True


def confirm_if_asked(screen):
    if screen.wait_js("document.querySelector('.modal-root .modal')", 3):
        screen.click_text("", within=".modal-root .modal-foot", last=True)


@case("Чат в реальном времени: сообщение с телефона сразу у второго участника, ответ — сразу на телефоне",
      layer="device", order=1)
def live_chat(t):
    d, other = pair(t)
    chat = t.data.private_chat("worker_mont", "worker_mont2")
    route = "#/messenger/%d" % chat["id"]
    ready = "document.querySelector('#messageInput') && document.querySelector('#messageInput').offsetParent !== null"
    d.login("worker_mont", route)
    other.login("worker_mont2", route)
    t.require(d.wait_js(ready, 20), "%s: чат не открылся" % d.label)
    t.require(other.wait_js(ready, 20), "%s: чат не открылся" % other.label)
    for sender, receiver in ((d, other), (other, d)):
        text = "Живое сообщение %d" % (int(time.time() * 1000) % 10000000)
        sender.fill("#messageInput", text)
        t.require(sender.click("#btnSend"), "%s: нет кнопки «Отправить»" % sender.label)
        # отправка очищает поле: текст остался — касание «Отправить» не сработало
        t.check(sender.wait_js("!document.querySelector('#messageInput').value", 5),
                "%s: после касания «Отправить» текст остался в поле ввода" % sender.label)
        t.check(sender.wait_js(MSG % json.dumps(text), 10), "%s: своё сообщение не появилось в ленте" % sender.label)
        if live(t, receiver, MSG % json.dumps(text), "сообщение от " + sender.label):
            time.sleep(1)
            count = receiver.js(MSG_COUNT, text)
            t.check(count == 1, "%s: сообщение показано %s раз(а) вместо одного" % (receiver.label, count))


@case("Список задач в реальном времени: задача, созданная на телефоне, сразу в списке у второго участника",
      layer="device", order=2)
def live_task_list(t):
    d, other = pair(t)
    other.login("worker_mont", "#/tasks")
    t.require(other.wait_js("document.querySelector('table.data')", 20), "%s: список задач не открылся" % other.label)
    d.login("head_mont", "#/tasks")
    t.require(d.click_text("Новая задача"), "%s: нет кнопки «Новая задача»" % d.label)
    t.require(d.wait_js("document.querySelector('.content input.input')", 15), "%s: форма задачи не открылась" % d.label)
    title = t.data.name("Живая задача %d" % (int(time.time()) % 100000))
    d.fill(".content input.input", title)
    t.require(d.wait_js("document.querySelector('.content input[type=date]')", 15), "в форме нет поля «Дата окончания»")
    d.set_value(".content input[type=date]", t.data.deadline())
    t.require(d.click_text("Сохранить", last=True), "в форме нет кнопки «Сохранить»")
    where = d.wait_hash(r"^#/tasks/\d+$", 20)
    t.require(where, "%s: после сохранения задача не открылась" % d.label)
    task_id = int(where.split("/")[-1])
    t.data.tasks.append(task_id)
    live(t, other, "!!document.querySelector(%s)" % json.dumps(ROW % task_id), "новая задача в списке")


@case("Карточка задачи в реальном времени: описание и завершение этапа сразу видны у второго участника",
      layer="device", order=3)
def live_task_card(t):
    d, other = pair(t)
    task = t.data.create_task("director", "Живая карточка", ["Монтажный отдел"], responsible=t.data.ids("head_mont"),
                              assigned=t.data.ids("worker_mont", "worker_mont2"))
    route = "#/tasks/%d" % task["id"]
    stage_ready = "document.querySelector('.stage-nav button[data-stage], .content .stage-actions')"
    other.login("worker_mont2", route)
    d.login("worker_mont", route)
    t.require(d.wait_js(stage_ready, 20) and other.wait_js(stage_ready, 20), "карточка задачи не открылась у обоих")
    t.require(other.click(".content [data-key='desc']"), "%s: нет вкладки описания этапа" % other.label)
    t.require(open_stage_desc(d), "%s: нет вкладки описания этапа" % d.label)
    t.require(d.wait_js("document.querySelector('.desc-input')", 10), "%s: поле описания этапа не открылось" % d.label)
    text = "Живое описание %d" % (int(time.time()) % 100000)
    d.fill(".desc-input", text)
    live(t, other, "[...document.querySelectorAll('.desc-input, .desc-view')].some(e => (e.value || e.textContent || '')"
                   ".includes(%s))" % json.dumps(text), "новое описание этапа")
    t.require(d.click_text("Завершить этап"), "%s: нет кнопки «Завершить этап»" % d.label)
    confirm_if_asked(d)
    t.require(d.wait_js(HAS_BUTTON % json.dumps("Возобновить этап"), 10), "%s: этап не завершился" % d.label)
    live(t, other, HAS_BUTTON % json.dumps("Возобновить этап"), "этап завершён")


@case("Удаление в реальном времени: задачу удалили на телефоне — у второго участника карточка закрылась сама",
      layer="device", order=4)
def live_task_deleted(t):
    d, other = pair(t)
    task = t.data.create_task("head_mont", "Живое удаление", ["Монтажный отдел"], responsible=t.data.ids("head_mont"),
                              assigned=t.data.ids("worker_mont"))
    other.login("worker_mont", "#/tasks/%d" % task["id"])
    t.require(other.wait_js("document.querySelector('.stage-nav button[data-stage], .content .stage-actions')", 20),
              "%s: карточка задачи не открылась" % other.label)
    d.login("head_mont", "#/tasks")
    t.require(d.long_press(ROW % task["id"]), "%s: задачи нет в списке" % d.label)
    t.require(d.wait_js("document.querySelector(%s).classList.contains('selected')" % json.dumps(ROW % task["id"]), 5),
              "%s: удержание не выделило строку" % d.label)
    t.require(d.click_text("Удалить"), "%s: нет кнопки «Удалить»" % d.label)
    t.require(d.confirm("Удалить"), "%s: не появилось подтверждение удаления" % d.label)
    if task["id"] in t.data.tasks:
        t.data.tasks.remove(task["id"])
    live(t, other, "!/^#\\/tasks\\/%d(\\/|$)/.test(location.hash)" % task["id"], "карточка удалённой задачи закрыта")
