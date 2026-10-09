package ru.kovcheg.kovapp;

import static org.junit.Assert.*;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;

import org.junit.Test;

import ru.kovcheg.kovapp.media.CodecReleaser;

public class CodecReleaserTest {
    @Test public void awaitIdleWaitsForPendingReleasesInOrder() {
        final List<Integer> done = Collections.synchronizedList(new ArrayList<>());
        CodecReleaser.submit(() -> { sleep(150); done.add(1); });
        CodecReleaser.submit(() -> { sleep(50); done.add(2); });
        long t0 = System.currentTimeMillis();
        assertTrue(CodecReleaser.awaitIdle(3000));
        assertTrue("ждали освобождения", System.currentTimeMillis() - t0 >= 150);
        assertEquals(Arrays.asList(1, 2), done);   // по очереди, как отдали
    }

    @Test public void awaitIdleGivesUpAfterTimeout() {
        CodecReleaser.submit(() -> sleep(400));
        assertFalse(CodecReleaser.awaitIdle(50));
        assertTrue(CodecReleaser.awaitIdle(3000));
    }

    private static void sleep(long ms) { try { Thread.sleep(ms); } catch (InterruptedException e) { Thread.currentThread().interrupt(); } }
}
