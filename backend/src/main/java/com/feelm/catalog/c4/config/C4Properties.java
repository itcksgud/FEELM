package com.feelm.catalog.c4.config;

import jakarta.annotation.PostConstruct;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.net.URI;
import java.security.SecureRandom;
import java.util.Base64;

@Component
public final class C4Properties {
    private final boolean enabled;
    private final boolean localProfile;
    private final String allowedOrigin;
    private byte[] deliveryKey;
    private final String deliveryKeyVersion;
    private final boolean mailEnabled;
    private final String mailHost;
    private final int mailPort;
    private final String mailFrom;

    public C4Properties(
            @Value("${catalog.c4.enabled:false}") boolean enabled,
            @Value("${catalog.c4.local-profile:false}") boolean localProfile,
            @Value("${catalog.c4.allowed-origin:}") String allowedOrigin,
            @Value("${catalog.c4.delivery-key-base64:}") String deliveryKeyBase64,
            @Value("${catalog.c4.delivery-key-version:local-v1}") String deliveryKeyVersion,
            @Value("${catalog.c4.mail.enabled:false}") boolean mailEnabled,
            @Value("${catalog.c4.mail.host:}") String mailHost,
            @Value("${catalog.c4.mail.port:1025}") int mailPort,
            @Value("${catalog.c4.mail.from:no-reply@feelm.test}") String mailFrom
    ) {
        this.enabled = enabled;
        this.localProfile = localProfile;
        this.allowedOrigin = allowedOrigin == null ? "" : allowedOrigin.strip();
        String configuredKey = deliveryKeyBase64 == null ? "" : deliveryKeyBase64.strip();
        if (!configuredKey.isBlank()) {
            try {
                this.deliveryKey = Base64.getDecoder().decode(configuredKey);
            } catch (IllegalArgumentException exception) {
                throw new IllegalStateException("C4 delivery key must be base64", exception);
            }
        }
        this.deliveryKeyVersion = deliveryKeyVersion;
        this.mailEnabled = mailEnabled;
        this.mailHost = mailHost == null ? "" : mailHost.strip();
        this.mailPort = mailPort;
        this.mailFrom = mailFrom;
    }

    @PostConstruct
    void validate() {
        if (!enabled) return;
        if (!localProfile) {
            throw new IllegalStateException("C4 production activation is not authorized");
        }
        URI origin;
        try {
            origin = URI.create(allowedOrigin);
        } catch (RuntimeException exception) {
            throw new IllegalStateException("C4_ALLOWED_ORIGIN must be an exact origin", exception);
        }
        boolean loopback = "http".equals(origin.getScheme())
                && ("127.0.0.1".equals(origin.getHost()) || "localhost".equalsIgnoreCase(origin.getHost()))
                && origin.getRawPath().isEmpty() && origin.getRawQuery() == null && origin.getRawFragment() == null;
        if (!loopback) throw new IllegalStateException("C4 local origin must be an exact loopback HTTP origin");
        if (deliveryKey == null && localProfile) {
            deliveryKey = new byte[32];
            new SecureRandom().nextBytes(deliveryKey);
        }
        if (deliveryKey == null || deliveryKey.length != 32)
            throw new IllegalStateException("C4 delivery key must decode to 32 bytes");
        if (mailEnabled && mailHost.isBlank()) throw new IllegalStateException("C4 local mail host is required");
        if (mailPort < 1 || mailPort > 65535) throw new IllegalStateException("C4 local mail port is invalid");
    }

    public boolean enabled() { return enabled; }
    public boolean localProfile() { return localProfile; }
    public String allowedOrigin() { return allowedOrigin; }
    public byte[] deliveryKey() { return deliveryKey.clone(); }
    public String deliveryKeyVersion() { return deliveryKeyVersion; }
    public boolean mailEnabled() { return mailEnabled; }
    public String mailHost() { return mailHost; }
    public int mailPort() { return mailPort; }
    public String mailFrom() { return mailFrom; }
}
