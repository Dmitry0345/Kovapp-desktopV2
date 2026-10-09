import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

import yaml

ROOT = Path(__file__).resolve().parent.parent


# ===== чтение окружения =====

def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def env(name: str, default: str = "") -> str:
    raw = os.environ.get(name)
    if raw is None:
        return default
    raw = raw.strip().strip('"').strip("'")
    return raw if raw != "" else default


def env_int(name: str, default: int) -> int:
    try:
        return int(float(env(name, str(default))))
    except ValueError:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    value = env(name, "").lower()
    if not value:
        return default
    return value in ("1", "true", "yes", "on", "да")


def env_list(name: str, default: str = "") -> List[str]:
    return [x.strip() for x in env(name, default).replace(";", ",").split(",") if x.strip()]


def load_yaml(name: str) -> dict:
    path = ROOT / "config" / name
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def parse_size(value: str, default=(1440, 900)):
    try:
        width, height = value.lower().replace("х", "x").split("x")
        return int(width), int(height)
    except Exception:
        return default


# ===== настройки прогона =====

@dataclass
class Settings:
    mode: str
    base_url: str
    app_url: str
    admin_login: str
    admin_password: str
    account_password: str
    create_accounts: bool
    cleanup: bool
    strict_files: str
    mail_dir: str
    browser_channel: str
    browsers: List[str]
    parallel: int
    mobile_device_webkit: str
    treat_secure: bool
    headless: bool
    slow_mo: int
    timeout_ms: int
    desktop_size: tuple
    mobile_device: str
    themes: List[str]
    devices: List[str]
    sections: List[str]
    layers: List[str]
    realtime_timeout: float
    log_root: Path
    keep_runs: int
    screenshots: str
    touch_min: int
    touch_warn: int
    record_video: bool
    run_label: str
    target: str = "web"              # web — сайт; apk — мобильная версия на ПК; device — настоящий Android
    project_dir: str = ""            # папка проекта (server, web_app, mobile) — нужна для target=apk
    apk_devices: Dict[str, str] = field(default_factory=dict)
    web_app_url: str = ""            # адрес сайта (для сверки снимков APK с сайтом)
    page_host: str = ""              # адрес, с которого страница ходит к API (у APK — «телефон»)
    # target=device: эмулятор Android Studio или телефон по USB (docs/DEVICE.md)
    adb_path: str = ""
    device_serial: str = ""
    device_app: str = "auto"         # auto | installed | apk | build
    device_apk: str = ""
    device_port: int = 8000          # порт сервера для приложения на телефоне (debugServerOrigin)
    device_host_port: int = 0        # порт сервера на ПК, куда телефон ходит через adb (0 — как в адресе сервера)
    device_live_web: bool = False    # веб-файлы прямо из проекта: правки без пересборки APK
    device_live_port: int = 8790
    extra: Dict[str, str] = field(default_factory=dict)


# Экраны мобильной версии: имя в APK_DEVICES → устройство Playwright. «desktop» у
# приложения — самый большой экран Android, планшет в альбомной ориентации
APK_DEVICE_DEFAULTS = {"mobile": ("APK_PHONE", "Pixel 7"), "small": ("APK_SMALL", "Galaxy S9+"),
                       "tablet": ("APK_TABLET", "Galaxy Tab S4"), "desktop": ("APK_WIDE", "Galaxy Tab S4 landscape")}


def project_dir() -> str:
    """Папка Android-версии (web_app и mobile/) — для интерфейса APK и сборки приложения."""
    for candidate in (env("PROJECT_MOUNT", ""), "/project", env("PROJECT_ANDROID_DIR", ""), env("PROJECT_DIR", ""),
                      "./project/android", "./project"):
        if candidate:
            path = Path(candidate)
            if not path.is_absolute():
                path = (ROOT / path).resolve()
            if (path / "web_app").is_dir():
                return str(path)
    return ""


def project_web_dir() -> str:
    """Папка веб-версии (server и web_app) — откуда запущен сервер; для сравнения сжатия и версии проекта."""
    for candidate in (env("PROJECT_WEB_MOUNT", ""), "/project-web", env("PROJECT_WEB_DIR", ""), env("PROJECT_DIR", ""),
                      "./project/web", "./project"):
        if candidate:
            path = Path(candidate)
            if not path.is_absolute():
                path = (ROOT / path).resolve()
            if (path / "web_app").is_dir():
                return str(path)
    return ""


def _server_root_for_apk() -> None:
    """Интерфейс APK на ПК берёт заголовки безопасности из сервера — он лежит в папке веб-версии."""
    web = project_web_dir()
    if web and (Path(web) / "server" / "server_app" / "core.py").is_file():
        os.environ.setdefault("KOVAPP_SERVER_ROOT", str(Path(web) / "server"))


