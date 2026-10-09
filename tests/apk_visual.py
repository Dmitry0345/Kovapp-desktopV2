import io
import re

from PIL import Image, ImageChops

from kovtest import case
from kovtest.config import env_int, env_list, load_yaml
from kovtest.flows import hash_of, segmented

UI = load_yaml("ui.yaml")
SECTIONS = UI["sections"]
VISUAL_ROLES = env_list("VISUAL_ROLES") or UI["visual"]["roles"]
TOLERANCE = env_int("APK_VISUAL_TOLERANCE", 2)        # шум сглаживания: разница яркости до 2 из 255
MAX_PIXELS = env_int("APK_VISUAL_MAX_PIXELS", 200)    # столько пикселей шума ещё не расхождение


def stable_png(page, tries: int = 12):
    """Снимок, когда экран перестал меняться: после перехода поля ещё дорисовываются,
    и снимок «в моменте» у сайта и приложения мог попасть на разные кадры."""
    last = page.raw.screenshot(animations="disabled", caret="hide")
    for _ in range(tries):
        page.raw.wait_for_timeout(300)
        now = page.raw.screenshot(animations="disabled", caret="hide")
        if now == last:
            return now
        last = now
    return last


def screen(t, role, theme, device, key, variant):
    route = SECTIONS[key]["route"]
    page = t.page(role, theme, device, path=route, variant=variant)
    if key == "tasks_archive" and hash_of(page).startswith(route):
        segmented(page, "archived")
        page.wait_ready()
    png = stable_png(page)
    text = page.raw.evaluate("() => (document.querySelector('.content') || document.body).innerText")
    errors = list(page.console) + list(page.http_errors)
    page.console.clear()
    page.http_errors.clear()
    t.world.browsers.release(page)
    t.pages.remove(page)
    return Image.open(io.BytesIO(png)).convert("RGB"), text, errors


def compare(a, b):
    if a.size != b.size:
        return None, 255
    diff = ImageChops.difference(a, b)
    if diff.getbbox() is None:
        return 0, 0
    worst = max(hi for lo, hi in diff.getextrema())
    count = diff.convert("L").point(lambda v: 255 if v > TOLERANCE else 0).histogram()[255]
    return count, worst


def save_pair(t, name, web, app):
    folder = t.log.dir / "screenshots"
    safe = re.sub(r"[^0-9A-Za-z._-]+", "_", name)[:110]
    diff = ImageChops.difference(web, app).convert("L").point(lambda v: 255 if v > TOLERANCE else 0)
    marked = app.copy()
    marked.paste((255, 0, 64), mask=diff)
    for suffix, image in (("web", web), ("apk", app), ("diff", marked)):
        path = folder / ("APKDIFF_%s_%s.png" % (safe, suffix))
        image.save(path)
        t.result.screenshots.append("screenshots/" + path.name)


def check_section(t, key):
    if t.settings.target != "apk":
        t.skip("сверка мобильной версии с сайтом: запуск run.cmd mobile")
    role, theme, device = t.params["role"], t.params["theme"], t.params["device"]
    where = "%s [%s/%s/%s]" % (SECTIONS[key]["title"], role, theme, device)
    if key == "settings":
        # «Настройки» отличаются намеренно: строки «про браузер» в приложении заменены
        _, web_text, _ = screen(t, role, theme, device, key, "web")
        _, app_text, errors = screen(t, role, theme, device, key, "apk")
        only_app = [l for l in set(app_text.split("\n")) - set(web_text.split("\n")) if l.strip()]
        bad = [l for l in only_app if "браузер" in l.lower() and "сервер" not in l.lower()]
        t.check(not bad, "%s: в приложении говорится о браузере: %s" % (where, bad[:3]))
        t.log.info("%s: строк только в приложении %d: %s" % (where, len(only_app), only_app[:6]))
        for e in errors:
            t.fail("%s: ошибка в приложении — %s" % (where, e[:300]))
        return
    for attempt in (1, 2):
        web, _, web_errors = screen(t, role, theme, device, key, "web")
        app, _, app_errors = screen(t, role, theme, device, key, "apk")
        count, worst = compare(web, app)
        if count == 0 or (count is not None and count <= MAX_PIXELS):
            break
        t.log.info("%s: снимки разошлись (%s пикс.), снимаю ещё раз — данные могли измениться" % (where, count))
    if count is None:
        t.fail("%s: размер экрана приложения %s, у сайта %s" % (where, app.size, web.size))
    elif count > MAX_PIXELS:
        save_pair(t, "%s_%s_%s_%s" % (key, role, theme, device), web, app)
        t.fail("%s: экран приложения отличается от сайта — %d пикселей (наибольшая разница %d/255), см. APKDIFF_*" % (
            where, count, worst))
    elif count:
        t.log.info("%s: совпадает с точностью до сглаживания (%d пикс., %d/255)" % (where, count, worst))
    for e in [e for e in app_errors if e not in web_errors]:
        t.fail("%s: в приложении ошибка, которой нет на сайте — %s" % (where, e[:300]))


def register(key, order):
    title = SECTIONS[key]["title"] + (" (архив)" if key == "tasks_archive" else "")

    @case("Экран «%s» в приложении совпадает с сайтом" % title, layer="visual",
          matrix={"role": VISUAL_ROLES, "theme": "themes", "device": "devices"}, order=order)
    def _check(t, _key=key):
        check_section(t, _key)
    return _check


for _i, _key in enumerate(SECTIONS):
    register(_key, 10 + _i)
