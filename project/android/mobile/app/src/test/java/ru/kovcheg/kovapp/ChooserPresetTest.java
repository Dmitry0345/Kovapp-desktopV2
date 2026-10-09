package ru.kovcheg.kovapp;

import static org.junit.Assert.*;

import java.util.Arrays;

import org.junit.Test;

import ru.kovcheg.kovapp.files.ChooserPreset;

public class ChooserPresetTest {
    @Test public void givenOnceWithinTtl() {
        ChooserPreset<String> p = new ChooserPreset<>();
        p.set(Arrays.asList("a", "b"), 10_000, 1_000);
        assertEquals(Arrays.asList("a", "b"), p.take(5_000));
        assertNull("второй раз не отдаётся", p.take(5_001));
    }

    @Test public void expiredOrEmptyIsNotGiven() {
        ChooserPreset<String> p = new ChooserPreset<>();
        p.set(Arrays.asList("a"), 10_000, 1_000);
        assertNull("просрочено", p.take(11_001));
        p.set(java.util.Collections.emptyList(), 10_000, 1_000);
        assertNull(p.take(1_001));
    }
}
