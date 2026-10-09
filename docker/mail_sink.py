import time
import uuid
from pathlib import Path

from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult

BOX = Path("/shared/mail")


# ===== приёмник почты =====

class Box:
    async def handle_DATA(self, server, session, envelope):
        BOX.mkdir(parents=True, exist_ok=True)
        name = "%d-%s.eml" % (int(time.time() * 1000), uuid.uuid4().hex[:6])
        (BOX / name).write_bytes(envelope.original_content or envelope.content)
        return "250 OK"


def accept(server, session, envelope, mechanism, auth_data):
    return AuthResult(success=True)


if __name__ == "__main__":
    controller = Controller(Box(), hostname="0.0.0.0", port=1025, authenticator=accept,
                            auth_require_tls=False, auth_exclude_mechanism=[])
    controller.start()
    print("Приёмник почты слушает порт 1025", flush=True)
    while True:
        time.sleep(3600)
