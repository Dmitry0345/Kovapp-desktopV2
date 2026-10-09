import os
import io
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


# ===== картинки =====

def photo_bytes(width: int = 1600, height: int = 1000, fmt: str = "JPEG", seed: int = 1) -> bytes:
    image = Image.new("RGB", (width, height), (30 + seed * 17 % 200, 90, 160))
    draw = ImageDraw.Draw(image)
    step = max(40, width // 16)
    for x in range(0, width, step):
        draw.rectangle([x, 0, x + step // 2, height], fill=(240, 180 - (x * 7 % 120), 40))
    for y in range(0, height, step):
        draw.line([(0, y), (width, y)], fill=(255, 255, 255), width=3)
    draw.ellipse([width // 3, height // 3, width * 2 // 3, height * 2 // 3], fill=(20, 200, 120))
    out = io.BytesIO()
    image.save(out, fmt, quality=90) if fmt in ("JPEG", "WEBP") else image.save(out, fmt)
    return out.getvalue()


def camera_photo_bytes(width: int = 2400, height: int = 1600) -> bytes:
    base = Image.linear_gradient("L").resize((width, height)).convert("RGB")
    noise = Image.effect_noise((width, height), 60).convert("RGB")
    image = Image.blend(base, noise, 0.5)
    draw = ImageDraw.Draw(image)
    for x in range(0, width, 120):
        draw.ellipse([x, (x * 7) % height, x + 200, (x * 7) % height + 200], fill=((x * 3) % 255, 120, 200))
    image = image.filter(ImageFilter.GaussianBlur(0.6))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=97)
    return out.getvalue()


# ===== видео =====

def video_bytes(seconds: int = 3, width: int = 640, height: int = 360) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден — видео для проверки не собрать")
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "clip.mp4"
        subprocess.run([ffmpeg, "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc=duration=%d:size=%dx%d:rate=25" % (seconds, width, height),
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=%d" % seconds,
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                        "-movflags", "+faststart", str(target)], check=True)
        return target.read_bytes()


# ===== документы =====

def pdf_bytes(text: str = "Kovapp autotest", pages: int = 1) -> bytes:
    kids = " ".join("%d 0 R" % (4 + i * 2) for i in range(pages))
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               ("<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, pages)).encode(),
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    for i in range(pages):
        line = ("%s - page %d" % (text, i + 1)).replace("(", "").replace(")", "")
        stream = ("BT /F1 28 Tf 72 700 Td (%s) Tj ET 0 0.5 0.8 rg 72 120 468 %d re f" % (line, 60 + i * 40)).encode()
        page_id, content_id = 4 + i * 2, 5 + i * 2
        objects.append(("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R "
                        "/Resources << /Font << /F1 3 0 R >> >> >>" % content_id).encode())
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref))
    return out.getvalue()


def log_bytes(lines: int = 6000) -> bytes:
    row = "2026-09-24 10:%02d:%02d INFO  kovapp.autotest строка журнала для проверки сжатия на устройстве\n"
    return "".join(row % (i // 60 % 60, i % 60) for i in range(lines)).encode("utf-8")


def gzip_bomb(expanded_mb: int = 300) -> bytes:
    import gzip as _gzip
    out = io.BytesIO()
    with _gzip.GzipFile(fileobj=out, mode="wb", compresslevel=9) as z:
        chunk = b"\0" * (1024 * 1024)
        for _ in range(expanded_mb):
            z.write(chunk)
    return out.getvalue()


# ===== как файлы приходят с телефонов после сжатия на устройстве =====

def jpeg_thumb(image, side: int = 320) -> bytes:
    small = image.copy()
    small.thumbnail((side, side))
    out = io.BytesIO()
    small.convert("RGB").save(out, "JPEG", quality=80)
    return out.getvalue()


def device_photo() -> tuple:
    image = Image.open(io.BytesIO(camera_photo_bytes())).convert("RGB")
    image.thumbnail((1600, 1600))
    out = io.BytesIO()
    image.save(out, "WEBP", quality=80, method=4)
    return out.getvalue(), jpeg_thumb(image), image.size


def ffmpeg_video(width: int, height: int, seconds: int, codec: str, bitrate: str) -> bytes:
    import shutil
    import subprocess
    import tempfile
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return b""
    target = Path(tempfile.mkdtemp(prefix="kovvid-")) / ("clip.mp4")
    encoder = (["-c:v", "libx265", "-tag:v", "hvc1", "-preset", "ultrafast", "-x265-params", "log-level=error"]
               if codec == "hevc" else ["-c:v", "libx264", "-profile:v", "main", "-preset", "veryfast"])
    subprocess.run([ffmpeg, "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc2=duration=%d:size=%dx%d:rate=30" % (seconds, width, height),
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=%d" % seconds,
                    "-vf", "noise=alls=12:allf=t"] + encoder + ["-b:v", bitrate, "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "96k", "-shortest", "-movflags", "+faststart", str(target)], check=True)
    return target.read_bytes()


def device_video() -> tuple:
    data = ffmpeg_video(1280, 720, 8, "h264", "1200k")
    poster = Image.new("RGB", (640, 360), (40, 90, 160))
    return data, jpeg_thumb(poster), (1280, 720), 8.0


def raw_video() -> bytes:
    return ffmpeg_video(1920, 1080, 6, "hevc", "8M")


def uniquify(name: str, data: bytes) -> bytes:
    tail = os.urandom(16).hex().encode()
    lower = name.lower()
    if lower.endswith((".log", ".txt", ".csv")):
        return data + b"\n# " + tail + b"\n"
    if lower.endswith(".pdf"):
        return data + b"%" + tail + b"\n"
    return data + tail


def asset(name: str) -> bytes:
    return (Path(__file__).resolve().parent / "assets" / name).read_bytes()


def docx_bytes(text: str = "Документ автотеста Kovapp") -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml",
                   '<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                   '<w:body><w:p><w:r><w:t>%s</w:t></w:r></w:p></w:body></w:document>' % text)
    return out.getvalue()


def xlsx_bytes(rows=None) -> bytes:
    from openpyxl import Workbook
    book = Workbook()
    sheet = book.active
    sheet.title = "Смета"
    for row in rows or [["Позиция", "Количество", "Цена"], ["Кабель", 10, 125.5], ["Датчик", 3, 990]]:
        sheet.append(row)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def text_bytes(text: str = "Проверочный текст автотеста\n") -> bytes:
    return text.encode("utf-8")


def csv_bytes(rows) -> bytes:
    lines = [";".join(str(c) for c in row) for row in rows]
    return ("\n".join(lines) + "\n").encode("utf-8-sig")


# ===== набор файлов =====

class MediaKit:
    def __init__(self):
        self._cache = {}

    def get(self, kind: str) -> tuple:
        if kind not in self._cache:
            self._cache[kind] = self._make(kind)
        return self._cache[kind]

    def fresh(self, kind: str) -> tuple:
        name, data, ctype = self.get(kind)
        return name, uniquify(name, data), ctype

    def _make(self, kind: str) -> tuple:
        if kind == "jpg":
            return "photo-autotest.jpg", photo_bytes(), "image/jpeg"
        if kind == "camera":
            return "camera-autotest.jpg", camera_photo_bytes(), "image/jpeg"
        if kind == "png":
            return "scheme-autotest.png", photo_bytes(900, 600, "PNG", 3), "image/png"
        if kind == "mp4":
            return "clip-autotest.mp4", video_bytes(), "video/mp4"
        if kind == "pdf":
            return "act-autotest.pdf", pdf_bytes(), "application/pdf"
        if kind == "pdf5":
            return "report-autotest.pdf", pdf_bytes("Kovapp autotest report", 6), "application/pdf"
        if kind == "pptx":
            return "slides-autotest.pptx", asset("slides-autotest.pptx"), "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        if kind == "log":
            return "journal-autotest.log", log_bytes(), "text/plain"
        if kind == "docx":
            return "contract-autotest.docx", docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if kind == "xlsx":
            return "estimate-autotest.xlsx", xlsx_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if kind == "txt":
            return "notes-autotest.txt", text_bytes(), "text/plain"
        raise KeyError(kind)
