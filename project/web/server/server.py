# -*- coding: utf-8 -*-
"""Точка входа сервера."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def get_app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent

    return Path(__file__).resolve().parent.parent


# Запуск «python server.py» прямо из папки пакета: каталог скрипта стоит первым
# в путях поиска, и «server» находится как файл server.py, а не как пакет —
# отсюда «No module named 'server.server_app'; 'server' is not a package».
# Ставим каталог на уровень выше первым: тогда работают оба способа запуска —
# и python server.py, и python -m server.server.
if not getattr(sys, "frozen", False):
    _PACKAGE_ROOT = str(Path(__file__).resolve().parent.parent)
    if not sys.path or sys.path[0] != _PACKAGE_ROOT:
        sys.path.insert(0, _PACKAGE_ROOT)


# =====================================================
# ENV
# =====================================================

APP_ROOT = get_app_root()
ENV_FILE = APP_ROOT / ".env"

load_dotenv(ENV_FILE)


# =====================================================
# Логи настраиваем до импорта приложения, чтобы поймать
# в файл и ошибки старта
# =====================================================

from server.server_app.logging_setup import setup_logging  # noqa: E402

setup_logging(APP_ROOT)


# =====================================================
# Только после загрузки .env импортируем приложение
# =====================================================

ROLE = (os.getenv("KOVAPP_ROLE") or "all").strip().lower()
if ROLE != "split" and os.getenv("KOVAPP_WORKER") != "1" and os.getenv("KOVAPP_SUPERVISOR") != "1":
    # рабочим процессам обработки файлов приложение целиком не нужно
    from server.server_app.app import app  # noqa: E402,F401


def _serve(role: str, host: str, port: int, workers: int = 1) -> None:
    """Запуск одного процесса сервера; для процессов чтения — сразу нескольких на одном порту."""
    import uvicorn
    options = dict(host=host, port=port, proxy_headers=True, forwarded_allow_ips="127.0.0.1",
                   timeout_keep_alive=75, timeout_graceful_shutdown=10, access_log=False, log_config=None)
    if workers > 1:
        # uvicorn раз в полсекунды проверяет каждый процесс; не ответил за 5 с — убивает и запускает заново
        # (с ним рвётся загрузка, которая в нём шла). При всплеске нагрузки 5 с мало — ждём WORKER_HEALTHCHECK_S
        import inspect
        if "timeout_worker_healthcheck" in inspect.signature(uvicorn.Config.__init__).parameters:
            options["timeout_worker_healthcheck"] = float(os.getenv("WORKER_HEALTHCHECK_S") or 30)
        uvicorn.run("server.server_app.app:app", workers=workers, **options)
    else:
        uvicorn.run(app, **options)


def _split(host: str, port: int) -> None:
    """Процессы по ролям: API (запись, загрузки) на SERVER_PORT, переписка на RT_PORT,
    процессы чтения (списки, файлы, страницы) на READ_PORT — их столько, сколько позволяет машина.
    nginx направляет /ws/ переписке, обычные чтения — процессам чтения, остальное — API."""
    import multiprocessing as mp
    import signal
    import time as _time
    from server.server_app.lanes import PLAN
    ctx = mp.get_context("spawn")
    rt_port = int(os.getenv("RT_PORT") or port + 1)
    read_port = int(os.getenv("READ_PORT") or port + 2)
    upload_port = int(os.getenv("UPLOAD_PORT") or port + 3)
    readers = max(1, int(PLAN.get("readers") or 1))
    uploaders = max(1, int(PLAN.get("uploaders") or 1))
    plan = [("realtime", rt_port, 1), ("api", port, 1), ("reader", read_port, readers),
            ("uploader", upload_port, uploaders)]

    def spawn(role, role_port, workers):
        os.environ["KOVAPP_ROLE"] = role
        if workers > 1:
            # управляющему процессу uvicorn приложение не нужно — его загрузят сами рабочие процессы
            os.environ["KOVAPP_SUPERVISOR"] = "1"
        child = ctx.Process(target=_serve, args=(role, host, role_port, workers), name="kovapp-" + role)
        child.start()
        os.environ.pop("KOVAPP_SUPERVISOR", None)
        os.environ["KOVAPP_ROLE"] = "split"
        return child

    children = {spec: spawn(*spec) for spec in plan}
    print(f"Kovapp: API на {host}:{port}, переписка на {host}:{rt_port}, чтение на {host}:{read_port} "
          f"(процессов {readers}), приём загрузок на {host}:{upload_port} (процессов {uploaders})", flush=True)

    def stop(*_):
        for child in children.values():
            if child.is_alive():
                child.terminate()
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    # упавший процесс перезапускается сам; если падения идут подряд (5 за 10 минут) —
    # выходим целиком, и службу перезапустит systemd
    restarts = []
    while True:
        for spec, child in list(children.items()):
            if child.is_alive():
                continue
            now = _time.time()
            restarts = [t for t in restarts if now - t < 600] + [now]
            print(f"Kovapp: процесс {spec[0]} завершился (код {child.exitcode}) — перезапуск", flush=True)
            if len(restarts) > 5:
                print("Kovapp: процессы падают подряд — сервер останавливается целиком", flush=True)
                for other in children.values():
                    if other.is_alive():
                        other.terminate()
                raise SystemExit(child.exitcode or 1)
            children[spec] = spawn(*spec)
        _time.sleep(1)


if __name__ == "__main__":
    import uvicorn

    bind_host = os.getenv(
        "SERVER_BIND_HOST",
        "0.0.0.0"
    )

    port = int(
        os.getenv(
            "SERVER_PORT",
            "8000"
        )
    )

    # Второй экземпляр на том же порту не запускаем: два сервера делят базу,
    # фоновые задачи и архивацию журналов БД (ошибка binlog «same server_id»)
    import tempfile
    _lock_file = open(os.path.join(tempfile.gettempdir(), f"kovcheg-server-{port}.lock"), "a+")
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(_lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(f"Сервер на порту {port} уже запущен или ещё завершается. Закройте прежнее окно "
              "сервера (Ctrl+C, при зависании — второй раз) и запустите снова.")
        raise SystemExit(1)

    # all — один процесс, как раньше; split — API и переписка в двух процессах (см. docs/performance)
    if ROLE == "split":
        _split(bind_host, port)
    else:
        _serve(ROLE, bind_host, port)