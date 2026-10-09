/* Окно задачи: слева карточки «Прогресс», «О задаче», «Участники» и описание,
 * справа две постоянные зоны — ввод описания этапа и файлы (этапа/задачи),
 * сверху по центру список этапов, снизу строка действий: состояние этапа,
 * «Завершить/Возобновить этап», «Продолжить задачу», «Завершить задачу (Архив)». */
window.Kov = window.Kov || {};
Kov.Views = Kov.Views || {};

Kov.Views.taskView = (function () {
  const { h, icon, button, sectionHeader, field, fmtDate, fmtDateTime, deadlineState } = Kov.UI;
  const state = { taskId: null, task: null, stage: null, stageNumber: null, root: null, savedText: '',
                  scope: 'desc', fileCategory: 'documents', fileBlock: null, editor: null };

  const canManage = () => Kov.Store.isManagement();          // директор или администратор
  const isSeniorOf = dep => Kov.Store.isDepartmentHead() && (Kov.Store.state.me || {}).department === dep;
  const ownUpload = file => Number(file.uploaded_by_id || (file.uploaded_by && file.uploaded_by.id) || 0) === Number(Kov.Api.state.userId);

  function linkify(text) {
    const esc = String(text || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    return esc.replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
  }

  /** Сотрудник может прийти объектом или одним числом — ФИО показываем в обоих случаях */
  function whoLabel(person, id) {
    if (person && (person.last_name || person.first_name)) return Kov.UI.fullName(person);
    const key = Number((person && person.id) || id || 0);
    if (!key) return null;
    const found = (Kov.Store.state.employees || []).find(e => e.id === key);
    return found ? Kov.UI.fullName(found) : 'сотрудник №' + key;
  }
  function stageWho(s) { return whoLabel(s.completed_by, s.completed_by_employee_id || s.completed_by_id || s.who_completed); }
  function stageWhen(s) { return s.completion_timestamp || s.completed_at || s.when_completed || null; }
  function archivedWho(t) { return whoLabel(t.who_archived_employee, t.who_archived || t.archived_by_employee_id); }
  function archivedWhen(t) { return t.archived_at || t.when_archived || null; }

  // ── данные ───────────────────────────────────────────────────────────────

  async function loadTask() {
    state.task = await Kov.Api.get('tasks/' + state.taskId);
    Kov.Rights.setReadOnly(!!(state.task && state.task.project_archived), 'Проект этой задачи завершён — задача только для просмотра');
    return state.task;
  }
  async function loadStage(stageId) { state.stage = await Kov.Api.get('tasks/' + state.taskId + '/stages/' + stageId); return state.stage; }

  function stages() { return (state.task.stage_details || []).slice().sort((a, b) => a.stage_number - b.stage_number); }
  function currentStageNumber() {
    const n = Number(state.task.state);
    return Number.isFinite(n) && n > 0 ? n : (stages().find(s => !s.is_completed) || {}).stage_number || (stages()[stages().length - 1] || {}).stage_number;
  }
  // начальный этап — только свой: текущий, если он своего отдела; иначе ближайший свой незавершённый; иначе любой свой
  function openableStage() {
    const cur = currentStageNumber(), mine = stages().filter(s => Kov.Rights.canOpenStage(s));
    if (!mine.length) return null;
    const pick = mine.find(s => s.stage_number === cur) || mine.find(s => !s.is_completed && s.stage_number >= (cur || 0))
      || mine.find(s => !s.is_completed) || mine[0];
    return pick.stage_number;
  }
  function noOwnStage() {
    if (!state.zoneHolder) return;
    Kov.UI.clear(state.zoneHolder);
    state.zoneHolder.append(h('div.empty', 'Этапы этой задачи ведут другие отделы — их содержимое закрыто.'));
  }
  function isArchived() { return state.task.activity === 'Архивная'; }

  // ── раздел ───────────────────────────────────────────────────────────────

  function view(section, taskId) {
    state.taskId = Number(taskId);
    state.root = section;
    state.stageNumber = null;
    section.append(Kov.UI.bigLoading('Открываем задачу…'));
    Kov.WS.subscribeTask(state.taskId);
    // справочник сотрудников и фото — чтобы сразу показывать ФИО и аватары
    Promise.all([loadTask(), loadCompletion(), Kov.Store.employees().catch(() => []), Kov.Store.photoIds().catch(() => null)])
      .then(() => { render(); const n = openableStage(); if (n) selectStage(n); else noOwnStage(); })
      .catch(e => { Kov.UI.clear(section); section.append(h('div.empty', 'Не удалось открыть задачу: ' + Kov.UI.errorText(e)), h('div.btn-row', button('К списку задач', { kind: 'ghost', icon: 'arrow_back', onClick: goBack }))); });
  }

  function backHash() {
    let saved = null;
    try { saved = sessionStorage.getItem('kov.taskBack'); } catch (e) { /* */ }
    return saved || '#/tasks';
  }
  function goBack() {
    const target = backHash();
    try { sessionStorage.removeItem('kov.taskBack'); } catch (e) { /* */ }
    Kov.Router.go(target);
  }

  function render() {
    const section = state.root; if (!section || !section.isConnected) return;
    const t = state.task;
    Kov.UI.clear(section);
    section.classList.add('task-section');
    Kov.Shell.setTitle(t.name || 'Задача');
    const all = stages();

    // ── список этапов: отдельная строка по центру экрана ──────────────────
    const nav = h('div.stage-nav');
    all.forEach(s => {
      const b = h('button.btn' + (s.is_completed ? '.done' : ''), { type: 'button', dataset: { stage: s.stage_number }, onclick: () => selectStage(s.stage_number) },
        h('span', 'Этап ' + s.stage_number), h('span.dep', s.department_name || '—'));
      // Этап чужого отдела заперт, но из полосы не убран: иначе в задаче с
      // чередующимися отделами (первый мой, второй чужой, третий мой) номера
      // этапов шли бы вразнобой и переключаться было бы не по чему
      if (!Kov.Rights.canOpenStage(s)) {
        Kov.Rights.lock(b, 'Этап ведёт отдел «' + (s.department_name || '—') + '» — он закрыт');
      }
      b.title = s.is_completed
        ? 'Завершён' + (stageWho(s) ? ': ' + stageWho(s) : '') + (stageWhen(s) ? ' · ' + fmtDateTime(stageWhen(s)) : '')
        : 'В работе · отдел: ' + (s.department_name || 'не указан');
      nav.append(b);
    });
    // Телефон: лента из пяти-восьми этапов в экран не влезает. Вместо неё —
    // компактный переключатель: стрелки, номер текущего этапа с отделом и
    // ряд точек по числу этапов. Лента остаётся, но скрывается стилями.
    const mobileNav = buildMobileStages(all);
    state.stageShape = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    state.stageShape.setAttribute('class', 'stage-shape');
    state.stageShape.setAttribute('preserveAspectRatio', 'none');
    state.stageShape.setAttribute('aria-hidden', 'true');
    state.stageNav = nav;
    const shapeBox = h('div.stage-shape-box', state.stageShape, nav);
    const stagesRow = h('div.stages-row', h('div.stage-strip', shapeBox), mobileNav);
    if (stageShapeObserver) { stageShapeObserver.disconnect(); stageShapeObserver.observe(nav); }

    // ── левая колонка ─────────────────────────────────────────────────────
    const done = all.filter(s => s.is_completed).length;
    const progress = h('div.info-card',
      h('div.caption', h('span.badge-icon', icon('flag')), 'Прогресс'),
      h('div.btn-row', Kov.UI.stageDots(all, all.length, Number(t.state)), h('span.muted', 'Завершено ' + done + ' из ' + all.length)),
      h('div.progress-line', h('div.fill', { style: { width: (all.length ? (done / all.length * 100) : 0) + '%' } })),
    );
    const dState = deadlineState(t.deadline, !isArchived());
    const project = t.project && (t.project.name || null);
    const about = Kov.UI.infoCard('id_card', 'О задаче', [
      ['Название', t.name],
      ['Заказчик', t.customer_organization],
      ['Проект', project],
      ['Дата окончания', t.deadline ? fmtDate(t.deadline) : 'Не указана'],
      isArchived() ? ['Архивирована', archivedWhen(t) ? fmtDateTime(archivedWhen(t)) : '—'] : null,
      isArchived() ? ['Кто архивировал', archivedWho(t) || 'Неизвестный'] : null,
    ].filter(Boolean));
    // строки ищем по подписи: пустые строки карточка пропускает, индексы плывут
    const rowValue = label => { const row = [...about.querySelectorAll('.info-row')].find(r => r.querySelector('.k').textContent === label); return row && row.querySelector('.v'); };
    if (dState) {
      const v = rowValue('Дата окончания');
      if (v) { v.style.color = dState === 'overdue' ? 'var(--critical)' : 'var(--warning-text)'; v.title = dState === 'overdue' ? 'Дата окончания просрочена' : 'До даты окончания меньше суток'; }
    }
    if (project && t.project_id) {
      const v = rowValue('Проект');
      if (v) { v.style.cursor = 'pointer'; v.style.textDecoration = 'underline dotted'; v.title = 'Открыть проект'; v.onclick = () => Kov.Router.go('#/projects/' + t.project_id); }
    }
    const responsible = t.responsible_persons || [], assigned = t.assigned_employees || [];
    const showAll = () => peopleDialog('Участники задачи', [['Ответственные', responsible], ['Исполнители', assigned]]);
    const people = h('div.info-card.people-card',
      h('div.caption', h('span.badge-icon', icon('users')), 'Участники', h('span.spacer'),
        h('span.small.muted', 'всего: ' + (responsible.length + assigned.length))),
      field('Ответственные (' + responsible.length + ')', clampedPeople(responsible, 2, showAll)),
      field('Исполнители (' + assigned.length + ')', clampedPeople(assigned, 2, showAll)),
      h('div.btn-row', button('Показать всех участников', { kind: 'ghost', icon: 'users', onClick: showAll })));
    const description = h('div.info-card.grow', h('div.caption', h('span.badge-icon', icon('note')), 'Описание задачи'),
      h('div.desc-view', { html: (t.description || '').trim() ? linkify(t.description) : '<span class="muted">Описание отсутствует.</span>' }));
    const left = h('div.task-col.left', progress, about, people, description);

    // ── правая колонка: одна зона, три вкладки ────────────────────────────
    state.zoneTitle = h('span', 'Этап и файлы');
    state.zoneStatus = h('span.chip', { hidden: true });
    const ALLOWED = ['desc', 'task', 'stage'];
    if (!ALLOWED.includes(state.scope)) state.scope = 'desc';
    state.zoneSwitch = Kov.UI.segmented(
      [['desc', 'Описание этапа', 'note'], ['task', 'Файлы задачи', 'folder'], ['stage', 'Файлы этапа', 'list']],
      state.scope, key => { state.scope = key; renderZone(); });
    state.zoneHolder = h('div.zone-holder');
    const workZone = h('div.info-card.zone.work-zone',
      h('div.caption', h('span.badge-icon', icon('folder')), state.zoneTitle, h('span.spacer'), state.zoneStatus),
      state.zoneSwitch, state.zoneHolder);
    const right = h('div.task-col.right', workZone);

    // ── нижняя строка: состояние этапа и все кнопки рядом ─────────────────
    state.stageInfo = h('span.small.muted.stage-status');
    state.stageBtns = h('div.btn-row');
    state.taskBtns = h('div.btn-row');
    state.stageActions = h('div.btn-row.stage-actions',
      state.stageInfo, h('span.spacer'), state.stageBtns, state.taskBtns);
    renderTaskButtons();

    // На телефоне две колонки рядом не помещаются: вместо них — переключатель
    // «О задаче / Работа». На компьютере он скрыт стилями и ничего не меняет.
    const layout = h('div.task-layout.stretch.show-work', left, right);
    const mobileTabs = Kov.UI.segmented(
      [['info', 'О задаче', 'info'], ['work', 'Работа', 'folder']], 'work',
      key => {
        layout.classList.toggle('show-info', key === 'info');
        layout.classList.toggle('show-work', key === 'work');
        if (key === 'work') requestAnimationFrame(paintStageShape);
      });
    mobileTabs.classList.add('task-tabs');
    // Полоса «ждём подтверждения» — над этапами: её видят все, кто открыл
    // задачу, а директору она даёт кнопки решения не выходя отсюда
    const waiting = completionBanner();
    if (waiting) section.append(waiting);
    // Телефон: вариант «нижние вкладки» — короткая карточка задачи сверху,
    // «Этап · Файлы · Люди · История» внизу, «Завершить этап» — круглой кнопкой
    if (window.matchMedia('(max-width: 760px)').matches) {
      phoneLayout(section, { t, all, done, dState, project, about, description, progress, people, workZone, stagesRow });
      renderZone();
      markStageButtons();
      return;
    }
    section.append(stagesRow, mobileTabs, layout, state.stageActions);
    renderZone();
    markStageButtons();
  }

  function phoneLayout(section, p) {
    section.classList.add('task-v');
    state.zoneSwitch.hidden = true;
    const dl = p.t.deadline ? h('span.deadline' + (p.dState ? '.' + p.dState : ''), 'до ' + fmtDate(p.t.deadline)) : null;
    const sub = [p.t.customer_organization, p.project].filter(Boolean).join(' · ');
    const current = Math.min(p.done + 1, p.all.length);
    const more = h('div.tv-more', p.about, p.description);   // во вкладке «Люди» сведения сразу раскрыты
    const head = h('div.tv-head',
      h('div.tv-head-top', h('div.tv-title', p.t.name || 'Задача'), dl),
      sub ? h('div.tv-sub', sub) : null,
      h('div.tv-prog', Kov.UI.stageDots(p.all, p.all.length, Number(p.t.state)),
        h('span.small.muted', p.all.length ? (p.done >= p.all.length ? 'все этапы завершены' : 'этап ' + current + ' из ' + p.all.length) : 'без этапов'),
        h('span.tv-chev', icon('chevron_down'))),
      more);
    // касание раскрывает сведения о задаче; ссылки внутри раскрытого — работают как обычно
    head.addEventListener('click', e => {
      if (e.target.closest('.tv-more')) return;
      more.hidden = !more.hidden;
      head.classList.toggle('open', !more.hidden);
      requestAnimationFrame(() => state.fitPane && state.fitPane());
    });
    // «Файлы»: файлы задачи или этапа
    const fileBtns = [['task', 'Файлы задачи'], ['stage', 'Файлы этапа']].map(([k, label]) =>
      h('button.tv-sw', { type: 'button', dataset: { k }, onclick: () => { state.scope = k; paintFiles(); renderZone(); } }, label));
    const filesSwitch = h('div.tv-switch', fileBtns);
    const paintFiles = () => fileBtns.forEach(b => b.classList.toggle('on', b.dataset.k === state.scope));
    // «Этап»: состояние сверху, описание — во всё свободное место, «Завершить этап» — внизу карточки
    const zonePane = h('div.tv-pane.stage-pane', state.stageInfo, filesSwitch, p.workZone, state.stageActions);
    head.classList.add('open');
    const peoplePane = h('div.tv-pane', head, p.people);          // сведения о задаче — только во вкладке «Люди»
    const tabs = [['stage', 'Этап', 'note'], ['files', 'Файлы', 'folder'], ['people', 'Люди', 'users'], ['history', 'История', 'clock']];
    const bar = h('div.tv-tabbar', tabs.map(([k, label, ic]) =>
      h('button.tv-tab', { type: 'button', dataset: { k }, onclick: () => pick(k) }, icon(ic), h('span', label))));
    // карточка этапа тянется до панели вкладок
    const fit = () => {
      if (!zonePane.isConnected) return;
      if (zonePane.hidden || state.vtab !== 'stage') { zonePane.style.minHeight = ''; return; }
      const vv = window.visualViewport;
      if (vv && vv.height < window.innerHeight * 0.7) return;          // клавиатура открыта — размеры не трогаем
      let sc = section.parentElement;
      while (sc && sc !== document.body && !/(auto|scroll)/.test(getComputedStyle(sc).overflowY)) sc = sc.parentElement;
      const box = sc && sc !== document.body ? sc : null;
      const top = zonePane.getBoundingClientRect().top - (box ? box.getBoundingClientRect().top - box.scrollTop : -window.scrollY);
      const viewH = box ? box.clientHeight : window.innerHeight;
      zonePane.style.minHeight = Math.max(260, Math.round(viewH - top - bar.offsetHeight - 12)) + 'px';
    };
    state.fitPane = fit;
    if (!window.__kovFitHook) { window.__kovFitHook = true; window.addEventListener('resize', () => state.fitPane && state.fitPane()); }
    function pick(k) {
      if (k === 'history') return historyDialog();
      state.vtab = k;
      bar.querySelectorAll('.tv-tab').forEach(b => b.classList.toggle('on', b.dataset.k === k));
      zonePane.hidden = k === 'people';
      peoplePane.hidden = k !== 'people';
      filesSwitch.hidden = k !== 'files';
      state.stageInfo.hidden = k !== 'stage';
      state.stageActions.hidden = k !== 'stage';
      if (k === 'stage') state.scope = 'desc';
      else if (k === 'files' && state.scope === 'desc') state.scope = 'task';
      paintFiles();
      if (k !== 'people') renderZone();
      requestAnimationFrame(fit);
    }
    const waiting = section.querySelector('.completion-banner');
    section.append(p.stagesRow, zonePane, peoplePane, bar);
    if (waiting) section.prepend(waiting);
    pick(state.vtab && state.vtab !== 'history' ? state.vtab : 'stage');
  }

  /** Журнал задачи — то же окно, что у склада и оборудования */
  function historyDialog() {
    // У задачи выдач не бывает: показываем один раздел, без пустой вкладки
    const GLYPH = { deadline: 'clock', responsible: 'users', assigned: 'users',
      stage_completed: 'check', stage_department: 'building', activity: 'archive', project_id: 'project',
      task_file: 'file', stage_file: 'file', stage_participants: 'users',
      task_participants: 'users', stage_done: 'check', stage_added: 'plus',
      stage_removed: 'trash', stage_resumed: 'refresh', stage_text: 'edit',
      stage_lead: 'users', task_completion: 'flag', task_completed: 'check' };
    Kov.Journal.open({
      title: 'Журнал задачи',
      subtitle: (state.task && state.task.name) || '',
      load: async () => {
        const rows = await Kov.Api.get('tasks/' + state.taskId + '/history');
        return {
          issues: [],
          edits: (rows || []).map(r => ({
            kind: 'edit', action: GLYPH[r.field] ? 'changed' : 'changed',
            glyph: GLYPH[r.field], who: r.who, at: r.at,
            title: r.field_title,
            // События без «было» — прикрепили файл, завершили этап — одной строкой
            text: (r.old && r.old !== '—') ? (r.old + ' → ' + r.new) : r.new,
          })),
        };
      },
    });
  }

  /** Кнопки задачи живут отдельно от кнопок этапа и не стираются вместе с ними */
  function renderTaskButtons() {
    const box = state.taskBtns; if (!box) return;
    Kov.UI.clear(box);
    const t = state.task;
    box.append(button('', { kind: 'ghost', icon: 'clock', title: 'Кто и когда менял задачу', onClick: historyDialog }));
    const all = stages();
    const allDone = all.length > 0 && all.every(s => s.is_completed);
    if (!isArchived()) {
      const last = all[all.length - 1];
      // Кнопку не прячем, а запираем: пропавшая кнопка выглядит как поломка,
      // а с замком видно, что действие есть, но не для тебя
      if (allDone && all.length < 10) {
        box.append(Kov.Rights.gate(
          button('Продолжить задачу', { icon: 'plus', title: 'Добавить следующий этап', onClick: continueTask }),
          Kov.Rights.canExtendTask(last),
          'Продлить задачу может ответственный за последний этап, руководитель отдела или директор'));
      }
      if (allDone) {
        const boss = Kov.Store.isManagement();
        box.append(Kov.Rights.gate(
          button(boss ? 'Завершить задачу (Архив)' : 'Отправить на завершение',
            { icon: boss ? 'archive' : 'send',
              title: boss ? 'Перенести задачу в архив' : 'Директора подтвердят завершение',
              onClick: completeTask }),
          Kov.Rights.canRequestCompletion(last, t),
          'Запросить завершение может руководитель отдела задачи, ответственный за последний этап или руководство'));
      }
    } else {
      const who = archivedWho(t), when = archivedWhen(t);
      box.append(h('span.chip.warning', { title: 'Задача перенесена в архив' },
        'В архиве' + (when ? ' · ' + fmtDateTime(when) : '') + (who ? ' · ' + who : '')));
      if (Kov.Store.canManageWork()) {
        box.append(Kov.Rights.gate(
          button('Вернуть в работу', { kind: 'ghost', icon: 'refresh', onClick: unarchive }),
          Kov.Rights.canManageTask(t),
          'Вернуть задачу в работу может руководство или руководитель отдела, к которому относятся все её этапы'));
      }
    }
  }

  /** Содержимое рабочей зоны: описание этапа, файлы задачи или файлы этапа */
  function renderZone() {
    if (!state.zoneHolder || !state.zoneHolder.isConnected) return;
    const s = state.stage;
    const stageBtn = state.zoneSwitch.querySelector('[data-key="stage"]');
    if (stageBtn) stageBtn.lastChild.textContent = s ? 'Файлы этапа ' + s.stage_number : 'Файлы этапа';
    state.descArea = null;   // поле ввода существует только на вкладке описания
    state.editor = null;
    state.fileBlock = null;
    if (state.zoneStatus) { state.zoneStatus.hidden = true; state.zoneStatus.className = 'chip'; state.zoneStatus.textContent = ''; }
    Kov.UI.clear(state.zoneHolder);
    if (state.scope !== 'task' && !s) {
      state.zoneHolder.append(h('div.zone-body', h('div.empty', 'Выберите этап выше')));
      return;
    }
    if (state.scope === 'desc') { renderDescription(); return; }

    const onStage = state.scope === 'stage';
    const editableStage = onStage && !isArchived() && !s.is_completed && Kov.Rights.canEditStage(s);
    const hint = onStage
      ? (isArchived() ? 'Задача в архиве — файлы этапа изменить нельзя'
        : s.is_completed ? 'Этап завершён — возобновите его, чтобы добавить файлы'
        : 'Файлы этапа добавляет ведущий его отдел')
      : (isArchived() ? 'Задача в архиве — файлы задачи изменить нельзя' : 'Проект завершён — файлы задачи изменить нельзя');
    const block = Kov.Files.categoryBlock({
      initialCategory: state.fileCategory,
      // зона принимает перенос: подсветка появляется только на файлах,
      // выделенный текст и ссылки её не включают
      dropZone: true,
      onCategoryChange: key => { state.fileCategory = key; },
      getFiles: () => onStage
        ? ((state.stage && state.stage.files) || []).map(f => Object.assign({ file_kind: 'stage' }, f))
        : (state.task.task_files || []).filter(f => !f.stage_detail_id).map(f => Object.assign({ file_kind: 'task' }, f)),
      // файлы задачи — всем, кто видит задачу; в архиве и в завершённом проекте — никому
      canUpload: onStage ? editableStage : (!isArchived() && !Kov.Rights.readOnly()),
      canDelete: file => onStage
        ? (editableStage || (!isArchived() && !s.is_completed && ownUpload(file)))
        : (!isArchived() && (canManage() || Kov.Rights.headForTask(state.task) || ownUpload(file))),
      uploadHint: hint,
      uploadPath: 'upload',
      uploadFields: onStage ? { stage_detail_id: s.id } : { task_id: state.taskId },
      // добавленный файл сразу кладём в список — вкладка и прокрутка не сбрасываются
      onUploaded: created => {
        if (onStage) { if (state.stage) state.stage.files = created.concat(state.stage.files || []); }
        else state.task.task_files = created.concat(state.task.task_files || []);
      },
      onChanged: () => reloadFiles(onStage),
    });
    state.fileBlock = block;
    state.zoneHolder.append(h('div.zone-body.fit', block));
  }

  /**
   * Вкладка «Описание этапа». Кнопки «Сохранить» больше нет: текст уходит на
   * сервер сам, а сервер тем же запросом рассылает его остальным по WebSocket.
   * Цвет под текстом показывает, кто именно этот кусок написал; имя автора —
   * в подсказке при наведении.
   */
  function renderDescription() {
    const s = state.stage; if (!s) return;
    const editable = !isArchived() && !s.is_completed && Kov.Rights.canEditStage(s);
    state.savedText = s.text_description || '';
    const body = h('div.zone-body');
    const status = state.zoneStatus;   // чип в шапке зоны: ширина строки участников не пляшет
    if (status) { status.hidden = true; status.className = 'chip'; status.textContent = ''; }

    if (editable) {
      state.editor = descEditor(s, status);
      state.descArea = state.editor.input;
      body.append(state.editor.el);
    } else {
      state.descArea = null;
      state.editor = null;
      body.append(h('div.desc-view', { html: spanHtml(s) }));
    }

    const foot = h('div.zone-foot');
    const legend = authorsLegend(s);
    if (legend) foot.append(h('div.row.authors-legend', legend));
    const stagePeople = clampedPeople(s.participants, 1,
      () => peopleDialog('Участники этапа ' + s.stage_number, [['Участники этапа', s.participants || []]]));
    const peopleRow = h('div.row.stage-people', h('span.small.muted', 'Участники этапа:'), stagePeople);
    // Состав правит ответственный за этап: сервер это же и проверяет
    if (s.i_lead && !s.is_completed) {
      peopleRow.append(button('Изменить', { kind: 'ghost', icon: 'edit', cls: 'roster-btn',
        title: 'Изменить участников этапа', onClick: () => rosterDialog(s) }));
    }
    foot.append(peopleRow);
    state.zoneHolder.append(body, foot);
  }

  /**
   * Правка состава этапа. Предлагаются сотрудники ведущего отдела; себя
   * снять нельзя, пока в составе нет другого ответственного — иначе этап
   * останется без хозяина, и вернуть себе права человек уже не сможет.
   */
  function rosterDialog(s) {
    const dep = s.department_name || '';
    const staff = departmentStaff(dep);
    const chosen = new Set((s.participants || []).map(p => p.id));
    const leadIds = new Set(s.leads || []);
    const me = Kov.Api.state.userId;
    const list = h('div.lead-list');
    const warn = h('div.lead-hint');

    function paint() {
      Kov.UI.clear(list);
      staff.forEach(p => {
        const on = chosen.has(p.id);
        const lead = leadIds.has(p.id);
        const row = h('button.lead-row' + (on ? '.is-on' : ''), { type: 'button' },
          Kov.UI.avatar(p, null, Kov.Store.photoUrl(p.id)),
          h('div.lead-who', h('b', Kov.UI.fullName(p, true)),
            h('span.muted.small', [lead ? 'ответственный' : null, p.position].filter(Boolean).join(' · '))),
          h('span.lead-mark', icon(on ? 'check' : 'plus')));
        row.onclick = () => { if (on) chosen.delete(p.id); else chosen.add(p.id); paint(); };
        list.append(row);
      });
      const othersLead = [...chosen].some(id => id !== me && (leadIds.has(id) || !leadIds.size));
      warn.textContent = (!chosen.has(me) && !othersLead)
        ? 'Себя снять нельзя, пока в составе нет другого ответственного'
        : 'Выбрано: ' + chosen.size;
      warn.classList.toggle('is-warn', !chosen.has(me) && !othersLead);
    }
    paint();

    Kov.UI.modal({
      title: 'Участники этапа ' + s.stage_number, glyph: 'users',
      body: el => el.append(h('div.lead-picker',
        h('div.lead-hint', 'Отдел этапа: ' + (dep || '—')), warn, list)),
      buttons: [
        { text: 'Отмена', kind: 'ghost' },
        { text: 'Сохранить', icon: 'check', onClick: async close => {
          try {
            await Kov.Api.put('stages/' + s.id + '/participants', { json: [...chosen] });
            close(); Kov.UI.toast('Состав этапа изменён', 'success');
            await refreshTask(); await refreshStage();
          } catch (e) { Kov.UI.toast(Kov.UI.errorText(e), 'error'); }
        } },
      ],
    });
  }

  // ── редактор описания: авторство + автосохранение ────────────────────────

  /** Локальная правка спанов при вставке текста — та же логика, что на сервере */
  function spansInsert(spans, pos, len, authorId) {
    const out = [];
    spans.forEach(sp => {
      if (sp.end <= pos) { out.push(sp); return; }
      if (sp.start >= pos) { out.push({ start: sp.start + len, end: sp.end + len, author_id: sp.author_id, author: sp.author }); return; }
      // вставка внутри фрагмента — делим его надвое
      out.push({ start: sp.start, end: pos, author_id: sp.author_id, author: sp.author });
      out.push({ start: pos + len, end: sp.end + len, author_id: sp.author_id, author: sp.author });
    });
    out.push({ start: pos, end: pos + len, author_id: authorId, author: meShort() });
    return mergeSpans(out);
  }

  /** Локальная правка спанов при удалении диапазона */
  function spansDelete(spans, start, end) {
    const len = end - start;
    const out = [];
    spans.forEach(sp => {
      if (sp.end <= start) { out.push(sp); return; }
      if (sp.start >= end) { out.push({ start: sp.start - len, end: sp.end - len, author_id: sp.author_id, author: sp.author }); return; }
      const a = Math.max(sp.start, start), b = Math.min(sp.end, end);
      const left = sp.start < start ? { s: sp.start, e: start } : null;
      const right = sp.end > end ? { s: end - len, e: sp.end - len } : null;
      if (left && right) { out.push({ start: left.s, end: right.e, author_id: sp.author_id, author: sp.author }); return; }
      if (left) out.push({ start: left.s, end: left.e, author_id: sp.author_id, author: sp.author });
      if (right) out.push({ start: Math.max(start, right.s), end: right.e, author_id: sp.author_id, author: sp.author });
      if (a >= b) { /* пересечения нет */ }
    });
    return mergeSpans(out.filter(x => x.end > x.start));
  }

  function mergeSpans(spans) {
    const list = spans.filter(x => x.end > x.start).sort((a, b) => a.start - b.start || a.end - b.end);
    const out = [];
    list.forEach(sp => {
      const prev = out[out.length - 1];
      if (prev && prev.end === sp.start && prev.author_id === sp.author_id) prev.end = sp.end;
      else out.push({ start: sp.start, end: sp.end, author_id: sp.author_id, author: sp.author });
    });
    return out;
  }

  function meShort() {
    const me = Kov.Store.state.me || {};
    return { id: Kov.Api.state.userId, last_name: me.last_name, first_name: me.first_name, middle_name: me.middle_name };
  }
  function spanAuthorName(sp) {
    if (sp.author && (sp.author.last_name || sp.author.first_name)) return Kov.UI.fullName(sp.author, true);
    if (!sp.author_id) return 'Автор удалён';
    const found = (Kov.Store.state.employees || []).find(e => e.id === sp.author_id);
    return found ? Kov.UI.fullName(found, true) : 'Сотрудник №' + sp.author_id;
  }

  /** Одна заменённая область: общий префикс и суффикс, между ними — правка */
  function diffOne(oldText, newText) {
    let prefix = 0;
    while (prefix < oldText.length && prefix < newText.length && oldText[prefix] === newText[prefix]) prefix++;
    let suffix = 0;
    while (suffix < oldText.length - prefix && suffix < newText.length - prefix &&
           oldText[oldText.length - 1 - suffix] === newText[newText.length - 1 - suffix]) suffix++;
    return { start: prefix, delEnd: oldText.length - suffix, insert: newText.slice(prefix, newText.length - suffix) };
  }

  function escapeHtml(text) {
    return String(text || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  /**
   * Поле ввода с подложкой: под текстом лежит зеркало с теми же переносами,
   * где каждый фрагмент подкрашен цветом своего автора. Само поле прозрачное,
   * поэтому курсор и выделение работают как в обычном textarea.
   */
  function descEditor(stage, status) {
    let text = stage.text_description || '';
    // старые записи могут выходить за границы текста — подрезаем, иначе
    // правка сдвинет несуществующие позиции
    const clamp = (list, len) => mergeSpans((list || [])
      .map(sp => ({ start: Math.max(0, Math.min(sp.start, len)), end: Math.max(0, Math.min(sp.end, len)),
                    author_id: sp.author_id, author: sp.author }))
      .filter(sp => sp.end > sp.start));
    let spans = clamp(stage.spans, text.length);
    let saved = text;
    let timer = null, saving = false, pending = false, failed = false;

    const mirror = h('div.desc-mirror', { 'aria-hidden': 'true' });
    const input = h('textarea.textarea.desc-input', { value: text, placeholder: 'Введите описание этого этапа…', spellcheck: 'false', 'data-writer-title': 'Описание этапа' });
    const tip = h('div.author-tip', { hidden: true });
    const el = h('div.desc-editor', mirror, input, tip);

    function setStatus(kind, textValue) {
      if (!status) return;
      status.hidden = false;
      status.className = 'chip save-state ' + kind;
      status.textContent = textValue;
    }

    function paint() {
      let html = '', pos = 0;
      spans.slice().sort((a, b) => a.start - b.start).forEach(sp => {
        const start = Math.max(pos, sp.start), end = Math.min(text.length, sp.end);
        if (start > pos) html += escapeHtml(text.slice(pos, start));
        if (end > start) {
          const name = spanAuthorName(sp);
          html += '<span class="span-author" style="--mark:' + Kov.UI.avatarColor(name) + '" data-author="' + escapeHtml(name) + '">'
            + escapeHtml(text.slice(start, end)) + '</span>';
        }
        pos = Math.max(pos, end);
      });
      if (pos < text.length) html += escapeHtml(text.slice(pos));
      mirror.innerHTML = html + '\n';        // хвостовой перенос: иначе последняя строка не совпадает
      mirror.scrollTop = input.scrollTop;
    }

    async function flush() {
      clearTimeout(timer); timer = null;
      if (saving) { pending = true; return; }
      if (text === saved && !failed) { setStatus('success', 'Сохранено'); return; }
      saving = true;
      setStatus('info', 'Сохранение…');
      spans = clamp(spans, text.length);
      const body = { text_description: text, spans: spans.map(sp => ({ start: sp.start, end: sp.end, author_id: sp.author_id || null })) };
      try {
        // тот же маршрут, что у настольной версии: сервер сохраняет текст со
        // спанами и сам рассылает stage.description_updated остальным
        await Kov.Api.put('tasks/' + state.taskId + '/stages/' + stage.id, { json: body });
        saved = body.text_description; failed = false;
        state.savedText = saved;
        stage.text_description = saved;
        stage.spans = spans.slice();
        setStatus('success', 'Сохранено');
      } catch (e) {
        failed = true;
        setStatus('warning', 'Не сохранено: ' + Kov.UI.errorText(e));
      } finally {
        saving = false;
        if (pending) { pending = false; flush(); }
      }
    }

    input.addEventListener('input', () => {
      const next = input.value;
      const d = diffOne(text, next);
      if (d.delEnd > d.start) spans = spansDelete(spans, d.start, d.delEnd);
      if (d.insert) spans = spansInsert(spans, d.start, d.insert.length, Kov.Api.state.userId);
      text = next;
      paint();
      setStatus('', 'Изменения не сохранены');
      clearTimeout(timer);
      timer = setTimeout(flush, 900);
    });
    input.addEventListener('scroll', () => { mirror.scrollTop = input.scrollTop; mirror.scrollLeft = input.scrollLeft; });
    input.addEventListener('blur', () => { if (text !== saved) flush(); });

    // подсказка с именем автора: ищем прямоугольник фрагмента под курсором
    input.addEventListener('mousemove', e => {
      let found = null;
      mirror.querySelectorAll('.span-author').forEach(node => {
        if (found) return;
        for (const r of node.getClientRects()) {
          if (e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom) { found = node; return; }
        }
      });
      if (!found) { tip.hidden = true; return; }
      tip.textContent = found.dataset.author;
      tip.hidden = false;
      const box = el.getBoundingClientRect();
      tip.style.left = Math.max(4, Math.min(e.clientX - box.left + 12, box.width - tip.offsetWidth - 6)) + 'px';
      tip.style.top = Math.max(4, e.clientY - box.top - tip.offsetHeight - 10) + 'px';
    });
    input.addEventListener('mouseleave', () => { tip.hidden = true; });

    paint();
    setStatus('success', 'Сохранено');

    /** Пришла правка от другого сотрудника — забираем её, если сами не печатаем */
    function applyRemote(newText, newSpans) {
      if (text !== saved) { setStatus('warning', 'Этап изменил другой сотрудник — сохраните свой текст'); return false; }
      text = newText || '';
      spans = clamp(newSpans, text.length);
      saved = text;
      state.savedText = text;
      stage.text_description = text; stage.spans = spans.slice();
      input.value = text;
      paint();
      setStatus('info', 'Обновлено другим сотрудником');
      return true;
    }

    return { el, input, flush, applyRemote, hasUnsaved: () => text !== saved };
  }

  // ── участники ────────────────────────────────────────────────────────────

  /** В задаче приходят только id и ФИО — отдел и должность берём из справочника */
  function personOf(p) {
    const found = (Kov.Store.state.employees || []).find(e => e.id === p.id);
    return Object.assign({}, p, found || {});
  }

  function personChip(p) {
    const e = personOf(p);
    return h('span.chip.person', { title: [Kov.UI.fullName(e, true), e.department, e.position].filter(Boolean).join(' · ') },
      Kov.UI.avatar(e, null, Kov.Store.photoUrl(e.id)), h('span.person-name', Kov.UI.fullName(e)));
  }

  // одна ResizeObserver на весь раздел: пересчитывает «+N» при смене ширины
  const fitObserver = typeof ResizeObserver !== 'undefined'
    ? new ResizeObserver(list => list.forEach(x => x.target._fit && x.target._fit())) : null;
  let fitBoxes = [];
  function observeFit(box) {
    if (!fitObserver) return;
    fitBoxes = fitBoxes.filter(b => { if (b.isConnected) return true; fitObserver.unobserve(b); return false; });
    fitBoxes.push(box); fitObserver.observe(box);
  }

  /**
   * Список людей строго заданной высоты: что не поместилось — прячем,
   * вместо этого чип «+N», открывающий окно с полным составом.
   */
  function clampedPeople(list, rows, onMore) {
    if (!list || !list.length) return h('span.muted', 'Не назначены');
    const box = h('div.chips.clamped');
    box.style.setProperty('--rows', String(rows || 2));
    const more = h('button.chip.more', { type: 'button', hidden: true, title: 'Показать весь список', onclick: onMore });
    list.forEach(p => box.append(personChip(p)));
    box.append(more);

    box._fit = function () {
      if (!box.isConnected) return;
      const chips = [...box.children].filter(c => c !== more);
      chips.forEach(c => { c.hidden = false; });
      more.hidden = true;
      const limit = box.clientHeight + 1;
      const fits = el => el.hidden || el.offsetTop + el.offsetHeight <= limit;
      if (chips.every(fits)) return;
      more.hidden = false;
      for (let i = chips.length - 1; i >= 0; i--) {
        chips[i].hidden = true;
        more.textContent = '+' + (chips.length - i);
        if (chips.every(fits) && fits(more)) return;
      }
    };
    requestAnimationFrame(box._fit);
    observeFit(box);
    return box;
  }

  /** Сотрудники отдела, ведущего этап */
  function departmentStaff(dep) {
    if (!dep) return [];
    return Kov.Store.visible(Kov.Store.state.employees).filter(e => e.department === dep);
  }

  /** Окно полного состава: groups — [[заголовок, список], …] */
  function peopleDialog(title, groups) {
    const box = h('div.people-list');
    const search = Kov.UI.searchInput('Поиск по ФИО, отделу, должности…', render);

    function render(needle) {
      needle = (needle || '').trim().toLowerCase();
      Kov.UI.clear(box);
      let shown = 0;
      groups.forEach(([caption, list]) => {
        const people = (list || []).map(personOf).filter(p => !needle ||
          (Kov.UI.fullName(p, true) + ' ' + (p.department || '') + ' ' + (p.position || '')).toLowerCase().includes(needle));
        shown += people.length;
        box.append(h('div.people-group', h('span', caption), h('span.spacer'),
          h('span.muted.small', needle ? people.length + ' из ' + (list || []).length : String((list || []).length))));
        if (!people.length) { box.append(h('div.muted.small', { style: { padding: '.2rem .45rem .5rem' } }, needle ? 'Совпадений нет' : 'Никто не назначен')); return; }
        people.forEach(p => {
          const row = h('div.people-row', { role: 'button', tabIndex: 0 },
            Kov.UI.avatar(p, null, Kov.Store.photoUrl(p.id)),
            h('div.who', h('b', Kov.UI.fullName(p, true) || ('сотрудник №' + p.id)),
              h('span', [p.department, p.position].filter(Boolean).join(' · ') || 'Данные сотрудника недоступны')));
          row.addEventListener('click', () => { dlg.close(); Kov.Router.go('#/employees/' + p.id); });
          box.append(row);
        });
      });
      if (!shown && needle) box.append(h('div.empty', 'Никого не найдено'));
    }

    render('');
    const dlg = Kov.UI.modal({
      title, glyph: 'users', wide: true,
      body: el => el.append(h('div.toolbar', search.el), box, h('div.small.muted', 'Нажмите на сотрудника, чтобы открыть его профиль.')),
      buttons: [],
    });
    return dlg;
  }

  /**
   * Переключатель этапов для узкого экрана: «‹ Этап 2 из 5 · Бухгалтерия ›»
   * плюс точки состояния. Нажатие на подпись открывает список всех этапов.
   */
  function buildMobileStages(all) {
    const prev = button('', { kind: 'ghost', icon: 'chevron_left', title: 'Предыдущий этап',
      onClick: () => step(-1) });
    const next = button('', { kind: 'ghost', icon: 'chevron_right', title: 'Следующий этап',
      onClick: () => step(1) });
    const label = h('button.stage-pick', { type: 'button', title: 'Выбрать этап', onclick: () => pickStage(all) });
    const dots = h('div.stage-dots');
    const nav = h('div.stage-mobile', prev, label, next);
    const box = h('div.stage-mobile-wrap', nav, dots);

    function step(delta) {
      const idx = all.findIndex(s => s.stage_number === state.stageNumber);
      for (let k = (idx < 0 ? 0 : idx) + delta; k >= 0 && k < all.length; k += delta) {   // чужие этапы — перескакиваем
        if (Kov.Rights.canOpenStage(all[k])) { selectStage(all[k].stage_number); return; }
      }
    }

    box.refresh = () => {
      const idx = Math.max(0, all.findIndex(s => s.stage_number === state.stageNumber));
      const cur = all[idx] || {};
      Kov.UI.clear(label);
      label.append(h('span.n', 'Этап ' + (cur.stage_number || 1) + ' из ' + all.length),
        h('span.dep', cur.department_name || ''));
      prev.disabled = idx <= 0;
      next.disabled = idx >= all.length - 1;
      Kov.UI.clear(dots);
      all.forEach((st, i) => dots.append(h('span.dot'
        + (st.is_completed ? '.done' : '') + (i === idx ? '.on' : ''),
        { title: 'Этап ' + st.stage_number + ' · ' + (st.department_name || '') })));
    };
    box.refresh();
    return box;
  }

  /** Полный список этапов — когда их много, листать стрелками долго */
  function pickStage(all) {
    const dlg = Kov.UI.modal({
      title: 'Этапы задачи', glyph: 'list',
      body: el => {
        const list = h('div.stage-list');
        all.forEach(st => {
          const row = h('button.stage-row' + (st.stage_number === state.stageNumber ? '.on' : ''), { type: 'button',
            onclick: () => { dlg.close(); selectStage(st.stage_number); } },
            h('span.num', String(st.stage_number)),
            h('div.who', h('b', 'Этап ' + st.stage_number), h('span', st.department_name || '')),
            h('span.chip' + (st.is_completed ? '.success' : ''), st.is_completed ? 'завершён' : 'в работе'));
          if (!Kov.Rights.canOpenStage(st)) Kov.Rights.lock(row, 'Этап ведёт отдел «' + (st.department_name || '—') + '» — он закрыт');
          list.append(row);
        });
        el.append(list);
      },
      buttons: [],
    });
  }

  // ── лента этапов: один объект, нарисованный целиком ──────────────────────

  const SVG_NS = 'http://www.w3.org/2000/svg';
  const stageShapeObserver = typeof ResizeObserver !== 'undefined'
    ? new ResizeObserver(() => paintStageShape()) : null;

  /** Цвета сегмента по его состоянию — те же, что у кнопок в стилях */
  function stageColors(btn) {
    if (btn.classList.contains('on')) return ['var(--info-soft)', 'var(--info)'];
    if (btn.classList.contains('done')) return ['var(--success-soft)', 'var(--stage-done)'];
    return ['var(--card-bg)', 'var(--accent-soft)'];
  }

  /**
   * Лента этапов рисуется одной фигурой: прямоугольники переходят друг в друга
   * через плавную талию. Линии не прерываются — на стыке у обеих кривых
   * горизонтальная касательная, поэтому смена цвета не даёт излома.
   */
  function paintStageShape() {
    const svg = state.stageShape, nav = state.stageNav;
    if (!svg || !nav || !nav.isConnected) return;
    const buttons = [...nav.querySelectorAll('.btn')];
    if (!buttons.length) return;
    const navBox = nav.getBoundingClientRect();
    const W = navBox.width, H = navBox.height;
    if (W < 4 || H < 4) return;

    const edges = buttons.map(b => {
      const r = b.getBoundingClientRect();
      return { x0: r.left - navBox.left, x1: r.right - navBox.left };
    });
    const widths = edges.map(e => e.x1 - e.x0);

    // Контур ведём с отступом внутрь: линия на самом краю обрезалась рамкой ленты
    const m = 1;
    const T = m, B = H - m, L = m, Rg = W - m;
    const height = B - T;
    const R = Math.min(height * 0.26, 14);        // скругление внешних углов
    const D = height * 0.3;                       // насколько талия сужает ленту
    const N = Math.min(height * 1.3, Math.min(...widths) * 0.55);   // ширина перехода
    const q = N / 4;
    const wT = T + D, wB = B - D;                 // границы талии

    const f = n => Math.round(n * 100) / 100;
    // вход и выход талии: касательная горизонтальна с обеих сторон, излома нет
    const inTop = x => `M ${f(x)},${f(wT)} C ${f(x + q)},${f(wT)} ${f(x + q)},${f(T)} ${f(x + N / 2)},${f(T)}`;
    const outTop = x => `L ${f(x - N / 2)},${f(T)} C ${f(x - q)},${f(T)} ${f(x - q)},${f(wT)} ${f(x)},${f(wT)}`;
    const inBot = x => `M ${f(x)},${f(wB)} C ${f(x + q)},${f(wB)} ${f(x + q)},${f(B)} ${f(x + N / 2)},${f(B)}`;
    const outBot = x => `L ${f(x - N / 2)},${f(B)} C ${f(x - q)},${f(B)} ${f(x - q)},${f(wB)} ${f(x)},${f(wB)}`;

    while (svg.firstChild) svg.removeChild(svg.firstChild);
    svg.setAttribute('viewBox', '0 0 ' + f(W) + ' ' + f(H));

    buttons.forEach((btn, i) => {
      const first = i === 0, last = i === buttons.length - 1;
      const x0 = first ? L : (edges[i - 1].x1 + edges[i].x0) / 2;
      const x1 = last ? Rg : (edges[i].x1 + edges[i + 1].x0) / 2;
      const [fill, stroke] = stageColors(btn);

      // заливка — замкнутый контур; вертикали талии внутри фигуры и не видны
      let d = first ? `M ${f(L + R)},${f(T)}` : inTop(x0);
      d += last
        ? ` L ${f(Rg - R)},${f(T)} A ${f(R)} ${f(R)} 0 0 1 ${f(Rg)},${f(T + R)}`
          + ` L ${f(Rg)},${f(B - R)} A ${f(R)} ${f(R)} 0 0 1 ${f(Rg - R)},${f(B)}`
        : ' ' + outTop(x1)
          + ` L ${f(x1)},${f(wB)} C ${f(x1 - q)},${f(wB)} ${f(x1 - q)},${f(B)} ${f(x1 - N / 2)},${f(B)}`;
      d += first
        ? ` L ${f(L + R)},${f(B)} A ${f(R)} ${f(R)} 0 0 1 ${f(L)},${f(B - R)}`
          + ` L ${f(L)},${f(T + R)} A ${f(R)} ${f(R)} 0 0 1 ${f(L + R)},${f(T)} Z`
        : ` L ${f(x0 + N / 2)},${f(B)} C ${f(x0 + q)},${f(B)} ${f(x0 + q)},${f(wB)} ${f(x0)},${f(wB)} Z`;
      const area = document.createElementNS(SVG_NS, 'path');
      area.setAttribute('d', d);
      area.style.fill = fill;
      svg.append(area);

      // обводка — только верх, низ и внешние торцы: внутренних линий нет
      const top = (first ? `M ${f(L + R)},${f(T)}` : inTop(x0)) + ' '
        + (last ? `L ${f(Rg - R)},${f(T)}` : outTop(x1));
      const bot = (first ? `M ${f(L + R)},${f(B)}` : inBot(x0)) + ' '
        + (last ? `L ${f(Rg - R)},${f(B)}` : outBot(x1));
      let caps = '';
      if (first) caps += ` M ${f(L + R)},${f(T)} A ${f(R)} ${f(R)} 0 0 0 ${f(L)},${f(T + R)}`
        + ` L ${f(L)},${f(B - R)} A ${f(R)} ${f(R)} 0 0 0 ${f(L + R)},${f(B)}`;
      if (last) caps += ` M ${f(Rg - R)},${f(T)} A ${f(R)} ${f(R)} 0 0 1 ${f(Rg)},${f(T + R)}`
        + ` L ${f(Rg)},${f(B - R)} A ${f(R)} ${f(R)} 0 0 1 ${f(Rg - R)},${f(B)}`;
      const line = document.createElementNS(SVG_NS, 'path');
      line.setAttribute('d', top + ' ' + bot + caps);
      line.setAttribute('class', 'stage-outline');
      line.style.stroke = stroke;
      svg.append(line);
    });
  }

  /** Окно полного состава: groups — [[заголовок, список], …] */
  function peopleDialog(title, groups) {
    const box = h('div.people-list');
    const search = Kov.UI.searchInput('Поиск по ФИО, отделу, должности…', render);

    function render(needle) {
      needle = (needle || '').trim().toLowerCase();
      Kov.UI.clear(box);
      let shown = 0;
      groups.forEach(([caption, list]) => {
        const people = (list || []).map(personOf).filter(p => !needle ||
          (Kov.UI.fullName(p, true) + ' ' + (p.department || '') + ' ' + (p.position || '')).toLowerCase().includes(needle));
        shown += people.length;
        box.append(h('div.people-group', h('span', caption), h('span.spacer'),
          h('span.muted.small', needle ? people.length + ' из ' + (list || []).length : String((list || []).length))));
        if (!people.length) { box.append(h('div.muted.small', { style: { padding: '.2rem .45rem .5rem' } }, needle ? 'Совпадений нет' : 'Никто не назначен')); return; }
        people.forEach(p => {
          const row = h('div.people-row', { role: 'button', tabIndex: 0 },
            Kov.UI.avatar(p, null, Kov.Store.photoUrl(p.id)),
            h('div.who', h('b', Kov.UI.fullName(p, true) || ('сотрудник №' + p.id)),
              h('span', [p.department, p.position].filter(Boolean).join(' · ') || 'Данные сотрудника недоступны')));
          row.addEventListener('click', () => { dlg.close(); Kov.Router.go('#/employees/' + p.id); });
          box.append(row);
        });
      });
      if (!shown && needle) box.append(h('div.empty', 'Никого не найдено'));
    }

    render('');
    const dlg = Kov.UI.modal({
      title, glyph: 'users', wide: true,
      body: el => el.append(h('div.toolbar', search.el), box, h('div.small.muted', 'Нажмите на сотрудника, чтобы открыть его профиль.')),
      buttons: [],
    });
    return dlg;
  }


  function markStageButtons() {
    if (!state.root) return;
    state.root.querySelectorAll('.stage-nav .btn').forEach(b => b.classList.toggle('on', Number(b.dataset.stage) === state.stageNumber));
    requestAnimationFrame(paintStageShape);
    const mob = state.root && state.root.querySelector('.stage-mobile-wrap');
    if (mob && mob.refresh) mob.refresh();
    // лента прокручивается по горизонтали — держим активный этап на виду
    const active = state.root && state.root.querySelector('.stage-nav .btn.on');
    if (active && active.scrollIntoView) {
      requestAnimationFrame(() => active.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'smooth' }));
    }
  }

  // ── этап ─────────────────────────────────────────────────────────────────

  async function selectStage(stageNumber) {
    const meta = stages().find(s => s.stage_number === stageNumber);
    if (!meta) return;
    if (!Kov.Rights.canOpenStage(meta)) {   // чужой отдел — не открываем, откуда бы ни пришёл переход
      Kov.UI.toast('Этап ' + stageNumber + ' ведёт отдел «' + (meta.department_name || '—') + '» — он закрыт', 'warning');
      return;
    }
    state.stageNumber = stageNumber;
    markStageButtons();
    Kov.UI.clear(state.zoneHolder);
    state.zoneHolder.append(Kov.UI.bigLoading('Загружаем этап…'));
    try { await loadStage(meta.id); } catch (e) {
      Kov.UI.clear(state.zoneHolder);
      state.zoneHolder.append(h('div.empty', 'Этап не загружен: ' + Kov.UI.errorText(e)));
      return;
    }
    renderStage();
  }

  function renderStage() {
    const s = state.stage; if (!s || !state.zoneHolder || !state.zoneHolder.isConnected) return;
    state.savedText = s.text_description || '';
    const who = stageWho(s), when = stageWhen(s);
    const doneText = 'Этап ' + s.stage_number + ' завершён' + (who ? ': ' + who : '') + (when ? ' · ' + fmtDateTime(when) : '');
    const workText = 'Этап ' + s.stage_number + ' в работе · отдел: ' + (s.department_name || 'не указан');

    state.zoneTitle.textContent = 'Этап ' + s.stage_number + ' · ' + (s.department_name || 'отдел не указан');
    state.zoneStatus.hidden = false;
    state.zoneStatus.className = 'chip ' + (s.is_completed ? 'success' : 'info');
    state.zoneStatus.textContent = s.is_completed ? 'Завершён' : 'В работе';
    state.zoneStatus.title = s.is_completed ? doneText : workText;
    if (state.stageInfo) state.stageInfo.textContent = s.is_completed ? doneText : workText;

    // кнопки этапа — в общей нижней строке, рядом с кнопками задачи
    Kov.UI.clear(state.stageBtns);
    if (!isArchived()) {
      if (!s.is_completed) {
        if (Kov.Rights.canCompleteStage(s)) state.stageBtns.append(button('Завершить этап', { icon: 'check', onClick: completeStage }));
      } else if (Kov.Rights.canCompleteStage(s)) {
        state.stageBtns.append(button('Возобновить этап', { kind: 'ghost', icon: 'refresh', onClick: resumeStage }));
      }
    }
    renderZone();
  }

  function spanHtml(s) {
    const text = s.text_description || '';
    if (!text.trim()) return '<span class="muted">Описания пока нет.</span>';
    const spans = (s.spans || []).slice().sort((a, b) => a.start - b.start);
    if (!spans.length) return linkify(text);
    let html = ''; let pos = 0;
    spans.forEach(sp => {
      const start = Math.max(pos, sp.start), end = Math.min(text.length, sp.end);
      if (start > pos) html += linkify(text.slice(pos, start));
      if (end > start) {
        const name = sp.author ? Kov.UI.fullName(sp.author) : 'Автор удалён';
        html += '<span class="span-author" style="--mark:' + Kov.UI.avatarColor(name) + '" title="' + name.replace(/"/g, '&quot;') + '">' + linkify(text.slice(start, end)) + '</span>';
      }
      pos = Math.max(pos, end);
    });
    if (pos < text.length) html += linkify(text.slice(pos));
    return html;
  }

  function authorsLegend(s) {
    const names = new Map();
    (s.spans || []).forEach(sp => { if (sp.author) { const n = Kov.UI.fullName(sp.author); names.set(n, Kov.UI.avatarColor(n)); } });
    if (!names.size) return null;
    return h('div.chips', { style: { alignItems: 'center' } }, h('span.small.muted', 'Писали:'),
      [...names.entries()].map(([n, c]) => h('span.chip', h('span', { style: { width: '.6rem', height: '.6rem', borderRadius: '50%', background: c, display: 'inline-block' } }), n)));
  }

  // ── действия этапа ───────────────────────────────────────────────────────

  async function completeStage() {
    const s = state.stage;
    const hasContent = (state.descArea ? state.descArea.value : s.text_description || '').trim() || (s.files || []).length;
    const text = hasContent ? 'Завершить этап ' + s.stage_number + '?' : 'У этапа нет ни описания, ни файлов. Всё равно завершить?';
    if (!await Kov.UI.confirm('Завершение этапа', text, { okText: 'Завершить', icon: 'check' })) return;
    if (state.editor && state.editor.hasUnsaved()) await state.editor.flush();
    try {
      await Kov.Api.post('tasks/' + state.taskId + '/stages/' + s.id + '/complete');
      Kov.UI.toast('Этап завершён', 'success');
      await notifyTask('Этап ' + s.stage_number + ' задачи «' + (state.task.name || '') + '» завершён',
        'Вы завершили этап ' + s.stage_number + ' задачи «' + (state.task.name || '') + '»');
      await refreshTask(); await refreshStage();
    } catch (e) { Kov.UI.toast('Не удалось завершить: ' + Kov.UI.errorText(e), 'error'); }
  }

  async function resumeStage() {
    const s = state.stage;
    if (!await Kov.UI.confirm('Возобновление этапа', 'Вернуть этап ' + s.stage_number + ' в работу?', { okText: 'Возобновить', icon: 'refresh' })) return;
    try {
      await Kov.Api.post('tasks/' + state.taskId + '/stages/' + s.id + '/resume');
      Kov.UI.toast('Этап возобновлён', 'success');
      await notifyTask('Этап ' + s.stage_number + ' задачи «' + (state.task.name || '') + '» возобновлён',
        'Вы возобновили этап ' + s.stage_number + ' задачи «' + (state.task.name || '') + '»');
      await refreshTask(); await refreshStage();
    } catch (e) { Kov.UI.toast('Не удалось возобновить: ' + Kov.UI.errorText(e), 'error'); }
  }

  // ── действия задачи ──────────────────────────────────────────────────────

  /**
   * Список ответственных за новый этап: карточки с выбором мышью.
   *
   * Обычный выпадающий список тут не годится — выбрать можно нескольких, и
   * важно видеть, кто руководитель, а кто рядовой. Сервер отдаёт кандидатов
   * и признак «руководители в отделе есть»: в договорном отделе их нет, и
   * тогда ответственным может стать любой сотрудник.
   */
  function leadPicker() {
    const chosen = new Set();
    const list = h('div.lead-list');
    const hint = h('div.lead-hint');
    const box = h('div.lead-picker', hint, list);
    let people = [];

    function paint() {
      Kov.UI.clear(list);
      people.forEach(p => {
        const on = chosen.has(p.id);
        const row = h('button.lead-row' + (on ? '.is-on' : ''), { type: 'button' },
          Kov.UI.avatar(p, null, Kov.Store.photoUrl(p.id)),
          h('div.lead-who',
            h('b', Kov.UI.fullName(p, true)),
            h('span.muted.small', [p.is_head ? 'руководитель отдела' : null, p.position]
              .filter(Boolean).join(' · '))),
          h('span.lead-mark', icon(on ? 'check' : 'plus')));
        row.onclick = () => { if (on) chosen.delete(p.id); else chosen.add(p.id); paint(); };
        list.append(row);
      });
      const n = chosen.size;
      hint.textContent = n === 0
        ? (people.length ? 'Никого не выбрали — этап поведут все ' + (box.dataset.heads === '1' ? 'руководители отдела' : 'сотрудники отдела') + ', в состав войдёт весь отдел' : 'В этом отделе некого назначить')
        : (n === 1 ? 'Один ответственный — этап уйдёт только ему, без исполнителей'
                   : 'Ответственных ' + n + ' — в состав войдёт и весь отдел');
    }

    async function load(department) {
      Kov.UI.clear(list);
      list.append(Kov.UI.loading('Загружаем…'));
      chosen.clear();
      try {
        const data = await Kov.Api.get('stage-leads', { query: { department } });
        people = data.people || [];
        box.dataset.heads = data.heads_exist ? '1' : '0';
      } catch (e) { people = []; box.dataset.heads = '0'; }
      paint();
    }
    return { el: box, load, ids: () => [...chosen] };
  }

  async function continueTask() {
    await Kov.Store.lists();
    const fromServer = (Kov.Store.state.departments || []).filter(Boolean);
    const departments = (fromServer.length ? fromServer
      : ['Монтажный отдел', 'Бухгалтерия', 'Отдел ТО', 'Договорной отдел', 'Руководство'])
      .filter(d => d !== 'Администрация' && d !== 'Склад');
    const picker = leadPicker();
    const dep = Kov.UI.select(departments.map(d => [d, d]), departments[0], v => picker.load(v));
    const message = h('textarea.textarea', { placeholder: 'Сообщение нового этапа (необязательно)' });
    const next = stages().length + 1;
    picker.load(departments[0]);
    Kov.UI.modal({
      title: 'Продолжить задачу — этап ' + next, glyph: 'plus',
      body: el => el.append(field('Ответственный отдел', dep),
        field('Ответственный за этап', picker.el), field('Описание этапа', message)),
      buttons: [
        { text: 'Отмена', kind: 'ghost' },
        { text: 'Добавить этап', icon: 'check', onClick: async close => {
          try {
            // Состав определяет сервер по выбранным ответственным: клиент
            // больше не досылает список участников отдельным запросом
            const stage = await Kov.Api.post('tasks/' + state.taskId + '/stages', { json: {
              department_name: dep.getValue(), text_description: message.value.trim(),
              responsible_ids: picker.ids() } });
            const ids = ((stage && stage.participants) || []).map(e => e.id);
            const notify = [...new Set(ids.concat(
              (state.task.assigned_employees || []).map(e => e.id),
              (state.task.responsible_persons || []).map(e => e.id),
              [state.task.creator_id, Kov.Api.state.userId]))];
            await Kov.Views.notifications.send(notify,
              'Задача «' + state.task.name + '» продолжена: этап ' + next + ' (' + dep.getValue() + ')',
              'task_view', state.taskId,
              { selfMessage: 'Вы продолжили задачу «' + state.task.name + '»: этап ' + next + ' (' + dep.getValue() + ')' });
            close(); Kov.UI.toast('Этап добавлен', 'success');
            await refreshTask(); const nx = stages().find(s => s.stage_number === next); selectStage(nx && Kov.Rights.canOpenStage(nx) ? next : (state.stageNumber || next));
          } catch (e) { Kov.UI.toast('Не удалось: ' + Kov.UI.errorText(e), 'error'); }
        } },
      ],
    });
  }

  /**
   * Завершение задачи: не архив сразу, а заявка директорам.
   *
   * Директор и администратор — сами подтверждающая сторона, поэтому у них
   * задача уходит в архив тем же нажатием: ждать собственного подтверждения
   * бессмысленно. Сервер решает это сам, клиент лишь показывает исход.
   */
  async function completeTask() {
    const boss = Kov.Store.isManagement();
    const ok = await Kov.UI.confirm('Завершение задачи',
      boss ? 'Завершить задачу и перенести её в архив?'
           : 'Отправить задачу на подтверждение директорам? В архив она уйдёт после их согласия.',
      { okText: boss ? 'В архив' : 'Отправить', icon: boss ? 'archive' : 'send' });
    if (!ok) return;
    try {
      const res = await Kov.Api.post('tasks/' + state.taskId + '/completion');
      state.completion = res.request || null;
      Kov.UI.toast(res.status === 'archived' ? 'Задача перенесена в архив'
                                             : 'Заявка отправлена директорам', 'success');
      await refreshTask();
      if (state.stageNumber) selectStage(state.stageNumber);
    } catch (e) { Kov.UI.toast('Не удалось: ' + Kov.UI.errorText(e), 'error'); }
  }

  /** Решение директора внутри задачи — те же две кнопки, что в уведомлении */
  async function decideCompletion(path, okText) {
    let comment = '';
    if (path === 'decline') {
      const input = h('textarea.textarea', { placeholder: 'Причина (необязательно)' });
      const said = await new Promise(resolve => Kov.UI.modal({
        title: 'Отклонить завершение', glyph: 'question',
        body: el => el.append(field('Что доделать', input)),
        buttons: [{ text: 'Отмена', kind: 'ghost', onClick: close => { resolve(false); close(); } },
                  { text: 'Отклонить', icon: 'close', onClick: close => { comment = input.value.trim(); resolve(true); close(); } }],
        onClose: () => resolve(false),
      }));
      if (!said) return;
    }
    try {
      await Kov.Api.post('tasks/' + state.taskId + '/completion/' + path, { json: { comment } });
      Kov.UI.toast(okText, 'success');
      await loadCompletion();
      await refreshTask();
      if (state.stageNumber) selectStage(state.stageNumber);
    } catch (e) { Kov.UI.toast(Kov.UI.errorText(e), 'error'); }
  }

  async function loadCompletion() {
    try {
      const data = await Kov.Api.get('tasks/' + state.taskId + '/completion');
      state.completion = data.request || null;
      state.canDecide = !!data.can_decide;
    } catch (e) { state.completion = null; state.canDecide = false; }
  }

  /** Полоса «ждём подтверждения» над задачей */
  function completionBanner() {
    const req = state.completion;
    if (!req || req.status !== 'pending') return null;
    const box = h('div.completion-bar',
      icon('clock'),
      h('div.completion-text',
        h('b', 'Задача отправлена на завершение'),
        h('span.small.muted', req.requested_by_name + ' · ' + fmtDateTime(req.requested_at))));
    if (state.canDecide) {
      box.classList.add('is-mine');
      box.append(h('div.completion-actions',
        button('Подтвердить', { icon: 'check', onClick: () => decideCompletion('approve', 'Задача завершена') }),
        button('Отклонить', { icon: 'close', kind: 'ghost', onClick: () => decideCompletion('decline', 'Завершение отклонено') })));
    }
    return box;
  }

  async function unarchive() {
    if (!await Kov.UI.confirm('Возврат в работу', 'Вернуть задачу в работу?', { okText: 'Вернуть', icon: 'refresh' })) return;
    if (state.task && state.task.project_archived) return Kov.UI.toast('Задача относится к завершённому проекту — сначала верните проект в работу', 'warning');   // проект завершён
        try { await Kov.Api.post('tasks/' + state.taskId + '/unarchive'); Kov.UI.toast('Задача возвращена в работу', 'success');
      await notifyTask('Задача «' + (state.task.name || '') + '» возвращена в работу',
        'Вы вернули задачу «' + (state.task.name || '') + '» в работу'); await refreshTask(); if (state.stageNumber) selectStage(state.stageNumber); }
    catch (e) { Kov.UI.toast('Не удалось: ' + Kov.UI.errorText(e), 'error'); }
  }

  /**
   * Уведомляем всех, кто связан с задачей: ответственных, исполнителей,
   * участников этапов, создателя задачи и самого автора действия
   * (selfMessage — его текст, «Вы завершили этап…»).
   */
  async function notifyTask(message, selfMessage) {
    const t = state.task || {};
    const ids = [...new Set([...(t.responsible_persons || []), ...(t.assigned_employees || []),
      ...((state.stage && state.stage.participants) || []),
      ...((t.stage_details || []).flatMap(s => s.participants || []))].map(p => p.id)
      .concat([t.creator_id, Kov.Api.state.userId]))];
    await Kov.Views.notifications.send(ids, message, 'task_view', state.taskId, { selfMessage });
  }

  /**
   * Обновляем только список файлов: раньше здесь перерисовывалось всё окно,
   * и раздел файлов каждый раз сбрасывался на «Документы».
   */
  async function reloadFiles(onStage) {
    try {
      if (onStage && state.stage) await loadStage(state.stage.id);
      else await loadTask();
    } catch (e) { return; }
    if (state.fileBlock && state.fileBlock.isConnected) state.fileBlock.render();
    else renderZone();
  }

  async function refreshTask() {
    const keep = state.stageNumber;
    try { await loadTask(); } catch (e) { return; }
    render();
    state.stageNumber = keep;
    markStageButtons();
    if (keep && state.stage) renderStage();
  }
  async function refreshStage() {
    if (!state.stage) return;
    // пока в поле есть несохранённый текст, перерисовывать зону нельзя
    if (state.editor && state.editor.hasUnsaved()) return;
    try { await loadStage(state.stage.id); renderStage(); } catch (e) { /* */ }
  }

  // ── WebSocket ────────────────────────────────────────────────────────────

  let timer = null;
  // вернулись в приложение или связь восстановилась — список файлов сверяется с сервером
  // (событие об удалении могло пройти мимо, пока приложение было свёрнуто)
  const refetchFiles = () => {
    if (!state.root || !state.root.isConnected || document.visibilityState !== 'visible') return;
    if (state.scope === 'task' || state.scope === 'stage') reloadFiles(state.scope === 'stage');
  };
  document.addEventListener('visibilitychange', refetchFiles);
  if (Kov.WS && Kov.WS.onReconnect) Kov.WS.onReconnect(refetchFiles);

  function onEvent(evt) {
    if (!state.root || !state.root.isConnected) return;
    const kind = evt.type || '';
    if (evt.task_id !== undefined && Number(evt.task_id) !== state.taskId) return;
    if (kind === 'task.deleted') { Kov.UI.toast('Задача удалена другим пользователем', 'warning'); goBack(); return; }

    // своё эхо пропускаем: текст и файлы уже на экране
    const mine = evt.sender_id !== undefined && Number(evt.sender_id) === Kov.Api.state.userId;

    // описание этапа — забираем прямо из события, окно не перерисовываем
    if (kind === 'stage.description_updated') {
      if (mine) return;
      const s = state.stage;
      if (!s || Number(evt.stage_id) !== Number(s.id)) return;
      if (state.editor) { state.editor.applyRemote(evt.description, evt.spans); return; }
      s.text_description = evt.description || '';
      s.spans = evt.spans || [];
      if (state.scope === 'desc') renderZone();
      return;
    }

    // Состав поменялся: списки участников задачи и этапа должны сойтись у
    // всех сразу, поэтому перечитываем задачу целиком — она же и приносит
    // оба списка одним ответом
    // Решение по завершению видят все, кто открыл задачу
    if (kind === 'task.completion_requested' || kind === 'task.completion_declined'
        || kind === 'task.archived') {
      loadCompletion().then(() => refreshTask());
      if (kind === 'task.completion_declined' && evt.comment && !mine) {
        Kov.UI.toast('Завершение отклонено: ' + evt.comment, 'warning');
      }
      return;
    }

    if (kind === 'task.participants_updated') {
      if (mine) return;
      refreshTask();
      if (state.stage && Number(evt.stage_id) === Number(state.stage.id)) refreshStage();
      return;
    }

    // файлы — обновляем только список, чтобы не сбрасывать выбранный раздел
    if (kind === 'stage.file_added' || kind === 'stage.file_deleted') {
      if (mine) return;
      if (state.stage && Number(evt.stage_id) === Number(state.stage.id)) reloadFiles(true);
      return;
    }
    if (kind === 'task.file_added' || kind === 'task.file_deleted') {
      if (mine) return;
      reloadFiles(false);
      return;
    }
    // общий сигнал «у задачи изменились файлы»: обновляем только плитки файлов, а не всю задачу
    if (kind === 'task.files_changed') {
      clearTimeout(timer);
      timer = setTimeout(() => reloadFiles(!!state.stage), 300);
      return;
    }

    if (kind.startsWith('task.') || kind.startsWith('stage.')) {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        await refreshTask();
        if (kind.startsWith('stage.') && state.stage) refreshStage();
      }, 250);
    }
  }

  return { view, onEvent };
})();
