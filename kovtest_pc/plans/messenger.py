# -*- coding: utf-8 -*-
"""Мессенджер ПК-версии (роль director тестового сервера): окна и работа.

Окна — как у сайта: новый чат, выбор сотрудника, новая группа, карточка группы и её
вложения, настройки и название группы, отправка файлов, правка фото (кисть, размытие,
обрезка), обрезка видео; лента, панель эмодзи, поиск, меню сообщения.

Работа — что дошло до сервера: текст, реакция, правка, удаление; отправка четырёх файлов
одной группой со сжатием на компьютере (мелкое фото — как есть, крупное — WebP 3200,
видео с обрезкой 1:1 и отрезком по времени — H.264 + AAC, смета — как есть или gzip);
пересылка; группа (создать, участники, переименовать, удалить); входящее сообщение →
карточка уведомления → переход в чат.
"""

import json
import os
import subprocess
import tempfile

TEXT = "Проверка связи с ПК"
EDITED = TEXT + " — исправлено"
CAPTION = "Фото, видео и смета"
GROUP = "Проверка ПК"
INCOMING = "Входящее: привезли кабель, проверьте"
MENU_JS = ("(function(){var b=document.querySelectorAll('#messagesList .msg-bubble'); var r=b[1].getBoundingClientRect();"
           " b[1].dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:r.left+30,clientY:r.top+12}));})()")


def msgs(c, cid):
    with c.L.LOCK:
        return [dict(m) for m in (c.L.MESSAGES.get(cid) or [])]


def big_photo(c):
    """Крупное фото без файла в комплекте: 3600×2400 с шумом — PNG в несколько МБ."""
    path = os.path.join(c.out, "big_photo.png")
    if not os.path.isfile(path):
        from PIL import Image, ImageDraw
        w, h = 3600, 2400
        grad = Image.linear_gradient("L")
        base = Image.merge("RGB", (grad.resize((w, h)), grad.rotate(90).resize((w, h)), grad.rotate(45).resize((w, h))))
        noise = Image.effect_noise((w, h), 40).convert("RGB")
        img = Image.blend(base, noise, 0.1)
        d = ImageDraw.Draw(img)
        d.rectangle((600, 900, 3000, 2000), fill=(46, 64, 83))
        d.ellipse((2500, 300, 3100, 900), fill=(231, 76, 60))
        img.save(path)
    return path


def ffprobe(c, data, suffix):
    from kov.media import video
    exe = video.ffprobe_path()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, dir=c.out) as f:
        f.write(data)
        p = f.name
    try:
        kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
        out = subprocess.run([exe, "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height,"
                              "sample_rate,channels,pix_fmt,profile:format=duration", "-of", "json", p],
                             capture_output=True, text=True, timeout=60, **kw).stdout
        return json.loads(out or "{}")
    finally:
        os.unlink(p)


