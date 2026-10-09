package ru.kovcheg.kovapp;

import static org.junit.Assert.*;

import java.io.File;
import java.util.Arrays;
import java.util.List;
import java.util.Map;

import org.junit.Test;

import ru.kovcheg.kovapp.core.Json;
import ru.kovcheg.kovapp.media.FrameMapping;
import ru.kovcheg.kovapp.media.Mp4Demuxer;
import ru.kovcheg.kovapp.media.Source;
import ru.kovcheg.kovapp.media.VideoPlanner;

/** Решения о сжатии видео совпадают с video-prep.js. */
public class VideoPlannerTest {
    static VideoPlanner.Edits edits(String json) { return VideoPlanner.Edits.from(Json.parseObject(json)); }

    static String pair(int[] wh) { return "[" + wh[0] + ", " + wh[1] + "]"; }

    static String pairOf(Object o) {
        List<?> l = (List<?>) o;
        return "[" + ((Number) l.get(0)).longValue() + ", " + ((Number) l.get(1)).longValue() + "]";
    }

    @SuppressWarnings("unchecked")
    @Test public void analysisMatchesWeb() throws Exception {
        Map<String, Object> ref = Mp4ParityTest.reference();
        if (ref == null) return;
        int n = 0;
        for (Map.Entry<String, Object> e : Json.obj(ref, "demux").entrySet()) {
            Map<String, Object> want = Json.obj((Map<String, Object>) e.getValue(), "analysis");
            if (want == null) continue;
            Mp4Demuxer.Info info;
            try (Source src = Source.of(new File(TestPaths.fixtures(), e.getKey()))) { info = Mp4Demuxer.demux(src); }
            VideoPlanner.Analysis a = VideoPlanner.analyze(info);
            String p = e.getKey() + ": ";
            assertEquals(p + "width", Json.num(want, "width", -1), a.width);
            assertEquals(p + "height", Json.num(want, "height", -1), a.height);
            assertEquals(p + "fps", Json.dbl(want, "fps", -1), a.fps, 1e-9);
            assertEquals(p + "duration", Json.dbl(want, "duration", -1), a.duration, 1e-9);
            assertEquals(p + "bitrate", Json.dbl(want, "bitrate", -1), a.bitrate, 1e-6);
            assertEquals(p + "frames", Json.num(want, "frames", -1), a.frames);
            assertEquals(p + "ready", want.get("ready"), VideoPlanner.alreadyReady(a, null));
            assertEquals(p + "readyTrim", want.get("readyTrim"), VideoPlanner.alreadyReady(a, edits("{\"trim_start\":0.5}")));
            assertEquals(p + "readyRotate", want.get("readyRotate"), VideoPlanner.alreadyReady(a, edits("{\"rotate\":90}")));
            assertEquals(p + "readyCrop", want.get("readyCrop"), VideoPlanner.alreadyReady(a, edits("{\"crop\":{\"x\":0.1,\"y\":0.1,\"w\":0.5,\"h\":0.5}}")));
            assertEquals(p + "outFull", pairOf(want.get("outFull")), pair(VideoPlanner.outputSize(a, 1920, null)));
            assertEquals(p + "outHd", pairOf(want.get("outHd")), pair(VideoPlanner.outputSize(a, 1280, null)));
            assertEquals(p + "outRot", pairOf(want.get("outRot")), pair(VideoPlanner.outputSize(a, 1920, edits("{\"rotate\":90}"))));
            assertEquals(p + "outCrop", pairOf(want.get("outCrop")),
                    pair(VideoPlanner.outputSize(a, 1280, edits("{\"crop\":{\"x\":0.25,\"y\":0.1,\"w\":0.5,\"h\":0.6}}"))));
            assertEquals(p + "outBoth", pairOf(want.get("outBoth")),
                    pair(VideoPlanner.outputSize(a, 1920, edits("{\"rotate\":270,\"crop\":{\"x\":0,\"y\":0.2,\"w\":0.7,\"h\":0.5}}"))));
            n++;
        }
        assertTrue("сверено видео: " + n, n >= 9);
    }

