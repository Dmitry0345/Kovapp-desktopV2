package ru.kovcheg.kovapp.media;

import android.app.ActivityManager;
import android.content.Context;
import android.graphics.Bitmap;

import java.io.FileOutputStream;
import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

import ru.kovcheg.kovapp.core.Json;
import ru.kovcheg.kovapp.files.PickedFiles;
import ru.kovcheg.kovapp.files.TempFiles;

/**
 * Обработка файлов на устройстве по командам страницы: отпечаток файла,
 * решение и сжатие видео, запасное сжатие фото. Долгие операции — в
 * фоновых потоках; прогресс и куски результата уходят событиями моста.
 */
public final class MediaJobs {
    public interface Events { void emit(String event, Map<String, Object> data); }

    static final long MAX_PENDING_BYTES = 128L * 1024 * 1024;

    private final Context ctx;
    private final PickedFiles picked;
    private final TempFiles temp;
    private final AtomicInteger ids = new AtomicInteger(1);
    private final Map<String, AtomicBoolean> cancels = new ConcurrentHashMap<>();
    private final Map<String, Plan> plans = new ConcurrentHashMap<>();
    private final Object pendingLock = new Object();
    private long pendingBytes;
    private final Map<String, Long> pendingSizes = new ConcurrentHashMap<>();

    static final class Plan {
        String handle;
        VideoPlanner.Edits edits;
        String decoder;
        VideoTranscoder.Encoder encoder;
        int width, height, fps;
    }

    /** Температура и частоты — в журнал сжатия (что телефон не отдаёт — пропускается) */
    private String thermal() {
        Integer battery = null;
        try {
            android.content.Intent b = ctx.registerReceiver(null, new android.content.IntentFilter("android.intent.action.BATTERY_CHANGED"));
            if (b != null) { int t = b.getIntExtra("temperature", -1); if (t > 0) battery = t; }
        } catch (RuntimeException ignore) { /* нет данных о батарее */ }
        try { return ThermalProbe.device().snapshot(battery); } catch (RuntimeException e) { return "нет данных"; }
    }

    public MediaJobs(Context ctx, PickedFiles picked, TempFiles temp) {
        this.ctx = ctx.getApplicationContext();
        this.picked = picked;
        this.temp = temp;
    }

    public String newJob() {
        String id = "j" + ids.getAndIncrement();
        cancels.put(id, new AtomicBoolean(false));
        return id;
    }

    public void cancel(String job) {
        AtomicBoolean c = cancels.get(job);
        if (c != null) c.set(true);
        synchronized (pendingLock) { pendingLock.notifyAll(); }
    }

    private AtomicBoolean flag(String job) {
        AtomicBoolean c = cancels.get(job);
        if (c == null) { c = new AtomicBoolean(false); cancels.put(job, c); }
        return c;
    }

    // ── файлы со страницы ────────────────────────────────────────────────
    public List<Object> resolve(List<Object> files) {
        List<Object> out = new ArrayList<>();
        for (Object o : files) {
            if (!(o instanceof Map)) { out.add(null); continue; }
            @SuppressWarnings("unchecked") Map<String, Object> m = (Map<String, Object>) o;
            String name = Json.str(m, "name", null);
            long size = Json.num(m, "size", -1);
            out.add(name == null ? null : picked.resolve(name, size));
        }
        return out;
    }

    // ── отпечаток ────────────────────────────────────────────────────────
    public String hashTree(String handle, final String job, final Events ev) throws IOException {
        final AtomicBoolean c = flag(job);
        try (Source s = picked.open(handle)) {
            final long[] last = {0};
            return FileHasher.tree(s, p -> {
                long now = System.currentTimeMillis();
                if (now - last[0] > 150 || p >= 1) { last[0] = now; ev.emit("media.progress", Json.map("job", job, "p", p)); }
            }, c::get);
        } finally {
            cancels.remove(job);
        }
    }

    public String hashSample(String handle) throws IOException {
        try (Source s = picked.open(handle)) { return FileHasher.sample(s); }
    }

    public String hashRange(String handle, long offset, long length) throws IOException {
        try (Source s = picked.open(handle)) { return FileHasher.range(s, offset, length); }
    }

    // ── фото ─────────────────────────────────────────────────────────────
    public Map<String, Object> image(String handle, int maxSide) throws IOException {
        PickedFiles.Entry e = picked.get(handle);
        if (e == null) throw new IOException("файл не выбирался в приложении");
        return NativeImage.prepare(ctx.getContentResolver(), e, temp, maxSide);
    }

    // ── видео ────────────────────────────────────────────────────────────
    long totalMemory() {
        ActivityManager am = (ActivityManager) ctx.getSystemService(Context.ACTIVITY_SERVICE);
        ActivityManager.MemoryInfo mi = new ActivityManager.MemoryInfo();
        if (am != null) am.getMemoryInfo(mi);
        return mi.totalMem;
    }

