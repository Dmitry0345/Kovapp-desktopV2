import json
import re
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from .api import ApiError, hide_token
from .config import load_yaml

AUDIT_JS = (Path(__file__).resolve().parent / "audit.js").read_text(encoding="utf-8")

UPLOAD_RECORDER = r"""(() => {
  const log = window.__kovUploads = [];
  const describe = (url, body, method) => {
    const entry = { url: String(url || ''), method: String(method || 'GET').toUpperCase(), at: Date.now(), parts: [] };
    if (typeof FormData !== 'undefined' && body instanceof FormData) {
      for (const [field, value] of body.entries()) {
        if (typeof Blob !== 'undefined' && value instanceof Blob) entry.parts.push({ field, name: value.name || '', type: value.type || '', size: value.size });
        else entry.parts.push({ field, value: String(value).slice(0, 4000) });
      }
    } else if (typeof Blob !== 'undefined' && body instanceof Blob) {
      entry.parts.push({ field: 'body', type: body.type || '', size: body.size });
    } else if (body && (body instanceof ArrayBuffer || ArrayBuffer.isView(body))) {
      entry.parts.push({ field: 'body', size: body.byteLength });
    } else if (typeof body === 'string') {
      entry.parts.push({ field: 'text', value: body.slice(0, 4000) });
    }
    if (/upload/.test(entry.url)) log.push(entry);
  };
  const send = XMLHttpRequest.prototype.send;
  const open = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url) { this.__kovUrl = url; this.__kovMethod = method; return open.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function (body) { try { describe(this.__kovUrl, body, this.__kovMethod); } catch (e) {} return send.apply(this, arguments); };
  const originalFetch = window.fetch;
  window.fetch = function (input, init) {
    const url = typeof input === 'string' ? input : (input && input.url);
    const method = (init && init.method) || (input && input.method) || 'GET';
    try { describe(url, init && init.body, method); } catch (e) {}
    const delay = Number(window.__kovUploadDelay || 0);
    if (delay > 0 && String(method).toUpperCase() === 'PUT' && /uploads\/sessions\/[0-9a-f]{32}/.test(String(url))) {
      const self = this, args = arguments;
      return new Promise(resolve => setTimeout(resolve, delay)).then(() => originalFetch.apply(self, args));
    }
    return originalFetch.apply(this, arguments);
  };
})();"""


# ===== страница приложения =====

