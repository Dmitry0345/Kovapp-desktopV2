"""
Ночное пережатие видео.

Днём видео, которые «уже подходят», сервер принимает как есть — человек не ждёт сжатия.
Ночью (VIDEO_NIGHT_HOUR) сервер пережимает видео за прошедшие дни (VIDEO_NIGHT_LOOKBACK_DAYS), если это заметно уменьшит файл.
Перед полным сжатием длинного видео — проба (VIDEO_NIGHT_SAMPLE_S секунд из середины): мал выигрыш — полное
сжатие не нужно; есть выигрыш — он записывается, и видео остаётся в очереди, пока не будет сжато целиком;
принятые без сжатия на устройстве — первыми. Ждёт окончания резервной копии и сжатия таблиц базы:
длинные (дольше VIDEO_LONG_S) — до VIDEO_LONG_SIDE, остальные — если поток выше нормы.
Файл заменяется, только если стал меньше хотя бы на VIDEO_NIGHT_MIN_GAIN и длительность совпала;
размер обновляется в реестре, файлах задач, этапов, проектов и сообщениях чата.
Вручную: python -m server.server_app.night_video --now
"""
import argparse
import os
import time
from datetime import datetime, timedelta
from typing import Callable, Optional

from .config import (FILE_STORAGE_ROOT, logger, VIDEO_CODEC, VIDEO_REMUX_SLACK, VIDEO_NIGHT_HOUR,
                     VIDEO_NIGHT_END_HOUR, VIDEO_NIGHT_MIN_GAIN, VIDEO_NIGHT_LOOKBACK_DAYS, VIDEO_NIGHT_SAMPLE_S,
                     VIDEO_NIGHT_SAMPLE_MIN_S, VIDEO_NIGHT_SAMPLE_MARGIN, VIDEO_NIGHT_MAX_ATTEMPTS)
from . import media

VIDEO_EXT = {"mp4", "mov", "m4v", "mkv"}


def plan(path: str) -> Optional[int]:
    """Сторона, до которой стоит пережать видео, или None — файл и так компактен."""
    data = media.probe_media(path)
    video = next((s for s in (data.get("streams") or []) if s.get("codec_type") == "video"), None)
    if not video:
        return None
    side = media._side_for(media._duration_of(data))
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    if max(width, height) > side:
        return side
    source = media._source_bitrate(data, video)
    target = media._target_bitrate(width, height, media._fps_of(video), side)
    return side if source and target and source > target * VIDEO_REMUX_SLACK else None


def _same_length(a: str, b: str) -> bool:
    try:
        da, db_ = media._duration_of(media.probe_media(a)), media._duration_of(media.probe_media(b))
    except Exception:
        return False
    return da > 0 and abs(da - db_) <= max(1.0, da * 0.01)


def _slotted_transcode(side: int) -> Callable[[str, str], None]:
    """Перекодирование с местом у распорядителя и в общем на машину пределе: ночью не больше, чем днём."""
    def transcode(src, dst):
        data = media.probe_media(src)
        v = next((x for x in (data.get("streams") or []) if x.get("codec_type") == "video"), {}) or {}
        need = media.estimate_video_memory(int(v.get("width") or 0), int(v.get("height") or 0), media._bit_depth(v))
        try:
            slot = media._governor().slot("video", need)
        except Exception:
            slot = media._video_slot
        with slot:
            media._process_video_sync(src, dst, None, None, allow_light=False, side=side)
    return transcode


