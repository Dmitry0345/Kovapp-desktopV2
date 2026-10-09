package ru.kovcheg.kovapp.files;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;

import java.io.File;
import java.io.FileNotFoundException;
import java.io.IOException;
import java.util.List;

/**
 * Файл для снимка или ролика с камеры: приложение камеры пишет в него по
 * выданному на один раз разрешению, затем страница читает его как обычный
 * выбранный файл. Провайдер закрыт для других приложений (exported=false).
 */
public final class CaptureProvider extends ContentProvider {
    public static String authority(Context c) { return c.getPackageName() + ".capture"; }

    public static File dir(Context c) { return new File(c.getCacheDir(), "capture"); }

    public static Uri newUri(Context c, String name) throws IOException {
        File d = dir(c);
        if (!d.isDirectory() && !d.mkdirs()) throw new IOException("нет папки для снимков");
        File f = new File(d, name);
        if (!f.exists() && !f.createNewFile()) throw new IOException("не создан файл снимка");
        return new Uri.Builder().scheme("content").authority(authority(c)).appendPath("f").appendPath(name).build();
    }

    public static File fileOf(Context c, Uri uri) {
        List<String> seg = uri.getPathSegments();
        if (seg.size() != 2 || !"f".equals(seg.get(0))) return null;
        String name = seg.get(1);
        if (!name.matches("[A-Za-z0-9_.-]{1,100}") || name.startsWith(".")) return null;
        File f = new File(dir(c), name);
        try {
            return f.getCanonicalPath().startsWith(dir(c).getCanonicalPath() + File.separator) ? f : null;
        } catch (IOException e) {
            return null;
        }
    }

    @Override public boolean onCreate() { return true; }

    @Override public Cursor query(Uri uri, String[] projection, String selection, String[] args, String sort) {
        File f = fileOf(getContext(), uri);
        if (f == null) return null;
        String[] cols = projection != null ? projection : new String[]{OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE};
        MatrixCursor c = new MatrixCursor(cols, 1);
        Object[] row = new Object[cols.length];
        for (int i = 0; i < cols.length; i++) {
            if (OpenableColumns.DISPLAY_NAME.equals(cols[i])) row[i] = f.getName();
            else if (OpenableColumns.SIZE.equals(cols[i])) row[i] = f.length();
        }
        c.addRow(row);
        return c;
    }

    @Override public String getType(Uri uri) {
        String p = uri.getLastPathSegment() == null ? "" : uri.getLastPathSegment().toLowerCase(java.util.Locale.ROOT);
        return p.endsWith(".mp4") ? "video/mp4" : p.endsWith(".jpg") ? "image/jpeg" : "application/octet-stream";
    }

    @Override public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        File f = fileOf(getContext(), uri);
        if (f == null) throw new FileNotFoundException("неизвестный файл");
        return ParcelFileDescriptor.open(f, ParcelFileDescriptor.parseMode(mode));
    }

    @Override public Uri insert(Uri uri, ContentValues values) { throw new UnsupportedOperationException(); }
    @Override public int delete(Uri uri, String selection, String[] args) { return 0; }
    @Override public int update(Uri uri, ContentValues values, String selection, String[] args) { return 0; }
}
