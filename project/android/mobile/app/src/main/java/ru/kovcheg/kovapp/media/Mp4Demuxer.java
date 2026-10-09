package ru.kovcheg.kovapp.media;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/**
 * Разбор MP4/MOV без чтения файла целиком — перенос web_app/js/media/mp4.js
 * (demux, parseTrack, sampleEntry). Поля и формулы те же, чтобы решение
 * «сжимать ли на устройстве» совпадало с веб-версией.
 */
public final class Mp4Demuxer {
    private Mp4Demuxer() {}

    public static final class Entry {
        public String fourcc;
        public byte[] raw;
        public int width, height;
        public byte[] description;
        public String codec;
        public String family;
        public byte[] vpcC;
        public int channels, sampleRate;
        public byte[] asc;
    }

    public static final class Track {
        public long id;
        public String handler;
        public long timescale;
        public double duration;
        public int rotation;
        public boolean flip;
        public Entry entry;
        public long shift;
        public int count;
        public long[] offset, dts, pts;
        public int[] size;
        public boolean[] key;

        public long bytes() { long n = 0; for (int i = 0; i < count; i++) n += size[i]; return n; }
    }

    public static final class Info {
        public List<Track> tracks = new ArrayList<>();
        public Track video;
        public Track audio;
        public int audioTracks;
        public double duration;
        public List<String> top = new ArrayList<>();
    }

    // ── чтение чисел ─────────────────────────────────────────────────────
    static long u32(byte[] b, int o) {
        return ((long) (b[o] & 255) << 24) | ((b[o + 1] & 255) << 16) | ((b[o + 2] & 255) << 8) | (b[o + 3] & 255);
    }
    static int u16(byte[] b, int o) { return ((b[o] & 255) << 8) | (b[o + 1] & 255); }
    static int i32(byte[] b, int o) { return (int) u32(b, o); }
    static long u64(byte[] b, int o) { return u32(b, o) * 4294967296L + u32(b, o + 4); }
    static long i64(byte[] b, int o) { return (u32(b, o) << 32) | u32(b, o + 4); }
    static String type(byte[] b, int o) { return new String(b, o, 4, StandardCharsets.ISO_8859_1); }

    static final class Box {
        final String type;
        final int start, body, end;
        Box(String type, int start, int body, int end) { this.type = type; this.start = start; this.body = body; this.end = end; }
    }

    static List<Box> children(byte[] b, int start, int end) {
        List<Box> out = new ArrayList<>();
        int o = start;
        while (o + 8 <= end) {
            long size = u32(b, o);
            int head = 8;
            String t = type(b, o + 4);
            if (size == 1) {
                if (o + 16 > end) break;
                size = u64(b, o + 8);
                head = 16;
            } else if (size == 0) size = end - o;
            if (size < head || o + size > end) break;
            out.add(new Box(t, o, o + head, (int) (o + size)));
            o += (int) size;
        }
        return out;
    }

    static Box find(byte[] b, Box box, String t) {
        for (Box c : children(b, box.body, box.end)) if (c.type.equals(t)) return c;
        return null;
    }

    static Box path(byte[] b, Box box, String... types) {
        Box cur = box;
        for (String t : types) { if (cur == null) return null; cur = find(b, cur, t); }
        return cur;
    }

    /** Длина дескриптора MPEG-4: {длина, сколько байт заняла} */
    static int[] descriptorLen(byte[] b, int o) {
        int len = 0, n = 0;
        for (; n < 4; n++) {
            int c = b[o + n] & 255;
            len = (len << 7) | (c & 0x7f);
            if ((c & 0x80) == 0) { n++; break; }
        }
        return new int[]{len, n};
    }

    static String hex2(int n) { return String.format(Locale.ROOT, "%02x", n & 255); }
    static String pad2(int n) { return String.format(Locale.ROOT, "%02d", n); }

    /** Разбор esds: кодек аудио в записи WebCodecs (mp4a.40.2, mp3) */
    static Object[] esdsInfo(byte[] b, Box box) {
        int[] o = {box.body + 4};
        int[] d = readDesc(b, o[0]);
        if (d[0] != 3) return null;
        int p = d[2] + 3;
        int flags = b[d[2] + 2] & 255;
        if ((flags & 0x80) != 0) p += 2;
        if ((flags & 0x40) != 0) p += 1 + (b[p] & 255);
        if ((flags & 0x20) != 0) p += 2;
        d = readDesc(b, p);
        if (d[0] != 4) return null;
        int oti = b[d[2]] & 255;
        p = d[2] + 13;
        d = readDesc(b, p);
        byte[] asc = d[0] == 5 ? Arrays.copyOfRange(b, d[2], Math.min(b.length, d[2] + d[1])) : null;
        int aot = asc != null && asc.length > 0 ? (asc[0] & 255) >> 3 : 2;
        if (aot == 31 && asc != null && asc.length > 1) aot = 32 + ((((asc[0] & 255) & 7) << 3) | ((asc[1] & 255) >> 5));
        String codec = (oti == 0x6B || oti == 0x69) ? "mp3" : "mp4a.40." + aot;
        return new Object[]{oti, asc, codec};
    }

