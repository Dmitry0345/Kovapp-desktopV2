/* Свод прав доступа: один источник правды для всего приложения.
 *
 * Раньше каждая проверка жила там, где понадобилась: где-то смотрели на
 * должность, где-то на маркер прав, где-то на отдел. Разъехаться такому
 * набору легко, а заметить расхождение трудно — кнопка просто перестаёт
 * работать у половины людей. Здесь правила собраны в одном месте и названы
 * словами постановки.
 *
 * Настоящая проверка всё равно на сервере: здесь решается только, что
 * человеку показывать и что помечать замком.
 */
window.Kov = window.Kov || {};

Kov.Rights = (function () {
  const CONTRACT = 'Договорной отдел';
  // В этих отделах раздел «Проекты» доступен только руководителям
  const NO_PROJECTS_FOR_WORKERS = ['Монтажный отдел', 'Отдел ТО'];

  function me() { return Kov.Store.state.me || {}; }
  function dept(user) { return String(((user || me()).department) || '').trim(); }
  function management(user) { return Kov.Store.isManagement(user || me()); }
  function head(user) { return Kov.Store.isDepartmentHead(user || me()); }
  function contract(user) { return dept(user) === CONTRACT; }

  // ── задачи ──────────────────────────────────────────────────────────────

  /** Создавать задачи: руководство и руководители отделов */
  function canCreateTask() {
    if (readOnly()) return false;   // завершённый проект — только просмотр
    return management() || head(); }

  /**
   * Сколько этапов вправе завести создатель. Руководитель отдела — только
   * один и только со своим отделом: задача на чужие отделы не его дело.
   */
  function maxStagesOnCreate() { return management() ? Infinity : 1; }
  function departmentsForNewTask(all) {
    if (management()) return all;
    const own = dept();
    return all.filter(d => d === own);
  }

  function taskDepartments(task) {
    if (!task) return [];
    if (Array.isArray(task.stage_details)) {
      return [...new Set(task.stage_details.map(s => (s.department_name == null ? null : String(s.department_name))))];
    }
    if (Array.isArray(task.departments)) return [...new Set(task.departments.map(String))];
    return [];
  }

  function headForTask(task) {
    if (!head()) return false;
    const own = (me().department == null ? '' : String(me().department));
    const deps = taskDepartments(task);
    return deps.length === 1 && deps[0] !== null && deps[0] !== '' && deps[0] === own;
  }

  function canManageTask(task) {
    if (readOnly()) return false;   // завершённый проект — только просмотр
    return management() || headForTask(task); }

  function canManageTemplates() { return management(); }

  /** Этап ведёт мой отдел — значит, содержимое этапа мне доступно */
  function inStageDepartment(stage) {
    if (!stage) return false;
    if (management()) return true;
    return String(stage.department_name || '').trim() === dept();
  }

  /**
   * Видно ли содержимое этапа. Чужой отдел закрыт целиком — и переключиться
   * на него нельзя. Это важно для задач, где этапы чередуются: первый и
   * третий мои, второй чужой. Первый и третий открываются, второй показан
   * в полосе этапов, но заперт.
   */
  function canOpenStage(stage) { return inStageDepartment(stage); }

  /** Править описание и файлы этапа — участникам этапа */
  function canEditStage(stage) {
    if (readOnly()) return false;   // завершённый проект — только просмотр
    return inStageDepartment(stage); }

  /**
   * Распоряжаться этапом: завершать его, менять состав, продлевать задачу.
   * Сервер отдаёт признак i_lead — он учитывает и названных ответственных, и
   * отдел без руководителей. Если признака нет (старый ответ), решаем сами.
   */
  function leadsStage(stage) {
    if (management()) return true;
    if (!stage) return false;
    if (typeof stage.i_lead === 'boolean') return stage.i_lead;
    if (!inStageDepartment(stage)) return false;
    return head() || contract();
  }

  function canCompleteStage(stage) {
    if (readOnly()) return false;   // завершённый проект — только просмотр
    return inStageDepartment(stage); }
  function canExtendTask(stage) {
    if (readOnly()) return false;   // завершённый проект — только просмотр
    return management() || head() || leadsStage(stage); }
  function canEditStageRoster(stage) {
    if (readOnly()) return false;   // завершённый проект — только просмотр
    return leadsStage(stage); }
  /** Просить завершения задачи — руководитель отдела или руководство */
  function canRequestCompletion(stage, task) {
    if (readOnly()) return false;   // завершённый проект — только просмотр
    return management() || headForTask(task) || leadsStage(stage); }
  /** Решать по заявке на завершение — только руководство */
  function canDecideCompletion() {
    if (readOnly()) return false;   // завершённый проект — только просмотр
    return management(); }

  // ── организации ─────────────────────────────────────────────────────────

  function canViewOrganizations() { return true; }
  function canEditOrganizations() { return management() || head() || contract(); }

  // ── проекты ─────────────────────────────────────────────────────────────

  /**
   * Раздел «Проекты» закрыт рядовым сотрудникам монтажа и техобслуживания —
   * их руководителям открыт.
   */
  function canViewProjects() {
    if (management() || head()) return true;
    return !NO_PROJECTS_FOR_WORKERS.includes(dept());
  }

  /** Заводить проекты: договорной отдел, руководители любых отделов, руководство */
  function canCreateProject() { return management() || head() || contract(); }

  /** Свой проект правит его создатель; чужой — только руководство */
  function canEditProject(project) {
    if (readOnly()) return false;   // завершённый проект — только просмотр
   
    if (!project) return canCreateProject();
    if (typeof project.can_edit === 'boolean') return project.can_edit;
    return Kov.Store.isDirector() || project.creator_id === me().id;
  }

  // ── разделы меню ────────────────────────────────────────────────────────

  const SECTIONS = {
    notifications: () => true,
    tasks: () => true,
    projects: canViewProjects,
    organizations: canViewOrganizations,
    equipment: () => true,
    employees: () => true,
    csv: management,
    profile: () => true,
    settings: () => true,
    messenger: () => true,
  };

  function canOpenSection(key) {
    const rule = SECTIONS[key];
    return rule ? !!rule() : true;
  }

  // завершённый проект — только просмотр: на его странице, в подразделах и в его задачах изменения недоступны
  let roScope = null;
  const here = () => location.hash.split('?')[0];
  function readOnly() { const h = here(); return !!roScope && (h === roScope || h.indexOf(roScope + '/') === 0); }
  function paintReadOnly(text) {
    const on = readOnly();
    document.documentElement.classList.toggle('kov-readonly', on);
    if (on && text) document.documentElement.style.setProperty('--ro-text', JSON.stringify(text));
  }
  function setReadOnly(on, text) { roScope = on ? here() : null; paintReadOnly(text); }
  window.addEventListener('hashchange', () => { if (!readOnly()) { roScope = null; paintReadOnly(); } });

  return {
    setReadOnly, readOnly,
    CONTRACT, canCreateTask, maxStagesOnCreate, departmentsForNewTask, taskDepartments, headForTask,
    canManageTask, canManageTemplates,
    inStageDepartment, canOpenStage, canEditStage, leadsStage, canCompleteStage,
    canExtendTask, canEditStageRoster, canRequestCompletion, canDecideCompletion,
    canViewOrganizations, canEditOrganizations, canViewProjects, canCreateProject,
    canEditProject, canOpenSection,
  };
})();

