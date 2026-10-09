import time


# ===== общие действия в интерфейсе =====

def hash_of(page) -> str:
    return page.raw.evaluate("() => location.hash")


def wait_for(page, predicate_js: str, timeout: float = 10.0, arg=None) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if page.raw.evaluate(predicate_js, arg):
                return True
        except Exception:
            pass
        page.raw.wait_for_timeout(150)
    return False


def select_row(page, key) -> None:
    page.raw.click("table.data tr.row[data-key='%s']" % key)
    page.raw.wait_for_timeout(200)


def row_exists(page, key) -> bool:
    return page.raw.locator("table.data tr.row[data-key='%s']" % key).count() > 0


def file_input(page, scope: str = ".file-block"):
    return page.raw.locator(scope + " input[type=file]").first


def upload_through_ui(page, files, scope: str = ".file-block", timeout: float = 120.0) -> bool:
    payload = [{"name": name, "mimeType": ctype, "buffer": data} for name, data, ctype in files]
    file_input(page, scope).set_input_files(payload)
    deadline = time.time() + timeout
    while time.time() < deadline:
        busy = page.raw.evaluate("s => { const w = document.querySelector(s + ' .upload-progress'); return !!(w && !w.hidden); }", scope)
        if not busy:
            page.raw.wait_for_timeout(400)
            return True
        page.raw.wait_for_timeout(250)
    return False


# просмотр «Свет кадра» (beta 3): кадр — в .mv-stage, действия — кнопки с подписями
VIEWER_IMG = ".viewer-root .mv-stage img"
VIEWER_VIDEO = ".viewer-root .mv-stage video"
VIEWER_DOWNLOAD = ".viewer-root button[title='Скачать']:visible"   # на телефоне кнопка внизу, верхняя скрыта


def tile_action(page, name: str, action: str) -> None:
    """Действие с плиткой файла. Компьютер — кнопки на плитке; телефон — касание открывает,
    «⋯» — шторка «Открыть · Скачать · Удалить»."""
    item = tile(page, name)
    if action == "open":
        item.locator("button.tile-thumb").first.click()
        return
    if item.locator(".tile-actions").count():
        title = {"download": "Скачать файл", "delete": "Удалить файл"}[action]
        item.locator(".tile-actions button[title='%s']" % title).click()
        return
    item.locator("button.tile-more").click()
    label = {"download": "Скачать", "delete": "Удалить"}[action]
    page.raw.locator(".kov-sheet-root .sheet-action", has_text=label).first.click()


def tile(page, name: str):
    return page.raw.locator(".file-tile", has=page.raw.locator(".tile-name", has_text=name)).first


def wait_tile(page, name: str, timeout: float = 20.0) -> bool:
    return wait_for(page, "n => [...document.querySelectorAll('.file-tile .tile-name')].some(e => e.textContent.includes(n))",
                    timeout, name)


def tile_thumb_painted(page, name: str, timeout: float = 20.0) -> bool:
    return wait_for(page, """n => {
        const t = [...document.querySelectorAll('.file-tile')].find(x => (x.querySelector('.tile-name') || {}).textContent?.includes(n));
        const img = t && t.querySelector('.tile-thumb img');
        return !!(img && img.complete && img.naturalWidth > 0);
    }""", timeout, name)


def open_task_zone(page, key: str) -> None:
    """Зона окна задачи: stage — файлы этапа, task — файлы задачи. Компьютер — переключатель над зоной;
    телефон (до 760 px) — вкладка «Файлы» внизу, затем «Файлы этапа» или «Файлы задачи»."""
    raw = page.raw
    # на телефоне переключатель компьютера тоже есть в разметке, но скрыт — ждём то, что видно
    raw.wait_for_function("""k => { const vis = e => !!e && e.getClientRects().length > 0;
        return vis(document.querySelector(".tv-tabbar .tv-tab[data-k='files']"))
            || vis(document.querySelector(".content [data-key='" + k + "']")); }""", arg=key)
    tab = raw.locator(".tv-tabbar .tv-tab[data-k='files']")
    if tab.count() and tab.first.is_visible():
        tab.first.click()
        raw.wait_for_timeout(300)
        raw.click(".tv-sw[data-k='%s']" % key)
    else:
        raw.click(".content [data-key='%s']" % key)
    raw.wait_for_timeout(400)


def segmented(page, key: str) -> None:
    page.raw.click("[data-key='%s']" % key)
    page.raw.wait_for_timeout(250)


def modal_title(page) -> str:
    loc = page.raw.locator(".modal-root .modal h2")
    return loc.last.inner_text().strip() if loc.count() else ""


def confirm_modal(page, text: str) -> None:
    page.raw.locator(".modal-root .modal-foot button", has_text=text).last.click()
    page.raw.wait_for_timeout(250)


def download_via(page, trigger, timeout: float = 60.0):
    # в приложении файл уходит в «Загрузки» телефона, а не в загрузки браузера
    if getattr(page, "variant", "web") in ("apk", "http"):
        return page.world.apk.download_via(page, trigger, timeout)
    with page.raw.expect_download(timeout=timeout * 1000) as info:
        trigger()
    download = info.value
    path = download.path()
    size = 0
    if path:
        import os
        size = os.path.getsize(path)
    return download.suggested_filename, size
