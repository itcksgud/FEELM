package com.feelm.catalog.security;

import com.feelm.catalog.api.ApiException;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.HttpMethod;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.request;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("fixture")
class C1RequiredAuthAcceptanceTest {
    private static final List<Operation> C1_OPERATIONS = List.of(
            new Operation(HttpMethod.POST, "/api/v1/watch-intents"),
            new Operation(HttpMethod.GET, "/api/v1/me/watch-intents/pending-confirmation"),
            new Operation(HttpMethod.POST, "/api/v1/watch-intents/2dfa8b82-9f40-452d-a63f-18347483f7b7/confirmation"),
            new Operation(HttpMethod.GET, "/api/v1/me/viewing-records/unrated"),
            new Operation(HttpMethod.GET, "/api/v1/me/ratings"),
            new Operation(HttpMethod.PUT, "/api/v1/me/ratings/6b226903-0ca4-4f5a-9bf0-50d6cedd224c"),
            new Operation(HttpMethod.DELETE, "/api/v1/me/ratings/6b226903-0ca4-4f5a-9bf0-50d6cedd224c"),
            new Operation(HttpMethod.GET, "/api/v1/me/film"),
            new Operation(HttpMethod.GET, "/api/v1/me/film/frames/2b480314-590c-4d9a-b5df-1ef745c15e76"),
            new Operation(HttpMethod.GET, "/api/v1/me/popcorn-bucket"),
            new Operation(HttpMethod.GET, "/api/v1/me/taste-profile")
    );

    @Autowired
    MockMvc mvc;

    @Autowired
    CatalogUserContextResolver resolver;

    @Autowired
    C1Ownership ownership;

    @Test
    void everyC1OperationRejectsMissingAndInvalidBearerTokens() throws Exception {
        for (Operation operation : C1_OPERATIONS) {
            mvc.perform(request(operation.method(), operation.path()))
                    .andExpect(status().isUnauthorized())
                    .andExpect(jsonPath("$.code").value("UNAUTHORIZED"))
                    .andExpect(jsonPath("$.traceId").isNotEmpty());
            mvc.perform(request(operation.method(), operation.path())
                            .header("Authorization", "Bearer test-c1-invalid-token"))
                    .andExpect(status().isUnauthorized())
                    .andExpect(jsonPath("$.code").value("UNAUTHORIZED"));
        }
    }

    @Test
    void fakeTokensMapToStableActorsAndOwnershipHidesOtherResources() {
        CatalogUserContext owner = resolver.resolveRequired("Bearer test-c1-owner-token");
        CatalogUserContext other = resolver.resolveRequired("Bearer test-c1-other-token");

        assertThat(owner.actorUserId()).isEqualTo(CatalogUserContextResolver.C1_OWNER);
        assertThat(other.actorUserId()).isEqualTo(CatalogUserContextResolver.C1_OTHER);
        assertThatCode(() -> ownership.requireOwner(owner.actorUserId(), owner.actorUserId()))
                .doesNotThrowAnyException();
        assertThatThrownBy(() -> ownership.requireOwner(owner.actorUserId(), other.actorUserId()))
                .isInstanceOf(ApiException.class)
                .extracting(exception -> ((ApiException) exception).code())
                .isEqualTo("RESOURCE_NOT_FOUND");
    }

    private record Operation(HttpMethod method, String path) {
    }
}
