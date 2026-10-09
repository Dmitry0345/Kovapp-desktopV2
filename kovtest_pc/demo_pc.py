# -*- coding: utf-8 -*-
"""Показ ПК-версии руками: настоящее приложение на сервере demo — том же, что у demo web.

    python kovtest_pc/demo_pc.py --project <папка ПК-версии> [--host http://localhost:18000] [--role director]
                                 [--theme light|dark] [--fullscreen] [--stop-file путь] [--log путь]

Запускают run.cmd demo pc и ./run.sh demo pc, когда сервер в Docker уже поднят и заполнен данными:
учётные записи те же, что у сайта (logs/demo-accounts.txt), и сайт в браузере работает одновременно
с программой. Без --role открывается окно входа. С --role вход сразу: роль — ключ из config/roles.yaml
(director, head_mont, worker_mont, …) или логин; пароль — FULL_ACCOUNT_PASSWORD, у администратора —
FULL_ADMIN_LOGIN и FULL_ADMIN_PASSWORD (из окна или .env, как у docker compose).
Настройки программы — во временной папке: настоящие настройки пользователя не трогаются.
--stop-file: появился файл — программа закрывается (так run.cmd закрывает её вместе с сервером).
--log: всё, что программа печатает, и ошибки — в этот файл (окна консоли у программы нет).
"""

import argparse
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--project", required=True, help="папка ПК-версии (project/pc)")
ap.add_argument("--host", default="", help="адрес сервера; по умолчанию http://localhost:<FULL_PUBLISH_PORT>")
ap.add_argument("--role", default="", help="войти сразу: роль из config/roles.yaml или логин; пусто — окно входа")
ap.add_argument("--theme", default="light", choices=["light", "dark"])
ap.add_argument("--fullscreen", action="store_true", help="во весь экран (по умолчанию — в окне)")
ap.add_argument("--stop-file", default="", help="закрыться, как только появится этот файл")
ap.add_argument("--log", default="", help="журнал программы")
A = ap.parse_args()
PROJECT = os.path.abspath(A.project)
STOP = os.path.abspath(A.stop_file) if A.stop_file else ""

