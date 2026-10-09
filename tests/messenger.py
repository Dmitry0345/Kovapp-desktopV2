import time

from kovtest import case
from kovtest.flows import wait_for


def shared_group(t):
    if "msg_group" not in t.data.cache:
        t.data.cache["msg_group"] = t.data.group_chat("director", ["worker_mont", "head_buh"], "Мессенджер")
    return t.data.cache["msg_group"]


# ===== сервер =====

@case("Личный чат, группа, участники, история", layer="api", order=1)
def chats_api(t):
    private = t.data.private_chat("worker_mont", "worker_to")
    t.check(private.get("id"), "личный чат не создан")
    again = t.data.private_chat("worker_to", "worker_mont")
    t.check(again.get("id") == private.get("id"), "повторный личный чат создал дубль")
    group = shared_group(t)
    info = t.api("worker_mont").call("GET", "chat/group/%d" % group["id"])
    ids = {p.get("id") or p.get("user_id") for p in info.get("participants") or []}
    t.check(t.account("worker_mont").id in ids and t.account("head_buh").id in ids, "участники группы не совпадают")
    chats = t.api("head_buh").call("GET", "chat/list")
    t.check(any(c["id"] == group["id"] for c in chats), "группа не видна участнику в списке чатов")
    t.expect_status(t.api("worker_to").get("chat/history/%d" % group["id"]), 403, "история группы для постороннего")


@case("Вложения чата: фото с миниатюрой и документ", layer="api", order=2)
def chat_files_api(t):
    group = shared_group(t)
    sent = t.data.send_chat_files("director", group["id"], ("jpg", "pdf"))
    files = sent.get("files") or []
    t.require(len(files) == 2, "отправлено %d файлов из 2" % len(files))
    api = t.api("head_buh")
    for item in files:
        t.expect_status(api.get_file("chat/download/%d" % item["id"]), 200, "скачивание вложения %s" % item.get("filename"))
        t.expect_status(api.get_file("file_storage/" + item["filepath"]), 200, "вложение по пути %s" % item.get("filename"))



def tree_hash(data: bytes, leaf: int = 4 * 1024 * 1024) -> str:
    """Отпечаток файла, как его считают браузер и сервер (для повтора без отправки)."""
    import hashlib
    leaves = hashlib.sha256()
    for start in range(0, len(data), leaf):
        leaves.update(hashlib.sha256(data[start:start + leaf]).digest())
    leaves.update(len(data).to_bytes(8, "big"))
    return leaves.hexdigest()


def same_file(t, api, sent: dict, data: bytes, what: str) -> None:
    files = sent.get("files") or []
    if not t.check(len(files) == 1, "%s: в сообщении %d файлов вместо 1" % (what, len(files))):
        return
    got = api.get_file("chat/download/%d" % files[0]["id"])
    t.check(got.status_code == 200 and got.content == data,
            "%s: файл из чата отличается от отправленного (ответ %s, %d байт из %d)"
            % (what, got.status_code, len(got.content), len(data)))


@case("Большой файл частями и повтор файла доходят до чата", layer="api", order=3)
def chat_big_file_and_repeat(t):
    import json
    import os
    import zlib
    group = shared_group(t)
    api = t.api("director")
    reader = t.api("head_buh")
    data = os.urandom(3 * 1024 * 1024 + 4321)
    # файл частями — тем же путём, что большие файлы из браузера (больше 32 МБ)
    session = api.call("POST", "uploads/sessions", json={"name": "chat-big-autotest.zip", "size": len(data), "kind": "file"})
    sid = session["id"]
    step = min(int(session.get("chunk_max") or 1048576), 1048576)
    offset = 0
    while offset < len(data):
        part = data[offset:offset + step]
        response = api.put("uploads/sessions/%s?offset=%d" % (sid, offset), data=part,
                           headers={"x-chunk-crc32": str(zlib.crc32(part)), "Content-Type": "application/octet-stream"})
        t.require(response.status_code == 200, "порция с %d не принята: %s" % (offset, response.status_code))
        offset += len(part)
    done = api.call("POST", "uploads/sessions/%s/complete" % sid, json={"size": len(data)})
    t.check(done.get("tree") == tree_hash(data), "сервер посчитал другой отпечаток файла")
    meta = json.dumps([{"src": "session", "session": sid, "name": "chat-big-autotest.zip"}])
    first = api.post("chat/grouped-message", data={"chat_id": str(group["id"]), "meta": meta})
    t.expect_status(first, 200, "файл частями в чат")
    if first.status_code == 200:
        same_file(t, reader, first.json(), data, "файл частями")
    # тот же файл ещё раз — сервер узнаёт повтор и берёт копию без отправки
    challenge = api.call("POST", "uploads/dedup/check", json={"tree": tree_hash(data), "size": len(data)})
    token = api.call("POST", "uploads/dedup/answer", json={"id": challenge["id"], "hashes": []}).get("token")
    t.require(token, "сервер не узнал уже отправленный файл — повтор без отправки невозможен")
    meta = json.dumps([{"src": "dedup", "token": token, "name": "chat-repeat-autotest.zip"}])
    again = api.post("chat/grouped-message", data={"chat_id": str(group["id"]), "meta": meta})
    t.expect_status(again, 200, "повтор того же файла в чат без отправки")
    if again.status_code == 200:
        same_file(t, reader, again.json(), data, "повтор файла")


