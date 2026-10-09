# -*- coding: utf-8 -*-
"""Единая палитра и типографика приложения.

Один источник правды по цветам: все разделы и окна берут значения отсюда.
Две темы — светлая и тёмная, значения те же, что в css/theme.css
веб-версии (светлая согласована 06.10.2026, тёмная — «Чёрный со слоями»).
Тема выбирается при запуске (apply) и хранится на сервере вместе
с масштабом под ключом web.settings.pc — общим с сайтом на компьютере.
"""

from PySide6.QtGui import QColor, QFont, QPalette

import scaler

LIGHT = {
    "HEADER_TOP": "#607D8B", "HEADER_MID": "#546E7A", "HEADER_BOTTOM": "#37474F",
    "TOP_BAR_FG": "#37474F",
    "ACCENT": "#546E7A", "ACCENT_SOFT": "#78909C", "ACCENT_FILL": "#546E7A",
    "PAGE_BG": "#F1F4F6", "CARD_BG": "#FFFFFF", "CARD_BORDER": "#DCE3E7", "CHIP_BG": "#ECEFF1",
    "TEXT_LABEL": "#546E7A", "TEXT_VALUE": "#263238", "TEXT_MUTED": "#627A86", "TEXT_ON_ACCENT": "#FFFFFF",
    "DECOR": "#B0BEC5",                      # неяркие линии, стрелки, ползунки прокрутки
    "BTN_BG": "#546E7A", "BTN_BORDER": "#546E7A", "BTN_HOVER": "#5F7A86", "BTN_PRESSED": "#455A64",
    "BTN_GHOST_BG": "#ECEFF1", "BTN_GHOST_BORDER": "#CFD8DC", "BTN_GHOST_HOVER": "#DDE3E6",
    "BTN_GHOST_PRESSED": "#CBD4D8", "BTN_GHOST_TEXT": "#37474F",
    "DANGER": "#D32F2F", "DANGER_HOVER": "#C62828", "DANGER_PRESSED": "#B71C1C", "DANGER_TEXT": "#D32F2F",
    "SUCCESS": "#43A047", "WARNING": "#F9A825", "WARNING_TEXT": "#B47A16", "INFO": "#3A7283", "CRITICAL": "#E53935",
    "SUCCESS_SOFT": "#E8F5E9", "WARNING_SOFT": "#FFF8E1", "INFO_SOFT": "#E4EEF2", "CRITICAL_SOFT": "#FFEBEE",
    "SUCCESS_TEXT": "#2E7D32", "CRITICAL_TEXT": "#C62828", "DEADLINE_OVERDUE": "#C62828",
    "STAGE_DONE": "#4CAF50", "STAGE_DONE_BORDER": "#388E3C", "STAGE_TODO": "#E0E0E0", "STAGE_TODO_BORDER": "#A0A0A0",
    "FIELD_BORDER": "#78909C", "COUNT_BG": "#E53935", "BADGE_BG": "#D32F2F",
    "SURFACE_2": "#F7FAFB", "SURFACE_3": "#FBFDFE", "SURFACE_FOOT": "#FAFCFD",
    "SURFACE_HEAD_A": "#FBFCFD", "SURFACE_HEAD_B": "#F3F6F8", "FOCUS_RING": (84, 110, 122, 46),
    "SELECTION": "#D7E1E6", "TITLE_FG": "#546E7A",
    "MENU_ITEM_FG": "#37474F", "MENU_DIVIDER": "#CFD8DC", "MENU_HOVER": "#ECEFF1", "MENU_ACTIVE": "#CFD8DC",
    "MENU_CLOSE_BG": "#E0E0E0", "MENU_CLOSE_BORDER": "#CCCCCC", "MENU_CLOSE_FG": "#555555", "MENU_CLOSE_HOVER": "#D0D0D0",
    "KNOB_BG": "#FFFFFF", "TOOLTIP_BG": "#37474F", "TOOLTIP_FG": "#FFFFFF", "TOOLTIP_BORDER": "#37474F",
    "LINK": "#3A7283",
    "MARK_INK": "#132A32", "MARK_ACCENT": "#F5B301", "MARK_KNOCK": "#F5F7F6",
    "STAGE_DARK": "#0F2027", "STAGE_CHECK_A": "#E9EDEC", "STAGE_CHECK_B": "#F7F9FA",   # редакторы фото и видео
    "LOGIN_TITLE": "#37474F", "LOGIN_BLOB_1": "#B9D0D8", "LOGIN_BLOB_2": "#CFE0D6",
    # экран входа (login.css): третье пятно, прозрачность пятен, сетка; #AARRGGBB
    "LOGIN_BLOB_3": "#47F5B301", "LOGIN_BLOB_OPACITY": 0.5, "LOGIN_GRID": "#0D132A32",
    "SHADOW": (38, 50, 56, 110), "OVERLAY_RGBA": (38, 50, 56, 110),
}

