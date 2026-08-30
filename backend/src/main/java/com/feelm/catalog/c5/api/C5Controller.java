package com.feelm.catalog.c5.api;

import com.feelm.catalog.c5.api.C5ApiDtos.*;
import com.feelm.catalog.c5.service.C5LocalService;
import com.feelm.catalog.c5.service.C5LoopbackGuard;
import com.feelm.catalog.security.CatalogUserContextResolver;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Profile;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.UUID;

@Validated
@RestController
@Profile("local")
@ConditionalOnProperty(name = "c5.local.enabled", havingValue = "true")
public class C5Controller {
    private static final String NO_REFERRER = "no-referrer";
    private final C5LocalService service;
    private final C5LoopbackGuard loopback;
    private final CatalogUserContextResolver resolver;

    public C5Controller(C5LocalService service, C5LoopbackGuard loopback, CatalogUserContextResolver resolver) {
        this.service = service;
        this.loopback = loopback;
        this.resolver = resolver;
    }

    @GetMapping("/api/v1/me/taste-reports")
    public ResponseEntity<TasteReportSummaryPage> listReports(
            @RequestHeader(value = "Authorization", required = false) String authorization,
            @RequestParam(required = false) @Size(max = 2048) String cursor,
            @RequestParam(defaultValue = "20") @Min(1) @Max(100) int limit,
            HttpServletRequest request
    ) {
        return ok(service.listReports(actor(authorization, request), cursor, limit));
    }

    @PostMapping("/api/v1/me/taste-reports")
    public ResponseEntity<TasteReport> createReport(
            @RequestHeader(value = "Authorization", required = false) String authorization,
            @RequestHeader("Idempotency-Key") @Size(min = 8, max = 128)
            @Pattern(regexp = "^[!-~]+$") String idempotencyKey,
            @Valid @RequestBody CreateTasteReportRequest body,
            HttpServletRequest request
    ) {
        return response(HttpStatus.CREATED, service.createReport(actor(authorization, request), idempotencyKey, body));
    }

    @GetMapping("/api/v1/me/taste-reports/{reportId}")
    public ResponseEntity<TasteReport> getReport(
            @PathVariable UUID reportId,
            @RequestHeader(value = "Authorization", required = false) String authorization,
            @RequestParam(required = false) @Size(max = 2048) String cursor,
            @RequestParam(defaultValue = "20") @Min(1) @Max(100) int limit,
            HttpServletRequest request
    ) {
        return ok(service.report(actor(authorization, request), reportId, cursor, limit));
    }

    @PostMapping("/api/v1/me/taste-reports/{reportId}/exports")
    public ResponseEntity<ReportExport> createExport(
            @PathVariable UUID reportId,
            @RequestHeader(value = "Authorization", required = false) String authorization,
            @RequestHeader("Idempotency-Key") @Size(min = 8, max = 128)
            @Pattern(regexp = "^[!-~]+$") String idempotencyKey,
            HttpServletRequest request
    ) {
        return response(HttpStatus.ACCEPTED,
                service.createExport(actor(authorization, request), reportId, idempotencyKey));
    }

    @GetMapping("/api/v1/me/report-exports/{exportId}")
    public ResponseEntity<ReportExport> getExport(
            @PathVariable UUID exportId,
            @RequestHeader(value = "Authorization", required = false) String authorization,
            HttpServletRequest request
    ) {
        return ok(service.export(actor(authorization, request), exportId));
    }

