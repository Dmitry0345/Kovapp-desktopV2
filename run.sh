#!/usr/bin/env bash
set -u
cd "$(dirname "$0")"
[ -f VERSION ] && echo "Модуль тестов: версия $(cat VERSION)" || echo "Модуль тестов: версия не указана — обновите"
# папки вариантов приложения: веб-версия (сервер + сайт), Android, ПК; PROJECT_DIR — прежняя общая папка
envval() { grep -E "^$1=" .env 2>/dev/null | head -n 1 | cut -d= -f2-; }
OLDPROJ="$(envval PROJECT_DIR)"
export PROJECT_WEB_DIR="${PROJECT_WEB_DIR:-$(envval PROJECT_WEB_DIR)}"; PROJECT_WEB_DIR="${PROJECT_WEB_DIR:-${OLDPROJ:-./project/web}}"
export PROJECT_ANDROID_DIR="${PROJECT_ANDROID_DIR:-$(envval PROJECT_ANDROID_DIR)}"; PROJECT_ANDROID_DIR="${PROJECT_ANDROID_DIR:-${OLDPROJ:-./project/android}}"
export PROJECT_PC_DIR="${PROJECT_PC_DIR:-$(envval PROJECT_PC_DIR)}"; PROJECT_PC_DIR="${PROJECT_PC_DIR:-./project/pc}"
# архив распаковали «с лишней папкой» (project/web/web/...) — берём вложенную
[ ! -f "$PROJECT_WEB_DIR/server/server.py" ] && [ -f "$PROJECT_WEB_DIR/web/server/server.py" ] && PROJECT_WEB_DIR="$PROJECT_WEB_DIR/web"
[ ! -f "$PROJECT_ANDROID_DIR/mobile/app/build.gradle" ] && [ -f "$PROJECT_ANDROID_DIR/android/mobile/app/build.gradle" ] && PROJECT_ANDROID_DIR="$PROJECT_ANDROID_DIR/android"
[ -f "$PROJECT_WEB_DIR/server/server.py" ] || echo "ВНИМАНИЕ: в $PROJECT_WEB_DIR нет веб-версии — распакуйте web.zip в папку project"
[ -f "$PROJECT_ANDROID_DIR/mobile/app/build.gradle" ] || echo "ВНИМАНИЕ: в $PROJECT_ANDROID_DIR нет Android-версии — распакуйте android.zip в папку project"
PROJ="$PROJECT_WEB_DIR"
[ -f "$PROJ/VERSION" ] && echo "Веб-версия:    версия $(cat "$PROJ/VERSION") ($PROJ)" || echo "Веб-версия:    версия не указана ($PROJ)"
[ -f "$PROJECT_ANDROID_DIR/VERSION" ] && echo "Android:       версия $(cat "$PROJECT_ANDROID_DIR/VERSION") ($PROJECT_ANDROID_DIR)" || echo "Android:       версия не указана ($PROJECT_ANDROID_DIR)"
[ ! -f "$PROJECT_PC_DIR/main.py" ] && [ -f "$PROJECT_PC_DIR/pc/main.py" ] && PROJECT_PC_DIR="$PROJECT_PC_DIR/pc"
[ -f "$PROJECT_PC_DIR/VERSION" ] && echo "ПК-версия:     версия $(cat "$PROJECT_PC_DIR/VERSION") ($PROJECT_PC_DIR)" || echo "ПК-версия:     версия не указана ($PROJECT_PC_DIR)"
[ "${1:-}" = "version" ] && exit 0
[ -f pc/main.py ] && [ -d pc/kov ] && { echo "ВНИМАНИЕ: архив pc.zip распакован в папку модуля, а нужно в папку project: перенесите папку pc в project (получится project/pc/main.py)"; exit 1; }
for d in */; do [ -f "${d}run.sh" ] && { echo "ВНИМАНИЕ: в папке ${d} ещё один модуль тестов — архив распакован во вложенную папку, обновление не применилось. Перенесите её содержимое сюда с заменой и удалите папку."; exit 1; }; done

