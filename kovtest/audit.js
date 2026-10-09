(opts) => {
  const out = { errors: [], warnings: [], stats: { interactive: 0, checked: 0, images: 0, videos: 0 } };
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const ignoreSel = (opts.ignore || []).join(',');
  const containerSel = (opts.containers || []).join(',');
  const allowOverflowSel = (opts.allowOverflow || []).join(',');

  const ignored = el => !!(ignoreSel && el.closest(ignoreSel));

  const visible = el => {
    if (!(el instanceof Element)) return false;
    if (el.closest('[hidden]')) return false;
    const st = getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width >= 1 && r.height >= 1;
  };

  const label = el => {
    const text = (el.getAttribute('aria-label') || el.getAttribute('title') || el.textContent || el.value || '').replace(/\s+/g, ' ').trim();
    const cls = typeof el.className === 'string' ? el.className.trim().split(/\s+/).slice(0, 3).join('.') : '';
    return el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (cls ? '.' + cls : '') + (text ? ' «' + text.slice(0, 40) + '»' : '');
  };

  const clipRect = el => {
    let rect = { left: 0, top: 0, right: vw, bottom: vh };
    let node = el.parentElement;
    while (node && node !== document.documentElement) {
      const st = getComputedStyle(node);
      if (/(hidden|auto|scroll|clip)/.test(st.overflowX + st.overflowY)) {
        const r = node.getBoundingClientRect();
        rect = { left: Math.max(rect.left, r.left), top: Math.max(rect.top, r.top),
                 right: Math.min(rect.right, r.right), bottom: Math.min(rect.bottom, r.bottom) };
      }
      if (st.position === 'fixed') break;
      node = node.parentElement;
    }
    return rect;
  };

  const inHorizontalScroller = el => {
    let node = el.parentElement;
    while (node && node !== document.body) {
      const st = getComputedStyle(node);
      if ((st.overflowX === 'auto' || st.overflowX === 'scroll') && node.scrollWidth > node.clientWidth + 1) return true;
      node = node.parentElement;
    }
    return false;
  };

  const area = r => Math.max(0, r.right - r.left) * Math.max(0, r.bottom - r.top);
  const intersect = (a, b) => ({ left: Math.max(a.left, b.left), top: Math.max(a.top, b.top),
                                 right: Math.min(a.right, b.right), bottom: Math.min(a.bottom, b.bottom) });

  if (document.documentElement.scrollWidth > vw + 1 && !opts.allowPageScrollX) {
    out.errors.push('Страница шире экрана: ' + document.documentElement.scrollWidth + ' px при ширине ' + vw + ' px');
  }

  const overlays = [...document.querySelectorAll((opts.overlays || []).join(',') || 'none')].filter(visible);
  const scope = overlays.length ? overlays[overlays.length - 1] : document;
  if (overlays.length) out.stats.scope = label(scope);
  const interactive = [...scope.querySelectorAll(opts.interactive)]
    .filter(el => visible(el) && !ignored(el));
  out.stats.interactive = interactive.length;
  const checked = [];
  const deferred = [];
  const pinned = node => {
    while (node && node !== document.body) {
      const pos = getComputedStyle(node).position;
      if (pos === 'fixed' || pos === 'sticky') return node;
      node = node.parentElement;
    }
    return null;
  };

  for (const el of interactive) {
    const r = el.getBoundingClientRect();
    const clip = clipRect(el);
    const seen = intersect(r, clip);
    const full = area(r);
    if (full <= 0) continue;
    const share = area(seen) / full;
    if (share <= 0) continue;
    const scroller = inHorizontalScroller(el);
    if (!scroller && (r.left < -1 || r.right > vw + 1) && share < 0.99) {
      out.errors.push('Элемент за краем экрана: ' + label(el));
      continue;
    }
    if (share < 0.98) continue;
    checked.push({ el, r });
    const points = [[(r.left + r.right) / 2, (r.top + r.bottom) / 2]];
    for (const [x, y] of points) {
      const hit = document.elementFromPoint(x, y);
      if (!hit) continue;
      if (hit === el || el.contains(hit)) continue;
      if (hit.closest && hit.closest('label') && hit.closest('label').contains(el)) continue;
      if (el.tagName === 'INPUT' && hit.tagName === 'LABEL' && hit.contains(el)) continue;
      if (ignored(hit)) continue;
      if (pinned(hit) !== pinned(el)) { deferred.push({ el, hit }); continue; }
      out.errors.push('Элемент перекрыт: ' + label(el) + ' — сверху ' + label(hit));
    }
    const small = Math.min(r.width, r.height);
    const isCheck = el.tagName === 'INPUT' && /^(checkbox|radio)$/.test(el.type);
    const isRange = el.tagName === 'INPUT' && el.type === 'range';
    if (isRange) {
      if (opts.mobile && r.height < opts.touchWarn) out.warnings.push('Ползунок невысокий для пальца (' + Math.round(r.height) + ' px): ' + label(el));
    } else if (!isCheck) {
      if (small < opts.touchMin) out.errors.push('Слишком мелкий для нажатия (' + Math.round(r.width) + '×' + Math.round(r.height) + ' px): ' + label(el));
      else if (opts.mobile && small < opts.touchWarn) out.warnings.push('Мелковат для пальца (' + Math.round(r.width) + '×' + Math.round(r.height) + ' px): ' + label(el));
    }
  }
  out.stats.checked = checked.length;

  if (deferred.length) {
    const scrollers = [];
    const remember = node => {
      while (node) {
        if (node.scrollHeight > node.clientHeight + 1 || node.scrollWidth > node.clientWidth + 1) scrollers.push([node, node.scrollTop, node.scrollLeft]);
        node = node.parentElement;
      }
    };
    deferred.forEach(d => remember(d.el));
    scrollers.push([document.scrollingElement, document.scrollingElement.scrollTop, document.scrollingElement.scrollLeft]);
    for (const d of deferred) {
      d.el.scrollIntoView({ block: 'center', inline: 'nearest' });
      const r = d.el.getBoundingClientRect();
      const hit = document.elementFromPoint((r.left + r.right) / 2, (r.top + r.bottom) / 2);
      if (hit && hit !== d.el && !d.el.contains(hit) && !ignored(hit)) {
        out.errors.push('Элемент закрыт закреплённой панелью даже после прокрутки: ' + label(d.el) + ' — сверху ' + label(hit));
      }
    }
    for (const [node, top, left] of scrollers) { node.scrollTop = top; node.scrollLeft = left; }
  }

  for (let i = 0; i < checked.length; i += 1) {
    for (let j = i + 1; j < checked.length; j += 1) {
      const a = checked[i], b = checked[j];
      if (a.el.contains(b.el) || b.el.contains(a.el)) continue;
      const inside = (x, y) => x.left >= y.left - 1 && x.right <= y.right + 1 && x.top >= y.top - 1 && x.bottom <= y.bottom + 1;
      if (inside(a.r, b.r) || inside(b.r, a.r)) continue;
      if (pinned(a.el) !== pinned(b.el)) continue;
      const cross = area(intersect(a.r, b.r));
      if (cross > 4 && cross > 0.05 * Math.min(area(a.r), area(b.r))) {
        out.errors.push('Элементы наезжают друг на друга: ' + label(a.el) + ' и ' + label(b.el));
      }
    }
  }

  if (containerSel) {
    for (const box of scope.querySelectorAll(containerSel)) {
      if (!visible(box) || ignored(box)) continue;
      const st = getComputedStyle(box);
      if (/(hidden|auto|scroll|clip)/.test(st.overflowX + st.overflowY)) continue;
      const br = box.getBoundingClientRect();
      for (const child of box.querySelectorAll('*')) {
        if (!visible(child) || ignored(child)) continue;
        if (allowOverflowSel && child.closest(allowOverflowSel)) continue;
        const cst = getComputedStyle(child);
        if (cst.position === 'fixed') continue;
        if (child.children.length && !child.matches(opts.interactive)) continue;
        const cr = child.getBoundingClientRect();
        const clip = clipRect(child);
        if (area(intersect(cr, clip)) < area(cr) * 0.98) continue;
        const outX = Math.max(0, br.left - cr.left) + Math.max(0, cr.right - br.right);
        const outY = Math.max(0, br.top - cr.top) + Math.max(0, cr.bottom - br.bottom);
        if (outX > 2 || outY > 2) {
          out.errors.push('Выходит за границы блока ' + label(box) + ': ' + label(child) + ' (на ' + Math.round(Math.max(outX, outY)) + ' px)');
        }
      }
    }
  }

  for (const el of document.querySelectorAll(opts.textClip)) {
    if (!visible(el) || ignored(el)) continue;
    const st = getComputedStyle(el);
    if (st.overflowX !== 'hidden' || st.textOverflow === 'ellipsis' || st.whiteSpace !== 'nowrap') continue;
    if (el.scrollWidth > el.clientWidth + 2) out.warnings.push('Текст обрезан без многоточия: ' + label(el));
  }

  for (const img of document.querySelectorAll('img')) {
    if (!visible(img) || ignored(img)) continue;
    out.stats.images += 1;
    const ir = img.getBoundingClientRect();
    const offscreen = ir.bottom < 0 || ir.top > innerHeight || ir.right < 0 || ir.left > innerWidth;
    if (!img.complete && img.loading === 'lazy' && offscreen) continue;
    if (!img.complete) out.warnings.push('Картинка ещё грузится: ' + (img.currentSrc || img.src || '').replace(/token=[^&]+/, 'token=…').slice(0, 120));
    else if (!img.naturalWidth) out.errors.push('Картинка не отрисовалась: ' + (img.currentSrc || img.src || '').replace(/token=[^&]+/, 'token=…').slice(0, 120));
  }
  for (const video of document.querySelectorAll('video')) {
    if (!visible(video) || ignored(video)) continue;
    out.stats.videos += 1;
    if (video.error) out.errors.push('Видео не воспроизводится (код ' + video.error.code + '): ' + (video.currentSrc || '').replace(/token=[^&]+/, 'token=…').slice(0, 120));
  }
  return out;
}
