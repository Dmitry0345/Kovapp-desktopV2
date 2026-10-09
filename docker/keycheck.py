"""Проверка ключей Firebase из папки keys — тем же кодом, которым сервер отправляет уведомления.

1. google-services: проект, приложение ru.kovcheg.kovapp, ключ API.
2. google_key: ключ читается, Google выдаёт по нему токен доступа.
3. Оба файла — от одного проекта Firebase.
4. Отправка через FCM разрешена: пробное сообщение с validate_only (Google проверяет,
   но никому не доставляет).
5. Сторона телефона: ключ API приложения из google-services принимается службой Firebase Installations —
   с неё телефон начинает получать адрес доставки. С подписью отладочной сборки (SHA-1), как у телефона.
Итог — в /shared/keycheck.json (его читают тесты) и в окне.
"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, "/project")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import keyfiles  # noqa: E402

PACKAGE = "ru.kovcheg.kovapp"
OK, BAD, NOTE = "  ✓ ", "  ✗ ", "  • "


def with_retries(action, attempts: int = 5, pause: float = 3.0):
    """Сбой связи (DNS, таймаут) повторяем: сразу после создания сети Docker DNS бывает недоступен."""
    for attempt in range(attempts):
        try:
            return action()
        except urllib.error.HTTPError:
            raise                                   # Google ответил — это ответ, а не сбой связи
        except OSError:
            if attempt == attempts - 1:
                raise
            time.sleep(pause)


def main() -> int:
    # checked=False — ключи не проверены (нет связи); это не то же самое, что «ключи плохие»
    result = {"present": False, "valid": False, "checked": True, "problems": []}
    files = keyfiles.find("/keys")
    print("Проверка ключей Firebase (папка keys)\n")
    if not files["server"] and not files["app"]:
        print(NOTE + "ключей нет — уведомления проверяются только через собственный канал приложения")
        save(result)
        return 0
    result["present"] = True
    problems = result["problems"]

    app_project = None
    if not files["app"]:
        problems.append("нет файла google-services")
    else:
        try:
            data = json.loads(Path(files["app"]).read_text(encoding="utf-8-sig"))
            app_project = (data.get("project_info") or {}).get("project_id")
            clients = [c for c in data.get("client") or []
                       if c.get("client_info", {}).get("android_client_info", {}).get("package_name") == PACKAGE]
            if not clients:
                problems.append("в google-services нет приложения %s — зарегистрируйте его в Firebase" % PACKAGE)
            else:
                key = ((clients[0].get("api_key") or [{}])[0]).get("current_key", "")
                if not key.startswith("AIza"):
                    problems.append("в google-services нет ключа API приложения")
                else:
                    print(OK + "google-services: проект %s, приложение %s" % (app_project, PACKAGE))
                    app_id = clients[0].get("client_info", {}).get("mobilesdk_app_id", "")
                    phone_side(app_project, app_id, key, problems, result)
        except ValueError as e:
            problems.append("google-services не читается: %s" % e)

    if not files["server"]:
        problems.append("нет файла google_key (ключ сервера)")
    else:
        from server.server_app.native_push import FcmSender
        sender = FcmSender(files["server"])
        if not sender.available:
            problems.append("google_key не читается: %s" % sender.error)
        else:
            server_project = sender.info["project_id"]
            if app_project and app_project != server_project:
                problems.append("файлы от разных проектов Firebase: google-services — %s, google_key — %s"
                                % (app_project, server_project))
            try:
                token = with_retries(lambda: sender.access_token(force=True))
                print(OK + "google_key: Google принял ключ, выдал токен доступа (проект %s)" % server_project)
                # пробная отправка: Google проверяет права и проект, но никому не доставляет
                url = "https://fcm.googleapis.com/v1/projects/%s/messages:send" % server_project
                body = json.dumps({"validate_only": True, "message": {"topic": "kovapp-keycheck", "data": {"check": "1"}}})
                req = urllib.request.Request(url, data=body.encode(), method="POST", headers={
                    "Authorization": "Bearer " + token, "Content-Type": "application/json"})
                try:
                    with_retries(lambda: urllib.request.urlopen(req, timeout=20).read())
                    print(OK + "FCM: отправка разрешена (пробное сообщение прошло проверку Google)")
                except urllib.error.HTTPError as e:
                    text = e.read().decode("utf-8", "replace")[:300]
                    hint = {403: "у ключа нет прав или выключен Firebase Cloud Messaging API (V1)",
                            404: "проект не найден"}.get(e.code, "")
                    problems.append("FCM отказал (код %d%s): %s" % (e.code, ", " + hint if hint else "", text))
            except urllib.error.HTTPError as e:
                problems.append("Google не принял ключ google_key (код %d): %s"
                                % (e.code, e.read().decode("utf-8", "replace")[:200]))
            except OSError as e:
                result["checked"] = False
                problems.append("нет связи с Google из Docker: %s — ключи не проверены" % e)

    for p in problems:
        print(BAD + p)
    result["valid"] = not problems
    if result["valid"]:
        print("\nКлючи в порядке: уведомления через Google будут проверены")
    elif not result["checked"]:
        print("\nКлючи не проверены: нет связи с Google. Тесты доставки покажут, доходят ли уведомления на самом деле")
    else:
        print("\nКлючи с ошибками: тесты покажут, что доставка через Google не работает")
    save(result)
    return 0 if result["valid"] else 1


def debug_cert_sha1() -> str:
    """SHA-1 сертификата отладочной подписи APK (том android-home сборки). Пусто — подпись ещё не создана."""
    path = Path("/android-home/debug.keystore")
    if not path.is_file():
        return ""
    try:
        from cryptography.hazmat.primitives.serialization import pkcs12
        from cryptography.hazmat.primitives import hashes, serialization
        _, cert, _ = pkcs12.load_key_and_certificates(path.read_bytes(), b"android")
        return cert.fingerprint(hashes.SHA1()).hex().upper() if cert else ""
    except Exception:
        return ""


def phone_side(project: str, app_id: str, api_key: str, problems: list, result: dict) -> None:
    """Первый шаг телефона к адресу доставки — регистрация в Firebase Installations ключом API приложения."""
    import base64
    import os
    raw = bytearray(os.urandom(17))
    raw[0] = 0x70 | (raw[0] & 0x0F)                      # формат FID: первые 4 бита 0111
    fid = base64.urlsafe_b64encode(bytes(raw)).decode()[:22]
    cert = debug_cert_sha1()
    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key, "X-Android-Package": PACKAGE}
    if cert:
        headers["X-Android-Cert"] = cert
    url = "https://firebaseinstallations.googleapis.com/v1/projects/%s/installations" % project
    body = json.dumps({"fid": fid, "appId": app_id, "authVersion": "FIS_v2", "sdkVersion": "a:18.0.0"}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    sha_note = (" Отпечаток отладочной подписи (SHA-1): %s" % ":".join(cert[i:i + 2] for i in range(0, len(cert), 2))) if cert else ""
    try:
        reply = json.loads(with_retries(lambda: urllib.request.urlopen(req, timeout=20).read()) or b"{}")
        print(OK + "телефон: ключ API приложения принят (Firebase Installations), адрес доставки выдаётся")
        try:   # пробную установку убираем, чтобы не копились
            token = (reply.get("refreshToken") or "")
            if token:
                urllib.request.urlopen(urllib.request.Request(url + "/" + reply.get("fid", fid), method="DELETE", headers={
                    "x-goog-api-key": api_key, "Authorization": "FIS_v2 " + token}), timeout=10).read()
        except Exception:
            pass
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")
        if "blocked" in text and "Android client application" in text:
            hint = ("ключ API приложения ограничен приложениями Android, и отладочная подпись в разрешённых не значится. "
                    "Добавьте SHA-1 ниже в Firebase: «Настройки проекта → Ваши приложения → %s → Добавить отпечаток», "
                    "или в Google Cloud Console → «API и сервисы → Учётные данные» → этот ключ → приложения Android." % PACKAGE)
        elif "has not been used" in text or "is disabled" in text or "SERVICE_DISABLED" in text:
            hint = "в проекте выключен Firebase Installations API — включите его в Google Cloud Console (API и сервисы)"
        elif "API key not valid" in text or "API_KEY_INVALID" in text:
            hint = "ключ API приложения недействителен — скачайте google-services.json заново"
        elif "are blocked" in text or "API_KEY_SERVICE_BLOCKED" in text:
            hint = ("ключу API приложения запрещён Firebase Installations API — в Google Cloud Console → «Учётные данные» "
                    "→ этот ключ → «Ограничения API» добавьте Firebase Installations API и FCM Registration API")
        else:
            hint = "Google отказал"
        problems.append("телефон не получит адрес доставки (код %d): %s. Ответ Google: %s.%s" % (e.code, hint, text[:240], sha_note))
    except OSError as e:
        result["checked"] = False
        problems.append("нет связи с Google из Docker при проверке стороны телефона: %s" % e)


def save(result: dict) -> None:
    try:
        Path("/shared").mkdir(exist_ok=True)
        Path("/shared/keycheck.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(main())
