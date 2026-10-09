package ru.kovcheg.kovapp.media;

import android.media.MediaCodecInfo;
import android.media.MediaFormat;
import android.os.Build;

import java.util.ArrayList;
import java.util.List;

/**
 * Кодеки под процессор телефона: родной аппаратный кодировщик производителя — первым,
 * и подсказка работать на его предельной скорости, а не в темпе реального времени.
 */
public final class HardwareCodecs {
    private HardwareCodecs() {}

    /** Производитель процессора: сведения Android 12+ или название платформы */
    public static String vendor() {
        String soc = Build.VERSION.SDK_INT >= 31 ? Build.SOC_MANUFACTURER : "";
        return CodecRanking.vendorFrom(soc, Build.HARDWARE, Build.BOARD);
    }

    /** Кодеки в порядке предпочтения; внутри группы — порядок системы */
    public static List<MediaCodecInfo> ordered(List<MediaCodecInfo> list) {
        String vendor = vendor();
        List<MediaCodecInfo> out = new ArrayList<>();
        for (int r = 0; r <= 2; r++) {
            for (MediaCodecInfo i : list) if (CodecRanking.rank(i.getName(), VideoTranscoder.hardware(i), vendor) == r) out.add(i);
        }
        return out;
    }

    /** Сколько кадров в секунду кодек успевает для этого размера; 0 — неизвестно */
    public static int maxFrameRate(MediaCodecInfo.VideoCapabilities vc, int w, int h) {
        if (vc == null) return 0;
        if (Build.VERSION.SDK_INT >= 29) {
            List<MediaCodecInfo.VideoCapabilities.PerformancePoint> points = vc.getSupportedPerformancePoints();
            if (points != null && !points.isEmpty()) {
                for (int fr : new int[]{960, 480, 240, 120, 60}) {
                    MediaCodecInfo.VideoCapabilities.PerformancePoint want = new MediaCodecInfo.VideoCapabilities.PerformancePoint(w, h, fr);
                    for (MediaCodecInfo.VideoCapabilities.PerformancePoint p : points) if (p.covers(want)) return fr;
                }
            }
        }
        try {
            android.util.Range<Double> r = vc.getAchievableFrameRatesFor(w, h);
            if (r != null && r.getUpper() != null) return (int) Math.floor(r.getUpper());
        } catch (RuntimeException e) {
            // размер вне таблицы замеров производителя
        }
        return 0;
    }

    /**
     * Рабочая скорость выше реального времени — кодек планирует ресурсы и частоты под неё.
     * Приоритет 1 («без гарантий реального времени»): так перекодируют Media3 и системный перекодировщик;
     * 0 — для воспроизведения и съёмки, с ним кодек обязан держать темп и чаще отказывается от настройки.
     */
    public static void applySpeed(MediaFormat f, int rate) {
        if (rate <= 0) return;
        f.setInteger(MediaFormat.KEY_PRIORITY, 1);
        f.setInteger(MediaFormat.KEY_OPERATING_RATE, rate);
    }
}
