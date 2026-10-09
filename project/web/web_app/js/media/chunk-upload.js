window.Kov = window.Kov || {};

// ── Порционная отправка с докачкой после обрыва связи ──
Kov.ChunkUpload = (function () {
  const CHUNK = 4 * 1024 * 1024;
  const HIGH_WATER = 32 * 1024 * 1024;
  const BACKOFF = [1000, 2000, 4000, 8000, 15000, 30000];
  // сколько ждём связь, прежде чем бросить отправку
  const STALL_MS = 2 * 60 * 1000;
  const TABLE = (() => { const t = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; })();

  function crc32(bytes) {
    let c = 0xFFFFFFFF;
    for (let i = 0; i < bytes.length; i++) c = TABLE[(c ^ bytes[i]) & 0xFF] ^ (c >>> 8);
    return (c ^ 0xFFFFFFFF) >>> 0;
  }
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  function waitOnline(signal, until) {
    if (navigator.onLine !== false) return Promise.resolve();
    return new Promise(resolve => {
      let timer = null;
      const done = () => { window.removeEventListener('online', done); clearTimeout(timer); resolve(); };
      window.addEventListener('online', done);
      if (until) timer = setTimeout(done, Math.max(0, until - Date.now()));
      if (signal) signal.addEventListener('abort', done, { once: true });
    });
  }

  // ── Незавершённые сеансы: при отмене и обрыве их куски удаляются с сервера ──
  const live = new Map();
  const doomed = new Map();
  let sweepTimer = null;

  function track(id, signal) { if (id) live.set(id, { signal }); }
  function forget(id) { live.delete(id); }
  function forgetBySignal(signal) { for (const [id, v] of [...live]) if (v.signal === signal) live.delete(id); }

  function discard(id) {
    if (!id) return;
    live.delete(id);
    if (!doomed.has(id)) doomed.set(id, Date.now());
    sweep();
  }
  function discardBySignal(signal) { for (const [id, v] of [...live]) if (v.signal === signal) discard(id); }
  function discardAll() { for (const id of [...live.keys()]) discard(id); }

  async function sweep() {
    clearTimeout(sweepTimer); sweepTimer = null;
    for (const [id, since] of [...doomed]) {
      if (Date.now() - since > 10 * 60 * 1000) { doomed.delete(id); continue; }
      try {
        const res = await fetch(Kov.Api.url('uploads/sessions/' + id), {
          method: 'DELETE', keepalive: true, headers: { Authorization: 'Bearer ' + Kov.Api.state.token },
        });
        if (res.ok || res.status === 404) doomed.delete(id);
      } catch (e) { /* связи нет — повторим */ }
    }
    if (doomed.size) sweepTimer = setTimeout(sweep, 5000);
  }
  window.addEventListener('online', () => { if (doomed.size) sweep(); });
  class Fatal extends Error {}
  function stalled() {
    return Object.assign(new Error('Связь не вернулась за ' + Math.round(STALL_MS / 60000) + ' мин — отправка отменена, файл не сохранён'),
      { network: true, stalled: true });
  }

  async function call(method, path, body, headers, signal, timeout) {
    const ctrl = new AbortController();
    const onAbort = () => ctrl.abort();
    if (signal) { if (signal.aborted) throw Object.assign(new Error('Загрузка отменена'), { aborted: true }); signal.addEventListener('abort', onAbort, { once: true }); }
    const timer = setTimeout(() => ctrl.abort(), timeout || 120000);
    try {
      const res = await fetch(Kov.Api.url(path), {
        method, body, signal: ctrl.signal,
        headers: Object.assign({ Authorization: 'Bearer ' + Kov.Api.state.token }, headers || {}),
      });
      let data = null; try { data = await res.json(); } catch (e) { data = null; }
      if (res.ok) return data || {};
      const detail = data && (typeof data.detail === 'string' ? data.detail : (data.detail && data.detail.detail)) || ('HTTP ' + res.status);
      const received = data && (data.received != null ? data.received : (data.detail && data.detail.received));
      const err = new Error(detail);
      err.status = res.status; err.received = received;
      if (res.status >= 400 && res.status < 500 && res.status !== 408 && res.status !== 429 && res.status !== 409) throw Object.assign(new Fatal(detail), { status: res.status });
      throw err;
    } catch (e) {
      if (signal && signal.aborted) throw Object.assign(new Error('Загрузка отменена'), { aborted: true });
      throw e;
    } finally {
      clearTimeout(timer);
      if (signal) signal.removeEventListener('abort', onAbort);
    }
  }

  class Session {
    constructor(opts) {
      this.opts = opts || {};
      this.signal = this.opts.signal;
      this.id = null; this.received = 0; this.retries = 0; this.stalledSince = 0;
    }
    async open() {
      const r = await this.retry(() => call('POST', 'uploads/sessions', JSON.stringify({
        name: this.opts.name, size: this.opts.size == null ? null : this.opts.size, kind: this.opts.kind || '', meta: this.opts.meta || {},
      }), { 'Content-Type': 'application/json' }, this.signal));
      this.id = r.id; this.received = r.received || 0;
      track(this.id, this.signal);
      return this;
    }
    async sync() {
      const r = await call('GET', 'uploads/sessions/' + this.id, undefined, null, this.signal, 30000);
      this.received = r.received || 0;
      return this.received;
    }
    async retry(fn) {
      for (let attempt = 0; ; attempt++) {
        try {
          const result = await fn();
          this.stalledSince = 0;
          return result;
        } catch (e) {
          if (e instanceof Fatal || e.aborted) throw e;
          if (!this.stalledSince) this.stalledSince = Date.now();
          const deadline = this.stalledSince + STALL_MS;
          if (Date.now() >= deadline) throw stalled();
          this.retries++;
          if (this.opts.onRetry) this.opts.onRetry(e, attempt);
          await sleep(Math.min(BACKOFF[Math.min(attempt, BACKOFF.length - 1)], Math.max(0, deadline - Date.now())));
          await waitOnline(this.signal, deadline);
          if (this.signal && this.signal.aborted) throw Object.assign(new Error('Загрузка отменена'), { aborted: true });
          if (Date.now() >= deadline) throw stalled();
          if (this.id) { try { await this.sync(); } catch (x) { /* ещё нет связи */ } }
        }
      }
    }
    async put(offset, bytes) {
      return this.retry(async () => {
        if (this.received >= offset + bytes.length) return this.received;
        const skip = Math.max(0, this.received - offset);
        const part = skip ? bytes.subarray(skip) : bytes;
        try {
          const r = await call('PUT', 'uploads/sessions/' + this.id + '?offset=' + (offset + skip), part,
            { 'Content-Type': 'application/octet-stream', 'X-Chunk-Crc32': String(crc32(part)) }, this.signal);
          this.received = r.received;
        } catch (e) {
          if (e.status === 409 && e.received != null) { this.received = e.received; if (this.received >= offset + bytes.length) return this.received; }
          throw e;
        }
        if (this.opts.onProgress) this.opts.onProgress(this.received);
        return this.received;
      });
    }
    async complete(size) {
      return this.retry(() => call('POST', 'uploads/sessions/' + this.id + '/complete', JSON.stringify({ size }), { 'Content-Type': 'application/json' }, this.signal));
    }
  }

  async function uploadFile(file, opts) {
    const s = new Session(Object.assign({ name: file.name, size: file.size }, opts || {}));
    try {
      await s.open();
      while (s.received < file.size) {
        const start = s.received;
        const bytes = new Uint8Array(await file.slice(start, Math.min(file.size, start + CHUNK)).arrayBuffer());
        await s.put(start, bytes);
      }
      const done = await s.complete(file.size);
      return { session: s.id, tree: done.tree, sha256: done.sha256, size: file.size, retries: s.retries };
    } catch (e) {
      discard(s.id);
      throw e;
    }
  }

  function stream(opts) {
    const s = new Session(Object.assign({ size: null }, opts || {}));
    let queue = [], queued = 0, written = 0, sentOffset = 0, closed = false, failed = null;
    let wake = null, drained = null;
    // сверка итога: дерево хешей по 4 МБ — так же считает сервер (tree_hash_file); без WebCrypto — без сверки
    const LEAF = 4 * 1024 * 1024;
    const canHash = !!(window.crypto && crypto.subtle && typeof crypto.subtle.digest === 'function');
    const leaves = []; let leafBuf = canHash ? new Uint8Array(LEAF) : null, leafLen = 0, hashing = Promise.resolve();
    const leafDone = block => { hashing = hashing.then(async () => { leaves.push(new Uint8Array(await crypto.subtle.digest('SHA-256', block))); }); };
    const feedHash = bytes => {
      if (!canHash) return;
      let at = 0;
      while (at < bytes.length) {
        const take = Math.min(LEAF - leafLen, bytes.length - at);
        leafBuf.set(bytes.subarray(at, at + take), leafLen);
        leafLen += take; at += take;
        if (leafLen === LEAF) { leafDone(leafBuf); leafBuf = new Uint8Array(LEAF); leafLen = 0; }
      }
    };
    const treeHex = async () => {
      if (leafLen) { leafDone(leafBuf.slice(0, leafLen)); leafLen = 0; }
      await hashing;
      // как tree_hash_file: хеши листьев подряд, затем размер — 8 байт, старший байт первым
      const all = new Uint8Array(leaves.length * 32 + 8);
      leaves.forEach((d, i) => all.set(d, i * 32));
      const dv = new DataView(all.buffer, leaves.length * 32, 8);
      dv.setUint32(0, Math.floor(written / 4294967296)); dv.setUint32(4, written >>> 0);
      return [...new Uint8Array(await crypto.subtle.digest('SHA-256', all))].map(x => x.toString(16).padStart(2, '0')).join('');
    };
    const opened = s.open();
    const notify = () => { if (wake) { const w = wake; wake = null; w(); } };
    // отправка во время сжатия: полная часть — 4 МБ; но если данные ждут дольше FLUSH_MS, уходит то, что есть
    // (от MIN_FLUSH) — короткие видео тоже отправляются по ходу сжатия, досылка в конце меньше;
    // на медленной сети очередь сама копится до полной части
    const FLUSH_MS = 300, MIN_FLUSH = 256 * 1024;
    let oldest = 0;   // когда в очередь легли самые старые неотправленные данные
    const pump = (async () => {
      await opened;
      for (;;) {
        if (failed) return;
        if (queued < CHUNK && !closed) {
          const age = Date.now() - oldest;
          if (!(queued >= MIN_FLUSH && age >= FLUSH_MS)) {
            const waitMs = queued >= MIN_FLUSH ? Math.max(10, FLUSH_MS - age) : 0;
            await new Promise(r => {
              wake = r;
              if (waitMs) setTimeout(() => { if (wake === r) { wake = null; r(); } }, waitMs);
            });
            continue;
          }
        }
        if (!queued && closed) return;
        const size = Math.min(CHUNK, queued);
        const chunk = new Uint8Array(size);
        let filled = 0;
        while (filled < size) {
          const head = queue[0];
          const take = Math.min(head.length, size - filled);
          chunk.set(head.subarray(0, take), filled);
          filled += take;
          if (take === head.length) queue.shift(); else queue[0] = head.subarray(take);
        }
        queued -= size;
        oldest = queued ? Date.now() : 0;
        await s.put(sentOffset, chunk);
        sentOffset += size;
        if (drained && queued < HIGH_WATER) { const d = drained; drained = null; d(); }
      }
    })().catch(e => { failed = e; discard(s.id); if (drained) { const d = drained; drained = null; d(); } });
    return {
      get session() { return s; },
      get sent() { return sentOffset; },
      get written() { return written; },
      async write(bytes) {
        if (failed) throw failed;
        if (closed) throw new Error('Поток уже закрыт');
        if (!queued) oldest = Date.now();
        queue.push(bytes); queued += bytes.length; written += bytes.length;
        feedHash(bytes);
        notify();
        if (queued >= HIGH_WATER) await new Promise(r => { drained = r; });
        if (failed) throw failed;
      },
      async close() {
        closed = true; notify();
        await pump;
        if (failed) throw failed;
        try {
          const done = await s.complete(written);
          // сервер собрал ровно то, что ушло: дерево хешей совпадает (сервер его не прислал — сверка пропускается)
          if (canHash && done && done.tree) {
            const mine = await treeHex();
            if (mine !== done.tree) throw Object.assign(new Error('Файл на сервере собран с ошибкой'), { mismatch: true });
          }
          return { session: s.id, tree: done.tree, sha256: done.sha256, size: written, retries: s.retries };
        } catch (e) { discard(s.id); throw e; }
      },
      abort() { failed = failed || Object.assign(new Error('Загрузка отменена'), { aborted: true }); discard(s.id); notify(); },
    };
  }

  return { uploadFile, stream, crc32, CHUNK, STALL_MS, Session, forget, forgetBySignal, discard, discardBySignal, discardAll, get live() { return live.size; } };
})();
