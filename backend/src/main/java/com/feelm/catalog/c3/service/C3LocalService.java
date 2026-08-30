package com.feelm.catalog.c3.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.feelm.catalog.api.ApiException;
import com.feelm.catalog.api.CatalogApiDtos;
import org.springframework.context.annotation.Profile;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.sql.Array;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Clock;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HexFormat;
import java.util.List;
import java.util.Objects;
import java.util.UUID;
import java.util.function.Supplier;

import static com.feelm.catalog.c3.api.C3ApiDtos.*;

@Service
@Profile("local")
@ConditionalOnProperty(name = "catalog.c3.enabled", havingValue = "true")
public final class C3LocalService {
    private static final int MAXIMUM_MEMBER_COUNT = 4;
    private static final String POLICY = "CATALOG_POPULARITY_KR_FLATRATE_V1";
    private static final String IMAGE_BASE = "https://image.tmdb.org/t/p/w500";

    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;
    private final Clock clock;
    private final C3CursorCodec cursors;
    private final PlatformTransactionManager transactionManager;

    public C3LocalService(
            JdbcTemplate jdbc,
            ObjectMapper objectMapper,
            Clock clock,
            C3CursorCodec cursors,
            PlatformTransactionManager transactionManager
    ) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
        this.clock = clock;
        this.cursors = cursors;
        this.transactionManager = transactionManager;
    }

    public UUID requireActor(String header) {
        UUID actorId;
        try {
            actorId = UUID.fromString(Objects.requireNonNull(header));
        } catch (Exception exception) {
            throw unauthorized();
        }
        Integer count = jdbc.queryForObject(
                "SELECT count(*) FROM c3_local_fake_actor WHERE actor_id = ? AND enabled",
                Integer.class,
                actorId
        );
        if (count == null || count != 1) throw unauthorized();
        return actorId;
    }

    public HttpMutation createParty(UUID actor, String key, CreatePartyRequest request) {
        List<UUID> providers = canonicalProviders(request.providerIds());
        String name = request.name().trim();
        ObjectNode canonical = objectMapper.createObjectNode().put("name", name);
        putUuids(canonical.putArray("providerIds"), providers);
        return transaction().execute(status -> idempotent(actor, "CREATE_PARTY", key, canonical, 201, () -> {
            Materialization materialization = completeMaterialization();
            requireScopedProviders(materialization.id(), providers);
            UUID partyId = UUID.randomUUID();
            OffsetDateTime now = now();
            jdbc.update("""
                    INSERT INTO c3_party (
                        party_id, owner_actor_id, name, status, member_count, revision, created_at, updated_at
                    ) VALUES (?, ?, ?, 'DRAFT', 1, 1, ?, ?)
                    """, partyId, actor, name, now, now);
            jdbc.update("""
                    INSERT INTO c3_party_member (party_id, member_id, actor_id, role, joined_at)
                    VALUES (?, ?, ?, 'OWNER', ?)
                    """, partyId, UUID.randomUUID(), actor, now);
            providers.forEach(provider -> jdbc.update(
                    "INSERT INTO c3_party_provider (party_id, provider_id) VALUES (?, ?)", partyId, provider
            ));
            return objectMapper.valueToTree(renderParty(requirePartyMember(partyId, actor, false), actor));
        }));
    }

    public PartyPage listMyParties(UUID actor, String cursor, int limit) {
        C3CursorCodec.Decoded decoded = cursors.decode(cursor, "MY_PARTIES", actor, List.of(), 2);
        List<Object> arguments = new ArrayList<>();
        String keyset = "";
        if (decoded != null) {
            keyset = " AND (p.created_at, p.party_id) < (?::timestamptz, ?::uuid)";
            arguments.add(decoded.lastKey().get(0));
            arguments.add(decoded.lastKey().get(1));
        }
        arguments.add(actor);
        arguments.add(limit + 1);
        List<PartyRow> rows = jdbc.query("""
                SELECT p.* FROM c3_party p
                JOIN c3_party_member pm ON pm.party_id = p.party_id
                WHERE pm.actor_id = ?
                """.replace("WHERE pm.actor_id = ?", "WHERE 1=1" + keyset + " AND pm.actor_id = ?")
                + " ORDER BY p.created_at DESC, p.party_id DESC LIMIT ?",
                (rs, row) -> partyRow(rs), arguments.toArray());
        int total = jdbc.queryForObject("""
                SELECT count(*) FROM c3_party p
                JOIN c3_party_member pm ON pm.party_id = p.party_id WHERE pm.actor_id = ?
                """, Integer.class, actor);
        boolean hasNext = rows.size() > limit;
        List<PartyRow> pageRows = hasNext ? rows.subList(0, limit) : rows;
        List<Party> items = pageRows.stream().map(row -> renderParty(row, actor)).toList();
        String next = hasNext ? cursors.encode("MY_PARTIES", actor, List.of(), List.of(
                pageRows.get(pageRows.size() - 1).createdAt().toString(),
                pageRows.get(pageRows.size() - 1).id().toString()
        )) : null;
        return new PartyPage(total, hasNext, next, items);
    }

    public Party getParty(UUID actor, UUID partyId) {
        return renderParty(requirePartyMember(partyId, actor, false), actor);
    }

    public HttpMutation createInvitation(
            UUID actor,
            UUID partyId,
            String key,
            CreatePartyInvitationRequest request
    ) {
        ObjectNode canonical = objectMapper.createObjectNode()
                .put("partyId", partyId.toString())
                .put("recipientActorId", request.recipientActorId().toString())
                .put("expectedPartyRevision", request.expectedPartyRevision());
        return transaction().execute(status -> idempotent(actor, "CREATE_PARTY_INVITATION", key, canonical, 201, () -> {
            PartyRow party = requirePartyOwner(partyId, actor, true);
            if (party.revision() != request.expectedPartyRevision()) throw revisionConflict();
            if (actor.equals(request.recipientActorId()) || !actorExists(request.recipientActorId())) {
                throw actorUnavailable();
            }
            if (isMember(partyId, request.recipientActorId())) throw duplicateInvitation();
            Integer pending = jdbc.queryForObject("""
                    SELECT count(*) FROM c3_party_invitation
                    WHERE party_id = ? AND recipient_actor_id = ? AND status = 'PENDING'
                    """, Integer.class, partyId, request.recipientActorId());
            if (pending != null && pending > 0) throw duplicateInvitation();
            UUID invitationId = UUID.randomUUID();
            OffsetDateTime now = now();
            jdbc.update("""
                    INSERT INTO c3_party_invitation (
                        invitation_id, party_id, inviter_actor_id, recipient_actor_id,
                        status, revision, created_at, accepted_at
                    ) VALUES (?, ?, ?, ?, 'PENDING', 1, ?, NULL)
                    """, invitationId, partyId, actor, request.recipientActorId(), now);
            jdbc.update("UPDATE c3_party SET revision = revision + 1, updated_at = ? WHERE party_id = ?",
                    now, partyId);
            return objectMapper.valueToTree(renderInvitation(requireInvitation(invitationId, false)));
        }));
    }

    public PartyInvitationPage listPartyInvitations(
            UUID actor,
            UUID partyId,
            String cursor,
            int limit
    ) {
        requirePartyOwner(partyId, actor, false);
        return invitationPage(actor, partyId, null, cursor, limit, "PARTY_INVITATIONS");
    }

    public PartyInvitationPage listMyInvitations(UUID actor, String cursor, int limit) {
        return invitationPage(actor, null, actor, cursor, limit, "MY_INVITATIONS");
    }

    public HttpMutation acceptInvitation(
            UUID actor,
            UUID invitationId,
            String key,
            AcceptPartyInvitationRequest request
    ) {
        UUID partyId = jdbc.query("""
                SELECT party_id FROM c3_party_invitation
                WHERE invitation_id = ? AND recipient_actor_id = ?
                """, (rs, row) -> rs.getObject(1, UUID.class), invitationId, actor).stream()
                .findFirst().orElseThrow(C3LocalService::notFound);
        ObjectNode canonical = objectMapper.createObjectNode()
                .put("invitationId", invitationId.toString())
                .put("expectedPartyRevision", request.expectedPartyRevision())
                .put("expectedInvitationRevision", request.expectedInvitationRevision());
        return transaction().execute(status -> {
            PartyRow party = requirePartyById(partyId, true);
            InvitationRow invitation = requireRecipientInvitation(invitationId, actor, true);
            jdbc.query("SELECT member_id FROM c3_party_member WHERE party_id = ? FOR UPDATE",
                    (rs, row) -> rs.getObject(1, UUID.class), partyId);
            lockIdempotency(actor, "ACCEPT_PARTY_INVITATION", key);
            StoredResult stored = stored(actor, "ACCEPT_PARTY_INVITATION", key);
            String requestHash = fingerprint(canonical);
            if (stored != null) return replay(stored, requestHash);
            if (party.revision() != request.expectedPartyRevision()
                    || invitation.revision() != request.expectedInvitationRevision()) {
                throw revisionConflict();
            }
            if (!"PENDING".equals(invitation.status())) throw invalidTransition();
            if (party.memberCount() >= MAXIMUM_MEMBER_COUNT) throw capacityReached();
            if (isMember(partyId, actor)) throw invalidTransition();
            OffsetDateTime now = now();
            jdbc.update("""
                    INSERT INTO c3_party_member (party_id, member_id, actor_id, role, joined_at)
                    VALUES (?, ?, ?, 'MEMBER', ?)
                    """, partyId, UUID.randomUUID(), actor, now);
            jdbc.update("""
                    UPDATE c3_party_invitation
                    SET status = 'ACCEPTED', revision = revision + 1, accepted_at = ?
                    WHERE invitation_id = ? AND status = 'PENDING'
                    """, now, invitationId);
            jdbc.update("""
                    UPDATE c3_party SET status = 'ACTIVE', member_count = member_count + 1,
                        revision = revision + 1, updated_at = ? WHERE party_id = ?
                    """, now, partyId);
            AcceptPartyInvitationResponse response = new AcceptPartyInvitationResponse(
                    renderInvitation(requireInvitation(invitationId, false)),
                    renderParty(requirePartyMember(partyId, actor, false), actor)
            );
            JsonNode body = objectMapper.valueToTree(response);
            store(actor, "ACCEPT_PARTY_INVITATION", key, requestHash, 200, body);
            return new HttpMutation(200, body, false);
        });
    }

    public HttpMutation createComparison(
            UUID actor,
            String key,
            CreateOttCatalogComparisonRequest request
    ) {
        List<UUID> providers = canonicalProviders(request.providerIds());
        ObjectNode canonical = objectMapper.createObjectNode();
        putUuids(canonical.putArray("providerIds"), providers);
        return transaction().execute(status -> idempotent(
                actor, "CREATE_OTT_CATALOG_COMPARISON", key, canonical, 201, () -> {
                    Materialization materialization = completeMaterialization();
                    requireScopedProviders(materialization.id(), providers);
                    UUID comparisonId = UUID.randomUUID();
                    OffsetDateTime now = now();
                    jdbc.update("""
                            INSERT INTO c3_ott_catalog_comparison (
                                comparison_id, owner_actor_id, materialization_id, status, created_at
                            ) VALUES (?, ?, ?, 'READY', ?)
                            """, comparisonId, actor, materialization.id(), now);
                    String selected = uuidArray(providers);
                    for (UUID provider : providers) {
                        List<SnapshotMovie> movies = snapshotMovies(materialization, provider, selected);
                        jdbc.update("""
                                INSERT INTO c3_ott_catalog_provider (comparison_id, provider_id, movie_count)
                                VALUES (?, ?, ?)
                                """, comparisonId, provider, movies.size());
                        for (SnapshotMovie movie : movies) {
                            jdbc.update("""
                                    INSERT INTO c3_ott_catalog_movie (
                                        comparison_id, provider_id, movie_id, display_title, poster_url,
                                        release_year, available_provider_ids, popularity_rank
                                    ) VALUES (?, ?, ?, ?, ?, ?, ?::uuid[], ?)
                                    """, comparisonId, provider, movie.movieId(), movie.title(), movie.posterUrl(),
                                    movie.releaseYear(), uuidArray(movie.providers()), movie.popularityRank());
                        }
                    }
                    return objectMapper.valueToTree(renderComparison(requireComparison(comparisonId, actor)));
                }
        ));
    }

    public OttCatalogComparison getComparison(UUID actor, UUID comparisonId) {
        return renderComparison(requireComparison(comparisonId, actor));
    }

    public CatalogMoviePage listComparisonMovies(
            UUID actor,
            UUID comparisonId,
            UUID providerId,
            String cursor,
            int limit
    ) {
        ComparisonRow comparison = requireComparison(comparisonId, actor);
        Integer total = jdbc.query("""
                SELECT movie_count FROM c3_ott_catalog_provider
                WHERE comparison_id = ? AND provider_id = ?
                """, (rs, row) -> rs.getInt(1), comparisonId, providerId).stream()
                .findFirst().orElseThrow(C3LocalService::notFound);
        List<String> bindings = List.of(
                comparisonId.toString(), providerId.toString(), comparison.materializationId().toString()
        );
        C3CursorCodec.Decoded decoded = cursors.decode(
                cursor, "COMPARISON_MOVIES", actor, bindings, 3
        );
        List<Object> arguments = new ArrayList<>(List.of(comparisonId, providerId));
        String keyset = "";
        if (decoded != null) {
            keyset = """
                     AND (popularity_rank, lower(display_title), movie_id)
                         > (?, ?, ?::uuid)
                    """;
            arguments.add(Integer.parseInt(decoded.lastKey().get(0)));
            arguments.add(decoded.lastKey().get(1));
            arguments.add(decoded.lastKey().get(2));
        }
        arguments.add(limit + 1);
        List<SnapshotMovie> rows = jdbc.query("""
                SELECT movie_id, display_title, poster_url, release_year,
                       available_provider_ids, popularity_rank
                FROM c3_ott_catalog_movie
                WHERE comparison_id = ? AND provider_id = ?
                """ + keyset + " ORDER BY popularity_rank, lower(display_title), movie_id LIMIT ?",
                (rs, row) -> snapshotMovie(rs), arguments.toArray());
        boolean hasNext = rows.size() > limit;
        List<SnapshotMovie> pageRows = hasNext ? rows.subList(0, limit) : rows;
        List<CatalogMovie> items = pageRows.stream().map(movie -> new CatalogMovie(
                movieSummary(movie), movie.providers()
        )).toList();
        String next = hasNext ? comparisonCursor(actor, bindings, pageRows.get(pageRows.size() - 1)) : null;
        return new CatalogMoviePage(comparisonId, providerId, total, hasNext, next, items);
    }

    public PartyBaselinePage listBaseline(
            UUID actor,
            UUID partyId,
            String cursor,
            int limit
    ) {
        PartyRow party = requirePartyMember(partyId, actor, false);
        Materialization materialization = completeMaterialization();
        List<UUID> providers = partyProviders(partyId);
        requireScopedProviders(materialization.id(), providers);
        String selected = uuidArray(providers);
        List<String> bindings = List.of(
                partyId.toString(), Integer.toString(party.revision()), materialization.id().toString(),
                selected, POLICY
        );
        C3CursorCodec.Decoded decoded = cursors.decode(cursor, "PARTY_BASELINE", actor, bindings, 4);
        List<Object> arguments = new ArrayList<>(List.of(materialization.id(), selected, materialization.id()));
        String keyset = "";
        if (decoded != null) {
            keyset = """
                    WHERE coverage < ?
                       OR (coverage = ? AND popularity_rank > ?)
                       OR (coverage = ? AND popularity_rank = ? AND normalized_title > ?)
                       OR (coverage = ? AND popularity_rank = ? AND normalized_title = ? AND movie_id > ?::uuid)
                    """;
            int coverage = Integer.parseInt(decoded.lastKey().get(0));
            int rank = Integer.parseInt(decoded.lastKey().get(1));
            String title = decoded.lastKey().get(2);
            String movie = decoded.lastKey().get(3);
            arguments.addAll(List.of(
                    coverage, coverage, rank, coverage, rank, title,
                    coverage, rank, title, movie
            ));
        }
        arguments.add(limit + 1);
        String cte = baselineCte();
        List<BaselineMovie> rows = jdbc.query(cte + keyset + """
                ORDER BY coverage DESC, popularity_rank, normalized_title, movie_id
                LIMIT ?
                """, (rs, row) -> baselineMovie(rs), arguments.toArray());
        int total = jdbc.queryForObject(
                "SELECT count(*) FROM (" + cte + ") baseline_count",
                Integer.class, materialization.id(), selected, materialization.id()
        );
        boolean hasNext = rows.size() > limit;
        List<BaselineMovie> pageRows = hasNext ? rows.subList(0, limit) : rows;
        List<PartyBaselineItem> items = pageRows.stream().map(movie -> new PartyBaselineItem(
                new MovieSummary(movie.movieId(), movie.title(), movie.posterUrl(), movie.releaseYear()),
                movie.providers(),
                new PartyBaselineExplanation(movie.coverage(), providers.size(), movie.popularityRank(), POLICY)
        )).toList();
        String next = hasNext ? baselineCursor(actor, bindings, pageRows.get(pageRows.size() - 1)) : null;
        return new PartyBaselinePage(
                partyId, POLICY, materialization.publicVersion(), total, hasNext, next, items
        );
    }

    private PartyInvitationPage invitationPage(
            UUID actor,
            UUID partyId,
            UUID recipient,
            String cursor,
            int limit,
            String kind
    ) {
        List<String> bindings = partyId == null ? List.of() : List.of(partyId.toString());
        C3CursorCodec.Decoded decoded = cursors.decode(cursor, kind, actor, bindings, 2);
        List<Object> arguments = new ArrayList<>();
        String ownerFilter;
        if (partyId != null) {
            ownerFilter = "i.party_id = ?";
            arguments.add(partyId);
        } else {
            ownerFilter = "i.recipient_actor_id = ?";
            arguments.add(recipient);
        }
        String keyset = "";
        if (decoded != null) {
            keyset = " AND (i.created_at, i.invitation_id) < (?::timestamptz, ?::uuid)";
            arguments.add(decoded.lastKey().get(0));
            arguments.add(decoded.lastKey().get(1));
        }
        List<Object> pageArguments = new ArrayList<>(arguments);
        pageArguments.add(limit + 1);
        List<InvitationRow> rows = jdbc.query("""
                SELECT i.*, p.name AS party_name
                FROM c3_party_invitation i JOIN c3_party p ON p.party_id = i.party_id
                """ + " WHERE " + ownerFilter + keyset
                + " ORDER BY i.created_at DESC, i.invitation_id DESC LIMIT ?",
                (rs, row) -> invitationRow(rs), pageArguments.toArray());
        int total = jdbc.queryForObject(
                "SELECT count(*) FROM c3_party_invitation i WHERE " + ownerFilter,
                Integer.class, arguments.get(0)
        );
        boolean hasNext = rows.size() > limit;
        List<InvitationRow> pageRows = hasNext ? rows.subList(0, limit) : rows;
        List<PartyInvitation> items = pageRows.stream().map(this::renderInvitation).toList();
        String next = hasNext ? cursors.encode(kind, actor, bindings, List.of(
                pageRows.get(pageRows.size() - 1).createdAt().toString(),
                pageRows.get(pageRows.size() - 1).id().toString()
        )) : null;
        return new PartyInvitationPage(total, hasNext, next, items);
    }

    private List<SnapshotMovie> snapshotMovies(
            Materialization materialization,
            UUID provider,
            String selectedProviders
    ) {
        return jdbc.query("""
                SELECT base.movie_id,
                       COALESCE((SELECT ml.title FROM movie_localization ml
                                  WHERE ml.catalog_version_id = p.catalog_version_id
                                    AND ml.movie_id = p.movie_id
                                  ORDER BY CASE ml.locale WHEN 'ko-KR' THEN 0 WHEN 'en-US' THEN 1 ELSE 2 END
                                  LIMIT 1), p.original_title) AS display_title,
                       p.poster_path, extract(year from p.release_date)::int AS release_year,
                       array_agg(allm.provider_id ORDER BY allm.provider_id) AS available_provider_ids,
                       min(base.catalog_popularity_rank) AS popularity_rank
                FROM c3_availability_membership base
                JOIN movie_catalog_projection p
                  ON p.catalog_version_id = ? AND p.movie_id = base.movie_id
                JOIN c3_availability_membership allm
                  ON allm.materialization_id = base.materialization_id AND allm.movie_id = base.movie_id
                 AND allm.provider_id = ANY(?::uuid[])
                WHERE base.materialization_id = ? AND base.provider_id = ?
                  AND p.identity_status = 'IDENTITY_VERIFIED' AND p.visibility_status = 'UI_READY'
                  AND p.deleted = false
                GROUP BY base.movie_id, p.catalog_version_id, p.movie_id, p.original_title,
                         p.poster_path, p.release_date
                ORDER BY popularity_rank, lower(COALESCE((SELECT ml.title FROM movie_localization ml
                                  WHERE ml.catalog_version_id = p.catalog_version_id
                                    AND ml.movie_id = p.movie_id
                                  ORDER BY CASE ml.locale WHEN 'ko-KR' THEN 0 WHEN 'en-US' THEN 1 ELSE 2 END
                                  LIMIT 1), p.original_title)), base.movie_id
                """, (rs, row) -> new SnapshotMovie(
                rs.getObject("movie_id", UUID.class), rs.getString("display_title"),
                posterUrl(rs.getString("poster_path")), (Integer) rs.getObject("release_year"),
                uuids(rs.getArray("available_provider_ids")), rs.getInt("popularity_rank")
        ), materialization.catalogVersionId(), selectedProviders, materialization.id(), provider);
    }

    private String baselineCte() {
        return """
                WITH candidates AS (
                    SELECT m.movie_id, count(DISTINCT m.provider_id)::int AS coverage,
                           min(m.catalog_popularity_rank) AS popularity_rank,
                           array_agg(DISTINCT m.provider_id ORDER BY m.provider_id) AS available_provider_ids
                    FROM c3_availability_membership m
                    WHERE m.materialization_id = ? AND m.provider_id = ANY(?::uuid[])
                    GROUP BY m.movie_id
                ), cards AS (
                    SELECT c.movie_id, c.coverage, c.popularity_rank, c.available_provider_ids,
                           COALESCE((SELECT ml.title FROM movie_localization ml
                                      WHERE ml.catalog_version_id = p.catalog_version_id
                                        AND ml.movie_id = p.movie_id
                                      ORDER BY CASE ml.locale WHEN 'ko-KR' THEN 0 WHEN 'en-US' THEN 1 ELSE 2 END
                                      LIMIT 1), p.original_title) AS display_title,
                           lower(COALESCE((SELECT ml.title FROM movie_localization ml
                                      WHERE ml.catalog_version_id = p.catalog_version_id
                                        AND ml.movie_id = p.movie_id
                                      ORDER BY CASE ml.locale WHEN 'ko-KR' THEN 0 WHEN 'en-US' THEN 1 ELSE 2 END
                                      LIMIT 1), p.original_title)) AS normalized_title,
                           p.poster_path, extract(year from p.release_date)::int AS release_year
                    FROM candidates c
                    JOIN c3_availability_materialization am ON am.id = ?
                    JOIN movie_catalog_projection p
                      ON p.catalog_version_id = am.catalog_version_id AND p.movie_id = c.movie_id
                    WHERE p.identity_status = 'IDENTITY_VERIFIED' AND p.visibility_status = 'UI_READY'
                      AND p.deleted = false
                )
                SELECT movie_id, coverage, popularity_rank, available_provider_ids,
                       display_title, normalized_title, poster_path, release_year FROM cards
                """;
    }

    private Party renderParty(PartyRow party, UUID actor) {
        String role = jdbc.queryForObject("""
                SELECT role FROM c3_party_member WHERE party_id = ? AND actor_id = ?
                """, String.class, party.id(), actor);
        List<PartyMemberSummary> members = jdbc.query("""
                SELECT pm.member_id, pm.actor_id, a.nickname, pm.role, pm.joined_at
                FROM c3_party_member pm JOIN c3_local_fake_actor a ON a.actor_id = pm.actor_id
                WHERE pm.party_id = ?
                ORDER BY CASE pm.role WHEN 'OWNER' THEN 0 ELSE 1 END, pm.joined_at, pm.member_id
                """, (rs, row) -> new PartyMemberSummary(
                rs.getObject("member_id", UUID.class),
                new LocalActorSummary(rs.getObject("actor_id", UUID.class), rs.getString("nickname")),
                rs.getString("role"), rs.getObject("joined_at", OffsetDateTime.class)
        ), party.id());
        return new Party(
                party.id(), party.name(), party.status(), role, party.memberCount(), MAXIMUM_MEMBER_COUNT,
                party.revision(), partyProviders(party.id()), members,
                "/api/v1/parties/" + party.id() + "/baseline-recommendations"
        );
    }

    private PartyInvitation renderInvitation(InvitationRow invitation) {
        return new PartyInvitation(
                invitation.id(), invitation.partyId(), invitation.partyName(),
                actorSummary(invitation.inviter()), actorSummary(invitation.recipient()),
                invitation.status(), invitation.revision()
        );
    }

    private OttCatalogComparison renderComparison(ComparisonRow comparison) {
        List<ProviderCatalogSummary> providers = jdbc.query("""
                SELECT cp.provider_id, p.display_name, p.logo_path, cp.movie_count
                FROM c3_ott_catalog_provider cp JOIN ott_provider p ON p.id = cp.provider_id
                WHERE cp.comparison_id = ? ORDER BY p.display_priority, cp.provider_id
                """, (rs, row) -> {
            UUID providerId = rs.getObject("provider_id", UUID.class);
            return new ProviderCatalogSummary(
                    new ProviderSummary(providerId, rs.getString("display_name"),
                            posterUrl(rs.getString("logo_path"))),
                    rs.getInt("movie_count"),
                    "/api/v1/me/ott-catalog-comparisons/" + comparison.id()
                            + "/movies?providerId=" + providerId
            );
        }, comparison.id());
        Materialization materialization = requireMaterialization(comparison.materializationId());
        return new OttCatalogComparison(
                comparison.id(), "READY", "KR", "FLATRATE", materialization.publicVersion(), providers
        );
    }

    private HttpMutation idempotent(
            UUID actor,
            String operation,
            String key,
            JsonNode canonical,
            int responseStatus,
            Supplier<JsonNode> mutation
    ) {
        lockIdempotency(actor, operation, key);
        String requestHash = fingerprint(canonical);
        StoredResult stored = stored(actor, operation, key);
        if (stored != null) return replay(stored, requestHash);
        JsonNode body = mutation.get();
        store(actor, operation, key, requestHash, responseStatus, body);
        return new HttpMutation(responseStatus, body, false);
    }

    private HttpMutation replay(StoredResult stored, String requestHash) {
        if (!stored.requestHash().equals(requestHash)) throw idempotencyConflict();
        return new HttpMutation(stored.status(), stored.body(), true);
    }

    private void lockIdempotency(UUID actor, String operation, String key) {
        jdbc.query("SELECT pg_advisory_xact_lock(hashtextextended(?, 0))", (rs, row) -> 0,
                actor + ":" + operation + ":" + key);
    }

    private StoredResult stored(UUID actor, String operation, String key) {
        return jdbc.query("""
                SELECT request_sha256, response_status, response_body
                FROM c3_idempotency_result
                WHERE actor_id = ? AND operation = ? AND idempotency_key = ?
                """, (rs, row) -> new StoredResult(
                rs.getString("request_sha256"), rs.getInt("response_status"),
                readJson(rs.getString("response_body"))
        ), actor, operation, key).stream().findFirst().orElse(null);
    }

    private void store(
            UUID actor,
            String operation,
            String key,
            String requestHash,
            int status,
            JsonNode body
    ) {
        jdbc.update("""
                INSERT INTO c3_idempotency_result (
                    actor_id, operation, idempotency_key, request_sha256,
                    response_status, response_body, created_at
                ) VALUES (?, ?, ?, ?, ?, ?::jsonb, ?)
                """, actor, operation, key, requestHash, status, body.toString(), now());
    }

    private PartyRow requirePartyOwner(UUID partyId, UUID actor, boolean lock) {
        List<PartyRow> values = jdbc.query("SELECT * FROM c3_party WHERE party_id = ? AND owner_actor_id = ?"
                        + (lock ? " FOR UPDATE" : ""),
                (rs, row) -> partyRow(rs), partyId, actor);
        if (values.isEmpty()) throw notFound();
        return values.get(0);
    }

    private PartyRow requirePartyMember(UUID partyId, UUID actor, boolean lock) {
        List<PartyRow> values = jdbc.query("""
                SELECT p.* FROM c3_party p JOIN c3_party_member pm ON pm.party_id = p.party_id
                WHERE p.party_id = ? AND pm.actor_id = ?
                """ + (lock ? " FOR UPDATE OF p" : ""),
                (rs, row) -> partyRow(rs), partyId, actor);
        if (values.isEmpty()) throw notFound();
        return values.get(0);
    }

    private PartyRow requirePartyById(UUID partyId, boolean lock) {
        List<PartyRow> values = jdbc.query("SELECT * FROM c3_party WHERE party_id = ?"
                        + (lock ? " FOR UPDATE" : ""),
                (rs, row) -> partyRow(rs), partyId);
        if (values.isEmpty()) throw notFound();
        return values.get(0);
    }

    private InvitationRow requireRecipientInvitation(UUID invitationId, UUID actor, boolean lock) {
        List<InvitationRow> values = jdbc.query("""
                SELECT i.*, p.name AS party_name FROM c3_party_invitation i
                JOIN c3_party p ON p.party_id = i.party_id
                WHERE i.invitation_id = ? AND i.recipient_actor_id = ?
                """ + (lock ? " FOR UPDATE OF i" : ""),
                (rs, row) -> invitationRow(rs), invitationId, actor);
        if (values.isEmpty()) throw notFound();
        return values.get(0);
    }

    private InvitationRow requireInvitation(UUID invitationId, boolean lock) {
        List<InvitationRow> values = jdbc.query("""
                SELECT i.*, p.name AS party_name FROM c3_party_invitation i
                JOIN c3_party p ON p.party_id = i.party_id
                WHERE i.invitation_id = ?
                """ + (lock ? " FOR UPDATE OF i" : ""),
                (rs, row) -> invitationRow(rs), invitationId);
        if (values.isEmpty()) throw notFound();
        return values.get(0);
    }

    private ComparisonRow requireComparison(UUID comparisonId, UUID actor) {
        return jdbc.query("""
                SELECT comparison_id, owner_actor_id, materialization_id, created_at
                FROM c3_ott_catalog_comparison
                WHERE comparison_id = ? AND owner_actor_id = ? AND status = 'READY'
                """, (rs, row) -> new ComparisonRow(
                rs.getObject("comparison_id", UUID.class), rs.getObject("owner_actor_id", UUID.class),
                rs.getObject("materialization_id", UUID.class),
                rs.getObject("created_at", OffsetDateTime.class)
        ), comparisonId, actor).stream().findFirst().orElseThrow(C3LocalService::notFound);
    }

    private Materialization completeMaterialization() {
        List<Materialization> values = jdbc.query("""
                SELECT am.id, am.catalog_version_id, am.public_catalog_version
                FROM c3_availability_materialization am
                JOIN catalog_version cv ON cv.id = am.catalog_version_id
                WHERE am.region = 'KR' AND am.monetization_type = 'FLATRATE' AND am.status = 'COMPLETE'
                  AND cv.status = 'ACTIVE' AND cv.public_version = am.public_catalog_version
                  AND EXISTS (
                      SELECT 1 FROM c3_availability_provider ap WHERE ap.materialization_id = am.id
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM c3_availability_membership m
                      LEFT JOIN c3_availability_provider ap
                        ON ap.materialization_id = m.materialization_id AND ap.provider_id = m.provider_id
                      LEFT JOIN movie_catalog_projection p
                        ON p.catalog_version_id = am.catalog_version_id AND p.movie_id = m.movie_id
                       AND p.identity_status = 'IDENTITY_VERIFIED'
                       AND p.visibility_status = 'UI_READY' AND p.deleted = false
                      WHERE m.materialization_id = am.id AND (ap.provider_id IS NULL OR p.movie_id IS NULL)
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM c3_availability_membership m
                      WHERE m.materialization_id = am.id
                      GROUP BY m.movie_id HAVING min(m.catalog_popularity_rank) <> max(m.catalog_popularity_rank)
                  )
                """, (rs, row) -> new Materialization(
                rs.getObject("id", UUID.class), rs.getObject("catalog_version_id", UUID.class),
                rs.getString("public_catalog_version")
        ));
        if (values.size() != 1) throw materializationUnavailable();
        return values.get(0);
    }

    private Materialization requireMaterialization(UUID id) {
        return jdbc.query("""
                SELECT am.id, am.catalog_version_id, am.public_catalog_version
                FROM c3_availability_materialization am
                JOIN catalog_version cv ON cv.id = am.catalog_version_id
                WHERE am.id = ? AND am.status = 'COMPLETE' AND cv.status = 'ACTIVE'
                  AND cv.public_version = am.public_catalog_version
                """, (rs, row) -> new Materialization(
                rs.getObject("id", UUID.class), rs.getObject("catalog_version_id", UUID.class),
                rs.getString("public_catalog_version")
        ), id).stream().findFirst().orElseThrow(C3LocalService::materializationUnavailable);
    }

    private void requireScopedProviders(UUID materializationId, List<UUID> providers) {
        Integer count = jdbc.queryForObject("""
                SELECT count(*) FROM c3_availability_provider
                WHERE materialization_id = ? AND provider_id = ANY(?::uuid[])
                """, Integer.class, materializationId, uuidArray(providers));
        if (count == null || count != providers.size()) {
            throw validation("providerIds", "unsupported_provider");
        }
    }

    private List<UUID> partyProviders(UUID partyId) {
        return jdbc.query("""
                SELECT provider_id FROM c3_party_provider WHERE party_id = ? ORDER BY provider_id
                """, (rs, row) -> rs.getObject(1, UUID.class), partyId);
    }

    private boolean actorExists(UUID actor) {
        Integer count = jdbc.queryForObject(
                "SELECT count(*) FROM c3_local_fake_actor WHERE actor_id = ? AND enabled",
                Integer.class, actor
        );
        return count != null && count == 1;
    }

    private boolean isMember(UUID partyId, UUID actor) {
        Integer count = jdbc.queryForObject(
                "SELECT count(*) FROM c3_party_member WHERE party_id = ? AND actor_id = ?",
                Integer.class, partyId, actor
        );
        return count != null && count > 0;
    }

    private LocalActorSummary actorSummary(UUID actor) {
        return jdbc.query("SELECT actor_id, nickname FROM c3_local_fake_actor WHERE actor_id = ?",
                (rs, row) -> new LocalActorSummary(rs.getObject("actor_id", UUID.class), rs.getString("nickname")),
                actor).stream().findFirst().orElseThrow(C3LocalService::actorUnavailable);
    }

    private List<UUID> canonicalProviders(List<UUID> input) {
        if (input == null || input.size() < 2 || input.size() > 4 || input.stream().anyMatch(Objects::isNull)) {
            throw validation("providerIds", "size_2_to_4");
        }
        List<UUID> result = input.stream().distinct().sorted().toList();
        if (result.size() != input.size()) throw validation("providerIds", "must_be_unique");
        return result;
    }

    private String comparisonCursor(UUID actor, List<String> bindings, SnapshotMovie last) {
        return cursors.encode("COMPARISON_MOVIES", actor, bindings, List.of(
                Integer.toString(last.popularityRank()), last.title().toLowerCase(), last.movieId().toString()
        ));
    }

    private String baselineCursor(UUID actor, List<String> bindings, BaselineMovie last) {
        return cursors.encode("PARTY_BASELINE", actor, bindings, List.of(
                Integer.toString(last.coverage()), Integer.toString(last.popularityRank()),
                last.normalizedTitle(), last.movieId().toString()
        ));
    }

    private static String uuidArray(List<UUID> values) {
        return "{" + String.join(",", values.stream().map(UUID::toString).toList()) + "}";
    }

    private static List<UUID> uuids(Array value) throws SQLException {
        if (value == null) return List.of();
        Object raw = value.getArray();
        if (raw instanceof UUID[] uuidValues) return List.copyOf(Arrays.asList(uuidValues));
        Object[] values = (Object[]) raw;
        return Arrays.stream(values).map(item -> UUID.fromString(item.toString())).toList();
    }

    private static SnapshotMovie snapshotMovie(ResultSet rs) throws SQLException {
        return new SnapshotMovie(
                rs.getObject("movie_id", UUID.class), rs.getString("display_title"),
                rs.getString("poster_url"), (Integer) rs.getObject("release_year"),
                uuids(rs.getArray("available_provider_ids")), rs.getInt("popularity_rank")
        );
    }

    private static BaselineMovie baselineMovie(ResultSet rs) throws SQLException {
        return new BaselineMovie(
                rs.getObject("movie_id", UUID.class), rs.getString("display_title"),
                rs.getString("normalized_title"), posterUrl(rs.getString("poster_path")),
                (Integer) rs.getObject("release_year"), uuids(rs.getArray("available_provider_ids")),
                rs.getInt("coverage"), rs.getInt("popularity_rank")
        );
    }

    private static MovieSummary movieSummary(SnapshotMovie movie) {
        return new MovieSummary(movie.movieId(), movie.title(), movie.posterUrl(), movie.releaseYear());
    }

    private static String posterUrl(String path) {
        if (path == null || path.isBlank()) return null;
        return path.startsWith("http://") || path.startsWith("https://") ? path : IMAGE_BASE + path;
    }

    private static PartyRow partyRow(ResultSet rs) throws SQLException {
        return new PartyRow(
                rs.getObject("party_id", UUID.class), rs.getObject("owner_actor_id", UUID.class),
                rs.getString("name"), rs.getString("status"), rs.getInt("member_count"),
                rs.getInt("revision"), rs.getObject("created_at", OffsetDateTime.class),
                rs.getObject("updated_at", OffsetDateTime.class)
        );
    }

    private static InvitationRow invitationRow(ResultSet rs) throws SQLException {
        return new InvitationRow(
                rs.getObject("invitation_id", UUID.class), rs.getObject("party_id", UUID.class),
                rs.getString("party_name"), rs.getObject("inviter_actor_id", UUID.class),
                rs.getObject("recipient_actor_id", UUID.class), rs.getString("status"),
                rs.getInt("revision"), rs.getObject("created_at", OffsetDateTime.class)
        );
    }

    private JsonNode readJson(String value) {
        try {
            return objectMapper.readTree(value);
        } catch (Exception exception) {
            throw materializationUnavailable();
        }
    }

    private static void putUuids(ArrayNode target, List<UUID> values) {
        values.forEach(value -> target.add(value.toString()));
    }

    private String fingerprint(JsonNode node) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(objectMapper.writeValueAsString(node).getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest);
        } catch (Exception exception) {
            throw new IllegalStateException("cannot fingerprint canonical C3 request", exception);
        }
    }

    private TransactionTemplate transaction() {
        return new TransactionTemplate(transactionManager);
    }

    private OffsetDateTime now() {
        return OffsetDateTime.ofInstant(clock.instant(), ZoneOffset.UTC);
    }

    private static ApiException validation(String field, String reason) {
        return new ApiException(HttpStatus.BAD_REQUEST, "VALIDATION_ERROR", "요청 값을 확인해 주세요.",
                List.of(new CatalogApiDtos.FieldError(field, reason)));
    }

    private static ApiException unauthorized() {
        return new ApiException(HttpStatus.UNAUTHORIZED, "LOCAL_ACTOR_UNAUTHORIZED", "로컬 사용자를 확인해 주세요.");
    }

    private static ApiException actorUnavailable() {
        return new ApiException(HttpStatus.BAD_REQUEST, "LOCAL_ACTOR_UNAVAILABLE", "초대할 로컬 사용자를 찾을 수 없어요.");
    }

    private static ApiException notFound() {
        return new ApiException(HttpStatus.NOT_FOUND, "RESOURCE_NOT_FOUND", "요청한 항목을 찾을 수 없어요.");
    }

    private static ApiException revisionConflict() {
        return new ApiException(HttpStatus.CONFLICT, "REVISION_CONFLICT", "최신 상태를 다시 불러와 주세요.");
    }

    private static ApiException capacityReached() {
        return new ApiException(HttpStatus.CONFLICT, "PARTY_CAPACITY_REACHED", "파티 정원이 모두 찼어요.");
    }

    private static ApiException duplicateInvitation() {
        return new ApiException(HttpStatus.CONFLICT, "DUPLICATE_INVITATION", "이미 초대했거나 참여한 사용자예요.");
    }

    private static ApiException idempotencyConflict() {
        return new ApiException(HttpStatus.CONFLICT, "IDEMPOTENCY_KEY_REUSED", "같은 요청 키가 다른 요청에 사용됐어요.");
    }

    private static ApiException invalidTransition() {
        return new ApiException(HttpStatus.CONFLICT, "INVALID_STATE_TRANSITION", "현재 상태에서는 처리할 수 없어요.");
    }

    private static ApiException materializationUnavailable() {
        return new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "CATALOG_MATERIALIZATION_UNAVAILABLE",
                "완전한 OTT 영화 목록을 준비하지 못했어요.");
    }

    public record HttpMutation(int status, JsonNode body, boolean replayed) {
    }

    private record PartyRow(
            UUID id, UUID owner, String name, String status, int memberCount, int revision,
            OffsetDateTime createdAt, OffsetDateTime updatedAt
    ) {
    }

    private record InvitationRow(
            UUID id, UUID partyId, String partyName, UUID inviter, UUID recipient,
            String status, int revision, OffsetDateTime createdAt
    ) {
    }

    private record ComparisonRow(UUID id, UUID owner, UUID materializationId, OffsetDateTime createdAt) {
    }

    private record Materialization(UUID id, UUID catalogVersionId, String publicVersion) {
    }

    private record SnapshotMovie(
            UUID movieId, String title, String posterUrl, Integer releaseYear,
            List<UUID> providers, int popularityRank
    ) {
    }

    private record BaselineMovie(
            UUID movieId, String title, String normalizedTitle, String posterUrl, Integer releaseYear,
            List<UUID> providers, int coverage, int popularityRank
    ) {
    }

    private record StoredResult(String requestHash, int status, JsonNode body) {
    }
}