    @SuppressWarnings("unchecked")
    @Test public void formulasMatchWeb() throws Exception {
        Map<String, Object> ref = Mp4ParityTest.reference();
        if (ref == null) return;
        Map<String, Object> f = Json.obj(ref, "formulas");
        for (Object o : Json.arr(f, "evenFit")) {
            List<Object> row = (List<Object>) o;
            int[] got = VideoPlanner.evenFit(((Number) row.get(0)).doubleValue(), ((Number) row.get(1)).doubleValue(), ((Number) row.get(2)).doubleValue());
            assertEquals("evenFit " + row, pairOf(row.get(3)), pair(got));
        }
        for (Object o : Json.arr(f, "avcCodec")) {
            List<Object> row = (List<Object>) o;
            String got = VideoPlanner.avcCodec(((Number) row.get(0)).intValue(), ((Number) row.get(1)).intValue(), ((Number) row.get(2)).doubleValue(), (String) row.get(3));
            assertEquals("avcCodec " + row, row.get(4), got);
        }
    }

    @Test public void plannerConstants() {
        assertEquals(10, VideoPlanner.benchmarkFrames(1));
        assertEquals(45, VideoPlanner.benchmarkFrames(30));
        assertEquals(38, VideoPlanner.benchmarkFrames(25));
        assertEquals(30, VideoPlanner.targetFps(59.94));
        assertEquals(24, VideoPlanner.targetFps(23.976));
        assertEquals(1, VideoPlanner.targetFps(0.2));
        assertEquals(4354560, VideoPlanner.bitrate(1920, 1080, 30));
        assertTrue(Arrays.equals(new int[]{1280}, VideoPlanner.sides(2_000_000_000L)));
        assertTrue(Arrays.equals(new int[]{1920, 1280}, VideoPlanner.sides(3_800_000_000L)));
        assertTrue(Arrays.equals(new int[]{1920, 1280}, VideoPlanner.sides(0)));
        // ролик длиннее минуты — сразу HD; ровно минута — ещё Full HD; мало памяти — HD всегда
        assertTrue(Arrays.equals(new int[]{1920, 1280}, VideoPlanner.sides(3_800_000_000L, 60)));
        assertTrue(Arrays.equals(new int[]{1280}, VideoPlanner.sides(3_800_000_000L, 61)));
        assertTrue(Arrays.equals(new int[]{1280}, VideoPlanner.sides(2_000_000_000L, 10)));
        // прогноз как в JS: edited * fps / rate * 1.15
        assertEquals(60 * 30 / (45 / 3.0) * 1.15, VideoPlanner.estimateSeconds(60, 30, 45, 3.0), 1e-9);
    }

    static float[] corners(int outW, int outH, int fw, int fh, int rot, String e) {
        return FrameMapping.cornerTexCoords(outW, outH, fw, fh, rot, e == null ? null : edits(e));
    }

    static void near(String msg, float[] want, float[] got) {
        for (int i = 0; i < want.length; i++) assertEquals(msg + " [" + i + "]", want[i], got[i], 1e-6);
    }

    @Test public void frameMappingBasics() {
        // без поворота: углы кадра переходят в углы текстуры (низ-лево, низ-право, верх-лево, верх-право)
        near("0°", new float[]{0, 0, 1, 0, 0, 1, 1, 1}, corners(640, 360, 640, 360, 0, null));
        // 90° по часовой: верх-лево результата берёт низ-лево исходника
        float[] r90 = corners(360, 640, 640, 360, 90, null);
        near("90° верх-лево", new float[]{0, 0}, new float[]{r90[4], r90[5]});
        near("90° верх-право", new float[]{0, 1}, new float[]{r90[6], r90[7]});
        near("90° низ-лево", new float[]{1, 0}, new float[]{r90[0], r90[1]});
        // 180°: всё наоборот
        near("180°", new float[]{1, 1, 0, 1, 1, 0, 0, 0}, corners(640, 360, 640, 360, 180, null));
        // обрезка без поворота: левая половина по ширине, нижняя половина по высоте
        near("обрезка", new float[]{0, 0, 0.5f, 0, 0, 0.5f, 0.5f, 0.5f},
                corners(320, 180, 640, 360, 0, "{\"crop\":{\"x\":0,\"y\":0.5,\"w\":0.5,\"h\":0.5}}"));
    }
}
