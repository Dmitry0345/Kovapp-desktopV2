# -*- coding: utf-8 -*-
"""Генерация ключей VAPID для Web Push.

Запуск:  python -m server.gen_vapid
Печатает две строки для .env и сохраняет приватный ключ в файл vapid_private.pem
рядом с .env (права 600). Публичный ключ — в формате, который принимает браузер
(base64url несжатой точки P-256), приватный — путь к PEM-файлу.
"""

import base64
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def main() -> int:
    app_root = Path(__file__).resolve().parent.parent
    pem_path = app_root / "vapid_private.pem"
    if pem_path.exists() and "--force" not in sys.argv:
        print(f"Файл {pem_path} уже существует. Повторная генерация сделает старые подписки "
              f"недействительными; чтобы всё равно перегенерировать, добавьте --force")
        return 1

    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    pem_path.write_bytes(pem)
    try:
        os.chmod(pem_path, 0o600)
    except Exception:
        pass

    public_raw = key.public_key().public_bytes(serialization.Encoding.X962,
                                               serialization.PublicFormat.UncompressedPoint)
    print("Добавьте в .env:\n")
    print(f"VAPID_PUBLIC_KEY={b64url(public_raw)}")
    print(f"VAPID_PRIVATE_KEY={pem_path}")
    print("VAPID_SUBJECT=mailto:admin@example.com   # любой контактный e-mail организации")
    print("\nЗатем перезапустите сервер. Проверка: POST /push/test из веб-приложения.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
