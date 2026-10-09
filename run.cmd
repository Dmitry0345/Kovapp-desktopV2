@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

set "PSH=powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\android-host.ps1""
rem ключ adb этого компьютера — для всех режимов с эмулятором: ему эмулятор доверяет без окна «Allow USB debugging?»
set "ADB_KEYS_DIR=%USERPROFILE%\.android"

rem ===== версия модуля и проверка распаковки =====
set "KOVVER="
if exist "VERSION" set /p KOVVER=<"VERSION"
rem однострочные if: скобки в номере версии не ломают разбор строки
if defined KOVVER echo Модуль тестов: версия %KOVVER%
if not defined KOVVER echo Модуль тестов: версия не указана - модуль старый, обновите
rem папки вариантов приложения: веб-версия (сервер + сайт), Android, ПК (из .env; PROJECT_DIR — прежняя общая папка)
set "OLDPROJ="
set "WEBPROJ="
set "ANDPROJ="
set "PCPROJ="
if exist ".env" for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
  if /i "%%A"=="PROJECT_DIR" set "OLDPROJ=%%B"
  if /i "%%A"=="PROJECT_WEB_DIR" set "WEBPROJ=%%B"
  if /i "%%A"=="PROJECT_ANDROID_DIR" set "ANDPROJ=%%B"
  if /i "%%A"=="PROJECT_PC_DIR" set "PCPROJ=%%B"
)
if not defined WEBPROJ if defined OLDPROJ set "WEBPROJ=%OLDPROJ%"
if not defined ANDPROJ if defined OLDPROJ set "ANDPROJ=%OLDPROJ%"
if not defined WEBPROJ set "WEBPROJ=./project/web"
if not defined ANDPROJ set "ANDPROJ=./project/android"
if not defined PCPROJ set "PCPROJ=./project/pc"
rem для docker compose: переменные окна главнее строк .env
set "PROJECT_WEB_DIR=%WEBPROJ%"
set "PROJECT_ANDROID_DIR=%ANDPROJ%"
set "PROJECT_PC_DIR=%PCPROJ%"
rem архив web.zip / android.zip распаковали «с лишней папкой» (project\web\web\...) — берём вложенную
set "PROJ=%WEBPROJ:/=\%"
if not exist "%PROJ%\server\server.py" if exist "%PROJ%\web\server\server.py" set "PROJ=%PROJ%\web"
set "PROJECT_WEB_DIR=%PROJ:\=/%"
set "APROJ=%ANDPROJ:/=\%"
if not exist "%APROJ%\mobile\app\build.gradle" if exist "%APROJ%\android\mobile\app\build.gradle" set "APROJ=%APROJ%\android"
set "PROJECT_ANDROID_DIR=%APROJ:\=/%"
if not exist "%PROJ%\server\server.py" echo ВНИМАНИЕ: в %PROJ% нет веб-версии. Распакуйте web.zip в папку project - получится project\web\server, project\web\web_app
if not exist "%APROJ%\mobile\app\build.gradle" echo ВНИМАНИЕ: в %APROJ% нет Android-версии. Распакуйте android.zip в папку project - получится project\android\mobile, project\android\web_app
set "PROJVER="
if exist "%PROJ%\VERSION" set /p PROJVER=<"%PROJ%\VERSION"
if defined PROJVER echo Веб-версия:    версия %PROJVER%   (%PROJ%)
if not defined PROJVER echo Веб-версия:    версия не указана - проект старый или не в папке %PROJ%
set "ANDVER="
if exist "%APROJ%\VERSION" set /p ANDVER=<"%APROJ%\VERSION"
if defined ANDVER echo Android:       версия %ANDVER%   (%APROJ%)
if not defined ANDVER echo Android:       версия не указана - нет проекта в папке %APROJ%
rem ПК-версия; архив pc.zip распаковали «с лишней папкой» (project\pc\pc\...) — берём вложенную
set "PPROJ=%PCPROJ:/=\%"
if "%PPROJ:~-1%"=="\" set "PPROJ=%PPROJ:~0,-1%"
if not exist "%PPROJ%\main.py" if exist "%PPROJ%\pc\main.py" set "PPROJ=%PPROJ%\pc"
set "PCVER="
if exist "%PPROJ%\VERSION" set /p PCVER=<"%PPROJ%\VERSION"
if defined PCVER echo ПК-версия:     версия %PCVER%   (%PPROJ%)
if not defined PCVER echo ПК-версия:     версия не указана - нет проекта в папке %PPROJ%
if /i "%~1"=="version" exit /b 0
rem pc.zip распаковали в папку модуля, а не в project
if exist "pc\main.py" if exist "pc\kov" (
  echo ВНИМАНИЕ: архив pc.zip распакован в папку модуля, а нужно в папку project.
  echo Перенесите папку %~dp0pc в %~dp0project ^(получится project\pc\main.py^) и запустите снова.
  exit /b 1
)
rem архив, распакованный во вложенную папку: обновление не применилось — работают старые файлы
for /d %%D in (*) do if exist "%%D\run.cmd" (
  echo.
  echo ВНИМАНИЕ: в папке %%D лежит ещё один модуль тестов - архив распакован во вложенную папку,
  echo и обновление не применилось: работают старые файлы.
  echo Перенесите всё из %~dp0%%D\ сюда, в %~dp0, с заменой файлов и удалите папку %%D.
  exit /b 1
)
rem ===== список всех вариантов проверки — без Docker =====
if /i "%~1"=="list" goto list
rem ===== ПК-версия — без Docker: Python этого компьютера =====
if /i "%~1"=="pc" goto pc

