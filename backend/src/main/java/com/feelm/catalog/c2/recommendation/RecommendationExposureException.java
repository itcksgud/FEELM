package com.feelm.catalog.c2.recommendation;

public final class RecommendationExposureException extends RuntimeException {
    public enum Code {
        INVALID_EXPOSURE_REQUEST,
        INVALID_SERVING_SNAPSHOT,
        EXPOSURE_BATCH_REUSED,
        EXPOSURE_PERSISTENCE_REJECTED
    }

    private final Code code;

    public RecommendationExposureException(Code code) {
        super(code.name());
        this.code = code;
    }

    public Code code() {
        return code;
    }
}

