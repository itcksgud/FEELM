package com.feelm.catalog.security;

import java.util.UUID;
import java.util.Set;

public record CatalogUserContext(boolean authenticated, UUID actorUserId, Set<UUID> subscribedProviderIds) {
    public CatalogUserContext {
        subscribedProviderIds = Set.copyOf(subscribedProviderIds);
    }

    public static CatalogUserContext anonymous() {
        return new CatalogUserContext(false, null, Set.of());
    }
}
