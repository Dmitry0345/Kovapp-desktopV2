import sys
import os
import json
from PySide6.QtCore import QCoreApplication, Qt
try:
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
except Exception:
    pass
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont

APP_NAME = "KovApp"


def get_settings_file():
    local_app_data = os.environ.get("LOCALAPPDATA")

    if local_app_data:
        app_data_dir = os.path.join(
            local_app_data,
            APP_NAME
        )
    else:
        app_data_dir = os.path.join(
            os.path.expanduser("~"),
            f".{APP_NAME}"
        )

    os.makedirs(app_data_dir, exist_ok=True)

    return os.path.join(app_data_dir, "settings.txt")


SETTINGS_FILE = get_settings_file()

# Допустимый диапазон пользовательского множителя масштаба (как у сайта).
MIN_SCALE_MULTIPLIER = 0.75
MAX_SCALE_MULTIPLIER = 1.25
# Эталон экрана — тот же, что у сайта (js/core/scale.js): 1536×864.
# Прежний эталон 1920×1080 давал вёрстку на четверть мельче сайта.
BASE_SCREEN_WIDTH = 1536
BASE_SCREEN_HEIGHT = 864
OLD_BASE_WIDTH, OLD_BASE_HEIGHT = 1920, 1080
DESK_MIN, DESK_MAX = 0.62, 1.8
DEFAULT_HOST = "http://83.171.90.230:54545"
DEBUG = bool(os.environ.get("KOVAPP_DEBUG"))


def _log(text):
    if DEBUG:
        print(f"[SCALER LOG] {text}")


class _SettingsManager:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(_SettingsManager, cls).__new__(cls)
        return cls._instance

    def __init__(self, app=None):
        if hasattr(self, 'initialized') and self.initialized:
            return

        if not app:
            app = QApplication.instance()
            if not app:
                app = QApplication(sys.argv)

        self.settings = {}
        self._load_or_create(app)
        self.initialized = True

    def _screen_geometry(self, app=None):
        width, height = BASE_SCREEN_WIDTH, BASE_SCREEN_HEIGHT
        app = app or QApplication.instance()
        if app:
            screen = app.primaryScreen()
            if screen:
                width = screen.geometry().width()
                height = screen.geometry().height()
        return width, height

    def base_scale_factor(self, app=None):
        """Коэффициент экрана без пользовательского множителя."""
        width, height = self._screen_geometry(app)
        base = min(width / BASE_SCREEN_WIDTH, height / BASE_SCREEN_HEIGHT) or 1.0
        return min(max(base, DESK_MIN), DESK_MAX)

    def clamp_multiplier(self, value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = 1.0
        return min(max(value, MIN_SCALE_MULTIPLIER), MAX_SCALE_MULTIPLIER)

    def clamp_scale_factor(self, factor, app=None):
        """Зажимает итоговый коэффициент в границы множителя 0.75–1.25."""
        base = self.base_scale_factor(app) or 1.0
        try:
            factor = float(factor)
        except (TypeError, ValueError):
            factor = base
        return min(max(factor, base * MIN_SCALE_MULTIPLIER), base * MAX_SCALE_MULTIPLIER)

    def _get_default_settings(self, app):
        return {
            "scale_multiplier": 1.0,
            "window_mode": "fullscreen",
            "host": DEFAULT_HOST,
            "theme": "light",
        }

    def _load_or_create(self, app):
        defaults = self._get_default_settings(app)
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                    self.settings = json.load(f)
                updated = False
                # Прежний файл хранил готовый коэффициент от эталона 1920×1080:
                # переводим его в множитель, чтобы масштаб остался выбранным
                if "scale_multiplier" not in self.settings and "scale_factor" in self.settings:
                    width, height = self._screen_geometry(app)
                    old_base = min(width / OLD_BASE_WIDTH, height / OLD_BASE_HEIGHT) or 1.0
                    self.settings["scale_multiplier"] = self.clamp_multiplier(
                        float(self.settings.get("scale_factor") or old_base) / old_base)
                    updated = True
                for key, value in defaults.items():
                    if key not in self.settings:
                        self.settings[key] = value
                        updated = True
                clamped = self.clamp_multiplier(self.settings.get("scale_multiplier"))
                if clamped != self.settings.get("scale_multiplier"):
                    self.settings["scale_multiplier"] = clamped
                    updated = True
                self._refresh_factor(app)
                if updated:
                    self.save()
                return
            except (json.JSONDecodeError, IOError, ValueError) as e:
                _log(f"Ошибка чтения файла настроек: {e}")

        self.settings = defaults
        self._refresh_factor(app)
        self.save()

    def _refresh_factor(self, app=None):
        """scale_factor = база экрана × множитель; держим в памяти для scale()."""
        self.settings["scale_factor"] = round(
            self.base_scale_factor(app) * self.clamp_multiplier(self.settings.get("scale_multiplier", 1.0)), 4)

    def save(self):
        try:
            with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.settings, f, indent=4, ensure_ascii=False)
        except IOError as e:
            print(f"[настройки] Не удалось сохранить файл настроек: {e}")

    def get(self, key, default=None):
        return self.settings.get(key, default)

    def set(self, key, value):
        if key == 'scale_factor':
            # старый вызов: коэффициент → множитель
            base = self.base_scale_factor() or 1.0
            key, value = 'scale_multiplier', float(value) / base
        if key == 'scale_multiplier':
            value = self.clamp_multiplier(value)
        self.settings[key] = value
        if key == 'scale_multiplier':
            self._refresh_factor()

    def scale(self, value):
        return int(value * self.get('scale_factor', 1.0))

    def scale_font(self, font: QFont):
        point_size = font.pointSize()
        if point_size > 0:
            base_pixel_size = int((point_size * 96) / 72)
            scaled_pixel_size = self.scale(base_pixel_size)
            if scaled_pixel_size > 0:
                font.setPixelSize(scaled_pixel_size)
        else:
            pixel_size = font.pixelSize()
            if pixel_size > 0:
                scaled_pixel_size = self.scale(pixel_size)
                if scaled_pixel_size > 0:
                    font.setPixelSize(scaled_pixel_size)
        return font

    def scale_value(self, value):
        """Универсальная функция масштабирования для чисел и QFont"""
        if isinstance(value, QFont):
            return self.scale_font(value)
        return self.scale(value)


def get_manager(app=None):
    return _SettingsManager(app)


def get_setting(key, default=None):
    return get_manager().get(key, default)


def set_setting(key, value):
    get_manager().set(key, value)


def save_settings():
    get_manager().save()


def scale(value):
    return get_manager().scale(value)


def scale_font(font):
    return get_manager().scale_font(font)


def scale_value(value):
    """Универсальная функция масштабирования"""
    return get_manager().scale_value(value)


def scale_less_10(value):
    return get_manager().scale(value * 0.90)


def rem(value):
    """Размер в rem сайта (1rem = 16 px при множителе 1 на эталонном экране)."""
    return max(1, int(round(16 * value * get_manager().get('scale_factor', 1.0))))


def base_scale_factor(app=None):
    return get_manager().base_scale_factor(app)


def clamp_scale_factor(factor, app=None):
    return get_manager().clamp_scale_factor(factor, app)


def scale_multiplier():
    """Текущий пользовательский множитель в границах 0.75–1.25."""
    return get_manager().clamp_multiplier(get_manager().get('scale_multiplier', 1.0))


def init_scaler(app):
    get_manager(app)
