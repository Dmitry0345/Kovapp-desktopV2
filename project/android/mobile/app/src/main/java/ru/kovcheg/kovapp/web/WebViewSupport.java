package ru.kovcheg.kovapp.web;

import android.content.Context;
import android.content.pm.PackageInfo;

import androidx.webkit.WebViewCompat;

import ru.kovcheg.kovapp.AppConfig;

/** Проверка движка WebView: слишком старый не справится с интерфейсом. */
public final class WebViewSupport {
    private WebViewSupport() {}

    public static final class Status {
        public boolean ok;
        public int major;
        public String packageName = "com.google.android.webview";
        public String version = "";
    }

    public static Status check(Context c) {
        Status s = new Status();
        PackageInfo pi = null;
        try { pi = WebViewCompat.getCurrentWebViewPackage(c); } catch (RuntimeException e) { /* WebView отключён */ }
        if (pi != null) {
            s.packageName = pi.packageName;
            s.version = pi.versionName == null ? "" : pi.versionName;
            s.major = major(s.version);
        }
        s.ok = pi != null && s.major >= AppConfig.MIN_WEBVIEW_MAJOR && Bridge.supported();
        return s;
    }

    static int major(String v) {
        try {
            int dot = v.indexOf('.');
            return Integer.parseInt(dot > 0 ? v.substring(0, dot) : v);
        } catch (NumberFormatException e) {
            return 0;
        }
    }
}
