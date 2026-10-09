import gzip
import json
import os
import time

from kovtest import case
from kovtest.browser import KovPage
from kovtest.flows import (VIEWER_IMG, open_task_zone, tile, tile_action, tile_thumb_painted, upload_through_ui, wait_for,
                           wait_tile)


# ===== подготовка =====

def stage_page(t, role="worker_mont2", device="desktop", theme="light"):
    task = t.data.task("mont")
    page = t.page(role, theme, device, path="#/tasks/%d" % task["id"])
    open_task_zone(page, "stage")      # на телефоне — вкладка «Файлы» внизу
    return page, task


def recorded_uploads(page):
    return page.raw.evaluate("() => window.__kovUploads || []")


def uploaded_parts(page, stem):
    files, metas = [], []
    for entry in recorded_uploads(page):
        for part in entry.get("parts") or []:
            if "size" in part and part.get("field") != "thumbs":
                files.append(part)
            if part.get("field") == "meta":
                try:
                    metas.extend(json.loads(part.get("value") or "[]"))
                except ValueError:
                    pass
            if part.get("field") == "body":
                files.append(part)
    mine = [m for m in metas if str(m.get("name") or "").startswith(stem)]
    return files, mine


def unique(name, t):
    base, ext = os.path.splitext(name)
    return "%s-%s-%d%s" % (base, t.params.get("engine", "x"), int(time.time() * 1000) % 1000000, ext)


def show(page, key):
    page.raw.click(".file-block [data-key='%s']" % key)
    page.raw.wait_for_timeout(300)


def find_tile(page, stem):
    for key in ("documents", "others", "images", "videos"):
        if page.raw.locator(".file-block [data-key='%s']" % key).count():
            show(page, key)
            if wait_tile(page, stem, 3):
                return True
    return False


def open_document(t, page, stem):
    with page.context.expect_page(timeout=20000) as info:
        tile_action(page, stem, "open")
    doc = info.value
    doc.wait_for_load_state("domcontentloaded")
    view = KovPage(doc, page.context, page.role, page.theme, page.device, page.browser, t.world, t, page.engine)
    view.opened = True
    t.pages.append(view)
    return doc, view


def doc_ready(doc, selector, timeout=40000):
    try:
        doc.wait_for_selector(selector, timeout=timeout)
        return True
    except Exception:
        return False


# ===== обработка на устройстве =====

@case("Фото сжимается в WebP на устройстве до отправки", layer="ui", matrix={"engine": "engines"}, order=1)
def photo_webp_on_device(t):
    page, task = stage_page(t)
    name, data, ctype = t.world.media.fresh("camera")
    name = unique(name, t)
    t.require(upload_through_ui(page, [(name, data, ctype)]), "загрузка фото не завершилась")
    stem = os.path.splitext(name)[0]
    files, metas = uploaded_parts(page, stem)
    t.require(files, "браузер не отправил ни одного файла")
    webp = any(p.get("type") == "image/webp" or str(p.get("name", "")).lower().endswith(".webp") for p in files)
    webp = webp or any(str(m.get("name", "")).lower().endswith(".webp") or m.get("prep") in ("image", "device") for m in metas)
    t.check(webp, "фото ушло на сервер без сжатия на устройстве (%s): %s" % (page.engine, files[:2]))
    sent = sum(int(p.get("size") or 0) for p in files if p.get("field") != "thumbs")
    t.check(sent < len(data), "отправлено %d байт при исходных %d — фото не сжато" % (sent, len(data)))
    show(page, "images")
    t.check(wait_tile(page, stem), "плитка фото не появилась")
    t.check(tile_thumb_painted(page, stem), "миниатюра фото не отрисовалась")
    files = t.api("worker_mont2").call("GET", "tasks/%d/stages/%d/files" % (task["id"], task["stage_details"][0]["id"]))
    stored = [f for f in files if f["filename"].startswith(stem)]
    t.require(stored, "фото не появилось на сервере")
    body = t.api("worker_mont2").get("stages/files/%d/download" % stored[0]["id"]).content
    t.check(body[:4] == b"RIFF" and body[8:12] == b"WEBP", "на сервере фото хранится не в WebP (%s)" % stored[0]["filename"])
    t.check(len(body) < len(data), "на сервере фото не меньше исходного: %d из %d байт" % (len(body), len(data)))
    page.assert_clean("фото на устройстве")


