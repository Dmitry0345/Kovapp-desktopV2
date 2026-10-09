package ru.kovcheg.kovapp.media;

import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Pattern;

import ru.kovcheg.kovapp.core.Json;

/**
 * Решения о сжатии видео — перенос формул web_app/js/media/video-prep.js:
 * когда файл уже готов, до какого размера ужимать, какой битрейт, сколько
 * кадров пробного прогона и как считать прогноз времени.
 */
public final class VideoPlanner {
    private VideoPlanner() {}

    public static final int FULL_SIDE = 1920, HD_SIDE = 1280, MAX_FPS = 30;
    public static final double BPP = 0.07, READY_BPP = 0.075;
    public static final long KEY_EVERY_US = 4_000_000L;
    public static final int TIME_LIMIT_S = 600;
    public static final long WATCHDOG_MS = 20_000;
    public static final List<String> AUDIO_PASS = Arrays.asList(
            "mp4a", "Opus", "ac-3", "ec-3", "alac", "fLaC", "sowt", "twos", "ipcm", "lpcm", "samr", ".mp3");

    private static final Pattern READY_CODEC = Pattern.compile("^avc1\\.(42|4d|58|64)", Pattern.CASE_INSENSITIVE);
    private static final Pattern READY_AUDIO = Pattern.compile("^mp4a\\.40\\.|^mp3$");

    /** Правки из окна отправки: обрезка по времени, поворот, кадрирование */
    public static final class Edits {
        public double trimStart, trimEnd;
        public int rotate;
        public double cropX, cropY, cropW = 1, cropH = 1;
        public boolean hasCrop;

        public boolean cropActive() { return hasCrop && (cropW < 0.999 || cropH < 0.999); }
        public boolean any() { return trimStart != 0 || trimEnd != 0 || rotate != 0 || cropActive(); }

        /** Из объекта JS; неверные значения отбрасываются так же, как Number(...) || 0 */
        @SuppressWarnings("unchecked")
        public static Edits from(Map<String, Object> m) {
            if (m == null) return null;
            Edits e = new Edits();
            e.trimStart = number(m.get("trim_start"));
            e.trimEnd = number(m.get("trim_end"));
            // интерфейс даёт 0/90/180/270; приводим к [0, 360), чтобы размер и поворот не разошлись
            e.rotate = Math.floorMod((int) number(m.get("rotate")), 360);
            Object c = m.get("crop");
            if (c instanceof Map) {
                Map<String, Object> cm = (Map<String, Object>) c;
                e.hasCrop = true;
                e.cropX = clamp01(number(cm.get("x")));
                e.cropY = clamp01(number(cm.get("y")));
                e.cropW = cm.get("w") == null ? 1 : clamp01(number(cm.get("w")));
                e.cropH = cm.get("h") == null ? 1 : clamp01(number(cm.get("h")));
            }
            return e;
        }

        static double number(Object o) {
            if (o instanceof Number) { double d = ((Number) o).doubleValue(); return Double.isNaN(d) ? 0 : d; }
            if (o instanceof String) { try { return Double.parseDouble(((String) o).trim()); } catch (NumberFormatException ex) { return 0; } }
            if (o instanceof Boolean) return ((Boolean) o) ? 1 : 0;
            return 0;
        }

        static double clamp01(double v) { return Math.max(0, Math.min(1, v)); }
    }

    /** Итог разбора исходника (analyze в JS) */
    public static final class Analysis {
        public Mp4Demuxer.Info info;
        public Mp4Demuxer.Track video, audio;
        public int width, height;
        public double fps, duration, bitrate;
        public int frames;
    }

    public static int[] evenFit(double w, double h, double side) {
        double r = Math.min(1, side / Math.max(w, h));
        return new int[]{(int) Math.max(2, jsRound(w * r / 2) * 2), (int) Math.max(2, jsRound(h * r / 2) * 2)};
    }

    /** Math.round из JS: половина округляется вверх */
    static long jsRound(double v) { return (long) Math.floor(v + 0.5); }

    public static String avcCodec(int w, int h, double fps, String profile) {
        double mbps = Math.ceil(w / 16.0) * Math.ceil(h / 16.0) * fps;
        int level = mbps <= 108000 ? 0x1f : mbps <= 245760 ? 0x28 : mbps <= 522240 ? 0x32 : 0x33;
        String p = "high".equals(profile) ? "6400" : "main".equals(profile) ? "4d00" : "42e0";
        return "avc1." + p + String.format(Locale.ROOT, "%02x", level);
    }

    public static int avcLevel(int w, int h, double fps) {
        return Integer.parseInt(avcCodec(w, h, fps, "high").substring(9), 16);
    }

    public static double fpsOf(Mp4Demuxer.Track t) {
        int n = t.count;
        if (n < 2) return 30;
        double span = (t.dts[n - 1] - t.dts[0]) / (double) t.timescale;
        return span > 0 ? (n - 1) / span : 30;
    }

    public static Analysis analyze(Mp4Demuxer.Info info) {
        Mp4Demuxer.Track v = info.video;
        if (v == null || v.entry == null) throw new IllegalArgumentException("в файле нет видеодорожки");
        Analysis a = new Analysis();
        a.info = info;
        a.video = v;
        a.audio = info.audio;
        boolean turned = v.rotation == 90 || v.rotation == 270;
        a.width = turned ? v.entry.height : v.entry.width;
        a.height = turned ? v.entry.width : v.entry.height;
        a.fps = fpsOf(v);
        a.duration = info.duration != 0 ? info.duration : v.duration;
        long bytes = v.bytes();
        a.bitrate = a.duration != 0 ? bytes * 8 / a.duration : 0;
        a.frames = v.count;
        return a;
    }

