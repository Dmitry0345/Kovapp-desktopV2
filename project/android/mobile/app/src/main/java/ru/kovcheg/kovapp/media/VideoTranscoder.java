package ru.kovcheg.kovapp.media;

import android.graphics.Bitmap;
import android.media.MediaCodec;
import android.media.MediaCodecInfo;
import android.media.MediaCodecList;
import android.media.MediaFormat;
import android.os.Build;
import android.os.Bundle;
import android.view.Surface;

import java.io.IOException;
import java.nio.Buffer;
import java.nio.ByteBuffer;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Сжатие видео аппаратным кодеком телефона — запасной путь, когда
 * страница не может сжать видео сама (нет WebCodecs, например по HTTP).
 * Логика кадров перенесена из run() в video-prep.js: та же обрезка по
 * времени, прореживание до 30 к/с, опорный кадр раз в 4 с, звук без
 * перекодирования, тот же формат файла.
 */
public final class VideoTranscoder {
    public interface Listener {
        /** Готовый кусок файла (ftyp+moov или moof+mdat). Может подождать, пока страница заберёт прежние. */
        void onData(byte[] data) throws IOException;
        void onProgress(double p);
        void onPoster(Bitmap frame);
    }

    /** Выбранный кодировщик */
    public static final class Encoder {
        public String name;
        public int profile, level;
        public boolean vbr;
        public long bitrate;
        public String profileName;
        public int maxRate;          // предел кодека, к/с (0 — неизвестен)
        public String speed = "";    // какая подсказка скорости принята
    }

    private static final long TIMEOUT_US = 5000;

    // ── возможности устройства ───────────────────────────────────────────
    public static String mimeFor(String family) {
        if ("avc".equals(family)) return MediaFormat.MIMETYPE_VIDEO_AVC;
        if ("hevc".equals(family)) return MediaFormat.MIMETYPE_VIDEO_HEVC;
        if ("vp9".equals(family)) return MediaFormat.MIMETYPE_VIDEO_VP9;
        if ("vp8".equals(family)) return MediaFormat.MIMETYPE_VIDEO_VP8;
        if ("av1".equals(family)) return "video/av01";
        return null;
    }

