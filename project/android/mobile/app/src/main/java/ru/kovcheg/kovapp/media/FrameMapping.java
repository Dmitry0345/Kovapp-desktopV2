package ru.kovcheg.kovapp.media;

/**
 * Геометрия кадра при сжатии: тот же результат, что даёт makeRenderer в
 * video-prep.js (canvas: translate → rotate → drawImage с обрезкой), но в
 * виде текстурных координат для четырёх углов выходного кадра OpenGL.
 * Поворот кратен 90°, поэтому отображение аффинное и углов достаточно.
 */
public final class FrameMapping {
    private FrameMapping() {}

    /** Прямоугольник-источник в пикселях декодированного кадра (sx, sy, sw, sh) */
    public static long[] sourceRect(int fw, int fh, int rot, VideoPlanner.Edits e) {
        long sx = 0, sy = 0, sw = fw, sh = fh;
        if (e != null && e.cropActive()) {
            boolean turned = rot == 90 || rot == 270;
            double bx = turned ? e.cropY : e.cropX, by = turned ? e.cropX : e.cropY;
            double bw = turned ? e.cropH : e.cropW, bh = turned ? e.cropW : e.cropH;
            sx = VideoPlanner.jsRound(bx * fw);
            sy = VideoPlanner.jsRound(by * fh);
            sw = VideoPlanner.jsRound(bw * fw);
            sh = VideoPlanner.jsRound(bh * fh);
        }
        return new long[]{sx, sy, sw, sh};
    }

    /**
     * Текстурные координаты (s, t; t снизу вверх, как в OpenGL) для углов
     * выходного кадра в порядке полосы треугольников: низ-лево, низ-право,
     * верх-лево, верх-право.
     */
    public static float[] cornerTexCoords(int outW, int outH, int fw, int fh, int rot, VideoPlanner.Edits e) {
        if (rot % 90 != 0) throw new IllegalArgumentException("поворот должен быть кратен 90°");
        long[] r = sourceRect(fw, fh, rot, e);
        boolean turned = rot == 90 || rot == 270;
        double dw = turned ? outH : outW, dh = turned ? outW : outH;
        int cos, sin;
        switch (((rot % 360) + 360) % 360) {
            case 90: cos = 0; sin = 1; break;
            case 180: cos = -1; sin = 0; break;
            case 270: cos = 0; sin = -1; break;
            default: cos = 1; sin = 0;
        }
        double[][] corners = {{0, outH}, {outW, outH}, {0, 0}, {outW, 0}};
        float[] out = new float[8];
        for (int i = 0; i < 4; i++) {
            double qx = corners[i][0] - outW / 2.0, qy = corners[i][1] - outH / 2.0;
            double lx = qx * cos + qy * sin, ly = -qx * sin + qy * cos;
            double u = (lx + dw / 2) / dw, v = (ly + dh / 2) / dh;
            double px = r[0] + u * r[2], py = r[1] + v * r[3];
            out[i * 2] = (float) (px / fw);
            out[i * 2 + 1] = (float) (1 - py / fh);
        }
        return out;
    }
}
