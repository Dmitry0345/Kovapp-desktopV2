# -*- coding: utf-8 -*-
"""ThumbHash — размытая заглушка картинки из ~25 байт (js/media/thumbhash.js).

Те же формулы, что у веб-версии, и то же округление, что у Math.round
в JavaScript (половина — вверх), поэтому заглушки совпадают.
Алгоритм ThumbHash © 2023 Evan Wallace, лицензия MIT — licenses/ThumbHash-MIT.txt.
"""

import base64
import math


def _jround(x):
    """Math.round из JavaScript: .5 округляется вверх."""
    return int(math.floor(x + 0.5))


def encode(w, h, rgba):
    """rgba — байты w*h*4 (или список). Возвращает bytes."""
    if w > 100 or h > 100:
        raise ValueError("ThumbHash: не больше 100×100")
    n = w * h
    avg_r = avg_g = avg_b = avg_a = 0.0
    for i in range(n):
        j = i * 4
        a = rgba[j + 3] / 255.0
        avg_r += a / 255.0 * rgba[j]
        avg_g += a / 255.0 * rgba[j + 1]
        avg_b += a / 255.0 * rgba[j + 2]
        avg_a += a
    if avg_a:
        avg_r /= avg_a
        avg_g /= avg_a
        avg_b /= avg_a
    has_alpha = avg_a < n
    l_limit = 5 if has_alpha else 7
    lx = max(1, _jround(l_limit * w / max(w, h)))
    ly = max(1, _jround(l_limit * h / max(w, h)))
    L, P, Q, A = [0.0] * n, [0.0] * n, [0.0] * n, [0.0] * n
    for i in range(n):
        j = i * 4
        alpha = rgba[j + 3] / 255.0
        r = avg_r * (1 - alpha) + alpha / 255.0 * rgba[j]
        g = avg_g * (1 - alpha) + alpha / 255.0 * rgba[j + 1]
        b = avg_b * (1 - alpha) + alpha / 255.0 * rgba[j + 2]
        L[i] = (r + g + b) / 3
        P[i] = (r + g) / 2 - b
        Q[i] = r - g
        A[i] = alpha

    def channel(ch, nx, ny):
        dc = 0.0
        scale = 0.0
        ac = []
        # строки сворачиваются по x один раз на каждый cx — быстрее прямой суммы
        rowdot = {}
        for cy in range(ny):
            cx = 0
            while cx * ny < nx * (ny - cy):
                if cx not in rowdot:
                    fx = [math.cos(math.pi / w * cx * (x + 0.5)) for x in range(w)]
                    rowdot[cx] = [sum(ch[x + y * w] * fx[x] for x in range(w)) for y in range(h)]
                rd = rowdot[cx]
                f = 0.0
                for y in range(h):
                    f += rd[y] * math.cos(math.pi / h * cy * (y + 0.5))
                f /= n
                if cx or cy:
                    ac.append(f)
                    scale = max(scale, abs(f))
                else:
                    dc = f
                cx += 1
        if scale:
            ac = [0.5 + 0.5 / scale * v for v in ac]
        return dc, ac, scale

    l_dc, l_ac, l_scale = channel(L, max(3, lx), max(3, ly))
    p_dc, p_ac, p_scale = channel(P, 3, 3)
    q_dc, q_ac, q_scale = channel(Q, 3, 3)
    if has_alpha:
        a_dc, a_ac, a_scale = channel(A, 5, 5)
    is_landscape = w > h
    header24 = (_jround(63 * l_dc) | (_jround(31.5 + 31.5 * p_dc) << 6) | (_jround(31.5 + 31.5 * q_dc) << 12)
                | (_jround(31 * l_scale) << 18) | (int(has_alpha) << 23))
    header16 = ((ly if is_landscape else lx) | (_jround(63 * p_scale) << 3) | (_jround(63 * q_scale) << 9)
                | (int(is_landscape) << 15))
    out = [header24 & 255, (header24 >> 8) & 255, header24 >> 16, header16 & 255, header16 >> 8]
    ac_start = 6 if has_alpha else 5
    if has_alpha:
        out.append(_jround(15 * a_dc) | (_jround(15 * a_scale) << 4))
    index = 0
    for ac in ([l_ac, p_ac, q_ac, a_ac] if has_alpha else [l_ac, p_ac, q_ac]):
        for f in ac:
            k = ac_start + (index >> 1)
            while len(out) <= k:
                out.append(0)
            out[k] |= _jround(15 * f) << ((index & 1) << 2)
            index += 1
    return bytes(v & 255 for v in out)


