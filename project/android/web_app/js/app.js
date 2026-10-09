/* Точка входа веб-версии: восстановление сеанса, масштаб, маршруты,
 * WebSocket, центр уведомлений, выход. */
window.Kov = window.Kov || {};

Kov.App = (function () {
  const { h } = Kov.UI;
  let started = false;
  let wsBound = false;   // обработчики сокета подключены

  function placeholder(title, note) {
    return section => section.append(Kov.UI.sectionHeader(title, 'info'), h('div.card', h('p', { text: note })));
  }

  /** Маршруты, открытые по прямому адресу, тоже обязаны проверять права */
  function guard(check, fallback) {
    return handler => (...args) => {
      if (!check()) { Kov.UI.toast('Раздел доступен только руководству', 'warning'); return Kov.Router.go(fallback || '#/tasks', true); }
      return handler(...args);
    };
  }

  function registerRoutes() {
    const R = Kov.Router;
    R.add('#/', () => R.go('#/tasks', true));
    R.add('#/notifications', () => Kov.Shell.show('notifications', 'Уведомления', s => Kov.Views.notifications.view(s)));
    R.add('#/tasks', () => Kov.Shell.show('tasks', 'Задачи', s => Kov.Views.tasks.view(s)));
    const canWork = guard(() => Kov.Store.canManageWork());
    const canOrg = guard(() => Kov.Store.canManageOrganizations());
    R.add('#/tasks/new', canWork(() => Kov.Shell.show('tasks', 'Новая задача', (Kov.Views.taskEdit ? s => Kov.Views.taskEdit.view(s, null) : placeholder('Новая задача', 'Форма задачи появится на следующем шаге разработки.')))));
    R.add('#/tasks/:id/edit', canWork(p => Kov.Shell.show('tasks', 'Редактирование задачи', (Kov.Views.taskEdit ? s => Kov.Views.taskEdit.view(s, p.id) : placeholder('Редактирование задачи', 'Форма задачи появится на следующем шаге разработки.')))));
    R.add('#/tasks/:id', p => Kov.Shell.show('tasks', 'Задача', (Kov.Views.taskView ? s => Kov.Views.taskView.view(s, p.id) : placeholder('Задача #' + p.id, 'Подробное окно задачи появится на следующем шаге разработки.'))));
    // Раздел закрыт рядовым сотрудникам монтажа и техобслуживания: из меню
    // он убран, но по прямой ссылке войти тоже нельзя
    const canProjects = fn => (...a) => (Kov.Rights.canViewProjects() ? fn(...a) : R.go('#/tasks', true));
    R.add('#/projects', canProjects(() => Kov.Shell.show('projects', 'Проекты', (Kov.Views.projects ? s => Kov.Views.projects.view(s) : placeholder('Проекты', 'Раздел «Проекты» появится на следующем шаге разработки.')))));
    R.add('#/projects/:id', canProjects(p => Kov.Shell.show('projects', 'Проект', (Kov.Views.projects ? s => Kov.Views.projects.view(s, p.id) : placeholder('Проект', 'Раздел «Проекты» появится на следующем шаге разработки.')))));
    // подраздел проекта живёт в адресе: на склад можно дать ссылку, и «назад» работает
    R.add('#/projects/:id/:section', canProjects(p => Kov.Shell.show('projects', 'Проект', s => Kov.Views.projects.view(s, p.id, p.section))));
    R.add('#/organizations', () => Kov.Shell.show('organizations', 'Организации', s => Kov.Views.organizations.view(s)));
    R.add('#/equipment', () => Kov.Shell.show('equipment', 'Оборудование', s => Kov.Views.equipment.view(s)));
    R.add('#/organizations/new', canOrg(() => Kov.Views.organizations.editPage(null), '#/organizations'));
    R.add('#/organizations/:id/edit', canOrg(p => Kov.Views.organizations.editPage(p.id), '#/organizations'));
    R.add('#/employees', () => Kov.Shell.show('employees', 'Работники', s => Kov.Views.employees.view(s)));
    R.add('#/employees/:id', p => openProfilePanel(p.id));
    R.add('#/profile', () => openProfilePanel(Kov.Api.state.userId));
    R.add('#/settings', () => Kov.Shell.show('settings', 'Настройки', s => Kov.Views.settings.view(s)));
    // на телефоне настройки — разделами: каждый открывается отдельным экраном
    const SET_TITLES = { look: 'Внешний вид', notify: 'Уведомления', device: 'Это устройство' };
    R.add('#/settings/:part', p => Kov.Shell.show('settings', (Kov.Scale.isMobile() && SET_TITLES[p.part]) || 'Настройки',
      s => Kov.Views.settings.view(s, p.part)));
    R.add('#/csv', () => { if (!Kov.Store.isManagement()) return R.go('#/tasks', true); Kov.Shell.show('csv', 'Файлы CSV', s => Kov.Views.csv.view(s)); });
    R.add('#/messenger', () => Kov.Shell.show('messenger', 'Мессенджер', (Kov.Views.messenger ? s => Kov.Views.messenger.view(s) : placeholder('Мессенджер', 'Мессенджер появится на следующем шаге разработки.'))));
    R.add('#/messenger/:id', p => Kov.Shell.show('messenger', 'Мессенджер', (Kov.Views.messenger ? s => Kov.Views.messenger.view(s, p.id) : placeholder('Мессенджер', 'Мессенджер появится на следующем шаге разработки.'))));
  }

  /** Профиль выезжает панелью слева поверх текущего раздела */
  function openProfilePanel(id) {
    if (Kov.Shell.isEmpty()) Kov.Shell.show('tasks', 'Задачи', s => Kov.Views.tasks.view(s));
    Kov.Views.employees.openProfile(Number(id));
  }

  // Закрытие вкладки во время отправки: предупреждаем и обрываем передачу
  window.addEventListener('beforeunload', e => {
    if (Kov.Files && Kov.Files.current && Kov.Files.current.length) {
      e.preventDefault(); e.returnValue = '';
    }
  });
  window.addEventListener('pagehide', () => {
    if (Kov.Files && Kov.Files.abortAllUploads) Kov.Files.abortAllUploads();
    if (Kov.ChunkUpload) Kov.ChunkUpload.discardAll();
  });
  // короткий обрыв — докачка, дольше OFFLINE_GRACE_MS — отмена
  let offlineTimer = null;
  const uploading = () => !!(Kov.Files && Kov.Files.uploadsActive && Kov.Files.uploadsActive());
  window.addEventListener('offline', () => {
    if (!uploading() || offlineTimer) return;
    const grace = (Kov.Files && Kov.Files.OFFLINE_GRACE_MS) || 120000;
    Kov.UI.toast('Связь пропала — отправка продолжится, когда сеть вернётся', 'warning');
    offlineTimer = setTimeout(() => {
      offlineTimer = null;
      if (navigator.onLine !== false || !uploading()) return;
      Kov.Files.abortAllUploads('offline');
      Kov.UI.toast('Сети нет дольше ' + Math.round(grace / 60000) + ' мин — отправка отменена, файл не сохранён', 'error');
    }, grace);
  });
  window.addEventListener('online', () => {
    if (!offlineTimer) return;
    clearTimeout(offlineTimer); offlineTimer = null;
    if (uploading()) Kov.UI.toast('Связь вернулась — отправка продолжается', 'info');
  });

  function onWsEvent(evt) {
    Kov.NotifyCenter.handleEvent(evt);
    const kind = evt.type || '';
    // справочники живут в кэше — сбрасываем его, чтобы изменения увидели все
    if (kind.startsWith('employee.')) {
      Kov.Store.employees(true).then(() => {
        if (Kov.Views.employees && Kov.Views.employees.refresh) Kov.Views.employees.refresh(true);
      }).catch(() => {});
      if (Number(evt.employee_id) === Kov.Api.state.userId && kind === 'employee.updated') Kov.Store.loadMe().catch(() => {});
    }
    if (kind.startsWith('organization.') && Kov.Views.organizations && Kov.Views.organizations.refresh) {
      Kov.Views.organizations.refresh();
    }
    if (Kov.Views.notifications) Kov.Views.notifications.onEvent(evt);
    if (Kov.Views.tasks) Kov.Views.tasks.onEvent(evt);
    if (Kov.Views.projects && Kov.Views.projects.onEvent) Kov.Views.projects.onEvent(evt);
    if (Kov.Views.taskView && Kov.Views.taskView.onEvent) Kov.Views.taskView.onEvent(evt);
    if (Kov.Views.messenger && Kov.Views.messenger.onEvent) Kov.Views.messenger.onEvent(evt);
    if (Kov.Views.equipment && Kov.Views.equipment.onEvent) Kov.Views.equipment.onEvent(evt);
  }

  async function startSession() {
    try { await Kov.Store.loadMe(); }
    catch (e) { if (e.status === 401) { Kov.Api.clear(); return showLogin(); } }
    // вход состоялся: ни одного экрана входа под интерфейсом остаться не должно
    document.querySelectorAll('.login-screen').forEach(el => el.remove());
    await Kov.Api.startFileTokens();   // короткий токен для ссылок на файлы
    Kov.Shell.build();
    Kov.NotifyCenter.init({
      userId: Kov.Api.state.userId,
      navigate: url => Kov.Router.go(url),
      resolveChat: async id => { const chats = await Kov.Api.get('chat/list'); const c = (chats || []).find(x => x.id === Number(id)); return c ? { name: c.name, is_group: c.is_group } : null; },
    });
    Kov.NotifyCenter.on('counters', c => { Kov.Shell.setBadge(c.notifications); Kov.Shell.setChatCount(c.chats); });
    const prefs = await Kov.Store.pref('web.notifications');
    if (prefs) Kov.NotifyCenter.setSettings(prefs);
    const webSettings = await Kov.Store.pref('web.settings');
    // Тема сотрудника с сервера главнее местной: он мог сменить её на другом устройстве
    Kov.Theme.applyFromPrefs(webSettings);
    if (webSettings && webSettings.scale) {
      // База стала на четверть крупнее: у тех, кто уже поднимал масштаб,
      // делим сохранённый множитель, иначе размер подпрыгнул бы дважды
      if (!webSettings.scaleBase) {
        webSettings.scale = Math.max(0.75, Math.min(1.25, webSettings.scale / 1.25));
        webSettings.scaleBase = 2;
        Kov.Store.setPref('web.settings', webSettings).catch(() => {});
      }
      Kov.Scale.setMultiplier(webSettings.scale);
    }
    document.documentElement.dataset.settings = 'applied';   // сохранённые тема и масштаб применены — этого ждут тесты снимков
    try { await Kov.Views.notifications.load(); } catch (e) { console.error('Уведомления не загрузились', e); }
    Kov.Views.notifications.startAutoRefresh();
    await Kov.PushClient.init({ swUrl: 'sw.js' });
    Kov.PushClient.sync();
    setTimeout(() => { try { Kov.NotifyCenter.offerPermission(); } catch (e) { /* */ } }, 1500);
    // подписка — одна на всю жизнь страницы: после выхода и входа без перезагрузки
    // вторая подписка удваивала каждое событие (счётчик «вниз» в чате рос на 2)
    if (!wsBound) {
      wsBound = true;
      Kov.WS.on(onWsEvent);
      // после разрыва связи события за время простоя не приходят — перечитываем раздел
      Kov.WS.onReconnect(() => {
        Kov.Views.notifications.refreshQuiet();
        Kov.Store.employees(true).catch(() => {});
        // перерисовка раздела оборвала бы идущую отправку — ждём, пока она закончится
        if (Kov.Files && Kov.Files.uploadsActive && Kov.Files.uploadsActive()) Kov.Files.whenIdle(() => Kov.Router.dispatch());
        else Kov.Router.dispatch();
      });
    }
    Kov.WS.start();
    if (!started) { started = true; registerRoutes(); Kov.Router.start(() => Kov.Router.go('#/tasks', true)); }
    else Kov.Router.dispatch();
  }

  function showLogin() {
    // 401 сначала ловит обработчик kov:unauthorized, затем startSession: экран входа — один
    if (document.querySelector('.login-screen')) return;
    Kov.Views.login.render(() => startSession());
  }

  async function logout() {
    if (!await Kov.UI.confirm('Выход', 'Выйти из учётной записи?', { okText: 'Выйти', icon: 'logout' })) return;
    Kov.WS.stop();
    Kov.Api.clear(); Kov.Store.reset();
    document.querySelector('.app')?.remove();
    Kov.NotifyCenter.setUnreadNotifications(0); Kov.NotifyCenter.setUnreadChats(0);
    location.hash = '#/';
    showLogin();
  }

  function init() {
    Kov.Theme.init();
    Kov.Scale.init();
    Kov.NotifyUI.setBaseTitle('Kovapp');
    window.addEventListener('kov:unauthorized', () => { if (document.querySelector('.login-screen')) return; Kov.WS.stop(); Kov.Api.clear(); document.querySelector('.app')?.remove(); showLogin(); });
    const host = window.KOV_API_HOST || location.origin;
    if (Kov.Api.restore() && Kov.Api.state.token) { if (!Kov.Api.state.host) Kov.Api.state.host = host; startSession(); }
    else { Kov.Api.setAuth(host, '', null); showLogin(); }
  }

  return { init, logout, startSession };
})();

document.addEventListener('DOMContentLoaded', Kov.App.init);
