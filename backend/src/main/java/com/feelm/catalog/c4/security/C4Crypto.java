package com.feelm.catalog.c4.security;

import com.feelm.catalog.c4.config.C4Properties;
import org.bouncycastle.crypto.generators.Argon2BytesGenerator;
import org.bouncycastle.crypto.params.Argon2Parameters;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

import javax.crypto.Cipher;
import javax.crypto.Mac;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Base64;
import java.util.HexFormat;

@Component
@ConditionalOnProperty(name = "catalog.c4.enabled", havingValue = "true")
public final class C4Crypto {
    private static final int MEMORY_KIB = 19_456;
    private static final int ITERATIONS = 2;
    private static final int PARALLELISM = 1;
    private final SecureRandom random = new SecureRandom();
    private final C4Properties properties;

    public C4Crypto(C4Properties properties) { this.properties = properties; }

    public String randomToken() {
        byte[] value = new byte[32];
        random.nextBytes(value);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(value);
    }

    public String passwordHash(String password) {
        byte[] salt = new byte[16];
        random.nextBytes(salt);
        byte[] hash = argon(password, salt);
        return "$argon2id$v=19$m=" + MEMORY_KIB + ",t=" + ITERATIONS + ",p=" + PARALLELISM
                + "$" + Base64.getEncoder().withoutPadding().encodeToString(salt)
                + "$" + Base64.getEncoder().withoutPadding().encodeToString(hash);
    }

    public boolean passwordMatches(String password, String phc) {
        try {
            String[] parts = phc.split("\\$");
            byte[] salt = Base64.getDecoder().decode(parts[4]);
            byte[] expected = Base64.getDecoder().decode(parts[5]);
            return MessageDigest.isEqual(expected, argon(password, salt));
        } catch (RuntimeException exception) {
            return false;
        }
    }

    private byte[] argon(String password, byte[] salt) {
        Argon2Parameters parameters = new Argon2Parameters.Builder(Argon2Parameters.ARGON2_id)
                .withVersion(Argon2Parameters.ARGON2_VERSION_13)
                .withMemoryAsKB(MEMORY_KIB)
                .withIterations(ITERATIONS)
                .withParallelism(PARALLELISM)
                .withSalt(salt)
                .build();
        Argon2BytesGenerator generator = new Argon2BytesGenerator();
        generator.init(parameters);
        byte[] result = new byte[32];
        generator.generateBytes(password.toCharArray(), result);
        return result;
    }

    public String sha256(String value) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (Exception exception) {
            throw new IllegalStateException(exception);
        }
    }

    public String hmac(String value) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(properties.deliveryKey(), "HmacSHA256"));
            return HexFormat.of().formatHex(mac.doFinal(value.getBytes(StandardCharsets.UTF_8)));
        } catch (Exception exception) {
            throw new IllegalStateException(exception);
        }
    }

    public Encrypted encrypt(String value, byte[] aad) {
        try {
            byte[] nonce = new byte[12];
            random.nextBytes(nonce);
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, new SecretKeySpec(properties.deliveryKey(), "AES"), new GCMParameterSpec(128, nonce));
            cipher.updateAAD(aad);
            return new Encrypted(cipher.doFinal(value.getBytes(StandardCharsets.UTF_8)), nonce);
        } catch (Exception exception) {
            throw new IllegalStateException(exception);
        }
    }

    public String decrypt(byte[] ciphertext, byte[] nonce, byte[] aad) {
        try {
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, new SecretKeySpec(properties.deliveryKey(), "AES"), new GCMParameterSpec(128, nonce));
            cipher.updateAAD(aad);
            return new String(cipher.doFinal(ciphertext), StandardCharsets.UTF_8);
        } catch (Exception exception) {
            throw new IllegalStateException("C4 delivery material cannot be decrypted", exception);
        }
    }

    public record Encrypted(byte[] ciphertext, byte[] nonce) {}
}
