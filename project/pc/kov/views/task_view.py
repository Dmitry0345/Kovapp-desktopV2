# -*- coding: utf-8 -*-
"""Окно задачи — как js/views/task-view.js веб-версии (раскладка компьютера).

Сверху по центру — лента этапов (одна фигура с плавными переходами),
слева карточки «Прогресс», «О задаче», «Участники» и описание задачи,
справа рабочая зона с вкладками «Описание этапа / Файлы задачи / Файлы
этапа», снизу строка действий: состояние этапа, «Завершить/Возобновить
этап», журнал, «Продолжить задачу», «Завершить задачу (Архив)».

Описание этапа сохраняется само (через 0,9 с после правки) вместе с
авторством фрагментов: под каждым куском текста — черта цвета его автора,
имя — в подсказке. Правки других сотрудников приходят по сокету.
"""

import html
import re

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFontMetrics, QPainter, QPainterPath, QPen, QTextCharFormat, QTextCursor,
                         QTextFormat)
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSizePolicy, QTextBrowser,
                             QTextEdit, QToolTip, QVBoxLayout, QWidget)

from general_functions import theme
from kov import files as kfiles
from kov import icons, jobs, notify, rights
from kov.api import client, human_error
from kov.dialogs import Modal, confirm, field
from kov.fmt import deadline_state, fmt_date, fmt_datetime
from kov.people import ClampedPeople, LeadRow, avatar, full_name, people_dialog, person_of
from kov.store import store
from kov.ui import (Button, Card, Chip, IconBadge, InfoCard, InfoRow, Segmented, Select, StageDots, border, fs,
                    clear_layout, empty, font, gate, label, loading, mix, muted, rem, scroll_area, text_qss, toast)
from kov.views.base import Section

URL_RE = re.compile(r"(https?://[^\s<]+)")
HISTORY_GLYPH = {"deadline": "clock", "responsible": "users", "assigned": "users", "stage_completed": "check",
                 "stage_department": "building", "activity": "archive", "project_id": "project", "task_file": "file",
                 "stage_file": "file", "stage_participants": "users", "task_participants": "users",
                 "stage_done": "check", "stage_added": "plus", "stage_removed": "trash", "stage_resumed": "refresh",
                 "stage_text": "edit", "stage_lead": "users", "task_completion": "flag", "task_completed": "check"}


# ── текст в единицах UTF-16 (как строки JavaScript и QTextDocument) ─────────

def u16(s):
    b = (s or "").encode("utf-16-le")
    return [b[i] | (b[i + 1] << 8) for i in range(0, len(b), 2)]


def u16len(s):
    return len((s or "").encode("utf-16-le")) // 2


def u16slice(s, start, end):
    b = (s or "").encode("utf-16-le")
    return b[start * 2:end * 2].decode("utf-16-le", "replace")


def diff_one(old, new):
    """Одна заменённая область: общий префикс и суффикс, между ними — правка."""
    a, b = u16(old), u16(new)
    prefix = 0
    while prefix < len(a) and prefix < len(b) and a[prefix] == b[prefix]:
        prefix += 1
    suffix = 0
    while suffix < len(a) - prefix and suffix < len(b) - prefix and a[len(a) - 1 - suffix] == b[len(b) - 1 - suffix]:
        suffix += 1
    return prefix, len(a) - suffix, len(b) - suffix - prefix


def merge_spans(spans):
    lst = sorted([dict(s) for s in spans if s["end"] > s["start"]], key=lambda s: (s["start"], s["end"]))
    out = []
    for sp in lst:
        prev = out[-1] if out else None
        if prev and prev["end"] == sp["start"] and prev.get("author_id") == sp.get("author_id"):
            prev["end"] = sp["end"]
        else:
            out.append({"start": sp["start"], "end": sp["end"], "author_id": sp.get("author_id"),
                        "author": sp.get("author")})
    return out


def spans_insert(spans, pos, length, author_id, author):
    out = []
    for sp in spans:
        if sp["end"] <= pos:
            out.append(sp)
        elif sp["start"] >= pos:
            out.append(dict(sp, start=sp["start"] + length, end=sp["end"] + length))
        else:
            out.append(dict(sp, start=sp["start"], end=pos))
            out.append(dict(sp, start=pos + length, end=sp["end"] + length))
    out.append({"start": pos, "end": pos + length, "author_id": author_id, "author": author})
    return merge_spans(out)


def spans_delete(spans, start, end):
    length = end - start
    out = []
    for sp in spans:
        if sp["end"] <= start:
            out.append(sp)
            continue
        if sp["start"] >= end:
            out.append(dict(sp, start=sp["start"] - length, end=sp["end"] - length))
            continue
        left = (sp["start"], start) if sp["start"] < start else None
        right = (end - length, sp["end"] - length) if sp["end"] > end else None
        if left and right:
            out.append(dict(sp, start=left[0], end=right[1]))
            continue
        if left:
            out.append(dict(sp, start=left[0], end=left[1]))
        if right:
            out.append(dict(sp, start=max(start, right[0]), end=right[1]))
    return merge_spans([s for s in out if s["end"] > s["start"]])


def clamp_spans(spans, length):
    out = []
    for sp in spans or []:
        try:
            s = max(0, min(int(sp.get("start") or 0), length))
            e = max(0, min(int(sp.get("end") or 0), length))
        except (TypeError, ValueError):
            continue
        if e > s:
            out.append({"start": s, "end": e, "author_id": sp.get("author_id"), "author": sp.get("author")})
    return merge_spans(out)


def span_author_name(sp):
    a = sp.get("author")
    if a and (a.get("last_name") or a.get("first_name")):
        return full_name(a, True)
    if not sp.get("author_id"):
        return "Автор удалён"
    found = next((e for e in (store._employees or []) if e.get("id") == sp.get("author_id")), None)
    return full_name(found, True) if found else "Сотрудник №%s" % sp.get("author_id")


def me_short():
    me = store.me or {}
    return {"id": client.user_id, "last_name": me.get("last_name"), "first_name": me.get("first_name"),
            "middle_name": me.get("middle_name")}


def _who(person, emp_id):
    if person and (person.get("last_name") or person.get("first_name")):
        return full_name(person)
    key = (person or {}).get("id") or emp_id
    try:
        key = int(key or 0)
    except (TypeError, ValueError):
        key = 0
    if not key:
        return None
    found = next((e for e in (store._employees or []) if e.get("id") == key), None)
    return full_name(found) if found else "сотрудник №%d" % key


def stage_who(s):
    return _who(s.get("completed_by"), s.get("completed_by_employee_id") or s.get("completed_by_id") or s.get("who_completed"))


def stage_when(s):
    return s.get("completion_timestamp") or s.get("completed_at") or s.get("when_completed")


def archived_who(t):
    return _who(t.get("who_archived_employee"), t.get("who_archived") or t.get("archived_by_employee_id"))


def archived_when(t):
    return t.get("archived_at") or t.get("when_archived")


# ══════════════════════════════════════════════════════════════════════════
# Лента этапов
# ══════════════════════════════════════════════════════════════════════════

