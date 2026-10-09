/* Кадрирование фотографии под кружок.
 *
 * Раньше выбранное фото уходило на сервер как есть: человек не видел, что
 * попадёт в кружок, и часто получал обрезанную макушку. Здесь он двигает
 * снимок, приближает его колесом или щипком и сразу видит итог — ровно тот
 * кружок, который увидят остальные.
 *
 * Приближение ограничено: меньше единицы нельзя (иначе в кружок попали бы
 * пустые поля), больше пяти бессмысленно — снимок рассыпается на пиксели.
 * Результат отдаётся готовым квадратом: сервер получает уже кадрированное,
 * и на всех экранах кружок выглядит одинаково.
 */
window.Kov = window.Kov || {};

Kov.PhotoCrop = (function () {
  const { h, icon, button } = Kov.UI;

  const MIN_ZOOM = 1;
  const MAX_ZOOM = 5;
  const OUT_SIZE = 512;          // сторона готового квадрата
  const MAX_INPUT = 12 * 1024 * 1024;

  function readImage(file) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => resolve({ img, url });
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('Не удалось прочитать изображение')); };
      img.src = url;
    });
  }

  /**
   * Открывает окно кадрирования. Возвращает обещание: Blob готового квадрата
   * или null, если человек отказался.
   *
   * cfg: { file, title, round (кружок или квадрат), outSize }
   */
  async function open(cfg) {
    const file = cfg && cfg.file;
    if (!file) return null;
    if (file.size > MAX_INPUT) {
      Kov.UI.toast('Фото больше 12 МБ — выберите поменьше', 'warning');
      return null;
    }
    let source;
    try { source = await readImage(file); }
    catch (e) { Kov.UI.toast(Kov.UI.errorText(e), 'error'); return null; }

    const round = cfg.round !== false;
    const outSize = cfg.outSize || OUT_SIZE;
    const stage = h('div.crop-stage' + (round ? '.is-round' : ''));
    const canvas = h('canvas.crop-canvas');
    const mask = h('div.crop-mask');
    stage.append(canvas, mask);

    const zoomIn = button('', { kind: 'ghost', icon: 'plus', title: 'Приблизить' });
    const zoomOut = button('', { kind: 'ghost', icon: 'minus', title: 'Отдалить' });
    const range = h('input.crop-range', { type: 'range', min: String(MIN_ZOOM * 100),
      max: String(MAX_ZOOM * 100), value: '100', 'aria-label': 'Приближение' });
    const reset = button('Сбросить', { kind: 'ghost', icon: 'refresh', title: 'Вернуть исходное положение' });

    // Состояние: сдвиг центра снимка и приближение
    const view = { zoom: 1, x: 0, y: 0, size: 0, ratio: 1 };

    function fit() {
      const box = stage.getBoundingClientRect();
      view.size = Math.round(Math.min(box.width, box.height)) || 260;
      view.ratio = window.devicePixelRatio || 1;
      canvas.width = view.size * view.ratio;
      canvas.height = view.size * view.ratio;
      canvas.style.width = view.size + 'px';
      canvas.style.height = view.size + 'px';
      draw();
    }

    /** Наименьший масштаб, при котором снимок закрывает кадр целиком */
    function baseScale() {
      return Math.max(view.size / source.img.naturalWidth, view.size / source.img.naturalHeight);
    }

    /** Не даём утащить снимок так, чтобы в кадре появилось пустое поле */
    function clamp() {
      const scale = baseScale() * view.zoom;
      const w = source.img.naturalWidth * scale;
      const h2 = source.img.naturalHeight * scale;
      const maxX = Math.max(0, (w - view.size) / 2);
      const maxY = Math.max(0, (h2 - view.size) / 2);
      view.x = Math.min(maxX, Math.max(-maxX, view.x));
      view.y = Math.min(maxY, Math.max(-maxY, view.y));
    }

    function draw() {
      clamp();
      const ctx = canvas.getContext('2d');
      const scale = baseScale() * view.zoom * view.ratio;
      const w = source.img.naturalWidth * scale;
      const h2 = source.img.naturalHeight * scale;
      const side = view.size * view.ratio;
      ctx.clearRect(0, 0, side, side);
      ctx.imageSmoothingQuality = 'high';
      ctx.drawImage(source.img,
        (side - w) / 2 + view.x * view.ratio,
        (side - h2) / 2 + view.y * view.ratio, w, h2);
    }

    function setZoom(next, anchor) {
      const before = view.zoom;
      view.zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, next));
      if (anchor && before) {
        // Приближаем к точке под пальцем, а не к середине
        const k = view.zoom / before;
        view.x = anchor.x + (view.x - anchor.x) * k;
        view.y = anchor.y + (view.y - anchor.y) * k;
      }
      range.value = String(Math.round(view.zoom * 100));
      paintRange();
      draw();
    }

    // ── перетаскивание пальцем и мышью ──
    let drag = null;
    const pointers = new Map();
    let pinch = null;

    stage.addEventListener('pointerdown', e => {
      stage.setPointerCapture(e.pointerId);
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pointers.size === 2) {
        const [a, b] = [...pointers.values()];
        pinch = { dist: Math.hypot(a.x - b.x, a.y - b.y), zoom: view.zoom };
        drag = null;
      } else {
        drag = { x: e.clientX - view.x, y: e.clientY - view.y };
      }
      stage.classList.add('is-dragging');
    });
    stage.addEventListener('pointermove', e => {
      if (!pointers.has(e.pointerId)) return;
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pinch && pointers.size === 2) {
        const [a, b] = [...pointers.values()];
        const dist = Math.hypot(a.x - b.x, a.y - b.y);
        if (pinch.dist > 0) setZoom(pinch.zoom * (dist / pinch.dist));
        return;
      }
      if (!drag) return;
      view.x = e.clientX - drag.x;
      view.y = e.clientY - drag.y;
      draw();
    });
    const release = e => {
      pointers.delete(e.pointerId);
      if (pointers.size < 2) pinch = null;
      if (!pointers.size) { drag = null; stage.classList.remove('is-dragging'); }
    };
    stage.addEventListener('pointerup', release);
    stage.addEventListener('pointercancel', release);
    stage.addEventListener('wheel', e => {
      e.preventDefault();
      const box = stage.getBoundingClientRect();
      setZoom(view.zoom * (e.deltaY < 0 ? 1.12 : 1 / 1.12), {
        x: view.x - (e.clientX - box.left - box.width / 2),
        y: view.y - (e.clientY - box.top - box.height / 2),
      });
    }, { passive: false });

    /** Закрашенная часть дорожки: обычный ползунок её не показывает */
    function paintRange() {
      const share = (Number(range.value) - MIN_ZOOM * 100) / ((MAX_ZOOM - MIN_ZOOM) * 100) * 100;
      range.style.setProperty('--fill', share + '%');
    }
    paintRange();
    range.addEventListener('input', () => setZoom(Number(range.value) / 100));
    zoomIn.onclick = () => setZoom(view.zoom * 1.2);
    zoomOut.onclick = () => setZoom(view.zoom / 1.2);
    reset.onclick = () => { view.x = 0; view.y = 0; setZoom(1); };

    /** Готовый квадрат: то же, что человек видит в кадре, только крупнее */
    function toBlob() {
      const out = document.createElement('canvas');
      out.width = outSize; out.height = outSize;
      const ctx = out.getContext('2d');
      ctx.imageSmoothingQuality = 'high';
      const k = outSize / view.size;
      const scale = baseScale() * view.zoom * k;
      const w = source.img.naturalWidth * scale;
      const h2 = source.img.naturalHeight * scale;
      ctx.drawImage(source.img, (outSize - w) / 2 + view.x * k,
        (outSize - h2) / 2 + view.y * k, w, h2);
      return new Promise(resolve => out.toBlob(b => resolve(b), 'image/jpeg', 0.92));
    }

    return new Promise(resolve => {
      let done = false;
      const finish = value => {
        if (done) return;
        done = true;
        URL.revokeObjectURL(source.url);
        resolve(value);
      };
      const box = Kov.UI.modal({
        title: cfg.title || 'Кадрирование фото', glyph: 'crop',
        body: el => el.append(h('div.crop-box',
          h('p.crop-hint', icon('info'),
            h('span', 'Двигайте снимок и приближайте — в кружке будет ровно то, что видно')),
          stage,
          h('div.crop-tools', h('div.crop-zoom', zoomOut, range, zoomIn), reset))),
        buttons: [
          { text: 'Отмена', kind: 'ghost', onClick: close => { finish(null); close(); } },
          { text: 'Сохранить', icon: 'check', onClick: async close => {
            // Результат отдаём до закрытия: закрытие зовёт onClose, а тот
            // считает окно отменённым — и готовый кадр терялся
            finish(await toBlob());
            close();
          } },
        ],
        onClose: () => finish(null),
      });
      box.el.classList.add('crop-modal');
      // Размер сцены известен только после вставки в страницу
      requestAnimationFrame(() => { fit(); });
      window.addEventListener('resize', fit);
    });
  }

  /**
   * Полный путь: выбрать файл → кадрировать → отдать готовый File.
   * Возвращает File или null, если человек передумал.
   */
  function pick(cfg) {
    return new Promise(resolve => {
      const crop = async file => {
        if (!file) return resolve(null);
        const blob = await open(Object.assign({ file }, cfg || {}));
        if (!blob) return resolve(null);
        const name = String(file.name || 'photo').replace(/\.[^.]+$/, '') + '.jpg';
        resolve(new File([blob], name, { type: 'image/jpeg' }));
      };
      // телефон: шторка вложений в режиме «одно фото» (камера, последние снимки), как в чате; компьютер — обычный выбор
      if (Kov.AttachSheet && Kov.UI.touchUi && Kov.UI.touchUi()) {
        Kov.AttachSheet.open(files => crop(files && files[0]), { single: true, kind: 'image', onCancel: () => resolve(null) });
        return;
      }
      const input = h('input', { type: 'file', accept: 'image/*', hidden: true });
      input.onchange = async () => {
        const file = input.files[0];
        input.remove();
        crop(file);
      };
      document.body.append(input);
      input.click();
    });
  }

  return { open, pick, MIN_ZOOM, MAX_ZOOM };
})();
