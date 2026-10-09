package ru.kovcheg.kovapp;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Message;
import android.provider.Settings;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;
import android.window.OnBackInvokedDispatcher;

import androidx.webkit.WebSettingsCompat;
import androidx.webkit.WebViewFeature;

import java.util.ArrayList;
import java.util.List;

import ru.kovcheg.kovapp.files.Downloads;
import ru.kovcheg.kovapp.files.FileChooser;
import ru.kovcheg.kovapp.web.AppChromeClient;
import ru.kovcheg.kovapp.web.AppWebViewClient;
import ru.kovcheg.kovapp.web.AssetServer;
import ru.kovcheg.kovapp.web.Bridge;
import ru.kovcheg.kovapp.web.NavigationPolicy;
import ru.kovcheg.kovapp.web.SystemBars;
import ru.kovcheg.kovapp.web.WebViewSupport;

/**
 * Единственное окно приложения: WebView с веб-интерфейсом «Ковчега».
 * Дочерние окна (просмотр документов, таблиц) открываются поверх, как
 * вкладки браузера; кнопка «Назад» закрывает сначала их.
 */
public final class MainActivity extends Activity implements AppWebViewClient.Host, AppChromeClient.Host {
    public static final String ACTION_OPEN = "ru.kovcheg.kovapp.OPEN";
    public static final String EXTRA_URL = "url", EXTRA_TAG = "tag";
    private static final int REQ_NOTIFY = 7201, REQ_STORAGE = 7202, REQ_GALLERY = 7203, REQ_CAMERA = 7204;

    public interface Result<T> { void on(T value); }