    /** Решение как plan() в video-prep.js: as-is / device / server с причиной; каждое — строкой в журнал KovVideo */
    public Map<String, Object> videoPlan(String handle, Map<String, Object> editsMap) {
        long t0 = System.nanoTime();
        Map<String, Object> out = planVideo(handle, editsMap);
        String mode = String.valueOf(out.get("mode"));
        String what = "as-is".equals(mode) ? "без сжатия — видео уже подходит"
                : "device".equals(mode) ? "сжатие на телефоне"
                : "сжатие на сервере, причина: " + out.get("reason");
        android.util.Log.i("KovVideo", "решение: " + what + " (" + (System.nanoTime() - t0) / 1_000_000 + " мс на проверку)");
        return out;
    }

    private Map<String, Object> planVideo(String handle, Map<String, Object> editsMap) {
        Map<String, Object> out = Json.map("mode", "server", "reason", "");
        VideoPlanner.Edits edits = VideoPlanner.Edits.from(editsMap);
        VideoPlanner.Analysis a;
        try (Source s = picked.open(handle)) {
            a = VideoPlanner.analyze(Mp4Demuxer.demux(s));
        } catch (IOException | RuntimeException e) {
            out.put("reason", "demux: " + e.getMessage());
            return out;
        }
        out.put("duration", a.duration);
        out.put("analysis", VideoPlanner.describe(a));
        String pre = VideoPlanner.precheck(a, edits);
        if ("ready".equals(pre)) { out.put("mode", "as-is"); out.put("reason", "ready"); return out; }
        if (pre != null) { out.put("reason", pre); return out; }
        if (VideoPlanner.totalRotation(a, edits) % 90 != 0) { out.put("reason", "rotation"); return out; }
        String decoder = VideoTranscoder.findDecoder(a.video);
        if (decoder == null) { out.put("reason", "decoder:" + a.video.entry.codec); return out; }
        int fps = VideoPlanner.targetFps(a.fps);
        double edited = VideoPlanner.editedDuration(a, edits);
        String reason = "";
        for (int side : VideoPlanner.sides(totalMemory(), edited)) {
            int[] wh = VideoPlanner.outputSize(a, side, edits);
            if (VideoPlanner.skipSide(a, side, wh)) continue;
            VideoTranscoder.Encoder enc = VideoTranscoder.chooseEncoder(wh[0], wh[1], fps);
            if (enc == null) continue;
            double estimate;
            if (edited <= VideoPlanner.LONG_VIDEO_S) {
                // короткий ролик укладывается в лимит и так: вместо пробного сжатия — проверка настроек кодировщика
                try {
                    VideoTranscoder.probeEncoder(enc, wh[0], wh[1], fps);
                } catch (IOException e) {
                    reason = "encoder";
                    continue;
                }
                estimate = VideoPlanner.quickEstimate(edited, fps, enc.maxRate);
            } else {
                int frames = VideoPlanner.benchmarkFrames(fps);
                long t0 = System.nanoTime();
                try (Source s = picked.open(handle)) {
                    new VideoTranscoder(s, a, edits, decoder, enc, wh[0], wh[1], fps, true, frames, null, new AtomicBoolean(false)).run();
                } catch (IOException | RuntimeException e) {
                    reason = "encoder";
                    continue;
                }
                double seconds = (System.nanoTime() - t0) / 1e9;
                estimate = VideoPlanner.estimateSeconds(edited, fps, frames, seconds);
            }
            if (estimate <= VideoPlanner.TIME_LIMIT_S) {
                Plan p = new Plan();
                p.handle = handle; p.edits = edits; p.decoder = decoder; p.encoder = enc; p.width = wh[0]; p.height = wh[1]; p.fps = fps;
                String job = newJob();
                plans.put(job, p);
                out.put("mode", "device");
                out.put("reason", "");
                out.put("job", job);
                out.put("width", wh[0]);
                out.put("height", wh[1]);
                out.put("fps", fps);
                out.put("estimate", estimate);
                out.put("side", side);
                out.put("codec", enc.profileName);
                out.put("encoder", enc.name);
                out.put("speed", enc.speed);
                android.util.Log.i("KovVideo", "видео " + wh[0] + "x" + wh[1] + ", кодек " + enc.name + ", скорость " + enc.speed
                        + ", оценка " + Math.round(estimate) + " с");
                return out;
            }
            reason = "slow:" + Math.round(estimate) + "s";
        }
        out.put("reason", reason.isEmpty() ? "encoder" : reason);
        return out;
    }

