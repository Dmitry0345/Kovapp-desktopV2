/* Скачивание файлов с настоящим прогрессом.
 *
 * Обычная ссылка <a download> не показывает, сколько уже скачано. Здесь файл
 * забирается через fetch потоком, проценты считаются по Content-Length, а в
 * правом нижнем углу висит небольшая панель со всеми текущими загрузками.
 * Когда файл получен целиком, он отдаётся браузеру на сохранение — ровно в
 * ту секунду, когда им можно пользоваться. Если размер заранее неизвестен,
 * показывается объём, а не проценты. Очень большие файлы (больше лимита
 * памяти вкладки) отдаются прямой ссылкой, как раньше.
 */
window.Kov = window.Kov || {};

Kov.Downloads = (function () {
  const { h, icon } = Kov.UI;
  const MEMORY_LIMIT = 700 * 1024 * 1024;   // выше этого — прямая ссылка, не через память
  let panel = null, list = null;
  const jobs = new Map();
  let seq = 0;

  function ensurePanel() {
    if (panel) return;
    list = h('div.dl-list');
    panel = h('div.dl-panel', { hidden: true },
      h('div.dl-head', icon('download'), h('span', 'Загрузки'), h('span.spacer'),
        h('button.dl-clear', { type: 'button', title: 'Скрыть завершённые', onclick: clearDone }, '×')),
      list);
    document.body.append(panel);
  }

  function clearDone() {
    [...jobs.values()].filter(j => j.done).forEach(j => { j.el.remove(); jobs.delete(j.id); });
    if (!jobs.size) panel.hidden = true;
  }

  function fmtSize(bytes) {
    if (!bytes && bytes !== 0) return '';
    if (bytes < 1024) return bytes + ' Б';
    if (bytes < 1048576) return (bytes / 1024).toFixed(0) + ' КБ';
    if (bytes < 1073741824) return (bytes / 1048576).toFixed(1) + ' МБ';
    return (bytes / 1073741824).toFixed(2) + ' ГБ';
  }

  function row(job) {
    const bar = h('div.bar');
    const status = h('span.s', 'Подготовка…');
    const cancel = h('button.dl-cancel', { type: 'button', title: 'Отменить', onclick: () => job.abort() }, '×');
    const el = h('div.dl-item',
      h('div.dl-row', icon('file'), h('span.t', { title: job.name }, job.name), cancel),
      h('div.track', bar), status);
    job.el = el; job.bar = bar; job.status = status; job.cancelBtn = cancel;
    return el;
  }

  function withToken(url, token) {
    try {
      const u = new URL(url, location.href);
      if (!u.searchParams.has('token') || !token) return url;
      u.searchParams.set('token', token);
      return u.toString();
    } catch (e) { return url; }
  }

  async function reason(res) {
    let detail = '';
    try {
      const data = await res.clone().json();
      if (data && typeof data.detail === 'string') detail = data.detail;
    } catch (e) { /* */ }
    return detail || ('Сервер ответил ' + res.status);
  }

  function directLink(url, name) {
    const a = h('a', { href: url, download: name || '' });
    document.body.append(a); a.click(); a.remove();
  }

  /**
   * download(url, name) — скачать с прогрессом. Возвращает промис, который
   * завершается, когда файл отдан браузеру на сохранение.
   */
  async function download(url, name) {
    ensurePanel();
    const job = { id: ++seq, name: name || 'файл', done: false, controller: new AbortController() };
    job.abort = () => { job.controller.abort(); };
    jobs.set(job.id, job);
    list.prepend(row(job));
    panel.hidden = false;

    try {
      let res = await fetch(url, { signal: job.controller.signal });
      if (res.status === 401 && Kov.Api && Kov.Api.ensureFileToken) {
        await Kov.Api.ensureFileToken(true);
        const fresh = withToken(url, Kov.Api.mediaToken());
        if (fresh !== url) {
          url = fresh;
          res = await fetch(url, { signal: job.controller.signal });
        }
      }
      if (!res.ok) throw new Error(await reason(res));
      // у сжатых при выдаче файлов Content-Length — размер gzip, исходный приходит отдельно
      const total = Number(res.headers.get('x-original-length') || (res.headers.get('content-encoding') ? 0 : res.headers.get('content-length')) || 0);
      if (total > MEMORY_LIMIT || !res.body) {
        // слишком большой, чтобы держать в памяти вкладки — отдаём напрямую
        job.controller.abort();
        job.status.textContent = 'Открыто браузером';
        job.bar.style.width = '100%';
        directLink(url, name);
        return finish(job);
      }
      const reader = res.body.getReader();
      const chunks = [];
      let received = 0;
      let lastPaint = 0;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        chunks.push(value);
        received += value.length;
        const now = performance.now();
        if (now - lastPaint > 80) {
          lastPaint = now;
          if (total) {
            const p = received / total;
            job.bar.style.width = (p * 100).toFixed(1) + '%';
            job.status.textContent = Math.round(p * 100) + '% · ' + fmtSize(received) + ' из ' + fmtSize(total);
          } else {
            job.bar.style.width = '60%';
            job.status.textContent = 'Скачано ' + fmtSize(received);
          }
        }
      }
      const blob = new Blob(chunks, { type: res.headers.get('content-type') || 'application/octet-stream' });
      const objectUrl = URL.createObjectURL(blob);
      directLink(objectUrl, name);
      setTimeout(() => URL.revokeObjectURL(objectUrl), 60000);
      job.bar.style.width = '100%';
      job.status.textContent = 'Готово · ' + fmtSize(received);
      job.el.classList.add('done');
      return finish(job);
    } catch (e) {
      if (e && e.name === 'AbortError') { job.status.textContent = 'Отменено'; }
      else {
        job.status.textContent = 'Ошибка: ' + ((e && e.message) || 'не удалось скачать');
        job.el.classList.add('error');
      }
      return finish(job, true);
    }
  }

  function finish(job, failed) {
    job.done = true;
    job.cancelBtn.hidden = true;
    // завершённые убираются сами, ошибки остаются на виду
    if (!failed) setTimeout(() => { if (jobs.has(job.id)) { job.el.remove(); jobs.delete(job.id); if (!jobs.size) panel.hidden = true; } }, 6000);
  }

  return { download };
})();
