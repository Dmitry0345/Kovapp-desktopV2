import os
import time

from kovtest import case
from kovtest.flows import (VIEWER_DOWNLOAD, VIEWER_IMG, VIEWER_VIDEO, download_via, open_task_zone, tile, tile_action,
                           tile_thumb_painted, upload_through_ui, wait_for, wait_tile)

KINDS = ("jpg", "png", "mp4", "pdf", "docx", "xlsx", "txt")


# ===== подготовка =====

def uploaded(t):
    if "files_set" not in t.data.cache:
        task = t.data.task("mont")
        stage = task["stage_details"][0]
        items = t.data.upload("worker_mont", KINDS, task_id=task["id"], stage_id=stage["id"])
        by_kind = {}
        for kind, item in zip(KINDS, items):
            by_kind[kind] = item
        t.data.cache["files_set"] = by_kind
    return t.data.cache["files_set"]


def stem(kind, t):
    return os.path.splitext(t.world.media.get(kind)[0])[0]


# ===== сервер =====

@case("Загрузка файлов всех типов в этап", layer="api", order=1)
def upload_all_kinds(t):
    items = uploaded(t)
    for kind in KINDS:
        item = items.get(kind)
        t.check(item and item.get("id"), "файл %s не загрузился" % kind)
        if item:
            t.check(item.get("size", 0) > 0, "у файла %s нулевой размер" % kind)
            t.check(stem(kind, t) in item.get("filename", ""), "имя файла %s изменилось неожиданно: %s" % (kind, item.get("filename")))


@case("Миниатюры фото и видео отдаются как JPEG", layer="api", order=2)
def thumbnails(t):
    items = uploaded(t)
    api = t.api("head_mont")
    for kind in ("jpg", "png", "mp4"):
        response = api.get("stages/files/%d/thumb" % items[kind]["id"])
        t.expect_status(response, 200, "миниатюра %s" % kind)
        if response.status_code == 200:
            t.check(response.headers.get("content-type", "").startswith("image/"), "миниатюра %s не картинка: %s" % (
                kind, response.headers.get("content-type")))
            t.check(len(response.content) > 500, "миниатюра %s подозрительно маленькая: %d байт" % (kind, len(response.content)))


@case("Просмотр фото, PDF, текста; документ Office — исходным файлом для устройства", layer="api", order=3)
def view_files(t):
    items = uploaded(t)
    api = t.api("worker_mont2")
    expectations = {"jpg": "image/", "png": "image/", "pdf": "application/pdf", "txt": "text/plain"}
    for kind, prefix in expectations.items():
        response = api.get("files/%d/view?is_stage=true" % items[kind]["id"])
        t.expect_status(response, 200, "просмотр %s" % kind)
        if response.status_code == 200:
            ctype = response.headers.get("content-type", "")
            t.check(ctype.startswith(prefix), "просмотр %s: тип %s, ожидался %s…" % (kind, ctype, prefix))
            t.check("inline" in response.headers.get("content-disposition", "inline"),
                    "просмотр %s отдаётся как вложение, браузер его скачает" % kind)
    # документ Office рисует само устройство (docx-view.js): сервер отдаёт исходный файл
    raw = api.get("stages/files/%d/download" % items["docx"]["id"])
    t.expect_status(raw, 200, "DOCX для показа на устройстве")
    if raw.status_code == 200:
        t.check(raw.content[:2] == b"PK", "DOCX отдан не исходным файлом (нет подписи ZIP)")
    # запасной просмотр через сервер: LibreOffice на сервере не нужен — допустим честный отказ 415
    office = api.get("files/%d/view?is_stage=true" % items["docx"]["id"])
    t.check(office.status_code in (200, 415),
            "запасной просмотр DOCX через сервер: ответ %s (ожидался 200 или отказ 415)" % office.status_code)
    sheet = api.get("files/%d/sheet?is_stage=true" % items["xlsx"]["id"])
    t.expect_status(sheet, 200, "таблица XLSX для просмотра ячейками")


