(function (root) {
  const Kov = root.Kov = root.Kov || {};

  // ── Чтение MP4/MOV без загрузки файла целиком ──
  const td = new TextDecoder('latin1');
  const typeAt = (b, o) => td.decode(b.subarray(o, o + 4));
  const u32 = (b, o) => ((b[o] << 24) | (b[o + 1] << 16) | (b[o + 2] << 8) | b[o + 3]) >>> 0;
  const u16 = (b, o) => (b[o] << 8) | b[o + 1];
  const i32 = (b, o) => (b[o] << 24) | (b[o + 1] << 16) | (b[o + 2] << 8) | b[o + 3];
  const u64 = (b, o) => u32(b, o) * 4294967296 + u32(b, o + 4);

  async function readAt(blob, offset, length) {
    return new Uint8Array(await blob.slice(offset, offset + length).arrayBuffer());
  }

  function children(b, start, end) {
    const out = [];
    let o = start;
    while (o + 8 <= end) {
      let size = u32(b, o), head = 8;
      const type = typeAt(b, o + 4);
      if (size === 1) { size = u64(b, o + 8); head = 16; } else if (size === 0) size = end - o;
      if (size < head || o + size > end) break;
      out.push({ type, start: o, body: o + head, end: o + size });
      o += size;
    }
    return out;
  }
  const find = (b, box, type) => children(b, box.body, box.end).find(c => c.type === type);
  const path = (b, box, types) => types.reduce((cur, t) => cur && find(b, cur, t), box);

  function descriptorLen(b, o) {
    let len = 0, n = 0;
    for (; n < 4; n++) { const c = b[o + n]; len = (len << 7) | (c & 0x7f); if (!(c & 0x80)) { n++; break; } }
    return [len, n];
  }

  function esdsInfo(b, box) {
    let o = box.body + 4;
    const read = () => { const tag = b[o]; const [len, n] = descriptorLen(b, o + 1); const at = o + 1 + n; return { tag, len, at }; };
    let d = read();
    if (d.tag !== 3) return null;
    o = d.at + 3;
    const flags = b[d.at + 2];
    if (flags & 0x80) o += 2;
    if (flags & 0x40) o += 1 + b[o];
    if (flags & 0x20) o += 2;
    d = read();
    if (d.tag !== 4) return null;
    const oti = b[d.at];
    o = d.at + 13;
    d = read();
    const asc = d.tag === 5 ? b.slice(d.at, d.at + d.len) : null;
    let aot = asc && asc.length ? asc[0] >> 3 : 2;
    if (aot === 31 && asc.length > 1) aot = 32 + (((asc[0] & 7) << 3) | (asc[1] >> 5));
    return { oti, asc, codec: oti === 0x6B || oti === 0x69 ? 'mp3' : 'mp4a.40.' + aot };
  }

  function hex2(n) { return n.toString(16).padStart(2, '0'); }

  function hevcCodec(c, fourcc) {
    const profileSpace = ['', 'A', 'B', 'C'][c[1] >> 6];
    const tier = (c[1] >> 5) & 1 ? 'H' : 'L';
    const profile = c[1] & 0x1f;
    let compat = u32(c, 2), rev = 0;
    for (let i = 0; i < 32; i++) { rev = (rev << 1) | (compat & 1); compat >>>= 1; }
    const level = c[12];
    const cons = [c[6], c[7], c[8], c[9], c[10], c[11]];
    while (cons.length && cons[cons.length - 1] === 0) cons.pop();
    return [fourcc, profileSpace + profile, (rev >>> 0).toString(16).toUpperCase(), tier + level].concat(cons.map(x => x.toString(16).toUpperCase())).join('.');
  }

  function sampleEntry(b, stsd, handler) {
    const entry = children(b, stsd.body + 8, stsd.end)[0];
    if (!entry) return null;
    const out = { fourcc: entry.type, raw: b.slice(entry.start, entry.end) };
    if (handler === 'vide') {
      out.width = u16(b, entry.body + 24); out.height = u16(b, entry.body + 26);
      const kids = children(b, entry.body + 78, entry.end);
      const cfg = t => kids.find(k => k.type === t);
      const avcC = cfg('avcC'), hvcC = cfg('hvcC'), vpcC = cfg('vpcC'), av1C = cfg('av1C');
      if (avcC) { out.description = b.slice(avcC.body, avcC.end); out.codec = entry.type + '.' + hex2(b[avcC.body + 1]) + hex2(b[avcC.body + 2]) + hex2(b[avcC.body + 3]); out.family = 'avc'; }
      else if (hvcC) { out.description = b.slice(hvcC.body, hvcC.end); out.codec = hevcCodec(out.description, entry.type); out.family = 'hevc'; }
      else if (vpcC) { const v = vpcC.body + 4; out.codec = 'vp09.' + String(b[v]).padStart(2, '0') + '.' + String(b[v + 1]).padStart(2, '0') + '.' + String(b[v + 2] >> 4).padStart(2, '0'); out.family = 'vp9'; out.vpcC = b.slice(vpcC.body, vpcC.end); }
      else if (av1C) { out.description = b.slice(av1C.body, av1C.end); const s = out.description; const profile = s[1] >> 5, level = s[1] & 31, tier = s[2] >> 7 ? 'H' : 'M', hbd = (s[2] >> 6) & 1, twelve = (s[2] >> 5) & 1; out.codec = 'av01.' + profile + '.' + String(level).padStart(2, '0') + tier + '.' + (hbd ? (twelve ? '12' : '10') : '08'); out.family = 'av1'; }
      else if (entry.type === 'vp08') { out.codec = 'vp8'; out.family = 'vp8'; }
      else out.family = 'unknown';
    } else if (handler === 'soun') {
      const version = u16(b, entry.body + 8);
      out.channels = u16(b, entry.body + 16);
      out.sampleRate = u32(b, entry.body + 24) >>> 16;
      const skip = version === 1 ? 16 : version === 2 ? 36 : 0;
      const kids = children(b, entry.body + 28 + skip, entry.end);
      const esds = kids.find(k => k.type === 'esds');
      if (entry.type === 'mp4a' && esds) { const e = esdsInfo(b, esds); if (e) { out.codec = e.codec; out.asc = e.asc; } }
      else if (entry.type === 'Opus') out.codec = 'opus';
      else out.codec = entry.type;
    }
    return out;
  }

  function parseTrack(b, trak) {
    const tkhd = find(b, trak, 'tkhd');
    const mdhd = path(b, trak, ['mdia', 'mdhd']);
    const hdlr = path(b, trak, ['mdia', 'hdlr']);
    const stbl = path(b, trak, ['mdia', 'minf', 'stbl']);
    if (!tkhd || !mdhd || !hdlr || !stbl) return null;
    const handler = typeAt(b, hdlr.body + 8);
    const tv = b[tkhd.body];
    const trackId = u32(b, tkhd.body + (tv ? 20 : 12));
    const matrixAt = tkhd.body + (tv ? 52 : 40);
    const ma = i32(b, matrixAt), mb = i32(b, matrixAt + 4), mc = i32(b, matrixAt + 12), md = i32(b, matrixAt + 16);
    let rotation = Math.round(Math.atan2(mb, ma) * 180 / Math.PI);
    rotation = ((rotation % 360) + 360) % 360;
    const flip = ma * md - mb * mc < 0;
    const mv = b[mdhd.body];
    const timescale = u32(b, mdhd.body + (mv ? 20 : 12));
    const mediaDuration = mv ? u64(b, mdhd.body + 24) : u32(b, mdhd.body + 16);
    const stsd = find(b, stbl, 'stsd');
    const entry = stsd ? sampleEntry(b, stsd, handler) : null;
    const box = t => find(b, stbl, t);
    const stts = box('stts'), ctts = box('ctts'), stss = box('stss'), stsc = box('stsc'), stsz = box('stsz'), stz2 = box('stz2'), stco = box('stco'), co64 = box('co64');
    const sizes = [];
    if (stsz) { const fixed = u32(b, stsz.body + 4), n = u32(b, stsz.body + 8); for (let i = 0; i < n; i++) sizes.push(fixed || u32(b, stsz.body + 12 + i * 4)); }
    else if (stz2) { const f = b[stz2.body + 7], n = u32(b, stz2.body + 8); for (let i = 0; i < n; i++) sizes.push(f === 16 ? u16(b, stz2.body + 12 + i * 2) : f === 8 ? b[stz2.body + 12 + i] : (b[stz2.body + 12 + (i >> 1)] >> (i & 1 ? 0 : 4)) & 15); }
    const count = sizes.length;
    const chunks = [];
    if (stco) { const n = u32(b, stco.body + 4); for (let i = 0; i < n; i++) chunks.push(u32(b, stco.body + 8 + i * 4)); }
    else if (co64) { const n = u32(b, co64.body + 4); for (let i = 0; i < n; i++) chunks.push(u64(b, co64.body + 8 + i * 8)); }
    const offsets = new Array(count);
    if (stsc) {
      const n = u32(b, stsc.body + 4);
      const runs = [];
      for (let i = 0; i < n; i++) runs.push([u32(b, stsc.body + 8 + i * 12), u32(b, stsc.body + 12 + i * 12)]);
      let s = 0;
      for (let r = 0; r < runs.length && s < count; r++) {
        const last = r + 1 < runs.length ? runs[r + 1][0] - 1 : chunks.length;
        for (let c = runs[r][0]; c <= last && s < count; c++) {
          let off = chunks[c - 1];
          for (let k = 0; k < runs[r][1] && s < count; k++) { offsets[s] = off; off += sizes[s]; s++; }
        }
      }
    }
    const dts = new Array(count), cts = new Array(count).fill(0);
    if (stts) { const n = u32(b, stts.body + 4); let s = 0, t = 0; for (let i = 0; i < n; i++) { const c = u32(b, stts.body + 8 + i * 8), d = u32(b, stts.body + 12 + i * 8); for (let k = 0; k < c && s < count; k++) { dts[s++] = t; t += d; } } for (; s < count; s++) dts[s] = t; }
    if (ctts) { const v = b[ctts.body]; const n = u32(b, ctts.body + 4); let s = 0; for (let i = 0; i < n; i++) { const c = u32(b, ctts.body + 8 + i * 8); const off = v ? i32(b, ctts.body + 12 + i * 8) : u32(b, ctts.body + 12 + i * 8); for (let k = 0; k < c && s < count; k++) cts[s++] = off; } }
    let keys = null;
    if (stss) { keys = new Set(); const n = u32(b, stss.body + 4); for (let i = 0; i < n; i++) keys.add(u32(b, stss.body + 8 + i * 4) - 1); }
    let shift = 0;
    const elst = path(b, trak, ['edts', 'elst']);
    if (elst) {
      const v = b[elst.body], n = u32(b, elst.body + 4);
      for (let i = 0; i < n; i++) {
        const at = elst.body + 8 + i * (v ? 20 : 12);
        const mediaTime = v ? u64(b, at + 8) : i32(b, at + 4);
        if (mediaTime >= 0 && mediaTime !== 4294967295) { shift = mediaTime; break; }
      }
    }
    const samples = new Array(count);
    for (let i = 0; i < count; i++) {
      samples[i] = { offset: offsets[i], size: sizes[i], dts: dts[i], pts: dts[i] + cts[i] - shift, key: keys ? keys.has(i) : true };
    }
    return { id: trackId, handler, timescale, duration: mediaDuration / timescale, rotation, flip, entry, samples, shift };
  }

  async function demux(blob) {
    let offset = 0, moov = null;
    const top = [];
    while (offset + 8 <= blob.size) {
      const head = await readAt(blob, offset, 16);
      let size = u32(head, 0);
      const type = typeAt(head, 4);
      if (size === 1) size = u64(head, 8); else if (size === 0) size = blob.size - offset;
      if (size < 8) break;
      top.push(type);
      if (type === 'moov') {
        if (size > 256 * 1024 * 1024) throw new Error('moov слишком большой');
        moov = await readAt(blob, offset, size);
      }
      if (type === 'moof') throw new Error('фрагментированный MP4 не поддерживается');
      offset += size;
    }
    if (!moov) throw new Error('в файле нет описания дорожек (moov)');
    const root = { body: 8, end: moov.length };
    const tracks = children(moov, root.body, root.end).filter(c => c.type === 'trak').map(t => parseTrack(moov, t)).filter(Boolean);
    const video = tracks.find(t => t.handler === 'vide' && t.samples.length);
    const audio = tracks.filter(t => t.handler === 'soun' && t.samples.length);
    const mvhd = find(moov, root, 'mvhd');
    const mv = mvhd ? moov[mvhd.body] : 0;
    const duration = mvhd ? (mv ? u64(moov, mvhd.body + 24) : u32(moov, mvhd.body + 16)) / u32(moov, mvhd.body + (mv ? 20 : 12)) : (video ? video.duration : 0);
    return { tracks, video, audio: audio[0] || null, audioTracks: audio.length, duration, top };
  }

  async function readSample(blob, s) { return readAt(blob, s.offset, s.size); }

  // ── Запись фрагментированного MP4 ──
  function concat(parts) {
    let n = 0; for (const p of parts) n += p.length;
    const out = new Uint8Array(n); let o = 0;
    for (const p of parts) { out.set(p, o); o += p.length; }
    return out;
  }
  const be32 = v => new Uint8Array([(v >>> 24) & 255, (v >>> 16) & 255, (v >>> 8) & 255, v & 255]);
  const be16 = v => new Uint8Array([(v >>> 8) & 255, v & 255]);
  const be64 = v => { const hi = Math.floor(v / 4294967296), lo = v >>> 0; return concat([be32(hi), be32(lo)]); };
  const ascii = s => new Uint8Array(Array.from(s, c => c.charCodeAt(0)));
  const box = (type, ...parts) => { const body = concat(parts); return concat([be32(body.length + 8), ascii(type), body]); };
  const full = (type, version, flags, ...parts) => box(type, new Uint8Array([version, (flags >> 16) & 255, (flags >> 8) & 255, flags & 255]), ...parts);
  const zeros = n => new Uint8Array(n);
  const MATRIX = concat([be32(0x10000), be32(0), be32(0), be32(0), be32(0x10000), be32(0), be32(0), be32(0), be32(0x40000000)]);

  function vpcC(codec) {
    const p = codec.split('.');
    const profile = +p[1] || 0, level = +p[2] || 10, depth = +p[3] || 8;
    return full('vpcC', 1, 0, new Uint8Array([profile, level, (depth << 4) | (1 << 1), 1, 1, 1]), be16(0));
  }

  function videoEntry(t) {
    let cfg;
    if (t.codec.startsWith('avc')) cfg = box('avcC', t.description);
    else if (t.codec.startsWith('hvc') || t.codec.startsWith('hev')) cfg = box('hvcC', t.description);
    else if (t.codec.startsWith('vp09')) cfg = vpcC(t.codec);
    else if (t.codec.startsWith('av01')) cfg = box('av1C', t.description);
    const fourcc = t.codec.startsWith('avc') ? 'avc1' : t.codec.startsWith('hev') ? 'hev1' : t.codec.startsWith('hvc') ? 'hvc1' : t.codec.startsWith('vp09') ? 'vp09' : t.codec.startsWith('av01') ? 'av01' : 'vp08';
    return box(fourcc, zeros(6), be16(1), zeros(16), be16(t.width), be16(t.height), be32(0x00480000), be32(0x00480000), be32(0), be16(1), zeros(32), be16(0x18), be16(0xffff), cfg || zeros(0));
  }

  function trak(t) {
    const video = t.kind === 'video';
    const tkhd = full('tkhd', 0, 3, be32(0), be32(0), be32(t.id), be32(0), be32(0), zeros(8), be16(0), be16(0), be16(video ? 0 : 0x0100), be16(0), MATRIX,
      be32(video ? t.width << 16 : 0), be32(video ? t.height << 16 : 0));
    const mdhd = full('mdhd', 0, 0, be32(0), be32(0), be32(t.timescale), be32(0), be16(0x55c4), be16(0));
    const hdlr = full('hdlr', 0, 0, be32(0), ascii(video ? 'vide' : 'soun'), zeros(12), ascii(video ? 'VideoHandler' : 'SoundHandler'), zeros(1));
    const head = video ? full('vmhd', 0, 1, zeros(8)) : full('smhd', 0, 0, zeros(4));
    const dinf = box('dinf', full('dref', 0, 0, be32(1), full('url ', 0, 1)));
    const stsd = full('stsd', 0, 0, be32(1), video ? videoEntry(t) : t.entryRaw);
    const stbl = box('stbl', stsd, full('stts', 0, 0, be32(0)), full('stsc', 0, 0, be32(0)), full('stsz', 0, 0, be32(0), be32(0)), full('stco', 0, 0, be32(0)));
    return box('trak', tkhd, box('mdia', mdhd, hdlr, box('minf', head, dinf, stbl)));
  }

  class Muxer {
    constructor(onData, opts) {
      this.onData = onData;
      this.opts = opts || {};
      this.tracks = [];
      this.seq = 0;
      this.started = false;
      this.pending = { video: [], audio: [] };
      this.fragmentSeconds = this.opts.fragmentSeconds || 2;
    }
    addVideo(cfg) { const t = Object.assign({ kind: 'video', id: this.tracks.length + 1, timescale: 90000, samples: [], next: 0 }, cfg); this.tracks.push(t); this.video = t; return t; }
    addAudio(cfg) { const t = Object.assign({ kind: 'audio', id: this.tracks.length + 1, samples: [], next: 0 }, cfg); this.tracks.push(t); this.audio = t; return t; }
    async start() {
      if (this.started) return;
      this.started = true;
      const ftyp = box('ftyp', ascii('isom'), be32(0x200), ascii('isom'), ascii('iso6'), ascii('mp41'));
      const mvhd = full('mvhd', 0, 0, be32(0), be32(0), be32(1000), be32(0), be32(0x10000), be16(0x0100), zeros(10), MATRIX, zeros(24), be32(this.tracks.length + 1));
      const mvex = box('mvex', ...this.tracks.map(t => full('trex', 0, 0, be32(t.id), be32(1), be32(0), be32(0), be32(0))));
      await this.onData(concat([ftyp, box('moov', mvhd, ...this.tracks.map(trak), mvex)]));
    }
    addVideoChunk(data, ptsUs, key) {
      const t = this.video;
      const pts = Math.round(ptsUs * t.timescale / 1e6);
      if (key && t.gop && t.gop.length) this.closeGop();
      t.gop = t.gop || [];
      t.gop.push({ data, pts, key });
    }
    closeGop() {
      const t = this.video;
      const gop = t.gop || [];
      if (!gop.length) return;
      const sorted = gop.map(s => s.pts).sort((a, b) => a - b);
      gop.forEach((s, i) => { s.dts = sorted[i]; });
      for (const s of gop) t.samples.push(s);
      t.gop = [];
    }
    addAudioSample(data, dts, duration) { this.audio.samples.push({ data, dts, pts: dts, duration, key: true }); }
    videoSeconds() { const t = this.video; return t.samples.length ? (t.samples[t.samples.length - 1].dts - t.samples[0].dts) / t.timescale : 0; }
    async maybeFlush() { if (this.video && this.videoSeconds() >= this.fragmentSeconds) await this.flush(false); }
    async flush(final) {
      await this.start();
      if (final && this.video) this.closeGop();
      const trafs = [], payloads = [];
      const vt = this.video;
      let cut = Infinity;
      if (vt && vt.samples.length) {
        let take = vt.samples.length;
        if (!final) { take = vt.samples.length - 1; }
        const chunk = vt.samples.splice(0, take);
        if (!final && vt.samples.length) vt.hold = vt.samples[0];
        if (chunk.length) {
          const next = vt.samples.length ? vt.samples[0].dts : null;
          for (let i = 0; i < chunk.length; i++) {
            const nd = i + 1 < chunk.length ? chunk[i + 1].dts : (next != null ? next : chunk[i].dts + (vt.lastDuration || Math.round(vt.timescale / 30)));
            chunk[i].duration = Math.max(1, nd - chunk[i].dts);
            vt.lastDuration = chunk[i].duration;
          }
          cut = (chunk[chunk.length - 1].dts + chunk[chunk.length - 1].duration) / vt.timescale;
          trafs.push({ t: vt, samples: chunk });
        }
      }
      const at = this.audio;
      if (at && at.samples.length) {
        const limit = final ? Infinity : cut;
        let n = 0;
        while (n < at.samples.length && (at.samples[n].dts / at.timescale) < limit) n++;
        if (n) trafs.push({ t: at, samples: at.samples.splice(0, n) });
      }
      if (!trafs.length) return;
      this.seq += 1;
      const build = dataOffsets => {
        const parts = [full('mfhd', 0, 0, be32(this.seq))];
        trafs.forEach((f, k) => {
          const video = f.t.kind === 'video';
          const tfhd = full('tfhd', 0, 0x020000, be32(f.t.id));
          const tfdt = full('tfdt', 1, 0, be64(f.samples[0].dts));
          const flags = 0x000001 | 0x000100 | 0x000200 | (video ? 0x000400 | 0x000800 : 0);
          const rows = f.samples.map(s => concat(video
            ? [be32(s.duration), be32(s.data.length), be32(s.key ? 0x02000000 : 0x01010000), be32((s.pts - s.dts) | 0)]
            : [be32(s.duration), be32(s.data.length)]));
          const trun = full('trun', video ? 1 : 0, flags, be32(f.samples.length), be32(dataOffsets[k]), ...rows);
          parts.push(box('traf', tfhd, tfdt, trun));
        });
        return box('moof', ...parts);
      };
      let moof = build(trafs.map(() => 0));
      const offsets = [];
      let pos = moof.length + 8;
      trafs.forEach(f => { offsets.push(pos); for (const s of f.samples) pos += s.data.length; });
      moof = build(offsets);
      const body = [];
      trafs.forEach(f => f.samples.forEach(s => body.push(s.data)));
      const mdatLen = body.reduce((n, p) => n + p.length, 0) + 8;
      await this.onData(concat([moof, be32(mdatLen), ascii('mdat'), ...body]));
    }
    async finish() { await this.flush(true); }
  }

  Kov.MP4 = { demux, readSample, Muxer, hevcCodec };
})(typeof window !== 'undefined' ? window : globalThis);