    public static MediaFormat decoderFormat(Mp4Demuxer.Track v) {
        Mp4Demuxer.Entry e = v.entry;
        MediaFormat f = MediaFormat.createVideoFormat(mimeFor(e.family), e.width, e.height);
        if ("avc".equals(e.family) && e.description != null) {
            NalUnits.Csd csd = NalUnits.fromAvcC(e.description);
            f.setByteBuffer("csd-0", ByteBuffer.wrap(csd.csd0));
            f.setByteBuffer("csd-1", ByteBuffer.wrap(csd.csd1));
        } else if ("hevc".equals(e.family) && e.description != null) {
            f.setByteBuffer("csd-0", ByteBuffer.wrap(NalUnits.fromHvcC(e.description).csd0));
        } else if ("av1".equals(e.family) && e.description != null) {
            f.setByteBuffer("csd-0", ByteBuffer.wrap(e.description));
        }
        int max = 0;
        for (int i = 0; i < v.count; i++) max = Math.max(max, v.size[i]);
        f.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, max + 4096);
        return f;
    }

    public static String findDecoder(Mp4Demuxer.Track v) {
        String mime = mimeFor(v.entry.family);
        if (mime == null) return null;
        try {
            return new MediaCodecList(MediaCodecList.REGULAR_CODECS).findDecoderForFormat(decoderFormat(v));
        } catch (RuntimeException e) {
            return null;
        }
    }

    static boolean hardware(MediaCodecInfo info) {
        if (Build.VERSION.SDK_INT >= 29) return info.isHardwareAccelerated();
        String n = info.getName().toLowerCase(Locale.ROOT);
        return !(n.startsWith("omx.google.") || n.startsWith("c2.android.") || n.contains("sw"));
    }

    static int levelConstant(int levelIdc) {
        switch (levelIdc) {
            case 0x1f: return MediaCodecInfo.CodecProfileLevel.AVCLevel31;
            case 0x28: return MediaCodecInfo.CodecProfileLevel.AVCLevel4;
            case 0x32: return MediaCodecInfo.CodecProfileLevel.AVCLevel5;
            default: return MediaCodecInfo.CodecProfileLevel.AVCLevel51;
        }
    }

    /** Кодировщик H.264 для размера и частоты: High → Main → Baseline, как в JS */
    public static Encoder chooseEncoder(int w, int h, int fps) {
        MediaCodecInfo[] all = new MediaCodecList(MediaCodecList.REGULAR_CODECS).getCodecInfos();
        List<MediaCodecInfo> list = new ArrayList<>();
        for (MediaCodecInfo i : all) {
            if (!i.isEncoder()) continue;
            for (String t : i.getSupportedTypes()) if (t.equalsIgnoreCase(MediaFormat.MIMETYPE_VIDEO_AVC)) { list.add(i); break; }
        }
        // родной кодек процессора → другие аппаратные → программные
        List<MediaCodecInfo> sorted = HardwareCodecs.ordered(list);
        int[] profiles = {MediaCodecInfo.CodecProfileLevel.AVCProfileHigh, MediaCodecInfo.CodecProfileLevel.AVCProfileMain, MediaCodecInfo.CodecProfileLevel.AVCProfileBaseline};
        String[] names = {"high", "main", "base"};
        for (int p = 0; p < profiles.length; p++) {
            for (MediaCodecInfo info : sorted) {
                MediaCodecInfo.CodecCapabilities caps;
                try { caps = info.getCapabilitiesForType(MediaFormat.MIMETYPE_VIDEO_AVC); } catch (RuntimeException e) { continue; }
                MediaCodecInfo.VideoCapabilities vc = caps.getVideoCapabilities();
                if (vc == null || !vc.isSizeSupported(w, h)) continue;
                boolean has = false;
                for (MediaCodecInfo.CodecProfileLevel pl : caps.profileLevels) if (pl.profile == profiles[p]) { has = true; break; }
                if (!has && profiles[p] != MediaCodecInfo.CodecProfileLevel.AVCProfileBaseline) continue;
                Encoder e = new Encoder();
                e.name = info.getName();
                e.profile = profiles[p];
                e.profileName = names[p];
                e.level = levelConstant(VideoPlanner.avcLevel(w, h, fps));
                MediaCodecInfo.EncoderCapabilities ec = caps.getEncoderCapabilities();
                e.vbr = ec != null && ec.isBitrateModeSupported(MediaCodecInfo.EncoderCapabilities.BITRATE_MODE_VBR);
                e.maxRate = HardwareCodecs.maxFrameRate(vc, w, h);
                long br = VideoPlanner.bitrate(w, h, fps);
                android.util.Range<Integer> range = vc.getBitrateRange();
                e.bitrate = Math.max(range.getLower(), Math.min(range.getUpper(), br));
                return e;
            }
        }
        return null;
    }

    static MediaFormat encoderFormat(Encoder e, int w, int h, int fps, boolean withProfile, int speed) {
        MediaFormat f = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, w, h);
        f.setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface);
        f.setInteger(MediaFormat.KEY_BIT_RATE, (int) Math.min(Integer.MAX_VALUE, e.bitrate));
        f.setInteger(MediaFormat.KEY_FRAME_RATE, fps);
        f.setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 4);
        if (e.vbr) f.setInteger(MediaFormat.KEY_BITRATE_MODE, MediaCodecInfo.EncoderCapabilities.BITRATE_MODE_VBR);
        if (withProfile) {
            f.setInteger(MediaFormat.KEY_PROFILE, e.profile);
            f.setInteger(MediaFormat.KEY_LEVEL, e.level);
        }
        HardwareCodecs.applySpeed(f, speed);
        return f;
    }

    /** Настроить кодировщик: подсказка скорости под процессор; не принял — скромнее, затем как раньше */
    static void configureEncoder(MediaCodec encoder, Encoder e, int w, int h, int fps) {
        RuntimeException last = null;
        for (int speed : CodecRanking.speedLadder(e.maxRate, w, h)) {
            for (boolean profile : new boolean[]{true, false}) {   // не все кодировщики принимают профиль явно
                try {
                    encoder.configure(encoderFormat(e, w, h, fps, profile, speed), null, null, MediaCodec.CONFIGURE_FLAG_ENCODE);
                    e.speed = speed > 0 ? speed + " к/с" : "обычная";
                    return;
                } catch (RuntimeException ex) {
                    last = ex;
                    encoder.reset();
                }
            }
        }
        throw last;
    }

    // ── прогон ───────────────────────────────────────────────────────────
    private final Source src;
    private final VideoPlanner.Analysis a;
    private final VideoPlanner.Edits edits;
    private final String decoderName;
    private final Encoder enc;
    private final int width, height, fps;
    private final boolean benchmark;
    private final int limitFrames;
    private final Listener listener;
    private final AtomicBoolean cancelled;
    public int keptFrames, encodedFrames;
    public final StageTimes times = new StageTimes();   // время по этапам — в журнал KovVideo

    public VideoTranscoder(Source src, VideoPlanner.Analysis a, VideoPlanner.Edits edits, String decoderName, Encoder enc,
                           int width, int height, int fps, boolean benchmark, int benchmarkFrames, Listener listener, AtomicBoolean cancelled) {
        this.src = src; this.a = a; this.edits = edits; this.decoderName = decoderName; this.enc = enc;
        this.width = width; this.height = height; this.fps = fps; this.benchmark = benchmark;
        this.limitFrames = benchmark ? benchmarkFrames : Integer.MAX_VALUE;
        this.listener = listener; this.cancelled = cancelled;
    }

    /** Кодировщик принимает настройки — без пробного сжатия: создать, настроить, освободить */
    // настройки, которые кодировщик уже принял: повторная проверка не нужна (экономит создание и освобождение кодека)
    private static final java.util.concurrent.ConcurrentHashMap<String, Long> PROBED = new java.util.concurrent.ConcurrentHashMap<>();
    private static final long PROBE_TTL_MS = 30 * 60 * 1000L;

    static void probeEncoder(Encoder enc, int w, int h, int fps) throws IOException {
        String key = enc.name + '|' + w + 'x' + h + '@' + fps;
        Long at = PROBED.get(key);
        if (at != null && System.currentTimeMillis() - at < PROBE_TTL_MS) return;
        CodecReleaser.awaitIdle(3000);
        MediaCodec c = null;
        try {
            c = MediaCodec.createByCodecName(enc.name);
            configureEncoder(c, enc, w, h, fps);
            c.createInputSurface().release();
            PROBED.put(key, System.currentTimeMillis());
        } catch (RuntimeException e) {
            throw new IOException("кодировщик не принял настройки: " + e.getMessage(), e);
        } finally {
            if (c != null) c.release();
        }
    }

    /** Упаковка кадра: без времени отправки, которая случается внутри упаковщика (она в «отправке») */
    private void muxDone(long since, long send0) {
        times.addNanos(StageTimes.MUX, Math.max(0, StageTimes.now() - since - (times.get(StageTimes.SEND) - send0)));
    }

    private void check() throws IOException { if (cancelled != null && cancelled.get()) throw new IOException("отменено"); }

    public void run() throws IOException {
        final long setup0 = StageTimes.now();
        Mp4Demuxer.Track v = a.video;
        final double trimStart = edits != null ? Math.max(0, edits.trimStart) * 1e6 : 0;
        final double trimEnd = edits != null && edits.trimEnd > 0 ? edits.trimEnd * 1e6 : Double.POSITIVE_INFINITY;
        final double posterAt = Math.min(1e6, (a.duration * 1e6) / 2) + trimStart;
        final double frameStep = 1e6 / fps;
        int rot = VideoPlanner.totalRotation(a, edits);
        float[] tex = FrameMapping.cornerTexCoords(width, height, v.entry.width, v.entry.height, rot, edits);
        boolean annexB = "avc".equals(v.entry.family) || "hevc".equals(v.entry.family);
        int nalLen = 4;
        if ("avc".equals(v.entry.family) && v.entry.description != null) nalLen = NalUnits.fromAvcC(v.entry.description).nalLength;
        if ("hevc".equals(v.entry.family) && v.entry.description != null) nalLen = NalUnits.fromHvcC(v.entry.description).nalLength;

        // начало: опорный кадр не позже начала обрезки
        int startIndex = 0;
        if (trimStart > 0) {
            for (int i = 0; i < v.count; i++) {
                double t = v.pts[i] * 1e6 / v.timescale;
                if (v.key[i] && t <= trimStart) startIndex = i;
                if (t > trimStart) break;
            }
        }
        // звук без перекодирования — те же границы и сдвиг, что в JS
        final Mp4Demuxer.Track au = benchmark ? null : a.audio;
        final int audioCount = au == null ? 0 : au.count;
        final double ats = a.audio != null ? a.audio.timescale : 1;
        int audioIndex = 0, audioEnd = audioCount;
        while (audioIndex < audioCount && au.dts[audioIndex] * 1e6 / ats < trimStart) audioIndex++;
        while (audioEnd > audioIndex && au.dts[audioEnd - 1] * 1e6 / ats >= trimEnd) audioEnd--;
        long audioShift = 0;
        if (au != null) {
            long first = audioIndex < audioCount ? au.dts[audioIndex] : 0;
            double firstUs = audioIndex < audioCount ? au.dts[audioIndex] * 1e6 / ats : 0;
            audioShift = first - VideoPlanner.jsRound(Math.max(0, firstUs - trimStart) * ats / 1e6);
        }

        // отметка активности для сторожа; ожидание страницы и сети (обрыв связи) простоем не считается
        final long[] lastActivity = {System.currentTimeMillis()};
        // отправка: передача готовых кусков странице, вместе с ожиданием, пока она их заберёт
        final FragmentedMp4Muxer muxer = new FragmentedMp4Muxer(data -> {
            long s0 = StageTimes.now();
            if (!benchmark) listener.onData(data);
            times.add(StageTimes.SEND, s0);
            lastActivity[0] = System.currentTimeMillis();
        }, 1);   // фрагмент — 1 с видео: данные для отправки появляются чаще
        MediaCodec decoder = null, encoder = null;
        GlRenderer gl = null;
        Surface encSurface = null;
        try {
            CodecReleaser.awaitIdle(3000);   // прежние кодеки освобождены — только тогда создаём новые
            encoder = MediaCodec.createByCodecName(enc.name);
            configureEncoder(encoder, enc, width, height, fps);
            encSurface = encoder.createInputSurface();
            gl = new GlRenderer(encSurface, width, height);
            encoder.start();
            decoder = MediaCodec.createByCodecName(decoderName);
            MediaFormat fast = decoderFormat(v);
            // декодеру — та же скорость, что кодировщику (как в системном перекодировщике Android)
            HardwareCodecs.applySpeed(fast, enc.maxRate > 0 ? enc.maxRate : CodecRanking.defaultRate(width, height));
            try {
                decoder.configure(fast, gl.decoderSurface(), null, 0);
            } catch (RuntimeException e) {
                decoder.reset();
                decoder.configure(decoderFormat(v), gl.decoderSurface(), null, 0);
            }
            decoder.start();

            times.add(StageTimes.SETUP, setup0);
            MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
            boolean inputDone = false, decoderDone = false, encoderDone = false, posterDone = listener == null || benchmark;
            double nextKeep = Double.NEGATIVE_INFINITY, lastKey = Double.NEGATIVE_INFINITY;
            long lastProgress = 0;
            byte[] avcC = null;
            boolean muxerReady = false;
            int i = startIndex;
            while (!encoderDone) {
                check();
                if (System.currentTimeMillis() - lastActivity[0] > VideoPlanner.WATCHDOG_MS) throw new IOException("сжатие остановилось");
                // 1. подать кадр декодеру
                if (!inputDone) {
                    long d0 = StageTimes.now();
                    int idx = decoder.dequeueInputBuffer(TIMEOUT_US);
                    times.add(StageTimes.DECODE, d0);
                    if (idx >= 0) {
                        double t = i < v.count ? v.pts[i] * 1e6 / v.timescale : 0;
                        boolean stop = i >= v.count || keptFrames >= limitFrames || (t > trimEnd + 2e6 && v.key[i]);
                        if (stop) {
                            decoder.queueInputBuffer(idx, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM);
                            inputDone = true;
                        } else {
                            long r0 = StageTimes.now();
                            byte[] sample = Mp4Demuxer.readSample(src, v, i);
                            byte[] data = annexB ? NalUnits.toAnnexB(sample, nalLen) : sample;
                            times.add(StageTimes.READ, r0);
                            ByteBuffer buf = decoder.getInputBuffer(idx);
                            if (buf == null || data.length > buf.capacity()) throw new IOException("кадр больше буфера декодера");
                            ((Buffer) buf).clear();
                            buf.put(data);
                            decoder.queueInputBuffer(idx, 0, data.length, VideoPlanner.jsRound(t), v.key[i] ? MediaCodec.BUFFER_FLAG_KEY_FRAME : 0);
                            if (muxerReady) {
                                while (audioIndex < audioEnd && au.dts[audioIndex] * 1e6 / ats <= t) {
                                    long dur = audioIndex + 1 < audioCount ? au.dts[audioIndex + 1] - au.dts[audioIndex]
                                            : (audioIndex > 0 ? au.dts[audioIndex] - au.dts[audioIndex - 1] : 1024);
                                    long ar0 = StageTimes.now();
                                    byte[] audio = Mp4Demuxer.readSample(src, au, audioIndex);
                                    times.add(StageTimes.READ, ar0);
                                    { long mx0 = StageTimes.now(), sd0 = times.get(StageTimes.SEND); muxer.addAudioSample(audio, au.dts[audioIndex] - audioShift, dur); muxDone(mx0, sd0); }
                                    audioIndex++;
                                }
                                { long mx0 = StageTimes.now(), sd0 = times.get(StageTimes.SEND); muxer.maybeFlush(); muxDone(mx0, sd0); }
                            }
                            if (listener != null && !benchmark && a.duration > 0 && System.currentTimeMillis() - lastProgress > 100) {
                                lastProgress = System.currentTimeMillis();
                                listener.onProgress(Math.min(0.99, (t - trimStart) / 1e6 / Math.max(0.1, Math.min(a.duration, trimEnd / 1e6) - trimStart / 1e6)));
                            }
                            i++;
                        }
                    }
                }
                // 2. кадр из декодера → OpenGL → кодировщик
                if (!decoderDone) {
                    long d1 = StageTimes.now();
                    int st = decoder.dequeueOutputBuffer(info, TIMEOUT_US);
                    times.add(StageTimes.DECODE, d1);
                    if (st >= 0) {
                        boolean eos = (info.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0;
                        double ts = info.presentationTimeUs;
                        boolean keep = info.size != 0 && !(ts < trimStart || ts > trimEnd || ts < nextKeep - frameStep * 0.25 || keptFrames >= limitFrames);
                        if (keep) nextKeep = (nextKeep == Double.NEGATIVE_INFINITY ? ts : Math.max(nextKeep, ts - frameStep * 0.5)) + frameStep;
                        long w0 = StageTimes.now();
                        decoder.releaseOutputBuffer(st, keep);
                        if (keep) gl.awaitFrame(2500);                 // кадр декодера доходит до текстуры
                        times.add(StageTimes.DECODE, w0);
                        if (keep) {
                            long g0 = StageTimes.now();
                            gl.draw(tex);
                            if (ts - lastKey >= VideoPlanner.KEY_EVERY_US) {
                                lastKey = ts;
                                Bundle b = new Bundle();
                                b.putInt(MediaCodec.PARAMETER_KEY_REQUEST_SYNC_FRAME, 0);
                                encoder.setParameters(b);
                            }
                            if (!posterDone && ts >= posterAt) {
                                posterDone = true;
                                Bitmap poster = gl.readFrame();
                                if (poster != null) listener.onPoster(poster);   // нет — обложку сделает сервер
                            }
                            times.add(StageTimes.DRAW, g0);
                            long e0 = StageTimes.now();
                            gl.present((long) (ts * 1000));                 // ждёт, если кодировщик не успевает принимать
                            times.add(StageTimes.ENCODE, e0);
                            keptFrames++;
                            lastActivity[0] = System.currentTimeMillis();
                        }
                        if (eos) {
                            encoder.signalEndOfInputStream();
                            decoderDone = true;
                        }
                    }
                }
                // 3. забрать всё, что выдал кодировщик
                while (true) {
                    long e1 = StageTimes.now();
                    int st = encoder.dequeueOutputBuffer(info, decoderDone ? TIMEOUT_US : 0);
                    times.add(StageTimes.ENCODE, e1);
                    if (st == MediaCodec.INFO_TRY_AGAIN_LATER) break;
                    if (st == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                        MediaFormat of = encoder.getOutputFormat();
                        if (avcC == null && of.containsKey("csd-0") && of.containsKey("csd-1")) {
                            byte[] c0 = bytes(of.getByteBuffer("csd-0")), c1 = bytes(of.getByteBuffer("csd-1"));
                            byte[] both = new byte[c0.length + c1.length];
                            System.arraycopy(c0, 0, both, 0, c0.length);
                            System.arraycopy(c1, 0, both, c0.length, c1.length);
                            avcC = NalUnits.avcCFromCodecConfig(both, 0, both.length);
                        }
                        continue;
                    }
                    if (st < 0) continue;
                    ByteBuffer out = encoder.getOutputBuffer(st);
                    byte[] data = new byte[info.size];
                    if (out != null && info.size > 0) {
                        ((Buffer) out).position(info.offset);
                        ((Buffer) out).limit(info.offset + info.size);
                        out.get(data);
                    }
                    boolean config = (info.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) != 0;
                    if (config) {
                        if (avcC == null && data.length > 0) avcC = NalUnits.avcCFromCodecConfig(data, 0, data.length);
                    } else if (data.length > 0) {
                        if (!muxerReady) {
                            if (avcC == null) avcC = NalUnits.avcCFromCodecConfig(data, 0, data.length);
                            muxer.addVideo(NalUnits.avcCodecString(avcC), width, height, avcC);
                            if (au != null) muxer.addAudio(au.timescale, au.entry.raw);
                            muxerReady = true;
                        }
                        { long mx0 = StageTimes.now(), sd0 = times.get(StageTimes.SEND); muxer.addVideoChunk(NalUnits.annexBToAvcc(data, 0, data.length), VideoPlanner.jsRound(info.presentationTimeUs - trimStart),
                                (info.flags & MediaCodec.BUFFER_FLAG_KEY_FRAME) != 0); muxDone(mx0, sd0); }
                        encodedFrames++;
                        lastActivity[0] = System.currentTimeMillis();
                    }
                    encoder.releaseOutputBuffer(st, false);
                    if ((info.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0) { encoderDone = true; break; }
                }
            }
            if (!muxerReady) throw new IOException("кодировщик не выдал ни одного кадра");
            while (audioIndex < audioEnd) {
                long dur = audioIndex + 1 < audioCount ? au.dts[audioIndex + 1] - au.dts[audioIndex]
                        : (audioIndex > 0 ? au.dts[audioIndex] - au.dts[audioIndex - 1] : 1024);
                long ar1 = StageTimes.now();
                byte[] audio = Mp4Demuxer.readSample(src, au, audioIndex);
                times.add(StageTimes.READ, ar1);
                { long mx0 = StageTimes.now(), sd0 = times.get(StageTimes.SEND); muxer.addAudioSample(audio, au.dts[audioIndex] - audioShift, dur); muxDone(mx0, sd0); }
                audioIndex++;
            }
            muxer.finish();
        } catch (IllegalStateException e) {
            throw new IOException("кодек телефона отказал: " + e.getMessage(), e);
        } finally {
            long f0 = StageTimes.now();   // этап «завершение»: только то, что нельзя отложить
            // декодер останавливается сразу (он выводит в поверхность графики), графика — в своём потоке (EGL);
            // освобождение кодеков — в фоне: результат отдаётся без ожидания
            if (decoder != null) { try { decoder.stop(); } catch (RuntimeException ignore) { /* */ } }
            if (gl != null) gl.release();
            final MediaCodec dec = decoder, encd = encoder;
            final Surface es = encSurface;
            final boolean quiet = benchmark;
            CodecReleaser.submit(() -> {
                long r0 = System.nanoTime();
                if (dec != null) { try { dec.release(); } catch (RuntimeException ignore) { /* */ } }
                long r1 = System.nanoTime();
                if (encd != null) {
                    try { encd.stop(); } catch (RuntimeException ignore) { /* */ }
                    try { encd.release(); } catch (RuntimeException ignore) { /* */ }
                }
                if (es != null) es.release();
                long r2 = System.nanoTime();
                if (!quiet) android.util.Log.i("KovVideo", String.format(java.util.Locale.ROOT,
                        "освобождение кодеков в фоне: декодер %d мс, кодировщик %d мс", (r1 - r0) / 1_000_000, (r2 - r1) / 1_000_000));
            });
            times.add(StageTimes.FINISH, f0);
        }
    }

    static byte[] bytes(ByteBuffer b) {
        ByteBuffer d = b.duplicate();
        ((Buffer) d).position(0);
        byte[] out = new byte[d.remaining()];
        d.get(out);
        return out;
    }
}
