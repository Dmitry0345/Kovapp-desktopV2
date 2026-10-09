"""Тесты на настоящем Android (всё в Docker, эмулятор — Android Studio на ПК).

Две части приложения — два способа управления:
- система телефона — Appium (UiAutomator2): установка, системный запрос разрешения, шторка,
  «Назад», «Домой», запуск и сворачивание, поворот, журнал logcat;
- страница внутри приложения — DevTools WebView напрямую (тот же протокол, которым пользуется
  Chromedriver, но без него): вход ролью, разделы, проверки вёрстки. Нажатия и ввод — настоящие
  события касания и клавиатуры.
У каждого действия есть предел по времени: прогон не может «повиснуть».
Приложение перед прогоном ставится начисто — как у нового сотрудника.
"""
import json
import os
import re
import socket
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .device import PKG, SERVICE_NOTIFICATION_ID, DeviceError, parse_notifications  # noqa: F401

ACTIVITY = ".MainActivity"
AUDIT_JS = (Path(__file__).resolve().parent / "audit.js")
COMMAND_TIMEOUT = 240            # предел одной команды Appium, с
CDP_TIMEOUT = 30                 # предел одной команды страницы, с


def _reason(text: str) -> str:
    """Суть ошибки Appium: код отказа Android, если он есть, иначе первая строка."""
    for pattern in (r"Failure \[[^\]]+\]", r"INSTALL_[A-Z_]+[^'\n]*", r"adb: failed to install[^\n']*"):
        m = re.search(pattern, text)
        if m:
            return m.group(0)[:400]
    lines = [l for l in text.splitlines() if l.strip()]
    return (lines[0] if lines else text)[:400]


class JsError(Exception):
    pass


