# -*- coding: utf-8 -*-
import io
import sys


# ── Обработка фото в отдельном процессе с квотой памяти ──
def main(argv) -> int:
    src, dst, max_side, webp_q, jpeg_q, prefer_webp = argv[1], argv[2], int(argv[3]), int(argv[4]), int(argv[5]), argv[6] == "1"
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS = None
    img = Image.open(src)
    if max_side > 0 and img.format == "JPEG" and max(img.size) >= max_side * 2:
        w, h = img.size
        r = max_side / float(max(w, h))
        img.draft("RGB", (max(1, int(w * r)), max(1, int(h * r))))
    img.load()
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    w, h = img.size
    if max_side > 0 and max(w, h) > max_side:
        r = max_side / float(max(w, h))
        img = img.resize((max(1, int(w * r)), max(1, int(h * r))), Image.LANCZOS)
    alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    out = io.BytesIO()
    if prefer_webp:
        (img if alpha or img.mode in ("RGB", "L") else img.convert("RGB")).save(out, "WEBP", quality=webp_q, method=4)
        ext = ".webp"
    else:
        (img if img.mode in ("RGB", "L") else img.convert("RGB")).save(out, "JPEG", quality=jpeg_q, optimize=True)
        ext = ".jpg"
    with open(dst, "wb") as fh:
        fh.write(out.getvalue())
    sys.stdout.write(ext)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
