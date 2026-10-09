package ru.kovcheg.kovapp.net;

import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.Closeable;
import java.io.EOFException;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Locale;
import java.util.Map;

import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLSocket;
import javax.net.ssl.SSLSocketFactory;

import ru.kovcheg.kovapp.core.B64;

/**
 * Небольшой клиент WebSocket (RFC 6455) для собственного канала уведомлений
 * на телефонах без сервисов Google. Без сторонних библиотек: меньше кода —
 * меньше поверхность атаки. Сообщения — только текст (JSON).
 */
public final class WebSocketClient implements Closeable {
    public static final int MAX_MESSAGE = 1024 * 1024;
    private static final String GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";
    private static final SecureRandom RANDOM = new SecureRandom();

    private final Socket socket;
    private final InputStream in;
    private final OutputStream out;
    private final Object sendLock = new Object();
    private volatile boolean closed;

    private WebSocketClient(Socket socket) throws IOException {
        this.socket = socket;
        this.in = new BufferedInputStream(socket.getInputStream(), 16384);
        this.out = socket.getOutputStream();
    }

    /**
     * Подключение. readTimeoutMs — сколько ждать любых данных (сервер шлёт
     * ping); истёк срок — соединение считается мёртвым.
     */
    public static WebSocketClient connect(String url, Map<String, String> headers, int connectTimeoutMs, int readTimeoutMs) throws IOException {
        URI u = URI.create(url);
        String scheme = u.getScheme() == null ? "" : u.getScheme().toLowerCase(Locale.ROOT);
        boolean tls;
        if (scheme.equals("wss")) tls = true;
        else if (scheme.equals("ws")) tls = false;
        else throw new IOException("нужен адрес ws:// или wss://");
        String host = u.getHost();
        if (host == null) throw new IOException("в адресе нет хоста");
        int port = u.getPort() > 0 ? u.getPort() : (tls ? 443 : 80);
        Socket s = new Socket();
        try {
            s.connect(new InetSocketAddress(host, port), connectTimeoutMs);
            s.setTcpNoDelay(true);
            s.setKeepAlive(true);
            s.setSoTimeout(readTimeoutMs);
            if (tls) {
                SSLSocket ssl = (SSLSocket) ((SSLSocketFactory) SSLSocketFactory.getDefault()).createSocket(s, host, port, true);
                ssl.startHandshake();
                // сокет TLS сам имя сервера не сверяет — проверяем явно
                if (!HttpsURLConnection.getDefaultHostnameVerifier().verify(host, ssl.getSession())) {
                    ssl.close();
                    throw new IOException("сертификат не подходит к имени сервера");
                }
                s = ssl;
            }
            WebSocketClient c = new WebSocketClient(s);
            c.handshake(u, host, port, tls, headers);
            return c;
        } catch (IOException | RuntimeException e) {
            try { s.close(); } catch (IOException ignore) { /* */ }
            throw e;
        }
    }

    private void handshake(URI u, String host, int port, boolean tls, Map<String, String> headers) throws IOException {
        byte[] nonce = new byte[16];
        RANDOM.nextBytes(nonce);
        String key = B64.encode(nonce);
        String path = (u.getRawPath() == null || u.getRawPath().isEmpty() ? "/" : u.getRawPath())
                + (u.getRawQuery() != null ? "?" + u.getRawQuery() : "");
        boolean defPort = (tls && port == 443) || (!tls && port == 80);
        StringBuilder req = new StringBuilder();
        req.append("GET ").append(path).append(" HTTP/1.1\r\n");
        req.append("Host: ").append(host).append(defPort ? "" : ":" + port).append("\r\n");
        req.append("Upgrade: websocket\r\nConnection: Upgrade\r\n");
        req.append("Sec-WebSocket-Key: ").append(key).append("\r\n");
        req.append("Sec-WebSocket-Version: 13\r\n");
        if (headers != null) {
            for (Map.Entry<String, String> h : headers.entrySet()) {
                String name = h.getKey(), value = h.getValue();
                if (name.indexOf('\r') >= 0 || name.indexOf('\n') >= 0 || value.indexOf('\r') >= 0 || value.indexOf('\n') >= 0) {
                    throw new IOException("недопустимый заголовок");
                }
                req.append(name).append(": ").append(value).append("\r\n");
            }
        }
        req.append("\r\n");
        out.write(req.toString().getBytes(StandardCharsets.ISO_8859_1));
        out.flush();
        String status = readLine();
        if (status == null || !status.startsWith("HTTP/1.1 101")) {
            throw new IOException("сервер не принял WebSocket: " + (status == null ? "нет ответа" : status));
        }
        String accept = null;
        String line;
        int count = 0;
        while ((line = readLine()) != null && !line.isEmpty()) {
            if (++count > 100) throw new IOException("слишком много заголовков");
            int colon = line.indexOf(':');
            if (colon > 0 && line.substring(0, colon).trim().equalsIgnoreCase("Sec-WebSocket-Accept")) accept = line.substring(colon + 1).trim();
        }
        String expected;
        try {
            expected = B64.encode(MessageDigest.getInstance("SHA-1").digest((key + GUID).getBytes(StandardCharsets.ISO_8859_1)));
        } catch (Exception e) {
            throw new IOException(e);
        }
        if (!expected.equals(accept)) throw new IOException("неверный ответ сервера WebSocket");
    }

