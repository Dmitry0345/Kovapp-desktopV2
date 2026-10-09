# -*- coding: utf-8 -*-
"""Рассылка уведомлений о действиях — как send() из js/views/notifications.js:
автор действия получает своё сообщение («Вы завершили этап…»), остальные —
общее. Уведомление открывает раздел target_section с номером target_id."""

from kov.api import ApiError, client


def _post(ids, message, section, target_id):
    body = {"user_ids": ids, "message": message, "target_section": section or "notifications"}
    if target_id is not None:
        body["target_id"] = target_id
    try:
        client.post("notifications", json_body=body)
        return True
    except ApiError as e:
        if e.status in (400, 422):
            for i in ids:
                one = dict(body)
                one.pop("user_ids", None)
                one["employee_id"] = i
                client.post("notifications", json_body=one)
            return True
        raise


def send(user_ids, message, section=None, target_id=None, self_message=None):
    """Вызывать в фоне (jobs.run). Возвращает True, если всё ушло."""
    me = client.user_id
    ids = []
    for x in user_ids or []:
        try:
            v = int(x)
        except (TypeError, ValueError):
            continue
        if v and v not in ids:
            ids.append(v)
    if not ids:
        return False
    groups = []
    if self_message and me in ids:
        groups.append(([me], self_message))
        rest = [i for i in ids if i != me]
        if rest:
            groups.append((rest, message))
    else:
        groups.append((ids, message))
    ok = True
    for g_ids, text in groups:
        if not _post(g_ids, text, section, target_id):
            ok = False
    return ok
