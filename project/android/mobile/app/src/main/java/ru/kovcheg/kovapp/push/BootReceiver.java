package ru.kovcheg.kovapp.push;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

import ru.kovcheg.kovapp.App;

/** После перезагрузки телефона или обновления приложения — вернуть собственный канал. */
public final class BootReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context context, Intent intent) {
        String a = intent == null ? null : intent.getAction();
        if (!Intent.ACTION_BOOT_COMPLETED.equals(a) && !Intent.ACTION_MY_PACKAGE_REPLACED.equals(a)) return;
        App.get().push().onAppStart();
    }
}
