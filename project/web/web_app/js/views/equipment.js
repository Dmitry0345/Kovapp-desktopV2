/* Раздел «Оборудование»: ведомость предприятия, выдача людям и возврат.
 *
 * Чем отличается от склада проекта. Складская ведомость привязана к объекту и
 * свёрстана таблицей на сто строк, как бумажная. Здесь оборудование числится
 * за предприятием, список свободный, а у каждой позиции своя жизнь: кто взял,
 * сколько осталось, что с возвратом. Поэтому вместо таблицы — карточки: в
 * строку таблицы всё это не помещается, а на телефоне не помещается и половина.
 *
 * Каждая карточка показывает полосу занятости (выдано и остаток), людей, за
 * кем числится оборудование, и раскрывающийся журнал: сверху выдачи и
 * возвраты, ниже правки значений.
 *
 * Изменения приходят по сокету и заменяют одну карточку, а не весь список:
 * у всех, кто смотрит раздел, цифры меняются на месте и одновременно.
 */
window.Kov = window.Kov || {};
Kov.Views = Kov.Views || {};

Kov.Views.equipment = (function () {
  const { h, icon, button, sectionHeader } = Kov.UI;

  const S = { rows: [], units: [], access: null, search: '', filter: 'all',
              ui: null, open: new Set(), busy: new Set() };

  const FILTERS = [
    ['all', 'Всё'],
    ['out', 'На руках'],
    ['mine', 'У меня'],
    ['wait', 'Ждут ответа'],
  ];

  function num(v) { return v === null || v === undefined ? '—' : String(Math.round(v * 1000) / 1000); }
  function unitOf(row) { return row.unit ? ' ' + row.unit : ''; }
  function can(flag) { return !!(S.access && S.access[flag]); }

  // ── загрузка и обновление ───────────────────────────────────────────────

  async function load() {
    const data = await Kov.Api.get('equipment');
    S.rows = data.rows || [];
    S.units = data.units || [];
    S.access = data.access || {};
  }

  /** Событие сокета: меняем одну карточку на месте, список не перечитываем */
  function onEvent(evt) {
    if (!evt || !S.ui || !String(evt.type || '').startsWith('equipment.')) return;
    if (evt.type === 'equipment.removed') {
      const card = S.ui.list.querySelector('[data-item="' + evt.item_id + '"]');
      S.rows = S.rows.filter(r => r.id !== evt.item_id);
      if (card) { card.classList.add('is-leaving'); setTimeout(() => render(), 260); }
      else render();
      return;
    }
    if (evt.type !== 'equipment.row' || !evt.row) return;
    const at = S.rows.findIndex(r => r.id === evt.row.id);
    // «мои» выдачи считает сервер для того, кто спрашивал: у события другой
    // получатель, поэтому эти пометки пересчитываем на месте
    const row = markMine(evt.row);
    if (at < 0) S.rows.push(row); else S.rows[at] = row;
    render(evt.actor_id !== Kov.Api.state.userId ? evt.row.id : null);
  }

  function markMine(row) {
    const me = Kov.Api.state.userId;
    (row.issues || []).forEach(i => {
      i.mine = !!(i.receiver && i.receiver.id === me);
      i.issued_by_me = !!(i.issuer && i.issuer.id === me);
    });
    return row;
  }

  // ── действия ────────────────────────────────────────────────────────────

  async function act(key, fn, okText) {
    if (S.busy.has(key)) return;
    S.busy.add(key);
    render();
    try {
      await fn();
      if (okText) Kov.UI.toast(okText, 'success');
    } catch (e) {
      Kov.UI.toast(Kov.UI.errorText(e), 'error');
    } finally {
      S.busy.delete(key);
      await load().catch(() => {});
      render();
    }
  }

  function patchRow(row, fields, values) {
    return Kov.Api.patch('equipment/' + row.id, { json: Object.assign({ fields }, values) });
  }

  // ── карточка ────────────────────────────────────────────────────────────

  /** Полоса занятости: сколько на руках и сколько свободно */
  function gauge(row) {
    const total = row.quantity || 0;
    const share = total > 0 ? Math.min(100, Math.round((row.issued / total) * 100)) : 0;
    const bar = h('div.eq-gauge', { title: 'На руках ' + num(row.issued) + ' из ' + num(total) },
      h('div.eq-gauge-fill'));
    // ширину ставим следующим кадром — иначе полоса появляется уже заполненной
    requestAnimationFrame(() => { bar.firstChild.style.width = share + '%'; });
    if (share >= 100) bar.classList.add('is-full');
    else if (share >= 70) bar.classList.add('is-high');
    return bar;
  }

  function numberBlock(label, value, cls) {
    return h('div.eq-num' + (cls ? '.' + cls : ''),
      h('span.eq-num-value', value), h('span.eq-num-label', label));
  }

  /** Значение, которое правится по нажатию — если права позволяют */
  function editable(row, field, text, opts) {
    opts = opts || {};
    if (!can('can_edit_goods')) {
      return h('span.eq-value' + (opts.cls ? '.' + opts.cls : ''), text);
    }
    const el = h('button.eq-value.is-editable' + (opts.cls ? '.' + opts.cls : ''),
      { type: 'button', title: 'Изменить' }, h('span', text), icon('edit'));
    el.onclick = () => editField(row, field, opts);
    return el;
  }

  function editField(row, field, opts) {
    const isNumber = field === 'quantity';
    const input = h('input.input', {
      type: isNumber ? 'number' : 'text',
      value: row[field] === null || row[field] === undefined ? '' : String(row[field]),
      placeholder: opts.placeholder || '',
    });
    if (isNumber) { input.min = '0'; input.step = 'any'; }
    Kov.UI.modal({
      title: opts.title || 'Изменение', glyph: 'edit',
      body: el => el.append(h('div',
        Kov.UI.field(opts.title || '', input),
        field === 'quantity' && row.issued > 0
          ? h('p.eq-hint', icon('info'), h('span', 'На руках ' + num(row.issued) + ' — меньше поставить нельзя'))
          : null)),
      buttons: [
        { text: 'Отмена', kind: 'ghost' },
        { text: 'Сохранить', icon: 'check', primary: true, onClick: close => {
          const raw = input.value.trim();
          const value = isNumber ? (raw === '' ? null : Number(raw)) : raw;
          if (isNumber && value !== null && !(value >= 0)) return Kov.UI.toast('Количество не может быть отрицательным', 'warning');
          close();
          act('edit' + row.id + field, () => patchRow(row, [field], { [field]: value }));
        } },
      ],
    });
  }

  /** Строка одной выдачи: состояние и кнопки, доступные именно этому человеку */
  function issueLine(row, issue) {
    const who = (issue.receiver && issue.receiver.name) || 'сотрудник';
    const qty = num(issue.quantity) + unitOf(row);
    const actions = h('div.eq-issue-actions');

    let state = '';
    if (issue.status === 'pending') state = 'ждёт подтверждения';
    else if (issue.status === 'issued') state = 'на руках';
    else if (issue.status === 'return_pending') state = 'возвращает — нужна приёмка';

    const busy = key => S.busy.has(key);
    const add = (text, opts, key, fn) => {
      const b = button(text, Object.assign({ onClick: () => act(key, fn) }, opts));
      if (busy(key)) { b.disabled = true; b.classList.add('is-busy'); }
      actions.append(b);
    };

    if (issue.status === 'pending' && issue.mine) {
      add('Принять', { icon: 'check' }, 'c' + issue.id,
        () => Kov.Api.post('equipment/issues/' + issue.id + '/confirm'));
      add('Отказаться', { icon: 'close', kind: 'ghost' }, 'd' + issue.id,
        () => Kov.Api.post('equipment/issues/' + issue.id + '/decline'));
    } else if (issue.status === 'pending' && (issue.issued_by_me || can('is_management'))) {
      add('Отменить', { icon: 'close', kind: 'ghost' }, 'd' + issue.id,
        () => Kov.Api.post('equipment/issues/' + issue.id + '/decline'));
    }
    if (issue.status === 'issued' && issue.mine) {
      add('Вернуть', { icon: 'undo' }, 'r' + issue.id,
        () => Kov.Api.post('equipment/issues/' + issue.id + '/return'));
    }
    if (issue.status === 'return_pending' && can('can_accept_return')) {
      add('Принять возврат', { icon: 'check' }, 'ra' + issue.id,
        () => Kov.Api.post('equipment/issues/' + issue.id + '/return/accept'));
      add('Отклонить', { icon: 'close', kind: 'ghost' }, 'rd' + issue.id,
        () => Kov.Api.post('equipment/issues/' + issue.id + '/return/decline'));
    }

    // Element.append() превращает null в текст «null» — в отличие от h().
    // Поэтому собираем детей через h(), он пустые пропускает.
    return h('div.eq-issue.st-' + issue.status,
      Kov.UI.avatar(issue.receiver || {}, null, issue.receiver ? Kov.Store.photoUrl(issue.receiver.id) : null),
      h('div.eq-issue-body',
        h('div.eq-issue-who', who, issue.mine ? h('span.eq-tag', 'вы') : null),
        h('div.eq-issue-meta', qty, state ? h('span.eq-state', state) : null,
          issue.issuer ? h('span.muted', 'выдал ' + issue.issuer.name) : null)),
      actions.children.length ? actions : null);
  }

  function issueDialog(row) {
    let receiver = null;
    const qty = h('input.input', { type: 'number', value: '1', min: '0', step: 'any' });
    const who = h('div.eq-receiver', h('span.muted', 'Сотрудник не выбран'));
    const free = (row.quantity || 0) - (row.issued || 0);

    const pick = button('Выбрать сотрудника', { kind: 'ghost', icon: 'users', onClick: () => {
      Kov.Views.messenger.employeePicker('Кому выдаём оборудование', ids => {
        Kov.Store.pickable().then(list => {
          receiver = list.find(e => e.id === ids[0]) || { id: ids[0] };
          Kov.UI.clear(who);
          who.append(Kov.UI.avatar(receiver, null, Kov.Store.photoUrl(receiver.id)),
            h('span', Kov.UI.fullName(receiver, true),
              h('span.muted.small', ' — ' + [receiver.department, receiver.position].filter(Boolean).join(', '))));
        });
      }, false, { okText: 'Выбрать' });
    } });

    Kov.UI.modal({
      title: 'Выдача «' + row.name + '»', glyph: 'users',
      body: el => el.append(
        h('p.eq-hint', icon('info'), h('span', 'Свободно ' + num(free) + unitOf(row)
          + '. Получатель подтвердит получение у себя в разделе')),
        Kov.UI.field('Кому', h('div.eq-receiver-row', who, pick)),
        Kov.UI.field('Сколько', qty)),
      buttons: [
        { text: 'Отмена', kind: 'ghost' },
        { text: 'Выдать', icon: 'check', primary: true, onClick: close => {
          const value = Number(qty.value);
          if (!receiver) return Kov.UI.toast('Выберите получателя', 'warning');
          if (!(value > 0)) return Kov.UI.toast('Количество должно быть больше нуля', 'warning');
          if (value > free) return Kov.UI.toast('Свободно только ' + num(free), 'warning');
          close();
          act('issue' + row.id, () => Kov.Api.post('equipment/' + row.id + '/issue',
            { json: { receiver_id: receiver.id, quantity: value } }), 'Выдача отправлена на подтверждение');
        } },
      ],
    });
  }

  /**
   * Журнал позиции — отдельным окном, как на складе проекта.
   *
   * Внутри карточки он занимал место и на телефоне уводил строку вниз на
   * два экрана. Окно одно на все разделы: там же поиск по сотруднику и
   * разделение на выдачи и правки значений.
   */
  function openJournal(row) {
    Kov.Journal.open({
      title: 'Журнал позиции',
      subtitle: row.name || 'Без названия',
      alwaysIssues: true,
      load: async () => {
        const data = await Kov.Api.get('equipment/' + row.id + '/history');
        const unit = unitOf(row);
        return {
          issues: (data.issues || []).map(r => ({
            kind: 'issue', action: r.action, who: r.user, target: r.target, at: r.created_at,
            title: (r.new_value ? r.new_value + unit + ' · ' : '') + (r.action_title || ''),
            text: [r.user && r.user.name, r.target ? '→ ' + r.target.name : null].filter(Boolean).join(' '),
          })),
          edits: (data.cells || []).map(r => ({
            kind: 'edit', action: r.action, who: r.user, at: r.created_at,
            title: r.action === 'created' ? 'Позиция заведена' : (r.field_title || 'Изменение'),
            text: r.action === 'created' ? '' :
              (r.old_value === null || r.old_value === '' ? '—' : r.old_value) + ' → '
              + (r.new_value === null || r.new_value === '' ? '—' : r.new_value),
          })),
        };
      },
    });
  }

  /**
   * Снимок позиции. На складе одинаковых названий много, и фотография
   * узнаётся быстрее строки. Кто ведёт наименование, тот и меняет снимок:
   * нажатие на пустое место открывает выбор, на готовый снимок — просмотр.
   */
  function photoCell(row) {
    const box = h('div.eq-photo' + (row.has_photo ? '' : '.is-empty'));
    if (row.has_photo) {
      const img = h('img', { src: Kov.Api.fileUrl('equipment/' + row.id + '/photo') + '&v=' + (row.updated_at || ''),
        alt: row.name || 'Оборудование', loading: 'lazy' });
      img.onerror = () => { box.classList.add('is-empty'); img.remove(); box.append(icon('image')); };
      box.append(img);
      box.title = 'Открыть снимок';
      box.onclick = () => Kov.Files.viewer([{ src: img.src, filename: row.name || 'Оборудование', download: img.src }], 0);
    } else {
      // снимка нет — место не кликабельно: добавить можно кнопкой в углу
      box.append(icon('image'));
      box.title = 'Снимка нет';
    }
    if (can('can_edit_goods')) {
      const edit = button('', { kind: 'ghost', icon: 'camera',
        title: row.has_photo ? 'Заменить снимок' : 'Добавить снимок' });
      edit.classList.add('eq-photo-btn');
      edit.onclick = e => { e.stopPropagation(); uploadPhoto(row); };
      box.append(edit);
    }
    return box;
  }

  async function uploadPhoto(row) {
    const file = await Kov.PhotoCrop.pick({ title: 'Снимок «' + (row.name || 'позиция') + '»', round: false });
    if (!file) return;
    const form = new FormData();
    form.append('file', file, file.name);
    act('photo' + row.id, () => Kov.Api.post('equipment/' + row.id + '/photo', { form }), 'Снимок обновлён');
  }

  /**
   * Правая половина строки, когда оборудование никому не выдано.
   *
   * Раньше там висела одна серая надпись, и на широком экране строка
   * выглядела наполовину пустой. Теперь это состояние объясняет, что
   * позиция целиком на месте, и подсказывает следующий шаг.
   */
  function emptyState(row) {
    const box = h('div.eq-idle');
    if (!row.ready) {
      box.classList.add('is-draft');
      box.append(h('span.circ', icon('warning')),
        h('div.eq-idle-text', h('b', 'Позиция не заполнена'),
          h('span', can('can_edit_goods')
            ? 'Укажите название и количество — после этого её можно будет выдавать'
            : 'Название и количество заполняет бухгалтерия')));
      return box;
    }
    box.append(h('span.circ', icon('archive')),
      h('div.eq-idle-text',
        h('b', 'Всё на месте' + (row.quantity ? ' — ' + num(row.quantity) + unitOf(row) : '')),
        h('span', can('can_issue')
          ? 'Никому не выдано. Нажмите «Выдать», чтобы передать сотруднику'
          : 'Никому не выдано')));
    return box;
  }

  function card(row, flash) {
    const held = (row.issues || []).filter(i => i.status !== 'pending');
    const waiting = (row.issues || []).filter(i => i.status === 'pending');
    const needsMe = (row.issues || []).some(i =>
      (i.status === 'pending' && i.mine) || (i.status === 'return_pending' && can('can_accept_return')));

    const el = h('div.eq-card', { 'data-item': String(row.id) });
    if (flash) { el.classList.add('is-flash'); setTimeout(() => el.classList.remove('is-flash'), 1200); }
    if (needsMe) el.classList.add('needs-me');

    const actions = h('div.eq-card-actions');
    if (can('can_issue') && row.ready && (row.left === null || row.left > 0)) {
      actions.append(button('Выдать', { icon: 'send', onClick: () => issueDialog(row) }));
    } else if (can('can_issue') && !row.ready) {
      // Кнопка не появляется, пока строка не заполнена — объясняем почему
      actions.append(h('span.eq-locked', { title: 'Кнопка выдачи появится, когда заполнены оборудование и количество' }, icon('key'), h('span', 'Строка не заполнена')));
    } else if (can('can_issue')) {
      actions.append(h('span.eq-locked', { title: 'Свободных единиц нет — выдавать нечего' }, icon('key'), h('span', 'Всё на руках')));
    }
    if (can('can_edit_goods')) {
      actions.append(button('Изменить', { icon: 'edit', kind: 'ghost', title: 'Название, количество и единица',
        onClick: () => editDialog(row) }));
      actions.append(button('', { icon: 'trash', kind: 'ghost', title: 'Удалить позицию',
        onClick: () => Kov.UI.confirm('Удалить позицию?', 'Строка «' + (row.name || '') + '» и её журнал будут удалены.')
          .then(yes => { if (yes) act('del' + row.id, () => Kov.Api.del('equipment/' + row.id), 'Позиция удалена'); }) }));
    }

    const toggle = button('Журнал', { kind: 'ghost', icon: 'clock',
      title: 'Кто и когда выдавал и менял строку', onClick: () => openJournal(row) });

    el.append(
      photoCell(row),
      h('div.eq-card-head',
        h('div.eq-title',
          editable(row, 'name', row.name || 'Без названия', { title: 'Оборудование', cls: row.name ? '' : 'is-empty', placeholder: 'Например, перфоратор' })),
        actions),
      h('div.eq-numbers',
        h('div.eq-num',
          editable(row, 'quantity', num(row.quantity), { title: 'Количество', cls: 'eq-num-value' }),
          h('span.eq-num-label', 'Количество' + (row.unit ? ', ' + row.unit : ''))),
        numberBlock('На руках', num(row.issued), 'is-issued'),
        numberBlock('Осталось', num(row.left), row.left === 0 ? 'is-zero' : 'is-left')),
      gauge(row),
      held.length || waiting.length
        ? h('div.eq-issues', waiting.concat(held).map(i => issueLine(row, i)))
        : emptyState(row),
      h('div.eq-card-foot', toggle,
        row.updated_by ? h('span.muted.small', 'правил ' + row.updated_by.name + ' · ' + Kov.UI.fmtDate(row.updated_at)) : null));
    return el;
  }

  /** Телефон: компактная строка — снимок, название, «на складе · на руках»; касание — полная карточка в шторке */
  function compactRow(row, flash) {
    const needsMe = (row.issues || []).some(i =>
      (i.status === 'pending' && i.mine) || (i.status === 'return_pending' && can('can_accept_return')));
    const thumb = h('span.eq-row-thumb');
    if (row.has_photo) {
      const img = h('img', { src: Kov.Api.fileUrl('equipment/' + row.id + '/photo') + '&v=' + (row.updated_at || ''), alt: '', loading: 'lazy' });
      img.onerror = () => { img.remove(); thumb.append(icon('image')); };
      thumb.append(img);
    } else thumb.append(icon('image'));
    const el = h('button.eq-row', { type: 'button', 'data-item': String(row.id) }, thumb,
      h('span.eq-row-main',
        h('span.eq-row-name', row.name || 'Без названия'),
        h('span.eq-row-nums', 'на складе ', h('b', num(row.left)), ' · на руках ', h('b', num(row.issued)))),
      needsMe ? h('span.eq-row-dot', { title: 'Ждёт вашего ответа' }) : null,
      icon('chevron_right'));
    if (needsMe) el.classList.add('needs-me');
    if (flash) { el.classList.add('is-flash'); setTimeout(() => el.classList.remove('is-flash'), 1200); }
    el.onclick = () => openRowSheet(row.id);
    return el;
  }

  /** Полная карточка позиции в шторке; при изменении данных — обновляется на месте */
  function openRowSheet(id) {
    const row = S.rows.find(r => r.id === id);
    if (!row) return;
    const body = h('div.eq-sheet-card', card(row));
    S.sheetRow = { id, body, sign: rowSign(row) };
    Kov.UI.bottomSheet(row.name || 'Позиция', body, { onClose: () => { S.sheetRow = null; } });
  }

  // ── отрисовка ───────────────────────────────────────────────────────────

  function visibleRows() {
    const q = S.search.trim().toLowerCase();
    return S.rows.filter(r => {
      if (q && !String(r.name || '').toLowerCase().includes(q)) return false;
      if (S.filter === 'out') return (r.issued || 0) > 0;
      if (S.filter === 'mine') return (r.issues || []).some(i => i.mine && i.status !== 'pending');
      if (S.filter === 'wait') return (r.issues || []).some(i => i.status === 'pending' || i.status === 'return_pending');
      return true;
    });
  }

  /** Отпечаток строки: пока он тот же, карточку не пересобираем */
  function rowSign(row) {
    return [row.name, row.quantity, row.unit, row.issued, row.left, row.ready,
      row.has_photo ? 1 : 0, row.updated_at,
      (row.issues || []).map(i => [i.id, i.status, i.quantity, i.mine ? 1 : 0].join('-')).join(','),
      S.busy.size ? [...S.busy].join(',') : '',
    ].join('|');
  }

  function render(flashId) {
    if (!S.ui) return;
    const rows = visibleRows();
    // Не очищаем список: карточки сверяются по номеру позиции и
    // переиспользуются. Иначе при каждой выдаче и каждом чужом изменении
    // раздел моргал целиком, а снимки перезагружались.
    Kov.UI.syncList(S.ui.list, rows, {
      key: r => r.id,
      sign: r => rowSign(r) + (flashId === r.id ? '|flash' : ''),
      make: (r, i) => {
        const el = Kov.UI.touchUi() ? compactRow(r, flashId === r.id) : card(r, flashId === r.id);
        el.style.setProperty('--eq-i', String(Math.min(i, 12)));
        return el;
      },
    });
    // открытая шторка позиции — свежая карточка без закрытия и моргания
    if (S.sheetRow) {
      const r = S.rows.find(x => x.id === S.sheetRow.id);
      if (r && rowSign(r) !== S.sheetRow.sign) {
        S.sheetRow.sign = rowSign(r);
        Kov.UI.clear(S.sheetRow.body);
        S.sheetRow.body.append(card(r));
      }
    }
    S.ui.empty.hidden = rows.length > 0;
    S.ui.empty.textContent = S.rows.length
      ? 'По этому запросу ничего нет'
      : 'Список пуст' + (can('can_edit_goods') ? ' — добавьте первую позицию' : '');
    const mine = S.rows.reduce((n, r) => n + (r.issues || []).filter(i => i.mine && i.status !== 'pending').length, 0);
    S.ui.count.textContent = 'Позиций: ' + S.rows.length + (mine ? ' · за вами ' + mine : '');
  }

  /**
   * Выбор единицы — тот же элемент, что в складе проекта: кнопка и
   * всплывающий список с поиском. Обычный select тут не годится, единиц в
   * справочнике со временем много, и в нём нельзя ни искать, ни показать
   * полное название.
   */
  function unitPicker(value) {
    let picked = value || '';
    const btn = h('button.wh-unit-btn', { type: 'button', title: 'Выбрать единицу измерения' },
      h('span.v', picked || '—'), icon('chevron_down'));

    function open() {
      document.querySelectorAll('.wh-unit-pop').forEach(x => x.remove());
      const search = h('input.input.wh-unit-search', { placeholder: 'Поиск единицы…', autocomplete: 'off' });
      const list = h('div.wh-unit-list');
      const pop = h('div.wh-unit-pop', h('div.wh-unit-pop-head', search), list);

      function render(filter) {
        Kov.UI.clear(list);
        const needle = (filter || '').trim().toLowerCase();
        const found = S.units.filter(u => !needle
          || u.name.toLowerCase().includes(needle)
          || (u.full_name || '').toLowerCase().includes(needle));
        list.append(h('button.wh-unit-item' + (picked ? '' : '.on'),
          { type: 'button', onclick: () => pick('') }, h('b', '—'), h('span', 'без единицы')));
        found.forEach(u => list.append(h('button.wh-unit-item' + (u.name === picked ? '.on' : ''),
          { type: 'button', onclick: () => pick(u.name) }, h('b', u.name), h('span', u.full_name || ''))));
        if (!found.length) list.append(h('div.empty', 'Ничего не найдено'));
      }
      function pick(name) { picked = name; btn.querySelector('.v').textContent = name || '—'; close(); }
      function close() {
        pop.remove();
        document.removeEventListener('pointerdown', outside, true);
        window.removeEventListener('resize', onResize);
      }
      function outside(e) { if (!pop.contains(e.target) && e.target !== btn) close(); }
    // открылась клавиатура — окно стало ниже, но список остаётся; закрываем только при повороте экрана
    const w0 = window.innerWidth;
    function onResize() { if (Math.abs(window.innerWidth - w0) > 40) close(); }

      search.addEventListener('input', () => render(search.value));
      search.addEventListener('keydown', e => {
        if (e.key === 'Escape') close();
        if (e.key === 'Enter') { const first = list.querySelector('.wh-unit-item'); if (first) first.click(); }
      });
      render('');
      // Список кладём в конец страницы и ставим по месту кнопки: внутри окна
      // его обрезал бы край прокручиваемой области
      document.body.append(pop);
      const box = btn.getBoundingClientRect();
      const height = Math.min(pop.offsetHeight || 260, window.innerHeight - 20);
      const below = window.innerHeight - box.bottom > height + 8;
      pop.style.width = Math.max(box.width, 200) + 'px';
      pop.style.left = Math.max(8, Math.min(box.left, window.innerWidth - pop.offsetWidth - 8)) + 'px';
      pop.style.top = (below ? box.bottom + 4 : Math.max(8, box.top - height - 4)) + 'px';
      setTimeout(() => { document.addEventListener('pointerdown', outside, true); Kov.UI.softFocus(search); }, 10);
      window.addEventListener('resize', onResize);
    }
    btn.addEventListener('click', open);
    return { el: h('div.wh-unit-field', btn), value: () => picked };
  }

  /**
   * Правка записи целиком: название, количество и единица в одном окне.
   *
   * Значения меняются и нажатием на них в карточке, но это находят не сразу,
   * а бухгалтерии количество приходится править постоянно. Поэтому у неё
   * есть и обычная кнопка.
   */
  function editDialog(row) {
    const name = h('input.input', { value: row.name || '', placeholder: 'Например, перфоратор' });
    const qty = h('input.input', { type: 'number', min: '0', step: 'any',
      value: row.quantity === null || row.quantity === undefined ? '' : String(row.quantity) });
    const unit = unitPicker(row.unit || '');
    const picker = photoPicker(row);
    Kov.UI.modal({
      title: 'Изменение позиции', glyph: 'edit',
      body: el => el.append(h('div',
        row.issued > 0
          ? h('p.eq-hint', icon('info'), h('span', 'На руках ' + num(row.issued) + unitOf(row)
            + ' — меньше этого количество поставить нельзя'))
          : null,
        Kov.UI.field('Оборудование', name, true),
        Kov.UI.field('Количество', qty, true),
        Kov.UI.field('Единица', unit.el),
        Kov.UI.field('Снимок', picker))),
      buttons: [
        { text: 'Отмена', kind: 'ghost' },
        { text: 'Сохранить', icon: 'check', onClick: async close => {
          const title = name.value.trim();
          if (!title) return Kov.UI.toast('Укажите название', 'warning');
          const value = qty.value.trim() === '' ? null : Number(qty.value);
          if (value !== null && !(value >= 0)) return Kov.UI.toast('Количество не может быть отрицательным', 'warning');
          if (value !== null && value < row.issued) {
            return Kov.UI.toast('На руках ' + num(row.issued) + ' — меньше поставить нельзя', 'warning');
          }
          const next = { name: title, quantity: value, unit: unit.value() };
          const fields = Object.keys(next).filter(k =>
            String(next[k] === null ? '' : next[k]) !== String(row[k] === null || row[k] === undefined ? '' : row[k]));
          const photoChanged = picker.__state.file || picker.__state.drop;
          if (!fields.length && !photoChanged) { close(); return Kov.UI.toast('Ничего не изменилось', 'info'); }
          close();
          act('edit' + row.id, async () => {
            if (fields.length) await patchRow(row, fields, next);
            await savePhoto(row.id, picker);
          }, 'Позиция изменена');
        } },
      ],
    });
  }

  /** Выбор снимка внутри окна: кнопка и подпись о том, что уже есть */
  function photoPicker(row) {
    const state = { file: null, drop: false };
    // снимок открывается в просмотре; пока его нет — не кликабелен; сменить — только кнопкой в углу
    const preview = h('div.eq-photo-pick');
    const change = button('', { kind: 'ghost', icon: 'camera', title: 'Выбрать снимок', onClick: async e => {
      if (e && e.stopPropagation) e.stopPropagation();
      const file = await Kov.PhotoCrop.pick({ title: 'Снимок оборудования', round: false });
      if (!file) return;
      state.file = file; state.drop = false; paint();
    } });
    change.classList.add('eq-photo-btn');
    const dropBtn = button('Убрать', { kind: 'ghost', icon: 'trash', onClick: () => {
      state.file = null; state.drop = true; paint();
    } });
    const paint = () => {
      Kov.UI.clear(preview);
      const has = state.file || (row && row.has_photo && !state.drop);
      let src = null;
      if (state.file) src = URL.createObjectURL(state.file);
      else if (has) src = Kov.Api.fileUrl('equipment/' + row.id + '/photo');
      preview.append(src ? h('img', { src, alt: '' }) : icon('image'), change);
      preview.classList.toggle('is-empty', !has);
      preview.onclick = src ? () => Kov.Files.viewer([{ src, filename: (row && row.name) || 'Снимок', download: src }], 0) : null;
      preview.title = src ? 'Открыть снимок' : '';
      dropBtn.hidden = !has;
    };
    paint();
    const row2 = h('div.eq-photo-row', preview, h('div.eq-photo-acts', dropBtn));
    row2.__state = state;
    return row2;
  }

  /** Досылает снимок после того, как позиция создана или сохранена */
  async function savePhoto(itemId, picker) {
    const state = picker && picker.__state;
    if (!state) return;
    try {
      if (state.file) {
        const form = new FormData();
        form.append('file', state.file, state.file.name);
        await Kov.Api.post('equipment/' + itemId + '/photo', { form });
      } else if (state.drop) {
        await Kov.Api.del('equipment/' + itemId + '/photo');
      }
    } catch (e) { Kov.UI.toast('Снимок не сохранён: ' + Kov.UI.errorText(e), 'warning'); }
  }

  function addDialog() {
    const name = h('input.input', { placeholder: 'Например, перфоратор' });
    const qty = h('input.input', { type: 'number', value: '1', min: '0', step: 'any' });
    const unit = unitPicker((S.units[0] && S.units[0].name) || 'шт');
    const picker = photoPicker(null);
    Kov.UI.modal({
      title: 'Новая позиция', glyph: 'plus',
      body: el => el.append(Kov.UI.field('Оборудование', name, true),
        Kov.UI.field('Количество', qty, true), Kov.UI.field('Единица', unit.el),
        Kov.UI.field('Снимок', picker)),
      buttons: [
        { text: 'Отмена', kind: 'ghost' },
        { text: 'Добавить', icon: 'check', primary: true, onClick: close => {
          const title = name.value.trim();
          if (!title) return Kov.UI.toast('Укажите название', 'warning');
          const value = qty.value.trim() === '' ? null : Number(qty.value);
          if (value !== null && !(value >= 0)) return Kov.UI.toast('Количество не может быть отрицательным', 'warning');
          close();
          // Снимок досылаем после создания: до него у позиции ещё нет номера
          act('add', async () => {
            const created = await Kov.Api.post('equipment', { json: { name: title, quantity: value, unit: unit.value() } });
            if (created && created.id) await savePhoto(created.id, picker);
          }, 'Позиция добавлена');
        } },
      ],
    });
  }

  function view(section) {
    S.ui = {};
    section.classList.add('fill');
    const search = Kov.UI.searchInput('Поиск по названию…', v => { S.search = v; render(); });
    // телефон: вместо четырёх кнопок — список в одной строке с поиском
    const filter = Kov.UI.touchUi() ? Kov.UI.select(FILTERS, S.filter, v => { S.filter = v; render(); }, { title: 'Показать' })
      : Kov.UI.segmented(FILTERS, S.filter, v => { S.filter = v; render(); });
    S.ui.count = h('span.eq-count');
    S.ui.list = h('div.eq-list');
    S.ui.empty = h('div.eq-empty.eq-empty-big');
    const addBtn = h('div.eq-add');

    Kov.UI.clear(section);
    section.append(
      // Название раздела уже стоит в верхней полосе приложения — второй
      // заголовок под ней только съедал место
      h('div.toolbar.eq-toolbar', search.el, filter, S.ui.count, addBtn),
      S.ui.empty, S.ui.list);

    S.ui.list.append(Kov.UI.bigLoading('Загружаем оборудование…'));
    load().then(() => {
      if (can('can_edit_goods')) addBtn.append(button('Добавить', { icon: 'plus', onClick: addDialog }));
      render();
    }).catch(e => {
      Kov.UI.clear(S.ui.list);
      S.ui.empty.hidden = false;
      S.ui.empty.textContent = 'Не загрузилось: ' + Kov.UI.errorText(e);
    });
  }

  /**
   * Панель «ждут вашего подтверждения» для раздела «Уведомления».
   *
   * Устроена так же, как складская: человек идёт в уведомления смотреть, что
   * от него ждут, и подтверждает получение там же, не заходя в раздел.
   * Показывает и своё («вам выдают»), и складское («человек возвращает»).
   */
  function pendingPanel() {
    const list = h('div.wh-pending');
    const box = h('div.wh-global', { hidden: true },
      h('div.wh-global-head', icon('archive'), h('b', 'Оборудование: ждут вашего подтверждения'),
        h('span.count')), list);

    const TITLES = {
      receive: ['download', p => 'от ' + ((p.issuer && p.issuer.name) || 'склада')],
      return: ['undo', p => 'возвращает ' + ((p.receiver && p.receiver.name) || 'сотрудник')],
      sent: ['clock', p => 'для ' + ((p.receiver && p.receiver.name) || 'сотрудника')],
    };

    async function load() {
      let items = [];
      try { const data = await Kov.Api.get('equipment/issues/pending'); items = (data && data.items) || []; }
      catch (e) { box.hidden = true; return; }
      Kov.UI.clear(list);
      box.hidden = !items.length;
      box.querySelector('.count').textContent = items.length ? String(items.length) : '';
      items.forEach(p => {
        const [glyph, say] = TITLES[p.kind] || TITLES.sent;
        const buttons = [];
        if (p.kind === 'receive') {
          buttons.push(button('Подтвердить', { icon: 'check', onClick: () => act(p, 'confirm', 'Получение подтверждено') }));
          buttons.push(button('Отказаться', { kind: 'ghost', icon: 'close', onClick: () => act(p, 'decline', 'Выдача отклонена') }));
        } else if (p.kind === 'return') {
          buttons.push(button('Принять возврат', { icon: 'check', onClick: () => act(p, 'return/accept', 'Возврат принят') }));
          buttons.push(button('Отклонить', { kind: 'ghost', icon: 'close', onClick: () => act(p, 'return/decline', 'Возврат отклонён') }));
        } else {
          buttons.push(button('Отменить', { kind: 'ghost', icon: 'close', onClick: () => act(p, 'decline', 'Выдача отменена') }));
        }
        list.append(h('div.wh-pending-card' + (p.kind === 'receive' ? '.mine' : ''),
          h('span.circ', icon(glyph)),
          h('div.who',
            h('b', p.name),
            h('span', num(p.quantity) + (p.unit ? ' ' + p.unit : '') + ' · ' + say(p)),
            h('a.small.muted', { href: '#/equipment' }, 'Раздел «Оборудование»')),
          ...buttons));
      });
    }

    async function act(p, path, okText) {
      try {
        await Kov.Api.post('equipment/issues/' + p.id + '/' + path);
        Kov.UI.toast(okText, 'success');
        load();
      } catch (e) { Kov.UI.toast(Kov.UI.errorText(e), 'error'); }
    }

    load();
    const off = Kov.WS && Kov.WS.on ? Kov.WS.on(evt => {
      if (evt && (String(evt.type || '').startsWith('equipment.') || evt.type === 'notification.new')) load();
    }) : null;
    return { el: box, load, destroy: () => { if (off) off(); } };
  }

  return { view, onEvent, pendingPanel, __test: { S, card, visibleRows, markMine } };
})();
