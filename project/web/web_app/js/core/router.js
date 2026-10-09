/* Маршруты по хэшу: #/tasks, #/tasks/12, #/messenger/5 … Параметры — числа или строки. */
window.Kov = window.Kov || {};

Kov.Router = (function () {
  const routes = [];
  let current = null;
  let notFound = null;

  function add(pattern, handler) {
    // '#/tasks/:id' → регулярное выражение с именованными группами
    const keys = [];
    const re = new RegExp('^' + pattern.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/:([a-zA-Z_]+)/g, (_m, k) => { keys.push(k); return '([^/]+)'; }) + '/?$');
    routes.push({ re, keys, handler, pattern });
  }

  function parse(hash) {
    const clean = (hash || '#/').split('?')[0];
    for (const r of routes) {
      const m = r.re.exec(clean);
      if (m) {
        const params = {};
        r.keys.forEach((k, i) => { const v = decodeURIComponent(m[i + 1]); params[k] = /^\d+$/.test(v) ? Number(v) : v; });
        return { route: r, params, hash: clean };
      }
    }
    return null;
  }

  /** Поменять адрес, не перерисовывая раздел: нужно, когда раздел уже открыт
   *  и просто хочется, чтобы ссылка была правильной */
  function setHash(hash) {
    hash = hash.startsWith('#') ? hash : '#' + hash;
    if (location.hash === hash) return;
    history.replaceState(null, '', hash);
    current = parse(location.hash);
  }

  function go(hash, replace) {
    hash = hash.startsWith('#') ? hash : '#' + hash;
    const same = location.hash === hash || (!location.hash && hash === '#/');
    if (replace) history.replaceState(null, '', hash); else location.hash = hash;
    // hashchange не срабатывает, если адрес совпал — перерисовываем сами
    if (replace || same) dispatch();
  }

  function dispatch() {
    const found = parse(location.hash);
    current = found;
    try {
      if (found) found.route.handler(found.params, found.hash);
      else if (notFound) notFound();
    } catch (e) {
      console.error('Ошибка перехода', e);
      if (window.Kov && Kov.UI) Kov.UI.toast('Не удалось открыть раздел: ' + Kov.UI.errorText(e), 'error');
    }
  }

  function start(fallback) {
    notFound = fallback;
    window.addEventListener('hashchange', dispatch);
    dispatch();
  }

  return { add, go, setHash, start, dispatch, get current() { return current; } };
})();
