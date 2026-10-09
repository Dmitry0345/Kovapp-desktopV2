/* Фирменный знак Kovapp: контур со скруглёнными углами, буква K внутри и
 * янтарная дуга в верхнем правом углу. Геометрия взята из макета знака —
 * менять числа нельзя, знак собран по модулю буквы. */
window.Kov = window.Kov || {};

Kov.Logo = Kov.Logo || (function () {
  const NS = 'http://www.w3.org/2000/svg';

  // контур разомкнут в верхнем правом углу — там стоит янтарная дуга
  const RING = 'M124 42V78A26 26 0 0 1 98 104H62A26 26 0 0 1 36 78V42A26 26 0 0 1 62 16H98';
  const CORNER = 'M98 16A26 26 0 0 1 124 42';
  const KAY = 'M95 38L73 60L95 82';

  const ring = cls => '<path class="si ' + (cls || '') + '" stroke-width="8" d="' + RING + '"/>';
  const corner = cls => '<path class="sa ' + (cls || '') + '" stroke-width="8" stroke-linecap="round" d="' + CORNER + '"/>';
  const kay = (cls, knock) => '<g class="' + (cls || '') + '">'
    + '<rect class="' + (knock ? 'knock' : 'ink') + '" x="59" y="36" width="11" height="48" rx="5.5"/>'
    + '<path class="' + (knock ? 'sk' : 'si') + '" stroke-width="11" stroke-linecap="round" stroke-linejoin="round" d="' + KAY + '"/></g>';

  // буквы названия построены по модулю K: та же толщина 11 и скруглённые концы
  function wordPaths(withAccent) {
    const acc = withAccent ? 'sa' : 'si';
    const bowl = (cx, cy) => '<path class="si" stroke-width="11" d="M' + cx + ' ' + (cy - 14.5)
      + 'A14.5 14.5 0 0 1 ' + cx + ' ' + (cy + 14.5) + 'A14.5 14.5 0 0 1 ' + cx + ' ' + (cy - 14.5) + '"/>';
    return ''
      + '<path class="si" stroke-width="11" stroke-linecap="round" d="M104.5 65A14.5 14.5 0 1 1 90 50.5"/>'
      + '<path class="' + acc + '" stroke-width="11" stroke-linecap="round" d="M90 50.5A14.5 14.5 0 0 1 104.5 65"/>'
      + '<path class="si" stroke-width="11" stroke-linecap="round" stroke-linejoin="round" d="M122.5 51.5L136 78.5L149.5 51.5"/>'
      + bowl(178, 65) + '<path class="si" stroke-width="11" stroke-linecap="round" d="M192.5 51.5V78.5"/>'
      + '<path class="si" stroke-width="11" stroke-linecap="round" d="M210.5 51.5V102.5"/>' + bowl(225, 65)
      + '<path class="si" stroke-width="11" stroke-linecap="round" d="M257.5 51.5V102.5"/>' + bowl(272, 65);
  }

  const MARK = ring() + corner() + kay();
  // плотная версия: та же геометрия заливкой, буква просветом — для мелких размеров
  const SOLID = '<rect class="ink" x="32" y="12" width="96" height="96" rx="30"/>' + corner() + kay(null, true);
  // с обводкой: штрих идёт по периметру, потом буква, потом янтарный угол
  const MARK_ANIM = ring('an draw') + kay('an pop') + corner('an draw corner');

  function svg(viewBox, body, cls) {
    const el = document.createElementNS(NS, 'svg');
    el.setAttribute('class', 'kov-mark ' + (cls || ''));
    el.setAttribute('viewBox', viewBox);
    el.setAttribute('role', 'img');
    el.setAttribute('aria-label', 'Kovapp');
    el.innerHTML = body;
    return el;
  }

  /** Знак без анимации */
  function mark(cls) { return svg('0 0 160 120', MARK, cls); }
  /** Плотная версия — для размеров меньше 32 px и иконок */
  function solid(cls) { return svg('0 0 160 120', SOLID, cls); }
  /** Знак с названием в строку */
  function lockup(cls) {
    return svg('12 0 370 120', MARK + '<g transform="translate(70,0)">' + wordPaths(false) + '</g>', cls);
  }

  /**
   * Знак, который сам себя рисует: длина контура считается по факту, иначе
   * штриховая обводка не работает. play() запускает заново.
   */
  function animated(cls) {
    const el = svg('0 0 160 120', MARK_ANIM, 'animated ' + (cls || ''));
    const prepare = () => {
      el.querySelectorAll('.draw').forEach(node => {
        try {
          const len = Math.ceil(node.getTotalLength());
          node.style.setProperty('--len', len);
          node.style.strokeDasharray = len;
        } catch (e) { /* знак ещё не в документе — попробуем позже */ }
      });
    };
    el.play = function () {
      prepare();
      el.classList.remove('play');
      // у SVG нет offsetWidth, поэтому перезапуск через два кадра
      requestAnimationFrame(() => requestAnimationFrame(() => el.classList.add('play')));
    };
    requestAnimationFrame(() => el.play());
    return el;
  }

  return { mark, solid, lockup, animated, MARK, SOLID };
})();
