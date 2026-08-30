package com.feelm.catalog.c4;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.feelm.catalog.c4.mail.C4MailGateway;
import jakarta.servlet.http.Cookie;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Primary;
import org.springframework.http.HttpHeaders;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.util.List;
import java.net.URI;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.clearInvocations;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest(properties = {
        "spring.flyway.locations=classpath:db/migration,classpath:db/local",
        "spring.flyway.out-of-order=true",
        "catalog.c4.enabled=true",
        "catalog.c4.local-profile=true",
        "catalog.c4.allowed-origin=http://127.0.0.1:5173",
        "catalog.c4.delivery-key-base64=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        "catalog.c4.mail.enabled=false",
        "catalog.auth-mode=fake"
})
@AutoConfigureMockMvc
@ActiveProfiles("local")
@Testcontainers(disabledWithoutDocker = true)
class C4LocalVerticalPostgresAcceptanceTest {
    private static final String ORIGIN = "http://127.0.0.1:5173";
    private static final String EMAIL = "c4.local@example.test";
    private static final String PASSWORD = "correct-horse-local-password";

    @Container
    static final PostgreSQLContainer<?> POSTGRES = new PostgreSQLContainer<>("postgres:17.6-alpine")
            .withDatabaseName("feelm")
            .withUsername("feelm")
            .withPassword("feelm_local");

    @DynamicPropertySource
    static void database(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", POSTGRES::getJdbcUrl);
        registry.add("spring.datasource.username", POSTGRES::getUsername);
        registry.add("spring.datasource.password", POSTGRES::getPassword);
    }

    @Autowired MockMvc mvc;
    @Autowired ObjectMapper objectMapper;
    @Autowired C4MailGateway mailGateway;
    @Autowired JdbcTemplate jdbc;

