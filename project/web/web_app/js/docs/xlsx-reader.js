/* Разбор xlsx и csv на устройстве. Выдаёт те же данные, что сервер (sheets.py),
 * и немного больше: подчёркивание, размер шрифта, границы, высоты и скрытые строки. */
(function (root) {
  const Kov = root.Kov = root.Kov || {};
  const { kids, kid, path, att, all, flag, rels, byType } = Kov.Zip.x;
  const C = Kov.Zip.color;
  const MAX_ROWS = 3000, MAX_COLS = 200, MAX_SHEETS = 30, MAX_TEXT = 500;

  // ── Палитра индексов и форматы ──
  const PALETTE = ['000000', 'FFFFFF', 'FF0000', '00FF00', '0000FF', 'FFFF00', 'FF00FF', '00FFFF',
    '000000', 'FFFFFF', 'FF0000', '00FF00', '0000FF', 'FFFF00', 'FF00FF', '00FFFF', '800000', '008000', '000080', '808000',
    '800080', '008080', 'C0C0C0', '808080', '9999FF', '993366', 'FFFFCC', 'CCFFFF', '660066', 'FF8080', '0066CC', 'CCCCFF',
    '000080', 'FF00FF', 'FFFF00', '00FFFF', '800080', '800000', '008080', '0000FF', '00CCFF', 'CCFFFF', 'CCFFCC', 'FFFF99',
    '99CCFF', 'FF99CC', 'CC99FF', 'FFCC99', '3366FF', '33CCCC', '99CC00', 'FFCC00', 'FF9900', 'FF6600', '666699', '969696',
    '003366', '339966', '003300', '333300', '993300', '993366', '333399', '333333'];
  const BUILTIN = { 0: 'General', 1: '0', 2: '0.00', 3: '#,##0', 4: '#,##0.00', 9: '0%', 10: '0.00%', 11: '0.00E+00', 12: '# ?/?', 13: '# ??/??',
    14: 'dd.mm.yyyy', 15: 'd-mmm-yy', 16: 'd-mmm', 17: 'mmm-yy', 18: 'h:mm AM/PM', 19: 'h:mm:ss AM/PM', 20: 'h:mm', 21: 'h:mm:ss', 22: 'dd.mm.yyyy h:mm',
    37: '#,##0 ;(#,##0)', 38: '#,##0 ;[Red](#,##0)', 39: '#,##0.00;(#,##0.00)', 40: '#,##0.00;[Red](#,##0.00)', 45: 'mm:ss', 46: '[h]:mm:ss', 47: 'mm:ss.0', 48: '##0.0E+0', 49: '@' };
  const NAMED = { red: '#FF0000', blue: '#0000FF', green: '#008000', black: '#000000', white: '#FFFFFF', yellow: '#FFFF00', magenta: '#FF00FF', cyan: '#00FFFF',
    'красный': '#FF0000', 'синий': '#0000FF', 'зеленый': '#008000', 'черный': '#000000', 'белый': '#FFFFFF', 'желтый': '#FFFF00' };
  const MONTHS = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь'];
  const MONTHS_GEN = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
  const MON = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
  const DAYS = ['воскресенье', 'понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота'];
  const DAY = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'];
  const NBSP = '\u00a0';

  function color(el, theme) {
    if (!el || att(el, 'auto') === '1') return null;
    let hex = null;
    const rgb = att(el, 'rgb'), th = att(el, 'theme'), ix = att(el, 'indexed');
    if (rgb) hex = '#' + rgb.slice(-6);
    else if (th != null) hex = theme[+th] || null;
    else if (ix != null) hex = PALETTE[+ix] ? '#' + PALETTE[+ix] : null;
    if (!hex) return null;
    const t = parseFloat(att(el, 'tint') || '0');
    return C.tint(hex, t).toUpperCase();
  }

  function parseTheme(doc) {
    const out = [];
    const scheme = all(doc, 'clrScheme')[0];
    if (!scheme) return out;
    const pick = n => { const e = kid(scheme, n); if (!e) return null; const s = kid(e, 'srgbClr'), y = kid(e, 'sysClr'); return '#' + ((s && att(s, 'val')) || (y && att(y, 'lastClr')) || '000000'); };
    ['lt1', 'dk1', 'lt2', 'dk2', 'accent1', 'accent2', 'accent3', 'accent4', 'accent5', 'accent6', 'hlink', 'folHlink'].forEach(n => out.push(pick(n)));
    return out;
  }

  function parseStyles(doc, theme) {
    const numFmts = {};
    if (!doc) return { numFmts, xfs: [], baseSz: 11 };
    const top = doc.documentElement;
    for (const f of all(top, 'numFmt')) numFmts[att(f, 'numFmtId')] = att(f, 'formatCode');
    const fonts = kids(kid(top, 'fonts'), 'font').map(f => {
      const u = kid(f, 'u');
      return { b: flag(kid(f, 'b')), i: flag(kid(f, 'i')), u: !!u && att(u, 'val') !== 'none', s: flag(kid(f, 'strike')),
        sz: parseFloat(att(kid(f, 'sz'), 'val')) || null, color: color(kid(f, 'color'), theme) };
    });
    const fills = kids(kid(top, 'fills'), 'fill').map(f => {
      const p = kid(f, 'patternFill');
      if (p) return att(p, 'patternType') && att(p, 'patternType') !== 'none' ? color(kid(p, 'fgColor'), theme) : null;
      const g = kid(f, 'gradientFill');
      const stop = g && kid(g, 'stop');
      return stop ? color(kid(stop, 'color'), theme) : null;
    });
    const borders = kids(kid(top, 'borders'), 'border').map(b => ['top', 'right', 'bottom', 'left'].map(side => {
      const e = kid(b, side), st = e && att(e, 'style');
      if (!st || st === 'none') return null;
      const width = st === 'double' ? 3 : /medium/i.test(st) ? 2 : st === 'thick' ? 3 : 1;
      const line = st === 'double' ? 'double' : /dash/i.test(st) ? 'dashed' : /dot|hair/i.test(st) ? 'dotted' : 'solid';
      return width + 'px ' + line + ' ' + (color(kid(e, 'color'), theme) || '#000000');
    }));
    const xfs = kids(kid(top, 'cellXfs'), 'xf').map(x => {
      const al = kid(x, 'alignment');
      return { fmt: +att(x, 'numFmtId') || 0, font: fonts[+att(x, 'fontId') || 0] || {}, fill: fills[+att(x, 'fillId') || 0] || null,
        border: borders[+att(x, 'borderId') || 0] || null, h: al && att(al, 'horizontal'), wrap: !!al && ['1', 'true'].includes(att(al, 'wrapText')) };
    });
    return { numFmts, xfs, baseSz: (fonts[0] && fonts[0].sz) || 11 };
  }

  // ── Числа и даты так, как их показывает Excel с русскими настройками ──
  function general(x) {
    if (Number.isInteger(x) && Math.abs(x) < 1e11) return String(x);
    const a = Math.abs(x);
    const s = a >= 1e11 || (a < 1e-9 && a > 0) ? x.toExponential(5).replace(/\.?0+e/, 'e').replace('e', 'E') : String(Number(x.toPrecision(10)));
    return s.replace('.', ',');
  }
  function sections(code) {
    const out = [];
    let cur = '', q = false;
    for (let i = 0; i < code.length; i++) {
      const ch = code[i];
      if (ch === '"') q = !q;
      if (ch === '\\' && !q) { cur += ch + (code[i + 1] || ''); i++; continue; }
      if (ch === ';' && !q) { out.push(cur); cur = ''; continue; }
      cur += ch;
    }
    out.push(cur);
    return out;
  }
  // буквы столбцов для заголовка (A…Z, AA…), как в ответе сервера
  function letters(sheets) {
    let n = 0;
    sheets.forEach(sh => sh.rows.forEach(r => { if (r.length > n) n = r.length; }));
    return Array.from({ length: n }, (_, i) => { let s = ''; for (let k = i + 1; k > 0; k = Math.floor((k - 1) / 26)) s = String.fromCharCode(65 + (k - 1) % 26) + s; return s; });
  }

  function tokenize(sec, dates) {
    const out = [];
    for (let i = 0; i < sec.length; i++) {
      const ch = sec[i];
      if (ch === '"') { const j = sec.indexOf('"', i + 1); out.push({ lit: sec.slice(i + 1, j < 0 ? sec.length : j) }); i = j < 0 ? sec.length : j; }
      else if (ch === '\\') { out.push({ lit: sec[i + 1] || '' }); i++; }
      else if (ch === '_') { out.push({ lit: ' ' }); i++; }
      else if (ch === '*') { i++; }
      else if (ch === '[') { const j = sec.indexOf(']', i); const inner = sec.slice(i + 1, j < 0 ? sec.length : j); if (/^(h+|m+|s+)$/i.test(inner)) out.push({ el: inner[0].toLowerCase(), len: inner.length }); i = j < 0 ? sec.length : j; }
      else if (/^(AM\/PM|A\/P)/i.test(sec.slice(i))) { const m = sec.slice(i).match(/^(AM\/PM|A\/P)/i)[0]; out.push({ ampm: m.length > 3 }); i += m.length - 1; }
      else if (/[yYmMdDhHsS]/.test(ch)) { let j = i; while (j < sec.length && sec[j].toLowerCase() === ch.toLowerCase()) j++; out.push({ t: ch.toLowerCase(), len: j - i }); i = j - 1; }
      // доли секунд («ss.00») — только в форматах времени; в числах «.00» — это копейки
      else if (dates && ch === '0' && out.length && out[out.length - 1].frac !== undefined) out[out.length - 1].frac++;
      else if (dates && ch === '.' && sec[i + 1] === '0') out.push({ frac: 0 });
      else if ('0#?,.%Ee+-'.includes(ch)) out.push({ ph: ch });
      else out.push({ lit: ch });
    }
    return out;
  }
  function isDate(sec) {
    const bare = sec.replace(/"[^"]*"/g, '').replace(/\\./g, '').replace(/\[(?!h+\]|m+\]|s+\])[^\]]*\]/gi, '');
    return /[dmyhs]/i.test(bare.replace(/E\+|E-/g, ''));
  }
  function fmtDate(serial, sec, d1904) {
    const days = Math.floor(serial);
    const base = d1904 ? Date.UTC(1904, 0, 1) : Date.UTC(1899, 11, 31);
    const d = d1904 ? days : days > 59 ? days - 1 : days;
    let secs = Math.round((serial - days) * 86400);
    const dt = new Date(base + d * 86400000 + secs * 1000);
    const toks = tokenize(sec, true);
    const hasAm = toks.some(t => t.ampm !== undefined);
    const hasDay = toks.some(t => t.t === 'd');
    let out = '';
    toks.forEach((t, i) => {
      if (t.lit !== undefined) { out += t.lit; return; }
      if (t.ampm !== undefined) { out += dt.getUTCHours() < 12 ? (t.ampm ? 'AM' : 'A') : (t.ampm ? 'PM' : 'P'); return; }
      if (t.el) { const total = serial * 86400; out += String(Math.floor(t.el === 'h' ? total / 3600 : t.el === 'm' ? total / 60 : total)).padStart(t.len, '0'); return; }
      if (t.frac !== undefined) { if (t.frac) out += ',' + String(Math.round((serial * 86400 % 1) * Math.pow(10, t.frac))).padStart(t.frac, '0'); return; }
      if (t.ph) { out += t.ph; return; }
      const two = v => String(v).padStart(2, '0');
      let tt = t.t;
      if (tt === 'm' && t.len <= 2) {
        const prev = toks.slice(0, i).reverse().find(x => x.t), next = toks.slice(i + 1).find(x => x.t);
        if ((prev && prev.t === 'h') || (next && next.t === 's')) tt = 'min';
      }
      switch (tt) {
        case 'y': out += t.len <= 2 ? two(dt.getUTCFullYear() % 100) : dt.getUTCFullYear(); break;
        case 'm': out += t.len === 1 ? dt.getUTCMonth() + 1 : t.len === 2 ? two(dt.getUTCMonth() + 1) : t.len === 3 ? MON[dt.getUTCMonth()] : t.len === 5 ? MONTHS[dt.getUTCMonth()][0] : (hasDay ? MONTHS_GEN : MONTHS)[dt.getUTCMonth()]; break;
        case 'd': out += t.len === 1 ? dt.getUTCDate() : t.len === 2 ? two(dt.getUTCDate()) : t.len === 3 ? DAY[dt.getUTCDay()] : DAYS[dt.getUTCDay()]; break;
        case 'h': { let hh = dt.getUTCHours(); if (hasAm) hh = hh % 12 || 12; out += t.len > 1 ? two(hh) : hh; break; }
        case 'min': out += t.len > 1 ? two(dt.getUTCMinutes()) : dt.getUTCMinutes(); break;
        case 's': out += t.len > 1 ? two(dt.getUTCSeconds()) : dt.getUTCSeconds(); break;
      }
    });
    return out;
  }
  function fmtNumber(val, sec, neg) {
    const toks = tokenize(sec);
    const first = toks.findIndex(t => t.ph && '0#?.,'.includes(t.ph));
    if (first < 0) return (neg ? '-' : '') + toks.map(t => t.lit !== undefined ? t.lit : t.ph === '%' ? '%' : '').join('');
    let last = first;
    toks.forEach((t, i) => { if (t.ph && '0#?.,Ee+-'.includes(t.ph) && i >= first) last = i; });
    const pat = toks.slice(first, last + 1).map(t => t.ph || (t.lit === ' ' ? ',' : '')).join('');
    const pre = toks.slice(0, first).map(t => t.lit !== undefined ? t.lit : t.ph === '%' ? '%' : '').join('');
    const post = toks.slice(last + 1).map(t => t.lit !== undefined ? t.lit : t.ph === '%' ? '%' : '').join('');
    const pct = (sec.replace(/"[^"]*"/g, '').match(/%/g) || []).length;
    val *= Math.pow(100, pct);
    const ePos = pat.search(/[Ee][+-]/);
    if (ePos >= 0) {
      const mant = pat.slice(0, ePos), dec = (mant.split('.')[1] || '').length;
      const [m, e] = val.toExponential(dec).split('e');
      const ed = pat.slice(ePos + 2).length || 1;
      return (neg ? '-' : '') + pre + m.replace('.', ',') + 'E' + (+e < 0 ? '-' : '+') + String(Math.abs(+e)).padStart(ed, '0') + post;
    }
    let [ip, fp = ''] = pat.split('.');
    let scale = 0;
    while (ip.endsWith(',')) { ip = ip.slice(0, -1); scale++; }
    val /= Math.pow(1000, scale);
    const group = ip.includes(',');
    const digits = fp.replace(/[^0#?]/g, '').length, minDigits = (fp.match(/0/g) || []).length;
    const minInt = (ip.match(/0/g) || []).length;
    let [si, sf = ''] = val.toFixed(digits).split('.');
    while (sf.length > minDigits && sf.endsWith('0')) sf = sf.slice(0, -1);
    if (si === '0' && minInt === 0) si = '';
    si = si.padStart(minInt, '0');
    if (group) si = si.replace(/\B(?=(\d{3})+(?!\d))/g, NBSP);
    const num = si + (sf ? ',' + sf : '');
    return (neg && (+val.toFixed(digits) !== 0) ? '-' : '') + pre + (num || '0') + post;
  }
  function formatValue(x, code, d1904) {
    if (!code || /^general$/i.test(code.trim())) return { text: general(x) };
    const secs = sections(code);
    let sec = secs[0], val = x, neg = false;
    if (x < 0 && secs.length > 1 && secs[1] !== '') { sec = secs[1]; val = -x; }
    else if (x < 0) { neg = true; val = -x; }
    if (x === 0 && secs.length > 2) sec = secs[2];
    let color = null;
    sec = sec.replace(/\[([A-Za-zА-Яа-яЁё]+)\]/g, (m, c) => { const k = c.toLowerCase(); if (NAMED[k]) { color = NAMED[k]; return ''; } return /^(h+|m+|s+)$/i.test(c) ? m : ''; });
    sec = sec.replace(/\[Color\s*(\d+)\]/gi, (m, n) => { color = PALETTE[+n + 7] ? '#' + PALETTE[+n + 7] : null; return ''; });
    sec = sec.replace(/\[\$([^\]-]*)(-[^\]]*)?\]/g, (m, sym) => sym ? '"' + sym + '"' : '');
    sec = sec.replace(/\[[<>=][^\]]*\]/g, '');
    if (sec.trim() === '@' || (sec.includes('@') && !/[0#?]/.test(sec))) return { text: general(x), color };
    if (isDate(sec)) return { text: (neg ? '-' : '') + fmtDate(val, sec, d1904), color };
    return { text: fmtNumber(val, sec, neg), color };
  }

  // ── Книга ──
  function colIndex(ref) { let n = 0; for (const ch of ref) { const c = ch.charCodeAt(0); if (c < 65 || c > 90) break; n = n * 26 + c - 64; } return n - 1; }
  function cellRef(ref) { const m = /^([A-Z]+)(\d+)$/.exec(ref || ''); return m ? { c: colIndex(m[1]), r: +m[2] - 1 } : null; }
  const textOf = el => el ? kids(el, 't').concat(kids(el, 'r').map(r => kid(r, 't')).filter(Boolean)).map(t => t.textContent).join('') || kids(el, 't').map(t => t.textContent).join('') : '';
  function richText(si) {
    const direct = kid(si, 't');
    if (direct && !kid(si, 'r')) return direct.textContent;
    return kids(si, 'r').map(r => { const t = kid(r, 't'); return t ? t.textContent : ''; }).join('');
  }

  function parseSheet(doc, name, ctx) {
    const top = doc.documentElement;
    const rows = [], rowInfo = [];
    let maxR = -1, maxC = -1, truncated = false;
    const data = kid(top, 'sheetData');
    let lastR = -1;
    for (const row of data ? data.children : []) {
      if (row.localName !== 'row') continue;
      const r = att(row, 'r') ? +att(row, 'r') - 1 : lastR + 1;
      lastR = r;
      if (r >= MAX_ROWS) { truncated = true; break; }
      const info = {};
      if (['1', 'true'].includes(att(row, 'hidden'))) info.h = 1;
      if (['1', 'true'].includes(att(row, 'customHeight')) && att(row, 'ht')) info.ht = +att(row, 'ht');
      if (info.h || info.ht) rowInfo[r] = info;
      let lastC = -1;
      for (const c of row.children) {
        if (c.localName !== 'c') continue;
        const ref = att(c, 'r');
        const col = ref ? colIndex(ref) : lastC + 1;
        lastC = col;
        if (col >= MAX_COLS) { truncated = true; continue; }
        const xf = ctx.styles.xfs[+att(c, 's') || 0] || { font: {} };
        const item = {};
        const f = xf.font || {};
        if (f.b) item.b = 1;
        if (f.i) item.i = 1;
        if (f.u) item.u = 1;
        if (f.s) item.s = 1;
        if (f.color && f.color !== '#000000') item.fg = f.color;
        if (f.sz && Math.abs(f.sz - ctx.styles.baseSz) > 0.4) item.fs = Math.round(f.sz / ctx.styles.baseSz * 100) / 100;
        if (xf.fill && xf.fill !== '#FFFFFF') item.bg = xf.fill;
        if (xf.h === 'center' || xf.h === 'right' || xf.h === 'centerContinuous') item.a = xf.h === 'right' ? 'right' : 'center';
        if (xf.h === 'left') item.a = 'left';
        if (xf.wrap) item.w = 1;
        if (xf.border && xf.border.some(Boolean)) item.bd = xf.border;
        const t = att(c, 't'), vEl = kid(c, 'v');
        let v = null;
        if (t === 's') v = vEl ? ctx.shared[+vEl.textContent] || '' : '';
        else if (t === 'inlineStr') v = richText(kid(c, 'is'));
        else if (t === 'b') v = vEl && vEl.textContent === '1' ? 'ИСТИНА' : 'ЛОЖЬ';
        else if (t === 'e' || t === 'str') v = vEl ? vEl.textContent : '';
        else if (t === 'd') v = vEl ? vEl.textContent.replace(/T.*/, '').split('-').reverse().join('.') : '';
        else if (vEl && vEl.textContent !== '') {
          const x = parseFloat(vEl.textContent);
          if (isFinite(x)) {
            const code = ctx.styles.numFmts[xf.fmt] || BUILTIN[xf.fmt] || 'General';
            const res = formatValue(x, code, ctx.d1904);
            v = res.text; item.n = 1;
            if (res.color) item.fg = res.color;
          } else v = vEl.textContent;
        }
        if (v != null && v !== '') item.v = String(v).slice(0, MAX_TEXT);
        const visible = item.v != null || item.bg || item.bd;
        if (!visible && Object.keys(item).length === 0) continue;
        (rows[r] || (rows[r] = []))[col] = item;
        if (visible) { if (r > maxR) maxR = r; if (col > maxC) maxC = col; }
      }
    }
    const merges = all(kid(top, 'mergeCells'), 'mergeCell').map(m => {
      const [a, b] = (att(m, 'ref') || '').split(':').map(cellRef);
      return a && b ? { r: a.r, c: a.c, rs: b.r - a.r + 1, cs: b.c - a.c + 1 } : null;
    }).filter(m => m && m.r <= maxR && m.c <= maxC && m.r < MAX_ROWS && m.c < MAX_COLS);
    merges.forEach(m => { maxR = Math.max(maxR, Math.min(MAX_ROWS - 1, m.r + m.rs - 1)); maxC = Math.max(maxC, Math.min(MAX_COLS - 1, m.c + m.cs - 1)); });
    const width = maxC + 1, height = maxR + 1;
    const cols = [];
    for (let i = 0; i < width; i++) cols.push({});
    for (const col of all(kid(top, 'cols'), 'col')) {
      const lo = +att(col, 'min') - 1, hi = Math.min(width - 1, +att(col, 'max') - 1);
      for (let i = lo; i <= hi; i++) {
        if (['1', 'true'].includes(att(col, 'hidden'))) cols[i] = { h: 1 };
        else if (att(col, 'width')) cols[i] = { w: Math.round(parseFloat(att(col, 'width')) * 10) / 10 };
      }
    }
    const out = [];
    for (let r = 0; r < height; r++) {
      const src = rows[r] || [];
      const line = [];
      for (let c = 0; c < width; c++) line.push(src[c] || {});
      out.push(line);
    }
    let freeze = null;
    const pane = path(top, 'sheetViews', 'sheetView', 'pane');
    if (pane && /frozen/i.test(att(pane, 'state') || '')) {
      const xs = +att(pane, 'xSplit') || 0, ys = +att(pane, 'ySplit') || 0;
      if (xs || ys) freeze = { r: ys, c: xs };
    }
    const sheet = { name, rows: out, cols, merges, freeze, truncated };
    const ri = rowInfo.slice(0, height);
    if (ri.some(Boolean)) sheet.rowInfo = Array.from(ri, x => x || {});
    return sheet;
  }

  async function readXlsx(buf, filename) {
    const zip = Kov.Zip.open(buf);
    const top = await Kov.Zip.x.rels(zip, '');
    const main = (byType(top, 'officeDocument') || { target: 'xl/workbook.xml' }).target;
    const wb = await zip.xml(main);
    if (!wb) throw new Error('В файле нет книги Excel');
    const wr = await rels(zip, main);
    const theme = byType(wr, 'theme') ? parseTheme(await zip.xml(byType(wr, 'theme').target)) : [];
    const styles = parseStyles(byType(wr, 'styles') ? await zip.xml(byType(wr, 'styles').target) : null, theme);
    const sst = byType(wr, 'sharedStrings') ? await zip.xml(byType(wr, 'sharedStrings').target) : null;
    const shared = sst ? kids(sst.documentElement, 'si').map(richText) : [];
    const pr = kid(wb.documentElement, 'workbookPr');
    const ctx = { styles, shared, d1904: !!pr && ['1', 'true'].includes(att(pr, 'date1904')) };
    const sheets = [];
    for (const s of kids(kid(wb.documentElement, 'sheets'), 'sheet').slice(0, MAX_SHEETS)) {
      if (att(s, 'state') && att(s, 'state') !== 'visible') continue;
      const rel = wr[att(s, 'id')];
      if (!rel || rel.type !== 'worksheet') continue;
      const doc = await zip.xml(rel.target);
      if (doc) sheets.push(parseSheet(doc, att(s, 'name') || 'Лист', ctx));
    }
    if (!sheets.length) throw new Error('В книге нет видимых листов');
    return { filename, sheets, letters: letters(sheets), source: 'device' };
  }

  function readCsv(buf, filename) {
    const u8 = new Uint8Array(buf);
    let text;
    try { text = new TextDecoder('utf-8', { fatal: true }).decode(u8); } catch (e) { text = new TextDecoder('windows-1251').decode(u8); }
    if (text.charCodeAt(0) === 0xfeff) text = text.slice(1);
    const head = text.slice(0, 20000);
    const delim = /\.tsv$/i.test(filename) ? '\t' : [';', ',', '\t'].map(d => [d, head.split(d).length]).sort((a, b) => b[1] - a[1])[0][0];
    const rows = [];
    let row = [], cell = '', q = false, truncated = false;
    const push = () => { if (row.length < MAX_COLS) row.push(cell); else truncated = true; cell = ''; };
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      if (q) { if (ch === '"') { if (text[i + 1] === '"') { cell += '"'; i++; } else q = false; } else cell += ch; continue; }
      if (ch === '"' && cell === '') q = true;
      else if (ch === delim) push();
      else if (ch === '\n' || ch === '\r') { if (ch === '\r' && text[i + 1] === '\n') i++; push(); rows.push(row); row = []; if (rows.length >= MAX_ROWS) { truncated = i < text.length - 1; break; } }
      else cell += ch;
    }
    if (cell !== '' || row.length) { push(); rows.push(row); }
    const width = Math.max(0, ...rows.map(r => r.length));
    const out = rows.map(r => Array.from({ length: width }, (_, c) => {
      const v = (r[c] || '').slice(0, MAX_TEXT);
      return v === '' ? {} : /^-?\d[\d\s\u00a0]*([.,]\d+)?$/.test(v.trim()) ? { v, n: 1 } : { v };
    }));
    const stem = filename.replace(/\.[^.]+$/, '').slice(0, 31) || 'Данные';
    const sheets = [{ name: stem, rows: out, cols: [], merges: [], freeze: null, truncated }];
    return { filename, sheets, letters: letters(sheets), source: 'device' };
  }

  const SUPPORTED = /\.(xlsx|xlsm|csv|tsv)$/i;
  async function read(buf, filename) { return /\.(csv|tsv)$/i.test(filename) ? readCsv(buf, filename) : readXlsx(buf, filename); }
  async function fromUrl(url, filename) {
    const res = await fetch(url, { credentials: 'same-origin' });
    if (!res.ok) throw new Error('Файл не получен: ' + res.status);
    return read(await res.arrayBuffer(), filename);
  }
  Kov.XlsxReader = { read, fromUrl, formatValue, supports: name => SUPPORTED.test(name || '') && (typeof DecompressionStream !== 'undefined' || !!Kov.Gzip || /\.(csv|tsv)$/i.test(name)) };
})(typeof self !== 'undefined' ? self : this);