# ===== интерфейс =====

@case("Сообщение отправляется из окна чата", layer="ui",
      matrix={"engine": "engines", "theme": "themes", "device": "devices"}, order=10)
def send_message_ui(t):
    group = shared_group(t)
    page = t.page("head_buh", t.params["theme"], t.params["device"], path="#/messenger/%d" % group["id"])
    raw = page.raw
    t.require(wait_for(page, "() => !!document.querySelector('#messageInput') && document.querySelector('#messageInput').offsetParent !== null", 15),
              "поле ввода сообщения не показано")
    text = "Сообщение автотеста %s %d" % (t.params["device"], int(time.time()) % 100000)
    raw.fill("#messageInput", text)
    raw.keyboard.press("Enter")
    t.check(wait_for(page, "x => [...document.querySelectorAll('.msg-text')].some(e => e.textContent.includes(x))", 10, text),
            "сообщение не появилось в ленте")
    history = t.api("head_buh").call("GET", "chat/history/%d" % group["id"])
    t.check(any((m.get("text") or "") == text for m in history), "сообщение не сохранилось на сервере")
    page.audit("chat_%s_%s" % (t.params["theme"], t.params["device"]))
    page.assert_clean("мессенджер")


@case("Большой файл частями и повтор файла из задачи доходят до чата", layer="api", order=3)
def chat_big_and_repeat(t):
    import json
    import os
    import zlib
    from tests.files import tree_hash
    group = shared_group(t)
    api = t.api("head_buh")
    data = os.urandom(3 * 1024 * 1024 + 4321)
    session = api.call("POST", "uploads/sessions", json={"name": "chat-big-autotest.zip", "size": len(data), "kind": "file"})
    for offset in range(0, len(data), 1048576):
        part = data[offset:offset + 1048576]
        api.call("PUT", "uploads/sessions/%s?offset=%d" % (session["id"], offset), data=part,
                 headers={"x-chunk-crc32": str(zlib.crc32(part)), "Content-Type": "application/octet-stream"})
    api.call("POST", "uploads/sessions/%s/complete" % session["id"], json={"size": len(data)})
    meta = json.dumps([{"src": "session", "session": session["id"], "name": "chat-big-autotest.zip"}])
    response = api.post("chat/grouped-message", data={"chat_id": str(group["id"]), "meta": meta})
    t.expect_status(response, 200, "файл частями в чат")
    files = (response.json() or {}).get("files") or [] if response.status_code == 200 else []
    t.require(files, "сервер не вернул сохранённый файл")
    got = t.api("director").get_file("chat/download/%d" % files[0]["id"])
    t.check(got.content == data, "файл из чата отличается от отправленного частями")
    director = t.api("director")
    task = t.data.task("mont")
    director.call("POST", "upload", data={"task_id": str(task["id"])},
                  files=[("files", ("chat-repeat-source.zip", data, "application/zip"))])
    challenge = director.call("POST", "uploads/dedup/check", json={"tree": tree_hash(data), "size": len(data)})
    token = director.call("POST", "uploads/dedup/answer", json={"id": challenge["id"], "hashes": []}).get("token")
    t.require(token, "сервер не узнал файл, уже лежащий в задаче")
    meta = json.dumps([{"src": "dedup", "token": token, "name": "chat-repeat-autotest.zip"}])
    again = director.post("chat/grouped-message", data={"chat_id": str(group["id"]), "meta": meta})
    t.expect_status(again, 200, "файл из задачи повтором в чат без отправки")
    copies = (again.json() or {}).get("files") or [] if again.status_code == 200 else []
    if copies:
        t.check(t.api("head_buh").get_file("chat/download/%d" % copies[0]["id"]).content == data,
                "повтор файла в чате отличается от исходного")