DARK = dict(LIGHT)
DARK.update({
    "HEADER_TOP": "#000000", "HEADER_MID": "#000000", "HEADER_BOTTOM": "#000000",
    "TOP_BAR_FG": "#F5F5F7",
    "ACCENT": "#409CFF", "ACCENT_SOFT": "#409CFF", "ACCENT_FILL": "#0071E3",
    "PAGE_BG": "#000000", "CARD_BG": "#1C1C1E", "CARD_BORDER": "#38383A", "CHIP_BG": "#2C2C2E",
    "TEXT_LABEL": "#A1A1A6", "TEXT_VALUE": "#F5F5F7", "TEXT_MUTED": "#8E8E93", "TEXT_ON_ACCENT": "#FFFFFF",
    "DECOR": "#48484A",
    "BTN_BG": "#0071E3", "BTN_BORDER": "#0071E3", "BTN_HOVER": "#0077ED", "BTN_PRESSED": "#0062C4",
    "BTN_GHOST_BG": "#2C2C2E", "BTN_GHOST_BORDER": "#3A3A3C", "BTN_GHOST_HOVER": "#3A3A3C",
    "BTN_GHOST_PRESSED": "#48484A", "BTN_GHOST_TEXT": "#F5F5F7",
    "DANGER": "#D70015", "DANGER_HOVER": "#E0001A", "DANGER_PRESSED": "#C8001A", "DANGER_TEXT": "#FF453A",
    "SUCCESS": "#30D158", "WARNING": "#FF9F0A", "WARNING_TEXT": "#FFB340", "INFO": "#64D2FF", "CRITICAL": "#FF453A",
    "SUCCESS_SOFT": "#0E2A17", "WARNING_SOFT": "#33240A", "INFO_SOFT": "#0C2633", "CRITICAL_SOFT": "#3A1210",
    "SUCCESS_TEXT": "#30D158", "CRITICAL_TEXT": "#FF6961", "DEADLINE_OVERDUE": "#FF453A",
    "STAGE_DONE": "#30D158", "STAGE_DONE_BORDER": "#248A3D", "STAGE_TODO": "#3A3A3C", "STAGE_TODO_BORDER": "#48484A",
    "FIELD_BORDER": "#67676A", "COUNT_BG": "#D70015", "BADGE_BG": "#D70015",
    "SURFACE_2": "#2C2C2E", "SURFACE_3": "#1C1C1E", "SURFACE_FOOT": "#141416",
    "SURFACE_HEAD_A": "#2C2C2E", "SURFACE_HEAD_B": "#1C1C1E", "FOCUS_RING": (10, 132, 255, 89),
    "SELECTION": "#3A3A3C", "TITLE_FG": "#F5F5F7",
    "MENU_ITEM_FG": "#F5F5F7", "MENU_DIVIDER": "#38383A", "MENU_HOVER": "#2C2C2E", "MENU_ACTIVE": "#3A3A3C",
    "MENU_CLOSE_BG": "#2C2C2E", "MENU_CLOSE_BORDER": "#3A3A3C", "MENU_CLOSE_FG": "#A1A1A6", "MENU_CLOSE_HOVER": "#3A3A3C",
    "KNOB_BG": "#DCE7EC", "TOOLTIP_BG": "#2C2C2E", "TOOLTIP_FG": "#F5F5F7", "TOOLTIP_BORDER": "#48484A",
    "LINK": "#64D2FF",
    "MARK_INK": "#EAEFEE", "MARK_KNOCK": "#17272E",
    "STAGE_CHECK_A": "#1A2A31", "STAGE_CHECK_B": "#21323A",
    "LOGIN_TITLE": "#D6E3EA", "LOGIN_BLOB_1": "#1B4050", "LOGIN_BLOB_2": "#17362F",
    "LOGIN_BLOB_3": "#29F5B301", "LOGIN_BLOB_OPACITY": 0.55, "LOGIN_GRID": "#0DBED7E1",
    "SHADOW": (0, 0, 0, 160), "OVERLAY_RGBA": (0, 0, 0, 184),
})

