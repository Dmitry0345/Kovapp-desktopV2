# -*- coding: utf-8 -*-
"""Файлы — как js/core/files.js веб-версии: категории, плитки «три в ряд»
с миниатюрами с сервера и подписью «кто прикрепил», фильтр по отправителю,
загрузка с подготовкой на компьютере и полосой хода, замена одноимённых,
перенос мышью, просмотр фото и видео, скачивание с ходом и открытие
документов программой компьютера.
"""

import os
import re
import tempfile
import threading
import time
import urllib.parse

from PySide6.QtCore import QObject, QPoint, QRect, QRectF, QSize, QStandardPaths, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFontMetrics, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QSizePolicy,
                             QToolButton, QVBoxLayout, QWidget)

import shiboken6

from general_functions import theme
from kov import icons, jobs
from kov.api import client, human_error
from kov.fmt import fmt_datetime, parse_date
from kov.media import thumbhash
from kov.ui import (Button, PickerSelect, ProgressLine, SearchInput, Segmented, border, clear_layout, empty, font,
                    label, rem, scroll_area, toast)

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".webp", ".svg")
VIDEO_EXT = (".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".mkv", ".ts")
DOC_EXT = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".txt")
MEDIA_FILTER = "Фото и видео (%s)" % " ".join("*" + e for e in IMAGE_EXT + VIDEO_EXT)
MAX_UPLOAD = 2 * 1024 * 1024 * 1024
CATEGORIES = [("documents", "Документы", "file"), ("images", "Изображения", "image"),
              ("videos", "Видео", "video"), ("others", "Другое", "folder")]
DOC_GLYPHS = [((".xls", ".xlsx", ".ods", ".csv"), "table"), ((".zip", ".rar", ".7z", ".tar", ".gz"), "archive"),
              ((".txt", ".md", ".log"), "list")]

ENDPOINTS = {
    "task": {"download": "task/files/{id}/download", "stream": "task/files/{id}/stream/{name}",
             "view": ("files/{id}/view", {"is_stage": "false"}), "thumb": "task/files/{id}/thumb",
             "del": "files/task/{id}"},
    "stage": {"download": "stages/files/{id}/download", "stream": "stages/files/{id}/stream/{name}",
              "view": ("files/{id}/view", {"is_stage": "true"}), "thumb": "stages/files/{id}/thumb",
              "del": "files/stage/{id}"},
    "project": {"download": "projects/files/{id}/download", "stream": "projects/files/{id}/stream/{name}",
                "view": "projects/files/{id}/view", "thumb": "projects/files/{id}/thumb",
                "del": "projects/files/{id}"},
}


def ext(name):
    m = re.search(r"\.[^./\\]+$", str(name or "").lower())
    return m.group(0) if m else ""


def is_image(name):
    return ext(name) in IMAGE_EXT


def is_video(name):
    return ext(name) in VIDEO_EXT


def is_media(name):
    return is_image(name) or is_video(name)


def categorize(name):
    e = ext(name)
    if e in IMAGE_EXT:
        return "images"
    if e in VIDEO_EXT:
        return "videos"
    if e in DOC_EXT:
        return "documents"
    return "others"


def file_name(f):
    return str((f or {}).get("filename") or f.get("file_name") or f.get("name") or f.get("original_name") or "")


def file_id(f):
    f = f or {}
    for k in ("id", "file_id", "task_file_id"):
        if f.get(k) is not None:
            return f.get(k)
    return None


def file_kind(f):
    return f.get("file_kind") or ("stage" if f.get("stage_detail_id") else "task")


def safe_name(name):
    try:
        return urllib.parse.unquote(str(name or ""))
    except Exception:
        return str(name or "")


def uploader_id(f):
    return f.get("uploaded_by_id") or ((f.get("uploaded_by") or {}).get("id"))


def uploader_name(f, short=False):
    person = f.get("uploaded_by")
    if not person:
        return ""
    first = person.get("first_name") or ""
    if short and first:
        first = first[0] + "."
    return ((person.get("last_name") or "") + " " + first).strip()


def fmt_size(size):
    if size is None:
        return ""
    size = float(size)
    if size < 1024:
        return "%d Б" % size
    if size < 1048576:
        return "%.0f КБ" % (size / 1024)
    if size < 1073741824:
        return "%.1f МБ" % (size / 1048576)
    return "%.2f ГБ" % (size / 1073741824)


def doc_glyph(name):
    e = ext(name)
    for exts, glyph in DOC_GLYPHS:
        if e in exts:
            return glyph
    return "file"


def endpoint(f, action):
    """(путь, параметры) адреса файла на сервере."""
    e = ENDPOINTS[file_kind(f)][action]
    query = {}
    if isinstance(e, tuple):
        e, query = e
    path = e.format(id=file_id(f), name=urllib.parse.quote(file_name(f)))
    if action in ("download", "view"):
        query = dict(query, ce="1")
    return path, query


def url(f, action):
    if f.get("src") and action in ("view", "stream", "download"):
        return f["src"]
    path, query = endpoint(f, action)
    return client.file_url(path, query)


# ══════════════════════════════════════════════════════════════════════════
# Миниатюры: общий загрузчик с памятью
# ══════════════════════════════════════════════════════════════════════════