    @GetMapping(value = "/api/v1/me/report-exports/{exportId}/content", produces = MediaType.APPLICATION_PDF_VALUE)
    public ResponseEntity<byte[]> download(
            @PathVariable UUID exportId,
            @RequestHeader(value = "Authorization", required = false) String authorization,
            HttpServletRequest request
    ) {
        PdfContent content = service.download(actor(authorization, request), exportId);
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore().cachePrivate())
                .header("Referrer-Policy", NO_REFERRER)
                .header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename=feelm-report.pdf")
                .contentType(MediaType.APPLICATION_PDF)
                .contentLength(content.bytes().length)
                .body(content.bytes());
    }

    @GetMapping("/api/v1/me/privacy-settings")
    public ResponseEntity<PrivacySettings> privacy(
            @RequestHeader(value = "Authorization", required = false) String authorization,
            HttpServletRequest request
    ) {
        return ok(service.privacy(actor(authorization, request)));
    }

    @PutMapping("/api/v1/me/privacy-settings")
    public ResponseEntity<PrivacySettings> replacePrivacy(
            @RequestHeader(value = "Authorization", required = false) String authorization,
            @RequestHeader("Idempotency-Key") @Size(min = 8, max = 128)
            @Pattern(regexp = "^[!-~]+$") String idempotencyKey,
            @Valid @RequestBody ReplacePrivacySettingsRequest body,
            HttpServletRequest request
    ) {
        return ok(service.replacePrivacy(actor(authorization, request), idempotencyKey, body));
    }

    @GetMapping("/api/v1/public/profiles/{publicProfileId}")
    public ResponseEntity<PublicProfile> publicProfile(
            @PathVariable UUID publicProfileId,
            HttpServletRequest request
    ) {
        local(request);
        return ok(service.publicProfile(publicProfileId));
    }

    @GetMapping("/api/v1/public/profiles/{publicProfileId}/film")
    public ResponseEntity<PublicFilmPage> publicFilm(
            @PathVariable UUID publicProfileId,
            @RequestParam(required = false) @Size(max = 2048) String cursor,
            @RequestParam(defaultValue = "20") @Min(1) @Max(100) int limit,
            HttpServletRequest request
    ) {
        local(request);
        return ok(service.publicFilm(publicProfileId, cursor, limit));
    }

    @GetMapping("/api/v1/public/profiles/{publicProfileId}/popcorns")
    public ResponseEntity<PublicPopcornPage> publicPopcorn(
            @PathVariable UUID publicProfileId,
            @RequestParam(required = false) @Size(max = 2048) String cursor,
            @RequestParam(defaultValue = "20") @Min(1) @Max(100) int limit,
            HttpServletRequest request
    ) {
        local(request);
        return ok(service.publicPopcorn(publicProfileId, cursor, limit));
    }

    @PostMapping("/api/v1/me/taste-reports/{reportId}/shares")
    public ResponseEntity<CreatedReportShare> createShare(
            @PathVariable UUID reportId,
            @RequestHeader(value = "Authorization", required = false) String authorization,
            @RequestHeader("Idempotency-Key") @Size(min = 8, max = 128)
            @Pattern(regexp = "^[!-~]+$") String idempotencyKey,
            HttpServletRequest request
    ) {
        return response(HttpStatus.CREATED,
                service.createShare(actor(authorization, request), reportId, idempotencyKey));
    }

    @PostMapping("/api/v1/me/report-shares/{shareId}/revoke")
    public ResponseEntity<Void> revokeShare(
            @PathVariable UUID shareId,
            @RequestHeader(value = "Authorization", required = false) String authorization,
            @RequestHeader("Idempotency-Key") @Size(min = 8, max = 128)
            @Pattern(regexp = "^[!-~]+$") String idempotencyKey,
            HttpServletRequest request
    ) {
        service.revokeShare(actor(authorization, request), shareId, idempotencyKey);
        return ResponseEntity.noContent().cacheControl(CacheControl.noStore()).header("Referrer-Policy", NO_REFERRER).build();
    }

    @PostMapping("/api/v1/public/report-shares/exchange")
    public ResponseEntity<ReportViewerSession> exchange(
            @Valid @RequestBody ExchangeReportShareRequest body,
            HttpServletRequest request
    ) {
        local(request);
        return ok(service.exchange(body.rawToken()));
    }

    @GetMapping("/api/v1/public/shared-report")
    public ResponseEntity<SharedTasteReport> sharedReport(
            @RequestHeader(value = "X-Report-Viewer-Session", required = false) String viewerSession,
            @RequestParam(required = false) @Size(max = 2048) String cursor,
            @RequestParam(defaultValue = "20") @Min(1) @Max(100) int limit,
            HttpServletRequest request
    ) {
        local(request);
        return ok(service.sharedReport(viewerSession, cursor, limit));
    }

    @GetMapping("/api/v1/me/notification-settings")
    public ResponseEntity<NotificationSettings> notificationSettings(
            @RequestHeader(value = "Authorization", required = false) String authorization,
            HttpServletRequest request
    ) {
        return ok(service.notificationSettings(actor(authorization, request)));
    }

    @PutMapping("/api/v1/me/notification-settings")
    public ResponseEntity<NotificationSettings> replaceNotificationSettings(
            @RequestHeader(value = "Authorization", required = false) String authorization,
            @RequestHeader("Idempotency-Key") @Size(min = 8, max = 128)
            @Pattern(regexp = "^[!-~]+$") String idempotencyKey,
            @Valid @RequestBody ReplaceNotificationSettingsRequest body,
            HttpServletRequest request
    ) {
        return ok(service.replaceNotificationSettings(actor(authorization, request), idempotencyKey, body));
    }

    @GetMapping("/api/v1/me/notifications")
    public ResponseEntity<NotificationPage> notifications(
            @RequestHeader(value = "Authorization", required = false) String authorization,
            @RequestParam(required = false) @Size(max = 2048) String cursor,
            @RequestParam(defaultValue = "20") @Min(1) @Max(100) int limit,
            HttpServletRequest request
    ) {
        return ok(service.notifications(actor(authorization, request), cursor, limit));
    }

    @PutMapping("/api/v1/me/notifications/{notificationId}/state")
    public ResponseEntity<InAppNotification> updateNotification(
            @PathVariable UUID notificationId,
            @RequestHeader(value = "Authorization", required = false) String authorization,
            @RequestHeader("Idempotency-Key") @Size(min = 8, max = 128)
            @Pattern(regexp = "^[!-~]+$") String idempotencyKey,
            @Valid @RequestBody UpdateNotificationStateRequest body,
            HttpServletRequest request
    ) {
        return ok(service.updateNotification(actor(authorization, request), notificationId, idempotencyKey, body));
    }

    private UUID actor(String authorization, HttpServletRequest request) {
        local(request);
        return service.requireActiveActor(resolver.resolveRequired(authorization).actorUserId());
    }

    private void local(HttpServletRequest request) {
        loopback.requireLocal(request.getRemoteAddr(), request.getHeader("Origin"));
    }

    private static <T> ResponseEntity<T> ok(T body) {
        return response(HttpStatus.OK, body);
    }

    private static <T> ResponseEntity<T> response(HttpStatus status, T body) {
        return ResponseEntity.status(status)
                .cacheControl(CacheControl.noStore().cachePrivate())
                .header("Referrer-Policy", NO_REFERRER)
                .body(body);
    }
}
