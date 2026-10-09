/* Склад проекта — ведомость на 100 строк в стиле приложения.
 *
 * Столбцы: Товар · Количество · Единица · Выдано · Осталось · На складе.
 * «Выдано» руками не правится: это сумма подтверждённых выдач, поэтому цифра
 * всегда сходится с историей. «Осталось» считается из количества и выдач.
 *
 * Права приходят с сервера (объект access) и здесь только отражаются в
 * оформлении: чужие ячейки не редактируются и подсвечены как «только чтение».
 * Настоящая проверка — на сервере, клиент ей не замена.
 */
window.Kov = window.Kov || {};
Kov.Views = Kov.Views || {};

Kov.Warehouse = (function () {
  const { h, icon, button } = Kov.UI;
  const STATES = [['', '—'], ['enough', 'Достаточно'], ['short', 'Недостаточно']];
  const isPhone = () => window.matchMedia('(max-width: 760px)').matches;
  /** Полное название единицы, если оно заведено: «м — Метр» */
  const unitTitle = u => (u && u.full_name ? u.name + ' — ' + u.full_name : (u ? u.name : ''));
  const num = v => (v === null || v === undefined || v === '' ? null : Number(v));
  const show = v => (v === null || v === undefined ? '' : String(Math.round(v * 1000) / 1000));

  /** block(projectId) → { el, load() } — вставляется в раздел проекта */
  function block(projectId) {
    const state = { rows: [], units: [], access: {}, pending: [], filter: '', onlyFilled: false };
    const body = h('tbody');
    const head = h('thead', h('tr',
      h('th.wh-no', '№'),
      h('th.wh-name', 'Товар'),
      h('th.wh-num', 'Количество'),
      h('th.wh-unit', 'Единица'),
      h('th.wh-num', 'Выдано'),
      h('th.wh-num', 'Осталось'),
      h('th.wh-stock', 'Количество на складе'),
      h('th.wh-act', '')));
    const table = h('table.data.wh-table', head, body);
    const cards = h('div.wh-cards', { hidden: true });
    const scroller = h('div.wh-scroll', table, cards);
    const pendingBox = h('div.wh-pending', { hidden: true });
    const search = Kov.UI.searchInput('Поиск по товару…', v => { state.filter = v; draw(); });
    const filledBtn = button('Только заполненные', { kind: 'ghost', icon: 'filter', onClick: () => {
      state.onlyFilled = !state.onlyFilled;
      filledBtn.classList.toggle('on', state.onlyFilled);
      draw();
    } });
    const editorsBtn = button('Кто правит товары', { kind: 'ghost', icon: 'users', hidden: true, onClick: editorsDialog });
    const unitsBtn = button('Единицы', { kind: 'ghost', icon: 'table', title: 'Справочник единиц измерения', onClick: unitsDialog });
    const legend = h('span.small.muted');
    const toolbar = h('div.toolbar.wh-toolbar', search.el, filledBtn, unitsBtn, editorsBtn, h('span.spacer'), legend);
    const root = h('div.wh-block', toolbar, pendingBox, scroller);

    // ── загрузка ───────────────────────────────────────────────────────────
    async function load(opts) {
      const soft = !!(opts && opts.soft) && state.rows.length > 0;
      let data;
      try {
        data = await Kov.Api.get('projects/' + projectId + '/warehouse');
      } catch (e) {
        if (soft) return;                       // тихое обновление молчит об ошибке
        Kov.UI.clear(scroller);
        scroller.append(h('div.empty', Kov.UI.errorText(e)));
        return;
      }
      const before = new Map(state.rows.map(r => [r.row_no, rowSig(r)]));
      state.rows = data.rows || [];
      state.units = data.units || [];
      state.access = data.access || {};
      state.pending = data.pending || [];
      editorsBtn.hidden = !state.access.is_management;
      if (soft) softUpdate(before); else draw();
      drawPending();
    }

    /** Обновляем только то, что изменилось: список не перерисовывается целиком */
    function softUpdate(before) {
      if (isPhone()) { drawCardsSoft(); return; }
      const visible = new Set(visibleRows().map(r => r.row_no));
      let structural = false;
      state.rows.forEach(r => {
        const was = before.get(r.row_no);
        if (was === rowSig(r)) return;
        if (!visible.has(r.row_no)) { structural = true; return; }
        const tr = body.querySelector('tr[data-row="' + r.row_no + '"]');
        if (tr) { refreshRow(r); tr.classList.remove('row-appear'); void tr.offsetWidth; tr.classList.add('row-appear'); }
        else structural = true;
      });
      if (structural) draw();
    }
    function drawCardsSoft() {
      const rows = visibleRows();
      const filled = state.rows.filter(r => (r.name || '').trim()).length;
      legend.textContent = isPhone() ? filled + ' из ' + state.rows.length
        : 'Заполнено строк: ' + filled + ' из ' + state.rows.length;
      const add = cards.querySelector('.wh-add-card');
      if (add) {
        const free = state.rows.find(r => !(r.name || '').trim());
        add.disabled = !free;
        const label = add.querySelector('.s');
        if (label) label.textContent = free ? 'строка № ' + free.row_no : '';
      }
      syncCards(rows.filter(r => (r.name || '').trim()));
    }

    function drawPending() {
      Kov.UI.clear(pendingBox);
      const list = state.pending || [];
      pendingBox.hidden = !list.length;
      if (!list.length) return;
      list.forEach(p => {
        const mine = p.mine;
        const who = mine ? ('от ' + ((p.issuer && p.issuer.name) || 'склада'))
                         : ('для ' + ((p.receiver && p.receiver.name) || 'сотрудника'));
        const row = state.rows.find(r => r.row_no === p.row_no);
        const card = h('div.wh-pending-card' + (mine ? '.mine' : ''),
          h('span.circ', icon(mine ? 'download' : 'clock')),
          h('div.who',
            h('b', (row && row.name) || 'Строка ' + p.row_no),
            h('span', show(p.quantity) + ' ' + ((row && row.unit) || '') + ' · ' + who)),
          mine ? button('Подтвердить получение', { icon: 'check', onClick: () => act('confirm', p) }) : null,
          button(mine ? 'Отказаться' : 'Отменить', { kind: 'ghost', icon: 'close', onClick: () => act('cancel', p) }));
        pendingBox.append(card);
      });
    }

    async function act(what, p) {
      if (what === 'cancel' && !await Kov.UI.confirm('Отмена выдачи',
        'Отменить выдачу до подтверждения?', { okText: 'Отменить выдачу', danger: true })) return;
      try {
        await Kov.Api.post('warehouse/issues/' + p.id + '/' + what, { json: {} });
        Kov.UI.toast(what === 'confirm' ? 'Получение подтверждено' : 'Выдача отменена', 'success');
        load();
      } catch (e) { Kov.UI.toast(Kov.UI.errorText(e), 'error'); }
    }

    // ── таблица ────────────────────────────────────────────────────────────
    function visibleRows() {
      const needle = state.filter.trim().toLowerCase();
      return state.rows.filter(r => {
        if (state.onlyFilled && !(r.name || '').trim()) return false;
        if (needle && !((r.name || '').toLowerCase().includes(needle))) return false;
        return true;
      });
    }

    function draw() {
      // на узком экране подписи кнопок панели съедают три строки — оставляем значки
      [filledBtn, unitsBtn, editorsBtn].forEach(b => b.classList.toggle('icon-only', isPhone()));
      // На телефоне карточками показываются только заполненные строки,
      // поэтому кнопка «Только заполненные» там лишняя
      filledBtn.hidden = isPhone();
      const rows = visibleRows();
      const filled = state.rows.filter(r => (r.name || '').trim()).length;
      // на телефоне подпись короткая: длинная не помещается рядом с кнопками
      legend.textContent = isPhone() ? filled + ' из ' + state.rows.length
        : 'Заполнено строк: ' + filled + ' из ' + state.rows.length;
      if (isPhone()) return drawCards(rows, filled);
      table.hidden = false; cards.hidden = true;
      Kov.UI.clear(body);
      if (!rows.length) {
        body.append(h('tr', h('td', { colSpan: 8 }, h('div.empty', 'Ничего не найдено'))));
        return;
      }
      rows.forEach(r => body.append(rowEl(r)));
    }

    // ── телефон ────────────────────────────────────────────────────────────
    /**
     * На узком экране таблица из шести столбцов нечитаема: её пришлось бы
     * возить пальцем вбок, теряя из виду название товара. Поэтому здесь
     * карточки: в каждой видно главное — сколько выдано из скольких, чего
     * не хватает, — а правка открывается отдельной карточкой строки.
     * Пустые строки списком не показываем: сто пустых карточек бесполезны,
     * вместо них кнопка «Добавить товар».
     */
    // Ключевая сборка: карточки и строки, которые не изменились, остаются
    // теми же узлами. Раньше список очищался целиком, и при обновлении по
    // сокету склад на телефоне вздрагивал.
    const cardKeys = new Map();       // номер строки → { el, sig }
    const rowSig = r => [r.name, r.quantity, r.unit, r.issued, r.pending,
                         r.stock_state, r.stock_qty].join('|');

    function syncCards(list) {
      const seen = new Set();
      let prev = cards.querySelector('.wh-add-card') || null;
      list.forEach(r => {
        const key = String(r.row_no), sig = rowSig(r);
        seen.add(key);
        let rec = cardKeys.get(key);
        if (!rec || rec.sig !== sig) {
          const el = cardEl(r);
          if (rec) rec.el.replaceWith(el); else el.classList.add('card-appear');
          rec = { el, sig };
          cardKeys.set(key, rec);
        }
        const target = prev ? prev.nextSibling : cards.firstChild;
        if (rec.el !== target) cards.insertBefore(rec.el, target);
        prev = rec.el;
      });
      cardKeys.forEach((rec, key) => {
        if (seen.has(key)) return;
        rec.el.remove(); cardKeys.delete(key);
      });
    }

    function drawCards(rows, filled) {
      table.hidden = true; cards.hidden = false;
      cardKeys.clear();
      Kov.UI.clear(cards);
      const withName = rows.filter(r => (r.name || '').trim());
      const goods = !!state.access.can_edit_goods;
      if (goods) {
        const free = state.rows.find(r => !(r.name || '').trim());
        cards.append(h('button.wh-add-card', { type: 'button', disabled: !free,
          onclick: () => free && rowSheet(free, true) },
          h('span.circ', icon('plus')),
          h('span.t', free ? 'Добавить товар' : 'Свободных строк не осталось'),
          free ? h('span.s', 'строка № ' + free.row_no) : null));
      }
      if (!withName.length) {
        cards.append(h('div.empty', state.filter ? 'Ничего не найдено' : 'Товары ещё не заведены'));
        return;
      }
      syncCards(withName);
      if (!state.filter && filled < state.rows.length) {
        cards.append(h('div.small.muted.wh-cards-foot',
          'Свободных строк: ' + (state.rows.length - filled) + ' из ' + state.rows.length));
      }
    }

    function cardEl(r) {
      const f = rowFlags(r);
      const left = r.quantity === null || r.quantity === undefined ? null
        : Math.round((r.quantity - (r.issued || 0)) * 1000) / 1000;
      const share = r.quantity ? Math.min(100, Math.round((r.issued || 0) / r.quantity * 100)) : 0;
      const pendShare = r.quantity ? Math.min(100 - share, Math.round((r.pending || 0) / r.quantity * 100)) : 0;
      const card = h('div.wh-card-row' + (f.short ? '.short' : f.done ? '.done' : ''), { dataset: { row: r.row_no } },
        h('div.head',
          h('span.no', '№ ' + r.row_no),
          h('b.nm', r.name),
          h('span.chip.tiny' + (r.stock_state === 'enough' ? '.success' : r.stock_state === 'short' ? '.danger' : '.info'),
            r.stock_state === 'enough' ? 'на складе есть' : r.stock_state === 'short' ? 'не хватает' : 'склад не отмечен')),
        h('div.bar', h('i.done', { style: { width: share + '%' } }), h('i.pend', { style: { width: pendShare + '%' } })),
        h('div.nums',
          h('span', h('b', show(r.quantity) || '—'), ' ' + (r.unit || ''), h('em', 'нужно')),
          h('span', h('b', show(r.issued) || '0'), r.pending ? h('span.pend-mark', ' +' + show(r.pending)) : null, h('em', 'выдано')),
          h('span' + (left !== null && left <= 0 ? '.zero' : ''), h('b', left === null ? '—' : show(left)), h('em', 'осталось'))),
        h('div.acts',
          state.access.can_issue ? button('Выдать', { icon: 'download', cls: 'wh-give', onClick: e => { e && e.stopPropagation && e.stopPropagation(); issueDialog(r); } }) : null,
          button('Строка', { kind: 'ghost', icon: 'edit', onClick: () => rowSheet(r) }),
          button('', { kind: 'ghost', icon: 'clock', title: 'История', onClick: () => historyDialog(r) })));
      card.addEventListener('click', e => { if (!e.target.closest('.btn')) rowSheet(r); });
      return card;
    }

    /** Карточка строки: всё, что в таблице лежит по столбцам, здесь по полям */
    function rowSheet(r, isNew) {
      const goods = !!state.access.can_edit_goods;
      const stock = !!state.access.can_issue;
      const name = h('input.input', { value: r.name || '', placeholder: 'Например: Кабель ВВГнг 3х2.5', disabled: !goods });
      const qty = h('input.input.right', { value: r.quantity === null || r.quantity === undefined ? '' : show(r.quantity),
        inputMode: 'decimal', placeholder: '0', disabled: !goods });
      const unitBtn = h('button.wh-unit-btn.wide', { type: 'button', disabled: !goods },
        h('span.v', r.unit || 'не выбрана'), icon('chevron_down'));
      const stockSel = h('select.select', { disabled: !stock });
      STATES.forEach(([v, t]) => stockSel.append(h('option', { value: v, selected: (r.stock_state || '') === v }, t)));
      const stockQty = h('input.input.right', { value: r.stock_qty === null || r.stock_qty === undefined ? '' : show(r.stock_qty),
        inputMode: 'decimal', placeholder: 'сколько есть', disabled: !stock });
      const facts = h('div.wh-sheet-facts');
      function paintFacts() {
        const left = r.quantity === null || r.quantity === undefined ? null
          : Math.round((r.quantity - (r.issued || 0)) * 1000) / 1000;
        Kov.UI.clear(facts);
        facts.append(
          h('div', h('b', show(r.issued) || '0'), h('span', 'выдано' + (r.pending ? ' · ждёт ' + show(r.pending) : ''))),
          h('div', h('b', left === null ? '—' : show(left)), h('span', 'осталось выдать')));
        const hint = cellHint(r, 'name') || cellHint(r, 'quantity');
        if (hint) facts.append(h('div.wide', h('span', hint)));
      }
      paintFacts();
      unitBtn.addEventListener('click', () => openUnitList(unitBtn, r, unitBtn.parentElement, () => {
        unitBtn.querySelector('.v').textContent = r.unit || 'не выбрана';
      }));

      const dlg = Kov.UI.modal({
        title: isNew ? 'Новый товар · строка № ' + r.row_no : 'Строка № ' + r.row_no, glyph: 'archive',
        body: el => el.append(...[
          Kov.UI.field('Товар', name),
          h('div.wh-sheet-two', Kov.UI.field('Количество', qty), Kov.UI.field('Единица', unitBtn)),
          facts,
          stock ? h('div.wh-sheet-two', Kov.UI.field('Количество на складе', stockSel), Kov.UI.field('Сколько есть', stockQty)) : null,
          h('div.btn-row.wh-sheet-acts', ...[
            stock && (r.name || '').trim() ? button('Выдать товар', { icon: 'download', onClick: () => { dlg.close(); issueDialog(r); } }) : null,
            button('История строки', { kind: 'ghost', icon: 'clock', onClick: () => { dlg.close(); historyDialog(r); } }),
          ].filter(Boolean)),
        ].filter(Boolean)),
        buttons: [{ text: 'Сохранить', icon: 'save', onClick: async close => {
          const patch = {}, fields = [];
          if (goods) {
            if ((name.value.trim() || '') !== (r.name || '')) { patch.name = name.value.trim(); fields.push('name'); }
            const parsed = qty.value.trim() === '' ? null : Number(qty.value.replace(',', '.'));
            if (parsed !== null && (isNaN(parsed) || parsed < 0)) return Kov.UI.toast('Количество — неотрицательное число', 'warning');
            if (parsed !== (r.quantity === undefined ? null : r.quantity)) { patch.quantity = parsed; fields.push('quantity'); }
          }
          if (stock) {
            if ((stockSel.value || null) !== (r.stock_state || null)) { patch.stock_state = stockSel.value || null; fields.push('stock_state'); }
            const sq = stockQty.value.trim() === '' ? null : Number(stockQty.value.replace(',', '.'));
            if (sq !== null && (isNaN(sq) || sq < 0)) return Kov.UI.toast('Количество на складе — неотрицательное число', 'warning');
            if (sq !== (r.stock_qty === undefined ? null : r.stock_qty)) { patch.stock_qty = sq; fields.push('stock_qty'); }
          }
          if (!fields.length) { close(); return; }
          try {
            const res = await Kov.Api.patch('projects/' + projectId + '/warehouse/' + r.row_no,
              { json: Object.assign({}, patch, { fields }) });
            Object.assign(r, res.row || {});
            close(); draw();
            Kov.UI.toast('Сохранено', 'success');
          } catch (e) { Kov.UI.toast(Kov.UI.errorText(e), 'error'); }
        } }],
      });
      if (goods && isNew) Kov.UI.softFocus(name);
    }

    /** Строки, требующие внимания: всё выдано или на складе не хватает */
    function rowFlags(r) {
      const filled = !!(r.name || '').trim();
      const left = r.quantity === null || r.quantity === undefined ? null
        : Math.round((r.quantity - (r.issued || 0)) * 1000) / 1000;
      return {
        empty: !filled,
        done: filled && left !== null && left <= 0,          // выдавать больше нечего
        short: filled && r.stock_state === 'short',           // на складе не хватает
      };
    }
    function paintFlags(tr, r) {
      const f = rowFlags(r);
      tr.classList.toggle('empty-row', f.empty);
      tr.classList.toggle('row-done', f.done);
      tr.classList.toggle('row-short', f.short);
      tr.title = f.short ? 'На складе недостаточно товара' : f.done ? 'Выдано полностью' : '';
    }

    function rowEl(r) {
      const goods = !!state.access.can_edit_goods;
      const stock = !!state.access.can_issue;
      const tr = h('tr.wh-row', { dataset: { row: r.row_no } });
      paintFlags(tr, r);

      const nameCell = editableText(r, 'name', goods, '');
      const qtyCell = editableNumber(r, 'quantity', goods);
      const unitCell = unitPicker(r, goods);
      const issuedCell = h('td.wh-num.wh-issued' + (stock && (r.name || '').trim() ? '.clickable' : ''),
        { title: stock && (r.name || '').trim() ? 'Нажмите, чтобы выдать товар' : '' }, issuedContent(r));
      if (stock && (r.name || '').trim()) {
        issuedCell.dataset.bound = '1';
        issuedCell.addEventListener('click', () => issueDialog(r));
      }
      const leftCell = h('td.wh-num.wh-left', leftContent(r));
      const stockCell = stockPicker(r, stock);
      const actions = h('td.wh-act',
        stock && (r.name || '').trim()
          ? button('Выдать', { kind: 'ghost', cls: 'wh-give', icon: 'download', title: 'Выдать товар сотруднику', onClick: () => issueDialog(r) })
          : null,
        button('', { kind: 'ghost', icon: 'clock', title: 'Кто и когда менял строку', onClick: () => historyDialog(r) }));

      tr.append(h('td.wh-no', String(r.row_no)), nameCell, qtyCell, unitCell, issuedCell, leftCell, stockCell, actions);
      return tr;
    }

    function issuedContent(r) {
      const parts = [h('span', show(r.issued) || '0')];
      if (r.pending) parts.push(h('span.chip.warning.tiny', { title: 'Ждёт подтверждения получателем' }, '+' + show(r.pending)));
      return parts;
    }
    function leftContent(r) {
      if (r.quantity === null || r.quantity === undefined) return h('span.muted', '—');
      const left = Math.round((r.quantity - (r.issued || 0)) * 1000) / 1000;
      return h('span' + (left <= 0 ? '.wh-zero' : ''), show(left));
    }

    /** Ячейка редактируется по нажатию, значение уходит на сервер при уходе из неё */
    /** «Иванов Иван · 12.09.2026 14:30» — кто последним трогал эту ячейку */
    function cellHint(r, field) {
      const info = r.by && r.by[field];
      if (!info) return '';
      const when = info.at ? Kov.UI.fmtDateTime(info.at) : '';
      return 'Изменил: ' + info.who + (when ? ' · ' + when : '');
    }
    function applyHint(cell, r, field) {
      const hint = cellHint(r, field);
      if (hint) { cell.title = hint; cell.classList.add('has-hint'); }
      else { cell.removeAttribute('title'); cell.classList.remove('has-hint'); }
    }

    function editableText(r, field, allowed, placeholder) {
      const td = h('td.wh-name');
      applyHint(td, r, field);
      const value = r[field] || '';
      if (!allowed) { td.append(h('span' + (value ? '' : '.muted'), value || '—')); td.classList.add('ro'); return td; }
      // подсказку вешаем на само поле: оно закрывает ячейку целиком и её
      // собственный title перебивал подпись «кто менял»
      const input = h('input.wh-input', { value, placeholder, title: cellHint(r, field) || '' });
      let before = value;
      input.addEventListener('focus', () => { before = input.value; td.classList.add('editing'); });
      input.addEventListener('blur', () => { td.classList.remove('editing'); commitIfChanged(); });
      input.addEventListener('keydown', e => {
        if (e.key === 'Enter') { input.blur(); moveFocus(r.row_no, field, 1); }
        if (e.key === 'Escape') { input.value = before; input.blur(); }
      });
      function commitIfChanged() {
        if (input.value === before) return;
        save(r, { [field]: input.value.trim() }, [field], td, () => { input.value = before; });
      }
      input.dataset.field = field; input.dataset.row = r.row_no;
      td.append(input);
      return td;
    }

    function editableNumber(r, field, allowed) {
      const td = h('td.wh-num');
      applyHint(td, r, field);
      const value = r[field];
      if (!allowed) { td.append(h('span' + (value === null ? '.muted' : ''), value === null ? '—' : show(value))); td.classList.add('ro'); return td; }
      const input = h('input.wh-input.right', { value: value === null || value === undefined ? '' : show(value),
        inputMode: 'decimal', title: cellHint(r, field) || '' });
      let before = input.value;
      input.addEventListener('focus', () => { before = input.value; td.classList.add('editing'); });
      input.addEventListener('blur', () => {
        td.classList.remove('editing');
        if (input.value === before) return;
        const parsed = input.value.trim() === '' ? null : num(input.value.replace(',', '.'));
        if (parsed !== null && (isNaN(parsed) || parsed < 0)) {
          Kov.UI.toast('Количество — неотрицательное число', 'warning');
          input.value = before; return;
        }
        save(r, { [field]: parsed }, [field], td, () => { input.value = before; });
      });
      input.addEventListener('keydown', e => {
        if (e.key === 'Enter') { input.blur(); moveFocus(r.row_no, field, 1); }
        if (e.key === 'Escape') { input.value = before; input.blur(); }
      });
      input.dataset.field = field; input.dataset.row = r.row_no;
      td.append(input);
      return td;
    }

    /**
     * Единица измерения: свой список вместо select. В штатном списке нельзя
     * ни искать, ни показать полное название, а единиц в справочнике со
     * временем становится много. Новые заводятся в окне «Единицы измерения».
     */
    function unitPicker(r, allowed, onPick) {
      const current = () => state.units.find(u => u.name === r.unit) || (r.unit ? { name: r.unit } : null);
      const td = h('td.wh-unit');
      applyHint(td, r, 'unit');
      if (!allowed) {
        td.append(h('span' + (r.unit ? '' : '.muted'), { title: unitTitle(current()) }, r.unit || '—'));
        td.classList.add('ro');
        return td;
      }
      const btn = h('button.wh-unit-btn', { type: 'button', title: cellHint(r, 'unit') || 'Выбрать единицу измерения' },
        h('span.v', r.unit || '—'), icon('chevron_down'));
      btn.addEventListener('click', () => openUnitList(btn, r, td, onPick));
      td.append(btn);
      return td;
    }

    /** Всплывающий список с поиском: строится рядом с ячейкой */
    function openUnitList(anchor, r, cell, onPick) {
      document.querySelectorAll('.wh-unit-pop').forEach(x => x.remove());
      const search = h('input.input.wh-unit-search', { placeholder: 'Поиск единицы…', autocomplete: 'off' });
      const list = h('div.wh-unit-list');
      const pop = h('div.wh-unit-pop', h('div.wh-unit-pop-head', search), list,
        h('button.wh-unit-add', { type: 'button' }, icon('plus'), 'Новая единица — в справочнике'));
      pop.querySelector('.wh-unit-add').addEventListener('click', () => { close(); unitsDialog(); });

      function render(filter) {
        Kov.UI.clear(list);
        const needle = (filter || '').trim().toLowerCase();
        const found = state.units.filter(u => !needle
          || u.name.toLowerCase().includes(needle)
          || (u.full_name || '').toLowerCase().includes(needle));
        list.append(h('button.wh-unit-item' + (r.unit ? '' : '.on'), { type: 'button', onclick: () => pick('') },
          h('b', '—'), h('span', 'без единицы')));
        found.forEach(u => list.append(h('button.wh-unit-item' + (u.name === r.unit ? '.on' : ''),
          { type: 'button', onclick: () => pick(u.name) }, h('b', u.name), h('span', u.full_name || ''))));
        if (!found.length) list.append(h('div.empty', 'Ничего не найдено'));
      }
      function pick(name) {
        close();
        if (name === (r.unit || '')) return;
        save(r, { unit: name }, ['unit'], cell, () => {}).then(() => { onPick && onPick(); });
      }
      function close() { pop.remove(); document.removeEventListener('pointerdown', outside, true); window.removeEventListener('resize', onResize); }
      function outside(e) { if (!pop.contains(e.target) && e.target !== anchor) close(); }
    // открылась клавиатура — окно стало ниже, но список остаётся; закрываем только при повороте экрана
    const w0 = window.innerWidth;
    function onResize() { if (Math.abs(window.innerWidth - w0) > 40) close(); }

      search.addEventListener('input', () => render(search.value));
      search.addEventListener('keydown', e => {
        if (e.key === 'Escape') close();
        if (e.key === 'Enter') { const first = list.querySelector('.wh-unit-item'); if (first) first.click(); }
      });
      render('');
      document.body.append(pop);
      // список ставим под ячейкой, а если снизу не помещается — над ней
      const box = anchor.getBoundingClientRect();
      const height = Math.min(pop.offsetHeight || 260, window.innerHeight - 20);
      const below = window.innerHeight - box.bottom > height + 8;
      pop.style.left = Math.max(8, Math.min(box.left, window.innerWidth - pop.offsetWidth - 8)) + 'px';
      pop.style.top = (below ? box.bottom + 4 : Math.max(8, box.top - height - 4)) + 'px';
      setTimeout(() => { document.addEventListener('pointerdown', outside, true); Kov.UI.softFocus(search); }, 10);
      window.addEventListener('resize', onResize);
    }

    /** Количество на складе: достаточно/недостаточно и сколько именно */
    function stockPicker(r, allowed) {
      const td = h('td.wh-stock');
      applyHint(td, r, 'stock_state');
      if (!allowed) {
        const label = STATES.find(s => s[0] === (r.stock_state || ''))[1];
        td.append(h('span.chip' + (r.stock_state === 'enough' ? '.success' : r.stock_state === 'short' ? '.danger' : ''), label),
          r.stock_qty !== null && r.stock_qty !== undefined ? h('span.wh-stock-qty', show(r.stock_qty)) : null);
        td.classList.add('ro');
        return td;
      }
      const sel = h('select.wh-select.wh-state');
      STATES.forEach(([value, title]) => sel.append(h('option', { value, selected: (r.stock_state || '') === value }, title)));
      const qty = h('input.wh-input.right.wh-qty', { value: r.stock_qty === null || r.stock_qty === undefined ? '' : show(r.stock_qty),
        inputMode: 'decimal', placeholder: 'сколько', hidden: !r.stock_state });
      sel.addEventListener('change', () => {
        qty.hidden = !sel.value;
        save(r, { stock_state: sel.value || null }, ['stock_state'], td, () => { sel.value = r.stock_state || ''; });
        if (sel.value) Kov.UI.softFocus(qty);
      });
      let before = qty.value;
      qty.addEventListener('focus', () => { before = qty.value; });
      qty.addEventListener('blur', () => {
        if (qty.value === before) return;
        const parsed = qty.value.trim() === '' ? null : num(qty.value.replace(',', '.'));
        if (parsed !== null && (isNaN(parsed) || parsed < 0)) { Kov.UI.toast('Количество — неотрицательное число', 'warning'); qty.value = before; return; }
        save(r, { stock_qty: parsed }, ['stock_qty'], td, () => { qty.value = before; });
      });
      td.classList.add('state-' + (r.stock_state || 'none'));
      td.append(h('div.wh-stock-box', sel, qty));
      return td;
    }

    /** Сохранение ячейки: подсветка успеха, откат при отказе сервера */
    async function save(r, patch, fields, cell, revert) {
      cell && cell.classList.add('saving');
      try {
        const res = await Kov.Api.patch('projects/' + projectId + '/warehouse/' + r.row_no,
          { json: Object.assign({}, patch, { fields }) });
        Object.assign(r, res.row || {});
        if (cell) { cell.classList.remove('saving'); cell.classList.add('saved'); setTimeout(() => cell.classList.remove('saved'), 900); }
        refreshRow(r);
      } catch (e) {
        cell && cell.classList.remove('saving');
        Kov.UI.toast(Kov.UI.errorText(e), 'error');
        revert && revert();
      }
    }

    /** Пересчитанные ячейки строки обновляем на месте, не перерисовывая таблицу */
    function refreshRow(r) {
      if (isPhone()) {
        const card = cards.querySelector('.wh-card-row[data-row="' + r.row_no + '"]');
        if (card) card.replaceWith(cardEl(r)); else draw();
        return;
      }
      const tr = body.querySelector('tr[data-row="' + r.row_no + '"]');
      if (!tr) return;
      const issued = tr.querySelector('.wh-issued');
      const left = tr.querySelector('.wh-left');
      if (issued) { Kov.UI.clear(issued); issued.append(...issuedContent(r)); }
      if (left) { Kov.UI.clear(left); left.append(leftContent(r)); }
      // Значения в ячейках берём из ответа сервера: раньше подпись единицы
      // оставалась старой до перезагрузки страницы
      const unitBtn = tr.querySelector('.wh-unit .wh-unit-btn .v');
      if (unitBtn) unitBtn.textContent = r.unit || '—';
      const unitRo = tr.querySelector('.wh-unit.ro span');
      if (unitRo) unitRo.textContent = r.unit || '—';
      const nameInput = tr.querySelector('.wh-name input');
      if (nameInput && document.activeElement !== nameInput) nameInput.value = r.name || '';
      const qtyInput = tr.querySelector('.wh-num input');
      if (qtyInput && document.activeElement !== qtyInput) qtyInput.value = r.quantity === null || r.quantity === undefined ? '' : show(r.quantity);
      const stockSel = tr.querySelector('.wh-stock select');
      if (stockSel && document.activeElement !== stockSel) stockSel.value = r.stock_state || '';
      const stockQty = tr.querySelector('.wh-stock .wh-qty');
      if (stockQty && document.activeElement !== stockQty) {
        stockQty.value = r.stock_qty === null || r.stock_qty === undefined ? '' : show(r.stock_qty);
        stockQty.hidden = !r.stock_state;
      }
      ['name', 'quantity', 'unit'].forEach((f, i) => {
        const cell = tr.querySelector(['.wh-name', '.wh-num', '.wh-unit'][i]);
        if (!cell) return;
        applyHint(cell, r, f);
        const inner = cell.querySelector('input, button');
        if (inner) { const t = cellHint(r, f); if (t) inner.title = t; else inner.removeAttribute('title'); }
      });
      paintFlags(tr, r);
      const stockTd = tr.querySelector('.wh-stock');
      if (stockTd) { stockTd.className = 'wh-stock' + (stockTd.classList.contains('ro') ? ' ro' : '') + ' state-' + (r.stock_state || 'none'); }
      // Строку заполнили — кнопка выдачи должна появиться тут же, а не после
      // перезагрузки страницы: пересобираем ячейку действий и «Выдано»
      const filled = !!(r.name || '').trim();
      const canIssue = !!state.access.can_issue;
      const actions = tr.querySelector('.wh-act');
      if (actions) {
        const hasGive = !!actions.querySelector('.wh-give');
        if (hasGive !== (canIssue && filled)) {
          Kov.UI.clear(actions);
          if (canIssue && filled) {
            actions.append(button('Выдать', { kind: 'ghost', cls: 'wh-give', icon: 'download',
              title: 'Выдать товар сотруднику', onClick: () => issueDialog(r) }));
          }
          actions.append(button('', { kind: 'ghost', icon: 'clock',
            title: 'Кто и когда менял строку', onClick: () => historyDialog(r) }));
        }
      }
      const issuedTd = tr.querySelector('.wh-issued');
      if (issuedTd) {
        const clickable = canIssue && filled;
        issuedTd.classList.toggle('clickable', clickable);
        issuedTd.title = clickable ? 'Нажмите, чтобы выдать товар' : '';
        if (clickable && !issuedTd.dataset.bound) {
          issuedTd.dataset.bound = '1';
          issuedTd.addEventListener('click', () => issueDialog(r));
        }
      }
    }

    // ── вставка из буфера ──────────────────────────────────────────────────

    /**
     * Разбор вставки. Excel и Google Таблицы кладут в буфер строки, разделённые
     * переводом строки, а столбцы — табуляцией. Точку с запятой понимаем ради
     * выгрузок из 1С, запятую — нет: в названиях товаров она встречается.
     */
    function parsePaste(text) {
      const raw = String(text || '').replace(/\r\n?/g, '\n').replace(/\n+$/, '');
      if (!raw.trim()) return { error: 'Буфер пуст' };
      const lines = raw.split('\n').filter(l => l.trim() !== '');
      if (lines.length > ROWS_LIMIT) return { error: 'За раз можно вставить не больше ' + ROWS_LIMIT + ' строк' };
      const sep = lines[0].includes('\t') ? '\t' : (lines.some(l => l.includes(';')) ? ';' : null);
      const rows = [], problems = [];
      lines.forEach((line, i) => {
        const cells = (sep ? line.split(sep) : [line]).map(c => c.trim().replace(/^"|"$/g, ''));
        if (cells.length > 3) { problems.push('Строка ' + (i + 1) + ': больше трёх столбцов'); return; }
        const name = cells[0] || '';
        if (!name) { problems.push('Строка ' + (i + 1) + ': нет названия товара'); return; }
        if (name.length > 255) { problems.push('Строка ' + (i + 1) + ': название длиннее 255 знаков'); return; }
        let quantity = null;
        if (cells.length > 1 && cells[1] !== '') {
          // «1 250,5» из Excel — пробелы-разделители тысяч и запятая
          const cleaned = cells[1].replace(/\s|\u00A0/g, '').replace(',', '.');
          quantity = Number(cleaned);
          if (!isFinite(quantity) || cleaned === '') { problems.push('Строка ' + (i + 1) + ': «' + cells[1] + '» — не число'); return; }
          if (quantity < 0) { problems.push('Строка ' + (i + 1) + ': количество отрицательное'); return; }
        }
        const unit = cells.length > 2 ? cells[2].slice(0, 32) : null;
        rows.push({ name, quantity, unit: unit || null });
      });
      if (!rows.length) return { error: problems[0] || 'Ничего подходящего не нашлось', problems };
      return { rows, problems, columns: sep ? Math.min(3, (lines[0].split(sep) || []).length) : 1 };
    }
    const ROWS_LIMIT = 100;

    /** Показываем, что именно вставится, и только потом пишем на сервер */
    function pasteDialog(startRow, parsed) {
      const free = state.rows.length - startRow + 1;
      const fits = parsed.rows.length <= free;
      const preview = h('div.wh-paste-preview');
      parsed.rows.slice(0, 8).forEach((r, i) => preview.append(h('div.wh-paste-row',
        h('span.no', String(startRow + i)),
        h('span.nm', r.name),
        h('span.qt', r.quantity === null ? '—' : show(r.quantity)),
        h('span.un', r.unit || ''))));
      if (parsed.rows.length > 8) preview.append(h('div.small.muted', '…и ещё ' + (parsed.rows.length - 8) + ' строк'));

      Kov.UI.modal({
        title: 'Вставка из буфера', glyph: 'table', wide: true,
        // el.append — родной метод DOM: null он превращает в текст «null»,
        // поэтому пустые куски отсеиваем заранее
        body: el => el.append(...[
          h('div.wh-issue-head', h('b', 'Будет заполнено строк: ' + parsed.rows.length),
            h('span.muted', 'начиная со строки ' + startRow)),
          h('div.small.muted', 'Столбцы распознаны так: товар' + (parsed.columns > 1 ? ', количество' : '')
            + (parsed.columns > 2 ? ', единица' : '') + '. Существующие значения в этих строках будут заменены.'),
          preview,
          parsed.problems && parsed.problems.length
            ? h('div.wh-paste-problems', h('b', 'Пропущено строк: ' + parsed.problems.length),
                ...parsed.problems.slice(0, 6).map(t => h('div', t)),
                parsed.problems.length > 6 ? h('div.small.muted', '…и ещё ' + (parsed.problems.length - 6)) : null)
            : null,
          !fits ? h('div.wh-paste-problems', 'Не помещается: со строки ' + startRow + ' свободно ' + free + ' строк') : null,
        ].filter(Boolean)),
        buttons: [{ text: 'Отмена', kind: 'ghost' },
          { text: 'Вставить', icon: 'check', onClick: async close => {
            if (!fits) return Kov.UI.toast('Выберите строку выше — не помещается', 'warning');
            try {
              const res = await Kov.Api.post('projects/' + projectId + '/warehouse/bulk',
                { json: { start_row: startRow, rows: parsed.rows } });
              close();
              Kov.UI.toast('Вставлено строк: ' + (res.count || parsed.rows.length), 'success');
              await load();
              flashRows(startRow, parsed.rows.length);
            } catch (e) { Kov.UI.toast(Kov.UI.errorText(e), 'error'); }
          } }],
      });
    }

    /** Свежевставленные строки коротко подсвечиваются — видно, куда легли данные */
    function flashRows(startRow, count) {
      for (let i = 0; i < count; i++) {
        const tr = body.querySelector('tr[data-row="' + (startRow + i) + '"]');
        if (!tr) continue;
        tr.classList.add('row-flash');
        setTimeout(() => tr.classList.remove('row-flash'), 1400);
      }
      const first = body.querySelector('tr[data-row="' + startRow + '"]');
      if (first && first.scrollIntoView) first.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }

    scroller.addEventListener('paste', e => {
      if (!state.access.can_edit_goods) return;
      const target = e.target;
      const cell = target && target.closest ? target.closest('td') : null;
      // вставляем только из ячеек «Товар»: там это осмысленно
      if (!cell || !cell.classList.contains('wh-name')) return;
      const text = (e.clipboardData || window.clipboardData).getData('text/plain');
      const parsed = parsePaste(text);
      const single = !/[\t\n;]/.test(String(text || ''));
      if (single) return;                      // одна ячейка — обычная вставка текста
      e.preventDefault();
      if (parsed.error) return Kov.UI.toast(parsed.error, 'warning');
      const startRow = Number(cell.closest('tr').dataset.row) || 1;
      pasteDialog(startRow, parsed);
    });

    /** Enter переводит на ту же ячейку следующей строки — как в таблице */
    function moveFocus(rowNo, field, delta) {
      const next = body.querySelector('tr[data-row="' + (rowNo + delta) + '"] input[data-field="' + field + '"]');
      if (next) { next.focus(); next.select && next.select(); }
    }

    // ── выдача товара ──────────────────────────────────────────────────────
    function issueDialog(r) {
      let receiver = null;
      const left = r.quantity === null ? null : Math.round((r.quantity - (r.issued || 0) - (r.pending || 0)) * 1000) / 1000;
      const qty = h('input.input', { type: 'number', min: '0', step: 'any', placeholder: 'Сколько выдаём' });
      const who = h('div.wh-receiver', h('span.muted', 'Сотрудник не выбран'));
      const pick = button('Выбрать сотрудника', { kind: 'ghost', icon: 'users', onClick: () => {
        Kov.Views.messenger.employeePicker('Кому выдаём товар', ids => {
          Kov.Store.employees().then(list => {
            receiver = list.find(e => e.id === ids[0]) || { id: ids[0] };
            Kov.UI.clear(who);
            who.append(Kov.UI.avatar(receiver, null, Kov.Store.photoUrl(receiver.id)),
              h('span', Kov.UI.fullName(receiver, true),
                h('span.muted.small', ' — ' + [receiver.department, receiver.position].filter(Boolean).join(', '))));
          });
        }, false, { okText: 'Выбрать' });
      } });
      Kov.UI.modal({
        title: 'Выдача товара', glyph: 'download', wide: false,
        body: el => el.append(
          h('div.wh-issue-head', h('b', r.name || 'Строка ' + r.row_no),
            h('span.muted', left === null ? '' : 'Можно выдать: ' + show(left) + ' ' + (r.unit || ''))),
          Kov.UI.field('Количество', qty, true),
          Kov.UI.field('Кому выдаём', h('div.wh-receiver-box', who, pick), true),
          h('div.small.muted', 'Выдача попадёт в таблицу только после того, как сотрудник подтвердит получение. До подтверждения её можно отменить.')),
        buttons: [{ text: 'Отмена', kind: 'ghost' }, { text: 'Выдать', icon: 'check', onClick: async close => {
          const value = num(String(qty.value).replace(',', '.'));
          if (!value || value <= 0) return Kov.UI.toast('Укажите количество больше нуля', 'warning');
          if (!receiver) return Kov.UI.toast('Выберите сотрудника, которому выдаёте', 'warning');
          try {
            await Kov.Api.post('projects/' + projectId + '/warehouse/' + r.row_no + '/issue',
              { json: { quantity: value, receiver_id: receiver.id } });
            close();
            Kov.UI.toast('Выдача отправлена на подтверждение', 'success');
            load();
          } catch (e) { Kov.UI.toast(Kov.UI.errorText(e), 'error'); }
        } }],
      });
      Kov.UI.softFocus(qty);
    }

    // ── история строки ─────────────────────────────────────────────────────
    /** Журнал строки — общее окно, одно на склад, оборудование и задачи */
    function historyDialog(r) {
      Kov.Journal.open({
        title: 'Журнал строки № ' + r.row_no,
        subtitle: r.name || 'Без названия',
        alwaysIssues: true,
        load: async () => {
          const data = await Kov.Api.get('projects/' + projectId + '/warehouse/' + r.row_no + '/history');
          const unit = r.unit ? ' ' + r.unit : '';
          const STATE = { confirmed: 'подтверждено', cancelled: 'отменено', pending: 'ждёт подтверждения' };
          return {
            issues: (data.issues || []).map(i => ({
              kind: 'issue', action: i.status, who: i.issuer, target: i.receiver, at: i.at,
              title: show(i.quantity) + unit + ' · ' + (STATE[i.status] || i.status),
              text: ((i.issuer && i.issuer.name) || '—') + ' → ' + ((i.receiver && i.receiver.name) || '—')
                + (i.confirmed_at ? ' · принято ' + Kov.Journal.when(i.confirmed_at) : '')
                + (i.cancelled_at ? ' · отменено ' + Kov.Journal.when(i.cancelled_at) : ''),
            })),
            edits: (data.edits || []).map(e => ({
              kind: 'edit', action: 'changed', who: e.who, at: e.at,
              title: e.field_title,
              text: (e.old || '—') + ' → ' + (e.new || '—'),
            })),
          };
        },
      });
    }

    // ── справочник единиц ──────────────────────────────────────────────────
    /** Здесь единицы заводятся, переименовываются и удаляются — одно место на всё */
    // проверка для журнала приложения: курсор не должен оказываться в начале поля после ввода
    function caretCheck(e) {
      const t = e.target;
      if (t.value.length > 1 && t.selectionStart === 0) console.warn('Единицы: курсор в начале поля после ввода, длина ' + t.value.length);
    }

    async function unitsDialog() {
      let data = { units: [], can_manage: false, can_add: false };
      try { data = await Kov.Api.get('warehouse/units/full'); }
      catch (e) { return Kov.UI.toast(Kov.UI.errorText(e), 'error'); }
      const list = h('div.wh-units');

      async function refresh() {
        try {
          const units = await Kov.Api.get('warehouse/units');
          state.units = units || state.units;
          data = await Kov.Api.get('warehouse/units/full');
          fill(); draw();
        } catch (e) { /* окно остаётся открытым */ }
      }

      function fill() {
        Kov.UI.clear(list);
        if (!data.units.length) { list.append(h('div.empty', 'Пока ни одной единицы')); return; }
        data.units.forEach(u => {
          const short = h('input.input.wh-unit-short', { value: u.name, maxLength: 32, disabled: !data.can_manage, title: 'Краткое обозначение' });
          const full = h('input.input.wh-unit-full', { value: u.full_name || '', maxLength: 64, disabled: !data.can_manage, placeholder: 'Полное название' });
          const save = button('', { kind: 'ghost', icon: 'check', title: 'Сохранить', disabled: true, onClick: async () => {   // всегда на месте: строка не перестраивается при вводе
            const nextShort = short.value.trim(), nextFull = full.value.trim();
            if (!nextShort) { Kov.UI.toast('Краткое обозначение пустое', 'warning'); short.value = u.name; return; }
            try {
              const res = await Kov.Api.patch('warehouse/units/' + u.id, { json: { name: nextShort, full_name: nextFull } });
              u.name = res.name; u.full_name = res.full_name; save.disabled = true;
              Kov.UI.toast(res.moved ? 'Сохранено, обновлено строк: ' + res.moved : 'Сохранено', 'success');
              refresh();
            } catch (e) { Kov.UI.toast(Kov.UI.errorText(e), 'error'); short.value = u.name; full.value = u.full_name || ''; }
          } });
          const dirty = () => { save.disabled = short.value.trim() === u.name && full.value.trim() === (u.full_name || ''); };
          short.addEventListener('input', dirty); full.addEventListener('input', dirty);
          short.addEventListener('input', caretCheck); full.addEventListener('input', caretCheck);
          const del = button('', { kind: 'ghost', icon: 'trash',
            title: u.used ? 'Стоит в строках — сначала замените её там' : 'Удалить',
            disabled: !data.can_manage || !!u.used, onClick: async () => {
              if (!await Kov.UI.confirm('Удаление единицы', 'Убрать «' + u.name + '» из справочника?', { okText: 'Удалить', danger: true })) return;
              try {
                await Kov.Api.del('warehouse/units/' + u.id);
                Kov.UI.toast('Единица удалена', 'success');
                refresh();
              } catch (e) { Kov.UI.toast(Kov.UI.errorText(e), 'error'); }
            } });
          list.append(h('div.wh-unit-row', short, full,
            h('span.chip.tiny' + (u.used ? '' : '.info'), u.used ? 'в строках: ' + u.used : 'свободна'),
            save, del));
        });
      }
      fill();

      // строка добавления — сверху, чтобы её было видно сразу
      const newShort = h('input.input.wh-unit-short', { placeholder: 'м³', maxLength: 32 });
      const newFull = h('input.input.wh-unit-full', { placeholder: 'Кубический метр', maxLength: 64 });
      const addBtn = button('Добавить', { icon: 'plus', onClick: async () => {
        const name = newShort.value.trim();
        if (!name) return Kov.UI.toast('Введите краткое обозначение', 'warning');
        try {
          await Kov.Api.post('warehouse/units', { json: { name, full_name: newFull.value.trim() } });
          newShort.value = ''; newFull.value = '';
          Kov.UI.toast('Единица добавлена', 'success');
          refresh();
        } catch (e) { Kov.UI.toast(Kov.UI.errorText(e), 'error'); }
      } });
      newShort.addEventListener('keydown', e => { if (e.key === 'Enter') newFull.focus(); });
      newFull.addEventListener('keydown', e => { if (e.key === 'Enter') addBtn.click(); });
      newShort.addEventListener('input', caretCheck); newFull.addEventListener('input', caretCheck);

      Kov.UI.modal({
        title: 'Единицы измерения', glyph: 'table', wide: true,
        body: el => el.append(...[
          data.can_add ? h('div.wh-unit-new', h('span.lbl', 'Новая единица'), h('div.wh-unit-new-row', newShort, newFull), addBtn) : null,
          h('div.wh-unit-head', h('span', 'Кратко'), h('span', 'Полное название'), h('span', 'Где стоит')),
          list,
          data.can_manage
            ? h('div.small.muted', 'Переименование меняет единицу во всех строках всех проектов. Удалить можно только свободную.')
            : null,
        ].filter(Boolean)),
        buttons: [],
      });
      if (data.can_add) Kov.UI.softFocus(newShort);
    }

    // ── кому доверено править товары ───────────────────────────────────────
    async function editorsDialog() {
      let data = { people: [], user_ids: [] };
      try { data = await Kov.Api.get('warehouse/editors'); } catch (e) { return Kov.UI.toast(Kov.UI.errorText(e), 'error'); }
      const list = h('div.wh-editors');
      function fill() {
        Kov.UI.clear(list);
        if (!data.people.length) { list.append(h('div.empty', 'Никто пока не добавлен')); return; }
        data.people.forEach(p => list.append(h('div.wh-editor',
          Kov.UI.avatar({ last_name: p.last_name, first_name: p.first_name }, null, Kov.Store.photoUrl(p.id)),
          h('span', { style: { flex: 1 } }, p.name),
          button('', { kind: 'ghost', icon: 'close', title: 'Убрать', onClick: () => {
            data.user_ids = data.user_ids.filter(id => id !== p.id);
            data.people = data.people.filter(x => x.id !== p.id);
            fill();
          } }))));
      }
      fill();
      Kov.UI.modal({
        title: 'Кто заполняет «Товар» и «Количество»', glyph: 'users',
        body: el => el.append(
          h('div.wh-editors-note', icon('info'), h('span', 'Список общий для всех проектов — и нынешних, и будущих')),
          list,
          button('Добавить сотрудника', { kind: 'ghost', icon: 'plus', onClick: () => {
            Kov.Views.messenger.employeePicker('Кому разрешить правку товаров', ids => {
              Kov.Store.employees().then(all => {
                ids.forEach(id => {
                  if (data.user_ids.includes(id)) return;
                  const e = all.find(x => x.id === id);
                  data.user_ids.push(id);
                  data.people.push({ id, name: Kov.UI.fullName(e || {}, true), last_name: e && e.last_name, first_name: e && e.first_name });
                });
                fill();
              });
            }, true, { okText: 'Добавить', withSelf: true });
          } })),
        buttons: [{ text: 'Отмена', kind: 'ghost' }, { text: 'Сохранить', icon: 'save', onClick: async close => {
          try {
            await Kov.Api.put('warehouse/editors', { json: { user_ids: data.user_ids } });
            Kov.UI.toast('Список сохранён', 'success');
            close(); load();
          } catch (e) { Kov.UI.toast(Kov.UI.errorText(e), 'error'); }
        } }],
      });
    }

    // Смена ориентации или окна: раскладка перестраивается только при
    // переходе через границу, иначе перерисовка на каждый пиксель
    let wasPhone = isPhone();
    const onResize = () => { const now = isPhone(); if (now !== wasPhone) { wasPhone = now; draw(); } };
    window.addEventListener('resize', onResize);

    // Событие о выдаче: ведомость обновляется у всех, кто её сейчас смотрит.
    // Перечитываем целиком — цифры «выдано» и «осталось» считает сервер,
    // и складывать их на клиенте значило бы держать вторую версию правды.
    let reloadTimer = null;
    const offWs = Kov.WS && Kov.WS.on ? Kov.WS.on(evt => {
      if (!evt) return;
      const own = evt.actor_id === state.access.user_id;
      if (evt.type === 'warehouse.updated') {
        // Справочник единиц и список доверенных общие для всех проектов,
        // поэтому такие события принимаем без проверки проекта
        const global = evt.what === 'units' || evt.what === 'editors';
        if (!global && String(evt.project_id) !== String(projectId)) return;
        if (own) return;                        // свои правки уже отражены
        clearTimeout(reloadTimer);
        reloadTimer = setTimeout(() => load({ soft: true }), 400);
        return;
      }
      if (evt.type !== 'warehouse.issue') return;
      if (String(evt.project_id) !== String(projectId)) return;
      clearTimeout(reloadTimer);
      reloadTimer = setTimeout(() => load({ soft: true }), 250);   // пачку событий сводим в одно чтение
      if (!own) toastEvent(evt);
    }) : null;

    function toastEvent(evt) {
      const what = evt.name ? '«' + evt.name + '»' : 'товар';
      if (evt.action === 'created' && evt.receiver_id === state.access.user_id)
        return Kov.UI.toast('Вам выдают ' + what + ' — подтвердите получение', 'info');
      if (evt.action === 'confirmed' && evt.issuer_id === state.access.user_id)
        return Kov.UI.toast('Получение ' + what + ' подтверждено', 'success');
      if (evt.action === 'cancelled') return Kov.UI.toast('Выдача ' + what + ' отменена', 'warning');
    }

    load();
    return { el: root, load, destroy: () => {
      window.removeEventListener('resize', onResize);
      clearTimeout(reloadTimer);
      if (offWs) offWs();
    } };
  }

  /**
   * pendingPanel() → элемент со всеми выдачами, ждущими сотрудника, по всем
   * проектам сразу. Встраивается наверх раздела «Уведомления»: именно туда
   * человек идёт смотреть, что от него ждут. Пусто — элемент не показывается.
   */
  function pendingPanel() {
    const list = h('div.wh-pending');
    const box = h('div.wh-global', { hidden: true },
      h('div.wh-global-head', icon('archive'), h('b', 'Склад: ждут вашего подтверждения'), h('span.count')), list);

    async function load() {
      let items = [];
      try { const data = await Kov.Api.get('warehouse/issues/pending'); items = (data && data.items) || []; }
      catch (e) { box.hidden = true; return; }
      Kov.UI.clear(list);
      box.hidden = !items.length;
      box.querySelector('.count').textContent = items.length ? String(items.length) : '';
      items.forEach(p => {
        const mine = p.mine;
        const who = mine ? ('от ' + ((p.issuer && p.issuer.name) || 'склада'))
                         : ('для ' + ((p.receiver && p.receiver.name) || 'сотрудника'));
        list.append(h('div.wh-pending-card' + (mine ? '.mine' : ''),
          h('span.circ', icon(mine ? 'download' : 'clock')),
          h('div.who',
            h('b', p.name || ('Строка ' + p.row_no)),
            h('span', show(p.quantity) + ' ' + (p.unit || '') + ' · ' + who),
            h('a.small.muted', { href: '#/projects/' + p.project_id + '/warehouse' }, 'Проект: ' + (p.project || '№' + p.project_id))),
          mine ? button('Подтвердить', { icon: 'check', onClick: () => act('confirm', p) }) : null,
          button(mine ? 'Отказаться' : 'Отменить', { kind: 'ghost', icon: 'close', onClick: () => act('cancel', p) })));
      });
    }
    async function act(what, p) {
      if (what === 'cancel' && !await Kov.UI.confirm('Отмена выдачи',
        'Отменить выдачу «' + (p.name || '') + '» до подтверждения?', { okText: 'Отменить выдачу', danger: true })) return;
      try {
        await Kov.Api.post('warehouse/issues/' + p.id + '/' + what, { json: {} });
        Kov.UI.toast(what === 'confirm' ? 'Получение подтверждено' : 'Выдача отменена', 'success');
        load();
      } catch (e) { Kov.UI.toast(Kov.UI.errorText(e), 'error'); }
    }
    load();
    // список ожидающих обновляется от того же события, что и ведомость
    const off = Kov.WS && Kov.WS.on ? Kov.WS.on(evt => {
      if (evt && (evt.type === 'warehouse.issue' || evt.type === 'notification.new')) load();
    }) : null;
    return { el: box, load, destroy: () => { if (off) off(); } };
  }

  return { block, pendingPanel };
})();
