/* Окно отправки файлов в чат — как в Telegram.
 *
 * Открывается при выборе файлов: список с превью, подпись, кнопка «Добавить»
 * и приём перетаскиванием прямо в окно. Текст из строки ввода переезжает
 * сюда, а при отмене возвращается обратно.
 *
 * Фото правится на устройстве, без сервера: отражение, поворот, обрезка,
 * рисование, ластик, размытие и отмена. Итог — новый файл, который и уходит
 * на отправку; исходник на диске не трогается, а «Сбросить» возвращает его.
 *
 * Видео браузер перекодировать не может, поэтому для него выбираются точки
 * обрезки по времени и рамка кадра, а применяет их сервер в том же проходе
 * ffmpeg, что и сжатие, — лишней нагрузки это не добавляет.
 */
window.Kov = window.Kov || {};

Kov.SendDialog = (function () {
  const { h, icon, button } = Kov.UI;
  const IMAGE_RE = /\.(jpe?g|png|gif|webp|bmp|heic|heif)$/i;
  const VIDEO_RE = /\.(mp4|mov|avi|mkv|webm|m4v|3gp)$/i;
  const isImage = n => IMAGE_RE.test(n || '');
  const isVideo = n => VIDEO_RE.test(n || '');
  const isMobile = () => window.matchMedia('(max-width: 760px)').matches;

  // Итог редактора: «Отмена» — оставить как было, null — правок нет
  const CANCELLED = { cancelled: true };

  function fmtSize(b) {
    if (b < 1024) return b + ' Б';
    if (b < 1048576) return (b / 1024).toFixed(0) + ' КБ';
    return (b / 1048576).toFixed(1) + ' МБ';
  }
  function fmtTime(sec) {
    sec = Math.max(0, Math.round(sec));
    const m = Math.floor(sec / 60), s = sec % 60;
    return m + ':' + String(s).padStart(2, '0');
  }
  /** Тянут файлы, а не выделенный текст или ссылку */
  function dragHasFiles(e) {
    const dt = e.dataTransfer;
    if (!dt) return false;
    if (dt.types && [...dt.types].includes('Files')) return true;
    return !!(dt.items && [...dt.items].some(i => i.kind === 'file'));
  }

  /**
   * open({ files, text, max, onSend(files, caption, edits), onCancel(text) })
   * files — File[]; edits — { index: { trim_start, trim_end, crop } } для видео.
   */
  function open(cfg) {
    const items = [];       // { file, url, kind, edit, original }
    const max = cfg.max || 10;
    const caption = h('textarea.textarea.send-caption', { placeholder: 'Подпись (необязательно)', value: cfg.text || '' });
    const list = h('div.send-list');
    const counter = h('span.small.muted');
    const addInput = h('input', { type: 'file', multiple: true, hidden: true });
    addInput.addEventListener('change', () => { addFiles(addInput.files); addInput.value = ''; });

    function addFiles(fileList) {
      const room = max - items.length;
      const incoming = [...fileList];
      if (!room) { Kov.UI.toast('Не больше ' + max + ' файлов за раз', 'warning'); return; }
      if (incoming.length > room) Kov.UI.toast('Добавлено ' + room + ' из ' + incoming.length + ' — не больше ' + max + ' за раз', 'warning');
      incoming.slice(0, room).forEach(file => {
        const kind = isImage(file.name) ? 'image' : isVideo(file.name) ? 'video' : 'file';
        const url = kind === 'file' ? null : URL.createObjectURL(file);
        items.push({ file, url, kind, edit: null, original: null });
      });
      draw();
    }
    function release(it) { if (it.url) { try { URL.revokeObjectURL(it.url); } catch (e) { /* */ } it.url = null; } }
    function reopen(it) { release(it); it.url = URL.createObjectURL(it.file); }

    function thumbFor(it) {
      const box = h('div.send-thumb.' + it.kind);
      if (it.kind === 'image') box.append(h('img', { src: it.url, alt: '' }));
      else if (it.kind === 'video') {
        const v = h('video', { src: it.url, muted: true, preload: 'metadata', playsInline: true });
        v.addEventListener('loadedmetadata', () => { try { v.currentTime = Math.min(0.2, v.duration || 0); } catch (e) { /* */ } });
        box.append(v, h('span.play', icon('play', { fill: true, stroke: 1 })));
      } else box.append(icon('file'));
      if (it.edit) box.append(h('span.edited', 'изменено'));
      return box;
    }

    function draw() {
      Kov.UI.clear(list);
      items.forEach((it, i) => {
        const row = h('div.send-row',
          thumbFor(it),
          h('div.who', h('b', it.file.name), h('span', fmtSize(it.file.size) + (it.kind === 'video' && it.edit ? ' · обрезано' : ''))),
          it.kind !== 'file' ? button('', { kind: 'ghost', icon: 'edit', title: it.kind === 'image' ? 'Редактировать фото' : 'Обрезать видео',
            onClick: () => edit(i) }) : null,
          button('', { kind: 'ghost', icon: 'close', title: 'Убрать', onClick: () => { release(it); items.splice(i, 1); draw(); } }));
        if (it.kind !== 'file') row.querySelector('.send-thumb').addEventListener('click', () => edit(i));
        list.append(row);
      });
      counter.textContent = items.length + ' из ' + max;
      if (!items.length) list.append(h('div.empty', 'Файлы не выбраны'));
    }

    async function edit(i) {
      const it = items[i];
      if (it.kind === 'image') {
        const result = await editImage(it.file, it.url);
        if (result === CANCELLED) return;
        if (!result) {                       // «Сбросить» или правок не было — возвращаем исходник
          if (it.original) { it.file = it.original; it.original = null; reopen(it); }
          it.edit = null; draw(); return;
        }
        if (!it.original) it.original = it.file;
        it.file = result; reopen(it); it.edit = { photo: true }; draw();
      } else if (it.kind === 'video') {
        const result = await editVideo(it.file, it.url, it.edit);
        if (result === CANCELLED) return;
        it.edit = result || null; draw();
      }
    }

    const dlg = Kov.UI.modal({
      title: 'Отправка файлов', glyph: 'upload', wide: true,
      body: el => {
        el.classList.add('send-dialog');
        el.append(h('div.send-head', counter, h('span.spacer'),
          button('Добавить', { kind: 'ghost', icon: 'plus', onClick: () => addInput.click() }), addInput),
          list, Kov.UI.field('Подпись', caption));
        draw();
      },
      buttons: [
        { text: 'Отмена', kind: 'ghost', onClick: close => { close(); cleanup(); cfg.onCancel && cfg.onCancel(caption.value); } },
        { text: 'Отправить', icon: 'send', onClick: close => {
          if (!items.length) return Kov.UI.toast('Добавьте хотя бы один файл', 'warning');
          const edits = {};
          items.forEach((it, i) => { if (it.kind === 'video' && it.edit) edits[i] = it.edit; });
          const files = items.map(it => it.file);
          close(); cleanup(false);
          cfg.onSend && cfg.onSend(files, caption.value.trim(), edits);
        } },
      ],
    });
    function cleanup(revoke) { if (revoke !== false) items.forEach(release); }

    // Перетаскивание при открытом окне: подсветка только на самом окне, файлы
    // добавляются к отправке. stopPropagation глушит подсветку чата под окном.
    const overlay = dlg.el.parentElement;
    let depth = 0;
    const mark = on => dlg.el.classList.toggle('drag-on', on);
    overlay.addEventListener('dragenter', e => { if (!dragHasFiles(e)) return; e.preventDefault(); e.stopPropagation(); depth++; mark(true); });
    overlay.addEventListener('dragover', e => {
      if (!dragHasFiles(e)) return;
      e.preventDefault(); e.stopPropagation();
      try { e.dataTransfer.dropEffect = 'copy'; } catch (err) { /* */ }
    });
    overlay.addEventListener('dragleave', e => { if (!dragHasFiles(e)) return; e.stopPropagation(); if (--depth <= 0) { depth = 0; mark(false); } });
    overlay.addEventListener('drop', e => {
      if (!dragHasFiles(e)) return;
      e.preventDefault(); e.stopPropagation(); depth = 0; mark(false);
      if (e.dataTransfer.files && e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
    });

    addFiles(cfg.files || []);
    return dlg;
  }

  // ── редактор фото ─────────────────────────────────────────────────────────

  /**
   * Редактор на <canvas>: два слоя одного размера — clean (сам снимок после
   * поворотов и обрезки) и base (он же с рисунком поверх). Ластик стирает
   * рисунок и возвращает пиксели clean, размытие берёт картинку из base.
   * Итог собирается в JPEG (или PNG, если исходник был PNG) в браузере.
   */
  /** Телефон: редактор во весь экран — «Отмена · название · Готово» сверху, снимок крупно, «Сбросить» внизу */
  function fullscreenEditor(box) {
    if (!box || !window.matchMedia('(max-width: 760px)').matches) return;
    box.classList.add('ed-full');
    const head = box.querySelector('.modal-head'), foot = box.querySelector('.modal-foot');
    if (!head || !foot) return;
    const btns = [...foot.querySelectorAll('button')];
    const find = t => btns.find(b => b.textContent.trim().endsWith(t));
    const cancel = find('Отмена'), ok = find('Готово'), x = head.querySelector('.close-x');
    const h2 = head.querySelector('h2');
    if (h2) h2.textContent = /видео/i.test(h2.textContent) ? 'Видео' : 'Фото';
    if (cancel) head.prepend(cancel);
    if (ok) head.append(ok);
    if (x && cancel) x.remove();   // крестик повторял бы «Отмену»
    requestAnimationFrame(() => window.dispatchEvent(new Event('resize')));
  }

  function editImage(file, url) {
    return new Promise(resolve => {
      const img = new Image();
      img.onload = () => resolve(runEditor(file, img));
      img.onerror = () => { Kov.UI.toast('Не удалось открыть изображение', 'warning'); resolve(CANCELLED); };
      img.src = url;
    });
  }

  function runEditor(file, img) {
    return new Promise(resolve => {
      const MAX_SIDE = 2560;      // больше не нужно и тяжело для устройства
      const canvas = (w, hh) => { const c = document.createElement('canvas'); c.width = w; c.height = hh; return c; };
      const clone = c => { const n = canvas(c.width, c.height); n.getContext('2d').drawImage(c, 0, 0); return n; };

      const scale = Math.min(1, MAX_SIDE / Math.max(img.width, img.height));
      let base = canvas(Math.round(img.width * scale), Math.round(img.height * scale));
      base.getContext('2d').drawImage(img, 0, 0, base.width, base.height);
      let clean = clone(base);          // снимок без рисунка — для ластика
      const origin = clone(base);       // исходное состояние — для «Сбросить»

      // history: {base, clean|null}. Для штрихов нижний слой не меняется —
      // храним только верхний, иначе память улетает на больших снимках
      const history = [];
      const pushHistory = paintOnly => {
        history.push({ base: clone(base), clean: paintOnly ? null : clone(clean) });
        if (history.length > 14) history.shift();
      };

      const view = h('canvas.ed-view');
      const frame = cropFrame(() => view.getBoundingClientRect());   // рамка обрезки с ручками
      const brushDot = h('span.ed-brush-dot', { hidden: true });
      const stage = h('div.ed-stage', view, frame.el, brushDot);
      let tool = 'none';                 // none | draw | erase | blur | crop
      let color = '#F5B301', size = 6;
      let drawing = false, last = null, strokeStarted = false;

      function redraw() { view.getContext('2d').drawImage(base, 0, 0, view.width, view.height); }
      function fit() {
        const box = stage.getBoundingClientRect();
        const pad = 2 * parseFloat(getComputedStyle(stage).paddingLeft || 0) + 8;
        const s = Math.min((box.width - pad) / base.width, (box.height - pad) / base.height, 1);
        const w = Math.max(1, Math.round(base.width * s)), hh = Math.max(1, Math.round(base.height * s));
        // чёткость на телефоне: холст — в физических пикселях экрана, на странице — в CSS-пикселях
        const dpr = Math.min(3, window.devicePixelRatio || 1);
        view.width = Math.round(w * dpr); view.height = Math.round(hh * dpr); view.style.width = w + 'px'; view.style.height = hh + 'px';
        redraw();
        frame.layout(tool === 'crop');
      }
      function toBase(x, y) { const r = view.getBoundingClientRect(); return { x: (x - r.left) * base.width / r.width, y: (y - r.top) * base.height / r.height }; }
      const scaleK = () => base.width / Math.max(1, view.getBoundingClientRect().width || view.width);   // пиксели снимка на CSS-пиксель

      function transform(fn) { pushHistory(); base = fn(base); clean = fn(clean); frame.reset(); fit(); }
      /** Холст после разворота: система координат обязательно возвращается на
       *  место, иначе кисть продолжает рисовать зеркально или боком —
       *  getContext отдаёт тот же контекст с оставшимся преобразованием */
      const turned = (c, w, hh, setup) => {
        const n = canvas(w, hh); const x = n.getContext('2d');
        x.save(); setup(x, n); x.drawImage(c, ...(n.width === c.width && n.height === c.height ? [0, 0] : [-c.width / 2, -c.height / 2]));
        x.restore(); x.setTransform(1, 0, 0, 1, 0, 0);
        return n;
      };
      const mirrorH = () => transform(c => turned(c, c.width, c.height, (x, n) => { x.translate(n.width, 0); x.scale(-1, 1); }));
      const mirrorV = () => transform(c => turned(c, c.width, c.height, (x, n) => { x.translate(0, n.height); x.scale(1, -1); }));
      const rotate = () => transform(c => turned(c, c.height, c.width, (x, n) => { x.translate(n.width / 2, n.height / 2); x.rotate(Math.PI / 2); }));
      function undo() {
        const prev = history.pop(); if (!prev) return;
        base = prev.base; if (prev.clean) clean = prev.clean;
        frame.reset(); fit();
      }
      function applyCrop() {
        const r = frame.rect();                     // доли кадра
        if (!r) return;
        transform(c => {
          const n = canvas(Math.max(1, Math.round(c.width * r.w)), Math.max(1, Math.round(c.height * r.h)));
          n.getContext('2d').drawImage(c, Math.round(c.width * r.x), Math.round(c.height * r.y),
            n.width, n.height, 0, 0, n.width, n.height);
          return n;
        });
      }
      /** Что-то менялось: по истории правок и по выставленной рамке обрезки */
      const changed = () => history.length > 0 || !!frame.rect();
      function resetAll() {
        base = clone(origin); clean = clone(origin);
        history.length = 0; frame.reset(); setTool('none'); fit();
      }

      // ── кисть, ластик, размытие ─────────────────────────────────────────
      let blurSrc = null, blurMask = null, blurTmp = null, blurQueued = false;
      function beginBlur() {
        const k = Math.min(1, 1400 / Math.max(base.width, base.height));   // размытие считаем на уменьшенной копии
        const small = canvas(Math.max(1, Math.round(base.width * k)), Math.max(1, Math.round(base.height * k)));
        const sc = small.getContext('2d');
        sc.filter = 'blur(' + Math.max(3, size * scaleK() * k * 1.15).toFixed(1) + 'px)';
        sc.drawImage(base, 0, 0, small.width, small.height);
        sc.filter = 'none';
        blurSrc = small;
        blurMask = canvas(base.width, base.height);
        blurTmp = canvas(base.width, base.height);
      }
      function paintBlur() {
        if (!blurTmp) return;
        const t = blurTmp.getContext('2d');
        t.clearRect(0, 0, blurTmp.width, blurTmp.height);
        t.globalCompositeOperation = 'source-over';
        t.drawImage(blurSrc, 0, 0, blurTmp.width, blurTmp.height);
        t.globalCompositeOperation = 'destination-in';
        t.drawImage(blurMask, 0, 0);
        t.globalCompositeOperation = 'source-over';
        const b = base.getContext('2d');
        b.globalCompositeOperation = 'source-over';
        b.drawImage(blurTmp, 0, 0);
        redraw();
      }
      function queueBlur() {
        if (blurQueued) return;
        blurQueued = true;
        requestAnimationFrame(() => { blurQueued = false; paintBlur(); });
      }
      function endBlur() { blurSrc = blurMask = blurTmp = null; }

      function pointerDown(e) {
        if (tool !== 'draw' && tool !== 'erase' && tool !== 'blur') return;
        drawing = true; strokeStarted = false; last = toBase(e.clientX, e.clientY);
        // захват указателя нужен, чтобы линия не рвалась за краем снимка;
        // если браузер его не даёт, рисование всё равно должно работать
        try { view.setPointerCapture(e.pointerId); } catch (err) { /* */ }
        stroke({ x: last.x + 0.01, y: last.y });     // точка от одиночного касания
      }
      function stroke(p) {
        if (!strokeStarted) { pushHistory(true); strokeStarted = true; if (tool === 'blur') beginBlur(); }
        const k = scaleK();
        if (tool === 'blur') {
          const m = blurMask.getContext('2d');
          m.setTransform(1, 0, 0, 1, 0, 0);
          m.lineCap = 'round'; m.lineJoin = 'round';
          m.strokeStyle = '#fff'; m.lineWidth = Math.max(2, size * k * 1.6);
          m.beginPath(); m.moveTo(last.x, last.y); m.lineTo(p.x, p.y); m.stroke();
          last = p; queueBlur(); return;
        }
        const ctx = base.getContext('2d');
        ctx.save();
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.lineCap = 'round'; ctx.lineJoin = 'round';
        ctx.lineWidth = Math.max(1, size * k);   // на мелких снимках линия не должна быть тоньше пикселя
        if (tool === 'erase') {
          // Ластик снимает нарисованное и возвращает пиксели нижнего слоя:
          // destination-out делал дыру насквозь и ничего не «стирал»
          ctx.globalCompositeOperation = 'destination-out';
          ctx.strokeStyle = 'rgba(0,0,0,1)';
          ctx.lineWidth = Math.max(1.5, size * k * 1.6);
        } else ctx.strokeStyle = color;
        ctx.beginPath();
        ctx.moveTo(last.x, last.y); ctx.lineTo(p.x, p.y); ctx.stroke();
        ctx.restore();
        if (tool === 'erase') restoreClean();
        last = p;
        redraw();
      }
      /** Под прозрачными следами ластика снова показываем снимок без рисунка */
      function restoreClean() {
        const ctx = base.getContext('2d');
        ctx.save();
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.globalCompositeOperation = 'destination-over';
        ctx.drawImage(clean, 0, 0, base.width, base.height);
        ctx.restore();
      }
      function pointerMove(e) {
        const r = view.getBoundingClientRect();
        if (tool === 'draw' || tool === 'erase' || tool === 'blur') {
          const inside = e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom;
          brushDot.hidden = !inside;
          if (inside) {
            const d = Math.max(4, size * (tool === 'draw' ? 1 : 1.6));
            const box = stage.getBoundingClientRect();
            brushDot.style.width = brushDot.style.height = d + 'px';
            brushDot.style.left = (e.clientX - box.left - d / 2) + 'px';
            brushDot.style.top = (e.clientY - box.top - d / 2) + 'px';
            brushDot.style.borderColor = tool === 'erase' ? '#E53935' : tool === 'blur' ? '#37474F' : color;
            brushDot.classList.toggle('blur', tool === 'blur');
          }
        } else brushDot.hidden = true;
        if (!drawing) return;
        stroke(toBase(e.clientX, e.clientY));
      }
      function pointerUp() { if (drawing && tool === 'blur') { paintBlur(); endBlur(); } drawing = false; last = null; }
      view.addEventListener('pointerdown', pointerDown);
      stage.addEventListener('pointermove', pointerMove);
      stage.addEventListener('pointerleave', () => { brushDot.hidden = true; });
      view.addEventListener('pointerup', pointerUp);
      view.addEventListener('pointercancel', pointerUp);
      view.style.touchAction = 'none';

      // ── панель ──────────────────────────────────────────────────────────
      const colors = h('div.ed-colors');
      const swatches = [];
      ['#F5B301', '#E53935', '#43A047', '#1E88E5', '#132A32', '#FFFFFF'].forEach(c => {
        const b = h('button.ed-color' + (c === color ? '.on' : ''), { type: 'button', title: 'Цвет кисти', style: { background: c } });
        b.dataset.color = c;
        b.addEventListener('click', () => { color = c; setTool('draw'); });
        colors.append(b); swatches.push(b);
      });
      // Размытие стоит рядом с цветами: это такая же «краска», только скрывающая
      const blurSwatch = h('button.ed-color.blur', { type: 'button', title: 'Размытие — закрасить номера, лица, подписи' });
      blurSwatch.addEventListener('click', () => setTool(tool === 'blur' ? 'none' : 'blur'));
      colors.append(blurSwatch);

      const sizeInput = h('input.ed-size', { type: 'range', min: 2, max: 40, value: size, title: 'Толщина линии' });
      const sizePreview = h('span.ed-size-preview');
      const sizeName = h('span.ed-size-name');
      const sizeBox = h('div.ed-size-box', sizeName, sizeInput, sizePreview);
      function paintSize() {
        sizeName.textContent = tool === 'erase' ? 'Ластик' : tool === 'blur' ? 'Размытие' : 'Кисть';
        const d = Math.max(4, Number(sizeInput.value) * (tool === 'draw' ? 1 : 1.6));
        sizePreview.style.width = sizePreview.style.height = Math.min(28, d) + 'px';
        sizePreview.style.background = tool === 'erase' ? '#E53935' : tool === 'blur' ? '#90A4AE' : color;
        sizePreview.classList.toggle('blur', tool === 'blur');
        sizeInput.style.setProperty('--fill', ((sizeInput.value - 2) / 38 * 100) + '%');
      }
      sizeInput.addEventListener('input', () => { size = Number(sizeInput.value); paintSize(); });

      const tools = h('div.ed-tools');
      const iconBtn = (glyph, title, fn, key) => {
        const b = button('', { kind: 'ghost', icon: glyph, title, onClick: fn });
        if (key) b.dataset.tool = key;
        return b;
      };
      function setTool(t) {
        if (t !== 'crop' && tool === 'crop') frame.reset();   // ушли с обрезки — рамка не «выстрелит» при сохранении
        // тонкой кистью номер не закрасишь — под размытие берём заметную толщину
        if (t === 'blur' && tool !== 'blur' && size < 16) { size = 20; sizeInput.value = size; }
        tool = t;
        tools.querySelectorAll('[data-tool]').forEach(b => b.classList.toggle('on', b.dataset.tool === t));
        swatches.forEach(b => b.classList.toggle('on', t === 'draw' && b.dataset.color === color));
        blurSwatch.classList.toggle('on', t === 'blur');
        colors.hidden = !(t === 'draw' || t === 'blur');
        sizeBox.hidden = !(t === 'draw' || t === 'erase' || t === 'blur');
        brushDot.hidden = true;
        view.style.cursor = (t === 'draw' || t === 'erase' || t === 'blur') ? 'none' : 'default';
        frame.layout(t === 'crop');
        paintSize();
      }
      tools.append(
        iconBtn('brush', 'Рисовать', () => setTool(tool === 'draw' ? 'none' : 'draw'), 'draw'),
        iconBtn('eraser', 'Ластик — убрать нарисованное', () => setTool(tool === 'erase' ? 'none' : 'erase'), 'erase'),
        iconBtn('crop', 'Обрезать', () => setTool(tool === 'crop' ? 'none' : 'crop'), 'crop'),
        iconBtn('flip', 'Отразить слева направо', mirrorH),
        iconBtn('flip_v', 'Перевернуть сверху вниз', mirrorV),
        iconBtn('rotate', 'Повернуть на 90°', rotate),
        iconBtn('undo', 'Отменить последнее действие', undo));

      const dlg = Kov.UI.modal({
        title: 'Редактирование фото', glyph: 'image', wide: true,
        body: el => {
          el.classList.add('ed-body');
          // панель под снимком, как просили
          el.append(stage, h('div.ed-panel', tools, colors, sizeBox));
          colors.hidden = true; sizeBox.hidden = true;
        },
        buttons: [
          { text: 'Сбросить', kind: 'ghost', icon: 'refresh', onClick: close => { resetAll(); close(); resolve(null); } },
          { text: 'Отмена', kind: 'ghost', onClick: close => { close(); resolve(CANCELLED); } },
          { text: 'Готово', icon: 'check', onClick: close => {
            if (!changed()) { close(); return resolve(null); }   // правок нет — отправится исходный файл
            applyCrop();                            // обрезка применяется при сохранении
            const png = /\.png$/i.test(file.name);
            const type = png ? 'image/png' : 'image/jpeg';
            base.toBlob(blob => {
              if (!blob) { Kov.UI.toast('Не удалось собрать изображение', 'warning'); return; }
              const name = file.name.replace(/\.[^.]+$/, '') + (png ? '.png' : '.jpg');
              close();
              resolve(new File([blob], name, { type }));
            }, type, 0.92);
          } },
        ],
      });
      fullscreenEditor(dlg.el);   // телефон: редактор во весь экран
      setTimeout(fit, 30);
      window.addEventListener('resize', fit);
      const origClose = dlg.close;
      dlg.close = () => { window.removeEventListener('resize', fit); origClose(); };
    });
  }

  /**
   * Рамка обрезки с восемью ручками: тянется за края и углы, переносится за
   * середину, затемняет всё вокруг. Возвращает область в долях кадра, так
   * что одна и та же рамка годится и фото, и видео.
   */
  function cropFrame(getBox, opts) {
    opts = opts || {};
    let r = { x: 0, y: 0, w: 1, h: 1 };
    let ratio = null;                 // null — свободно, иначе ширина/высота
    const shade = h('div.cf-shade');
    const box = h('div.cf-box');
    const el = h('div.cf', { hidden: true }, shade, box);
    ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'].forEach(k => box.append(h('span.cf-h.cf-' + k, { dataset: { k } })));
    box.append(h('span.cf-grid'));

    function layout(show) {
      el.hidden = !show;
      if (!show || !el.parentElement) return;
      const b = getBox();
      const host = el.parentElement.getBoundingClientRect();
      const left = b.left - host.left, top = b.top - host.top;
      Object.assign(el.style, { left: left + 'px', top: top + 'px', width: b.width + 'px', height: b.height + 'px' });
      Object.assign(box.style, { left: (r.x * b.width) + 'px', top: (r.y * b.height) + 'px',
        width: (r.w * b.width) + 'px', height: (r.h * b.height) + 'px' });
      shade.style.clipPath = 'polygon(0 0, 100% 0, 100% 100%, 0 100%, 0 0, '
        + (r.x * 100) + '% ' + (r.y * 100) + '%, ' + (r.x * 100) + '% ' + ((r.y + r.h) * 100) + '%, '
        + ((r.x + r.w) * 100) + '% ' + ((r.y + r.h) * 100) + '%, ' + ((r.x + r.w) * 100) + '% ' + (r.y * 100) + '%, '
        + (r.x * 100) + '% ' + (r.y * 100) + '%)';
    }

    let mode = null, startRect = null, startPoint = null;
    function begin(e, kind) {
      e.preventDefault(); e.stopPropagation();
      // палец легко съезжает с ручки — забираем указатель себе, иначе
      // браузер обрывает жест и рамка двигается рывками по чуть-чуть
      try { e.currentTarget.setPointerCapture(e.pointerId); } catch (err) { /* */ }
      mode = kind; startRect = Object.assign({}, r);
      const b = getBox();
      startPoint = { x: (e.clientX - b.left) / b.width, y: (e.clientY - b.top) / b.height };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', end);
      window.addEventListener('pointercancel', end);
    }
    function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
    function move(e) {
      const b = getBox();
      const p = { x: (e.clientX - b.left) / b.width, y: (e.clientY - b.top) / b.height };
      const dx = p.x - startPoint.x, dy = p.y - startPoint.y;
      const MIN = 0.08;
      let n = Object.assign({}, startRect);
      if (mode === 'move') {
        n.x = clamp(startRect.x + dx, 0, 1 - startRect.w);
        n.y = clamp(startRect.y + dy, 0, 1 - startRect.h);
      } else {
        if (mode.includes('w')) { const nx = clamp(startRect.x + dx, 0, startRect.x + startRect.w - MIN); n.w = startRect.w + (startRect.x - nx); n.x = nx; }
        if (mode.includes('e')) { n.w = clamp(startRect.w + dx, MIN, 1 - startRect.x); }
        if (mode.includes('n')) { const ny = clamp(startRect.y + dy, 0, startRect.y + startRect.h - MIN); n.h = startRect.h + (startRect.y - ny); n.y = ny; }
        if (mode.includes('s')) { n.h = clamp(startRect.h + dy, MIN, 1 - startRect.y); }
        if (ratio) {
          // держим заданное соотношение относительно самого кадра
          const b2 = getBox();
          const px = b2.width, py = b2.height;
          let wpx = n.w * px, hpx = wpx / ratio;
          if (hpx / py + n.y > 1) { hpx = (1 - n.y) * py; wpx = hpx * ratio; }
          n.w = wpx / px; n.h = hpx / py;
        }
      }
      r = n; layout(true);
    }
    function end() {
      mode = null;
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', end);
      window.removeEventListener('pointercancel', end);
    }
    box.addEventListener('pointerdown', e => { if (e.target === box || e.target.classList.contains('cf-grid')) begin(e, 'move'); });
    box.querySelectorAll('.cf-h').forEach(hnd => hnd.addEventListener('pointerdown', e => begin(e, hnd.dataset.k)));

    return {
      el, layout,
      rect: () => (r.w > 0.995 && r.h > 0.995 ? null : Object.assign({}, r)),
      reset: () => { r = { x: 0, y: 0, w: 1, h: 1 }; ratio = null; layout(!el.hidden); },
      setRatio: (value, frameRatio) => {
        ratio = value;
        if (!value) return;
        // вписываем прямоугольник нужного соотношения в кадр
        let w = 1, hh = (1 / value) * frameRatio;
        if (hh > 1) { hh = 1; w = value / frameRatio; }
        r = { x: (1 - w) / 2, y: (1 - hh) / 2, w, h: hh };
        layout(true);
      },
    };
  }

  // ── обрезка видео ─────────────────────────────────────────────────────────

  /**
   * Обрезка видео: лента кадров с двумя ручками (как в Telegram), рамка
   * кадра с ручками и поворот. Кадры для ленты рисуются на устройстве из
   * самого файла — сервер не участвует. Сами правки применяет сервер тем же
   * проходом ffmpeg, что и сжатие.
   */
  function editVideo(file, url, current) {
    return new Promise(resolve => {
      const video = h('video.vt-video', { src: url, preload: 'metadata', playsInline: true });
      video.addEventListener('error', () => {
        if (stage.querySelector('.vt-error')) return;
        stage.append(h('div.vt-error', 'Видео не открылось в редакторе — его можно отправить без правок'));
      }, { once: true });
      const frame = cropFrame(() => video.getBoundingClientRect());
      const stage = h('div.vt-stage', video, frame.el);
      let duration = 0;
      let start = current ? Number(current.trim_start || 0) : 0;
      let end = current ? Number(current.trim_end || 0) : 0;
      let rotate = current ? Number(current.rotate || 0) : 0;
      let cropOn = !!(current && current.crop);

      // ── лента кадров ────────────────────────────────────────────────────
      const strip = h('div.vt-strip');
      const shadeL = h('div.vt-shade left'), shadeR = h('div.vt-shade right');
      const handleL = h('div.vt-handle left', h('span')), handleR = h('div.vt-handle right', h('span'));
      const playhead = h('div.vt-playhead');
      const selection = h('div.vt-selection');
      const track = h('div.vt-track', strip, shadeL, shadeR, selection, handleL, handleR, playhead);
      const timeLabel = h('div.vt-time', '0:00');

      async function buildStrip() {
        const count = Math.min(12, Math.max(6, Math.round(track.clientWidth / 90)) || 8);
        Kov.UI.clear(strip);
        const cvs = [];
        for (let i = 0; i < count; i++) { const c = h('canvas.vt-frame'); strip.append(c); cvs.push(c); }
        if (!duration) return;
        const w = 96, hgt = Math.max(24, Math.round(w * (video.videoHeight || 9) / (video.videoWidth || 16)));
        const grab = document.createElement('video');
        grab.src = url; grab.muted = true; grab.preload = 'metadata';
        await new Promise(r => { grab.onloadedmetadata = r; grab.onerror = r; });
        for (let i = 0; i < count; i++) {
          const t = duration * (i + 0.5) / count;
          try {
            await new Promise((res, rej) => {
              const to = setTimeout(res, 1200);          // не ждём вечно на длинном видео
              grab.onseeked = () => { clearTimeout(to); res(); };
              grab.onerror = () => { clearTimeout(to); rej(); };
              grab.currentTime = Math.min(t, Math.max(0, duration - 0.05));
            });
            const c = cvs[i];
            c.width = w; c.height = hgt;
            c.getContext('2d').drawImage(grab, 0, 0, w, hgt);
          } catch (e) { break; }
        }
        try { grab.src = ''; } catch (e) { /* */ }
      }

      function paintTrack() {
        if (!duration) return;
        const a = start / duration, b = (end || duration) / duration;
        shadeL.style.width = (a * 100) + '%';
        shadeR.style.width = ((1 - b) * 100) + '%';
        selection.style.left = (a * 100) + '%';
        selection.style.width = ((b - a) * 100) + '%';
        handleL.style.left = (a * 100) + '%';
        handleR.style.left = (b * 100) + '%';
        timeLabel.textContent = fmtTime(start) + ' – ' + fmtTime(end || duration)
          + '  ·  длительность ' + fmtTime((end || duration) - start);
      }
      function seekTo(sec) { try { video.currentTime = Math.max(0, Math.min(duration, sec)); } catch (e) { /* */ } }

      let dragKind = null;
      function trackPos(e) { const r = track.getBoundingClientRect(); return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)); }
      function dragMove(e) {
        const p = trackPos(e) * duration;
        const MIN = Math.min(0.5, duration / 20);
        if (dragKind === 'left') { start = Math.min(p, (end || duration) - MIN); seekTo(start); }
        else if (dragKind === 'right') { end = Math.max(p, start + MIN); seekTo(end); }
        paintTrack();
      }
      function dragEnd() { dragKind = null; window.removeEventListener('pointermove', dragMove); window.removeEventListener('pointerup', dragEnd); }
      const startDrag = kind => e => {
        e.preventDefault();
        try { e.currentTarget.setPointerCapture(e.pointerId); } catch (err) { /* */ }
        dragKind = kind;
        window.addEventListener('pointermove', dragMove);
        window.addEventListener('pointerup', dragEnd);
      };
      handleL.addEventListener('pointerdown', startDrag('left'));
      handleR.addEventListener('pointerdown', startDrag('right'));
      track.addEventListener('pointerdown', e => {
        if (e.target === handleL || e.target === handleR || handleL.contains(e.target) || handleR.contains(e.target)) return;
        seekTo(trackPos(e) * duration);
      });
      video.addEventListener('timeupdate', () => {
        if (!duration) return;
        playhead.style.left = (video.currentTime / duration * 100) + '%';
        if (video.currentTime >= (end || duration)) video.pause();
      });

      // Размер кадра считаем сами: max-height в процентах внутри сжимаемой
      // колонки окна давал кадр выше самой области, и нижняя ручка рамки
      // уезжала под край — её нельзя было ухватить
      function fitVideo() {
        const vw = video.videoWidth, vh = video.videoHeight;
        if (!vw || !vh) return;
        const cs = getComputedStyle(stage);
        const padX = parseFloat(cs.paddingLeft || 0) + parseFloat(cs.paddingRight || 0);
        const padY = parseFloat(cs.paddingTop || 0) + parseFloat(cs.paddingBottom || 0);
        const box = stage.getBoundingClientRect();
        const cw = Math.max(40, box.width - padX), ch = Math.max(40, box.height - padY);
        const turned = rotate % 180 !== 0;         // повёрнутый кадр занимает область накрест
        const maxW = turned ? ch : cw, maxH = turned ? cw : ch;
        const s = Math.min(maxW / vw, maxH / vh, 1);
        video.style.width = Math.round(vw * s) + 'px';
        video.style.height = Math.round(vh * s) + 'px';
      }

      video.addEventListener('loadedmetadata', () => {
        duration = video.duration || 0;
        if (!end || end > duration) end = duration;
        fitVideo();
        paintTrack(); buildStrip();     // теперь с настоящими кадрами
        setTimeout(() => frame.layout(cropOn), 30);
        if (current && current.crop) frame.setRatio(null, 1);
      });

      // ── соотношения сторон и поворот ────────────────────────────────────
      const ratioRow = h('div.vt-ratios');
      const RATIOS = [['free', 'Свободно', null], ['1:1', '1:1', 1], ['4:3', '4:3', 4 / 3], ['16:9', '16:9', 16 / 9], ['9:16', '9:16', 9 / 16]];
      RATIOS.forEach(([key, text, value], i) => {
        const b = h('button.chip' + (i === 0 ? '.info' : ''), { type: 'button' }, text);
        b.addEventListener('click', () => {
          ratioRow.querySelectorAll('.chip').forEach(x => x.classList.toggle('info', x === b));
          cropOn = true; cropBtn.classList.add('on'); ratioRow.hidden = false;
          const box = video.getBoundingClientRect();
          frame.layout(true);
          frame.setRatio(value, box.width / Math.max(1, box.height));
        });
        ratioRow.append(b);
      });
      const cropBtn = button('', { kind: 'ghost', icon: 'crop', title: 'Рамка кадра', onClick: () => {
        cropOn = !cropOn; cropBtn.classList.toggle('on', cropOn);
        if (!cropOn) frame.reset();
        ratioRow.hidden = !cropOn;
        frame.layout(cropOn);
      } });
      const rotateBtn = button('', { kind: 'ghost', icon: 'rotate', title: 'Повернуть на 90°', onClick: () => {
        rotate = (rotate + 90) % 360;
        video.style.transform = 'rotate(' + rotate + 'deg)';
        video.classList.toggle('vt-rot', rotate % 180 !== 0);
        rotateBtn.classList.toggle('on', rotate !== 0);
        fitVideo();
        setTimeout(() => frame.layout(cropOn), 220);
      } });

      // Плей/пауза: на кнопке треугольник или две полоски — по состоянию видео
      const playBtn = button('', { kind: 'ghost', icon: 'play', cls: 'vt-play', title: 'Просмотр фрагмента', onClick: () => {
        if (video.paused) {
          const stop = end || duration;
          if (video.currentTime < start - 0.05 || video.currentTime >= stop - 0.05) seekTo(start);
          const p = video.play(); if (p && p.catch) p.catch(() => {});
        } else video.pause();
      } });
      function paintPlay() {
        const playing = !video.paused && !video.ended;
        Kov.UI.clear(playBtn);
        playBtn.append(playing ? icon('pause', { stroke: 2.6 }) : icon('play', { fill: true, stroke: 1.2 }));
        playBtn.title = playing ? 'Пауза' : 'Просмотр фрагмента';
        playBtn.classList.toggle('on', playing);
      }
      video.addEventListener('play', paintPlay);
      video.addEventListener('pause', paintPlay);
      video.addEventListener('ended', paintPlay);
      paintPlay();
      stage.addEventListener('click', e => { if (e.target === video) playBtn.click(); });
      ratioRow.hidden = !cropOn;

      function resetAll() {
        start = 0; end = duration; rotate = 0; cropOn = false;
        video.style.transform = ''; video.classList.remove('vt-rot');
        rotateBtn.classList.remove('on'); cropBtn.classList.remove('on');
        fitVideo();
        frame.reset(); frame.layout(false); ratioRow.hidden = true;
        try { video.pause(); } catch (e) { /* */ }
        paintTrack();
      }

      const dlg = Kov.UI.modal({
        title: 'Обрезка видео', glyph: 'video', wide: true,
        body: el => {
          el.classList.add('vt-body');
          el.append(stage, h('div.vt-panel', playBtn, cropBtn, rotateBtn, h('span.spacer'), timeLabel),
            track, ratioRow,
            h('div.small.muted', 'Обрезка и поворот применяются на сервере при сжатии — устройство не нагружается.'));
        },
        buttons: [
          // «Сбросить» снимает все правки: файл уйдёт как есть, без пометки «изменено»
          { text: 'Сбросить', kind: 'ghost', icon: 'refresh', onClick: close => { resetAll(); close(); resolve(null); } },
          { text: 'Отмена', kind: 'ghost', onClick: close => { close(); resolve(CANCELLED); } },
          { text: 'Готово', icon: 'check', onClick: close => {
            const rect = cropOn ? frame.rect() : null;
            const trimmed = start > 0.05 || (end && duration && end < duration - 0.05);
            close();
            if (!trimmed && !rect && !rotate) return resolve(null);
            resolve({
              trim_start: Math.round(start * 1000) / 1000,
              trim_end: end && end < duration ? Math.round(end * 1000) / 1000 : 0,
              crop: rect ? { x: +rect.x.toFixed(4), y: +rect.y.toFixed(4), w: +rect.w.toFixed(4), h: +rect.h.toFixed(4) } : null,
              rotate,
            });
          } },
        ],
      });
      fullscreenEditor(dlg.el);   // телефон: редактор во весь экран
      // Пустая лента появляется сразу — иначе до загрузки метаданных под
      // дорожкой зияет пустота, а на длинном видео это заметная пауза
      buildStrip();
      const relayout = () => { fitVideo(); paintTrack(); frame.layout(cropOn); };
      window.addEventListener('resize', relayout);
      // Размер кадра меняется после загрузки метаданных и при повороте —
      // рамка обрезки обязана переехать вместе с ним
      let ro = null;
      if (window.ResizeObserver) { ro = new ResizeObserver(() => frame.layout(cropOn)); ro.observe(video); }
      const origClose = dlg.close;
      dlg.close = () => {
        window.removeEventListener('resize', relayout);
        if (ro) ro.disconnect();
        try { video.pause(); } catch (e) { /* */ }
        origClose();
      };
      setTimeout(relayout, 80);
    });
  }

  /** Меню скрепки на телефоне: «Фото или видео» открывает галерею, «Файл» — проводник */
  function attachMenu(onFiles) {
    // шторка вложений как в Telegram: живая камера, последние фото и видео, выбор нескольких, файлы
    if (Kov.AttachSheet) return Kov.AttachSheet.open(files => onFiles(files));
    const media = h('input', { type: 'file', multiple: true, accept: 'image/*,video/*', hidden: true });
    const any = h('input', { type: 'file', multiple: true, hidden: true });
    media.addEventListener('change', () => { onFiles(media.files); media.remove(); });
    any.addEventListener('change', () => { onFiles(any.files); any.remove(); });
    document.body.append(media, any);
    const dlg = Kov.UI.modal({
      title: 'Что приложить', glyph: 'plus',
      body: el => el.append(
        h('button.attach-choice', { type: 'button', onclick: () => { dlg.close(); media.click(); } }, icon('image'), h('div.who', h('b', 'Фото или видео'), h('span', 'Из галереи, с превью и правкой'))),
        h('button.attach-choice', { type: 'button', onclick: () => { dlg.close(); any.click(); } }, icon('file'), h('div.who', h('b', 'Файл'), h('span', 'Документ, архив, любой другой файл')))),
      buttons: [{ text: 'Отмена', kind: 'ghost', onClick: close => { close(); media.remove(); any.remove(); } }],
    });
    return dlg;
  }

  return { open, editImage, editVideo, attachMenu, isImage, isVideo, isMobile, dragHasFiles };
})();
