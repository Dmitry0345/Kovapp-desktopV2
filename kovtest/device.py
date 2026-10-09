"""Тесты на настоящем Android: эмулятор Android Studio или телефон по USB.

Приложение — настоящий APK (отладочная сборка). Страницу внутри него тесты видят
через отладку WebView — как chrome://inspect, теми же средствами Playwright, что
и сайт. Системные части телефона — через adb: шторка и уведомления, разрешения,
«Назад» и «Домой», запуск и закрытие приложения, связь с сервером (adb reverse),
«Загрузки», поворот экрана, снимки экрана.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

PKG = "ru.kovcheg.kovapp"
ACTIVITY = PKG + "/.MainActivity"
ACTION_OPEN = PKG + ".OPEN"
SERVICE_NOTIFICATION_ID = 1001          # Notifier.SERVICE_ID — «Kovapp на связи»


class DeviceError(Exception):
    """Понятная человеку причина, почему тесты на устройстве не могут идти."""


# ===== поиск инструментов =====

def _sdk_from_project(project_dir: str) -> str:
    lp = Path(project_dir or ".", "mobile", "local.properties")
    if lp.is_file():
        for line in lp.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith("sdk.dir="):
                # в local.properties Android Studio экранирует «:» и «\»
                return line.split("=", 1)[1].strip().replace("\\:", ":").replace("\\\\", "\\")
    return ""


def android_sdk(project_dir: str = "") -> str:
    for env in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(env) and Path(os.environ[env]).is_dir():
            return os.environ[env]
    for cand in (_sdk_from_project(project_dir),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Android", "Sdk") if os.environ.get("LOCALAPPDATA") else "",
                 "C:\\Android\\Sdk", os.path.expanduser("~/Android/Sdk"), os.path.expanduser("~/Library/Android/sdk")):
        if cand and Path(cand).is_dir():
            return cand
    return ""


def find_adb(explicit: str = "", project_dir: str = "") -> str:
    exe = "adb.exe" if os.name == "nt" else "adb"
    if explicit and Path(explicit).is_file():
        return explicit
    sdk = android_sdk(project_dir)
    if sdk and Path(sdk, "platform-tools", exe).is_file():
        return str(Path(sdk, "platform-tools", exe))
    found = shutil.which("adb")
    if found:
        return found
    raise DeviceError("не найден adb. Установите Android SDK (Android Studio → SDK Manager → «Android SDK Platform-Tools») "
                      "или укажите путь в .env: ADB_PATH=C:\\Android\\Sdk\\platform-tools\\adb.exe")


def find_java() -> str:
    """Java для сборки: JAVA_HOME или встроенная в Android Studio (jbr)."""
    exe = "java.exe" if os.name == "nt" else "java"
    cands = [os.environ.get("JAVA_HOME", "")]
    if os.name == "nt":
        cands += [r"C:\Program Files\Android\Android Studio\jbr",
                  os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Android Studio", "jbr")]
    cands += ["/Applications/Android Studio.app/Contents/jbr/Contents/Home", "/opt/android-studio/jbr",
              os.path.expanduser("~/android-studio/jbr")]
    for home in cands:
        if home and Path(home, "bin", exe).is_file():
            return home
    return ""


def find_gradle(mobile_dir: Path) -> list:
    """Gradle для сборки: обёртка проекта, скачанный Android Studio дистрибутив или gradle из PATH."""
    for name in (("gradlew.bat",) if os.name == "nt" else ("gradlew",)):
        if Path(mobile_dir, name).is_file():
            return [str(Path(mobile_dir, name))]
    wrapper = Path(mobile_dir, "gradle", "wrapper", "gradle-wrapper.properties")
    m = re.search(r"gradle-([\d.]+)-(bin|all)\.zip", wrapper.read_text(encoding="utf-8") if wrapper.is_file() else "")
    if m:
        home = Path(os.environ.get("GRADLE_USER_HOME") or Path.home() / ".gradle")
        exe = "gradle.bat" if os.name == "nt" else "gradle"
        for cand in sorted(Path(home, "wrapper", "dists", "gradle-%s-%s" % (m.group(1), m.group(2))).glob("*/gradle-%s/bin/%s" % (m.group(1), exe))):
            return [str(cand)]
    found = shutil.which("gradle")
    return [found] if found else []


# ===== adb =====

class Adb:
    def __init__(self, path: str, serial: str = ""):
        self.path = path
        self.serial = serial

    def run(self, *args, timeout: float = 60, check: bool = True, binary: bool = False):
        cmd = [self.path] + (["-s", self.serial] if self.serial else []) + [str(a) for a in args]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise DeviceError("adb %s: нет ответа за %d с" % (" ".join(map(str, args))[:80], timeout))
        if check and r.returncode != 0:
            err = (r.stderr or r.stdout).decode("utf-8", "replace").strip()
            raise DeviceError("adb %s: %s" % (" ".join(map(str, args))[:80], err[:300]))
        return r.stdout if binary else r.stdout.decode("utf-8", "replace")

    def shell(self, command: str, timeout: float = 60, check: bool = True) -> str:
        return self.run("shell", command, timeout=timeout, check=check)

    def devices(self) -> list:
        out = Adb(self.path).run("devices", timeout=20)
        rows = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                rows.append((parts[0], parts[1]))
        return rows


# ===== разбор вывода системных команд (проверяется без устройства) =====

def parse_notifications(dump: str, package: str = PKG) -> list:
    """Уведомления приложения из `dumpsys notification --noredact`."""
    found = []
    for block in dump.split("NotificationRecord(")[1:]:
        head = block.split("\n", 1)[0]
        if "pkg=%s " % package not in head and not head.rstrip(")").endswith("pkg=" + package):
            continue
        item = {"id": None, "tag": None, "channel": None, "title": "", "text": "", "ongoing": False}
        m = re.search(r"\bid=(-?\d+)", head)
        item["id"] = int(m.group(1)) if m else None
        m = re.search(r"\btag=(\S+)", head)
        item["tag"] = None if not m or m.group(1) == "null" else m.group(1)
        m = re.search(r"channel=([^\s)]+)", block)
        item["channel"] = m.group(1) if m else None
        m = re.search(r"flags=0x([0-9a-fA-F]+)", block)
        flags = int(m.group(1), 16) if m else 0
        item["ongoing"] = bool(flags & 0x2) or bool(flags & 0x40)
        m = re.search(r"android\.title=\w+ \((.*)\)\s*$", block, re.M)
        item["title"] = m.group(1) if m else ""
        m = re.search(r"android\.text=\w+ \((.*)\)\s*$", block, re.M)
        item["text"] = m.group(1) if m else ""
        found.append(item)
    return found


def parse_ui(xml_text: str) -> list:
    """Элементы экрана из `uiautomator dump`: текст, описание, id, границы."""
    nodes = []
    try:
        root = ET.fromstring(xml_text[xml_text.find("<"):]) if "<" in xml_text else None
    except ET.ParseError:
        return nodes
    if root is None:
        return nodes
    for n in root.iter("node"):
        m = re.match(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", n.get("bounds", ""))
        if not m:
            continue
        x1, y1, x2, y2 = map(int, m.groups())
        nodes.append({"text": n.get("text", ""), "desc": n.get("content-desc", ""), "id": n.get("resource-id", ""),
                      "cls": n.get("class", ""), "pkg": n.get("package", ""), "clickable": n.get("clickable") == "true",
                      "center": ((x1 + x2) // 2, (y1 + y2) // 2), "bounds": (x1, y1, x2, y2)})
    return nodes


def parse_resumed(dump: str) -> str:
    """Какое окно сейчас на экране: `dumpsys activity activities`."""
    for key in ("topResumedActivity", "mResumedActivity", "ResumedActivity"):
        m = re.search(key + r"\S*[:=]\s*ActivityRecord\{\S+ \S+ (\S+)", dump)
        if m:
            return m.group(1)
    return ""


def parse_permission(dump: str, permission: str = "android.permission.POST_NOTIFICATIONS") -> str:
    """granted / denied по `dumpsys package` (runtime permissions)."""
    m = re.search(re.escape(permission) + r": granted=(true|false)", dump)
    if not m:
        return "unknown"
    return "granted" if m.group(1) == "true" else "denied"


# ===== окружение устройства =====

class _Listeners:
    """Страница WebView одна на все тесты: запоминает подписки KovPage, чтобы снять их после теста."""

    def __init__(self, page):
        self._page = page
        self._subs = []

    def on(self, event, handler):
        self._subs.append((event, handler))
        return self._page.on(event, handler)

    def detach(self):
        for event, handler in self._subs:
            try:
                self._page.remove_listener(event, handler)
            except Exception:
                pass
        self._subs = []

    def __getattr__(self, name):
        return getattr(self._page, name)


class DeviceEnv:
    def __init__(self, world):
        self.world = world
        self.settings = s = world.settings
        self.log = world.log
        self.adb = Adb(find_adb(s.adb_path, s.project_dir))
        devices = [d for d in self.adb.devices() if d[1] == "device"]
        if s.device_serial:
            if not any(d[0] == s.device_serial for d in devices):
                raise DeviceError("устройство %s не подключено (adb devices: %s)" % (s.device_serial, devices or "пусто"))
            self.adb.serial = s.device_serial
        elif len(devices) == 1:
            self.adb.serial = devices[0][0]
        elif not devices:
            raise DeviceError("нет подключённого устройства: запустите эмулятор (Device Manager → ▶) "
                              "или подключите телефон с «Отладкой по USB»")
        else:
            raise DeviceError("подключено несколько устройств %s — укажите нужное в .env: DEVICE_SERIAL=…" % [d[0] for d in devices])
        self.sdk = int(self.adb.shell("getprop ro.build.version.sdk").strip() or 0)
        self.release = self.adb.shell("getprop ro.build.version.release").strip()
        self.model = self.adb.shell("getprop ro.product.model").strip()
        self.gms = bool(self.adb.shell("pm path com.google.android.gms", check=False).strip())
        self.server_port = urlparse(s.base_url).port or (443 if s.base_url.startswith("https") else 80)
        # сервер в Docker: телефон ходит на опубликованный порт ПК (адрес server:8000 ему не виден)
        if s.device_host_port:
            self.server_port = s.device_host_port
        # adb на ПК (тесты в Docker): перенаправления портов открываются на ПК, а не в контейнере
        socket_spec = os.environ.get("ADB_SERVER_SOCKET", "")
        self.cdp_host = socket_spec.split(":")[1] if socket_spec.startswith("tcp:") and socket_spec.count(":") >= 2 else "127.0.0.1"
        self.device_port = s.device_port
        self.live = None                    # отладочный сервер веб-файлов (DEVICE_LIVE_WEB)
        self._browser = None
        self._page = None
        self._page_pid = None
        self._forward = None
        self._current = None
        if s.device_live_web:
            self.start_live_web()           # до сборки: её адрес попадает в отладочный APK
        self.reverse_on()
        self.app_version = self.ensure_app()
        self.log.info("устройство: %s, Android %s (API %d), сервисы Google: %s; приложение %s → сервер %s"
                      % (self.model, self.release, self.sdk, "есть" if self.gms else "нет", self.app_version, s.base_url))

    # ----- связь телефона с сервером -----

    def reverse_on(self) -> None:
        """localhost:8000 телефона → сервер под тестом (и отладочный сервер веб-файлов)."""
        self.adb.run("reverse", "tcp:%d" % self.device_port, "tcp:%d" % self.server_port)
        if self.live is not None:
            self.adb.run("reverse", "tcp:%d" % self.live_port, "tcp:%d" % self.live_port)

    def reverse_off(self) -> None:
        """Сервер для телефона «пропал» — так проверяется работа без связи."""
        self.adb.run("reverse", "--remove", "tcp:%d" % self.device_port, check=False)

    # ----- приложение: установка и сборка -----

    def installed_version(self) -> str:
        out = self.adb.shell("dumpsys package %s" % PKG, check=False)
        m = re.search(r"versionName=(\S+)", out)
        return m.group(1) if m else ""

    def debuggable(self) -> bool:
        out = self.adb.shell("dumpsys package %s" % PKG, check=False)
        return "DEBUGGABLE" in out

    def ensure_app(self) -> str:
        s = self.settings
        mode = s.device_app
        if mode == "auto":
            if s.device_apk:
                mode = "apk"
            elif s.project_dir and Path(s.project_dir, "mobile", "app", "build.gradle").is_file() and find_java():
                mode = "build"
            else:
                mode = "installed"
        if mode == "apk":
            self.install(s.device_apk)
        elif mode == "build":
            self.install(self.build())
        if not self.installed_version():
            raise DeviceError("приложение Kovapp не установлено на устройстве: нажмите ▶ Run в Android Studio "
                              "или укажите в .env DEVICE_APP=build (собрать из проекта) либо DEVICE_APK=путь к APK")
        if not self.debuggable():
            raise DeviceError("установлена рабочая (release) сборка: тестам нужна отладочная (debug) — в ней включена отладка WebView")
        return self.installed_version()

    def install(self, apk: str) -> None:
        if not apk or not Path(apk).is_file():
            raise DeviceError("нет файла APK: %s" % apk)
        self.log.info("устройство: устанавливаю %s" % apk)
        out = self.adb.run("install", "-r", "-t", "-d", apk, timeout=300, check=False)
        if "UPDATE_INCOMPATIBLE" in out or "SIGNATURES_DO_NOT_MATCH" in out:
            # стоит сборка с другой подписью (например, из Android Studio) — ставим свою начисто
            self.log.info("устройство: у установленной версии другая подпись — переустанавливаю")
            self.adb.run("uninstall", PKG, timeout=120, check=False)
            out = self.adb.run("install", "-t", apk, timeout=300, check=False)
        if "Success" not in out:
            raise DeviceError("APK не установился: %s" % out.strip()[-300:])

    def _sources_hash(self, project: Path) -> str:
        h = hashlib.sha1()
        roots = [project / "mobile" / "app" / "src", project / "mobile" / "overlay", project / "web_app"]
        files = [project / "mobile" / "app" / "build.gradle", project / "mobile" / "build.gradle",
                 project / "mobile" / "settings.gradle", project / "mobile" / "kovapp.properties"]
        for root in roots:
            if root.is_dir():
                # сжатые копии статики создаёт сервер при каждом запуске — на сборку APK они не влияют
                files += [p for p in root.rglob("*") if p.is_file() and p.suffix not in (".gz", ".br")]
        for f in sorted(files):
            if f.is_file():
                st = f.stat()
                h.update(("%s|%d|%d\n" % (f.relative_to(project), st.st_size, int(st.st_mtime))).encode("utf-8"))
        h.update(("live=%s" % self.settings.device_live_web).encode())
        return h.hexdigest()

    def build(self) -> str:
        """Отладочный APK из проекта; собирается заново, только если код изменился."""
        project = Path(self.settings.project_dir)
        mobile = project / "mobile"
        apk = mobile / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
        mark = mobile / "app" / "build" / "kovtest-build.json"
        digest = self._sources_hash(project)
        try:
            if apk.is_file() and json.loads(mark.read_text(encoding="utf-8")).get("hash") == digest:
                self.log.info("устройство: проект не менялся — пересборка не нужна")
                return str(apk)
        except (OSError, ValueError):
            pass
        java, gradle, sdk = find_java(), find_gradle(mobile), android_sdk(str(project))
        if not java:
            raise DeviceError("для сборки нужна Java 17+: установите Android Studio (у неё встроенная) или задайте JAVA_HOME")
        if not gradle:
            raise DeviceError("не найден Gradle: откройте проект mobile в Android Studio один раз (она скачает Gradle) "
                              "или соберите APK там и запустите тесты с DEVICE_APP=installed")
        if not sdk:
            raise DeviceError("не найден Android SDK: задайте ANDROID_HOME или откройте проект в Android Studio")
        if not (mobile / "kovapp.properties").is_file():
            raise DeviceError("в проекте нет mobile/kovapp.properties — скопируйте kovapp.properties.example")
        args = gradle + ["-p", str(mobile), "assembleDebug", "--console=plain",
                         "-Pkovapp.debugServerOrigin=http://localhost:%d" % self.device_port]
        if self.settings.device_live_web:
            args.append("-Pkovapp.devAssetsUrl=http://localhost:%d" % self.live_port)
        env = dict(os.environ, JAVA_HOME=java, ANDROID_HOME=sdk)
        self.log.info("устройство: собираю отладочный APK (первый раз — несколько минут)…")
        started = time.time()
        r = subprocess.run(args, cwd=str(mobile), env=env, capture_output=True, timeout=1800)
        out = (r.stdout + r.stderr).decode("utf-8", "replace")
        Path(self.log.dir, "device-build.log").write_text(out, encoding="utf-8")
        if r.returncode != 0 or not apk.is_file():
            tail = "\n".join(l for l in out.splitlines() if "error" in l.lower() or "what went wrong" in l.lower())[-800:]
            raise DeviceError("сборка APK не удалась (журнал — device-build.log):\n%s" % (tail or out[-800:]))
        mark.write_text(json.dumps({"hash": digest, "at": time.time()}), encoding="utf-8")
        self.log.info("устройство: APK собран за %d с" % (time.time() - started))
        return str(apk)

    @property
    def live_port(self) -> int:
        return self.settings.device_live_port

    def start_live_web(self) -> None:
        """Веб-файлы прямо из проекта: правки веб-версии видны без пересборки APK."""
        if self.live is not None or not self.settings.device_live_web:
            return
        devserver = Path(self.settings.project_dir, "mobile", "tools", "devserver.py")
        if not devserver.is_file():
            raise DeviceError("для DEVICE_LIVE_WEB нужна папка проекта с mobile/tools/devserver.py")
        import sys
        self.live = subprocess.Popen([sys.executable, str(devserver), "--port", str(self.live_port),
                                      "--upstream", self.settings.base_url],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.5)

    # ----- приложение: запуск и управление -----

    def pid(self) -> int:
        out = self.adb.shell("pidof %s" % PKG, check=False).strip().split()
        return int(out[0]) if out and out[0].isdigit() else 0

    def start(self, route: str = "", wait: float = 25) -> float:
        """Запуск приложения (с переходом в раздел, как из уведомления). Возвращает время запуска, мс."""
        if route:
            out = self.adb.shell("am start -W -a %s -n %s --es url '%s'" % (ACTION_OPEN, ACTIVITY, route), timeout=60)
        else:
            out = self.adb.shell("am start -W -n %s" % ACTIVITY, timeout=60)
        m = re.search(r"TotalTime:\s*(\d+)", out)
        deadline = time.time() + wait
        while time.time() < deadline and not self.pid():
            time.sleep(0.3)
        return float(m.group(1)) if m else 0.0

    def stop(self) -> None:
        self.adb.shell("am force-stop %s" % PKG, check=False)
        self._drop_page()

    def clear_data(self) -> None:
        self.adb.shell("pm clear %s" % PKG, check=False)
        self._drop_page()

    def key(self, code: str) -> None:
        self.adb.shell("input keyevent %s" % code)

    def back(self) -> None:
        self.key("KEYCODE_BACK")

    def home(self) -> None:
        self.key("KEYCODE_HOME")

    def resumed(self) -> str:
        return parse_resumed(self.adb.shell("dumpsys activity activities", check=False))

    def app_on_screen(self) -> bool:
        return self.resumed().startswith(PKG + "/")

    def rotate(self, landscape: bool) -> None:
        self.adb.shell("settings put system accelerometer_rotation 0", check=False)
        self.adb.shell("settings put system user_rotation %d" % (1 if landscape else 0), check=False)

    def night(self, on: bool) -> None:
        self.adb.shell("cmd uimode night %s" % ("yes" if on else "no"), check=False)

    # ----- разрешения и уведомления -----

    def notification_permission(self) -> str:
        if self.sdk < 33:
            out = self.adb.shell("cmd notification allowed_listeners", check=False)  # до Android 13 разрешения нет
            return "granted"
        return parse_permission(self.adb.shell("dumpsys package %s" % PKG, check=False))

    def set_notification_permission(self, granted: bool) -> None:
        if self.sdk >= 33:
            self.adb.shell("pm %s %s android.permission.POST_NOTIFICATIONS" % ("grant" if granted else "revoke", PKG), check=False)

    def notifications(self) -> list:
        return parse_notifications(self.adb.shell("dumpsys notification --noredact", check=False, timeout=60))

    def wait_notification(self, predicate, timeout: float = 30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for n in self.notifications():
                if predicate(n):
                    return n
            time.sleep(1)
        return None

    def open_shade(self) -> None:
        self.adb.shell("cmd statusbar expand-notifications", check=False)
        time.sleep(1.2)

    def close_shade(self) -> None:
        self.adb.shell("cmd statusbar collapse", check=False)
        time.sleep(0.6)

    # ----- экран: элементы и нажатия -----

    def ui(self) -> list:
        for _ in range(4):
            self.adb.shell("uiautomator dump /sdcard/kovtest-ui.xml", check=False, timeout=30)
            text = self.adb.run("exec-out", "cat", "/sdcard/kovtest-ui.xml", check=False, timeout=30)
            nodes = parse_ui(text)
            if nodes:
                return nodes
            time.sleep(0.8)
        return []

    def find(self, pattern: str, timeout: float = 10):
        rx = re.compile(pattern, re.I)
        deadline = time.time() + timeout
        while time.time() < deadline:
            for n in self.ui():
                if rx.search(n["text"]) or rx.search(n["desc"]) or rx.search(n["id"]):
                    return n
            time.sleep(0.8)
        return None

    def tap(self, x: int, y: int) -> None:
        self.adb.shell("input tap %d %d" % (x, y))

    def tap_text(self, pattern: str, timeout: float = 10) -> bool:
        node = self.find(pattern, timeout)
        if node is None:
            return False
        self.tap(*node["center"])
        return True

    def allow_permission_dialog(self, timeout: float = 10) -> bool:
        return self.tap_text(r"permission_allow_button|^Разрешить$|^Allow$|^Разрешать$", timeout)

    def screenshot(self, path: str) -> str:
        data = self.adb.run("exec-out", "screencap", "-p", binary=True, timeout=30)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(data)
        return path

    # ----- журнал: падения приложения -----

    def logcat_clear(self) -> None:
        self.adb.run("logcat", "-c", check=False)

    def crashes(self) -> list:
        out = self.adb.run("logcat", "-d", "-b", "crash", check=False, timeout=30)
        return [block.strip()[:1500] for block in out.split("FATAL EXCEPTION")[1:] if PKG in block]

    # ----- страница внутри WebView -----

    def _drop_page(self) -> None:
        if self._current is not None:
            self._current.detach()
            self._current = None
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
        self._browser = self._page = self._page_pid = None
        if self._forward:
            self.adb.run("forward", "--remove", "tcp:%d" % self._forward, check=False)
            self._forward = None

    def page(self, timeout: float = 30):
        """Страница приложения через отладку WebView — обычная страница Playwright."""
        pid = self.pid()
        if not pid:
            self.start()
            pid = self.pid()
        if self._page is not None and self._page_pid == pid and not self._page.is_closed():
            return self._page
        self._drop_page()
        socket_name = "webview_devtools_remote_%d" % pid
        deadline = time.time() + timeout
        while time.time() < deadline:
            if socket_name in self.adb.shell("cat /proc/net/unix", check=False):
                break
            time.sleep(0.5)
        else:
            raise DeviceError("отладка WebView недоступна (%s): нужна отладочная сборка приложения" % socket_name)
        self._forward = int(self.adb.run("forward", "tcp:0", "localabstract:" + socket_name).strip().splitlines()[-1])
        pw = self.world.browsers.pw
        errors = []
        for endpoint in ("http://%s:%d" % (self.cdp_host, self._forward), "ws://%s:%d/devtools/browser" % (self.cdp_host, self._forward)):
            try:
                self._browser = pw.chromium.connect_over_cdp(endpoint, timeout=20000)
                break
            except Exception as e:
                errors.append(str(e).splitlines()[0][:200])
        if self._browser is None:
            raise DeviceError("не удалось подключиться к WebView: %s" % "; ".join(errors))
        while time.time() < deadline:
            for context in self._browser.contexts:
                for p in context.pages:
                    if "/app/" in p.url or "index.html" in p.url:
                        self._page, self._page_pid = p, pid
                        p.set_default_timeout(self.settings.timeout_ms)
                        return p
            time.sleep(0.5)
        raise DeviceError("в WebView нет страницы приложения")

    def listened_page(self):
        """Та же страница, но подписки прошлого теста сняты (для KovPage)."""
        if self._current is not None:
            self._current.detach()
        self._current = _Listeners(self.page())
        return self._current

    def origin(self) -> str:
        return "http://localhost:%d" % self.device_port

    def close(self) -> None:
        self._drop_page()
        if self.live is not None:
            self.live.terminate()
            self.live = None
        self.adb.run("reverse", "--remove-all", check=False)
