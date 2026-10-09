/* Панель эмодзи, стикеров и GIF для чата.
 *
 * Живёт справа от переписки и открывается так же, как поиск по чату.
 * Три раздела:
 *   Эмодзи   — полный набор из стандартных диапазонов Unicode, по категориям;
 *   Стикеры  — пять наборов по шестнадцать настроений, векторные файлы;
 *   GIF      — поиск через сервис KLIPY.
 *
 * Про экономию запросов. Ключ работает в режиме испытаний: сто обращений в
 * час. Поэтому здесь всё устроено так, чтобы тратить их как можно реже:
 *   · при открытии раздела запрос не делается вовсе — только строка поиска;
 *   · после ввода ждём пять секунд молчания, и лишь потом спрашиваем сервис;
 *   · ответ каждого запроса складывается в память и в хранилище вкладки на
 *     тридцать минут: повтор того же слова не стоит ничего;
 *   · одинаковые запросы подряд склеиваются, счётчик держит потолок в час.
 * Счёт израсходованного ведётся молча: человеку он ни о чём не говорит, а
 * когда обращения кончатся, об этом скажет отдельное сообщение.
 */
window.Kov = window.Kov || {};

Kov.MediaPanel = (function () {
  'use strict';

  // Путь к стикерам считаем от адреса этого файла: чат может открываться из
  // разных мест, и относительный путь тогда указывал бы не туда
  const SCRIPT_SRC = (document.currentScript && document.currentScript.src) || '';
  const STICKER_BASE = SCRIPT_SRC ? SCRIPT_SRC.replace(/chat\/media-panel\.js.*$/, 'stickers/') : 'stickers/';

  // ── эмодзи ──────────────────────────────────────────────────────────────
  // Диапазоны кодов, а не готовый список: так набор занимает пару строк и
  // покрывает всю стандартную палитру. Диапазоны выбраны сплошные, без
  // незанятых мест, чтобы не появлялись пустые квадраты.
  const EMOJI_GROUPS = [
    ['Лица', '😀', [[0x1F600, 0x1F64F], [0x1F910, 0x1F92F], [0x1F970, 0x1F97A],
                    [0x1F9D0, 0x1F9D0], [0x1FAE0, 0x1FAE7], [0x263A, 0x263A], [0x2639, 0x2639]]],
    ['Жесты', '👋', [[0x1F440, 0x1F450], [0x1F590, 0x1F596], [0x1F918, 0x1F91F], [0x1F932, 0x1F933],
                     [0x1F44A, 0x1F450], [0x1FAF0, 0x1FAF6], [0x261D, 0x261D], [0x270A, 0x270D]]],
    ['Люди', '🧑', [[0x1F466, 0x1F487], [0x1F9D1, 0x1F9DF], [0x1F926, 0x1F931], [0x1F934, 0x1F93A],
                    [0x1F468, 0x1F469], [0x1F574, 0x1F57A], [0x1FAC0, 0x1FAC5]]],
    ['Природа', '🌿', [[0x1F400, 0x1F43F], [0x1F980, 0x1F9AE], [0x1F330, 0x1F343], [0x1F980, 0x1F991],
                       [0x1F308, 0x1F320], [0x1F32D, 0x1F32F], [0x1FAB0, 0x1FABA], [0x2600, 0x2604],
                       [0x26C4, 0x26C5], [0x2744, 0x2744]]],
    ['Еда', '🍔', [[0x1F345, 0x1F37F], [0x1F950, 0x1F96F], [0x1F344, 0x1F344], [0x1F32D, 0x1F32F],
                   [0x1F9C0, 0x1F9CB], [0x1FAD0, 0x1FAD9], [0x2615, 0x2615]]],
    ['Занятия', '⚽', [[0x1F380, 0x1F3C4], [0x1F3C5, 0x1F3CA], [0x1F3D8, 0x1F3DF], [0x1F93C, 0x1F945],
                       [0x1F947, 0x1F94C], [0x1F3AE, 0x1F3B5], [0x26BD, 0x26BE], [0x1F396, 0x1F397]]],
    ['Места', '🚗', [[0x1F680, 0x1F6C5], [0x1F5FA, 0x1F5FF], [0x1F3E0, 0x1F3F0], [0x1F6F3, 0x1F6FC],
                     [0x26F0, 0x26F5], [0x1F3D4, 0x1F3D7], [0x2708, 0x2708], [0x1F6E9, 0x1F6EC],
                     [0x26FD, 0x26FD]]],
    ['Предметы', '💡', [[0x1F4A0, 0x1F4FF], [0x1F500, 0x1F53D], [0x1F550, 0x1F567], [0x1F6CB, 0x1F6D2],
                        [0x1F9F0, 0x1F9FF], [0x1FA70, 0x1FA74], [0x1FA78, 0x1FA7C], [0x1FA80, 0x1FA86],
                        [0x1FA90, 0x1FAA8], [0x231A, 0x231B], [0x260E, 0x260E], [0x1F45C, 0x1F465], [0x1F97E, 0x1F97F]]],
    ['Символы', '❤️', [[0x2764, 0x2764], [0x1F493, 0x1F49F], [0x2705, 0x2705], [0x274C, 0x274C],
                       [0x2728, 0x2728], [0x2B50, 0x2B50], [0x1F31F, 0x1F320], [0x1F7E0, 0x1F7EB],
                       [0x2795, 0x2797], [0x2B06, 0x2B07], [0x27A1, 0x27A1], [0x2049, 0x2049],
                       [0x203C, 0x203C], [0x1F51D, 0x1F53D], [0x1F191, 0x1F19A]]],
  ];

  // Ключевые слова для поиска лежат в chat/emoji-index.js: там английские
  // названия всех символов по стандарту Unicode и русский словарь к ним.
  // Диапазоны выше и данные там должны совпадать по составу.

  function expandGroup(ranges) {
    const out = [];
    ranges.forEach(([from, to]) => {
      for (let code = from; code <= to; code++) out.push(String.fromCodePoint(code));
    });
    return out;
  }

  // Системный шрифт старых Windows не знает новых эмодзи и рисует вместо них
  // пустой квадрат. Такие в панель не выводим: каждое рисуем на крошечном холсте
  // и сравниваем с заведомо несуществующим символом.
  const EMOJI_FONT = '"Segoe UI Emoji","Apple Color Emoji","Noto Color Emoji","Segoe UI Symbol",sans-serif';
  const drawable = (() => {
    const cache = new Map();
    let ctx = null, blank = null, colorful = null;
    const paint = ch => {
      ctx.clearRect(0, 0, 24, 24);
      ctx.fillText(ch, 2, 20);
      return ctx.getImageData(0, 0, 24, 24).data;
    };
    const hasColor = d => {
      for (let i = 0; i < d.length; i += 4) {
        if (d[i + 3] > 40 && (Math.abs(d[i] - d[i + 1]) > 24 || Math.abs(d[i + 1] - d[i + 2]) > 24)) return true;
      }
      return false;
    };
    return ch => {
      if (cache.has(ch)) return cache.get(ch);
      try {
        if (!ctx) {
          const canvas = document.createElement('canvas');
          canvas.width = canvas.height = 24;
          ctx = canvas.getContext('2d', { willReadFrequently: true });
          ctx.font = '18px ' + EMOJI_FONT;
          ctx.fillStyle = '#000';
          blank = paint(String.fromCodePoint(0x1FFFE));
          colorful = hasColor(paint(String.fromCodePoint(0x1F600)));
        }
        const d = paint(ch);
        let same = true;
        for (let i = 0; i < d.length; i++) { if (d[i] !== blank[i]) { same = false; break; } }
        const ok = !same && (!colorful || hasColor(d));
        cache.set(ch, ok);
        return ok;
      } catch (e) {
        return true;
      }
    };
  })();

  /**
   * Общий поиск для разделов «Эмодзи» и «Стикеры».
   *
   * Ячейки заранее снабжены списком слов (`cell.__words`), поиск сверяет с
   * ними запрос. Сначала требуем совпадения всех слов запроса, и только если
   * так ничего не нашлось — засчитываем любое: «красное сердце» лучше покажет
   * сердечки, чем пустоту.
   */
  function applySearch(query, groups, empty) {
    const index = window.Kov && Kov.EmojiIndex;
    const cells = groups.reduce((all, g) => all.concat([...g.grid.children]), []);
    // Слова считаем при первом поиске, а не при сборке: тысяча с лишним
    // разборов на открытии раздела задерживала его без всякой пользы —
    // ищут далеко не всегда
    if (index) {
      cells.forEach(c => {
        if (!c.__words) c.__words = index.words(c.textContent, c.dataset.group || '');
      });
    }
    const showAll = () => {
      cells.forEach(c => { c.hidden = false; });
      groups.forEach(g => { g.box.hidden = false; });
      if (empty) empty.hidden = true;
    };
    if (!index) return showAll();
    let test = index.matcher(query);
    if (!test) return showAll();
    let found = cells.filter(c => test(c.__words));
    if (!found.length && String(query).trim().split(/\s+/).length > 1) {
      test = index.matcher(query, true);
      found = cells.filter(c => test(c.__words));
    }
    const ok = new Set(found);
    cells.forEach(c => { c.hidden = !ok.has(c); });
    groups.forEach(g => { g.box.hidden = ![...g.grid.children].some(c => !c.hidden); });
    if (empty) empty.hidden = found.length > 0;
  }

  // ── стикеры ─────────────────────────────────────────────────────────────
  const STICKER_MOODS = ['happy', 'love', 'laugh', 'wink', 'sad', 'cry', 'angry', 'sleep',
                         'shy', 'cool', 'think', 'ok', 'surprise', 'party', 'hug', 'sick'];
  const STICKER_SETS = [
    { id: 'bear', title: 'Мишка' }, { id: 'cat', title: 'Котик' }, { id: 'bunny', title: 'Зайка' },
    { id: 'panda', title: 'Панда' }, { id: 'frog', title: 'Лягушка' },
  ];
  const MOOD_WORDS = {
    happy: 'радость улыбка happy smile', love: 'любовь сердце love heart',
    laugh: 'смех хаха laugh joy', wink: 'подмигнул подмигивание wink',
    sad: 'грусть печаль sad', cry: 'плач слезы cry tears', angry: 'злость гнев angry',
    sleep: 'сон спать sleep', shy: 'смущение стесняюсь shy', cool: 'круто очки cool',
    think: 'думаю мысль think', ok: 'окей готово ok', surprise: 'удивление шок surprise',
    party: 'праздник вечеринка party', hug: 'обнимашки обнять hug',
    sick: 'плохо болею sick ill',
  };

  // ── GIF: настройки бережного обращения к сервису ────────────────────────
  const KLIPY = {
    key: 'PN6UZoDmjcljsyxx2BTA9vBkr43nX36tbukI2H6Q8wHnSG9rR0CuERd8RT25fyrG',
    base: 'https://api.klipy.com/api/v1',
    perPage: 24,
    waitMs: 5000,          // сколько молчим после последней буквы
    cacheMs: 30 * 60 * 1000,
    hourLimit: 100,        // потолок тарифа: сто обращений в час
    minQuery: 2,
  };

  const gifCache = new Map();          // запрос → { at, items }
  let gifTimer = null;
  let gifInFlight = null;

  function cacheKey(q) { return 'kov.klipy.' + q.trim().toLowerCase(); }

  function readCache(q) {
    const key = cacheKey(q);
    const live = gifCache.get(key);
    if (live && Date.now() - live.at < KLIPY.cacheMs) return live.items;
    try {
      const saved = JSON.parse(sessionStorage.getItem(key) || 'null');
      if (saved && Date.now() - saved.at < KLIPY.cacheMs) {
        gifCache.set(key, saved);
        return saved.items;
      }
    } catch (e) { /* хранилище может быть недоступно */ }
    return null;
  }

  function writeCache(q, items) {
    const value = { at: Date.now(), items };
    gifCache.set(cacheKey(q), value);
    try { sessionStorage.setItem(cacheKey(q), JSON.stringify(value)); } catch (e) { /* переполнено */ }
  }

  /** Счётчик обращений за час: держим свой потолок ниже тарифного */
  function budget() {
    let log = [];
    try { log = JSON.parse(sessionStorage.getItem('kov.klipy.log') || '[]'); } catch (e) { log = []; }
    const hourAgo = Date.now() - 3600 * 1000;
    log = log.filter(t => t > hourAgo);
    return {
      used: log.length,
      left: Math.max(0, KLIPY.hourLimit - log.length),
      spend() {
        log.push(Date.now());
        try { sessionStorage.setItem('kov.klipy.log', JSON.stringify(log)); } catch (e) { /* */ }
      },
    };
  }

  /** Разбор ответа: у сервиса несколько форм ответа, берём первую подходящую */
  function parseGifs(data) {
    const root = (data && (data.data || data.result || data)) || {};
    const list = root.data || root.items || root.results || (Array.isArray(root) ? root : []);
    if (!Array.isArray(list)) return [];
    return list.map(item => {
      const file = item.file || item.files || {};
      const pick = (...paths) => {
        for (const p of paths) {
          const value = p.split('.').reduce((acc, k) => (acc && acc[k] !== undefined ? acc[k] : null), file);
          if (typeof value === 'string' && value) return value;
          if (value && typeof value.url === 'string') return value.url;
        }
        return null;
      };
      const preview = pick('sm.gif', 'xs.gif', 'md.gif', 'sm.webp', 'md.webp')
        || item.preview_url || item.thumbnail || item.url || null;
      const full = pick('md.gif', 'lg.gif', 'hd.gif', 'sm.gif') || item.url || preview;
      return preview ? {
        slug: item.slug || item.id || preview,
        title: item.title || item.slug || 'GIF',
        preview, full,
        width: item.width || 0, height: item.height || 0,
      } : null;
    }).filter(Boolean);
  }

  async function searchGifs(query) {
    const cached = readCache(query);
    if (cached) return { items: cached, fromCache: true };
    const b = budget();
    if (!b.left) return { items: [], limited: true };
    if (gifInFlight && gifInFlight.query === query) return gifInFlight.promise;

    const url = KLIPY.base + '/' + KLIPY.key + '/gifs/search'
      + '?q=' + encodeURIComponent(query)
      + '&page=1&per_page=' + KLIPY.perPage
      + '&customer_id=' + encodeURIComponent(customerId());
    const promise = (async () => {
      b.spend();
      const res = await fetch(url, { headers: { Accept: 'application/json' } });
      if (!res.ok) throw new Error('Сервис ответил ' + res.status);
      const items = parseGifs(await res.json());
      writeCache(query, items);
      return { items, fromCache: false };
    })();
    gifInFlight = { query, promise };
    try { return await promise; } finally { gifInFlight = null; }
  }

  /** Постоянный номер пользователя для сервиса: нужен для его статистики */
  function customerId() {
    try {
      let id = localStorage.getItem('kov.klipy.customer');
      if (!id) {
        id = 'kov-' + Math.random().toString(36).slice(2, 12);
        localStorage.setItem('kov.klipy.customer', id);
      }
      return id;
    } catch (e) { return 'kov-guest'; }
  }

  // ── разметка ────────────────────────────────────────────────────────────
  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  /**
   * mount({ sidebar, tabs, body, onEmoji, onSticker, onGif })
   * Возвращает { open, close, toggle, isOpen }.
   */
  function mount(opts) {
    const sidebar = opts.sidebar, body = opts.body, tabsBox = opts.tabs;
    // закрытая панель не попадает в обход клавишей Tab (раньше — тысяча невидимых смайликов)
    if (!sidebar.classList.contains('open')) sidebar.inert = true;
    let current = 'emoji';
    const built = {};

    /**
     * Показ раздела.
     *
     * Эмодзи возвращаются в документ при каждом переключении вкладки, и
     * раскладывать всю тысячу ячеек заново — почти полсекунды впустую.
     * Поэтому при показе готовность разделов сбрасывается: браузер
     * раскладывает только видимое, а остальное догоняет в свободное время.
     * Держать панели в документе внахлёст я пробовал — возврат ускоряется,
     * но прокрутка падает с шестидесяти кадров до пяти, размен плохой.
     */
    function show(tab) {
      current = tab;
      [...tabsBox.querySelectorAll('.media-tab')].forEach(b => b.classList.toggle('on', b.dataset.tab === tab));
      const pane = ensure(tab);
      body.replaceChildren(pane);
      if (pane.__warmUp) setTimeout(pane.__warmUp, 0);
      const input = body.querySelector('.media-search input');
      // на сенсорном экране курсор (и клавиатура) — только по касанию: иначе она закрывает панель
      const touch = window.matchMedia && window.matchMedia('(hover: none), (pointer: coarse)').matches;
      if (input && tab !== 'emoji' && !touch) setTimeout(() => input.focus(), 120);
    }

    function ensure(tab) {
      if (built[tab]) return built[tab];
      const pane = build(tab);
      pane.dataset.pane = tab;
      built[tab] = pane;
      body.append(pane);
      return pane;
    }

    function build(tab) {
      if (tab === 'emoji') return buildEmoji(opts.onEmoji);
      if (tab === 'stickers') return buildStickers(opts.onSticker);
      return buildGif(opts.onGif);
    }

    // Эмодзи собираем заранее, не дожидаясь открытия панели: раздел большой,
    // и человеку не за что ждать. Пока раздел скрыт, он ничего не стоит.
    const idle = window.requestIdleCallback || (fn => setTimeout(fn, 400));
    idle(() => { if (!built.emoji) ensure('emoji'); });

    tabsBox.addEventListener('click', e => {
      const btn = e.target.closest('.media-tab');
      if (btn) show(btn.dataset.tab);
    });

    const api = {
      open() {
        sidebar.inert = false;
        sidebar.classList.add('open');
        sidebar.setAttribute('aria-hidden', 'false');
        if (!body.firstChild) show(current);
      },
      close() { sidebar.classList.remove('open'); sidebar.setAttribute('aria-hidden', 'true'); sidebar.inert = true; },
      toggle() { api.isOpen() ? api.close() : api.open(); },
      isOpen() { return sidebar.classList.contains('open'); },
      show,
    };
    return api;
  }

  // ── раздел «Эмодзи» ─────────────────────────────────────────────────────
  function buildEmoji(onPick) {
    const wrap = el('div', 'media-pane emoji-pane');
    const search = el('div', 'media-search');
    const input = document.createElement('input');
    input.type = 'search';
    input.placeholder = 'Поиск: улыбка, огонь, cat, heart…';
    search.append(input);

    const nav = el('div', 'emoji-nav');
    const list = el('div', 'emoji-list');
    const empty = el('div', 'media-empty', 'Ничего не нашлось — попробуйте другое слово');
    empty.hidden = true;
    const index = window.Kov && Kov.EmojiIndex;
    const groups = EMOJI_GROUPS.map(([title, glyph, ranges]) => {
      const box = el('div', 'emoji-group');
      const head = el('div', 'emoji-group-title', title);
      const grid = el('div', 'emoji-grid');
      // Тысяча ячеек — это тысяча обработчиков и тысяча подсказок, если
      // вешать их поштучно. Нажатие ловим одно на весь список, подсказку
      // ставим при наведении, слова для поиска — при первом поиске.
      const frag = document.createDocumentFragment();
      expandGroup(ranges).filter(drawable).forEach(ch => {
        const b = el('button', 'emoji-cell', ch);
        b.type = 'button';
        b.dataset.group = title;
        frag.append(b);
      });
      grid.append(frag);
      // Пока раздел не на экране, браузер его не раскладывает и не рисует.
      // Высоту подсказываем сами, чтобы полоса прокрутки сразу была верной.
      const rows = Math.ceil(grid.children.length / 8);
      box.style.containIntrinsicSize = 'auto ' + (rows * 40 + 26) + 'px';
      box.append(head, grid);
      const tab = el('button', 'emoji-nav-btn', glyph);
      tab.type = 'button';
      tab.title = title;
      tab.addEventListener('click', () => box.scrollIntoView({ behavior: 'smooth', block: 'start' }));
      nav.append(tab);
      return { box, grid, title };
    });

    groups.forEach(g => list.append(g.box));

    input.addEventListener('input', () => applySearch(input.value, groups, empty));

    // Одно нажатие на весь список вместо обработчика в каждой ячейке
    list.addEventListener('click', e => {
      const cell = e.target.closest && e.target.closest('.emoji-cell');
      if (cell && onPick) onPick(cell.textContent);
    });
    // Название показываем при наведении — и только у той ячейки, на которую
    // навели: тысяча заранее проставленных подсказок ничего не даёт
    list.addEventListener('pointerover', e => {
      const cell = e.target.closest && e.target.closest('.emoji-cell');
      if (cell && !cell.title) cell.title = (index && index.nameOf(cell.textContent)) || cell.textContent;
    });

    wrap.append(search, nav, empty, list);
    // Прогрев запускается не здесь, а когда раздел впервые показали: пока он
    // скрыт, раскладывать нечего — ширина панели ещё не известна, и вся
    // работа всё равно повторилась бы при показе.
    wrap.__warmUp = () => warmUp(groups);
    return wrap;
  }

  /**
   * Догоняющая отрисовка разделов.
   *
   * Пока раздел не на экране, браузер его не раскладывает — благодаря этому
   * панель открывается сразу. Но тогда работа переезжает на прокрутку, и она
   * начинает дёргаться. Поэтому, показав панель, доводим разделы до
   * готовности в свободные промежутки между кадрами: человек ещё смотрит на
   * первый экран, а остальные уже разложены и прокрутка идёт гладко.
   */
  function warmUp(groups) {
    // Раздел только что вернулся в документ: заново разложить всё сразу —
    // это и есть та самая задержка. Снимаем готовность и набираем её по мере
    // свободного времени.
    groups.forEach(g => g.box.classList.remove('is-warm'));
    let i = 0;
    const idle = window.requestIdleCallback
      || (fn => setTimeout(() => fn({ timeRemaining: () => 8 }), 50));
    function step(deadline) {
      while (i < groups.length && deadline.timeRemaining() > 4) groups[i++].box.classList.add('is-warm');
      if (i < groups.length) idle(step);
    }
    idle(step);
  }

  // ── раздел «Стикеры» ────────────────────────────────────────────────────
  function buildStickers(onPick) {
    const wrap = el('div', 'media-pane');
    const search = el('div', 'media-search');
    const input = document.createElement('input');
    input.type = 'search';
    input.placeholder = 'Поиск: радость, сон, окей, happy…';
    search.append(input);

    const list = el('div', 'sticker-list');
    const empty = el('div', 'media-empty', 'Ничего не нашлось — попробуйте другое слово');
    empty.hidden = true;
    const index = window.Kov && Kov.EmojiIndex;
    const groups = STICKER_SETS.map(set => {
      const box = el('div', 'sticker-group');
      const head = el('div', 'emoji-group-title', set.title);
      const grid = el('div', 'sticker-grid');
      STICKER_MOODS.forEach(mood => {
        const b = el('button', 'sticker-cell');
        b.type = 'button';
        b.title = set.title + ' — ' + (MOOD_WORDS[mood] || mood);
        const text = (MOOD_WORDS[mood] || mood) + ' ' + mood + ' ' + set.title;
        b.__words = index ? index.words('', text) : text.toLowerCase().split(/\s+/);
        const img = document.createElement('img');
        img.src = STICKER_BASE + set.id + '/' + mood + '.svg';
        img.alt = b.title;
        img.loading = 'lazy';
        img.draggable = false;          // стикер не перетаскивается в чат: отправляется только нажатием
        b.append(img);
        b.addEventListener('click', () => onPick && onPick(set.id + '/' + mood, set.title));
        grid.append(b);
      });
      box.append(head, grid);
      return { box, grid, title: set.title.toLowerCase() };
    });
    groups.forEach(g => list.append(g.box));

    input.addEventListener('input', () => applySearch(input.value, groups, empty));

    wrap.append(search, empty, list);
    return wrap;
  }

  // ── раздел «GIF» ────────────────────────────────────────────────────────
  function buildGif(onPick) {
    const wrap = el('div', 'media-pane');
    const search = el('div', 'media-search');
    const input = document.createElement('input');
    input.type = 'search';
    input.placeholder = 'Введите слово и подождите…';
    search.append(input);

    const hint = el('div', 'gif-hint', 'Наберите слово — поиск начнётся, когда вы закончите печатать');
    // Прокручиваемая обёртка и внутри неё поток колонок. Сетка тут не годится:
    // она равняет строку по самой высокой картинке, и высокие GIF обрезались,
    // а широкие оставляли под собой пустое поле. В колонках каждая картинка
    // сохраняет свои пропорции и укладывается встык к соседней.
    const grid = el('div', 'gif-grid');
    const flow = el('div', 'gif-flow');
    grid.append(flow);
    const brand = el('div', 'gif-brand');
    brand.innerHTML = '<span>GIF от</span><b>KLIPY</b>';

    function skeleton(count) {
      flow.replaceChildren();
      for (let i = 0; i < (count || 12); i++) {
        const cell = el('div', 'gif-skeleton');
        cell.style.height = (90 + (i % 4) * 26) + 'px';
        cell.style.animationDelay = (i % 6) * 0.08 + 's';
        flow.append(cell);
      }
    }

    function draw(items) {
      flow.replaceChildren();
      if (!items.length) {
        flow.append(el('div', 'gif-empty', 'Ничего не нашлось — попробуйте другое слово'));
        return;
      }
      items.forEach((item, i) => {
        const cell = el('button', 'gif-cell');
        cell.type = 'button';
        cell.style.animationDelay = Math.min(i, 12) * 0.03 + 's';
        const img = document.createElement('img');
        img.draggable = false;
        img.src = item.preview;
        img.alt = item.title;
        img.loading = 'lazy';
        cell.append(img);
        cell.addEventListener('click', () => onPick && onPick(item));
        flow.append(cell);
      });
    }

    async function run(query) {
      try {
        const res = await searchGifs(query);
        if (input.value.trim().toLowerCase() !== query) return;      // пока искали, набрали другое
        if (res.limited) {
          flow.replaceChildren(el('div', 'gif-empty',
            'На этот час запросы к сервису закончились. Найденное раньше по-прежнему открывается мгновенно.'));
          hint.textContent = 'Лимит запросов исчерпан, попробуйте позже';
          return;
        }
        draw(res.items);
        hint.textContent = res.fromCache
          ? 'Показано из памяти — запрос к сервису не потребовался'
          : 'Найдено по слову «' + query + '»';
      } catch (e) {
        flow.replaceChildren(el('div', 'gif-empty', 'Сервис не ответил: ' + (e.message || 'ошибка сети')));
        hint.textContent = 'Попробуйте ещё раз чуть позже';
      }
    }

    input.addEventListener('input', () => {
      clearTimeout(gifTimer);
      const query = input.value.trim().toLowerCase();
      if (query.length < KLIPY.minQuery) {
        flow.replaceChildren();
        hint.textContent = 'Наберите хотя бы два знака';
        return;
      }
      const cached = readCache(query);
      if (cached) {                       // уже искали — отвечаем мгновенно и бесплатно
        draw(cached);
        hint.textContent = 'Показано из памяти — запрос к сервису не потребовался';
        return;
      }
      // Ждём, пока человек допечатает. Скелет крутится всё это время, чтобы
      // не казалось, будто поиск не работает.
      skeleton();
      hint.textContent = 'Ищем «' + query + '»…';
      gifTimer = setTimeout(() => run(query), KLIPY.waitMs);
    });

    wrap.append(search, hint, grid, brand);
    return wrap;
  }

  return { EMOJI_GROUPS_TEST: EMOJI_GROUPS, expandGroup, mount, stickerBase: () => STICKER_BASE, STICKER_SETS, STICKER_MOODS, searchGifs, parseGifs, budget, readCache, writeCache };
})();
