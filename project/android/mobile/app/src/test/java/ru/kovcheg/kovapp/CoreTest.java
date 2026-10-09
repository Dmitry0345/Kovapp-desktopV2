package ru.kovcheg.kovapp;

import static org.junit.Assert.*;

import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import java.util.Random;

import org.junit.Test;

import ru.kovcheg.kovapp.core.B64;
import ru.kovcheg.kovapp.core.Json;

public class CoreTest {
    @Test public void jsonRoundTrip() {
        String src = "{\"a\":1,\"b\":[true,false,null,\"x\\n\\\"y\\\"\"],\"c\":{\"d\":-2.5,\"e\":\"Ковчег \\u2028\"},\"big\":12345678901234}";
        Map<String, Object> m = Json.parseObject(src);
        assertEquals(1L, m.get("a"));
        assertEquals(-2.5, ((Number) Json.obj(m, "c").get("d")).doubleValue(), 1e-9);
        assertEquals("Ковчег \u2028", Json.obj(m, "c").get("e"));
        assertEquals(12345678901234L, m.get("big"));
        List<Object> b = Json.arr(m, "b");
        assertEquals("x\n\"y\"", b.get(3));
        Map<String, Object> again = Json.parseObject(Json.stringify(m));
        assertEquals(Json.stringify(m), Json.stringify(again));
    }

    @Test public void jsonEscapesForScripts() {
        String s = Json.stringify(Json.map("x", "</script><b>\u2028"));
        assertFalse(s.contains("</script>"));
        assertFalse(s.contains("\u2028"));
        assertEquals("</script><b>\u2028", Json.parseObject(s).get("x"));
    }

    @Test public void jsonRejectsGarbage() {
        String[] bad = {"", "{", "[1,]", "{\"a\" 1}", "tru", "\"abc", "{\"a\":1}x", "[\"\\q\"]", "01a"};
        for (String b : bad) {
            try { Json.parse(b); fail("должно было упасть: " + b); } catch (IllegalArgumentException expected) { /* ок */ }
        }
        StringBuilder deep = new StringBuilder();
        for (int i = 0; i < 200; i++) deep.append('[');
        try { Json.parse(deep.toString()); fail("глубина"); } catch (IllegalArgumentException expected) { /* ок */ }
    }

    @Test public void base64MatchesJdk() {
        Random r = new Random(7);
        for (int len = 0; len < 70; len++) {
            byte[] b = new byte[len];
            r.nextBytes(b);
            assertEquals(java.util.Base64.getEncoder().encodeToString(b), B64.encode(b));
            assertEquals(java.util.Base64.getUrlEncoder().withoutPadding().encodeToString(b), B64.encodeUrl(b));
            assertArrayEquals(b, B64.decode(B64.encode(b)));
            assertArrayEquals(b, B64.decode(B64.encodeUrl(b)));
        }
        assertEquals("d29ybGQ=", B64.encode("world".getBytes(StandardCharsets.US_ASCII)));
        assertEquals("0a0bff", B64.hex(new byte[]{10, 11, (byte) 255}));
    }
}
