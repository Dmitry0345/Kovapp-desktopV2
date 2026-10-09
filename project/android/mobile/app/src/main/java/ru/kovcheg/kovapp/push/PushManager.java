package ru.kovcheg.kovapp.push;

import android.content.Context;
import android.os.Build;
import android.os.PowerManager;
import android.util.Log;

import java.security.GeneralSecurityException;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;

import ru.kovcheg.kovapp.App;
import ru.kovcheg.kovapp.BuildConfig;
import ru.kovcheg.kovapp.core.B64;
import ru.kovcheg.kovapp.core.Json;
import ru.kovcheg.kovapp.net.HttpJson;
import ru.kovcheg.kovapp.notify.Notifier;

/**
 * Уведомления при закрытом приложении. Два пути доставки:
 *   fcm    — через сервисы Google (телефоны с Google Play);
 *   direct — собственное постоянное соединение с сервером (Huawei, Honor и
 *            другие телефоны без сервисов Google).
 * Устройство привязано к одному пользователю; при выходе привязка снимается.
 * Методы enable/disable/sync блокирующие — вызываются из фонового потока.
 */
public final class PushManager {
    private static final String TAG = "KovappPush";

    private final Context ctx;
    private final DeviceStore store;
    private volatile String host, token;
    private volatile long userId;
    private volatile Boolean serverSupported;
    private volatile String serverError;   // почему не удалось спросить сервер (не «модуля нет»)
    private volatile boolean serverFcm;
    private volatile String fcmError;      // почему Google не выдал адрес доставки (последняя попытка)
    private volatile boolean fcmRetryPlanned;
    private volatile int ping = 240;
    private final Set<String> recent = Collections.newSetFromMap(new LinkedHashMap<String, Boolean>() {
        @Override protected boolean removeEldestEntry(Map.Entry<String, Boolean> e) { return size() > 300; }
    });

    public PushManager(Context ctx) {
        this.ctx = ctx.getApplicationContext();
        this.store = new DeviceStore(this.ctx);
    }

    public DeviceStore store() { return store; }

    public synchronized void setSession(String host, String token, long userId) {
        boolean changed = token == null || !token.equals(this.token) || host == null || !host.equals(this.host);
        this.host = host;
        this.token = token;
        this.userId = userId;
        if (changed) {             // новый вход или сервер — прежний ответ о модуле больше не в силе
            serverSupported = null;
            serverError = null;
        }
    }

    /** Состояние для страницы; ответ сервера о модуле уточняется, если его ещё нет (не в потоке UI) */
    public Map<String, Object> stateFresh() {
        if (serverSupported == null && host != null && token != null && !token.isEmpty()) loadServerConfig();
        return state();
    }

    public long sessionUser() { return userId; }

    // ── состояние для страницы ───────────────────────────────────────────
    public Map<String, Object> state() {
        DeviceStore.Creds c = store.creds();
        boolean registered = c != null && userId > 0 && c.userId == userId;
        return Json.map(
                "enabled", store.enabled(),
                "registered", registered,
                "mode", registered ? c.mode : "",
                "serverSupported", serverSupported,
                "serverError", serverError,
                "permission", permission(),
                "battery", batteryExempt(),
                "fcmPossible", FcmSupport.available(ctx) && ru.kovcheg.kovapp.AppConfig.fcmConfigured(),
                // почему выбран собственный канал: нет Google, нет Firebase в приложении или на сервере
                "gms", FcmSupport.googleServices(ctx),
                "fcmApp", ru.kovcheg.kovapp.AppConfig.fcmConfigured(),
                "fcmServer", serverFcm,
                "fcmError", fcmError,
                "fcmExpected", fcmExpected(),
                "serviceHidden", ru.kovcheg.kovapp.notify.Notifier.serviceHidden(ctx),
                "vendor", Build.MANUFACTURER == null ? "" : Build.MANUFACTURER.toLowerCase(java.util.Locale.ROOT));
    }

    public String permission() {
        if (Notifier.enabled(ctx)) return "granted";
        if (Build.VERSION.SDK_INT >= 33 && !store.askedPermission()) return "default";
        return "denied";
    }