class PageCdp:
    """Страница WebView по протоколу DevTools (websocket к цели «page»)."""

    def __init__(self, base: str):
        import websocket
        targets = json.loads(urllib.request.urlopen(base + "/json/list", timeout=15).read().decode("utf-8"))
        pages = [t for t in targets if t.get("type") == "page"]
        page = next((t for t in pages if "/app/" in t.get("url", "")), pages[0] if pages else None)
        if not page:
            raise DeviceError("страница приложения не найдена среди отладочных целей WebView")
        # адрес websocket — через тот же хост и порт, по которому пришли (IP: WebView отклоняет имена)
        path = urllib.parse.urlparse(page["webSocketDebuggerUrl"]).path
        self.ws = websocket.create_connection("ws://%s%s" % (urllib.parse.urlparse(base).netloc, path),
                                              timeout=CDP_TIMEOUT, suppress_origin=True)
        self.seq = 0

    def call(self, method: str, params=None, timeout: float = CDP_TIMEOUT) -> dict:
        self.seq += 1
        mine = self.seq
        self.ws.settimeout(timeout)
        self.ws.send(json.dumps({"id": mine, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mine:
                if "error" in msg:
                    raise JsError("%s: %s" % (method, msg["error"].get("message")))
                return msg.get("result", {})
        raise DeviceError("страница приложения не ответила за %d с (%s)" % (timeout, method))

    def evaluate(self, expression: str, timeout: float = CDP_TIMEOUT):
        result = self.call("Runtime.evaluate", {"expression": expression, "awaitPromise": True,
                                                "returnByValue": True, "userGesture": True}, timeout)
        if result.get("exceptionDetails"):
            details = result["exceptionDetails"]
            text = (details.get("exception") or {}).get("description") or details.get("text") or "ошибка JS"
            raise JsError(text.splitlines()[0][:300])
        return (result.get("result") or {}).get("value")

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass


class AppiumEnv:
    def __init__(self, world, url: str = "", link_url: str = "", label: str = "телефон"):
        from appium import webdriver
        from appium.options.android import UiAutomator2Options

        self.world = world
        self.settings = world.settings
        self.log = world.log
        self.label = label                           # «телефон 1» / «телефон 2» — в сообщениях тестов
        self.url = url or os.environ.get("APPIUM_URL", "http://appium:4723")
        self.link_url = link_url or os.environ.get("LINK_URL", "http://appium:4799")
        self.apk = os.environ.get("DEVICE_APK", "/apk/app-debug.apk")
        host = urllib.parse.urlparse(self.link_url).hostname
        self.cdp_base = "http://%s:9223" % socket.gethostbyname(host)     # IP, не имя: так требует WebView
        self.page = None
        self.keys = self._keycheck()
        self._say("проверяю связь с телефоном и готовлю его к установке начисто...")
        reset = self._post("/reset")                 # установка начисто
        if not reset.get("ok"):
            raise DeviceError("adb потерял связь с эмулятором (состояние: %s). Закройте эмулятор и запустите снова"
                              % (reset.get("state") or "нет ответа"))
        self._post("/link/on")
        opts = UiAutomator2Options()
        opts.platform_name = "Android"
        opts.automation_name = "UiAutomator2"
        opts.app = self.apk
        opts.app_package = PKG
        opts.app_activity = ACTIVITY
        opts.no_reset = True                         # между тестами данные приложения не стираются
        opts.auto_grant_permissions = False          # разрешение на уведомления тесты проверяют сами
        opts.new_command_timeout = 900
        for name, value in (("uiautomator2ServerInstallTimeout", 180000), ("androidInstallTimeout", 240000),
                            ("adbExecTimeout", 120000), ("uiautomator2ServerLaunchTimeout", 180000)):
            opts.set_capability("appium:" + name, value)
        self.notes = []                               # попадают в отчёт отдельными строками
        self._say("открываю сессию Appium: ставлю приложение и служебные модули (до 5 минут)...")
        try:
            self.driver = self._remote(webdriver, opts)
        except Exception as e:
            text = str(e)
            self.log.error("Appium: ошибка открытия сессии, полный текст:\n%s" % text)
            if "io.appium.settings" not in text:
                raise DeviceError("Appium не открыл сессию: %s" % _reason(text))
            # служебное приложение Appium не встало — тестам оно не нужно, открываем сессию без него
            self.notes.append("Служебное приложение Appium (io.appium.settings) не установилось: %s — прогон без него" % _reason(text))
            self.log.warn(self.notes[-1])
            opts.set_capability("appium:skipDeviceInitialization", True)
            self._say("служебное приложение Appium не встало — открываю сессию без него...")
            try:
                self.driver = self._remote(webdriver, opts)
            except Exception as e2:
                self.log.error("Appium: повторная попытка без служебного приложения, полный текст:\n%s" % e2)
                raise DeviceError("Appium не открыл сессию: %s" % _reason(str(e2)))
        self._say("сессия открыта, подключаюсь к странице приложения...")
        caps = self.driver.capabilities
        self.sdk = int(caps.get("deviceApiLevel") or 0)
        self.release = str(caps.get("platformVersion") or "")
        self.model = str(caps.get("deviceModel") or caps.get("deviceName") or "")
        self.gms = bool(self.shell("pm path com.google.android.gms").strip())
        self.app_version = self._version()
        self.shell("logcat -b crash -c")            # падения считаем с начала этого прогона
        self.web()
        self._say("страница приложения доступна, начинаю тесты")
        self.log.info("устройство: %s, Android %s (API %d), сервисы Google: %s; приложение %s"
                      % (self.model, self.release, self.sdk, "есть" if self.gms else "нет", self.app_version))

    # ----- служебное -----

    def _remote(self, webdriver, opts):
        """Сессия Appium с пределом времени на каждую команду (зависшая команда не держит прогон)."""
        try:
            from appium.webdriver.client_config import AppiumClientConfig
            config = AppiumClientConfig(remote_server_addr=self.url, timeout=COMMAND_TIMEOUT)
            return webdriver.Remote(self.url, options=opts, client_config=config)
        except ImportError:
            from selenium.webdriver.remote.remote_connection import RemoteConnection
            RemoteConnection.set_timeout(COMMAND_TIMEOUT)
            return webdriver.Remote(self.url, options=opts)

    def _say(self, text: str) -> None:
        """Ход подготовки — в окно и в журнал, чтобы долгие шаги не выглядели зависанием."""
        print("  %s: %s" % (self.label, text), flush=True)
        self.log.info("%s: %s" % (self.label, text))

    def _post(self, path: str) -> dict:
        req = urllib.request.Request(self.link_url + path, data=b"", method="POST")
        try:
            with urllib.request.urlopen(req, timeout=240) as r:
                return json.loads(r.read().decode("utf-8"))
        except OSError as e:
            raise DeviceError("служба связи Appium (%s) не отвечает: %s" % (self.link_url, e))

    @staticmethod
    def _keycheck() -> dict:
        try:
            return json.loads(Path("/shared/keycheck.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"present": False, "valid": False, "problems": []}

    def _version(self) -> str:
        for line in self.shell("dumpsys package %s" % PKG).splitlines():
            if "versionName=" in line:
                return line.split("versionName=", 1)[1].split()[0]
        return ""

    def shell(self, command: str) -> str:
        parts = command.split()
        try:
            return str(self.driver.execute_script("mobile: shell", {"command": parts[0], "args": parts[1:], "timeout": 60000}) or "")
        except Exception as e:
            self.log.warn("устройство: команда «%s» не выполнена: %s" % (command[:60], str(e).splitlines()[0][:200]))
            return ""

    def native(self) -> None:
        """Appium всегда в системной части — переключать контекст не нужно (оставлено для совместимости)."""

    # ----- страница внутри приложения (DevTools) -----

    def web(self, timeout: float = 40) -> PageCdp:
        """Подключение к странице приложения; после перезапуска приложения — заново."""
        if self.page is not None:
            return self.page
        deadline = time.time() + timeout
        last = ""
        while time.time() < deadline:
            info = self._post("/webview")
            if info.get("ok"):
                try:
                    self.page = PageCdp(self.cdp_base)
                    return self.page
                except DeviceError:
                    raise
                except Exception as e:
                    last = str(e)
            else:
                last = info.get("error") or "нет ответа"
            time.sleep(2)
        raise DeviceError("страница приложения недоступна тестам: %s" % last)

    def _page_call(self, action):
        """Команда странице; если связь с ней оборвалась (приложение перезапустилось) — одна переподключка.
        Страница не отвечает и поверх приложения системное окно (Android приостанавливает страницу) —
        отвечаем «Разрешить», как сделал бы сотрудник, или честно говорим, какое окно мешает."""
        try:
            return action(self.web())
        except (JsError, DeviceError):
            raise
        except Exception:
            blocked = self.system_dialog()
            if blocked:
                self.answer_system_dialog()
                still = self.system_dialog()
                if still:
                    raise DeviceError("страница приложения не отвечает: поверх приложения открыто окно %s" % still)
            self._drop_page()
            return action(self.web())

    def system_dialog(self) -> str:
        """Системное окно поверх приложения (пусто, если на экране приложение)."""
        try:
            where = self.resumed()
        except Exception:
            return ""
        return "" if where.startswith(PKG + "/") else where

    def answer_system_dialog(self) -> str:
        """Нажать «Разрешить» в системном окне поверх приложения. Вернуть, что это было за окно."""
        from appium.webdriver.common.appiumby import AppiumBy
        where = self.system_dialog()
        if not where:
            return ""
        for selector in ('new UiSelector().resourceIdMatches(".*permissioncontroller:id/permission_allow_button")',
                         'new UiSelector().resourceId("android:id/button1")',
                         'new UiSelector().textMatches("(?i)^(Разрешить|Allow)$")'):
            try:
                found = self.driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, selector)
                if found:
                    found[0].click()
                    time.sleep(1.5)
                    self.log.warn("поверх приложения было окно %s — нажато «Разрешить»" % where)
                    return where
            except Exception as e:
                if "Stale" not in type(e).__name__:
                    raise
        return where

    def _drop_page(self) -> None:
        if self.page is not None:
            self.page.close()
        self.page = None

    def js(self, script: str, *args):
        """Выполнить тело функции на странице (return и arguments[i] — как в Selenium)."""
        expression = "(function(){ %s }).apply(null, %s)" % (script, json.dumps(list(args), ensure_ascii=False))
        return self._page_call(lambda p: p.evaluate(expression))

    def js_async(self, script: str, *args, timeout: float = CDP_TIMEOUT):
        """То же, но результат отдаётся вызовом последнего аргумента (как execute_async_script)."""
        expression = ("new Promise(function(__done){ (function(){ %s }).apply(null, %s.concat([__done])); })"
                      % (script, json.dumps(list(args), ensure_ascii=False)))
        return self._page_call(lambda p: p.evaluate(expression, timeout))

    def fetch_status(self, path: str) -> int:
        """Запрос к серверу изнутри приложения: код ответа или 0 (нет связи)."""
        return self.js_async("""
            const done = arguments[arguments.length - 1];
            const auth = JSON.parse(localStorage.getItem('kov.auth') || '{}');
            fetch(arguments[0], { headers: { Authorization: 'Bearer ' + (auth.token || '') } })
              .then(r => done(r.status)).catch(() => done(0));""", path)

    def wait_js(self, expression: str, timeout: float = 30) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.js("return !!(%s);" % expression):
                    return True
            except JsError:
                pass                                   # страница ещё грузится
            time.sleep(0.5)
        return False

    def _rect(self, finder: str, *args) -> dict:
        rect = self.js("""const el = (%s);
            if (!el) return null;
            // мгновенно: при плавной прокрутке координаты были бы ещё старыми, и касание промахнулось бы
            // (старый WebView без 'instant' — обычная прокрутка)
            try { el.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' }); }
            catch (e) { el.scrollIntoView({ block: 'center', inline: 'center' }); }
            const r = el.getBoundingClientRect();
            const x = r.left + r.width / 2, y = r.top + r.height / 2;
            // что окажется под пальцем: сам элемент или то, что его закрывает (для журнала)
            const top = document.elementFromPoint(x, y);
            const hit = !top || top === el || el.contains(top) || top.contains(el);
            const name = n => n.tagName.toLowerCase() + (typeof n.className === 'string' && n.className.trim()
                ? '.' + n.className.trim().split(/\\s+/).join('.') : '');
            return { x, y, w: r.width, h: r.height, cover: hit ? '' : name(top) };""" % finder, *args)
        return rect

    def _steady_rect(self, finder: str, *args, wait: float = 2.5):
        """Координаты элемента, когда он перестал двигаться: шторка выезжает, окно появляется,
        страница сжимается под клавиатуру. Касание по движущейся кнопке уходит мимо неё."""
        last = self._rect(finder, *args)
        deadline = time.time() + wait
        while last and time.time() < deadline:
            time.sleep(0.08)
            rect = self._rect(finder, *args)
            if not rect:
                return None
            if all(abs(rect[k] - last[k]) < 1 for k in ("x", "y", "w", "h")):
                return rect
            last = rect
        return last

    def _tap_logged(self, rect: dict, what: str) -> None:
        if rect.get("cover"):
            self.log.warn("%s: точку касания %s закрывает %s" % (self.label, what, rect["cover"]))
        self._tap(rect)

    def _tap(self, rect: dict) -> None:
        """Настоящее касание в точке элемента."""
        point = [{"x": rect["x"], "y": rect["y"]}]
        self._page_call(lambda p: p.call("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": point}))
        self._page_call(lambda p: p.call("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []}))

    def hold(self, rect: dict, seconds: float = 0.8) -> None:
        """Настоящее удержание пальцем в точке элемента."""
        point = [{"x": rect["x"], "y": rect["y"]}]
        self._page_call(lambda p: p.call("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": point}))
        time.sleep(seconds)
        self._page_call(lambda p: p.call("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []}))

    def long_press(self, selector: str, hold: float = 0.8, timeout: float = 15) -> bool:
        """Удержание по селектору (в списках так выделяют строку или карточку)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            rect = self._steady_rect("document.querySelector(arguments[0])", selector)
            if rect and rect["w"] > 0 and rect["h"] > 0:
                if rect.get("cover"):
                    self.log.warn("%s: точку удержания %s закрывает %s" % (self.label, selector, rect["cover"]))
                self.hold(rect, hold)
                return True
            time.sleep(0.5)
        return False

    def click(self, selector: str, timeout: float = 15) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            rect = self._steady_rect("document.querySelector(arguments[0])", selector)
            if rect and rect["w"] > 0 and rect["h"] > 0:
                self._tap_logged(rect, selector)
                return True
            time.sleep(0.5)
        return False

    def click_text(self, text: str, timeout: float = 15, within: str = "", last: bool = False) -> bool:
        """Нажать видимую кнопку с text (within — область, например окно; last — последнюю из найденных)."""
        finder = """(() => {
            const root = arguments[1] ? [...document.querySelectorAll(arguments[1])].pop() : document;
            if (!root) return null;
            const found = [...root.querySelectorAll('button, [role=button], a.btn')].filter(b => {
                const r = b.getBoundingClientRect();
                return r.width > 0 && r.height > 0 && (b.textContent || '').replace(/\\s+/g, ' ').includes(arguments[0]);
            });
            return arguments[2] ? found.pop() : found[0];
        })()"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            rect = self._steady_rect(finder, text, within, last)
            if rect:
                self._tap_logged(rect, "«%s»" % text if text else "кнопку окна")
                return True
            time.sleep(0.5)
        return False

    def button_enabled(self, text: str) -> bool:
        return bool(self.js("""return [...document.querySelectorAll('.content button, button')].some(b =>
            (b.textContent || '').includes(arguments[0]) && !b.disabled && !b.classList.contains('is-locked')
            && b.getBoundingClientRect().width > 0);""", text))

    def confirm(self, text: str) -> bool:
        """Подтвердить в окне приложения (кнопка в подвале верхнего окна)."""
        if not self.wait_js("document.querySelector('.modal-root .modal')", 8):
            return False
        return self.click_text(text, within=".modal-root", last=True, timeout=8)

    def set_value(self, selector: str, value: str) -> None:
        """Поле, касание которого открывает системный выбор (дата): значение ставится так же, как после выбора."""
        ok = self.js("""const el = document.querySelector(arguments[0]); if (!el) return false;
            const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
            set.call(el, arguments[1]);
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            return true;""", selector, value)
        if not ok:
            raise DeviceError("поле %s не найдено на странице" % selector)

    def wait_hash(self, pattern: str, timeout: float = 15) -> str:
        """Дождаться адреса вида pattern (регулярное выражение); вернуть адрес или пустую строку."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            where = self.js("return location.hash;") or ""
            if re.match(pattern, where):
                return where
            time.sleep(0.5)
        return ""

    def keyboard_shown(self):
        """Открыта ли клавиатура телефона (None — Appium не ответил)."""
        try:
            return bool(self.driver.is_keyboard_shown())
        except Exception:
            return None

    def settle(self, quiet: float = 0.5, timeout: float = 5, keyboard_wait: float = 2.5) -> None:
        """Дождаться, пока экран перестанет меняться: после касания поля открывается клавиатура,
        страница сжимается и прокручивается к полю — касание в это время уходит мимо кнопки.
        Поле ввода в фокусе — ждём клавиатуру (первый раз она открывается дольше), нет — её закрытия."""
        probe = """const v = window.visualViewport, a = document.activeElement;
            const top = a && a !== document.body && a.getBoundingClientRect ? Math.round(a.getBoundingClientRect().top) : 0;
            const typing = !!a && !a.readOnly && (a.tagName === 'TEXTAREA' || (a.tagName === 'INPUT'
                && /^(text|search|email|password|tel|url|number)$/.test(a.type || 'text')));
            return [innerWidth, innerHeight, v ? Math.round(v.height) : 0, v ? Math.round(v.offsetTop) : 0, top, typing ? 1 : 0].join(',');"""
        start = time.time()
        deadline = start + timeout
        last, since = None, start
        while time.time() < deadline:
            now = self.js(probe) or ""
            if now != last:
                last, since = now, time.time()
            elif time.time() - since >= quiet:
                shown = self.keyboard_shown()
                if shown is None or shown == now.endswith(",1") or time.time() - start >= keyboard_wait:
                    return
            time.sleep(0.1)

    def fill(self, selector: str, text: str) -> None:
        """Ввод текста с клавиатуры в поле. Многострочное поле на телефоне открывает окно письма
        во весь экран: текст печатается там и подтверждается «Готово» — как это делает сотрудник."""
        if not self.click(selector):
            raise DeviceError("поле %s не найдено на странице" % selector)
        where, deadline = "", time.time() + 3
        while time.time() < deadline:
            where = self.js("""const el = document.querySelector(arguments[0]), a = document.activeElement;
                if (a && a.closest && a.closest('.writer-root')) return 'writer';
                return a === el ? 'field' : '';""", selector)
            if where:
                break
            time.sleep(0.1)
        if where == "writer":
            self.js("const a = [...document.querySelectorAll('.writer-root .writer-area')].pop(); a.focus(); a.select();")
            self._page_call(lambda p: p.call("Input.insertText", {"text": text}))
            self.settle()
            if not self.click(".writer-root .writer-done", timeout=5):
                raise DeviceError("в окне письма нет кнопки «Готово»")
            if not self.wait_js("!document.querySelector('.writer-root')", 5):
                raise DeviceError("окно письма не закрылось после «Готово»")
            self.settle()
            return
        self.js("const el = document.querySelector(arguments[0]); if (document.activeElement !== el) el.focus();"
                " el.select && el.select();", selector)
        self._page_call(lambda p: p.call("Input.insertText", {"text": text}))
        self.settle()

    def press_enter(self) -> None:
        for kind in ("keyDown", "keyUp"):
            self._page_call(lambda p: p.call("Input.dispatchKeyEvent", {
                "type": kind, "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13,
                "nativeVirtualKeyCode": 13, "text": "\r" if kind == "keyDown" else ""}))

    def login(self, role: str, route: str = "#/tasks", theme: str = "light") -> None:
        """Вход ролью в ту же страницу приложения и переход в раздел."""
        api = self.world.api(role)
        self.world.set_theme(role, theme)
        self.js("""const a = arguments[0];
                   localStorage.setItem('kov.auth', JSON.stringify({ host: location.origin, token: a.token, userId: a.uid }));
                   localStorage.setItem('kov.theme', a.theme);
                   if (!sessionStorage.getItem('kov.autotest.offer')) localStorage.setItem('kov.notify.offer', String(Date.now()));
                   location.hash = a.route; setTimeout(() => location.reload(), 0);""",
                {"token": api.token, "uid": api.user_id, "theme": theme, "route": route})
        time.sleep(1.5)
        if not self.wait_js("document.querySelector('.app .content')", 40):
            raise DeviceError("после входа ролью %s интерфейс не появился" % role)

    def go(self, route: str) -> None:
        self.js("location.hash = arguments[0];", route)
        time.sleep(0.8)
        self.wait_js("document.querySelector('.app .content') && !document.querySelector('.content .spinner, .content .loading')", 20)

    def audit(self, t, name: str) -> dict:
        """Проверка вёрстки на экране телефона — тот же audit.js и те же правила, что у сайта."""
        cfg = self.world.ui["audit"]
        s = self.settings
        options = {"interactive": cfg["interactive"], "ignore": cfg["ignore"], "containers": cfg["containers"],
                   "allowOverflow": cfg["allow_overflow"], "textClip": cfg["text_clip"],
                   "touchMin": s.touch_min, "touchWarn": s.touch_warn, "mobile": True,
                   "allowPageScrollX": False, "overlays": cfg.get("overlays") or []}
        result = self.js("return (" + AUDIT_JS.read_text(encoding="utf-8") + ")(arguments[0]);", options) or {}
        for message in dict.fromkeys(result.get("errors") or []):
            t.fail("%s [телефон]: %s" % (name, message))
        for message in dict.fromkeys(result.get("warnings") or []):
            t.warn("%s [телефон]: %s" % (name, message))
        return result

    # ----- система телефона (Appium) -----

    def app_on_screen(self) -> bool:
        return self.driver.current_package == PKG

    def activate(self) -> None:
        self.driver.activate_app(PKG)
        time.sleep(2)

    def clear_app(self) -> None:
        """Стереть данные приложения — как после установки."""
        self._drop_page()
        self.driver.execute_script("mobile: clearApp", {"appId": PKG})

    def back(self) -> None:
        self.driver.back()

    def home(self) -> None:
        self.driver.press_keycode(3)
        time.sleep(1)

    def restart_app(self) -> None:
        self._drop_page()
        self.driver.terminate_app(PKG)
        self.driver.activate_app(PKG)
        time.sleep(3)

    def rotate(self, landscape: bool) -> None:
        self.driver.orientation = "LANDSCAPE" if landscape else "PORTRAIT"
        time.sleep(2)

    def permission_granted(self) -> bool:
        granted = self.driver.execute_script("mobile: getPermissions", {"type": "granted", "appPackage": PKG}) or []
        return "android.permission.POST_NOTIFICATIONS" in granted

    def set_permission(self, granted: bool) -> None:
        self.driver.execute_script("mobile: changePermissions", {
            "permissions": "android.permission.POST_NOTIFICATIONS", "appPackage": PKG,
            "action": "grant" if granted else "revoke"})

    def tap_native(self, texts, timeout: float = 15) -> bool:  # noqa: C901
        """Нажать системную кнопку/строку по id или тексту (ищется до timeout)."""
        from appium.webdriver.common.appiumby import AppiumBy
        deadline = time.time() + timeout
        while time.time() < deadline:
            for text in texts:
                if ":id/" in text:
                    found = self.driver.find_elements(AppiumBy.ID, text)
                else:
                    found = self.driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR,
                                                      'new UiSelector().textContains("%s")' % text.replace('"', '\\"'))
                if found:
                    found[0].click()
                    return True
            time.sleep(1)
        return False

    def allow_permission_dialog(self, timeout: float = 12) -> bool:
        """«Разрешить» в системном запросе. Кнопку ищем только в самом системном окне (пакет
        permissioncontroller: com.android… или com.google…), а не на странице приложения."""
        from appium.webdriver.common.appiumby import AppiumBy
        selectors = ('new UiSelector().resourceIdMatches(".*permissioncontroller:id/permission_allow_button")',
                     'new UiSelector().packageNameMatches(".*permissioncontroller.*").textMatches("(?i)^(Разрешить|Allow)$")')
        deadline = time.time() + timeout
        while time.time() < deadline:
            for selector in selectors:
                try:
                    found = self.driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, selector)
                    if found:
                        found[0].click()
                        return True
                except Exception as e:           # окно перерисовалось между поиском и нажатием — ещё раз
                    if "Stale" not in type(e).__name__:
                        raise
            time.sleep(0.7)
        return False

    def reset_permission_flags(self) -> None:
        """После отзыва — как у нового приложения: системный запрос может появиться снова."""
        self.shell("pm clear-permission-flags %s android.permission.POST_NOTIFICATIONS user-set user-fixed" % PKG)

    def wait_permission(self, timeout: float = 6) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.permission_granted():
                return True
            time.sleep(0.5)
        return False

    def push_state(self) -> dict:
        """Свежее состояние уведомлений из приложения (канал, подписка, причина)."""
        return self.js_async("""const done = arguments[arguments.length - 1];
            (window.KovNative && KovNative.call ? KovNative.call('push.state') : Promise.resolve(null))
              .then(st => done(st || {}), () => done({}));""") or {}

    def app_running(self) -> bool:
        return bool(self.shell("pidof %s" % PKG).strip())

    def ensure_app(self, t=None) -> None:
        """Перед тестом приложение должно работать. Если оно закрылось — сказать почему и поднять."""
        if self.app_running():
            return
        crash = self.crashes()
        text = ("приложение было закрыто перед этим тестом" +
                ((" — оно упало: " + crash[-1].splitlines()[0][:300]) if crash else " (падения в журнале нет)") +
                "; запускаю заново")
        self.log.warn(text)
        if t is not None:
            t.warn(text)
        self._drop_page()
        self.activate()

    def notifications(self) -> list:
        return parse_notifications(self.shell("dumpsys notification --noredact"))

    def wait_notification(self, predicate, timeout: float = 45):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for n in self.notifications():
                if predicate(n):
                    return n
            time.sleep(1.5)
        return None

    def open_shade(self) -> None:
        self.driver.open_notifications()
        time.sleep(1.5)

    def close_shade(self) -> None:
        self.driver.back()

    def resumed(self) -> str:
        return "%s/%s" % (self.driver.current_package, self.driver.current_activity)

    def screenshot(self, path: str) -> str:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.driver.get_screenshot_as_file(path)
        return path

    def crashes(self) -> list:
        """Падения приложения за прогон — из журнала аварий Android (logcat -b crash)."""
        text = self.shell("logcat -b crash -d")
        return [("FATAL EXCEPTION" + b).strip()[:1500] for b in text.split("FATAL EXCEPTION")[1:] if PKG in b]

    # ----- связь с сервером -----

    def link(self, on: bool) -> None:
        self._post("/link/on" if on else "/link/off")

    def close(self) -> None:
        self._drop_page()
        try:
            self._post("/link/on")
        except DeviceError:
            pass
        try:
            self.driver.quit()
        except Exception:
            pass
