/* Раздел «Работники» и профиль сотрудника (панель справа, 750px).
 * Таблица ФИО / Должность / Телефон / Отдел, поиск по полю, фильтр по
 * должностям, «Показать профиль», «Добавить работника» (руководство). */
window.Kov = window.Kov || {};
Kov.Views = Kov.Views || {};

Kov.Views.employees = (function () {
  const { h, icon, button, sectionHeader, field } = Kov.UI;
  // Должности «Руководитель» нет: руководят директора. Складом теперь
  // распоряжается отдел «Склад», а не должность «Кладовщик»
  const STANDARD_POSITIONS = ['Директор', 'Администратор', 'Старший сотрудник', 'Сотрудник', 'Главный бухгалтер', 'Бухгалтер', 'Старший монтажник', 'Монтажник', 'Старший инженер', 'Инженер'];
  const STANDARD_DEPARTMENTS = ['Монтажный отдел', 'Бухгалтерия', 'Отдел ТО', 'Договорной отдел', 'Склад', 'Руководство'];
  const HIDDEN_DEPARTMENTS = ['Администрация'];
  const FIELD_TITLES = { last_name: 'фамилия', first_name: 'имя', middle_name: 'отчество',
    position: 'должность', department: 'отдел', status: 'права', phone_number: 'телефон',
    email: 'почта', profile: 'о себе' };
  const SEARCH_MODES = [['fio', 'ФИО'], ['department', 'Отдел'], ['position', 'Должность'], ['phone', 'Номер телефона']];
  const OTHER = '__other__';
  const STATUS_OPTIONS = [['работник', 'Работник'], ['руководитель отдела', 'Руководитель отдела'],
                          ['директор', 'Директор'], ['администратор', 'Администратор']];
  const state = { list: [], search: '', mode: 'fio', positions: new Set(), selected: null };
  let ui = null;

  // ── список ───────────────────────────────────────────────────────────────

  function matches(e) {
    if (state.positions.size && !state.positions.has(e.position || '')) return false;
    const needle = state.search.trim().toLowerCase();
    if (!needle) return true;
    const value = state.mode === 'department' ? e.department : state.mode === 'position' ? e.position
      : state.mode === 'phone' ? e.phone_number : Kov.UI.fullName(e, true);
    return (value || '').toLowerCase().includes(needle);
  }

  function renderTable() {
    // Таблицы может не быть вовсе: профиль открывают и из мессенджера, и по
    // прямой ссылке — раздел со списком в этот момент не построен. Раньше
    // обновление списка после смены фото падало здесь на пустом ui.
    if (!ui || !ui.table) return;
    const rows = state.list.filter(matches).sort((a, b) => Kov.UI.fullName(a, true).localeCompare(Kov.UI.fullName(b, true), 'ru'));
    ui.rows = rows;
    ui.table.fill(rows);
    state.selected = null; ui.profileBtn.disabled = true;
    ui.empty.hidden = rows.length > 0;
    ui.filterBtn.style.background = state.positions.size ? 'var(--warning-soft)' : '';
  }

  async function refresh(force) {
    try {
      const [list] = await Promise.all([Kov.Store.pickable(force), Kov.Store.photoIds(force)]);
      state.list = list;
      renderTable();     // сама проверит, есть ли куда рисовать
    } catch (e) { Kov.UI.toast('Не удалось загрузить работников: ' + Kov.UI.errorText(e), 'error'); }
  }

  function view(section) {
    ui = {};
    section.classList.add('fill');
    const search = Kov.UI.searchInput('Поиск по работникам…', v => { state.search = v; renderTable(); });
    const mode = Kov.UI.select(SEARCH_MODES, state.mode, v => { state.mode = v; renderTable(); }, { title: 'Поле, по которому идёт поиск' });
    ui.filterBtn = button('Должности', { kind: 'ghost', icon: 'filter', title: 'Фильтр по должности', onClick: positionFilter });
    ui.profileBtn = button('Показать профиль', { kind: 'ghost', icon: 'id_card', disabled: true, onClick: () => state.selected && openProfile(state.selected.id) });
    ui.profileBtn.classList.add('only-desktop');   // телефон: касание карточки и так открывает профиль
    const add = Kov.Store.isManagement() ? button('Добавить работника', { icon: 'plus', onClick: addDialog }) : null;
    ui.empty = h('div.empty', { hidden: true }, 'Никого не найдено');
    // телефон: карточка без подписей полей — имя, «должность · отдел», номер ссылкой
    const phone = window.matchMedia('(max-width: 760px)').matches;
    ui.table = Kov.UI.table({
      cardsClass: 'emp-cards',
      columns: [
        { key: 'fio', label: 'ФИО', align: 'left', render: e => h('span.name-cell', Kov.UI.avatar(e, null, Kov.Store.photoUrl(e.id)), Kov.UI.fullName(e, true) || 'Имя не указано') },
        { key: 'position', label: 'Должность', render: e => e.position || (phone ? 'Должность не указана' : 'Не указана') },
        { key: 'phone_number', label: 'Телефон', render: e => phone ? telLink(e.phone_number) : (e.phone_number || 'Не указан') },
        { key: 'department', label: 'Отдел', render: e => e.department || (phone ? 'отдел не указан' : 'Не указан') },
      ],
      rowKey: e => e.id, rowsRef: () => ui.rows || [],
      hrefFor: e => '#/employees/' + e.id,
      onSelect: e => { state.selected = e; ui.profileBtn.disabled = false; },
      onActivate: e => openProfile(e.id),
    });
    // без «Добавить работника» на телефоне строка кнопок пустая — не показываем её
    section.append(h('div.toolbar', search.el, mode, ui.filterBtn), h('div.table-wrap', ui.table, ui.empty), h('div.btn-row.end' + (add ? '' : '.only-desktop'), ui.profileBtn, add));
    refresh();
  }

  /** Номер ссылкой: нажатие открывает набор номера, а не профиль (касание карточки) */
  function telLink(number) {
    const digits = String(number || '').replace(/[^\d+]/g, '');
    if (!digits.replace(/\D/g, '')) return h('span.muted', 'Телефон не указан');
    return h('a.tel', { href: 'tel:' + digits, onclick: e => e.stopPropagation(), 'aria-label': 'Позвонить: ' + number },
      icon('phone'), h('span', number));
  }

  function positionFilter() {
    const positions = [...new Set(state.list.map(e => e.position || '').filter(Boolean))].sort();
    const box = h('div.checklist');
    positions.forEach(p => box.append(h('label', h('input', { type: 'checkbox', checked: state.positions.has(p), value: p }), p)));
    if (!Kov.UI.touchUi()) {
      // компьютер — прежнее окно; телефон и планшет — выезжающее снизу меню
      Kov.UI.modal({
        title: 'Фильтр по должности', glyph: 'filter',
        body: el => el.append(h('div.muted', 'Выберите должности:'), box),
        buttons: [
          { text: 'Сбросить', kind: 'ghost', onClick: close => { state.positions.clear(); renderTable(); close(); } },
          { text: 'Применить', icon: 'check', onClick: close => { state.positions = new Set([...box.querySelectorAll('input:checked')].map(i => i.value)); renderTable(); close(); } },
        ],
      });
      return;
    }
    let sheet = null;
    const reset = Kov.UI.button('Сбросить', { kind: 'ghost', onClick: () => { state.positions.clear(); renderTable(); sheet.close(); } });
    const apply = Kov.UI.button('Применить', { icon: 'check', onClick: () => {
      state.positions = new Set([...box.querySelectorAll('input:checked')].map(i => i.value)); renderTable(); sheet.close(); } });
    // выезжающее снизу меню — как фильтры задач и проектов
    sheet = Kov.UI.bottomSheet('Фильтр по должности', h('div.sheet-form', h('div.muted', 'Выберите должности:'), box, h('div.sheet-buttons', reset, apply)));
  }

  // ── профиль ──────────────────────────────────────────────────────────────

  async function openProfile(employeeId) {
    let e = null;
    try { e = await Kov.Api.get('employees/' + employeeId); } catch (err) { /* карточки может не быть */ }
    if (!e) e = Object.assign({ id: employeeId }, Kov.Store.state.me || {});
    await Kov.Store.photoIds();
    const me = Kov.Store.state.me || {};
    const own = e.id === me.id;
    const management = Kov.Store.isManagement();
    const panel = h('div.profile-panel');
    const photoStamp = { v: Date.now() };

    function render() {
      Kov.UI.clear(panel);
      const photo = Kov.Store.photoUrl(e.id, photoStamp.v);
      const av = Kov.UI.avatar(e, 'xl', photo);
      if (photo) {
        // Фото открывается во весь экран тем же просмотрщиком, что и вложения
        av.classList.add('clickable');
        av.title = 'Открыть фото';
        av.addEventListener('click', () => Kov.Files.viewer([{ src: photo, filename: 'Фото сотрудника', download: photo }], 0));
      }
      const avatarWrap = h('div.avatar-wrap', av);
      if (own || management) {
        // Снимок сначала кадрируется под кружок: человек видит, что попадёт
        // в аватар, и может отказаться, не загружая ничего на сервер
        const pickPhoto = async () => {
          const file = await Kov.PhotoCrop.pick({ title: 'Фото сотрудника' });
          if (!file) return;
          const form = new FormData(); form.append('file', file, file.name);
          try {
            await Kov.Api.post('employees/' + e.id + '/photo', { form });
            await Kov.Store.photoIds(true); photoStamp.v = Date.now(); render();
            Kov.UI.toast('Фото обновлено', 'success');
            if (ui && ui.table) renderTable();
          } catch (err) { Kov.UI.toast('Не удалось загрузить фото: ' + Kov.UI.errorText(err), 'error'); }
        };
        avatarWrap.append(button('', { icon: 'camera', title: 'Сменить фото', cls: 'photo-btn', onClick: pickPhoto }));
      }
      const headActions = h('div.head-actions');
      // Два уровня правки: руководство меняет карточку целиком, остальные —
      // только свои телефон и почту. Кнопка одна, разное за ней.
      const applySaved = updated => { Object.assign(e, updated); render(); refresh(true); };
      if (management) {
        headActions.append(button('', { kind: 'ghost', icon: 'edit', title: 'Изменить данные',
          onClick: () => editDialog(e, applySaved) }));
      } else if (own) {
        headActions.append(button('', { kind: 'ghost', icon: 'edit', title: 'Изменить телефон и почту',
          onClick: () => contactsDialog(e, applySaved) }));
      }
      if (management && !own) headActions.append(button('', { kind: 'ghost', icon: 'trash', title: 'Удалить сотрудника', onClick: () => deleteEmployee(e) }));
      if (own) headActions.append(button('Выход', { kind: 'ghost', icon: 'logout', onClick: () => Kov.App.logout() }));
      headActions.append(button('', { kind: 'ghost', icon: 'close', title: 'Закрыть', onClick: () => Kov.Shell.closePanel() }));

      const subtitle = [e.position, e.department].filter(Boolean).join(' • ');
      panel.append(
        h('div.profile-head',
          h('div.row', h('span.title', own ? 'Мой профиль' : 'Профиль'), h('span', { style: { flex: 1 } }), headActions),
          avatarWrap,
          h('div.name', Kov.UI.fullName(e, true) || 'Имя не указано'),
          subtitle ? h('div.sub', subtitle) : null,
        ),
        h('div.profile-body',
          Kov.UI.infoCard('id_card', 'Личные данные', [['Фамилия', e.last_name], ['Имя', e.first_name], ['Отчество', e.middle_name || '—']]),
          Kov.UI.infoCard('flag', 'Работа', [['Отдел', e.department || '—'], ['Должность', e.position || '—']]),
          Kov.UI.infoCard('phone', 'Контакты', [['Номер телефона', e.phone_number, { copy: true }], ['Электронная почта', e.email, { copy: true }]]),
          e.profile ? Kov.UI.infoCard('note', 'О сотруднике', [['', e.profile]]) : null,
        ),
      );
    }
    render();
    Kov.Shell.openPanel(panel);
  }

  async function deleteEmployee(e) {
    if (!await Kov.UI.confirm('Удаление сотрудника', 'Удалить сотрудника «' + Kov.UI.fullName(e, true) + '»? Его задачи и файлы останутся, учётная запись будет закрыта.', { okText: 'Удалить', danger: true })) return;
    try { await Kov.Api.del('employees/' + e.id); Kov.UI.toast('Сотрудник удалён', 'success'); Kov.Shell.closePanel(); refresh(true); }
    catch (err) { Kov.UI.toast('Не удалось удалить: ' + Kov.UI.errorText(err), 'error'); }
  }

  // ── формы ────────────────────────────────────────────────────────────────

  function personForm(values, opts) {
    values = values || {};
    const positions = [...new Set(STANDARD_POSITIONS.concat(Kov.Store.state.positions || []))];
    const fromServer = (Kov.Store.state.departments || []).filter(Boolean);
    const departments = [...new Set(fromServer.concat(STANDARD_DEPARTMENTS))].filter(d => !HIDDEN_DEPARTMENTS.includes(d));
    const f = {
      last_name: h('input.input', { placeholder: 'Фамилия', value: values.last_name || '' }),
      first_name: h('input.input', { placeholder: 'Имя', value: values.first_name || '' }),
      middle_name: h('input.input', { placeholder: 'Отчество', value: values.middle_name || '' }),
      department: Kov.UI.select(departments.map(d => [d, d]), values.department && departments.includes(values.department) ? values.department : departments[0]),
      position: Kov.UI.select(positions.map(p => [p, p]).concat([[OTHER, 'Ввести другую']]), values.position && positions.includes(values.position) ? values.position : (values.position ? OTHER : positions[0])),
      // Права сотрудника: отдельное поле, а не догадка по названию должности
      status: Kov.UI.select(STATUS_OPTIONS, Kov.Store.statusOf(values)),
      other_position: h('input.input', { placeholder: 'Название должности', value: values.position && !positions.includes(values.position) ? values.position : '' }),
      phone_number: h('input.input', { placeholder: 'Номер телефона', type: 'tel', value: values.phone_number || '' }),
      email: h('input.input', { placeholder: 'example@domain.com', type: 'email', value: values.email || '' }),
      profile: h('textarea.textarea', { placeholder: 'Профиль (описание)', value: values.profile || '' }),
    };
    const otherField = field('Другая должность:', f.other_position);
    const syncOther = () => { otherField.hidden = f.position.getValue() !== OTHER; };
    f.position.addEventListener('change', syncOther); syncOther();
    const el = h('div', { style: { display: 'flex', flexDirection: 'column', gap: '.7rem' } });
    if (opts && opts.credentials) {
      f.username = h('input.input', { placeholder: 'Оставьте пустым — создастся автоматически', autocomplete: 'off', autocapitalize: 'none' });
      f.password = h('input.input', { placeholder: 'Оставьте пустым — сгенерируется', autocomplete: 'new-password' });
      el.append(h('div.form-grid', field('Логин:', f.username), field('Пароль:', f.password)));
    }
    el.append(
      h('div.form-grid', field('Фамилия:', f.last_name, true), field('Имя:', f.first_name, true), field('Отчество:', f.middle_name)),
      h('div.form-grid', field('Отдел:', f.department), field('Должность:', f.position)), otherField,
      h('div.form-grid', field('Права:', f.status),
        h('div.small.muted', { style: { alignSelf: 'end', paddingBottom: '.4rem' } },
          'Руководитель отдела ведёт задачи своего отдела, директор и администратор — все')),
      h('div.form-grid', field('Номер телефона:', f.phone_number), field('Электронная почта:', f.email)),
      field('Профиль (Описание):', f.profile),
    );
    function collect() {
      const position = f.position.getValue() === OTHER ? f.other_position.value.trim() : f.position.getValue();
      return {
        last_name: f.last_name.value.trim(), first_name: f.first_name.value.trim(), middle_name: f.middle_name.value.trim() || null,
        department: f.department.getValue(), position, status: f.status.getValue(),
        phone_number: f.phone_number.value.trim() || null,
        email: f.email.value.trim() || null, profile: f.profile.value.trim() || null,
      };
    }
    function validate(data) {
      if (!data.last_name || !data.first_name) return 'Фамилия и имя обязательны';
      if (!data.position) return "Поле 'Другая должность' не может быть пустым";
      if (data.email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(data.email)) return 'Проверьте адрес электронной почты';
      return null;
    }
    return { el, f, collect, validate };
  }

  function generatePassword(length) {
    const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789';
    const buf = new Uint32Array(length || 8); crypto.getRandomValues(buf);
    return Array.from(buf, n => chars[n % chars.length]).join('');
  }

  async function addDialog() {
    await Kov.Store.lists();
    const form = personForm({}, { credentials: true });
    Kov.UI.modal({
      title: 'Добавить работника', glyph: 'user', wide: true,
      body: el => el.append(form.el),
      buttons: [
        { text: 'Отмена', kind: 'ghost' },
        { text: 'Создать', icon: 'check', onClick: async close => {
          const data = form.collect();
          const error = form.validate(data); if (error) return Kov.UI.toast(error, 'warning');
          const username = form.f.username.value.trim() || ('user' + Math.floor(Date.now() / 1000));
          const password = form.f.password.value.trim() || generatePassword(8);
          try {
            await Kov.Api.post('employees', { json: Object.assign({ username, password }, data) });
            close(); credentialsDialog(username, password); refresh(true);
          } catch (e) { Kov.UI.toast('Не удалось создать: ' + Kov.UI.errorText(e), 'error'); }
        } },
      ],
    });
  }

  function credentialsDialog(username, password) {
    const row = (label, value) => h('div.info-row', h('span.k', label), h('span.v.copy', { text: value, title: 'Скопировать', onclick: () => Kov.UI.copyText(value) }));
    Kov.UI.modal({
      title: 'Учетные данные нового пользователя', glyph: 'key', dismissible: false,
      body: el => el.append(h('p.muted', 'Передайте сотруднику. Пароль показывается только сейчас.'), h('div.info-card', row('Логин', username), row('Пароль', password))),
      buttons: [{ text: 'Скопировать оба', kind: 'ghost', icon: 'copy', onClick: () => Kov.UI.copyText('Логин: ' + username + '\nПароль: ' + password) }, { text: 'Готово', icon: 'check' }],
    });
  }

  /**
   * Частичная правка профиля: телефон и электронная почта.
   *
   * Должность, отдел и права человек себе не меняет — иначе смысл прав
   * теряется. Перед сохранением показываем, что именно изменилось: правка
   * своих контактов дело редкое, и опечатку легко не заметить.
   */
  function contactsDialog(e, onSaved) {
    const phone = h('input.input', { value: e.phone_number || '', placeholder: '+7 (999) 000-00-00' });
    const email = h('input.input', { type: 'email', value: e.email || '', placeholder: 'name@example.com' });
    Kov.UI.modal({
      title: 'Мои контакты', glyph: 'id_card',
      body: el => el.append(h('div',
        h('p.small.muted', 'Изменить можно только телефон и почту. Должность, отдел и права '
          + 'меняет директор или администратор.'),
        Kov.UI.field('Телефон', phone),
        Kov.UI.field('Электронная почта', email))),
      buttons: [
        { text: 'Отмена', kind: 'ghost' },
        { text: 'Сохранить', icon: 'save', onClick: async close => {
          const data = { phone_number: phone.value.trim(), email: email.value.trim() };
          if (data.email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(data.email)) {
            return Kov.UI.toast('Проверьте адрес почты', 'warning');
          }
          const changes = [];
          if (data.phone_number !== (e.phone_number || '')) {
            changes.push('телефон: ' + (e.phone_number || '—') + ' → ' + (data.phone_number || '—'));
          }
          if (data.email !== (e.email || '')) {
            changes.push('почта: ' + (e.email || '—') + ' → ' + (data.email || '—'));
          }
          if (!changes.length) { close(); return Kov.UI.toast('Ничего не изменилось', 'info'); }
          const ok = await Kov.UI.confirm('Сохранить изменения?', changes.join('\n'),
            { okText: 'Сохранить', icon: 'save' });
          if (!ok) return;
          try {
            const updated = await Kov.Api.put('employees/' + e.id, { json: data });
            close(); Kov.UI.toast('Контакты обновлены', 'success');
            if (e.id === Kov.Store.state.me.id) Object.assign(Kov.Store.state.me, updated);
            onSaved && onSaved(updated);
          } catch (err) { Kov.UI.toast('Не удалось сохранить: ' + Kov.UI.errorText(err), 'error'); }
        } },
      ],
    });
  }

  async function editDialog(e, onSaved) {
    await Kov.Store.lists();
    const form = personForm(e);
    Kov.UI.modal({
      title: 'Редактировать профиль', glyph: 'edit', wide: true,
      body: el => el.append(form.el),
      buttons: [
        { text: 'Отмена', kind: 'ghost' },
        { text: 'Сохранить', icon: 'save', onClick: async close => {
          const data = form.collect();
          const error = form.validate(data); if (error) return Kov.UI.toast(error, 'warning');
          // Спрашиваем перед записью: правка чужой карточки меняет и права
          const diff = Object.keys(data).filter(k => String(data[k] ?? '') !== String(e[k] ?? ''));
          if (!diff.length) { close(); return Kov.UI.toast('Ничего не изменилось', 'info'); }
          const ok = await Kov.UI.confirm('Сохранить изменения?',
            'Будут изменены поля: ' + diff.map(k => FIELD_TITLES[k] || k).join(', '),
            { okText: 'Сохранить', icon: 'save' });
          if (!ok) return;
          try {
            const updated = await Kov.Api.put('employees/' + e.id, { json: data });
            close(); Kov.UI.toast('Данные профиля успешно обновлены.', 'success');
            if (e.id === Kov.Store.state.me.id) Object.assign(Kov.Store.state.me, updated);
            onSaved && onSaved(updated);
          } catch (err) { Kov.UI.toast('Не удалось сохранить: ' + Kov.UI.errorText(err), 'error'); }
        } },
      ],
    });
  }

  return { view, openProfile, refresh, __test_form: personForm };
})();
