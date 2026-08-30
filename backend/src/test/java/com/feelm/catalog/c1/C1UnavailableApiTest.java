package com.feelm.catalog.c1;

import com.feelm.catalog.api.GlobalExceptionHandler;
import com.feelm.catalog.api.TraceIdFilter;
import com.feelm.catalog.c1.api.C1Controller;
import com.feelm.catalog.c1.service.C1Service;
import com.feelm.catalog.security.C1RequiredAuthFilter;
import com.feelm.catalog.security.CatalogUserContextResolver;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.dao.DataAccessResourceFailureException;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import java.util.UUID;

import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(C1Controller.class)
@ActiveProfiles("local")
@Import({GlobalExceptionHandler.class, TraceIdFilter.class, C1RequiredAuthFilter.class, CatalogUserContextResolver.class})
class C1UnavailableApiTest {
    @Autowired
    MockMvc mvc;

    @MockitoBean
    C1Service service;

    @Test
    void c1DatabaseFailureUsesTheRatingService503Contract() throws Exception {
        when(service.popcornBucket(UUID.fromString("018f6826-4da1-7c38-a846-8f794cd8b0cf")))
                .thenThrow(new DataAccessResourceFailureException("database offline"));

        mvc.perform(get("/api/v1/me/popcorn-bucket")
                        .header("Authorization", "Bearer test-c1-owner-token"))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.code").value("RATING_SERVICE_UNAVAILABLE"))
                .andExpect(jsonPath("$.traceId").isNotEmpty())
                .andExpect(jsonPath("$.fieldErrors").isArray());
    }
}
