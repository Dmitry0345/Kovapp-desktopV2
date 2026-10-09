package ru.kovcheg.kovapp.media;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

/**
 * Освобождение кодеков в фоне: результат сжатия отдаётся сразу, а новые кодеки
 * создаются только после того, как прежние освобождены (у телефона их число ограничено).
 */
public final class CodecReleaser {
    private static final ExecutorService POOL = Executors.newSingleThreadExecutor(r -> {
        Thread t = new Thread(r, "kov-codec-release");
        t.setDaemon(true);
        return t;
    });

    private CodecReleaser() { }

    public static void submit(Runnable task) { POOL.execute(task); }

    /** Дождаться освобождения ранее отданных кодеков (не дольше timeoutMs); true — дождались */
    public static boolean awaitIdle(long timeoutMs) {
        try {
            POOL.submit(() -> { }).get(timeoutMs, TimeUnit.MILLISECONDS);
            return true;
        } catch (Exception e) {
            return false;
        }
    }
}