@case("Новое сообщение не сдвигает ленту, пока человек читает историю: счётчик на кнопке «вниз»", layer="ui",
      matrix={"engine": "engines", "device": "devices"}, order=11)
def unseen_counter_ui(t):
    import uuid
    from kovtest.ws import Socket
    group = t.data.group_chat("director", ["worker_mont2"], "Счётчик новых")
    sock = Socket(t.settings.base_url, t.api("director").token, t.log, who="director", quiet=True)
    try:
        time.sleep(1)
        for i in range(40):
            sock.send({"type": "message", "chat_id": group["id"], "text": "история %d" % i, "client_id": "h" + uuid.uuid4().hex[:10]})
            time.sleep(0.03)
        time.sleep(1.5)
        page = t.page("worker_mont2", "light", t.params["device"], path="#/messenger/%d" % group["id"])
        t.require(wait_for(page, "() => document.querySelectorAll('.msg-text').length >= 20", 20), "история чата не показалась")
        page.raw.wait_for_timeout(2500)
        before = page.raw.evaluate("""() => { const m = document.getElementById('messagesList');
            m.scrollTop = Math.max(120, Math.round((m.scrollHeight - m.clientHeight) / 2));
            m.dispatchEvent(new Event('scroll')); return m.scrollTop; }""")
        page.raw.wait_for_timeout(600)
        before = page.raw.evaluate("() => document.getElementById('messagesList').scrollTop")
        text = "новое сообщение %d" % (int(time.time()) % 100000)
        sock.send({"type": "message", "chat_id": group["id"], "text": text, "client_id": "n" + uuid.uuid4().hex[:10]})
        t.require(wait_for(page, "x => [...document.querySelectorAll('.msg-text')].some(e => e.textContent.includes(x))", 10, text),
                  "новое сообщение не пришло в ленту")
        page.raw.wait_for_timeout(1200)
        after = page.raw.evaluate("() => document.getElementById('messagesList').scrollTop")
        t.check(abs(after - before) <= 3, "лента сдвинулась с %s на %s, хотя человек читал историю" % (before, after))
        badge = page.raw.evaluate("() => { const b = document.querySelector('#scrollToBottomBtn .unseen-count'); return b && !b.hidden ? b.textContent : ''; }")
        t.check(badge == "1", "на кнопке «вниз» нет счётчика новых сообщений (%r)" % badge)
        page.raw.click("#scrollToBottomBtn")
        t.check(wait_for(page, "() => { const m = document.getElementById('messagesList'); return m.scrollHeight - m.scrollTop <= m.clientHeight + 10; }", 5),
                "кнопка «вниз» не привела к новым сообщениям")
        t.check(wait_for(page, "() => { const b = document.querySelector('#scrollToBottomBtn .unseen-count'); return !b || b.hidden; }", 5),
                "счётчик не сбросился внизу ленты")
        page.assert_clean("счётчик новых сообщений")
    finally:
        sock.close()


@case("Счётчик на кнопке «вниз» прибавляет по одному и после выхода со входом, и после возврата сети", layer="ui",
      matrix={"engine": "engines"}, order=12)
