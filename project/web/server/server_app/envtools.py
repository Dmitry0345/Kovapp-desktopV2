# -*- coding: utf-8 -*-
"""Чтение настроек окружения, устойчивое к пустым значениям.

Строка вида `VIDEO_MAX_PARALLEL=` в .env — это заданная переменная со
значением "", а не отсутствующая. Обычный int("") падает и роняет сервер
ещё на импорте, до единой строчки в журнале. Поэтому все числовые настройки
читаются отсюда: пусто — берём значение по умолчанию, мусор — тоже, но с
предупреждением в журнале, чтобы опечатку было видно.

Модуль намеренно ни от чего не зависит, кроме стандартной библиотеки.
"""

import logging
import os

logger = logging.getLogger(__name__)


def env_str(name: str, default: str = "") -> str:
    """Строка без кавычек и пробелов по краям; пустая считается незаданной."""
    raw = os.getenv(name)
    if raw is None:
        return default
    raw = raw.strip().strip('"').strip("'").strip()
    return raw if raw else default


def env_int(name: str, default: int) -> int:
    raw = env_str(name, "")
    if not raw:
        return default
    try:
        return int(float(raw))          # «2» и «2.0» одинаково подходят
    except (TypeError, ValueError):
        logger.warning("Настройка %s=%r — не число, беру значение по умолчанию %s", name, raw, default)
        return default


def env_float(name: str, default: float) -> float:
    raw = env_str(name, "")
    if not raw:
        return default
    try:
        return float(raw.replace(",", "."))
    except (TypeError, ValueError):
        logger.warning("Настройка %s=%r — не число, беру значение по умолчанию %s", name, raw, default)
        return default


def env_bool(name: str, default: bool = False) -> bool:
    raw = env_str(name, "").lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on", "да")
