package ru.kovcheg.kovapp.web;

import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;

import ru.kovcheg.kovapp.core.Json;

/**
 * Протокол моста «страница ↔ приложение». Страница шлёт
 *   {"id": 7, "cmd": "notify.show", "args": {...}}
 * приложение отвечает {"id": 7, "ok": true, "result": ...} или
 * {"id": 7, "ok": false, "error": "..."}, а события шлёт как
 * {"event": "push", "data": {...}}.
 *
 * Всё, что приходит со страницы, считается недоверенным: размер, типы и
 * границы проверяются до того, как команда дойдёт до обработчика.
 */
public final class BridgeProtocol {
    private BridgeProtocol() {}

    public static final int MAX_MESSAGE = 3 * 1024 * 1024;
    private static final Pattern CMD = Pattern.compile("^[a-z]{2,16}(\\.[a-zA-Z]{2,24})?$");

    public static final class BadRequest extends RuntimeException {
        public final long id;
        public BadRequest(long id, String msg) { super(msg); this.id = id; }
    }

    public static final class Request {
        public final long id;
        public final String cmd;
        public final Args args;
        Request(long id, String cmd, Args args) { this.id = id; this.cmd = cmd; this.args = args; }
    }

    public static Request parse(String message) {
        if (message == null || message.length() > MAX_MESSAGE) throw new BadRequest(0, "сообщение слишком большое");
        Map<String, Object> m;
        try {
            m = Json.parseObject(message);
        } catch (IllegalArgumentException e) {
            throw new BadRequest(0, "неверный JSON");
        }
        Object idv = m.get("id");
        if (!(idv instanceof Long) || (Long) idv <= 0 || (Long) idv > Integer.MAX_VALUE) throw new BadRequest(0, "неверный id");
        long id = (Long) idv;
        String cmd = Json.str(m, "cmd", null);
        if (cmd == null || !CMD.matcher(cmd).matches()) throw new BadRequest(id, "неверная команда");
        Object a = m.get("args");
        if (a != null && !(a instanceof Map)) throw new BadRequest(id, "args должен быть объектом");
        @SuppressWarnings("unchecked") Map<String, Object> args = a == null ? Collections.<String, Object>emptyMap() : (Map<String, Object>) a;
        return new Request(id, cmd, new Args(id, args));
    }

    public static String ok(long id, Object result) {
        return Json.stringify(Json.map("id", id, "ok", true, "result", result));
    }

    public static String error(long id, String message) {
        return Json.stringify(Json.map("id", id, "ok", false, "error", message == null ? "ошибка" : message));
    }

    public static String event(String name, Object data) {
        return Json.stringify(Json.map("event", name, "data", data));
    }

    /** Проверенное чтение аргументов команды */
    public static final class Args {
        private final long id;
        private final Map<String, Object> m;
        Args(long id, Map<String, Object> m) { this.id = id; this.m = m; }

        public Map<String, Object> raw() { return m; }
        public boolean has(String k) { return m.get(k) != null; }

        public String str(String k, int maxLen) {
            Object v = m.get(k);
            if (!(v instanceof String)) throw new BadRequest(id, k + ": ожидалась строка");
            String s = (String) v;
            if (s.length() > maxLen) throw new BadRequest(id, k + ": слишком длинно");
            return s;
        }

        public String optStr(String k, int maxLen, String def) {
            return m.get(k) == null ? def : str(k, maxLen);
        }

        public long num(String k, long min, long max) {
            Object v = m.get(k);
            long n;
            if (v instanceof Long) n = (Long) v;
            else if (v instanceof Double && ((Double) v) == Math.rint((Double) v)) n = ((Double) v).longValue();
            else throw new BadRequest(id, k + ": ожидалось целое число");
            if (n < min || n > max) throw new BadRequest(id, k + ": вне допустимых границ");
            return n;
        }

        public long optNum(String k, long min, long max, long def) { return m.get(k) == null ? def : num(k, min, max); }

        public double dbl(String k, double min, double max) {
            Object v = m.get(k);
            if (!(v instanceof Number)) throw new BadRequest(id, k + ": ожидалось число");
            double d = ((Number) v).doubleValue();
            if (Double.isNaN(d) || d < min || d > max) throw new BadRequest(id, k + ": вне допустимых границ");
            return d;
        }

        public boolean optBool(String k, boolean def) {
            Object v = m.get(k);
            return v instanceof Boolean ? (Boolean) v : def;
        }

        @SuppressWarnings("unchecked")
        public Map<String, Object> optObj(String k) {
            Object v = m.get(k);
            if (v == null) return null;
            if (!(v instanceof Map)) throw new BadRequest(id, k + ": ожидался объект");
            return (Map<String, Object>) v;
        }

        @SuppressWarnings("unchecked")
        public List<Object> list(String k, int maxItems) {
            Object v = m.get(k);
            if (!(v instanceof List)) throw new BadRequest(id, k + ": ожидался массив");
            List<Object> l = (List<Object>) v;
            if (l.size() > maxItems) throw new BadRequest(id, k + ": слишком много элементов");
            return l;
        }
    }
}
