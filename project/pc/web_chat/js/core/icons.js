/* Векторные значки — те же имена, что в general_functions/vector_icons.py десктопа.
 * Линии stroke, viewBox 24, цвет через currentColor.
 * Часть значков — по рисункам Lucide (ISC) и Feather (MIT): тексты лицензий в THIRD-PARTY-NOTICES.txt. */
window.Kov = window.Kov || {};

Kov.Icons = (function () {
  const P = {
    menu: 'M4 7h16M4 12h16M4 17h16',
    close: 'M6 6l12 12M18 6L6 18',
    arrow_back: 'M15 5l-7 7 7 7',
    arrow_right: 'M9 5l7 7-7 7',
    chevron_down: 'M6 9l6 6 6-6',
    crop: 'M6 2v14a2 2 0 0 0 2 2h14M2 6h14a2 2 0 0 1 2 2v14',
    // ластик: корпус под 45°, линия отрыва резинки и «стол», по которому стирают
    eraser: 'M7 21l-4.3-4.3a2.4 2.4 0 0 1 0-3.4l9.6-9.6a2.4 2.4 0 0 1 3.4 0l5.6 5.6a2.4 2.4 0 0 1 0 3.4L13 21M22 21H7M5 11l9 9',
    brush: 'M9.1 11.9l8-8a2.85 2.85 0 1 1 4.03 4.03l-8.06 8.08M7.07 14.94a3 3 0 0 0-3 3.02c0 1.33-2.5 1.52-2 2.02 1.08 1.1 2.49 2.02 4 2.02a4 4 0 0 0 4-4.04 3.01 3.01 0 0 0-3-3.02z',
    flip: 'M12 3v18M8 7l-5 5 5 5M16 7l5 5-5 5',
    flip_v: 'M3 12h18M7 8l5-5 5 5M7 16l5 5 5-5',
    rotate: 'M21 12a9 9 0 1 1-3-6.7M21 3v6h-6',
    undo: 'M9 14l-5-5 5-5M4 9h11a6 6 0 0 1 0 12h-3',
    send: 'M22 2L11 13M22 2l-7 20-4-9-9-4z',
    chevron_left: 'M15 6l-6 6 6 6',
    chevron_right: 'M9 6l6 6-6 6',
    minus: 'M5 12h14',
    plus: 'M12 5v14M5 12h14',
    search: 'M11 4a7 7 0 1 1 0 14 7 7 0 0 1 0-14zM20 20l-4-4',
    filter: 'M3 5h18l-7 8v6l-4-2v-4L3 5z',
    edit: 'M4 20h4l10-10-4-4L4 16v4zM13 7l4 4',
    trash: 'M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6',
    download: 'M12 4v11M7 10l5 5 5-5M4 20h16',
    upload: 'M12 15V4M7 9l5-5 5 5M4 20h16',
    eye: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z',
    eye_off: 'M3 3l18 18M10.6 10.6a3 3 0 0 0 4.2 4.2M9.9 5.2A10.5 10.5 0 0 1 12 5c6 0 10 7 10 7a18 18 0 0 1-3.2 4.1M6.2 6.6A18 18 0 0 0 2 12s4 7 10 7a10.5 10.5 0 0 0 3.6-.6',
    play: 'M7 4l12 8-12 8V4z',
    pause: 'M9 5v14M15 5v14',
    // размытие: половина круга чёткая, половина «рассыпана» точками
    blur: 'M12 3a9 9 0 0 1 0 18M12 3a9 9 0 0 0 0 18M12 7.5h.01M12 12h.01M12 16.5h.01M8 9.7h.01M8 14.3h.01M15.6 9.7h.01M15.6 14.3h.01',
    device: 'M7 2h10v20H7zM10 18.6h4',
    sun: 'M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4',
    moon: 'M20.5 14.7A8.6 8.6 0 0 1 9.3 3.5a8.6 8.6 0 1 0 11.2 11.2z',
    volume: 'M4 9h3l4-3v12l-4-3H4V9zM16 9.6a3.5 3.5 0 0 1 0 4.8M18.6 7a7 7 0 0 1 0 10',
    zoom: 'M11 4a7 7 0 1 1 0 14 7 7 0 0 1 0-14zM20 20l-4-4M8.6 11h4.8M11 8.6v4.8',
    image: 'M4 5h16v14H4zM8 10a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3zM4 17l5-5 4 4 3-3 4 4',
    video: 'M3 7h12v10H3zM15 10l6-3v10l-6-3',
    file: 'M6 3h8l4 4v14H6zM14 3v4h4',
    folder: 'M3 6h6l2 2h10v11H3z',
    users: 'M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM2.5 20a6.5 6.5 0 0 1 13 0M16 4.5a3.5 3.5 0 0 1 0 7M21.5 20a6 6 0 0 0-4.5-5.8',
    user: 'M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21a8 8 0 0 1 16 0',
    id_card: 'M3 6h18v12H3zM7 10a2 2 0 1 0 0 4 2 2 0 0 0 0-4zM13 10h5M13 14h5',
    settings: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z',
    archive: 'M3 5h18v4H3zM5 9v11h14V9M10 13h4',
    calendar: 'M4 6h16v14H4zM4 10h16M8 3v4M16 3v4',
    clock: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2',
    flag: 'M5 21V4h11l-2 4 2 4H5',
    list: 'M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01',
    bell: 'M6 16V11a6 6 0 0 1 12 0v5l2 2H4l2-2zM10 20a2 2 0 0 0 4 0',
    mail: 'M3 6h18v12H3zM3 7l9 6 9-6',
    note: 'M5 3h14v18H5zM9 8h6M9 12h6M9 16h4',
    check: 'M4 12l5 5L20 6',
    copy: 'M9 9h11v11H9zM4 15V4h11',
    save: 'M4 4h12l4 4v12H4zM8 4v5h7V4M8 20v-6h8v6',
    table: 'M3 5h18v14H3zM3 10h18M3 15h18M9 5v14M15 5v14',
    building: 'M4 21V4h10v17M14 9h6v12M7 8h1M7 12h1M7 16h1M17 13h1M17 17h1',
    info: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 11v6M12 8h.01',
    warning: 'M12 3L2 21h20L12 3zM12 10v5M12 18h.01',
    error: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM9 9l6 6M15 9l-6 6',
    question: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.3-1 .8-1 1.7M12 17h.01',
    success: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM8 12l3 3 5-6',
    logout: 'M10 4H4v16h6M15 8l5 4-5 4M20 12H9',
    key: 'M15 3a6 6 0 1 0 5.5 8.4L22 13v3h-3v3h-3v-3l-2.5-2.5A6 6 0 0 0 15 3z',
    refresh: 'M4 12a8 8 0 0 1 14-5l2 2M20 4v5h-5M20 12a8 8 0 0 1-14 5l-2-2M4 20v-5h5',
    chat: 'M4 4h16v11H8l-4 4V4z',
    project: 'M3 6h6l2 2h10v11H3zM8 13h8M12 11v4',
    camera: 'M3 8h4l2-3h6l2 3h4v11H3zM12 17a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z',
    phone: 'M5 3h4l2 5-2.5 1.5a11 11 0 0 0 6 6L16 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 5a2 2 0 0 1 2-2z',
    star: 'M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9L12 3z',
  };

  // opts: { stroke — толщина линии, fill — залить фигуру (для «плей»), cls }
  function svg(name, opts) {
    opts = opts || {};
    const d = P[name] || P.file;
    const stroke = opts.stroke || 1.9;
    return '<svg viewBox="0 0 24 24" fill="' + (opts.fill ? 'currentColor' : 'none') + '" stroke="currentColor" stroke-width="' + stroke +
      '" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"' +
      (opts.cls ? ' class="' + opts.cls + '"' : '') + '><path d="' + d + '"/></svg>';
  }

  return { svg, names: Object.keys(P) };
})();


