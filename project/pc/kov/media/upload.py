# -*- coding: utf-8 -*-
"""Отправка файлов с подготовкой на компьютере — как js/media/media-prep.js,
js/media/chunk-upload.js и upload() из js/core/files.js веб-версии.

Порядок для каждого файла:
1. Проверка копии: образец (≈0,6 МБ) → быстрый вопрос серверу; есть похожий —
   полный отпечаток и испытание: копия на сервере → файл не отправляется.
2. Подготовка: фото → WebP, видео → H.264/AAC (FFmpeg), документ → gzip.
3. Отправка: до 2 МБ — в общем запросе, крупнее — кусками по 4 МБ с докачкой
   после обрыва (до 2 минут ожидания связи).
4. Общий запрос с описанием файлов (meta) и миниатюрами; ход обработки на
   сервере — опросом upload/progress/<номер отправки>.

Всё выполняется в фоновом потоке; progress(доля, подпись, подробности)
вызывается из него же — интерфейс сам переносит его в свой поток.
"""

import gzip
import json
import math
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor

import requests

from kov.api import ApiError, client
from kov.media import filehash, image as imageprep, video as videoprep

BIG = 2 * 1024 * 1024               # крупнее — кусками
CHUNK = 4 * 1024 * 1024
DEDUP_MIN = 256 * 1024
MAX_UPLOAD = 2 * 1024 * 1024 * 1024
STALL_S = 120                       # сколько ждём связь, прежде чем бросить отправку
BACKOFF = (1, 2, 4, 8, 15, 30)
SEND_END, TAKE_END, CAP = 0.45, 0.58, 0.99

IMAGE_RE = re.compile(r"\.(jpe?g|png|webp|gif|bmp|heic|heif|avif|tiff?)$", re.I)
VIDEO_RE = re.compile(r"\.(mp4|mov|m4v|3gp|avi|mkv|webm|wmv|flv|ts)$", re.I)
GZ_SKIP = re.compile(r"\.(jpe?g|png|gif|webp|heic|heif|avif|jxl|mp4|mov|m4v|webm|mkv|avi|3gp|mp3|m4a|aac|ogg|oga|opus|"
                     r"flac|wma|zip|rar|7z|gz|tgz|bz2|xz|zst|lz|lzma|cab|docx|docm|dotx|xlsx|xlsm|xltx|pptx|pptm|"
                     r"ppsx|potx|odt|ods|odp|odg|epub|jar|apk|aab|ipa|woff2?|pdf)$", re.I)
GZ_MIN, GZ_MAX = 1024, 512 * 1024 * 1024


class Cancelled(Exception):
    def __init__(self, text="Загрузка отменена"):
        super().__init__(text)


class NetworkLost(Exception):
    pass


