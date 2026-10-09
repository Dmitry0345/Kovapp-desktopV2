// Глобальные переменные
/** Экранирование для подстановки в атрибуты HTML: имена файлов задаёт пользователь */
function escapeAttr(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

let backend = null;
let currentUserId = null;
let activeChatId = null;
let chatMessages = [];
let loadedMessageIds = new Set();
let isLoadingHistory = false;
let hasOlderMessages = true;
let hasNewerMessages = true;
let activeChatIsGroup = false;
// Фамилия/имя собеседника в открытом личном чате — нужны для цвета аватарки
// в шапке (тот же ключ, что и в списке чатов).
let activeChatLastName = '';
let activeChatFirstName = '';
// Исходный путь к картинке открытого чата и последний список чатов:
// нужны, чтобы перерисовать их, когда станет известен адрес сервера
let activeChatAvatarPath = null;
let lastChatsPayload = null;
let knownChatIds = new Set();

// Временное сообщение-заглушка на время загрузки файлов.
// ВАЖНО: объявляется здесь, в самом верху, а не в середине файла.
// Иначе любая ошибка выше по скрипту оставляет переменную в TDZ и
// renderMessages() падает с "Cannot access 'tempUploadData' before initialization".
let tempUploadData = null;
// До какого номера сообщения чат прочитан другими участниками: по нему
// на своих сообщениях рисуется вторая галочка
let readUpto = 0;

// Элементы DOM
const chatListContainer = document.getElementById('chatListContainer');
const chatArea = document.getElementById('chatArea');
const placeholder = document.getElementById('placeholder');
const headerTitle = document.getElementById('headerTitle');
const headerSub = document.getElementById('headerSub');
const headerAvatar = document.getElementById('headerAvatar');
const btnSearchChat = document.getElementById('btnSearchChat');
const btnSettings = document.getElementById('btnSettings');
const messagesList = document.getElementById('messagesList');
const messageListContainer = document.getElementById('messageListContainer');
const scrollToBottomBtn = document.getElementById('scrollToBottomBtn');
const messageInput = document.getElementById('messageInput');
const btnSend = document.getElementById('btnSend');
const btnAttach = document.getElementById('btnAttach');
const charCounter = document.getElementById('charCounter');
const infoPanel = document.getElementById('infoPanel');
const replyContainer = document.getElementById('replyContainer');
const replyInfo = document.getElementById('replyInfo');
const btnCloseReply = document.getElementById('btnCloseReply');
const attachmentsContainer = document.getElementById('attachmentsContainer');
const searchSidebar = document.getElementById('searchSidebar');
const btnCloseSearchSidebar = document.getElementById('btnCloseSearchSidebar');
const messageSearchInput = document.getElementById('messageSearchInput');
const railBtn = document.getElementById('railBtn');
const searchResultsList = document.getElementById('searchResultsList');
const chatSearchInput = document.getElementById('chatSearchInput');
const uploadProgressContainer = document.getElementById('uploadProgressContainer');
const uploadProgressBar = document.getElementById('uploadProgressBar');
const uploadProgressText = document.getElementById('uploadProgressText');

let replyToMsgId = null;
let kbdMenuAt = 0;   // когда меню сообщения открыли с клавиатуры

/* ── панель эмодзи, стикеров и GIF ───────────────────────────────────────
 * Стикер и GIF уходят обычным сообщением с короткой пометкой в тексте:
 * так они попадают в историю, поиск и уведомления без правок на сервере,
 * а в ленте показываются отдельным объектом, без пузыря.
 */
var STICKER_MARK = /^\[\[sticker:([a-z]+)\/([a-z]+)\]\]$/;
/** Папка со стикерами: тот же приём, что в панели — считаем от адреса чата */
function stickerBase() {
    if (window.Kov && Kov.MediaPanel && Kov.MediaPanel.stickerBase) return Kov.MediaPanel.stickerBase();
    return 'stickers/';
}
var GIF_MARK = /^\[\[gif:(https:\/\/[^\s\]]+)\]\]$/;

/** Как показать сообщение в списке чатов и уведомлениях */
function previewText(text) {
    if (stickerOf(text)) return 'Стикер';
    if (gifOf(text)) return 'GIF';
    return text;
}

function stickerOf(text) {
    const m = STICKER_MARK.exec(String(text || '').trim());
    return m ? { set: m[1], mood: m[2], src: stickerBase() + m[1] + '/' + m[2] + '.svg' } : null;
}
function gifOf(text) {
    const m = GIF_MARK.exec(String(text || '').trim());
    return m ? { src: m[1] } : null;
}
/** Стикер или GIF: текст у них служебный, как обычное сообщение их не трогаем */
function isMarkedMessage(msg) {
    const text = msg && msg.text;
    return !!(stickerOf(text) || gifOf(text));
}

/* Панель эмодзи как клавиатура.
 *
 * На телефоне панель раньше открывалась сбоку и лезла за край экрана. Здесь
 * она выезжает снизу на место клавиатуры — так устроены все мессенджеры, и
 * человеку не приходится гадать, куда делось поле ввода.
 *
 * Высоту берём настоящую, клавиатурную. Задать размер системной клавиатуры
 * нельзя, но можно его измерить: visualViewport показывает видимую часть
 * страницы, и разница с высотой окна и есть высота клавиатуры. Замер
 * запоминается, поэтому панель открывается ровно той же высоты — экран не
 * дёргается при переключении между клавиатурой и эмодзи.
 */
const KEYBOARD_KEY = 'kov.chat.kbd';
const KEYBOARD_MIN = 220;      // ниже этого — не клавиатура, а адресная строка
const KEYBOARD_MAX = 460;

function isPhone() { return window.innerWidth <= 760; }

function savedKeyboardHeight() {
    let saved = 0;
    try { saved = Number(localStorage.getItem(KEYBOARD_KEY)) || 0; } catch (e) { /* приватный режим */ }
    if (saved >= KEYBOARD_MIN && saved <= KEYBOARD_MAX) return saved;
    // Пока не измеряли — берём долю экрана: столько обычно занимает клавиатура
    return Math.round(Math.min(KEYBOARD_MAX, Math.max(KEYBOARD_MIN, window.innerHeight * 0.42)));
}

function watchKeyboard() {
    const vv = window.visualViewport;
    if (!vv) return;
    const onResize = () => {
        const gap = Math.round(window.innerHeight - vv.height - vv.offsetTop);
        if (gap >= KEYBOARD_MIN && gap <= KEYBOARD_MAX) {
            try { localStorage.setItem(KEYBOARD_KEY, String(gap)); } catch (e) { /* приватный режим */ }
        }
        // Пока клавиатура открыта, поле ввода не должно уезжать под неё
        if (document.documentElement) {
            document.documentElement.style.setProperty('--kbd-h', (gap > 60 ? gap : 0) + 'px');
        }
    };
    vv.addEventListener('resize', onResize);
    vv.addEventListener('scroll', onResize);
    onResize();
}
watchKeyboard();

function applyPanelHeight() {
    const root = document.getElementById('kovChat');
    if (!root) return;
    root.style.setProperty('--media-h', savedKeyboardHeight() + 'px');
}

var mediaPanel = null;
function setupMediaPanel() {
    const sidebar = document.getElementById('mediaSidebar');
    const btn = document.getElementById('btnEmoji');
    if (!sidebar || !btn || !window.Kov || !Kov.MediaPanel) return;
    mediaPanel = Kov.MediaPanel.mount({
        sidebar,
        tabs: document.getElementById('mediaTabs'),
        body: document.getElementById('mediaBody'),
        onEmoji: ch => {
            const input = document.getElementById('messageInput');
            if (!input) return;
            const at = input.selectionStart ?? input.value.length;
            input.value = input.value.slice(0, at) + ch + input.value.slice(input.selectionEnd ?? at);
            input.selectionStart = input.selectionEnd = at + ch.length;
            // на сенсорном экране не поднимаем клавиатуру: она закрыла бы панель эмодзи
            if (!(window.matchMedia && window.matchMedia('(hover: none), (pointer: coarse)').matches)) input.focus();
            input.dispatchEvent(new Event('input', { bubbles: true }));
        },
        onSticker: id => { sendMarked('[[sticker:' + id + ']]'); },
        onGif: item => { if (item && item.full) sendMarked('[[gif:' + item.full + ']]'); },
    });
    const root = document.getElementById('kovChat');
    const markOpen = on => {
        if (root) root.classList.toggle('media-open', !!on);
        btn.classList.toggle('on', !!on);
        // Пока панель открыта, кнопка возвращает клавиатуру — и подпись,
        // и значок должны говорить именно об этом
        btn.title = (on && isPhone()) ? 'Клавиатура' : 'Эмодзи, стикеры и GIF';
    };
    btn.addEventListener('click', () => {
        const willOpen = !mediaPanel.isOpen();
        if (willOpen && isPhone()) {
            // Панель встаёт на место клавиатуры: сначала измеряем её высоту,
            // потом убираем фокус, чтобы клавиатура ушла без скачка
            applyPanelHeight();
            const input = document.getElementById('messageInput');
            if (input) input.blur();
        }
        mediaPanel.toggle();
        markOpen(mediaPanel.isOpen());
    });
    const close = document.getElementById('btnCloseMediaSidebar');
    if (close) close.addEventListener('click', () => { mediaPanel.close(); markOpen(false); });
    // Тронули поле ввода — панель уступает место клавиатуре
    const input = document.getElementById('messageInput');
    if (input) {
        input.addEventListener('focus', () => {
            if (isPhone() && mediaPanel.isOpen()) { mediaPanel.close(); markOpen(false); }
        });
    }
    window.addEventListener('resize', () => { if (mediaPanel.isOpen() && isPhone()) applyPanelHeight(); });
}

/** Отправка стикера или GIF: тем же путём, что обычное сообщение */
function sendMarked(text) {
    if (!activeChatId) return;
    if (backend && backend.send_text_message) backend.send_text_message(text, replyToMsgId || null);
    if (typeof clearInfoPanel === 'function') clearInfoPanel();
    if (mediaPanel && isPhone()) {
        mediaPanel.close();
        const root = document.getElementById('kovChat');
        if (root) root.classList.remove('media-open');
        const btn = document.getElementById('btnEmoji');
        if (btn) btn.classList.remove('on');
    }
}

let editingMsgId = null;
let pendingFiles = [];

// === Цвета аватарок ===
// ВАЖНО: это точная копия алгоритма из chat_module/user_colors.py.
// Ключ строится из ФАМИЛИИ и ИМЕНИ, хэш — FNV-1a (32 бита), поэтому один и
// тот же сотрудник получает один и тот же цвет и в Qt-диалогах, и здесь.
// Любое изменение нужно повторить в user_colors.py, иначе цвета разъедутся.

const AVATAR_COLOR_PAIRS = [
    ["#EF5350", "#E53935"], ["#EC407A", "#D81B60"], ["#AB47BC", "#8E24AA"],
    ["#7E57C2", "#5E35B1"], ["#5C6BC0", "#3949AB"], ["#42A5F5", "#1E88E5"],
    ["#29B6F6", "#039BE5"], ["#26C6DA", "#00ACC1"], ["#26A69A", "#00897B"],
    ["#66BB6A", "#43A047"], ["#9CCC65", "#7CB342"], ["#D4E157", "#C0CA33"],
    ["#FFEE58", "#FDD835"], ["#FFCA28", "#FFB300"], ["#FFA726", "#FB8C00"]
];

const FALLBACK_COLOR_PAIR = ["#B0BEC5", "#CFD8DC"];

const _colorCache = new Map();
const _COLOR_CACHE_LIMIT = 4096;

function normalizeUserKey(lastName, firstName, fullName) {
    const parts = [];
    if (lastName) parts.push(String(lastName));
    if (firstName) parts.push(String(firstName));
    if (parts.length === 0 && fullName) parts.push(String(fullName));

    let key = parts.join(" ");
    key = key.split(/\s+/).filter(Boolean).join(" ");
    key = key.trim().toLowerCase();
    key = key.replace(/\u0451/g, "\u0435");   // ё -> е
    return key;
}

// FNV-1a, 32 бита. Math.imul нужен, чтобы умножение осталось 32-битным,
// как в Python-версии с маской 0xFFFFFFFF.
function _fnv1a32(text) {
    let h = 2166136261;
    for (let i = 0; i < text.length; i++) {
        h = (h ^ (text.codePointAt(i) & 0xFFFFFFFF)) >>> 0;
        h = Math.imul(h, 16777619) >>> 0;
    }
    return h >>> 0;
}

function userColorPair(lastName, firstName, fullName) {
    const key = normalizeUserKey(lastName, firstName, fullName);
    if (!key) return FALLBACK_COLOR_PAIR;

    const cached = _colorCache.get(key);
    if (cached) return cached;

    const pair = AVATAR_COLOR_PAIRS[_fnv1a32(key) % AVATAR_COLOR_PAIRS.length];

    if (_colorCache.size >= _COLOR_CACHE_LIMIT) _colorCache.clear();
    _colorCache.set(key, pair);
    return pair;
}

function userInitials(lastName, firstName, fullName) {
    if (lastName || firstName) {
        let initials = "";
        if (lastName) initials += String(lastName).trim().charAt(0);
        if (firstName) initials += String(firstName).trim().charAt(0);
        return initials.toUpperCase() || "?";
    }

    const parts = String(fullName || "").trim().split(/\s+/).filter(Boolean);
    if (parts.length === 0) return "?";
    let initials = parts[0].charAt(0);
    if (parts.length > 1) initials += parts[1].charAt(0);
    return initials.toUpperCase();
}

/**
 * Красит элемент-аватарку градиентом по фамилии и имени.
 * Вызывается для всех мест, где нет фотографии: список чатов, шапка чата,
 * аватарки рядом с сообщениями.
 */
function applyAvatarColor(el, lastName, firstName, fullName) {
    if (!el) return;
    const pair = userColorPair(lastName, firstName, fullName);
    el.style.background = `linear-gradient(135deg, ${pair[0]} 0%, ${pair[1]} 100%)`;
    el.style.color = "#FFFFFF";
}

/** Убирает заливку, когда вместо инициалов показывается фотография. */
function clearAvatarColor(el) {
    if (!el) return;
    el.style.background = "transparent";
}

// === Адреса картинок ===
// Страница загружается из локального файла, поэтому относительный путь
// вроде /file_storage/... браузер превратил бы в file:///file_storage/...
// Адрес сервера приходит из Python и подставляется ко всем ссылкам.

let apiHost = '';
let apiToken = '';

// Токен для медиа: <img> и <video> грузятся самим браузером, заголовок
// Authorization к ним не приложить, поэтому он идёт в query-параметре.
// Сервер принимает оба варианта (см. get_current_user_id_from_header_or_query).
function setApiToken(token) {
    apiToken = String(token || '');
}

