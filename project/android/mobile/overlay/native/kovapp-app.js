/*
 * Kovapp для Android: подключение модулей веб-версии к возможностям
 * телефона. Выполняется после всех модулей и до js/app.js. Код веб-версии
 * не меняется — заменяются только объекты, которые в WebView работать не
 * могут (Web Push, браузерные уведомления), и добавляются запасные пути.
 */
(function () {
  'use strict';
  var N = window.KovNative;
  var Kov = window.Kov;
  if (!N || !N.available || !Kov) return;

  function safe(fn) { try { return fn(); } catch (e) { console.error('[kovapp]', e); return undefined; } }

  // ── сеанс: приложению нужен токен для регистрации уведомлений ───────
  function syncSession() {
    var s = Kov.Api && Kov.Api.state;
    if (!s || !s.token || !s.userId) return Promise.resolve(false);
    return N.call('session.set', { host: s.host || location.origin, token: s.token, userId: Number(s.userId) })
      .then(function () { return true; }, function () { return false; });
  }

  safe(function () {
    var clear = Kov.Api.clear;
    Kov.Api.clear = function () {
      var s = Kov.Api.state || {};
      var host = s.host, token = s.token;
      var r = clear.apply(this, arguments);
      if (token) N.call('session.clear', { host: host || location.origin, token: token }).catch(function () {});
      return r;
    };
  });

  // ── режим уведомлений: в приложении всегда «push» ────────────────────
  function pushState() { return N.state.push || {}; }

  // почему собственный канал, а не Google — одной понятной фразой
  function channelReason(st) {
    if (!st.gms) return 'На этом телефоне нет сервисов Google — так и должно быть.';
    if (!st.fcmApp) return 'Сервисы Google на телефоне есть, но в приложении не настроена доставка через Google (Firebase) — поэтому работает запасной канал.';
    if (st.serverSupported === true && !st.fcmServer) return 'Сервисы Google на телефоне есть, но сервер не настроен отправлять через Google — поэтому работает запасной канал.';
    var e = String(st.fcmError || '');
    var why = /SERVICE_NOT_AVAILABLE/.test(e)
      ? 'сервисы Google на этом телефоне не подключаются к службе уведомлений Google. Так бывает на Huawei и Honor ' +
        'без сертификации Google, когда сервисам Google запрещена работа в фоне или когда сеть закрывает связь с Google'
      : /AUTHENTICATION_FAILED|PHONE_REGISTRATION_ERROR/.test(e)
        ? 'Google не принял этот телефон — проверьте дату и время и обновите «Сервисы Google Play»'
        : /FIS_AUTH_ERROR|API_KEY|INVALID/.test(e)
          ? 'Google не принял ключ приложения — проверьте ключи (run.cmd keys-check)'
          : /Timeout/.test(e) ? 'Google долго не отвечает — приложение попробует ещё раз' : '';
    return 'Google не выдал адрес для доставки' + (why ? ': ' + why : '') + (e ? ' (' + e + ')' : '') + '. Работает запасной канал.';
  }

  function describe() {
    var st = pushState();
    if (st.serverSupported !== true && st.serverError) {
      return 'Приложение: не удалось проверить уведомления на сервере — ' + st.serverError + '.';
    }
    if (st.serverSupported === false) {
      return 'Приложение: уведомления приходят, пока оно открыто или свёрнуто. Чтобы они приходили и при закрытом приложении, на сервере нужен модуль уведомлений для приложения.';
    }
    if (st.registered && st.mode === 'fcm') {
      return 'Приложение: уведомления приходят даже когда оно закрыто — через сервисы Google.';
    }
    if (st.registered && st.mode === 'direct') {
      // короткая фраза; шаги (значок, работа в фоне) — отдельным блоком с кнопками ниже
      return 'Уведомления приходят, даже когда приложение закрыто: телефон сам держит связь с сервером. ' + channelReason(st);
    }
    return 'Приложение: уведомления могут приходить даже когда оно закрыто — включите их на этом устройстве.';
  }

  safe(function () {
    Kov.NotifyMode = {
      secure: window.isSecureContext === true,
      isFile: false,
      notificationsApi: true,
      serviceWorker: false,
      pushApi: true,
      push: true,
      isIOS: false,
      standalone: true,
      iosNeedsInstall: false,
      level: 'push',
      describe: describe,
      canAskAgain: true,        // приложение может спросить снова или открыть настройки телефона
    };
  });

  // ── клиент уведомлений: вместо Web Push — приложение ────────────────
  function refreshState() {
    return N.call('push.state').then(function (st) { if (st) { N.state.push = st; N.state.permission = st.permission || N.state.permission; } return pushState(); },
      function () { return pushState(); });
  }

  safe(function () {
    Kov.PushClient = {
      init: function () { return syncSession().then(function () { return null; }); },
      capability: function () {
        return { supported: true, permission: N.state.permission, iosNeedsInstall: false, reason: '' };
      },
      status: function () {
        return syncSession().then(refreshState).then(function (st) {
          return {
            supported: true,
            permission: N.state.permission,
            iosNeedsInstall: false,
            reason: '',
            serverEnabled: st.serverSupported !== false,
            subscribed: !!(st.enabled && st.registered),
            endpoint: st.registered ? 'native' : '',
          };
        });
      },
      ensurePermission: function () {
        if (N.state.permission === 'default') return Notification.requestPermission();
        return Promise.resolve(N.state.permission);
      },
      enable: function () {
        try { localStorage.removeItem('kov.push.off'); } catch (e) { /* приватный режим */ }
        var go = N.state.permission === 'granted' ? Promise.resolve('granted') : Notification.requestPermission();
        return go.then(function (p) {
          if (p !== 'granted') return { ok: false, push: false, reason: 'Уведомления запрещены в настройках телефона для приложения' };
          return syncSession().then(function () { return N.call('push.enable'); }).then(function (r) {
            return refreshState().then(function () { return r; });
          });
        }).catch(function (e) { return { ok: false, push: false, reason: String(e && e.message || e) }; });
      },
      disable: function () {
        try { localStorage.setItem('kov.push.off', '1'); } catch (e) { /* приватный режим */ }   // выключил сам — сами не включаем
        return N.call('push.disable').then(function (r) { return refreshState().then(function () { return r || { ok: true }; }); },
          function (e) { return { ok: false, reason: String(e && e.message || e) }; });
      },
      sync: function () {
        lastSync = Date.now();
        return syncSession().then(refreshState).then(function (st) {
          // Разрешение уже есть (телефон выдал его при установке), а к серверу телефон не привязан и человек
          // уведомления не выключал — привязываем сразу, как после «Разрешить». Иначе переключатель горит,
          // а уведомления не приходят.
          var off = false;
          try { off = localStorage.getItem('kov.push.off') === '1'; } catch (e) { /* приватный режим */ }
          if (N.state.permission === 'granted' && !(st && st.enabled) && !off) return N.call('push.enable');
          return N.call('push.sync');
        }).then(function (r) { return refreshState().then(function () { return !!(r && r.push); }); }, function () { return false; });
      },
      test: function () { return Kov.Api.post('push/test'); },
      get registration() { return null; },
    };
  });

  // Сервер держит привязку телефона не дольше срока входа: при возвращении к
  // приложению, открытому давно, обновляем регистрацию (раз в сутки, не чаще)
  var lastSync = 0;
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState !== 'visible' || !lastSync || Date.now() - lastSync < 86400000) return;
    if (Kov.Api && Kov.Api.state && Kov.Api.state.token) safe(function () { Kov.PushClient.sync(); });
  });

  // ── события приложения ──────────────────────────────────────────────
  N.on('push', function (d) {
    if (Kov.NotifyCenter && d && d.payload) safe(function () { Kov.NotifyCenter.handleEvent({ type: 'push', payload: d.payload }); });
  });

  // Переход в другой раздел отменяет идущую отправку файла (так устроена
  // веб-версия). На телефоне к нему ведут нажатие на уведомление и жест
  // «Назад» — сначала спрашиваем, а не обрываем отправку молча
  // признак веб-версии: тот же, по которому она сама откладывает перерисовку (app.js)
  function uploadBusy() { return !!(Kov.Files && Kov.Files.uploadsActive && Kov.Files.uploadsActive()); }
  function confirmLeave(okText, go) {
    if (!uploadBusy() || !Kov.UI || !Kov.UI.confirm) { go(); return; }
    Kov.UI.confirm('Идёт отправка файла', 'Перейти сейчас? Отправка файла будет отменена.', { okText: okText })
      .then(function (ok) { if (ok) go(); });
  }

  // разрешение отозвали в настройках телефона: снова предложить включить и обновить настройки
  var lastPermission = N.state.permission;
  N.on('permission', function (p) {
    var was = lastPermission;
    lastPermission = p;
    safe(function () { window.dispatchEvent(new Event('kov:permission')); });
    // отзыв: было разрешено (или уведомления на устройстве были включены), а теперь нет
    var hadPush = !!(N.state.push && N.state.push.enabled);
    if (p !== 'granted' && (was === 'granted' || hadPush)) {
      safe(function () { localStorage.removeItem('kov.notify.offer'); });
      setTimeout(function () { safe(function () { Kov.NotifyCenter && Kov.NotifyCenter.offerPermission(); }); }, 400);
    }
  });

  // прочитанный чат — его уведомление из шторки убирается
  document.addEventListener('kov:notify-clear', function (e) {
    var tag = e.detail && e.detail.tag;
    if (tag) N.call('notify.close', { tag: tag }).catch(function () {});
  });

  // отправка файлов в фоне: общий ход — в шторку уведомлений, «Отменить» оттуда — странице
  (function hookTransfers() {
    if (!window.Kov || !Kov.Files || !('onTransfer' in Kov.Files)) { setTimeout(hookTransfers, 300); return; }
    Kov.Files.onTransfer = function (st) {
      if (st && st.count) N.call('transfer.update', { count: st.count, percent: st.percent, quiet: st.quiet ? 1 : 0 }).catch(function () {});
      else N.call('transfer.end').catch(function () {});
    };
    N.on('transfer.cancel', function () { if (Kov.Files.cancelTransfers) Kov.Files.cancelTransfers(); });
  })();

  N.on('navigate', function (d) {
    var url = d && d.url;
    if (!url || !/^#\/[A-Za-z0-9_\/-]{0,200}$/.test(url)) return;
    confirmLeave('Перейти', function () {
      var inst = d.tag && N.notifications.get(d.tag);
      if (inst && typeof inst.onclick === 'function') {
        safe(function () { inst.onclick(new Event('click')); });
        return;
      }
      if (document.querySelector('.app') && Kov.Router && Kov.Router.go) Kov.Router.go(url);
      else location.hash = url;
    });
  });

  // ── «Это устройство»: что сделать, с точными шагами и кнопками ─────
  function helpStep(title, lines, buttonText, onClick, done) {
    var box = document.createElement('div');
    box.className = 'kovapp-step' + (done ? ' done' : '');
    var head = document.createElement('b');
    head.textContent = (done ? '✓ ' : '') + title;
    box.appendChild(head);
    if (lines.length) {
      var list = document.createElement(lines.length > 1 ? 'ol' : 'p');
      lines.forEach(function (t) {
        var li = document.createElement(lines.length > 1 ? 'li' : 'span');
        li.textContent = t;
        list.appendChild(li);
      });
      box.appendChild(list);
    }
    if (buttonText && Kov.UI && Kov.UI.button) box.appendChild(Kov.UI.button(buttonText, { kind: 'ghost', onClick: onClick }));
    return box;
  }

  safe(function () {
    // оформление блока шагов — цветами темы страницы
    var css = '.kovapp-device-help{display:flex;flex-direction:column;gap:.75rem;margin-top:.9rem}'
      + '.kovapp-step{padding:.85rem .95rem;border-radius:.85rem;background:var(--ghost-bg);border:1px solid var(--card-border)}'
      + '.kovapp-step b{display:block;font-size:.95rem;margin-bottom:.4rem;color:var(--text-value)}'
      + '.kovapp-step.done b{color:var(--success-text);margin-bottom:.2rem}'
      + '.kovapp-step ol{margin:.1rem 0 .75rem 1.15rem;padding:0;font-size:.9rem;line-height:1.45;color:var(--text-value)}'
      + '.kovapp-step ol li{margin:.15rem 0}'
      + '.kovapp-step p{margin:0;font-size:.9rem;color:var(--text-label)}'
      + '.kovapp-step .btn{width:100%;justify-content:center}';
    var el = document.createElement('style');
    el.textContent = css;
    (document.head || document.documentElement).appendChild(el);
  });

  function deviceCardButton() {
    var st = pushState();
    var cards = document.querySelectorAll('.set-card');
    for (var i = 0; i < cards.length; i++) {
      var title = cards[i].querySelector('.set-head .t');
      if (!title || title.textContent !== 'Это устройство') continue;
      var body = cards[i].querySelector('.set-body');
      if (!body) return;
      var help = body.querySelector('.kovapp-device-help');
      // значок «Kovapp на связи» и «работа в фоне» — только у постоянного собственного канала;
      // если возможна доставка через Google, собственный канал временный: настройки не показываем
      if (!(st.registered && st.mode === 'direct') || st.fcmExpected) { if (help) help.remove(); return; }
      var key = [st.serviceHidden, st.battery, st.vendor].join('|');
      if (help && help.getAttribute('data-key') === key) return;
      if (!help) { help = document.createElement('div'); help.className = 'kovapp-device-help'; body.appendChild(help); }
      help.setAttribute('data-key', key);
      help.textContent = '';
      help.appendChild(st.serviceHidden
        ? helpStep('Значок «Kovapp на связи» в шторке скрыт', ['Уведомления приходят как обычно.'], '', null, true)
        : helpStep('Как убрать значок «Kovapp на связи» из шторки', [
            'Нажмите кнопку «Убрать значок» ниже.',
            'Откроются настройки телефона — выключите переключатель в самом верху экрана.',
            'Вернитесь в приложение. Значок пропадёт, уведомления продолжат приходить.',
          ], 'Убрать значок', function () { N.call('notify.channel').catch(function () {}); }));
      if (!st.battery) {
        var steps = ['Нажмите «Разрешить работу в фоне» ниже.', 'В окне телефона нажмите «Разрешить».'];
        if (st.vendor === 'huawei' || st.vendor === 'honor') {
          steps.push('Huawei и Honor: ещё «Настройки» телефона → «Приложения» → «Запуск приложений» → Kovapp → выключите «Автоматически» и включите все три переключателя.');
        }
        help.appendChild(helpStep('Чтобы телефон не обрывал связь в фоне', steps, 'Разрешить работу в фоне',
          function () { N.call('push.battery').catch(function () {}); }));
      }
      return;
    }
  }
  safe(function () {
    var pending = false;
    new MutationObserver(function () {
      if (pending) return;
      pending = true;
      setTimeout(function () { pending = false; safe(deviceCardButton); }, 150);
    }).observe(document.documentElement, { childList: true, subtree: true });
  });

  // ── цвет системных панелей = theme-color страницы ────────────────────
  var lastColor = '';
  function reportTheme() {
    var m = document.querySelector('meta[name="theme-color"]');
    var color = m && m.getAttribute('content');
    if (!color && document.body) color = getComputedStyle(document.body).backgroundColor;
    if (!color || color === lastColor) return;
    lastColor = color;
    N.call('ui.theme', { color: color }).catch(function () {});
  }
  safe(function () {
    new MutationObserver(reportTheme).observe(document.documentElement, { subtree: true, attributes: true, attributeFilter: ['content', 'class', 'data-theme'], childList: true });
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', reportTheme); else reportTheme();
  });

  // ── кнопка «Назад»: сначала закрываются всплывающие слои ─────────────
  function visible(el) {
    if (!el || !el.isConnected || el.hidden) return false;
    var cs = getComputedStyle(el);
    return cs.display !== 'none' && cs.visibility !== 'hidden';
  }
  function last(sel) {
    var all = Array.prototype.slice.call(document.querySelectorAll(sel)).filter(visible);
    return all.length ? all[all.length - 1] : null;
  }
  function escape(target) {
    (target || document.activeElement || document.body).dispatchEvent(new KeyboardEvent('keydown', {
      key: 'Escape', code: 'Escape', keyCode: 27, which: 27, bubbles: true, cancelable: true,
    }));
  }
  function outsidePress() {
    ['pointerdown', 'mousedown'].forEach(function (type) {
      var Ctor = type === 'pointerdown' && window.PointerEvent ? PointerEvent : MouseEvent;
      document.body.dispatchEvent(new Ctor(type, { bubbles: true, cancelable: true }));
    });
  }

  N.onBack = function () {
    return safe(function () {
      if (document.querySelector('.login-screen')) return 'exit';
      // выпадающий список выбора
      var picker = last('.picker-panel');
      if (picker) { escape(); return true; }
      // списки единиц (склад, оборудование) закрываются нажатием мимо
      if (last('.wh-unit-pop')) { outsidePress(); return true; }
      // контекстные меню и реакции чата
      if (last('.context-menu, .reaction-popup')) { escape(); outsidePress(); return true; }
      // шторки снизу (выбор из списка, вложения), их просмотр и камера — своей кнопкой закрытия
      var sheet = last('.kov-sheet-root');
      if (sheet) { var x = sheet.querySelector('[data-close]'); if (x) x.click(); else sheet.remove(); return true; }
      // верхнее окно или просмотр файла — своей кнопкой закрытия
      var layer = last('.viewer-root, .modal-root');
      if (layer) {
        var btn = layer.classList.contains('viewer-root')
          ? layer.querySelector('.viewer-top button[title^="Закрыть"]')
          : layer.querySelector('.modal-head .close-x');
        if (btn) btn.click(); else layer.remove();
        return true;
      }
      // боковое меню и панели
      var scrim = document.querySelector('.scrim.open');
      if (scrim) { scrim.click(); return true; }
      // поиск и карточка чата
      var chatLayer = last('#kovChat .search-sidebar.open, #kovChat .chat-info-card');
      if (chatLayer) { escape(chatLayer); return true; }
      // открытый чат на узком экране — к списку чатов
      var back = document.querySelector('#kovChat.chat-open #kovChatBack');
      if (back && visible(back) && back.offsetParent !== null) { back.click(); return true; }
      // шаг назад по истории сменит раздел и оборвёт отправку — сначала спросить
      if (uploadBusy()) { confirmLeave('Назад', function () { history.back(); }); return true; }
      return false;
    }) || false;
  };

  // ── скачивание: в «Загрузки» телефона ────────────────────────────────
  var CHUNK = 512 * 1024;
  function b64(bytes) {
    var s = '';
    for (var i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(s);
  }
  function saveBlob(blob, name) {
    return N.call('download.begin', { name: name || 'file', mime: blob.type || '', size: blob.size }).then(function (id) {
      var offset = 0;
      function next() {
        if (offset >= blob.size) return N.call('download.end', { id: id });
        var part = blob.slice(offset, Math.min(blob.size, offset + CHUNK));
        offset += part.size;
        return part.arrayBuffer().then(function (buf) {
          return N.call('download.chunk', { id: id, data: b64(new Uint8Array(buf)) });
        }).then(next);
      }
      return next().catch(function (e) { N.call('download.abort', { id: id }).catch(function () {}); throw e; });
    });
  }
  function toast(text, kind) { if (Kov.UI && Kov.UI.toast) Kov.UI.toast(text, kind || 'info'); }

  // Данные blob: берём у самого объекта, а не запросом по адресу: политика
  // безопасности сервера (connect-src 'self') запрещает fetch('blob:…')
  var blobs = new Map();
  safe(function () {
    var create = URL.createObjectURL, revoke = URL.revokeObjectURL;
    URL.createObjectURL = function (obj) {
      var url = create.apply(URL, arguments);
      if (obj instanceof Blob) blobs.set(url, obj);
      return url;
    };
    URL.revokeObjectURL = function (url) {
      blobs.delete(url);
      return revoke.apply(URL, arguments);
    };
  });

  document.addEventListener('click', function (e) {
    var a = e.target && e.target.closest ? e.target.closest('a[download]') : null;
    if (!a || !a.href) return;
    var href = a.href, name = a.getAttribute('download') || '';
    if (/^blob:/i.test(href)) {
      e.preventDefault();
      e.stopImmediatePropagation();
      var known = blobs.get(href);
      (known ? Promise.resolve(known) : Promise.reject(new Error('файл больше недоступен')))
        .then(function (blob) { return saveBlob(blob, name); })
        .then(function (saved) { toast('Файл «' + (saved || name) + '» сохранён в «Загрузки»', 'success'); },
          function (err) { toast('Не удалось сохранить файл: ' + (err && err.message || err), 'error'); });
      return;
    }
    if (/^https?:/i.test(href)) {
      e.preventDefault();
      e.stopImmediatePropagation();
      N.call('download.url', { url: href, name: name }).then(
        function () { toast('Скачивание «' + (name || 'файл') + '» — в шторке уведомлений', 'info'); },
        function (err) { toast('Не удалось скачать: ' + (err && err.message || err), 'error'); });
    }
  }, true);

  // ── файлы, выбранные в приложении: их может обработать телефон ───────
  var handles = new WeakMap();
  var nativeSizes = new Set();
  function resolveFiles(files) {
    var list = Array.prototype.slice.call(files || []).filter(function (f) { return f && typeof f.name === 'string' && !handles.has(f); });
    if (!list.length) return Promise.resolve();
    return N.call('files.resolve', { files: list.map(function (f) { return { name: f.name, size: f.size }; }) }).then(function (res) {
      list.forEach(function (f, i) {
        var h = res && res[i];
        handles.set(f, h || null);
        if (h) nativeSizes.add(f.size);
      });
    }, function () { list.forEach(function (f) { handles.set(f, null); }); });
  }
  function handleOf(file) {
    if (!file || typeof file.name !== 'string') return Promise.resolve(null);
    if (handles.has(file)) return Promise.resolve(handles.get(file));
    return resolveFiles([file]).then(function () { return handles.get(file) || null; });
  }

  safe(function () {
    if (!Kov.MediaPrep || !Kov.MediaPrep.prepareAll) return;
    var prepareAll = Kov.MediaPrep.prepareAll;
    Kov.MediaPrep.prepareAll = function (files, opts) {
      var self = this, args = arguments;
      return resolveFiles(files).then(function () { return prepareAll.apply(self, args); });
    };
  });

  // отпечаток, участки и образец файла: есть ссылка телефона — считает телефон (читает файл напрямую,
  // системный SHA-256 — в разы быстрее страницы), иначе — страница
  safe(function () {
    var H = Kov.FileHash;
    if (!H) return;
    var tree = H.tree, range = H.range, sample = H.sample, worthIt = H.worthIt, jobs = 0;
    H.tree = function (blob, onProgress) {
      return handleOf(blob).then(function (h) {
        if (!h) return tree.call(H, blob, onProgress);
        var job = 'h' + (++jobs);
        var off = N.on('media.progress', function (d) { if (d && d.job === job && onProgress) onProgress(d.p); });
        return N.call('media.hashTree', { handle: h, job: job })
          .catch(function () { return tree.call(H, blob, onProgress); })
          .finally(off);
      });
    };
    H.range = function (blob, offset, length) {
      return handleOf(blob).then(function (h) {
        if (!h) return range.call(H, blob, offset, length);
        return N.call('media.hashRange', { handle: h, offset: offset, length: length })
          .catch(function () { return range.call(H, blob, offset, length); });
      });
    };
    if (sample) H.sample = function (blob) {
      return handleOf(blob).then(function (h) {
        if (!h) return sample.call(H, blob);
        return N.call('media.hashSample', { handle: h }).catch(function () { return sample.call(H, blob); });
      });
    };
    H.worthIt = function (size) { return nativeSizes.has(size) || worthIt.call(H, size); };
  });

  // фото: если страница не смогла распаковать (например, HEIC) — сжимает телефон
  function loadImage(src) {
    return new Promise(function (resolve, reject) {
      var img = new Image();
      img.onload = function () { resolve(img); };
      img.onerror = function () { reject(new Error('превью не загрузилось')); };
      img.src = src;
    });
  }
  function release(url) { N.call('media.release', { url: url }).catch(function () {}); }

  safe(function () {
    var P = Kov.ImagePrep;
    if (!P || !P.prepare) return;
    var prepare = P.prepare;
    P.prepare = function (file, opts) {
      var self = this, args = arguments;
      return prepare.apply(self, args).then(function (r) {
        if (!r || r.reason !== 'decode' || (opts && (opts.forceEncoder || opts.noWebp))) return r;
        return handleOf(file).then(function (h) {
          if (!h) return r;
          return N.call('media.image', { handle: h, maxSide: (opts && opts.maxSide) || P.MAX_SIDE }).then(function (n) {
            var out = { kind: 'image', file: file, changed: false, width: n.width || 0, height: n.height || 0, thumb: null, thumbHash: '', reason: n.reason || '' };
            var steps = Promise.resolve();
            if (n.preview) {
              steps = loadImage(n.preview.url).then(function (img) {
                return P.placeholder(img, img.naturalWidth, img.naturalHeight).then(function (ph) {
                  out.thumbHash = ph.hash;
                  if (ph.canvas) { ph.canvas.width = 0; ph.canvas.height = 0; }
                  return P.thumbnail(img, img.naturalWidth, img.naturalHeight);
                }).then(function (th) {
                  if (th) { out.thumb = th.blob; out.thumbWidth = th.width; out.thumbHeight = th.height; }
                });
              }).finally(function () { release(n.preview.url); });
            }
            if (n.changed && n.file) {
              steps = steps.then(function () { return fetch(n.file.url); }).then(function (res) { return res.blob(); }).then(function (blob) {
                release(n.file.url);
                out.file = new File([blob], n.name, { type: n.mime, lastModified: Date.now() });
                out.changed = true;
                out.mime = n.mime;
                out.reason = '';
              });
            }
            return steps.then(function () { return out; });
          }).catch(function () { return r; });
        });
      });
    };
  });

  // шторка вложений: галерея и камера телефона
  safe(function () {
    if (!Kov.AttachSheet) return;
    Kov.AttachSheet.provider = {
      camera: function (audio) { return N.call('camera.request', { audio: !!audio }); },
      access: function () { return N.call('gallery.access'); },
      request: function () { return N.call('gallery.request'); },
      recent: function (offset, limit) { return N.call('gallery.recent', { offset: offset, limit: limit }); },
      thumbs: function (ids) { return N.call('gallery.thumbs', { ids: ids }); },
      pick: function (ids) { return N.call('gallery.pick', { ids: ids }); },
      // выбранное — к ближайшему выбору файла страницы: файлы без копирования
      preset: function (ids) { return N.call('gallery.preset', { ids: ids }); }
    };
  });

  // видео: сначала сжимает телефон — родной кодек процессора на полной скорости, кадры идут мимо памяти;
  // телефон не может (кодек, поворот, ошибка) — сжимает страница (WebCodecs), как раньше
  safe(function () {
    var V = Kov.VideoPrep;
    if (!V || !V.plan || !V.compress) return;
    var plan = V.plan, compress = V.compress;
    V.plan = function (file, opts) {
      var self = this, args = arguments;
      function page(note) {
        return plan.apply(V, args).then(function (r) {
          return r && r.mode === 'server' && note ? Object.assign({}, r, { reason: (r.reason || '') + '; телефон: ' + note }) : r;
        });
      }
      return handleOf(file).then(function (h) {
        if (!h) return page('');
        return N.call('media.videoPlan', { handle: h, edits: (opts && opts.edits) || null }).then(function (n) {
          var analysis = { duration: n.duration, width: n.width, height: n.height };
          if (n.mode === 'device') {
            return { mode: 'device', native: n.job, width: n.width, height: n.height, fps: n.fps, estimate: n.estimate,
              side: n.side, codec: n.codec, reason: '', analysis: analysis };
          }
          if (n.mode === 'as-is') return { mode: 'as-is', reason: 'ready', analysis: analysis };
          return page(n.reason || 'нет');
        }, function () { return page('ошибка'); });
      });
    };
    V.compress = function (file, p, sink, opts) {
      if (!p || !p.native) return compress.apply(V, arguments);
      return runNative(p, sink, opts || {});
    };
  });

  function posterCanvas(d) {
    return loadImage(d.url).then(function (img) {
      var c = document.createElement('canvas');
      c.width = d.width || img.naturalWidth;
      c.height = d.height || img.naturalHeight;
      var g = c.getContext('2d');
      g.imageSmoothingQuality = 'high';
      g.drawImage(img, 0, 0, c.width, c.height);
      return c;
    }).finally(function () { release(d.url); });
  }

  function runNative(p, sink, opts) {
    var job = p.native;
    var queue = [], expected = 0, finished = false, failure = null, wake = null, posterDone = Promise.resolve();
    function poke() { if (wake) { var w = wake; wake = null; w(); } }
    var offs = [
      N.on('media.segment', function (d) { if (d && d.job === job) { queue.push(d); poke(); } }),
      N.on('media.progress', function (d) { if (d && d.job === job && opts.onProgress) opts.onProgress(d.p); }),
      N.on('media.poster', function (d) {
        if (!d || d.job !== job) return;
        posterDone = posterCanvas(d).then(function (c) { if (opts.onPoster) opts.onPoster(c); }, function () {});
      }),
    ];
    var result = N.call('media.videoRun', { job: job }).then(
      function (r) { finished = true; poke(); return r; },
      function (e) { failure = e; poke(); throw e; });
    result.catch(function () {});
    function pump() {
      queue.sort(function (a, b) { return a.index - b.index; });
      if (queue.length && queue[0].index === expected) {
        var seg = queue.shift();
        return fetch(seg.url).then(function (res) { return res.arrayBuffer(); }).then(function (buf) {
          release(seg.url);
          return sink(new Uint8Array(buf));
        }).then(function () { expected++; return pump(); });
      }
      if (failure) return Promise.reject(failure);
      if (finished && !queue.length) return result;
      return new Promise(function (r) { wake = r; setTimeout(poke, 1000); }).then(pump);
    }
    return pump().then(function (r) {
      return posterDone.then(function () { return { frames: r.frames, encoded: r.encoded }; });
    }).catch(function (e) {
      N.call('media.cancel', { job: job }).catch(function () {});
      queue.forEach(function (s) { release(s.url); });
      throw e;
    }).finally(function () { offs.forEach(function (off) { off(); }); });
  }

  // звуковой сигнал страницы не нужен, когда приложение свёрнуто: звучит система
  safe(function () {
    var UI = Kov.NotifyUI;
    if (!UI || !UI.playChime) return;
    var chime = UI.playChime;
    UI.playChime = function () {
      if (document.visibilityState === 'hidden') return false;
      return chime.apply(this, arguments);
    };
  });
})();