    /** {tag, len, at} */
    private static int[] readDesc(byte[] b, int o) {
        int tag = b[o] & 255;
        int[] ln = descriptorLen(b, o + 1);
        return new int[]{tag, ln[0], o + 1 + ln[1]};
    }

    static String hevcCodec(byte[] c, String fourcc) {
        String[] spaces = {"", "A", "B", "C"};
        String profileSpace = spaces[(c[1] & 255) >> 6];
        String tier = (((c[1] & 255) >> 5) & 1) != 0 ? "H" : "L";
        int profile = c[1] & 0x1f;
        long compat = u32(c, 2), rev = 0;
        for (int i = 0; i < 32; i++) { rev = (rev << 1) | (compat & 1); compat >>>= 1; }
        rev &= 0xFFFFFFFFL;
        int level = c[12] & 255;
        List<Integer> cons = new ArrayList<>();
        for (int i = 6; i <= 11; i++) cons.add(c[i] & 255);
        while (!cons.isEmpty() && cons.get(cons.size() - 1) == 0) cons.remove(cons.size() - 1);
        StringBuilder sb = new StringBuilder();
        sb.append(fourcc).append('.').append(profileSpace).append(profile).append('.')
          .append(Long.toHexString(rev).toUpperCase(Locale.ROOT)).append('.').append(tier).append(level);
        for (int x : cons) sb.append('.').append(Integer.toHexString(x).toUpperCase(Locale.ROOT));
        return sb.toString();
    }

    static Entry sampleEntry(byte[] b, Box stsd, String handler) {
        List<Box> list = children(b, stsd.body + 8, stsd.end);
        if (list.isEmpty()) return null;
        Box entry = list.get(0);
        Entry out = new Entry();
        out.fourcc = entry.type;
        out.raw = Arrays.copyOfRange(b, entry.start, entry.end);
        if ("vide".equals(handler)) {
            out.width = u16(b, entry.body + 24);
            out.height = u16(b, entry.body + 26);
            List<Box> kids = entry.body + 78 <= entry.end ? children(b, entry.body + 78, entry.end) : new ArrayList<Box>();
            Box avcC = null, hvcC = null, vpcC = null, av1C = null;
            for (Box k : kids) {
                if (avcC == null && k.type.equals("avcC")) avcC = k;
                if (hvcC == null && k.type.equals("hvcC")) hvcC = k;
                if (vpcC == null && k.type.equals("vpcC")) vpcC = k;
                if (av1C == null && k.type.equals("av1C")) av1C = k;
            }
            if (avcC != null) {
                out.description = Arrays.copyOfRange(b, avcC.body, avcC.end);
                out.codec = entry.type + "." + hex2(b[avcC.body + 1]) + hex2(b[avcC.body + 2]) + hex2(b[avcC.body + 3]);
                out.family = "avc";
            } else if (hvcC != null) {
                out.description = Arrays.copyOfRange(b, hvcC.body, hvcC.end);
                out.codec = hevcCodec(out.description, entry.type);
                out.family = "hevc";
            } else if (vpcC != null) {
                int v = vpcC.body + 4;
                out.codec = "vp09." + pad2(b[v] & 255) + "." + pad2(b[v + 1] & 255) + "." + pad2((b[v + 2] & 255) >> 4);
                out.family = "vp9";
                out.vpcC = Arrays.copyOfRange(b, vpcC.body, vpcC.end);
            } else if (av1C != null) {
                out.description = Arrays.copyOfRange(b, av1C.body, av1C.end);
                byte[] s = out.description;
                int profile = (s[1] & 255) >> 5, level = s[1] & 31;
                String tier = ((s[2] & 255) >> 7) != 0 ? "H" : "M";
                int hbd = ((s[2] & 255) >> 6) & 1, twelve = ((s[2] & 255) >> 5) & 1;
                out.codec = "av01." + profile + "." + pad2(level) + tier + "." + (hbd != 0 ? (twelve != 0 ? "12" : "10") : "08");
                out.family = "av1";
            } else if (entry.type.equals("vp08")) {
                out.codec = "vp8";
                out.family = "vp8";
            } else {
                out.family = "unknown";
            }
        } else if ("soun".equals(handler)) {
            int version = u16(b, entry.body + 8);
            out.channels = u16(b, entry.body + 16);
            out.sampleRate = (int) (u32(b, entry.body + 24) >>> 16);
            int skip = version == 1 ? 16 : version == 2 ? 36 : 0;
            int from = entry.body + 28 + skip;
            List<Box> kids = from <= entry.end ? children(b, from, entry.end) : new ArrayList<Box>();
            Box esds = null;
            for (Box k : kids) if (k.type.equals("esds")) { esds = k; break; }
            if (entry.type.equals("mp4a") && esds != null) {
                Object[] e = esdsInfo(b, esds);
                if (e != null) { out.codec = (String) e[2]; out.asc = (byte[]) e[1]; }
            } else if (entry.type.equals("Opus")) out.codec = "opus";
            else out.codec = entry.type;
        }
        return out;
    }

