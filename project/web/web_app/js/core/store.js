/* Состояние сеанса: текущий сотрудник, кэш сотрудников и фото, настройки. */
window.Kov = window.Kov || {};

Kov.Store = (function () {
  const state = { me: null, employees: [], employeesAt: 0, photoIds: new Set(), photoIdsAt: 0, prefs: {}, departments: [], positions: [] };
  const MANAGEMENT = ['Директор', 'Администратор'];
  // Права определяет маркер employees.status, а не слово в названии должности.
  // Разбор должности остался запасным путём для записей, заведённых до
  // перехода: пока маркер не проставлен, права не должны ни пропасть, ни
  // расшириться. Настоящая проверка всё равно на сервере.
  const STATUSES = { worker: 'работник', head: 'руководитель отдела', director: 'директор', admin: 'администратор' };

  function statusOf(user) {
    const me = user || state.me || {};
    const marked = String(me.status || '').trim().toLowerCase();
    if (Object.values(STATUSES).includes(marked)) return marked;
    const p = String(me.position || '').trim();
    if (p === 'Директор') return STATUSES.director;
    if (p === 'Администратор') return STATUSES.admin;
    if (p && (p.includes('Старший') || p.includes('Главный'))) return STATUSES.head;
    return STATUSES.worker;
  }

  function isManagement(user) { const s = statusOf(user); return s === STATUSES.director || s === STATUSES.admin; }

  /**
   * Учётная запись администратора невидима для всех остальных.
   *
   * Она служебная: в списках участников, исполнителей, получателей и в выборе
   * собеседника ей делать нечего. Сам администратор себя видит — иначе не
   * сможет вести свою же запись. В переписке имя остаётся: скрывать автора
   * уже отправленных сообщений нельзя, прячется только выбор нового
   * собеседника.
   */
  function isHiddenAccount(person) {
    if (!person) return false;
    if (isAdmin(state.me)) return false;
    return statusOf(person) === STATUSES.admin && person.id !== Kov.Api.state.userId;
  }

  /** Список людей без служебных записей — для любых выпадающих и выборов */
  function visible(list) { return (list || []).filter(p => !isHiddenAccount(p)); }
  function isDirector(user) { return statusOf(user) === STATUSES.director; }
  function isAdmin(user) { return statusOf(user) === STATUSES.admin; }
  function isDepartmentHead(user) { return statusOf(user) === STATUSES.head; }

  /** Справочник организаций: руководство, старшие и главные, Договорной отдел */
  function canManageOrganizations(user) {
    const me = user || state.me || {};
    if (isManagement(me) || isDepartmentHead(me)) return true;
    return String(me.department || '').trim() === 'Договорной отдел';
  }

  async function loadMe() {
    try {
      state.me = await Kov.Api.get('employees/' + Kov.Api.state.userId);
    } catch (e) {
      if (e.status === 401) throw e;
      // Карточки сотрудника нет (аварийный вход, пустая база) — работаем
      // дальше с минимальными данными, иначе пропадают все кнопки
      state.me = { id: Kov.Api.state.userId, last_name: 'Пользователь', first_name: '',
                   position: 'Администратор', department: '', _fallback: true };
      Kov.UI.toast('Карточка сотрудника не найдена — часть данных профиля недоступна', 'warning');
    }
    return state.me;
  }

  async function employees(force) {
    if (!force && state.employees.length && Date.now() - state.employeesAt < 60000) return state.employees;
    state.employees = await Kov.Api.get('employees') || [];
    state.employeesAt = Date.now();
    return state.employees;
  }

  /** Сотрудники для выбора: тот же кэш, но без служебных записей */
  async function pickable(force) { return visible(await employees(force)); }

  async function employee(id) {
    const cached = state.employees.find(e => e.id === id);
    return cached || Kov.Api.get('employees/' + id);
  }

  async function photoIds(force) {
    if (!force && Date.now() - state.photoIdsAt < 60000) return state.photoIds;
    try {
      const data = await Kov.Api.get('employees/photo/ids');
      state.photoIds = new Set(Array.isArray(data) ? data : (data.ids || data.employee_ids || []));
    } catch (e) { state.photoIds = new Set(); }
    state.photoIdsAt = Date.now();
    return state.photoIds;
  }

  function photoUrl(employeeId, bust) {
    if (!state.photoIds.has(employeeId)) return null;
    return Kov.Api.fileUrl('employees/' + employeeId + '/photo', bust ? { v: bust } : null);
  }

  async function lists() {
    try { state.departments = await Kov.Api.get('employees/departments') || []; } catch (e) { /* */ }
    try { state.positions = await Kov.Api.get('employees/positions') || []; } catch (e) { /* */ }
    return { departments: state.departments, positions: state.positions };
  }

  // настройки хранятся отдельно для компьютера и смартфона: ключ.pc / ключ.mobile
  function deviceKey(key) {
    const phone = !!(window.matchMedia && window.matchMedia('(pointer: coarse)').matches);
    return key + (phone ? '.mobile' : '.pc');
  }
  // все настройки сотрудника — одним запросом при первом обращении (быстрее, чем по ключу)
  let allPrefs = null;
  function loadAllPrefs() {
    if (!allPrefs) allPrefs = Kov.Api.get('preferences').then(rows => {
      const map = {};
      (rows || []).forEach(r => { map[r.key] = r.value; });
      return map;
    }).catch(() => null);   // старый сервер — по ключам
    return allPrefs;
  }
  async function pref(key) {
    if (key in state.prefs) return state.prefs[key];
    const map = await loadAllPrefs();
    let value;
    if (map) value = map[deviceKey(key)] != null ? map[deviceKey(key)] : map[key];
    else {   // ключ устройства и прежний общий — одновременно, не по очереди
      const [own, shared] = await Promise.all([Kov.Api.get('preferences/' + deviceKey(key)).catch(() => null), Kov.Api.get('preferences/' + key).catch(() => null)]);
      value = own && own.value != null ? own.value : (shared ? shared.value : null);
    }
    if (key in state.prefs) return state.prefs[key];   // пока ждали, уже сохранили новое
    state.prefs[key] = value === undefined ? null : value;   // на этом устройстве не сохраняли — прежнее общее значение
    return state.prefs[key];
  }
  async function setPref(key, value) {
    state.prefs[key] = value;
    if (allPrefs) allPrefs.then(map => { if (map) map[deviceKey(key)] = value; });
    try { await Kov.Api.put('preferences/' + deviceKey(key), { json: { value } }); } catch (e) { /* сервер старый */ }
  }

  function reset() { allPrefs = null; Object.assign(state, { me: null, employees: [], employeesAt: 0, photoIds: new Set(), photoIdsAt: 0, prefs: {} }); }

  /** Кто может создавать и править задачи и проекты: руководство и руководители отделов */
  function canManageWork(user) { return isManagement(user) || isDepartmentHead(user); }

  return { state, loadMe, canManageWork, statusOf, STATUSES, employees, pickable, visible, isHiddenAccount, employee, photoIds, photoUrl, lists, pref, setPref, reset, isManagement, isDirector, isAdmin, isDepartmentHead, canManageOrganizations };
})();
