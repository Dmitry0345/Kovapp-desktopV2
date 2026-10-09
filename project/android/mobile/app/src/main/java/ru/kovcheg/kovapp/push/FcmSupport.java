package ru.kovcheg.kovapp.push;

import android.content.Context;

import com.google.android.gms.common.ConnectionResult;
import com.google.android.gms.common.GoogleApiAvailabilityLight;
import com.google.android.gms.tasks.Tasks;
import com.google.firebase.FirebaseApp;
import com.google.firebase.FirebaseOptions;
import com.google.firebase.messaging.FirebaseMessaging;

import java.util.concurrent.TimeUnit;

import ru.kovcheg.kovapp.AppConfig;
import ru.kovcheg.kovapp.BuildConfig;

/**
 * Доставка через Google (FCM). Firebase запускается вручную и только когда
 * пользователь включил уведомления: без этого приложение к Google не
 * обращается. Параметры проекта — из kovapp.properties.
 */
public final class FcmSupport {
    private FcmSupport() {}

    /** Есть ли на телефоне сервисы Google (без учёта настроек Firebase) */
    public static boolean googleServices(Context c) {
        try {
            return GoogleApiAvailabilityLight.getInstance().isGooglePlayServicesAvailable(c) == ConnectionResult.SUCCESS;
        } catch (Throwable t) {
            return false;
        }
    }

    public static boolean available(Context c) {
        if (!AppConfig.fcmConfigured()) return false;
        try {
            // Light — из play-services-basement: он приходит вместе с firebase-messaging
            return GoogleApiAvailabilityLight.getInstance().isGooglePlayServicesAvailable(c) == ConnectionResult.SUCCESS;
        } catch (Throwable t) {
            return false;
        }
    }

    static synchronized void init(Context c) {
        if (!FirebaseApp.getApps(c).isEmpty()) return;
        FirebaseOptions options = new FirebaseOptions.Builder()
                .setProjectId(BuildConfig.FCM_PROJECT_ID)
                .setApplicationId(BuildConfig.FCM_APP_ID)
                .setApiKey(BuildConfig.FCM_API_KEY)
                .setGcmSenderId(BuildConfig.FCM_SENDER_ID)
                .build();
        FirebaseApp.initializeApp(c, options);
    }

    /** Блокирующий вызов: только из фонового потока */
    public static String token(Context c, long timeoutMs) throws Exception {
        init(c);
        return Tasks.await(FirebaseMessaging.getInstance().getToken(), timeoutMs, TimeUnit.MILLISECONDS);
    }

    public static void deleteToken(Context c) {
        if (!available(c)) return;
        init(c);
        FirebaseMessaging.getInstance().deleteToken();
    }
}
