# -*- coding: utf-8 -*-
import json
import math
import os
import struct
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

MB = 1024 * 1024
LAMBDA = 4.0
ZIP_UNPACK_BUDGET = int(os.getenv("GUARD_UNPACK_BUDGET_MB", "1024")) * MB
MAX_VIDEO_PIXELS = 40_000_000
MAX_VIDEO_FPS = 240
MAX_VIDEO_SECONDS = 4 * 3600
MAX_STREAMS = 16
OFFICE_EXT = {".docx", ".xlsx", ".xlsm", ".xltx", ".xltm", ".pptx", ".odt", ".ods", ".odp", ".docm", ".pptm"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".heif", ".avif"}
VIDEO_EXT = {".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".mkv", ".ts", ".m4v", ".3gp"}
DANGEROUS = {"html", "exe", "elf", "script"}
DECODE_COEF = {"hevc": 0.23, "h264": 0.066, "vp9": 0.12, "av1": 0.2, "mpeg4": 0.04}
ENCODE_COEF = 0.43
CPU_FACTOR = float(os.getenv("GUARD_CPU_FACTOR", "1.35"))
_journal_lock = threading.Lock()


# ── Вердикт допускового контроля ──
@dataclass
class Verdict:
    outcome: str
    container: str
    kind: str
    reasons: list = field(default_factory=list)
    features: dict = field(default_factory=dict)
    estimate: dict = field(default_factory=dict)
    message: str = ""

    def as_dict(self) -> dict:
        return {"outcome": self.outcome, "container": self.container, "kind": self.kind,
                "reasons": self.reasons, "features": self.features, "estimate": self.estimate,
                "message": self.message}


# ── Анализаторы контейнеров без декодирования ──
def sniff(head: bytes) -> str:
    if head[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "avi"
    if head[:2] == b"BM" and len(head) > 26:
        return "bmp"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"heic", b"heix", b"mif1", b"msf1", b"heim", b"heis", b"hevc", b"hevx"):
            return "heic"
        if brand in (b"avif", b"avis"):
            return "avif"
        return "mp4"
    if head[4:8] in (b"moov", b"mdat", b"wide", b"free", b"skip"):
        return "mp4"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "mkv"
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06"):
        return "zip"
    if head[:2] == b"\x1f\x8b":
        return "gzip"
    if head[:5] == b"%PDF-":
        return "pdf"
    if head[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "ole"
    if head[:4] == b"Rar!":
        return "rar"
    if head[:6] == b"7z\xbc\xaf\x27\x1c":
        return "7z"
    if head[:2] == b"MZ":
        return "exe"
    if head[:4] == b"\x7fELF":
        return "elf"
    text = head[:1024].lstrip().lower()
    if text.startswith(b"<!doctype html") or text.startswith(b"<html") or b"<script" in text:
        return "html"
    if text.startswith(b"<?xml") or text.startswith(b"<svg"):
        return "svg" if b"<svg" in head[:4096].lower() else "xml"
    if text.startswith(b"#!"):
        return "script"
    return "unknown"


def entropy(block: bytes) -> float:
    if not block:
        return 0.0
    counts = [0] * 256
    for b in block:
        counts[b] += 1
    total = float(len(block))
    return -sum(c / total * math.log2(c / total) for c in counts if c)


def _jpeg_dims(fh, limit: int = 4 * MB):
    fh.seek(2)
    read = 2
    while read < limit:
        marker = fh.read(2)
        if len(marker) < 2 or marker[0] != 0xFF:
            return None
        code = marker[1]
        read += 2
        if code in (0xD8, 0x01) or 0xD0 <= code <= 0xD7:
            continue
        length_raw = fh.read(2)
        if len(length_raw) < 2:
            return None
        length = struct.unpack(">H", length_raw)[0]
        if code in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            data = fh.read(6)
            bits, h, w, comps = data[0], struct.unpack(">H", data[1:3])[0], struct.unpack(">H", data[3:5])[0], data[5]
            return w, h, comps, bits
        fh.seek(length - 2, 1)
        read += length
    return None


def _raster(path: Path, container: str):
    with open(path, "rb") as fh:
        head = fh.read(64)
        if container == "png" and len(head) >= 26:
            w, h = struct.unpack(">II", head[16:24])
            depth, color = head[24], head[25]
            channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color, 4)
            return w, h, channels, depth
        if container == "gif" and len(head) >= 10:
            w, h = struct.unpack("<HH", head[6:10])
            return w, h, 4, 8
        if container == "bmp" and len(head) >= 26:
            w, h = struct.unpack("<ii", head[18:26])
            return abs(w), abs(h), 4, 8
        if container == "webp" and len(head) >= 30:
            kind = head[12:16]
            if kind == b"VP8 ":
                w, h = struct.unpack("<HH", head[26:30])
                return w & 0x3FFF, h & 0x3FFF, 3, 8
            if kind == b"VP8L":
                b = head[21:25]
                w = 1 + (((b[1] & 0x3F) << 8) | b[0])
                h = 1 + (((b[3] & 0x0F) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6))
                return w, h, 4, 8
            if kind == b"VP8X":
                w = 1 + int.from_bytes(head[24:27], "little")
                h = 1 + int.from_bytes(head[27:30], "little")
                return w, h, 4, 8
        if container == "jpeg":
            return _jpeg_dims(fh)
        if container in ("heic", "avif"):
            fh.seek(0)
            data = fh.read(256 * 1024)
            i = data.find(b"ispe")
            if i > 0 and i + 16 <= len(data):
                w, h = struct.unpack(">II", data[i + 8:i + 16])
                return w, h, 3, 10
        if container == "tiff":
            fh.seek(0)
            data = fh.read(64 * 1024)
            end = "<" if data[:2] == b"II" else ">"
            try:
                off = struct.unpack(end + "I", data[4:8])[0]
                n = struct.unpack(end + "H", data[off:off + 2])[0]
                w = h = 0
                for i in range(n):
                    e = data[off + 2 + 12 * i: off + 14 + 12 * i]
                    tag, typ = struct.unpack(end + "HH", e[:4])
                    val = struct.unpack(end + ("H" if typ == 3 else "I"), e[8:10] if typ == 3 else e[8:12])[0]
                    if tag == 256:
                        w = val
                    elif tag == 257:
                        h = val
                return w, h, 4, 8
            except Exception:
                return None
    return None


def _zip_directory(path: Path, length: int) -> Optional[dict]:
    with open(path, "rb") as fh:
        tail = min(length, 66 * 1024)
        fh.seek(length - tail)
        data = fh.read(tail)
        pos = data.rfind(b"PK\x05\x06")
        if pos < 0:
            return None
        entries, cd_size, cd_off = struct.unpack("<HII", data[pos + 10:pos + 20])
        if cd_off == 0xFFFFFFFF or entries == 0xFFFF:
            return {"entries": entries, "unpacked": 0, "max_entry": 0, "max_ratio": 0.0,
                    "nested": 0, "bad_names": 0, "encrypted": 0, "zip64": True}
        fh.seek(cd_off)
        cd = fh.read(min(cd_size, 64 * MB))
    info = {"entries": 0, "unpacked": 0, "max_entry": 0, "max_ratio": 0.0, "nested": 0,
            "bad_names": 0, "encrypted": 0, "zip64": False}
    i = 0
    while i + 46 <= len(cd) and cd[i:i + 4] == b"PK\x01\x02":
        flags, method = struct.unpack("<HH", cd[i + 8:i + 12])
        comp, raw = struct.unpack("<II", cd[i + 20:i + 28])
        nlen, xlen, clen = struct.unpack("<HHH", cd[i + 28:i + 34])
        name = cd[i + 46:i + 46 + nlen].decode("utf-8", "replace")
        info["entries"] += 1
        info["unpacked"] += raw
        info["max_entry"] = max(info["max_entry"], raw)
        if comp:
            info["max_ratio"] = max(info["max_ratio"], raw / comp)
        elif raw:
            info["max_ratio"] = max(info["max_ratio"], float(raw))
        low = name.lower()
        if low.endswith((".zip", ".gz", ".7z", ".rar", ".jar")):
            info["nested"] += 1
        if name.startswith(("/", "\\")) or ".." in name.replace("\\", "/").split("/") or (len(name) > 1 and name[1] == ":"):
            info["bad_names"] += 1
        if flags & 1:
            info["encrypted"] += 1
        i += 46 + nlen + xlen + clen
    return info


def _kind_of(ext: str) -> str:
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    return "document"


def probe(path, name: str) -> dict:
    path = Path(path)
    length = path.stat().st_size
    with open(path, "rb") as fh:
        head = fh.read(64 * 1024)
    ext = Path(str(name or "")).suffix.lower()
    container = sniff(head)
    features = {"length": length, "ext": ext, "container": container,
                "entropy": round(entropy(head[:4096]), 3)}
    if container in ("jpeg", "png", "gif", "webp", "bmp", "tiff", "heic", "avif"):
        dims = None
        try:
            dims = _raster(path, container)
        except Exception:
            dims = None
        if dims:
            w, h, ch, bits = dims
            features.update({"width": w, "height": h, "channels": ch, "bits": bits,
                             "declared_raster": w * h * max(ch, 3) * (2 if bits > 8 else 1)})
            features["ratio"] = round(features["declared_raster"] / max(1, length), 1)
    elif container == "zip":
        z = _zip_directory(path, length)
        if z:
            features.update({"zip_" + k: v for k, v in z.items()})
            features["ratio"] = round(z["unpacked"] / max(1, length), 1)
    elif container == "gzip" and length >= 18:
        with open(path, "rb") as fh:
            fh.seek(-4, 2)
            isize = struct.unpack("<I", fh.read(4))[0]
        features.update({"gzip_isize": isize, "ratio": round(isize / max(1, length), 1)})
    elif container in ("mp4", "mkv", "avi") or ext in VIDEO_EXT:
        try:
            from .media import probe_media
            data = probe_media(str(path))
            streams = data.get("streams") or []
            video = next((s for s in streams if s.get("codec_type") == "video"), {}) or {}
            fps = 0.0
            try:
                num, _, den = str(video.get("avg_frame_rate") or "0/1").partition("/")
                fps = float(num) / float(den or 1) if float(den or 1) else 0.0
            except Exception:
                fps = 0.0
            fmt = str(video.get("pix_fmt") or "")
            features.update({"width": int(video.get("width") or 0), "height": int(video.get("height") or 0),
                             "fps": round(fps, 2), "codec": video.get("codec_name") or "",
                             "bits": 10 if ("10" in fmt or "12" in fmt) else 8,
                             "duration": float((data.get("format") or {}).get("duration") or 0.0),
                             "streams": len(streams),
                             "audio": sum(1 for s in streams if s.get("codec_type") == "audio")})
        except Exception as e:
            features["probe_error"] = str(e)[:200]
    return features


# ── Оценка ресурсоёмкости ──
def estimate(features: dict, kind: str) -> dict:
    c = features.get("container")
    if kind == "image" and features.get("width"):
        w, h = features["width"], features["height"]
        pixels = w * h
        factor = 1.0
        if c == "jpeg" and max(w, h) >= 6400:
            factor = 0.25
        mem = pixels * 4 * factor * 1.6 + 40 * MB
        cpu = pixels / 1e6 * 0.08
        return {"mem": int(mem), "cpu": round(cpu, 2), "lower": int(pixels * 3 * factor)}
    if kind == "video" and features.get("width"):
        from .load_governor import estimate_video_memory
        w, h, fps, dur = features["width"], features["height"], features.get("fps") or 30.0, features.get("duration") or 0.0
        mp_in = w * h / 1e6
        side = max(w, h)
        mp_out = mp_in * (min(1.0, 1920.0 / side) ** 2 if side else 1.0)
        per_sec = (DECODE_COEF.get(features.get("codec"), 0.1) * mp_in * max(1.0, fps / 30.0)
                   + ENCODE_COEF * mp_out * min(1.0, 30.0 / max(1.0, fps)) * max(1.0, fps / 30.0))
        mem = estimate_video_memory(w, h, features.get("bits") or 8)
        return {"mem": int(mem), "cpu": round(dur * per_sec * CPU_FACTOR, 1),
                "lower": int(w * h * 1.5 * (2 if (features.get("bits") or 8) > 8 else 1) * 4)}
    if c == "zip":
        top = features.get("zip_max_entry", 0)
        return {"mem": int(150 * MB + top * 6), "cpu": round(features.get("zip_unpacked", 0) / (5 * MB), 1),
                "lower": int(top)}
    return {"mem": int(20 * MB), "cpu": 0.1, "lower": 0}


# ── Журнал наблюдений и калибровка ──
def _journal_path() -> Path:
    base = os.getenv("GUARD_JOURNAL") or str(Path(os.getenv("LOG_DIR") or "logs") / "resguard_journal.jsonl")
    return Path(base)


def journal(verdict: "Verdict", actual: Optional[dict] = None) -> None:
    try:
        path = _journal_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"t": round(time.time(), 1), "kind": verdict.kind, "outcome": verdict.outcome,
                  "container": verdict.container, "features": verdict.features,
                  "estimate": verdict.estimate, "actual": actual or {}}
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        with _journal_lock:
            if path.exists() and path.stat().st_size > 20 * MB:
                os.replace(path, path.with_suffix(".jsonl.1"))
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line)
    except Exception:
        pass


