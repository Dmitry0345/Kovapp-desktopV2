/* Форма задачи (создание и правка): название, заказчик из справочника,
 * проект, описание, дата окончания, этапы с отделами, ответственные и исполнители,
 * шаблоны задач — как TaskEditWidget десктопа. */
window.Kov = window.Kov || {};
Kov.Views = Kov.Views || {};

Kov.Views.taskEdit = (function () {
  const { h, icon, button, sectionHeader, field } = Kov.UI;
  const BASE_DEPARTMENTS = ['Монтажный отдел', 'Бухгалтерия', 'Отдел ТО', 'Договорной отдел', 'Руководство'];
  // Отделы, которых не бывает среди исполнителей этапов: «Склад» ведёт
  // своё хозяйство, «Администрация» — служебный
  const NON_TASK_DEPARTMENTS = ['Администрация', 'Склад'];

  // Ответственным может быть руководитель отдела, директор и администратор —
  // это определяется маркером прав сотрудника, а не названием его должности
  function canBeResponsible(e) { return Kov.Store.canManageWork(e); }
  // роли не пересекаются: кто может быть ответственным — не исполнитель, и наоборот
  function canBeAssigned(e) { return !canBeResponsible(e); }
  function allowedFor(key, e) { return key === 'responsible' ? canBeResponsible(e) : canBeAssigned(e); }
  const TEMPLATES_LOCKED = 'Шаблоны задач ведут директор и администратор';

  function view(section, taskId) {
    const editing = taskId !== null && taskId !== undefined;
    section.append(Kov.UI.loading(editing ? 'Загружаем задачу…' : 'Готовим форму…'));
    build(section, editing ? Number(taskId) : null).catch(e => {
      Kov.UI.clear(section);
      section.append(h('div.empty', 'Не удалось открыть форму: ' + Kov.UI.errorText(e)));
    });
  }

  async function build(section, taskId) {
    const [task, organizations, employees] = await Promise.all([
      taskId ? Kov.Api.get('tasks/' + taskId) : Promise.resolve(null),
      Kov.Api.get('organizations').catch(() => []),
      Kov.Store.pickable(),
    ]);
    await Kov.Store.lists();
    // список отделов — из справочника сервера (реальные отделы сотрудников)
    const fromServer = (Kov.Store.state.departments || []).filter(Boolean);
    // «Склад» ведёт своё хозяйство и в этапах задач не участвует
    const allDepartments = (fromServer.length ? fromServer : BASE_DEPARTMENTS)
      .filter(d => !NON_TASK_DEPARTMENTS.includes(d));
    const departments = Kov.Rights.departmentsForNewTask(allDepartments);
    const maxStages = Math.min(10, Kov.Rights.maxStagesOnCreate());
    Kov.UI.clear(section);
    if (task && !Kov.Rights.canManageTask(task)) {
      section.append(h('div.empty', 'Править эту задачу может руководство или руководитель отдела, к которому относятся все её этапы'),
        h('div.btn-row', button('К задаче', { kind: 'ghost', icon: 'arrow_back', onClick: () => Kov.Router.go('#/tasks/' + task.id) })));
      return;
    }
    if (!departments.length) {
      section.append(h('div.empty', 'Ваш отдел не ведёт этапы задач — создать задачу не получится'),
        h('div.btn-row', button('К списку задач', { kind: 'ghost', icon: 'arrow_back', onClick: () => Kov.Router.go('#/tasks') })));
      return;
    }

    // ── поля ──
    const name = h('input.input', { placeholder: 'Название задачи', value: task ? task.name || '' : '' });
    // Проект здесь не выбирается: задача привязывается к проекту только
    // при создании из окна проекта («Новая задача проекта»)
    let presetProject = null;
    try {
      const raw = sessionStorage.getItem('kov.presetProject');
      if (!task && raw) { presetProject = JSON.parse(raw); sessionStorage.removeItem('kov.presetProject'); }
    } catch (e) { /* */ }
    // организация задачи проекта всегда совпадает с организацией проекта
    const lockedOrg = presetProject ? (presetProject.organization || null)
      : (task && task.project ? (task.project.organization || null) : null);

    const orgOptions = [[null, '— Не указана —']].concat((organizations || []).map(o => [o.name, o.name]).sort((a, b) => a[1].localeCompare(b[1], 'ru')));
    // организация задачи могла быть удалена из справочника — оставляем как есть, но помечаем
    if (task && task.customer_organization && !orgOptions.some(([v]) => v === task.customer_organization)) {
      orgOptions.splice(1, 0, [task.customer_organization, task.customer_organization + ' (нет в справочнике)']);
    }
    if (lockedOrg && !orgOptions.some(([v]) => v === lockedOrg)) orgOptions.splice(1, 0, [lockedOrg, lockedOrg]);
    const org = Kov.UI.pickerSelect(orgOptions, lockedOrg || (task ? task.customer_organization : null), null,
      { placeholder: '— Не указана —', searchPlaceholder: 'Поиск по названию организации…', empty: 'Организация не найдена' });
    if (lockedOrg) { org.setDisabled(true); org.title = 'Организация берётся из проекта. Снимите привязку к проекту, чтобы её изменить'; }

    const projectNote = presetProject
      ? h('div.chip.info', 'Задача будет привязана к проекту «' + (presetProject.name || presetProject.id) + '»'
          + (lockedOrg ? ' · организация: ' + lockedOrg : ''))
      : (task && task.project ? h('div.chip.info', 'Проект: ' + (task.project.name || '')
          + (lockedOrg ? ' · организация: ' + lockedOrg : '')) : null);
    const description = h('textarea.textarea', { placeholder: 'Описание задачи', value: task ? task.description || '' : '', style: { minHeight: '7rem' } });
    // при создании срок — не раньше сегодняшнего дня (у существующей задачи прошедший срок возможен)
    const deadline = h('input.input', Object.assign({ type: 'date', value: task && task.deadline ? String(task.deadline).slice(0, 10) : '' }, task ? {} : { min: Kov.UI.isoDate(new Date()) }));

    // ── этапы ──
    const existingStages = task ? (task.stage_details || []).slice().sort((a, b) => a.stage_number - b.stage_number) : [];
    const count = h('input.input', { type: 'number', min: 1, max: task ? 10 : maxStages, value: existingStages.length || 1, style: { width: '6rem' }, disabled: !!task || maxStages === 1 });
    if (!task && maxStages === 1) count.title = 'Руководитель отдела заводит задачу с одним этапом своего отдела — другие отделы подключаются продлением задачи';
    const stagesBox = h('div', { style: { display: 'flex', flexDirection: 'column', gap: '.45rem' } });
    function stageRow(n, dep) {
      const sel = Kov.UI.select(departments.map(d => [d, d]), dep && departments.includes(dep) ? dep : departments[0]);
      return h('div', { style: { display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '.5rem', alignItems: 'center' } }, h('span.chip', 'Этап ' + n), sel);
    }
    function rebuildStages(deps) {
      const n = Math.min(task ? 10 : maxStages, Math.max(1, Number(count.value) || 1));
      count.value = n;
      const current = stageDeps();
      Kov.UI.clear(stagesBox);
      for (let i = 1; i <= n; i++) stagesBox.append(stageRow(i, (deps && deps[i - 1]) || current[i - 1] || (existingStages[i - 1] || {}).department_name));
    }
    // на сенсорном экране выбор — кнопка со шторкой (.sheet-select), не <select>: читаем оба вида
    function stageDeps() { return [...stagesBox.querySelectorAll('select, .sheet-select')].map(s => s.value); }
    count.addEventListener('change', () => rebuildStages());
    rebuildStages(existingStages.map(s => s.department_name));

    // ── люди ──
    const selected = {
      responsible: new Set((task ? task.responsible_persons || [] : []).map(p => p.id)),
      assigned: new Set((task ? task.assigned_employees || [] : []).map(p => p.id)),
    };
    const peopleRows = {};
    function peopleField(key, title) {
      const chips = h('div.chips');
      peopleRows[key] = chips;
      const pick = button('Выбрать', { kind: 'ghost', icon: 'users', onClick: () => pickPeople(key, title) });
      renderChips(key);
      return field(title, h('div', { style: { display: 'flex', flexDirection: 'column', gap: '.4rem' } }, chips, h('div', pick)));
    }
    function renderChips(key) {
      const chips = peopleRows[key];
      Kov.UI.clear(chips);
      const list = employees.filter(e => selected[key].has(e.id));
      if (!list.length) { chips.append(h('span.muted', 'Никто не выбран')); return; }
      list.forEach(e => chips.append(h('span.chip.person',
        Kov.UI.avatar(e, null, Kov.Store.photoUrl(e.id)),
        h('span.person-name', Kov.UI.fullName(e)),
        h('button.chip-x', { type: 'button', text: '×', title: 'Убрать', onclick: () => { selected[key].delete(e.id); renderChips(key); } }))));
    }
    function pickPeople(key, title) {
      const temp = new Set(selected[key]);
      const onlyHeads = key === 'responsible';
      // только те, чьи отделы участвуют в этапах задачи
      const stageSet = new Set(stageDeps().filter(Boolean));
      const byStages = stageSet.size ? employees.filter(e => stageSet.has(e.department)) : employees;
      // роли не пересекаются: ответственные — старшие, главные, директор и
      // администратор; исполнители — все остальные. Плюс те, кто уже отмечен
      // раньше, иначе их нельзя было бы снять.
      const pool = byStages.filter(e => allowedFor(key, e) || selected[key].has(e.id));
      const listBox = h('div.checklist', { style: { maxHeight: '23rem' } });
      const search = Kov.UI.searchInput('Поиск по ФИО...', renderList);
      const byDepartment = [...new Set(pool.map(e => e.department).filter(Boolean))]
        .filter(d => d !== 'Администрация').sort((a, b) => a.localeCompare(b, 'ru'));
      const depPick = Kov.UI.select([[null, 'Выбрать весь отдел…']].concat(byDepartment.map(d => [d, d])), null, dep => {
        if (!dep) return;
        // отдел добавляем только теми, кого вообще можно выбрать в этой роли
        const add = pool.filter(e => e.department === dep && allowedFor(key, e));
        add.forEach(e => temp.add(e.id));
        depPick.value = ''; renderList(search.input.value);
        Kov.UI.toast(add.length ? 'Добавлено из отдела: ' + add.length
          : 'В отделе «' + dep + '» нет подходящих для этой роли сотрудников', add.length ? 'success' : 'warning');
      }, { title: onlyHeads ? 'Добавить старших и главных сотрудников отдела' : 'Добавить рядовых сотрудников отдела' });
      function renderList(needle) {
        needle = (needle || '').toLowerCase();
        Kov.UI.clear(listBox);
        const rows = pool.filter(e => !needle || Kov.UI.fullName(e, true).toLowerCase().includes(needle));
        rows.forEach(e => {
          const allowed = allowedFor(key, e);
          const cb = h('input', { type: 'checkbox', checked: temp.has(e.id), disabled: !allowed && !temp.has(e.id),
            onchange: ev => ev.target.checked ? temp.add(e.id) : temp.delete(e.id) });
          listBox.append(h('label' + (allowed ? '' : '.off'), cb, Kov.UI.avatar(e, null, Kov.Store.photoUrl(e.id)),
            h('span', { style: { flex: 1 } }, Kov.UI.fullName(e, true),
              h('span.muted.small', ' — ' + [e.department, e.position].filter(Boolean).join(', ')),
              allowed ? null : h('span.chip.warning', { style: { marginLeft: '.4rem' } }, 'не по должности'))));
        });
        if (!rows.length) listBox.append(h('div.muted', { style: { padding: '.4rem' } },
          onlyHeads ? 'В выбранных отделах нет старших и главных сотрудников'
            : 'В выбранных отделах нет рядовых сотрудников'));
      }
      renderList('');
      const hint = !stageSet.size ? 'Сначала выберите отделы этапов'
        : (onlyHeads
          ? 'Ответственными могут быть только старшие и главные сотрудники, директор и администратор. Отделы этапов: ' + [...stageSet].join(', ')
          : 'Исполнителями могут быть только рядовые сотрудники — старшие, главные, директор и администратор идут в ответственные. Отделы этапов: ' + [...stageSet].join(', '));
      Kov.UI.modal({
        title, glyph: 'users', width: '41.4rem',
        body: el => el.append(h('div.small.muted', hint), h('div.toolbar', search.el, depPick), listBox),
        buttons: [{ text: 'Отмена', kind: 'ghost' }, { text: 'Готово', icon: 'check', onClick: close => { selected[key] = temp; renderChips(key); close(); } }],
      });
    }

    // ── шаблоны ──
    async function templatesDialog() {
      let templates = [];
      try { templates = await Kov.Api.get('templates') || []; } catch (e) { /* */ }
      const listBox = h('div.checklist', { style: { maxHeight: '16rem' } });
      let chosen = null;

      function renderList() {
        Kov.UI.clear(listBox);
        if (!templates.length) { listBox.append(h('div.muted', { style: { padding: '.4rem' } }, 'Шаблонов пока нет — создайте первый')); return; }
        templates.forEach(t => {
          const row = h('div', { style: { display: 'flex', alignItems: 'center', gap: '.4rem' } });
          const label = h('label', { style: { flex: 1 } }, h('input', { type: 'radio', name: 'tpl', onchange: () => { chosen = t; } }),
            h('span', t.template_name, h('span.muted.small', ' — этапов: ' + t.num_stages + ' (' + t.departments + ')')));
          row.append(label,
            Kov.Rights.gate(button('', { kind: 'ghost', icon: 'edit', title: 'Изменить шаблон', onClick: () => templateForm(t) }),
              Kov.Rights.canManageTemplates(), TEMPLATES_LOCKED),
            Kov.Rights.gate(button('', { kind: 'danger', icon: 'trash', title: 'Удалить шаблон', onClick: async () => {
              if (!await Kov.UI.confirm('Удаление шаблона', 'Удалить шаблон «' + t.template_name + '»?', { okText: 'Удалить', danger: true })) return;
              try { await Kov.Api.del('templates/' + t.id); await reload(); }
              catch (e) { Kov.UI.toast('Не удалось удалить: ' + Kov.UI.errorText(e), 'error'); }
            } }), Kov.Rights.canManageTemplates(), TEMPLATES_LOCKED));
          listBox.append(row);
        });
      }
      async function reload() {
        try { templates = await Kov.Api.get('templates') || []; } catch (e) { templates = []; }
        chosen = null; renderList();
      }

      /** Окно создания и правки шаблона: название и отдел каждого этапа */
      function templateForm(tpl) {
        const nameInput = h('input.input', { placeholder: 'Название шаблона', value: tpl ? tpl.template_name : '' });
        const startDeps = tpl ? String(tpl.departments || '').split(',').map(x => x.trim()).filter(Boolean) : [departments[0]];
        const countInput = h('input.input', { type: 'number', min: 1, max: 10, value: Math.max(1, startDeps.length), style: { width: '6rem' } });
        const box = h('div', { style: { display: 'flex', flexDirection: 'column', gap: '.4rem' } });
        function rebuild(deps) {
          const n = Math.min(10, Math.max(1, Number(countInput.value) || 1));
          countInput.value = n;
          const current = [...box.querySelectorAll('select, .sheet-select')].map(x => x.value);
          Kov.UI.clear(box);
          for (let i = 1; i <= n; i++) {
            const value = (deps && deps[i - 1]) || current[i - 1] || departments[0];
            const sel = Kov.UI.select(departments.map(d => [d, d]), departments.includes(value) ? value : departments[0]);
            box.append(h('div', { style: { display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '.5rem', alignItems: 'center' } }, h('span.chip', 'Этап ' + i), sel));
          }
        }
        countInput.addEventListener('change', () => rebuild());
        rebuild(startDeps);
        Kov.UI.modal({
          title: tpl ? 'Изменение шаблона' : 'Новый шаблон', glyph: 'file',
          body: el => el.append(field('Название', nameInput, true), field('Количество этапов', countInput), box),
          buttons: [
            { text: 'Отмена', kind: 'ghost' },
            { text: 'Сохранить', icon: 'save', onClick: async close => {
              const title = nameInput.value.trim();
              if (!title) return Kov.UI.toast('Укажите название шаблона', 'warning');
              const deps = [...box.querySelectorAll('select, .sheet-select')].map(x => x.value);
              const payload = { template_name: title, num_stages: deps.length, departments: deps.join(', ') };
              try {
                if (tpl) await Kov.Api.put('templates/' + tpl.id, { json: payload });
                else await Kov.Api.post('templates', { json: payload });
                close(); Kov.UI.toast('Шаблон сохранён', 'success'); await reload();
              } catch (e) { Kov.UI.toast('Не удалось сохранить: ' + Kov.UI.errorText(e), 'error'); }
            } },
          ],
        });
      }

      renderList();
      const dialog = Kov.UI.modal({
        title: 'Шаблоны задач', glyph: 'file', wide: true,
        body: el => el.append(listBox),
        buttons: [
          { text: 'Создать шаблон', kind: 'ghost', icon: 'plus', onClick: () => {
            if (!Kov.Rights.canManageTemplates()) return Kov.UI.toast(TEMPLATES_LOCKED, 'info');
            templateForm(null);
          } },
          { text: 'Из текущей задачи', kind: 'ghost', icon: 'save', onClick: async () => {
            if (!Kov.Rights.canManageTemplates()) return Kov.UI.toast(TEMPLATES_LOCKED, 'info');
            const deps = stageDeps();
            const tplName = name.value.trim() || 'Шаблон ' + new Date().toLocaleDateString('ru-RU');
            try { await Kov.Api.post('templates', { json: { template_name: tplName, num_stages: deps.length, departments: deps.join(', ') } }); Kov.UI.toast('Шаблон сохранён', 'success'); await reload(); }
            catch (e) { Kov.UI.toast('Не удалось сохранить шаблон: ' + Kov.UI.errorText(e), 'error'); }
          } },
          { text: 'Применить', icon: 'check', onClick: close => {
            if (!chosen) return Kov.UI.toast('Выберите шаблон', 'warning');
            name.value = chosen.template_name;
            const deps = String(chosen.departments || '').split(',').map(x => x.trim()).filter(Boolean);
            const wanted = Math.min(10, Math.max(1, chosen.num_stages || deps.length || 1));
            count.value = Math.min(wanted, maxStages);
            rebuildStages(deps);
            if (wanted > maxStages || deps.some(d => !departments.includes(d))) {
              Kov.UI.toast('Шаблон применён частично: руководитель отдела заводит задачу с одним этапом своего отдела', 'warning');
            }
            close();
          } },
        ],
      });
      if (!Kov.Rights.canManageTemplates()) {
        [...dialog.foot.querySelectorAll('button.btn')].slice(0, 2).forEach(b => Kov.Rights.setLocked(b, true, TEMPLATES_LOCKED));
      }
    }

    // ── сохранение ──

    /** Участники каждого этапа — все сотрудники его отдела */
    async function fillStageParticipants(saved, only) {
      for (const stage of ((saved && saved.stage_details) || [])) {
        if (only && !only.has(stage.id)) continue;
        const ids = employees.filter(e => e.department === stage.department_name).map(e => e.id);
        if (!ids.length) continue;
        try { await Kov.Api.put('stages/' + stage.id + '/participants', { json: ids }); }
        catch (e) { console.debug('участники этапа не сохранены', stage.id, e); }
      }
    }

    async function save() {
      const title = name.value.trim();
      if (!title) return Kov.UI.toast('Укажите название задачи', 'warning');
      if (!deadline.value) { Kov.UI.softFocus(deadline); return Kov.UI.toast('Укажите дату окончания — без неё задачу сохранить нельзя', 'warning'); }
      if (!task && deadline.value < Kov.UI.isoDate(new Date())) { Kov.UI.softFocus(deadline); return Kov.UI.toast('Дата окончания не может быть раньше сегодняшнего дня', 'warning'); }
      const deps = stageDeps();
      const payload = {
        name: title,
        description: description.value.trim() || null,
        deadline: deadline.value || null,
        customer_organization: lockedOrg || org.getValue(),
        responsible_persons_ids: [...selected.responsible],
        assigned_employees_ids: [...selected.assigned],
      };
      /**
       * Уведомление о назначении. Получатели: новые участники, прежние
       * (чтобы снятый с задачи тоже узнал) и сам автор правки — иначе
       * в его разделе «Уведомления» не появляется ничего.
       */
      async function notify(taskId, taskName, isNew) {
        const was = task ? [...(task.responsible_persons || []), ...(task.assigned_employees || [])].map(p => p.id) : [];
        const ids = [...new Set([...selected.responsible, ...selected.assigned, ...was, Kov.Api.state.userId])];
        await Kov.Views.notifications.send(ids,
          isNew ? 'Вам назначена задача «' + taskName + '»' : 'Задача «' + taskName + '» изменена',
          'task_view', taskId,
          { selfMessage: (isNew ? 'Вы создали задачу «' : 'Вы изменили задачу «') + taskName + '»' });
      }

      try {
        if (task) {
          payload.stages = existingStages.map((s, i) => ({ stage_number: s.stage_number, department_name: deps[i] || s.department_name }));
          const moved = new Set(existingStages.filter((s, i) => deps[i] && deps[i] !== s.department_name).map(s => s.id));
          await Kov.Api.put('tasks/' + task.id, { json: payload });
          if (moved.size) await fillStageParticipants(await Kov.Api.get('tasks/' + task.id).catch(() => null), moved);
          await notify(task.id, title, false);
          Kov.UI.toast('Задача сохранена', 'success');
          Kov.Router.go('#/tasks/' + task.id);
        } else {
          if (presetProject) payload.project_id = presetProject.id;
          payload.stages = deps.map(d => ({ department_name: d, text_description: '' }));
          const created = await Kov.Api.post('tasks', { json: payload });
          await fillStageParticipants(created);
          await notify(created.id, title, true);
          Kov.UI.toast('Задача создана', 'success');
          Kov.Router.go('#/tasks/' + created.id);
        }
      } catch (e) { Kov.UI.toast('Не удалось сохранить: ' + Kov.UI.errorText(e), 'error'); }
    }

    const backTarget = task ? '#/tasks/' + task.id : (presetProject ? '#/projects/' + presetProject.id : '#/tasks');
    Kov.UI.append(section, [
      h('div.card.form-card', { style: { display: 'flex', flexDirection: 'column', gap: '.8rem' } },
        h('div.form-grid', field('Название', name, true), field('Заказчик', org)),
        projectNote,
        field('Дата окончания', deadline, true),
        field('Описание', description),
        field('Количество этапов', h('div.btn-row', count, task ? h('span.small.muted', 'Число этапов меняется кнопкой «Продолжить задачу» в окне задачи') : button('Шаблоны задач', { kind: 'ghost', icon: 'file', onClick: templatesDialog }))),
        stagesBox,
        peopleField('responsible', 'Ответственные'),
        peopleField('assigned', 'Исполнители'),
        h('div.btn-row.end', button('Отмена', { kind: 'ghost', onClick: () => Kov.Router.go(backTarget) }), button('Сохранить', { icon: 'save', onClick: save })),
      ),
      task ? null : h('div.small.muted', 'Файлы можно будет прикрепить сразу после создания — в окне задачи.'),
    ]);
  }

  return { view };
})();
