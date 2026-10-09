import time

from kovtest import case, mail
from kovtest.flows import hash_of, wait_for


# ===== сервер =====

@case("Вход: верный пароль даёт токен, неверный — отказ", layer="api", order=1)
def login_api(t):
    anon = t.anon()
    account = t.account("worker_to")
    good = anon.post("login", data={"username": account.login, "password": account.password}, auth=False)
    t.expect_status(good, 200, "вход с верным паролем")
    body = good.json() if good.status_code == 200 else {}
    t.check(body.get("access_token") and body.get("user_id") == account.id, "в ответе нет токена или номера сотрудника")
    bad = anon.post("login", data={"username": account.login, "password": account.password + "x"}, auth=False)
    t.expect_status(bad, 401, "вход с неверным паролем")
    nobody = anon.post("login", data={"username": "no_such_user_autotest", "password": "x"}, auth=False)
    t.expect_status(nobody, 401, "вход несуществующего пользователя")


@case("Без входа закрытые адреса недоступны", layer="api", order=2)
def anonymous_blocked(t):
    anon = t.anon()
    for path in ("employees", "tasks?is_archived=false", "projects", "equipment", "organizations", "chat/list",
                 "templates", "health/file-access"):
        t.expect_status(anon.get(path), 401, "аноним: GET " + path)


@case("Файловый токен открывает только файлы", layer="api", order=3)
def file_token_scope(t):
    api = t.api("worker_mont")
    token = api.file_token(force=True)
    stranger = t.anon()
    stranger.token = token
    t.expect_status(stranger.get("employees"), (401, 403), "файловый токен на обычном адресе API")
    photo = t.anon().get("employees/%d/photo?token=%s" % (api.user_id, token), auth=False)
    t.expect_status(photo, (200, 404), "файловый токен на адресе фотографии")


@case("Сброс пароля через код из письма", layer="api", order=4)
def reset_password_api(t):
    if not mail.available(t.settings):
        t.skip("приёмник почты недоступен — письмо с кодом не прочитать (есть только в полном режиме)")
    account = t.account("worker_to")
    anon = t.anon()
    started = time.time()
    t.expect_status(anon.post("reset-password/request", json={"username": account.login}, auth=False), 200, "запрос кода")
    code = mail.wait_code(t.settings, "%s@kovapp-autotest.ru" % account.login, started)
    t.require(code, "письмо с кодом не пришло")
    wrong = anon.post("reset-password/verify", json={"username": account.login, "reset_code": "000000" if code != "000000" else "111111"}, auth=False)
    t.check(wrong.status_code == 200 and wrong.json().get("success") is False, "неверный код должен отклоняться")
    right = anon.post("reset-password/verify", json={"username": account.login, "reset_code": code}, auth=False)
    t.check(right.status_code == 200 and right.json().get("success") is not False, "верный код не принят")
    temp = account.password + "-tmp"
    done = anon.post("reset-password/confirm", json={"username": account.login, "reset_code": code, "new_password": temp}, auth=False)
    t.check(done.status_code == 200 and done.json().get("success") is not False, "пароль не сменился")
    t.expect_status(anon.post("login", data={"username": account.login, "password": temp}, auth=False), 200, "вход с новым паролем")
    started = time.time()
    anon.post("reset-password/request", json={"username": account.login}, auth=False)
    code = mail.wait_code(t.settings, "%s@kovapp-autotest.ru" % account.login, started)
    t.require(code, "второе письмо с кодом не пришло — пароль остался временным: " + temp)
    anon.post("reset-password/confirm", json={"username": account.login, "reset_code": code, "new_password": account.password}, auth=False)
    t.expect_status(anon.post("login", data={"username": account.login, "password": account.password}, auth=False), 200,
                    "вход с восстановленным паролем")


# ===== экран входа =====

@case("Экран входа: вёрстка и переключатель темы", layer="ui",
      matrix={"engine": "engines", "theme": "themes", "device": "devices"}, order=10)