class _StageButton(QPushButton):
    def __init__(self, stage, parent=None):
        super().__init__(parent)
        self.stage = stage
        self.on = False
        self.locked = False
        self.reason = ""
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)
        self.setStyleSheet("QPushButton { background: transparent; border: none; }")
        fm1 = QFontMetrics(font(0.875, bold=True))
        fm2 = QFontMetrics(font(0.72))
        dep = stage.get("department_name") or "—"
        w = max(fm1.horizontalAdvance("Этап %s" % stage.get("stage_number")), fm2.horizontalAdvance(dep)) + 2 * rem(1.8)
        self.setFixedSize(max(rem(9.5), w), rem(3.0))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        s = self.stage
        if self.on:
            c1 = c2 = QColor(theme.INFO)
            c2.setAlphaF(0.75)
        elif s.get("is_completed"):
            c1 = QColor(theme.STAGE_DONE_BORDER)
            c2 = QColor(theme.STAGE_DONE_BORDER)
            c2.setAlphaF(0.7)
        else:
            c1 = QColor(theme.ACCENT)
            c2 = QColor(theme.TEXT_LABEL)
        h1 = rem(1.15)
        top = (self.height() - h1 - rem(0.95)) // 2
        p.setFont(font(0.875, bold=True))
        p.setPen(c1)
        p.drawText(0, top, self.width(), h1, Qt.AlignHCenter | Qt.AlignVCenter, "Этап %s" % s.get("stage_number"))
        p.setFont(font(0.72))
        p.setPen(c2)
        dep = s.get("department_name") or "—"
        fm = QFontMetrics(font(0.72))
        p.drawText(0, top + h1, self.width(), rem(0.95), Qt.AlignHCenter | Qt.AlignVCenter,
                   fm.elidedText(dep, Qt.ElideRight, self.width() - rem(1)))
        if self.locked:
            k = rem(0.8)
            p.drawPixmap(self.width() - k - rem(0.9), (self.height() - k) // 2, icons.pixmap("key", k, theme.TEXT_MUTED))
        p.end()


class StageStrip(QWidget):
    """Лента этапов: фон и обводку рисует одна фигура; кнопки поверх прозрачные."""

    picked = Signal(int)

    def __init__(self, stages, parent=None):
        super().__init__(parent)
        self.buttons = []
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        for s in stages:
            b = _StageButton(s)
            b.clicked.connect(lambda _=False, n=s.get("stage_number"): self.picked.emit(n))
            lay.addWidget(b)
            self.buttons.append(b)
        self.setFixedHeight(rem(3.0))
        self.setFixedWidth(sum(b.width() for b in self.buttons) or rem(9.5))

    def mark(self, number):
        for b in self.buttons:
            b.on = b.stage.get("stage_number") == number
            b.update()
        self.update()

    def lock(self, number, reason):
        for b in self.buttons:
            if b.stage.get("stage_number") == number:
                b.locked = True
                b.reason = reason
                b.setToolTip(reason)

    def _colors(self, b):
        if b.on:
            return QColor(theme.INFO_SOFT), QColor(theme.INFO)
        if b.stage.get("is_completed"):
            return QColor(theme.SUCCESS_SOFT), QColor(theme.STAGE_DONE)
        return QColor(theme.CARD_BG), QColor(theme.ACCENT_SOFT)

    def paintEvent(self, e):
        if not self.buttons:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        W, H = float(self.width()), float(self.height())
        m = 1.0
        T, B, L, Rg = m, H - m, m, W - m
        height = B - T
        R = min(height * 0.26, rem(0.875))
        D = height * 0.3
        widths = [b.width() for b in self.buttons]
        N = min(height * 1.3, min(widths) * 0.55)
        q = N / 4
        wT, wB = T + D, B - D
        edges = []
        x = 0.0
        for b in self.buttons:
            edges.append((x, x + b.width()))
            x += b.width()
        n = len(self.buttons)
        for i, b in enumerate(self.buttons):
            first, last = i == 0, i == n - 1
            x0 = L if first else (edges[i - 1][1] + edges[i][0]) / 2
            x1 = Rg if last else (edges[i][1] + edges[i + 1][0]) / 2
            fill, stroke = self._colors(b)
            area = QPainterPath()
            # верх
            if first:
                area.moveTo(L + R, T)
            else:
                area.moveTo(x0, wT)
                area.cubicTo(x0 + q, wT, x0 + q, T, x0 + N / 2, T)
            if last:
                area.lineTo(Rg - R, T)
                area.arcTo(QRectF(Rg - 2 * R, T, 2 * R, 2 * R), 90, -90)
                area.lineTo(Rg, B - R)
                area.arcTo(QRectF(Rg - 2 * R, B - 2 * R, 2 * R, 2 * R), 0, -90)
            else:
                area.lineTo(x1 - N / 2, T)
                area.cubicTo(x1 - q, T, x1 - q, wT, x1, wT)
                area.lineTo(x1, wB)
                area.cubicTo(x1 - q, wB, x1 - q, B, x1 - N / 2, B)
            if first:
                area.lineTo(L + R, B)
                area.arcTo(QRectF(L, B - 2 * R, 2 * R, 2 * R), 270, -90)
                area.lineTo(L, T + R)
                area.arcTo(QRectF(L, T, 2 * R, 2 * R), 180, -90)
            else:
                area.lineTo(x0 + N / 2, B)
                area.cubicTo(x0 + q, B, x0 + q, wB, x0, wB)
            area.closeSubpath()
            p.setPen(Qt.NoPen)
            p.setBrush(fill)
            p.drawPath(area)
            # обводка — верх, низ и внешние торцы
            line = QPainterPath()
            if first:
                line.moveTo(L + R, T)
            else:
                line.moveTo(x0, wT)
                line.cubicTo(x0 + q, wT, x0 + q, T, x0 + N / 2, T)
            if last:
                line.lineTo(Rg - R, T)
            else:
                line.lineTo(x1 - N / 2, T)
                line.cubicTo(x1 - q, T, x1 - q, wT, x1, wT)
            if first:
                line.moveTo(L + R, B)
            else:
                line.moveTo(x0, wB)
                line.cubicTo(x0 + q, wB, x0 + q, B, x0 + N / 2, B)
            if last:
                line.lineTo(Rg - R, B)
            else:
                line.lineTo(x1 - N / 2, B)
                line.cubicTo(x1 - q, B, x1 - q, wB, x1, wB)
            if first:
                line.moveTo(L + R, T)
                line.arcTo(QRectF(L, T, 2 * R, 2 * R), 90, 90)
                line.lineTo(L, B - R)
                line.arcTo(QRectF(L, B - 2 * R, 2 * R, 2 * R), 180, 90)
            if last:
                line.moveTo(Rg - R, T)
                line.arcTo(QRectF(Rg - 2 * R, T, 2 * R, 2 * R), 90, -90)
                line.lineTo(Rg, B - R)
                line.arcTo(QRectF(Rg - 2 * R, B - 2 * R, 2 * R, 2 * R), 0, -90)
            pen = QPen(stroke, max(1.2, border(1) * 1.2))
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPath(line)
        p.end()


# ══════════════════════════════════════════════════════════════════════════
# Описание этапа: авторство + автосохранение
# ══════════════════════════════════════════════════════════════════════════

def _desc_qss(widget):
    b = border()
    return (f"{widget} {{ background: {theme.CARD_BG}; color: {theme.TEXT_VALUE}; border: {b}px solid {theme.FIELD_BORDER};"
            f" border-radius: {rem(0.5)}px; padding: {rem(0.35)}px {rem(0.45)}px; font-size: {fs(0.875)};"
            f" selection-background-color: {theme.ACCENT_FILL}; selection-color: #FFFFFF; }}"
            f"{widget}:focus {{ border-color: {theme.ACCENT}; }}")


def _underline(fmt_target, color):
    f = QTextCharFormat()
    f.setUnderlineStyle(QTextCharFormat.SingleUnderline)
    f.setUnderlineColor(QColor(color))
    return f


class _Editor(QPlainTextEdit):
    hovered = Signal(QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)

    def mouseMoveEvent(self, e):
        self.hovered.emit(e.position().toPoint())
        super().mouseMoveEvent(e)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        cb = getattr(self, "on_blur", None)
        if cb:
            cb()


class DescEditor(QWidget):
    """Поле описания этапа с подсветкой авторов и сохранением на сервер."""

    def __init__(self, view, stage, status_cb):
        super().__init__()
        self.view = view
        self.stage = stage
        self.status_cb = status_cb
        self.text = stage.get("text_description") or ""
        self.spans = clamp_spans(stage.get("spans"), u16len(self.text))
        self.saved = self.text
        self.saving = self.pending = self.failed = False
        self._remote = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.input = _Editor()
        self.input.setPlaceholderText("Введите описание этого этапа…")
        self.input.setFont(font(0.875))
        self.input.setStyleSheet(_desc_qss("QPlainTextEdit"))
        self.input.setPlainText(self.text)
        self.input.textChanged.connect(self._changed)
        self.input.hovered.connect(self._hover)
        self.input.on_blur = lambda: self.flush() if self.text != self.saved else None
        lay.addWidget(self.input)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.flush)
        self.paint()
        self.status("success", "Сохранено")

    def status(self, kind, text):
        if self.status_cb:
            self.status_cb(kind, text)

    def paint(self):
        sels = []
        doc = self.input.document()
        for sp in self.spans:
            color = theme.avatar_color(span_author_name(sp))
            c = QTextCursor(doc)
            c.setPosition(min(sp["start"], doc.characterCount() - 1))
            c.setPosition(min(sp["end"], doc.characterCount() - 1), QTextCursor.KeepAnchor)
            sel = QTextEdit.ExtraSelection()
            sel.cursor = c
            sel.format = _underline(None, color)
            sels.append(sel)
        self.input.setExtraSelections(sels)

    def _changed(self):
        if self._remote:
            return
        new = self.input.toPlainText()
        start, del_end, ins_len = diff_one(self.text, new)
        if del_end > start:
            self.spans = spans_delete(self.spans, start, del_end)
        if ins_len > 0:
            self.spans = spans_insert(self.spans, start, ins_len, client.user_id, me_short())
        self.text = new
        self.paint()
        self.status("", "Изменения не сохранены")
        self.timer.start(900)

    def _hover(self, pos):
        c = self.input.cursorForPosition(pos)
        at = c.position()
        rect = self.input.cursorRect(c)
        if abs(rect.center().y() - pos.y()) > rect.height():
            QToolTip.hideText()
            return
        found = next((sp for sp in self.spans if sp["start"] <= at < sp["end"] or
                      (sp["start"] < at <= sp["end"] and at == sp["end"])), None)
        if found:
            QToolTip.showText(self.input.viewport().mapToGlobal(pos + QPoint(rem(0.8), -rem(1.6))),
                              span_author_name(found), self.input)
        else:
            QToolTip.hideText()

    def flush(self):
        self.timer.stop()
        if self.saving:
            self.pending = True
            return
        if self.text == self.saved and not self.failed:
            self.status("success", "Сохранено")
            return
        self.saving = True
        self.status("info", "Сохранение…")
        self.spans = clamp_spans(self.spans, u16len(self.text))
        body = {"text_description": self.text,
                "spans": [{"start": s["start"], "end": s["end"], "author_id": s.get("author_id") or None}
                          for s in self.spans]}
        path = "tasks/%s/stages/%s" % (self.view.task_id, self.stage["id"])

        def ok(_res, sent=body["text_description"], spans=list(self.spans)):
            self.saved = sent
            self.failed = False
            self.stage["text_description"] = sent
            self.stage["spans"] = spans
            self.status("success", "Сохранено" if self.text == self.saved else "Изменения не сохранены")
            self._done()

        def bad(e):
            self.failed = True
            self.status("warning", "Не сохранено: " + human_error(e))
            self._done()
        jobs.run(client.put, path, json_body=body, done=ok, failed=bad, owner=self)

    def _done(self):
        self.saving = False
        if self.pending:
            self.pending = False
            self.flush()
        elif self.text != self.saved and not self.failed:
            self.timer.start(900)

    def has_unsaved(self):
        return self.text != self.saved

    def flush_sync(self):
        """Перед завершением этапа: дописать несохранённое сразу."""
        if not self.has_unsaved():
            return True
        body = {"text_description": self.text,
                "spans": [{"start": s["start"], "end": s["end"], "author_id": s.get("author_id") or None}
                          for s in clamp_spans(self.spans, u16len(self.text))]}
        try:
            client.put("tasks/%s/stages/%s" % (self.view.task_id, self.stage["id"]), json_body=body)
            self.saved = self.text
            self.status("success", "Сохранено")
            return True
        except Exception as e:
            self.status("warning", "Не сохранено: " + human_error(e))
            return False

    def apply_remote(self, text, spans):
        if self.text != self.saved:
            self.status("warning", "Этап изменил другой сотрудник — сохраните свой текст")
            return False
        self.text = text or ""
        self.spans = clamp_spans(spans, u16len(self.text))
        self.saved = self.text
        self.stage["text_description"] = self.text
        self.stage["spans"] = list(self.spans)
        cur = self.input.textCursor().position()
        self._remote = True
        self.input.setPlainText(self.text)
        self._remote = False
        c = self.input.textCursor()
        c.setPosition(min(cur, len(self.input.toPlainText())))
        self.input.setTextCursor(c)
        self.paint()
        self.status("info", "Обновлено другим сотрудником")
        return True


