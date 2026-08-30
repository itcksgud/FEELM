package com.feelm.catalog.c2b.api;

import com.fasterxml.jackson.databind.JsonNode;
import com.feelm.catalog.c2b.service.PersonalDiscoveryService;
import com.feelm.catalog.security.C1RequiredAuthFilter;
import com.feelm.catalog.security.CatalogUserContext;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import org.springframework.context.annotation.Profile;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;

import java.util.UUID;

import static com.feelm.catalog.c2b.api.C2BApiDtos.*;

@Validated
@RestController
@Profile({"postgres", "local"})
public class C2BController {
    private final PersonalDiscoveryService service;

    public C2BController(PersonalDiscoveryService service) {
        this.service = service;
    }

    @GetMapping("/api/v1/me/recommendations/personal-discovery")
    public ResponseEntity<RecommendationDelivery> get(HttpServletRequest request) {
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore().cachePrivate())
                .body(service.getOrCreate(actor(request)));
    }

    @PostMapping("/api/v1/me/recommendation-deliveries/{deliveryId}/append")
    public ResponseEntity<JsonNode> append(
            @PathVariable UUID deliveryId,
            @RequestHeader("Idempotency-Key")
            @Size(min = 8, max = 128) @Pattern(regexp = "^[!-~]+$") String idempotencyKey,
            @Valid @RequestBody AppendRecommendationsRequest body,
            HttpServletRequest request
    ) {
        PersonalDiscoveryService.HttpMutation result = service.append(
                actor(request), deliveryId, idempotencyKey, body
        );
        return ResponseEntity.status(result.status())
                .cacheControl(CacheControl.noStore().cachePrivate())
                .body(result.body());
    }

    @PostMapping("/api/v1/me/recommendation-delivery-items/{deliveryItemId}/dismissals")
    public ResponseEntity<JsonNode> dismiss(
            @PathVariable UUID deliveryItemId,
            @RequestHeader("Idempotency-Key")
            @Size(min = 8, max = 128) @Pattern(regexp = "^[!-~]+$") String idempotencyKey,
            @Valid @RequestBody DismissRecommendationRequest body,
            HttpServletRequest request
    ) {
        PersonalDiscoveryService.HttpMutation result = service.dismiss(
                actor(request), deliveryItemId, idempotencyKey, body
        );
        return ResponseEntity.status(result.status())
                .cacheControl(CacheControl.noStore().cachePrivate())
                .body(result.body());
    }

    private static UUID actor(HttpServletRequest request) {
        CatalogUserContext actor = (CatalogUserContext) request.getAttribute(C1RequiredAuthFilter.ACTOR_ATTRIBUTE);
        if (actor == null || actor.actorUserId() == null) {
            throw new IllegalStateException("required recommendation actor is missing");
        }
        return actor.actorUserId();
    }
}
