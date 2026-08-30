package com.feelm.catalog.security;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.feelm.catalog.api.CatalogApiDtos;
import com.feelm.catalog.api.ApiException;
import com.feelm.catalog.api.TraceIdFilter;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.List;
import java.util.Set;

@Component
@Order(Ordered.HIGHEST_PRECEDENCE + 10)
public final class C1RequiredAuthFilter extends OncePerRequestFilter {
    public static final String ACTOR_ATTRIBUTE = C1RequiredAuthFilter.class.getName() + ".actor";
    private static final Set<String> EXACT_GET_PATHS = Set.of(
            "/api/v1/me/watch-intents/pending-confirmation",
            "/api/v1/me/viewing-records/unrated",
            "/api/v1/me/ratings",
            "/api/v1/me/film",
            "/api/v1/me/popcorn-bucket",
            "/api/v1/me/taste-profile",
            "/api/v1/me/recommendations/personal-discovery",
            "/api/v1/me/recommendation-interpretation-experiment"
    );

    private final CatalogUserContextResolver resolver;
    private final ObjectMapper objectMapper;

    public C1RequiredAuthFilter(CatalogUserContextResolver resolver, ObjectMapper objectMapper) {
        this.resolver = resolver;
        this.objectMapper = objectMapper;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        String method = request.getMethod();
        String path = request.getRequestURI();
        if ("POST".equals(method) && "/api/v1/watch-intents".equals(path)) {
            return false;
        }
        if ("POST".equals(method) && path.matches("^/api/v1/watch-intents/[^/]+/confirmation$")) {
            return false;
        }
        if ("GET".equals(method) && EXACT_GET_PATHS.contains(path)) {
            return false;
        }
        if ("GET".equals(method) && path.matches("^/api/v1/me/film/frames/[^/]+$")) {
            return false;
        }
        if ("POST".equals(method)
                && (path.matches("^/api/v1/me/recommendation-deliveries/[^/]+/append$")
                || path.matches("^/api/v1/me/recommendation-delivery-items/[^/]+/dismissals$"))) {
            return false;
        }
        return !(Set.of("PUT", "DELETE").contains(method)
                && path.matches("^/api/v1/me/ratings/[^/]+$"));
    }

    @Override
    protected void doFilterInternal(
            HttpServletRequest request,
            HttpServletResponse response,
            FilterChain filterChain
    ) throws ServletException, IOException {
        CatalogUserContext actor;
        try {
            actor = resolver.resolveRequired(request.getHeader("Authorization"));
        } catch (ApiException exception) {
            Object trace = request.getAttribute(TraceIdFilter.TRACE_ID_ATTRIBUTE);
            response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
            response.setContentType(MediaType.APPLICATION_JSON_VALUE);
            objectMapper.writeValue(response.getOutputStream(), new CatalogApiDtos.ErrorResponse(
                    "UNAUTHORIZED",
                    "로그인이 필요해요.",
                    trace == null ? "unavailable" : trace.toString(),
                    List.of()
            ));
            return;
        }
        request.setAttribute(ACTOR_ATTRIBUTE, actor);
        filterChain.doFilter(request, response);
    }
}
