/* Подписка на Web Push: регистрация service worker, разрешение, подписка,
 * синхронизация с сервером (/push/config, /push/subscribe).
 *
 * При HTTP модуль безопасно бездействует: capability().push === false,
 * enable() возвращает {ok:false, reason}. Всё остальное приложение от него
 * не зависит.
 */
window.Kov = window.Kov || {};

Kov.PushClient = (function () {
  const state = { registration: null, swUrl: 'sw.js', serverEnabled: null, publicKey: '' };

  function base64UrlToUint8Array(base64Url) {
    const padding = '='.repeat((4 - base64Url.length % 4) % 4);
    const base64 = (base64Url + padding).replace(/-/g, '+').replace(/_/g, '/');
    const raw = atob(base64);
    return Uint8Array.from(raw, c => c.charCodeAt(0));
  }

  /** Регистрирует service worker (только HTTPS). Вызывать при старте приложения. */
  async function init(opts) {
    opts = opts || {};
    if (opts.swUrl) state.swUrl = opts.swUrl;
    if (!Kov.NotifyMode.serviceWorker) return null;
    try {
      state.registration = await navigator.serviceWorker.register(state.swUrl, { scope: opts.scope || './' });
      // Новая версия sw.js подхватывается сразу, без ожидания закрытия всех вкладок
      state.registration.update().catch(() => {});
      return state.registration;
    } catch (e) {
      console.warn('Service worker не зарегистрирован:', e);
      return null;
    }
  }

  async function serverConfig() {
    if (state.serverEnabled !== null) return { enabled: state.serverEnabled, public_key: state.publicKey };
    try {
      const cfg = await Kov.Api.get('push/config');
      state.serverEnabled = !!cfg.enabled;
      state.publicKey = cfg.public_key || '';
    } catch (e) {
      state.serverEnabled = false;
    }
    return { enabled: state.serverEnabled, public_key: state.publicKey };
  }

  function capability() {
    const m = Kov.NotifyMode;
    return {
      supported: m.push,
      permission: m.notificationsApi ? Notification.permission : 'unsupported',
      iosNeedsInstall: m.iosNeedsInstall,
      reason: m.push ? '' : m.describe(),
    };
  }

  /** Текущее состояние подписки в этом браузере. */
  async function status() {
    const cap = capability();
    const result = Object.assign({ subscribed: false, endpoint: '', serverEnabled: false }, cap);
    if (!cap.supported || !state.registration) return result;
    const cfg = await serverConfig();
    result.serverEnabled = cfg.enabled;
    try {
      const sub = await state.registration.pushManager.getSubscription();
      if (sub) { result.subscribed = true; result.endpoint = sub.endpoint; }
    } catch (e) { /* ignore */ }
    return result;
  }

  async function sendToServer(sub) {
    const json = sub.toJSON();
    await Kov.Api.post('push/subscribe', {
      json: { endpoint: json.endpoint, keys: { p256dh: json.keys.p256dh, auth: json.keys.auth }, user_agent: navigator.userAgent },
    });
  }

  async function ensurePermission() {
    if (!Kov.NotifyMode.notificationsApi) return 'unsupported';
    if (Notification.permission !== 'default') return Notification.permission;
    try { return await Notification.requestPermission(); } catch (e) { return Notification.permission; }
  }

  async function enable() {
    const mode = Kov.NotifyMode;
    if (!mode.notificationsApi) return { ok: false, push: false, reason: mode.describe() };
    const permission = await ensurePermission();
    if (permission !== 'granted') return { ok: false, push: false, reason: permission === 'denied' ? 'Уведомления запрещены в настройках браузера для этого сайта' : 'Разрешение не выдано' };
    if (!mode.push) return { ok: true, push: false, reason: mode.describe() };
    if (!state.registration) await init();
    if (!state.registration) return { ok: true, push: false, reason: 'Системные уведомления включены, пока сайт открыт' };
    const cfg = await serverConfig();
    if (!cfg.enabled) return { ok: true, push: false, reason: 'Системные уведомления включены, пока сайт открыт. Для уведомлений при закрытом браузере серверу нужны ключи VAPID' };
    try {
      let sub = await state.registration.pushManager.getSubscription();
      if (sub) {
        // Ключ сервера сменился — старую подписку нужно пересоздать
        const current = sub.options && sub.options.applicationServerKey
          ? btoa(String.fromCharCode.apply(null, new Uint8Array(sub.options.applicationServerKey))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
          : null;
        if (current && current !== cfg.public_key) { await sub.unsubscribe(); sub = null; }
      }
      if (!sub) {
        sub = await state.registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: base64UrlToUint8Array(cfg.public_key),
        });
      }
      await sendToServer(sub);
      return { ok: true, push: true };
    } catch (e) {
      return { ok: true, push: false, reason: 'Системные уведомления включены, но подписка на push не оформилась: ' + (e.message || e) };
    }
  }

  /** Отключить push в этом браузере. */
  async function disable() {
    if (!state.registration) return { ok: true };
    try {
      const sub = await state.registration.pushManager.getSubscription();
      if (sub) {
        try { await Kov.Api.del('push/subscribe', { json: { endpoint: sub.endpoint } }); } catch (e) { /* сервер недоступен — подписку всё равно снимаем */ }
        await sub.unsubscribe();
      }
      return { ok: true };
    } catch (e) {
      return { ok: false, reason: e.message || String(e) };
    }
  }

  /**
   * Синхронизация при каждом входе: если подписка есть и разрешение выдано —
   * заново сообщаем её серверу (подписки меняются, пользователи перелогиниваются
   * под другим аккаунтом в том же браузере, сервер чистит «мёртвые»).
   */
  async function sync() {
    const cap = capability();
    if (!cap.supported || cap.permission !== 'granted' || !state.registration) return false;
    const cfg = await serverConfig();
    if (!cfg.enabled) return false;
    try {
      const sub = await state.registration.pushManager.getSubscription();
      if (!sub) return false;
      await sendToServer(sub);
      return true;
    } catch (e) { return false; }
  }

  async function test() {
    return Kov.Api.post('push/test');
  }

  return {
    init, capability, status, enable, disable, sync, test, ensurePermission,
    get registration() { return state.registration; },
  };
})();
