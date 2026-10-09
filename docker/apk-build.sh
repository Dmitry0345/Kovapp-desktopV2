#!/bin/sh
# Сборка отладочного APK из папки проекта. Рабочая копия живёт в томе /work — Gradle
# пересобирает только изменения; ключ отладочной подписи — в томе /root/.android (одна подпись).
if [ ! -f /project/mobile/app/build.gradle ]; then
  echo "В папке Android-версии нет mobile/app/build.gradle — положите проект Kovapp в папку project/android (или укажите PROJECT_ANDROID_DIR)"
  exit 2
fi
mkdir -p /work /apk /root/.gradle
# сжатые копии статики (*.gz, *.br) и файлы Android Studio с путями Windows не нужны
rsync -a --delete --exclude 'build/' --exclude '.gradle/' --exclude '.idea/' --exclude 'local.properties' \
      --exclude '*.gz' --exclude '*.br' /project/mobile/ /work/mobile/
rsync -a --delete --exclude '*.gz' --exclude '*.br' /project/web_app/ /work/web_app/
cd /work/mobile
[ -f kovapp.properties ] || cp kovapp.properties.example kovapp.properties
# настройки ПК (прокси Android Studio, путь к JDK Windows) в контейнере не работают
[ -f gradle.properties ] && sed -i -E '/^[[:space:]]*(systemProp\.https?\.(proxy|nonProxy)|org\.gradle\.java\.home)/d' gradle.properties

# ===== Gradle: IPv4, терпеливые скачивания, прокси Docker (если он задан) =====
PROXY="${HTTPS_PROXY:-${https_proxy:-${HTTP_PROXY:-${http_proxy:-}}}}"
{
  echo "org.gradle.jvmargs=-Xmx3g -Dfile.encoding=UTF-8 -Djava.net.preferIPv4Stack=true"
  echo "systemProp.java.net.preferIPv4Stack=true"
  echo "systemProp.org.gradle.internal.http.connectionTimeout=120000"
  echo "systemProp.org.gradle.internal.http.socketTimeout=120000"
  echo "systemProp.org.gradle.internal.repository.max.retries=6"
  echo "systemProp.org.gradle.internal.repository.initial.backoff=2000"
  if [ -n "$PROXY" ]; then
    hostport="${PROXY#*://}"; hostport="${hostport##*@}"; hostport="${hostport%%/*}"
    host="${hostport%:*}"; port="${hostport##*:}"; [ "$port" = "$host" ] && port=80
    nonproxy="$(echo "${NO_PROXY:-${no_proxy:-localhost}}" | tr ',' '|')"
    for scheme in http https; do
      echo "systemProp.$scheme.proxyHost=$host"
      echo "systemProp.$scheme.proxyPort=$port"
      echo "systemProp.$scheme.nonProxyHosts=$nonproxy"
    done
    echo "Прокси Docker передан Gradle: $host:$port" >&2
  fi
} > /root/.gradle/gradle.properties

# ===== доступ к репозиториям (curl учитывает прокси Docker) =====
echo "Доступ к репозиториям библиотек:"
NET_OK=1
for pair in "Google|https://dl.google.com/dl/android/maven2/master-index.xml" \
            "Maven Central|https://repo.maven.apache.org/maven2/" \
            "Gradle Plugin Portal|https://plugins.gradle.org/m2/"; do
  name="${pair%%|*}"; url="${pair#*|}"
  code="$(curl -s -o /dev/null -m 25 -w '%{http_code}' "$url" 2>/dev/null)"
  case "$code" in
    2*|3*) echo "  ✓ $name" ;;
    000)   echo "  ✗ $name — нет соединения"; NET_OK=0 ;;
    *)     echo "  ✗ $name — ответ $code"; NET_OK=0 ;;
  esac
done
[ "$NET_OK" = "1" ] || echo "  Часть репозиториев недоступна. Если библиотеки уже скачаны прошлыми сборками — не страшно"

ARGS="-Pkovapp.debugServerOrigin=http://localhost:${DEVICE_PORT:-8000}"
# доставка через Google: четыре значения из keys/google-services (если он там есть)
FCM="$(python3 /opt/kovapp/fcm_args.py /keys || true)"
if [ -n "$FCM" ]; then
  echo "Firebase: значения для приложения взяты из keys/google-services"
  ARGS="$ARGS $FCM"
fi
echo "Сборка отладочного APK (первый раз — 10-15 минут: скачиваются библиотеки)..."
{ gradle --console=plain --stacktrace assembleDebug $ARGS; echo $? > /tmp/gradle.rc; } 2>&1 | tee /tmp/gradle.log
RC="$(cat /tmp/gradle.rc 2>/dev/null || echo 1)"
if [ "$RC" != "0" ]; then
  echo
  echo "===== ПЕРВОПРИЧИНА (из журнала Gradle) ====="
  grep -E "^Caused by: " /tmp/gradle.log | sed -E 's/^Caused by: //' | awk '!seen[$0]++' | tail -n 6
  CAUSE="$(grep -E '^Caused by: ' /tmp/gradle.log)"
  echo
  case "$CAUSE" in
    *UnknownHostException*)   echo "Подсказка: контейнер не находит адрес репозитория (DNS). Проверьте интернет и настройки DNS в Docker Desktop." ;;
    *"Connection refused"*)   echo "Подсказка: соединение отклонено — вероятно, мешает прокси или файрвол. Если интернет у вас через прокси — укажите его в Docker Desktop (Settings → Resources → Proxies)." ;;
    *SSLHandshake*|*"handshake"*|*"Remote host terminated"*) echo "Подсказка: соединение обрывается на этапе шифрования — провайдер ограничивает доступ. Включите VPN на время первой сборки: скачанное сохранится." ;;
    *SocketTimeout*|*"timed out"*) echo "Подсказка: репозиторий не отвечает — сеть медленная или доступ ограничен. Повторите запуск или включите VPN на время первой сборки." ;;
    *" 407"*|*"Proxy Authentication"*) echo "Подсказка: прокси требует пароль — укажите его в Docker Desktop (Settings → Resources → Proxies)." ;;
    *) [ "$NET_OK" = "1" ] || echo "Подсказка: репозитории недоступны из Docker (см. проверку выше). Включите VPN на время первой сборки: скачанное сохранится." ;;
  esac
  exit 1
fi
cp app/build/outputs/apk/debug/app-debug.apk /apk/app-debug.apk
echo "APK готов"
