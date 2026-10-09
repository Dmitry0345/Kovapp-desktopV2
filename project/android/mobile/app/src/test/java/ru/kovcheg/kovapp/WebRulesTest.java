package ru.kovcheg.kovapp;

import static org.junit.Assert.*;

import java.io.File;
import java.util.Map;

import org.junit.Test;

import ru.kovcheg.kovapp.web.Overlay;
import ru.kovcheg.kovapp.web.WebRules;

public class WebRulesTest {
    @Test public void normalizeBlocksTraversal() {
        assertEquals("index.html", WebRules.normalize(""));
        assertEquals("index.html", WebRules.normalize("/"));
        assertEquals("js/app.js", WebRules.normalize("js/app.js"));
        assertEquals("js/app.js", WebRules.normalize("./js//app.js"));
        assertEquals("chat/index.html", WebRules.normalize("chat/"));
        assertEquals("stickers/cat/ok.svg", WebRules.normalize("stickers/cat/ok%2Esvg"));
        assertNull(WebRules.normalize("../server/.env"));
        assertNull(WebRules.normalize("js/../../x"));
        assertNull(WebRules.normalize("%2e%2e/secret"));
        assertNull(WebRules.normalize("js\\..\\x"));
        assertNull(WebRules.normalize("js/%00.js"));
        assertNull(WebRules.normalize("c:/windows"));
        assertNull(WebRules.normalize("bad%zz"));
    }

    @Test public void mimeAndHeaders() {
        assertEquals("text/javascript", WebRules.mimeFor("vendor/pdfjs/pdf.min.mjs"));
        assertEquals("application/wasm", WebRules.mimeFor("a/openjpeg.wasm"));
        assertEquals("image/svg+xml", WebRules.mimeFor("icons/icon.svg"));
        assertEquals("application/octet-stream", WebRules.mimeFor("noext"));
        Map<String, String> h = WebRules.headers("text/html", WebRules.DEFAULT_CSP, false);
        assertEquals("text/html; charset=utf-8", h.get("Content-Type"));
        assertEquals("nosniff", h.get("X-Content-Type-Options"));
        assertTrue(h.get("Content-Security-Policy").contains("script-src 'self' 'wasm-unsafe-eval'"));
        assertNull(WebRules.headers("image/png", WebRules.DEFAULT_CSP, false).get("Content-Security-Policy"));
    }

    @Test public void cspMatchesServer() throws Exception {
        // Политика обязана совпадать с серверной: иначе страница в приложении
        // работала бы под другими правилами, чем в браузере
        String core = TestPaths.text(new File(TestPaths.mobile().getParentFile(), "server/server_app/core.py"));
        String block = core.substring(core.indexOf("DEFAULT_CSP = ("), core.indexOf("\n)\n", core.indexOf("DEFAULT_CSP = (")));
        StringBuilder csp = new StringBuilder();
        java.util.regex.Matcher m = java.util.regex.Pattern.compile("f?\"([^\"]*)\"").matcher(block);
        while (m.find()) csp.append(m.group(1));
        String expected = csp.toString()
                .replace("{KLIPY_MEDIA}", "https://static.klipy.com https://static1.klipy.com https://static2.klipy.com")
                .replace("{KLIPY_API}", "https://api.klipy.com");
        assertEquals(expected, WebRules.DEFAULT_CSP);
    }

    @Test public void cspWithApiHostLikeServer() {
        // ожидание получено функцией _csp_with_host сервера (HOST=https://api.example.ru)
        String got = WebRules.cspWithHost(WebRules.DEFAULT_CSP, "https://api.example.ru");
        assertTrue(got.contains("connect-src 'self' https://api.example.ru wss://api.example.ru ws: wss: https://api.klipy.com;"));
        assertTrue(got.contains("img-src 'self' https://api.example.ru data: blob:"));
        assertTrue(got.contains("media-src 'self' https://api.example.ru blob:"));
        assertEquals(WebRules.DEFAULT_CSP, WebRules.cspWithHost(WebRules.DEFAULT_CSP, ""));
    }

    @Test public void rangeParsing() {
        assertArrayEquals(new byte[0], new byte[0]);
        long[] r = WebRules.parseRange("bytes=0-0", 10);
        assertEquals(0, r[0]); assertEquals(0, r[1]);
        r = WebRules.parseRange("bytes=5-", 10);
        assertEquals(5, r[0]); assertEquals(9, r[1]);
        r = WebRules.parseRange("bytes=-3", 10);
        assertEquals(7, r[0]); assertEquals(9, r[1]);
        r = WebRules.parseRange("bytes=2-100", 10);
        assertEquals(9, r[1]);
        assertNull(WebRules.parseRange("bytes=10-", 10));
        assertNull(WebRules.parseRange("bytes=3-1", 10));
        assertNull(WebRules.parseRange("items=0-1", 10));
        assertNull(WebRules.parseRange("bytes=0-1,4-5", 10));
    }

    private static Overlay overlay(boolean patches) throws Exception {
        return new Overlay(TestPaths.text(new File(TestPaths.overlay(), "inject.json")),
                TestPaths.text(new File(TestPaths.overlay(), "web-patches.json")), patches);
    }

    @Test public void injectionIntoRealPages() throws Exception {
        Overlay o = overlay(true);
        for (String page : new String[]{"index.html", "doc.html", "sheet.html"}) {
            String html = TestPaths.text(new File(TestPaths.webApp(), page));
            String out = o.injectHtml(html);
            int host = out.indexOf("js/core/api-host.js");
            int state = out.indexOf("native/kovapp-state.js");
            int bridge = out.indexOf("native/kovapp-bridge.js");
            int late = out.indexOf("native/kovapp-app.js");
            assertTrue(page + ": ранние скрипты после api-host.js", host > 0 && state > host && bridge > state);
            assertTrue(page + ": поздний скрипт есть", late > bridge);
            if (page.equals("index.html")) {
                int app = out.indexOf("<script src=\"js/app.js\"></script>");
                assertTrue("поздний скрипт перед app.js", late < app);
                assertTrue("и после всех разделов", late > out.indexOf("js/views/csv.js"));
            } else {
                assertTrue(page + ": в конце body", late < out.indexOf("</body>"));
            }
            // остальное содержимое не тронуто
            String back = out.replace("<script src=\"native/kovapp-state.js\"></script><script src=\"native/kovapp-bridge.js\"></script>", "")
                    .replace("<script src=\"native/kovapp-app.js\"></script>", "");
            assertEquals(page + ": страница без надстройки совпадает с исходной", html, back);
        }
    }

    @Test public void patchesApplyExactlyOnce() throws Exception {
        Overlay o = overlay(true);
        assertEquals(9, o.patches().size());
        for (Overlay.Patch p : o.patches()) {
            String src = TestPaths.text(new File(TestPaths.webApp(), p.file));
            String out = o.patchText(p.file, src);
            assertTrue(p.file + ": замена применилась", out.contains(p.replace));
            assertFalse(p.file + ": старый текст ушёл", out.contains(p.find));
        }
        assertEquals("предупреждений нет", 0, o.warnings().size());
        // отключённые правки ничего не меняют
        Overlay off = overlay(false);
        String settings = TestPaths.text(new File(TestPaths.webApp(), "js/views/settings.js"));
        assertEquals(settings, off.patchText("js/views/settings.js", settings));
        // несовпадение — пропуск с предупреждением, а не порча файла
        assertEquals("x", o.patchText("js/core/downloads.js", "x"));
        assertEquals(1, o.warnings().size());
    }
}
