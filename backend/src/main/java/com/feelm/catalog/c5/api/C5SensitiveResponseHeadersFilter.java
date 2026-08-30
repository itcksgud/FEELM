package com.feelm.catalog.c5.api;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.List;

@Component
@Profile("local")
@ConditionalOnProperty(name = "c5.local.enabled", havingValue = "true")
public final class C5SensitiveResponseHeadersFilter extends OncePerRequestFilter {
    private static final List<String> C5_PATH_PREFIXES = List.of(
            "/api/v1/me/taste-reports",
            "/api/v1/me/report-exports",
            "/api/v1/me/privacy-settings",
            "/api/v1/me/report-shares",
            "/api/v1/public/profiles",
            "/api/v1/public/report-shares",
            "/api/v1/public/shared-report",
            "/api/v1/me/notification-settings",
            "/api/v1/me/notifications"
    );

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        String path = request.getRequestURI();
        return C5_PATH_PREFIXES.stream().noneMatch(path::startsWith);
    }

    @Override
    protected void doFilterInternal(
            HttpServletRequest request,
            HttpServletResponse response,
            FilterChain filterChain
    ) throws ServletException, IOException {
        // Set before the chain so validation, authentication and not-found errors are protected too.
        response.setHeader("Cache-Control", "no-store, private");
        response.setHeader("Referrer-Policy", "no-referrer");
        filterChain.doFilter(request, response);
    }
}
