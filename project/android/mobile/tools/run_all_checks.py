#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Все проверки мобильной версии одной командой и сводный отчёт.

    python mobile/tools/run_all_checks.py          # без UI-тестов (пара минут)
    python mobile/tools/run_all_checks.py --ui     # вместе с UI-тестами (около часа)

Вывод проверок — mobile/tests/ui/report/checks.txt, отчёт —
docs/mobile-test-report.md.
"""
import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MOBILE = os.path.dirname(HERE)
ROOT = os.path.dirname(MOBILE)
PY = sys.executable
OUT = os.path.join(MOBILE, "tests", "ui", "report", "checks.txt")


def run(title, cmd, cwd=ROOT, timeout=900):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, timeout=timeout)
        # служебные сообщения (javac, журналы) — сначала, итог программы — последней строкой
        text = (r.stderr + b"\n" + r.stdout).decode("utf-8", "replace")
        code = r.returncode
    except subprocess.TimeoutExpired:
        text, code = "не уложилось в %d с" % timeout, 1
    lines = [l for l in text.strip().split("\n") if l.strip()]
    last = lines[-1] if lines else ""
    print("%-28s %s" % (title, ("ОК  " if code == 0 else "ОШИБКА  ") + last[:100]))
    return "== %s\n%s\n" % (title, "\n".join(lines[-40:]))


def js_syntax():
    files = glob.glob(os.path.join(ROOT, "web_app", "js", "**", "*.js"), recursive=True) + \
        glob.glob(os.path.join(MOBILE, "overlay", "native", "*.js")) + glob.glob(os.path.join(MOBILE, "tools", "devbridge", "*.js")) + \
        glob.glob(os.path.join(MOBILE, "tools", "simulator", "*.js"))
    script = "const fs=require('fs');let bad=[];for(const f of process.argv.slice(1)){try{new Function(fs.readFileSync(f,'utf8'))}catch(e){bad.push(f+': '+e.message)}}" \
             "console.log(bad.length?bad.join('\\n'):'все файлы разобраны ('+(process.argv.length-1)+')');process.exit(bad.length?1:0)"
    return ["node", "-e", script] + files


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    py_files = [p for p in glob.glob(os.path.join(ROOT, "server", "**", "*.py"), recursive=True) if "__pycache__" not in p]
    parts = [
        run("JVM", [PY, os.path.join(MOBILE, "tools", "offline-check", "run_jvm_tests.py")]),
        run("компиляция", [PY, os.path.join(MOBILE, "tools", "offline-check", "compile_android.py")]),
        run("XML-ресурсы", [PY, os.path.join(MOBILE, "tools", "offline-check", "check_resources.py")]),
        run("уровни API", [PY, os.path.join(MOBILE, "tools", "offline-check", "api_levels.py")]),
        run("надстройка Java/Python", [PY, os.path.join(MOBILE, "tools", "offline-check", "overlay_parity.py")]),
        run("безопасность", [PY, os.path.join(MOBILE, "tools", "security_check.py")]),
        run("сервер: уведомления", [PY, "-m", "server.tests.native_push_tests"]),
        run("сервер: исправления 25.09", [PY, "-m", "server.tests.fixes_2026_09_25_tests"]),
        run("сервер: отправка частями", [PY, "-m", "server.tests.chunked_sessions_tests"]),
        run("сервер: быстрая проверка копии", [PY, "-m", "server.tests.dedup_probe_tests"]),
        run("сервер: длинные видео", [PY, "-m", "server.tests.video_long_tests"]),
        run("сервер: ночное пережатие видео", [PY, "-m", "server.tests.night_video_tests"]),
        run("видео: «уже подходит» (JS)", ["node", "mobile/tests/node/ready_long_test.js"]),
        run("py_compile сервера", [PY, "-m", "py_compile"] + py_files),
        run("синтаксис JS", js_syntax()),
    ]
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    if "--ui" in sys.argv:
        subprocess.run([PY, os.path.join(MOBILE, "tests", "ui", "run_ui_tests.py")], cwd=ROOT)
    if os.path.isfile(os.path.join(MOBILE, "tests", "ui", "report", "report.json")):
        subprocess.run([PY, os.path.join(HERE, "make_test_report.py"), OUT], cwd=ROOT)
    else:
        print("UI-тестов ещё не было — отчёт соберётся после запуска с --ui")


if __name__ == "__main__":
    main()
