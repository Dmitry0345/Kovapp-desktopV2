/* Service worker веб-версии «Ковчег».
 * Задачи: принимать push, показывать системные уведомления, открывать нужный
 * раздел по клику. Кэширования страниц нет намеренно: приложение всегда берёт
 * свежую версию с сервера, а офлайн-режим ему не нужен.
 * Регистрируется только по HTTPS (ограничение браузеров).
 */
const SW_VERSION = 'kovapp-sw-beta3-v1';
// Safari (iPhone с сайтом на экране «Домой», Mac): push без системного уведомления считается «тихим»,
// и после нескольких таких подписку снимают — поэтому уведомление показывается всегда
const APPLE = /Safari\//.test(self.navigator.userAgent) && !/Chrome|Chromium|CriOS|Edg\/|Android/.test(self.navigator.userAgent);

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));

// Пустой обработчик fetch нужен, чтобы браузер считал сайт устанавливаемым
// («Добавить на главный экран»). Запросы уходят в сеть как обычно.
self.addEventListener('fetch', () => {});

function absoluteUrl(url) {
  try { return new URL(url || '', self.registration.scope).href; } catch (e) { return self.registration.scope; }
}

async function handlePush(payload) {
  const all = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
  // Активная вкладка сама покажет всплывашку — системное уведомление лишнее
  const focused = all.find(c => c.focused || c.visibilityState === 'visible');
  const title = payload.title || 'Ковчег';
  const tag = payload.tag || 'kovcheg';
  if (focused) {
    focused.postMessage({ type: 'push', payload });
    if (!APPLE) return;
    // Safari: показ обязателен; вкладка уже показала всплывашку, системное убираем сразу
    await self.registration.showNotification(title, { body: payload.body || '', tag, silent: true, data: { url: payload.url || '' } });
    (await self.registration.getNotifications({ tag })).forEach(n => n.close());
    return;
  }
  await self.registration.showNotification(title, {
    body: payload.body || '',
    tag,                       // та же метка, что у уведомлений страницы: дубликаты схлопываются
    renotify: false,
    icon: absoluteUrl('icons/kovapp-192.png'),
    badge: absoluteUrl('icons/kovapp-badge-96.png'),
    lang: 'ru',
    data: { url: payload.url || '', kind: payload.kind || '' },
  });
}

self.addEventListener('push', event => {
  let payload = {};
  try { payload = event.data ? event.data.json() : {}; } catch (e) { payload = { body: event.data ? event.data.text() : '' }; }
  event.waitUntil(Promise.all([handlePush(payload), confirmProbe(payload)]));
});

// контрольное уведомление: сообщаем серверу, что устройство его получило
async function confirmProbe(payload) {
  if (!payload || !payload.probe) return;
  try {
    const sub = await self.registration.pushManager.getSubscription();
    await fetch(new URL('push/ack', self.registration.scope.replace(/app\/?$/, '')).href, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ probe: payload.probe, endpoint: sub ? sub.endpoint : '' }),
    });
  } catch (e) { /* не страшно: проверка покажет, что подтверждения нет */ }
}

async function openOrFocus(url) {
  const target = absoluteUrl(url);
  const all = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
  // Уже открытая вкладка приложения: переключаем раздел внутри неё
  for (const client of all) {
    if (client.url.startsWith(self.registration.scope) && 'focus' in client) {
      await client.focus();
      client.postMessage({ type: 'navigate', url: url || '' });
      return;
    }
  }
  if (self.clients.openWindow) await self.clients.openWindow(target);
}

self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil(openOrFocus((event.notification.data || {}).url));
});

// Push-сервис сменил адрес подписки: подписываемся заново с тем же ключом,
// а страница при следующем входе отправит новую подписку серверу (PushClient.sync)
self.addEventListener('pushsubscriptionchange', event => {
  const old = event.oldSubscription;
  const key = old && old.options ? old.options.applicationServerKey : null;
  if (!key) return;
  event.waitUntil(self.registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key }).catch(() => null));
});

self.addEventListener('message', event => {
  if (event.data && event.data.type === 'version') event.source.postMessage({ type: 'version', version: SW_VERSION });
});