function withAuth(url) {
    if (!url || !apiToken) return url;
    if (!/^https?:\/\//i.test(url)) return url;
    if (/[?&]token=/.test(url)) return url;
    return url + (url.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(apiToken);
}

function setApiHost(host) {
    const next = String(host || '').replace(/\/+$/, '');
    if (next === apiHost) return;

    apiHost = next;

    // Списки могли отрисоваться до того, как пришёл адрес сервера:
    // тогда пути к картинкам были относительными и не загрузились
    if (lastChatsPayload) renderChatList(lastChatsPayload);
    if (activeChatId !== null) {
        renderHeaderAvatar(headerTitle.innerText, activeChatIsGroup, activeChatAvatarPath,
                           activeChatLastName, activeChatFirstName);
    }
    if (chatMessages.length) renderMessages(chatMessages);
}

function absoluteUrl(path) {
    if (!path) return null;
    if (/^https?:\/\//i.test(path)) return withAuth(path);

    let clean = String(path).replace(/\\/g, '/');
    if (!clean.startsWith('/')) clean = '/' + clean;
    return apiHost ? withAuth(apiHost + clean) : clean;
}

/** Адрес файла из хранилища сервера (аватарки групп, вложения). */
function storageUrl(path) {
    if (!path) return null;
    if (/^https?:\/\//i.test(path)) return path;

    let clean = String(path).replace(/\\/g, '/');
    if (!clean.startsWith('/')) clean = '/' + clean;
    if (!clean.startsWith('/file_storage')) clean = '/file_storage' + clean;
    return absoluteUrl(clean);
}

/** Картинка чата: у групп это файл в хранилище, у людей — фото из базы. */
function avatarUrlFor(isGroup, path) {
    return isGroup ? storageUrl(path) : absoluteUrl(path);
}

function chatAvatarPath(chat) {
    if (!chat) return null;
    return chat.is_group ? chat.image_path : chat.avatar_url;
}

/**
 * Единая отрисовка круглой аватарки.
 * Фотография ставится, если она есть; цветная заглушка с инициалами —
 * только когда фотографии нет или она не загрузилась.
 */
function renderAvatar(el, options) {
    if (!el) return;

    const opts = options || {};
    const name = opts.name || '';
    const lastName = opts.isGroup ? '' : (opts.lastName || '');
    const firstName = opts.isGroup ? '' : (opts.firstName || '');
    const initials = opts.isGroup
        ? (name || '?').trim().charAt(0).toUpperCase()
        : userInitials(lastName, firstName, name);

    const drawPlaceholder = () => {
        el.innerHTML = '';
        applyAvatarColor(el, lastName, firstName, name);
        const span = document.createElement('span');
        span.className = 'avatar-initials';
        span.innerText = initials;
        el.appendChild(span);
    };

    drawPlaceholder();

    if (!opts.url) return;

    const img = document.createElement('img');
    img.alt = initials;
    img.onload = () => {
        el.innerHTML = '';
        clearAvatarColor(el);
        el.appendChild(img);
    };
    img.onerror = () => { drawPlaceholder(); };
    img.src = opts.url;
}

// Инициализация QWebChannel
new QWebChannel(qt.webChannelTransport, function(channel) {
    backend = channel.objects.backend;

    // Получаем начальные данные
    backend.get_current_user_id(function(id) {
        currentUserId = String(id);
    });

    backend.get_api_host(function(host) {
        setApiHost(host);
    });

    // Связываем сигналы от Python с JS функциями.
    // Питоновский мост может не объявлять часть сигналов (он вызывает эти же
    // функции напрямую через runJavaScript), поэтому связываем только то,
    // что реально существует — иначе одна отсутствующая сигнатура обрывала
    // весь колбэк инициализации.
    const bind = (signalName, handler) => {
        const sig = backend[signalName];
        if (sig && typeof sig.connect === 'function') {
            try {
                sig.connect(handler);
            } catch (e) {
                console.warn(`Не удалось связать сигнал ${signalName}:`, e);
            }
        }
    };

    // ── панель эмодзи, стикеров и GIF ──
    setupMediaPanel();

    bind('readState', payload => {
        // {chat_id, upto} — до какого сообщения чат прочитан другими
        let data; try { data = JSON.parse(payload); } catch (e) { return; }
        if (!data || String(data.chat_id) !== String(activeChatId)) return;
        applyReadState(data.upto);
    });
    bind('chatsUpdated', renderChatList);
    bind('historyLoaded', onHistoryLoaded);
    bind('messageReceived', onMessageReceived);
    bind('messageUpdated', onMessageUpdated);
    bind('messageDeleted', onMessageDeleted);
    bind('reactionsUpdated', onReactionsUpdated);
    bind('groupInfoLoaded', onGroupInfoLoaded);
    bind('chatCardLoaded', onChatCardLoaded);
    bind('searchResultsReceived', renderSearchResults);
    bind('uploadProgress', updateUploadProgress);
    bind('scrollRequested', scrollToMessage);
});

// === Функции для списка чатов ===

function renderChatList(chatsStr) {
    const chats = JSON.parse(chatsStr);
    lastChatsPayload = chatsStr;

    const incomingIds = new Set(chats.map(c => String(c.id)));
    const activeKey = activeChatId === null ? null : String(activeChatId);
    const lostActiveChat = activeKey !== null
        && knownChatIds.has(activeKey)
        && !incomingIds.has(activeKey);
    knownChatIds = incomingIds;
    // фокус клавиатуры был в списке — после перерисовки вернуть его на тот же чат
    const focusedId = document.activeElement && document.activeElement.closest
        && document.activeElement.closest('#chatListContainer .chat-item')
        ? document.activeElement.dataset.chatId : null;
    chatListContainer.innerHTML = '';

    chats.forEach(chat => {
        const item = document.createElement('div');
        item.className = `chat-item ${String(chat.id) === String(activeChatId) ? 'active' : ''}`;
        item.dataset.chatId = String(chat.id);
        item.setAttribute('role', 'button');
        item.tabIndex = -1;
        if (String(chat.id) === String(activeChatId)) item.setAttribute('aria-current', 'true');
        item.onclick = () => openChat(chat.id, chat.name, chat.is_group, chatAvatarPath(chat), chat.role,
                                      chat.last_name, chat.first_name);
        
        // Аватар: фотография сотрудника или картинка группы, а цветная
        // заглушка с инициалами — только если картинки нет
        const avatarDiv = document.createElement('div');
        avatarDiv.className = 'chat-item-avatar';

        renderAvatar(avatarDiv, {
            url: avatarUrlFor(chat.is_group, chatAvatarPath(chat)),
            name: chat.name,
            lastName: chat.last_name,
            firstName: chat.first_name,
            isGroup: chat.is_group
        });

        const infoDiv = document.createElement('div');
        infoDiv.className = 'chat-item-info';
        
        // Имя чата и текст последнего сообщения задаёт пользователь: раньше они
        // подставлялись в innerHTML как есть, и сообщение вида <img onerror=…>
        // выполнялось у всех участников при отрисовке списка
        const topDiv = document.createElement('div');
        topDiv.className = 'chat-item-top';
        const nameSpan = document.createElement('span');
        nameSpan.className = 'chat-item-name';
        nameSpan.textContent = chat.name || '';
        const timeSpan = document.createElement('span');
        timeSpan.className = 'chat-item-time';
        timeSpan.textContent = chat.last_message_time || '';
        topDiv.append(nameSpan, timeSpan);

        const bottomDiv = document.createElement('div');
        bottomDiv.className = 'chat-item-bottom';
        // В группе без имени автора не понять, от кого пришло сообщение
        const lastSpan = document.createElement('span');
        lastSpan.className = 'chat-item-last-msg';
        if (chat.last_message_sender && chat.last_message) {
            const who = document.createElement('span');
            who.className = 'chat-item-sender';
            who.textContent = chat.last_message_sender + ': ';
            lastSpan.append(who);
        }
        // В списке чатов служебная пометка стикера и GIF заменяется словом
        lastSpan.append(document.createTextNode(previewText(chat.last_message || '')));
        bottomDiv.append(lastSpan);
        
        if (chat.unread_count && chat.unread_count > 0 && String(chat.id) !== String(activeChatId)) {
            const badge = document.createElement('span');
            badge.className = 'unread-badge';
            badge.innerText = chat.unread_count;
            bottomDiv.appendChild(badge);
            // В свёрнутом виде подписи не видно — счётчик уходит на аватар
            item.classList.add('has-unread');
            const dot = document.createElement('span');
            dot.className = 'rail-dot';
            dot.textContent = chat.unread_count > 99 ? '99+' : String(chat.unread_count);
            item.appendChild(dot);
        }

        infoDiv.appendChild(topDiv);
        infoDiv.appendChild(bottomDiv);
        
        // В свёрнутом виде имени не видно — оставляем подсказку при наведении
        item.title = chat.name || '';
        item.appendChild(avatarDiv);
        item.appendChild(infoDiv);
        
        chatListContainer.appendChild(item);
    });

    // в список — одна остановка Tab (открытый чат или первый), дальше стрелками
    const stop = chatListContainer.querySelector('.chat-item.active') || chatListContainer.querySelector('.chat-item');
    if (stop) stop.tabIndex = 0;
    if (focusedId) {
        const back = chatListContainer.querySelector(`.chat-item[data-chat-id="${CSS.escape(focusedId)}"]`);
        if (back) { chatListContainer.querySelectorAll('.chat-item').forEach(x => { x.tabIndex = -1; }); back.tabIndex = 0; back.focus({ preventScroll: true }); }
    }

    if (lostActiveChat) closeActiveChat();
}

// Список чатов с клавиатуры: Enter или пробел открывает, стрелки, Home и End — по списку
chatListContainer.addEventListener('keydown', (e) => {
    const item = e.target.closest && e.target.closest('.chat-item');
    if (!item) return;
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); item.click(); return; }
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return;
    const all = [...chatListContainer.querySelectorAll('.chat-item')].filter(x => x.style.display !== 'none');
    let i = all.indexOf(item);
    i = e.key === 'Home' ? 0 : e.key === 'End' ? all.length - 1 : i + (e.key === 'ArrowDown' ? 1 : -1);
    const next = all[Math.max(0, Math.min(all.length - 1, i))];
    if (!next) return;
    e.preventDefault();
    chatListContainer.querySelectorAll('.chat-item').forEach(x => { x.tabIndex = -1; });
    next.tabIndex = 0;
    next.focus();
});

function closeActiveChat() {
    clearChat();
    if (backend && typeof backend.chat_closed === 'function') {
        backend.chat_closed();
    }
}

chatSearchInput.addEventListener('input', (e) => {
    const text = e.target.value.toLowerCase();
    const items = chatListContainer.querySelectorAll('.chat-item');
    items.forEach(item => {
        const name = item.querySelector('.chat-item-name').innerText.toLowerCase();
        item.style.display = name.includes(text) ? 'flex' : 'none';
        item.tabIndex = -1;
    });
    const stop = [...items].find(x => x.style.display !== 'none' && x.classList.contains('active'))
        || [...items].find(x => x.style.display !== 'none');
    if (stop) stop.tabIndex = 0;
});

document.getElementById('addChatBtn').onclick = () => {
    if (backend) backend.show_add_menu();
};

// === Функции открытия чата ===

function openChat(id, name, isGroup, avatarPath, role, lastName, firstName) {
    if (String(activeChatId) === String(id)) return;

    activeChatId = id;
    activeChatAvatarPath = avatarPath || null;
    activeChatLastName = lastName || '';
    activeChatFirstName = firstName || '';
    if (backend) {
        backend.mark_chat_as_read(String(id));
    }
    activeChatIsGroup = isGroup;
    chatMessages = [];
    loadedMessageIds.clear();
    messagesList.innerHTML = '';
    hasOlderMessages = true;
    hasNewerMessages = true;
    
    messageInput.value = '';
    messageInput.style.height = 'auto';
    charCounter.style.display = 'none';
    pendingFiles = [];
    renderPendingFiles();
    clearInfoPanel();
    
    placeholder.style.display = 'none';
    chatArea.style.display = 'flex';
    
    headerTitle.innerText = name;
    setHeaderSub('');
    // личный чат — под названием должность собеседника; у группы — число участников (из get_group_info)
    if (backend && !isGroup && typeof backend.get_chat_card === 'function') backend.get_chat_card(String(id));
    
    renderHeaderAvatar(name, isGroup, activeChatAvatarPath, activeChatLastName, activeChatFirstName);
    
    if (btnSearchChat) btnSearchChat.style.display = 'flex';
    if (typeof btnSettings !== 'undefined' && btnSettings) btnSettings.style.display = 'none';
    
    clearInfoPanel();
    
    // Выделение в списке — сразу, не дожидаясь обновления списка с сервера
    const items = chatListContainer.querySelectorAll('.chat-item');
    items.forEach(item => { item.classList.remove('active'); item.removeAttribute('aria-current'); });
    const current = chatListContainer.querySelector(`.chat-item[data-chat-id="${CSS.escape(String(id))}"]`);
    if (current) { current.classList.add('active'); current.setAttribute('aria-current', 'true'); }
    
    if (backend) backend.load_history(String(id), 30, 0);
    if (backend && isGroup) backend.get_group_info(String(id));
}

/**
 * Рисует аватарку в шапке чата: фотография, если она есть, иначе инициалы
 * на градиенте, посчитанном по тому же ключу "Фамилия Имя".
 */
function renderHeaderAvatar(name, isGroup, avatarPath, lastName, firstName) {
    renderAvatar(headerAvatar, {
        url: avatarUrlFor(isGroup, avatarPath),
        name: name,
        lastName: lastName,
        firstName: firstName,
        isGroup: isGroup
    });
}

function onGroupInfoLoaded(infoStr) {
    const info = JSON.parse(infoStr);
    if (String(info.chat_id) === String(activeChatId)) {
        headerTitle.innerText = info.name;
        activeChatAvatarPath = info.image_path || null;
        renderHeaderAvatar(info.name, true, activeChatAvatarPath, '', '');
        const n = Number(info.participant_count) || (info.participants || []).length;
        setHeaderSub(n ? participantsLabel(n) : '');
    }
}

/** Подпись под названием чата: число участников группы или должность собеседника. */
function setHeaderSub(text) {
    if (!headerSub) return;
    headerSub.textContent = text || '';
    headerSub.hidden = !text;
}

function participantsLabel(n) {
    const m10 = n % 10, m100 = n % 100;
    const word = (m10 === 1 && m100 !== 11) ? 'участник'
        : (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) ? 'участника' : 'участников';
    return n + ' ' + word;
}

/** Карточка личного чата пришла — под названием должность собеседника. */
function onChatCardLoaded(infoStr) {
    let info;
    try { info = JSON.parse(infoStr); } catch (e) { return; }
    if (!info || info.is_group || activeChatIsGroup || String(info.chat_id) !== String(activeChatId)) return;
    setHeaderSub(info.position || '');
}

// Элемента #btnSettings в chat.html нет: настройки группы открываются из
// карточки чата. Без проверки обращение к null обрывало бы весь скрипт.
if (btnSettings) {
    btnSettings.onclick = () => {
        if (backend && activeChatId) backend.open_group_settings(String(activeChatId));
    };
}

// === Отрисовка сообщений ===

function formatDateSeparator(timestamp) {
    const date = new Date(timestamp);
    const today = new Date();
    const yesterday = new Date(today);
    yesterday.setDate(yesterday.getDate() - 1);
    
    if (date.toDateString() === today.toDateString()) return "Сегодня";
    if (date.toDateString() === yesterday.toDateString()) return "Вчера";
    
    return date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

function formatTime(timestamp) {
    const date = new Date(timestamp);
    return date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
}

/**
 * Дата + время для результатов поиска.
 *
 * Формат всегда полный — ДД.ММ.ГГГГ, ЧЧ:ММ, чтобы выдача была однородной
 * и год читался у каждого результата.
 */
function formatDateTime(timestamp) {
    if (!timestamp) return '';

    const date = new Date(timestamp);
    if (isNaN(date.getTime())) return '';

    const time = date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
    const day = date.toLocaleDateString('ru-RU', {
        day: '2-digit',
        month: '2-digit',
        year: 'numeric'
    });

    return `${day}, ${time}`;
}

/**
 * Значок состояния своего сообщения: часы — уходит, одна галочка — сервер
 * принял, две — прочитано. В группе достаточно, чтобы прочитал любой.
 */
function messageStatus(msg) {
    if (msg.pending) return 'sending';
    const id = Number(msg.id);
    if (!isFinite(id)) return 'sending';
    return readUpto && id <= readUpto ? 'read' : 'sent';
}

function statusElement(msg) {
    const state = messageStatus(msg);
    const box = document.createElement('span');
    box.className = 'msg-status ' + state;
    box.title = state === 'sending' ? 'Отправляется' : state === 'sent' ? 'Доставлено' : 'Прочитано';
    box.innerHTML = state === 'sending'
        ? '<i class="clock"></i>'
        : '<i class="tick"></i>' + (state === 'read' ? '<i class="tick second"></i>' : '');
    return box;
}

/** Пересчитать галочки после того, как собеседник прочитал чат */
function applyReadState(upto) {
    readUpto = Math.max(readUpto || 0, Number(upto) || 0);
    document.querySelectorAll('#kovChat .msg-wrapper.msg-mine').forEach(w => {
        const id = Number((w.id || '').replace('msg-', ''));
        if (!isFinite(id) || id > readUpto) return;
        const box = w.querySelector('.msg-status');
        if (box && !box.classList.contains('read')) {
            box.className = 'msg-status read';
            box.title = 'Прочитано';
            box.innerHTML = '<i class="tick"></i><i class="tick second"></i>';
        }
    });
}

/**
 * Убирает фон-заглушку у миниатюр после загрузки. Раньше это делал обработчик
 * прямо в атрибуте onload, но такие вставки запрещает политика содержимого —
 * а она нужна, чтобы чужой скрипт не выполнился, даже если куда-то попадёт.
 */
function clearThumbBackgrounds(root) {
    (root || document).querySelectorAll('img[data-clear-bg]').forEach(img => {
        if (img.dataset.clearBound) return;
        img.dataset.clearBound = '1';
        const done = () => { img.style.backgroundImage = 'none'; };
        if (img.complete && img.naturalWidth) done();
        else { img.addEventListener('load', done, { once: true }); img.addEventListener('error', done, { once: true }); }
    });
}


/** Ключ группы вложений; для одиночного сообщения — null */
function groupKeyOf(msg) {
    const g = msg && msg.group_id;
    if (g === null || g === undefined) return null;
    const s = String(g);
    return (s === '' || s === '0' || s === 'null' || s === 'undefined' || s === 'None') ? null : s;
}

/** У сообщения есть настоящий номер с сервера */
function hasRealId(msg) {
    if (!msg || msg.id === null || msg.id === undefined) return false;
    const s = String(msg.id);
    return s !== '' && s !== 'undefined' && s !== 'null' && s !== 'NaN';
}

/** Собственный файл сообщения в виде записи для files[] */
function fileOfMessage(msg) {
    if (!msg || (!msg.file_path && !msg.url)) return null;
    let fileUrl = msg.url;
    if (!fileUrl && msg.file_path) fileUrl = `/file_storage/${String(msg.file_path).replace(/\\/g, '/')}`;
    const name = msg.file_name || msg.filename || 'Файл';
    return { id: msg.id, url: fileUrl, filename: name, file_name: name, file_path: msg.file_path,
             size: msg.file_size, is_image: msg.is_image };
}

/** Добавляет файлы к сообщению, не создавая дублей (по адресу и по номеру) */
function addFilesTo(target, files) {
    if (!target || !files || !files.length) return;
    if (!Array.isArray(target.files)) target.files = [];
    files.forEach(f => {
        if (!f) return;
        const same = target.files.some(x => (x.url && f.url && x.url === f.url)
            || (x.id !== undefined && f.id !== undefined && x.id !== null && f.id !== null && String(x.id) === String(f.id)));
        if (!same) target.files.push(f);
    });
}

/**
 * Сводит сообщения в один список.
 *
 * Главное правило: всё, помеченное одним group_id, — это ОДНО сообщение с
 * несколькими файлами, каким бы путём его части ни пришли (историей, сокетом,
 * дозагрузкой после отправки). Раньше копия из сокета и копия из истории
 * считались разными сообщениями, и вложения показывались в чате дважды.
 */
function mergeMessages(current, incoming) {
    const messageMap = new Map();

    // 1. Раскладываем по номеру сообщения; сообщение без номера получает
    // временный ключ по группе — иначе две такие отправки слиплись бы в одну
    // Пришло настоящее сообщение с меткой отправителя — временное убираем
    const arrivedTokens = new Set(incoming.filter(m => m && m.client_id && hasRealId(m)).map(m => m.client_id));
    const alive = [...current, ...incoming].filter(msg => {
        if (!msg || !msg.pending) return true;
        if (arrivedTokens.has(msg.client_id)) return false;
        // страховка: если ответ так и не пришёл, пузырь не висит вечно
        if (!msg.__born) msg.__born = Date.now();
        return Date.now() - msg.__born < 60000;
    });
    alive.forEach(msg => {
        if (!msg) return;
        const key = msg.pending ? String(msg.id)
            : (hasRealId(msg) ? String(msg.id) : ('tmp:' + (groupKeyOf(msg) || Math.random().toString(36))));
        msg.__key = key;
        if (!messageMap.has(key)) { messageMap.set(key, msg); return; }
        const existing = messageMap.get(key);
        if (!existing.text && msg.text) existing.text = msg.text;
        addFilesTo(existing, msg.files);          // файлы объединяем, а не заменяем
        if (msg.reply_to || msg.reply_to_id) existing.reply_to_id = msg.reply_to || msg.reply_to_id;
        if (msg.parent_id) existing.parent_id = msg.parent_id;
        if (msg.reply_to_message) existing.reply_to_message = msg.reply_to_message;
        if (msg.reactions) existing.reactions = msg.reactions;
        if (msg.reactions_detailed) existing.reactions_detailed = msg.reactions_detailed;
        if (msg.special && !existing.special) existing.special = msg.special;
        if (msg.is_edited) existing.is_edited = true;
    });

    // 2. Одна группа — одно сообщение
    const groups = new Map();
    messageMap.forEach(msg => {
        const key = groupKeyOf(msg);
        if (!key) return;
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(msg);
    });
    groups.forEach(list => {
        const ids = new Set(list.map(m => String(m.id)));
        // родителем берём запись с настоящим номером: она переживёт перерисовку
        const parent = list.find(m => hasRealId(m) && m.special === 'file_group')
            || list.find(m => hasRealId(m) && m.special !== 'file_item')
            || list.find(hasRealId)
            || list[0];
        addFilesTo(parent, [fileOfMessage(parent)].filter(Boolean));
        list.forEach(m => {
            if (m === parent) return;
            addFilesTo(parent, m.files);
            addFilesTo(parent, [fileOfMessage(m)].filter(Boolean));
            if (!parent.text && m.text) parent.text = m.text;
            if (!parent.reply_to_message && m.reply_to_message) parent.reply_to_message = m.reply_to_message;
            if (!parent.timestamp && m.timestamp) parent.timestamp = m.timestamp;
            messageMap.delete(m.__key !== undefined ? m.__key : String(m.id));
        });
        // связь «файл → родитель» внутри своей же группы — это не ответ
        if (parent.parent_id && ids.has(String(parent.parent_id))) delete parent.parent_id;
    });

    // 3. Файловые элементы без группы — к своему родителю
    [...messageMap.values()].forEach(msg => {
        if (msg.special !== 'file_item') return;
        const parentId = msg.parent_id ? String(msg.parent_id) : null;
        if (!parentId || parentId === String(msg.id) || !messageMap.has(parentId)) return;
        const parent = messageMap.get(parentId);
        addFilesTo(parent, msg.files);
        addFilesTo(parent, [fileOfMessage(msg)].filter(Boolean));
        messageMap.delete(msg.__key !== undefined ? msg.__key : String(msg.id));
    });

    return Array.from(messageMap.values()).sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
}

function onHistoryLoaded(messagesStr, chatId, newer) {
    if (!newer && String(chatId) !== String(activeChatId)) readUpto = 0;
    if (String(chatId) !== String(activeChatId)) return;
    
    const newMessages = JSON.parse(messagesStr);
    isLoadingHistory = false;
    
    const wasAtBottom = messagesList.scrollHeight - messagesList.scrollTop <= messagesList.clientHeight + 50;
    
    if (newMessages.length === 0) {
        if (chatMessages.length > 0) {
            if (newer) hasNewerMessages = false;
            else hasOlderMessages = false;
        }
        return;
    }
    
    // Если это начальная загрузка (нету старых сообщений)
    if (chatMessages.length === 0) {
        chatMessages = mergeMessages([], newMessages);
        following = true;
        setUnseenBelow(0);
        renderMessages(chatMessages);
        // Держимся низа, пока догружаются миниатюры последних сообщений
        stickBottom(4000);
    } else {
        const oldLength = chatMessages.length;
        if (newer) {
            chatMessages = mergeMessages(chatMessages, newMessages);
            if (chatMessages.length === oldLength) {
                hasNewerMessages = false;
            }
            renderMessages(chatMessages);
            if (wasAtBottom) stickBottom(2000);
        } else {
            const oldScrollHeight = messagesList.scrollHeight;
            const oldScrollTop = messagesList.scrollTop;
            chatMessages = mergeMessages(chatMessages, newMessages);
            if (chatMessages.length === oldLength) {
                hasOlderMessages = false;
            }
            renderMessages(chatMessages);
            messagesList.scrollTop = (messagesList.scrollHeight - oldScrollHeight) + oldScrollTop;
        }
    }
}

function onHistoryAroundLoaded(messagesStr, targetMsgId, groupId) {
    const newMessages = JSON.parse(messagesStr);
    if (newMessages.length === 0) return;

    hasOlderMessages = true;
    hasNewerMessages = true;

    chatMessages = mergeMessages([], newMessages);
    renderMessages(chatMessages);

    // groupId обязателен для вложений: файл внутри группы отдельного
    // элемента не имеет, искать надо обёртку всей группы
    setTimeout(() => {
        scrollToMessage(targetMsgId, groupId || '');
    }, 100);
}

function onMessageReceived(msgStr) {
    const msg = JSON.parse(msgStr);
    // Сравниваем через String: с сервера chat_id приходит числом,
    // а activeChatId в разных местах мог оказаться строкой.
    if (String(msg.chat_id) !== String(activeChatId)) return;

    // Помечаем сообщение как новое для анимации
    msg.is_new = true;
    
    const wasAtBottom = messagesList.scrollHeight - messagesList.scrollTop <= messagesList.clientHeight + 50;
    // новое сообщение добавляется снизу: читающему историю держим прежний scrollTop,
    // а не расстояние до низа — иначе лента сдвигалась на высоту каждого нового сообщения
    const topBefore = messagesList.scrollTop;
    // то же сообщение может прийти ещё раз (дозагрузка файлов группы, повтор доставки):
    // на кнопке «вниз» считаем только то, чего в ленте ещё не было
    const group = groupKeyOf(msg);
    const isNew = !chatMessages.some(m => String(m.id) === String(msg.id)
        || (group && groupKeyOf(m) === group)
        || (msg.special === 'file_item' && msg.parent_id && String(m.id) === String(msg.parent_id)));

    chatMessages = mergeMessages(chatMessages, [msg]);

    // временный пузырь загрузки убирает только пришедшее сообщение с файлами:
    // текст, написанный во время загрузки, просто встаёт над ним
    const withFiles = !!(msg.file_path || (msg.files && msg.files.length) || msg.special === 'file_group');
    if (String(msg.sender_id) === currentUserId && withFiles) {
        if (typeof removeTempUploadMessage === 'function') {
            removeTempUploadMessage();
        }
    }

    renderMessages(chatMessages);
    
    // Снимаем флаг 'is_new' после рендера, чтобы при перерисовке оно уже не анимировалось
    setTimeout(() => {
        const fresh = chatMessages.find(m => m.id === msg.id);
        if (fresh) fresh.is_new = false;
    }, 400);

    // Своё сообщение — показываем. Чужое: если человек у самого низа, он там и остаётся;
    // если читает историю — не сдвигаем, а считаем новые на кнопке «вниз»
    if (String(msg.sender_id) === currentUserId) {
        setUnseenBelow(0);
        stickBottom(2500);
    } else if (following) {
        stickBottom(800);
    } else {
        messagesList.scrollTop = topBefore;
        if (isNew) setUnseenBelow(unseenBelow + 1);
    }

    // Чат открыт — значит сообщение сразу прочитано, счётчик непрочитанных не копим
    if (backend && String(msg.sender_id) !== currentUserId) {
        backend.mark_chat_as_read(String(activeChatId));
    }
}

function onMessageUpdated(msgStr) {
    const msgData = JSON.parse(msgStr);
    const index = chatMessages.findIndex(m => String(m.id) === String(msgData.message_id));
    if (index !== -1) {
        chatMessages[index].text = msgData.new_text;
        chatMessages[index].is_edited = true;
        renderMessages(chatMessages);
    }
}

function onMessageDeleted(msgStr) {
    const msgData = JSON.parse(msgStr);
    const gone = String(msgData.message_id);
    chatMessages = chatMessages.filter(m => String(m.id) !== gone);
    chatMessages = chatMessages.filter(m => {   // удалён файл группы — только он
        if (!Array.isArray(m.files) || !m.files.some(f => fileIdOf(f) === gone)) return true;
        m.files = m.files.filter(f => fileIdOf(f) !== gone);
        return m.files.length > 0 || !!String(m.text || '').trim();
    });
    renderMessages(chatMessages);
}

function onReactionsUpdated(msgStr) {
    const msgData = JSON.parse(msgStr);
    const index = chatMessages.findIndex(m => String(m.id) === String(msgData.message_id));
    if (index !== -1) {
        chatMessages[index].reactions = msgData.reactions;
        chatMessages[index].reactions_detailed = msgData.reactions_detailed || [];
        // Строка реакций меняет высоту сообщения, и обычная перерисовка
        // уводила ленту вверх — как при постановке реакции, так и при снятии.
        // Тот же приём, что при замене временного пузыря на настоящий.
        renderKeepingPlace();
    }
}

// обложка загружаемого видео: кадр из самого файла (серверной обложки ещё нет)
const localPosters = new Map();
function localPoster(url) {
    if (!url) return '';
    const known = localPosters.get(url);
    if (known) return known === 'pending' || known === 'none' ? '' : known;
    localPosters.set(url, 'pending');
    const v = document.createElement('video');
    v.muted = true; v.playsInline = true; v.preload = 'auto';
    const done = data => {
        localPosters.set(url, data || 'none');
        v.removeAttribute('src'); try { v.load(); } catch (e) { /* */ }
        if (data) document.querySelectorAll('video[data-local-poster]').forEach(el => {
            if (el.getAttribute('src') === url) el.setAttribute('poster', data);
        });
    };
    v.addEventListener('loadeddata', () => {
        try { v.currentTime = Math.min(1, (v.duration || 0) / 3); } catch (e) { done(''); }
    }, { once: true });
    v.addEventListener('seeked', () => {
        try {
            const k = Math.min(1, 480 / Math.max(v.videoWidth, v.videoHeight, 1));
            const c = document.createElement('canvas');
            c.width = Math.max(1, Math.round(v.videoWidth * k)); c.height = Math.max(1, Math.round(v.videoHeight * k));
            c.getContext('2d').drawImage(v, 0, 0, c.width, c.height);
            done(c.toDataURL('image/jpeg', 0.8));
        } catch (e) { done(''); }
    }, { once: true });
    v.addEventListener('error', () => done(''), { once: true });
    setTimeout(() => { if (localPosters.get(url) === 'pending') done(''); }, 10000);
    v.src = url;
    return '';
}

function showTempUploadMessage(text, files) {
    tempUploadData = { text, files, percent: 0 };
    renderMessages(chatMessages);
    stickBottom(1500);
}

function cancelTempUpload() {
    if (window.Kov && Kov.Views && Kov.Views.messenger && Kov.Views.messenger.cancelUpload) Kov.Views.messenger.cancelUpload();
}

function removeTempUploadMessage() {
    // Временный пузырь и настоящее сообщение разной высоты: без сохранения
    // места лента заметно дёргалась вверх в момент замены
    const wasBottom = atBottom(120);
    tempUploadData = null;
    renderKeepingPlace(wasBottom);
    return;
}

// Пофайловый прогресс: сервер отдаёт состояние каждого файла, клиент
// подсвечивает его на плитке временного сообщения
function applyTempDetail() {
    if (!tempUploadData || !tempUploadData.detail) return;
    // Этапы пользователю не показываем: он видит один общий процент, а
    // подпись появляется только по итогу файла
    const NOTES = { done: 'Готово', error: 'Ошибка' };
    (tempUploadData.detail.files || []).forEach(f => {
        const tile = document.querySelector('.msg-temp .temp-tile[data-index="' + f.index + '"]');
        if (!tile) return;
        // кольцо не откатывается: один проход по окружности от начала до конца отправки
        const pct = Math.max(tile._kovPct || 0, Math.max(0, Math.min(100, Number(f.percent || 0))));
        tile._kovPct = pct;
        const fill = tile.querySelector('.temp-ring-fill');
        const label = tile.querySelector('.temp-pct');
        const note = tile.querySelector('.temp-note');
        if (fill) fill.style.setProperty('--p', pct + '%');
        if (label) label.textContent = pct + '%';
        if (note) note.textContent = NOTES[f.state] || '';
        tile.classList.toggle('done', f.state === 'done');
        tile.classList.toggle('error', f.state === 'error');
    });
}

function setTempDetail(detail) {
    if (!tempUploadData) return;
    tempUploadData.detail = detail;
    applyTempDetail();
}

// === Сервисные сообщения-маркеры ===

const SERVICE_SPECIAL_TYPES = [
    'avatar_change', 'member_add', 'member_remove', 'member_leave',
    'owner_transfer', 'group_rename'
];

function isServiceMessage(msg) {
    if (!msg || !msg.special) return false;
    if (msg.special === 'file_group' || msg.special === 'file_item') return false;
    return true;
}

// Формулировки маркеров, которые собирает сервер. Ответ поиска поля special
// не содержит, поэтому маркер там опознаётся только по тексту.
const SERVICE_TEXT_PATTERNS = [
    /^.+ переименовал(а)? группу в «.*»$/,
    /^.+ сменил(а)? аватар группы$/,
    /^.+ был(а)? добавлен(а)? в группу$/,
    /^.+ удалил(а)? из группы .+$/,
    /^.+ вышел из группы$/,
    /^.+ вышла из группы$/,
    /^.+ стал(а)? владельцем группы$/
];

function isServiceMessageText(text) {
    const value = (text || '').trim();
    if (!value) return false;
    return SERVICE_TEXT_PATTERNS.some(re => re.test(value));
}

// Текст маркера приходит с сервера. Фолбэк нужен на случай старых записей в БД,
// созданных до появления нормальных формулировок.
function buildServiceMessageText(msg) {
    if (msg.text) return msg.text;

    const meta = msg.metadata || msg.metadata_json || {};
    const actor = msg.sender_name || meta.actor_name || '';
    const target = meta.user_name || meta.target_name || '';

    switch (msg.special) {
        case 'avatar_change':
            return `${actor} сменил аватар группы`.trim();
        case 'member_add':
            return `${target || actor} был добавлен в группу`.trim();
        case 'member_remove':
            return `${actor} удалил из группы ${target}`.trim();
        case 'member_leave':
            return `${actor} вышел из группы`.trim();
        case 'owner_transfer':
            return `${target || 'Участник'} стал владельцем группы`.trim();
        case 'group_rename':
            return `${actor} переименовал группу в «${meta.new_name || ''}»`.trim();
        default:
            return '';
    }
}

// === Пересланные сообщения ===

/**
 * Достаёт данные об авторе оригинала.
 * Сервер кладёт их в metadata_json['forwarded_from'], а до клиента они
 * доезжают то как msg.metadata, то как msg.metadata_json — зависит от того,
 * пришло сообщение из истории или по WebSocket.
 */
function getForwardedFrom(msg) {
    if (!msg) return null;
    const meta = msg.metadata || msg.metadata_json || {};
    const origin = meta.forwarded_from;
    if (!origin) return null;
    return origin;
}

/** "Фамилия Имя" автора оригинала. */
function forwardedAuthorName(origin) {
    if (!origin) return 'Неизвестный';

    const parts = [];
    if (origin.last_name) parts.push(String(origin.last_name).trim());
    if (origin.first_name) parts.push(String(origin.first_name).trim());

    const composed = parts.join(' ').trim();
    if (composed) return composed;

    return String(origin.sender_name || '').trim() || 'Неизвестный';
}


/**
 * Раскладка группового файлового сообщения.
 *
 * Плитки бьются на ряды по три и по два так, чтобы ни в одном ряду не
 * осталась одна сиротливая картинка: 5 → 3+2, 7 → 3+2+2, 8 → 3+3+2.
 * Три файла показываются «героем»: одна крупная слева и две справа —
 * ряд из трёх узких полосок для трёх фотографий выглядит бедно.
 * Ряд задаёт высоту через aspect-ratio, поэтому плитки в нём всегда
 * одинаковые, сколько бы их ни было.
 */
function albumRows(n) {
    if (n <= 1) return [n];
    if (n === 2) return [2];
    if (n === 4) return [2, 2];
    const rows = [];
    let left = n;
    while (left > 0) {
        if (left === 4) { rows.push(2, 2); left = 0; }
        else if (left === 2) { rows.push(2); left = 0; }
        else if (left === 3) { rows.push(3); left = 0; }
        else { rows.push(3); left -= 3; }
    }
    return rows;
}

/** Расставляет готовые плитки по рядам внутри контейнера альбома */
function layoutAlbum(container, tiles) {
    container.classList.add('media-album');
    if (tiles.length === 1) { container.appendChild(tiles[0]); return; }
    if (tiles.length === 3) {
        const hero = document.createElement('div');
        hero.className = 'media-hero';
        tiles.forEach(t => hero.appendChild(t));
        container.appendChild(hero);
        return;
    }
    let i = 0;
    albumRows(tiles.length).forEach(count => {
        const row = document.createElement('div');
        row.className = 'media-row';
        row.dataset.count = String(count);
        for (let k = 0; k < count && i < tiles.length; k++, i++) row.appendChild(tiles[i]);
        container.appendChild(row);
    });
}

/**
 * Отпечаток сообщения: по нему решается, надо ли пересобирать его узел.
 *
 * В отпечаток входит всё, что видно на экране. Совпал — узел остаётся как
 * есть, и лента не моргает при новом сообщении или реакции. Разошёлся —
 * пересобирается одно это сообщение, остальные не трогаются.
 */
/** Время, пометка «изменено» и состояние доставки — отдельным блоком */
function buildMeta(msg, isMine) {
    const metaDiv = document.createElement('div');
    metaDiv.className = 'msg-meta';
    if (msg.is_edited) {
        const editedSpan = document.createElement('span');
        editedSpan.className = 'msg-edited';
        editedSpan.innerText = 'изменено';
        metaDiv.appendChild(editedSpan);
    }
    const timeSpan = document.createElement('span');
    timeSpan.innerText = formatTime(msg.timestamp);
    metaDiv.appendChild(timeSpan);
    // Своё сообщение подписывается состоянием: часы, галочка, две галочки
    if (isMine && !isServiceMessage(msg)) metaDiv.appendChild(statusElement(msg));
    return metaDiv;
}

/**
 * Обновление мелочей на месте: реакции, время, состояние доставки.
 *
 * Раньше любое такое изменение пересобирало сообщение целиком — вместе с
 * картинками, которые из-за этого перезагружались. Собеседник прочитал
 * переписку, статусы поменялись у десятка сообщений разом, и лента моргала.
 * Здесь меняются только эти два маленьких блока, всё остальное не трогается.
 */
function patchMessageLight(wrapper, msg, isMine) {
    const bubble = wrapper.querySelector ? wrapper.querySelector('.msg-bubble') : null;
    if (!bubble) return;
    bubble.__msg = msg;

    // Ссылки на изменяемые части храним прямо в пузыре, а не ищем по имени
    // класса: имя легко разойтись с разметкой, и тогда старая строка не
    // находится, а новая добавляется рядом — реакции начинают множиться,
    // и снять их становится нельзя. Ссылка не врёт.
    if (bubble.__metaEl && bubble.__metaEl.parentNode === bubble) {
        const meta = buildMeta(msg, isMine);
        bubble.replaceChild(meta, bubble.__metaEl);
        bubble.__metaEl = meta;
    }

    // Реакции те же — строку не трогаем: иначе новая строка снова проигрывает
    // появление, и эмодзи «мигают» при каждом новом сообщении или смене галочек
    const old = bubble.__reactionsEl;
    const sig = reactionsSignature(msg);
    if (old && old.parentNode === bubble && old._sig === sig) return;
    const row = buildReactionsRow(msg);
    if (old && old.parentNode === bubble) {
        if (row) {
            row.style.animation = 'none';     // реакции поменялись в уже видимой строке — без повторного появления
            bubble.replaceChild(row, old);
        } else bubble.removeChild(old);
    } else if (row) {
        bubble.appendChild(row);
    }
    bubble.__reactionsEl = row || null;
    if (row) collapseReactionsOverflow(row);
}

function messageSign(msg) {
    // Только строение: текст, вложения, ответ. Реакции, время и состояние
    // доставки сюда не входят — они меняются часто, а правятся на месте,
    // без пересборки узла и перезагрузки картинок.
    return [
        msg.text || '',
        (msg.files || []).map(f => f.id || f.filename || '').join(','),
        (msg.attachments || []).length,
        msg.reply_to_id || '', msg.forwarded_from_id || '', msg.pinned ? 1 : 0,
    ].join('|');
}

function renderMessages(messages) {
    // фон миниатюр снимаем после отрисовки — обработчиков в атрибутах больше нет
    setTimeout(() => clearThumbBackgrounds(messagesList), 0);
    // Ленту не очищаем: узлы сверяются по ключу и переиспользуются, иначе при
    // каждом новом сообщении и каждой реакции экран моргает целиком.
    // Собираем нужный состав в список задач, а расстановкой займётся syncList.
    const plan = [];
    const hadMessages = messagesList.children.length > 0;
    loadedMessageIds.clear();
    closeReactionPopup();

    let lastDateStr = null;
    // Ширину строки с реакциями можно измерить только после вставки в
    // документ, поэтому лишние чипы схлопываются уже после отрисовки
    const pendingReactionRows = [];
    
    messages.forEach(msg => {
        loadedMessageIds.add(String(msg.id));
        if (msg.group_id) loadedMessageIds.add('group:' + msg.group_id);
        
        // Добавление разделителя дат
        if (msg.timestamp) {
            const dateStr = formatDateSeparator(msg.timestamp);
            if (dateStr !== lastDateStr) {
                const sep = document.createElement('div');
                sep.className = 'date-separator';
                sep.innerText = dateStr;
                plan.push({ key: 'date:' + dateStr, sign: dateStr, node: sep });
                lastDateStr = dateStr;
            }
        }
        
        // Сервисные сообщения-маркеры ("сменил аватар группы", "вышел из группы" и т.д.)
        if (isServiceMessage(msg)) {
            const specWrapper = document.createElement('div');
            specWrapper.className = 'service-message-row';

            const spec = document.createElement('div');
            spec.className = 'special-message';
            spec.innerText = buildServiceMessageText(msg);
            specWrapper.appendChild(spec);

            plan.push({ key: 'srv:' + msg.id, sign: spec.innerText, node: specWrapper });
            return;
        }
        
        // Обычное сообщение
        // Ни текста, ни файлов — показывать нечего. Так выглядела отправка,
        // у которой сервер не смог сохранить вложение: пустой пузырь в чате
        const hasFiles = Array.isArray(msg.files) && msg.files.length > 0;
        if (!hasFiles && !msg.file_path && !String(msg.text || '').trim()) return;

        const isMine = String(msg.sender_id) === currentUserId;
        const wrapper = document.createElement('div');
        const sticker = stickerOf(msg.text);
        const gif = !sticker ? gifOf(msg.text) : null;
        wrapper.className = `msg-wrapper ${isMine ? 'msg-mine' : 'msg-other'} ${msg.is_new ? 'msg-new' : ''} ${msg.pending ? 'msg-pending' : ''} ${sticker ? 'msg-sticker' : ''} ${gif ? 'msg-gif' : ''}`;
        wrapper.id = `msg-${msg.id}`;
        if (msg.group_id) wrapper.dataset.groupId = msg.group_id;
        // id вложений группы: по ним прокрутка находит обёртку, когда переход
        // идёт к конкретному файлу из списка вложений
        if (Array.isArray(msg.files) && msg.files.length) {
            const ids = msg.files.map(f => f && (f.id || f.message_id)).filter(Boolean);
            if (ids.length) wrapper.dataset.childIds = ids.join(' ');
        }

        if (!isMine && activeChatIsGroup) {
            // Сервер отдаёт sender_last_name/sender_first_name отдельно —
            // именно по этой паре считается цвет, как и везде в приложении.
            const avatarEl = document.createElement('div');
            avatarEl.className = 'msg-avatar';

            renderAvatar(avatarEl, {
                url: absoluteUrl(msg.sender_avatar_url),
                name: msg.sender_name,
                lastName: msg.sender_last_name,
                firstName: msg.sender_first_name
            });

            // Клик по аватарке открывает карточку пользователя
            if (msg.sender_id) {
                avatarEl.classList.add('clickable');
                avatarEl.title = msg.sender_name || 'Открыть карточку сотрудника';
                avatarEl.onclick = (e) => {
                    e.stopPropagation();
                    if (backend) backend.request_user_info_card(String(msg.sender_id));
                };
            }

            wrapper.appendChild(avatarEl);
        }
        
        const bubble = document.createElement('div');
        bubble.className = 'msg-bubble';
        
        // Имя отправителя для чужих сообщений
        if (!isMine && msg.sender_name && activeChatIsGroup) {
            const senderName = document.createElement('div');
            senderName.className = 'msg-sender';
            senderName.innerText = msg.sender_name;
            bubble.appendChild(senderName);
        }

        // Подпись автора над пересланным сообщением.
        // Показывается ВСЕГДА, в том числе когда пользователь переслал
        // собственное сообщение — иначе непонятно, чей это текст.
        const forwardedFrom = getForwardedFrom(msg);
        if (forwardedFrom) {
            const fwd = document.createElement('div');
            fwd.className = 'msg-forwarded';

            const fwdLabel = document.createElement('div');
            fwdLabel.className = 'msg-forwarded-label';
            fwdLabel.innerText = 'Пересланное сообщение';

            const fwdAuthor = document.createElement('div');
            fwdAuthor.className = 'msg-forwarded-author';
            fwdAuthor.innerText = forwardedAuthorName(forwardedFrom);

            fwd.appendChild(fwdLabel);
            fwd.appendChild(fwdAuthor);
            bubble.appendChild(fwd);
        }
        
        // Ответ на сообщение
        if (msg.reply_to || msg.reply_to_id || (msg.parent_id && msg.parent_id !== msg.group_id)) {
            const replyId = msg.reply_to || msg.reply_to_id || msg.parent_id;
            const replyDiv = document.createElement('div');
            // If it is an ID, make sure it is a number or string

            replyDiv.className = 'msg-reply';
            replyDiv.style.borderLeft = '3px solid var(--col-primary)';
            replyDiv.style.paddingLeft = '8px';
            replyDiv.style.marginBottom = '4px';
            replyDiv.style.cursor = 'pointer';
            replyDiv.style.fontSize = '12px';
            replyDiv.style.color = 'var(--col-text-secondary)';
            replyDiv.style.backgroundColor = 'var(--col-inset)';
            replyDiv.style.borderRadius = '4px';
            replyDiv.style.padding = '4px 8px';
            
            let replyText = "Ответ на сообщение";
            let replySender = "";
            // Попробуем найти оригинальное сообщение в памяти, чтобы показать кусочек текста
            const origMsg = chatMessages.find(m => String(m.id) === String(replyId));
            if (origMsg) {
                replySender = origMsg.sender_name || "";
                const shown = previewText(origMsg.text || '');
                if (shown) {
                    replyText = shown.substring(0, 30) + (shown.length > 30 ? "..." : "");
                } else if (origMsg.files && origMsg.files.length > 0) {
                    replyText = "Файл";
                }
            } else if (msg.reply_to_message) {
                const replyMsg = msg.reply_to_message;
                replySender = replyMsg.sender_name || "";
                const shown = previewText(replyMsg.text || '');
                if (shown) {
                    replyText = shown.substring(0, 30) + (shown.length > 30 ? "..." : "");
                } else {
                    replyText = "Файл";
                }
            }
            
            const replyTitle = document.createElement('div');
            replyTitle.innerText = replySender ? `Ответ для: ${replySender}` : "В ответ на:";
            replyTitle.style.fontWeight = 'bold';
            replyTitle.style.color = 'var(--col-primary)';
            replyTitle.style.marginBottom = '2px';
            
            const replyContent = document.createElement('div');
            replyContent.innerText = replyText;
            replyContent.style.whiteSpace = 'nowrap';
            replyContent.style.overflow = 'hidden';
            replyContent.style.textOverflow = 'ellipsis';
            
            replyDiv.appendChild(replyTitle);
            replyDiv.appendChild(replyContent);
            
            replyDiv.onclick = (e) => {
                e.stopPropagation();
                // Используем существующую функцию scrollToMessage, она умеет правильно подсвечивать баббл
                let el = document.getElementById(`msg-${replyId}`);
                if (el) {
                    scrollToMessage(replyId);
                } else {
                    if (backend) backend.go_to_message(String(replyId), '');
                }
            };
            bubble.appendChild(replyDiv);
        }
        
        
        
        // Файлы
        if (msg.files && msg.files.length > 0) {
            const mediaDiv = document.createElement('div');
            mediaDiv.className = 'msg-media';
            const albumTiles = [], docTiles = [];
            
            msg.files.forEach(f => {
                const fileItem = document.createElement('div');
                fileItem.__file = f;   // какой файл группы — для выбора по одному
                const ext = f.filename ? f.filename.split('.').pop().toLowerCase() : '';
                const isVideo = ['mp4', 'avi', 'mov', 'mkv', 'webm', 'ts'].includes(ext);
                const isImage = f.is_image || ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'heic'].includes(ext);
                
                if (isImage) {
                    let fullUrl = f.url || '';
                    if (fullUrl && !fullUrl.endsWith('.gz') && fullUrl.includes('/file_storage/')) {
                        fullUrl += '.gz';
                    }
                    
                    let thumbUrl = f.thumb_url || fullUrl;
                    if (f.thumb_url && !thumbUrl.endsWith('.gz') && thumbUrl.includes('/file_storage/')) {
                        thumbUrl += '.gz';
                    }
                    
                    let imgStyle = '';
                    if (f.width && f.height && msg.files.length === 1) {
                        let w = f.width;
                        let h = f.height;
                        let maxW = 300;
                        let maxH = 400;
                        if (w > maxW) {
                            h = Math.round((maxW / w) * h);
                            w = maxW;
                        }
                        if (h > maxH) {
                            w = Math.round((maxH / h) * w);
                            h = maxH;
                        }
                        // Одиночный файл в сообщении показывается целиком:
                        // ширина с потолком по пузырю, высота из пропорций
                        imgStyle = `width: ${w}px; max-width: 100%; height: auto;`
                            + ` aspect-ratio: ${w} / ${h}; object-fit: contain;`;
                    } else if (msg.files.length === 1) {
                        imgStyle = `width: 300px; max-width: 100%; height: auto; max-height: 60vh; object-fit: contain;`;
                    }
                    
                    let bgImg = '';
                    if (f.micro_thumb) {
                        bgImg = `background-image: url('data:image/jpeg;base64,${f.micro_thumb}'); background-size: cover; background-position: center;`;
                    }
                    
                    fileItem.innerHTML = `<img src="${withAuth(thumbUrl)}" class="media-image" alt="${escapeAttr(f.filename)}" style="${imgStyle} ${bgImg}" loading="lazy" decoding="async" data-clear-bg="1">`;
                    fileItem.onclick = (e) => {
                        e.stopPropagation();
                        // always open the original full URL when clicked
                        let fCopy = Object.assign({}, f);
                        fCopy.url = f.url; // Use original
                        if(backend) backend.open_media(String(msg.id), JSON.stringify(fCopy));
                    };
                    fileItem.oncontextmenu = (e) => {
                        if (coarsePointer()) { e.preventDefault(); e.stopPropagation(); return; }   // телефон: долгое нажатие — выбор
                        e.stopPropagation();
                        e.preventDefault();
                        showContextMenu(e.clientX, e.clientY, msg, isMine, f);
                    };
                } else if (isVideo) {
                    let vidStyle = '';
                    if (f.width && f.height && msg.files.length === 1) {
                        let w = f.width;
                        let h = f.height;
                        let maxW = 300;
                        let maxH = 400;
                        if (w > maxW) {
                            h = Math.round((maxW / w) * h);
                            w = maxW;
                        }
                        if (h > maxH) {
                            w = Math.round((maxH / h) * w);
                            h = maxH;
                        }
                        // Как и у фотографии: ширина с потолком по пузырю,
                        // высота из пропорций, кадр не обрезается
                        vidStyle = `width: ${w}px; max-width: 100%; height: auto;`
                            + ` aspect-ratio: ${w} / ${h}; object-fit: contain;`;
                    } else if (msg.files.length === 1) {
                        vidStyle = `width: 300px; max-width: 100%; height: auto; max-height: 60vh; object-fit: contain;`;
                    }

                    let videoHtml = '';
                    if (f.preview_data) {
                        videoHtml = `<img src="data:image/png;base64,${f.preview_data}" class="media-image" alt="${escapeAttr(f.filename)}" style="${vidStyle}">`;
                    } else {
                        let videoUrl = f.url || '';
                        if (videoUrl.endsWith('.gz')) videoUrl = videoUrl.slice(0, -3);
                        videoUrl = videoUrl.replace(/\.(avi|mov|mkv|ts|wmv|flv)$/i, '.webm');
                        // обложка — миниатюра с сервера: без неё Android показывает серую заглушку со значком «воспроизвести»
                        videoHtml = `<img src="${withAuth(videoUrl + '.thumb.jpg')}" class="media-image" alt="" loading="lazy" decoding="async" style="background: #000 ${f.micro_thumb ? `url('data:image/jpeg;base64,${f.micro_thumb}') center / cover no-repeat` : ''}; ${vidStyle}">`;
                    }
                    
                    fileItem.innerHTML = `${videoHtml}
                                          <span class="video-play"><i></i></span>`;
                    fileItem.style.position = 'relative';
                    fileItem.onclick = (e) => {
                        e.stopPropagation();
                        let fCopy = Object.assign({}, f);
                        fCopy.url = (f.url || '').replace(/\.gz$/, '').replace(/\.(avi|mov|mkv|ts|wmv|flv)$/i, '.webm');
                        if(backend) backend.open_media(String(msg.id), JSON.stringify(fCopy));
                    };
                    fileItem.oncontextmenu = (e) => {
                        if (coarsePointer()) { e.preventDefault(); e.stopPropagation(); return; }   // телефон: долгое нажатие — выбор
                        e.stopPropagation();
                        e.preventDefault();
                        showContextMenu(e.clientX, e.clientY, msg, isMine, f);
                    };
                } else {
                    fileItem.className = 'media-file';
                    fileItem.innerHTML = `
                        <div class="media-file-icon">📄</div>
                        <div class="media-file-info">
                            <span class="media-file-name">${escapeAttr(f.filename || 'Файл')}</span>
                            <span class="media-file-size">${formatBytes(f.size || 0)}</span>
                        </div>
                    `;
                    markDownloadTarget(fileItem, msg.id, f);
                    fileItem.onclick = (e) => {
                        e.stopPropagation();
                        if(backend) backend.open_file(String(msg.id), JSON.stringify(f));
                    };
                    fileItem.oncontextmenu = (e) => {
                        if (coarsePointer()) { e.preventDefault(); e.stopPropagation(); return; }   // телефон: долгое нажатие — выбор
                        e.stopPropagation();
                        e.preventDefault();
                        showContextMenu(e.clientX, e.clientY, msg, isMine, f);
                    };
                }
                // Документы идут отдельным списком под альбомом: смешивать
                // карточку файла с сеткой картинок — значит ломать и то, и другое
                (fileItem.classList.contains('media-file') ? docTiles : albumTiles).push(fileItem);
            });
            if (albumTiles.length) {
                const album = document.createElement('div');
                layoutAlbum(album, albumTiles);
                mediaDiv.appendChild(album);
            }
            docTiles.forEach(d => mediaDiv.appendChild(d));
            bubble.appendChild(mediaDiv);
        } else if (msg.file_path) {
            // Одиночный файл из старого формата
            const mediaDiv = document.createElement('div');
            mediaDiv.className = 'msg-media';
            const ext = msg.file_name ? msg.file_name.split('.').pop().toLowerCase() : '';
            const isVideo = ['mp4', 'avi', 'mov', 'mkv', 'webm', 'ts'].includes(ext);
            const isImage = msg.is_image || ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'heic'].includes(ext);
            
            if (isImage) {
                let fullUrl = msg.url || `/file_storage/${msg.file_path.replace(/\\\\/g, '/')}`;
                if (fullUrl && !fullUrl.endsWith('.gz') && fullUrl.includes('/file_storage/')) {
                    fullUrl += '.gz';
                }
                
                let thumbUrl = msg.thumb_url || fullUrl;
                if (msg.thumb_url && !thumbUrl.endsWith('.gz') && thumbUrl.includes('/file_storage/')) {
                    thumbUrl += '.gz';
                }
                
                // Размер задаётся в пикселях, но с потолком по ширине пузыря:
                // раньше жёсткие 300 px не давали картинке ужаться на узком
                // экране, и правый край уходил под границу сообщения, а
                // object-fit: cover срезал у кадра верх и низ.
                let imgStyle = '';
                if (msg.width && msg.height) {
                    let w = msg.width;
                    let h = msg.height;
                    const maxW = 300;
                    const maxH = 400;
                    if (w > maxW) { h = Math.round((maxW / w) * h); w = maxW; }
                    if (h > maxH) { w = Math.round((maxH / h) * w); h = maxH; }
                    imgStyle = `width: ${w}px; max-width: 100%; height: auto;`
                        + ` aspect-ratio: ${msg.width} / ${msg.height}; object-fit: contain;`;
                } else {
                    imgStyle = `width: 300px; max-width: 100%; height: auto; max-height: 60vh; object-fit: contain;`;
                }
                
                let bgImg = '';
                if (msg.micro_thumb) {
                    bgImg = `background-image: url('data:image/jpeg;base64,${msg.micro_thumb}'); background-size: cover; background-position: center;`;
                }
                
                mediaDiv.innerHTML = `<img src="${withAuth(thumbUrl)}" class="media-image" alt="${escapeAttr(msg.file_name || 'image')}" style="${imgStyle} ${bgImg}" loading="lazy" decoding="async"  data-clear-bg="1">`;
                mediaDiv.onclick = (e) => {
                    e.stopPropagation();
                    let msgCopy = Object.assign({}, msg);
                    msgCopy.url = msg.url || fullUrl; // Use original
                    if(backend) backend.open_media(String(msg.id), JSON.stringify(msgCopy));
                };
            } else if (isVideo) {
                let vidStyle = '';
                if (msg.width && msg.height) {
                    let w = msg.width;
                    let h = msg.height;
                    let maxW = 300;
                    let maxH = 400;
                    if (w > maxW) {
                        h = Math.round((maxW / w) * h);
                        w = maxW;
                    }
                    if (h > maxH) {
                        w = Math.round((maxH / h) * w);
                        h = maxH;
                    }
                    // Как и у фотографии: пиксели с потолком по ширине пузыря,
                    // высота из пропорций. Жёсткая ширина не давала видео
                    // ужаться, и правый край уходил под границу сообщения.
                    vidStyle = `width: ${w}px; max-width: 100%; height: auto;`
                        + ` aspect-ratio: ${w} / ${h}; object-fit: contain;`;
                } else {
                    vidStyle = `width: 300px; max-width: 100%; height: auto; max-height: 60vh; object-fit: contain;`;
                }

                let videoHtml = '';
                if (msg.preview_data) {
                    videoHtml = `<img src="data:image/png;base64,${msg.preview_data}" class="media-image" alt="${msg.file_name}" style="${vidStyle}">`;
                } else {
                    let videoUrl = msg.url || `/file_storage/${msg.file_path.replace(/\\\\/g, '/')}`;
                    if (videoUrl.endsWith('.gz')) videoUrl = videoUrl.slice(0, -3);
                    videoUrl = videoUrl.replace(/\.(avi|mov|mkv|ts|wmv|flv)$/i, '.webm');
                    videoHtml = `<img src="${withAuth(videoUrl + '.thumb.jpg')}" class="media-image" alt="" loading="lazy" decoding="async" style="background: #000 ${msg.micro_thumb ? `url('data:image/jpeg;base64,${msg.micro_thumb}') center / cover no-repeat` : ''}; ${vidStyle}">`;
                }

                mediaDiv.innerHTML = `${videoHtml}
                                      <span class="video-play"><i></i></span>`;
                mediaDiv.style.position = 'relative';
                mediaDiv.onclick = (e) => {
                    e.stopPropagation();
                    let msgCopy = Object.assign({}, msg);
                    msgCopy.url = (msg.url || '').replace(/\.gz$/, '').replace(/\.(avi|mov|mkv|ts|wmv|flv)$/i, '.webm');
                    if(backend) backend.open_media(String(msg.id), JSON.stringify(msgCopy));
                };
            } else {
                mediaDiv.className = 'media-file';
                mediaDiv.innerHTML = `
                    <div class="media-file-icon">📄</div>
                    <div class="media-file-info">
                        <span class="media-file-name">${escapeAttr(msg.file_name || 'Файл')}</span>
                        <span class="media-file-size">${formatBytes(msg.file_size || 0)}</span>
                    </div>
                `;
                markDownloadTarget(mediaDiv, msg.id, msg);
                mediaDiv.onclick = (e) => {
                    e.stopPropagation();
                    if(backend) backend.open_file(String(msg.id), JSON.stringify(msg));
                };
            }
            bubble.appendChild(mediaDiv);
        }
        
        // Стикер и GIF — самостоятельные объекты: картинка вместо текста.
        // Блок должен идти после объявления sticker/gif и создания bubble,
        // иначе обращение к ним обрывает отрисовку всей ленты сообщений.
        if (sticker || gif) {
            const holder = document.createElement('div');
            const media = document.createElement('img');
            if (sticker) {
                media.className = 'sticker-view';
                media.src = sticker.src;
                media.alt = 'Стикер';
                media.draggable = false;
            } else {
                media.className = 'gif-view';
                media.src = gif.src;
                media.alt = 'GIF';
                media.loading = 'lazy';
                media.draggable = false;
            }
            holder.appendChild(media);
            bubble.appendChild(holder);
        }

        // Текст. У стикера и GIF текст — это служебная пометка, её не показываем
        if (msg.text && !sticker && !gif) {
            const textDiv = document.createElement('div');
            textDiv.className = 'msg-text';
            textDiv.innerText = msg.text;
            bubble.appendChild(textDiv);
        }
        
        // Мета-информация (время, изменено)
        bubble.__metaEl = buildMeta(msg, isMine);
        bubble.appendChild(bubble.__metaEl);
        
        // Реакции
        const reactionsRow = buildReactionsRow(msg);
        bubble.__reactionsEl = reactionsRow || null;
        if (reactionsRow) {
            bubble.appendChild(reactionsRow);
            pendingReactionRows.push(reactionsRow);
        }
        
        // Меню действий: правая и левая кнопка мыши, долгое нажатие на телефоне
        bubble.__msg = msg;
        bubble.__isMine = isMine;
        attachMessageMenu(bubble, msg, isMine);
        
        wrapper.appendChild(bubble);
        plan.push({ key: 'msg:' + msg.id, sign: messageSign(msg), node: wrapper,
                    msg: msg, mine: isMine });
    });

    // Временный пузырь загрузки дорисовывается ниже, вне сверки: своего
    // номера у него нет, и при следующей отрисовке он убирается как чужой —
    // это и нужно, его место занимает настоящее сообщение
    const placed = Kov.UI.syncList(messagesList, plan, {
        key: p => p.key,
        sign: p => p.sign,
        make: p => p.node,
    });
    // Узлы, пережившие сверку, — это чужие свежесобранные пузыри не заменили:
    // им нужно обновить время, состояние доставки и реакции
    placed.forEach((el, i) => {
        const item = plan[i];
        if (!item || !item.msg) return;
        if (el !== item.node) { patchMessageLight(el, item.msg, item.mine); return; }
        // Узел новый. Появление сглаживаем, но только если лента уже была
        // на экране: при первом открытии переписки мигать было бы нечему
        if (hadMessages) el.classList.add('msg-new');
    });

    pendingReactionRows.forEach(collapseReactionsOverflow);
    initMessageContextMenu();

    if (tempUploadData) {
        // Временное сообщение того же размера, что и настоящее: на каждой
        // плитке — свой процент и состояние (отправка, в очереди, сжатие)
        const wrapper = document.createElement('div');
        wrapper.className = 'msg-wrapper msg-mine msg-temp';
        const bubble = document.createElement('div');
        bubble.className = 'msg-bubble';

        if (tempUploadData.files && tempUploadData.files.length > 0) {
            const mediaDiv = document.createElement('div');
            mediaDiv.className = 'msg-media';
            const tempTiles = [], tempDocs = [];
            tempUploadData.files.forEach((f, i) => {
                const tile = document.createElement('div');
                tile.className = 'temp-tile';
                tile.dataset.index = String(i);
                const ext = f.filename ? f.filename.split('.').pop().toLowerCase() : '';
                const isVideo = ['mp4', 'avi', 'mov', 'mkv', 'webm', 'ts', 'm4v'].includes(ext);
                const isImage = f.is_image || ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'heic'].includes(ext);
                if ((isImage || isVideo) && f.preview_url) {
                    tile.innerHTML = isVideo
                        ? `<video src="${escapeAttr(f.preview_url)}" poster="${escapeAttr(localPoster(f.preview_url))}" data-local-poster="1" muted preload="metadata" playsinline class="media-image" style="background: black"></video>`
                        : `<img src="${escapeAttr(f.preview_url)}" class="media-image" alt="">`;
                } else if (isImage && f.preview_data) {
                    tile.innerHTML = `<img src="data:image/png;base64,${f.preview_data}" class="media-image" alt="">`;
                } else {
                    tile.className += ' media-file';
                    tile.innerHTML = `<div class="media-file-icon">📄</div><div class="media-file-info"><span class="media-file-name">${escapeAttr(f.filename || 'Файл')}</span></div>`;
                }
                const ring = document.createElement('div');
                ring.className = 'temp-ring';
                ring.innerHTML = '<div class="temp-ring-fill"></div><span class="temp-pct">0%</span><span class="temp-note"></span><span class="temp-x">✕</span>';
                ring.title = 'Отменить загрузку';
                ring.addEventListener('click', e => { e.stopPropagation(); cancelTempUpload(); });
                tile.appendChild(ring);
                (tile.classList.contains('media-file') ? tempDocs : tempTiles).push(tile);
            });
            if (tempTiles.length) {
                const album = document.createElement('div');
                layoutAlbum(album, tempTiles);
                mediaDiv.appendChild(album);
            }
            tempDocs.forEach(d => mediaDiv.appendChild(d));
            // кольцо — по размеру плитки: 42 % меньшей стороны, от 28 до 56 px
            const fitRings = () => tempTiles.forEach(t => {
                const r = t.getBoundingClientRect();
                if (r.width && r.height) t.style.setProperty('--ring', Math.round(Math.max(28, Math.min(56, Math.min(r.width, r.height) * 0.42))) + 'px');
            });
            requestAnimationFrame(fitRings);
            setTimeout(fitRings, 400);
            bubble.appendChild(mediaDiv);
        }
        // отмена — касанием кольца («✕» в центре); процентов нет — кольца достаточно
        if (tempUploadData.text) {
            const textDiv = document.createElement('div');
            textDiv.className = 'msg-text';
            textDiv.innerText = tempUploadData.text;
            bubble.appendChild(textDiv);
        }
        // Полосы и подписи под плитками больше нет: процент показывает
        // кольцо на самой плитке, два одинаковых числа рядом только мешали
        wrapper.appendChild(bubble);
        messagesList.appendChild(wrapper);
        applyTempDetail();
    }
}

function formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

// === Реакции ===

const REACTION_EMOJIS = {
    'like': '👍', 'dislike': '👎', 'sad': '😢', 'trophy': '🏆', 'confetti': '🎉',
    'laugh': '😂', 'fire': '🔥', 'heart': '❤️', 'wow': '😮', 'check': '✅',
    'poop': '💩', 'thinking': '🤔', 'cool': '😎', 'clown': '🤡', 'angry': '😡'
};

// Подписи для всплывающей подсказки: служебные ключи пользователю ни о чём
const REACTION_TITLES = {
    'like': 'Нравится', 'dislike': 'Не нравится', 'sad': 'Грустно', 'trophy': 'Отлично',
    'confetti': 'Поздравляю', 'laugh': 'Смешно', 'fire': 'Огонь', 'heart': 'Сердце',
    'wow': 'Ого', 'check': 'Готово', 'poop': 'Плохо', 'thinking': 'Думаю',
    'cool': 'Круто', 'clown': 'Несерьёзно', 'angry': 'Злюсь'
};

// Реакции, видимые в меню сразу. Остальные — под кнопкой-стрелкой.
const QUICK_REACTIONS = ['like', 'dislike', 'heart', 'check'];

function reactionEmoji(type) {
    return REACTION_EMOJIS[type] || type || '';
}

