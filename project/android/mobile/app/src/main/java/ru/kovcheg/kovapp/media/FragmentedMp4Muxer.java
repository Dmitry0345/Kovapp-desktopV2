package ru.kovcheg.kovapp.media;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/**
 * Запись фрагментированного MP4 — перенос класса Muxer из
 * web_app/js/media/mp4.js. Та же структура файла (ftyp + moov с mvex, затем
 * moof+mdat каждые ~2 с), те же флаги и порядок полей: сервер получает от
 * телефона ровно такой же файл, как от браузера.
 */
public final class FragmentedMp4Muxer {
    public interface Sink { void write(byte[] data) throws IOException; }

    static final class Sample {
        final byte[] data;
        long pts, dts, duration;
        final boolean key;
        Sample(byte[] data, long pts, long dts, long duration, boolean key) {
            this.data = data; this.pts = pts; this.dts = dts; this.duration = duration; this.key = key;
        }
    }

    public static final class TrackCfg {
        String kind;
        int id;
        long timescale;
        String codec;
        int width, height;
        byte[] description;
        byte[] entryRaw;
        final List<Sample> samples = new ArrayList<>();
        List<Sample> gop;
        long lastDuration;
    }

    private final Sink sink;
    private final double fragmentSeconds;
    private final List<TrackCfg> tracks = new ArrayList<>();
    private TrackCfg video, audio;
    private int seq = 0;
    private boolean started = false;

    public FragmentedMp4Muxer(Sink sink, double fragmentSeconds) {
        this.sink = sink;
        this.fragmentSeconds = fragmentSeconds > 0 ? fragmentSeconds : 2;
    }

    public boolean hasVideo() { return video != null; }
    public boolean hasAudio() { return audio != null; }

    public void addVideo(String codec, int width, int height, byte[] description) {
        TrackCfg t = new TrackCfg();
        t.kind = "video"; t.id = tracks.size() + 1; t.timescale = 90000;
        t.codec = codec; t.width = width; t.height = height; t.description = description;
        tracks.add(t);
        video = t;
    }

    public void addAudio(long timescale, byte[] entryRaw) {
        TrackCfg t = new TrackCfg();
        t.kind = "audio"; t.id = tracks.size() + 1; t.timescale = timescale; t.entryRaw = entryRaw;
        tracks.add(t);
        audio = t;
    }

    // ── кирпичики ─────────────────────────────────────────────────────────
    static byte[] concat(byte[]... parts) {
        int n = 0;
        for (byte[] p : parts) n += p.length;
        byte[] out = new byte[n];
        int o = 0;
        for (byte[] p : parts) { System.arraycopy(p, 0, out, o, p.length); o += p.length; }
        return out;
    }

    static byte[] concat(List<byte[]> parts) { return concat(parts.toArray(new byte[0][])); }

    static byte[] be32(long v) { return new byte[]{(byte) (v >>> 24), (byte) (v >>> 16), (byte) (v >>> 8), (byte) v}; }
    static byte[] be16(int v) { return new byte[]{(byte) (v >>> 8), (byte) v}; }
    static byte[] be64(long v) { return concat(be32(v >>> 32), be32(v & 0xFFFFFFFFL)); }
    static byte[] ascii(String s) { return s.getBytes(StandardCharsets.ISO_8859_1); }
    static byte[] zeros(int n) { return new byte[n]; }

    static byte[] box(String type, byte[]... parts) {
        byte[] body = concat(parts);
        return concat(be32(body.length + 8), ascii(type), body);
    }

    static byte[] full(String type, int version, int flags, byte[]... parts) {
        byte[] head = {(byte) version, (byte) (flags >> 16), (byte) (flags >> 8), (byte) flags};
        byte[][] all = new byte[parts.length + 1][];
        all[0] = head;
        System.arraycopy(parts, 0, all, 1, parts.length);
        return box(type, all);
    }

    static final byte[] MATRIX = concat(be32(0x10000), be32(0), be32(0), be32(0), be32(0x10000), be32(0), be32(0), be32(0), be32(0x40000000));

    static byte[] vpcC(String codec) {
        String[] p = codec.split("\\.");
        // как в JS: +p[1] || 0 — ноль или не число дают значение по умолчанию
        int profile = orDefault(p, 1, 0), level = orDefault(p, 2, 10), depth = orDefault(p, 3, 8);
        return full("vpcC", 1, 0, new byte[]{(byte) profile, (byte) level, (byte) ((depth << 4) | (1 << 1)), 1, 1, 1}, be16(0));
    }

