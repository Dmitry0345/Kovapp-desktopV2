package ru.kovcheg.kovapp.web;

import android.graphics.Bitmap;
import android.net.Uri;
import android.net.http.SslError;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.SslErrorHandler;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import ru.kovcheg.kovapp.AppConfig;

/** Навигация и выдача файлов для главного и дочерних окон. */
public final class AppWebViewClient extends WebViewClient {
    public interface Host {
        AssetServer assets();
        void openExternal(Uri uri);
        void onPageStarted(WebView view, String url);
        void onPageFinished(WebView view, String url);
        void onRendererGone(WebView view);
        void closeChild(WebView view);
    }

    private final Host host;
    private final boolean child;

    public AppWebViewClient(Host host, boolean child) {
        this.host = host;
        this.child = child;
    }

    @Override public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
        return host.assets().handle(request);
    }

    @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
        if (!request.isForMainFrame()) return false;
        String url = request.getUrl().toString();
        switch (AppConfig.NAV.classify(url)) {
            case APP:
            case SERVER:
                return false;
            case EXTERNAL:
            case HANDOFF:
                host.openExternal(request.getUrl());
                // пустое дочернее окно, открытое ради внешней ссылки, закрываем
                if (child && (view.getUrl() == null || view.getUrl().equals("about:blank"))) host.closeChild(view);
                return true;
            default:
                return true;
        }
    }

    @Override public void onPageStarted(WebView view, String url, Bitmap favicon) { host.onPageStarted(view, url); }

    @Override public void onPageFinished(WebView view, String url) { host.onPageFinished(view, url); }

    /** Ошибка сертификата — никогда не продолжаем (защита от подмены сервера) */
    @Override public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) { handler.cancel(); }

    @Override public boolean onRenderProcessGone(WebView view, RenderProcessGoneDetail detail) {
        host.onRendererGone(view);
        return true;
    }
}