_calib_cache = {"t": 0.0, "value": {}}


def calibration(kind: str) -> dict:
    now = time.time()
    if now - _calib_cache["t"] > 300:
        ratios = {}
        try:
            path = _journal_path()
            if path.exists():
                with open(path, "rb") as fh:
                    fh.seek(max(0, path.stat().st_size - 2 * MB))
                    lines = fh.read().decode("utf-8", "replace").splitlines()[-2000:]
                for line in lines:
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    act, est = r.get("actual") or {}, r.get("estimate") or {}
                    for key in ("mem", "cpu"):
                        if act.get(key) and est.get(key):
                            ratios.setdefault((r.get("kind"), key), []).append(act[key] / est[key])
        except Exception:
            ratios = {}
        value = {}
        for (k, key), items in ratios.items():
            items = sorted(items[-200:])
            if len(items) >= 20:
                value.setdefault(k, {})[key] = max(0.5, min(4.0, items[int(0.95 * (len(items) - 1))]))
        _calib_cache.update(t=now, value=value)
    return _calib_cache["value"].get(kind, {})


# ── Политика допуска ──
def budget() -> int:
    env = os.getenv("GUARD_MEMORY_BUDGET_MB")
    if env:
        return int(env) * MB
    try:
        from .load_governor import governor
        cap = governor().capacity
        return max(256 * MB, int((cap.ram_total - cap.ram_reserve) * 0.6))
    except Exception:
        return 512 * MB


