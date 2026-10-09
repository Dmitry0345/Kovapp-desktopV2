package ru.kovcheg.kovapp.files;

import java.util.ArrayList;
import java.util.List;

/**
 * Заготовленный выбор файлов: галерея приложения отдаёт выбранное странице как обычный выбор файла
 * (страница читает файлы с телефона, без копирования). Одноразово и недолго — чтобы не попасть в чужой выбор.
 */
public final class ChooserPreset<T> {
    private List<T> items;
    private long until;

    public synchronized void set(List<T> list, long ttlMs, long now) {
        items = list == null || list.isEmpty() ? null : new ArrayList<>(list);
        until = now + ttlMs;
    }

    /** Забрать заготовку (один раз); просроченная — не отдаётся */
    public synchronized List<T> take(long now) {
        List<T> p = items;
        items = null;
        return p != null && now <= until ? p : null;
    }
}
