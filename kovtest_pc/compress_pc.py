# -*- coding: utf-8 -*-
"""ПК-версия в проверке качества сжатия: те же исходники сжимает код программы.

    python kovtest_pc/compress_pc.py --project <папка ПК-версии> --out <папка>

Исходники — logs/compression-inputs (их готовит run.cmd compression в Docker). Фото — kov.media.image
(WebP), видео — kov.media.video (FFmpeg из комплекта, OpenH264), документы — gzip из kov.media.upload:
то же, что делает программа перед отправкой. Результат — в logs/compression-results/ПК, рядом с
результатами браузеров, сервера и телефона; сравнение и отчёт строит tests/compression.py
(стороны, длительность, сходство с оригиналом и размер считает FFmpeg контейнера тестов).
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE = os.path.dirname(HERE)
LABEL = "ПК"
INPUTS = (  # как в tests/compression.py: имя, вид
    ("photo-4032.jpg", "photo"),
    ("photo-1600.png", "photo"),
    ("video-12s-1080p.mp4", "video"),
    ("video-64s-1080p.mp4", "video"),
    ("document.csv", "document"),
)

ap = argparse.ArgumentParser(description="ПК-версия Kovapp: сжатие исходников проверки качества")
ap.add_argument("--project", required=True)
ap.add_argument("--out", required=True)
A = ap.parse_args()
OUT = os.path.abspath(A.out)
os.makedirs(OUT, exist_ok=True)
home = os.path.join(OUT, "home")
os.makedirs(home, exist_ok=True)
os.environ["HOME"] = os.environ["USERPROFILE"] = os.environ["LOCALAPPDATA"] = home
sys.path.insert(0, os.path.abspath(A.project))
LOG = open(os.path.join(OUT, "run.log"), "a", encoding="utf-8")
RESULTS = []


def log(text):
    LOG.write("%s %s\n" % (time.strftime("%H:%M:%S"), text))
    LOG.flush()


def check(name, ok, detail="", warn=False):
    status = "passed" if ok else ("warned" if warn else "failed")
    RESULTS.append({"name": name, "status": status, "detail": str(detail)[:600]})
    log("CHECK %s %s | %s" % (status.upper(), name, str(detail)[:600]))


def finish():
    with open(os.path.join(OUT, "results.json"), "w", encoding="utf-8") as f:
        json.dump({"checks": RESULTS}, f, ensure_ascii=False, indent=1)


def compress(src, name, kind, tmp):
    """(байты результата, решение программы) или (None, решение с причиной)."""
    if kind == "photo":
        from kov.media import image as imageprep
        r = imageprep.prepare(src, name, tmp)
        with open(r["path"], "rb") as f:
            data = f.read()
        return data, {"changed": bool(r.get("changed")), "reason": r.get("reason") or "",
                      "type": "image/webp" if r["path"].lower().endswith(".webp") else ""}
    if kind == "video":
        from kov.media import video as v
        p = v.plan(src)
        decision = {"mode": p["mode"], "reason": p.get("reason") or "", "encoder": "FFmpeg из комплекта, OpenH264"}
        if p["mode"] != "device":
            return None, decision
        dst = os.path.join(tmp, os.path.splitext(name)[0] + "-pc.mp4")
        v.compress(src, dst, p)
        with open(dst, "rb") as f:
            return f.read(), decision
    from kov.media import upload as up
    enc, packed = up.gzip_pack(up.Job(), src, name, tmp, lambda part: None)
    with open(packed if enc == "gzip" and packed else src, "rb") as f:
        return f.read(), {"enc": enc or "identity"}


def main():
    inputs = os.path.join(MODULE, "logs", "compression-inputs")
    missing = [n for n, _ in INPUTS if not os.path.isfile(os.path.join(inputs, n))]
    if missing:
        check("исходники проверки качества", False, "нет %s в logs/compression-inputs — их готовит run.cmd compression "
              "(Docker); запустите его, ПК-версия войдёт в сравнение сама" % ", ".join(missing), warn=True)
        return
    from kov.media import video as v
    check("FFmpeg из комплекта найден", v.available(), "%s | %s" % (v.ffmpeg_path(), v.ffprobe_path()))
    tmp = tempfile.mkdtemp(dir=OUT)
    target = os.path.join(MODULE, "logs", "compression-results", LABEL)
    if os.path.isdir(target):
        shutil.rmtree(target)
    os.makedirs(target)
    meta = {"path": LABEL, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "run": os.path.basename(os.path.dirname(OUT)), "items": {}}
    for name, kind in INPUTS:
        t0 = time.time()
        try:
            data, decision = compress(os.path.join(inputs, name), name, kind, tmp)
        except Exception as e:
            meta["items"][name] = {"error": "сбой: %s" % str(e)[:200]}
            check("%s: сжатие" % name, False, str(e)[:200])
            continue
        if data is None:
            meta["items"][name] = {"decision": decision, "error": "ПК не сжимал (%s: %s)" % (decision.get("mode"), decision.get("reason"))}
            check("%s: сжатие" % name, False, meta["items"][name]["error"])
            continue
        out = "out-" + name + (".gz" if kind == "document" and decision.get("enc") == "gzip" else "")
        with open(os.path.join(target, out), "wb") as f:
            f.write(data)
        meta["items"][name] = {"decision": decision, "file": out}
        check("%s: сжато" % name, True, "%.2f МБ → %.2f МБ за %.1f с" % (
            os.path.getsize(os.path.join(inputs, name)) / 1048576.0, len(data) / 1048576.0, time.time() - t0))
    with open(os.path.join(target, "result.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    log("результат: %s" % target)
    shutil.rmtree(tmp, ignore_errors=True)


try:
    main()
except Exception as e:
    import traceback
    check("сжатие на ПК", False, traceback.format_exc()[-500:])
finish()
