package com.feelm.catalog.api;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.ConstraintViolationException;
import org.springframework.dao.DataAccessException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.bind.MissingRequestHeaderException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.transaction.TransactionException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.List;
import java.util.Objects;

@RestControllerAdvice
public final class GlobalExceptionHandler {
    private static final Logger log = LoggerFactory.getLogger(GlobalExceptionHandler.class);
    @ExceptionHandler(ApiException.class)
    ResponseEntity<CatalogApiDtos.ErrorResponse> handleApi(ApiException exception, HttpServletRequest request) {
        return response(exception.status(), exception.code(), exception.getMessage(), exception.fieldErrors(), request);
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    ResponseEntity<CatalogApiDtos.ErrorResponse> handleBodyValidation(
            MethodArgumentNotValidException exception,
            HttpServletRequest request
    ) {
        List<CatalogApiDtos.FieldError> fields = exception.getBindingResult().getFieldErrors().stream()
                .map(error -> new CatalogApiDtos.FieldError(error.getField(), Objects.toString(error.getDefaultMessage(), "invalid")))
                .toList();
        return validation(fields, request);
    }

    @ExceptionHandler(ConstraintViolationException.class)
    ResponseEntity<CatalogApiDtos.ErrorResponse> handleConstraint(
            ConstraintViolationException exception,
            HttpServletRequest request
    ) {
        List<CatalogApiDtos.FieldError> fields = exception.getConstraintViolations().stream()
                .map(violation -> new CatalogApiDtos.FieldError(
                        lastSegment(violation.getPropertyPath().toString()),
                        violation.getMessage()
                ))
                .toList();
        return validation(fields, request);
    }

    @ExceptionHandler(MethodArgumentTypeMismatchException.class)
    ResponseEntity<CatalogApiDtos.ErrorResponse> handleType(
            MethodArgumentTypeMismatchException exception,
            HttpServletRequest request
    ) {
        return validation(List.of(new CatalogApiDtos.FieldError(exception.getName(), "invalid_format")), request);
    }

    @ExceptionHandler(MissingServletRequestParameterException.class)
    ResponseEntity<CatalogApiDtos.ErrorResponse> handleMissing(
            MissingServletRequestParameterException exception,
            HttpServletRequest request
    ) {
        return validation(List.of(new CatalogApiDtos.FieldError(exception.getParameterName(), "required")), request);
    }

    @ExceptionHandler(DataAccessException.class)
    ResponseEntity<CatalogApiDtos.ErrorResponse> handleDatabase(
            DataAccessException exception,
            HttpServletRequest request
    ) {
        boolean c3 = isC3(request);
        boolean c2b = isC2B(request);
        boolean c1 = isC1(request);
        log.error(c3 ? "C3 local database transaction failed"
                : c2b ? "Recommendation database transaction failed"
                : c1 ? "C1 database transaction failed" : "Catalog database read failed", exception);
        return response(
                HttpStatus.SERVICE_UNAVAILABLE,
                c3 ? "CATALOG_MATERIALIZATION_UNAVAILABLE"
                        : c2b ? "RECOMMENDATION_UNAVAILABLE"
                        : c1 ? "RATING_SERVICE_UNAVAILABLE" : "CATALOG_UNAVAILABLE",
                c3 ? "완전한 OTT 영화 목록을 준비하지 못했어요."
                        : c2b ? "추천을 불러올 수 없어요. 잠시 후 다시 시도해 주세요."
                        : c1 ? "평가 서비스를 사용할 수 없어요. 잠시 후 다시 시도해 주세요."
                        : "영화 정보를 불러올 수 없어요. 잠시 후 다시 시도해 주세요.",
                List.of(),
                request
        );
    }

    @ExceptionHandler(TransactionException.class)
    ResponseEntity<CatalogApiDtos.ErrorResponse> handleTransaction(
            TransactionException exception,
            HttpServletRequest request
    ) {
        boolean c3 = isC3(request);
        boolean c2b = isC2B(request);
        log.error(c3 ? "C3 local database transaction failed"
                : c2b ? "Recommendation database transaction failed" : "C1 database transaction failed", exception);
        return response(
                HttpStatus.SERVICE_UNAVAILABLE,
                c3 ? "CATALOG_MATERIALIZATION_UNAVAILABLE"
                        : c2b ? "RECOMMENDATION_UNAVAILABLE" : "RATING_SERVICE_UNAVAILABLE",
                c3 ? "완전한 OTT 영화 목록을 준비하지 못했어요."
                        : c2b ? "추천을 불러올 수 없어요. 잠시 후 다시 시도해 주세요."
                        : "평가 서비스를 사용할 수 없어요. 잠시 후 다시 시도해 주세요.",
                List.of(),
                request
        );
    }

    @ExceptionHandler(MissingRequestHeaderException.class)
    ResponseEntity<CatalogApiDtos.ErrorResponse> handleMissingHeader(
            MissingRequestHeaderException exception,
            HttpServletRequest request
    ) {
        return validation(List.of(new CatalogApiDtos.FieldError(exception.getHeaderName(), "required")), request);
    }

    @ExceptionHandler(HttpMessageNotReadableException.class)
    ResponseEntity<CatalogApiDtos.ErrorResponse> handleUnreadableBody(
            HttpMessageNotReadableException exception,
            HttpServletRequest request
    ) {
        return validation(List.of(new CatalogApiDtos.FieldError("body", "invalid_format")), request);
    }

    private ResponseEntity<CatalogApiDtos.ErrorResponse> validation(
            List<CatalogApiDtos.FieldError> fields,
            HttpServletRequest request
    ) {
        return response(HttpStatus.BAD_REQUEST, "VALIDATION_ERROR", "요청 값을 확인해 주세요.", fields, request);
    }

    private ResponseEntity<CatalogApiDtos.ErrorResponse> response(
            HttpStatus status,
            String code,
            String message,
            List<CatalogApiDtos.FieldError> fields,
            HttpServletRequest request
    ) {
        Object trace = request.getAttribute(TraceIdFilter.TRACE_ID_ATTRIBUTE);
        String traceId = trace == null ? "unavailable" : trace.toString();
        return ResponseEntity.status(status)
                .body(new CatalogApiDtos.ErrorResponse(code, message, traceId, fields));
    }

    private static String lastSegment(String path) {
        int position = path.lastIndexOf('.');
        return position < 0 ? path : path.substring(position + 1);
    }

    private static boolean isC1(HttpServletRequest request) {
        String path = request.getRequestURI();
        return path.startsWith("/api/v1/watch-intents")
                || path.startsWith("/api/v1/me/watch-intents")
                || path.startsWith("/api/v1/me/viewing-records")
                || path.startsWith("/api/v1/me/ratings")
                || path.startsWith("/api/v1/me/film")
                || path.startsWith("/api/v1/me/popcorn-bucket")
                || path.startsWith("/api/v1/me/taste-profile");
    }

    private static boolean isC2B(HttpServletRequest request) {
        String path = request.getRequestURI();
        return path.startsWith("/api/v1/me/recommendations/personal-discovery")
                || path.startsWith("/api/v1/me/recommendation-deliveries")
                || path.startsWith("/api/v1/me/recommendation-delivery-items");
    }

    private static boolean isC3(HttpServletRequest request) {
        String path = request.getRequestURI();
        return path.startsWith("/api/v1/me/ott-catalog-comparisons")
                || path.startsWith("/api/v1/me/parties")
                || path.startsWith("/api/v1/me/party-invitations")
                || path.startsWith("/api/v1/parties/");
    }
}
