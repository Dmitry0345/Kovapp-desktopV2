package ru.kovcheg.kovapp.push;

import com.google.firebase.messaging.FirebaseMessagingService;
import com.google.firebase.messaging.RemoteMessage;

import ru.kovcheg.kovapp.App;

/** Приём уведомлений от Google: данные зашифрованы ключом устройства. */
public final class FcmService extends FirebaseMessagingService {
    @Override public void onMessageReceived(RemoteMessage message) {
        App.get().push().handleFcm(message.getData());
    }

    @Override public void onNewToken(String token) {
        App.get().push().onFcmToken(token);
    }
}
