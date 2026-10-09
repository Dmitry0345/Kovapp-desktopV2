import importlib.util
import json
import re
import sys
import threading
import time
from pathlib import Path

import requests

from .config import env, env_bool


# ===== строка браузера как у приложения на телефоне =====

def webview_user_agent(chrome_ua: str, version: str = "") -> str:
    """Строка Chrome для Android → строка Android System WebView с меткой Kovapp."""
    ua = chrome_ua or "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36"
    ua = re.sub(r"\(Linux; ([^)]*)\)", lambda m: "(Linux; %s; wv)" % m.group(1) if "; wv" not in m.group(1) else m.group(0), ua, count=1)
    if "Version/4.0" not in ua:
        ua = ua.replace("(KHTML, like Gecko) ", "(KHTML, like Gecko) Version/4.0 ", 1)
    return ua + " KovappAndroid/%s (autotest)" % (version or "autotest")


# ===== «телефон»: интерфейс APK на ПК =====

class ApkEnv:
    """Интерфейс APK на ПК: web_app с той же надстройкой, что в APK (проект:
    mobile/tools/devserver.py), и эмулятор моста вместо Android-части. Запросы
    к API и WebSocket идут на тот же сервер, что и у сайта."""

    def __init__(self, world):
        self.world = world
        self.settings = world.settings
        self.log = world.log
        self.root = Path(self.settings.project_dir)
        tools = self.root / "mobile" / "tools"
        spec = importlib.util.spec_from_file_location("kovapp_devserver", tools / "devserver.py")
        self.devserver = importlib.util.module_from_spec(spec)
        sys.modules["kovapp_devserver"] = self.devserver
        spec.loader.exec_module(self.devserver)
        self.version = self._project_version()
        self._servers = []
        self.server, self.port = self._start(["--bridge"])
        self.origin = "http://127.0.0.1:%d" % self.port
        self.app_path = "/" + env("APP_PATH", "/app/").strip("/") + "/"
        self.app_url = self.origin + self.app_path
        self.http_origin = "http://kovapp.test:%d" % self.port        # имя без HTTPS: запасные пути
        self.http_app_url = self.http_origin + self.app_path
        self._sim = None

    def _start(self, extra):
        args = ["--port", "0", "--host", "127.0.0.1", "--upstream", self.settings.base_url,
                "--web", str(self.root / "web_app"), "--app-path", "/" + env("APP_PATH", "/app/").strip("/") + "/"] + extra
        if env_bool("APK_INSECURE_UPSTREAM", False):
            args.append("--insecure-upstream")
        server, _ = self.devserver.make_server(args)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self._servers.append(server)
        return server, server.server_address[1]

    def _project_version(self) -> str:
        path = self.root / "mobile" / "kovapp.properties.example"
        try:
            text = path.read_text(encoding="utf-8")
            m = re.search(r"^versionName=(.+)$", text, re.M)
            return m.group(1).strip() if m else "?"
        except OSError:
            return "?"

    # ===== симулятор телефона (mobile/tools/simulator) =====

    @property
    def sim_url(self) -> str:
        if self._sim is None:
            self._sim = self._start(["--sim"])
        return "http://127.0.0.1:%d/__sim/" % self._sim[1]

    # ===== сеть телефона =====

    def offline(self, off: bool = True) -> None:
        """Как пропавший интернет: сервер недоступен, открытые соединения рвутся; интерфейс APK остаётся."""
        for server in self._servers:
            server.cfg.offline = bool(off)
            if off:
                server.cfg.drop_tunnels()
        self.log.info("сеть телефона: %s" % ("нет" if off else "есть"))

    @staticmethod
    def forget_offline_noise(page) -> None:
        """Сообщения браузера об оборванных соединениях во время имитации «нет сети»."""
        page.forget_network_noise()
        noise = ("ERR_EMPTY_RESPONSE",)
        page.console = [m for m in page.console if not any(n in m for n in noise)]
        page.http_errors = [m for m in page.http_errors if not any(n in m for n in noise)]

    @staticmethod
    def forget_http_notes(page) -> None:
        """Предупреждение Chromium для любой страницы без HTTPS: заголовок COOP на ней не действует."""
        page.console = [m for m in page.console if "Cross-Origin-Opener-Policy header has been ignored" not in m]

    # ===== надстройка =====

    def overlay(self) -> dict:
        return json.loads((self.root / "mobile" / "overlay" / "inject.json").read_text(encoding="utf-8"))

    def patches(self) -> list:
        return json.loads((self.root / "mobile" / "overlay" / "web-patches.json").read_text(encoding="utf-8"))["patches"]

    def served(self, rel: str) -> str:
        """Файл интерфейса так, как его получает WebView в APK."""
        return requests.get(self.app_url + rel.lstrip("/"), timeout=30).text

    # ===== эмулятор моста в странице =====

    @staticmethod
    def state(page) -> dict:
        return page.raw.evaluate("() => window.__kovDev ? JSON.parse(JSON.stringify(__kovDev.state)) : null")

    @staticmethod
    def calls(page, cmd: str) -> list:
        return page.raw.evaluate("c => window.__kovDev ? __kovDev.calls(c).map(m => m.args) : []", cmd)

    @staticmethod
    def commands(page) -> list:
        return page.raw.evaluate("() => window.__kovDev ? __kovDev.log.map(m => m.cmd) : []")

    @staticmethod
    def emit(page, event: str, data) -> None:
        page.raw.evaluate("([e, d]) => __kovDev.emit(e, d)", [event, data])

    # ===== «Загрузки» телефона вместо загрузок браузера =====

    def download_via(self, page, trigger, timeout: float = 60.0):
        count = "() => ({ blobs: Object.values(__kovDev.state.blobs).filter(b => b.done).length, urls: __kovDev.state.downloads.length })"
        before = page.raw.evaluate(count)
        trigger()
        deadline = time.time() + timeout
        while time.time() < deadline:
            now = page.raw.evaluate(count)
            if now["urls"] > before["urls"]:
                item = page.raw.evaluate("() => __kovDev.state.downloads.slice(-1)[0]")
                size = len(requests.get(item["url"], timeout=max(30, timeout)).content)
                self.log.info("приложение отдало файл системному загрузчику: %s (%d байт)" % (item.get("name"), size))
                return item.get("name") or "", size
            if now["blobs"] > before["blobs"]:
                item = page.raw.evaluate("""() => { const done = Object.values(__kovDev.state.blobs).filter(b => b.done);
                    const b = done[done.length - 1];
                    return { name: b.name, size: b.parts.reduce((n, p) => n + atob(p).length, 0) }; }""")
                self.log.info("приложение сохранило файл в «Загрузки»: %s (%d байт)" % (item["name"], item["size"]))
                return item["name"], item["size"]
            page.raw.wait_for_timeout(200)
        raise AssertionError("приложение не сохранило файл в «Загрузки» за %d с" % timeout)

    def close(self) -> None:
        for server in self._servers:
            try:
                server.shutdown()
                server.server_close()
            except Exception:
                pass
