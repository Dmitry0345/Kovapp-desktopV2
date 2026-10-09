package ru.kovcheg.kovapp.push;

import android.app.job.JobInfo;
import android.app.job.JobParameters;
import android.app.job.JobScheduler;
import android.app.job.JobService;
import android.content.ComponentName;
import android.content.Context;
import android.util.Log;

import ru.kovcheg.kovapp.App;

/**
 * Раз в 15 минут (когда разрешит система): если служба собственного канала
 * была остановлена прошивкой, пробуем поднять её, а если нельзя — коротким
 * подключением забираем накопившиеся на сервере уведомления.
 */
public final class CatchUpJob extends JobService {
    static final int JOB_ID = 4242;

    public static void schedule(Context c) {
        JobScheduler js = (JobScheduler) c.getSystemService(Context.JOB_SCHEDULER_SERVICE);
        if (js == null) return;
        JobInfo job = new JobInfo.Builder(JOB_ID, new ComponentName(c, CatchUpJob.class))
                .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
                .setPeriodic(15 * 60 * 1000L)
                .setPersisted(true)
                .build();
        js.schedule(job);
    }

    public static void cancel(Context c) {
        JobScheduler js = (JobScheduler) c.getSystemService(Context.JOB_SCHEDULER_SERVICE);
        if (js != null) js.cancel(JOB_ID);
    }

    @Override public boolean onStartJob(final JobParameters params) {
        new Thread(() -> {
            try {
                run();
            } finally {
                jobFinished(params, false);
            }
        }, "kovapp-catchup").start();
        return true;
    }

    @Override public boolean onStopJob(JobParameters params) { return true; }

    private void run() {
        PushManager pm = App.get().push();
        DeviceStore.Creds c = pm.store().creds();
        if (c == null || !"direct".equals(c.mode) || !pm.store().enabled()) return;
        try {
            DirectPushService.start(this);
            return;
        } catch (Exception e) {
            Log.i("KovappDirect", "служба не поднимается из фона, забираем накопленное");
        }
        try {
            DirectPushService.session(c, true, pm, null);
        } catch (Exception e) {
            Log.i("KovappDirect", "досылка не удалась: " + e.getMessage());
        }
    }
}
