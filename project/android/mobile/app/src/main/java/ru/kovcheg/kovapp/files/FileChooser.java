package ru.kovcheg.kovapp.files;

import android.app.Activity;
import android.content.ClipData;
import android.content.Intent;
import android.net.Uri;
import android.provider.MediaStore;
import android.webkit.MimeTypeMap;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;

import java.io.File;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

import ru.kovcheg.kovapp.App;

/**
 * Окно выбора файлов для <input type="file">: как у Chrome — галерея и
 * файлы, а для фото и видео ещё камера. Выбранные файлы записываются в
 * PickedFiles, чтобы приложение могло сжать их на устройстве.
 */
public final class FileChooser {
    public static final int REQUEST = 7101;

    private final Activity activity;
    private ValueCallback<Uri[]> pending;
    private Uri photoOut, videoOut;

    public FileChooser(Activity activity) { this.activity = activity; }

    // выбор из галереи приложения: отдаётся странице без окна выбора (см. Gallery.preset)
    public static final ChooserPreset<Uri> PRESET = new ChooserPreset<>();

    public boolean open(ValueCallback<Uri[]> callback, WebChromeClient.FileChooserParams params) {
        List<Uri> ready = PRESET.take(System.currentTimeMillis());
        if (ready != null) {
            PickedFiles picked = App.get().picked();
            for (Uri u : ready) picked.add(u);
            callback.onReceiveValue(ready.toArray(new Uri[0]));
            return true;
        }
        if (pending != null) pending.onReceiveValue(null);
        pending = callback;
        photoOut = videoOut = null;
        Set<String> mimes = new LinkedHashSet<>();
        for (String a : params.getAcceptTypes()) {
            if (a == null) continue;
            for (String part : a.split(",")) {
                String t = part.trim().toLowerCase(Locale.ROOT);
                if (t.isEmpty()) continue;
                if (t.startsWith(".")) {
                    String m = MimeTypeMap.getSingleton().getMimeTypeFromExtension(t.substring(1));
                    mimes.add(m != null ? m : "application/octet-stream");
                } else mimes.add(t);
            }
        }
        boolean any = mimes.isEmpty() || mimes.contains("*/*");
        boolean images = any || mimes.contains("image/*") || containsPrefix(mimes, "image/");
        boolean videos = any || mimes.contains("video/*") || containsPrefix(mimes, "video/");
        boolean multiple = params.getMode() == WebChromeClient.FileChooserParams.MODE_OPEN_MULTIPLE;

        Intent content = new Intent(Intent.ACTION_GET_CONTENT);
        content.addCategory(Intent.CATEGORY_OPENABLE);
        if (!any && mimes.size() == 1) content.setType(mimes.iterator().next());
        else {
            content.setType("*/*");
            if (!any) content.putExtra(Intent.EXTRA_MIME_TYPES, mimes.toArray(new String[0]));
        }
        if (multiple) content.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);

        List<Intent> extra = new ArrayList<>();
        String stamp = new SimpleDateFormat("yyyyMMdd_HHmmss", Locale.ROOT).format(new Date());
        try {
            if (images) {
                photoOut = CaptureProvider.newUri(activity, "IMG_" + stamp + ".jpg");
                extra.add(cameraIntent(MediaStore.ACTION_IMAGE_CAPTURE, photoOut));
            }
            if (videos) {
                videoOut = CaptureProvider.newUri(activity, "VID_" + stamp + ".mp4");
                extra.add(cameraIntent(MediaStore.ACTION_VIDEO_CAPTURE, videoOut));
            }
        } catch (Exception e) {
            photoOut = videoOut = null;
            extra.clear();
        }
        List<Intent> usable = new ArrayList<>();
        for (Intent i : extra) if (i.resolveActivity(activity.getPackageManager()) != null) usable.add(i);
        Intent chooser = Intent.createChooser(content, "Выберите файл");
        if (!usable.isEmpty()) chooser.putExtra(Intent.EXTRA_INITIAL_INTENTS, usable.toArray(new Intent[0]));
        try {
            activity.startActivityForResult(chooser, REQUEST);
            return true;
        } catch (RuntimeException e) {
            pending = null;
            return false;
        }
    }

    private static boolean containsPrefix(Set<String> set, String prefix) {
        for (String s : set) if (s.startsWith(prefix)) return true;
        return false;
    }

    private static Intent cameraIntent(String action, Uri out) {
        Intent i = new Intent(action);
        i.putExtra(MediaStore.EXTRA_OUTPUT, out);
        i.addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION | Intent.FLAG_GRANT_READ_URI_PERMISSION);
        i.setClipData(ClipData.newRawUri("", out));
        return i;
    }

    public void onResult(int resultCode, Intent data) {
        ValueCallback<Uri[]> cb = pending;
        pending = null;
        if (cb == null) return;
        List<Uri> uris = new ArrayList<>();
        if (resultCode == Activity.RESULT_OK) {
            if (data != null && data.getClipData() != null) {
                ClipData clip = data.getClipData();
                for (int i = 0; i < clip.getItemCount(); i++) if (clip.getItemAt(i).getUri() != null) uris.add(clip.getItemAt(i).getUri());
            } else if (data != null && data.getData() != null) {
                uris.add(data.getData());
            }
            if (uris.isEmpty()) {
                // камера ничего не вернула в data — снимок лежит в нашем файле
                Uri shot = pick(photoOut);
                if (shot == null) shot = pick(videoOut);
                if (shot != null) uris.add(shot);
            }
        }
        cleanupEmpty();
        if (uris.isEmpty()) { cb.onReceiveValue(null); return; }
        PickedFiles picked = App.get().picked();
        for (Uri u : uris) picked.add(u);
        cb.onReceiveValue(uris.toArray(new Uri[0]));
    }

    private Uri pick(Uri u) {
        if (u == null) return null;
        File f = CaptureProvider.fileOf(activity, u);
        return f != null && f.length() > 0 ? u : null;
    }

    private void cleanupEmpty() {
        for (Uri u : new Uri[]{photoOut, videoOut}) {
            if (u == null) continue;
            File f = CaptureProvider.fileOf(activity, u);
            if (f != null && f.length() == 0) //noinspection ResultOfMethodCallIgnored
                f.delete();
        }
    }

    public void cancel() {
        if (pending != null) { pending.onReceiveValue(null); pending = null; }
    }
}