    static Track parseTrack(byte[] b, Box trak) {
        Box tkhd = find(b, trak, "tkhd");
        Box mdhd = path(b, trak, "mdia", "mdhd");
        Box hdlr = path(b, trak, "mdia", "hdlr");
        Box stbl = path(b, trak, "mdia", "minf", "stbl");
        if (tkhd == null || mdhd == null || hdlr == null || stbl == null) return null;
        Track t = new Track();
        t.handler = type(b, hdlr.body + 8);
        int tv = b[tkhd.body] & 255;
        t.id = u32(b, tkhd.body + (tv != 0 ? 20 : 12));
        int matrixAt = tkhd.body + (tv != 0 ? 52 : 40);
        int ma = i32(b, matrixAt), mb = i32(b, matrixAt + 4), mc = i32(b, matrixAt + 12), md = i32(b, matrixAt + 16);
        long rot = Math.round(Math.atan2(mb, ma) * 180 / Math.PI);
        t.rotation = (int) (((rot % 360) + 360) % 360);
        t.flip = (double) ma * md - (double) mb * mc < 0;
        int mv = b[mdhd.body] & 255;
        t.timescale = u32(b, mdhd.body + (mv != 0 ? 20 : 12));
        double mediaDuration = mv != 0 ? (double) u64(b, mdhd.body + 24) : (double) u32(b, mdhd.body + 16);
        t.duration = t.timescale > 0 ? mediaDuration / t.timescale : 0;
        Box stsd = find(b, stbl, "stsd");
        t.entry = stsd != null ? sampleEntry(b, stsd, t.handler) : null;
        Box stts = find(b, stbl, "stts"), ctts = find(b, stbl, "ctts"), stss = find(b, stbl, "stss"), stsc = find(b, stbl, "stsc");
        Box stsz = find(b, stbl, "stsz"), stz2 = find(b, stbl, "stz2"), stco = find(b, stbl, "stco"), co64 = find(b, stbl, "co64");

        int count;
        int[] sizes;
        if (stsz != null) {
            long fixed = u32(b, stsz.body + 4);
            count = (int) u32(b, stsz.body + 8);
            sizes = new int[count];
            for (int i = 0; i < count; i++) sizes[i] = (int) (fixed != 0 ? fixed : u32(b, stsz.body + 12 + i * 4));
        } else if (stz2 != null) {
            int f = b[stz2.body + 7] & 255;
            count = (int) u32(b, stz2.body + 8);
            sizes = new int[count];
            for (int i = 0; i < count; i++) {
                if (f == 16) sizes[i] = u16(b, stz2.body + 12 + i * 2);
                else if (f == 8) sizes[i] = b[stz2.body + 12 + i] & 255;
                else sizes[i] = ((b[stz2.body + 12 + (i >> 1)] & 255) >> ((i & 1) != 0 ? 0 : 4)) & 15;
            }
        } else {
            count = 0;
            sizes = new int[0];
        }
        long[] chunks;
        if (stco != null) {
            int n = (int) u32(b, stco.body + 4);
            chunks = new long[n];
            for (int i = 0; i < n; i++) chunks[i] = u32(b, stco.body + 8 + i * 4);
        } else if (co64 != null) {
            int n = (int) u32(b, co64.body + 4);
            chunks = new long[n];
            for (int i = 0; i < n; i++) chunks[i] = u64(b, co64.body + 8 + i * 8);
        } else chunks = new long[0];

        long[] offsets = new long[count];
        if (stsc != null) {
            int n = (int) u32(b, stsc.body + 4);
            long[][] runs = new long[n][2];
            for (int i = 0; i < n; i++) { runs[i][0] = u32(b, stsc.body + 8 + i * 12); runs[i][1] = u32(b, stsc.body + 12 + i * 12); }
            int s = 0;
            for (int r = 0; r < runs.length && s < count; r++) {
                long last = r + 1 < runs.length ? runs[r + 1][0] - 1 : chunks.length;
                for (long c = runs[r][0]; c <= last && s < count; c++) {
                    if (c - 1 < 0 || c - 1 >= chunks.length) break;
                    long off = chunks[(int) (c - 1)];
                    for (long k = 0; k < runs[r][1] && s < count; k++) { offsets[s] = off; off += sizes[s]; s++; }
                }
            }
        }
        long[] dts = new long[count], cts = new long[count];
        if (stts != null) {
            int n = (int) u32(b, stts.body + 4);
            int s = 0;
            long tt = 0;
            for (int i = 0; i < n; i++) {
                long c = u32(b, stts.body + 8 + i * 8), d = u32(b, stts.body + 12 + i * 8);
                for (long k = 0; k < c && s < count; k++) { dts[s++] = tt; tt += d; }
            }
            for (; s < count; s++) dts[s] = tt;
        }
        if (ctts != null) {
            int v = b[ctts.body] & 255;
            int n = (int) u32(b, ctts.body + 4);
            int s = 0;
            for (int i = 0; i < n; i++) {
                long c = u32(b, ctts.body + 8 + i * 8);
                long off = v != 0 ? i32(b, ctts.body + 12 + i * 8) : u32(b, ctts.body + 12 + i * 8);
                for (long k = 0; k < c && s < count; k++) cts[s++] = off;
            }
        }
        Set<Integer> keys = null;
        if (stss != null) {
            keys = new HashSet<>();
            int n = (int) u32(b, stss.body + 4);
            for (int i = 0; i < n; i++) keys.add((int) u32(b, stss.body + 8 + i * 4) - 1);
        }
        long shift = 0;
        Box elst = path(b, trak, "edts", "elst");
        if (elst != null) {
            int v = b[elst.body] & 255;
            int n = (int) u32(b, elst.body + 4);
            for (int i = 0; i < n; i++) {
                int at = elst.body + 8 + i * (v != 0 ? 20 : 12);
                // для версии 1 время читается со знаком: -1 означает пустую правку
                long mediaTime = v != 0 ? i64(b, at + 8) : i32(b, at + 4);
                if (mediaTime >= 0 && mediaTime != 4294967295L) { shift = mediaTime; break; }
            }
        }
        t.count = count;
        t.size = sizes;
        t.offset = offsets;
        t.dts = dts;
        t.pts = new long[count];
        t.key = new boolean[count];
        for (int i = 0; i < count; i++) {
            t.pts[i] = dts[i] + cts[i] - shift;
            t.key[i] = keys == null || keys.contains(i);
        }
        t.shift = shift;
        return t;
    }

