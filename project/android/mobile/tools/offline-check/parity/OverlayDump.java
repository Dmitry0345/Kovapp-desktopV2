import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;

import ru.kovcheg.kovapp.web.Overlay;

/** Печатает результат надстройки Java для файла: сверка с tools/devserver.py. */
public final class OverlayDump {
    public static void main(String[] a) throws Exception {
        File overlay = new File(a[0]), web = new File(a[1]), out = new File(a[2]);
        Overlay o = new Overlay(read(new File(overlay, "inject.json")), read(new File(overlay, "web-patches.json")), true);
        for (int i = 3; i < a.length; i++) {
            String path = a[i];
            String text = read(new File(web, path));
            if (path.endsWith(".html")) text = o.injectHtml(text);
            text = o.patchText(path, text);
            File f = new File(out, path);
            f.getParentFile().mkdirs();
            Files.write(f.toPath(), text.getBytes(StandardCharsets.UTF_8));
        }
        for (String w : o.warnings()) System.out.println("WARN " + w);
    }

    static String read(File f) throws Exception { return new String(Files.readAllBytes(f.toPath()), StandardCharsets.UTF_8); }
}