class KovPage:
    def __init__(self, raw, context, role, theme, device, browser, world, t, engine="chromium", app_url=""):
        self.raw = raw
        self.app_url = app_url or world.settings.app_url       # сайт или интерфейс APK
        self.variant = "apk" if world.settings.target == "apk" else "web"
        self.engine = engine
        self.context = context
        self.role = role
        self.theme = theme
        self.device = device
        self.browser = browser
        self.world = world
        self.t = t
        self.ui = world.ui
        self.console = []
        self.http_errors = []
        self.stamps = {}
        self.outages = []
        self._outage = None
        self.pending = set()
        self.opened = False
        raw.on("console", self._on_console)
        raw.on("pageerror", lambda e: self._note(self.console, "ошибка скрипта: %s" % e))
        raw.on("request", self._on_request)
        raw.on("requestfinished", lambda r: self.pending.discard(r))
        raw.on("requestfailed", self._on_failed)
        raw.on("response", self._on_response)

    @property
    def who(self) -> str:
        return "%s/%s/%s/%s/%s" % (self.engine, self.browser, self.role, self.theme, self.device)

    def _on_console(self, message):
        if message.type in ("error",):
            text = message.text
            if not any(p in text for p in self.ui["audit"]["allowed_console"]):
                self._note(self.console, text)

    def _on_request(self, request):
        if request.resource_type not in ("media", "websocket", "eventsource"):
            self.pending.add(request)

    def _on_failed(self, request):
        self.pending.discard(request)
        failure = request.failure or ""
        if "ERR_ABORTED" in str(failure) or request.resource_type == "media":
            return
        self._note(self.http_errors, "запрос не выполнен: %s %s (%s)" % (request.method, hide_token(request.url), failure))

    def _on_response(self, response):
        status = response.status
        if status < 400:
            return
        path = re.sub(r"^https?://[^/]+", "", hide_token(response.url)).split("?")[0]
        line = "%d %s" % (status, path)
        if any(re.search(p, line) for p in self.ui["audit"]["allowed_http"]):
            return
        self._note(self.http_errors, "ответ %d на %s %s" % (status, response.request.method, path))

    # ===== переходы =====

    def goto(self, route: str = "#/tasks", wait: bool = True) -> "KovPage":
        if not self.opened:
            self.raw.goto(self.app_url + route)
            self.opened = True
        else:
            self.raw.evaluate("r => { location.hash = r; }", route)
        if wait:
            self.wait_ready()
        return self

    def wait_idle(self, quiet_ms: int = 450, timeout: float = 20.0) -> None:
        deadline = time.time() + timeout
        quiet_since = None
        while time.time() < deadline:
            if self.pending:
                quiet_since = None
            elif quiet_since is None:
                quiet_since = time.time()
            elif (time.time() - quiet_since) * 1000 >= quiet_ms:
                return
            self.raw.wait_for_timeout(100)

    def wait_ready(self, timeout: float = 25.0) -> None:
        deadline = time.time() + timeout
        self.raw.wait_for_selector(".app .content, .login-screen", state="attached", timeout=timeout * 1000)
        while time.time() < deadline:
            busy = self.raw.evaluate(
                "() => [...document.querySelectorAll('.content .big-loading, .content .loading, .modal .loading')]"
                ".some(el => el.offsetParent !== null)")
            if not busy:
                break
            self.raw.wait_for_timeout(150)
        self.wait_idle(timeout=max(1.0, deadline - time.time()))
        # полоска «Подключение…» сверху: пока она видна, на телефоне она закрывает кнопки под шапкой;
        # и сохранённые тема и масштаб применяются чуть позже загрузки (отметка data-settings)
        try:
            self.raw.wait_for_function("() => { const b = document.querySelector('.link-bar'); return !b || b.hidden || !b.offsetParent; }",
                                       timeout=4000)
            if self.raw.locator(".app .content").count():
                self.raw.wait_for_function("() => document.documentElement.dataset.settings === 'applied'", timeout=6000)
        except Exception:
            pass
        self.raw.wait_for_timeout(250)

    def menu(self, key: str) -> None:
        self.raw.click(".menu-btn")
        self.raw.wait_for_selector(".side-menu.open", timeout=5000)
        self.raw.click(".side-menu nav a.item[data-key='%s']" % key)
        self.wait_ready()

    def menu_keys(self) -> list:
        return self.raw.eval_on_selector_all(".side-menu nav a.item", "els => els.map(e => e.dataset.key)")

    # ===== поиск элементов =====

    def button(self, text: str, scope: str = "body"):
        return self.raw.locator(scope).locator("button", has_text=text)

    def visible_button(self, text: str, scope: str = "body"):
        loc = self.button(text, scope)
        for i in range(loc.count()):
            item = loc.nth(i)
            if item.is_visible():
                return item
        return None

    def is_locked(self, locator) -> bool:
        return "is-locked" in (locator.get_attribute("class") or "")

    def modal(self):
        return self.raw.locator(".modal-root .modal").last

    def close_modal(self) -> None:
        self.close_overlays()

    def close_overlays(self) -> None:
        """Закрыть окна (Escape) и шторки снизу (кнопка «×»): на телефоне фильтры и меню — шторками."""
        for _ in range(4):
            # раскрытый список выбора с поиском (организация, создатель) закрывается Escape
            if self.raw.evaluate("() => [...document.querySelectorAll('.picker-panel')].some(p => !p.hidden && p.offsetParent)"):
                self.raw.keyboard.press("Escape")
                self.raw.wait_for_timeout(150)
                continue
            if self.raw.locator(".modal-root").count():
                self.raw.keyboard.press("Escape")
                self.raw.wait_for_timeout(200)
                continue
            cross = self.raw.locator(".kov-sheet-root .kov-sheet-x")
            if cross.count():
                try:
                    cross.last.click(timeout=3000)
                except Exception:
                    self.raw.evaluate("() => { const r = [...document.querySelectorAll('.kov-sheet-root')].pop(); if (r) r.remove(); }")
                self.raw.wait_for_timeout(300)
                continue
            break

    def text(self, selector: str) -> str:
        return (self.raw.locator(selector).first.inner_text() or "").strip()

    def toast_texts(self) -> list:
        return self.raw.eval_on_selector_all("#kov-toasts .kov-toast", "els => els.map(e => e.innerText)")

    # ===== проверки вёрстки =====

    def audit(self, name: str, allow_page_scroll_x: bool = False, shot: bool = True) -> dict:
        cfg = self.ui["audit"]
        options = {
            "interactive": cfg["interactive"], "ignore": cfg["ignore"], "containers": cfg["containers"],
            "allowOverflow": cfg["allow_overflow"], "textClip": cfg["text_clip"],
            "touchMin": self.world.settings.touch_min, "touchWarn": self.world.settings.touch_warn,
            "mobile": self.device != "desktop" or self.world.settings.target == "apk", "allowPageScrollX": allow_page_scroll_x,
            "overlays": cfg.get("overlays") or [],
        }
        result = self.raw.evaluate(AUDIT_JS, options)
        where = "%s [%s]" % (name, self.who)
        for message in dict.fromkeys(result["errors"]):
            self.t.fail("%s: %s" % (where, message))
        for message in dict.fromkeys(result["warnings"]):
            self.t.warn("%s: %s" % (where, message))
        self.t.log.info("вёрстка %s: интерактивных %d, проверено %d, картинок %d, видео %d" % (
            where, result["stats"]["interactive"], result["stats"]["checked"], result["stats"]["images"],
            result["stats"]["videos"]))
        if shot and self.world.settings.screenshots == "all":
            self.t.screenshot(self, "%s_%s" % (name, self.who.replace("/", "_")))
        return result

    def _note(self, bucket, text):
        bucket.append(text)
        self.stamps.setdefault(text, []).append(time.time())

    def outage_started(self) -> None:
        self._outage = time.time()

    def outage_ended(self) -> None:
        if self._outage is not None:
            self.outages.append((self._outage, time.time()))
            self._outage = None

    def forget_network_noise(self) -> None:
        # сетевые ошибки прощаются, только если пришлись на устроенный тестом обрыв (если обрывы отмечались)
        noise = ("ERR_INTERNET_DISCONNECTED", "ERR_NETWORK_CHANGED", "ERR_CONNECTION", "Failed to fetch",
                 "NetworkError", "Load failed", "network connection was lost", "ERR_NAME_NOT_RESOLVED",
                 "ERR_ABORTED", "ERR_FAILED", "WebSocket", "NS_ERROR_OFFLINE", "NS_ERROR_NET_RESET",
                 "NS_ERROR_NET_INTERRUPT", "NS_ERROR_CONNECTION_REFUSED", "NS_BINDING_ABORTED",
                 "Blocked by Web Inspector", "WebKit encountered an internal error", "Could not connect to the server")
        windows = list(self.outages) + ([(self._outage, time.time())] if self._outage is not None else [])

        def expected(text):
            if not any(n in text for n in noise):
                return False
            if not windows:
                return True
            return any(start - 1 <= at <= end + 5 for at in self.stamps.get(text, []) for start, end in windows)
        self.console = [m for m in self.console if not expected(m)]
        self.http_errors = [m for m in self.http_errors if not expected(m)]

    def assert_clean(self, name: str) -> None:
        for message in dict.fromkeys(self.console):
            self.t.fail("%s [%s]: ошибка в консоли браузера — %s" % (name, self.who, message[:300]))
        for message in dict.fromkeys(self.http_errors):
            self.t.fail("%s [%s]: %s" % (name, self.who, message))
        self.console.clear()
        self.http_errors.clear()

    def media_state(self, selector: str = "img") -> list:
        return self.raw.eval_on_selector_all(selector, """els => els.filter(e => e.offsetParent !== null).map(e => ({
            src: (e.currentSrc || e.src || '').replace(/token=[^&]+/, 'token=…'),
            complete: e.complete, w: e.naturalWidth || e.videoWidth || 0, h: e.naturalHeight || e.videoHeight || 0,
            ready: e.readyState, error: e.error ? e.error.code : null }))""")

    def image_painted(self, selector: str) -> bool:
        return self.raw.eval_on_selector(selector, """img => {
            if (!img.complete || !img.naturalWidth) return false;
            const c = document.createElement('canvas');
            c.width = Math.min(64, img.naturalWidth); c.height = Math.min(64, img.naturalHeight);
            const g = c.getContext('2d');
            g.drawImage(img, 0, 0, c.width, c.height);
            const d = g.getImageData(0, 0, c.width, c.height).data;
            let min = 765, max = 0;
            for (let i = 0; i < d.length; i += 4) { const v = d[i] + d[i + 1] + d[i + 2]; if (v < min) min = v; if (v > max) max = v; }
            return max - min > 30;
        }""")

    def video_plays(self, selector: str = ".viewer-root .mv-stage video", seconds: float = 6.0) -> dict:
        return self.raw.eval_on_selector(selector, """(v, ms) => new Promise(resolve => {
            v.muted = true;
            const start = performance.now();
            const done = ok => resolve({ ok, time: v.currentTime, ready: v.readyState, w: v.videoWidth,
                                          error: v.error ? v.error.code : null });
            v.play().catch(() => {});
            const tick = () => {
                if (v.error) return done(false);
                if (v.currentTime > 0.3 && v.videoWidth > 0) return done(true);
                if (performance.now() - start > ms) return done(false);
                setTimeout(tick, 150);
            };
            tick();
        })""", seconds * 1000)


