/* Шторка вложений на телефоне (как в Telegram): живая камера, последние фото и видео, выбор нескольких
   (кружок, удержание и протягивание), просмотр с редактором фото и видео, подпись и отправка.
   Галерею и камеру телефона подключает приложение (Kov.AttachSheet.provider), на сайте — системный выбор (айфон).
   open(onFiles, opts): opts.onSend(files, caption, edits) — чат (без промежуточного окна);
   opts.single + opts.kind = 'image' — выбор одного фото; opts.onCancel — закрыли без выбора. */
window.Kov = window.Kov || {};
Kov.AttachSheet = (function () {
  'use strict';
  const { h, icon } = Kov.UI;
  // поставщик галереи и камеры: { camera(audio), access(), request(), recent(offset, limit), thumbs(ids), pick(ids) }
  const provider = () => (window.Kov && Kov.AttachSheet && Kov.AttachSheet.provider) || null;
  const editors = () => (Kov.SendDialog && Kov.SendDialog.editImage ? Kov.SendDialog : null);

  function fmtDur(s) {
    s = Math.max(0, Math.round(Number(s) || 0));
    return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
  }
  function stamp() {
    const d = new Date(), p = n => String(n).padStart(2, '0');
    return d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) + '_' + p(d.getHours()) + p(d.getMinutes()) + p(d.getSeconds());
  }
  function layer(cls) {
    const root = h('div.kov-sheet-root.' + cls);
    document.body.append(root);
    if (Kov.UI.raise) Kov.UI.raise(root);   // поверх открытых окон и шторок
    requestAnimationFrame(() => root.classList.add('open'));
    return root;
  }
  function stop(stream) { if (stream) stream.getTracks().forEach(t => { try { t.stop(); } catch (e) { /* */ } }); }
  const kindOf = f => (f.type || '').startsWith('video/') ? 'video' : (f.type || '').startsWith('image/') ? 'image' : 'file';
  // значок отправки — тот же «самолётик», что у кнопки рядом со строкой ввода чата
  function planeIcon() {
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor'); svg.setAttribute('stroke-width', '2');
    const l = document.createElementNS(ns, 'line');
    [['x1', 22], ['y1', 2], ['x2', 11], ['y2', 13]].forEach(([k, v]) => l.setAttribute(k, v));
    const p = document.createElementNS(ns, 'polygon'); p.setAttribute('points', '22 2 15 22 11 13 2 9 22 2');
    svg.append(l, p);
    return svg;
  }

  function open(onFiles, opts) {
    opts = opts || {};
    const N = provider();
    const onlyImages = opts.kind === 'image';
    const chat = typeof opts.onSend === 'function';
    const st = { items: [], selected: [], stream: null, closed: false, sent: false, urls: [], access: N ? 'none' : 'web' };
    let dragging = false;   // удержание выбрало плитку — палец, не отрываясь, выбирает задетые
    const root = layer('attach-root');
    const grid = h('div.attach-grid');
    const count = h('span.attach-count');
    // подпись — многострочная, растёт до четырёх строк
    const caption = h('textarea.attach-caption', { rows: 1, placeholder: 'Добавить подпись…', maxlength: 4000 });
    caption.addEventListener('input', () => { caption.style.height = 'auto'; caption.style.height = Math.min(caption.scrollHeight, 104) + 'px'; });
    // чат: круглая кнопка, как у строки ввода, с числом выбранного; иначе — «Прикрепить (N)»
    const sendBtn = chat
      ? h('button.attach-sendbtn', { type: 'button', hidden: true, title: 'Отправить', onclick: () => send(st.selected.slice()) }, planeIcon(), count)
      : h('button.btn.primary.attach-send', { type: 'button', hidden: true, onclick: () => send(st.selected.slice()) }, icon('check'), count);
    const pickerMedia = h('input', { type: 'file', multiple: !opts.single, accept: onlyImages ? 'image/*' : 'image/*,video/*', hidden: true });
    const pickerAny = h('input', { type: 'file', multiple: !opts.single, accept: onlyImages ? 'image/*' : null, hidden: true });
    pickerMedia.addEventListener('change', () => { addWebFiles(Array.from(pickerMedia.files || [])); pickerMedia.value = ''; });
    pickerAny.addEventListener('change', () => {
      const f = Array.from(pickerAny.files || []); pickerAny.value = '';
      if (!f.length) return;
      if (chat) return addWebFiles(f);                  // чат: документы — в ту же сетку, с подписью и отправкой
      done(f, {});
    });
    const tabs = h('div.attach-tabs',
      // системный выбор: на сайте — всегда, в приложении — если доступ к фото не полный
      h('button.attach-tab.on', { type: 'button', onclick: () => {
        if (!N || st.access !== 'granted') pickerMedia.click(); else grid.scrollTo({ top: 0, behavior: 'smooth' });
      } }, icon('image'), h('span', 'Галерея')),
      h('button.attach-tab', { type: 'button', onclick: () => pickerAny.click() }, icon('file'), h('span', 'Файлы')));
    const bar = h('div.attach-bar' + (chat ? '.chat' : ''), tabs, chat ? caption : null, sendBtn);
    const panel = h('div.kov-sheet.attach-sheet',
      h('div.kov-sheet-handle'),
      h('div.kov-sheet-head', h('b', opts.single ? 'Выберите фото' : 'Галерея'),
        h('button.kov-sheet-x', { type: 'button', title: 'Закрыть', 'data-close': '1', onclick: () => close() }, icon('close'))),
      grid, bar, pickerMedia, pickerAny);
    root.append(h('div.kov-sheet-back', { onclick: () => close() }), panel);
    if (opts.single) root.classList.add('single');

    // живая камера: плитка высотой в две клетки
    const camVideo = h('video', { autoplay: true, muted: true, playsinline: true });
    camVideo.muted = true;
    const camTile = h('button.attach-cam', { type: 'button', title: 'Камера', onclick: () => openCamera() },
      camVideo, h('span.cam-ico', icon('camera')));
    grid.append(camTile);
    startPreview();
    if (N) loadNative();

    grid.addEventListener('touchmove', e => {
      if (!dragging || e.touches.length !== 1) return;
      e.preventDefault();
      const t = e.touches[0];
      const el = document.elementFromPoint(t.clientX, t.clientY);
      const tile = el && el.closest('.attach-tile');
      const it = tile && st.items.find(x => x.tile === tile);
      if (it && !st.selected.includes(it)) select(it, true);
    }, { passive: false });
    grid.addEventListener('touchend', () => { dragging = false; });
    grid.addEventListener('touchcancel', () => { dragging = false; });

    async function startPreview() {
      try {
        if (N && !(await N.camera(false))) throw new Error('нет разрешения');
        st.stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' }, audio: false });
        if (st.closed) { stop(st.stream); return; }
        camVideo.srcObject = st.stream;
        camTile.classList.add('live');
        await camVideo.play().catch(() => {});
      } catch (e) { camTile.classList.add('off'); }
    }

    async function loadNative() {
      try {
        const access = await N.access();
        st.access = access;
        if (access === 'none') {
          grid.append(h('div.attach-access', h('p', 'Разрешите доступ к фото и видео — здесь появятся последние снимки'),
            h('button.btn', { type: 'button', onclick: async () => {
              const now = await N.request();
              if (now !== 'none') { grid.querySelector('.attach-access').remove(); loadNative(); }
            } }, 'Разрешить')));
          return;
        }
        const list = (await N.recent(0, 90) || []).filter(x => !onlyImages || x.kind === 'image');
        list.forEach(x => addItem({ key: x.id, nativeId: x.id, kind: x.kind, name: x.name, duration: x.duration }));
        for (let i = 0; i < st.items.length; i += 12) {
          if (st.closed) return;
          const chunk = st.items.slice(i, i + 12).filter(it => it.nativeId);
          const map = await N.thumbs(chunk.map(it => it.nativeId));
          chunk.forEach(it => { if (map && map[it.nativeId]) it.img.src = map[it.nativeId]; });
        }
      } catch (e) { /* галерея недоступна — остаются камера, «Галерея» и «Файлы» */ }
    }

    function addWebFiles(files) {
      files.filter(f => !onlyImages || kindOf(f) === 'image').forEach(f => {
        const it = addItem({ key: 'f' + st.items.length + '_' + f.name, file: f, kind: kindOf(f), name: f.name });
        if (opts.single) return pick(it);
        select(it, true);
      });
    }

    function addItem(it) {
      it.img = h('img', { alt: '', decoding: 'async' });
      const check = h('span.attach-check', { onclick: e => { e.stopPropagation(); select(it); } });
      it.tile = h('button.attach-tile', { type: 'button', 'data-kind': it.kind }, it.img,
        it.kind === 'video' ? h('span.attach-dur', icon('video'), it.duration ? fmtDur(it.duration) : '') : null,
        it.kind === 'file' ? h('span.attach-doc', icon('file'), h('span', it.name || 'Файл')) : null,
        h('span.attach-edited', icon('edit')),
        opts.single ? null : check);
      if (it.file && it.kind !== 'file') setPreview(it, it.file);
      // касание: одно фото — выбрать; иначе — открыть (посмотреть, изменить); кружок — выбор
      it.tile.addEventListener('click', () => {
        if (opts.single) return pick(it);
        if (it.kind === 'file') return select(it);
        view(it);
      });
      if (!opts.single) Kov.UI.longPress(it.tile, () => { select(it, true); dragging = true; });
      st.items.push(it);
      grid.append(it.tile);
      return it;
    }

    function setPreview(it, file) {
      const url = URL.createObjectURL(file);
      st.urls.push(url);
      it.url = url;
      if (it.kind === 'image') it.img.src = url;
      else if (it.kind === 'video') {
        it.tile.classList.add('as-video');
        const old = it.tile.querySelector(':scope > video');
        if (old) old.remove();
        it.tile.prepend(h('video', { src: url, muted: true, preload: 'metadata', playsinline: true }));
      }
    }

    // выбор: подсветка и номер по порядку; в чате — подпись и «Отправить (N)», иначе «Прикрепить (N)»
    function select(it, only) {
      const i = st.selected.indexOf(it);
      if (i >= 0 && only !== true) st.selected.splice(i, 1);
      else if (i < 0) st.selected.push(it);
      st.items.forEach(x => {
        const n = st.selected.indexOf(x);
        x.tile.classList.toggle('sel', n >= 0);
        const c = x.tile.querySelector('.attach-check');
        if (c) c.textContent = n >= 0 ? String(n + 1) : '';
      });
      const any = st.selected.length > 0;
      grid.classList.toggle('selecting', any);
      bar.classList.toggle('has-selection', any);
      sendBtn.hidden = !any;
      count.textContent = any ? (chat ? String(st.selected.length) : 'Прикрепить (' + st.selected.length + ')') : '';
    }

    // файл выбранного элемента: с телефона — потоком из галереи (реестр выбранных: видео сожмёт телефон)
    async function fileOf(it) {
      if (it.file) return it.file;
      if (!it.picked) {
        const res = await N.pick([it.nativeId]);
        if (!res || !res[0]) throw new Error('файл недоступен');
        it.picked = res[0];
      }
      const blob = await (await fetch(it.picked.url)).blob();
      it.file = new File([blob], it.picked.name, { type: it.picked.mime || blob.type, lastModified: it.picked.lastModified || Date.now() });
      return it.file;
    }

    // выбранное в галерее телефона — без копирования: телефон отдаёт эти файлы странице как обычный выбор
    // файла (страница читает их прямо с телефона); не вышло — fileOf скопирует, как раньше
    async function directFiles(items) {
      if (!N || !N.preset || !items.length) return false;
      const need = items.filter(it => !it.picked);
      if (need.length) {
        const res = await N.pick(need.map(it => it.nativeId));
        need.forEach((it, i) => { if (res && res[i]) it.picked = res[i]; });
      }
      if (items.some(it => !it.picked)) return false;
      let files = null;
      try {
        if ((await N.preset(items.map(it => it.nativeId))) !== items.length) return false;
        const input = document.createElement('input');
        input.type = 'file'; input.multiple = true; input.hidden = true;
        document.body.appendChild(input);
        try {
          files = await new Promise(resolve => {
            const t = setTimeout(() => resolve(null), 4000);
            input.addEventListener('change', () => { clearTimeout(t); resolve(Array.from(input.files || [])); }, { once: true });
            input.click();
          });
        } finally { input.remove(); }
      } catch (e) { files = null; }
      const ok = files && files.length === items.length
        && files.every((f, i) => f.name === items[i].picked.name && f.size === items[i].picked.size);
      if (!ok) { try { await N.preset([]); } catch (e) { /* */ } return false; }   // заготовку — не оставлять чужому выбору
      files.forEach((f, i) => {
        try { f.kovModified = items[i].picked.lastModified || 0; } catch (e) { /* */ }   // ключ запоминания отпечатка
        items[i].file = f;
      });
      return true;
    }

    async function view(it) {
      let file;
      if (!it.file && it.nativeId) await directFiles([it]).catch(() => false);
      try { file = await fileOf(it); } catch (e) { Kov.UI.toast('Файл недоступен', 'error'); return; }
      if (!it.url) setPreview(it, file);
      const v = layer('attach-view');
      const shut = () => v.remove();
      const body = h('div.attach-view-body');
      const paint = () => {
        Kov.UI.clear(body);
        body.append(it.kind === 'video'
          ? h('video', { src: it.url, controls: true, autoplay: true, playsinline: true })
          : h('img', { src: it.url, alt: it.name || '' }));
      };
      paint();
      const E = editors();
      // редактор — поверх: шторка и просмотр на время правки прячутся, но не закрываются (выбор и правки целы)
      const editBtn = E && (it.kind === 'image' || it.kind === 'video')
        ? tool('edit', 'Изменить', async () => {
          [root, v].forEach(l => l.classList.add('under-editor'));
          try {
          if (it.kind === 'image') {
            const r = await E.editImage(it.file, it.url);
            if (r && r.cancelled) return;
            if (!r) { if (it.original) { it.file = it.original; it.original = null; setPreview(it, it.file); } it.edited = false; }
            else { if (!it.original) it.original = it.file; it.file = r; setPreview(it, r); it.edited = true; }
          } else {
            const r = await E.editVideo(it.file, it.url, it.edit);
            if (r && r.cancelled) return;
            it.edit = r || null; it.edited = !!r;
          }
          it.tile.classList.toggle('edited', !!it.edited);
          if (!st.selected.includes(it)) select(it, true);
          paint(); paintSel();
          } finally { [root, v].forEach(l => l.classList.remove('under-editor')); }
        })
        : null;
      const inSel = () => st.selected.includes(it);
      const selBtn = tool('plus', 'Выбрать', () => { select(it, inSel() ? undefined : true); paintSel(); });
      const paintSel = () => {
        selBtn.classList.toggle('on', inSel());
        Kov.UI.clear(selBtn); selBtn.append(icon(inSel() ? 'check' : 'plus'), h('span', inSel() ? 'Выбрано' : 'Выбрать'));
      };
      paintSel();
      v.append(
        h('div.attach-view-top', h('span.attach-view-name', it.name || ''),
          h('button.kov-sheet-x', { type: 'button', title: 'Закрыть', 'data-close': '1', onclick: shut }, icon('close'))),
        body,
        h('div.attach-view-bar', editBtn, selBtn, h('span.av-spacer'),
          // отправляется выбранное (с этим файлом); ничего не выбрано — только этот
          chat
            ? h('button.attach-sendbtn.big', { type: 'button', title: 'Отправить', onclick: () => { shut(); send(inSel() ? st.selected.slice() : [it]); } }, planeIcon())
            : tool('check', 'Прикрепить', () => { shut(); send(inSel() ? st.selected.slice() : [it]); }, '.primary')));
    }

    function pick(it) { send([it]); }

    function tool(ic, label, fn, cls) {
      return h('button.av-tool' + (cls || ''), { type: 'button', onclick: fn }, icon(ic), h('span', label));
    }

    // выбранное — в File (как после системного выбора) и правки видео по номеру файла
    async function send(list) {
      if (!list.length) return;
      sendBtn.disabled = true;
      try {
        const files = [], edits = {};
        const t0 = Date.now();   // в журнал: сколько ушло на получение файлов (раньше — копирование исходников)
        const fromGallery = list.filter(it => !it.file && it.nativeId);
        const direct = fromGallery.length ? await directFiles(fromGallery).catch(() => false) : false;
        for (const it of list) {
          const f = await fileOf(it);
          if (it.kind === 'video' && it.edit) edits[files.length] = it.edit;
          files.push(f);
        }
        if (fromGallery.length) {
          console.info('Шторка: файлы из галереи получены за ' + ((Date.now() - t0) / 1000).toFixed(1) + ' с, '
            + (direct ? 'без копирования' : 'с копированием') + ' (' + fromGallery.length + ' шт.)');
        }
        done(files, edits);
      } catch (e) {
        Kov.UI.toast('Не удалось прочитать файл: ' + Kov.UI.errorText(e), 'error');
      } finally { sendBtn.disabled = false; }
    }

    function done(files, edits) {
      st.sent = true;
      const text = caption.value.trim();
      close();
      if (chat) opts.onSend(files, text, edits);
      else onFiles(files, edits);
    }

    // съёмка внутри приложения: касание — фото, удержание — видео (в режиме «одно фото» — только фото)
    function openCamera() {
      stop(st.stream); st.stream = null; camTile.classList.remove('live');
      const cam = layer('attach-camera');
      const video = h('video', { autoplay: true, muted: true, playsinline: true });
      video.muted = true;
      const hint = h('div.cam-hint', onlyImages ? 'Касание — фото' : 'Касание — фото, удержание — видео');
      const shutter = h('button.cam-shutter', { type: 'button', title: 'Снять' });
      let facing = 'environment', stream = null, rec = null, chunks = [], holdTimer = 0, recording = false;
      const shut = () => { stop(stream); if (rec && rec.state !== 'inactive') rec.stop(); cam.remove(); if (!st.closed) startPreview(); };
      cam.append(
        h('div.attach-view-top', h('span'),
          h('button.kov-sheet-x', { type: 'button', title: 'Закрыть', 'data-close': '1', onclick: shut }, icon('close'))),
        h('div.cam-body', video, hint),
        h('div.cam-bar',
          h('span'), shutter,
          h('button.cam-flip', { type: 'button', title: 'Сменить камеру', onclick: () => { facing = facing === 'environment' ? 'user' : 'environment'; start(); } }, icon('rotate'))));

      async function start() {
        stop(stream);
        try {
          const wantAudio = !onlyImages;
          if (N && !(await N.camera(wantAudio))) throw new Error('нет разрешения');
          try {
            stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: facing, width: { ideal: 1920 }, height: { ideal: 1080 } }, audio: wantAudio });
          } catch (e) {
            stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: facing }, audio: false });
          }
          video.srcObject = stream;
          await video.play().catch(() => {});
        } catch (e) { hint.textContent = 'Камера недоступна: разрешите доступ к камере'; }
      }
      start();

      shutter.addEventListener('pointerdown', () => { if (!onlyImages) holdTimer = setTimeout(startRec, 350); else holdTimer = -1; });
      shutter.addEventListener('pointerup', () => { if (holdTimer > 0) clearTimeout(holdTimer); if (recording) stopRec(); else if (holdTimer) photo(); holdTimer = 0; });
      shutter.addEventListener('pointerleave', () => { if (holdTimer > 0) clearTimeout(holdTimer); holdTimer = 0; if (recording) stopRec(); });

      function photo() {
        if (!video.videoWidth) return;
        const c = document.createElement('canvas');
        c.width = video.videoWidth; c.height = video.videoHeight;
        c.getContext('2d').drawImage(video, 0, 0);
        c.toBlob(b => b && preview(new File([b], 'Фото_' + stamp() + '.jpg', { type: 'image/jpeg', lastModified: Date.now() })), 'image/jpeg', 0.92);
      }
      function startRec() {
        holdTimer = 0;
        if (!stream || !window.MediaRecorder) return;
        const types = ['video/mp4', 'video/webm;codecs=vp8,opus', 'video/webm'];
        const type = types.find(t => MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(t)) || '';
        rec = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
        chunks = [];
        rec.ondataavailable = e => { if (e.data && e.data.size) chunks.push(e.data); };
        rec.onstop = () => {
          const mime = (rec.mimeType || type || 'video/webm').split(';')[0];
          const ext = mime.includes('mp4') ? '.mp4' : '.webm';
          if (chunks.length) preview(new File(chunks, 'Видео_' + stamp() + ext, { type: mime, lastModified: Date.now() }));
        };
        rec.start(500);
        recording = true;
        shutter.classList.add('rec');
        hint.textContent = 'Идёт запись — отпустите, чтобы закончить';
      }
      function stopRec() { recording = false; shutter.classList.remove('rec'); if (rec && rec.state !== 'inactive') rec.stop(); }

      // снятое — в сетку и сразу в просмотр: там можно изменить, выбрать или отправить
      function preview(file) {
        shut();
        const it = addItem({ key: 'cam' + Date.now(), file, kind: kindOf(file), name: file.name });
        grid.insertBefore(it.tile, camTile.nextSibling);
        if (opts.single) return pick(it);
        select(it, true);
        view(it);
      }
    }

    function close() {
      if (st.closed) return;
      st.closed = true;
      stop(st.stream);
      root.classList.remove('open');
      setTimeout(() => { root.remove(); st.urls.forEach(u => URL.revokeObjectURL(u)); }, 220);
      if (!st.sent && opts.onCancel) opts.onCancel();
    }

    return { close, root };
  }

  return { open, provider: null };
})();