# журнал: pythonw.exe работает без консоли — печать программы, предупреждения Qt и ошибки идут в файл
if A.log:
    _log = open(os.path.abspath(A.log), "w", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = _log
    for fd in (1, 2):
        try:
            os.dup2(_log.fileno(), fd)
        except OSError:
            pass
elif sys.stdout is None:
    sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")


def env_value(name, default):
    """Значение как у docker compose: переменная окна главнее строки .env, пустое — значение по умолчанию."""
    value = os.environ.get(name, "")
    if not value:
        try:
            with open(os.path.join(ROOT, ".env"), encoding="utf-8-sig") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith(name + "="):
                        value = line.split("=", 1)[1].strip()
                        if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
                            value = value[1:-1]
                        break
        except OSError:
            pass
    return value or default


def roles():
    """config/roles.yaml: ключ роли → логин и признак встроенного администратора."""
    out, key = {}, None
    try:
        with open(os.path.join(ROOT, "config", "roles.yaml"), encoding="utf-8-sig") as f:
            for line in f:
                m = re.match(r"^  ([A-Za-z0-9_]+):\s*$", line)
                if m:
                    key = m.group(1)
                    out[key] = {"login": key, "builtin": False}
                    continue
                m = re.match(r"^    (login|builtin):\s*(\S+)", line)
                if m and key:
                    out[key][m.group(1)] = m.group(2) if m.group(1) == "login" else m.group(2).lower() == "true"
    except OSError:
        pass
    return out


def account(role):
    """Логин и пароль роли на сервере demo — как их заводит сервер в Docker."""
    admin_login = env_value("FULL_ADMIN_LOGIN", "admin")
    specs = roles()
    spec = specs.get(role) or next((s for s in specs.values() if s["login"] == role), None)
    if (spec and spec["builtin"]) or role == admin_login:
        return admin_login, env_value("FULL_ADMIN_PASSWORD", "Admin-Autotest-2026")
    return (spec["login"] if spec else role), env_value("FULL_ACCOUNT_PASSWORD", "Autotest-2026!")


HOST = (A.host or "http://localhost:%s" % env_value("FULL_PUBLISH_PORT", "18000")).rstrip("/")
LOGIN, PASSWORD = account(A.role) if A.role else ("", "")
if STOP and os.path.exists(STOP):
    os.remove(STOP)          # остался от прошлого показа

# свой профиль настроек: настоящие настройки пользователя не трогаем, веб-движок кэшируется здесь же
home = tempfile.mkdtemp(prefix="kovapp-demo-")
# только LOCALAPPDATA: по нему программа находит settings.txt. Профиль Windows (USERPROFILE, HOME) не трогаем —
# от него Windows считает «Рабочий стол», «Загрузки» и др.: с подменой окно выбора файлов писало
# «Расположение недоступно … Desktop» и оставляло пустое окно
os.environ["LOCALAPPDATA"] = home
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox" if sys.platform.startswith("linux") else "")

# настройки программы — до импорта scaler (он читает их при импорте); адрес — сервера demo
os.makedirs(os.path.join(home, "KovApp"), exist_ok=True)
with open(os.path.join(home, "KovApp", "settings.txt"), "w", encoding="utf-8") as f:
    json.dump({"scale_multiplier": 1.0, "window_mode": "fullscreen" if A.fullscreen else "windowed",
               "host": HOST, "theme": A.theme, "last_login": LOGIN}, f)
os.chdir(PROJECT)
sys.path.insert(0, PROJECT)

import main as kov_main  # noqa: E402  — как при запуске программы: веб-движок до приложения, настройки
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

try:
    from PySide6.QtWebEngineCore import QWebEngineProfile  # noqa: E402
except ImportError:
    QWebEngineProfile = None

sys.excepthook = kov_main._keep_running      # ошибка в обработчике не закрывает программу — как у main.py
app = QApplication(sys.argv[:1])
app.setApplicationName("KovApp")
app.setOrganizationName("Kovapp")
app.setStyle("Fusion")
if QWebEngineProfile is not None:
    QWebEngineProfile.defaultProfile().setPersistentStoragePath(os.path.join(home, "webengine"))
    QWebEngineProfile.defaultProfile().setCachePath(os.path.join(home, "webcache"))

scaler = kov_main.scaler
scaler.init_scaler(app)
kov_main.theme.apply(scaler.get_setting("theme", "light"))
from kov.shell import app_icon  # noqa: E402
from kov.ui import apply_app_theme  # noqa: E402

apply_app_theme(app)
app.setWindowIcon(app_icon())
from admin_windows1.Authorization.login_window import LoginWindow  # noqa: E402

print("Показ ПК-версии Kovapp")
print("Сервер: %s (тот же, что у сайта %s/app/)" % (HOST, HOST))
print("Вход сразу: %s" % LOGIN if LOGIN else "Окно входа: учётные записи — в logs/demo-accounts.txt")
print("Настройки показа: %s" % home)
sys.stdout.flush()

win = LoginWindow()
if A.fullscreen:
    win.showFullScreen()
else:
    win.show()

if LOGIN:
    # входим настоящим путём приложения: заполняем поля и нажимаем «Войти»
    def do_login():
        win.login_input.setText(LOGIN)
        win.password_input.setText(PASSWORD)
        win.on_login_clicked()
    QTimer.singleShot(300, do_login)

if STOP:
    # run.cmd закончил показ — закрываемся вместе с сервером
    def poll_stop():
        if os.path.exists(STOP):
            print("Показ завершён из окна команды")
            app.quit()
    stop_timer = QTimer()
    stop_timer.setInterval(400)
    stop_timer.timeout.connect(poll_stop)
    stop_timer.start()

# служебное: снять окно и закрыться (для проверки демо из скрипта; в обычном показе переменных нет)
_shot = os.environ.get("KOVAPP_DEMO_SHOT")
if _shot:
    def _grab_quit():
        shown = [w for w in app.topLevelWidgets() if w.isVisible()]
        target = max(shown, key=lambda w: w.width() * w.height(), default=win)
        target.grab().save(_shot)
        app.quit()
    QTimer.singleShot(int(os.environ.get("KOVAPP_DEMO_SECONDS", "6")) * 1000, _grab_quit)

rc = app.exec()
sys.stdout.flush()
# сразу: фоновые запросы и веб-движок не держат процесс после закрытия окна
os._exit(rc)