# ===== браузеры =====

ENGINE_TITLES = {"chrome": "Google Chrome", "chromium": "Chromium", "firefox": "Firefox", "webkit": "WebKit (движок Safari)"}


class EngineUnavailable(Exception):
    pass


class Browsers:
    def __init__(self, world):
        self.world = world
        self.settings = world.settings
        self.log = world.log
        self.pw = sync_playwright().start()
        self.instances = {}
        self.used = {}
        self.broken = {}
        self.h264 = {}

    @property
    def channel_used(self):
        return ", ".join("%s → %s" % (k, v) for k, v in self.used.items()) or None

    def _chromium_args(self):
        args = ["--disable-dev-shm-usage", "--autoplay-policy=no-user-gesture-required", "--lang=ru-RU",
                "--no-first-run", "--no-default-browser-check"]
        origin = re.match(r"^(https?://[^/]+)", self.settings.base_url or "")
        if self.settings.treat_secure and origin and origin.group(1).startswith("http://"):
            args.append("--unsafely-treat-insecure-origin-as-secure=" + origin.group(1))
        if self.settings.target == "apk":
            args.append("--host-resolver-rules=MAP kovapp.test 127.0.0.1")
        return args

    def _launch(self, engine: str):
        s = self.settings
        common = dict(headless=s.headless, slow_mo=s.slow_mo)
        if engine == "firefox":
            prefs = {"media.autoplay.default": 0, "media.autoplay.blocking_policy": 0, "intl.accept_languages": "ru-RU,ru"}
            return self.pw.firefox.launch(firefox_user_prefs=prefs, **common), "firefox"
        if engine == "webkit":
            return self.pw.webkit.launch(**common), "webkit"
        if engine in ("chrome", "msedge"):
            try:
                return self.pw.chromium.launch(channel=engine, args=self._chromium_args(), **common), engine
            except Exception as e:
                self.log.warn("%s не запустился (%s) — беру встроенный Chromium" % (engine, str(e).splitlines()[0]))
        try:
            return self.pw.chromium.launch(channel="chromium", args=self._chromium_args(), **common), "chromium"
        except Exception:
            return self.pw.chromium.launch(args=self._chromium_args(), **common), "chromium-shell"

    def get(self, name: str, engine: str = ""):
        engine = (engine or self.settings.browsers[0]).lower()
        key = (engine, name)
        if engine in self.broken:
            raise EngineUnavailable(self.broken[engine])
        if key not in self.instances:
            try:
                browser, used = self._launch(engine)
            except Exception as e:
                self.broken[engine] = "браузер %s не запускается: %s" % (ENGINE_TITLES.get(engine, engine), str(e).splitlines()[0][:200])
                self.log.warn(self.broken[engine])
                raise EngineUnavailable(self.broken[engine])
            self.instances[key] = browser
            self.used[engine] = "%s %s" % (used, browser.version)
            self.log.info("браузер %s/%s запущен: %s %s" % (engine, name, used, browser.version))
        return self.instances[key]

    def context_options(self, device: str, theme: str, engine: str = "", variant: str = "") -> dict:
        s = self.settings
        engine = engine or s.browsers[0]
        if s.target == "apk":
            from .apk import webview_user_agent
            name = s.apk_devices.get(device) or s.apk_devices.get("mobile") or "Pixel 7"
            options = dict(self.pw.devices[name])
            options.pop("default_browser_type", None)
            if variant != "web":
                options["user_agent"] = webview_user_agent(options.get("user_agent", ""))
        elif device == "mobile":
            name = s.mobile_device_webkit if engine == "webkit" else s.mobile_device
            options = dict(self.pw.devices[name])
            options.pop("default_browser_type", None)
            if engine == "firefox":
                options.pop("is_mobile", None)
        else:
            options = {"viewport": {"width": s.desktop_size[0], "height": s.desktop_size[1]}}
        options.update(locale="ru-RU", timezone_id="Europe/Moscow",
                       color_scheme="dark" if theme == "dark" else "light",
                       reduced_motion="reduce", accept_downloads=True)
        if s.record_video:
            options["record_video_dir"] = str(self.world.log.dir / "video")
        return options

    def new_context(self, engine: str, browser: str, device: str, theme: str, variant: str = ""):
        target = self.get(browser, engine)
        options = self.context_options(device, theme, engine, variant)
        try:
            return target.new_context(**options)
        except Exception as e:
            if "has_touch" in options or "is_mobile" in options:
                self.log.warn("%s: эмуляция телефона урезана (%s)" % (engine, str(e).splitlines()[0][:120]))
                options.pop("has_touch", None)
                options.pop("is_mobile", None)
                return target.new_context(**options)
            raise

    def where(self, variant: str = ""):
        """(адрес страницы, адрес API для неё): сайт, приложение или приложение по HTTP."""
        s = self.settings
        if variant == "web" or s.target != "apk":
            return s.web_app_url or s.app_url, s.base_url
        apk = self.world.apk
        if variant == "http":
            return apk.http_app_url, apk.http_origin
        return apk.app_url, apk.origin

    def open_device(self, role, theme, route, t, wait=True):
        """Страница внутри настоящего приложения: другая роль — вход в ту же страницу и перезагрузка."""
        dev = self.world.device
        api = self.world.api(role)
        self.world.set_theme(role, theme)
        raw = dev.listened_page()
        auth = {"host": dev.origin(), "token": api.token, "userId": api.user_id}
        raw.evaluate("a => { try { localStorage.setItem('kov.auth', a.auth); localStorage.setItem('kov.theme', a.theme);"
                     " if (!sessionStorage.getItem('kov.autotest.offer')) localStorage.setItem('kov.notify.offer', String(Date.now())); } catch (e) {} }",
                     {"auth": json.dumps(auth), "theme": theme})
        page = KovPage(raw, raw.context, role, theme, "phone", "A", self.world, t, "chromium", self.settings.app_url)
        page.variant = "device"
        # страница уже открыта: переход на тот же адрес её не перезагрузил бы — вход новой ролью через reload
        if raw.url.startswith(self.settings.app_url):
            raw.evaluate("r => { location.hash = r; }", route)
            raw.reload()
        else:
            raw.goto(self.settings.app_url + route)
        page.opened = True
        if wait:
            page.wait_ready()
        return page

    def open(self, role, theme, device, browser, route, t, wait=True, engine="", variant=""):
        if self.settings.target == "device" and variant in ("", "device"):
            return self.open_device(role, theme, route, t, wait)
        engine = (engine or self.settings.browsers[0]).lower()
        api = self.world.api(role)
        self.world.set_theme(role, theme)
        try:
            context = self.new_context(engine, browser, device, theme, variant)
        except EngineUnavailable as e:
            t.skip(str(e))
        context.set_default_timeout(self.settings.timeout_ms)
        app_url, host = self.where(variant)
        auth = {"host": host, "token": api.token, "userId": api.user_id}
        context.add_init_script(UPLOAD_RECORDER)
        context.add_init_script(
            "try { localStorage.setItem('kov.auth', %s); localStorage.setItem('kov.theme', %s);"
            " if (!sessionStorage.getItem('kov.autotest.offer')) localStorage.setItem('kov.notify.offer', String(Date.now())); } catch (e) {}"
            % (json.dumps(json.dumps(auth)), json.dumps(theme)))
        page = KovPage(context.new_page(), context, role, theme, device, browser, self.world, t, engine, app_url)
        page.variant = variant or ("apk" if self.settings.target == "apk" else "web")
        page.goto(route, wait)
        return page

    def open_login(self, theme, device, browser, t, engine="", variant=""):
        engine = (engine or t.params.get("engine") or self.settings.browsers[0]).lower()
        try:
            context = self.new_context(engine, browser, device, theme, variant)
        except EngineUnavailable as e:
            t.skip(str(e))
        context.set_default_timeout(self.settings.timeout_ms)
        context.add_init_script("try { localStorage.removeItem('kov.auth'); localStorage.setItem('kov.theme', %s); } catch (e) {}"
                                % json.dumps(theme))
        app_url, _ = self.where(variant)
        page = KovPage(context.new_page(), context, "гость", theme, device, browser, self.world, t, engine, app_url)
        page.variant = variant or ("apk" if self.settings.target == "apk" else "web")
        page.raw.goto(app_url)
        page.opened = True
        page.raw.wait_for_selector(".login-screen .login-card input", timeout=self.settings.timeout_ms)
        t.pages.append(page)
        return page

    def release(self, page: KovPage) -> None:
        try:
            page.context.close()
        except Exception:
            pass

    def detect_h264(self, engine: str = "") -> bool:
        engine = (engine or self.settings.browsers[0]).lower()
        if engine not in self.h264:
            context = self.get("A", engine).new_context()
            try:
                page = context.new_page()
                page.set_content("<video></video>")
                verdict = page.evaluate("() => document.querySelector('video').canPlayType('video/mp4; codecs=\"avc1.42E01E, mp4a.40.2\"')")
                self.h264[engine] = verdict in ("probably", "maybe")
            finally:
                context.close()
            self.log.info("%s воспроизводит H.264: %s" % (engine, "да" if self.h264[engine] else "нет"))
        return self.h264[engine]

    def close(self) -> None:
        for browser in self.instances.values():
            try:
                browser.close()
            except Exception:
                pass
        try:
            self.pw.stop()
        except Exception:
            pass


def load_ui() -> dict:
    return load_yaml("ui.yaml")
