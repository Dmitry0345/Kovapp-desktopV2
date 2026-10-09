"""Сервер днём: видео с умеренным потоком принимается как есть при любой длине; размер для ночного пережатия. Настоящий media.py."""
import os
import secrets
import sys
import types

os.environ.setdefault("JWT_SECRET_KEY", secrets.token_hex(32))   # только для загрузки настроек в тесте

for name, attrs in (("sqlalchemy", {}), ("sqlalchemy.pool", {"QueuePool": object}),
                    ("fastapi", {}), ("fastapi.staticfiles", {"StaticFiles": type("StaticFiles", (), {"__init__": lambda self, *a, **k: None})})):
    if name not in sys.modules:          # без базы и веб-сервера: проверяется только логика видео
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod

from server.server_app import media  # noqa: E402

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
    check("сторона: до 60 с — %d, дольше — %d" % (media.VIDEO_MAX_SIDE, media.VIDEO_LONG_SIDE),
          media._side_for(30) == media.VIDEO_MAX_SIDE and media._side_for(60) == media.VIDEO_MAX_SIDE and media._side_for(900) == 1280)
    cases = [("короткое 1080p с умеренным потоком — без перекодирования", probe(1080, 1920, 30, 0.06), "copy"),
             ("длинное 1080p с умеренным потоком — днём без перекодирования", probe(1080, 1920, 900, 0.06), "copy"),
             ("длинное 720p с умеренным потоком — без перекодирования", probe(720, 1280, 900, 0.06), "copy"),
             ("длинное 720p с большим потоком — перекодируется", probe(720, 1280, 900, 0.2), None)]
    for name, data, want in cases:
        media.probe_media = lambda path, d=data: d
        got = media.remux_plan("видео.mp4")
        check(name, got == want, got)
    check("ночное пережатие уменьшает длинное видео до 1280", "min(1280,iw)" in media._video_filter(side=1280)
          and "min(%d,iw)" % media.VIDEO_MAX_SIDE in media._video_filter())
    check("предел потока считается для 1280", media._target_bitrate(1080, 1920, 30, 1280) == int(media.VIDEO_TARGET_BPP * 720 * 1280 * 30))
    bad = results.count(False)
    print("Итого: %d проверок, ошибок: %d" % (len(results), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
