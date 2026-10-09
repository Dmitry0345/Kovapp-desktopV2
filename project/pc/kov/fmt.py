# -*- coding: utf-8 -*-
"""Даты, имена и размеры — так же, как их показывает сайт (Kov.UI)."""

from datetime import date, datetime, timedelta, timezone


def _get(obj, key, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def parse_date(value):
    """'2026-10-12' или ISO-время → datetime (местное время) или None."""
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    else:
        text = str(value).strip()
        try:
            if len(text) == 10:
                return datetime.strptime(text, "%Y-%m-%d")
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def fmt_date(value, empty="—"):
    d = parse_date(value)
    return d.strftime("%d.%m.%Y") if d else empty


def fmt_datetime(value, empty="—"):
    d = parse_date(value)
    return d.strftime("%d.%m.%Y %H:%M") if d else empty


def iso_date(d):
    return d.strftime("%Y-%m-%d")


def deadline_state(deadline, active=True):
    """'overdue' — срок прошёл, 'urgent' — меньше суток, иначе None."""
    d = parse_date(deadline)
    if not d or active is False:
        return None
    today = datetime.combine(date.today(), datetime.min.time())
    if d < today:
        return "overdue"
    if (d - today) < timedelta(days=1):
        return "urgent"
    return None


def full_name(person, with_middle=False):
    if not person:
        return ""
    parts = [_get(person, "last_name"), _get(person, "first_name")]
    if with_middle:
        parts.append(_get(person, "middle_name"))
    text = " ".join(p for p in parts if p)
    return text or _get(person, "name") or ""


def fmt_size(size):
    try:
        size = float(size or 0)
    except (TypeError, ValueError):
        return ""
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024 or unit == "ГБ":
            return ("%d %s" % (size, unit)) if unit == "Б" else ("%.1f %s" % (size, unit)).replace(".", ",")
        size /= 1024.0
    return ""


def plural(n, one, few, many):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def now_utc():
    return datetime.now(timezone.utc)
