# -*- coding: utf-8 -*-
"""Окна мессенджера — как в js/views/messenger.js сайта: новый чат, новая
группа, карточки сотрудника и группы, настройки группы, переименование,
пересылка и вложения чата."""

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QToolButton,
                             QVBoxLayout, QWidget)

from general_functions import theme
from kov import icons, jobs
from kov.api import client, human_error
from kov.dialogs import Modal, confirm, field
from kov.messenger.util import role_label
from kov.people import PHOTOS, avatar, employee_picker, full_name
from kov.store import store
from kov.ui import (Button, Chip, InfoCard, InfoRow, SearchInput, border, clear_layout, fit_scroll, font, label,
                    line_edit, muted, rem, scroll_area, toast)

MEDIA_CATEGORIES = [("photo", "Фото", "image"), ("video", "Видео", "video"), ("file", "Файлы", "file")]


def _err(e):
    return human_error(e, str(e) or "ошибка")


def person_label(p):
    return full_name(p, True) or p.get("full_name") or p.get("name") or ("#%s" % p.get("id"))


# ── общие кусочки ────────────────────────────────────────────────────────────

class _Tile(QFrame):
    """Плитка выбора (.chat-tile): кружок со значком, заголовок и пояснение."""

    def __init__(self, glyph, title, sub, on_click):
        super().__init__()
        self.on_click = on_click
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("chatTile")
        self.setAttribute(Qt.WA_Hover, True)
        b = border()
        self.setStyleSheet(f"QFrame#chatTile {{ background: {theme.CARD_BG}; border: {b}px solid {theme.CARD_BORDER};"
                           f" border-radius: {rem(0.8)}px; }}"
                           f"QFrame#chatTile:hover {{ background: {theme.CHIP_BG}; border-color: {theme.ACCENT}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(rem(0.8), rem(1.1), rem(0.8), rem(1.1))
        lay.setSpacing(rem(0.35))
        circ = QLabel()
        d = rem(3)
        circ.setFixedSize(d, d)
        circ.setAlignment(Qt.AlignCenter)
        primary = theme.chat_vars().get("--col-primary", theme.ACCENT)
        circ.setPixmap(icons.pixmap(glyph, rem(1.5), primary))
        circ.setStyleSheet(f"background: {theme.INFO_SOFT}; border-radius: {d // 2}px; border: none;")
        lay.addWidget(circ, 0, Qt.AlignHCenter)
        lay.addWidget(label(title, 0.875, theme.TEXT_VALUE, bold=True, align=Qt.AlignHCenter))
        lay.addWidget(label(sub, 0.78, theme.TEXT_LABEL, align=Qt.AlignHCenter, wrap=True))
        for w in self.findChildren(QLabel):
            w.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.on_click()


class _RoundImage(QLabel):
    """Круглая картинка группы (.avatar.xl): фото или значок «люди» на цветном фоне."""

    def __init__(self, name, size_rem=8.0, glyph="users", on_open=None):
        super().__init__()
        self.d = rem(size_rem)
        self.setFixedSize(self.d + rem(0.8), self.d + rem(0.8))
        self.color = QColor(theme.avatar_color(name or "группа"))
        self.glyph = glyph
        self.img = None
        self.on_open = on_open

    def set_image(self, img):
        self.img = img
        if img is not None and self.on_open:
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip("Открыть фото")
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        off = rem(0.4)
        for k in range(6, 0, -1):         # тень 0 .25rem .7rem
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(38, 50, 56, 8))
            p.drawEllipse(QRectF(off - k * 0.5, off + rem(0.25) - k * 0.5, self.d + k, self.d + k))
        path = QPainterPath()
        path.addEllipse(off, off, self.d, self.d)
        if self.img is not None:
            p.setClipPath(path)
            pm = QPixmap.fromImage(self.img).scaled(self.d, self.d, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            p.drawPixmap(off + (self.d - pm.width()) // 2, off + (self.d - pm.height()) // 2, pm)
        else:
            p.fillPath(path, self.color)
            s = rem(2.6)
            p.drawPixmap(off + (self.d - s) // 2, off + (self.d - s) // 2, icons.pixmap(self.glyph, s, "#FFFFFF"))
        p.end()

    def mouseReleaseEvent(self, e):
        if self.img is not None and self.on_open and e.button() == Qt.LeftButton:
            self.on_open()


def _fetch_image(url):
    import requests
    from PySide6.QtGui import QImage
    res = requests.get(url, timeout=(10, 60), headers={"Authorization": "Bearer " + client.token} if client.token else {})
    res.raise_for_status()
    img = QImage()
    img.loadFromData(res.content)
    return img if not img.isNull() else None


def _open_image(owner, src, name):
    if not src:
        return
    from kov.viewer import MediaViewer
    MediaViewer.show_list(owner, [{"src": src, "filename": name or "photo.jpg"}], 0)


def _card_head(target, pic, name, sub):
    """Шапка карточки (.chat-card-head): фото по центру, имя и подпись.
    У картинки свой запас 0,4rem под тень — он входит в отступы шапки."""
    box = QWidget()
    box.setStyleSheet("background: transparent;")
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, rem(0.8))
    lay.setSpacing(0)
    lay.addWidget(pic, 0, Qt.AlignHCenter)
    lay.addWidget(label(name, 1.15, theme.TEXT_VALUE, bold=True, align=Qt.AlignHCenter, wrap=True))
    lay.addSpacing(rem(0.3))
    lay.addWidget(label(sub, 0.82, theme.TEXT_LABEL, align=Qt.AlignHCenter, wrap=True))
    target.addWidget(box)


class _PersonPic(QWidget):
    """Аватар сотрудника 8rem с тенью (как .avatar.xl в карточке)."""

    def __init__(self, person, on_open=None):
        super().__init__()
        self.person = person
        self.d = rem(8)
        self.setFixedSize(self.d + rem(0.8), self.d + rem(0.8))
        self.img = PHOTOS.get(person.get("id")) if isinstance(person.get("id"), int) else None
        self.on_open = on_open
        if self.img is None and isinstance(person.get("id"), int) and person.get("id") in store.photo_ids:
            PHOTOS.ready.connect(self._ready)
            PHOTOS.request(person["id"])
        self._cursor()

    def _cursor(self):
        if self.img is not None and self.on_open:
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip("Открыть фото")

    def _ready(self, emp_id, img):
        if emp_id == self.person.get("id"):
            try:
                self.img = img
                self._cursor()
                self.update()
            except RuntimeError:
                pass

    def paintEvent(self, _e):
        from kov.views.employees import paint_avatar
        from PySide6.QtCore import QRect
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        off = rem(0.4)
        for k in range(6, 0, -1):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(38, 50, 56, 8))
            p.drawEllipse(QRectF(off - k * 0.5, off + rem(0.25) - k * 0.5, self.d + k, self.d + k))
        paint_avatar(p, QRect(off, off, self.d, self.d), self.person, self.img)
        p.end()

    def mouseReleaseEvent(self, e):
        if self.img is not None and self.on_open and e.button() == Qt.LeftButton:
            self.on_open()


def _page(m):
    """Содержимое карточки — отдельной страницей: список вложений встаёт на её место."""
    page = QWidget()
    page.setStyleSheet("background: transparent;")
    lay = QVBoxLayout(page)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(rem(0.7))
    m.body.addWidget(page)
    return page, lay


def _refit(m):
    try:
        m._center()
    except RuntimeError:
        pass


# ── новый чат и новая группа ─────────────────────────────────────────────────

def private_partners(be):
    """С кем уже есть личный чат — по загруженному списку чатов."""
    out = []
    for c in be.chats or []:
        if not c.get("is_group") and c.get("other_user_id"):
            try:
                out.append({"id": int(c["other_user_id"]), "note": "чат уже есть"})
            except (TypeError, ValueError):
                pass
    return out


def open_private(be, user_id):
    """Личный чат с сотрудником: создаётся (или находится сервером) и открывается."""
    def done(chat):
        cid = (chat or {}).get("id") or ((chat or {}).get("chat") or {}).get("id")
        be.load_chats(lambda: be.open_by_id(cid))
    jobs.run(client.post, "chat/private", json_body={"target_user_id": int(user_id)}, done=done,
             failed=lambda e: toast(be.host, "Не удалось создать чат: " + _err(e), "error"), owner=be)


def add_menu(be):
    m = Modal(be.host, "Новый чат", "chat", width_rem=36)
    grid = QGridLayout()
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setSpacing(rem(0.7))

    def private():
        m.accept()
        employee_picker(be.host, "Выберите сотрудника", lambda ids: open_private(be, ids[0]), multi=False,
                        taken=private_partners(be))

    def group():
        m.accept()
        new_group_dialog(be)
    grid.addWidget(_Tile("user", "Личный чат", "Переписка с одним сотрудником", private), 0, 0)
    grid.addWidget(_Tile("users", "Новая группа", "Общий чат для нескольких сотрудников", group), 0, 1)
    m.body.addLayout(grid)
    m.hide_foot()
    m.exec()


def new_group_dialog(be):
    m = Modal(be.host, "Новая группа", "users", width_rem=36)
    name = line_edit("Название группы")
    m.body.addWidget(field("Название", name, True))
    m.body.addWidget(label("Участников можно будет добавить сразу после создания.", 0.8, theme.TEXT_MUTED, wrap=True))

    def create(_m):
        title = name.text().strip()
        if not title:
            toast(m, "Введите название", "warning")
            return False

        def done(chat):
            m.accept()
            cid = chat.get("id")
            be.load_chats(lambda: be.open_by_id(cid))

            def add(ids):
                def ok(_r):
                    toast(be.host, "Участники добавлены", "success")
                    be.get_group_info(str(cid))
                jobs.run(client.post, "chat/group/%s/participants" % cid, json_body={"user_ids": ids}, done=ok,
                         failed=lambda e: toast(be.host, "Не удалось добавить: " + _err(e), "error"), owner=be)
            # создатель уже в группе
            QTimer.singleShot(0, lambda: employee_picker(be.host, "Участники группы «%s»" % title, add, multi=True))
        jobs.run(client.post, "chat/group", json_body={"name": title}, done=done,
                 failed=lambda e: toast(m, "Не удалось создать группу: " + _err(e), "error"), owner=m)
        return False
    m.add_button("Отмена", "ghost")
    m.add_button("Создать", "primary", "check", create)
    name.setFocus()
    m.exec()


# ── вложения чата ────────────────────────────────────────────────────────────

class _MediaCat(QPushButton):
    """Кнопка категории вложений со счётчиком (.media-cat)."""

    def __init__(self, title, glyph):
        super().__init__("0")
        self.glyph = glyph
        self.setToolTip(title)
        self.setCursor(Qt.PointingHandCursor)
        self.setFont(font(0.85, bold=True))
        self.setEnabled(False)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(rem(2.5))
        self._paint()

    def _paint(self):
        b = border()
        col = theme.TEXT_LABEL
        self.setIcon(icons.icon(self.glyph, rem(1.15), col))
        self.setIconSize(QSize(rem(1.15), rem(1.15)))
        self.setStyleSheet(f"QPushButton {{ background: {theme.CARD_BG}; color: {col}; border: {b}px solid {theme.CARD_BORDER};"
                           f" border-radius: {rem(0.5)}px; padding: {rem(0.6)}px {rem(0.5)}px; }}"
                           f"QPushButton:hover:enabled {{ background: {theme.CHIP_BG}; border-color: {theme.ACCENT_SOFT};"
                           f" color: {theme.ACCENT}; }}"
                           f"QPushButton:disabled {{ color: rgba({_rgb(col)},128); border-color: rgba({_rgb(theme.CARD_BORDER)},128); }}")


def _rgb(hex_color):
    c = QColor(hex_color)
    return "%d,%d,%d" % (c.red(), c.green(), c.blue())


def media_section(be, m, chat_id, card_page):
    """Блок вложений: категории со счётчиками; нажатие — список вместо содержимого карточки."""
    box = QWidget()
    box.setStyleSheet("background: transparent;")
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, rem(0.2), 0, rem(0.2))
    lay.setSpacing(rem(0.5))
    box.hide()
    if not chat_id:
        return box
    buttons = {}
    for key, title, glyph in MEDIA_CATEGORIES:
        btn = _MediaCat(title, glyph)
        btn.clicked.connect(lambda _=False, k=key, t=title: open_media_list(be, m, chat_id, k, t, card_page))
        buttons[key] = btn
        lay.addWidget(btn)

    def done(raw):
        summary = (raw or {}).get("counts") if isinstance(raw, dict) and "counts" in raw else raw
        anything = False
        for key, _t, _g in MEDIA_CATEGORIES:
            try:
                n = int((summary or {}).get(key) or 0)
            except (TypeError, ValueError):
                n = 0
            try:
                buttons[key].setText(str(n))
                buttons[key].setEnabled(n > 0)
                buttons[key]._paint()
            except RuntimeError:
                return
            anything = anything or n > 0
        try:
            box.setVisible(anything)
            QTimer.singleShot(0, lambda: _refit(m))
        except RuntimeError:
            pass
    jobs.run(client.get, "chat/%d/media/summary" % chat_id, done=done, failed=lambda e: None, owner=box)
    return box