class _Thumbs(QObject):
    ready = Signal(str, QImage)

    def __init__(self):
        super().__init__()
        self.cache = {}
        self.waiting = set()
        self.failed = set()

    def key(self, f):
        return "%s:%s" % (file_kind(f), file_id(f))

    def get(self, f):
        return self.cache.get(self.key(f))

    def request(self, f, side=320):
        key = self.key(f)
        if key in self.cache or key in self.waiting or key in self.failed:
            return
        self.waiting.add(key)
        video = is_video(file_name(f))

        def load():
            tries = [endpoint(f, "thumb")]
            if not video and is_image(file_name(f)):
                tries.append(endpoint(f, "view"))
            for path, query in tries:
                for attempt in range(2):
                    try:
                        res = client.request("GET", path, query=query, raw=True, timeout=(10, 60))
                        img = QImage()
                        if img.loadFromData(res.content) and not img.isNull():
                            if max(img.width(), img.height()) > side * 2:
                                img = img.scaled(side * 2, side * 2, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                            return img
                        break
                    except Exception as e:
                        if getattr(e, "status", None) in (401, 403) and attempt == 0:
                            client.file_token = None
                            continue
                        break
            return None

        def done(img):
            self.waiting.discard(key)
            if img is None:
                self.failed.add(key)
                return
            self.cache[key] = img
            if len(self.cache) > 600:
                self.cache.pop(next(iter(self.cache)))
            self.ready.emit(key, img)

        def failed(_e):
            self.waiting.discard(key)
            self.failed.add(key)
        jobs.run(load, done=done, failed=failed)


THUMBS = _Thumbs()


def _rounded(pm, w, h, radius):
    out = QPixmap(w, h)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, w, h), radius, radius)
    p.setClipPath(path)
    scaled = pm.scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    p.drawPixmap((w - scaled.width()) // 2, (h - scaled.height()) // 2, scaled)
    p.end()
    return out


class TileThumb(QToolButton):
    """Квадрат миниатюры: снимок с сервера, заглушка ThumbHash или значок."""

    def __init__(self, f, side_rem=5.4, parent=None):
        super().__init__(parent)
        self.f = f
        self._side = rem(side_rem)
        self.setFixedSize(self._side, self._side)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Открыть просмотр")
        self.setStyleSheet("QToolButton { border: none; background: transparent; }")
        name = file_name(f)
        self._media = is_media(name)
        self._video = is_video(name)
        self._doc_thumb = not self._media and (bool(f.get("thumb_hash")) or name.lower().endswith(".pdf"))
        self._img = None
        self._placeholder = thumbhash.to_qimage(f["thumb_hash"]) if f.get("thumb_hash") else None
        self._glyph = ("video" if self._video else "image") if self._media else doc_glyph(name)
        if self._media or self._doc_thumb:
            cached = THUMBS.get(f)
            if cached is not None:
                self._img = cached
            else:
                THUMBS.ready.connect(self._on_ready)
                THUMBS.request(f)

    def _on_ready(self, key, img):
        if key == THUMBS.key(self.f):
            self._img = img
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        r = rem(0.6)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, self.width(), self.height()), r, r)
        p.setClipPath(path)
        p.fillRect(self.rect(), QColor(theme.CHIP_BG))
        src = self._img if self._img is not None else self._placeholder
        if src is not None:
            pm = QPixmap.fromImage(src)
            scaled = pm.scaled(self.width(), self.height(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            p.drawPixmap((self.width() - scaled.width()) // 2, (self.height() - scaled.height()) // 2, scaled)
        if src is None or (self._img is None and not self._placeholder):
            g = rem(2.2)
            p.drawPixmap((self.width() - g) // 2, (self.height() - g) // 2,
                         icons.pixmap(self._glyph, g, theme.ACCENT_SOFT))
        if self._video:
            p.fillRect(self.rect(), QColor(38, 50, 56, 64))
            g = rem(1.8)
            p.drawPixmap((self.width() - g) // 2, (self.height() - g) // 2,
                         icons.pixmap("play", g, "#FFFFFF", 1.9, fill=True))
        if self.underMouse():
            p.fillRect(self.rect(), QColor(255, 255, 255, 30))
        p.end()


class FileTile(QFrame):
    open_requested = Signal(object)
    download_requested = Signal(object)
    delete_requested = Signal(object)

    def __init__(self, f, can_delete=False, parent=None):
        super().__init__(parent)
        self.f = f
        self.setObjectName("fileTile")
        self.setStyleSheet(f"QFrame#fileTile {{ background: {theme.CARD_BG}; border: {border()}px solid {theme.CARD_BORDER};"
                           f" border-radius: {rem(0.75)}px; }}")
        self.setMinimumWidth(rem(14))
        lay = QHBoxLayout(self)
        pad = rem(0.55)
        lay.setContentsMargins(pad, pad, pad, pad)
        lay.setSpacing(rem(0.6))
        self.thumb = TileThumb(f)
        self.thumb.clicked.connect(lambda: self.open_requested.emit(self.f))
        lay.addWidget(self.thumb, 0, Qt.AlignVCenter)
        info = QVBoxLayout()
        info.setSpacing(rem(0.15))
        info.setContentsMargins(0, 0, 0, 0)
        name = safe_name(file_name(f))
        self.name = QLabel()
        self.name.setFont(font(0.875, bold=True))
        self.name.setStyleSheet(f"color: {theme.TEXT_VALUE}; background: transparent;")
        self.name.setToolTip(name)
        self._full = name
        self.name.setMinimumWidth(rem(4))
        self.name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        info.addWidget(self.name)
        who = uploader_name(f)
        if who:
            info.addWidget(label(who, 0.78, theme.TEXT_LABEL))
        meta = " · ".join(x for x in (fmt_size(f.get("size")) if f.get("size") is not None else "",
                                      fmt_datetime(f.get("created_at"), "") if f.get("created_at") else "") if x)
        self.meta = label(meta, 0.74, theme.TEXT_MUTED)
        info.addWidget(self.meta)
        acts = QHBoxLayout()
        acts.setSpacing(rem(0.3))
        acts.setContentsMargins(0, rem(0.25), 0, 0)
        b1 = Button("", "ghost", "eye", "Открыть просмотр")
        b2 = Button("", "ghost", "download", "Скачать файл")
        for b in (b1, b2):
            b.setFixedSize(rem(2), rem(2))
        b1.clicked.connect(lambda: self.open_requested.emit(self.f))
        b2.clicked.connect(lambda: self.download_requested.emit(self.f))
        acts.addWidget(b1)
        acts.addWidget(b2)
        if can_delete:
            b3 = Button("", "danger", "trash", "Удалить файл")
            b3.setFixedSize(rem(2), rem(2))
            b3.clicked.connect(lambda: self.delete_requested.emit(self.f))
            acts.addWidget(b3)
        acts.addStretch(1)
        info.addLayout(acts)
        lay.addLayout(info, 1)
        self._elide()

    def _elide(self):
        fm = QFontMetrics(self.name.font())
        self.name.setText(fm.elidedText(self._full, Qt.ElideRight, max(rem(3), self.name.width())))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()


class FileGrid(QWidget):
    """Плитки три в ряд; при обновлении списка плитки переиспользуются."""

    open_requested = Signal(object)
    download_requested = Signal(object)
    delete_requested = Signal(object)

    def __init__(self, can_delete=None, empty_text="Файлов в этой категории пока нет", columns=3, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        self.can_delete = can_delete
        self.empty_text = empty_text
        self.columns = columns
        self.files = []
        self._tiles = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.inner = QWidget()
        self.inner.setStyleSheet("background: transparent;")
        self.grid = QGridLayout(self.inner)
        self.grid.setContentsMargins(0, 0, rem(0.2), 0)
        self.grid.setHorizontalSpacing(rem(0.6))
        self.grid.setVerticalSpacing(rem(0.6))
        self.area = scroll_area(self.inner)
        outer.addWidget(self.area)
        self.empty = empty(empty_text)
        self.empty.hide()
        outer.addWidget(self.empty, 1)

    @staticmethod
    def _sig(f):
        return "|".join(str(x) for x in (file_id(f), file_name(f), f.get("size"), f.get("created_at"),
                                          uploader_id(f)))

    def fill(self, files):
        self.files = list(files or [])
        while self.grid.count():
            self.grid.takeAt(0)
        if not self.files:
            for w, _ in self._tiles.values():
                w.setParent(None)
                w.deleteLater()
            self._tiles = {}
            self.area.hide()
            self.empty.setText(self.empty_text)
            self.empty.show()
            return
        self.empty.hide()
        self.area.show()
        seen = set()
        for i, f in enumerate(self.files):
            key = "%s:%s" % (file_kind(f), file_id(f))
            seen.add(key)
            rec = self._tiles.get(key)
            sig = self._sig(f)
            if rec is None or rec[1] != sig:
                if rec is not None:
                    rec[0].setParent(None)
                    rec[0].deleteLater()
                allowed = bool(self.can_delete(f)) if self.can_delete else False
                tile = FileTile(f, allowed)
                tile.open_requested.connect(self.open_requested.emit)
                tile.download_requested.connect(self.download_requested.emit)
                tile.delete_requested.connect(self.delete_requested.emit)
                rec = (tile, sig)
                self._tiles[key] = rec
            self.grid.addWidget(rec[0], i // self.columns, i % self.columns)
            rec[0].show()
        for key in list(self._tiles):
            if key not in seen:
                w = self._tiles.pop(key)[0]
                w.setParent(None)
                w.deleteLater()
        for c in range(self.columns):
            self.grid.setColumnStretch(c, 1)
        rows = (len(self.files) + self.columns - 1) // self.columns
        self.grid.setRowStretch(rows, 1)


# ══════════════════════════════════════════════════════════════════════════
# Скачивание и открытие
# ══════════════════════════════════════════════════════════════════════════

def downloads_dir():
    path = QStandardPaths.writableLocation(QStandardPaths.DownloadLocation) or os.path.expanduser("~")
    os.makedirs(path, exist_ok=True)
    return path


def unique_path(folder, name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", safe_name(name)).strip() or "file"
    base, extn = os.path.splitext(name)
    path = os.path.join(folder, name)
    k = 1
    while os.path.exists(path):
        path = os.path.join(folder, "%s (%d)%s" % (base, k, extn))
        k += 1
    return path


class _Download(QObject):
    progress = Signal(int, int)
    finished = Signal(str)
    failed = Signal(str)

    def __init__(self, path, query, dest):
        super().__init__()
        self.path, self.query, self.dest = path, query, dest
        self.stop = threading.Event()
        self.state = "running"

    def run(self):
        def work():
            tmp = self.dest + ".part"
            try:
                res = client.request("GET", self.path, query=self.query, stream=True, timeout=(10, 300))
                total = int(res.headers.get("content-length") or 0)
                with open(tmp, "wb") as fh:
                    for part in res.iter_content(256 * 1024):
                        if self.stop.is_set():
                            raise InterruptedError()
                        if part:
                            fh.write(part)
                            try:
                                raw = res.raw.tell()
                            except Exception:
                                raw = fh.tell()
                            self.progress.emit(int(raw), total)
                res.close()
                os.replace(tmp, self.dest)
                self.state = "done"
                self.finished.emit(self.dest)
            except InterruptedError:
                self.state = "cancelled"
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                self.failed.emit("Скачивание отменено")
            except Exception as e:
                self.state = "error"
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                self.failed.emit(human_error(e, "Не удалось скачать файл"))
        threading.Thread(target=work, daemon=True, name="kov-download").start()


class DownloadsPanel(QFrame):
    """Панель загрузок в правом нижнем углу окна: ход, «Открыть», «Папка» — как Kov.Downloads сайта.

    Скачанное убирается само через несколько секунд (пока указатель над панелью — ждёт),
    ошибки и отмены остаются на виду. Крестик убирает все завершённые насовсем: раньше он
    только прятал панель, и при следующем скачивании она возвращалась со всеми прежними."""

    KEEP_DONE_MS = 6000

    def __init__(self, window):
        super().__init__(window)
        self.setObjectName("downloads")
        self.setFixedWidth(rem(22))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(rem(0.8), rem(0.6), rem(0.6), rem(0.7))
        lay.setSpacing(rem(0.45))
        head = QHBoxLayout()
        self.head_label = label("Загрузки", 0.8, theme.TEXT_LABEL, bold=True)
        head.addWidget(self.head_label)
        head.addStretch(1)
        self.close_btn = QToolButton()
        self.close_btn.setIconSize(QSize(rem(0.9), rem(0.9)))
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setToolTip("Скрыть завершённые")
        self.close_btn.clicked.connect(self.clear_done)
        head.addWidget(self.close_btn)
        lay.addLayout(head)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(rem(0.4))
        lay.addLayout(self.rows)
        self.items = []
        self.restyle()
        self.hide()

    def restyle(self):
        self.setStyleSheet(f"QFrame#downloads {{ background: {theme.CARD_BG}; border: {border()}px solid {theme.CARD_BORDER};"
                           f" border-radius: {rem(0.75)}px; }}")
        self.close_btn.setIcon(icons.icon("close", rem(0.9), theme.TEXT_LABEL))
        self.close_btn.setStyleSheet("QToolButton { border: none; background: transparent; }")

    def place(self):
        win = self.parentWidget()
        if win is None:
            return
        self.adjustSize()
        self.move(win.width() - self.width() - rem(1), win.height() - self.height() - rem(1))
        self.raise_()

    def add(self, name, job):
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        rl = QVBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(rem(0.2))
        top = QHBoxLayout()
        fm = QFontMetrics(font(0.82, bold=True))
        nm = label(fm.elidedText(safe_name(name), Qt.ElideMiddle, rem(14)), 0.82, theme.TEXT_VALUE, bold=True)
        nm.setToolTip(safe_name(name))
        top.addWidget(nm, 1)
        stop = Button("", "ghost", "close", "Отменить")
        stop.setFixedSize(rem(1.8), rem(1.8))
        stop.clicked.connect(job.stop.set)
        top.addWidget(stop)
        rl.addLayout(top)
        bar = ProgressLine(height_rem=0.4, flow=False)
        rl.addWidget(bar)
        note = label("Скачивание…", 0.74, theme.TEXT_MUTED)
        rl.addWidget(note)
        acts = QHBoxLayout()
        acts.setSpacing(rem(0.35))
        open_b = Button("Открыть", "ghost", "eye", compact=True)
        folder_b = Button("Папка", "ghost", "folder", compact=True)
        acts.addWidget(open_b)
        acts.addWidget(folder_b)
        acts.addStretch(1)
        rl.addLayout(acts)
        open_b.hide()
        folder_b.hide()
        item = {"row": row, "job": job, "finished": False, "ok": False, "timer": None}
        self.rows.insertWidget(0, row)
        self.items.insert(0, item)
        # не больше пяти строк: лишними уходят самые старые из завершённых
        while len(self.items) > 5:
            old = next((x for x in reversed(self.items) if x["finished"]), None)
            if old is None:
                break
            self._remove(old, relayout=False)

        def alive():
            try:
                return item in self.items and shiboken6.isValid(note)
            except Exception:
                return False

        def prog(done, total):
            if not alive():
                return
            if total:
                bar.set_value(done / float(total))
                note.setText("%s из %s" % (fmt_size(done), fmt_size(total)))
            else:
                note.setText(fmt_size(done))

        def fin(path):
            if not alive():
                return
            item["finished"] = item["ok"] = True
            bar.set_value(1.0)
            note.setText("Готово · " + os.path.dirname(path))
            note.setToolTip(path)
            stop.hide()
            open_b.show()
            folder_b.show()
            open_b.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(path)))
            folder_b.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path))))
            self._schedule(item)
            self.place()

        def fail(text):
            if not alive():
                return
            item["finished"] = True             # ошибка и отмена остаются, пока их не уберут крестиком
            note.setText(text)
            note.setStyleSheet(f"color: {theme.DANGER_TEXT}; background: transparent;")
            stop.hide()
            self.place()
        job.progress.connect(prog)
        job.finished.connect(fin)
        job.failed.connect(fail)
        self.show()
        self.place()

    def _schedule(self, item, ms=None):
        t = QTimer(self)
        t.setSingleShot(True)
        t.timeout.connect(lambda: self._remove(item))
        item["timer"] = t
        if not self.underMouse():
            t.start(ms or self.KEEP_DONE_MS)

    def _remove(self, item, relayout=True):
        if item in self.items:
            self.items.remove(item)
        if item.get("timer") is not None:
            item["timer"].stop()
        try:
            item["row"].hide()
            item["row"].deleteLater()
        except RuntimeError:
            pass
        if not relayout:
            return
        if not self.items:
            self.hide()
        else:
            self.place()

    def clear_done(self):
        """Крестик: убрать все завершённые (и ошибки); идущие скачивания остаются на панели."""
        for item in [x for x in self.items if x["finished"]]:
            self._remove(item, relayout=False)
        if not self.items:
            self.hide()
        else:
            self.place()

    def enterEvent(self, e):
        for item in self.items:            # пока указатель над панелью, готовые не исчезают
            t = item.get("timer")
            if t is not None and t.isActive():
                t.stop()
        super().enterEvent(e)

    def leaveEvent(self, e):
        for item in self.items:
            t = item.get("timer")
            if item["ok"] and t is not None and not t.isActive():
                t.start(2500)
        super().leaveEvent(e)