@case("Текстовый файл сжимается gzip на устройстве и отдаётся браузеру сжатым", layer="ui",
      matrix={"engine": "engines"}, order=2)
def text_gzip_on_device(t):
    page, task = stage_page(t)
    name, data, ctype = t.world.media.fresh("log")
    name = unique(name, t)
    t.require(upload_through_ui(page, [(name, data, ctype)]), "загрузка журнала не завершилась")
    files, metas = uploaded_parts(page, os.path.splitext(name)[0])
    t.check(any(m.get("enc") == "gzip" for m in metas), "текст ушёл на сервер без сжатия на устройстве (%s)" % page.engine)
    sent = sum(int(p.get("size") or 0) for p in files)
    t.check(0 < sent < len(data) // 3, "отправлено %d байт при исходных %d — сжатие не сработало" % (sent, len(data)))
    files = t.api("worker_mont2").call("GET", "tasks/%d/stages/%d/files" % (task["id"], task["stage_details"][0]["id"]))
    stored = [f for f in files if f["filename"] == name]
    t.require(stored, "файл не появился на сервере")
    api = t.api("worker_mont2")
    raw = api.get("stages/files/%d/download?ce=1" % stored[0]["id"], headers={"Accept-Encoding": "gzip"}, stream=True)
    t.expect_status(raw, 200, "скачивание сжатым (ce=1)")
    t.check(raw.headers.get("content-encoding") == "gzip", "с ce=1 файл пришёл без Content-Encoding: gzip")
    t.check(raw.headers.get("x-original-length") == str(len(data)), "X-Original-Length: %s, ожидалось %d" % (
        raw.headers.get("x-original-length"), len(data)))
    plain = api.get("stages/files/%d/download" % stored[0]["id"])
    t.check(plain.content == data, "без ce=1 пришли не исходные байты")
    page.assert_clean("gzip на устройстве")


@case("Миниатюру PDF делает устройство", layer="ui", matrix={"engine": "engines"}, order=3)
def pdf_thumb_on_device(t):
    page, task = stage_page(t)
    name, data, ctype = t.world.media.fresh("pdf5")
    name = unique(name, t)
    t.require(upload_through_ui(page, [(name, data, ctype)]), "загрузка PDF не завершилась")
    stem = os.path.splitext(name)[0]
    show(page, "documents")
    t.check(wait_tile(page, stem), "плитка PDF не появилась")
    t.check(tile_thumb_painted(page, stem), "у PDF нет миниатюры первой страницы")
    page.assert_clean("миниатюра PDF")


# ===== просмотр документов =====

def uploaded_doc(t, kind):
    key = "doc_" + kind
    if key not in t.data.cache:
        task = t.data.task("mont")
        item = t.data.upload("worker_mont", (kind,), task_id=task["id"], stage_id=task["stage_details"][0]["id"])[0]
        t.data.cache[key] = item
    return t.data.cache[key]


@case("PDF: страницы рисуются, колесо мыши прокручивает, масштаб меняется", layer="ui",
      matrix={"engine": "engines", "device": "devices"}, order=10)
def pdf_viewer(t):
    item = uploaded_doc(t, "pdf5")
    page, task = stage_page(t, device=t.params["device"])
    show(page, "documents")
    stem = os.path.splitext(item["filename"])[0]
    t.require(wait_tile(page, stem), "плитка PDF не показана")
    doc, view = open_document(t, page, stem)
    t.require(doc_ready(doc, ".pdf-pages .pdf-page canvas"), "страницы PDF не нарисовались")
    painted = doc.evaluate("""() => { const c = document.querySelector('.pdf-page canvas');
        if (!c || !c.width) return false; const g = c.getContext('2d'); const d = g.getImageData(0, 0, Math.min(200, c.width), Math.min(200, c.height)).data;
        let dark = 0; for (let i = 0; i < d.length; i += 4) if (d[i] + d[i + 1] + d[i + 2] < 600) dark += 1; return dark > 20; }""")
    t.check(painted, "первая страница PDF пустая")
    before = doc.evaluate("() => (document.scrollingElement || document.body).scrollTop + (document.querySelector('.doc-page-body') || {}).scrollTop || 0")
    if page.engine == "webkit" and page.device == "mobile":
        doc.evaluate("() => { const b = document.querySelector('.doc-page-body'); (b && b.scrollHeight > b.clientHeight ? b : document.scrollingElement).scrollBy(0, 3000); }")
        doc.wait_for_timeout(300)
    else:
        box = doc.locator(".doc-page-body").bounding_box() or {"x": 100, "y": 200, "width": 300, "height": 300}
        doc.mouse.move(box["x"] + box["width"] / 2, box["y"] + min(box["height"] / 2, 300))
        for _ in range(6):
            doc.mouse.wheel(0, 600)
            doc.wait_for_timeout(150)
    after = doc.evaluate("() => (document.scrollingElement || document.body).scrollTop + (document.querySelector('.doc-page-body') || {}).scrollTop || 0")
    t.check(after > before + 200, "колесо мыши не прокручивает документ (%s → %s)" % (before, after))
    label = doc.locator(".doc-zoom").inner_text().strip()
    doc.locator("button[aria-label='Крупнее']").click()
    doc.wait_for_timeout(500)
    bigger = doc.locator(".doc-zoom").inner_text().strip()
    t.check(bigger != label, "кнопка «Крупнее» не изменила масштаб (%s)" % label)
    doc.locator("button[aria-label='Мельче']").click()
    doc.wait_for_timeout(500)
    t.check(doc.locator(".doc-zoom").inner_text().strip() == label, "«Мельче» не вернула масштаб %s" % label)
    view.audit("pdf_viewer")
    view.assert_clean("просмотр PDF")


@case("Word и PowerPoint показываются на устройстве", layer="ui", matrix={"engine": "engines"}, order=11)
def office_viewer(t):
    docx = uploaded_doc(t, "docx")
    pptx = uploaded_doc(t, "pptx")
    page, task = stage_page(t)
    show(page, "documents")
    for item, selector, text in ((docx, ".doc-view.is-docx", "Документ автотеста Kovapp"),
                                 (pptx, ".doc-view.is-pptx", "Слайд автотеста 1")):
        stem = os.path.splitext(item["filename"])[0]
        t.require(find_tile(page, stem), "плитка %s не показана" % item["filename"])
        doc, view = open_document(t, page, stem)
        t.check(doc_ready(doc, selector), "%s не открылся на устройстве" % item["filename"])
        t.check(wait_for_text(doc, text), "%s: в показе нет текста «%s»" % (item["filename"], text))
        view.audit("office_viewer_" + os.path.splitext(item["filename"])[1].lstrip("."))
        view.assert_clean("просмотр " + item["filename"])
    page.assert_clean("просмотр Word и PowerPoint")


def wait_for_text(doc, text, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if text in (doc.evaluate("() => document.body.innerText") or ""):
            return True
        doc.wait_for_timeout(300)
    return False


@case("Excel показывается ячейками на устройстве", layer="ui", matrix={"engine": "engines"}, order=12)
def sheet_viewer(t):
    item = uploaded_doc(t, "xlsx")
    page, task = stage_page(t)
    show(page, "documents")
    stem = os.path.splitext(item["filename"])[0]
    t.require(wait_tile(page, stem), "плитка таблицы не показана")
    doc, view = open_document(t, page, stem)
    t.check(doc_ready(doc, ".sheet-page-body"), "страница таблицы не открылась")
    t.check(wait_for_text(doc, "Кабель"), "в таблице нет ячейки «Кабель»")
    view.audit("sheet_viewer")
    view.assert_clean("просмотр таблицы")
    page.assert_clean("просмотр таблицы")


# ===== запасной путь сервера =====

@case("Сервер принимает gzip от устройства и отдаёт его без пересжатия", layer="api", order=20)
def gzip_intake_api(t):
    task = t.data.task("mont")
    name, data, ctype = t.world.media.get("log")
    packed = gzip.compress(data, 9)
    meta = json.dumps([{"src": "file", "file": 0, "name": "api-" + name, "enc": "gzip", "size": len(data)}])
    item = t.api("director").call("POST", "upload", data={"task_id": str(task["id"]), "meta": meta},
                                  files=[("files", ("api-" + name + ".gz", packed, "application/gzip"))])[0]
    api = t.api("worker_mont")
    raw = api.get("task/files/%d/download?ce=1" % item["id"], headers={"Accept-Encoding": "gzip"}, stream=True)
    t.expect_status(raw, 200, "скачивание сжатым")
    t.check(raw.headers.get("content-encoding") == "gzip", "с ce=1 нет Content-Encoding: gzip")
    body = raw.raw.read()
    t.check(body[:2] == b"\x1f\x8b" and len(body) < len(data), "отдан не хранимый gzip (%d байт)" % len(body))
    t.check(gzip.decompress(body) == data, "хранимый gzip распаковывается не в исходный файл")
    plain = api.get("task/files/%d/download" % item["id"])
    t.check(plain.content == data and plain.headers.get("content-encoding") != "gzip",
            "без ce=1 должен приходить распакованный файл")


@case("Сервер отклоняет gzip-бомбу и повреждённый gzip", layer="api", order=21)
def gzip_bomb_api(t):
    task = t.data.task("mont")
    from kovtest.media import gzip_bomb
    bomb = gzip_bomb(300)
    started = time.time()
    meta = json.dumps([{"src": "file", "file": 0, "name": "bomb.txt", "enc": "gzip", "size": 1000}])
    response = t.api("director").post("upload", data={"task_id": str(task["id"]), "meta": meta},
                                      files=[("files", ("bomb.txt.gz", bomb, "application/gzip"))])
    t.check(response.status_code in (400, 413, 422), "gzip-бомба принята: ответ %s" % response.status_code)
    t.check(time.time() - started < 30, "проверка бомбы заняла %.0f с" % (time.time() - started))
    broken = gzip.compress(b"x" * 5000)[:-9] + b"broken!!!"
    meta = json.dumps([{"src": "file", "file": 0, "name": "broken.txt", "enc": "gzip", "size": 5000}])
    response = t.api("director").post("upload", data={"task_id": str(task["id"]), "meta": meta},
                                      files=[("files", ("broken.txt.gz", broken, "application/gzip"))])
    t.check(response.status_code in (400, 422), "повреждённый gzip принят: ответ %s" % response.status_code)


@case("Статика приложения отдаётся заранее сжатой, политика безопасности разрешает PDF.js", layer="api", order=22)
def static_precompressed(t):
    response = t.anon().get(t.settings.app_url + "js/app.js", auth=False, headers={"Accept-Encoding": "gzip"}, stream=True)
    t.expect_status(response, 200, "скрипт приложения")
    t.check(response.headers.get("content-encoding") == "gzip", "скрипт приложения отдан без сжатия")
    page = t.anon().get(t.settings.app_url, auth=False)
    csp = page.headers.get("content-security-policy", "")
    t.check("wasm-unsafe-eval" in csp, "в политике безопасности нет 'wasm-unsafe-eval' — PDF.js не покажет JPEG 2000")
    t.check("'unsafe-eval'" not in csp.replace("'wasm-unsafe-eval'", ""), "политика безопасности разрешает произвольный eval")