@case("Видео: потоковая отдача с поддержкой Range", layer="api", order=4)
def video_stream(t):
    item = uploaded(t)["mp4"]
    api = t.api("head_mont")
    name = item["filename"]
    full = api.get("stages/files/%d/stream/%s" % (item["id"], name))
    t.expect_status(full, 200, "видео целиком")
    if full.status_code == 200:
        t.check(full.headers.get("content-type", "").startswith("video/"), "тип видео: %s" % full.headers.get("content-type"))
    part = api.get("stages/files/%d/stream/%s" % (item["id"], name), headers={"Range": "bytes=0-1023"})
    t.expect_status(part, 206, "видео частями (Range)")
    if part.status_code == 206:
        t.check(len(part.content) == 1024, "по Range пришло %d байт вместо 1024" % len(part.content))
        t.check((part.headers.get("content-range") or "").startswith("bytes 0-1023/"), "нет заголовка Content-Range")
    photo = uploaded(t)["jpg"]
    t.expect_status(api.get("stages/files/%d/stream/%s" % (photo["id"], photo["filename"])), 400,
                    "адрес видео для фотографии")


@case("Скачивание: вложение, размер и отсутствие сжатия gzip", layer="api", order=5)
def downloads(t):
    items = uploaded(t)
    api = t.api("worker_mont")
    for kind in KINDS:
        name, original, _ = t.world.media.get(kind)
        response = api.get("stages/files/%d/download" % items[kind]["id"], headers={"Accept-Encoding": "gzip"})
        t.expect_status(response, 200, "скачивание %s" % kind)
        if response.status_code != 200:
            continue
        t.check("attachment" in (response.headers.get("content-disposition") or ""), "скачивание %s без attachment" % kind)
        t.check((response.headers.get("content-encoding") or "identity") != "gzip",
                "файл %s пришёл пересжатым gzip — большие файлы будут тормозить сервер" % kind)
        if kind in ("pdf", "docx", "xlsx", "txt"):
            t.check(response.content == original, "скачанный %s не совпадает с загруженным" % kind)
        else:
            t.check(len(response.content) > 0, "скачанный %s пустой" % kind)


@case("Скачивание большого файла потоком", layer="api", order=6)
def big_download(t):
    task = t.data.task("mont")
    payload = os.urandom(24 * 1024 * 1024)
    item = t.api("director").call("POST", "upload", data={"task_id": str(task["id"])},
                                  files=[("files", ("big-autotest.bin", payload, "application/octet-stream"))], timeout=900)[0]
    started = time.time()
    response = t.api("worker_mont").get("task/files/%d/download" % item["id"], stream=True, timeout=900)
    t.expect_status(response, 200, "скачивание 24 МБ")
    size = 0
    first_byte = None
    for chunk in response.iter_content(1024 * 256):
        if first_byte is None:
            first_byte = time.time() - started
        size += len(chunk)
    elapsed = time.time() - started
    t.check(size == len(payload), "скачано %d байт из %d" % (size, len(payload)))
    t.log.info("большой файл: первый байт через %.2f с, всего %.1f с" % (first_byte or 0, elapsed))
    if first_byte and first_byte > 5:
        t.warn("первый байт большого файла пришёл через %.1f с — отдача не потоковая" % first_byte)
    t.api("director").delete("files/task/%d" % item["id"])


@case("Файл хранилища по пути и его миниатюра", layer="api", order=7)
def by_path(t):
    photo = uploaded(t)["jpg"]
    api = t.api("worker_mont")
    t.expect_status(api.get_file("file_storage/" + photo["filepath"]), 200, "файл по пути")
    thumb = api.get_file("file_storage/" + photo["filepath"].replace(".gz", "") + ".thumb.jpg")
    t.expect_status(thumb, (200, 404), "миниатюра по пути")
    t.expect_status(api.get_file("file_storage/../../etc/passwd"), (400, 403, 404), "выход за пределы хранилища")


