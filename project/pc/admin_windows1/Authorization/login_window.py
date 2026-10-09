import os
import sys

from dotenv import load_dotenv
from PySide6.QtCore import QCoreApplication, QPointF, QProcess, QRectF, QSize, Qt, QTimer
try:
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
except Exception:
    pass

from PySide6.QtGui import QColor, QImage, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import (QApplication, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit,
                             QMainWindow, QPushButton, QToolButton, QVBoxLayout, QWidget)

import admin_windows1.scaler as scaler
from general_functions import theme
from kov import icons, jobs
from kov.api import ApiError, client, human_error
from kov.ui import Button, font, input_qss, label, rem, border

load_dotenv()


class _Backdrop(QWidget):
    """Фон входа как у сайта (login.css): три мягких пятна и еле заметная сетка в середине.
    Пятна — radial-gradient(circle at X Y, цвет, transparent 70%) по месту кругов сайта; без движения."""

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QColor(theme.PAGE_BG))
        w, h, r = self.width(), self.height(), rem(1)
        # (левый край круга, верхний край, диаметр, центр градиента в долях круга, цвет)
        blobs = ((-10 * r, -12 * r, 38 * r, (0.35, 0.35), theme.LOGIN_BLOB_1),
                 (w + 8 * r - 32 * r, h + 10 * r - 32 * r, 32 * r, (0.6, 0.4), theme.LOGIN_BLOB_2),
                 (w * 0.82 - 22 * r, h * 0.08, 22 * r, (0.5, 0.5), theme.LOGIN_BLOB_3))
        p.setOpacity(theme.LOGIN_BLOB_OPACITY)
        for x, y, d, (fx, fy), color in blobs:
            cx, cy = x + d * fx, y + d * fy
            far = d * max(fx, 1 - fx) * 2 ** 0.5             # до дальнего угла (farthest-corner)
            g = QRadialGradient(QPointF(cx, cy), far * 0.7)
            c, c0 = QColor(color), QColor(color)
            c0.setAlpha(0)
            g.setColorAt(0.0, c)
            g.setColorAt(1.0, c0)
            p.fillRect(self.rect(), g)          # без обрезки кругом: у сайта край круга размыт blur(60px)
        p.setOpacity(1.0)
        # сетка 3,5 rem, видна в середине: маска radial-gradient(circle at 50% 45%, #000 15%, transparent 68%)
        grid = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        grid.fill(Qt.transparent)
        gp = QPainter(grid)
        gp.setPen(QPen(QColor(theme.LOGIN_GRID), 1))
        step = 3.5 * r
        k = 0.0
        while k < max(w, h):
            gp.drawLine(QPointF(k, 0), QPointF(k, h))
            gp.drawLine(QPointF(0, k), QPointF(w, k))
            k += step
        gp.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        gx, gy = w * 0.5, h * 0.43                          # 50 % 45 % поля сетки (оно шире экрана на 20 % с каждой стороны)
        far = ((0.7 * w) ** 2 + (gy + 0.2 * h) ** 2) ** 0.5
        mask = QRadialGradient(QPointF(gx, gy), far)
        mask.setColorAt(0.15, QColor(0, 0, 0, 255))
        mask.setColorAt(0.68, QColor(0, 0, 0, 0))
        mask.setColorAt(0.0, QColor(0, 0, 0, 255))
        gp.fillRect(grid.rect(), mask)
        gp.end()
        p.drawImage(0, 0, grid)
        p.end()


class _Logo(QLabel):
    def __init__(self, small=False, parent=None):
        super().__init__(parent)
        if small:
            svg, w, h = icons.mark_svg(theme.MARK_INK, theme.MARK_ACCENT), rem(4.4), rem(3.3)
        else:
            svg, w, h = icons.lockup_svg(theme.MARK_INK, theme.MARK_ACCENT), rem(17), rem(5.5)
        self.setPixmap(icons.render_svg(svg, w, h))
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background: transparent;")


