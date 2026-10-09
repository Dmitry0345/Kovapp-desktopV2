"""Большие файлы на настоящем Android: частями, с настоящим обрывом связи телефона с сервером, и «Отменить».

Файл кладётся в поле выбора файлов приложения так же, как после выбора в системном окне
(пропускается только само системное окно выбора файла); дальше работает код приложения:
отправка частями, продолжение после обрыва, отмена, уборка кусков на сервере.
"""
import hashlib
import re
import time

from kovtest import case
from kovtest.config import env_int
from kovtest.device_views import open_stage_files
from tests.network import check_no_leftovers, stage_files

BIG_MB = env_int("DEVICE_BIG_FILE_MB", env_int("NETWORK_FILE_MB", 40))
OUTAGE_S = env_int("NETWORK_SHORT_OUTAGE_S", 6)
DELAY_MS = env_int("NETWORK_UPLOAD_DELAY_MS", 1200)   # пауза между порциями: успеть оборвать связь посреди загрузки
SESSION = re.compile(r"uploads/sessions/([0-9a-f]{32})")

PUT_FILE = """const [name, mb] = arguments;
    const size = mb * 1048576 + 777;
    const data = new Uint8Array(size);
    for (let i = 0; i < size; i += 65536) crypto.getRandomValues(data.subarray(i, Math.min(i + 65536, size)));
    window.__kovBig = data;
    performance.setResourceTimingBufferSize(20000);
    const input = document.querySelector('.file-block input[type=file]');
    const dt = new DataTransfer();
    dt.items.add(new File([data], name, { type: 'application/zip' }));
    input.files = dt.files;
    input.dispatchEvent(new Event('change', { bubbles: true }));
    return size;"""
BUSY = "const w = document.querySelector('.file-block .upload-progress'); return !!(w && !w.hidden);"
TILE = "return [...document.querySelectorAll('.file-tile .tile-name')].some(e => e.textContent.includes(arguments[0]));"
# для разбора неудачи: последние запросы загрузки с кодами ответа, сообщения на экране, открытая вкладка и плитки
EVIDENCE = """return {
    requests: performance.getEntriesByType('resource').filter(e => /uploads\\/sessions|\\/upload(\\?|$)/.test(e.name))
      .slice(-8).map(e => e.name.replace(/^.*?\\/(uploads|upload)/, '$1').replace(/\\?offset=/, '@').slice(0, 70)
                        + ' → ' + (e.responseStatus || '?')),
    toasts: [...document.querySelectorAll('[class*=toast]')].map(e => e.textContent.trim()).filter(Boolean).slice(-3),
    tab: ((document.querySelector('.zone .segmented .on, .zone .segmented [aria-pressed=true]') || {}).textContent || '').trim(),
    block: !!document.querySelector('.file-block'),
    tiles: [...document.querySelectorAll('.file-tile .tile-name')].map(e => e.textContent).slice(0, 5) };"""
DIGEST = """const done = arguments[arguments.length - 1];
    crypto.subtle.digest('SHA-256', window.__kovBig)
      .then(h => done([...new Uint8Array(h)].map(b => b.toString(16).padStart(2, '0')).join('')), () => done(''));"""


def dev(t):
    if t.world.device is None:
        t.skip("только для тестов на устройстве (run.cmd device)")
    t.world.device.ensure_app(t)
    return t.world.device


def wait(check, timeout: float, step: float = 0.5) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if check():
            return True
        time.sleep(step)
    return False


def chunks_sent(d) -> int:
    return d.js("return performance.getEntriesByType('resource')"
                ".filter(e => /uploads\\/sessions\\/[0-9a-f]{32}\\?offset=/.test(e.name)).length;") or 0


def sessions(d) -> list:
    names = d.js("return performance.getEntriesByType('resource').map(e => e.name).join(' ');") or ""
    return list(dict.fromkeys(SESSION.findall(names)))


def open_stage(t, title: str):
    d = dev(t)
    task = t.data.create_task("director", title, ["Монтажный отдел"], responsible=t.data.ids("head_mont"),
                              assigned=t.data.ids("worker_mont2"))
    d.login("worker_mont2", "#/tasks/%d" % task["id"])
    t.require(open_stage_files(d), "в карточке задачи нет вкладки этапа")
    t.require(d.wait_js("document.querySelector('.file-block input[type=file]')", 20), "в этапе нет блока файлов")
    d.js("window.__kovUploadDelay = arguments[0];", DELAY_MS)
    return d, task