    static int orDefault(String[] p, int i, int def) {
        if (i >= p.length) return def;
        try {
            int v = Integer.parseInt(p[i].trim());
            return v == 0 ? def : v;
        } catch (NumberFormatException e) {
            return def;
        }
    }

    static byte[] videoEntry(TrackCfg t) {
        byte[] cfg = new byte[0];
        String c = t.codec;
        if (c.startsWith("avc")) cfg = box("avcC", t.description);
        else if (c.startsWith("hvc") || c.startsWith("hev")) cfg = box("hvcC", t.description);
        else if (c.startsWith("vp09")) cfg = vpcC(c);
        else if (c.startsWith("av01")) cfg = box("av1C", t.description);
        String fourcc = c.startsWith("avc") ? "avc1" : c.startsWith("hev") ? "hev1" : c.startsWith("hvc") ? "hvc1"
                : c.startsWith("vp09") ? "vp09" : c.startsWith("av01") ? "av01" : "vp08";
        return box(fourcc, zeros(6), be16(1), zeros(16), be16(t.width), be16(t.height), be32(0x00480000), be32(0x00480000),
                be32(0), be16(1), zeros(32), be16(0x18), be16(0xffff), cfg);
    }

    static byte[] trak(TrackCfg t) {
        boolean v = t.kind.equals("video");
        byte[] tkhd = full("tkhd", 0, 3, be32(0), be32(0), be32(t.id), be32(0), be32(0), zeros(8), be16(0), be16(0),
                be16(v ? 0 : 0x0100), be16(0), MATRIX, be32(v ? ((long) t.width) << 16 : 0), be32(v ? ((long) t.height) << 16 : 0));
        byte[] mdhd = full("mdhd", 0, 0, be32(0), be32(0), be32(t.timescale), be32(0), be16(0x55c4), be16(0));
        byte[] hdlr = full("hdlr", 0, 0, be32(0), ascii(v ? "vide" : "soun"), zeros(12), ascii(v ? "VideoHandler" : "SoundHandler"), zeros(1));
        byte[] head = v ? full("vmhd", 0, 1, zeros(8)) : full("smhd", 0, 0, zeros(4));
        byte[] dinf = box("dinf", full("dref", 0, 0, be32(1), full("url ", 0, 1)));
        byte[] stsd = full("stsd", 0, 0, be32(1), v ? videoEntry(t) : t.entryRaw);
        byte[] stbl = box("stbl", stsd, full("stts", 0, 0, be32(0)), full("stsc", 0, 0, be32(0)),
                full("stsz", 0, 0, be32(0), be32(0)), full("stco", 0, 0, be32(0)));
        return box("trak", tkhd, box("mdia", mdhd, hdlr, box("minf", head, dinf, stbl)));
    }

    public void start() throws IOException {
        if (started) return;
        started = true;
        byte[] ftyp = box("ftyp", ascii("isom"), be32(0x200), ascii("isom"), ascii("iso6"), ascii("mp41"));
        byte[] mvhd = full("mvhd", 0, 0, be32(0), be32(0), be32(1000), be32(0), be32(0x10000), be16(0x0100), zeros(10), MATRIX,
                zeros(24), be32(tracks.size() + 1));
        List<byte[]> trex = new ArrayList<>();
        for (TrackCfg t : tracks) trex.add(full("trex", 0, 0, be32(t.id), be32(1), be32(0), be32(0), be32(0)));
        byte[] mvex = box("mvex", concat(trex));
        List<byte[]> moov = new ArrayList<>();
        moov.add(mvhd);
        for (TrackCfg t : tracks) moov.add(trak(t));
        moov.add(mvex);
        sink.write(concat(ftyp, box("moov", concat(moov))));
    }

    /** Кадр кодировщика: время показа в микросекундах от начала ролика */
    public void addVideoChunk(byte[] data, long ptsUs, boolean key) {
        TrackCfg t = video;
        long pts = Math.round(ptsUs * (double) t.timescale / 1e6);
        if (key && t.gop != null && !t.gop.isEmpty()) closeGop();
        if (t.gop == null) t.gop = new ArrayList<>();
        t.gop.add(new Sample(data, pts, 0, 0, key));
    }

    void closeGop() {
        TrackCfg t = video;
        List<Sample> gop = t.gop;
        if (gop == null || gop.isEmpty()) return;
        long[] sorted = new long[gop.size()];
        for (int i = 0; i < sorted.length; i++) sorted[i] = gop.get(i).pts;
        Arrays.sort(sorted);
        for (int i = 0; i < sorted.length; i++) gop.get(i).dts = sorted[i];
        t.samples.addAll(gop);
        t.gop = new ArrayList<>();
    }

    public void addAudioSample(byte[] data, long dts, long duration) {
        audio.samples.add(new Sample(data, dts, dts, duration, true));
    }

