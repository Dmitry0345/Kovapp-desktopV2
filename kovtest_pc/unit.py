# -*- coding: utf-8 -*-
"""ПК-версия: проверки без окон — то, что можно проверить программой напрямую.

    python kovtest_pc/unit.py --project <папка ПК-версии> --out <папка>

  стили мессенджера для старого движка (compat)   чистые функции мессенджера (util)
  правила уведомлений (notify_center.decide)       фото: поворот, WebP, «уже компактные», размеры
  отпечатки файлов (как у сайта и сервера)         gzip документов
  видео на образцах: план и сжатие FFmpeg из комплекта
  отправка файлов на тестовый сервер: группа, сжатие, отмена

Итог — results.json и журнал run.log в папке --out (формат как у harness.py).
"""

import argparse
import gzip
import json
import os
import random
import shutil
import sys
import tempfile
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")
MEDIA = os.path.join(HERE, "media")

ap = argparse.ArgumentParser(description="ПК-версия Kovapp: проверки без окон")
ap.add_argument("--project", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--only", default="", help="через запятую: compat,util,notify,images,hashes,gzip,video,upload")
A = ap.parse_args()
OUT = os.path.abspath(A.out)
os.makedirs(OUT, exist_ok=True)
home = os.path.join(OUT, "home")
os.makedirs(home, exist_ok=True)
os.environ["HOME"] = os.environ["USERPROFILE"] = os.environ["LOCALAPPDATA"] = home
sys.path.insert(0, os.path.join(HERE, "mock"))
sys.path.insert(0, os.path.abspath(A.project))
LOG = open(os.path.join(OUT, "run.log"), "a", encoding="utf-8")
RESULTS = []
CURRENT = {"group": ""}


def log(text):
    LOG.write("%s %s\n" % (time.strftime("%H:%M:%S"), text))
    LOG.flush()


def check(name, ok, detail="", warn=False):
    status = "passed" if ok else ("warned" if warn else "failed")
    full = "%s: %s" % (CURRENT["group"], name) if CURRENT["group"] else name
    RESULTS.append({"name": full, "status": status, "detail": str(detail)[:600]})
    log("CHECK %s %s | %s" % (status.upper(), full, str(detail)[:600]))


TESTS = []


def test(group):
    def wrap(fn):
        TESTS.append((group, fn))
        return fn
    return wrap


# ── стили мессенджера для Chromium 83 ────────────────────────────────────────

@test("compat")
def t_compat():
    from kov.messenger import compat
    check("версия движка из строки браузера", compat.chromium_major("Mozilla/5.0 QtWebEngine/5.15.2 Chrome/83.0.4103.122 Safari/537.36") == 83
          and compat.chromium_major("… Chrome/87.0.4280.144 …") == 87 and compat.chromium_major("") == 83)
    css = (".row { display: flex; gap: 8px; }\n.col { display: flex; flex-direction: column; gap: 10px; }\n"
           ".col .sep { margin: 10px 0; }\n.x:focus-visible { outline: 2px solid red; }\n.o { position: absolute; inset: 0 4px; }\n"
           ".sq { aspect-ratio: 16 / 9; }\n.a :is(.b, .c) { color: red; }\n.n:not(.p, .q) { color: blue; }\n.g { display: grid; gap: 4px; }")
    out, info = compat.transform(css, 83)
    check("flex gap → отступы у соседей", ".row > * + * { margin-left: 8px; }" in out and ".col > * + * { margin-top: 10px; }" in out, out[:300])
    check("gap у flex убран, у grid оставлен", "display: flex; gap" not in out and ".g { display: grid; gap: 4px; }" in out)
    check("свой отступ + gap (у .sep margin: 10px 0)", ".col > * + .sep { margin-top: calc(10px + 10px); }" in out)
    check(":focus-visible → .kov-fv", ".x.kov-fv" in out and ":focus-visible" not in out)
    check("inset → top/right/bottom/left", "top: 0; right: 4px; bottom: 0; left: 4px" in out)
    check("aspect-ratio → в скрипт страницы", [".sq", round(16 / 9, 6)] in info["aspect"] and "aspect-ratio" not in out)
    check(":is() и :not(a, b) раскрыты", ".a .b, .a .c" in out and ".n:not(.p):not(.q)" in out)
    same, info2 = compat.transform(css, 120)
    check("свежий движок: без замен", "gap: 8px" in same and ":focus-visible" in same and not info2["flex_gap"] and not info2["aspect"])
    # настоящие стили чата — разбираются целиком, без «неизвестных» flex-контейнеров в переписке
    from kov.messenger import page
    root = page.assets_dir()
    chat = open(os.path.join(root, "chat", "chat.css"), encoding="utf-8").read()
    o83, i83 = compat.transform(chat, 83)
    check("стили чата: преобразованы", len(i83["flex_gap"]) > 20 and "#kovChat .messages-list > * + *" in o83, len(i83["flex_gap"]))
    check("стили чата: правила не потеряны", o83.count("{") >= compat.dump(compat.parse(chat)).count("{"))
    t0 = time.time()
    for major in (83, 87):
        page._CACHE.clear()
        html = page.build("light", 1.0, 16.0, 1, "http://127.0.0.1:1", "t", major=major)
        check("страница мессенджера собирается (Chromium %d)" % major, "<div id=\"kovChat\">" in html and len(html) < 2_000_000,
              "%d КБ" % (len(html) // 1024))
    check("сборка страницы быстрая", time.time() - t0 < 5, "%.1f с" % (time.time() - t0))


# ── чистые функции мессенджера ───────────────────────────────────────────────

@test("util")
def t_util():
    from kov.messenger import util
    u = util.Urls("http://h:1/", lambda: "tok en")
    check("адрес файла хранилища", u.storage_path("messenger/1/a.png") == "http://h:1/file_storage/messenger/1/a.png"
          and u.storage_path("/file_storage/x.png") == "http://h:1/file_storage/x.png" and u.storage_path("https://e/x") == "https://e/x")
    check("токен в ссылке", u.with_token("http://h:1/a?b=1") == "http://h:1/a?b=1&token=tok%20en")
    check("видео из .mov — по адресу .webm, без .gz", u.media_src({"url": "v/c.mov.gz", "filename": "c.mov"}).startswith("http://h:1/file_storage/v/c.webm?token="))
    check("фото сотрудника — адрес обработчика", u.avatar_src("/employees/7/photo").startswith("http://h:1/employees/7/photo?token="))
    msgs = [
        {"id": 10, "group_id": "g", "special": "file_group", "text": "подпись", "timestamp": "2026-10-05T10:00:02"},
        {"id": 11, "group_id": "g", "parent_id": 10, "file_path": "m/a.png", "file_name": "a.png", "timestamp": "2026-10-05T10:00:02"},
        {"id": 12, "group_id": "g", "parent_id": 10, "file_path": "m/b.mp4", "file_name": "b.mp4", "timestamp": "2026-10-05T10:00:02"},
        {"id": 9, "text": "раньше", "timestamp": "2026-10-05T09:00:00"},
    ]
    agg = util.aggregate(msgs, u)
    check("группа файлов → одно сообщение с файлами", len(agg) == 2 and agg[1]["id"] == 10 and len(agg[1]["files"]) == 2
          and agg[1]["text"] == "подпись", [(m["id"], len(m.get("files") or [])) for m in agg])
    check("порядок — по времени", [m["id"] for m in agg] == [9, 10])
    chats = [{"id": 1, "unread_count": 2}, {"id": 2, "unread_count": "3"}, {"id": 3, "unread_count": None}]
    check("непрочитанные: открытый на экране чат не считается", util.unread_total(chats, 2, True) == 2
          and util.unread_total(chats, 2, False) == 5 and util.unread_total(chats) == 5)
    check("больше 10 файлов — сообщениями по 10", [len(p) for p in util.parts_of(list(range(23)))] == [10, 10, 3])
    check("правки файлов делятся по сообщениям", util.split_edits({"0": "a", "11": "b", "x": "c"}, 10, 10) == {"1": "b"})
    check("стикер и GIF — словом", util.preview_text("[[sticker:pack/cat]]") == "Стикер"
          and util.preview_text("[[gif:https://x/y.gif]]") == "GIF" and util.preview_text("текст") == "текст")
    check("роли по-русски", util.role_label("owner") == "владелец" and util.role_label("participant") == "участник"
          and util.role_label("??") == "участник")


# ── уведомления ──────────────────────────────────────────────────────────────

@test("notify")
def t_notify():
    from kov import notify_center as nc
    base = {"settings": dict(nc.DEFAULT_SETTINGS), "visible": True, "user_id": 1, "active_chat": None,
            "chats": {5: {"is_group": False, "name": "Петров"}, 9: {"is_group": True, "name": "Монтаж"}}}

    def ctx(**kw):
        c = json.loads(json.dumps(base))
        c["chats"] = {int(k): v for k, v in c["chats"].items()}
        c.update(kw)
        return c
    msg = {"type": "message", "sender_id": 3, "chat_id": 5, "text": "привет", "sender_name": "Петров Иван",
           "sender_last_name": "Петров", "sender_first_name": "Иван", "id": 77}
    p = nc.decide(msg, ctx())
    check("сообщение: карточка, звук, без системного (окно видно)", p and p["kind"] == "chat" and p["toast"] and p["sound"]
          and not p["os"] and p["initials"] == "ПИ" and p["tag"] == "chat-5", p)
    check("окно свёрнуто — и системное уведомление", nc.decide(msg, ctx(visible=False))["os"])
    check("своё сообщение — без уведомления", nc.decide(dict(msg, sender_id=1), ctx()) is None)
    check("этот чат открыт — без уведомления", nc.decide(msg, ctx(active_chat=5)) is None)
    g = nc.decide(dict(msg, chat_id=9), ctx())
    check("группа — «в группе «…»»", g["where"] == "в группе «Монтаж»" and g["is_group"], g["where"])
    f = nc.decide({"type": "grouped_message", "sender_id": 3, "chat_id": 5, "file_count": 3, "text": ""}, ctx())
    check("файлы без подписи — «Файлы: 3»", f["body"] == "Файлы: 3", f["body"])
    check("стикер — словом", nc.decide(dict(msg, text="[[sticker:a/b]]"), ctx())["body"] == "Стикер")
    off = ctx()
    off["settings"].update(chatToasts=False, sound=False)
    q = nc.decide(msg, off)
    check("настройки: без карточек чата и звука", not q["toast"] and not q["sound"])
    add = nc.decide({"type": "special_message", "special": "member_add", "sender_id": 3, "chat_id": 9, "id": 5,
                     "metadata": {"user_id": 1, "actor_name": "Петров"}}, ctx())
    check("меня добавили в группу", add and add["kind"] == "group" and "добавил" in add["body"])
    check("удалили из группы — предупреждение без звука", nc.decide({"type": "removed_from_chat", "chat_id": 9}, ctx())["sound"] is False)
    n = nc.decide({"type": "notification.new", "message": "Новая задача", "notification_id": 4}, ctx())
    check("уведомление сервера — карточка и звук", n and n["kind"] == "notification" and n["toast"] and n["sound"])
    wav = nc.chime_wav("chat")
    import struct
    rate = struct.unpack("<I", wav[24:28])[0]
    frames = struct.unpack("<I", wav[40:44])[0] // 2
    check("звук: WAV 16 бит, 22050 Гц, 0,62 с", wav[:4] == b"RIFF" and wav[8:12] == b"WAVE" and rate == 22050
          and abs(frames / rate - 0.62) < 0.01, "%d Гц, %.2f с" % (rate, frames / rate))


# ── фото ─────────────────────────────────────────────────────────────────────

@test("images")
def t_images():
    from kov.media import image as ip
    ref = json.load(open(os.path.join(FIX, "reference.json"), encoding="utf-8"))
    for w, h, side, want in ref["fit"]:
        got = list(ip.fit(w, h, side))
        if got != want:
            check("вписывание %d×%d в %d" % (w, h, side), False, "%s вместо %s" % (got, want))
    check("вписывание в сторону — как у сайта (%d случаев)" % len(ref["fit"]),
          all(list(ip.fit(w, h, s)) == want for w, h, s, want in ref["fit"]))
    tmp = tempfile.mkdtemp(dir=OUT)
    for name, dims in ref["dims"].items():
        r = ip.prepare(os.path.join(FIX, name), name, tmp)
        w, h = dims["width"], dims["height"]
        if dims.get("orientation") in (5, 6, 7, 8):
            w, h = h, w
        ok = (r["width"], r["height"]) == (w, h) and (r["thumb"] is not None or name.endswith(".gif"))
        check("фото %s: размер с учётом поворота %d×%d, миниатюра" % (name, w, h), ok,
              "%s×%s %s %s" % (r["width"], r["height"], r["reason"] or ("сжато" if r["changed"] else ""), r["name"]))
    gif = ip.prepare(os.path.join(FIX, "anim.gif"), "anim.gif", tmp)
    check("GIF — без пересжатия (может быть анимацией)", not gif["changed"] and gif["reason"] == "gif")
    from PIL import Image
    big = os.path.join(tmp, "big.png")
    Image.effect_noise((4000, 3000), 30).convert("RGB").save(big)
    r = ip.prepare(big, "big.png", tmp)
    with Image.open(r["path"]) as im:
        fmt, size = im.format, im.size
    check("крупное фото → WebP 3200×2400", r["changed"] and r["name"] == "big.webp" and fmt == "WEBP" and size == (3200, 2400),
          "%s %s %s" % (r["name"], fmt, size))
    alpha = os.path.join(tmp, "alpha.png")
    n = Image.effect_noise((900, 700), 50)
    Image.merge("RGBA", (n, n, n, Image.linear_gradient("L").resize((900, 700)))).save(alpha)
    ra = ip.prepare(alpha, "alpha.png", tmp)
    with Image.open(ra["path"]) as im:
        check("прозрачность сохраняется (WebP с альфой)", ra["changed"] and im.mode == "RGBA", "%s %s" % (ra["name"], im.mode))


# ── отпечатки файлов ─────────────────────────────────────────────────────────

@test("hashes")
def t_hashes():
    from kov.media import filehash as fh
    ref = json.load(open(os.path.join(FIX, "reference.json"), encoding="utf-8"))["hash"]
    tmp = tempfile.mkdtemp(dir=OUT)
    rnd = random.Random(42)                     # как make_fixtures.py: те же байты по порядку размеров
    for size in (0, 1, 1000, 4 * 1024 * 1024 - 1, 4 * 1024 * 1024, 4 * 1024 * 1024 + 1, 9 * 1024 * 1024 + 123):
        name = "blob_%d.bin" % size
        data = rnd.randbytes(size)
        path = os.path.join(FIX, name) if os.path.isfile(os.path.join(FIX, name)) else os.path.join(tmp, name)
        if not os.path.isfile(path):
            with open(path, "wb") as f:
                f.write(data)
        want = ref[name]
        ok_tree = fh.tree(path) == want["tree"]
        ok_soft = fh.range_hash(path, 0, 100000) == want["soft"]     # «soft» сайта: SHA-256 первых 100 000 байт
        ok_ranges = all(fh.range_hash(path, o, n) == h for o, n, h in want["ranges"])
        th = fh.TreeHasher()
        with open(path, "rb") as f:
            while True:
                part = f.read(1000003)
                if not part:
                    break
                th.update(part)
        check("отпечаток %s (%d байт): дерево, начало, участки" % (name, size), ok_tree and ok_soft and ok_ranges
              and th.hexdigest() == want["tree"], "дерево %s, начало %s, участки %s" % (ok_tree, ok_soft, ok_ranges))


# ── gzip документов ──────────────────────────────────────────────────────────

@test("gzip")
def t_gzip():
    from kov.media import upload as up
    job = up.Job()
    tmp = tempfile.mkdtemp(dir=OUT)
    text = os.path.join(tmp, "отчёт.csv")
    with open(text, "w", encoding="utf-8") as f:
        for i in range(40000):
            f.write("%d;Кабель UTP cat.5e;%d;м;склад %d\n" % (i, i % 300, i % 7))
    enc, packed = up.gzip_pack(job, text, "отчёт.csv", tmp, lambda p: None)
    ok = enc == "gzip" and packed and gzip.decompress(open(packed, "rb").read()) == open(text, "rb").read()
    check("текст сжимается gzip и распаковывается без потерь", ok, "%s %d → %d" % (enc, os.path.getsize(text), os.path.getsize(packed) if packed else 0))
    noise = os.path.join(tmp, "шум.bin")
    with open(noise, "wb") as f:
        f.write(os.urandom(400000))
    check("несжимаемое — как есть", up.gzip_pack(job, noise, "шум.bin", tmp, lambda p: None)[0] == "identity")
    check("фото и видео — без gzip", up.gzip_pack(job, os.path.join(MEDIA, "clip.mp4"), "clip.mp4", tmp, lambda p: None)[0] == "identity")
    small = os.path.join(MEDIA, "smeta.csv")
    check("крошечный файл — как есть", up.gzip_pack(job, small, "smeta.csv", tmp, lambda p: None)[0] == "identity")


# ── видео на образцах ────────────────────────────────────────────────────────

EXPECT = {  # образец: (план, сторона вывода или None, кадров/с не больше, звук)
    "h264_aac_rot.mp4": ("device", (360, 640), 30, True),
    "h264_ready_faststart.mp4": ("as-is", None, None, None),
    "h264_60fps_noaudio.mp4": ("device", (1920, 1080), 30, False),
    "hevc_opus.mp4": ("device", (640, 480), 30, True),
    "h264_mp3_rot.mov": ("device", (270, 480), 30, True),
    "vp9.mp4": ("device", (320, 240), 30, False),
    "av1.mp4": ("device", (320, 240), 30, False),
    "fragmented.mp4": ("device", (320, 240), 30, False),
    "two_audio.mp4": ("device", (320, 240), 30, True),
    "mjpeg.mov": ("device", (320, 240), 30, False),
    "small.3gp": ("device", (176, 144), 30, False),
    "garbage.mp4": ("server", None, None, None),
}


@test("video")
def t_video():
    from kov.media import video as v
    check("FFmpeg из комплекта найден", v.available(), "%s | %s" % (v.ffmpeg_path(), v.ffprobe_path()))
    if not v.available():
        return
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
    import subprocess
    ver = subprocess.run([v.ffmpeg_path(), "-hide_banner", "-version"], capture_output=True, text=True, **kw).stdout.splitlines()[:1]
    enc = subprocess.run([v.ffmpeg_path(), "-hide_banner", "-encoders"], capture_output=True, text=True, **kw).stdout
    dec = subprocess.run([v.ffmpeg_path(), "-hide_banner", "-decoders"], capture_output=True, text=True, **kw).stdout
    check("FFmpeg: OpenH264 и AAC для сжатия", " libopenh264 " in enc and " aac " in enc, ver)
    lic = subprocess.run([v.ffmpeg_path(), "-hide_banner", "-L"], capture_output=True, text=True, **kw).stdout
    conf = subprocess.run([v.ffmpeg_path(), "-hide_banner", "-buildconf"], capture_output=True, text=True, **kw).stdout
    check("FFmpeg: без частей под GPL — лицензия LGPL, x264 нет", "Lesser General Public" in lic and " libx264 " not in enc
          and "--enable-gpl" not in conf, " ".join(lic.split())[:120])
    check("FFmpeg: читает HEVC, AV1 (dav1d), VP9, MPEG-4", all(x in dec for x in (" hevc ", " libdav1d ", " vp9 ", " mpeg4 ")))
    tmp = tempfile.mkdtemp(dir=OUT)
    for name in json.load(open(os.path.join(FIX, "videos.json"), encoding="utf-8")):
        want_mode, dims, fps_max, audio = EXPECT.get(name, (None, None, None, None))
        src = os.path.join(FIX, name)
        p = v.plan(src)
        check("видео %s: план «%s»" % (name, want_mode), p["mode"] == want_mode, "%s %s" % (p["mode"], p.get("reason", "")[:80]))
        if p["mode"] != "device":
            continue
        dst = os.path.join(tmp, os.path.splitext(name)[0] + ".mp4")
        t0 = time.time()
        try:
            v.compress(src, dst, p)
        except Exception as e:
            check("видео %s: сжатие" % name, False, str(e)[:200])
            continue
        d = v.probe(dst)
        vs = next((s for s in d["streams"] if s["codec_type"] == "video"), {})
        auds = [s for s in d["streams"] if s["codec_type"] == "audio"]
        fps = v.fps_of(vs)
        dur_in, dur_out = v.duration_of(p["data"]), v.duration_of(d)
        ok = (vs.get("codec_name") == "h264" and vs.get("profile") == "High" and (vs.get("width"), vs.get("height")) == dims
              and fps <= fps_max + 0.01 and (len(auds) == 1 if audio else not auds)
              and all(a.get("codec_name") == "aac" for a in auds) and abs(dur_in - dur_out) < 0.12
              and not v.rotation_of(vs))
        check("видео %s → H.264 High %d×%d, ≤%d к/с, %s" % (name, dims[0], dims[1], fps_max, "AAC" if audio else "без звука"), ok,
              "%s %s %s×%s %.0f к/с, звук %s, %.2f→%.2f с, %.1f с работы" % (
                  vs.get("codec_name"), vs.get("profile"), vs.get("width"), vs.get("height"), fps,
                  [a.get("codec_name") for a in auds], dur_in, dur_out, time.time() - t0))
        with open(dst, "rb") as f:
            head = f.read(4096)
        check("видео %s: индекс в начале файла (faststart)" % name, head.find(b"moov") != -1 and (head.find(b"mdat") == -1 or head.find(b"moov") < head.find(b"mdat")))
        # сервер берёт сжатое на устройстве без перекодирования, если поток не выше цели ×1,6 (intake.py);
        # у крошечного кадра (предел меньше 64 кбит/с) один первый ключевой кадр уже больше — такие не проверяются
        rate, aim = v.source_bitrate(d, vs), v.target_bitrate(vs.get("width"), vs.get("height"), fps)
        if aim * 1.6 >= 64000:
            check("видео %s: поток в пределах, которые сервер берёт без перекодирования" % name, 0 < rate <= aim * 1.6,
                  "%.0f кбит/с, цель сервера %.0f, предел %.0f" % (rate / 1000.0, aim / 1000.0, aim * 1.6 / 1000.0))
    # длинное видео — сторона HD 1280 (правило «длиннее 60 с»)
    check("сторона: до 60 с — 1920, длиннее — 1280", v.side_for(59) == 1920 and v.side_for(61) == 1280)
    # правки: обрезка 1:1, поворот 90°, отрезок 1,0–2,6 с — тем же проходом
    src = os.path.join(MEDIA, "clip.mp4")
    edits = {"crop": {"x": 0.21875, "y": 0, "w": 0.5625, "h": 1}, "rotate": 90, "trim_start": 1.0, "trim_end": 2.6}
    p = v.plan(src, edits)
    dst = os.path.join(tmp, "edited.mp4")
    v.compress(src, dst, p, edits)
    d = v.probe(dst)
    vs = next((s for s in d["streams"] if s["codec_type"] == "video"), {})
    check("правки видео: обрезка 1:1, поворот, отрезок 1,6 с", (vs.get("width"), vs.get("height")) == (1080, 1080)
          and abs(v.duration_of(d) - 1.6) < 0.12, "%s×%s %.2f с" % (vs.get("width"), vs.get("height"), v.duration_of(d)))
    shot = v.poster(dst)
    check("обложка видео — JPEG", bool(shot) and shot[:2] == b"\xff\xd8", len(shot or b""))


# ── отправка на тестовый сервер ──────────────────────────────────────────────

@test("upload")
def t_upload():
    import pc_mock
    srv, host = pc_mock.start()
    L = srv.state.L
    try:
        from kov.api import client
        from kov.media import upload as up
        client.login(host, "director", "test")
        check("вход через клиент программы", bool(client.token) and client.user_id == 1, client.user_id)
        captured = []
        orig = L.resolve_items

        def capture(meta, files):
            items = orig(meta, files)
            captured.append({"meta": meta, "items": [(it["name"], len(it["data"])) for it in items]})
            return items
        L.resolve_items = capture
        tmp = tempfile.mkdtemp(dir=OUT)
        doc = os.path.join(tmp, "акт.txt")
        with open(doc, "w", encoding="utf-8") as f:
            f.write("Акт выполненных работ\n" * 20000)
        files = [(os.path.join(MEDIA, "facade.jpg"), "facade.jpg"), (os.path.join(FIX, "hevc_opus.mp4"), "hevc_opus.mp4"),
                 (doc, "акт.txt")]
        marks = []
        job = up.Job()
        try:
            created = up.upload(job, files, "chat/grouped-message", {"chat_id": 33, "captions": "из программы"},
                                lambda p, label, d: marks.append(round(p, 3)))
        finally:
            job.cleanup()
        meta = captured[-1]["meta"] if captured else []
        by = {m.get("name"): m for m in meta}
        check("группа из трёх файлов принята", len((created or {}).get("files") or []) == 3, [f.get("file_name") for f in (created or {}).get("files") or []])
        check("ход отправки растёт до конца", marks and max(marks) >= 0.99 and marks == sorted(marks), marks[-3:])
        vid = by.get("hevc_opus.mp4") or {}
        check("HEVC + Opus сжато на компьютере в MP4 и пришло кусками", vid.get("prep") == "device" and vid.get("src") == "session",
              {k: vid.get(k) for k in ("prep", "src", "width", "height", "duration", "prep_reason")})
        check("документ ушёл gzip, сервер распаковал", (by.get("акт.txt") or {}).get("enc") == "gzip"
              and ("акт.txt", os.path.getsize(doc)) in captured[-1]["items"], captured[-1]["items"] if captured else None)
        # отмена: сжатие крупного видео прерывается, сообщение не создаётся
        before = len(L.MESSAGES.get(33) or [])
        job2 = up.Job()
        threading.Timer(0.8, lambda: job2.cancel("user")).start()
        cancelled = False
        try:
            up.upload(job2, [(os.path.join(FIX, "h264_60fps_noaudio.mp4"), "big.mp4"), (os.path.join(MEDIA, "clip.mp4"), "clip.mp4")],
                      "chat/grouped-message", {"chat_id": 33})
        except up.Cancelled:
            cancelled = True
        finally:
            job2.cleanup()
        check("отмена отправки: остановлено, сообщение не создано", cancelled and len(L.MESSAGES.get(33) or []) == before)
        L.resolve_items = orig
    finally:
        srv.shutdown()


def main():
    only = set(x.strip() for x in A.only.split(",") if x.strip())
    t0 = time.time()
    for group, fn in TESTS:
        if only and group not in only:
            continue
        CURRENT["group"] = {"compat": "стили для Chromium 83", "util": "мессенджер, функции", "notify": "уведомления",
                            "images": "фото", "hashes": "отпечатки", "gzip": "gzip", "video": "видео",
                            "upload": "отправка"}.get(group, group)
        log("== %s" % group)
        try:
            fn()
        except Exception:
            text = traceback.format_exc()
            log("ERROR %s\n%s" % (group, text))
            check("проверка остановилась ошибкой", False, text.strip().splitlines()[-1])
    with open(os.path.join(OUT, "results.json"), "w", encoding="utf-8") as f:
        json.dump({"plan": "unit", "role": "-", "theme": "-", "seconds": round(time.time() - t0, 1), "checks": RESULTS},
                  f, ensure_ascii=False, indent=1)
    failed = sum(1 for r in RESULTS if r["status"] == "failed")
    if not failed:              # рабочие файлы (сжатые видео, большие образцы) нужны только для разбора ошибок
        for name in os.listdir(OUT):
            if name.startswith("tmp") and os.path.isdir(os.path.join(OUT, name)):
                shutil.rmtree(os.path.join(OUT, name), ignore_errors=True)
    print("проверок %d, ошибок %d" % (len(RESULTS), failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
