/* Раздел «Уведомления»: список сервера (сообщения чата сюда не попадают),
 * «Отметить все как прочитанные», «Создать уведомление» (руководство и старшие). */
window.Kov = window.Kov || {};
Kov.Views = Kov.Views || {};

Kov.Views.notifications = (function () {
  const { h, icon, button, sectionHeader, fmtDateTime } = Kov.UI;
  let items = [];
  let listEl = null;

  let knownIds = null;   // что уже видели: null — ещё ни разу не загружали
  let lastError = null;  // последняя ошибка чтения — показываем её в разделе

  // Основной маршрут чтения и запасные: если сервер отвечает «нет такого
  // адреса», пробуем остальные и запоминаем сработавший.
  const LIST_PATHS = [
    me => 'employees/' + me + '/notifications',
    me => 'notifications/employee/' + me,
    () => 'notifications/my',
    () => 'notifications',
  ];
  let listPath = null;

  /** Ответ сервера может быть массивом или объектом со списком внутри */
  function asList(data) {
    if (Array.isArray(data)) return data;
    if (data && typeof data === 'object') {
      for (const key of ['items', 'notifications', 'results', 'data']) if (Array.isArray(data[key])) return data[key];
    }
    return null;
  }

  async function fetchList() {
    const me = Kov.Api.state.userId;
    const order = listPath ? [listPath].concat(LIST_PATHS.filter(f => f !== listPath)) : LIST_PATHS;
    let firstError = null;
    for (const make of order) {
      const path = make(me);
      try {
        const list = asList(await Kov.Api.get(path));
        if (list) { listPath = make; return list; }
        if (!firstError) firstError = new Error('Сервер вернул не список: ' + path);
      } catch (e) {
        if (!firstError) firstError = e;
        if (e.status && e.status !== 404 && e.status !== 405) throw e;   // 500, 403 — дальше не идём
      }
    }
    throw firstError || new Error('Список уведомлений недоступен');
  }

  async function load() {
    try { items = await fetchList(); lastError = null; }
    catch (e) { lastError = e; throw e; }
    items.sort((a, b) => (a.is_read - b.is_read) || (new Date(b.created_at || 0) - new Date(a.created_at || 0)));
    const unread = items.filter(n => !n.is_read).length;
    Kov.NotifyCenter.setUnreadNotifications(unread);
    if (knownIds === null) knownIds = new Set(items.map(n => n.id));
    return items;
  }

  // ── отправка ───────────────────────────────────────────────────────────────

  /** Один запрос на группу получателей; старый сервер понимает только employee_id */
  async function post(ids, message, targetSection, targetId) {
    const json = { user_ids: ids, message: message, target_section: targetSection || 'notifications' };
    if (targetId !== undefined && targetId !== null) json.target_id = targetId;
    try {
      await Kov.Api.post('notifications', { json });
      return true;
    } catch (e) {
      if (e.status === 400 || e.status === 422) {
        try {
          for (const id of ids) {
            await Kov.Api.post('notifications', { json: Object.assign({}, json, { user_ids: undefined, employee_id: id }) });
          }
          return true;
        } catch (e2) { e = e2; }
      }
      console.error('Уведомление не отправлено', json, e);
      Kov.UI.toast('Уведомление не отправлено: ' + Kov.UI.errorText(e), 'error');
      return false;
    }
  }

  /**
   * send(получатели, текст, раздел, id, { selfMessage })
   *
   * Себя из списка больше не вычёркиваем: раньше автор действия не видел
   * в разделе ни одного своего уведомления, а если он был единственным
   * участником задачи — уведомление не уходило вообще. Кого уведомлять,
   * решает вызывающая сторона. selfMessage — свой текст для автора
   * («Вы завершили этап…» вместо «Этап… завершён»).
   */
  async function send(userIds, message, targetSection, targetId, opts) {
    opts = opts || {};
    const me = Kov.Api.state.userId;
    const ids = [...new Set((userIds || []).map(Number).filter(Boolean))];
    if (!ids.length) return false;

    const groups = [];
    if (opts.selfMessage && ids.includes(me)) {
      groups.push({ ids: [me], text: opts.selfMessage });
      const rest = ids.filter(id => id !== me);
      if (rest.length) groups.push({ ids: rest, text: message });
    } else {
      groups.push({ ids, text: message });
    }

    let ok = true;
    for (const g of groups) if (!await post(g.ids, g.text, targetSection, targetId)) ok = false;
    if (ok && ids.includes(me)) refreshQuiet();   // своё уведомление показываем сразу
    return ok;
  }

  // Счётчик обновляем сами: если сервер не пришлёт notification.new по сокету,
  // уведомления всё равно появятся — при возврате на вкладку и раз в минуту.
  let pollTimer = null, loadFailed = false;
  async function refreshQuiet() {
    const before = knownIds;
    try { await load(); loadFailed = false; }
    catch (e) {
      if (!loadFailed) { loadFailed = true; console.error('Список уведомлений не загружен', e); }
      if (listEl && listEl.isConnected) renderList();   // покажем причину прямо в разделе
      return;
    }
    // всплывашка для тех, что появились между опросами (сокет мог не дойти)
    if (before) {
      const fresh = items.filter(n => !n.is_read && !before.has(n.id));
      fresh.forEach(n => Kov.NotifyCenter.handleEvent({
        type: 'notification.new', notification_id: n.id, message: n.message,
        target_section: n.target_section, target_id: n.target_id,
      }));
      if (fresh.length) Kov.NotifyCenter.setUnreadNotifications(items.filter(n => !n.is_read).length);
    }
    knownIds = new Set(items.map(n => n.id));
    if (listEl && listEl.isConnected) renderList();
  }
  function onWake() { if (document.visibilityState === 'visible') refreshQuiet(); }
  function startAutoRefresh(ms) {
    clearInterval(pollTimer);
    pollTimer = setInterval(onWake, ms || 60000);
    document.removeEventListener('visibilitychange', onWake);
    document.addEventListener('visibilitychange', onWake);
    window.removeEventListener('focus', onWake);
    window.addEventListener('focus', onWake);
  }

  async function markRead(ids) {
    if (!ids.length) return;
    await Kov.Api.post('notifications/mark-as-read', { json: ids });
  }

  const SECTION_GLYPH = { task_view: 'flag', warehouse: 'archive', equipment: 'archive',
    project: 'project', task_complete: 'check' };

  function targetHash(n) {
    if (!n.target_id) return null;
    if (n.target_section === 'task_view') return '#/tasks/' + n.target_id;
    // склад: target_id — номер проекта, открываем сразу нужный подраздел
    if (n.target_section === 'warehouse') return '#/projects/' + n.target_id + '/warehouse';
    // У оборудования нет проекта: ведём в общий раздел
    if (n.target_section === 'equipment') return '#/equipment';
    // Заявка на завершение ведёт внутрь самой задачи: директор смотрит
    // материалы и решает уже там, если не решил прямо в уведомлении
    if (n.target_section === 'task_complete') return '#/tasks/' + n.target_id;
    if (n.target_section === 'project') return '#/projects/' + n.target_id;
    return null;
  }

  /**
   * Кнопки решения прямо в уведомлении о завершении задачи.
   *
   * Директору не нужно заходить в задачу, чтобы её закрыть, — но и заходить
   * ничто не мешает: там те же две кнопки и все материалы задачи. Обе дороги
   * ведут в одну точку сервера, поэтому разойтись они не могут.
   */
  function completionActions(n) {
    if (n.target_section !== 'task_complete' || !Kov.Store.isManagement()) return null;
    const row = h('div.notif-actions');
    const run = async (path, okText) => {
      row.querySelectorAll('.btn').forEach(b => { b.disabled = true; });
      try {
        await Kov.Api.post('tasks/' + n.target_id + '/completion/' + path);
        Kov.UI.toast(okText, 'success');
        Kov.UI.clear(row);
        row.append(h('span.notif-done', icon('check'), h('span', okText)));
        load().then(renderList).catch(() => {});
      } catch (e) {
        Kov.UI.toast(Kov.UI.errorText(e), 'error');
        row.querySelectorAll('.btn').forEach(b => { b.disabled = false; });
      }
    };
    const stop = fn => e => { e.stopPropagation(); fn(); };
    row.append(
      button('Подтвердить', { icon: 'check', onClick: stop(() => run('approve', 'Задача завершена')) }),
      button('Отклонить', { icon: 'close', kind: 'ghost', onClick: stop(() => run('decline', 'Завершение отклонено')) }));
    return row;
  }

  function renderList() {
    // Сообщения о пустоте и об ошибке ключей не имеют — убираем их сами,
    // дальше список сверяется по номерам уведомлений
    [...listEl.children].forEach(el => { if (!el.dataset.syncKey) el.remove(); });
    const reload = button('Обновить', { kind: 'ghost', icon: 'refresh', onClick: () => refreshQuiet() });
    // Ошибка или пустой список: карточек быть не должно вовсе
    if (lastError || !items.length) Kov.UI.clear(listEl);
    if (lastError) {
      listEl.append(h('div.empty-state',
        h('span.empty-icon', icon('warning')),
        h('div.empty-title', 'Список не загрузился'),
        h('div.empty-text', Kov.UI.errorText(lastError) + (lastError.status ? ' (HTTP ' + lastError.status + ')' : '')),
        h('div.btn-row', reload)));
      return;
    }
    if (!items.length) {
      listEl.append(h('div.empty-state',
        h('span.empty-icon', icon('bell')),
        h('div.empty-title', 'Уведомлений пока нет'),
        h('div.empty-text', 'Здесь появляются сообщения о назначении на задачи, изменениях, завершении и возобновлении этапов и переводе задач в архив.'),
        h('div.btn-row', reload)));
      return;
    }
    // Список сверяется по номеру уведомления, а не собирается заново:
    // новые приходят по сокету, и полная пересборка мигала всем списком
    Kov.UI.syncList(listEl, items, {
      key: n => n.id,
      sign: n => [n.is_read ? 1 : 0, n.message, n.created_at].join('|'),
      make: n => {
        const el = h('div.notif-item' + (n.is_read ? '' : '.unread'), { role: 'button', tabIndex: 0 },
          icon(SECTION_GLYPH[n.target_section] || 'bell'),
          h('div.text', h('div', n.message), h('div.time', fmtDateTime(n.created_at)),
            completionActions(n)),
        );
        el.addEventListener('click', async () => {
          if (!n.is_read) { try { await markRead([n.id]); n.is_read = true; } catch (e) { /* */ } }
          renderList(); Kov.NotifyCenter.setUnreadNotifications(items.filter(x => !x.is_read).length);
          const target = targetHash(n);
          if (target) Kov.Router.go(target);
        });
        return el;
      },
    });
  }

  function view(section) {
    const me = Kov.Store.state.me;
    const canCreate = Kov.Store.canManageWork(me);   // маркер прав, а не слово в должности
    listEl = h('div.notif-list');
    // подпись: полная — на компьютере, короткая — на телефоне, чтобы обе кнопки встали в одну строку
    const label = (full, short) => {
      const f = document.createDocumentFragment();
      f.append(h('span.lbl-full', full), h('span.lbl-short', short));
      return f;
    };
    const markAll = button(label('Отметить все как прочитанные', 'Прочитать все'), { kind: 'ghost', icon: 'check', onClick: async () => {
      const ids = items.filter(n => !n.is_read).map(n => n.id);
      if (!ids.length) return Kov.UI.toast('Непрочитанных нет', 'info');
      try { await markRead(ids); items.forEach(n => { n.is_read = true; }); renderList(); Kov.NotifyCenter.markNotificationsRead(); }
      catch (e) { Kov.UI.toast('Не удалось отметить: ' + Kov.UI.errorText(e), 'error'); }
    } });
    markAll.setAttribute('aria-label', 'Отметить все как прочитанные');
    const create = canCreate ? button(label('Создать уведомление', 'Создать'), { icon: 'plus', onClick: createDialog }) : null;
    if (create) create.setAttribute('aria-label', 'Создать уведомление');
    // Кнопки прижаты к низу раздела и не уезжают вместе со списком:
    // прокручивается сам список, а не вся страница
    const actions = h('div.btn-row.notif-actions-bar', markAll, h('span', { style: { flex: 1 } }), create);
    // Выдачи со склада, ждущие сотрудника, — здесь, а не в глубине проекта:
    // раздел уведомлений и есть место, куда идут смотреть «что от меня хотят»
    const pending = Kov.Warehouse ? Kov.Warehouse.pendingPanel() : null;
    // То же и по оборудованию: выдача, возврат и приёмка подтверждаются здесь
    const gear = Kov.Views.equipment ? Kov.Views.equipment.pendingPanel() : null;
    section.classList.add('fill', 'notif-section');
    section.append(Kov.UI.loading());
    const done = () => {
      section.querySelector('.loading')?.remove();
      if (pending) section.append(pending.el);
      if (gear) section.append(gear.el);
      section.append(listEl, actions);
      renderList();
    };
    load().then(done).catch(() => done());   // ошибку покажет сам список
    // подписку на события ведёт сама панель — здесь только обновление списка
    Kov.WS && Kov.WS.on && Kov.WS.on(evt => {
      if (evt && evt.type === 'warehouse.issue') load().then(renderList).catch(() => {});
    });
  }

  /** Живое обновление из WebSocket: сервер может звать событие по-разному */
  const NEW_EVENTS = ['notification.new', 'notification.created', 'notification', 'notifications.new'];
  function onEvent(evt) {
    if (!NEW_EVENTS.includes(evt.type)) return;
    // всплывашку уже показал центр уведомлений — второй раз не показываем
    if (knownIds && evt.notification_id) knownIds.add(Number(evt.notification_id));
    refreshQuiet();
  }

  // ── окно «Создать уведомление» ─────────────────────────────────────────────

  /**
   * Два режима выбора получателей вместо двух списков сразу: сверху
   * переключатель «По отделам» / «Сотрудникам», ниже — один список,
   * освободившееся место отдано полю текста.
   */
  function createDialog() {
    let employees = [];
    let mode = 'dept';                      // dept | emp
    const selected = new Set();

    const message = h('textarea.textarea.notif-message', { placeholder: 'Текст уведомления…' });
    const deptBox = h('div.checklist.notif-pick');
    const empBox = h('div.checklist.notif-pick');
    const search = Kov.UI.searchInput('Поиск по ФИО, отделу, должности…', () => renderEmployees());
    const searchRow = h('div.toolbar', search.el);
    const counter = h('span.small.muted', 'Выбрано: 0');
    const clearBtn = button('Снять выбор', { kind: 'ghost', icon: 'close', title: 'Убрать всех получателей', onClick: () => { selected.clear(); renderAll(); } });

    const switcher = Kov.UI.segmented(
      [['dept', 'По отделам', 'building'], ['emp', 'Сотрудникам', 'users']],
      mode, key => { mode = key; applyMode(); });

    function departmentNames() {
      return [...new Set(employees.map(e => e.department).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'ru'));
    }

    function updateCounter() {
      counter.textContent = 'Выбрано: ' + selected.size + (selected.size ? ' из ' + employees.length : '');
      clearBtn.disabled = !selected.size;
    }

    function renderDepartments() {
      Kov.UI.clear(deptBox);
      // «Всем отделам» — абсолютно все сотрудники, включая тех, у кого отдел не заполнен
      const everyone = employees.length > 0 && employees.every(e => selected.has(e.id));
      const allCb = h('input', { type: 'checkbox', checked: everyone, onchange: ev => {
        employees.forEach(e => ev.target.checked ? selected.add(e.id) : selected.delete(e.id));
        renderAll();
      } });
      allCb.indeterminate = !everyone && selected.size > 0;
      deptBox.append(h('label.pick-all', { title: 'Отправить уведомление всем сотрудникам' },
        allCb, h('span', { style: { flex: 1 } }, 'Всем отделам'),
        h('span.chip' + (everyone ? '.info' : ''), 'сотрудников: ' + employees.length)));

      const names = departmentNames();
      if (!names.length) { deptBox.append(h('div.muted', { style: { padding: '.4rem' } }, 'Отделы не найдены')); return; }
      names.forEach(d => {
        const staff = employees.filter(e => e.department === d);
        const picked = staff.filter(e => selected.has(e.id)).length;
        const cb = h('input', { type: 'checkbox', checked: picked === staff.length, onchange: ev => {
          staff.forEach(e => ev.target.checked ? selected.add(e.id) : selected.delete(e.id));
          renderAll();
        } });
        cb.indeterminate = picked > 0 && picked < staff.length;
        deptBox.append(h('label', cb, h('span', { style: { flex: 1 } }, d),
          h('span.chip' + (picked ? '.info' : ''), picked ? picked + ' из ' + staff.length : 'сотрудников: ' + staff.length)));
      });
    }

    function renderEmployees() {
      const needle = (search.input.value || '').trim().toLowerCase();
      Kov.UI.clear(empBox);
      const list = employees.filter(e => !needle ||
        (Kov.UI.fullName(e, true) + ' ' + (e.department || '') + ' ' + (e.position || '')).toLowerCase().includes(needle));
      list.forEach(e => {
        const cb = h('input', { type: 'checkbox', checked: selected.has(e.id), onchange: ev => {
          ev.target.checked ? selected.add(e.id) : selected.delete(e.id);
          updateCounter(); renderDepartments();
        } });
        const self = e.id === Kov.Api.state.userId;
        empBox.append(h('label', cb, Kov.UI.avatar(e, null, Kov.Store.photoUrl(e.id)),
          h('span', { style: { flex: 1 } }, Kov.UI.fullName(e, true) + (self ? ' (вы)' : ''),
            h('span.muted.small', ' — ' + [e.department, e.position].filter(Boolean).join(', ')))));
      });
      if (!list.length) empBox.append(h('div.muted', { style: { padding: '.4rem' } }, 'Никого не найдено'));
    }

    function renderAll() { renderDepartments(); renderEmployees(); updateCounter(); }

    function applyMode() {
      deptBox.hidden = mode !== 'dept';
      searchRow.hidden = mode !== 'emp';
      empBox.hidden = mode !== 'emp';
      renderAll();
    }

    return Kov.UI.modal({
      title: 'Создать уведомление', glyph: 'bell', wide: true,
      body: el => {
        el.classList.add('notif-create');
        el.append(
          h('div.notif-mode', switcher, h('span.spacer'), counter, clearBtn),
          h('div.notif-pick-wrap', deptBox, searchRow, empBox),   // общая высота задана в стилях
          Kov.UI.field('Текст уведомления', message, true),
        );
        applyMode();
        Kov.Store.photoIds().catch(() => null);
        Kov.Store.employees().then(list => { employees = list || []; renderAll(); });
      },
      buttons: [
        { text: 'Отмена', kind: 'ghost' },
        { text: 'Отправить', icon: 'mail', onClick: async close => {
          const text = message.value.trim();
          if (!selected.size) return Kov.UI.toast('Выберите получателей', 'warning');
          if (!text) return Kov.UI.toast('Введите текст уведомления', 'warning');
          if (await send([...selected], text, 'notifications', null)) {
            Kov.UI.toast('Уведомление отправлено получателям: ' + selected.size, 'success');
            close();
          }
        } },
      ],
    });
  }

  return { view, load, onEvent, send, startAutoRefresh, refreshQuiet, targetHash };
})();
