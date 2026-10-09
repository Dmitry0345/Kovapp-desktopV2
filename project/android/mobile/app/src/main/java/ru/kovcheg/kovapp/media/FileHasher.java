package ru.kovcheg.kovapp.media;

import java.io.IOException;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;

import ru.kovcheg.kovapp.core.B64;

/**
 * Отпечаток файла для «уже есть на сервере» — как hash.js: SHA-256 от
 * хешей кусков по 4 МиБ и размера (8 байт, старший байт первым).
 */
public final class FileHasher {
    private FileHasher() {}

    public static final int LEAF = 4 * 1024 * 1024;

    public interface Progress { void on(double p); }
    public interface Cancel { boolean cancelled(); }

    static MessageDigest sha() {
        try { return MessageDigest.getInstance("SHA-256"); } catch (NoSuchAlgorithmException e) { throw new IllegalStateException(e); }
    }

    public static String tree(Source src, Progress progress, Cancel cancel) throws IOException {
        long size = src.size();
        int leaves = (int) ((size + LEAF - 1) / LEAF);
        byte[] all = new byte[leaves * 32 + 8];
        byte[] buf = new byte[1 << 20];
        for (int i = 0; i < leaves; i++) {
            if (cancel != null && cancel.cancelled()) throw new IOException("отменено");
            MessageDigest md = sha();
            long off = (long) i * LEAF, end = Math.min(size, off + LEAF);
            for (long p = off; p < end; ) {
                int n = src.read(p, buf, 0, (int) Math.min(buf.length, end - p));
                if (n <= 0) throw new IOException("файл короче ожидаемого");
                md.update(buf, 0, n);
                p += n;
            }
            System.arraycopy(md.digest(), 0, all, i * 32, 32);
            if (progress != null) progress.on(Math.min(1, (off + LEAF) / (double) size));
        }
        for (int k = 0; k < 8; k++) all[leaves * 32 + k] = (byte) (size >>> (56 - 8 * k));
        return B64.hex(sha().digest(all));
    }

    public static final int SAMPLE_WINDOW = 64 * 1024, SAMPLE_WINDOWS = 9;

    /** Образец файла (как blobs.sample_of и hash.js): 9 участков по 64 КБ от начала до конца,
     *  перед каждым — его смещение (8 байт), в конце — размер; ≈0,6 МБ чтения вместо всего файла */
    public static String sample(Source src) throws IOException {
        long size = src.size();
        MessageDigest md = sha();
        byte[] buf = new byte[SAMPLE_WINDOW];
        for (int i = 0; i < SAMPLE_WINDOWS; i++) {
            long pos = size <= SAMPLE_WINDOW ? 0 : (size - SAMPLE_WINDOW) * i / (SAMPLE_WINDOWS - 1);
            int len = (int) Math.min(SAMPLE_WINDOW, size - pos);
            for (int k = 0; k < 8; k++) md.update((byte) (pos >>> (56 - 8 * k)));
            int got = 0;
            while (got < len) {
                int n = src.read(pos + got, buf, got, len - got);
                if (n <= 0) throw new IOException("файл короче ожидаемого");
                got += n;
            }
            md.update(buf, 0, len);
        }
        for (int k = 0; k < 8; k++) md.update((byte) (size >>> (56 - 8 * k)));
        return B64.hex(md.digest());
    }

    public static String range(Source src, long offset, long length) throws IOException {
        MessageDigest md = sha();
        long size = src.size();
        long from = Math.max(0, Math.min(offset, size)), to = Math.max(from, Math.min(size, offset + Math.max(0, length)));
        byte[] buf = new byte[1 << 16];
        for (long p = from; p < to; ) {
            int n = src.read(p, buf, 0, (int) Math.min(buf.length, to - p));
            if (n <= 0) throw new IOException("файл короче ожидаемого");
            md.update(buf, 0, n);
            p += n;
        }
        return B64.hex(md.digest());
    }
}
