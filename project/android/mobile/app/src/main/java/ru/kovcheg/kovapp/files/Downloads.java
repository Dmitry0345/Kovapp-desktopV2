package ru.kovcheg.kovapp.files;

import android.app.DownloadManager;
import android.app.PendingIntent;
import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.MediaStore;
import android.webkit.MimeTypeMap;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;

import ru.kovcheg.kovapp.notify.Notifier;

/**
 * Скачивание файлов в «Загрузки» телефона.
 *  • по адресу сервера — системным загрузчиком (прогресс в шторке, докачка);
 *  • файл, который страница уже собрала у себя (Kov.Downloads), — частями
 *    через мост и записью в «Загрузки» (MediaStore на Android 10+).
 */
public final class Downloads {
    private final Context ctx;
    private final Map<Integer, Save> saves = new ConcurrentHashMap<>();
    private final AtomicInteger ids = new AtomicInteger(1);
    public static final long MAX_BLOB = 2L * 1024 * 1024 * 1024;

    static final class Save {
        final String name, mime;
        final long expected;
        OutputStream out;
        Uri uri;
        File file;
        long written;
        Save(String name, String mime, long expected) { this.name = name; this.mime = mime; this.expected = expected; }
    }

    public Downloads(Context ctx) { this.ctx = ctx.getApplicationContext(); }

    /** Имя без путей и служебных символов */
    public static String safeName(String name) {
        String n = name == null ? "" : name.replaceAll("[\\\\/:*?\"<>|\\x00-\\x1f]", "_").trim();
        while (n.startsWith(".")) n = n.substring(1);
        if (n.isEmpty()) n = "file";
        if (n.length() > 150) {
            int dot = n.lastIndexOf('.');
            String ext = dot > 0 && n.length() - dot <= 10 ? n.substring(dot) : "";
            n = n.substring(0, 150 - ext.length()) + ext;
        }
        return n;
    }

    static String mimeOf(String name, String given) {
        if (given != null && !given.isEmpty() && !given.equals("application/octet-stream")) return given;
        int dot = name.lastIndexOf('.');
        String m = dot >= 0 ? MimeTypeMap.getSingleton().getMimeTypeFromExtension(name.substring(dot + 1).toLowerCase(Locale.ROOT)) : null;
        return m != null ? m : "application/octet-stream";
    }

    /** Нужно ли спрашивать разрешение на запись (только Android 9 и старше) */
    public static boolean needsStoragePermission() { return Build.VERSION.SDK_INT <= 28; }

    public long enqueueUrl(String url, String name, String mime, String userAgent) {
        String safe = safeName(name);
        DownloadManager dm = (DownloadManager) ctx.getSystemService(Context.DOWNLOAD_SERVICE);
        DownloadManager.Request r = new DownloadManager.Request(Uri.parse(url));
        r.setTitle(safe);
        r.setDescription("Kovapp");
        r.setMimeType(mimeOf(safe, mime));
        if (userAgent != null) r.addRequestHeader("User-Agent", userAgent);
        r.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
        r.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, safe);
        return dm.enqueue(r);
    }

    public int begin(String name, String mime, long size) throws IOException {
        if (size < 0 || size > MAX_BLOB) throw new IOException("файл слишком большой");
        Save s = new Save(safeName(name), mimeOf(safeName(name), mime), size);
        ContentResolver cr = ctx.getContentResolver();
        if (Build.VERSION.SDK_INT >= 29) {
            ContentValues v = new ContentValues();
            v.put(MediaStore.MediaColumns.DISPLAY_NAME, s.name);
            v.put(MediaStore.MediaColumns.MIME_TYPE, s.mime);
            v.put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS);
            v.put(MediaStore.MediaColumns.IS_PENDING, 1);
            s.uri = cr.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, v);
            if (s.uri == null) throw new IOException("не удалось создать файл в «Загрузках»");
            s.out = cr.openOutputStream(s.uri);
        } else {
            File dir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS);
            if (!dir.isDirectory() && !dir.mkdirs()) throw new IOException("нет папки «Загрузки»");
            s.file = unique(dir, s.name);
            s.out = new FileOutputStream(s.file);
        }
        if (s.out == null) throw new IOException("файл не открылся для записи");
        int id = ids.getAndIncrement();
        saves.put(id, s);
        return id;
    }

    static File unique(File dir, String name) {
        File f = new File(dir, name);
        int dot = name.lastIndexOf('.');
        String base = dot > 0 ? name.substring(0, dot) : name, ext = dot > 0 ? name.substring(dot) : "";
        for (int i = 1; f.exists() && i < 1000; i++) f = new File(dir, base + " (" + i + ")" + ext);
        return f;
    }

    public void chunk(int id, byte[] data) throws IOException {
        Save s = saves.get(id);
        if (s == null) throw new IOException("неизвестная загрузка");
        if (s.written + data.length > Math.max(s.expected, 0) + 16) { abort(id); throw new IOException("данных больше, чем объявлено"); }
        s.out.write(data);
        s.written += data.length;
    }

    public String end(int id) throws IOException {
        Save s = saves.remove(id);
        if (s == null) throw new IOException("неизвестная загрузка");
        s.out.close();
        Uri open;
        if (Build.VERSION.SDK_INT >= 29) {
            ContentValues v = new ContentValues();
            v.put(MediaStore.MediaColumns.IS_PENDING, 0);
            ctx.getContentResolver().update(s.uri, v, null, null);
            open = s.uri;
            Intent view = new Intent(Intent.ACTION_VIEW).setDataAndType(open, s.mime)
                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK);
            Notifier.fileSaved(ctx, s.name, PendingIntent.getActivity(ctx, s.name.hashCode(), view, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT));
        } else {
            DownloadManager dm = (DownloadManager) ctx.getSystemService(Context.DOWNLOAD_SERVICE);
            //noinspection deprecation
            dm.addCompletedDownload(s.file.getName(), "Kovapp", true, s.mime, s.file.getAbsolutePath(), s.file.length(), true);
        }
        return s.name;
    }

    public void abort(int id) {
        Save s = saves.remove(id);
        if (s == null) return;
        try { s.out.close(); } catch (IOException ignore) { /* */ }
        if (s.uri != null) ctx.getContentResolver().delete(s.uri, null, null);
        if (s.file != null) //noinspection ResultOfMethodCallIgnored
            s.file.delete();
    }
}
