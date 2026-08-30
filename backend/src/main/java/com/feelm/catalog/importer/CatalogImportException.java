package com.feelm.catalog.importer;

public final class CatalogImportException extends RuntimeException {
    private final String code;
    private final long lineNumber;

    public CatalogImportException(String code, String message) {
        this(code, message, 0, null);
    }

    public CatalogImportException(String code, String message, long lineNumber) {
        this(code, message, lineNumber, null);
    }

    public CatalogImportException(String code, String message, long lineNumber, Throwable cause) {
        super(message, cause);
        this.code = code;
        this.lineNumber = lineNumber;
    }

    public String code() {
        return code;
    }

    public long lineNumber() {
        return lineNumber;
    }
}
