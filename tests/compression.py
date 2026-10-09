"""Качество сжатия: одинаковый ли результат там, где сжимают — в браузерах, на сервере, на телефоне и на ПК.

Один и тот же исходник (фото, видео до минуты, видео длиннее минуты, документ) сжимается каждым путём:
  браузеры (CMP_BROWSERS, none — ни одного) — кодом сайта прямо в странице (Kov.ImagePrep, Kov.VideoPrep, Kov.Gzip);
  сервер (CMP_SERVER) — исходник отправляется «как есть», сервер сжимает сам;
  телефон (run.cmd android-device compression) — в приложении: видео — родным кодеком (MediaCodec)
  через галерею приложения, фото и документы — кодом страницы во WebView;
  ПК (kovtest_pc/compress_pc.py, его запускает run.cmd compression) — кодом программы для Windows:
  видео — FFmpeg из её комплекта (OpenH264). Он только сжимает; разбор результата — здесь.
Результаты сравниваются между собой (не с оригиналом): стороны, длительность, частота кадров и дорожки —
точно; сходство с оригиналом (SSIM) и размер файла — в пределах допусков из .env.
Результаты каждого пути хранятся в logs/compression-results, поэтому прогон на телефоне сравнивается
с последним прогоном браузеров, сервера и ПК, и наоборот.
"""
import base64
import gzip
import hashlib
import io
import json
import shutil
import subprocess
import time
from pathlib import Path

from kovtest import case
from kovtest.config import env

CHUNK = 512 * 1024
PKG = "ru.kovcheg.kovapp"
INPUTS = (  # имя, вид, тип
    ("photo-4032.jpg", "photo", "image/jpeg"),
    ("photo-1600.png", "photo", "image/png"),
    ("video-12s-1080p.mp4", "video", "video/mp4"),
    ("video-64s-1080p.mp4", "video", "video/mp4"),
    ("document.csv", "document", "text/csv"),
)


def num(name, default):
    try:
        return float(env(name, str(default)) or default)
    except ValueError:
        return float(default)


def tolerances() -> dict:
    return {"photo_ssim": num("CMP_PHOTO_SSIM_DIFF", 0.02), "photo_size": num("CMP_PHOTO_SIZE_DIFF", 25),
            "video_dur": num("CMP_VIDEO_DURATION_S", 0.1), "video_ssim": num("CMP_VIDEO_SSIM_DIFF", 0.03),
            "video_size": num("CMP_VIDEO_SIZE_DIFF", 40), "doc_size": num("CMP_DOC_SIZE_DIFF", 2)}


# ===== исходники: одинаковые байты для всех путей и всех прогонов =====

def ffmpeg(*args) -> None:
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=True, timeout=900)


def inputs_dir(t) -> Path:
    folder = Path(t.log.dir).parent / "compression-inputs"
    folder.mkdir(parents=True, exist_ok=True)
    src = folder / "photo-4032.jpg"
    if not src.exists():
        # «снимок камеры»: мелкие детали и шум, как у настоящего фото
        ffmpeg("-f", "lavfi", "-i", "testsrc2=size=4032x3024:rate=1", "-vf", "noise=alls=18:allf=t", "-frames:v", "1",
               "-q:v", "2", str(src))
    if not (folder / "photo-1600.png").exists():
        ffmpeg("-f", "lavfi", "-i", "smptehdbars=size=1600x1200:rate=1", "-frames:v", "1", str(folder / "photo-1600.png"))
    for name, seconds, rate in (("video-12s-1080p.mp4", 12, "10M"), ("video-64s-1080p.mp4", 64, "6M")):
        if not (folder / name).exists():
            # поток выше нормы — «не подходит как есть», сжимать обязательно; звук AAC
            ffmpeg("-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=%d" % seconds,
                   "-f", "lavfi", "-i", "sine=frequency=440:duration=%d" % seconds,
                   "-vf", "noise=alls=10:allf=t", "-c:v", "libx264", "-preset", "veryfast", "-b:v", rate, "-minrate", rate,
                   "-maxrate", rate, "-bufsize", "4M", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                   "-movflags", "+faststart", "-shortest", str(folder / name))
    if not (folder / "document.csv").exists():
        rows = ["№;Задача;Отдел;Срок;Ответственный;Комментарий"]
        for i in range(30000):
            rows.append("%d;Монтаж узла %d;Монтажный отдел;2026-10-%02d;Иванов С. П.;проверить крепление и подписать акт" % (i, i % 97, i % 28 + 1))
        (folder / "document.csv").write_text("\n".join(rows), encoding="utf-8")
    return folder