rem ===== проверки =====
if not exist ".env" if exist "kovapp_mobile.env" copy "kovapp_mobile.env" ".env" >nul
if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo Создан файл .env из .env.example. Проверьте настройки и запустите снова.
  exit /b 1
)
where docker >nul 2>nul
if errorlevel 1 (
  echo Docker не найден. Установите Docker Desktop: docs\WINDOWS.md
  exit /b 1
)
docker info >nul 2>nul
if errorlevel 1 (
  echo Docker Desktop не запущен. Запустите его, дождитесь зелёного значка и повторите.
  exit /b 1
)
rem без модуля Compose «docker compose --profile ...» падает со справкой docker и «unknown flag: --profile»
docker compose version >nul 2>nul
if errorlevel 1 (
  echo Docker найден, но не работает «docker compose» - модуль Compose не подключён.
  echo   1^) Полностью перезапустите Docker Desktop: значок кита в трее - Quit Docker Desktop, затем запустите снова.
  echo   2^) Проверьте, какой docker используется: where.exe docker - первым должен идти
  echo      C:\Program Files\Docker\Docker\resources\bin\docker.exe
  echo   3^) Не помогло - обновите или восстановите Docker Desktop установщиком с docker.com.
  exit /b 1
)
if not exist "logs" mkdir "logs"

set "PAIR="
set "PAIR_ENV="
rem ===== разбор команды =====
set "CMD=%~1"
if "%CMD%"=="" set "CMD=full"
shift
set "SECTIONS="
:collect
if "%~1"=="" goto collected
if defined SECTIONS (set "SECTIONS=%SECTIONS%,%~1") else (set "SECTIONS=%~1")
shift
goto collect
:collected
if not defined SECTIONS set "SECTIONS=all"

if /i "%CMD%"=="full" goto full
if /i "%CMD%"=="web" goto full
if /i "%CMD%"=="external" goto external
if /i "%CMD%"=="web-external" goto external
if /i "%CMD%"=="android" goto android
if /i "%CMD%"=="android-pc" goto mobile
if /i "%CMD%"=="android-external" goto mobile_external
if /i "%CMD%"=="android-device" goto device
if /i "%CMD%"=="mobile" goto mobile
if /i "%CMD%"=="mobile-external" goto mobile_external
if /i "%CMD%"=="device" goto device
if /i "%CMD%"=="load" goto load
if /i "%CMD%"=="stand" goto stand
if /i "%CMD%"=="compression" goto compression
if /i "%CMD%"=="device-both" goto device_both
if /i "%CMD%"=="device-pair" goto device_pair
if /i "%CMD%"=="demo" goto demo
if /i "%CMD%"=="video-log" goto video_log
if /i "%CMD%"=="login-check" goto login_check
if /i "%CMD%"=="notify" goto notify
if /i "%CMD%"=="all" goto run_all
if /i "%CMD%"=="keys-check" goto keys_check
if /i "%CMD%"=="stop" goto stop_all
if /i "%CMD%"=="list" goto list
if /i "%CMD%"=="build" goto build
if /i "%CMD%"=="server" goto server
if /i "%CMD%"=="stop" goto stop
if /i "%CMD%"=="help" goto help
echo Неизвестная команда: %CMD%
goto help

rem ===== полный прогон: база, сервер и тесты в Docker =====
:full
docker compose --profile full down --remove-orphans >nul 2>nul
ver >nul
if not "%SKIP_BUILD%"=="1" docker compose --profile full build
if errorlevel 1 goto build_failed
docker compose --profile full up -d --wait db server
if errorlevel 1 goto server_failed
docker compose --profile full run --rm -e ONLY tests --mode full --sections %SECTIONS%
set "RC=%ERRORLEVEL%"
call :save_console
docker compose --profile full down --remove-orphans >nul 2>nul
call :show_result
exit /b %RC%

rem ===== внешний сервер =====
:external
ver >nul
if not "%SKIP_BUILD%"=="1" docker compose build tests
if errorlevel 1 goto build_failed
docker compose run --rm -e ONLY tests --mode external --sections %SECTIONS%
set "RC=%ERRORLEVEL%"
call :show_result
exit /b %RC%

rem ===== мобильная версия: интерфейс APK на ПК =====
:mobile
docker compose --profile full down --remove-orphans >nul 2>nul
ver >nul
if not "%SKIP_BUILD%"=="1" docker compose --profile full build
if errorlevel 1 goto build_failed
docker compose --profile full up -d --wait db server
if errorlevel 1 goto server_failed
docker compose --profile full run --rm -e ONLY tests --mode full --target apk --sections %SECTIONS%
set "RC=%ERRORLEVEL%"
call :save_console
docker compose --profile full down --remove-orphans >nul 2>nul
call :show_result
exit /b %RC%

