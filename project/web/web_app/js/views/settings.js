/* Настройки: три карточки — внешний вид (масштаб с живым предпросмотром),
 * уведомления (переключатели) и это устройство (push). Порядок сверху вниз —
 * от того, что меняют чаще, к тому, что настраивают один раз.
 *
 * Всё, кроме кнопок push, применяется сразу для предпросмотра, а «Сохранить»
 * закрепляет выбор на сервере: так видно результат до сохранения.
 */
window.Kov = window.Kov || {};
Kov.Views = Kov.Views || {};

Kov.Views.settings = (function () {
  const { h, icon, button } = Kov.UI;

  /** Переключатель с анимацией: ручка на пружине, галочка проявляется */
  function toggle(checked, onChange) {
    const input = h('input', { type: 'checkbox', checked: !!checked, role: 'switch' });
    input.addEventListener('change', () => onChange && onChange(input.checked));
    const el = h('label.switch', input, h('span.track'), h('span.knob', icon('check', { stroke: 3 })));
    el.input = input;
    return el;
  }

  /** Строка настройки: значок, название с пояснением, справа управление */
  function row(glyph, title, note, control, sub) {
    const el = h('div.set-row' + (sub ? '.sub' : ''),
      h('span.set-ico', icon(glyph)),
      h('div.set-text', h('b', title), note ? h('span', note) : null),
      control);
    // по всей строке удобнее попадать, чем по самому переключателю
    if (control && control.classList && control.classList.contains('switch')) {
      el.addEventListener('click', e => {
        if (e.target.closest('.switch')) return;
        control.input.checked = !control.input.checked;
        control.input.dispatchEvent(new Event('change'));
      });
    }
    return el;
  }

  function card(glyph, title, note, body, wide) {
    const el = h('div.set-card' + (wide ? '.span2' : ''),
      h('div.set-head', h('span.set-glyph', icon(glyph)),
        h('div', h('span.t', title), note ? h('span.s', note) : null)),
      h('div.set-body', ...body.filter(Boolean)));
    return el;
  }

  // разделы для телефона: ключ адреса, значок, название, пояснение
  const PARTS = [
    ['look', 'sun', 'Внешний вид', 'Тема и размер интерфейса'],
    ['notify', 'bell', 'Уведомления', 'Что показывать, пока приложение открыто'],
    ['device', 'device', 'Это устройство', 'Уведомления, когда приложение закрыто'],
  ];

  function view(section, part) {
    let dirty = false;
    // телефон: раздел на отдельном экране, изменения сохраняются сами (уйти «Назад» — не потерять)
    const mobile = Kov.Scale.isMobile();
    let autoSave = null;
    const hint = h('span.hint', 'Оформление сохраняется само, уведомления — кнопкой справа');
    const saveBtn = button('Сохранить', { icon: 'save', onClick: save });
    function markDirty() {
      if (mobile) { clearTimeout(autoSave); autoSave = setTimeout(save, 500); return; }
      if (dirty) return;
      dirty = true;
      hint.textContent = 'Есть несохранённые изменения в уведомлениях';
      saveBtn.classList.remove('saved');
    }

    // ── тема ───────────────────────────────────────────────────────────────
    const themePick = Kov.UI.segmented(
      [['light', 'Светлая', 'sun'], ['dark', 'Тёмная', 'moon'], ['auto', 'Как в системе', 'device']],
      Kov.Theme.mode,
      (key, btn) => {
        const r = (btn || themePick).getBoundingClientRect();
        // тема уходит на сервер сразу: иначе после перезагрузки серверное
        // значение возвращало прежнее оформление, и выбор «слетал»
        Kov.Theme.set(key, { origin: { x: r.left + r.width / 2, y: r.top + r.height / 2 }, persist: true });
      });
    const themeNote = h('div.small.muted', 'Тема запоминается за сотрудником: на другом устройстве включится та же.');

    // ── внешний вид ────────────────────────────────────────────────────────
    let scale = Kov.Scale.getMultiplier();
    const scaleValue = h('div.set-scale-value', h('b', scale.toFixed(2) + '×'), h('span', 'размер интерфейса'));
    const slider = h('input.range', { type: 'range', min: Kov.Scale.MIN, max: Kov.Scale.MAX, step: 0.01, value: scale });
    const presets = Kov.UI.segmented(Kov.Scale.PRESETS.map(([v, name]) => [String(v), name]),
      String(scale), key => apply(parseFloat(key)));
    const ticks = h('div.set-ticks', h('span', 'мельче'), h('span', 'стандарт'), h('span', 'крупнее'));
    const preview = h('div.preview-box',
      h('span', 'Пример текста'), button('Пример кнопки', {}), h('span.chip.info', 'Пример метки'));

    // Масштаб тоже закрепляется сам, с задержкой: ползунок шлёт десятки
    // значений в секунду, на сервер уходит только то, на чём человек замер
    let scaleTimer = null;
    function persistScale() {
      clearTimeout(scaleTimer);
      scaleTimer = setTimeout(async () => {
        try {
          const current = (await Kov.Store.pref('web.settings')) || {};
          await Kov.Store.setPref('web.settings', Object.assign({}, current, { scale, scaleBase: 2 }));
          Kov.Scale.setMultiplier(scale, true);
        } catch (e) { /* останется кнопка «Сохранить» */ }
      }, 700);
    }

    function apply(v) {
      scale = Kov.Scale.setMultiplier(v, false);
      scaleValue.firstChild.textContent = scale.toFixed(2) + '×';
      slider.value = scale;
      // подсветка пресета снимается, если значение между ними
      const exact = Kov.Scale.PRESETS.find(([p]) => Math.abs(p - scale) < 0.005);
      presets.setActive(exact ? String(exact[0]) : '');
      persistScale();
      markDirty();
    }
    slider.addEventListener('input', () => apply(parseFloat(slider.value)));
    apply(scale); clearTimeout(scaleTimer); dirty = false;
    hint.textContent = 'Оформление сохраняется само, уведомления — кнопкой справа';

    // ── уведомления ────────────────────────────────────────────────────────
    const s = Kov.NotifyCenter.getSettings();
    const state = { toasts: !!s.toasts, chatToasts: !!s.chatToasts, sound: !!s.sound, os: !!s.os };

    const chatSwitch = toggle(state.chatToasts, v => { state.chatToasts = v; markDirty(); });
    const chatRow = row('chat', 'В том числе о сообщениях чата',
      'Без этого чат будет виден только счётчиком', chatSwitch, true);
    const toastSwitch = toggle(state.toasts, v => {
      state.toasts = v; syncChatRow(); markDirty();
    });
    function syncChatRow() { chatRow.classList.toggle('off', !state.toasts); }
    syncChatRow();

    const soundSwitch = toggle(state.sound, v => { state.sound = v; markDirty(); });
    const osSwitch = toggle(state.os, async v => {
      state.os = v; markDirty();
      if (v && Kov.NotifyMode.notificationsApi && Notification.permission !== 'granted') {
        await Kov.NotifyCenter.requestPermission();
      }
      refreshPerm();
    });
    const osRow = row('device', 'Системные уведомления',
      'Когда вкладка свёрнута, уведомление показывает сама система', osSwitch);
    if (!Kov.NotifyMode.notificationsApi) { osSwitch.input.disabled = true; osRow.classList.add('off'); }

    // ── это устройство ─────────────────────────────────────────────────────
    const pushPill = h('span.set-pill', icon('info'), 'Проверяем…');
    const pushNote = h('div.set-note', Kov.NotifyMode.describe());
    const pushOn = button('Включить на этом устройстве', { icon: 'bell', onClick: async () => {
      const r = await Kov.PushClient.enable();
      Kov.UI.toast(r.ok ? 'Уведомления включены' : r.reason, r.ok ? 'success' : 'warning');
      refreshPush();
    } });
    const pushOff = button('Отключить', { kind: 'ghost', icon: 'close', onClick: async () => { await Kov.PushClient.disable(); refreshPush(); } });
    const pushTest = button('Проверить', { kind: 'ghost', icon: 'check', onClick: async () => {
      try { await Kov.PushClient.test(); Kov.UI.toast('Тест отправлен', 'success'); }
      catch (e) { Kov.UI.toast(Kov.UI.errorText(e), 'error'); }
    } });
    const pushButtons = h('div.btn-row', pushOn, pushOff, pushTest);
    const permNote = h('div.set-note');
    const permAllow = button('Разрешить уведомления', { icon: 'bell', onClick: async () => {
      const r = await Kov.PushClient.enable();
      Kov.UI.toast(r.ok ? (r.push ? 'Уведомления включены, в том числе при закрытом браузере' : (r.reason || 'Системные уведомления включены')) : r.reason,
        r.ok ? 'success' : 'warning');
      refreshPerm(); refreshPush();
    } });
    const permRow = h('div.set-perm', permNote, h('div.btn-row', permAllow));
    function refreshPerm() {
      const api = Kov.NotifyMode.notificationsApi;
      const p = api ? Notification.permission : 'unsupported';
      // приложение может спросить снова (или открыть настройки телефона), браузер — нет
      permAllow.hidden = !(p === 'default' || (p === 'denied' && Kov.NotifyMode.canAskAgain));
      // переключатель горит только при выданном разрешении: без него система уведомления не покажет.
      // Не спрашивали (или запрос закрыли) — погашен, но нажимается и вызывает запрос; запрещены — погашен и заперт.
      // Сама настройка сохраняется и вернётся вместе с разрешением
      const granted = api && p === 'granted';
      const blocked = api && p === 'denied';
      osSwitch.input.checked = granted ? !!state.os : false;
      osSwitch.input.disabled = !api || blocked;
      osRow.classList.toggle('off', !granted);
      permNote.textContent = !api ? 'Системные уведомления этот браузер здесь не даёт: ' + Kov.NotifyMode.describe()
        : p === 'granted' ? 'Браузер разрешил системные уведомления для этого сайта.'
        : p === 'denied' ? 'Браузер запретил уведомления для этого сайта. Разрешить их можно в настройках сайта: значок замка слева от адреса → «Уведомления» → «Разрешить».'
        : 'Браузер ещё не спрашивал разрешение. Нажмите кнопку — появится запрос.';
    }
    refreshPerm();

    function setPill(kind, glyph, text) {
      Kov.UI.clear(pushPill);
      pushPill.className = 'set-pill' + (kind ? ' ' + kind : '');
      pushPill.append(icon(glyph), text);
    }
    async function refreshPush() {
      const st = await Kov.PushClient.status();
      // описание зависит от состояния устройства — обновляем вместе со значком
      pushNote.textContent = Kov.NotifyMode.describe();
      const ready = st.supported && st.serverEnabled;
      const denied = Kov.NotifyMode.notificationsApi && Notification.permission === 'denied';
      if (st.subscribed && denied) setPill('warn', 'warning', 'Запрещены в настройках');
      else if (st.subscribed) setPill('on', 'success', 'Устройство подписано');
      else if (ready) setPill('', 'bell', 'Подписки нет');
      else setPill('warn', 'warning', 'Пока недоступно');
      pushOn.hidden = !ready || st.subscribed || (Kov.NotifyMode.notificationsApi && Notification.permission === 'default');
      pushOff.hidden = !st.subscribed;
      pushTest.hidden = !st.subscribed;
      pushButtons.hidden = pushOn.hidden && pushOff.hidden && pushTest.hidden;
    }
    refreshPush();
    // вернулись из настроек телефона или разрешение поменяли там — показать как есть
    const onPermission = () => {
      if (!section.isConnected) {
        document.removeEventListener('visibilitychange', onPermission);
        window.removeEventListener('kov:permission', onPermission);
        return;
      }
      if (document.visibilityState === 'visible') { refreshPerm(); refreshPush(); }
    };
    document.addEventListener('visibilitychange', onPermission);
    window.addEventListener('kov:permission', onPermission);

    // ── сохранение ─────────────────────────────────────────────────────────
    async function save() {
      Kov.Scale.setMultiplier(scale, true);
      Kov.NotifyCenter.setSettings(state);
      try {
        await Kov.Store.setPref('web.notifications', Kov.NotifyCenter.getSettings());
        await Kov.Store.setPref('web.settings', { scale, scaleBase: 2, theme: Kov.Theme.mode });
      } catch (e) {
        return Kov.UI.toast('Не удалось сохранить: ' + Kov.UI.errorText(e), 'error');
      }
      dirty = false;
      saveBtn.classList.add('saved');
      Kov.UI.clear(hint);
      hint.append(h('span.set-saved-mark', icon('check', { stroke: 3 }), 'Сохранено'));
      setTimeout(() => { if (!dirty) { saveBtn.classList.remove('saved'); hint.textContent = 'Настройки сохранены на сервере — они переедут на другие устройства'; } }, 1600);
    }

    const cards = {
        // тема и масштаб — про одно и то же, поэтому в одной карточке:
        // так обе колонки одинаковой высоты и нет пустого места
        look: card('sun', 'Внешний вид', 'Как приложение выглядит на этом и других устройствах', [
          h('div.set-sub', 'Тема'), themePick, themeNote,
          h('div.set-divider'),
          h('div.set-sub', 'Размер интерфейса'),
          scaleValue, presets, slider, ticks,
          h('div.small.muted', 'Предпросмотр'), preview,
        ]),
        notify: card('bell', 'Уведомления', 'Что показывать, пока приложение открыто', [
          row('info', 'Всплывающие уведомления', 'Карточка в углу экрана о новых событиях', toastSwitch),
          chatRow,
          row('volume', 'Звук', 'Короткий сигнал вместе с уведомлением', soundSwitch),
          osRow, permRow,
        ]),
        device: card('device', 'Это устройство', 'Уведомления, когда приложение закрыто', [
          h('div.row', { style: { display: 'flex', alignItems: 'center', gap: '.6rem', flexWrap: 'wrap' } }, pushPill),
          pushNote, pushButtons,
        ], true),
    };

    if (!mobile) {
      section.append(h('div.settings-page',
        h('div.settings-grid', cards.look, cards.notify, cards.device),
        h('div.set-save', hint, saveBtn),
      ));
      return;
    }
    const current = PARTS.find(p => p[0] === part);
    if (!current) {
      // список разделов
      section.append(h('div.settings-page.set-menu',
        ...PARTS.map(([key, glyph, title, note]) => h('a.set-menu-row', { href: '#/settings/' + key },
          h('span.set-glyph', icon(glyph)),
          h('div.set-menu-text', h('span.t', title), h('span.s', note)),
          h('span.set-menu-go', icon('chevron_right')))),
        h('div.set-menu-hint', 'Изменения сохраняются сами и переезжают на другие ваши устройства'),
      ));
      return;
    }
    section.append(h('div.settings-page.set-part',
      h('a.set-back', { href: '#/settings' }, icon('chevron_left'), 'Настройки'),
      cards[current[0]],
    ));
  }

  return { view };
})();