# ===== что получилось: стороны, длительность, сходство с оригиналом =====

def probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                         capture_output=True, text=True, timeout=120)
    return json.loads(out.stdout or "{}")


def ssim(out: Path, src: Path, width: int, height: int, video: bool) -> float:
    """SSIM результата с исходником, приведённым к тем же сторонам (0…1, 1 — совпадает)."""
    flt = "[1:v]scale=%d:%d:flags=bicubic%s[r];[0:v]%s[o];[o][r]ssim" % (
        width, height, ",fps=30" if video else "", "fps=30" if video else "null")
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(out), "-i", str(src), "-lavfi", flt,
                           "-f", "null", "-"], capture_output=True, text=True, timeout=1800)
    for line in reversed((proc.stderr or "").splitlines()):
        if "All:" in line:
            try:
                return round(float(line.split("All:")[1].split()[0]), 4)
            except ValueError:
                break
    return 0.0


def analyze(kind: str, out: Path, src: Path) -> dict:
    info = {"size": out.stat().st_size}
    if kind == "document":
        data = out.read_bytes()
        plain = gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data
        info["same_bytes"] = hashlib.sha256(plain).hexdigest() == hashlib.sha256(src.read_bytes()).hexdigest()
        return info
    p = probe(out)
    v = next((s for s in p.get("streams") or [] if s.get("codec_type") == "video"), {}) or {}
    info.update(width=int(v.get("width") or 0), height=int(v.get("height") or 0), codec=v.get("codec_name"))
    if kind == "video":
        try:
            a, b = (v.get("avg_frame_rate") or "0/1").split("/")
            info["fps"] = round(float(a) / float(b or 1), 2)
        except (ValueError, ZeroDivisionError):
            info["fps"] = 0.0
        info["duration"] = round(float((p.get("format") or {}).get("duration") or 0), 2)
        info["audio_tracks"] = sum(1 for s in p.get("streams") or [] if s.get("codec_type") == "audio")
    if info.get("width") and info.get("height"):
        info["ssim"] = ssim(out, src, info["width"], info["height"], kind == "video")
    return info


# ===== передача файлов в страницу и обратно (сайт в Playwright или WebView по DevTools) =====

def ev(page, expression: str):
    return page.raw.evaluate(expression)


def to_page(page, key: str, data: bytes) -> None:
    ev(page, "(() => { window.__cmpIn = window.__cmpIn || {}; window.__cmpIn[%s] = []; return 0; })()" % json.dumps(key))
    for i in range(0, len(data), CHUNK):
        part = base64.b64encode(data[i:i + CHUNK]).decode("ascii")
        ev(page, "(() => { window.__cmpIn[%s].push(%s); return 0; })()" % (json.dumps(key), json.dumps(part)))


def from_page(page, key: str, size: int) -> bytes:
    out = bytearray()
    for i in range(0, size, CHUNK):
        part = ev(page, """(() => { const u = window.__cmpOut[%s].subarray(%d, %d); let s = '';
            for (let k = 0; k < u.length; k += 32768) s += String.fromCharCode.apply(null, u.subarray(k, k + 32768));
            return btoa(s); })()""" % (json.dumps(key), i, min(size, i + CHUNK)))
        out += base64.b64decode(part)
    return bytes(out)


PIPELINE = """(async () => {
  const key = %(key)s, kind = %(kind)s, name = %(name)s, type = %(type)s;
  let file = (window.__cmpFile || {})[key];
  if (!file) {
    const parts = (window.__cmpIn || {})[key] || [];
    file = new File(parts.map(b => Uint8Array.from(atob(b), c => c.charCodeAt(0))), name, { type });
  }
  let out = null, info = {};
  if (kind === 'photo') {
    const r = await Kov.ImagePrep.prepare(file);
    const f = r.file || file;
    info = { changed: !!r.changed, reason: r.reason || '', type: f.type || '' };
    out = new Uint8Array(await f.arrayBuffer());
  } else if (kind === 'video') {
    const plan = await Kov.VideoPrep.plan(file, {});
    info = { mode: plan.mode, reason: plan.reason || '', native: !!plan.native };
    if (plan.mode === 'device') {
      const chunks = [];
      await Kov.VideoPrep.compress(file, plan, b => { chunks.push(new Uint8Array(b)); }, {});
      const total = chunks.reduce((s, c) => s + c.length, 0);
      out = new Uint8Array(total);
      let o = 0;
      for (const c of chunks) { out.set(c, o); o += c.length; }
    }
  } else {
    out = Kov.Gzip.gzipSync(new Uint8Array(await file.arrayBuffer()), 9);
    info = { level: 9 };
  }
  window.__cmpOut = window.__cmpOut || {};
  window.__cmpOut[key] = out;
  return { info, size: out ? out.length : 0 };
})()"""


