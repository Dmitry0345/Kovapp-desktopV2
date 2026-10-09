/* Внутристраничная часть уведомлений: всплывашки, звук, счётчик во вкладке.
 * Работает в любом режиме, в том числе при HTTP. Оформление — в css/notify.css,
 * цвета берутся из дизайн-системы приложения (theme.py десктопа).
 */
window.Kov = window.Kov || {};

Kov.NotifyUI = (function () {
  const MAX_TOASTS = 4;
  // На телефоне карточка занимает заметную часть экрана, поэтому их не больше
  // двух: третья вытесняет самую старую
  const isPhone = () => window.matchMedia && window.matchMedia('(max-width: 640px)').matches;
  const maxToasts = () => (isPhone() ? 2 : MAX_TOASTS);
  const DEFAULT_TTL = 6000;
  const SOUND_THROTTLE = 1500;

  let container = null;
  let baseTitle = document.title || 'Kovapp';
  let badgeCount = 0;
  let lastSoundAt = 0;
  let audioCtx = null;
  let audioUnlocked = false;
  let navigateFn = function (url) { if (url) location.hash = url.replace(/^#?/, '#'); };

  // ── всплывашки ─────────────────────────────────────────────────────────────

  function ensureContainer() {
    if (container) return container;
    container = document.createElement('div');
    container.id = 'kov-toasts';
    container.setAttribute('role', 'status');
    container.setAttribute('aria-live', 'polite');
    document.body.appendChild(container);
    return container;
  }

  const ICONS = {
    chat: '<svg viewBox="0 0 24 24"><path d="M4 4h16v11H8l-4 4V4z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>',
    bell: '<svg viewBox="0 0 24 24"><path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4l2-2zM10 20a2 2 0 0 0 4 0" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>',
    group: '<svg viewBox="0 0 24 24"><circle cx="9" cy="8" r="3.5" fill="none" stroke="currentColor" stroke-width="2"/><path d="M2.5 19a6.5 6.5 0 0 1 13 0M16 5a3 3 0 0 1 0 6M21.5 19a5.5 5.5 0 0 0-4-5.3" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
    warn: '<svg viewBox="0 0 24 24"><path d="M12 3 2 21h20L12 3zM12 10v5M12 18h.01" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/></svg>',
    ok: '<svg viewBox="0 0 24 24"><path d="M4 12l5 5L20 6" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  };

  /**
   * show({title, body, where, kind: 'chat'|'notification'|'group'|'warning'|'success', url, tag, ttl, onClick, avatarUrl, initials})
   * Возвращает функцию закрытия.
   */
  function show(opts) {
    const root = ensureContainer();
    // Та же метка — обновляем существующую всплывашку, а не плодим вторую
    if (opts.tag) {
      const old = root.querySelector('[data-tag="' + CSS.escape(opts.tag) + '"]');
      if (old) old._close(true);
    }
    // Лишние карточки убираем сразу. Раньше здесь был вечный цикл: close()
    // снимает узел следующим тактом, а условие проверялось в этом же — и при
    // очередной всплывашке сверх лимита страница намертво зависала.
    let guard = 0;
    while (root.children.length >= maxToasts() && guard++ < 20) {
      const first = root.firstChild;
      if (first._close) first._close(true);
      if (first.parentNode === root) root.removeChild(first);
    }

    const kind = opts.kind || 'notification';
    const el = document.createElement('div');
    el.className = 'kov-toast kov-toast--' + kind;
    if (opts.tag) el.dataset.tag = opts.tag;
    el.setAttribute('role', 'button');
    el.tabIndex = 0;

    const iconName = { chat: 'chat', notification: 'bell', group: 'group', warning: 'warn', success: 'ok' }[kind] || 'bell';
    const avatar = opts.avatarUrl
      ? '<img class="kov-toast__avatar" src="' + escapeAttr(opts.avatarUrl) + '" alt="">'
      : (opts.initials ? '<span class="kov-toast__avatar kov-toast__avatar--text" style="background:' + colorFor(opts.initials) + '">' + escapeHtml(opts.initials) + '</span>' : '');

    el.innerHTML =
      '<span class="kov-toast__icon">' + (avatar || ICONS[iconName]) + '</span>' +
      '<span class="kov-toast__text">' +
        '<span class="kov-toast__title">' + escapeHtml(opts.title || '') + '</span>' +
        // строка «в группе «…»»: сразу видно, что сообщение не личное
        (opts.where ? '<span class="kov-toast__where">' + ICONS.group + escapeHtml(opts.where) + '</span>' : '') +
        (opts.body ? '<span class="kov-toast__body">' + escapeHtml(opts.body) + '</span>' : '') +
        ((opts.url || opts.onClick) ? '<span class="kov-toast__hint">Нажмите, чтобы открыть</span>' : '') +
      '</span>' +
      '<button class="kov-toast__close" type="button" aria-label="Закрыть">×</button>';

    let timer = null;
    let remaining = opts.ttl || DEFAULT_TTL;
    let startedAt = 0;
    let closed = false;

    function close(silent) {
      if (closed) return; closed = true;
      clearTimeout(timer);
      el.classList.add('kov-toast--out');
      setTimeout(() => el.remove(), silent ? 0 : 220);
    }
    function startTimer() {
      if (document.visibilityState !== 'visible') return; // пока вкладка не видна, не исчезает
      startedAt = Date.now();
      timer = setTimeout(close, remaining);
    }
    function pauseTimer() {
      if (!timer) return;
      clearTimeout(timer); timer = null;
      remaining = Math.max(1500, remaining - (Date.now() - startedAt));
    }
    el._close = close;
    el._pause = pauseTimer;
    el._resume = startTimer;

    el.querySelector('.kov-toast__close').addEventListener('click', e => { e.stopPropagation(); close(); });
    el.addEventListener('click', () => {
      close();
      if (opts.onClick) opts.onClick(); else if (opts.url) navigateFn(opts.url);
    });
    el.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); el.click(); } });
    el.addEventListener('mouseenter', pauseTimer);
    el.addEventListener('mouseleave', startTimer);

    // Смахивание на телефоне
    let touchX = null;
    el.addEventListener('touchstart', e => { touchX = e.touches[0].clientX; pauseTimer(); }, { passive: true });
    el.addEventListener('touchmove', e => {
      if (touchX == null) return;
      const dx = e.touches[0].clientX - touchX;
      el.style.transform = 'translateX(' + dx + 'px)';       // смахнуть можно в любую сторону
      el.style.opacity = String(Math.max(.3, 1 - Math.abs(dx) / 260));
    }, { passive: true });
    el.addEventListener('touchend', e => {
      const dx = (e.changedTouches[0].clientX - (touchX || 0));
      touchX = null;
      if (Math.abs(dx) > 70) close();
      else { el.style.transform = ''; el.style.opacity = ''; startTimer(); }
    });

    root.appendChild(el);
    requestAnimationFrame(() => el.classList.add('kov-toast--in'));
    startTimer();
    return close;
  }

  document.addEventListener('visibilitychange', () => {
    if (!container) return;
    Array.from(container.children).forEach(el => document.visibilityState === 'visible' ? el._resume() : el._pause());
  });

  // ── звук (WebAudio, без файлов) ───────────────────────────────────────────

  function unlockAudio() {
    try {
      if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      if (audioCtx.state === 'suspended') audioCtx.resume();
      audioUnlocked = true;
    } catch (e) { /* нет WebAudio */ }
  }
  // Браузер разрешает звук только после действия пользователя — ловим первое
  ['pointerdown', 'keydown', 'touchstart'].forEach(ev => window.addEventListener(ev, unlockAudio, { once: true, passive: true }));

  function playChime(kind) {
    if (!audioUnlocked || !audioCtx) return false;
    const now = Date.now();
    if (now - lastSoundAt < SOUND_THROTTLE) return false;
    lastSoundAt = now;
    try {
      if (audioCtx.state === 'suspended') audioCtx.resume();
      const t = audioCtx.currentTime;
      // Чат — два восходящих тона, уведомление — один более низкий
      const notes = kind === 'chat' ? [[880, 0], [1174.7, 0.10]] : [[659.3, 0], [659.3, 0.16]];
      notes.forEach(([freq, delay]) => {
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        osc.type = 'sine';
        osc.frequency.value = freq;
        gain.gain.setValueAtTime(0.0001, t + delay);
        gain.gain.exponentialRampToValueAtTime(0.22, t + delay + 0.015);
        gain.gain.exponentialRampToValueAtTime(0.0001, t + delay + 0.38);
        osc.connect(gain).connect(audioCtx.destination);
        osc.start(t + delay);
        osc.stop(t + delay + 0.4);
      });
      return true;
    } catch (e) { return false; }
  }

  // ── счётчик во вкладке: заголовок + иконка ────────────────────────────────

  function setBaseTitle(title) { baseTitle = title; applyTitle(); }
  function applyTitle() { document.title = badgeCount > 0 ? '(' + badgeCount + ') ' + baseTitle : baseTitle; }

  function faviconSvg(count) {
    const label = count > 99 ? '99+' : String(count);
    const badge = count > 0
      ? '<circle cx="46" cy="18" r="15" fill="#E53935"/><text x="46" y="23" font-family="Arial" font-size="' + (label.length > 2 ? 13 : 17) + '" font-weight="700" fill="#fff" text-anchor="middle">' + label + '</text>'
      : '';
    return 'data:image/svg+xml,' + encodeURIComponent(
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">' +
      '<rect x="2" y="2" width="60" height="60" rx="14" fill="#546E7A"/>' +
      '<path d="M22 16v32M22 32l17-16M24 34l17 14" fill="none" stroke="#fff" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>' +
      badge + '</svg>');
  }

  function applyFavicon() {
    let link = document.querySelector('link[rel="icon"]');
    if (!link) { link = document.createElement('link'); link.rel = 'icon'; document.head.appendChild(link); }
    link.type = 'image/svg+xml';
    link.href = faviconSvg(badgeCount);
  }

  function setBadge(count) {
    badgeCount = Math.max(0, count | 0);
    applyTitle();
    applyFavicon();
    // Значок на иконке установленного приложения (Android/Windows), где поддерживается
    if (navigator.setAppBadge) {
      (badgeCount ? navigator.setAppBadge(badgeCount) : navigator.clearAppBadge()).catch(() => {});
    }
  }

  // ── утилиты ──────────────────────────────────────────────────────────────

  const AVATAR_COLORS = ['#5C6BC0', '#26A69A', '#EF6C00', '#8E24AA', '#039BE5', '#43A047', '#D81B60', '#6D4C41', '#00897B', '#7CB342'];
  function colorFor(text) {
    let h = 0; for (const ch of String(text)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    return AVATAR_COLORS[h % AVATAR_COLORS.length];
  }
  function escapeHtml(s) { return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
  function escapeAttr(s) { return escapeHtml(s); }
  function initials(lastName, firstName) {
    return ((lastName || '')[0] || '') + ((firstName || '')[0] || '') || '?';
  }

  return {
    show, playChime, unlockAudio, setBadge, setBaseTitle, initials, colorFor,
    setNavigate(fn) { navigateFn = fn; },
    get badgeCount() { return badgeCount; },
  };
})();
