import importlib
import itertools
import re
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional


# ===== регистрация тестов =====

class SkipCase(Exception):
    pass


class CaseFailure(AssertionError):
    pass


def skip(reason: str):
    raise SkipCase(reason)


@dataclass
class CaseSpec:
    func: Callable
    title: str
    layer: str
    matrix: Dict[str, object]
    order: int
    module: str


REGISTRY: Dict[str, List[CaseSpec]] = {}


def case(title: str, layer: str = "api", matrix: Optional[dict] = None, order: int = 100):
    def wrap(func):
        module = func.__module__.split(".")[-1]
        REGISTRY.setdefault(module, []).append(CaseSpec(func, title, layer, matrix or {}, order, module))
        return func
    return wrap


# ===== результат =====

@dataclass
class CaseResult:
    section: str
    title: str
    layer: str
    params: Dict[str, str]
    status: str = "passed"
    duration: float = 0.0
    messages: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    screenshots: List[str] = field(default_factory=list)

    @property
    def full_title(self) -> str:
        if not self.params:
            return self.title
        return self.title + " [" + ", ".join("%s=%s" % (k, v) for k, v in self.params.items()) + "]"

    def as_dict(self) -> dict:
        return {"section": self.section, "title": self.title, "layer": self.layer, "params": self.params,
                "status": self.status, "duration_s": round(self.duration, 2), "messages": self.messages,
                "warnings": self.warnings, "screenshots": self.screenshots}


# ===== контекст одного теста =====

class CaseContext:
    def __init__(self, world, result: CaseResult):
        self.world = world
        self.settings = world.settings
        self.log = world.log
        self.result = result
        self.params = result.params
        self.pages = []
        self.cleanup_callbacks = []

    @property
    def data(self):
        return self.world.data

    def api(self, role: str):
        return self.world.api(role)

    def anon(self):
        return self.world.anon()

    def account(self, role: str):
        return self.world.accounts.get(role)

    def step(self, text: str) -> None:
        self.log.info("шаг: " + text)

    def warn(self, text: str) -> None:
        self.result.warnings.append(text)
        self.log.warn(text)

    def fail(self, text: str) -> None:
        self.result.messages.append(text)
        self.log.error("ПРОВЕРКА НЕ ПРОЙДЕНА: " + text)

    def check(self, condition, text: str) -> bool:
        if condition:
            self.log.debug("ok: " + text)
            return True
        self.fail(text)
        return False

    def require(self, condition, text: str):
        if not self.check(condition, text):
            raise CaseFailure(text)
        return condition

    def expect_status(self, response, expected, what: str) -> bool:
        codes = expected if isinstance(expected, (list, tuple, set)) else (expected,)
        ok = response.status_code in codes
        detail = ""
        if not ok:
            try:
                body = response.json()
                detail = body.get("detail") if isinstance(body, dict) else str(body)[:200]
            except Exception:
                detail = (response.text or "")[:200]
        return self.check(ok, "%s: ожидался ответ %s, пришёл %s%s" % (
            what, "/".join(str(c) for c in codes), response.status_code, (" — " + str(detail)) if detail else ""))

    def skip(self, reason: str):
        raise SkipCase(reason)

    def page(self, role: str, theme: str = "light", device: str = "desktop", browser: str = "A",
             path: str = "#/tasks", wait: bool = True, engine: str = "", variant: str = ""):
        engine = engine or self.params.get("engine") or self.settings.browsers[0]
        page = self.world.browsers.open(role, theme, device, browser, path, self, wait, engine, variant)
        self.pages.append(page)
        return page

    def later(self, callback) -> None:
        self.cleanup_callbacks.append(callback)

    def screenshot(self, page, name: str) -> Optional[str]:
        safe = re.sub(r"[^0-9A-Za-zА-Яа-яЁё._-]+", "_", name)[:120]
        target = self.log.dir / "screenshots" / (safe + ".png")
        n = 2
        while target.exists():
            target = self.log.dir / "screenshots" / ("%s_%d.png" % (safe, n))
            n += 1
        try:
            page.raw.screenshot(path=str(target), full_page=False)
        except Exception as e:
            self.log.warn("снимок не получился: %s" % e)
            return None
        rel = "screenshots/" + target.name
        self.result.screenshots.append(rel)
        return rel


# ===== прогон =====

def expand(spec: CaseSpec, settings, roles) -> List[Dict[str, str]]:
    if not spec.matrix:
        return [{}]
    keys, values = [], []
    for key, source in spec.matrix.items():
        if source == "themes":
            options = list(settings.themes)
        elif source == "engines":
            options = list(settings.browsers)
        elif source == "devices":
            options = list(settings.devices)
        elif isinstance(source, str) and source.startswith("roles"):
            options = roles.select(source.partition(":")[2] or "all")
        elif isinstance(source, (list, tuple)):
            options = list(source)
        else:
            options = [source]
        keys.append(key)
        values.append(options)
    return [dict(zip(keys, combo)) for combo in itertools.product(*values)]


def load_sections(section_list):
    loaded = []
    for item in section_list:
        importlib.import_module("tests." + item["module"])
        loaded.append(item)
    return loaded


def run_case(world, spec: CaseSpec, section: str, params: Dict[str, str]) -> CaseResult:
    result = CaseResult(section=section, title=spec.title, layer=spec.layer, params=params)
    t = CaseContext(world, result)
    world.log.banner("[%s] %s" % (section, result.full_title))
    started = time.time()
    try:
        spec.func(t)
    except SkipCase as e:
        if not result.messages:
            result.status = "skipped"
            result.messages.append(str(e))
            world.log.info("пропущено: %s" % e)
    except CaseFailure:
        pass
    except Exception as e:
        result.messages.append("исключение %s: %s" % (type(e).__name__, str(e).splitlines()[0] if str(e) else ""))
        world.log.error(traceback.format_exc())
    if result.messages and result.status != "skipped":
        result.status = "failed"
        for page in t.pages:
            try:
                t.screenshot(page, "FAIL_%s_%s" % (section, spec.func.__name__))
            except Exception:
                pass
    for page in t.pages:
        world.browsers.release(page)
    for callback in reversed(t.cleanup_callbacks):
        try:
            callback()
        except Exception as e:
            world.log.warn("уборка после теста не удалась: %s" % e)
    result.duration = time.time() - started
    world.log.info("итог: %s за %.1f с" % (result.status.upper(), result.duration))
    return result


def planned(world, item) -> List:
    out = []
    specs = sorted(REGISTRY.get(item["module"], []), key=lambda s: s.order)
    for spec in specs:
        if spec.layer not in world.settings.layers:
            continue
        for params in expand(spec, world.settings, world.accounts):
            out.append((spec, params))
    return out


def only_filter() -> list:
    """ONLY — отбор отдельных тестов: части названия или имени функции через запятую."""
    import os
    return [x.strip().lower() for x in os.environ.get("ONLY", "").split(",") if x.strip()]


def run(world, sections, checkpoint=None) -> List[CaseResult]:
    results: List[CaseResult] = []
    only = only_filter()
    for item in sections:
        cases = planned(world, item)
        if only:
            cases = [(spec, params) for spec, params in cases
                     if any(o in spec.title.lower() or o in spec.func.__name__.lower() for o in only)]
        shard = item.get("shard")
        if shard:
            index, count = shard
            cases = [c for n, c in enumerate(cases) if n % count == index]
        for spec, params in cases:
            result = run_case(world, spec, item["key"], params)
            results.append(result)
            if checkpoint:
                checkpoint(results)
            world.progress(result, len(results))
    return results
