package ru.kovcheg.kovapp.media;

import java.io.ByteArrayOutputStream;
import java.util.ArrayList;
import java.util.List;

/**
 * Преобразования потока H.264/HEVC между видом MP4 (длина перед каждым NAL)
 * и видом Annex-B (стартовые коды 00 00 00 01), который ждёт и выдаёт
 * MediaCodec; сборка avcC из SPS/PPS кодировщика.
 */
public final class NalUnits {
    private NalUnits() {}

    static final byte[] START = {0, 0, 0, 1};

    /** Кадр из MP4 (длина nalLen байт + NAL) → Annex-B */
    public static byte[] toAnnexB(byte[] sample, int nalLen) {
        ByteArrayOutputStream out = new ByteArrayOutputStream(sample.length + 16);
        int o = 0;
        while (o + nalLen <= sample.length) {
            long n = 0;
            for (int i = 0; i < nalLen; i++) n = (n << 8) | (sample[o + i] & 255);
            o += nalLen;
            if (n <= 0 || o + n > sample.length) break;
            out.write(START, 0, 4);
            out.write(sample, o, (int) n);
            o += (int) n;
        }
        return out.toByteArray();
    }

    /** Разбить Annex-B на NAL-блоки (без стартовых кодов) */
    public static List<byte[]> splitAnnexB(byte[] data, int off, int len) {
        List<byte[]> out = new ArrayList<>();
        int end = off + len;
        int i = off, start = -1;
        while (i + 2 < end) {
            if (data[i] == 0 && data[i + 1] == 0 && (data[i + 2] == 1 || (i + 3 < end && data[i + 2] == 0 && data[i + 3] == 1))) {
                int codeLen = data[i + 2] == 1 ? 3 : 4;
                if (start >= 0) out.add(trimmed(data, start, i));
                start = i + codeLen;
                i += codeLen;
            } else i++;
        }
        if (start >= 0 && start < end) out.add(trimmed(data, start, end));
        else if (start < 0 && len > 0) out.add(trimmed(data, off, end));
        return out;
    }

    private static byte[] trimmed(byte[] d, int from, int to) {
        // хвостовые нули перед следующим стартовым кодом не входят в NAL
        while (to > from && d[to - 1] == 0) to--;
        byte[] b = new byte[to - from];
        System.arraycopy(d, from, b, 0, b.length);
        return b;
    }

    /** Annex-B от кодировщика → кадр для MP4 с 4-байтной длиной; SPS/PPS/AUD выбрасываются */
    public static byte[] annexBToAvcc(byte[] data, int off, int len) {
        ByteArrayOutputStream out = new ByteArrayOutputStream(len + 16);
        for (byte[] nal : splitAnnexB(data, off, len)) {
            if (nal.length == 0) continue;
            int type = nal[0] & 0x1f;
            if (type == 7 || type == 8 || type == 9) continue;
            out.write((nal.length >>> 24) & 255);
            out.write((nal.length >>> 16) & 255);
            out.write((nal.length >>> 8) & 255);
            out.write(nal.length & 255);
            out.write(nal, 0, nal.length);
        }
        return out.toByteArray();
    }

    /** Конфигурация декодера (csd-0, csd-1) и длина поля NAL из avcC */
    public static final class Csd {
        public byte[] csd0, csd1;
        public int nalLength;
    }

    public static Csd fromAvcC(byte[] c) {
        Csd r = new Csd();
        r.nalLength = (c[4] & 3) + 1;
        ByteArrayOutputStream sps = new ByteArrayOutputStream(), pps = new ByteArrayOutputStream();
        int o = 5;
        int ns = c[o++] & 0x1f;
        for (int i = 0; i < ns; i++) {
            int n = ((c[o] & 255) << 8) | (c[o + 1] & 255);
            o += 2;
            sps.write(START, 0, 4);
            sps.write(c, o, n);
            o += n;
        }
        int np = c[o++] & 255;
        for (int i = 0; i < np; i++) {
            int n = ((c[o] & 255) << 8) | (c[o + 1] & 255);
            o += 2;
            pps.write(START, 0, 4);
            pps.write(c, o, n);
            o += n;
        }
        r.csd0 = sps.toByteArray();
        r.csd1 = pps.toByteArray();
        return r;
    }

    public static Csd fromHvcC(byte[] c) {
        Csd r = new Csd();
        r.nalLength = (c[21] & 3) + 1;
        ByteArrayOutputStream all = new ByteArrayOutputStream();
        int o = 22;
        int arrays = c[o++] & 255;
        for (int a = 0; a < arrays; a++) {
            o++; // тип NAL массива
            int count = ((c[o] & 255) << 8) | (c[o + 1] & 255);
            o += 2;
            for (int i = 0; i < count; i++) {
                int n = ((c[o] & 255) << 8) | (c[o + 1] & 255);
                o += 2;
                all.write(START, 0, 4);
                all.write(c, o, n);
                o += n;
            }
        }
        r.csd0 = all.toByteArray();
        return r;
    }

