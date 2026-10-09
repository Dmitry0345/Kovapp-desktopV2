package ru.kovcheg.kovapp.web;

import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.webkit.WebView;

import androidx.webkit.JavaScriptReplyProxy;
import androidx.webkit.WebMessageCompat;
import androidx.webkit.WebViewCompat;
import androidx.webkit.WebViewFeature;

import java.util.Collections;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

import ru.kovcheg.kovapp.AppConfig;

/**
 * Мост «страница ↔ приложение». Используется WebMessageListener: Android
 * сам сообщает, из какого источника и из какого фрейма пришло сообщение,
 * а объект моста виден только страницам своего сервера. Никаких
 * addJavascriptInterface — у страницы нет прямого доступа к Java.
 */
public final class Bridge implements WebViewCompat.WebMessageListener {
    public static final String NAME = "kovNativePort";
    private static final String TAG = "KovappBridge";

    public interface Command { void run(BridgeProtocol.Args args, Reply reply) throws Exception; }

    /** Ответ на команду; можно вызывать из любого потока */
    public static final class Reply {
        private final long id;
        private final JavaScriptReplyProxy proxy;
        private final Handler main;
        private volatile boolean done;
        Reply(long id, JavaScriptReplyProxy proxy, Handler main) { this.id = id; this.proxy = proxy; this.main = main; }
        public void ok(Object result) { send(BridgeProtocol.ok(id, result)); }
        public void fail(String message) { send(BridgeProtocol.error(id, message)); }
        private void send(final String msg) {
            if (done) return;
            done = true;
            main.post(() -> {
                try { proxy.postMessage(msg); } catch (RuntimeException e) { /* страница ушла */ }
            });
        }
    }

    private final Map<String, Command> commands = new ConcurrentHashMap<>();
    private final Handler main = new Handler(Looper.getMainLooper());
    private volatile JavaScriptReplyProxy page;

    public static boolean supported() {
        return WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER);
    }

    public void attach(WebView view) {
        WebViewCompat.addWebMessageListener(view, NAME, Collections.singleton(AppConfig.origin()), this);
    }

    public void register(String cmd, Command c) { commands.put(cmd, c); }

    @Override public void onPostMessage(WebView view, WebMessageCompat message, Uri sourceOrigin, boolean isMainFrame, JavaScriptReplyProxy replyProxy) {
        // только главная страница своего сервера; вложенные фреймы моста не получают
        if (!isMainFrame || sourceOrigin == null || !AppConfig.NAV.sameOrigin(sourceOrigin.toString())) return;
        String data = message.getData();
        if (data == null) return;
        BridgeProtocol.Request req;
        try {
            req = BridgeProtocol.parse(data);
        } catch (BridgeProtocol.BadRequest e) {
            if (e.id > 0) replyProxy.postMessage(BridgeProtocol.error(e.id, e.getMessage()));
            return;
        }
        page = replyProxy;
        Command c = commands.get(req.cmd);
        Reply r = new Reply(req.id, replyProxy, main);
        if (c == null) { r.fail("неизвестная команда: " + req.cmd); return; }
        try {
            c.run(req.args, r);
        } catch (BridgeProtocol.BadRequest e) {
            r.fail(e.getMessage());
        } catch (Exception e) {
            Log.w(TAG, req.cmd + ": " + e);
            r.fail(e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage());
        }
    }

    /** Событие странице: push, navigate, media.* */
    public void emit(String event, Object data) {
        final String msg = BridgeProtocol.event(event, data);
        main.post(() -> {
            JavaScriptReplyProxy p = page;
            if (p == null) return;
            try { p.postMessage(msg); } catch (RuntimeException e) { /* страница перезагружается */ }
        });
    }

    public boolean connected() { return page != null; }

    /** Страница начала загружаться заново — старый адресат событий недействителен */
    public void reset() { page = null; }
}