def run_in_page(page, folder: Path, name: str, kind: str, ctype: str, use_native: bool = False) -> dict:
    key = hashlib.md5(name.encode()).hexdigest()[:10]
    if not use_native:
        to_page(page, key, (folder / name).read_bytes())
    res = ev(page, PIPELINE % {"key": json.dumps(key), "kind": json.dumps(kind), "name": json.dumps(name), "type": json.dumps(ctype)})
    res = res or {}
    data = from_page(page, key, int(res.get("size") or 0)) if res.get("size") else b""
    ev(page, "(() => { delete (window.__cmpIn || {})[%s]; delete (window.__cmpOut || {})[%s]; return 0; })()" % (json.dumps(key), json.dumps(key)))
    return {"decision": res.get("info") or {}, "data": data}


# ===== путь «сервер»: исходник «как есть», сжимает сервер =====

def server_path(t, folder: Path, task, stage) -> dict:
    api = t.api("director")
    out = {}
    for name, kind, ctype in INPUTS:
        data = (folder / name).read_bytes()
        item = {"src": "file", "file": 0, "name": name, "size": len(data), "prep": "original"}
        response = api.post("upload", data={"task_id": str(task["id"]), "stage_detail_id": str(stage["id"]), "meta": json.dumps([item])},
                            files=[("files", (name, data, ctype))], timeout=1800)
        if response.status_code != 200:
            out[name] = {"error": "сервер не принял: %s %s" % (response.status_code, response.text[:120])}
            continue
        files = api.call("GET", "tasks/%d/stages/%d/files" % (task["id"], stage["id"]))
        stem = Path(name).stem
        row = next((f for f in sorted(files, key=lambda f: -int(f.get("id") or 0)) if Path(f.get("filename") or "").stem == stem), None)
        if row is None:
            out[name] = {"error": "файл не найден среди файлов этапа"}
            continue
        if kind == "document":
            # хранимый gzip — как есть (ce=1), без распаковки на сервере
            raw = api.get("stages/files/%d/download?ce=1" % row["id"], stream=True, headers={"Accept-Encoding": "gzip"})
            body = raw.raw.read(decode_content=False)
        else:
            body = api.get("stages/files/%d/download" % row["id"], timeout=600).content
        out[name] = {"data": body, "decision": {"stored_as": row.get("filename")}}
    return out


# ===== путь «телефон»: видео — через галерею приложения, чтобы сжимал родной кодек =====

NATIVE_PICK = """(async () => {
  const P = window.Kov && Kov.AttachSheet && Kov.AttachSheet.provider;
  if (!P) return { error: 'в приложении нет галереи' };
  const list = await P.recent(0, 90) || [];
  const it = list.find(x => (x.name || '') === %(name)s);
  if (!it) return { error: 'файла нет в галерее телефона' };
  await P.pick([it.id]);
  if ((await P.preset([it.id])) !== 1) return { error: 'выбор не заготовлен' };
  const input = document.createElement('input'); input.type = 'file'; input.hidden = true; document.body.appendChild(input);
  const files = await new Promise(res => { const tm = setTimeout(() => res(null), 8000);
    input.addEventListener('change', () => { clearTimeout(tm); res(Array.from(input.files || [])); }, { once: true }); input.click(); });
  input.remove();
  if (!files || !files.length) return { error: 'выбор не вернул файл' };
  window.__cmpFile = window.__cmpFile || {}; window.__cmpFile[%(key)s] = files[0];
  return { ok: true, size: files[0].size };
})()"""