    private FrameLayout root;
    private WebView web;
    private Bridge bridge;
    private AssetServer assets;
    private SystemBars bars;
    private FileChooser chooser;
    private final List<WebView> children = new ArrayList<>();
    private View customView;
    private WebChromeClient.CustomViewCallback customCallback;
    private boolean resumed, pageHello;
    private String pendingRoute, pendingTag;
    private int mainColor = Color.WHITE;
    private final List<Result<String>> notifyWaiters = new ArrayList<>();
    private final List<Result<Boolean>> storageWaiters = new ArrayList<>();
    private final List<Result<Boolean>> galleryWaiters = new ArrayList<>();
    private final List<Result<Boolean>> cameraWaiters = new ArrayList<>();

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        WebViewSupport.Status wv = WebViewSupport.check(this);
        if (!wv.ok) { showUpdateScreen(wv); return; }
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG);
        root = new FrameLayout(this);
        root.setBackgroundColor(Color.WHITE);
        setContentView(root);
        bars = new SystemBars(this, root);
        bars.apply(Color.WHITE);
        chooser = new FileChooser(this);
        assets = new AssetServer(getAssets(), App.get().overlay(), App.get().temp(), NativeCommands::stateScript);
        web = createWebView(false);
        root.addView(web, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        bridge = attachBridge(web, true);
        App.get().setPageBridge(bridge);
        if (Build.VERSION.SDK_INT >= 33) {
            getOnBackInvokedDispatcher().registerOnBackInvokedCallback(OnBackInvokedDispatcher.PRIORITY_DEFAULT, this::handleBack);
        }
        String route = routeFrom(getIntent());
        web.loadUrl(AppConfig.startUrl() + (route != null ? route : ""));
    }

    private Bridge attachBridge(WebView view, boolean main) {
        Bridge b = new Bridge();
        new NativeCommands(this, b, main).registerAll();
        b.attach(view);
        return b;
    }

    @SuppressLint("SetJavaScriptEnabled")
    private WebView createWebView(boolean child) {
        WebView w = new WebView(this);
        WebSettings s = w.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(true);          // чтение выбранных файлов; адреса content:// закрыты политикой и AssetServer
        s.setAllowFileAccessFromFileURLs(false);
        s.setAllowUniversalAccessFromFileURLs(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        s.setSupportMultipleWindows(true);
        s.setJavaScriptCanOpenWindowsAutomatically(false);
        s.setMediaPlaybackRequiresUserGesture(true);
        s.setGeolocationEnabled(false);
        // интерфейс приложения пальцами не масштабируется; окна документов — масштабируются
        s.setSupportZoom(child);
        s.setBuiltInZoomControls(child);
        s.setDisplayZoomControls(false);
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(false);
        s.setTextZoom(100);
        s.setUserAgentString(s.getUserAgentString() + AppConfig.userAgentSuffix());
        // тёмная тема — только своя у страницы, без «затемнения» движком
        if (WebViewFeature.isFeatureSupported(WebViewFeature.ALGORITHMIC_DARKENING)) {
            WebSettingsCompat.setAlgorithmicDarkeningAllowed(s, false);
        } else if (WebViewFeature.isFeatureSupported(WebViewFeature.FORCE_DARK)) {
            //noinspection deprecation
            WebSettingsCompat.setForceDark(s, WebSettingsCompat.FORCE_DARK_OFF);
        }
        CookieManager.getInstance().setAcceptThirdPartyCookies(w, false);
        w.setWebViewClient(new AppWebViewClient(this, child));
        w.setWebChromeClient(new AppChromeClient(this));
        w.setDownloadListener((url, ua, disposition, mime, length) -> onDownload(w, url, disposition, mime));
        w.setBackgroundColor(Color.TRANSPARENT);
        return w;
    }

    String userAgent() { return web == null ? null : web.getSettings().getUserAgentString(); }

    // ── окно «обновите WebView» ──────────────────────────────────────────
    private void showUpdateScreen(final WebViewSupport.Status s) {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER);
        int pad = (int) (24 * getResources().getDisplayMetrics().density);
        box.setPadding(pad, pad, pad, pad);
        TextView t = new TextView(this);
        t.setTextSize(17);
        t.setGravity(Gravity.CENTER);
        t.setText("Для работы Kovapp нужен компонент «Android System WebView» версии "
                + AppConfig.MIN_WEBVIEW_MAJOR + " или новее.\n\nУстановлена версия: "
                + (s.version.isEmpty() ? "нет" : s.version) + ".\n\nОбновите его в магазине приложений и запустите Kovapp снова.");
        Button b = new Button(this);
        b.setText("Открыть магазин приложений");
        b.setOnClickListener(v -> {
            String pkg = s.packageName;
            Uri market = Uri.parse(pkg.startsWith("com.huawei") ? "appmarket://details?id=" + pkg : "market://details?id=" + pkg);
            try { startActivity(new Intent(Intent.ACTION_VIEW, market)); }
            catch (ActivityNotFoundException e) { openExternal(Uri.parse("https://play.google.com/store/apps/details?id=" + pkg)); }
        });
        box.addView(t);
        box.addView(b);
        setContentView(box);
    }

    // ── жизненный цикл ───────────────────────────────────────────────────
    @Override protected void onResume() {
        super.onResume();
        resumed = true;
        ru.kovcheg.kovapp.files.TransferService.appShown(this, true);
        if (web != null) {
            web.onResume();
            web.resumeTimers();
            App.get().setPageVisible(pageHello);
            // разрешение могли поменять в настройках телефона
            bridge.emit("permission", App.get().push().permission());
        }
    }

    @Override protected void onPause() {
        resumed = false;
        App.get().setPageVisible(false);
        ru.kovcheg.kovapp.files.TransferService.appShown(this, false);
        if (web != null) web.onPause();
        super.onPause();
    }

    @Override protected void onDestroy() {
        App.get().setPageVisible(false);
        if (chooser != null) chooser.cancel();
        for (WebView c : new ArrayList<>(children)) closeChild(c);
        if (web != null) {
            root.removeView(web);
            web.destroy();
            web = null;
        }
        super.onDestroy();
    }

    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        String route = routeFrom(intent);
        if (route == null || web == null) return;
        // метка — чтобы страница нашла своё уведомление и перешла так же, как по щелчку в браузере
        String tag = intent.getStringExtra(EXTRA_TAG);
        for (WebView c : new ArrayList<>(children)) closeChild(c);
        if (pageHello) bridge.emit("navigate", ru.kovcheg.kovapp.core.Json.map("url", route, "tag", tag == null ? "" : tag));
        else { pendingRoute = route; pendingTag = tag; }
    }

    private static String routeFrom(Intent i) {
        if (i == null || !ACTION_OPEN.equals(i.getAction())) return null;
        String url = i.getStringExtra(EXTRA_URL);
        return NavigationPolicy.isSafeRoute(url) ? url : null;
    }

    /** Страница загрузилась и прислала hello */
    void onPageHello() {
        pageHello = true;
        App.get().setPageVisible(resumed);
        if (pendingRoute != null) {
            bridge.emit("navigate", ru.kovcheg.kovapp.core.Json.map("url", pendingRoute, "tag", pendingTag == null ? "" : pendingTag));
            pendingRoute = null;
            pendingTag = null;
        }
    }

    // ── «Назад» ──────────────────────────────────────────────────────────
    @Override public void onBackPressed() { handleBack(); }

    private void handleBack() {
        if (customView != null) { hideCustom(); return; }
        if (!children.isEmpty()) {
            final WebView top = children.get(children.size() - 1);
            top.evaluateJavascript("(window.KovNative&&KovNative.onBack)?KovNative.onBack():false", v -> {
                if ("true".equals(v)) return;
                if (top.canGoBack()) top.goBack();
                else closeChild(top);
            });
            return;
        }
        if (web == null) { finish(); return; }
        web.evaluateJavascript("(window.KovNative&&KovNative.onBack)?KovNative.onBack():false", v -> {
            if ("true".equals(v)) return;
            if (!"\"exit\"".equals(v) && web.canGoBack()) web.goBack();
            else moveTaskToBack(true);
        });
    }

    void exitFromPage(boolean mainWindow) {
        if (!mainWindow && !children.isEmpty()) closeChild(children.get(children.size() - 1));
        else moveTaskToBack(true);
    }

    // ── AppWebViewClient.Host ────────────────────────────────────────────
    @Override public AssetServer assets() { return assets; }

    @Override public void openExternal(Uri uri) {
        String scheme = uri.getScheme() == null ? "" : uri.getScheme().toLowerCase(java.util.Locale.ROOT);
        if (!scheme.equals("http") && !scheme.equals("https") && !scheme.equals("mailto") && !scheme.equals("tel")) return;
        Intent i = new Intent(scheme.equals("tel") ? Intent.ACTION_DIAL : Intent.ACTION_VIEW, uri);
        i.addCategory(Intent.CATEGORY_BROWSABLE);
        try { startActivity(i); }
        catch (ActivityNotFoundException e) { Toast.makeText(this, "Нет приложения, чтобы открыть ссылку", Toast.LENGTH_SHORT).show(); }
    }

    @Override public void onPageStarted(WebView view, String url) {
        if (view == web) {
            pageHello = false;
            App.get().setPageVisible(false);
            bridge.reset();
        }
    }

    @Override public void onPageFinished(WebView view, String url) { }

    @Override public void onRendererGone(WebView view) {
        if (view != web) { closeChild(view); return; }
        // процесс страницы упал (нехватка памяти) — пересоздаём окно
        root.removeView(web);
        web.destroy();
        web = createWebView(false);
        root.addView(web, 0, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        bridge = attachBridge(web, true);
        App.get().setPageBridge(bridge);
        web.loadUrl(AppConfig.startUrl());
    }

    // ── AppChromeClient.Host ─────────────────────────────────────────────
    @Override public boolean showFileChooser(ValueCallback<Uri[]> callback, WebChromeClient.FileChooserParams params) {
        return chooser.open(callback, params);
    }

    @Override public boolean openChild(Message resultMsg) {
        WebView child = createWebView(true);
        attachBridge(child, false);
        children.add(child);
        root.addView(child, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        WebView.WebViewTransport t = (WebView.WebViewTransport) resultMsg.obj;
        t.setWebView(child);
        resultMsg.sendToTarget();
        return true;
    }

    @Override public void closeChild(WebView view) {
        if (!children.remove(view)) return;
        root.removeView(view);
        view.stopLoading();
        view.destroy();
        if (children.isEmpty()) bars.apply(mainColor);
    }

    // полный экран (видео в просмотре): без строки состояния и панели навигации, поворот — по датчику
    private int savedUi, savedOrientation;

    @Override public void showCustom(View view, WebChromeClient.CustomViewCallback callback) {
        if (customView != null) { callback.onCustomViewHidden(); return; }
        customView = view;
        customCallback = callback;
        root.addView(view, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        View decor = getWindow().getDecorView();
        savedUi = decor.getSystemUiVisibility();
        savedOrientation = getRequestedOrientation();
        decor.setSystemUiVisibility(View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_LAYOUT_STABLE);
        setRequestedOrientation(android.content.pm.ActivityInfo.SCREEN_ORIENTATION_FULL_SENSOR);
    }

    @Override public void hideCustom() {
        if (customView == null) return;
        root.removeView(customView);
        customView = null;
        if (customCallback != null) customCallback.onCustomViewHidden();
        customCallback = null;
        getWindow().getDecorView().setSystemUiVisibility(savedUi);
        setRequestedOrientation(savedOrientation);
    }

    // ── скачивание по адресу ─────────────────────────────────────────────
    private void onDownload(WebView from, String url, String disposition, String mime) {
        NavigationPolicy.Kind k = AppConfig.NAV.classify(url);
        boolean child = from != web;
        String name = URLUtil.guessFileName(url, disposition, mime);
        if (k != NavigationPolicy.Kind.SERVER && k != NavigationPolicy.Kind.APP) {
            if (child) closeChild(from);
            return;
        }
        // PDF во вкладке: WebView его не рисует — показываем просмотрщиком самой веб-версии
        if (child && "application/pdf".equalsIgnoreCase(mime)) {
            String page = AppConfig.origin() + AppConfig.appPath() + "doc.html?u=" + Uri.encode(url) + "&n=" + Uri.encode(name.toLowerCase(java.util.Locale.ROOT).endsWith(".pdf") ? name : name + ".pdf");
            from.loadUrl(page);
            return;
        }
        final String fname = name;
        withStoragePermission(granted -> {
            if (!granted) { Toast.makeText(this, "Нет разрешения на сохранение файлов", Toast.LENGTH_SHORT).show(); return; }
            App.get().downloads().enqueueUrl(url, fname, mime, userAgent());
            Toast.makeText(this, "Скачивание: " + fname, Toast.LENGTH_SHORT).show();
        });
        if (child && (from.getUrl() == null || "about:blank".equals(from.getUrl()) || !from.canGoBack())) closeChild(from);
    }

    // ── оформление ───────────────────────────────────────────────────────
    void applyTheme(String css, boolean mainWindow) {
        runOnUiThread(() -> {
            int c = SystemBars.parse(css, mainWindow ? mainColor : bars.color());
            if (mainWindow) mainColor = c;
            if (mainWindow == children.isEmpty()) bars.apply(c);
        });
    }

    // ── разрешения ───────────────────────────────────────────────────────
    void requestNotificationPermission(final Result<String> done) {
        runOnUiThread(() -> {
            String now = App.get().push().permission();
            if ("granted".equals(now)) { done.on(now); return; }
            if (Build.VERSION.SDK_INT < 33) {
                // до Android 13 разрешение не спрашивают — уведомления выключены в настройках
                openNotificationSettings();
                done.on(now);
                return;
            }
            App.get().push().store().setAskedPermission();
            if (!shouldShowRequestPermissionRationale(Manifest.permission.POST_NOTIFICATIONS) && "denied".equals(now)) {
                // система больше не спросит — открываем настройки уведомлений приложения
                openNotificationSettings();
                done.on("denied");
                return;
            }
            notifyWaiters.add(done);
            if (notifyWaiters.size() == 1) requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, REQ_NOTIFY);
        });
    }

    void withStoragePermission(final Result<Boolean> done) {
        runOnUiThread(() -> {
            if (!Downloads.needsStoragePermission()
                    || checkSelfPermission(Manifest.permission.WRITE_EXTERNAL_STORAGE) == PackageManager.PERMISSION_GRANTED) {
                done.on(true);
                return;
            }
            storageWaiters.add(done);
            if (storageWaiters.size() == 1) requestPermissions(new String[]{Manifest.permission.WRITE_EXTERNAL_STORAGE}, REQ_STORAGE);
        });
    }

    /** Фото и видео галереи — для шторки вложений */
    void requestGalleryPermission(final Result<Boolean> done) {
        runOnUiThread(() -> {
            galleryWaiters.add(done);
            if (galleryWaiters.size() == 1) requestPermissions(ru.kovcheg.kovapp.files.Gallery.permissions(), REQ_GALLERY);
        });
    }

    /** Камера (и микрофон — для съёмки видео со звуком) */
    void requestCameraPermission(final boolean audio, final Result<Boolean> done) {
        runOnUiThread(() -> {
            String[] need = audio ? new String[]{"android.permission.CAMERA", "android.permission.RECORD_AUDIO"}
                    : new String[]{"android.permission.CAMERA"};
            boolean all = true;
            for (String p : need) all &= checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED;
            if (all) { done.on(true); return; }
            cameraWaiters.add(done);
            if (cameraWaiters.size() == 1) requestPermissions(need, REQ_CAMERA);
        });
    }

    /** Камера и микрофон — только своей странице и только после разрешения Android; всё прочее — отказ */
    @Override public void permission(final android.webkit.PermissionRequest req) {
        String origin = String.valueOf(req.getOrigin());
        if (origin.endsWith("/")) origin = origin.substring(0, origin.length() - 1);
        final String[] res = req.getResources();
        boolean ownPage = origin.equalsIgnoreCase(AppConfig.origin());
        boolean onlyCapture = res != null && res.length > 0;
        boolean audio = false;
        if (res != null) for (String r : res) {
            if ("android.webkit.resource.AUDIO_CAPTURE".equals(r)) audio = true;
            else if (!"android.webkit.resource.VIDEO_CAPTURE".equals(r)) onlyCapture = false;
        }
        if (!ownPage || !onlyCapture) { runOnUiThread(req::deny); return; }
        requestCameraPermission(audio, ok -> runOnUiThread(() -> { if (ok) req.grant(res); else req.deny(); }));
    }

    @Override public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(requestCode, permissions, results);
        boolean granted = results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED;
        if (requestCode == REQ_GALLERY || requestCode == REQ_CAMERA) {
            boolean any = false;
            for (int r : results) any |= r == PackageManager.PERMISSION_GRANTED;
            List<Result<Boolean>> src = requestCode == REQ_GALLERY ? galleryWaiters : cameraWaiters;
            List<Result<Boolean>> w = new ArrayList<>(src);
            src.clear();
            // камера: нужна именно она (первая в запросе); галерея: хватит любого из разрешений
            for (Result<Boolean> r : w) r.on(requestCode == REQ_CAMERA ? granted : any);
            return;
        }
        if (requestCode == REQ_NOTIFY) {
            String p = App.get().push().permission();
            List<Result<String>> w = new ArrayList<>(notifyWaiters);
            notifyWaiters.clear();
            for (Result<String> r : w) r.on(p);
        } else if (requestCode == REQ_STORAGE) {
            List<Result<Boolean>> w = new ArrayList<>(storageWaiters);
            storageWaiters.clear();
            for (Result<Boolean> r : w) r.on(granted);
        }
    }

    /** Настройки одной категории уведомлений (например, «Связь с сервером»: её можно отключить) */
    void openChannelSettings(String channel) {
        runOnUiThread(() -> {
            if (Build.VERSION.SDK_INT < 26) { openNotificationSettings(); return; }
            Intent i = new Intent(Settings.ACTION_CHANNEL_NOTIFICATION_SETTINGS)
                    .putExtra(Settings.EXTRA_APP_PACKAGE, getPackageName())
                    .putExtra(Settings.EXTRA_CHANNEL_ID, channel);
            try { startActivity(i); } catch (ActivityNotFoundException e) { openNotificationSettings(); }
        });
    }

    void openNotificationSettings() {
        runOnUiThread(() -> {
            Intent i;
            if (Build.VERSION.SDK_INT >= 26) {
                i = new Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, getPackageName());
            } else {
                i = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:" + getPackageName()));
            }
            try { startActivity(i); } catch (ActivityNotFoundException ignore) { /* */ }
        });
    }

    /** Собственный канал держится, только если система не «усыпляет» приложение */
    @SuppressLint("BatteryLife")
    void requestBatteryExemption() {
        if (App.get().push().batteryExempt()) return;
        try {
            startActivity(new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:" + getPackageName())));
        } catch (ActivityNotFoundException e) {
            try { startActivity(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)); } catch (ActivityNotFoundException ignore) { /* */ }
        }
    }

    @Override protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == FileChooser.REQUEST) chooser.onResult(resultCode, data);
    }
}