    double videoSeconds() {
        TrackCfg t = video;
        if (t.samples.isEmpty()) return 0;
        return (t.samples.get(t.samples.size() - 1).dts - t.samples.get(0).dts) / (double) t.timescale;
    }

    public void maybeFlush() throws IOException {
        if (video != null && videoSeconds() >= fragmentSeconds) flush(false);
    }

    static final class Traf {
        final TrackCfg t; final List<Sample> samples;
        Traf(TrackCfg t, List<Sample> samples) { this.t = t; this.samples = samples; }
    }

    void flush(boolean fin) throws IOException {
        start();
        if (fin && video != null) closeGop();
        List<Traf> trafs = new ArrayList<>();
        TrackCfg vt = video;
        double cut = Double.POSITIVE_INFINITY;
        if (vt != null && !vt.samples.isEmpty()) {
            int take = vt.samples.size();
            if (!fin) take = vt.samples.size() - 1;
            List<Sample> chunk = new ArrayList<>(vt.samples.subList(0, take));
            vt.samples.subList(0, take).clear();
            if (!chunk.isEmpty()) {
                Long next = vt.samples.isEmpty() ? null : vt.samples.get(0).dts;
                for (int i = 0; i < chunk.size(); i++) {
                    long nd = i + 1 < chunk.size() ? chunk.get(i + 1).dts
                            : (next != null ? next : chunk.get(i).dts + (vt.lastDuration != 0 ? vt.lastDuration : Math.round(vt.timescale / 30.0)));
                    chunk.get(i).duration = Math.max(1, nd - chunk.get(i).dts);
                    vt.lastDuration = chunk.get(i).duration;
                }
                Sample last = chunk.get(chunk.size() - 1);
                cut = (last.dts + last.duration) / (double) vt.timescale;
                trafs.add(new Traf(vt, chunk));
            }
        }
        TrackCfg at = audio;
        if (at != null && !at.samples.isEmpty()) {
            double limit = fin ? Double.POSITIVE_INFINITY : cut;
            int n = 0;
            while (n < at.samples.size() && (at.samples.get(n).dts / (double) at.timescale) < limit) n++;
            if (n > 0) {
                List<Sample> part = new ArrayList<>(at.samples.subList(0, n));
                at.samples.subList(0, n).clear();
                trafs.add(new Traf(at, part));
            }
        }
        if (trafs.isEmpty()) return;
        seq += 1;
        long[] zero = new long[trafs.size()];
        byte[] moof = buildMoof(trafs, zero);
        long[] offsets = new long[trafs.size()];
        long pos = moof.length + 8;
        for (int k = 0; k < trafs.size(); k++) {
            offsets[k] = pos;
            for (Sample s : trafs.get(k).samples) pos += s.data.length;
        }
        moof = buildMoof(trafs, offsets);
        ByteArrayOutputStream out = new ByteArrayOutputStream((int) Math.min(Integer.MAX_VALUE - 8, pos + 8));
        long mdatLen = 8;
        for (Traf f : trafs) for (Sample s : f.samples) mdatLen += s.data.length;
        out.write(moof);
        out.write(be32(mdatLen));
        out.write(ascii("mdat"));
        for (Traf f : trafs) for (Sample s : f.samples) out.write(s.data);
        sink.write(out.toByteArray());
    }

    private byte[] buildMoof(List<Traf> trafs, long[] dataOffsets) {
        List<byte[]> parts = new ArrayList<>();
        parts.add(full("mfhd", 0, 0, be32(seq)));
        for (int k = 0; k < trafs.size(); k++) {
            Traf f = trafs.get(k);
            boolean v = f.t.kind.equals("video");
            byte[] tfhd = full("tfhd", 0, 0x020000, be32(f.t.id));
            byte[] tfdt = full("tfdt", 1, 0, be64(f.samples.get(0).dts));
            int flags = 0x000001 | 0x000100 | 0x000200 | (v ? 0x000400 | 0x000800 : 0);
            List<byte[]> rows = new ArrayList<>();
            for (Sample s : f.samples) {
                if (v) rows.add(concat(be32(s.duration), be32(s.data.length), be32(s.key ? 0x02000000 : 0x01010000), be32((int) (s.pts - s.dts))));
                else rows.add(concat(be32(s.duration), be32(s.data.length)));
            }
            byte[] trun = full("trun", v ? 1 : 0, flags, be32(f.samples.size()), be32(dataOffsets[k]), concat(rows));
            parts.add(box("traf", tfhd, tfdt, trun));
        }
        return box("moof", concat(parts));
    }

    public void finish() throws IOException { flush(true); }
}
