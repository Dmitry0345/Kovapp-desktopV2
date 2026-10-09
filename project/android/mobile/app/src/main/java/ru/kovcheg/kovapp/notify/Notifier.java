package ru.kovcheg.kovapp.notify;

import android.Manifest;
import android.annotation.TargetApi;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;

import java.util.ArrayList;
import java.util.List;

import ru.kovcheg.kovapp.MainActivity;
import ru.kovcheg.kovapp.R;

/**
 * Системные уведомления. Метка (tag) та же, что у веб-версии
 * («chat-12», «notification-5»): повторное событие заменяет прежнее и не
 * звучит второй раз — как renotify:false у браузера.
 */
public final class Notifier {
    private Notifier() {}

    public static final String CH_CHAT = "chat", CH_GROUP = "group", CH_NOTIFY = "notification",
            CH_SERVICE = "service", CH_FILES = "downloads", CH_TRANSFER = "transfers";
    static final int ID = 1;
    public static final int SERVICE_ID = 1001;
    static final int ACCENT = 0xFF546E7A;

    public static void ensureChannels(Context c) {
        if (Build.VERSION.SDK_INT < 26) return;
        NotificationManager nm = c.getSystemService(NotificationManager.class);
        List<NotificationChannel> list = new ArrayList<>();
        list.add(channel(CH_CHAT, "Сообщения", "Новые сообщения в личных и групповых чатах", NotificationManager.IMPORTANCE_HIGH));
        list.add(channel(CH_GROUP, "Группы", "Вас добавили в группу", NotificationManager.IMPORTANCE_HIGH));
        list.add(channel(CH_NOTIFY, "Уведомления", "Уведомления разделов: задачи, проекты, оборудование", NotificationManager.IMPORTANCE_DEFAULT));
        list.add(channel(CH_FILES, "Загрузки", "Скачанные файлы", NotificationManager.IMPORTANCE_LOW));
        NotificationChannel transfer = channel(CH_TRANSFER, "Отправка файлов", "Ход отправки файлов в фоне", NotificationManager.IMPORTANCE_LOW);
        transfer.setShowBadge(false);
        list.add(transfer);
        NotificationChannel service = channel(CH_SERVICE, "Связь с сервером", "Постоянное соединение для уведомлений на телефонах без сервисов Google", NotificationManager.IMPORTANCE_MIN);
        service.setShowBadge(false);
        list.add(service);
        nm.createNotificationChannels(list);
    }

    @TargetApi(26)
    private static NotificationChannel channel(String id, String name, String description, int importance) {
        NotificationChannel ch = new NotificationChannel(id, name, importance);
        ch.setDescription(description);
        // на заблокированном экране — без текста сообщения (см. setPublicVersion)
        ch.setLockscreenVisibility(Notification.VISIBILITY_PRIVATE);
        return ch;
    }

    /** Разрешены ли уведомления приложению (Android 13+ спрашивает отдельно) */
    public static boolean enabled(Context c) {
        NotificationManager nm = (NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm == null || !nm.areNotificationsEnabled()) return false;
        return Build.VERSION.SDK_INT < 33 || c.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED;
    }

    static Notification.Builder builder(Context c, String channel) {
        return Build.VERSION.SDK_INT >= 26 ? new Notification.Builder(c, channel) : new Notification.Builder(c);
    }

    public static PendingIntent openIntent(Context c, String url, String tag) {
        Intent i = new Intent(c, MainActivity.class)
                .setAction(MainActivity.ACTION_OPEN)
                .putExtra(MainActivity.EXTRA_URL, url)
                .putExtra(MainActivity.EXTRA_TAG, tag)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        return PendingIntent.getActivity(c, (tag == null ? 0 : tag.hashCode()), i,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    public static boolean show(Context c, String tag, String title, String body, String url, String channel) {
        if (!enabled(c)) return false;
        String ch = CH_CHAT.equals(channel) || CH_GROUP.equals(channel) ? channel : CH_NOTIFY;
        Notification pub = builder(c, ch)
                .setSmallIcon(R.drawable.ic_stat_kovapp)
                .setColor(ACCENT)
                .setContentTitle("Kovapp")
                .setContentText(CH_CHAT.equals(ch) ? "Новое сообщение" : "Новое уведомление")
                .build();
        Notification.Builder b = builder(c, ch)
                .setSmallIcon(R.drawable.ic_stat_kovapp)
                .setColor(ACCENT)
                .setContentTitle(title)
                .setContentText(body)
                .setStyle(new Notification.BigTextStyle().bigText(body))
                .setAutoCancel(true)
                .setOnlyAlertOnce(true)
                .setShowWhen(true)
                .setWhen(System.currentTimeMillis())
                .setCategory(CH_NOTIFY.equals(ch) ? Notification.CATEGORY_EVENT : Notification.CATEGORY_MESSAGE)
                .setVisibility(Notification.VISIBILITY_PRIVATE)
                .setPublicVersion(pub)
                .setContentIntent(openIntent(c, url, tag));
        if (Build.VERSION.SDK_INT < 26) {
            b.setPriority(CH_NOTIFY.equals(ch) ? Notification.PRIORITY_DEFAULT : Notification.PRIORITY_HIGH);
            b.setDefaults(Notification.DEFAULT_ALL);
        }
        NotificationManager nm = (NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE);
        nm.notify(tag, ID, b.build());
        return true;
    }

    public static void cancel(Context c, String tag) {
        NotificationManager nm = (NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm != null) nm.cancel(tag, ID);
    }

    /** При выходе из учётной записи уведомления прежнего пользователя убираются */
    public static void cancelAll(Context c) {
        NotificationManager nm = (NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm != null) nm.cancelAll();
    }

    /** Значок «Kovapp на связи» скрыт: категорию «Связь с сервером» (или все уведомления) выключили */
    public static boolean serviceHidden(Context c) {
        if (!enabled(c)) return true;
        if (Build.VERSION.SDK_INT < 26) return false;
        NotificationManager nm = c.getSystemService(NotificationManager.class);
        NotificationChannel ch = nm == null ? null : nm.getNotificationChannel(CH_SERVICE);
        return ch != null && ch.getImportance() == NotificationManager.IMPORTANCE_NONE;
    }

    /** Уведомление службы постоянного соединения */
    public static Notification service(Context c) {
        return builder(c, CH_SERVICE)
                .setSmallIcon(R.drawable.ic_stat_kovapp)
                .setColor(ACCENT)
                .setContentTitle("Kovapp на связи")
                .setContentText("Получение уведомлений без сервисов Google")
                // Android 13+: значок можно смахнуть, соединение при этом продолжает работать
                .setOngoing(false)
                .setShowWhen(false)
                .setContentIntent(openIntent(c, null, "service"))
                .build();
    }

    /** Файл сохранён в «Загрузки» — нажатие открывает его */
    public static void fileSaved(Context c, String name, PendingIntent open) {
        if (!enabled(c)) return;
        Notification.Builder b = builder(c, CH_FILES)
                .setSmallIcon(R.drawable.ic_stat_kovapp)
                .setColor(ACCENT)
                .setContentTitle("Файл скачан")
                .setContentText(name)
                .setAutoCancel(true);
        if (open != null) b.setContentIntent(open);
        NotificationManager nm = (NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE);
        nm.notify("file-" + name, ID, b.build());
    }
}
