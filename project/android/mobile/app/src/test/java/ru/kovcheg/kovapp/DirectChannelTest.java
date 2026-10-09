package ru.kovcheg.kovapp;

import static org.junit.Assert.*;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.Map;

import org.junit.Test;

import ru.kovcheg.kovapp.core.Json;
import ru.kovcheg.kovapp.net.HttpJson;
import ru.kovcheg.kovapp.net.WebSocketClient;
import ru.kovcheg.kovapp.push.PushMessage;

/**
 * Собственный канал уведомлений целиком: классы приложения против серверного
 * кода native_push (через тестовый сервер). Запускается, если задан адрес
 * тестового сервера -Dkovapp.mock=http://127.0.0.1:PORT (так делает
 * tools/offline-check/run_jvm_tests.py).
 */
public class DirectChannelTest {
    static String mock() { return System.getProperty("kovapp.mock", ""); }

    static Map<String, Object> login(String role) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(mock() + "/login").openConnection();
        c.setRequestMethod("POST");
        c.setDoOutput(true);
        c.setRequestProperty("Content-Type", "application/x-www-form-urlencoded");
        try (OutputStream out = c.getOutputStream()) { out.write(("username=" + role + "&password=test").getBytes(StandardCharsets.UTF_8)); }
        byte[] body = new byte[c.getContentLength() > 0 ? c.getContentLength() : 4096];
        int n = 0, r;
        while ((r = c.getInputStream().read(body, n, body.length - n)) > 0) n += r;
        return Json.parseObject(new String(body, 0, n, StandardCharsets.UTF_8));
    }

    static WebSocketClient connect() throws Exception {
        return WebSocketClient.connect(mock().replace("http://", "ws://") + "/ws/native-push",
                Collections.singletonMap("User-Agent", "KovappAndroid/test"), 5000, 10000);
    }

    static Map<String, Object> next(WebSocketClient ws) throws Exception {
        String text = ws.receive();
        assertNotNull("сервер закрыл канал", text);
        return Json.parseObject(text);
    }

    static String hello(String device, String secret) {
        return Json.stringify(Json.map("type", "hello", "device_id", device, "secret", secret, "app_version", "test", "once", false));
    }

    @Test public void directChannelEndToEnd() throws Exception {
        if (mock().isEmpty()) return;               // без тестового сервера — пропуск
        Map<String, Object> auth = login("worker");
        String token = Json.str(auth, "access_token", "");
        long uid = Json.num(auth, "user_id", 0);

        HttpJson.Response cfg = HttpJson.call("GET", HttpJson.join(mock(), "push/native/config"), token, null);
        assertTrue("сервер умеет уведомления для приложения", Json.bool(cfg.body, "enabled", false));

        String device = "0123456789abcdef0123456789abcdef";
        HttpJson.Response reg = HttpJson.call("POST", HttpJson.join(mock(), "push/native/register"), token,
                Json.map("device_id", device, "provider", "direct", "platform", "android", "model", "test", "app_version", "test"));
        assertTrue(reg.ok());
        String secret = Json.str(reg.body, "secret", "");
        assertEquals("direct", Json.str(reg.body, "provider", ""));
        assertEquals(32, ru.kovcheg.kovapp.core.B64.decode(Json.str(reg.body, "payload_key", "")).length);

        // подключение: welcome → накопленного нет → flushed
        WebSocketClient ws = connect();
        ws.sendText(hello(device, secret));
        Map<String, Object> w = next(ws);
        assertEquals("welcome", Json.str(w, "type", ""));
        assertTrue(Json.num(w, "ping", 0) >= 30);
        assertEquals("flushed", Json.str(next(ws), "type", ""));

        // контрольное уведомление: приходит сразу, с получателем и меткой проверки
        String probe = "abcdefabcdefabcdefabcdefabcdef01";
        HttpJson.Response t = HttpJson.call("POST", HttpJson.join(mock(), "push/test"), token, Json.map("probe", probe));
        assertTrue(t.ok());
        Map<String, Object> push = next(ws);
        assertEquals("push", Json.str(push, "type", ""));
        PushMessage m = PushMessage.parse(Json.stringify(Json.obj(push, "payload")));
        assertEquals(uid, m.uid);
        assertEquals(probe, m.probe);
        assertEquals("push-test", m.tag);
        assertEquals("#/notifications", m.url);
        ws.sendText(Json.stringify(Json.map("type", "ack", "id", Json.str(push, "id", ""))));
        HttpJson.Response ack = HttpJson.call("POST", HttpJson.join(mock(), "push/ack"), null, Json.map("probe", probe, "endpoint", "native:" + device));
        assertTrue(ack.ok());
        HttpJson.Response st = HttpJson.call("GET", HttpJson.join(mock(), "push/test/" + probe), token, null);
        assertEquals("сервер видит подтверждение с телефона", 1, Json.arr(st.body, "acks").size());
        ws.close();

        // телефон офлайн: уведомление ждёт в очереди и приходит при подключении
        HttpJson.call("POST", HttpJson.join(mock(), "mock/notify"), token,
                Json.map("user_ids", Collections.singletonList(uid), "title", "Задача", "body", "Срок завтра", "url", "#/tasks/101", "tag", "notification-101"));
        ws = connect();
        ws.sendText(hello(device, secret));
        assertEquals("welcome", Json.str(next(ws), "type", ""));
        push = next(ws);
        assertEquals("push", Json.str(push, "type", ""));
        m = PushMessage.parse(Json.stringify(Json.obj(push, "payload")));
        assertEquals("Срок завтра", m.body);
        assertEquals("#/tasks/101", m.url);
        assertEquals("notification", m.channel());
        assertEquals("flushed", Json.str(next(ws), "type", ""));
        ws.sendText(Json.stringify(Json.map("type", "ack", "id", Json.str(push, "id", ""))));
        ws.close();

        // подтверждённое не приходит второй раз
        ws = connect();
        ws.sendText(hello(device, secret));
        assertEquals("welcome", Json.str(next(ws), "type", ""));
        assertEquals("flushed", Json.str(next(ws), "type", ""));
        ws.close();

        // чужой секрет — отказ; телефон по нему забывает регистрацию
        ws = connect();
        ws.sendText(hello(device, secret + "x"));
        Map<String, Object> e = next(ws);
        assertEquals("error", Json.str(e, "type", ""));
        assertEquals("unauthorized", Json.str(e, "reason", ""));
        ws.close();

        // телефон перешёл к другому сотруднику: прежний секрет больше не действует
        String other = Json.str(login("montage"), "access_token", "");
        assertTrue(HttpJson.call("POST", HttpJson.join(mock(), "push/native/register"), other,
                Json.map("device_id", device, "provider", "direct", "platform", "android")).ok());
        ws = connect();
        ws.sendText(hello(device, secret));
        assertEquals("unauthorized", Json.str(next(ws), "reason", ""));
        ws.close();
    }
}
