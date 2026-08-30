package com.feelm.catalog.api;

import org.springframework.http.HttpStatus;

import java.util.List;

public final class ApiException extends RuntimeException {
    private final HttpStatus status;
    private final String code;
    private final List<CatalogApiDtos.FieldError> fieldErrors;

    public ApiException(HttpStatus status, String code, String message) {
        this(status, code, message, List.of());
    }

    public ApiException(
            HttpStatus status,
            String code,
            String message,
            List<CatalogApiDtos.FieldError> fieldErrors
    ) {
        super(message);
        this.status = status;
        this.code = code;
        this.fieldErrors = List.copyOf(fieldErrors);
    }

    public HttpStatus status() {
        return status;
    }

    public String code() {
        return code;
    }

    public List<CatalogApiDtos.FieldError> fieldErrors() {
        return fieldErrors;
    }

    public static ApiException invalidCursor() {
        return new ApiException(
                HttpStatus.BAD_REQUEST,
                "INVALID_CURSOR",
                "페이지 정보를 다시 확인해 주세요.",
                List.of(new CatalogApiDtos.FieldError("cursor", "invalid_or_expired"))
        );
    }

    public static ApiException movieNotFound() {
        return new ApiException(HttpStatus.NOT_FOUND, "MOVIE_NOT_FOUND", "영화 정보를 찾을 수 없어요.");
    }

    public static ApiException invalidToken() {
        return new ApiException(HttpStatus.UNAUTHORIZED, "INVALID_ACCESS_TOKEN", "인증 정보를 확인해 주세요.");
    }

    public static ApiException unauthorized() {
        return new ApiException(HttpStatus.UNAUTHORIZED, "UNAUTHORIZED", "로그인이 필요해요.");
    }
}