:mobile_external
ver >nul
if not "%SKIP_BUILD%"=="1" docker compose build tests
if errorlevel 1 goto build_failed
docker compose run --rm -e ONLY tests --mode external --target apk --sections %SECTIONS%
set "RC=%ERRORLEVEL%"
call :show_result
exit /b %RC%

rem ===== настоящий Android: Appium и всё остальное в Docker, эмулятор — Android Studio =====
:device
set "ADB_KEYS_DIR=%USERPROFILE%\.android"
%PSH% start
if errorlevel 1 exit /b 1
if "%PAIR%"=="1" (
  %PSH% start -Slot 2
  if errorlevel 1 goto device_cleanup_fail
)
docker compose --profile full --profile device --profile pair down --remove-orphans >nul 2>nul
ver >nul
if not "%SKIP_BUILD%"=="1" docker compose --profile full --profile device build db server keycheck apk device
if errorlevel 1 goto device_build_failed
docker compose --profile full up -d --wait db server
if errorlevel 1 goto server_failed
docker compose --profile device run --rm keycheck
echo.
echo Сборка приложения из папки project...
docker compose --profile device run --rm apk
if errorlevel 1 (
  echo Сборка APK не удалась - строки с ошибкой выше.
  goto device_cleanup_fail
)
call :device_appium
if errorlevel 1 goto device_cleanup_fail
if "%PAIR%"=="1" (
  echo Второй телефон: жду Appium...
  docker compose --profile device --profile pair up -d --wait appium2
  if errorlevel 1 goto device_cleanup_fail
)
docker compose --profile device run --rm -e ONLY %PAIR_ENV% device --mode full --target device --sections %SECTIONS%
set "RC=%ERRORLEVEL%"
if "%PAIR%"=="1" docker compose --profile device --profile pair logs --no-color appium2 > "logs\appium2-last.log" 2>&1
call :save_console
docker compose --profile device logs --no-color appium > "logs\appium-last.log" 2>&1
docker compose --profile full --profile device --profile pair down --remove-orphans >nul 2>nul
%PSH% stop
call :show_result
exit /b %RC%

rem Appium подключает эмулятор: сначала напрямую, иначе - через ретранслятор на этом компьютере
:device_appium
set "DEVICE_ADB=host.docker.internal:5601"
echo Appium подключается к эмулятору и ждёт загрузки Android (до 10 минут)...
docker compose --profile device up -d --wait appium
set "APPIUM_RC=%ERRORLEVEL%"
docker compose --profile device logs --no-color --no-log-prefix appium
if "%APPIUM_RC%"=="0" exit /b 0
echo Appium не достучался до эмулятора напрямую - включаю ретранслятор на этом компьютере...
%PSH% relay
set "DEVICE_ADB=host.docker.internal:15601"
docker compose --profile device up -d --force-recreate --wait appium
if not errorlevel 1 exit /b 0
echo.
echo Appium не может подключиться к эмулятору. Журнал контейнера:
docker compose --profile device logs --no-color --tail 30 appium
echo.
echo Если Windows спрашивала разрешение для PowerShell или adb - разрешите доступ и запустите снова.
echo Подробно - docs\DEVICE.md, раздел «Если что-то не так».
exit /b 1

:device_build_failed
echo Сборка образов не удалась - строки с ошибкой выше. Проверьте интернет и запустите снова.
%PSH% stop
exit /b 3

:device_cleanup_fail
docker compose --profile full --profile device --profile pair down --remove-orphans >nul 2>nul
%PSH% stop
exit /b 3

rem ===== Android целиком: интерфейс APK на ПК, затем настоящее приложение на эмуляторе =====
:android
set "AVD_NOGMS="
for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do if /i "%%A"=="DEVICE_AVD_NOGMS" set "AVD_NOGMS=%%B"
echo.
echo ===== Android, часть 1 из 2: интерфейс APK на ПК =====
call "%~f0" android-pc %SECTIONS%
set "RC_APK=%ERRORLEVEL%"
echo.
echo ===== Android, часть 2 из 2: приложение на эмуляторе =====
if defined AVD_NOGMS (call "%~f0" device-both) else (call "%~f0" device)
set "RC_DEV=%ERRORLEVEL%"
echo.
echo APK на ПК: код %RC_APK%    Android на эмуляторе: код %RC_DEV%   (0 - всё прошло)
if not "%RC_APK%"=="0" exit /b %RC_APK%
exit /b %RC_DEV%

