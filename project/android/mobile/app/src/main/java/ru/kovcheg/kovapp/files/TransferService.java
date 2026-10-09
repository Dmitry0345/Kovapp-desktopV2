package ru.kovcheg.kovapp.files;

import android.app.Notification;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;

import ru.kovcheg.kovapp.MainActivity;
import ru.kovcheg.kovapp.R;
import ru.kovcheg.kovapp.notify.Notifier;

/**
 * Отправка файлов в фоне: пока она идёт, Android не усыпляет процесс;
 * ход и «Отменить» — в шторке уведомлений. Запускается при первой отправке
 * (приложение на экране), дальше обновляется только уведомление.
 */
public class TransferService extends Service {
    static final int ID = 7301;
    static final String ACTION_SHOW = "ru.kovcheg.kovapp.TRANSFER_SHOW";
    static final String ACTION_CANCEL = "ru.kovcheg.kovapp.TRANSFER_CANCEL";
    private static volatile boolean running, active, quiet, shown = true;
    private static volatile int count = 1, percent;
    /** «Отменить» в шторке — страница останавливает отправку (ставит NativeCommands) */
    public static volatile Runnable onCancel;

    /** quiet — отправка в чат, пока открыт «Мессенджер»: уведомления нет, пока приложение на экране */
    public static void update(Context ctx, int files, int pct, boolean q) {
        count = Math.max(1, files);
        percent = Math.max(0, Math.min(100, pct));
        active = true; quiet = q;
        Context app = ctx.getApplicationContext();
        if (quiet && shown) { if (running) app.stopService(new Intent(app, TransferService.class)); return; }
        if (running) { show(app, build(app, true)); return; }
        start(app);
    }

    /** Приложение ушло с экрана (onPause — запуск ещё разрешён) или вернулось на него */
    public static void appShown(Context ctx, boolean on) {
        shown = on;
        if (!active) return;
        Context app = ctx.getApplicationContext();
        if (!on && !running) start(app);
        else if (on && quiet && running) app.stopService(new Intent(app, TransferService.class));
    }

    private static void start(Context app) {
        Intent i = new Intent(app, TransferService.class).setAction(ACTION_SHOW);
        try {
            if (Build.VERSION.SDK_INT >= 26) app.startForegroundService(i); else app.startService(i);
        } catch (Exception e) {
            // из фона Android 12+ запуск не разрешит — отправка идёт и без службы
        }
    }

    public static void end(Context ctx) {
        active = false; quiet = false;
        if (!running) return;
        Context app = ctx.getApplicationContext();
        app.stopService(new Intent(app, TransferService.class));
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        String a = intent == null ? null : intent.getAction();
        if (ACTION_CANCEL.equals(a)) {
            Runnable r = onCancel;
            if (r != null) r.run();
            if (!running) stopSelf();
            return START_NOT_STICKY;
        }
        Notification n = build(this, true);
        if (Build.VERSION.SDK_INT >= 29) startForeground(ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
        else startForeground(ID, n);
        running = true;
        return START_NOT_STICKY;
    }

    /** Приложение смахнули из недавних: страница закрыта, отправка остановилась — так и пишем */
    @Override public void onTaskRemoved(Intent rootIntent) {
        if (!running) return;
        running = false;
        stopForeground(true);
        show(this, build(this, false));
        stopSelf();
    }

    @Override public void onDestroy() {
        if (running) { running = false; stopForeground(true); }
        super.onDestroy();
    }

    @Override public IBinder onBind(Intent intent) { return null; }

    static Notification build(Context c, boolean active) {
        Notification.Builder b = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(c, Notifier.CH_TRANSFER) : new Notification.Builder(c);
        int fl = PendingIntent.FLAG_UPDATE_CURRENT | (Build.VERSION.SDK_INT >= 23 ? PendingIntent.FLAG_IMMUTABLE : 0);
        Intent open = new Intent(c, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        b.setSmallIcon(R.drawable.ic_stat_kovapp)
                .setContentIntent(PendingIntent.getActivity(c, ID, open, fl))
                .setOnlyAlertOnce(true);
        if (active) {
            Intent cancel = new Intent(c, TransferService.class).setAction(ACTION_CANCEL);
            b.setContentTitle(TransferText.title(count))
                    .setContentText(TransferText.text(percent))
                    .setProgress(100, percent, percent <= 0)
                    .setOngoing(true)
                    .addAction(0, "Отменить", PendingIntent.getService(c, ID + 1, cancel, fl));
        } else {
            b.setContentTitle("Отправка прервана")
                    .setContentText("Приложение закрыто — откройте его и отправьте файлы снова")
                    .setAutoCancel(true);
        }
        return b.build();
    }

    private static void show(Context c, Notification n) {
        NotificationManager nm = (NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm != null) nm.notify(ID, n);
    }
}
