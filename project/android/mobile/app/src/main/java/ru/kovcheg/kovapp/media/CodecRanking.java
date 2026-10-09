package ru.kovcheg.kovapp.media;

import java.util.Locale;

/** Выбор кодека под процессор — без зависимостей от Android (проверяется тестами JVM) */
public final class CodecRanking {
    private CodecRanking() {}

    public static String vendorFrom(String soc, String hardware, String board) {
        String s = ((soc == null ? "" : soc) + " " + (hardware == null ? "" : hardware) + " " + (board == null ? "" : board))
                .toLowerCase(Locale.ROOT);
        if (s.contains("qti") || s.contains("qcom") || s.contains("qualcomm")) return "qualcomm";
        if (s.contains("mediatek") || s.matches(".*\\bmt\\d{4}.*")) return "mediatek";
        if (s.contains("google") || s.contains("gs101") || s.contains("gs201") || s.contains("zuma")) return "google";
        if (s.contains("samsung") || s.contains("exynos") || s.matches(".*\\bs5e\\d+.*")) return "samsung";
        if (s.contains("hisilicon") || s.contains("kirin") || s.matches(".*\\bhi\\d{4}.*")) return "hisilicon";
        if (s.contains("unisoc") || s.contains("spreadtrum") || s.contains("sprd") || s.matches(".*\\bums\\d+.*")) return "unisoc";
        return "";
    }

    /** Начала имён кодеков, которые написал производитель процессора */
    public static String[] prefixes(String vendor) {
        switch (vendor) {
            case "qualcomm": return new String[]{"c2.qti.", "omx.qcom."};
            case "mediatek": return new String[]{"c2.mtk.", "omx.mtk."};
            case "samsung": return new String[]{"c2.exynos.", "omx.exynos.", "omx.sec."};
            case "google": return new String[]{"c2.google.", "c2.exynos.", "omx.exynos."};   // Tensor — на основе Exynos
            case "hisilicon": return new String[]{"c2.hisi.", "omx.hisi."};
            case "unisoc": return new String[]{"c2.unisoc.", "c2.sprd.", "omx.sprd."};
            default: return new String[0];
        }
    }

    /** 0 — родной кодек процессора, 1 — другой аппаратный, 2 — программный */
    public static int rank(String codecName, boolean hardware, String vendor) {
        if (!hardware) return 2;
        String n = codecName == null ? "" : codecName.toLowerCase(Locale.ROOT);
        for (String p : prefixes(vendor)) if (n.startsWith(p)) return 0;
        return 1;
    }

    /** Кадр RGBA почти сплошной чёрный: ни одна из ~4000 проб не ярче 8/255 (даже ночная съёмка ярче) */
    public static boolean blank(java.nio.ByteBuffer rgba, int pixels) {
        if (pixels <= 0) return true;
        int step = Math.max(1, pixels / 4000);
        for (int i = 0; i < pixels; i += step) {
            int o = i * 4;
            if ((rgba.get(o) & 0xff) > 8 || (rgba.get(o + 1) & 0xff) > 8 || (rgba.get(o + 2) & 0xff) > 8) return false;
        }
        return true;
    }

    /** Рабочая скорость по размеру, как в системном перекодировщике Android (AOSP MediaTranscoder) */
    public static int defaultRate(int w, int h) {
        long px = (long) w * h;
        return px <= 1280L * 720 ? 480 : px <= 1920L * 1088 ? 240 : 120;
    }

    /** Подсказки по очереди: предел кодека (или по размеру) → 120 к/с → без подсказки, как раньше */
    public static int[] speedLadder(int maxRate, int w, int h) {
        return new int[]{maxRate > 0 ? maxRate : defaultRate(w, h), 120, 0};
    }

}
