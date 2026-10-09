/*
 * Симулятор Kovapp: телефон в окне браузера. Играет роль Android-части
 * приложения — ту же, что MainActivity, Notifier и DirectPushService в APK:
 * жизненный цикл приложения, системные уведомления, собственный канал
 * уведомлений к серверу (/ws/native-push), кнопки «Назад», «Домой»,
 * «Недавние», сеть, разрешения, загрузки. Страница приложения подключается
 * к симулятору через эмулятор моста (tools/devbridge/dev-bridge.js).
 */
(function () {
  'use strict';
  const $ = s => document.querySelector(s);
  const DEVICES = {
    small: { name: 'Маленький экран — 320×640', w: 320, h: 640 },
    normal: { name: 'Обычный Android — 360×800', w: 360, h: 800 },
    tall: { name: 'Современный — 393×873', w: 393, h: 873 },
    large: { name: 'Большой — 412×915', w: 412, h: 915 },
    tablet: { name: 'Планшет — 800×1280', w: 800, h: 1280 },
  };
  const STATUS_H = 24, NAV_H = 48;

  // ── настройки симулятора и «данные приложения» ────────────────────────
  const keep = {
    get(k, d) { try { const v = localStorage.getItem('kovsim.' + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem('kovsim.' + k, JSON.stringify(v)); } catch (e) { /* */ } },
    del(k) { try { localStorage.removeItem('kovsim.' + k); } catch (e) { /* */ } },
  };
  const state = {
    info: { app_path: '/app/', upstream: '', mock: false },
    app: 'closed',                       // closed | foreground | background
    online: true,
    permission: keep.get('permission', 'default'),
    battery: keep.get('battery', false),
    pushEnabled: keep.get('pushEnabled', false),
    device: keep.get('device', null),   // { id, secret, userId, host }
    serverSupported: null,
    session: null,
  };
  const frames = { main: null, children: [] };
  const devs = new Map();                // окно страницы → её эмулятор моста
  const themes = new Map();              // окно страницы → её theme-color
  const seen = new Set();                // id уже показанных уведомлений

  // ── журнал моста ──────────────────────────────────────────────────────
  function log(kind, text) {
    const box = $('#log');
    const line = document.createElement('div');
    line.className = kind || '';
    line.textContent = new Date().toTimeString().slice(0, 8) + '  ' + text;
    box.append(line);
    while (box.childNodes.length > 300) box.firstChild.remove();
    box.scrollTop = box.scrollHeight;
  }
  function short(obj) {
    const s = JSON.stringify(obj, (k, v) => (k === 'token' || k === 'secret' || k === 'data') && v ? '…' : v);
    return s && s.length > 140 ? s.slice(0, 137) + '…' : (s || '');
  }

  // ── телефон: размер, поворот, масштаб ─────────────────────────────────
  // экран приложения (между строкой состояния и панелью навигации), в CSS-пикселях
  function appSize() {
    const dev = DEVICES[keep.get('model', 'normal')] || DEVICES.normal;
    const land = keep.get('landscape', false);
    return { width: land ? dev.h : dev.w, height: (land ? dev.w : dev.h) - STATUS_H - NAV_H };
  }

  // meta viewport с числовой шириной: Android рисует страницу этой ширины и растягивает на экран
  const viewports = new Map();       // окно страницы → заданная ширина (или 0)
  function fitFrame(f) {
    const want = f && viewports.get(f.contentWindow);
    const size = appSize();
    if (!f) return;
    if (want && want < size.width) {
      const k = size.width / want;
      f.style.width = want + 'px';
      f.style.height = (size.height / k) + 'px';
      f.style.transform = 'scale(' + k + ')';
      f.style.transformOrigin = 'top left';
    } else {
      f.style.width = f.style.height = f.style.transform = '';
    }
  }
  function fitFrames() { [frames.main].concat(frames.children).forEach(f => f && fitFrame(f)); }

  function layout() {
    const dev = DEVICES[keep.get('model', 'normal')] || DEVICES.normal;
    const land = keep.get('landscape', false);
    const w = land ? dev.h : dev.w, h = land ? dev.w : dev.h;
    const screen = $('#screen');
    screen.style.width = w + 'px';
    screen.style.height = h + 'px';
    const zoom = keep.get('zoom', 'fit');
    const stage = $('#stage');
    const scale = zoom === 'fit' ? Math.min(1, (stage.clientHeight - 40) / (h + 24), (stage.clientWidth - 30) / (w + 24)) : Number(zoom);
    $('#phone').style.transform = 'scale(' + Math.max(0.3, scale) + ')';
    fitFrames();
  }

  function setupControls() {
    const sel = $('#device');
    Object.keys(DEVICES).forEach(k => { const o = document.createElement('option'); o.value = k; o.textContent = DEVICES[k].name; sel.append(o); });
    sel.value = keep.get('model', 'normal');
    sel.onchange = () => { keep.set('model', sel.value); layout(); };
    $('#landscape').checked = keep.get('landscape', false);
    $('#landscape').onchange = e => { keep.set('landscape', e.target.checked); layout(); };
    $('#zoom').value = String(keep.get('zoom', 'fit'));
    $('#zoom').onchange = e => { keep.set('zoom', e.target.value === 'fit' ? 'fit' : Number(e.target.value)); layout(); };
    window.addEventListener('resize', layout);

    $('#net').onclick = e => { const b = e.target.closest('button'); if (b) setNet(b.dataset.v); };
    $('#perm').value = state.permission;
    $('#perm').onchange = e => setPermission(e.target.value, true);
    $('#battery').checked = state.battery;
    $('#battery').onchange = e => { state.battery = e.target.checked; keep.set('battery', state.battery); };
    $('#testPush').onclick = testPush;
    $('#colleague').onclick = colleagueMessage;
    $('#restart').onclick = () => { killApp(); launch(''); };
    $('#wipe').onclick = wipeAppData;
    $('#clearShade').onclick = () => { $('#shade').innerHTML = ''; renderEmpty(); };
    $('#clearLog').onclick = () => { $('#log').innerHTML = ''; };
    $('#navbar').onclick = e => { const b = e.target.closest('button'); if (!b) return; ({ back, home, recent })[b.dataset.k](); };
    $('#launcher').onclick = () => launch('');
    setInterval(tick, 15000);
    tick();
  }

  function tick() { $('#clock').textContent = new Date().toTimeString().slice(0, 5); }

  // ── строка состояния и панель навигации по цвету страницы ─────────────
  function parseColor(css) {
    const m = /^#([0-9a-f]{6})$/i.exec(css || '');
    if (m) { const n = parseInt(m[1], 16); return [n >> 16, (n >> 8) & 255, n & 255]; }
    const r = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(css || '');
    return r ? [+r[1], +r[2], +r[3]] : [255, 255, 255];
  }
  function topFrame() { return frames.children[frames.children.length - 1] || frames.main; }
  function paintBars() {
    const onApp = state.app === 'foreground';
    const top = topFrame();
    const color = onApp ? ((top && themes.get(top.contentWindow)) || '#FFFFFF') : '#132A32';
    const [r, g, b] = parseColor(color);
    const dark = (0.299 * r + 0.587 * g + 0.114 * b) / 255 <= 0.6;
    for (const el of [$('#status'), $('#navbar')]) {
      el.style.background = color;
      el.classList.toggle('dark', dark);
    }
  }

  // ── приложение: запуск, «Домой», «Недавние», закрытие ─────────────────
  const appUrl = route => state.info.app_path + 'index.html' + (route || '');

  function launch(route, tag) {
    hideRecents();
    if (state.app === 'closed') {
      const f = document.createElement('iframe');
      f.className = 'main';
      f.src = appUrl(route);
      $('#appArea').append(f);
      frames.main = f;
      log('ev', 'приложение запущено' + (route ? ' с экраном ' + route : ''));
      setApp('foreground');
      return;
    }
    setApp('foreground');
    if (route) emit('navigate', { url: route, tag: tag || '' });
  }

  function setApp(s) {
    state.app = s;
    $('#home').hidden = s === 'foreground';
    $('#appArea').style.visibility = s === 'foreground' ? 'visible' : 'hidden';
    paintBars();
    // видимость страницы, как у WebView при onPause/onResume
    for (const w of devs.keys()) {
      try { w.document.dispatchEvent(new w.Event('visibilitychange')); } catch (e) { /* страница ушла */ }
    }
  }

  function home() {
    if (state.app !== 'foreground') { hideRecents(); return; }
    setApp('background');
    log('ev', 'приложение свёрнуто');
  }

  function killApp() {
    frames.children.forEach(f => f.remove());
    frames.children = [];
    if (frames.main) frames.main.remove();
    frames.main = null;
    devs.clear();
    state.app = 'closed';
    setApp('closed');
    $('#appArea').style.visibility = 'hidden';
    log('ev', 'приложение закрыто (процесс завершён); канал уведомлений продолжает работать');
  }

  function recent() {
    const box = $('#recents');
    if (!box.hidden) { hideRecents(); return; }
    box.innerHTML = '';
    if (state.app === 'closed') {
      const p = document.createElement('p');
      p.textContent = 'Нет недавних приложений';
      box.append(p);
    } else {
      const card = document.createElement('div');
      card.className = 'rcard';
      card.innerHTML = '<b>Kovapp</b><span>нажмите, чтобы вернуться</span>';
      card.onclick = () => { hideRecents(); launch(''); };
      const close = document.createElement('button');
      close.type = 'button';
      close.textContent = 'Смахнуть — закрыть приложение';
      close.onclick = () => { hideRecents(); killApp(); };
      box.append(card, close);
    }
    box.hidden = false;
  }
  function hideRecents() { $('#recents').hidden = true; }

  function back() {
    if (!$('#sysdialog').hidden) { closeDialog(null); return; }
    if (!$('#recents').hidden) { hideRecents(); return; }
    if (state.app !== 'foreground') return;
    const child = frames.children[frames.children.length - 1];
    const target = child || frames.main;
    if (!target) return;
    const w = target.contentWindow;
    let v = false;
    try { v = w.KovNative && w.KovNative.onBack ? w.KovNative.onBack() : false; } catch (e) { v = false; }
    if (v === true) return;
    const canBack = !!(w && w.navigation && w.navigation.canGoBack);
    if (child) { if (canBack) w.history.back(); else closeChild(child); return; }
    if (v !== 'exit' && canBack) w.history.back();
    else home();
  }

  // окна документов открываются поверх приложения, как дочерние WebView
  function openChild(url, from) {
    let abs;
    try { abs = new URL(url || 'about:blank', from.location.href); } catch (e) { return null; }
    if (abs.href !== 'about:blank' && abs.origin !== location.origin) {
      log('ev', 'внешняя ссылка → системный браузер: ' + abs.href);
      return window.open(abs.href, '_blank', 'noopener') || null;
    }
    const f = document.createElement('iframe');
    f.className = 'child';
    f.src = abs.href;
    $('#appArea').append(f);
    frames.children.push(f);
    log('ev', 'окно поверх приложения: ' + abs.pathname + abs.search.slice(0, 60));
    return f.contentWindow;
  }
  function closeChild(f) {
    const i = frames.children.indexOf(f);
    if (i >= 0) frames.children.splice(i, 1);
    try { devs.delete(f.contentWindow); themes.delete(f.contentWindow); } catch (e) { /* */ }
    f.remove();
    paintBars();
  }
  function frameOf(win) {
    if (frames.main && frames.main.contentWindow === win) return frames.main;
    return frames.children.find(f => f.contentWindow === win) || null;
  }

  // ── события странице (как Bridge.emit в APK) ──────────────────────────
  function emit(event, data) {
    const w = frames.main && frames.main.contentWindow;
    const dev = w && devs.get(w);
    if (!dev) { log('err', 'страница ещё не готова для события ' + event); return; }
    log('ev', '← ' + event + ' ' + short(data));
    dev.emit(event, data);
  }

  // ── системный диалог и сообщение ──────────────────────────────────────
  let dialogDone = null;
  function dialog(text, buttons) {
    const box = $('#sysdialog');
    box.innerHTML = '';
    const inner = document.createElement('div');
    inner.className = 'box';
    const p = document.createElement('p');
    p.textContent = text;
    inner.append(p);
    return new Promise(resolve => {
      dialogDone = resolve;
      buttons.forEach(([label, value, primary]) => {
        const b = document.createElement('button');
        b.type = 'button';
        b.textContent = label;
        if (primary) b.className = 'primary';
        b.onclick = () => closeDialog(value);
        inner.append(b);
      });
      box.append(inner);
      box.hidden = false;
    });
  }
  function closeDialog(value) {
    $('#sysdialog').hidden = true;
    const d = dialogDone;
    dialogDone = null;
    if (d) d(value);
  }
  let toastTimer = null;
  function toast(text) {
    const t = $('#systoast');
    t.textContent = text;
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, 3500);
  }

  // ── разрешение на уведомления ─────────────────────────────────────────
  function setPermission(p, fromSettings) {
    state.permission = p;
    keep.set('permission', p);
    $('#perm').value = p;
    if (p !== 'granted' && fromSettings) channelStop();
    if (frames.main && devs.get(frames.main.contentWindow)) emit('permission', p);
    if (p === 'granted' && state.pushEnabled) pushSync();
  }
  async function askPermission() {
    if (state.permission === 'granted') return 'granted';
    if (state.permission === 'denied') {
      toast('Уведомления запрещены в настройках телефона — переключатель слева');
      return 'denied';
    }
    const v = await dialog('Разрешить приложению «Kovapp» отправлять вам уведомления?',
      [['Разрешить', 'granted', true], ['Не разрешать', 'denied', false]]);
    setPermission(v || 'denied');
    return state.permission;
  }

  // ── системные уведомления (Notifier в APK) ────────────────────────────
  function renderEmpty() {
    const shade = $('#shade');
    const empty = shade.querySelector('.empty');
    if (!shade.querySelector('.note') && !empty) {
      const p = document.createElement('p');
      p.className = 'empty';
      p.textContent = 'Уведомлений нет';
      shade.append(p);
    } else if (shade.querySelector('.note') && empty) empty.remove();
  }
  const safeRoute = url => (/^#\/[A-Za-z0-9_/-]{0,200}$/.test(url || '') ? url : '#/notifications');

  function showSystem(n) {
    if (state.permission !== 'granted') { log('err', 'уведомление не показано: нет разрешения'); return false; }
    const tag = String(n.tag || 'kovcheg');
    const shade = $('#shade');
    const old = shade.querySelector('.note[data-tag="' + CSS.escape(tag) + '"]');
    const el = document.createElement('div');
    const kind = n.kind === 'chat' || n.kind === 'group' ? n.kind : 'notification';
    el.className = 'note ' + kind;
    el.dataset.tag = tag;
    el.innerHTML = '<small></small><b></b><p></p><button type="button" class="x" title="Смахнуть">✕</button>';
    el.querySelector('small').textContent = 'Kovapp · ' + new Date().toTimeString().slice(0, 5) + (kind === 'chat' ? ' · сообщения' : kind === 'group' ? ' · группы' : ' · уведомления');
    el.querySelector('b').textContent = n.title || 'Kovapp';
    el.querySelector('p').textContent = n.body || '';
    const route = safeRoute(n.url);
    el.onclick = e => {
      if (e.target.classList.contains('x')) { el.remove(); renderEmpty(); return; }
      el.remove();
      renderEmpty();
      log('ev', 'нажато уведомление → ' + route);
      launch(route, tag);
    };
    if (old) old.replaceWith(el); else shade.prepend(el);
    renderEmpty();
    if (state.app !== 'foreground') headsUp(n, () => el.click());
    return true;
  }
  let headsTimer = null;
  function headsUp(n, onTap) {
    const h = $('#headsup');
    h.innerHTML = '<small>Kovapp</small><b></b><p></p>';
    h.querySelector('b').textContent = n.title || 'Kovapp';
    h.querySelector('p').textContent = n.body || '';
    h.onclick = () => { h.hidden = true; onTap(); };
    h.hidden = false;
    clearTimeout(headsTimer);
    headsTimer = setTimeout(() => { h.hidden = true; }, 4500);
  }
  function closeSystem(tag) {
    const el = $('#shade').querySelector('.note[data-tag="' + CSS.escape(String(tag)) + '"]');
    if (el) el.remove();
    renderEmpty();
  }

  // ── запросы к серверу от имени приложения ─────────────────────────────
  async function api(method, path, body, token) {
    const s = state.session;
    const host = (s && s.host) || location.origin;
    const headers = { Accept: 'application/json' };
    const t = token !== undefined ? token : (s && s.token);
    if (t) headers.Authorization = 'Bearer ' + t;
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    const res = await fetch(host.replace(/\/+$/, '') + '/' + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw Object.assign(new Error(data.detail || ('код ' + res.status)), { status: res.status });
    return data;
  }

  // ── собственный канал уведомлений (DirectPushService в APK) ──────────
  const channel = { ws: null, want: false, timer: null, backoff: 1000 };
  function chanStatus(text) { $('#chan').textContent = text; }
  function deviceId() {
    let id = keep.get('deviceId', null);
    if (!/^[0-9a-f]{32}$/.test(id || '')) {
      id = Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('');
      keep.set('deviceId', id);
    }
    return id;
  }
  function showDevice() { $('#devid').textContent = state.device ? state.device.id.slice(0, 8) + '… (сотрудник ' + state.device.userId + ')' : '—'; }

  function channelStart() { channel.want = true; clearTimeout(channel.timer); channelConnect(); }
  function channelStop() {
    channel.want = false;
    clearTimeout(channel.timer);
    if (channel.ws) { try { channel.ws.close(); } catch (e) { /* */ } }
    channel.ws = null;
    chanStatus(state.device ? 'остановлен' : 'выключен');
  }
  function channelConnect() {
    const d = state.device;
    if (!channel.want || !d) return;
    // одно соединение за раз, как единственный поток службы в APK: сервер закрывает
    // прежнее соединение того же телефона, и два сразу выбивали бы друг друга
    if (channel.ws && channel.ws.readyState <= 1) return;
    clearTimeout(channel.timer);
    const ws = new WebSocket(d.host.replace(/^http/, 'ws').replace(/\/+$/, '') + '/ws/native-push');
    channel.ws = ws;
    chanStatus('подключение…');
    ws.onopen = () => ws.send(JSON.stringify({ type: 'hello', device_id: d.id, secret: d.secret, app_version: 'simulator', once: false }));
    ws.onmessage = e => {
      let m;
      try { m = JSON.parse(e.data); } catch (x) { return; }
      if (m.type === 'welcome') { channel.backoff = 1000; chanStatus('на связи'); log('', 'канал уведомлений: на связи (ping ' + m.ping + ' с)'); }
      else if (m.type === 'push') { onPush(m.payload || {}, m.id); ws.send(JSON.stringify({ type: 'ack', id: m.id })); }
      else if (m.type === 'ping') ws.send('{"type":"pong"}');
      else if (m.type === 'error' && m.reason === 'unauthorized') {
        log('err', 'сервер не узнал устройство — регистрация сброшена (как в APK)');
        state.device = null;
        keep.del('device');
        showDevice();
        channel.want = false;
      }
    };
    ws.onclose = () => {
      if (channel.ws !== ws) return;          // закрылось старое соединение — новое уже есть
      channel.ws = null;
      if (!channel.want) return;
      chanStatus('нет связи — повтор через ' + Math.round(channel.backoff / 1000) + ' с');
      channel.timer = setTimeout(channelConnect, channel.backoff);
      channel.backoff = Math.min(channel.backoff * 2, 30000);
    };
  }

  function onPush(p, envelopeId) {
    const d = state.device;
    const id = p.id || envelopeId;
    if (!d || Number(p.uid) !== Number(d.userId)) { log('err', 'уведомление другому сотруднику — отброшено'); return; }
    if (id && seen.has(id)) return;
    if (id) seen.add(id);
    log('ev', 'канал: уведомление «' + (p.title || '') + '»');
    if (p.probe) api('POST', 'push/ack', { probe: p.probe, endpoint: 'native:' + d.id }, null).catch(() => {});
    if (state.app === 'foreground' && frames.main && devs.get(frames.main.contentWindow)) {
      const payload = Object.assign({}, p);
      delete payload.uid;
      emit('push', { payload });       // открытое приложение показывает само
      return;
    }
    showSystem({ title: p.title || 'Ковчег', body: p.body || '', tag: p.tag || 'kovcheg', url: p.url,
      kind: p.kind === 'chat' ? 'chat' : p.kind === 'group_added' ? 'group' : 'notification' });
  }

  async function loadConfig() {
    try {
      const c = await api('GET', 'push/native/config');
      state.serverSupported = !!c.enabled;
      state.serverError = null;
    } catch (e) {
      // «модуля нет» — только ответ 404; вход и связь — другое (как в APK)
      if (e.status === 404) { state.serverSupported = false; state.serverError = null; }
      else state.serverError = e.status === 401 || e.status === 403 ? 'сервер не принял вход — выйдите и войдите заново'
        : (e.status ? 'сервер ответил кодом ' + e.status : 'нет связи с сервером');
    }
    return state.serverSupported === true;
  }

  async function register() {
    const s = state.session;
    if (!s || !s.token) return { ok: false, push: false, reason: 'Нет входа' };
    if (!await loadConfig()) {
      if (state.serverSupported === null) {
        return { ok: false, push: false, reason: 'Не удалось проверить уведомления на сервере: ' + state.serverError + '. Попробуйте ещё раз.' };
      }
      return { ok: true, push: false, reason: 'Для уведомлений при закрытом приложении обновите сервер (модуль native_push)' };
    }
    try {
      const id = deviceId();
      const r = await api('POST', 'push/native/register', { device_id: id, provider: 'direct', platform: 'android', model: 'Симулятор на ПК', app_version: 'simulator' });
      state.device = { id, secret: r.secret, userId: s.userId, host: s.host };
      keep.set('device', state.device);
      showDevice();
      channelStop();
      channelStart();
      return { ok: true, push: true, mode: 'direct' };
    } catch (e) {
      return { ok: true, push: false, reason: 'Сервер не принял регистрацию: ' + e.message };
    }
  }

  function pushState() {
    const d = state.device, s = state.session;
    const registered = !!(d && s && Number(d.userId) === Number(s.userId));
    return { enabled: state.pushEnabled, registered, mode: registered ? 'direct' : '', serverSupported: state.serverSupported,
      serverError: state.serverError || null,
      permission: state.permission, battery: state.battery, fcmPossible: false };
  }

  async function pushEnable() {
    if (state.permission !== 'granted') return { ok: false, push: false, reason: 'Уведомления запрещены в настройках телефона для приложения' };
    state.pushEnabled = true;
    keep.set('pushEnabled', true);
    const r = await register();
    if (r.push && !state.battery) {
      const v = await dialog('Разрешить приложению «Kovapp» работать в фоне без ограничений? Иначе телефон может отключать канал уведомлений.',
        [['Разрешить', true, true], ['Отмена', false, false]]);
      if (v) { state.battery = true; keep.set('battery', true); $('#battery').checked = true; }
    }
    return r;
  }
  async function pushDisable() {
    state.pushEnabled = false;
    keep.set('pushEnabled', false);
    if (state.device) await api('DELETE', 'push/native/register', { device_id: state.device.id }).catch(() => {});
    channelStop();
    state.device = null;
    keep.del('device');
    showDevice();
    return { ok: true };
  }
  async function pushSync() {
    if (!state.pushEnabled || state.permission !== 'granted' || !state.session) return { ok: false };
    return register();
  }

  async function logout(a) {
    const d = state.device;
    channelStop();
    state.device = null;
    keep.del('device');
    showDevice();
    state.session = null;
    $('#shade').querySelectorAll('.note').forEach(n => n.remove());
    renderEmpty();
    if (d) {
      const token = a && a.token;
      if (token) await api('DELETE', 'push/native/register', { device_id: d.id }, token).catch(() => {});
      else await api('POST', 'push/native/unregister', { device_id: d.id, secret: d.secret }, null).catch(() => {});
      log('ev', 'выход: телефон отвязан на сервере');
    }
    return true;
  }

  // ── загрузки (Downloads в APK) ────────────────────────────────────────
  const saves = new Map();
  function addDownload(name, size, href) {
    const list = $('#downloads');
    const empty = list.querySelector('.empty');
    if (empty) empty.remove();
    const row = document.createElement('div');
    row.className = 'dl';
    const a = document.createElement('a');
    a.href = href;
    a.download = name;
    a.textContent = name;
    const s = document.createElement('span');
    s.textContent = size >= 1048576 ? (size / 1048576).toFixed(1) + ' МБ' : Math.max(1, Math.round(size / 1024)) + ' КБ';
    row.append(a, s);
    list.prepend(row);
  }

  // ── команды страницы (NativeCommands в APK) ───────────────────────────
  function handle(cmd, a, win) {
    switch (cmd) {
      case 'hello': return Object.assign({}, initState(), { push: pushState() });
      case 'session.set':
        if (!state.session || state.session.token !== a.token) { state.serverSupported = null; state.serverError = null; }
        state.session = { host: a.host, token: a.token, userId: a.userId };
        return true;
      case 'session.clear': return logout(a);
      case 'notify.permission': return state.permission;
      case 'notify.request': return askPermission();
      case 'notify.show': return showSystem(a);
      case 'notify.close': closeSystem(a.tag); return true;
      case 'notify.settings': toast('Настройки уведомлений телефона — переключатель «Разрешение» слева'); return true;
      case 'notify.channel': toast('Настройки категории «Связь с сервером»: на телефоне её можно отключить — значок пропадёт, уведомления останутся'); return true;
      case 'push.state': return (state.serverSupported === null && state.session ? loadConfig() : Promise.resolve()).then(pushState);
      case 'push.enable': return pushEnable();
      case 'push.disable': return pushDisable();
      case 'push.sync': return pushSync();
      case 'push.battery': state.battery = true; keep.set('battery', true); $('#battery').checked = true; toast('Kovapp работает без ограничений батареи'); return true;
      case 'ui.theme':
        if (a.color) themes.set(win, a.color);
        paintBars();
        return true;
      case 'ui.exit': {
        const f = frameOf(win);
        if (f && f !== frames.main) closeChild(f); else home();
        return true;
      }
      case 'open.external': window.open(a.url, '_blank', 'noopener'); return true;
      case 'files.resolve': return (a.files || []).map(() => null);   // на ПК фото и видео обрабатывает браузер
      case 'download.url':
        addDownload(a.name || 'файл', 0, a.url);
        toast('Скачивание: ' + (a.name || 'файл'));
        return true;
      case 'download.begin': {
        const id = saves.size + 1 + Math.floor(Math.random() * 1e6);
        saves.set(id, { name: a.name, mime: a.mime, parts: [] });
        return id;
      }
      case 'download.chunk': {
        const s = saves.get(a.id);
        if (!s) throw new Error('неизвестная загрузка');
        s.parts.push(Uint8Array.from(atob(a.data), c => c.charCodeAt(0)));
        return true;
      }
      case 'download.end': {
        const s = saves.get(a.id);
        saves.delete(a.id);
        const blob = new Blob(s.parts, { type: s.mime || 'application/octet-stream' });
        addDownload(s.name, blob.size, URL.createObjectURL(blob));
        return s.name;
      }
      case 'download.abort': saves.delete(a.id); return true;
    }
    if (cmd.startsWith('media.')) return Promise.reject(new Error('на ПК фото и видео обрабатывает браузер'));
    return undefined;
  }

  function initState() {
    return { platform: 'android', app: 'simulator', build: 0, flavor: 'universal', sdk: 36,
      permission: state.permission, push: pushState(), features: { video: true, hash: true, image: true, downloads: true } };
  }

  // ── сеть (через отладочный сервер) ────────────────────────────────────
  async function setNet(mode) {
    const online = mode !== 'offline';
    await fetch('/__sim/net', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ online, delay_ms: mode === 'slow' ? 1500 : 0 }) }).catch(() => {});
    state.online = online;
    document.querySelectorAll('#net button').forEach(b => b.classList.toggle('on', b.dataset.v === mode));
    $('#neticon').textContent = online ? (mode === 'slow' ? '▾ медленно' : '▾ Wi-Fi') : '✕ нет сети';
    for (const w of devs.keys()) {
      try { w.dispatchEvent(new w.Event(online ? 'online' : 'offline')); } catch (e) { /* */ }
    }
    if (online && channel.want) { channel.backoff = 1000; channelConnect(); }
    log('ev', 'сеть: ' + ({ online: 'есть', slow: 'медленная', offline: 'нет' })[mode]);
  }

  // ── проверочные уведомления ───────────────────────────────────────────
  async function testPush() {
    if (!state.session) { toast('Сначала войдите в приложение'); return; }
    const probe = Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('');
    try {
      await api('POST', 'push/test', { probe });
      log('', 'сервер отправил проверочное уведомление');
      setTimeout(async () => {
        try {
          const st = await api('GET', 'push/test/' + probe);
          const acks = st.acks || [];
          log(acks.length ? '' : 'err', acks.length ? 'доставлено на устройство за ' + acks[0].delay_ms + ' мс' : 'подтверждения доставки нет (уведомления выключены?)');
        } catch (e) { /* */ }
      }, 2500);
    } catch (e) { log('err', 'проверка не отправлена: ' + e.message); }
  }
  async function colleagueMessage() {
    if (!state.session) { toast('Сначала войдите в приложение'); return; }
    await api('POST', 'mock/notify', { user_ids: [state.session.userId], title: 'Орлов Андрей',
      body: 'Проверка: документы по объекту отправил', url: '#/messenger/35', tag: 'chat-35', extra: { kind: 'chat', chat_id: 35 } })
      .catch(e => log('err', 'не отправлено: ' + e.message));
  }

  // ── стереть данные приложения ─────────────────────────────────────────
  async function wipeAppData() {
    const ok = await dialog('Стереть данные приложения? Вход, настройки и регистрация уведомлений будут удалены.', [['Стереть', true, true], ['Отмена', false, false]]);
    if (!ok) return;
    killApp();
    channelStop();
    Object.keys(localStorage).filter(k => !k.startsWith('kovsim.') || k === 'kovsim.device' || k === 'kovsim.pushEnabled')
      .forEach(k => localStorage.removeItem(k));
    state.device = null;
    state.pushEnabled = false;
    state.session = null;
    showDevice();
    launch('');
  }

  // ── подключение страниц через эмулятор моста ──────────────────────────
  window.__kovSim = {
    state,
    handle,
    initState,
    openChild,
    attach(win, dev) {
      devs.set(win, dev);
      win.addEventListener('unload', () => { devs.delete(win); viewports.delete(win); });
    },
    device() { const s = appSize(); return { touch: true, width: s.width, height: s.height }; },
    viewport(win, content) {
      const m = /(?:^|,)\s*width\s*=\s*(\d+)/.exec(content || '');
      viewports.set(win, m ? Number(m[1]) : 0);
      fitFrame(frameOf(win));
    },
    visible(win) { return state.app === 'foreground' && !!frameOf(win); },
    log(dir, obj) {
      if (obj && obj.cmd) log('', '→ ' + obj.cmd + ' ' + short(obj.args));
      else if (obj && obj.id && !obj.ok) log('err', '✕ ' + (obj.error || ''));
    },
    closeWindow(win) { const f = frameOf(win); if (f && f !== frames.main) closeChild(f); },
  };

  async function start() {
    setupControls();
    try { state.info = await (await fetch('/__sim/info')).json(); } catch (e) { /* по умолчанию */ }
    $('#server').textContent = state.info.upstream + (state.info.mock ? ' (тестовый)' : '');
    $('#colleague').hidden = !state.info.mock;
    if (state.info.upstream) {
      // сайт с адреса сервера: свой вход, можно войти вторым сотрудником
      $('#siteLink').href = state.info.upstream.replace(/\/+$/, '') + state.info.app_path;
      $('#siteLink').hidden = false;
      $('#siteHint').hidden = !state.info.mock;
    }
    $('#launcherImg').src = 'launcher.svg';   // значок приложения — как у APK (mobile/app/src/main/res)
    showDevice();
    renderEmpty();
    $('#downloads').innerHTML = '<p class="empty">Пусто</p>';
    layout();
    // устройство было зарегистрировано раньше — канал работает и до входа, как служба в APK
    if (state.device && state.pushEnabled && state.permission === 'granted') channelStart();
    launch('');
  }
  start();
})();