def desc_view(stage_or_text, spans=None, empty_text="Описания пока нет."):
    """Только чтение: текст с чертой авторов и ссылками."""
    view = QTextBrowser()
    view.setOpenExternalLinks(True)
    view.setFont(font(0.875))
    view.setStyleSheet(f"QTextBrowser {{ background: transparent; border: none; color: {theme.TEXT_VALUE}; }}")
    text = stage_or_text if isinstance(stage_or_text, str) else (stage_or_text.get("text_description") or "")
    if spans is None and not isinstance(stage_or_text, str):
        spans = stage_or_text.get("spans")
    if not text.strip():
        view.setHtml("<span style='color:%s'>%s</span>" % (theme.TEXT_MUTED, html.escape(empty_text)))
        return view
    view.setPlainText(text)
    doc = view.document()
    for sp in clamp_spans(spans, u16len(text)):
        c = QTextCursor(doc)
        c.setPosition(sp["start"])
        c.setPosition(min(sp["end"], doc.characterCount() - 1), QTextCursor.KeepAnchor)
        name = full_name(sp.get("author")) if sp.get("author") else "Автор удалён"
        f = _underline(None, theme.avatar_color(name))
        f.setToolTip(name)
        c.mergeCharFormat(f)
    for m in URL_RE.finditer(text):
        start = u16len(text[:m.start()])
        c = QTextCursor(doc)
        c.setPosition(start)
        c.setPosition(start + u16len(m.group(1)), QTextCursor.KeepAnchor)
        f = QTextCharFormat()
        f.setAnchor(True)
        f.setAnchorHref(m.group(1))
        f.setForeground(QColor(theme.INFO))
        c.mergeCharFormat(f)
    return view


# ══════════════════════════════════════════════════════════════════════════
# Окно задачи
# ══════════════════════════════════════════════════════════════════════════