def phone_gallery(t, folder: Path, name: str) -> str:
    """Положить видео в галерею эмулятора и выбрать его так, как выбирает человек. Пусто — получилось."""
    dev = t.world.device
    driver = getattr(dev, "driver", None)
    if driver is None:
        return "нет связи с телефоном через Appium"
    remote = "/sdcard/DCIM/KovappCmp/" + name
    try:
        driver.push_file(remote, base64.b64encode((folder / name).read_bytes()).decode("ascii"))
        for perm in ("android.permission.READ_MEDIA_VIDEO", "android.permission.READ_MEDIA_IMAGES", "android.permission.READ_EXTERNAL_STORAGE"):
            try:
                driver.execute_script("mobile: changePermissions", {"permissions": [perm], "appPackage": PKG, "action": "grant"})
            except Exception:
                pass
        for command in ("am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d file://" + remote,
                        "content call --uri content://media --method scan_volume --arg external_primary"):
            try:
                dev.shell(command)
            except Exception:
                pass
    except Exception as e:
        return "файл не положен на телефон: %s" % str(e)[:120]
    key = hashlib.md5(name.encode()).hexdigest()[:10]
    deadline = time.time() + 30
    res = {}
    while time.time() < deadline:
        res = t.page_device_eval(NATIVE_PICK % {"name": json.dumps(name), "key": json.dumps(key)}) if hasattr(t, "page_device_eval") else None
        if res is None:
            break
        if res.get("ok"):
            return ""
        time.sleep(2)
    return (res or {}).get("error") or "галерея недоступна"


# ===== сравнение путей =====