@case("Удалённый файл больше не отдаётся", layer="api", order=8)
def deleted_file(t):
    task = t.data.task("mont")
    name, content, ctype = t.world.media.get("txt")
    item = t.api("director").call("POST", "upload", data={"task_id": str(task["id"])},
                                  files=[("files", ("gone-" + name, content, ctype))])[0]
    t.api("director").call("DELETE", "files/task/%d" % item["id"])
    t.expect_status(t.api("director").get("task/files/%d/download" % item["id"]), 404, "скачивание удалённого файла")


# ===== интерфейс =====

def show_category(page, key):
    page.raw.click(".file-block [data-key='%s']" % key)
    page.raw.wait_for_timeout(300)


def open_stage_files(page):
    open_task_zone(page, "stage")      # на телефоне — вкладка «Файлы» внизу


@case("Файлы этапа: загрузка, миниатюры, просмотр фото и видео, скачивание, удаление", layer="ui",
      matrix={"engine": "engines", "theme": "themes", "device": "devices"}, order=20)
def stage_files_ui(t):
    task = t.data.task("mont")
    page = t.page("worker_mont2", t.params["theme"], t.params["device"], path="#/tasks/%d" % task["id"])
    open_stage_files(page)
    stamp = "%s-%s-%s-%d" % (t.params["engine"], t.params["theme"], t.params["device"], int(time.time()) % 100000)
    files = []
    for kind in ("jpg", "mp4", "pdf"):
        name, data, ctype = t.world.media.fresh(kind)
        base, ext = os.path.splitext(name)
        files.append(("%s-%s%s" % (base, stamp, ext), data, ctype))
    t.require(upload_through_ui(page, files), "загрузка через окно задачи не завершилась")
    photo = os.path.splitext(files[0][0])[0]
    video = os.path.splitext(files[1][0])[0]
    document = os.path.splitext(files[2][0])[0]
    show_category(page, "documents")
    t.check(wait_tile(page, document), "плитка «%s» не появилась во вкладке «Документы»" % document)
    show_category(page, "videos")
    t.check(wait_tile(page, video), "плитка «%s» не появилась во вкладке «Видео»" % video)
    t.check(tile_thumb_painted(page, video), "миниатюра видео не отрисовалась")
    show_category(page, "images")
    t.check(wait_tile(page, photo), "плитка «%s» не появилась во вкладке «Изображения»" % photo)
    t.check(tile_thumb_painted(page, photo), "миниатюра фото не отрисовалась")
    page.audit("stage_files_%s" % stamp)
    tile_action(page, photo, "open")
    t.require(wait_for(page, "() => !!document.querySelector('.viewer-root .mv-stage img')", 10), "просмотр фото не открылся")
    t.check(wait_for(page, "() => { const i = document.querySelector('.viewer-root .mv-stage img'); return i && i.complete && i.naturalWidth > 0; }", 15),
            "фото в окне просмотра не отрисовалось")
    t.check(page.image_painted(VIEWER_IMG), "фото в окне просмотра пустое (одноцветное)")
    page.audit("viewer_photo_%s" % stamp)
    name, size = download_via(page, lambda: page.raw.click(VIEWER_DOWNLOAD))
    t.check(size > 0, "скачивание из окна просмотра дало пустой файл")
    page.raw.keyboard.press("Escape")
    page.raw.wait_for_timeout(300)
    show_category(page, "videos")
    tile_action(page, video, "open")
    t.require(wait_for(page, "() => !!document.querySelector('.viewer-root .mv-stage video')", 10), "просмотр видео не открылся")
    if t.world.browsers.detect_h264(page.engine):
        state = page.video_plays(VIEWER_VIDEO)
        t.check(state.get("ok"), "видео не воспроизводится: %s" % state)
    else:
        t.warn("браузер без H.264 — воспроизведение видео не проверено (нужен BROWSER_CHANNEL=chrome)")
    page.audit("viewer_video_%s" % stamp)
    page.raw.keyboard.press("Escape")
    page.raw.wait_for_timeout(300)
    show_category(page, "documents")
    name, size = download_via(page, lambda: tile_action(page, document, "download"))
    t.check(size == len(files[2][1]), "скачанный PDF %d байт, загружали %d" % (size, len(files[2][1])))
    tile_action(page, document, "delete")
    page.raw.locator(".modal-root .modal-foot button", has_text="Удалить").click()
    t.check(wait_for(page, "n => ![...document.querySelectorAll('.file-tile .tile-name')].some(e => e.textContent.includes(n))", 10,
                     document), "удалённый файл остался на экране")
    page.assert_clean("файлы этапа")