def assess(path, name: str, memory_budget: Optional[int] = None) -> Verdict:
    ext = Path(str(name or "")).suffix.lower()
    kind = _kind_of(ext)
    features = probe(path, name)
    container = features["container"]
    b_m = memory_budget or budget()
    reasons = []
    if kind in ("image", "video") and container in DANGEROUS:
        return Verdict("reject", container, kind, ["содержимое не соответствует расширению"], features, {},
                       "Файл выдаёт себя за фото или видео, но им не является")
    if kind == "image" and container not in ("jpeg", "png", "gif", "webp", "bmp", "tiff", "heic", "avif"):
        kind, reasons = "document", ["это не изображение — сохраняем как файл"]
    if kind == "video" and container not in ("mp4", "mkv", "avi") and not features.get("width"):
        kind, reasons = "document", ["это не видео — сохраняем как файл"]
    est = estimate(features, kind)
    calib = calibration(kind)
    if calib:
        est = dict(est, mem=int(est["mem"] * calib.get("mem", 1.0)), cpu=round(est["cpu"] * calib.get("cpu", 1.0), 1))
    if kind == "image" and features.get("declared_raster", 0) > LAMBDA * b_m:
        return Verdict("reject", container, kind, ["объявленный объём растра превышает бюджет"], features, est,
                       "Изображение слишком большое для обработки ({}×{})".format(features.get("width"), features.get("height")))
    if container == "zip" and (ext in OFFICE_EXT):
        if features.get("zip_unpacked", 0) > ZIP_UNPACK_BUDGET or features.get("zip_bad_names", 0):
            return Verdict("reject", container, kind, ["объявленный объём распаковки превышает бюджет"
                                                       if features.get("zip_unpacked", 0) > ZIP_UNPACK_BUDGET
                                                       else "недопустимые пути внутри архива"], features, est,
                           "Документ устроен подозрительно и не будет принят")
    if kind == "video" and features.get("width"):
        if (features["width"] * features["height"] > MAX_VIDEO_PIXELS or (features.get("fps") or 0) > MAX_VIDEO_FPS
                or (features.get("duration") or 0) > MAX_VIDEO_SECONDS or features.get("streams", 0) > MAX_STREAMS):
            return Verdict("reject", container, kind, ["параметры видео вне допустимых пределов"], features, est,
                           "Видео с такими параметрами сервер не обрабатывает")
    outcome = "accept"
    if est.get("mem", 0) > 0.5 * b_m:
        outcome = "isolate"
        reasons.append("высокая оценка памяти — отдельный процесс с квотой")
    return Verdict(outcome, container, kind, reasons, features, est, "")
