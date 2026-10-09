# -*- coding: utf-8 -*-
"""Все разделы ПК-версии под одной ролью: вход (и неверный пароль), пункты меню по правам,
каждый раздел и его страницы — без ошибок Python, сбоев запросов и предупреждений; снимки.

Ожидания по меню — правила сайта (web_app/js/core/rights.js): «Файлы CSV» — только руководству;
«Проекты» закрыты рядовым сотрудникам монтажа и отдела ТО (их руководителям открыты).
Склад проекта — правило сервера (routers/warehouse.py): руководство, бухгалтерия, монтажный отдел
и склад; остальным карточка «Склад» показывает объяснение вместо ведомости, как сайт.
"""

MANAGEMENT = {"директор", "администратор"}
NO_PROJECTS = {"Монтажный отдел", "Отдел ТО"}
WAREHOUSE_DEPTS = {"Бухгалтерия", "Монтажный отдел", "Склад"}


def expected_menu(emp):
    mgmt = emp.get("status") in MANAGEMENT
    head = emp.get("status") == "руководитель отдела"
    return {"csv": mgmt, "projects": mgmt or head or emp.get("department") not in NO_PROJECTS}


def can_warehouse(emp):
    return emp.get("status") in MANAGEMENT or emp.get("department") in WAREHOUSE_DEPTS


