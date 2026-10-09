/* Оболочка: верхняя панель (кнопка меню, название раздела, колокольчик),
 * выезжающее меню слева (270px, InOutCubic), правая панель (профиль/просмотр). */
window.Kov = window.Kov || {};
Kov.Views = Kov.Views || {};

Kov.Shell = (function () {
  const { h, icon, button } = Kov.UI;
  const els = {};
  let menuOpen = false;
  let panelOpen = false;
  let currentSection = null;

  const MENU = [
    ['notifications', 'Уведомления', 'bell'],
    ['tasks', 'Задачи', 'flag'],
    ['projects', 'Проекты', 'project'],
    ['organizations', 'Организации', 'building'],
    ['equipment', 'Оборудование', 'archive'],
    ['employees', 'Работники', 'users'],
    ['csv', 'Файлы CSV', 'table', 'management'],
    ['profile', 'Профиль', 'id_card'],
    ['settings', 'Настройки', 'settings'],
    ['messenger', 'Мессенджер', 'chat'],
  ];

  function build() {
    const app = h('div.app');
    els.title = h('div.title-chip', 'Kovapp');
    els.badge = h('span.badge', { hidden: true });
    // колокольчик, как у «Уведомлений» в меню: конверт принимали за сообщения чата
    els.bell = h('button.bell-btn', { type: 'button', title: 'Уведомления', 'aria-label': 'Уведомления', onclick: () => Kov.Router.go('#/notifications') }, icon('bell'), els.badge);
    els.menuBtn = h('button.menu-btn', { type: 'button', title: 'Меню', 'aria-label': 'Меню', onclick: () => toggleMenu() }, icon('menu'));
    els.topBar = h('div.top-bar', els.menuBtn, els.title, els.bell);
    els.content = h('div.content');
    els.scrim = h('div.scrim', { onclick: () => { closeMenu(); closePanel(); } });
    els.nav = h('nav');
    // В шапке меню — фирменный знак: он же служит подписью приложения
    els.menu = h('div.side-menu',
      // Ни крестика, ни логотипа: меню закрывается повторным нажатием на
      // кнопку, нажатием мимо него и клавишей Escape
      els.nav);
    els.panel = h('div.side-panel');
    els.panelLeft = h('div.side-panel.left');
    // Полоска связи висит поверх содержимого и не двигает разметку: любой
    // раздел под ней остаётся ровно на своём месте
    els.link = h('div.link-bar', { hidden: true });
    app.append(els.topBar, els.link, els.content, els.scrim, els.menu, els.panel, els.panelLeft);
    document.body.append(app);
    buildMenu();
    watchConnection();
    document.addEventListener('keydown', e => { if (e.key === 'Escape') { closeMenu(); closePanel(); } });
    return app;
  }

  function buildMenu() {
    Kov.UI.clear(els.nav);
    const me = Kov.Store.state.me;
    MENU.forEach(([key, label, glyph, access]) => {
      if (access === 'management' && !Kov.Store.isManagement(me)) return;
      // Недоступный раздел из меню убираем: пункт, ведущий на отказ,
      // раздражает сильнее, чем его отсутствие
      if (Kov.Rights && !Kov.Rights.canOpenSection(key)) return;
      // ссылка, а не кнопка: работает открытие в новой вкладке колесом мыши
      const item = h('a.item', { href: '#/' + key, dataset: { key }, onclick: e => { if (e.button === 0 && !e.ctrlKey && !e.metaKey) { e.preventDefault(); closeMenu(); Kov.Router.go('#/' + key); } } }, icon(glyph), label);
      if (key === 'messenger') { const count = h('span.count', { hidden: true }); item.append(count); els.chatCount = count; }
      els.nav.append(item);
    });
    markActive(currentSection);
  }

  function markActive(key) {
    currentSection = key;
    els.nav.querySelectorAll('.item').forEach(i => i.classList.toggle('active', i.dataset.key === key));
  }

  // Меню и боковое окно профиля не должны быть открыты одновременно:
  // на телефоне окно профиля перекрывает весь экран
  function toggleMenu() { closePanel(); menuOpen ? closeMenu() : openMenu(); }
  function openMenu() { menuOpen = true; els.menu.classList.add('open'); els.scrim.classList.add('open'); }
  function closeMenu() { menuOpen = false; els.menu.classList.remove('open'); if (!panelOpen) els.scrim.classList.remove('open'); }

  /** side: 'right' (по умолчанию) или 'left' — профиль выезжает слева, как меню */
  function openPanel(contentEl, side) {
    const target = side === 'left' ? els.panelLeft : els.panel;
    const other = side === 'left' ? els.panel : els.panelLeft;
    other.classList.remove('open');
    Kov.UI.clear(target); target.append(contentEl);
    panelOpen = true; target.classList.add('open'); els.scrim.classList.add('open');
  }
  function closePanel() {
    if (!panelOpen) return;
    panelOpen = false;
    els.panel.classList.remove('open'); els.panelLeft.classList.remove('open');
    if (!menuOpen) els.scrim.classList.remove('open');
    setTimeout(() => { if (!panelOpen) { Kov.UI.clear(els.panel); Kov.UI.clear(els.panelLeft); } }, 520);
  }
  function isEmpty() { return !els.content.firstChild; }

  function setTitle(text) { els.title.textContent = text; if (Kov.NotifyUI) Kov.NotifyUI.setBaseTitle(text + ' — Kovapp'); }
  function setBadge(count) { els.badge.textContent = count > 99 ? '99+' : String(count); els.badge.hidden = !(count > 0); }
  function setChatCount(count) { if (!els.chatCount) return; els.chatCount.textContent = String(count); els.chatCount.hidden = !(count > 0); }

  /** Показывает раздел: заголовок, пункт меню, содержимое (фабрика возвращает элемент) */
  function show(key, title, factory) {
    closePanel();
    // ушли из деталей задачи — покидаем её комнату, иначе сервер шлёт события впустую
    if (key !== 'tasks' && Kov.WS && Kov.WS.unsubscribeTask) Kov.WS.unsubscribeTask();
    // Ушли из мессенджера — чат больше не «открыт», иначе уведомления по нему
    // молчали бы во всех остальных разделах: выбранный чат-то запоминается
    if (key !== 'messenger' && Kov.NotifyCenter && Kov.NotifyCenter.setActiveChat) {
      Kov.NotifyCenter.setActiveChat(null);
    }
    // отправки файлов идут дальше: переход между разделами их не прерывает
    markActive(key);
    setTitle(title);
    Kov.UI.clear(els.content);
    const section = h('div.section');
    els.content.append(section);
    els.content.scrollTop = 0;
    const result = factory(section);
    if (result instanceof Node) section.append(result);
    return section;
  }

  /** Связь с сервером: пропала — предупреждаем, вернулась — коротко успокаиваем */
  let linkTimer = null, linkKnownDown = false;
  function setLink(state) {
    clearTimeout(linkTimer);
    if (state === 'down') {
      linkKnownDown = true;
      Kov.UI.clear(els.link);
      els.link.className = 'link-bar down';
      els.link.append(h('span.dot'), h('span', 'Нет связи с сервером — переподключаемся'));
      els.link.hidden = false;
      return;
    }
    if (!linkKnownDown) { els.link.hidden = true; return; }   // первый вход — молчим
    linkKnownDown = false;
    Kov.UI.clear(els.link);
    els.link.className = 'link-bar up';
    els.link.append(icon('check'), h('span', 'Связь восстановлена'));
    els.link.hidden = false;
    linkTimer = setTimeout(() => { els.link.hidden = true; }, 2400);
  }
  function watchConnection() {
    if (!window.Kov || !Kov.WS || !Kov.WS.onStatus) return;
    // сокет часто дёргается на секунду при переходе сети: ждём, прежде чем пугать
    let downTimer = null;
    Kov.WS.onStatus(connected => {
      clearTimeout(downTimer);
      if (connected) return setLink('up');
      downTimer = setTimeout(() => setLink('down'), 1500);
    });
    window.addEventListener('offline', () => setLink('down'));
  }

  function panelHeader(title, onClose, extra) {
    return h('div.panel-head', button('', { kind: 'ghost', icon: 'arrow_back', title: 'Закрыть', onClick: onClose }), h('strong', { style: { fontSize: '1rem', flex: '1' } }, title), extra || null);
  }

  return { build, buildMenu, show, setTitle, setBadge, setChatCount, openPanel, closePanel, panelHeader, closeMenu, isEmpty, get content() { return els.content; } };
})();
