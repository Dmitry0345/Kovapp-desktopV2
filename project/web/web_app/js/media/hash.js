window.Kov = window.Kov || {};

// ── Отпечаток содержимого: SHA-256 от хешей кусков по 4 МиБ и размера ──
Kov.FileHash = (function () {
  const LEAF = 4 * 1024 * 1024;
  const K = new Uint32Array([0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2]);

  function sha256Soft(bytes) {
    const H = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
    const len = bytes.length;
    const padded = new Uint8Array(((len + 9 + 63) >> 6) << 6);
    padded.set(bytes); padded[len] = 0x80;
    const view = new DataView(padded.buffer);
    view.setUint32(padded.length - 8, Math.floor(len / 0x20000000));
    view.setUint32(padded.length - 4, (len << 3) >>> 0);
    const W = new Uint32Array(64);
    for (let off = 0; off < padded.length; off += 64) {
      for (let i = 0; i < 16; i++) W[i] = view.getUint32(off + i * 4);
      for (let i = 16; i < 64; i++) {
        const a = W[i - 15], b = W[i - 2];
        const s0 = ((a >>> 7) | (a << 25)) ^ ((a >>> 18) | (a << 14)) ^ (a >>> 3);
        const s1 = ((b >>> 17) | (b << 15)) ^ ((b >>> 19) | (b << 13)) ^ (b >>> 10);
        W[i] = (W[i - 16] + s0 + W[i - 7] + s1) >>> 0;
      }
      let a = H[0], b = H[1], c = H[2], d = H[3], e = H[4], f = H[5], g = H[6], h = H[7];
      for (let i = 0; i < 64; i++) {
        const S1 = ((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7));
        const t1 = (h + S1 + ((e & f) ^ (~e & g)) + K[i] + W[i]) >>> 0;
        const S0 = ((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10));
        const t2 = (S0 + ((a & b) ^ (a & c) ^ (b & c))) >>> 0;
        h = g; g = f; f = e; e = (d + t1) >>> 0; d = c; c = b; b = a; a = (t1 + t2) >>> 0;
      }
      H[0] = (H[0] + a) >>> 0; H[1] = (H[1] + b) >>> 0; H[2] = (H[2] + c) >>> 0; H[3] = (H[3] + d) >>> 0;
      H[4] = (H[4] + e) >>> 0; H[5] = (H[5] + f) >>> 0; H[6] = (H[6] + g) >>> 0; H[7] = (H[7] + h) >>> 0;
    }
    const out = new Uint8Array(32);
    const ov = new DataView(out.buffer);
    for (let i = 0; i < 8; i++) ov.setUint32(i * 4, H[i]);
    return out;
  }

  const fast = () => !!(window.crypto && crypto.subtle && typeof crypto.subtle.digest === 'function');
  async function sha256(bytes) {
    if (fast()) return new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
    return sha256Soft(bytes);
  }
  const hex = bytes => Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');

  async function tree(blob, onProgress) {
    const leaves = [];
    for (let off = 0; off < blob.size || (off === 0 && blob.size === 0); off += LEAF) {
      if (blob.size === 0) break;
      const part = new Uint8Array(await blob.slice(off, Math.min(blob.size, off + LEAF)).arrayBuffer());
      leaves.push(await sha256(part));
      if (onProgress) onProgress(Math.min(1, (off + LEAF) / blob.size));
    }
    const all = new Uint8Array(leaves.length * 32 + 8);
    leaves.forEach((d, i) => all.set(d, i * 32));
    new DataView(all.buffer).setBigUint64(leaves.length * 32, BigInt(blob.size));
    return hex(await sha256(all));
  }

  async function range(blob, offset, length) {
    return hex(await sha256(new Uint8Array(await blob.slice(offset, offset + length).arrayBuffer())));
  }

  function worthIt(size) { return fast() ? true : size <= 64 * 1024 * 1024; }

  // образец (как blobs.sample_of и FileHasher.sample): 9 участков по 64 КБ от начала до конца,
  // перед каждым — его смещение (8 байт), в конце — размер; ≈0,6 МБ чтения вместо всего файла
  const SAMPLE_WINDOW = 64 * 1024, SAMPLE_WINDOWS = 9;
  async function sample(blob) {
    const size = blob.size, parts = [];
    const be64 = n => { const b = new Uint8Array(8); new DataView(b.buffer).setBigUint64(0, BigInt(n)); return b; };
    for (let i = 0; i < SAMPLE_WINDOWS; i++) {
      const pos = size <= SAMPLE_WINDOW ? 0 : Math.floor((size - SAMPLE_WINDOW) * i / (SAMPLE_WINDOWS - 1));
      const len = Math.min(SAMPLE_WINDOW, size - pos);
      parts.push(be64(pos), new Uint8Array(await blob.slice(pos, pos + len).arrayBuffer()));
    }
    parts.push(be64(size));
    const all = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
    let at = 0;
    for (const p of parts) { all.set(p, at); at += p.length; }
    return hex(await sha256(all));
  }

  // запомненные отпечатки: имя + размер + дата изменения + образец → отпечаток;
  // без даты изменения не запоминаем — иначе можно спутать два разных файла
  const CACHE_KEY = 'kov.fileHash.v1', CACHE_MAX = 300;
  const modified = file => file.kovModified || file.lastModified || 0;   // у файлов из галереи — дата из галереи
  const cacheKey = (file, smp) => [file.name || '', file.size, modified(file), smp].join('|');
  function readCache() { try { return JSON.parse(localStorage.getItem(CACHE_KEY) || '{}') || {}; } catch (e) { return {}; } }
  function cached(file, smp) {
    if (!smp || !modified(file)) return null;
    const c = readCache()[cacheKey(file, smp)];
    return c && /^[0-9a-f]{64}$/.test(c.t) ? c.t : null;
  }
  function remember(file, smp, treeHex) {
    if (!smp || !treeHex || !modified(file)) return;
    try {
      const c = readCache();
      c[cacheKey(file, smp)] = { t: treeHex, at: Date.now() };
      const keys = Object.keys(c);
      if (keys.length > CACHE_MAX) keys.sort((a, b) => c[a].at - c[b].at).slice(0, keys.length - CACHE_MAX).forEach(k => delete c[k]);
      localStorage.setItem(CACHE_KEY, JSON.stringify(c));
    } catch (e) { /* хранилище недоступно — просто посчитаем заново */ }
  }
  return { tree, range, sample, cached, remember, sha256, sha256Soft, hex, worthIt, fast, LEAF };
})();
