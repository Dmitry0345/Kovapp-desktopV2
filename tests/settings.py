from kovtest import case
from kovtest.flows import wait_for


def device_key(page) -> str:
    """Тема и масштаб хранятся отдельно для компьютера и телефона: web.settings.pc / web.settings.mobile."""
    coarse = page.raw.evaluate("() => !!(window.matchMedia && matchMedia('(pointer: coarse)').matches)")
    return "web.settings." + ("mobile" if coarse else "pc")


# ===== тема и масштаб =====

@case("Тема из настроек применяется и сохраняется на сервере", layer="ui",
      matrix={"engine": "engines", "device": "devices"}, order=1)
def theme_switch(t):
    role = "worker_buh"
    # раздел «Внешний вид»: на телефоне настройки открываются разделами, на компьютере — та же сетка
    page = t.page(role, "light", t.params["device"], path="#/settings/look")
    raw = page.raw
    raw.click(".settings-page [data-key='dark']")
    t.check(wait_for(page, "() => document.documentElement.dataset.theme === 'dark'", 5), "тёмная тема не включилась")
    page.raw.wait_for_timeout(800)
    stored = t.api(role).call("GET", "preferences/" + device_key(page))
    t.check(((stored or {}).get("value") or {}).get("theme") == "dark", "выбор темы не сохранился на сервере: %s" % stored)
    page.audit("settings_dark")
    raw.reload()
    page.wait_ready()
    t.check(page.raw.evaluate("() => document.documentElement.dataset.theme") == "dark", "после перезагрузки тема сбросилась")
    raw.click(".settings-page [data-key='light']")
    t.check(wait_for(page, "() => document.documentElement.dataset.theme === 'light'", 5), "светлая тема не вернулась")
    page.raw.wait_for_timeout(800)
    page.assert_clean("настройки")


@case("Масштаб интерфейса меняется ползунком и сохраняется", layer="ui", order=2)
def scale_change(t):
    role = "worker_to"
    page = t.page(role, "light", "desktop", path="#/settings/look")
    before = page.raw.evaluate("() => getComputedStyle(document.documentElement).fontSize")
    slider = page.raw.locator(".settings-page input.range").first
    slider.evaluate("(el) => { el.value = String(Number(el.max)); el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true })); }")
    page.raw.wait_for_timeout(1500)
    after = page.raw.evaluate("() => getComputedStyle(document.documentElement).fontSize")
    t.check(before != after, "размер шрифта не изменился (%s)" % before)
    page.audit("settings_scaled")
    stored = t.api(role).call("GET", "preferences/" + device_key(page))
    t.check(((stored or {}).get("value") or {}).get("scale"), "масштаб не сохранился на сервере")
    slider.evaluate("(el) => { el.value = '1'; el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true })); }")
    page.raw.wait_for_timeout(1500)