# ===== список всех вариантов проверки — без Docker =====
if [ "${1:-}" = "list" ]; then
  PORT="$(envval FULL_PUBLISH_PORT)"; PORT="${PORT:-18000}"
  ONLY_HINT='ONLY="слова" ./run.sh web   только тесты, в названии которых есть эти слова'
  sed -e "s#{RUN}#./run.sh#g" -e "s#{PORT}#$PORT#g" tools/list.txt | awk -v only="$ONLY_HINT" '{ i = index($0, "{ONLY}"); if (i) $0 = substr($0, 1, i - 1) only substr($0, i + 6); print }'
  echo
  echo "РАЗДЕЛЫ (для web, android, external и других вариантов)"
  awk '/^ *- *\{key:/ {
    match($0, /key: *[A-Za-z0-9_-]+/); key = substr($0, RSTART, RLENGTH); sub(/key: */, "", key)
    match($0, /title: *"[^"]*"/); title = substr($0, RSTART, RLENGTH); sub(/title: *"/, "", title); sub(/"$/, "", title)
    mark = ""
    if (match($0, /targets: *\[[^]]*\]/)) {
      t = substr($0, RSTART, RLENGTH); sub(/targets: *\[/, "", t); sub(/\]/, "", t); gsub(/ /, "", t)
      if (t == "apk") mark = "  [только мобильная]"; else if (t == "device") mark = "  [только устройство]"; else if (t != "web,apk,device") mark = "  [только сайт]"
    }
    if ($0 ~ /manual: *true/) mark = mark "  [отдельной командой: ./run.sh " key "]"
    printf "  %-17s %s%s\n", key, title, mark
  }' config/sections.yaml
  exit 0
fi

# ===== окружение Python для ПК-версии (.venv-pc): создаётся при первом запуске, библиотеки — из requirements.txt =====
pc_env() {
  P="$PROJECT_PC_DIR"
  [ -f "$P/main.py" ] || { echo "Нет ПК-версии в $P — распакуйте pc.zip в папку project (получится project/pc/main.py)"; return 1; }
  mkdir -p logs
  if [ ! -x .venv-pc/bin/python ]; then
    PY="$(command -v python3 || command -v python)"
    [ -n "$PY" ] || { echo "Не найден Python 3.10–3.12"; return 1; }
    "$PY" -m venv .venv-pc || { echo "Окружение .venv-pc не создалось (Debian/Ubuntu: sudo apt install python3-venv)"; return 1; }
  fi
  .venv-pc/bin/python -m pip install --disable-pip-version-check -r "$P/requirements.txt" || { echo "Библиотеки не установились"; return 3; }
  # в комплекте программы FFmpeg для Windows; здесь — свой, с OpenH264 (apt-пакет ffmpeg собран без него)
  command -v ffmpeg >/dev/null 2>&1 || [ -n "${KOVAPP_FFMPEG_DIR:-}" ] \
    || echo "ВНИМАНИЕ: нет ffmpeg и ffprobe — укажите папку со сборкой с OpenH264 в KOVAPP_FFMPEG_DIR (docs/PC.md)"
  RUN=()
  if [ "$(uname)" = "Linux" ] && [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
    command -v xvfb-run >/dev/null 2>&1 || { echo "Нет экрана: установите xvfb (sudo apt install xvfb) или запустите из графического сеанса"; return 1; }
    RUN=(xvfb-run -a -s "-screen 0 1600x1000x24")
  fi
  return 0
}

# ===== ПК-версия — без Docker: Python этого компьютера в .venv-pc, тестовый сервер внутри прогона =====
if [ "${1:-}" = "pc" ]; then
  shift
  pc_env || exit $?
  PARTS="$(IFS=,; echo "$*")"
  ${RUN[@]+"${RUN[@]}"} .venv-pc/bin/python kovtest_pc/run_pc.py --project "$P" --parts "${PARTS:-unit,sections,actions,messenger}"
  RC=$?
  if [ -f logs/last-run.txt ]; then
    R="$(cat logs/last-run.txt)"
    printf '\nПолный журнал:  logs/%s/full.log\nКраткий итог:   logs/%s/summary.txt\nСнимки экранов: logs/%s/index.html\n' "$R" "$R" "$R"
  fi
  exit $RC
fi

# ===== проверки =====
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Создан файл .env из .env.example. Проверьте настройки и запустите снова."
  exit 1
fi
command -v docker >/dev/null 2>&1 || { echo "Docker не найден"; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker не запущен"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "Не работает «docker compose»: подключите модуль Compose (docker-compose-plugin) и повторите"; exit 1; }

mkdir -p logs

# ===== разбор команды =====
CMD="${1:-full}"
[ $# -gt 0 ] && shift
SECTIONS="$(IFS=,; echo "$*")"
[ -z "$SECTIONS" ] && SECTIONS="all"

save_console() {
  [ -f logs/last-run.txt ] || return 0
  RUNDIR="$(cat logs/last-run.txt)"
  [ -d "logs/$RUNDIR" ] && docker compose --profile full logs --no-color server > "logs/$RUNDIR/server-console.log" 2>&1
  return 0
}

show_result() {
  [ -f logs/last-run.txt ] || return 0
  RUNDIR="$(cat logs/last-run.txt)"
  echo
  echo "Полный журнал:  logs/$RUNDIR/full.log"
  echo "Краткий итог:   logs/$RUNDIR/summary.txt"
}

server_failed() {
  echo "Сервер в Docker не запустился. Частые причины: нет проекта в папке веб-версии ($PROJECT_WEB_DIR, PROJECT_WEB_DIR в .env) или занят порт FULL_PUBLISH_PORT."
  docker compose --profile full logs --no-color --tail 80 server
  docker compose --profile full down --remove-orphans >/dev/null 2>&1
  exit 3
}

# ===== новые имена команд (прежние работают как раньше) =====
case "$CMD" in
  web) CMD=full ;;
  web-external) CMD=external ;;
  android-pc) CMD=mobile ;;
  android-external) CMD=mobile-external ;;
  android-device) CMD=device ;;