_PANELS = {}
_ACTIVE = []


def _host_of(win):
    return win.centralWidget() if hasattr(win, "centralWidget") and win.centralWidget() is not None else win


def _panel_for(widget):
    win = widget.window() if widget is not None else None
    if win is None:
        return None
    p = _PANELS.get(id(win))
    try:
        if p is not None and (not shiboken6.isValid(p) or p.parentWidget() is not _host_of(win)):
            p = None
    except Exception:
        p = None
    if p is None:
        p = DownloadsPanel(_host_of(win))
        _PANELS[id(win)] = p
    return p


def take_panel(window):
    """Окно пересобирается (тема, размер): панель загрузок с идущими скачиваниями — в сторону."""
    p = _PANELS.pop(id(window), None)
    try:
        if p is None or not shiboken6.isValid(p):
            return None
        p.hide()
        p.setParent(None)
        return p
    except RuntimeError:
        return None


def give_panel(window, p):
    """…и обратно в новое окно, в цветах новой темы."""
    try:
        p.setParent(_host_of(window))
        p.restyle()
        _PANELS[id(window)] = p
        if p.items:
            p.show()
            p.place()
    except RuntimeError:
        pass


def download(owner, f, folder=None):
    """Скачать файл в папку «Загрузки» (с ходом в панели)."""
    name = file_name(f)
    if f.get("src"):
        path, query = f["src"], None
    else:
        path, query = endpoint(f, "download")
    dest = unique_path(folder or downloads_dir(), name)
    job = _Download(path, query, dest)
    _ACTIVE.append(job)
    job.finished.connect(lambda _p: _ACTIVE.remove(job) if job in _ACTIVE else None)
    job.failed.connect(lambda _t: _ACTIVE.remove(job) if job in _ACTIVE else None)
    panel = _panel_for(owner)
    if panel is not None:
        panel.add(name, job)
    job.run()
    return job


