// Эталон для сверки Java-кода с веб-версией. Запускается из make_fixtures.py.
//   node dump_reference.js <папка образцов>
'use strict';
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { makeContext, load } = require('./webmods');

const dir = process.argv[2];
const out = {};

const hex = u8 => u8 ? Buffer.from(u8).toString('hex') : null;
const sha = s => crypto.createHash('sha256').update(s).digest('hex');
const blobOf = f => new Blob([fs.readFileSync(path.join(dir, f))]);

async function main() {
  const ctx = makeContext();
  load(ctx, 'js/media/mp4.js');
  load(ctx, 'js/media/hash.js');
  load(ctx, 'js/media/image-prep.js');
  // внутренние функции video-prep нужны для сверки формул — открываем их, не меняя логики
  load(ctx, 'js/media/video-prep.js', src => {
    const from = 'return { supported, analyze, plan, compress, posterFromElement, alreadyReady, avcCodec, evenFit, TIME_LIMIT };';
    if (!src.includes(from)) throw new Error('video-prep.js изменился — обновите dump_reference.js');
    return src.replace(from, 'return { supported, analyze, plan, compress, posterFromElement, alreadyReady, avcCodec, evenFit, TIME_LIMIT, outputSize, fpsOf, encoderConfig };');
  });
  const Kov = ctx.Kov;

  // ── разбор MP4 ──
  out.demux = {};
  const videos = JSON.parse(fs.readFileSync(path.join(dir, 'videos.json'), 'utf8'));
  for (const f of videos) {
    try {
      const info = await Kov.MP4.demux(blobOf(f));
      const tracks = info.tracks.map(t => {
        let rows = '';
        t.samples.forEach(s => { rows += s.offset + ',' + s.size + ',' + s.dts + ',' + s.pts + ',' + (s.key ? 1 : 0) + ';'; });
        const e = t.entry || {};
        return {
          id: t.id, handler: t.handler, timescale: t.timescale, duration: t.duration, rotation: t.rotation, flip: t.flip, shift: t.shift,
          count: t.samples.length, samplesSha: sha(rows), first: t.samples.slice(0, 3),
          entry: { fourcc: e.fourcc, codec: e.codec || null, family: e.family || null, width: e.width || 0, height: e.height || 0,
            channels: e.channels || 0, sampleRate: e.sampleRate || 0, raw: hex(e.raw), description: hex(e.description), asc: hex(e.asc), vpcC: hex(e.vpcC) },
        };
      });
      const a = await Kov.VideoPrep.analyze(blobOf(f)).catch(err => ({ error: String(err.message || err) }));
      let analysis = null;
      if (!a.error) {
        analysis = { width: a.width, height: a.height, fps: a.fps, duration: a.duration, bitrate: a.bitrate, frames: a.frames,
          ready: Kov.VideoPrep.alreadyReady(a, null),
          readyTrim: Kov.VideoPrep.alreadyReady(a, { trim_start: 0.5 }),
          readyRotate: Kov.VideoPrep.alreadyReady(a, { rotate: 90 }),
          readyCrop: Kov.VideoPrep.alreadyReady(a, { crop: { x: 0.1, y: 0.1, w: 0.5, h: 0.5 } }),
          outFull: Kov.VideoPrep.outputSize(a, 1920, null),
          outHd: Kov.VideoPrep.outputSize(a, 1280, null),
          outRot: Kov.VideoPrep.outputSize(a, 1920, { rotate: 90 }),
          outCrop: Kov.VideoPrep.outputSize(a, 1280, { crop: { x: 0.25, y: 0.1, w: 0.5, h: 0.6 } }),
          outBoth: Kov.VideoPrep.outputSize(a, 1920, { rotate: 270, crop: { x: 0, y: 0.2, w: 0.7, h: 0.5 } }),
        };
      }
      out.demux[f] = { ok: true, duration: info.duration, audioTracks: info.audioTracks, top: info.top,
        video: info.video ? info.video.id : null, audio: info.audio ? info.audio.id : null, tracks, analysis };
    } catch (e) {
      out.demux[f] = { ok: false, error: String(e.message || e) };
    }
  }

  // ── формулы video-prep ──
  out.formulas = {
    evenFit: [[1920, 1080, 1920], [1080, 1920, 1280], [3840, 2160, 1920], [641, 359, 1280], [1, 1, 1920], [4000, 3000, 1280], [1279, 721, 1280]]
      .map(([w, h, s]) => [w, h, s, Kov.VideoPrep.evenFit(w, h, s)]),
    avcCodec: [[1920, 1080, 30], [1280, 720, 30], [3840, 2160, 30], [640, 360, 25], [1920, 1088, 60], [2560, 1440, 30]]
      .flatMap(([w, h, f]) => ['high', 'main', 'base'].map(p => [w, h, f, p, Kov.VideoPrep.avcCodec(w, h, f, p)])),
  };

  // ── отпечатки файлов ──
  out.hash = {};
  const blobs = JSON.parse(fs.readFileSync(path.join(dir, 'blobs.json'), 'utf8'));
  for (const f of blobs) {
    const b = blobOf(f);
    const ranges = [[0, 1], [0, 65536], [b.size > 10 ? b.size - 7 : 0, 7], [Math.floor(b.size / 3), 12345], [b.size, 5], [b.size + 10, 3]];
    out.hash[f] = { size: b.size, tree: await Kov.FileHash.tree(b), soft: hex(Kov.FileHash.sha256Soft(new Uint8Array(await b.slice(0, 100000).arrayBuffer()))),
      ranges: await Promise.all(ranges.map(async ([o, l]) => [o, l, await Kov.FileHash.range(b, o, l)])) };
  }

  // ── размеры картинок по заголовку ──
  out.dims = {};
  const images = JSON.parse(fs.readFileSync(path.join(dir, 'images.json'), 'utf8'));
  for (const f of images) {
    const head = new Uint8Array(await blobOf(f).slice(0, 256 * 1024).arrayBuffer());
    out.dims[f] = Kov.ImagePrep.dims(head);
  }
  out.fit = [[4000, 3000, 3200], [3000, 4000, 3200], [3200, 100, 3200], [100, 50, 320], [5000, 1, 100], [640, 480, 100], [321, 320, 320]]
    .map(([w, h, s]) => [w, h, s, Kov.ImagePrep.fit(w, h, s)]);

  // ── запись фрагментированного MP4: одна и та же последовательность для JS и Java ──
  const ops = [];
  const rnd = (i, n) => { const a = new Uint8Array(n); for (let k = 0; k < n; k++) a[k] = (i * 131 + k * 17 + (k >> 3)) & 255; return a; };
  const desc = rnd(7, 40);
  const entryRaw = rnd(9, 36);
  // порядок декодирования с B-кадрами: I P B B P B B ...
  const order = [];
  for (let g = 0; g < 5; g++) {
    const b = g * 30;
    order.push(b);
    let k = 1;
    while (k + 2 <= 29) { order.push(b + k + 2, b + k, b + k + 1); k += 3; }
    while (k <= 29) { order.push(b + k); k++; }
  }
  const clean = order;
  let audioT = 0;
  clean.forEach((idx, n) => {
    const pts = Math.round(idx * 1e6 / 30);
    ops.push(['v', hex(rnd(idx, 50 + (idx % 13))), pts, idx % 30 === 0]);
    while (audioT * 1024 / 48000 * 1e6 <= pts) { ops.push(['a', hex(rnd(1000 + audioT, 20 + (audioT % 5))), audioT * 1024, 1024]); audioT++; }
    ops.push(['f']);
  });
  const chunks = [];
  const m = new Kov.MP4.Muxer(bytes => { chunks.push(Buffer.from(bytes)); }, { fragmentSeconds: 2 });
  m.addVideo({ codec: 'avc1.64001f', width: 640, height: 360, description: desc });
  m.addAudio({ timescale: 48000, entryRaw });
  for (const op of ops) {
    if (op[0] === 'v') m.addVideoChunk(Buffer.from(op[1], 'hex'), op[2], op[3]);
    else if (op[0] === 'a') m.addAudioSample(Buffer.from(op[1], 'hex'), op[2], op[3]);
    else await m.maybeFlush();
  }
  await m.finish();
  out.mux = { description: hex(desc), entryRaw: hex(entryRaw), ops, chunks: chunks.map(c => c.length), sha: sha(Buffer.concat(chunks)) };
  fs.writeFileSync(path.join(dir, 'mux_js.mp4'), Buffer.concat(chunks));

  fs.writeFileSync(path.join(dir, 'reference.json'), JSON.stringify(out, null, 1));
  console.log('эталон записан:', Object.keys(out.demux).length, 'видео,', Object.keys(out.hash).length, 'файлов,', Object.keys(out.dims).length, 'картинок');
}

main().catch(e => { console.error(e); process.exit(1); });
