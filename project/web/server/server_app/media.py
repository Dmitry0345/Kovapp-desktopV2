# -*- coding: utf-8 -*-
"""Обработка загружаемых файлов: фото, видео, документы, миниатюры.

Что изменилось по сравнению с версией 1:

* Фотографии больше не заворачиваются в gzip (JPEG/PNG/WebP он не сжимает,
  только тратит процессор). Вместо этого снимок поворачивается по EXIF,
  ужимается до IMAGE_MAX_SIDE по длинной стороне и перекодируется в тот же
  формат с разумным качеством. Формат и расширение не меняются — пользователь
  видит тот же .jpg, что и отправил.
* Для каждой картинки и каждого видео рядом кладётся миниатюра
  `<файл>.thumb.jpg` (THUMB_MAX_SIDE по длинной стороне). Плитки клиентов
  грузят её вместо полного файла: раньше десктоп ради 86-пиксельной плитки
  скачивал и распаковывал файл целиком.
* Видео по-прежнему конвертируется в WebM VP9/Opus (единственный формат,
  который гарантированно играет QtWebEngine десктопа), но с параметрами,
  дающими 2–3-кратный выигрыш во времени при почти том же размере.
* Документы, которые сжимать бесполезно (zip, docx, pdf, …), хранятся как
  есть; остальные — gzip уровня GZIP_LEVEL (6 вместо 9: та же степень,
  в разы быстрее).

Все функции синхронные и рассчитаны на вызов через asyncio.to_thread.
"""

import gzip
import io
import json
import os
import threading
import logging
import shutil
import subprocess
import uuid
import copy
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import  (FILE_STORAGE_ROOT, GZIP_LEVEL, IMAGE_JPEG_QUALITY, IMAGE_KEEP_ORIGINAL, IMAGE_MAX_SIDE,
                     THUMB_JPEG_QUALITY, THUMB_MAX_SIDE, VIDEO_AUDIO_BITRATE, VIDEO_CODEC, VIDEO_CPU_USED,
                     VIDEO_CRF, VIDEO_H264_AUDIO_BITRATE, VIDEO_H264_CRF, VIDEO_H264_PRESET,
                     VIDEO_MAX_FPS, VIDEO_MAX_SIDE, VIDEO_LONG_S, VIDEO_LONG_SIDE, VIDEO_REMUX_READY, VIDEO_SERVER_MAX_SIDE,
                     VIDEO_REMUX_SLACK, VIDEO_TARGET_BPP,
                     VIDEO_CPU_PERCENT, VIDEO_MAX_PARALLEL, VIDEO_NICE,
                     IMAGE_WEBP_QUALITY, IMAGE_WEBP_METHOD, IMAGE_PREFER_WEBP)

logger = logging.getLogger(__name__)

try:
    from PIL import Image, ImageOps
    PIL_AVAILABLE = True
except Exception:  # Pillow не установлен — картинки сохраняются как есть
    Image = ImageOps = None
    PIL_AVAILABLE = False

# Изображения, которые оптимизируем (остальные графические форматы идут как документы)
IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".mkv", ".ts", ".m4v", ".3gp"})
# Уже сжатые форматы: gzip им не помогает
INCOMPRESSIBLE_EXTENSIONS = frozenset({
    ".zip", ".rar", ".7z", ".gz", ".bz2", ".xz", ".zst", ".tgz",
    ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".epub", ".jar", ".apk",
    ".pdf", ".mp3", ".aac", ".ogg", ".m4a", ".flac", ".opus",
    ".heic", ".heif", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif",
})

THUMB_SUFFIX = ".thumb.jpg"


def ext_of(name: str) -> str:
    return Path(str(name or "")).suffix.lower()


def is_image_name(name: str) -> bool:
    return ext_of(name) in IMAGE_EXTENSIONS


def is_video_name(name: str) -> bool:
    return ext_of(name) in VIDEO_EXTENSIONS


def is_media_name(name: str) -> bool:
    return is_image_name(name) or is_video_name(name)


@dataclass
class StoredFile:
    relative_path: str      # путь относительно FILE_STORAGE_ROOT (как в БД)
    display_name: str       # имя, которое видит пользователь
    extension: str          # без точки
    size: int               # размер исходных данных (для документов) / готового файла
    kind: str               # image | video | document
    thumb_path: Optional[str] = None


# ══════════════════════════════════════════════════════════════════════════════
# Изображения
# ══════════════════════════════════════════════════════════════════════════════

def _fit_size(width: int, height: int, max_side: int):
    longest = max(width, height)
    if max_side <= 0 or longest <= max_side:
        return width, height
    ratio = max_side / float(longest)
    return max(1, int(width * ratio)), max(1, int(height * ratio))