def start_big_upload(t, d, tag: str):
    name = "device-%s-%d.zip" % (tag, int(time.time() * 1000) % 1000000)
    size = d.js(PUT_FILE, name, BIG_MB)
    t.require(wait(lambda: chunks_sent(d) >= 1, 90), "отправка частями не началась (файл %d МБ)" % BIG_MB)
    return name, size


@case("Большой файл на телефоне: связь пропала и вернулась — загрузка продолжилась, файл на сервере цел",
      layer="device", order=1)
def big_file_outage(t):
    d, task = open_stage(t, "Большой файл на телефоне")
    name, size = start_big_upload(t, d, "big")
    t.step("файл %.1f МБ, отправка частями началась" % (size / 1048576.0))
    t.later(lambda: d.link(True))
    d.link(False)
    t.step("связь телефона с сервером выключена на %d с" % OUTAGE_S)
    time.sleep(OUTAGE_S)
    t.check(d.js(BUSY), "без связи загрузка пропала с экрана — продолжить её нельзя")
    d.link(True)
    t.require(wait(lambda: not d.js(BUSY), 240, 1), "после возвращения связи загрузка не завершилась за 4 мин")
    stem = name.rsplit(".", 1)[0]
    found = []
    wait(lambda: found.extend(f for f in stage_files(t, "worker_mont2", task) if f["filename"].startswith(stem)) or found, 20, 1)
    if not found:
        t.fail("после обрыва файл не дошёл до сервера. Приложение: %s" % d.js(EVIDENCE))
        return
    t.step("файл на сервере: %s" % found[0]["filename"])
    if not wait(lambda: d.js(TILE, stem), 20):
        t.fail("файл сохранён на сервере, но в «Файлах этапа» на экране его нет. Экран: %s" % d.js(EVIDENCE))
    stored = found
    content = t.api("worker_mont2").get_file("stages/files/%d/download" % stored[0]["id"]).content
    t.check(len(content) == size, "размер на сервере %d байт, а отправляли %d" % (len(content), size))
    local = d.js_async(DIGEST, timeout=90)
    t.check(bool(local) and hashlib.sha256(content).hexdigest() == local,
            "файл на сервере отличается от отправленного (SHA-256 не совпадает)")


@case("Отмена загрузки на телефоне: «Отменить» посреди загрузки — ни плитки, ни записи, ни кусков на сервере",
      layer="device", order=2)
def cancel_upload(t):
    d, task = open_stage(t, "Отмена загрузки на телефоне")
    name, _ = start_big_upload(t, d, "cancel")
    t.require(d.click_text("Отменить", within=".file-block", timeout=5), "во время загрузки нет кнопки «Отменить»")
    t.check(wait(lambda: not d.js(BUSY), 10), "после отмены полоса загрузки не исчезла")
    time.sleep(15)                                  # уборка кусков на сервере
    t.check(not d.js(TILE, name.rsplit(".", 1)[0]), "после отмены на экране осталась плитка недогруженного файла")
    stem = name.rsplit(".", 1)[0]
    stored = [f for f in stage_files(t, "worker_mont2", task) if f["filename"].startswith(stem)]
    t.check(not stored, "после отмены на сервере появилась запись недогруженного файла")
    check_no_leftovers(t, "worker_mont2", sessions(d), "после отмены")


@case("Отмена во время обрыва связи на телефоне: после возвращения связи на сервере ничего не осталось",
      layer="device", order=3)
def cancel_during_outage(t):
    d, task = open_stage(t, "Отмена во время обрыва на телефоне")
    name, _ = start_big_upload(t, d, "outage-cancel")
    t.later(lambda: d.link(True))
    d.link(False)
    time.sleep(3)
    t.require(d.click_text("Отменить", within=".file-block", timeout=5), "во время обрыва нет кнопки «Отменить»")
    t.check(wait(lambda: not d.js(BUSY), 10), "после отмены полоса загрузки не исчезла")
    d.link(True)
    time.sleep(20)                                  # связь вернулась — приложение убирает брошенный сеанс
    t.check(not d.js(TILE, name.rsplit(".", 1)[0]), "после отмены на экране осталась плитка недогруженного файла")
    stem = name.rsplit(".", 1)[0]
    stored = [f for f in stage_files(t, "worker_mont2", task) if f["filename"].startswith(stem)]
    t.check(not stored, "после отмены на сервере появилась запись недогруженного файла")
    check_no_leftovers(t, "worker_mont2", sessions(d), "после отмены во время обрыва")