class _PassField(QWidget):
    """Пароль с глазом: текст можно на время показать."""

    def __init__(self, placeholder, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.input = QLineEdit()
        self.input.setPlaceholderText(placeholder)
        self.input.setEchoMode(QLineEdit.Password)
        self.input.setAlignment(Qt.AlignCenter)
        self.input.setFont(font(0.875))
        self.input.setMinimumHeight(rem(2.6))
        self.input.setStyleSheet(input_qss() + f"QLineEdit {{ padding-left: {rem(2.6)}px; padding-right: {rem(2.6)}px; }}")
        lay.addWidget(self.input)
        self.eye = QToolButton(self.input)
        self.eye.setCursor(Qt.PointingHandCursor)
        self.eye.setToolTip("Показать пароль")
        self.eye.setStyleSheet(f"QToolButton {{ border: none; background: transparent; border-radius: {rem(1)}px; padding: {rem(0.3)}px; }}"
                               f"QToolButton:hover {{ background: {theme.CHIP_BG}; }}")
        self.eye.clicked.connect(self._toggle)
        self._paint_eye(False)

    def _paint_eye(self, shown):
        self.eye.setIcon(icons.icon("eye_off" if shown else "eye", rem(1.1), theme.INFO if shown else theme.TEXT_LABEL))
        self.eye.setIconSize(QSize(rem(1.1), rem(1.1)))
        self.eye.setToolTip("Скрыть пароль" if shown else "Показать пароль")

    def _toggle(self):
        shown = self.input.echoMode() == QLineEdit.Password
        self.input.setEchoMode(QLineEdit.Normal if shown else QLineEdit.Password)
        self._paint_eye(shown)
        self.input.setFocus()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        s = rem(1.8)
        self.eye.setGeometry(self.input.width() - s - rem(0.35), (self.input.height() - s) // 2, s, s)


def _centered_input(placeholder, text=""):
    e = QLineEdit()
    e.setPlaceholderText(placeholder)
    e.setText(text or "")
    e.setAlignment(Qt.AlignCenter)
    e.setFont(font(0.875))
    e.setMinimumHeight(rem(2.6))
    e.setStyleSheet(input_qss())
    return e


def _link(text):
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    b.setFont(font(0.85))
    b.setStyleSheet(f"QPushButton {{ border: none; background: transparent; color: {theme.LINK};"
                    f" text-decoration: underline; padding: {rem(0.3)}px; }}"
                    f"QPushButton:hover {{ color: {theme.ACCENT}; }}")
    return b


class LoginWindow(QMainWindow):
    """Окно входа — как экран входа сайта: знак Kovapp, логин, пароль,
    «Войти», «Сброс пароля» и переключатель темы. Адрес сервера задан при установке и не показывается."""

    def __init__(self):
        super().__init__()
        self.main_window = None
        self.setWindowTitle("Kovapp — вход")
        from kov.shell import app_icon
        self.setWindowIcon(app_icon())
        self.setMinimumSize(rem(30), rem(36))
        self.resize(rem(34), rem(40))
        self._build_ui()
        self.show_login()
        self.center_on_screen()

    def _build_ui(self):
        """Фон, переключатель темы и карточка — в цветах текущей темы (и при смене темы — заново)."""
        self.backdrop = _Backdrop()
        self.setCentralWidget(self.backdrop)
        outer = QVBoxLayout(self.backdrop)
        outer.setContentsMargins(rem(1), rem(1), rem(1), rem(1))

        top = QHBoxLayout()
        top.addStretch(1)
        self.theme_btn = QPushButton()
        self.theme_btn.setCursor(Qt.PointingHandCursor)
        self.theme_btn.setFont(font(0.8, bold=True))

        def toggle_qss(radius):
            return (f"QPushButton {{ background: {theme.CARD_BG}; border: {border()}px solid {theme.CARD_BORDER};"
                    f" color: {theme.TEXT_LABEL}; border-radius: {radius}px; padding: {rem(0.5)}px {rem(0.85)}px; }}"
                    f"QPushButton:hover {{ color: {theme.ACCENT}; border-color: {theme.ACCENT_SOFT}; }}")
        self.theme_btn.setStyleSheet(toggle_qss(rem(1.4)))
        dark = theme.is_dark()
        self.theme_btn.setText("Светлая тема" if dark else "Тёмная тема")
        self.theme_btn.setIcon(icons.icon("sun" if dark else "moon", rem(1.05), theme.TEXT_LABEL))
        self.theme_btn.setIconSize(QSize(rem(1.05), rem(1.05)))     # не от DPI экрана (125 %, 150 % в Windows)
        # скругление 1,4rem больше половины высоты: браузер делает «таблетку», Qt — прямые углы
        side = self.theme_btn.sizeHint().height()
        self.theme_btn.setFixedHeight(side)
        self.theme_btn.setStyleSheet(toggle_qss(min(rem(1.4), side // 2)))
        self.theme_btn.setToolTip("Вернуть светлую тему" if dark else "Включить тёмную тему")
        self.theme_btn.clicked.connect(self._toggle_theme)
        top.addWidget(self.theme_btn)
        outer.addLayout(top)
        outer.addStretch(1)

        row = QHBoxLayout()
        row.addStretch(1)
        self.card = QFrame()
        self.card.setObjectName("loginCard")
        self.card.setFixedWidth(rem(24))
        self.card.setStyleSheet(f"QFrame#loginCard {{ background: {theme.CARD_BG}; border: {border()}px solid {theme.CARD_BORDER};"
                                f" border-radius: {rem(1)}px; }}")
        sh = QGraphicsDropShadowEffect(self.card)
        sh.setBlurRadius(rem(2))
        sh.setOffset(0, rem(0.4))
        sh.setColor(QColor(*theme.SHADOW))
        self.card.setGraphicsEffect(sh)
        self.card_layout = QVBoxLayout(self.card)
        self.card_layout.setContentsMargins(rem(1.75), rem(2), rem(1.75), rem(2))
        self.card_layout.setSpacing(rem(0.8))
        row.addWidget(self.card)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(2)   # адрес сервера скрыт: он задаётся при установке, менять его в окне входа не нужно

    def rebuild(self):
        """Смена темы на окне входа: то же окно, содержимое собирается заново (логин сохраняется)."""
        login = self.login_input.text() if hasattr(self, "login_input") else ""
        old = self.takeCentralWidget()
        self._build_ui()
        self.show_login()
        if login:
            self.login_input.setText(login)
        if old is not None:
            old.hide()
            old.deleteLater()

    # ── экраны ───────────────────────────────────────────────────────────────

    def _clear(self):
        lay = self.card_layout
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _error_label(self):
        err = label("", 0.85, theme.CRITICAL_TEXT, wrap=True)
        err.setStyleSheet(f"color: {theme.CRITICAL_TEXT}; background: {theme.CRITICAL_SOFT}; border-radius: {rem(0.5)}px;"
                          f" padding: {rem(0.5)}px {rem(0.7)}px;")
        err.hide()
        return err

    def show_login(self, message=""):
        self._clear()
        self.card_layout.addWidget(_Logo())
        if message:
            hint = label(message, 0.78, theme.TEXT_LABEL, wrap=True, align=Qt.AlignCenter)
            self.card_layout.addWidget(hint)
        self.login_input = _centered_input("Логин", scaler.get_setting("last_login", "") or "")
        self.pass_box = _PassField("Пароль")
        self.password_input = self.pass_box.input
        self.error_label = self._error_label()
        self.login_button = Button("Войти", "primary", "check")
        self.login_button.setMinimumHeight(rem(2.6))
        self.login_button.clicked.connect(self.on_login_clicked)
        reset = _link("Сброс пароля")
        reset.clicked.connect(lambda: self.show_reset(self.login_input.text().strip()))
        for w in (self.login_input, self.pass_box, self.error_label, self.login_button):
            self.card_layout.addWidget(w)
        self.card_layout.addWidget(reset, 0, Qt.AlignHCenter)
        for e in (self.login_input, self.password_input):
            e.returnPressed.connect(self.on_login_clicked)
        QTimer.singleShot(0, lambda: (self.password_input if self.login_input.text() else self.login_input).setFocus())

    def show_reset(self, prefill=""):
        self._clear()
        brand = QWidget()
        bl = QVBoxLayout(brand)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.addWidget(_Logo(small=True))
        bl.addWidget(label("Сброс пароля", 1.2, theme.MARK_INK, bold=True, align=Qt.AlignCenter))
        self.card_layout.addWidget(brand)
        info = label("Введите логин — на почту сотрудника придёт код", 0.78, theme.TEXT_LABEL, wrap=True, align=Qt.AlignCenter)
        username = _centered_input("Логин", prefill)
        code = _centered_input("Код из письма")
        box1, box2 = _PassField("Новый пароль"), _PassField("Повторите пароль")
        error = self._error_label()
        nxt = Button("Отправить код", "primary", "mail")
        back = _link("Назад ко входу")
        back.clicked.connect(lambda: self.show_login())
        code.hide()
        box1.hide()
        box2.hide()
        step = {"n": 1}

        def fail(text):
            error.setText(text)
            error.show()

        def go():
            error.hide()
            try:
                if step["n"] == 1:
                    if not username.text().strip():
                        return fail("Введите логин")
                    r = client.request("POST", "reset-password/request", json_body={"username": username.text().strip()}, anonymous=True)
                    if r and r.get("success") is False:
                        return fail(r.get("message") or "Не удалось отправить код")
                    step["n"] = 2
                    info.setText((r or {}).get("message") or "Код отправлен. Введите его ниже.")
                    code.show()
                    nxt.setText("Проверить код")
                    nxt.set_glyph("check")
                    code.setFocus()
                elif step["n"] == 2:
                    if not code.text().strip():
                        return fail("Введите код")
                    r = client.request("POST", "reset-password/verify", anonymous=True, json_body={
                        "username": username.text().strip(), "reset_code": code.text().strip()})
                    if r and r.get("success") is False:
                        left = r.get("attempts_left")
                        return fail((r.get("message") or "Неверный код") + (" (осталось попыток: %s)" % left if left is not None else ""))
                    step["n"] = 3
                    info.setText("Код принят. Придумайте новый пароль.")
                    box1.show()
                    box2.show()
                    nxt.setText("Сменить пароль")
                    nxt.set_glyph("key")
                    box1.input.setFocus()
                else:
                    if len(box1.input.text()) < 6:
                        return fail("Пароль не короче 6 символов")
                    if box1.input.text() != box2.input.text():
                        return fail("Пароли не совпадают")
                    r = client.request("POST", "reset-password/confirm", anonymous=True, json_body={
                        "username": username.text().strip(), "reset_code": code.text().strip(),
                        "new_password": box1.input.text()})
                    if r and r.get("success") is False:
                        return fail(r.get("message") or "Не удалось сменить пароль")
                    self.show_login("Пароль изменён — войдите с новым паролем")
            except Exception as e:
                fail(human_error(e))
        nxt.clicked.connect(go)
        for e in (username, code, box1.input, box2.input):
            e.returnPressed.connect(go)
        for w in (info, username, code, box1, box2, error, nxt):
            self.card_layout.addWidget(w)
        self.card_layout.addWidget(back, 0, Qt.AlignHCenter)
        client.set_auth(scaler.get_setting("host", scaler.DEFAULT_HOST), "", None)
        username.setFocus()

    # ── вход ─────────────────────────────────────────────────────────────────

    def on_login_clicked(self):
        self.error_label.hide()
        login = self.login_input.text().strip()
        password = self.password_input.text()
        if not login or not password:
            self.error_label.setText("Введите логин и пароль")
            self.error_label.show()
            return
        self.login_button.setEnabled(False)
        host = scaler.get_setting("host", scaler.DEFAULT_HOST)

        def work():
            client.login(host, login, password)
            from kov.store import store
            store.reset()
            store.load_me()
            try:
                settings = store.pref("web.settings") or {}
            except Exception:
                settings = {}
            return settings

        def done(settings):
            scaler.set_setting("last_login", login)
            scaler.save_settings()
            self.open_main_window(client.user_id, client.token, settings)

        def failed(e):
            self.login_button.setEnabled(True)
            if isinstance(e, ApiError) and e.status == 401:
                text = "Неверный логин или пароль"
            elif isinstance(e, ApiError) and e.status == 429:
                text = "Слишком много попыток, подождите минуту"
            else:
                text = "Ошибка входа: " + human_error(e)
            self.error_label.setText(text)
            self.error_label.show()
            self.password_input.clear()
        jobs.run(work, done=done, failed=failed, owner=self)

    def open_main_window(self, user_id, access_token, settings=None, route=None):
        """Тема и масштаб сотрудника с сервера главнее местных: он мог сменить их на сайте."""
        settings = settings or {}
        wanted_theme = settings.get("theme") if settings.get("theme") in ("light", "dark", "auto") else None
        wanted_scale = settings.get("scale")
        changed = False
        if wanted_theme and wanted_theme != scaler.get_setting("theme", "light"):
            scaler.set_setting("theme", wanted_theme)
            changed = True
        if wanted_scale:
            try:
                value = scaler.get_manager().clamp_multiplier(float(wanted_scale))
                if abs(value - scaler.scale_multiplier()) > 0.004:
                    scaler.set_setting("scale_multiplier", value)
                    changed = True
            except (TypeError, ValueError):
                pass
        if changed:
            scaler.save_settings()
            # тема и масштаб вступают в силу при сборке окон: главное окно ещё не собрано —
            # достаточно включить их до него (раньше здесь был перезапуск программы)
            theme.apply(scaler.get_setting("theme", "light"))
        from kov.shell import Shell
        from kov.ui import apply_app_theme
        apply_app_theme(QApplication.instance())
        self.main_window = Shell()
        self.main_window.logged_out.connect(_back_to_login)
        if scaler.get_setting("window_mode", "fullscreen") == "fullscreen":
            self.main_window.showFullScreen()
        else:
            self.main_window.resize(min(rem(80), QApplication.primaryScreen().availableGeometry().width() - 40),
                                    min(rem(50), QApplication.primaryScreen().availableGeometry().height() - 60))
            self.main_window.show()
        self.main_window.start(route)
        _WINDOWS.append(self.main_window)
        self.close()

    # ── тема ─────────────────────────────────────────────────────────────────

    def _toggle_theme(self):
        """Как на сайте: новая тема наплывает кругом от кнопки, окно то же."""
        mode = "light" if theme.is_dark() else "dark"
        origin = self.theme_btn.mapTo(self, self.theme_btn.rect().center())
        from kov import restyle
        restyle.switch(self, mode=mode, origin=origin)

    def center_on_screen(self):
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.center().x() - self.width() // 2, screen.center().y() - self.height() // 2)


_WINDOWS = []


def _back_to_login():
    w = LoginWindow()
    w.show()
    _WINDOWS.append(w)


if __name__ == '__main__':
    app = QApplication(sys.argv)
    scaler.init_scaler(app)
    app.setStyle('Fusion')
    window = LoginWindow()
    window.show()
    sys.exit(app.exec())
