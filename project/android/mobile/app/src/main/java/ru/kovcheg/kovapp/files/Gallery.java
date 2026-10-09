package ru.kovcheg.kovapp.files;

import android.content.ContentResolver;
import android.content.Context;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.graphics.Bitmap;
import android.net.Uri;
import android.os.Build;
import android.provider.MediaStore;
import android.util.Size;

import java.io.FileOutputStream;
import java.io.IOException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

import ru.kovcheg.kovapp.core.Json;

/**
 * Последние фото и видео телефона для шторки вложений: список, превью, выбор.
 * Файлы не копируются: выбранный отдаётся странице потоком по одноразовому адресу
 * и попадает в реестр выбранных — видео сжимает родной кодек, как при обычном выборе.
 */
public final class Gallery {
    private static final String[] IMAGE_COLS = {"_id", "_display_name", "_size", "date_added", "mime_type"};
    private static final String[] VIDEO_COLS = {"_id", "_display_name", "_size", "date_added", "mime_type", "duration"};

    private final Context ctx;
    private final TempFiles temp;
    private final PickedFiles picked;
    private final Map<String, String> thumbCache = new ConcurrentHashMap<>();

    public Gallery(Context ctx, TempFiles temp, PickedFiles picked) {
        this.ctx = ctx.getApplicationContext();
        this.temp = temp;
        this.picked = picked;
    }

    /** Разрешения на чтение фото и видео — по версии Android */
    public static String[] permissions() {
        if (Build.VERSION.SDK_INT >= 34) return new String[]{"android.permission.READ_MEDIA_IMAGES",
                "android.permission.READ_MEDIA_VIDEO", "android.permission.READ_MEDIA_VISUAL_USER_SELECTED"};
        if (Build.VERSION.SDK_INT >= 33) return new String[]{"android.permission.READ_MEDIA_IMAGES", "android.permission.READ_MEDIA_VIDEO"};
        return new String[]{"android.permission.READ_EXTERNAL_STORAGE"};
    }

    /** granted — вся галерея; partial — только выбранные человеком (Android 14); none — нет доступа */
    public String access() {
        if (Build.VERSION.SDK_INT >= 33 ? granted("android.permission.READ_MEDIA_IMAGES")
                : granted("android.permission.READ_EXTERNAL_STORAGE")) return "granted";
        if (Build.VERSION.SDK_INT >= 34 && granted("android.permission.READ_MEDIA_VISUAL_USER_SELECTED")) return "partial";
        return "none";
    }

    private boolean granted(String p) { return ctx.checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED; }

    /** Последние фото и видео, новые сверху: id вида i123 / v456 */
    public List<Object> recent(int offset, int limit) {
        List<Map<String, Object>> all = new ArrayList<>();
        int need = offset + limit;
        read(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, IMAGE_COLS, "image", need, all);
        read(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, VIDEO_COLS, "video", need, all);
        Collections.sort(all, (a, b) -> Long.compare((Long) b.get("date"), (Long) a.get("date")));
        List<Object> out = new ArrayList<>();
        for (int i = offset; i < Math.min(all.size(), need); i++) out.add(all.get(i));
        return out;
    }

    private void read(Uri base, String[] cols, String kind, int need, List<Map<String, Object>> into) {
        ContentResolver cr = ctx.getContentResolver();
        try (Cursor c = cr.query(base, cols, null, null, "date_added DESC")) {
            if (c == null) return;
            int n = 0;
            while (n < need && c.moveToNext()) {
                long id = c.getLong(0);
                Map<String, Object> m = Json.map("id", (kind.equals("video") ? "v" : "i") + id, "kind", kind,
                        "name", c.getString(1), "size", c.getLong(2), "date", c.getLong(3), "mime", c.getString(4));
                if (kind.equals("video")) m.put("duration", c.isNull(5) ? 0 : c.getLong(5) / 1000.0);
                into.add(m);
                n++;
            }
        } catch (RuntimeException e) {
            // нет доступа или галерея недоступна — пустой список, шторка предложит «Галерея» системным выбором
        }
    }

    /** Превью для плиток: адреса временных JPEG (с кешем на сеанс) */
    public Map<String, Object> thumbs(List<Object> ids) {
        Map<String, Object> out = Json.map();
        for (Object o : ids) {
            String id = String.valueOf(o);
            try { out.put(id, thumb(id)); } catch (IOException | RuntimeException e) { out.put(id, null); }
        }
        return out;
    }

    private String thumb(String id) throws IOException {
        String cached = thumbCache.get(id);
        if (cached != null && temp.lookup(cached) != null) return cached;
        Uri uri = uriOf(id);
        if (uri == null) return null;
        ContentResolver cr = ctx.getContentResolver();
        Bitmap bm;
        if (Build.VERSION.SDK_INT >= 29) {
            bm = cr.loadThumbnail(uri, new Size(360, 360), null);
        } else if (id.startsWith("v")) {
            bm = MediaStore.Video.Thumbnails.getThumbnail(cr, number(id), MediaStore.Video.Thumbnails.MINI_KIND, null);
        } else {
            bm = MediaStore.Images.Thumbnails.getThumbnail(cr, number(id), MediaStore.Images.Thumbnails.MINI_KIND, null);
        }
        if (bm == null) return null;
        TempFiles.Item it = temp.create(".jpg", "image/jpeg");
        try (FileOutputStream out = new FileOutputStream(it.file)) { bm.compress(Bitmap.CompressFormat.JPEG, 82, out); }
        String url = TempFiles.relUrl(it);
        thumbCache.put(id, url);
        return url;
    }

    /** Выбранные: адрес для страницы (поток из галереи, без копии) и запись в реестр выбранных */
    /** Выбранное в галерее — к ближайшему выбору файла страницы (10 с): страница получит файлы без копирования */
    public int preset(List<Object> ids) {
        List<Uri> uris = new ArrayList<>();
        for (Object id : ids) {
            Uri uri = uriOf(String.valueOf(id));
            if (uri != null) uris.add(uri);
        }
        FileChooser.PRESET.set(uris, 10_000, System.currentTimeMillis());
        return uris.size();
    }

    public List<Object> pick(List<Object> ids) {
        List<Object> out = new ArrayList<>();
        ContentResolver cr = ctx.getContentResolver();
        for (Object o : ids) {
            String id = String.valueOf(o);
            Uri uri = uriOf(id);
            if (uri == null) { out.add(null); continue; }
            try (Cursor c = cr.query(uri, new String[]{"_display_name", "_size", "mime_type", "date_modified"}, null, null, null)) {
                if (c == null || !c.moveToFirst()) { out.add(null); continue; }
                String name = c.getString(0), mime = c.getString(2);
                long size = c.getLong(1), modified = c.getLong(3) * 1000L;
                picked.add(uri);
                TempFiles.Item it = temp.createStream(() -> cr.openInputStream(uri), mime, size);
                out.add(Json.map("id", id, "url", TempFiles.relUrl(it), "name", name, "size", size,
                        "mime", mime, "lastModified", modified));
            } catch (RuntimeException e) {
                out.add(null);
            }
        }
        return out;
    }

    private static long number(String id) {
        try { return Long.parseLong(id.substring(1)); } catch (RuntimeException e) { return -1; }
    }

    private static Uri uriOf(String id) {
        if (id == null || !id.matches("[iv]\\d{1,19}")) return null;
        Uri base = id.startsWith("v") ? MediaStore.Video.Media.EXTERNAL_CONTENT_URI : MediaStore.Images.Media.EXTERNAL_CONTENT_URI;
        return Uri.parse(base + "/" + id.substring(1));
    }
}
