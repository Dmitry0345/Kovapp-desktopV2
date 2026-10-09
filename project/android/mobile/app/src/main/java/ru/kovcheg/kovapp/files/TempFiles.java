package ru.kovcheg.kovapp.files;

import java.io.File;
import java.io.IOException;
import java.security.SecureRandom;
import java.util.Iterator;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

import ru.kovcheg.kovapp.core.B64;

/**
 * Временные файлы, которые приложение отдаёт странице (сжатое видео кусками,
 * обложка, превью фото). Страница получает их по адресу
 * /app/__kovapp__/<метка>: метка случайная (128 бит), файл живёт до
 * release() или до выхода из учётной записи.
 */
public final class TempFiles {
    public static final String PREFIX = "__kovapp__/";
    private static final SecureRandom RANDOM = new SecureRandom();

    /** Источник файла галереи: поток открывается при каждом запросе страницы */
    public interface Opener { java.io.InputStream open() throws IOException; }

    public static final class Item {
        public final String token, mime;
        public final File file;          // обычный временный файл, или null
        public final Opener opener;      // файл галереи (без копии), или null
        public final long size;
        Item(String token, File file, String mime) { this(token, file, mime, null, -1); }
        Item(String token, File file, String mime, Opener opener, long size) {
            this.token = token; this.file = file; this.mime = mime; this.opener = opener; this.size = size;
        }
        public long length() { return opener != null ? size : file.length(); }
        public java.io.InputStream open() throws IOException { return opener != null ? opener.open() : new java.io.FileInputStream(file); }
    }

    private final File dir;
    private final Map<String, Item> items = new ConcurrentHashMap<>();

    public TempFiles(File dir) { this.dir = dir; }

    public File dir() { return dir; }

    /** Новый пустой файл под метку */
    public Item create(String ext, String mime) throws IOException {
        if (!dir.isDirectory() && !dir.mkdirs()) throw new IOException("нет папки временных файлов");
        byte[] b = new byte[16];
        RANDOM.nextBytes(b);
        String token = B64.hex(b);
        File f = new File(dir, token + (ext == null ? "" : ext));
        if (!f.createNewFile()) throw new IOException("не удалось создать временный файл");
        Item it = new Item(token, f, mime == null ? "application/octet-stream" : mime);
        items.put(token, it);
        return it;
    }

    /** Файл галереи под метку — отдаётся потоком из самой галереи */
    public Item createStream(Opener opener, String mime, long size) {
        byte[] b = new byte[16];
        RANDOM.nextBytes(b);
        String token = B64.hex(b);
        Item it = new Item(token, null, mime == null ? "application/octet-stream" : mime, opener, size);
        items.put(token, it);
        return it;
    }

    /** Относительный адрес для страницы (от папки приложения) */
    public static String relUrl(Item it) { return PREFIX + it.token; }

    /** Метка из относительного пути; чужие и несуществующие метки → null */
    public Item lookup(String rel) {
        if (rel == null || !rel.startsWith(PREFIX)) return null;
        String token = rel.substring(PREFIX.length());
        if (!token.matches("[0-9a-f]{32}")) return null;
        Item it = items.get(token);
        return it != null && (it.opener != null || it.file.isFile()) ? it : null;
    }

    public boolean release(String relOrToken) {
        String token = relOrToken == null ? "" : relOrToken;
        int i = token.lastIndexOf('/');
        if (i >= 0) token = token.substring(i + 1);
        Item it = items.remove(token);
        return it != null && (it.file == null || !it.file.exists() || it.file.delete());
    }

    /** Удалить всё: при запуске приложения и при выходе из учётной записи */
    public void clearAll() {
        for (Iterator<Map.Entry<String, Item>> i = items.entrySet().iterator(); i.hasNext(); ) {
            Item it = i.next().getValue();
            if (it.file != null && it.file.exists() && !it.file.delete()) it.file.deleteOnExit();
            i.remove();
        }
        File[] rest = dir.listFiles();
        if (rest != null) for (File f : rest) deleteTree(f);
    }

    static void deleteTree(File f) {
        File[] kids = f.listFiles();
        if (kids != null) for (File k : kids) deleteTree(k);
        if (!f.delete()) f.deleteOnExit();
    }

    public int size() { return items.size(); }
}
