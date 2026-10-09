# -*- coding: utf-8 -*-
"""Страница мессенджера: файлы сайта (web_chat/) + тема, масштаб и мост.

Страница собирается в памяти и открывается в QWebEngineView через setHtml
с базовым адресом папки web_chat — скрипты, стикеры и эмодзи берутся из неё.
Стили проходят через compat.transform: в старом движке (Chromium 83)
недостающие возможности заменяются (см. compat.py).
"""

import json
import os
import re

from kov.messenger import compat

_CACHE = {}


def assets_dir():
    """Папка web_chat: рядом с программой (сборка PyInstaller — во временной папке запуска)."""
    try:
        from general_functions.Trifle import resource_path
        p = resource_path("web_chat")
        if os.path.isdir(p):
            return p
    except Exception:
        pass
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(here, "web_chat")


def _read(rel):
    with open(os.path.join(assets_dir(), rel), encoding="utf-8") as f:
        return f.read()


def _qwebchannel_js():
    """qwebchannel.js из ресурсов Qt — встраивается в страницу целиком."""
    try:
        from PySide6 import QtWebChannel  # noqa: F401 — регистрирует ресурсы :/qtwebchannel
        from PySide6.QtCore import QFile, QIODevice
        f = QFile(":/qtwebchannel/qwebchannel.js")
        if f.open(QIODevice.ReadOnly):
            data = f.readAll().data().decode("utf-8")
            f.close()
            return data
    except Exception:
        pass
    return None


def _markup(major, force):
    """Разметка чата; flex-gap во встроенных стилях — тоже под старый движок."""
    text = _read("chat/chat-markup.html")
    extra = []
    if force or major < 84:
        def fix(m):
            tag = m.group(0)
            sm = re.search(r'style="([^"]*)"', tag)
            im = re.search(r'id="([^"]+)"', tag)
            if not sm or not im or "gap" not in sm.group(1):
                return tag
            style = sm.group(1)
            if not re.search(r"display\s*:\s*(inline-)?flex", style):
                return tag
            gm = re.search(r"(?:^|;)\s*gap\s*:\s*([^;]+)", style)
            if not gm:
                return tag
            column = re.search(r"flex-direction\s*:\s*column", style) is not None
            extra.append("#%s > * + * { margin-%s: %s; }" % (im.group(1), "top" if column else "left", gm.group(1).strip()))
            style = re.sub(r"(?:^|;)\s*gap\s*:\s*[^;]+;?", ";", style).strip("; ")
            return tag.replace(sm.group(0), 'style="%s"' % style)
        text = re.sub(r"<[a-zA-Z][^>]*>", fix, text)
    return text, "\n".join(extra)


def styles(major, force=False):
    """(css темы, css чата, сведения для скрипта) — с кэшем на время работы."""
    key = (major, bool(force))
    if key not in _CACHE:
        # как на сайте: цвета, общие стили страницы (шрифт, полосы прокрутки), стили чата и раздела
        theme_css, _ = compat.transform(_read("css/theme.css"), major, force)
        common_css, _ = compat.transform(_read("css/common.css"), major, force)
        chat_css, info = compat.transform(_read("chat/chat.css") + "\n" + _read("css/sections/messenger.css"), major, force)
        markup, markup_css = _markup(major, force)
        _CACHE[key] = (theme_css + "\n" + common_css, chat_css + "\n" + markup_css, info, markup)
    return _CACHE[key]


def build(theme_mode, user_scale, rem_px, user_id, host, token, major=None, force_compat=False, max_files=100):
    """HTML страницы. rem_px — 1rem страницы в её пикселях (до увеличения окна просмотра)."""
    if major is None:
        major = compat.chromium_major()
    theme_css, chat_css, info, markup = styles(major, force_compat)
    channel = _qwebchannel_js()
    boot = {"user": str(user_id or ""), "host": host or "", "token": token or "", "maxFiles": int(max_files)}
    head = [
        '<!DOCTYPE html><html lang="ru" data-theme="%s" style="font-size: %.3fpx; --user-scale: %.2f">'
        % ("dark" if theme_mode == "dark" else "light", rem_px, user_scale),
        '<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Мессенджер</title>",
        "<style>%s</style>" % theme_css,
        "<style>%s</style>" % chat_css,
        "<style>%s</style>" % _read("pc/page.css"),
        "<script>window.__kovBoot = %s; %s</script>" % (json.dumps(boot, ensure_ascii=False), compat.page_config(info)),
        "<script>%s</script>" % compat.POLYFILL_JS,
        ("<script>%s</script>" % channel) if channel else '<script src="qrc:///qtwebchannel/qwebchannel.js"></script>',
        '<script src="pc/bridge.js"></script>',
        '<script src="js/core/icons.js"></script>',
        '<script src="chat/emoji-index.js"></script>',
        '<script src="chat/media-panel.js"></script>',
        "</head>",
    ]
    body = '<body><div id="kovChat">%s</div><script src="chat/chat.js"></script></body></html>' % markup
    return "\n".join(head) + "\n" + body


def base_url():
    from PySide6.QtCore import QUrl
    return QUrl.fromLocalFile(os.path.join(assets_dir(), ""))

