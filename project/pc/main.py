# -*- coding: utf-8 -*-
"""Kovapp для компьютера: точка входа.

Порядок важен: сначала настройки и масштаб, затем тема (цвета нужны уже
при сборке окна входа), затем окно входа. Тема и масштаб меняются на ходу,
без перезапуска, поэтому сеанс между процессами не передаётся.
"""

import os
import sys

from PySide6.QtCore import QCoreApplication, Qt  # noqa: E402

QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
try:  # веб-движок мессенджера должен загрузиться до создания приложения
    from PySide6 import QtWebEngineWidgets  # noqa: F401,E402
except Exception:
    QtWebEngineWidgets = None

from PySide6.QtWidgets import QApplication  # noqa: E402

import admin_windows1.scaler as scaler  # noqa: E402
from general_functions import theme  # noqa: E402

try:
    from dotenv import load_dotenv  # noqa: E402
    load_dotenv()
except Exception:
    pass

_KEEP = []


def _keep_running(kind, value, tb):
    """Ошибка в обработчике не закрывает программу:
    текст — в errors.log рядом с settings.txt."""
    if issubclass(kind, KeyboardInterrupt):
        return sys.__excepthook__(kind, value, tb)
    import time
    import traceback
    text = "".join(traceback.format_exception(kind, value, tb))
    print(text)
    try:
        path = os.path.join(os.path.dirname(scaler.SETTINGS_FILE), "errors.log")
        mode = "w" if os.path.isfile(path) and os.path.getsize(path) > 512 * 1024 else "a"
        with open(path, mode, encoding="utf-8") as f:
            f.write(time.strftime("%d.%m.%Y %H:%M:%S ") + text + "\n")
    except OSError:
        pass


def main():
    sys.excepthook = _keep_running
    app = QApplication(sys.argv)
    app.setApplicationName("KovApp")
    app.setOrganizationName("Kovapp")
    app.setStyle("Fusion")
    scaler.init_scaler(app)
    theme.apply(scaler.get_setting("theme", "light"))
    from kov.ui import apply_app_theme
    from kov.shell import app_icon
    apply_app_theme(app)
    app.setWindowIcon(app_icon())

    from admin_windows1.Authorization.login_window import LoginWindow
    window = LoginWindow()
    window.show()
    _KEEP.append(window)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
