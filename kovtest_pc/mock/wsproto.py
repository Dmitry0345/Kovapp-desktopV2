# -*- coding: utf-8 -*-
"""Минимальный WebSocket (RFC 6455) для тестового сервера: только то, что
нужно чату и каналу уведомлений — текст, ping/pong, закрытие, фрагменты."""

import base64
import hashlib
import struct
import threading

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_MESSAGE = 1 << 20


class Closed(Exception):
    pass


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + GUID).encode("ascii")).digest()).decode("ascii")


def handshake(handler) -> bool:
    """Ответ 101 на запрос-апгрейд из BaseHTTPRequestHandler; False — это не WebSocket."""
    h = handler.headers
    if (h.get("Upgrade", "").lower() != "websocket" or "upgrade" not in h.get("Connection", "").lower()
            or not h.get("Sec-WebSocket-Key")):
        return False
    handler.send_response_only(101, "Switching Protocols")
    handler.send_header("Upgrade", "websocket")
    handler.send_header("Connection", "Upgrade")
    handler.send_header("Sec-WebSocket-Accept", accept_key(h["Sec-WebSocket-Key"]))
    handler.end_headers()
    handler.wfile.flush()
    return True


class Socket:
    """Серверная сторона соединения поверх уже открытого сокета."""

    def __init__(self, rfile, wfile):
        self.rfile, self.wfile = rfile, wfile
        self.lock = threading.Lock()
        self.closed = False
        self.sent_close = False

    def _read(self, n: int) -> bytes:
        data = self.rfile.read(n)
        if data is None or len(data) < n:
            raise Closed("соединение закрыто")
        return data

    def _frame(self):
        b1, b2 = self._read(2)
        fin, opcode = b1 & 0x80, b1 & 0x0F
        masked, length = b2 & 0x80, b2 & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._read(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._read(8))[0]
        if not masked:
            raise Closed("кадр клиента без маски")       # RFC 6455: клиент обязан маскировать
        if length > MAX_MESSAGE:
            raise Closed("слишком большое сообщение")
        mask = self._read(4)
        payload = bytearray(self._read(length))
        for i in range(length):
            payload[i] ^= mask[i % 4]
        return bool(fin), opcode, bytes(payload)

    def receive(self):
        """Следующее текстовое сообщение; None — клиент закрыл соединение."""
        parts, total = [], 0
        while True:
            try:
                fin, opcode, payload = self._frame()
            except Closed:
                self.closed = True
                return None
            if opcode == 0x8:
                if not self.sent_close:          # отвечаем на закрытие один раз
                    self.send_raw(0x8, payload[:2])
                self.closed = True
                return None
            if opcode == 0x9:
                self.send_raw(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode in (0x1, 0x2, 0x0):
                parts.append(payload)
                total += len(payload)
                if total > MAX_MESSAGE:
                    raise Closed("слишком большое сообщение")
                if fin:
                    return b"".join(parts).decode("utf-8")

    def send_raw(self, opcode: int, payload: bytes) -> None:
        head = bytes([0x80 | opcode])
        n = len(payload)
        if n < 126:
            head += bytes([n])
        elif n < 65536:
            head += bytes([126]) + struct.pack(">H", n)
        else:
            head += bytes([127]) + struct.pack(">Q", n)
        with self.lock:
            if self.closed and opcode != 0x8:
                raise Closed("соединение закрыто")
            if opcode == 0x8:
                if self.sent_close:
                    return
                self.sent_close = True
            self.wfile.write(head + payload)
            self.wfile.flush()

    def send_text(self, text: str) -> None:
        self.send_raw(0x1, text.encode("utf-8"))

    def close(self, code: int = 1000) -> None:
        try:
            self.send_raw(0x8, struct.pack(">H", code))
        except Exception:
            pass
        self.closed = True