/**
 * Замок на недоступном действии.
 *
 * Просто спрятать кнопку мало: человек не понимает, потерялась она или ему
 * не положена. Поэтому кнопка остаётся на месте, гаснет, получает значок
 * замка и подсказку с причиной — при нажатии причина всплывает сообщением.
 */
// Название кнопки для подсказки: своё aria-label (у кнопок с короткой подписью на телефоне),
// иначе текст. Запоминается при первом обращении, чтобы замок его не затёр.
function baseLabel(btn) {
  if (btn._baseAria === undefined) btn._baseAria = btn.getAttribute('aria-label') || '';
  return btn._baseAria || (btn.textContent || 'Действие').trim();
}

Kov.Rights.lock = function (btn, reason) {
  if (!btn) return btn;
  btn.classList.add('is-locked');
  // Ни disabled, ни aria-disabled: на телефоне нет наведения, и единственный
  // способ узнать причину — нажать. Отключённая кнопка нажатие не пропустит,
  // а вспомогательные программы объявят её мёртвой. Поэтому кнопка живая,
  // просто вместо действия объясняет, почему нельзя.
  btn.disabled = false;
  btn.title = reason;
  btn.setAttribute('aria-label', baseLabel(btn) + ' — ' + reason);
  const mark = Kov.UI.icon('key');
  mark.classList.add('lock-mark');
  btn.append(mark);
  btn.addEventListener('click', e => {
    e.preventDefault();
    e.stopImmediatePropagation();
    Kov.UI.toast(reason, 'info');
  }, true);
  return btn;
};

Kov.Rights.setLocked = function (btn, locked, reason) {
  if (!btn) return btn;
  if (!btn._lockHook) {
    btn._lockHook = true;
    btn._baseTitle = btn.title || '';
    if (btn._baseAria === undefined) btn._baseAria = btn.getAttribute('aria-label') || '';
    btn.addEventListener('click', e => {
      if (!btn.classList.contains('is-locked')) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      Kov.UI.toast(btn.dataset.lockReason || 'Действие недоступно', 'info');
    }, true);
  }
  const mark = btn.querySelector('.lock-mark');
  btn.classList.toggle('is-locked', !!locked);
  if (locked) {
    btn.dataset.lockReason = reason;
    btn.title = reason;
    btn.setAttribute('aria-label', baseLabel(btn) + ' — ' + reason);
    if (!mark) {
      const icon = Kov.UI.icon('key');
      icon.classList.add('lock-mark');
      btn.append(icon);
    }
  } else {
    delete btn.dataset.lockReason;
    btn.title = btn._baseTitle;
    if (btn._baseAria) btn.setAttribute('aria-label', btn._baseAria);
    else btn.removeAttribute('aria-label');
    if (mark) mark.remove();
  }
  return btn;
};

/** Кнопка, доступная не всем: allowed → обычная, иначе с замком */
Kov.Rights.gate = function (btn, allowed, reason) {
  return allowed ? btn : Kov.Rights.lock(btn, reason);
};
