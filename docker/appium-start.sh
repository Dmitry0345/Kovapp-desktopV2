#!/bin/sh
# Appium для настоящего Android: эмулятор Android Studio на этом компьютере подключается по adb через сеть.
# ключ adb этого компьютера: эмулятор ему уже доверяет — окна «Разрешить отладку?» не будет
if [ -f /host-adb/adbkey ]; then
  mkdir -p /root/.android && cp /host-adb/adbkey /host-adb/adbkey.pub /root/.android/ 2>/dev/null
fi
adb start-server >/dev/null 2>&1
TARGETS="${DEVICE_ADB:-host.docker.internal:5601}"
SERIAL=""
echo "Подключаюсь к эмулятору ($TARGETS)..."
i=0
HINTED=""
while [ $i -lt 90 ] && [ -z "$SERIAL" ]; do
  for t in $TARGETS; do
    adb connect "$t" >/dev/null 2>&1
    STATE="$(adb -s "$t" get-state 2>&1)"
    if [ "$STATE" = "device" ]; then SERIAL="$t"; break; fi
    # эмулятор ждёт разрешения на отладку — подсказать человеку и подождать его дольше
    if [ -z "$HINTED" ] && echo "$STATE" | grep -q unauthorized; then
      echo "На эмуляторе окно «Allow USB debugging?»: отметьте «Always allow from this computer» и нажмите Allow."
      echo "(Окно бывает, если ключа adb этого компьютера не было при запуске эмулятора; в следующий раз его не будет.)"
      HINTED=1
      i=0
    fi
  done
  i=$((i + 1))
  [ -z "$SERIAL" ] && sleep 2
done
if [ -z "$SERIAL" ]; then
  echo "НЕТ СВЯЗИ С ЭМУЛЯТОРОМ ($TARGETS)"
  exit 3
fi
echo "Эмулятор подключён: $SERIAL. Жду загрузки Android..."
i=0
while [ "$(adb -s "$SERIAL" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" != "1" ]; do
  i=$((i + 1))
  if [ $i -gt 240 ]; then echo "ANDROID НЕ ЗАГРУЗИЛСЯ за 8 минут"; exit 3; fi
  [ $((i % 15)) -eq 0 ] && echo "  всё ещё загружается ($((i * 2)) с)..."
  sleep 2
done
echo "Android загружен"
# связь телефона с сервером и служебные команды для тестов
python3 /opt/kovapp/link.py "$SERIAL" &
# подробный журнал Appium — в logs/appium-debug.log (в окне — только предупреждения)
LOG="${APPIUM_LOG:-/logs/appium-debug.log}"
rm -f "$LOG" 2>/dev/null
exec appium --address 0.0.0.0 --port 4723 --log-level warn:debug --log "$LOG" \
  --allow-insecure "*:adb_shell"
