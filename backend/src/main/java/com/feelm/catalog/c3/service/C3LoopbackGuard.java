package com.feelm.catalog.c3.service;

import com.feelm.catalog.api.ApiException;
import org.springframework.beans.factory.InitializingBean;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Profile;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;

import java.net.InetAddress;

@Component
@Profile("local")
@ConditionalOnProperty(name = "catalog.c3.enabled", havingValue = "true")
public final class C3LoopbackGuard implements InitializingBean {
    private final String c3BindAddress;
    private final String serverAddress;

    public C3LoopbackGuard(
            @Value("${catalog.c3.local-bind-address:127.0.0.1}") String c3BindAddress,
            @Value("${server.address:}") String serverAddress
    ) {
        this.c3BindAddress = c3BindAddress;
        this.serverAddress = serverAddress;
    }

    @Override
    public void afterPropertiesSet() {
        if (!isLoopback(c3BindAddress)
                || (!serverAddress.isBlank() && !isLoopback(serverAddress))) {
            throw new IllegalStateException("C3 local MVP requires a loopback bind");
        }
    }

    public void requireLoopbackRemote(String remoteAddress) {
        if (!isLoopback(remoteAddress)) {
            throw new ApiException(
                    HttpStatus.UNAUTHORIZED,
                    "LOCAL_ACTOR_UNAUTHORIZED",
                    "로컬 실행 환경에서만 사용할 수 있어요."
            );
        }
    }

    static boolean isLoopback(String value) {
        try {
            return value != null && !value.isBlank() && InetAddress.getByName(value).isLoopbackAddress();
        } catch (Exception ignored) {
            return false;
        }
    }
}
