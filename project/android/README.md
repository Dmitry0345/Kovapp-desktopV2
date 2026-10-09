# Kovapp beta 3 — Android-версия

Исходники приложения для Android (`mobile/`) и интерфейс, из которого собирается APK (`web_app/`).
Сборка — `mobile/README.md`; устройство приложения — `docs/mobile-architecture.md`, безопасность — `docs/mobile-security.md`.

Сервер в эту папку не входит: он в архиве `web.zip`. Симулятор телефона и «APK в браузере»
(`mobile/tools/devserver.py`) берут заголовки безопасности сервера из соседней папки `web`
(или из `KOVAPP_SERVER_ROOT`).