def sample_gain(abs_path: str, side: int, duration: float,
                transcode: Optional[Callable[[str, str], None]] = None) -> Optional[float]:
    """Проба: VIDEO_NIGHT_SAMPLE_S секунд из середины копируются как есть и пережимаются теми же настройками.
    Выигрыш пробы (0…1) оценивает выигрыш всего файла. None — оценить не удалось (тогда — полное сжатие)."""
    seconds = float(VIDEO_NIGHT_SAMPLE_S)
    base = os.path.splitext(abs_path)[0]
    src, out = base + ".sample-src.mp4", base + ".sample-out.mp4"
    try:
        start = max(0.0, duration / 2 - seconds / 2)
        media._run_ffmpeg([media._ffmpeg(), "-y", "-hide_banner", "-nostdin", "-ss", "%.2f" % start, "-i", abs_path,
                           "-t", "%.2f" % seconds, "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", "-f", "mp4", src], None)
        (transcode or _slotted_transcode(side))(src, out)
        before, after = os.path.getsize(src), os.path.getsize(out)
        if before <= 0 or after <= 0:
            return None
        return 1.0 - after / before
    except Exception:
        logger.warning("Ночное сжатие видео: проба %s не удалась — будет полное сжатие", abs_path, exc_info=True)
        return None
    finally:
        for leftover in (src, out):
            try:
                if os.path.exists(leftover):
                    os.remove(leftover)
            except OSError:
                pass


def recompress(abs_path: str, side: int, transcode: Optional[Callable[[str, str], None]] = None) -> Optional[int]:
    """Пережать во временный файл и заменить исходный, если стал заметно меньше. Новый размер или None."""
    if transcode is None:
        transcode = _slotted_transcode(side)
    old = os.path.getsize(abs_path)
    base, ext = os.path.splitext(abs_path)
    tmp = base + ".night" + (ext or ".mp4")
    try:
        transcode(abs_path, tmp)
        new = os.path.getsize(tmp) if os.path.exists(tmp) else 0
        if new <= 0 or new > old * (1 - VIDEO_NIGHT_MIN_GAIN) or not _same_length(abs_path, tmp):
            return None
        os.replace(tmp, abs_path)   # атомарно: кто сейчас читает файл, дочитает прежний
        return new
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def in_window(hour: int, start: int = VIDEO_NIGHT_HOUR, end: int = VIDEO_NIGHT_END_HOUR) -> bool:
    """Ночное окно [start, end) по часам сервера, в том числе через полночь."""
    return start <= hour < end if start < end else (hour >= start or hour < end)


def _busy() -> str:
    """Чем занята машина ночью: резервная копия или перестройка таблиц базы. Пусто — свободна."""
    try:
        from .backup.services import backup_manager
        if backup_manager._run_lock.locked():
            return "резервная копия"
    except Exception:
        pass
    try:
        from . import table_compression
        if table_compression.busy():
            return "сжатие таблиц базы"
    except Exception:
        pass
    return ""


def _wait_quiet(window: bool) -> bool:
    """Ждёт, пока закончатся копия базы и сжатие таблиц. False — ночное окно закончилось."""
    noted = False
    while True:
        reason = _busy()
        if not reason:
            return True
        if window and not in_window(datetime.now().hour):
            return False
        if not noted:
            logger.info("Ночное сжатие видео ждёт: идёт %s", reason)
            noted = True
        time.sleep(30)


def _session(name: str):
    from . import core
    gen = getattr(core, name)()
    return gen, next(gen)


def _dims(path: str):
    try:
        data = media.probe_media(path)
        v = next((s for s in (data.get("streams") or []) if s.get("codec_type") == "video"), {}) or {}
        return v.get("width"), v.get("height")
    except Exception:
        return None, None


def _update_sizes(rel: str, size: int, width, height) -> None:
    """Новый размер — везде, где он показывается."""
    from server.database.models import FileBlob, TaskFile, TaskStageFile, ProjectFile
    gen, db = _session("get_db")
    try:
        db.query(FileBlob).filter(FileBlob.stored_path == rel).update(
            {"size": size, "width": width, "height": height}, synchronize_session=False)
        for model in (TaskFile, TaskStageFile, ProjectFile):
            db.query(model).filter(model.filepath == rel).update({"size": size}, synchronize_session=False)
        db.commit()
    finally:
        gen.close()
    try:
        from .chat_models import MessageModel
        gen, cdb = _session("get_chat_db")
        try:
            cdb.query(MessageModel).filter(MessageModel.file_path == rel).update({"file_size": size}, synchronize_session=False)
            cdb.commit()
        finally:
            gen.close()
    except Exception:
        logger.warning("Ночное сжатие видео: размер в сообщениях чата не обновлён (%s)", rel, exc_info=True)