def open_external(owner, f):
    """Документ — во временную папку и в программу компьютера (Word, Excel, просмотр PDF…)."""
    name = file_name(f)
    folder = os.path.join(tempfile.gettempdir(), "kovapp-open")
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, "%s-%s" % (file_id(f), re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", safe_name(name))))
    if os.path.exists(dest) and time.time() - os.path.getmtime(dest) < 600:
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(dest)):
            toast(owner, "На компьютере нет программы для «%s» — файл сохранён: %s" % (safe_name(name), dest), "warning", 5000)
        return
    toast(owner, "Открываем «%s»…" % safe_name(name), "info")
    path, query = endpoint(f, "download") if not f.get("src") else (f["src"], None)
    job = _Download(path, query, dest)
    _ACTIVE.append(job)

    def ok(p):
        if job in _ACTIVE:
            _ACTIVE.remove(job)
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(p)):
            toast(owner, "На компьютере нет программы для «%s» — скачайте файл" % safe_name(name), "warning", 5000)

    def bad(text):
        if job in _ACTIVE:
            _ACTIVE.remove(job)
        toast(owner, "Не удалось открыть «%s»: %s" % (safe_name(name), text), "error")
    job.finished.connect(ok)
    job.failed.connect(bad)
    job.run()


def open_file(owner, f, files=None, actions=None):
    name = file_name(f)
    if is_media(name):
        media = [x for x in (files or [f]) if is_media(file_name(x))]
        index = next((i for i, x in enumerate(media) if file_id(x) == file_id(f) and file_kind(x) == file_kind(f)), 0)
        from kov.viewer import MediaViewer
        MediaViewer.show_list(owner, media, index, actions)
        return
    open_external(owner, f)