def optimize_image(data: bytes, ext: str):
    """Поворот по EXIF, уменьшение до IMAGE_MAX_SIDE и плотное перекодирование.

    Возвращает (байты, расширение). По умолчанию картинка переводится в WebP:
    при том же качестве он на четверть-треть легче JPEG и заметно легче PNG,
    а по нагрузке на процессор сопоставим с обычным JPEG. Если WebP вдруг
    оказался не меньше — остаётся исходный формат. GIF (возможна анимация) и
    файлы, которые Pillow не смог открыть, возвращаются как есть.
    """
    if IMAGE_KEEP_ORIGINAL or not PIL_AVAILABLE or ext == ".gif":
        return data, ext
    if image_is_ready(data, ext):
        return data, ext
    try:
        img = Image.open(io.BytesIO(data))
        _jpeg_draft(img, IMAGE_MAX_SIDE)
        img.load()
    except Exception:
        return data, ext

    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass

    new_size = _fit_size(img.width, img.height, IMAGE_MAX_SIDE)
    resized = new_size != (img.width, img.height)
    if resized:
        img = img.resize(new_size, Image.LANCZOS)

    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)

    best, best_ext = data, ext
    if IMAGE_PREFER_WEBP:
        try:
            buf = io.BytesIO()
            source = img if has_alpha else (img if img.mode in ("RGB", "L") else img.convert("RGB"))
            source.save(buf, "WEBP", quality=IMAGE_WEBP_QUALITY, method=IMAGE_WEBP_METHOD)
            webp = buf.getvalue()
            if webp and (len(webp) < len(best) or resized):
                best, best_ext = webp, ".webp"
        except Exception:
            pass

    # запасной путь: если WebP не вышел, пережимаем в исходном формате
    if best_ext == ext and (resized or ext in (".jpg", ".jpeg")):
        out = io.BytesIO()
        try:
            if ext in (".jpg", ".jpeg"):
                flat = img if img.mode in ("RGB", "L") else img.convert("RGB")
                # subsampling=1 (4:2:2) — цвет теряется меньше, чем при 4:2:0;
                # важно для сканов и фото документов с мелким цветным текстом
                flat.save(out, "JPEG", quality=IMAGE_JPEG_QUALITY, optimize=True,
                          progressive=True, subsampling=1)
            elif ext == ".png":
                img.save(out, "PNG", optimize=True)
            elif ext == ".webp":
                img.save(out, "WEBP", quality=IMAGE_WEBP_QUALITY, method=IMAGE_WEBP_METHOD)
            else:
                out = None
        except Exception:
            out = None
        if out is not None:
            result = out.getvalue()
            if result and (len(result) < len(best) or resized):
                best = result
    return best, best_ext


def _jpeg_draft(img, max_side: int) -> None:
    try:
        if max_side > 0 and img.format == "JPEG" and max(img.size) >= max_side * 2:
            img.draft("RGB", _fit_size(img.width, img.height, max_side))
    except Exception as e:
        logger.debug("draft для JPEG не применён: %s", e)


READY_BYTES_PER_PIXEL = 1.0
_EXIF_ORIENTATION = 0x0112
_EXIF_GPS = 0x8825


def image_is_ready(data: bytes, ext: str) -> bool:
    if not PIL_AVAILABLE or ext not in (".webp", ".jpg", ".jpeg"):
        return False
    try:
        img = Image.open(io.BytesIO(data))
        width, height = img.size
        if IMAGE_MAX_SIDE > 0 and max(width, height) > IMAGE_MAX_SIDE:
            return False
        if len(data) > width * height * READY_BYTES_PER_PIXEL:
            return False
        if (img.format or "").upper() not in ("JPEG", "WEBP"):
            return False
        exif = img.getexif()
        if exif and (exif.get(_EXIF_ORIENTATION, 1) not in (0, 1) or _EXIF_GPS in exif):
            return False
        return True
    except Exception:
        return False


def make_image_thumbnail(data: bytes) -> Optional[bytes]:
    """JPEG-миниатюра THUMB_MAX_SIDE по длинной стороне; None, если картинка не читается."""
    if not PIL_AVAILABLE:
        return None
    try:
        img = Image.open(io.BytesIO(data))
        _jpeg_draft(img, THUMB_MAX_SIDE * 2)
        img.load()
        try:
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass
        img.thumbnail((THUMB_MAX_SIDE, THUMB_MAX_SIDE), Image.LANCZOS)
        if img.mode not in ("RGB", "L"):
            # Прозрачность кладём на белый, как фон карточки
            bg = Image.new("RGB", img.size, (255, 255, 255))
            try:
                bg.paste(img, mask=img.convert("RGBA").split()[-1])
            except Exception:
                bg.paste(img.convert("RGB"))
            img = bg
        out = io.BytesIO()
        img.save(out, "JPEG", quality=THUMB_JPEG_QUALITY, optimize=True)
        return out.getvalue()
    except Exception as e:
        logger.debug("Миниатюра изображения не создана: %s", e)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Видео
# ══════════════════════════════════════════════════════════════════════════════

_FFMPEG_CAPS = {}


def _ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise RuntimeError("ffmpeg не найден в PATH. Debian: sudo apt install -y ffmpeg")
    return path


# Входные файлы — только с диска: плейлист (HLS, concat) под видом видео не заставит ffmpeg ходить
# по сети или подставлять в результат чужие файлы сервера (новые FFmpeg и так ограничивают это,
# но рабочий сервер может стоять на старой сборке)
_INPUT_GUARD = ["-protocol_whitelist", "file,pipe"]
_PLAYLIST_FORMATS = {"hls", "applehttp", "concat", "ffconcat"}


