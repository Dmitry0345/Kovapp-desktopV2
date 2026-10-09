import json
import shutil
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path


# ===== журнал прогона =====

class RunLog:
    def __init__(self, root: Path, mode: str, label: str = "", keep: int = 30, folder: Path = None):
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        name = stamp + "_" + mode + (("_" + label) if label else "")
        self.dir = folder or (root / name)
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "screenshots").mkdir(exist_ok=True)
        self.path = self.dir / "full.log"
        self._handle = open(self.path, "a", encoding="utf-8", buffering=1)
        self._lock = threading.Lock()
        self.started = time.time()
        self.prefix = ""
        if folder is None:
            self._rotate(root, keep)

    def _rotate(self, root: Path, keep: int) -> None:
        runs = sorted([p for p in root.iterdir() if p.is_dir() and p != self.dir], key=lambda p: p.name)
        for old in runs[:max(0, len(runs) - max(1, keep) + 1)]:
            shutil.rmtree(old, ignore_errors=True)

    def write(self, level: str, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        lines = str(text).splitlines() or [""]
        with self._lock:
            for i, line in enumerate(lines):
                head = "%s %-5s %s" % (stamp, level, self.prefix) if i == 0 else " " * 20
                self._handle.write(head + line + "\n")

    def info(self, text: str) -> None:
        self.write("INFO", text)

    def warn(self, text: str) -> None:
        self.write("WARN", text)

    def error(self, text: str) -> None:
        self.write("ERROR", text)

    def debug(self, text: str) -> None:
        self.write("DEBUG", text)

    def http(self, method: str, url: str, status, ms: float, who: str = "", note: str = "") -> None:
        self.write("HTTP", "%s %s %s → %s (%d мс)%s" % (who or "-", method, url, status, ms, (" " + note) if note else ""))

    def raw(self, text: str) -> None:
        with self._lock:
            self._handle.write(text if text.endswith("\n") else text + "\n")

    def banner(self, text: str) -> None:
        with self._lock:
            self._handle.write("\n" + "=" * 100 + "\n" + text + "\n" + "=" * 100 + "\n")

    def exception(self, text: str) -> None:
        self.write("ERROR", text + "\n" + traceback.format_exc())

    def close(self) -> None:
        with self._lock:
            self._handle.close()


# ===== итоговые файлы =====

def percent(part: int, whole: int) -> str:
    return "%.1f%%" % (100.0 * part / whole) if whole else "—"


def write_summary(log: RunLog, settings, results, extra_lines=()) -> Path:
    total = len(results)
    passed = sum(1 for r in results if r.status == "passed")
    failed = sum(1 for r in results if r.status == "failed")
    skipped = sum(1 for r in results if r.status == "skipped")
    executed = passed + failed
    duration = time.time() - log.started
    lines = []
    lines.append("Автотесты Kovapp — краткий итог")
    lines.append("=" * 72)
    lines.append("Дата:            %s" % datetime.now().strftime("%d.%m.%Y %H:%M:%S"))
    lines.append("Версия:          %s" % {
        "apk": "мобильная — интерфейс APK на ПК (движок Chromium, как Android WebView)",
        "device": "мобильная — настоящее приложение на Android (эмулятор или телефон)",
    }.get(getattr(settings, "target", "web"), "сайт"))
    lines.append("Режим:           %s" % ("полный (сервер в Docker)" if settings.mode == "full" else "внешний сервер"))
    lines.append("Сервер:          %s" % settings.base_url)
    lines.append("Разделы:         %s" % ", ".join(settings.sections))
    lines.append("Слои:            %s" % ", ".join(settings.layers))
    lines.append("Браузеры:        %s; темы: %s; экраны: %s" % (
        ", ".join(settings.browsers), ", ".join(settings.themes), ", ".join(settings.devices)))
    lines.append("Длительность:    %d мин %02d с" % (duration // 60, duration % 60))
    lines.extend(extra_lines)
    lines.append("")
    lines.append("ВЫПОЛНЕНИЕ: %s  (пройдено %d из %d выполненных, провалено %d, пропущено %d, всего %d)" % (
        percent(passed, executed), passed, executed, failed, skipped, total))
    lines.append("")
    lines.append("%-26s %8s %8s %8s %8s %9s" % ("Раздел", "всего", "успех", "ошибки", "пропуск", "успех %"))
    lines.append("-" * 72)
    sections = []
    for r in results:
        if r.section not in sections:
            sections.append(r.section)
    for name in sections:
        rows = [r for r in results if r.section == name]
        ok = sum(1 for r in rows if r.status == "passed")
        bad = sum(1 for r in rows if r.status == "failed")
        skip = sum(1 for r in rows if r.status == "skipped")
        lines.append("%-26s %8d %8d %8d %8d %9s" % (name[:26], len(rows), ok, bad, skip, percent(ok, ok + bad)))
    lines.append("")
    failures = [r for r in results if r.status == "failed"]
    lines.append("ОШИБКИ (%d)" % len(failures))
    lines.append("-" * 72)
    if not failures:
        lines.append("Ошибок нет.")
    for i, r in enumerate(failures, 1):
        lines.append("%d. [%s] %s" % (i, r.section, r.full_title))
        for message in r.messages[:12]:
            lines.append("     — " + message)
        if len(r.messages) > 12:
            lines.append("     … ещё %d, подробно в full.log" % (len(r.messages) - 12))
        for shot in r.screenshots[:3]:
            lines.append("     снимок: " + shot)
    warnings = [(r, w) for r in results for w in r.warnings]
    lines.append("")
    lines.append("ПРЕДУПРЕЖДЕНИЯ (%d)" % len(warnings))
    lines.append("-" * 72)
    for r, w in warnings[:300]:
        lines.append("[%s] %s — %s" % (r.section, r.full_title, w))
    if len(warnings) > 300:
        lines.append("… ещё %d, подробно в full.log" % (len(warnings) - 300))
    skipped_rows = [r for r in results if r.status == "skipped"]
    if skipped_rows:
        lines.append("")
        lines.append("ПРОПУЩЕНО (%d)" % len(skipped_rows))
        lines.append("-" * 72)
        for r in skipped_rows:
            lines.append("[%s] %s — %s" % (r.section, r.full_title, "; ".join(r.messages[:1]) or "без причины"))
    path = log.dir / "summary.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = {
        "mode": settings.mode, "target": getattr(settings, "target", "web"), "base_url": settings.base_url, "sections": settings.sections,
        "layers": settings.layers, "passed": passed, "failed": failed, "skipped": skipped,
        "percent": round(100.0 * passed / executed, 2) if executed else None,
        "duration_s": round(duration, 1),
        "results": [r.as_dict() for r in results],
    }
    (log.dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return path