def delete_file(owner, f, done=None):
    from kov.dialogs import confirm
    name = safe_name(file_name(f))
    if not confirm(owner, "Удаление файла", "Удалить файл «%s»?" % name, "Удалить", danger=True):
        return

    def ok(_):
        toast(owner, "Файл удалён", "success")
        if done:
            done(f)
    path = ENDPOINTS[file_kind(f)]["del"].format(id=file_id(f))
    jobs.run(client.delete, path, done=ok,
             failed=lambda e: toast(owner, "Не удалось удалить: " + human_error(e), "error"), owner=owner)


# ══════════════════════════════════════════════════════════════════════════
# Отправка (фоновый поток → сигналы в интерфейс)
# ══════════════════════════════════════════════════════════════════════════

class UploadTask(QObject):
    progress = Signal(float, str, object)
    finished = Signal(object)
    failed = Signal(str, str)

    def __init__(self, files, path, fields):
        super().__init__()
        from kov.media.upload import Job
        self.files = files
        self.path = path
        self.fields = fields
        self.job = Job()
        self.p = 0.0
        self.label = ""
        self.done = False

    def start(self):
        from kov.media import upload as up

        def report(p, text, detail):
            self.p, self.label = p, text
            self.progress.emit(float(p), str(text or ""), detail)

        def work():
            try:
                created = up.upload(self.job, self.files, self.path, self.fields, report)
                self.done = True
                self.finished.emit(created)
            except up.Cancelled:
                self.done = True
                self.failed.emit("aborted", "Загрузка отменена — файл не сохранён")
            except up.Stalled as e:
                self.done = True
                self.failed.emit("stalled", str(e))
            except up.NetworkLost as e:
                self.done = True
                self.failed.emit("network", str(e) + ". Попробуйте ещё раз, когда связь вернётся")
            except Exception as e:
                self.done = True
                self.failed.emit("error", "Загрузка не удалась: " + up.human(e))
            finally:
                self.job.cleanup()
        threading.Thread(target=work, daemon=True, name="kov-upload-job").start()

    def cancel(self):
        self.job.cancel("user")


