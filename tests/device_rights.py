"""Права ролей на настоящем Android: меню у каждой роли то же, что на сайте."""
from kovtest import case

MENU = "return [...document.querySelectorAll('.side-menu nav .item')].map(i => i.dataset.key || i.textContent.trim());"
MENU_PW = "() => [...document.querySelectorAll('.side-menu nav .item')].map(i => i.dataset.key || i.textContent.trim())"


@case("Меню каждой роли на телефоне совпадает с сайтом", layer="device", order=1)
def menu_by_role(t):
    d = t.world.device
    if d is None:
        t.skip("только для тестов на устройстве (run.cmd device)")
    d.ensure_app(t)
    differ = []
    for role in t.world.accounts.specs:
        d.login(role, "#/tasks")
        phone = d.js(MENU)
        site_page = t.page(role, device="mobile", path="#/tasks", variant="web")
        site = site_page.raw.evaluate(MENU_PW)
        site_page.context.close()
        t.step("%s: %s" % (role, ", ".join(phone)))
        if phone != site:
            differ.append("%s: на телефоне %s, на сайте %s" % (role, phone, site))
    t.check(not differ, "меню отличается: %s" % "; ".join(differ))
