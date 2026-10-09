"""Ночное пережатие видео: что пережимать, когда заменять файл, ночное окно. Настоящий night_video.py."""
import os
import secrets
import sys
import tempfile
import types

os.environ.setdefault("JWT_SECRET_KEY", secrets.token_hex(32))   # только для загрузки настроек в тесте
for name, attrs in (("sqlalchemy", {}), ("sqlalchemy.pool", {"QueuePool": object}), ("fastapi", {}),
                    ("fastapi.staticfiles", {"StaticFiles": type("StaticFiles", (), {"__init__": lambda self, *a, **k: None})})):
    if name not in sys.modules:
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod

from server.server_app import media, night_video  # noqa: E402

results = []


def check(name, ok, extra=""):
    results.append(ok)
    print(("  ok   " if ok else "  FAIL ") + name + (("  — %s" % extra) if extra and not ok else ""))


def probe(w, h, duration, bpp):
    rate = int(bpp * w * h * 30)
    return {"format": {"duration": str(duration), "bit_rate": str(rate + 128000)},
            "streams": [{"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p", "profile": "High",
                         "width": w, "height": h, "avg_frame_rate": "30/1", "r_frame_rate": "30/1", "bit_rate": str(rate)},
                        {"codec_type": "audio", "codec_name": "aac", "bit_rate": "128000"}]}


