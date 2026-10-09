#!/usr/bin/env python3
"""Шифрует тестовые уведомления так же, как сервер (server_app/native_push.py),
чтобы Java-тест расшифровал их кодом приложения."""
import base64
import json
import os
import sys

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

out = sys.argv[1]
key = bytes(range(32))
vectors = []
for i, payload in enumerate([
    {"id": "n1", "uid": 7, "title": "Иван Петров", "body": "Привет! Как дела?", "url": "#/messenger/12", "tag": "chat-12", "kind": "chat", "chat_id": 12},
    {"id": "n2", "uid": 7, "title": "Уведомление", "body": "x" * 300, "url": "javascript:alert(1)", "tag": "notification-5", "kind": "notification"},
    {"id": "n3", "uid": 9, "title": "Проверка", "body": "Push-уведомления работают", "url": "#/notifications", "tag": "push-test", "probe": "0123456789abcdef0123456789abcdef"},
]):
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, json.dumps(payload, ensure_ascii=False).encode('utf-8'), None)
    enc = lambda b: base64.urlsafe_b64encode(b).decode().rstrip('=')
    vectors.append({"data": {"v": "1", "n": enc(nonce), "c": enc(ct)}, "payload": payload})
json.dump({"key": base64.b64encode(key).decode(), "vectors": vectors}, open(out, 'w', encoding='utf-8'), ensure_ascii=False)
print('push vectors:', len(vectors))
