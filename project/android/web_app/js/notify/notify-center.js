/* Центр уведомлений веб-версии. Единственная точка входа для событий:
 *   Kov.NotifyCenter.handleEvent(событие_WebSocket)
 *   Kov.NotifyCenter.handleEvent({type:'push', payload})   — из service worker
 *
 * Правила (одинаковые для HTTP и HTTPS; отличается только доступность
 * системных уведомлений — см. notify-mode.js):
 *
 *  событие                     раздел «Уведомления»  всплывашка  системное (вкладка скрыта)  звук
 *  message / grouped_message          нет               да*            да, если режим ≠ inpage    да
 *  member_add (меня добавили)         нет               да             да                         да
 *  removed_from_chat/group_deleted    нет               да             нет                        нет
 *  notification.new                   ДА                да             да                         да
 *  task.* / stage.* / project.*       нет               нет            нет                        нет
 *  chats_updated                      нет (только счётчик мессенджера)
 *
 *  * — если этот чат сейчас открыт и вкладка активна, ничего не показываем:
 *      сообщение уже видно в самом чате.
 *
 * Сообщения чата намеренно НЕ попадают в раздел «Уведомления» — это требование
 * заказчика; там только уведомления сервера (notification.new).
 */
window.Kov = window.Kov || {};

Kov.NotifyCenter = (function () {
  const UI = () => Kov.NotifyUI;
  const Mode = () => Kov.NotifyMode;

  const DEFAULT_SETTINGS = {
    toasts: true,       // всплывашки внутри страницы
    chatToasts: true,   // ...в том числе для чата
    sound: true,        // звук
    os: true,           // системные уведомления при скрытой вкладке (HTTPS)
    push: true,         // подписка на push при закрытом браузере (HTTPS)
  };
  const DEDUP_WINDOW = 5000;
  const PREVIEW_LIMIT = 120;

  const state = {
    userId: null,
    settings: Object.assign({}, DEFAULT_SETTINGS),
    activeChatId: null,        // чат, открытый в мессенджере (null — мессенджер не открыт)
    chatsById: {},             // кэш из chats_updated: id → {name, is_group, unread_count}
    unreadChats: 0,
    unreadNotifications: 0,
    recent: new Map(),         // ключ дедупликации → время
    resolveChat: null,         // async (chatId) → {name, is_group} | null
    navigate: null,
    inited: false,
  };
  const listeners = {};

  function on(type, fn) { (listeners[type] = listeners[type] || []).push(fn); return () => off(type, fn); }
  function off(type, fn) { listeners[type] = (listeners[type] || []).filter(f => f !== fn); }
  function emit(type, data) { (listeners[type] || []).forEach(fn => { try { fn(data); } catch (e) { console.error(e); } }); }

  // ── настройка ────────────────────────────────────────────────────────────

  /**
   * init({ userId, settings, navigate(url), resolveChat(chatId) })
   */
  function init(opts) {
    opts = opts || {};
    state.userId = Number(opts.userId);
    if (opts.settings) Object.assign(state.settings, opts.settings);
    state.navigate = opts.navigate || (url => { location.hash = url.replace(/^#?/, '#'); });
    state.resolveChat = opts.resolveChat || null;
    UI().setNavigate(state.navigate);
    if (!state.inited) {
      state.inited = true;
      // Сообщения от service worker: push пришёл в активную вкладку / клик по уведомлению
      if (navigator.serviceWorker) {
        navigator.serviceWorker.addEventListener('message', e => {
          const msg = e.data || {};
          if (msg.type === 'push') handleEvent({ type: 'push', payload: msg.payload || {} });
          if (msg.type === 'navigate' && msg.url) state.navigate(msg.url);
        });
      }
    }
  }

  function setSettings(patch) { Object.assign(state.settings, patch || {}); emit('settings', getSettings()); }
  function getSettings() { return Object.assign({}, state.settings); }
  function setActiveChat(chatId) { state.activeChatId = chatId == null ? null : Number(chatId); }
  function setUnreadNotifications(n) { state.unreadNotifications = Math.max(0, n | 0); applyBadge(); }
  function setUnreadChats(n) { state.unreadChats = Math.max(0, n | 0); applyBadge(); }
  function markNotificationsRead() { setUnreadNotifications(0); }
  function getCounters() { return { notifications: state.unreadNotifications, chats: state.unreadChats, total: state.unreadNotifications + state.unreadChats }; }

  function applyBadge() {
    UI().setBadge(state.unreadNotifications + state.unreadChats);
    emit('counters', getCounters());
  }

  // ── контекст и решение ───────────────────────────────────────────────────

  function context() {
    let permission = 'unsupported';
    if (Mode().notificationsApi) permission = Notification.permission;
    return {
      userId: state.userId,
      visible: document.visibilityState === 'visible' && (typeof document.hasFocus !== 'function' || document.hasFocus()),
      activeChatId: state.activeChatId,
      mode: Mode().level,
      permission,
      settings: state.settings,
    };
  }

  function preview(text, filesCount) {
    text = String(text || '').trim();
    // служебные пометки стикера и GIF человеку показывать незачем
    if (/^\[\[sticker:[a-z]+\/[a-z]+\]\]$/.test(text)) return 'Стикер';
    if (/^\[\[gif:https:\/\/[^\s\]]+\]\]$/.test(text)) return 'GIF';
    if (text) return text.length > PREVIEW_LIMIT ? text.slice(0, PREVIEW_LIMIT - 1) + '…' : text;
    if (filesCount > 1) return 'Файлы: ' + filesCount;
    if (filesCount === 1) return 'Файл';
    return 'Новое сообщение';
  }

  function chatInfo(chatId) { return state.chatsById[chatId] || null; }

  /**
   * Чистая функция: событие + контекст → план действий или null.
   * Вынесена отдельно, чтобы правила можно было проверить без браузера.
   */
  function decide(evt, ctx) {
    if (!evt || !evt.type) return null;
    const s = ctx.settings;
    const osAllowed = !ctx.visible && s.os && ctx.mode !== 'inpage' && ctx.permission === 'granted';

    switch (evt.type) {
      case 'message':
      case 'grouped_message': {
        if (Number(evt.sender_id) === ctx.userId) return null;
        const chatId = Number(evt.chat_id);
        // Чат открыт — уведомления по нему не нужны совсем, даже когда окно
        // свёрнуто: сообщение всё равно ляжет в открытую переписку
        if (ctx.activeChatId === chatId) return null;
        const info = chatInfo(chatId);
        const isGroup = info ? !!info.is_group : false;
        const files = evt.type === 'grouped_message' ? (evt.file_count || (evt.files || []).length) : (evt.file_path ? 1 : 0);
        const text = preview(evt.text, files);
        const sender = evt.sender_name || 'Сотрудник';
        // Кто написал — всегда в заголовке, а групповой чат подписывается
        // отдельной строкой: иначе непонятно, личное это сообщение или в группу
        return {
          kind: 'chat', evt, chatId,
          needsChatName: !info,          // название группы дотянем, если списка ещё нет
          isGroup,
          title: sender,
          where: isGroup ? 'в группе «' + (info.name || 'без названия') + '»' : '',
          body: text,
          initials: UI().initials(evt.sender_last_name, evt.sender_first_name) ,
          avatarUrl: evt.sender_avatar_url ? Kov.Api.fileUrl(evt.sender_avatar_url) : null,
          url: '#/messenger/' + chatId,
          tag: 'chat-' + chatId,
          dedupKey: evt.type + ':' + (evt.id || evt.group_id || evt.parent_id || text),
          inApp: false,
          toast: s.toasts && s.chatToasts,
          os: osAllowed,
          sound: s.sound,
        };
      }

      case 'special_message': {
        const meta = evt.metadata || {};
        if (evt.special !== 'member_add') return null;
        if (Number(meta.user_id) !== ctx.userId || Number(evt.sender_id) === ctx.userId) return null;
        const chatId = Number(evt.chat_id);
        return {
          kind: 'group', evt, chatId, needsChatName: true,
          title: 'Вас добавили в группу',
          body: (meta.actor_name ? meta.actor_name + ' добавил(а) вас' : ''),
          url: '#/messenger/' + chatId,
          tag: 'chat-' + chatId,
          dedupKey: 'member_add:' + evt.id,
          inApp: false, toast: s.toasts, os: osAllowed, sound: s.sound,
        };
      }

      case 'removed_from_chat':
      case 'group_deleted':
        return {
          kind: 'warning', evt,
          title: 'Мессенджер',
          body: evt.message || (evt.type === 'removed_from_chat' ? 'Вас удалили из группы' : 'Группа удалена'),
          url: '#/messenger',
          tag: 'chat-' + evt.chat_id,
          dedupKey: evt.type + ':' + evt.chat_id,
          inApp: false, toast: s.toasts, os: false, sound: false,
        };

      case 'notification':
      case 'notifications.new':
      case 'notification.created':
      case 'notification.new': {
        // то же место, что и в списке уведомлений: задача, проект, склад, оборудование
        const views = window.Kov && Kov.Views && Kov.Views.notifications;
        const url = (views && views.targetHash && views.targetHash(evt)) || '#/notifications';
        return {
          kind: 'notification', evt,
          title: 'Уведомление',
          body: preview(evt.message, 0),
          url,
          tag: 'notification-' + (evt.notification_id || 'x'),
          dedupKey: 'notification:' + (evt.notification_id || evt.message),
          inApp: true, toast: s.toasts, os: osAllowed, sound: s.sound,
        };
      }

      case 'push': {
        // Пришло от service worker: браузер получил push, но вкладка активна —
        // показываем как всплывашку. Если то же событие уже пришло по WebSocket,
        // дедупликация по метке и тексту его отсечёт.
        const p = evt.payload || {};
        const kind = p.kind === 'chat' ? 'chat' : (p.kind === 'group_added' ? 'group' : 'notification');
        return {
          kind, evt,
          title: p.title || 'Kovapp', body: p.body || '',
          url: p.url || '#/notifications', tag: p.tag || 'push',
          dedupKey: 'push:' + (p.tag || '') + ':' + (p.body || ''),
          inApp: false, toast: s.toasts && (kind !== 'chat' || s.chatToasts), os: false, sound: s.sound,
        };
      }

      default:
        return null;
    }
  }

  // ── выполнение ───────────────────────────────────────────────────────────

  function isDuplicate(plan) {
    const now = Date.now();
    for (const [k, t] of state.recent) if (now - t > DEDUP_WINDOW) state.recent.delete(k);
    const keys = [plan.dedupKey, 'tb:' + plan.tag + '|' + plan.body];
    if (keys.some(k => state.recent.has(k))) return true;
    keys.forEach(k => state.recent.set(k, now));
    return false;
  }

  async function fillChatName(plan) {
    if (!plan.needsChatName) return;
    let info = chatInfo(plan.chatId);
    if (!info && state.resolveChat) {
      try {
        info = await Promise.race([state.resolveChat(plan.chatId), new Promise(r => setTimeout(() => r(null), 1500))]);
      } catch (e) { info = null; }
    }
    if (!info) return;
    if (plan.kind === 'chat') {
      // список чатов подгрузился позже события — уточняем подпись
      if (info.is_group) plan.where = 'в группе «' + (info.name || 'без названия') + '»';
      return;
    }
    if (info.name) plan.body = '«' + info.name + '»' + (plan.body ? ' — ' + plan.body : '');
  }

  async function activeRegistration() {
    const reg = Kov.PushClient && Kov.PushClient.registration;
    if (!reg || !reg.showNotification) return null;
    if (reg.active) return reg;
    try {
      return await Promise.race([navigator.serviceWorker.ready, new Promise(r => setTimeout(() => r(null), 1500))]);
    } catch (e) { return null; }
  }

  async function showOs(plan) {
    const options = {
      body: (plan.where ? plan.where + ' · ' : '') + plan.body, tag: plan.tag, data: { url: plan.url }, renotify: false,
      icon: 'icons/kovapp-192.png', badge: 'icons/kovapp-badge-96.png', lang: 'ru',
    };
    const reg = await activeRegistration();
    if (reg) {
      try { await reg.showNotification(plan.title, options); return true; } catch (e) { /* пробуем без service worker */ }
    }
    try {
      const n = new Notification(plan.title, options);
      n.onclick = () => { window.focus(); state.navigate(plan.url); n.close(); };
      return true;
    } catch (e) { return false; }
  }

  // ── приглашение разрешить системные уведомления ────────────────────────
  const OFFER_KEY = 'kov.notify.offer';
  function offerPermission() {
    if (!Mode().notificationsApi || !state.settings.os) return false;
    // в браузере отказ окончательный; приложение может спросить снова или открыть настройки телефона
    const again = Notification.permission === 'denied' && !!Mode().canAskAgain;
    if (Notification.permission !== 'default' && !again) return false;
    try {
      const last = Number(localStorage.getItem(OFFER_KEY) || 0);
      if (Date.now() - last < 7 * 86400000) return false;
    } catch (e) { /* хранилище недоступно — предлагаем */ }
    if (document.querySelector('.kov-perm-offer')) return true;
    const bar = document.createElement('div');
    bar.className = 'kov-perm-offer';
    bar.setAttribute('role', 'dialog');
    bar.setAttribute('aria-labelledby', 'kovPermOfferTitle');
    // карточка в стиле приложения: значок, заголовок, пояснение, кнопки .btn
    const badge = document.createElement('div');
    badge.className = 'kov-perm-offer__icon';
    try { if (Kov.UI && Kov.UI.icon) badge.append(Kov.UI.icon('bell')); } catch (e) { /* без значка */ }
    const body = document.createElement('div');
    body.className = 'kov-perm-offer__body';
    const title = document.createElement('b');
    title.id = 'kovPermOfferTitle';
    title.className = 'kov-perm-offer__title';
    title.textContent = 'Уведомления';
    const text = document.createElement('span');
    text.textContent = 'Разрешите системные уведомления — тогда сообщения будут видны, даже когда вкладка свёрнута.';
    if (again) text.textContent = 'Уведомления сейчас выключены — включите их, чтобы сообщения были видны, даже когда приложение свёрнуто.';
    body.append(title, text);
    const actions = document.createElement('div');
    actions.className = 'kov-perm-offer__actions';
    const allow = document.createElement('button');
    allow.type = 'button'; allow.className = 'btn kov-perm-offer__allow'; allow.textContent = again ? 'Включить' : 'Разрешить';
    const later = document.createElement('button');
    later.type = 'button'; later.className = 'btn ghost kov-perm-offer__later'; later.textContent = 'Позже';
    actions.append(later, allow);
    const close = () => { bar.remove(); try { localStorage.setItem(OFFER_KEY, String(Date.now())); } catch (e) { /* */ } };
    allow.addEventListener('click', async () => {
      const result = await requestPermission();
      close();
      // «Включить» в приложении могло открыть настройки телефона — там человек решит сам
      if (result === 'granted' || !again) {
        UI().show({ title: result === 'granted' ? 'Системные уведомления включены' : 'Уведомления не разрешены — включить можно в настройках',
          kind: result === 'granted' ? 'success' : 'warning', ttl: 3200 });
      }
      if (result === 'granted' && Kov.PushClient) Kov.PushClient.enable().catch(() => {});
    });
    later.addEventListener('click', close);
    bar.append(badge, body, actions);
    document.body.append(bar);
    return true;
  }

  async function execute(plan) {
    if (plan.inApp) {
      state.unreadNotifications += 1;
      applyBadge();
      emit('notification', plan.evt);
    }
    if (isDuplicate(plan)) return;
    await fillChatName(plan);
    if (plan.toast) {
      UI().show({
        title: plan.title, body: plan.body, where: plan.where, kind: plan.kind, url: plan.url, tag: plan.tag,
        initials: plan.initials, avatarUrl: plan.avatarUrl,
      });
    }
    if (plan.os) showOs(plan);
    if (plan.sound) UI().playChime(plan.kind);
    emit('shown', plan);
  }

  function handleEvent(evt) {
    if (!evt) return;
    // Служебные события со счётчиками — вне таблицы решений
    if (evt.type === 'chats_updated' && Array.isArray(evt.chats)) {
      let unread = 0;
      evt.chats.forEach(c => {
        state.chatsById[c.id] = { name: c.name, is_group: !!c.is_group, unread_count: c.unread_count | 0 };
        if (c.id !== state.activeChatId || document.visibilityState !== 'visible') unread += c.unread_count | 0;
      });
      setUnreadChats(unread);
      emit('chats', evt.chats);
      return;
    }
    const plan = decide(evt, context());
    if (plan) execute(plan);
  }

  // ── разрешение на системные уведомления ──────────────────────────────────

  /** Вызывать из обработчика клика (браузер требует действие пользователя). */
  async function requestPermission() {
    if (!Mode().notificationsApi) return 'unsupported';
    if (Notification.permission === 'granted') return 'granted';
    try {
      const result = await Notification.requestPermission();
      emit('permission', result);
      return result;
    } catch (e) { return Notification.permission; }
  }

  return {
    init, on, off, handleEvent, decide, context,
    setSettings, getSettings, setActiveChat, __test_decide: decide,
    setUnreadNotifications, setUnreadChats, markNotificationsRead, getCounters,
    requestPermission, offerPermission, showOs,
    DEFAULT_SETTINGS,
  };
})();