rem ===== нагрузка по ступеням на сервере размером с рабочий (2 ядра, 1,4 ГБ; база — полядра, 512 МБ) =====
:load
set "COMPOSE=docker compose -f docker-compose.yml -f docker-compose.load.yml --profile full"
%COMPOSE% down --remove-orphans >nul 2>nul
ver >nul
if not "%SKIP_BUILD%"=="1" %COMPOSE% build
if errorlevel 1 goto build_failed
%COMPOSE% up -d --wait db server
if errorlevel 1 goto server_failed
%COMPOSE% run --rm -e PARALLEL=1 -e RUN_LABEL=load -e ONLY tests --mode full --sections @load
set "RC=%ERRORLEVEL%"
call :save_console
%COMPOSE% down --remove-orphans >nul 2>nul
call :show_result
exit /b %RC%

rem ===== стенд: нагрузка в фоне, а вы работаете в браузере сами =====
:stand
set "COMPOSE=docker compose -f docker-compose.yml -f docker-compose.load.yml --profile full"
%COMPOSE% down --remove-orphans >nul 2>nul
ver >nul
if not "%SKIP_BUILD%"=="1" %COMPOSE% build
if errorlevel 1 goto build_failed
%COMPOSE% up -d --wait db server
if errorlevel 1 goto server_failed
echo.
echo Стенд запускается. Продукт: http://localhost:18000/app/   Панель стенда: http://localhost:18090/
echo Остановить: Ctrl+C, затем дождитесь выключения.
%COMPOSE% run --rm -p 18090:8090 -e PARALLEL=1 tests --mode full --stand
%COMPOSE% down --remove-orphans >nul 2>nul
exit /b 0

rem ===== качество сжатия: браузеры и сервер, затем телефон (если настроен эмулятор) =====
:compression
rem телефон в сравнении: CMP_PHONE=1 — всегда, auto — если есть Android SDK, 0 — нет
set "CMP_PHONE=auto"
set "SDKDIR="
for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
  if /i "%%A"=="CMP_PHONE" set "CMP_PHONE=%%B"
  if /i "%%A"=="ANDROID_SDK" set "SDKDIR=%%B"
)
set "HAS_AVD="
if "%CMP_PHONE%"=="1" set "HAS_AVD=1"
if /i "%CMP_PHONE%"=="auto" if defined SDKDIR if exist "%SDKDIR%\platform-tools\adb.exe" set "HAS_AVD=1"
docker compose --profile full down --remove-orphans >nul 2>nul
ver >nul
if not "%SKIP_BUILD%"=="1" docker compose --profile full build
if errorlevel 1 goto build_failed
docker compose --profile full up -d --wait db server
if errorlevel 1 goto server_failed
docker compose --profile full run --rm -e PARALLEL=1 -e RUN_LABEL=compression -e ONLY tests --mode full --sections compression
set "RC=%ERRORLEVEL%"
call :save_console
rem ПК-версия: те же исходники сжимает её код (FFmpeg из комплекта), разбор и общий отчёт - вторым коротким прогоном
rem CMP_PC=0 в .env - не включать
set "CMP_PC=auto"
for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do if /i "%%A"=="CMP_PC" set "CMP_PC=%%B"
if "%CMP_PC%"=="0" goto compression_pc_done
if not exist "%PPROJ%\main.py" echo ПК-версия в сравнение не вошла: нет её папки ^(project\pc или PROJECT_PC_DIR в .env^)
if not exist "%PPROJ%\main.py" goto compression_pc_done
echo.
echo ===== Качество сжатия на ПК =====
call "%~f0" pc compression
docker compose --profile full run --rm -e PARALLEL=1 -e RUN_LABEL=compression-pc -e CMP_BROWSERS=none -e CMP_SERVER=0 tests --mode full --sections compression
set "RC_PCC=%ERRORLEVEL%"
call :save_console
if "%RC%"=="0" set "RC=%RC_PCC%"
:compression_pc_done
docker compose --profile full down --remove-orphans >nul 2>nul
call :show_result
if defined RUNDIR echo Отчёт сравнения: logs\%RUNDIR%\compression-report.html
if not defined HAS_AVD (
  echo Телефон в сравнение не вошёл: нужен Android SDK ^(ANDROID_SDK в .env^) или CMP_PHONE=1 ^(docs\DEVICE.md^)
  exit /b %RC%
)
echo.
echo ===== Качество сжатия на телефоне ^(эмулятор^) =====
call "%~f0" device compression
set "RC2=%ERRORLEVEL%"
if not "%RC%"=="0" exit /b %RC%
exit /b %RC2%

