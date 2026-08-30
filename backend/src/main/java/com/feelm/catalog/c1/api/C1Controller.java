package com.feelm.catalog.c1.api;

import com.fasterxml.jackson.databind.JsonNode;
import com.feelm.catalog.api.TraceIdFilter;
import com.feelm.catalog.c1.service.C1Service;
import com.feelm.catalog.security.C1RequiredAuthFilter;
import com.feelm.catalog.security.CatalogUserContext;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import org.springframework.context.annotation.Profile;
import org.springframework.http.ResponseEntity;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.UUID;

import static com.feelm.catalog.c1.api.C1ApiDtos.*;

@Validated
@RestController
@Profile({"postgres", "local"})
public class C1Controller {
    private final C1Service service;

    public C1Controller(C1Service service) {
        this.service = service;
    }

    @PostMapping("/api/v1/watch-intents")
    public ResponseEntity<JsonNode> createWatchIntent(
            @RequestHeader("Idempotency-Key")
            @Size(min = 8, max = 128) @Pattern(regexp = "^[!-~]+$") String idempotencyKey,
            @Valid @RequestBody CreateWatchIntentRequest body,
            HttpServletRequest request
    ) {
        C1Service.HttpMutation result = service.createWatchIntent(actor(request), idempotencyKey, body, trace(request));
        return ResponseEntity.status(result.status()).body(result.body());
    }

    @GetMapping("/api/v1/me/watch-intents/pending-confirmation")
    public CursorPage<PendingWatchConfirmation> pending(
            @RequestParam(required = false) @Size(max = 2048) String cursor,
            @RequestParam(defaultValue = "20") @Min(1) @Max(50) int limit,
            HttpServletRequest request
    ) {
        return service.pending(actor(request), cursor, limit);
    }

    @PostMapping("/api/v1/watch-intents/{watchIntentId}/confirmation")
    public ResponseEntity<JsonNode> confirm(
            @PathVariable UUID watchIntentId,
            @RequestHeader("Idempotency-Key")
            @Size(min = 8, max = 128) @Pattern(regexp = "^[!-~]+$") String idempotencyKey,
            @Valid @RequestBody ConfirmWatchIntentRequest body,
            HttpServletRequest request
    ) {
        C1Service.HttpMutation result = service.confirm(
                actor(request), watchIntentId, idempotencyKey, body, trace(request)
        );
        return ResponseEntity.status(result.status()).body(result.body());
    }

    @GetMapping("/api/v1/me/viewing-records/unrated")
    public CursorPage<UnratedViewingRecord> unrated(
            @RequestParam(required = false) @Size(max = 2048) String cursor,
            @RequestParam(defaultValue = "20") @Min(1) @Max(50) int limit,
            HttpServletRequest request
    ) {
        return service.unrated(actor(request), cursor, limit);
    }

    @GetMapping("/api/v1/me/ratings")
    public CursorPage<RatingItem> ratings(
            @RequestParam(required = false) @Size(max = 2048) String cursor,
            @RequestParam(defaultValue = "20") @Min(1) @Max(50) int limit,
            HttpServletRequest request
    ) {
        return service.ratings(actor(request), cursor, limit);
    }

    @PutMapping("/api/v1/me/ratings/{movieId}")
    public ResponseEntity<JsonNode> putRating(
            @PathVariable UUID movieId,
            @RequestHeader("Idempotency-Key")
            @Size(min = 8, max = 128) @Pattern(regexp = "^[!-~]+$") String idempotencyKey,
            @Valid @RequestBody PutRatingRequest body,
            HttpServletRequest request
    ) {
        C1Service.HttpMutation result = service.putRating(actor(request), movieId, idempotencyKey, body, trace(request));
        return ResponseEntity.status(result.status()).body(result.body());
    }

    @DeleteMapping("/api/v1/me/ratings/{movieId}")
    public ResponseEntity<JsonNode> deleteRating(
            @PathVariable UUID movieId,
            @RequestHeader("Idempotency-Key")
            @Size(min = 8, max = 128) @Pattern(regexp = "^[!-~]+$") String idempotencyKey,
            @RequestHeader("X-Expected-Revision") @Min(1) int expectedRevision,
            HttpServletRequest request
    ) {
        C1Service.HttpMutation result = service.deleteRating(
                actor(request), movieId, idempotencyKey, expectedRevision, trace(request)
        );
        return ResponseEntity.status(result.status()).body(result.body());
    }

    @GetMapping("/api/v1/me/film")
    public FilmPage film(
            @RequestParam(required = false) @Size(max = 2048) String cursor,
            @RequestParam(defaultValue = "20") @Min(1) @Max(50) int limit,
            HttpServletRequest request
    ) {
        return service.film(actor(request), cursor, limit);
    }

    @GetMapping("/api/v1/me/film/frames/{frameId}")
    public FrameDetail frame(@PathVariable UUID frameId, HttpServletRequest request) {
        return service.frame(actor(request), frameId);
    }

    @GetMapping("/api/v1/me/popcorn-bucket")
    public PopcornBucket popcornBucket(HttpServletRequest request) {
        return service.popcornBucket(actor(request));
    }

    @GetMapping("/api/v1/me/taste-profile")
    public TasteProfile tasteProfile(HttpServletRequest request) {
        return service.tasteProfile(actor(request));
    }

    private static UUID actor(HttpServletRequest request) {
        CatalogUserContext actor = (CatalogUserContext) request.getAttribute(C1RequiredAuthFilter.ACTOR_ATTRIBUTE);
        if (actor == null || actor.actorUserId() == null) {
            throw new IllegalStateException("required C1 actor is missing");
        }
        return actor.actorUserId();
    }

    private static String trace(HttpServletRequest request) {
        Object trace = request.getAttribute(TraceIdFilter.TRACE_ID_ATTRIBUTE);
        return trace == null ? "unavailable" : trace.toString();
    }
}
