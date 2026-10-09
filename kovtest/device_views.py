"""Карточка задачи на устройстве: телефон — вкладки внизу (Этап · Файлы · Люди · История),
планшет и компьютер — переключатель «Описание этапа · Файлы задачи · Файлы этапа»."""

VISIBLE = "const b = document.querySelector(arguments[0]); return !!b && b.getBoundingClientRect().height > 0;"


def phone_card(d) -> bool:
    return bool(d.js(VISIBLE, ".tv-tab[data-k='stage']"))


def open_stage_desc(d) -> bool:
    """Описание этапа: на телефоне — вкладка «Этап»."""
    if phone_card(d):
        return d.click(".tv-tab[data-k='stage']")
    return d.click(".content [data-key='desc']")


def open_stage_files(d) -> bool:
    """Файлы этапа: на телефоне — «Файлы» внизу, затем «Файлы этапа»."""
    if phone_card(d):
        return d.click(".tv-tab[data-k='files']") and d.click(".tv-sw[data-k='stage']")
    return d.click(".content [data-key='stage']")
