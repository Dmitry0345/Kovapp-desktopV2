# -*- coding: utf-8 -*-
"""Прогон ПК-версии Kovapp в этом же процессе: тестовый сервер, вход, шаги по таймеру.

    python kovtest_pc/harness.py --project <папка ПК-версии> --plan sections --role director --theme light --out <папка>

Приложение запускается так же, как main.py (масштаб, тема, окно входа), но настройки
и кэш пишутся во временную папку прогона — настоящие настройки пользователя не трогаются.
Шаги плана (kovtest_pc/plans/*.py) идут по очереди через QTimer: окна, открытые exec(),
не останавливают прогон. Итог — results.json, журнал и снимки в папке --out.
"""

import argparse
import importlib.util
import json
import os
import sys
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE = os.path.dirname(HERE)


def parse():
    ap = argparse.ArgumentParser(description="ПК-версия Kovapp: прогон плана проверок")
    ap.add_argument("--project", required=True, help="папка ПК-версии (main.py, kov/)")
    ap.add_argument("--plan", required=True, help="имя плана из kovtest_pc/plans: sections, messenger")
    ap.add_argument("--role", default="director", help="логин тестового сервера: director, head, worker, montage, stock…")
    ap.add_argument("--theme", default="light", choices=["light", "dark"])
    ap.add_argument("--out", required=True, help="папка результатов")
    ap.add_argument("--size", default="1600x1000", help="размер окна для снимков")
    ap.add_argument("--timeout", type=int, default=600, help="предел времени прогона, секунд")
    return ap.parse_args()


A = parse()
OUT = os.path.abspath(A.out)
SHOTS = os.path.join(OUT, "shots")
os.makedirs(SHOTS, exist_ok=True)
WIN_W, WIN_H = (int(x) for x in A.size.lower().split("x"))

# ── окружение: свои настройки, тестовый сервер ──────────────────────────────
home = os.path.join(OUT, "home")
os.makedirs(home, exist_ok=True)
os.environ["HOME"] = home
os.environ["USERPROFILE"] = home
os.environ["LOCALAPPDATA"] = home           # Windows: настройки приложения — здесь, а не в профиле
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox" if sys.platform.startswith("linux") else "")
PROJECT = os.path.abspath(A.project)
sys.path.insert(0, os.path.join(HERE, "mock"))
sys.path.insert(0, PROJECT)

import pc_mock  # noqa: E402

srv, HOST = pc_mock.start()
STATE = srv.state
LOG = open(os.path.join(OUT, "run.log"), "a", encoding="utf-8")


def log(text):
    LOG.write("%s %s\n" % (time.strftime("%H:%M:%S"), text))
    LOG.flush()


import urllib.parse  # noqa: E402
import urllib.request  # noqa: E402

login = json.load(urllib.request.urlopen(urllib.request.Request(
    HOST + "/login", data=urllib.parse.urlencode({"username": A.role, "password": "test"}).encode(), method="POST")))
for key in ("web.settings.pc",):           # тема сайта на сервере — как выбранная: без перезапуска после входа
    urllib.request.urlopen(urllib.request.Request(
        HOST + "/preferences/" + key, data=json.dumps({"value": {"theme": A.theme, "scale": 1}}).encode(), method="PUT",
        headers={"Authorization": "Bearer " + login["access_token"], "Content-Type": "application/json"})).read()

# настройки приложения (адрес сервера, тема, окно) — до загрузки scaler: он читает файл при импорте
os.makedirs(os.path.join(home, "KovApp"), exist_ok=True)
with open(os.path.join(home, "KovApp", "settings.txt"), "w", encoding="utf-8") as f:
    json.dump({"scale_multiplier": 1.0, "window_mode": "windowed", "host": HOST, "theme": A.theme}, f)
os.chdir(PROJECT)
from PySide6.QtCore import QCoreApplication, QPoint, Qt, QTimer  # noqa: E402

QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
try:  # как main.py: веб-движок — до создания приложения
    from PySide6 import QtWebEngineWidgets  # noqa: E402,F401
    from PySide6.QtWebEngineCore import QWebEngineProfile  # noqa: E402
except ImportError:      # сборка Qt без веб-движка: мессенджер не проверить, остальное — можно
    QWebEngineProfile = None
