package ru.kovcheg.kovapp;

import static org.junit.Assert.*;

import java.io.File;
import java.util.Arrays;
import java.util.List;
import java.util.Map;

import org.junit.Test;

import ru.kovcheg.kovapp.core.Json;
import ru.kovcheg.kovapp.media.FileHasher;
import ru.kovcheg.kovapp.media.ImageRules;
import ru.kovcheg.kovapp.media.Source;

/** Размеры картинок по заголовку и отпечатки файлов совпадают с веб-версией. */
public class MediaRulesTest {
    @SuppressWarnings("unchecked")
    @Test public void imageDimsMatchWeb() throws Exception {
        Map<String, Object> ref = Mp4ParityTest.reference();
        if (ref == null) return;
        for (Map.Entry<String, Object> e : Json.obj(ref, "dims").entrySet()) {
            Map<String, Object> want = (Map<String, Object>) e.getValue();
            byte[] all = TestPaths.read(new File(TestPaths.fixtures(), e.getKey()));
            byte[] head = Arrays.copyOf(all, Math.min(all.length, 256 * 1024));
            ImageRules.Dims d = ImageRules.dims(head);
            String p = e.getKey() + ": ";
            assertNotNull(p + "распознан", d);
            assertEquals(p + "type", Json.str(want, "type", null), d.type);
            assertEquals(p + "width", Json.num(want, "width", -1), d.width);
            assertEquals(p + "height", Json.num(want, "height", -1), d.height);
            assertEquals(p + "alpha", Json.bool(want, "alpha", false), d.alpha);
            if (want.containsKey("orientation")) assertEquals(p + "orientation", Json.num(want, "orientation", -1), d.orientation);
        }
        for (Object o : Json.arr(ref, "fit")) {
            List<Object> row = (List<Object>) o;
            long[] got = ImageRules.fit(((Number) row.get(0)).longValue(), ((Number) row.get(1)).longValue(), ((Number) row.get(2)).longValue());
            List<Object> want = (List<Object>) row.get(3);
            assertEquals("fit " + row, ((Number) want.get(0)).longValue(), got[0]);
            assertEquals("fit " + row, ((Number) want.get(1)).longValue(), got[1]);
        }
        assertNull(ImageRules.dims(new byte[]{1, 2, 3, 4, 5}));
        assertEquals("photo.webp", ImageRules.renamed("photo.HEIC", ".webp"));
        assertEquals("IMG_1.webp", ImageRules.renamed("IMG_1.jpg", ".webp"));
        assertEquals("photo.webp", ImageRules.renamed(".jpg", ".webp"));
        assertEquals("a.b.webp", ImageRules.renamed("a.b.c", ".webp"));
    }

    @SuppressWarnings("unchecked")
    @Test public void fileHashMatchesWeb() throws Exception {
        Map<String, Object> ref = Mp4ParityTest.reference();
        if (ref == null) return;
        for (Map.Entry<String, Object> e : Json.obj(ref, "hash").entrySet()) {
            Map<String, Object> want = (Map<String, Object>) e.getValue();
            try (Source s = Source.of(new File(TestPaths.fixtures(), e.getKey()))) {
                final double[] last = {0};
                assertEquals(e.getKey() + ": дерево", Json.str(want, "tree", null), FileHasher.tree(s, p -> last[0] = p, null));
                if (s.size() > 0) assertEquals(1.0, last[0], 1e-9);
                for (Object o : Json.arr(want, "ranges")) {
                    List<Object> r = (List<Object>) o;
                    long off = ((Number) r.get(0)).longValue(), len = ((Number) r.get(1)).longValue();
                    assertEquals(e.getKey() + ": кусок " + off + "+" + len, r.get(2), FileHasher.range(s, off, len));
                }
            }
        }
    }
}
