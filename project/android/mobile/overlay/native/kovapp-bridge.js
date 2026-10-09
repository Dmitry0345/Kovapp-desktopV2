/*
 * Kovapp для Android: связь страницы с приложением.
 * Подключается приложением к страницам web_app (в самих файлах веб-версии
 * этой строки нет). В обычном браузере объекта kovNativePort нет — скрипт
 * ничего не делает.
 */
(function () {
  'use strict';
  var port = window.kovNativePort;
  var init = window.__kovNativeInit;
  if (!port || !init || window.KovNative) return;

  var seq = 0;
  var pending = new Map();
  var listeners = {};
  var state = {
    init: init,
    permission: init.permission || 'default',
    push: init.push || {},
  };

  function call(cmd, args, timeoutMs) {
    var id = ++seq;
    return new Promise(function (resolve, reject) {
      pending.set(id, { resolve: resolve, reject: reject });
      try {
        port.postMessage(JSON.stringify({ id: id, cmd: cmd, args: args || {} }));
      } catch (e) {
        pending.delete(id);
        reject(e);
        return;
      }
      if (timeoutMs) setTimeout(function () {
        if (pending.delete(id)) reject(new Error('Приложение не ответило'));
      }, timeoutMs);
    });
  }

  function on(event, fn) {
    (listeners[event] = listeners[event] || []).push(fn);
    return function off() {
      var list = listeners[event] || [];
      var i = list.indexOf(fn);
      if (i >= 0) list.splice(i, 1);
    };
  }

  port.onmessage = function (e) {
    var m;
    try { m = JSON.parse(e.data); } catch (x) { return; }
    if (m && m.id) {
      var p = pending.get(m.id);
      if (!p) return;
      pending.delete(m.id);
      if (m.ok) p.resolve(m.result); else p.reject(new Error(m.error || 'Ошибка приложения'));
      return;
    }
    if (m && m.event) (listeners[m.event] || []).slice().forEach(function (fn) {
      try { fn(m.data); } catch (x) { console.error(x); }
    });
  };

  on('permission', function (p) { if (p) state.permission = p; });

  // ── системные уведомления вместо браузерных ─────────────────────────
  // WebView не умеет Notification, поэтому тот же интерфейс показывает
  // уведомления телефона. Метка (tag) та же, что у веб-версии.
  var shown = new Map();

  function kindOf(tag) {
    return /^chat-/.test(tag || '') ? 'chat' : 'notification';
  }

  function KovNotification(title, options) {
    if (!(this instanceof KovNotification)) throw new TypeError("Failed to construct 'Notification'");
    options = options || {};
    this.title = String(title);
    this.body = options.body ? String(options.body) : '';
    this.tag = options.tag ? String(options.tag) : '';
    this.data = options.data === undefined ? null : options.data;
    this.icon = options.icon || '';
    this.onclick = null; this.onclose = null; this.onerror = null; this.onshow = null;
    var self = this;
    if (state.permission !== 'granted') {
      setTimeout(function () { if (typeof self.onerror === 'function') self.onerror(new Event('error')); }, 0);
      return;
    }
    var url = self.data && typeof self.data.url === 'string' ? self.data.url : '';
    if (self.tag) shown.set(self.tag, self);
    call('notify.show', { title: self.title, body: self.body, tag: self.tag, url: url, kind: kindOf(self.tag) })
      .then(function (ok) {
        if (ok && typeof self.onshow === 'function') self.onshow(new Event('show'));
        if (!ok && typeof self.onerror === 'function') self.onerror(new Event('error'));
      })
      .catch(function () { if (typeof self.onerror === 'function') self.onerror(new Event('error')); });
  }
  KovNotification.prototype.close = function () {
    if (this.tag) {
      if (shown.get(this.tag) === this) shown.delete(this.tag);
      call('notify.close', { tag: this.tag }).catch(function () {});
    }
    if (typeof this.onclose === 'function') this.onclose(new Event('close'));
  };
  Object.defineProperty(KovNotification, 'permission', { get: function () { return state.permission; } });
  KovNotification.maxActions = 0;
  KovNotification.requestPermission = function (cb) {
    return call('notify.request').then(function (p) {
      state.permission = p || 'denied';
      if (typeof cb === 'function') cb(state.permission);
      return state.permission;
    });
  };
  try {
    Object.defineProperty(window, 'Notification', { value: KovNotification, configurable: true, writable: true });
  } catch (e) {
    window.Notification = KovNotification;
  }

  window.KovNative = {
    available: true,
    call: call,
    on: on,
    state: state,
    notifications: shown,
    onBack: null,
  };

  call('hello').then(function (s) {
    if (!s) return;
    state.init = s;
    state.permission = s.permission || state.permission;
    state.push = s.push || state.push;
  }).catch(function () {});
})();