def to_base64(data):
    return base64.b64encode(bytes(data)).decode("ascii")


def from_base64(text):
    return base64.b64decode(str(text or ""))


def aspect(h):
    header = h[3]
    has_alpha = h[2] & 0x80
    is_landscape = h[4] & 0x80
    lx = (5 if has_alpha else 7) if is_landscape else header & 7
    ly = header & 7 if is_landscape else (5 if has_alpha else 7)
    return lx / ly if ly else 1.0


def decode(h):
    """bytes → (w, h, rgba bytearray) — картинка до 32×32 для заглушки."""
    header24 = h[0] | (h[1] << 8) | (h[2] << 16)
    header16 = h[3] | (h[4] << 8)
    l_dc = (header24 & 63) / 63
    p_dc = ((header24 >> 6) & 63) / 31.5 - 1
    q_dc = ((header24 >> 12) & 63) / 31.5 - 1
    l_scale = ((header24 >> 18) & 31) / 31
    has_alpha = header24 >> 23
    p_scale = ((header16 >> 3) & 63) / 63
    q_scale = ((header16 >> 9) & 63) / 63
    is_landscape = header16 >> 15
    lx = max(3, (5 if has_alpha else 7) if is_landscape else header16 & 7)
    ly = max(3, header16 & 7 if is_landscape else (5 if has_alpha else 7))
    a_dc = (h[5] & 15) / 15 if has_alpha else 1
    a_scale = (h[5] >> 4) / 15 if has_alpha else 0
    ac_start = 6 if has_alpha else 5
    state = {"i": 0}

    def channel(nx, ny, scale):
        ac = []
        for cy in range(ny):
            cx = 1 if cy == 0 else 0
            while cx * ny < nx * (ny - cy):
                i = state["i"]
                ac.append((((h[ac_start + (i >> 1)] >> ((i & 1) << 2)) & 15) / 7.5 - 1) * scale)
                state["i"] = i + 1
                cx += 1
        return ac

    l_ac = channel(lx, ly, l_scale)
    p_ac = channel(3, 3, p_scale * 1.25)
    q_ac = channel(3, 3, q_scale * 1.25)
    a_ac = channel(5, 5, a_scale) if has_alpha else []
    ratio = aspect(h)
    w = _jround(32 if ratio > 1 else 32 * ratio)
    hh = _jround(32 / ratio if ratio > 1 else 32)
    rgba = bytearray(w * hh * 4)
    i = 0
    for y in range(hh):
        for x in range(w):
            l, p, q, a = l_dc, p_dc, q_dc, a_dc
            fx = [math.cos(math.pi / w * (x + 0.5) * cx) for cx in range(max(lx, 5 if has_alpha else 3))]
            fy = [math.cos(math.pi / hh * (y + 0.5) * cy) for cy in range(max(ly, 5 if has_alpha else 3))]
            j = 0
            for cy in range(ly):
                cx = 1 if cy == 0 else 0
                fy2 = fy[cy] * 2
                while cx * ly < lx * (ly - cy):
                    l += l_ac[j] * fx[cx] * fy2
                    j += 1
                    cx += 1
            j = 0
            for cy in range(3):
                cx = 1 if cy == 0 else 0
                fy2 = fy[cy] * 2
                while cx < 3 - cy:
                    f = fx[cx] * fy2
                    p += p_ac[j] * f
                    q += q_ac[j] * f
                    j += 1
                    cx += 1
            if has_alpha:
                j = 0
                for cy in range(5):
                    cx = 1 if cy == 0 else 0
                    fy2 = fy[cy] * 2
                    while cx < 5 - cy:
                        a += a_ac[j] * fx[cx] * fy2
                        j += 1
                        cx += 1
            b = l - 2 / 3 * p
            r = (3 * l - b + q) / 2
            g = r - q
            rgba[i] = int(max(0, 255 * min(1, r)))
            rgba[i + 1] = int(max(0, 255 * min(1, g)))
            rgba[i + 2] = int(max(0, 255 * min(1, b)))
            rgba[i + 3] = int(max(0, 255 * min(1, a)))
            i += 4
    return w, hh, rgba


def to_qimage(text):
    """Заглушка из base64 для плитки (QImage) или None."""
    try:
        from PySide6.QtGui import QImage
        w, hh, rgba = decode(from_base64(text))
        img = QImage(bytes(rgba), w, hh, w * 4, QImage.Format_RGBA8888)
        return img.copy()
    except Exception:
        return None