/**
 * Сводит реакции сообщения к списку групп по типу эмодзи.
 *
 * Основной источник — reactions_detailed: там есть автор с именем и фото.
 * Старое поле reactions остаётся запасным вариантом, если сообщение
 * пришло от сервера прошлой версии.
 */
function groupReactions(msg) {
    if (!msg) return [];

    const order = [];
    const groups = new Map();

    const push = (type, user) => {
        if (!type) return;
        if (!groups.has(type)) {
            groups.set(type, { type: type, users: [], mine: false });
            order.push(type);
        }
        const group = groups.get(type);
        group.users.push(user);
        if (String(user.user_id) === String(currentUserId)) group.mine = true;
    };

    const detailed = Array.isArray(msg.reactions_detailed) ? msg.reactions_detailed : null;

    if (detailed && detailed.length) {
        detailed.forEach(r => push(r.reaction_type, {
            user_id: r.user_id,
            name: r.name || '',
            last_name: r.last_name || '',
            first_name: r.first_name || '',
            avatar_url: r.avatar_url || null
        }));
    } else if (msg.reactions) {
        Object.entries(msg.reactions).forEach(([uid, type]) => push(type, {
            user_id: uid,
            name: '',
            last_name: '',
            first_name: '',
            avatar_url: null
        }));
    }

    return order.map(type => groups.get(type));
}