/* ── фирменный знак ────────────────────────────────────────────────────────
 * Живёт здесь, а не в отдельном файле: новый файл требует новой строки в
 * index.html, а закэшированный браузером index.html её не содержит — знак
 * тогда просто не загружался. Этот файл подключён с самого начала.
 */
Kov.Logo = Kov.Logo || (function () {
  const NS = 'http://www.w3.org/2000/svg';

  // контур разомкнут в верхнем правом углу — там стоит янтарная дуга
  const RING = 'M124 42V78A26 26 0 0 1 98 104H62A26 26 0 0 1 36 78V42A26 26 0 0 1 62 16H98';
  const CORNER = 'M98 16A26 26 0 0 1 124 42';
  const KAY = 'M95 38L73 60L95 82';

  const ring = cls => '<path class="si ' + (cls || '') + '" stroke-width="8" d="' + RING + '"/>';
  const corner = cls => '<path class="sa ' + (cls || '') + '" stroke-width="8" stroke-linecap="round" d="' + CORNER + '"/>';
  const kay = (cls, knock) => '<g class="' + (cls || '') + '">'
    + '<rect class="' + (knock ? 'knock' : 'ink') + '" x="59" y="36" width="11" height="48" rx="5.5"/>'
    + '<path class="' + (knock ? 'sk' : 'si') + '" stroke-width="11" stroke-linecap="round" stroke-linejoin="round" d="' + KAY + '"/></g>';

  // буквы названия построены по модулю K: та же толщина 11 и скруглённые концы
  function wordPaths(withAccent) {
    const acc = withAccent ? 'sa' : 'si';
    const bowl = (cx, cy) => '<path class="si" stroke-width="11" d="M' + cx + ' ' + (cy - 14.5)
      + 'A14.5 14.5 0 0 1 ' + cx + ' ' + (cy + 14.5) + 'A14.5 14.5 0 0 1 ' + cx + ' ' + (cy - 14.5) + '"/>';
    return ''
      + '<path class="si" stroke-width="11" stroke-linecap="round" d="M104.5 65A14.5 14.5 0 1 1 90 50.5"/>'
      + '<path class="' + acc + '" stroke-width="11" stroke-linecap="round" d="M90 50.5A14.5 14.5 0 0 1 104.5 65"/>'
      + '<path class="si" stroke-width="11" stroke-linecap="round" stroke-linejoin="round" d="M122.5 51.5L136 78.5L149.5 51.5"/>'
      + bowl(178, 65) + '<path class="si" stroke-width="11" stroke-linecap="round" d="M192.5 51.5V78.5"/>'
      + '<path class="si" stroke-width="11" stroke-linecap="round" d="M210.5 51.5V102.5"/>' + bowl(225, 65)
      + '<path class="si" stroke-width="11" stroke-linecap="round" d="M257.5 51.5V102.5"/>' + bowl(272, 65);
  }

  const MARK = ring() + corner() + kay();
  // плотная версия: та же геометрия заливкой, буква просветом — для мелких размеров
  const SOLID = '<rect class="ink" x="32" y="12" width="96" height="96" rx="30"/>' + corner() + kay(null, true);
  // с обводкой: штрих идёт по периметру, потом буква, потом янтарный угол
  const MARK_ANIM = ring('an draw') + kay('an pop') + corner('an draw corner');

  function svg(viewBox, body, cls) {
    const el = document.createElementNS(NS, 'svg');
    el.setAttribute('class', 'kov-mark ' + (cls || ''));
    el.setAttribute('viewBox', viewBox);
    el.setAttribute('role', 'img');
    el.setAttribute('aria-label', 'Kovapp');
    el.innerHTML = body;
    return el;
  }

  /** Знак без анимации */
  function mark(cls) { return svg('0 0 160 120', MARK, cls); }
  /** Плотная версия — для размеров меньше 32 px и иконок */
  function solid(cls) { return svg('0 0 160 120', SOLID, cls); }
  /** Знак с названием в строку */
  function lockup(cls) {
    return svg('12 0 370 120', MARK + '<g transform="translate(70,0)">' + wordPaths(false) + '</g>', cls);
  }

  // Логотип с названием: буквы «ovapp» спрятаны за правой гранью контура и
  // выезжают из-под него, пока знак сам себя обводит — как в макете
  const REST = wordPaths(false);
  const LOCK_ANIM =
    '<clipPath id="kovLockClip" clipPathUnits="userSpaceOnUse"><rect x="124" y="0" width="290" height="120"/></clipPath>'
    + '<g clip-path="url(#kovLockClip)"><g class="an slide" style="--d:1.1s;--dur:.85s">'
    + '<g transform="translate(70,0)">' + REST + '</g></g></g>'
    + ring('an draw') + kay('an pop') + corner('an draw corner');

  /** Знак с названием, который сам себя собирает: play() запускает заново */
  function lockupAnimated(cls) {
    const el = svg('12 0 370 120', LOCK_ANIM, 'animated lockup ' + (cls || ''));
    return withPlay(el);
  }

  function withPlay(el) {
    const prepare = () => {
      el.querySelectorAll('.draw').forEach(node => {
        try {
          const len = Math.ceil(node.getTotalLength());
          node.style.setProperty('--len', len);
          node.style.strokeDasharray = len;
        } catch (e) { /* знак ещё не в документе — попробуем позже */ }
      });
    };
    el.play = function () {
      prepare();
      el.classList.remove('play');
      requestAnimationFrame(() => requestAnimationFrame(() => el.classList.add('play')));
    };
    requestAnimationFrame(() => el.play());
    return el;
  }

  /**
   * Знак, который сам себя рисует: длина контура считается по факту, иначе
   * штриховая обводка не работает. play() запускает заново.
   */
  function animated(cls) {
    const el = svg('0 0 160 120', MARK_ANIM, 'animated ' + (cls || ''));
    const prepare = () => {
      el.querySelectorAll('.draw').forEach(node => {
        try {
          const len = Math.ceil(node.getTotalLength());
          node.style.setProperty('--len', len);
          node.style.strokeDasharray = len;
        } catch (e) { /* знак ещё не в документе — попробуем позже */ }
      });
    };
    el.play = function () {
      prepare();
      el.classList.remove('play');
      // у SVG нет offsetWidth, поэтому перезапуск через два кадра
      requestAnimationFrame(() => requestAnimationFrame(() => el.classList.add('play')));
    };
    requestAnimationFrame(() => el.play());
    return el;
  }

  return { mark, solid, lockup, animated, lockupAnimated, MARK, SOLID };
})();
