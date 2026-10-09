/* Страница просмотра документа (doc.html): PDF, Word и PowerPoint показывает само устройство.
 * Старые форматы (.doc, .ppt, .odt, .rtf) и неудачи — через PDF, который готовит сервер. */
(function () {
  const { h, button } = Kov.UI;
  const params = new URLSearchParams(location.search);
  const src = params.get('u') || '', name = params.get('n') || 'Документ', fallback = params.get('v') || '';
  const ext = ((name.match(/\.[^.]+$/) || [''])[0]).toLowerCase();
  document.title = name;
  const tools = h('div.doc-page-tools');
  const head = h('div.doc-page-head', h('span.doc-page-name', name), tools);
  const body = h('div.doc-page-body');
  document.body.append(head, body);
  if (src) tools.append(button('Скачать', { icon: 'download', kind: 'ghost', onClick: () => { location.href = src; } }));
  function fail(text) {
    Kov.UI.clear(body);
    body.append(h('div.doc-page-fail', h('p', text), src ? button('Скачать файл', { icon: 'download', onClick: () => { location.href = src; } }) : null));
  }
  async function bytes(url) {
    const res = await fetch(url, { credentials: 'same-origin' });
    if (!res.ok) throw new Error('сервер ответил ' + res.status);
    return new Uint8Array(await res.arrayBuffer());
  }
  async function showPdf(url, view) {
    const doc = await Kov.PdfView.render(await bytes(url), view);
    const label = h('span.doc-zoom', '100%');
    const set = z => { label.textContent = Math.round(doc.setZoom(z) * 100) + '%'; };
    const zoomBtn = (sign, title, step) => {
      const b = button(sign, { kind: 'ghost', title, onClick: () => set(doc.zoom + step) });
      b.classList.add('doc-zoom-btn'); b.setAttribute('aria-label', title);
      return b;
    };
    tools.prepend(zoomBtn('−', 'Мельче', -0.25), label, zoomBtn('+', 'Крупнее', 0.25));
    return doc;
  }
  async function show() {
    if (!src) return fail('Не указано, какой документ открывать.');
    // вид вставляется в страницу до отрисовки: слайдам и страницам сразу известна ширина
    const loading = Kov.UI.bigLoading('Открываем документ…');
    const view = h('div.doc-view');
    body.append(loading, view);
    try {
      if (ext === '.pdf') await showPdf(src, view);
      else if (/^\.(docx|docm|dotx)$/.test(ext)) { view.classList.add('is-docx'); await Kov.DocxView.render(await bytes(src), view); }
      else if (/^\.(pptx|pptm|ppsx|potx)$/.test(ext)) { view.classList.add('is-pptx'); await Kov.PptxView.render(await bytes(src), view); }
      else throw new Error('этот формат показывает сервер');
      loading.remove();
    } catch (e) {
      console.warn('Документ на устройстве не показан:', e);
      if (!fallback) return fail('Показать документ не получилось: ' + (e && e.message || e));
      try {
        const alt = h('div.doc-view'), wait = Kov.UI.bigLoading('Готовим документ на сервере…');
        Kov.UI.clear(body);
        body.append(wait, alt);
        await showPdf(fallback, alt);
        wait.remove();
      } catch (e2) { fail('Показать документ не получилось: ' + (e2 && e2.message || e2)); }
    }
  }
  show();
})();