    /** Доставка через Google возможна на этом телефоне: сервисы Google, ключи приложения и сервера.
     *  Тогда собственный канал (и значок «Kovapp на связи») — лишь временный, пока не пришёл токен. */
    public boolean fcmExpected() {
        // Google на этом телефоне отказал (а не просто долго отвечал) — собственный канал постоянный:
        // показываем, как убрать значок, и просим работу в фоне
        if (fcmError != null && !fcmRetryPlanned) return false;
        return serverFcm && ru.kovcheg.kovapp.AppConfig.fcmConfigured() && FcmSupport.available(ctx);
    }

    public boolean batteryExempt() {
        PowerManager pm = (PowerManager) ctx.getSystemService(Context.POWER_SERVICE);
        return pm != null && pm.isIgnoringBatteryOptimizations(ctx.getPackageName());
    }

    // ── настройка сервера ────────────────────────────────────────────────
    /** Знает ли сервер про уведомления для приложения (модуль native_push) */
    public boolean loadServerConfig() {
        if (host == null || token == null || token.isEmpty()) return false;
        try {
            HttpJson.Response r = HttpJson.call("GET", HttpJson.join(host, "push/native/config"), token, null);
            if (r.ok()) {
                boolean ok = Json.bool(r.body, "enabled", false);
                serverSupported = ok;
                serverError = null;
                serverFcm = ok && Json.bool(r.body, "fcm", false);
                ping = (int) Math.max(30, Math.min(900, Json.num(r.body, "ping", 240)));
                return ok;
            }
            // «модуля нет» — только если сервер сам так ответил; вход и сбои — другое
            if (r.status == 404) {
                serverSupported = false;
                serverError = null;
            } else {
                serverError = (r.status == 401 || r.status == 403)
                        ? "сервер не принял вход — выйдите и войдите заново"
                        : "сервер ответил кодом " + r.status;
            }
            return false;
        } catch (Exception e) {
            Log.w(TAG, "config: " + e.getMessage());
            serverError = "нет связи с сервером";
            return false;
        }
    }

    public int ping() { return ping; }

    // ── включение и выключение ───────────────────────────────────────────
    public Map<String, Object> enable() {
        if (!Notifier.enabled(ctx)) {
            return Json.map("ok", false, "push", false, "reason", "Уведомления запрещены в настройках телефона для приложения");
        }
        store.setEnabled(true);
        return register(true);
    }

    public Map<String, Object> sync() {
        if (!store.enabled() || !Notifier.enabled(ctx) || userId <= 0) return Json.map("ok", false);
        return register(false);
    }

