# -*- coding: utf-8 -*-
"""Сборка приложения.

Роутеры подключаются строго в том же порядке, в каком маршруты шли
в исходном server.py: FastAPI разбирает пути по очереди, и перестановка
изменила бы поведение пересекающихся маршрутов
(например /chat/download/{message_id} и /chat/download/{file_path:path}).
"""

from .core import app
from .routers import (
    chat,
    auth,
    files,
    stages,
    employees,
    tasks,
    notifications,
    templates,
    organizations,
    csv_io,
    chat_ws,
    admin_health,
    projects,
    push,
    preferences,
    warehouse,
    equipment, uploads,
    native_push,
)
from . import task_completion  # noqa: E402  — завершение задачи через подтверждение
from . import bootstrap  # noqa: F401  — регистрирует startup/shutdown

app.include_router(chat.router)
app.include_router(auth.router)
app.include_router(files.router)
app.include_router(uploads.router)
app.include_router(stages.router)
app.include_router(employees.router)
app.include_router(tasks.router)
app.include_router(notifications.router)
app.include_router(templates.router)
app.include_router(organizations.router)
app.include_router(csv_io.router)
app.include_router(chat_ws.router)
app.include_router(admin_health.router)
# Версия 2: проекты, push-уведомления, настройки пользователя
app.include_router(projects.router)
app.include_router(push.router)
app.include_router(preferences.router)
app.include_router(warehouse.router)
app.include_router(equipment.router)
app.include_router(task_completion.router)
# Приложение Kovapp для Android: регистрация телефонов и собственный канал уведомлений
app.include_router(native_push.router)


# База недоступна или соединение сломалось: отвечаем 503 коротко и понятно,
# вместо трассировки на экране и повторного использования битого соединения
from fastapi import Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from sqlalchemy.exc import OperationalError, InterfaceError, InternalError  # noqa: E402


@app.exception_handler(OperationalError)
@app.exception_handler(InterfaceError)
@app.exception_handler(InternalError)
async def _db_unavailable(request: Request, exc: Exception):
    from .config import logger
    logger.error("База данных недоступна на %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=503, content={"detail": "База данных временно недоступна, попробуйте позже"})


@app.exception_handler(MemoryError)
async def _out_of_memory(request: Request, exc: Exception):
    from .config import logger
    logger.error("Не хватило памяти на %s %s", request.method, request.url.path)
    return JSONResponse(status_code=503, content={"detail": "Серверу не хватило памяти — повторите позже"})

# Веб-приложение можно раздавать прямо сервером (WEB_APP_DIR в .env);
# в проде предпочтительнее nginx, см. инструкцию по развёртыванию
from .config import WEB_APP_DIR  # noqa: E402
if WEB_APP_DIR:
    import os
    import logging
    from fastapi import HTTPException
    import mimetypes
    from fastapi.staticfiles import StaticFiles
    from . import gzip_tools
    from .config import STATIC_PRECOMPRESS
    # модули PDF.js браузер выполняет только с типом JavaScript
    mimetypes.add_type("text/javascript", ".mjs")
    mimetypes.add_type("application/wasm", ".wasm")

    class WebAppFiles(StaticFiles):
        """Статика приложения с запретом кэширования разметки и скриптов.

        Браузер по своему усмотрению держал index.html в кэше, и после
        обновления папки страница продолжала подключать старый список файлов:
        новые модули не загружались вовсе. Теперь HTML, JS и CSS каждый раз
        сверяются с сервером, а картинки и шрифты кэшируются как обычно.
        """

        NO_CACHE = (".html", ".js", ".css", ".webmanifest")

        def file_response(self, full_path, stat_result, scope, status_code=200):
            """Готовая сжатая копия вместо сжатия на каждый запрос.

            Ответ собирает сам StaticFiles (так работает любая версия Starlette),
            здесь лишь подменяется файл на .gz и дописываются заголовки. При любой
            ошибке отдаётся обычный файл: раздача приложения не должна падать.
            """
            try:
                accept = ""
                for key, value in scope.get("headers") or []:
                    if key == b"accept-encoding":
                        accept = value.decode("latin-1").lower()
                        break
                if "gzip" in accept and gzip_tools.static_compressible(full_path):
                    packed = str(full_path) + ".gz"
                    pst = os.stat(packed) if os.path.isfile(packed) else None
                    if pst is not None and gzip_tools.fresh(stat_result, pst):
                        response = super().file_response(packed, pst, scope, status_code)
                        media_type = mimetypes.guess_type(str(full_path))[0] or "application/octet-stream"
                        if media_type.startswith("text/") or media_type in ("application/json", "image/svg+xml"):
                            media_type += "; charset=utf-8"
                        response.headers["content-type"] = media_type
                        response.headers["content-encoding"] = "gzip"
                        response.headers["vary"] = "Accept-Encoding"
                        return response
            except Exception:
                logging.getLogger(__name__).exception("Статика: сжатая копия %s не отдана, отдаю обычный файл", full_path)
            return super().file_response(full_path, stat_result, scope, status_code)

        async def get_response(self, path, scope):
            try:
                response = await super().get_response(path, scope)
            except (OSError, ValueError) as e:
                # Путь, который файловая система принять не может (например,
                # с двоеточием внутри на Windows), приходил из ошибочной ссылки
                # клиента и валил обработчик с трассировкой на пол-экрана.
                # Такому запросу место в 404, а в журнале — одна строка.
                logging.getLogger(__name__).info("Статика: некорректный путь %r (%s)", path, e)
                raise HTTPException(status_code=404, detail="Файл не найден")
            if str(path).lower().endswith(self.NO_CACHE) or path in ("", "."):
                response.headers["Cache-Control"] = "no-cache, must-revalidate"
            return response

    if os.path.isdir(WEB_APP_DIR):
        @app.get("/favicon.ico", include_in_schema=False)
        async def favicon():
            # браузеры сами спрашивают значок в корне сайта — отдаём тот же знак, что на вкладках
            from fastapi.responses import FileResponse
            icon_path = os.path.join(WEB_APP_DIR, "icons", "kovapp-192.png")
            if not os.path.isfile(icon_path):
                raise HTTPException(status_code=404, detail="Файл не найден")
            return FileResponse(icon_path, media_type="image/png", headers={"Cache-Control": "no-cache"})

        app.mount("/app", WebAppFiles(directory=WEB_APP_DIR, html=True), name="webapp")
        if STATIC_PRECOMPRESS:
            # один раз после обновления файлов; дальше отдаются готовые .gz
            import threading
            threading.Thread(target=lambda: logging.getLogger(__name__).info(
                "Статика: сжатые копии готовы %s", gzip_tools.precompress_tree(WEB_APP_DIR)),
                daemon=True, name="static-gzip").start()

__all__ = ["app"]
