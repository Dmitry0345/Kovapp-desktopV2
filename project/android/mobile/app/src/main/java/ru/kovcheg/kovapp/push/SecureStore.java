package ru.kovcheg.kovapp.push;

import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;

import java.nio.charset.StandardCharsets;
import java.security.KeyStore;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

import ru.kovcheg.kovapp.core.B64;

/**
 * Шифрование секретов устройства ключом из хранилища Android Keystore:
 * сам ключ не покидает защищённое хранилище телефона, в файлах приложения
 * лежит только шифртекст. Если хранилище на устройстве сломано (бывает у
 * старых прошивок), значение остаётся в закрытой папке приложения.
 */
final class SecureStore {
    private SecureStore() {}

    private static final String ALIAS = "kovapp.device.v1";

    private static SecretKey key() throws Exception {
        KeyStore ks = KeyStore.getInstance("AndroidKeyStore");
        ks.load(null);
        if (ks.containsAlias(ALIAS)) {
            KeyStore.Entry e = ks.getEntry(ALIAS, null);
            if (e instanceof KeyStore.SecretKeyEntry) return ((KeyStore.SecretKeyEntry) e).getSecretKey();
            ks.deleteEntry(ALIAS);
        }
        KeyGenerator kg = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
        kg.init(new KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build());
        return kg.generateKey();
    }

    static String seal(String plain) {
        if (plain == null) return null;
        try {
            Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
            c.init(Cipher.ENCRYPT_MODE, key());
            byte[] ct = c.doFinal(plain.getBytes(StandardCharsets.UTF_8));
            return "k1:" + B64.encode(c.getIV()) + ":" + B64.encode(ct);
        } catch (Exception e) {
            return "p1:" + B64.encode(plain.getBytes(StandardCharsets.UTF_8));
        }
    }

    static String open(String sealed) {
        if (sealed == null) return null;
        try {
            if (sealed.startsWith("p1:")) return new String(B64.decode(sealed.substring(3)), StandardCharsets.UTF_8);
            if (!sealed.startsWith("k1:")) return null;
            String[] p = sealed.split(":");
            Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
            c.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, B64.decode(p[1])));
            return new String(c.doFinal(B64.decode(p[2])), StandardCharsets.UTF_8);
        } catch (Exception e) {
            return null;
        }
    }
}
