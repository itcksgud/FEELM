package com.feelm.catalog.c4.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.feelm.catalog.api.ApiException;
import com.feelm.catalog.c4.api.C4ApiDtos;
import com.feelm.catalog.c4.config.C4Properties;
import com.feelm.catalog.c4.mail.C4MailGateway;
import com.feelm.catalog.c4.security.C4Crypto;
import com.feelm.catalog.c4.security.C4JwtService;
import com.feelm.catalog.security.C4AccessTokenVerifier;
import com.feelm.catalog.security.CatalogUserContext;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.nio.charset.StandardCharsets;
import java.net.URLEncoder;
import java.text.Normalizer;
import java.time.Instant;
import java.sql.Timestamp;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import java.util.function.Supplier;
import java.util.regex.Pattern;

@Service
@ConditionalOnProperty(name = "catalog.c4.enabled", havingValue = "true")
public class C4Service implements C4AccessTokenVerifier {
    private static final Pattern NICKNAME = Pattern.compile("[\\p{L}\\p{N}_]{2,20}");
    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;
    private final C4Crypto crypto;
    private final C4JwtService jwt;
    private final C4Properties properties;
    private final List<C4MailGateway> mailGateways;
    private final String dummyPasswordPhc;

    public C4Service(JdbcTemplate jdbc, ObjectMapper objectMapper, C4Crypto crypto, C4JwtService jwt,
                     C4Properties properties, List<C4MailGateway> mailGateways) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
        this.crypto = crypto;
        this.jwt = jwt;
        this.properties = properties;
        this.mailGateways = List.copyOf(mailGateways);
        this.dummyPasswordPhc = crypto.passwordHash("c4-dummy-password-workload-only");
    }

    @Override
    public Optional<CatalogUserContext> verify(String token) {
        Optional<C4JwtService.Claims> claims = jwt.verify(token, dbNow());
        if (claims.isEmpty()) return Optional.empty();
        UUID userId = claims.get().userId();
        Integer active = jdbc.query("SELECT 1 FROM c4_user_account WHERE user_id=? AND membership_status='ACTIVE'",
                rs -> rs.next() ? 1 : null, userId);
        if (active == null) return Optional.empty();
        Set<UUID> providers = new HashSet<>(jdbc.query(
                "SELECT provider_id FROM c4_user_ott_subscription WHERE user_id=?",
                (rs, rowNum) -> rs.getObject(1, UUID.class), userId));
        return Optional.of(new CatalogUserContext(true, userId, providers));
    }

    @Transactional
    public C4ApiDtos.PendingEmailSignup signup(C4ApiDtos.CreateEmailSignupRequest request, String idempotencyKey) {
        String email = normalizeEmail(request.email());
        String nickname = normalizeNickname(request.nickname());
        validatePassword(request.password());
        UUID scope = stableScope("signup:" + email);
        String canonical = email + "\n" + request.password() + "\n" + nickname;
        C4ApiDtos.PendingEmailSignup replay = replay(scope, "createEmailSignup", idempotencyKey, canonical,
                C4ApiDtos.PendingEmailSignup.class);
        if (replay != null) return replay;
        admit("signup:" + email, 5);

        Instant now = dbNow();
        Instant flowExpiry = now.plus(24, ChronoUnit.HOURS);
        Instant verificationExpiry = now.plus(10, ChronoUnit.MINUTES);
        Instant resendAt = now.plus(60, ChronoUnit.SECONDS);
        UUID signupId = UUID.randomUUID();
        String masked = maskEmail(email);
        jdbc.query("SELECT pg_advisory_xact_lock(hashtextextended(?, 0))", rs -> null, "c4-signup:" + crypto.hmac(email));
        AccountCredential existing = credentialByEmail(email, true);
        UUID materialId = null;

        if (existing == null) {
            UUID userId = UUID.randomUUID();
            jdbc.update("INSERT INTO c4_user_account(user_id,membership_status,created_at,pending_purge_at) VALUES (?,'PENDING_EMAIL_VERIFICATION',?,?)",
                    userId, ts(now), ts(now.plus(30, ChronoUnit.DAYS)));
            jdbc.update("INSERT INTO c4_email_credential(user_id,email_normalized,password_phc,created_at) VALUES (?,?,?,?)",
                    userId, email, crypto.passwordHash(request.password()), ts(now));
            try {
                jdbc.update("INSERT INTO c4_user_profile(user_id,nickname,nickname_normalized,normalization_version,nickname_changed_at) VALUES (?,?,?,?,?)",
                        userId, request.nickname().strip(), nickname, "trim-nfkc-casefold-v1", ts(now.minus(30, ChronoUnit.DAYS)));
            } catch (DuplicateKeyException exception) {
                throw conflict("NICKNAME_ALREADY_USED", "이미 사용 중인 닉네임이에요.");
            }
            jdbc.update("INSERT INTO c4_onboarding_journey(user_id,journey_status,revision,updated_at) VALUES (?,'NOT_STARTED',1,?)", userId, ts(now));
            jdbc.update("INSERT INTO c4_ott_subscription_set(user_id,selection_status,revision,updated_at) VALUES (?,'NOT_CONFIGURED',1,?)", userId, ts(now));
            jdbc.update("INSERT INTO c4_email_signup_flow(signup_id,flow_kind,user_id,email_masked,flow_status,created_at,flow_expires_at,verification_expires_at,resend_available_at) VALUES (?, 'REAL', ?, ?, 'OPEN', ?, ?, ?, ?)",
                    signupId, userId, masked, ts(now), ts(flowExpiry), ts(verificationExpiry), ts(resendAt));
            materialId = createChallenge(signupId, userId, 1, verificationExpiry, now);
        } else if (recoverablePending(existing, request.password(), now)) {
            ExpiredFlow expired = latestRealFlow(existing.userId());
            if (expired != null && !now.isBefore(expired.flowExpiresAt())) {
                jdbc.update("UPDATE c4_email_signup_flow SET flow_status='EXPIRED' WHERE signup_id=? AND flow_status='OPEN'", expired.signupId());
                jdbc.update("UPDATE c4_email_verification_challenge SET challenge_status='EXPIRED' WHERE signup_id=? AND challenge_status='ACTIVE'", expired.signupId());
                jdbc.update("DELETE FROM c4_verification_delivery_material WHERE challenge_id IN (SELECT challenge_id FROM c4_email_verification_challenge WHERE signup_id=?)", expired.signupId());
                jdbc.update("INSERT INTO c4_email_signup_flow(signup_id,flow_kind,user_id,email_masked,flow_status,created_at,flow_expires_at,verification_expires_at,resend_available_at) VALUES (?, 'REAL', ?, ?, 'OPEN', ?, ?, ?, ?)",
                        signupId, existing.userId(), masked, ts(now), ts(flowExpiry), ts(verificationExpiry), ts(resendAt));
                materialId = createChallenge(signupId, existing.userId(), 1, verificationExpiry, now);
            } else {
                insertDecoy(signupId, masked, now, flowExpiry, verificationExpiry, resendAt);
            }
        } else {
            if ("ACTIVE".equals(existing.status())) crypto.passwordMatches(request.password(), dummyPasswordPhc);
            insertDecoy(signupId, masked, now, flowExpiry, verificationExpiry, resendAt);
        }
        C4ApiDtos.PendingEmailSignup result = new C4ApiDtos.PendingEmailSignup(signupId,
                "PENDING_EMAIL_VERIFICATION", masked, "QUEUED", verificationExpiry, resendAt, 1);
        store(scope, "createEmailSignup", idempotencyKey, canonical, 202, result);
        if (materialId != null) dispatchAfterCommit(materialId);
        return result;
    }

    private void insertDecoy(UUID signupId, String masked, Instant now, Instant flowExpiry,
                             Instant verificationExpiry, Instant resendAt) {
            jdbc.update("INSERT INTO c4_email_signup_flow(signup_id,flow_kind,email_masked,flow_status,created_at,flow_expires_at,verification_expires_at,resend_available_at) VALUES (?, 'DECOY', ?, 'OPEN', ?, ?, ?, ?)",
                    signupId, masked, ts(now), ts(flowExpiry), ts(verificationExpiry), ts(resendAt));
    }

    private boolean recoverablePending(AccountCredential existing, String password, Instant now) {
        return "PENDING_EMAIL_VERIFICATION".equals(existing.status())
                && now.isBefore(existing.pendingPurgeAt())
                && crypto.passwordMatches(password, existing.passwordPhc());
    }

    private ExpiredFlow latestRealFlow(UUID userId) {
        return jdbc.query("SELECT signup_id,flow_expires_at FROM c4_email_signup_flow WHERE user_id=? AND flow_kind='REAL' ORDER BY created_at DESC LIMIT 1",
                rs -> rs.next() ? new ExpiredFlow(rs.getObject(1, UUID.class), rs.getTimestamp(2).toInstant()) : null, userId);
    }

    @Transactional(noRollbackFor = ApiException.class)
    public C4ApiDtos.EmailVerificationResult verifyEmail(C4ApiDtos.VerifyEmailRequest request, String idempotencyKey) {
        UUID scope = request.signupId();
        String canonical = request.signupId() + "\n" + request.verificationSecret();
        C4ApiDtos.EmailVerificationResult replay = replay(scope, "verifySignupEmail", idempotencyKey, canonical,
                C4ApiDtos.EmailVerificationResult.class);
        if (replay != null) return replay;
        Flow flow = lockFlow(request.signupId());
        Instant now = dbNow();
        if (flow == null || !"OPEN".equals(flow.status()) || !now.isBefore(flow.flowExpiresAt())) {
            throw bad("SIGNUP_FLOW_INVALID_OR_EXPIRED", "가입 인증 흐름이 만료됐어요.");
        }
        if (flow.failedAttempts() >= 5) throw tooMany("VERIFICATION_ATTEMPTS_EXHAUSTED", "인증 시도 횟수를 초과했어요.");
        boolean matched = false;
        if ("REAL".equals(flow.kind()) && flow.challengeId() != null) {
            String expected = jdbc.query("SELECT secret_sha256 FROM c4_email_verification_challenge WHERE challenge_id=? AND challenge_status='ACTIVE' AND expires_at>?",
                    rs -> rs.next() ? rs.getString(1) : null, flow.challengeId(), ts(now));
            matched = expected != null && constantHex(expected, crypto.sha256(request.verificationSecret()));
        }
        if (!matched) {
            int attempts = flow.failedAttempts() + 1;
            jdbc.update("UPDATE c4_email_signup_flow SET failed_attempt_count=?, flow_status=CASE WHEN ? >= 5 THEN 'EXHAUSTED' ELSE flow_status END WHERE signup_id=?",
                    attempts, attempts, request.signupId());
            if (attempts >= 5) throw tooMany("VERIFICATION_ATTEMPTS_EXHAUSTED", "인증 시도 횟수를 초과했어요.");
            throw bad("VERIFICATION_INVALID", "인증 정보를 확인해 주세요.");
        }
        jdbc.update("UPDATE c4_email_verification_challenge SET challenge_status='CONSUMED' WHERE challenge_id=?", flow.challengeId());
        jdbc.update("UPDATE c4_email_signup_flow SET flow_status='VERIFIED', revision=revision+1 WHERE signup_id=?", flow.signupId());
        jdbc.update("UPDATE c4_user_account SET membership_status='ACTIVE', activated_at=?, pending_purge_at=? WHERE user_id=? AND membership_status='PENDING_EMAIL_VERIFICATION'",
                ts(now), ts(now), flow.userId());
        jdbc.update("DELETE FROM c4_verification_delivery_material WHERE challenge_id=?", flow.challengeId());
        C4ApiDtos.EmailVerificationResult result = new C4ApiDtos.EmailVerificationResult("ACTIVE", flow.emailMasked(), "LOGIN", flow.revision() + 1);
        store(scope, "verifySignupEmail", idempotencyKey, canonical, 200, result);
        return result;
    }

    @Transactional
    public C4ApiDtos.VerificationDeliveryState resend(C4ApiDtos.ResendEmailVerificationRequest request, String idempotencyKey) {
        UUID scope = request.signupId();
        String canonical = request.signupId().toString();
        C4ApiDtos.VerificationDeliveryState replay = replay(scope, "resendSignupEmailVerification", idempotencyKey, canonical,
                C4ApiDtos.VerificationDeliveryState.class);
        if (replay != null) return replay;
        Flow flow = lockFlow(request.signupId());
        Instant now = dbNow();
        if (flow == null || !"OPEN".equals(flow.status()) || !now.isBefore(flow.flowExpiresAt()))
            throw bad("SIGNUP_FLOW_INVALID_OR_EXPIRED", "가입 인증 흐름이 만료됐어요.");
        if (now.isBefore(flow.resendAvailableAt())) throw tooMany("AUTH_FLOW_THROTTLED", "잠시 후 다시 시도해 주세요.");
        Instant expires = min(now.plus(10, ChronoUnit.MINUTES), flow.flowExpiresAt());
        Instant resendAt = now.plus(60, ChronoUnit.SECONDS);
        UUID materialId = null;
        if ("REAL".equals(flow.kind())) {
            jdbc.update("UPDATE c4_email_verification_challenge SET challenge_status='SUPERSEDED' WHERE challenge_id=? AND challenge_status='ACTIVE'", flow.challengeId());
            materialId = createChallenge(flow.signupId(), flow.userId(), (int) flow.revision() + 1, expires, now);
        }
        jdbc.update("UPDATE c4_email_signup_flow SET failed_attempt_count=0, verification_expires_at=?, resend_available_at=?, revision=revision+1 WHERE signup_id=?",
                ts(expires), ts(resendAt), flow.signupId());
        C4ApiDtos.VerificationDeliveryState result = new C4ApiDtos.VerificationDeliveryState(flow.signupId(), "QUEUED", expires, resendAt, flow.revision() + 1);
        store(scope, "resendSignupEmailVerification", idempotencyKey, canonical, 202, result);
        if (materialId != null) dispatchAfterCommit(materialId);
        return result;
    }

    @Transactional
    public C4ApiDtos.AuthenticationEnvelope login(C4ApiDtos.EmailLoginRequest request, String origin) {
        requireOrigin(origin);
        String email = normalizeEmail(request.email());
        validatePassword(request.password());
        AccountCredential credential = credentialByEmail(email, false);
        String phc = credential == null ? dummyPasswordPhc : credential.passwordPhc();
        boolean matches = crypto.passwordMatches(request.password(), phc);
        if (credential == null || !matches) throw unauthorized("INVALID_CREDENTIALS", "이메일 또는 비밀번호를 확인해 주세요.");
        if (!"ACTIVE".equals(credential.status())) throw forbidden("EMAIL_VERIFICATION_REQUIRED", "이메일 인증이 필요해요.");
        return createSession(credential.userId());
    }

    @Transactional(noRollbackFor = ApiException.class)
    public C4ApiDtos.AuthenticationEnvelope refresh(String origin, String refreshToken, String csrfCookie, String csrfHeader) {
        requireOrigin(origin);
        requireCompleteCookies(refreshToken, csrfCookie, csrfHeader);
        Instant now = dbNow();
        Refresh current = lockRefresh(refreshToken);
        if (current == null) {
            throw unauthorized("AUTH_SESSION_INVALID", "인증 세션이 유효하지 않아요.");
        }
        if (!constantHex(current.csrfSha256(), crypto.sha256(csrfCookie)) || !csrfCookie.equals(csrfHeader))
            throw forbidden("CSRF_FORBIDDEN", "CSRF 검증에 실패했어요.");
        if ("ROTATED".equals(current.tokenStatus())) {
            if ("ACTIVE".equals(current.sessionStatus()) && current.rotatedAt() != null
                    && current.generation() == current.currentGeneration() - 1
                    && !now.isAfter(current.rotatedAt().plusSeconds(5))) {
                throw conflict("REFRESH_RACE_RETRY_NEW_COOKIE", "이미 갱신된 세션 쿠키로 다시 시도해 주세요.");
            }
            revokeRefreshFamily(current.sessionId(), now);
            throw unauthorized("AUTH_SESSION_INVALID", "인증 세션이 유효하지 않아요.");
        }
        if (!"ACTIVE".equals(current.tokenStatus()) || !"ACTIVE".equals(current.sessionStatus())
                || !now.isBefore(current.expiresAt()) || !now.isBefore(current.absoluteExpiresAt())) {
            throw unauthorized("AUTH_SESSION_INVALID", "인증 세션이 유효하지 않아요.");
        }
        jdbc.update("UPDATE c4_refresh_token SET token_status='ROTATED', rotated_at=? WHERE token_id=? AND token_status='ACTIVE'", ts(now), current.tokenId());
        String nextRefresh = crypto.randomToken();
        String nextCsrf = crypto.randomToken();
        int generation = current.generation() + 1;
        jdbc.update("INSERT INTO c4_refresh_token(token_id,session_id,generation,token_sha256,csrf_sha256,token_status,created_at,expires_at) VALUES (?,?,?,?,?,'ACTIVE',?,?)",
                UUID.randomUUID(), current.sessionId(), generation, crypto.sha256(nextRefresh), crypto.sha256(nextCsrf),
                ts(now), ts(min(now.plus(7, ChronoUnit.DAYS), current.absoluteExpiresAt())));
        jdbc.update("UPDATE c4_auth_session SET current_generation=?, csrf_sha256=?, idle_expires_at=? WHERE session_id=?",
                generation, crypto.sha256(nextCsrf), ts(min(now.plus(7, ChronoUnit.DAYS), current.absoluteExpiresAt())), current.sessionId());
        C4ApiDtos.AuthenticationResult body = new C4ApiDtos.AuthenticationResult("Bearer",
                jwt.issue(current.userId(), current.sessionId(), now), 600, membership(current.userId()));
        return new C4ApiDtos.AuthenticationEnvelope(body, nextRefresh, nextCsrf);
    }

    @Transactional
    public void logout(String origin, String refreshToken, String csrfCookie, String csrfHeader, String idempotencyKey) {
        requireOrigin(origin);
        boolean refreshMissing = refreshToken == null || refreshToken.isBlank();
        boolean csrfMissing = csrfCookie == null || csrfCookie.isBlank();
        if (refreshMissing && csrfMissing) return;
        requireCompleteCookies(refreshToken, csrfCookie, csrfHeader);
        validateIdempotencyKey(idempotencyKey);
        Refresh current = lockRefresh(refreshToken);
        if (current == null)
            throw unauthorized("AUTH_SESSION_INVALID", "인증 세션이 유효하지 않아요.");
        if (!csrfCookie.equals(csrfHeader) || !constantHex(current.csrfSha256(), crypto.sha256(csrfCookie)))
            throw forbidden("CSRF_FORBIDDEN", "CSRF 검증에 실패했어요.");
        String canonical = current.sessionId() + "\nlogout";
        if (hasVoidReplay(current.userId(), "logoutCurrentSession", idempotencyKey, canonical)) return;
        if (!"ACTIVE".equals(current.tokenStatus()) || !"ACTIVE".equals(current.sessionStatus()))
            throw unauthorized("AUTH_SESSION_INVALID", "인증 세션이 유효하지 않아요.");
        Instant now = dbNow();
        revokeRefreshFamily(current.sessionId(), now);
        store(current.userId(), "logoutCurrentSession", idempotencyKey, canonical, 204, java.util.Map.of("status", "LOGGED_OUT"));
    }

    @Transactional(readOnly = true)
    public C4ApiDtos.MyMembership membership(UUID userId) {
        return jdbc.queryForObject("""
                SELECT a.membership_status,c.email_normalized,p.nickname,p.revision,j.journey_status,j.revision,
                       (SELECT count(*) FROM c4_onboarding_preference op WHERE op.user_id=a.user_id)
                FROM c4_user_account a JOIN c4_email_credential c ON c.user_id=a.user_id
                JOIN c4_user_profile p ON p.user_id=a.user_id JOIN c4_onboarding_journey j ON j.user_id=a.user_id
                WHERE a.user_id=?
                """, (rs, rowNum) -> new C4ApiDtos.MyMembership(rs.getString(1), maskEmail(rs.getString(2)), rs.getString(3), rs.getLong(4),
                new C4ApiDtos.OnboardingSummary(rs.getString(5), rs.getInt(7), rs.getLong(6))), userId);
    }

    @Transactional
    public C4ApiDtos.MyMembership updateNickname(UUID userId, C4ApiDtos.UpdateNicknameRequest request,
                                                 long expectedRevision, String idempotencyKey) {
        String normalized = normalizeNickname(request.nickname());
        String canonical = normalized + "\n" + expectedRevision;
        C4ApiDtos.MyMembership replay = replay(userId, "updateMyNickname", idempotencyKey, canonical, C4ApiDtos.MyMembership.class);
        if (replay != null) return replay;
        Profile profile = jdbc.queryForObject("SELECT revision,nickname_changed_at FROM c4_user_profile WHERE user_id=? FOR UPDATE",
                (rs, rowNum) -> new Profile(rs.getLong(1), rs.getTimestamp(2).toInstant()), userId);
        if (profile.revision() != expectedRevision) throw conflict("REVISION_CONFLICT", "최신 상태를 다시 확인해 주세요.");
        Instant now = dbNow();
        if (profile.changedAt().plus(30, ChronoUnit.DAYS).isAfter(now)) throw conflict("NICKNAME_CHANGE_COOLDOWN", "닉네임은 30일마다 변경할 수 있어요.");
        try {
            jdbc.update("UPDATE c4_user_profile SET nickname=?,nickname_normalized=?,revision=revision+1,nickname_changed_at=? WHERE user_id=?",
                    request.nickname().strip(), normalized, ts(now), userId);
        } catch (DuplicateKeyException exception) {
            throw conflict("NICKNAME_ALREADY_USED", "이미 사용 중인 닉네임이에요.");
        }
        C4ApiDtos.MyMembership result = membership(userId);
        store(userId, "updateMyNickname", idempotencyKey, canonical, 200, result);
        return result;
    }

    @Transactional(readOnly = true)
    public C4ApiDtos.OnboardingMoviePage onboardingMovies() {
        Policy policy = jdbc.queryForObject("SELECT policy_version,target_count FROM c4_local_selection_policy WHERE active=true",
                (rs, rowNum) -> new Policy(rs.getString(1), rs.getInt(2)));
        String catalogVersion = jdbc.queryForObject("SELECT public_version FROM catalog_version WHERE status='ACTIVE'", String.class);
        List<C4ApiDtos.OnboardingMovie> items = jdbc.query("""
                SELECT p.movie_id,COALESCE(l.title,p.original_title),p.poster_path
                FROM catalog_version v JOIN movie_catalog_projection p ON p.catalog_version_id=v.id
                LEFT JOIN movie_localization l ON l.catalog_version_id=p.catalog_version_id AND l.movie_id=p.movie_id AND l.locale='ko-KR'
                LEFT JOIN movie_search_document s ON s.catalog_version_id=p.catalog_version_id AND s.movie_id=p.movie_id
                WHERE v.status='ACTIVE' AND p.visibility_status='UI_READY' AND p.deleted=false
                ORDER BY COALESCE(s.popularity_score,0) DESC,p.movie_id LIMIT ?
                """, (rs, rowNum) -> new C4ApiDtos.OnboardingMovie(rs.getObject(1, UUID.class), rs.getString(2),
                rs.getString(3) == null ? null : "https://image.tmdb.org/t/p/w500" + rs.getString(3)), policy.targetCount());
        return new C4ApiDtos.OnboardingMoviePage(catalogVersion, policy.version(), policy.targetCount(), items);
    }

    @Transactional
    public C4ApiDtos.OnboardingState replacePreferences(UUID userId, C4ApiDtos.ReplaceOnboardingPreferencesRequest request,
                                                        long expectedRevision, String idempotencyKey) {
        validateUniqueMovies(request.preferences());
        String canonical = request.catalogVersion() + "\n" + request.selectionPolicyVersion() + "\n" + request.preferences() + "\n" + expectedRevision;
        C4ApiDtos.OnboardingState replay = replay(userId, "replaceOnboardingPreferences", idempotencyKey, canonical, C4ApiDtos.OnboardingState.class);
        if (replay != null) return replay;
        Journey journey = lockJourney(userId);
        if (journey.revision() != expectedRevision) throw conflict("REVISION_CONFLICT", "최신 상태를 다시 확인해 주세요.");
        if (Set.of("COMPLETED", "SKIPPED").contains(journey.status()))
            throw conflict("ONBOARDING_ALREADY_TERMINAL", "온보딩은 이미 완료됐어요.");
        C4ApiDtos.OnboardingMoviePage page = onboardingMovies();
        if (!page.catalogVersion().equals(request.catalogVersion()) || !page.selectionPolicyVersion().equals(request.selectionPolicyVersion()))
            throw conflict("REVISION_CONFLICT", "영화 선택 목록이 갱신됐어요.");
        Set<UUID> allowed = new HashSet<>(); page.items().forEach(item -> allowed.add(item.movieId()));
        if (!allowed.containsAll(request.preferences().stream().map(C4ApiDtos.OnboardingPreferenceInput::movieId).toList()))
            throw bad("VALIDATION_ERROR", "온보딩 영화 목록을 확인해 주세요.");
        jdbc.update("DELETE FROM c4_onboarding_preference WHERE user_id=?", userId);
        Instant now = dbNow();
        for (var preference : request.preferences()) {
            jdbc.update("INSERT INTO c4_onboarding_preference(user_id,movie_id,preference,selected_at) VALUES (?,?,?,?)",
                    userId, preference.movieId(), preference.preference().name(), ts(now));
        }
        jdbc.update("UPDATE c4_onboarding_journey SET journey_status=?,catalog_version=?,selection_policy_version=?,revision=revision+1,updated_at=? WHERE user_id=?",
                request.preferences().isEmpty() ? "NOT_STARTED" : "IN_PROGRESS", request.catalogVersion(), request.selectionPolicyVersion(), ts(now), userId);
        C4ApiDtos.OnboardingState result = onboardingState(userId);
        store(userId, "replaceOnboardingPreferences", idempotencyKey, canonical, 200, result);
        return result;
    }

    @Transactional
    public C4ApiDtos.OnboardingState completeOnboarding(UUID userId, C4ApiDtos.CompleteOnboardingRequest request,
                                                       long expectedRevision, String idempotencyKey) {
        String canonical = request.completionMode() + "\n" + request.expectedPreferenceCount() + "\n" + expectedRevision;
        C4ApiDtos.OnboardingState replay = replay(userId, "completeOnboarding", idempotencyKey, canonical, C4ApiDtos.OnboardingState.class);
        if (replay != null) return replay;
        Journey journey = lockJourney(userId);
        if (journey.revision() != expectedRevision) throw conflict("REVISION_CONFLICT", "최신 상태를 다시 확인해 주세요.");
        if (Set.of("COMPLETED", "SKIPPED").contains(journey.status()))
            throw conflict("ONBOARDING_ALREADY_TERMINAL", "온보딩은 이미 완료됐어요.");
        Integer count = jdbc.queryForObject("SELECT count(*) FROM c4_onboarding_preference WHERE user_id=?", Integer.class, userId);
        if (count == null || count != request.expectedPreferenceCount()) throw conflict("REVISION_CONFLICT", "선택 수가 변경됐어요.");
        String status;
        if (request.completionMode() == C4ApiDtos.CompletionMode.SKIPPED) {
            if (count != 0) throw bad("VALIDATION_ERROR", "선택이 있으면 건너뛸 수 없어요.");
            status = "SKIPPED";
        } else {
            if (count < 1 || count > 10) throw bad("VALIDATION_ERROR", "영화를 1개 이상 선택해 주세요.");
            status = "COMPLETED";
        }
        jdbc.update("UPDATE c4_onboarding_journey SET journey_status=?,revision=revision+1,updated_at=? WHERE user_id=?",
                status, ts(dbNow()), userId);
        C4ApiDtos.OnboardingState result = onboardingState(userId);
        store(userId, "completeOnboarding", idempotencyKey, canonical, 200, result);
        return result;
    }

    @Transactional(readOnly = true)
    public C4ApiDtos.MyOttSubscriptionSet ottSubscriptions(UUID userId) {
        return jdbc.queryForObject("SELECT selection_status,revision FROM c4_ott_subscription_set WHERE user_id=?",
                (rs, rowNum) -> new C4ApiDtos.MyOttSubscriptionSet("KR", rs.getString(1),
                        jdbc.query("SELECT provider_id FROM c4_user_ott_subscription WHERE user_id=? ORDER BY provider_id",
                                (providers, index) -> providers.getObject(1, UUID.class), userId), rs.getLong(2)), userId);
    }

    @Transactional
    public C4ApiDtos.MyOttSubscriptionSet replaceOttSubscriptions(UUID userId, C4ApiDtos.ReplaceOttSubscriptionsRequest request,
                                                                  long expectedRevision, String idempotencyKey) {
        if (new HashSet<>(request.providerIds()).size() != request.providerIds().size()) throw bad("VALIDATION_ERROR", "providerIds는 중복될 수 없어요.");
        if (request.selectionMode() == C4ApiDtos.OttSelectionMode.SKIPPED && !request.providerIds().isEmpty())
            throw bad("VALIDATION_ERROR", "건너뛰기에는 providerIds를 보낼 수 없어요.");
        String canonical = request.selectionMode() + "\n" + request.providerIds() + "\n" + expectedRevision;
        C4ApiDtos.MyOttSubscriptionSet replay = replay(userId, "replaceMyOttSubscriptions", idempotencyKey, canonical,
                C4ApiDtos.MyOttSubscriptionSet.class);
        if (replay != null) return replay;
        Long revision = jdbc.queryForObject("SELECT revision FROM c4_ott_subscription_set WHERE user_id=? FOR UPDATE", Long.class, userId);
        if (revision == null || revision != expectedRevision) throw conflict("REVISION_CONFLICT", "최신 상태를 다시 확인해 주세요.");
        for (UUID providerId : request.providerIds()) {
            Integer exists = jdbc.query("SELECT 1 FROM ott_provider WHERE id=? AND active=true", rs -> rs.next() ? 1 : null, providerId);
            if (exists == null) throw new ApiException(HttpStatus.NOT_FOUND, "OTT_PROVIDER_NOT_FOUND", "OTT를 찾을 수 없어요.");
        }
        jdbc.update("DELETE FROM c4_user_ott_subscription WHERE user_id=?", userId);
        Instant now = dbNow();
        for (UUID providerId : request.providerIds()) jdbc.update("INSERT INTO c4_user_ott_subscription(user_id,provider_id,selected_at) VALUES (?,?,?)", userId, providerId, ts(now));
        jdbc.update("UPDATE c4_ott_subscription_set SET selection_status=?,revision=revision+1,updated_at=? WHERE user_id=?",
                request.selectionMode().name(), ts(now), userId);
        C4ApiDtos.MyOttSubscriptionSet result = ottSubscriptions(userId);
        store(userId, "replaceMyOttSubscriptions", idempotencyKey, canonical, 200, result);
        return result;
    }

    @Transactional(readOnly = true)
    public C4ApiDtos.OnboardingState onboardingState(UUID userId) {
        return jdbc.queryForObject("""
                SELECT j.journey_status,j.revision,j.recommendation_projection,
                       count(p.movie_id),count(p.movie_id) FILTER (WHERE p.preference='LIKE'),count(p.movie_id) FILTER (WHERE p.preference='DISLIKE')
                FROM c4_onboarding_journey j LEFT JOIN c4_onboarding_preference p ON p.user_id=j.user_id
                WHERE j.user_id=? GROUP BY j.journey_status,j.revision,j.recommendation_projection
                """, (rs, rowNum) -> new C4ApiDtos.OnboardingState(rs.getString(1), rs.getInt(4), rs.getInt(5), rs.getInt(6),
                1, 10, rs.getLong(2), rs.getString(3)), userId);
    }

    private C4ApiDtos.AuthenticationEnvelope createSession(UUID userId) {
        Instant now = dbNow();
        UUID sessionId = UUID.randomUUID();
        String refresh = crypto.randomToken();
        String csrf = crypto.randomToken();
        jdbc.update("INSERT INTO c4_auth_session(session_id,user_id,session_status,csrf_sha256,current_generation,created_at,idle_expires_at,absolute_expires_at) VALUES (?,?,'ACTIVE',?,1,?,?,?)",
                sessionId, userId, crypto.sha256(csrf), ts(now), ts(now.plus(7, ChronoUnit.DAYS)), ts(now.plus(30, ChronoUnit.DAYS)));
        jdbc.update("INSERT INTO c4_refresh_token(token_id,session_id,generation,token_sha256,csrf_sha256,token_status,created_at,expires_at) VALUES (?,?,1,?,?,'ACTIVE',?,?)",
                UUID.randomUUID(), sessionId, crypto.sha256(refresh), crypto.sha256(csrf), ts(now), ts(now.plus(7, ChronoUnit.DAYS)));
        var result = new C4ApiDtos.AuthenticationResult("Bearer", jwt.issue(userId, sessionId, now), 600, membership(userId));
        return new C4ApiDtos.AuthenticationEnvelope(result, refresh, csrf);
    }

    private UUID createChallenge(UUID signupId, UUID userId, int version, Instant expires, Instant now) {
        UUID challengeId = UUID.randomUUID();
        String secret = crypto.randomToken();
        jdbc.update("INSERT INTO c4_email_verification_challenge(challenge_id,signup_id,user_id,challenge_version,secret_sha256,challenge_status,expires_at,created_at) VALUES (?,?,?,?,?,'ACTIVE',?,?)",
                challengeId, signupId, userId, version, crypto.sha256(secret), ts(expires), ts(now));
        jdbc.update("UPDATE c4_email_signup_flow SET current_challenge_id=? WHERE signup_id=?", challengeId, signupId);
        C4Crypto.Encrypted encrypted = crypto.encrypt(secret, challengeId.toString().getBytes(StandardCharsets.US_ASCII));
        UUID materialId = UUID.randomUUID();
        jdbc.update("INSERT INTO c4_verification_delivery_material(material_id,challenge_id,ciphertext,nonce,key_version,expires_at,created_at) VALUES (?,?,?,?,?,?,?)",
                materialId, challengeId, encrypted.ciphertext(), encrypted.nonce(), properties.deliveryKeyVersion(), ts(expires), ts(now));
        jdbc.update("INSERT INTO c4_mail_outbox(outbox_id,challenge_id,material_id,delivery_status,created_at) VALUES (?,?,?,'PENDING',?)",
                UUID.randomUUID(), challengeId, materialId, ts(now));
        return materialId;
    }

    private void dispatchAfterCommit(UUID materialId) {
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override public void afterCommit() { dispatch(materialId); }
        });
    }

    private void dispatch(UUID materialId) {
        if (mailGateways.isEmpty()) return;
        Delivery delivery = jdbc.query("""
                SELECT m.ciphertext,m.nonce,ch.challenge_id,ch.signup_id,c.email_normalized
                FROM c4_verification_delivery_material m
                JOIN c4_email_verification_challenge ch ON ch.challenge_id=m.challenge_id
                JOIN c4_email_credential c ON c.user_id=ch.user_id WHERE m.material_id=?
                """, rs -> rs.next() ? new Delivery(rs.getBytes(1), rs.getBytes(2), rs.getObject(3, UUID.class),
                rs.getObject(4, UUID.class), rs.getString(5)) : null, materialId);
        if (delivery == null) return;
        try {
            String secret = crypto.decrypt(delivery.ciphertext(), delivery.nonce(), delivery.challengeId().toString().getBytes(StandardCharsets.US_ASCII));
            String link = properties.allowedOrigin() + "/verify-email?signupId=" + delivery.signupId()
                    + "#verificationSecret=" + URLEncoder.encode(secret, StandardCharsets.UTF_8);
            mailGateways.get(0).sendVerification(delivery.recipient(), link);
            jdbc.update("UPDATE c4_mail_outbox SET delivery_status='DELIVERED',attempt_count=attempt_count+1,delivered_at=clock_timestamp() WHERE material_id=?", materialId);
            jdbc.update("DELETE FROM c4_verification_delivery_material WHERE material_id=?", materialId);
        } catch (RuntimeException exception) {
            jdbc.update("UPDATE c4_mail_outbox SET delivery_status='FAILED',attempt_count=attempt_count+1,last_error_code='LOCAL_CAPTURE_FAILED' WHERE material_id=?", materialId);
        }
    }

    private <T> T replay(UUID scope, String operation, String key, String canonical, Class<T> type) {
        validateIdempotencyKey(key);
        Stored stored = jdbc.query("SELECT request_hmac,response_body::text FROM c4_idempotency_result WHERE scope_id=? AND operation_code=? AND idempotency_key=?",
                rs -> rs.next() ? new Stored(rs.getString(1), rs.getString(2)) : null, scope, operation, key);
        if (stored == null) return null;
        if (!constantHex(stored.requestHmac(), crypto.hmac(canonical))) throw conflict("IDEMPOTENCY_KEY_REUSED", "Idempotency-Key를 다른 요청에 재사용할 수 없어요.");
        try { return objectMapper.readValue(stored.responseBody(), type); }
        catch (Exception exception) { throw new IllegalStateException(exception); }
    }

    private boolean hasVoidReplay(UUID scope, String operation, String key, String canonical) {
        validateIdempotencyKey(key);
        Stored stored = jdbc.query("SELECT request_hmac,response_body::text FROM c4_idempotency_result WHERE scope_id=? AND operation_code=? AND idempotency_key=?",
                rs -> rs.next() ? new Stored(rs.getString(1), rs.getString(2)) : null, scope, operation, key);
        if (stored == null) return false;
        if (!constantHex(stored.requestHmac(), crypto.hmac(canonical))) throw conflict("IDEMPOTENCY_KEY_REUSED", "Idempotency-Key를 다른 요청에 재사용할 수 없어요.");
        return true;
    }

    private void store(UUID scope, String operation, String key, String canonical, int status, Object response) {
        try {
            jdbc.update("INSERT INTO c4_idempotency_result(scope_id,operation_code,idempotency_key,request_hmac,response_status,response_body,created_at) VALUES (?,?,?,?,?,?::jsonb,?)",
                    scope, operation, key, crypto.hmac(canonical), status, objectMapper.writeValueAsString(response), ts(dbNow()));
        } catch (DuplicateKeyException exception) {
            throw conflict("AUTH_STATE_CONFLICT", "동시에 처리된 요청을 다시 확인해 주세요.");
        } catch (Exception exception) {
            throw new IllegalStateException(exception);
        }
    }

    private void admit(String identity, int limit) {
        Instant now = dbNow();
        Instant window = now.truncatedTo(ChronoUnit.HOURS);
        Integer count = jdbc.queryForObject("""
                INSERT INTO c4_auth_rate_counter(counter_key,window_started_at,counter_value,expires_at)
                VALUES (?,?,1,?) ON CONFLICT(counter_key,window_started_at)
                DO UPDATE SET counter_value=c4_auth_rate_counter.counter_value+1 RETURNING counter_value
                """, Integer.class, crypto.hmac(identity), ts(window), ts(window.plus(1, ChronoUnit.HOURS)));
        if (count != null && count > limit) throw tooMany("AUTH_FLOW_THROTTLED", "잠시 후 다시 시도해 주세요.");
    }

    private AccountCredential credentialByEmail(String email, boolean lock) {
        String suffix = lock ? " FOR UPDATE" : "";
        return jdbc.query("SELECT a.user_id,a.membership_status,c.password_phc,a.pending_purge_at FROM c4_email_credential c JOIN c4_user_account a ON a.user_id=c.user_id WHERE c.email_normalized=?" + suffix,
                rs -> rs.next() ? new AccountCredential(rs.getObject(1, UUID.class), rs.getString(2), rs.getString(3), rs.getTimestamp(4).toInstant()) : null, email);
    }

    private Flow lockFlow(UUID signupId) {
        return jdbc.query("SELECT signup_id,flow_kind,user_id,email_masked,flow_status,current_challenge_id,failed_attempt_count,revision,flow_expires_at,resend_available_at FROM c4_email_signup_flow WHERE signup_id=? FOR UPDATE",
                rs -> rs.next() ? new Flow(rs.getObject(1, UUID.class), rs.getString(2), rs.getObject(3, UUID.class), rs.getString(4), rs.getString(5),
                        rs.getObject(6, UUID.class), rs.getInt(7), rs.getLong(8), rs.getTimestamp(9).toInstant(), rs.getTimestamp(10).toInstant()) : null, signupId);
    }

    private Refresh lockRefresh(String token) {
        return jdbc.query("""
                SELECT t.token_id,t.session_id,s.user_id,t.generation,t.token_status,s.session_status,t.csrf_sha256,
                       t.expires_at,s.absolute_expires_at,t.rotated_at,s.current_generation
                FROM c4_refresh_token t JOIN c4_auth_session s ON s.session_id=t.session_id
                WHERE t.token_sha256=? FOR UPDATE OF t,s
                """, rs -> rs.next() ? new Refresh(rs.getObject(1, UUID.class), rs.getObject(2, UUID.class), rs.getObject(3, UUID.class), rs.getInt(4),
                rs.getString(5), rs.getString(6), rs.getString(7), rs.getTimestamp(8).toInstant(), rs.getTimestamp(9).toInstant(),
                rs.getTimestamp(10) == null ? null : rs.getTimestamp(10).toInstant(), rs.getInt(11)) : null, crypto.sha256(token));
    }

    private void revokeRefreshFamily(UUID sessionId, Instant now) {
        jdbc.update("UPDATE c4_auth_session SET session_status='REVOKED', terminal_at=? WHERE session_id=? AND session_status='ACTIVE'", ts(now), sessionId);
        jdbc.update("UPDATE c4_refresh_token SET token_status='REVOKED' WHERE session_id=? AND token_status='ACTIVE'", sessionId);
    }

    private Journey lockJourney(UUID userId) {
        return jdbc.queryForObject("SELECT journey_status,revision FROM c4_onboarding_journey WHERE user_id=? FOR UPDATE",
                (rs, rowNum) -> new Journey(rs.getString(1), rs.getLong(2)), userId);
    }

    private void validateUniqueMovies(List<C4ApiDtos.OnboardingPreferenceInput> values) {
        if (values == null || values.size() > 10 || new HashSet<>(values.stream().map(C4ApiDtos.OnboardingPreferenceInput::movieId).toList()).size() != values.size())
            throw bad("VALIDATION_ERROR", "영화 선택은 중복 없이 최대 10개예요.");
    }

    private void requireOrigin(String origin) {
        if (!properties.allowedOrigin().equals(origin)) throw forbidden("AUTH_ORIGIN_FORBIDDEN", "허용되지 않은 Origin이에요.");
    }

    private void requireCompleteCookies(String refreshToken, String csrfCookie, String csrfHeader) {
        if (refreshToken == null || refreshToken.isBlank() || csrfCookie == null || csrfCookie.isBlank()
                || csrfHeader == null || csrfHeader.isBlank()) throw forbidden("CSRF_FORBIDDEN", "CSRF 검증에 실패했어요.");
    }

    private static String normalizeEmail(String email) { return email.strip().toLowerCase(Locale.ROOT); }
    private static String normalizeNickname(String nickname) {
        String normalized = Normalizer.normalize(nickname.strip(), Normalizer.Form.NFKC).toLowerCase(Locale.ROOT);
        if (!NICKNAME.matcher(normalized).matches()) throw bad("VALIDATION_ERROR", "닉네임 형식을 확인해 주세요.");
        return normalized;
    }
    private static void validatePassword(String password) {
        if (password == null || password.length() < 15 || password.length() > 128) throw bad("VALIDATION_ERROR", "비밀번호 길이를 확인해 주세요.");
    }
    private static String maskEmail(String email) {
        int at = email.indexOf('@');
        if (at <= 0) return "***";
        return email.substring(0, 1) + "***" + email.substring(at);
    }
    private UUID stableScope(String value) { return UUID.nameUUIDFromBytes(crypto.hmac(value).getBytes(StandardCharsets.US_ASCII)); }
    private Instant dbNow() { return jdbc.queryForObject("SELECT clock_timestamp()", Timestamp.class).toInstant(); }
    private static Timestamp ts(Instant value) { return Timestamp.from(value); }
    private static Instant min(Instant left, Instant right) { return left.isBefore(right) ? left : right; }
    private static boolean constantHex(String left, String right) { return java.security.MessageDigest.isEqual(left.getBytes(StandardCharsets.US_ASCII), right.getBytes(StandardCharsets.US_ASCII)); }
    private static void validateIdempotencyKey(String key) {
        if (key == null || key.length() < 8 || key.length() > 128 || key.chars().anyMatch(c -> c < 33 || c > 126))
            throw bad("VALIDATION_ERROR", "Idempotency-Key를 확인해 주세요.");
    }
    private static ApiException bad(String code, String message) { return new ApiException(HttpStatus.BAD_REQUEST, code, message); }
    private static ApiException unauthorized(String code, String message) { return new ApiException(HttpStatus.UNAUTHORIZED, code, message); }
    private static ApiException forbidden(String code, String message) { return new ApiException(HttpStatus.FORBIDDEN, code, message); }
    private static ApiException conflict(String code, String message) { return new ApiException(HttpStatus.CONFLICT, code, message); }
    private static ApiException tooMany(String code, String message) { return new ApiException(HttpStatus.TOO_MANY_REQUESTS, code, message); }

    private record AccountCredential(UUID userId, String status, String passwordPhc, Instant pendingPurgeAt) {}
    private record ExpiredFlow(UUID signupId, Instant flowExpiresAt) {}
    private record Flow(UUID signupId, String kind, UUID userId, String emailMasked, String status, UUID challengeId,
                        int failedAttempts, long revision, Instant flowExpiresAt, Instant resendAvailableAt) {}
    private record Refresh(UUID tokenId, UUID sessionId, UUID userId, int generation, String tokenStatus,
                           String sessionStatus, String csrfSha256, Instant expiresAt, Instant absoluteExpiresAt,
                           Instant rotatedAt, int currentGeneration) {}
    private record Profile(long revision, Instant changedAt) {}
    private record Journey(String status, long revision) {}
    private record Policy(String version, int targetCount) {}
    private record Delivery(byte[] ciphertext, byte[] nonce, UUID challengeId, UUID signupId, String recipient) {}
    private record Stored(String requestHmac, String responseBody) {}
}