    /** Сжатие по готовому плану. Куски файла — временными файлами для страницы. */
    public Map<String, Object> videoRun(final String job, final Events ev) throws IOException {
        final Plan p = plans.remove(job);
        if (p == null) throw new IOException("план сжатия не найден");
        final AtomicBoolean c = flag(job);
        final AtomicInteger index = new AtomicInteger(0);
        final long started = System.nanoTime();
        final java.util.concurrent.atomic.AtomicLong written = new java.util.concurrent.atomic.AtomicLong();
        try (Source s = picked.open(p.handle)) {
            long parse0 = System.nanoTime();   // разбор исходного файла — до сжатия, этап «разбор файла»
            VideoPlanner.Analysis a = VideoPlanner.analyze(Mp4Demuxer.demux(s));
            final long parseNanos = System.nanoTime() - parse0;
            final VideoTranscoder[] self = new VideoTranscoder[1];
            VideoTranscoder t = new VideoTranscoder(s, a, p.edits, p.decoder, p.encoder, p.width, p.height, p.fps, false, 0,
                    new VideoTranscoder.Listener() {
                        @Override public void onData(byte[] data) throws IOException {
                            waitForRoom(data.length, c);
                            written.addAndGet(data.length);
                            TempFiles.Item it = temp.create(".bin", "application/octet-stream");
                            try (FileOutputStream fo = new FileOutputStream(it.file)) { fo.write(data); }
                            synchronized (pendingLock) { pendingBytes += data.length; }
                            pendingSizes.put(it.token, (long) data.length);
                            ev.emit("media.segment", Json.map("job", job, "index", index.getAndIncrement(), "url", TempFiles.relUrl(it), "size", data.length));
                        }
                        @Override public void onProgress(double pr) { ev.emit("media.progress", Json.map("job", job, "p", pr)); }
                        @Override public void onPoster(Bitmap frame) {
                            try {
                                int w = frame.getWidth(), h = frame.getHeight();
                                double r = Math.min(1, 640.0 / Math.max(w, h));
                                Bitmap small = r < 1 ? Bitmap.createScaledBitmap(frame, Math.max(1, (int) Math.round(w * r)), Math.max(1, (int) Math.round(h * r)), true) : frame;
                                TempFiles.Item it = temp.create(".jpg", "image/jpeg");
                                try (FileOutputStream fo = new FileOutputStream(it.file)) { small.compress(Bitmap.CompressFormat.JPEG, 92, fo); }
                                ev.emit("media.poster", Json.map("job", job, "url", TempFiles.relUrl(it), "width", w, "height", h));
                                if (small != frame) small.recycle();
                            } catch (IOException e) { /* без обложки */ } finally { frame.recycle(); }
                        }
                    }, c);
            self[0] = t;
            t.times.addNanos(StageTimes.PARSE, parseNanos);
            android.util.Log.i("KovVideo", "нагрев перед сжатием: " + thermal());
            t.run();
            android.util.Log.i("KovVideo", "нагрев после сжатия: " + thermal());
            double secs = (System.nanoTime() - started) / 1e9;
            android.util.Log.i("KovVideo", String.format(java.util.Locale.ROOT,
                    "сжато за %.1f с: %dx%d, %.1f МБ, кадров %d (%.0f к/с), кодек %s, скорость %s",
                    secs, p.width, p.height, written.get() / 1048576.0, t.encodedFrames,
                    t.encodedFrames / Math.max(0.001, secs), p.encoder.name, p.encoder.speed));
            android.util.Log.i("KovVideo", "этапы: " + t.times.format(System.nanoTime() - started));
            return Json.map("frames", t.keptFrames, "encoded", t.encodedFrames, "segments", index.get());
        } finally {
            cancels.remove(job);
        }
    }

    private void waitForRoom(int size, AtomicBoolean c) throws IOException {
        synchronized (pendingLock) {
            while (pendingBytes + size > MAX_PENDING_BYTES && pendingBytes > 0) {
                if (c.get()) throw new IOException("отменено");
                try { pendingLock.wait(500); } catch (InterruptedException e) { throw new IOException("прервано"); }
            }
        }
    }

    /** Страница забрала временный файл — удаляем и освобождаем место в очереди */
    public boolean release(String url) {
        String token = url == null ? "" : url.substring(url.lastIndexOf('/') + 1);
        Long size = pendingSizes.remove(token);
        if (size != null) synchronized (pendingLock) { pendingBytes = Math.max(0, pendingBytes - size); pendingLock.notifyAll(); }
        return temp.release(token);
    }

    public void clear() {
        for (AtomicBoolean c : cancels.values()) c.set(true);
        plans.clear();
        pendingSizes.clear();
        synchronized (pendingLock) { pendingBytes = 0; pendingLock.notifyAll(); }
    }
}
