@echo off
rem Сборка Kovapp для Windows: dist\Kovapp\Kovapp.exe (папку dist\Kovapp копируют целиком).
rem Код компилируется в машинный (Nuitka): исходного кода Python и .pyc в сборке нет, восстановить код нельзя.
rem Библиотеки Qt остаются отдельными файлами рядом с программой (этого требует LGPL).
rem Нужен Python 3.10-3.12 (64 бита) с python.org. Первый запуск скачивает библиотеки
rem и, если нет компилятора, предлагаемый Nuitka MinGW (отвечаем "да" автоматически). Сборка идёт несколько минут.
rem Ключ nopause - без паузы в конце. Запасной сборщик (без защиты кода): python -m PyInstaller --noconfirm kovapp.spec
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" ( py -3 -m venv .venv 2>nul || python -m venv .venv )
if not exist ".venv\Scripts\python.exe" ( echo Не найден Python. Установите его с python.org и запустите снова. & goto :fail )
set "PY=.venv\Scripts\python.exe"
"%PY%" -m pip install --upgrade pip
"%PY%" -m pip install -r requirements.txt "nuitka>=2.4"
if errorlevel 1 ( echo Библиотеки не установились - см. сообщения выше. & goto :fail )

rem числовая версия (напр. 3.0.12.0) - из tools\version_info.txt, чтобы не задавать её в двух местах
set "VNUM=0.0.0.0"
for /f "usebackq delims=" %%v in (`"%PY%" "tools\version_num.py"`) do set "VNUM=%%v"

if exist "build-nuitka" rmdir /s /q "build-nuitka"
"%PY%" -m nuitka --standalone --assume-yes-for-downloads --remove-output --enable-plugin=pyside6 ^
  --windows-console-mode=disable --windows-icon-from-ico=images\app_icon.ico ^
  --company-name=Kovapp --product-name=Kovapp --file-version=%VNUM% --product-version=%VNUM% ^
  --file-description="Kovapp - задачи, проекты, склад, оборудование и мессенджер" ^
  --include-data-dir=web_chat=web_chat --include-data-dir=images=images ^
  --include-data-dir=ffmpeg=ffmpeg --include-data-dir=licenses=licenses ^
  --include-package-data=certifi ^
  --nofollow-import-to=tkinter,PyQt5,PyQt6,PySide2,IPython,jedi,matplotlib,numpy,pandas,scipy,sphinx,docutils,pytest ^
  --output-dir=build-nuitka --output-filename=Kovapp.exe main.py
if errorlevel 1 ( echo Сборка не удалась - см. сообщения выше. & goto :fail )

rem привычная раскладка dist\Kovapp\Kovapp.exe (её ждёт установщик)
if exist "dist\Kovapp" rmdir /s /q "dist\Kovapp"
if not exist "dist" mkdir "dist"
move "build-nuitka\main.dist" "dist\Kovapp" >nul
if errorlevel 1 ( echo Не удалось переместить результат сборки. & goto :fail )
rmdir /s /q "build-nuitka" 2>nul
echo.
echo Готово: %~dp0dist\Kovapp\Kovapp.exe
if /i not "%~1"=="nopause" pause
exit /b 0

:fail
if /i not "%~1"=="nopause" pause
exit /b 1
