package ru.kovcheg.kovapp.push;

import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.NetworkRequest;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;
import android.security.NetworkSecurityPolicy;
import android.util.Log;

import java.io.IOException;
import java.net.URI;
import java.util.Collections;
import java.util.Map;
import java.util.Random;

import ru.kovcheg.kovapp.App;
import ru.kovcheg.kovapp.AppConfig;
import ru.kovcheg.kovapp.BuildConfig;
import ru.kovcheg.kovapp.core.Json;
import ru.kovcheg.kovapp.net.WebSocketClient;
import ru.kovcheg.kovapp.notify.Notifier;

/**
 * Собственный канал уведомлений для телефонов без сервисов Google: служба
 * держит одно WebSocket-соединение с сервером (/ws/native-push). Сервер
 * шлёт ping раз в несколько минут — это поддерживает соединение через
 * роутеры и позволяет быстро заметить обрыв.
 */
public final class DirectPushService extends Service {
    private static final String TAG = "KovappDirect";
    private boolean notStarted;
    private volatile boolean running;
    private Thread worker;
    private volatile WebSocketClient socket;
    private volatile long connectedAt;
    private final Object netLock = new Object();
    private ConnectivityManager.NetworkCallback netCallback;

    static final class Unauthorized extends IOException {
        Unauthorized(String m) { super(m); }
    }

    // запущена через startForegroundService, но ещё не стала постоянной: останавливать её в это
    // время нельзя — Android закроет приложение («did not then call startForeground»)
    private static volatile boolean pendingForeground;
    private static volatile boolean stopRequested;

    public static void start(Context c) {
        stopRequested = false;
        Intent i = new Intent(c, DirectPushService.class);
        // приложение открыто — обычный запуск: без 10-секундного срока startForegroundService
        // (при долгом запуске WebView он истекал бы и Android закрывал приложение)
        if (Build.VERSION.SDK_INT >= 26 && !ru.kovcheg.kovapp.App.get().pageVisible()) {
            pendingForeground = true;
            c.startForegroundService(i);
        } else {
            c.startService(i);
        }
    }

    public static void stop(Context c) {
        if (pendingForeground) { stopRequested = true; return; }   // остановится сама, став постоянной
        c.stopService(new Intent(c, DirectPushService.class));
    }

    @Override public IBinder onBind(Intent intent) { return null; }

