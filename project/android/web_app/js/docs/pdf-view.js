/* PDF на устройстве через PDF.js: страницы рисуются по мере прокрутки, дальние освобождаются */
(function (root) {
  const Kov = root.Kov = root.Kov || {};
  const SRC = document.currentScript && document.currentScript.src;
  const LIB = (SRC ? SRC.replace(/js\/docs\/pdf-view\.js.*$/, '') : '') + 'vendor/pdfjs/';
  let lib = null;
  function load() {
    if (!lib) lib = import(LIB + 'pdf.min.mjs').then(m => { m.GlobalWorkerOptions.workerSrc = LIB + 'pdf.worker.min.mjs'; return m; });
    return lib;
  }
  async function open(data) {
    const pdfjs = await load();
    return pdfjs.getDocument({ data, cMapUrl: LIB + 'cmaps/', cMapPacked: true, standardFontDataUrl: LIB + 'standard_fonts/',
      wasmUrl: LIB + 'wasm/', isEvalSupported: false, enableXfa: false }).promise;
  }
  // на слабых телефонах держим меньше пикселей в памяти
  const MAX_PIXELS = navigator.deviceMemory && navigator.deviceMemory <= 2 ? 4e6 : 12e6;

  async function render(data, host) {
    const doc = await open(data);
    const first = (await doc.getPage(1)).getViewport({ scale: 1 });
    const list = document.createElement('div');
    list.className = 'pdf-pages';
    host.append(list);
    let zoom = 1;
    const pages = [];
    for (let n = 1; n <= doc.numPages; n++) {
      const box = document.createElement('div');
      box.className = 'pdf-page';
      box.dataset.n = n;
      box.style.aspectRatio = first.width + ' / ' + first.height;
      list.append(box);
      pages.push({ n, box, canvas: null, zoom: 0, busy: false });
    }
    async function draw(p) {
      if (p.busy || (p.canvas && p.zoom === zoom)) return;
      p.busy = true;
      try {
        const page = await doc.getPage(p.n);
        const base = page.getViewport({ scale: 1 });
        p.box.style.aspectRatio = base.width + ' / ' + base.height;
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        let scale = (p.box.clientWidth || 800) / base.width * dpr;
        if (base.width * base.height * scale * scale > MAX_PIXELS) scale = Math.sqrt(MAX_PIXELS / (base.width * base.height));
        const vp = page.getViewport({ scale });
        const c = document.createElement('canvas');
        c.width = Math.floor(vp.width); c.height = Math.floor(vp.height);
        const ctx = c.getContext('2d');
        ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, c.width, c.height);
        await page.render({ canvasContext: ctx, canvas: c, viewport: vp }).promise;
        drop(p);
        p.canvas = c; p.zoom = zoom; p.box.append(c);
        page.cleanup();
      } catch (e) { console.warn('PDF: страница', p.n, e); } finally { p.busy = false; }
    }
    function drop(p) { if (p.canvas) { p.canvas.width = p.canvas.height = 0; p.canvas.remove(); p.canvas = null; } }
    const io = new IntersectionObserver(items => items.forEach(e => {
      const p = pages[+e.target.dataset.n - 1];
      if (e.isIntersecting) draw(p); else drop(p);
    }), { rootMargin: '1500px 0px' });
    pages.forEach(p => io.observe(p.box));
    return {
      pages: doc.numPages,
      setZoom(z) { zoom = Math.max(0.5, Math.min(3, z)); list.style.setProperty('--pdf-zoom', zoom); pages.forEach(p => { if (p.canvas) draw(p); }); return zoom; },
      get zoom() { return zoom; },
      release() { io.disconnect(); doc.destroy(); },
    };
  }

  /** Миниатюра первой страницы: JPEG до 320 px на белом фоне и ThumbHash */
  async function thumbnail(blob, side) {
    side = side || 320;
    const doc = await open(new Uint8Array(await blob.arrayBuffer()));
    try {
      const page = await doc.getPage(1);
      const base = page.getViewport({ scale: 1 });
      const vp = page.getViewport({ scale: side / Math.max(base.width, base.height) });
      const c = document.createElement('canvas');
      c.width = Math.max(1, Math.round(vp.width)); c.height = Math.max(1, Math.round(vp.height));
      const ctx = c.getContext('2d');
      ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, c.width, c.height);
      await page.render({ canvasContext: ctx, canvas: c, viewport: vp }).promise;
      const jpeg = await new Promise(r => c.toBlob(r, 'image/jpeg', 0.6));
      let hash = null;
      if (Kov.ThumbHash) {
        const k = 100 / Math.max(c.width, c.height), s = document.createElement('canvas');
        s.width = Math.max(1, Math.round(c.width * k)); s.height = Math.max(1, Math.round(c.height * k));
        s.getContext('2d').drawImage(c, 0, 0, s.width, s.height);
        hash = Kov.ThumbHash.toBase64(Kov.ThumbHash.encode(s.width, s.height, s.getContext('2d').getImageData(0, 0, s.width, s.height).data));
      }
      return { blob: jpeg, hash, width: Math.round(base.width), height: Math.round(base.height), pages: doc.numPages };
    } finally { doc.destroy(); }
  }
  Kov.PdfView = { render, thumbnail, load };
})(typeof self !== 'undefined' ? self : this);
