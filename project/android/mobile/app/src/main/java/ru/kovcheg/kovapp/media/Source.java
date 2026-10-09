package ru.kovcheg.kovapp.media;

import java.io.Closeable;
import java.io.EOFException;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;

/**
 * Файл с чтением по смещению. На телефоне — канал дескриптора из
 * ContentResolver (выбранный пользователем файл), в тестах — обычный файл.
 */
public class Source implements Closeable {
    private final FileChannel channel;
    private final long size;
    private final Closeable owner;

    public Source(FileChannel channel, long size, Closeable owner) {
        this.channel = channel;
        this.size = size;
        this.owner = owner;
    }

    public static Source of(File f) throws IOException {
        FileInputStream in = new FileInputStream(f);
        return new Source(in.getChannel(), f.length(), in);
    }

    public long size() { return size; }

    /** Читает ровно len байт с позиции pos (или меньше — только у конца файла) */
    public int read(long pos, byte[] dst, int off, int len) throws IOException {
        if (pos >= size) return 0;
        len = (int) Math.min(len, size - pos);
        ByteBuffer buf = ByteBuffer.wrap(dst, off, len);
        int total = 0;
        while (buf.hasRemaining()) {
            int n = channel.read(buf, pos + total);
            if (n < 0) break;
            total += n;
        }
        return total;
    }

    public byte[] readAt(long pos, int len) throws IOException {
        int n = (int) Math.max(0, Math.min(len, size - pos));
        byte[] b = new byte[n];
        int got = read(pos, b, 0, n);
        if (got != n) throw new EOFException("файл короче ожидаемого");
        return b;
    }

    @Override public void close() throws IOException {
        try { channel.close(); } finally { if (owner != null) owner.close(); }
    }
}
