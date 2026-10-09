"""Связь телефона с сервером и служебные команды для тестов (порт 4799).

localhost:8000 в телефоне → (adb reverse) → этот контейнер :8000 → (socat) → server:8000.
POST /link/on, /link/off — тест «сервер пропал»; POST /reset — удалить приложение
перед прогоном (установка начисто, как у нового сотрудника); GET /status.
"""
import http.server
import json
import subprocess
import sys

SERIAL = sys.argv[1]
PKG = "ru.kovcheg.kovapp"
proxy = None


def adb(*args):
    r = subprocess.run(["adb", "-s", SERIAL] + list(args), capture_output=True, text=True, timeout=180)
    return (r.stdout + r.stderr).strip()


def link_on():
    global proxy
    if proxy is None or proxy.poll() is not None:
        proxy = subprocess.Popen(["socat", "TCP-LISTEN:8000,fork,reuseaddr", "TCP:server:8000"])
    return adb("reverse", "tcp:8000", "tcp:8000")


def ensure_online(timeout: float = 90) -> str:
    """Связь с телефоном перед прогоном: если adb потерял его (offline), переподключаемся."""
    import time
    deadline = time.time() + timeout
    state = ""
    while time.time() < deadline:
        state = adb("get-state")
        if state == "device":
            return state
        subprocess.run(["adb", "disconnect", SERIAL], capture_output=True, timeout=30)
        subprocess.run(["adb", "connect", SERIAL], capture_output=True, timeout=30)
        time.sleep(3)
    return state


def prepare():
    """Тестовый эмулятор: установки через adb без проверки Google Play Защитой, экран не гаснет."""
    out = []
    for args in (("shell", "settings", "put", "global", "verifier_verify_adb_installs", "0"),
                 ("shell", "settings", "put", "global", "package_verifier_enable", "0"),
                 ("shell", "svc", "power", "stayon", "true")):
        out.append(adb(*args))
    return " ".join(o for o in out if o)


cdp_proxy = None


def webview(timeout: float = 30) -> dict:
    """Страница приложения для тестов: отладочный сокет WebView → порт 9222 → 0.0.0.0:9223 (сеть Docker)."""
    import time
    global cdp_proxy
    deadline = time.time() + timeout
    while time.time() < deadline:
        pids = adb("shell", "pidof", PKG).split()
        if pids:
            name = "webview_devtools_remote_%s" % pids[0]
            if name in adb("shell", "cat", "/proc/net/unix"):
                adb("forward", "--remove", "tcp:9222")
                out = adb("forward", "tcp:9222", "localabstract:" + name)
                if cdp_proxy is None or cdp_proxy.poll() is not None:
                    cdp_proxy = subprocess.Popen(["socat", "TCP-LISTEN:9223,fork,reuseaddr", "TCP:127.0.0.1:9222"])
                return {"ok": True, "pid": pids[0], "socket": name, "adb": out}
        time.sleep(1)
    running = bool(adb("shell", "pidof", PKG).strip())
    return {"ok": False, "error": ("у приложения нет отладочного сокета WebView — нужна отладочная сборка" if running
                                   else "приложение не запущено")}


def kill_relays(port: int) -> None:
    """Все процессы socat на этом порту — и главный, и дочерние: через дочерние живут открытые
    соединения приложения (keep-alive), и без этого «пропавший» сервер продолжал бы отвечать."""
    import os
    import signal
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            cmd = open("/proc/%s/cmdline" % pid, "rb").read().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if cmd.startswith("socat") and ("TCP-LISTEN:%d," % port) in cmd:
            try:
                os.kill(int(pid), signal.SIGKILL)
            except OSError:
                pass


def link_off():
    global proxy
    out = adb("reverse", "--remove", "tcp:8000")
    kill_relays(8000)
    proxy = None
    return out


class Handler(http.server.BaseHTTPRequestHandler):
    def reply(self, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.reply({"serial": SERIAL, "link": proxy is not None and proxy.poll() is None})

    def do_POST(self):
        if self.path == "/link/on":
            self.reply({"ok": True, "adb": link_on()})
        elif self.path == "/link/off":
            self.reply({"ok": True, "adb": link_off()})
        elif self.path == "/webview":
            self.reply(webview())
        elif self.path == "/reset":
            state = ensure_online()
            if state != "device":
                self.reply({"ok": False, "state": state})
                return
            self.reply({"ok": True, "state": state, "adb": adb("uninstall", PKG), "prepare": prepare()})
        else:
            self.send_error(404)

    def log_message(self, *args):
        pass


link_on()
http.server.ThreadingHTTPServer(("0.0.0.0", 4799), Handler).serve_forever()