LIVE_UPLOADS = {}       # куда грузим → идущая отправка


def uploads_active():
    return any(not t.done for t in LIVE_UPLOADS.values())


def abort_all_uploads():
    for t in list(LIVE_UPLOADS.values()):
        t.cancel()


# ══════════════════════════════════════════════════════════════════════════
# Блок «категории файлов»
# ══════════════════════════════════════════════════════════════════════════

class CategoryBlock(QWidget):
    """Вкладки Документы/Изображения/Видео/Другое, поиск, фильтр по отправителю,
    плитки, «Добавить файлы» и перенос мышью, полоса загрузки с отменой."""

    category_changed = Signal(str)

    def __init__(self, get_files, can_upload=False, can_delete=None, upload_path="upload", upload_fields=None,
                 on_changed=None, on_uploaded=None, initial_category="documents", upload_hint="",
                 sort_earliest_first=False, no_tabs=False, drop_zone=True, accept=None, parent=None):
        """accept="media" — только фото и видео (медиафайлы проекта): документы не принимаются."""
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        self.accept = accept
        self.get_files = get_files
        self.can_upload = can_upload
        self.can_delete = can_delete
        self.upload_path = upload_path
        self.upload_fields = dict(upload_fields or {})
        self.on_changed = on_changed
        self.on_uploaded = on_uploaded
        self.sort_earliest_first = sort_earliest_first
        self.category = None if no_tabs else (initial_category or "documents")
        self.uploader = None
        self.job_key = "%s|%s" % (upload_path, sorted(self.upload_fields.items()))
        self.task = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(rem(0.6))
        self.tabs = None
        if not no_tabs:
            self.tabs = Segmented([(k, t, g) for k, t, g in CATEGORIES], self.category)
            self.tabs.changed.connect(self._tab)
            lay.addWidget(self.tabs)
        bar = QHBoxLayout()
        bar.setSpacing(rem(0.5))
        self.search = SearchInput("Поиск по имени файла…")
        self.search.textChanged.connect(lambda _t: self.render())
        bar.addWidget(self.search, 4)
        self.filter = PickerSelect([(None, "Все отправители")], None, "Все отправители", "Поиск по ФИО…",
                                   "Никого не найдено")
        self.filter.setToolTip("Фильтр по сотруднику")
        self.filter.changed.connect(self._filter)
        bar.addWidget(self.filter, 2)
        bar.addSpacing(rem(0.4))
        media = accept == "media"
        self.add_btn = Button("Добавить фото и видео" if media else "Добавить файлы", "primary", "upload")
        self.add_btn.setToolTip(("Выбрать фото и видео" if media else "Выбрать файлы") +
                                " · можно и перетащить их сюда · до 2 ГБ каждый" if can_upload
                                else (upload_hint or "Загрузка файлов в этот раздел сейчас недоступна"))
        self.add_btn.setEnabled(bool(can_upload))
        self.add_btn.clicked.connect(self.pick)
        bar.addWidget(self.add_btn)
        lay.addLayout(bar)
        self.grid = FileGrid(can_delete, ("Файлов пока нет — нажмите «%s» или перетащите их сюда" % self.add_btn.text())
                             if can_upload else "Файлов пока нет")
        self.grid.open_requested.connect(self._open)
        self.grid.download_requested.connect(lambda f: download(self, f))
        self.grid.delete_requested.connect(lambda f: delete_file(self, f, lambda _f: self.on_changed and self.on_changed()))
        lay.addWidget(self.grid, 1)

        self.progress = QWidget()
        pl = QVBoxLayout(self.progress)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(rem(0.35))
        self.bar = ProgressLine()
        pl.addWidget(self.bar)
        row = QHBoxLayout()
        self.bar_text = label("", 0.8, theme.TEXT_LABEL)
        row.addWidget(self.bar_text, 1)
        self.cancel_btn = Button("Отменить", "ghost", "close", compact=True)
        self.cancel_btn.clicked.connect(self.cancel_upload)
        row.addWidget(self.cancel_btn)
        pl.addLayout(row)
        self.progress.hide()
        lay.addWidget(self.progress)

        self.drop = None
        if drop_zone and can_upload:
            self.setAcceptDrops(True)
            self.drop = _DropZone(self, "Отпустите фото и видео здесь" if media else "Отпустите файлы здесь")
            self.drop.hide()
        self.render()
        running = LIVE_UPLOADS.get(self.job_key)
        if running is not None and not running.done:
            self._attach(running)

    # ── вывод ────────────────────────────────────────────────────────────────

    def files_in_category(self):
        all_files = self.get_files() or []
        if self.category is None:
            return all_files
        return [f for f in all_files if categorize(file_name(f)) == self.category]

    def render(self):
        all_files = self.get_files() or []
        in_cat = self.files_in_category()
        people = {}
        for f in in_cat:
            pid, nm = uploader_id(f), uploader_name(f)
            if pid and nm and pid not in people:
                people[pid] = nm
        options = [(None, "Все отправители")] + sorted(people.items(), key=lambda x: x[1])
        if self.uploader is not None and self.uploader not in people:
            self.uploader = None
        self.filter.set_options(options, self.uploader)
        self.filter.setVisible(bool(people))
        lst = in_cat
        needle = self.search.text().strip().lower()
        if needle:
            lst = [f for f in lst if needle in file_name(f).lower()]
        if self.uploader is not None:
            lst = [f for f in lst if uploader_id(f) == self.uploader]

        def when(f):
            d = parse_date(f.get("created_at"))
            return (d.timestamp() if d else 0, file_id(f) or 0)
        lst = sorted(lst, key=when, reverse=not self.sort_earliest_first)
        self.grid.fill(lst)
        if self.tabs is not None:
            for key, text, _g in CATEGORIES:
                n = sum(1 for f in all_files if categorize(file_name(f)) == key)
                b = self.tabs.button(key)
                if b is not None:
                    b.setText(text + (" (%d)" % n if n else ""))
                    b.setToolTip(text + (": %d" % n if n else ""))

    def _tab(self, key):
        self.category = key
        self.category_changed.emit(key)
        self.render()

    def set_category(self, key):
        if self.tabs is None or not key:
            return
        self.category = key
        self.tabs.set_current(key)
        self.render()

    def _filter(self, value):
        self.uploader = value
        self.render()

    def _open(self, f):
        open_file(self, f, self.grid.files)

    # ── загрузка ─────────────────────────────────────────────────────────────

    def pick(self):
        if not self.can_upload:
            return
        if self.accept == "media":
            paths, _ = QFileDialog.getOpenFileNames(self.window(), "Выберите фото и видео", "", MEDIA_FILTER)
        else:
            paths, _ = QFileDialog.getOpenFileNames(self.window(), "Выберите файлы", "", "Все файлы (*)")
        if paths:
            self.upload_paths(paths)

    def _fits(self, path):
        return self.accept != "media" or is_media(os.path.basename(path))

    def dragEnterEvent(self, e):
        local = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()] if e.mimeData().hasUrls() else []
        if local and any(self._fits(p) for p in local):
            e.acceptProposedAction()
            if self.drop is not None:
                self.drop.setGeometry(self.rect())
                self.drop.show()
                self.drop.raise_()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        if self.drop is not None:
            self.drop.hide()

    def dropEvent(self, e):
        if self.drop is not None:
            self.drop.hide()
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        paths = [p for p in paths if os.path.isfile(p)]
        if paths:
            e.acceptProposedAction()
            self.upload_paths(paths)

    @staticmethod
    def _name_keys(name):
        full = str(name or "").strip().lower()
        if not full:
            return []
        return [full, re.sub(r"\.[^.]+$", "", full)] if is_media(full) else [full]

    def _ask_replace(self, paths):
        if not self.can_delete:
            return []
        picked = set()
        for p in paths:
            picked.update(self._name_keys(os.path.basename(p)))
        clash = [f for f in (self.get_files() or []) if file_id(f) is not None and self.can_delete(f)
                 and any(k in picked for k in self._name_keys(file_name(f)))]
        if not clash:
            return []
        counts = {}
        for f in clash:
            counts[file_name(f)] = counts.get(file_name(f), 0) + 1
        total = len(clash)
        tail = "файл" if total % 10 == 1 and total % 100 != 11 else \
            ("файла" if total % 10 in (2, 3, 4) and total % 100 not in (12, 13, 14) else "файлов")
        from kov.dialogs import Modal
        m = Modal(self, "Файл с таким названием уже есть", "warning", width_rem=30)
        m.body.addWidget(label("Здесь уже есть файл с таким названием. Заменить его новым?" if total == 1 else
                               "Здесь уже есть %d %s с такими названиями. Заменить их новыми?" % (total, tail),
                               0.9, theme.TEXT_VALUE, wrap=True))
        lines = "\n".join("• " + n + (" — %d шт." % c if c > 1 else "") for n, c in counts.items())
        m.body.addWidget(label(lines, 0.87, theme.TEXT_VALUE, wrap=True))
        m.body.addWidget(label("Сначала загрузится новый файл, старые удалятся только после этого.", 0.8,
                               theme.TEXT_MUTED, wrap=True))
        answer = {"v": []}

        def yes(_m):
            answer["v"] = [{"id": file_id(f), "kind": file_kind(f), "name": file_name(f)} for f in clash]
            return True
        m.add_button("Оставить как есть", "ghost", None, lambda _m: True)
        m.add_button("Заменить", "primary", "refresh", yes)
        m.exec()
        return answer["v"]

    def upload_paths(self, paths):
        paths = [p for p in paths if os.path.isfile(p)]
        if not self.can_upload:
            return
        unfit = [p for p in paths if not self._fits(p)]
        if unfit:
            name = os.path.basename(unfit[0])
            more = " и ещё %d" % (len(unfit) - 1) if len(unfit) > 1 else ""
            toast(self, "Сюда добавляются только фото и видео — «%s»%s не добавлен%s" % (name, more, "ы" if more else ""),
                  "warning", 4500)
            paths = [p for p in paths if p not in unfit]
        if not paths:
            return
        big = [p for p in paths if os.path.getsize(p) > MAX_UPLOAD]
        if big:
            toast(self, "Файл больше 2 ГБ: " + os.path.basename(big[0]), "error")
            return
        if self.task is not None and not self.task.done:
            toast(self, "Дождитесь окончания текущей загрузки", "info")
            return
        try:
            replace = self._ask_replace(paths)
        except Exception:
            replace = []
        task = UploadTask([(p, os.path.basename(p)) for p in paths], self.upload_path, self.upload_fields)
        task.replace = replace
        task.many = len(paths) > 1
        task.owner_key = self.job_key
        LIVE_UPLOADS[self.job_key] = task
        task.finished.connect(lambda created, t=task: _upload_finished(t, created))
        task.failed.connect(lambda kind, text, t=task: _upload_failed(t, kind, text))
        self._attach(task)
        task.start()

    def _attach(self, task):
        self.task = task
        self.progress.show()
        self.cancel_btn.show()
        self.cancel_btn.setEnabled(True)
        self.add_btn.setEnabled(False)
        self.bar.set_value(task.p)
        self.bar_text.setText(task.label or "Подготовка…")
        task.progress.connect(self._on_progress)
        task.finished.connect(self._on_finished)
        task.failed.connect(self._on_failed)

    def _on_progress(self, p, text, _detail):
        self.bar.set_value(p)
        if text:
            self.bar_text.setText(text)

    def _on_finished(self, created):
        self.bar.set_value(1.0)
        many = getattr(self.task, "many", False)
        self.bar_text.setText("Готово · файлы доступны" if many else "Готово · файл доступен")
        if self.on_uploaded and isinstance(created, list) and created:
            self.on_uploaded(created)
        shown = [f for f in (created or []) if isinstance(f, dict) and file_name(f)] if isinstance(created, list) else []
        if self.tabs is not None and shown and not any(categorize(file_name(f)) == self.category for f in shown):
            self.set_category(categorize(file_name(shown[0])))
            self.category_changed.emit(self.category)
        self.render()
        if self.on_changed:
            self.on_changed()
        QTimer.singleShot(900, self._reset)

    def _on_failed(self, kind, text):
        self._reset()

    def _reset(self):
        self.task = None
        self.progress.hide()
        self.bar.reset()
        self.bar_text.setText("")
        self.add_btn.setEnabled(bool(self.can_upload))

    def cancel_upload(self):
        if self.task is not None and not self.task.done:
            self.task.cancel()
            self.bar_text.setText("Отменяем…")
            self.cancel_btn.setEnabled(False)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.drop is not None and self.drop.isVisible():
            self.drop.setGeometry(self.rect())

    def shutdown(self):
        # отправка продолжается в фоне; при возвращении блок снова покажет её ход
        pass


