# -*- coding: utf-8 -*-
"""Состояние безопасности: коды сброса пароля и торможение перебора.

Данные живут в памяти процесса. Сервер запускается в одном экземпляре
(этого требуют ConnectionManager и планировщик бэкапов), поэтому общего
хранилища не нужно. Перезапуск аннулирует незавершённые сбросы — код
живёт минуты, потеря некритична.

Сам код в память не кладётся: хранится HMAC-SHA256 с разовым перцем,
чтобы дамп памяти или случайный лог не выдали действующий код.
"""

import hmac
import hashlib
import secrets
import threading
import time


# Перец живёт столько же, сколько процесс: при рестарте все коды протухают
_PEPPER = secrets.token_bytes(32)


def _hash_code(code: str) -> str:
    return hmac.new(_PEPPER, (code or "").encode("utf-8"), hashlib.sha256).hexdigest()


class ResetCodeStore:
    """Коды сброса пароля: срок жизни, счётчик попыток, пауза между запросами.

    Ключ — имя пользователя в нижнем регистре. Запись создаётся даже для
    несуществующего логина: иначе по времени ответа можно перебирать,
    какие учётные записи есть в системе.
    """

    def __init__(self, ttl_seconds: int, max_attempts: int, cooldown_seconds: int):
        self._ttl = ttl_seconds
        self._max_attempts = max_attempts
        self._cooldown = cooldown_seconds
        self._lock = threading.Lock()
        self._entries = {}      # username -> dict
        self._last_request = {}  # username -> timestamp

    @staticmethod
    def _key(username: str) -> str:
        return (username or "").strip().lower()

    def _purge(self, now: float) -> None:
        for key in [k for k, e in self._entries.items() if e["expires_at"] <= now]:
            self._entries.pop(key, None)
        for key in [k for k, t in self._last_request.items() if now - t > self._cooldown * 10]:
            self._last_request.pop(key, None)

    def can_request(self, username: str) -> bool:
        """Пауза между запросами кода. Считается и для несуществующих логинов."""
        key = self._key(username)
        now = time.time()
        with self._lock:
            self._purge(now)
            last = self._last_request.get(key)
            if last is not None and now - last < self._cooldown:
                return False
            self._last_request[key] = now
            return True

    def issue(self, username: str, code: str) -> None:
        key = self._key(username)
        now = time.time()
        with self._lock:
            self._purge(now)
            self._entries[key] = {
                "code_hash": _hash_code(code),
                "expires_at": now + self._ttl,
                "attempts": 0,
            }

    def verify(self, username: str, code: str, consume: bool = False):
        """Возвращает пару (состояние, сколько попыток осталось).

        Состояния: ok | missing | expired | locked | invalid.
        consume=True удаляет запись при успехе — код одноразовый.
        """
        key = self._key(username)
        now = time.time()
        with self._lock:
            self._purge(now)
            entry = self._entries.get(key)
            if entry is None:
                return "missing", 0
            if entry["expires_at"] <= now:
                self._entries.pop(key, None)
                return "expired", 0
            if entry["attempts"] >= self._max_attempts:
                self._entries.pop(key, None)
                return "locked", 0

            entry["attempts"] += 1
            left = max(0, self._max_attempts - entry["attempts"])

            if not hmac.compare_digest(entry["code_hash"], _hash_code(code)):
                if entry["attempts"] >= self._max_attempts:
                    self._entries.pop(key, None)
                    return "locked", 0
                return "invalid", left

            if consume:
                self._entries.pop(key, None)
                return "ok", self._max_attempts
            return "ok", left

    def clear(self, username: str) -> None:
        key = self._key(username)
        with self._lock:
            self._entries.pop(key, None)


class LoginThrottle:
    """Замедляет подбор пароля по конкретной учётной записи.

    Блокировки нет намеренно: она позволила бы закрыть чужой аккаунт
    чужими же неудачными попытками. Вместо этого растёт задержка ответа —
    на живого человека она почти не влияет, перебор делает бессмысленным.
    Ограничение по IP отдельно держат nginx и fail2ban.
    """

    def __init__(self, free_attempts: int = 5, max_delay: float = 8.0, window_seconds: int = 900):
        self._free = free_attempts
        self._max_delay = max_delay
        self._window = window_seconds
        self._lock = threading.Lock()
        self._failures = {}  # username -> (count, last_ts)

    @staticmethod
    def _key(username: str) -> str:
        return (username or "").strip().lower()

    def delay_for(self, username: str) -> float:
        key = self._key(username)
        now = time.time()
        with self._lock:
            count, last = self._failures.get(key, (0, 0.0))
            if now - last > self._window:
                return 0.0
        if count <= self._free:
            return 0.0
        return min(self._max_delay, 2 ** (count - self._free) * 0.25)

    def register_failure(self, username: str) -> None:
        key = self._key(username)
        now = time.time()
        with self._lock:
            count, last = self._failures.get(key, (0, 0.0))
            if now - last > self._window:
                count = 0
            self._failures[key] = (count + 1, now)
            if len(self._failures) > 10000:
                stale = [k for k, (_, t) in self._failures.items() if now - t > self._window]
                for k in stale:
                    self._failures.pop(k, None)

    def reset(self, username: str) -> None:
        with self._lock:
            self._failures.pop(self._key(username), None)