class Stalled(NetworkLost):
    def __init__(self):
        super().__init__("Связь не вернулась за %d мин — отправка отменена, файл не сохранён" % (STALL_S // 60))


def kind_of(name):
    if IMAGE_RE.search(name or ""):
        return "image"
    if VIDEO_RE.search(name or ""):
        return "video"
    return "document"


def crc32(data):
    return zlib.crc32(data) & 0xFFFFFFFF


class Job:
    """Одна отправка: отмена и уборка незавершённых сеансов на сервере."""

    def __init__(self):
        self._stop = threading.Event()
        self.sessions = set()
        self.temp = tempfile.mkdtemp(prefix="kovapp-up-")
        self.reason = ""

    def cancel(self, reason="user"):
        self.reason = reason
        self._stop.set()

    def cancelled(self):
        return self._stop.is_set()

    def check(self):
        if self._stop.is_set():
            raise Cancelled()

    def discard_sessions(self):
        for sid in list(self.sessions):
            try:
                client.request("DELETE", "uploads/sessions/" + sid, timeout=(5, 15))
            except Exception:
                pass
            self.sessions.discard(sid)

    def cleanup(self):
        shutil.rmtree(self.temp, ignore_errors=True)


# ── сеанс докачки ────────────────────────────────────────────────────────────

def _is_network(exc):
    if isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
        return True
    return isinstance(exc, ApiError) and exc.status in (408, 429, 500, 502, 503, 504)


class Session:
    def __init__(self, job, name, size=None, kind="", progress=None):
        self.job, self.name, self.size, self.kind = job, name, size, kind
        self.progress = progress
        self.id = None
        self.received = 0
        self.stalled_since = 0.0

    def _retry(self, fn):
        attempt = 0
        while True:
            self.job.check()
            try:
                result = fn()
                self.stalled_since = 0.0
                return result
            except (Cancelled, InterruptedError):
                raise
            except Exception as e:
                conflict = isinstance(e, ApiError) and e.status == 409
                if not (_is_network(e) or conflict):
                    raise
                if not self.stalled_since:
                    self.stalled_since = time.time()
                deadline = self.stalled_since + STALL_S
                if time.time() >= deadline:
                    raise Stalled()
                pause = BACKOFF[min(attempt, len(BACKOFF) - 1)] if not conflict else 0
                attempt += 1
                end = time.time() + min(pause, max(0, deadline - time.time()))
                while time.time() < end:
                    self.job.check()
                    time.sleep(0.2)
                if self.id:
                    try:
                        self.sync()
                    except Exception:
                        pass

    def open(self):
        r = self._retry(lambda: client.request("POST", "uploads/sessions", timeout=(10, 60), json_body={
            "name": self.name, "size": self.size, "kind": self.kind or "", "meta": {}}))
        self.id = r.get("id")
        self.received = int(r.get("received") or 0)
        self.job.sessions.add(self.id)
        return self

    def sync(self):
        r = client.request("GET", "uploads/sessions/" + self.id, timeout=(10, 30))
        self.received = int(r.get("received") or 0)
        return self.received

    def put(self, offset, data):
        def send():
            if self.received >= offset + len(data):
                return self.received
            skip = max(0, self.received - offset)
            part = data[skip:]
            try:
                r = client.request("PUT", "uploads/sessions/" + self.id, query={"offset": offset + skip},
                                   data=part, timeout=(10, 120),
                                   headers={"Content-Type": "application/octet-stream",
                                            "X-Chunk-Crc32": str(crc32(part))})
                self.received = int(r.get("received") or 0)
            except ApiError as e:
                got = (e.data or {}).get("received") if isinstance(e.data, dict) else None
                if got is None and isinstance(e.data, dict) and isinstance(e.data.get("detail"), dict):
                    got = e.data["detail"].get("received")
                if e.status == 409 and got is not None:
                    self.received = int(got)
                    if self.received >= offset + len(data):
                        return self.received
                raise
            if self.progress:
                self.progress(self.received)
            return self.received
        return self._retry(send)

    def complete(self, size):
        return self._retry(lambda: client.request("POST", "uploads/sessions/%s/complete" % self.id,
                                                  json_body={"size": size}, timeout=(10, 300)))


def upload_file(job, path, name, kind, progress=None):
    """Файл кусками; возвращает номер сеанса. Итог сверяется по отпечатку."""
    size = os.path.getsize(path)
    s = Session(job, name, size, kind, progress).open()
    with open(path, "rb") as f:
        while s.received < size:
            job.check()
            start = s.received
            f.seek(start)
            s.put(start, f.read(min(CHUNK, size - start)))
    done = s.complete(size) or {}
    if done.get("tree"):
        mine = filehash.tree(path)
        if mine != done["tree"]:
            raise RuntimeError("Файл на сервере собран с ошибкой")
    return s.id


# ── проверка копии ───────────────────────────────────────────────────────────

class _Unsupported(Exception):
    pass


def _api(method, path, body):
    try:
        return client.request(method, path, json_body=body, timeout=(10, 60))
    except ApiError as e:
        if e.status in (404, 405):
            raise _Unsupported()
        raise


def quick_dedup(job, path, name, report):
    """{'token', 'sample', 'tree'}: token — файл уже на сервере."""
    size = os.path.getsize(path)
    out = {"token": None, "sample": None, "tree": None}
    if size < DEDUP_MIN:
        return out
    try:
        out["sample"] = filehash.sample(path)
    except Exception:
        return out
    candidate, probed = False, False
    try:
        candidate = bool((_api("POST", "uploads/dedup/probe", {"size": size, "sample": out["sample"]}) or {})
                         .get("candidate"))
        probed = True
    except _Unsupported:
        return out
    except Exception:
        probed = False
    job.check()
    if candidate or not probed:
        out["tree"] = filehash.tree(path, lambda p: report(p * 0.1, "Проверка"), job.cancelled)
        try:
            ch = _api("POST", "uploads/dedup/check", {"tree": out["tree"], "size": size, "name": name}) or {}
            hashes = [filehash.range_hash(path, int(w.get("o") or 0), int(w.get("l") or 0))
                      for w in ch.get("windows") or []]
            ans = _api("POST", "uploads/dedup/answer", {"id": ch.get("id"), "hashes": hashes}) or {}
            out["token"] = ans.get("token") or None
        except _Unsupported:
            pass
        except Exception:
            pass
    return out


# ── gzip документов ──────────────────────────────────────────────────────────

def gzip_pack(job, path, name, out_dir, report):
    """('gzip', путь) | ('identity', None) | (None, None) — как Kov.Gzip.pack."""
    size = os.path.getsize(path)
    if GZ_SKIP.search(name or "") or size < GZ_MIN:
        return "identity", None
    if size > GZ_MAX:
        return None, None
    seg = 65536
    cuts = [0] if size <= 3 * seg else [0, size // 2 - seg // 2, size - seg]
    sample = b""
    with open(path, "rb") as f:
        for c in cuts:
            f.seek(c)
            sample += f.read(seg)
    ratio = len(zlib.compress(sample, 9)) / float(max(1, len(sample)))
    if ratio > 0.9:
        return "identity", None
    target = os.path.join(out_dir, "gz-%s.gz" % secrets.token_hex(4))
    done = 0
    with open(path, "rb") as src, open(target, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", compresslevel=9, fileobj=raw, mtime=0) as z:
            while True:
                job.check()
                part = src.read(1024 * 1024)
                if not part:
                    break
                z.write(part)
                done += len(part)
                report(done / float(size))
    if os.path.getsize(target) >= size:
        os.remove(target)
        return "identity", None
    return "gzip", target


# ── подготовка одного файла ──────────────────────────────────────────────────

def _send_whole(job, path, name, kind, report_bytes):
    if os.path.getsize(path) <= BIG:
        return {"multipart": (name, path)}
    sid = upload_file(job, path, name, kind, report_bytes)
    return {"session": sid}


def prepare_one(job, path, name, edits, report):
    kind = kind_of(name)
    size = os.path.getsize(path)
    meta = {"name": name, "size": size}
    out = {"meta": meta, "thumb": None}
    report(0, "Проверка")
    d = quick_dedup(job, path, name, report)
    if d["sample"]:
        meta["sample"] = d["sample"]
    out["tree"] = d["tree"]
    if d["token"]:
        meta.update(src="dedup", token=d["token"])
        report(1, "Готово")
        return out
    job.check()

    if kind == "image":
        report(0.15, "Сжатие фото")
        r = imageprep.prepare(path, name, job.temp)
        if r.get("thumb_hash"):
            meta["thumb_hash"] = r["thumb_hash"]
        if r.get("width"):
            meta["width"], meta["height"] = r["width"], r["height"]
        out["thumb"] = r.get("thumb")
        meta["prep"] = "image" if r["changed"] else "original"
        sent_size = os.path.getsize(r["path"])
        out.update(_send_whole(job, r["path"], r["name"], "image",
                               lambda b: report(0.5 + 0.5 * b / float(max(1, sent_size)), "Отправка")))
        report(1, "Готово к отправке")
        return out

    if kind == "video":
        report(0.12, "Проверка видео")
        plan = videoprep.plan(path, edits)
        why = "" if plan["mode"] == "device" else (plan.get("reason") or plan["mode"])
        if plan["mode"] == "device":
            target = os.path.join(job.temp, os.path.splitext(os.path.basename(name))[0] + ".mp4")
            ok = True
            try:
                videoprep.compress(path, target, plan, edits,
                                   progress=lambda p: report(0.15 + 0.6 * p, "Сжатие видео"),
                                   cancelled=job.cancelled)
            except InterruptedError:
                raise Cancelled()
            except Exception as e:
                job.check()
                ok = False
                why = "сбой сжатия: " + str(e)[:120]
            if ok:
                try:
                    inf = videoprep.info(target)
                except Exception:
                    inf = {}
                shot = videoprep.poster(target, at=min(1.0, (inf.get("duration") or 0) / 2))
                ph, th = imageprep.thumbs_from_bytes(shot)
                if ph:
                    meta["thumb_hash"] = ph
                    out["thumb"] = th
                out_size = os.path.getsize(target)
                try:
                    sid = upload_file(job, target, os.path.basename(target), "video",
                                      lambda b: report(0.75 + 0.25 * b / float(max(1, out_size)), "Отправка"))
                except (Cancelled, NetworkLost):
                    raise
                except Exception as e:
                    job.check()
                    sid = None
                    why = "сжатое не дошло до сервера: " + str(e)[:120]
                if sid:
                    meta.update(src="session", session=sid, prep="device", edits_applied=bool(edits),
                                width=inf.get("width") or None, height=inf.get("height") or None,
                                duration=inf.get("duration") or plan.get("duration"))
                    out["session"] = sid
                    report(1, "Готово к отправке")
                    return out
        meta["prep"] = "original"
        if why:
            meta["prep_reason"] = why
        shot = videoprep.poster(path)
        if shot:
            ph, th = imageprep.thumbs_from_bytes(shot)
            if ph:
                meta["thumb_hash"] = ph
                out["thumb"] = th
            try:
                inf = videoprep.info(path)
                meta.update(duration=inf.get("duration"), width=inf.get("width"), height=inf.get("height"))
            except Exception:
                pass
        out.update(_send_whole(job, path, name, "video",
                               lambda b: report(0.2 + 0.8 * b / float(max(1, size)), "Отправка")))
        report(1, "Готово к отправке")
        return out

    # документ
    meta["prep"] = "original"
    body = path
    try:
        enc, packed = gzip_pack(job, path, name, job.temp, lambda p: report(0.05 + 0.25 * p, "Сжатие"))
    except Cancelled:
        raise
    except Exception:
        enc, packed = None, None
    if enc == "gzip" and packed:
        meta["enc"] = "gzip"
        body = packed
    elif enc == "identity":
        meta["enc"] = "identity"
    start = 0.3 if body != path else 0.1
    body_size = os.path.getsize(body)
    out.update(_send_whole(job, body, name, kind,
                           lambda b: report(start + (1 - start) * b / float(max(1, body_size)), "Отправка")))
    report(1, "Готово к отправке")
    return out


def prepare_all(job, files, edits, on_progress):
    """files — [(путь, имя)]. Возвращает (multipart, thumbs, meta)."""
    weights = [max(1, os.path.getsize(p)) * (3 if kind_of(n) == "video" else 1) for p, n in files]
    total = float(sum(weights))
    progress = [0.0] * len(files)
    states = [{"index": i, "name": n, "state": "queued", "percent": 0, "note": ""} for i, (_, n) in enumerate(files)]
    lock = threading.Lock()

    def emit():
        p = sum(progress[i] * weights[i] for i in range(len(files))) / total
        active = next((s for s in states if s["state"] == "processing"), None)
        label = "Подготовка · " + ((active["note"].lower() + " · ") if active and active["note"] else "") + \
            "%d%%" % round(p * 100)
        on_progress(p, label, {"files": [dict(s) for s in states]})

    failed = []

    def run(i):
        p, n = files[i]
        if failed:
            raise RuntimeError("пропущен после ошибки")
        with lock:
            states[i]["state"] = "processing"

        def report(v, note):
            with lock:
                progress[i] = max(progress[i], min(1.0, v))
                states[i]["percent"] = int(round(progress[i] * 100))
                states[i]["note"] = note
            emit()
        per = (edits or {}).get(str(i)) if edits else None
        try:
            item = prepare_one(job, p, n, per, report)
        except Exception as e:
            failed.append(e)
            raise
        with lock:
            states[i]["state"] = "received"
            progress[i] = 1.0
            states[i]["percent"] = 100
        emit()
        return item

    heavy = any(kind_of(n) == "video" for _, n in files)
    workers = 1 if heavy else min(3, max(1, len(files)))
    items = [None] * len(files)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run, i): i for i in range(len(files))}
        for fut, i in futures.items():
            try:
                items[i] = fut.result()
            except Exception:
                pass
    if failed:
        raise failed[0]
    multipart, thumbs, meta = [], [], []
    for i, it in enumerate(items):
        m = dict(it["meta"])
        if it.get("tree") and "tree" not in m and m.get("src") != "dedup":
            m["tree"] = it["tree"]
            m["source_size"] = os.path.getsize(files[i][0])
        if not m.get("src"):
            if it.get("session"):
                m.update(src="session", session=it["session"])
            else:
                m.update(src="file", file=len(multipart))
                multipart.append(it["multipart"])
        if it.get("thumb"):
            m["thumb"] = len(thumbs)
            thumbs.append(it["thumb"])
        meta.append(m)
    return multipart, thumbs, meta


# ── общий запрос ─────────────────────────────────────────────────────────────

def _process_tau(files):
    mb = sum(os.path.getsize(p) for p, _ in files) / 1048576.0
    if any(kind_of(n) == "video" for _, n in files):
        return 5 + mb / 3
    if any(kind_of(n) == "image" for _, n in files):
        return 0.8 + mb / 15
    return 0.5 + mb / 30


def send_form(job, multipart, path, fields, on_progress, thumbs=None, names=None, tau_files=None):
    """Многочастный запрос с ходом отправки и опросом обработки на сервере."""
    from requests_toolbelt.multipart.encoder import MultipartEncoder, MultipartEncoderMonitor
    for name, p in multipart:
        if os.path.getsize(p) > MAX_UPLOAD:
            raise RuntimeError("Файл больше 2 ГБ: " + name)
    upload_id = "u%x%s" % (int(time.time() * 1000), secrets.token_hex(3))
    opened = []
    parts = []
    for name, p in multipart:
        fh = open(p, "rb")
        opened.append(fh)
        parts.append(("files", (name, fh, "application/octet-stream")))
    for i, t in enumerate(thumbs or []):
        parts.append(("thumbs", ("thumb%d.jpg" % i, t, "image/jpeg")))
    parts.append(("upload_id", upload_id))
    for k, v in (fields or {}).items():
        if v is not None:
            parts.append((k, str(v)))
    names = names or [n for n, _ in multipart]
    total = max(1, len(names))
    state = {"shown": 0.0, "detail": None, "done": False, "sent_all": False, "began": time.time()}
    lock = threading.Lock()

    def report(p, text, detail=None):
        with lock:
            state["shown"] = max(state["shown"], max(0.0, min(1.0, p)))
            if detail is not None:
                state["detail"] = detail
            on_progress(state["shown"], text, detail or state["detail"])

    def say(p, done_count):
        pct = "%d%%" % round(p * 100)
        if total > 1:
            return "Загрузка · %d из %d · %s" % (min(done_count + 1, total), total, pct)
        return "Загрузка · " + pct

    encoder = MultipartEncoder(fields=parts)
    sizes = [os.path.getsize(p) for _, p in multipart]

    def monitor_cb(m):
        if job.cancelled():
            raise Cancelled()
        if state["detail"] is not None:
            return
        loaded, length = m.bytes_read, max(1, encoder.len)
        p = loaded / float(length) * SEND_END
        sent = loaded
        files = []
        done_count = 0
        for i, n in enumerate(names):
            if i < len(sizes):
                size = sizes[i] or 1
                if sent >= size:
                    files.append({"index": i, "name": n, "state": "received", "percent": 100})
                    sent -= size
                    done_count += 1
                else:
                    files.append({"index": i, "name": n, "state": "receiving", "percent": int(sent * 100 / size)})
                    sent = 0
            else:
                files.append({"index": i, "name": n, "state": "receiving", "percent": int(loaded * 100 / length)})
        if loaded >= length:
            state["sent_all"] = True
            state["began"] = time.time()
        report(p, say(p, done_count), {"files": files})

    monitor = MultipartEncoderMonitor(encoder, monitor_cb)
    result = {}

    def post():
        try:
            result["value"] = client.request("POST", path, data=monitor, timeout=(15, 1800),
                                             headers={"Content-Type": monitor.content_type})
        except Exception as e:
            result["error"] = e
        finally:
            state["done"] = True

    worker = threading.Thread(target=post, name="kov-upload", daemon=True)
    worker.start()
    tau = _process_tau(tau_files or [(p, n) for n, p in multipart])
    report(0, "Отправка…")
    try:
        while not state["done"]:
            if job.cancelled():
                raise Cancelled()
            time.sleep(0.4)
            if state["done"]:
                break
            try:
                snap = client.request("GET", "upload/progress/" + upload_id, timeout=(5, 10))
            except Exception:
                snap = None
            if isinstance(snap, dict) and snap.get("files"):
                p = TAKE_END + (CAP - TAKE_END) * (float(snap.get("percent") or 0) / 100.0)
                done_count = sum(1 for f in snap["files"] if f.get("state") in ("done", "error"))
                report(p, say(p, done_count), snap)
            elif state["sent_all"] or not multipart:
                elapsed = time.time() - state["began"]
                p = SEND_END + (TAKE_END - SEND_END) * (1 - math.exp(-elapsed / max(1.0, tau / 4)))
                report(p, say(p, 0))
    finally:
        for fh in opened:
            try:
                fh.close()
            except Exception:
                pass
    err = result.get("error")
    if err is not None:
        if isinstance(err, Cancelled) or job.cancelled():
            raise Cancelled()
        if isinstance(err, (requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError)):
            raise NetworkLost("Связь с сервером потеряна — файл не отправлен")
        if isinstance(err, requests.exceptions.Timeout):
            raise NetworkLost("Сервер не ответил вовремя")
        raise err
    done = {"files": [{"index": i, "name": n, "state": "done", "percent": 100} for i, n in enumerate(names)],
            "percent": 100}
    with lock:
        state["shown"] = 1.0
    on_progress(1.0, "Готово", done)
    return result.get("value")


def _attach_sessions(job, multipart, thumbs, meta, path, fields, on_progress, files):
    """Повтор общего запроса после обрыва — только если сервер ещё не забрал куски."""
    sessions = [m["session"] for m in meta if m.get("src") == "session"]
    safe = bool(sessions) and not multipart
    until = time.time() + STALL_S
    while True:
        try:
            return send_form(job, multipart, path, fields, on_progress, thumbs, [m["name"] for m in meta], files)
        except NetworkLost:
            if not safe or job.cancelled() or time.time() >= until:
                raise
            time.sleep(2)
            taken = False
            for sid in sessions:
                try:
                    client.request("GET", "uploads/sessions/" + sid, timeout=(5, 10))
                except ApiError as e:
                    if e.status == 404:
                        taken = True
                except Exception:
                    pass
            if taken:
                return []


def scale_detail(detail, start, part, last):
    if not isinstance(detail, dict) or not isinstance(detail.get("files"), list):
        return detail
    files = []
    for f in detail["files"]:
        g = dict(f)
        g["percent"] = int(round(start * 100 + max(0, min(100, float(f.get("percent") or 0))) * part))
        if not last and f.get("state") == "done":
            g["state"] = ""
        files.append(g)
    return dict(detail, files=files)


def upload(job, files, path, fields=None, on_progress=None):
    """files — [(путь, имя)]. Подготовка на компьютере и отправка. Возвращает ответ сервера."""
    on_progress = on_progress or (lambda *a: None)
    fields = dict(fields or {})
    job.check()
    edits = None
    try:
        edits = json.loads(fields["edits"]) if fields.get("edits") else None
    except (TypeError, ValueError):
        edits = None
    if any(kind_of(n) == "video" for _, n in files) or any(os.path.getsize(p) > 32 * 1048576 for p, _ in files):
        share = 0.75
    elif any(kind_of(n) == "image" for _, n in files):
        share = 0.35
    else:
        share = 0.1
    try:
        multipart, thumbs, meta = prepare_all(job, files, edits,
                                              lambda p, label, d: on_progress(p * share, label,
                                                                              scale_detail(d, 0, share, False)))
    except (Cancelled, NetworkLost):
        job.discard_sessions()
        raise
    except Exception:
        if job.cancelled():
            job.discard_sessions()
            raise Cancelled()
        job.discard_sessions()
        # подготовка не удалась — отправляем исходные файлы, как браузер
        return send_form(job, [(n, p) for p, n in files], path, fields, on_progress)
    job.check()
    extra = dict(fields)
    extra["meta"] = json.dumps(meta, ensure_ascii=False)
    if edits:
        left = {k: v for k, v in edits.items()
                if not (k.isdigit() and int(k) < len(meta) and meta[int(k)].get("edits_applied"))}
        extra["edits"] = json.dumps(left, ensure_ascii=False) if left else None

    def attach(p, label, detail):
        on_progress(share + p * (1 - share), label, scale_detail(detail, share, 1 - share, True))
    try:
        created = _attach_sessions(job, multipart, thumbs, meta, path, extra, attach, files)
        job.sessions.clear()
        return created
    except Exception:
        job.discard_sessions()
        raise


def human(exc):
    if isinstance(exc, Cancelled):
        return "Загрузка отменена — файл не сохранён"
    if isinstance(exc, NetworkLost):
        return str(exc)
    from kov.api import human_error
    return human_error(exc, str(exc) or "Загрузка не удалась")
