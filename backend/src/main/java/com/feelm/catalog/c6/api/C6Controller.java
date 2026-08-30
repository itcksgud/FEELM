package com.feelm.catalog.c6.api;

import com.feelm.catalog.c6.service.C6InterpretationService;
import com.feelm.catalog.c6.service.C6LoopbackGuard;
import com.feelm.catalog.security.C1RequiredAuthFilter;
import com.feelm.catalog.security.CatalogUserContext;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Profile;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@Profile("local")
@ConditionalOnProperty(name = "catalog.c6.local.enabled", havingValue = "true")
public final class C6Controller {
    private final C6InterpretationService service;
    private final C6LoopbackGuard loopback;

    public C6Controller(C6InterpretationService service, C6LoopbackGuard loopback) {
        this.service = service;
        this.loopback = loopback;
    }

    @GetMapping("/api/v1/me/recommendation-interpretation-experiment")
    public ResponseEntity<C6ApiDtos.RecommendationInterpretationExperiment> experiment(
            HttpServletRequest request
    ) {
        loopback.requireLocal(request.getRemoteAddr(), request.getHeader("Origin"));
        CatalogUserContext actor = (CatalogUserContext) request.getAttribute(C1RequiredAuthFilter.ACTOR_ATTRIBUTE);
        if (actor == null || !actor.authenticated() || actor.actorUserId() == null) {
            throw com.feelm.catalog.api.ApiException.unauthorized();
        }
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore().cachePrivate())
                .header("Referrer-Policy", "no-referrer")
                .body(service.run(actor.actorUserId()));
    }
}
