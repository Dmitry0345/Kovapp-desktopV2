#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""«APK в браузере»: интерфейс приложения на ПК без телефона.

Отдаёт web_app с той же надстройкой, что и APK (overlay/inject.json и
overlay/web-patches.json — побайтно как Java-класс Overlay), а остальные
адреса (API, WebSocket) пересылает на сервер: тестовый или настоящий.

    python mobile/tools/devserver.py --upstream http://127.0.0.1:8000 --bridge
    → откройте http://localhost:8790/app/

--bridge   подключить эмулятор моста Android (tools/devbridge/dev-bridge.js):
           надстройка приложения работает в обычном браузере;
--assets   режим «живых файлов» для отладочной сборки APK (devAssetsUrl):
           телефон берёт файлы интерфейса с ПК (adb reverse tcp:8790 tcp:8790).

localhost — «защищённый» адрес для браузера даже по http, поэтому ведёт себя
как рабочий HTTPS. Чтобы проверить запасные пути для HTTP, откройте тот же
сервер по имени, которое браузер не считает защищённым (см. README).
"""

import argparse
import http.client
import time
import json
import mimetypes
import os
import re
import secrets
import select
import socket
import ssl
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
MOBILE = os.path.dirname(HERE)
ROOT = os.path.dirname(MOBILE)
OVERLAY_DIR = os.path.join(MOBILE, "overlay")
BRIDGE_JS = os.path.join(HERE, "devbridge", "dev-bridge.js")
SIM_DIR = os.path.join(HERE, "simulator")
SIM_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
           "connect-src 'self' ws: wss:; frame-src 'self'; frame-ancestors 'none'")

mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("application/wasm", ".wasm")

HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
       "transfer-encoding", "upgrade", "content-length", "content-encoding"}


# ── надстройка: тот же алгоритм, что web/Overlay.java ────────────────────────
class Overlay:
    def __init__(self, inject_json: str, patches_json: str, patches_enabled: bool = True, extra_head=()):
        inj = json.loads(inject_json)
        self.head_after = inj.get("head_after", "")
        self.head_fallback = inj.get("head_fallback", "</head>")
        self.tail_before = inj.get("tail_before", "")
        self.tail_fallback = inj.get("tail_fallback", "</body>")
        self.head_scripts = list(extra_head) + [s for s in inj.get("head_scripts", []) if isinstance(s, str)]
        self.tail_scripts = [s for s in inj.get("tail_scripts", []) if isinstance(s, str)]
        self.patches = []
        if patches_json and patches_json.strip():
            for p in json.loads(patches_json).get("patches", []):
                if isinstance(p, dict) and p.get("file") is not None and p.get("find") and p.get("replace") is not None:
                    self.patches.append(p)
        self.enabled = patches_enabled
        self.warnings = []

    @staticmethod
    def _tags(scripts):
        return "".join('<script src="%s"></script>' % s for s in scripts)

    @staticmethod
    def _insert(html, anchor, after, fallback, what):
        i = html.find(anchor) if anchor else -1
        if i >= 0:
            at = i + len(anchor) if after else i
            return html[:at] + what + html[at:]
        f = html.rfind(fallback)
        if f >= 0:
            return html[:f] + what + html[f:]
        return html + what

    def inject_html(self, html: str) -> str:
        head, tail = self._tags(self.head_scripts), self._tags(self.tail_scripts)
        out = html
        if head:
            out = self._insert(out, self.head_after, True, self.head_fallback, head)
        if tail:
            out = self._insert(out, self.tail_before, False, self.tail_fallback, tail)
        return out

    def patch_text(self, path: str, text: str) -> str:
        if not self.enabled:
            return text
        out = text
        for p in self.patches:
            if p["file"] != path:
                continue
            first = out.find(p["find"])
            if first < 0:
                self.warnings.append("%s: не найден фрагмент %r" % (path, p["find"][:60]))
                continue
            if out.find(p["find"], first + len(p["find"])) >= 0:
                self.warnings.append("%s: фрагмент встречается несколько раз %r" % (path, p["find"][:60]))
                continue
            out = out[:first] + p["replace"] + out[first + len(p["find"]):]
        return out

    def touches(self, path: str) -> bool:
        if path.endswith(".html"):
            return True
        return self.enabled and any(p["file"] == path for p in self.patches)

    def transform(self, path: str, raw: bytes) -> bytes:
        text = raw.decode("utf-8")
        if path.endswith(".html"):
            text = self.inject_html(text)
        return self.patch_text(path, text).encode("utf-8")


def load_overlay(bridge=False, patches=True) -> Overlay:
    with open(os.path.join(OVERLAY_DIR, "inject.json"), encoding="utf-8") as f:
        inj = f.read()
    with open(os.path.join(OVERLAY_DIR, "web-patches.json"), encoding="utf-8") as f:
        pat = f.read()
    return Overlay(inj, pat, patches, extra_head=["native/dev-bridge.js"] if bridge else ())


def server_csp() -> str:
    # заголовки безопасности — как у настоящего сервера; сервер может лежать отдельно (папка веб-версии)
    candidates = [os.environ.get("KOVAPP_SERVER_ROOT") or "", os.path.join(ROOT, "server"),
                  os.path.join(os.path.dirname(ROOT), "web", "server")]
    path = next((os.path.join(c, "server_app", "core.py") for c in candidates
                 if c and os.path.isfile(os.path.join(c, "server_app", "core.py"))), None)
    if path is None:
        raise SystemExit("Не найден сервер Kovapp (server/server_app/core.py): положите веб-версию рядом "
                         "(папка web) или укажите KOVAPP_SERVER_ROOT")
    core = open(path, encoding="utf-8").read()
    start = core.index("DEFAULT_CSP = (")
    block = core[start:core.index("\n)\n", start)]
    csp = "".join(re.findall(r'f?"([^"]*)"', block))
    return (csp.replace("{KLIPY_MEDIA}", "https://static.klipy.com https://static1.klipy.com https://static2.klipy.com")
               .replace("{KLIPY_API}", "https://api.klipy.com"))


def normalize(rel: str):
    """Как WebRules.normalize в APK: без выхода за папку и без «:» в пути."""
    try:
        decoded = urllib.parse.unquote(rel, errors="strict")
    except UnicodeDecodeError:
        return None
    if "\\" in decoded or "\0" in decoded:
        return None
    parts = []
    for seg in decoded.split("/"):
        if seg in ("", "."):
            continue
        if seg == ".." or ":" in seg:
            return None
        parts.append(seg)
    path = "/".join(parts)
    if not path or rel.endswith("/"):
        path = "index.html" if not path else path + "/index.html"
    return path


def default_state():
    return {"platform": "android", "app": "dev", "build": 0, "flavor": "devserver", "sdk": 36, "permission": "default",
            "push": {"enabled": False, "registered": False, "mode": "", "serverSupported": True, "permission": "default",
                     "battery": False, "fcmPossible": False},
            "features": {"video": True, "hash": True, "image": True, "downloads": True}}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "KovappDev/1.0"

    def log_message(self, fmt, *args):
        if self.server.cfg.verbose:
            sys.stderr.write("[dev] " + (fmt % args) + "\n")

    def _send(self, status, body: bytes, ctype: str, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def app_headers(self, ctype):
        # те же заголовки, что выдаёт AssetServer в APK (WebRules.headers)
        h = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
             "Cross-Origin-Opener-Policy": "same-origin",
             "Permissions-Policy": "geolocation=(), microphone=(self), camera=(self)",
             "Cache-Control": "no-store"}
        if ctype.startswith(("text/html", "text/javascript")):
            h["Content-Security-Policy"] = self.server.cfg.csp
        if self.server.cfg.sim:
            # в симуляторе приложение живёт в рамке своей же страницы — и только её
            h["X-Frame-Options"] = "SAMEORIGIN"
        return h

    def do_GET(self):
        self.handle_any()

    def do_HEAD(self):
        self.handle_any()

    def do_POST(self):
        self.handle_any()

    def do_PUT(self):
        self.handle_any()

    def do_PATCH(self):
        self.handle_any()

    def do_DELETE(self):
        self.handle_any()

    def do_OPTIONS(self):
        self.handle_any()

    def handle_any(self):
        cfg = self.server.cfg
        parts = urllib.parse.urlsplit(self.path)
        try:
            if cfg.sim and parts.path.startswith("/__sim"):
                return self.serve_sim(parts.path)
            if parts.path == "/__devbridge/temp" and self.command == "POST":
                n = int(self.headers.get("Content-Length") or 0)
                token = secrets.token_hex(16)
                cfg.temp[token] = (self.rfile.read(n), self.headers.get("Content-Type") or "application/octet-stream")
                return self._send(200, json.dumps({"url": "__kovapp__/" + token}).encode(), "application/json")
            if parts.path.startswith(cfg.app_path) and self.command in ("GET", "HEAD") and "upgrade" not in self.headers.get("Connection", "").lower():
                if self.serve_app(parts.path[len(cfg.app_path):], urllib.parse.parse_qs(parts.query)):
                    return
            if parts.path == "/" and self.command == "GET":
                self.send_response(302)
                self.send_header("Location", cfg.app_path)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.proxy()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def serve_app(self, rel, query) -> bool:
        cfg = self.server.cfg
        if rel.startswith("__kovapp__/"):
            item = cfg.temp.get(rel.split("/", 1)[1])
            if item is None:
                self._send(404, b"404", "text/plain")
            else:
                self._send(200, item[0], item[1], {"Cache-Control": "no-store"})
            return True
        if rel == "native/kovapp-state.js":
            body = ("window.__kovNativeInit=" + json.dumps(default_state(), ensure_ascii=False) + ";\n").encode("utf-8")
            self._send(200, body, "text/javascript; charset=utf-8", self.app_headers("text/javascript"))
            return True
        path = normalize(rel)
        if path is None:
            self._send(404, b"404", "text/plain")
            return True
        if path == "native/dev-bridge.js" and cfg.bridge:
            full = BRIDGE_JS
        elif path.startswith("native/"):
            full = os.path.join(OVERLAY_DIR, path)
        else:
            full = os.path.join(cfg.web, path)
        if os.path.isdir(full):
            full = os.path.join(full, "index.html")
            path = path.rstrip("/") + "/index.html"
        if not os.path.isfile(full):
            return False              # как в APK: чего нет в файлах интерфейса — спросить у сервера
        with open(full, "rb") as f:
            body = f.read()
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        raw = "raw" in query           # режим «живых файлов»: надстройку применяет сам APK
        if not raw and cfg.overlay.touches(path) and (ctype.startswith("text/") or ctype.endswith("javascript")):
            body = cfg.overlay.transform(path, body)
        if ctype.startswith("text/") or ctype in ("application/json", "image/svg+xml", "application/manifest+json"):
            ctype += "; charset=utf-8"
        self._send(200, body, ctype, self.app_headers(ctype))
        return True

    # ── симулятор телефона ──
    def serve_sim(self, path):
        cfg = self.server.cfg
        if path == "/__sim/info":
            body = json.dumps({"upstream": cfg.upstream, "mock": cfg.mock, "app_path": cfg.app_path}, ensure_ascii=False)
            return self._send(200, body.encode("utf-8"), "application/json", {"Cache-Control": "no-store"})
        if path == "/__sim/net" and self.command == "POST":
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(n) or b"{}")
            cfg.offline = not data.get("online", True)
            cfg.delay_ms = max(0, min(10000, int(data.get("delay_ms", 0))))
            if cfg.offline:
                cfg.drop_tunnels()           # открытые сокеты рвутся, как при потере сети
            return self._send(200, b'{"ok":true}', "application/json")
        name = path[len("/__sim"):].lstrip("/") or "index.html"
        full = os.path.join(SIM_DIR, name)
        if name not in ("index.html", "sim.js", "sim.css", "launcher.svg") or not os.path.isfile(full):
            if path == "/__sim":
                self.send_response(302)
                self.send_header("Location", "/__sim/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            return self._send(404, b"404", "text/plain")
        ctype = mimetypes.guess_type(full)[0] or "text/plain"
        with open(full, "rb") as f:
            body = f.read()
        self._send(200, body, ctype + "; charset=utf-8", {"Content-Security-Policy": SIM_CSP, "Cache-Control": "no-store",
                                                         "X-Content-Type-Options": "nosniff"})

    # ── пересылка на сервер ──
    def proxy(self):
        cfg = self.server.cfg
        if cfg.offline:
            self.close_connection = True     # как без сети: соединение рвётся без ответа
            return
        if cfg.delay_ms:
            time.sleep(cfg.delay_ms / 1000.0)
        if "upgrade" in self.headers.get("Connection", "").lower():
            return self.tunnel()
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP and k.lower() not in ("host", "accept-encoding")}
        conn = cfg.connect()
        try:
            conn.request(self.command, cfg.upstream_prefix + self.path, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
        except Exception as e:
            return self._send(502, ("Сервер недоступен: %s" % e).encode("utf-8"), "text/plain; charset=utf-8")
        finally:
            conn.close()
        self.send_response(resp.status, resp.reason)
        for k, v in resp.getheaders():
            if k.lower() not in HOP:
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def tunnel(self):
        """WebSocket: запрос уходит на сервер как есть, дальше байты пересылаются в обе стороны."""
        cfg = self.server.cfg
        up = socket.create_connection((cfg.host, cfg.port), timeout=10)
        if cfg.tls:
            up = cfg.ssl_context().wrap_socket(up, server_hostname=cfg.host)
        lines = ["%s %s HTTP/1.1" % (self.command, cfg.upstream_prefix + self.path)]
        for k, v in self.headers.items():
            lines.append("%s: %s" % (k, cfg.netloc if k.lower() == "host" else v))
        up.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
        up.settimeout(None)
        client = self.connection
        self.close_connection = True
        socks = [client, up]
        with cfg.lock:
            cfg.tunnels.add((client, up))
        try:
            while True:
                ready, _, _ = select.select(socks, [], [], 300)
                if not ready:
                    break
                for s in ready:
                    data = s.recv(65536)
                    if not data:
                        return
                    (up if s is client else client).sendall(data)
        except (OSError, ValueError):
            pass
        finally:
            with cfg.lock:
                cfg.tunnels.discard((client, up))
            up.close()


class Config:
    def __init__(self, a):
        u = urllib.parse.urlsplit(a.upstream)
        self.tls = u.scheme == "https"
        self.host = u.hostname
        self.port = u.port or (443 if self.tls else 80)
        self.netloc = u.netloc
        self.upstream_prefix = u.path.rstrip("/")
        self.insecure = a.insecure_upstream
        self.web = os.path.abspath(a.web)
        self.app_path = a.app_path
        self.sim = a.sim
        self.bridge = a.bridge or a.sim
        self.mock = a.upstream_is_mock
        self.upstream = a.upstream
        self.overlay = load_overlay(bridge=self.bridge, patches=not a.no_patches)
        self.csp = server_csp()
        if self.sim:
            self.csp = self.csp.replace("frame-ancestors 'none'", "frame-ancestors 'self'")
        self.offline = False
        self.delay_ms = 0
        self.tunnels = set()
        self.lock = threading.Lock()
        self.temp = {}
        self.verbose = a.verbose

    def drop_tunnels(self):
        with self.lock:
            pairs = list(self.tunnels)
        for client, up in pairs:
            for sock in (client, up):
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def ssl_context(self):
        ctx = ssl.create_default_context()
        if self.insecure:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def connect(self):
        if self.tls:
            return http.client.HTTPSConnection(self.host, self.port, timeout=60, context=self.ssl_context())
        return http.client.HTTPConnection(self.host, self.port, timeout=60)


def make_server(args_list):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 — открыть для телефона в той же сети")
    ap.add_argument("--upstream", default="http://127.0.0.1:8000", help="сервер API: тестовый или настоящий https://…")
    ap.add_argument("--insecure-upstream", action="store_true", help="не проверять сертификат сервера (самоподписанный)")
    ap.add_argument("--web", default=os.path.join(ROOT, "web_app"))
    ap.add_argument("--app-path", default="/app/")
    ap.add_argument("--bridge", action="store_true", help="эмулятор моста Android для обычного браузера")
    ap.add_argument("--assets", action="store_true", help="режим живых файлов для отладочного APK (то же, что без --bridge)")
    ap.add_argument("--no-patches", action="store_true", help="без правок текстов (как webPatches=false)")
    ap.add_argument("--sim", action="store_true", help="симулятор телефона: http://127.0.0.1:ПОРТ/__sim/")
    ap.add_argument("--upstream-is-mock", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(args_list)
    if a.assets:
        a.bridge = False
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    srv.cfg = Config(a)
    return srv, a


def main():
    srv, a = make_server(sys.argv[1:])
    mode = "симулятор телефона" if a.sim else ("эмулятор моста" if a.bridge else ("живые файлы для APK" if a.assets else "без моста"))
    where = "/__sim/" if a.sim else a.app_path
    print("Kovapp на ПК: http://127.0.0.1:%d%s  (%s; сервер: %s)" % (srv.server_address[1], where, mode, a.upstream))
    for w in srv.cfg.overlay.warnings:
        print("  внимание:", w)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
