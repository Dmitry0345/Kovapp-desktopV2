package ru.kovcheg.kovapp.core;

/**
 * Base64 без java.util.Base64 (его нет на Android 7) и без android.util
 * (ядро проверяется на обычной JVM). Разбор принимает оба алфавита.
 */
public final class B64 {
    private B64() {}

    private static final char[] STD = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/".toCharArray();
    private static final char[] URL = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_".toCharArray();
    private static final int[] REV = new int[128];
    static {
        java.util.Arrays.fill(REV, -1);
        for (int i = 0; i < 64; i++) { REV[STD[i]] = i; REV[URL[i]] = i; }
    }

    public static String encode(byte[] data) { return encode(data, 0, data.length, false, true); }
    public static String encodeUrl(byte[] data) { return encode(data, 0, data.length, true, false); }

    public static String encode(byte[] data, int off, int len, boolean url, boolean pad) {
        char[] a = url ? URL : STD;
        StringBuilder sb = new StringBuilder((len + 2) / 3 * 4);
        int end = off + len, i = off;
        while (i + 2 < end) {
            int v = ((data[i] & 255) << 16) | ((data[i + 1] & 255) << 8) | (data[i + 2] & 255);
            sb.append(a[v >>> 18]).append(a[(v >>> 12) & 63]).append(a[(v >>> 6) & 63]).append(a[v & 63]);
            i += 3;
        }
        int rest = end - i;
        if (rest == 1) {
            int v = (data[i] & 255) << 16;
            sb.append(a[v >>> 18]).append(a[(v >>> 12) & 63]);
            if (pad) sb.append("==");
        } else if (rest == 2) {
            int v = ((data[i] & 255) << 16) | ((data[i + 1] & 255) << 8);
            sb.append(a[v >>> 18]).append(a[(v >>> 12) & 63]).append(a[(v >>> 6) & 63]);
            if (pad) sb.append('=');
        }
        return sb.toString();
    }

    /** Разбор; пробелы и переводы строк пропускаются. Неверный символ → IllegalArgumentException. */
    public static byte[] decode(String s) {
        int n = 0;
        int[] q = new int[s.length()];
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c == '=' ) break;
            if (c == ' ' || c == '\n' || c == '\r' || c == '\t') continue;
            int v = c < 128 ? REV[c] : -1;
            if (v < 0) throw new IllegalArgumentException("base64: неверный символ");
            q[n++] = v;
        }
        if (n % 4 == 1) throw new IllegalArgumentException("base64: неверная длина");
        byte[] out = new byte[n * 3 / 4];
        int o = 0, i = 0;
        while (i + 3 < n) {
            int v = (q[i] << 18) | (q[i + 1] << 12) | (q[i + 2] << 6) | q[i + 3];
            out[o++] = (byte) (v >>> 16); out[o++] = (byte) (v >>> 8); out[o++] = (byte) v;
            i += 4;
        }
        if (n - i == 2) {
            int v = (q[i] << 18) | (q[i + 1] << 12);
            out[o++] = (byte) (v >>> 16);
        } else if (n - i == 3) {
            int v = (q[i] << 18) | (q[i + 1] << 12) | (q[i + 2] << 6);
            out[o++] = (byte) (v >>> 16); out[o++] = (byte) (v >>> 8);
        }
        return out;
    }

    public static String hex(byte[] b) {
        char[] h = "0123456789abcdef".toCharArray();
        char[] out = new char[b.length * 2];
        for (int i = 0; i < b.length; i++) { out[i * 2] = h[(b[i] >> 4) & 15]; out[i * 2 + 1] = h[b[i] & 15]; }
        return new String(out);
    }
}