def _upload_finished(task, created):
    """Итог отправки — даже если блок уже закрыт: тост и замена одноимённых."""
    replace = getattr(task, "replace", []) or []
    win = QApplication.activeWindow()
    toast(win, "Файлы загружены" if getattr(task, "many", False) else "Файл загружен", "success")
    if replace:
        fresh = set(file_id(f) for f in (created or []) if isinstance(f, dict))

        def work():
            gone, failed = 0, []
            for old in replace:
                if old["id"] is None or old["id"] in fresh:
                    continue
                try:
                    client.delete(ENDPOINTS[old["kind"]]["del"].format(id=old["id"]))
                    gone += 1
                except Exception as e:
                    if getattr(e, "status", None) in (404, 410):
                        gone += 1
                    else:
                        failed.append(old["name"])
            return gone, failed

        def done(res):
            gone, failed = res
            if gone:
                toast(win, "Старый файл заменён" if gone == 1 else "Заменено файлов: %d" % gone, "success")
            if failed:
                toast(win, "Новый файл загружен, но прежний остался: " + ", ".join(sorted(set(failed))), "warning")
        jobs.run(work, done=done)
    QTimer.singleShot(1200, lambda: LIVE_UPLOADS.pop(task.owner_key, None) if LIVE_UPLOADS.get(task.owner_key) is task else None)


