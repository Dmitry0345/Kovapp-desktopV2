package ru.kovcheg.kovapp.web;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;

import ru.kovcheg.kovapp.core.Json;

/**
 * Надстройка приложения над веб-интерфейсом: подключение своих скриптов в
 * HTML и точечные замены текстов, которые в приложении были бы неверны
 * («значок замка слева от адреса» — адресной строки в приложении нет).
 *
 * Файлы web_app не меняются: всё применяется при выдаче из APK. Правила
 * лежат в overlay/inject.json и overlay/web-patches.json; тот же алгоритм
 * повторяет tools/devserver.py, и тест сверяет результаты побайтно.
 */
public final class Overlay {
    public static final class Patch {
        public final String file, find, replace, note;
        Patch(String file, String find, String replace, String note) {
            this.file = file; this.find = find; this.replace = replace; this.note = note;
        }
    }

    private final String headAfter, headFallback, tailBefore, tailFallback;
    private final List<String> headScripts, tailScripts;
    private final List<Patch> patches;
    private final boolean patchesEnabled;
    private final List<String> warnings = Collections.synchronizedList(new ArrayList<String>());

    public Overlay(String injectJson, String patchesJson, boolean patchesEnabled) {
        Map<String, Object> inj = Json.parseObject(injectJson);
        headAfter = Json.str(inj, "head_after", "");
        headFallback = Json.str(inj, "head_fallback", "</head>");
        tailBefore = Json.str(inj, "tail_before", "");
        tailFallback = Json.str(inj, "tail_fallback", "</body>");
        headScripts = strings(Json.arr(inj, "head_scripts"));
        tailScripts = strings(Json.arr(inj, "tail_scripts"));
        List<Patch> list = new ArrayList<>();
        if (patchesJson != null && !patchesJson.trim().isEmpty()) {
            Map<String, Object> root = Json.parseObject(patchesJson);
            List<Object> items = Json.arr(root, "patches");
            if (items != null) {
                for (Object o : items) {
                    if (!(o instanceof Map)) continue;
                    @SuppressWarnings("unchecked") Map<String, Object> m = (Map<String, Object>) o;
                    String f = Json.str(m, "file", null), fi = Json.str(m, "find", null), r = Json.str(m, "replace", null);
                    if (f != null && fi != null && r != null && !fi.isEmpty()) list.add(new Patch(f, fi, r, Json.str(m, "note", "")));
                }
            }
        }
        patches = Collections.unmodifiableList(list);
        this.patchesEnabled = patchesEnabled;
    }

    private static List<String> strings(List<Object> in) {
        List<String> out = new ArrayList<>();
        if (in != null) for (Object o : in) if (o instanceof String) out.add((String) o);
        return out;
    }

    public List<Patch> patches() { return patches; }
    public List<String> warnings() { return new ArrayList<>(warnings); }

    private static String tags(List<String> scripts) {
        StringBuilder sb = new StringBuilder();
        for (String s : scripts) sb.append("<script src=\"").append(s).append("\"></script>");
        return sb.toString();
    }

    /** Вставляет свои скрипты: ранние — сразу после api-host.js, поздние — перед app.js */
    public String injectHtml(String html) {
        String head = tags(headScripts), tail = tags(tailScripts);
        String out = html;
        if (!head.isEmpty()) out = insert(out, headAfter, true, headFallback, head);
        if (!tail.isEmpty()) out = insert(out, tailBefore, false, tailFallback, tail);
        return out;
    }

    private static String insert(String html, String anchor, boolean after, String fallback, String what) {
        int i = anchor.isEmpty() ? -1 : html.indexOf(anchor);
        if (i >= 0) {
            int at = after ? i + anchor.length() : i;
            return html.substring(0, at) + what + html.substring(at);
        }
        int f = html.lastIndexOf(fallback);
        if (f >= 0) return html.substring(0, f) + what + html.substring(f);
        return html + what;
    }

    /** Замены текстов в файле. Каждая обязана найтись ровно один раз, иначе пропускается. */
    public String patchText(String path, String text) {
        if (!patchesEnabled) return text;
        String out = text;
        for (Patch p : patches) {
            if (!p.file.equals(path)) continue;
            int first = out.indexOf(p.find);
            if (first < 0) { warn(path + ": не найден фрагмент «" + shorten(p.find) + "»"); continue; }
            if (out.indexOf(p.find, first + p.find.length()) >= 0) { warn(path + ": фрагмент встречается несколько раз «" + shorten(p.find) + "»"); continue; }
            out = out.substring(0, first) + p.replace + out.substring(first + p.find.length());
        }
        return out;
    }

    public boolean touches(String path) {
        if (path.endsWith(".html")) return true;
        if (!patchesEnabled) return false;
        for (Patch p : patches) if (p.file.equals(path)) return true;
        return false;
    }

    private void warn(String w) { if (!warnings.contains(w)) warnings.add(w); }
    private static String shorten(String s) { return s.length() > 60 ? s.substring(0, 57) + "…" : s; }
}
