/* Тема оформления: светлая (как было), тёмная и «как в системе».
 *
 * Где хранится. Выбор пишется сразу в два места:
 *   localStorage kov.theme — чтобы применить тему до первой отрисовки и
 *     чтобы экран входа знал её ещё до того, как появится токен;
 *   настройка web.settings на сервере (та же таблица user_preferences, где
 *     лежат фильтры разделов) — чтобы тема и масштаб переезжали за
 *     сотрудником на другое устройство.
 * При входе серверное значение главнее: оно переписывает местное.
 *
 * Как переключается. Атрибут data-theme на <html>. Плавность включается
 * только на время перехода классом theme-anim, иначе постоянные переходы
 * мешали бы собственным анимациям разделов. Где браузер умеет переходы вида
 * (Chromium), новая тема дополнительно наплывает кругом от самой кнопки.
 */
window.Kov = window.Kov || {};

Kov.Theme = (function () {
  const KEY = 'kov.theme';
  const MODES = ['light', 'dark', 'auto'];
  const listeners = [];
  let mode = 'light';                       // что выбрал пользователь
  let touched = false;                      // переключал ли он тему руками в этот заход
  let pendingSave = false;                  // выбор сделан до входа — дошлём с токеном
  const media = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;

  function systemDark() { return !!(media && media.matches); }
  /** Тема, которая реально показывается: auto разворачивается в свет/тьму */
  function resolved(m) { const x = m || mode; return x === 'auto' ? (systemDark() ? 'dark' : 'light') : x; }

  function readStored() {
    try { const v = localStorage.getItem(KEY); if (MODES.includes(v)) return v; } catch (e) { /* */ }
    return 'light';                          // по умолчанию — прежнее оформление
  }
  function store(m) { try { localStorage.setItem(KEY, m); } catch (e) { /* приватный режим */ } }

  function paint() {
    const theme = resolved();
    document.documentElement.dataset.theme = theme;
    // цвет системной строки браузера на телефоне
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', theme === 'dark' ? '#1C1C1E' : '#FFFFFF');
    listeners.forEach(fn => { try { fn(theme, mode); } catch (e) { console.error(e); } });
  }

  /** Мягкое переключение: перекрашивание с переходом, плюс круг от кнопки */
  function animate(apply, origin) {
    const html = document.documentElement;
    const reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const ms = 420;
    if (reduced) return apply();

    // Переход вида снимает страницу целиком и наплывает кругом от кнопки.
    // Класс theme-anim при этом не нужен: во время перехода браузер
    // показывает снимок, а перекрашивание с задержкой сработало бы вторично.
    if (origin && document.startViewTransition) {
      const x = origin.x, y = origin.y;
      const r = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
      html.style.setProperty('--sweep-x', x + 'px');
      html.style.setProperty('--sweep-y', y + 'px');
      html.style.setProperty('--sweep-r', r + 'px');
      html.classList.add('theme-sweep');
      let t;
      try { t = document.startViewTransition(() => apply()); }
      catch (e) { html.classList.remove('theme-sweep'); return fade(); }
      t.finished.catch(() => {}).then(() => html.classList.remove('theme-sweep'));
      return;
    }
    fade();

    // Запасной путь: плавное перекрашивание переходами по цветовым свойствам
    function fade() {
      html.classList.add('theme-anim');
      apply();
      setTimeout(() => html.classList.remove('theme-anim'), ms + 60);
    }
  }

  /**
   * set(mode, opts) — 'light' | 'dark' | 'auto'.
   * opts: { animate, origin:{x,y}, persist — писать ли на сервер }
   */
  function set(next, opts) {
    opts = opts || {};
    if (!MODES.includes(next)) next = 'light';
    if (opts.explicit !== false) touched = true;
    const changed = resolved(next) !== resolved(mode);
    mode = next;
    store(mode);
    if (changed && opts.animate !== false) animate(paint, opts.origin);
    else paint();
    if (opts.persist !== false) save();      // без токена запомнится и уйдёт после входа
    return mode;
  }

  function toggle(opts) { return set(resolved() === 'dark' ? 'light' : 'dark', opts); }

  /** Сохранение на сервере рядом с масштабом — одной записью web.settings */
  async function save() {
    if (!window.Kov || !Kov.Store || !Kov.Api || !Kov.Api.state.token) { pendingSave = true; return; }
    pendingSave = false;
    try {
      const current = (await Kov.Store.pref('web.settings')) || {};
      await Kov.Store.setPref('web.settings', Object.assign({}, current, { theme: mode }));
    } catch (e) { /* сервер недоступен — местный выбор всё равно работает */ }
  }

  /** Тема из настроек сотрудника после входа; она главнее местной */
  function applyFromPrefs(prefs) {
    const value = prefs && prefs.theme;
    if (!MODES.includes(value)) { save(); return mode; }   // на сервере ещё нет записи
    // тему только что переключили на экране входа — этот выбор и уходит на сервер
    if ((touched || pendingSave) && value !== mode) { save(); return mode; }
    if (value === mode) return mode;
    mode = value; store(mode); paint();
    return mode;
  }

  function onChange(fn) { listeners.push(fn); return () => { const i = listeners.indexOf(fn); if (i >= 0) listeners.splice(i, 1); }; }

  function init() {
    mode = readStored();
    paint();
    if (media && media.addEventListener) media.addEventListener('change', () => { if (mode === 'auto') paint(); });
  }

  return { init, set, toggle, save, applyFromPrefs, onChange,
           get mode() { return mode; }, get current() { return resolved(); }, MODES };
})();
