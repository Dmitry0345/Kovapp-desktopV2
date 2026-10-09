package ru.kovcheg.kovapp;

import static org.junit.Assert.*;

import java.io.File;

import org.junit.Test;

import ru.kovcheg.kovapp.media.Mp4Demuxer;
import ru.kovcheg.kovapp.media.Source;
import ru.kovcheg.kovapp.media.VideoPlanner;

/** «Уже подходит» днём: длинное видео с умеренным потоком уходит как есть (пережимает сервер ночью), как ready_long_test.js */
public class VideoReadyLongTest {
    private static VideoPlanner.Analysis ready720() throws Exception {
        File f = new File(TestPaths.fixtures(), "h264_ready_faststart.mp4");
        if (!f.exists()) return null;
        try (Source src = Source.of(f)) { return VideoPlanner.analyze(Mp4Demuxer.demux(src)); }
    }

    @Test public void longVideosWithModerateBitrateAreReady() throws Exception {
        VideoPlanner.Analysis a = ready720();
        if (a == null) return;
        assertTrue("образец 720p готов", VideoPlanner.alreadyReady(a, null));
        double bpp = a.bitrate / (a.width * (double) a.height * Math.min(a.fps, VideoPlanner.MAX_FPS));
        a.duration = 900;
        assertTrue("длинное 720p — уже HD, готово", VideoPlanner.alreadyReady(a, null));
        int w = a.width > a.height ? 1920 : 1080, h = a.width > a.height ? 1080 : 1920;
        a.width = w; a.height = h; a.bitrate = bpp * w * h * Math.min(a.fps, VideoPlanner.MAX_FPS);
        a.duration = 30;
        assertTrue("короткое 1080p с тем же качеством — готово", VideoPlanner.alreadyReady(a, null));
        a.duration = 900;
        assertTrue("длинное 1080p — днём как есть, ночью пережмёт сервер", VideoPlanner.alreadyReady(a, null));
        a.bitrate = VideoPlanner.READY_BPP * 2 * w * h * Math.min(a.fps, VideoPlanner.MAX_FPS);   // вдвое выше порога
        assertFalse("большой поток — сжимается сразу", VideoPlanner.alreadyReady(a, null));
    }
}
