/* Определение режима уведомлений. Считается один раз при загрузке страницы.
 *
 *   inpage — только внутри открытой вкладки: всплывашки, звук, счётчик во вкладке.
 *            Так работает всё при HTTP (браузер не даёт ни Notification API,
 *            ни service worker с незащищённого адреса).
 *   os     — HTTPS, есть Notification API, но нет push (например, iOS без
 *            установки на экран «Домой»): при свёрнутой вкладке уведомление
 *            системы показывает сама страница.
 *   push   — HTTPS + service worker + PushManager: уведомления приходят
 *            даже при закрытом браузере.
 */
window.Kov = window.Kov || {};

Kov.NotifyMode = (function () {
  const ua = navigator.userAgent || '';
  const isIOS = /iPad|iPhone|iPod/.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const standalone = window.matchMedia && window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
  // file:// браузер тоже считает «защищённым», но service worker там не работает —
  // для наших целей это тот же HTTP
  const isFile = location.protocol === 'file:';
  const secure = window.isSecureContext === true && !isFile;
  const notificationsApi = secure && 'Notification' in window;
  const serviceWorker = secure && 'serviceWorker' in navigator;
  const pushApi = serviceWorker && 'PushManager' in window;
  // На iOS push даёт только сайт, добавленный на экран «Домой»
  const iosNeedsInstall = isIOS && !standalone;
  const push = pushApi && !iosNeedsInstall;

  let level = 'inpage';
  if (push) level = 'push';
  else if (notificationsApi) level = 'os';

  function describe() {
    if (isFile) return 'Страница открыта с диска: режим как при HTTP — уведомления только пока сайт открыт.';
    if (!secure) return 'HTTP-соединение: уведомления работают только пока сайт открыт. Для уведомлений на телефон нужен HTTPS.';
    if (level === 'push') return 'HTTPS: уведомления приходят даже при закрытом браузере.';
    if (iosNeedsInstall) return 'iPhone/iPad: добавьте сайт на экран «Домой» (Поделиться → На экран «Домой»), тогда уведомления будут приходить и при закрытом браузере.';
    return 'Уведомления системы показываются, пока вкладка открыта (хотя бы в фоне).';
  }

  return { secure, isFile, notificationsApi, serviceWorker, pushApi, push, isIOS, standalone, iosNeedsInstall, level, describe };
})();