rem ===== всё подряд: сайт, Android (APK на ПК и эмулятор), качество сжатия, нагрузка, ПК-версия =====
:run_all
set "AVD_NOGMS="
for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do if /i "%%A"=="DEVICE_AVD_NOGMS" set "AVD_NOGMS=%%B"
echo.
echo ===== Часть 1 из 6: веб-версия, все разделы =====
call "%~f0" full
set "RC_SITE=%ERRORLEVEL%"
echo.
echo ===== Часть 2 из 6: Android - интерфейс APK на ПК =====
call "%~f0" mobile
set "RC_APK=%ERRORLEVEL%"
echo.
echo ===== Часть 3 из 6: Android - приложение на эмуляторе =====
if defined AVD_NOGMS (call "%~f0" device-both) else (call "%~f0" device)
set "RC_DEV=%ERRORLEVEL%"
echo.
echo ===== Часть 4 из 6: качество сжатия - браузеры, сервер, телефон =====
call "%~f0" compression
set "RC_CMP=%ERRORLEVEL%"
echo.
echo ===== Часть 5 из 6: нагрузка по ступеням =====
call "%~f0" load
set "RC_LOAD=%ERRORLEVEL%"
echo.
echo ===== Часть 6 из 6: ПК-версия =====
call "%~f0" pc
set "RC_PC=%ERRORLEVEL%"
echo.
echo ===== Итог всех прогонов (0 - всё прошло) =====
echo Веб-версия:           код %RC_SITE%
echo APK на ПК:            код %RC_APK%
if defined AVD_NOGMS echo Android, два эмулятора: код %RC_DEV%
if not defined AVD_NOGMS echo Android, один эмулятор: код %RC_DEV%   ^(второй, без Google: DEVICE_AVD_NOGMS в .env^)
echo Качество сжатия:      код %RC_CMP%
echo Нагрузка:             код %RC_LOAD%
echo ПК-версия:            код %RC_PC%
echo Отчёты - в папках logs, по одному на каждый прогон.
if not "%RC_SITE%"=="0" exit /b %RC_SITE%
if not "%RC_APK%"=="0" exit /b %RC_APK%
if not "%RC_DEV%"=="0" exit /b %RC_DEV%
if not "%RC_CMP%"=="0" exit /b %RC_CMP%
if not "%RC_LOAD%"=="0" exit /b %RC_LOAD%
exit /b %RC_PC%

rem ===== ПК-версия: без Docker — Python этого компьютера в .venv-pc, тестовый сервер внутри прогона =====
:pc
shift
set "PCPARTS="
:pc_collect
if "%~1"=="" goto pc_collected
if defined PCPARTS (set "PCPARTS=%PCPARTS%,%~1") else (set "PCPARTS=%~1")
shift
goto pc_collect
:pc_collected
if not defined PCPARTS set "PCPARTS=unit,sections,actions,messenger"
call :pc_env
if errorlevel 1 exit /b %ERRORLEVEL%
echo.
if /i not "%PCPARTS%"=="compression" echo Окна программы будут сами открываться и закрываться - все части идут около 17 минут. Не трогайте в это время мышь и клавиатуру.
"%PCPY%" kovtest_pc\run_pc.py --project "%PPROJ%" --parts %PCPARTS%
set "RC=%ERRORLEVEL%"
call :show_result
if defined RUNDIR echo Снимки экранов: logs\%RUNDIR%\index.html
exit /b %RC%

rem ===== окружение Python для ПК-версии (.venv-pc): создаётся при первом запуске, библиотеки - из requirements.txt =====
:pc_env
if not exist "%PPROJ%\main.py" echo Нет ПК-версии в папке %PPROJ%. Распакуйте pc.zip в папку project - получится project\pc\main.py
if not exist "%PPROJ%\main.py" exit /b 1
if not exist "logs" mkdir "logs"
set "PCPY=.venv-pc\Scripts\python.exe"
if exist "%PCPY%" goto pc_env_deps
set "PYBASE="
py -3 -c "import sys" >nul 2>nul && set "PYBASE=py -3"
if not defined PYBASE python -c "import sys" >nul 2>nul && set "PYBASE=python"
if not defined PYBASE echo Не найден Python. Установите Python 3.10-3.12 ^(64 бита^) с python.org, отметив «Add python.exe to PATH», и запустите снова.
if not defined PYBASE exit /b 1
echo Создаю окружение Python для ПК-версии: .venv-pc
%PYBASE% -m venv .venv-pc
if not exist "%PCPY%" echo Окружение .venv-pc не создалось - сообщения выше.
if not exist "%PCPY%" exit /b 1
:pc_env_deps
echo Библиотеки ПК-версии из %PPROJ%\requirements.txt ^(первый раз - несколько минут^)...
"%PCPY%" -m pip install --disable-pip-version-check -r "%PPROJ%\requirements.txt"
if errorlevel 1 echo Библиотеки не установились - сообщения выше. Нужен интернет и Python 3.10-3.12 ^(64 бита^).
if errorlevel 1 exit /b 3
exit /b 0

rem ===== окно ПК-версии на сервере demo: без консоли, печать программы - в logs\demo-pc.log =====
:demo_pc_start
if exist "logs\demo-pc.stop" del /q "logs\demo-pc.stop"
set "DEMOR="
if defined DEMOROLE set "DEMOR=--role %DEMOROLE%"
set "PCPYW=.venv-pc\Scripts\pythonw.exe"
if not exist "%PCPYW%" set "PCPYW=%PCPY%"
echo Открываю ПК-версию на этом же сервере: http://localhost:%PUBLISH%
start "" "%PCPYW%" kovtest_pc\demo_pc.py --project "%PPROJ%" --host http://localhost:%PUBLISH% --stop-file "%CD%\logs\demo-pc.stop" --log "%CD%\logs\demo-pc.log" %DEMOR%
exit /b 0

