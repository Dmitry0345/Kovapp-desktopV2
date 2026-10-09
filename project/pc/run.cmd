@echo off
rem Запуск Kovapp из исходников (без сборки). Библиотеки ставит build.cmd или эта же команда.
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" ( py -3 -m venv .venv 2>nul || python -m venv .venv )
if not exist ".venv\Scripts\pythonw.exe" ( echo Не найден Python. Установите его с python.org и запустите снова. & pause & exit /b 1 )
".venv\Scripts\python.exe" -c "import PySide6.QtWebEngineWidgets, PySide6.QtMultimedia, PySide6.QtWebSockets, requests, PIL, dotenv, requests_toolbelt" 2>nul || ".venv\Scripts\python.exe" -m pip install -r requirements.txt
start "" ".venv\Scripts\pythonw.exe" main.py
