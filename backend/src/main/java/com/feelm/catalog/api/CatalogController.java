package com.feelm.catalog.api;

import com.feelm.catalog.domain.CatalogModels.MonetizationType;
import com.feelm.catalog.domain.CatalogModels.MovieSort;
import com.feelm.catalog.security.CatalogUserContextResolver;
import com.feelm.catalog.service.CatalogSearchQuery;
import com.feelm.catalog.service.CatalogService;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import org.springframework.http.ResponseEntity;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.UUID;

@Validated
@RestController
public class CatalogController {
    private static final String CATALOG_VERSION_HEADER = "X-Catalog-Version";

    private final CatalogService catalogService;
    private final CatalogUserContextResolver userContextResolver;

    public CatalogController(CatalogService catalogService, CatalogUserContextResolver userContextResolver) {
        this.catalogService = catalogService;
        this.userContextResolver = userContextResolver;
    }

    @GetMapping("/api/v1/movies")
    public ResponseEntity<CatalogApiDtos.MovieSearchPage> searchMovies(
            @RequestParam(required = false) @Size(max = 100) String query,
            @RequestParam(required = false) @Size(max = 20) List<UUID> genreIds,
            @RequestParam(required = false) @Size(max = 20) List<@Pattern(regexp = "^[A-Z]{2}$") String> countryCodes,
            @RequestParam(required = false) @Min(1870) @Max(2100) Integer releaseYearFrom,
            @RequestParam(required = false) @Min(1870) @Max(2100) Integer releaseYearTo,
            @RequestParam(required = false) @Size(max = 20) List<UUID> ottProviderIds,
            @RequestParam(required = false) @Size(max = 5) List<MonetizationType> ottMonetizationTypes,
            @RequestParam(required = false) MovieSort sort,
            @RequestParam(required = false) @Size(max = 2048) String cursor,
            @RequestParam(defaultValue = "20") @Min(1) @Max(50) int limit,
            @RequestHeader(value = "Authorization", required = false) String authorization
    ) {
        CatalogApiDtos.MovieSearchPage body = catalogService.search(
                new CatalogSearchQuery(
                        query, genreIds, countryCodes, releaseYearFrom, releaseYearTo, ottProviderIds,
                        ottMonetizationTypes, sort, cursor, limit
                ),
                userContextResolver.resolve(authorization)
        );
        return versioned(body.catalogVersion(), body);
    }

    @GetMapping("/api/v1/movies/{movieId}")
    public ResponseEntity<CatalogApiDtos.MovieDetail> getMovie(
            @PathVariable UUID movieId,
            @RequestHeader(value = "Authorization", required = false) String authorization
    ) {
        CatalogApiDtos.MovieDetail body = catalogService.getMovie(movieId, userContextResolver.resolve(authorization));
        return versioned(body.catalogVersion(), body);
    }

    @GetMapping("/api/v1/movies/{movieId}/similar")
    public ResponseEntity<CatalogApiDtos.SimilarMovieResponse> getSimilarMovies(
            @PathVariable UUID movieId,
            @RequestParam(defaultValue = "10") @Min(1) @Max(30) int limit,
            @RequestHeader(value = "Authorization", required = false) String authorization
    ) {
        CatalogApiDtos.SimilarMovieResponse body = catalogService.getSimilar(
                movieId, limit, userContextResolver.resolve(authorization)
        );
        return versioned(body.catalogVersion(), body);
    }

    @GetMapping("/api/v1/movies/{movieId}/ott-offers")
    public ResponseEntity<CatalogApiDtos.OttAvailability> getMovieOttOffers(
            @PathVariable UUID movieId,
            @RequestHeader(value = "Authorization", required = false) String authorization
    ) {
        CatalogApiDtos.OttAvailability body = catalogService.getOttOffers(movieId, userContextResolver.resolve(authorization));
        return versioned(body.catalogVersion(), body);
    }

    @GetMapping("/api/v1/catalog/genres")
    public ResponseEntity<CatalogApiDtos.GenreListResponse> listGenres() {
        CatalogApiDtos.GenreListResponse body = catalogService.listGenres();
        return versioned(body.catalogVersion(), body);
    }

    @GetMapping("/api/v1/catalog/countries")
    public ResponseEntity<CatalogApiDtos.CountryListResponse> listCountries() {
        CatalogApiDtos.CountryListResponse body = catalogService.listCountries();
        return versioned(body.catalogVersion(), body);
    }

    @GetMapping("/api/v1/ott-providers")
    public ResponseEntity<CatalogApiDtos.OttProviderListResponse> listOttProviders(
            @RequestHeader(value = "Authorization", required = false) String authorization
    ) {
        CatalogApiDtos.OttProviderListResponse body = catalogService.listProviders(userContextResolver.resolve(authorization));
        return versioned(body.catalogVersion(), body);
    }

    private <T> ResponseEntity<T> versioned(String catalogVersion, T body) {
        return ResponseEntity.ok().header(CATALOG_VERSION_HEADER, catalogVersion).body(body);
    }
}