class _MediaTile(QFrame):
    """Строка вложения: миниатюра, имя, дата и размер (.media-tile)."""

    def __init__(self, be, item, category, on_open, on_goto):
        super().__init__()
        from kov.files import fmt_size
        from kov.fmt import fmt_datetime
        self.setObjectName("mediaTile")
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(f"QFrame#mediaTile {{ border-radius: {rem(0.5)}px; background: transparent; }}"
                           f"QFrame#mediaTile:hover {{ background: {theme.CHIP_BG}; }}")
        name = item.get("filename") or item.get("file_name") or "Файл"
        self.setToolTip(name + "\nПравая кнопка — перейти к сообщению")
        self.on_open, self.on_goto = on_open, on_goto
        lay = QHBoxLayout(self)
        lay.setContentsMargins(rem(0.5), rem(0.45), rem(0.5), rem(0.45))
        lay.setSpacing(rem(0.7))
        side = rem(3.2)
        thumb = QLabel()
        thumb.setFixedSize(side, side)
        thumb.setAlignment(Qt.AlignCenter)
        thumb.setStyleSheet(f"background: {theme.CHIP_BG}; border-radius: {rem(0.55)}px;")
        glyph = "image" if category == "photo" else "video" if category == "video" else "file"
        thumb.setPixmap(icons.pixmap(glyph, rem(1.3), theme.ACCENT))
        lay.addWidget(thumb)
        src = None
        if item.get("url"):
            if category == "photo":
                src = be.urls.with_token(be.urls.storage_path(item["url"]))
            elif category == "video":
                import re
                u = re.sub(r"\.gz$", "", be.urls.storage_path(item["url"]))
                src = be.urls.with_token(u + ".thumb.jpg")
        if src:
            def put(img, t=thumb, video=category == "video"):
                if img is None:
                    return
                from kov.files import _rounded
                pm = _rounded(QPixmap.fromImage(img), side, side, rem(0.55))
                if video:
                    q = QPainter(pm)
                    q.fillRect(pm.rect(), QColor(19, 42, 50, 82))
                    s = rem(1.2)
                    q.drawPixmap((side - s) // 2, (side - s) // 2, icons.pixmap("video", s, "#FFFFFF"))
                    q.end()
                try:
                    t.setPixmap(pm)
                except RuntimeError:
                    pass
            jobs.run(_fetch_image, src, done=put, failed=lambda e: None, owner=thumb)
        who = QVBoxLayout()
        who.setSpacing(0)
        nm = label(name, 0.86, theme.TEXT_VALUE, bold=True)
        nm.setMinimumWidth(rem(4))
        who.addWidget(nm)
        size = item.get("size") or item.get("file_size")
        when = fmt_datetime(item.get("timestamp"), "") if item.get("timestamp") else ""
        who.addWidget(label(" · ".join(x for x in (when, fmt_size(size) if size else "") if x), 0.74, theme.TEXT_LABEL))
        lay.addLayout(who, 1)
        for w in self.findChildren(QLabel):
            w.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.on_open()
        elif e.button() == Qt.RightButton:
            self.on_goto()


def open_media_list(be, m, chat_id, category, title, card_page):
    """Список вложений одной категории вместо содержимого карточки; стрелка — назад."""
    card_page.hide()
    page = QWidget()
    page.setStyleSheet("background: transparent;")
    pl = QVBoxLayout(page)
    pl.setContentsMargins(0, 0, 0, 0)
    pl.setSpacing(rem(0.7))
    head = QHBoxLayout()
    head.setSpacing(rem(0.65))
    back = QToolButton()
    back.setCursor(Qt.PointingHandCursor)
    back.setToolTip("Вернуться к карточке")
    back.setIcon(icons.icon("arrow_back", rem(1.2), theme.ACCENT))
    back.setIconSize(QSize(rem(1.2), rem(1.2)))
    back.setFixedSize(rem(2), rem(2))
    back.setStyleSheet(f"QToolButton {{ border: none; border-radius: {rem(1)}px; background: transparent; }}"
                       f"QToolButton:hover {{ background: {theme.CHIP_BG}; }}")
    head.addWidget(back)
    from kov.ui import IconBadge
    head.addWidget(IconBadge("folder", 1.7, 0.95))
    head.addWidget(label(title, 0.875, theme.TEXT_VALUE, bold=True))
    head.addStretch(1)
    pl.addLayout(head)
    holder = QWidget()
    holder.setStyleSheet("background: transparent;")
    hl = QVBoxLayout(holder)
    hl.setContentsMargins(0, 0, 0, 0)
    hl.setSpacing(rem(0.15))
    hl.addWidget(muted("Загружаем вложения…", 0.85))
    area = scroll_area(holder)
    area.setMaximumHeight(rem(24))
    pl.addWidget(area)
    m.body.addWidget(page)

    def fit_area():
        fit_scroll(area, 24, lambda: _refit(m))

    def restore():
        page.hide()
        m.body.removeWidget(page)
        page.deleteLater()
        card_page.show()
        QTimer.singleShot(0, lambda: _refit(m))
    back.clicked.connect(restore)

    def done(data):
        items = data if isinstance(data, list) else ((data or {}).get("items") or [])
        try:
            clear_layout(hl)
        except RuntimeError:
            return
        if not items:
            hl.addWidget(muted("Вложений нет", 0.85))
            fit_area()
            return
        for it in items:
            def open_it(it=it):
                name = it.get("filename") or it.get("file_name") or "Файл"
                spec = dict(it, filename=name)
                if category in ("photo", "video"):
                    _open_image(be.host, be.urls.media_src(spec), name)
                else:
                    from kov import files as kfiles
                    kfiles.open_external(be.host, {"src": be.urls.file_href(spec), "filename": name,
                                                   "id": it.get("message_id") or it.get("id")})

            def goto(it=it):
                m.accept()
                if it.get("message_id"):
                    be.go_to_message(str(it["message_id"]), str(it.get("group_id") or ""), str(chat_id))
            hl.addWidget(_MediaTile(be, it, category, open_it, goto))
        hl.addStretch(1)
        fit_area()

    def failed(_e):
        try:
            clear_layout(hl)
            hl.addWidget(muted("Не удалось загрузить вложения", 0.85))
            fit_area()
        except RuntimeError:
            pass
    jobs.run(client.get, "chat/%d/media" % chat_id, query={"category": category, "limit": 200}, done=done,
             failed=failed, owner=page)
    QTimer.singleShot(0, lambda: _refit(m))


# ── карточка сотрудника ──────────────────────────────────────────────────────

def user_card(be, user_id, chat_id=None, person=None):
    """Карточка сотрудника: фото, контакты, «Написать», вложения переписки с ним."""
    def work():
        try:
            store.load_photo_ids()
        except Exception:
            pass
        try:
            return store.employee(int(user_id))
        except Exception:
            return None

    def show(found):
        p = found or (dict(person, id=user_id) if person else {"id": user_id})
        existing = next((c for c in be.chats or [] if not c.get("is_group")
                         and str(c.get("other_user_id")) == str(user_id)), None)
        cid = chat_id or (existing or {}).get("id")
        own = int(user_id) == int(client.user_id or 0)
        m = Modal(be.host, "Карточка сотрудника", "user", width_rem=52)
        if not own:
            def write():
                m.accept()
                if cid and be.open_by_id(cid):
                    return
                open_private(be, user_id)
            m.add_head_button("chat", "Написать сотруднику", write)
        src = client.file_url("employees/%d/photo" % int(user_id))
        pic = _PersonPic(p, on_open=lambda: _open_image(be.host, src, "Фото сотрудника"))
        page, body = _page(m)
        _card_head(body, pic, full_name(p, True) or "Сотрудник",
                   " · ".join(x for x in (p.get("position"), p.get("department")) if x) or "Сотрудник")
        rows = [("Телефон", p.get("phone_number")), ("Почта", p.get("email")), ("Должность", p.get("position")),
                ("Отдел", p.get("department"))]
        rows = [(k, v) for k, v in rows if v]
        if rows:
            card = InfoCard("Контакты", "id_card")
            for k, v in rows:
                card.body.addWidget(InfoRow(k, v))
            body.addWidget(card)
        body.addWidget(media_section(be, m, int(cid) if cid else None, page))
        row = QHBoxLayout()
        row.addStretch(1)
        prof = Button("Профиль сотрудника", "ghost", "id_card")

        def profile():
            m.accept()
            shell = be.shell
            if shell is not None:
                shell.open_profile(int(user_id))
        prof.clicked.connect(profile)
        row.addWidget(prof)
        body.addLayout(row)
        m.hide_foot()
        m.exec()
    jobs.run(work, done=show, failed=lambda e: show(None), owner=be)


# ── карточка группы ──────────────────────────────────────────────────────────

class _PersonRow(QFrame):
    """Участник группы (.chat-person): аватар, имя и роль, кнопки в своей колонке."""

    def __init__(self, p, on_avatar):
        super().__init__()
        self.setObjectName("chatPerson")
        self.setStyleSheet(f"QFrame#chatPerson {{ border-radius: {rem(0.45)}px; background: transparent; }}"
                           f"QFrame#chatPerson:hover {{ background: {theme.CHIP_BG}; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(rem(0.45), rem(0.35), rem(0.45), rem(0.35))
        lay.setSpacing(rem(0.55))
        av = avatar(p, 2.4)
        av.setCursor(Qt.PointingHandCursor)
        av.setToolTip("Карточка сотрудника")
        av.mouseReleaseEvent = lambda e: on_avatar() if e.button() == Qt.LeftButton else None
        lay.addWidget(av)
        main = QHBoxLayout()
        main.setSpacing(rem(0.4))
        nm = label(person_label(p), 0.875, theme.TEXT_VALUE, bold=True)
        main.addWidget(nm)
        main.addWidget(Chip(role_label(p.get("role")), "info" if p.get("role") == "owner" else "default"))
        main.addStretch(1)
        lay.addLayout(main, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(rem(0.3))
        self.actions.addStretch(1)
        box = QWidget()
        box.setStyleSheet("background: transparent;")
        box.setLayout(self.actions)
        box.setFixedWidth(rem(5.4))
        self.actions.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(box)

    def add_action(self, glyph, tip, kind, handler):
        b = Button("", kind, glyph, tooltip=tip)
        b.setFixedSize(rem(2.4), rem(2.2))
        b.clicked.connect(lambda _=False: handler())
        self.actions.addWidget(b)
        return b


def refresh_card(be, chat_id, deleted=False):
    """Права или состав группы изменились — открытая карточка перестраивается."""
    if be.card is None or chat_id is None:
        return
    cid, dlg = be.card
    if str(cid) != str(chat_id):
        return
    be.card = None
    try:
        dlg.reject()
    except RuntimeError:
        return
    if not deleted:
        QTimer.singleShot(0, lambda: info_card(be, int(chat_id)))


def info_card(be, chat_id):
    def work():
        info = client.get("chat/info/%d" % chat_id)
        try:
            store.load_photo_ids()
        except Exception:
            pass
        return info
    jobs.run(work, done=lambda info: _info_card(be, chat_id, info),
             failed=lambda e: toast(be.host, "Карточка чата недоступна", "warning"), owner=be)


def _info_card(be, chat_id, info):
    info = info or {}
    if not info.get("is_group"):
        # личный чат — карточка сотрудника; номер собеседника — строго из ответа
        other = info.get("other_user_id") or info.get("user_id") or (info.get("user") or {}).get("id")
        if not other:
            toast(be.host, "Не удалось определить собеседника", "warning")
            return
        user_card(be, int(other), chat_id=info.get("chat_id"), person=info)
        return
    my_role = str(info.get("my_role") or "").lower()
    can_manage = my_role in ("owner", "admin")
    is_owner = my_role == "owner"
    participants = info.get("participants") or []
    m = Modal(be.host, "Группа: " + (info.get("name") or ""), "users", width_rem=52, scroll=True)
    be.card = (chat_id, m)
    m.finished.connect(lambda _r: setattr(be, "card", None) if be.card and be.card[1] is m else None)
    if can_manage:
        m.add_head_button("settings", "Настройки группы", lambda: (m.accept(), group_settings(be, chat_id)))
    m.add_head_button("logout", "Покинуть группу", lambda: leave_group(be, chat_id, info.get("name"), m),
                      color=theme.DANGER)
    photo = be.urls.avatar_src(info.get("image_path")) if info.get("image_path") else None
    pic = _RoundImage(info.get("name") or "группа", on_open=lambda: _open_image(be.host, photo, "Фото группы"))
    if photo:
        jobs.run(_fetch_image, photo, done=lambda img: pic.set_image(img), failed=lambda e: None, owner=pic)
    page, body = _page(m)
    _card_head(body, pic, info.get("name") or "Группа", "Участников: %d" % len(participants))
    body.addWidget(media_section(be, m, chat_id, page))
    row = QHBoxLayout()
    row.addWidget(label("Участники", 0.75, theme.TEXT_LABEL), 1)
    if can_manage:
        add = Button("Добавить участников", "ghost", "plus")

        def add_people():
            taken = [{"id": int(p["id"]), "note": "уже в группе"} for p in participants if p.get("id") is not None]

            def picked(ids):
                def ok(_r):
                    refresh_card(be, chat_id)
                jobs.run(client.post, "chat/group/%d/participants" % chat_id, json_body={"user_ids": ids}, done=ok,
                         failed=lambda e: toast(be.host, "Не удалось добавить: " + _err(e), "error"), owner=be)
            employee_picker(m, "Добавить в группу", picked, multi=True, taken=taken)
        add.clicked.connect(add_people)
        row.addWidget(add)
    body.addLayout(row)
    search = SearchInput("Поиск участников…")
    body.addWidget(search)
    holder = QFrame()
    holder.setObjectName("checklist")
    holder.setStyleSheet(f"QFrame#checklist {{ border: {border()}px solid {theme.CARD_BORDER}; border-radius: {rem(0.5)}px;"
                         f" background: {theme.CARD_BG}; }}")
    lst = QVBoxLayout(holder)
    lst.setContentsMargins(rem(0.4), rem(0.4), rem(0.4), rem(0.4))
    lst.setSpacing(rem(0.25))
    area = scroll_area(holder)
    area.setMaximumHeight(rem(18))
    body.addWidget(area)

    def draw():
        clear_layout(lst)
        needle = search.text().strip().lower()
        shown = 0
        for p in participants:
            if needle and needle not in person_label(p).lower():
                continue
            r = _PersonRow(p, lambda p=p: (m.accept(), user_card(be, int(p["id"]))))
            other = str(p.get("id")) != str(client.user_id) and p.get("role") != "owner"
            if is_owner and other:
                admin = p.get("role") == "admin"

                def toggle(p=p, admin=admin):
                    body = {"user_id": int(p["id"]), "role": "participant" if admin else "admin"}

                    def ok(_r):
                        toast(be.host, "Права администратора сняты" if admin else "Назначен администратором", "success")
                        refresh_card(be, chat_id)
                    jobs.run(client.post, "chat/group/%d/%s" % (chat_id, "demote" if admin else "promote"),
                             json_body=body, done=ok,
                             failed=lambda e: toast(be.host, "Не удалось изменить роль: " + _err(e), "error"), owner=be)
                r.add_action("user" if admin else "star", "Снять права администратора" if admin else "Сделать администратором",
                             "ghost", toggle)
            if can_manage and other:
                def remove(p=p):
                    if not confirm(m, "Удалить участника", "Удалить «%s» из группы?" % person_label(p), "Удалить", danger=True):
                        return
                    jobs.run(client.delete, "chat/group/%d/participants/%s" % (chat_id, p["id"]),
                             done=lambda _r: refresh_card(be, chat_id),
                             failed=lambda e: toast(be.host, "Не удалось: " + _err(e), "error"), owner=be)
                r.add_action("trash", "Удалить из группы", "danger", remove)
            lst.addWidget(r)
            shown += 1
        if not shown:
            lst.addWidget(muted("Никого не найдено", 0.85))
        lst.addStretch(1)
        fit_scroll(area, 18, lambda: _refit(m))
    search.textChanged.connect(lambda _t: draw())
    draw()
    m.hide_foot()
    m.exec()


def leave_group(be, chat_id, name, dlg=None):
    """Выход из группы: сервер сам удалит её, если никого не останется."""
    if not confirm(dlg or be.host, "Выход из группы", "Вы хотите выйти из группы «%s»?" % (name or ""), "Покинуть",
                   danger=True):
        return

    def ok(_r):
        if dlg is not None:
            try:
                dlg.accept()
            except RuntimeError:
                pass
        if be._here(chat_id):
            be.run_js("if (typeof clearChat === 'function') clearChat();"
                      " var k = document.getElementById('kovChat'); if (k) k.classList.remove('chat-open');")
            be.chat_closed()
        be.load_chats()
        toast(be.host, "Вы покинули группу", "success")
    jobs.run(client.delete, "chat/group/%d/leave" % chat_id, done=ok,
             failed=lambda e: toast(be.host, "Не удалось выйти: " + _err(e), "error"), owner=be)


# ── настройки группы ─────────────────────────────────────────────────────────

def group_settings(be, chat_id):
    """Настройки — отдельное окно за шестерёнкой; роль перечитывается при каждом открытии."""
    jobs.run(client.get, "chat/info/%d" % chat_id, done=lambda info: _group_settings(be, chat_id, info or {}),
             failed=lambda e: toast(be.host, "Настройки недоступны: " + _err(e), "error"), owner=be)


def _group_settings(be, chat_id, info):
    my_role = str(info.get("my_role") or "").lower()
    if my_role not in ("owner", "admin"):
        toast(be.host, "Права администратора сняты — настройки недоступны", "warning")
        return
    m = Modal(be.host, "Настройки группы: " + (info.get("name") or ""), "settings", width_rem=36)
    m.body.addWidget(label("Участники добавляются в карточке группы", 0.75, theme.TEXT_LABEL, wrap=True))
    rows = QVBoxLayout()
    rows.setSpacing(rem(0.5))
    rename = Button("Переименовать группу", "ghost", "edit")
    rename.clicked.connect(lambda: rename_dialog(be, chat_id, info.get("name"),
                                                 lambda: (m.accept(), info_card(be, chat_id))))
    rows.addWidget(rename)
    picture = Button("Сменить картинку", "ghost", "image")

    def change_picture():
        from kov import photo_crop
        picked = photo_crop.pick(m, "Картинка группы", True)
        if not picked:
            return
        name, data = picked

        def work():
            r = client.post("chat/upload", files={"file": (name, data, "image/jpeg")},
                            data={"chat_id": str(chat_id), "is_avatar": "true"}, timeout=(10, 120))
            client.put("chat/group/%d" % chat_id, json_body={"image_path": (r or {}).get("url") or (r or {}).get("file_path")})
            return r

        def ok(_r):
            toast(be.host, "Картинка обновлена", "success")
            m.accept()
            be.load_chats()
            be.get_group_info(str(chat_id))
            info_card(be, chat_id)          # карточка — сразу с новой картинкой
        jobs.run(work, done=ok, failed=lambda e: toast(be.host, "Не удалось: " + _err(e), "error"), owner=be)
    picture.clicked.connect(change_picture)
    rows.addWidget(picture)
    m.body.addLayout(rows)
    danger = QVBoxLayout()
    danger.setSpacing(rem(0.5))
    danger.setContentsMargins(0, rem(1.4), 0, 0)      # .danger-zone: margin-top 1.4rem сверх gap тела
    if my_role == "owner":
        delete = Button("Удалить группу", "danger", "trash")

        def delete_group():
            if not confirm(m, "Удаление группы", "Удалить группу со всей перепиской?", "Удалить", danger=True):
                return

            def ok(_r):
                m.accept()
                if be._here(chat_id):
                    be.run_js("if (typeof clearChat === 'function') clearChat();"
                              " var k = document.getElementById('kovChat'); if (k) k.classList.remove('chat-open');")
                    be.chat_closed()
                be.load_chats()
            jobs.run(client.delete, "chat/group/%d" % chat_id, done=ok,
                     failed=lambda e: toast(be.host, "Не удалось: " + _err(e), "error"), owner=be)
        delete.clicked.connect(delete_group)
        danger.addWidget(delete)
    else:
        leave = Button("Покинуть группу", "danger", "logout")
        leave.clicked.connect(lambda: leave_group(be, chat_id, info.get("name"), m))
        danger.addWidget(leave)
    m.body.addLayout(danger)
    m.add_button("Назад к карточке", "ghost", "arrow_back", lambda _m: (QTimer.singleShot(0, lambda: info_card(be, chat_id)), True)[1])
    m.exec()


def rename_dialog(be, chat_id, current, done=None):
    m = Modal(be.host, "Название группы", "edit", width_rem=36)
    name = line_edit("", current or "")
    m.body.addWidget(field("Название", name, True))

    def save(_m):
        v = name.text().strip()
        if not v:
            toast(m, "Введите название", "warning")
            return False

        def ok(_r):
            m.accept()
            be.get_group_info(str(chat_id))
            be.load_chats()
            if done:
                done()
        jobs.run(client.put, "chat/group/%d" % chat_id, json_body={"name": v}, done=ok,
                 failed=lambda e: toast(m, "Не удалось: " + _err(e), "error"), owner=m)
        return False
    m.add_button("Отмена", "ghost")
    m.add_button("Сохранить", "primary", "save", save)
    name.setFocus()
    m.exec()


# ── пересылка ────────────────────────────────────────────────────────────────

class _ForwardRow(QFrame):
    """Строка чата в окне пересылки (.forward-row): фото, название, вид; отмеченный — подсвечен."""

    def __init__(self, be, chat, on_toggle):
        super().__init__()
        self.setObjectName("forwardRow")
        self.setCursor(Qt.PointingHandCursor)
        self.on_toggle = on_toggle
        lay = QHBoxLayout(self)
        lay.setContentsMargins(rem(0.55), rem(0.45), rem(0.55), rem(0.45))
        lay.setSpacing(rem(0.6))
        if chat.get("is_group"):
            pic = _GroupDot(chat.get("name"), 2.4)
            if chat.get("image_path"):
                src = be.urls.with_token(be.urls.storage_path(chat["image_path"]))
                jobs.run(_fetch_image, src, done=lambda img: pic.set_image(img), failed=lambda e: None, owner=pic)
            lay.addWidget(pic)
        else:
            lay.addWidget(avatar({"id": _int(chat.get("other_user_id")), "last_name": chat.get("last_name"),
                                  "first_name": chat.get("first_name"), "name": chat.get("name")}, 2.4))
        who = QVBoxLayout()
        who.setSpacing(0)
        who.addWidget(label(chat.get("name") or "Чат", 0.88, theme.TEXT_VALUE, bold=True))
        who.addWidget(label("Группа" if chat.get("is_group") else "Личный чат", 0.74, theme.TEXT_LABEL))
        lay.addLayout(who, 1)
        self.mark = QLabel()
        self.mark.setFixedSize(rem(1.2), rem(1.2))
        lay.addWidget(self.mark)
        for w in self.findChildren(QLabel):
            w.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.set_on(False)

    def set_on(self, on):
        b = border()
        self.mark.setPixmap(icons.pixmap("check", rem(1.1), theme.INFO) if on else QPixmap())
        self.setStyleSheet(f"QFrame#forwardRow {{ border: {b}px solid {theme.INFO if on else 'transparent'};"
                           f" border-radius: {rem(0.5)}px; background: {theme.INFO_SOFT if on else 'transparent'}; }}"
                           + ("" if on else f"QFrame#forwardRow:hover {{ background: {theme.CHIP_BG}; }}"))

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.on_toggle()


class _GroupDot(_RoundImage):
    """Картинка группы в строке: цвет по названию, значок «люди»."""

    def __init__(self, name, size_rem):
        super().__init__(name, size_rem)
        self.setFixedSize(self.d, self.d)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        path = QPainterPath()
        path.addEllipse(0, 0, self.d, self.d)
        if self.img is not None:
            p.setClipPath(path)
            pm = QPixmap.fromImage(self.img).scaled(self.d, self.d, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            p.drawPixmap((self.d - pm.width()) // 2, (self.d - pm.height()) // 2, pm)
        else:
            p.fillPath(path, self.color)
            s = int(self.d * 0.92)
            p.drawPixmap((self.d - s) // 2, (self.d - s) // 2, icons.pixmap("users", s, "#FFFFFF"))
        p.end()


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def forward_dialog(be, ops, preview=None):
    """Пересылка: чаты с фотографиями, поиск и выбор нескольких."""
    selected = []
    m = Modal(be.host, "Переслать сообщение", "arrow_right", width_rem=52)
    if preview:
        pv = label(str(preview)[:200], 0.82, theme.TEXT_LABEL, wrap=True)
        pv.setStyleSheet(f"color: {theme.TEXT_LABEL}; background: {theme.CHIP_BG}; border-radius: {rem(0.5)}px;"
                         f" padding: {rem(0.5)}px {rem(0.7)}px;")
        pv.setMaximumHeight(rem(6))
        m.body.addWidget(pv)
    search = SearchInput("Поиск по чатам и сотрудникам…")
    m.body.addWidget(search)
    counter = label("", 0.75, theme.TEXT_LABEL)
    m.body.addWidget(counter)
    holder = QWidget()
    holder.setStyleSheet("background: transparent;")
    lst = QVBoxLayout(holder)
    lst.setContentsMargins(0, 0, 0, 0)
    lst.setSpacing(rem(0.2))
    area = scroll_area(holder)
    area.setMaximumHeight(rem(22))
    m.body.addWidget(area)

    def draw():
        clear_layout(lst)
        needle = search.text().strip().lower()
        rows = [c for c in be.chats or [] if not needle or needle in str(c.get("name") or "").lower()]
        for c in rows:
            row = _ForwardRow(be, c, lambda c=c: toggle(c))
            row.set_on(c.get("id") in selected)
            lst.addWidget(row)
        if not rows:
            lst.addWidget(muted("Чат не найден", 0.85))
        lst.addStretch(1)
        counter.setText(("Выбрано чатов: %d" % len(selected)) if selected else "Можно выбрать несколько")
        fit_scroll(area, 22, m.refit)

    def toggle(c):
        cid = c.get("id")
        if cid in selected:
            selected.remove(cid)
        else:
            selected.append(cid)
        draw()
    search.textChanged.connect(lambda _t: draw())
    draw()

    def send(_m):
        if not selected:
            toast(m, "Выберите, куда переслать", "warning")
            return False
        chats = list(selected)

        def work():
            for it in ops:     # целиком — номер; часть группы — номер и выбранные файлы
                mid = it.get("id") if isinstance(it, dict) else it
                files = it.get("files") if isinstance(it, dict) else None
                body = {"chat_ids": chats, "file_ids": files} if files else {"chat_ids": chats}
                client.post("chat/message/%s/forward" % mid, json_body=body)
            return True

        def ok(_r):
            text = ("Переслано сообщений: %d" % len(ops)) if len(ops) > 1 else \
                ("Переслано в %d чата(ов)" % len(chats)) if len(chats) > 1 else "Сообщение переслано"
            toast(be.host, text, "success")
            m.accept()
        jobs.run(work, done=ok, failed=lambda e: toast(m, "Не удалось переслать: " + _err(e), "error"), owner=m)
        return False
    m.add_button("Отмена", "ghost")
    m.add_button("Переслать", "primary", "check", send)
    m.exec()
