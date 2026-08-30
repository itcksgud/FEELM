package com.feelm.catalog.c3.api;

import com.fasterxml.jackson.databind.JsonNode;
import com.feelm.catalog.c3.service.C3LocalService;
import com.feelm.catalog.c3.service.C3LoopbackGuard;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import org.springframework.context.annotation.Profile;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.UUID;

import static com.feelm.catalog.c3.api.C3ApiDtos.*;

@Validated
@RestController
@Profile("local")
@ConditionalOnProperty(name = "catalog.c3.enabled", havingValue = "true")
public class C3Controller {
    private final C3LocalService service;
    private final C3LoopbackGuard loopback;

    public C3Controller(C3LocalService service, C3LoopbackGuard loopback) {
        this.service = service;
        this.loopback = loopback;
    }

    @PostMapping("/api/v1/me/ott-catalog-comparisons")
    public ResponseEntity<JsonNode> createComparison(
            @RequestHeader(value = "X-Local-Actor-Id", required = false) String actorHeader,
            @RequestHeader("Idempotency-Key")
            @Size(min = 8, max = 128) @Pattern(regexp = "^[!-~]+$") String idempotencyKey,
            @Valid @RequestBody CreateOttCatalogComparisonRequest body,
            HttpServletRequest request
    ) {
        C3LocalService.HttpMutation result = service.createComparison(
                actor(actorHeader, request), idempotencyKey, body
        );
        return mutation(result);
    }

    @GetMapping("/api/v1/me/ott-catalog-comparisons/{comparisonId}")
    public ResponseEntity<OttCatalogComparison> getComparison(
            @PathVariable UUID comparisonId,
            @RequestHeader(value = "X-Local-Actor-Id", required = false) String actorHeader,
            HttpServletRequest request
    ) {
        return ok(service.getComparison(actor(actorHeader, request), comparisonId));
    }

    @GetMapping("/api/v1/me/ott-catalog-comparisons/{comparisonId}/movies")
    public ResponseEntity<CatalogMoviePage> listComparisonMovies(
            @PathVariable UUID comparisonId,
            @RequestParam UUID providerId,
            @RequestParam(required = false) @Size(max = 2048) String cursor,
            @RequestParam(defaultValue = "20") @Min(1) @Max(100) int limit,
            @RequestHeader(value = "X-Local-Actor-Id", required = false) String actorHeader,
            HttpServletRequest request
    ) {
        return ok(service.listComparisonMovies(
                actor(actorHeader, request), comparisonId, providerId, cursor, limit
        ));
    }

    @GetMapping("/api/v1/me/parties")
    public ResponseEntity<PartyPage> listMyParties(
            @RequestParam(required = false) @Size(max = 2048) String cursor,
            @RequestParam(defaultValue = "20") @Min(1) @Max(100) int limit,
            @RequestHeader(value = "X-Local-Actor-Id", required = false) String actorHeader,
            HttpServletRequest request
    ) {
        return ok(service.listMyParties(actor(actorHeader, request), cursor, limit));
    }

    @PostMapping("/api/v1/me/parties")
    public ResponseEntity<JsonNode> createParty(
            @RequestHeader(value = "X-Local-Actor-Id", required = false) String actorHeader,
            @RequestHeader("Idempotency-Key")
            @Size(min = 8, max = 128) @Pattern(regexp = "^[!-~]+$") String idempotencyKey,
            @Valid @RequestBody CreatePartyRequest body,
            HttpServletRequest request
    ) {
        return mutation(service.createParty(actor(actorHeader, request), idempotencyKey, body));
    }

    @GetMapping("/api/v1/me/party-invitations")
    public ResponseEntity<PartyInvitationPage> listMyInvitations(
            @RequestParam(required = false) @Size(max = 2048) String cursor,
            @RequestParam(defaultValue = "20") @Min(1) @Max(100) int limit,
            @RequestHeader(value = "X-Local-Actor-Id", required = false) String actorHeader,
            HttpServletRequest request
    ) {
        return ok(service.listMyInvitations(actor(actorHeader, request), cursor, limit));
    }

    @PostMapping("/api/v1/me/party-invitations/{invitationId}/accept")
    public ResponseEntity<JsonNode> acceptInvitation(
            @PathVariable UUID invitationId,
            @RequestHeader(value = "X-Local-Actor-Id", required = false) String actorHeader,
            @RequestHeader("Idempotency-Key")
            @Size(min = 8, max = 128) @Pattern(regexp = "^[!-~]+$") String idempotencyKey,
            @Valid @RequestBody AcceptPartyInvitationRequest body,
            HttpServletRequest request
    ) {
        return mutation(service.acceptInvitation(
                actor(actorHeader, request), invitationId, idempotencyKey, body
        ));
    }

    @GetMapping("/api/v1/parties/{partyId}")
    public ResponseEntity<Party> getParty(
            @PathVariable UUID partyId,
            @RequestHeader(value = "X-Local-Actor-Id", required = false) String actorHeader,
            HttpServletRequest request
    ) {
        return ok(service.getParty(actor(actorHeader, request), partyId));
    }

    @GetMapping("/api/v1/parties/{partyId}/invitations")
    public ResponseEntity<PartyInvitationPage> listPartyInvitations(
            @PathVariable UUID partyId,
            @RequestParam(required = false) @Size(max = 2048) String cursor,
            @RequestParam(defaultValue = "20") @Min(1) @Max(100) int limit,
            @RequestHeader(value = "X-Local-Actor-Id", required = false) String actorHeader,
            HttpServletRequest request
    ) {
        return ok(service.listPartyInvitations(actor(actorHeader, request), partyId, cursor, limit));
    }

    @PostMapping("/api/v1/parties/{partyId}/invitations")
    public ResponseEntity<JsonNode> createInvitation(
            @PathVariable UUID partyId,
            @RequestHeader(value = "X-Local-Actor-Id", required = false) String actorHeader,
            @RequestHeader("Idempotency-Key")
            @Size(min = 8, max = 128) @Pattern(regexp = "^[!-~]+$") String idempotencyKey,
            @Valid @RequestBody CreatePartyInvitationRequest body,
            HttpServletRequest request
    ) {
        return mutation(service.createInvitation(
                actor(actorHeader, request), partyId, idempotencyKey, body
        ));
    }

    @GetMapping("/api/v1/parties/{partyId}/baseline-recommendations")
    public ResponseEntity<PartyBaselinePage> listBaseline(
            @PathVariable UUID partyId,
            @RequestParam(required = false) @Size(max = 2048) String cursor,
            @RequestParam(defaultValue = "20") @Min(1) @Max(100) int limit,
            @RequestHeader(value = "X-Local-Actor-Id", required = false) String actorHeader,
            HttpServletRequest request
    ) {
        return ok(service.listBaseline(actor(actorHeader, request), partyId, cursor, limit));
    }

    private UUID actor(String header, HttpServletRequest request) {
        loopback.requireLoopbackRemote(request.getRemoteAddr());
        return service.requireActor(header);
    }

    private static <T> ResponseEntity<T> ok(T body) {
        return ResponseEntity.ok().cacheControl(CacheControl.noStore().cachePrivate()).body(body);
    }

    private static ResponseEntity<JsonNode> mutation(C3LocalService.HttpMutation result) {
        return ResponseEntity.status(result.status())
                .cacheControl(CacheControl.noStore().cachePrivate())
                .body(result.body());
    }

}
