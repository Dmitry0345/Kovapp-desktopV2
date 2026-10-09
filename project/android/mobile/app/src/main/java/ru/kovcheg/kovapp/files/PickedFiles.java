package ru.kovcheg.kovapp.files;

import android.content.ContentResolver;
import android.content.Context;
import android.database.Cursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;

import java.io.FileInputStream;
import java.io.IOException;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;

import ru.kovcheg.kovapp.core.B64;
import ru.kovcheg.kovapp.media.Source;

/**
 * Файлы, которые пользователь сам выбрал в окне выбора (или снял камерой).
 * Страница может попросить приложение обработать только их — по имени и
 * размеру находится выданная при выборе метка; других путей к файлам
 * телефона у страницы нет.
 */
public final class PickedFiles {
    private static final int MAX = 300;
    private static final SecureRandom RANDOM = new SecureRandom();

    public static final class Entry {
        public final String handle, name, mime;
        public final Uri uri;
        public final long size, statSize, at;
        Entry(String handle, Uri uri, String name, long size, long statSize, String mime) {
            this.handle = handle; this.uri = uri; this.name = name; this.size = size; this.statSize = statSize; this.mime = mime;
            this.at = System.currentTimeMillis();
        }
    }

    private final Context ctx;
    private final List<Entry> entries = new CopyOnWriteArrayList<>();

    public PickedFiles(Context ctx) { this.ctx = ctx.getApplicationContext(); }

    public Entry add(Uri uri) {
        ContentResolver cr = ctx.getContentResolver();
        String name = null;
        long size = -1, stat = -1;
        try (Cursor c = cr.query(uri, new String[]{OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE}, null, null, null)) {
            if (c != null && c.moveToFirst()) {
                int ni = c.getColumnIndex(OpenableColumns.DISPLAY_NAME), si = c.getColumnIndex(OpenableColumns.SIZE);
                if (ni >= 0 && !c.isNull(ni)) name = c.getString(ni);
                if (si >= 0 && !c.isNull(si)) size = c.getLong(si);
            }
        } catch (RuntimeException e) {
            // провайдер без сведений о файле — узнаем размер по дескриптору
        }
        try (ParcelFileDescriptor pfd = cr.openFileDescriptor(uri, "r")) {
            if (pfd != null) stat = pfd.getStatSize();
        } catch (IOException | RuntimeException e) {
            stat = -1;
        }
        if (name == null) name = uri.getLastPathSegment() == null ? "file" : uri.getLastPathSegment();
        byte[] b = new byte[16];
        RANDOM.nextBytes(b);
        Entry e = new Entry(B64.hex(b), uri, name, size, stat, cr.getType(uri));
        entries.add(0, e);
        while (entries.size() > MAX) entries.remove(entries.size() - 1);
        return e;
    }

    /** Метка по имени и размеру файла со страницы; самый свежий выбор — первым */
    public String resolve(String name, long size) {
        for (Entry e : entries) {
            if (e.name.equals(name) && (e.size == size || e.statSize == size)) return e.handle;
        }
        return null;
    }

    public Entry get(String handle) {
        if (handle == null) return null;
        for (Entry e : entries) if (e.handle.equals(handle)) return e;
        return null;
    }

    /** Открыть для чтения по смещению (разбор видео, отпечаток) */
    public Source open(String handle) throws IOException {
        Entry e = get(handle);
        if (e == null) throw new IOException("файл не выбирался в приложении");
        final ParcelFileDescriptor pfd = ctx.getContentResolver().openFileDescriptor(e.uri, "r");
        if (pfd == null) throw new IOException("файл недоступен");
        final FileInputStream in = new FileInputStream(pfd.getFileDescriptor());
        long size = pfd.getStatSize() >= 0 ? pfd.getStatSize() : Math.max(0, e.size);
        return new Source(in.getChannel(), size, () -> {
            try { in.close(); } finally { pfd.close(); }
        });
    }

    public List<Entry> snapshot() { return new ArrayList<>(entries); }

    public void clear() { entries.clear(); }
}
