/* Показ таблиц настоящими ячейками.
 *
 * Раньше xlsx открывался как документ: сервер делал из него PDF, и человек
 * видел картинку таблицы — без выделения, без прокрутки по столбцам, с
 * разрывами страниц посреди данных. Здесь сервер отдаёт книгу данными
 * (js/core/sheet-view.js ↔ server_app/sheets.py), а сетку рисует браузер.
 *
 * Что умеет: закреплённые шапки строк и столбцов, вкладки листов, поиск по
 * значениям с переходом к найденному, показ полного значения выделенной
 * ячейки, объединённые ячейки, оформление из книги. На телефоне — то же
 * самое, только плотнее и с прокруткой пальцем.
 */
window.Kov = window.Kov || {};

Kov.SheetView = (function () {
  const { h, icon, button } = Kov.UI;

  // Путь к странице просмотра считаем от собственного адреса, а не от адреса
  // страницы: приложение живёт на хэш-маршрутах, и относительная ссылка
  // «sheet.html» разрешалась от текущего маршрута — уводя на несуществующий
  // адрес и открывая пустую вкладку.
  const SELF_SRC = (document.currentScript && document.currentScript.src) || '';
  const PAGE_URL = SELF_SRC
    ? SELF_SRC.replace(/js\/core\/sheet-view\.js.*$/, 'sheet.html')
    : 'sheet.html';

  const SHEET_EXT = ['.xlsx', '.xlsm', '.xltx', '.xltm', '.csv', '.tsv'];
  function isSheet(name) {
    const m = /\.[^.\/\\]+$/.exec(String(name || '').toLowerCase());
    return !!m && SHEET_EXT.includes(m[0]);
  }

  /** Ширина столбца книги → пиксели. 1 единица ≈ ширина знака шрифта. */
  function colWidth(col) {
    if (col && col.h) return 0;
    const units = (col && col.w) || 9;
    return Math.max(56, Math.min(360, Math.round(units * 7.5)));
  }

  /**
   * Рисует одну книгу. data — ответ сервера: { filename, sheets, letters }.
   * Возвращает узел; вызывающий сам решает, куда его положить.
   */
  function render(data, opts) {
    opts = opts || {};
    let active = 0;
    let found = [];                 // совпадения поиска: [{ r, c }]
    let foundAt = -1;

    const status = h('div.sheet-status');
    const grid = h('div.sheet-grid');
    const tabs = h('div.sheet-tabs');

    const search = h('input.sheet-search-input', { type: 'search', placeholder: 'Поиск по таблице' });
    const searchBox = h('div.sheet-search', icon('search'), search,
      h('span.sheet-search-count'));
    const counter = searchBox.querySelector('.sheet-search-count');

    function sheet() { return data.sheets[active]; }

    /** Ячейки, накрытые объединением, рисовать не нужно */
    function coveredSet(s) {
      const covered = new Set();
      (s.merges || []).forEach(m => {
        for (let r = m.r; r < m.r + m.rs; r++)
          for (let c = m.c; c < m.c + m.cs; c++)
            if (r !== m.r || c !== m.c) covered.add(r + ':' + c);
      });
      return covered;
    }

    function draw() {
      const s = sheet();
      const covered = coveredSet(s);
      const merges = new Map();
      (s.merges || []).forEach(m => merges.set(m.r + ':' + m.c, m));

      const table = h('table.sheet-table');
      const head = h('tr', h('th.sheet-corner'));
      s.cols.forEach((col, i) => {
        const w = colWidth(col);
        const th = h('th.sheet-col-head', { text: data.letters[i] || '' });
        if (!w) th.classList.add('is-hidden'); else th.style.width = w + 'px';
        head.append(th);
      });
      table.append(h('thead', head));

      const body = h('tbody');
      s.rows.forEach((row, r) => {
        const tr = h('tr', h('th.sheet-row-head', { text: String(r + 1) }));
        row.forEach((cell, c) => {
          if (covered.has(r + ':' + c)) return;
          const td = h('td.sheet-cell', { text: cell.v || '' });
          const m = merges.get(r + ':' + c);
          if (m) { if (m.rs > 1) td.rowSpan = m.rs; if (m.cs > 1) td.colSpan = m.cs; }
          if (cell.b) td.classList.add('is-bold');
          if (cell.i) td.classList.add('is-italic');
          if (cell.w) td.classList.add('is-wrap');
          if (cell.a) td.classList.add('al-' + cell.a);
          else if (cell.n) td.classList.add('al-right');
          if (cell.bg) td.style.background = cell.bg;
          if (cell.fg) td.style.color = cell.fg;
          if (cell.u || cell.s) td.style.textDecoration = ((cell.u ? 'underline ' : '') + (cell.s ? 'line-through' : '')).trim();
          if (cell.fs) td.style.fontSize = (cell.fs * 100) + '%';
          if (cell.bd) ['Top', 'Right', 'Bottom', 'Left'].forEach((side, k) => { if (cell.bd[k]) td.style['border' + side] = cell.bd[k]; });
          if (!colWidth(s.cols[c] || {})) td.classList.add('is-hidden');
          td.dataset.r = r; td.dataset.c = c;
          tr.append(td);
        });
        body.append(tr);
      });
      table.append(body);

      Kov.UI.clear(grid);
      grid.append(table);
      grid.scrollTop = 0; grid.scrollLeft = 0;

      // Закрепление из книги: шапка и первые столбцы не уезжают.
      // Высоту полосы с буквами меряем на месте — на глаз она не угадывается,
      // и закреплённая строка наезжала на соседнюю.
      grid.classList.toggle('freeze-row', !!(s.freeze && s.freeze.r));
      table.classList.toggle('freeze-col', !!(s.freeze && s.freeze.c));
      requestAnimationFrame(() => {
        const head = table.querySelector('.sheet-col-head');
        if (head) grid.style.setProperty('--sheet-head-h', head.getBoundingClientRect().height + 'px');
      });

      const size = s.rows.length + ' × ' + s.cols.length;
      Kov.UI.clear(status);
      status.append(h('span', 'Строк и столбцов: ' + size));
      if (s.truncated) status.append(h('span.sheet-warn', 'показана первая часть — файл слишком большой'));
      pick(null);
      runSearch();
    }

    /** Значение выделенной ячейки целиком: в узкий столбец оно не помещается */
    const picked = h('div.sheet-picked');
    function pick(td) {
      grid.querySelectorAll('.sheet-cell.is-picked').forEach(x => x.classList.remove('is-picked'));
      Kov.UI.clear(picked);
      if (!td) { picked.hidden = true; return; }
      td.classList.add('is-picked');
      const addr = (data.letters[+td.dataset.c] || '') + (+td.dataset.r + 1);
      picked.hidden = false;
      picked.append(h('span.sheet-addr', addr), h('span.sheet-value', { text: td.textContent }));
    }
    grid.addEventListener('click', e => {
      const td = e.target.closest && e.target.closest('.sheet-cell');
      if (td) pick(td);
    });

    function runSearch() {
      const q = search.value.trim().toLowerCase();
      grid.querySelectorAll('.sheet-cell.is-hit').forEach(x => x.classList.remove('is-hit'));
      found = []; foundAt = -1;
      if (!q) { counter.textContent = ''; return; }
      grid.querySelectorAll('.sheet-cell').forEach(td => {
        if (td.textContent.toLowerCase().includes(q)) { td.classList.add('is-hit'); found.push(td); }
      });
      counter.textContent = found.length ? ('найдено: ' + found.length) : 'ничего не найдено';
      if (found.length) step(0);
    }
    function step(delta) {
      if (!found.length) return;
      foundAt = (foundAt + delta + found.length) % found.length;
      const td = found[foundAt];
      td.scrollIntoView({ block: 'center', inline: 'center', behavior: 'smooth' });
      pick(td);
      counter.textContent = (foundAt + 1) + ' из ' + found.length;
    }
    search.addEventListener('input', runSearch);
    search.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); step(e.shiftKey ? -1 : 1); }
    });

    data.sheets.forEach((s, i) => {
      const tab = h('button.sheet-tab', { type: 'button', text: s.name, title: s.name });
      tab.onclick = () => {
        active = i;
        tabs.querySelectorAll('.sheet-tab').forEach(t => t.classList.remove('is-active'));
        tab.classList.add('is-active');
        draw();
      };
      if (i === 0) tab.classList.add('is-active');
      tabs.append(tab);
    });

    const root = h('div.sheet-view',
      h('div.sheet-bar', searchBox, opts.actions || null),
      data.sheets.length > 1 ? tabs : null,
      grid, picked, status);
    draw();
    return root;
  }

  /**
   * Открывает книгу отдельной вкладкой — так же, как документы.
   *
   * Таблицу листают долго, к ней возвращаются, рядом держат другое окно —
   * окно поверх приложения для этого неудобно. Страница sheet.html лежит на
   * том же сайте и берёт вход из того же хранилища, поэтому передавать в
   * адресе токен не нужно.
   */
  function openTab(cfg) {
    const url = PAGE_URL + '?u=' + encodeURIComponent(cfg.url)
      + '&n=' + encodeURIComponent(cfg.title || 'Таблица')
      + (cfg.downloadUrl ? '&b=' + encodeURIComponent(cfg.downloadUrl) : '');
    // Без строки параметров: с «noopener» window.open возвращает null даже
    // при удачном открытии, и запасной путь срабатывал всегда — вкладка
    // открывалась, а следом за ней окно поверх приложения.
    const win = window.open(url, '_blank');
    if (win) {
      try { win.opener = null; } catch (e) { /* межстраничные ограничения */ }
      return win;
    }
    // Вкладку не пустили (блокировщик всплывающих окон) — показываем окном
    open(cfg);
    return null;
  }

  /** Запасной путь: книга окном поверх приложения */
  async function open(cfg) {
    const box = Kov.UI.modal({
      title: cfg.title || 'Таблица',
      glyph: 'table',
      wide: true,
      body: () => Kov.UI.bigLoading('Читаем таблицу…'),
    });
    box.el.classList.add('sheet-modal');
    try {
      const data = await Kov.Api.get(cfg.url);
      Kov.UI.clear(box.body);
      box.body.append(render(data, {
        actions: cfg.onDownload
          ? button('Скачать', { icon: 'download', onClick: cfg.onDownload })
          : null,
      }));
    } catch (e) {
      Kov.UI.clear(box.body);
      box.body.append(h('div.sheet-fail',
        h('p', 'Показать таблицу ячейками не получилось: ' + Kov.UI.errorText(e)),
        cfg.onFallback ? button('Открыть как документ', { onClick: () => { box.close(); cfg.onFallback(); } }) : null,
        cfg.onDownload ? button('Скачать файл', { icon: 'download', onClick: cfg.onDownload }) : null));
    }
    return box;
  }

  return { isSheet, open, openTab, render };
})();
