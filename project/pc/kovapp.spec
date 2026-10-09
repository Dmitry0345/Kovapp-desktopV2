# -*- mode: python ; coding: utf-8 -*-
# Сборка Kovapp для Windows программой PyInstaller (6.x).
# Запуск из папки с этим файлом: python -m PyInstaller --noconfirm kovapp.spec
# Результат: dist\Kovapp\Kovapp.exe — папку dist\Kovapp копируют целиком.
#
# Внутрь кладутся: страница мессенджера (web_chat), значки (images), FFmpeg
# со сжатием видео (ffmpeg: ffmpeg.exe, ffprobe.exe, лицензии, правки) и тексты
# лицензий библиотек (licenses). Программа ищет их через resource_path() — в сборке
# это папка _internal рядом с Kovapp.exe. Библиотеки Qt остаются отдельными файлами
# (_internal\PySide6) — этого требует LGPL: их можно заменить своей сборкой.

import os
import sys

HERE = os.path.abspath(SPECPATH)
SUFFIX = ".exe" if sys.platform.startswith("win") else ""


def folder(name):
    path = os.path.join(HERE, name)
    if not os.path.isdir(path):
        raise SystemExit("Нет папки %s — без неё сборка будет неполной (см. README)" % name)
    return (path, name)


for exe in ("ffmpeg", "ffprobe"):
    if not os.path.isfile(os.path.join(HERE, "ffmpeg", exe + SUFFIX)):
        raise SystemExit("Нет ffmpeg/%s%s — видео не будет сжиматься на компьютере (см. README)" % (exe, SUFFIX))

a = Analysis(
    [os.path.join(HERE, "main.py")],
    pathex=[HERE],
    binaries=[],
    datas=[folder("web_chat"), folder("images"), folder("ffmpeg"), folder("licenses")],
    hiddenimports=[
        "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineCore", "PySide6.QtWebChannel", "PySide6.QtWebSockets",
        "PySide6.QtNetwork", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtSvg",
    ],
    hookspath=[],
    runtime_hooks=[],
    # лишнее из чужих окружений: python-dotenv ссылается на IPython (надстройка для него), программе это не нужно;
    # другие привязки Qt — PyInstaller не собирает две сразу
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "IPython", "jedi", "matplotlib", "numpy", "pandas", "scipy",
              "sphinx", "docutils", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Kovapp",
    icon=os.path.join(HERE, "images", "app_icon.ico"),
    version=os.path.join(HERE, "tools", "version_info.txt"),
    console=False,          # без чёрного окна консоли
    upx=False,              # UPX портит библиотеки Qt и веб-движка
    debug=False,
    strip=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="Kovapp",
    upx=False,
    strip=False,
)
