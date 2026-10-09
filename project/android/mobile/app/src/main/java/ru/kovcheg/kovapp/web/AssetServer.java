package ru.kovcheg.kovapp.web;

import android.content.res.AssetManager;
import android.net.Uri;
import android.util.Log;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.FileInputStream;
import java.io.FileNotFoundException;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

import ru.kovcheg.kovapp.AppConfig;
import ru.kovcheg.kovapp.files.TempFiles;

/**
 * Интерфейс открывается «с адреса сервера» (/app/…), но файлы берутся из
 * APK: приложение работает без интернета, а запросы к API остаются на том
 * же адресе, что и в браузере (без CORS). Заголовки безопасности — как у
 * сервера. Остальные адреса уходят в сеть как обычно.
 */
public final class AssetServer {
    private static final String TAG = "KovappAssets";
    public interface StateScript { String build(); }

    private final AssetManager assets;
    private final Overlay overlay;
    private final TempFiles temp;
    private final StateScript state;
    private final Map<String, byte[]> cache = new ConcurrentHashMap<>();

    public AssetServer(AssetManager assets, Overlay overlay, TempFiles temp, StateScript state) {
        this.assets = assets;
        this.overlay = overlay;
        this.temp = temp;
        this.state = state;
    }

    public WebResourceResponse handle(WebResourceRequest req) {
        Uri u = req.getUrl();
        String scheme = u.getScheme() == null ? "" : u.getScheme();
        // страница не должна читать content:// и file:// — только свои адреса
        if (scheme.equals("content") || scheme.equals("file")) return status(403, "Forbidden");
        String url = u.toString();
        String rel = AppConfig.NAV.appRelative(url);
        if (rel == null) return null;
        String method = req.getMethod() == null ? "GET" : req.getMethod();
        // POST и прочее — всегда в сеть: если интерфейс лежит в корне сайта, это запросы к API
        if (!method.equalsIgnoreCase("GET") && !method.equalsIgnoreCase("HEAD")) return null;
        if (rel.startsWith(TempFiles.PREFIX)) return tempFile(rel, req.getRequestHeaders());
        if (rel.equals("native/kovapp-state.js")) {
            return respond(200, "OK", "text/javascript", state.build().getBytes(StandardCharsets.UTF_8), true);
        }
        String path = WebRules.normalize(rel);
        if (path == null) return status(404, "Not Found");
        String live = AppConfig.devAssetsUrl();
        if (!live.isEmpty()) {
            WebResourceResponse r = fromDevServer(live, path);
            if (r != null) return r;
        }
        return fromAssets(path);
    }

    private WebResourceResponse fromAssets(String path) {
        String mime = WebRules.mimeFor(path);
        try {
            if (overlay.touches(path) && WebRules.isText(mime)) {
                byte[] body = cache.get(path);
                if (body == null) {
                    body = transform(path, readAll(assets.open("web/" + path)));
                    cache.put(path, body);
                }
                return respond(200, "OK", mime, body, false);
            }
            return stream(200, "OK", mime, assets.open("web/" + path), false);
        } catch (FileNotFoundException e) {
            // такого файла в APK нет — пусть ответит сервер (API или новый файл)
            return null;
        } catch (IOException e) {
            Log.w(TAG, "asset " + path + ": " + e.getMessage());
            return status(500, "Internal Error");
        }
    }

    private byte[] transform(String path, byte[] raw) {
        String text = new String(raw, StandardCharsets.UTF_8);
        if (path.endsWith(".html")) text = overlay.injectHtml(text);
        text = overlay.patchText(path, text);
        for (String w : overlay.warnings()) Log.w(TAG, "правка текста не применилась: " + w);
        return text.getBytes(StandardCharsets.UTF_8);
    }

    /** Отладка: файлы интерфейса берутся с ПК (tools/devserver.py через adb reverse) */
    private WebResourceResponse fromDevServer(String base, String path) {
        try {
            HttpURLConnection c = (HttpURLConnection) new URL(base.replaceAll("/+$", "") + "/" + path + "?raw=1").openConnection();
            c.setConnectTimeout(1500);
            c.setReadTimeout(5000);
            if (c.getResponseCode() == 404) return status(404, "Not Found");
            if (c.getResponseCode() != 200) return null;
            byte[] body = readAll(c.getInputStream());
            String mime = WebRules.mimeFor(path);
            if (overlay.touches(path) && WebRules.isText(mime)) body = transform(path, body);
            return respond(200, "OK", mime, body, true);
        } catch (IOException e) {
            return null;
        }
    }

    private WebResourceResponse tempFile(String rel, Map<String, String> headers) {
        TempFiles.Item it = temp.lookup(rel);
        if (it == null) return status(404, "Not Found");
        long size = it.length();
        String range = null;
        if (headers != null) for (Map.Entry<String, String> h : headers.entrySet()) if ("range".equalsIgnoreCase(h.getKey())) range = h.getValue();
        try {
            long[] r = WebRules.parseRange(range, size);
            InputStream in = it.open();
            if (r == null) {
                WebResourceResponse res = stream(200, "OK", it.mime, in, true);
                res.getResponseHeaders().put("Content-Length", String.valueOf(size));
                res.getResponseHeaders().put("Accept-Ranges", "bytes");
                return res;
            }
            long skipped = 0;
            while (skipped < r[0]) {
                long n = in.skip(r[0] - skipped);
                if (n <= 0) break;
                skipped += n;
            }
            WebResourceResponse res = stream(206, "Partial Content", it.mime, new Limited(in, r[1] - r[0] + 1), true);
            res.getResponseHeaders().put("Content-Range", "bytes " + r[0] + "-" + r[1] + "/" + size);
            res.getResponseHeaders().put("Content-Length", String.valueOf(r[1] - r[0] + 1));
            res.getResponseHeaders().put("Accept-Ranges", "bytes");
            return res;
        } catch (IOException e) {
            return status(404, "Not Found");
        }
    }

    static final class Limited extends InputStream {
        private final InputStream in;
        private long left;
        Limited(InputStream in, long left) { this.in = in; this.left = left; }
        @Override public int read() throws IOException {
            if (left <= 0) return -1;
            int b = in.read();
            if (b >= 0) left--;
            return b;
        }
        @Override public int read(byte[] b, int off, int len) throws IOException {
            if (left <= 0) return -1;
            int n = in.read(b, off, (int) Math.min(len, left));
            if (n > 0) left -= n;
            return n;
        }
        @Override public void close() throws IOException { in.close(); }
    }

    private static WebResourceResponse respond(int code, String reason, String mime, byte[] body, boolean noStore) {
        return stream(code, reason, mime, new ByteArrayInputStream(body), noStore);
    }

    private static WebResourceResponse stream(int code, String reason, String mime, InputStream in, boolean noStore) {
        Map<String, String> h = WebRules.headers(mime, AppConfig.csp(), noStore);
        h.remove("Content-Type");
        return new WebResourceResponse(mime, WebRules.isText(mime) ? "utf-8" : null, code, reason, h, in);
    }

    private static WebResourceResponse status(int code, String reason) {
        return respond(code, reason, "text/plain", (code + " " + reason).getBytes(StandardCharsets.UTF_8), true);
    }

    static byte[] readAll(InputStream in) throws IOException {
        try (InputStream s = in) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[16384];
            int n;
            while ((n = s.read(buf)) > 0) out.write(buf, 0, n);
            return out.toByteArray();
        }
    }
}