from PySide6.QtGui import QCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv[:1])
app.setApplicationName("KovApp")
app.setStyle("Fusion")
# веб-движок мессенджера хранит данные страницы в папке прогона (в Windows иначе — в общем профиле пользователя)
if QWebEngineProfile is not None:
    QWebEngineProfile.defaultProfile().setPersistentStoragePath(os.path.join(home, "webengine"))
    QWebEngineProfile.defaultProfile().setCachePath(os.path.join(home, "webcache"))
_avail = app.primaryScreen().availableGeometry()
WIN_ASKED = (WIN_W, WIN_H)
if _avail.width() < WIN_W or _avail.height() < WIN_H:      # экран меньше окна снимков: окно по экрану
    WIN_W, WIN_H = min(WIN_W, _avail.width()), min(WIN_H, _avail.height() - 40)
import admin_windows1.scaler as scaler  # noqa: E402

scaler.init_scaler(app)
from general_functions import theme  # noqa: E402

theme.apply(A.theme)
from kov.ui import apply_app_theme  # noqa: E402

apply_app_theme(app)
_scr = app.primaryScreen().geometry()
QCursor.setPos(QPoint(_scr.right() - 1, _scr.bottom() - 1))     # указатель в углу: без подсветки наведения на снимках


# ── что пошло не так: исключения, сбои фоновых запросов, сообщения об ошибках ─
class Watch:
    def __init__(self):
        self.lock = threading.Lock()
        self.uncaught, self.jobs, self.toasts = [], [], []

    def mark(self):
        with self.lock:
            return len(self.uncaught), len(self.jobs), len(self.toasts)

    def since(self, mark):
        with self.lock:
            return self.uncaught[mark[0]:], self.jobs[mark[1]:], self.toasts[mark[2]:]


WATCH = Watch()


def excepthook(t, v, tb):
    text = "".join(traceback.format_exception(t, v, tb))
    with WATCH.lock:
        WATCH.uncaught.append(text)
    log("UNCAUGHT\n" + text)


sys.excepthook = excepthook

from kov import jobs as _jobs  # noqa: E402
from kov.api import ApiError  # noqa: E402

_orig_run = _jobs._Job.run


def _emit(signal, value):
    try:
        signal.emit(value)
    except RuntimeError:          # программу уже закрыли: PySide6 удалил сигналы вместе с окнами
        pass


def _job_run(self):
    name = getattr(self.fn, "__qualname__", None) or getattr(self.fn, "__name__", None) or repr(self.fn)
    t0 = time.time()
    try:
        result = self.fn(*self.args, **self.kwargs)
    except Exception as exc:
        exc.trace = traceback.format_exc()
        mock_gap = isinstance(exc, ApiError) and exc.status == 404 and "тестовом сервере" in str(exc)
        with WATCH.lock:
            WATCH.jobs.append({"fn": name, "error": "%s: %s" % (type(exc).__name__, exc), "mock_gap": mock_gap,
                               "api": isinstance(exc, ApiError), "status": getattr(exc, "status", None),
                               "args": repr(self.args)[:300]})
        log("JOB %s %s: %s" % (name, type(exc).__name__, exc))
        _emit(self.signals.failed, exc)
    else:
        _emit(self.signals.done, result)
    if time.time() - t0 > 20:
        log("SLOW JOB %s: %.0f с, %s" % (name, time.time() - t0, repr(self.args)[:200]))


_jobs._Job.run = _job_run

from kov import notify_ui as _nui  # noqa: E402

_orig_show = _nui.show


def _show(title, body=None, where=None, kind="notification", **kw):
    with WATCH.lock:
        WATCH.toasts.append({"title": str(title or ""), "body": str(body or ""), "kind": kind})
    log("TOAST [%s] %s %s" % (kind, title, body or ""))
    return _orig_show(title, body, where, kind, **kw)


_nui.show = _show