def main():
    cases = [("длинное 1080p с умеренным потоком — до 1280", probe(1080, 1920, 900, 0.06), 1280),
             ("короткое 1080p с умеренным потоком — не трогаем", probe(1080, 1920, 30, 0.06), None),
             ("длинное 720p с умеренным потоком — не трогаем", probe(720, 1280, 900, 0.06), None),
             ("длинное 720p с большим потоком — пережимаем", probe(720, 1280, 900, 0.2), 1280),
             ("короткое 1080p с большим потоком — пережимаем в 1920", probe(1080, 1920, 30, 0.2), 1920)]
    for name, data, want in cases:
        media.probe_media = lambda path, d=data: d
        got = night_video.plan("видео.mp4")
        check(name, got == want, got)

    root = tempfile.mkdtemp(prefix="night-")
    src = os.path.join(root, "v.mp4")

    def setup(size=1000):
        open(src, "wb").write(b"a" * size)

    durations = {}
    media.probe_media = lambda path: {"format": {"duration": str(durations.get(os.path.basename(path), 0))}}

    def fake(out_size, out_duration):
        def run(a, b):
            open(b, "wb").write(b"b" * out_size)
            durations[os.path.basename(b)] = out_duration
        return run

    setup(); durations["v.mp4"] = 900
    new = night_video.recompress(src, 1280, fake(400, 900))
    check("меньше на 60% — файл заменён", new == 400 and os.path.getsize(src) == 400 and open(src, "rb").read(1) == b"b")
    setup(); durations["v.mp4"] = 900
    check("выигрыш меньше 15% — исходник не тронут", night_video.recompress(src, 1280, fake(900, 900)) is None
          and open(src, "rb").read(1) == b"a")
    setup(); durations["v.mp4"] = 900
    check("длительность не совпала — исходник не тронут", night_video.recompress(src, 1280, fake(300, 450)) is None
          and open(src, "rb").read(1) == b"a")

    def broken(a, b):
        raise RuntimeError("ffmpeg упал")
    setup(); durations["v.mp4"] = 900
    try:
        night_video.recompress(src, 1280, broken)
        failed = False
    except RuntimeError:
        failed = True
    check("сбой пережатия — исходник цел, временный файл убран", failed and open(src, "rb").read(1) == b"a"
          and not [f for f in os.listdir(root) if ".night" in f])


    # ===== проба 10 с перед полным сжатием и сохранность очереди =====
    import shutil
    import subprocess
    nv = night_video
    ok_path = os.path.join(root, "q.mp4")
    open(ok_path, "wb").write(b"x" * 1000)
    calls = []

    def step(estimate=None, gain=None, duration=900, side=1280, window=(True,), full=500, raise_full=False):
        calls.clear()
        saved = []
        state = iter(list(window) + [True] * 5)
        def recompress_fn(path, side_):
            calls.append("full")
            if raise_full:
                raise RuntimeError("ffmpeg упал")
            return full
        r = nv.process_one(ok_path, estimate, saved.append, lambda: next(state),
                           plan_fn=lambda p: side, duration_fn=lambda p: duration,
                           sample_fn=lambda p, sd, d: (calls.append("sample"), gain)[1], recompress_fn=recompress_fn)
        return r, saved

    r, saved = step(gain=0.03)
    check("проба: выигрыш ~3% — полного сжатия нет, видео отмечается", r["action"] == "skip" and calls == ["sample"] and not saved)
    r, saved = step(gain=0.12)
    check("проба на границе (12% при пороге 15% и запасе 5%) — полное сжатие всё равно делается",
          r["action"] == "replaced" and calls == ["sample", "full"] and saved == [0.12])
    r, saved = step(gain=0.45)
    check("проба ~45% — выигрыш записан до полного сжатия, затем полное сжатие", r["action"] == "replaced"
          and calls == ["sample", "full"] and saved == [0.45])
    r, saved = step(gain=0.45, window=(False,))
    check("после пробы окно закончилось — видео не отмечено, выигрыш сохранён (deferred)",
          r["action"] == "deferred" and calls == ["sample"] and saved == [0.45])
    r, saved = step(estimate=0.45)
    check("следующая ночь: проба уже была — сразу полное сжатие, без повторной пробы",
          r["action"] == "replaced" and calls == ["full"] and not saved)
    r, saved = step(gain=0.45, raise_full=True)
    check("сбой полного сжатия после пробы — failed, выигрыш сохранён (видео не выпадет из очереди)",
          r["action"] == "failed" and saved == [0.45])
    r, saved = step(gain=None)
    check("проба не удалась — полное сжатие, как раньше", r["action"] == "replaced" and calls == ["sample", "full"])
    r, saved = step(duration=20)
    check("короткое видео (20 с) — без пробы, сразу полное сжатие", r["action"] == "replaced" and calls == ["full"])
    r, saved = step(side=None)
    check("сжимать нечего — без пробы и без сжатия", r["action"] == "nothing" and calls == [])

    class Ops:
        def __init__(self):
            self.marked, self.estimates, self.fails, self.sizes = [], {}, {}, []
        def mark(self, i): self.marked.append(i)
        def save_estimate(self, i, g): self.estimates[i] = g
        def failed(self, i):
            self.fails[i] = self.fails.get(i, 0) + 1
            return self.fails[i]
        def update_sizes(self, *a): self.sizes.append(a)

    real = nv.process_one
    script = {}
    def fake_process(path, estimate, on_estimate, window_open, **k):
        act = script[os.path.basename(path)]
        if act == "deferred":
            on_estimate(0.5)
            return {"action": "deferred", "estimate": 0.5}
        if act == "failed":
            on_estimate(0.5)
            return {"action": "failed", "estimate": 0.5, "error": "сбой"}
        return {"action": act, "estimate": 0.03 if act == "skip" else None, "side": 1280, "old": 1000, "new": 400}
    nv.process_one = fake_process
    saved_root, nv.FILE_STORAGE_ROOT = nv.FILE_STORAGE_ROOT, root
    saved_wait, nv._wait_quiet = nv._wait_quiet, lambda w: True
    saved_dims, nv._dims = nv._dims, lambda p: (1280, 720)
    try:
        ops = Ops()
        script.update({"a.mp4": "replaced", "b.mp4": "skip", "c.mp4": "failed", "d.mp4": "deferred", "e.mp4": "replaced"})
        nv.run_items([(1, "a.mp4", None, 0), (2, "b.mp4", None, 0), (3, "c.mp4", None, 0), (4, "d.mp4", None, 0),
                      (5, "e.mp4", None, 0)], False, ops)
        check("очередь: сжатое и «проба — не нужно» отмечены; сбой и отложенное — нет; после отложенного ночь кончилась",
              ops.marked == [1, 2] and ops.estimates == {3: 0.5, 4: 0.5} and ops.fails == {3: 1} and len(ops.sizes) == 1)
        ops2 = Ops()
        ops2.fails = {3: nv.VIDEO_NIGHT_MAX_ATTEMPTS - 1}
        nv.run_items([(3, "c.mp4", 0.5, nv.VIDEO_NIGHT_MAX_ATTEMPTS - 1)], False, ops2)
        check("после %d сбоев подряд видео больше не берётся (отмечается)" % nv.VIDEO_NIGHT_MAX_ATTEMPTS, ops2.marked == [3])
    finally:
        nv.process_one, nv.FILE_STORAGE_ROOT, nv._wait_quiet, nv._dims = real, saved_root, saved_wait, saved_dims

    # проба на настоящем видео: ffmpeg копирует 10 с из середины и пережимает теми же настройками
    if shutil.which("ffmpeg"):
        import importlib
        importlib.reload(media)          # вернуть настоящий probe_media после подмен выше
        clip = os.path.join(root, "clip.mp4")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=40",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=40", "-c:v", "libx264", "-preset", "ultrafast",
                        "-b:v", "12M", "-maxrate", "12M", "-bufsize", "6M", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", clip], check=True)
        direct = lambda a, b: media._process_video_sync(a, b, None, None, allow_light=False, side=1280)
        gain = nv.sample_gain(clip, 1280, 40.0, transcode=direct)
        check("проба на видео 1080p с потоком 12 Мбит/с: выигрыш больше порога", gain is not None and gain > nv.VIDEO_NIGHT_MIN_GAIN,
              gain)
        check("временные файлы пробы убраны", not [f for f in os.listdir(root) if ".sample-" in f])

    check("ночное окно 3–7", night_video.in_window(4, 3, 7) and not night_video.in_window(8, 3, 7) and not night_video.in_window(2, 3, 7))
    check("ночное окно через полночь 22–5", night_video.in_window(23, 22, 5) and night_video.in_window(4, 22, 5)
          and not night_video.in_window(12, 22, 5))
    bad = results.count(False)
    print("Итого: %d проверок, ошибок: %d" % (len(results), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
