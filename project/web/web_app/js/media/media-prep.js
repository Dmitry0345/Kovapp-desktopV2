window.Kov = window.Kov || {};

// ── Подготовка вложений на устройстве перед отправкой ──
Kov.MediaPrep = (function () {
  const BIG = 2 * 1024 * 1024;   // крупнее — кусками: сервер не разбирает большой составной запрос
  const DEDUP_MIN = 256 * 1024;
  const IMAGE_RE = /\.(jpe?g|png|webp|gif|bmp|heic|heif|avif|tiff?)$/i;
  const VIDEO_RE = /\.(mp4|mov|m4v|3gp|avi|mkv|webm|wmv|flv|ts)$/i;
  let controllers = new Set();
  let serverSupport = null;

  function enabled() { return typeof Blob !== 'undefined' && !!Blob.prototype.slice && typeof fetch === 'function' && serverSupport !== false; }
  function abortAll() { controllers.forEach(c => { try { c.abort(); } catch (e) { /* */ } }); controllers.clear(); }
  const kindOf = name => IMAGE_RE.test(name) ? 'image' : VIDEO_RE.test(name) ? 'video' : 'document';

  async function api(method, path, body, signal) {
    const res = await fetch(Kov.Api.url(path), {
      method, signal, body: body ? JSON.stringify(body) : undefined,
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + Kov.Api.state.token },
    });
    if (res.status === 404 || res.status === 405) { serverSupport = false; throw Object.assign(new Error('сервер не поддерживает'), { unsupported: true }); }
    if (!res.ok) throw new Error('HTTP ' + res.status);
    serverSupport = true;
    return res.json();
  }

  async function dedup(file, signal, onHash, knownTree) {
    if (file.size < DEDUP_MIN || !Kov.FileHash.worthIt(file.size)) return { tree: null, token: null };
    const tree = knownTree || await Kov.FileHash.tree(file, onHash);
    try {
      const ch = await api('POST', 'uploads/dedup/check', { tree, size: file.size, name: file.name }, signal);
      const hashes = [];
      for (const w of ch.windows || []) hashes.push(await Kov.FileHash.range(file, w.o, w.l));
      const ans = await api('POST', 'uploads/dedup/answer', { id: ch.id, hashes }, signal);
      return { tree, token: ans.token || null };
    } catch (e) {
      if (e.unsupported) throw e;
      return { tree, token: null };
    }
  }

  async function posterFromCanvas(canvas) {
    const ph = await Kov.ImagePrep.placeholder(canvas, canvas.width, canvas.height);
    const th = await Kov.ImagePrep.thumbnail(canvas, canvas.width, canvas.height);
    return { hash: ph.hash, thumb: th ? th.blob : null };
  }

  async function sendWhole(file, name, kind, signal, onBytes) {
    // небольшой файл идёт в общем запросе — копией в памяти, а не ссылкой на исходник телефона
    if (file.size <= BIG) return { multipart: Kov.Files && Kov.Files.inMemory ? await Kov.Files.inMemory(file) : file };
    const r = await Kov.ChunkUpload.uploadFile(file, { name, kind, signal, onProgress: onBytes });
    return { session: r.session };
  }

  /**
   * Проверка копии без ожидания: образец (≈0,6 МБ) и быстрый вопрос серверу. Кандидат есть — полный
   * отпечаток и испытание (пропуск — только с доказательством владения); кандидата нет — подготовка
   * идёт сразу, а полный отпечаток считается в фоне (он нужен серверу для будущих повторных отправок).
   */
  async function quickDedup(file, signal, onHash) {
    const none = { token: null, sample: null, treeP: Promise.resolve(null) };
    if (file.size < DEDUP_MIN || !Kov.FileHash.worthIt(file.size)) return none;
    let smp = null;
    try { smp = Kov.FileHash.sample ? await Kov.FileHash.sample(file) : null; } catch (e) { smp = null; }
    const known = smp && Kov.FileHash.cached ? Kov.FileHash.cached(file, smp) : null;
    let candidate = false, probed = false;
    if (smp) {
      try {
        candidate = !!(await api('POST', 'uploads/dedup/probe', { size: file.size, sample: smp }, signal)).candidate;
        probed = true;
      } catch (e) {
        if (signal && signal.aborted) throw e;   // сервер без быстрой проверки — прежний порядок (полный отпечаток)
      }
    }
    console.info('Проверка копии: ' + (!probed ? 'сервер без быстрой проверки' : candidate ? 'похожий файл есть — сверяем полностью' : 'копии нет')
      + ' (' + (file.size / 1048576).toFixed(1) + ' МБ)');
    if (candidate || !probed) {
      // кандидат есть (или сервер без быстрой проверки) — как раньше: полный отпечаток и испытание
      const d = await dedup(file, signal, onHash, known);
      console.info('Проверка копии: ' + (d.token ? 'копия на сервере — отправка не нужна' : 'отпечаток не совпал — отправляем'));
      if (d.tree && smp && Kov.FileHash.remember) Kov.FileHash.remember(file, smp, d.tree);
      return { token: d.token, sample: smp, treeP: Promise.resolve(d.tree) };
    }
    const h0 = Date.now();
    const treeP = known ? Promise.resolve(known)
      : Kov.FileHash.tree(file).then(t => {
        if (Kov.FileHash.remember) Kov.FileHash.remember(file, smp, t);
        const sec = (Date.now() - h0) / 1000, mb = file.size / 1048576;   // в журнал: скорость подсчёта отпечатка
        console.info('Отпечаток файла в фоне: ' + mb.toFixed(1) + ' МБ за ' + sec.toFixed(1) + ' с (' + (mb / Math.max(sec, 0.001)).toFixed(0) + ' МБ/с)');
        return t;
      }).catch(() => null);
    return { token: null, sample: smp, treeP };
  }

  let lastVideoEnd = 0;   // конец подготовки предыдущего видео — чтобы видеть паузу между видео

  async function prepareOne(file, ctx, report) {
    const name = file.name || 'file';
    const kind = kindOf(name);
    const meta = { name, size: file.size };
    const out = { meta, thumb: null, payload: null };
    const signal = ctx.signal;
    const tStart = Date.now();   // замеры для журнала: на что уходит время между видео
    report(0, 'Проверка');
    const d = await quickDedup(file, signal, p => report(p * 0.1, 'Проверка'));
    const tDedup = Date.now();
    if (d.sample) meta.sample = d.sample;
    out.treeP = d.treeP;   // полный отпечаток — прикладывается в конце подготовки (prepareAll)
    // копия найдена: для человека это просто мгновенная отправка
    if (d.token) { meta.src = 'dedup'; meta.token = d.token; report(1, 'Готово'); return out; }
    if (kind === 'image') {
      report(0.15, 'Сжатие фото');
      const r = await Kov.ImagePrep.prepare(file);
      if (r.thumbHash) meta.thumb_hash = r.thumbHash;
      if (r.width) { meta.width = r.width; meta.height = r.height; }
      out.thumb = r.thumb;
      meta.prep = r.changed ? 'image' : 'original';
      const sent = await sendWhole(r.file, r.file.name || name, 'image', signal, b => report(0.5 + 0.5 * b / r.file.size, 'Отправка'));
      Object.assign(out, sent);
      report(1, 'Готово к отправке');
      return out;
    }
    if (kind === 'video' && Kov.VideoPrep) {
      const edits = ctx.edits || null;
      report(0.12, 'Проверка устройства');
      let plan = { mode: 'server' };
      const tPlan0 = Date.now();
      try { plan = await Kov.VideoPrep.plan(file, { edits }); } catch (e) { plan = { mode: 'server', reason: String(e && e.message || e) }; }
      const tPlan = Date.now();
      // почему видео ушло без сжатия — сервер пишет это в журнал рядом с размером и потоком
      let why = plan.mode === 'device' ? '' : (plan.reason || plan.mode || '');
      if (plan.mode === 'device') {
        const writer = Kov.ChunkUpload.stream({ name: name.replace(/\.[^.]+$/, '') + '.mp4', kind: 'video', signal });
        let poster = null, compressed = true;
        try {
          await Kov.VideoPrep.compress(file, plan, bytes => writer.write(bytes), {
            edits,
            onProgress: p => report(0.15 + 0.8 * p, 'Сжатие видео'),
            onPoster: canvas => { poster = posterFromCanvas(canvas); },
          });
        } catch (e) {
          if (signal && signal.aborted) throw e;
          // сжатие сорвалось (кодек телефона, память): недописанное отбрасываем, видео уходит как есть — с докачкой
          console.warn('Сжатие видео не удалось, отправляем как есть', e);
          try { writer.abort(); } catch (err) { /* */ }
          compressed = false;
          why = 'сбой сжатия: ' + String(e && e.message || e).slice(0, 120);
        }
        let handle = null;
        if (compressed) {
          // сколько ушло на сервер ещё во время сжатия — в журнал приложения
          const sentDuring = writer.sent, total = writer.written, t0 = Date.now();
          const sec = ms => (ms / 1000).toFixed(1);
          const gap = lastVideoEnd && tStart - lastVideoEnd < 60000 ? ', пауза после предыдущего видео ' + sec(tStart - lastVideoEnd) + ' с' : '';
          console.info('Видео: проверка дубликата ' + sec(tDedup - tStart) + ' с, план ' + sec(tPlan - tPlan0) + ' с, сжатие ' + sec(t0 - tPlan) + ' с' + gap);
          try {
            handle = await writer.close();
            const mb = n => (n / 1048576).toFixed(1);
            console.info('Видео: во время сжатия отправлено ' + Math.round(100 * sentDuring / Math.max(1, total)) + '% (' + mb(sentDuring) + ' из ' + mb(total)
              + ' МБ), остаток и проверка — ' + ((Date.now() - t0) / 1000).toFixed(1) + ' с');
          } catch (e) {
            if (signal && signal.aborted) throw e;
            console.warn('Потоковая отправка не завершилась, видео уйдёт как есть', e);
            try { writer.abort(); } catch (err) { /* */ }
            compressed = false;
            why = 'сжатое не дошло до сервера: ' + String(e && e.message || e).slice(0, 120);
          }
        }
        if (compressed && handle) {
          lastVideoEnd = Date.now();
          if (poster) { const pp = await poster; meta.thumb_hash = pp.hash; out.thumb = pp.thumb; }
          Object.assign(meta, { src: 'session', session: handle.session, prep: 'device', edits_applied: !!edits,
            width: plan.width, height: plan.height,
            duration: edits && (edits.trim_end || edits.trim_start) ? Math.max(0, (Number(edits.trim_end) || plan.analysis.duration) - (Number(edits.trim_start) || 0)) : plan.analysis.duration });
          out.session = handle.session;
          report(1, 'Готово к отправке');
          return out;
        }
      }
      meta.prep = 'original';
      if (why) meta.prep_reason = why;
      const shot = await Kov.VideoPrep.posterFromElement(file, 640).catch(() => null);
      if (shot && shot.canvas) {
        const pp = await posterFromCanvas(shot.canvas);
        meta.thumb_hash = pp.hash; out.thumb = pp.thumb;
        meta.duration = shot.duration; meta.width = shot.width; meta.height = shot.height;
      }
      Object.assign(out, await sendWhole(file, name, 'video', signal, b => report(0.2 + 0.8 * b / file.size, 'Отправка')));
      report(1, 'Готово к отправке');
      return out;
    }
    meta.prep = 'original';
    let body = file;
    if (kind === 'document') {
      if (/\.pdf$/i.test(name) && Kov.PdfView && file.size <= 40 * 1048576) {
        try { const t = await Kov.PdfView.thumbnail(file); if (t.hash) meta.thumb_hash = t.hash; out.thumb = t.blob; } catch (e) { /* без миниатюры */ }
      }
      if (Kov.Gzip) {
        try {
          const z = await Kov.Gzip.pack(file, { signal, onProgress: p => report(0.05 + 0.25 * p, 'Сжатие') });
          if (z.enc === 'gzip' && z.blob.size < file.size) { meta.enc = 'gzip'; body = z.blob; }
          else if (z.enc === 'identity') meta.enc = 'identity';
        } catch (e) { if (signal && signal.aborted) throw e; }
      }
    }
    const from = body === file ? 0.1 : 0.3;
    Object.assign(out, await sendWhole(body, name, kind, signal, b => report(from + (1 - from) * b / body.size, 'Отправка')));
    report(1, 'Готово к отправке');
    return out;
  }

  async function prepareAll(files, opts) {
    opts = opts || {};
    const ctrl = new AbortController();
    controllers.add(ctrl);
    if (opts.signal) {
      if (opts.signal.aborted) ctrl.abort();
      else opts.signal.addEventListener('abort', () => ctrl.abort(), { once: true });
    }
    const weights = files.map(f => Math.max(1, f.size) * (kindOf(f.name) === 'video' ? 3 : 1));
    const total = weights.reduce((a, b) => a + b, 0);
    const progress = files.map(() => 0);
    const states = files.map(f => ({ name: f.name, state: 'queued', percent: 0, note: '' }));
    const emit = () => {
      const p = progress.reduce((sum, v, i) => sum + v * weights[i], 0) / total;
      const active = states.find(s => s.state === 'processing');
      const label = 'Подготовка · ' + (active ? active.note.toLowerCase() + ' · ' : '') + Math.round(p * 100) + '%';
      if (opts.onProgress) opts.onProgress(p, label, { files: states.map((s, i) => ({ index: i, name: s.name, state: s.state, percent: s.percent, note: s.note })) });
    };
    const items = new Array(files.length);
    try {
      let next = 0;
      const worker = async () => {
        while (next < files.length) {
          const i = next++;
          const file = files[i];
          const perFileEdits = opts.edits && opts.edits[String(i)] ? opts.edits[String(i)] : null;
          states[i].state = 'processing';
          items[i] = await prepareOne(file, { signal: ctrl.signal, edits: perFileEdits }, (p, note) => {
            progress[i] = Math.max(progress[i], Math.min(1, p)); states[i].percent = Math.round(progress[i] * 100); states[i].note = note; emit();
          });
          states[i].state = 'received'; progress[i] = 1; states[i].percent = 100; emit();
        }
      };
      const heavy = files.some(f => kindOf(f.name) === 'video');
      await Promise.all(Array.from({ length: heavy ? 1 : Math.min(3, files.length) }, worker));
    } catch (e) {
      if (Kov.ChunkUpload) Kov.ChunkUpload.discardBySignal(ctrl.signal);
      if (ctrl.signal.aborted && !(e && e.aborted)) throw Object.assign(new Error('Загрузка отменена'), { aborted: true });
      throw e;
    } finally { controllers.delete(ctrl); }
    // полные отпечатки, считавшиеся в фоне, — к описаниям файлов (сервер запомнит их для повторных отправок)
    await Promise.all(items.map(async (it, i) => {
      if (!it || !it.treeP) return;
      const t = await it.treeP.catch(() => null);
      delete it.treeP;
      if (t) { it.meta.tree = t; it.meta.source_size = files[i].size; }
    }));
    const multipart = [], thumbs = [], meta = [];
    items.forEach(it => {
      const m = Object.assign({}, it.meta);
      if (!m.src) {
        if (it.session) { m.src = 'session'; m.session = it.session; }
        else { m.src = 'file'; m.file = multipart.length; multipart.push(it.multipart); }
      }
      if (it.thumb) { m.thumb = thumbs.length; thumbs.push(it.thumb); }
      meta.push(m);
    });
    return { files: multipart, thumbs, meta, signal: ctrl.signal, heavy: items.some(it => it.meta.prep === 'device' || it.session) };
  }

  return { enabled, prepareAll, abortAll, kindOf, dedup };
})();
