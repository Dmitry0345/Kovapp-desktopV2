package ru.kovcheg.kovapp.core;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Небольшой разбор и запись JSON без зависимостей от Android.
 * Объекты — LinkedHashMap, массивы — ArrayList, числа — Long или Double.
 * Своя реализация, а не org.json: ядро проверяется тестами на обычной JVM.
 */
public final class Json {
    private Json() {}

    /** Предел вложенности: защита от переполнения стека на злонамеренном вводе */
    private static final int MAX_DEPTH = 64;

    public static Object parse(String text) {
        if (text == null) throw new IllegalArgumentException("json: null");
        Parser p = new Parser(text);
        p.skipWs();
        Object v = p.value(0);
        p.skipWs();
        if (p.pos != text.length()) throw p.error("лишние символы");
        return v;
    }

    @SuppressWarnings("unchecked")
    public static Map<String, Object> parseObject(String text) {
        Object v = parse(text);
        if (!(v instanceof Map)) throw new IllegalArgumentException("json: ожидался объект");
        return (Map<String, Object>) v;
    }

    public static String stringify(Object value) {
        StringBuilder sb = new StringBuilder();
        write(sb, value, 0);
        return sb.toString();
    }

    public static String quote(String s) {
        StringBuilder sb = new StringBuilder(s.length() + 2);
        writeString(sb, s);
        return sb.toString();
    }

    // ── запись ───────────────────────────────────────────────────────────
    @SuppressWarnings("unchecked")
    private static void write(StringBuilder sb, Object v, int depth) {
        if (depth > MAX_DEPTH) throw new IllegalArgumentException("json: слишком глубокая вложенность");
        if (v == null) { sb.append("null"); return; }
        if (v instanceof String) { writeString(sb, (String) v); return; }
        if (v instanceof Boolean) { sb.append(((Boolean) v) ? "true" : "false"); return; }
        if (v instanceof Double || v instanceof Float) {
            double d = ((Number) v).doubleValue();
            if (Double.isNaN(d) || Double.isInfinite(d)) { sb.append("null"); return; }
            if (d == Math.rint(d) && Math.abs(d) < 1e15) sb.append((long) d);
            else sb.append(d);
            return;
        }
        if (v instanceof Number) { sb.append(((Number) v).longValue()); return; }
        if (v instanceof Map) {
            sb.append('{');
            boolean first = true;
            for (Map.Entry<Object, Object> e : ((Map<Object, Object>) v).entrySet()) {
                if (!first) sb.append(',');
                first = false;
                writeString(sb, String.valueOf(e.getKey()));
                sb.append(':');
                write(sb, e.getValue(), depth + 1);
            }
            sb.append('}');
            return;
        }
        if (v instanceof List) {
            sb.append('[');
            boolean first = true;
            for (Object o : (List<Object>) v) {
                if (!first) sb.append(',');
                first = false;
                write(sb, o, depth + 1);
            }
            sb.append(']');
            return;
        }
        if (v instanceof Object[]) {
            List<Object> list = new ArrayList<>();
            for (Object o : (Object[]) v) list.add(o);
            write(sb, list, depth);
            return;
        }
        writeString(sb, String.valueOf(v));
    }