@case("Файлы задачи: рядовой участник добавляет файл переносом в зону и удаляет свой", layer="ui",
      matrix={"engine": "engines", "device": "devices"}, order=21)
def task_files_drop_ui(t):
    task = t.data.task("mont")
    page = t.page("worker_mont2", "light", t.params["device"], path="#/tasks/%d" % task["id"])
    open_task_zone(page, "task")
    add = page.visible_button("Добавить файлы", ".content")
    t.require(add is not None and add.is_enabled(), "кнопка «Добавить файлы» в файлах задачи недоступна рядовому участнику")
    name = "task-drop-%s-%s-%d.txt" % (t.params["engine"], t.params["device"], int(time.time()) % 100000)
    # перенос: те же события, что у файла, брошенного мышью из проводника
    page.raw.evaluate("""n => {
        const zone = document.querySelector('.content .file-block');
        const dt = new DataTransfer();
        dt.items.add(new File(['файл задачи от участника'], n, { type: 'text/plain' }));
        for (const type of ['dragenter', 'dragover', 'drop']) {
            zone.dispatchEvent(new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: dt }));
        }
    }""", name)
    show_category(page, "documents")
    t.require(wait_tile(page, name[:-4], 30), "брошенный в зону файл не появился в файлах задачи")
    files = t.api("worker_mont2").call("GET", "tasks/%d" % task["id"]).get("task_files") or []
    t.check(any((f.get("filename") or "").startswith(name[:-4]) for f in files), "файла нет в задаче на сервере")
    tile_action(page, name[:-4], "delete")
    page.raw.locator(".modal-root .modal-foot button", has_text="Удалить").click()
    t.check(wait_for(page, "n => ![...document.querySelectorAll('.file-tile .tile-name')].some(e => e.textContent.includes(n))", 10,
                     name[:-4]), "свой файл задачи не удалился")
    page.assert_clean("файлы задачи у участника")


@case("Медиаконтент проекта: фото и видео задач открываются в просмотре", layer="ui",
      matrix={"engine": "engines", "device": "devices"}, order=21)
def project_media_ui(t):
    project = t.data.project("main")
    key = "project_media_task"
    if key not in t.data.cache:
        task = t.data.create_task("director", "Задача проекта с фото", ["Монтажный отдел"],
                                  responsible=t.data.ids("head_mont"), assigned=t.data.ids("worker_mont"),
                                  project_id=project["id"])
        stage = task["stage_details"][0]
        t.data.upload("worker_mont", ("jpg", "mp4"), task_id=task["id"], stage_id=stage["id"])
        t.data.cache[key] = task
    task = t.data.cache[key]
    page = t.page("head_mont", "light", t.params["device"], path="#/projects/%d/photos" % project["id"])
    t.require(wait_for(page, "() => document.querySelectorAll('.folder-head, .sub-head').length > 0", 15), "папки задач не показаны")
    heads = page.raw.locator(".folder-head")
    for i in range(heads.count()):
        if task["name"][:20] in (heads.nth(i).inner_text() or ""):
            heads.nth(i).click()
            break
    page.raw.wait_for_timeout(500)
    subs = page.raw.locator(".sub-head")
    for i in range(subs.count()):
        if subs.nth(i).is_visible():
            subs.nth(i).click()
            page.raw.wait_for_timeout(300)
    photo = stem("jpg", t)
    if page.raw.locator(".file-block [data-key='images']").count():
        show_category(page, "images")
    t.require(wait_tile(page, photo, 15), "фото задачи не показано в разделе «Медиаконтент»")
    t.check(tile_thumb_painted(page, photo), "миниатюра фото в проекте не отрисовалась")
    tile_action(page, photo, "open")
    t.check(wait_for(page, "() => { const i = document.querySelector('.viewer-root .mv-stage img'); return i && i.complete && i.naturalWidth > 0; }", 15),
            "фото из проекта не открылось в просмотре")
    page.audit("project_media_viewer")
    page.raw.keyboard.press("Escape")
    page.assert_clean("медиаконтент проекта")


