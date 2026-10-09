#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Автоматическая проверка безопасности приложения Kovapp и серверного модуля.

Проверяет то, что можно проверить по исходникам без телефона: манифест,
настройки сборки, код WebView и моста, хранение секретов, надстройку JS и
серверный модуль уведомлений. Подробное объяснение каждого правила —
docs/mobile-security.md.

    python mobile/tools/security_check.py
"""

import json
import os
import re
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
MOBILE = os.path.dirname(HERE)
ROOT = os.path.dirname(MOBILE)
APP = os.path.join(MOBILE, "app")
JAVA = os.path.join(APP, "src")
A = "{http://schemas.android.com/apk/res/android}"
RESULTS = []


def check(rule, ok, detail=""):
    RESULTS.append((rule, bool(ok), detail))
    print(("  OK   " if ok else "  FAIL ") + rule + ("" if ok else "  — " + str(detail)[:240]))


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def java_sources():
    out = {}
    for base, _, files in os.walk(JAVA):
        if os.sep + "test" + os.sep in base:
            continue
        for f in files:
            if f.endswith(".java"):
                p = os.path.join(base, f)
                out[os.path.relpath(p, MOBILE)] = read(p)
    return out


def grep(sources, pattern):
    rx = re.compile(pattern)
    hits = []
    for path, text in sources.items():
        for i, line in enumerate(text.split("\n"), 1):
            if rx.search(line) and not line.strip().startswith(("//", "*")):
                hits.append("%s:%d" % (path, i))
    return hits


def main():
    print("Проверка безопасности Kovapp")
    src = java_sources()
    manifest = ET.parse(os.path.join(JAVA, "main", "AndroidManifest.xml")).getroot()
    app = manifest.find("application")
    gradle = read(os.path.join(APP, "build.gradle"))

    # ── манифест ──
    check("резервное копирование выключено (allowBackup=false)", app.get(A + "allowBackup") == "false")
    rules = read(os.path.join(JAVA, "main", "res", "xml", "data_extraction_rules.xml"))
    check("при переносе на новый телефон и в облако ничего не копируется",
          all(rules.count('<exclude domain="%s" />' % d) == 2 for d in ("root", "file", "database", "sharedpref", "external")))
    check("режим отладки не включён в манифесте (android:debuggable)", app.get(A + "debuggable") is None)
    exported = []
    for tag in ("activity", "service", "receiver", "provider"):
        for c in app.findall(tag):
            if c.get(A + "exported") == "true":
                exported.append(c.get(A + "name"))
    check("снаружи доступен только значок запуска (MainActivity)", exported == [".MainActivity"], exported)
    main_act = next(c for c in app.findall("activity") if c.get(A + "name") == ".MainActivity")
    actions = [a.get(A + "name") for f in main_act.findall("intent-filter") for a in f.findall("action")]
    check("главное окно принимает только запуск с рабочего стола", actions == ["android.intent.action.MAIN"], actions)
    provider = app.find("provider")
    check("файлы камеры: провайдер закрыт, доступ только по разовому разрешению",
          provider.get(A + "exported") == "false" and provider.get(A + "grantUriPermissions") == "true")
    perms = sorted(p.get(A + "name").split(".")[-1] for p in manifest.findall("uses-permission"))
    # камера, микрофон и галерея — для шторки вложений (живая камера, съёмка, последние фото и видео);
    # контакты, геопозиция и прочее — по-прежнему нет
    allowed = sorted(["INTERNET", "ACCESS_NETWORK_STATE", "POST_NOTIFICATIONS", "FOREGROUND_SERVICE",
                      "FOREGROUND_SERVICE_REMOTE_MESSAGING", "FOREGROUND_SERVICE_DATA_SYNC", "RECEIVE_BOOT_COMPLETED", "WAKE_LOCK",
                      "REQUEST_IGNORE_BATTERY_OPTIMIZATIONS", "WRITE_EXTERNAL_STORAGE",
                      "CAMERA", "RECORD_AUDIO", "READ_MEDIA_IMAGES", "READ_MEDIA_VIDEO",
                      "READ_MEDIA_VISUAL_USER_SELECTED", "READ_EXTERNAL_STORAGE"])
    check("разрешения — только нужные (камера, микрофон, галерея — для вложений; без контактов и геопозиции)",
          perms == allowed, perms)
    res = next((p for p in manifest.findall("uses-permission") if p.get(A + "name").endswith("READ_EXTERNAL_STORAGE")), None)
    check("чтение памяти — только на Android до 12 (дальше — отдельно фото и видео)",
          res is not None and res.get(A + "maxSdkVersion") == "32", res.attrib if res is not None else None)
    act = open(os.path.join(JAVA, "main", "java", "ru", "kovcheg", "kovapp", "MainActivity.java"), encoding="utf-8").read()
    chrome = open(os.path.join(JAVA, "main", "java", "ru", "kovcheg", "kovapp", "web", "AppChromeClient.java"), encoding="utf-8").read()
    check("камера и микрофон — только своей странице, только эти ресурсы и после разрешения Android",
          "host.permission(request)" in chrome and "equalsIgnoreCase(AppConfig.origin())" in act
          and '"android.webkit.resource.VIDEO_CAPTURE"' in act and "req::deny" in act
          and "requestCameraPermission(audio" in act, "проверьте MainActivity.permission()")
    wes = next(p for p in manifest.findall("uses-permission") if p.get(A + "name").endswith("WRITE_EXTERNAL_STORAGE"))
    check("запись в «Загрузки» — только на Android 9 и старше", wes.get(A + "maxSdkVersion") == "28")

    # ── сеть ──
    check("рабочая сборка только по https (http — сборка остановится)", "!serverOrigin.startsWith('https://')" in gradle
          and "throw new GradleException('Рабочая сборка работает только по https" in gradle)
    check("базовое правило сети: без http, только системные сертификаты",
          '<base-config cleartextTrafficPermitted="false">' in gradle and '<certificates src="system"/>' in gradle)
    nsc_debug = gradle[gradle.index("if (debug) ['localhost'"):gradle.index("if (uri.scheme == 'http')")]
    check("http разрешён только отладочной сборке и только к localhost/эмулятору",
          "'localhost', '127.0.0.1', '10.0.2.2'" in nsc_debug)
    user_ca = gradle[gradle.index('<certificates src="user"/>') - 250:gradle.index('<certificates src="user"/>')]
    check("пользовательские сертификаты — только в debug-overrides отладочной сборки", "if (debug)" in user_ca and "debug-overrides" in user_ca)

    # ── WebView и мост ──
    check("нет addJavascriptInterface (страница не видит Java-объекты)", not grep(src, r"addJavascriptInterface"), grep(src, r"addJavascriptInterface"))
    check("ошибка сертификата никогда не пропускается (нет proceed)", not grep(src, r"\.proceed\(") and grep(src, r"handler\.cancel\(\)"))
    # своя «доверчивая» проверка сертификатов — частая уязвимость Android-приложений;
    # вызов системной проверки имени (getDefaultHostnameVerifier().verify) её, наоборот, усиливает
    weak = [h for h in grep(src, r"X509TrustManager|HostnameVerifier|ALLOW_ALL_HOSTNAME|setDefaultHostnameVerifier|checkServerTrusted")
            if "getDefaultHostnameVerifier().verify(" not in src[h.split(":")[0]].split("\n")[int(h.split(":")[1]) - 1]]
    check("нет своих TrustManager/HostnameVerifier (проверка сертификатов не ослаблена)", not weak, weak)
    ws_client = read(os.path.join(JAVA, "main", "java", "ru", "kovcheg", "kovapp", "net", "WebSocketClient.java"))
    check("канал уведомлений (wss) сверяет имя сервера с сертификатом", "getDefaultHostnameVerifier().verify(host" in ws_client)
    ma = src[os.path.join("app", "src", "main", "java", "ru", "kovcheg", "kovapp", "MainActivity.java")]
    for setting in ("setAllowFileAccess(false)", "setAllowFileAccessFromFileURLs(false)", "setAllowUniversalAccessFromFileURLs(false)",
                    "MIXED_CONTENT_NEVER_ALLOW", "setWebContentsDebuggingEnabled(BuildConfig.DEBUG)", "setGeolocationEnabled(false)"):
        check("WebView: " + setting, setting in ma)
    bridge = read(os.path.join(JAVA, "main", "java", "ru", "kovcheg", "kovapp", "web", "Bridge.java"))
    check("мост принимает сообщения только главной страницы своего сервера",
          "Collections.singleton(AppConfig.origin())" in bridge and "!isMainFrame" in bridge and "AppConfig.NAV.sameOrigin(sourceOrigin" in bridge)
    cmds = read(os.path.join(JAVA, "main", "java", "ru", "kovcheg", "kovapp", "NativeCommands.java"))
    check("токен отдаётся только своему серверу; скачивание — только со своего сервера",
          cmds.count("AppConfig.ownServer(") >= 3)
    check("к файлам телефона — только выбранные пользователем (метки 128 бит)", 'HANDLE = "[0-9a-f]{32}"' in cmds and cmds.count("checkMatches(handle, HANDLE") >= 4)
    check("внешние ссылки — только http/https/mailto/tel", 'scheme.equals("http") && !scheme.equals("https") && !scheme.equals("mailto") && !scheme.equals("tel")' in ma)
    check("адрес из уведомления проверяется (только маршруты #/…)", "NavigationPolicy.isSafeRoute(url)" in ma)
    check("PendingIntent неизменяемые (FLAG_IMMUTABLE)", all("FLAG_IMMUTABLE" in src[p] for p in src if "PendingIntent.get" in src[p]))
    cap = read(os.path.join(JAVA, "main", "java", "ru", "kovcheg", "kovapp", "files", "CaptureProvider.java"))
    check("провайдер снимков защищён от выхода из папки", "getCanonicalPath()" in cap and '[A-Za-z0-9_.-]{1,100}' in cap)
    asset = read(os.path.join(JAVA, "main", "java", "ru", "kovcheg", "kovapp", "web", "AssetServer.java"))
    check("страница не читает content:// и file://", 'scheme.equals("content") || scheme.equals("file")' in asset)
    secure = read(os.path.join(JAVA, "main", "java", "ru", "kovcheg", "kovapp", "push", "SecureStore.java"))
    check("секреты устройства шифруются ключом Android Keystore (AES-GCM)", "AndroidKeyStore" in secure and "AES/GCM/NoPadding" in secure)
    logs = [h for h in grep(src, r"Log\.[dviwe]\(") if re.search(r"(token|secret|payload_key|password)\b", src[h.split(":")[0]].split("\n")[int(h.split(":")[1]) - 1], re.I)]
    check("в журнал не пишутся токены и секреты", not logs, logs)
    check("R8 удаляет отладочный вывод из рабочей сборки", "-assumenosideeffects class android.util.Log" in read(os.path.join(APP, "proguard-rules.pro")))

    # ── надстройка JS ──
    overlay = read(os.path.join(MOBILE, "overlay", "native", "kovapp-app.js")) + read(os.path.join(MOBILE, "overlay", "native", "kovapp-bridge.js"))
    check("надстройка не выполняет строки как код (eval, new Function, innerHTML)", not re.search(r"\beval\(|new Function\(|\.innerHTML\s*=", overlay))
    check("переход по уведомлению — только по маршрутам #/…", "^#\\/[A-Za-z0-9_\\/-]{0,200}$" in overlay)
    web_leak = []
    for base, _, files in os.walk(os.path.join(ROOT, "web_app")):
        for f in files:
            if f.endswith((".js", ".html")) and "KovNative" in read(os.path.join(base, f)):
                web_leak.append(f)
    check("веб-версия не содержит кода приложения (всё подключается только в APK)", not web_leak, web_leak)

    # ── сервер ──
    np_src = read(os.path.join(ROOT, "server", "server_app", "native_push.py"))
    router = read(os.path.join(ROOT, "server", "server_app", "routers", "native_push.py"))
    check("сервер хранит только хеш секрета и сравнивает за постоянное время", "hash_secret(secret)" in np_src and "hmac.compare_digest" in np_src)
    check("через Google уходит только шифртекст (AES-256-GCM, только data)", '"data": data' in np_src and "encrypt_payload(device" in np_src and '"notification"' not in np_src)
    check("ключ склейки для Google — хеш метки с ключом телефона (номер чата не виден)",
          "private_topic(device[\"payload_key\"]" in np_src and "hmac.new(" in np_src)
    check("регистрация и отвязка — только после входа", router.count("Depends(get_current_user_id)") >= 3)
    check("номер устройства проверяется (32 шестнадцатеричных знака)", 'DEVICE_ID_RE = re.compile(r"[0-9a-f]{32}")' in np_src)
    check("очередь ограничена (память сервера не переполнить)", "while len(q) > self.queue_limit" in np_src)
    check("канал ждёт вход не дольше 10 с и закрывает чужих", "HELLO_TIMEOUT = 10" in router and '"reason": "unauthorized"' in router)
    assets_task = gradle[gradle.index("abstract class KovappAssetsTask"):gradle.index("abstract class KovappNetworkTask")]
    check("инструменты для ПК (симулятор, эмулятор моста) в APK не попадают",
          "devbridge" not in assets_task and "simulator" not in assets_task and "'tools" not in assets_task)
    deps = re.findall(r"(?:implementation|Implementation)\s+(?:platform\()?'([^']+)'", gradle)
    check("версии библиотек зафиксированы (без «+»)", deps and all("+" not in d for d in deps), deps)

    bad = [r for r in RESULTS if not r[1]]
    print("Итого: %d правил, нарушений: %d" % (len(RESULTS), len(bad)))
    out = os.path.join(MOBILE, "tests", "ui", "report", "security.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump([{"rule": r, "ok": ok, "detail": d} for r, ok, d in RESULTS], f, ensure_ascii=False, indent=1)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