# ── контекст для плана ───────────────────────────────────────────────────────
class Ctx:
    AGAIN = "again"         # шаг вернул это — повторить его через ту же паузу (ожидание фоновой работы)

    def __init__(self):
        self.app, self.host, self.role, self.theme, self.out = app, HOST, A.role, A.theme, OUT
        self.state = STATE
        self.M, self.L = STATE.M, STATE.L
        self.user_id = login["user_id"]
        self.media = os.path.join(HERE, "media")
        self.fixtures = os.path.join(HERE, "fixtures")
        self.results = []
        self.R = {}
        self.data = {}
        self.login = None
        # окно меньше заданного (экран мал или крупный масштаб Windows) — проверки «влезает без прокрутки» не строгие
        self.window, self.window_asked = (WIN_W, WIN_H), WIN_ASKED
        self.shell = None
        self.watch = WATCH
        self.mark = WATCH.mark()

    # проверки
    def check(self, name, ok, detail="", warn=False):
        status = "passed" if ok else ("warned" if warn else "failed")
        self.results.append({"name": name, "status": status, "detail": str(detail)[:600]})
        log("CHECK %s %s | %s" % (status.upper(), name, str(detail)[:600]))

    def clean(self, name, allow_toasts=(), expected=()):
        """Со времени прошлой отметки: ни исключений, ни сбоев запросов (кроме «нет на тестовом сервере»), ни ошибок.
        expected — части адресов, отказ по которым для этой роли ожидаем (склад без доступа и т. п.)."""
        unc, jobs, toasts = WATCH.since(self.mark)
        self.mark = WATCH.mark()
        bad_jobs = [j for j in jobs if not j["mock_gap"] and not (j["api"] and j["status"] in (403, 404, 409, 422))]
        gaps = [j for j in jobs if j["mock_gap"]]
        api = [j for j in jobs if not j["mock_gap"] and j["api"] and j["status"] in (403, 404, 409, 422)
               and not any(e in j.get("args", "") for e in expected)]
        errors = [t for t in toasts if t["kind"] == "warning" and not any(a in t["title"] for a in allow_toasts)]
        self.check(name + ": без ошибок Python", not unc, unc[-1].strip().splitlines()[-1] if unc else "")
        self.check(name + ": фоновые запросы без сбоев", not bad_jobs, "; ".join(j["fn"] + " — " + j["error"] for j in bad_jobs[:3]))
        if api:
            self.check(name + ": ответы сервера с отказом", False, "; ".join(j["fn"] + " — " + j["error"] for j in api[:3]), warn=True)
        if gaps:
            self.check(name + ": адреса, которых нет у тестового сервера", False,
                       "; ".join(j["fn"] + " — " + j["error"] for j in gaps[:3]), warn=True)
        self.check(name + ": без предупреждений на экране", not errors, "; ".join(t["title"] for t in errors[:3]))

    def layout(self, name, widget=None, bottom=None):
        """Вёрстка открытого экрана: текст выпадающих списков не обрезан; bottom — нижняя панель
        раздела (последний элемент) стоит у нижнего края, а не висит посередине."""
        from PySide6.QtWidgets import QComboBox, QStyle, QStyleOptionComboBox
        w = widget or (self.shell.current if self.shell is not None else None)
        if w is None:
            return
        cut = []
        for cb in w.findChildren(QComboBox):
            if not cb.isVisible() or cb.isEditable() or not cb.currentText():
                continue
            opt = QStyleOptionComboBox()
            cb.initStyleOption(opt)
            field = cb.style().subControlRect(QStyle.CC_ComboBox, opt, QStyle.SC_ComboBoxEditField, cb)
            need = cb.fontMetrics().horizontalAdvance(cb.currentText()) + 2
            if field.width() < need:
                cut.append("«%s» %d из %d px" % (cb.currentText(), field.width(), need))
        self.check(name + ": текст в выпадающих списках не обрезан", not cut, "; ".join(cut[:3]))
        if bottom:
            lay = w.layout()
            item = lay.itemAt(lay.count() - 1) if lay is not None and lay.count() else None
            if item is not None:
                gap = w.height() - lay.contentsMargins().bottom() - item.geometry().bottom() - 1
                self.check(name + ": нижняя панель у нижнего края", abs(gap) <= scaler.rem(0.6), "зазор %d px" % gap)

    # снимки
    def shot(self, name, widget=None):
        w = widget or self.shell or self.login
        path = os.path.join(SHOTS, "%s_%s_%s.png" % (self.theme, self.role, name))
        w.grab().save(path)
        log("shot %s" % path)
        return path

    def shot_screen(self, name):
        """Весь экран: с окнами поверх главного (диалоги, карточки)."""
        app.processEvents()             # отложенная перерисовка — до снимка, иначе на нём прежний кадр
        path = os.path.join(SHOTS, "%s_%s_%s.png" % (self.theme, self.role, name))
        g = (self.shell or self.login).frameGeometry()
        app.primaryScreen().grabWindow(0, g.x(), g.y(), g.width(), g.height()).save(path)
        log("shot %s" % path)
        return path

    # приложение
    def do_login(self):
        self.login.login_input.setText(A.role)
        self.login.password_input.setText("test")
        self.login.on_login_clicked()

    def find_shell(self):
        from kov.shell import Shell
        for w in app.topLevelWidgets():
            if isinstance(w, Shell) and w.isVisible():
                self.shell = w
                w.showNormal()
                w.resize(WIN_W, WIN_H)
                w.move(_avail.topLeft())
                return w
        return None

    def be(self):
        return self.shell.messenger_host().backend

    def js(self, code):
        self.shell.messenger_host().run_js(code)

    def ask(self, key, expr):
        self.R.pop(key, None)
        self.shell.messenger_host().run_js(expr, lambda v, k=key: self.R.__setitem__(k, v))

    @staticmethod
    def rem(v):
        return scaler.rem(v)

    @staticmethod
    def top():
        return QApplication.activeModalWidget()

    @staticmethod
    def button(widget, text):
        from PySide6.QtWidgets import QPushButton
        if widget is None:
            return None
        return next((b for b in widget.findChildren(QPushButton) if b.text().strip() == text and b.isVisible()), None)

    def click(self, widget, text):
        b = self.button(widget, text)
        if b is None:
            raise RuntimeError("нет кнопки «%s»" % text)
        b.click()

    def later(self, items):
        """[(мс, функция(ctx))] — таймеры идут и внутри окон exec()."""
        for ms, fn in items:
            QTimer.singleShot(ms, lambda fn=fn: self._safe(fn))

    def _safe(self, fn):
        try:
            fn(self)
        except Exception:
            text = traceback.format_exc()
            log("ERROR in later:\n" + text)
            self.check("шаг по таймеру: %s" % getattr(fn, "__name__", "?"), False, text.strip().splitlines()[-1])

    def close_modals(self):
        for _ in range(4):
            m = QApplication.activeModalWidget()
            if m is None:
                return
            try:
                m.reject()
            except Exception:
                return