def login_screen(t):
    page = t.world.browsers.open_login(t.params["theme"], t.params["device"], "A", t)
    page.audit("login")
    before = page.raw.evaluate("() => document.documentElement.dataset.theme")
    page.raw.click(".theme-toggle")
    page.raw.wait_for_timeout(700)
    after = page.raw.evaluate("() => document.documentElement.dataset.theme")
    t.check(before != after, "кнопка темы на экране входа не переключила тему (%s → %s)" % (before, after))
    page.assert_clean("экран входа")


@case("Экран входа: ошибка при неверном пароле и успешный вход", layer="ui",
      matrix={"engine": "engines", "device": "devices"}, order=11)
def login_flow(t):
    page = t.world.browsers.open_login("light", t.params["device"], "A", t)
    account = t.account("head_to")
    raw = page.raw
    raw.fill(".login-card input[placeholder='Логин']", account.login)
    raw.fill(".login-card input[type=password]", account.password + "-неверный")
    raw.locator(".login-card button", has_text="Войти").click()
    t.check(wait_for(page, "() => { const e = document.querySelector('.login-card .error'); return e && !e.hidden && e.textContent.includes('Неверный'); }"),
            "после неверного пароля не показано «Неверный логин или пароль»")
    raw.fill(".login-card input[type=password]", account.password)
    raw.keyboard.press("Enter")
    t.check(wait_for(page, "() => !!document.querySelector('.app .top-bar .menu-btn')", 15),
            "после верного пароля приложение не открылось")
    page.wait_ready()
    t.check(hash_of(page).startswith("#/tasks"), "после входа открыт не раздел задач: " + hash_of(page))
    page.console.clear()
    page.http_errors = [e for e in page.http_errors if "/login" not in e]
    page.assert_clean("вход")


@case("Выход из учётной записи через профиль", layer="ui", order=12)
def logout_flow(t):
    page = t.page("worker_to", "light", "desktop", path="#/profile")
    t.check(wait_for(page, "() => !!document.querySelector('.side-panel.open, .side-panel.left.open')", 10),
            "профиль не открылся")
    page.raw.locator(".side-panel.open button", has_text="Выход").first.click()
    page.raw.locator(".modal-root .modal-foot button", has_text="Выйти").click()
    t.check(wait_for(page, "() => !!document.querySelector('.login-screen .login-card')", 10),
            "после выхода не показан экран входа")
    stored = page.raw.evaluate("() => localStorage.getItem('kov.auth')")
    t.check(not stored, "после выхода в браузере остался токен")


@case("Сброс пароля через экран входа", layer="ui", order=13)
def reset_password_ui(t):
    if not mail.available(t.settings):
        t.skip("приёмник почты недоступен — письмо с кодом не прочитать (есть только в полном режиме)")
    account = t.account("worker_buh")
    page = t.world.browsers.open_login("light", "desktop", "A", t)
    raw = page.raw
    raw.locator(".login-card button.link", has_text="Сброс пароля").click()
    raw.fill(".login-card input[placeholder='Логин']", account.login)
    started = time.time()
    raw.locator(".login-card button", has_text="Отправить код").click()
    code = mail.wait_code(t.settings, "%s@kovapp-autotest.ru" % account.login, started)
    t.require(code, "письмо с кодом не пришло")
    raw.fill(".login-card input[placeholder='Код из письма']", code)
    raw.locator(".login-card button", has_text="Проверить код").click()
    t.require(wait_for(page, "() => [...document.querySelectorAll('.login-card input[type=password]')].some(i => i.offsetParent)", 10),
              "после кода не появились поля нового пароля")
    fields = raw.locator(".login-card input[type=password]")
    fields.nth(0).fill(account.password)
    fields.nth(1).fill(account.password)
    raw.locator(".login-card button", has_text="Сменить пароль").click()
    t.check(wait_for(page, "() => document.querySelector('.login-card').innerText.includes('Пароль изменён')", 10),
            "нет сообщения «Пароль изменён»")
    page.audit("reset_done")