def settings(mode: str = "", sections=None, layers=None, target: str = "") -> Settings:
    _server_root_for_apk()
    load_env_file(ROOT / ".env")
    mode = (mode or env("RUN_MODE", "full")).lower()
    target = (target or env("TARGET", "web")).lower()
    if mode == "full":
        base = env("FULL_BASE_URL", "http://server:8000")
        admin_login = env("FULL_ADMIN_LOGIN", "admin")
        admin_password = env("FULL_ADMIN_PASSWORD", "")
        account_password = env("FULL_ACCOUNT_PASSWORD", "Autotest-2026!")
        create_accounts = False
        cleanup = env_bool("FULL_CLEANUP", False)
        strict = "1" if env_bool("FULL_FILE_ACCESS_STRICT", True) else "0"
    else:
        base = env("EXTERNAL_BASE_URL", "")
        admin_login = env("EXTERNAL_ADMIN_LOGIN", "")
        admin_password = env("EXTERNAL_ADMIN_PASSWORD", "")
        account_password = env("EXTERNAL_ACCOUNT_PASSWORD", "Autotest-2026!")
        create_accounts = env_bool("EXTERNAL_CREATE_ACCOUNTS", True)
        cleanup = env_bool("EXTERNAL_CLEANUP", True)
        strict = env("EXTERNAL_FILE_ACCESS_STRICT", "auto").lower()
    base = base.rstrip("/")
    app_path = env("APP_PATH", "/app/")
    app_url = env("APP_URL", "") or (base + "/" + app_path.strip("/") + "/")
    chosen_sections = sections if sections is not None else env_list("SECTIONS", "all")
    chosen_layers = layers if layers is not None else env_list("LAYERS", "api,ui,visual,realtime")
    browsers = [b.lower() for b in env_list("BROWSERS", env("BROWSER_CHANNEL", "chrome") + ",firefox,webkit")]
    devices = env_list("DEVICES", "desktop,mobile")
    apk_devices = {}
    if target == "device":
        # страница в приложении одна — сам телефон; второй участник — сайт в Chromium на ПК
        browsers = ["chrome"]
        devices = ["phone"]
        if layers is None:
            chosen_layers = env_list("DEVICE_LAYERS", "device")
    if target == "apk":
        # приложение на телефоне рисует Android System WebView — это движок Chromium
        browsers = [env("APK_BROWSER", "chrome").lower()]
        devices = env_list("APK_DEVICES", "mobile,small,tablet")
        if layers is None:
            chosen_layers = env_list("APK_LAYERS", "ui,visual,realtime,mobile")
        apk_devices = {name: env(var, default) for name, (var, default) in APK_DEVICE_DEFAULTS.items()}
        for name in devices:
            apk_devices.setdefault(name, name)          # можно указать и название устройства Playwright
    return Settings(
        mode=mode,
        base_url=base,
        app_url=app_url,
        admin_login=admin_login,
        admin_password=admin_password,
        account_password=account_password,
        create_accounts=create_accounts,
        cleanup=cleanup,
        strict_files=strict,
        mail_dir=env("MAIL_DIR", "/shared/mail" if mode == "full" else ""),
        browser_channel=env("BROWSER_CHANNEL", "chrome"),
        browsers=browsers,
        parallel=max(1, min(8, env_int("PARALLEL", 1))),
        mobile_device_webkit=env("MOBILE_DEVICE_WEBKIT", "iPhone 13"),
        treat_secure=env_bool("TREAT_AS_SECURE", True),
        headless=env_bool("HEADLESS", True),
        slow_mo=env_int("SLOW_MO_MS", 0),
        timeout_ms=env_int("TIMEOUT_MS", 15000),
        desktop_size=parse_size(env("DESKTOP_VIEWPORT", "1440x900")),
        mobile_device=env("MOBILE_DEVICE", "Pixel 7"),
        themes=env_list("THEMES", "light,dark"),
        devices=devices,
        sections=[s.lower() for s in chosen_sections] or ["all"],
        layers=[x.lower() for x in chosen_layers],
        realtime_timeout=float(env_int("REALTIME_TIMEOUT_S", 12)),
        log_root=Path(env("LOG_DIR", str(ROOT / "logs"))),
        keep_runs=env_int("KEEP_RUNS", 30),
        screenshots=env("SCREENSHOTS", "all").lower(),
        touch_min=env_int("TOUCH_MIN_PX", 24),
        touch_warn=env_int("TOUCH_WARN_PX", 40),
        record_video=env_bool("RECORD_VIDEO", False),
        run_label=env("RUN_LABEL", "") or (target if target in ("apk", "device") else ""),
        target=target,
        project_dir=project_dir(),
        apk_devices=apk_devices,
        adb_path=env("ADB_PATH", ""),
        device_serial=env("DEVICE_SERIAL", ""),
        device_app=env("DEVICE_APP", "auto").lower(),
        device_apk=env("DEVICE_APK", ""),
        device_port=int(env("DEVICE_PORT", "8000") or 8000),
        device_host_port=int(env("DEVICE_HOST_PORT", "0") or 0),
        device_live_web=env_bool("DEVICE_LIVE_WEB", False),
        device_live_port=int(env("DEVICE_LIVE_PORT", "8790") or 8790),
        web_app_url=app_url,
        page_host=base,
    )