ctx = Ctx()


def load_plan(name):
    path = os.path.join(HERE, "plans", name + ".py")
    spec = importlib.util.spec_from_file_location("kov_pc_plan_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


plan = load_plan(A.plan)
steps = plan.steps(ctx)
started = time.time()


def finish(reason=""):
    if reason:
        ctx.check("прогон плана %s" % A.plan, False, reason)
    unc, jobs, _t = WATCH.since((0, 0, 0))
    result = {"plan": A.plan, "role": A.role, "theme": A.theme, "seconds": round(time.time() - started, 1),
              "checks": ctx.results, "uncaught": unc, "job_failures": jobs}
    with open(os.path.join(OUT, "results.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    log("done: %d проверок" % len(ctx.results))
    app.quit()


def run(i=0, attempt=0):
    if time.time() - started > A.timeout:
        finish("превышено время прогона (%d с) на шаге %d" % (A.timeout, i))
        return
    if i >= len(steps):
        finish()
        return
    name, fn, delay = steps[i]
    log("step %s%s" % (name, " (ещё раз: %d)" % attempt if attempt else ""))
    result = None
    try:
        result = fn(ctx)
    except Exception:
        text = traceback.format_exc()
        log("ERROR in %s:\n%s" % (name, text))
        ctx.check("шаг «%s»" % name, False, text.strip().splitlines()[-1])
    if result == Ctx.AGAIN and attempt < 60:
        QTimer.singleShot(delay, lambda: run(i, attempt + 1))
        return
    QTimer.singleShot(delay, lambda: run(i + 1))


from admin_windows1.Authorization.login_window import LoginWindow  # noqa: E402

ctx.login = LoginWindow()
ctx.login.resize(WIN_W, WIN_H)
ctx.login.move(_avail.topLeft())
ctx.login.show()
QTimer.singleShot(900, run)
QTimer.singleShot((A.timeout + 30) * 1000, lambda: finish("приложение не ответило за %d с" % A.timeout))
app.exec()
srv.shutdown()
