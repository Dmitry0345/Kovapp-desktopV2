package ru.kovcheg.kovapp.media;

import android.content.ContentResolver;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.ImageDecoder;
import android.graphics.Matrix;
import android.graphics.Paint;
import android.media.ExifInterface;
import android.os.Build;

import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.Map;

import ru.kovcheg.kovapp.core.Json;
import ru.kovcheg.kovapp.files.PickedFiles;
import ru.kovcheg.kovapp.files.TempFiles;

/**
 * Запасное сжатие фото, когда страница не смогла его распаковать (например,
 * HEIC с камеры). Правила те же, что в image-prep.js: сторона до 3200,
 * WebP 80 %, «уже компактно», «не стало меньше». Миниатюру и заглушку
 * страница строит сама по превью — тем же кодом, что в браузере.
 */
public final class NativeImage {
    private NativeImage() {}

    static final int PREVIEW_SIDE = 640;

    static final class Decoded {
        Bitmap bitmap;
        boolean scaled;
    }

    public static Map<String, Object> prepare(ContentResolver cr, PickedFiles.Entry e, TempFiles temp, int maxSide) throws IOException {
        Map<String, Object> out = Json.map("changed", false, "width", 0, "height", 0, "reason", "");
        ImageRules.Dims info = null;
        try (InputStream in = cr.openInputStream(e.uri)) {
            if (in != null) {
                byte[] head = new byte[256 * 1024];
                int n = 0, r;
                while (n < head.length && (r = in.read(head, n, head.length - n)) > 0) n += r;
                info = ImageRules.dims(java.util.Arrays.copyOf(head, n));
            }
        }
        if (info != null && info.width * info.height > ImageRules.PIXEL_LIMIT) { out.put("reason", "too-large"); return out; }
        if (info != null && "gif".equals(info.type)) { out.put("reason", "gif"); return out; }
        Decoded pic = decode(cr, e, maxSide);
        if (pic == null || pic.bitmap == null) { out.put("reason", "decode"); return out; }
        try {
            Bitmap bmp = pic.bitmap;
            if ((double) bmp.getWidth() * bmp.getHeight() > ImageRules.PIXEL_LIMIT) { out.put("reason", "too-large"); return out; }
            long[] wh = ImageRules.fit(bmp.getWidth(), bmp.getHeight(), maxSide);
            int w = (int) wh[0], h = (int) wh[1];
            out.put("width", w);
            out.put("height", h);
            // превью для заглушки и миниатюры (PNG — сохраняет прозрачность)
            long[] pw = ImageRules.fit(w, h, PREVIEW_SIDE);
            Bitmap preview = scale(bmp, (int) pw[0], (int) pw[1]);
            TempFiles.Item pv = temp.create(".png", "image/png");
            try (FileOutputStream fo = new FileOutputStream(pv.file)) { preview.compress(Bitmap.CompressFormat.PNG, 100, fo); }
            out.put("preview", Json.map("url", TempFiles.relUrl(pv), "width", preview.getWidth(), "height", preview.getHeight()));
            if (preview != bmp) preview.recycle();
            boolean resized = w != bmp.getWidth() || h != bmp.getHeight() || pic.scaled;
            long size = Math.max(e.size, e.statSize);
            if (!resized && (size <= ImageRules.COMPACT_BYTES || ImageRules.light(info, size, bmp.getWidth(), bmp.getHeight()))) {
                out.put("width", bmp.getWidth());
                out.put("height", bmp.getHeight());
                out.put("reason", "already-compact");
                return out;
            }
            Bitmap target = scale(bmp, w, h);
            TempFiles.Item file = temp.create(".webp", "image/webp");
            try (FileOutputStream fo = new FileOutputStream(file.file)) {
                Bitmap.CompressFormat fmt = Build.VERSION.SDK_INT >= 30 ? Bitmap.CompressFormat.WEBP_LOSSY : Bitmap.CompressFormat.WEBP;
                if (!target.compress(fmt, ImageRules.WEBP_QUALITY, fo)) { out.put("reason", "encode"); return out; }
            } finally {
                if (target != bmp) target.recycle();
            }
            if (!resized && file.file.length() >= size * ImageRules.NOT_SMALLER) {
                temp.release(file.token);
                out.put("width", bmp.getWidth());
                out.put("height", bmp.getHeight());
                out.put("reason", "not-smaller");
                return out;
            }
            out.put("changed", true);
            out.put("mime", "image/webp");
            out.put("name", ImageRules.renamed(e.name, ".webp"));
            out.put("file", Json.map("url", TempFiles.relUrl(file), "size", file.file.length()));
            return out;
        } finally {
            pic.bitmap.recycle();
        }
    }