def results_dir(t) -> Path:
    folder = Path(t.log.dir).parent / "compression-results"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def save_path(t, path_name: str, results: dict) -> None:
    folder = results_dir(t) / path_name.replace(" ", "_")
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    meta = {"path": path_name, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "run": Path(t.log.dir).name, "items": {}}
    for name, r in results.items():
        entry = {k: v for k, v in r.items() if k != "data"}
        if r.get("data"):
            out = folder / ("out-" + name + (".gz" if name.endswith(".csv") else ""))
            out.write_bytes(r["data"])
            entry["file"] = out.name
        meta["items"][name] = entry
    (folder / "result.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


def load_all(t) -> dict:
    out = {}
    for f in sorted(results_dir(t).glob("*/result.json")):
        try:
            meta = json.loads(f.read_text(encoding="utf-8"))
            out[meta["path"]] = meta
        except Exception:
            continue
    return out


def complete_analysis(t, sources: dict) -> None:
    """Результаты, сохранённые без разбора (ПК: там нет полного FFmpeg), разбираются здесь и дописываются."""
    kinds = dict((n, k) for n, k, _ in INPUTS)
    for f in sorted(results_dir(t).glob("*/result.json")):
        try:
            meta = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        changed = False
        for name, item in (meta.get("items") or {}).items():
            out = f.parent / str(item.get("file") or "")
            if item.get("analysis") or not item.get("file") or name not in kinds or not out.is_file():
                continue
            try:
                item["analysis"] = analyze(kinds[name], out, sources[name])
            except Exception as e:
                item["error"] = "результат не разобран: %s" % str(e)[:150]
            changed = True
        if changed:
            f.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
            t.log.info("разобран результат «%s» (%s)" % (meta.get("path"), meta.get("at")))


def spread(values) -> float:
    values = [v for v in values if v]
    return (max(values) / min(values) - 1) * 100 if len(values) > 1 and min(values) > 0 else 0.0


def compare(t, folder: Path) -> list:
    tol = tolerances()
    paths = load_all(t)
    lines = []
    for name, kind, _ in INPUTS:
        got = {p: m["items"].get(name) for p, m in paths.items() if (m["items"].get(name) or {}).get("analysis")}
        if len(got) < 2:
            lines.append((name, {p: (m["items"].get(name) or {}) for p, m in paths.items()}, ["сравнивать не с чем: путей с результатом %d" % len(got)], []))
            continue
        errors, warns = [], []
        a = {p: g["analysis"] for p, g in got.items()}
        if kind == "document":
            for p, x in a.items():
                if not x.get("same_bytes"):
                    errors.append("%s: после распаковки файл отличается от исходного" % p)
            sp = spread([x["size"] for x in a.values()])
            if sp > tol["doc_size"]:
                warns.append("размер сжатого различается на %.1f%% (допуск %.0f%%)" % (sp, tol["doc_size"]))
        else:
            dims = {(x.get("width"), x.get("height")) for x in a.values()}
            if len(dims) > 1:
                errors.append("стороны различаются: " + ", ".join("%s %sx%s" % (p, x.get("width"), x.get("height")) for p, x in a.items()))
            if kind == "video":
                if len({x.get("fps") for x in a.values()}) > 1:
                    errors.append("частота кадров различается: " + ", ".join("%s %s" % (p, x.get("fps")) for p, x in a.items()))
                durs = [x.get("duration") or 0 for x in a.values()]
                if max(durs) - min(durs) > tol["video_dur"]:
                    errors.append("длительность различается на %.2f с (допуск %.2f)" % (max(durs) - min(durs), tol["video_dur"]))
                if len({x.get("audio_tracks") for x in a.values()}) > 1:
                    errors.append("число звуковых дорожек различается: " + ", ".join("%s %s" % (p, x.get("audio_tracks")) for p, x in a.items()))
            ss = [x.get("ssim") or 0 for x in a.values()]
            limit = tol["video_ssim" if kind == "video" else "photo_ssim"]
            if max(ss) - min(ss) > limit:
                warns.append("сходство с оригиналом (SSIM) различается на %.3f (допуск %.3f): %s" % (
                    max(ss) - min(ss), limit, ", ".join("%s %.3f" % (p, x.get("ssim") or 0) for p, x in a.items())))
            sp = spread([x["size"] for x in a.values()])
            size_limit = tol["video_size" if kind == "video" else "photo_size"]
            if sp > size_limit:
                warns.append("размер файла различается на %.0f%% (допуск %.0f%%): %s" % (
                    sp, size_limit, ", ".join("%s %.2f МБ" % (p, x["size"] / 1048576) for p, x in a.items())))
        lines.append((name, got, errors, warns))
    return lines


def write_report(t, lines) -> Path:
    rows = []
    for name, got, errors, warns in lines:
        cells = []
        for p, g in sorted(got.items()):
            x = (g or {}).get("analysis") or {}
            d = (g or {}).get("decision") or {}
            text = "%.2f МБ" % (x["size"] / 1048576) if x.get("size") else (g or {}).get("error") or "—"
            if x.get("width"):
                text += ", %sx%s" % (x["width"], x["height"])
            if x.get("fps"):
                text += ", %s к/с, %s с, звук: %s" % (x["fps"], x.get("duration"), x.get("audio_tracks"))
            if x.get("ssim"):
                text += ", SSIM %.3f" % x["ssim"]
            if "same_bytes" in x:
                text += ", байт в байт" if x["same_bytes"] else ", ОТЛИЧАЕТСЯ"
            if d.get("mode"):
                how = "родной кодек телефона" if d.get("native") else (d.get("encoder") or d["mode"])
                text += " <i>(%s%s)</i>" % (how, ": " + d["reason"] if d.get("reason") else "")
            cells.append("<b>%s</b>: %s" % (p, text))
        verdict = "<span class='bad'>%s</span>" % "; ".join(errors) if errors else ("<span class='warn'>%s</span>" % "; ".join(warns) if warns else "<span class='ok'>совпадает</span>")
        rows.append("<tr><td>%s</td><td>%s</td><td>%s</td></tr>" % (name, "<br>".join(cells), verdict))
    html = """<!doctype html><html lang="ru"><meta charset="utf-8"><title>Качество сжатия — Kovapp</title>
<style>body{font:14px/1.45 system-ui,Segoe UI,Roboto,sans-serif;margin:24px;color:#132A32;background:#F1F4F6}
table{border-collapse:collapse;background:#fff}td,th{border:1px solid #D5DDE2;padding:8px 10px;vertical-align:top}th{background:#E8EEF1}
.ok{color:#2E7D32}.warn{color:#B26A00}.bad{color:#C62828;font-weight:600}</style>
<h1>Качество сжатия: одинаковый ли результат там, где сжимают</h1>
<p>Результаты разных мест сравниваются между собой. Стороны, частота кадров, длительность и звуковые дорожки должны совпадать;
сходство с оригиналом (SSIM, 1 — без потерь) и размер — в пределах допусков .env (CMP_*).</p>
<table><tr><th>Исходник</th><th>Результат по местам сжатия</th><th>Итог</th></tr>%s</table></html>""" % "".join(rows)
    path = Path(t.log.dir) / "compression-report.html"
    path.write_text(html, encoding="utf-8")
    return path


# ===== сама проверка =====

@case("Качество сжатия: браузеры, сервер, телефон и ПК дают одинаковый результат", layer="api", order=1)
def compression_quality(t):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        t.skip("нет ffmpeg в контейнере тестов")
    folder = inputs_dir(t)
    sources = {name: folder / name for name, _, _ in INPUTS}
    target = getattr(t.settings, "target", "web")
    done = []
    if target == "device":
        page = t.page("director", "light", "phone", path="#/tasks")
        t.page_device_eval = lambda expr: page.raw.evaluate(expr)
        results = {}
        for name, kind, ctype in INPUTS:
            native = False
            if kind == "video":
                problem = phone_gallery(t, folder, name)
                native = not problem
                if problem:
                    t.warn("%s: родной кодек телефона не проверен (%s) — сжимает WebView приложения" % (name, problem))
            try:
                r = run_in_page(page, folder, name, kind, ctype, use_native=native)
            except Exception as e:
                results[name] = {"error": str(e)[:200]}
                continue
            if kind == "video" and r["decision"].get("mode") != "device":
                results[name] = {"decision": r["decision"], "error": "телефон не сжимал (%s)" % r["decision"].get("reason")}
                continue
            results[name] = r
        label = "телефон"
        for name, r in results.items():
            if r.get("data"):
                tmp = Path(t.log.dir) / ("phone-" + name + (".gz" if name.endswith(".csv") else ""))
                tmp.write_bytes(r["data"])
                r["analysis"] = analyze(dict((n, k) for n, k, _ in INPUTS)[name], tmp, sources[name])
        save_path(t, label, results)
        done.append(label)
    else:
        engines = [e.strip().lower() for e in env("CMP_BROWSERS", "chrome,firefox,webkit").split(",")
                   if e.strip() and e.strip().lower() not in ("none", "0")]
        for engine in engines:
            try:
                page = t.page("director", "light", "desktop", path="#/tasks", engine=engine)
            except Exception as e:
                t.warn("браузер %s не открылся: %s" % (engine, str(e)[:120]))
                continue
            results = {}
            for name, kind, ctype in INPUTS:
                try:
                    r = run_in_page(page, folder, name, kind, ctype)
                except Exception as e:
                    results[name] = {"error": str(e)[:200]}
                    continue
                if kind == "video" and r["decision"].get("mode") != "device":
                    # браузер отдал видео серверу — результат этого места = результат сервера
                    results[name] = {"decision": r["decision"], "error": "браузер не сжимал — отдаёт серверу (%s)" % r["decision"].get("reason")}
                    continue
                results[name] = r
            label = "браузер " + engine
            for name, r in results.items():
                if r.get("data"):
                    tmp = Path(t.log.dir) / (engine + "-" + name + (".gz" if name.endswith(".csv") else ""))
                    tmp.write_bytes(r["data"])
                    r["analysis"] = analyze(dict((n, k) for n, k, _ in INPUTS)[name], tmp, sources[name])
            save_path(t, label, results)
            done.append(label)
        if env("CMP_SERVER", "1") != "0":
            task = t.data.create_task("director", "Качество сжатия", ["Монтажный отдел"], responsible=t.data.ids("head_mont"))
            stage = task["stage_details"][0]
            results = server_path(t, folder, task, stage)
            for name, r in results.items():
                if r.get("data"):
                    tmp = Path(t.log.dir) / ("server-" + name + (".gz" if name.endswith(".csv") else ""))
                    tmp.write_bytes(r["data"])
                    r["analysis"] = analyze(dict((n, k) for n, k, _ in INPUTS)[name], tmp, sources[name])
            save_path(t, "сервер", results)
            done.append("сервер")
    complete_analysis(t, sources)
    t.log.info("сжато в этом прогоне: %s; хранятся результаты: %s" % (", ".join(done) or "—", ", ".join(sorted(load_all(t)))))
    lines = compare(t, folder)
    report = write_report(t, lines)
    t.log.info("отчёт сравнения: %s" % report.name)
    for name, got, errors, warns in lines:
        for e in errors:
            t.fail("%s: %s" % (name, e))
        for w in warns:
            t.warn("%s: %s" % (name, w))
        for p, g in got.items():
            if (g or {}).get("error"):
                t.warn("%s, %s: %s" % (name, p, g["error"]))
