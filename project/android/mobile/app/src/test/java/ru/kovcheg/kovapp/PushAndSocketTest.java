package ru.kovcheg.kovapp;

import static org.junit.Assert.*;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;

import org.junit.Test;

import ru.kovcheg.kovapp.core.B64;
import ru.kovcheg.kovapp.core.Json;
import ru.kovcheg.kovapp.net.WebSocketClient;
import ru.kovcheg.kovapp.push.PushCrypto;
import ru.kovcheg.kovapp.push.PushMessage;

public class PushAndSocketTest {
    @SuppressWarnings("unchecked")
    @Test public void decryptsServerPayloads() throws Exception {
        File fx = TestPaths.fixtures();
        if (fx == null || !new File(fx, "push_vectors.json").isFile()) return;
        Map<String, Object> v = Json.parseObject(TestPaths.text(new File(fx, "push_vectors.json")));
        byte[] key = B64.decode(Json.str(v, "key", ""));
        List<Object> vectors = Json.arr(v, "vectors");
        Map<String, Object> first = (Map<String, Object>) vectors.get(0);
        PushMessage m = PushMessage.parse(PushCrypto.open(key, Json.obj(first, "data")));
        assertEquals("n1", m.id);
        assertEquals(7, m.uid);
        assertEquals("Иван Петров", m.title);
        assertEquals("#/messenger/12", m.url);
        assertEquals("chat-12", m.tag);
        assertEquals("chat", m.channel());
        PushMessage second = PushMessage.parse(PushCrypto.open(key, Json.obj((Map<String, Object>) vectors.get(1), "data")));
        assertEquals("опасный адрес заменён", "#/notifications", second.url);
        assertEquals(300, second.body.length());
        PushMessage third = PushMessage.parse(PushCrypto.open(key, Json.obj((Map<String, Object>) vectors.get(2), "data")));
        assertEquals("0123456789abcdef0123456789abcdef", third.probe);
        // подмена байта — отказ
        Map<String, Object> bad = new java.util.LinkedHashMap<>(Json.obj(first, "data"));
        String c = (String) bad.get("c");
        bad.put("c", (c.charAt(0) == 'A' ? "B" : "A") + c.substring(1));
        try { PushCrypto.open(key, bad); fail("подделка должна отвергаться"); } catch (java.security.GeneralSecurityException expected) { /* ок */ }
        // чужой ключ — отказ
        byte[] other = key.clone();
        other[0] ^= 1;
        try { PushCrypto.open(other, Json.obj(first, "data")); fail("чужой ключ"); } catch (java.security.GeneralSecurityException expected) { /* ок */ }
    }

    // ── крошечный сервер WebSocket для проверки клиента ──
    interface Script { void run(InputStream in, OutputStream out) throws Exception; }

