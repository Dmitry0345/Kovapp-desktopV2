package ru.kovcheg.kovapp.push;

import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.util.Map;

import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;

import ru.kovcheg.kovapp.core.B64;

/**
 * Расшифровка уведомления, пришедшего через Google (FCM). Сервер шифрует
 * текст ключом этого устройства (AES-256-GCM), поэтому Google видит только
 * бессмысленные байты: {"v":"1","n":<nonce>,"c":<шифртекст+тег>}.
 */
public final class PushCrypto {
    private PushCrypto() {}

    public static String open(byte[] key, Map<String, ?> data) throws GeneralSecurityException {
        Object v = data.get("v"), n = data.get("n"), c = data.get("c");
        if (!"1".equals(String.valueOf(v)) || !(n instanceof String) || !(c instanceof String)) {
            throw new GeneralSecurityException("неизвестный формат уведомления");
        }
        return new String(decrypt(key, B64.decode((String) n), B64.decode((String) c)), StandardCharsets.UTF_8);
    }

    public static byte[] decrypt(byte[] key, byte[] nonce, byte[] cipherAndTag) throws GeneralSecurityException {
        if (key == null || key.length != 32) throw new GeneralSecurityException("нет ключа устройства");
        if (nonce.length != 12) throw new GeneralSecurityException("неверная длина nonce");
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, new SecretKeySpec(key, "AES"), new GCMParameterSpec(128, nonce));
        return cipher.doFinal(cipherAndTag);
    }
}