# ===== загрузка частями и повтор по содержимому =====

def tree_hash(data: bytes, leaf: int = 4 * 1024 * 1024) -> str:
    import hashlib
    leaves = hashlib.sha256()
    for start in range(0, len(data), leaf):
        leaves.update(hashlib.sha256(data[start:start + leaf]).digest())
    leaves.update(len(data).to_bytes(8, "big"))
    return leaves.hexdigest()


@case("Загрузка частями: порции, проверка, прикрепление к задаче", layer="api", order=9)
def chunked_upload(t):
    import json
    import zlib
    task = t.data.task("mont")
    api = t.api("director")
    data = os.urandom(3 * 1024 * 1024 + 12345)
    session = api.call("POST", "uploads/sessions", json={"name": "chunked-autotest.bin", "size": len(data), "kind": "file"})
    sid = session["id"]
    step = min(int(session.get("chunk_max") or 1048576), 1048576)
    offset = 0
    while offset < len(data):
        part = data[offset:offset + step]
        response = api.put("uploads/sessions/%s?offset=%d" % (sid, offset), data=part,
                           headers={"x-chunk-crc32": str(zlib.crc32(part)), "Content-Type": "application/octet-stream"})
        t.require(response.status_code == 200, "порция с %d не принята: %s" % (offset, response.status_code))
        offset += len(part)
    status = api.call("GET", "uploads/sessions/%s" % sid)
    t.check(status.get("received") == len(data), "сервер получил %s байт из %d" % (status.get("received"), len(data)))
    bad = api.put("uploads/sessions/%s?offset=0" % sid, data=b"x" * 10,
                  headers={"x-chunk-crc32": "1", "Content-Type": "application/octet-stream"})
    t.check(bad.status_code in (400, 409), "порция с неверной контрольной суммой принята: %s" % bad.status_code)
    done = api.call("POST", "uploads/sessions/%s/complete" % sid, json={"size": len(data)})
    t.check(done.get("tree") == tree_hash(data), "сервер посчитал другой отпечаток файла")
    meta = json.dumps([{"src": "session", "session": sid, "name": "chunked-autotest.bin"}])
    item = api.call("POST", "upload", data={"task_id": str(task["id"]), "meta": meta})[0]
    got = t.api("worker_mont").get("task/files/%d/download" % item["id"])
    t.check(got.content == data, "собранный из порций файл не совпадает с отправленным")
    again = api.post("upload", data={"task_id": str(task["id"]), "meta": meta})
    t.check(again.status_code in (404, 409), "сеанс загрузки удалось использовать второй раз: %s" % again.status_code)
    t.expect_status(t.api("worker_mont").get("uploads/sessions/%s" % sid), 404, "чужой сеанс загрузки")