/** Круглое лицо автора реакции — как в Telegram, когда реакция одна. */
function buildReactionFace(user) {
    const face = document.createElement('span');
    face.className = 'reaction-face';
    renderAvatar(face, {
        url: absoluteUrl(user.avatar_url),
        name: user.name,
        lastName: user.last_name,
        firstName: user.first_name
    });
    return face;
}

function reactionUserLabel(user) {
    const name = (user.name || '').trim();
    return name || `Сотрудник #${user.user_id}`;
}

/** Отпечаток реакций сообщения: какие, чьи и есть ли среди них свои. */
function reactionsSignature(msg) {
    return JSON.stringify(groupReactions(msg).map(g => [g.type, !!g.mine, g.users.map(u => u.user_id)]));
}

function buildReactionsRow(msg) {
    const groups = groupReactions(msg);
    if (!groups.length) return null;

    const row = document.createElement('div');
    row.className = 'reactions-row';
    row._reactionGroups = groups;
    row._messageId = msg.id;
    row._sig = reactionsSignature(msg);

    groups.forEach(group => {
        const chip = document.createElement('div');
        chip.className = `reaction-chip${group.mine ? ' mine' : ''}`;
        chip.title = group.users.map(reactionUserLabel).join(', ');

        const emoji = document.createElement('span');
        emoji.className = 'reaction-emoji';
        emoji.innerText = reactionEmoji(group.type);
        chip.appendChild(emoji);

        // Один человек — показываем его лицо, несколько — их количество
        if (group.users.length === 1) {
            chip.appendChild(buildReactionFace(group.users[0]));
        } else {
            const count = document.createElement('span');
            count.className = 'reaction-count';
            count.innerText = group.users.length;
            chip.appendChild(count);
        }

        chip.onclick = (e) => {
            e.stopPropagation();
            if (backend) backend.send_reaction(String(msg.id), group.type);
        };

        row.appendChild(chip);
    });

    return row;
}

/**
 * Прячет чипы, не поместившиеся в ширину сообщения, и заменяет их на «+N».
 *
 * Реакций может быть больше, чем влезает под текст, поэтому в одну строку
 * остаются только те, что поместились, а остальные открываются списком.
 */
function collapseReactionsOverflow(row) {
    if (!row || !row.isConnected) return;

    const chips = Array.from(row.querySelectorAll('.reaction-chip'));
    if (chips.length < 2) return;

    const existingMore = row.querySelector('.reaction-more');
    if (existingMore) existingMore.remove();

    chips.forEach(chip => chip.classList.remove('hidden'));

    const firstTop = chips[0].offsetTop;
    let firstOverflow = chips.findIndex(chip => chip.offsetTop > firstTop);
    if (firstOverflow === -1) return;

    // Место под сам «+N» тоже нужно занять, поэтому прячем на чип больше
    if (firstOverflow > 1) firstOverflow -= 1;

    const hidden = chips.slice(firstOverflow);
    hidden.forEach(chip => chip.classList.add('hidden'));

    const more = document.createElement('div');
    more.className = 'reaction-chip reaction-more';
    more.innerText = `+${hidden.length}`;
    more.title = 'Показать все реакции';
    more.onclick = (e) => {
        e.stopPropagation();
        openReactionPopup(more, row._reactionGroups, row._messageId);
    };
    row.appendChild(more);

    // Сам «+N» тоже занимает место: если из-за него строка снова
    // перенеслась, прячем ещё по одному чипу
    let visible = chips.slice(0, firstOverflow);
    while (visible.length > 1 && more.offsetTop > firstTop) {
        const last = visible.pop();
        last.classList.add('hidden');
        more.innerText = `+${chips.length - visible.length}`;
    }
}

let reactionPopup = null;

function closeReactionPopup() {
    if (reactionPopup) {
        reactionPopup.remove();
        reactionPopup = null;
    }
}

function openReactionPopup(anchor, groups, messageId) {
    closeReactionPopup();
    if (!groups || !groups.length) return;

    const popup = document.createElement('div');
    popup.className = 'reaction-popup';

    groups.forEach(group => {
        const section = document.createElement('div');
        section.className = 'reaction-popup-group';

        const head = document.createElement('div');
        head.className = `reaction-popup-head${group.mine ? ' mine' : ''}`;
        head.innerHTML = `<span class="reaction-emoji">${reactionEmoji(group.type)}</span>`;

        const total = document.createElement('span');
        total.className = 'reaction-popup-count';
        total.innerText = group.users.length;
        head.appendChild(total);

        head.onclick = (e) => {
            e.stopPropagation();
            if (backend) backend.send_reaction(String(messageId), group.type);
            closeReactionPopup();
        };
        section.appendChild(head);

        group.users.forEach(user => {
            const line = document.createElement('div');
            line.className = 'reaction-popup-user';

            const face = document.createElement('span');
            face.className = 'reaction-popup-face';
            renderAvatar(face, {
                url: absoluteUrl(user.avatar_url),
                name: user.name,
                lastName: user.last_name,
                firstName: user.first_name
            });
            line.appendChild(face);

            const label = document.createElement('span');
            label.className = 'reaction-popup-name';
            label.innerText = reactionUserLabel(user);
            line.appendChild(label);

            section.appendChild(line);
        });

        popup.appendChild(section);
    });

    document.body.appendChild(popup);
    reactionPopup = popup;

    const rect = anchor.getBoundingClientRect();
    const width = popup.offsetWidth;
    const height = popup.offsetHeight;

    let left = rect.left;
    let top = rect.bottom + 6;
    if (left + width > window.innerWidth - 8) left = window.innerWidth - width - 8;
    if (top + height > window.innerHeight - 8) top = rect.top - height - 6;

    popup.style.left = `${Math.max(8, left)}px`;
    popup.style.top = `${Math.max(8, top)}px`;
}

document.addEventListener('click', (e) => {
    if (reactionPopup && !reactionPopup.contains(e.target)) closeReactionPopup();
});

document.addEventListener('scroll', closeReactionPopup, true);

// === Контекстное меню ===

let currentContextMenu = null;

/**
 * Кнопка меню в углу пузыря и долгое нажатие на сенсорном экране.
 * Раньше меню открывалось только правой кнопкой — на телефоне до него было
 * не добраться, а на компьютере о нём никто не догадывался.
 */
/**
 * Правый клик по сообщению. Обработчик один на всю переписку и вешается на
 * контейнер: раньше он присваивался каждому пузырю через oncontextmenu, и
 * достаточно было одного перерисованного или вложенного элемента, чтобы
 * браузер показал своё меню вместо нашего. Делегирование на контейнере
 * работает для любых потомков и переживает перерисовку списка.
 */
function initMessageContextMenu() {
    const host = messageListContainer || messagesList;
    if (!host || host.dataset.menuBound === '1') return;
    host.dataset.menuBound = '1';

    host.addEventListener('contextmenu', (e) => {
        if (coarsePointer()) { e.preventDefault(); return; }   // телефон: долгое нажатие — выбор, не меню
        if (Date.now() - kbdMenuAt < 600) { e.preventDefault(); return; }   // клавиша меню: уже открыто с клавиатуры
        const bubble = e.target.closest && e.target.closest('.msg-bubble');
        if (!bubble || !bubble.__msg) return;
        e.preventDefault();
        e.stopPropagation();
        showContextMenu(e.clientX, e.clientY, bubble.__msg, bubble.__isMine);
    });

    // Меню — обычным нажатием по сообщению (телефон — касание, компьютер — левая кнопка).
    // Нажатия по вложениям, ссылкам и кнопкам работают как раньше; выделение текста не мешает.
    host.addEventListener('click', (e) => {
        const picked = window.getSelection && window.getSelection();
        if (picked && !picked.isCollapsed && String(picked).trim()) return;
        if (e.target.closest('a, button, img, video, input, textarea, .file-item, .msg-reactions, .reaction-chip, .msg-reply')) return;
        const bubble = e.target.closest && e.target.closest('.msg-bubble');
        if (!bubble || !bubble.__msg) return;
        e.preventDefault();
        e.stopPropagation();
        const r = bubble.getBoundingClientRect();
        showContextMenu(Math.min(e.clientX, r.right - 8), e.clientY, bubble.__msg, bubble.__isMine);
    });
}

