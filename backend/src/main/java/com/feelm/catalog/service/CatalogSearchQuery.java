package com.feelm.catalog.service;

import com.feelm.catalog.domain.CatalogModels.MonetizationType;
import com.feelm.catalog.domain.CatalogModels.MovieSort;

import java.util.List;
import java.util.UUID;

public record CatalogSearchQuery(
        String query,
        List<UUID> genreIds,
        List<String> countryCodes,
        Integer releaseYearFrom,
        Integer releaseYearTo,
        List<UUID> ottProviderIds,
        List<MonetizationType> ottMonetizationTypes,
        MovieSort sort,
        String cursor,
        int limit
) {
    public CatalogSearchQuery {
        genreIds = genreIds == null ? List.of() : List.copyOf(genreIds);
        countryCodes = countryCodes == null ? List.of() : List.copyOf(countryCodes);
        ottProviderIds = ottProviderIds == null ? List.of() : List.copyOf(ottProviderIds);
        ottMonetizationTypes = ottMonetizationTypes == null || ottMonetizationTypes.isEmpty()
                ? List.of(MonetizationType.FLATRATE)
                : List.copyOf(ottMonetizationTypes);
    }
}