rem ===== закрыть окно ПК-версии: программа замечает файл за полсекунды =====
:demo_pc_stop
echo stop> "logs\demo-pc.stop"
ping -n 3 127.0.0.1 >nul
if exist "logs\demo-pc.stop" del /q "logs\demo-pc.stop"
exit /b 0

rem ===== ручная проверка: сервер с готовыми данными, приложение на эмуляторе =====
:demo
rem demo pc [роль]: тот же сервер, данные и учётные записи, что у demo web, и окно ПК-версии на нём
for /f "tokens=1,2 delims=," %%a in ("%SECTIONS%") do ( set "DEMO1=%%a" & set "DEMOROLE=%%b" )
set "DEMO_PC="
if /i "%DEMO1%"=="pc" set "DEMO_PC=1"
set "DEMO_PHONE=1"
if /i "%SECTIONS%"=="site" set "DEMO_PHONE="
if /i "%SECTIONS%"=="web" set "DEMO_PHONE="
if /i "%SECTIONS%"=="phone" set "DEMO_PHONE=usb"
if defined DEMO_PC set "DEMO_PHONE="
set "PUBLISH=18000"
set "AVD_NOGMS="
for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
  if /i "%%A"=="FULL_PUBLISH_PORT" set "PUBLISH=%%B"
  if /i "%%A"=="DEVICE_AVD_NOGMS" set "AVD_NOGMS=%%B"
)
if /i "%SECTIONS%"=="nogms" set "KOV_AVD=%AVD_NOGMS%"
if /i "%SECTIONS%"=="nogms" if not defined AVD_NOGMS (
  echo Для demo nogms укажите в .env DEVICE_AVD_NOGMS - эмулятор без сервисов Google.
  exit /b 1
)
rem ПК-версия: окружение Python - до сервера (в первый раз ставятся библиотеки)
if not defined DEMO_PC goto demo_docker
call :pc_env
if errorlevel 1 exit /b %ERRORLEVEL%
:demo_docker
docker compose --profile full --profile device --profile pair down --remove-orphans >nul 2>nul
if "%DEMO_PHONE%"=="1" (
  %PSH% start
  if errorlevel 1 exit /b 1
)
if not "%SKIP_BUILD%"=="1" docker compose --profile full build
if errorlevel 1 goto demo_fail
if defined DEMO_PHONE if not "%SKIP_BUILD%"=="1" docker compose --profile device build apk
if errorlevel 1 goto demo_fail
docker compose --profile full up -d --wait db server
if errorlevel 1 goto demo_fail
docker compose --profile full run --rm tests --mode full --demo
if errorlevel 1 goto demo_fail
echo.
echo Проверяю вход всех ролей на сервер этого ПК (тем же путём, что браузер и приложение):
%PSH% login-check -Port %PUBLISH%
if not defined DEMO_PHONE goto demo_ready
docker compose --profile device run --rm apk
if errorlevel 1 goto demo_fail
if "%DEMO_PHONE%"=="usb" goto demo_usb
call :device_appium
if errorlevel 1 goto demo_fail
echo Ставлю приложение на эмулятор начисто и связываю его с сервером...
docker compose --profile device exec -T appium sh -c "adb uninstall ru.kovcheg.kovapp >/dev/null 2>&1; adb install /apk/app-debug.apk && curl -s -X POST http://127.0.0.1:4799/link/on >/dev/null && adb shell am start -n ru.kovcheg.kovapp/.MainActivity"
if errorlevel 1 goto demo_fail
%PSH% video-log -Target emulator
goto demo_ready

rem телефон по USB: приложение из сборки Docker -> в папку out -> на телефон; сервер — по кабелю
:demo_usb
if not exist "out" mkdir "out"
docker compose --profile device run --rm --no-deps -v "%CD%\out:/out" --entrypoint sh apk -c "cp /apk/app-debug.apk /out/kovapp-debug.apk"
if errorlevel 1 goto demo_fail
%PSH% phone -Apk "%CD%\out\kovapp-debug.apk" -Port %PUBLISH%
if errorlevel 1 goto demo_fail
%PSH% video-log -Target phone
:demo_ready
if defined DEMO_PC call :demo_pc_start
echo.
echo ================== Kovapp готов к ручной проверке ==================
echo Сайт на этом ПК:  http://localhost:%PUBLISH%/app/
if defined DEMO_PC if defined DEMOROLE echo ПК-версия:        окно Kovapp, вход ролью %DEMOROLE% - тот же сервер, что у сайта; работают одновременно
if defined DEMO_PC if not defined DEMOROLE echo ПК-версия:        окно Kovapp - войдите любой учётной записью; тот же сервер, что у сайта, работают одновременно
if defined DEMO_PC echo Журнал ПК-версии: logs\demo-pc.log
if "%DEMO_PHONE%"=="1" echo Телефон:          окно эмулятора - Kovapp открыт, войдите любой учётной записью
if "%DEMO_PHONE%"=="usb" echo Телефон:          ваш смартфон по USB - Kovapp открыт; кабель не отключайте: через него идёт связь с сервером
if defined DEMO_PHONE echo Журнал видео:     в отдельном окне - строки появляются, когда отправляете видео
echo Учётные записи:   выше и в logs\demo-accounts.txt
echo Данные:           3 проекта, 6 задач на разных этапах, чаты, оборудование, склад
echo.
echo Проверяйте сколько нужно. Когда закончите - нажмите любую клавишу в этом окне: всё остановится.
pause >nul
if defined DEMO_PC call :demo_pc_stop
rem журнал сервера за сеанс: запросы файлов и обложек, ошибки обработки видео
docker compose --profile full logs --no-color server > "logs\server-demo.log" 2>&1
echo Журнал сервера за сеанс: logs\server-demo.log
docker compose --profile full --profile device --profile pair down --remove-orphans >nul 2>nul
if "%DEMO_PHONE%"=="usb" %PSH% phone-stop
if "%DEMO_PHONE%"=="1" %PSH% stop-all
set "KOV_AVD="
echo Остановлено.
exit /b 0