    @Override public void onCreate() {
        super.onCreate();
        try {
            if (Build.VERSION.SDK_INT >= 34) {
                startForeground(Notifier.SERVICE_ID, Notifier.service(this), ServiceInfo.FOREGROUND_SERVICE_TYPE_REMOTE_MESSAGING);
            } else {
                startForeground(Notifier.SERVICE_ID, Notifier.service(this));
            }
            pendingForeground = false;
            if (stopRequested) {          // пока запускалась, канал сменился на Google
                stopRequested = false;
                notStarted = true;
                stopSelf();
                return;
            }
        } catch (RuntimeException e) {
            pendingForeground = false;
            // приложение ушло в фон раньше, чем служба успела стать постоянной: поднимем при следующем открытии
            Log.w(TAG, "не удалось стать постоянной службой: " + e.getMessage());
            CatchUpJob.schedule(this);
            notStarted = true;
            stopSelf();
            return;
        }
        ConnectivityManager cm = (ConnectivityManager) getSystemService(Context.CONNECTIVITY_SERVICE);
        netCallback = new ConnectivityManager.NetworkCallback() {
            @Override public void onAvailable(Network network) {
                synchronized (netLock) { netLock.notifyAll(); }
                // сменилась сеть (Wi-Fi ↔ мобильная): старое соединение может молча висеть — переподключаемся
                WebSocketClient s = socket;
                if (s != null && System.currentTimeMillis() - connectedAt > 5000) s.close();
            }
        };
        try {
            cm.registerNetworkCallback(new NetworkRequest.Builder().addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET).build(), netCallback);
        } catch (RuntimeException e) {
            netCallback = null;
        }
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (notStarted) return START_NOT_STICKY;   // не стали постоянной службой — соединение не открываем
        if (!running) {
            running = true;
            worker = new Thread(this::loop, "kovapp-direct");
            worker.start();
        }
        return START_STICKY;
    }

    @Override public void onDestroy() {
        running = false;
        WebSocketClient s = socket;
        if (s != null) s.close();
        if (worker != null) worker.interrupt();
        if (netCallback != null) {
            try { ((ConnectivityManager) getSystemService(Context.CONNECTIVITY_SERVICE)).unregisterNetworkCallback(netCallback); } catch (RuntimeException ignore) { /* */ }
        }
        super.onDestroy();
    }

    private boolean online() {
        ConnectivityManager cm = (ConnectivityManager) getSystemService(Context.CONNECTIVITY_SERVICE);
        Network n = cm == null ? null : cm.getActiveNetwork();
        NetworkCapabilities caps = n == null ? null : cm.getNetworkCapabilities(n);
        return caps != null && caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET);
    }

    private void loop() {
        long backoff = 1000;
        Random rnd = new Random();
        PushManager pm = App.get().push();
        while (running) {
            DeviceStore.Creds c = pm.store().creds();
            if (c == null || !"direct".equals(c.mode) || !pm.store().enabled()) { stopSelf(); return; }
            if (!online()) {
                synchronized (netLock) { try { netLock.wait(60000); } catch (InterruptedException e) { return; } }
                continue;
            }
            try {
                session(c, false, pm, this);
                backoff = 1000;
            } catch (Unauthorized u) {
                Log.w(TAG, "сервер не узнал устройство: " + u.getMessage());
                pm.store().clearCredentials();
                stopSelf();
                return;
            } catch (Exception e) {
                if (!running) return;
                Log.i(TAG, "обрыв: " + e.getMessage());
            }
            try {
                Thread.sleep(backoff + rnd.nextInt(1000));
            } catch (InterruptedException e) {
                return;
            }
            backoff = Math.min(backoff * 2, 300_000);
        }
    }

    static String wsUrl(String host) throws IOException {
        URI u = URI.create(host);
        String scheme = "https".equalsIgnoreCase(u.getScheme()) ? "wss" : "ws";
        if (scheme.equals("ws") && !NetworkSecurityPolicy.getInstance().isCleartextTrafficPermitted(u.getHost())) {
            throw new IOException("HTTP к этому адресу запрещён настройками безопасности");
        }
        String port = u.getPort() > 0 ? ":" + u.getPort() : "";
        String base = u.getRawPath() == null ? "" : u.getRawPath().replaceAll("/+$", "");
        return scheme + "://" + u.getHost() + port + base + "/ws/native-push";
    }

    /** Одно соединение. once — забрать накопленное и закрыть (для фоновой задачи). */
    static void session(DeviceStore.Creds c, boolean once, PushManager pm, DirectPushService owner) throws IOException {
        WebSocketClient ws = WebSocketClient.connect(wsUrl(c.host),
                Collections.singletonMap("User-Agent", "KovappAndroid/" + BuildConfig.VERSION_NAME), 15000, 60000);
        try {
            ws.sendText(Json.stringify(Json.map("type", "hello", "device_id", c.deviceId, "secret", c.secret,
                    "app_version", BuildConfig.VERSION_NAME, "once", once)));
            String first = ws.receive();
            if (first == null) throw new IOException("сервер закрыл соединение");
            Map<String, Object> w = Json.parseObject(first);
            if ("error".equals(Json.str(w, "type", ""))) {
                String reason = Json.str(w, "reason", "");
                if ("unauthorized".equals(reason)) throw new Unauthorized(reason);
                throw new IOException(reason);
            }
            int ping = (int) Math.max(30, Math.min(900, Json.num(w, "ping", pm.ping())));
            ws.setReadTimeout((ping * 2 + 30) * 1000);
            if (owner != null) { owner.socket = ws; owner.connectedAt = System.currentTimeMillis(); }
            while (true) {
                String text = ws.receive();
                if (text == null) return;
                Map<String, Object> m = Json.parseObject(text);
                String type = Json.str(m, "type", "");
                if ("push".equals(type)) {
                    PowerManager power = (PowerManager) App.get().getSystemService(Context.POWER_SERVICE);
                    PowerManager.WakeLock lock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "kovapp:push");
                    lock.acquire(10_000);
                    try {
                        Map<String, Object> payload = Json.obj(m, "payload");
                        if (payload != null) {
                            if (!payload.containsKey("id")) payload.put("id", Json.str(m, "id", ""));
                            pm.handle(Json.stringify(payload), "direct");
                        }
                        ws.sendText(Json.stringify(Json.map("type", "ack", "id", Json.str(m, "id", ""))));
                    } finally {
                        if (lock.isHeld()) lock.release();
                    }
                } else if ("ping".equals(type)) {
                    ws.sendText("{\"type\":\"pong\"}");
                } else if ("flushed".equals(type) && once) {
                    return;
                } else if ("error".equals(type) && "unauthorized".equals(Json.str(m, "reason", ""))) {
                    throw new Unauthorized("unauthorized");
                }
            }
        } finally {
            ws.close();
        }
    }
}
