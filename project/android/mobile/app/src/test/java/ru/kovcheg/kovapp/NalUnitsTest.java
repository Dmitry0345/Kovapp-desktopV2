package ru.kovcheg.kovapp;

import static org.junit.Assert.*;

import java.io.File;
import java.util.List;

import org.junit.Test;

import ru.kovcheg.kovapp.media.Mp4Demuxer;
import ru.kovcheg.kovapp.media.NalUnits;
import ru.kovcheg.kovapp.media.Source;

public class NalUnitsTest {
    static Mp4Demuxer.Info demux(String name) throws Exception {
        try (Source s = Source.of(new File(TestPaths.fixtures(), name))) { return Mp4Demuxer.demux(s); }
    }

    @Test public void avcCRebuiltExactlyLikeFfmpeg() throws Exception {
        if (TestPaths.fixtures() == null) return;
        for (String f : new String[]{"h264_aac_rot.mp4", "h264_ready_faststart.mp4", "h264_60fps_noaudio.mp4", "h264_mp3_rot.mov", "small.3gp", "two_audio.mp4"}) {
            byte[] avcC = demux(f).video.entry.description;
            NalUnits.Csd csd = NalUnits.fromAvcC(avcC);
            assertEquals(f + ": длина поля NAL", 4, csd.nalLength);
            List<byte[]> sps = NalUnits.splitAnnexB(csd.csd0, 0, csd.csd0.length);
            List<byte[]> pps = NalUnits.splitAnnexB(csd.csd1, 0, csd.csd1.length);
            assertEquals(f + ": SPS", 7, sps.get(0)[0] & 0x1f);
            assertEquals(f + ": PPS", 8, pps.get(0)[0] & 0x1f);
            assertArrayEquals(f + ": avcC побайтно как у ffmpeg", avcC, NalUnits.buildAvcC(sps, pps));
            byte[] config = new byte[csd.csd0.length + csd.csd1.length];
            System.arraycopy(csd.csd0, 0, config, 0, csd.csd0.length);
            System.arraycopy(csd.csd1, 0, config, csd.csd0.length, csd.csd1.length);
            assertArrayEquals(f + ": avcC из буфера кодировщика", avcC, NalUnits.avcCFromCodecConfig(config, 0, config.length));
            assertEquals(f + ": строка кодека", demux(f).video.entry.codec, NalUnits.avcCodecString(avcC));
        }
    }

    @Test public void samplesRoundTrip() throws Exception {
        if (TestPaths.fixtures() == null) return;
        Mp4Demuxer.Info info = demux("h264_aac_rot.mp4");
        try (Source s = Source.of(new File(TestPaths.fixtures(), "h264_aac_rot.mp4"))) {
            Mp4Demuxer.Track v = info.video;
            for (int i = 0; i < v.count; i++) {
                byte[] sample = Mp4Demuxer.readSample(s, v, i);
                byte[] annexB = NalUnits.toAnnexB(sample, 4);
                assertEquals(0, annexB[0]); assertEquals(1, annexB[3]);
                assertArrayEquals("кадр " + i, sample, NalUnits.annexBToAvcc(annexB, 0, annexB.length));
            }
        }
    }

    @Test public void hevcConfig() throws Exception {
        if (TestPaths.fixtures() == null) return;
        byte[] hvcC = demux("hevc_opus.mp4").video.entry.description;
        NalUnits.Csd csd = NalUnits.fromHvcC(hvcC);
        assertEquals(4, csd.nalLength);
        List<byte[]> nals = NalUnits.splitAnnexB(csd.csd0, 0, csd.csd0.length);
        assertTrue("VPS, SPS, PPS", nals.size() >= 3);
        assertEquals(32, (nals.get(0)[0] >> 1) & 0x3f);
        assertEquals(33, (nals.get(1)[0] >> 1) & 0x3f);
        assertEquals(34, (nals.get(2)[0] >> 1) & 0x3f);
    }

    @Test public void splitHandlesThreeByteCodesAndGarbage() {
        byte[] d = {0, 0, 1, 0x67, 1, 2, 0, 0, 0, 1, 0x68, 3, 0, 0, 1, 0x65, 9, 9, 0};
        List<byte[]> n = NalUnits.splitAnnexB(d, 0, d.length);
        assertEquals(3, n.size());
        assertArrayEquals(new byte[]{0x67, 1, 2}, n.get(0));
        assertArrayEquals(new byte[]{0x68, 3}, n.get(1));
        assertArrayEquals(new byte[]{0x65, 9, 9}, n.get(2));
        byte[] avcc = NalUnits.annexBToAvcc(d, 0, d.length);
        assertArrayEquals(new byte[]{0, 0, 0, 3, 0x65, 9, 9}, avcc);
        assertEquals(0, NalUnits.toAnnexB(new byte[]{0, 0, 0, 50, 1}, 4).length);
    }
}