    static int serve(final Script script, final boolean badAccept, final AtomicReference<Throwable> error) throws Exception {
        final ServerSocket server = new ServerSocket(0);
        Thread t = new Thread(() -> {
            try (Socket s = server.accept()) {
                InputStream in = s.getInputStream();
                OutputStream out = s.getOutputStream();
                String key = null;
                StringBuilder line = new StringBuilder();
                int c, prev = 0;
                while ((c = in.read()) >= 0) {
                    if (c == '\n') {
                        String l = line.toString().trim();
                        if (l.isEmpty()) break;
                        if (l.toLowerCase().startsWith("sec-websocket-key:")) key = l.substring(18).trim();
                        line.setLength(0);
                    } else if (c != '\r') line.append((char) c);
                    prev = c;
                }
                String accept = B64.encode(MessageDigest.getInstance("SHA-1").digest((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").getBytes(StandardCharsets.ISO_8859_1)));
                if (badAccept) accept = "AAAA" + accept.substring(4);
                out.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + accept + "\r\n\r\n").getBytes(StandardCharsets.ISO_8859_1));
                out.flush();
                script.run(in, out);
            } catch (Throwable e) {
                error.set(e);
            } finally {
                try { server.close(); } catch (Exception ignore) { /* */ }
            }
        });
        t.setDaemon(true);
        t.start();
        return server.getLocalPort();
    }

    static void frame(OutputStream out, int b0, byte[] payload) throws Exception {
        ByteArrayOutputStream f = new ByteArrayOutputStream();
        f.write(b0);
        if (payload.length < 126) f.write(payload.length);
        else { f.write(126); f.write(payload.length >> 8); f.write(payload.length & 255); }
        f.write(payload);
        out.write(f.toByteArray());
        out.flush();
    }

    /** Кадр клиента: обязан быть замаскирован */
    static Object[] readClient(InputStream in) throws Exception {
        int b0 = in.read(), b1 = in.read();
        assertTrue("клиент маскирует кадры", (b1 & 0x80) != 0);
        int len = b1 & 0x7f;
        if (len == 126) len = (in.read() << 8) | in.read();
        byte[] mask = new byte[4];
        for (int i = 0; i < 4; i++) mask[i] = (byte) in.read();
        byte[] p = new byte[len];
        for (int i = 0; i < len; i++) p[i] = (byte) (in.read() ^ mask[i & 3]);
        return new Object[]{b0 & 0x0f, p};
    }

    @Test public void webSocketRoundTrip() throws Exception {
        final AtomicReference<Throwable> err = new AtomicReference<>();
        int port = serve((in, out) -> {
            Object[] hello = readClient(in);
            assertEquals(1, hello[0]);
            assertEquals("{\"type\":\"hello\"}", new String((byte[]) hello[1], StandardCharsets.UTF_8));
            frame(out, 0x81, "{\"type\":\"welcome\"}".getBytes(StandardCharsets.UTF_8));
            frame(out, 0x89, "hb".getBytes(StandardCharsets.UTF_8));             // ping
            Object[] pong = readClient(in);
            assertEquals("ответ на ping — pong", 0xA, pong[0]);
            assertEquals("hb", new String((byte[]) pong[1], StandardCharsets.UTF_8));
            frame(out, 0x01, "Прив".getBytes(StandardCharsets.UTF_8));           // начало, без FIN
            frame(out, 0x80, "ет".getBytes(StandardCharsets.UTF_8));              // продолжение с FIN
            byte[] big = new byte[70000];
            java.util.Arrays.fill(big, (byte) 'x');
            ByteArrayOutputStream f = new ByteArrayOutputStream();
            f.write(0x81); f.write(127);
            for (int i = 7; i >= 0; i--) f.write((int) ((long) big.length >>> (8 * i)) & 255);
            f.write(big);
            out.write(f.toByteArray()); out.flush();
            frame(out, 0x88, new byte[]{0x03, (byte) 0xE8});
            Object[] close = readClient(in);
            assertEquals(8, close[0]);
        }, false, err);
        try (WebSocketClient ws = WebSocketClient.connect("ws://127.0.0.1:" + port + "/ws/native-push", Collections.singletonMap("User-Agent", "Kovapp-test"), 3000, 5000)) {
            ws.sendText("{\"type\":\"hello\"}");
            assertEquals("{\"type\":\"welcome\"}", ws.receive());
            assertEquals("склейка фрагментов", "Привет", ws.receive());
            assertEquals(70000, ws.receive().length());
            assertNull("штатное закрытие", ws.receive());
        }
        Thread.sleep(100);
        if (err.get() != null) throw new AssertionError("сервер: " + err.get());
    }

    @Test public void webSocketRejectsWrongAccept() throws Exception {
        final AtomicReference<Throwable> err = new AtomicReference<>();
        int port = serve((in, out) -> { Thread.sleep(200); }, true, err);
        try {
            WebSocketClient.connect("ws://127.0.0.1:" + port + "/x", null, 3000, 3000).close();
            fail("неверный Sec-WebSocket-Accept должен отвергаться");
        } catch (java.io.IOException expected) {
            assertTrue(expected.getMessage().contains("неверный ответ"));
        }
    }

    @Test public void webSocketRejectsHeaderInjection() throws Exception {
        try {
            WebSocketClient.connect("ws://127.0.0.1:9/x", Collections.singletonMap("X", "a\r\nEvil: 1"), 300, 300).close();
            fail("перевод строки в заголовке должен отвергаться");
        } catch (java.io.IOException expected) { /* ок: либо заголовок, либо нет соединения */ }
    }
}
