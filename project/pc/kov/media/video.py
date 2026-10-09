# -*- coding: utf-8 -*-
"""Видео перед отправкой: FFmpeg из комплекта приложения.

Правила — те же, что у сервера (server_app/media.py): H.264 High, yuv420p,
не больше 30 кадров/с; звук — первая дорожка, AAC 128 кбит/с стерео; MP4
с индексом в начале. Сторона: до 60 с — до 1920 (Full HD), длиннее — до 1280
(HD), как на телефоне и в браузере. Видео, которое уже подходит, уходит как есть.

Кодировщик — OpenH264 (лицензия BSD; x264 под GPL в сборку не входит):
QP не ниже 26, поток не выше 0,08 бита на точку, ключевой кадр не реже
раза в 10 с и на смене сцены. Сервер принимает
сжатое здесь без перекодирования: для сжатого на устройстве он допускает
поток до 0,06 × 1,6 = 0,096 бита на точку (intake.py).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading

from general_functions.Trifle import resource_path

# ── параметры (сервер: config.py) ───────────────────────────────────────────
MAX_SIDE = 1920
LONG_S = 60.0
LONG_SIDE = 1280
MAX_FPS = 30
H264_QP_MIN = 26              # лучше этого кадр не кодируется: меньше QP — крупнее файл
CAP_BPP = 0.08                # потолок потока при сжатии, бит на точку
H264_GOP = 300                # ключевой кадр не реже раза в 10 с (при 30 к/с): файл меньше, чем с 4 с
TARGET_BPP = 0.06             # цель сервера (VIDEO_TARGET_BPP): по ней решается, уйдёт ли видео как есть
READY_SLACK = 1.25            # как VIDEO_REMUX_SLACK: подходит, если поток не выше цели ×1,25
AUDIO_BITRATE = "128k"
THUMB_SIDE = 640              # кадр для обложки: дальше из него делаются заглушка и миниатюра

_NO_WINDOW = 0x08000000       # CREATE_NO_WINDOW: без чёрного окна консоли
_BELOW_NORMAL = 0x00004000    # BELOW_NORMAL_PRIORITY_CLASS: окно приложения не тормозит


class VideoError(Exception):
    pass


def _exe(name):
    suffix = ".exe" if sys.platform.startswith("win") else ""
    custom = os.environ.get("KOVAPP_FFMPEG_DIR")
    places = []
    if custom:
        places.append(os.path.join(custom, name + suffix))
    places.append(resource_path(os.path.join("ffmpeg", name + suffix)))
    for path in places:
        if os.path.isfile(path):
            return path
    found = shutil.which(name)
    return found


def ffmpeg_path():
    return _exe("ffmpeg")


def ffprobe_path():
    return _exe("ffprobe")


def available():
    return bool(ffmpeg_path() and ffprobe_path())


def _popen_kwargs():
    kw = {"stdin": subprocess.DEVNULL}
    if sys.platform.startswith("win"):
        kw["creationflags"] = _NO_WINDOW | _BELOW_NORMAL
    return kw


def probe(path):
    exe = ffprobe_path()
    if not exe:
        raise VideoError("ffprobe не найден")
    cmd = [exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path]
    proc = subprocess.run(cmd, capture_output=True, timeout=120, **_popen_kwargs())
    if proc.returncode != 0:
        raise VideoError((proc.stderr or b"").decode("utf-8", "replace").strip()[-300:] or "ffprobe: ошибка")
    try:
        return json.loads(proc.stdout.decode("utf-8", "replace") or "{}")
    except ValueError:
        raise VideoError("ffprobe: непонятный ответ")


# ── разбор (как у сервера) ──────────────────────────────────────────────────

def fps_of(stream):
    for key in ("avg_frame_rate", "r_frame_rate"):
        raw = str((stream or {}).get(key) or "")
        if "/" in raw:
            a, b = raw.split("/", 1)
            try:
                a, b = float(a), float(b)
                if a > 0 and b > 0:
                    return a / b
            except ValueError:
                pass
    return 30.0


def duration_of(data):
    try:
        d = float(((data or {}).get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        d = 0.0
    if d > 0:
        return d
    for s in (data or {}).get("streams") or []:
        try:
            d = max(d, float(s.get("duration") or 0))
        except (TypeError, ValueError):
            pass
    return d


def rotation_of(stream):
    for item in (stream or {}).get("side_data_list") or []:
        if "rotation" in item:
            try:
                return int(round(float(item["rotation"]))) % 360
            except (TypeError, ValueError):
                pass
    try:
        return int((stream.get("tags") or {}).get("rotate") or 0) % 360
    except (TypeError, ValueError):
        return 0


def side_for(duration):
    if LONG_SIDE and duration > LONG_S:
        return min(MAX_SIDE, LONG_SIDE)
    return MAX_SIDE


def target_bitrate(width, height, fps, max_side=None, bpp=TARGET_BPP):
    width = max(1, int(width or 0))
    height = max(1, int(height or 0))
    fps = min(max(float(fps or 30.0), 1.0), float(MAX_FPS))
    limit = max_side or MAX_SIDE
    side = max(width, height)
    if side > limit:
        k = limit / side
        width, height = int(width * k), int(height * k)
    return int(bpp * width * height * fps)


def source_bitrate(data, video):
    try:
        rate = int(video.get("bit_rate") or 0)
        if rate > 0:
            return rate
    except (TypeError, ValueError):
        pass
    try:
        rate = int(((data or {}).get("format") or {}).get("bit_rate") or 0)
    except (TypeError, ValueError):
        return 0
    for audio in [s for s in (data.get("streams") or []) if s.get("codec_type") == "audio"]:
        try:
            rate -= int(audio.get("bit_rate") or 0) or 160000
        except (TypeError, ValueError):
            rate -= 160000
    return max(0, rate)


def _has_edits(edits):
    if not edits:
        return False
    crop = edits.get("crop") or {}
    try:
        cw, ch = float(crop.get("w", 1) or 1), float(crop.get("h", 1) or 1)
    except (TypeError, ValueError):
        cw = ch = 1.0
    return bool(float(edits.get("trim_start") or 0) > 0 or float(edits.get("trim_end") or 0) > 0
                or int(edits.get("rotate") or 0) % 360 or (cw < 0.999 or ch < 0.999))


def ready_as_is(data, edits=None):
    """Подходит без сжатия: H.264 8 бит 4:2:0, не больше Full HD и 31 кадра/с,
    поток не выше цели с запасом, звук AAC/MP3 одной дорожкой, без поворота."""
    if _has_edits(edits):
        return False
    streams = (data or {}).get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None or video.get("codec_name") != "h264":
        return False
    if video.get("pix_fmt") not in ("yuv420p", "yuvj420p"):
        return False
    if str(video.get("profile") or "").lower() in ("high 10", "high 4:2:2", "high 4:4:4 predictive"):
        return False
    w, h = int(video.get("width") or 0), int(video.get("height") or 0)
    if max(w, h) > MAX_SIDE or fps_of(video) > MAX_FPS + 1 or rotation_of(video):
        return False
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    if len(audios) > 1 or any(a.get("codec_name") not in ("aac", "mp3") for a in audios):
        return False
    src = source_bitrate(data, video)
    target = target_bitrate(w, h, fps_of(video))
    if src and target and src > target * READY_SLACK:
        return False
    return True


def _video_filter(fps_first, side):
    scale = ("scale='min(%d,iw)':'min(%d,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2"
             % (side, side))
    fps = "fps=fps='min(%d,source_fps)'" % MAX_FPS
    return (fps + "," + scale if fps_first else scale + "," + fps) + ",format=yuv420p"


def _edit_args(data, edits):
    pre, vf = [], None
    if not edits:
        return pre, vf
    try:
        start = float(edits.get("trim_start") or 0)
        end = float(edits.get("trim_end") or 0)
        if start > 0:
            pre += ["-ss", "%.3f" % start]
        if end > 0 and end > start:
            pre += ["-to", "%.3f" % end]
        rot = int(edits.get("rotate") or 0) % 360
        crop = edits.get("crop") or {}
        x, y, w, h = (float(crop.get(k, 0)) for k in ("x", "y", "w", "h"))
        if 0 < w <= 1 and 0 < h <= 1 and (w < 0.999 or h < 0.999):
            stream = next((s for s in (data.get("streams") or []) if s.get("codec_type") == "video"), {})
            W, H = int(stream.get("width") or 0), int(stream.get("height") or 0)
            if rotation_of(stream) in (90, 270):
                W, H = H, W           # FFmpeg поворачивает кадр по метке телефона до фильтров: обрезка — по повёрнутому
            if W and H:
                cw = max(16, int(W * w) // 2 * 2)
                ch = max(16, int(H * h) // 2 * 2)
                cx = min(max(0, int(W * x)), W - cw)
                cy = min(max(0, int(H * y)), H - ch)
                vf = "crop=%d:%d:%d:%d" % (cw, ch, cx, cy)
        if rot in (90, 180, 270):
            steps = {90: "transpose=1", 180: "transpose=1,transpose=1", 270: "transpose=2"}[rot]
            vf = vf + "," + steps if vf else steps
    except Exception:
        return [], None
    return pre, vf


def edited_duration(duration, edits):
    try:
        start = float((edits or {}).get("trim_start") or 0)
        end = float((edits or {}).get("trim_end") or 0)
    except (TypeError, ValueError):
        return duration
    if end > start:
        return end - start
    return max(0.0, duration - start) if start > 0 else duration


def plan(path, edits=None):
    """{'mode': 'as-is'|'device'|'server', 'reason', 'data', 'side', 'duration'}"""
    if not available():
        return {"mode": "server", "reason": "нет FFmpeg в комплекте"}
    try:
        data = probe(path)
    except Exception as e:
        return {"mode": "server", "reason": "demux: %s" % str(e)[:120]}
    video = next((s for s in data.get("streams") or [] if s.get("codec_type") == "video"), None)
    if video is None:
        return {"mode": "server", "reason": "demux: в файле нет видеодорожки", "data": data}
    duration = duration_of(data)
    out = {"data": data, "video": video, "duration": duration}
    if ready_as_is(data, edits):
        out.update(mode="as-is", reason="ready")
        return out
    out.update(mode="device", reason="", side=side_for(edited_duration(duration, edits)))
    return out


def compress(path, out_path, p, edits=None, progress=None, cancelled=None):
    """Сжатие по плану p (mode='device'). progress(доля 0..1)."""
    exe = ffmpeg_path()
    data = p.get("data") or {}
    video = p.get("video") or {}
    side = p.get("side") or MAX_SIDE
    pre, crop_vf = _edit_args(data, edits or {})
    vf = _video_filter(fps_of(video) > MAX_FPS + 0.5, side)
    if crop_vf:
        vf = crop_vf + "," + vf
    if video:
        cap = target_bitrate(video.get("width") or side, video.get("height") or side, fps_of(video), side, CAP_BPP)
    else:
        cap = target_bitrate(side, int(side * 9 / 16), MAX_FPS, side, CAP_BPP)
    total = edited_duration(p.get("duration") or 0.0, edits)
    cmd = [exe, "-y", "-hide_banner", "-nostdin", "-loglevel", "error", "-progress", "pipe:1", "-nostats",
           *pre, "-i", path,
           "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
           "-vf", vf,
           "-c:v", "libopenh264", "-profile:v", "high", "-rc_mode", "quality",
           "-b:v", str(cap), "-maxrate", str(cap), "-qmin", str(H264_QP_MIN), "-qmax", "51",
           "-g", str(H264_GOP), "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ac", "2",
           "-movflags", "+faststart", "-f", "mp4", out_path]
    _run(cmd, total, progress, cancelled)
    return out_path


def _run(cmd, total, progress=None, cancelled=None):
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **_popen_kwargs())
    errors = []

    def drain():
        for line in iter(proc.stderr.readline, b""):
            errors.append(line.decode("utf-8", "replace"))
            del errors[:-40]
    t = threading.Thread(target=drain, daemon=True)
    t.start()
    stopped = False
    try:
        for raw in iter(proc.stdout.readline, b""):
            if cancelled and cancelled():
                stopped = True
                proc.kill()
                break
            line = raw.decode("ascii", "replace").strip()
            if progress and total > 0 and line.startswith(("out_time_us=", "out_time_ms=")):
                try:
                    us = int(line.split("=", 1)[1])
                    progress(max(0.0, min(1.0, us / 1e6 / total)))
                except ValueError:
                    pass
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        t.join(timeout=2)
    if stopped or (cancelled and cancelled()):
        raise InterruptedError("Отменено")
    if proc.returncode != 0:
        raise VideoError("FFmpeg не смог сжать видео: " + "".join(errors).strip()[-400:])


def poster(path, at=None, side=THUMB_SIDE):
    """Кадр (JPEG-байты) с секунды min(1, длительность/2) — как у веб-версии."""
    exe = ffmpeg_path()
    if not exe:
        return None
    if at is None:
        try:
            dur = duration_of(probe(path))
        except Exception:
            dur = 0.0
        at = min(1.0, dur / 2) if dur else 0.0
    vf = "scale='min(%d,iw)':'min(%d,ih)':force_original_aspect_ratio=decrease" % (side, side)
    for seek in ([] if not at else ["-ss", "%.3f" % at], []):
        cmd = [exe, "-hide_banner", "-nostdin", "-loglevel", "error", *seek, "-i", path,
               "-frames:v", "1", "-vf", vf, "-q:v", "3", "-f", "image2pipe", "-c:v", "mjpeg", "pipe:1"]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=120, **_popen_kwargs())
        except Exception:
            continue
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout
    return None


def info(path):
    """Ширина, высота и длительность готового файла (для описания на сервер)."""
    data = probe(path)
    video = next((s for s in data.get("streams") or [] if s.get("codec_type") == "video"), {}) or {}
    w, h = int(video.get("width") or 0), int(video.get("height") or 0)
    if rotation_of(video) in (90, 270):
        w, h = h, w
    return {"width": w, "height": h, "duration": duration_of(data)}


def temp_dir():
    return tempfile.mkdtemp(prefix="kovapp-")
