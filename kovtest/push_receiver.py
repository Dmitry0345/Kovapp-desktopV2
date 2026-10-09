import base64
import datetime
import json
import os
import socket
import ssl
import struct
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.x509.oid import NameOID


# ===== ключи подписки и расшифровка (RFC 8291, aes128gcm) =====

def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64u_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class Keys:
    def __init__(self):
        self.private = ec.generate_private_key(ec.SECP256R1())
        self.public = self.private.public_key().public_bytes(serialization.Encoding.X962,
                                                             serialization.PublicFormat.UncompressedPoint)
        self.auth = os.urandom(16)

    def subscription(self, endpoint: str) -> dict:
        return {"endpoint": endpoint, "keys": {"p256dh": b64u(self.public), "auth": b64u(self.auth)}}

    def decrypt(self, body: bytes) -> bytes:
        salt, rs, idlen = body[:16], struct.unpack(">I", body[16:20])[0], body[20]
        sender = body[21:21 + idlen]
        records = body[21 + idlen:]
        peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sender)
        secret = self.private.exchange(ec.ECDH(), peer)
        ikm = HKDF(hashes.SHA256(), 32, self.auth, b"WebPush: info\x00" + self.public + sender).derive(secret)
        cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
        nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
        out = b""
        for index, start in enumerate(range(0, len(records), rs)):
            iv = (int.from_bytes(nonce, "big") ^ index).to_bytes(12, "big")
            plain = AESGCM(cek).decrypt(iv, records[start:start + rs], None).rstrip(b"\x00")
            out += plain[:-1]
            if plain[-1:] == b"\x02":
                break
        return out


# ===== тестовый push-сервис =====

def own_address() -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def make_certificate(folder: Path, address: str):
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Kovapp autotest push CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
          .not_valid_after(now + datetime.timedelta(days=2))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(ca_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    import ipaddress
    names = [x509.DNSName("localhost")]
    try:
        names.append(x509.IPAddress(ipaddress.ip_address(address)))
    except ValueError:
        names.append(x509.DNSName(address))
    cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, address)]))
            .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=2))
            .add_extension(x509.SubjectAlternativeName(names), critical=False).sign(ca_key, hashes.SHA256()))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "push.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (folder / "push.key").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                        serialization.NoEncryption()))
    return ca.public_bytes(serialization.Encoding.PEM)


class Receiver:
    def __init__(self, log, ca_bundle: str = "", port: int = 0):
        self.log = log
        self.address = os.environ.get("PUSH_RECEIVER_HOST") or own_address()
        self.inbox = {}
        self.answers = {}
        self.lock = threading.Lock()
        folder = Path(os.environ.get("TMPDIR", "/tmp")) / ("kovpush-" + uuid.uuid4().hex[:8])
        ca = make_certificate(folder, self.address)
        if ca_bundle:
            with open(ca_bundle, "ab") as handle:
                handle.write(b"\n" + ca)
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                box = self.path.strip("/").split("/")[-1]
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                with receiver.lock:
                    receiver.inbox.setdefault(box, []).append({"headers": dict(self.headers), "body": body, "at": time.time()})
                    status = receiver.answers.get(box, 201)
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()

        self.server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(folder / "push.crt"), str(folder / "push.key"))
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.log.info("тестовый push-сервис: https://%s:%d" % (self.address, self.port))

    def endpoint(self, box: str) -> str:
        return "https://%s:%d/push/%s" % (self.address, self.port, box)

    def answer(self, box: str, status: int) -> None:
        with self.lock:
            self.answers[box] = status

    def wait(self, box: str, count: int = 1, timeout: float = 20.0) -> list:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.lock:
                got = list(self.inbox.get(box, []))
            if len(got) >= count:
                return got
            time.sleep(0.2)
        with self.lock:
            return list(self.inbox.get(box, []))

    def clear(self, box: str) -> None:
        with self.lock:
            self.inbox.pop(box, None)

    def close(self) -> None:
        try:
            self.server.shutdown()
        except Exception:
            pass


def payload_of(keys: Keys, message: dict) -> dict:
    return json.loads(keys.decrypt(message["body"]).decode("utf-8"))
