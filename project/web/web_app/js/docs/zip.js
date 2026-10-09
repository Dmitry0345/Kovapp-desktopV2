/* Чтение ZIP и общих частей форматов Office (xlsx, docx, pptx) прямо в браузере */
(function (root) {
  const Kov = root.Kov = root.Kov || {};

  // ── Пределы: защита от «бомб» ──
  const LIMITS = { entries: 20000, total: 512 * 1024 * 1024, entry: 256 * 1024 * 1024, ratio: 1000 };

  class Zip {
    constructor(buf) {
      this.u8 = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
      this.dv = new DataView(this.u8.buffer, this.u8.byteOffset, this.u8.byteLength);
      this.entries = new Map();
      this.spent = 0;
      this.parse();
    }
    parse() {
      const u = this.u8, dv = this.dv;
      let eocd = -1;
      for (let i = u.length - 22; i >= Math.max(0, u.length - 65557); i--) if (dv.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
      if (eocd < 0) throw new Error('Файл повреждён или это не документ Office');
      let count = dv.getUint16(eocd + 10, true), cd = dv.getUint32(eocd + 16, true);
      if (count === 0xffff || cd === 0xffffffff) {
        const loc = eocd - 20;
        if (loc >= 0 && dv.getUint32(loc, true) === 0x07064b50) {
          const z = Number(dv.getBigUint64(loc + 8, true));
          if (dv.getUint32(z, true) === 0x06064b50) { count = Number(dv.getBigUint64(z + 32, true)); cd = Number(dv.getBigUint64(z + 48, true)); }
        }
      }
      if (count > LIMITS.entries) throw new Error('В архиве слишком много частей');
      const dec = new TextDecoder();
      let p = cd;
      for (let n = 0; n < count; n++) {
        if (p + 46 > u.length || dv.getUint32(p, true) !== 0x02014b50) throw new Error('Файл повреждён');
        const method = dv.getUint16(p + 10, true);
        let csize = dv.getUint32(p + 20, true), size = dv.getUint32(p + 24, true), off = dv.getUint32(p + 42, true);
        const nlen = dv.getUint16(p + 28, true), xlen = dv.getUint16(p + 30, true), clen = dv.getUint16(p + 32, true);
        const name = dec.decode(u.subarray(p + 46, p + 46 + nlen)).replace(/\\/g, '/');
        let x = p + 46 + nlen;
        const xe = x + xlen;
        while (x + 4 <= xe) {
          const id = dv.getUint16(x, true), sz = dv.getUint16(x + 2, true);
          if (id === 1) {
            let q = x + 4;
            if (size === 0xffffffff) { size = Number(dv.getBigUint64(q, true)); q += 8; }
            if (csize === 0xffffffff) { csize = Number(dv.getBigUint64(q, true)); q += 8; }
            if (off === 0xffffffff) off = Number(dv.getBigUint64(q, true));
          }
          x += 4 + sz;
        }
        this.entries.set(name, { method, csize, size, off });
        p = xe + clen;
      }
    }
    has(name) { return this.entries.has(name); }
    names() { return [...this.entries.keys()]; }
    async bytes(name) {
      const e = this.entries.get(name);
      if (!e) return null;
      if (e.size > LIMITS.entry || (e.size > (1 << 20) && e.size / Math.max(1, e.csize) > LIMITS.ratio)) throw new Error('Подозрительно сильно сжатая часть: ' + name);
      this.spent += e.size;
      if (this.spent > LIMITS.total) throw new Error('Документ распаковывается в слишком большой объём');
      const dv = this.dv;
      if (dv.getUint32(e.off, true) !== 0x04034b50) throw new Error('Файл повреждён');
      const start = e.off + 30 + dv.getUint16(e.off + 26, true) + dv.getUint16(e.off + 28, true);
      const raw = this.u8.subarray(start, start + e.csize);
      if (e.method === 0) return raw;
      if (e.method !== 8) throw new Error('Неизвестный способ сжатия');
      // старые Safari и Firefox: своя распаковка из gzip.js
      if (typeof DecompressionStream === 'undefined') {
        if (Kov.Gzip) return Kov.Gzip.inflateRaw(raw, e.size + 4096);
        throw new Error('Браузер не умеет распаковывать документы');
      }
      const reader = new Blob([raw]).stream().pipeThrough(new DecompressionStream('deflate-raw')).getReader();
      const parts = [];
      let got = 0;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        got += value.length;
        // заявленному размеру не верим: он мог быть занижен нарочно
        if (got > e.size + 4096) { reader.cancel(); throw new Error('Размер части не совпадает с заявленным: ' + name); }
        parts.push(value);
      }
      const out = new Uint8Array(got);
      let o = 0;
      for (const v of parts) { out.set(v, o); o += v.length; }
      return out;
    }
    async text(name) { const b = await this.bytes(name); return b ? new TextDecoder().decode(b) : null; }
    async xml(name) {
      const t = await this.text(name);
      if (t == null) return null;
      const doc = new DOMParser().parseFromString(t, 'application/xml');
      if (doc.getElementsByTagName('parsererror').length) throw new Error('Не удалось разобрать ' + name);
      return doc;
    }
    async blobUrl(name, type) {
      const b = await this.bytes(name);
      if (!b) return null;
      const url = URL.createObjectURL(new Blob([b], { type: type || mimeOf(name) }));
      (this.urls || (this.urls = [])).push(url);
      return url;
    }
    release() { (this.urls || []).forEach(u => URL.revokeObjectURL(u)); this.urls = []; }
  }

  function mimeOf(name) {
    const e = name.toLowerCase().split('.').pop();
    return { png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif', bmp: 'image/bmp', svg: 'image/svg+xml', webp: 'image/webp', tif: 'image/tiff', tiff: 'image/tiff' }[e] || 'application/octet-stream';
  }

  // ── XML: поиск по локальному имени, без привязки к префиксам ──
  const kids = (el, n) => { const out = []; if (el) for (const c of el.children) if (c.localName === n) out.push(c); return out; };
  const kid = (el, n) => { if (el) for (const c of el.children) if (c.localName === n) return c; return null; };
  const path = (el, ...names) => { for (const n of names) el = kid(el, n); return el; };
  const att = (el, n) => { if (!el || !el.attributes) return null; for (const a of el.attributes) if (a.localName === n) return a.value; return null; };
  const all = (el, n) => el ? Array.from(el.getElementsByTagNameNS('*', n)) : [];
  const flag = el => !!el && !['0', 'false', 'off'].includes(att(el, 'val'));

  function resolve(base, target) {
    if (!target) return '';
    if (target.startsWith('/')) return target.slice(1);
    const parts = base.split('/');
    parts.pop();
    for (const seg of target.split('/')) { if (seg === '..') parts.pop(); else if (seg && seg !== '.') parts.push(seg); }
    return parts.join('/');
  }
  async function rels(zip, part) {
    const dir = part.split('/');
    const file = dir.pop();
    const doc = await zip.xml(dir.concat('_rels', file + '.rels').join('/'));
    const map = {};
    if (doc) for (const r of all(doc, 'Relationship')) {
      const external = att(r, 'TargetMode') === 'External';
      map[att(r, 'Id')] = { target: external ? att(r, 'Target') : resolve(part, att(r, 'Target')), type: (att(r, 'Type') || '').split('/').pop(), external };
    }
    return map;
  }
  const byType = (map, type) => Object.values(map).find(r => r.type === type) || null;

  // ── Цвета ──
  function hexRgb(hex) { const n = parseInt(hex, 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
  function rgbHex([r, g, b]) { const c = v => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0'); return '#' + c(r) + c(g) + c(b); }
  function toHsl([r, g, b]) {
    r /= 255; g /= 255; b /= 255;
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2;
    if (mx === mn) return [0, 0, l];
    const d = mx - mn, s = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
    const h = mx === r ? (g - b) / d + (g < b ? 6 : 0) : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
    return [h / 6, s, l];
  }
  function fromHsl([h, s, l]) {
    if (!s) return [l * 255, l * 255, l * 255];
    const q = l < 0.5 ? l * (1 + s) : l + s - l * s, p = 2 * l - q;
    const f = t => { t = (t + 1) % 1; return t < 1 / 6 ? p + (q - p) * 6 * t : t < 1 / 2 ? q : t < 2 / 3 ? p + (q - p) * (2 / 3 - t) * 6 : p; };
    return [f(h + 1 / 3) * 255, f(h) * 255, f(h - 1 / 3) * 255];
  }
  function tint(hex, t) {
    if (!t) return hex;
    const [h, s, l] = toHsl(hexRgb(hex.slice(1)));
    return rgbHex(fromHsl([h, s, t < 0 ? l * (1 + t) : l * (1 - t) + t]));
  }
  function lum(hex, mod, off) {
    const [h, s, l] = toHsl(hexRgb(hex.slice(1)));
    return rgbHex(fromHsl([h, s, Math.max(0, Math.min(1, l * mod + off))]));
  }
  function mix(hex, other, share) {
    const a = hexRgb(hex.slice(1)), b = hexRgb(other.slice(1));
    return rgbHex(a.map((v, i) => v * share + b[i] * (1 - share)));
  }

  Kov.Zip = { open: buf => new Zip(buf), LIMITS, mimeOf,
    x: { kids, kid, path, att, all, flag, resolve, rels, byType },
    color: { hexRgb, rgbHex, tint, lum, mix } };
})(typeof self !== 'undefined' ? self : this);
