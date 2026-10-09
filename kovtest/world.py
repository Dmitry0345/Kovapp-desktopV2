import os
import sys
import time

import requests

from .accounts import Accounts
from .api import Api, ApiError
from .browser import Browsers, load_ui
from .data import Data
from .log import RunLog
from .media import MediaKit


# ===== общее окружение прогона =====

class World:
    def __init__(self, settings, folder=None):
        self.settings = settings
        self.log = RunLog(settings.log_root, settings.mode, settings.run_label, settings.keep_runs, folder=folder)
        self.ui = load_ui()
        self.media = MediaKit()
        self._apis = {}
        self.accounts = Accounts(self)
        self.data = Data(self)
        self._browsers = None
        self._push = None
        self.apk = None                  # «телефон» для мобильной версии (target=apk)
        self.device = None               # настоящий Android (target=device)
        self.device2 = None              # второй телефон (run.cmd device-pair)
        self.facts = {}

    def start_apk(self) -> None:
        """Мобильная версия: интерфейс APK с надстройкой, запросы — на тот же сервер."""
        if self.settings.target != "apk" or self.apk is not None:
            return
        from .apk import ApkEnv
        self.apk = ApkEnv(self)
        self.settings.app_url = self.apk.app_url
        self.settings.page_host = self.apk.origin
        self.log.info("мобильная версия: интерфейс APK %s → сервер %s" % (self.apk.app_url, self.settings.base_url))

    def start_device(self) -> None:
        """Настоящий Android: приложение на эмуляторе или телефоне, сервер — через adb reverse."""
        if self.settings.target != "device" or self.device is not None:
            return
        # Appium (всё в Docker) — основной путь; без него — прежнее подключение через adb на этом ПК
        if os.environ.get("APPIUM_URL"):
            from .appium_env import AppiumEnv
            self.device = AppiumEnv(self, label="телефон 1" if os.environ.get("APPIUM2_URL") else "телефон")
            # второй телефон (run.cmd device-pair): проверки в реальном времени между двумя устройствами
            if os.environ.get("APPIUM2_URL"):
                self.device2 = AppiumEnv(self, url=os.environ["APPIUM2_URL"],
                                         link_url=os.environ.get("LINK2_URL", "http://appium2:4799"), label="телефон 2")
            origin = "http://localhost:%d" % self.settings.device_port
        else:
            from .device import DeviceEnv
            self.device = DeviceEnv(self)
            origin = self.device.origin()
        self.settings.app_url = origin + "/app/index.html"
        self.settings.page_host = origin

    def api(self, role: str) -> Api:
        if role not in self._apis:
            client = Api(self.settings.base_url, self.log, who=role)
            if role == "admin":
                client.login(self.settings.admin_login, self.settings.admin_password)
            else:
                account = self.accounts.get(role)
                client.login(account.login, account.password)
            self._apis[role] = client
        return self._apis[role]

    def anon(self) -> Api:
        return Api(self.settings.base_url, self.log, who="аноним")

    @property
    def push_receiver(self):
        if self._push is None:
            from .push_receiver import Receiver
            from .config import env
            self._push = Receiver(self.log, env("PUSH_CA_BUNDLE", ""))
        return self._push

    @property
    def browsers(self) -> Browsers:
        if self._browsers is None:
            self._browsers = Browsers(self)
        return self._browsers

    def set_theme(self, role: str, theme: str) -> None:
        # тема хранится отдельно для компьютера и телефона (beta 3) и в прежнем общем ключе — ставим во всех
        api = self.api(role)
        for key in ("web.settings", "web.settings.pc", "web.settings.mobile"):
            try:
                current = api.call("GET", "preferences/" + key, expect=(200, 404))
                value = (current or {}).get("value") if isinstance(current, dict) else None
            except ApiError:
                value = None
            value = dict(value or {})
            if value.get("theme") == theme:
                continue
            value["theme"] = theme
            api.call("PUT", "preferences/" + key, json={"value": value})

    def wait_server(self, timeout: float = 240.0) -> bool:
        deadline = time.time() + timeout
        last = ""
        while time.time() < deadline:
            try:
                response = requests.get(self.settings.app_url, timeout=10)
                if response.status_code == 200:
                    return True
                last = "ответ %s" % response.status_code
            except requests.RequestException as e:
                last = str(e)[:120]
            time.sleep(3)
        self.log.error("сервер не ответил за %d с: %s" % (timeout, last))
        return False

    def detect_file_mode(self) -> str:
        try:
            body = self.api("admin").call("GET", "health/file-access")
            mode = "strict" if body.get("strict") else "soft"
        except ApiError as e:
            mode = "unknown"
            self.log.warn("режим доступа к файлам не определён: %s" % e)
        wanted = self.settings.strict_files
        if wanted in ("0", "1"):
            expected = "strict" if wanted == "1" else "soft"
            if mode != "unknown" and mode != expected:
                self.log.warn("сервер работает в режиме %s, а в .env ожидается %s" % (mode, expected))
        self.facts["file_mode"] = mode
        return mode

    def progress(self, result, number: int) -> None:
        mark = {"passed": "OK  ", "failed": "FAIL", "skipped": "SKIP"}.get(result.status, result.status)
        line = "%4d %s [%s] %s (%.1f с)" % (number, mark, result.section, result.full_title, result.duration)
        print(line[:220], flush=True)
        if result.status == "failed":
            for message in result.messages[:3]:
                print("         — " + message[:200], flush=True)

    def close(self) -> None:
        if self._push is not None:
            self._push.close()
        if self.apk is not None:
            self.apk.close()
        if self.device is not None:
            self.device.close()
        if self.device2 is not None:
            self.device2.close()
        if self._browsers is not None:
            self._browsers.close()
