package ru.kovcheg.kovapp;

import ru.kovcheg.kovapp.web.NavigationPolicy;
import ru.kovcheg.kovapp.web.WebRules;

/**
 * Настройки сборки (kovapp.properties → BuildConfig). Адрес сервера один на
 * всё приложение: интерфейс открывается «как будто» с сервера, поэтому
 * запросы к API идут на свой же адрес, как в браузере.
 */
public final class AppConfig {
    private AppConfig() {}

    public static final NavigationPolicy NAV = new NavigationPolicy(BuildConfig.SERVER_ORIGIN, BuildConfig.APP_PATH);

    /** Отдельный адрес API (если задан apiOrigin) */
    private static final NavigationPolicy API = BuildConfig.API_ORIGIN.trim().isEmpty() ? null
            : new NavigationPolicy(BuildConfig.API_ORIGIN.trim(), "/");

    /** Адрес принадлежит своему серверу: странице или API */
    public static boolean ownServer(String url) {
        return NAV.sameOrigin(url) || (API != null && API.sameOrigin(url));
    }

    public static String origin() { return NAV.origin(); }
    public static String appPath() { return NAV.appPath(); }
    public static String startUrl() { return NAV.startUrl(); }

    public static String csp() {
        String c = BuildConfig.CSP_OVERRIDE;
        if (c != null && !c.trim().isEmpty()) return c.trim();
        // отдельный адрес API — как переменная HOST у сервера
        return WebRules.cspWithHost(WebRules.DEFAULT_CSP, BuildConfig.API_ORIGIN.trim().replaceAll("/+$", ""));
    }

    public static boolean webPatches() { return BuildConfig.WEB_PATCHES; }

    /** Живые файлы интерфейса с ПК (tools/devserver.py) — только в отладочной сборке */
    public static String devAssetsUrl() {
        return BuildConfig.DEBUG && BuildConfig.DEV_ASSETS_URL != null ? BuildConfig.DEV_ASSETS_URL.trim() : "";
    }

    public static boolean fcmConfigured() {
        return !BuildConfig.FCM_PROJECT_ID.isEmpty() && !BuildConfig.FCM_APP_ID.isEmpty()
                && !BuildConfig.FCM_API_KEY.isEmpty() && !BuildConfig.FCM_SENDER_ID.isEmpty();
    }

    /** Минимальная версия движка WebView: ниже интерфейс работать не будет */
    public static final int MIN_WEBVIEW_MAJOR = 90;
    /** С этой версии оформление совпадает с браузером полностью (color-mix, :has, dvh) */
    public static final int FULL_WEBVIEW_MAJOR = 111;

    public static String userAgentSuffix() {
        return " KovappAndroid/" + BuildConfig.VERSION_NAME;
    }
}
