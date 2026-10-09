#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Симулятор телефона Kovapp на ПК — одной командой.

    python mobile/tools/simulator.py                              # с тестовым сервером
    python mobile/tools/simulator.py --server https://kovcheg.example.ru   # с рабочим сервером

Откроется браузер со «телефоном»: приложение с той же надстройкой, что в
APK, шторка уведомлений, кнопки «Назад», «Домой», «Недавние», сеть,
разрешения, загрузки. Нужен только Python 3.8+ (без сторонних пакетов).
"""
import argparse
import os
import sys
import threading
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
MOBILE = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(MOBILE, "tests", "mock"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", default="", help="адрес сервера; пусто — встроенный тестовый сервер")
    ap.add_argument("--port", type=int, default=8790, help="порт симулятора (по умолчанию 8790)")
    ap.add_argument("--no-browser", action="store_true", help="не открывать браузер")
    ap.add_argument("--insecure-upstream", action="store_true", help="сервер с самоподписанным сертификатом")
    a = ap.parse_args()

    import devserver
    args = ["--port", str(a.port), "--sim"]
    if a.server:
        args += ["--upstream", a.server.rstrip("/")]
        if a.insecure_upstream:
            args.append("--insecure-upstream")
        users = "учётные записи рабочего сервера"
    else:
        import mock_server
        try:
            mock = mock_server.make_server("127.0.0.1", 8000)      # постоянный адрес сайта для второго сотрудника
        except OSError:
            mock = mock_server.make_server("127.0.0.1", 0)
        threading.Thread(target=mock.serve_forever, daemon=True).start()
        args += ["--upstream", "http://127.0.0.1:%d" % mock.server_address[1], "--upstream-is-mock"]
        users = "логин — роль (director, worker, montage, stock, …), пароль test"
    try:
        dev, _ = devserver.make_server(args)
    except OSError:
        print("Порт %d занят — запускаю на свободном" % a.port)
        args[1] = "0"
        dev, _ = devserver.make_server(args)
    url = "http://127.0.0.1:%d/__sim/" % dev.server_address[1]
    print("\nСимулятор Kovapp: %s" % url)
    print("Сервер: %s" % (a.server or "встроенный тестовый (переписка и файлы хранятся, пока это окно открыто)"))
    print("Вход: %s" % users)
    if not a.server:
        print("Второй сотрудник — сайт на том же сервере: http://127.0.0.1:%d/app/ (переписка с «телефоном»)" % mock.server_address[1])
    print("Остановить: Ctrl+C\n")
    if not a.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        dev.serve_forever()
    except KeyboardInterrupt:
        print("Остановлено")


if __name__ == "__main__":
    main()