def _upload_failed(task, kind, text):
    win = QApplication.activeWindow()
    toast(win, text, "info" if kind == "aborted" else "error", 4200)
    if LIVE_UPLOADS.get(task.owner_key) is task:
        LIVE_UPLOADS.pop(task.owner_key, None)


class _DropZone(QWidget):
    """Подсветка при переносе файлов: рамка и «Отпустите файлы здесь»."""

    def __init__(self, parent, text="Отпустите файлы здесь"):
        super().__init__(parent)
        self.text = text
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        bg = QColor(theme.INFO_SOFT)
        bg.setAlpha(225)
        from PySide6.QtGui import QPen
        pen = QPen(QColor(theme.ACCENT), max(2, rem(0.16)), Qt.DashLine)
        p.setPen(pen)
        p.setBrush(bg)
        p.drawRoundedRect(r, rem(0.75), rem(0.75))
        g = rem(2.4)
        cy = int(r.center().y())
        p.drawPixmap(int(r.center().x()) - g // 2, cy - g - rem(0.4), icons.pixmap("upload", g, theme.INFO))
        p.setPen(QColor(theme.INFO))
        p.setFont(font(1.0, bold=True))
        p.drawText(QRect(0, cy, self.width(), rem(1.5)), Qt.AlignHCenter | Qt.AlignTop, self.text)
        p.setFont(font(0.78))
        p.drawText(QRect(0, cy + rem(1.5), self.width(), rem(1.3)), Qt.AlignHCenter | Qt.AlignTop, "до 2 ГБ каждый")
        p.end()
