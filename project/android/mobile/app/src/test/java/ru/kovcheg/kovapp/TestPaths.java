package ru.kovcheg.kovapp;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;

/** Где лежат проект, веб-интерфейс и подготовленные образцы для тестов. */
public final class TestPaths {
    private TestPaths() {}

    public static File mobile() {
        String fx = System.getProperty("kovapp.fixtures");
        if (fx != null) return new File(fx).getParentFile().getParentFile();
        File here = new File("").getAbsoluteFile();
        // gradlew test запускает тесты из папки app/
        return here.getName().equals("app") ? here.getParentFile() : here;
    }

    public static File webApp() { return new File(mobile().getParentFile(), "web_app"); }
    public static File overlay() { return new File(mobile(), "overlay"); }

    /** Папка образцов; null — образцы не подготовлены (tests/make_fixtures.py), тест пропускается */
    public static File fixtures() {
        File f = new File(mobile(), "tests/fixtures");
        return new File(f, "READY").isFile() ? f : null;
    }

    public static byte[] read(File f) throws IOException {
        try (InputStream in = new FileInputStream(f)) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[65536];
            int n;
            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
            return out.toByteArray();
        }
    }

    public static String text(File f) throws IOException { return new String(read(f), StandardCharsets.UTF_8); }
}
