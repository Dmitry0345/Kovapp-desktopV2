/* Мессенджер веб-версии. Интерфейс чата — тот же chat.js/chat.css, что и в
 * десктопе (QWebEngineView), без изменений. Вместо Python-моста QWebChannel
 * здесь JS-объект с теми же 26 методами и сигналами: он ходит на REST
 * сервера и слушает общий WebSocket приложения. Диалоги, которые в десктопе
 * были окнами Qt (новый чат, группа, карточка чата, пересылка, просмотр
 * медиа), здесь сделаны обычными окнами приложения. */
window.Kov = window.Kov || {};
Kov.Views = Kov.Views || {};

Kov.Views.messenger = (function () {
  const { h, icon, button, field } = Kov.UI;
  const IMAGE_EXT = ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.heic'];
  const VIDEO_EXT = ['.mp4', '.avi', '.mov', '.mkv', '.webm', '.ts'];

  let root = null;            // #kovChat — создаётся один раз и переиспользуется
  let loaded = false;         // chat.js подключён
  let loading = null;         // Promise загрузки chat.css/chat.js
  let chats = [];             // последний список чатов с сервера
  let activeChatId = null;
  let pendingOpenId = null;   // чат из адреса #/messenger/N
  const pendingFiles = new Map();   // filepath-метка → File
  let fileSeq = 0;
  const pendingGrouped = new Map(); // id сообщения → {msg, timer} ожидание file_item
  const activeUploads = new Map();
  /** Прочитанный чат: убрать его уведомления (метка chat-<id> — та же у сервера, сайта и приложения) */
  function clearChatNotices(id) {
    const tag = 'chat-' + id;
    document.dispatchEvent(new CustomEvent('kov:notify-clear', { detail: { tag } }));   // приложение — шторка Android
    if ('serviceWorker' in navigator && navigator.serviceWorker.getRegistration) {       // сайт — уведомления браузера
      navigator.serviceWorker.getRegistration()
        .then(reg => (reg && reg.getNotifications ? reg.getNotifications({ tag }) : []))
        .then(list => (list || []).forEach(n => n.close())).catch(() => {});
    }
  }  // чат → идущая отправка файлов (пузырь восстанавливается при возвращении)
  const orphanFiles = new Map();    // parent_id → [file_item]

  // ── сигналы в стиле QWebChannel ─────────────────────────────────────────
  function signal() {
    const handlers = [];
    return { connect: fn => handlers.push(fn), emit: (...args) => handlers.forEach(fn => { try { fn(...args); } catch (e) { console.error('chat signal', e); } }) };
  }

  const backend = {
    chatsUpdated: signal(), historyLoaded: signal(), messageReceived: signal(), messageUpdated: signal(),
    messageDeleted: signal(), reactionsUpdated: signal(), groupInfoLoaded: signal(), chatCardLoaded: signal(), searchResultsReceived: signal(),
    uploadProgress: signal(), scrollRequested: signal(), readState: signal(),

    get_current_user_id(cb) { cb(String(Kov.Api.state.userId)); },
    get_api_host(cb) { cb(Kov.Api.state.host || location.origin); },
    show_toast(text) { Kov.UI.toast(text, 'info'); },
    copy_text(text) { Kov.UI.copyText(String(text || '')); },
    chat_closed() { activeChatId = null; Kov.NotifyCenter.setActiveChat(null); root && root.classList.remove('chat-open'); syncAppTopBar(); },

    async mark_chat_as_read(chatIdStr) {
      const id = Number(chatIdStr);
      activeChatId = id;
      Kov.NotifyCenter.setActiveChat(id);
      root && root.classList.add('chat-open'); syncAppTopBar();
      try { await Kov.Api.post('chat/' + id + '/read'); } catch (e) { /* */ }
      clearChatNotices(id);   // прочитано — уведомление об этом чате из шторки убирается
      loadReadState(id);          // до какого сообщения чат прочитан другими
      const chat = chats.find(c => String(c.id) === String(id));
      if (chat && chat.unread_count) { chat.unread_count = 0; pushChats(); }
    },

    async load_history(chatIdStr, limit, offset) {
      await history(chatIdStr, { limit: limit || 30, offset: offset || 0 }, false);
    },
    async load_older_history(chatIdStr, beforeIdStr) {
      await history(chatIdStr, { limit: 30, before_id: Number(beforeIdStr) }, false);
    },
    async load_newer_history(chatIdStr, afterIdStr) {
      await history(chatIdStr, { limit: 30, after_id: Number(afterIdStr) }, true);
    },
    async go_to_message(msgIdStr, groupIdStr, chatIdStr) {
      // Из карточки вложения могут звать к сообщению другого чата: сначала
      // открываем нужный чат, потом переходим — раньше переход молча терялся
      const target = chatIdStr ? Number(chatIdStr) : null;
      if (target && target !== activeChatId) {
        if (!openById(target)) return Kov.UI.toast('Чат не найден', 'warning');
        // Открытие чата само запускает загрузку последних сообщений.
        // Ждём именно её, а не фиксированные миллисекунды: на медленной
        // сети она приходила уже после перехода и стирала цель с экрана.
        await new Promise(r => setTimeout(r, 30));
        await lastHistory;
      }
      if (!activeChatId) return;
      try {
        const list = await Kov.Api.get('chat/history/' + activeChatId, { query: { around_id: Number(msgIdStr), limit: 31 } });
        const payload = JSON.stringify(aggregate((list || []).map(formatUrls)));
        if (typeof onHistoryAroundLoaded === 'function') onHistoryAroundLoaded(payload, String(msgIdStr), String(groupIdStr || ''));
      } catch (e) { Kov.UI.toast('Не удалось перейти к сообщению', 'warning'); }
    },
    async get_group_info(chatIdStr) {
      try { const info = await Kov.Api.get('chat/group/' + Number(chatIdStr)); backend.groupInfoLoaded.emit(JSON.stringify(info)); }
      catch (e) { /* */ }
    },
    // карточка личного чата — для подписи под названием (должность собеседника)
    async get_chat_card(chatIdStr) {
      try { const info = await Kov.Api.get('chat/info/' + Number(chatIdStr)); backend.chatCardLoaded.emit(JSON.stringify(info)); }
      catch (e) { /* подписи не будет */ }
    },
    async search_messages(query) {
      if (!activeChatId) return;
      try {
        const results = await Kov.Api.get('chat/' + activeChatId + '/search', { query: { query, limit: 50 } });
        const list = Array.isArray(results) ? results : (results && results.results) || [];
        backend.searchResultsReceived.emit(JSON.stringify(list.map(formatUrls)));
      } catch (e) { backend.searchResultsReceived.emit('[]'); }
    },

    send_text_message(text, replyTo) {
      if (!activeChatId) return;
      // Метка отправителя: сервер вернёт её в рассылке, и временное
      // сообщение с часами заменится настоящим, а не задвоится
      const token = 'c' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
      const payload = { type: 'message', chat_id: activeChatId, text, client_id: token };
      if (replyTo) payload.reply_to = Number(replyTo);
      Kov.WS.send(payload);
      const me = Kov.Store.state.me || {};
      emitMessage({
        id: 'tmp:' + token, client_id: token, pending: true,
        chat_id: activeChatId, text, sender_id: me.id,
        sender_name: Kov.UI.fullName(me, true),
        sender_last_name: me.last_name, sender_first_name: me.first_name,
        timestamp: new Date().toISOString(),
        parent_id: replyTo ? Number(replyTo) : null,
        files: [], reactions: {}, reactions_detailed: [],
      });
    },
    edit_message(msgIdStr, newText) {
      if (!activeChatId) return;
      Kov.WS.send({ type: 'edit', chat_id: activeChatId, message_id: Number(msgIdStr), new_text: newText });
    },
    delete_message(msgIdStr) { Kov.WS.send({ type: 'delete', message_id: Number(msgIdStr) }); },
    async confirm_delete_message(msgIdStr, preview) {
      const text = String(preview || '').trim();
      const short = text.length > 90 ? text.slice(0, 87) + '…' : text;
      const ok = await Kov.UI.confirm('Удаление сообщения',
        short ? 'Удалить сообщение «' + short + '»? Отменить это будет нельзя.'
              : 'Удалить это сообщение? Отменить это будет нельзя.',
        { okText: 'Удалить', danger: true });
      if (ok) backend.delete_message(msgIdStr);
    },
    async send_reaction(msgIdStr, reactionType) {
      try { await Kov.Api.post('chat/message/' + Number(msgIdStr) + '/reaction', { json: { message_id: Number(msgIdStr), reaction_type: reactionType } }); }
      catch (e) { Kov.UI.toast('Реакция не отправлена', 'warning'); }
    },

    open_file_dialog() {
      // Телефон: шторка как в Telegram — выбор, правка, подпись и отправка без промежуточного окна
      if (Kov.SendDialog && Kov.SendDialog.isMobile()) {
        if (Kov.AttachSheet) {
          const chatId = activeChatId;
          const reply = typeof replyToMsgId !== 'undefined' && replyToMsgId ? Number(replyToMsgId) : null;
          return Kov.AttachSheet.open(files => openSendDialog(files), { onSend: (files, caption, edits) => {
            if (typeof clearInfoPanel === 'function') clearInfoPanel();
            sendInGroups(chatId, files, caption, reply, edits, null);   // больше 10 — несколько сообщений
          } });
        }
        return Kov.SendDialog.attachMenu(files => openSendDialog(files));
      }
      const input = h('input', { type: 'file', multiple: true, hidden: true });
      input.onchange = () => { openSendDialog(input.files); input.remove(); };
      document.body.append(input); input.click();
    },
    /** Сколько файлов можно приложить к одному сообщению */
    max_files() { return MAX_PENDING; },
    /** Файлы, брошенные в окно чата или выбранные снаружи — в окно отправки */
    drop_files(fileList) { openSendDialog(fileList); },
    async send_message_with_files(text, filesJson, replyTo) {
      if (!activeChatId) return;
      let items = []; try { items = JSON.parse(filesJson) || []; } catch (e) { /* */ }
      const files = items.map(i => pendingFiles.get(i.filepath)).filter(Boolean);
      const edits = {};
      items.forEach((i, idx) => { const e = pendingEdits.get(i.filepath); if (e) edits[idx] = e; });
      items.forEach(i => { pendingFiles.delete(i.filepath); pendingEdits.delete(i.filepath); });
      if (!files.length) { if (text) backend.send_text_message(text, replyTo); items.forEach(i => dropPreview(i.filepath)); return; }
      await sendInGroups(activeChatId, files, text, replyTo, edits, items.map(i => i.filepath));
    },

    open_media(msgIdStr, mediaJson) {
      let item = null; try { item = JSON.parse(mediaJson); } catch (e) { return; }
      const files = (item.files && item.files.length) ? item.files : [item];
      const clickedName = fileName(item);
      // смахиванием листаются все фото и видео чата (загруженная история) по порядку отправки
      const all = chatMedia();
      const at = all.findIndex(m => m.msg === String(msgIdStr) && m.filename === clickedName);
      const media = at >= 0 ? all
        : files.filter(f => isMedia(fileName(f))).map(f => ({ src: mediaSrc(f), filename: fileName(f), download: fileHref(f) }));
      if (!media.length) return;
      const idx = at >= 0 ? at : Math.max(0, media.findIndex(m => m.filename === clickedName));
      const preview = m => (typeof previewText === 'function' ? previewText(m.caption || '') : '') || 'Файл';
      Kov.Files.viewer(media, idx, { actions: {
        reply: m => { if (typeof prepareReply === 'function' && m.msg) prepareReply(m.msg, preview(m)); },
        forward: m => { if (m.msg) backend.forward_message(m.msg, preview(m)); },
        remove: m => { if (m.msg && m.mine) backend.confirm_delete_message(m.msg, preview(m)); },
        canRemove: m => !!m.mine,
      } });
    },
    open_file(msgIdStr, fileJson) {
      let f = null; try { f = JSON.parse(fileJson); } catch (e) { return; }
      const name = fileName(f) || 'Файл';
      // Показать в браузере можно только то, что он умеет рисовать сам:
      // картинки, PDF и текст. Документ или архив без этого открывался
      // пустой вкладкой, поэтому такие файлы сразу скачиваем.
      const isSheet = Kov.SheetView && Kov.SheetView.isSheet(name);
      if (!isSheet && Kov.Files && Kov.Files.isViewable && !Kov.Files.isViewable(name)) {
        Kov.UI.toast('«' + name + '» браузер показать не может — скачиваем', 'info');
        if (Kov.Downloads) return Kov.Downloads.download(fileHref(f), name);
        const a = h('a', { href: fileHref(f), download: name });
        document.body.append(a); a.click(); a.remove();
        return;
      }
      openInTab(f, name);
    },
    download_file(msgIdStr, fileJson) {
      let f = null; try { f = JSON.parse(fileJson); } catch (e) { return; }
      const files = (f.files && f.files.length) ? f.files : [f];
      files.forEach((file, i) => setTimeout(() => {
        // через панель загрузок с прогрессом
        if (Kov.Downloads) return Kov.Downloads.download(fileHref(file), fileName(file) || 'file');
        const a = h('a', { href: fileHref(file), download: fileName(file) || 'file' });
        document.body.append(a); a.click(); a.remove();
      }, i * 300));
    },

    forward_message(msgIdStr, preview) { forwardDialog(Number(msgIdStr), preview); },
    forward_messages(ops) { forwardDialog(ops, 'Сообщений: ' + ops.length); },
    async confirm_delete_messages(ids) {
      const ok = await Kov.UI.confirm('Удаление сообщений', 'Удалить выбранные сообщения (' + ids.length + ')? Отменить это будет нельзя.',
        { okText: 'Удалить', danger: true });
      if (ok) ids.forEach(id => backend.delete_message(String(id)));   // сервер всё равно удалит только свои
    },
    show_add_menu() { addMenu(); },
    request_user_info_card(userIdStr) { userCard(Number(userIdStr)); },
    request_chat_info_card(chatIdStr) { infoCard(Number(chatIdStr)); },
    open_group_settings(chatIdStr) { infoCard(Number(chatIdStr)); },
  };

  // ── адреса файлов ────────────────────────────────────────────────────────
  function host() { return (Kov.Api.state.host || location.origin).replace(/\/+$/, ''); }
  function storagePath(p) {
    let clean = String(p || '').replace(/\\/g, '/');
    if (!clean) return '';
    if (/^https?:\/\//i.test(clean) || clean.startsWith('data:')) return clean;
    if (!clean.startsWith('/')) clean = '/' + clean;
    if (!clean.startsWith('/file_storage')) clean = '/file_storage' + clean;
    return host() + clean;
  }
  function formatUrls(msg) {
    if (!msg) return msg;
    if (Array.isArray(msg.files)) msg.files.forEach(f => { const u = f.url || f.filepath || f.file_path || ''; if (u) f.url = storagePath(u); });
    if (msg.file_path) msg.url = storagePath(msg.url || msg.file_path);
    return msg;
  }
  function fileName(f) { return (f && (f.filename || f.file_name)) || ''; }
  function isImage(name) { const n = String(name || '').toLowerCase(); return IMAGE_EXT.some(e => n.endsWith(e)); }
  function isVideo(name) { const n = String(name || '').toLowerCase(); return VIDEO_EXT.some(e => n.endsWith(e)); }
  function isMedia(name) { return isImage(name) || isVideo(name); }
  function withToken(url) { if (!url || !/^https?:/i.test(url)) return url; return url + (url.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(Kov.Api.mediaToken()); }
  /** Картинки и видео сервер отдаёт распакованными по адресу файла; видео у него всегда .webm */
  function mediaSrc(f) {
    let u = storagePath(f.url || f.file_path || '');
    u = u.replace(/\.gz$/, '');
    // .mp4 и .webm сервер отдаёт как есть; прочие расширения бывают только у старых записей
    if (isVideo(fileName(f))) u = u.replace(/\.(avi|mov|mkv|ts|wmv|flv)$/i, '.webm');
    return withToken(u);
  }
  function fileHref(f) { return withToken(storagePath(f.url || f.file_path || '')); }

  /**
   * Путь предпросмотра документа: сервер превратит его в PDF.
   *
   * Адрес файла к этому месту уже прошёл через storagePath() и стал полным
   * («http://host/file_storage/…»), поэтому снимать надо и адрес сервера, и
   * префикс хранилища. Без этого получалась ссылка вида
   * «file_preview/http://host/file_storage/…» — относительная, браузер считал
   * её от страницы, и запрос уходил не в API, а в статику веб-приложения:
   * сервер падал с ошибкой имени файла.
   */
  function previewHref(f) {
    let raw = String(f.url || f.file_path || '').replace(/\\/g, '/');
    raw = raw.split('?')[0].replace(/^https?:\/\/[^/]+/i, '');
    raw = raw.replace(/^\/+/, '').replace(/^file_storage\//, '');
    return withToken(host() + '/file_preview/' + raw);
  }

  /**
   * Открыть файл соседней вкладкой. Картинки, PDF и текст сервер отдаёт как
   * есть; документ сначала превращается в PDF, и только если это не вышло —
   * файл скачивается.
   */
  /** Путь разбора таблицы для вложения чата: у него нет номера, только путь */
  function sheetHref(f) {
    let raw = String(f.url || f.file_path || '').replace(/\\/g, '/');
    raw = raw.split('?')[0].replace(/^https?:\/\/[^/]+/i, '');
    raw = raw.replace(/^\/+/, '').replace(/^file_storage\//, '');
    return 'sheet_preview/' + raw;
  }

  async function openInTab(f, name) {
    let href = fileHref(f);
    // PDF, Word и PowerPoint — просмотрщиком устройства, как в задачах и проектах
    if (Kov.Files && Kov.Files.isDoc && Kov.Files.isDoc(name)) return Kov.Files.openDocument(href, name, previewHref(f));
    // Таблицу открываем настоящими ячейками — так же, как в задачах и проектах
    if (Kov.SheetView && Kov.SheetView.isSheet(name)) {
      Kov.SheetView.openTab({ title: name, url: sheetHref(f), downloadUrl: fileHref(f) });
      return;
    }
    if (Kov.Files && Kov.Files.isOffice && Kov.Files.isOffice(name)) {
      const probe = previewHref(f);
      try {
        const res = await fetch(probe, { headers: { Range: 'bytes=0-0' } });
        if (!res.ok) throw new Error(String(res.status));
        href = probe;
      } catch (e) {
        Kov.UI.toast('Показать «' + name + '» не получилось — скачиваем', 'info');
        if (Kov.Downloads) return Kov.Downloads.download(fileHref(f), name);
        const dl = h('a', { href: fileHref(f), download: name });
        document.body.append(dl); dl.click(); dl.remove();
        return;
      }
    }
    const a = h('a', { href, target: '_blank', rel: 'noopener' });
    document.body.append(a); a.click(); a.remove();
  }

  /** Как _aggregate_grouped_messages десктопа: сообщения одной группы → одно с files[] */
  function aggregate(messages) {
    const groups = new Map();
    messages.forEach(m => {
      const gid = m.group_id;
      const key = gid && !['', '0', 'null'].includes(String(gid)) ? String(gid) : '__single_' + m.id;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(m);
    });
    const out = [];
    groups.forEach(list => {
      // Родитель группы — сообщение с текстом или помеченное file_group.
      // Раньше при группе без текста брался первый попавшийся элемент, а им
      // мог оказаться дочерний файл: у него parent_id указывает на родителя,
      // и такое сообщение рисовалось как ответ на самого себя.
      const ids = new Set(list.map(m => Number(m.id)));
      let parent = list.find(m => m.special === 'file_group')
        || list.find(m => !m.parent_id || !ids.has(Number(m.parent_id)))
        || list.find(m => m.text) || list[0];
      const files = [];
      list.forEach(m => {
        if (m.file_path || m.url) {
          const info = { id: m.id, filename: fileName(m) || 'Файл', file_name: fileName(m) || 'Файл', file_path: m.file_path, url: storagePath(m.url || m.file_path), size: m.file_size || 0, is_image: m.is_image || isImage(fileName(m)) };
          if (!files.some(f => f.url === info.url)) files.push(info);
        }
        (m.files || []).forEach(f => { if (!files.some(x => x.url === f.url)) files.push(f); });
      });
      const merged = Object.assign({}, parent);
      // связь «файл → родитель» внутри своей же группы — это не ответ
      if (merged.parent_id && ids.has(Number(merged.parent_id))) delete merged.parent_id;
      if (!merged.text) { const withText = list.find(m => m.text); if (withText) merged.text = withText.text; }
      if (files.length) merged.files = files; else if (!merged.files) merged.files = [];
      if (list.length > 1) { merged.special = merged.special || 'grouped_message'; merged.group_id = merged.group_id || parent.group_id; }
      out.push(merged);
    });
    return out.sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
  }

  // Последняя загрузка истории: переход к сообщению другого чата ждёт её,
  // иначе обычная история, пришедшая позже, стирала уже показанную цель
  let lastHistory = Promise.resolve();

  function history(chatIdStr, query, newer) {
    const id = Number(chatIdStr);
    lastHistory = (async () => {
      try {
        const list = await Kov.Api.get('chat/history/' + id, { query });
        const first = !newer && !(query && query.before_id);
        // пузырь загрузки — только у своего чата: чужая отправка в этой ленте не рисуется
        if (first && typeof tempUploadData !== 'undefined' && tempUploadData && !activeUploads.has(String(id))) tempUploadData = null;
        backend.historyLoaded.emit(JSON.stringify(aggregate((list || []).map(formatUrls))), id, newer);
        if (first) setTimeout(() => restoreUpload(id), 0);
      } catch (e) {
        console.error('История чата не загрузилась: ' + (e && (e.status || '') + ' ' + (e.message || e)));
        backend.historyLoaded.emit('[]', id, newer);
      }
    })();
    return lastHistory;
  }

  const pendingEdits = new Map();     // ключ файла → правки видео

  /** Окно отправки: превью, подпись, правка фото и видео */
  function openSendDialog(fileList) {
    if (!activeChatId || !fileList || !fileList.length) return;
    const input = document.getElementById('messageInput');
    const text = input ? input.value : '';
    if (input) input.value = '';
    Kov.SendDialog.open({
      files: [...fileList], text, max: MAX_PENDING,
      onCancel: restored => { if (input) { input.value = restored || ''; input.dispatchEvent(new Event('input')); Kov.UI.softFocus(input); } },
      onSend: (files, caption, edits) => {
        const replyTo = (typeof replyToMsgId !== 'undefined' && replyToMsgId) ? replyToMsgId : null;
        if (typeof clearInfoPanel === 'function') clearInfoPanel();
        sendInGroups(activeChatId, files, caption, replyTo, edits, null);
      },
    });
  }

  /** Номер последнего сообщения открытого чата — до отправки */
  function lastMessageId() {
    if (typeof chatMessages === 'undefined' || !Array.isArray(chatMessages)) return 0;
    return chatMessages.reduce((m, x) => Math.max(m, Number(x.id) || 0), 0);
  }

  /** Своё сообщение с этими файлами, новее lastBefore — после потерянного ответа (имена без расширений:
   *  сервер мог сменить формат фото и видео) */
  async function findSent(chatId, names, lastBefore) {
    const me = String(Kov.Api.state.userId);
    const stem = n => String(n || '').replace(/\.[^.]+$/, '').toLowerCase();
    const want = new Set(names.map(stem));
    for (const delay of [0, 1500, 3000, 4000]) {
      if (delay) await new Promise(r => setTimeout(r, delay));
      try {
        const list = await Kov.Api.get('chat/history/' + chatId, { query: { limit: 20 } });
        const found = aggregate((list || []).map(formatUrls)).find(m => String(m.sender_id) === me
          && (Number(m.id) || 0) > lastBefore && (m.files || []).some(f => want.has(stem(fileName(f)))));
        if (found) return found;
      } catch (err) { /* связь ещё не вернулась — ждём */ }
    }
    return null;
  }

  /** Фото и видео всех загруженных сообщений чата — от старых к новым */
  // телефон: пока открыт чат — без верхней строки приложения (меню, раздел, уведомления)
  function syncAppTopBar() {
    const kc = document.getElementById('kovChat');
    const inChat = !!kc && kc.classList.contains('chat-open') && /^#\/messenger/.test(location.hash);
    document.documentElement.classList.toggle('kov-in-chat', inChat);
  }
  window.addEventListener('hashchange', () => setTimeout(syncAppTopBar, 0));

  function chatMedia() {
    if (typeof chatMessages === 'undefined' || !Array.isArray(chatMessages)) return [];
    const when = m => Date.parse(m.timestamp) || 0;
    const out = [];
    chatMessages.slice().sort((a, b) => when(a) - when(b) || (Number(a.id) || 0) - (Number(b.id) || 0)).forEach(msg => {
      const files = (msg.files && msg.files.length) ? msg.files : (msg.file_path || msg.url ? [msg] : []);
      // для просмотра: автор, время, подпись, миниатюра и «своё ли» (можно ли удалить)
      const mine = typeof currentUserId !== 'undefined' && String(msg.sender_id) === String(currentUserId);
      files.filter(f => isMedia(fileName(f)))
        .forEach(f => out.push({ src: mediaSrc(f), filename: fileName(f), download: fileHref(f), msg: String(msg.id),
          who: msg.sender_name || '', at: msg.timestamp || '', caption: msg.text && msg.text !== 'null' ? msg.text : '', mine,
          thumb: f.thumb_url || '', micro: f.micro_thumb || '', size: f.file_size || f.size || 0 }));
    });
    return out;
  }

  /**
   * Одна отправка на всю группу: файлы уходят одним запросом, сервер
   * сжимает их по очереди и отдаёт ход по каждому — он и рисуется на
   * плитках временного сообщения.
   */
  async function sendGroup(chatId, files, text, replyTo, edits, keys) {
    const lastBefore = lastMessageId();
    const previews = files.map((f, i) => ({
      filename: f.name, size: f.size, is_image: isImage(f.name),
      preview_url: (isImage(f.name) || isVideo(f.name)) ? URL.createObjectURL(f) : null,
    }));
    // идущая отправка помнится по чату: вышли из чата и вернулись — пузырь с ходом загрузки на месте;
    // ход рисуется только в своём чате, чтобы две отправки не смешались
    const ctrl = new AbortController();
    const up = { text, previews, percent: 0, label: '', detail: null, ctrl };
    activeUploads.set(String(chatId), up);
    const here = () => String(activeChatId) === String(chatId);
    const emit = v => { if (here()) backend.uploadProgress.emit(v); };
    if (here() && typeof showTempUploadMessage === 'function') showTempUploadMessage(text, previews);
    emit(0);
    try {
      const fields = { chat_id: chatId };
      if (text) fields.captions = text;
      if (replyTo) fields.reply_to_id = Number(replyTo);
      if (edits && Object.keys(edits).length) fields.edits = JSON.stringify(edits);
      const created = await Kov.Files.upload(files, 'chat/grouped-message', fields, (p, label, detail) => {
        up.percent = Math.min(99, Math.round(p * 100)); up.label = label; if (detail) up.detail = detail;
        if (!here()) return;
        if (typeof tempUploadData !== 'undefined' && tempUploadData) tempUploadData.label = label;
        backend.uploadProgress.emit(up.percent);
        if (detail && typeof setTempDetail === 'function') setTempDetail(detail);
      }, ctrl.signal);
      activeUploads.delete(String(chatId));
      emit(100);
      // Полосу видно на сотне: если сразу подставить настоящее сообщение,
      // временный пузырь исчезнет и человек так и не увидит конец загрузки
      await new Promise(r => setTimeout(r, 650));
      setTimeout(() => emit(-2), 3500);
      applySent(chatId, created, files.length);     // без await: дозагрузка идёт своим ходом
    } catch (e) {
      activeUploads.delete(String(chatId));
      if (ctrl.signal.aborted || (e && e.aborted)) { emit(-2); Kov.UI.toast('Отправка файлов отменена', 'info'); return; }
      // связь оборвалась, а файлы, возможно, уже сохранены (ответ потерялся по дороге) — проверяем историю чата
      const saved = e && e.network ? await findSent(chatId, files.map(f => f.name), lastBefore) : null;
      if (saved) {
        emit(100);
        setTimeout(() => emit(-2), 1500);
        applySent(chatId, saved, files.length);
        return;
      }
      emit(-2);
      Kov.UI.toast('Не удалось отправить файлы: ' + Kov.UI.errorText(e), 'error');
    } finally {
      activeUploads.delete(String(chatId));
      previews.forEach(pv => { if (pv.preview_url) { try { URL.revokeObjectURL(pv.preview_url); } catch (err) { /* */ } } });
      (keys || []).forEach(k => dropPreview(k));
    }
  }

  /** Вернулись в чат, где идёт отправка файлов, — пузырь с ходом загрузки снова на месте */
  function restoreUpload(chatId) {
    const up = activeUploads.get(String(chatId));
    if (!up || typeof showTempUploadMessage !== 'function') return;
    if (typeof tempUploadData !== 'undefined' && tempUploadData) return;
    showTempUploadMessage(up.text, up.previews);
    if (typeof tempUploadData !== 'undefined' && tempUploadData) tempUploadData.label = up.label;
    backend.uploadProgress.emit(up.percent);
    if (up.detail && typeof setTempDetail === 'function') setTempDetail(up.detail);
  }

  /**
   * Ответ сервера на отправку — самый полный источник: там и номер
   * сообщения, и список сохранённых файлов. Рисуем его сразу, не дожидаясь
   * сокета. Если файлов пришло меньше, чем отправляли, добираем сообщение
   * из истории — раньше в чате оставался пустой пузырь до обновления страницы.
   */
  async function applySent(chatId, created, expected) {
    if (!created || created.id === undefined || created.id === null) return;
    if (String(chatId) !== String(activeChatId)) return;
    const failed = Array.isArray(created.failed) ? created.failed : [];
    if (failed.length) Kov.UI.toast('Не удалось сохранить: ' + failed.join(', '), 'warning');

    const id = String(created.id);
    const pend = pendingGrouped.get(id);
    if (pend) { clearTimeout(pend.timer); pendingGrouped.delete(id); }

    const msg = Object.assign({}, created);
    if (!Array.isArray(msg.files)) msg.files = [];
    if (!msg.chat_id) msg.chat_id = chatId;
    emitMessage(msg);

    if (msg.files.length >= Math.max(0, expected - failed.length)) return;
    for (const delay of [900, 3000]) {
      await new Promise(r => setTimeout(r, delay));
      if (String(chatId) !== String(activeChatId)) return;
      try {
        const list = await Kov.Api.get('chat/history/' + chatId, { query: { around_id: Number(id), limit: 5 } });
        const fresh = aggregate((list || []).map(formatUrls)).find(m => String(m.id) === id);
        if (fresh && (fresh.files || []).length > msg.files.length) { emitMessage(fresh); return; }
      } catch (e) { /* попробуем ещё раз */ }
    }
  }

  function uploadOne(file, chatId, onLoaded) {
    return new Promise((resolve, reject) => {
      const form = new FormData(); form.append('file', file, file.name); form.append('chat_id', String(chatId));
      const xhr = new XMLHttpRequest();
      xhr.open('POST', Kov.Api.url('chat/upload'));
      xhr.setRequestHeader('Authorization', 'Bearer ' + Kov.Api.state.token);
      xhr.upload.onprogress = e => { if (e.lengthComputable) onLoaded(e.loaded); };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) { try { resolve(JSON.parse(xhr.responseText)); } catch (e) { resolve({}); } }
        else reject(new Error('HTTP ' + xhr.status));
      };
      xhr.onerror = () => reject(new Error('Сеть недоступна'));
      xhr.send(form);
    });
  }

  // Больше десяти вложений за раз не отправляем: и сервер, и предпросмотр
  // на это не рассчитаны, а очередь загрузки становится неуправляемой
  const MAX_ATTACHMENTS = 10;   // файлов в одном сообщении
  const MAX_PENDING = 100;      // за одну отправку: больше 10 — несколько сообщений по 10

  /** Одно сообщение — не больше MAX_ATTACHMENTS файлов: остальное уходит следующими сообщениями, по порядку */
  async function sendInGroups(chatId, files, text, replyTo, edits, keys) {
    const list = [...files];
    for (let start = 0; start < list.length; start += MAX_ATTACHMENTS) {
      const part = list.slice(start, start + MAX_ATTACHMENTS);
      const partEdits = {};
      Object.keys(edits || {}).forEach(k => {
        const i = Number(k) - start;
        if (i >= 0 && i < part.length) partEdits[i] = edits[k];
      });
      const first = start === 0;   // подпись и ответ — у первого сообщения
      await sendGroup(chatId, part, first ? text : '', first ? replyTo : null, partEdits,
        keys ? keys.slice(start, start + MAX_ATTACHMENTS) : null);
    }
  }

  // Ссылки на превью держим отдельно, чтобы вовремя их отзывать
  const previewUrls = new Map();
  function dropPreview(key) {
    const url = previewUrls.get(key);
    if (url) { try { URL.revokeObjectURL(url); } catch (e) { /* */ } previewUrls.delete(key); }
  }

  function addFiles(fileList) {
    if (!activeChatId || !fileList || !fileList.length) return;
    const already = (typeof pendingCount === 'function') ? pendingCount() : 0;
    const room = Math.max(0, MAX_PENDING - already);
    if (!room) return Kov.UI.toast('За раз можно отправить не больше ' + MAX_PENDING + ' файлов', 'warning');
    let list = [...fileList];
    if (list.length > room) {
      Kov.UI.toast('Добавлено ' + room + ' из ' + list.length + ': за раз можно отправить не больше ' + MAX_PENDING + ' файлов', 'warning');
      list = list.slice(0, room);
    }
    const items = [];
    list.forEach(file => {
      const key = 'web:' + (++fileSeq) + ':' + file.name;
      pendingFiles.set(key, file);
      const item = { filepath: key, filename: file.name, size: file.size, is_image: isImage(file.name) };
      items.push(item);
      // Превью прямо из выбранного файла: для фото и для видео. Ссылка на
      // объект не грузит память копией файла и отзывается после отправки.
      if (isImage(file.name) || isVideo(file.name)) {
        try {
          item.preview_url = URL.createObjectURL(file);
          previewUrls.set(key, item.preview_url);
        } catch (e) { /* браузер не дал — покажем значок */ }
      }
    });
    if (typeof addAttachedFiles === 'function') addAttachedFiles(JSON.stringify(items));
  }

  // ── список чатов и счётчик ───────────────────────────────────────────────
  function pushChats() {
    backend.chatsUpdated.emit(JSON.stringify(chats));
    const unread = chats.reduce((s, c) => s + (String(c.id) === String(activeChatId) ? 0 : (c.unread_count || 0)), 0);
    Kov.NotifyCenter.setUnreadChats(unread);
  }
  async function loadChats() {
    try { chats = await Kov.Api.get('chat/list') || []; } catch (e) { chats = []; }
    pushChats();
    return chats;
  }

  function openById(id) {
    const chat = chats.find(c => String(c.id) === String(id));
    if (!chat || typeof openChatFromBackend !== 'function') return false;
    openChatFromBackend(JSON.stringify({ id: chat.id, name: chat.name, is_group: chat.is_group, avatar_path: chat.is_group ? chat.image_path : chat.avatar_url, last_name: chat.last_name, first_name: chat.first_name }));
    return true;
  }

  // ── загрузка chat.js один раз ────────────────────────────────────────────
  function loadAssets() {
    if (loading) return loading;
    loading = (async () => {
      const markup = await (await fetch('chat/chat-markup.html', { cache: 'no-cache' })).text();
      root = h('div#kovChat', { html: markup });
      // корень должен быть в документе до загрузки chat.js: скрипт ищет элементы по id при старте
      const holder = h('div', { style: { position: 'fixed', left: '-9999px', top: '0', width: '1000px', height: '600px' } }, root);
      document.body.append(holder);
      await new Promise((res, rej) => { const l = h('link', { rel: 'stylesheet', href: 'chat/chat.css', onload: res, onerror: rej }); document.head.append(l); });
      window.qt = { webChannelTransport: {} };
      window.QWebChannel = function (_transport, cb) { cb({ objects: { backend } }); };
      await new Promise((res, rej) => { const s = h('script', { src: 'chat/chat.js', onload: res, onerror: rej }); document.body.append(s); });
      holder.remove();
      loaded = true;
      if (typeof setApiToken === 'function') setApiToken(Kov.Api.mediaToken());
      window.addEventListener('kov:file-token', () => { if (typeof setApiToken === 'function') setApiToken(Kov.Api.mediaToken()); });
      root.querySelector('#kovChatBack').onclick = () => { if (typeof closeActiveChat === 'function') closeActiveChat(); backend.chat_closed(); };
      // перетаскивание файлов в открытый чат — в десктопе это делал Qt
      root.addEventListener('drop', e => { e.preventDefault(); if (e.dataTransfer && e.dataTransfer.files.length) openSendDialog(e.dataTransfer.files); });
      root.addEventListener('dragover', e => e.preventDefault());
    })();
    return loading;
  }

  // ── раздел ───────────────────────────────────────────────────────────────
  async function view(section, chatId) {
    pendingOpenId = chatId ? Number(chatId) : null;
    section.classList.add('messenger-section');
    section.append(Kov.UI.bigLoading('Открываем мессенджер…'));
    try { await loadAssets(); } catch (e) { Kov.UI.clear(section); section.append(h('div.empty', 'Не удалось загрузить мессенджер: ' + Kov.UI.errorText(e))); return; }
    if (!section.isConnected) return;
    Kov.UI.clear(section);
    section.append(root);
    if (typeof setApiToken === 'function') setApiToken(Kov.Api.mediaToken());
    await loadChats();
    if (pendingOpenId) { openById(pendingOpenId); pendingOpenId = null; }
    else if (activeChatId) {
      // вернулись в раздел с уже открытым чатом — снова считаем его открытым
      root.classList.add('chat-open'); syncAppTopBar();
      Kov.NotifyCenter.setActiveChat(activeChatId);
    }
  }

  // ── события WebSocket (общий сокет приложения) ───────────────────────────
  // Уже показанные группы: по ним доставляются файлы, пришедшие с опозданием
  const recentGroups = new Map();     // id сообщения → сообщение

  /** До какого сообщения чат прочитан другими — для второй галочки */
  async function loadReadState(chatId) {
    try {
      const data = await Kov.Api.get('chat/' + chatId + '/read-state');
      if (data && String(chatId) === String(activeChatId)) {
        backend.readState.emit(JSON.stringify({ chat_id: chatId, upto: data.upto || 0 }));
      }
    } catch (e) { /* без него просто не будет второй галочки */ }
  }

  function emitMessage(msg) {
    const parent = String(msg.id);
    if (orphanFiles.has(parent)) { msg.files = (msg.files || []).concat(orphanFiles.get(parent)); orphanFiles.delete(parent); }
    if (msg.group_id) {
      recentGroups.set(parent, msg);
      setTimeout(() => recentGroups.delete(parent), 120000);
    }
    backend.messageReceived.emit(JSON.stringify(formatUrls(msg)));
  }
  function onEvent(evt) {
    // Кто-то прочитал чат — у отправителя появляется вторая галочка
    if (evt && evt.type === 'chat.read') {
      if (String(evt.chat_id) === String(activeChatId) && evt.user_id !== Kov.Api.state.userId) {
        backend.readState.emit(JSON.stringify({ chat_id: evt.chat_id, upto: evt.upto || 0 }));
      }
      return;
    }
    if (!loaded) return;
    const type = evt.type;
    if (type === 'file_item' || evt.special === 'file_item') {
      const parent = String(evt.parent_id || evt.group_id || '');
      const name = evt.file_name || evt.filename || 'Файл';
      const item = { id: evt.id, file_name: name, filename: name, file_path: evt.file_path, url: evt.url, size: evt.size || evt.file_size, is_image: evt.is_image || isMedia(name) };
      if (parent && pendingGrouped.has(parent)) pendingGrouped.get(parent).msg.files.push(item);
      else if (parent && recentGroups.has(parent)) {
        // сообщение уже нарисовано — дополняем его файлом, а не теряем
        const msg = recentGroups.get(parent);
        msg.files = (msg.files || []).concat([item]);
        emitMessage(msg);
      }
      else if (parent) { if (!orphanFiles.has(parent)) orphanFiles.set(parent, []); orphanFiles.get(parent).push(item); }
      else emitMessage(evt);
      return;
    }
    if (type === 'grouped_message' || type === 'message') {
      const id = String(evt.id);
      if (type === 'grouped_message' || evt.special === 'grouped_message' || evt.is_grouped) {
        const msg = Object.assign({}, evt, { files: (evt.files || []).slice() });
        pendingGrouped.set(id, { msg, timer: setTimeout(() => { pendingGrouped.delete(id); emitMessage(msg); }, 500) });
        return;
      }
      emitMessage(Object.assign({}, evt));
      return;
    }
    if (type === 'edit') { backend.messageUpdated.emit(JSON.stringify(evt)); return; }
    if (type === 'delete') { backend.messageDeleted.emit(JSON.stringify(evt)); return; }
    if (type === 'reaction') { backend.reactionsUpdated.emit(JSON.stringify(evt)); return; }
    if (type === 'special_message') { emitMessage(Object.assign({}, evt)); return; }
    if (type === 'chats_updated') { chats = evt.chats || []; pushChats(); return; }
    // Права в группе изменились — открытая карточка обязана перестроиться,
    // иначе снятый администратор ещё какое-то время видит свои кнопки
    if (['group_role_changed', 'member_remove', 'member_add', 'member_leave',
         'owner_transfer', 'group_deleted', 'participant_removed', 'participants_added'].includes(type)) {
      const open = document.querySelector('.modal-root .modal');
      if (open && evt.chat_id) {
        const closeBtn = open.querySelector('.modal-head .close-x');
        if (type === 'group_deleted') { if (closeBtn) closeBtn.click(); }
        else { if (closeBtn) closeBtn.click(); infoCard(Number(evt.chat_id)); }
      }
      loadChats();
      return;
    }
    if (type === 'group_updated' || type === 'group_role_changed') {
      if (activeChatId && String(evt.chat_id) === String(activeChatId)) backend.get_group_info(String(activeChatId));
      loadChats();
      return;
    }
    if (type === 'action_rejected' || type === 'error') { if (evt.message) Kov.UI.toast(evt.message, 'warning'); return; }
    if (type === 'removed_from_chat' || type === 'group_deleted') {
      if (activeChatId && String(evt.chat_id) === String(activeChatId) && typeof clearChat === 'function') { clearChat(); backend.chat_closed(); }
      Kov.UI.toast(type === 'group_deleted' ? 'Группа удалена' : 'Вас удалили из чата', 'warning');
      loadChats();
    }
  }

  // ── диалоги, бывшие окнами Qt ────────────────────────────────────────────
  function personLabel(p) { return Kov.UI.fullName(p, true) || p.full_name || p.name || ('#' + p.id); }

  // Сервер отдаёт роль по-английски и называет обычного участника то member,
  // то participant — показываем всегда по-русски, неизвестное считаем участником
  const ROLE_RU = { owner: 'владелец', admin: 'администратор', administrator: 'администратор',
                    member: 'участник', participant: 'участник', user: 'участник' };
  function roleLabel(role) { return ROLE_RU[String(role || '').trim().toLowerCase()] || 'участник'; }

  /**
   * Выбор сотрудников. opts.taken — те, кого выбирать нельзя: они уже в
   * группе (или с ними уже есть личный чат). Как в настольной версии:
   * строка приглушена, отметка недоступна, рядом подпись почему.
   */
  function employeePicker(title, onPick, multi, opts) {
    opts = opts || {};
    const taken = new Map();           // id → подпись, почему нельзя выбрать
    (opts.taken || []).forEach(t => taken.set(Number(t.id), t.note || 'уже добавлен'));
    const selected = new Set();
    const listBox = h('div.checklist', { style: { maxHeight: '20rem' } });
    const counter = h('div.small.muted');
    const search = Kov.UI.searchInput('Поиск по ФИО, отделу, должности…', render);
    let people = [];
    function render(needle) {
      needle = (needle || '').trim().toLowerCase();
      Kov.UI.clear(listBox);
      const rows = people.filter(e => !needle
        || (Kov.UI.fullName(e, true) + ' ' + (e.department || '') + ' ' + (e.position || '')).toLowerCase().includes(needle));
      rows.forEach(e => {
        const note = taken.get(Number(e.id));
        const input = h('input', { type: multi ? 'checkbox' : 'radio', name: 'kovChatPick',
          checked: selected.has(e.id), disabled: !!note,
          onchange: ev => { if (!multi) selected.clear(); ev.target.checked ? selected.add(e.id) : selected.delete(e.id); updateCounter(); } });
        listBox.append(h('label' + (note ? '.off' : ''), input,
          Kov.UI.avatar(e, null, Kov.Store.photoUrl(e.id)),
          h('span', { style: { flex: 1 } }, Kov.UI.fullName(e, true),
            h('span.muted.small', ' — ' + [e.department, e.position].filter(Boolean).join(', '))),
          note ? h('span.chip.info', note) : null));
      });
      if (!rows.length) listBox.append(h('div.muted', { style: { padding: '.4rem' } }, 'Никого не найдено'));
      updateCounter();
    }
    function updateCounter() {
      const free = people.filter(e => !taken.has(Number(e.id))).length;
      counter.textContent = multi
        ? 'Выбрано: ' + selected.size + ' · доступно: ' + free + (taken.size ? ' · уже в списке: ' + taken.size : '')
        : (taken.size ? 'С отмеченными сотрудниками чат уже создан' : '');
    }
    const dlg = Kov.UI.modal({
      title, glyph: 'users', width: '41.4rem',
      body: el => {
        el.append(search.el, counter, listBox);
        // pickable() убирает служебную запись администратора: в собеседники,
        // участники и получатели она не предлагается никому, кроме него самого
        Kov.Store.pickable().then(list => { people = opts.withSelf ? list : list.filter(e => e.id !== Kov.Api.state.userId); render(''); });
      },
      buttons: [{ text: 'Отмена', kind: 'ghost' }, { text: opts.okText || (multi ? 'Добавить' : 'Открыть чат'), icon: 'check', onClick: close => { if (!selected.size) return Kov.UI.toast('Никто не выбран', 'warning'); onPick([...selected]); close(); } }],
    });
    return dlg;
  }

  /** С кем уже есть личный чат — по загруженному списку чатов */
  function privateChatPartners() {
    return (chats || []).filter(c => !c.is_group && c.other_user_id)
      .map(c => ({ id: Number(c.other_user_id), note: 'чат уже есть' }));
  }

  function addMenu() {
    Kov.UI.modal({
      title: 'Новый чат', glyph: 'chat',
      body: el => el.append(
        h('div.chat-tiles',
        h('button.chat-tile', { type: 'button', onclick: () => { el.closest('.modal-root').remove(); employeePicker('Выберите сотрудника', async ids => {
          try { const chat = await Kov.Api.post('chat/private', { json: { target_user_id: ids[0] } }); await loadChats(); openById(chat.id || (chat.chat && chat.chat.id)); }
          catch (e) { Kov.UI.toast('Не удалось создать чат: ' + Kov.UI.errorText(e), 'error'); }
        }, false, { taken: privateChatPartners() }); } }, h('span.circ', icon('user')), h('span.t', 'Личный чат'), h('span.s', 'Переписка с одним сотрудником')),
        h('button.chat-tile', { type: 'button', onclick: () => { el.closest('.modal-root').remove(); newGroupDialog(); } }, h('span.circ', icon('users')), h('span.t', 'Новая группа'), h('span.s', 'Общий чат для нескольких сотрудников')),
      )),
    });
  }

  function newGroupDialog() {
    const name = h('input.input', { placeholder: 'Название группы' });
    Kov.UI.modal({
      title: 'Новая группа', glyph: 'users',
      body: el => el.append(field('Название', name, true), h('div.small.muted', 'Участников можно будет добавить сразу после создания.')),
      buttons: [{ text: 'Отмена', kind: 'ghost' }, { text: 'Создать', icon: 'check', onClick: async close => {
        const title = name.value.trim(); if (!title) return Kov.UI.toast('Введите название', 'warning');
        try {
          const chat = await Kov.Api.post('chat/group', { json: { name: title } });
          close(); await loadChats(); openById(chat.id);
          employeePicker('Участники группы «' + title + '»', async ids => {   // создатель уже в группе
            try { await Kov.Api.post('chat/group/' + chat.id + '/participants', { json: { user_ids: ids } }); Kov.UI.toast('Участники добавлены', 'success'); backend.get_group_info(String(chat.id)); }
            catch (e) { Kov.UI.toast('Не удалось добавить: ' + Kov.UI.errorText(e), 'error'); }
          }, true);
        } catch (e) { Kov.UI.toast('Не удалось создать группу: ' + Kov.UI.errorText(e), 'error'); }
      } }],
    });
  }

  // ── карточки чата и сотрудника ───────────────────────────────────────────

  const MEDIA_CATEGORIES = [['photo', 'Фото', 'image'], ['video', 'Видео', 'video'], ['file', 'Файлы', 'file']];

  /** Полная ссылка на аватар из карточки */
  /**
   * Адрес аватара. Сервер присылает сюда два разных вида путей.
   *
   * Фотография сотрудника приходит как адрес обработчика — «/employees/7/photo».
   * Картинка группы приходит как путь внутри хранилища файлов. Раньше к
   * обоим приписывался префикс хранилища, и у сотрудников получалось
   * «/file_storage/employees/7/photo» — такого файла нет, картинка не
   * загружалась, и в списке участников оставались одни инициалы.
   */
  function avatarSrc(path) {
    if (!path) return null;
    const clean = String(path).replace(/\\/g, '/');
    if (/^https?:\/\//i.test(clean) || clean.startsWith('data:')) return clean;
    // Обработчик сервера, а не файл в хранилище. Шаблон нарочно узкий:
    // картинка группы лежит в хранилище и может начинаться с «chat/» —
    // широкий шаблон увёл бы её на несуществующий адрес.
    if (/^\/?employees\/\d+\/photo$/i.test(clean)) {
      return Kov.Api.fileUrl(clean.replace(/^\/+/, ''));
    }
    return withToken(storagePath(clean));
  }

  /** Открыть картинку во весь экран — то же поведение, что у вложений */
  function openImage(src, name) {
    if (!src) return;
    Kov.Files.viewer([{ src, filename: name || 'photo.jpg', download: src }], 0);
  }

  /**
   * Блок вложений чата: кнопки категорий со счётчиками. По нажатию карточка
   * целиком сменяется списком — как в настольной версии.
   */
  function mediaSection(chatId, getDlg, back) {
    const box = h('div.chat-media', { hidden: true });
    if (!chatId) return box;
    const buttons = {};
    MEDIA_CATEGORIES.forEach(([key, title, glyph]) => {
      const count = h('span.n', '0');
      const btn = h('button.media-cat', { type: 'button', title, disabled: true,
        onclick: () => openMediaList(chatId, key, title, getDlg(), back) },
        icon(glyph), count);
      buttons[key] = { btn, count };
      box.append(btn);
    });
    Kov.Api.get('chat/' + chatId + '/media/summary').then(raw => {
      const summary = (raw && raw.counts) ? raw.counts : raw;
      let any = false;
      MEDIA_CATEGORIES.forEach(([key]) => {
        const n = Number((summary || {})[key] || 0);
        buttons[key].count.textContent = String(n);
        buttons[key].btn.disabled = n === 0;
        if (n) any = true;
      });
      box.hidden = !any;
    }).catch(() => { box.hidden = true; });
    return box;
  }

  /** Список вложений одной категории вместо содержимого карточки */
  async function openMediaList(chatId, category, title, dlg, back) {
    const body = dlg.el.querySelector('.modal-body');
    if (!body) return;
    const saved = [...body.childNodes];
    const list = h('div.chat-media-list');
    const restore = () => { Kov.UI.clear(body); saved.forEach(n => body.append(n)); if (back) back(); };
    Kov.UI.clear(body);
    body.append(
      // Возврат к карточке — крестиком в правом верхнем углу, а не кнопкой
      // Стрелка назад слева, перед папкой и названием раздела
      h('div.row.media-head',
        h('button.media-back', { type: 'button', title: 'Вернуться к карточке', 'aria-label': 'Вернуться к карточке', onclick: restore }, icon('arrow_back')),
        h('span.cat-icon', icon('folder')), h('b', title), h('span.spacer')),
      list);
    list.append(Kov.UI.bigLoading('Загружаем вложения…'));
    let items = [];
    try {
      // Сервер отдаёт {chat_id, category, total, items: [...]}, а не голый список
      const data = await Kov.Api.get('chat/' + chatId + '/media', { query: { category, limit: 200 } });
      items = Array.isArray(data) ? data : ((data && data.items) || []);
    } catch (e) { Kov.UI.clear(list); list.append(h('div.empty', 'Не удалось загрузить вложения')); return; }
    Kov.UI.clear(list);
    if (!items.length) { list.append(h('div.empty', 'Вложений нет')); return; }
    items.forEach(it => {
      const name = it.filename || it.file_name || 'Файл';
      const when = it.timestamp ? Kov.UI.fmtDateTime(it.timestamp) : '';
      const size = it.size || it.file_size;
      const src = it.url ? withToken(storagePath(it.url)) : null;
      const isPhoto = category === 'photo';
      const isVideo = category === 'video';

      // Единая плитка для всех видов: слева превью или значок, справа
      // название, дата и размер
      const thumb = h('span.mt-thumb' + (isPhoto ? '.photo' : isVideo ? '.video' : '.file'));
      if (src && isPhoto) {
        const img = h('img', { src, alt: '', loading: 'lazy' });
        img.addEventListener('error', () => { Kov.UI.clear(thumb); thumb.append(icon('image')); });
        thumb.append(img);
      } else if (src && isVideo) {
        // Кадр из видео браузер отрисует сам, звук не нужен
        const v = h('video', { src: src + '#t=0.2', muted: true, preload: 'metadata', playsInline: true });
        v.addEventListener('error', () => { Kov.UI.clear(thumb); thumb.append(icon('video')); });
        thumb.append(v, h('span.play', icon('video')));
      } else {
        thumb.append(icon(isPhoto ? 'image' : isVideo ? 'video' : 'file'));
      }

      const row = h('div.media-tile', { role: 'button', tabIndex: 0,
        title: name + '\nПравая кнопка — перейти к сообщению' },
        thumb,
        h('div.who', h('b', name), h('span', [when, size ? Kov.Files.fmtSize(size) : null].filter(Boolean).join(' · '))));

      const open = () => {
        if (!src) return;
        if (isPhoto || isVideo) openImage(src, name);
        else window.open(src, '_blank');
      };
      const goto = () => {
        dlg.close();
        if (it.message_id) backend.go_to_message(String(it.message_id), String(it.group_id || ''), String(chatId));
      };
      row.addEventListener('click', open);
      // Переход к сообщению — правой кнопкой, как просили; на телефоне
      // работает долгое нажатие
      row.addEventListener('contextmenu', e => { e.preventDefault(); goto(); });
      let hold = null;
      row.addEventListener('touchstart', () => { hold = setTimeout(goto, 450); }, { passive: true });
      ['touchend', 'touchmove', 'touchcancel'].forEach(ev =>
        row.addEventListener(ev, () => { clearTimeout(hold); }, { passive: true }));
      list.append(row);
    });
  }

  /**
   * Карточка сотрудника: та же подача, что в настольной версии —
   * фото, контакты, кнопка «Написать» и вложения переписки с ним.
   */
  async function userCard(userId, opts) {
    opts = opts || {};
    let person = null;
    // Набор сотрудников с фотографией заполняет раздел «Сотрудники». Если
    // человек зашёл сразу в мессенджер, набор пуст, и photoUrl отвечает
    // «фото нет» — карточка показывала инициалы вместо снимка. Дожидаемся
    // набора здесь; повторные вызовы берут его из памяти.
    try { await Kov.Store.photoIds(); } catch (e) { /* фото просто не будет */ }
    try { person = await Kov.Store.employee(userId); } catch (e) { /* возьмём из карточки чата */ }
    if (!person && opts.person) person = Object.assign({ id: userId }, opts.person);
    // номер чата приходит из карточки; если её нет — ищем в списке чатов
    const existing = (chats || []).find(c => !c.is_group && Number(c.other_user_id) === Number(userId));
    const chatId = opts.chatId || (existing ? existing.id : null);
    const own = Number(userId) === Number(Kov.Api.state.userId);
    const photo = Kov.Store.photoUrl(userId);
    let dlg;
    dlg = Kov.UI.modal({
      title: 'Карточка сотрудника', glyph: 'user', wide: true,
      headButtons: own ? [] : [button('', { kind: 'ghost', icon: 'chat', title: 'Написать сотруднику', onClick: async () => {
        dlg.close();
        if (chatId) return openById(chatId);
        try { const chat = await Kov.Api.post('chat/private', { json: { target_user_id: Number(userId) } }); await loadChats(); openById(chat.id || (chat.chat && chat.chat.id)); }
        catch (e) { Kov.UI.toast('Не удалось открыть чат: ' + Kov.UI.errorText(e), 'error'); }
      } })],
      body: el => {
        const p = person || { id: userId };
        const av = Kov.UI.avatar(p, 'xl', photo);
        if (photo) { av.classList.add('clickable'); av.title = 'Открыть фото'; av.addEventListener('click', () => openImage(photo, 'Фото сотрудника')); }
        el.append(h('div.chat-card-head', av,
          h('div.chat-card-name', Kov.UI.fullName(p, true) || 'Сотрудник'),
          h('div.chat-card-sub', [p.position, p.department].filter(Boolean).join(' · ') || 'Сотрудник')));
        const rows = [['Телефон', p.phone_number], ['Почта', p.email], ['Должность', p.position], ['Отдел', p.department]]
          .filter(([, v]) => v);
        if (rows.length) el.append(Kov.UI.infoCard('id_card', 'Контакты', rows));
        el.append(mediaSection(chatId, () => dlg));
        el.append(h('div.btn-row.end', button('Профиль сотрудника', { kind: 'ghost', icon: 'id_card',
          onClick: () => { dlg.close(); Kov.Views.employees.openProfile(Number(userId)); } })));
      },
    });
    return dlg;
  }

  async function infoCard(chatId, openSettings) {
    let info;
    try { info = await Kov.Api.get('chat/info/' + chatId); } catch (e) { return Kov.UI.toast('Карточка чата недоступна', 'warning'); }
    // Участники группы показываются с фотографиями — набор нужен и здесь
    try { await Kov.Store.photoIds(); } catch (e) { /* фото просто не будет */ }
    const isGroup = !!info.is_group;
    // Личный чат открывается карточкой сотрудника — она богаче.
    // Номер сотрудника берём строго из ответа: раньше при его отсутствии
    // подставлялся номер чата, и открывалась карточка постороннего человека
    // с совпавшим номером.
    if (!isGroup) {
      const other = info.other_user_id || info.user_id || (info.user && info.user.id);
      if (!other) return Kov.UI.toast('Не удалось определить собеседника', 'warning');
      return userCard(Number(other), { chatId: info.chat_id, person: info });
    }
    const myRole = String(info.my_role || '').toLowerCase();
    const canManage = ['owner', 'admin'].includes(myRole);
    const isOwner = myRole === 'owner';   // права администратора раздаёт только владелец

    const head = [];
    // Шестерёнка — только тем, кто действительно может менять группу
    if (canManage) head.push(button('', { kind: 'ghost', icon: 'settings', title: 'Настройки группы', onClick: () => { dlg.close(); groupSettings(chatId); } }));
    // Выход — в правом верхнем углу, маленькой кнопкой
    head.push(button('', { kind: 'ghost', icon: 'logout', title: 'Покинуть группу', cls: 'leave-btn', onClick: () => leaveGroup(chatId, info.name, dlg) }));

    let dlg;
    dlg = Kov.UI.modal({
      title: 'Группа: ' + (info.name || ''), glyph: 'users', wide: true,
      headButtons: head,
      body: el => {
        const list = h('div.checklist', { style: { maxHeight: '18rem' } });
        const search = Kov.UI.searchInput('Поиск участников…', v => draw(v));

        function draw(needle) {
          needle = (needle || '').trim().toLowerCase();
          Kov.UI.clear(list);
          (info.participants || [])
            .filter(p => !needle || personLabel(p).toLowerCase().includes(needle))
            .forEach(p => {
              const av = Kov.UI.avatar(p, null, p.avatar_url ? avatarSrc(p.avatar_url) : Kov.Store.photoUrl(p.id));
              av.classList.add('clickable');
              av.title = 'Карточка сотрудника';
              av.addEventListener('click', () => { dlg.close(); userCard(Number(p.id)); });
              // Имя и роль — в одной сжимаемой колонке, кнопки — в своей,
              // фиксированной: длина ФИО на их место не влияет
              const row = h('div.chat-person',
                av,
                h('div.chat-person-main',
                  h('span.chat-person-name', personLabel(p)),
                  h('span.chip' + (p.role === 'owner' ? '.info' : ''), roleLabel(p.role))),
                h('div.chat-person-actions'));
              const actions = row.querySelector('.chat-person-actions');
              row.append = (...nodes) => actions.append(...nodes);
              const other = String(p.id) !== String(Kov.Api.state.userId) && p.role !== 'owner';
              if (isOwner && other) {
                const admin = p.role === 'admin';
                row.append(button('', { kind: 'ghost', icon: admin ? 'user' : 'star', title: admin ? 'Снять права администратора' : 'Сделать администратором', onClick: async () => {
                  // Сервер ждёт и user_id, и role — без role запрос отклонялся с 422
                  const json = { user_id: Number(p.id), role: admin ? 'participant' : 'admin' };
                  try {
                    await Kov.Api.post('chat/group/' + chatId + '/' + (admin ? 'demote' : 'promote'), { json });
                    Kov.UI.toast(admin ? 'Права администратора сняты' : 'Назначен администратором', 'success');
                    dlg.close(); infoCard(chatId);
                  } catch (e) { Kov.UI.toast('Не удалось изменить роль: ' + Kov.UI.errorText(e), 'error'); }
                } }));
              }
              if (canManage && other) {
                row.append(button('', { kind: 'danger', icon: 'trash', title: 'Удалить из группы', onClick: async () => {
                  if (!await Kov.UI.confirm('Удалить участника', 'Удалить «' + personLabel(p) + '» из группы?', { okText: 'Удалить', danger: true })) return;
                  try { await Kov.Api.del('chat/group/' + chatId + '/participants/' + p.id); dlg.close(); infoCard(chatId); }
                  catch (e) { Kov.UI.toast('Не удалось: ' + Kov.UI.errorText(e), 'error'); }
                } }));
              }
              list.append(row);
            });
          if (!list.children.length) list.append(h('div.muted', { style: { padding: '.4rem' } }, 'Никого не найдено'));
        }

        const addBtn = canManage ? button('Добавить участников', { kind: 'ghost', icon: 'plus', onClick: () => {
          // уже состоящих в группе выбрать нельзя
          const taken = (info.participants || []).map(p => ({ id: Number(p.id), note: 'уже в группе' }));
          employeePicker('Добавить в группу', async ids => {
            try { await Kov.Api.post('chat/group/' + chatId + '/participants', { json: { user_ids: ids } }); dlg.close(); infoCard(chatId); }
            catch (e) { Kov.UI.toast('Не удалось добавить: ' + Kov.UI.errorText(e), 'error'); }
          }, true, { taken });
        } }) : null;

        const groupPhoto = info.image_path ? avatarSrc(info.image_path) : null;
        const av = h('span.avatar.xl' + (groupPhoto ? '.clickable' : ''),
          { style: { background: Kov.UI.avatarColor(info.name || 'группа') }, title: groupPhoto ? 'Открыть фото' : '' },
          groupPhoto ? h('img', { src: groupPhoto, alt: '' }) : icon('users'));
        if (groupPhoto) av.addEventListener('click', () => openImage(groupPhoto, 'Фото группы'));

        el.append(h('div.chat-card-head', av,
            h('div.chat-card-name', info.name || 'Группа'),
            h('div.chat-card-sub', 'Участников: ' + (info.participants || []).length)),
          mediaSection(chatId, () => dlg),
          h('div.btn-row', h('span.muted.small', { style: { flex: 1 } }, 'Участники'), addBtn),
          search.el, list);
        draw('');
      },
    });
    if (openSettings && canManage) { dlg.close(); groupSettings(chatId); }
    return dlg;
  }

  /** Выход из группы: сервер сам удалит её, если никого не останется */
  async function leaveGroup(chatId, name, dlg) {
    if (!await Kov.UI.confirm('Выход из группы',
      'Вы хотите выйти из группы «' + (name || '') + '»?',
      { okText: 'Покинуть', danger: true })) return;
    try {
      await Kov.Api.del('chat/group/' + chatId + '/leave');
      if (dlg) dlg.close();
      if (typeof clearChat === 'function') clearChat();
      backend.chat_closed(); loadChats();
      Kov.UI.toast('Вы покинули группу', 'success');
    } catch (e) { Kov.UI.toast('Не удалось выйти: ' + Kov.UI.errorText(e), 'error'); }
  }

  /**
   * Настройки группы — отдельное окно за шестерёнкой.
   * Роль перечитывается с сервера при каждом открытии: раньше карточка,
   * открытая до понижения, продолжала пускать в настройки.
   */
  async function groupSettings(chatId, info) {
    try { info = await Kov.Api.get('chat/info/' + chatId); }
    catch (e) { return Kov.UI.toast('Настройки недоступны: ' + Kov.UI.errorText(e), 'error'); }
    const myRole = String(info.my_role || '').toLowerCase();
    const canManage = ['owner', 'admin'].includes(myRole);
    if (!canManage) return Kov.UI.toast('Права администратора сняты — настройки недоступны', 'warning');
    const dlg = Kov.UI.modal({
      title: 'Настройки группы: ' + (info.name || ''), glyph: 'settings',
      body: el => {
        const rows = h('div', { style: { display: 'flex', flexDirection: 'column', gap: '.5rem' } });
        if (canManage) {
          rows.append(
            button('Переименовать группу', { kind: 'ghost', icon: 'edit', onClick: () => renameDialog(chatId, info.name, () => { dlg.close(); infoCard(chatId); }) }),
            button('Сменить картинку', { kind: 'ghost', icon: 'image', onClick: async () => {
              // Тот же кадровщик, что у фото профиля: группа тоже показывается кружком
              const file = await Kov.PhotoCrop.pick({ title: 'Картинка группы' });
              if (!file) return;
              try {
                const form = new FormData(); form.append('file', file, file.name); form.append('chat_id', String(chatId)); form.append('is_avatar', 'true');
                const r = await Kov.Api.post('chat/upload', { form });
                await Kov.Api.put('chat/group/' + chatId, { json: { image_path: r.url || r.file_path } });
                Kov.UI.toast('Картинка обновлена', 'success'); dlg.close(); loadChats(); backend.get_group_info(String(chatId));
                // карточка группы сразу с новой картинкой, без повторного открытия
                infoCard(chatId);
              } catch (e) { Kov.UI.toast('Не удалось: ' + Kov.UI.errorText(e), 'error'); }
            } }));
        }
        if (myRole === 'owner') {
          rows.append(button('Удалить группу', { kind: 'danger', icon: 'trash', onClick: async () => {
            if (!await Kov.UI.confirm('Удаление группы', 'Удалить группу со всей перепиской?', { okText: 'Удалить', danger: true })) return;
            try { await Kov.Api.del('chat/group/' + chatId); dlg.close(); if (typeof clearChat === 'function') clearChat(); backend.chat_closed(); loadChats(); }
            catch (e) { Kov.UI.toast('Не удалось: ' + Kov.UI.errorText(e), 'error'); }
          } }));
        } else {
          rows.append(button('Покинуть группу', { kind: 'danger', icon: 'logout',
            onClick: () => leaveGroup(chatId, info.name, dlg) }));
        }
        // Опасные действия отодвинуты вниз отдельным блоком: на телефоне
        // кнопка выхода стояла вплотную к значку группы и ловила промахи
        const danger = h('div.danger-zone', rows.querySelectorAll ? [] : []);
        [...rows.children].filter(b => b.classList.contains('danger')).forEach(b => danger.append(b));
        el.append(h('div.small.muted', 'Участники добавляются в карточке группы'), rows,
          danger.children.length ? danger : null);
      },
      buttons: [{ text: 'Назад к карточке', kind: 'ghost', icon: 'arrow_back', onClick: close => { close(); infoCard(chatId); } }],
    });
  }

  function renameDialog(chatId, current, done) {
    const name = h('input.input', { value: current || '' });
    Kov.UI.modal({
      title: 'Название группы', glyph: 'edit',
      body: el => el.append(field('Название', name, true)),
      buttons: [{ text: 'Отмена', kind: 'ghost' }, { text: 'Сохранить', icon: 'save', onClick: async close => {
        const v = name.value.trim(); if (!v) return Kov.UI.toast('Введите название', 'warning');
        try { await Kov.Api.put('chat/group/' + chatId, { json: { name: v } }); close(); backend.get_group_info(String(chatId)); loadChats(); done && done(); }
        catch (e) { Kov.UI.toast('Не удалось: ' + Kov.UI.errorText(e), 'error'); }
      } }],
    });
  }

  /**
   * Пересылка: список чатов с фотографиями, поиском и множественным выбором.
   * Личные и групповые вперемешку, как в самом списке чатов.
   */
  function forwardDialog(msgIds, preview) {
    const ids = Array.isArray(msgIds) ? msgIds : [msgIds];   // несколько — по очереди, в порядке написания
    const selected = new Set();
    const list = h('div.forward-list');
    const counter = h('div.small.muted');
    const search = Kov.UI.searchInput('Поиск по чатам и сотрудникам…', () => draw());

    function chatAvatar(c) {
      if (c.is_group) {
        return h('span.avatar', { style: { background: Kov.UI.avatarColor(c.name || 'группа') } },
          c.image_path ? h('img', { src: withToken(storagePath(c.image_path)), alt: '' }) : icon('users'));
      }
      const person = { id: c.other_user_id, last_name: c.last_name, first_name: c.first_name, name: c.name };
      return Kov.UI.avatar(person, null, c.avatar_url ? withToken(storagePath(c.avatar_url)) : Kov.Store.photoUrl(c.other_user_id));
    }

    function draw() {
      const needle = (search.input.value || '').trim().toLowerCase();
      Kov.UI.clear(list);
      const rows = (chats || []).filter(c => !needle || String(c.name || '').toLowerCase().includes(needle));
      rows.forEach(c => {
        const on = selected.has(c.id);
        const row = h('button.forward-row' + (on ? '.on' : ''), { type: 'button' },
          chatAvatar(c),
          h('div.who', h('b', c.name || 'Чат'),
            h('span', c.is_group ? 'Группа' : 'Личный чат')),
          h('span.mark', on ? icon('check') : null));
        row.addEventListener('click', () => {
          if (selected.has(c.id)) selected.delete(c.id); else selected.add(c.id);
          draw();
        });
        list.append(row);
      });
      if (!rows.length) list.append(h('div.empty', 'Чат не найден'));
      counter.textContent = selected.size ? 'Выбрано чатов: ' + selected.size : 'Можно выбрать несколько';
    }

    draw();
    Kov.UI.modal({
      title: 'Переслать сообщение', glyph: 'arrow_right', wide: true,
      body: el => el.append(
        preview ? h('div.forward-preview', String(preview).slice(0, 200)) : null,
        h('div.toolbar', search.el), counter, list),
      buttons: [{ text: 'Отмена', kind: 'ghost' }, { text: 'Переслать', icon: 'check', onClick: async close => {
        if (!selected.size) return Kov.UI.toast('Выберите, куда переслать', 'warning');
        try {
          for (const it of ids) {   // целиком — номер; часть группы — номер и выбранные файлы
            const id = typeof it === 'object' ? it.id : it, files = typeof it === 'object' && it.files ? it.files : null;
            await Kov.Api.post('chat/message/' + id + '/forward', { json: files ? { chat_ids: [...selected], file_ids: files } : { chat_ids: [...selected] } });
          }
          Kov.UI.toast(ids.length > 1 ? 'Переслано сообщений: ' + ids.length : selected.size > 1 ? 'Переслано в ' + selected.size + ' чата(ов)' : 'Сообщение переслано', 'success');
          close();
        } catch (e) { Kov.UI.toast('Не удалось переслать: ' + Kov.UI.errorText(e), 'error'); }
      } }],
    });
  }

  // точка входа для автопроверок: выбор личного собеседника
  function __test_privatePicker() { employeePicker('Выберите сотрудника', () => {}, false, { taken: privateChatPartners() }); }

  // выбор сотрудника пригодился и складу — отдаём его наружу
  return { view, onEvent, loadChats, backend, employeePicker, __test_privatePicker, __test_aggregate: aggregate,
    cancelUpload() { const up = activeUploads.get(String(activeChatId)); if (up && up.ctrl) up.ctrl.abort(); },
  };
})();
