# -*- coding: utf-8 -*-
"""ПК-версия Kovapp: все проверки подряд — это и есть «run.cmd pc».

    python kovtest_pc/run_pc.py [--project project/pc] [--parts unit,sections,actions,messenger]
                        [--roles director,admin,head,worker,montage,stock,accountant] [--dark-roles director]

  unit       — без окон: стили мессенджера, функции, уведомления, фото, отпечатки,
               gzip, видео на образцах через FFmpeg из комплекта, отправка на тестовый сервер
  sections   — все разделы под каждой ролью (светлая тема; тёмная — для ролей из --dark-roles)
  actions    — действия: смена темы и размера без перезапуска, этап и журнал задачи, шаблоны,
               подсказки, флажки, загрузки, медиаконтент, переходы без отдельных окон
  messenger  — окна и работа мессенджера, обе темы
  compression — исходники проверки качества сжатия (logs/compression-inputs) сжимает код программы;
               результат — в logs/compression-results/ПК (часть run.cmd compression, отдельно не нужна)

Сервер — тестовый, внутри прогона (kovtest_pc/mock), Docker не нужен. Приложение запускается из папки
ПК-версии теми же библиотеками, что и при сборке. Итог — в logs/<дата>-pc: summary.txt,
results.json, full.log и index.html со снимками всех экранов.
"""

import argparse
import html
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE = os.path.dirname(HERE)


def decode_output(raw: bytes) -> str:
    """Python пишет UTF-8, а сообщения Windows и Qt — в кодировке системы (cp1251 или cp866):
    строку, которая не UTF-8, читаем той из двух, где в ней больше русских букв."""
    lines = []
    for line in raw.split(b"\n"):
        try:
            lines.append(line.decode("utf-8"))
        except UnicodeDecodeError:
            variants = [line.decode(enc, "replace") for enc in ("cp1251", "cp866")]
            lines.append(max(variants, key=lambda t: sum(1 for ch in t if "А" <= ch <= "я" or ch in "Ёё")))
    return "\n".join(lines)


def project_dir(arg):
    cands = [arg] if arg else [os.environ.get("PROJECT_PC_DIR", ""), os.path.join(MODULE, "project", "pc")]
    for c in cands:
        if not c:
            continue
        p = c if os.path.isabs(c) else os.path.join(MODULE, c)
        for q in (p, os.path.join(p, "pc")):           # архив распакован «с лишней папкой»
            if os.path.isfile(os.path.join(q, "main.py")) and os.path.isdir(os.path.join(q, "kov")):
                return os.path.abspath(q)
    return None


def parse():
    ap = argparse.ArgumentParser(description="ПК-версия Kovapp: все проверки")
    ap.add_argument("--project", default="", help="папка ПК-версии (по умолчанию PROJECT_PC_DIR или project/pc)")
    ap.add_argument("--parts", default="unit,sections,actions,messenger")
    ap.add_argument("--roles", default="director,admin,head,worker,montage,stock,accountant")
    ap.add_argument("--dark-roles", default="director")
    ap.add_argument("--themes", default="light,dark", help="темы для мессенджера")
    ap.add_argument("--out", default="")
    return ap.parse_args()


def keep_runs():
    """Сколько прогонов хранить — KEEP_RUNS (как у прогонов в Docker), по умолчанию 30."""
    value = os.environ.get("KEEP_RUNS", "")
    if not value:
        try:
            with open(os.path.join(MODULE, ".env"), encoding="utf-8") as f:
                for line in f:
                    if line.startswith("KEEP_RUNS="):
                        value = line.split("=", 1)[1].strip()
        except OSError:
            pass
    try:
        return max(1, int(value))
    except ValueError:
        return 30


def prune(logs, current):
    runs = sorted(d for d in os.listdir(logs) if d.endswith("-pc") and os.path.isdir(os.path.join(logs, d)))
    for d in runs[:-keep_runs()]:
        if os.path.join(logs, d) != current:
            shutil.rmtree(os.path.join(logs, d), ignore_errors=True)


def percent(a, b):
    return "%.1f%%" % (100.0 * a / b) if b else "—"


