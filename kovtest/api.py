import re
import time
from typing import Optional

import requests


# ===== ошибки =====

class ApiError(AssertionError):
    def __init__(self, text: str, response=None):
        super().__init__(text)
        self.response = response


def detail_of(response) -> str:
    try:
        body = response.json()
        if isinstance(body, dict) and "detail" in body:
            value = body["detail"]
            return value if isinstance(value, str) else str(value)[:300]
        return str(body)[:300]
    except Exception:
        return (response.text or "")[:300]


def hide_token(url: str) -> str:
    return re.sub(r"([?&]token=)[^&]+", r"\1…", url)


# ===== клиент API =====

class Api:
    def __init__(self, base_url: str, log, who: str = "аноним", timeout: float = 90.0):
        self.base = base_url.rstrip("/")
        self.log = log
        self.who = who
        self.timeout = timeout
        self.session = requests.Session()
        self.token: Optional[str] = None
        self.user_id: Optional[int] = None
        self.login_name: Optional[str] = None
        self._file_token: Optional[str] = None
        self._file_token_until = 0.0

    def url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return self.base + "/" + path.lstrip("/")

    def login(self, login: str, password: str) -> dict:
        response = self.request("POST", "login", data={"username": login, "password": password}, auth=False)
        if response.status_code != 200:
            raise ApiError("вход %s не удался: %s %s" % (login, response.status_code, detail_of(response)), response)
        body = response.json()
        self.token = body["access_token"]
        self.user_id = int(body["user_id"])
        self.login_name = login
        return body

    def request(self, method: str, path: str, auth: bool = True, headers=None, **kwargs):
        target = self.url(path)
        head = dict(headers or {})
        if auth and self.token and "Authorization" not in head:
            head["Authorization"] = "Bearer " + self.token
        kwargs.setdefault("timeout", self.timeout)
        started = time.time()
        try:
            response = self.session.request(method, target, headers=head, **kwargs)
        except requests.RequestException as e:
            self.log.http(method, hide_token(target), "СБОЙ", (time.time() - started) * 1000, self.who, str(e)[:200])
            raise
        note = ""
        if response.status_code >= 400:
            note = "— " + detail_of(response)[:200]
        self.log.http(method, hide_token(target), response.status_code, (time.time() - started) * 1000, self.who, note)
        return response

    def get(self, path: str, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs):
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs):
        return self.request("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs):
        return self.request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs):
        return self.request("DELETE", path, **kwargs)

    def call(self, method: str, path: str, expect=(200, 201), **kwargs):
        response = self.request(method, path, **kwargs)
        codes = expect if isinstance(expect, (list, tuple, set)) else (expect,)
        if response.status_code not in codes:
            raise ApiError("%s %s → %s: %s" % (method, path, response.status_code, detail_of(response)), response)
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.content

    def file_token(self, force: bool = False) -> str:
        if force or not self._file_token or time.time() > self._file_token_until - 60:
            body = self.call("POST", "files/access-token")
            self._file_token = body["access_token"]
            self._file_token_until = time.time() + float(body.get("expires_in") or 1200)
        return self._file_token

    def file_url(self, path: str, token: Optional[str] = None) -> str:
        target = self.url(path)
        sign = "&" if "?" in target else "?"
        return target + sign + "token=" + (token or self.file_token())

    def get_file(self, path: str, token: Optional[str] = None, **kwargs):
        kwargs.setdefault("auth", False)
        return self.request("GET", self.file_url(path, token), **kwargs)
