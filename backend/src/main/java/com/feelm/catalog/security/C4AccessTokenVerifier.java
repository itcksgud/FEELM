package com.feelm.catalog.security;

import java.util.Optional;

public interface C4AccessTokenVerifier {
    Optional<CatalogUserContext> verify(String token);
}
