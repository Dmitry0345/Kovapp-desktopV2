@echo off
chcp 65001 >nul
rem Симулятор телефона Kovapp: двойной щелчок — с тестовым сервером.
rem С рабочим сервером: simulator.bat --server https://kovcheg.example.ru
python "%~dp0tools\simulator.py" %*
if errorlevel 1 pause
