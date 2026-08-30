package com.feelm.catalog.c2.recommendation;

public final class C2RecommendationFailure extends RuntimeException {
    public enum Code {
        CONFIGURATION_UNAVAILABLE,
        CANDIDATE_ARTIFACT_INVALID,
        CATALOG_VERSION_MISMATCH,
        NO_ELIGIBLE_CANDIDATES,
        DEADLINE_EXCEEDED,
        CONNECTION_FAILURE,
        AUTH_REQUIRED,
        AUTH_FORBIDDEN,
        SERVICE_UNAVAILABLE,
        UPSTREAM_REJECTED,
        INVALID_RESPONSE
    }

    private final Code code;
    private final boolean retryable;

    public C2RecommendationFailure(Code code, boolean retryable) {
        super(code.name());
        this.code = code;
        this.retryable = retryable;
    }

    public Code code() {
        return code;
    }

    public boolean retryable() {
        return retryable;
    }
}
