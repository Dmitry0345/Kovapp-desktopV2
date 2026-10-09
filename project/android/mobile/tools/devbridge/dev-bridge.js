/*
 * Эмулятор моста Android для проверки в браузере на ПК (tools/devserver.py --bridge).
 * Играет роль приложения: принимает команды надстройки и отвечает так, как
 * ответил бы телефон. В APK этого файла нет. Тесты меняют поведение через
 * window.__kovDev.handlers[команда] и шлют события через __kovDev.emit().
 */
(function () {
  'use strict';
  if (window.kovNativePort) return;
  // внутри симулятора телефона (tools/simulator) команды обрабатывает он
  var sim = null;
  try { if (window.parent !== window && window.parent.__kovSim) sim = window.parent.__kovSim; } catch (e) { sim = null; }
  var log = [];
  var handlers = {};
  var state = {
    permission: 'default',
    grant: 'granted',
    push: { enabled: false, registered: false, mode: '', serverSupported: true, permission: 'default', battery: false, fcmPossible: false },
    picked: {},
    shown: [],
    downloads: [],
    blobs: {},
    released: [],
    cancelled: [],
    themes: [],
  };
  var port = {
    onmessage: null,
    postMessage: function (text) {
      var msg = JSON.parse(text);
      log.push(msg);
      if (sim) sim.log('out', msg);
      setTimeout(function () { dispatch(msg); }, 0);
    },
  };

  function deliver(obj) {
    if (sim && obj.id) sim.log('in', obj);
    if (typeof port.onmessage === 'function') port.onmessage({ data: JSON.stringify(obj) });
  }

  function dispatch(msg) {
    if (!handlers[msg.cmd] && sim) {
      var r;
      try { r = sim.handle(msg.cmd, msg.args || {}, window); } catch (e) { r = Promise.reject(e); }
      if (r !== undefined) {
        Promise.resolve(r).then(
          function (v) { deliver({ id: msg.id, ok: true, result: v === undefined ? null : v }); },
          function (e) { deliver({ id: msg.id, ok: false, error: String(e && e.message || e) }); });
        return;
      }
    }
    var h = handlers[msg.cmd] || defaults[msg.cmd];
    if (!h) { deliver({ id: msg.id, ok: false, error: 'неизвестная команда: ' + msg.cmd }); return; }
    Promise.resolve().then(function () { return h(msg.args || {}, msg); }).then(
      function (r) { deliver({ id: msg.id, ok: true, result: r === undefined ? null : r }); },
      function (e) { deliver({ id: msg.id, ok: false, error: String(e && e.message || e) }); });
  }

  function pushState() { state.push.permission = state.permission; return state.push; }

  var defaults = {
    'hello': function () { return Object.assign({}, window.__kovNativeInit || {}, { permission: state.permission, push: pushState() }); },
    'session.set': function (a) { state.session = a; return true; },
    'session.clear': function (a) { state.session = null; state.cleared = a; return true; },
    'notify.permission': function () { return state.permission; },
    'notify.request': function () { state.permission = state.grant; return state.permission; },
    'notify.show': function (a) { state.shown.push(a); return state.permission === 'granted'; },
    'notify.close': function (a) { state.closed = a.tag; return true; },
    'notify.settings': function () { state.settingsOpened = true; return true; },
    'notify.channel': function () { state.channelOpened = true; return true; },
    'push.state': function () { return pushState(); },
    'push.enable': function () {
      Object.assign(state.push, { enabled: true, registered: true, mode: 'direct', serverSupported: true });
      return { ok: true, push: true, mode: 'direct' };
    },
    'push.disable': function () { Object.assign(state.push, { enabled: false, registered: false, mode: '' }); return { ok: true }; },
    'push.sync': function () { return { ok: true, push: state.push.registered }; },
    'push.battery': function () { state.batteryAsked = true; return true; },
    'ui.theme': function (a) { state.themes.push(a.color); return true; },
    'ui.exit': function () { state.exited = true; return true; },
    'open.external': function (a) { state.external = a.url; return true; },
    'files.resolve': function (a) {
      return (a.files || []).map(function (f) { return state.picked[f.name + ':' + f.size] || null; });
    },
    'download.url': function (a) { state.downloads.push(Object.assign({ kind: 'url' }, a)); return true; },
    'download.begin': function (a) {
      var id = Object.keys(state.blobs).length + 1;
      state.blobs[id] = { name: a.name, mime: a.mime, size: a.size, parts: [], done: false };
      return id;
    },
    'download.chunk': function (a) { state.blobs[a.id].parts.push(a.data); return true; },
    'download.end': function (a) { state.blobs[a.id].done = true; return state.blobs[a.id].name; },
    'download.abort': function (a) { if (state.blobs[a.id]) state.blobs[a.id].aborted = true; return true; },
    'media.release': function (a) { state.released.push(a.url); return true; },
    'media.cancel': function (a) { state.cancelled.push(a.job); return true; },
  };

  window.kovNativePort = port;

  if (sim) {
    // состояние «телефона» подмешивается в native/kovapp-state.js при его загрузке
    var init;
    Object.defineProperty(window, '__kovNativeInit', {
      configurable: true,
      get: function () { return init; },
      set: function (v) { init = Object.assign({}, v, sim.initState()); },
    });
    // видимость и сеть — как их видит страница на телефоне
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: function () { return sim.visible(window) ? 'visible' : 'hidden'; } });
    Object.defineProperty(document, 'hidden', { configurable: true, get: function () { return !sim.visible(window); } });
    Object.defineProperty(navigator, 'onLine', { configurable: true, get: function () { return sim.state.online; } });
    // экран «телефона»: сенсорный, своего размера (страница решает, какую вёрстку показать)
    Object.defineProperty(window, '__kovDevice', { configurable: true, get: function () { return sim.device(window); } });
    // заданную страницей ширину (meta viewport) Android растягивает на экран — симулятор тоже
    document.addEventListener('DOMContentLoaded', function () {
      var meta = document.querySelector('meta[name="viewport"]');
      if (!meta) return;
      var report = function () { sim.viewport(window, meta.getAttribute('content') || ''); };
      new MutationObserver(report).observe(meta, { attributes: true });
      report();
    });
    // новые окна (документы, таблицы) — поверх приложения, как дочерние WebView
    var nativeOpen = window.open;
    window.open = function (url) {
      var w = sim.openChild(url, window);
      return w === undefined ? nativeOpen.apply(window, arguments) : w;
    };
    window.close = function () { sim.closeWindow(window); };
    document.addEventListener('click', function (e) {
      var a = e.target && e.target.closest ? e.target.closest('a[target="_blank"]') : null;
      if (!a || !a.href || a.hasAttribute('download')) return;
      e.preventDefault();
      sim.openChild(a.href, window);
    }, true);
  }
  window.__kovDev = {
    log: log,
    handlers: handlers,
    state: state,
    emit: function (event, data) { deliver({ event: event, data: data }); },
    calls: function (cmd) { return log.filter(function (m) { return m.cmd === cmd; }); },
    /** Отметить файл как выбранный «в приложении» (как окно выбора Android) */
    pick: function (file, handle) {
      var h = handle || Array.prototype.map.call(crypto.getRandomValues(new Uint8Array(16)), function (b) { return (b < 16 ? '0' : '') + b.toString(16); }).join('');
      state.picked[file.name + ':' + file.size] = h;
      return h;
    },
    /** Положить байты во временный файл отладочного сервера — как TempFiles в APK */
    temp: function (blob) {
      return fetch('/__devbridge/temp', { method: 'POST', body: blob }).then(function (r) { return r.json(); }).then(function (j) { return j.url; });
    },
  };
  if (sim) sim.attach(window, window.__kovDev);
})();
