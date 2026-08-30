package com.feelm.catalog.c3;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.feelm.catalog.c3.service.C3LoopbackGuard;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("local")
@Testcontainers(disabledWithoutDocker = true)
class C3LocalPostgresAcceptanceTest {
    private static final UUID OWNER = UUID.fromString("018f6826-4da1-7c38-a846-8f794cd8b0cf");
    private static final UUID MEMBER_A = UUID.fromString("4d85e2ae-87ce-4f48-8ac1-fabf89bb1371");
    private static final UUID MEMBER_B = UUID.fromString("bb5799ab-7654-4e01-8e0f-c1fe583d340d");
    private static final UUID MEMBER_C = UUID.fromString("85b0fa76-5b3e-4fcb-8846-807b466e757d");
    private static final UUID OTHER = UUID.fromString("83b8c4bd-7027-4b5a-86cc-82ccb574da64");
    private static final UUID UNKNOWN = UUID.fromString("99999999-9999-4999-8999-999999999999");
    private static final UUID NETFLIX = UUID.fromString("d392a4d5-0428-4e06-aa41-aef899c06842");
    private static final UUID WATCHA = UUID.fromString("4f57022d-6d8e-40b2-b7be-4ac313ef6bd0");
    private static final UUID COMMON_MOVIE = UUID.fromString("cc3ddb45-0511-46ea-bf28-95b67c9fd20f");
    private static final UUID MATERIALIZATION = UUID.fromString("30000000-0000-0000-0000-000000000001");

    @Container
    static final PostgreSQLContainer<?> POSTGRES = new PostgreSQLContainer<>("postgres:17-alpine")
            .withDatabaseName("feelm_c3_acceptance_test");

    @DynamicPropertySource
    static void configure(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", POSTGRES::getJdbcUrl);
        registry.add("spring.datasource.username", POSTGRES::getUsername);
        registry.add("spring.datasource.password", POSTGRES::getPassword);
        registry.add("catalog.c1.watch-intent-scheduler-delay-ms", () -> "3600000");
        registry.add("catalog.c1.outbox-worker.enabled", () -> "false");
        registry.add("catalog.c3.local-bind-address", () -> "127.0.0.1");
        registry.add("catalog.c3.enabled", () -> "true");
        registry.add("catalog.c4.enabled", () -> "false");
    }

    @Autowired MockMvc mvc;
    @Autowired ObjectMapper json;
    @Autowired JdbcTemplate jdbc;

    @BeforeEach
    void reset() {
        jdbc.update("DELETE FROM c3_idempotency_result");
        jdbc.update("DELETE FROM c3_ott_catalog_comparison");
        jdbc.update("DELETE FROM c3_party_invitation");
        jdbc.update("DELETE FROM c3_party");
        jdbc.update("UPDATE c3_availability_materialization SET status = 'COMPLETE' WHERE id = ?", MATERIALIZATION);
    }

    @AfterEach
    void restoreMaterialization() {
        jdbc.update("UPDATE c3_availability_materialization SET status = 'COMPLETE' WHERE id = ?", MATERIALIZATION);
    }

