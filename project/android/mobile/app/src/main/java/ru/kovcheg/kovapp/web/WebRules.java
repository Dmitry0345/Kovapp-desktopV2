package ru.kovcheg.kovapp.web;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;

/**
 * Правила выдачи файлов веб-интерфейса, вшитых в APK. Без Android:
 * проверяются тестами на JVM.
 */
public final class WebRules {
    private WebRules() {}

    /**
     * Политика содержимого — та же, что ставит сервер (server_app/core.py,
     * DEFAULT_CSP). Страница в приложении работает под теми же ограничениями,
     * что и в браузере.
     */
    public static final String DEFAULT_CSP =
            "default-src 'self'; "
            + "script-src 'self' 'wasm-unsafe-eval'; "
            + "style-src 'self' 'unsafe-inline'; "
            + "img-src 'self' data: blob: https://static.klipy.com https://static1.klipy.com https://static2.klipy.com; "
            + "media-src 'self' blob: https://static.klipy.com https://static1.klipy.com https://static2.klipy.com; "
            + "font-src 'self' data:; "
            + "connect-src 'self' ws: wss: https://api.klipy.com; "
            + "object-src 'none'; "
            + "base-uri 'self'; "
            + "form-action 'self'; "
            + "frame-ancestors 'none'";

    /**
     * Относительный путь внутри папки приложения → безопасный вид или null.
     * Отсекает выход наверх, обратные слэши, NUL и прочие фокусы с адресом.
     */
    /**
     * Как _csp_with_host в server_app/core.py: если API отвечает с другого
     * адреса, он добавляется в разрешённые для запросов, картинок и видео.
     */
    public static String cspWithHost(String policy, String host) {
        if (host == null || host.isEmpty()) return policy;
        String ws = host.replace("https://", "wss://").replace("http://", "ws://");
        return policy.replace("connect-src 'self'", "connect-src 'self' " + host + " " + ws)
                .replace("img-src 'self'", "img-src 'self' " + host)
                .replace("media-src 'self'", "media-src 'self' " + host);
    }

    public static String normalize(String raw) {
        if (raw == null) return null;
        String decoded = percentDecode(raw);
        if (decoded == null) return null;
        if (decoded.indexOf('\0') >= 0 || decoded.indexOf('\\') >= 0) return null;
        StringBuilder out = new StringBuilder();
        for (String seg : decoded.split("/", -1)) {
            if (seg.isEmpty() || seg.equals(".")) continue;
            if (seg.equals("..")) return null;
            if (seg.indexOf(':') >= 0) return null;
            if (out.length() > 0) out.append('/');
            out.append(seg);
        }
        String path = out.toString();
        if (path.isEmpty() || raw.endsWith("/")) path = path.isEmpty() ? "index.html" : path + "/index.html";
        return path;
    }

    /** Декодирует %XX как UTF-8. Неверная последовательность → null. */
    public static String percentDecode(String s) {
        if (s.indexOf('%') < 0) return s;
        ByteArrayOutputStream buf = new ByteArrayOutputStream(s.length());
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c == '%') {
                if (i + 2 >= s.length()) return null;
                int hi = Character.digit(s.charAt(i + 1), 16);
                int lo = Character.digit(s.charAt(i + 2), 16);
                if (hi < 0 || lo < 0) return null;
                buf.write((hi << 4) | lo);
                i += 2;
            } else {
                byte[] b = String.valueOf(c).getBytes(StandardCharsets.UTF_8);
                buf.write(b, 0, b.length);
            }
        }
        return new String(buf.toByteArray(), StandardCharsets.UTF_8);
    }

    private static final Map<String, String> MIME = new LinkedHashMap<>();
    static {
        MIME.put("html", "text/html");
        MIME.put("htm", "text/html");
        MIME.put("js", "text/javascript");
        MIME.put("mjs", "text/javascript");
        MIME.put("css", "text/css");
        MIME.put("json", "application/json");
        MIME.put("webmanifest", "application/manifest+json");
        MIME.put("svg", "image/svg+xml");
        MIME.put("png", "image/png");
        MIME.put("jpg", "image/jpeg");
        MIME.put("jpeg", "image/jpeg");
        MIME.put("gif", "image/gif");
        MIME.put("webp", "image/webp");
        MIME.put("ico", "image/x-icon");
        MIME.put("wasm", "application/wasm");
        MIME.put("bcmap", "application/octet-stream");
        MIME.put("pfb", "application/octet-stream");
        MIME.put("ttf", "font/ttf");
        MIME.put("otf", "font/otf");
        MIME.put("woff", "font/woff");
        MIME.put("woff2", "font/woff2");
        MIME.put("txt", "text/plain");
        MIME.put("md", "text/plain");
        MIME.put("mp3", "audio/mpeg");
        MIME.put("ogg", "audio/ogg");
        MIME.put("wav", "audio/wav");
        MIME.put("mp4", "video/mp4");
        MIME.put("bin", "application/octet-stream");
    }

    public static String mimeFor(String path) {
        String p = path == null ? "" : path.toLowerCase(Locale.ROOT);
        int dot = p.lastIndexOf('.');
        String ext = dot >= 0 ? p.substring(dot + 1) : "";
        String m = MIME.get(ext);
        return m != null ? m : "application/octet-stream";
    }

    public static boolean isText(String mime) {
        return mime.startsWith("text/") || mime.equals("application/json")
                || mime.equals("application/manifest+json") || mime.equals("image/svg+xml");
    }

    /** Заголовки ответа — те же, что добавляет сервер ко всем ответам */
    public static Map<String, String> headers(String mime, String csp, boolean noStore) {
        Map<String, String> h = new LinkedHashMap<>();
        h.put("Content-Type", isText(mime) ? mime + "; charset=utf-8" : mime);
        h.put("X-Content-Type-Options", "nosniff");
        h.put("X-Frame-Options", "DENY");
        h.put("Referrer-Policy", "no-referrer");
        h.put("Cross-Origin-Opener-Policy", "same-origin");
        h.put("Permissions-Policy", "geolocation=(), microphone=(self), camera=(self)");   // без payment: WebView его не знает
        if (csp != null && !csp.isEmpty() && (mime.equals("text/html") || mime.equals("text/javascript"))) {
            h.put("Content-Security-Policy", csp);
        }
        h.put("Cache-Control", noStore ? "no-store" : "no-cache");
        return h;
    }

    /** Разбор заголовка Range: bytes=a-b → {from, to} включительно, либо null */
    public static long[] parseRange(String header, long size) {
        if (header == null || size <= 0) return null;
        String h = header.trim().toLowerCase(Locale.ROOT);
        if (!h.startsWith("bytes=") || h.indexOf(',') >= 0) return null;
        String spec = h.substring(6).trim();
        int dash = spec.indexOf('-');
        if (dash < 0) return null;
        String a = spec.substring(0, dash).trim(), b = spec.substring(dash + 1).trim();
        try {
            long from, to;
            if (a.isEmpty()) {
                if (b.isEmpty()) return null;
                long n = Long.parseLong(b);
                if (n <= 0) return null;
                from = Math.max(0, size - n);
                to = size - 1;
            } else {
                from = Long.parseLong(a);
                to = b.isEmpty() ? size - 1 : Math.min(Long.parseLong(b), size - 1);
            }
            if (from < 0 || from > to || from >= size) return null;
            return new long[]{from, to};
        } catch (NumberFormatException e) {
            return null;
        }
    }
}