    private String readLine() throws IOException {
        ByteArrayOutputStream buf = new ByteArrayOutputStream();
        int c;
        while ((c = in.read()) >= 0) {
            if (c == '\n') break;
            if (c != '\r') buf.write(c);
            if (buf.size() > 8192) throw new IOException("слишком длинная строка ответа");
        }
        if (c < 0 && buf.size() == 0) return null;
        return new String(buf.toByteArray(), StandardCharsets.ISO_8859_1);
    }

    public void sendText(String text) throws IOException { send(0x1, text.getBytes(StandardCharsets.UTF_8)); }

    private void send(int opcode, byte[] payload) throws IOException {
        synchronized (sendLock) {
            if (closed && opcode != 0x8) throw new IOException("соединение закрыто");
            ByteArrayOutputStream f = new ByteArrayOutputStream(payload.length + 14);
            f.write(0x80 | opcode);
            int n = payload.length;
            if (n < 126) f.write(0x80 | n);
            else if (n < 65536) { f.write(0x80 | 126); f.write(n >>> 8); f.write(n & 255); }
            else {
                f.write(0x80 | 127);
                for (int i = 7; i >= 0; i--) f.write((int) (((long) n >>> (8 * i)) & 255));
            }
            byte[] mask = new byte[4];
            RANDOM.nextBytes(mask);
            f.write(mask, 0, 4);
            byte[] masked = new byte[n];
            for (int i = 0; i < n; i++) masked[i] = (byte) (payload[i] ^ mask[i & 3]);
            f.write(masked, 0, n);
            out.write(f.toByteArray());
            out.flush();
        }
    }

    private int readByte() throws IOException {
        int b = in.read();
        if (b < 0) throw new EOFException("соединение оборвалось");
        return b;
    }

    private byte[] readFully(long n) throws IOException {
        if (n > MAX_MESSAGE) throw new IOException("сообщение слишком большое");
        byte[] b = new byte[(int) n];
        int o = 0;
        while (o < n) {
            int r = in.read(b, o, (int) n - o);
            if (r < 0) throw new EOFException("соединение оборвалось");
            o += r;
        }
        return b;
    }

    /**
     * Следующее текстовое сообщение. null — сервер закрыл соединение штатно.
     * На ping сервера отвечает сам.
     */
    public String receive() throws IOException {
        ByteArrayOutputStream message = null;
        while (true) {
            int b0 = readByte(), b1 = readByte();
            boolean fin = (b0 & 0x80) != 0;
            int opcode = b0 & 0x0f;
            if ((b0 & 0x70) != 0) throw new IOException("неизвестные расширения WebSocket");
            if ((b1 & 0x80) != 0) throw new IOException("сервер не должен маскировать кадры");
            long len = b1 & 0x7f;
            if (len == 126) len = ((long) readByte() << 8) | readByte();
            else if (len == 127) {
                len = 0;
                for (int i = 0; i < 8; i++) len = (len << 8) | readByte();
                if (len < 0) throw new IOException("неверная длина кадра");
            }
            if (opcode >= 0x8 && (len > 125 || !fin)) throw new IOException("неверный служебный кадр");
            byte[] payload = readFully(len);
            switch (opcode) {
                case 0x9: send(0xA, payload); continue;           // ping → pong
                case 0xA: continue;                                // pong
                case 0x8:
                    if (!closed) {
                        closed = true;
                        try { send(0x8, payload.length >= 2 ? new byte[]{payload[0], payload[1]} : new byte[0]); } catch (IOException ignore) { /* */ }
                    }
                    return null;
                case 0x1:
                case 0x2:
                    if (message != null) throw new IOException("новое сообщение внутри незаконченного");
                    if (fin) return new String(payload, StandardCharsets.UTF_8);
                    message = new ByteArrayOutputStream();
                    message.write(payload, 0, payload.length);
                    continue;
                case 0x0:
                    if (message == null) throw new IOException("продолжение без начала");
                    if (message.size() + payload.length > MAX_MESSAGE) throw new IOException("сообщение слишком большое");
                    message.write(payload, 0, payload.length);
                    if (fin) return new String(message.toByteArray(), StandardCharsets.UTF_8);
                    continue;
                default:
                    throw new IOException("неизвестный тип кадра " + opcode);
            }
        }
    }

    public boolean isOpen() { return !closed && !socket.isClosed(); }

    /** Изменить таймаут чтения (после приветствия сервер сообщает свой интервал ping) */
    public void setReadTimeout(int ms) throws IOException { socket.setSoTimeout(ms); }

    @Override public void close() {
        if (!closed) {
            closed = true;
            try { send(0x8, new byte[]{0x03, (byte) 0xE8}); } catch (IOException ignore) { /* */ }
        }
        try { socket.close(); } catch (IOException ignore) { /* */ }
    }
}
