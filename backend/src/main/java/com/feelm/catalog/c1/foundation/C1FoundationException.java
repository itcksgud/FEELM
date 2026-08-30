package com.feelm.catalog.c1.foundation;

public final class C1FoundationException extends RuntimeException {
    private final String code;

    public C1FoundationException(String code, String message) {
        super(message);
        this.code = code;
    }

    public String code() {
        return code;
    }
}
