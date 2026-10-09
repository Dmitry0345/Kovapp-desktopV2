import base64
import shutil
import subprocess
import tempfile
from pathlib import Path

from kovtest import case

_BIG_VIDEO = {}


def big_video(t):
    """Ролик, который страница обязана сжимать (1080p, высокий битрейт): готовый к отправке
    образец из MediaKit страница правильно отправляет как есть и телефон не спрашивает."""
    if "data" not in _BIG_VIDEO:
        if not shutil.which("ffmpeg"):
            t.skip("нет ffmpeg — не из чего сделать ролик для проверки")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "big.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30", "-t", "2",
                            "-c:v", "libx264", "-b:v", "14M", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-y", str(out)],
                           check=True, timeout=120)
            _BIG_VIDEO["data"] = out.read_bytes()
    return "clip-1080p-autotest.mp4", _BIG_VIDEO["data"]


def apk(t):
    if t.settings.target != "apk":
        t.skip("раздел мобильной версии: запуск run.cmd mobile")
    return t.world.apk


# ===== HTTPS: всё делает сама страница, как сайт =====

@case("По HTTPS видео, фото и отпечатки обрабатывает сама страница — как сайт", layer="mobile", order=1)
def secure_paths(t):
    env = apk(t)
    page = t.page("worker_mont", "light", "mobile", path="#/tasks")
    info = page.raw.evaluate("""async () => {
      const file = new File([new Uint8Array(200000).fill(3)], 'big.bin');
      __kovDev.pick(file);
      const hex = await Kov.FileHash.tree(file);
      return { secure: window.isSecureContext, fast: Kov.FileHash.fast(), webcodecs: typeof VideoEncoder !== 'undefined',
               hex: typeof hex === 'string' && hex.length > 0, native: __kovDev.calls('media.hashTree').length };
    }""")
    t.check(info["secure"] and info["fast"], "страница приложения не «защищённая» — без HTTPS пропадут WebCodecs и отпечатки: %s" % info)
    t.check(info["webcodecs"], "в приложении нет WebCodecs — видео пойдёт на запасной путь телефона")
    t.check(info["hex"] and info["native"] == 0, "по HTTPS отпечаток должен считать код страницы: %s" % info)
    page.assert_clean("пути HTTPS")


# ===== HTTP: запасные пути через телефон =====

@case("По HTTP отпечаток выбранного файла считает телефон, остальных — страница", layer="mobile", order=2)
def http_hash(t):
    apk(t)
    page = t.page("worker_mont", "light", "mobile", path="#/tasks", variant="http")
    got = page.raw.evaluate("""async () => {
      const file = new File([new Uint8Array(1024 * 1024).fill(7)], 'big.bin');
      const handle = __kovDev.pick(file);
      const progress = [];
      __kovDev.handlers['media.hashTree'] = a => { __kovDev.emit('media.progress', { job: a.job, p: 1 });
        return a.handle === handle ? 'ab'.repeat(32) : Promise.reject(new Error('чужой файл')); };
      __kovDev.handlers['media.hashRange'] = () => 'cd'.repeat(32);
      const tree = await Kov.FileHash.tree(file, p => progress.push(p));
      const range = await Kov.FileHash.range(file, 0, 4096);
      const other = await Kov.FileHash.tree(new File([new Uint8Array(1000).fill(1)], 'small.bin'));
      return { secure: window.isSecureContext, tree, range, progress, other: typeof other === 'string' && other.length > 0 && other !== tree };
    }""")
    t.check(got["secure"] is False, "страница по HTTP почему-то «защищённая» — проверка запасного пути невозможна")
    t.check(got["tree"] == "ab" * 32 and got["range"] == "cd" * 32 and got["progress"] == [1],
            "по HTTP отпечаток выбранного файла посчитал не телефон: %s" % got)
    t.check(got["other"], "по HTTP файл, выбранный не в приложении, остался без отпечатка")
    t.world.apk.forget_http_notes(page)
    page.forget_network_noise()
    page.assert_clean("отпечатки по HTTP")


@case("По HTTP решение о сжатии видео спрашивается у телефона", layer="mobile", order=3)
def http_video(t):
    apk(t)
    name, data, ctype = t.world.media.get("mp4")
    page = t.page("worker_mont", "light", "mobile", path="#/tasks", variant="http")
    got = page.raw.evaluate("""async ([b64, name]) => {
      const file = new File([Uint8Array.from(atob(b64), c => c.charCodeAt(0))], name, { type: 'video/mp4' });
      __kovDev.pick(file);
      __kovDev.handlers['media.videoPlan'] = () => ({ mode: 'server', reason: 'slow:900s' });
      const plan = await Kov.VideoPrep.plan(file, { edits: null });
      return { mode: plan.mode, reason: plan.reason, asked: __kovDev.calls('media.videoPlan').length };
    }""", [base64.b64encode(data).decode(), name])
    t.check(got["asked"] == 1 and got["reason"].startswith("no-webcodecs") and "телефон: slow" in got["reason"],
            "по HTTP страница не спросила телефон о сжатии видео: %s" % got)
    t.world.apk.forget_http_notes(page)
    page.forget_network_noise()
    page.assert_clean("видео по HTTP")