:demo_fail
echo Режим ручной проверки не запустился - причина выше.
docker compose --profile full --profile device --profile pair down --remove-orphans >nul 2>nul
if "%DEMO_PHONE%"=="usb" %PSH% phone-stop
if "%DEMO_PHONE%"=="1" %PSH% stop-all
set "KOV_AVD="
exit /b 3

rem ===== журнал сжатия видео (KovVideo) в отдельном окне: эмулятор или телефон по USB =====
:video_log
set "VL_TARGET=emulator"
if /i "%SECTIONS%"=="phone" set "VL_TARGET=phone"
%PSH% video-log -Target %VL_TARGET%
exit /b %ERRORLEVEL%

rem ===== проверочное уведомление с ПК на телефон: run.cmd notify [роль] (по умолчанию монтажнику) =====
:notify
set "PUBLISH=18000"
for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do if /i "%%A"=="FULL_PUBLISH_PORT" set "PUBLISH=%%B"
%PSH% notify -Port %PUBLISH% -Target %SECTIONS%
exit /b %ERRORLEVEL%

rem ===== вход всех ролей на запущенный сервер демо (пароль — из .env) =====
:login_check
set "PUBLISH=18000"
for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do if /i "%%A"=="FULL_PUBLISH_PORT" set "PUBLISH=%%B"
%PSH% login-check -Port %PUBLISH%
exit /b %ERRORLEVEL%

rem ===== два телефона одновременно: проверки в реальном времени (WebSocket) =====
:device_pair
set "PAIR=1"
set "PAIR_ENV=-e APPIUM2_URL=http://appium2:4723 -e LINK2_URL=http://appium2:4799"
if /i "%SECTIONS%"=="all" set "SECTIONS=device_check,device_live"
echo Два телефона: DEVICE_AVD и DEVICE_AVD_NOGMS (или DEVICE_AVD2). Нужно около 16 ГБ памяти.
goto device

rem ===== два прогона: эмулятор с сервисами Google и без них (как Huawei) =====
:device_both
set "AVD_GMS="
set "AVD_NOGMS="
for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
  if /i "%%A"=="DEVICE_AVD" set "AVD_GMS=%%B"
  if /i "%%A"=="DEVICE_AVD_NOGMS" set "AVD_NOGMS=%%B"
)
if not defined AVD_NOGMS (
  echo Для второго прогона нужен эмулятор без сервисов Google.
  echo Создайте его в Android Studio: Device Manager - + - Pixel 8 - образ БЕЗ слов Google Play и Google APIs,
  echo и впишите его имя в .env: DEVICE_AVD_NOGMS=имя_эмулятора. Подробно - docs\DEVICE.md.
  exit /b 1
)
echo.
echo ===== Прогон 1 из 2: эмулятор с сервисами Google =====
set "KOV_AVD=%AVD_GMS%"
call "%~f0" device %SECTIONS%
set "RC1=%ERRORLEVEL%"
%PSH% stop-all >nul
echo.
echo ===== Прогон 2 из 2: эмулятор без сервисов Google (%AVD_NOGMS%) =====
set "KOV_AVD=%AVD_NOGMS%"
call "%~f0" device %SECTIONS%
set "RC2=%ERRORLEVEL%"
set "KOV_AVD="
echo.
echo ===== Итог двух прогонов =====
echo С сервисами Google:  код %RC1%   (отчёт - в первой из двух последних папок logs)
echo Без сервисов Google: код %RC2%   (отчёт - в последней папке logs)
echo 0 - всё прошло; иначе смотрите summary.txt соответствующего прогона.
if not "%RC1%"=="0" exit /b %RC1%
exit /b %RC2%

rem ===== остановить всё: контейнеры, тестовый эмулятор, вспомогательные процессы =====
:stop_all
docker compose --profile full --profile device --profile pair down --remove-orphans
%PSH% stop-all
echo Всё остановлено: контейнеры тестов, эмулятор для тестов, вспомогательные процессы.
exit /b 0

rem ===== проверка ключей Firebase из папки keys =====
:keys_check
docker compose --profile device build keycheck
if errorlevel 1 exit /b 3
docker compose --profile device run --rm keycheck
exit /b %ERRORLEVEL%