    /** Распаковка с учётом поворота по EXIF; крупное фото сразу уменьшается */
    static Decoded decode(ContentResolver cr, PickedFiles.Entry e, final int maxSide) throws IOException {
        final Decoded d = new Decoded();
        if (Build.VERSION.SDK_INT >= 28) {
            try {
                ImageDecoder.Source src = ImageDecoder.createSource(cr, e.uri);
                d.bitmap = ImageDecoder.decodeBitmap(src, (decoder, imgInfo, source) -> {
                    int w = imgInfo.getSize().getWidth(), h = imgInfo.getSize().getHeight();
                    if ((double) w * h > ImageRules.PIXEL_LIMIT) throw new IllegalArgumentException("too-large");
                    long[] t = ImageRules.fit(w, h, maxSide);
                    if (t[0] != w || t[1] != h) { decoder.setTargetSize((int) t[0], (int) t[1]); d.scaled = true; }
                    decoder.setAllocator(ImageDecoder.ALLOCATOR_SOFTWARE);
                });
                return d;
            } catch (IOException | RuntimeException ex) {
                return null;
            }
        }
        BitmapFactory.Options bounds = new BitmapFactory.Options();
        bounds.inJustDecodeBounds = true;
        try (InputStream in = cr.openInputStream(e.uri)) { BitmapFactory.decodeStream(in, null, bounds); }
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null;
        if ((double) bounds.outWidth * bounds.outHeight > ImageRules.PIXEL_LIMIT) return null;
        int sample = 1;
        while (Math.max(bounds.outWidth, bounds.outHeight) / (sample * 2) >= maxSide) sample *= 2;
        BitmapFactory.Options opts = new BitmapFactory.Options();
        opts.inSampleSize = sample;
        Bitmap bmp;
        try (InputStream in = cr.openInputStream(e.uri)) { bmp = BitmapFactory.decodeStream(in, null, opts); }
        if (bmp == null) return null;
        d.scaled = sample > 1;
        int orientation = ExifInterface.ORIENTATION_NORMAL;
        try (InputStream in = cr.openInputStream(e.uri)) {
            if (in != null) orientation = new ExifInterface(in).getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL);
        } catch (IOException | RuntimeException ignore) { /* без EXIF */ }
        Matrix m = new Matrix();
        switch (orientation) {
            case ExifInterface.ORIENTATION_FLIP_HORIZONTAL: m.setScale(-1, 1); break;
            case ExifInterface.ORIENTATION_ROTATE_180: m.setRotate(180); break;
            case ExifInterface.ORIENTATION_FLIP_VERTICAL: m.setScale(1, -1); break;
            case ExifInterface.ORIENTATION_TRANSPOSE: m.setRotate(90); m.postScale(-1, 1); break;
            case ExifInterface.ORIENTATION_ROTATE_90: m.setRotate(90); break;
            case ExifInterface.ORIENTATION_TRANSVERSE: m.setRotate(-90); m.postScale(-1, 1); break;
            case ExifInterface.ORIENTATION_ROTATE_270: m.setRotate(-90); break;
            default: break;
        }
        if (!m.isIdentity()) {
            Bitmap r = Bitmap.createBitmap(bmp, 0, 0, bmp.getWidth(), bmp.getHeight(), m, true);
            if (r != bmp) bmp.recycle();
            bmp = r;
        }
        d.bitmap = bmp;
        return d;
    }

    /** Уменьшение ступенями по половине, как draw() в JS, чтобы не было «лесенок» */
    static Bitmap scale(Bitmap src, int tw, int th) {
        if (src.getWidth() == tw && src.getHeight() == th) return src;
        Bitmap cur = src;
        Paint p = new Paint(Paint.FILTER_BITMAP_FLAG | Paint.ANTI_ALIAS_FLAG);
        while ((double) cur.getWidth() / tw > 2.2 && (double) cur.getHeight() / th > 2.2) {
            int w = Math.max(tw, Math.round(cur.getWidth() / 2f)), h = Math.max(th, Math.round(cur.getHeight() / 2f));
            Bitmap next = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
            new Canvas(next).drawBitmap(cur, null, new android.graphics.Rect(0, 0, w, h), p);
            if (cur != src) cur.recycle();
            cur = next;
        }
        Bitmap out = Bitmap.createBitmap(tw, th, Bitmap.Config.ARGB_8888);
        new Canvas(out).drawBitmap(cur, null, new android.graphics.Rect(0, 0, tw, th), p);
        if (cur != src) cur.recycle();
        return out;
    }
}
