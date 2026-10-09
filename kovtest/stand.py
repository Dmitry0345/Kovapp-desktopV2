"""Стенд для ручной проверки: сервер размером с рабочий, в фоне работают N человек, а вы работаете в браузере сами."""
import collections
import json
import random
import statistics
import threading
import time
import uuid
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from kovtest.api import Api
from kovtest.config import env, env_int
from kovtest.media import device_photo, device_video, uniquify
from kovtest.ws import Socket

MODES = {
    "тихо": {"chat": (20.0, 40.0), "lists": (20.0, 40.0), "uploads_per_min": 6},
    "обычно": {"chat": (5.0, 10.0), "lists": (6.0, 12.0), "uploads_per_min": 30},
    "пик": {"chat": (1.0, 2.5), "lists": (2.0, 4.0), "uploads_per_min": 300},
}


class QuietLog:
    def http(self, *args, **kwargs):
        pass

    def debug(self, *args, **kwargs):
        pass

    def info(self, text):
        print(text, flush=True)

    def warn(self, text):
        print("! " + text, flush=True)

    def fail(self, text):
        print("!! " + text, flush=True)


def pct(values, share):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(share * (len(ordered) - 1))))]


class Stand:
    def __init__(self, base_url, users, password):
        self.base = base_url
        self.log = QuietLog()
        self.users_wanted = users
        self.password = password
        self.mode = "обычно"
        self.samples = collections.deque(maxlen=20000)
        self.server = {}
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.users = []
        self.sockets = {}
        self.sent = {}
        self.task = None
        self.stage = None
        self.media = None
        self.bursting = False

    def note(self, kind, value):
        with self.lock:
            self.samples.append((time.time(), kind, value))

    def start(self):
        self.log.info("Стенд: вход %d человек…" % self.users_wanted)
        for n in range(1, self.users_wanted + 1):
            api = Api(self.base, self.log, who="at_load%02d" % n)
            api.login("at_load%02d" % n, self.password)
            self.users.append(api)
        director = Api(self.base, self.log, who="at_director")
        director.login("at_director", self.password)
        payload = {"name": "Стенд: файлы %s" % time.strftime("%H:%M"), "description": "Нагрузка стенда",
                   "deadline": (date.today() + timedelta(days=30)).isoformat(), "customer_organization": None,
                   "responsible_persons_ids": [], "assigned_employees_ids": [],
                   "stages": [{"department_name": "Монтажный отдел", "text_description": ""}]}
        self.task = director.call("POST", "tasks", json=payload, expect=201)
        self.stage = self.task["stage_details"][0]
        director.call("PUT", "stages/%d/participants" % self.stage["id"], json=[u.user_id for u in self.users])
        for start in range(0, len(self.users), 5):
            members = self.users[start:start + 5]
            chat = director.call("POST", "chat/group", json={"name": "Стенд %d" % (start // 5 + 1)})
            director.call("POST", "chat/group/%d/participants" % chat["id"], json={"user_ids": [u.user_id for u in members]})
            for api in members:
                self.sockets[api.who] = (chat["id"], Socket(self.base, api.token, self.log, who=api.who, quiet=True))
        photo, photo_thumb, photo_size = device_photo()
        video, video_thumb, video_size, video_len = device_video()
        self.media = {"photo": (photo, photo_thumb, photo_size), "video": (video, video_thumb, video_size, video_len)}
        self.admin = Api(self.base, self.log, who="admin")
        self.admin.login(self.admin_login, self.admin_password)
        for api in self.users:
            threading.Thread(target=self.talker, args=(api,), daemon=True).start()
            threading.Thread(target=self.browser, args=(api,), daemon=True).start()
        threading.Thread(target=self.receiver, daemon=True).start()
        threading.Thread(target=self.uploader, daemon=True).start()
        threading.Thread(target=self.monitor, daemon=True).start()

    # ===== участники =====
    def talker(self, api):
        chat_id, sock = self.sockets[api.who]
        while not self.stop.is_set():
            low, high = MODES[self.mode]["chat"]
            time.sleep(random.uniform(low, high))
            mark = "%s-%s" % (api.who, uuid.uuid4().hex[:8])
            with self.lock:
                self.sent[mark] = time.time()
            try:
                sock.send({"type": "message", "chat_id": chat_id, "text": "стенд " + mark, "client_id": "c" + mark[-8:]})
            except Exception:
                pass

    def receiver(self):
        seen = {}
        while not self.stop.is_set():
            time.sleep(1)
            for login, (chat_id, sock) in self.sockets.items():
                with sock._lock:
                    events = list(sock.events)[seen.get(login, 0):]
                    seen[login] = len(sock.events)
                for stamp, event in events:
                    text = event.get("text") if isinstance(event, dict) and event.get("type") == "message" else ""
                    if text and text.startswith("стенд ") and not text[len("стенд "):].startswith(login + "-"):
                        at = self.sent.get(text[len("стенд "):])
                        if at:
                            self.note("чат", stamp - at)

    def browser(self, api):
        paths = ["tasks/light?is_archived=false", "projects", "employees/%d/notifications" % api.user_id, "chat/list"]
        tags = {}
        while not self.stop.is_set():
            low, high = MODES[self.mode]["lists"]
            time.sleep(random.uniform(low, high))
            path = random.choice(paths)
            began = time.time()
            try:
                response = api.get(path, headers={"If-None-Match": tags[path]} if path in tags else {}, timeout=60)
                if response.headers.get("ETag"):
                    tags[path] = response.headers["ETag"]
                self.note("списки", time.time() - began)
            except Exception:
                self.note("ошибки", 1)

    def upload_one(self, api, kind):
        if kind == "photo":
            data, thumb, size = self.media["photo"]
            name, ctype, meta = "%s-stand.webp" % api.who, "image/webp", {"prep": "image", "width": size[0], "height": size[1]}
        else:
            data, thumb, size, length = self.media["video"]
            name, ctype, meta = "%s-stand.mp4" % api.who, "video/mp4", {"prep": "device", "width": size[0], "height": size[1],
                                                                          "duration": length}
        data = uniquify(name, data)
        item = dict({"src": "file", "file": 0, "name": name, "size": len(data), "thumb": 0}, **meta)
        began = time.time()
        try:
            response = api.post("upload", data={"task_id": str(self.task["id"]), "stage_detail_id": str(self.stage["id"]),
                                                "meta": json.dumps([item])},
                                files=[("files", (name, data, ctype)), ("thumbs", ("thumb.jpg", thumb, "image/jpeg"))], timeout=600)
            self.note("фото" if kind == "photo" else "видео", time.time() - began) if response.status_code == 200 \
                else self.note("ошибки", 1)
        except Exception:
            self.note("ошибки", 1)

    def uploader(self):
        while not self.stop.is_set():
            per_min = MODES[self.mode]["uploads_per_min"]
            time.sleep(60.0 / per_min)
            api = random.choice(self.users)
            threading.Thread(target=self.upload_one, args=(api, random.choice(("photo", "video"))), daemon=True).start()

    def burst(self):
        if self.bursting:
            return
        self.bursting = True

        def run():
            threads = [threading.Thread(target=self.upload_one, args=(api, kind), daemon=True)
                       for api in self.users for kind in ("photo", "video")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.bursting = False
        threading.Thread(target=run, daemon=True).start()

    def monitor(self):
        while not self.stop.is_set():
            try:
                self.server = self.admin.call("GET", "health/load")
            except Exception:
                pass
            time.sleep(2)

    # ===== сводка для страницы =====
    def state(self):
        now = time.time()
        with self.lock:
            recent = [(k, v) for t, k, v in self.samples if now - t <= 60]
        out = {"mode": self.mode, "modes": list(MODES), "users": len(self.users), "bursting": self.bursting, "metrics": {}}
        for kind in ("чат", "списки", "фото", "видео"):
            values = [v for k, v in recent if k == kind]
            out["metrics"][kind] = {"count": len(values), "median": statistics.median(values) if values else None,
                                    "p95": pct(values, 0.95)}
        out["metrics"]["ошибки"] = {"count": sum(1 for k, _ in recent if k == "ошибки")}
        server = self.server or {}
        heavy = server.get("heavy") or {}
        out["server"] = {"cpu": server.get("cpu_busy"), "ram_total": server.get("ram_total_mb"),
                         "ram_free": server.get("ram_available_mb"), "lag_ms": server.get("loop_lag_ms"),
                         "uploads_active": heavy.get("active"), "uploads_waiting": heavy.get("waiting"),
                         "light_p95_ms": heavy.get("light_p95_ms"), "plan": server.get("plan")}
        return out


PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Стенд Kovapp — нагрузка</title><style>
:root{--bg:#f4f5f7;--card:#fff;--ink:#1d2433;--muted:#6b7385;--accent:#2f6fed;--good:#1f9d57;--warn:#d98a00;--bad:#d64545}
@media(prefers-color-scheme:dark){:root{--bg:#12151c;--card:#1b2029;--ink:#e8ebf2;--muted:#8d95a8}}
body{margin:0;font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--ink)}
main{max-width:1100px;margin:0 auto;padding:24px}h1{font-size:22px;margin:0 0 4px}p{margin:0 0 16px;color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px}
.card{background:var(--card);border-radius:14px;padding:16px 18px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.label{color:var(--muted);font-size:13px}.big{font-size:30px;font-weight:650;margin-top:4px}.small{color:var(--muted);font-size:13px}
.good{color:var(--good)}.warn{color:var(--warn)}.bad{color:var(--bad)}
.bar{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0}button{border:0;border-radius:10px;padding:10px 16px;font:inherit;cursor:pointer;
background:var(--card);color:var(--ink);box-shadow:0 1px 3px rgba(0,0,0,.1)}button.on{background:var(--accent);color:#fff}
a{color:var(--accent)}code{background:var(--card);padding:2px 6px;border-radius:6px}
</style></head><body><main>
<h1>Стенд Kovapp: в фоне работают <span id="users">…</span> человек</h1>
<p>Откройте <a href="http://localhost:18000/app/" target="_blank">http://localhost:18000/app/</a> и работайте сами —
вход: <code>at_director</code> / пароль из <code>.env</code> (FULL_ACCOUNT_PASSWORD). Цифры ниже — за последнюю минуту.</p>
<div class="bar" id="modes"></div>
<div class="grid" id="cards"></div>
<p class="small" id="plan" style="margin-top:14px"></p>
</main><script>
const fmt=v=>v==null?'—':(v<1?Math.round(v*1000)+' мс':v.toFixed(1)+' с');
const tone=(v,good,warn)=>v==null?'':(v<=good?'good':(v<=warn?'warn':'bad'));
async function send(url){await fetch(url,{method:'POST'});tick()}
async function tick(){try{const s=await (await fetch('/api/state')).json();
document.getElementById('users').textContent=s.users;
document.getElementById('modes').innerHTML=s.modes.map(m=>`<button class="${m===s.mode?'on':''}" onclick="send('/api/mode?name=${encodeURIComponent(m)}')">${m}</button>`).join('')
+`<button onclick="send('/api/burst')">${s.bursting?'всплеск идёт…':'всплеск: все отправляют по 2 файла'}</button>`;
const m=s.metrics,sv=s.server;const card=(t,v,sub,cls)=>`<div class="card"><div class="label">${t}</div><div class="big ${cls||''}">${v}</div><div class="small">${sub}</div></div>`;
document.getElementById('cards').innerHTML=[
card('Сообщение в чате (95%)',fmt(m['чат'].p95),'медиана '+fmt(m['чат'].median)+', сообщений '+m['чат'].count,tone(m['чат'].p95,1,3)),
card('Открытие списка (95%)',fmt(m['списки'].p95),'медиана '+fmt(m['списки'].median)+', запросов '+m['списки'].count,tone(m['списки'].p95,1,2.5)),
card('Фото с телефона (95%)',fmt(m['фото'].p95),'медиана '+fmt(m['фото'].median)+', файлов '+m['фото'].count,tone(m['фото'].p95,2,5)),
card('Видео с телефона (95%)',fmt(m['видео'].p95),'медиана '+fmt(m['видео'].median)+', файлов '+m['видео'].count,tone(m['видео'].p95,5,20)),
card('Процессор сервера',sv.cpu==null?'—':Math.round(sv.cpu*100)+'%','задержка ответа '+(sv.lag_ms==null?'—':Math.round(sv.lag_ms)+' мс'),tone(sv.cpu,.7,.9)),
card('Память сервера',sv.ram_free==null?'—':Math.round(sv.ram_total-sv.ram_free)+' МБ','из '+(sv.ram_total||'—')+' МБ'),
card('Загрузки на сервере',(sv.uploads_active??'—')+' / '+(sv.uploads_waiting??'—'),'идут / ждут очереди'),
card('Ошибки',m['ошибки'].count,'за минуту',m['ошибки'].count?'bad':'good')].join('');
const p=sv.plan||{};document.getElementById('plan').textContent=p.cores?`Сервер видит: ядер ${p.cores}, памяти ${p.ram_mb} МБ; потоков ${p.threads}, загрузок одновременно до ${p.heavy}, процессов чтения ${p.readers}, пул базы ${p.db_pool}+${p.db_overflow}`:'';
}catch(e){}}
tick();setInterval(tick,2000);
</script></body></html>"""


def serve(stand, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, body, kind):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.startswith("/api/state"):
                return self.reply(200, json.dumps(stand.state(), ensure_ascii=False), "application/json; charset=utf-8")
            return self.reply(200, PAGE, "text/html; charset=utf-8")

        def do_POST(self):
            from urllib.parse import parse_qs, unquote, urlparse
            url = urlparse(self.path)
            if url.path == "/api/mode":
                name = unquote((parse_qs(url.query).get("name") or [""])[0])
                if name in MODES:
                    stand.mode = name
            elif url.path == "/api/burst":
                stand.burst()
            return self.reply(200, "{}", "application/json")

    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


def main(settings) -> int:
    users = env_int("LOAD_USERS", 50)
    password = env("FULL_ACCOUNT_PASSWORD", "Autotest-2026!")
    stand = Stand(settings.base_url, users, password)
    stand.admin_login, stand.admin_password = settings.admin_login, settings.admin_password
    stand.start()
    port = env_int("STAND_PORT", 8090)
    print("", flush=True)
    print("Стенд готов: в фоне работают %d человек (режим «обычно»)." % users, flush=True)
    print("  Продукт:        http://localhost:18000/app/   (вход at_director / пароль из .env)", flush=True)
    print("  Панель стенда:  http://localhost:18090/        (режимы, всплеск файлов, цифры за минуту)", flush=True)
    print("  Остановить: Ctrl+C", flush=True)
    try:
        serve(stand, port)
    except KeyboardInterrupt:
        stand.stop.set()
    return 0
