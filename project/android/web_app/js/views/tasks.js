/* Раздел «Задачи»: список активных/архивных, поиск по полю, окно «Фильтры»
 * (хранится на сервере: /task-filters/{is_archived}), подсветка дат окончания —
 * как в десктопе. Порядок «по важности» (по умолчанию) считает сервер одним запросом. */
window.Kov = window.Kov || {};
Kov.Views = Kov.Views || {};

Kov.Views.tasks = (function () {
  const { h, icon, button, sectionHeader, fmtDate, fmtDateTime, deadlineState } = Kov.UI;
  const SEARCH_MODES = [['name', 'Название'], ['organization', 'Организация'], ['responsible', 'Ответственный'], ['department', 'Отдел']];
  const state = { archived: false, tasks: [], filter: null, search: '', mode: 'name', selected: null };
  let ui = null;

  // ── данные ───────────────────────────────────────────────────────────────

  async function loadFilter() {
    try { state.filter = await Kov.Api.get('task-filters/' + state.archived, { query: { force_create: true } }); }
    catch (e) { state.filter = null; }
    return state.filter;
  }

  async function load() {
    const [tasks] = await Promise.all([Kov.Api.get('tasks/light', { query: { is_archived: state.archived } }).catch(e => (e && (e.status === 404 || e.status === 422)) ? Kov.Api.get('tasks', { query: { is_archived: state.archived } }) : Promise.reject(e)), loadFilter()]);
    state.tasks = tasks || [];
    // список перерисовывается, только если в нём что-то изменилось — иначе строки «мигают»
    let sig = '';
    try { sig = JSON.stringify([state.tasks, state.filter]); } catch (e) { sig = String(Date.now()); }
    state.changed = sig !== state.sig;
    state.sig = sig;
    return state.tasks;
  }

  function activeStage(task) {
    if (task.state === null || task.state === undefined) return null;
    return (task.stage_details || []).find(s => s.stage_number === Number(task.state)) || null;
  }
  // текущий этап ведёт отдел пользователя и он ещё не завершён — то же условие, что у сервера
  function isMine(task) {
    const dep = (Kov.Store.state.me || {}).department;
    const stage = activeStage(task);
    return !!(dep && stage && stage.department_name === dep && !stage.is_completed);
  }
  const ORDER_CODES = ['id_asc', 'id_desc', 'deadline_asc', 'deadline_desc', 'archived_asc', 'archived_desc'];
  function sortCodes() {
    const raw = (state.filter && state.filter.sort_by) || '';
    return raw.split(',').map(s => s.trim()).filter(Boolean);
  }
  function sorted(tasks) {
    const far = new Date(9999, 11, 31).getTime();
    let codes = sortCodes().filter(c => ORDER_CODES.includes(c));
    if (!codes.length && state.archived) codes = ['archived_desc'];
    // «по важности»: порядок уже задал сервер — список не пересортировывается
    if (!codes.length) return tasks.slice();
    const list = tasks.slice();
    codes.slice().reverse().forEach(code => {
      const i = code.lastIndexOf('_'); const field = code.slice(0, i), dir = code.slice(i + 1);
      const key = t => field === 'deadline' ? (Kov.UI.parseDate(t.deadline)?.getTime() ?? far)
        : field === 'id' ? (t.id || 0)
        : field === 'archived' ? (Kov.UI.parseDate(t.archived_at)?.getTime() ?? far) : 0;
      list.sort((a, b) => (key(a) - key(b)) * (dir === 'desc' ? -1 : 1));
    });
    return list;
  }
  function matches(task) {
    const needle = state.search.trim().toLowerCase();
    if (!needle) return true;
    switch (state.mode) {
      case 'organization': return (task.customer_organization || '').toLowerCase().includes(needle);
      case 'responsible': return (task.responsible_persons || []).some(p => Kov.UI.fullName(p).toLowerCase().includes(needle));
      case 'department': return (task.stage_details || []).some(s => (s.department_name || '').toLowerCase().includes(needle));
      default: return (task.name || '').toLowerCase().includes(needle);
    }
  }
  function filterActive() {
    const f = state.filter;
    if (!f) return false;
    if (f.stage_count || f.min_created_date || f.max_created_date) return true;
    const codes = sortCodes(); const def = state.archived ? 'archived_desc' : 'priority';
    return !(codes.length === 0 || (codes.length === 1 && codes[0] === def));
  }

  // ── таблица ──────────────────────────────────────────────────────────────

  function columns() {
    const name = { key: 'name', label: 'Название', align: 'left', render: t => {
      const cell = h('span.name-cell' + (isMine(t) ? '.mine' : ''));
      if (isMine(t)) {
        const s = deadlineState(t.deadline, t.activity === 'Активная');
        const [glyph, color, hint] = s === 'overdue' ? ['warning', 'var(--critical)', 'просрочена'] : s === 'urgent' ? ['clock', 'var(--warning)', 'меньше суток до даты окончания'] : ['flag', 'var(--accent)', 'в работе'];
        cell.append(h('span.mark', { style: { color }, title: 'Текущий этап ведёт ваш отдел — ' + hint, html: Kov.Icons.svg(glyph, { stroke: 2.2 }) }));
      }
      cell.append(t.name || '');
      return cell;
    } };
    const org = { key: 'customer_organization', label: 'Организация', render: t => t.customer_organization || 'Не указана' };
    const progress = { key: 'progress', label: 'Прогресс', render: t => Kov.UI.stageDots(t.stage_details, (t.stage_details || []).length, Number(t.state)) };
    if (state.archived) {
      return [name, org,
        { key: 'archived_at', label: 'Дата архивации', render: t => fmtDateTime(t.archived_at) },
        { key: 'who', label: 'Кто архивировал', render: t => t.who_archived_employee ? Kov.UI.fullName(t.who_archived_employee) : 'Неизвестный' },
        progress];
    }
    return [name, org,
      { key: 'deadline', label: 'Дата окончания', render: t => {
        const s = deadlineState(t.deadline, t.activity === 'Активная');
        const date = h('span.deadline' + (s ? '.' + s : ''), { title: s === 'overdue' ? 'Дата окончания просрочена' : s === 'urgent' ? 'До даты окончания меньше суток' : null }, t.deadline ? fmtDate(t.deadline) : 'Не указана');
        // просрочка — словом, а не только красным цветом
        return s === 'overdue' ? [date, h('span.overdue-note', 'просрочено')] : date;
      } },
      { key: 'departments', label: 'Отделы', render: t => { const d = [...new Set((t.stage_details || []).map(s => s.department_name).filter(Boolean))].sort(); return d.join(', ') || 'Нет отделов'; } },
      progress];
  }

  function rowClass(t) {
    const s = deadlineState(t.deadline, t.activity === 'Активная');
    return s || '';
  }

  function renderTable(opts) {
    const rows = sorted(state.tasks.filter(matches));
    // при обновлении по сокету список не перерисовывается целиком: строки
    // остаются на месте, новая задача появляется мягко
    ui.table.fill(rows, { keepDrawn: !!(opts && opts.keep) });
    ui.rowsRef = rows;
    if (!(opts && opts.keep)) state.selected = null;
    updateButtons();
    ui.summary.textContent = rows.length ? 'Задач: ' + rows.length + (state.search ? ' (по фильтру)' : '') : '';
    ui.empty.hidden = rows.length > 0;
  }

  const TASK_LOCKED = 'Изменять задачу может руководство или руководитель отдела, к которому относятся все её этапы';

  function updateButtons() {
    const has = !!state.selected;
    const allowed = has && Kov.Rights.canManageTask(state.selected);
    [ui.edit, ui.restore, ui.del].forEach(btn => {
      if (!btn) return;
      btn.disabled = !has;
      Kov.Rights.setLocked(btn, has && !allowed, TASK_LOCKED);
    });
    ui.filterBtn.classList.toggle('on', filterActive());
    ui.filterBtn.style.background = filterActive() ? 'var(--warning-soft)' : '';
  }

  function rebuildTable() {
    const table = Kov.UI.table({
      columns: columns(), rowKey: t => t.id, rowClass, cardsClass: 'task-cards',
      hrefFor: t => '#/tasks/' + t.id,
      rowsRef: () => ui.rowsRef || [],
      onSelect: t => { state.selected = t; updateButtons(); },
      onActivate: t => Kov.Router.go('#/tasks/' + t.id),
    });
    Kov.UI.clear(ui.tableWrap); ui.tableWrap.append(table, ui.empty);
    ui.table = table;
  }

  async function refresh(opts) {
    // тихое обновление по событию: без затемнения и без перерисовки, если данные те же
    const quiet = !!(opts && opts.quiet);
    if (!quiet) ui.tableWrap.classList.add('busy');
    try {
      await load();
      if (quiet && !state.changed) return;
      rebuildTable(); renderTable(quiet ? { keep: true } : undefined);
    }
    catch (e) { if (!quiet) Kov.UI.toast('Не удалось загрузить задачи: ' + Kov.UI.errorText(e), 'error'); }
    finally { if (!quiet) ui.tableWrap.classList.remove('busy'); }
  }

  // ── разметка раздела ─────────────────────────────────────────────────────

  /**
   * Список сам подтягивает чужие изменения. Основной путь — событие по
   * сокету, но полагаться только на него нельзя: соединение могло оборваться
   * или событие потеряться. Поэтому есть ещё три повода перечитать список —
   * возвращение к вкладке, восстановление связи и редкий тихий опрос.
   */
  function watchTasks() {
    if (watchTasks.armed) return;
    watchTasks.armed = true;
    let timer = null;
    const alive = () => ui && ui.table && document.body.contains(ui.table);
    const soft = () => {
      if (!alive()) return;
      clearTimeout(timer);
      timer = setTimeout(async () => {
        if (!alive()) return;
        try { await load(); if (state.changed) renderTable({ keep: true }); } catch (e) { /* попробуем в следующий раз */ }
      }, 400);
    };
    if (Kov.WS && Kov.WS.on) {
      Kov.WS.on(evt => {
        if (!evt) return;
        if (evt.type === 'tasks.changed' || evt.type === 'task.created'
            || evt.type === 'task.deleted' || evt.type === 'task.archived'
            || evt.type === 'task.unarchived' || evt.type === 'task.updated'
            || evt.type === 'task.participants_updated') soft();
      });
    }
    if (Kov.WS && Kov.WS.onReconnect) Kov.WS.onReconnect(soft);
    window.addEventListener('focus', soft);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) soft(); });
    setInterval(soft, 60000);
  }

  function view(section) {
    ui = {};
    watchTasks();
    section.classList.add('fill');   // высота = экран, список листается внутри
    const search = Kov.UI.searchInput('Поиск по задачам…', v => { state.search = v; renderTable(); });
    const mode = Kov.UI.select(SEARCH_MODES, state.mode, v => { state.mode = v; renderTable(); }, { title: 'Поле, по которому идёт поиск' });
    // подпись «Искать в:» — без неё выбор поля принимали за сортировку
    const modeBox = h('label.search-in', h('span.search-in-t', 'Искать в:'), mode);
    ui.filterBtn = button('Фильтры', { kind: 'ghost', icon: 'filter', title: 'Сортировка, число этапов, дата создания', onClick: filterDialog });
    const seg = Kov.UI.segmented([['active', 'Активные', 'flag'], ['archived', 'Архивные', 'archive']], state.archived ? 'archived' : 'active', key => {
      state.archived = key === 'archived'; state.selected = null; buildActions(); refresh();
    });
    ui.tableWrap = h('div.table-wrap');
    ui.empty = h('div.empty', { hidden: true }, 'Задач нет');
    ui.summary = h('div.small.muted');
    // ширину проверяет fitRow: не помещаются подписи — компактнее (значки, без значков)
    ui.actions = Kov.UI.fitRow(h('div.btn-row.end.tasks-actions'), ['icons-only']);
    const switchRow = Kov.UI.fitRow(h('div.list-switch', seg, ui.summary), ['fit-tight', 'fit-bare', 'fit-wrap']);
    section.append(h('div.toolbar', search.el, modeBox, ui.filterBtn), ui.tableWrap,
      h('div.btn-row', switchRow, ui.actions));
    buildActions();
    ui.tableWrap.append(Kov.UI.bigLoading('Загружаем задачи…'));
    refresh();
  }

  // подпись кнопки: полная — на компьютере, короткая — на телефоне (кнопки в одну строку)
  function action(full, short, opts) {
    const text = document.createDocumentFragment();
    text.append(h('span.lbl-full', full), h('span.lbl-short', short));
    const b = button(text, opts);
    b.setAttribute('aria-label', full);
    return b;
  }

  function buildActions() {
    Kov.UI.clear(ui.actions);
    ui.edit = ui.restore = ui.del = null;
    // Создавать и править задачи могут руководство и руководители отделов
    if (!Kov.Store.canManageWork()) return;
    if (!state.archived) {
      ui.edit = action('Редактировать', 'Изменить', { kind: 'ghost', icon: 'edit', disabled: true, onClick: () => state.selected && Kov.Router.go('#/tasks/' + state.selected.id + '/edit') });
      // Удалять можно и активную задачу: раньше кнопка была только в архиве
      ui.del = action('Удалить', 'Удалить', { kind: 'danger', icon: 'trash', disabled: true, onClick: deleteSelected });
      ui.actions.append(ui.edit, ui.del, action('Новая задача', 'Создать', { icon: 'plus', onClick: () => Kov.Router.go('#/tasks/new') }));
    } else {
      ui.restore = action('Вернуть в работу', 'Вернуть', { kind: 'ghost', icon: 'refresh', disabled: true, onClick: restoreSelected });
      ui.del = action('Удалить', 'Удалить', { kind: 'danger', icon: 'trash', disabled: true, onClick: deleteSelected });
      ui.actions.append(ui.restore, ui.del);
    }
  }

  async function restoreSelected() {
    const t = state.selected; if (!t) return;
    if (!await Kov.UI.confirm('Вернуть в работу', 'Вернуть задачу «' + t.name + '» в работу?', { okText: 'Вернуть', icon: 'refresh' })) return;
    if (t.project_archived) return Kov.UI.toast('Задача относится к завершённому проекту — сначала верните проект в работу', 'warning');   // проект завершён
        try { await Kov.Api.post('tasks/' + t.id + '/unarchive'); Kov.UI.toast('Задача возвращена в работу', 'success'); refresh(); }
    catch (e) { Kov.UI.toast('Не удалось: ' + Kov.UI.errorText(e), 'error'); }
  }
  async function deleteSelected() {
    const t = state.selected; if (!t) return;
    const warn = 'Удалить задачу «' + t.name + '» без возможности восстановления?\n\n'
      + 'Вместе с ней уйдут: все этапы и их описания, файлы задачи и этапов вместе с папкой на сервере, '
      + 'участники, ответственные и уведомления по этой задаче.'
      + (t.activity === 'Активная' ? '\n\nЗадача сейчас в работе — возможно, её достаточно отправить в архив.' : '');
    if (!await Kov.UI.confirm('Удаление задачи', warn, { okText: 'Удалить навсегда', danger: true })) return;
    try { await Kov.Api.del('tasks/' + t.id); Kov.UI.toast('Задача удалена', 'success'); refresh(); }
    catch (e) { Kov.UI.toast('Не удалось удалить: ' + Kov.UI.errorText(e), 'error'); }
  }

  // ── окно «Фильтры» (как TaskFilterDialog) ────────────────────────────────

  // «Сортировка и фильтры» — выезжающее снизу меню, как в проектах; всё сохраняется на сервере, как раньше
  function filterDialog() {
    if (!Kov.UI.touchUi()) return filterModal();   // компьютер — прежнее окно, телефон и планшет — шторка снизу
    const f = state.filter || {};
    const codes = sortCodes();
    // одна «Сортировка» вместо двух-трёх списков со значением «—»: было непонятно, какая действует
    // по умолчанию — «по важности»: сначала задачи, где сейчас этап вашего отдела, потом чужие просроченные и на сегодня
    const SORTS = (state.archived ? [['archived_desc', 'По архивации: сначала недавние'], ['archived_asc', 'По архивации: сначала давние']]
      : [['priority', 'По важности: сначала мой отдел']])
      .concat([['deadline_asc', 'По сроку: сначала ближние'], ['deadline_desc', 'По сроку: сначала дальние'],
        ['id_desc', 'Сначала новые'], ['id_asc', 'Сначала старые']]);
    const first = codes.length ? codes[0] : SORTS[0][0];
    const v = { sort: SORTS.some(o => o[0] === first) ? first : SORTS[0][0], stages: f.stage_count || null };
    // короткие списки — шторкой по высоте содержимого, без поиска (как сортировка и фильтры в проектах)
    const sortSel = Kov.UI.select(SORTS, v.sort, x => { v.sort = x; }, { title: 'Сортировка' });
    const stages = Kov.UI.select([[null, 'Любое']].concat(Array.from({ length: 10 }, (_, k) => [k + 1, String(k + 1)])), v.stages, x => { v.stages = x; }, { title: 'Количество этапов' });
    const useDates = h('input', { type: 'checkbox', checked: !!(f.min_created_date || f.max_created_date) });
    const from = h('input.input', { type: 'date', value: f.min_created_date ? String(f.min_created_date).slice(0, 10) : Kov.UI.isoDate(new Date(Date.now() - 365 * 86400000)) });
    const to = h('input.input', { type: 'date', value: f.max_created_date ? String(f.max_created_date).slice(0, 10) : Kov.UI.isoDate(new Date()) });
    const datesRow = h('div.form-grid', Kov.UI.field('Создана с', from), Kov.UI.field('Создана по', to));
    datesRow.hidden = !useDates.checked;
    useDates.addEventListener('change', () => { datesRow.hidden = !useDates.checked; });
    let sheet = null;
    const reset = Kov.UI.button('Сбросить', { kind: 'ghost', icon: 'refresh', onClick: async () => {
      try { await Kov.Api.post('task-filters/' + state.archived + '/reset'); sheet.close(); refresh(); }
      catch (e) { Kov.UI.toast('Не удалось сбросить: ' + Kov.UI.errorText(e), 'error'); }
    } });
    const apply = Kov.UI.button('Применить', { icon: 'check', onClick: async () => {
      // «по важности» — без явной сортировки: так её понимает и приложение для ПК
      const sortBy = v.sort && v.sort !== 'priority' ? v.sort : null;
      let min = null, max = null;
      if (useDates.checked) {
        if (from.value > to.value) return Kov.UI.toast('Начальная дата позже конечной', 'warning');
        min = from.value + 'T00:00:00'; max = to.value + 'T23:59:59';
      }
      try {
        await Kov.Api.put('task-filters/' + state.archived, { json: { sort_by: sortBy, stage_count: v.stages ? Number(v.stages) : null, min_created_date: min, max_created_date: max } });
        sheet.close(); refresh();
      } catch (e) { Kov.UI.toast('Не удалось сохранить фильтр: ' + Kov.UI.errorText(e), 'error'); }
    } });
    const body = h('div.sheet-form');
    Kov.UI.append(body, [
      Kov.UI.field('Сортировка', sortSel),
      Kov.UI.field('Количество этапов', stages),
      h('label.checkbox', useDates, 'Фильтр по дате создания'), datesRow,
      h('div.sheet-buttons', reset, apply),
    ]);
    sheet = Kov.UI.bottomSheet('Сортировка и фильтры', body);
  }

  // компьютер: прежнее окно «Фильтрация задач»
  function filterModal() {
    const f = state.filter || {};
    const codes = sortCodes();
    const pick = prefix => codes.find(c => c.startsWith(prefix + '_')) || null;
    const NONE = [[null, '—']];
    const idSel = Kov.UI.select(NONE.concat([['id_asc', 'По возрастанию'], ['id_desc', 'По убыванию']]), pick('id'));
    const dlSel = Kov.UI.select(NONE.concat([['deadline_asc', 'По возрастанию'], ['deadline_desc', 'По убыванию']]), pick('deadline'));
    const arSel = Kov.UI.select(NONE.concat([['archived_asc', 'Сначала старые'], ['archived_desc', 'Сначала новые']]), pick('archived'));
    const stages = Kov.UI.select(NONE.concat(Array.from({ length: 10 }, (_, i) => [i + 1, String(i + 1)])), f.stage_count || null);
    const useDates = h('input', { type: 'checkbox', checked: !!(f.min_created_date || f.max_created_date) });
    const from = h('input.input', { type: 'date', value: f.min_created_date ? String(f.min_created_date).slice(0, 10) : Kov.UI.isoDate(new Date(Date.now() - 365 * 86400000)) });
    const to = h('input.input', { type: 'date', value: f.max_created_date ? String(f.max_created_date).slice(0, 10) : Kov.UI.isoDate(new Date()) });
    const datesRow = h('div.form-grid', Kov.UI.field('Создана с', from), Kov.UI.field('Создана по', to));
    datesRow.hidden = !useDates.checked;
    useDates.addEventListener('change', () => { datesRow.hidden = !useDates.checked; });

    Kov.UI.modal({
      title: 'Фильтрация задач', glyph: 'filter',
      // append печатает null словом «null» — собираем список через хелпер
      body: el => { Kov.UI.append(el, [
        Kov.UI.field('Сортировка по ID', idSel), Kov.UI.field('Сортировка по дате окончания', dlSel),
        state.archived ? Kov.UI.field('Сортировка по дате архивации', arSel)
          : h('div.small.muted.sort-hint', 'Если сортировка не выбрана, задачи идут по важности: сначала те, где сейчас этап вашего отдела (просроченные, на сегодня, остальные), затем чужие просроченные и на сегодня, затем все остальные.'),
        Kov.UI.field('Количество этапов', stages),
        h('label.checkbox', useDates, 'Фильтр по дате создания'), datesRow,
      ]); },
      buttons: [
        { text: 'Сбросить', kind: 'ghost', icon: 'refresh', onClick: async close => {
          try { await Kov.Api.post('task-filters/' + state.archived + '/reset'); close(); refresh(); }
          catch (e) { Kov.UI.toast('Не удалось сбросить: ' + Kov.UI.errorText(e), 'error'); }
        } },
        { text: 'Отмена', kind: 'ghost' },
        { text: 'Применить', icon: 'check', onClick: async close => {
          const sortBy = [idSel.getValue(), dlSel.getValue(), state.archived ? arSel.getValue() : null].filter(Boolean).join(',') || null;
          let min = null, max = null;
          if (useDates.checked) {
            if (from.value > to.value) return Kov.UI.toast('Начальная дата позже конечной', 'warning');
            min = from.value + 'T00:00:00'; max = to.value + 'T23:59:59';
          }
          try {
            await Kov.Api.put('task-filters/' + state.archived, { json: { sort_by: sortBy, stage_count: stages.getValue(), min_created_date: min, max_created_date: max } });
            close(); refresh();
          } catch (e) { Kov.UI.toast('Не удалось сохранить фильтр: ' + Kov.UI.errorText(e), 'error'); }
        } },
      ],
    });
  }

  function onEvent(evt) {
    if (!ui || !ui.tableWrap || !ui.tableWrap.isConnected) return;
    const t = evt.type || '';
    // файлы в списке задач не показываются — события о них список не трогают
    if (t.includes('file')) return;
    if (t.startsWith('task.') || (t.startsWith('stage.') && t !== 'stage.description_updated')) {
      clearTimeout(ui.reloadTimer); ui.reloadTimer = setTimeout(() => refresh({ quiet: true }), 300);
    }
  }

  return { view, onEvent, state };
})();