    @Test
    void localActorAndLoopbackBoundaryFailClosedAndPrivateResourcesStayHidden() throws Exception {
        mvc.perform(get("/api/v1/me/parties"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("LOCAL_ACTOR_UNAUTHORIZED"));
        mvc.perform(get("/api/v1/me/parties").header("X-Local-Actor-Id", UNKNOWN))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("LOCAL_ACTOR_UNAUTHORIZED"));
        mvc.perform(get("/api/v1/me/parties")
                        .header("X-Local-Actor-Id", OWNER).with(request -> {
                            request.setRemoteAddr("192.0.2.10");
                            return request;
                        }))
                .andExpect(status().isUnauthorized());

        assertThatThrownBy(() -> new C3LoopbackGuard("0.0.0.0", "").afterPropertiesSet())
                .isInstanceOf(IllegalStateException.class);

        JsonNode party = createParty(OWNER, "private-party-001", "private");
        mvc.perform(get("/api/v1/parties/{partyId}", party.path("partyId").asText())
                        .header("X-Local-Actor-Id", OTHER))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("RESOURCE_NOT_FOUND"))
                .andExpect(jsonPath("$.partyId").doesNotExist());
    }

    @Test
    void partyCreateReplayValidationAndReloadUseOneOwnerAggregate() throws Exception {
        JsonNode created = createParty(OWNER, "create-party-001", "영화 모임");
        assertThat(created.path("status").asText()).isEqualTo("DRAFT");
        assertThat(created.path("myRole").asText()).isEqualTo("OWNER");
        assertThat(created.path("memberCount").asInt()).isEqualTo(1);
        assertThat(created.path("maximumMemberCount").asInt()).isEqualTo(4);
        assertThat(created.path("revision").asInt()).isEqualTo(1);
        assertThat(created.path("providerIds")).hasSize(2);
        assertThat(created.path("members")).hasSize(1);

        JsonNode replay = json.readTree(mvc.perform(post("/api/v1/me/parties")
                        .header("X-Local-Actor-Id", OWNER)
                        .header("Idempotency-Key", "create-party-001")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(partyBody("영화 모임")))
                .andExpect(status().isCreated()).andReturn().getResponse().getContentAsString());
        assertThat(replay).isEqualTo(created);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM c3_party", Integer.class)).isEqualTo(1);

        mvc.perform(post("/api/v1/me/parties")
                        .header("X-Local-Actor-Id", OWNER)
                        .header("Idempotency-Key", "create-party-001")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(partyBody("다른 이름")))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("IDEMPOTENCY_KEY_REUSED"));
        mvc.perform(post("/api/v1/me/parties")
                        .header("X-Local-Actor-Id", OWNER)
                        .header("Idempotency-Key", "invalid-providers")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"name":"bad","providerIds":["%s","%s"]}
                                """.formatted(NETFLIX, NETFLIX)))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_ERROR"));
        mvc.perform(post("/api/v1/me/parties")
                        .header("X-Local-Actor-Id", OWNER)
                        .header("Idempotency-Key", "one-provider-invalid")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"name":"bad","providerIds":["%s"]}
                                """.formatted(NETFLIX)))
                .andExpect(status().isBadRequest());

        mvc.perform(get("/api/v1/me/parties").header("X-Local-Actor-Id", OWNER))
                .andExpect(status().isOk())
                .andExpect(header().string("Cache-Control", "no-store, private"))
                .andExpect(jsonPath("$.totalCount").value(1))
                .andExpect(jsonPath("$.items[0].partyId").value(created.path("partyId").asText()));
        mvc.perform(get("/api/v1/parties/{partyId}", created.path("partyId").asText())
                        .header("X-Local-Actor-Id", OWNER))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.revision").value(1));
    }

    @Test
    void invitationCreateAcceptReplayRevisionAndOwnershipAreAtomic() throws Exception {
        JsonNode party = createParty(OWNER, "invite-party-001", "초대 파티");
        UUID partyId = UUID.fromString(party.path("partyId").asText());
        JsonNode invitation = createInvitation(OWNER, partyId, MEMBER_A, 1, "invite-member-a");
        UUID invitationId = UUID.fromString(invitation.path("invitationId").asText());
        assertThat(invitation.path("status").asText()).isEqualTo("PENDING");
        assertThat(jdbc.queryForObject("SELECT revision FROM c3_party WHERE party_id = ?", Integer.class, partyId))
                .isEqualTo(2);

        JsonNode invitationReplay = createInvitation(OWNER, partyId, MEMBER_A, 1, "invite-member-a");
        assertThat(invitationReplay).isEqualTo(invitation);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM c3_party_invitation", Integer.class)).isEqualTo(1);
        mvc.perform(post("/api/v1/parties/{partyId}/invitations", partyId)
                        .header("X-Local-Actor-Id", OWNER)
                        .header("Idempotency-Key", "invite-member-a")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"recipientActorId":"%s","expectedPartyRevision":1}
                                """.formatted(MEMBER_B)))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("IDEMPOTENCY_KEY_REUSED"));
        mvc.perform(post("/api/v1/parties/{partyId}/invitations", partyId)
                        .header("X-Local-Actor-Id", OWNER)
                        .header("Idempotency-Key", "duplicate-member-a")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"recipientActorId":"%s","expectedPartyRevision":2}
                                """.formatted(MEMBER_A)))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("DUPLICATE_INVITATION"));
        for (UUID unavailable : List.of(OWNER, UNKNOWN)) {
            mvc.perform(post("/api/v1/parties/{partyId}/invitations", partyId)
                            .header("X-Local-Actor-Id", OWNER)
                            .header("Idempotency-Key", "unavailable-" + unavailable)
                            .contentType(MediaType.APPLICATION_JSON)
                            .content("""
                                    {"recipientActorId":"%s","expectedPartyRevision":2}
                                    """.formatted(unavailable)))
                    .andExpect(status().isBadRequest())
                    .andExpect(jsonPath("$.code").value("LOCAL_ACTOR_UNAVAILABLE"));
        }

        mvc.perform(get("/api/v1/parties/{partyId}", partyId).header("X-Local-Actor-Id", MEMBER_A))
                .andExpect(status().isNotFound());
        mvc.perform(post("/api/v1/me/party-invitations/{invitationId}/accept", invitationId)
                        .header("X-Local-Actor-Id", OTHER)
                        .header("Idempotency-Key", "not-recipient")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(acceptBody(2, 1)))
                .andExpect(status().isNotFound());
        mvc.perform(get("/api/v1/parties/{partyId}/invitations", partyId)
                        .header("X-Local-Actor-Id", OWNER))
                .andExpect(status().isOk()).andExpect(jsonPath("$.totalCount").value(1));
        mvc.perform(get("/api/v1/me/party-invitations").header("X-Local-Actor-Id", MEMBER_A))
                .andExpect(status().isOk()).andExpect(jsonPath("$.items[0].status").value("PENDING"));

        JsonNode accepted = accept(MEMBER_A, invitationId, 2, 1, "accept-member-a");
        assertThat(accepted.path("invitation").path("status").asText()).isEqualTo("ACCEPTED");
        assertThat(accepted.path("party").path("status").asText()).isEqualTo("ACTIVE");
        assertThat(accepted.path("party").path("memberCount").asInt()).isEqualTo(2);
        assertThat(accepted.path("party").path("revision").asInt()).isEqualTo(3);

        JsonNode replay = accept(MEMBER_A, invitationId, 2, 1, "accept-member-a");
        assertThat(replay).isEqualTo(accepted);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM c3_party_member WHERE party_id = ?", Integer.class, partyId))
                .isEqualTo(2);
        mvc.perform(post("/api/v1/me/party-invitations/{invitationId}/accept", invitationId)
                        .header("X-Local-Actor-Id", MEMBER_A)
                        .header("Idempotency-Key", "accept-member-a")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(acceptBody(2, 2)))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("IDEMPOTENCY_KEY_REUSED"));

        JsonNode second = createInvitation(OWNER, partyId, MEMBER_B, 3, "invite-member-b-stale");
        UUID secondId = UUID.fromString(second.path("invitationId").asText());
        mvc.perform(post("/api/v1/me/party-invitations/{invitationId}/accept", secondId)
                        .header("X-Local-Actor-Id", MEMBER_B)
                        .header("Idempotency-Key", "accept-member-b-stale")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(acceptBody(3, 1)))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("REVISION_CONFLICT"));
        assertThat(jdbc.queryForObject("SELECT status FROM c3_party_invitation WHERE invitation_id = ?",
                String.class, secondId)).isEqualTo("PENDING");
        assertThat(jdbc.queryForObject("SELECT count(*) FROM c3_party_member WHERE actor_id = ?",
                Integer.class, MEMBER_B)).isZero();
    }

    @Test
    void concurrentLastSeatAcceptHasOneWinnerAndFinishesAtFourMembers() throws Exception {
        JsonNode party = createParty(OWNER, "capacity-party-001", "정원 테스트");
        UUID partyId = UUID.fromString(party.path("partyId").asText());
        int revision = 1;
        revision = inviteAndAccept(partyId, MEMBER_A, revision, "cap-a");
        revision = inviteAndAccept(partyId, MEMBER_B, revision, "cap-b");
        JsonNode inviteC = createInvitation(OWNER, partyId, MEMBER_C, revision, "cap-invite-c");
        revision++;
        JsonNode inviteOther = createInvitation(OWNER, partyId, OTHER, revision, "cap-invite-other");
        revision++;
        int expectedRevision = revision;

        CountDownLatch ready = new CountDownLatch(2);
        CountDownLatch start = new CountDownLatch(1);
        ExecutorService executor = Executors.newFixedThreadPool(2);
        try {
            Future<Integer> first = executor.submit(() -> concurrentAccept(
                    MEMBER_C, UUID.fromString(inviteC.path("invitationId").asText()),
                    expectedRevision, "cap-accept-c", ready, start
            ));
            Future<Integer> second = executor.submit(() -> concurrentAccept(
                    OTHER, UUID.fromString(inviteOther.path("invitationId").asText()),
                    expectedRevision, "cap-accept-other", ready, start
            ));
            ready.await();
            start.countDown();
            assertThat(List.of(first.get(), second.get())).containsExactlyInAnyOrder(200, 409);
        } finally {
            executor.shutdownNow();
        }
        assertThat(jdbc.queryForObject("SELECT member_count FROM c3_party WHERE party_id = ?", Integer.class, partyId))
                .isEqualTo(4);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM c3_party_member WHERE party_id = ?", Integer.class, partyId))
                .isEqualTo(4);
        assertThat(jdbc.queryForObject("""
                SELECT count(*) FROM c3_party_invitation WHERE party_id = ? AND status = 'ACCEPTED'
                """, Integer.class, partyId)).isEqualTo(3);
    }

    @Test
    void immutableComparisonTraversesEveryActualMovieAndRejectsCursorReuse() throws Exception {
        JsonNode comparison = createComparison(OWNER, "compare-001", List.of(NETFLIX, WATCHA));
        UUID comparisonId = UUID.fromString(comparison.path("comparisonId").asText());
        assertThat(comparison.path("status").asText()).isEqualTo("READY");
        assertThat(comparison.path("region").asText()).isEqualTo("KR");
        assertThat(comparison.path("monetizationType").asText()).isEqualTo("FLATRATE");
        assertThat(comparison.path("providers")).hasSize(2);
        assertThat(providerCount(comparison, NETFLIX)).isEqualTo(3);
        assertThat(providerCount(comparison, WATCHA)).isEqualTo(2);

        JsonNode page1 = comparisonMovies(OWNER, comparisonId, NETFLIX, null, 2);
        assertThat(page1.path("items")).hasSize(2);
        assertThat(page1.path("hasNext").asBoolean()).isTrue();
        assertThat(page1.path("nextCursor").isTextual()).isTrue();
        JsonNode page2 = comparisonMovies(
                OWNER, comparisonId, NETFLIX, page1.path("nextCursor").asText(), 2
        );
        assertThat(page2.path("items")).hasSize(1);
        assertThat(page2.path("hasNext").asBoolean()).isFalse();
        assertThat(page2.path("nextCursor").isNull()).isTrue();
        List<String> netflixMovies = movieIds(page1, page2);
        assertThat(netflixMovies).hasSize(3).doesNotHaveDuplicates();

        JsonNode watcha = comparisonMovies(OWNER, comparisonId, WATCHA, null, 100);
        assertThat(watcha.path("items")).hasSize(2);
        JsonNode overlap = findMovie(watcha, COMMON_MOVIE);
        assertThat(overlap.path("availableProviderIds")).hasSize(2);
        assertThat(netflixMovies).contains(COMMON_MOVIE.toString());

        String cursor = page1.path("nextCursor").asText();
        mvc.perform(get("/api/v1/me/ott-catalog-comparisons/{comparisonId}/movies", comparisonId)
                        .header("X-Local-Actor-Id", OWNER)
                        .param("providerId", WATCHA.toString()).param("cursor", cursor).param("limit", "2"))
                .andExpect(status().isBadRequest()).andExpect(jsonPath("$.code").value("INVALID_CURSOR"));
        mvc.perform(get("/api/v1/me/ott-catalog-comparisons/{comparisonId}/movies", comparisonId)
                        .header("X-Local-Actor-Id", OWNER)
                        .param("providerId", NETFLIX.toString()).param("cursor", cursor + "x").param("limit", "2"))
                .andExpect(status().isBadRequest()).andExpect(jsonPath("$.code").value("INVALID_CURSOR"));
        mvc.perform(get("/api/v1/me/ott-catalog-comparisons/{comparisonId}", comparisonId)
                        .header("X-Local-Actor-Id", OTHER))
                .andExpect(status().isNotFound());

        JsonNode otherComparison = createComparison(OTHER, "compare-other-001", List.of(NETFLIX, WATCHA));
        mvc.perform(get("/api/v1/me/ott-catalog-comparisons/{comparisonId}/movies",
                        otherComparison.path("comparisonId").asText())
                        .header("X-Local-Actor-Id", OTHER)
                        .param("providerId", NETFLIX.toString()).param("cursor", cursor).param("limit", "2"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("INVALID_CURSOR"));

        JsonNode replay = createComparison(OWNER, "compare-001", List.of(WATCHA, NETFLIX));
        assertThat(replay).isEqualTo(comparison);
        assertThat(jdbc.queryForObject(
                "SELECT count(*) FROM c3_ott_catalog_comparison WHERE owner_actor_id = ?",
                Integer.class, OWNER
        )).isEqualTo(1);
    }

    @Test
    void missingCompleteMaterializationReturns503WithoutPartialRows() throws Exception {
        jdbc.update("UPDATE c3_availability_materialization SET status = 'FAILED' WHERE id = ?", MATERIALIZATION);
        mvc.perform(post("/api/v1/me/ott-catalog-comparisons")
                        .header("X-Local-Actor-Id", OWNER)
                        .header("Idempotency-Key", "comparison-no-materialization")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(comparisonBody(List.of(NETFLIX, WATCHA))))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.code").value("CATALOG_MATERIALIZATION_UNAVAILABLE"));
        assertThat(jdbc.queryForObject("SELECT count(*) FROM c3_ott_catalog_comparison", Integer.class)).isZero();
        assertThat(jdbc.queryForObject("SELECT count(*) FROM c3_ott_catalog_provider", Integer.class)).isZero();
        assertThat(jdbc.queryForObject("SELECT count(*) FROM c3_ott_catalog_movie", Integer.class)).isZero();
        assertThat(jdbc.queryForObject("SELECT count(*) FROM c3_idempotency_result", Integer.class)).isZero();
    }

    @Test
    void partyBaselineIsCoveragePopularityDeterministicAndContainsNoPersonalSignals() throws Exception {
        JsonNode ownerParty = createParty(OWNER, "baseline-owner-party", "기준 파티");
        UUID ownerPartyId = UUID.fromString(ownerParty.path("partyId").asText());
        JsonNode otherParty = createParty(OTHER, "baseline-other-party", "동일 기준 파티");
        UUID otherPartyId = UUID.fromString(otherParty.path("partyId").asText());

        JsonNode first = baseline(OWNER, ownerPartyId, null, 2);
        JsonNode second = baseline(OWNER, ownerPartyId, first.path("nextCursor").asText(), 2);
        List<String> titles = titles(first, second);
        assertThat(titles).containsExactly("프레스티지", "나우 유 씨 미", "인사이드 맨", "The English Fallback");
        assertThat(first.path("items").get(0).path("explanation").fieldNames())
                .toIterable().containsExactlyInAnyOrder(
                        "availableProviderCount", "selectedProviderCount", "catalogPopularityRank", "policyVersion"
                );
        assertThat(first.path("items").get(0).path("explanation").path("availableProviderCount").asInt())
                .isEqualTo(2);
        assertThat(first.path("policyVersion").asText()).isEqualTo("CATALOG_POPULARITY_KR_FLATRATE_V1");

        JsonNode other = baseline(OTHER, otherPartyId, null, 100);
        assertThat(titles(other)).containsExactlyElementsOf(titles);

        JsonNode invitation = createInvitation(OWNER, ownerPartyId, MEMBER_A, 1, "baseline-invite-a");
        UUID invitationId = UUID.fromString(invitation.path("invitationId").asText());
        mvc.perform(get("/api/v1/parties/{partyId}/baseline-recommendations", ownerPartyId)
                        .header("X-Local-Actor-Id", MEMBER_A))
                .andExpect(status().isNotFound());
        accept(MEMBER_A, invitationId, 2, 1, "baseline-accept-a");
        assertThat(titles(baseline(OWNER, ownerPartyId, null, 100))).containsExactlyElementsOf(titles);
        assertThat(titles(baseline(MEMBER_A, ownerPartyId, null, 100))).containsExactlyElementsOf(titles);

        String wire = first.toString().toLowerCase();
        for (String forbidden : List.of(
                "rating", "behavior", "expectedstar", "utility", "satisfaction", "fairness", "average", "balanced"
        )) {
            assertThat(wire).doesNotContain(forbidden);
        }
    }

    private JsonNode createParty(UUID actor, String key, String name) throws Exception {
        return json.readTree(mvc.perform(post("/api/v1/me/parties")
                        .header("X-Local-Actor-Id", actor)
                        .header("Idempotency-Key", key)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(partyBody(name)))
                .andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString());
    }

    private String partyBody(String name) {
        return """
                {"name":"%s","providerIds":["%s","%s"]}
                """.formatted(name, NETFLIX, WATCHA);
    }

    private JsonNode createInvitation(
            UUID actor, UUID partyId, UUID recipient, int expectedRevision, String key
    ) throws Exception {
        return json.readTree(mvc.perform(post("/api/v1/parties/{partyId}/invitations", partyId)
                        .header("X-Local-Actor-Id", actor)
                        .header("Idempotency-Key", key)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"recipientActorId":"%s","expectedPartyRevision":%d}
                                """.formatted(recipient, expectedRevision)))
                .andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString());
    }

    private JsonNode accept(UUID actor, UUID invitationId, int partyRevision, int invitationRevision, String key)
            throws Exception {
        return json.readTree(mvc.perform(post(
                                "/api/v1/me/party-invitations/{invitationId}/accept", invitationId)
                        .header("X-Local-Actor-Id", actor)
                        .header("Idempotency-Key", key)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(acceptBody(partyRevision, invitationRevision)))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString());
    }

    private String acceptBody(int partyRevision, int invitationRevision) {
        return """
                {"expectedPartyRevision":%d,"expectedInvitationRevision":%d}
                """.formatted(partyRevision, invitationRevision);
    }

    private int inviteAndAccept(UUID partyId, UUID member, int revision, String prefix) throws Exception {
        JsonNode invitation = createInvitation(OWNER, partyId, member, revision, prefix + "-invite");
        int afterInvite = revision + 1;
        accept(member, UUID.fromString(invitation.path("invitationId").asText()), afterInvite, 1,
                prefix + "-accept");
        return afterInvite + 1;
    }

    private int concurrentAccept(
            UUID actor,
            UUID invitation,
            int partyRevision,
            String key,
            CountDownLatch ready,
            CountDownLatch start
    ) throws Exception {
        ready.countDown();
        start.await();
        MvcResult result = mvc.perform(post("/api/v1/me/party-invitations/{invitationId}/accept", invitation)
                        .header("X-Local-Actor-Id", actor)
                        .header("Idempotency-Key", key)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(acceptBody(partyRevision, 1)))
                .andReturn();
        return result.getResponse().getStatus();
    }

    private JsonNode createComparison(UUID actor, String key, List<UUID> providers) throws Exception {
        return json.readTree(mvc.perform(post("/api/v1/me/ott-catalog-comparisons")
                        .header("X-Local-Actor-Id", actor)
                        .header("Idempotency-Key", key)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(comparisonBody(providers)))
                .andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString());
    }

    private String comparisonBody(List<UUID> providers) throws Exception {
        return json.writeValueAsString(java.util.Map.of("providerIds", providers));
    }

    private JsonNode comparisonMovies(UUID actor, UUID comparison, UUID provider, String cursor, int limit)
            throws Exception {
        var request = get("/api/v1/me/ott-catalog-comparisons/{comparisonId}/movies", comparison)
                .header("X-Local-Actor-Id", actor)
                .param("providerId", provider.toString()).param("limit", Integer.toString(limit));
        if (cursor != null) request.param("cursor", cursor);
        return json.readTree(mvc.perform(request).andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString());
    }

    private JsonNode baseline(UUID actor, UUID party, String cursor, int limit) throws Exception {
        var request = get("/api/v1/parties/{partyId}/baseline-recommendations", party)
                .header("X-Local-Actor-Id", actor).param("limit", Integer.toString(limit));
        if (cursor != null) request.param("cursor", cursor);
        return json.readTree(mvc.perform(request).andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString());
    }

    private int providerCount(JsonNode comparison, UUID provider) {
        for (JsonNode item : comparison.path("providers")) {
            if (provider.toString().equals(item.path("provider").path("providerId").asText())) {
                return item.path("movieCount").asInt();
            }
        }
        return -1;
    }

    private List<String> movieIds(JsonNode... pages) {
        List<String> values = new ArrayList<>();
        for (JsonNode page : pages) page.path("items").forEach(item ->
                values.add(item.path("movie").path("movieId").asText()));
        return values;
    }

    private JsonNode findMovie(JsonNode page, UUID movie) {
        for (JsonNode item : page.path("items")) {
            if (movie.toString().equals(item.path("movie").path("movieId").asText())) return item;
        }
        throw new AssertionError("movie missing from page");
    }

    private List<String> titles(JsonNode... pages) {
        List<String> values = new ArrayList<>();
        for (JsonNode page : pages) page.path("items").forEach(item ->
                values.add(item.path("movie").path("displayTitle").asText()));
        return values;
    }
}
