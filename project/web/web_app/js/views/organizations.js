/* Раздел «Организации»: список с поиском, просмотр в панели справа,
 * создание и редактирование (название, описание, телефоны, e-mail). */
window.Kov = window.Kov || {};
Kov.Views = Kov.Views || {};

Kov.Views.organizations = (function () {
  const { h, icon, button, sectionHeader, field } = Kov.UI;
  const state = { list: [], search: '', selected: null };
  let ui = null;

  /**
   * Править справочник вправе только директор и администратор — так решает
   * сервер. Раньше кнопки показывались и Договорному отделу, а сохранение
   * всегда отбивалось с 403.
   */
  function canEdit() { return Kov.Store.canManageOrganizations(); }

  async function refresh() {
    try { state.list = await Kov.Api.get('organizations') || []; renderTable(); }
    catch (e) { Kov.UI.toast('Не удалось отобразить организации: ' + Kov.UI.errorText(e), 'error'); }
  }

  function renderTable(opts) {
    const needle = state.search.trim().toLowerCase();
    const rows = state.list.filter(o => !needle || (o.name || '').toLowerCase().includes(needle)).sort((a, b) => (a.name || '').localeCompare(b.name || '', 'ru'));
    ui.rows = rows;
    // при чужой правке список не пересобирается целиком и не моргает
    ui.table.fill(rows, { keepDrawn: !!(opts && opts.keep) });
    if (!(opts && opts.keep)) state.selected = null;
    updateButtons();
    ui.empty.hidden = rows.length > 0;
  }

  /**
   * Справочник общий, поэтому чужие правки подтягиваются сами: событие по
   * сокету, возвращение к вкладке, восстановление связи и редкий опрос —
   * на случай, если событие потерялось.
   */
  function watchOrgs() {
    if (watchOrgs.armed) return;
    watchOrgs.armed = true;
    let timer = null;
    const alive = () => ui && ui.table && document.body.contains(ui.table);
    const soft = () => {
      if (!alive()) return;
      clearTimeout(timer);
      timer = setTimeout(async () => {
        if (!alive()) return;
        try { state.list = await Kov.Api.get('organizations') || []; renderTable({ keep: true }); }
        catch (e) { /* попробуем в следующий раз */ }
      }, 400);
    };
    if (Kov.WS && Kov.WS.on) {
      Kov.WS.on(evt => {
        if (evt && String(evt.type || '').startsWith('organization.')) soft();
      });
    }
    if (Kov.WS && Kov.WS.onReconnect) Kov.WS.onReconnect(soft);
    window.addEventListener('focus', soft);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) soft(); });
    setInterval(soft, 60000);
  }
  function updateButtons() {
    const has = !!state.selected;
    ui.edit && (ui.edit.disabled = !has); ui.del && (ui.del.disabled = !has);
  }

  function view(section) {
    ui = {};
    watchOrgs();
    section.classList.add('fill');
    const search = Kov.UI.searchInput('Поиск по организациям…', v => { state.search = v; renderTable(); });
    ui.empty = h('div.empty', { hidden: true }, 'Организаций нет');
    ui.table = Kov.UI.table({
      cardsClass: 'roomy',
      // телефон: в карточке без подписи — столбец один, подпись съедала треть ширины
      columns: [{ key: 'name', label: 'Название организации', align: 'left', noLabel: true, render: o => h('span.name-cell', icon('building'), o.name || '') }],
      rowKey: o => o.id, rowsRef: () => ui.rows || [],
      onSelect: o => { state.selected = o; updateButtons(); }, onActivate: o => openView(o.id),
    });
    // как у задач: на телефоне одной строкой с короткими подписями, главная кнопка последней
    const actions = Kov.UI.fitRow(h('div.btn-row.end.list-actions'), ['icons-only']);
    const twoLabels = (full, short, opts) => {
      const text = document.createDocumentFragment();
      text.append(h('span.lbl-full', full), h('span.lbl-short', short));
      const b = button(text, opts);
      b.setAttribute('aria-label', full);
      return b;
    };
    if (canEdit()) {
      ui.edit = twoLabels('Редактировать', 'Изменить', { kind: 'ghost', icon: 'edit', disabled: true, onClick: () => state.selected && editPage(state.selected.id) });
      ui.del = twoLabels('Удалить', 'Удалить', { kind: 'danger', icon: 'trash', disabled: true, onClick: deleteSelected });
      actions.append(ui.edit, ui.del, twoLabels('Добавить организацию', 'Добавить', { icon: 'plus', onClick: () => editPage(null) }));
    }
    section.append(h('div.toolbar', search.el), h('div.table-wrap', ui.table, ui.empty), actions);
    refresh();
  }

  async function deleteSelected() {
    const o = state.selected; if (!o) return;
    if (!await Kov.UI.confirm('Удаление организации', 'Удалить организацию «' + o.name + '»?', { okText: 'Удалить', danger: true })) return;
    try { await Kov.Api.del('organizations/' + o.id); Kov.UI.toast('Организация удалена', 'success'); refresh(); }
    catch (e) { Kov.UI.toast('Не удалось удалить: ' + Kov.UI.errorText(e), 'error'); }
  }

  // ── просмотр ─────────────────────────────────────────────────────────────

  /** Сервер хранит автора числом — показываем фамилию и имя */
  async function personName(value) {
    if (value === null || value === undefined || value === '') return null;
    if (!/^\d+$/.test(String(value).trim())) return String(value);
    try {
      const list = await Kov.Store.employees();
      const person = list.find(e => e.id === Number(value));
      return person ? Kov.UI.fullName(person, true) : 'Сотрудник №' + value;
    } catch (e) { return 'Сотрудник №' + value; }
  }

  async function openView(id) {
    let o;
    try { o = await Kov.Api.get('organizations/' + id); } catch (e) { return Kov.UI.toast('Не удалось открыть: ' + Kov.UI.errorText(e), 'error'); }
    const createdBy = await personName(o.who_create);
    const editedBy = await personName(o.who_edit);
    const panel = h('div',
      Kov.Shell.panelHeader('Просмотр организации', () => Kov.Shell.closePanel(), canEdit() ? button('', { kind: 'ghost', icon: 'edit', title: 'Редактировать', onClick: () => { Kov.Shell.closePanel(); editPage(o.id); } }) : null),
      h('div.panel-body',
        Kov.UI.infoCard('building', 'Организация', [['Название', o.name], ['Описание', o.description]]),
        Kov.UI.infoCard('phone', 'Номера телефонов', (o.phones || []).map(p => [p.description || 'Телефон', p.phone_number, { copy: true }])),
        Kov.UI.infoCard('mail', 'Email адреса', (o.emails || []).map(m => [m.description || 'E-mail', m.email, { copy: true }])),
        Kov.UI.infoCard('info', 'Информация о записи', [['Создал', createdBy], ['Создано', o.when_create ? Kov.UI.fmtDateTime(o.when_create) : null], ['Изменил', editedBy], ['Изменено', o.when_edit ? Kov.UI.fmtDateTime(o.when_edit) : null]]),
        h('div.btn-row.end', button('Свернуть', { kind: 'ghost', icon: 'arrow_right', onClick: () => Kov.Shell.closePanel() })),
      ));
    Kov.Shell.openPanel(panel);
  }

  // ── создание / редактирование ────────────────────────────────────────────

  async function editPage(id) {
    let o = { name: '', description: '', phones: [], emails: [] };
    if (id) { try { o = await Kov.Api.get('organizations/' + id); } catch (e) { return Kov.UI.toast('Не удалось загрузить: ' + Kov.UI.errorText(e), 'error'); } }
    const name = h('input.input', { value: o.name || '', placeholder: 'Название организации' });
    const description = h('textarea.textarea', { value: o.description || '', placeholder: 'Описание' });
    const phones = h('div', { style: { display: 'flex', flexDirection: 'column', gap: '.5rem' } });
    const emails = h('div', { style: { display: 'flex', flexDirection: 'column', gap: '.5rem' } });

    function row(container, kind, value) {
      const main = h('input.input', { type: kind === 'phone' ? 'tel' : 'email', placeholder: kind === 'phone' ? 'Номер телефона' : 'example@domain.com', value: (kind === 'phone' ? value.phone_number : value.email) || '' });
      const desc = h('input.input', { placeholder: kind === 'phone' ? 'Описание (мобильный, приёмная…)' : 'Описание', value: value.description || '' });
      const line = h('div', { style: { display: 'grid', gridTemplateColumns: 'minmax(8rem, 1fr) minmax(8rem, 1fr) auto', gap: '.4rem' } }, main, desc, button('', { kind: 'danger', icon: 'trash', title: 'Убрать', onClick: () => line.remove() }));
      line.getValue = () => { const v = main.value.trim(); return v ? (kind === 'phone' ? { phone_number: v, description: desc.value.trim() } : { email: v, description: desc.value.trim() }) : null; };
      container.append(line);
    }
    (o.phones || []).forEach(p => row(phones, 'phone', p));
    (o.emails || []).forEach(m => row(emails, 'email', m));

    Kov.Shell.show('organizations', id ? 'Редактирование организации' : 'Создание новой организации', section => {
      section.append(
        h('div.card', { style: { display: 'flex', flexDirection: 'column', gap: '.8rem' } },
          field('Название организации:', name, true), field('Описание:', description),
          field('Номера телефонов:', phones), h('div', button('Добавить телефон', { kind: 'ghost', icon: 'plus', onClick: () => row(phones, 'phone', {}) })),
          field('Email адреса:', emails), h('div', button('Добавить Email', { kind: 'ghost', icon: 'plus', onClick: () => row(emails, 'email', {}) })),
          h('div.btn-row.end',
            button('Отмена', { kind: 'ghost', onClick: () => Kov.Router.go('#/organizations') }),
            button('Сохранить', { icon: 'save', onClick: async () => {
              const value = name.value.trim();
              if (!value) return Kov.UI.toast('Укажите название организации', 'warning');
              const payload = { name: value, description: description.value.trim() || null,
                phones: [...phones.children].map(l => l.getValue()).filter(Boolean), emails: [...emails.children].map(l => l.getValue()).filter(Boolean) };
              try {
                if (id) await Kov.Api.put('organizations/' + id, { json: payload }); else await Kov.Api.post('organizations', { json: payload });
                Kov.UI.toast('Организация сохранена', 'success'); Kov.Router.go('#/organizations');
              } catch (e) { Kov.UI.toast('Не удалось сохранить: ' + Kov.UI.errorText(e), 'error'); }
            } }),
          ),
        ),
      );
    });
  }

  return { view, openView, editPage };
})();
