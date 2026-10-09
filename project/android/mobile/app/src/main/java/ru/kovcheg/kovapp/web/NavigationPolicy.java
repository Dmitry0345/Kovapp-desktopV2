package ru.kovcheg.kovapp.web;

import java.net.URI;
import java.util.Locale;
import java.util.regex.Pattern;

/**
 * Куда можно переходить. Внутри приложения открывается только адрес своего
 * сервера; сторонние сайты уходят во внешний браузер, опасные схемы
 * (javascript:, file:, content:, intent:, data:) не открываются вовсе.
 */
public final class NavigationPolicy {
    public enum Kind {
        /** страница приложения (из APK): /app/… */
        APP,
        /** остальные адреса своего сервера: файлы, скачивание */
        SERVER,
        /** чужой сайт — во внешний браузер */
        EXTERNAL,
        /** почта, телефон — системному приложению */
        HANDOFF,
        /** не открывать */
        BLOCK
    }

    private static final Pattern ROUTE = Pattern.compile("^#/[A-Za-z0-9_/\\-]{0,200}$");

    private final String scheme, host, appPath;
    private final int port;

    public NavigationPolicy(String serverOrigin, String appPath) {
        URI u = URI.create(serverOrigin);
        scheme = lower(u.getScheme());
        host = lower(u.getHost());
        port = effectivePort(scheme, u.getPort());
        this.appPath = appPath.endsWith("/") ? appPath : appPath + "/";
        if (!("http".equals(scheme) || "https".equals(scheme)) || host == null) {
            throw new IllegalArgumentException("Адрес сервера должен быть http(s)://хост[:порт]");
        }
    }

    public String origin() {
        boolean def = ("http".equals(scheme) && port == 80) || ("https".equals(scheme) && port == 443);
        return scheme + "://" + host + (def ? "" : ":" + port);
    }

    public String appPath() { return appPath; }
    public String startUrl() { return origin() + appPath + "index.html"; }

    private static String lower(String s) { return s == null ? null : s.toLowerCase(Locale.ROOT); }

    private static int effectivePort(String scheme, int port) {
        if (port > 0) return port;
        return "https".equals(scheme) ? 443 : 80;
    }

    public boolean sameOrigin(String url) {
        try {
            URI u = new URI(url);
            String s = lower(u.getScheme());
            return s != null && s.equals(scheme) && host.equals(lower(u.getHost())) && port == effectivePort(s, u.getPort());
        } catch (Exception e) {
            return false;
        }
    }

    public Kind classify(String url) {
        if (url == null) return Kind.BLOCK;
        String trimmed = url.trim();
        if (trimmed.equals("about:blank")) return Kind.APP;
        URI u;
        try {
            u = new URI(trimmed);
        } catch (Exception e) {
            return Kind.BLOCK;
        }
        String s = lower(u.getScheme());
        if (s == null) return Kind.BLOCK;
        switch (s) {
            case "http":
            case "https":
                if (u.getHost() == null) return Kind.BLOCK;
                if (sameOrigin(trimmed)) {
                    String path = u.getRawPath() == null ? "/" : u.getRawPath();
                    return path.startsWith(appPath) ? Kind.APP : Kind.SERVER;
                }
                return Kind.EXTERNAL;
            case "mailto":
            case "tel":
                return Kind.HANDOFF;
            default:
                return Kind.BLOCK;
        }
    }

    /** Относительный путь внутри /app/ для адреса своего сервера, иначе null */
    public String appRelative(String url) {
        if (classify(url) != Kind.APP) return null;
        try {
            String path = new URI(url.trim()).getRawPath();
            if (path == null) return null;
            return path.substring(appPath.length());
        } catch (Exception e) {
            return null;
        }
    }

    /** Маршрут внутри приложения из уведомления: только вида #/раздел/номер */
    public static boolean isSafeRoute(String route) {
        return route != null && ROUTE.matcher(route).matches();
    }
}
