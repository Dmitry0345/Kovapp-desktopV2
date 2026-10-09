/* Показ Word (docx) на устройстве: абзацы, стили, списки, таблицы, картинки, ссылки */
(function (root) {
  const Kov = root.Kov = root.Kov || {};
  const { kids, kid, att, all, flag, rels, byType } = Kov.Zip.x;
  const TW = 15, EMU = 9525;
  const HL = { yellow: '#ffff00', green: '#00ff00', cyan: '#00ffff', magenta: '#ff00ff', blue: '#0000ff', red: '#ff0000', darkBlue: '#000080', darkCyan: '#008080',
    darkGreen: '#008000', darkMagenta: '#800080', darkRed: '#800000', darkYellow: '#808000', darkGray: '#808080', lightGray: '#c0c0c0', black: '#000000', white: '#ffffff' };
  const PUA = { 0xF0B7: '•', 0xF0A7: '▪', 0xF0D8: '➢', 0xF0FC: '✓', 0xF076: '❖', 0xF0A8: '◆', 0xF06E: '■', 0xF071: '❑', 0xF06C: '●' };

  function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }

  // ── Свойства абзаца и фрагмента ──
  function readP(pPr, out) {
    if (!pPr) return out;
    const jc = kid(pPr, 'jc'); if (jc) out.jc = att(jc, 'val');
    const ind = kid(pPr, 'ind');
    if (ind) for (const [k, n] of [['left', 'l'], ['start', 'l'], ['right', 'r'], ['end', 'r'], ['firstLine', 'fl'], ['hanging', 'hg']]) { const v = att(ind, k); if (v != null) { out['i' + n] = +v; if (n === 'fl') delete out.ihg; if (n === 'hg') delete out.ifl; } }
    const sp = kid(pPr, 'spacing');
    if (sp) { for (const k of ['before', 'after', 'line']) { const v = att(sp, k); if (v != null) out['s' + k] = +v; } if (att(sp, 'lineRule')) out.srule = att(sp, 'lineRule'); }
    const shd = kid(pPr, 'shd'); if (shd && att(shd, 'fill') && att(shd, 'fill') !== 'auto') out.shd = '#' + att(shd, 'fill');
    const num = kid(pPr, 'numPr');
    if (num) { const id = att(kid(num, 'numId'), 'val'); if (id != null) out.numId = id; const lv = att(kid(num, 'ilvl'), 'val'); if (lv != null) out.ilvl = +lv; }
    if (kid(pPr, 'pageBreakBefore') && flag(kid(pPr, 'pageBreakBefore'))) out.pbb = true;
    return out;
  }
  function readR(rPr, out) {
    if (!rPr) return out;
    for (const [n, k] of [['b', 'b'], ['i', 'i'], ['strike', 's'], ['dstrike', 's'], ['caps', 'caps'], ['smallCaps', 'sc'], ['vanish', 'hide']]) { const e = kid(rPr, n); if (e) out[k] = flag(e); }
    const u = kid(rPr, 'u'); if (u) out.u = att(u, 'val') !== 'none';
    const c = kid(rPr, 'color'); if (c && att(c, 'val') && att(c, 'val') !== 'auto') out.color = '#' + att(c, 'val');
    const sz = kid(rPr, 'sz'); if (sz) out.sz = +att(sz, 'val') / 2;
    const hl = kid(rPr, 'highlight'); if (hl) out.hl = HL[att(hl, 'val')] || null;
    const shd = kid(rPr, 'shd'); if (shd && att(shd, 'fill') && att(shd, 'fill') !== 'auto') out.bg = '#' + att(shd, 'fill');
    const va = kid(rPr, 'vertAlign'); if (va) out.va = att(va, 'val');
    const f = kid(rPr, 'rFonts'); if (f && (att(f, 'ascii') || att(f, 'hAnsi'))) out.font = att(f, 'ascii') || att(f, 'hAnsi');
    return out;
  }

  // ── Стили документа ──
  async function loadStyles(zip, rel) {
    const S = { map: {}, defP: {}, defR: {}, defPara: null, memo: {} };
    const doc = rel ? await zip.xml(rel.target) : null;
    if (!doc) return S;
    const dd = kid(doc.documentElement, 'docDefaults');
    if (dd) { readP(all(dd, 'pPr')[0], S.defP); readR(all(dd, 'rPr')[0], S.defR); }
    for (const s of kids(doc.documentElement, 'style')) {
      const id = att(s, 'styleId');
      S.map[id] = { type: att(s, 'type'), name: (att(kid(s, 'name'), 'val') || '').toLowerCase(), basedOn: att(kid(s, 'basedOn'), 'val'), pPr: kid(s, 'pPr'), rPr: kid(s, 'rPr'), tblPr: kid(s, 'tblPr') };
      if (att(s, 'type') === 'paragraph' && ['1', 'true'].includes(att(s, 'default'))) S.defPara = id;
    }
    return S;
  }
  function styleProps(S, id, depth) {
    if (!id || !S.map[id] || (depth || 0) > 12) return { p: {}, r: {} };
    if (S.memo[id]) return S.memo[id];
    const st = S.map[id];
    const base = styleProps(S, st.basedOn, (depth || 0) + 1);
    const res = { p: readP(st.pPr, Object.assign({}, base.p)), r: readR(st.rPr, Object.assign({}, base.r)) };
    S.memo[id] = res;
    return res;
  }

  // ── Нумерация списков ──
  async function loadNumbering(zip, rel) {
    const N = { abs: {}, nums: {}, count: {} };
    const doc = rel ? await zip.xml(rel.target) : null;
    if (!doc) return N;
    for (const a of kids(doc.documentElement, 'abstractNum')) {
      N.abs[att(a, 'abstractNumId')] = kids(a, 'lvl').map(l => ({
        fmt: att(kid(l, 'numFmt'), 'val') || 'decimal', text: att(kid(l, 'lvlText'), 'val') || '', start: +(att(kid(l, 'start'), 'val') || 1), p: readP(kid(l, 'pPr'), {}), r: readR(kid(l, 'rPr'), {}) }));
    }
    for (const n of kids(doc.documentElement, 'num')) {
      const starts = {};
      for (const o of kids(n, 'lvlOverride')) { const s = kid(o, 'startOverride'); if (s) starts[+att(o, 'ilvl')] = +att(s, 'val'); }
      N.nums[att(n, 'numId')] = { abs: att(kid(n, 'abstractNumId'), 'val'), starts };
    }
    return N;
  }
  function roman(n) { const v = [[1000, 'm'], [900, 'cm'], [500, 'd'], [400, 'cd'], [100, 'c'], [90, 'xc'], [50, 'l'], [40, 'xl'], [10, 'x'], [9, 'ix'], [5, 'v'], [4, 'iv'], [1, 'i']]; let s = ''; for (const [k, r] of v) while (n >= k) { s += r; n -= k; } return s; }
  function letters(n, abc) { let s = ''; while (n > 0) { n--; s = abc[n % abc.length] + s; n = Math.floor(n / abc.length); } return s; }
  function numText(n, fmt) {
    switch (fmt) {
      case 'lowerLetter': return letters(n, 'abcdefghijklmnopqrstuvwxyz');
      case 'upperLetter': return letters(n, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ');
      case 'lowerRoman': return roman(n);
      case 'upperRoman': return roman(n).toUpperCase();
      case 'decimalZero': return String(n).padStart(2, '0');
      case 'russianLower': return letters(n, 'абвгдежзиклмнопрстуфхцчшщэюя');
      case 'russianUpper': return letters(n, 'АБВГДЕЖЗИКЛМНОПРСТУФХЦЧШЩЭЮЯ');
      default: return String(n);
    }
  }
  function listLabel(N, numId, ilvl) {
    const num = N.nums[numId];
    const levels = num && N.abs[num.abs];
    const lvl = levels && levels[ilvl];
    if (!lvl) return null;
    const cnt = N.count[numId] || (N.count[numId] = []);
    const startOf = k => num.starts[k] != null ? num.starts[k] : (levels[k] ? levels[k].start : 1);
    cnt[ilvl] = cnt[ilvl] == null ? startOf(ilvl) : cnt[ilvl] + 1;
    for (let k = ilvl + 1; k < cnt.length; k++) cnt[k] = null;
    let text;
    if (lvl.fmt === 'bullet') {
      const ch = lvl.text.charCodeAt(0);
      text = PUA[ch] || (lvl.text === 'o' ? '◦' : ch >= 0xF000 ? '•' : lvl.text || '•');
    } else if (lvl.fmt === 'none') text = '';
    else text = lvl.text.replace(/%(\d)/g, (m, d) => { const k = +d - 1; return numText(cnt[k] != null ? cnt[k] : startOf(k), levels[k] ? levels[k].fmt : 'decimal'); });
    return { text, lvl };
  }

  // ── Отрисовка ──
  function applyRun(span, r) {
    const st = span.style;
    if (r.b) st.fontWeight = '700';
    if (r.i) st.fontStyle = 'italic';
    const deco = [r.u ? 'underline' : '', r.s ? 'line-through' : ''].filter(Boolean).join(' ');
    if (deco) st.textDecoration = deco;
    if (r.color) st.color = r.color;
    if (r.sz) st.fontSize = r.sz + 'pt';
    if (r.hl || r.bg) st.background = r.hl || r.bg;
    if (r.va === 'superscript' || r.va === 'subscript') { st.verticalAlign = r.va === 'superscript' ? 'super' : 'sub'; st.fontSize = ((r.sz || 11) * 0.7) + 'pt'; }
    if (r.caps) st.textTransform = 'uppercase';
    if (r.sc) st.fontVariant = 'small-caps';
    if (r.font) st.fontFamily = '"' + r.font.replace(/"/g, '') + '", var(--docx-font)';
  }
  function applyPara(div, p) {
    const st = div.style;
    const jc = { center: 'center', right: 'right', end: 'right', both: 'justify', distribute: 'justify' }[p.jc];
    if (jc) st.textAlign = jc;
    if (p.il) st.marginLeft = (p.il / TW) + 'px';
    if (p.ir) st.marginRight = (p.ir / TW) + 'px';
    if (p.ifl) st.textIndent = (p.ifl / TW) + 'px';
    if (p.ihg) st.textIndent = (-p.ihg / TW) + 'px';
    if (p.sbefore != null) st.marginTop = (p.sbefore / TW) + 'px';
    if (p.safter != null) st.marginBottom = (p.safter / TW) + 'px';
    if (p.sline) st.lineHeight = (!p.srule || p.srule === 'auto') ? String(Math.max(0.8, p.sline / 240 * 1.15)) : (p.sline / TW) + 'px';
    if (p.shd) st.background = p.shd;
  }

  async function renderRuns(nodes, out, ctx, pr, link) {
    for (const n of nodes) {
      switch (n.localName) {
        case 'r': await renderRun(n, out, ctx, pr, link); break;
        case 'hyperlink': {
          const id = att(n, 'id'), rel = id && ctx.rels[id];
          let a = null;
          if (rel && rel.external && /^(https?:|mailto:)/i.test(rel.target)) { a = el('a', 'docx-link'); a.href = rel.target; a.target = '_blank'; a.rel = 'noopener noreferrer'; out.append(a); }
          await renderRuns(n.children, a || out, ctx, pr, true);
          break;
        }
        case 'ins': case 'smartTag': case 'customXml': case 'fldSimple': case 'dir': case 'bdo':
          await renderRuns(n.children, out, ctx, pr, link); break;
        case 'sdt': await renderRuns(kids(kid(n, 'sdtContent'), 'r').concat(kids(kid(n, 'sdtContent'), 'hyperlink')), out, ctx, pr, link); break;
        case 'oMath': case 'oMathPara': out.append(el('span', 'docx-math', all(n, 't').map(t => t.textContent).join(''))); break;
      }
    }
  }
  async function renderRun(r, out, ctx, pr, link) {
    const props = Object.assign({}, pr);
    const rPr = kid(r, 'rPr');
    const rs = rPr && att(kid(rPr, 'rStyle'), 'val');
    if (rs) Object.assign(props, styleProps(ctx.S, rs).r);
    readR(rPr, props);
    if (props.hide) return;
    let span = null;
    const text = s => {
      if (ctx.field === 'code') return;
      if (!span) { span = el('span'); applyRun(span, props); if (link) span.classList.add('docx-link-text'); out.append(span); }
      span.append(s);
    };
    for (const c of r.children) {
      switch (c.localName) {
        case 't': text(c.textContent); break;
        case 'tab': text('\t'); break;
        case 'noBreakHyphen': text('\u2011'); break;
        case 'sym': { const code = parseInt(att(c, 'char') || '0', 16); text(PUA[code] || String.fromCharCode(code >= 0xF000 ? code - 0xF000 : code)); break; }
        case 'br': case 'cr':
          if (att(c, 'type') === 'page') { ctx.pageBreak = true; out.append(el('span', 'docx-pb-inline')); }
          else { out.append(el('br')); span = null; }
          break;
        case 'fldChar': { const t = att(c, 'fldCharType'); ctx.field = t === 'begin' ? 'code' : t === 'separate' ? 'result' : null; break; }
        case 'drawing': case 'pict': case 'object': { const d = await renderDrawing(c, ctx); if (d) { out.append(d); span = null; } break; }
        case 'footnoteReference': case 'endnoteReference': { const s = el('sup', 'docx-note', String(++ctx.notes)); out.append(s); break; }
      }
    }
  }
  async function renderDrawing(node, ctx) {
    const tb = all(node, 'txbxContent')[0];
    if (tb) { const box = el('div', 'docx-textbox'); await renderBlocks(tb.children, box, ctx); return box; }
    const blip = all(node, 'blip')[0] || all(node, 'imagedata')[0];
    const id = blip && (att(blip, 'embed') || att(blip, 'id') || att(blip, 'link'));
    const rel = id && ctx.rels[id];
    const ext = all(node, 'extent')[0];
    const w = ext ? +att(ext, 'cx') / EMU : null, hgt = ext ? +att(ext, 'cy') / EMU : null;
    if (!rel || rel.external) return null;
    if (/\.(emf|wmf)$/i.test(rel.target)) { const ph = el('span', 'docx-img-missing', 'Рисунок'); if (w) { ph.style.width = w + 'px'; ph.style.height = hgt + 'px'; } return ph; }
    const img = el('img', 'docx-img');
    img.alt = '';
    img.src = await ctx.zip.blobUrl(rel.target);
    if (w) { img.style.width = w + 'px'; img.style.aspectRatio = w + ' / ' + hgt; }
    return img;
  }
  async function renderPara(p, host, ctx) {
    const pPr = kid(p, 'pPr');
    const sid = (pPr && att(kid(pPr, 'pStyle'), 'val')) || ctx.S.defPara;
    const sp = styleProps(ctx.S, sid);
    const props = Object.assign({}, ctx.S.defP, sp.p);
    const direct = readP(pPr, {});
    const numId = direct.numId != null ? direct.numId : props.numId;
    const ilvl = direct.ilvl != null ? direct.ilvl : (props.ilvl || 0);
    let label = null;
    if (numId && numId !== '0') {
      label = listLabel(ctx.N, numId, ilvl);
      if (label) Object.assign(props, label.lvl.p);
    }
    Object.assign(props, direct);
    const div = el('div', 'docx-p');
    if (props.pbb) host.append(el('hr', 'docx-pb'));
    applyPara(div, props);
    const runProps = Object.assign({}, ctx.S.defR, sp.r);
    if (label && label.text) {
      const lab = el('span', 'docx-label', label.text + '\u00a0');
      applyRun(lab, Object.assign({}, runProps, label.lvl.r));
      if (props.ihg) lab.style.display = 'inline-block', lab.style.minWidth = (props.ihg / TW) + 'px', lab.style.textIndent = '0';
      div.append(lab);
    }
    ctx.pageBreak = false;
    await renderRuns(p.children, div, ctx, runProps, false);
    if (!div.textContent && !div.querySelector('img,.docx-textbox,.docx-img-missing')) {
      div.append(el('br'));
      if (runProps.sz) div.style.fontSize = runProps.sz + 'pt';
    }
    host.append(div);
    if (ctx.pageBreak) host.append(el('hr', 'docx-pb'));
  }
  async function renderTable(t, host, ctx) {
    const tblPr = kid(t, 'tblPr');
    const sid = tblPr && att(kid(tblPr, 'tblStyle'), 'val');
    const sname = sid && ctx.S.map[sid] ? ctx.S.map[sid].name : '';
    const borders = kid(tblPr, 'tblBorders') || (sid && ctx.S.map[sid] && kid(ctx.S.map[sid].tblPr, 'tblBorders'));
    const grid = /grid|сетка/i.test(sname) || (!!borders && Array.from(borders.children).some(b => !['nil', 'none'].includes(att(b, 'val'))));
    const wrap = el('div', 'docx-table-wrap');
    const table = el('table', 'docx-table' + (grid ? ' is-grid' : ''));
    const cols = kids(kid(t, 'tblGrid'), 'gridCol').map(g => +att(g, 'w') / TW);
    if (cols.length) { const cg = el('colgroup'); cols.forEach(w => { const c = el('col'); c.style.width = w + 'px'; cg.append(c); }); table.append(cg); }
    const rows = kids(t, 'tr').map(tr => {
      let col = +(att(kid(kid(tr, 'trPr'), 'gridBefore'), 'val') || 0);
      return kids(tr, 'tc').map(tc => {
        const pr = kid(tc, 'tcPr');
        const span = +(att(kid(pr, 'gridSpan'), 'val') || 1);
        const vm = kid(pr, 'vMerge');
        const cell = { tc, pr, col, span, vm: vm ? (att(vm, 'val') === 'restart' ? 'start' : 'cont') : null };
        col += span;
        return cell;
      });
    });
    const tbody = el('tbody');
    for (let i = 0; i < rows.length; i++) {
      const tr = el('tr');
      for (const cell of rows[i]) {
        if (cell.vm === 'cont') continue;
        const td = el('td');
        if (cell.span > 1) td.colSpan = cell.span;
        if (cell.vm === 'start') {
          let n = 1;
          for (let j = i + 1; j < rows.length; j++) { const c = rows[j].find(x => x.col === cell.col); if (c && c.vm === 'cont') n++; else break; }
          if (n > 1) td.rowSpan = n;
        }
        const shd = kid(cell.pr, 'shd');
        if (shd && att(shd, 'fill') && att(shd, 'fill') !== 'auto') td.style.background = '#' + att(shd, 'fill');
        const va = att(kid(cell.pr, 'vAlign'), 'val');
        if (va) td.style.verticalAlign = va === 'center' ? 'middle' : va;
        await renderBlocks(cell.tc.children, td, ctx);
        tr.append(td);
      }
      tbody.append(tr);
    }
    table.append(tbody);
    wrap.append(table);
    host.append(wrap);
  }
  async function renderBlocks(nodes, host, ctx) {
    for (const n of Array.from(nodes)) {
      if (n.localName === 'p') await renderPara(n, host, ctx);
      else if (n.localName === 'tbl') await renderTable(n, host, ctx);
      else if (n.localName === 'sdt') await renderBlocks(kid(n, 'sdtContent') ? kid(n, 'sdtContent').children : [], host, ctx);
      else if (n.localName === 'customXml') await renderBlocks(n.children, host, ctx);
    }
  }

  async function render(buf, host) {
    const zip = Kov.Zip.open(buf);
    const top = await rels(zip, '');
    const main = (byType(top, 'officeDocument') || { target: 'word/document.xml' }).target;
    const doc = await zip.xml(main);
    if (!doc) throw new Error('В файле нет документа Word');
    const r = await rels(zip, main);
    const ctx = { zip, rels: r, S: await loadStyles(zip, byType(r, 'styles')), N: await loadNumbering(zip, byType(r, 'numbering')), notes: 0, field: null };
    const body = kid(doc.documentElement, 'body');
    const sect = kid(body, 'sectPr');
    const pg = kid(sect, 'pgSz'), mar = kid(sect, 'pgMar');
    const page = el('div', 'docx-page');
    const px = (v, d) => (v != null ? +v : d) / TW;
    page.style.setProperty('--page-w', px(att(pg, 'w'), 11906) + 'px');
    page.style.setProperty('--pad-t', px(att(mar, 'top'), 1134) + 'px');
    page.style.setProperty('--pad-r', px(att(mar, 'right'), 850) + 'px');
    page.style.setProperty('--pad-b', px(att(mar, 'bottom'), 1134) + 'px');
    page.style.setProperty('--pad-l', px(att(mar, 'left'), 1701) + 'px');
    if (ctx.S.defR.sz) page.style.fontSize = ctx.S.defR.sz + 'pt';
    if (ctx.S.defR.font) page.style.setProperty('--docx-font', '"' + ctx.S.defR.font + '", "Liberation Serif", "Times New Roman", serif');
    await renderBlocks(body.children, page, ctx);
    host.append(page);
    return { release: () => zip.release() };
  }
  Kov.DocxView = { render };
})(typeof self !== 'undefined' ? self : this);