    @Test
    void signupVerifyLoginRefreshLogoutAndProtectedSlicesShareTheC4Actor() throws Exception {
        clearInvocations(mailGateway);
        String signupBody = """
                {"email":"c4.local@example.test","password":"correct-horse-local-password","nickname":"c4_local"}
                """;
        String first = mvc.perform(post("/api/v1/auth/sign-up")
                        .header("Idempotency-Key", "dddddddddddddddd")
                        .contentType("application/json").content(signupBody))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.membershipStatus").value("PENDING_EMAIL_VERIFICATION"))
                .andReturn().getResponse().getContentAsString();
        String replay = mvc.perform(post("/api/v1/auth/sign-up")
                        .header("Idempotency-Key", "dddddddddddddddd")
                        .contentType("application/json").content(signupBody))
                .andExpect(status().isAccepted()).andReturn().getResponse().getContentAsString();
        assertThat(replay).isEqualTo(first);

        JsonNode pending = objectMapper.readTree(first);
        ArgumentCaptor<String> link = ArgumentCaptor.forClass(String.class);
        verify(mailGateway, times(1)).sendVerification(eq(EMAIL), link.capture());
        URI verificationLink = URI.create(link.getValue());
        assertThat(verificationLink.getScheme() + "://" + verificationLink.getAuthority() + verificationLink.getPath())
                .isEqualTo(ORIGIN + "/verify-email");
        assertThat(verificationLink.getQuery()).isEqualTo("signupId=" + pending.path("signupId").asText());
        assertThat(verificationLink.getFragment()).startsWith("verificationSecret=");
        String secret = URLDecoder.decode(verificationLink.getFragment().substring("verificationSecret=".length()),
                StandardCharsets.UTF_8);

        mvc.perform(post("/api/v1/auth/email-verifications")
                        .header("Idempotency-Key", "eeeeeeeeeeeeeeee")
                        .contentType("application/json")
                        .content("{\"signupId\":\"" + pending.path("signupId").asText() + "\",\"verificationSecret\":\"" + secret + "\"}"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.membershipStatus").value("ACTIVE"));

        var login = mvc.perform(post("/api/v1/auth/login").header("Origin", ORIGIN)
                        .contentType("application/json")
                        .content("{\"email\":\"" + EMAIL + "\",\"password\":\"" + PASSWORD + "\"}"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.tokenType").value("Bearer"))
                .andReturn().getResponse();
        JsonNode loginBody = objectMapper.readTree(login.getContentAsString());
        String access = loginBody.path("accessToken").asText();
        List<String> loginCookies = login.getHeaders(HttpHeaders.SET_COOKIE);
        assertThat(loginCookies).hasSize(2);
        String refresh = cookie(loginCookies, "feelm_local_refresh");
        String csrf = cookie(loginCookies, "feelm_local_csrf");

        mvc.perform(get("/api/v1/me").header("Authorization", "Bearer " + access))
                .andExpect(status().isOk()).andExpect(jsonPath("$.nickname").value("c4_local"));
        mvc.perform(get("/api/v1/me/ratings").header("Authorization", "Bearer " + access))
                .andExpect(status().isOk());
        // Authentication must reach C2B as the same actor. This bounded test
        // intentionally does not start the recommender process, so the service
        // boundary is expected to fail as unavailable rather than as 401.
        mvc.perform(get("/api/v1/me/recommendations/personal-discovery").header("Authorization", "Bearer " + access))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.code").value("RECOMMENDATION_UNAVAILABLE"));

        JsonNode movies = objectMapper.readTree(mvc.perform(get("/api/v1/onboarding/movies")
                        .header("Authorization", "Bearer " + access))
                .andExpect(status().isOk()).andExpect(jsonPath("$.items").isNotEmpty())
                .andReturn().getResponse().getContentAsString());
        String movieId = movies.path("items").get(0).path("movieId").asText();
        mvc.perform(put("/api/v1/onboarding/preferences")
                        .header("Authorization", "Bearer " + access)
                        .header("Idempotency-Key", "ffffffffffffffff")
                        .header("X-Expected-Revision", "1")
                        .contentType("application/json")
                        .content("{\"catalogVersion\":\"" + movies.path("catalogVersion").asText()
                                + "\",\"selectionPolicyVersion\":\"" + movies.path("selectionPolicyVersion").asText()
                                + "\",\"preferences\":[{\"movieId\":\"" + movieId + "\",\"preference\":\"LIKE\"}]}"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.status").value("IN_PROGRESS"));
        mvc.perform(post("/api/v1/onboarding/complete")
                        .header("Authorization", "Bearer " + access)
                        .header("Idempotency-Key", "c4-complete-vertical-1")
                        .header("X-Expected-Revision", "2")
                        .contentType("application/json")
                        .content("{\"completionMode\":\"SUBMITTED\",\"expectedPreferenceCount\":1}"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.status").value("COMPLETED"));
        mvc.perform(put("/api/v1/onboarding/preferences")
                        .header("Authorization", "Bearer " + access)
                        .header("Idempotency-Key", "c4-pref-rerun-blocked")
                        .header("X-Expected-Revision", "3")
                        .contentType("application/json")
                        .content("{\"catalogVersion\":\"" + movies.path("catalogVersion").asText()
                                + "\",\"selectionPolicyVersion\":\"" + movies.path("selectionPolicyVersion").asText()
                                + "\",\"preferences\":[{\"movieId\":\"" + movieId + "\",\"preference\":\"DISLIKE\"}]}"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("ONBOARDING_ALREADY_TERMINAL"));

        mvc.perform(get("/api/v1/me/ott-subscriptions").header("Authorization", "Bearer " + access))
                .andExpect(status().isOk()).andExpect(jsonPath("$.selectionStatus").value("NOT_CONFIGURED"));
        mvc.perform(put("/api/v1/me/ott-subscriptions")
                        .header("Authorization", "Bearer " + access)
                        .header("Idempotency-Key", "c4-ott-vertical-00001")
                        .header("X-Expected-Revision", "1")
                        .contentType("application/json")
                        .content("{\"selectionMode\":\"CONFIGURED\",\"providerIds\":[\"d392a4d5-0428-4e06-aa41-aef899c06842\"]}"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.selectionStatus").value("CONFIGURED"));

        var refreshed = mvc.perform(post("/api/v1/auth/refresh")
                        .header("Origin", ORIGIN).header("X-CSRF-Token", csrf)
                        .cookie(new Cookie("feelm_local_refresh", refresh), new Cookie("feelm_local_csrf", csrf)))
                .andExpect(status().isOk()).andReturn().getResponse();
        String nextAccess = objectMapper.readTree(refreshed.getContentAsString()).path("accessToken").asText();
        String nextRefresh = cookie(refreshed.getHeaders(HttpHeaders.SET_COOKIE), "feelm_local_refresh");
        String nextCsrf = cookie(refreshed.getHeaders(HttpHeaders.SET_COOKIE), "feelm_local_csrf");
        mvc.perform(post("/api/v1/auth/refresh").header("Origin", ORIGIN).header("X-CSRF-Token", csrf)
                        .cookie(new Cookie("feelm_local_refresh", refresh), new Cookie("feelm_local_csrf", csrf)))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("REFRESH_RACE_RETRY_NEW_COOKIE"));

        mvc.perform(post("/api/v1/auth/logout").header("Origin", ORIGIN).header("X-CSRF-Token", nextCsrf)
                        .header("Idempotency-Key", "c4-logout-vertical-1")
                        .cookie(new Cookie("feelm_local_refresh", nextRefresh), new Cookie("feelm_local_csrf", nextCsrf)))
                .andExpect(status().isNoContent()).andExpect(result -> assertThat(result.getResponse().getHeaders(HttpHeaders.SET_COOKIE)).hasSize(2));
        mvc.perform(post("/api/v1/auth/logout").header("Origin", ORIGIN).header("X-CSRF-Token", nextCsrf)
                        .header("Idempotency-Key", "c4-logout-vertical-1")
                        .cookie(new Cookie("feelm_local_refresh", nextRefresh), new Cookie("feelm_local_csrf", nextCsrf)))
                .andExpect(status().isNoContent());
        mvc.perform(get("/api/v1/me/ratings").header("Authorization", "Bearer " + nextAccess)).andExpect(status().isOk());
        mvc.perform(post("/api/v1/auth/refresh").header("Origin", ORIGIN).header("X-CSRF-Token", nextCsrf)
                        .cookie(new Cookie("feelm_local_refresh", nextRefresh), new Cookie("feelm_local_csrf", nextCsrf)))
                .andExpect(status().isUnauthorized()).andExpect(jsonPath("$.code").value("AUTH_SESSION_INVALID"));
    }

    @Test
    void expiredPendingSignupCanRecoverWithoutReplacingTheExistingNickname() throws Exception {
        clearInvocations(mailGateway);
        String email = "recover.local@example.test";
        String body = "{\"email\":\"" + email + "\",\"password\":\"" + PASSWORD
                + "\",\"nickname\":\"original_name\"}";
        JsonNode first = objectMapper.readTree(mvc.perform(post("/api/v1/auth/sign-up")
                        .header("Idempotency-Key", "c4-recover-create-0001")
                        .contentType("application/json").content(body))
                .andExpect(status().isAccepted()).andReturn().getResponse().getContentAsString());
        jdbc.update("UPDATE c4_email_signup_flow SET flow_expires_at=clock_timestamp()-interval '1 second' WHERE signup_id=?::uuid",
                first.path("signupId").asText());

        String recoveryBody = "{\"email\":\"" + email + "\",\"password\":\"" + PASSWORD
                + "\",\"nickname\":\"ignored_replacement\"}";
        JsonNode recovered = objectMapper.readTree(mvc.perform(post("/api/v1/auth/sign-up")
                        .header("Idempotency-Key", "c4-recover-create-0002")
                        .contentType("application/json").content(recoveryBody))
                .andExpect(status().isAccepted()).andReturn().getResponse().getContentAsString());

        assertThat(recovered.path("signupId").asText()).isNotEqualTo(first.path("signupId").asText());
        assertThat(jdbc.queryForObject("SELECT p.nickname FROM c4_user_profile p JOIN c4_email_credential c ON c.user_id=p.user_id WHERE c.email_normalized=?",
                String.class, email)).isEqualTo("original_name");
        verify(mailGateway, times(2)).sendVerification(eq(email), org.mockito.ArgumentMatchers.anyString());
    }

    @Test
    void originAndBlockedRouteBoundariesFailClosed() throws Exception {
        mvc.perform(post("/api/v1/auth/login").contentType("application/json")
                        .content("{\"email\":\"nobody@example.test\",\"password\":\"correct-horse-local-password\"}"))
                .andExpect(status().isForbidden()).andExpect(jsonPath("$.code").value("AUTH_ORIGIN_FORBIDDEN"));
        mvc.perform(post("/api/v1/auth/logout").header("Origin", ORIGIN))
                .andExpect(status().isNoContent());
        mvc.perform(post("/api/v1/onboarding/restart"))
                .andExpect(status().isNotFound());
        mvc.perform(post("/api/v1/auth/social/google"))
                .andExpect(status().isNotFound());
    }

    private static String cookie(List<String> headers, String name) {
        return headers.stream().filter(value -> value.startsWith(name + "="))
                .map(value -> value.substring(name.length() + 1, value.indexOf(';'))).findFirst().orElseThrow();
    }

    @TestConfiguration
    static class MailConfiguration {
        @Bean @Primary C4MailGateway testMailGateway() { return mock(C4MailGateway.class); }
    }
}
