"""Чат на настоящем Android: выбор сообщений долгим нажатием, часть группы файлов, кружок у сообщения,
пересылка и удаление выбранного, меню без «Выбрать», шапка мессенджера, просмотр фото (проект v58–v59).
Жесты — настоящие (долгое нажатие и касания через Appium); состояние экрана читается со страницы приложения."""
import time

from kovtest import case
from tests.messenger import shared_group


def dev(t):
    if t.world.device is None:
        t.skip("только для тестов на устройстве (run.cmd device)")
    t.world.device.ensure_app(t)
    return t.world.device


def wait(check, timeout: float, step: float = 0.4) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if check():
            return True
        time.sleep(step)
    return False


# пометить k-ю плитку последней группы файлов и её кружок — для настоящих касаний по селектору
MARK = """const k = arguments[0];
  document.querySelectorAll('[data-kt]').forEach(e => e.removeAttribute('data-kt'));
  const b = [...document.querySelectorAll('#messagesList .msg-bubble')].filter(x => x.__msg && (x.__msg.files || []).length > 1).pop();
  if (!b) return 0;
  const tiles = [...b.querySelectorAll('*')].filter(e => e.__file);
  if (!tiles[k]) return 0;
  b.setAttribute('data-kt', 'bubble'); tiles[k].setAttribute('data-kt', 'tile');
  const c = b.parentElement.querySelector(':scope > .sel-circle'); if (c) c.setAttribute('data-kt', 'circle');
  tiles[k].scrollIntoView({ block: 'center' });
  return tiles.length;"""
STATE = """const b = document.querySelector('[data-kt=bubble]'), bar = document.getElementById('selBar');
  const tiles = b ? [...b.querySelectorAll('*')].filter(e => e.__file) : [];
  const c = b && b.parentElement.querySelector(':scope > .sel-circle');
  return { on: !!bar && !bar.hidden, count: bar ? bar.querySelector('.sel-count').textContent : '',
           del: bar ? !bar.querySelector('.sel-del').hidden : null,
           files: tiles.map(e => e.classList.contains('sel-file') ? 1 : 0).join(''),
           circle: !c || c.hidden ? '-' : c.classList.contains('on') ? 'v' : c.classList.contains('part') ? '/' : 'o',
           viewer: !!document.querySelector('.mv-root') };"""
STOP = "if (typeof stopSelect === 'function') stopSelect(); document.querySelectorAll('.context-menu, .modal-root').forEach(e => e.remove());"


def history(t, chat_id: int) -> list:
    data = t.api("director").call("GET", "chat/history/%d" % chat_id)
    return data if isinstance(data, list) else (data or {}).get("messages", [])


def group_of(t, chat_id: int, file_id: int) -> dict:
    for m in history(t, chat_id):
        if any(int(f.get("id") or f.get("message_id") or 0) == int(file_id) for f in (m.get("files") or [])):
            return m
    return {}


def group_files(t, chat_id: int, file_id: int) -> list:
    """Файлы группы на сервере. В истории каждый файл группы — своя запись (special=file_item)
    с общим group_id; сама группа (подпись) — запись без файлов."""
    msgs = history(t, chat_id)
    own = next((m for m in msgs if any(int(f.get("id") or f.get("message_id") or 0) == int(file_id)
                                       for f in (m.get("files") or []))), None)
    gid = own.get("group_id") if own else None
    if not gid:
        return list((own or {}).get("files") or [])
    return [f for m in msgs if m.get("group_id") == gid for f in (m.get("files") or [])]


def open_group(t, d, kinds=("jpg", "jpg", "jpg")):
    group = shared_group(t)
    sent = t.data.send_chat_files("director", group["id"], kinds)
    files = (sent or {}).get("files") or []
    t.require(len(files) == len(kinds), "в чат ушло %d файлов из %d" % (len(files), len(kinds)))
    d.login("director", "#/messenger/%d" % group["id"])
    t.require(wait(lambda: d.js(MARK, 1) == len(kinds), 25), "в открытом чате нет сообщения с группой файлов")
    d.js(STOP)
    return group, files


def mark(t, d, k: int):
    t.require(d.js(MARK, k), "плитка %d группы не найдена" % (k + 1))


@case("Чат на телефоне: долгое нажатие выбирает один файл группы, кружок у сообщения — всю группу",
      layer="device", order=1)
