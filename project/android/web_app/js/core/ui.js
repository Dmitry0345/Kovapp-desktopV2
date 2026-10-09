/* Мелкие помощники интерфейса: сборка DOM, диалоги, таблицы, аватары, даты. */
window.Kov = window.Kov || {};

Kov.UI = (function () {
  const I = name => Kov.Icons.svg(name);

  // ── DOM ──────────────────────────────────────────────────────────────────

  /** h('div.card#id', {onclick, attrs...}, children...) — короткая сборка элементов */
  function h(spec, props, ...children) {
    const [tagPart, ...classParts] = spec.split('.');
    const [tag, id] = tagPart.split('#');
    const el = document.createElement(tag || 'div');
    if (id) el.id = id;
    if (classParts.length) el.className = classParts.join(' ');
    if (props && typeof props === 'object' && !(props instanceof Node) && !Array.isArray(props)) {
      Object.entries(props).forEach(([key, value]) => {
        if (value === null || value === undefined || value === false) return;
        if (key.startsWith('on') && typeof value === 'function') el.addEventListener(key.slice(2).toLowerCase(), value);
        else if (key === 'html') el.innerHTML = value;
        else if (key === 'text') el.textContent = value;
        else if (key === 'style' && typeof value === 'object') Object.assign(el.style, value);
        else if (key === 'dataset') Object.assign(el.dataset, value);
        else if (key === 'class') el.className += (el.className ? ' ' : '') + value;
        else if (key in el && key !== 'list') { try { el[key] = value; } catch (e) { el.setAttribute(key, value); } }
        else el.setAttribute(key, value === true ? '' : value);
      });
    } else if (props !== undefined) {
      children.unshift(props);
    }
    append(el, children);
    return el;
  }

  function append(el, children) {
    children.flat(Infinity).forEach(child => {
      if (child === null || child === undefined || child === false) return;
      el.append(child instanceof Node ? child : document.createTextNode(String(child)));
    });
    return el;
  }

  /**
   * Обновление списка без пересборки.
   *
   * Обычная перерисовка — очистить и собрать заново — стирает узлы, которые
   * не изменились. Браузер заново раскладывает и красит их, картинки
   * перезагружаются, анимации появления проигрываются снова: со стороны это
   * выглядит как мерцание всего экрана при каждом новом сообщении.
   *
   * Здесь узлы сверяются по ключу: что не изменилось — остаётся на месте,
   * что изменилось — пересобирается поодиночке, лишнее убирается. Двигаются
   * только те узлы, которые действительно стоят не там.
   *
   * host  — куда рисуем
   * items — данные
   * key   — устойчивый ключ записи (номер, id)
   * sign  — отпечаток содержимого: если он тот же, узел не трогаем
   * make  — как построить узел записи
   */
  function syncList(host, items, opts) {
    const key = opts.key || (x => x.id);
    const sign = opts.sign || (() => '');
    const make = opts.make;
    const kept = new Map();
    [...host.children].forEach(el => {
      const k = el.dataset && el.dataset.syncKey;
      if (k !== undefined && k !== null && k !== '') kept.set(k, el);
      else el.remove();                    // чужие узлы в списке не задерживаются
    });

    const next = [];
    (items || []).forEach((item, i) => {
      const k = String(key(item, i));
      const s = String(sign(item, i));
      let el = kept.get(k);
      if (el && el.dataset.syncSign === s) {
        kept.delete(k);
      } else {
        if (el) el.remove();
        el = make(item, i);
        if (!el) return;
        el.dataset.syncKey = k;
        el.dataset.syncSign = s;
        kept.delete(k);
      }
      next.push(el);
    });
    kept.forEach(el => el.remove());

    // Расставляем по порядку, трогая только то, что стоит не на месте
    next.forEach((el, i) => {
      if (host.children[i] !== el) host.insertBefore(el, host.children[i] || null);
    });
    return next;
  }

  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }

  function icon(name, opts) { const span = h('span.ico', { html: Kov.Icons.svg(name, opts) }); return span.firstChild; }

  function button(text, opts) {
    opts = opts || {};
    const cls = 'btn' + (opts.kind ? ' ' + opts.kind : '') + (opts.cls ? ' ' + opts.cls : '') + (!text && opts.icon ? ' icon' : '');
    const el = h('button.' + cls.replace(/ /g, '.'), { type: 'button', title: opts.title || null, onclick: opts.onClick, disabled: opts.disabled || false });
    if (opts.hidden) el.hidden = true;      // раньше опция молча терялась
    if (opts.icon) el.append(icon(opts.icon));
    if (text) el.append(text);
    return el;
  }

  function sectionHeader(title, glyph) {
    return h('div.section-header', glyph ? icon(glyph) : null, h('span.title', title), h('span.rule'));
  }

  function searchInput(placeholder, onInput) {
    const input = h('input.input', { type: 'search', placeholder, autocomplete: 'off', oninput: e => onInput && onInput(e.target.value) });
    return { el: h('div.search', icon('search'), input), input };
  }

  /** Курсор в поле: на компьютере — как обычно; на телефоне клавиатура открывается только касанием поля,
   *  поэтому поле лишь прокручивается в зону видимости */
  function softFocus(el) {
    if (!el) return;
    if (touchUi()) { try { el.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); } catch (e) { /* */ } return; }
    setTimeout(() => { try { el.focus(); } catch (e) { /* */ } }, 30);
  }

  /** Режим письма (телефон): длинный текст — во весь экран над клавиатурой, написанное всегда видно */
  function writer(src, title) {
    const area = h('textarea.writer-area', { placeholder: src.placeholder || '' });
    area.value = src.value;
    if (src.maxLength > 0) area.maxLength = src.maxLength;
    const vv = window.visualViewport;
    let closed = false;
    const fit = () => { if (vv) { root.style.top = vv.offsetTop + 'px'; root.style.height = vv.height + 'px'; } };
    const close = () => {
      if (closed) return; closed = true;
      if (vv) { vv.removeEventListener('resize', fit); vv.removeEventListener('scroll', fit); }
      root.remove();
    };
    const done = () => {
      if (area.value !== src.value) {
        src.value = area.value;
        src.dispatchEvent(new Event('input', { bubbles: true }));
        src.dispatchEvent(new Event('change', { bubbles: true }));
      }
      close();
    };
    const root = h('div.kov-sheet-root.writer-root',
      h('div.writer-top',
        h('button.writer-cancel', { type: 'button', 'data-close': '1', onclick: close }, 'Отмена'),
        h('b.writer-title', title || 'Текст'),
        h('button.btn.primary.writer-done', { type: 'button', onclick: done }, 'Готово')),
      area);
    document.body.append(root);
    if (vv) { vv.addEventListener('resize', fit); vv.addEventListener('scroll', fit); fit(); }
    area.focus();
    try { area.setSelectionRange(area.value.length, area.value.length); } catch (e) { /* */ }
    area.scrollTop = area.scrollHeight;
    return { close, root };
  }

  /** Окна и шторки: открытое последним — поверх остальных (окно из шторки не прячется под ней) */
  let zTop = 1300;
  function raise(el) {
    if (!el) return el;
    const open = document.querySelectorAll('.modal-root, .kov-sheet-root:not(.writer-root), .viewer-root').length;
    if (open <= 1) zTop = 1300;
    el.style.zIndex = String(++zTop);
    return el;
  }

  // Телефон: касание многострочного поля — режим письма; окна и шторки — над клавиатурой,
  // поле ввода прокручивается в видимую часть экрана
  (function keyboardAware() {
    // режим письма — только после касания пальцем, а не при программной постановке курсора
    let tapEl = null, tapAt = 0;
    document.addEventListener('pointerdown', e => { tapEl = e.target; tapAt = Date.now(); }, true);
    const byTap = el => !!tapEl && Date.now() - tapAt < 1500 && (tapEl === el || (tapEl.contains && tapEl.contains(el)) || el.contains(tapEl));
    document.addEventListener('focusin', e => {
      const el = e.target;
      if (!touchUi() || !el || !el.matches) return;
      if (el.tagName === 'TEXTAREA' && !el.readOnly && !el.disabled && byTap(el)
          && !el.closest('#kovChat, .writer-root, [data-no-writer]') && !el.classList.contains('attach-caption')) {
        const field = el.closest('.field');
        const lbl = field && field.querySelector('label, .field-label');
        const title = el.dataset.writerTitle || (lbl && lbl.textContent) || el.placeholder || 'Текст';
        writer(el, String(title).trim().replace(/[:*\s]+$/, ''));
        return;
      }
      if (el.matches('input:not([type=checkbox]):not([type=radio]):not([type=file]):not([type=range]), textarea')) {
        setTimeout(() => { try { el.scrollIntoView({ block: 'center', behavior: 'smooth' }); } catch (x) { /* */ } }, 320);
      }
    });
    // Строка ввода под клавиатурой: её текст показывается сверху, поверх всего, пока идёт ввод;
    // закончили ввод — строка исчезает, всё на прежних местах
    const TEXT = 'input:not([type=checkbox]):not([type=radio]):not([type=file]):not([type=range]):not([type=button]):not([type=submit]):not([type=color]):not([type=date]):not([type=time]):not([type=datetime-local]), textarea';
    let lift = null, liftTimer = null;
    const liftEl = () => {
      if (!lift) {
        lift = h('div.kb-lift', { hidden: true, 'aria-hidden': 'true' }, h('div.kb-lift-label'), h('div.kb-lift-line', h('span.kb-lift-text'), h('i.kb-lift-caret')));
        document.body.append(lift);
      }
      return lift;
    };
    const coveredByKeyboard = el => {
      const v = window.visualViewport;
      if (!v) return false;
      const r = el.getBoundingClientRect();
      return r.height > 0 && (r.bottom > v.offsetTop + v.height - 2 || r.top < v.offsetTop);
    };
    const paintLift = () => {
      const el = document.activeElement;
      if (!lift || lift.hidden || !el || !el.matches || !el.matches(TEXT)) return;
      const v = el.value || '';
      lift.querySelector('.kb-lift-text').textContent = el.type === 'password' ? '•'.repeat(v.length) : v;
    };
    const checkLift = () => {
      const el = document.activeElement;
      if (!touchUi() || !el || !el.matches || !el.matches(TEXT) || el.closest('.writer-root')) { if (lift) lift.hidden = true; return; }
      if (!coveredByKeyboard(el)) { if (lift) lift.hidden = true; return; }
      const bar = liftEl();
      const field = el.closest('.field');
      const lbl = field && field.querySelector('label, .field-label');
      bar.querySelector('.kb-lift-label').textContent = String((lbl && lbl.textContent) || el.placeholder || el.getAttribute('aria-label') || '').trim();
      const v = window.visualViewport;
      bar.style.top = ((v ? v.offsetTop : 0) + 6) + 'px';
      bar.hidden = false;
      paintLift();
    };
    const later = ms => { clearTimeout(liftTimer); liftTimer = setTimeout(checkLift, ms); };
    document.addEventListener('focusin', () => later(450));
    document.addEventListener('focusout', () => later(60));
    document.addEventListener('input', paintLift, true);

    const vv = window.visualViewport;
    if (!vv) return;
    vv.addEventListener('resize', () => later(120));
    vv.addEventListener('scroll', () => later(120));
    const set = () => {
      const r = document.documentElement.style;
      r.setProperty('--vvh', Math.round(vv.height) + 'px');
      r.setProperty('--kb', Math.max(0, Math.round(window.innerHeight - vv.height - vv.offsetTop)) + 'px');
    };
    vv.addEventListener('resize', set); vv.addEventListener('scroll', set); set();
  })();

  // сенсорный экран: вместо системного списка Android — кнопка и шторка снизу в стиле приложения
  function touchUi() { return window.matchMedia('(pointer: coarse)').matches || window.matchMedia('(max-width: 760px)').matches; }

  function select(options, value, onChange, opts) {
    if (touchUi()) return sheetSelect(options, value, onChange, opts);
    const el = h('select.select', { onchange: e => onChange && onChange(parseValue(e.target.value)) });
    options.forEach(([val, label]) => el.append(h('option', { value: val === null ? '' : String(val), text: label })));
    if (value !== undefined && value !== null) el.value = String(value);
    if (opts && opts.title) el.title = opts.title;
    el.getValue = () => parseValue(el.value);
    el.setOptions = (opts2, keep) => {
      const current = el.value; clear(el);
      opts2.forEach(([val, label]) => el.append(h('option', { value: val === null ? '' : String(val), text: label })));
      if (keep && Array.from(el.options).some(o => o.value === current)) el.value = current;
    };
    return el;
  }
  function parseValue(v) { return v === '' ? null : (/^-?\d+$/.test(v) ? Number(v) : v); }

  /** Тот же интерфейс, что у select: value, getValue, setOptions, событие change */
  function sheetSelect(options, value, onChange, opts) {
    let list = options.slice();
    const key = v => (v === null || v === undefined ? '' : String(v));
    let current = value !== undefined && value !== null ? String(value) : (list.length ? key(list[0][0]) : '');
    const label = h('span.t');
    const el = h('button.select.picker.sheet-select', { type: 'button' }, label, h('span.chev', icon('chevron_down')));
    if (opts && opts.title) el.title = opts.title;
    const paint = () => { const o = list.find(x => key(x[0]) === current); label.textContent = o ? o[1] : ''; };
    Object.defineProperty(el, 'value', { get: () => current, set: v => { current = key(v); paint(); } });
    el.getValue = () => parseValue(current);
    el.setOptions = (next, keep) => {
      const prev = current;
      list = next.slice();
      current = keep && list.some(o => key(o[0]) === prev) ? prev : (list.length ? key(list[0][0]) : '');
      paint();
    };
    el.addEventListener('click', () => {
      if (el.disabled) return;
      optionSheet((opts && opts.title) || 'Выберите', list.map(o => ({ value: key(o[0]), label: o[1], on: key(o[0]) === current })), v => {
        if (v === current) return;
        current = v; paint();
        if (onChange) onChange(parseValue(v));
        el.dispatchEvent(new Event('change', { bubbles: true }));
      });
    });
    paint();
    return el;
  }

  /** Шторка снизу с произвольным содержимым (фильтры на телефоне и т. п.) */
  // меню с серой полоской: зажать полоску (или шапку) и протянуть вниз — свернуть, как крестиком
  (function sheetDrag() {
    let g = null;
    const move = e => {
      if (!g || e.touches.length !== 1) return;
      e.preventDefault();
      g.dy = Math.max(0, e.touches[0].clientY - g.y0);
      g.sheet.style.transform = 'translateY(' + g.dy + 'px)';
      if (g.back) g.back.style.opacity = String(Math.max(0.15, 1 - g.dy / (g.sheet.offsetHeight || 400)));
    };
    const end = () => {
      if (!g) return;
      const { sheet, root, back, dy, t0 } = g;
      g = null;
      window.removeEventListener('touchmove', move);
      const fast = dy > 30 && dy / Math.max(1, Date.now() - t0) > 0.6;
      if (dy > Math.min(140, sheet.offsetHeight * 0.25) || fast) {
        sheet.style.transition = 'transform .18s ease';
        sheet.style.transform = 'translateY(100%)';
        const x = root.querySelector('.kov-sheet-x, [data-close]');
        setTimeout(() => { if (x) x.click(); else if (back) back.click(); }, 170);
      } else {
        sheet.style.transition = 'transform .2s ease';
        sheet.style.transform = '';
        if (back) back.style.opacity = '';
      }
    };
    document.addEventListener('touchstart', e => {
      if (e.touches.length !== 1) return;
      const t = e.target, grip = t && t.closest && t.closest('.kov-sheet-handle, .kov-sheet-head');
      if (!grip || t.closest('button, input, textarea, select, a')) return;
      const sheet = grip.closest('.kov-sheet'), root = sheet && sheet.closest('.kov-sheet-root');
      if (!sheet || !root) return;
      g = { sheet, root, back: root.querySelector('.kov-sheet-back'), y0: e.touches[0].clientY, t0: Date.now(), dy: 0 };
      sheet.style.transition = 'none';
      window.addEventListener('touchmove', move, { passive: false });
    }, { passive: true });
    document.addEventListener('touchend', end);
    document.addEventListener('touchcancel', end);
  })();

  function bottomSheet(title, body, opts) {
    const root = h('div.kov-sheet-root.bottom-root');
    const close = () => { root.classList.remove('open'); setTimeout(() => root.remove(), 200); if (opts && opts.onClose) opts.onClose(); };
    root.append(h('div.kov-sheet-back', { onclick: close }),
      h('div.kov-sheet.bottom-sheet', h('div.kov-sheet-handle'),
        h('div.kov-sheet-head', h('b', title),
          h('button.kov-sheet-x', { type: 'button', title: 'Закрыть', 'data-close': '1', onclick: close }, icon('close'))),
        h('div.bottom-body', body)));
    document.body.append(root); raise(root);   // последним открытое — поверх остальных
    requestAnimationFrame(() => root.classList.add('open'));
    return { close, root };
  }

  /** Шторка снизу со списком: текущий пункт отмечен, у длинных списков — поиск */
  function optionSheet(title, items, onPick) {
    const root = h('div.kov-sheet-root.option-root');
    const close = () => { root.classList.remove('open'); setTimeout(() => root.remove(), 200); };
    const listEl = h('div.option-list');
    const draw = q => {
      clear(listEl);
      items.filter(o => !q || o.label.toLowerCase().includes(q)).forEach(o => listEl.append(
        h('button.option-row' + (o.on ? '.on' : ''), { type: 'button', onclick: () => { close(); onPick(o.value); } },
          h('span', o.label), o.on ? icon('check') : null)));
    };
    const search = items.length > 8
      ? h('input.input.option-search', { type: 'search', placeholder: 'Поиск…', oninput: e => draw(e.target.value.trim().toLowerCase()) })
      : null;
    root.append(h('div.kov-sheet-back', { onclick: close }),
      h('div.kov-sheet.option-sheet', h('div.kov-sheet-handle'),
        h('div.kov-sheet-head', h('b', title),
          h('button.kov-sheet-x', { type: 'button', title: 'Закрыть', 'data-close': '1', onclick: close }, icon('close'))),
        search, listEl));
    draw('');
    document.body.append(root); raise(root);   // последним открытое — поверх остальных
    requestAnimationFrame(() => root.classList.add('open'));
    const on = listEl.querySelector('.on');
    if (on) on.scrollIntoView({ block: 'center' });
  }

  /**
   * pickerSelect(options, value, onChange, opts) — список с поиском.
   * options: [[значение, подпись, подпись2?], …]. Внешне как обычный select,
   * но при нажатии открывает панель с поиском и списком ограниченной высоты.
   * opts: { placeholder, searchPlaceholder, title, empty }
   */
  function pickerSelect(options, value, onChange, opts) {
    opts = opts || {};
    let list = options || [];
    let current = value === undefined ? null : value;
    const label = h('span.t');
    const el = h('button.select.picker', { type: 'button', title: opts.title || '' },
      label, h('span.chev', icon('chevron_down')));
    const search = searchInput(opts.searchPlaceholder || 'Поиск…', () => fill());
    const rows = h('div.picker-list');
    const panel = h('div.picker-panel', { hidden: true }, h('div.picker-search', search.el), rows);
    const wrap = h('div.picker-wrap', el, panel);

    function labelOf(v) {
      const found = list.find(o => String(o[0]) === String(v));
      return found ? found[1] : (opts.placeholder || '—');
    }
    // Класс называется no-value, а не empty: .empty в разделах — это большая
    // заглушка «ничего нет», от неё кнопка вырастала до 128 px
    function paint() { label.textContent = labelOf(current); el.classList.toggle('no-value', current === null || current === undefined); }
    function fill() {
      const needle = (search.input.value || '').trim().toLowerCase();
      clear(rows);
      const shown = list.filter(o => !needle || String(o[1] || '').toLowerCase().includes(needle)
        || String(o[2] || '').toLowerCase().includes(needle));
      if (!shown.length) { rows.append(h('div.muted.small', { style: { padding: '.5rem' } }, opts.empty || 'Ничего не найдено')); return; }
      shown.forEach(([val, text, sub]) => {
        const active = String(val) === String(current);
        rows.append(h('button.picker-row' + (active ? '.on' : ''), { type: 'button', onclick: () => choose(val) },
          h('span.t', text), sub ? h('span.s', sub) : null));
      });
    }
    function choose(val) { current = val; paint(); close(); onChange && onChange(val); }
    // телефон, фильтр в разделе со списком: панель во всю ширину экрана под кнопкой, высотой до
    // клавиатуры (привязанная к кнопке в полэкрана, она уходила за край). В формах — как было.
    let sheet = false;
    function place() {
      const r = el.getBoundingClientRect();
      panel.style.top = Math.round(r.bottom + 4) + 'px';
      rows.style.maxHeight = Math.max(160, Math.round(window.innerHeight - r.bottom - 76)) + 'px';
    }
    // телефон: выбор — отдельной шторкой поверх всего; поиск сверху, список ниже прокручивается
    let pickSheet = null;
    function openSheet() {
      search.input.value = ''; fill();
      const field = wrap.closest('.field');
      const lbl = field && field.querySelector('label, .field-label');
      const title = opts.sheetTitle || (lbl && lbl.textContent.trim()) || opts.placeholder || 'Выбор';
      const slot = panel.querySelector('.picker-search');
      pickSheet = bottomSheet(title, h('div.picker-sheet', h('div.picker-search', search.el), rows), {
        onClose: () => { pickSheet = null; slot.append(search.el); panel.append(rows); },
      });
      pickSheet.root.classList.add('picker-sheet-root');
    }
    function open() {
      if (touchUi()) return openSheet();
      if (!panel.hidden) return close();
      panel.hidden = false; search.input.value = ''; fill();
      sheet = window.matchMedia('(max-width: 760px)').matches && !!wrap.closest('.toolbar');
      panel.classList.toggle('sheet', sheet);
      if (sheet) { place(); window.addEventListener('resize', place); }
      // панель не должна вылезать за нижний край экрана
      panel.classList.toggle('up', !sheet && el.getBoundingClientRect().bottom + 260 > window.innerHeight);
      softFocus(search.input);   // телефон: клавиатура — только касанием поля
      document.addEventListener('mousedown', outside, true);
      document.addEventListener('keydown', onKey, true);
    }
    function close() {
      if (pickSheet) { const s = pickSheet; pickSheet = null; s.close(); return; }
      panel.hidden = true;
      if (sheet) {
        window.removeEventListener('resize', place);
        panel.classList.remove('sheet'); panel.style.top = ''; rows.style.maxHeight = ''; sheet = false;
      }
      document.removeEventListener('mousedown', outside, true);
      document.removeEventListener('keydown', onKey, true);
    }
    function outside(e) { if (!wrap.contains(e.target)) close(); }
    function onKey(e) { if (e.key === 'Escape') { e.stopPropagation(); close(); } }
    el.addEventListener('click', open);

    wrap.getValue = () => current;
    wrap.setValue = v => { current = v; paint(); };
    wrap.setOptions = (next, keep) => { list = next || []; if (!keep) current = null; paint(); if (!panel.hidden) fill(); };
    wrap.setDisabled = v => { el.disabled = !!v; wrap.classList.toggle('disabled', !!v); };
    paint();
    return wrap;
  }

  function segmented(items, active, onChange) {
    const wrap = h('div.segmented');
    const buttons = items.map(([key, label, glyph]) => {
      const b = button(label, { icon: glyph, onClick: () => { setActive(key); onChange && onChange(key, b); } });
      b.dataset.key = key; wrap.append(b); return b;
    });
    function setActive(key) { buttons.forEach(b => b.classList.toggle('on', b.dataset.key === String(key))); wrap.dataset.active = key; }
    setActive(active); wrap.setActive = setActive;
    return wrap;
  }

  // ── строка кнопок «по месту» ────────────────────────────────────────────

  /** Ширина, нужная кнопке без сжатия: значок, подпись, промежуток, поля и рамка */
  function needWidth(btn) {
    const cs = getComputedStyle(btn);
    let w = 0, n = 0;
    btn.childNodes.forEach(node => {
      if (node.nodeType === 1) {
        if (!node.getClientRects().length) return;
        w += node.getBoundingClientRect().width; n++;
      } else if (node.nodeType === 3 && node.textContent.trim()) {
        const r = document.createRange(); r.selectNodeContents(node);
        w += r.getBoundingClientRect().width; n++;
      }
    });
    return w + (parseFloat(cs.columnGap) || 0) * Math.max(0, n - 1)
      + parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight) + parseFloat(cs.borderLeftWidth) + parseFloat(cs.borderRightWidth);
  }

  function rowFits(row) {
    if (row.scrollWidth > row.clientWidth + 1) return false;
    return [...row.querySelectorAll('.btn')].every(b => !b.getClientRects().length || needWidth(b) <= b.getBoundingClientRect().width + 0.5);
  }

  /**
   * Подписи не помещаются при выбранном размере текста — строка по очереди
   * получает классы steps (компактнее с каждым), пока всё не поместится.
   * Решает место для текста, а не ширина экрана; проверка повторяется при
   * любом изменении размеров строки и её кнопок.
   */
  function fitRow(row, steps) {
    if (typeof ResizeObserver === 'undefined') return row;
    let queued = false, seen = false;
    const fit = () => {
      queued = false;
      if (!row.isConnected || !row.getClientRects().length) return;
      steps.forEach(c => row.classList.remove(c));
      for (const c of steps) { if (rowFits(row)) return; row.classList.add(c); }
    };
    const schedule = () => { if (!queued) { queued = true; requestAnimationFrame(fit); } };
    const ro = new ResizeObserver(() => {
      if (!row.isConnected) { if (seen) { ro.disconnect(); mo.disconnect(); } return; }
      seen = true; schedule();
    });
    // кнопки пересобираются (активные / архивные) — следим и за новыми
    const mo = new MutationObserver(() => { [...row.children].forEach(c => ro.observe(c)); schedule(); });
    ro.observe(row);
    [...row.children].forEach(c => ro.observe(c));
    mo.observe(row, { childList: true });
    row._fit = fit;
    return row;
  }

  // ── аватар (тот же хэш и палитра, что в theme.avatar_color) ─────────────

  const PALETTE = ['#546E7A', '#5C6BC0', '#26A69A', '#8D6E63', '#7E57C2', '#42A5F5', '#66BB6A', '#EC407A', '#EF6C00', '#78909C'];
  function avatarColor(text) {
    if (!text) return PALETTE[0];
    let value = 0;
    for (const ch of String(text)) value = (Math.imul(value, 31) + ch.codePointAt(0)) >>> 0;
    return PALETTE[value % PALETTE.length];
  }
  function initials(person) {
    const l = (person && person.last_name || '')[0] || '', f = (person && person.first_name || '')[0] || '';
    return (l + f).toUpperCase() || '?';
  }
  function fullName(person, withMiddle) {
    if (!person) return '';
    const parts = [person.last_name, person.first_name, withMiddle ? person.middle_name : null].filter(Boolean);
    return parts.join(' ') || person.name || '';
  }
  function avatar(person, size, photoUrl) {
    const el = h('span.avatar' + (size ? '.' + size : ''), { style: { background: avatarColor(fullName(person)) }, text: photoUrl ? '' : initials(person), title: fullName(person, true) });
    if (photoUrl) {
      const img = h('img', { src: photoUrl, alt: '', loading: 'lazy' });
      img.onerror = () => { img.remove(); el.textContent = initials(person); };
      el.append(img);
    }
    return el;
  }

  // ── даты ─────────────────────────────────────────────────────────────────

  function parseDate(v) { if (!v) return null; const d = new Date(String(v).length === 10 ? v + 'T00:00:00' : v); return isNaN(d) ? null : d; }
  function fmtDate(v) { const d = parseDate(v); return d ? d.toLocaleDateString('ru-RU') : '—'; }
  function fmtDateTime(v) { const d = parseDate(v); return d ? d.toLocaleDateString('ru-RU') + ' ' + d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }) : '—'; }
  function isoDate(d) { const x = new Date(d); return x.getFullYear() + '-' + String(x.getMonth() + 1).padStart(2, '0') + '-' + String(x.getDate()).padStart(2, '0'); }
  function deadlineState(deadline, active) {
    const d = parseDate(deadline);
    if (!d || active === false) return null;
    const today = new Date(); today.setHours(0, 0, 0, 0);
    if (d < today) return 'overdue';
    if ((d - today) / 86400000 < 1) return 'urgent';
    return null;
  }

  // ── таблица с карточками на телефоне ─────────────────────────────────────

  /**
   * table({ columns:[{key,label,align,render(row)}], rows, rowKey, onActivate(row), onSelect(row), rowClass(row), cards:true })
   */
  /**
   * longPress(el, fn) — удержание пальцем около полусекунды.
   *
   * Работает только для касаний: мышью выделяют обычным нажатием. Жест
   * отменяется, если палец сдвинулся (человек листает) или отпущен раньше.
   * Короткая вибрация подтверждает, что выделение сработало.
   */
  function longPress(el, fn, ms) {
    let timer = null, startX = 0, startY = 0, fired = false;
    const cancel = () => { clearTimeout(timer); timer = null; el.classList.remove('pressing'); };
    el.addEventListener('touchstart', e => {
      if (!e.touches || e.touches.length !== 1) return;
      fired = false;
      startX = e.touches[0].clientX; startY = e.touches[0].clientY;
      el.classList.add('pressing');
      timer = setTimeout(() => {
        fired = true;
        el.classList.remove('pressing');
        try { navigator.vibrate && navigator.vibrate(12); } catch (err) { /* не везде есть */ }
        fn();
      }, ms || 450);
    }, { passive: true });
    el.addEventListener('touchmove', e => {
      if (!timer || !e.touches || !e.touches.length) return;
      const dx = Math.abs(e.touches[0].clientX - startX), dy = Math.abs(e.touches[0].clientY - startY);
      if (dx > 10 || dy > 10) cancel();      // это прокрутка, а не удержание
    }, { passive: true });
    el.addEventListener('touchend', e => {
      cancel();
      // после срабатывания удержания щелчок гасим, иначе выделение снимется
      if (fired && e.cancelable) e.preventDefault();
      fired = false;
    });
    el.addEventListener('touchcancel', cancel);
    return el;
  }

  function table(cfg) {
    const t = h('table.data' + (cfg.cards === false ? '' : '.cards') + (cfg.cardsClass ? '.' + cfg.cardsClass : ''));
    const thead = h('thead', h('tr', cfg.columns.map(c => h('th', { text: c.label }))));
    const tbody = h('tbody');
    t.append(thead, tbody);
    let selected = null;

    // Длинный список рисуется частями: первые CHUNK строк сразу, остальные —
    // когда прокрутка подходит к концу. На тысяче задач разметка перестала
    // строиться целиком, а поиск и сортировка по-прежнему идут по всем данным.
    const CHUNK = cfg.chunk || 60;
    let allRows = [], drawn = 0, io = null, tail = null;

    function makeRow(row) {
      const tr = h('tr.row' + (cfg.rowClass ? cfg.rowClass(row).split(' ').filter(Boolean).map(c => '.' + c).join('') : ''));
      tr.dataset.key = cfg.rowKey ? cfg.rowKey(row) : '';
      cfg.columns.forEach((c, i) => {
        // data-col — ключ столбца: по нему телефонная карточка раскладывает ячейки
        const td = h('td' + (c.align === 'left' ? '.left' : '') + (i === 0 ? '.name-td' : '') + (c.noLabel ? '.no-label' : ''), { dataset: { label: c.label.replace(/\n/g, ' '), col: c.key || '' } });
        const content = c.render ? c.render(row) : (row[c.key] ?? '');
        append(td, [content]);
        tr.append(td);
      });
      tr.addEventListener('click', () => { selectRow(tr, row); cfg.onSelect && cfg.onSelect(row); });
      tr.addEventListener('dblclick', () => cfg.onActivate && cfg.onActivate(row));
      if (cfg.hrefFor) tr.addEventListener('auxclick', e => {
        if (e.button !== 1) return;   // средняя кнопка мыши
        e.preventDefault();
        window.open(location.pathname + cfg.hrefFor(row), '_blank');
      });
      tr.addEventListener('keydown', e => { if (e.key === 'Enter') cfg.onActivate && cfg.onActivate(row); });
      // Долгое удержание пальцем — выделение строки. На телефоне список
      // показывается карточками, и обычное нажатие часто принимали за
      // прокрутку: теперь выделение делается осознанным жестом.
      longPress(tr, () => { selectRow(tr, row); cfg.onSelect && cfg.onSelect(row); });
      tr.tabIndex = 0;
      return tr;
    }

    // Обновление списка не должно моргать: строки, которые не изменились,
    // остаются теми же узлами, а новые появляются мягко. Раньше таблица
    // очищалась целиком, и при обновлении по сокету раздел вздрагивал.
    const byKey = new Map();          // ключ строки → { el, sig }
    const sigOf = row => { try { return JSON.stringify(row); } catch (e) { return String(Math.random()); } };

    function reconcile(list) {
      let prev = null;
      const seen = new Set();
      list.forEach(row => {
        const key = String(cfg.rowKey ? cfg.rowKey(row) : '');
        const sig = sigOf(row);
        seen.add(key);
        let rec = byKey.get(key);
        if (!rec || rec.sig !== sig) {
          const el = makeRow(row);
          if (rec) {
            if (rec.el.classList.contains('selected')) el.classList.add('selected');
            rec.el.replaceWith(el);
          } else el.classList.add('row-appear');
          rec = { el, sig };
          byKey.set(key, rec);
        }
        const target = prev ? prev.nextSibling : tbody.firstChild;
        if (rec.el !== target) tbody.insertBefore(rec.el, target);
        prev = rec.el;
      });
      byKey.forEach((rec, key) => {
        if (seen.has(key)) return;
        rec.el.remove();
        byKey.delete(key);
      });
    }

    function drawChunk() {
      const want = Math.min(drawn + CHUNK, allRows.length);
      if (cfg.rowKey) reconcile(allRows.slice(0, want));
      else {
        const frag = document.createDocumentFragment();
        allRows.slice(drawn, want).forEach(row => frag.append(makeRow(row)));
        tbody.append(frag);
      }
      drawn = want;
      watchTail();
    }

    /** Следим за последней строкой: попала в поле зрения — дорисовываем следующую часть */
    function watchTail() {
      if (io) { io.disconnect(); io = null; }
      if (tail) { tail.remove(); tail = null; }
      if (drawn >= allRows.length) return;
      const last = tbody.lastElementChild;
      if (!last) return;
      if (!window.IntersectionObserver) { while (drawn < allRows.length) { const s = allRows.slice(drawn, drawn + CHUNK); s.forEach(r => tbody.append(makeRow(r))); drawn += s.length; } return; }
      io = new IntersectionObserver(entries => { if (entries.some(e => e.isIntersecting)) drawChunk(); },
        { rootMargin: '600px 0px' });
      io.observe(last);
    }

    /** fill(rows, { keepDrawn }) — keepDrawn сохраняет уже показанные строки
     *  при обновлении списка, чтобы прокрутка не прыгала к началу */
    function fill(rows, opts) {
      if (io) { io.disconnect(); io = null; }
      // Переиспользование строк включено всегда, когда у строк есть ключ:
      // полная пересборка стирала и рисовала заново даже то, что не
      // изменилось, и раздел моргал при каждом обновлении. Отключить можно
      // явно — fill(rows, { keepDrawn: false }).
      const keep = (!opts || opts.keepDrawn !== false) && !!cfg.rowKey;
      const shown = Math.max(CHUNK, drawn);
      if (!cfg.rowKey || !keep) { clear(tbody); byKey.clear(); }
      allRows = rows || [];
      drawn = 0;
      if (keep) {
        drawn = Math.min(shown, allRows.length);
        reconcile(allRows.slice(0, drawn));
        watchTail();
        // Выбранная строка могла исчезнуть из набора — тогда выбор снимаем
        if (selected && !allRows.some(r => String(cfg.rowKey(r)) === String(cfg.rowKey(selected)))) {
          selected = null;
        }
      } else {
        drawChunk();
        selected = null;
      }
    }
    /** Дорисовать всё сразу — нужно печати и поиску по странице */
    function drawAll() { while (drawn < allRows.length) drawChunk(); }
    function selectRow(tr, row) {
      tbody.querySelectorAll('tr.selected').forEach(x => x.classList.remove('selected'));
      tr.classList.add('selected'); selected = row;
    }
    t.fill = fill; t.getSelected = () => selected; t.tbody = tbody;
    t.drawAll = drawAll; t.stats = () => ({ drawn, total: allRows.length });
    window.addEventListener('beforeprint', drawAll);
    // Сенсорный экран: одиночное нажатие открывает. Раньше условие смотрело на
    // ширину до 760px — на планшете в альбомной ориентации не работало ни
    // одиночное нажатие, ни двойной клик.
    if (cfg.onActivate) tbody.addEventListener('click', e => {
      if (window.matchMedia('(pointer: coarse)').matches || window.matchMedia('(max-width: 760px)').matches) {
        const tr = e.target.closest('tr'); if (!tr) return;
        const row = (cfg.rowsRef ? cfg.rowsRef() : []).find(r => String(cfg.rowKey ? cfg.rowKey(r) : '') === tr.dataset.key);
        if (row) cfg.onActivate(row);
      }
    });
    return t;
  }

  function stageDots(stages, total, current) {
    const wrap = h('span.stage-dots');
    const byNumber = {}; (stages || []).forEach(s => { byNumber[s.stage_number] = s; });
    for (let i = 1; i <= (total || 0); i++) {
      const s = byNumber[i];
      wrap.append(h('i' + (s && s.is_completed ? '.done' : '') + (current === i ? '.current' : ''), { title: 'Этап ' + i + (s && s.department_name ? ': ' + s.department_name : '') }));
    }
    return wrap;
  }

  // ── диалоги ──────────────────────────────────────────────────────────────

  /** modal({title, glyph, body(el), buttons:[{text, kind, icon, onClick(close), primary}], wide, onClose}) → {el, close} */
  function modal(cfg) {
    // одно и то же окно дважды не открывается (случайное двойное касание)
    const same = cfg.title && [...document.querySelectorAll('.modal-root')].find(r => r.dataset.title === cfg.title && r.__modal);
    if (same) return same.__modal;
    const root = h('div.modal-root');
    root.dataset.title = cfg.title || '';
    const box = h('div.modal' + (cfg.wide ? '.wide' : ''), { role: 'dialog', 'aria-modal': 'true' });
    if (cfg.width) box.style.width = 'min(' + cfg.width + ', 100%)';
    const closeBtn = h('button.close-x', { type: 'button', text: '×', 'aria-label': 'Закрыть', onclick: () => close() });
    const head = h('div.modal-head', cfg.glyph ? icon(cfg.glyph) : null, h('h2', cfg.title || ''),
                   ...(cfg.headButtons || []), closeBtn);
    const body = h('div.modal-body');
    const foot = h('div.modal-foot');
    box.append(head, body, foot);
    root.append(box);
    function close(result) { root.remove(); document.removeEventListener('keydown', onKey); cfg.onClose && cfg.onClose(result); }
    function onKey(e) { if (e.key === 'Escape') close(); }
    document.addEventListener('keydown', onKey);
    root.addEventListener('click', e => { if (e.target === root && cfg.dismissible !== false) close(); });
    (cfg.buttons || []).forEach(b => {
      const btn = button(b.text, { kind: b.kind, icon: b.icon, onClick: () => b.onClick ? b.onClick(close, box) : close() });
      if (b.primary) btn.classList.add('primary-btn');
      foot.append(btn);
    });
    if (!cfg.buttons || !cfg.buttons.length) foot.remove();
    if (cfg.body) { const r = cfg.body(body, close); if (r) append(body, [r]); }
    document.body.append(root); raise(root);   // последним открытое — поверх остальных
    const first = box.querySelector('input, select, textarea, button.btn');
    if (first) softFocus(first);
    root.__modal = { el: box, close, body, foot };
    return root.__modal;
  }

  function confirm(title, text, opts) {
    opts = opts || {};
    return new Promise(resolve => modal({
      title, glyph: opts.glyph || 'question', dismissible: false,
      body: el => el.append(h('p', { text })),
      buttons: [
        // resolve — до close(): close() дёргает onClose, который отвечает «нет»
        { text: opts.cancelText || 'Отмена', kind: 'ghost', onClick: close => { resolve(false); close(); } },
        { text: opts.okText || 'Да', kind: opts.danger ? 'danger' : '', icon: opts.icon || (opts.danger ? 'trash' : 'check'), onClick: close => { resolve(true); close(); } },
      ],
      onClose: () => resolve(false),
    }));
  }

  function alert(title, text, kind) {
    return new Promise(resolve => modal({
      title, glyph: kind || 'info', body: el => el.append(h('p', { text })),
      buttons: [{ text: 'Понятно', icon: 'check', onClick: close => { close(); resolve(); } }], onClose: () => resolve(),
    }));
  }

  function toast(text, kind) {
    const map = { success: 'success', warning: 'warning', error: 'warning', info: 'notification' };
    if (Kov.NotifyUI) Kov.NotifyUI.show({ title: text, kind: map[kind] || 'notification', ttl: 3200 });
  }

  function loading(text) { return h('div.loading', h('span.spinner'), h('span', text || 'Загрузка…')); }

  /** Крупная заставка по центру свободного места: три бегущие точки и надпись */
  function bigLoading(text) {
    return h('div.big-loading',
      h('div.dots', h('i'), h('i'), h('i')),
      h('div.t', text || 'Загрузка…'));
  }

  /**
   * Короткое человеческое сообщение об ошибке.
   *
   * Сервер и браузер иногда отдают простыню: список ошибок валидации,
   * трассировку, HTML страницы прокси. Пользователю это ничего не даёт,
   * поэтому оставляем одну фразу и код ответа — подробности уходят в консоль.
   */
  const HTTP_TEXT = {
    400: 'Запрос не принят',
    401: 'Нужно войти заново',
    403: 'Недостаточно прав',
    404: 'Не найдено',
    409: 'Данные уже изменились',
    413: 'Файл слишком большой',
    422: 'Данные заполнены неверно',
    429: 'Слишком много запросов, подождите',
    500: 'Ошибка на сервере',
    502: 'Сервер недоступен',
    503: 'Сервер занят, попробуйте позже',
    504: 'Сервер не ответил вовремя',
  };

  function errorText(e) {
    if (!e) return 'Неизвестная ошибка';
    const status = e.status || (e.response && e.response.status);
    let text = typeof e === 'string' ? e : (e.message || '');

    // Ответ FastAPI при ошибке проверки — массив объектов; берём первое поле
    if (e.detail && typeof e.detail !== 'string') {
      try {
        const first = Array.isArray(e.detail) ? e.detail[0] : e.detail;
        const field = first && first.loc ? String(first.loc[first.loc.length - 1]) : '';
        text = (first && first.msg ? first.msg : 'Данные заполнены неверно') + (field ? ' (поле «' + field + '»)' : '');
      } catch (err) { text = 'Данные заполнены неверно'; }
    }

    text = String(text || '').replace(/\s+/g, ' ').trim();
    if (/^<|<html/i.test(text)) text = '';                 // страница прокси вместо ответа
    // технический текст браузера или сети (латиницей: «Failed to fetch», «NetworkError», «Load failed»…) —
    // человеческими словами; подробности — только в консоль
    if (text && !/[а-яё]/i.test(text)) {
      console.warn('Ошибка (подробности):', text, e);
      text = /fetch|network|load failed|net::|err_|connection/i.test(text) ? 'нет связи с сервером — проверьте интернет'
        : /timeout|timed out/i.test(text) ? 'сервер не ответил вовремя'
        : /abort/i.test(text) ? 'действие отменено' : '';
    }
    if (text.length > 140) text = text.slice(0, 137) + '…'; // трассировку не показываем
    if (!text) text = HTTP_TEXT[status] || 'Не удалось выполнить запрос';
    if (status) {
      if (e && !e.__logged) { e.__logged = true; console.error('Ошибка запроса', status, e); }
      return text;                                         // код ответа — только в консоль
    }
    return text;
  }

  /**
   * Копирование в буфер. navigator.clipboard работает только в защищённом
   * контексте (HTTPS или localhost), а сервер пока отдаёт обычный HTTP —
   * поэтому есть запасной путь через скрытое поле и execCommand.
   */
  async function copyText(text) {
    const value = String(text == null ? '' : text);
    if (navigator.clipboard && window.isSecureContext) {
      try { await navigator.clipboard.writeText(value); toast('Скопировано', 'success'); return true; }
      catch (e) { /* дальше пробуем запасной путь */ }
    }
    if (legacyCopy(value)) { toast('Скопировано', 'success'); return true; }
    promptCopy(value);
    return false;
  }

  function legacyCopy(value) {
    const area = h('textarea', { value, readonly: true,
      style: { position: 'fixed', top: '-1000px', left: '0', opacity: '0', pointerEvents: 'none' } });
    document.body.append(area);
    let ok = false;
    try {
      area.focus(); area.select(); area.setSelectionRange(0, value.length);
      ok = document.execCommand('copy');
    } catch (e) { ok = false; }
    area.remove();
    return ok;
  }

  /** Совсем не вышло — показываем текст, чтобы скопировать вручную */
  function promptCopy(value) {
    const area = h('textarea.textarea', { value, readonly: true, style: { minHeight: '5rem' } });
    modal({
      title: 'Скопируйте вручную', glyph: 'copy',
      body: el => el.append(h('div.small.muted', 'Браузер запретил доступ к буферу обмена. Выделите текст и нажмите Ctrl+C.'), area),
      buttons: [{ text: 'Готово', icon: 'check' }],
    });
    setTimeout(() => { area.focus(); area.select(); }, 60);
  }

  function infoCard(glyph, caption, rows) {
    const card = h('div.info-card', h('div.caption', h('span.badge-icon', icon(glyph)), caption));
    (rows || []).forEach(([k, v, opts]) => {
      if (v === null || v === undefined || v === '') return;
      const val = h('span.v' + (opts && opts.copy ? '.copy' : ''), { text: v, title: opts && opts.copy ? 'Нажмите, чтобы скопировать' : null, onclick: opts && opts.copy ? () => copyText(String(v)) : null });
      card.append(h('div.info-row', h('span.k', k), val));
    });
    return card;
  }

  function field(label, control, required) {
    return h('div.field', h('label', label, required ? h('span.req', ' *') : null), control);
  }

  return {
    h, append, clear, syncList, icon, button, sectionHeader, searchInput, select, segmented, fitRow,
    avatar, avatarColor, initials, fullName, parseDate, fmtDate, fmtDateTime, isoDate, deadlineState, longPress,
    table, stageDots, modal, confirm, alert, toast, loading, bigLoading, errorText, copyText, infoCard, field, pickerSelect,
    bottomSheet, touchUi, softFocus, writer, raise,
  };
})();
