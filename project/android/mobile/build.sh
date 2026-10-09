#!/bin/sh
# Сборка APK на Linux/macOS: нужен JDK 17+, Android SDK (ANDROID_HOME) и Gradle 8.14+.
# Без них проще Android Studio или Docker (см. README.md).
set -e
cd "$(dirname "$0")"
[ -f kovapp.properties ] || { echo "Нет kovapp.properties: скопируйте kovapp.properties.example и впишите адрес сервера"; exit 1; }
if [ -x ./gradlew ]; then G=./gradlew; else G=gradle; fi
$G --no-daemon ${@:-assembleRelease}
echo "Готово: app/build/outputs/apk/"
