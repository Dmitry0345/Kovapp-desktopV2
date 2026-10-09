/* Масштабирование — повторяет scaler.py десктопа: база по размеру экрана
 * (min(ширина/1920, высота/1080)) × множитель пользователя 0.75–1.25.
 * Итог записывается в font-size корня, все размеры в rem. */
window.Kov = window.Kov || {};

Kov.Scale = (function () {
  const MIN = 0.75, MAX = 1.25, BASE_PX = 16;
  const PRESETS = [[0.85, 'Маленький'], [1.0, 'Стандартный'], [1.25, 'Увеличенный']];
  let multiplier = 1.0;

  function isMobile() { return window.matchMedia('(max-width: 760px)').matches; }

  /*
   * Сенсорные планшеты. Компьютерная вёрстка считает масштаб от экрана
   * 1536×864, и на планшете в портретной ориентации (800 px) шрифт падал до
   * 10 px. Там включаем вёрстку телефона: страница рисуется шириной 760 px и
   * растягивается на экран. В альбомной — компьютерная, но от эталона 1280×800.
   */
  const TABLET_WIDTH = 760, TOUCH_REF_W = 1280, TOUCH_REF_H = 800;
  let originalViewport = null;

  function device() {
    const d = window.__kovDevice;                 // симулятор телефона на ПК подставляет свой экран
    if (d && d.width) return d;
    return { touch: !!(window.matchMedia && window.matchMedia('(pointer: coarse)').matches),
             width: screen.width || window.innerWidth, height: screen.height || window.innerHeight };
  }

  function fitTablet() {
    const meta = document.querySelector('meta[name="viewport"]');
    if (!meta) return;
    if (originalViewport === null) originalViewport = meta.getAttribute('content') || '';
    const d = device();
    const tablet = d.touch && d.height >= d.width && Math.min(d.width, d.height) > TABLET_WIDTH;
    // масштаб явно: WebView без «обзорного режима» сам страницу не растягивает
    const want = tablet
      ? originalViewport.replace(/width=device-width\s*,?\s*(initial-scale=[\d.]+\s*,?\s*)?/,
          'width=' + TABLET_WIDTH + ', initial-scale=' + (d.width / TABLET_WIDTH).toFixed(4) + ', ')
      : originalViewport;
    if (meta.getAttribute('content') !== want) meta.setAttribute('content', want);
  }

  /**
   * Пропорциональное масштабирование: за единицу взят экран 1920×1080, всё
   * остальное — та же вёрстка, увеличенная или уменьшенная целиком. Поэтому
   * на ноутбуке 1366×768 и на мониторе 2560×1440 разделы выглядят одинаково,
   * отличается только физический размер. Раньше здесь стоял нижний предел
   * 0.92, из-за него на 1366×768 вёрстка не помещалась и вылезала за экран.
   */
  // Эталон уменьшен на четверть — это делает всю вёрстку на 25% крупнее
  // при том же множителе, каким она была на пресете «Увеличенный».
  const REF_W = 1536, REF_H = 864;
  const DESK_MIN = 0.62, DESK_MAX = 1.8;
  // Компьютерной раскладке нужно не меньше 56 rem по ширине: при сильном
  // увеличении шрифт растёт до этой границы, дальше — раскладка телефона
  const DESK_MIN_REM = 56;
  const ZOOMS = [0.5, 0.67, 0.75, 0.8, 0.9, 1.1, 1.2, 1.25, 1.33, 1.5, 1.7, 1.75, 2, 2.4, 2.5, 3];

  /*
   * Масштаб браузера (Ctrl + / Ctrl −). Раньше база считалась от окна в точках
   * страницы: при 150 % окно «сужалось» в полтора раза, шрифт уменьшался столько
   * же, и текст на экране не рос. Теперь база считается от окна без масштаба.
   * Масштаб видно по отношению внешней ширины окна (точки экрана) к внутренней
   * (точки страницы); высота проверяет, что это не панель разработчика сбоку.
   * Где так не определить, всё остаётся как раньше (масштаб 1).
   */
  function pageZoom() {
    if (isMobile() || device().touch || window.__kovDevice) return 1;
    const ow = window.outerWidth, iw = window.innerWidth, oh = window.outerHeight, ih = window.innerHeight;
    if (!ow || !iw || !oh || !ih) return 1;
    let z = ow / iw;
    if (Math.abs(z - 1) < 0.05) return 1;
    const near = ZOOMS.reduce((a, b) => (Math.abs(b - z) < Math.abs(a - z) ? b : a));
    if (Math.abs(near - z) / near < 0.04) z = near;
    if (z < 0.5 || z > 3) return 1;
    const chrome = oh - ih * z;            // панели браузера над страницей, в точках экрана
    return chrome >= 0 && chrome <= 400 ? z : 1;
  }

  function baseFactor() {
    if (isMobile()) {
      // На телефоне за основу берётся ширина экрана: 390px ≈ 1.0
      return Math.min(Math.max(window.innerWidth / 390, 0.9), 1.15);
    }
    const touch = device().touch;
    const z = pageZoom();
    const base = Math.min(window.innerWidth * z / (touch ? TOUCH_REF_W : REF_W), window.innerHeight * z / (touch ? TOUCH_REF_H : REF_H));
    return Math.min(Math.max(base, DESK_MIN), DESK_MAX);
  }

  function clamp(value) { return Math.min(Math.max(Number(value) || 1, MIN), MAX); }

  function apply() {
    let px = BASE_PX * baseFactor() * multiplier;
    if (!isMobile()) px = Math.min(px, window.innerWidth / DESK_MIN_REM);   // раскладка компьютера помещается по ширине
    document.documentElement.style.fontSize = px.toFixed(2) + 'px';
    document.documentElement.dataset.scale = multiplier.toFixed(2);
    // мессенджер размечен в пикселях — его шрифты берут множитель отсюда
    document.documentElement.style.setProperty('--user-scale', multiplier.toFixed(2));
  }

  function setMultiplier(value, persist) {
    multiplier = clamp(value);
    apply();
    if (persist !== false) {
      try { localStorage.setItem('kov.scale', String(multiplier)); } catch (e) { /* ignore */ }
    }
    return multiplier;
  }

  function init() {
    try { const saved = parseFloat(localStorage.getItem('kov.scale')); if (saved) multiplier = clamp(saved); } catch (e) { /* ignore */ }
    fitTablet();
    apply();
    let timer = null;
    const refit = () => { fitTablet(); apply(); };
    window.addEventListener('resize', () => { clearTimeout(timer); timer = setTimeout(refit, 120); });
    window.addEventListener('orientationchange', () => setTimeout(refit, 200));
  }

  return { init, apply, setMultiplier, getMultiplier: () => multiplier, isMobile, PRESETS, MIN, MAX };
})();
