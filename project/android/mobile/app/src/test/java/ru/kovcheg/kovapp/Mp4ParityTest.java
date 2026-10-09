package ru.kovcheg.kovapp;

import static org.junit.Assert.*;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.security.MessageDigest;
import java.util.List;
import java.util.Map;

import org.junit.Test;

import ru.kovcheg.kovapp.core.B64;
import ru.kovcheg.kovapp.core.Json;
import ru.kovcheg.kovapp.media.FragmentedMp4Muxer;
import ru.kovcheg.kovapp.media.Mp4Demuxer;
import ru.kovcheg.kovapp.media.Source;

/** Разбор и запись MP4 в Java совпадают с web_app/js/media/mp4.js. */
public class Mp4ParityTest {
    static Map<String, Object> reference() throws Exception {
        File fx = TestPaths.fixtures();
        return fx == null ? null : Json.parseObject(TestPaths.text(new File(fx, "reference.json")));
    }

    static String hex(byte[] b) { return b == null ? null : B64.hex(b); }

    static String sha(String s) throws Exception {
        return B64.hex(MessageDigest.getInstance("SHA-256").digest(s.getBytes("UTF-8")));
    }

    @SuppressWarnings("unchecked")
    @Test public void demuxMatchesWeb() throws Exception {
        Map<String, Object> ref = reference();
        if (ref == null) return;
        Map<String, Object> demux = Json.obj(ref, "demux");
        int checked = 0;
        for (Map.Entry<String, Object> e : demux.entrySet()) {
            String name = e.getKey();
            Map<String, Object> want = (Map<String, Object>) e.getValue();
            Mp4Demuxer.Info info = null;
            String error = null;
            try (Source src = Source.of(new File(TestPaths.fixtures(), name))) {
                info = Mp4Demuxer.demux(src);
            } catch (java.io.IOException ex) {
                error = ex.getMessage();
            }
            if (!Json.bool(want, "ok", false)) {
                assertEquals(name + ": та же ошибка", Json.str(want, "error", null), error);
                checked++;
                continue;
            }
            assertNull(name + ": без ошибки", error);
            assertEquals(name + ": длительность", Json.dbl(want, "duration", -1), info.duration, 1e-9);
            assertEquals(name + ": звуковых дорожек", Json.num(want, "audioTracks", -1), info.audioTracks);
            assertEquals(name + ": верхние блоки", String.valueOf(want.get("top")), String.valueOf(info.top));
            List<Object> tracks = Json.arr(want, "tracks");
            assertEquals(name + ": дорожек", tracks.size(), info.tracks.size());
            for (int i = 0; i < tracks.size(); i++) {
                Map<String, Object> wt = (Map<String, Object>) tracks.get(i);
                Mp4Demuxer.Track t = info.tracks.get(i);
                String p = name + "#" + i + ": ";
                assertEquals(p + "id", Json.num(wt, "id", -1), t.id);
                assertEquals(p + "handler", Json.str(wt, "handler", null), t.handler);
                assertEquals(p + "timescale", Json.num(wt, "timescale", -1), t.timescale);
                assertEquals(p + "duration", Json.dbl(wt, "duration", -1), t.duration, 1e-9);
                assertEquals(p + "rotation", Json.num(wt, "rotation", -1), t.rotation);
                assertEquals(p + "flip", Json.bool(wt, "flip", false), t.flip);
                assertEquals(p + "shift", Json.num(wt, "shift", -1), t.shift);
                assertEquals(p + "count", Json.num(wt, "count", -1), t.count);
                StringBuilder rows = new StringBuilder();
                for (int s = 0; s < t.count; s++) {
                    rows.append(t.offset[s]).append(',').append(t.size[s]).append(',').append(t.dts[s]).append(',')
                        .append(t.pts[s]).append(',').append(t.key[s] ? 1 : 0).append(';');
                }
                assertEquals(p + "таблица сэмплов", Json.str(wt, "samplesSha", null), sha(rows.toString()));
                Map<String, Object> we = Json.obj(wt, "entry");
                Mp4Demuxer.Entry en = t.entry;
                assertEquals(p + "fourcc", Json.str(we, "fourcc", null), en.fourcc);
                assertEquals(p + "codec", Json.str(we, "codec", null), en.codec);
                assertEquals(p + "family", Json.str(we, "family", null), en.family);
                assertEquals(p + "width", Json.num(we, "width", -1), en.width);
                assertEquals(p + "height", Json.num(we, "height", -1), en.height);
                assertEquals(p + "channels", Json.num(we, "channels", -1), en.channels);
                assertEquals(p + "sampleRate", Json.num(we, "sampleRate", -1), en.sampleRate);
                assertEquals(p + "raw", Json.str(we, "raw", null), hex(en.raw));
                assertEquals(p + "description", Json.str(we, "description", null), hex(en.description));
                assertEquals(p + "asc", Json.str(we, "asc", null), hex(en.asc));
                assertEquals(p + "vpcC", Json.str(we, "vpcC", null), hex(en.vpcC));
            }
            Object vid = want.get("video");
            assertEquals(name + ": видеодорожка", vid, info.video == null ? null : info.video.id);
            Object aud = want.get("audio");
            assertEquals(name + ": звуковая дорожка", aud, info.audio == null ? null : info.audio.id);
            checked++;
        }
        assertEquals("сверены все образцы", demux.size(), checked);
    }

    @SuppressWarnings("unchecked")
    @Test public void muxerByteExactWithWeb() throws Exception {
        Map<String, Object> ref = reference();
        if (ref == null) return;
        Map<String, Object> mux = Json.obj(ref, "mux");
        final ByteArrayOutputStream out = new ByteArrayOutputStream();
        final java.util.List<Integer> sizes = new java.util.ArrayList<>();
        FragmentedMp4Muxer m = new FragmentedMp4Muxer(data -> { sizes.add(data.length); out.write(data); }, 2);
        m.addVideo("avc1.64001f", 640, 360, B64.decode(hexToB64(Json.str(mux, "description", ""))));
        m.addAudio(48000, B64.decode(hexToB64(Json.str(mux, "entryRaw", ""))));
        for (Object o : Json.arr(mux, "ops")) {
            List<Object> op = (List<Object>) o;
            String kind = (String) op.get(0);
            if (kind.equals("v")) m.addVideoChunk(unhex((String) op.get(1)), ((Number) op.get(2)).longValue(), (Boolean) op.get(3));
            else if (kind.equals("a")) m.addAudioSample(unhex((String) op.get(1)), ((Number) op.get(2)).longValue(), ((Number) op.get(3)).longValue());
            else m.maybeFlush();
        }
        m.finish();
        assertEquals("размеры частей", String.valueOf(Json.arr(mux, "chunks")), String.valueOf(sizes));
        byte[] js = TestPaths.read(new File(TestPaths.fixtures(), "mux_js.mp4"));
        assertArrayEquals("файл побайтно совпадает с веб-версией", js, out.toByteArray());
    }

    static byte[] unhex(String h) {
        byte[] b = new byte[h.length() / 2];
        for (int i = 0; i < b.length; i++) b[i] = (byte) Integer.parseInt(h.substring(i * 2, i * 2 + 2), 16);
        return b;
    }

    static String hexToB64(String h) { return B64.encode(unhex(h)); }
}
