import time

from kovtest import case
from kovtest.flows import wait_for


def apk(t):
    if t.settings.target != "apk":
        t.skip("раздел мобильной версии: запуск run.cmd mobile")
    return t.world.apk


@case("Без связи с сервером приложение открывается: интерфейс внутри APK", layer="mobile",
      matrix={"device": "devices"}, order=1)
def offline_start(t):
    env = apk(t)
    page = t.page("worker_mont", "light", t.params["device"], path="#/tasks")
    env.offline(True)
    t.later(lambda: env.offline(False))
    page.raw.reload()
    t.check(wait_for(page, "() => !!document.querySelector('.app .top-bar, .login-screen .login-card')", 20),
            "без связи с сервером интерфейс не открылся")
    body = page.raw.evaluate("() => document.body.innerText.slice(0, 300)")
    t.check("ERR_" not in body and "не удаётся получить доступ" not in body.lower(),
            "вместо интерфейса показана страница ошибки браузера: %s" % body[:120])
    t.screenshot(page, "apk_offline_%s" % t.params["device"])
    env.offline(False)
    page.raw.reload()
    page.wait_ready()
    t.check(page.raw.locator(".app .content").count() == 1, "после возвращения связи раздел не открылся")
    env.forget_offline_noise(page)
    page.assert_clean("запуск без связи")


@case("Связь пропала и вернулась: приложение переподключается само", layer="mobile", order=2)
def reconnect(t):
    env = apk(t)
    page = t.page("worker_mont", "light", "mobile", path="#/tasks")
    page.raw.evaluate("() => { window.__apkReconnects = 0; Kov.WS.onReconnect(() => { window.__apkReconnects++; }); }")
    env.offline(True)
    t.later(lambda: env.offline(False))
    page.raw.wait_for_timeout(2500)
    t.check(page.raw.locator(".app").count() == 1, "без связи пропал интерфейс")
    env.offline(False)
    t.check(wait_for(page, "() => window.__apkReconnects > 0", 40), "после возвращения связи приложение не переподключилось")
    page.wait_ready()
    env.forget_offline_noise(page)
    page.assert_clean("переподключение")
