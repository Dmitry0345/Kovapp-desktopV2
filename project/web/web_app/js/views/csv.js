/* Файлы CSV (руководство): импорт/экспорт пользователей и организаций. */
window.Kov = window.Kov || {};
Kov.Views = Kov.Views || {};

Kov.Views.csv = (function () {
  const { h, button, sectionHeader } = Kov.UI;

  function view(section) {
    let mode = 'import', type = 'users', file = null;
    const modeSeg = Kov.UI.segmented([['import', 'Импорт', 'upload'], ['export', 'Экспорт', 'download']], mode, k => { mode = k; sync(); });
    const typeSeg = Kov.UI.segmented([['users', 'Пользователи', 'users'], ['organizations', 'Организации', 'building']], type, k => { type = k; });
    const input = h('input', { type: 'file', accept: '.csv,text/csv', hidden: true, onchange: e => { file = e.target.files[0] || null; dropText.textContent = file ? file.name : 'Перетащите файл сюда'; } });
    const dropText = h('div', 'Перетащите файл сюда');
    const drop = h('div.csv-drop', { onclick: () => input.click() }, Kov.UI.icon('upload'), dropText, h('div.small.muted', 'или нажмите, чтобы выбрать'));
    drop.addEventListener('dragover', e => { e.preventDefault(); drop.style.borderColor = 'var(--accent)'; });
    drop.addEventListener('dragleave', () => { drop.style.borderColor = ''; });
    drop.addEventListener('drop', e => { e.preventDefault(); drop.style.borderColor = ''; file = e.dataTransfer.files[0] || null; dropText.textContent = file ? file.name : 'Перетащите файл сюда'; });
    const pick = button('Выбрать файл', { kind: 'ghost', icon: 'folder', onClick: () => input.click() });
    const importBox = h('div', { style: { display: 'flex', flexDirection: 'column', gap: '.6rem' } }, drop, input);
    const hint = h('div.small.muted', 'Импорт добавляет записи из файла; существующие обновляются по совпадению. Экспорт выгружает всё содержимое раздела одним файлом CSV в кодировке UTF-8.');
    const result = h('div.small.muted');
    const run = button('Выполнить', { icon: 'check', onClick: async () => {
      result.textContent = '';
      try {
        if (mode === 'import') {
          if (!file) return Kov.UI.toast('Выберите CSV-файл', 'warning');
          const form = new FormData(); form.append('file', file);
          const r = await Kov.Api.post('csv/' + type + '/import', { form });
          result.textContent = typeof r === 'object' ? Object.values(r).join(' ') : String(r);
          Kov.UI.toast('Импорт завершён', 'success');
        } else {
          const res = await fetch(Kov.Api.url('csv/' + type + '/export'), { headers: { Authorization: 'Bearer ' + Kov.Api.state.token } });
          if (!res.ok) throw new Error('HTTP ' + res.status);
          const blob = await res.blob();
          const a = h('a', { href: URL.createObjectURL(blob), download: (type === 'users' ? 'users' : 'organizations') + '.csv' });
          document.body.append(a); a.click(); a.remove();
          setTimeout(() => URL.revokeObjectURL(a.href), 5000);
        }
      } catch (e) { Kov.UI.toast('Ошибка: ' + Kov.UI.errorText(e), 'error'); }
    } });
    function sync() { importBox.hidden = mode !== 'import'; pick.hidden = mode !== 'import'; run.replaceChildren(Kov.UI.icon(mode === 'import' ? 'upload' : 'download'), mode === 'import' ? 'Импортировать' : 'Скачать CSV'); }
    sync();
    section.append(h('div.card', { style: { display: 'flex', flexDirection: 'column', gap: '.8rem' } },
        h('div', { style: { fontWeight: 700 } }, 'Режим'), modeSeg, h('div', { style: { fontWeight: 700 } }, 'Тип данных'), typeSeg, importBox, h('div.btn-row.csv-actions', pick, run), result));
  }

  return { view };
})();
