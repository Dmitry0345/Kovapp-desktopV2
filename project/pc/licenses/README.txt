Сторонние компоненты Kovapp для компьютера и их лицензии
========================================================

Код самой программы Kovapp принадлежит её владельцу и под эти лицензии не подпадает.
В сборке он скомпилирован в машинный код (Nuitka) и лежит внутри Kovapp.exe.
Ниже — библиотеки, которые поставляются вместе с программой (рядом с Kovapp.exe в папке
dist\Kovapp), их лицензии и где взять исходный код. Тексты лицензий лежат в этой папке,
для FFmpeg — в папке ffmpeg.

Компонент                       Лицензия                  Текст в этой папке
------------------------------  ------------------------  ----------------------------------
Qt 6.10.2 (окна, веб-движок,    LGPL v3                   LGPL-3.0.txt + GPL-3.0.txt
  мультимедиа, сеть)
  - в Qt WebEngine — Chromium   BSD и др. (см. ниже)
  - в Qt Multimedia — FFmpeg 7  LGPL 2.1 или новее        LGPL-2.1.txt
PySide6 и shiboken6 6.10.2      LGPL v3                   LGPL-3.0.txt + GPL-3.0.txt
Python                          PSF License               Python-PSF.txt
requests                        Apache 2.0                requests.txt
requests-toolbelt               Apache 2.0                requests-toolbelt.txt, Apache-2.0.txt
urllib3                         MIT                       urllib3.txt
idna                            BSD 3-Clause              idna.txt
charset-normalizer              MIT                       charset_normalizer.txt
certifi                         MPL 2.0                   certifi.txt, MPL-2.0.txt
Pillow                          MIT-CMU (HPND)            Pillow.txt
python-dotenv                   BSD 3-Clause              python-dotenv.txt
FFmpeg 8.1.3 (сжатие видео)     LGPL 2.1 или новее        ..\ffmpeg\LICENSE-FFmpeg-LGPLv2.1.txt
OpenH264 2.6.0 (в FFmpeg)       BSD 2-Clause              ..\ffmpeg\LICENSE-OpenH264-BSD2.txt
dav1d 1.5.1 (в FFmpeg)          BSD 2-Clause              ..\ffmpeg\LICENSE-dav1d-BSD2.txt
Nuitka (компилятор сборки)      Apache 2.0: на собранную программу его условия не распространяются
PyInstaller (запасной сборщик)  GPL 2.0 с исключением: на собранную программу его условия не распространяются

Чужой код и данные внутри исходников Kovapp (переписаны или взяты частично):
ThumbHash (kov/media/thumbhash.py) MIT, © Evan Wallace     ThumbHash-MIT.txt
Значки Lucide и Feather            ISC и MIT                Lucide-ISC.txt
  (kov/icons.py, web_chat/js/core/icons.js — часть значков)
Названия символов Unicode          Unicode License V3       Unicode-3.0.txt
  (web_chat/chat/emoji-index.js)
Поиск GIF в мессенджере — сервис KLIPY: не код, а условия использования (для выпуска нужен рабочий ключ).
Установщик собран Inno Setup: его лицензия разрешает и продаваемые программы, исходники не нужны;
автор просит коммерческих пользователей купить лицензию (просьба, не условие): jrsoftware.org/isorder.php

Исходный код
------------
Qt 6.10.2:            https://download.qt.io/official_releases/qt/6.10/6.10.2/single/
PySide6 6.10.2:       https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.10.2-src/
Сторонний код внутри Qt (Chromium в Qt WebEngine, FFmpeg 7.1 в Qt Multimedia и др.) — список
и лицензии:           https://doc.qt.io/qt-6.10/licenses-used-in-qt.html
Python:               https://www.python.org/downloads/source/
FFmpeg, OpenH264, dav1d — ссылки и номера версий в ..\ffmpeg\versions.txt;
                      как собран FFmpeg — tools\ffmpeg\build_ffmpeg.sh в исходниках Kovapp.
Остальные библиотеки — на https://pypi.org под своими именами.

Qt и PySide6 (LGPL v3)
----------------------
- Библиотеки Qt и PySide6 не встроены в Kovapp.exe, а лежат отдельными файлами рядом с ним
  в папке dist\Kovapp (Qt6*.dll, PySide6\*.pyd, shiboken6\*.pyd): пользователь может заменить
  их своей совместимой сборкой той же версии.
- Исходники Qt и PySide6 этих версий доступны по ссылкам выше; владелец Kovapp хранит
  их копию и передаёт её по запросу.
