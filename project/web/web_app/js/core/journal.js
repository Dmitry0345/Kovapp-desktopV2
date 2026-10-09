/* Единое окно журнала действий.
 *
 * Раньше журналы жили порознь: у задачи свой список, у склада своё окно, у
 * оборудования полоска внутри карточки. Выглядели по-разному, искать в них
 * было нечем. Здесь одно окно на все три раздела: сверху разделы «Выдача» и
 * «Редактирование», рядом поиск по сотруднику.
 *
 * Поиск ищет не только автора действия, но и того, кого оно касается: по
 * фамилии кладовщика находятся его выдачи, по фамилии получателя — всё, что
 * ему выдавали. Иначе «кто в чём участвовал» пришлось бы искать глазами.
 *
 * Разделы, которых нет, не показываются: у задачи выдач не бывает, и лишняя
 * пустая вкладка там ни к чему.
 */
window.Kov = window.Kov || {};

Kov.Journal = (function () {
  const { h, icon, button } = Kov.UI;

  const GLYPH = {
    issued: 'send', confirmed: 'check', declined: 'close', cancelled: 'close',
    return_requested: 'undo', returned: 'check', return_declined: 'close',
    pending: 'clock', edit: 'edit', created: 'plus', changed: 'edit',
  };

  const TONE = {
    confirmed: 'ok', returned: 'ok', issued: 'wait', pending: 'wait',
    return_requested: 'wait', cancelled: 'bad', declined: 'bad', return_declined: 'bad',
  };

  function when(at) {
    if (!at) return '';
    return Kov.UI.fmtDateTime ? Kov.UI.fmtDateTime(at) : new Date(at).toLocaleString('ru-RU');
  }

  function nameOf(person) {
    if (!person) return '';
    if (typeof person === 'string') return person;
    return person.name || [person.last_name, person.first_name].filter(Boolean).join(' ');
  }

  /** Все, кого запись касается: и автор, и тот, о ком речь */
  function peopleOf(entry) {
    return [entry.who, entry.target].map(nameOf).filter(Boolean);
  }

  function matches(entry, needle) {
    if (!needle) return true;
    const hay = peopleOf(entry).join(' ').toLowerCase() + ' '
      + String(entry.title || '').toLowerCase() + ' ' + String(entry.text || '').toLowerCase();
    return hay.includes(needle);
  }

  function line(entry) {
    const tone = TONE[entry.action] || (entry.kind === 'issue' ? 'wait' : 'edit');
    return h('div.jr-row.tone-' + tone,
      h('span.circ', icon(GLYPH[entry.action] || (entry.kind === 'issue' ? 'send' : 'edit'))),
      h('div.jr-body',
        h('b', entry.title || ''),
        entry.text ? h('span.jr-text', entry.text) : null,
        h('span.jr-when', [nameOf(entry.who), when(entry.at)].filter(Boolean).join(' · '))));
  }

  /**
   * Показывает окно журнала.
   *
   * cfg: {
   *   title, subtitle,
   *   load() -> { issues: [], edits: [] }   записи в общем виде
   * }
   * Запись: { kind: 'issue' | 'edit', action, title, text, who, target, at }
   */
  function open(cfg) {
    const state = { tab: 'issues', query: '', data: null };
    const list = h('div.jr-list');
    const count = h('span.jr-count');
    const search = h('input.input.jr-search', { type: 'search', placeholder: 'Поиск по сотруднику…' });
    const tabs = h('div.jr-tabs');

    function paint() {
      const rows = (state.data && state.data[state.tab]) || [];
      const needle = state.query.trim().toLowerCase();
      const found = rows.filter(r => matches(r, needle));
      Kov.UI.clear(list);
      if (!found.length) {
        list.append(h('div.jr-empty',
          rows.length ? 'По этому запросу ничего нет' : 'Записей пока нет'));
      } else {
        found.forEach((r, i) => {
          const el = line(r);
          el.style.setProperty('--jr-i', String(Math.min(i, 14)));
          list.append(el);
        });
      }
      count.textContent = needle
        ? ('найдено: ' + found.length + ' из ' + rows.length)
        : ('записей: ' + rows.length);
    }

    function buildTabs() {
      Kov.UI.clear(tabs);
      const kinds = [];
      if ((state.data.issues || []).length || cfg.alwaysIssues) kinds.push(['issues', 'Выдача']);
      kinds.push(['edits', 'Редактирование']);
      if (kinds.length < 2) { state.tab = kinds[0][0]; return; }
      if (!kinds.some(k => k[0] === state.tab)) state.tab = kinds[0][0];
      kinds.forEach(([key, label]) => {
        const b = h('button.jr-tab' + (key === state.tab ? '.on' : ''), { type: 'button' },
          h('span', label),
          h('span.jr-tab-n', String(((state.data[key] || []).length))));
        b.onclick = () => {
          state.tab = key;
          tabs.querySelectorAll('.jr-tab').forEach(x => x.classList.remove('on'));
          b.classList.add('on');
          paint();
        };
        tabs.append(b);
      });
    }

    search.addEventListener('input', () => { state.query = search.value; paint(); });

    const box = Kov.UI.modal({
      title: cfg.title || 'Журнал действий', glyph: 'clock', wide: true,
      body: el => el.append(h('div.jr-box',
        cfg.subtitle ? h('div.jr-subtitle', cfg.subtitle) : null,
        h('div.jr-head', tabs, h('div.jr-search-wrap', icon('search'), search), count),
        list)),
      buttons: [],
    });
    box.el.classList.add('jr-modal');

    list.append(Kov.UI.loading ? Kov.UI.loading('Читаем журнал…') : h('div.jr-empty', 'Загружаем…'));
    Promise.resolve()
      .then(() => cfg.load())
      .then(data => {
        state.data = { issues: data.issues || [], edits: data.edits || [] };
        buildTabs();
        paint();
      })
      .catch(e => {
        Kov.UI.clear(list);
        list.append(h('div.jr-empty', Kov.UI.errorText(e)));
      });
    return box;
  }

  return { open, when, nameOf };
})();
