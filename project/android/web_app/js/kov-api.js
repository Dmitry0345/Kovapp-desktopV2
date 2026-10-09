/* Ковчег, веб-версия — обёртка над fetch.
 * Токен уходит в заголовке Authorization (по нему nginx считает лимиты);
 * для <img>/<video>/<a download> адрес строится с ?token= через Kov.Api.fileUrl().
 */
window.Kov = window.Kov || {};

Kov.Api = (function () {
  const state = { host: '', token: '', userId: null };

  function setAuth(host, token, userId) {
    state.host = (host || '').replace(/\/+$/, '');
    state.token = token || '';
    state.userId = userId == null ? null : Number(userId);
    try {
      localStorage.setItem('kov.auth', JSON.stringify(state));
    } catch (e) { /* приватный режим */ }
  }

  function restore() {
    try {
      const saved = JSON.parse(localStorage.getItem('kov.auth') || 'null');
      if (saved && saved.token) Object.assign(state, saved);
    } catch (e) { /* ignore */ }
    return !!state.token;
  }

  function clear() {
    state.token = ''; state.userId = null;
    stopFileTokens();
    try { localStorage.removeItem('kov.auth'); } catch (e) { /* ignore */ }
  }

  function url(path, query) {
    const u = new URL(path.replace(/^\/+/, ''), state.host + '/');
    if (query) Object.keys(query).forEach(k => {
      if (query[k] !== undefined && query[k] !== null && query[k] !== '') u.searchParams.set(k, query[k]);
    });
    return u.toString();
  }

  // Адрес для тегов, которые не умеют слать заголовки
  /**
   * Адрес файла для <img>, <video> и <a download>. Заголовок Authorization
   * туда не подставить, поэтому токен уходит в строку запроса — а она видна
   * в логах прокси, истории браузера и заголовке Referer. Поэтому в ссылку
   * идёт не токен сессии, а отдельный короткий файловый токен: он живёт
   * 20 минут и не открывает остальной API.
   */
  // файловый токен обновляем заранее, чтобы уже открытые страницы не ломались
  let fileTokenTimer = null;
  let fileTokenUntil = 0;
  let fileTokenJob = null;
  let fileTokensOn = false;

  function scheduleFileToken(seconds) {
    clearTimeout(fileTokenTimer);
    fileTokenTimer = seconds > 0 ? setTimeout(refreshFileToken, seconds * 1000) : null;
  }

  function fileTokenUsable() { return !!state.fileToken && Date.now() < fileTokenUntil; }
  function fileTokenFresh() { return !!state.fileToken && Date.now() < fileTokenUntil - 60000; }

  function refreshFileToken() {
    if (fileTokenJob) return fileTokenJob;
    fileTokenJob = (async () => {
      try {
        const r = await request('POST', 'files/access-token');
        const life = Math.max(120, Number((r && r.expires_in) || 1200));
        state.fileToken = r && r.access_token ? r.access_token : null;
        fileTokenUntil = state.fileToken ? Date.now() + life * 1000 : 0;
        scheduleFileToken(Math.max(60, life - 120));
      } catch (e) {
        state.fileToken = null;
        fileTokenUntil = 0;
        if (e && e.status === 401) scheduleFileToken(0);
        else scheduleFileToken(e && (e.status === 404 || e.status === 405) ? 600 : 60);
      } finally {
        fileTokenJob = null;
        try { window.dispatchEvent(new CustomEvent('kov:file-token')); } catch (e) { /* */ }
      }
      return state.fileToken;
    })();
    return fileTokenJob;
  }

  function startFileTokens() { fileTokensOn = true; return refreshFileToken(); }
  function stopFileTokens() {
    fileTokensOn = false;
    scheduleFileToken(0);
    state.fileToken = null;
    fileTokenUntil = 0;
  }
  let fileTokenForcedAt = 0;
  function ensureFileToken(force) {
    if (!fileTokensOn || !state.token) return Promise.resolve(state.fileToken || null);
    if (force && Date.now() - fileTokenForcedAt > 30000) {
      fileTokenForcedAt = Date.now();
      return refreshFileToken();
    }
    if (fileTokenFresh()) return Promise.resolve(state.fileToken || null);
    return refreshFileToken();
  }
  function wakeFileTokens() {
    if (fileTokensOn && state.token && !document.hidden && !fileTokenFresh()) refreshFileToken();
  }
  document.addEventListener('visibilitychange', wakeFileTokens);
  window.addEventListener('focus', wakeFileTokens);
  window.addEventListener('online', wakeFileTokens);
  window.addEventListener('pageshow', wakeFileTokens);

  function mediaToken() {
    if (fileTokenUsable()) return state.fileToken;
    if (fileTokensOn && state.token) refreshFileToken();
    return state.token;
  }

  function fileUrl(path, query) {
    return url(path, Object.assign({}, query || {}, { token: mediaToken() }));
  }

  function wsUrl() {
    // Токен в адресе не передаём: он попадает в журналы сервера и обратного
    // прокси целиком, а это готовая чужая сессия. Отправляем его первым
    // сообщением после подключения — см. Kov.WS.connect.
    const u = new URL('ws/chat', state.host + '/');
    u.protocol = u.protocol === 'https:' ? 'wss:' : 'ws:';
    return u.toString();
  }

  async function request(method, path, opts) {
    opts = opts || {};
    const headers = Object.assign({}, opts.headers || {});
    if (state.token && !opts.anonymous) headers['Authorization'] = 'Bearer ' + state.token;
    let body = opts.body;
    if (opts.json !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(opts.json); }
    if (opts.form) { body = opts.form; }                     // FormData или URLSearchParams
    const res = await fetch(url(path, opts.query), { method, headers, body, signal: opts.signal });
    if (res.status === 401 && !opts.anonymous) {
      window.dispatchEvent(new CustomEvent('kov:unauthorized'));
    }
    if (res.status === 429 && !opts._retried) {
      // Сработал лимит nginx: подождать и повторить один раз
      const wait = Number(res.headers.get('Retry-After') || 1) * 1000;
      await new Promise(r => setTimeout(r, Math.min(wait, 5000)));
      return request(method, path, Object.assign({}, opts, { _retried: true }));
    }
    const ct = res.headers.get('content-type') || '';
    const data = ct.includes('application/json') ? await res.json().catch(() => null) : await res.text();
    if (!res.ok) {
      // detail разбирает Kov.UI.errorText: сюда кладём его как есть,
      // чтобы в сообщение пользователю не попал JSON целиком
      const err = new Error((data && typeof data.detail === 'string') ? data.detail : ('HTTP ' + res.status));
      if (data && data.detail && typeof data.detail !== 'string') err.detail = data.detail;
      err.status = res.status; err.data = data;
      throw err;
    }
    return data;
  }

  return {
    state, setAuth, restore, clear, url, fileUrl, wsUrl, request, startFileTokens, ensureFileToken, mediaToken,
    get: (p, o) => request('GET', p, o),
    post: (p, o) => request('POST', p, o),
    put: (p, o) => request('PUT', p, o),
    patch: (p, o) => request('PATCH', p, o),
    del: (p, o) => request('DELETE', p, o),
    async login(host, username, password) {
      setAuth(host, '', null);
      const form = new URLSearchParams({ username, password });
      const data = await request('POST', 'login', { form, anonymous: true,
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' } });
      setAuth(host, data.access_token, data.user_id);
      return data;
    },
  };
})();
