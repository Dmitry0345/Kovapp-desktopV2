package ru.kovcheg.kovapp;

import static org.junit.Assert.assertEquals;

import java.io.File;
import java.io.RandomAccessFile;
import java.nio.file.Files;

import org.junit.Test;

import ru.kovcheg.kovapp.media.FileHasher;
import ru.kovcheg.kovapp.media.Source;

/** Образец файла совпадает с серверным (blobs.sample_of) — эталоны посчитаны серверным кодом */
public class FileHasherSampleTest {
    private static String sampleOf(int n) throws Exception {
        byte[] data = new byte[n];
        for (int i = 0; i < n; i++) data[i] = (byte) ((i * 31 + 7) & 255);
        File f = File.createTempFile("sample", ".bin");
        f.deleteOnExit();
        Files.write(f.toPath(), data);
        RandomAccessFile raf = new RandomAccessFile(f, "r");
        try (Source s = new Source(raf.getChannel(), f.length(), raf)) {
            return FileHasher.sample(s);
        }
    }

    @Test public void matchesServerForLargeFile() throws Exception {
        assertEquals("4a145cef8f4818566691a9dcef90ade4bb237f064ec3e88e2e3492c0007a11af", sampleOf(1_000_003));
    }

    @Test public void matchesServerForFileSmallerThanWindow() throws Exception {
        assertEquals("9963c1f3c7c5caf752719aaa74fbed94aaf33bfbfdff768c6c4b3d918822441d", sampleOf(50_000));
    }
}
