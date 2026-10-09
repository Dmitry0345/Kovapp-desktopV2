/* Показ PowerPoint (pptx) на устройстве: слайды с фоном, фигурами, текстом, картинками и таблицами */
(function (root) {
  const Kov = root.Kov = root.Kov || {};
  const { kids, kid, path, att, all, rels, byType } = Kov.Zip.x;
  const C = Kov.Zip.color;
  const EMU = 9525, PT = 4 / 3;
  const PRST = { black: '#000000', white: '#FFFFFF', red: '#FF0000', green: '#008000', blue: '#0000FF', yellow: '#FFFF00', gray: '#808080', grey: '#808080',
    darkGray: '#A9A9A9', lightGray: '#D3D3D3', orange: '#FFA500', navy: '#000080', purple: '#800080', silver: '#C0C0C0', maroon: '#800000', teal: '#008080', olive: '#808000' };
  const CLIP = { triangle: 'polygon(50% 0,100% 100%,0 100%)', rtTriangle: 'polygon(0 0,100% 100%,0 100%)', diamond: 'polygon(50% 0,100% 50%,50% 100%,0 50%)',
    rightArrow: 'polygon(0 25%,60% 25%,60% 0,100% 50%,60% 100%,60% 75%,0 75%)', leftArrow: 'polygon(100% 25%,40% 25%,40% 0,0 50%,40% 100%,40% 75%,100% 75%)',
    upArrow: 'polygon(25% 100%,25% 40%,0 40%,50% 0,100% 40%,75% 40%,75% 100%)', downArrow: 'polygon(25% 0,75% 0,75% 60%,100% 60%,50% 100%,0 60%,25% 60%)',
    homePlate: 'polygon(0 0,80% 0,100% 50%,80% 100%,0 100%)', chevron: 'polygon(0 0,80% 0,100% 50%,80% 100%,0 100%,20% 50%)',
    hexagon: 'polygon(25% 0,75% 0,100% 50%,75% 100%,25% 100%,0 50%)', octagon: 'polygon(30% 0,70% 0,100% 30%,100% 70%,70% 100%,30% 100%,0 70%,0 30%)',
    parallelogram: 'polygon(25% 0,100% 0,75% 100%,0 100%)', trapezoid: 'polygon(25% 0,75% 0,100% 100%,0 100%)', pentagon: 'polygon(50% 0,100% 38%,82% 100%,18% 100%,0 38%)',
    star5: 'polygon(50% 0,61% 35%,98% 35%,68% 57%,79% 91%,50% 70%,21% 91%,32% 57%,2% 35%,39% 35%)' };
  const WING = { '§': '▪', 'Ø': '➢', 'ü': '✓', 'l': '●', 'q': '❑', 'n': '■', 'v': '❖', 'Ÿ': '•', 'o': '○', 'p': '◆', 'ª': '▪' };

  function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  const fam = name => '"' + String(name || 'Calibri').replace(/"/g, '') + '", "Carlito", "Liberation Sans", Arial, sans-serif';

  // ── Цвет, заливка, линия ──
  function colorOf(holder, th, ph) {
    if (!holder) return null;
    const c = /Clr$/.test(holder.localName) ? holder : Array.from(holder.children).find(x => /Clr$/.test(x.localName));
    if (!c) return null;
    let hex = null;
    switch (c.localName) {
      case 'srgbClr': hex = '#' + att(c, 'val'); break;
      case 'schemeClr': { const v = att(c, 'val'); if (v === 'phClr' && ph) hex = ph; else { const k = v === 'phClr' ? 'tx1' : v; hex = th.colors[th.map[k] || k] || th.colors[k] || null; } break; }
      case 'sysClr': hex = '#' + (att(c, 'lastClr') || (att(c, 'val') === 'window' ? 'FFFFFF' : '000000')); break;
      case 'prstClr': hex = PRST[att(c, 'val')] || null; break;
      case 'scrgbClr': hex = C.rgbHex(['r', 'g', 'b'].map(k => Math.pow(Math.max(0, +att(c, k)) / 100000, 1 / 2.2) * 255)); break;
    }
    if (!hex || hex.length !== 7) return null;
    let alpha = 1, mod = 1, off = 0, lum = false;
    for (const m of c.children) {
      const v = +att(m, 'val') / 100000;
      if (m.localName === 'lumMod') { mod = v; lum = true; }
      else if (m.localName === 'lumOff') { off = v; lum = true; }
      else if (m.localName === 'tint') hex = C.mix(hex, '#FFFFFF', v);
      else if (m.localName === 'shade') hex = C.mix(hex, '#000000', v);
      else if (m.localName === 'alpha') alpha = v;
    }
    if (lum) hex = C.lum(hex, mod, off);
    if (alpha < 1) { const [r, g, b] = C.hexRgb(hex.slice(1)); return 'rgba(' + r + ',' + g + ',' + b + ',' + alpha.toFixed(3) + ')'; }
    return hex;
  }
  async function fillOf(pr, ctx, part, style) {
    if (pr) for (const f of pr.children) {
      switch (f.localName) {
        case 'noFill': return 'transparent';
        case 'solidFill': return colorOf(f, ctx.th);
        case 'pattFill': return colorOf(kid(f, 'fgClr'), ctx.th);
        case 'gradFill': {
          const stops = all(f, 'gs').map(g => (colorOf(g, ctx.th) || '#FFFFFF') + ' ' + (+att(g, 'pos') / 1000) + '%');
          const lin = kid(f, 'lin');
          if (!stops.length) return null;
          return 'linear-gradient(' + ((lin ? +att(lin, 'ang') / 60000 : 90) + 90) + 'deg, ' + stops.join(', ') + ')';
        }
        case 'blipFill': {
          const b = all(f, 'blip')[0], rel = b && part.rels[att(b, 'embed')];
          if (rel && !rel.external) return 'center / cover no-repeat url("' + await ctx.zip.blobUrl(rel.target) + '")';
          return null;
        }
      }
    }
    const ref = style && kid(style, 'fillRef');
    if (ref && +att(ref, 'idx') > 0) return colorOf(ref, ctx.th);
    return null;
  }
  function lineOf(pr, ctx, style) {
    const ln = kid(pr, 'ln');
    const ref = style && kid(style, 'lnRef');
    if (ln && kid(ln, 'noFill')) return null;
    const c = (ln && colorOf(kid(ln, 'solidFill'), ctx.th)) || (ref && +att(ref, 'idx') > 0 ? colorOf(ref, ctx.th) : null);
    if (!c) return null;
    const dash = ln && att(kid(ln, 'prstDash'), 'val');
    return { w: Math.max(1, (ln && +att(ln, 'w') ? +att(ln, 'w') : 12700) / EMU), c, dash: !dash || dash === 'solid' ? 'solid' : /dot/i.test(dash) ? 'dotted' : 'dashed' };
  }

  // ── Положение и заполнители ──
  function xfrmOf(x) {
    const off = kid(x, 'off'), ext = kid(x, 'ext');
    if (!off || !ext) return null;
    return { x: +att(off, 'x'), y: +att(off, 'y'), w: +att(ext, 'cx'), h: +att(ext, 'cy'), rot: (+att(x, 'rot') || 0) / 60000,
      fh: att(x, 'flipH') === '1', fv: att(x, 'flipV') === '1', chOff: kid(x, 'chOff'), chExt: kid(x, 'chExt') };
  }
  function phInfo(sp) {
    const nv = Array.from(sp.children).find(c => /^nv/.test(c.localName));
    const ph = nv && path(nv, 'nvPr', 'ph');
    return ph ? { type: att(ph, 'type') || 'body', idx: att(ph, 'idx') } : null;
  }
  const group = t => t === 'ctrTitle' || t === 'title' ? 'title' : ['subTitle', 'body', 'obj', 'tbl', 'chart', 'pic', 'media', 'clipArt', 'dgm'].includes(t) ? 'body' : t;
  function findPh(tree, ph, byIdx) {
    if (!tree || !ph) return null;
    const list = all(tree, 'sp').filter(s => phInfo(s));
    if (byIdx && ph.idx != null) { const m = list.find(s => phInfo(s).idx === ph.idx); if (m) return m; }
    return list.find(s => phInfo(s).type === ph.type) || list.find(s => group(phInfo(s).type) === group(ph.type)) || null;
  }

  // ── Текст ──
  function mergeLvl(P, e, th) {
    if (!e) return;
    const a = att(e, 'algn'); if (a) P.algn = a;
    if (att(e, 'marL') != null) P.marL = +att(e, 'marL') / EMU;
    if (att(e, 'indent') != null) P.indent = +att(e, 'indent') / EMU;
    const sp = (n, k) => { const s = kid(e, n); if (!s) return; const pts = kid(s, 'spcPts'), pct = kid(s, 'spcPct'); if (pts) P[k] = { px: +att(pts, 'val') / 100 * PT }; else if (pct) P[k] = { em: +att(pct, 'val') / 100000 }; };
    sp('lnSpc', 'ln'); sp('spcBef', 'bef'); sp('spcAft', 'aft');
    if (kid(e, 'buNone')) P.bu = null;
    if (kid(e, 'buChar')) P.bu = { ch: att(kid(e, 'buChar'), 'char') || '•' };
    if (kid(e, 'buAutoNum')) P.bu = { auto: att(kid(e, 'buAutoNum'), 'type') || 'arabicPeriod', start: +(att(kid(e, 'buAutoNum'), 'startAt') || 1) };
    if (kid(e, 'buFont')) P.buFont = att(kid(e, 'buFont'), 'typeface');
    if (kid(e, 'buClr')) P.buClr = colorOf(kid(e, 'buClr'), th);
    if (kid(e, 'buSzPct')) P.buSz = +att(kid(e, 'buSzPct'), 'val') / 100000;
    mergeRun(P, kid(e, 'defRPr'), th);
  }
  function mergeRun(P, r, th) {
    if (!r) return;
    if (att(r, 'sz')) P.sz = +att(r, 'sz') / 100;
    if (att(r, 'b') != null) P.b = att(r, 'b') === '1';
    if (att(r, 'i') != null) P.i = att(r, 'i') === '1';
    if (att(r, 'u') != null) P.u = att(r, 'u') !== 'none';
    if (att(r, 'strike') != null) P.s = att(r, 'strike') !== 'noStrike';
    if (att(r, 'cap') != null) P.cap = att(r, 'cap');
    if (att(r, 'baseline') != null) P.base = +att(r, 'baseline');
    const f = kid(r, 'solidFill'); if (f) P.color = colorOf(f, th);
    const l = kid(r, 'latin'); if (l && att(l, 'typeface')) P.font = att(l, 'typeface');
  }
  function autoNum(type, n) {
    const alpha = s => { let o = ''; while (n > 0) { n--; o = s[n % 26] + o; n = Math.floor(n / 26); } return o; };
    const rom = v => [[1000, 'm'], [900, 'cm'], [500, 'd'], [400, 'cd'], [100, 'c'], [90, 'xc'], [50, 'l'], [40, 'xl'], [10, 'x'], [9, 'ix'], [5, 'v'], [4, 'iv'], [1, 'i']].reduce((o, [k, r]) => { while (v >= k) { o += r; v -= k; } return o; }, '');
    let s = /alpha/i.test(type) ? alpha(/Uc/.test(type) ? 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' : 'abcdefghijklmnopqrstuvwxyz') : /roman/i.test(type) ? (/Uc/.test(type) ? rom(n).toUpperCase() : rom(n)) : String(n);
    if (/ParenBoth/.test(type)) s = '(' + s + ')'; else if (/ParenR/.test(type)) s = s + ')'; else if (/Period/.test(type)) s = s + '.';
    return s;
  }
  function renderParas(txBody, host, ctx, chain, base, scale) {
    const counters = [];
    for (const p of kids(txBody, 'p')) {
      const pPr = kid(p, 'pPr');
      const lvl = +att(pPr, 'lvl') || 0;
      const P = Object.assign({}, base);
      for (const lst of chain) mergeLvl(P, lst && kid(lst, 'lvl' + (lvl + 1) + 'pPr'), ctx.th);
      mergeLvl(P, pPr, ctx.th);
      const div = el('div', 'pptx-p');
      const st = div.style;
      if (P.algn) st.textAlign = { ctr: 'center', r: 'right', just: 'justify', dist: 'justify' }[P.algn] || 'left';
      if (P.marL) st.paddingLeft = P.marL + 'px';
      if (P.indent) st.textIndent = P.indent + 'px';
      const size = (P.sz || 18) * PT * scale;
      st.fontSize = size + 'px';
      if (P.ln) st.lineHeight = P.ln.px ? P.ln.px + 'px' : String(P.ln.em * 1.2 * (ctx.lnRed || 1));
      if (P.bef) st.marginTop = P.bef.px ? P.bef.px * scale + 'px' : P.bef.em + 'em';
      if (P.aft) st.marginBottom = P.aft.px ? P.aft.px * scale + 'px' : P.aft.em + 'em';
      const runs = Array.from(p.children).filter(c => ['r', 'br', 'fld'].includes(c.localName));
      const hasText = runs.some(r => r.localName !== 'br' && (kid(r, 't') || {}).textContent);
      if (P.bu && hasText) {
        const text = P.bu.ch != null ? (WING[P.bu.ch] && /wingdings|symbol/i.test(P.buFont || '') ? WING[P.bu.ch] : P.bu.ch) : autoNum(P.bu.auto, (counters[lvl] = (counters[lvl] || P.bu.start - 1) + 1));
        const b = el('span', 'pptx-bullet', text + '\u00a0');
        b.style.color = P.buClr || P.color || '';
        if (P.buSz) b.style.fontSize = (P.buSz * 100) + '%';
        div.append(b);
      } else if (!P.bu) counters[lvl] = 0;
      for (const r of runs) {
        if (r.localName === 'br') { div.append(el('br')); continue; }
        const R = Object.assign({}, P);
        mergeRun(R, kid(r, 'rPr'), ctx.th);
        const s = el('span', null, (kid(r, 't') || {}).textContent || '');
        const ss = s.style;
        if (R.sz !== P.sz) ss.fontSize = (R.sz || 18) * PT * scale + 'px';
        if (R.b) ss.fontWeight = '700';
        if (R.i) ss.fontStyle = 'italic';
        const deco = [R.u ? 'underline' : '', R.s ? 'line-through' : ''].filter(Boolean).join(' ');
        if (deco) ss.textDecoration = deco;
        if (R.color) ss.color = R.color;
        if (R.font) ss.fontFamily = fam(R.font === '+mj-lt' ? ctx.th.fonts.major : R.font === '+mn-lt' ? ctx.th.fonts.minor : R.font);
        if (R.cap === 'all') ss.textTransform = 'uppercase';
        if (R.base) { ss.verticalAlign = R.base > 0 ? 'super' : 'sub'; ss.fontSize = '70%'; }
        div.append(s);
      }
      if (!runs.length || !div.textContent) { div.append(el('br')); const e = kid(p, 'endParaRPr'); if (e && att(e, 'sz')) st.fontSize = (+att(e, 'sz') / 100) * PT * scale + 'px'; }
      if (P.color) st.color = P.color;
      if (P.b) st.fontWeight = '700';
      if (P.i) st.fontStyle = 'italic';
      if (P.font) st.fontFamily = fam(P.font === '+mj-lt' ? ctx.th.fonts.major : P.font === '+mn-lt' ? ctx.th.fonts.minor : P.font);
      host.append(div);
    }
  }

  // ── Фигуры ──
  function place(host, r) {
    const d = el('div', 'pptx-shape');
    d.style.left = r.x / EMU + 'px'; d.style.top = r.y / EMU + 'px';
    d.style.width = Math.max(1, r.w / EMU) + 'px'; d.style.height = Math.max(1, r.h / EMU) + 'px';
    if (r.rot) d.style.transform = 'rotate(' + r.rot + 'deg)';
    host.append(d);
    return d;
  }
  function lineSvg(d, r, line) {
    const w = Math.max(1, r.w / EMU), h = Math.max(1, r.h / EMU);
    const flip = r.fh !== r.fv;
    d.innerHTML = '<svg width="100%" height="100%" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none" style="overflow:visible"><line x1="' + (flip ? w : 0) + '" y1="0" x2="' + (flip ? 0 : w) + '" y2="' + h + '" /></svg>';
    const ln = d.querySelector('line');
    ln.setAttribute('stroke', line ? line.c : '#000');
    ln.setAttribute('stroke-width', line ? line.w : 1);
    if (line && line.dash !== 'solid') ln.setAttribute('stroke-dasharray', line.dash === 'dotted' ? '2 3' : '8 5');
  }
  async function renderShape(sp, host, ctx, tf, part, isSlide) {
    const ph = phInfo(sp);
    if (!isSlide && ph) return;
    const spPr = kid(sp, 'spPr');
    let x = xfrmOf(kid(spPr, 'xfrm'));
    let layoutPh = null, masterPh = null;
    if (ph && part === ctx.slide) { layoutPh = findPh(path(ctx.layout && ctx.layout.doc.documentElement, 'cSld', 'spTree'), ph, true); masterPh = findPh(path(ctx.master && ctx.master.doc.documentElement, 'cSld', 'spTree'), ph, false); }
    if (!x) x = xfrmOf(path(layoutPh, 'spPr', 'xfrm')) || xfrmOf(path(masterPh, 'spPr', 'xfrm'));
    if (!x) return;
    const r = tf(x);
    const d = place(host, r);
    const style = kid(sp, 'style');
    const prst = att(kid(spPr, 'prstGeom'), 'prst') || 'rect';
    const line = lineOf(spPr, ctx, style);
    if (prst === 'line' || prst === 'straightConnector1') { lineSvg(d, r, line || { c: ctx.th.colors[ctx.th.map.tx1 || 'dk1'] || '#000', w: 1, dash: 'solid' }); return; }
    const fill = await fillOf(spPr, ctx, part, style);
    if (fill || line) {
      const f = el('div', 'pptx-fill');
      if (fill) f.style.background = fill;
      if (line) f.style.border = line.w + 'px ' + line.dash + ' ' + line.c;
      if (prst === 'ellipse') f.style.borderRadius = '50%';
      else if (prst === 'roundRect') f.style.borderRadius = Math.min(r.w, r.h) / EMU * 0.1667 + 'px';
      else if (CLIP[prst]) f.style.clipPath = CLIP[prst];
      if (r.fh || r.fv) f.style.transform = (r.fh ? 'scaleX(-1) ' : '') + (r.fv ? 'scaleY(-1)' : '');
      d.append(f);
    }
    const tx = kid(sp, 'txBody');
    if (!tx) return;
    const txs = path(ctx.master && ctx.master.doc.documentElement, 'txStyles');
    const g = ph ? group(ph.type) : null;
    const chain = [g === 'title' ? kid(txs, 'titleStyle') : g === 'body' ? kid(txs, 'bodyStyle') : kid(txs, 'otherStyle'),
      path(masterPh, 'txBody', 'lstStyle'), path(layoutPh, 'txBody', 'lstStyle'), kid(tx, 'lstStyle')];
    const bps = [path(masterPh, 'txBody', 'bodyPr'), path(layoutPh, 'txBody', 'bodyPr'), kid(tx, 'bodyPr')];
    const B = { anchor: g === 'title' && !ph.type.includes('Title') ? 'ctr' : 't', l: 91440, t: 45720, r: 91440, b: 45720, scale: 1, lnRed: 1 };
    for (const bp of bps) {
      if (!bp) continue;
      if (att(bp, 'anchor')) B.anchor = att(bp, 'anchor');
      for (const [k, n] of [['lIns', 'l'], ['tIns', 't'], ['rIns', 'r'], ['bIns', 'b']]) if (att(bp, k) != null) B[n] = +att(bp, k);
      const fit = kid(bp, 'normAutofit');
      if (fit && att(fit, 'fontScale')) B.scale = +att(fit, 'fontScale') / 100000;
      if (fit && att(fit, 'lnSpcReduction')) B.lnRed = 1 - +att(fit, 'lnSpcReduction') / 100000;
    }
    const t = el('div', 'pptx-text');
    t.style.justifyContent = B.anchor === 'ctr' ? 'center' : B.anchor === 'b' ? 'flex-end' : 'flex-start';
    t.style.padding = [B.t, B.r, B.b, B.l].map(v => v / EMU + 'px').join(' ');
    const fontRef = style && kid(style, 'fontRef');
    const base = { color: (fontRef && colorOf(fontRef, ctx.th)) || ctx.th.colors[ctx.th.map.tx1 || 'dk1'] || '#000000', font: g === 'title' ? ctx.th.fonts.major : ctx.th.fonts.minor };
    ctx.lnRed = B.lnRed;
    renderParas(tx, t, ctx, chain, base, B.scale);
    d.append(t);
  }
  async function renderPic(pic, host, ctx, tf, part, isSlide) {
    if (!isSlide && phInfo(pic)) return;
    const spPr = kid(pic, 'spPr');
    let x = xfrmOf(kid(spPr, 'xfrm'));
    if (!x && part === ctx.slide) { const ph = phInfo(pic); const lp = ph && findPh(path(ctx.layout && ctx.layout.doc.documentElement, 'cSld', 'spTree'), ph, true); x = xfrmOf(path(lp, 'spPr', 'xfrm')); }
    if (!x) return;
    const r = tf(x);
    const d = place(host, r);
    d.classList.add('pptx-pic');
    const bf = kid(pic, 'blipFill'), blip = all(bf, 'blip')[0];
    const rel = blip && part.rels[att(blip, 'embed')];
    if (!rel || rel.external) return;
    if (/\.(emf|wmf)$/i.test(rel.target)) { d.classList.add('pptx-placeholder'); d.textContent = 'Рисунок'; return; }
    const img = el('img');
    img.alt = '';
    img.src = await ctx.zip.blobUrl(rel.target);
    const sr = kid(bf, 'srcRect');
    if (sr) {
      const [l, t, rr, b] = ['l', 't', 'r', 'b'].map(k => (+att(sr, k) || 0) / 100000);
      const w = 1 - l - rr, h = 1 - t - b;
      if (w > 0 && h > 0) Object.assign(img.style, { width: 100 / w + '%', height: 100 / h + '%', left: -l / w * 100 + '%', top: -t / h * 100 + '%' });
    }
    if (r.fh || r.fv) img.style.transform = (r.fh ? 'scaleX(-1) ' : '') + (r.fv ? 'scaleY(-1)' : '');
    d.append(img);
  }
  async function renderFrame(gf, host, ctx, tf) {
    const x = xfrmOf(kid(gf, 'xfrm'));
    if (!x) return;
    const r = tf(x);
    const d = place(host, r);
    const gd = path(gf, 'graphic', 'graphicData');
    const tbl = kid(gd, 'tbl');
    if (!tbl) { d.classList.add('pptx-placeholder'); d.textContent = /chart/.test(att(gd, 'uri') || '') ? 'Диаграмма' : 'Объект'; return; }
    const tp = kid(tbl, 'tblPr');
    const styled = !!kid(tp, 'tableStyleId') || !!(tp && tp.attributes.length);
    const accent = ctx.th.colors.accent1 || '#4472C4';
    const table = el('table', 'pptx-table');
    const cg = el('colgroup');
    kids(kid(tbl, 'tblGrid'), 'gridCol').forEach(g => { const c = el('col'); c.style.width = +att(g, 'w') / EMU + 'px'; cg.append(c); });
    table.append(cg);
    const txs = path(ctx.master && ctx.master.doc.documentElement, 'txStyles');
    kids(tbl, 'tr').forEach((tr, i) => {
      const row = el('tr');
      row.style.height = +att(tr, 'h') / EMU + 'px';
      for (const tc of kids(tr, 'tc')) {
        if (att(tc, 'hMerge') === '1' || att(tc, 'vMerge') === '1') continue;
        const td = el('td');
        if (+att(tc, 'gridSpan') > 1) td.colSpan = +att(tc, 'gridSpan');
        if (+att(tc, 'rowSpan') > 1) td.rowSpan = +att(tc, 'rowSpan');
        const head = styled && i === 0 && att(tp, 'firstRow') === '1';
        const band = styled && att(tp, 'bandRow') === '1' && i % 2 === (att(tp, 'firstRow') === '1' ? 1 : 0);
        const own = colorOf(kid(kid(tc, 'tcPr'), 'solidFill'), ctx.th);
        td.style.background = own || (head ? accent : band ? C.mix(accent, '#FFFFFF', 0.2) : styled ? C.mix(accent, '#FFFFFF', 0.08) : '');
        const base = { color: head && !own ? '#FFFFFF' : ctx.th.colors[ctx.th.map.tx1 || 'dk1'] || '#000', b: head, sz: 18, font: ctx.th.fonts.minor };
        if (kid(tc, 'txBody')) renderParas(kid(tc, 'txBody'), td, ctx, [kid(txs, 'otherStyle')], base, 1);
        row.append(td);
      }
      table.append(row);
    });
    d.append(table);
  }
  async function renderTree(tree, host, ctx, tf, part, isSlide) {
    for (const n of tree ? Array.from(tree.children) : []) {
      switch (n.localName) {
        case 'sp': await renderShape(n, host, ctx, tf, part, isSlide); break;
        case 'pic': await renderPic(n, host, ctx, tf, part, isSlide); break;
        case 'graphicFrame': await renderFrame(n, host, ctx, tf); break;
        case 'cxnSp': {
          const x = xfrmOf(path(n, 'spPr', 'xfrm'));
          if (x) { const r = tf(x); const d = place(host, r); lineSvg(d, r, lineOf(kid(n, 'spPr'), ctx, kid(n, 'style')) || { c: '#000', w: 1, dash: 'solid' }); }
          break;
        }
        case 'grpSp': {
          const gx = xfrmOf(path(n, 'grpSpPr', 'xfrm'));
          if (!gx || !gx.chExt) { await renderTree(n, host, ctx, tf, part, isSlide); break; }
          const cx = +att(gx.chOff, 'x'), cy = +att(gx.chOff, 'y'), sx = gx.w / (+att(gx.chExt, 'cx') || gx.w), sy = gx.h / (+att(gx.chExt, 'cy') || gx.h);
          await renderTree(n, host, ctx, c => { const o = tf({ x: gx.x + (c.x - cx) * sx, y: gx.y + (c.y - cy) * sy, w: c.w * sx, h: c.h * sy }); return Object.assign({}, c, o); }, part, isSlide);
          break;
        }
        case 'AlternateContent': { const b = kid(n, 'Fallback') || kid(n, 'Choice'); if (b) await renderTree(b, host, ctx, tf, part, isSlide); break; }
      }
    }
  }

  // ── Презентация ──
  async function render(buf, host, opts) {
    opts = opts || {};
    const zip = Kov.Zip.open(buf);
    const top = await rels(zip, '');
    const main = (byType(top, 'officeDocument') || { target: 'ppt/presentation.xml' }).target;
    const pres = await zip.xml(main);
    if (!pres) throw new Error('В файле нет презентации');
    const pr = await rels(zip, main);
    const sz = all(pres, 'sldSz')[0];
    const W = (+att(sz, 'cx') || 12192000) / EMU, H = (+att(sz, 'cy') || 6858000) / EMU;
    const parts = new Map(), themes = new Map();
    const part = p => { if (!parts.has(p)) parts.set(p, (async () => ({ path: p, doc: await zip.xml(p), rels: await rels(zip, p) }))()); return parts.get(p); };
    async function themeOf(master) {
      if (themes.has(master.path)) return themes.get(master.path);
      const t = byType(master.rels, 'theme');
      const doc = t ? await zip.xml(t.target) : null;
      const colors = {}, fonts = { major: 'Calibri Light', minor: 'Calibri' }, map = {};
      const sch = doc && all(doc, 'clrScheme')[0];
      if (sch) for (const c of sch.children) { const s = kid(c, 'srgbClr'), y = kid(c, 'sysClr'); colors[c.localName] = '#' + ((s && att(s, 'val')) || (y && (att(y, 'lastClr') || (att(y, 'val') === 'window' ? 'FFFFFF' : '000000'))) || '000000'); }
      const mj = doc && all(doc, 'majorFont')[0], mn = doc && all(doc, 'minorFont')[0];
      if (mj && kid(mj, 'latin')) fonts.major = att(kid(mj, 'latin'), 'typeface');
      if (mn && kid(mn, 'latin')) fonts.minor = att(kid(mn, 'latin'), 'typeface');
      const cm = all(master.doc, 'clrMap')[0];
      if (cm) for (const a of cm.attributes) map[a.localName] = a.value;
      const th = { colors, fonts, map };
      themes.set(master.path, th);
      return th;
    }
    // у sldId два атрибута id: номер и ссылка r:id — нужна ссылка
    const relId = e => { for (const a of e.attributes) if (a.localName === 'id' && a.namespaceURI && /relationships/.test(a.namespaceURI)) return a.value; return att(e, 'id'); };
    const slides = all(kid(pres.documentElement, 'sldIdLst'), 'sldId').map(s => pr[relId(s)]).filter(Boolean);
    if (!slides.length) throw new Error('В презентации не найдено слайдов');
    const frames = [];
    // пока слайд не в странице, ширина 0 — ждём ResizeObserver, а не сжимаем в точку
    const fit = () => frames.forEach(f => { const w = f.frame.clientWidth; if (w) f.canvas.style.transform = 'scale(' + (w / W) + ')'; });
    for (let i = 0; i < slides.length; i++) {
      const slide = await part(slides[i].target);
      const lr = byType(slide.rels, 'slideLayout'), layout = lr ? await part(lr.target) : null;
      const mr = layout && byType(layout.rels, 'slideMaster'), master = mr ? await part(mr.target) : null;
      const th = master ? await themeOf(master) : { colors: {}, fonts: { major: 'Calibri Light', minor: 'Calibri' }, map: {} };
      const ctx = { zip, th, slide, layout, master };
      const frame = el('div', 'pptx-slide');
      frame.style.aspectRatio = W + ' / ' + H;
      const canvas = el('div', 'pptx-canvas');
      canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
      canvas.style.fontFamily = fam(th.fonts.minor);
      let bg = '#FFFFFF';
      for (const p of [slide, layout, master]) {
        const b = p && path(p.doc.documentElement, 'cSld', 'bg');
        if (!b) continue;
        const f = kid(b, 'bgPr') ? await fillOf(kid(b, 'bgPr'), ctx, p) : colorOf(kid(b, 'bgRef'), th);
        if (f) { bg = f; break; }
      }
      canvas.style.background = bg;
      const showMaster = att(slide.doc.documentElement, 'showMasterSp') !== '0';
      const id = x => x;
      if (master && showMaster && (!layout || att(layout.doc.documentElement, 'showMasterSp') !== '0')) await renderTree(path(master.doc.documentElement, 'cSld', 'spTree'), canvas, ctx, id, master, false);
      if (layout && showMaster) await renderTree(path(layout.doc.documentElement, 'cSld', 'spTree'), canvas, ctx, id, layout, false);
      await renderTree(path(slide.doc.documentElement, 'cSld', 'spTree'), canvas, ctx, id, slide, true);
      frame.append(canvas);
      const wrap = el('figure', 'pptx-item');
      wrap.append(frame, el('figcaption', 'pptx-num', 'Слайд ' + (i + 1) + ' из ' + slides.length));
      host.append(wrap);
      frames.push({ frame, canvas });
      fit();
      if (opts.onSlide) opts.onSlide(i + 1, slides.length);
    }
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(fit) : null;
    if (ro) ro.observe(host); else window.addEventListener('resize', fit);
    fit();
    return { slides: slides.length, release: () => { if (ro) ro.disconnect(); zip.release(); } };
  }
  Kov.PptxView = { render };
})(typeof self !== 'undefined' ? self : this);
