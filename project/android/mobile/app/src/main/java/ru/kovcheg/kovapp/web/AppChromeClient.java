package ru.kovcheg.kovapp.web;

import android.graphics.Bitmap;
import android.net.Uri;
import android.os.Message;
import android.util.Log;
import android.view.View;
import android.webkit.ConsoleMessage;
import android.webkit.GeolocationPermissions;
import android.webkit.PermissionRequest;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebView;

import ru.kovcheg.kovapp.BuildConfig;

/** Окна, выбор файлов, полноэкранное видео. Камера, микрофон, геопозиция — запрещены, как на сервере. */
public final class AppChromeClient extends WebChromeClient {
    public interface Host {
        boolean showFileChooser(ValueCallback<Uri[]> callback, FileChooserParams params);
        boolean openChild(Message resultMsg);
        void closeChild(WebView view);
        void showCustom(View view, CustomViewCallback callback);
        void hideCustom();
        /** Камера и микрофон для страницы (шторка вложений): решает Activity */
        void permission(PermissionRequest request);
    }

    private final Host host;

    public AppChromeClient(Host host) { this.host = host; }

    @Override public boolean onShowFileChooser(WebView webView, ValueCallback<Uri[]> callback, FileChooserParams params) {
        return host.showFileChooser(callback, params);
    }

    @Override public boolean onCreateWindow(WebView view, boolean isDialog, boolean isUserGesture, Message resultMsg) {
        return host.openChild(resultMsg);
    }

    @Override public void onCloseWindow(WebView window) { host.closeChild(window); }

    @Override public void onPermissionRequest(PermissionRequest request) { host.permission(request); }

    @Override public void onGeolocationPermissionsShowPrompt(String origin, GeolocationPermissions.Callback callback) {
        callback.invoke(origin, false, false);
    }

    @Override public void onShowCustomView(View view, CustomViewCallback callback) { host.showCustom(view, callback); }

    @Override public void onHideCustomView() { host.hideCustom(); }

    // видео без своей обложки: вместо серой заглушки WebView со значком «воспроизвести» — прозрачная,
    // виден фон плитки (обложку готового видео задаёт страница)
    @Override public Bitmap getDefaultVideoPoster() {
        return Bitmap.createBitmap(1, 1, Bitmap.Config.ARGB_8888);
    }

    @Override public boolean onConsoleMessage(ConsoleMessage m) {
        if (BuildConfig.DEBUG) Log.d("KovappJS", m.message() + " @" + m.sourceId() + ":" + m.lineNumber());
        return true;
    }
}
