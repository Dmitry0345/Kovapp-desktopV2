window.Kov = window.Kov || {};

// ── Сжатие фото и миниатюры на устройстве ──
Kov.ImagePrep = (function () {
  const MAX_SIDE = 3200, THUMB_SIDE = 320;
  const WEBP_Q = 0.8, JPEG_Q = 0.82, THUMB_Q = 0.6;
  const PIXEL_LIMIT = 100e6;
  let webp = null;

  function canWebp() {
    if (webp === null) {
      try {
        const c = document.createElement('canvas'); c.width = c.height = 2;
        webp = c.toDataURL('image/webp', 0.8).startsWith('data:image/webp');
      } catch (e) { webp = false; }
    }
    return webp;
  }

  // ── Размеры по заголовку, без распаковки ──
  function dims(b) {
    const u16 = (i, le) => le ? b[i] | (b[i + 1] << 8) : (b[i] << 8) | b[i + 1];
    const u32 = i => ((b[i] << 24) | (b[i + 1] << 16) | (b[i + 2] << 8) | b[i + 3]) >>> 0;
    if (b[0] === 0x89 && b[1] === 0x50 && b[2] === 0x4E && b[3] === 0x47) return { type: 'png', width: u32(16), height: u32(20), alpha: b[25] === 4 || b[25] === 6 || b[25] === 3 };
    if (b[0] === 0x47 && b[1] === 0x49 && b[2] === 0x46) return { type: 'gif', width: u16(6, true), height: u16(8, true), alpha: true };
    if (b[0] === 0x52 && b[1] === 0x49 && b[8] === 0x57 && b[9] === 0x45) {
      const kind = String.fromCharCode(b[12], b[13], b[14], b[15]);
      if (kind === 'VP8 ') return { type: 'webp', width: u16(26, true) & 0x3FFF, height: u16(28, true) & 0x3FFF, alpha: false };
      if (kind === 'VP8L') return { type: 'webp', width: 1 + (((b[22] & 0x3F) << 8) | b[21]), height: 1 + (((b[24] & 0x0F) << 10) | (b[23] << 2) | ((b[22] & 0xC0) >> 6)), alpha: true };
      if (kind === 'VP8X') return { type: 'webp', width: 1 + (b[24] | (b[25] << 8) | (b[26] << 16)), height: 1 + (b[27] | (b[28] << 8) | (b[29] << 16)), alpha: !!(b[20] & 0x10) };
    }
    if (b[0] === 0xFF && b[1] === 0xD8) {
      let i = 2, orientation = 1;
      while (i + 9 < b.length) {
        if (b[i] !== 0xFF) { i++; continue; }
        const m = b[i + 1];
        if (m === 0xD8 || m === 0x01 || (m >= 0xD0 && m <= 0xD7) || m === 0xFF) { i += (m === 0xFF ? 1 : 2); continue; }
        const len = u16(i + 2);
        if (m === 0xE1 && b[i + 4] === 0x45 && b[i + 5] === 0x78 && b[i + 6] === 0x69 && b[i + 7] === 0x66) orientation = exifOrientation(b, i + 10) || orientation;
        if ([0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF].includes(m)) return { type: 'jpeg', width: u16(i + 7), height: u16(i + 5), alpha: false, orientation };
        i += 2 + len;
      }
      return { type: 'jpeg', width: 0, height: 0, alpha: false, orientation };
    }
    if (b[0] === 0x42 && b[1] === 0x4D) return { type: 'bmp', width: Math.abs(b[18] | (b[19] << 8) | (b[20] << 16) | (b[21] << 24)), height: Math.abs(b[22] | (b[23] << 8) | (b[24] << 16) | (b[25] << 24)), alpha: false };
    return null;
  }

  function exifOrientation(b, t) {
    if (t + 8 > b.length) return 0;
    const le = b[t] === 0x49;
    const u16 = i => le ? b[i] | (b[i + 1] << 8) : (b[i] << 8) | b[i + 1];
    const u32 = i => le ? (b[i] | (b[i + 1] << 8) | (b[i + 2] << 16) | (b[i + 3] << 24)) >>> 0 : ((b[i] << 24) | (b[i + 1] << 16) | (b[i + 2] << 8) | b[i + 3]) >>> 0;
    const ifd = t + u32(t + 4);
    if (ifd + 2 > b.length) return 0;
    const n = u16(ifd);
    for (let k = 0; k < n; k++) {
      const e = ifd + 2 + k * 12;
      if (e + 12 > b.length) return 0;
      if (u16(e) === 0x0112) return u16(e + 8);
    }
    return 0;
  }

  async function probe(file) {
    const head = new Uint8Array(await file.slice(0, 256 * 1024).arrayBuffer());
    return dims(head);
  }

  // ── Распаковка с учётом поворота по EXIF ──
  async function decode(file, targetW, targetH) {
    if (window.createImageBitmap) {
      if (targetW && targetH) {
        try {
          const bmp = await createImageBitmap(file, { imageOrientation: 'from-image', resizeWidth: targetW, resizeHeight: targetH, resizeQuality: 'high' });
          if (bmp.width === targetW && bmp.height === targetH) return { source: bmp, width: bmp.width, height: bmp.height, scaled: true, close: () => bmp.close() };
          bmp.close();
        } catch (e) { /* без масштабирования при распаковке */ }
      }
      try {
        const bmp = await createImageBitmap(file, { imageOrientation: 'from-image' });
        return { source: bmp, width: bmp.width, height: bmp.height, scaled: false, close: () => bmp.close() };
      } catch (e) { /* через <img> */ }
    }
    const url = URL.createObjectURL(file);
    try {
      const img = new Image();
      img.decoding = 'async';
      img.src = url;
      await img.decode();
      return { source: img, width: img.naturalWidth, height: img.naturalHeight, scaled: false, close: () => {} };
    } finally { setTimeout(() => URL.revokeObjectURL(url), 1000); }
  }

  function fit(w, h, side) {
    if (Math.max(w, h) <= side) return [w, h];
    const r = side / Math.max(w, h);
    return [Math.max(1, Math.round(w * r)), Math.max(1, Math.round(h * r))];
  }

  function release(canvas) { try { canvas.width = 1; canvas.height = 1; } catch (e) { /* */ } }

  function draw(source, sw, sh, tw, th, opaque, quality) {
    let src = source, w = sw, h = sh;
    const temps = [];
    while (w / tw > 2.2 && h / th > 2.2) {
      const c = document.createElement('canvas');
      c.width = Math.max(tw, Math.round(w / 2)); c.height = Math.max(th, Math.round(h / 2));
      const x = c.getContext('2d');
      x.imageSmoothingEnabled = true; x.imageSmoothingQuality = 'high';
      x.drawImage(src, 0, 0, c.width, c.height);
      temps.push(c); src = c; w = c.width; h = c.height;
    }
    const canvas = document.createElement('canvas');
    canvas.width = tw; canvas.height = th;
    const ctx = canvas.getContext('2d', { alpha: !opaque });
    if (opaque) { ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, tw, th); }
    ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = quality || 'high';
    ctx.drawImage(src, 0, 0, tw, th);
    temps.forEach(release);
    return canvas;
  }

  function blobOf(canvas, type, q) {
    return new Promise(resolve => canvas.toBlob(b => resolve(b), type, q));
  }

  function hasAlpha(canvas) {
    const d = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
    for (let i = 3; i < d.length; i += 4) if (d[i] < 250) return true;
    return false;
  }

  async function placeholder(source, sw, sh) {
    const [tw, th] = fit(sw, sh, 100);
    const canvas = draw(source, sw, sh, tw, th, false, 'medium');
    const data = canvas.getContext('2d').getImageData(0, 0, tw, th).data;
    const hash = Kov.ThumbHash.toBase64(Kov.ThumbHash.encode(tw, th, data));
    return { hash, alpha: hasAlpha(canvas), canvas };
  }

  async function thumbnail(source, sw, sh) {
    const [tw, th] = fit(sw, sh, THUMB_SIDE);
    const canvas = draw(source, sw, sh, tw, th, true, 'medium');
    const blob = await blobOf(canvas, 'image/jpeg', THUMB_Q);
    release(canvas);
    return blob && blob.type === 'image/jpeg' ? { blob, width: tw, height: th } : null;
  }

  function renamed(name, ext) {
    const base = String(name || 'photo').replace(/\.[^.\/\\]+$/, '') || 'photo';
    return base + ext;
  }

  // ── Подготовка одного фото ──
  async function prepare(file, opts) {
    opts = opts || {};
    const maxSide = opts.maxSide || MAX_SIDE;
    const result = { kind: 'image', file, changed: false, width: 0, height: 0, thumb: null, thumbHash: '', reason: '' };
    const name = String(file.name || '').toLowerCase();
    if (/\.svg$/.test(name) || file.type === 'image/svg+xml') { result.reason = 'svg'; return result; }
    let info = null;
    try { info = await probe(file); } catch (e) { info = null; }
    if (info && info.width * info.height > PIXEL_LIMIT) { result.reason = 'too-large'; return result; }
    const turned = info && info.orientation >= 5;
    const natural = info && info.width && info.height ? (turned ? [info.height, info.width] : [info.width, info.height]) : null;
    const target = natural ? fit(natural[0], natural[1], maxSide) : [0, 0];
    let pic;
    const shrink = natural && (target[0] !== natural[0] || target[1] !== natural[1]);
    try { pic = await decode(file, shrink ? target[0] : 0, shrink ? target[1] : 0); } catch (e) { result.reason = 'decode'; return result; }
    try {
      if (pic.width * pic.height > PIXEL_LIMIT) { result.reason = 'too-large'; return result; }
      const ph = await placeholder(pic.source, pic.width, pic.height);
      result.thumbHash = ph.hash;
      release(ph.canvas);
      const th = await thumbnail(pic.source, pic.width, pic.height);
      if (th) { result.thumb = th.blob; result.thumbWidth = th.width; result.thumbHeight = th.height; }
      const [w, h] = fit(pic.width, pic.height, maxSide);
      result.width = w; result.height = h;
      const animated = info && info.type === 'gif';
      if (animated) { result.width = pic.width; result.height = pic.height; result.reason = 'gif'; return result; }
      const resized = w !== pic.width || h !== pic.height || pic.scaled;
      const alpha = ph.alpha;
      const native = canWebp() && !opts.forceEncoder;
      const encoder = !!Kov.WebPEncoder;
      let type = (native || encoder) && !opts.noWebp ? 'image/webp' : 'image/jpeg';
      let quality = type === 'image/webp' ? WEBP_Q : JPEG_Q;
      if (alpha && type === 'image/jpeg') {
        if (!resized) { result.reason = 'alpha'; return result; }
        type = 'image/png'; quality = undefined;
      }
      const light = info && (info.type === 'jpeg' || info.type === 'webp') && file.size / Math.max(1, pic.width * pic.height) <= 0.35;
      if (!resized && (file.size <= 300 * 1024 || light)) { result.width = pic.width; result.height = pic.height; result.reason = 'already-compact'; return result; }
      const canvas = draw(pic.source, pic.width, pic.height, w, h, type === 'image/jpeg', 'high');
      const blob = type === 'image/webp' && !native
        ? await Kov.WebPEncoder.encodeImageData(canvas.getContext('2d').getImageData(0, 0, w, h), { quality })
        : await blobOf(canvas, type, quality);
      release(canvas);
      if (!blob || blob.type !== type) { result.reason = 'encode'; return result; }
      if (!resized && blob.size >= file.size * 0.95) { result.width = pic.width; result.height = pic.height; result.reason = 'not-smaller'; return result; }
      const ext = type === 'image/webp' ? '.webp' : type === 'image/png' ? '.png' : '.jpg';
      result.file = new File([blob], renamed(file.name, ext), { type, lastModified: Date.now() });
      result.changed = true;
      result.mime = type;
      return result;
    } finally { pic.close(); }
  }

  return { prepare, probe, dims, canWebp, decode, thumbnail, placeholder, fit, MAX_SIDE, THUMB_SIDE, WEBP_Q };
})();
