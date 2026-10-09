window.Kov = window.Kov || {};

// ── Сжатие видео на устройстве через WebCodecs ──
Kov.VideoPrep = (function () {
  const FULL_SIDE = 1920, HD_SIDE = 1280, MAX_FPS = 30, BPP = 0.07, READY_BPP = 0.075;
  const KEY_EVERY = 4e6, TIME_LIMIT = 600, WATCHDOG = 20000;
  // дольше минуты — сразу HD 1280: сжатие примерно вдвое быстрее, файл в ~2,25 раза меньше (как в приложении)
  const LONG_VIDEO_S = 60;
  const AUDIO_PASS = ['mp4a', 'Opus', 'ac-3', 'ec-3', 'alac', 'fLaC', 'sowt', 'twos', 'ipcm', 'lpcm', 'samr', '.mp3'];

  function supported() { return typeof VideoEncoder === 'function' && typeof VideoDecoder === 'function' && window.isSecureContext && typeof OffscreenCanvas === 'function'; }

  function evenFit(w, h, side) {
    const r = Math.min(1, side / Math.max(w, h));
    return [Math.max(2, Math.round(w * r / 2) * 2), Math.max(2, Math.round(h * r / 2) * 2)];
  }

  function avcCodec(w, h, fps, profile) {
    const mbps = Math.ceil(w / 16) * Math.ceil(h / 16) * fps;
    const level = mbps <= 108000 ? 0x1f : mbps <= 245760 ? 0x28 : mbps <= 522240 ? 0x32 : 0x33;
    const p = { high: '6400', main: '4d00', base: '42e0' }[profile];
    return 'avc1.' + p + level.toString(16).padStart(2, '0');
  }

  function fpsOf(track) {
    const n = track.samples.length;
    if (n < 2) return 30;
    const span = (track.samples[n - 1].dts - track.samples[0].dts) / track.timescale;
    return span > 0 ? (n - 1) / span : 30;
  }

  // ── Анализ исходника ──
  async function analyze(file) {
    const info = await Kov.MP4.demux(file);
    const v = info.video;
    if (!v || !v.entry) throw new Error('в файле нет видеодорожки');
    const turned = v.rotation === 90 || v.rotation === 270;
    const width = turned ? v.entry.height : v.entry.width;
    const height = turned ? v.entry.width : v.entry.height;
    const fps = fpsOf(v);
    const duration = info.duration || v.duration;
    const bytes = v.samples.reduce((n, s) => n + s.size, 0);
    const bitrate = duration ? bytes * 8 / duration : 0;
    return { info, video: v, audio: info.audio, width, height, fps, duration, bitrate, frames: v.samples.length };
  }

  function alreadyReady(a, edits) {
    if (edits && (edits.trim_start || edits.trim_end || edits.rotate || (edits.crop && (edits.crop.w < 0.999 || edits.crop.h < 0.999)))) return false;
    const e = a.video.entry;
    const audioOk = !a.audio || /^mp4a\.40\.|^mp3$/.test(a.audio.entry && a.audio.entry.codec || '');
    const fps = Math.min(a.fps, MAX_FPS);
    return e.family === 'avc' && /^avc1\.(42|4d|58|64)/i.test(e.codec) && Math.max(a.width, a.height) <= FULL_SIDE
      && a.fps <= MAX_FPS + 1 && a.bitrate <= READY_BPP * a.width * a.height * fps && audioOk && !a.video.rotation && a.info.audioTracks <= 1;
  }

  async function decoderConfig(a) {
    const e = a.video.entry;
    const cfg = { codec: e.codec, codedWidth: e.width, codedHeight: e.height };
    if (e.description) cfg.description = e.description;
    const r = await VideoDecoder.isConfigSupported(cfg);
    return r.supported ? cfg : null;
  }

  async function encoderConfig(w, h, fps, codecOverride) {
    const bitrate = Math.round(BPP * w * h * fps);
    const codecs = codecOverride ? [codecOverride] : [avcCodec(w, h, fps, 'high'), avcCodec(w, h, fps, 'main'), avcCodec(w, h, fps, 'base')];
    for (const codec of codecs) {
      const cfg = { codec, width: w, height: h, bitrate, framerate: fps, bitrateMode: 'variable', latencyMode: 'quality' };
      if (codec.startsWith('avc')) cfg.avc = { format: 'avc' };
      // сначала аппаратный кодировщик (видеокарта, Quick Sync, кодек телефона), нет его — как раньше
      for (const accel of ['prefer-hardware', null]) {
        const tryCfg = accel ? Object.assign({}, cfg, { hardwareAcceleration: accel }) : cfg;
        try { if ((await VideoEncoder.isConfigSupported(tryCfg)).supported) return tryCfg; } catch (e) { /* следующий вариант */ }
      }
    }
    return null;
  }

  // ── Кадр: обрезка, поворот, масштаб ──
  function makeRenderer(a, outW, outH, edits) {
    const rot = ((a.video.rotation || 0) + (edits && edits.rotate ? Number(edits.rotate) : 0)) % 360;
    const canvas = new OffscreenCanvas(outW, outH);
    const ctx = canvas.getContext('2d', { alpha: false });
    ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = 'high';
    const crop = edits && edits.crop ? edits.crop : null;
    const cw = a.video.entry.width, ch = a.video.entry.height;
    return frame => {
      const fw = frame.displayWidth || cw, fh = frame.displayHeight || ch;
      let sx = 0, sy = 0, sw = fw, sh = fh;
      if (crop && (crop.w < 0.999 || crop.h < 0.999)) {
        const turned = rot === 90 || rot === 270;
        const box = turned ? { x: crop.y, y: crop.x, w: crop.h, h: crop.w } : crop;
        sx = Math.round(box.x * fw); sy = Math.round(box.y * fh); sw = Math.round(box.w * fw); sh = Math.round(box.h * fh);
      }
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.translate(outW / 2, outH / 2);
      ctx.rotate(rot * Math.PI / 180);
      const turned = rot === 90 || rot === 270;
      const dw = turned ? outH : outW, dh = turned ? outW : outH;
      ctx.drawImage(frame, sx, sy, sw, sh, -dw / 2, -dh / 2, dw, dh);
      return canvas;
    };
  }

  function outputSize(a, side, edits) {
    let w = a.width, h = a.height;
    const extra = edits && edits.rotate ? Number(edits.rotate) % 180 === 90 : false;
    if (extra) [w, h] = [h, w];
    if (edits && edits.crop && (edits.crop.w < 0.999 || edits.crop.h < 0.999)) { w = Math.round(w * edits.crop.w); h = Math.round(h * edits.crop.h); }
    return evenFit(w, h, side);
  }

  // ── Основной конвейер ──
  async function run(file, a, plan, sink, opts) {
    opts = opts || {};
    const v = a.video;
    const muxer = new Kov.MP4.Muxer(bytes => sink(bytes), { fragmentSeconds: 2 });
    let codecDescription = null, encodedCount = 0, lastProgress = Date.now(), failed = null;
    const trimStart = Math.max(0, Number(opts.edits && opts.edits.trim_start) || 0) * 1e6;
    const trimEnd = Number(opts.edits && opts.edits.trim_end) > 0 ? Number(opts.edits.trim_end) * 1e6 : Infinity;
    const limitFrames = plan.benchmark ? plan.benchmarkFrames : Infinity;
    let poster = null;
    const posterAt = Math.min(1e6, (a.duration * 1e6) / 2) + trimStart;
    const writes = [];
    const encoder = new VideoEncoder({
      output: (chunk, meta) => {
        if (meta && meta.decoderConfig && meta.decoderConfig.description && !codecDescription) codecDescription = new Uint8Array(meta.decoderConfig.description);
        const data = new Uint8Array(chunk.byteLength); chunk.copyTo(data);
        if (!muxer.video) muxer.addVideo({ codec: plan.config.codec, width: plan.width, height: plan.height, description: codecDescription });
        if (!muxer.audio && a.audio && !plan.benchmark) muxer.addAudio({ timescale: a.audio.timescale, entryRaw: a.audio.entry.raw });
        muxer.addVideoChunk(data, chunk.timestamp - trimStart, chunk.type === 'key');
        encodedCount++;
        lastProgress = Date.now();
        writes.push(Promise.resolve());
      },
      error: e => { failed = failed || e; },
    });
    encoder.configure(plan.config);
    const render = makeRenderer(a, plan.width, plan.height, opts.edits);
    let nextKeep = -Infinity, lastKey = -Infinity, keptFrames = 0;
    const frameStep = 1e6 / plan.fps;
    const pendingFrames = [];
    const decoder = new VideoDecoder({
      output: frame => {
        const ts = frame.timestamp;
        try {
          if (ts < trimStart || ts > trimEnd || ts < nextKeep - frameStep * 0.25 || keptFrames >= limitFrames) return;
          nextKeep = (nextKeep === -Infinity ? ts : Math.max(nextKeep, ts - frameStep * 0.5)) + frameStep;
          const canvas = render(frame);
          const out = new VideoFrame(canvas, { timestamp: ts, duration: frameStep });
          const key = ts - lastKey >= KEY_EVERY;
          if (key) lastKey = ts;
          if (!poster && opts.onPoster && ts >= posterAt) { poster = true; opts.onPoster(canvas); }
          encoder.encode(out, { keyFrame: key });
          out.close();
          keptFrames++;
        } finally { frame.close(); }
        lastProgress = Date.now();
      },
      error: e => { failed = failed || e; },
    });
    decoder.configure(plan.decoder);
    let startIndex = 0;
    if (trimStart > 0) {
      for (let i = 0; i < v.samples.length; i++) { const t = v.samples[i].pts * 1e6 / v.timescale; if (v.samples[i].key && t <= trimStart) startIndex = i; if (t > trimStart) break; }
    }
    const all = a.audio && !plan.benchmark ? a.audio.samples : [];
    const ats = a.audio ? a.audio.timescale : 1;
    let audioIndex = 0, audioEnd = all.length;
    while (audioIndex < all.length && all[audioIndex].dts * 1e6 / ats < trimStart) audioIndex++;
    while (audioEnd > audioIndex && all[audioEnd - 1].dts * 1e6 / ats >= trimEnd) audioEnd--;
    const audioShift = a.audio ? (audioIndex < all.length ? all[audioIndex].dts : 0) - Math.round(Math.max(0, (all[audioIndex] ? all[audioIndex].dts * 1e6 / ats : 0) - trimStart) * ats / 1e6) : 0;
    const pushAudio = async () => {
      const as = all[audioIndex];
      const duration = audioIndex + 1 < all.length ? all[audioIndex + 1].dts - as.dts : (audioIndex ? as.dts - all[audioIndex - 1].dts : 1024);
      audioIndex++;
      muxer.addAudioSample(await Kov.MP4.readSample(file, as), as.dts - audioShift, duration);
    };
    const watchdog = setInterval(() => { if (Date.now() - lastProgress > WATCHDOG) failed = failed || new Error('сжатие остановилось'); }, 2000);
    try {
      for (let i = startIndex; i < v.samples.length; i++) {
        if (failed) throw failed;
        const s = v.samples[i];
        const t = s.pts * 1e6 / v.timescale;
        if (keptFrames >= limitFrames) break;
        if (t > trimEnd + 2e6 && s.key) break;
        const data = await Kov.MP4.readSample(file, s);
        decoder.decode(new EncodedVideoChunk({ type: s.key ? 'key' : 'delta', timestamp: Math.round(t), data }));
        while ((decoder.decodeQueueSize > 6 || encoder.encodeQueueSize > 6) && !failed) await new Promise(r => setTimeout(r, 5));
        if (muxer.video) {
          const videoTime = (i < v.samples.length ? t : Infinity);
          while (audioIndex < audioEnd && (all[audioIndex].dts * 1e6 / ats) <= videoTime) await pushAudio();
          await muxer.maybeFlush();
        }
        if (opts.onProgress && a.duration) opts.onProgress(Math.min(0.99, (t - trimStart) / 1e6 / Math.max(0.1, Math.min(a.duration, trimEnd / 1e6) - trimStart / 1e6)));
      }
      await decoder.flush();
      await encoder.flush();
      if (failed) throw failed;
      if (!muxer.video) throw new Error('кодировщик не выдал ни одного кадра');
      while (audioIndex < audioEnd) await pushAudio();
      await muxer.finish();
      return { frames: keptFrames, encoded: encodedCount };
    } finally {
      clearInterval(watchdog);
      try { decoder.close(); } catch (e) { /* */ }
      try { encoder.close(); } catch (e) { /* */ }
    }
  }

  // ── Решение: где и как обрабатывать ──
  async function plan(file, opts) {
    opts = opts || {};
    const out = { mode: 'server', reason: '' };
    if (!supported()) { out.reason = 'no-webcodecs'; return out; }
    let a;
    try { a = await analyze(file); } catch (e) { out.reason = 'demux: ' + e.message; return out; }
    out.analysis = a;
    if (a.audio && !AUDIO_PASS.includes(a.audio.entry.fourcc)) { out.reason = 'audio:' + a.audio.entry.fourcc; return out; }
    // несколько звуковых дорожек (редкость): сжимаем на устройстве с первой, а не отдаём серверу целиком
    if (!opts.codec && !Kov.VideoPrep.forceCodec && alreadyReady(a, opts.edits)) { out.mode = 'as-is'; out.reason = 'ready'; return out; }
    const decoder = await decoderConfig(a).catch(() => null);
    if (!decoder) { out.reason = 'decoder:' + a.video.entry.codec; return out; }
    const fps = Math.min(MAX_FPS, Math.max(1, Math.round(a.fps)));
    const memory = navigator.deviceMemory || 8;
    const edited = opts.edits && (opts.edits.trim_end || opts.edits.trim_start) ? Math.max(0.5, (Number(opts.edits.trim_end) || a.duration) - (Number(opts.edits.trim_start) || 0)) : a.duration;
    const sides = memory <= 2 || edited > LONG_VIDEO_S ? [HD_SIDE] : [FULL_SIDE, HD_SIDE];
    for (const side of sides) {
      const [w, h] = outputSize(a, side, opts.edits);
      if (side === HD_SIDE && Math.max(w, h) < Math.min(HD_SIDE, Math.max(a.width, a.height))) continue;
      let config = await encoderConfig(w, h, fps, opts.codec || Kov.VideoPrep.forceCodec);
      if (!config) continue;
      const trial = { decoder, config, width: w, height: h, fps, benchmark: true, benchmarkFrames: Math.min(45, Math.max(10, Math.round(fps * 1.5))) };
      let t0 = performance.now();
      try { await run(file, a, trial, () => {}, { edits: opts.edits }); } catch (e) {
        // аппаратный кодировщик согласился в проверке, но отказал в работе — тот же размер без него
        if (!config.hardwareAcceleration) continue;
        config = Object.assign({}, config); delete config.hardwareAcceleration;
        trial.config = config; t0 = performance.now();
        try { await run(file, a, trial, () => {}, { edits: opts.edits }); } catch (e2) { continue; }
      }
      const seconds = (performance.now() - t0) / 1000;
      const rate = trial.benchmarkFrames / Math.max(0.05, seconds);
      const estimate = edited * fps / rate * 1.15;
      if (estimate <= (opts.timeLimit || TIME_LIMIT)) {
        return Object.assign(out, { mode: 'device', decoder, config, width: w, height: h, fps, estimate, rate, side });
      }
      out.reason = 'slow:' + Math.round(estimate) + 's';
    }
    if (!out.reason) out.reason = 'encoder';
    return out;
  }

  async function compress(file, p, sink, opts) {
    return run(file, p.analysis, p, sink, opts);
  }

  // ── Обложка без воспроизведения: кадр через перемотку ──
  function posterFromElement(file, maxSide) {
    return new Promise(resolve => {
      const video = document.createElement('video');
      const url = URL.createObjectURL(file);
      let done = false;
      const finish = value => { if (done) return; done = true; clearTimeout(timer); URL.revokeObjectURL(url); video.removeAttribute('src'); try { video.load(); } catch (e) { /* */ } resolve(value); };
      const timer = setTimeout(() => finish(null), 8000);
      video.muted = true; video.playsInline = true; video.preload = 'metadata';
      video.onloadedmetadata = () => { video.currentTime = Math.min(1, (video.duration || 0) / 2 || 0); };
      video.onseeked = () => {
        try {
          const [w, h] = evenFit(video.videoWidth, video.videoHeight, maxSide || 640);
          const canvas = document.createElement('canvas');
          canvas.width = w; canvas.height = h;
          canvas.getContext('2d').drawImage(video, 0, 0, w, h);
          finish({ canvas, width: video.videoWidth, height: video.videoHeight, duration: video.duration });
        } catch (e) { finish(null); }
      };
      video.onerror = () => finish(null);
      video.src = url;
    });
  }

  return { supported, analyze, plan, compress, posterFromElement, alreadyReady, avcCodec, evenFit, TIME_LIMIT };
})();
