package com.feelm.catalog.c6;

import com.feelm.catalog.c2.recommendation.CandidateSetPort;
import com.feelm.catalog.c6.service.C6RecommenderPort;
import com.feelm.catalog.c6.service.PostgresC6ExperimentDataAdapter;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.annotation.Transactional;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("local")
@Testcontainers(disabledWithoutDocker = true)
@Transactional
class C6PostgresDataAdapterIntegrationTest {
    private static final UUID OWNER = UUID.fromString("018f6826-4da1-7c38-a846-8f794cd8b0cf");
    private static final UUID OTHER = UUID.fromString("5f93a51d-a6f1-41dc-8d86-6b570d53bd82");
    private static final UUID RATED = UUID.fromString("6b226903-0ca4-4f5a-9bf0-50d6cedd224c");
    private static final UUID ELIGIBLE = UUID.fromString("19406c31-213f-4fe1-93f6-109f8570ec20");
    private static final UUID THIRD = UUID.fromString("e8f7cf02-9bc4-4ff7-87b7-12fb02dd2490");

    @Container
    static final PostgreSQLContainer<?> POSTGRES = new PostgreSQLContainer<>("postgres:17-alpine")
            .withDatabaseName("feelm_c6_data_test");

    @DynamicPropertySource
    static void configure(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", POSTGRES::getJdbcUrl);
        registry.add("spring.datasource.username", POSTGRES::getUsername);
        registry.add("spring.datasource.password", POSTGRES::getPassword);
        registry.add("catalog.c6.local.enabled", () -> "true");
        registry.add("catalog.c1.watch-intent-scheduler-delay-ms", () -> "3600000");
        registry.add("catalog.c1.outbox-worker.enabled", () -> "false");
        registry.add("catalog.c4.enabled", () -> "false");
        registry.add("c5.local.enabled", () -> "false");
    }

    @Autowired
    PostgresC6ExperimentDataAdapter adapter;

    @Autowired
    MockMvc mvc;

    @Autowired
    org.springframework.jdbc.core.JdbcTemplate jdbc;

    @MockitoBean
    CandidateSetPort candidateSet;

    @MockitoBean
    C6RecommenderPort recommender;

    @BeforeEach
    void configureExperimentAdapters() {
        when(candidateSet.loadActive()).thenReturn(new CandidateSetPort.Snapshot(
                "candidate-v1", "catalog-fixture-20260829-01", "b".repeat(64),
                "fixture-family-v1", List.of(RATED, ELIGIBLE)
        ));
        when(recommender.interpret(any())).thenAnswer(invocation -> {
            C6RecommenderPort.Command command = invocation.getArgument(0);
            return new C6RecommenderPort.Result(
                    "c6-recommendation-interpretation-v2",
                    new C6RecommenderPort.Snapshot(
                            "artifact-set-v1", "policy-v1", command.inputVersion(),
                            "C6_MOST_RECENT_VALIDATED_K_FLOOR_V1",
                            "C6_DISCRETE_QUANTIZED_MIDRANK_ECDF_V2", 1, 1
                    ),
                    new C6RecommenderPort.RatingProfile(
                            1, java.math.BigDecimal.valueOf(4), java.math.BigDecimal.valueOf(4), "LOW"
                    ),
                    List.of(new C6RecommenderPort.Item(
                            ELIGIBLE, java.math.BigDecimal.valueOf(4.2), java.math.BigDecimal.valueOf(0.8),
                            true, "LOW", false
                    )),
                    List.of(
                            "LOCAL_EXPERIMENT_ONLY", "NOT_SELF_REPORTED_SATISFACTION",
                            "NOT_PRODUCT_DISPLAY_APPROVED", "K_BUCKETED_MOST_RECENT"
                    )
            );
        });
    }

