/* Файлы в веб-версии: категории, адреса, плитки «три в ряд» с миниатюрами
 * с сервера и подписью «кто прикрепил», фильтр по отправителю, загрузка с
 * прогрессом, просмотр фото и видео. Используется окном задачи и проектами. */
window.Kov = window.Kov || {};

Kov.Files = (function () {
  const { h, icon, button } = Kov.UI;

  const IMAGE_EXT = ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.webp', '.svg'];
  const VIDEO_EXT = ['.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.mkv', '.ts'];
  const DOC_EXT = ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.txt'];
  const MAX_UPLOAD = 2 * 1024 * 1024 * 1024;
  const CATEGORIES = [['documents', 'Документы', 'file'], ['images', 'Изображения', 'image'], ['videos', 'Видео', 'video'], ['others', 'Другое', 'folder']];

  function ext(name) { const m = /\.[^.\/\\]+$/.exec(String(name || '').toLowerCase()); return m ? m[0] : ''; }
  function isImage(name) { return IMAGE_EXT.includes(ext(name)); }
  function isVideo(name) { return VIDEO_EXT.includes(ext(name)); }
  function isMedia(name) { return isImage(name) || isVideo(name); }
  function categorize(name) {
    const e = ext(name);
    if (IMAGE_EXT.includes(e)) return 'images';
    if (VIDEO_EXT.includes(e)) return 'videos';
    if (DOC_EXT.includes(e)) return 'documents';
    return 'others';
  }

  const ENDPOINTS = {
    task: { sheet: id => 'files/' + id + '/sheet?is_stage=false', download: id => 'task/files/' + id + '/download', stream: (id, name) => 'task/files/' + id + '/stream/' + encodeURIComponent(name), view: id => 'files/' + id + '/view?is_stage=false', thumb: id => 'task/files/' + id + '/thumb', del: id => 'files/task/' + id },
    stage: { sheet: id => 'files/' + id + '/sheet?is_stage=true', download: id => 'stages/files/' + id + '/download', stream: (id, name) => 'stages/files/' + id + '/stream/' + encodeURIComponent(name), view: id => 'files/' + id + '/view?is_stage=true', thumb: id => 'stages/files/' + id + '/thumb', del: id => 'files/stage/' + id },
    project: { sheet: id => 'projects/files/' + id + '/sheet', download: id => 'projects/files/' + id + '/download', stream: (id, name) => 'projects/files/' + id + '/stream/' + encodeURIComponent(name), view: id => 'projects/files/' + id + '/view', thumb: id => 'projects/files/' + id + '/thumb', del: id => 'projects/files/' + id },
  };
  // Что браузер действительно умеет показать сам. Остальное — документы,
  // архивы, таблицы — открывать бессмысленно: их надо скачивать.
  const VIEWABLE_EXT = ['.pdf', '.txt', '.log', '.md'];
  // PDF, Word и PowerPoint показывает само устройство — соседней вкладкой, как
  // в мессенджерах. Старые форматы (doc, odt, rtf, ppt, odp) устройство не
  // читает: их показывает сервер, если умеет, иначе файл скачивается.
  const DOC_RE = /\.(pdf|docx|docm|dotx|pptx|pptm|ppsx|potx)$/i;
  const DOC_PAGE = ((document.currentScript && document.currentScript.src) || '').replace(/js\/core\/files\.js.*$/, 'doc.html') || 'doc.html';
  const OFFICE_EXT = ['.doc', '.docx', '.odt', '.rtf', '.xls', '.xlsx', '.ods', '.csv', '.ppt', '.pptx', '.odp'];
  function isOffice(name) { return OFFICE_EXT.includes(ext(name)); }
  function isViewable(name) { return isImage(name) || VIEWABLE_EXT.includes(ext(name)) || isOffice(name); }
  function fileKind(file) { return file.file_kind || (file.stage_detail_id ? 'stage' : 'task'); }
  /** Имя и номер файла — терпимо к разным названиям полей на сервере */
  function fileName(file) { return String((file && (file.filename || file.file_name || file.name || file.original_name)) || ''); }
  function fileId(file) { return (file && (file.id != null ? file.id : (file.file_id != null ? file.file_id : file.task_file_id))) || null; }
  function url(file, action) {
    const e = ENDPOINTS[fileKind(file)];
    const id = fileId(file);
    const u = Kov.Api.fileUrl(action === 'stream' ? e.stream(id, fileName(file)) : e[action](id));
    // ce=1: сжатый при хранении файл сервер отдаёт как есть, распаковывает браузер
    return action === 'download' || action === 'view' ? u + (u.includes('?') ? '&' : '?') + 'ce=1' : u;
  }

  /** Адрес разбора таблицы: есть не у всех видов файлов */
  function sheetUrl(file) {
    const e = ENDPOINTS[fileKind(file)];
    return e && e.sheet ? e.sheet(fileId(file)) : null;
  }

  function safeName(name) { try { return decodeURIComponent(String(name || '')); } catch (e) { return String(name || ''); } }

  function uploaderName(file, short) {
    const person = file.uploaded_by;
    if (!person) return '';
    let first = person.first_name || '';
    if (short && first) first = first[0] + '.';
    return ((person.last_name || '') + ' ' + first).trim();
  }

  function fmtSize(bytes) {
    if (bytes == null) return '';
    if (bytes < 1024) return bytes + ' Б';
    if (bytes < 1048576) return (bytes / 1024).toFixed(0) + ' КБ';
    if (bytes < 1073741824) return (bytes / 1048576).toFixed(1) + ' МБ';
    return (bytes / 1073741824).toFixed(2) + ' ГБ';
  }

  const DOC_GLYPHS = [[['.xls', '.xlsx', '.ods', '.csv'], 'table'], [['.zip', '.rar', '.7z', '.tar', '.gz'], 'archive'], [['.txt', '.md', '.log'], 'list']];
  function docGlyph(name) {
    const e = ext(name);
    for (const [exts, glyph] of DOC_GLYPHS) if (exts.includes(e)) return glyph;
    return 'file';
  }

  // ── плитка и сетка ──────────────────────────────────────────────────────

  /**
   * grid({files, canDelete(file)->bool, onDelete(file), viewer:true, emptyText})
   * Плитки три в ряд (на телефоне — одна колонка), миниатюры с сервера,
   * подпись «кто прикрепил», кнопки просмотр/скачать/удалить.
   */
  function grid(cfg) {
    const root = h('div.file-grid');   // при пустом списке — растянутая подсказка
    let files = [];
    // Плитки переиспользуются: при обновлении списка миниатюры не грузятся
    // заново, и раздел не моргает — новый файл просто появляется в ряду.
    const byKey = new Map();          // id файла → { el, sig }
    const sigOf = f => [fileId(f), fileName(f), f.size, f.created_at, f.uploaded_by_id,
                        f.uploaded_by && f.uploaded_by.id].join('|');

    function fill(list) {
      files = list || [];
      const empty = root.querySelector('.empty');
      if (!files.length) {
        Kov.UI.clear(root); byKey.clear();
        root.append(h('div.empty', cfg.emptyText || 'Файлов в этой категории пока нет'));
        return;
      }
      if (empty) empty.remove();
      const seen = new Set();
      let prev = null;
      files.forEach(file => {
        const key = String(fileId(file)), sig = sigOf(file);
        seen.add(key);
        let rec = byKey.get(key);
        if (!rec || rec.sig !== sig) {
          const el = tile(file);
          if (rec) rec.el.replaceWith(el); else el.classList.add('tile-appear');
          rec = { el, sig };
          byKey.set(key, rec);
        }
        const target = prev ? prev.nextSibling : root.firstChild;
        if (rec.el !== target) root.insertBefore(rec.el, target);
        prev = rec.el;
      });
      byKey.forEach((rec, key) => {
        if (seen.has(key)) return;
        rec.el.remove(); byKey.delete(key);
      });
    }
    function tile(file) {
      const fname = fileName(file);
      const media = isMedia(fname);
      const video = isVideo(fname);
      const thumb = h('button.tile-thumb', { type: 'button', title: 'Открыть просмотр', onclick: () => open(file) });
      // PDF: миниатюру делает устройство при загрузке; у старых PDF её нет — сразу значок
      const docThumb = !media && (!!file.thumb_hash || /\.pdf$/i.test(fname));
      if (media || docThumb) {
        // если миниатюры на сервере нет, показываем сам файл, и только потом значок
        const img = h('img', { src: url(file, 'thumb'), alt: '', loading: 'lazy' });
        let attempt = 0;
        img.onerror = async () => {
          attempt += 1;
          if (attempt === 1 && (media || file.thumb_hash)) { await Kov.Api.ensureFileToken(true); img.src = url(file, 'thumb'); return; }
          if (attempt === 2 && !video && !docThumb) { img.src = url(file, 'view'); return; }
          img.onerror = null;
          img.remove(); thumb.append(h('span.tile-glyph', { html: Kov.Icons.svg(video ? 'video' : docThumb ? 'file' : 'image') }));
        };
        thumb.append(img);
        if (video) thumb.append(h('span.tile-play', { html: Kov.Icons.svg('play') }));
      } else {
        thumb.append(h('span.tile-glyph', { html: Kov.Icons.svg(docGlyph(fname)) }));
      }
      const who = uploaderName(file);
      const name = safeName(fname);
      const actions = h('div.tile-actions',
        button('', { kind: 'ghost', icon: 'eye', title: 'Открыть просмотр', onClick: () => open(file) }),
        button('', { kind: 'ghost', icon: 'download', title: 'Скачать файл', onClick: () => download(file) }),
      );
      const canDel = !cfg.canDelete || cfg.canDelete(file);
      const del = async () => {
        if (!await Kov.UI.confirm('Удаление файла', 'Удалить файл «' + name + '»?', { okText: 'Удалить', danger: true })) return;
        try {
          await Kov.Api.del(ENDPOINTS[fileKind(file)].del(file.id));
          Kov.UI.toast('Файл удалён', 'success');
          cfg.onDelete && cfg.onDelete(file);
        } catch (e) { Kov.UI.toast('Не удалось удалить: ' + Kov.UI.errorText(e), 'error'); }
      };
      if (canDel) actions.append(button('', { kind: 'danger', icon: 'trash', title: 'Удалить файл', onClick: del }));
      // телефон: единая карточка — касание открывает, «⋯» — скачать или удалить
      if (Kov.UI.touchUi()) {
        const meta = [who, fmtSize(file.size), file.created_at ? Kov.UI.fmtDateTime(file.created_at) : null].filter(Boolean).join(' · ');
        const more = h('button.tile-more', { type: 'button', title: 'Действия с файлом', 'aria-label': 'Действия с файлом',
          onclick: e => { e.stopPropagation(); fileMenu(name, file, canDel ? del : null); } }, '⋯');
        const card = h('div.file-tile.compact', { tabIndex: 0 }, thumb,
          h('div.tile-info', h('div.tile-name', { text: name, title: name }), h('div.tile-meta', meta)), more);
        card.addEventListener('click', e => { if (!e.target.closest('.tile-more, .tile-thumb')) open(file); });
        return card;
      }
      return h('div.file-tile', thumb,
        h('div.tile-info',
          h('div.tile-name', { text: name, title: name }),
          who ? h('div.tile-who', who) : null,
          h('div.tile-meta', [fmtSize(file.size), file.created_at ? Kov.UI.fmtDateTime(file.created_at) : null].filter(Boolean).join(' · ')),
          actions,
        ));
    }
    function fileMenu(name, file, del) {
      const body = h('div.sheet-actions');
      const sheet = Kov.UI.bottomSheet(name, body);
      const item = (ic, label, fn, danger) => h('button.sheet-action' + (danger ? '.danger' : ''), { type: 'button',
        onclick: () => { sheet.close(); fn(); } }, icon(ic), h('span', label));
      body.append(item('eye', 'Открыть', () => open(file)), item('download', 'Скачать', () => download(file)));
      if (del) body.append(item('trash', 'Удалить', del, true));
    }

    function open(file) {
      const name = fileName(file);
      if (isMedia(name)) {
        const mediaFiles = files.filter(f => isMedia(fileName(f)));
        const index = mediaFiles.indexOf(file);
        viewer(mediaFiles, index < 0 ? 0 : index);
        return;
      }
      // Таблицу показываем настоящими ячейками, а не картинкой из PDF,
      // и отдельной вкладкой — как документы
      if (Kov.SheetView && Kov.SheetView.isSheet(name) && sheetUrl(file)) {
        Kov.SheetView.openTab({
          title: name,
          url: sheetUrl(file),
          downloadUrl: url(file, 'download'),
          onDownload: () => download(file),
          onFallback: () => openViewTab(file, name),
        });
        return;
      }
      if (DOC_RE.test(name)) return openDocument(url(file, 'download'), name, url(file, 'view'));
      if (isViewable(name)) {
        // Просмотр идёт через свой адрес: /stream отдаёт только видео.
        // Документ сервер превратит в PDF; если не сможет — скачиваем.
        openViewTab(file, name);
        return;
      }
      // документ, таблицу или архив браузер показать не может — скачиваем
      download(file);
    }
    /** Открыть просмотр в соседней вкладке, а при отказе сервера — скачать */
    async function openViewTab(file, name) {
      const href = url(file, 'view');
      if (isOffice(name)) {
        // проверяем заранее: иначе человек увидит пустую вкладку с ошибкой
        try {
          const res = await fetch(href, { method: 'GET', headers: { Range: 'bytes=0-0' } });
          if (!res.ok) throw new Error(String(res.status));
        } catch (e) {
          Kov.UI.toast('Показать «' + name + '» не получилось — скачиваем', 'info');
          return download(file);
        }
      }
      // ссылкой, а не window.open: всплывающие окна часто заблокированы
      const a = h('a', { href, target: '_blank', rel: 'noopener' });
      document.body.append(a); a.click(); a.remove();
    }

    function download(file) {
      // с прогрессом в панели загрузок; без модуля — прямой ссылкой
      if (Kov.Downloads) return Kov.Downloads.download(url(file, 'download'), fileName(file));
      const a = h('a', { href: url(file, 'download'), download: fileName(file) });
      document.body.append(a); a.click(); a.remove();
    }
    fill(cfg.files || []);
    root.fill = fill;
    return root;
  }

  /** Список отправителей из файлов: [[id, ФИО], …] */
  function uploaderList(files) {
    const people = new Map();
    (files || []).forEach(f => {
      const id = f.uploaded_by_id || (f.uploaded_by && f.uploaded_by.id);
      const name = uploaderName(f);
      if (id && name && !people.has(id)) people.set(id, name);
    });
    return [...people.entries()].sort((a, b) => a[1].localeCompare(b[1], 'ru'));
  }

  /** Фильтр «Все отправители» с поиском по списку; onChange(id|null). */
  function uploaderFilter(files, onChange, value) {
    const people = uploaderList(files);
    const pick = Kov.UI.pickerSelect([[null, 'Все отправители']].concat(people),
      value === undefined ? null : value, onChange,
      { placeholder: 'Все отправители', title: 'Показать файлы только одного отправителя',
        searchPlaceholder: 'Поиск по ФИО…', empty: 'Никого не найдено' });
    // при одном отправителе фильтр тоже нужен: список бывает длинным
    pick.hidden = people.length === 0;
    pick.title = 'Фильтр по сотруднику';
    return pick;
  }

  // ── просмотр фото и видео: «Свет кадра» ─────────────────────────────────
  // фон светится цветами снимка; управление белое и тихое; у видео — своё управление
  const MV_PATHS = {
    back: '<path d="M15 5l-7 7 7 7"/>', close: '<path d="M6 6l12 12M18 6L6 18"/>',
    down: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>', fwd: '<path d="M14 5l6 6-6 6M20 11H9a5 5 0 0 0-5 5v3"/>',
    reply: '<path d="M10 5L4 11l6 6M4 11h11a5 5 0 0 1 5 5v3"/>', trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
    play: '<path d="M8 5v14l11-7z"/>', pause: '<path d="M7 5h3.2v14H7zM13.8 5H17v14h-3.2z"/>',
    vol: '<path d="M4 9v6h4l5 4V5L8 9H4z"/><path d="M16 9a4 4 0 0 1 0 6M18.5 6.5a7.5 7.5 0 0 1 0 11"/>',
    mute: '<path d="M4 9v6h4l5 4V5L8 9H4z"/><path d="M17 9.5l4 5M21 9.5l-4 5"/>',
    full: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>', info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
    left: '<path d="M14 6l-6 6 6 6"/>', right: '<path d="M10 6l6 6-6 6"/>',
  };
  function mvIcon(name, size) {
    const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    const solid = name === 'play' || name === 'pause';
    s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('width', size || 22); s.setAttribute('height', size || 22);
    s.setAttribute('fill', solid ? 'currentColor' : 'none'); s.setAttribute('stroke', solid ? 'none' : 'currentColor');
    s.setAttribute('stroke-width', '1.9'); s.setAttribute('stroke-linecap', 'round'); s.setAttribute('stroke-linejoin', 'round');
    s.setAttribute('aria-hidden', 'true');
    s.innerHTML = MV_PATHS[name];
    return s;
  }
  const mvBtn = (name, label, onClick, cls) => {
    const b = h('button' + (cls ? '.' + cls : ''), { type: 'button', 'aria-label': label, title: label, onclick: e => { e.stopPropagation(); onClick(e); } });
    b.append(mvIcon(name));
    return b;
  };

  // цвет кадра: средний, насыщенные точки весомее; на тёмном фоне — яркий и заметный
  function frameHue(el) {
    try {
      const c = document.createElement('canvas'); c.width = c.height = 24;
      const g = c.getContext('2d', { willReadFrequently: true });
      g.drawImage(el, 0, 0, 24, 24);
      const d = g.getImageData(0, 0, 24, 24).data;
      let r = 0, gr = 0, b = 0, w = 0;
      for (let k = 0; k < d.length; k += 4) {
        const wt = 1 + 4 * (Math.max(d[k], d[k + 1], d[k + 2]) - Math.min(d[k], d[k + 1], d[k + 2])) / 255;
        r += d[k] * wt; gr += d[k + 1] * wt; b += d[k + 2] * wt; w += wt;
      }
      r /= w * 255; gr /= w * 255; b /= w * 255;
      const mx = Math.max(r, gr, b), mn = Math.min(r, gr, b), l = (mx + mn) / 2;
      if (mx === mn) return null;   // серый кадр — цвет по умолчанию
      const dd = mx - mn, sat = l > 0.5 ? dd / (2 - mx - mn) : dd / (mx + mn);
      let hue = mx === r ? (gr - b) / dd + (gr < b ? 6 : 0) : mx === gr ? (b - r) / dd + 2 : (r - gr) / dd + 4;
      return { h: Math.round(hue * 60), s: Math.round(Math.max(0.6, sat) * 100) };
    } catch (e) { return null; }    // кадр с чужого адреса — цвет по умолчанию
  }

  // откуда открыли: последний нажатый снимок — к нему просмотр «вырастает» и возвращается
  let mvTap = null;
  document.addEventListener('pointerdown', e => {
    const el = e.target && e.target.closest && e.target.closest('img, video, .file-tile, .media-file-item');
    mvTap = el ? { el: el.matches('img, video') ? el : (el.querySelector('img, video') || el), at: Date.now() } : null;
  }, true);

  function viewer(list, index, opts) {
    list = list || []; if (!list.length) return;
    opts = opts || {};
    const actions = opts.actions || {};
    const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const touch = Kov.UI.touchUi();
    let i = Math.max(0, Math.min(list.length - 1, index | 0));
    let media = null, closed = false, idleTimer = 0;
    const origin = opts.from || (mvTap && Date.now() - mvTap.at < 1500 ? mvTap.el : null);

    const amb = [h('img.mv-amb', { alt: '' }), h('img.mv-amb', { alt: '' })];
    let ambOn = 0;
    const stage = h('div.mv-stage');
    const who = h('div.mv-who');
    const counter = h('div.mv-count');
    const caption = h('div.mv-caption');
    const strip = h('div.mv-strip');
    const info = h('div.mv-info');
    const vctl = h('div.mv-vctl');
    const cur = () => list[i];
    const doDownload = () => {
      const f = cur(); const href = f.download || f.src || url(f, 'download');
      if (Kov.Downloads) return Kov.Downloads.download(href, fileName(f));
      const a = h('a', { href, download: fileName(f) }); document.body.append(a); a.click(); a.remove();
    };
    const acts = [];
    if (actions.reply) acts.push(['reply', 'Ответить', () => { close(); actions.reply(cur()); }]);
    if (actions.forward) acts.push(['fwd', 'Переслать', () => { close(); actions.forward(cur()); }]);
    acts.push(['down', 'Скачать', doDownload]);
    if (actions.remove) acts.push(['trash', 'Удалить', () => { close(); actions.remove(cur()); }, 'del']);
    const actRow = h('div.mv-acts', acts.map(([n, label, fn, cls]) => {
      const b = mvBtn(n, label, fn, cls ? 'mv-act.' + cls : 'mv-act'); b.append(h('span', label)); return b;
    }));
    const tools = h('div.mv-tools',
      mvBtn('info', 'Сведения (I)', () => toggleInfo()),
      ...acts.map(([n, label, fn, cls]) => mvBtn(n, label, fn, cls)),
      mvBtn('close', 'Закрыть (Esc)', () => close()));
    const nav = list.length > 1 && !touch
      ? [mvBtn('left', 'Предыдущий', () => show(i - 1), 'mv-nav.prev'), mvBtn('right', 'Следующий', () => show(i + 1), 'mv-nav.next')] : [];
    const root = h('div.viewer-root.mv-root' + (touch ? '.mv-touch' : '.mv-desk'), { role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Просмотр' },
      ...amb, h('div.mv-dim'),
      h('div.mv-top', mvBtn('back', 'Закрыть', () => close(), 'mv-back'), who, counter, tools),
      stage, caption, ...nav, vctl, strip, actRow, info);
    // сведения о файле открываются только кнопкой «Сведения» (или клавишей I), сами — никогда
    function toggleInfo() {
      root.classList.toggle('mv-info-on');
    }

    // подсветка: размытая копия кадра; при смене — плавно перетекает в новую
    function setAmbient(src, el) {
      const next = amb[1 - ambOn];
      next.onload = () => { next.classList.add('on'); amb[ambOn].classList.remove('on'); ambOn = 1 - ambOn; };
      if (src) next.src = src;
      const hue = el ? frameHue(el) : null;
      if (hue) {
        root.style.setProperty('--mv-a', 'hsl(' + hue.h + ' ' + hue.s + '% 62%)');
        root.style.setProperty('--mv-b', 'hsl(' + ((hue.h + 38) % 360) + ' ' + hue.s + '% 68%)');
      }
    }
    function thumbOf(f) {
      if (f.thumb) return f.thumb;
      if (!f.src) return url(f, 'thumb');   // файлы задач, этапов и проектов: миниатюру и обложку видео отдаёт сервер
      if (!isVideo(fileName(f))) return f.src;
      const s = f.src;
      return /^(blob|data):/.test(s) ? '' : (s.split('?')[0].replace(/\.gz$/, '') + '.thumb.jpg' + (s.includes('?') ? '?' + s.split('?')[1] : ''));
    }
    const when = at => {
      const d = at ? new Date(at) : null;
      if (!d || isNaN(d)) return '';
      const t = d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
      const today = new Date().toDateString() === d.toDateString();
      return (today ? 'сегодня' : d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' })) + ' в ' + t;
    };

    // лента строится один раз (у длинных — окно ±60, пересборка только у края); при листании меняется лишь выделение
    let stripFrom = -1, stripTo = -1;
    function fillStrip() {
      if (list.length < 2) { Kov.UI.clear(strip); return; }
      if (stripFrom < 0 || (i < stripFrom + 5 && stripFrom > 0) || (i > stripTo - 6 && stripTo < list.length)) {
        stripFrom = Math.max(0, i - 60); stripTo = Math.min(list.length, i + 61);
        Kov.UI.clear(strip);
        for (let k = stripFrom; k < stripTo; k++) {
          const f = list[k], t = thumbOf(f);
          const b = h('button.mv-thumb', { type: 'button', 'data-k': String(k), 'aria-label': 'Снимок ' + (k + 1), onclick: e => { e.stopPropagation(); show(k); } },
            t ? h('img', { src: t, alt: '', loading: 'lazy', decoding: 'async', onerror: e => e.target.replaceWith(h('span.mv-thumb-v')) }) : h('span.mv-thumb-v'));
          if (f.micro) b.style.backgroundImage = 'url(data:image/jpeg;base64,' + f.micro + ')';
          strip.append(b);
        }
      }
      strip.querySelectorAll('.mv-thumb.on').forEach(t => t.classList.remove('on'));
      const on = strip.querySelector('.mv-thumb[data-k="' + i + '"]');
      if (!on) return;
      on.classList.add('on');
      // прокручивается только лента — не весь просмотр (иначе у крайних снимков он «съезжал» вбок)
      const left = Math.max(0, on.offsetLeft - (strip.clientWidth - on.offsetWidth) / 2);
      if (strip.scrollTo) strip.scrollTo({ left, behavior: reduce ? 'auto' : 'smooth' }); else strip.scrollLeft = left;
      root.scrollLeft = 0; root.scrollTop = 0;
    }

    // приближение фото: щипок, перетаскивание и двойное касание — на телефоне; колесо, перетаскивание и двойной щелчок — мышью
    let zoom = null;
    function attachZoom(img) {
      let s = 1, tx = 0, ty = 0, pinch = null, pan = null, drag = null, lastTap = 0, tapTimer = 0, moved = false;
      const box = () => { const r = stage.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2, w: r.width, h: r.height }; };
      const apply = anim => {
        img.style.transition = anim ? 'transform .22s ease' : 'none';
        img.style.transform = s > 1.001 ? 'translate(' + tx + 'px,' + ty + 'px) scale(' + s + ')' : '';
        root.classList.toggle('mv-zoomed', s > 1.001);
      };
      const clamp = () => {
        const c = box(), mx = Math.max(0, (img.offsetWidth * s - c.w) / 2), my = Math.max(0, (img.offsetHeight * s - c.h) / 2);
        tx = Math.max(-mx, Math.min(mx, tx)); ty = Math.max(-my, Math.min(my, ty));
      };
      const zoomAt = (ns, x, y, anim) => {
        const c = box(); ns = Math.max(1, Math.min(6, ns));
        const px = x - c.x, py = y - c.y, k = ns / s;
        tx = px - (px - tx) * k; ty = py - (py - ty) * k; s = ns;
        if (s <= 1.001) { s = 1; tx = ty = 0; }
        clamp(); apply(anim);
      };
      const toggle = (x, y) => zoomAt(s > 1.001 ? 1 : 2.5, x, y, true);
      const dist = (a, b) => Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
      const ts = e => {
        if (e.touches.length === 2) {
          const [a, b] = e.touches;
          pinch = { d: dist(a, b) || 1, s, x: (a.clientX + b.clientX) / 2, y: (a.clientY + b.clientY) / 2, tx, ty }; pan = null; moved = true;
        } else if (e.touches.length === 1) {
          moved = false;
          pan = s > 1.001 ? { x: e.touches[0].clientX, y: e.touches[0].clientY, tx, ty } : null;
        }
      };
      const tm = e => {
        if (pinch && e.touches.length === 2) {
          e.preventDefault();
          const [a, b] = e.touches, mx = (a.clientX + b.clientX) / 2, my = (a.clientY + b.clientY) / 2;
          s = pinch.s; tx = pinch.tx + (mx - pinch.x); ty = pinch.ty + (my - pinch.y);
          zoomAt(pinch.s * dist(a, b) / pinch.d, mx, my, false);
        } else if (pan && e.touches.length === 1) {
          e.preventDefault(); moved = true;
          tx = pan.tx + e.touches[0].clientX - pan.x; ty = pan.ty + e.touches[0].clientY - pan.y; clamp(); apply(false);
        } else if (e.touches.length === 1) moved = true;
      };
      const te = e => {
        if (pinch && e.touches.length < 2) { pinch = null; if (s < 1.05) { s = 1; tx = ty = 0; apply(true); } }
        if (e.touches.length) return;
        pan = null;
        if (moved || e.changedTouches.length !== 1 || e.target !== img) return;
        const t = e.changedTouches[0], now = Date.now();
        if (now - lastTap < 300) { clearTimeout(tapTimer); lastTap = 0; toggle(t.clientX, t.clientY); }
        else { lastTap = now; tapTimer = setTimeout(() => root.classList.toggle('mv-idle'), 300); }   // одно касание — спрятать или вернуть кнопки
      };
      const wheel = e => { e.preventDefault(); zoomAt(s * (e.deltaY < 0 ? 1.2 : 1 / 1.2), e.clientX, e.clientY, false); };
      const dbl = e => { e.preventDefault(); toggle(e.clientX, e.clientY); };
      const down = e => {
        if (e.pointerType !== 'mouse' || s <= 1.001) return;
        e.preventDefault(); drag = { x: e.clientX, y: e.clientY, tx, ty };
      };
      const move = e => { if (!drag) return; tx = drag.tx + e.clientX - drag.x; ty = drag.ty + e.clientY - drag.y; clamp(); apply(false); };
      const up = () => { drag = null; };
      img.draggable = false;
      stage.addEventListener('touchstart', ts, { passive: true });
      stage.addEventListener('touchmove', tm, { passive: false });
      stage.addEventListener('touchend', te); stage.addEventListener('touchcancel', te);
      if (!touch) {
        stage.addEventListener('wheel', wheel, { passive: false });
        img.addEventListener('dblclick', dbl); img.addEventListener('pointerdown', down);
        window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
      }
      return {
        zoomed: () => s > 1.001 || !!pinch,
        dispose: () => {
          clearTimeout(tapTimer);
          stage.removeEventListener('touchstart', ts); stage.removeEventListener('touchmove', tm);
          stage.removeEventListener('touchend', te); stage.removeEventListener('touchcancel', te);
          stage.removeEventListener('wheel', wheel); window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up);
          root.classList.remove('mv-zoomed');
        },
      };
    }

    function show(next) {
      if (videoState) videoState.dispose();
      if (zoom) { zoom.dispose(); zoom = null; }
      i = (next + list.length) % list.length;
      const f = list[i];
      const shown = i;
      const cap = /^(null|none|undefined)?$/i.test(String(f.caption == null ? '' : f.caption).trim()) ? '' : String(f.caption);   // подпись к файлу
      const at = f.at || f.created_at;
      Kov.UI.clear(stage); Kov.UI.clear(vctl);
      const video = isVideo(fileName(f));
      root.classList.toggle('mv-is-video', video);
      const build = () => (f.src ? [f.src] : [url(f, video ? 'stream' : 'view'), url(f, 'download')]);
      let sources = build(), step = 0, refreshed = false;
      media = video
        ? h('video.mv-media', { src: sources[0], autoplay: true, playsInline: true, preload: 'auto' })
        : h('img.mv-media', { src: sources[0], alt: fileName(f), decoding: 'async' });
      const onFail = async () => {
        if (!refreshed && !f.src) {
          refreshed = true;
          await Kov.Api.ensureFileToken(true);
          if (shown !== i || !media.isConnected) return;
          sources = build(); media.src = sources[step];
          return;
        }
        step += 1;
        if (step < sources.length) { media.src = sources[step]; return; }
        Kov.UI.clear(stage);
        stage.append(h('div.viewer-error', h('div', 'Файл не открылся. Попробуйте скачать его.'),
          h('div.small', { style: { opacity: .7, wordBreak: 'break-all' } }, String(sources[0]).replace(/([?&]token=)[^&]*/, '$1…'))));
      };
      media.addEventListener('error', onFail);
      stage.append(media);
      if (video) {
        const poster = thumbOf(f);
        if (poster) setAmbient(poster, null);
        media.addEventListener('loadeddata', () => { if (shown === i) setAmbient(null, media); }, { once: true });
        videoState = videoControls(media);
      } else {
        media.addEventListener('load', () => { if (shown === i) setAmbient(media.currentSrc || media.src, media); }, { once: true });
        zoom = attachZoom(media);
      }
      Kov.UI.clear(who);
      const name = f.who || uploaderName(f) || '';
      who.append(h('b', name || safeName(fileName(f))), h('span', when(at) || (name ? safeName(fileName(f)) : '')));
      counter.textContent = list.length > 1 ? (i + 1) + ' из ' + list.length : '';
      counter.hidden = list.length < 2;
      caption.textContent = cap;
      caption.hidden = !cap;
      root.classList.toggle('mv-has-cap', !!cap && !video);
      // «Удалить» — только у своего (решает тот, кто открыл просмотр)
      root.querySelectorAll('.mv-act.del, .mv-tools .del').forEach(b => { b.hidden = !!(actions.canRemove && !actions.canRemove(f)); });
      root.classList.toggle('mv-has-strip', list.length > 1 && !(video && touch));
      Kov.UI.clear(info);
      // пустые строки не выводим: встроенный append печатает null словом «null»
      info.append(...[h('b', name || 'Файл'), when(at) ? h('small', when(at)) : null,
        cap ? h('p', cap) : null, h('p.mv-file', safeName(fileName(f))),
        h('small.mv-dims', f.size ? fmtSize(f.size) : '')].filter(Boolean));
      if (!video) media.addEventListener('load', () => {
        const d = info.querySelector('.mv-dims');
        if (d) d.textContent = media.naturalWidth + ' × ' + media.naturalHeight + (f.size ? ', ' + fmtSize(f.size) : '');
      }, { once: true });
      fillStrip();
      wake();
    }

    // ── видео: кольцо хода, шкала в цвет кадра, превью кадра, ±10 с двойным касанием
    let videoState = null;
    function videoControls(v) {
      const play = h('button.mv-play', { type: 'button', 'aria-label': 'Пауза', onclick: e => { e.stopPropagation(); toggle(); } }, h('i'));
      play.append(mvIcon('pause', 30));
      const buf = h('div.mv-buf'), fill = h('div.mv-fill'), knob = h('div.mv-knob');
      const prevImg = h('canvas.mv-pv-img', { width: 160, height: 90 });
      const prevTime = h('span');
      const preview = h('div.mv-pv', prevImg, prevTime);
      const scrub = h('div.mv-scrub', { role: 'slider', 'aria-label': 'Перемотка', tabindex: '0' }, buf, fill, knob);
      const tNow = h('span'), tLeft = h('span');
      const speeds = [1, 1.5, 2, 0.5];
      const speedBtn = h('button.mv-chip', { type: 'button', 'aria-label': 'Скорость', onclick: e => {
        e.stopPropagation(); const k = (speeds.indexOf(v.playbackRate) + 1) % speeds.length; v.playbackRate = speeds[k];
        speedBtn.textContent = String(speeds[k]).replace('.', ',') + '×'; wake();
      } }, '1×');
      const muteBtn = mvBtn('vol', 'Звук (M)', () => { v.muted = !v.muted; syncMute(); wake(); }, 'mv-ic');
      const syncMute = () => { Kov.UI.clear(muteBtn); muteBtn.append(mvIcon(v.muted ? 'mute' : 'vol')); };
      const canFull = !!(document.fullscreenEnabled && root.requestFullscreen);
      const fullBtn = canFull ? mvBtn('full', 'Во весь экран (F)', () => fullscreen(), 'mv-ic') : null;
      const ripple = h('div.mv-ripple');
      stage.append(play, ripple);   // по центру самого видео: область кадра следует за сведениями и полным экраном
      vctl.append(preview, scrub, h('div.mv-times', tNow, tLeft), h('div.mv-ctl', muteBtn, speedBtn, h('span', { style: { flex: 1 } }), fullBtn));
      const fmt = s => { s = Math.max(0, Math.floor(s || 0)); return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); };
      const sync = () => {
        const d = isFinite(v.duration) ? v.duration : 0, p = d ? v.currentTime / d : 0;
        fill.style.width = (p * 100) + '%'; knob.style.left = (p * 100) + '%';
        play.style.setProperty('--p', (p * 100) + '%');
        tNow.textContent = fmt(v.currentTime); tLeft.textContent = d ? '−' + fmt(d - v.currentTime) : '';
        try { if (v.buffered.length && d) buf.style.width = (v.buffered.end(v.buffered.length - 1) / d * 100) + '%'; } catch (e) { /* */ }
      };
      const syncPlay = () => {
        Kov.UI.clear(play); play.append(h('i'), mvIcon(v.paused ? 'play' : 'pause', 30));
        play.setAttribute('aria-label', v.paused ? 'Смотреть' : 'Пауза');
        root.classList.toggle('mv-paused', v.paused);
        wake();
      };
      function toggle() { if (v.paused) v.play().catch(() => {}); else v.pause(); }
      const seekTo = t => { if (v.duration) v.currentTime = Math.max(0, Math.min(v.duration - 0.05, t)); sync(); };
      // превью кадра: отдельная тихая копия видео, только пока тянут шкалу
      let pv = null, pvBusy = false, pvWant = null;
      const drawPreview = t => {
        pvWant = t;
        if (!pv) {
          pv = h('video', { src: v.currentSrc || v.src, muted: true, preload: 'auto', playsInline: true });
          pv.addEventListener('seeked', () => {
            try { prevImg.getContext('2d').drawImage(pv, 0, 0, 160, 90); } catch (e) { /* */ }
            pvBusy = false; if (pvWant !== null && Math.abs(pvWant - pv.currentTime) > 0.3) drawPreview(pvWant);
          });
        }
        if (pvBusy) return;
        pvBusy = true;
        try { pv.currentTime = t; } catch (e) { pvBusy = false; }
      };
      let dragging = false;
      const at = e => { const r = scrub.getBoundingClientRect(); return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)); };
      const showPreview = p => {
        preview.classList.add('on');
        preview.style.left = 'calc(18px + (100% - 36px) * ' + p + ' - 52px)';
        prevTime.textContent = fmt(p * (v.duration || 0));
        drawPreview(p * (v.duration || 0));
      };
      scrub.addEventListener('pointerdown', e => {
        e.stopPropagation(); dragging = true;
        const p = at(e); seekTo(p * (v.duration || 0)); showPreview(p); wake();
        try { scrub.setPointerCapture(e.pointerId); } catch (err) { /* захват не обязателен: перемотка уже сделана */ }
      });
      scrub.addEventListener('pointermove', e => { if (!dragging) return; const p = at(e); showPreview(p); seekTo(p * (v.duration || 0)); });
      const endDrag = () => { dragging = false; preview.classList.remove('on'); wake(); };
      scrub.addEventListener('pointerup', endDrag); scrub.addEventListener('pointercancel', endDrag);
      scrub.addEventListener('click', e => e.stopPropagation());
      scrub.addEventListener('keydown', e => {
        if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault(); e.stopPropagation(); seekTo(v.currentTime + (e.key === 'ArrowLeft' ? -5 : 5)); }
      });
      // двойное касание: у краёв — ±10 с, по центру — пауза; одиночное — показать или спрятать управление
      let lastTap = 0, tapTimer = 0;
      const jump = (sec, side) => {
        seekTo(v.currentTime + sec);
        ripple.textContent = (sec > 0 ? '+' : '−') + Math.abs(sec) + ' с';
        ripple.className = 'mv-ripple on ' + side;
        clearTimeout(ripple._t); ripple._t = setTimeout(() => { ripple.className = 'mv-ripple'; }, 650);
      };
      const onStageTap = e => {
        if (e.target.closest('.mv-vctl button, .mv-scrub, .mv-play')) return;
        const now = Date.now(), r = stage.getBoundingClientRect(), x = (e.clientX - r.left) / r.width;
        if (now - lastTap < 300) {
          clearTimeout(tapTimer); lastTap = 0;
          if (x < 0.33) jump(-10, 'l'); else if (x > 0.67) jump(10, 'r'); else toggle();
          return;
        }
        lastTap = now;
        tapTimer = setTimeout(() => { root.classList.toggle('mv-idle'); if (!root.classList.contains('mv-idle')) wake(); }, 300);
      };
      stage.addEventListener('click', onStageTap);
      const evs = { timeupdate: sync, progress: sync, loadedmetadata: sync, durationchange: sync, play: syncPlay, pause: syncPlay, ended: () => { syncPlay(); root.classList.remove('mv-idle'); } };
      Object.keys(evs).forEach(k => v.addEventListener(k, evs[k]));
      syncPlay(); sync();
      return {
        toggle, seekBy: s => seekTo(v.currentTime + s), mute: () => { v.muted = !v.muted; syncMute(); },
        dispose: () => { stage.removeEventListener('click', onStageTap); try { v.pause(); } catch (e) { /* */ } if (pv) { pv.removeAttribute('src'); pv.load(); } videoState = null; },
      };
    }
    function fullscreen() {
      if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
      else if (root.requestFullscreen) root.requestFullscreen({ navigationUI: 'hide' }).catch(() => {});
    }
    const onFullscreen = () => { root.classList.toggle('mv-full', document.fullscreenElement === root); root.scrollLeft = 0; wake(); };
    document.addEventListener('fullscreenchange', onFullscreen);
    // управление прячется во время просмотра видео и проявляется при касании
    function wake() {
      root.classList.remove('mv-idle'); clearTimeout(idleTimer);
      if (media && media.tagName === 'VIDEO' && !media.paused) idleTimer = setTimeout(() => { if (!closed) root.classList.add('mv-idle'); }, 2600);
    }
    root.addEventListener('pointermove', () => { if (!touch) wake(); });

    function onKey(e) {
      if (e.target && /^(INPUT|TEXTAREA)$/.test(e.target.tagName)) return;
      if (e.key === 'Escape') { if (document.fullscreenElement) document.exitFullscreen().catch(() => {}); else close(); }
      else if (e.key === 'ArrowLeft' && list.length > 1) show(i - 1);
      else if (e.key === 'ArrowRight' && list.length > 1) show(i + 1);
      else if (e.key === ' ' && videoState) { e.preventDefault(); videoState.toggle(); }
      else if ((e.key === 'm' || e.key === 'ь') && videoState) videoState.mute();
      else if ((e.key === 'f' || e.key === 'а') && videoState) fullscreen();
      else if ((e.key === 'i' || e.key === 'ш') && !touch) toggleInfo();
      else return;
      wake();
    }

    // жесты: в сторону — листать, вниз — закрыть (кадр уменьшается, фон тает)
    {
      let x0 = null, y0 = 0, dx = 0, dy = 0, dir = null;
      const reset = () => { x0 = null; stage.style.transition = 'transform .22s ease'; stage.style.transform = ''; root.style.setProperty('--mv-fade', '1'); };
      stage.addEventListener('touchstart', e => {
        if (e.touches.length !== 1 || (zoom && zoom.zoomed())) { x0 = null; return; }   // приближенное фото — двигается, а не листается
        x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; dx = dy = 0; dir = null;
        stage.style.transition = 'none';
      }, { passive: true });
      stage.addEventListener('touchmove', e => {
        if (x0 === null || e.touches.length !== 1) return;
        dx = e.touches[0].clientX - x0; dy = e.touches[0].clientY - y0;
        if (dir === null && (Math.abs(dx) > 8 || Math.abs(dy) > 8)) dir = Math.abs(dx) > Math.abs(dy) ? 'x' : (dy > 0 ? 'down' : 'up');
        if (dir === 'x' && list.length > 1) { e.preventDefault(); stage.style.transform = 'translateX(' + dx + 'px)'; }
        if (dir === 'down') {
          e.preventDefault();
          stage.style.transform = 'translateY(' + dy + 'px) scale(' + Math.max(0.7, 1 - dy / 1400) + ')';
          root.style.setProperty('--mv-fade', String(Math.max(0.25, 1 - dy / 500)));
        }
      }, { passive: false });
      stage.addEventListener('touchcancel', reset);
      stage.addEventListener('touchend', () => {
        if (x0 === null) return;
        const w = stage.clientWidth || window.innerWidth;
        if (dir === 'down') { if (dy > 110) { x0 = null; close(); } else reset(); return; }
        const go = dir === 'x' && list.length > 1 && Math.abs(dx) > Math.max(60, w * 0.18) ? (dx < 0 ? 1 : -1) : 0;
        if (!go) return reset();
        x0 = null;
        stage.style.transition = 'transform .18s ease';
        stage.style.transform = 'translateX(' + (-go * w) + 'px)';
        setTimeout(() => {
          stage.style.transition = 'none';
          stage.style.transform = 'translateX(' + (go * w * 0.3) + 'px)';
          show(i + go);
          requestAnimationFrame(() => requestAnimationFrame(() => { stage.style.transition = 'transform .22s ease'; stage.style.transform = ''; }));
        }, 180);
      });
    }

    // открытие: кадр вырастает из нажатой плитки; закрытие — возвращается в неё
    const originRect = () => {
      if (!origin || !origin.isConnected) return null;
      const r = origin.getBoundingClientRect();
      return r.width > 4 && r.bottom > 0 && r.top < window.innerHeight ? r : null;
    };
    const flyFrom = r => {
      const s = stage.getBoundingClientRect();
      const k = Math.max(0.05, Math.min(r.width / s.width, r.height / s.height));
      return 'translate(' + (r.left + r.width / 2 - (s.left + s.width / 2)) + 'px,' + (r.top + r.height / 2 - (s.top + s.height / 2)) + 'px) scale(' + k + ')';
    };
    function close() {
      if (closed) return;
      closed = true;
      clearTimeout(idleTimer);
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('fullscreenchange', onFullscreen);
      if (document.fullscreenElement === root) document.exitFullscreen().catch(() => {});
      if (videoState) videoState.dispose();
      if (zoom) zoom.dispose();
      const r = !reduce && originRect();
      const done = () => root.remove();
      if (reduce) return done();
      root.classList.add('mv-closing');
      stage.style.transition = 'transform .26s cubic-bezier(.4,0,.2,1), opacity .26s ease';
      stage.style.transform = r ? flyFrom(r) : 'scale(.92)';
      if (!r) stage.style.opacity = '0';
      setTimeout(done, 270);
    }
    root.addEventListener('click', e => { if (e.target === root || (e.target === stage && !videoState)) close(); });
    document.addEventListener('keydown', onKey);
    document.body.append(root); Kov.UI.raise(root);
    show(i);
    if (!reduce) {
      const r = originRect();
      root.classList.add('mv-opening');
      if (r) { stage.style.transition = 'none'; stage.style.transform = flyFrom(r); }
      requestAnimationFrame(() => requestAnimationFrame(() => {
        stage.style.transition = 'transform .3s cubic-bezier(.2,.8,.2,1)'; stage.style.transform = '';
        root.classList.remove('mv-opening');
      }));
    }
    const first = root.querySelector('.mv-back');
    if (first && !touch) first.focus({ preventScroll: true });
    return { close, show: k => show(k) };
  }

  // ── загрузка с прогрессом ───────────────────────────────────────────────

  /**
   * Одна полоса на всю отправку, без разделения на «отправку» и «архивацию»:
   * пользователю важно, что дело движется, а не какой сейчас этап. Диапазоны
   * подобраны так, чтобы у самой долгой части — обработки на сервере — был
   * самый широкий кусок шкалы, и полоса не замирала на 98%.
   *
   *   0 … SEND   — байты уходят на сервер (настоящий прогресс XHR)
   *   SEND … TAKE — сервер принимает и разбирает пачку (пока не отвечает)
   *   TAKE … 0.99 — обработка файлов (опрос /upload/progress)
   */
  const SEND_END = 0.45;
  const TAKE_END = 0.58;
  const CAP = 0.99;

  /** Грубая оценка длительности, сек: нужна только для плавности полосы */
  function processTau(files) {
    const mb = files.reduce((sum, f) => sum + f.size, 0) / 1048576;
    if (files.some(f => isVideo(f.name))) return 5 + mb / 3;
    if (files.some(f => isImage(f.name))) return 0.8 + mb / 15;
    return 0.5 + mb / 30;
  }

  /**
   * upload(fileList, path, fields, onProgress(0..1, label, detail)) → Promise(json)
   *
   * Процент собирается из двух настоящих источников: сетевая отправка — по
   * событиям XHR, обработка на сервере — по опросу /upload/progress: сервер
   * отдаёт состояние каждого файла (в очереди, сжатие, готово). Доля сети
   * зависит от типа файлов: у видео обработка занимает большую часть времени.
   * detail — снимок по файлам, его показывают пофайловые индикаторы.
   */
  let formSignal = null;   // отмена последнего запроса отправки — только своего, а не всех загрузок
  function sendForm(fileList, path, fields, onProgress, thumbs, names) {
    return new Promise((resolve, reject) => {
      const files = [...fileList];
      const oversize = files.filter(f => f.size > MAX_UPLOAD);
      if (oversize.length) return reject(new Error('Файл больше 2 ГБ: ' + oversize[0].name));
      const uploadId = 'u' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
      const form = new FormData();
      files.forEach(f => form.append('files', f, f.name));
      (thumbs || []).forEach((t, i) => form.append('thumbs', t, 'thumb' + i + '.jpg'));
      form.append('upload_id', uploadId);
      Object.entries(fields || {}).forEach(([k, v]) => { if (v !== null && v !== undefined) form.append(k, String(v)); });

      const total = names ? names.length : files.length;
      let timer = null;
      let lastDetail = null;
      let shown = 0;                    // назад полоса не откатывается
      const report = (p, text, detail) => {
        shown = Math.max(shown, Math.max(0, Math.min(1, p)));
        onProgress && onProgress(shown, text, detail || lastDetail);
      };
      /** Единая подпись: сколько файлов готово и общий процент */
      const say = (p, doneCount) => {
        const pct = Math.round(p * 100) + '%';
        if (total > 1) return 'Загрузка · ' + Math.min(doneCount + 1, total) + ' из ' + total + ' · ' + pct;
        return 'Загрузка · ' + pct;
      };

      // Ход обработки на сервере: раз в 400 мс спрашиваем, что с каждым файлом
      async function poll() {
        try {
          const snap = await Kov.Api.get('upload/progress/' + uploadId);
          if (!snap || !snap.files) return;
          lastDetail = snap;
          const p = TAKE_END + (CAP - TAKE_END) * (Number(snap.percent || 0) / 100);
          const doneCount = snap.files.filter(f => f.state === 'done' || f.state === 'error').length;
          report(p, say(p, doneCount), snap);
        } catch (e) { /* сервер ещё разбирает пачку — идём по оценке времени */ }
      }
      // Опрос идёт с самого начала: пока сервер принимает многочастный запрос,
      // ответа по прогрессу ещё нет, и полосу ведёт мягкая оценка по времени —
      // но она упирается в TAKE_END, а не в самый конец шкалы
      let uploadDone = false, began = Date.now();
      function startPolling() {
        clearInterval(timer);
        const tau = processTau(files);
        timer = setInterval(() => {
          if (lastDetail) return poll();
          if (!uploadDone) return poll();
          const elapsed = (Date.now() - began) / 1000;
          const p = SEND_END + (TAKE_END - SEND_END) * (1 - Math.exp(-elapsed / Math.max(1, tau / 4)));
          report(p, say(p, 0));
          poll();
        }, 400);
      }
      function startProcessing() { uploadDone = true; began = Date.now(); }
      const forget = () => { const i = upload.current.indexOf(xhr); if (i >= 0) upload.current.splice(i, 1); };
      const stop = () => { clearInterval(timer); forget(); };

      const xhr = new XMLHttpRequest();
    const sig = formSignal; formSignal = null;
    if (sig) {
      if (sig.aborted) setTimeout(() => { try { xhr.abort(); } catch (e) { /* */ } }, 0);
      else sig.addEventListener('abort', () => { try { xhr.abort(); } catch (e) { /* */ } }, { once: true });
    }
      upload.current.push(xhr);
      xhr.open('POST', Kov.Api.url(path));
      xhr.setRequestHeader('Authorization', 'Bearer ' + Kov.Api.state.token);
      let sentBytes = 0, totalBytes = 0;
      xhr.upload.onprogress = e => {
        sentBytes = e.loaded; totalBytes = e.total;
        if (!e.lengthComputable || lastDetail) return;
        const p = (e.loaded / e.total) * SEND_END;
        // по объёму понятно, какой файл сейчас уходит
        let sent = e.loaded, detail = { files: (names || files.map(f => f.name)).map((n, i) => ({ index: i, name: n, state: 'receiving', percent: names ? Math.round(e.loaded / e.total * 100) : 0 })) };
        let doneCount = 0;
        if (!names) for (const f of detail.files) {
          const size = files[f.index].size || 1;
          if (sent >= size) { f.percent = 100; f.state = 'received'; sent -= size; doneCount++; }
          else { f.percent = Math.round(sent / size * 100); sent = 0; }
        }
        report(p, say(p, doneCount), detail);
      };
      xhr.upload.onload = () => startProcessing();
      xhr.onload = () => {
        stop();
        if (xhr.status >= 200 && xhr.status < 300) {
          let data = null; try { data = JSON.parse(xhr.responseText); } catch (e) { /* */ }
          // Сервер ответил — значит всё сохранено. Доводим полосу ровно до
          // ста процентов: раньше она обрывалась там, где успела дойти
          const done = { files: (names || files.map(f => f.name)).map((n, i) => ({ index: i, name: n, state: 'done', percent: 100 })), percent: 100 };
          lastDetail = done;
          shown = 1;
          onProgress && onProgress(1, 'Готово', done);
          resolve(data);
        } else {
          let detail = 'HTTP ' + xhr.status;
          try { const d = JSON.parse(xhr.responseText); detail = typeof d.detail === 'string' ? d.detail : detail; } catch (e) { /* */ }
          const err = new Error(detail); err.status = xhr.status;
          reject(err);
        }
      };
      xhr.onerror = () => {
        // подробности для журнала телефона (консоль страницы → KovappJS): где именно оборвалось
        console.error('Отправка файлов оборвалась: ' + JSON.stringify({ path, uploadDone, readyState: xhr.readyState, status: xhr.status,
          sent: sentBytes, total: totalBytes, serverPercent: lastDetail ? lastDetail.percent : null,
          serverFiles: lastDetail && lastDetail.files ? lastDetail.files.map(f => f.state + ':' + f.percent) : null }));
        stop(); reject(Object.assign(new Error('Связь с сервером потеряна — файл не отправлен'), { network: true }));
      };
      xhr.ontimeout = () => { stop(); reject(Object.assign(new Error('Сервер не ответил вовремя'), { network: true })); };
      xhr.onabort = () => { stop(); reject(Object.assign(new Error('Загрузка отменена'), { aborted: true })); };
      xhr.send(form);
      report(0, 'Отправка…');
      startPolling();
      if (typeof onProgress === 'function' && onProgress.holder) onProgress.holder.xhr = xhr;
    });
  }
  const OFFLINE_GRACE_MS = 2 * 60 * 1000;
  const activeJobs = new Set();

  function waitNetwork(signal, until) {
    return new Promise(resolve => {
      const check = () => {
        if (signal && signal.aborted) return resolve(false);
        if (navigator.onLine !== false) return resolve(true);
        if (Date.now() >= until) return resolve(false);
        setTimeout(check, 1000);
      };
      check();
    });
  }

  // повтор прикрепления только если сервер ещё не забрал куски: иначе будет дубль
  async function attachSessions(prepared, path, extra, onProgress, signal) {
    const sessions = prepared.meta.filter(m => m.src === 'session').map(m => m.session);
    const safe = sessions.length && !prepared.files.length;
    const until = Date.now() + OFFLINE_GRACE_MS;
    for (;;) {
      try {
        formSignal = signal || null;
        return await sendForm(prepared.files, path, extra, onProgress, prepared.thumbs, prepared.meta.map(m => m.name));
      } catch (e) {
        if (!safe || !e || !e.network || (signal && signal.aborted) || Date.now() >= until) throw e;
        if (!await waitNetwork(signal, until)) throw e;
        await new Promise(r => setTimeout(r, 2000));
        let taken = false;
        for (const sid of sessions) {
          try {
            const res = await fetch(Kov.Api.url('uploads/sessions/' + sid), { headers: { Authorization: 'Bearer ' + Kov.Api.state.token } });
            if (res.status === 404) taken = true;
          } catch (x) { /* снова нет связи — решим на следующем круге */ }
        }
        if (taken) return [];
      }
    }
  }

  // ── Отправка с подготовкой на устройстве: сжатие, миниатюры, копии, докачка ──
  /** Копия файла в памяти: выбранный на телефоне файл — ссылка на исходник, и встроенный браузер Android
   *  иногда не может прочитать его при отправке (запрос обрывается до первого байта) */
  /** PDF, Word и PowerPoint показывает само устройство (сервер — запасной путь) */
  function isDoc(name) { return DOC_RE.test(String(name || '')); }
  function openDocument(downloadUrl, name, viewUrl) {
    const page = DOC_PAGE + '?u=' + encodeURIComponent(downloadUrl) + '&n=' + encodeURIComponent(name) + '&v=' + encodeURIComponent(viewUrl || downloadUrl);
    const a = h('a', { href: page, target: '_blank', rel: 'noopener' });
    document.body.append(a); a.click(); a.remove();
  }

  async function inMemory(f) {
    if (!f || typeof f.arrayBuffer !== 'function') return f;
    try {
      const buf = await f.arrayBuffer();
      return new File([buf], f.name || 'file', { type: f.type || '', lastModified: f.lastModified || Date.now() });
    } catch (e) {
      throw Object.assign(new Error('Файл «' + (f.name || 'файл') + '» не читается — выберите его заново'), { unreadable: true });
    }
  }

  // ── общий ход отправок — для шторки уведомлений в приложении (служба Android держит отправку в фоне);
  //    надстройка приложения ставит onTransfer и по «Отменить» из шторки вызывает cancelTransfers
  const transfers = new Map();
  let transferSeq = 0, pushTimer = null, lastPush = 0;
  function pushTransfers(force) {
    const hook = api.onTransfer;
    if (typeof hook !== 'function') return;
    const send = () => {
      pushTimer = null; lastPush = Date.now();
      let count = 0, sum = 0;
      transfers.forEach(t => { count += t.count; sum += t.p * t.count; });
      // отправка в чат, пока открыт «Мессенджер» и приложение на экране, — без уведомления в шторке
      const inChat = /^#\/messenger/.test(location.hash) && document.visibilityState === 'visible';
      const quiet = inChat && [...transfers.values()].every(t => t.chat);
      try { hook(transfers.size ? { count, percent: Math.round(sum / Math.max(1, count) * 100), quiet } : null); } catch (e) { /* */ }
    };
    if (force) { clearTimeout(pushTimer); send(); return; }
    if (!pushTimer) pushTimer = setTimeout(send, Math.max(0, 700 - (Date.now() - lastPush)));
  }
  function cancelTransfers() { transfers.forEach(t => { try { t.abort(); } catch (e) { /* */ } }); }
  // сменился раздел или приложение ушло с экрана — пересчитать, нужно ли уведомление
  window.addEventListener('hashchange', () => { if (transfers.size) pushTransfers(true); });
  document.addEventListener('visibilitychange', () => { if (transfers.size) pushTransfers(true); });

  async function upload(fileList, path, fields, onProgress, signal) {
    const inner = new AbortController();
    if (signal) { if (signal.aborted) inner.abort(); else signal.addEventListener('abort', () => inner.abort(), { once: true }); }
    const id = ++transferSeq;
    transfers.set(id, { count: Math.max(1, (fileList && fileList.length) || 1), p: 0, abort: () => inner.abort(), chat: String(path || '').startsWith('chat/') });
    pushTransfers(true);
    try {
      return await uploadRun(fileList, path, fields, (p, ...rest) => {
        const t = transfers.get(id);
        if (t) { t.p = Math.max(0, Math.min(1, Number(p) || 0)); pushTransfers(); }
        if (onProgress) onProgress(p, ...rest);
      }, inner.signal);
    } finally {
      transfers.delete(id);
      pushTransfers(true);
    }
  }

  async function uploadRun(fileList, path, fields, onProgress, signal) {
    direct++;
    try { return await uploadNow(fileList, path, fields, onProgress, signal); }
    finally { direct--; setTimeout(notifyIdle, 0); }
  }

  /** Ход каждого файла — в той же шкале, что и общий: подготовка — первая доля круга, приём сервером — остальное;
   *  «готово» после подготовки ещё не конец — кольцо остаётся до конца отправки */
  function scaleDetail(detail, from, part, last) {
    if (!detail || !Array.isArray(detail.files)) return detail;
    return Object.assign({}, detail, { files: detail.files.map(f => Object.assign({}, f, {
      percent: Math.round(from * 100 + Math.max(0, Math.min(100, Number(f.percent || 0))) * part),
      state: !last && f.state === 'done' ? '' : f.state,
    })) });
  }

  async function uploadNow(fileList, path, fields, onProgress, signal) {
    const files = [...fileList];
    const report = (p, label, detail) => onProgress && onProgress(p, label, detail);
    const aborted = () => Object.assign(new Error('Загрузка отменена'), { aborted: true });
    if (signal && signal.aborted) throw aborted();
    if (!Kov.MediaPrep || !Kov.MediaPrep.enabled() || !files.length) {
      const mem = await Promise.all(files.map(f => (f.size <= 64 * 1048576 ? inMemory(f) : f)));
      formSignal = signal || null;
      return sendForm(mem, path, fields, onProgress);
    }
    let edits = null;
    try { edits = fields && fields.edits ? JSON.parse(fields.edits) : null; } catch (e) { edits = null; }
    const share = files.some(f => Kov.MediaPrep.kindOf(f.name) === 'video' || f.size > 32 * 1048576) ? 0.75
      : files.some(f => Kov.MediaPrep.kindOf(f.name) === 'image') ? 0.35 : 0.1;
    let prepared;
    try {
      prepared = await Kov.MediaPrep.prepareAll(files, { edits, signal, onProgress: (p, label, detail) => report(p * share, label, scaleDetail(detail, 0, share, false)) });
    } catch (e) {
      if ((e && (e.aborted || e.stalled)) || (signal && signal.aborted)) throw e && e.stalled ? e : aborted();
      console.warn('Подготовка на устройстве не удалась — отправляем исходные файлы', e);
      return sendForm(files, path, fields, onProgress);
    }
    if (signal && signal.aborted) { Kov.ChunkUpload && Kov.ChunkUpload.discardBySignal(prepared.signal); throw aborted(); }
    const extra = Object.assign({}, fields || {}, { meta: JSON.stringify(prepared.meta) });
    if (edits) {
      const left = {};
      Object.keys(edits).forEach(k => { const m = prepared.meta[Number(k)]; if (!m || !m.edits_applied) left[k] = edits[k]; });
      extra.edits = Object.keys(left).length ? JSON.stringify(left) : null;
    }
    const attachProgress = (p, label, detail) => report(share + p * (1 - share), label, scaleDetail(detail, share, 1 - share, true));
    attachProgress.holder = onProgress && onProgress.holder;
    try {
      const created = await attachSessions(prepared, path, extra, attachProgress, signal);
      if (Kov.ChunkUpload) Kov.ChunkUpload.forgetBySignal(prepared.signal);
      return created;
    } catch (e) {
      if (Kov.ChunkUpload) Kov.ChunkUpload.discardBySignal(prepared.signal);
      throw e;
    }
  }
  upload.current = sendForm.current = [];
  upload.scaleDetail = scaleDetail;   // для проверок: шкала хода файла
  /** Прерываем всё, что сейчас отправляется: уход из раздела, выход, обрыв связи */
  let direct = 0;   // отправки мимо плиток файлов (мессенджер зовёт upload() сам)
  function uploadsActive() { return activeJobs.size > 0 || direct > 0; }
  const idleWaiters = [];
  function whenIdle(fn) { if (!uploadsActive()) fn(); else if (!idleWaiters.includes(fn)) idleWaiters.push(fn); }
  function notifyIdle() {
    if (uploadsActive()) return;
    idleWaiters.splice(0).forEach(fn => { try { fn(); } catch (e) { console.warn(e); } });
  }
  function abortAllUploads(reason) {
    const jobs = [...activeJobs];
    jobs.forEach(j => { try { j.abort(reason); } catch (e) { /* */ } });
    if (Kov.MediaPrep) Kov.MediaPrep.abortAll();
    const list = upload.current.splice(0);
    list.forEach(x => { try { x.abort(); } catch (e) { /* */ } });
    return list.length + jobs.length;
  }

  /**
   * Блок «категории файлов»: вкладки Документы/Изображения/Видео/Другое,
   * поиск, фильтр по отправителю, сетка, загрузка (кнопка + перетаскивание).
   * cfg: { getFiles() -> file[], canUpload, canDelete(file), uploadPath, uploadFields,
   *        onChanged(), sortEarliestFirst }
   */
  const liveJobs = new Map();   // куда грузим → идущая отправка (блок файлов вернулся — подхватывает её ход)

  function categoryBlock(cfg) {
    // выбранная вкладка приходит снаружи: при добавлении файла раздел
    // пересобирается, и раньше он каждый раз сбрасывался на «Документы»
    let category = cfg.noTabs ? null : (cfg.initialCategory || 'documents');
    const jobKey = (cfg.uploadPath || 'upload') + '|' + JSON.stringify(cfg.uploadFields || {});
    let search = '';
    let uploaderId = null;
    const tabs = cfg.noTabs ? null : Kov.UI.segmented(CATEGORIES.map(([key, label, glyph]) => [key, label, glyph]), category,
      key => { category = key; cfg.onCategoryChange && cfg.onCategoryChange(key); render(); });
    const searchBox = Kov.UI.searchInput('Поиск по имени файла…', v => { search = v; render(); });
    if (tabs && Kov.UI.touchUi()) tabs.classList.add('cat-icons');   // телефон: значки без подписей, число — бейджем
    const gridEl = grid({ files: [], canDelete: cfg.canDelete, onDelete: () => cfg.onChanged && cfg.onChanged(),
      emptyText: cfg.canUpload ? 'Файлов пока нет — нажмите «Добавить файлы» или перетащите их сюда' : 'Файлов пока нет' });
    let filterSel = uploaderFilter([], v => { uploaderId = v; render(); });
    // FileList живой: сброс value его опустошает — копируем до сброса
    const input = h('input', { type: 'file', multiple: true, hidden: true, onchange: e => {
      const picked = [...(e.target.files || [])];
      e.target.value = '';   // тот же файл можно выбрать повторно
      doUpload(picked);
    } });
    // телефон: шторка вложений (камера, последние фото и видео, файлы); компьютер — обычный выбор
    const addBtn = button('Добавить файлы', { icon: 'upload', onClick: () => {
      if (Kov.SendDialog && Kov.SendDialog.isMobile() && Kov.AttachSheet) Kov.AttachSheet.open(picked => doUpload([...picked]));
      else input.click();
    } });
    addBtn.title = cfg.canUpload ? 'Выбрать файлы · можно и перетащить их сюда · до 2 ГБ каждый'
                                 : (cfg.uploadHint || 'Загрузка файлов в этот раздел сейчас недоступна');
    addBtn.disabled = !cfg.canUpload;
    const toolbar = h('div.toolbar', searchBox.el, filterSel, h('span.spacer'), addBtn, input);
    const progressWrap = h('div.upload-progress', { hidden: true });
    const bar = h('div.bar'); const barText = h('div.small.muted');
    const cancelBtn = h('button.btn.ghost.tiny', { type: 'button', hidden: true, onclick: () => cancelUpload() },
      icon('close'), 'Отменить');
    progressWrap.append(h('div.track', bar), h('div.row', barText, h('span.spacer'), cancelBtn));

    // зона переноса — невидимый оверлей на всю область, проявляется при перетаскивании
    const dropZone = cfg.dropZone && cfg.canUpload
      ? h('div.drop-zone', { hidden: true },
          icon('upload'), h('span.t', 'Отпустите файлы здесь'), h('span.s', 'до 2 ГБ каждый'))
      : null;

    const root = h('div.file-block' + (dropZone ? '.has-drop' : ''));
    // в эти же файлы уже идёт отправка (ушли и вернулись) — показываем её ход и даём отменить
    const running = liveJobs.get(jobKey);
    if (running) setTimeout(() => {
      if (!liveJobs.has(jobKey)) return;
      progressWrap.hidden = false; cancelBtn.hidden = false; addBtn.disabled = true;
      const paint = (p, label) => { bar.style.width = (p * 100).toFixed(1) + '%'; if (label) barText.textContent = label; };
      running.listeners.add(paint);
      inFlight = running;
      paint(running.p || 0, running.label || '');
      running.done.then(() => {
        running.listeners.delete(paint);
        if (inFlight === running) inFlight = null;
        progressWrap.hidden = true; cancelBtn.hidden = true; bar.style.width = '0'; barText.textContent = '';
        addBtn.disabled = !cfg.canUpload;
      });
    }, 0);
    if (tabs) root.append(tabs);
    root.append(toolbar, gridEl);
    if (dropZone) root.append(dropZone);
    root.append(progressWrap);

    if (cfg.canUpload) {
      let depth = 0;   // dragleave прилетает и от вложенных элементов
      const show = on => { root.classList.toggle('dragging', on); if (dropZone) dropZone.hidden = !on; };
      // Тянут именно файлы, а не выделенный текст или ссылку
      const hasFiles = e => {
        const dt = e.dataTransfer;
        if (!dt) return false;
        if (dt.types && [...dt.types].includes('Files')) return true;
        return !!(dt.items && [...dt.items].some(i => i.kind === 'file'));
      };
      root.addEventListener('dragenter', e => { if (!hasFiles(e)) return; e.preventDefault(); depth++; show(true); });
      root.addEventListener('dragover', e => { if (!hasFiles(e)) return; e.preventDefault(); });
      root.addEventListener('dragleave', e => { if (!hasFiles(e)) return; if (--depth <= 0) { depth = 0; show(false); } });
      root.addEventListener('drop', e => {
        if (!hasFiles(e)) return;
        e.preventDefault(); depth = 0; show(false);
        if (e.dataTransfer.files.length) doUpload(e.dataTransfer.files);
      });
    }

    /**
     * Одноимённые файлы: спрашиваем, заменить ли их новыми.
     *
     * Порядок важен — сначала грузим новое, потом удаляем старое. Наоборот
     * нельзя: если загрузка сорвётся, человек останется и без нового файла,
     * и без старого. Возвращает список номеров на удаление.
     */
    /** Список раздела: у блока своего нет, он всегда приходит снаружи */
    function currentFiles() {
      const list = cfg.getFiles ? cfg.getFiles() : null;
      return Array.isArray(list) ? list : [];
    }

    /**
     * По каким именам считать файл тем же самым.
     *
     * У фотографий и видео расширение меняется при сжатии на сервере: .jpg
     * ложится как .webp, .mov — как .mp4. Человек выбирает «Фото.jpg», а на
     * сервере лежит «Фото.webp», и по полному имени совпадения не было —
     * поэтому в разделе фотографий окно замены не появлялось вовсе.
     * Для них совпадением считается и одна основа имени. Для документов
     * так нельзя: «Договор.pdf» и «Договор.docx» — разные файлы.
     */
    function nameKeys(name) {
      const full = String(name || '').trim().toLowerCase();
      if (!full) return [];
      return isMedia(full) ? [full, full.replace(/\.[^.]+$/, '')] : [full];
    }

    async function askReplace(fileList) {
      if (!cfg.canDelete) return [];
      const picked = new Set();
      [...fileList].forEach(f => nameKeys(f.name).forEach(k => picked.add(k)));
      // Запоминаем номера, а не сами записи: после загрузки список
      // пересобирается, и прежние объекты в нём уже не те
      const clash = currentFiles().filter(f => fileId(f) != null && cfg.canDelete(f)
        && nameKeys(fileName(f)).some(k => picked.has(k)));
      if (!clash.length) return [];

      const byName = new Map();
      clash.forEach(f => {
        const key = fileName(f);
        byName.set(key, (byName.get(key) || 0) + 1);
      });
      const lines = [...byName.entries()].map(([name, n]) =>
        h('li', name + (n > 1 ? ' — ' + n + ' шт.' : '')));
      const total = clash.length;
      // «3 файла(ов)» читается как недоделка — склоняем по-человечески
      const tail = total % 10 === 1 && total % 100 !== 11 ? 'файл'
        : ([2, 3, 4].includes(total % 10) && ![12, 13, 14].includes(total % 100) ? 'файла' : 'файлов');

      return new Promise(resolve => Kov.UI.modal({
        title: 'Файл с таким названием уже есть', glyph: 'warning', dismissible: false,
        body: el => el.append(h('div',
          h('p', total === 1
            ? 'Здесь уже есть файл с таким названием. Заменить его новым?'
            : 'Здесь уже есть ' + total + ' ' + tail + ' с такими названиями. Заменить их новыми?'),
          h('ul.replace-list', ...lines),
          h('p.small.muted', 'Сначала загрузится новый файл, старые удалятся только после этого.'))),
        buttons: [
          { text: 'Оставить как есть', kind: 'ghost', onClick: close => { resolve([]); close(); } },
          { text: 'Заменить', icon: 'refresh', onClick: close => {
            resolve(clash.map(f => ({ id: fileId(f), kind: fileKind(f), name: fileName(f) })));
            close();
          } },
        ],
        onClose: () => resolve([]),
      }));
    }

    let inFlight = null;
    async function doUpload(fileList) {
      // снимок до первого await: живой FileList может опустеть
      const files = [...(fileList || [])];
      if (!files.length) return;
      const many = files.length > 1;
      let replace = [];
      try { replace = await askReplace(files); } catch (e) { replace = []; }
      progressWrap.hidden = false;
      addBtn.disabled = true;
      cancelBtn.hidden = false;
      // ушли в другой раздел или вкладку — отправка идёт дальше; вернулись к этим файлам — её ход снова виден
      let resolveDone; const done = new Promise(r => { resolveDone = r; });
      const listeners = new Set([(p, label) => { bar.style.width = (p * 100).toFixed(1) + '%'; barText.textContent = label; }]);
      const jobCtrl = new AbortController();
      const track = (p, label) => {
        if (jobCtrl.signal.aborted) return;
        job.p = p; job.label = label;
        listeners.forEach(fn => { try { fn(p, label); } catch (e) { /* */ } });
      };
      track.holder = {};
      const job = inFlight = { listeners, done, p: 0, label: '',
        reason: '',
        abort(reason) {
          if (jobCtrl.signal.aborted) return;
          job.reason = reason || 'user';
          jobCtrl.abort();
          const xhr = track.holder.xhr;
          if (xhr) { try { xhr.abort(); } catch (e) { /* */ } }
          barText.textContent = 'Отменяем…';
          cancelBtn.disabled = true;
        },
      };
      activeJobs.add(job);
      liveJobs.set(jobKey, job);
      try {
        const created = await upload(files, cfg.uploadPath || 'upload', cfg.uploadFields, track, jobCtrl.signal);
        // файл уже на месте: добавляем его в список сразу, до перезагрузки
        if (cfg.onUploaded && Array.isArray(created) && created.length) cfg.onUploaded(created);
        // Новое легло — теперь можно убрать старое с тем же названием.
        // Работаем по номерам: только что созданные записи не трогаем, даже
        // если сервер дал им то же имя.
        if (replace.length) {
          const fresh = new Set((Array.isArray(created) ? created : []).map(f => fileId(f)));
          const failed = [];
          let gone = 0;
          for (const old of replace) {
            if (old.id == null || fresh.has(old.id)) continue;
            try {
              await Kov.Api.del(ENDPOINTS[old.kind].del(old.id));
              gone++;
            } catch (e) {
              // Файла уже нет — цель достигнута, это не ошибка
              if (e && (e.status === 404 || e.status === 410)) { gone++; continue; }
              failed.push(old.name);
            }
          }
          if (gone) Kov.UI.toast(gone === 1 ? 'Старый файл заменён' : 'Заменено файлов: ' + gone, 'success');
          if (failed.length) {
            // Одно имя могло встретиться несколько раз — в сообщении оно одно
            const names = [...new Set(failed)];
            Kov.UI.toast('Новый файл загружен, но прежний остался: ' + names.join(', '), 'warning');
          }
        }
        // файл попал в другую категорию (архив — в «Другое»): открываем её, иначе загруженного не видно
        const shown = Array.isArray(created) ? created.filter(f => fileName(f)) : [];
        if (tabs && shown.length && !shown.some(f => categorize(fileName(f)) === category)) {
          category = categorize(fileName(shown[0]));
          tabs.setActive(category);
          cfg.onCategoryChange && cfg.onCategoryChange(category);
        }
        render();
        // 100% — ровно тогда, когда файл виден в списке и им можно пользоваться
        bar.style.width = '100%';
        barText.textContent = many ? 'Готово · файлы доступны' : 'Готово · файл доступен';
        Kov.UI.toast(many ? 'Файлы загружены' : 'Файл загружен', 'success');
        cfg.onChanged && cfg.onChanged();
        await new Promise(r => setTimeout(r, 900));
      } catch (e) {
        if (e && e.stalled) Kov.UI.toast(Kov.UI.errorText(e), 'error');
        else if (e && e.aborted && job.reason === 'offline') { /* сообщение показал app.js */ }
        else if (e && e.aborted) Kov.UI.toast('Загрузка отменена — файл не сохранён', 'info');
        else if (e && e.network) Kov.UI.toast(Kov.UI.errorText(e) + '. Попробуйте ещё раз, когда связь вернётся', 'error');
        else Kov.UI.toast('Загрузка не удалась: ' + Kov.UI.errorText(e), 'error');
      }
      finally {
        if (liveJobs.get(jobKey) === job) liveJobs.delete(jobKey);
        resolveDone();
        activeJobs.delete(job);
        inFlight = null;
        setTimeout(notifyIdle, 0);
        cancelBtn.disabled = false;
        progressWrap.hidden = true; cancelBtn.hidden = true;
        bar.style.width = '0'; barText.textContent = ''; addBtn.disabled = !cfg.canUpload;
      }
    }

    function cancelUpload() {
      if (!inFlight) return;
      try { inFlight.abort(); } catch (e) { /* */ }
      inFlight = null;
    }

    function render() {
      const all = cfg.getFiles() || [];
      // фильтр по отправителю пересобирается из файлов текущей категории
      const inCategory = category ? all.filter(f => categorize(fileName(f)) === category) : all;
      const people = uploaderList(inCategory);
      if (uploaderId !== null && !people.some(([id]) => id === uploaderId)) uploaderId = null;
      const fresh = uploaderFilter(inCategory, v => { uploaderId = v; render(); }, uploaderId);
      filterSel.replaceWith(fresh); filterSel = fresh;
      let list = inCategory;
      if (search.trim()) { const needle = search.trim().toLowerCase(); list = list.filter(f => fileName(f).toLowerCase().includes(needle)); }
      if (uploaderId !== null) list = list.filter(f => (f.uploaded_by_id || (f.uploaded_by && f.uploaded_by.id)) === uploaderId);
      list = list.slice().sort((a, b) => {
        const ta = Kov.UI.parseDate(a.created_at)?.getTime() ?? 0, tb = Kov.UI.parseDate(b.created_at)?.getTime() ?? 0;
        return cfg.sortEarliestFirst ? (ta - tb || a.id - b.id) : (tb - ta || b.id - a.id);
      });
      gridEl.fill(list);
      // счётчики в подписях вкладок
      if (tabs) CATEGORIES.forEach(([key, label]) => {
        const count = all.filter(f => categorize(fileName(f)) === key).length;
        const btn = tabs && tabs.querySelector('[data-key="' + key + '"]');
        if (btn) {
        btn.lastChild.textContent = label + (count ? ' (' + count + ')' : '');
        btn.dataset.count = count ? String(count) : '';
        btn.title = label + (count ? ': ' + count : '');
        btn.setAttribute('aria-label', btn.title);
      }
      });
    }
    root.render = render;
    root.setSort = v => { cfg.sortEarliestFirst = v; render(); };
    root.getCategory = () => category;
    root.setCategory = key => { if (!tabs || !key) return; category = key; tabs.setActive(key); render(); };
    render();
    return root;
  }

  const api = { IMAGE_EXT, VIDEO_EXT, CATEGORIES, OFFLINE_GRACE_MS, sheetUrl, abortAllUploads, uploadsActive, whenIdle, get current() { return upload.current; }, categorize, isImage, isVideo, isMedia, isViewable, isOffice, ext, url, fileKind, fileName, fileId, uploaderName, fmtSize, grid, uploaderFilter, uploaderList, viewer, upload, categoryBlock, inMemory, isDoc, openDocument, cancelTransfers, onTransfer: null };
  return api;
})();
