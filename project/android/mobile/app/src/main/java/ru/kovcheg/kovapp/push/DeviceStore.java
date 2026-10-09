package ru.kovcheg.kovapp.push;

import android.content.Context;
import android.content.SharedPreferences;

import java.security.SecureRandom;

import ru.kovcheg.kovapp.core.B64;

/** Что приложение помнит о своей регистрации для уведомлений. */
public final class DeviceStore {
    private final SharedPreferences prefs;

    public DeviceStore(Context c) { prefs = c.getSharedPreferences("kovapp.push", Context.MODE_PRIVATE); }

    public static final class Creds {
        public final String host, deviceId, secret, mode;
        public final byte[] payloadKey;
        public final long userId;
        Creds(String host, String deviceId, String secret, byte[] payloadKey, long userId, String mode) {
            this.host = host; this.deviceId = deviceId; this.secret = secret; this.payloadKey = payloadKey; this.userId = userId; this.mode = mode;
        }
    }

    /** Постоянный номер установки: 128 случайных бит, не связан с железом */
    public synchronized String deviceId() {
        String id = prefs.getString("device_id", null);
        if (id == null || !id.matches("[0-9a-f]{32}")) {
            byte[] b = new byte[16];
            new SecureRandom().nextBytes(b);
            id = B64.hex(b);
            prefs.edit().putString("device_id", id).apply();
        }
        return id;
    }

    /** Пользователь включил уведомления на этом телефоне (как подписка в браузере) */
    public boolean enabled() { return prefs.getBoolean("enabled", false); }
    public void setEnabled(boolean on) { prefs.edit().putBoolean("enabled", on).apply(); }

    public boolean askedPermission() { return prefs.getBoolean("asked_permission", false); }
    public void setAskedPermission() { prefs.edit().putBoolean("asked_permission", true).apply(); }

    public synchronized void saveRegistration(String host, long userId, String mode, String secret, byte[] payloadKey) {
        prefs.edit()
                .putString("host", host)
                .putLong("user_id", userId)
                .putString("mode", mode)
                .putString("secret", SecureStore.seal(secret))
                .putString("payload_key", SecureStore.seal(B64.encode(payloadKey)))
                .apply();
    }

    public synchronized Creds creds() {
        String host = prefs.getString("host", null), mode = prefs.getString("mode", null);
        String secret = SecureStore.open(prefs.getString("secret", null));
        String key = SecureStore.open(prefs.getString("payload_key", null));
        long user = prefs.getLong("user_id", 0);
        if (host == null || mode == null || secret == null || key == null || user <= 0) return null;
        try {
            return new Creds(host, deviceId(), secret, B64.decode(key), user, mode);
        } catch (IllegalArgumentException e) {
            return null;
        }
    }

    public String mode() { return prefs.getString("mode", ""); }

    public synchronized void clearCredentials() {
        prefs.edit().remove("host").remove("user_id").remove("mode").remove("secret").remove("payload_key").remove("fcm_token").apply();
    }

    public String fcmToken() { return prefs.getString("fcm_token", null); }
    public void setFcmToken(String t) { prefs.edit().putString("fcm_token", t).apply(); }
}
