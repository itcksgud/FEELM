package com.feelm.catalog.c3.api;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;

public final class C3ApiDtos {
    private C3ApiDtos() {
    }

    public record CreateOttCatalogComparisonRequest(
            @NotNull @Size(min = 2, max = 4) List<@NotNull UUID> providerIds
    ) {
        public CreateOttCatalogComparisonRequest {
            providerIds = providerIds == null ? null : List.copyOf(providerIds);
        }
    }

    public record ProviderSummary(UUID providerId, String name, String logoUrl) {
    }

    public record ProviderCatalogSummary(ProviderSummary provider, int movieCount, String moviesHref) {
    }

    public record OttCatalogComparison(
            UUID comparisonId,
            String status,
            String region,
            String monetizationType,
            String catalogVersion,
            List<ProviderCatalogSummary> providers
    ) {
        public OttCatalogComparison {
            providers = List.copyOf(providers);
        }
    }

    public record MovieSummary(
            UUID movieId,
            String displayTitle,
            String posterUrl,
            Integer releaseYear
    ) {
    }

    public record CatalogMovie(MovieSummary movie, List<UUID> availableProviderIds) {
        public CatalogMovie {
            availableProviderIds = List.copyOf(availableProviderIds);
        }
    }

    public record CatalogMoviePage(
            UUID comparisonId,
            UUID providerId,
            int totalCount,
            boolean hasNext,
            String nextCursor,
            List<CatalogMovie> items
    ) {
        public CatalogMoviePage {
            items = List.copyOf(items);
        }
    }

    public record CreatePartyRequest(
            @NotBlank @Size(max = 60) String name,
            @NotNull @Size(min = 2, max = 4) List<@NotNull UUID> providerIds
    ) {
        public CreatePartyRequest {
            providerIds = providerIds == null ? null : List.copyOf(providerIds);
        }
    }

    public record LocalActorSummary(UUID actorId, String nickname) {
    }

    public record PartyMemberSummary(
            UUID memberId,
            LocalActorSummary actor,
            String role,
            OffsetDateTime joinedAt
    ) {
    }

    public record Party(
            UUID partyId,
            String name,
            String status,
            String myRole,
            int memberCount,
            int maximumMemberCount,
            int revision,
            List<UUID> providerIds,
            List<PartyMemberSummary> members,
            String baselineHref
    ) {
        public Party {
            providerIds = List.copyOf(providerIds);
            members = List.copyOf(members);
        }
    }

    public record PartyPage(int totalCount, boolean hasNext, String nextCursor, List<Party> items) {
        public PartyPage {
            items = List.copyOf(items);
        }
    }

    public record CreatePartyInvitationRequest(
            @NotNull UUID recipientActorId,
            @Min(1) int expectedPartyRevision
    ) {
    }

    public record PartyInvitation(
            UUID invitationId,
            UUID partyId,
            String partyName,
            LocalActorSummary inviter,
            LocalActorSummary recipient,
            String status,
            int revision
    ) {
    }

    public record PartyInvitationPage(
            int totalCount,
            boolean hasNext,
            String nextCursor,
            List<PartyInvitation> items
    ) {
        public PartyInvitationPage {
            items = List.copyOf(items);
        }
    }

    public record AcceptPartyInvitationRequest(
            @Min(1) int expectedPartyRevision,
            @Min(1) int expectedInvitationRevision
    ) {
    }

    public record AcceptPartyInvitationResponse(PartyInvitation invitation, Party party) {
    }

    public record PartyBaselineExplanation(
            int availableProviderCount,
            int selectedProviderCount,
            int catalogPopularityRank,
            String policyVersion
    ) {
    }

    public record PartyBaselineItem(
            MovieSummary movie,
            List<UUID> availableProviderIds,
            PartyBaselineExplanation explanation
    ) {
        public PartyBaselineItem {
            availableProviderIds = List.copyOf(availableProviderIds);
        }
    }

    public record PartyBaselinePage(
            UUID partyId,
            String policyVersion,
            String catalogVersion,
            int totalCount,
            boolean hasNext,
            String nextCursor,
            List<PartyBaselineItem> items
    ) {
        public PartyBaselinePage {
            items = List.copyOf(items);
        }
    }

    public record PageRequest(
            @Size(max = 2048) String cursor,
            @Min(1) @Max(100) int limit
    ) {
    }
}
