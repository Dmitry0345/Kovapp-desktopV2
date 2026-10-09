# -*- coding: utf-8 -*-
"""Обращения к серверу — то же, что js/kov-api.js веб-версии.

Токен уходит в заголовке Authorization. Для ссылок на файлы (плеер,
скачивание по адресу) берётся отдельный короткий файловый токен
(POST files/access-token): он живёт минуты и не открывает остальной API.
Ошибки сервера приходят исключением ApiError с понятным текстом из detail.
"""

import json
import threading
import time
import urllib.parse

import requests

DEFAULT_TIMEOUT = (10, 60)


class ApiError(Exception):
    """Ответ сервера с ошибкой: status и текст detail для человека."""

    def __init__(self, status, message, data=None):
        super().__init__(message)
        self.status = status
        self.data = data
        self.detail = message

    def __str__(self):
        return self.detail or ("HTTP %s" % self.status)


def normalize_host(host):
    """«83.171.90.230:54545» → «http://83.171.90.230:54545», без слэша в конце."""
    host = (host or "").strip()
    if not host:
        return ""
    if "://" not in host:
        host = "http://" + host
    return host.rstrip("/")


def human_error(exc, fallback="Не удалось выполнить действие. Повторите попытку позже."):
    """Текст ошибки для окна: detail сервера или описание сбоя связи."""
    if isinstance(exc, ApiError):
        if exc.status == 401:
            return "Сессия истекла. Войдите в приложение заново."
        if exc.detail and not exc.detail.startswith("HTTP "):
            return exc.detail
        return {403: "Недостаточно прав для этого действия.",
                404: "Запись не найдена — возможно, её уже удалили.",
                409: "Действие сейчас недоступно: данные изменились.",
                413: "Файл слишком большой для сервера.",
                429: "Сервер занят. Повторите через несколько секунд."}.get(exc.status, fallback)
    if isinstance(exc, requests.exceptions.Timeout):
        return "Сервер не ответил вовремя. Повторите попытку позже."
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "Нет связи с сервером. Проверьте подключение к сети."
    return fallback


