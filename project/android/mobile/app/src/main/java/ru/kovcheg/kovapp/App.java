package ru.kovcheg.kovapp;

import android.app.Application;
import android.os.Handler;
import android.os.Looper;

import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.atomic.AtomicInteger;

import ru.kovcheg.kovapp.files.Downloads;
import ru.kovcheg.kovapp.files.PickedFiles;
import ru.kovcheg.kovapp.files.TempFiles;
import ru.kovcheg.kovapp.media.MediaJobs;
import ru.kovcheg.kovapp.net.HttpJson;
import ru.kovcheg.kovapp.notify.Notifier;
import ru.kovcheg.kovapp.push.PushManager;
import ru.kovcheg.kovapp.web.Bridge;
import ru.kovcheg.kovapp.web.Overlay;

/** Общие службы приложения: фоновые потоки, файлы, уведомления, мост к странице. */
public final class App extends Application {
    private static App instance;

    private final ExecutorService io = Executors.newCachedThreadPool(new ThreadFactory() {
        private final AtomicInteger n = new AtomicInteger(1);
        @Override public Thread newThread(Runnable r) {
            Thread t = new Thread(r, "kovapp-io-" + n.getAndIncrement());
            t.setPriority(Thread.NORM_PRIORITY - 1);
            return t;
        }
    });
    private final Handler main = new Handler(Looper.getMainLooper());
    private TempFiles temp;
    private PickedFiles picked;
    private MediaJobs media;
    private PushManager push;
    private Downloads downloads;
    private volatile Overlay overlay;
    private volatile Bridge pageBridge;
    private volatile boolean pageVisible;

    public static App get() { return instance; }

    @Override public void onCreate() {
        super.onCreate();
        instance = this;
        HttpJson.userAgent = "KovappAndroid/" + BuildConfig.VERSION_NAME;
        Notifier.ensureChannels(this);
        temp = new TempFiles(new File(getCacheDir(), "kovapp-tmp"));
        temp.clearAll();
        picked = new PickedFiles(this);
        media = new MediaJobs(this, picked, temp);
        downloads = new Downloads(this);
        push = new PushManager(this);
        push.onAppStart();
    }

    public void io(Runnable r) { io.execute(r); }
    public void main(Runnable r) { main.post(r); }

    public TempFiles temp() { return temp; }
    public PickedFiles picked() { return picked; }
    public MediaJobs media() { return media; }
    public PushManager push() { return push; }
    public Downloads downloads() { return downloads; }

    public Overlay overlay() {
        Overlay o = overlay;
        if (o == null) {
            synchronized (this) {
                if (overlay == null) {
                    try {
                        overlay = new Overlay(asset("kovapp/inject.json"), asset("kovapp/web-patches.json"), AppConfig.webPatches());
                    } catch (IOException e) {
                        throw new IllegalStateException("в APK нет файлов надстройки", e);
                    }
                }
                o = overlay;
            }
        }
        return o;
    }

    private String asset(String path) throws IOException {
        try (InputStream in = getAssets().open(path)) {
            java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
            return new String(out.toByteArray(), java.nio.charset.StandardCharsets.UTF_8);
        }
    }

    // ── связь с открытой страницей ───────────────────────────────────────
    public void setPageBridge(Bridge b) { pageBridge = b; }

    /** Приложение на экране и страница готова принимать события */
    public void setPageVisible(boolean v) { pageVisible = v; }
    public boolean pageVisible() { return pageVisible && pageBridge != null && pageBridge.connected(); }

    public void emitToPage(String event, Map<String, Object> data) {
        Bridge b = pageBridge;
        if (b != null) b.emit(event, data);
    }

    /** Выход из учётной записи: всё, что относилось к прежнему пользователю, удаляется */
    public void wipeSession() {
        media.clear();
        picked.clear();
        temp.clearAll();
    }
}