# ===== то, с чем страница не справилась: телефон =====

@case("Фото, которое браузерный движок не распаковал (HEIC), сжимает телефон", layer="mobile", order=4)
def heic_photo(t):
    apk(t)
    page = t.page("worker_mont", "light", "mobile", path="#/tasks")
    got = page.raw.evaluate("""async () => {
      const file = new File([new Uint8Array([0,0,0,24,102,116,121,112,104,101,105,99,0,0,0,0])], 'IMG_0001.HEIC', { type: 'image/heic' });
      const handle = __kovDev.pick(file);
      const canvas = document.createElement('canvas'); canvas.width = 640; canvas.height = 480;
      const g = canvas.getContext('2d'); g.fillStyle = '#2F6F7E'; g.fillRect(0, 0, 640, 480); g.fillStyle = '#F5B301'; g.fillRect(100, 100, 200, 150);
      const png = await new Promise(r => canvas.toBlob(r, 'image/png'));
      const webp = await new Promise(r => canvas.toBlob(r, 'image/webp', 0.8));
      const previewUrl = await __kovDev.temp(png), fileUrl = await __kovDev.temp(webp);
      __kovDev.handlers['media.image'] = a => a.handle === handle ? { changed: true, width: 3200, height: 2400, mime: 'image/webp',
        name: 'IMG_0001.webp', reason: '', file: { url: fileUrl, size: webp.size }, preview: { url: previewUrl, width: 640, height: 480 } }
        : Promise.reject(new Error('чужой файл'));
      const out = await Kov.ImagePrep.prepare(file);
      return { changed: out.changed, type: out.file.type, name: out.file.name, same: out.file.size === webp.size,
               hash: !!out.thumbHash, thumb: out.thumb ? out.thumb.size : 0, thumbW: out.thumbWidth };
    }""")
    t.check(got["changed"] and got["type"] == "image/webp" and got["name"] == "IMG_0001.webp" and got["same"],
            "HEIC не сжат телефоном: %s" % got)
    t.check(got["hash"] and got["thumb"] > 0 and got["thumbW"] == 320, "миниатюра и заглушка HEIC не построены кодом сайта: %s" % got)
    page.assert_clean("HEIC")


@case("Видео, которое страница сжать не может, сжимает кодек телефона; куски собираются по порядку", layer="mobile", order=5)
def native_video(t):
    apk(t)
    name, data = big_video(t)
    page = t.page("worker_mont", "light", "mobile", path="#/tasks")
    got = page.raw.evaluate("""async ([b64, name]) => {
      const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
      const file = new File([bytes], name, { type: 'video/mp4' });
      const handle = __kovDev.pick(file);
      if (window.VideoDecoder) VideoDecoder.isConfigSupported = async cfg => ({ supported: false, config: cfg });
      const cut = Math.floor(bytes.length / 3);
      const parts = [bytes.slice(0, cut), bytes.slice(cut, 2 * cut), bytes.slice(2 * cut)];
      const urls = [];
      for (const p of parts) urls.push(await __kovDev.temp(new Blob([p])));
      __kovDev.handlers['media.videoPlan'] = a => a.handle === handle
        ? { mode: 'device', job: 'j1', width: 1280, height: 720, fps: 30, estimate: 10, side: 1280, duration: 3 }
        : { mode: 'server', reason: 'x' };
      __kovDev.handlers['media.videoRun'] = async a => {
        __kovDev.emit('media.segment', { job: a.job, index: 1, url: urls[1], size: parts[1].length });
        __kovDev.emit('media.segment', { job: a.job, index: 0, url: urls[0], size: parts[0].length });
        __kovDev.emit('media.segment', { job: a.job, index: 2, url: urls[2], size: parts[2].length });
        return { frames: 90, encoded: 90, segments: 3 };
      };
      const plan = await Kov.VideoPrep.plan(file, { edits: null });
      if (plan.mode !== 'device') return { plan: plan.mode, reason: plan.reason };
      const got = [];
      const res = await Kov.VideoPrep.compress(file, plan, c => { got.push(c); }, {});
      const joined = new Uint8Array(got.reduce((n, c) => n + c.length, 0));
      let o = 0; for (const c of got) { joined.set(c, o); o += c.length; }
      return { plan: plan.mode, native: plan.native, frames: res.frames,
               same: joined.length === bytes.length && joined.every((v, i) => v === bytes[i]) };
    }""", [base64.b64encode(data).decode(), name])
    t.check(got.get("plan") == "device" and got.get("native") == "j1", "план телефона не встал на место плана страницы: %s" % got)
    t.check(got.get("same") and got.get("frames") == 90, "куски видео от телефона собраны не по порядку или не целиком: %s" % got)
    page.assert_clean("видео телефоном")