def process_one(path: str, estimate: Optional[float], on_estimate: Callable[[float], None],
                window_open: Callable[[], bool], plan_fn=None, duration_fn=None, sample_fn=None,
                recompress_fn=None) -> dict:
    """Одно видео: оценка по описанию → проба → полное сжатие.

    Возвращает action: nothing (сжимать нечего), skip (проба: выигрыш мал), deferred (после пробы окно закончилось —
    видео остаётся в очереди), replaced / kept (полное сжатие сделано: файл заменён / прежний меньше), failed (сбой).
    Выигрыш пробы сохраняется (on_estimate) до полного сжатия: такое видео не выпадет из очереди, даже если
    полное сжатие прервётся или окно закончится."""
    plan_fn = plan_fn or plan
    duration_fn = duration_fn or (lambda p: media._duration_of(media.probe_media(p)))
    sample_fn = sample_fn or sample_gain
    recompress_fn = recompress_fn or recompress
    side = plan_fn(path) if os.path.exists(path) else None
    if not side:
        return {"action": "nothing"}
    if estimate is None and VIDEO_NIGHT_SAMPLE_S > 0:
        duration = duration_fn(path)
        if duration >= VIDEO_NIGHT_SAMPLE_MIN_S:
            gain = sample_fn(path, side, duration)
            if gain is not None:
                if gain < VIDEO_NIGHT_MIN_GAIN - VIDEO_NIGHT_SAMPLE_MARGIN:
                    return {"action": "skip", "estimate": gain}
                estimate = gain
                on_estimate(gain)          # до полного сжатия: при сбое или конце окна видео останется в очереди
                if not window_open():
                    return {"action": "deferred", "estimate": gain}
    try:
        old = os.path.getsize(path)
        new = recompress_fn(path, side)
    except Exception as e:
        return {"action": "failed", "estimate": estimate, "error": "%s: %s" % (type(e).__name__, e)}
    return {"action": "replaced" if new else "kept", "estimate": estimate, "side": side, "old": old, "new": new}


def run_items(todo, window: bool, ops) -> dict:
    """Очередь видео: что отметить проверенным, а что оставить на следующую ночь.

    todo — [(id, путь относительно хранилища, оценка пробы, сбоев)]; ops — действия с базой
    (mark, save_estimate, failed, update_sizes), в тестах — подмена."""
    checked = shrunk = saved = 0
    window_open = (lambda: in_window(datetime.now().hour)) if window else (lambda: True)
    for blob_id, rel, estimate, attempts in todo:
        if not window_open() or not _wait_quiet(window):
            logger.info("Ночное сжатие видео: окно закончилось, осталось %d — продолжим следующей ночью", len(todo) - checked)
            break
        path = os.path.join(FILE_STORAGE_ROOT, rel)
        t0 = time.time()
        try:
            r = process_one(path, estimate, lambda gain, _id=blob_id: ops.save_estimate(_id, gain), window_open)
        except Exception as e:     # оценка по описанию не удалась: видео остаётся, как при сбое сжатия
            r = {"action": "failed", "estimate": estimate, "error": "%s: %s" % (type(e).__name__, e)}
        action = r["action"]
        if action == "deferred":
            logger.info("Ночное сжатие видео: %s — проба ~%.0f%%, окно закончилось; полное сжатие — следующей ночью первым",
                        rel, 100 * (r.get("estimate") or 0))
            break
        if action == "failed":
            tries = ops.failed(blob_id)
            if tries >= VIDEO_NIGHT_MAX_ATTEMPTS:
                logger.warning("Ночное сжатие видео: %s не обработан %d раз — больше не пробуем (%s)", rel, tries, r.get("error"))
                ops.mark(blob_id)
            else:
                logger.warning("Ночное сжатие видео: %s не обработан (%s) — повтор следующей ночью", rel, r.get("error"))
            checked += 1
            continue
        if action == "replaced":
            width, height = _dims(path)
            ops.update_sizes(rel, r["new"], width, height)
            shrunk += 1
            saved += r["old"] - r["new"]
            logger.info("Ночное сжатие видео: %s — %.1f → %.1f МБ (до %d) за %.0f с%s", rel, r["old"] / 1048576,
                        r["new"] / 1048576, r["side"], time.time() - t0,
                        "" if r.get("estimate") is None else "; проба обещала ~%.0f%%" % (100 * r["estimate"]))
        elif action == "skip":
            logger.info("Ночное сжатие видео: %s — проба %d с даёт ~%.0f%%, меньше порога %.0f%%: полное сжатие не нужно",
                        rel, VIDEO_NIGHT_SAMPLE_S, 100 * (r.get("estimate") or 0), 100 * VIDEO_NIGHT_MIN_GAIN)
        elif action == "kept" and r.get("estimate") is not None:
            logger.info("Ночное сжатие видео: %s — проба обещала ~%.0f%%, полное сжатие меньше порога: файл прежний",
                        rel, 100 * r["estimate"])
        ops.mark(blob_id)
        checked += 1
    logger.info("Ночное сжатие видео: проверено %d, пережато %d, освобождено %.1f МБ", checked, shrunk, saved / 1048576)
    return {"checked": checked, "shrunk": shrunk, "saved": saved}


