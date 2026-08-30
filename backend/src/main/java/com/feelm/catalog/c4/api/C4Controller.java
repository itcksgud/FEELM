package com.feelm.catalog.c4.api;

import com.feelm.catalog.c4.service.C4Service;
import com.feelm.catalog.security.CatalogUserContext;
import com.feelm.catalog.security.CatalogUserContextResolver;
import jakarta.validation.Valid;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.http.HttpHeaders;
import org.springframework.http.ResponseCookie;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.CookieValue;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.time.Duration;

@RestController
@RequestMapping("/api/v1")
@ConditionalOnProperty(name = "catalog.c4.enabled", havingValue = "true")
public final class C4Controller {
    private static final String REFRESH_COOKIE = "feelm_local_refresh";
    private static final String CSRF_COOKIE = "feelm_local_csrf";
    private final C4Service service;
    private final CatalogUserContextResolver resolver;

    public C4Controller(C4Service service, CatalogUserContextResolver resolver) {
        this.service = service;
        this.resolver = resolver;
    }

    @PostMapping("/auth/sign-up")
    ResponseEntity<C4ApiDtos.PendingEmailSignup> signup(
            @Valid @RequestBody C4ApiDtos.CreateEmailSignupRequest request,
            @RequestHeader("Idempotency-Key") String idempotencyKey
    ) {
        return ResponseEntity.accepted().body(service.signup(request, idempotencyKey));
    }

    @PostMapping("/auth/email-verifications")
    C4ApiDtos.EmailVerificationResult verify(
            @Valid @RequestBody C4ApiDtos.VerifyEmailRequest request,
            @RequestHeader("Idempotency-Key") String idempotencyKey
    ) { return service.verifyEmail(request, idempotencyKey); }

    @PostMapping("/auth/email-verification-resends")
    ResponseEntity<C4ApiDtos.VerificationDeliveryState> resend(
            @Valid @RequestBody C4ApiDtos.ResendEmailVerificationRequest request,
            @RequestHeader("Idempotency-Key") String idempotencyKey
    ) { return ResponseEntity.accepted().body(service.resend(request, idempotencyKey)); }

    @PostMapping("/auth/login")
    ResponseEntity<C4ApiDtos.AuthenticationResult> login(
            @Valid @RequestBody C4ApiDtos.EmailLoginRequest request,
            @RequestHeader(name = "Origin", required = false) String origin
    ) { return authenticated(service.login(request, origin)); }

    @PostMapping("/auth/refresh")
    ResponseEntity<C4ApiDtos.AuthenticationResult> refresh(
            @RequestHeader(name = "Origin", required = false) String origin,
            @RequestHeader(name = "X-CSRF-Token", required = false) String csrfHeader,
            @CookieValue(name = REFRESH_COOKIE, required = false) String refresh,
            @CookieValue(name = CSRF_COOKIE, required = false) String csrf
    ) { return authenticated(service.refresh(origin, refresh, csrf, csrfHeader)); }

    @PostMapping("/auth/logout")
    ResponseEntity<Void> logout(
            @RequestHeader(name = "Origin", required = false) String origin,
            @RequestHeader(name = "X-CSRF-Token", required = false) String csrfHeader,
            @RequestHeader(name = "Idempotency-Key", required = false) String idempotencyKey,
            @CookieValue(name = REFRESH_COOKIE, required = false) String refresh,
            @CookieValue(name = CSRF_COOKIE, required = false) String csrf
    ) {
        service.logout(origin, refresh, csrf, csrfHeader, idempotencyKey);
        HttpHeaders headers = new HttpHeaders();
        headers.add(HttpHeaders.SET_COOKIE, clearCookie(REFRESH_COOKIE, true).toString());
        headers.add(HttpHeaders.SET_COOKIE, clearCookie(CSRF_COOKIE, false).toString());
        return ResponseEntity.noContent().headers(headers).build();
    }

