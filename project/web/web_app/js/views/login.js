/* Экран входа: как окно 350×400 десктопа — «Kovapp», логин, пароль, «Войти»,
 * ссылка «Сброс пароля» (запрос кода → проверка → новый пароль). */
window.Kov = window.Kov || {};

Kov.Views = Kov.Views || {};
Kov.Views.login = (function () {
  const { h, button, field } = Kov.UI;

  function render(onSuccess) {
    const root = h('div.login-screen');
    // Неспешный фон: три мягких пятна фирменных цветов, медленно плывущих
    // друг относительно друга. Всё на CSS-анимации, без нагрузки на процессор.
    const backdrop = h('div.login-bg', { 'aria-hidden': 'true' },
      h('span.blob.b1'), h('span.blob.b2'), h('span.blob.b3'), h('span.grid'));
    const card = h('div.login-card');
    // Переключатель темы: до входа выбор хранится только на устройстве, а
    // после входа уходит в настройки сотрудника вместе с масштабом
    const themeBtn = h('button.theme-toggle', { type: 'button' });
    function paintTheme() {
      const dark = Kov.Theme.current === 'dark';
      Kov.UI.clear(themeBtn);
      themeBtn.append(Kov.UI.icon(dark ? 'sun' : 'moon'), h('span', dark ? 'Светлая тема' : 'Тёмная тема'));
      themeBtn.title = dark ? 'Вернуть светлую тему' : 'Включить тёмную тему';
    }
    themeBtn.addEventListener('click', e => {
      const r = themeBtn.getBoundingClientRect();
      Kov.Theme.toggle({ origin: { x: r.left + r.width / 2, y: r.top + r.height / 2 } });   // на сервер уйдёт после входа
      paintTheme();
    });
    paintTheme();
    root.append(backdrop, themeBtn, card);
    document.body.append(root);
    // Любой сбой при сборке формы раньше оставлял пустую карточку без
    // единого следа. Теперь ошибка видна и форму можно открыть повторно.
    try { showLogin(); }
    catch (e) {
      console.error('Форма входа не собралась', e);
      Kov.UI.clear(card);
      card.append(h('div.error', 'Страница входа не загрузилась полностью. Обновите страницу с Ctrl+F5; если не поможет, проверьте, что на сервер скопированы все файлы приложения.'),
        h('div.small.muted', String((e && e.message) || e)),
        button('Обновить страницу', { icon: 'refresh', onClick: () => location.reload(true) }));
    }

    /** Поле пароля с глазом: текст можно на время показать */
    function passwordField(placeholder, autocomplete) {
      const input = h('input.input.centered', { type: 'password', placeholder, autocomplete });
      const eye = h('button.eye', { type: 'button', title: 'Показать пароль', 'aria-label': 'Показать пароль' }, Kov.UI.icon('eye'));
      eye.addEventListener('click', () => {
        const shown = input.type === 'text';
        input.type = shown ? 'password' : 'text';
        eye.classList.toggle('on', !shown);
        eye.title = eye.ariaLabel = shown ? 'Показать пароль' : 'Скрыть пароль';
        Kov.UI.clear(eye); eye.append(Kov.UI.icon(shown ? 'eye' : 'eye_off'));
        Kov.UI.softFocus(input);
      });
      const box = h('div.pass-field', input, eye);
      box.input = input;
      return box;
    }

    function showLogin(message) {
      Kov.UI.clear(card);
      const login = h('input.input.centered', { type: 'text', placeholder: 'Логин', autocomplete: 'username', autocapitalize: 'none' });
      const passBox = passwordField('Пароль', 'current-password');
      const password = passBox.input;
      const error = h('div.error', { hidden: true });
      const submit = button('Войти', { icon: 'check', onClick: doLogin });
      async function doLogin() {
        error.hidden = true;
        if (!login.value.trim() || !password.value) { error.textContent = 'Введите логин и пароль'; error.hidden = false; return; }
        submit.disabled = true;
        try {
          await Kov.Api.login(Kov.Api.state.host || location.origin, login.value.trim(), password.value);
          root.remove();
          onSuccess();
        } catch (e) {
          error.textContent = e.status === 401 ? 'Неверный логин или пароль' : (e.status === 429 ? 'Слишком много попыток, подождите минуту' : 'Ошибка входа: ' + Kov.UI.errorText(e));
          error.hidden = false;
        } finally { submit.disabled = false; }
      }
      [login, password].forEach(i => i.addEventListener('keydown', e => { if (e.key === 'Enter') doLogin(); }));
      // Знак — отдельный файл js/core/logo.js. Если он не доехал на сервер,
      // форма всё равно должна открываться: раньше при его отсутствии
      // showLogin падал на первой же строке и карточка оставалась пустой.
      // Логотип с названием: слово выезжает из-под буквы K — как в макете.
      // Отдельная надпись «Kovapp» под знаком больше не нужна.
      let logo = null;
      try { if (window.Kov && Kov.Logo && Kov.Logo.lockupAnimated) logo = Kov.Logo.lockupAnimated(); }
      catch (e) { console.error('Знак не построился', e); }
      const brand = h('div.brand.lock' + (logo ? '' : '.no-mark'), { title: logo ? 'Нажмите, чтобы повторить' : '' },
        logo, logo ? null : h('div.word', 'Kovapp'));
      if (logo) brand.addEventListener('click', () => logo.play());
      Kov.UI.append(card, [
        brand,
        message ? h('div.hint', message) : null,
        login, passBox, error, submit,
        h('button.link', { type: 'button', text: 'Сброс пароля', onclick: () => showReset(login.value.trim()) }),
      ]);
      Kov.UI.softFocus(login.value ? password : login);
    }

    function showReset(prefill) {
      Kov.UI.clear(card);
      const username = h('input.input.centered', { type: 'text', placeholder: 'Логин', value: prefill || '', autocapitalize: 'none' });
      const code = h('input.input.centered', { type: 'text', placeholder: 'Код из письма', inputmode: 'numeric', autocomplete: 'one-time-code' });
      const box1 = passwordField('Новый пароль', 'new-password'), pass1 = box1.input;
      const box2 = passwordField('Повторите пароль', 'new-password'), pass2 = box2.input;
      const info = h('div.hint', 'Введите логин — на почту сотрудника придёт код');
      const error = h('div.error', { hidden: true });
      let step = 1;
      const next = button('Отправить код', { icon: 'mail', onClick: go });
      const back = h('button.link', { type: 'button', text: 'Назад ко входу', onclick: () => showLogin() });
      function fail(text) { error.textContent = text; error.hidden = false; }
      async function go() {
        error.hidden = true; next.disabled = true;
        try {
          if (step === 1) {
            if (!username.value.trim()) return fail('Введите логин');
            const r = await Kov.Api.post('reset-password/request', { json: { username: username.value.trim() }, anonymous: true });
            if (r && r.success === false) return fail(r.message || 'Не удалось отправить код');
            step = 2; info.textContent = (r && r.message) || 'Код отправлен. Введите его ниже.';
            code.hidden = false; next.replaceChildren(Kov.UI.icon('check'), 'Проверить код'); Kov.UI.softFocus(code);
          } else if (step === 2) {
            if (!code.value.trim()) return fail('Введите код');
            const r = await Kov.Api.post('reset-password/verify', { json: { username: username.value.trim(), reset_code: code.value.trim() }, anonymous: true });
            if (r && r.success === false) return fail((r.message || 'Неверный код') + (r.attempts_left != null ? ' (осталось попыток: ' + r.attempts_left + ')' : ''));
            step = 3; info.textContent = 'Код принят. Придумайте новый пароль.';
            box1.hidden = box2.hidden = false; next.replaceChildren(Kov.UI.icon('key'), 'Сменить пароль'); Kov.UI.softFocus(pass1);
          } else {
            if (pass1.value.length < 6) return fail('Пароль не короче 6 символов');
            if (pass1.value !== pass2.value) return fail('Пароли не совпадают');
            const r = await Kov.Api.post('reset-password/confirm', { json: { username: username.value.trim(), reset_code: code.value.trim(), new_password: pass1.value }, anonymous: true });
            if (r && r.success === false) return fail(r.message || 'Не удалось сменить пароль');
            showLogin('Пароль изменён — войдите с новым паролем');
          }
        } catch (e) { fail(Kov.UI.errorText(e)); }
        finally { next.disabled = false; }
      }
      code.hidden = box1.hidden = box2.hidden = true;
      [username, code, pass1, pass2].forEach(i => i.addEventListener('keydown', e => { if (e.key === 'Enter') go(); }));
      card.append(h('div.brand.small', (window.Kov && Kov.Logo) ? Kov.Logo.mark() : null, h('div.word', 'Сброс пароля')),
        info, username, code, box1, box2, error, next, back);
      Kov.UI.softFocus(username);
    }
    return root;
  }

  return { render };
})();