    public static boolean alreadyReady(Analysis a, Edits edits) {
        if (edits != null && edits.any()) return false;
        Mp4Demuxer.Entry e = a.video.entry;
        String audioCodec = a.audio != null && a.audio.entry != null && a.audio.entry.codec != null ? a.audio.entry.codec : "";
        boolean audioOk = a.audio == null || READY_AUDIO.matcher(audioCodec).find();
        double fps = Math.min(a.fps, MAX_FPS);
        return "avc".equals(e.family) && e.codec != null && READY_CODEC.matcher(e.codec).find()
                && Math.max(a.width, a.height) <= FULL_SIDE && a.fps <= MAX_FPS + 1
                && a.bitrate <= READY_BPP * a.width * a.height * fps && audioOk && a.video.rotation == 0
                && a.info.audioTracks <= 1;
    }

    public static int[] outputSize(Analysis a, int side, Edits edits) {
        double w = a.width, h = a.height;
        boolean extra = edits != null && edits.rotate % 180 == 90;
        if (extra) { double t = w; w = h; h = t; }
        if (edits != null && edits.cropActive()) { w = jsRound(w * edits.cropW); h = jsRound(h * edits.cropH); }
        return evenFit(w, h, side);
    }

    /** Кадров в пробном прогоне */
    public static int benchmarkFrames(int fps) { return (int) Math.min(45, Math.max(10, jsRound(fps * 1.5))); }

    public static int targetFps(double sourceFps) { return (int) Math.min(MAX_FPS, Math.max(1, jsRound(sourceFps))); }

    public static long bitrate(int w, int h, int fps) { return jsRound(BPP * w * h * fps); }

    /** Длительность результата с учётом обрезки — для прогноза */
    public static double editedDuration(Analysis a, Edits e) {
        if (e != null && (e.trimEnd != 0 || e.trimStart != 0)) {
            return Math.max(0.5, (e.trimEnd != 0 ? e.trimEnd : a.duration) - e.trimStart);
        }
        return a.duration;
    }

    /** Длительность для meta.duration (как в media-prep.js) */
    public static double metaDuration(Analysis a, Edits e) {
        if (e != null && (e.trimEnd != 0 || e.trimStart != 0)) {
            return Math.max(0, (e.trimEnd != 0 ? e.trimEnd : a.duration) - e.trimStart);
        }
        return a.duration;
    }

    /** Оценка без пробного сжатия: не медленнее реального времени, быстрее — по скорости кодировщика */
    public static double quickEstimate(double editedSeconds, int fps, int encoderRate) {
        double rate = Math.max(fps, encoderRate > 0 ? encoderRate : fps);
        return Math.max(0, editedSeconds) * fps / Math.max(1, rate);
    }

    public static double estimateSeconds(double editedDuration, int fps, int benchFrames, double benchSeconds) {
        double rate = benchFrames / Math.max(0.05, benchSeconds);
        return editedDuration * fps / rate * 1.15;
    }

    /** Дольше — сразу HD 1280: сжатие примерно вдвое быстрее, файл в ~2,25 раза меньше (как в JS) */
    public static final double LONG_VIDEO_S = 60;

    /** Стороны для перебора: на слабой памяти — только HD */
    public static int[] sides(long totalMemoryBytes) {
        return sides(totalMemoryBytes, 0);
    }

    /** То же с учётом длительности: ролик длиннее минуты — только HD */
    public static int[] sides(long totalMemoryBytes, double editedDuration) {
        // navigator.deviceMemory ≤ 2 ГБ — телефон на «2 ГБ» показывает около 1.8–2.0 ГБ
        boolean low = totalMemoryBytes > 0 && totalMemoryBytes <= 2_200_000_000L;
        return low || editedDuration > LONG_VIDEO_S ? new int[]{HD_SIDE} : new int[]{FULL_SIDE, HD_SIDE};
    }

    /** Пропустить HD-вариант, если он меньше нужного (как в JS) */
    public static boolean skipSide(Analysis a, int side, int[] wh) {
        return side == HD_SIDE && Math.max(wh[0], wh[1]) < Math.min(HD_SIDE, Math.max(a.width, a.height));
    }

    /** Итоговый поворот кадра: из файла + правка пользователя */
    public static int totalRotation(Analysis a, Edits e) {
        int r = (a.video.rotation + (e != null ? e.rotate : 0)) % 360;
        return r < 0 ? r + 360 : r;
    }

    /** Предварительные отказы без устройства: звук, дорожки, готовность */
    public static String precheck(Analysis a, Edits edits) {
        if (a.audio != null && a.audio.entry != null && !AUDIO_PASS.contains(a.audio.entry.fourcc)) return "audio:" + a.audio.entry.fourcc;
        // несколько звуковых дорожек: сжимается с первой (как в браузере), сервер их не получает
        if (alreadyReady(a, edits)) return "ready";
        return null;
    }

    public static Map<String, Object> describe(Analysis a) {
        return Json.map("width", a.width, "height", a.height, "fps", a.fps, "duration", a.duration, "bitrate", a.bitrate, "frames", a.frames);
    }
}
