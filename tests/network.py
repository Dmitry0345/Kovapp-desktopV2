import os
import re
import socket
import tempfile
import time
import zlib
from pathlib import Path
from urllib.parse import urlparse

from kovtest import case
from kovtest.config import env, env_int
from kovtest.flows import file_input, wait_for, wait_tile

SHORT = env_int("NETWORK_SHORT_OUTAGE_S", 6)
LONG = env_int("NETWORK_LONG_OUTAGE_S", 90)
DELAY_MS = env_int("NETWORK_UPLOAD_DELAY_MS", 1200)
BIG_MB = env_int("NETWORK_FILE_MB", 40)
SESSION_ID = re.compile(r"uploads/sessions/([0-9a-f]{32})")
OFFLINE_EXPECT = env("NETWORK_OFFLINE_EXPECT", "resume").lower()
GRACE = env_int("NETWORK_OFFLINE_GRACE_S", 120)
UPLOAD_URLS = re.compile(r".*/(uploads/|upload(\?|$))")


# ===== подготовка =====

def big_file(t, kind="bin"):
    key = "net_" + kind
    if key not in t.data.cache:
        folder = Path(tempfile.mkdtemp(prefix="kovnet-"))
        if kind == "video":
            import shutil
            import subprocess
            target = folder / "net-video.mp4"
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                return None
            subprocess.run([ffmpeg, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=duration=20:size=1920x1080:rate=30",
                            "-f", "lavfi", "-i", "anoisesrc=d=20", "-vf", "noise=alls=40:allf=t",
                            "-c:v", "libx264", "-b:v", "16M", "-maxrate", "16M", "-bufsize", "32M", "-pix_fmt", "yuv420p",
                            "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(target)], check=True)
        else:
            target = folder / "net-archive.zip"
            with open(target, "wb") as handle:
                for _ in range(BIG_MB):
                    handle.write(os.urandom(1024 * 1024))
        t.data.cache[key] = target
    return t.data.cache[key]


def open_stage(t, role="worker_mont2", engine=""):
    task = t.data.task("mont")
    page = t.page(role, "light", "desktop", path="#/tasks/%d" % task["id"], engine=engine)
    page.raw.click(".content [data-key='stage']")
    page.raw.wait_for_timeout(400)
    page.raw.evaluate("ms => { window.__kovUploadDelay = ms; }", DELAY_MS)
    return page, task


def recorded(page):
    try:
        return page.raw.evaluate("() => window.__kovUploads || []")
    except Exception:
        return []


def session_ids(entries):
    ids = []
    for entry in entries:
        found = SESSION_ID.search(entry.get("url") or "")
        if found and found.group(1) not in ids:
            ids.append(found.group(1))
    return ids


def chunk_offsets(entries):
    out = []
    for entry in entries:
        url = entry.get("url") or ""
        found = re.search(r"offset=(\d+)", url)
        if entry.get("method") == "PUT" and found and SESSION_ID.search(url):
            out.append(int(found.group(1)))
    return out


def restarted(entries):
    seq = chunk_offsets(entries)
    first = next((i for i, o in enumerate(seq) if o > 0), None)
    return first is not None and any(o == 0 for o in seq[first:])


def add_button_state(page):
    return page.raw.evaluate("""() => {
        const all = [...document.querySelectorAll('.file-block button')].filter(b => b.textContent.includes('Добавить файлы'));
        if (!all.length) return 'none';
        return all.some(b => !b.disabled) ? 'enabled' : 'disabled'; }""")


def chunk_bytes(entries):
    total = 0
    for entry in entries:
        if entry.get("method") == "PUT" and SESSION_ID.search(entry.get("url") or ""):
            total += sum(int(p.get("size") or 0) for p in entry.get("parts") or [])
    return total


def wait_chunks(page, count=2, timeout=120.0):
    return wait_for(page, """n => (window.__kovUploads || []).filter(e => e.method === 'PUT'
        && /uploads\\/sessions\\/[0-9a-f]{32}/.test(e.url)).length >= n""", timeout, count)


def uploading(page):
    return page.raw.evaluate("() => { const w = document.querySelector('.file-block .upload-progress'); return !!(w && !w.hidden); }")


def wait_upload_end(page, timeout):
    return wait_for(page, "() => { const w = document.querySelector('.file-block .upload-progress'); return !w || w.hidden; }", timeout)


def cut(route):
    try:
        route.abort("connectionreset")
    except Exception:
        route.abort()


def outage(page, seconds, kind="connection"):
    page.outage_started()
    if kind == "offline":
        page.context.set_offline(True)
        page.raw.wait_for_timeout(int(seconds * 1000))
        page.context.set_offline(False)
    else:
        page.context.route(UPLOAD_URLS, cut)
        page.raw.wait_for_timeout(int(seconds * 1000))
        page.context.unroute(UPLOAD_URLS, cut)
    page.outage_ended()


def stage_files(t, role, task):
    return t.api(role).call("GET", "tasks/%d/stages/%d/files" % (task["id"], task["stage_details"][0]["id"]))


def session_alive(t, role, sid):
    response = t.api(role).get("uploads/sessions/%s" % sid)
    return response.status_code == 200, (response.json() if response.status_code == 200 else None)


def disk_leftovers(sid):
    root = env("UPLOAD_SESSIONS_DIR", "")
    if not root or not Path(root).is_dir():
        return None
    folder = Path(root) / sid
    if not folder.exists():
        return 0
    return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())


def check_no_leftovers(t, role, sids, what):
    for sid in sids:
        alive, state = session_alive(t, role, sid)
        t.check(not alive, "%s: на сервере остался сеанс загрузки %s с %s байт — куски файла не удалены" % (
            what, sid, (state or {}).get("received")))
        left = disk_leftovers(sid)
        if left is not None:
            t.check(left == 0, "%s: в папке сеансов на диске сервера остались куски файла %s (%d байт)" % (what, sid, left))


def check_single_tile(t, page, stem):
    count = page.raw.evaluate("n => [...document.querySelectorAll('.file-tile .tile-name')].filter(e => e.textContent.includes(n)).length", stem)
    t.check(count == 1, "плитка файла показана %d раз, ожидалась одна" % count)


def show_category(page, key):
    if page.raw.locator(".file-block [data-key='%s']" % key).count():
        page.raw.click(".file-block [data-key='%s']" % key)
        page.raw.wait_for_timeout(300)


# ===== короткий обрыв =====

def upload_with_outage(t, kind, seconds, engine="", mode="connection"):
    page, task = open_stage(t, engine=engine)
    source = big_file(t, kind)
    if source is None:
        t.skip("нет ffmpeg — видео для проверки не собрать")
    stamp = "%s-%d" % (page.engine, int(time.time() * 1000) % 1000000)
    target = source.with_name(source.stem + "-" + stamp + source.suffix)
    if not target.exists():
        target.write_bytes(source.read_bytes() + os.urandom(64))
    file_input(page).set_input_files(str(target))
    t.require(wait_chunks(page, 2, 300), "отправка частями не началась — файл %d МБ ушёл одним запросом или не ушёл" % (
        target.stat().st_size // 1048576))
    sent_before = chunk_bytes(recorded(page))
    t.log.info("%s на %d с после %d байт" % ("сеть устройства выключена" if mode == "offline" else "связь с сервером оборвана",
                                            seconds, sent_before))
    outage(page, seconds, mode)
    if mode == "offline":
        return page, task, target, recorded(page)
    finished = wait_upload_end(page, max(240, seconds * 3))
    t.require(finished, "после восстановления сети загрузка не завершилась")
    entries = recorded(page)
    return page, task, target, entries


def verify_upload(t, page, task, target, entries, role="worker_mont2", compare=True):
    stem = target.stem
    show_category(page, "videos" if target.suffix == ".mp4" else "others")
    if not wait_tile(page, stem, 15):
        show_category(page, "documents")
    t.check(wait_tile(page, stem, 15), "после обрыва файл не появился в списке")
    check_single_tile(t, page, stem)
    stored = [f for f in stage_files(t, role, task) if f["filename"].startswith(stem)]
    t.check(len(stored) == 1, "на сервере %d записей файла вместо одной" % len(stored))
    if stored and compare:
        body = t.api(role).get("stages/files/%d/download" % stored[0]["id"], timeout=600).content
        t.check(body == target.read_bytes(), "файл на сервере отличается от отправленного (%d и %d байт)" % (
            len(body), target.stat().st_size))
    t.check(not restarted(entries), "после обрыва отправка началась с нуля, а не с места обрыва (смещения: %s)" % (
        chunk_offsets(entries)[:30]))
    check_no_leftovers(t, role, session_ids(entries), "после успешной загрузки")
    page.forget_network_noise()
    page.assert_clean("обрыв сети")


@case("Короткий обрыв связи при отправке большого файла: загрузка продолжается с места обрыва", layer="ui",
      matrix={"engine": "engines"}, order=1)
def short_drop_file(t):
    page, task, target, entries = upload_with_outage(t, "bin", SHORT)
    verify_upload(t, page, task, target, entries)


@case("Короткий обрыв связи при отправке видео", layer="ui", order=2)
def short_drop_video(t):
    page, task, target, entries = upload_with_outage(t, "video", SHORT)
    verify_upload(t, page, task, target, entries, compare=False)


@case("Долгий обрыв связи: после возвращения загрузка завершается", layer="ui", order=3)
def long_drop_resume(t):
    page, task, target, entries = upload_with_outage(t, "bin", LONG)
    verify_upload(t, page, task, target, entries)


def start_upload(t, label):
    page, task = open_stage(t)
    source = big_file(t, "bin")
    target = source.with_name("%s-%s-%s-%d%s" % (source.stem, label, page.engine, int(time.time() * 1000) % 1000000, source.suffix))
    target.write_bytes(source.read_bytes() + os.urandom(64))
    file_input(page).set_input_files(str(target))
    t.require(wait_chunks(page, 2, 300), "отправка частями не началась")
    return page, task, target


def collect_toasts(page, seconds, seen):
    deadline = time.time() + seconds
    while time.time() < deadline:
        page.raw.wait_for_timeout(250)
        seen.extend(x for x in page.toast_texts() if x not in seen)
    return seen


def check_clean_abort(t, page, task, target, what):
    t.check(wait_upload_end(page, 20), "%s: полоса загрузки осталась висеть" % what)
    t.check(not page.raw.evaluate("n => [...document.querySelectorAll('.file-tile .tile-name')].some(e => e.textContent.includes(n))",
                                  target.stem), "%s: на экране осталась плитка недогруженного файла" % what)
    state = add_button_state(page)
    if state == "none":
        page.raw.click(".content [data-key='stage']")
        page.raw.wait_for_timeout(500)
        state = add_button_state(page)
    t.check(state == "enabled", "%s: кнопка «Добавить файлы» недоступна (%s)" % (what, state))


def check_server_clean(t, page, task, target, what, wait_s=30):
    deadline = time.time() + wait_s
    sids = session_ids(recorded(page))
    while time.time() < deadline and any(session_alive(t, "worker_mont2", sid)[0] for sid in sids):
        time.sleep(2)
    stored = [f for f in stage_files(t, "worker_mont2", task) if f["filename"].startswith(target.stem)]
    t.check(not stored, "%s: на сервере появилась запись недогруженного файла" % what)
    check_no_leftovers(t, "worker_mont2", sids, what)


@case("Сеть устройства пропала на несколько секунд: отправка продолжается, когда сеть вернулась", layer="ui",
      matrix={"engine": "engines"}, order=4)
def device_offline(t):
    page, task, target = start_upload(t, "offline")
    page.outage_started()
    page.context.set_offline(True)
    toasts = collect_toasts(page, 2, [])
    page.raw.wait_for_timeout(max(0, SHORT * 1000 - 2000))
    page.context.set_offline(False)
    page.outage_ended()
    collect_toasts(page, 2, toasts)
    joined = " | ".join(toasts)
    if OFFLINE_EXPECT == "abort":
        check_clean_abort(t, page, task, target, "после пропадания сети")
        check_server_clean(t, page, task, target, "после прерывания из-за пропадания сети")
    else:
        t.check("Связь пропала" in joined, "человеку не сказали, что связь пропала (сообщения: %s)" % (joined or "нет"))
        t.require(wait_upload_end(page, 300), "после возвращения сети загрузка не завершилась")
        verify_upload(t, page, task, target, recorded(page))
    page.forget_network_noise()
    page.assert_clean("пропадание сети устройства")


@case("Сети нет дольше допустимого: отправка отменяется, куски удаляются", layer="ui", order=7)
def device_offline_long(t):
    page, task, target = start_upload(t, "offline-long")
    page.outage_started()
    page.context.set_offline(True)
    toasts = collect_toasts(page, GRACE + 15, [])
    joined = " | ".join(toasts)
    t.check("Связь пропала" in joined, "человеку не сказали, что связь пропала")
    t.check("отменена" in joined, "через %d с без сети отправка не отменилась (сообщения: %s)" % (GRACE + 15, joined or "нет"))
    check_clean_abort(t, page, task, target, "после долгого отсутствия сети")
    page.context.set_offline(False)
    page.outage_ended()
    check_server_clean(t, page, task, target, "после отмены из-за долгого отсутствия сети")
    page.forget_network_noise()
    page.assert_clean("долгое отсутствие сети")


@case("Сервер недоступен дольше допустимого: отправка отменяется, куски удаляются", layer="ui", order=8)
def long_drop_abort(t):
    page, task, target = start_upload(t, "stall")
    page.outage_started()
    page.context.route(UPLOAD_URLS, cut)
    toasts = collect_toasts(page, GRACE + 25, [])
    joined = " | ".join(toasts)
    t.check("не вернулась" in joined or "отменена" in joined,
            "через %d с без связи с сервером отправка не отменилась (сообщения: %s)" % (GRACE + 25, joined or "нет"))
    check_clean_abort(t, page, task, target, "после долгого обрыва связи")
    restore(page)
    check_server_clean(t, page, task, target, "после отмены из-за долгого обрыва связи")
    page.forget_network_noise()
    page.assert_clean("долгий обрыв связи")


@case("Сеть пропала во время отправки файла в чат: отправка продолжается, когда сеть вернулась", layer="ui",
      matrix={"engine": "engines"}, order=9)
def chat_offline(t):
    # компьютер: на телефоне вложения идут через шторку — это проверяет device_upload на настоящем Android
    group = t.data.group_chat("director", ["worker_mont2"], "Обрыв в чате")
    page = t.page("worker_mont2", "light", "desktop", path="#/messenger/%d" % group["id"])
    t.require(wait_for(page, "() => !!document.querySelector('#btnAttach')", 15), "окно чата не открылось")
    page.raw.evaluate("ms => { window.__kovUploadDelay = ms; }", DELAY_MS)
    source = big_file(t, "bin")
    target = source.with_name("%s-chat-%s-%d%s" % (source.stem, page.engine, int(time.time() * 1000) % 1000000, source.suffix))
    target.write_bytes(source.read_bytes() + os.urandom(64))
    if page.device == "mobile":
        page.raw.click("#btnAttach")
        page.raw.wait_for_timeout(400)
        items = page.raw.locator(".modal-root button, .kov-sheet button, .sheet button", has_text="Файл")
        t.require(items.count(), "на телефоне нет пункта «Файл» в меню вложений")
        with page.raw.expect_file_chooser() as chooser:
            items.first.click()
    else:
        with page.raw.expect_file_chooser() as chooser:
            page.raw.click("#btnAttach")
    chooser.value.set_files(str(target))
    t.require(wait_for(page, "() => [...document.querySelectorAll('.modal-root button')].some(b => b.textContent.includes('Отправить'))", 15),
              "окно отправки файлов не открылось")
    page.raw.locator(".modal-root .modal-foot button", has_text="Отправить").last.click()
    t.require(wait_chunks(page, 2, 300), "отправка частями не началась")
    page.outage_started()
    page.context.set_offline(True)
    page.raw.wait_for_timeout(SHORT * 1000)
    page.context.set_offline(False)
    page.outage_ended()
    stored = []
    deadline = time.time() + 240
    while time.time() < deadline and not stored:
        history = t.api("worker_mont2").call("GET", "chat/history/%d" % group["id"])
        stored = [m for m in history if (m.get("file_name") or "").startswith(target.stem)
                  or any((f.get("filename") or "").startswith(target.stem) for f in (m.get("files") or []))]
        if not stored:
            time.sleep(2)
    t.check(stored, "после возвращения сети файл так и не дошёл до чата — отправку оборвали")
    t.check(not restarted(recorded(page)), "после обрыва отправка началась с нуля, а не с места обрыва")
    check_no_leftovers(t, "worker_mont2", session_ids(recorded(page)), "после отправки в чат")
    page.forget_network_noise()
    page.assert_clean("обрыв сети при отправке в чат")


# ===== отмена и уход со страницы во время обрыва =====

def start_and_drop(t):
    page, task = open_stage(t)
    source = big_file(t, "bin")
    target = source.with_name(source.stem + "-cancel-%d" % (int(time.time() * 1000) % 1000000) + source.suffix)
    target.write_bytes(source.read_bytes() + os.urandom(64))
    file_input(page).set_input_files(str(target))
    t.require(wait_chunks(page, 2, 300), "отправка частями не началась")
    page.outage_started()
    page.context.route(UPLOAD_URLS, cut)
    page.raw.wait_for_timeout(3000)
    return page, task, target


def restore(page):
    try:
        page.context.unroute(UPLOAD_URLS, cut)
    except Exception:
        pass
    page.outage_ended()


def after_abandon(t, page, task, target, what):
    restore(page)
    page.raw.wait_for_timeout(20000)
    entries = recorded(page)
    stored = [f for f in stage_files(t, "worker_mont2", task) if f["filename"].startswith(target.stem)]
    t.check(not stored, "%s: на сервере появилась запись недогруженного файла" % what)
    check_no_leftovers(t, "worker_mont2", session_ids(entries), what)


@case("Отмена загрузки во время обрыва: ни обломка в интерфейсе, ни кусков на сервере", layer="ui", order=5)
def cancel_during_outage(t):
    page, task, target = start_and_drop(t)
    cancel = page.visible_button("Отменить", ".file-block")
    t.require(cancel is not None, "во время загрузки нет кнопки «Отменить»")
    cancel.click()
    t.check(wait_upload_end(page, 10), "после отмены полоса загрузки не исчезла")
    t.check(not page.raw.evaluate("n => [...document.querySelectorAll('.file-tile .tile-name')].some(e => e.textContent.includes(n))",
                                  target.stem), "после отмены на экране осталась плитка недогруженного файла")
    t.check(add_button_state(page) == "enabled", "после отмены кнопка «Добавить файлы» недоступна (%s)" % add_button_state(page))
    after_abandon(t, page, task, target, "после отмены во время обрыва")
    page.forget_network_noise()
    page.assert_clean("отмена во время обрыва")


@case("Уход в другой раздел во время обрыва: отправка продолжается в фоне и доходит целиком", layer="ui", order=6)
def leave_during_outage(t):
    # beta 3: отправка в фоне — уход из задачи её не отменяет; вернулась сеть — файл доходит один раз и целиком
    page, task, target = start_and_drop(t)
    page.raw.evaluate("() => { location.hash = '#/tasks'; }")
    page.raw.wait_for_timeout(1500)
    restore(page)
    stored, deadline = [], time.time() + 120
    while time.time() < deadline:
        stored = [f for f in stage_files(t, "worker_mont2", task) if f["filename"].startswith(target.stem)]
        if stored:
            break
        page.raw.wait_for_timeout(2000)
    t.check(len(stored) == 1, "после ухода в другой раздел файл не дошёл в фоне (записей: %d)" % len(stored))
    if stored:
        size = stored[0].get("size") or stored[0].get("file_size")
        if size:
            t.check(int(size) == target.stat().st_size, "файл дошёл не целиком: %s байт из %d" % (size, target.stat().st_size))
    check_no_leftovers(t, "worker_mont2", session_ids(recorded(page)), "после фоновой отправки")
    page.forget_network_noise()
    page.assert_clean("уход во время обрыва")


# ===== обрыв на уровне сервера =====

def raw_put(base_url, token, sid, offset, declared, sent):
    parsed = urlparse(base_url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    sock = socket.create_connection((parsed.hostname, port), timeout=30)
    if parsed.scheme == "https":
        import ssl
        sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parsed.hostname)
    path = (parsed.path.rstrip("/") + "/uploads/sessions/%s?offset=%d" % (sid, offset))
    head = ("PUT %s HTTP/1.1\r\nHost: %s\r\nAuthorization: Bearer %s\r\nContent-Type: application/octet-stream\r\n"
            "Content-Length: %d\r\nConnection: close\r\n\r\n") % (path, parsed.netloc, token, declared)
    sock.sendall(head.encode() + sent)
    time.sleep(0.5)
    sock.close()


@case("Оборванная посреди порции передача не портит файл на сервере", layer="api", order=10)
def torn_chunk(t):
    import json
    task = t.data.task("mont")
    api = t.api("director")
    data = os.urandom(2 * 1048576 + 777)
    session = api.call("POST", "uploads/sessions", json={"name": "torn-autotest.bin", "size": len(data), "kind": "file"})
    sid = session["id"]
    first = data[:1048576]
    api.call("PUT", "uploads/sessions/%s?offset=0" % sid, data=first,
             headers={"x-chunk-crc32": str(zlib.crc32(first)), "Content-Type": "application/octet-stream"})
    raw_put(t.settings.base_url, api.token, sid, 1048576, 1048576, data[1048576:1048576 + 300000])
    time.sleep(1.0)
    status = api.call("GET", "uploads/sessions/%s" % sid)
    received = int(status.get("received") or 0)
    t.check(received in (1048576, 1048576 + 300000), "после обрыва сервер насчитал %d байт — порция записалась частично и неровно" % received)
    rest = data[received:]
    response = api.put("uploads/sessions/%s?offset=%d" % (sid, received), data=rest,
                       headers={"x-chunk-crc32": str(zlib.crc32(rest)), "Content-Type": "application/octet-stream"})
    t.expect_status(response, 200, "продолжение после обрыва")
    stale = api.put("uploads/sessions/%s?offset=0" % sid, data=first,
                    headers={"x-chunk-crc32": str(zlib.crc32(first)), "Content-Type": "application/octet-stream"})
    t.check(stale.status_code in (200, 409), "повтор уже принятой порции даёт ответ %s" % stale.status_code)
    api.call("POST", "uploads/sessions/%s/complete" % sid, json={"size": len(data)})
    meta = json.dumps([{"src": "session", "session": sid, "name": "torn-autotest.bin"}])
    item = api.call("POST", "upload", data={"task_id": str(task["id"]), "meta": meta})[0]
    body = api.get("task/files/%d/download" % item["id"]).content
    t.check(body == data, "файл после оборванной порции собран с ошибкой")
    t.expect_status(api.get("uploads/sessions/%s" % sid), 404, "сеанс после прикрепления файла")


@case("Брошенный сеанс удаляется по запросу клиента", layer="api", order=11)
def abandoned_session(t):
    api = t.api("director")
    data = os.urandom(1048576)
    sid = api.call("POST", "uploads/sessions", json={"name": "abandoned.bin", "size": 5 * 1048576, "kind": "file"})["id"]
    api.call("PUT", "uploads/sessions/%s?offset=0" % sid, data=data,
             headers={"x-chunk-crc32": str(zlib.crc32(data)), "Content-Type": "application/octet-stream"})
    api.call("DELETE", "uploads/sessions/%s" % sid)
    t.expect_status(api.get("uploads/sessions/%s" % sid), 404, "удалённый сеанс")
    left = disk_leftovers(sid)
    if left is not None:
        t.check(left == 0, "после удаления сеанса на диске остались куски (%d байт)" % left)
