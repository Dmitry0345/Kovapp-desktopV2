/* gzip на устройстве: сжатие уровня 9 (как zlib -9) в фоновом потоке и распаковка.
 * Распаковку делает сам браузер (Content-Encoding, DecompressionStream); своя — для старых браузеров. */
(function (root) {
  const Kov = root.Kov = root.Kov || {};

  // ── Таблицы DEFLATE и CRC32 ──
  const CRC = new Int32Array(256);
  for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; CRC[n] = c; }
  function crc32(buf, prev) { let c = ~(prev || 0); for (let i = 0; i < buf.length; i++) c = CRC[(c ^ buf[i]) & 255] ^ (c >>> 8); return ~c >>> 0; }
  const LBASE = [3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258];
  const LEXT = [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0];
  const DBASE = [1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769, 1025, 1537, 2049, 3073, 4097, 6145, 8193, 12289, 16385, 24577];
  const DEXT = [0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13];
  const LCODE = new Uint8Array(259), DCODE = new Uint8Array(32769);
  for (let c = 0; c < 29; c++) for (let l = LBASE[c]; l < (c < 28 ? LBASE[c + 1] : 259); l++) LCODE[l] = c;
  for (let c = 0; c < 30; c++) for (let d = DBASE[c]; d < (c < 29 ? DBASE[c + 1] : 32769); d++) DCODE[d] = c;
  const CL_ORDER = [16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15];
  const FIXED_L = new Uint8Array(288).map((_, i) => i < 144 ? 8 : i < 256 ? 9 : i < 280 ? 7 : 8), FIXED_D = new Uint8Array(30).fill(5);

  // ── Коды Хаффмана ──
  function lengths(freq, limit) {
    const len = new Uint8Array(freq.length), syms = [];
    for (let i = 0; i < freq.length; i++) if (freq[i]) syms.push(i);
    if (syms.length < 2) { if (syms.length) len[syms[0]] = 1; return len; }
    const m = syms.length;
    for (let scale = 0; ; scale++) {
      const w = syms.map(s => scale ? Math.max(1, freq[s] >> scale) : freq[s]);
      const order = syms.map((s, i) => i).sort((a, b) => w[a] - w[b] || a - b);
      const weight = new Float64Array(2 * m), parent = new Int32Array(2 * m), depth = new Uint8Array(2 * m);
      for (let i = 0; i < m; i++) weight[i] = w[order[i]];
      let leaf = 0, inner = m;
      for (let next = m; next < 2 * m - 1; next++) {
        const a = leaf < m && (inner >= next || weight[leaf] <= weight[inner]) ? leaf++ : inner++;
        const b = leaf < m && (inner >= next || weight[leaf] <= weight[inner]) ? leaf++ : inner++;
        weight[next] = weight[a] + weight[b]; parent[a] = parent[b] = next;
      }
      let max = 0;
      for (let i = 2 * m - 3; i >= 0; i--) { depth[i] = depth[parent[i]] + 1; if (i < m && depth[i] > max) max = depth[i]; }
      if (max <= limit) { for (let i = 0; i < m; i++) len[syms[order[i]]] = depth[i]; return len; }
    }
  }
  function codes(len) {
    const bl = new Uint16Array(16), next = new Uint16Array(16), out = new Uint16Array(len.length);
    for (let i = 0; i < len.length; i++) bl[len[i]]++;
    bl[0] = 0;
    for (let b = 1, code = 0; b < 16; b++) { code = (code + bl[b - 1]) << 1; next[b] = code; }
    for (let i = 0; i < len.length; i++) { const l = len[i]; if (!l) continue; let c = next[l]++, r = 0; for (let k = 0; k < l; k++) { r = (r << 1) | (c & 1); c >>= 1; } out[i] = r; }
    return out;
  }
  const FIXED_LC = codes(FIXED_L), FIXED_DC = codes(FIXED_D);

  class Bits {
    constructor() { this.buf = new Uint8Array(1 << 16); this.pos = 0; this.acc = 0; this.n = 0; this.parts = []; }
    put(v, n) {
      this.acc |= v << this.n; this.n += n;
      while (this.n >= 8) { if (this.pos === this.buf.length) this.spill(); this.buf[this.pos++] = this.acc & 255; this.acc >>>= 8; this.n -= 8; }
    }
    align() { if (this.n > 0) this.put(0, 8 - this.n); }
    raw(u8) { for (let i = 0; i < u8.length; i++) { if (this.pos === this.buf.length) this.spill(); this.buf[this.pos++] = u8[i]; } }
    spill() { this.parts.push(this.buf.slice(0, this.pos)); this.pos = 0; }
    take() { this.spill(); const p = this.parts; this.parts = []; return p; }
  }

  // ── Сжатие: ленивый поиск совпадений, параметры zlib ──
  const WSIZE = 32768, WMASK = WSIZE - 1, HMASK = 32767, LOOK = 262, MAX_DIST = WSIZE - LOOK, TOO_FAR = 4096, BLOCK = 16383;
  const LEVELS = { 9: [32, 258, 258, 4096], 6: [8, 16, 128, 128] };
  class Deflater {
    constructor(level) {
      [this.good, this.lazy, this.nice, this.chain] = LEVELS[level] || LEVELS[9];
      this.head = new Int32Array(32768).fill(-1); this.prev = new Int32Array(WSIZE).fill(-1);
      this.data = new Uint8Array(0); this.base = 0; this.end = 0; this.pos = 0;
      this.matchLen = 2; this.matchPos = 0; this.avail = false;
      this.sl = new Uint16Array(BLOCK + 1); this.sd = new Uint16Array(BLOCK + 1); this.nsym = 0;
      this.lf = new Uint32Array(286); this.df = new Uint32Array(30); this.blockStart = 0;
      this.bits = new Bits(); this.crc = 0; this.size = 0;
    }
    push(chunk, last) {
      const keep = Math.max(this.base, Math.min(this.pos - WSIZE, this.blockStart));
      const d = new Uint8Array(this.end - keep + chunk.length);
      d.set(this.data.subarray(keep - this.base, this.end - this.base)); d.set(chunk, this.end - keep);
      this.data = d; this.base = keep; this.end += chunk.length;
      this.crc = crc32(chunk, this.crc); this.size += chunk.length;
      this.run(last);
      return this.bits.take();
    }
    lit(b) { this.sl[this.nsym] = b; this.sd[this.nsym++] = 0; this.lf[b]++; }
    match(len, dist) { this.sl[this.nsym] = len; this.sd[this.nsym++] = dist; this.lf[257 + LCODE[len]]++; this.df[DCODE[dist]]++; }
    insert(p) {
      const i = p - this.base, D = this.data;
      const h = ((D[i] << 10) ^ (D[i + 1] << 5) ^ D[i + 2]) & HMASK;
      const old = this.head[h]; this.prev[p & WMASK] = old; this.head[h] = p;
      return old;
    }
    longest(pos, cur, prevLen) {
      const D = this.data, b = this.base, s = pos - b, look = this.end - pos;
      const nice = Math.min(this.nice, look), maxLen = Math.min(258, look), lim = Math.max(0, pos - MAX_DIST);
      let chain = prevLen >= this.good ? this.chain >> 2 : this.chain, best = prevLen, bestPos = -1;
      let e1 = D[s + best - 1], e0 = D[s + best];
      const s0 = D[s], s1 = D[s + 1];
      do {
        const m = cur - b;
        if (D[m + best] === e0 && D[m + best - 1] === e1 && D[m] === s0 && D[m + 1] === s1) {
          let len = 2;
          while (len < maxLen && D[m + len] === D[s + len]) len++;
          if (len > best) { best = len; bestPos = cur; if (len >= nice) break; e1 = D[s + best - 1]; e0 = D[s + best]; }
        }
        cur = this.prev[cur & WMASK];
      } while (cur >= lim && --chain !== 0);
      this.matchPos = bestPos >= 0 ? bestPos : this.matchPos;
      return Math.min(best, look);
    }
    run(flush) {
      const stop = flush ? this.end : this.end - LOOK;
      let pos = this.pos;
      while (pos < stop) {
        const head = pos + 2 < this.end ? this.insert(pos) : -1;
        const prevLen = this.matchLen, prevMatch = this.matchPos;
        this.matchLen = 2;
        if (head >= 0 && prevLen < this.lazy && pos - head <= MAX_DIST) {
          this.matchLen = this.longest(pos, head, prevLen);
          if (this.matchLen === 3 && pos - this.matchPos > TOO_FAR) this.matchLen = 2;
        }
        if (prevLen >= 3 && this.matchLen <= prevLen) {
          this.match(prevLen, pos - 1 - prevMatch);
          for (let k = prevLen - 2; k > 0; k--) { pos++; if (pos + 2 < this.end) this.insert(pos); }
          this.avail = false; this.matchLen = 2; pos++;
          if (this.nsym >= BLOCK) this.block(false, pos);
        } else if (this.avail) {
          this.lit(this.data[pos - 1 - this.base]); pos++;
          if (this.nsym >= BLOCK) this.block(false, pos);
        } else { this.avail = true; pos++; }
      }
      this.pos = pos;
      if (flush) { if (this.avail) this.lit(this.data[pos - 1 - this.base]); this.avail = false; this.block(true, pos); this.bits.align(); }
    }
    block(final, next) {
      const lf = this.lf, df = this.df, n = this.nsym;
      lf[256]++;
      const lfx = Uint32Array.from(lf), dfx = Uint32Array.from(df);
      for (const f of [lfx, dfx]) { let nz = 0; for (let i = 0; i < f.length; i++) if (f[i]) nz++; for (let i = 0; nz < 2; i++) if (!f[i]) { f[i] = 1; nz++; } }
      const ll = lengths(lfx, 15), dl = lengths(dfx, 15);
      let hlit = 286; while (hlit > 257 && !ll[hlit - 1]) hlit--;
      let hdist = 30; while (hdist > 1 && !dl[hdist - 1]) hdist--;
      const all = [...ll.subarray(0, hlit), ...dl.subarray(0, hdist)], seq = [];
      for (let i = 0; i < all.length;) {
        const v = all[i]; let run = 1;
        while (i + run < all.length && all[i + run] === v) run++;
        let left = run;
        if (v === 0) { while (left >= 3) { const k = Math.min(left, 138); if (k >= 11) seq.push([18, k - 11, 7]); else seq.push([17, k - 3, 3]); left -= k; } }
        else { seq.push([v, 0, 0]); left--; while (left >= 3) { const k = Math.min(left, 6); seq.push([16, k - 3, 2]); left -= k; } }
        for (; left > 0; left--) seq.push([v, 0, 0]);
        i += run;
      }
      const cf = new Uint32Array(19); seq.forEach(s => cf[s[0]]++);
      const cl = lengths(cf, 7);
      let hclen = 19; while (hclen > 4 && !cl[CL_ORDER[hclen - 1]]) hclen--;
      let extra = 0; for (let c = 0; c < 29; c++) extra += lf[257 + c] * LEXT[c]; for (let c = 0; c < 30; c++) extra += df[c] * DEXT[c];
      let dyn = 17 + 3 * hclen + extra, fix = 3 + extra;
      seq.forEach(s => { dyn += cl[s[0]] + s[2]; });
      for (let i = 0; i < 286; i++) if (lf[i]) { dyn += lf[i] * ll[i]; fix += lf[i] * FIXED_L[i]; }
      for (let i = 0; i < 30; i++) if (df[i]) { dyn += df[i] * dl[i]; fix += df[i] * 5; }
      const rawLen = next - this.blockStart, canRaw = this.blockStart >= this.base;
      const stored = canRaw ? rawLen * 8 + 40 * Math.max(1, Math.ceil(rawLen / 65535)) + 8 : Infinity;
      const bw = this.bits;
      if (stored <= dyn && stored <= fix) {
        const raw = this.data.subarray(this.blockStart - this.base, next - this.base);
        let o = 0;
        do {
          const k = Math.min(65535, raw.length - o), fin = final && o + k >= raw.length;
          bw.put(fin ? 1 : 0, 1); bw.put(0, 2); bw.align();
          bw.put(k & 255, 8); bw.put(k >> 8, 8); bw.put(~k & 255, 8); bw.put((~k >> 8) & 255, 8);
          bw.raw(raw.subarray(o, o + k)); o += k;
        } while (o < raw.length);
      } else {
        const useFix = fix < dyn, L = useFix ? FIXED_L : ll, Dl = useFix ? FIXED_D : dl;
        const LC = useFix ? FIXED_LC : codes(ll), DC = useFix ? FIXED_DC : codes(dl);
        bw.put(final ? 1 : 0, 1); bw.put(useFix ? 1 : 2, 2);
        if (!useFix) {
          const cc = codes(cl);
          bw.put(hlit - 257, 5); bw.put(hdist - 1, 5); bw.put(hclen - 4, 4);
          for (let i = 0; i < hclen; i++) bw.put(cl[CL_ORDER[i]], 3);
          for (const [s, e, eb] of seq) { bw.put(cc[s], cl[s]); if (eb) bw.put(e, eb); }
        }
        const sl = this.sl, sd = this.sd;
        for (let i = 0; i < n; i++) {
          const d = sd[i], v = sl[i];
          if (!d) { bw.put(LC[v], L[v]); continue; }
          const c = LCODE[v]; bw.put(LC[257 + c], L[257 + c]); if (LEXT[c]) bw.put(v - LBASE[c], LEXT[c]);
          const dc = DCODE[d]; bw.put(DC[dc], Dl[dc]); if (DEXT[dc]) bw.put(d - DBASE[dc], DEXT[dc]);
        }
        bw.put(LC[256], L[256]);
      }
      this.nsym = 0; lf.fill(0); df.fill(0); this.blockStart = next;
    }
  }
  const HEADER = new Uint8Array([0x1f, 0x8b, 8, 0, 0, 0, 0, 0, 2, 255]);
  const trailer = (crc, size) => new Uint8Array([crc & 255, (crc >>> 8) & 255, (crc >>> 16) & 255, crc >>> 24, size & 255, (size >>> 8) & 255, (size >>> 16) & 255, (size >>> 24) & 255]);
  function gzipSync(u8, level) {
    const z = new Deflater(level || 9);
    return concat([HEADER, ...z.push(u8, true), trailer(z.crc, z.size)]);
  }
  function concat(parts) { let n = 0; parts.forEach(p => { n += p.length; }); const o = new Uint8Array(n); let k = 0; parts.forEach(p => { o.set(p, k); k += p.length; }); return o; }

  // ── Распаковка (для браузеров без DecompressionStream) ──
  function table(len) {
    let max = 0; for (let i = 0; i < len.length; i++) if (len[i] > max) max = len[i];
    const t = new Uint32Array(1 << max), c = codes(len);
    for (let s = 0; s < len.length; s++) { const l = len[s]; if (l) for (let j = c[s]; j < t.length; j += 1 << l) t[j] = (s << 4) | l; }
    return { t, max };
  }
  const FIXED_T = [table(FIXED_L), table(FIXED_D)];
  function inflateRaw(src, limit) {
    limit = limit || 1 << 30;
    let out = new Uint8Array(Math.min(limit, Math.max(1024, src.length * 4))), o = 0, pos = 0, acc = 0, n = 0;
    const need = k => { while (n < k) { acc |= (pos < src.length ? src[pos] : 0) << n; pos++; n += 8; if (pos > src.length + 4) throw new Error('Данные gzip оборваны'); } };
    const bits = k => { need(k); const v = acc & ((1 << k) - 1); acc >>>= k; n -= k; return v; };
    const sym = T => { need(T.max); const e = T.t[acc & ((1 << T.max) - 1)]; if (!e) throw new Error('Повреждённые данные'); acc >>>= e & 15; n -= e & 15; return e >>> 4; };
    const grow = k => { if (o + k <= out.length) return; if (o + k > limit) throw new Error('Распакованный объём больше допустимого'); const b = new Uint8Array(Math.min(limit, Math.max(o + k, out.length * 2))); b.set(out.subarray(0, o)); out = b; };
    let final = 0;
    while (!final) {
      final = bits(1);
      const type = bits(2);
      if (type === 0) {
        acc >>>= n & 7; n -= n & 7;
        const k = bits(16); bits(16);
        grow(k);
        for (let i = 0; i < k; i++) out[o++] = n >= 8 ? bits(8) : src[pos++];
        continue;
      }
      let LT, DT;
      if (type === 1) [LT, DT] = FIXED_T;
      else if (type === 2) {
        const hlit = bits(5) + 257, hdist = bits(5) + 1, hclen = bits(4) + 4;
        const cl = new Uint8Array(19);
        for (let i = 0; i < hclen; i++) cl[CL_ORDER[i]] = bits(3);
        const CT = table(cl), lens = new Uint8Array(hlit + hdist);
        for (let i = 0; i < lens.length;) {
          const s = sym(CT);
          if (s < 16) lens[i++] = s;
          else { const [rep, prevV] = s === 16 ? [3 + bits(2), lens[i - 1]] : s === 17 ? [3 + bits(3), 0] : [11 + bits(7), 0]; if (i + rep > lens.length) throw new Error('Повреждённые данные'); lens.fill(prevV, i, i + rep); i += rep; }
        }
        LT = table(lens.subarray(0, hlit)); DT = table(lens.subarray(hlit));
      } else throw new Error('Повреждённые данные');
      for (;;) {
        const s = sym(LT);
        if (s < 256) { grow(1); out[o++] = s; continue; }
        if (s === 256) break;
        const c = s - 257, len = LBASE[c] + bits(LEXT[c]), dc = sym(DT), dist = DBASE[dc] + bits(DEXT[dc]);
        if (dist > o) throw new Error('Повреждённые данные');
        grow(len);
        for (let i = 0; i < len; i++, o++) out[o] = out[o - dist];
      }
    }
    return out.subarray(0, o);
  }
  function gunzipSync(u8, limit) {
    if (u8[0] !== 0x1f || u8[1] !== 0x8b) throw new Error('Это не gzip');
    let p = 10;
    const fl = u8[3];
    if (fl & 4) p += 2 + (u8[p] | (u8[p + 1] << 8));
    if (fl & 8) while (u8[p++]);
    if (fl & 16) while (u8[p++]);
    if (fl & 2) p += 2;
    const out = inflateRaw(u8.subarray(p, u8.length - 8), limit);
    const t = u8.length - 8, crc = (u8[t] | (u8[t + 1] << 8) | (u8[t + 2] << 16) | (u8[t + 3] << 24)) >>> 0;
    if (crc32(out) !== crc) throw new Error('Контрольная сумма gzip не сошлась');
    return out;
  }
  async function viaStream(u8, format) {
    const s = new Blob([u8]).stream().pipeThrough(new DecompressionStream(format));
    return new Uint8Array(await new Response(s).arrayBuffer());
  }
  const native = typeof DecompressionStream !== 'undefined';
  async function gunzip(u8, limit) { return native ? viaStream(u8, 'gzip') : gunzipSync(u8, limit); }

  // ── Сжатие файла в фоновом потоке ──
  async function packBlob(blob, level, progress) {
    const parts = [HEADER];
    if (level === 'native') {
      const s = blob.stream().pipeThrough(new CompressionStream('gzip'));
      return new Response(s).blob();
    }
    const z = new Deflater(level), CH = 1 << 20;
    for (let off = 0; off < blob.size || off === 0; off += CH) {
      const u8 = new Uint8Array(await blob.slice(off, off + CH).arrayBuffer());
      parts.push(...z.push(u8, off + CH >= blob.size));
      if (progress) progress(Math.min(1, (off + CH) / Math.max(1, blob.size)));
      if (blob.size === 0) break;
    }
    parts.push(trailer(z.crc, z.size));
    return new Blob(parts, { type: 'application/gzip' });
  }
  const inWorker = typeof WorkerGlobalScope !== 'undefined' && root instanceof WorkerGlobalScope;
  if (inWorker) {
    root.onmessage = async e => {
      const { id, op, blob, level, sample } = e.data;
      try {
        if (op === 'probe') {
          const t0 = performance.now(), z = gzipSync(sample, 9);
          root.postMessage({ id, ratio: z.length / Math.max(1, sample.length), ms: performance.now() - t0 });
        } else {
          const out = await packBlob(blob, level, p => root.postMessage({ id, progress: p }));
          root.postMessage({ id, blob: out });
        }
      } catch (err) { root.postMessage({ id, error: String(err && err.message || err) }); }
    };
  }

  // ── Решение: сжимать ли, чем и не перегрузит ли это устройство ──
  const SKIP = /\.(jpe?g|png|gif|webp|heic|heif|avif|jxl|mp4|mov|m4v|webm|mkv|avi|3gp|mp3|m4a|aac|ogg|oga|opus|flac|wma|zip|rar|7z|gz|tgz|bz2|xz|zst|lz|lzma|cab|docx|docm|dotx|xlsx|xlsm|xltx|pptx|pptm|ppsx|potx|odt|ods|odp|odg|epub|jar|apk|aab|ipa|woff2?|pdf)$/i;
  const MAX_SIZE = 512 * 1024 * 1024, MIN_SIZE = 1024;
  let worker = null, seq = 0;
  const waiting = new Map();
  const scriptUrl = typeof document !== 'undefined' && document.currentScript ? document.currentScript.src : 'js/media/gzip.js';
  function call(msg, onProgress) {
    if (!worker) {
      worker = new Worker(scriptUrl);
      worker.onmessage = e => { const w = waiting.get(e.data.id); if (!w) return; if (e.data.progress != null) { if (w.onProgress) w.onProgress(e.data.progress); return; } waiting.delete(e.data.id); if (e.data.error) w.reject(new Error(e.data.error)); else w.resolve(e.data); };
      worker.onerror = () => { waiting.forEach(w => w.reject(new Error('gzip: фоновый поток недоступен'))); waiting.clear(); worker = null; };
    }
    return new Promise((resolve, reject) => { const id = ++seq; waiting.set(id, { resolve, reject, onProgress }); worker.postMessage(Object.assign({ id }, msg)); });
  }
  function stop() { if (worker) { worker.terminate(); worker = null; } waiting.forEach(w => w.reject(new Error('Отменено'))); waiting.clear(); }
  function budgetMs() {
    const weak = (navigator.deviceMemory && navigator.deviceMemory <= 2) || (navigator.hardwareConcurrency || 4) <= 2;
    return weak ? 8000 : 15000;
  }
  let queue = Promise.resolve();
  function pack(file, opts) {
    const run = queue.then(() => packNow(file, opts || {}));
    queue = run.catch(() => {});
    return run;
  }
  async function packNow(file, opts) {
    const name = file.name || '';
    if (SKIP.test(name) || file.size < MIN_SIZE) return { enc: 'identity', reason: 'не сжимается' };
    if (file.size > MAX_SIZE || typeof Worker === 'undefined') return { enc: null, reason: 'велик для устройства' };
    const S = 65536, cuts = file.size <= 3 * S ? [0] : [0, Math.floor(file.size / 2) - S / 2, file.size - S];
    const sample = new Uint8Array(await new Blob(cuts.map(c => file.slice(c, c + S))).arrayBuffer());
    const probe = await call({ op: 'probe', sample });
    if (probe.ratio > 0.9) return { enc: 'identity', reason: 'сжатие почти ничего не даёт', ratio: probe.ratio };
    const predicted = file.size / sample.length * probe.ms * 1.2;
    const hasNative = typeof CompressionStream !== 'undefined';
    const level = predicted <= budgetMs() ? 9 : hasNative ? 'native' : predicted / 4 <= budgetMs() ? 6 : null;
    if (level === null) return { enc: null, reason: 'долго для этого устройства' };
    if (opts.signal) opts.signal.addEventListener('abort', stop, { once: true });
    const t0 = Date.now();
    const r = await call({ op: 'pack', blob: file, level }, opts.onProgress);
    return { enc: 'gzip', blob: r.blob, level, ratio: r.blob.size / file.size, ms: Date.now() - t0 };
  }

  Kov.Gzip = { crc32, gzipSync, gunzipSync, inflateRaw, gunzip, pack, stop, SKIP, Deflater };
})(typeof self !== 'undefined' ? self : globalThis);