    @GetMapping("/me")
    C4ApiDtos.MyMembership me(@RequestHeader("Authorization") String authorization) {
        return service.membership(actor(authorization).actorUserId());
    }

    @PatchMapping("/me")
    C4ApiDtos.MyMembership updateNickname(
            @RequestHeader("Authorization") String authorization,
            @RequestHeader("Idempotency-Key") String idempotencyKey,
            @RequestHeader("X-Expected-Revision") long expectedRevision,
            @Valid @RequestBody C4ApiDtos.UpdateNicknameRequest request
    ) { return service.updateNickname(actor(authorization).actorUserId(), request, expectedRevision, idempotencyKey); }

    @GetMapping("/onboarding/movies")
    C4ApiDtos.OnboardingMoviePage movies(@RequestHeader("Authorization") String authorization) {
        actor(authorization); return service.onboardingMovies();
    }

    @PutMapping("/onboarding/preferences")
    C4ApiDtos.OnboardingState replacePreferences(
            @RequestHeader("Authorization") String authorization,
            @RequestHeader("Idempotency-Key") String idempotencyKey,
            @RequestHeader("X-Expected-Revision") long expectedRevision,
            @Valid @RequestBody C4ApiDtos.ReplaceOnboardingPreferencesRequest request
    ) { return service.replacePreferences(actor(authorization).actorUserId(), request, expectedRevision, idempotencyKey); }

    @PostMapping("/onboarding/complete")
    C4ApiDtos.OnboardingState complete(
            @RequestHeader("Authorization") String authorization,
            @RequestHeader("Idempotency-Key") String idempotencyKey,
            @RequestHeader("X-Expected-Revision") long expectedRevision,
            @Valid @RequestBody C4ApiDtos.CompleteOnboardingRequest request
    ) { return service.completeOnboarding(actor(authorization).actorUserId(), request, expectedRevision, idempotencyKey); }

    @GetMapping("/me/ott-subscriptions")
    C4ApiDtos.MyOttSubscriptionSet ott(@RequestHeader("Authorization") String authorization) {
        return service.ottSubscriptions(actor(authorization).actorUserId());
    }

    @PutMapping("/me/ott-subscriptions")
    C4ApiDtos.MyOttSubscriptionSet replaceOtt(
            @RequestHeader("Authorization") String authorization,
            @RequestHeader("Idempotency-Key") String idempotencyKey,
            @RequestHeader("X-Expected-Revision") long expectedRevision,
            @Valid @RequestBody C4ApiDtos.ReplaceOttSubscriptionsRequest request
    ) { return service.replaceOttSubscriptions(actor(authorization).actorUserId(), request, expectedRevision, idempotencyKey); }

    private CatalogUserContext actor(String authorization) { return resolver.resolveRequired(authorization); }

    private static ResponseEntity<C4ApiDtos.AuthenticationResult> authenticated(C4ApiDtos.AuthenticationEnvelope envelope) {
        HttpHeaders headers = new HttpHeaders();
        headers.add(HttpHeaders.SET_COOKIE, issueCookie(REFRESH_COOKIE, envelope.refreshToken(), true).toString());
        headers.add(HttpHeaders.SET_COOKIE, issueCookie(CSRF_COOKIE, envelope.csrfToken(), false).toString());
        return ResponseEntity.ok().headers(headers).body(envelope.body());
    }

    private static ResponseCookie issueCookie(String name, String value, boolean httpOnly) {
        return ResponseCookie.from(name, value).path("/").sameSite("Lax").secure(false)
                .httpOnly(httpOnly).maxAge(Duration.ofDays(7)).build();
    }

    private static ResponseCookie clearCookie(String name, boolean httpOnly) {
        return ResponseCookie.from(name, "").path("/").sameSite("Lax").secure(false)
                .httpOnly(httpOnly).maxAge(Duration.ZERO).build();
    }
}