    // ── SPS ──────────────────────────────────────────────────────────────
    /** Байты RBSP: без защитных 0x03 после двух нулей */
    static byte[] rbsp(byte[] nal) {
        ByteArrayOutputStream out = new ByteArrayOutputStream(nal.length);
        int zeros = 0;
        for (int i = 0; i < nal.length; i++) {
            int b = nal[i] & 255;
            if (zeros >= 2 && b == 3) { zeros = 0; continue; }
            out.write(b);
            zeros = b == 0 ? zeros + 1 : 0;
        }
        return out.toByteArray();
    }

    static final class Bits {
        final byte[] d;
        int pos;
        Bits(byte[] d, int bytePos) { this.d = d; this.pos = bytePos * 8; }
        int bit() {
            if (pos >= d.length * 8) throw new IllegalArgumentException("SPS короче ожидаемого");
            int v = (d[pos >> 3] >> (7 - (pos & 7))) & 1;
            pos++;
            return v;
        }
        int ue() {
            int zeros = 0;
            while (bit() == 0) { zeros++; if (zeros > 31) throw new IllegalArgumentException("неверный код Голомба"); }
            long v = 1;
            for (int i = 0; i < zeros; i++) v = (v << 1) | bit();
            return (int) (v - 1);
        }
    }

    public static final class Sps {
        public int profile, compat, level, chromaFormat = 1, bitDepthLuma = 8, bitDepthChroma = 8;
    }

    /** Разбор начала SPS (NAL вместе с заголовком): профиль, цветность, разрядность */
    public static Sps parseSps(byte[] nal) {
        byte[] r = rbsp(nal);
        Sps s = new Sps();
        s.profile = r[1] & 255;
        s.compat = r[2] & 255;
        s.level = r[3] & 255;
        Bits b = new Bits(r, 4);
        b.ue(); // seq_parameter_set_id
        int p = s.profile;
        if (p == 100 || p == 110 || p == 122 || p == 244 || p == 44 || p == 83 || p == 86 || p == 118 || p == 128
                || p == 138 || p == 139 || p == 134 || p == 135) {
            s.chromaFormat = b.ue();
            if (s.chromaFormat == 3) b.bit();
            s.bitDepthLuma = b.ue() + 8;
            s.bitDepthChroma = b.ue() + 8;
        }
        return s;
    }

    /** avcC из SPS и PPS — тем же способом, что ffmpeg (ff_isom_write_avcc) */
    public static byte[] buildAvcC(List<byte[]> spsList, List<byte[]> ppsList) {
        if (spsList.isEmpty() || ppsList.isEmpty()) throw new IllegalArgumentException("нет SPS или PPS");
        byte[] sps = spsList.get(0);
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        out.write(1);
        out.write(sps[1] & 255);
        out.write(sps[2] & 255);
        out.write(sps[3] & 255);
        out.write(0xff);
        out.write(0xe0 | spsList.size());
        for (byte[] s : spsList) { out.write((s.length >> 8) & 255); out.write(s.length & 255); out.write(s, 0, s.length); }
        out.write(ppsList.size());
        for (byte[] p : ppsList) { out.write((p.length >> 8) & 255); out.write(p.length & 255); out.write(p, 0, p.length); }
        int profile = sps[1] & 255;
        if (profile != 66 && profile != 77 && profile != 88) {
            Sps s = parseSps(sps);
            out.write(0xfc | s.chromaFormat);
            out.write(0xf8 | (s.bitDepthLuma - 8));
            out.write(0xf8 | (s.bitDepthChroma - 8));
            out.write(0);
        }
        return out.toByteArray();
    }

    /** avcC из буфера CODEC_CONFIG кодировщика (Annex-B с SPS и PPS) */
    public static byte[] avcCFromCodecConfig(byte[] data, int off, int len) {
        List<byte[]> sps = new ArrayList<>(), pps = new ArrayList<>();
        for (byte[] nal : splitAnnexB(data, off, len)) {
            if (nal.length == 0) continue;
            int type = nal[0] & 0x1f;
            if (type == 7) sps.add(nal);
            else if (type == 8) pps.add(nal);
        }
        return buildAvcC(sps, pps);
    }

    /** Строка кодека для mp4 (avc1.PPCCLL) из avcC */
    public static String avcCodecString(byte[] avcC) {
        return String.format(java.util.Locale.ROOT, "avc1.%02x%02x%02x", avcC[1] & 255, avcC[2] & 255, avcC[3] & 255);
    }
}