function attachMessageMenu(bubble, msg, isMine) {
    // Долгое нажатие: 450 мс без заметного смещения пальца
    let timer = null, startX = 0, startY = 0, target = null;
    const cancel = () => { clearTimeout(timer); timer = null; };
    bubble.addEventListener('touchstart', (e) => {
        const t = e.touches[0];
        startX = t.clientX; startY = t.clientY; target = e.target;
        cancel();
        timer = setTimeout(() => {
            timer = null;
            if (navigator.vibrate) { try { navigator.vibrate(12); } catch (err) {} }
            bubble.__lpAt = Date.now();   // касание после долгого нажатия не открывает файл
            if (coarsePointer()) { pressSelect(bubble, target, msg, isMine); return; }   // телефон: долгое нажатие — выбор
            showContextMenu(startX, startY, msg, isMine);
        }, 450);
    }, { passive: true });
    bubble.addEventListener('touchmove', (e) => {
        const t = e.touches[0];
        if (Math.abs(t.clientX - startX) > 10 || Math.abs(t.clientY - startY) > 10) cancel();
    }, { passive: true });
    ['touchend', 'touchcancel'].forEach(ev => bubble.addEventListener(ev, cancel, { passive: true }));
}

// ── выбор нескольких сообщений: переслать или удалить разом ──
// в группе файлов можно выбрать часть (отдельные файлы) или всю группу
let selecting = false;
const selectedMsgs = new Map();   // номер сообщения → { msg, isMine, files: null — всё сообщение | Set номеров файлов }
let selWatch = null;
function coarsePointer() { return !!(window.matchMedia && window.matchMedia('(pointer: coarse)').matches); }
function fileIdOf(f) { const v = f && (f.id || f.message_id); return v ? String(v) : null; }
function groupFileIds(msg) { return (msg.files || []).length > 1 ? msg.files.map(fileIdOf).filter(Boolean) : []; }
function isWhole(entry) { return !entry.files || groupFileIds(entry.msg).every(id => entry.files.has(id)); }
function tileFileId(bubble, el) {
    for (; el && el !== bubble; el = el.parentElement) if (el.__file) return fileIdOf(el.__file);
    return null;
}
function selBar() {
    let bar = document.getElementById('selBar');
    if (bar) return bar;
    bar = document.createElement('div');
    bar.id = 'selBar';
    bar.className = 'sel-bar';
    bar.hidden = true;
    bar.innerHTML = '<button type="button" class="sel-x" aria-label="Отменить выбор">✕</button><span class="sel-count"></span>'
        + '<button type="button" class="sel-fwd">Переслать</button><button type="button" class="sel-del">Удалить</button>';
    const ordered = () => [...selectedMsgs.entries()].sort((p, q) => Number(p[0]) - Number(q[0]));   // по порядку написания
    bar.querySelector('.sel-x').onclick = () => stopSelect();
    bar.querySelector('.sel-fwd').onclick = () => {
        const ops = ordered().map(([id, v]) => ({ id: Number(id), files: isWhole(v) ? null : [...v.files].map(Number) }));
        stopSelect();
        if (backend) backend.forward_messages(ops);
    };
    bar.querySelector('.sel-del').onclick = () => {
        const ids = [];
        ordered().forEach(([id, v]) => { if (isWhole(v)) ids.push(id); else v.files.forEach(f => ids.push(f)); });   // часть группы — по файлам
        stopSelect();
        if (backend) backend.confirm_delete_messages(ids);
    };
    (document.getElementById('chatArea') || document.getElementById('kovChat') || document.body).appendChild(bar);
    return bar;
}
function selCount() {
    let n = 0;
    selectedMsgs.forEach(v => { n += isWhole(v) ? (groupFileIds(v.msg).length || 1) : v.files.size; });
    return n;
}
function paintSelection() {
    document.querySelectorAll('#messagesList .msg-bubble').forEach(b => {
        const v = b.__msg && selectedMsgs.get(String(b.__msg.id));
        const whole = !!v && isWhole(v);
        b.classList.toggle('sel-on', whole);
        b.classList.toggle('sel-part', !!v && !whole);
        // кружок у сообщения: выбрать или снять всё сообщение (у группы — все файлы сразу)
        const row = b.parentElement;
        let dot = row && row.querySelector(':scope > .sel-circle');
        if (row && !dot && selecting && b.__msg) {
            dot = document.createElement('button');
            dot.type = 'button';
            dot.className = 'sel-circle';
            dot.setAttribute('aria-label', 'Выбрать сообщение целиком');
            dot.onclick = e => { e.preventDefault(); e.stopPropagation(); toggleWhole(b.__msg, b.__isMine); };
            row.classList.add('sel-row');
            row.prepend(dot);
        }
        if (dot) {
            dot.hidden = !selecting;
            dot.style.left = '';   // кружок — вплотную к своему сообщению (слева от пузыря), а не через весь экран
            dot.classList.toggle('on', whole);
            dot.classList.toggle('part', !!v && !whole);
        }
        const group = b.__msg && groupFileIds(b.__msg).length > 0;
        b.querySelectorAll('*').forEach(el => {
            if (!el.__file || !group) return;
            el.classList.toggle('sel-able', selecting);
            el.classList.toggle('sel-file', !!v && (whole || v.files.has(fileIdOf(el.__file))));
        });
    });
    const bar = selBar();
    bar.hidden = !selecting;
    bar.querySelector('.sel-count').textContent = 'Выбрано: ' + selCount();
    // удалить можно, только если всё выбранное — своё
    bar.querySelector('.sel-del').hidden = ![...selectedMsgs.values()].every(v => v.isMine);
}
function toggleWhole(msg, isMine) {
    const id = String(msg.id), cur = selectedMsgs.get(id);
    if (cur && isWhole(cur)) selectedMsgs.delete(id); else selectedMsgs.set(id, { msg, isMine: !!isMine, files: null });
    afterToggle();
}
function toggleFile(msg, isMine, fid) {
    const all = groupFileIds(msg);
    if (!fid || !all.length) return toggleWhole(msg, isMine);
    const id = String(msg.id);
    const cur = selectedMsgs.get(id) || { msg, isMine: !!isMine, files: new Set() };
    if (!cur.files) cur.files = new Set(all);   // было всё — снимаем один
    if (cur.files.has(fid)) cur.files.delete(fid); else cur.files.add(fid);
    if (!cur.files.size) selectedMsgs.delete(id);
    else { if (all.every(x => cur.files.has(x))) cur.files = null; selectedMsgs.set(id, cur); }   // все файлы — значит, вся группа
    afterToggle();
}
function afterToggle() { if (!selectedMsgs.size) stopSelect(); else paintSelection(); }
function toggleSelect(msg, isMine, fid) { if (fid) toggleFile(msg, isMine, fid); else toggleWhole(msg, isMine); }
function startSelect(msg, isMine, fid) {
    if (!selecting) {
        selecting = true;
        const root = document.getElementById('kovChat');
        if (root) root.classList.add('selecting');
        const list = document.getElementById('messagesList');
        if (list && window.MutationObserver) {   // ушли в другой чат — выбор снимается; перерисовка — отметки возвращаются
            selWatch = new MutationObserver(() => {
                const shown = new Set([...document.querySelectorAll('#messagesList .msg-bubble')].map(b => b.__msg && String(b.__msg.id)));
                if (![...selectedMsgs.keys()].some(id => shown.has(id))) stopSelect(); else paintSelection();
            });
            selWatch.observe(list, { childList: true, subtree: true });
        }
    }
    toggleSelect(msg, isMine, fid);
}
// телефон: долгое нажатие — выбрать файл группы (на плитке) или всё сообщение
function pressSelect(bubble, target, msg, isMine) { startSelect(msg, isMine, tileFileId(bubble, target)); }
function stopSelect() {
    selecting = false;
    selectedMsgs.clear();
    if (selWatch) { selWatch.disconnect(); selWatch = null; }
    const root = document.getElementById('kovChat');
    if (root) root.classList.remove('selecting');
    paintSelection();
}
// касание после долгого нажатия гасится; в режиме выбора касание выбирает — не открывает файл и не меню
document.addEventListener('click', e => {
    let b = e.target && e.target.closest && e.target.closest('#messagesList .msg-bubble');
    if (!b && selecting && e.target.closest) {   // пустое место строки сообщения — как кружок
        const row = e.target.closest('#messagesList .sel-row');
        const rb = row && row.querySelector(':scope > .msg-bubble');
        if (rb && rb.__msg && !e.target.closest('.sel-circle')) { e.preventDefault(); e.stopPropagation(); toggleWhole(rb.__msg, rb.__isMine); }
        return;
    }
    if (!b || !b.__msg) return;
    if (b.__lpAt && Date.now() - b.__lpAt < 800) { b.__lpAt = 0; e.preventDefault(); e.stopPropagation(); return; }
    if (!selecting) return;
    e.preventDefault(); e.stopPropagation();
    toggleSelect(b.__msg, b.__isMine, tileFileId(b, e.target));
}, true);
document.addEventListener('keydown', e => { if (selecting && e.key === 'Escape') stopSelect(); });

/** Пункт меню сообщения: значок приложения и подпись (эмодзи выглядели по-разному
 *  на разных телефонах, а экранный диктор читал их названия перед каждым пунктом). */
function menuItem(glyph, label, onClick, danger) {
    const item = document.createElement('div');
    item.className = 'context-menu-item' + (danger ? ' danger' : '');
    item.setAttribute('role', 'menuitem');
    item.tabIndex = -1;
    const svg = window.Kov && Kov.Icons && Kov.Icons.svg ? Kov.Icons.svg(glyph) : '';
    if (svg) item.insertAdjacentHTML('beforeend', svg.replace('<svg', '<svg aria-hidden="true" focusable="false"'));
    const text = document.createElement('span');
    text.textContent = label;
    item.appendChild(text);
    item.onclick = onClick;
    return item;
}

/** Клавиатура в меню: стрелки, Home/End — по пунктам, Enter и пробел — выбрать, Esc и Tab — закрыть. */
function menuKeyboard(menu, opener) {
    const stops = () => [...menu.querySelectorAll('.reaction-picker-btn, [role="menuitem"]')]
        .filter(el => el.offsetParent !== null);
    menu.addEventListener('keydown', (e) => {
        const list = stops();
        let i = list.indexOf(document.activeElement);
        if (['ArrowDown', 'ArrowUp', 'ArrowRight', 'ArrowLeft', 'Home', 'End'].includes(e.key) && list.length) {
            e.preventDefault();
            const step = (e.key === 'ArrowDown' || e.key === 'ArrowRight') ? 1 : -1;
            i = e.key === 'Home' ? 0 : e.key === 'End' ? list.length - 1 : (i + step + list.length) % list.length;
            list[i].focus({ preventScroll: true });
        } else if ((e.key === 'Enter' || e.key === ' ') && document.activeElement && document.activeElement.getAttribute('role') === 'menuitem') {
            e.preventDefault();
            document.activeElement.click();
        } else if (e.key === 'Escape' || e.key === 'Tab') {
            e.preventDefault();
            closeContextMenu();
            if (opener && opener.isConnected && opener.focus) opener.focus({ preventScroll: true });
        }
    });
}

function showContextMenu(x, y, msg, isMine, clickedFile = null, viaKeyboard = false) {
    if (selecting) { toggleSelect(msg, isMine, clickedFile ? fileIdOf(clickedFile) : null); return; }   // в режиме выбора — тоже выбор
    closeContextMenu();
    const opener = document.activeElement;
    
    const menu = document.createElement('div');
    menu.className = 'context-menu';
    menu.setAttribute('role', 'menu');
    menu.setAttribute('aria-label', 'Действия с сообщением');
    menu.style.left = `${x}px`;
    menu.style.top = `${y}px`;
    menu.dataset.anchorX = String(x);
    menu.dataset.anchorY = String(y);
    // Положение уточняется после вставки — до этого меню не показываем
    menu.style.visibility = 'hidden';
    requestAnimationFrame(() => keepMenuOnScreen(menu));
    
    // Ряд быстрых реакций: 4 эмодзи + кнопка раскрытия полного списка
    const reactionsDiv = document.createElement('div');
    reactionsDiv.className = 'reaction-picker';

    const applyReaction = (type) => {
        if (backend) backend.send_reaction(String(msg.id), type);
        closeContextMenu();
    };

    const makeEmojiBtn = (type) => {
        const btn = document.createElement('button');
        btn.className = 'reaction-picker-btn';
        btn.innerText = reactionEmoji(type);
        btn.title = REACTION_TITLES[type] || type;
        btn.onclick = () => applyReaction(type);
        return btn;
    };

    QUICK_REACTIONS.forEach(type => reactionsDiv.appendChild(makeEmojiBtn(type)));

    // Пятая кнопка — раскрывает расширенный список (в него входят и быстрые)
    const expandBtn = document.createElement('button');
    expandBtn.className = 'reaction-picker-btn reaction-expand-btn';
    expandBtn.title = 'Ещё реакции';
    expandBtn.innerHTML = '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">' +
        '<path d="M6 9.5 L12 15.5 L18 9.5" fill="none" stroke="currentColor" ' +
        'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    reactionsDiv.appendChild(expandBtn);
    menu.appendChild(reactionsDiv);

    // Расширенный список: только те, которых нет в быстром ряду —
    // раньше первые четыре показывались дважды
    const extraDiv = document.createElement('div');
    extraDiv.className = 'reaction-picker-extra';
    Object.keys(REACTION_EMOJIS)
        .filter(type => !QUICK_REACTIONS.includes(type))
        .forEach(type => extraDiv.appendChild(makeEmojiBtn(type)));
    menu.appendChild(extraDiv);

    expandBtn.onclick = (e) => {
        e.stopPropagation();
        const opened = extraDiv.classList.toggle('open');
        expandBtn.classList.toggle('expanded', opened);
        // Верхний край меню остаётся на месте; если раскрытый список не
        // помещается снизу, меню только ужимается — не перескакивает вверх
        keepMenuOnScreen(menu, { keepTop: true });
    };

    const sep = document.createElement('div');
    sep.className = 'context-menu-separator';
    menu.appendChild(sep);
    
    // Копировать. Стикер и GIF копировать нечего: в тексте лежит пометка
    if (msg.text && !isMarkedMessage(msg)) {
        menu.appendChild(menuItem('copy', 'Копировать', () => {
            if (backend) backend.copy_text(msg.text);
            closeContextMenu();
        }));
    }
    
    // Ответить
    menu.appendChild(menuItem('undo', 'Ответить', () => {
        prepareReply(String(msg.id), previewText(msg.text || '') || (msg.files ? "Файл" : ""));
        closeContextMenu();
    }));

    // Выбрать: несколько сообщений — переслать или удалить разом
    const selItem = menuItem('check', 'Выбрать', () => { closeContextMenu(); startSelect(msg, isMine, clickedFile ? fileIdOf(clickedFile) : null); });
    if (!coarsePointer()) menu.appendChild(selItem);   // телефон: выбор — долгим нажатием

    // Переслать в другой чат.
    // Служебные маркеры ("вышел из группы" и т.п.) пересылать нечего —
    // сервер такие запросы всё равно отклонит.
    if (!isServiceMessage(msg)) {
        // меню открыто на файле группы — пересылается только этот файл
        const oneFile = clickedFile && (msg.files || []).length > 1 && (clickedFile.id || clickedFile.message_id);
        const forwardItem = menuItem('send', oneFile ? 'Переслать файл' : 'Переслать', null);
        forwardItem.onclick = () => {
            if (backend && oneFile) {
                backend.forward_message(String(oneFile), '📎 ' + (clickedFile.file_name || clickedFile.filename || clickedFile.name || 'Файл'));
                closeContextMenu();
                return;
            }
            if (backend) {
                let preview = previewText(msg.text || '');
                if (!preview) {
                    const count = (msg.files || []).length;
                    preview = count > 1 ? `📎 Файлов: ${count}` : '📎 Файл';
                }
                if (preview.length > 120) preview = preview.substring(0, 120) + '...';
                backend.forward_message(String(msg.id), preview);
            }
            closeContextMenu();
        };
        menu.appendChild(forwardItem);
    }
    
    if (clickedFile) {
        const downloadItem = menuItem('download', 'Скачать файл', null);
        downloadItem.onclick = () => {
            if (backend) {
                backend.download_file(String(msg.id), JSON.stringify(clickedFile));
            }
            closeContextMenu();
        };
        menu.appendChild(downloadItem);
    } else if (msg.files && msg.files.length > 0) {
        const downloadItem = menuItem('download', 'Скачать все файлы', null);
        downloadItem.onclick = () => {
            if (backend) {
                msg.files.forEach(f => {
                    backend.download_file(String(msg.id), JSON.stringify(f));
                });
            }
            closeContextMenu();
        };
        menu.appendChild(downloadItem);
    } else if (msg.file_path || msg.url) {
        const downloadItem = menuItem('download', 'Скачать файл', null);
        downloadItem.onclick = () => {
            if (backend) backend.download_file(String(msg.id), JSON.stringify(msg));
            closeContextMenu();
        };
        menu.appendChild(downloadItem);
    }

    if (isMine) {
        menu.appendChild(sep.cloneNode());
        
        // Редактировать.
        // У стикера и GIF «текст» — служебная пометка вида [[sticker:cat/happy]].
        // Правка превращала объект в обычное сообщение с этой пометкой, поэтому
        // такие сообщения только удаляются.
        if (msg.text && !getForwardedFrom(msg) && !isMarkedMessage(msg)) {
            menu.appendChild(menuItem('edit', 'Редактировать', () => {
                startEdit(String(msg.id), msg.text);
                closeContextMenu();
            }));
        }
        
        // Удалить
        menu.appendChild(menuItem('trash', 'Удалить', () => {
            closeContextMenu();
            // Удаление необратимо — спрашиваем подтверждение
            if (backend) backend.confirm_delete_message(String(msg.id), previewText(msg.text || ''));
        }, true));
    }
    
    document.body.appendChild(menu);
    currentContextMenu = menu;
    
    keepMenuOnScreen(menu);
    menuKeyboard(menu, opener);
    // с клавиатуры — фокус на первый пункт, дальше стрелками. С мыши и пальца фокус
    // не трогаем: на телефоне он убрал бы экранную клавиатуру, а с ней и меню (resize)
    if (viaKeyboard) {
        const first = menu.querySelector('[role="menuitem"]');
        if (first) first.focus({ preventScroll: true });
    }
}
// Esc закрывает меню, открытое мышью или пальцем (фокус остался в ленте или в поле ввода)
document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && currentContextMenu && !currentContextMenu.contains(document.activeElement)) closeContextMenu();
});

