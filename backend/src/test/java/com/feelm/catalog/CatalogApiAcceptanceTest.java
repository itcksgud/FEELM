package com.feelm.catalog;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.feelm.catalog.adapter.fixture.FixtureCatalogReadAdapter;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;

import static org.hamcrest.Matchers.containsString;
import static org.hamcrest.Matchers.everyItem;
import static org.hamcrest.Matchers.hasSize;
import static org.hamcrest.Matchers.is;
import static org.hamcrest.Matchers.nullValue;
import static org.hamcrest.Matchers.startsWith;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("fixture")
class CatalogApiAcceptanceTest {
    @Autowired
    MockMvc mvc;

    @Autowired
    ObjectMapper objectMapper;

    @Test
    void searchesTitlesDirectorsAndActorsWithoutExternalCalls() throws Exception {
        mvc.perform(get("/api/v1/movies").param("query", "나우 유"))
                .andExpect(status().isOk())
                .andExpect(header().string("X-Catalog-Version", FixtureCatalogReadAdapter.CATALOG_VERSION))
                .andExpect(jsonPath("$.catalogVersion").value(FixtureCatalogReadAdapter.CATALOG_VERSION))
                .andExpect(jsonPath("$.items[0].movieId").value(FixtureCatalogReadAdapter.MOV_KO_FULL.toString()));

        mvc.perform(get("/api/v1/movies").param("query", "Louis Leterrier"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[0].movieId").value(FixtureCatalogReadAdapter.MOV_KO_FULL.toString()));

        mvc.perform(get("/api/v1/movies").param("query", "Jesse Eisenberg"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[0].movieId").value(FixtureCatalogReadAdapter.MOV_KO_FULL.toString()));
    }

    @Test
    void emptySearchIsAStable200AndWhitespaceUsesPopularity() throws Exception {
        mvc.perform(get("/api/v1/movies").param("query", "존재하지않는검색어"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.totalCount").value(0))
                .andExpect(jsonPath("$.hasNext").value(false))
                .andExpect(jsonPath("$.nextCursor").value(nullValue()))
                .andExpect(jsonPath("$.items", hasSize(0)));

        mvc.perform(get("/api/v1/movies").param("query", "   "))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.appliedFilters.query").value(nullValue()))
                .andExpect(jsonPath("$.appliedFilters.sort").value("POPULARITY"));
    }

    @Test
    void paginationCursorIsBoundToNormalizedFilters() throws Exception {
        String firstBody = mvc.perform(get("/api/v1/movies").param("limit", "1"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.hasNext").value(true))
                .andExpect(jsonPath("$.nextCursor", startsWith("ey")))
                .andReturn().getResponse().getContentAsString();
        JsonNode first = objectMapper.readTree(firstBody);
        String cursor = first.path("nextCursor").asText();
        String firstMovie = first.path("items").path(0).path("movieId").asText();

        mvc.perform(get("/api/v1/movies").param("limit", "1").param("cursor", cursor))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[0].movieId").value(is(org.hamcrest.Matchers.not(firstMovie))));

        mvc.perform(get("/api/v1/movies").param("query", "영화").param("limit", "1").param("cursor", cursor))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("INVALID_CURSOR"))
                .andExpect(jsonPath("$.traceId").isNotEmpty());
    }

    @Test
    void validatesRangesLimitsAndTypesAsContractErrors() throws Exception {
        mvc.perform(get("/api/v1/movies").param("releaseYearFrom", "2020").param("releaseYearTo", "2010"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_ERROR"))
                .andExpect(jsonPath("$.fieldErrors[0].field").value("releaseYearFrom"));

        mvc.perform(get("/api/v1/movies").param("limit", "51"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_ERROR"))
                .andExpect(jsonPath("$.fieldErrors[0].field").value("limit"));

        mvc.perform(get("/api/v1/movies").param("genreIds", "not-a-uuid"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_ERROR"));
    }

    @Test
    void rejectsAnInvalidOptionalTokenInsteadOfDowngradingToAnonymous() throws Exception {
        mvc.perform(get("/api/v1/movies").header("Authorization", "Bearer test-invalid-token"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("INVALID_ACCESS_TOKEN"));
    }

    @Test
    void detailsApplyLocaleFallbackAndKeepNullablePoster() throws Exception {
        mvc.perform(get("/api/v1/movies/{id}", FixtureCatalogReadAdapter.MOV_KO_FULL))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.displayTitle").value("나우 유 씨 미"))
                .andExpect(jsonPath("$.displayTitleLocale").value("ko-KR"))
                .andExpect(jsonPath("$.overviewLocale").value("ko-KR"))
                .andExpect(jsonPath("$.externalRating.source").value("TMDB"))
                .andExpect(jsonPath("$.externalRating.scale").value(10))
                .andExpect(jsonPath("$.cast", hasSize(2)))
                .andExpect(content().string(org.hamcrest.Matchers.not(containsString("tmdbId"))))
                .andExpect(content().string(org.hamcrest.Matchers.not(containsString("movieLensId"))))
                .andExpect(content().string(org.hamcrest.Matchers.not(containsString("userId"))));

        mvc.perform(get("/api/v1/movies/{id}", FixtureCatalogReadAdapter.MOV_EN_FALLBACK))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.displayTitleLocale").value("en-US"))
                .andExpect(jsonPath("$.overviewLocale").value("en-US"));

        mvc.perform(get("/api/v1/movies/{id}", FixtureCatalogReadAdapter.MOV_NO_POSTER))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.posterUrl").value(nullValue()));
    }

    @Test
    void hiddenAndUnknownMoviesShareTheSameNotFoundError() throws Exception {
        mvc.perform(get("/api/v1/movies/{id}", FixtureCatalogReadAdapter.MOV_TV_MISMATCH))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("MOVIE_NOT_FOUND"));

        mvc.perform(get("/api/v1/movies/{id}", "00000000-0000-0000-0000-000000000000"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("MOVIE_NOT_FOUND"));
    }

    @Test
    void ottStatesAndAnonymousSubscriptionSemanticsAreDistinct() throws Exception {
        mvc.perform(get("/api/v1/movies/{id}/ott-offers", FixtureCatalogReadAdapter.MOV_KO_FULL))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.availabilityStatus").value("LISTED"))
                .andExpect(jsonPath("$.freshness").value("FRESH"))
                .andExpect(jsonPath("$.groups[*].monetizationType").value(org.hamcrest.Matchers.hasItems("FLATRATE", "RENT", "BUY")))
                .andExpect(jsonPath("$.groups[*].offers[*].isSubscribed", everyItem(nullValue())))
                .andExpect(jsonPath("$.groups[0].offers[0].link.type").value("AGGREGATOR"));

        mvc.perform(get("/api/v1/movies/{id}/ott-offers", FixtureCatalogReadAdapter.MOV_NONE_LISTED))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.availabilityStatus").value("NONE_LISTED"))
                .andExpect(jsonPath("$.freshness").value("FRESH"))
                .andExpect(jsonPath("$.groups", hasSize(0)));

        mvc.perform(get("/api/v1/movies/{id}/ott-offers", FixtureCatalogReadAdapter.MOV_OTT_UNKNOWN))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.availabilityStatus").value("UNKNOWN"))
                .andExpect(jsonPath("$.freshness").value("UNKNOWN"));

        mvc.perform(get("/api/v1/movies/{id}/ott-offers", FixtureCatalogReadAdapter.MOV_OTT_STALE))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.availabilityStatus").value("LISTED"))
                .andExpect(jsonPath("$.freshness").value("STALE"));
    }

    @Test
    void subscribedUserGetsNetflixFirstWithoutHidingOtherProviders() throws Exception {
        mvc.perform(get("/api/v1/movies/{id}/ott-offers", FixtureCatalogReadAdapter.MOV_KO_FULL)
                        .header("Authorization", "Bearer test-valid-subscribed-token"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.groups[0].monetizationType").value("FLATRATE"))
                .andExpect(jsonPath("$.groups[0].offers[0].providerId").value(FixtureCatalogReadAdapter.NETFLIX.toString()))
                .andExpect(jsonPath("$.groups[0].offers[0].isSubscribed").value(true))
                .andExpect(jsonPath("$.groups[0].offers[1].isSubscribed").value(false));
    }

    @Test
    void similarMoviesAreUiReadyDeterministicAndExplainable() throws Exception {
        mvc.perform(get("/api/v1/movies/{id}/similar", FixtureCatalogReadAdapter.MOV_KO_FULL))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.similarityVersion").value(FixtureCatalogReadAdapter.SIMILARITY_VERSION))
                .andExpect(jsonPath("$.items", hasSize(2)))
                .andExpect(jsonPath("$.items[0].movie.movieId").value(FixtureCatalogReadAdapter.MOV_SIMILAR_1.toString()))
                .andExpect(jsonPath("$.items[0].reasons", hasSize(2)))
                .andExpect(content().string(org.hamcrest.Matchers.not(containsString("score"))));

        mvc.perform(get("/api/v1/movies/{id}/similar", FixtureCatalogReadAdapter.MOV_EN_FALLBACK))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items", hasSize(0)));
    }

    @Test
    void facetsAndProvidersHaveStablePublicIdentifiersAndOrder() throws Exception {
        mvc.perform(get("/api/v1/catalog/genres"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[0].genreId").value(FixtureCatalogReadAdapter.CRIME.toString()))
                .andExpect(jsonPath("$.items[0].name").value("범죄"));

        mvc.perform(get("/api/v1/catalog/countries"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[0].code").value("FR"));

        mvc.perform(get("/api/v1/ott-providers"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[0].providerId").value(FixtureCatalogReadAdapter.NETFLIX.toString()))
                .andExpect(jsonPath("$.items[*].isSubscribed", everyItem(nullValue())));

        mvc.perform(get("/api/v1/ott-providers").header("Authorization", "Bearer test-valid-subscribed-token"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[0].providerId").value(FixtureCatalogReadAdapter.NETFLIX.toString()))
                .andExpect(jsonPath("$.items[0].isSubscribed").value(true))
                .andExpect(jsonPath("$.items[1].isSubscribed").value(false));
    }
}