    private static void writeString(StringBuilder sb, String s) {
        sb.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                case '\b': sb.append("\\b"); break;
                case '\f': sb.append("\\f"); break;
                default:
                    // U+2028/2029 экранируем: внутри <script> и evaluateJavascript
                    // они ломали бы строку как перевод строки
                    if (c < 0x20 || c == 0x2028 || c == 0x2029 || c == '<' || c == '>') {
                        sb.append(String.format("\\u%04x", (int) c));
                    } else {
                        sb.append(c);
                    }
            }
        }
        sb.append('"');
    }

    // ── разбор ───────────────────────────────────────────────────────────
    private static final class Parser {
        final String s;
        int pos;

        Parser(String s) { this.s = s; }

        IllegalArgumentException error(String what) {
            return new IllegalArgumentException("json: " + what + " в позиции " + pos);
        }

        void skipWs() {
            while (pos < s.length()) {
                char c = s.charAt(pos);
                if (c == ' ' || c == '\n' || c == '\r' || c == '\t') pos++;
                else break;
            }
        }

        Object value(int depth) {
            if (depth > MAX_DEPTH) throw error("слишком глубокая вложенность");
            if (pos >= s.length()) throw error("неожиданный конец");
            char c = s.charAt(pos);
            switch (c) {
                case '{': return object(depth);
                case '[': return array(depth);
                case '"': return string();
                case 't': literal("true"); return Boolean.TRUE;
                case 'f': literal("false"); return Boolean.FALSE;
                case 'n': literal("null"); return null;
                default:
                    if (c == '-' || (c >= '0' && c <= '9')) return number();
                    throw error("неожиданный символ '" + c + "'");
            }
        }

        void literal(String word) {
            if (!s.startsWith(word, pos)) throw error("ожидалось " + word);
            pos += word.length();
        }

        Map<String, Object> object(int depth) {
            Map<String, Object> m = new LinkedHashMap<>();
            pos++; // {
            skipWs();
            if (pos < s.length() && s.charAt(pos) == '}') { pos++; return m; }
            while (true) {
                skipWs();
                if (pos >= s.length() || s.charAt(pos) != '"') throw error("ожидался ключ");
                String key = string();
                skipWs();
                if (pos >= s.length() || s.charAt(pos) != ':') throw error("ожидалось ':'");
                pos++;
                skipWs();
                m.put(key, value(depth + 1));
                skipWs();
                if (pos >= s.length()) throw error("неожиданный конец объекта");
                char c = s.charAt(pos++);
                if (c == '}') return m;
                if (c != ',') throw error("ожидалось ',' или '}'");
            }
        }

        List<Object> array(int depth) {
            List<Object> list = new ArrayList<>();
            pos++; // [
            skipWs();
            if (pos < s.length() && s.charAt(pos) == ']') { pos++; return list; }
            while (true) {
                skipWs();
                list.add(value(depth + 1));
                skipWs();
                if (pos >= s.length()) throw error("неожиданный конец массива");
                char c = s.charAt(pos++);
                if (c == ']') return list;
                if (c != ',') throw error("ожидалось ',' или ']'");
            }
        }

        String string() {
            pos++; // "
            StringBuilder sb = new StringBuilder();
            while (true) {
                if (pos >= s.length()) throw error("незакрытая строка");
                char c = s.charAt(pos++);
                if (c == '"') return sb.toString();
                if (c == '\\') {
                    if (pos >= s.length()) throw error("обрыв экранирования");
                    char e = s.charAt(pos++);
                    switch (e) {
                        case '"': sb.append('"'); break;
                        case '\\': sb.append('\\'); break;
                        case '/': sb.append('/'); break;
                        case 'b': sb.append('\b'); break;
                        case 'f': sb.append('\f'); break;
                        case 'n': sb.append('\n'); break;
                        case 'r': sb.append('\r'); break;
                        case 't': sb.append('\t'); break;
                        case 'u':
                            if (pos + 4 > s.length()) throw error("обрыв \\u");
                            try {
                                sb.append((char) Integer.parseInt(s.substring(pos, pos + 4), 16));
                            } catch (NumberFormatException ex) {
                                throw error("неверный \\u");
                            }
                            pos += 4;
                            break;
                        default: throw error("неизвестное экранирование");
                    }
                } else {
                    if (c < 0x20) throw error("управляющий символ в строке");
                    sb.append(c);
                }
            }
        }

        Number number() {
            int start = pos;
            if (s.charAt(pos) == '-') pos++;
            boolean frac = false;
            while (pos < s.length()) {
                char c = s.charAt(pos);
                if (c >= '0' && c <= '9') pos++;
                else if (c == '.' || c == 'e' || c == 'E' || c == '+' || c == '-') { frac = true; pos++; }
                else break;
            }
            String t = s.substring(start, pos);
            if (t.equals("-") || t.isEmpty()) throw error("неверное число");
            try {
                if (!frac) {
                    if (t.length() < 18) return Long.parseLong(t);
                    return Double.parseDouble(t);
                }
                return Double.parseDouble(t);
            } catch (NumberFormatException ex) {
                throw error("неверное число");
            }
        }
    }

    // ── удобные чтения из Map ─────────────────────────────────────────────
    public static String str(Map<String, Object> m, String key, String def) {
        Object v = m == null ? null : m.get(key);
        return v instanceof String ? (String) v : def;
    }

    public static long num(Map<String, Object> m, String key, long def) {
        Object v = m == null ? null : m.get(key);
        return v instanceof Number ? ((Number) v).longValue() : def;
    }

    public static double dbl(Map<String, Object> m, String key, double def) {
        Object v = m == null ? null : m.get(key);
        return v instanceof Number ? ((Number) v).doubleValue() : def;
    }

    public static boolean bool(Map<String, Object> m, String key, boolean def) {
        Object v = m == null ? null : m.get(key);
        return v instanceof Boolean ? (Boolean) v : def;
    }

    @SuppressWarnings("unchecked")
    public static Map<String, Object> obj(Map<String, Object> m, String key) {
        Object v = m == null ? null : m.get(key);
        return v instanceof Map ? (Map<String, Object>) v : null;
    }

    @SuppressWarnings("unchecked")
    public static List<Object> arr(Map<String, Object> m, String key) {
        Object v = m == null ? null : m.get(key);
        return v instanceof List ? (List<Object>) v : null;
    }

    /** Короткий способ собрать объект: Json.map("a", 1, "b", "x") */
    public static Map<String, Object> map(Object... kv) {
        Map<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i + 1 < kv.length; i += 2) m.put(String.valueOf(kv[i]), kv[i + 1]);
        return m;
    }
}
