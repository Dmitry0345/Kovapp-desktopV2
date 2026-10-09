/* Тема ставится до загрузки стилей: иначе тёмный интерфейс на миг мигает белым.
   Отдельным файлом, а не внутри страницы, — так работает строгая политика
   содержимого, запрещающая встроенные скрипты. */
(function () {
  try {
    var t = localStorage.getItem('kov.theme') || 'light';
    if (t === 'auto') t = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    if (t !== 'light' && t !== 'dark') t = 'light';
    document.documentElement.dataset.theme = t;
    if (t === 'dark') {
      var m = document.querySelector('meta[name="theme-color"]');
      if (m) m.setAttribute('content', '#1C1C1E');   // строка браузера — цвет верхней панели тёмной темы
    }
  } catch (e) { document.documentElement.dataset.theme = 'light'; }
})();