rem ===== служебные команды =====
:list
set "PUBLISH=18000"
if exist ".env" for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do if /i "%%A"=="FULL_PUBLISH_PORT" set "PUBLISH=%%B"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\list.ps1" -Run ".\run.cmd" -Port "%PUBLISH%"
exit /b %ERRORLEVEL%

:build
docker compose --profile full build
exit /b %ERRORLEVEL%

:server
docker compose --profile full down --remove-orphans >nul 2>nul
docker compose --profile full build db server
docker compose --profile full up -d --wait db server
if errorlevel 1 goto server_failed
echo Сервер запущен: http://localhost:18000/app/  (остановить: run.cmd stop)
exit /b 0

:stop
docker compose --profile full down --remove-orphans
exit /b %ERRORLEVEL%

:save_console
set "RUNDIR="
if exist "logs\last-run.txt" for /f "usebackq delims=" %%R in ("logs\last-run.txt") do set "RUNDIR=%%R"
if defined RUNDIR if exist "logs\%RUNDIR%" docker compose --profile full logs --no-color server > "logs\%RUNDIR%\server-console.log" 2>&1
exit /b 0

:show_result
set "RUNDIR="
if exist "logs\last-run.txt" for /f "usebackq delims=" %%R in ("logs\last-run.txt") do set "RUNDIR=%%R"
if defined RUNDIR (
  echo.
  echo Полный журнал:  logs\%RUNDIR%\full.log
  echo Краткий итог:   logs\%RUNDIR%\summary.txt
)
exit /b 0

:build_failed
echo Сборка образов не удалась. Проверьте подключение к интернету и повторите.
exit /b 3

:server_failed
echo Сервер в Docker не запустился. Частые причины: нет проекта в папке веб-версии ^(%PROJECT_WEB_DIR%, PROJECT_WEB_DIR в .env^) или занят порт FULL_PUBLISH_PORT.
docker compose --profile full logs --no-color --tail 80 server
docker compose --profile full down --remove-orphans >nul 2>nul
exit /b 3

:help
echo.
echo   Полные прогоны
echo   run.cmd all                  всё подряд: веб-версия, Android ^(APK на ПК и эмулятор^), качество сжатия, нагрузка, ПК-версия
echo   run.cmd web [разделы]        веб-версия: все разделы сайта в браузерах ^(прежнее run.cmd full^)
echo   run.cmd android [разделы]    Android: интерфейс APK на ПК, затем приложение на эмуляторе
echo   run.cmd android-pc           только интерфейс APK на ПК ^(прежнее run.cmd mobile^)
echo   run.cmd android-device       только приложение на эмуляторе ^(прежнее run.cmd device^)
echo   run.cmd device-both          эмулятор с сервисами Google и без них ^(DEVICE_AVD_NOGMS^)
echo   run.cmd device-pair          два телефона одновременно: чат и задачи в реальном времени
echo   run.cmd pc [части]           ПК-версия, без Docker: функции и сжатие, разделы под каждой ролью, действия, мессенджер
echo                                части: unit sections actions messenger ^(без частей - все^)
echo   run.cmd external / android-external   то же против внешнего тестового сервера из .env
echo   run.cmd compression          качество сжатия: браузеры, сервер и телефон дают одинаковый результат
echo   run.cmd load                 нагрузка по ступеням: от LOAD_USERS человек до LOAD_USERS_MIN
echo   set ONLY=большой файл         перед запуском: только тесты, в названии которых есть эти слова
echo.
echo   Запуск с тестовыми данными для ручной проверки
echo   run.cmd demo web             веб-версия: сервер с готовыми данными, сайт http://localhost:18000/app/
echo   run.cmd demo android         Android: те же данные + приложение на эмуляторе ^(то же, что run.cmd demo^)
echo   run.cmd demo nogms           Android на эмуляторе без сервисов Google
echo   run.cmd demo phone           Android на вашем смартфоне по USB ^(отладка по USB включена^)
echo   run.cmd demo pc [роль]       ПК-версия: тот же сервер, данные и учётные записи, что у demo web, + окно программы
echo   run.cmd stand                стенд: LOAD_USERS человек работают в фоне, вы проверяете сайт сами
echo   run.cmd server               только сервер в Docker: http://localhost:18000/app/
echo.
echo   Отладка на телефоне и служебные
echo   run.cmd video-log [phone]    журнал сжатия видео в отдельном окне ^(эмулятор или телефон^)
echo   run.cmd login-check          проверить вход всех ролей на запущенный сервер демо
echo   run.cmd notify [роль]        проверочное уведомление с ПК ^(по умолчанию монтажнику^), пока идёт демо
echo   run.cmd keys-check           проверить ключи Firebase из папки keys
echo   run.cmd version              версии модуля и проектов в папках project\web, project\android и project\pc
echo   run.cmd list                 все варианты проверки: прогоны, внешний сервер, ручная проверка сайта, разделы
echo   run.cmd build                собрать образы заранее
echo   run.cmd stop                 остановить всё после прерванного прогона
exit /b 0