class TaskView(Section):
    title = "Задача"
    key = "task"

    def __init__(self, shell, task_id, read_only_text=None, back=None, parent=None):
        super().__init__(shell, parent)
        self.task_id = int(task_id)
        self.back_route = back
        self.task = None
        self.stage = None
        self.stage_number = None
        self.scope = "desc"
        self.file_category = "documents"
        self.editor = None
        self.block = None
        self.completion = None
        self.can_decide = False
        self._reload = QTimer(self)
        self._reload.setSingleShot(True)
        self._reload.timeout.connect(self._refresh_from_event)
        self._pending_kind = ""
        self.root.addWidget(loading("Открываем задачу…", big=True), 1)
        jobs.run(self._load_all, done=self._loaded, failed=self._load_failed, owner=self)

    def go_back(self):
        """Куда вернуться: в проект, если задачу открыли из него (как kov.taskBack на сайте)."""
        b = self.back_route
        if isinstance(b, (list, tuple)) and b:
            self.shell.go(b[0], **(dict(b[1]) if len(b) > 1 else {}))
        else:
            self.shell.go("tasks")

    # ── данные ───────────────────────────────────────────────────────────────

    def _load_all(self):
        task = client.get("tasks/%d" % self.task_id)
        try:
            comp = client.get("tasks/%d/completion" % self.task_id) or {}
        except Exception:
            comp = {}
        try:
            store.employees()
        except Exception:
            pass
        store.load_photo_ids()
        return task, comp

    def _loaded(self, res):
        task, comp = res
        self.task = task
        self.completion = comp.get("request")
        self.can_decide = bool(comp.get("can_decide"))
        rights.set_read_only(bool(task.get("project_archived")), "Проект этой задачи завершён — задача только для просмотра")
        self.render()
        n = self.openable_stage()
        if n:
            self.select_stage(n)
        else:
            self._no_own_stage()

    def _load_failed(self, e):
        clear_layout(self.root)
        self.root.addWidget(empty("Не удалось открыть задачу: " + human_error(e)), 1)
        back = Button("К списку задач", "ghost", "arrow_back")
        back.clicked.connect(self.go_back)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(back)
        row.addStretch(1)
        self.root.addLayout(row)

    def stages(self):
        return sorted(self.task.get("stage_details") or [], key=lambda s: s.get("stage_number") or 0)

    def current_number(self):
        try:
            n = int(self.task.get("state"))
            if n > 0:
                return n
        except (TypeError, ValueError):
            pass
        st = self.stages()
        nxt = next((s for s in st if not s.get("is_completed")), None)
        if nxt:
            return nxt.get("stage_number")
        return st[-1].get("stage_number") if st else None

    def openable_stage(self):
        cur = self.current_number()
        mine = [s for s in self.stages() if rights.can_open_stage(s)]
        if not mine:
            return None
        pick = next((s for s in mine if s.get("stage_number") == cur), None) or \
            next((s for s in mine if not s.get("is_completed") and (s.get("stage_number") or 0) >= (cur or 0)), None) or \
            next((s for s in mine if not s.get("is_completed")), None) or mine[0]
        return pick.get("stage_number")

    def archived(self):
        return (self.task or {}).get("activity") == "Архивная"

    def _no_own_stage(self):
        if getattr(self, "zone_holder", None) is None:
            return
        clear_layout(self.zone_holder)
        self.zone_holder.addWidget(empty("Этапы этой задачи ведут другие отделы — их содержимое закрыто."), 1)

    # ── отрисовка ────────────────────────────────────────────────────────────

    def render(self):
        t = self.task
        if self.editor is not None and self.editor.has_unsaved():
            self.editor.flush()
        clear_layout(self.root)
        self.editor = None
        self.block = None
        self.title = t.get("name") or "Задача"
        self.shell.set_title(self.title)
        all_stages = self.stages()

        banner = self._completion_banner()
        if banner is not None:
            self.root.addWidget(banner)

        # лента этапов по центру
        strip_row = QHBoxLayout()
        strip_row.setContentsMargins(0, rem(0.2), 0, rem(0.2))
        strip_row.addStretch(1)
        self.strip = StageStrip(all_stages)
        self.strip.picked.connect(self.select_stage)
        for s in all_stages:
            b = next(x for x in self.strip.buttons if x.stage is s)
            if not rights.can_open_stage(s):
                self.strip.lock(s.get("stage_number"), "Этап ведёт отдел «%s» — он закрыт" % (s.get("department_name") or "—"))
            else:
                w, wh = stage_who(s), stage_when(s)
                b.setToolTip(("Завершён" + (": " + w if w else "") + (" · " + fmt_datetime(wh) if wh else ""))
                             if s.get("is_completed") else "В работе · отдел: " + (s.get("department_name") or "не указан"))
        if self.strip.buttons:
            area = scroll_area(self.strip, horizontal=True)
            area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            area.setFixedHeight(rem(3.3))
            area.setMaximumWidth(self.strip.width() + 2)
            area.setMinimumWidth(min(self.strip.width() + 2, rem(40)))
            strip_row.addWidget(area)
        strip_row.addStretch(1)
        self.root.addLayout(strip_row)

        # две колонки
        cols = QHBoxLayout()
        cols.setSpacing(rem(0.75))
        left_box = QWidget()
        left = QVBoxLayout(left_box)
        left.setContentsMargins(0, 0, rem(0.2), 0)
        left.setSpacing(rem(0.75))
        done_n = sum(1 for s in all_stages if s.get("is_completed"))
        progress = InfoCard("Прогресс", "flag")
        prow = QHBoxLayout()
        prow.setSpacing(rem(0.5))
        cur_idx = None
        try:
            cur_idx = next((i for i, s in enumerate(all_stages) if s.get("stage_number") == int(t.get("state"))), None)
        except (TypeError, ValueError):
            cur_idx = None
        prow.addWidget(StageDots([bool(s.get("is_completed")) for s in all_stages], cur_idx))
        prow.addWidget(muted("Завершено %d из %d" % (done_n, len(all_stages)), 0.875))
        prow.addStretch(1)
        progress.body.addLayout(prow)
        progress.body.addWidget(_ProgressLine(done_n / float(len(all_stages)) if all_stages else 0.0))
        left.addWidget(progress)

        about = InfoCard("О задаче", "id_card")
        d_state = deadline_state(t.get("deadline"), not self.archived())
        project = (t.get("project") or {}).get("name") if isinstance(t.get("project"), dict) else None
        rows = [("Название", t.get("name")), ("Заказчик", t.get("customer_organization")), ("Проект", project),
                ("Дата окончания", fmt_date(t.get("deadline")) if t.get("deadline") else "Не указана")]
        if self.archived():
            rows.append(("Архивирована", fmt_datetime(archived_when(t)) if archived_when(t) else "—"))
            rows.append(("Кто архивировал", archived_who(t) or "Неизвестный"))
        for k, v in rows:
            if v in (None, ""):
                continue
            r = InfoRow(k, str(v))
            if k == "Дата окончания" and d_state:
                r.set_value(str(v), theme.CRITICAL if d_state == "overdue" else theme.WARNING_TEXT)
                r.value.setToolTip("Дата окончания просрочена" if d_state == "overdue" else "До даты окончания меньше суток")
            if k == "Проект" and t.get("project_id"):
                r.value.setText("<a href='#' style='color:%s; text-decoration: underline dotted;'>%s</a>"
                                % (theme.TEXT_VALUE, html.escape(str(v))))
                r.value.setTextInteractionFlags(Qt.TextBrowserInteraction)
                r.value.setToolTip("Открыть проект")
                r.value.linkActivated.connect(lambda _l, pid=t.get("project_id"): self.shell.go("project", project_id=pid))
            about.body.addWidget(r)
        left.addWidget(about)

        responsible = t.get("responsible_persons") or []
        assigned = t.get("assigned_employees") or []

        def show_all():
            people_dialog(self, "Участники задачи", [("Ответственные", responsible), ("Исполнители", assigned)], self.shell)
        people = InfoCard("Участники", "users", "всего: %d" % (len(responsible) + len(assigned)))
        people.body.addWidget(field("Ответственные (%d)" % len(responsible), ClampedPeople(responsible, 2, show_all)))
        people.body.addWidget(field("Исполнители (%d)" % len(assigned), ClampedPeople(assigned, 2, show_all)))
        all_btn = Button("Показать всех участников", "ghost", "users")
        all_btn.clicked.connect(show_all)
        people.body.addWidget(all_btn)
        left.addWidget(people)

        desc = InfoCard("Описание задачи", "note")
        desc.body.addWidget(desc_view(t.get("description") or "", [], "Описание отсутствует."), 1)
        desc.setMinimumHeight(rem(7))
        left.addWidget(desc, 1)
        left_area = scroll_area(left_box)
        cols.addWidget(left_area, 2)

        # правая колонка: одна зона, три вкладки
        zone = Card(name="workZone")
        zone.setMinimumHeight(rem(20))
        cap = QHBoxLayout()
        cap.setSpacing(rem(0.5))
        cap.addWidget(IconBadge("folder"))
        self.zone_title = label("ЭТАП И ФАЙЛЫ", 0.72, theme.TEXT_LABEL, bold=True)
        self.zone_title.setFont(font(0.72, bold=True, spacing=0.08))
        cap.addWidget(self.zone_title)
        cap.addStretch(1)
        self.zone_status = Chip("", "default", bold=True)
        self.zone_status.hide()
        cap.addWidget(self.zone_status)
        zone.body.addLayout(cap)
        self.zone_switch = Segmented([("desc", "Описание этапа", "note"), ("task", "Файлы задачи", "folder"),
                                      ("stage", "Файлы этапа", "list")], self.scope)
        self.zone_switch.changed.connect(self._switch_scope)
        zone.body.addWidget(self.zone_switch)
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")
        self.zone_holder = QVBoxLayout(holder)
        self.zone_holder.setContentsMargins(0, 0, 0, 0)
        self.zone_holder.setSpacing(rem(0.5))
        zone.body.addWidget(holder, 1)
        cols.addWidget(zone, 3)
        self.root.addLayout(cols, 1)

        # нижняя строка действий
        bar = Card(name="stageActions", padding=(0.6, 0.9))
        bl = QHBoxLayout()
        bl.setSpacing(rem(0.6))
        self.stage_info = label("", 0.85, theme.TEXT_LABEL)
        bl.addWidget(self.stage_info)
        bl.addStretch(1)
        self.stage_btns = QHBoxLayout()
        self.stage_btns.setSpacing(rem(0.5))
        bl.addLayout(self.stage_btns)
        self.task_btns = QHBoxLayout()
        self.task_btns.setSpacing(rem(0.5))
        bl.addLayout(self.task_btns)
        bar.body.addLayout(bl)
        self.root.addWidget(bar)
        self._task_buttons()
        self.render_zone()
        if self.stage_number:
            self.strip.mark(self.stage_number)

    def _completion_banner(self):
        req = self.completion
        if not req or req.get("status") != "pending":
            return None
        mine = self.can_decide
        box = QFrame()
        box.setObjectName("completionBar")
        bg, bd, fg = (theme.INFO_SOFT, theme.INFO, theme.INFO) if mine else (theme.WARNING_SOFT, theme.WARNING, theme.WARNING_TEXT)
        box.setStyleSheet(f"QFrame#completionBar {{ background: {bg}; border: {border()}px solid {bd}; border-radius: {rem(0.75)}px; }}")
        lay = QHBoxLayout(box)
        lay.setContentsMargins(rem(0.8), rem(0.6), rem(0.8), rem(0.6))
        lay.setSpacing(rem(0.6))
        ic = QLabel()
        ic.setPixmap(icons.pixmap("clock", rem(1.2), fg))
        lay.addWidget(ic)
        txt = QVBoxLayout()
        txt.setSpacing(rem(0.1))
        txt.addWidget(label("Задача отправлена на завершение", 0.875, theme.TEXT_VALUE, bold=True))
        txt.addWidget(label("%s · %s" % (req.get("requested_by_name") or "", fmt_datetime(req.get("requested_at"))),
                            0.78, theme.TEXT_LABEL))
        lay.addLayout(txt, 1)
        if mine:
            ok = Button("Подтвердить", "primary", "check")
            ok.clicked.connect(lambda: self._decide("approve", "Задача завершена"))
            no = Button("Отклонить", "ghost", "close")
            no.clicked.connect(lambda: self._decide("decline", "Завершение отклонено"))
            lay.addWidget(ok)
            lay.addWidget(no)
        return box

    def _clear_box(self, box):
        while box.count():
            w = box.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _task_buttons(self):
        box = self.task_btns
        self._clear_box(box)
        t = self.task
        hist = Button("", "ghost", "clock", "Кто и когда менял задачу")
        hist.clicked.connect(self.history)
        box.addWidget(hist)
        all_stages = self.stages()
        all_done = bool(all_stages) and all(s.get("is_completed") for s in all_stages)
        if not self.archived():
            last = all_stages[-1] if all_stages else None
            if all_done and len(all_stages) < 10:
                b = Button("Продолжить задачу", "primary", "plus", "Добавить следующий этап")
                b.clicked.connect(self.continue_task)
                gate(b, rights.can_extend_task(last),
                     "Продлить задачу может ответственный за последний этап, руководитель отдела или директор")
                box.addWidget(b)
            if all_done:
                boss = store.is_management()
                b = Button("Завершить задачу (Архив)" if boss else "Отправить на завершение", "primary",
                           "archive" if boss else "send",
                           "Перенести задачу в архив" if boss else "Директора подтвердят завершение")
                b.clicked.connect(self.complete_task)
                gate(b, rights.can_request_completion(last, t),
                     "Запросить завершение может руководитель отдела задачи, ответственный за последний этап или руководство")
                box.addWidget(b)
        else:
            who, when = archived_who(t), archived_when(t)
            chip = Chip("В архиве" + (" · " + fmt_datetime(when) if when else "") + (" · " + who if who else ""), "warning")
            chip.setToolTip("Задача перенесена в архив")
            box.addWidget(chip)
            if store.can_manage_work():
                b = Button("Вернуть в работу", "ghost", "refresh")
                b.clicked.connect(self.unarchive)
                gate(b, rights.can_manage_task(t),
                     "Вернуть задачу в работу может руководство или руководитель отдела, к которому относятся все её этапы")
                box.addWidget(b)

    def _switch_scope(self, key):
        if self.editor is not None and self.editor.has_unsaved():
            self.editor.flush()
        self.scope = key
        self.render_zone()

    def set_zone_status(self, kind, text):
        chip = self.zone_status
        if chip is None:
            return
        bg, fg = {"info": (theme.INFO_SOFT, theme.INFO), "success": (theme.SUCCESS_SOFT, theme.SUCCESS_TEXT),
                  "warning": (theme.WARNING_SOFT, theme.WARNING_TEXT)}.get(kind, (theme.CHIP_BG, theme.BTN_GHOST_TEXT))
        chip.setText(text.upper() if kind in ("success", "info") and len(text) < 20 else text)
        chip.setStyleSheet(f"background: {bg}; color: {fg}; border-radius: {rem(0.6)}px; padding: {rem(0.2)}px {rem(0.65)}px;")
        chip.setFont(font(0.72, bold=True, spacing=0.06))
        chip.show()

    def render_zone(self):
        s = self.stage
        b = self.zone_switch.button("stage")
        if b is not None:
            b.setText("Файлы этапа %s" % s.get("stage_number") if s else "Файлы этапа")
        self.editor = None
        self.block = None
        self.zone_status.hide()
        clear_layout(self.zone_holder)
        if self.scope != "task" and not s:
            self.zone_holder.addWidget(_zone_body(empty("Выберите этап выше")), 1)
            return
        if self.scope == "desc":
            self._render_description()
            return
        on_stage = self.scope == "stage"
        editable = on_stage and not self.archived() and not s.get("is_completed") and rights.can_edit_stage(s)
        if on_stage:
            hint = ("Задача в архиве — файлы этапа изменить нельзя" if self.archived() else
                    "Этап завершён — возобновите его, чтобы добавить файлы" if s.get("is_completed") else
                    "Файлы этапа добавляет ведущий его отдел")
        else:
            hint = "Задача в архиве — файлы задачи изменить нельзя" if self.archived() else "Проект завершён — файлы задачи изменить нельзя"
        me = client.user_id

        def own(f):
            return int(f.get("uploaded_by_id") or (f.get("uploaded_by") or {}).get("id") or 0) == int(me or 0)

        def can_delete(f):
            if on_stage:
                return editable or (not self.archived() and not s.get("is_completed") and own(f))
            return not self.archived() and (store.is_management() or rights.head_for_task(self.task) or own(f))

        def get_files():
            if on_stage:
                return [dict(f, file_kind="stage") for f in ((self.stage or {}).get("files") or [])]
            return [dict(f, file_kind="task") for f in (self.task.get("task_files") or []) if not f.get("stage_detail_id")]

        def uploaded(created):
            if on_stage and self.stage is not None:
                self.stage["files"] = list(created) + list(self.stage.get("files") or [])
            elif not on_stage:
                self.task["task_files"] = list(created) + list(self.task.get("task_files") or [])

        def changed_cat(k):
            self.file_category = k
        block = kfiles.CategoryBlock(
            # файлы задачи — всем, кто видит задачу; в архиве и в завершённом проекте — никому
            get_files, can_upload=editable if on_stage else (not self.archived() and not rights.read_only()),
            can_delete=can_delete, upload_path="upload",
            upload_fields={"stage_detail_id": s["id"]} if on_stage else {"task_id": self.task_id},
            on_changed=lambda: self.reload_files(on_stage), on_uploaded=uploaded,
            initial_category=self.file_category, upload_hint=hint)
        block.category_changed.connect(changed_cat)
        self.block = block
        self.zone_holder.addWidget(_zone_body(block, fit=True), 1)

    def _render_description(self):
        s = self.stage
        editable = not self.archived() and not s.get("is_completed") and rights.can_edit_stage(s)
        if editable:
            self.editor = DescEditor(self, s, self.set_zone_status)
            body = _zone_body(self.editor)
        else:
            self.editor = None
            body = _zone_body(desc_view(s))
        self.zone_holder.addWidget(body, 1)
        foot = QVBoxLayout()
        foot.setSpacing(rem(0.4))
        legend = self._authors_legend(s)
        if legend is not None:
            foot.addWidget(legend)
        row = QHBoxLayout()
        row.setSpacing(rem(0.45))
        row.addWidget(muted("Участники этапа:", 0.8), 0, Qt.AlignVCenter)
        parts = s.get("participants") or []
        row.addWidget(ClampedPeople(parts, 1, lambda: people_dialog(
            self, "Участники этапа %s" % s.get("stage_number"), [("Участники этапа", parts)], self.shell)), 1)
        if s.get("i_lead") and not s.get("is_completed"):
            b = Button("Изменить", "ghost", "edit", "Изменить участников этапа", compact=True)
            b.clicked.connect(lambda: self.roster_dialog(s))
            row.addWidget(b, 0, Qt.AlignVCenter)
        foot.addLayout(row)
        self.zone_holder.addLayout(foot)

    def _authors_legend(self, s):
        names = {}
        for sp in s.get("spans") or []:
            if sp.get("author"):
                n = full_name(sp["author"])
                names[n] = theme.avatar_color(n)
        if not names:
            return None
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(rem(0.35))
        lay.addWidget(muted("Писали:", 0.8))
        for n, c in names.items():
            chip = QLabel("<span style='color:%s'>●</span>&nbsp;%s" % (c, html.escape(n)))
            chip.setFont(font(0.75))
            chip.setStyleSheet(f"background: {theme.CHIP_BG}; color: {theme.BTN_GHOST_TEXT}; border-radius: {rem(0.6)}px;"
                               f" padding: {rem(0.2)}px {rem(0.65)}px;")
            lay.addWidget(chip)
        lay.addStretch(1)
        return w

    # ── этап ─────────────────────────────────────────────────────────────────

    def select_stage(self, number):
        meta = next((s for s in self.stages() if s.get("stage_number") == number), None)
        if meta is None:
            return
        if not rights.can_open_stage(meta):
            toast(self, "Этап %s ведёт отдел «%s» — он закрыт" % (number, meta.get("department_name") or "—"), "warning")
            return
        if self.editor is not None and self.editor.has_unsaved():
            self.editor.flush()
        self.stage_number = number
        self.strip.mark(number)
        clear_layout(self.zone_holder)
        self.zone_holder.addWidget(loading("Загружаем этап…", big=True), 1)

        def done(stage):
            if self.stage_number != number:
                return
            self.stage = stage
            self.render_stage()

        def failed(e):
            clear_layout(self.zone_holder)
            self.zone_holder.addWidget(empty("Этап не загружен: " + human_error(e)), 1)
        jobs.run(client.get, "tasks/%d/stages/%s" % (self.task_id, meta["id"]), done=done, failed=failed, owner=self)

    def render_stage(self):
        s = self.stage
        if not s:
            return
        who, when = stage_who(s), stage_when(s)
        done_text = "Этап %s завершён" % s.get("stage_number") + (": " + who if who else "") + \
            (" · " + fmt_datetime(when) if when else "")
        work_text = "Этап %s в работе · отдел: %s" % (s.get("stage_number"), s.get("department_name") or "не указан")
        self.zone_title.setText(("Этап %s · %s" % (s.get("stage_number"), s.get("department_name") or "отдел не указан")).upper())
        self.stage_info.setText(done_text if s.get("is_completed") else work_text)
        self._clear_box(self.stage_btns)
        if not self.archived() and rights.can_complete_stage(s):
            if not s.get("is_completed"):
                b = Button("Завершить этап", "primary", "check")
                b.clicked.connect(self.complete_stage)
            else:
                b = Button("Возобновить этап", "ghost", "refresh")
                b.clicked.connect(self.resume_stage)
            self.stage_btns.addWidget(b)
        self.render_zone()

    # ── действия этапа ───────────────────────────────────────────────────────

    def _notify(self, message, self_message):
        t = self.task or {}
        ids = []
        for p in (t.get("responsible_persons") or []) + (t.get("assigned_employees") or []) + \
                ((self.stage or {}).get("participants") or []):
            ids.append(p.get("id"))
        for s in t.get("stage_details") or []:
            for p in s.get("participants") or []:
                ids.append(p.get("id"))
        ids += [t.get("creator_id"), client.user_id]
        jobs.run(notify.send, ids, message, "task_view", self.task_id, self_message,
                 failed=lambda e: toast(self, "Уведомление не отправлено: " + human_error(e), "error"))

    def complete_stage(self):
        s = self.stage
        text_now = self.editor.text if self.editor is not None else (s.get("text_description") or "")
        has = text_now.strip() or (s.get("files") or [])
        msg = "Завершить этап %s?" % s.get("stage_number") if has else "У этапа нет ни описания, ни файлов. Всё равно завершить?"
        if not confirm(self, "Завершение этапа", msg, "Завершить", glyph="check"):
            return
        if self.editor is not None and self.editor.has_unsaved():
            self.editor.flush_sync()

        def ok(_):
            toast(self, "Этап завершён", "success")
            name = self.task.get("name") or ""
            self._notify("Этап %s задачи «%s» завершён" % (s.get("stage_number"), name),
                         "Вы завершили этап %s задачи «%s»" % (s.get("stage_number"), name))
            self.refresh_task(stage_too=True)
        jobs.run(client.post, "tasks/%d/stages/%s/complete" % (self.task_id, s["id"]), done=ok,
                 failed=lambda e: toast(self, "Не удалось завершить: " + human_error(e), "error"), owner=self)

    def resume_stage(self):
        s = self.stage
        if not confirm(self, "Возобновление этапа", "Вернуть этап %s в работу?" % s.get("stage_number"), "Возобновить",
                       glyph="refresh"):
            return

        def ok(_):
            toast(self, "Этап возобновлён", "success")
            name = self.task.get("name") or ""
            self._notify("Этап %s задачи «%s» возобновлён" % (s.get("stage_number"), name),
                         "Вы возобновили этап %s задачи «%s»" % (s.get("stage_number"), name))
            self.refresh_task(stage_too=True)
        jobs.run(client.post, "tasks/%d/stages/%s/resume" % (self.task_id, s["id"]), done=ok,
                 failed=lambda e: toast(self, "Не удалось возобновить: " + human_error(e), "error"), owner=self)

    def roster_dialog(self, s):
        dep = s.get("department_name") or ""
        staff = [e for e in store.visible(store._employees) if e.get("department") == dep] if dep else []
        chosen = set(p.get("id") for p in s.get("participants") or [])
        leads = set(s.get("leads") or [])
        me = client.user_id
        m = Modal(self, "Участники этапа %s" % s.get("stage_number"), "users", width_rem=32)
        m.body.addWidget(label("Отдел этапа: " + (dep or "—"), 0.8, theme.TEXT_MUTED))
        warn = label("", 0.8, theme.TEXT_MUTED)
        m.body.addWidget(warn)
        holder = QWidget()
        lst = QVBoxLayout(holder)
        lst.setContentsMargins(0, 0, 0, 0)
        lst.setSpacing(rem(0.35))
        area = scroll_area(holder)
        area.setMinimumHeight(rem(12))
        area.setMaximumHeight(rem(17))
        m.body.addWidget(area)

        def paint():
            others_lead = any(i != me and (i in leads or not leads) for i in chosen)
            bad = me not in chosen and not others_lead
            warn.setText("Себя снять нельзя, пока в составе нет другого ответственного" if bad else "Выбрано: %d" % len(chosen))
            warn.setStyleSheet(f"color: {theme.WARNING_TEXT if bad else theme.TEXT_MUTED}; background: transparent;"
                               f" font-weight: {'600' if bad else '400'};")
        for p in staff:
            sub = " · ".join(x for x in ("ответственный" if p.get("id") in leads else None, p.get("position")) if x)
            row = LeadRow(p, sub, p.get("id") in chosen)

            def toggled(on, pid=p.get("id")):
                if on:
                    chosen.add(pid)
                else:
                    chosen.discard(pid)
                paint()
            row.toggled.connect(toggled)
            lst.addWidget(row)
        if not staff:
            lst.addWidget(muted("В отделе этапа нет сотрудников", 0.85))
        lst.addStretch(1)
        paint()

        def save(_m):
            try:
                client.put("stages/%s/participants" % s["id"], json_body=sorted(chosen))
            except Exception as e:
                toast(self, human_error(e), "error")
                return False
            toast(self, "Состав этапа изменён", "success")
            QTimer.singleShot(0, lambda: self.refresh_task(stage_too=True))
            return True
        m.add_button("Отмена", "ghost")
        m.add_button("Сохранить", "primary", "check", save)
        m.exec()

    # ── действия задачи ──────────────────────────────────────────────────────

    def continue_task(self):
        def load():
            store.lists()
            return list(store.departments or [])
        jobs.run(load, done=self._continue_dialog, failed=lambda e: self._continue_dialog([]), owner=self)

    def _continue_dialog(self, from_server):
        deps = [d for d in (from_server or []) if d] or ["Монтажный отдел", "Бухгалтерия", "Отдел ТО", "Договорной отдел", "Руководство"]
        deps = [d for d in deps if d not in ("Администрация", "Склад")]
        nxt = len(self.stages()) + 1
        m = Modal(self, "Продолжить задачу — этап %d" % nxt, "plus", width_rem=36)
        dep = Select([(d, d) for d in deps])
        m.body.addWidget(field("Ответственный отдел", dep))
        hint = label("", 0.8, theme.TEXT_MUTED, wrap=True)
        holder = QWidget()
        lst = QVBoxLayout(holder)
        lst.setContentsMargins(0, 0, 0, 0)
        lst.setSpacing(rem(0.35))
        area = scroll_area(holder)
        area.setMinimumHeight(rem(10))
        area.setMaximumHeight(rem(17))
        pick = QWidget()
        pl = QVBoxLayout(pick)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(rem(0.5))
        pl.addWidget(hint)
        pl.addWidget(area)
        m.body.addWidget(field("Ответственный за этап", pick))
        message = QPlainTextEdit()
        message.setPlaceholderText("Сообщение нового этапа (необязательно)")
        message.setFont(font(0.875))
        message.setStyleSheet(text_qss())
        message.setFixedHeight(rem(5.5))
        m.body.addWidget(field("Описание этапа", message))
        state = {"people": [], "heads": False, "chosen": set()}

        def paint_hint():
            n = len(state["chosen"])
            if n == 0:
                hint.setText(("Никого не выбрали — этап поведут все " + ("руководители отдела" if state["heads"] else
                              "сотрудники отдела") + ", в состав войдёт весь отдел") if state["people"]
                             else "В этом отделе некого назначить")
            elif n == 1:
                hint.setText("Один ответственный — этап уйдёт только ему, без исполнителей")
            else:
                hint.setText("Ответственных %d — в состав войдёт и весь отдел" % n)

        def fill():
            clear_layout(lst)
            for p in state["people"]:
                sub = " · ".join(x for x in ("руководитель отдела" if p.get("is_head") else None, p.get("position")) if x)
                row = LeadRow(p, sub, p.get("id") in state["chosen"])

                def toggled(on, pid=p.get("id")):
                    (state["chosen"].add if on else state["chosen"].discard)(pid)
                    paint_hint()
                row.toggled.connect(toggled)
                lst.addWidget(row)
            lst.addStretch(1)
            paint_hint()

        def load(department):
            clear_layout(lst)
            lst.addWidget(loading("Загружаем…"))
            state["chosen"] = set()

            def done(data):
                state["people"] = (data or {}).get("people") or []
                state["heads"] = bool((data or {}).get("heads_exist"))
                fill()

            def bad(_e):
                state["people"], state["heads"] = [], False
                fill()
            jobs.run(client.get, "stage-leads", query={"department": department}, done=done, failed=bad, owner=m)
        dep.currentIndexChanged.connect(lambda _i: load(dep.value()))
        load(deps[0])

        def add(_m):
            name = self.task.get("name") or ""
            department = dep.value()
            try:
                stage = client.post("tasks/%d/stages" % self.task_id, json_body={
                    "department_name": department, "text_description": message.toPlainText().strip(),
                    "responsible_ids": sorted(state["chosen"])})
            except Exception as e:
                toast(self, "Не удалось: " + human_error(e), "error")
                return False
            ids = [p.get("id") for p in (stage or {}).get("participants") or []]
            ids += [p.get("id") for p in (self.task.get("assigned_employees") or []) + (self.task.get("responsible_persons") or [])]
            ids += [self.task.get("creator_id"), client.user_id]
            jobs.run(notify.send, ids, "Задача «%s» продолжена: этап %d (%s)" % (name, nxt, department), "task_view",
                     self.task_id, "Вы продолжили задачу «%s»: этап %d (%s)" % (name, nxt, department))
            toast(self, "Этап добавлен", "success")
            QTimer.singleShot(0, lambda: self.refresh_task(select=nxt))
            return True
        m.add_button("Отмена", "ghost")
        m.add_button("Добавить этап", "primary", "check", add)
        m.exec()

    def complete_task(self):
        boss = store.is_management()
        if not confirm(self, "Завершение задачи",
                       "Завершить задачу и перенести её в архив?" if boss else
                       "Отправить задачу на подтверждение директорам? В архив она уйдёт после их согласия.",
                       "В архив" if boss else "Отправить", glyph="archive" if boss else "send"):
            return

        def ok(res):
            self.completion = (res or {}).get("request")
            toast(self, "Задача перенесена в архив" if (res or {}).get("status") == "archived" else "Заявка отправлена директорам",
                  "success")
            self.refresh_task(reload_completion=True)
        jobs.run(client.post, "tasks/%d/completion" % self.task_id, done=ok,
                 failed=lambda e: toast(self, "Не удалось: " + human_error(e), "error"), owner=self)

    def _decide(self, path, ok_text):
        comment = ""
        if path == "decline":
            m = Modal(self, "Отклонить завершение", "question", width_rem=30)
            box = QPlainTextEdit()
            box.setPlaceholderText("Причина (необязательно)")
            box.setFont(font(0.875))
            box.setStyleSheet(text_qss())
            box.setFixedHeight(rem(5.5))
            m.body.addWidget(field("Что доделать", box))
            m.add_button("Отмена", "ghost")
            m.add_button("Отклонить", "primary", "close", lambda _m: True)
            if m.exec() != Modal.Accepted:
                return
            comment = box.toPlainText().strip()

        def ok(_):
            toast(self, ok_text, "success")
            self.refresh_task(reload_completion=True)
        jobs.run(client.post, "tasks/%d/completion/%s" % (self.task_id, path), json_body={"comment": comment},
                 done=ok, failed=lambda e: toast(self, human_error(e), "error"), owner=self)

    def unarchive(self):
        if not confirm(self, "Возврат в работу", "Вернуть задачу в работу?", "Вернуть", glyph="refresh"):
            return
        if self.task and self.task.get("project_archived"):
            toast(self, "Задача относится к завершённому проекту — сначала верните проект в работу", "warning")
            return

        def ok(_):
            toast(self, "Задача возвращена в работу", "success")
            name = self.task.get("name") or ""
            self._notify("Задача «%s» возвращена в работу" % name, "Вы вернули задачу «%s» в работу" % name)
            self.refresh_task(stage_too=True)
        jobs.run(client.post, "tasks/%d/unarchive" % self.task_id, done=ok,
                 failed=lambda e: toast(self, "Не удалось: " + human_error(e), "error"), owner=self)

    def history(self):
        from kov.journal import open_journal

        def load():
            rows = client.get("tasks/%d/history" % self.task_id) or []
            edits = []
            for r in rows:
                old = r.get("old")
                edits.append({"kind": "edit", "action": "changed", "glyph": HISTORY_GLYPH.get(r.get("field")),
                              "who": r.get("who"), "at": r.get("at"), "title": r.get("field_title"),
                              "text": ("%s → %s" % (old, r.get("new"))) if old and old != "—" else r.get("new")})
            return {"issues": [], "edits": edits}
        open_journal(self, "Журнал задачи", (self.task or {}).get("name") or "", load)

    # ── обновление ───────────────────────────────────────────────────────────

    def refresh_task(self, stage_too=False, select=None, reload_completion=False):
        keep = self.stage_number

        def load():
            task = client.get("tasks/%d" % self.task_id)
            comp = None
            if reload_completion:
                try:
                    comp = client.get("tasks/%d/completion" % self.task_id) or {}
                except Exception:
                    comp = {}
            return task, comp

        def done(res):
            task, comp = res
            self.task = task
            if comp is not None:
                self.completion = comp.get("request")
                self.can_decide = bool(comp.get("can_decide"))
            rights.set_read_only(bool(task.get("project_archived")), "Проект этой задачи завершён — задача только для просмотра")
            self.render()
            target = select or keep
            if select:
                nx = next((s for s in self.stages() if s.get("stage_number") == select), None)
                target = select if nx is not None and rights.can_open_stage(nx) else (keep or select)
            if target:
                self.select_stage(target)
            elif self.openable_stage():
                self.select_stage(self.openable_stage())
            else:
                self._no_own_stage()
        jobs.run(load, done=done, failed=lambda e: None, owner=self)

    def reload_files(self, on_stage):
        def load():
            if on_stage and self.stage is not None:
                return "stage", client.get("tasks/%d/stages/%s" % (self.task_id, self.stage["id"]))
            return "task", client.get("tasks/%d" % self.task_id)

        def done(res):
            kind, data = res
            if kind == "stage":
                self.stage = data
            else:
                self.task = data
            if self.block is not None:
                self.block.render()
            else:
                self.render_zone()
        jobs.run(load, done=done, failed=lambda e: None, owner=self)

    def _refresh_from_event(self):
        kind = self._pending_kind
        self.refresh_task(stage_too=kind.startswith("stage."))

    def on_event(self, evt):
        kind = evt.get("type") or ""
        if evt.get("task_id") is not None:
            try:
                if int(evt.get("task_id")) != self.task_id:
                    return
            except (TypeError, ValueError):
                return
        if self.task is None:
            return
        if kind == "task.deleted":
            toast(self, "Задача удалена другим пользователем", "warning")
            self.go_back()
            return
        mine = evt.get("sender_id") is not None and str(evt.get("sender_id")) == str(client.user_id)
        if kind == "stage.description_updated":
            if mine:
                return
            s = self.stage
            if not s or str(evt.get("stage_id")) != str(s.get("id")):
                return
            if self.editor is not None:
                self.editor.apply_remote(evt.get("description"), evt.get("spans"))
                return
            s["text_description"] = evt.get("description") or ""
            s["spans"] = evt.get("spans") or []
            if self.scope == "desc":
                self.render_zone()
            return
        if kind in ("task.completion_requested", "task.completion_declined", "task.archived"):
            self.refresh_task(reload_completion=True)
            if kind == "task.completion_declined" and evt.get("comment") and not mine:
                toast(self, "Завершение отклонено: " + str(evt.get("comment")), "warning")
            return
        if kind == "task.participants_updated":
            if not mine:
                self.refresh_task(stage_too=True)
            return
        if kind in ("stage.file_added", "stage.file_deleted"):
            if not mine and self.stage and str(evt.get("stage_id")) == str(self.stage.get("id")):
                self.reload_files(True)
            return
        if kind in ("task.file_added", "task.file_deleted"):
            if not mine:
                self.reload_files(False)
            return
        if kind == "task.files_changed":
            QTimer.singleShot(300, lambda: self.reload_files(self.stage is not None and self.scope == "stage"))
            return
        if kind.startswith("task.") or kind.startswith("stage."):
            if self.editor is not None and self.editor.has_unsaved():
                return
            self._pending_kind = kind
            self._reload.start(250)

    def on_reconnect(self):
        if self.scope in ("task", "stage"):
            self.reload_files(self.scope == "stage")

    def shutdown(self):
        if self.editor is not None and self.editor.has_unsaved():
            self.editor.flush_sync()
        rights.set_read_only(False)


def _zone_body(widget, fit=False):
    """Рабочая область с пунктирной рамкой (.zone-body)."""
    box = QFrame()
    box.setObjectName("zoneBody")
    box.setStyleSheet(f"QFrame#zoneBody {{ border: {max(1, rem(0.11))}px dashed {theme.BTN_GHOST_BORDER};"
                      f" border-radius: {rem(0.5)}px; background: {theme.SURFACE_3}; }}")
    lay = QVBoxLayout(box)
    pad = rem(0.5 if fit else 0.6)
    lay.setContentsMargins(pad, pad, pad, pad)
    lay.addWidget(widget, 1)
    return box


class _ProgressLine(QWidget):
    def __init__(self, value, parent=None):
        super().__init__(parent)
        self.value = max(0.0, min(1.0, value))
        self.setFixedHeight(rem(0.6))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect())
        rad = rem(0.4)
        path = QPainterPath()
        path.addRoundedRect(r, rad, rad)
        p.setClipPath(path)
        p.fillRect(r, QColor(theme.CHIP_BG))
        p.fillRect(QRectF(0, 0, r.width() * self.value, r.height()), QColor(theme.STAGE_DONE))
        p.end()