/**
 * Держит меню в границах окна. Координаты приходят от события (clientX/Y),
 * поэтому меню позиционируется fixed — при прокрученной странице absolute
 * давал сдвиг. Если меню выше экрана, оно раскрывается вверх, а при нехватке
 * места ужимается по высоте и получает свою прокрутку.
 */
function keepMenuOnScreen(menu, opts) {
    const pad = 8;
    const anchorX = Number(menu.dataset.anchorX || 0);
    const anchorY = Number(menu.dataset.anchorY || 0);
    const keepTop = !!(opts && opts.keepTop);
    const currentTop = parseFloat(menu.style.top) || anchorY;

    menu.style.maxHeight = '';
    menu.style.overflowY = '';
    let rect = menu.getBoundingClientRect();

    if (keepTop) {
        // положение уже выбрано — только следим, чтобы низ не ушёл за экран
        const room = window.innerHeight - pad - currentTop;
        if (rect.height > room) { menu.style.maxHeight = `${Math.max(120, room)}px`; menu.style.overflowY = 'auto'; }
        menu.style.top = `${currentTop}px`;
        menu.style.visibility = 'visible';
        return;
    }

    const maxH = window.innerHeight - pad * 2;
    if (rect.height > maxH) {
        menu.style.maxHeight = `${maxH}px`;
        menu.style.overflowY = 'auto';
        rect = menu.getBoundingClientRect();
    }

    let left = anchorX;
    let top = anchorY;
    if (left + rect.width > window.innerWidth - pad) left = window.innerWidth - rect.width - pad;
    if (left < pad) left = pad;
    if (top + rect.height > window.innerHeight - pad) {
        // сначала пробуем раскрыть вверх от точки нажатия
        top = anchorY - rect.height - 8;
        if (top < pad) top = Math.max(pad, window.innerHeight - rect.height - pad);
    }
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
    menu.style.visibility = 'visible';
}

function closeContextMenu() {
    if (currentContextMenu) {
        currentContextMenu.remove();
        currentContextMenu = null;
    }
}

document.addEventListener('click', (e) => {
    // нажатие внутри самого меню его не закрывает — иначе пункты не нажать
    if (e.target.closest && e.target.closest('.context-menu')) return;
    closeContextMenu();
});
document.addEventListener('scroll', closeContextMenu, true);
window.addEventListener('resize', closeContextMenu);

// === Ввод сообщений ===

messageInput.addEventListener('input', function() {
    // Анимация высоты
    const oldHeight = this.style.height || '40px';
    this.style.height = 'auto';
    const full = this.scrollHeight;
    const newHeight = Math.min(full, 120);
    this.style.height = oldHeight;
    // trigger reflow
    void this.offsetHeight;
    this.style.height = newHeight + 'px';
    // Полосу прокрутки показываем, только когда текст перерос поле: иначе
    // она висела справа даже у пустой строки
    this.style.overflowY = full > 120 ? 'auto' : 'hidden';
    
    // Счетчик символов
    const len = this.value.length;
    const limit = pendingFiles.length > 0 ? 500 : 4096;
    
    if (len > limit - 50 || pendingFiles.length > 0) {
        charCounter.style.display = 'block';
        const remain = limit - len;
        charCounter.innerText = remain >= 0 ? `Осталось: ${remain}` : `Превышено: ${Math.abs(remain)}`;
        
        charCounter.className = 'char-counter';
        if (remain < 0) charCounter.classList.add('error');
        else if (remain < 20) charCounter.classList.add('warning');
        
        if (len > (limit === 500 ? 500 : 8192)) {
            this.value = this.value.substring(0, limit === 500 ? 500 : 8192);
        }
    } else {
        charCounter.style.display = 'none';
    }
});

messageInput.addEventListener('keydown', function(e) {
    if (e.key === 'Enter') {
        if (e.shiftKey || e.ctrlKey) {
            // Перенос строки (дефолт)
        } else {
            e.preventDefault();
            sendMessage();
        }
    }
});

btnSend.onclick = sendMessage;

function sendMessage() {
    if (!activeChatId) return;
    
    const text = messageInput.value.trim();
    const limit = pendingFiles.length > 0 ? 500 : 4096;
    if (text.length > limit) {
        if (backend) backend.show_toast("Лимит символов в сообщении превышен");
        return;
    }
    
    if (editingMsgId) {
        if (text) {
            if (backend) backend.edit_message(editingMsgId, text);
        }
        // Раньше поле оставалось заполненным старым текстом, и следующее
        // сообщение уходило вместе с ним
        messageInput.value = '';
        messageInput.style.height = 'auto';
        charCounter.style.display = 'none';
        clearInfoPanel();
        return;
    }
    
    if (text || pendingFiles.length > 0) {
        // Пустая строка вместо null: иначе PyQt не приведёт тип
        let safeReplyId = replyToMsgId ? String(replyToMsgId) : ""; 
        
        if (pendingFiles.length > 0) {
            if (backend) backend.send_message_with_files(text, JSON.stringify(pendingFiles), safeReplyId);
            showTempUploadMessage(text, pendingFiles);
            pendingFiles = [];
            renderPendingFiles();
        } else {
            if (backend) backend.send_text_message(text, safeReplyId);
        }
        messageInput.value = '';
        messageInput.style.height = 'auto';
        charCounter.style.display = 'none';
        clearInfoPanel();
    }
}

function prepareReply(msgId, text) {
    clearInfoPanel();
    replyToMsgId = msgId;
    replyInfo.innerText = `Ответ: ${text.substring(0, 30)}...`;
    replyContainer.style.display = 'flex';
    infoPanel.style.display = 'flex';
    (window.Kov && Kov.UI && Kov.UI.softFocus ? Kov.UI.softFocus(messageInput) : messageInput.focus());
}

function startEdit(msgId, text) {
    clearInfoPanel();
    editingMsgId = msgId;
    messageInput.value = text;
    replyInfo.innerText = `Редактирование: ${text.substring(0, 30)}...`;
    replyContainer.style.display = 'flex';
    infoPanel.style.display = 'flex';
    (window.Kov && Kov.UI && Kov.UI.softFocus ? Kov.UI.softFocus(messageInput) : messageInput.focus());
}

/* Свёрнутый список чатов.
 *
 * На узком окне список в 350 точек забирает половину места, а нужен из него
 * обычно только выбор собеседника. В свёрнутом виде остаются кружки: видно,
 * кто есть и где непрочитанное, а переписке достаётся вся остальная ширина.
 * Выбор запоминается, чтобы не сворачивать заново при каждом входе.
 */
const RAIL_KEY = 'kov.chat.rail';
const chatRoot = document.getElementById('kovChat');

function setRail(on, remember) {
    if (!chatRoot) return;
    chatRoot.classList.toggle('rail', !!on);
    if (railBtn) {
        railBtn.classList.toggle('on', !!on);
        railBtn.title = on ? 'Развернуть список чатов' : 'Свернуть список чатов';
        railBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
    }
    if (remember) { try { localStorage.setItem(RAIL_KEY, on ? '1' : '0'); } catch (e) { /* приватный режим */ } }
}

function restoreRail() {
    let saved = null;
    try { saved = localStorage.getItem(RAIL_KEY); } catch (e) { /* приватный режим */ }
    if (saved !== null) return setRail(saved === '1', false);
    // Человек ещё не выбирал: на узком окне сворачиваем сами — там список
    // отнимает слишком много, — но выбор за ним не записываем
    setRail(window.innerWidth < 1000 && window.innerWidth > 760, false);
}

if (railBtn) {
    railBtn.addEventListener('click', () => {
        setRail(!chatRoot.classList.contains('rail'), true);
    });
}
restoreRail();

btnCloseReply.onclick = clearInfoPanel;

function clearInfoPanel() {
    replyToMsgId = null;
    editingMsgId = null;
    replyContainer.style.display = 'none';
    
    if (pendingFiles.length === 0) {
        infoPanel.style.display = 'none';
    }
}

// === Файлы ===

btnAttach.onclick = () => {
    if (backend) backend.open_file_dialog();
};

// Функция вызывается из Python
function addAttachedFiles(filesStr) {
    const files = JSON.parse(filesStr);
    
    files.forEach(f => {
        if (!pendingFiles.find(pf => pf.filepath === f.filepath)) {
            pendingFiles.push(f);
        }
    });
    
    renderPendingFiles();
}

// Сколько файлов уже приложено — нужно мосту, чтобы не превысить предел
function pendingCount() { return pendingFiles.length; }

function renderPendingFiles() {
    attachmentsContainer.innerHTML = '';

    if (pendingFiles.length > 0) {
        infoPanel.style.display = 'flex';
        // Раскладка задана в стилях сеткой в две строки; инлайновый flex
        // её перебивал, и все файлы выстраивались в одну строку
        attachmentsContainer.style.display = 'grid';

        // Лента прокручивается по горизонтали и не растягивает окно:
        // с десятью файлами она раньше уезжала за край экрана
        const counter = document.createElement('div');
        counter.className = 'attachments-count';
        const max = (window.backend && backend.max_files) ? backend.max_files() : 10;
        counter.textContent = pendingFiles.length + ' из ' + max;
        attachmentsContainer.appendChild(counter);
        
        pendingFiles.forEach((f, idx) => {
            const div = document.createElement('div');
            div.className = 'attachment-preview';
            div.title = f.filename || 'Файл';
            
            if (f.preview_url) {
                // Кадр из видео или картинка прямо из выбранного файла
                const isVideo = /\.(mp4|webm|mov|avi|mkv|m4v)$/i.test(f.filename || '');
                div.innerHTML = isVideo
                    ? `<video src="${escapeAttr(f.preview_url)}" poster="${escapeAttr(localPoster(f.preview_url))}" data-local-poster="1" muted preload="metadata" playsinline style="background: black"></video><span class="video-play small"><i></i></span>`
                    : `<img src="${escapeAttr(f.preview_url)}" alt="">`;
            } else if (f.is_image && f.preview_data) {
                div.innerHTML = `<img src="data:image/png;base64,${f.preview_data}">`;
            } else {
                let icon = '📄';
                const fname = (f.filename || '').toLowerCase();
                if (fname.endsWith('.mp4') || fname.endsWith('.avi') || fname.endsWith('.mov')) icon = '🎬';
                else if (fname.endsWith('.pdf')) icon = '📑';
                else if (fname.endsWith('.zip') || fname.endsWith('.rar') || fname.endsWith('.gz')) icon = '🗜️';
                else if (fname.endsWith('.doc') || fname.endsWith('.docx') || fname.endsWith('.xls') || fname.endsWith('.xlsx')) icon = '📝';
                
                div.innerHTML = `
                    <div style="display:flex; flex-direction:column; align-items:center; width:100%; padding:4px;">
                        <span style="font-size:24px; line-height:1;">${icon}</span>
                        <span class="file-name-span" style="font-size:9px; color:var(--col-text-secondary); width:100%; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; text-align:center; margin-top:2px;"></span>
                    </div>
                `;
                div.querySelector('.file-name-span').textContent = f.filename || 'Файл';
            }
            
            const remBtn = document.createElement('div');
            remBtn.className = 'attachment-remove';
            remBtn.innerText = '✕';
            remBtn.onclick = () => {
                pendingFiles.splice(idx, 1);
                renderPendingFiles();
                // Обновляем счетчик символов
                messageInput.dispatchEvent(new Event('input'));
            };
            
            div.appendChild(remBtn);
            attachmentsContainer.appendChild(div);
        });
    } else {
        attachmentsContainer.style.display = 'none';
        if (!replyToMsgId && !editingMsgId) {
            infoPanel.style.display = 'none';
        }
    }
    
    // Обновляем лимиты (500 символов с файлами)
    messageInput.dispatchEvent(new Event('input'));
}

function updateUploadProgress(percent) {
    // Hide old global progress bar completely
    if (uploadProgressContainer) {
        uploadProgressContainer.style.display = 'none';
    }
    
    // New logic for temp bubble
    if (percent === -2) {
        if (typeof removeTempUploadMessage === 'function') {
            removeTempUploadMessage();
        }
    } else if (percent >= 0) {
        if (tempUploadData) {
            tempUploadData.percent = percent;
        }
        if (percent >= 100) {
            document.querySelectorAll('#kovChat .temp-tile .temp-ring').forEach(ring => {
                const fillEl = ring.querySelector('.temp-ring-fill');
                if (fillEl) fillEl.style.setProperty('--p', '100%');
                const pct = ring.querySelector('.temp-pct');
                if (pct) pct.textContent = '100%';
                const note = ring.querySelector('.temp-note');
                if (note) note.textContent = 'Готово';
                ring.classList.add('done');
            });
        }

        
        if (percent >= 100) {
            // It will be removed when the actual message arrives
        }
    } else {
        if (typeof removeTempUploadMessage === 'function') {
            removeTempUploadMessage();
        }
    }
}



// === Скролл ===

var unseenBelow = 0;
var following = true;

/** Счётчик новых сообщений на кнопке «вниз» */
function setUnseenBelow(n) {
    unseenBelow = Math.max(0, n);
    let badge = scrollToBottomBtn.querySelector('.unseen-count');
    if (!badge) {
        badge = document.createElement('span');
        badge.className = 'unseen-count';
        scrollToBottomBtn.appendChild(badge);
    }
    badge.textContent = unseenBelow > 99 ? '99+' : String(unseenBelow);
    badge.hidden = !unseenBelow;
    scrollToBottomBtn.setAttribute('aria-label', unseenBelow
        ? 'К последним сообщениям, новых: ' + badge.textContent : 'К последним сообщениям');
    if (unseenBelow) scrollToBottomBtn.classList.add('visible');
}

messagesList.addEventListener('scroll', () => {
    const isAtBottom = messagesList.scrollHeight - messagesList.scrollTop <= messagesList.clientHeight + 100;
    // «у самого низа» — строго: чуть приподнял ленту — уже читает историю
    following = messagesList.scrollHeight - messagesList.scrollTop <= messagesList.clientHeight + 8;
    if (following) setUnseenBelow(0);
    if (isAtBottom && !unseenBelow) {
        scrollToBottomBtn.classList.remove('visible');
    } else {
        scrollToBottomBtn.classList.add('visible');
    }
    
    if (messagesList.scrollTop <= 50 && backend && chatMessages.length > 0 && !isLoadingHistory && hasOlderMessages) {
        // Подгрузка старых
        isLoadingHistory = true;
        const oldestMsgId = chatMessages[0].id;
        backend.load_older_history(String(activeChatId), String(oldestMsgId));
    }

    if (isAtBottom && backend && chatMessages.length > 0 && !isLoadingHistory && hasNewerMessages) {
        // Подгрузка новых
        isLoadingHistory = true;
        const newestMsgId = chatMessages[chatMessages.length - 1].id;
        backend.load_newer_history(String(activeChatId), String(newestMsgId));
    }
});

scrollToBottomBtn.onclick = () => { setUnseenBelow(0); following = true; scrollToBottom(); };

// ── клавиатура в переписке ──────────────────────────────────────────────
// Закрытая панель поиска не попадает в обход клавишей Tab: она закрывается из
// нескольких мест, поэтому отметка ставится по смене класса
if (searchSidebar) {
    searchSidebar.inert = !searchSidebar.classList.contains('open');
    new MutationObserver(() => { searchSidebar.inert = !searchSidebar.classList.contains('open'); })
        .observe(searchSidebar, { attributes: true, attributeFilter: ['class'] });
}

/* Лента — одна остановка Tab (последнее сообщение), дальше стрелки; Enter, пробел
   или клавиша меню открывают действия с сообщением. Аватары, фото, файлы и
   цитаты, которые открываются щелчком, становятся кнопками для клавиатуры. */
const KBD_TARGETS = '.msg-avatar.clickable, .msg-media div, .msg-media.media-file, .msg-reply, .reaction-chip';
let kbdQueued = false;
function enhanceMessagesKeyboard() {
    kbdQueued = false;
    const bubbles = [...messagesList.querySelectorAll('.msg-bubble')];
    const inside = messagesList.contains(document.activeElement);
    let stop = bubbles.find(b => b.tabIndex === 0 && b.hasAttribute('tabindex'));
    if (!inside || !stop) stop = bubbles[bubbles.length - 1];
    bubbles.forEach(b => { b.tabIndex = b === stop ? 0 : -1; });
    messagesList.querySelectorAll(KBD_TARGETS).forEach(el => {
        if (typeof el.onclick !== 'function' || el.hasAttribute('tabindex') || /^(BUTTON|A|INPUT)$/.test(el.tagName)) return;
        el.tabIndex = 0;
        el.setAttribute('role', 'button');
        if (el.hasAttribute('aria-label')) return;
        const img = el.querySelector('img[alt]');
        const fileName = el.querySelector('.media-file-name');
        const label = el.classList.contains('msg-avatar') ? 'Карточка сотрудника: ' + (el.title || '')
            : fileName ? 'Открыть файл ' + fileName.textContent
            : img && img.alt ? 'Открыть ' + img.alt
            : el.querySelector('video, img') ? 'Открыть вложение' : '';
        if (label) el.setAttribute('aria-label', label);
    });
}
new MutationObserver(() => {
    if (kbdQueued) return;
    kbdQueued = true;
    requestAnimationFrame(enhanceMessagesKeyboard);
}).observe(messagesList, { childList: true, subtree: true });

