# -*- coding: utf-8 -*-
"""Действия ПК-версии (роль director тестового сервера) — то, что исправлено в beta3-v14-pc.

Тема и размер включаются сразу, без перезапуска: новая тема наплывает кругом от кнопки
(как у сайта), окно и вход остаются те же — и на окне входа, и в «Настройках».
Этап задачи завершается и возобновляется, обе строки — в журнале задачи. Шаблоны задач
создаются, меняются и удаляются; вложенное окно не оставляет затемнения на окне под ним.
Подсказки — тёмная плашка и в прозрачных контейнерах (раньше — белый текст на светлом).
Флажки — квадрат цвета акцента с белой галочкой. Скачанное исчезает с панели само,
крестик убирает завершённые насовсем. «Медиаконтент» проекта принимает только фото и видео.
Кнопка снимка в окне оборудования — внутри рамки. Счётчик «1 из 2» в просмотре — без подложки.
Переходы между разделами — без отдельных окон: прежний раздел снимком уходит в новый.
"""

import os

STRAY = []


def _watch_stray():
    """Виджет без родителя, показанный из кода, — отдельное окно на долю секунды (так мигали
    пустые окна). Окна по назначению — главное, вход, диалоги, всплывающие — не считаются."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog, QMainWindow, QMenu, QWidget
    if getattr(QWidget, "_kov_stray_watch", False):
        return
    plain = (Qt.WindowType.Widget, Qt.WindowType.Window)

    def stray(w):
        try:
            return (w.parentWidget() is None and not isinstance(w, (QMainWindow, QDialog, QMenu))
                    and w.windowType() in plain and type(w).__name__ != "QTipLabel")
        except RuntimeError:
            return False
    orig_show, orig_vis = QWidget.show, QWidget.setVisible

    def show(self):
        if stray(self):
            STRAY.append(type(self).__name__)
        orig_show(self)

    def set_visible(self, on):
        if on and stray(self):
            STRAY.append(type(self).__name__)
        orig_vis(self, on)
    QWidget.show = show
    QWidget.setVisible = set_visible
    QWidget._kov_stray_watch = True


def _dominant(img, rect=None):
    from collections import Counter
    from PySide6.QtCore import QRect
    r = rect or QRect(0, 0, img.width(), img.height())
    cnt = Counter()
    for x in range(r.left(), r.right() + 1, max(1, r.width() // 40)):
        for y in range(r.top(), r.bottom() + 1, max(1, r.height() // 12)):
            cnt[img.pixelColor(x, y).name()] += 1
    return cnt.most_common(1)[0][0] if cnt else ""


def _near(a, b, tol=40):
    from PySide6.QtGui import QColor
    ca, cb = QColor(a), QColor(b)
    return abs(ca.red() - cb.red()) + abs(ca.green() - cb.green()) + abs(ca.blue() - cb.blue()) <= tol


def steps(ctx):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication, QToolTip

    _watch_stray()
    restarts = []

    def no_restart(c):
        # перезапуск — это запуск нового процесса программы: считаем любые попытки
        from PySide6.QtCore import QProcess
        QProcess.startDetached = lambda *a, **k: restarts.append(a) or (False, 0)

    # ── окно входа: тема без нового окна ────────────────────────────────────
    def login_dark(c):
        c.data["login"] = id(c.login)
        c.login.login_input.setText(c.role)
        c.login.theme_btn.click()

    def login_dark_check(c):
        from general_functions import theme
        from kov.restyle import Reveal
        c.check("вход: тёмная тема — сразу, в том же окне", theme.MODE == "dark" and id(c.login) == c.data["login"]
                and c.login.isVisible() and not restarts, theme.MODE)
        c.check("вход: логин после смены темы на месте", c.login.login_input.text() == c.role, c.login.login_input.text())
        c.check("вход: анимация смены темы закончилась", not c.login.findChildren(Reveal))
        c.shot("a01_login_dark")
        c.login.theme_btn.click()

    def login_light_check(c):
        from general_functions import theme
        c.check("вход: светлая тема вернулась", theme.MODE == "light", theme.MODE)

    # ── переходы: без отдельных окон, прежний раздел плавно сменяется ───────
    def logged(c):
        if c.find_shell() is None:
            raise RuntimeError("главное окно не открылось")
        c.mark = c.watch.mark()
        del STRAY[:]

    def go_cover(route, **params):
        def fn(c):
            c.shell.go(route, **params)
            c.data["cover"] = c.shell._cover is not None
        return fn

    def cover_check(c):
        from kov.restyle import Reveal
        c.check("переход: прежний раздел остаётся снимком, пока новый подгружается", c.data.get("cover") is True)
        c.check("переход: снимок ушёл, раздел открыт", c.shell._cover is None and not c.shell.content.findChildren(Reveal))

    def filter_dates(c):
        cur = c.shell.current
        cur.filter = dict(cur.filter or {}, min_created_date="2026-01-01T00:00:00")
        c.later([(900, lambda c: c.shot_screen("a02_tasks_filter")), (1100, lambda c: c.close_modals())])
        cur.filter_dialog()

    def wh_toggle(c):
        wh = getattr(c.shell.current, "wh", None)
        if wh is None:
            raise RuntimeError("нет ведомости склада")
        wh._toggle_filled()

    def stray_check(c):
        c.check("переходы и перерисовка: ни одного отдельного окна (пустые окна на долю секунды)", not STRAY,
                ", ".join(STRAY[:10]))
        c.clean("переходы между разделами")

    # ── «Настройки»: тема и размер без перезапуска ──────────────────────────
    def settings(c):
        c.shell.go("settings")

    def dark(c):
        c.data["shell"] = id(c.shell)
        btn = c.shell.current.theme_pick.button("dark")
        c.data["origin"] = btn.mapTo(c.shell, btn.rect().center())
        btn.click()

    def dark_mid(c):
        from kov.restyle import Reveal
        rv = c.shell.findChildren(Reveal)
        c.data["reveal"] = bool(rv) and rv[0].origin is not None and \
            abs(rv[0].origin.x() - c.data["origin"].x()) <= 2 and abs(rv[0].origin.y() - c.data["origin"].y()) <= 2
        c.shot_screen("a03_theme_sweep")

    def dark_check(c):
        from general_functions import theme
        from kov.restyle import Reveal
        c.check("тема: круг новой темы — от нажатой кнопки, как у сайта", c.data.get("reveal") is True)
        c.check("тема: тёмная включилась в том же окне, без перезапуска", theme.MODE == "dark"
                and id(c.shell) == c.data["shell"] and c.shell.isVisible() and not restarts, theme.MODE)
        c.check("тема: снова «Настройки», анимация закончилась",
                type(c.shell.current).__name__ == "SettingsSection" and not c.shell.findChildren(Reveal))
        c.check("тема: выбор ушёл на сервер (web.settings.pc)",
                (c.state.M.PREFS.get((c.user_id, "web.settings.pc")) or {}).get("theme") == "dark")
        c.clean("смена темы в настройках")
        c.shot("a04_settings_dark")

    def scale_up(c):
        cur = c.shell.current
        cur.set_scale(1.1)
        cur.apply_btn.click()

    def scale_check(c):
        import scaler
        c.check("размер: применился без перезапуска", abs(scaler.scale_multiplier() - 1.1) < 0.01 and not restarts,
                "%.2f" % scaler.scale_multiplier())
        c.clean("смена размера")
        cur = c.shell.current
        cur.set_scale(1.0)
        cur.apply_btn.click()

    def light(c):
        c.shell.current.theme_pick.button("light").click()

    def light_check(c):
        import scaler
        from general_functions import theme
        c.check("тема и размер вернулись", theme.MODE == "light" and abs(scaler.scale_multiplier() - 1.0) < 0.01)
        c.check("перезапусков приложения не было", not restarts, "%d" % len(restarts))

    # ── подсказки: тёмная плашка и в прозрачных контейнерах ─────────────────
    def tip_settings(c):
        w = c.shell.current.window_pick.button("windowed")
        QToolTip.showText(w.mapToGlobal(QPoint(6, 6)), "Подсказка для проверки — обнимашки hug", w)

    def tip_check(name):
        def fn(c):
            from general_functions import theme
            tips = [x for x in QApplication.topLevelWidgets() if x.objectName() == "qtooltip_label" and x.isVisible()]
            if not tips:
                c.check(name + ": подсказка показана", False)
                return
            t = tips[0]
            img = QApplication.primaryScreen().grabWindow(0, t.x(), t.y(), t.width(), t.height()).toImage()
            bg = _dominant(img)
            c.check(name + ": тёмная плашка, светлый текст", _near(bg, theme.TOOLTIP_BG), "фон %s, нужен %s" % (bg, theme.TOOLTIP_BG))
            c.shot_screen("a05_tooltip_" + ("chat" if "мессенджер" in name else "settings"))
            QToolTip.hideText()
        return fn

    def tip_chat(c):
        host = c.shell.messenger_host()
        target = host.view if host.view is not None else host
        QToolTip.showText(target.mapToGlobal(QPoint(40, 40)), "Панда — обнимашки обнять hug", target)

    # ── флажки: квадрат цвета акцента и белая галочка ───────────────────────
    def checkboxes(c):
        from kov import people

        def look(c):
            from PySide6.QtWidgets import QCheckBox, QStyle, QStyleOptionButton
            boxes = [b for b in c.top().findChildren(QCheckBox) if b.isEnabled() and b.isVisible()]
            if not boxes:
                c.check("флажки: в списке сотрудников есть флажки", False)
                return
            cb = boxes[0]
            cb.setChecked(True)
            QApplication.processEvents()
            opt = QStyleOptionButton()
            cb.initStyleOption(opt)
            r = cb.style().subElementRect(QStyle.SE_CheckBoxIndicator, opt, cb)
            img = cb.grab().toImage()
            dpr = img.devicePixelRatio() or 1.0
            white = accent = 0
            from general_functions import theme
            for x in range(int(r.left() * dpr), int((r.right() + 1) * dpr)):
                for y in range(int(r.top() * dpr), int((r.bottom() + 1) * dpr)):
                    col = img.pixelColor(x, y)
                    if col.red() > 225 and col.green() > 225 and col.blue() > 225:
                        white += 1
                    elif _near(col.name(), theme.ACCENT_FILL, 30):
                        accent += 1
            c.check("флажки: отмеченный — квадрат цвета акцента с белой галочкой", white >= 6 and accent >= 20,
                    "белых точек %d, цвета акцента %d" % (white, accent))
            c.shot_screen("a06_checkboxes")
        c.later([(1500, look), (1800, lambda c: c.close_modals())])
        people.employee_picker(c.shell, "Проверка флажков", lambda ids: None, multi=True)

    # ── этап задачи и журнал ────────────────────────────────────────────────
    def task(c):
        c.shell.go("task", task_id=101)

    def stage2(c):
        c.shell.current.select_stage(2)

    def complete(c):
        cur = c.shell.current
        c.later([(900, lambda c: c.click(c.top(), "Завершить"))])
        c.click(cur, "Завершить этап")

    def complete_check(c):
        t = next(x for x in c.state.M.TASKS if x["id"] == 101)
        done = next(s for s in t["stages"] if s[0] == 2)[4]
        c.check("этап: завершён на сервере", done is True)
        c.check("этап: в окне — «Возобновить этап»", c.button(c.shell.current, "Возобновить этап") is not None)
        c.shot("a07_stage_done")

    def resume(c):
        cur = c.shell.current
        c.later([(900, lambda c: c.click(c.top(), "Возобновить"))])
        c.click(cur, "Возобновить этап")

    def journal(c):
        from PySide6.QtWidgets import QLabel

        def look(c):
            texts = " | ".join(lab.text() for lab in c.top().findChildren(QLabel))
            c.check("журнал задачи: завершение этапа записано", "Завершение этапа" in texts)
            c.check("журнал задачи: возобновление этапа записано", "Этап возобновлён" in texts)
            c.shot_screen("a08_journal")
        t = next(x for x in c.state.M.TASKS if x["id"] == 101)
        c.check("этап: возобновлён на сервере", next(s for s in t["stages"] if s[0] == 2)[4] is False)
        c.later([(1600, look), (1900, lambda c: c.close_modals())])
        c.shell.current.history()

    # ── шаблоны задач: сохраняются; вложенное окно не оставляет затемнения ──
    NAME = "Шаблон проверки ПК"

    def templates(c):
        from PySide6.QtWidgets import QLabel, QLineEdit
        from kov.ui import Button

        def row_button(c, name, tip):
            for lab in c.top().findChildren(QLabel):
                if name in lab.text():
                    return next((b for b in lab.parentWidget().findChildren(Button) if b.toolTip() == tip), None)
            return None

        def create(c):
            c.data["tpl_modal"] = c.top()
            c.click(c.top(), "Создать шаблон")          # дальше — вложенное окно, его ведут таймеры ниже

        def fill(c, text):
            c.top().findChildren(QLineEdit)[0].setText(text)
            c.click(c.top(), "Сохранить")

        def listed(c, name):
            return any(name in lab.text() for lab in c.data["tpl_modal"].findChildren(QLabel))

        def after_create(c):
            c.check("шаблоны: новый сохранён", any(t["template_name"] == NAME for t in c.state.M.TEMPLATES))
            c.check("шаблоны: новый виден в списке", listed(c, NAME))
            m = c.data["tpl_modal"]
            dim = [w for w in m.card.children() if hasattr(w, "styleSheet") and "rgba(" in (w.styleSheet() or "")
                   and w.isVisible()]
            c.check("шаблоны: после вложенного окна затемнения на окне нет", not dim, "%d слоёв" % len(dim))
            c.shot_screen("a09_templates")
            b = row_button(c, NAME, "Изменить шаблон")
            if b is None:
                raise RuntimeError("нет кнопки «Изменить шаблон»")
            b.click()

        def after_edit(c):
            c.check("шаблоны: изменение сохранено", any(t["template_name"] == NAME + " 2" for t in c.state.M.TEMPLATES))
            b = row_button(c, NAME + " 2", "Удалить шаблон")
            if b is None:
                raise RuntimeError("нет кнопки «Удалить шаблон»")
            b.click()

        def after_delete(c):
            c.check("шаблоны: удаление сохранено", not any(NAME in t["template_name"] for t in c.state.M.TEMPLATES))
            c.close_modals()
        c.later([(1500, create), (2400, lambda c: fill(c, NAME)), (3600, after_create),
                 (4500, lambda c: fill(c, NAME + " 2")), (5700, after_edit),
                 (6600, lambda c: c.click(c.top(), "Удалить")), (7800, after_delete)])
        c.shell.current.templates_dialog()

    # ── загрузки: готовые исчезают сами, крестик убирает их насовсем ────────
    def dl_one(c, name):
        from kov import files as kfiles
        return kfiles.download(c.shell, {"src": "mock/file/" + name, "filename": name})

    def dl_start(c):
        dl_one(c, "proverka-1.bin")

    def dl_shown(c):
        from kov import files as kfiles
        p = kfiles._panel_for(c.shell)
        c.check("загрузки: панель с готовым файлом", p.isVisible() and len(p.items) == 1 and p.items[0]["ok"],
                "строк %d" % len(p.items))
        c.shot("a10_downloads")

    def dl_gone(c):
        from kov import files as kfiles
        p = kfiles._panel_for(c.shell)
        c.check("загрузки: готовое исчезает само через несколько секунд", not p.isVisible() and not p.items,
                "строк %d" % len(p.items))
        from kov import files as kf
        kf.download(c.shell, {"src": "mock/no-such-file", "filename": "нет-такого.bin"})

    def dl_error(c):
        from kov import files as kfiles
        p = kfiles._panel_for(c.shell)
        c.check("загрузки: ошибка остаётся на виду", p.isVisible() and len(p.items) == 1 and not p.items[0]["ok"])
        p.close_btn.click()
        c.check("загрузки: крестик убирает завершённые", not p.isVisible() and not p.items)
        dl_one(c, "proverka-2.bin")

    def dl_fresh(c):
        from kov import files as kfiles
        p = kfiles._panel_for(c.shell)
        c.check("загрузки: новое скачивание — без прежних строк", p.isVisible() and len(p.items) == 1, "строк %d" % len(p.items))
        p.clear_done()

    # ── «Медиаконтент» проекта: только фото и видео ─────────────────────────
    def media(c):
        c.shell.go("project", project_id=1)

    def media_open(c):
        c.shell.current.open_section("photos")

    def media_check(c):
        from kov import files as kfiles
        from kov.views.projects import MediaContent
        mc = c.shell.current.findChildren(MediaContent)
        if not mc:
            c.check("медиаконтент: раздел открыт", False)
            return
        extra = mc[0].findChildren(kfiles.CategoryBlock)
        if not extra:
            folder = mc[0].lst.itemAt(0).widget()
            folder.toggle(True)
            extra = mc[0].findChildren(kfiles.CategoryBlock)
        block = extra[0] if extra else None
        c.check("медиаконтент: «Дополнительные файлы» принимают только фото и видео",
                block is not None and block.accept == "media" and block._fits("x.jpg") and block._fits("x.mp4")
                and not block._fits("x.txt") and not block._fits("x.docx"))
        c.check("медиаконтент: в окне выбора — только фото и видео", "*.jpg" in kfiles.MEDIA_FILTER and "*.txt" not in kfiles.MEDIA_FILTER)
        if block is not None:
            txt = os.path.join(c.out, "заметка.txt")
            with open(txt, "w", encoding="utf-8") as f:
                f.write("проверка")
            mark = c.watch.mark()
            block.upload_paths([txt])
            _u, _j, toasts = c.watch.since(mark)
            c.check("медиаконтент: документ не принимается, объяснение на экране",
                    block.task is None and any("только фото и видео" in t["title"] for t in toasts),
                    "; ".join(t["title"] for t in toasts))
        c.shot("a11_media")
        c.mark = c.watch.mark()

    # ── оборудование: кнопка снимка внутри рамки ────────────────────────────
    def equipment(c):
        c.shell.go("equipment")

    def photo_button(c):
        def look(c):
            from kov.ui import Button
            cam = next((b for b in c.top().findChildren(Button) if b.toolTip() == "Выбрать снимок"), None)
            if cam is None:
                c.check("оборудование: кнопка снимка есть", False)
                return
            box = cam.parentWidget()
            g = cam.geometry()
            c.check("оборудование: кнопка снимка — внутри рамки снимка",
                    g.left() >= 0 and g.top() >= 0 and g.right() < box.width() and g.bottom() < box.height()
                    and g.height() == g.width(), "кнопка %s, рамка %dx%d" % (g.getRect(), box.width(), box.height()))
            c.shot_screen("a12_equipment_photo")
        c.later([(1200, look), (1400, lambda c: c.close_modals())])
        c.shell.current.add_dialog()

    # ── просмотр: «1 из 2» без подложки ─────────────────────────────────────
    def viewer(c):
        from kov.api import client
        from kov.viewer import MediaViewer
        files = []
        with open(os.path.join(c.media, "facade.jpg"), "rb") as f:
            data = f.read()
        for name in ("Снимок 1.jpg", "Снимок 2.jpg"):
            files += client.post("upload", data={"task_id": "101"}, files=[("files", (name, data, "image/jpeg"))]) or []
        c.data["viewer"] = MediaViewer.show_list(c.shell, [dict(f, file_kind="task") for f in files], 0)

    def viewer_check(c):
        v = c.data.get("viewer")
        ok = v is not None and v.counter.isVisible() and v.counter.text() == "1 из 2" and \
            "background: transparent" in v.counter.styleSheet() and "rgba(255,255,255,40)" not in v.counter.styleSheet()
        c.check("просмотр: счётчик «1 из 2» — текстом, без подложки", ok, v.counter.styleSheet() if v else "")
        c.shot("a13_viewer")
        if v is not None:
            v.close_viewer()

    s = [("без перезапусков", no_restart, 100),
         ("вход: тёмная тема", login_dark, 1500), ("вход: проверка темы", login_dark_check, 1500),
         ("вход: светлая тема", login_light_check, 200),
         ("вход", lambda c: c.do_login(), 3000), ("главное окно", logged, 800)]
    for route, params, name in (("tasks", {}, "tasks"), ("task", {"task_id": 101}, "task"),
                                ("projects", {}, "projects"), ("project", {"project_id": 1}, "project"),
                                ("warehouse", {"project_id": 1}, "warehouse")):
        s += [("переход: " + name, go_cover(route, **params), 1800), ("переход: проверка " + name, cover_check, 200)]
    s += [("склад: только заполненные", wh_toggle, 700), ("склад: все строки", wh_toggle, 700),
          ("задачи", go_cover("tasks"), 1600), ("задачи: фильтр с датами", filter_dates, 400)]
    for route in ("equipment", "employees", "organizations", "notifications", "csv"):
        s += [("переход: " + route, go_cover(route), 1600), ("переход: проверка " + route, cover_check, 200)]
    s += [("переходы: итог", stray_check, 200),
          ("настройки", settings, 1800), ("тёмная тема", dark, 180), ("круг темы", dark_mid, 1400),
          ("тёмная: проверка", dark_check, 300), ("размер 1.1", scale_up, 1600), ("размер: проверка", scale_check, 1600),
          ("светлая тема", light, 1600), ("светлая: проверка", light_check, 300),
          ("подсказка в настройках", tip_settings, 700), ("подсказка: проверка", tip_check("подсказка в настройках"), 300),
          ("мессенджер", lambda c: c.shell.go("messenger"), 3500),
          ("подсказка в мессенджере", tip_chat, 700), ("подсказка: проверка", tip_check("подсказка в мессенджере"), 300),
          ("флажки", checkboxes, 400),
          ("задача", task, 2200), ("этап 2", stage2, 1500), ("завершить этап", complete, 2200),
          ("этап завершён", complete_check, 300), ("возобновить этап", resume, 2200), ("журнал задачи", journal, 400),
          ("новая задача", lambda c: c.shell.go("task_new"), 2000), ("шаблоны задач", templates, 600),
          ("скачать", dl_start, 1600), ("загрузки: панель", dl_shown, 7600), ("загрузки: исчезли", dl_gone, 1600),
          ("загрузки: ошибка", dl_error, 1600), ("загрузки: новая", dl_fresh, 300),
          ("проект", media, 2000), ("медиаконтент", media_open, 2000), ("медиаконтент: проверка", media_check, 300),
          ("оборудование", equipment, 2000), ("кнопка снимка", photo_button, 400),
          ("просмотр", viewer, 2500), ("просмотр: проверка", viewer_check, 600),
          ("итог", lambda c: (c.check("отдельных окон за весь прогон нет", not STRAY, ", ".join(STRAY[:10])),
                              c.clean("действия")), 200)]
    return s
