window.Kov = window.Kov || {};

// ── ThumbHash: заглушка-размытие из ~25 байт ──
// Алгоритм ThumbHash © 2023 Evan Wallace, лицензия MIT — текст в THIRD-PARTY-NOTICES.txt
Kov.ThumbHash = (function () {
  const { PI, round, max, min, cos, abs } = Math;

  function encode(w, h, rgba) {
    if (w > 100 || h > 100) throw new Error('ThumbHash: не больше 100×100');
    let avgR = 0, avgG = 0, avgB = 0, avgA = 0;
    for (let i = 0, j = 0; i < w * h; i++, j += 4) {
      const a = rgba[j + 3] / 255;
      avgR += a / 255 * rgba[j]; avgG += a / 255 * rgba[j + 1]; avgB += a / 255 * rgba[j + 2]; avgA += a;
    }
    if (avgA) { avgR /= avgA; avgG /= avgA; avgB /= avgA; }
    const hasAlpha = avgA < w * h;
    const lLimit = hasAlpha ? 5 : 7;
    const lx = max(1, round(lLimit * w / max(w, h)));
    const ly = max(1, round(lLimit * h / max(w, h)));
    const l = [], p = [], q = [], a = [];
    for (let i = 0, j = 0; i < w * h; i++, j += 4) {
      const alpha = rgba[j + 3] / 255;
      const r = avgR * (1 - alpha) + alpha / 255 * rgba[j];
      const g = avgG * (1 - alpha) + alpha / 255 * rgba[j + 1];
      const b = avgB * (1 - alpha) + alpha / 255 * rgba[j + 2];
      l[i] = (r + g + b) / 3; p[i] = (r + g) / 2 - b; q[i] = r - g; a[i] = alpha;
    }
    const encodeChannel = (channel, nx, ny) => {
      let dc = 0, scale = 0;
      const ac = [], fx = [];
      for (let cy = 0; cy < ny; cy++) {
        for (let cx = 0; cx * ny < nx * (ny - cy); cx++) {
          let f = 0;
          for (let x = 0; x < w; x++) fx[x] = cos(PI / w * cx * (x + 0.5));
          for (let y = 0; y < h; y++) {
            const fy = cos(PI / h * cy * (y + 0.5));
            for (let x = 0; x < w; x++) f += channel[x + y * w] * fx[x] * fy;
          }
          f /= w * h;
          if (cx || cy) { ac.push(f); scale = max(scale, abs(f)); } else dc = f;
        }
      }
      if (scale) for (let i = 0; i < ac.length; i++) ac[i] = 0.5 + 0.5 / scale * ac[i];
      return [dc, ac, scale];
    };
    const [lDc, lAc, lScale] = encodeChannel(l, max(3, lx), max(3, ly));
    const [pDc, pAc, pScale] = encodeChannel(p, 3, 3);
    const [qDc, qAc, qScale] = encodeChannel(q, 3, 3);
    const [aDc, aAc, aScale] = hasAlpha ? encodeChannel(a, 5, 5) : [];
    const isLandscape = w > h;
    const header24 = round(63 * lDc) | (round(31.5 + 31.5 * pDc) << 6) | (round(31.5 + 31.5 * qDc) << 12) | (round(31 * lScale) << 18) | (hasAlpha << 23);
    const header16 = (isLandscape ? ly : lx) | (round(63 * pScale) << 3) | (round(63 * qScale) << 9) | (isLandscape << 15);
    const hash = [header24 & 255, (header24 >> 8) & 255, header24 >> 16, header16 & 255, header16 >> 8];
    const acStart = hasAlpha ? 6 : 5;
    let acIndex = 0;
    if (hasAlpha) hash.push(round(15 * aDc) | (round(15 * aScale) << 4));
    for (const ac of hasAlpha ? [lAc, pAc, qAc, aAc] : [lAc, pAc, qAc]) {
      for (const f of ac) {
        const k = acStart + (acIndex >> 1);
        hash[k] = (hash[k] || 0) | (round(15 * f) << ((acIndex++ & 1) << 2));
      }
    }
    return new Uint8Array(hash);
  }

  function aspect(hash) {
    const header = hash[3];
    const hasAlpha = hash[2] & 0x80;
    const isLandscape = hash[4] & 0x80;
    const lx = isLandscape ? (hasAlpha ? 5 : 7) : header & 7;
    const ly = isLandscape ? header & 7 : (hasAlpha ? 5 : 7);
    return lx / ly;
  }

  function decode(hash) {
    const header24 = hash[0] | (hash[1] << 8) | (hash[2] << 16);
    const header16 = hash[3] | (hash[4] << 8);
    const lDc = (header24 & 63) / 63;
    const pDc = ((header24 >> 6) & 63) / 31.5 - 1;
    const qDc = ((header24 >> 12) & 63) / 31.5 - 1;
    const lScale = ((header24 >> 18) & 31) / 31;
    const hasAlpha = header24 >> 23;
    const pScale = ((header16 >> 3) & 63) / 63;
    const qScale = ((header16 >> 9) & 63) / 63;
    const isLandscape = header16 >> 15;
    const lx = max(3, isLandscape ? (hasAlpha ? 5 : 7) : header16 & 7);
    const ly = max(3, isLandscape ? header16 & 7 : (hasAlpha ? 5 : 7));
    const aDc = hasAlpha ? (hash[5] & 15) / 15 : 1;
    const aScale = (hash[5] >> 4) / 15;
    const acStart = hasAlpha ? 6 : 5;
    let acIndex = 0;
    const decodeChannel = (nx, ny, scale) => {
      const ac = [];
      for (let cy = 0; cy < ny; cy++)
        for (let cx = cy ? 0 : 1; cx * ny < nx * (ny - cy); cx++)
          ac.push((((hash[acStart + (acIndex >> 1)] >> ((acIndex++ & 1) << 2)) & 15) / 7.5 - 1) * scale);
      return ac;
    };
    const lAc = decodeChannel(lx, ly, lScale);
    const pAc = decodeChannel(3, 3, pScale * 1.25);
    const qAc = decodeChannel(3, 3, qScale * 1.25);
    const aAc = hasAlpha && decodeChannel(5, 5, aScale);
    const ratio = aspect(hash);
    const w = round(ratio > 1 ? 32 : 32 * ratio);
    const h = round(ratio > 1 ? 32 / ratio : 32);
    const rgba = new Uint8ClampedArray(w * h * 4);
    const fx = [], fy = [];
    for (let y = 0, i = 0; y < h; y++) {
      for (let x = 0; x < w; x++, i += 4) {
        let l = lDc, p = pDc, q = qDc, a = aDc;
        for (let cx = 0, n = max(lx, hasAlpha ? 5 : 3); cx < n; cx++) fx[cx] = cos(PI / w * (x + 0.5) * cx);
        for (let cy = 0, n = max(ly, hasAlpha ? 5 : 3); cy < n; cy++) fy[cy] = cos(PI / h * (y + 0.5) * cy);
        for (let cy = 0, j = 0; cy < ly; cy++)
          for (let cx = cy ? 0 : 1, fy2 = fy[cy] * 2; cx * ly < lx * (ly - cy); cx++, j++) l += lAc[j] * fx[cx] * fy2;
        for (let cy = 0, j = 0; cy < 3; cy++)
          for (let cx = cy ? 0 : 1, fy2 = fy[cy] * 2; cx < 3 - cy; cx++, j++) { const f = fx[cx] * fy2; p += pAc[j] * f; q += qAc[j] * f; }
        if (hasAlpha)
          for (let cy = 0, j = 0; cy < 5; cy++)
            for (let cx = cy ? 0 : 1, fy2 = fy[cy] * 2; cx < 5 - cy; cx++, j++) a += aAc[j] * fx[cx] * fy2;
        const b = l - 2 / 3 * p;
        const r = (3 * l - b + q) / 2;
        const g = r - q;
        rgba[i] = max(0, 255 * min(1, r)); rgba[i + 1] = max(0, 255 * min(1, g));
        rgba[i + 2] = max(0, 255 * min(1, b)); rgba[i + 3] = max(0, 255 * min(1, a));
      }
    }
    return { w, h, rgba };
  }

  function toBase64(bytes) { return btoa(String.fromCharCode.apply(null, Array.from(bytes))); }
  function fromBase64(text) { return Uint8Array.from(atob(String(text || '')), c => c.charCodeAt(0)); }
  const cache = new Map();
  function toDataURL(b64) {
    if (!b64) return null;
    if (cache.has(b64)) return cache.get(b64);
    let url = null;
    try {
      const { w, h, rgba } = decode(fromBase64(b64));
      const canvas = document.createElement('canvas');
      canvas.width = w; canvas.height = h;
      canvas.getContext('2d').putImageData(new ImageData(rgba, w, h), 0, 0);
      url = canvas.toDataURL('image/png');
    } catch (e) { url = null; }
    if (cache.size > 500) cache.delete(cache.keys().next().value);
    cache.set(b64, url);
    return url;
  }
  return { encode, decode, aspect, toBase64, fromBase64, toDataURL };
})();
