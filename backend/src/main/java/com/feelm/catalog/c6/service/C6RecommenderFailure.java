package com.feelm.catalog.c6.service;

public final class C6RecommenderFailure extends RuntimeException {
    public enum Code {
        CONFIGURATION_UNAVAILABLE,
        AUTH_REQUIRED,
        AUTH_FORBIDDEN,
        SERVICE_UNAVAILABLE,
        UPSTREAM_REJECTED,
        DEADLINE_EXCEEDED,
        CONNECTION_FAILURE,
        INVALID_RESPONSE
    }

    private final Code code;
    private final boolean retryable;

    public C6RecommenderFailure(Code code, boolean retryable) {
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
