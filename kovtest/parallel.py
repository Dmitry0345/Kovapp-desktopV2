import json
import math
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from .runner import CaseResult


# ===== раскладка разделов по исполнителям =====

def plan(chosen, workers: int):
    units = []
    for item in chosen:
        weight = float(item.get("weight", 2))
        shards = 1 if item.get("exclusive") else max(1, min(workers, math.ceil(weight / 6)))
        for index in range(shards):
            units.append({"key": item["key"], "shard": (index, shards) if shards > 1 else None,
                          "weight": weight / shards, "exclusive": bool(item.get("exclusive"))})
    bins = [{"units": [], "load": 0.0} for _ in range(workers)]
    for unit in [u for u in units if u["exclusive"]]:
        bins[0]["units"].append(unit)
        bins[0]["load"] += unit["weight"]
    for unit in sorted([u for u in units if not u["exclusive"]], key=lambda u: -u["weight"]):
        target = min(bins, key=lambda b: b["load"])
        target["units"].append(unit)
        target["load"] += unit["weight"]
    return [b for b in bins if b["units"]]


def encode(units) -> str:
    return ",".join(u["key"] + ("@%d/%d" % u["shard"] if u["shard"] else "") for u in units)


def decode(text: str, catalog):
    by_key = {item["key"]: item for item in catalog}
    out = []
    for part in [p for p in text.split(",") if p]:
        key, _, shard = part.partition("@")
        item = dict(by_key[key])
        if shard:
            index, count = shard.split("/")
            item["shard"] = (int(index), int(count))
        out.append(item)
    return out


# ===== запуск исполнителей и сбор итогов =====

def launch(bins, run_dir: Path, args, log):
    processes = []
    for index, target in enumerate(bins):
        env = dict(os.environ, WORKER_INDEX=str(index), AUTOTEST_SKIP_LEFTOVERS="1", PYTHONUNBUFFERED="1")
        command = [sys.executable, str(Path(__file__).resolve().parent.parent / "main.py"), "--worker", str(index),
                   "--units", encode(target["units"]), "--run-dir", str(run_dir)]
        if args.mode:
            command += ["--mode", args.mode]
        if args.layers:
            command += ["--layers", args.layers]
        if getattr(args, "target", ""):
            command += ["--target", args.target]
        log.info("исполнитель w%d: %s (оценка %.0f)" % (index + 1, encode(target["units"]), target["load"]))
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                                   text=True, encoding="utf-8", errors="replace")
        threading.Thread(target=relay, args=(process, index), daemon=True).start()
        processes.append(process)
        time.sleep(2)
    return [p.wait() for p in processes]


def relay(process, index):
    for line in process.stdout:
        print("[w%d] %s" % (index + 1, line.rstrip()), flush=True)


def collect(run_dir: Path, log):
    results = []
    for folder in sorted((run_dir / "workers").glob("w*")):
        report = folder / "report.json"
        if not report.exists():
            log.error("исполнитель %s не оставил отчёта — смотрите %s" % (folder.name, folder / "full.log"))
            results.append(CaseResult(section="parallel", title="Исполнитель %s завершился без отчёта" % folder.name,
                                      layer="api", params={}, status="failed",
                                      messages=["смотрите workers/%s/full.log" % folder.name]))
            continue
        data = json.loads(report.read_text(encoding="utf-8"))
        for row in data.get("results") or []:
            results.append(CaseResult(section=row["section"], title=row["title"], layer=row.get("layer", ""),
                                      params=dict(row.get("params") or {}, worker=folder.name),
                                      status=row["status"], duration=row.get("duration_s", 0.0),
                                      messages=row.get("messages") or [], warnings=row.get("warnings") or [],
                                      screenshots=["workers/%s/%s" % (folder.name, s) for s in row.get("screenshots") or []]))
        with open(folder / "full.log", encoding="utf-8", errors="replace") as source:
            log.raw("\n" + "=" * 100 + "\nЖУРНАЛ ИСПОЛНИТЕЛЯ %s\n" % folder.name + "=" * 100 + "\n" + source.read())
    return results