def _guard(command: list) -> list:
    """Ограничение протоколов перед каждым входом (-i) команды ffmpeg."""
    out = []
    for arg in command:
        if arg == "-i":
            out += _INPUT_GUARD
        out.append(arg)
    return out


def _ffprobe() -> str:
    path = shutil.which("ffprobe")
    if not path:
        raise RuntimeError("ffprobe не найден в PATH (ставится вместе с ffmpeg)")
    return path


def _webm_supports_cues_to_front() -> bool:
    """`-cues_to_front` (ffmpeg ≥ 5) переносит индекс в начало файла: плеер
    начинает перемотку до полной загрузки. Проверяем один раз за процесс."""
    if "cues_to_front" not in _FFMPEG_CAPS:
        try:
            proc = subprocess.run([_ffmpeg(), "-hide_banner", "-h", "muxer=webm"],
                                  capture_output=True, text=True, timeout=20)
            _FFMPEG_CAPS["cues_to_front"] = "cues_to_front" in (proc.stdout or "")
        except Exception:
            _FFMPEG_CAPS["cues_to_front"] = False
    return _FFMPEG_CAPS["cues_to_front"]


_PROBE_CACHE = {}
_PROBE_LOCK = threading.Lock()


def probe_media(path: str) -> dict:
    try:
        st = os.stat(path)
        key = (os.path.abspath(path), st.st_size, st.st_mtime_ns)
    except OSError:
        key = None
    if key:
        with _PROBE_LOCK:
            hit = _PROBE_CACHE.get(key)
        if hit is not None:
            return copy.deepcopy(hit)
    proc = subprocess.run([_ffprobe(), "-v", "error", *_INPUT_GUARD, "-print_format", "json",
                           "-show_format", "-show_streams", path],
                          capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe не смог прочитать файл: {(proc.stderr or '').strip()}")
    data = json.loads(proc.stdout or "{}")
    names = set(((data.get("format") or {}).get("format_name") or "").split(","))
    if names & _PLAYLIST_FORMATS:
        raise RuntimeError("это не видео, а список других файлов (%s) — обработка отклонена" % ",".join(sorted(names)))
    if key:
        with _PROBE_LOCK:
            _PROBE_CACHE[key] = data
            while len(_PROBE_CACHE) > 64:
                _PROBE_CACHE.pop(next(iter(_PROBE_CACHE)))
    return copy.deepcopy(data)


def assert_browser_playable_webm(path: str) -> None:
    """WebM должен быть VP8/VP9 (4:2:0) + Opus/Vorbis — иначе QtWebEngine молча виснет."""
    data = probe_media(path)
    fmt = (data.get("format") or {}).get("format_name", "") or ""
    streams = data.get("streams") or []
    if "webm" not in fmt and "matroska" not in fmt:
        raise RuntimeError(f"после конвертации это не WebM (format_name={fmt!r})")
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise RuntimeError("после конвертации в файле нет видеодорожки")
    if video.get("codec_name") not in {"vp8", "vp9"}:
        raise RuntimeError(f"недопустимый видеокодек: {video.get('codec_name')!r}")
    if video.get("codec_name") == "vp9" and video.get("pix_fmt") not in {"yuv420p", "yuv420p10le", "yuv420p12le"}:
        raise RuntimeError(f"VP9 с неподдерживаемым pix_fmt={video.get('pix_fmt')!r}")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if audio is not None and audio.get("codec_name") not in {"opus", "vorbis"}:
        raise RuntimeError(f"недопустимый аудиокодек: {audio.get('codec_name')!r}")


def video_suffix() -> str:
    """Расширение готового файла: .mp4 для H.264, .webm для VP9."""
    return ".webm" if VIDEO_CODEC == "vp9" else ".mp4"


def assert_browser_playable_mp4(path: str) -> None:
    """MP4 должен быть H.264 (4:2:0) + AAC — то, что играет везде без доустановок."""
    data = probe_media(path)
    fmt = (data.get("format") or {}).get("format_name", "") or ""
    streams = data.get("streams") or []
    if "mp4" not in fmt and "mov" not in fmt:
        raise RuntimeError(f"после конвертации это не MP4 (format_name={fmt!r})")
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise RuntimeError("после конвертации в файле нет видеодорожки")
    if video.get("codec_name") != "h264":
        raise RuntimeError(f"недопустимый видеокодек: {video.get('codec_name')!r}")
    if video.get("pix_fmt") not in {"yuv420p", "yuvj420p"}:
        raise RuntimeError(f"H.264 с неподдерживаемым pix_fmt={video.get('pix_fmt')!r}")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if audio is not None and audio.get("codec_name") not in {"aac", "mp3"}:
        raise RuntimeError(f"недопустимый аудиокодек: {audio.get('codec_name')!r}")


def assert_browser_playable(path: str) -> None:
    if VIDEO_CODEC == "vp9":
        assert_browser_playable_webm(path)
    else:
        assert_browser_playable_mp4(path)


def _fps_of(stream: dict) -> float:
    raw = stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/1"
    try:
        num, _, den = raw.partition("/")
        den = float(den or 1)
        return float(num) / den if den else 0.0
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def _duration_of(data: dict) -> float:
    try:
        return float((data.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        return 0.0


def _edited_duration(duration: float, edit: Optional[dict]) -> float:
    """Длительность после обрезки из окна отправки."""
    try:
        start = float((edit or {}).get("trim_start") or 0)
        end = float((edit or {}).get("trim_end") or 0)
    except (TypeError, ValueError):
        return duration
    if end > start:
        return end - start
    return max(0.0, duration - start) if start > 0 else duration


def _side_for(duration: float) -> int:
    """Сторона, до которой сжимаем: длинное видео — до HD (как телефон и браузер), иначе VIDEO_MAX_SIDE."""
    if VIDEO_LONG_SIDE and duration > VIDEO_LONG_S:
        return min(VIDEO_MAX_SIDE, VIDEO_LONG_SIDE)
    return VIDEO_MAX_SIDE


def _target_bitrate(width: int, height: int, fps: float, max_side: Optional[int] = None) -> int:
    """Сколько бит в секунду достаточно для кадра такого размера."""
    width = max(1, int(width or 0))
    height = max(1, int(height or 0))
    fps = min(max(float(fps or 30.0), 1.0), float(VIDEO_MAX_FPS))
    # ограничиваем стороной, до которой всё равно уменьшим
    limit = max_side or VIDEO_MAX_SIDE
    side = max(width, height)
    if side > limit:
        scale = limit / side
        width, height = int(width * scale), int(height * scale)
    return int(VIDEO_TARGET_BPP * width * height * fps)


def _source_bitrate(data: dict, video: dict) -> int:
    try:
        rate = int(video.get("bit_rate") or 0)
        if rate > 0:
            return rate
    except (TypeError, ValueError):
        pass
    try:
        rate = int((data.get("format") or {}).get("bit_rate") or 0)
    except (TypeError, ValueError):
        return 0
    for audio in [s for s in (data.get("streams") or []) if s.get("codec_type") == "audio"]:
        try:
            rate -= int(audio.get("bit_rate") or 0) or 160_000
        except (TypeError, ValueError):
            rate -= 160_000
    return max(0, rate)


def _video_ready(video: dict, max_side: Optional[int] = None) -> bool:
    if video is None or video.get("codec_name") != "h264":
        return False
    if video.get("pix_fmt") not in {"yuv420p", "yuvj420p"}:
        return False
    if (video.get("profile") or "").lower() in {"high 10", "high 4:2:2", "high 4:4:4 predictive"}:
        return False
    width, height = video.get("width") or 0, video.get("height") or 0
    if max(width, height) > (max_side or VIDEO_MAX_SIDE):
        return False
    if _fps_of(video) > VIDEO_MAX_FPS + 1:
        return False
    return True


def remux_plan(input_path: str, slack: Optional[float] = None) -> Optional[str]:
    if not VIDEO_REMUX_READY or VIDEO_CODEC == "vp9":
        return None
    try:
        data = probe_media(input_path)
    except Exception:
        return None
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not _video_ready(video):
        return None
    source = _source_bitrate(data, video)
    target = _target_bitrate(video.get("width") or 0, video.get("height") or 0, _fps_of(video))
    if source and target and source > target * (slack or VIDEO_REMUX_SLACK):
        return None
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    if all(s.get("codec_name") in {"aac", "mp3"} for s in audios):
        return "copy"
    return "audio"


def _can_remux_to_mp4(input_path: str) -> bool:
    return remux_plan(input_path) == "copy"


def _bit_depth(stream: dict) -> int:
    try:
        raw = int(stream.get("bits_per_raw_sample") or 0)
    except (TypeError, ValueError):
        raw = 0
    if raw:
        return raw
    fmt = str(stream.get("pix_fmt") or "")
    return 10 if ("10" in fmt or "12" in fmt) else 8


_video_slot = threading.BoundedSemaphore(max(1, VIDEO_MAX_PARALLEL))   # запасной путь


def _governor():
    from .load_governor import governor
    return governor()


def estimate_video_memory(width: int = 0, height: int = 0, bits: int = 8) -> int:
    from .load_governor import estimate_video_memory as _est
    return _est(width, height, bits)


def video_threads() -> int:
    """Ядер на один ffmpeg. Обычно считает распорядитель; если он почему-то
    недоступен, остаётся прежняя доля от числа ядер."""
    try:
        return _governor().threads()
    except Exception:
        cores = os.cpu_count() or 2
        share = max(1, round(cores * max(5, min(100, VIDEO_CPU_PERCENT)) / 100))
        return max(1, min(cores, share))


def _lower_priority():
    """Понижаем приоритет ffmpeg, чтобы веб-сервер отвечал даже под нагрузкой."""
    try:
        os.nice(max(0, VIDEO_NICE))
    except Exception:
        pass
    try:
        with open("/proc/self/oom_score_adj", "w") as fh:
            fh.write("1000")
    except Exception:
        pass
    try:
        import psutil as _ps
        _ps.Process().ionice(_ps.IOPRIO_CLASS_IDLE)
    except Exception:
        pass


def _duration_seconds(path: str) -> float:
    """Длительность ролика в секундах — знаменатель для процента готовности."""
    try:
        data = probe_media(path)
        value = (data.get("format") or {}).get("duration")
        if value:
            return max(0.0, float(value))
        for stream in data.get("streams") or []:
            if stream.get("codec_type") == "video" and stream.get("duration"):
                return max(0.0, float(stream["duration"]))
    except Exception:
        pass
    return 0.0


def _run_with_progress(command: list, duration: float, on_progress=None,
                       timeout: float = 7200.0, heavy: bool = False) -> subprocess.CompletedProcess:
    cmd = list(command)
    cmd[1:1] = ["-progress", "pipe:1", "-nostats"]
    kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "text": True,
              "bufsize": 1, "stdin": subprocess.DEVNULL}
    if hasattr(os, "nice"):
        kwargs["preexec_fn"] = _lower_priority
    proc = subprocess.Popen(cmd, **kwargs)
    governor = None
    if heavy:
        try:
            governor = _governor()
            governor.register_heavy(proc)
        except Exception as e:
            governor = None
            logger.debug("Вытеснение недоступно: %s", e)
    errors = []

    def drain():
        try:
            for line in proc.stderr:
                errors.append(line)
                del errors[:-200]
        except Exception:
            pass

    def watch():
        started = time.time()
        while proc.poll() is None:
            time.sleep(1.0)
            paused = governor.paused_seconds(proc.pid) if governor else 0.0
            if time.time() - started - paused > timeout:
                logger.warning("ffmpeg превысил %s с работы — снимаю", int(timeout))
                try:
                    if governor:
                        governor.unregister_heavy(proc)
                    proc.kill()
                except Exception:
                    pass
                return

    pump = threading.Thread(target=drain, name="ffmpeg-stderr", daemon=True)
    pump.start()
    guard = threading.Thread(target=watch, name="ffmpeg-watch", daemon=True)
    guard.start()
    last = -1
    try:
        for line in proc.stdout:
            if not on_progress:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key in ("out_time_us", "out_time_ms") and duration > 0:
                try:
                    done = int(value) / 1_000_000.0
                except ValueError:
                    continue
                percent = max(0, min(99, int(done / duration * 100)))
                if percent != last:
                    last = percent
                    try:
                        on_progress(percent)
                    except Exception:
                        pass
            elif key == "progress" and value == "end":
                try:
                    on_progress(100)
                except Exception:
                    pass
    finally:
        try:
            proc.wait(timeout=60)
        except Exception:
            proc.kill()
            proc.wait()
        if governor:
            governor.unregister_heavy(proc)
        pump.join(timeout=5)
        try:
            proc.stdout.close(); proc.stderr.close()
        except Exception:
            pass
    return subprocess.CompletedProcess(cmd, proc.returncode, "", "".join(errors))


def _run_ffmpeg(command: list, vf: Optional[str], on_progress=None, duration: float = 0.0,
                heavy: bool = False) -> None:
    def run(cmd, progress=True):
        if heavy or (on_progress and duration > 0):
            return _run_with_progress(cmd, duration, on_progress if progress else None, heavy=heavy)
        kwargs = {"capture_output": True, "text": True}
        if hasattr(os, "nice"):
            kwargs["preexec_fn"] = _lower_priority
        return subprocess.run(cmd, **kwargs)
    command = _guard(command)
    proc = run(command)
    if proc.returncode == 0:
        return
    if on_progress and duration > 0:
        logger.warning("ffmpeg с отчётом о ходе работы вернул %s, повторяю без него", proc.returncode)
        proc = run(command, progress=False)
        if proc.returncode == 0:
            return
    if vf and "source_fps" in vf:
        simple = vf.replace(f"fps=fps='min({VIDEO_MAX_FPS},source_fps)',", "").replace(
            f",fps=fps='min({VIDEO_MAX_FPS},source_fps)'", "")
        fallback = [c if c != vf else simple for c in command]
        proc = run(fallback)
        if proc.returncode == 0:
            return
    raise RuntimeError("ffmpeg не смог сконвертировать видео:\n" + (proc.stderr or "")[-4000:])


def _video_filter(fps_first: bool = False, side: Optional[int] = None) -> str:
    side = side or VIDEO_MAX_SIDE
    scale = (f"scale='min({side},iw)':'min({side},ih)':force_original_aspect_ratio=decrease:"
             f"force_divisible_by=2")
    fps = f"fps=fps='min({VIDEO_MAX_FPS},source_fps)'"
    if fps_first:
        return f"{fps},{scale},format=yuv420p"
    return f"{scale},{fps},format=yuv420p"


def _has_edits(edit: Optional[dict]) -> bool:
    if not edit:
        return False
    pre, vf = _edit_args("", {k: v for k, v in edit.items() if k != "crop"})
    crop = edit.get("crop") or {}
    try:
        w, h = float(crop.get("w", 1) or 1), float(crop.get("h", 1) or 1)
    except (TypeError, ValueError):
        w = h = 1.0
    return bool(pre or vf or (0 < w <= 1 and 0 < h <= 1 and (w < 0.999 or h < 0.999)))


def _video_brief(path: str) -> str:
    """Коротко о видео для журнала: стороны, кодек, длительность, поток, размер."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return "файла нет"
    try:
        data = probe_media(path)
        v = next((x for x in (data.get("streams") or []) if x.get("codec_type") == "video"), {}) or {}
        duration = float((data.get("format") or {}).get("duration") or 0)
        rate = size * 8 / duration / 1e6 if duration > 0 else 0.0
        return "%sx%s %s, %.1f с, %.1f Мбит/с, %.1f МБ" % (v.get("width") or "?", v.get("height") or "?",
                                                         (v.get("codec_name") or "?").upper(), duration, rate, size / 1048576)
    except Exception:
        return "%.1f МБ" % (size / 1048576)


def process_video_sync(input_path: str, output_path: str, edit: Optional[dict] = None,
                       on_progress=None, slack: Optional[float] = None, origin: str = "") -> None:
    """Сохранение видео. В журнал — одна строка: откуда пришло, что пришло, что сохранено и как."""
    started = time.time()
    before = _video_brief(input_path)
    how = _process_video_routed(input_path, output_path, edit, on_progress, slack)
    logger.info("Видео: %s. Пришло: %s → сохранено: %s (%s, %.1f с)", origin or "откуда — не указано",
                before, _video_brief(output_path), how, time.time() - started)


def _process_video_routed(input_path: str, output_path: str, edit: Optional[dict] = None,
                          on_progress=None, slack: Optional[float] = None) -> str:
    edit = edit or {}
    try:
        g = _governor()
    except Exception as e:
        logger.debug("Распорядитель недоступен, работаем по одному: %s", e)
        with _video_slot:
            _process_video_sync(input_path, output_path, edit, on_progress)
        return "обработано сервером"
    plan = None if _has_edits(edit) else remux_plan(input_path, slack)
    if plan:
        with g.slot("light"):
            if _light_pass(input_path, output_path, plan, on_progress):
                return "без перекодирования изображения: " + ("переупаковано" if plan == "copy" else "пересжат только звук")
    stream = {}
    try:
        data = probe_media(input_path)
        stream = next((x for x in (data.get("streams") or []) if x.get("codec_type") == "video"), {}) or {}
    except Exception:
        pass
    need = estimate_video_memory(int(stream.get("width") or 0), int(stream.get("height") or 0),
                                 _bit_depth(stream))
    with g.slot("video", need):
        _process_video_sync(input_path, output_path, edit, on_progress, allow_light=False)
    return "пережато сервером"


def _light_pass(input_path: str, output_path: str, plan: str, on_progress=None) -> bool:
    audio = ["-c", "copy"] if plan == "copy" else ["-c:v", "copy", "-c:a", "aac",
                                                   "-b:a", VIDEO_H264_AUDIO_BITRATE, "-ac", "2"]
    command = [_ffmpeg(), "-y", "-hide_banner", "-nostdin", "-i", input_path,
               "-map", "0:v:0", "-map", "0:a?", "-sn", "-dn", *audio,
               "-movflags", "+faststart", "-f", "mp4", output_path]
    try:
        _run_ffmpeg(command, None)
        assert_browser_playable_mp4(output_path)
        if on_progress:
            on_progress(100)
        logger.debug("Видео принято без перекодирования изображения (%s): %s", plan, input_path)
        return True
    except Exception as e:
        logger.warning("Быстрая подготовка видео не удалась (%s) — перекодируем", e)
        try:
            Path(output_path).unlink(missing_ok=True)
        except Exception:
            pass
        return False


def _edit_args(input_path: str, edit: dict):
    pre, vf = [], None
    if not edit:
        return pre, vf
    try:
        start = float(edit.get("trim_start") or 0)
        end = float(edit.get("trim_end") or 0)
        if start > 0:
            pre += ["-ss", f"{start:.3f}"]
        if end > 0 and end > start:
            pre += ["-to", f"{end:.3f}"]
        rot = int(edit.get("rotate") or 0) % 360
        crop = edit.get("crop") or {}
        x, y, w, h = (float(crop.get(k, 0)) for k in ("x", "y", "w", "h"))
        if 0 < w <= 1 and 0 < h <= 1 and (w < 0.999 or h < 0.999) and input_path:
            data = probe_media(input_path)
            stream = next((s for s in (data.get("streams") or []) if s.get("codec_type") == "video"), {})
            W, H = int(stream.get("width") or 0), int(stream.get("height") or 0)
            if W and H:
                cw = max(16, int(W * w) // 2 * 2)
                ch = max(16, int(H * h) // 2 * 2)
                cx = min(max(0, int(W * x)), W - cw)
                cy = min(max(0, int(H * y)), H - ch)
                vf = f"crop={cw}:{ch}:{cx}:{cy}"
        if rot in (90, 180, 270):
            steps = {90: "transpose=1", 180: "transpose=1,transpose=1", 270: "transpose=2"}[rot]
            vf = f"{vf},{steps}" if vf else steps
    except Exception:
        return [], None
    return pre, vf


def _process_video_sync(input_path: str, output_path: str, edit: Optional[dict] = None,
                        on_progress=None, allow_light: bool = True, side: Optional[int] = None) -> None:
    pre, crop_vf = _edit_args(input_path, edit or {})
    if allow_light and not pre and not crop_vf:
        plan = remux_plan(input_path)
        if plan and _light_pass(input_path, output_path, plan, on_progress):
            return
    stream, probe = {}, {}
    try:
        probe = probe_media(input_path)
        stream = next((x for x in (probe.get("streams") or []) if x.get("codec_type") == "video"), {}) or {}
    except Exception:
        pass
    if not side:
        # днём — как на устройствах: длинное видео (после обрезки) до HD, остальное до Full HD
        side = _side_for(_edited_duration(_duration_of(probe), edit))
        if VIDEO_SERVER_MAX_SIDE > 0:
            side = min(side, VIDEO_SERVER_MAX_SIDE)   # режим экономии сервера
    threads = str(video_threads())
    vf = _video_filter(fps_first=_fps_of(stream) > VIDEO_MAX_FPS + 0.5, side=side)
    if crop_vf:
        vf = crop_vf + "," + vf
    base = [
        _ffmpeg(), "-y", "-hide_banner", "-nostdin",
        "-filter_threads", threads,
        "-threads", threads,
        *pre,
        "-i", input_path,
        "-map", "0:v:0", "-map", "0:a:0?",   # одна (первая) дорожка звука — как на устройствах
        "-sn", "-dn",
        "-vf", vf,
    ]
    if VIDEO_CODEC == "vp9":
        command = base + [
            "-c:v", "libvpx-vp9", "-profile:v", "0", "-pix_fmt", "yuv420p",
            "-crf", str(VIDEO_CRF), "-b:v", "0",
            "-deadline", "good", "-cpu-used", str(VIDEO_CPU_USED),
            "-row-mt", "1", "-tile-columns", "2",
            "-g", "240", "-threads", threads,
            "-c:a", "libopus", "-b:a", VIDEO_AUDIO_BITRATE, "-ac", "2",
        ]
        if _webm_supports_cues_to_front():
            command += ["-cues_to_front", "1"]
        command += ["-f", "webm", output_path]
    else:
        if stream:
            cap = _target_bitrate(stream.get("width") or side,
                                  stream.get("height") or side, _fps_of(stream), side)
        else:
            cap = _target_bitrate(side, int(side * 9 / 16), VIDEO_MAX_FPS, side)
        command = base + [
            "-c:v", "libx264", "-preset", VIDEO_H264_PRESET, "-crf", str(VIDEO_H264_CRF),
            "-maxrate", str(cap), "-bufsize", str(cap * 2),
            "-profile:v", "high", "-level", "4.1", "-pix_fmt", "yuv420p",
            "-g", "120", "-threads", threads,
            "-c:a", "aac", "-b:a", VIDEO_H264_AUDIO_BITRATE, "-ac", "2",
            "-movflags", "+faststart",
            "-f", "mp4", output_path,
        ]
    _run_ffmpeg(command, vf, on_progress, _duration_seconds(input_path) if on_progress else 0.0,
                heavy=True)
    assert_browser_playable(output_path)


def make_video_poster(video_path: str) -> Optional[bytes]:
    """Кадр с первой секунды, уменьшенный до THUMB_MAX_SIDE, как JPEG."""
    try:
        proc = subprocess.run(
            [_ffmpeg(), "-hide_banner", "-nostdin", "-loglevel", "error",
             "-ss", "1", *_INPUT_GUARD, "-i", video_path, "-frames:v", "1",
             "-vf", f"scale='min({THUMB_MAX_SIDE},iw)':'min({THUMB_MAX_SIDE},ih)':force_original_aspect_ratio=decrease",
             "-q:v", "4", "-f", "image2", "pipe:1"],
            capture_output=True, timeout=120)
        if proc.returncode != 0 or not proc.stdout:
            # Ролик короче секунды — берём самый первый кадр
            proc = subprocess.run(
                [_ffmpeg(), "-hide_banner", "-nostdin", "-loglevel", "error",
                 *_INPUT_GUARD, "-i", video_path, "-frames:v", "1",
                 "-vf", f"scale='min({THUMB_MAX_SIDE},iw)':'min({THUMB_MAX_SIDE},ih)':force_original_aspect_ratio=decrease",
                 "-q:v", "4", "-f", "image2", "pipe:1"],
                capture_output=True, timeout=120)
        return proc.stdout or None
    except Exception as e:
        logger.debug("Постер видео не создан: %s", e)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Единая точка сохранения
# ══════════════════════════════════════════════════════════════════════════════

def _thumb_path_for(stored: Path) -> Path:
    base = stored
    if base.suffix.lower() == ".gz":
        base = base.with_suffix("")
    return base.with_name(base.name + THUMB_SUFFIX)


def thumb_path_for_relative(relative_path: str) -> Path:
    return _thumb_path_for(Path(FILE_STORAGE_ROOT) / str(relative_path).replace("\\", "/").lstrip("/"))


def _relative(path: Path) -> str:
    return str(path.relative_to(Path(FILE_STORAGE_ROOT).resolve())).replace("\\", "/")


def store_document(folder: Path, original_name: str, content: bytes) -> StoredFile:
    """Документ: gzip для сжимаемых типов, как есть — для уже сжатых."""
    folder.mkdir(parents=True, exist_ok=True)
    ext = ext_of(original_name)
    if ext in INCOMPRESSIBLE_EXTENSIONS:
        target = folder / f"{uuid.uuid4()}{ext}"
        target.write_bytes(content)
    else:
        target = folder / f"{uuid.uuid4()}{ext}.gz"
        with gzip.open(target, "wb", compresslevel=GZIP_LEVEL) as fh:
            fh.write(content)
    return StoredFile(_relative(target.resolve()), Path(original_name).name,
                      ext[1:] if ext else "", len(content), "document")


def store_document_path(folder: Path, original_name: str, temp_path: Path, compress: bool = True) -> StoredFile:
    """compress=False — устройство уже выяснило, что сжатие ничего не даст."""
    folder.mkdir(parents=True, exist_ok=True)
    ext = ext_of(original_name)
    size = Path(temp_path).stat().st_size
    if ext in INCOMPRESSIBLE_EXTENSIONS or not compress:
        target = folder / f"{uuid.uuid4()}{ext}"
        shutil.move(str(temp_path), str(target))
    else:
        target = folder / f"{uuid.uuid4()}{ext}.gz"
        with open(temp_path, "rb") as src, gzip.open(target, "wb", compresslevel=GZIP_LEVEL) as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
    return StoredFile(_relative(target.resolve()), Path(original_name).name,
                      ext[1:] if ext else "", size, "document")


def store_document_packed(folder: Path, original_name: str, packed_path: Path, size: int) -> StoredFile:
    """Документ, сжатый на устройстве и уже проверенный: кладётся как есть, без пересжатия."""
    folder.mkdir(parents=True, exist_ok=True)
    ext = ext_of(original_name)
    target = folder / f"{uuid.uuid4()}{ext}.gz"
    shutil.move(str(packed_path), str(target))
    return StoredFile(_relative(target.resolve()), Path(original_name).name,
                      ext[1:] if ext else "", size, "document")


def store_image(folder: Path, original_name: str, content: bytes, thumb_ready: bool = False) -> StoredFile:
    folder.mkdir(parents=True, exist_ok=True)
    ext = ext_of(original_name)
    optimized, new_ext = optimize_image(content, ext)
    target = folder / f"{uuid.uuid4()}{new_ext}"
    target.write_bytes(optimized)
    thumb = None if thumb_ready else make_image_thumbnail(optimized)
    thumb_rel = None
    if thumb:
        tp = _thumb_path_for(target)
        tp.write_bytes(thumb)
        thumb_rel = _relative(tp.resolve())
    # имя для показа меняем вместе с форматом, иначе клиент получит .jpg с webp внутри
    display = Path(original_name).name
    if new_ext != ext:
        display = Path(original_name).stem + new_ext
    return StoredFile(_relative(target.resolve()), display,
                      new_ext[1:] if new_ext else "", len(optimized), "image", thumb_rel)


def store_video_from_temp(folder: Path, original_name: str, temp_path: Path,
                          edit: Optional[dict] = None, on_progress=None, slack: Optional[float] = None,
                          poster_ready: bool = False, origin: str = "") -> StoredFile:
    """Готовит видео к раздаче внутри folder и делает постер.

    Расширение зависит от VIDEO_CODEC: .mp4 (H.264, по умолчанию) или .webm (VP9).
    edit — правки из окна отправки: {trim_start, trim_end, crop:{x,y,w,h}}
    (в долях от 0 до 1). Применяются тем же проходом, что и сжатие.
    """
    folder.mkdir(parents=True, exist_ok=True)
    stem = Path(original_name).stem.strip() or "video"
    suffix = video_suffix()
    target = folder / f"{uuid.uuid4().hex}{suffix}"
    try:
        process_video_sync(str(temp_path), str(target), edit or {}, on_progress, slack, origin)
    except Exception:
        if target.exists():
            target.unlink(missing_ok=True)
        raise
    thumb_rel = None
    poster = None if poster_ready else make_video_poster(str(target))
    if poster:
        tp = _thumb_path_for(target)
        tp.write_bytes(poster)
        thumb_rel = _relative(tp.resolve())
    return StoredFile(_relative(target.resolve()), f"{stem}{suffix}", suffix[1:],
                      target.stat().st_size, "video", thumb_rel)


def read_stored_bytes(stored: Path) -> bytes:
    """Читает файл хранилища, распаковывая gzip по сигнатуре, а не по расширению."""
    data = stored.read_bytes()
    if data[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(data)
        except Exception:
            return data
    return data


def ensure_thumbnail(stored: Path, filename_hint: str) -> Optional[Path]:
    """Возвращает путь к миниатюре, создавая её при первом обращении.

    Нужна для файлов, загруженных до версии 2 (лежат в gzip, без миниатюр).
    """
    tp = _thumb_path_for(stored)
    if tp.is_file():
        return tp
    if not stored.is_file():
        return None
    name = filename_hint or stored.name
    thumb = None
    if is_video_name(name) or stored.suffix.lower() in (".webm", ".mp4"):
        thumb = make_video_poster(str(stored))
    elif is_image_name(name) or is_image_name(stored.name):
        thumb = make_image_thumbnail(read_stored_bytes(stored))
    if not thumb:
        return None
    try:
        tp.write_bytes(thumb)
    except Exception as e:
        logger.debug("Не удалось сохранить миниатюру %s: %s", tp, e)
        return None
    return tp


def delete_thumbnail(relative_path: str) -> None:
    try:
        tp = thumb_path_for_relative(relative_path)
        if tp.is_file():
            tp.unlink()
    except Exception:
        pass
