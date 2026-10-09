import base64
import json
import os
import socket
import struct
import threading
import time
import uuid
from urllib.parse import urlparse


# ===== «телефон» в собственном канале уведомлений =====

class NativeDevice:
    """Телефон с приложением Kovapp без сервисов Google: регистрируется от имени
    сотрудника (POST push/native/register) и держит канал /ws/native-push, как
    служба DirectPushService в APK: подтверждает каждое уведомление, отвечает
    на ping, при чужом секрете получает отказ."""

    def __init__(self, api, log, base_url: str, who: str = ""):
        self.api = api
        self.log = log
        self.base_url = base_url.rstrip("/")
        self.who = who or getattr(api, "who", "")
        self.device_id = uuid.uuid4().hex
        self.secret = ""
        self.pushes = []
        self.errors = []
        self.flushed = threading.Event()
        self.welcome = threading.Event()
        self.closed = threading.Event()
        self.sock = None
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()

    # ===== регистрация =====

    def register(self, api=None) -> dict:
        body = (api or self.api).call("POST", "push/native/register", json={
            "device_id": self.device_id, "provider": "direct", "platform": "android",
            "model": "Автотест (%s)" % self.who, "app_version": "autotest"})
        self.secret = body["secret"]
        return body

    def unregister(self, api=None) -> None:
        (api or self.api).call("DELETE", "push/native/register", json={"device_id": self.device_id}, expect=(200, 404))

    # ===== канал =====

    def connect(self, secret: str = "", wait: float = 10.0) -> bool:
        """Подключиться и представиться. True — сервер принял (welcome)."""
        parsed = urlparse(self.base_url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        path = parsed.path.rstrip("/") + "/ws/native-push"
        raw = socket.create_connection((host, port), timeout=15)
        if parsed.scheme == "https":
            import ssl
            raw = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
        key = base64.b64encode(os.urandom(16)).decode()
        request = ("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\nUser-Agent: KovappAndroid/autotest\r\n\r\n"
                   % (path, host, port, key))
        raw.sendall(request.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = raw.recv(1)
            if not chunk:
                raise ConnectionError("канал закрыт во время рукопожатия")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError("сервер не принял канал: " + head.split(b"\r\n", 1)[0].decode(errors="replace"))
        raw.settimeout(None)
        self.sock = raw
        self.closed.clear()
        self.welcome.clear()
        self.flushed.clear()
        threading.Thread(target=self._read_loop, daemon=True).start()
        self.send({"type": "hello", "device_id": self.device_id, "secret": secret or self.secret,
                   "app_version": "autotest", "once": False})
        deadline = time.time() + wait
        while time.time() < deadline:
            if self.welcome.is_set():
                return True
            if self.closed.is_set() or self.errors:
                return False
            time.sleep(0.05)
        return False

    def send(self, obj) -> None:
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        head = bytes([0x81])
        n = len(payload)
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self._send_lock:
            self.sock.sendall(head + mask + body)

    def _read_exact(self, n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                raise ConnectionError("канал закрыт")
            data += chunk
        return data

    def _read_loop(self) -> None:
        try:
            while True:
                b1, b2 = self._read_exact(2)
                length = b2 & 0x7F
                if length == 126:
                    length = struct.unpack(">H", self._read_exact(2))[0]
                elif length == 127:
                    length = struct.unpack(">Q", self._read_exact(8))[0]
                data = self._read_exact(length)
                opcode = b1 & 0x0F
                if opcode == 0x8:
                    break
                if opcode == 0x9:
                    continue
                if opcode == 0x1:
                    self._handle(data.decode("utf-8"))
        except (OSError, ConnectionError, ValueError):
            pass
        finally:
            self.closed.set()

    def _handle(self, text: str) -> None:
        msg = json.loads(text)
        kind = msg.get("type")
        if kind == "welcome":
            self.welcome.set()
        elif kind == "push":
            payload = msg.get("payload") or {}
            with self._lock:
                self.pushes.append(payload)
            self.log.info("телефон %s получил уведомление: %s" % (self.who, json.dumps(payload, ensure_ascii=False)[:200]))
            self.send({"type": "ack", "id": msg.get("id")})
        elif kind == "ping":
            self.send({"type": "pong"})
        elif kind == "flushed":
            self.flushed.set()
        elif kind == "error":
            self.errors.append(msg.get("reason") or "error")

    # ===== ожидание =====

    def wait(self, predicate, timeout: float = 15.0):
        """Первое уведомление, подходящее под условие (или None за время ожидания)."""
        deadline = time.time() + timeout
        seen = 0
        while time.time() < deadline:
            with self._lock:
                items = list(self.pushes)
            for payload in items[seen:]:
                if predicate(payload):
                    return payload
            seen = len(items)
            time.sleep(0.1)
        return None

    def ack_probe(self, api_anon, probe: str) -> None:
        """Подтверждение контрольного уведомления — как делает приложение."""
        api_anon.call("POST", "push/ack", json={"probe": probe, "endpoint": "native:" + self.device_id}, auth=False)

    def close(self) -> None:
        if self.sock is not None:
            try:
                with self._send_lock:
                    self.sock.sendall(bytes([0x88, 0x80]) + os.urandom(4))
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
