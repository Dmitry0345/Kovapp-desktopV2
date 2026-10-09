package ru.kovcheg.kovapp.media;

/**
 * Правила подготовки фото — перенос web_app/js/media/image-prep.js:
 * размеры по заголовку файла (dims), поворот по EXIF, вписывание (fit),
 * пороги «уже компактно» и «не стало меньше».
 */
public final class ImageRules {
    private ImageRules() {}

    public static final int MAX_SIDE = 3200, THUMB_SIDE = 320;
    public static final int WEBP_QUALITY = 80;
    public static final double PIXEL_LIMIT = 100e6;
    public static final long COMPACT_BYTES = 300 * 1024;
    public static final double LIGHT_BYTES_PER_PIXEL = 0.35;
    public static final double NOT_SMALLER = 0.95;

    public static final class Dims {
        public String type;
        public long width, height;
        public boolean alpha;
        public int orientation = 1;
        public boolean hasOrientation;
    }

    private static int b(byte[] d, int i) { return i < d.length && i >= 0 ? d[i] & 255 : 0; }
    private static boolean in(byte[] d, int i) { return i >= 0 && i < d.length; }

    public static Dims dims(byte[] d) {
        if (d.length < 4) return null;
        if (b(d, 0) == 0x89 && b(d, 1) == 0x50 && b(d, 2) == 0x4E && b(d, 3) == 0x47) {
            Dims r = dims("png", u32(d, 16), u32(d, 20));
            int ct = b(d, 25);
            r.alpha = ct == 4 || ct == 6 || ct == 3;
            return r;
        }
        if (b(d, 0) == 0x47 && b(d, 1) == 0x49 && b(d, 2) == 0x46) {
            Dims r = dims("gif", b(d, 6) | (b(d, 7) << 8), b(d, 8) | (b(d, 9) << 8));
            r.alpha = true;
            return r;
        }
        if (b(d, 0) == 0x52 && b(d, 1) == 0x49 && b(d, 8) == 0x57 && b(d, 9) == 0x45) {
            String kind = "" + (char) b(d, 12) + (char) b(d, 13) + (char) b(d, 14) + (char) b(d, 15);
            if (kind.equals("VP8 ")) return dims("webp", (b(d, 26) | (b(d, 27) << 8)) & 0x3FFF, (b(d, 28) | (b(d, 29) << 8)) & 0x3FFF);
            if (kind.equals("VP8L")) {
                Dims r = dims("webp", 1 + (((b(d, 22) & 0x3F) << 8) | b(d, 21)),
                        1 + (((b(d, 24) & 0x0F) << 10) | (b(d, 23) << 2) | ((b(d, 22) & 0xC0) >> 6)));
                r.alpha = true;
                return r;
            }
            if (kind.equals("VP8X")) {
                Dims r = dims("webp", 1 + (b(d, 24) | (b(d, 25) << 8) | (b(d, 26) << 16)), 1 + (b(d, 27) | (b(d, 28) << 8) | (b(d, 29) << 16)));
                r.alpha = (b(d, 20) & 0x10) != 0;
                return r;
            }
        }
        if (b(d, 0) == 0xFF && b(d, 1) == 0xD8) {
            int i = 2, orientation = 1;
            while (i + 9 < d.length) {
                if (b(d, i) != 0xFF) { i++; continue; }
                int m = b(d, i + 1);
                if (m == 0xD8 || m == 0x01 || (m >= 0xD0 && m <= 0xD7) || m == 0xFF) { i += (m == 0xFF ? 1 : 2); continue; }
                int len = (b(d, i + 2) << 8) | b(d, i + 3);
                if (m == 0xE1 && b(d, i + 4) == 0x45 && b(d, i + 5) == 0x78 && b(d, i + 6) == 0x69 && b(d, i + 7) == 0x66) {
                    int o = exifOrientation(d, i + 10);
                    if (o != 0) orientation = o;
                }
                if (m == 0xC0 || m == 0xC1 || m == 0xC2 || m == 0xC3 || m == 0xC5 || m == 0xC6 || m == 0xC7 || m == 0xC9
                        || m == 0xCA || m == 0xCB || m == 0xCD || m == 0xCE || m == 0xCF) {
                    Dims r = dims("jpeg", (b(d, i + 7) << 8) | b(d, i + 8), (b(d, i + 5) << 8) | b(d, i + 6));
                    r.orientation = orientation;
                    r.hasOrientation = true;
                    return r;
                }
                i += 2 + len;
            }
            Dims r = dims("jpeg", 0, 0);
            r.orientation = orientation;
            r.hasOrientation = true;
            return r;
        }
        if (b(d, 0) == 0x42 && b(d, 1) == 0x4D) {
            int w = b(d, 18) | (b(d, 19) << 8) | (b(d, 20) << 16) | (b(d, 21) << 24);
            int h = b(d, 22) | (b(d, 23) << 8) | (b(d, 24) << 16) | (b(d, 25) << 24);
            return dims("bmp", Math.abs((long) w), Math.abs((long) h));
        }
        return null;
    }

    private static Dims dims(String type, long w, long h) {
        Dims r = new Dims();
        r.type = type; r.width = w; r.height = h;
        return r;
    }

    private static long u32(byte[] d, int i) {
        return ((long) b(d, i) << 24) | (b(d, i + 1) << 16) | (b(d, i + 2) << 8) | b(d, i + 3);
    }

    static int exifOrientation(byte[] d, int t) {
        if (t + 8 > d.length) return 0;
        boolean le = b(d, t) == 0x49;
        long ifdOff = le ? (b(d, t + 4) | (b(d, t + 5) << 8) | (b(d, t + 6) << 16) | ((long) b(d, t + 7) << 24))
                : (((long) b(d, t + 4) << 24) | (b(d, t + 5) << 16) | (b(d, t + 6) << 8) | b(d, t + 7));
        long ifd = t + ifdOff;
        if (ifd + 2 > d.length) return 0;
        int n = u16(d, (int) ifd, le);
        for (int k = 0; k < n; k++) {
            long e = ifd + 2 + k * 12L;
            if (e + 12 > d.length) return 0;
            if (u16(d, (int) e, le) == 0x0112) return u16(d, (int) e + 8, le);
        }
        return 0;
    }

    private static int u16(byte[] d, int i, boolean le) {
        return le ? b(d, i) | (b(d, i + 1) << 8) : (b(d, i) << 8) | b(d, i + 1);
    }

    /** Вписать в квадрат side, как fit() в JS */
    public static long[] fit(long w, long h, long side) {
        if (Math.max(w, h) <= side) return new long[]{w, h};
        double r = side / (double) Math.max(w, h);
        return new long[]{Math.max(1, VideoPlanner.jsRound(w * r)), Math.max(1, VideoPlanner.jsRound(h * r))};
    }

    /** «Лёгкий» файл: JPEG/WebP с малым числом байт на пиксель — пересжимать незачем */
    public static boolean light(Dims info, long fileSize, long w, long h) {
        return info != null && ("jpeg".equals(info.type) || "webp".equals(info.type))
                && fileSize / (double) Math.max(1, w * h) <= LIGHT_BYTES_PER_PIXEL;
    }

    public static String renamed(String name, String ext) {
        String base = (name == null ? "photo" : name).replaceAll("\\.[^./\\\\]+$", "");
        if (base.isEmpty()) base = "photo";
        return base + ext;
    }
}
