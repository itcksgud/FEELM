package com.feelm.catalog.security;

import com.feelm.catalog.api.ApiException;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.util.Set;
import java.util.List;
import java.util.UUID;

@Component
public final class CatalogUserContextResolver {
    public static final UUID C1_OWNER = UUID.fromString("018f6826-4da1-7c38-a846-8f794cd8b0cf");
    public static final UUID C1_OTHER = UUID.fromString("5f93a51d-a6f1-41dc-8d86-6b570d53bd82");
    private static final String C0_FIXTURE_TOKEN = "test-valid-subscribed-token";
    private static final String C1_OWNER_TOKEN = "test-c1-owner-token";
    private static final String C1_OTHER_TOKEN = "test-c1-other-token";
    private static final UUID NETFLIX = UUID.fromString("d392a4d5-0428-4e06-aa41-aef899c06842");
    private final String authMode;
    private final List<C4AccessTokenVerifier> c4Verifiers;

    public CatalogUserContextResolver(
            @Value("${catalog.auth-mode:fake}") String authMode,
            List<C4AccessTokenVerifier> c4Verifiers
    ) {
        this.authMode = authMode;
        this.c4Verifiers = List.copyOf(c4Verifiers);
    }

    public CatalogUserContext resolve(String authorization) {
        if (authorization == null || authorization.isBlank()) {
            return CatalogUserContext.anonymous();
        }
        if (!authorization.startsWith("Bearer ")) {
            throw ApiException.invalidToken();
        }
        if (!"fake".equalsIgnoreCase(authMode)) {
            throw ApiException.invalidToken();
        }
        String token = authorization.substring("Bearer ".length()).trim();
        if (C0_FIXTURE_TOKEN.equals(token) || C1_OWNER_TOKEN.equals(token)) {
            return new CatalogUserContext(true, C1_OWNER, Set.of(NETFLIX));
        }
        if (C1_OTHER_TOKEN.equals(token)) {
            return new CatalogUserContext(true, C1_OTHER, Set.of());
        }
        for (C4AccessTokenVerifier verifier : c4Verifiers) {
            var verified = verifier.verify(token);
            if (verified.isPresent()) {
                return verified.get();
            }
        }
        throw ApiException.invalidToken();
    }

    public CatalogUserContext resolveRequired(String authorization) {
        if (authorization == null || authorization.isBlank()) {
            throw ApiException.unauthorized();
        }
        try {
            CatalogUserContext context = resolve(authorization);
            if (!context.authenticated() || context.actorUserId() == null) {
                throw ApiException.unauthorized();
            }
            return context;
        } catch (ApiException exception) {
            throw ApiException.unauthorized();
        }
    }
}
