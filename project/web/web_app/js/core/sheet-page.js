/* Страница просмотра таблицы (sheet.html).
 *
 * Вынесена отдельным файлом не для красоты: политика содержимого сервера
 * разрешает только script-src 'self', и встроенный в разметку сценарий
 * браузер молча не выполняет — страница оставалась пустой.
 *
 * Раньше книга показывалась окном поверх приложения. Документы открываются
 * вкладкой, и таблице разумно вести себя так же: её листают долго, к ней
 * возвращаются, рядом держат другую вкладку. Адрес разбора и название файла
 * приходят в строке запроса, вход берётся из того же хранилища, что и в
 * приложении, — вкладка открывается на том же сайте.
 */
(function () {
  const { h, icon, button } = Kov.UI;
  const params = new URLSearchParams(location.search);
  const path = params.get('u') || '';
  const name = params.get('n') || 'Таблица';
  const back = params.get('b') || '';

  document.title = name;
  const head = h('div.sheet-page-head', icon('table'), h('span.sheet-page-name', name));
  const body = h('div.sheet-page-body');
  document.body.append(head, body);

  function fail(text) {
    Kov.UI.clear(body);
    body.append(h('div.sheet-page-fail',
      h('p', text),
      back ? button('Скачать файл', { icon: 'download', onClick: () => { location.href = back; } }) : null));
  }

  if (!Kov.Api.restore()) {
    fail('Не удалось подтвердить вход. Откройте приложение и попробуйте ещё раз.');
    return;
  }
  if (!path) {
    fail('Не указано, какую таблицу открывать.');
    return;
  }

  head.append(button('Скачать', { icon: 'download', kind: 'ghost',
    onClick: () => { if (back) location.href = back; } }));

  body.append(Kov.UI.bigLoading('Читаем таблицу…'));
  // книгу разбирает само устройство; сервер — запасной путь
  const load = back && Kov.XlsxReader && Kov.XlsxReader.supports(name)
    ? Kov.XlsxReader.fromUrl(back, name).catch(e => { console.warn('Таблица не разобрана на устройстве:', e); return Kov.Api.get(path); })
    : Kov.Api.get(path);
  load
    .then(data => {
      Kov.UI.clear(body);
      body.append(Kov.SheetView.render(data, {}));
    })
    .catch(e => fail('Показать таблицу ячейками не получилось: ' + Kov.UI.errorText(e)));
})();