def unseen_counter_once_ui(t):
    import uuid
    from kovtest.ws import Socket
    group = t.data.group_chat("director", ["worker_mont2"], "Счётчик по одному")
    sock = Socket(t.settings.base_url, t.api("director").token, t.log, who="director", quiet=True)
    badge_js = "() => { const b = document.querySelector('#scrollToBottomBtn .unseen-count'); return b && !b.hidden ? b.textContent : ''; }"
    try:
        time.sleep(1)
        for i in range(40):
            sock.send({"type": "message", "chat_id": group["id"], "text": "история %d" % i, "client_id": "h" + uuid.uuid4().hex[:10]})
            time.sleep(0.03)
        time.sleep(1.5)
        page = t.page("worker_mont2", "light", "desktop", path="#/tasks")
        raw = page.raw
        # выход и вход на той же странице: раньше подписка на события удваивалась
        raw.evaluate("() => { Kov.App.logout(); }")
        raw.locator(".modal-root .modal-foot button", has_text="Выйти").click()
        t.require(wait_for(page, "() => !!document.querySelector('.login-screen .login-card')", 10), "после выхода не показан экран входа")
        account = t.account("worker_mont2")
        raw.fill(".login-card input[placeholder='Логин']", account.login)
        raw.fill(".login-card input[type=password]", account.password)
        raw.keyboard.press("Enter")
        t.require(wait_for(page, "() => !!document.querySelector('.app .top-bar .menu-btn') && Kov.WS.connected", 15),
                  "после входа приложение не открылось или нет связи с сервером")
        # запросы, оборванные выходом (401 без токена), — не предмет этой проверки
        page.console.clear()
        page.http_errors.clear()
        # сеть «вернулась»: раньше рядом с живым сокетом открывался второй
        raw.evaluate("() => window.dispatchEvent(new Event('online'))")
        raw.wait_for_timeout(300)
        t.require(wait_for(page, "() => Kov.WS.connected", 10), "после возврата сети нет связи с сервером")
        raw.evaluate("r => { location.hash = r; }", "#/messenger/%d" % group["id"])
        t.require(wait_for(page, "() => document.querySelectorAll('.msg-text').length >= 20", 20), "история чата не показалась")
        raw.wait_for_timeout(2500)
        raw.evaluate("""() => { const m = document.getElementById('messagesList');
            m.scrollTop = Math.max(120, Math.round((m.scrollHeight - m.clientHeight) / 2));
            m.dispatchEvent(new Event('scroll')); }""")
        raw.wait_for_timeout(600)
        for n in (1, 2):
            text = "по одному %d-%d" % (n, int(time.time()) % 100000)
            sock.send({"type": "message", "chat_id": group["id"], "text": text, "client_id": "o" + uuid.uuid4().hex[:10]})
            t.require(wait_for(page, "x => [...document.querySelectorAll('.msg-text')].some(e => e.textContent.includes(x))", 10, text),
                      "сообщение %d не пришло в ленту" % n)
            raw.wait_for_timeout(1200)
            badge = raw.evaluate(badge_js)
            t.check(badge == str(n), "после %d-го сообщения на кнопке «вниз» %r, ожидалось %d" % (n, badge, n))
        page.assert_clean("счётчик после выхода и входа")
    finally:
        sock.close()

@case("Пересылка части группы: в другой чат уходят только выбранные файлы, без подписи группы", layer="api", order=20)
def chat_forward_part(t):
    group = shared_group(t)
    sent = t.data.send_chat_files("director", group["id"], ("jpg", "jpg", "pdf"), text="Подпись группы")
    files = (sent or {}).get("files") or []
    t.require(len(files) == 3, "отправлено %d файлов из 3" % len(files))
    api = t.api("director")
    hist = api.call("GET", "chat/history/%d" % group["id"])
    msgs = hist if isinstance(hist, list) else (hist or {}).get("messages", [])
    # в истории файлы группы — отдельные записи (special=file_item); пересылают саму группу — запись без file_item
    item = next((m for m in msgs if any(int(f.get("id") or 0) == int(files[0]["id"]) for f in (m.get("files") or []))), None)
    src = next((m for m in msgs if item and item.get("group_id") and m.get("group_id") == item.get("group_id")
                and m.get("special") != "file_item"), None)
    t.require(src is not None, "отправленная группа не найдена в истории чата")
    target = t.data.group_chat("director", ["head_buh"], "Часть группы")
    api.call("POST", "chat/message/%d/forward" % int(src["id"]), json={"chat_ids": [target["id"]], "file_ids": [int(files[1]["id"])]})
    got = []
    deadline = time.time() + 20
    while not got and time.time() < deadline:   # пересылка доходит не мгновенно
        h = api.call("GET", "chat/history/%d" % target["id"])
        got = [m for m in (h if isinstance(h, list) else (h or {}).get("messages", [])) if m.get("files")]
        if not got:
            time.sleep(1)
    last = max(got or [{}], key=lambda m: int(m.get("id") or 0))
    names = [f.get("filename") or f.get("file_name") for f in (last.get("files") or [])]
    t.check(len(names) == 1 and names[0] == (files[1].get("filename") or files[1].get("file_name")), "пересланы файлы: %s" % names)
    t.check(not str(last.get("text") or "").strip(), "подпись группы перенеслась в часть: %r" % last.get("text"))
