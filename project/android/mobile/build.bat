@echo off
chcp 65001 >nul
rem Сборка APK в Windows: нужен JDK 17+, Android SDK (ANDROID_HOME) и Gradle 8.14+.
rem Без них проще Android Studio или Docker (см. README.md).
cd /d "%~dp0"
if not exist kovapp.properties (echo Нет kovapp.properties: скопируйте kovapp.properties.example & exit /b 1)
if exist gradlew.bat (set G=gradlew.bat) else (set G=gradle)
if "%~1"=="" (call %G% --no-daemon assembleRelease) else (call %G% --no-daemon %*)
echo Готово: app\build\outputs\apk\
