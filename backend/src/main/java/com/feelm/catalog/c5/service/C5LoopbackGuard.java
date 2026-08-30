package com.feelm.catalog.c5.service;

import com.feelm.catalog.api.ApiException;
import jakarta.annotation.PostConstruct;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Profile;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;

import java.net.InetAddress;
import java.net.URI;

@Component
@Profile("local")
@ConditionalOnProperty(name = "c5.local.enabled", havingValue = "true")
public final class C5LoopbackGuard {
    private final String serverAddress;

    public C5LoopbackGuard(@Value("${server.address:127.0.0.1}") String serverAddress) {
        this.serverAddress = serverAddress;
    }

    @PostConstruct
    public void validateBind() {
        if (!isLoopback(serverAddress)) {
            throw new IllegalStateException("C5 local capability requires an explicit loopback server.address");
        }
    }

    public void requireLocal(String remoteAddress, String origin) {
        if (!isLoopback(remoteAddress) || (origin != null && !origin.isBlank() && !loopbackOrigin(origin))) {
            throw new ApiException(HttpStatus.NOT_FOUND, "RESOURCE_NOT_FOUND", "요청한 정보를 찾을 수 없어요.");
        }
    }

    private static boolean loopbackOrigin(String origin) {
        try {
            URI uri = URI.create(origin);
            return ("http".equalsIgnoreCase(uri.getScheme()) || "https".equalsIgnoreCase(uri.getScheme()))
                    && uri.getHost() != null && isLoopback(uri.getHost());
        } catch (RuntimeException exception) {
            return false;
        }
    }

    static boolean isLoopback(String value) {
        try {
            return value != null && InetAddress.getByName(value).isLoopbackAddress();
        } catch (Exception exception) {
            return false;
        }
    }
}