def select_part_and_whole(t):
    d = dev(t)
    open_group(t, d)
    mark(t, d, 1)
    t.require(d.long_press("[data-kt=tile]", hold=0.9), "долгое нажатие на фото не получилось")
    s = d.js(STATE)
    t.check(s["on"] and s["files"] == "010" and s["circle"] == "/" and not s["viewer"],
            "после долгого нажатия должен быть выбран только 2-й файл (кружок — «часть»), просмотр не открыт: %s" % s)
    shadow = d.js("return getComputedStyle(document.querySelector('[data-kt=bubble]')).boxShadow;")
    t.check(shadow == "none", "при выборе части группы всё сообщение не должно выделяться рамкой: %r" % shadow)
    mark(t, d, 1)
    t.require(d.click("[data-kt=circle]"), "у сообщения нет кружка выбора")
    s = d.js(STATE)
    t.check(s["files"] == "111" and s["circle"] == "v" and s["count"] == "Выбрано: 3", "кружок должен выбрать всю группу: %s" % s)
    mark(t, d, 0)
    t.require(d.click("[data-kt=tile]"), "касание по фото в режиме выбора не прошло")
    s = d.js(STATE)
    t.check(s["files"] == "011" and s["circle"] == "/", "касание снимает один файл — группа снова частично: %s" % s)
    d.js(STOP)


@case("Чат на телефоне: пересылка одного файла группы — в другой чат уходит только он, без подписи",
      layer="device", order=2)
def forward_part(t):
    d = dev(t)
    group, files = open_group(t, d)
    title = "Пересылка %d" % int(time.time() % 100000)
    target = t.data.group_chat("director", ["head_buh"], title)
    d.login("director", "#/messenger/%d" % group["id"])
    t.require(wait(lambda: d.js(MARK, 2), 25), "группа файлов не появилась на экране")
    t.require(d.long_press("[data-kt=tile]", hold=0.9), "долгое нажатие на фото не получилось")
    t.require(d.click("#selBar .sel-fwd"), "в строке выбора нет «Переслать»")
    t.require(d.click_text(title, within=".forward-list", timeout=10), "в окне пересылки нет чата «%s»" % title)
    t.require(d.click_text("Переслать", within=".modal-root .modal", timeout=5), "в окне пересылки нет кнопки «Переслать»")
    got = {}
    wait(lambda: got.update(next(iter(sorted([m for m in history(t, target["id"]) if m.get("files")], key=lambda m: -int(m["id"]))), {})) or got, 20, 1)
    names = [f.get("filename") or f.get("file_name") for f in (got.get("files") or [])]
    t.check(len(names) == 1 and names[0] == (files[2].get("filename") or files[2].get("file_name")),
            "в чат «%s» должен прийти один выбранный файл, пришло: %s" % (title, names))
    t.check(not str(got.get("text") or "").strip(), "у части группы перенеслась подпись: %r" % got.get("text"))


@case("Чат на телефоне: удаление одного своего файла группы — остальные на месте (и на сервере)",
      layer="device", order=3)
def delete_one_file(t):
    d = dev(t)
    group, files = open_group(t, d)
    mark(t, d, 0)
    t.require(d.long_press("[data-kt=tile]", hold=0.9), "долгое нажатие на фото не получилось")
    t.require(d.js(STATE)["del"], "для своего файла нет кнопки «Удалить»")
    t.require(d.click("#selBar .sel-del"), "кнопка «Удалить» не нажалась")
    t.require(d.click_text("Удалить", within=".modal-root .modal", timeout=5), "нет подтверждения удаления")
    left = []
    wait(lambda: (left.clear() or left.extend(group_files(t, group["id"], files[1]["id"]))) or len(left) == 2, 20, 1)
    t.check(len(left) == 2, "на сервере в группе осталось %d файлов вместо 2" % len(left))
    t.check(wait(lambda: d.js(MARK, 0) == 2, 15), "на экране удалённый файл не пропал из группы")


@case("Чат на телефоне: в меню сообщения нет «Выбрать» (выбор — долгим нажатием)", layer="device", order=4)
def menu_without_select(t):
    d = dev(t)
    group = shared_group(t)
    t.data.send_chat_files("director", group["id"], ("jpg",), text="Проверка меню")
    d.login("director", "#/messenger/%d" % group["id"])
    t.require(wait(lambda: d.js("return [...document.querySelectorAll('#messagesList .msg-bubble')].some(b => (b.textContent || '').includes('Проверка меню'));"), 20),
              "сообщение не появилось")
    d.js("const b = [...document.querySelectorAll('#messagesList .msg-bubble')].filter(b => (b.textContent || '').includes('Проверка меню')).pop();"
         "const t = [...b.querySelectorAll('*')].find(e => !e.children.length && (e.textContent || '').includes('Проверка меню')); t.setAttribute('data-kt', 'text');")
    t.require(d.click("[data-kt=text]"), "касание по тексту сообщения не прошло")
    items = d.js("return [...document.querySelectorAll('.context-menu-item')].map(e => e.textContent.trim());") or []
    t.check(bool(items) and not any("Выбрать" in x for x in items), "меню сообщения на телефоне: %s" % items)
    d.js(STOP)


