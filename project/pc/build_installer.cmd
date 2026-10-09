@echo off
rem Установщик Kovapp: dist\KovappSetup-<версия>.exe - один файл, ставит программу без интернета.
rem Сначала собирает программу (build.cmd), затем упаковывает её Inno Setup 6.
rem Inno Setup ищется на компьютере; если его нет - один раз скачивается в tools\innosetup (нужен интернет).
chcp 65001 >nul
setlocal
cd /d "%~dp0"
call build.cmd nopause
if errorlevel 1 ( echo Программа не собралась - установщик не создан. & goto :fail )
call :find_iscc
if not defined ISCC call :get_inno
if not defined ISCC ( echo Нет Inno Setup 6 - см. раздел «Установщик» в README.md. & goto :fail )
set "SIGN="
if defined KOVAPP_SIGNTOOL set SIGN=/DSign "/Skovsign=%KOVAPP_SIGNTOOL%"
rem текстовая версия для имени установщика и свойств — первое слово из файла VERSION (напр. beta3-v12-pc)
set "VTEXT="
if exist "VERSION" for /f "usebackq tokens=1" %%v in ("VERSION") do if not defined VTEXT set "VTEXT=%%v"
set "VERDEF="
if defined VTEXT set "VERDEF=/DVerText=%VTEXT%"
echo.
echo Упаковка установщика: %ISCC%
"%ISCC%" /Qp %SIGN% %VERDEF% "installer\kovapp.iss"
if errorlevel 1 ( echo Установщик не собрался - см. сообщения выше. & goto :fail )
echo.
echo Готово: установщик в папке %~dp0dist ^(KovappSetup-...exe^)
pause
exit /b 0

:find_iscc
set "ISCC="
for %%P in ("%~dp0tools\innosetup\ISCC.exe" "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe" "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe") do if not defined ISCC if exist "%%~P" set "ISCC=%%~P"
exit /b 0

:get_inno
echo Inno Setup 6 не найден - скачиваю Inno Setup 6.7.3 с GitHub в tools\innosetup, один раз...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol='Tls12'; $f=Join-Path $env:TEMP 'innosetup-6.7.3.exe'; Invoke-WebRequest -UseBasicParsing 'https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe' -OutFile $f; if ((Get-FileHash $f -Algorithm SHA256).Hash -ne '9C73C3BAE7ED48D44112A0F48E66742C00090BDB5BEF71D9D3C056C66E97B732') { Remove-Item $f; throw 'checksum mismatch' }"
if not exist "%TEMP%\innosetup-6.7.3.exe" ( echo Не удалось скачать Inno Setup - проверьте интернет. & exit /b 0 )
"%TEMP%\innosetup-6.7.3.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /CURRENTUSER /PORTABLE=1 /DIR="%~dp0tools\innosetup"
del "%TEMP%\innosetup-6.7.3.exe" 2>nul
call :find_iscc
exit /b 0

:fail
pause
exit /b 1
