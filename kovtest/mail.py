import email
import re
import time
from email import policy
from pathlib import Path


# ===== письма из приёмника почты =====

def mailbox(settings) -> Path:
    return Path(settings.mail_dir) if settings.mail_dir else None


def available(settings) -> bool:
    box = mailbox(settings)
    return bool(box and box.is_dir())


def wait_code(settings, recipient: str, since: float, timeout: float = 60.0) -> str:
    box = mailbox(settings)
    deadline = time.time() + timeout
    while time.time() < deadline:
        for path in sorted(box.glob("*.eml"), key=lambda p: p.stat().st_mtime, reverse=True):
            if path.stat().st_mtime < since - 1:
                continue
            message = email.message_from_bytes(path.read_bytes(), policy=policy.default)
            if recipient.lower() not in str(message.get("To", "")).lower():
                continue
            part = message.get_body(preferencelist=("plain", "html"))
            text = part.get_content() if part else ""
            found = re.search(r"сброса пароля:\s*(\d{4,8})", text)
            if found:
                return found.group(1)
        time.sleep(1)
    return ""