@case("Мессенджер на телефоне: в открытом чате нет верхней строки приложения, в списке — есть",
      layer="device", order=5)
def top_bar_in_chat(t):
    d = dev(t)
    group = shared_group(t)
    bar = "const e = document.querySelector('.app > .top-bar'); return !!e && getComputedStyle(e).display !== 'none';"
    d.login("director", "#/messenger/%d" % group["id"])
    t.require(wait(lambda: d.js("return !!document.querySelector('#kovChat.chat-open');"), 20), "чат не открылся")
    t.check(not d.js(bar), "в открытом чате видна верхняя строка (меню, раздел, уведомления)")
    t.require(d.click("#kovChatBack"), "нет кнопки «‹» в шапке чата")
    t.check(wait(lambda: d.js(bar), 5), "в списке чатов верхняя строка не вернулась")


@case("Просмотр фото на телефоне: экран не сдвигается, щипок приближает, свайп вниз закрывает",
      layer="device", order=6)
def viewer_phone(t):
    d = dev(t)
    open_group(t, d)
    mark(t, d, 2)
    t.require(d.click("[data-kt=tile]"), "касание по фото не прошло")
    t.require(wait(lambda: d.js("return !!document.querySelector('.mv-root .mv-media');"), 10), "просмотр фото не открылся")
    time.sleep(0.8)
    t.check(d.js("const r = document.querySelector('.mv-root'), b = document.querySelector('.mv-back').getBoundingClientRect(); return r.scrollLeft === 0 && b.left >= 0;"),
            "просмотр сдвинут вбок (у крайнего снимка)")
    d.js("""const s = document.querySelector('.mv-stage'), im = document.querySelector('.mv-media'), r = im.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
      const t = (i, p) => new Touch({ identifier: i, target: im, clientX: p, clientY: y });
      s.dispatchEvent(new TouchEvent('touchstart', { touches: [t(1, x - 30), t(2, x + 30)], changedTouches: [t(2, x + 30)], bubbles: true, cancelable: true }));
      s.dispatchEvent(new TouchEvent('touchmove', { touches: [t(1, x - 90), t(2, x + 90)], bubbles: true, cancelable: true }));
      s.dispatchEvent(new TouchEvent('touchend', { touches: [], changedTouches: [t(1, x - 90), t(2, x + 90)], bubbles: true, cancelable: true }));""")
    tr = d.js("return document.querySelector('.mv-media').style.transform;") or ""
    t.check("scale(" in tr, "щипок не приблизил фото (transform: %r)" % tr)
    # приближенное фото свайп двигает, а не закрывает (как в Telegram) — закрытие проверяем на открытом заново
    t.require(d.click(".mv-back"), "в просмотре нет кнопки «Назад»")
    t.require(wait(lambda: not d.js("return !!document.querySelector('.mv-root');"), 5), "просмотр не закрылся кнопкой «Назад»")
    mark(t, d, 2)
    t.require(d.click("[data-kt=tile]"), "касание по фото не прошло")
    t.require(wait(lambda: d.js("return !!document.querySelector('.mv-root .mv-media');"), 10), "просмотр фото не открылся снова")
    time.sleep(0.8)
    d.js("""const s = document.querySelector('.mv-stage');
      const t = y => [new Touch({ identifier: 1, target: s, clientX: 180, clientY: y })];
      s.dispatchEvent(new TouchEvent('touchstart', { touches: t(200), changedTouches: t(200), bubbles: true, cancelable: true }));
      for (let k = 1; k <= 6; k++) s.dispatchEvent(new TouchEvent('touchmove', { touches: t(200 + k * 40), changedTouches: t(200 + k * 40), bubbles: true, cancelable: true }));
      s.dispatchEvent(new TouchEvent('touchend', { touches: [], changedTouches: t(440), bubbles: true, cancelable: true }));""")
    t.check(wait(lambda: not d.js("return !!document.querySelector('.mv-root');"), 3), "свайп вниз не закрыл просмотр")