# Цвета мессенджера (chat/chat.css): свой набор, вариант А согласован 06.10.2026
CHAT_LIGHT = {
    "--col-bg-main": "#f5f5f5", "--col-bg-sidebar": "#ffffff", "--col-bg-chat": "#e5ddd5",
    "--col-primary": "#3f51b5", "--col-primary-hover": "#303f9f", "--col-divider": "#e0e0e0",
    "--col-hover": "#f0f0f0", "--col-text-main": "#212121", "--col-text-secondary": "#616161",
    "--col-text-disabled": "#616161", "--col-selected": "#e8eaf6", "--msg-mine-bg": "#dcf8c6",
    "--msg-other-bg": "#ffffff", "--col-panel": "#ffffff", "--col-date-bg": "rgba(255,255,255,.92)",
    "--col-date-fg": "#54656F", "--col-mine-meta": "#56634F", "--col-field-border": "#8C8C8C",
    "--col-danger": "#C62828",
}
CHAT_DARK = {
    "--col-bg-main": "#000000", "--col-bg-sidebar": "#1C1C1E", "--col-bg-chat": "#17171A",
    "--col-primary": "#409CFF", "--col-primary-hover": "#70B4FF", "--col-divider": "#38383A",
    "--col-hover": "#2C2C2E", "--col-text-main": "#F5F5F7", "--col-text-secondary": "#AEAEB2",
    "--col-text-disabled": "#8E8E93", "--col-selected": "#3A3A3C", "--msg-mine-bg": "#2B5278",
    "--msg-other-bg": "#2C2C30", "--col-panel": "#1C1C1E", "--col-date-bg": "rgba(0,0,0,.2)",
    "--col-date-fg": "#FFFFFF", "--col-mine-meta": "#C8DAF0", "--col-field-border": "#67676A",
    "--col-danger": "#FF453A",
}

MODE = "light"

# ── Палитра аватаров без фотографии ──────────────────────────────────────────
AVATAR_PALETTE = (
    "#546E7A", "#5C6BC0", "#26A69A", "#8D6E63", "#7E57C2",
    "#42A5F5", "#66BB6A", "#EC407A", "#EF6C00", "#78909C",
)

FONT_FAMILY = "Arial"


def system_mode():
    """Тема Windows (параметр «Режим приложений»): 'dark' или 'light'."""
    import sys
    if sys.platform.startswith("win"):
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            winreg.CloseKey(key)
            return "light" if value else "dark"
        except OSError:
            return "light"
    return "light"


def resolve(mode):
    """'auto' («Как в системе») превращается в тему системы."""
    return system_mode() if mode == "auto" else ("dark" if mode == "dark" else "light")


def apply(mode):
    """Включает тему: значения палитры становятся атрибутами модуля."""
    global MODE, OVERLAY, CHOSEN
    CHOSEN = mode if mode in ("light", "dark", "auto") else "light"
    MODE = resolve(mode)
    values = DARK if MODE == "dark" else LIGHT
    g = globals()
    for key, value in values.items():
        g[key] = value
    OVERLAY = QColor(*values["OVERLAY_RGBA"])


def is_dark():
    return MODE == "dark"


def chat_vars():
    return dict(CHAT_DARK if is_dark() else CHAT_LIGHT)


def qpalette():
    """Палитра Fusion под тему — для полей, меню и окон без своего стиля."""
    p = QPalette()
    c = QColor
    p.setColor(QPalette.Window, c(PAGE_BG))
    p.setColor(QPalette.WindowText, c(TEXT_VALUE))
    p.setColor(QPalette.Base, c(CARD_BG))
    p.setColor(QPalette.AlternateBase, c(SURFACE_2))
    p.setColor(QPalette.Text, c(TEXT_VALUE))
    p.setColor(QPalette.Button, c(BTN_GHOST_BG))
    p.setColor(QPalette.ButtonText, c(BTN_GHOST_TEXT))
    p.setColor(QPalette.ToolTipBase, c(TOOLTIP_BG))
    p.setColor(QPalette.ToolTipText, c(TOOLTIP_FG))
    p.setColor(QPalette.Highlight, c(ACCENT_FILL))
    p.setColor(QPalette.HighlightedText, c("#FFFFFF"))
    p.setColor(QPalette.Link, c(LINK))
    p.setColor(QPalette.PlaceholderText, c(TEXT_MUTED))
    p.setColor(QPalette.Light, c(CARD_BG))
    p.setColor(QPalette.Midlight, c(CHIP_BG))
    p.setColor(QPalette.Mid, c(CARD_BORDER))
    p.setColor(QPalette.Dark, c(DECOR))
    p.setColor(QPalette.Shadow, c("#000000"))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, c(TEXT_MUTED))
    return p


def avatar_color(text):
    """Стабильный цвет по строке (обычно «Фамилия + Имя»)."""
    if not text:
        return AVATAR_PALETTE[0]
    value = 0
    for char in str(text):
        value = (value * 31 + ord(char)) & 0xFFFFFFFF
    return AVATAR_PALETTE[value % len(AVATAR_PALETTE)]


def font(size_px, bold=False, letter_spacing=0):
    """QFont с размером в пикселях — предсказуемо работает со scaler."""
    result = QFont(FONT_FAMILY)
    result.setPixelSize(max(int(size_px), 1))
    result.setBold(bold)
    if letter_spacing:
        result.setLetterSpacing(QFont.AbsoluteSpacing, letter_spacing)
    return result


def scaled_font(size_pt, bold=False):
    """QFont по «логическому» размеру, прогнанный через общий масштаб."""
    return font(scaler.scale(size_pt), bold)


apply("light")