def main():
    a = parse()
    proj = project_dir(a.project)
    if proj is None:
        print("Не найдена ПК-версия. Распакуйте pc.zip в папку project — получится project\\pc\\main.py "
              "(или укажите PROJECT_PC_DIR в .env).")
        return 2
    parts = [x.strip() for x in a.parts.split(",") if x.strip()]
    if "all" in parts:
        parts = ["unit", "sections", "actions", "messenger"]
    unknown = [x for x in parts if x not in ("unit", "sections", "actions", "messenger", "compression")]
    if unknown or not parts:
        print("Неизвестные части: %s. Есть: unit (без окон), sections (разделы), actions (действия), "
              "messenger (мессенджер), compression (сжатие исходников проверки качества)." % ", ".join(unknown or ["—"]))
        return 2
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    logs = os.path.join(MODULE, "logs")
    out = os.path.abspath(a.out or os.path.join(logs, stamp + "-pc"))
    os.makedirs(out, exist_ok=True)
    if os.path.dirname(out) == os.path.abspath(logs):        # run.cmd показывает итог по этой метке
        try:
            with open(os.path.join(logs, "last-run.txt"), "w", encoding="utf-8") as f:
                f.write(os.path.basename(out))
        except OSError:
            pass
        prune(os.path.abspath(logs), out)
    full = open(os.path.join(out, "full.log"), "a", encoding="utf-8")
    started = time.time()
    roles = [x.strip() for x in a.roles.split(",") if x.strip()]
    dark = [x.strip() for x in a.dark_roles.split(",") if x.strip()]
    themes = [x.strip() for x in a.themes.split(",") if x.strip()]
    runs = []
    if "unit" in parts:
        runs.append(("unit", [sys.executable, os.path.join(HERE, "unit.py"), "--project", proj], "без окон"))
    if "compression" in parts:
        runs.append(("compression", [sys.executable, os.path.join(HERE, "compress_pc.py"), "--project", proj],
                     "качество сжатия: исходники сжимает ПК-версия"))
    if "sections" in parts:
        for role in roles:
            for theme in (["light", "dark"] if role in dark else ["light"]):
                runs.append(("sections_%s_%s" % (role, theme),
                             [sys.executable, os.path.join(HERE, "harness.py"), "--project", proj, "--plan", "sections",
                              "--role", role, "--theme", theme], "разделы · %s · %s тема" % (role, "светлая" if theme == "light" else "тёмная")))
    if "actions" in parts:
        runs.append(("actions_director_light", [sys.executable, os.path.join(HERE, "harness.py"), "--project", proj,
                                                "--plan", "actions", "--role", "director", "--theme", "light"],
                     "действия · director"))
    if "messenger" in parts:
        for theme in themes:
            runs.append(("messenger_director_%s" % theme,
                         [sys.executable, os.path.join(HERE, "harness.py"), "--project", proj, "--plan", "messenger",
                          "--role", "director", "--theme", theme], "мессенджер · %s тема" % ("светлая" if theme == "light" else "тёмная")))
    version = ""
    try:
        version = open(os.path.join(proj, "VERSION"), encoding="utf-8").read().strip()
    except OSError:
        pass
    print("ПК-версия: %s (%s)" % (version or "версия не указана", proj), flush=True)
    print("Журналы прогона: %s" % out, flush=True)
    results = []
    for i, (key, cmd, title) in enumerate(runs, 1):
        print("[%d/%d] %s …" % (i, len(runs), title), flush=True)
        folder = os.path.join(out, key)
        t0 = time.time()
        code = None
        try:
            # вывод Python — в UTF-8; иначе в Windows он идёт в кодировке системы и в full.log нечитаем
            proc = subprocess.run(cmd + ["--out", folder], cwd=MODULE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  timeout=900, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
            code = proc.returncode
            text = decode_output(proc.stdout)
        except subprocess.TimeoutExpired as e:
            text = decode_output(e.stdout or b"") + "\nПРЕВЫШЕНО ВРЕМЯ (15 мин)"
        full.write("\n===== %s (%s) — код %s, %.0f с =====\n%s\n" % (title, key, code, time.time() - t0, text))
        try:
            full.write(open(os.path.join(folder, "run.log"), encoding="utf-8").read())
        except OSError:
            pass
        full.flush()
        try:
            data = json.load(open(os.path.join(folder, "results.json"), encoding="utf-8"))
            checks = data.get("checks") or []
        except (OSError, ValueError):
            checks = [{"name": "прогон не завершился", "status": "failed",
                       "detail": "код %s; подробно — full.log" % code}]
        bad = sum(1 for c in checks if c["status"] == "failed")
        print("      проверок %d, ошибок %d, %.0f с" % (len(checks), bad, time.time() - t0), flush=True)
        results.append((key, title, checks))
    summary = write_summary(out, proj, version, results, started)
    write_gallery(out, results)
    total = sum(len(c) for _k, _t, c in results)
    failed = sum(1 for _k, _t, c in results for x in c if x["status"] == "failed")
    passed = sum(1 for _k, _t, c in results for x in c if x["status"] == "passed")
    print("\nИтог: %s выполнено успешно, ошибок %d. Краткий отчёт: %s" % (percent(passed, passed + failed), failed, summary))
    print("Снимки экранов: %s" % os.path.join(out, "index.html"))
    return 0 if failed == 0 and total else 1


def write_summary(out, proj, version, results, started):
    allc = [(t, c) for _k, t, cs in results for c in cs]
    passed = sum(1 for _t, c in allc if c["status"] == "passed")
    failed = sum(1 for _t, c in allc if c["status"] == "failed")
    warned = sum(1 for _t, c in allc if c["status"] == "warned")
    dur = time.time() - started
    module_ver = ""
    try:
        module_ver = open(os.path.join(MODULE, "VERSION"), encoding="utf-8").read().strip()
    except OSError:
        pass
    lines = ["Автотесты Kovapp — краткий итог", "=" * 72,
             "Дата:            %s" % datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
             "Версия:          ПК-версия (настольное приложение)",
             "Режим:           тестовый сервер внутри прогона (kovtest_pc/mock), без Docker",
             "Длительность:    %d мин %02d с" % (dur // 60, dur % 60),
             "Модуль тестов: версия %s" % (module_ver or "не указана"),
             "ПК-версия: версия %s (%s)" % (version or "не указана", proj),
             "Python: %s" % sys.version.split()[0]]
    try:
        import PySide6
        from PySide6.QtCore import qVersion
        lines.append("PySide6 %s, Qt %s" % (PySide6.__version__, qVersion()))
    except Exception:
        pass
    lines += ["", "ВЫПОЛНЕНИЕ: %s  (пройдено %d из %d выполненных, провалено %d, предупреждений %d)" % (
        percent(passed, passed + failed), passed, passed + failed, failed, warned), "",
        "%-40s %7s %7s %7s %9s" % ("Прогон", "всего", "успех", "ошибки", "успех %"), "-" * 72]
    for _k, title, cs in results:
        ok = sum(1 for c in cs if c["status"] == "passed")
        bad = sum(1 for c in cs if c["status"] == "failed")
        lines.append("%-40s %7d %7d %7d %9s" % (title[:40], len(cs), ok, bad, percent(ok, ok + bad)))
    lines += ["", "ОШИБКИ (%d)" % failed, "-" * 72]
    n = 0
    for title, c in allc:
        if c["status"] == "failed":
            n += 1
            lines.append("%d. [%s] %s" % (n, title, c["name"]))
            if c.get("detail"):
                lines.append("     — " + c["detail"])
    if not failed:
        lines.append("Ошибок нет.")
    lines += ["", "ПРЕДУПРЕЖДЕНИЯ (%d)" % warned, "-" * 72]
    for title, c in allc:
        if c["status"] == "warned":
            lines.append("[%s] %s — %s" % (title, c["name"], c.get("detail", "")))
    path = os.path.join(out, "summary.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(os.path.join(out, "results.json"), "w", encoding="utf-8") as f:
        json.dump([{"run": k, "title": t, "checks": cs} for k, t, cs in results], f, ensure_ascii=False, indent=1)
    return path


def write_gallery(out, results):
    """index.html: снимки всех экранов по прогонам и ошибки рядом — для просмотра глазами."""
    parts = ["<!doctype html><html lang='ru'><head><meta charset='utf-8'><title>Kovapp ПК — снимки</title><style>",
             "body{font-family:Segoe UI,Arial,sans-serif;margin:16px;background:#F1F4F6;color:#132A32}",
             "h2{margin:24px 0 8px}.g{display:flex;flex-wrap:wrap;gap:10px}",
             "figure{margin:0;background:#fff;border:1px solid #D5DEE2;border-radius:8px;padding:6px;width:330px}",
             "figure img{width:100%;border-radius:4px}figcaption{font-size:12px;margin-top:4px;word-break:break-all}",
             ".bad{color:#C62828}.ok{color:#2E7D32}</style></head><body><h1>Kovapp ПК — снимки экранов</h1>"]
    for key, title, cs in results:
        bad = [c for c in cs if c["status"] == "failed"]
        parts.append("<h2>%s <small class='%s'>%d проверок, ошибок %d</small></h2>" % (
            html.escape(title), "bad" if bad else "ok", len(cs), len(bad)))
        for c in bad:
            parts.append("<div class='bad'>✗ %s — %s</div>" % (html.escape(c["name"]), html.escape(c.get("detail", ""))))
        shots = os.path.join(out, key, "shots")
        if os.path.isdir(shots):
            parts.append("<div class='g'>")
            for name in sorted(os.listdir(shots)):
                rel = "%s/shots/%s" % (key, name)
                parts.append("<figure><a href='%s'><img src='%s' loading='lazy'></a><figcaption>%s</figcaption></figure>" % (
                    html.escape(rel), html.escape(rel), html.escape(name)))
            parts.append("</div>")
    parts.append("</body></html>")
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write("\n".join(parts))


if __name__ == "__main__":
    sys.exit(main())
