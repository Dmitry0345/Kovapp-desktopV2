/* Раздел «Проекты» веб-версии — зеркало десктопного: таблица со столбцами
 * по руководителям отделов, диалог проекта, окно проекта 40/60 с задачами и
 * разделами «Договоры / Фото / Проект / Сдача». Настройки — projects.list и
 * projects.tasks на сервере, общие с десктопом. */
window.Kov = window.Kov || {};
Kov.Views = Kov.Views || {};

Kov.Views.projects = (function () {
  const { h, icon, button, sectionHeader, field, fmtDate, deadlineState } = Kov.UI;
  const SORTS = [['deadline_desc', 'Дата окончания: сначала поздние'], ['deadline_asc', 'Дата окончания: сначала ранние'], ['name_asc', 'Название: А → Я'], ['name_desc', 'Название: Я → А'], ['created_desc', 'Созданы: сначала новые'], ['created_asc', 'Созданы: сначала старые']];
  const TASK_SORTS = [['deadline_asc', 'Дата окончания: по возрастанию'], ['deadline_desc', 'Дата окончания: по убыванию'], ['id_asc', 'ID: по возрастанию'], ['id_desc', 'ID: по убыванию'], ['created_desc', 'Созданы: сначала новые'], ['created_asc', 'Созданы: сначала старые'], ['name_asc', 'Название: А → Я']];
  const SECTIONS = [['contracts', 'Договоры', 'file', 'Договоры и соглашения по проекту'], ['photos', 'Медиаконтент', 'image', 'Фото и видео задач проекта'], ['project', 'Проект', 'folder', 'Проектная документация'], ['delivery', 'Сдача', 'check', 'Акты, сдача и приёмка']];
  // Склад стоит отдельной широкой карточкой под четырьмя файловыми: это не
  // папка с файлами, а ведомость, и половина пустой строки в сетке не остаётся
  const WAREHOUSE = ['warehouse', 'Склад', 'archive', 'Ведомость товаров, выдача и остатки'];

  const isHead = () => Kov.Store.isDepartmentHead();
  // Заводить проекты вправе договорной отдел, руководители отделов и
  // руководство; править — только свои, кроме директора и администратора
  const canCreate = () => Kov.Rights.canCreateProject();
  const canEdit = project => Kov.Rights.canEditProject(project);
  function personName(p) { return p ? Kov.UI.fullName(p) : ''; }

  // ══════════════════════════ список ══════════════════════════

  const L = { projects: [], heads: [], prefs: { sort: 'deadline_desc', organization: null, head_id: null }, search: '', selected: null, ui: null, loadedPrefs: false };

  async function loadList() {
    if (!L.loadedPrefs) {
      const saved = await Kov.Store.pref('projects.list');
      if (saved) Object.assign(L.prefs, saved);
      L.loadedPrefs = true;
    }
    [L.heads, L.projects] = await Promise.all([
      Kov.Api.get('projects/heads').catch(() => []),
      Kov.Api.get('projects', { query: { sort: L.prefs.sort, organization: L.prefs.organization, head_id: L.prefs.head_id, search: L.search.trim() || null, is_archived: !!L.archived } }).catch(e => { Kov.UI.toast('Проекты не загружены: ' + Kov.UI.errorText(e), 'error'); return []; }),
    ]);
    if (L.ui && L.ui.count) L.ui.count.textContent = 'Проектов: ' + (L.projects || []).length;
  }

  /** Фамилия и имя создателя в две строки — как в шапке столбца */
  function ownerTitle(p) {
    const who = p.creator || (L.heads.find(x => x.id === p.creator_id)) || null;
    const last = (who && who.last_name) || '';
    const first = (who && who.first_name) || '';
    if (last || first) return last + '\n' + first;
    return p.creator_id ? 'Сотрудник №' + p.creator_id : 'Без создателя';
  }

  /**
   * Столбец — на создателя проекта. Пустых столбцов не бывает: сотрудник без
   * проектов в таблице не появляется. Общего столбца «Прочие» больше нет —
   * создатель есть у каждого проекта, и относить его некуда.
   */
  function buildColumns() {
    const selectedHead = L.prefs.head_id;
    const byOwner = new Map();
    L.projects.forEach(p => {
      const owner = p.creator_id != null ? p.creator_id : 0;
      if (selectedHead != null && owner !== selectedHead) return;
      if (!byOwner.has(owner)) byOwner.set(owner, { owner, title: ownerTitle(p), items: [] });
      byOwner.get(owner).items.push(p);
    });
    return [...byOwner.values()].sort((a, b) => a.title.localeCompare(b.title, 'ru'));
  }

  function view(section, projectId, sectionKey) {
    if (projectId) return viewProject(section, Number(projectId), sectionKey || null);
    L.ui = {};
    section.classList.add('fill');
    const search = Kov.UI.searchInput('Поиск по названию проекта…', v => { L.search = v; scheduleReload(); });
    const sort = Kov.UI.select(SORTS, L.prefs.sort, v => { L.prefs.sort = v; savePrefs(); reload(); });
    // организаций и сотрудников могут быть сотни — выбор с поиском, как в формах
    const orgSel = Kov.UI.pickerSelect([[null, 'Все организации']], L.prefs.organization || null,
      v => { L.prefs.organization = v; savePrefs(); reload(); },
      { placeholder: 'Все организации', searchPlaceholder: 'Поиск организации…', title: 'Организация' });
    const headSel = Kov.UI.pickerSelect([[null, 'Все создатели']], L.prefs.head_id != null ? L.prefs.head_id : null,
      v => { L.prefs.head_id = v; savePrefs(); reload(); },
      { placeholder: 'Все создатели', searchPlaceholder: 'Поиск сотрудника…', title: 'Создатель проекта' });
    L.ui.sort = sort; L.ui.org = orgSel; L.ui.head = headSel;
    L.ui.tableWrap = h('div.table-wrap');
    L.ui.empty = h('div.empty', { hidden: true }, 'Проектов пока нет');
    // как у задач: на телефоне одной строкой с короткими подписями, главная кнопка последней
    const actions = Kov.UI.fitRow(h('div.btn-row.end.list-actions'), ['icons-only']);
    L.ui.edit = twoLabels('Редактировать', 'Изменить', { kind: 'ghost', icon: 'edit', disabled: true, onClick: () => L.selected && editDialog(L.selected) });
    L.ui.del = twoLabels('Удалить', 'Удалить', { kind: 'danger', icon: 'trash', disabled: true, onClick: deleteSelected });
    if (canCreate()) actions.append(L.ui.edit, L.ui.del, twoLabels('Создать проект', 'Создать', { icon: 'plus', onClick: () => editDialog(null) }));
    // «Активные / Архивные» — как в задачах: компьютер — левый угол строки кнопок, телефон — над ней со счётчиком
    L.ui.arch = Kov.UI.segmented([['active', 'Активные', 'flag'], ['archived', 'Архивные', 'archive']], L.archived ? 'archived' : 'active', key => {
      L.archived = key === 'archived'; L.selected = null; reload();
    });
    L.ui.arch.classList.add('proj-arch');
    L.ui.count = h('span.small.muted.proj-count');
    if (!Kov.UI.touchUi()) actions.prepend(h('div.proj-lead', L.ui.arch, L.ui.count));   // компьютер: слева — переключатель и счётчик
    // телефон: одна строка — поиск и «Сортировка и фильтры» (шторка); компьютер — всё в ряд, как было
    let toolbar;
    if (Kov.UI.touchUi()) {
      const badge = h('span.filter-badge');
      const paintBadge = () => {
        const n = (L.prefs.organization ? 1 : 0) + (L.prefs.head_id != null ? 1 : 0) + (L.prefs.sort && L.prefs.sort !== SORTS[0][0] ? 1 : 0);
        badge.textContent = n ? String(n) : ''; badge.hidden = !n;
      };
      const fbtn = button('', { kind: 'ghost', icon: 'filter', title: 'Сортировка и фильтры', onClick: () =>
        Kov.UI.bottomSheet('Сортировка и фильтры', h('div.sheet-form',
          Kov.UI.field('Сортировка', sort), Kov.UI.field('Организация', orgSel), Kov.UI.field('Создатель проекта', headSel)),
          { onClose: paintBadge }) });
      fbtn.classList.add('filter-btn'); fbtn.append(badge); paintBadge();
      toolbar = h('div.toolbar.one-row', search.el, fbtn);
    } else toolbar = h('div.toolbar', search.el, sort, orgSel, headSel);
    // встроенный append печатает null словом «null» — пустое не передаём
    const switchRow = Kov.UI.touchUi() ? Kov.UI.fitRow(h('div.proj-switch-row.list-switch', L.ui.arch, L.ui.count), ['fit-tight', 'fit-bare', 'fit-wrap']) : null;
    section.append(...[toolbar, L.ui.tableWrap, switchRow, actions].filter(Boolean));
    L.ui.tableWrap.append(Kov.UI.bigLoading('Загружаем проекты…'));
    L.ui.section = section;
    reload();
  }

  let reloadTimer = null;
  function scheduleReload() { clearTimeout(reloadTimer); reloadTimer = setTimeout(reload, 250); }

  // задача видна в проекте, но открыть её, её этапы и файлы может только тот, у кого есть доступ
  function noAccess() { Kov.UI.toast('Нет доступа к этой задаче: её этапы и файлы открыты только участникам', 'warning'); }

  async function savePrefs() { try { await Kov.Store.setPref('projects.list', L.prefs); } catch (e) { /* */ } }

  async function reload(opts) {
    if (!L.ui || !L.ui.tableWrap.isConnected) return;
    await loadList();
    // по событию: те же проекты — ничего не перерисовываем
    let sig = '';
    try { sig = JSON.stringify(L.projects); } catch (e) { sig = String(Date.now()); }
    if (opts && opts.quiet && sig === L.sig) return;
    L.sig = sig;
    // динамические фильтры
    try { const orgs = await Kov.Api.get('projects/organizations'); L.ui.org.setOptions([[null, 'Все организации']].concat(orgs.map(o => [o, o])), true); if (L.prefs.organization) L.ui.org.setValue(L.prefs.organization); } catch (e) { /* */ }
    // В списке только те, у кого проекты есть: выбор пустого столбца
    // показывал бы пустую таблицу
    const creators = new Map();
    L.projects.forEach(p => {
      if (p.creator_id != null && !creators.has(p.creator_id)) {
        creators.set(p.creator_id, personName(p.creator) || ('Сотрудник №' + p.creator_id));
      }
    });
    L.heads.forEach(x => { if (creators.has(x.id)) creators.set(x.id, personName(x)); });
    if (L.prefs.head_id != null && !creators.has(L.prefs.head_id)) {
      const kept = L.heads.find(x => x.id === L.prefs.head_id);
      creators.set(L.prefs.head_id, kept ? personName(kept) : 'Выбранный сотрудник');
    }
    const options = [...creators.entries()].sort((a, b) => String(a[1]).localeCompare(String(b[1]), 'ru'));
    L.ui.head.setOptions([[null, 'Все создатели']].concat(options), true);
    if (L.prefs.head_id != null) L.ui.head.setValue(L.prefs.head_id);
    renderTable();
  }

  /**
   * Телефон: колонок по числу руководителей не помещается, поэтому вместо
   * широкой таблицы — список карточек, сгруппированный по руководителю.
   * На компьютере остаётся прежняя таблица.
   */
  function renderMobileList() {
    const columns = buildColumns();
    const box = h('div.project-cards');
    columns.forEach(c => {
      if (!c.items.length) return;
      box.append(h('div.group-head', c.title.replace('\n', ' '), h('span.muted.small', 'проектов: ' + c.items.length)));
      c.items.forEach(p => {
        const state = deadlineState(p.deadline);
        const cardEl = h('button.project-card' + (state ? '.' + state : ''), { type: 'button' },
          h('div.t', p.name || ''),
          h('div.s', [p.organization || null, p.deadline ? 'до ' + fmtDate(p.deadline) : null].filter(Boolean).join(' · ')),
          h('div.s.muted', 'задач: ' + (p.tasks_total || 0) + ' · активных: ' + (p.tasks_active || 0)
            + ' · файлов: ' + (p.files_total || 0)));
        const select = () => {
          box.querySelectorAll('.project-card.on').forEach(x => x.classList.remove('on'));
          cardEl.classList.add('on');
          L.selected = p; updateButtons();
        };
        // сенсорный экран — как в задачах: касание открывает, удержание выделяет (для «Изменить» и «Удалить»)
        cardEl.addEventListener('click', () => {
          if (isTouch() || cardEl.classList.contains('on')) return openProject(p.id);
          select();
        });
        Kov.UI.longPress(cardEl, select);
        box.append(cardEl);
      });
    });
    Kov.UI.clear(L.ui.tableWrap);
    if (columns.some(c => c.items.length)) L.ui.tableWrap.append(box);
    L.ui.empty.hidden = columns.some(c => c.items.length);
    L.ui.tableWrap.append(L.ui.empty);
    L.selected = null; updateButtons();
  }

  // Сколько колонок помещается на экран без прокрутки вбок
  const COLS_PER_PAGE = 4;
  let colPage = 0;

  function renderTable() {
    if (Kov.Scale.isMobile()) return renderMobileList();
    const all = buildColumns();
    // Колонок бывает больше, чем влезает: показываем их страницами со
    // стрелками и точками — вбок ничего не уезжает
    const pages = Math.max(1, Math.ceil(all.length / COLS_PER_PAGE));
    if (colPage > pages - 1) colPage = pages - 1;
    if (colPage < 0) colPage = 0;
    const columns = pages > 1 ? all.slice(colPage * COLS_PER_PAGE, (colPage + 1) * COLS_PER_PAGE) : all;
    const rows = Math.max(0, ...columns.map(c => c.items.length));
    const table = h('table.data.projects-table');
    table.append(h('thead', h('tr', h('th', { style: { width: '3rem' } }, '№'), columns.map(c => h('th', c.title)))));
    const tbody = h('tbody');
    for (let r = 0; r < rows; r++) {
      const tr = h('tr');
      tr.append(h('td', h('span.muted', String(r + 1))));
      columns.forEach(c => {
        const p = c.items[r];
        if (!p) { tr.append(h('td')); return; }
        const state = deadlineState(p.deadline);
        const cell = h('td.left.project-cell', { tabIndex: 0, style: { cursor: 'pointer', background: state === 'overdue' ? 'var(--critical-soft)' : state === 'urgent' ? 'var(--warning-soft)' : '' } },
          h('div', { style: { fontWeight: 700 } }, p.name || ''),
          p.deadline ? h('div.small', { style: { color: state === 'overdue' ? 'var(--critical)' : state === 'urgent' ? 'var(--warning-text)' : 'var(--text-label)' } }, 'до ' + fmtDate(p.deadline)) : null,
        );
        cell.title = [p.name, p.organization ? 'Организация: ' + p.organization : null, p.responsible ? 'Ответственный: ' + personName(p.responsible) : null, p.creator ? 'Создал: ' + personName(p.creator) : null, 'Задач: ' + (p.tasks_total || 0) + ' (активных ' + (p.tasks_active || 0) + ') · файлов: ' + (p.files_total || 0)].filter(Boolean).join('\n');
        cell.addEventListener('click', () => { if (isTouch()) openProject(p.id); else selectCell(cell, p); });
        Kov.UI.longPress(cell, () => selectCell(cell, p));
        cell.addEventListener('dblclick', () => openProject(p.id));
        cell.addEventListener('keydown', e => { if (e.key === 'Enter') openProject(p.id); });
        tr.append(cell);
      });
      tbody.append(tr);
    }
    table.append(tbody);
    Kov.UI.clear(L.ui.tableWrap);
    if (pages > 1) {
      const dots = h('div.col-dots');
      for (let i = 0; i < pages; i++) {
        dots.append(h('button.dot' + (i === colPage ? '.on' : ''), { type: 'button',
          title: 'Показать руководителей ' + (i * COLS_PER_PAGE + 1) + '–' + Math.min(all.length, (i + 1) * COLS_PER_PAGE),
          onclick: () => { colPage = i; renderTable(); } }));
      }
      const from = colPage * COLS_PER_PAGE + 1;
      const to = Math.min(all.length, (colPage + 1) * COLS_PER_PAGE);
      L.ui.tableWrap.append(h('div.col-pager',
        button('', { kind: 'ghost', icon: 'chevron_left', title: 'Предыдущие руководители',
          disabled: colPage === 0, onClick: () => { colPage--; renderTable(); } }),
        h('span.t', 'Руководители ' + from + '–' + to + ' из ' + all.length),
        dots,
        button('', { kind: 'ghost', icon: 'chevron_right', title: 'Следующие руководители',
          disabled: colPage >= pages - 1, onClick: () => { colPage++; renderTable(); } })));
    }
    if (columns.length) L.ui.tableWrap.append(table);
    L.ui.empty.hidden = columns.length > 0;
    L.ui.tableWrap.append(L.ui.empty);
    L.selected = null; updateButtons();
  }

  // сенсорный экран: то же условие, что у таблицы задач (Kov.UI.table)
  function isTouch() {
    return window.matchMedia('(pointer: coarse)').matches || window.matchMedia('(max-width: 760px)').matches;
  }

  function selectCell(cell, p) {
    L.ui.tableWrap.querySelectorAll('td').forEach(td => td.classList.remove('selected'));
    cell.classList.add('selected');
    L.selected = p; updateButtons();
  }
  function updateButtons() {
    L.ui.edit.disabled = !(L.selected && L.selected.can_edit);
    L.ui.del.disabled = !(L.selected && L.selected.can_delete);
  }

  async function deleteSelected() {
    const p = L.selected; if (!p) return;
    if (!await Kov.UI.confirm('Удаление проекта', 'Удалить проект «' + p.name + '»?\nФайлы разделов будут удалены, задачи останутся без проекта.', { okText: 'Удалить', danger: true })) return;
    try { await Kov.Api.del('projects/' + p.id); Kov.UI.toast('Проект удалён', 'success'); reload(); }
    catch (e) { Kov.UI.toast('Не удалось удалить: ' + Kov.UI.errorText(e), 'error'); }
  }

  // ══════════════════════════ диалог проекта ══════════════════════════

  async function editDialog(project, onSaved) {
    // Проект открывается отдельной страницей: полей много, во всплывающем окне тесно
    const id = project && project.id;
    Kov.Shell.show('projects', id ? 'Редактирование проекта' : 'Новый проект', async section => {
      section.append(Kov.UI.bigLoading('Готовим форму…'));
      const [candidates, orgList] = await Promise.all([
        Kov.Api.get('projects/responsible-candidates').catch(() => []),
        Kov.Api.get('organizations').catch(() => []),
      ]);
      let current = project;
      if (id) { try { current = await Kov.Api.get('projects/' + id); } catch (e) { /* оставим переданный */ } }
      current = current || {};
      Kov.UI.clear(section);

      const name = h('input.input', { placeholder: 'Название проекта', value: current.name || '' });

      // организация — список с поиском, только из справочника
      const organizations = (orgList || []).map(o => o.name).filter(Boolean).sort((a, b) => a.localeCompare(b, 'ru'));
      const orgPick = Kov.UI.pickerSelect(
        [[null, '— Не указана —']].concat(organizations.map(o => [o, o])),
        current.organization && organizations.includes(current.organization) ? current.organization : null,
        () => checkOrg(),
        { placeholder: '— Не указана —', searchPlaceholder: 'Поиск по названию организации…', empty: 'Организация не найдена' });
      const orgHint = h('div.small.muted');
      function checkOrg() {
        const v = orgPick.getValue();
        orgHint.textContent = !v ? 'Можно оставить пустым' : 'Организация из справочника';
      }
      // организация проекта могла быть заведена раньше и потом удалена из справочника
      if (current.organization && !organizations.includes(current.organization)) {
        orgPick.setOptions([[null, '— Не указана —'], [current.organization, current.organization + ' (нет в справочнике)']]
          .concat(organizations.map(o => [o, o])), true);
        orgPick.setValue(current.organization);
      }
      checkOrg();

      // ответственный — как выбор людей в задаче: чип с ФИО и окно со списком
      let responsibleId = current.responsible_id || null;
      let responsibleLocked = false;
      const respChip = h('div.chips');
      const respBtn = button('Выбрать', { kind: 'ghost', icon: 'users', onClick: () => pickResponsible() });
      const responsible = h('div.people-field', respChip, h('div.btn-row', respBtn));

      function candidateOf(id) { return (candidates || []).find(c => c.id === id) || null; }
      function drawResponsible() {
        Kov.UI.clear(respChip);
        const person = candidateOf(responsibleId);
        if (!person) { respChip.append(h('span.muted', 'Не назначен')); return; }
        const chip = h('span.chip.person', { title: [personName(person), person.position, person.department].filter(Boolean).join(' · ') },
          Kov.UI.avatar(person, null, Kov.Store.photoUrl(person.id)), h('span.person-name', personName(person)));
        if (!responsibleLocked) chip.append(h('button.chip-x', { type: 'button', title: 'Снять',
          onclick: () => { responsibleId = null; drawResponsible(); } }, '×'));
        respChip.append(chip);
      }

      function pickResponsible() {
        let chosen = responsibleId;
        const listBox = h('div.checklist', { style: { maxHeight: '23rem' } });
        const search = Kov.UI.searchInput('Поиск по ФИО, отделу, должности…', renderList);
        const byDepartment = [...new Set((candidates || []).map(c => c.department).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'ru'));
        const depPick = Kov.UI.select([[null, 'Все отделы']].concat(byDepartment.map(d => [d, d])), null, () => renderList(search.input.value),
          { title: 'Показать только один отдел' });
        function renderList(needle) {
          needle = (needle || '').trim().toLowerCase();
          const dep = depPick.getValue();
          Kov.UI.clear(listBox);
          const rows = (candidates || []).filter(c => (!dep || c.department === dep) && (!needle ||
            (personName(c) + ' ' + (c.department || '') + ' ' + (c.position || '')).toLowerCase().includes(needle)));
          rows.forEach(c => {
            const radio = h('input', { type: 'radio', name: 'proj-resp', checked: chosen === c.id,
              onchange: () => { chosen = c.id; } });
            listBox.append(h('label', radio, Kov.UI.avatar(c, null, Kov.Store.photoUrl(c.id)),
              h('span', { style: { flex: 1 } }, personName(c),
                h('span.muted.small', ' — ' + [c.position, c.department].filter(Boolean).join(', ')))));
          });
          if (!rows.length) listBox.append(h('div.muted', { style: { padding: '.4rem' } }, 'Никого не найдено'));
        }
        renderList('');
        Kov.UI.modal({
          title: 'Ответственный за проект', glyph: 'users', width: '41.4rem',
          body: el => el.append(h('div.small.muted', 'Ответственным может быть руководитель отдела или директор'),
            h('div.toolbar', search.el, depPick), listBox),
          buttons: [
            { text: 'Снять', kind: 'ghost', icon: 'close', onClick: close => { responsibleId = null; drawResponsible(); close(); } },
            { text: 'Готово', icon: 'check', onClick: close => { responsibleId = chosen; drawResponsible(); close(); } },
          ],
        });
      }

      if (isHead() && !Kov.Store.isManagement()) {
        responsibleId = Kov.Api.state.userId;
        responsibleLocked = true;
        respBtn.disabled = true;
        respBtn.title = 'Старший по отделу назначается ответственным автоматически';
      }
      drawResponsible();

      // срок обязателен: проекта без даты окончания быть не может
      // новый проект — срок не раньше сегодняшнего дня
      const deadline = h('input.input', Object.assign({ type: 'date', value: current.deadline ? String(current.deadline).slice(0, 10) : Kov.UI.isoDate(new Date(Date.now() + 30 * 86400000)) }, current.id ? {} : { min: Kov.UI.isoDate(new Date()) }));
      const description = h('textarea.textarea', { placeholder: 'Кратко о проекте (необязательно)', value: current.description || '' });

      async function save() {
        const title = name.value.trim();
        if (!title) return Kov.UI.toast('Укажите название проекта', 'warning');
        if (!deadline.value) { Kov.UI.softFocus(deadline); return Kov.UI.toast('Укажите дату окончания — без неё проект сохранить нельзя', 'warning'); }
        if (!current.id && deadline.value < Kov.UI.isoDate(new Date())) { Kov.UI.softFocus(deadline); return Kov.UI.toast('Дата окончания не может быть раньше сегодняшнего дня', 'warning'); }
        const payload = {
          name: title,
          organization: orgPick.getValue() || null,
          responsible_id: responsibleLocked ? Kov.Api.state.userId : responsibleId,
          deadline: deadline.value,
          description: description.value.trim() || null,
        };
        try {
          const saved = id ? await Kov.Api.put('projects/' + id, { json: payload }) : await Kov.Api.post('projects', { json: payload });
          Kov.UI.toast(id ? 'Проект сохранён' : 'Проект создан', 'success');
          if (onSaved) onSaved(saved);
          Kov.Router.go(id ? '#/projects/' + id : '#/projects');
        } catch (e) { Kov.UI.toast('Не удалось сохранить: ' + Kov.UI.errorText(e), 'error'); }
      }

      section.append(h('div.card.form-card', { style: { display: 'flex', flexDirection: 'column', gap: '.8rem' } },
        h('div.form-grid', field('Название', name, true), h('div.field', h('label', 'Организация'), orgPick, orgHint)),
        h('div.form-grid', field('Ответственный', responsible), field('Дата окончания', deadline, true)),
        field('Описание', description),
        h('div.btn-row.end',
          button('Отмена', { kind: 'ghost', onClick: () => Kov.Router.go(id ? '#/projects/' + id : '#/projects') }),
          button('Сохранить', { icon: 'save', onClick: save })),
      ));
    });
  }

  function openProject(id) { Kov.Router.go('#/projects/' + id); }

  function twoLabels(full, short, opts) {
    const text = document.createDocumentFragment();
    text.append(h('span.lbl-full', full), h('span.lbl-short', short));
    const b = button(text, opts);
    b.setAttribute('aria-label', full);
    return b;
  }

  // шапка открытого проекта: название и карточка сведений (телефон — строками, компьютер — колонками)
  function projectHead(p, state) {
    const text = document.createDocumentFragment();
    text.append(h('span.lbl-full', 'К проектам'));
    const back = button(text, { kind: 'ghost', icon: 'arrow_back', onClick: () => Kov.Router.go('#/projects') });
    back.classList.add('proj-back');
    back.setAttribute('aria-label', 'К проектам');
    back.title = 'К проектам';
    const cell = (glyph, title, value) => h('div.proj-cell', h('span.proj-ico', icon(glyph)), h('div.proj-txt', h('span.proj-lbl', title), value));
    const due = p.deadline
      ? h('span.proj-due' + (state ? '.' + state : ''), 'до ' + fmtDate(p.deadline) + ({ overdue: ' · просрочен', urgent: ' · меньше суток' }[state] || ''))
      : h('span.proj-val.muted', 'не задан');
    const info = h('div.proj-info', [
      p.organization ? cell('building', 'Организация', h('span.proj-val', p.organization)) : null,
      p.responsible ? cell('user', 'Ответственный', h('span.proj-val', personName(p.responsible))) : null,
      cell('calendar', 'Срок', due),
      p.description ? Object.assign(cell('note', 'Описание', h('span.proj-desc', p.description)), { className: 'proj-cell proj-cell-desc' }) : null,
    ].filter(Boolean));
    const done = completeControl(p);
    if (!window.matchMedia('(max-width: 760px)').matches) {
      V.ui.info = info;   // компьютер: сведения — в правой колонке над подразделами, в две строки
      return h('div.proj-head', h('div.proj-top', back, h('h2.proj-title', p.name || 'Проект'), done ? h('div.proj-done', done) : null));
    }
    if (done) info.append(h('div.proj-done.phone', done));   // телефон: внизу сведений проекта
    // телефон: сведения скрыты — «Сведения» раскрывает их
    info.hidden = !V.infoOpen;   // раскрытые сведения остаются раскрытыми после завершения или возврата в работу
    const tog = h('button.proj-more' + (V.infoOpen ? '.open' : ''), { type: 'button', 'aria-expanded': String(!!V.infoOpen) }, icon('info'), h('span', 'Сведения'), h('span.chev', icon('chevron_down')));
    tog.onclick = () => { info.hidden = !info.hidden; V.infoOpen = !info.hidden; tog.classList.toggle('open', !info.hidden); tog.setAttribute('aria-expanded', String(!info.hidden)); };
    return h('div.proj-head.collapsible', h('div.proj-top', back, h('span.proj-spacer'), tog), info);   // название — в верхней строке
  }

  // ══════════════════════════ окно проекта ══════════════════════════

  const V = { project: null, tasks: [], prefs: { state: 'all', sort: 'deadline_asc', department: null, stage_count: null, created_from: null, created_to: null }, loadedPrefs: false, ui: null, sectionKey: null };

  async function viewProject(section, projectId, sectionKey) {
    section.append(Kov.UI.bigLoading('Открываем проект…'));
    try { V.project = await Kov.Api.get('projects/' + projectId); }
    catch (e) { Kov.UI.clear(section); section.append(h('div.empty', 'Проект не открыт: ' + Kov.UI.errorText(e)), h('div.btn-row', button('К проектам', { kind: 'ghost', icon: 'arrow_back', onClick: () => Kov.Router.go('#/projects') }))); return; }
    if (!V.loadedPrefs) { const saved = await Kov.Store.pref('projects.tasks'); if (saved) Object.assign(V.prefs, saved); V.loadedPrefs = true; }
    V.ui = { section };
    V.sectionKey = null;
    renderProject();
    reloadTasks();
    // пришли по прямой ссылке на подраздел — открываем его сразу
    if (sectionKey && (sectionKey === WAREHOUSE[0] || SECTIONS.some(x => x[0] === sectionKey))) openSection(sectionKey, true);
  }

  // завершение проекта: создатель, директор или администратор; пока есть незавершённые задачи — недоступно
  function completeControl(p) {
    if (!p.can_complete) return null;
    if (p.is_archived) {
      const by = [p.archived_at ? Kov.UI.fmtDate(p.archived_at) : '', p.archived_by ? personName(p.archived_by) : ''].filter(Boolean).join(' · ');
      return h('div.proj-done-row', h('span.chip', 'Завершён' + (by ? ' ' + by : '')),
        button('Вернуть в работу', { kind: 'ghost', icon: 'refresh', onClick: () => setArchived(p, false) }));
    }
    const left = Number(p.tasks_unfinished != null ? p.tasks_unfinished : p.tasks_active) || 0;
    const btn = button('Завершить проект', { icon: 'check', disabled: left > 0, onClick: () => setArchived(p, true) });
    const hint = left ? h('span.proj-done-hint', 'Завершить нельзя — незавершённых задач: ' + left) : null;
    if (hint) btn.title = hint.textContent;
    return h('div.proj-done-row', hint, btn);
  }

  async function setArchived(p, done) {
    if (done && !(await Kov.UI.confirm('Завершение проекта', 'Завершить проект «' + (p.name || '') + '»? Он перейдёт в «Архивные».', { okText: 'Завершить' }))) return;
    try {
      await Kov.Api.post('projects/' + p.id + (done ? '/complete' : '/reopen'));
      Kov.UI.toast(done ? 'Проект завершён и перенесён в «Архивные»' : 'Проект возвращён в работу', 'success');
      viewProject(V.ui.section, p.id);
    } catch (e) { Kov.UI.toast((done ? 'Не удалось завершить проект: ' : 'Не удалось вернуть проект: ') + Kov.UI.errorText(e), 'error'); }
  }

  function renderProject() {
    const section = V.ui.section; const p = V.project;
    Kov.UI.clear(section);
    // на телефоне полное название — в шапке, сверху коротко; на компьютере название нужно и вкладке браузера
    Kov.Shell.setTitle(p.name || 'Проект');   // название проекта — в верхней строке и на телефоне
    Kov.Rights.setReadOnly(!!p.is_archived, 'Проект завершён — изменения недоступны. Вернуть его в работу может создатель, директор или администратор.');
    section.append(projectHead(p, deadlineState(p.deadline)));

    // левая колонка — задачи
    const search = Kov.UI.searchInput('Поиск по названию…', v => { V.search = v; scheduleTasks(); });
    const sort = Kov.UI.select(TASK_SORTS, V.prefs.sort, v => { V.prefs.sort = v; saveTaskPrefs(); reloadTasks(); });
    const dep = Kov.UI.select([[null, 'Все отделы']], V.prefs.department, v => { V.prefs.department = v; saveTaskPrefs(); reloadTasks(); });
    const stageCount = Kov.UI.select([[null, 'Этапов: любое']].concat(Array.from({ length: 10 }, (_, i) => [i + 1, 'Этапов: ' + (i + 1)])), V.prefs.stage_count, v => { V.prefs.stage_count = v; saveTaskPrefs(); reloadTasks(); });
    const seg = Kov.UI.segmented([['all', 'Все', 'list'], ['active', 'Активные', 'flag'], ['archived', 'Архивные', 'archive']], V.prefs.state || 'all', k => { V.prefs.state = k; saveTaskPrefs(); reloadTasks(); });
    V.ui.taskBody = h('tbody');
    const taskTable = h('table.data', h('thead', h('tr', h('th', 'Название'), h('th', 'Дата окончания'))), V.ui.taskBody);
    V.ui.taskSummary = h('div.small.muted');
    // телефон: поиск и «Сортировка и фильтры» — одна строка, задачи — карточками; компьютер — таблица, как было
    const touch = Kov.UI.touchUi();
    V.ui.taskCards = touch ? h('div.ptask-list') : null;
    let filters;
    if (touch) {
      seg.classList.add('seg-compact');
      Kov.UI.fitRow(seg, ['fit-bare']);
      const fbtn = button('', { kind: 'ghost', icon: 'filter', title: 'Сортировка и фильтры', onClick: () =>
        Kov.UI.bottomSheet('Сортировка и фильтры', h('div.sheet-form',
          Kov.UI.field('Сортировка', sort), Kov.UI.field('Отдел', dep), Kov.UI.field('Этапов', stageCount))) });
      fbtn.classList.add('filter-btn');
      filters = [h('div.toolbar.one-row', search.el, fbtn)];
    } else filters = [h('div.toolbar', search.el), h('div.toolbar', sort, dep, stageCount)];
    const left = h('div.info-card',
      h('div.caption', h('span.badge-icon', icon('list')), 'Задачи проекта'),
      ...filters, seg,
      touch ? V.ui.taskCards : h('div.table-wrap.project-tasks', taskTable), V.ui.taskSummary,
      taskButtons(p),
    );
    V.ui.dep = dep;

    // правая колонка — разделы
    V.ui.right = h('div.project-right');
    renderSectionCards(true);
    section.classList.add('project-section');
    // Телефон: колонки не помещаются рядом — переключаем «Задачи» и «Файлы»,
    // а открытый раздел занимает весь экран. На компьютере вкладки скрыты.
    const layout = h('div.project-layout.stretch.show-tasks', left, V.ui.right);
    V.ui.layout = layout;
    V.ui.tabs = Kov.UI.segmented(
      [['tasks', 'Задачи проекта', 'list'], ['files', 'Файлы проекта', 'folder']], 'tasks',
      key => showProjectTab(key));
    V.ui.tabs.classList.add('project-tabs');
    section.append(V.ui.tabs, layout);
    // телефон (вариант Б): «‹», вкладки «Задачи · N | Файлы · N» и «ⓘ» — одной строкой
    if (window.matchMedia('(max-width: 760px)').matches) {
      const spacer = section.querySelector('.proj-head.collapsible .proj-top .proj-spacer');
      if (spacer) { spacer.replaceWith(V.ui.tabs); V.ui.tabs.classList.add('in-head'); Kov.UI.fitRow(V.ui.tabs, ['fit-bare']); }
      paintTabCounts();
    }
  }

  function renderSectionCards(fromRoute) {
    if (V.ui && V.ui.right) V.ui.right.classList.remove('section-open');
    if (V.ui && V.ui.layout) V.ui.layout.classList.remove('full-right');
    if (V.ui) V.ui.fullRight = false;
    if (V.ui && V.ui.onLeaveSection) { V.ui.onLeaveSection(); V.ui.onLeaveSection = null; }
    if (!fromRoute && V.sectionKey && V.project && Kov.Router.setHash) Kov.Router.setHash('#/projects/' + V.project.id);
    Kov.UI.clear(V.ui.right);
    V.sectionKey = null;
    // телефон: каждый раздел — такая же строка, как «Склад»
    const rows = window.matchMedia('(max-width: 760px)').matches;
    const cards = h('div.section-cards' + (rows ? '.as-rows' : ''));
    SECTIONS.forEach(([key, title, glyph, subtitle]) => {
      const cnt = h('span.chip.cnt', { hidden: true });
      const card = rows
        ? h('button.section-card.wide', { type: 'button', onclick: () => openSection(key) },
          h('span.circ', icon(glyph)), h('span.body', h('span.t', title), h('span.s', subtitle)), cnt, h('span.go', icon('arrow_right')))
        : h('button.section-card', { type: 'button', onclick: () => openSection(key) },
          h('span.circ', icon(glyph)), h('span.t', title), h('span.s', subtitle), cnt);
      cards.append(card);
      if (key === 'photos') {
        Kov.Api.get('projects/' + V.project.id + '/photos').then(list => {
          const total = (list || []).reduce((n, f) => n + (f.files || []).length, 0);
          return Kov.Api.get('projects/' + V.project.id + '/files', { query: { section: 'extra' } })
            .catch(() => []).then(extra => { cnt.textContent = 'Файлов: ' + (total + (extra || []).length); cnt.hidden = false; });
        }).catch(() => {});
      } else {
        Kov.Api.get('projects/' + V.project.id + '/files', { query: { section: key } }).then(files => { cnt.textContent = 'Файлов: ' + files.length; cnt.hidden = false; }).catch(() => {});
      }
    });
    const wide = h('button.section-card.wide', { type: 'button', onclick: () => openSection('warehouse') },
      h('span.circ', icon(WAREHOUSE[2])),
      h('span.body', h('span.t', WAREHOUSE[1]), h('span.s', WAREHOUSE[3])),
      h('span.go', icon('arrow_right')));
    V.ui.right.append(cards, wide);
    if (V.ui.info && !window.matchMedia('(max-width: 760px)').matches) V.ui.right.prepend(V.ui.info);   // сведения — над подразделами
  }

  function openSection(key, fromRoute) {
    V.sectionKey = key;
    if (!fromRoute && Kov.Router.setHash) Kov.Router.setHash('#/projects/' + V.project.id + '/' + key);
    Kov.UI.clear(V.ui.right);
    const meta = key === 'warehouse' ? WAREHOUSE : SECTIONS.find(s => s[0] === key);
    const title = meta[1];
    const head = h('div.toolbar', button('Разделы', { kind: 'ghost', icon: 'arrow_back', onClick: () => renderSectionCards() }), sectionHeader(title, meta[2]));
    head.lastChild.style.flex = '1';
    head.classList.add('sec-head');
    if (key === 'warehouse') {
      // Ведомости нужна вся ширина: в правой колонке шесть столбцов не живут.
      // Колонка задач на время убирается, возврат — кнопкой «Разделы».
      const wh = Kov.Warehouse.block(V.project.id);
      V.ui.right.append(head, h('div.card.section-files.wh-card', wh.el));
      V.ui.right.classList.add('section-open');
      V.ui.fullRight = true;   // «Склад» во всю ширину — только пока открыты «Файлы проекта»
      if (V.ui.layout) V.ui.layout.classList.add('full-right');
      showProjectTab('files');
      V.ui.reloadSection = wh.load;
      V.ui.onLeaveSection = () => { if (wh.destroy) wh.destroy(); };
      return;
    }
    if (key === 'photos') {
      V.ui.right.append(head);
      V.ui.right.classList.add('section-open');   // на телефоне раздел занимает экран целиком
      showProjectTab('files');
      renderPhotos();
      return;
    }
    let earliestFirst = true;
    const sortBtn = button('Сначала ранние', { kind: 'ghost', icon: 'clock', title: 'Порядок по дате прикрепления', onClick: () => { earliestFirst = !earliestFirst; sortBtn.replaceChildren(Kov.UI.icon('clock'), earliestFirst ? 'Сначала ранние' : 'Сначала новые'); block.setSort(earliestFirst); } });
    head.append(sortBtn);
    // телефон: сортировка — значком; порядок — стрелкой (↑ ранние, ↓ поздние)
    sortBtn.classList.add('sort-btn');
    const syncSort = () => { sortBtn.dataset.order = /поздн/i.test(sortBtn.textContent) ? '↓' : '↑'; };
    syncSort(); sortBtn.addEventListener('click', () => setTimeout(syncSort, 0));
    let files = [];
    const block = Kov.Files.categoryBlock({
      noTabs: true, sortEarliestFirst: true, dropZone: true,
      getFiles: () => files,
      canUpload: V.project.can_upload !== false,
      canDelete: () => V.project.can_upload !== false,
      uploadPath: 'projects/' + V.project.id + '/files', uploadFields: { section: key },
      // файл виден сразу, не дожидаясь перезагрузки списка с сервера
      onUploaded: created => { files = created.map(f => Object.assign({ file_kind: 'project' }, f)).concat(files); },
      onChanged: load,
    });
    V.ui.right.append(head, h('div.card.section-files', block));
    V.ui.right.classList.add('section-open');
    showProjectTab('files');
    async function load(opts) {
      const soft = !!(opts && opts.soft);
      try { files = (await Kov.Api.get('projects/' + V.project.id + '/files', { query: { section: key } }) || []).map(f => Object.assign({ file_kind: 'project' }, f)); }
      catch (e) { if (!soft) Kov.UI.toast('Файлы не загружены: ' + Kov.UI.errorText(e), 'error'); files = []; }
      block.render();          // плитки собираются по ключам, список не моргает
    }
    V.ui.reloadSection = load;
    load();
  }

  async function renderPhotos() {
    let folders = [];
    let extraFiles = [];               // раздел «Дополнительные файлы» самого проекта
    const stageNames = new Map();      // task_id → { stage_number: department_name }
    const opened = new Set();          // какие задачи раскрыты — переживает перерисовку
    let uploaderId = null;

    const search = Kov.UI.searchInput('Поиск по названию задачи…', () => draw());
    const useDeadline = h('input', { type: 'checkbox' });
    const from = h('input.input', { type: 'date', value: Kov.UI.isoDate(new Date(Date.now() - 90 * 86400000)), hidden: true, style: { width: 'auto' } });
    const to = h('input.input', { type: 'date', value: Kov.UI.isoDate(new Date(Date.now() + 90 * 86400000)), hidden: true, style: { width: 'auto' } });
    useDeadline.addEventListener('change', () => { from.hidden = to.hidden = !useDeadline.checked; draw(); });
    [from, to].forEach(i => i.addEventListener('change', () => draw()));
    let uploaderSel = Kov.Files.uploaderFilter([], v => { uploaderId = v; draw(); });
    const toolbar = h('div.toolbar.media-toolbar', search.el, uploaderSel,
      h('label.checkbox', useDeadline, 'Фильтрация по дате окончания'), from, to);
    const listEl = h('div.media-list');
    V.ui.right.append(toolbar, listEl);

    function allFiles() {
      return extraFiles.concat(folders.flatMap(f => f.files || []));
    }

    async function load(opts) {
      const soft = !!(opts && opts.soft) && !!listEl.firstChild;
      // Тихое обновление: заставку не показываем, иначе раскрытые папки
      // схлопываются и список моргает на каждый чужой файл
      if (!soft) listEl.replaceChildren(Kov.UI.bigLoading('Собираем медиафайлы…'));
      const [got, extra] = await Promise.all([
        Kov.Api.get('projects/' + V.project.id + '/photos').catch(e => { Kov.UI.toast('Медиафайлы не загружены: ' + Kov.UI.errorText(e), 'error'); return []; }),
        Kov.Api.get('projects/' + V.project.id + '/files', { query: { section: 'extra' } }).catch(() => []),
      ]);
      folders = got || [];
      extraFiles = (extra || []).map(f => Object.assign({ file_kind: 'project' }, f));
      const fresh = Kov.Files.uploaderFilter(allFiles(), v => { uploaderId = v; draw(); }, uploaderId);
      uploaderSel.replaceWith(fresh); uploaderSel = fresh;
      draw({ soft });
    }

    /** Отделы этапов подтягиваем только для раскрытой задачи — лишних запросов нет */
    async function stageDepartments(taskId) {
      if (stageNames.has(taskId)) return stageNames.get(taskId);
      const map = {};
      try {
        const task = await Kov.Api.get('tasks/' + taskId);
        (task.stage_details || []).forEach(st => { map[st.stage_number] = st.department_name || ''; });
      } catch (e) { /* без отделов подпишем просто номером этапа */ }
      stageNames.set(taskId, map);
      return map;
    }

    const byUploader = f => uploaderId === null || (f.uploaded_by_id || (f.uploaded_by && f.uploaded_by.id)) === uploaderId;

    // Карточки папок переиспользуются: раскрытая папка остаётся раскрытой,
    // а новый файл просто добавляется в её список без перерисовки всего
    const cards = new Map();          // ключ папки → карточка

    function draw(opts) {
      const soft = !!(opts && opts.soft);
      const needle = (search.input.value || '').trim().toLowerCase();
      let list = folders.filter(f => (f.files || []).some(byUploader));
      if (needle) list = list.filter(f => (f.task_name || '').toLowerCase().includes(needle));
      if (useDeadline.checked) {
        list = list.filter(f => { const d = Kov.UI.parseDate(f.deadline); return d && d >= new Date(from.value) && d <= new Date(to.value + 'T23:59:59'); });
      }
      if (!soft) { Kov.UI.clear(listEl); cards.clear(); }
      const empty = listEl.querySelector('.empty');
      if (empty) empty.remove();

      const wanted = [['extra', null]].concat(list.map(f => ['task-' + f.task_id, f]));
      const seen = new Set();
      let prev = null;
      wanted.forEach(([key, folder]) => {
        seen.add(key);
        let card = cards.get(key);
        if (card && card.isConnected) {
          if (card._update) card._update(folder);
        } else {
          card = folder ? taskFolder(folder) : extraFolder();
          cards.set(key, card);
        }
        const target = prev ? prev.nextSibling : listEl.firstChild;
        if (card !== target) listEl.insertBefore(card, target);
        prev = card;
      });
      cards.forEach((card, key) => {
        if (seen.has(key)) return;
        card.remove(); cards.delete(key);
      });
      if (!list.length) listEl.append(h('div.empty', 'В задачах проекта пока нет фото и видео'));
    }

    /** Постоянная папка проекта: есть у любого проекта, файлы кладутся прямо сюда */
    function extraFolder() {
      const canUpload = V.project.can_upload !== false;
      const body = h('div.folder-body', { hidden: true });
      const card = h('div.card.folder-card.extra-folder',
        h('button.folder-head', { type: 'button', onclick: () => toggle() },
          h('span.folder-arrow', icon('chevron_down')), icon('folder'),
          h('span.t', 'Дополнительные файлы'),
          h('span.small.muted', 'файлов: ' + extraFiles.filter(byUploader).length)),
        body);
      let built = false;
      let blockRef = null;
      function toggle(force) {
        const show = force !== undefined ? force : body.hidden;
        body.hidden = !show;
        card.classList.toggle('open', show);
        if (show) opened.add('extra'); else opened.delete('extra');
        if (show && !built) {
          built = true;
          const block = Kov.Files.categoryBlock({
            noTabs: true, sortEarliestFirst: true, dropZone: true,
            getFiles: () => extraFiles.filter(byUploader),
            canUpload, canDelete: () => canUpload,
            uploadHint: 'Загрузка в этот раздел недоступна',
            uploadPath: 'projects/' + V.project.id + '/files', uploadFields: { section: 'extra' },
            onUploaded: created => { extraFiles = created.map(f => Object.assign({ file_kind: 'project' }, f)).concat(extraFiles); },
            onChanged: () => load({ soft: true }),
          });
          blockRef = block;
          body.append(block);
        }
      }
      if (opened.has('extra')) toggle(true);
      // Обновление на месте: счётчик и плитки, папка остаётся раскрытой
      card._update = () => {
        const count = card.querySelector('.folder-head .small.muted');
        if (count) count.textContent = 'файлов: ' + extraFiles.filter(byUploader).length;
        if (built && blockRef) blockRef.render();
      };
      return card;
    }

    /** Папка задачи: закрыта, содержимое собирается при первом раскрытии */
    function taskFolder(folder) {
      const state = deadlineState(folder.deadline, folder.activity === 'Активная');
      let files = (folder.files || []).filter(byUploader).map(f => Object.assign({ file_kind: f.is_stage_file ? 'stage' : 'task' }, f))
        .sort((a, b) => (Kov.UI.parseDate(a.created_at)?.getTime() ?? 0) - (Kov.UI.parseDate(b.created_at)?.getTime() ?? 0));
      const body = h('div.folder-body', { hidden: true });
      const card = h('div.card.folder-card',
        h('button.folder-head', { type: 'button', onclick: () => toggle() },
          h('span.folder-arrow', icon('chevron_down')), icon('folder'), h('span.t', folder.task_name || 'Задача'),
          h('span.small', { style: { color: state === 'overdue' ? 'var(--critical)' : state === 'urgent' ? 'var(--warning-text)' : 'var(--text-label)' } }, 'до ' + fmtDate(folder.deadline)),
          h('span.small.muted', 'файлов: ' + files.length)),
        body);
      let built = false;
      async function toggle(force) {
        const show = force !== undefined ? force : body.hidden;
        body.hidden = !show;
        card.classList.toggle('open', show);
        if (show) opened.add(folder.task_id); else opened.delete(folder.task_id);
        if (show && !built) { built = true; await buildBody(); }
      }
      const subs = new Map();          // ключ подпапки → её карточка
      async function buildBody(soft) {
        if (!soft) body.append(Kov.UI.loading('Собираем файлы…'));
        const departments = await stageDepartments(folder.task_id);
        // сначала — папки этапов, затем файлы самой задачи
        const byStage = new Map();
        const taskLevel = [];
        files.forEach(f => {
          if (f.is_stage_file && f.stage_number != null) {
            if (!byStage.has(f.stage_number)) byStage.set(f.stage_number, []);
            byStage.get(f.stage_number).push(f);
          } else taskLevel.push(f);
        });
        const wanted = [...byStage.keys()].sort((a, b) => a - b).map(number => {
          const dep = departments[number];
          return ['stage-' + number, 'Файлы этапа ' + number + (dep ? ' — ' + dep : ''), byStage.get(number)];
        });
        if (taskLevel.length) wanted.push(['task', 'Файлы задачи', taskLevel]);

        if (!soft) { Kov.UI.clear(body); subs.clear(); }
        const stub = body.querySelector('.muted.small');
        if (stub) stub.remove();
        // Подпапки переиспользуются: раскрытая остаётся раскрытой, а новый
        // файл появляется в её списке — без пересборки и мигания
        const seen = new Set();
        let prev = null;
        wanted.forEach(([key, title, list]) => {
          seen.add(key);
          let box = subs.get(key);
          if (box && box.isConnected) box._update(title, list);
          else { box = subFolder(title, list); subs.set(key, box); }
          const target = prev ? prev.nextSibling : body.firstChild;
          if (box !== target) body.insertBefore(box, target);
          prev = box;
        });
        subs.forEach((box, key) => { if (!seen.has(key)) { box.remove(); subs.delete(key); } });
        if (!wanted.length) body.append(h('div.muted.small', { style: { padding: '.4rem' } }, 'Файлов нет'));
      }
      if (opened.has(folder.task_id)) toggle(true);
      card._update = next => {
        if (next) folder = next;
        files = (folder.files || []).filter(byUploader).map(f => Object.assign({ file_kind: f.is_stage_file ? 'stage' : 'task' }, f))
          .sort((a, b) => (Kov.UI.parseDate(a.created_at)?.getTime() ?? 0) - (Kov.UI.parseDate(b.created_at)?.getTime() ?? 0));
        const count = card.querySelector('.folder-head .small.muted');
        if (count) count.textContent = 'файлов: ' + files.length;
        // содержимое пересобираем только у раскрытой папки — закрытая соберётся при открытии
        if (built && !body.hidden) buildBody(true);
      };
      return card;
    }

    /** Вложенная папка этапа: тоже закрыта до нажатия */
    function subFolder(title, files) {
      const inner = h('div.sub-body', { hidden: true });
      let built = false;
      let gridRef = null;
      const box = h('div.sub-folder',
        h('button.sub-head', { type: 'button', onclick: () => {
          const show = inner.hidden;
          inner.hidden = !show; box.classList.toggle('open', show);
          if (show && !built) {
            built = true;
            gridRef = Kov.Files.grid({ files, canDelete: () => false, emptyText: 'Файлов нет' });
            inner.append(gridRef);
          }
        } }, h('span.folder-arrow', icon('chevron_down')), icon('folder'),
           h('span.t', title), h('span.small.muted', 'файлов: ' + files.length)),
        inner);
      // Обновление на месте: заголовок, счётчик и плитки. Плитки в сетке
      // собираются по ключам, поэтому уже загруженные миниатюры остаются.
      box._update = (nextTitle, nextFiles) => {
        files = nextFiles || [];
        const t = box.querySelector('.sub-head .t');
        const count = box.querySelector('.sub-head .small.muted');
        if (t) t.textContent = nextTitle;
        if (count) count.textContent = 'файлов: ' + files.length;
        if (built && gridRef && gridRef.fill) gridRef.fill(files);
      };
      return box;
    }

    V.ui.reloadSection = load;
    load();
  }

  /** Переключение колонок на телефоне */
  /** Вкладки в строке заголовка (телефон): число задач и файлов */
  function paintTabCounts() {
    const tabs = V.ui && V.ui.tabs;
    if (!tabs || !tabs.classList.contains('in-head') || !V.project) return;
    const set = (key, label, n) => {
      const b = tabs.querySelector('[data-key="' + key + '"]');
      if (b && b.lastChild) b.lastChild.textContent = label + (n !== null && n !== undefined ? ' · ' + n : '');
    };
    set('tasks', 'Задачи', Array.isArray(V.tasks) ? V.tasks.length : (V.project.tasks_total ?? null));
    set('files', 'Файлы', V.project.files_total ?? null);
  }

  // вернулись в приложение или связь восстановилась — открытый раздел файлов сверяется с сервером
  const refetchSection = () => {
    if (document.visibilityState !== 'visible' || !V.ui || !V.ui.reloadSection || !V.sectionKey) return;
    if (V.ui.right && V.ui.right.isConnected) V.ui.reloadSection({ soft: true });
  };
  document.addEventListener('visibilitychange', refetchSection);
  if (Kov.WS && Kov.WS.onReconnect) Kov.WS.onReconnect(refetchSection);

  function showProjectTab(key) {
    const layout = V.ui && V.ui.layout;
    if (!layout) return;
    layout.classList.toggle('show-tasks', key === 'tasks');
    layout.classList.toggle('show-files', key === 'files');
    // «Склад» прячет колонку задач; «Задачи проекта» должны показывать задачи
    layout.classList.toggle('full-right', key === 'files' && !!V.ui.fullRight);
    if (V.ui.tabs && V.ui.tabs.setActive) V.ui.tabs.setActive(key);
  }

  function taskButtons(p) {
    if (p.is_archived) return h('div.small.muted.ro-note', 'Задачи, файлы и склад завершённого проекта — только для просмотра');
    if (!Kov.Store.canManageWork()) return h('div.small.muted', 'Изменение задач доступно руководителям');
    const canDelete = Kov.Store.canManageWork();
    V.ui.taskEdit = button('Редактировать', { kind: 'ghost', icon: 'edit', disabled: true, onClick: () => {
      const t = V.selectedTask; if (!t) return;
      try { sessionStorage.setItem('kov.taskBack', '#/projects/' + p.id); } catch (e) { /* */ }
      Kov.Router.go('#/tasks/' + t.id + '/edit');
    } });
    V.ui.taskDelete = button('Удалить', { kind: 'danger', icon: 'trash', disabled: true, onClick: async () => {
      const t = V.selectedTask; if (!t) return;
      if (!await Kov.UI.confirm('Удаление задачи', 'Удалить задачу «' + t.name + '» без возможности восстановления?', { okText: 'Удалить', danger: true })) return;
      try { await Kov.Api.del('tasks/' + t.id); Kov.UI.toast('Задача удалена', 'success'); V.selectedTask = null; reloadTasks(); }
      catch (e) { Kov.UI.toast('Не удалось удалить: ' + Kov.UI.errorText(e), 'error'); }
    } });
    if (!canDelete) V.ui.taskDelete.hidden = true;
    const create = button('Новая задача проекта', { icon: 'plus', onClick: () => {
      try { sessionStorage.setItem('kov.presetProject', JSON.stringify({ id: p.id, name: p.name, organization: p.organization || null })); } catch (e) { /* */ }
      Kov.Router.go('#/tasks/new');
    } });
    // как у списка задач: на телефоне одной строкой с короткими подписями, главная кнопка последней
    [[V.ui.taskEdit, 'Редактировать', 'Изменить'], [V.ui.taskDelete, 'Удалить', 'Удалить'], [create, 'Новая задача проекта', 'Создать']]
      .forEach(([b, full, short]) => {
        const label = [...b.childNodes].find(n => n.nodeType === 3 || (n.tagName === 'SPAN' && !n.querySelector('svg')));
        if (label) label.replaceWith(h('span.lbl-full', full), h('span.lbl-short', short));
        b.setAttribute('aria-label', full);
      });
    return Kov.UI.fitRow(h('div.btn-row.end.list-actions', V.ui.taskEdit, V.ui.taskDelete, create), ['icons-only']);
  }

  function updateTaskButtons() {
    const t = V.selectedTask;
    const locked = !!t && !Kov.Rights.canManageTask(t);
    const reason = 'Изменять задачу может руководство или руководитель отдела, к которому относятся все её этапы';
    if (V.ui.taskEdit) {
      V.ui.taskEdit.disabled = !(t && (t.activity || '') === 'Активная');
      Kov.Rights.setLocked(V.ui.taskEdit, locked && !V.ui.taskEdit.disabled, reason);
    }
    if (V.ui.taskDelete) {
      V.ui.taskDelete.disabled = !t;
      Kov.Rights.setLocked(V.ui.taskDelete, locked, reason);
    }
  }

  // ── задачи проекта ──
  let taskTimer = null;
  function scheduleTasks() { clearTimeout(taskTimer); taskTimer = setTimeout(reloadTasks, 250); }
  async function saveTaskPrefs() { try { await Kov.Store.setPref('projects.tasks', V.prefs); } catch (e) { /* */ } }

  async function reloadTasks(opts) {
    // на странице — таблица (компьютер) или карточки (телефон)
    if (!V.ui || !((V.ui.taskBody && V.ui.taskBody.isConnected) || (V.ui.taskCards && V.ui.taskCards.isConnected))) return;
    try {
      V.tasks = await Kov.Api.get('projects/' + V.project.id + '/tasks', { query: { state: V.prefs.state || 'all', search: (V.search || '').trim() || null, sort: V.prefs.sort, department: V.prefs.department, stage_count: V.prefs.stage_count } }) || [];
    } catch (e) { V.tasks = []; }
    // по событию перерисовываем, только если задачи правда изменились — иначе таблица «мигает»
    let sig = '';
    try { sig = JSON.stringify(V.tasks); } catch (e) { sig = String(Date.now()); }
    if (opts && opts.quiet && sig === V.tasksSig) return;
    V.tasksSig = sig;
    const deps = [...new Set(V.tasks.flatMap(t => t.departments || []))].sort((a, b) => a.localeCompare(b, 'ru'));
    V.ui.dep.setOptions([[null, 'Все отделы']].concat(deps.map(d => [d, d])), true);
    if (V.prefs.department) V.ui.dep.value = V.prefs.department;
    drawTasks();
  }

  /** Телефон: задача карточкой — название, срок, точки этапов; касание открывает, удержание выделяет */
  function taskCard(t) {
    const active = (t.activity || '') === 'Активная';
    const state = deadlineState(t.deadline, active);
    const total = t.total_stages || 0, done = Math.min(t.completed_stages || 0, total);
    const dots = h('span.ptask-dots');
    // как у этапов: пройденные — зелёные, текущий — синий, остальные — серые
    for (let i = 0; i < Math.min(total, 8); i++) dots.append(h('i' + (i < done ? '.done' : i === done && active ? '.cur' : '')));
    const stageText = !total ? 'без этапов' : done >= total ? 'все этапы пройдены' : 'этап ' + (done + 1) + ' из ' + total;
    const el = h('div.ptask' + (active ? '' : '.archived') + (t.can_open === false ? '.no-access' : ''), { tabIndex: 0, 'data-id': String(t.id), title: t.can_open === false ? 'Нет доступа к задаче' : '' },
      h('div.ptask-top', h('span.ptask-name', t.name || ''),
        h('span.deadline' + (state ? '.' + state : ''), t.deadline ? fmtDate(t.deadline) : '—')),
      h('div.ptask-meta', dots, h('span', stageText + (active ? '' : ' · архивная'))));
    const open = () => { if (t.can_open === false) return noAccess(); try { sessionStorage.setItem('kov.taskBack', '#/projects/' + V.project.id); } catch (e) { /* */ } Kov.Router.go('#/tasks/' + t.id); };
    const select = () => {
      V.ui.taskCards.querySelectorAll('.ptask.selected').forEach(x => x.classList.remove('selected'));
      el.classList.add('selected');
      V.selectedTask = t; updateTaskButtons();
    };
    el.addEventListener('click', open);
    Kov.UI.longPress(el, select);
    el.addEventListener('keydown', e => { if (e.key === 'Enter') open(); });
    return el;
  }

  function drawTasks() {
    const tbody = V.ui.taskBody;
    Kov.UI.clear(tbody);
    let list = V.tasks;
    if (V.prefs.created_from && V.prefs.created_to) {
      list = list.filter(t => { const d = Kov.UI.parseDate(t.creation_date); return d && d >= new Date(V.prefs.created_from) && d <= new Date(V.prefs.created_to + 'T23:59:59'); });
    }
    list.forEach(t => {
      const active = (t.activity || '') === 'Активная';
      const state = deadlineState(t.deadline, active);
      const tr = h('tr.row' + (state ? '.' + state : ''), { tabIndex: 0 });
      const name = h('td.left', h('div', { style: { fontWeight: active ? 400 : 400, color: active ? '' : 'var(--text-label)' } }, t.name || ''));
      name.title = 'Этапов: ' + (t.completed_stages || 0) + ' из ' + (t.total_stages || 0) + (active ? '' : ' · архивная');
      const dl = h('td', h('span.deadline' + (state ? '.' + state : ''), t.deadline ? fmtDate(t.deadline) : '—'));
      tr.append(name, dl);
      if (t.can_open === false) { tr.classList.add('no-access'); tr.title = 'Нет доступа к задаче'; }
      const open = () => { if (t.can_open === false) return noAccess(); try { sessionStorage.setItem('kov.taskBack', '#/projects/' + V.project.id); } catch (e) { /* */ } Kov.Router.go('#/tasks/' + t.id); };
      const select = () => {
        tbody.querySelectorAll('tr.selected').forEach(x => x.classList.remove('selected'));
        tr.classList.add('selected');
        V.selectedTask = t; updateTaskButtons();
      };
      // сенсорный экран — как в списке задач: касание открывает, удержание выделяет; мышью — выбор и двойной щелчок
      tr.addEventListener('click', () => { if (isTouch()) open(); else select(); });
      Kov.UI.longPress(tr, select);
      tr.addEventListener('dblclick', open);
      tr.addEventListener('keydown', e => { if (e.key === 'Enter') open(); });
      tbody.append(tr);
    });
    if (V.ui.taskCards) {
      // сверка по номеру задачи: меняются только изменившиеся карточки — без моргания
      Kov.UI.syncList(V.ui.taskCards, list, {
        key: t => t.id,
        sign: t => [t.name, t.deadline, t.activity, t.completed_stages, t.total_stages].join('|'),
        make: (t, i) => { const el = taskCard(t); el.style.setProperty('--eq-i', String(Math.min(i, 12))); return el; },
      });
      let empty = V.ui.taskCards.querySelector(':scope > .ptask-empty');
      if (!list.length && !empty) V.ui.taskCards.append(h('div.ptask-empty.muted', 'Задач нет'));
      else if (list.length && empty) empty.remove();
    }
    paintTabCounts();
    V.selectedTask = null; updateTaskButtons();
    if (!list.length) tbody.append(h('tr', h('td.left', { colSpan: 2 }, h('span.muted', 'Задач нет'))));
    const active = list.filter(t => (t.activity || '') === 'Активная').length;
    V.ui.taskSummary.textContent = 'Задач: ' + list.length + ' · активных: ' + active;
  }

  // ══════════════════════════ WebSocket ══════════════════════════

  let wsTimer = null;
  function onEvent(evt) {
    const kind = evt.type || '';
    // окно проекта открыто?
    if (V.ui && V.ui.section && V.ui.section.isConnected && V.project) {
      if (kind === 'project.deleted' && evt.project_id === V.project.id) { Kov.UI.toast('Проект удалён другим пользователем', 'warning'); Kov.Router.go('#/projects'); return; }
      if (kind === 'project.updated' && evt.project_id === V.project.id) {
        Kov.Api.get('projects/' + V.project.id).then(p => { V.project = p; const key = V.sectionKey; renderProject(); reloadTasks(); if (key) openSection(key); }).catch(() => {});
        return;
      }
      if ((kind === 'project.file_added' || kind === 'project.file_deleted') && evt.project_id === V.project.id) {
        // «Дополнительные файлы» лежат в разделе extra, а смотрят их из
        // медиаконтента — поэтому там обновляемся на любой файл проекта
        if (V.sectionKey === 'photos' && V.ui.reloadSection) V.ui.reloadSection({ soft: true });
        // любой файл этого проекта: открытый раздел сверяется с сервером (тихо)
        else if (V.sectionKey && V.ui.reloadSection) V.ui.reloadSection({ soft: true });
        else if (!V.sectionKey) renderSectionCards();
        return;
      }
      if (kind.startsWith('task.') || kind.startsWith('stage.')) {
        const aboutFiles = kind.includes('file');
        clearTimeout(wsTimer);
        wsTimer = setTimeout(() => {
          // о файлах задач — только медиаконтенту: таблица задач файлов не показывает
          if (!aboutFiles) reloadTasks({ quiet: true });
          if (V.sectionKey === 'photos' && V.ui.reloadSection) V.ui.reloadSection({ soft: true });
        }, 300);
      }
      return;
    }
    // список
    if (L.ui && L.ui.tableWrap && L.ui.tableWrap.isConnected && (kind.startsWith('project.') || kind.startsWith('task.'))
        && !kind.includes('file')) {
      clearTimeout(wsTimer); wsTimer = setTimeout(() => reload({ quiet: true }), 300);
    }
  }

  return { view, onEvent };
})();