class _DbOps:
    """Действия ночного сжатия с базой."""

    @staticmethod
    def _update(blob_id: int, values: dict) -> None:
        from server.database.models import FileBlob
        gen, db = _session("get_db")
        try:
            db.query(FileBlob).filter(FileBlob.id == blob_id).update(values, synchronize_session=False)
            db.commit()
        finally:
            gen.close()

    def mark(self, blob_id: int) -> None:
        self._update(blob_id, {"night_checked_at": datetime.utcnow(), "night_estimate": None})

    def save_estimate(self, blob_id: int, gain: float) -> None:
        self._update(blob_id, {"night_estimate": round(float(gain), 4)})

    def failed(self, blob_id: int) -> int:
        from server.database.models import FileBlob
        gen, db = _session("get_db")
        try:
            row = db.query(FileBlob).filter(FileBlob.id == blob_id).first()
            tries = int(getattr(row, "night_attempts", 0) or 0) + 1 if row else VIDEO_NIGHT_MAX_ATTEMPTS
            if row is not None:
                row.night_attempts = tries
                db.commit()
            return tries
        finally:
            gen.close()

    @staticmethod
    def update_sizes(rel: str, size: int, width, height) -> None:
        _update_sizes(rel, size, width, height)


def run_once(window: bool = True) -> dict:
    """Пережать видео за прошедшие сутки (крупные — первыми). window=False — без ночного окна (вручную)."""
    if VIDEO_CODEC != "h264":
        logger.info("Ночное сжатие видео пропущено: сервер сжимает в %s", VIDEO_CODEC)
        return {"checked": 0, "shrunk": 0, "saved": 0}
    from sqlalchemy import or_
    from server.database.models import FileBlob
    gen, db = _session("get_db")
    try:
        # за несколько дней; видео, у которых проба уже показала выигрыш или полное сжатие сорвалось, —
        # независимо от даты: из очереди они выходят только сжатыми (или после VIDEO_NIGHT_MAX_ATTEMPTS сбоев).
        # Порядок: подтверждённые пробой (с наибольшим выигрышем), затем принятые без сжатия на устройстве, крупные
        since = datetime.utcnow() - timedelta(days=max(1, VIDEO_NIGHT_LOOKBACK_DAYS))
        rows = (db.query(FileBlob).filter(FileBlob.night_checked_at.is_(None),
                                          or_(FileBlob.created_at >= since, FileBlob.night_estimate.isnot(None),
                                              FileBlob.night_attempts > 0))
                .order_by(FileBlob.night_estimate.is_(None), FileBlob.night_estimate.desc(),
                          FileBlob.verified.desc(), FileBlob.size.desc()).all())
        todo = [(r.id, r.stored_path, r.night_estimate, int(r.night_attempts or 0)) for r in rows
                if r.kind == "video" or (r.extension or "").lower().lstrip(".") in VIDEO_EXT]
    finally:
        gen.close()
    return run_items(todo, window, _DbOps())


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Ночное пережатие видео за сутки")
    p.add_argument("--now", action="store_true", help="запустить сразу, без ночного окна")
    print(run_once(window=not p.parse_args().now))