@case("Повтор файла без отправки: только владельцу или с доказательством содержимого", layer="api", order=10)
def dedup_proof(t):
    import hashlib
    import json
    task = t.data.task("mont")
    stage = task["stage_details"][0]
    director, worker = t.api("director"), t.api("worker_mont")
    data = os.urandom(300 * 1024)
    director.call("POST", "upload", data={"task_id": str(task["id"])},
                                      files=[("files", ("dedup-autotest.bin", data, "application/octet-stream"))])
    tree = tree_hash(data)

    def token_for(api, proof=None):
        challenge = api.call("POST", "uploads/dedup/check", json={"tree": tree, "size": len(data)})
        hashes = proof(challenge) if proof else []
        return api.call("POST", "uploads/dedup/answer", json={"id": challenge["id"], "hashes": hashes}).get("token")

    own = token_for(director)
    t.check(own, "владелец содержимого не получил разрешения на повтор без отправки")
    honest = token_for(worker, lambda c: [hashlib.sha256(data[w["o"]:w["o"] + w["l"]]).hexdigest() for w in c["windows"]])
    t.check(honest, "сотрудник с тем же файлом не прошёл проверку содержимого")
    forged = token_for(worker, lambda c: ["0" * 64 for _ in c["windows"]])
    t.check(not forged, "разрешение выдано без доказательства содержимого — чужой файл можно получить по отпечатку")
    stolen = token_for(director)
    meta = json.dumps([{"src": "dedup", "token": stolen, "name": "dedup-stolen.bin"}])
    response = worker.post("upload", data={"task_id": str(task["id"]), "stage_detail_id": str(stage["id"]), "meta": meta})
    t.check(response.status_code == 409, "чужое разрешение на повтор файла сработало: ответ %s" % response.status_code)
    meta = json.dumps([{"src": "dedup", "token": own, "name": "dedup-copy.bin"}])
    item = director.call("POST", "upload", data={"task_id": str(task["id"]), "meta": meta})[0]
    t.check(worker.get("task/files/%d/download" % item["id"]).content == data, "копия по содержимому отличается от исходника")


# ===== удаление и пересылка =====

@case("Пересланный файл остаётся после удаления исходного сообщения и чата", layer="api", order=11)
def forwarded_file_survives(t):
    first = t.data.group_chat("director", ["worker_mont"], "Исходный чат")
    second = t.data.group_chat("director", ["worker_to"], "Чат пересылки")
    sent = t.data.send_chat_files("director", first["id"], ("pdf",))
    item = (sent.get("files") or [None])[0]
    t.require(item, "вложение не отправлено")
    director = t.api("director")
    director.call("POST", "chat/message/%d/forward" % item["id"], json={"chat_ids": [second["id"]]})
    copy_id = None
    for _ in range(10):
        history = t.api("worker_to").call("GET", "chat/history/%d" % second["id"])
        ids = [m["id"] for m in history if m.get("file_path")]
        ids += [f["id"] for m in history for f in (m.get("files") or []) if f.get("id")]
        if ids:
            copy_id = ids[0]
            break
        time.sleep(0.5)
    t.require(copy_id, "пересланное сообщение с файлом не появилось в истории чата")
    director.call("DELETE", "chat/message/%d" % item["id"], expect=(200, 204))
    t.expect_status(t.api("worker_to").get_file("chat/download/%d" % copy_id), 200,
                    "пересланный файл после удаления исходного сообщения")
    director.call("DELETE", "chat/group/%d" % first["id"], expect=(200, 204))
    t.data.groups.pop(first["id"], None)
    t.expect_status(t.api("worker_to").get_file("chat/download/%d" % copy_id), 200,
                    "пересланный файл после удаления исходного чата")


@case("Удалённый файл задачи исчезает и с диска, и из поиска копий", layer="api", order=12)
def deleted_task_file_gone(t):
    task = t.data.task("mont")
    data = os.urandom(300 * 1024)
    item = t.api("director").call("POST", "upload", data={"task_id": str(task["id"])},
                                  files=[("files", ("gone-autotest.bin", data, "application/octet-stream"))])[0]
    t.api("director").call("DELETE", "files/task/%d" % item["id"])
    t.expect_status(t.api("director").get_file("file_storage/" + item["filepath"]), (403, 404), "файл удалённой записи по пути")
    challenge = t.api("director").call("POST", "uploads/dedup/check", json={"tree": tree_hash(data), "size": len(data)})
    token = t.api("director").call("POST", "uploads/dedup/answer", json={"id": challenge["id"], "hashes": []}).get("token")
    t.check(not token, "после удаления файла сервер всё ещё предлагает взять его копию")