    private synchronized Map<String, Object> register(boolean interactive) {
        if (!loadServerConfig()) {
            if (serverSupported == null) {
                return Json.map("ok", false, "push", false, "reason",
                        "Не удалось проверить уведомления на сервере: " + serverError + ". Попробуйте ещё раз.");
            }
            return Json.map("ok", true, "push", false, "reason",
                    "Уведомления приходят, пока приложение открыто или свёрнуто. Для уведомлений при закрытом приложении обновите сервер (модуль native_push)");
        }
        String mode = "direct", fcmToken = null;
        if (serverFcm && ru.kovcheg.kovapp.AppConfig.fcmConfigured() && FcmSupport.available(ctx)) {
            try {
                fcmToken = FcmSupport.token(ctx, 45000);
                if (fcmToken != null && !fcmToken.isEmpty()) { mode = "fcm"; fcmError = null; }
                else fcmError = "Google вернул пустой адрес";
            } catch (Exception e) {
                Throwable root = e;
                while (root.getCause() != null && root.getCause() != root) root = root.getCause();
                String msg = root.getMessage();
                // «java.io.IOException: SERVICE_NOT_AVAILABLE» → «SERVICE_NOT_AVAILABLE»
                if (msg != null && msg.lastIndexOf(": ") >= 0) msg = msg.substring(msg.lastIndexOf(": ") + 2);
                fcmError = msg != null && !msg.isEmpty() ? msg : root.getClass().getSimpleName();
                Log.w(TAG, "FCM недоступен, собственный канал: " + fcmError);
                // Google просто долго отвечал (первый запуск): адрес доставки досчитается в фоне —
                // через минуту пробуем ещё раз и переходим на Google без перезапуска приложения
                if (root instanceof java.util.concurrent.TimeoutException && !fcmRetryPlanned) {
                    fcmRetryPlanned = true;
                    new Thread(() -> {
                        try { Thread.sleep(60000); } catch (InterruptedException ie) { return; }
                        fcmRetryPlanned = false;
                        if (store.enabled() && userId > 0) register(false);
                    }, "kovapp-fcm-retry").start();
                }
            }
        }
        try {
            Map<String, Object> body = Json.map("device_id", store.deviceId(), "provider", mode, "platform", "android",
                    "model", (Build.MANUFACTURER + " " + Build.MODEL).trim(), "app_version", BuildConfig.VERSION_NAME);
            if (fcmToken != null) body.put("fcm_token", fcmToken);
            HttpJson.Response r = HttpJson.call("POST", HttpJson.join(host, "push/native/register"), token, body);
            if (!r.ok()) {
                return Json.map("ok", true, "push", false, "reason", "Сервер не принял регистрацию устройства: " + Json.str(r.body, "detail", "код " + r.status));
            }
            String secret = Json.str(r.body, "secret", null), key = Json.str(r.body, "payload_key", null);
            if (secret == null || key == null) return Json.map("ok", true, "push", false, "reason", "Сервер вернул неполный ответ");
            // сервер решает, каким путём доставлять: без настроенного FCM — собственный канал
            String provider = Json.str(r.body, "provider", mode);
            if ("direct".equals(provider) && "fcm".equals(mode)) {
                try { FcmSupport.deleteToken(ctx); } catch (Exception e) { /* не страшно */ }
                fcmToken = null;
            }
            mode = "fcm".equals(provider) ? "fcm" : "direct";
            store.saveRegistration(host, userId, mode, secret, B64.decode(key));
            if (fcmToken != null) store.setFcmToken(fcmToken);
            ping = (int) Math.max(30, Math.min(900, Json.num(r.body, "ping", ping)));
            startChannel(mode);
            return Json.map("ok", true, "push", true, "mode", mode);
        } catch (Exception e) {
            return Json.map("ok", true, "push", false, "reason", "Нет связи с сервером: " + e.getMessage());
        }
    }

    public synchronized Map<String, Object> disable() {
        store.setEnabled(false);
        DeviceStore.Creds c = store.creds();
        if (c != null && token != null && !token.isEmpty()) {
            try {
                HttpJson.call("DELETE", HttpJson.join(c.host, "push/native/register"), token, Json.map("device_id", c.deviceId));
            } catch (Exception e) {
                Log.w(TAG, "unregister: " + e.getMessage());
            }
        }
        stopChannels();
        store.clearCredentials();
        return Json.map("ok", true);
    }

    /** Выход из учётной записи: устройство больше не получает уведомления этого пользователя */
    public void logout(final String oldHost, final String oldToken) {
        final DeviceStore.Creds c = store.creds();
        stopChannels();
        store.clearCredentials();
        synchronized (this) { token = null; userId = 0; }
        Notifier.cancelAll(ctx);
        if (c == null) return;
        App.get().io(() -> {
            try {
                if (oldToken != null && !oldToken.isEmpty()) {
                    HttpJson.call("DELETE", HttpJson.join(oldHost != null ? oldHost : c.host, "push/native/register"), oldToken, Json.map("device_id", c.deviceId));
                } else {
                    HttpJson.call("POST", HttpJson.join(c.host, "push/native/unregister"), null, Json.map("device_id", c.deviceId, "secret", c.secret));
                }
            } catch (Exception e) {
                Log.w(TAG, "logout unregister: " + e.getMessage());
            }
        });
    }

    void startChannel(String mode) {
        if ("direct".equals(mode)) {
            try { DirectPushService.start(ctx); } catch (Exception e) { Log.w(TAG, "служба не запущена: " + e.getMessage()); }
            CatchUpJob.schedule(ctx);
        } else {
            DirectPushService.stop(ctx);
            CatchUpJob.cancel(ctx);
        }
    }