messagesList.addEventListener('keydown', (e) => {
    const t = e.target;
    if (t.classList && t.classList.contains('msg-bubble')) {
        if (['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(e.key)) {
            const all = [...messagesList.querySelectorAll('.msg-bubble')];
            let i = all.indexOf(t);
            i = e.key === 'Home' ? 0 : e.key === 'End' ? all.length - 1 : i + (e.key === 'ArrowDown' ? 1 : -1);
            const next = all[Math.max(0, Math.min(all.length - 1, i))];
            e.preventDefault();
            if (next && next !== t) { all.forEach(b => { b.tabIndex = -1; }); next.tabIndex = 0; next.focus(); }
            return;
        }
        if ((e.key === 'Enter' || e.key === ' ' || e.key === 'ContextMenu' || (e.key === 'F10' && e.shiftKey)) && t.__msg) {
            e.preventDefault();
            kbdMenuAt = Date.now();
            const r = t.getBoundingClientRect();
            showContextMenu(r.left + Math.min(r.width / 2, 40), r.top + Math.min(r.height / 2, 24), t.__msg, t.__isMine, null, true);
        }
        return;
    }
    if ((e.key === 'Enter' || e.key === ' ') && t.getAttribute && t.getAttribute('role') === 'button'
        && !/^(BUTTON|A|INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) {
        e.preventDefault();
        t.click();
    }
});

function scrollToBottom() {
    messagesList.scrollTop = messagesList.scrollHeight;
}

/* ── удержание ленты у нижнего края ───────────────────────────────────────
 * Одного scrollTop = scrollHeight мало: картинки и видео в последних
 * сообщениях к этому моменту ещё не загрузились, их высота неизвестна, и
 * лента останавливается выше настоящего низа. Поэтому после прокрутки мы
 * какое-то время держимся за нижний край: подтягиваем ленту при каждой
 * догрузившейся картинке и несколько раз по таймеру. Как только человек
 * сам крутит вверх — сразу отпускаем.
 */
let stickTimers = [];
let stickCleanup = null;

// До этого момента прилипание к низу не включается: идёт переход к сообщению
let jumpLockUntil = 0;

function stopStick() {
    stickTimers.forEach(clearTimeout);
    stickTimers = [];
    if (stickCleanup) { stickCleanup(); stickCleanup = null; }
}

function stickBottom(ms) {
    // Только что перешли к сообщению — вниз не тянем
    if (Date.now() < jumpLockUntil) return;
    stopStick();
    const until = Date.now() + (ms || 2000);
    let lastSet = -1;

    const jump = () => {
        if (Date.now() > until) return stopStick();
        messagesList.scrollTop = messagesList.scrollHeight;
        lastSet = messagesList.scrollTop;
        watchMedia();
    };
    const onScroll = () => {
        // Наши собственные прокрутки тоже поднимают событие, поэтому
        // сравниваем с последним выставленным значением
        if (lastSet >= 0 && Math.abs(messagesList.scrollTop - lastSet) > 60) stopStick();
    };
    const release = () => stopStick();

    function watchMedia() {
        messagesList.querySelectorAll('img, video').forEach(el => {
            if (el.dataset.stickWatched) return;
            el.dataset.stickWatched = '1';
            el.addEventListener('load', jump, { once: true });
            el.addEventListener('loadedmetadata', jump, { once: true });
            el.addEventListener('error', jump, { once: true });
        });
    }

    messagesList.addEventListener('scroll', onScroll, { passive: true });
    messagesList.addEventListener('wheel', release, { passive: true });
    messagesList.addEventListener('touchstart', release, { passive: true });
    stickCleanup = () => {
        messagesList.removeEventListener('scroll', onScroll);
        messagesList.removeEventListener('wheel', release);
        messagesList.removeEventListener('touchstart', release);
    };

    jump();
    [60, 150, 320, 650, 1100, 1800, 2600].forEach(delay => {
        if (delay <= (ms || 2000)) stickTimers.push(setTimeout(jump, delay));
    });
}

/** Была ли лента у нижнего края перед перерисовкой */
function atBottom(slack) {
    return messagesList.scrollHeight - messagesList.scrollTop <= messagesList.clientHeight + (slack || 50);
}

/**
 * Перерисовка без прыжка: если человек был внизу — там и останется, если
 * читал середину переписки — останется на том же месте, даже когда высота
 * сообщений изменилась (так бывает при замене временного пузыря настоящим).
 */
function renderKeepingPlace(atEnd) {
    const wasBottom = atEnd === undefined ? atBottom() : atEnd;
    const fromBottom = messagesList.scrollHeight - messagesList.scrollTop;
    renderMessages(chatMessages);
    if (wasBottom) stickBottom(1800);
    else messagesList.scrollTop = messagesList.scrollHeight - fromBottom;
}


// === Прогресс скачивания поверх объекта файла ===

/**
 * Ключ элемента файла. Совпадает с ChatWidget._download_key:
 * "<id сообщения>|<путь файла>". Путь нужен потому, что у сгруппированного
 * сообщения id один на все вложения.
 */
function downloadKey(msgId, file) {
    const f = file || {};
    const path = f.file_path || f.filepath || f.url || '';
    return `${msgId == null ? '' : msgId}|${path}`;
}

/** Помечает элемент файла ключом, чтобы найти его при обновлении прогресса. */
function markDownloadTarget(el, msgId, file) {
    if (!el) return el;
    el.dataset.downloadKey = downloadKey(msgId, file);
    if (getComputedStyle(el).position === 'static') el.style.position = 'relative';
    return el;
}

/**
 * Рисует полосу прогресса поверх самого файла — без отдельного окна.
 * percent: 0..100, -1 — размер неизвестен либо ошибка.
 */
function updateDownloadProgress(key, percent) {
    const targets = document.querySelectorAll(`[data-download-key="${CSS.escape(key)}"]`);
    targets.forEach(el => {
        let overlay = el.querySelector('.download-overlay');

        if (percent >= 100 || percent < 0) {
            if (overlay) overlay.remove();
            return;
        }

        if (!overlay) {
            overlay = document.createElement('div');
            overlay.className = 'download-overlay';
            overlay.innerHTML = `
                <div class="download-overlay-track"><div class="download-overlay-fill"></div></div>
                <div class="download-overlay-text">0%</div>`;
            if (getComputedStyle(el).position === 'static') el.style.position = 'relative';
            el.appendChild(overlay);
        }

        const fill = overlay.querySelector('.download-overlay-fill');
        const text = overlay.querySelector('.download-overlay-text');
        if (fill) fill.style.width = `${percent}%`;
        if (text) text.innerText = `${percent}%`;
    });
}

/** Открытие чата по команде из Python (создание чата, переход к сообщению). */
function openChatFromBackend(payloadStr) {
    try {
        const data = JSON.parse(payloadStr);
        // openChat выходит сразу, если чат уже активен, — сбрасываем метку
        if (String(activeChatId) === String(data.id)) activeChatId = null;
        openChat(data.id, data.name, !!data.is_group, data.avatar_path || null,
                 null, data.last_name || '', data.first_name || '');
    } catch (e) {
        console.error('openChatFromBackend: ' + e);
    }
}

/* Переход к сообщению.
 *
 * Прилипание к низу ленты — stickBottom — держит прокрутку внизу и после
 * каждой загрузившейся картинки возвращает её туда снова. Плавная прокрутка
 * к сообщению с ним не уживалась: картинки вокруг цели догружались, и лента
 * улетала вниз, не доехав. Поэтому прилипание останавливаем, прокручиваем
 * сразу, а не плавно, и на две секунды запрещаем прилипанию включаться.
 */
function scrollToMessage(msgId, groupId) {
    stopStick();
    jumpLockUntil = Date.now() + 2000;
    let el = document.getElementById(`msg-${msgId}`);
    if (!el && groupId) {
        el = document.querySelector(`.msg-wrapper[data-group-id="${groupId}"]`);
    }
    if (!el) {
        // Последняя попытка: сообщение могло отрисоваться как часть группы,
        // у которой собственный id отличается от id вложения
        el = document.querySelector(`[data-child-ids~="${msgId}"]`)
             || document.querySelector(`.msg-wrapper[data-group-id="${msgId}"]`);
    }

    if (el) {
        el.scrollIntoView({ behavior: 'auto', block: 'center' });

        // Ищем баббл для подсветки
        const bubble = el.querySelector('.msg-bubble');
        if (bubble) {
            const originalColor = bubble.style.backgroundColor;
            bubble.style.transition = 'none';
            bubble.style.backgroundColor = 'var(--col-selected)'; // подсветка найденного сообщения

            // Триггерим reflow чтобы переход сработал
            void bubble.offsetWidth;

            bubble.style.transition = 'background-color 1.5s ease-out';
            bubble.style.backgroundColor = originalColor || '';
        }
    }
}

// === Поиск в чате (Боковая панель) ===

btnSearchChat.onclick = (e) => {
    e.stopPropagation();
    searchSidebar.classList.toggle('open');
    searchSidebar.inert = !searchSidebar.classList.contains('open');
    if (searchSidebar.classList.contains('open')) {
        (window.Kov && Kov.UI && Kov.UI.softFocus ? Kov.UI.softFocus(messageSearchInput) : messageSearchInput.focus());
        btnSearchChat.classList.add('active');
    } else {
        btnSearchChat.classList.remove('active');
    }
};

btnCloseSearchSidebar.onclick = () => {
    searchSidebar.classList.remove('open');
    btnSearchChat.classList.remove('active');
};

let searchTimer = null;
messageSearchInput.addEventListener('input', (e) => {
    clearTimeout(searchTimer);
    const text = e.target.value.trim();
    if (text && activeChatId) {
        searchResultsList.innerHTML = '<div style="text-align:center; color:gray; padding:10px;">Поиск...</div>';
        searchTimer = setTimeout(() => {
            if (backend) backend.search_messages(text);
        }, 800);
    } else {
        searchResultsList.innerHTML = '';
    }
});

function renderSearchResults(resultsStr) {
    const results = JSON.parse(resultsStr);
    searchResultsList.innerHTML = '';
    
    if (results.length === 0) {
        searchResultsList.innerHTML = '<div style="text-align:center; color:gray; padding:10px;">Ничего не найдено</div>';
        return;
    }
    
    results.forEach(msg => {
        if (!msg.text) return;
        // Маркеры отсеиваются и на стороне Python; здесь — страховка
        if (isServiceMessage(msg) || isServiceMessageText(msg.text)) return;
        const item = document.createElement('div');
        item.className = 'search-result-item';
        
        const label = previewText(msg.text);
        const shortText = label.length > 30 ? label.substring(0, 30) + '...' : label;
        
        item.innerHTML = `
            <div class="search-result-header">
                <span class="search-result-sender">${escapeAttr(msg.sender_name)}</span>
                <span class="search-result-time">${formatDateTime(msg.timestamp)}</span>
            </div>
            <div class="search-result-text">${escapeAttr(shortText)}</div>
        `;
        
        item.onclick = () => {
            // На телефоне панель поиска перекрывает переписку целиком:
            // закрываем её, иначе переход к сообщению не виден
            searchSidebar.classList.remove('open');
            if (btnSearchChat) btnSearchChat.classList.remove('active');
            if (backend) backend.go_to_message(String(msg.id), msg.group_id);
        };
        
        searchResultsList.appendChild(item);
    });
}

// === Предпросмотр медиафайлов ===
async function previewMedia(url, token) {
    url = (url || '').replace(/\.gz$/, '').replace(/\.(avi|mov|mkv|ts|wmv|flv)$/i, '.webm');
    
    const modal = document.createElement('div');
    modal.style.position = 'fixed';
    modal.style.top = 0;
    modal.style.left = 0;
    modal.style.width = '100%';
    modal.style.height = '100%';
    modal.style.backgroundColor = 'rgba(0,0,0,0.8)';
    modal.style.zIndex = 10000;
    modal.style.display = 'flex';
    modal.style.alignItems = 'center';
    modal.style.justifyContent = 'center';
    modal.style.cursor = 'pointer';

    const content = document.createElement('div');
    content.style.maxWidth = '90%';
    content.style.maxHeight = '90%';
    content.style.position = 'relative';

    const loader = document.createElement('div');
    loader.innerText = 'Загрузка...';
    loader.style.color = 'white';
    loader.style.fontSize = '20px';
    content.appendChild(loader);

    modal.appendChild(content);
    modal.onclick = () => {
        modal.remove();
    };
    document.body.appendChild(modal);

    try {
        const headers = new Headers();
        if (token) {
            headers.append('Authorization', `Bearer ${token}`);
        }

console.log("previewMedia: Fetching URL: " + url);
        const response = await fetch(url, { headers });
        console.log("previewMedia: Fetch response status: " + response.status);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const arrayBuffer = await response.arrayBuffer();
        console.log("previewMedia: Fetched " + arrayBuffer.byteLength + " bytes");

        let blob;
        // Check for GZIP magic bytes (1f 8b)
        const bytes = new Uint8Array(arrayBuffer);
        const isGzip = bytes.length >= 2 && bytes[0] === 0x1f && bytes[1] === 0x8b;
        console.log("previewMedia: isGzip (magic bytes): " + isGzip);

        if (isGzip && typeof DecompressionStream !== 'undefined') {
            try {
                console.log("previewMedia: Attempting to decompress GZIP stream...");
                const ds = new DecompressionStream("gzip");
                const writer = ds.writable.getWriter();
                writer.write(arrayBuffer);
                writer.close();
                const decompressedResponse = new Response(ds.readable);
                blob = await decompressedResponse.blob();
                console.log("previewMedia: Decompression successful, blob size: " + blob.size);
            } catch(e) {
                console.error("previewMedia: Decompression error", e);
                blob = new Blob([arrayBuffer]); // Fallback
            }
        } else {
            console.log("previewMedia: Using original buffer (not GZIP or no DecompressionStream)");
            blob = new Blob([arrayBuffer]);
        }
        
        const objectUrl = URL.createObjectURL(blob);
        console.log("previewMedia: Object URL created: " + objectUrl);

        content.removeChild(loader);

        // Remove .gz extension to detect type
        let checkUrl = url.toLowerCase();
        if (checkUrl.endsWith('.gz')) {
            checkUrl = checkUrl.slice(0, -3);
        }

        const isImage = /\.(jpg|jpeg|png|gif|bmp|webp|heic)$/i.test(checkUrl);
        const isVideo = /\.(mp4|avi|mov|mkv|webm|ts)$/i.test(checkUrl);

        if (isImage) {
            const img = document.createElement('img');
            img.src = objectUrl;
            img.style.maxWidth = '100%';
            img.style.maxHeight = '100%';
            content.appendChild(img);
        } else if (isVideo) {
            const video = document.createElement('video');
            video.src = objectUrl;
            video.controls = true;
            video.style.maxWidth = '100%';
            video.style.maxHeight = '100%';
            content.appendChild(video);
            video.play();
        } else {
            content.innerText = 'Невозможно отобразить файл данного типа';
            content.style.color = 'white';
        }

        modal.onclick = (e) => {
            if (e.target === modal || e.target === content) {
                modal.remove();
                URL.revokeObjectURL(objectUrl);
            }
        };

    } catch (error) {
        console.error("Ошибка загрузки медиа:", error);
        content.removeChild(loader);
        const errDiv = document.createElement('div');
        errDiv.innerText = 'Ошибка загрузки медиа';
        errDiv.style.color = 'var(--critical, #E53935)';
        errDiv.style.backgroundColor = 'var(--col-panel, #fff)';
        errDiv.style.padding = '20px';
        errDiv.style.borderRadius = '5px';
        content.appendChild(errDiv);
    }
}


// === Drag and Drop ===
let dragCounter = 0;

/** Тянут файлы, а не выделенный текст или ссылку */
function dragHasFiles(e) {
    const dt = e.dataTransfer;
    if (!dt) return false;
    if (dt.types && Array.from(dt.types).includes('Files')) return true;
    return !!(dt.items && Array.from(dt.items).some(i => i.kind === 'file'));
}

/** Поверх чата открыто окно приложения (например, отправка файлов) */
function appDialogOpen() {
    return !!document.querySelector('.modal-root');
}

// Перетаскивание изнутри страницы (стикер, GIF, картинка из ленты) — не вложение:
// раньше такой перенос давал пустое сообщение. Принимаются только файлы извне.
let internalDrag = false;
document.addEventListener('dragstart', () => { internalDrag = true; }, true);
document.addEventListener('dragend', () => { internalDrag = false; }, true);
document.addEventListener('drop', (e) => {
    if (!internalDrag) return;
    internalDrag = false;
    e.preventDefault();             // и в поле ввода ничего не вставится
    e.stopPropagation();            // окно отправки файлов тоже не откроется
}, true);

document.addEventListener('dragenter', (e) => {
    if (internalDrag) return;
    if (!dragHasFiles(e)) return;   // перетаскивание текста подсветку не включает
    e.preventDefault();
    if (!activeChatId) return; // Не показываем, если чат не выбран
    // Окно отправки файлов принимает перенос само и подсвечивает себя:
    // синяя зона чата под ним только мешала
    if (appDialogOpen()) { dragCounter = 0; document.body.classList.remove('drag-active'); return; }
    dragCounter++;
    if (dragCounter === 1) {
        document.body.classList.add('drag-active');
    }
});

document.addEventListener('dragleave', (e) => {
    if (!dragHasFiles(e)) return;
    e.preventDefault();
    if (!activeChatId) return;
    if (appDialogOpen()) { dragCounter = 0; document.body.classList.remove('drag-active'); return; }
    dragCounter--;
    if (dragCounter === 0) {
        document.body.classList.remove('drag-active');
    }
});

document.addEventListener('dragover', (e) => {
    if (internalDrag) return;
    if (!dragHasFiles(e)) return;
    e.preventDefault(); // Необходимо для разрешения drop и предотвращения дефолтного поведения
});

document.addEventListener('drop', (e) => {
    if (!dragHasFiles(e)) return;
    e.preventDefault(); // Предотвращаем открытие файла в WebView на весь экран
    dragCounter = 0;
    document.body.classList.remove('drag-active');
    // Обработка самого файла происходит в Python (QWebEngineView::dropEvent)
});

/* Карточка чата открывается нажатием по шапке.
 *
 * Раньше это был атрибут onclick прямо в разметке. Политика содержимого
 * сервера разрешает только script-src 'self', и такие обработчики браузер
 * молча не выполняет — нажатие по шапке ничего не делало ни на компьютере,
 * ни на телефоне.
 *
 * Ловим нажатие на уровне страницы, а не на самом узле: разметка чата
 * вставляется в документ отдельно от загрузки этого файла, и навешивать
 * обработчик на узел значило бы зависеть от того, что успело раньше.
 */
document.addEventListener('click', e => {
    const info = e.target.closest && e.target.closest('.chat-header-info-clickable');
    if (info) showChatInfoCard();
});
document.addEventListener('keydown', e => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const info = e.target.closest && e.target.closest('.chat-header-info-clickable');
    if (info) { e.preventDefault(); showChatInfoCard(); }
});

function showChatInfoCard() {
    if (!activeChatId) return;
    if (backend) {
        backend.request_chat_info_card(String(activeChatId));
    }
}

// Вызывается из Python, например после удаления группы
function clearChat() {
    activeChatId = null;
    activeChatIsGroup = false;
    setHeaderSub('');
    activeChatAvatarPath = null;
    chatMessages = [];
    loadedMessageIds.clear();
    messagesList.innerHTML = '';
    pendingFiles = [];
    tempUploadData = null;
    renderPendingFiles();
    clearInfoPanel();

    chatArea.style.display = 'none';
    placeholder.style.display = 'flex';
    searchSidebar.classList.remove('open');
    if (btnSearchChat) {
        btnSearchChat.style.display = 'none';
        btnSearchChat.classList.remove('active');
    }
}