def steps(ctx):
    def wrong_password(c):
        c.login.login_input.setText(c.role)
        c.login.password_input.setText("неверный-пароль")
        c.login.on_login_clicked()

    def wrong_check(c):
        lab = c.login.error_label
        c.check("вход: неверный пароль — сообщение под полями", lab.isVisible() and bool(lab.text().strip()), lab.text())
        c.shot("01_login_error")
        c.login.password_input.clear()

    def logged_in(c):
        shell = c.find_shell()
        c.check("вход: открылось главное окно", shell is not None)
        if shell is None:
            raise RuntimeError("главное окно не открылось")
        c.mark = c.watch.mark()

    def menu(c):
        emp = c.M.EMP[c.user_id]
        want = expected_menu(emp)
        for key, visible in want.items():
            item = c.shell.menu_items.get(key)
            shown = item is not None and not item.isHidden()        # само меню свёрнуто — смотрим на пункт
            c.check("меню: «%s» %s" % (key, "есть" if visible else "скрыт"), shown == visible, "виден" if shown else "скрыт")
        c.data["menu"] = want
        c.shell.toggle_menu() if hasattr(c.shell, "toggle_menu") else None

    def menu_shot(c):
        c.shot("02_menu")
        if hasattr(c.shell, "toggle_menu"):
            c.shell.toggle_menu()

    def ids(c):
        from kov.api import client
        try:
            tasks = client.get("tasks/light", query={"is_archived": "false"}) or []
        except Exception:
            tasks = client.get("tasks", query={"is_archived": "false"}) or []
        c.data["task"] = (tasks[0].get("id") if tasks else None)
        c.data["projects"] = []
        if c.data["menu"]["projects"]:
            raw = client.get("projects") or []
            items = raw.get("items") if isinstance(raw, dict) else raw
            c.data["projects"] = [p.get("id") for p in items or [] if p.get("id")]
        c.check("данные для проверки: задачи у роли", bool(c.data["task"]), "задача %s" % c.data["task"], warn=True)

    def visit(route, name, cls, params=None, need=None, after=None, expected=(), bottom=False):
        """Перейти, подождать, проверить класс раздела, ошибки, вёрстку и сделать снимок."""
        def go(c):
            if need and not need(c):
                c.data["skip_" + name] = True
                return
            c.data.pop("skip_" + name, None)
            c.mark = c.watch.mark()
            c.shell.go(route, **(params(c) if callable(params) else (params or {})))

        def done(c):
            if c.data.get("skip_" + name):
                return
            cur = c.shell.current
            c.check("%s: открылся раздел" % name, cur is not None and type(cur).__name__ == cls,
                    type(cur).__name__ if cur is not None else "ничего")
            if after:
                after(c)
            c.clean(name, expected=expected)
            c.layout(name, bottom=bottom)
            c.shot(name)
        return [("перейти: " + name, go, 2600), ("проверить: " + name, done, 300)]

    def settings_bar(c):
        cur = c.shell.current
        bar = cur.save_btn.parentWidget()
        gap = bar.geometry().top() - cur.scroll.geometry().bottom() - 1
        c.check("настройки: «Сохранить» сразу под карточками, как на сайте", 0 <= gap <= c.rem(1.2), "зазор %d px" % gap)
        sb = cur.scroll.verticalScrollBar()
        small = c.window != c.window_asked     # экран не вместил окно заданного размера — прокрутка ожидаема
        c.check("настройки: карточки видны целиком (место есть — без прокрутки)", sb.maximum() == 0,
                "прокрутка %d px, видно %d px%s" % (sb.maximum(), cur.scroll.viewport().height(),
                                                   "; окно %dx%d вместо %dx%d — экран меньше" % (c.window + c.window_asked)
                                                   if small else ""), warn=small)

    def archive_state(c):
        cur = c.shell.current
        if cur.rows:
            return
        c.check("архив без задач: таблица с заголовками и «Задач нет» в ней", cur.table.isVisible() and cur.empty.isVisible()
                and cur.empty.parentWidget() is cur.table.viewport(), cur.empty.text())

    def task_files(c):
        cur = c.shell.current
        if hasattr(cur, "zone_switch"):
            cur.zone_switch.set_current("task", True)

    def task_files_check(c):
        """Файлы задачи добавляет любой, кому открыта задача; в архиве и в завершённом проекте — никто."""
        cur = c.shell.current
        block = getattr(cur, "block", None)
        if block is None or not hasattr(cur, "archived"):
            return
        closed = cur.archived() or bool((cur.task or {}).get("project_archived"))
        c.check("файлы задачи: «Добавить файлы» — всем, кому открыта задача", bool(block.can_upload) == (not closed),
                "можно добавлять: %s; задача в архиве или проект завершён: %s" % (bool(block.can_upload), closed))

    def project_section(key):
        def fn(c):
            cur = c.shell.current
            if hasattr(cur, "open_section"):
                cur.open_section(key)
        return fn

    def refused(route, name):
        """Раздел, закрытый правами: переход не выполняется, текущий раздел не меняется."""
        def go(c):
            c.data["before_" + name] = type(c.shell.current).__name__ if c.shell.current is not None else None
            c.mark = c.watch.mark()
            c.shell.go(route)

        def done(c):
            unc, jobs, toasts = c.watch.since(c.mark)
            now = type(c.shell.current).__name__ if c.shell.current is not None else None
            c.check("%s: недоступен этой роли" % name, now == c.data.get("before_" + name)
                    and any("недоступ" in t["title"] for t in toasts), [t["title"] for t in toasts])
            c.mark = c.watch.mark()
        return [("закрыто: " + name, go, 1200), ("проверить закрыто: " + name, done, 200)]

    def warehouse_state(c):
        wh = getattr(c.shell.current, "wh", None)
        if wh is None:
            c.check("склад: блок ведомости открыт", False)
            return
        if can_warehouse(c.M.EMP[c.user_id]):
            c.check("склад: ведомость открыта", wh.table.isVisible() and not wh.empty.isVisible())
            return
        text = wh.empty.text() if wh.empty.isVisible() else ""
        c.check("склад без доступа: объяснение вместо ведомости", not wh.table.isVisible() and bool(text), text)
        c.check("склад без доступа: объяснение сразу под панелью", wh.empty.y() < wh.height() // 3,
                "%d из %d px" % (wh.empty.y(), wh.height()))

    def profile_other(c):
        c.mark = c.watch.mark()
        c.shell.open_profile(6 if c.user_id != 6 else 3)

    def profile_check(c):
        c.check("профиль сотрудника: панель открыта", c.shell.panel.isVisible())
        c.clean("профиль сотрудника")
        c.shot("31_profile_other")
        c.shell.close_panel()

    def org_view(c):
        from kov.views.organizations import open_view
        c.mark = c.watch.mark()
        open_view(c.shell, 1)

    def org_view_check(c):
        c.clean("карточка организации")
        c.shot("41_org_view")
        c.shell.close_panel()

    has_task = lambda c: bool(c.data.get("task"))
    has_project = lambda c: c.data["menu"]["projects"] and bool(c.data.get("projects"))
    can_projects = lambda c: c.data["menu"]["projects"]
    can_csv = lambda c: c.data["menu"]["csv"]
    from_kov_rights = lambda fn: (lambda c: getattr(__import__("kov.rights", fromlist=["x"]), fn)())

    s = [
        ("окно входа", lambda c: c.shot("00_login"), 300),
        ("неверный пароль", wrong_password, 2200),
        ("неверный пароль: проверка", wrong_check, 300),
        ("вход", lambda c: c.do_login(), 3000),
        ("главное окно", logged_in, 600),
        ("меню по правам", menu, 900),
        ("меню: снимок", menu_shot, 600),
        ("данные роли", ids, 300),
    ]
    s += visit("notifications", "10_notifications", "NotificationsSection", bottom=True)
    s += visit("tasks", "11_tasks", "TasksSection", bottom=True)
    s += visit("tasks", "12_tasks_archive", "TasksSection", params={"archived": True}, bottom=True, after=archive_state)
    s += visit("task", "13_task", "TaskView", params=lambda c: {"task_id": c.data["task"]}, need=has_task)
    s += [("файлы задачи", task_files, 1800),
          ("файлы задачи: снимок", lambda c: (task_files_check(c), c.clean("14_task_files"), c.shot("14_task_files")), 300)]
    s += visit("task_new", "15_task_new", "TaskEdit", need=from_kov_rights("can_create_task"))
    s += visit("task_edit", "16_task_edit", "TaskEdit", params=lambda c: {"task_id": c.data["task"]},
               need=lambda c: has_task(c) and from_kov_rights("can_create_task")(c))
    s += visit("projects", "20_projects", "ProjectsSection", need=can_projects, bottom=True)
    s += visit("project", "21_project", "ProjectView", params=lambda c: {"project_id": c.data["projects"][0]}, need=has_project)
    for key, nm in (("contracts", "22_project_contracts"), ("photos", "23_project_photos")):
        s += [("раздел проекта " + key, lambda c, k=key: has_project(c) and project_section(k)(c), 1800),
              ("раздел проекта %s: снимок" % key, lambda c, n=nm: has_project(c) and (c.clean(n), c.shot(n)), 300)]
    no_wh = () if can_warehouse(ctx.M.EMP.get(ctx.user_id) or {}) else ("warehouse",)
    s += visit("warehouse", "24_warehouse", "ProjectView", params=lambda c: {"project_id": c.data["projects"][0]}, need=has_project,
               after=warehouse_state, expected=no_wh)
    s += visit("project_new", "25_project_new", "ProjectEdit", need=lambda c: can_projects(c) and from_kov_rights("can_create_project")(c))
    s += visit("organizations", "40_organizations", "OrganizationsSection", bottom=True)
    s += [("карточка организации", org_view, 1800), ("карточка организации: снимок", org_view_check, 400)]
    s += visit("organization_edit", "42_org_edit", "OrganizationEdit", params={"organization_id": 1},
               need=from_kov_rights("can_edit_organizations"))
    s += visit("equipment", "50_equipment", "EquipmentSection")
    s += visit("employees", "60_employees", "EmployeesSection", bottom=True)
    s += [("профиль сотрудника", profile_other, 1800), ("профиль сотрудника: снимок", profile_check, 400)]
    s += [("мой профиль", lambda c: (setattr(c, "mark", c.watch.mark()), c.shell.go("profile")), 1800),
          ("мой профиль: снимок", lambda c: (c.clean("61_profile"), c.shot("61_profile"), c.shell.close_panel()), 400)]
    s += visit("settings", "70_settings", "SettingsSection", after=settings_bar)
    s += visit("csv", "71_csv", "CsvSection", need=can_csv)
    s += visit("messenger", "80_messenger", "MessengerSection")
    s += [("колокольчик → уведомления", lambda c: (setattr(c, "mark", c.watch.mark()), c.shell.bell.click()), 2200),
          ("колокольчик: проверка", lambda c: c.check("колокольчик открывает уведомления",
                                                     type(c.shell.current).__name__ == "NotificationsSection"), 200)]
    # закрытое правами: переход не выполняется, раздел остаётся прежним
    want = expected_menu(ctx.M.EMP.get(ctx.user_id) or {})
    if not want["csv"]:
        s += refused("csv", "Файлы CSV")
    if not want["projects"]:
        s += refused("projects", "Проекты")
    return s