class Client:
    def __init__(self):
        self.host = ""
        self.token = ""
        self.user_id = None
        self.file_token = None
        self.file_token_until = 0.0
        self._local = threading.local()
        self._lock = threading.Lock()
        self.on_unauthorized = None        # вызывается при 401

    # ── сеанс ────────────────────────────────────────────────────────────────

    def set_auth(self, host, token, user_id):
        self.host = normalize_host(host)
        self.token = token or ""
        self.user_id = None if user_id is None else int(user_id)
        self.file_token = None
        self.file_token_until = 0.0

    def clear(self):
        self.token = ""
        self.user_id = None
        self.file_token = None
        self.file_token_until = 0.0

    def session(self):
        s = getattr(self._local, "session", None)
        if s is None:
            s = requests.Session()
            s.headers["User-Agent"] = "KovappPC/beta3"
            self._local.session = s
        return s

    # ── адреса ───────────────────────────────────────────────────────────────

    def url(self, path, query=None):
        base = self.host.rstrip("/") + "/" + str(path).lstrip("/")
        if query:
            q = {k: v for k, v in query.items() if v is not None and v != ""}
            if q:
                base += ("&" if "?" in base else "?") + urllib.parse.urlencode(q, doseq=True)
        return base

    def media_token(self):
        if self.file_token and time.time() < self.file_token_until:
            return self.file_token
        try:
            self.refresh_file_token()
        except Exception:
            pass
        return self.file_token or self.token

    def file_url(self, path, query=None):
        q = dict(query or {})
        q["token"] = self.media_token()
        return self.url(path, q)

    def ws_url(self):
        u = urllib.parse.urlsplit(self.host + "/ws/chat")
        scheme = "wss" if u.scheme == "https" else "ws"
        return urllib.parse.urlunsplit((scheme, u.netloc, u.path, "", ""))

    def refresh_file_token(self):
        with self._lock:
            if self.file_token and time.time() < self.file_token_until - 60:
                return self.file_token
            try:
                r = self.request("POST", "files/access-token")
                life = max(120, int((r or {}).get("expires_in") or 1200))
                self.file_token = (r or {}).get("access_token")
                self.file_token_until = time.time() + life if self.file_token else 0
            except ApiError as e:
                # старый сервер без файловых токенов — ссылки идут с токеном сессии
                if e.status not in (404, 405):
                    raise
                self.file_token = None
                self.file_token_until = time.time() + 600
            return self.file_token

    # ── запросы ──────────────────────────────────────────────────────────────

    def request(self, method, path, json_body=None, data=None, files=None, query=None,
                headers=None, timeout=DEFAULT_TIMEOUT, stream=False, anonymous=False, raw=False, _retried=False):
        hdrs = dict(headers or {})
        if self.token and not anonymous:
            hdrs["Authorization"] = "Bearer " + self.token
        kwargs = {"headers": hdrs, "timeout": timeout, "stream": stream}
        if json_body is not None:
            kwargs["data"] = json.dumps(json_body, ensure_ascii=False, default=str).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        elif data is not None:
            kwargs["data"] = data
        if files is not None:
            kwargs["files"] = files
        res = self.session().request(method, self.url(path, query), **kwargs)
        if res.status_code == 401 and not anonymous and callable(self.on_unauthorized):
            try:
                self.on_unauthorized()
            except Exception:
                pass
        if res.status_code == 429 and not _retried:
            wait = min(float(res.headers.get("Retry-After") or 1), 5.0)
            time.sleep(wait)
            return self.request(method, path, json_body, data, files, query, headers, timeout,
                                stream, anonymous, raw, True)
        if raw or stream:
            if res.status_code >= 400:
                self._raise(res)
            return res
        ctype = res.headers.get("content-type", "")
        if "application/json" in ctype:
            try:
                payload = res.json()
            except ValueError:
                payload = None
        else:
            payload = res.text
        if res.status_code >= 400:
            self._raise(res, payload)
        return payload

    @staticmethod
    def _raise(res, payload=None):
        if payload is None:
            try:
                payload = res.json()
            except Exception:
                payload = None
        detail = None
        if isinstance(payload, dict):
            d = payload.get("detail")
            if isinstance(d, str):
                detail = d
            elif isinstance(d, list) and d:
                # ошибки проверки полей FastAPI
                first = d[0] if isinstance(d[0], dict) else {}
                detail = first.get("msg") or None
        raise ApiError(res.status_code, detail or ("HTTP %s" % res.status_code), payload)

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, **kw):
        return self.request("POST", path, **kw)

    def put(self, path, **kw):
        return self.request("PUT", path, **kw)

    def patch(self, path, **kw):
        return self.request("PATCH", path, **kw)

    def delete(self, path, **kw):
        return self.request("DELETE", path, **kw)

    def login(self, host, username, password):
        self.set_auth(host, "", None)
        data = self.request("POST", "login", data={"username": username, "password": password},
                            anonymous=True,
                            headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.set_auth(host, data.get("access_token"), data.get("user_id"))
        return data

    def download(self, path, dest, query=None, progress=None, cancelled=None, chunk=256 * 1024):
        """Скачивание в файл с прогрессом; cancelled() → True прерывает."""
        res = self.request("GET", path, query=query, stream=True, timeout=(10, 300))
        total = int(res.headers.get("content-length") or 0)
        done = 0
        try:
            with open(dest, "wb") as f:
                for part in res.iter_content(chunk):
                    if cancelled and cancelled():
                        raise InterruptedError("Скачивание отменено")
                    if part:
                        f.write(part)
                        done += len(part)
                        if progress:
                            progress(done, total)
        finally:
            res.close()
        return dest


client = Client()
