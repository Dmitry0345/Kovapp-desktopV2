package ru.kovcheg.kovapp.push;

import java.util.Map;

import ru.kovcheg.kovapp.core.Json;
import ru.kovcheg.kovapp.web.NavigationPolicy;

/**
 * Уведомление от сервера — те же поля, что у Web Push (push.py):
 * title, body, url, tag, kind, probe; плюс uid получателя и id для
 * подтверждения доставки. Всё проверяется и обрезается: текст пришёл по сети.
 */
public final class PushMessage {
    public String id = "";
    public long uid;
    public String title = "Ковчег";
    public String body = "";
    public String url = "#/notifications";
    public String tag = "kovcheg";
    public String kind = "";
    public String probe = "";
    public Map<String, Object> raw;

    public static PushMessage parse(String json) {
        Map<String, Object> m = Json.parseObject(json);
        PushMessage p = new PushMessage();
        p.raw = m;
        p.id = clip(Json.str(m, "id", ""), 64);
        p.uid = Json.num(m, "uid", 0);
        String t = Json.str(m, "title", "");
        if (!t.isEmpty()) p.title = clip(t, 200);
        p.body = clip(Json.str(m, "body", ""), 1000);
        String url = Json.str(m, "url", "");
        // сервер шлёт «#/messenger/12»; всё остальное — в раздел уведомлений
        if (NavigationPolicy.isSafeRoute(url)) p.url = url;
        String tag = Json.str(m, "tag", "");
        if (!tag.isEmpty()) p.tag = clip(tag.replaceAll("[^A-Za-z0-9_:.\\-]", ""), 64);
        if (p.tag.isEmpty()) p.tag = "kovcheg";
        p.kind = clip(Json.str(m, "kind", ""), 32);
        String probe = Json.str(m, "probe", "");
        if (probe.matches("[0-9a-f]{32}")) p.probe = probe;
        return p;
    }

    /** Канал уведомлений Android по виду события */
    public String channel() {
        if ("chat".equals(kind)) return "chat";
        if ("group_added".equals(kind)) return "group";
        return "notification";
    }

    static String clip(String s, int max) { return s == null ? "" : (s.length() > max ? s.substring(0, max) : s); }
}