    void stopChannels() {
        DirectPushService.stop(ctx);
        CatchUpJob.cancel(ctx);
        try { FcmSupport.deleteToken(ctx); } catch (Exception e) { /* нет Google — нечего удалять */ }
    }

    /** Запуск процесса: вернуть собственный канал, если он был включён */
    public void onAppStart() {
        // токен Google запрашиваем сразу при запуске: к включению уведомлений он уже готов,
        // и приложение выбирает Google без промежуточного собственного канала
        if (ru.kovcheg.kovapp.AppConfig.fcmConfigured() && FcmSupport.available(ctx)) {
            App.get().io(() -> {
                try {
                    FcmSupport.token(ctx, 120000);
                } catch (Exception e) {
                    Log.i(TAG, "адрес Google для уведомлений пока не получен");
                }
            });
        }
        DeviceStore.Creds c = store.creds();
        if (c != null && store.enabled() && "direct".equals(c.mode)) {
            try { DirectPushService.start(ctx); } catch (Exception e) { /* из фона нельзя — поможет периодическая задача */ }
            CatchUpJob.schedule(ctx);
        }
    }

    // ── обновился токен FCM ──────────────────────────────────────────────
    public void onFcmToken(final String fcmToken) {
        final DeviceStore.Creds c = store.creds();
        if (c == null || fcmToken == null) return;
        // токен Google пришёл позже регистрации (у только что установленного приложения он
        // выдаётся не сразу) — переходим с собственного канала на Google
        if ("direct".equals(c.mode)) {
            App.get().io(() -> {
                try { sync(); } catch (Exception e) { Log.w(TAG, "переход на Google не удался: " + e.getMessage()); }
            });
            return;
        }
        if (!"fcm".equals(c.mode)) return;
        App.get().io(() -> {
            try {
                HttpJson.Response r = HttpJson.call("POST", HttpJson.join(c.host, "push/native/token"), null,
                        Json.map("device_id", c.deviceId, "secret", c.secret, "fcm_token", fcmToken));
                if (r.ok()) store.setFcmToken(fcmToken);
            } catch (Exception e) {
                Log.w(TAG, "новый адрес FCM не отправлен на сервер: " + e.getMessage());
            }
        });
    }

    // ── входящее уведомление ─────────────────────────────────────────────
    public void handleFcm(Map<String, String> data) {
        DeviceStore.Creds c = store.creds();
        if (c == null) return;
        try {
            handle(PushCrypto.open(c.payloadKey, data), "fcm");
        } catch (GeneralSecurityException e) {
            Log.w(TAG, "не расшифровано: " + e.getMessage());
        } catch (RuntimeException e) {
            // сообщение не того вида не должно закрывать приложение
            Log.w(TAG, "уведомление Google не обработано: " + e);
        }
    }

    public void handle(String json, String source) {
        DeviceStore.Creds c = store.creds();
        if (c == null) return;
        PushMessage m;
        try {
            m = PushMessage.parse(json);
        } catch (IllegalArgumentException e) {
            return;
        }
        // уведомление другому сотруднику (устройство успело сменить владельца) — не показываем
        if (m.uid != c.userId) return;
        if (!m.id.isEmpty()) {
            synchronized (recent) {
                if (recent.contains(m.id)) return;
                recent.add(m.id);
            }
        }
        if (!m.probe.isEmpty()) ackProbe(c, m.probe);
        App app = App.get();
        if (app.pageVisible()) {
            // как service worker: открытое приложение покажет всплывашку само
            Map<String, Object> payload = new LinkedHashMap<>(m.raw);
            payload.remove("uid");
            app.emitToPage("push", Json.map("payload", payload));
            return;
        }
        Notifier.show(ctx, m.tag, m.title, m.body, m.url, m.channel());
    }

    private void ackProbe(final DeviceStore.Creds c, final String probe) {
        App.get().io(() -> {
            try {
                HttpJson.call("POST", HttpJson.join(c.host, "push/ack"), null, Json.map("probe", probe, "endpoint", "native:" + c.deviceId));
            } catch (Exception e) { /* проверка покажет, что подтверждения нет */ }
        });
    }
}
