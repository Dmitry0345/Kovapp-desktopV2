import base64
import json
import os
import socket
import struct
import threading
import time
from urllib.parse import urlparse


# ===== минимальный клиент WebSocket =====

class Socket:
    def __init__(self, base_url: str, token: str, log, who: str = "", quiet: bool = False):
        self.log = log
        self.who = who
        self.quiet = quiet
        self.events = []
        self.closed = False
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        parsed = urlparse(base_url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        path = (parsed.path.rstrip("/") + "/ws/chat") or "/ws/chat"
        raw = socket.create_connection((host, port), timeout=15)
        if parsed.scheme == "https":
            import ssl
            raw = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
        key = base64.b64encode(os.urandom(16)).decode()
        request = ("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n") % (path, host, port, key)
        raw.sendall(request.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = raw.recv(1)
            if not chunk:
                raise ConnectionError("сокет закрыт во время рукопожатия")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError("сервер не принял WebSocket: " + head.split(b"\r\n", 1)[0].decode(errors="replace"))
        raw.settimeout(None)
        self.sock = raw
        self.send({"type": "auth", "token": token})
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._pinger = threading.Thread(target=self._ping_loop, daemon=True)
        self._pinger.start()

    def _frame(self, opcode: int, payload: bytes) -> bytes:
        head = bytes([0x80 | opcode])
        length = len(payload)
        if length < 126:
            head += bytes([0x80 | length])
        elif length < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", length)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", length)
        mask = os.urandom(4)
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return head + mask + body

    def _send_raw(self, opcode: int, payload: bytes) -> None:
        with self._send_lock:
            self.sock.sendall(self._frame(opcode, payload))

    def send(self, obj) -> None:
        self._send_raw(0x1, json.dumps(obj).encode())

    def _read_exact(self, n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                raise ConnectionError("соединение закрыто")
            data += chunk
        return data

    def _read_loop(self) -> None:
        buffer = b""
        try:
            while not self.closed:
                first, second = self._read_exact(2)
                opcode = first & 0x0F
                length = second & 0x7F
                if length == 126:
                    length = struct.unpack(">H", self._read_exact(2))[0]
                elif length == 127:
                    length = struct.unpack(">Q", self._read_exact(8))[0]
                if second & 0x80:
                    mask = self._read_exact(4)
                    payload = bytes(b ^ mask[i % 4] for i, b in enumerate(self._read_exact(length)))
                else:
                    payload = self._read_exact(length)
                if opcode == 0x8:
                    break
                if opcode == 0x9:
                    self._send_raw(0xA, payload)
                    continue
                if opcode in (0x1, 0x0):
                    buffer += payload
                    if not first & 0x80:
                        continue
                    text, buffer = buffer.decode("utf-8", errors="replace"), b""
                    self._handle(text)
        except Exception:
            pass
        finally:
            self.closed = True

    def _handle(self, text: str) -> None:
        try:
            data = json.loads(text)
        except ValueError:
            return
        if isinstance(data, dict) and data.get("type") == "ping":
            try:
                self.send({"type": "pong"})
            except Exception:
                pass
            return
        with self._lock:
            self.events.append((time.time(), data))
        if isinstance(data, dict) and not self.quiet:
            self.log.debug("WS %s ← %s" % (self.who, json.dumps(data, ensure_ascii=False)[:300]))

    def _ping_loop(self) -> None:
        while not self.closed:
            time.sleep(20)
            try:
                self.send({"type": "ping"})
            except Exception:
                return

    def subscribe_task(self, task_id: int) -> None:
        self.send({"type": "task.subscribe", "task_id": task_id})

    def mark(self) -> float:
        return time.time()

    def wait_for(self, predicate, timeout: float = 10.0, since: float = 0.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                for stamp, event in self.events:
                    if stamp >= since and predicate(event):
                        return event
            if self.closed:
                break
            time.sleep(0.1)
        return None

    def close(self) -> None:
        self.closed = True
        try:
            self._send_raw(0x8, b"")
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass
