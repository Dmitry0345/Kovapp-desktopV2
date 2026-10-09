package ru.kovcheg.kovapp;

import static org.junit.Assert.*;

import java.util.Arrays;

import org.junit.Test;

import ru.kovcheg.kovapp.media.CodecRanking;

public class CodecRankingTest {
    @Test public void vendorBySocAndPlatform() {
        assertEquals("qualcomm", CodecRanking.vendorFrom("QTI", "qcom", "taro"));
        assertEquals("qualcomm", CodecRanking.vendorFrom("", "qcom", "msmnile"));
        assertEquals("mediatek", CodecRanking.vendorFrom("Mediatek", "mt6893", "k6893v1_64"));
        assertEquals("mediatek", CodecRanking.vendorFrom("", "mt6765", ""));
        assertEquals("samsung", CodecRanking.vendorFrom("Samsung", "s5e9925", "s5e9925"));
        assertEquals("samsung", CodecRanking.vendorFrom("", "exynos2100", "universal2100"));
        assertEquals("google", CodecRanking.vendorFrom("Google", "zuma", "shiba"));
        assertEquals("hisilicon", CodecRanking.vendorFrom("", "kirin990", "hi3690"));
        assertEquals("unisoc", CodecRanking.vendorFrom("Spreadtrum", "ums9230", ""));
        assertEquals("", CodecRanking.vendorFrom("", "ranchu", "goldfish_x86_64"));   // эмулятор
        assertEquals("", CodecRanking.vendorFrom(null, null, null));
    }

    @Test public void nativeCodecFirst() {
        assertEquals(0, CodecRanking.rank("c2.qti.avc.encoder", true, "qualcomm"));
        assertEquals(0, CodecRanking.rank("OMX.qcom.video.encoder.avc", true, "qualcomm"));
        assertEquals(0, CodecRanking.rank("c2.mtk.avc.encoder", true, "mediatek"));
        assertEquals(0, CodecRanking.rank("c2.exynos.h264.encoder", true, "google"));
        assertEquals(1, CodecRanking.rank("c2.mtk.avc.encoder", true, "qualcomm"));    // чужой аппаратный
        assertEquals(1, CodecRanking.rank("c2.qti.avc.encoder", true, ""));            // процессор не распознан
        assertEquals(2, CodecRanking.rank("c2.android.avc.encoder", false, "qualcomm")); // программный — последним
    }

    @Test public void blankFrame() {
        java.nio.ByteBuffer black = java.nio.ByteBuffer.allocate(64 * 36 * 4);
        assertTrue(CodecRanking.blank(black, 64 * 36));
        java.nio.ByteBuffer dark = java.nio.ByteBuffer.allocate(64 * 36 * 4);
        dark.put(1000 * 4 + 1, (byte) 24);                  // одна тусклая точка — уже не сплошной чёрный
        assertFalse(CodecRanking.blank(dark, 64 * 36));
    }

    @Test public void speedLadder() {
        // предел, названный кодеком, — первым
        assertTrue(Arrays.equals(new int[]{960, 120, 0}, CodecRanking.speedLadder(960, 1920, 1080)));
        // не назвал — по размеру, как в AOSP MediaTranscoder: 720p → 480, 1080p → 240, больше → 120
        assertTrue(Arrays.equals(new int[]{480, 120, 0}, CodecRanking.speedLadder(0, 1280, 720)));
        assertTrue(Arrays.equals(new int[]{480, 120, 0}, CodecRanking.speedLadder(0, 720, 1280)));
        assertTrue(Arrays.equals(new int[]{240, 120, 0}, CodecRanking.speedLadder(0, 1920, 1080)));
        assertTrue(Arrays.equals(new int[]{120, 120, 0}, CodecRanking.speedLadder(0, 3840, 2160)));
    }
}
