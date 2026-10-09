package ru.kovcheg.kovapp.media;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/** Время сжатия по этапам — для журнала KovVideo: где на самом деле уходит время */
public final class StageTimes {
    public static final int SETUP = 0, READ = 1, DECODE = 2, DRAW = 3, ENCODE = 4, SEND = 5, PARSE = 6, POSTER = 7, FINISH = 8, MUX = 9;
    private static final String[] NAMES = {"подготовка", "чтение", "декодирование", "отрисовка", "кодирование", "отправка",
            "разбор файла", "обложка", "завершение", "упаковка"};
    private final long[] nanos = new long[NAMES.length];

    public static long now() { return System.nanoTime(); }

    /** Добавить к этапу время с момента since */
    public void add(int stage, long since) { nanos[stage] += System.nanoTime() - since; }

    public long get(int stage) { return nanos[stage]; }

    /** Добавить к этапу готовую длительность (замерена до создания сжатия) */
    public void addNanos(int stage, long n) { nanos[stage] += Math.max(0, n); }

    /** «кодирование 10.3 с (42%), …, прочее 0.4 с» — самый долгий этап первым */
    public String format(long totalNanos) {
        List<Integer> order = new ArrayList<>();
        long sum = 0;
        for (int i = 0; i < NAMES.length; i++) { order.add(i); sum += nanos[i]; }
        order.sort((x, y) -> Long.compare(nanos[y], nanos[x]));
        double total = Math.max(1, totalNanos);
        StringBuilder sb = new StringBuilder();
        for (int i : order) {
            if (i >= PARSE && nanos[i] == 0) continue;   // необязательные этапы — только если были
            if (sb.length() > 0) sb.append(", ");
            sb.append(String.format(Locale.ROOT, "%s %.1f с (%.0f%%)", NAMES[i], nanos[i] / 1e9, nanos[i] * 100 / total));
        }
        sb.append(String.format(Locale.ROOT, ", прочее %.1f с", Math.max(0, totalNanos - sum) / 1e9));
        return sb.toString();
    }
}