def steps(ctx):
    be = lambda c: c.be()

    # ── окна ────────────────────────────────────────────────────────────────
    def add_menu(c):
        c.later([(1400, lambda c: c.shot_screen("m_add")), (1600, lambda c: c.close_modals())])
        be(c).show_add_menu()

    def new_group_shot(c):
        from PySide6.QtWidgets import QLineEdit
        from kov.messenger import dialogs
        c.later([(1100, lambda c: c.top().findChildren(QLineEdit)[0].setText("Бригада монтажа")),
                 (1400, lambda c: c.shot_screen("m_newgroup")), (1600, lambda c: c.close_modals())])
        dialogs.new_group_dialog(be(c))

    def picker(c):
        from kov import people
        from kov.messenger import dialogs

        def look(c):
            from PySide6.QtWidgets import QRadioButton
            dlg = c.top()
            radios = dlg.findChildren(QRadioButton)
            off = [r for r in radios if not r.isEnabled()]
            c.check("выбор сотрудника: круглые переключатели, с кем чат есть — неактивны", len(radios) > 3 and len(off) >= 1,
                    "%d всего, %d неактивны" % (len(radios), len(off)))
            on = [r for r in radios if r.isEnabled()][:2]
            on[0].click()
            on[1].click()
            c.check("выбор сотрудника: выбран только один", sum(1 for r in radios if r.isChecked()) == 1)
            c.shot_screen("m_picker")
        c.later([(1800, look), (2100, lambda c: c.close_modals())])
        people.employee_picker(be(c).host, "Выберите сотрудника", lambda ids: None, multi=False,
                               taken=dialogs.private_partners(be(c)))

    def group_card(c):
        c.later([(2200, lambda c: c.shot_screen("m_groupcard"))])
        be(c).request_chat_info_card("900")

    def media_list(c):
        from PySide6.QtWidgets import QPushButton
        dlg = c.top()
        before = dlg.height()

        def look(c, before=before):
            c.shot_screen("m_media")
            c.check("вложения группы: окно по размеру списка", c.top().height() < before, "%d → %d" % (before, c.top().height()))
        c.later([(1500, look), (1700, lambda c: c.close_modals())])
        [b for b in dlg.findChildren(QPushButton) if b.toolTip() == "Фото"][0].click()

    def settings(c):
        from kov.messenger import dialogs
        c.later([(1800, lambda c: c.shot_screen("m_settings")), (2000, lambda c: c.close_modals())])
        dialogs.group_settings(be(c), 900)

    def rename_shot(c):
        from kov.messenger import dialogs

        def look(c):
            from PySide6.QtWidgets import QLineEdit
            e = c.top().findChildren(QLineEdit)[0]
            c.check("название группы: текст не выделен, курсор в конце", not e.hasSelectedText() and e.cursorPosition() == len(e.text()))
            c.shot_screen("m_rename")
        c.later([(1200, look), (1400, lambda c: c.close_modals())])
        dialogs.rename_dialog(be(c), 900, "Монтаж «Северный»")

    def drag(widget, a, b, steps=8):
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        left, none = Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton

        def ev(kind, p, button, buttons):      # Qt6: точка в окне и на экране
            p = QPointF(p)
            return QMouseEvent(kind, p, QPointF(widget.mapToGlobal(p.toPoint())), button, buttons, Qt.KeyboardModifier.NoModifier)
        widget.mousePressEvent(ev(QEvent.Type.MouseButtonPress, a, left, left))
        for k in range(1, steps + 1):
            p = QPointF(a.x() + (b.x() - a.x()) * k / steps, a.y() + (b.y() - a.y()) * k / steps)
            widget.mouseMoveEvent(ev(QEvent.Type.MouseMove, p, left, left))
        widget.mouseReleaseEvent(ev(QEvent.Type.MouseButtonRelease, b, left, none))

    def at(v, fx, fy):
        from PySide6.QtCore import QPoint
        return QPoint(int(v.x() + v.width() * fx), int(v.y() + v.height() * fy))

    def paint(c):
        ed = c.top()
        ed.set_tool("draw")
        v = ed.stage.view()
        drag(ed.stage, at(v, 0.2, 0.3), at(v, 0.6, 0.45))
        ed.pick_color("#E53935")
        drag(ed.stage, at(v, 0.25, 0.7), at(v, 0.7, 0.62))

    def blur(c):
        ed = c.top()
        ed.set_tool("blur")
        v = ed.stage.view()
        drag(ed.stage, at(v, 0.6, 0.25), at(v, 0.85, 0.3), 14)

    def crop(c):
        ed = c.top()
        ed.set_tool("crop")
        v = ed.stage.view()
        drag(ed.stage, at(v, 0.0, 0.0), at(v, 0.15, 0.12))
        ed.stage.update()

    def centered(c, name):
        dlg = c.top()
        main = c.shell.frameGeometry()
        g = dlg.frameGeometry()
        c.check("%s: окно внутри главного и по центру" % name, main.contains(g) and abs(g.center().y() - main.center().y()) < 40,
                "окно %d,%d %d×%d" % (g.x(), g.y(), g.width(), g.height()))

    def video_crop(c):
        ed = c.top()
        ed.toggle_crop()
        ed.pick_ratio(1.0, ed.ratio_btns[1])
        ed.start = 1.0
        ed.end = 3.2
        ed.paint_track()

    def editors(c):
        m = c.media
        c.later([(2500, lambda c: c.shot_screen("m_send")),
                 (2700, lambda c: c.top().edit(0)),
                 (4200, lambda c: (c.shot_screen("m_imgedit"), centered(c, "правка фото"))),
                 (4400, paint), (4900, lambda c: c.shot_screen("m_imgedit_draw")),
                 (5100, blur), (5600, lambda c: c.shot_screen("m_imgedit_blur")),
                 (5800, crop), (6300, lambda c: c.shot_screen("m_imgedit_crop")),
                 (6500, lambda c: c.top()._done(None) and c.top().accept()),
                 (7500, lambda c: c.shot_screen("m_send_edited")),
                 (7700, lambda c: c.top().edit(1)),
                 (10500, lambda c: (c.shot_screen("m_videdit"), centered(c, "обрезка видео"))),
                 (10700, video_crop), (11800, lambda c: c.shot_screen("m_videdit_crop")),
                 (12000, lambda c: c.top()._done(None) and c.top().accept()),
                 (12800, lambda c: c.shot_screen("m_send_final")),
                 (13000, lambda c: c.close_modals())])
        be(c).open_send_dialog([os.path.join(m, "facade.jpg"), os.path.join(m, "clip.mp4"), os.path.join(m, "smeta.csv")])

    # ── работа ──────────────────────────────────────────────────────────────
    def send_text(c):
        c.mark = c.watch.mark()
        c.js("var i=document.getElementById('messageInput'); i.value=%s; i.dispatchEvent(new Event('input'));"
             " document.getElementById('btnSend').click();" % json.dumps(TEXT, ensure_ascii=False))

    def after_text(c):
        m = msgs(c, 33)[-1]
        c.data["mid"] = m["id"]
        c.check("текст: сервер получил сообщение", m.get("text") == TEXT and m.get("sender_id") == c.user_id, m.get("text"))
        c.ask("last", "(function(){var x=document.getElementById('msg-%d'); var t=document.querySelectorAll('[id^=\"msg-tmp\"]').length;"
                      " return x ? (x.id + '|' + t + '|' + x.innerText.replace(/\\s+/g,' ')) : '';})()" % m["id"])

    def after_text_dom(c):
        v = c.R.get("last") or ""
        c.check("текст: в ленте настоящее сообщение, временного не осталось", v.startswith("msg-%d|0|" % c.data["mid"]) and TEXT in v, v[:120])

    def after_react(c):
        with c.L.LOCK:
            r = dict(c.L.REACTIONS.get(c.data["mid"]) or {})
        c.check("реакция: сервер записал «like»", r.get(c.user_id) == "like", r)
        c.ask("react", "(function(){var m=document.getElementById('msg-%d'); var r=m && m.querySelector('.reaction-chip');"
                       " return r ? r.innerText.replace(/\\s+/g,' ') : '';})()" % c.data["mid"])

    def after_edit(c):
        m = next((x for x in msgs(c, 33) if x["id"] == c.data["mid"]), {})
        c.check("правка: текст на сервере", m.get("text") == EDITED and m.get("is_edited"), (m.get("text"), m.get("is_edited")))
        c.ask("edit", "(function(){var m=document.getElementById('msg-%d'); return m ? m.innerText.replace(/\\s+/g,' ') : '';})()"
              % c.data["mid"])

    def after_delete(c):
        c.check("удаление: на сервере нет сообщения", all(x["id"] != c.data["mid"] for x in msgs(c, 33)))
        c.ask("del", "!!document.getElementById('msg-%d')" % c.data["mid"])

    def send_files(c):
        orig = c.L.resolve_items
        c.data["captured"] = []

        def capture(meta, files):
            items = orig(meta, files)
            c.data["captured"].append({"meta": meta, "items": [(it["name"], len(it["data"])) for it in items]})
            return items
        c.L.resolve_items = capture
        c.data["before"] = max([m["id"] for m in msgs(c, 33)] or [0])
        photo = big_photo(c)                    # до таймеров: файл готовится несколько секунд
        c.later([(1800, lambda c: c.top().edit(2)), (5000, video_crop),
                 (5600, lambda c: c.top()._done(None) and c.top().accept()),
                 (6600, lambda c: c.top().caption.setPlainText(CAPTION)),
                 (6900, lambda c: c.shot_screen("f_send")),
                 (7200, lambda c: c.click(c.top(), "Отправить"))])
        m = c.media
        be(c).open_send_dialog([os.path.join(m, "facade.jpg"), photo, os.path.join(m, "clip.mp4"), os.path.join(m, "smeta.csv")])

    def wait_files(c):
        """Сжатие и отправка идут в фоне — ждём сообщение на сервере (шаг повторяется до 90 с)."""
        new = [m for m in msgs(c, 33) if m["id"] > c.data.get("before", 0) and m.get("special") == "file_group"]
        if not new:
            return c.AGAIN
        return None

    def after_files(c):
        new = [m for m in msgs(c, 33) if m["id"] > c.data.get("before", 0)]
        parent = next((m for m in new if m.get("special") == "file_group"), None)
        items = [m for m in new if m.get("special") == "file_item"]
        c.check("файлы: одно сообщение-группа с подписью", parent is not None and parent.get("text") == CAPTION,
                parent and parent.get("text"))
        c.check("файлы: четыре вложения", len(items) == 4, [m["files"][0]["file_name"] for m in items])
        cap = (c.data.get("captured") or [{"meta": [], "items": []}])[-1]
        meta = cap["meta"]
        stored = [n for n, _s in cap["items"]]
        by = {m.get("name"): m for m in meta}
        small = by.get("facade.jpg") or {}
        c.check("фото до 300 КБ — без пересжатия (как сайт)", small.get("prep") == "original", small.get("prep"))
        big = next((m for m in meta if str(m.get("name", "")).startswith("big_photo")), {})
        c.check("крупное фото — WebP, длинная сторона 3200", "big_photo.webp" in stored and big.get("prep") == "image"
                and max(big.get("width") or 0, big.get("height") or 0) == 3200, (stored, big.get("width"), big.get("height")))
        vid = next((m for m in meta if str(m.get("name", "")).endswith(".mp4")), {})
        c.check("видео сжато на компьютере, правки применены", vid.get("prep") == "device" and vid.get("src") == "session"
                and vid.get("edits_applied") is True, {k: vid.get(k) for k in ("prep", "src", "edits_applied", "prep_reason")})
        c.check("видео: кадр 1:1 1080×1080, длина 2,2 с", vid.get("width") == 1080 and vid.get("height") == 1080
                and abs(float(vid.get("duration") or 0) - 2.2) < 0.15, (vid.get("width"), vid.get("height"), vid.get("duration")))
        doc = by.get("smeta.csv") or {}
        c.check("смета: gzip или как есть, если сжатие не выгодно", doc.get("enc") in ("gzip", "identity", None), doc.get("enc"))
        for m in items:
            f = m["files"][0]
            blob = c.L.BLOBS.get(f.get("path") or f.get("file_path")) or {}
            data = blob.get("data") or b""
            name = f.get("file_name") or ""
            if name.endswith(".mp4"):
                info = ffprobe(c, data, ".mp4")
                st = {s["codec_type"]: s for s in info.get("streams", [])}
                v, a = st.get("video", {}), st.get("audio", {})
                c.check("видео на сервере: H.264 High + AAC, 1080×1080, 8 бит 4:2:0", v.get("codec_name") == "h264"
                        and v.get("profile") == "High" and a.get("codec_name") == "aac" and v.get("width") == 1080
                        and v.get("height") == 1080 and v.get("pix_fmt") in ("yuv420p", "yuvj420p"),
                        (v.get("codec_name"), v.get("profile"), v.get("width"), v.get("height"), v.get("pix_fmt"),
                         a.get("codec_name"), a.get("channels"), info.get("format", {}).get("duration")))
            elif name.endswith(".webp"):
                c.check("фото на сервере — настоящий WebP", data[:4] == b"RIFF" and data[8:12] == b"WEBP", data[:12])
            elif name.endswith(".csv"):
                c.check("смета на сервере совпадает с исходной", data == open(os.path.join(c.media, "smeta.csv"), "rb").read(), len(data))
        c.ask("files", "(function(){var w=document.querySelectorAll('#messagesList .msg-wrapper'); var t='';"
                       " for (var i=0;i<w.length;i++) t+=w[i].innerText+' | '; return t.replace(/\\s+/g,' ');})()")

    def forward(c):
        from PySide6.QtWidgets import QLabel
        src = next(m for m in msgs(c, 33) if m.get("text") and m.get("sender_id") != c.user_id and not m.get("special"))
        c.data["fwd"] = src

        def pick(c):
            dlg = c.top()
            rows = [w for w in dlg.findChildren(QLabel) if w.text() == "Кузнецова Мария"]
            row = rows[0].parentWidget() if rows else None
            while row is not None and type(row).__name__ != "_ForwardRow":
                row = row.parentWidget()
            row.on_toggle()
        c.later([(1500, pick), (1900, lambda c: c.shot_screen("f_forward")), (2200, lambda c: c.click(c.top(), "Переслать"))])
        be(c).forward_message(str(src["id"]), src["text"])

    def after_forward(c):
        last = (msgs(c, 35) or [{}])[-1]
        src = c.data["fwd"]
        ff = (last.get("metadata") or {}).get("forwarded_from") or {}
        c.check("пересылка: копия в чате с Кузнецовой", last.get("text") == src.get("text") and ff.get("user_id") == src.get("sender_id"),
                (last.get("text"), ff.get("name")))

    def new_group(c):
        from PySide6.QtWidgets import QCheckBox, QLineEdit
        from kov.messenger import dialogs

        def members(c):
            boxes = [b for b in c.top().findChildren(QCheckBox) if b.isEnabled()]
            for b in boxes[:2]:
                b.click()
        c.data["groups_before"] = set(c.M.GROUPS)
        c.later([(1000, lambda c: c.top().findChildren(QLineEdit)[0].setText(GROUP)),
                 (1300, lambda c: c.click(c.top(), "Создать")),
                 (4200, members), (4600, lambda c: c.shot_screen("f_members")),
                 (4900, lambda c: c.click(c.top(), "Добавить"))])
        dialogs.new_group_dialog(be(c))

    def after_group(c):
        new = [g for g in c.M.GROUPS if g not in c.data["groups_before"]]
        gid = new[0] if new else None
        c.data["gid"] = gid
        g = c.M.GROUPS.get(gid) or {}
        c.check("группа создана на сервере", g.get("name") == GROUP, (gid, g.get("name")))
        c.check("участники добавлены (я + двое)", len(g.get("members") or ()) >= 3, sorted(g.get("members") or ()))
        c.check("открыта переписка новой группы", str(be(c).active_chat_id) == str(gid), be(c).active_chat_id)

    def rename(c):
        from PySide6.QtWidgets import QLineEdit
        from kov.messenger import dialogs
        c.later([(900, lambda c: c.top().findChildren(QLineEdit)[0].setText(GROUP + " — 2")),
                 (1200, lambda c: c.click(c.top(), "Сохранить"))])
        dialogs.rename_dialog(be(c), c.data["gid"], GROUP)

    def after_rename(c):
        c.check("переименование на сервере", (c.M.GROUPS.get(c.data["gid"]) or {}).get("name") == GROUP + " — 2")
        c.ask("title", "document.getElementById('headerTitle').innerText")

    def delete_group(c):
        from kov.messenger import dialogs
        c.later([(1600, lambda c: c.click(c.top(), "Удалить группу")),
                 (2400, lambda c: c.shot_screen("f_delete_confirm")),
                 (2600, lambda c: c.click(c.top(), "Удалить"))])
        dialogs.group_settings(be(c), c.data["gid"])

    def after_delete_group(c):
        c.check("группа удалена на сервере", c.data["gid"] not in c.M.GROUPS)
        c.check("группы нет в списке чатов", all(str(x.get("id")) != str(c.data["gid"]) for x in be(c).chats or []))

    def incoming(c):
        """Кузнецова пишет в личный чат, пока открыт другой, — как сервер на сообщение по WS."""
        cid, other = 35, 5
        members = c.M.chat_members(cid)
        c.L.seed(cid, members, c.M.EMP)
        msg = c.L.add_text(cid, other, c.M.EMP, INCOMING)
        c.M.broadcast_chat(cid, dict(msg, type="message", client_id=None, reply_to_message=None))
        c.M.chats_updated(members)

    def after_incoming(c):
        from PySide6.QtWidgets import QLabel
        from kov import notify_ui
        from kov.notify_center import center
        cards = [w for w in notify_ui._STACK if getattr(w, "tag", None) == "chat-35"]
        texts = [lab.text() for lab in cards[0].findChildren(QLabel) if lab.text()] if cards else []
        c.check("входящее: карточка уведомления", bool(cards) and any(INCOMING in t for t in texts), texts)
        c.check("входящее: открыт другой чат", center.active_chat == 900, center.active_chat)
        c.ask("preview", "(function(){var i=document.querySelector('.chat-item[data-chat-id=\"35\"]');"
                         " return i ? i.innerText.replace(/\\s+/g,' ') : '';})()")
        c.data["card"] = cards[0] if cards else None
        c.shot_screen("f_incoming")

    def open_card(c):
        c.check("входящее: строка чата с новым сообщением", INCOMING[:20] in (c.R.get("preview") or ""), c.R.get("preview"))
        card = c.data.get("card")
        if card is not None and card.on_click:
            card.on_click()

    def after_card(c):
        c.check("нажатие на карточку открывает чат", str(be(c).active_chat_id) == "35", be(c).active_chat_id)
        host = c.shell.messenger_host()
        c.check("переход из мессенджера в мессенджер: окно чата на месте", host.isVisible() and host.width() > 400,
                "%s %d×%d" % ("видно" if host.isVisible() else "скрыто", host.width(), host.height()))
        c.ask("title2", "document.getElementById('headerTitle').innerText")

    def clean_after(name):
        return (name + ": проверка", lambda c: c.clean(name), 100)

    s = [
        ("вход", lambda c: c.do_login(), 3000),
        ("главное окно", lambda c: c.find_shell() and setattr(c, "mark", c.watch.mark()), 300),
        ("раздел", lambda c: c.shell.go("messenger"), 4500),
        ("список", lambda c: c.shot("m_list"), 200),
        clean_after("мессенджер: список"),
        ("чат 33", lambda c: be(c).open_chat(33), 3000),
        ("чат 33: снимок", lambda c: c.shot("m_chat"), 200),
        ("группа 900", lambda c: be(c).open_chat(900), 3000),
        ("группа 900: снимок", lambda c: c.shot("m_group"), 200),
        ("меню сообщения", lambda c: c.js(MENU_JS), 1200),
        ("меню: снимок", lambda c: c.shot("m_menu"), 300),
        ("меню: закрыть", lambda c: c.js("if (typeof closeContextMenu === 'function') closeContextMenu();"), 500),
        ("эмодзи", lambda c: c.js("document.getElementById('btnEmoji').click()"), 1500),
        ("эмодзи: снимок", lambda c: c.shot("m_emoji"), 300),
        ("эмодзи: закрыть", lambda c: c.js("document.getElementById('btnEmoji').click()"), 600),
        ("поиск", lambda c: c.js("document.getElementById('btnSearchChat').click(); var i=document.getElementById('messageSearchInput');"
                                 " i.value='объект'; i.dispatchEvent(new Event('input'));"), 2500),
        ("поиск: снимок", lambda c: c.shot("m_search"), 300),
        ("поиск: результаты", lambda c: c.ask("found", "document.querySelectorAll('#searchResultsList > *').length"), 400),
        ("поиск: проверка", lambda c: c.check("поиск по чату находит сообщения", (c.R.get("found") or 0) >= 1, c.R.get("found")), 100),
        clean_after("мессенджер: лента, меню, эмодзи, поиск"),
        ("новый чат", add_menu, 600),
        ("новая группа: окно", new_group_shot, 600),
        ("выбор сотрудника", picker, 600),
        ("карточка группы", group_card, 2600),
        ("вложения группы", media_list, 2200),
        ("настройки группы", settings, 2600),
        ("название группы", rename_shot, 600),
        ("чат 33 снова", lambda c: be(c).open_chat(33), 2500),
        ("отправка и правка файлов", editors, 14000),
        clean_after("мессенджер: окна"),
        ("текст", send_text, 2200),
        ("текст: сервер", after_text, 600),
        ("текст: лента", after_text_dom, 100),
        ("реакция", lambda c: be(c).send_reaction(str(c.data["mid"]), "like"), 1800),
        ("реакция: сервер", after_react, 600),
        ("реакция: лента", lambda c: c.check("реакция: чип на сообщении", "👍" in (c.R.get("react") or ""), c.R.get("react")), 100),
        ("правка", lambda c: be(c).edit_message(str(c.data["mid"]), EDITED), 1800),
        ("правка: сервер", after_edit, 600),
        ("правка: лента", lambda c: c.check("правка: в ленте новый текст и «изменено»",
                                           EDITED in (c.R.get("edit") or "") and "изменено" in (c.R.get("edit") or ""), (c.R.get("edit") or "")[:140]), 100),
        ("удаление", lambda c: be(c).delete_message(str(c.data["mid"])), 1800),
        ("удаление: сервер", after_delete, 600),
        ("удаление: лента", lambda c: c.check("удаление: из ленты убрано", c.R.get("del") is False, c.R.get("del")), 100),
        ("файлы", send_files, 9000),
        ("файлы: ожидание", wait_files, 1500),
        ("файлы: сервер", after_files, 900),
        ("файлы: лента", lambda c: (c.check("файлы: в ленте подпись и смета", CAPTION in (c.R.get("files") or "")
                                            and "smeta.csv" in (c.R.get("files") or ""), (c.R.get("files") or "")[-200:]),
                                    c.shot("f_sent")), 300),
        ("пересылка", forward, 2600),
        ("пересылка: сервер", after_forward, 300),
        ("группа", new_group, 5500),
        ("группа: сервер", after_group, 300),
        ("переименовать", rename, 2500),
        ("переименовать: сервер", after_rename, 700),
        ("переименовать: заголовок", lambda c: c.check("переименование: заголовок чата обновился",
                                                       (c.R.get("title") or "").strip() == GROUP + " — 2", c.R.get("title")), 100),
        ("удалить группу", delete_group, 3000),
        ("удалить группу: сервер", after_delete_group, 300),
        ("группа 900 снова", lambda c: be(c).open_chat(900), 3000),
        ("входящее", incoming, 2000),
        ("входящее: карточка", after_incoming, 700),
        ("входящее: открыть", open_card, 3500),
        ("входящее: чат открыт", after_card, 700),
        ("входящее: заголовок", lambda c: (c.check("в заголовке — собеседник", "Кузнецова" in (c.R.get("title2") or ""), c.R.get("title2")),
                                           c.shot("f_opened")), 300),
        ("снова через меню", lambda c: c.shell.go("messenger"), 2500),
        ("снова через меню: проверка", lambda c: (c.check("мессенджер через меню из мессенджера: окно чата на месте",
                                                          c.shell.messenger_host().isVisible()), c.shot("f_again")), 300),
        clean_after("мессенджер: работа"),
    ]
    return s
