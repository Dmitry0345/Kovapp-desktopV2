package ru.kovcheg.kovapp;

import static org.junit.Assert.*;

import org.junit.Test;

import ru.kovcheg.kovapp.media.StageTimes;

public class StageTimesTest {
    @Test public void quickEstimateNeverSlowerThanRealtime() {
        assertEquals(30.0, ru.kovcheg.kovapp.media.VideoPlanner.quickEstimate(30, 30, 0), 1e-9);    // скорость неизвестна — реальное время
        assertEquals(10.0, ru.kovcheg.kovapp.media.VideoPlanner.quickEstimate(30, 30, 90), 1e-9);   // кодировщик втрое быстрее
        assertEquals(30.0, ru.kovcheg.kovapp.media.VideoPlanner.quickEstimate(30, 30, 20), 1e-9);   // медленнее реального времени не считаем
        assertEquals(0.0, ru.kovcheg.kovapp.media.VideoPlanner.quickEstimate(-5, 30, 60), 1e-9);
    }

    @Test public void longestStageFirstAndRestCounted() throws Exception {
        StageTimes t = new StageTimes();
        long s = StageTimes.now();
        Thread.sleep(30);
        t.add(StageTimes.ENCODE, s);
        long r = StageTimes.now();
        Thread.sleep(10);
        t.add(StageTimes.READ, r);
        long total = t.get(StageTimes.ENCODE) + t.get(StageTimes.READ) + 20_000_000L;   // + 20 мс вне этапов
        String line = t.format(total);
        assertTrue(line, line.startsWith("кодирование "));
        assertTrue(line, line.indexOf("кодирование") < line.indexOf("чтение"));
        assertTrue(line, line.endsWith("прочее 0.0 с") || line.contains("прочее 0.0 с"));
        assertTrue(line, line.contains("отправка 0.0 с (0%)"));
    }

    @org.junit.Test public void newStagesListedAndAddNanos() {
        ru.kovcheg.kovapp.media.StageTimes t = new ru.kovcheg.kovapp.media.StageTimes();
        t.addNanos(ru.kovcheg.kovapp.media.StageTimes.PARSE, 300_000_000L);
        t.addNanos(ru.kovcheg.kovapp.media.StageTimes.FINISH, 200_000_000L);
        t.addNanos(ru.kovcheg.kovapp.media.StageTimes.POSTER, -5);   // отрицательное не учитывается
        String line = t.format(1_000_000_000L);
        org.junit.Assert.assertTrue(line, line.startsWith("разбор файла 0.3 с (30%)"));
        org.junit.Assert.assertTrue(line, line.contains("завершение 0.2 с (20%)"));
        org.junit.Assert.assertFalse(line, line.contains("обложка"));   // не было — не показывается
        org.junit.Assert.assertTrue(line, line.endsWith("прочее 0.5 с"));
    }
}
