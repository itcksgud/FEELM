package com.feelm.catalog.security;

import com.feelm.catalog.api.GlobalExceptionHandler;
import com.feelm.catalog.api.TraceIdFilter;
import com.feelm.catalog.c1.api.C1ApiDtos;
import com.feelm.catalog.c1.api.C1Controller;
import com.feelm.catalog.c1.service.C1Service;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.system.CapturedOutput;
import org.springframework.boot.test.system.OutputCaptureExtension;
import org.springframework.context.annotation.Import;
import org.springframework.dao.DataAccessResourceFailureException;
import org.springframework.http.MediaType;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(C1Controller.class)
@ActiveProfiles("local")
@Import({GlobalExceptionHandler.class, TraceIdFilter.class, C1RequiredAuthFilter.class, CatalogUserContextResolver.class})
@ExtendWith(OutputCaptureExtension.class)
class C1SafeLoggingAcceptanceTest {
    private static final UUID MOVIE = UUID.fromString("6b226903-0ca4-4f5a-9bf0-50d6cedd224c");
    private static final String INVALID_AUTHORIZATION = "Bearer c1-invalid-log-canary-token";
    private static final String IDEMPOTENCY_KEY = "c1-safe-log-key";
    private static final String RAW_RATING_BODY = "{\"value\":5}";
    private static final String EMAIL_CANARY = "c1-log-canary@example.invalid";

    @Autowired
    MockMvc mvc;

    @MockitoBean
    C1Service service;

    @Test
    void authorizationAndRatingDatabaseFailureLogsExcludeSensitiveRequestValues(CapturedOutput output) throws Exception {
        mvc.perform(get("/api/v1/me/ratings").header("Authorization", INVALID_AUTHORIZATION))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("UNAUTHORIZED"));

        when(service.putRating(
                any(UUID.class),
                eq(MOVIE),
                eq(IDEMPOTENCY_KEY),
                any(C1ApiDtos.PutRatingRequest.class),
                anyString()
        )).thenThrow(new DataAccessResourceFailureException("database unavailable"));

        mvc.perform(put("/api/v1/me/ratings/{movieId}", MOVIE)
                        .header("Authorization", "Bearer test-c1-owner-token")
                        .header("Idempotency-Key", IDEMPOTENCY_KEY)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(RAW_RATING_BODY))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.code").value("RATING_SERVICE_UNAVAILABLE"));

        String logs = output.getAll();
        assertThat(logs)
                .doesNotContain(INVALID_AUTHORIZATION)
                .doesNotContain("test-c1-owner-token")
                .doesNotContain(IDEMPOTENCY_KEY)
                .doesNotContain(RAW_RATING_BODY)
                .doesNotContain("\"value\":5")
                .doesNotContain(CatalogUserContextResolver.C1_OWNER.toString())
                .doesNotContain(EMAIL_CANARY);
    }
}