esac
LOADC="docker compose -f docker-compose.yml -f docker-compose.load.yml --profile full"

# ===== команды =====
case "$CMD" in
  full)
    docker compose --profile full down --remove-orphans >/dev/null 2>&1
    [ "${SKIP_BUILD:-0}" = "1" ] || docker compose --profile full build || { echo "Сборка образов не удалась"; exit 3; }
    docker compose --profile full up -d --wait db server || server_failed
    docker compose --profile full run --rm -e ONLY tests --mode full --sections "$SECTIONS"
    RC=$?
    save_console
    docker compose --profile full down --remove-orphans >/dev/null 2>&1
    show_result
    exit $RC
    ;;
  external)
    [ "${SKIP_BUILD:-0}" = "1" ] || docker compose build tests || { echo "Сборка образа тестов не удалась"; exit 3; }
    docker compose run --rm -e ONLY tests --mode external --sections "$SECTIONS"
    RC=$?
    show_result
    exit $RC
    ;;
  mobile)
    docker compose --profile full down --remove-orphans >/dev/null 2>&1
    [ "${SKIP_BUILD:-0}" = "1" ] || docker compose --profile full build || { echo "Сборка образов не удалась"; exit 3; }
    docker compose --profile full up -d --wait db server || server_failed
    docker compose --profile full run --rm -e ONLY tests --mode full --target apk --sections "$SECTIONS"
    RC=$?
    save_console
    docker compose --profile full down --remove-orphans >/dev/null 2>&1
    show_result
    exit $RC
    ;;
  mobile-external)
    [ "${SKIP_BUILD:-0}" = "1" ] || docker compose build tests || { echo "Сборка образа тестов не удалась"; exit 3; }
    docker compose run --rm -e ONLY tests --mode external --target apk --sections "$SECTIONS"
    RC=$?
    show_result
    exit $RC
    ;;
  device)
    # эмулятор Android Studio на этом компьютере; Appium, сервер, сборка APK и тесты — в Docker
    SDK="${ANDROID_SDK:-${ANDROID_HOME:-$HOME/Android/Sdk}}"
    [ -d "$SDK" ] || SDK="$HOME/Library/Android/sdk"
    ADB="$SDK/platform-tools/adb"
    [ -x "$ADB" ] || { echo "Не найден Android SDK — укажите ANDROID_SDK в .env"; exit 1; }
    "$ADB" start-server >/dev/null 2>&1
    if ! "$ADB" devices | grep -q "device$"; then
      AVD="${DEVICE_AVD:-$("$SDK/emulator/emulator" -list-avds | grep -E '^[A-Za-z0-9_.-]+$' | head -n 1)}"
      [ -n "$AVD" ] || { echo "Нет эмулятора: создайте его в Android Studio (Device Manager → +)"; exit 1; }
      echo "Запускаю эмулятор $AVD..."
      nohup "$SDK/emulator/emulator" -avd "$AVD" -no-boot-anim >/dev/null 2>&1 &
      "$ADB" wait-for-device
    fi
    until [ "$("$ADB" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ]; do sleep 2; done
    "$ADB" kill-server >/dev/null 2>&1
    export ADB_KEYS_DIR="$HOME/.android"
    docker compose --profile full --profile device --profile pair down --remove-orphans >/dev/null 2>&1
    [ "${SKIP_BUILD:-0}" = "1" ] || docker compose --profile full --profile device build db server keycheck apk device || { echo "Сборка образов не удалась"; exit 3; }
    docker compose --profile full up -d --wait db server || server_failed
    docker compose --profile device run --rm keycheck
    docker compose --profile device run --rm apk || { echo "Сборка APK не удалась"; docker compose --profile full --profile device --profile pair down >/dev/null 2>&1; exit 3; }
    DEVICE_ADB=host.docker.internal:5601 docker compose --profile device up -d --wait appium \
      || { echo "Appium не подключился к эмулятору:"; docker compose --profile device logs --tail 30 appium; docker compose --profile full --profile device --profile pair down >/dev/null 2>&1; exit 3; }
    docker compose --profile device run --rm -e ONLY device --mode full --target device --sections "$SECTIONS"
    RC=$?
    save_console
    docker compose --profile device logs --no-color appium > logs/appium-last.log 2>&1
    docker compose --profile full --profile device --profile pair down --remove-orphans >/dev/null 2>&1
    "$ADB" start-server >/dev/null 2>&1
    show_result
    exit $RC
    ;;
  android)
    NOGMS="$(envval DEVICE_AVD_NOGMS)"
    echo "===== Android, часть 1 из 2: интерфейс APK на ПК ====="; "$0" mobile "$@"; RC_APK=$?
    echo "===== Android, часть 2 из 2: приложение на эмуляторе ====="
    if [ -n "$NOGMS" ]; then "$0" device-both; else "$0" device; fi; RC_DEV=$?
    echo "Итог: APK на ПК — $RC_APK, Android на эмуляторе — $RC_DEV (0 — всё прошло)"
    [ "$RC_APK" = "0" ] && exit $RC_DEV || exit $RC_APK
    ;;
  load)
    $LOADC down --remove-orphans >/dev/null 2>&1
    [ "${SKIP_BUILD:-0}" = "1" ] || $LOADC build || { echo "Сборка образов не удалась"; exit 3; }
    $LOADC up -d --wait db server || server_failed
    $LOADC run --rm -e PARALLEL=1 -e RUN_LABEL=load -e ONLY tests --mode full --sections @load
    RC=$?
    save_console
    $LOADC down --remove-orphans >/dev/null 2>&1
    show_result
    exit $RC
    ;;
  stand)
    $LOADC down --remove-orphans >/dev/null 2>&1
    [ "${SKIP_BUILD:-0}" = "1" ] || $LOADC build || { echo "Сборка образов не удалась"; exit 3; }
    $LOADC up -d --wait db server || server_failed
    echo "Стенд: продукт http://localhost:18000/app/, панель http://localhost:18090/ (остановить — Ctrl+C)"
    $LOADC run --rm -p 18090:8090 -e PARALLEL=1 tests --mode full --stand
    $LOADC down --remove-orphans >/dev/null 2>&1
    exit 0
    ;;
  compression)
    docker compose --profile full down --remove-orphans >/dev/null 2>&1
    [ "${SKIP_BUILD:-0}" = "1" ] || docker compose --profile full build || { echo "Сборка образов не удалась"; exit 3; }
    docker compose --profile full up -d --wait db server || server_failed
    docker compose --profile full run --rm -e PARALLEL=1 -e RUN_LABEL=compression -e ONLY tests --mode full --sections compression
    RC=$?
    save_console
    # ПК-версия: те же исходники сжимает её код; разбор и общий отчёт — вторым коротким прогоном (CMP_PC=0 — не включать)
    if [ "$(envval CMP_PC)" != "0" ] && [ -f "$PROJECT_PC_DIR/main.py" ]; then
      echo; echo "===== Качество сжатия на ПК ====="
      "$0" pc compression
      docker compose --profile full run --rm -e PARALLEL=1 -e RUN_LABEL=compression-pc -e CMP_BROWSERS=none -e CMP_SERVER=0 \
        tests --mode full --sections compression
      RC_PCC=$?; save_console; [ "$RC" = "0" ] && RC=$RC_PCC
    fi
    docker compose --profile full down --remove-orphans >/dev/null 2>&1
    show_result
    [ -n "${RUNDIR:-}" ] && echo "Отчёт сравнения: logs/$RUNDIR/compression-report.html"
    CMPP="$(envval CMP_PHONE)"; SDK="$(envval ANDROID_SDK)"
    if [ "$CMPP" = "1" ] || { [ "${CMPP:-auto}" = "auto" ] && [ -x "${SDK:-/nonexistent}/platform-tools/adb" ]; }; then
      "$0" device compression; RC2=$?; [ "$RC" = "0" ] && exit $RC2
    fi
    exit $RC
    ;;
  all)
    NOGMS="$(envval DEVICE_AVD_NOGMS)"
    echo "===== Часть 1 из 6: веб-версия, все разделы ====="; "$0" full; RC_SITE=$?
    echo "===== Часть 2 из 6: Android — интерфейс APK на ПК ====="; "$0" mobile; RC_APK=$?
    echo "===== Часть 3 из 6: Android — приложение на эмуляторе ====="
    if [ -n "$NOGMS" ]; then "$0" device-both; else "$0" device; fi; RC_DEV=$?
    echo "===== Часть 4 из 6: качество сжатия ====="; "$0" compression; RC_CMP=$?
    echo "===== Часть 5 из 6: нагрузка по ступеням ====="; "$0" load; RC_LOAD=$?
    echo "===== Часть 6 из 6: ПК-версия ====="; "$0" pc; RC_PC=$?
    echo "Итог: веб — $RC_SITE, APK на ПК — $RC_APK, Android — $RC_DEV, сжатие — $RC_CMP, нагрузка — $RC_LOAD, ПК — $RC_PC (0 — всё прошло)"
    for rc in $RC_SITE $RC_APK $RC_DEV $RC_CMP $RC_LOAD $RC_PC; do [ "$rc" = "0" ] || exit "$rc"; done; exit 0
    ;;
  demo)
    # ручная проверка сайта с готовыми данными; demo pc [роль] — и окно ПК-версии на том же сервере
    # (телефон в этом режиме — через run.cmd demo, Windows)
    DEMO_PC=""; ROLE=""
    if [ "${1:-}" = "pc" ]; then DEMO_PC=1; ROLE="${2:-}"; pc_env || exit $?; fi
    docker compose --profile full --profile device --profile pair down --remove-orphans >/dev/null 2>&1
    docker compose --profile full build && docker compose --profile full up -d --wait db server || exit 3
    docker compose --profile full run --rm tests --mode full --demo || exit 3
    PORT="$(envval FULL_PUBLISH_PORT)"; PORT="${PORT:-18000}"
    echo "Сайт: http://localhost:$PORT/app/  — учётные записи выше и в logs/demo-accounts.txt"
    if [ -n "$DEMO_PC" ]; then
      rm -f logs/demo-pc.stop
      ARGS=(--project "$PROJECT_PC_DIR" --host "http://localhost:$PORT" --stop-file "$PWD/logs/demo-pc.stop" --log "$PWD/logs/demo-pc.log")
      [ -n "$ROLE" ] && ARGS+=(--role "$ROLE")
      ${RUN[@]+"${RUN[@]}"} .venv-pc/bin/python kovtest_pc/demo_pc.py "${ARGS[@]}" &
      PCPID=$!
      echo "ПК-версия: окно Kovapp${ROLE:+, вход ролью $ROLE} — тот же сервер, что у сайта; журнал logs/demo-pc.log"
    fi
    read -r -p "Когда закончите — нажмите Enter: всё остановится. " _
    if [ -n "$DEMO_PC" ]; then
      touch logs/demo-pc.stop; sleep 2; kill "$PCPID" 2>/dev/null; wait "$PCPID" 2>/dev/null; rm -f logs/demo-pc.stop
    fi
    docker compose --profile full down --remove-orphans
    exit 0
    ;;
  device-pair)
    echo "Два телефона одновременно пока запускаются через run.cmd (Windows): эмуляторы управляются tools/android-host.ps1"
    exit 1
    ;;
  device-both)
    NOGMS="$(grep -E '^DEVICE_AVD_NOGMS=' .env | head -n 1 | cut -d= -f2-)"
    GMS="$(grep -E '^DEVICE_AVD=' .env | head -n 1 | cut -d= -f2-)"
    [ -n "$NOGMS" ] || { echo "Укажите в .env DEVICE_AVD_NOGMS — эмулятор без сервисов Google (docs/DEVICE.md)"; exit 1; }
    echo "===== Прогон 1 из 2: эмулятор с сервисами Google ====="
    DEVICE_AVD="$GMS" "$0" device "$SECTIONS"; RC1=$?
    pkill -f "\-ports 5600,5601" 2>/dev/null; sleep 5
    echo "===== Прогон 2 из 2: эмулятор без сервисов Google ($NOGMS) ====="
    DEVICE_AVD="$NOGMS" "$0" device "$SECTIONS"; RC2=$?
    echo "Итог: с Google — код $RC1, без Google — код $RC2 (0 — всё прошло)"
    [ "$RC1" = "0" ] && exit $RC2 || exit $RC1
    ;;
  stop)
    docker compose --profile full --profile device --profile pair down --remove-orphans
    pkill -f "\-ports 5600,5601" 2>/dev/null && echo "Эмулятор для тестов закрыт"
    echo "Всё остановлено"
    exit 0
    ;;
  keys-check)
    docker compose --profile device build keycheck && docker compose --profile device run --rm keycheck
    exit $?
    ;;
  list) ;;   # обрабатывается выше, без Docker
  build) docker compose --profile full build ;;
  server)
    docker compose --profile full down --remove-orphans >/dev/null 2>&1
    docker compose --profile full build db server
    docker compose --profile full up -d --wait db server || server_failed
    echo "Сервер запущен: http://localhost:18000/app/  (остановить: ./run.sh stop)"
    ;;
  stop) docker compose --profile full down --remove-orphans ;;
  *)
    echo "  ./run.sh all                   всё подряд: веб, Android (APK на ПК и эмулятор), сжатие, нагрузка, ПК-версия"
    echo "  ./run.sh web [разделы]         веб-версия (прежнее full)"
    echo "  ./run.sh android [разделы]     Android: APK на ПК, затем эмулятор"
    echo "  ./run.sh android-pc | android-device | device-both"
    echo "  ./run.sh external | android-external   против внешнего тестового сервера"
    echo "  ./run.sh compression | load | stand"
    echo "  ./run.sh pc [части]            ПК-версия без Docker (части: unit sections actions messenger)"
    echo "  ./run.sh demo [web]            ручная проверка сайта с тестовыми данными (телефон — через run.cmd на Windows)"
    echo "  ./run.sh demo pc [роль]        то же + окно ПК-версии на этом сервере (те же учётные записи)"
    echo "  ./run.sh list                   все варианты проверки: прогоны, внешний сервер, ручная проверка сайта, разделы"
    echo "  ./run.sh build | server | stop | version"
    ;;
esac