    @Test
    void readsFreshActiveRatingsExcludesRatedCandidatesAndEnrichesFromActiveCatalog() {
        var snapshot = adapter.load(
                OWNER, "catalog-fixture-20260829-01", List.of(RATED, ELIGIBLE)
        );

        assertThat(snapshot.ratingsMostRecentFirst()).singleElement().satisfies(rating -> {
            assertThat(rating.movieId()).isEqualTo(RATED);
            assertThat(rating.value()).isEqualTo(4);
            assertThat(rating.revision()).isEqualTo(2);
        });
        assertThat(snapshot.eligibleMovieIds()).containsExactly(ELIGIBLE);
        assertThat(snapshot.movies()).containsOnlyKeys(ELIGIBLE);
        assertThat(snapshot.movies().get(ELIGIBLE)).satisfies(movie -> {
            assertThat(movie.title()).isEqualTo("The English Fallback");
            assertThat(movie.releaseYear()).isEqualTo(2018);
            assertThat(movie.genres()).containsExactly("드라마");
        });
        assertThat(snapshot.tasteAggregates()).hasSize(3).allSatisfy(evidence -> {
            assertThat(evidence.ratingCount()).isEqualTo(1);
            assertThat(evidence.ratingSum()).isEqualTo(4);
            assertThat(evidence.displayName()).isNotBlank();
        });
    }

    @Test
    void noRatingActorKeepsCandidatesAndHasNoTasteEvidence() {
        var snapshot = adapter.load(
                OTHER, "catalog-fixture-20260829-01", List.of(RATED, ELIGIBLE)
        );
        assertThat(snapshot.ratingsMostRecentFirst()).isEmpty();
        assertThat(snapshot.eligibleMovieIds()).containsExactly(RATED, ELIGIBLE);
        assertThat(snapshot.movies()).containsOnlyKeys(RATED, ELIGIBLE);
        assertThat(snapshot.tasteAggregates()).isEmpty();
    }

    @Test
    void activeRatingsAreSentMostRecentlyUpdatedFirstWithMovieIdTieBreakContract() {
        jdbc.update("""
                INSERT INTO rating (
                    id, user_id, movie_id, viewing_record_id, value, logical_status, revision,
                    created_at, updated_at, deleted_at, deletion_trace_id
                ) VALUES (?, ?, ?, ?, 2, 'ACTIVE', 1,
                          '2026-08-25T00:00:00Z', '2026-08-25T00:00:00Z', NULL, NULL)
                """,
                UUID.fromString("61b230d5-c3fc-46c9-83df-82a7e92937fc"), OWNER, ELIGIBLE,
                UUID.fromString("531a4e1d-2da8-48f1-a702-79fd875793d3"));

        var snapshot = adapter.load(
                OWNER, "catalog-fixture-20260829-01", List.of(RATED, ELIGIBLE, THIRD)
        );
        assertThat(snapshot.ratingsMostRecentFirst())
                .extracting(com.feelm.catalog.c2.input.ActiveRatingInputPort.RatingInput::movieId)
                .containsExactly(ELIGIBLE, RATED);
        assertThat(snapshot.eligibleMovieIds()).containsExactly(THIRD);
    }

    @Test
    void authenticatedLoopbackHttpResponseIsPrivateAndKeepsEvidenceNonDiagnostic() throws Exception {
        mvc.perform(get("/api/v1/me/recommendation-interpretation-experiment"))
                .andExpect(status().isUnauthorized())
                .andExpect(header().string("Cache-Control", "no-store, private"))
                .andExpect(header().string("Referrer-Policy", "no-referrer"));

        mvc.perform(get("/api/v1/me/recommendation-interpretation-experiment")
                        .header("Authorization", "Bearer test-c1-owner-token")
                        .header("Origin", "http://127.0.0.1:5173"))
                .andExpect(status().isOk())
                .andExpect(header().string("Cache-Control", "no-store, private"))
                .andExpect(header().string("Referrer-Policy", "no-referrer"))
                .andExpect(jsonPath("$.modelContext.usedRatingCount").value(1))
                .andExpect(jsonPath("$.predictions[0].movie.movieId").value(ELIGIBLE.toString()))
                .andExpect(jsonPath("$.predictions[0].displayEligible").value(false))
                .andExpect(jsonPath("$.tasteEvidence.length()").value(3))
                .andExpect(jsonPath("$.tasteEvidence[0].confidence").value("INSUFFICIENT_DATA"));
    }
}
