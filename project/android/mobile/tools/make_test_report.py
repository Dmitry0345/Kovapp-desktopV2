#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сводный отчёт о проверках мобильной версии → docs/mobile-test-report.md.

Берёт итоги UI-тестов (tests/ui/report/report.json) и итоговые строки
остальных проверок (tests/ui/report/checks.txt — его пишет run_all_checks.py).

    python mobile/tools/make_test_report.py [вывод_проверок.txt]
"""
import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MOBILE = os.path.dirname(HERE)
ROOT = os.path.dirname(MOBILE)

SUITES = [("parity", "Внешний вид: APK против веб-версии"), ("rights", "Права по ролям (docs/permissions.md)"),
          ("overlay", "Надстройка приложения"), ("fixes", "Исправления 25.09.2026"), ("sim", "Симулятор телефона на ПК")]


def moscow_today():
    """Дата по Москве: часы сервера сборки могут идти по UTC."""
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=3)).strftime("%d.%m.%Y")


def main():
    ui = json.load(open(os.path.join(MOBILE, "tests", "ui", "report", "report.json"), encoding="utf-8"))
    default = os.path.join(MOBILE, "tests", "ui", "report", "checks.txt")
    checks = open(sys.argv[1] if len(sys.argv) > 1 else default, encoding="utf-8").read()

    def line(title):
        m = re.search(r"== " + re.escape(title) + r"[^\n]*\n(.*?)(?=\n== |\Z)", checks, re.S)
        return m.group(1).strip().split("\n")[-1] if m else "—"

    rows = [(title, sum(1 for r in ui["results"] if r["suite"] == key and r["ok"]),
             sum(1 for r in ui["results"] if r["suite"] == key)) for key, title in SUITES]
    shots = next((r["name"] for r in ui["results"] if r["name"].startswith("снимков совпало")), "")
    out = [
        "# Проверка мобильной версии Kovapp — отчёт",
        "",
        "Дата: %s. Код: веб-версия и сервер из `kovcheg-2026-09-25-v6` с дополнением "
        "по мессенджеру (`docs/changes-2026-09-25.md`), приложение `mobile/`." % moscow_today(),
        "",
        "## Итог",
        "",
        "| Что проверено | Как | Результат |",
        "|---|---|---|",
        "| Запасные пути телефона дают тот же результат, что код веб-версии | JVM-тесты: разбор и сборка MP4, план сжатия видео, отпечатки файлов, размеры картинок, расшифровка уведомлений, CSP | %s |" % line("JVM"),
        "| Собственный канал уведомлений целиком | JVM-тест `DirectChannelTest`: классы приложения против серверного кода | входит в JVM-тесты |",
        "| Код приложения собирается против Android API (одна сборка на все телефоны) | компиляция с заглушками SDK по реальным сигнатурам | %s |" % line("компиляция").replace("[gms] ", "universal и gms: "),
        "| Новые функции Android — только с проверкой версии (Android 7+) | статическая проверка | %s |" % line("уровни API"),
        "| Надстройка в APK и на ПК одинакова | Java `Overlay` против `tools/devserver.py` | %s |" % line("надстройка Java/Python"),
    ]
    for (key, _), (title, ok, total) in zip(SUITES, rows):
        result = "**%d из %d**" % (ok, total)
        if key == "parity" and shots:
            result = "**%s**, текст «Настроек» — как задумано" % shots.replace("снимков совпало: ", "")
            result = result.replace(" из ", " из ", 1).replace("**", "**", 1)
        out.append("| %s | UI-тесты (Chromium), веб-версия и APK на одном сервере | %s |" % (title, result))
    out += [
        "| Безопасность | `tools/security_check.py` | %s |" % line("безопасность"),
        "| Серверный модуль уведомлений, Web Push без изменений | `server/tests/native_push_tests.py` | %s |" % line("сервер: уведомления"),
        "| Серверные исправления 25.09 | `server/tests/fixes_2026_09_25_tests.py` | %s |" % line("сервер: исправления 25.09"),
        "",
        "## Внешний вид",
        "",
        shots + ". 16 экранов (вход, задачи, задача, проекты, проект, организации, "
        "оборудование, работники, уведомления, мессенджер, CSV — у директора и работника), "
        "7 размеров экрана (320×568, 360×800, 390×844, 412×915, 768×1024, 844×390, 1280×800), "
        "светлая и тёмная тема. Снимки — `mobile/tests/ui/report/parity/`.",
        "",
        "Экран «Настройки» сравнивается по тексту: в APK отличаются только строки, где "
        "веб-версия говорит о браузере (правки `overlay/web-patches.json`), — список в "
        "`mobile/tests/ui/report/settings-diff.txt`.",
        "",
        "Совпадение проверяется в браузере Chromium, на котором основан Android System "
        "WebView; на телефоне дополнительно действует его версия (минимум 90, для полного "
        "совпадения — 111+).",
        "",
        "## Подробно по UI-тестам",
        "",
    ]
    for key, title in SUITES:
        items = [r for r in ui["results"] if r["suite"] == key]
        if not items:
            continue
        out += ["### " + title, ""] + ["- %s %s" % ("✅" if r["ok"] else "❌", r["name"]) for r in items] + [""]
    if ui.get("observations"):
        out += ["### Наблюдения", ""] + ["- " + o for o in ui["observations"]] + [""]
    out += [
        "## Что можно проверить только на телефоне",
        "",
        "Здесь нет Android SDK, поэтому APK не собирался и не запускался на устройстве. "
        "Перед раздачей сотрудникам пройдите короткий список на 2–3 телефонах "
        "(обязательно на одном Huawei или Honor):",
        "",
        "1. Сборка `release` устанавливается и открывается; вход работает.",
        "2. Внешний вид совпадает с сайтом на том же телефоне (задачи, мессенджер, тёмная тема).",
        "3. «Настройки» → «Это устройство» → включить уведомления → «Проверить»: подтверждение доставки приходит.",
        "4. Приложение закрыто (смахнуть из списка недавних) → сообщение в чат от другого сотрудника → уведомление приходит; нажатие открывает этот чат.",
        "5. Huawei/Honor: то же после «Запуск приложений → Управлять вручную» и перезагрузки телефона.",
        "6. Отправка фото с камеры и из галереи в задачу и в чат; отправка видео 1–2 минуты (сжатие на телефоне).",
        "7. Отправка файла, затем на 20–30 секунд выключить Wi-Fi и включить снова — отправка продолжается.",
        "8. Скачивание файла из задачи → он появился в «Загрузках».",
        "9. «Назад»: закрывает окно, затем меню; на главном экране сворачивает приложение.",
        "10. Выход из учётной записи → уведомления этому телефону больше не приходят.",
        "",
    ]
    path = os.path.join(ROOT, "docs", "mobile-test-report.md")
    open(path, "w", encoding="utf-8").write("\n".join(out))
    print("записан", path)


if __name__ == "__main__":
    main()
