/* Мост страницы мессенджера к Python (ПК-версия).
 *
 * chat.js сайта работает с объектом backend: методы и сигналы QWebChannel.
 * Здесь настоящий канал Qt оборачивается: аргументы приводятся к строкам,
 * списки уходят текстом JSON, а то, о чём chat.js спрашивает без ожидания
 * ответа (кто вошёл, адрес сервера, предел файлов), отвечается сразу из
 * данных запуска страницы (window.__kovBoot).
 *
 * Kov.UI.syncList и Kov.UI.softFocus — те же, что в js/core/ui.js сайта.
 */
window.Kov = window.Kov || {};

(function () {
  var boot = window.__kovBoot || {};

  Kov.UI = Kov.UI || {};
  /* Список без перерисовки целиком (как Kov.UI.syncList сайта) */
  Kov.UI.syncList = function (host, items, opts) {
    var key = opts.key || function (x) { return x.id; };
    var sign = opts.sign || function () { return ''; };
    var make = opts.make;
    var kept = new Map();
    Array.prototype.slice.call(host.children).forEach(function (el) {
      var k = el.dataset && el.dataset.syncKey;
      if (k !== undefined && k !== null && k !== '') kept.set(k, el);
      else el.remove();
    });
    var next = [];
    (items || []).forEach(function (item, i) {
      var k = String(key(item, i));
      var s = String(sign(item, i));
      var el = kept.get(k);
      if (el && el.dataset.syncSign === s) {
        kept.delete(k);
      } else {
        if (el) el.remove();
        el = make(item, i);
        if (!el) return;
        el.dataset.syncKey = k;
        el.dataset.syncSign = s;
        kept.delete(k);
      }
      next.push(el);
    });
    kept.forEach(function (el) { el.remove(); });
    next.forEach(function (el, i) {
      if (host.children[i] !== el) host.insertBefore(el, host.children[i] || null);
    });
    return next;
  };
  /* Курсор в поле — на компьютере сразу */
  Kov.UI.softFocus = function (el) {
    if (!el) return;
    setTimeout(function () { try { el.focus(); } catch (e) { /* */ } }, 30);
  };

  Kov.Views = Kov.Views || {};
  Kov.Views.messenger = { cancelUpload: function () { if (Kov.__py) Kov.__py.cancel_upload(); } };

  var SIGNALS = ['chatsUpdated', 'historyLoaded', 'messageReceived', 'messageUpdated', 'messageDeleted',
    'reactionsUpdated', 'groupInfoLoaded', 'chatCardLoaded', 'searchResultsReceived', 'uploadProgress',
    'scrollRequested', 'readState'];
  var NO_ARGS = ['chat_closed', 'open_file_dialog', 'show_add_menu', 'cancel_upload'];
  var ONE = ['show_toast', 'copy_text', 'mark_chat_as_read', 'get_group_info', 'get_chat_card', 'search_messages',
    'delete_message', 'request_user_info_card', 'request_chat_info_card', 'open_group_settings'];
  var TWO = ['load_older_history', 'load_newer_history', 'edit_message', 'confirm_delete_message', 'send_reaction',
    'open_media', 'open_file', 'download_file', 'forward_message', 'send_text_message'];

  function S(v) { return v === undefined || v === null ? '' : String(v); }

  var Real = window.QWebChannel;
  window.QWebChannel = function (transport, ready) {
    return new Real(transport, function (channel) {
      var py = channel.objects.backend;
      Kov.__py = py;
      var be = {};
      SIGNALS.forEach(function (n) { be[n] = py[n]; });
      NO_ARGS.forEach(function (n) { be[n] = function () { py[n](); }; });
      ONE.forEach(function (n) { be[n] = function (a) { py[n](S(a)); }; });
      TWO.forEach(function (n) { be[n] = function (a, b) { py[n](S(a), S(b)); }; });
      be.get_current_user_id = function (done) { done(S(boot.user)); };
      be.get_api_host = function (done) { done(S(boot.host)); };
      be.max_files = function () { return boot.maxFiles || 100; };
      be.load_history = function (id, limit, offset) { py.load_history(S(id), Number(limit) || 30, Number(offset) || 0); };
      be.go_to_message = function (m, g, c) { py.go_to_message(S(m), S(g), S(c)); };
      be.send_message_with_files = function (t, f, r) { py.send_message_with_files(S(t), S(f), S(r)); };
      be.forward_messages = function (ops) { py.forward_messages(JSON.stringify(ops || [])); };
      be.confirm_delete_messages = function (ids) { py.confirm_delete_messages(JSON.stringify(ids || [])); };
      // открыт ли чат: узкое окно показывает либо список, либо переписку (как у сайта на телефоне)
      var root = document.getElementById('kovChat');
      be.mark_chat_as_read = function (id) { if (root) root.classList.add('chat-open'); py.mark_chat_as_read(S(id)); };
      be.chat_closed = function () { if (root) root.classList.remove('chat-open'); py.chat_closed(); };
      var back = document.getElementById('kovChatBack');
      if (back) back.onclick = function () { if (typeof closeActiveChat === 'function') closeActiveChat(); };
      if (typeof setApiToken === 'function') setApiToken(boot.token || '');
      ready({ objects: { backend: be } });
      py.page_ready();
    });
  };
})();