    public static Info demux(Source src) throws IOException {
        long offset = 0;
        byte[] moov = null;
        Info info = new Info();
        while (offset + 8 <= src.size()) {
            byte[] head = src.readAt(offset, 16);
            long size = u32(head, 0);
            String t = type(head, 4);
            if (size == 1) size = head.length >= 16 ? u64(head, 8) : 0;
            else if (size == 0) size = src.size() - offset;
            if (size < 8) break;
            info.top.add(t);
            if (t.equals("moov")) {
                if (size > 256L * 1024 * 1024) throw new IOException("moov слишком большой");
                moov = src.readAt(offset, (int) size);
            }
            if (t.equals("moof")) throw new IOException("фрагментированный MP4 не поддерживается");
            offset += size;
        }
        if (moov == null) throw new IOException("в файле нет описания дорожек (moov)");
        Box root = new Box("moov", 0, 8, moov.length);
        List<Track> audio = new ArrayList<>();
        for (Box c : children(moov, root.body, root.end)) {
            if (!c.type.equals("trak")) continue;
            Track tr = parseTrack(moov, c);
            if (tr == null) continue;
            info.tracks.add(tr);
            if (info.video == null && "vide".equals(tr.handler) && tr.count > 0) info.video = tr;
            if ("soun".equals(tr.handler) && tr.count > 0) audio.add(tr);
        }
        info.audio = audio.isEmpty() ? null : audio.get(0);
        info.audioTracks = audio.size();
        Box mvhd = find(moov, root, "mvhd");
        if (mvhd != null) {
            int mv = moov[mvhd.body] & 255;
            double d = mv != 0 ? (double) u64(moov, mvhd.body + 24) : (double) u32(moov, mvhd.body + 16);
            long ts = u32(moov, mvhd.body + (mv != 0 ? 20 : 12));
            info.duration = ts > 0 ? d / ts : 0;
        } else info.duration = info.video != null ? info.video.duration : 0;
        return info;
    }

    public static byte[] readSample(Source src, Track t, int i) throws IOException {
        return src.readAt(t.offset[i], t.size[i]);
    }
}
