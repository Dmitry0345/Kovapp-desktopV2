package ru.kovcheg.kovapp.net;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Map;

import ru.kovcheg.kovapp.core.Json;

/**
 * Запросы приложения к серверу вне страницы (регистрация устройства,
 * подтверждение доставки). HttpURLConnection соблюдает настройки
 * безопасности сети приложения (network_security_config): чужие
 * сертификаты и HTTP к посторонним адресам отвергаются системой.
 */
public final class HttpJson {
    private HttpJson() {}

    public static final class Response {
        public final int status;
        public final Map<String, Object> body;
        Response(int status, Map<String, Object> body) { this.status = status; this.body = body; }
        public boolean ok() { return status >= 200 && status < 300; }
    }

    public static String userAgent = "KovappAndroid";

    public static Response call(String method, String url, String bearer, Map<String, Object> json) throws IOException {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        try {
            c.setRequestMethod(method);
            c.setConnectTimeout(15000);
            c.setReadTimeout(20000);
            c.setUseCaches(false);
            c.setInstanceFollowRedirects(false);
            c.setRequestProperty("Accept", "application/json");
            c.setRequestProperty("User-Agent", userAgent);
            if (bearer != null && !bearer.isEmpty()) c.setRequestProperty("Authorization", "Bearer " + bearer);
            if (json != null) {
                byte[] body = Json.stringify(json).getBytes(StandardCharsets.UTF_8);
                c.setDoOutput(true);
                c.setRequestProperty("Content-Type", "application/json");
                c.setFixedLengthStreamingMode(body.length);
                try (OutputStream out = c.getOutputStream()) { out.write(body); }
            }
            int status = c.getResponseCode();
            InputStream in = status >= 400 ? c.getErrorStream() : c.getInputStream();
            String text = in == null ? "" : read(in, 1 << 20);
            Map<String, Object> parsed = null;
            if (!text.isEmpty()) {
                try { parsed = Json.parseObject(text); } catch (IllegalArgumentException e) { parsed = Json.map("detail", text.length() > 300 ? text.substring(0, 300) : text); }
            }
            return new Response(status, parsed == null ? Json.map() : parsed);
        } finally {
            c.disconnect();
        }
    }

    static String read(InputStream in, int limit) throws IOException {
        try (InputStream s = in) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int n;
            while ((n = s.read(buf)) > 0) {
                out.write(buf, 0, n);
                if (out.size() > limit) throw new IOException("ответ сервера слишком большой");
            }
            return new String(out.toByteArray(), StandardCharsets.UTF_8);
        }
    }

    /** Адрес API: база сессии (Kov.Api.state.host) + путь без ведущего «/» */
    public static String join(String base, String path) {
        String b = base == null ? "" : base.replaceAll("/+$", "");
        return b + "/" + path.replaceAll("^/+", "");
    }
}
