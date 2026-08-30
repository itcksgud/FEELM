from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Any, Callable

from .errors import TmdbAuthenticationError, TmdbError, TmdbNotFound
from .identity import IdentityEntry
from .movielens import MovieLensMovie, normalize_imdb_id
from .tmdb import TmdbGateway


IDENTITY_VERIFIED = "IDENTITY_VERIFIED"
TYPE_MISMATCH_TV = "TYPE_MISMATCH_TV"
TMDB_NOT_FOUND = "TMDB_NOT_FOUND"
IDENTITY_REVIEW_REQUIRED = "IDENTITY_REVIEW_REQUIRED"

MONETIZATION_TYPES = {
    "flatrate": "FLATRATE",
    "rent": "RENT",
    "buy": "BUY",
    "free": "FREE",
    "ads": "ADS",
}


def record(record_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"recordType": record_type, "payload": payload}


@dataclass(frozen=True)
class IdentityResolution:
    status: str
    details: dict[str, Any] | None
    resolved_tmdb_id: int | None
    resolution_method: str
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class NormalizedMovie:
    movie: MovieLensMovie
    identity: IdentityEntry
    identity_status: str
    visibility_status: str | None
    resolved_tmdb_id: int | None
    records: tuple[dict[str, Any], ...]
    safe_errors: tuple[str, ...]
    recovered: bool


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _year(value: Any) -> int | None:
    text = _text(value)
    if not text or len(text) < 4 or not text[:4].isdigit():
        return None
    return int(text[:4])


def _normalized_title(value: str | None) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _title_similarity(expected: str, candidates: list[str | None]) -> float:
    expected_normalized = _normalized_title(expected)
    if not expected_normalized:
        return 0.0
    return max(
        (
            SequenceMatcher(None, expected_normalized, _normalized_title(candidate)).ratio()
            for candidate in candidates
            if _normalized_title(candidate)
        ),
        default=0.0,
    )


def _details_match(
    movie: MovieLensMovie, details: dict[str, Any], title_threshold: float
) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    actual_imdb = normalize_imdb_id(_text(details.get("imdb_id")))
    if movie.imdb_id and actual_imdb != movie.imdb_id:
        reasons.append("IMDB_ID_MISMATCH")
    actual_year = _year(details.get("release_date"))
    if movie.release_year is not None and actual_year is not None and movie.release_year != actual_year:
        reasons.append("RELEASE_YEAR_MISMATCH")
    similarity = _title_similarity(
        movie.title, [_text(details.get("title")), _text(details.get("original_title"))]
    )
    if similarity < title_threshold:
        reasons.append("TITLE_SIMILARITY_LOW")
    return not reasons, tuple(reasons)


def resolve_identity(
    movie: MovieLensMovie, gateway: TmdbGateway, title_threshold: float
) -> IdentityResolution:
    direct_errors: list[str] = []
    if movie.tmdb_id is not None:
        try:
            details = gateway.details(movie.tmdb_id)
            matched, reasons = _details_match(movie, details, title_threshold)
            if matched:
                return IdentityResolution(
                    IDENTITY_VERIFIED, details, movie.tmdb_id, "MOVIELENS_TMDB_VERIFIED"
                )
            direct_errors.extend(reasons)
        except TmdbNotFound:
            direct_errors.append("STALE_TMDB_ID_404")
        except TmdbAuthenticationError:
            raise
        except TmdbError as error:
            direct_errors.append(error.code)

    if not movie.imdb_id:
        status = TMDB_NOT_FOUND if movie.tmdb_id is None else IDENTITY_REVIEW_REQUIRED
        return IdentityResolution(status, None, None, "NO_IMDB_RECOVERY_KEY", tuple(direct_errors))

    try:
        found = gateway.find_by_imdb(movie.imdb_id)
    except TmdbAuthenticationError:
        raise
    except TmdbError as error:
        return IdentityResolution(
            IDENTITY_REVIEW_REQUIRED,
            None,
            None,
            "IMDB_FIND_FAILED",
            tuple(direct_errors + [error.code]),
        )

    movie_results = found.get("movie_results") or []
    tv_results = found.get("tv_results") or []
    if not movie_results:
        if tv_results:
            return IdentityResolution(
                TYPE_MISMATCH_TV, None, None, "IMDB_FIND_TV_ONLY", tuple(direct_errors)
            )
        return IdentityResolution(TMDB_NOT_FOUND, None, None, "IMDB_FIND_EMPTY", tuple(direct_errors))
    if len(movie_results) != 1:
        return IdentityResolution(
            IDENTITY_REVIEW_REQUIRED,
            None,
            None,
            "IMDB_FIND_AMBIGUOUS",
            tuple(direct_errors + ["MULTIPLE_MOVIE_RESULTS"]),
        )

    candidate = movie_results[0]
    candidate_id = candidate.get("id")
    if not isinstance(candidate_id, int):
        return IdentityResolution(
            IDENTITY_REVIEW_REQUIRED,
            None,
            None,
            "IMDB_FIND_INVALID_RESULT",
            tuple(direct_errors + ["MISSING_TMDB_ID"]),
        )
    candidate_year = _year(candidate.get("release_date"))
    candidate_similarity = _title_similarity(
        movie.title, [_text(candidate.get("title")), _text(candidate.get("original_title"))]
    )
    if (
        movie.release_year is not None
        and candidate_year is not None
        and movie.release_year != candidate_year
    ) or candidate_similarity < title_threshold:
        return IdentityResolution(
            IDENTITY_REVIEW_REQUIRED,
            None,
            None,
            "IMDB_FIND_CANDIDATE_MISMATCH",
            tuple(direct_errors + ["FIND_CANDIDATE_VALIDATION_FAILED"]),
        )

    try:
        details = gateway.details(candidate_id)
    except TmdbAuthenticationError:
        raise
    except TmdbError as error:
        return IdentityResolution(
            IDENTITY_REVIEW_REQUIRED,
            None,
            None,
            "RECOVERED_DETAILS_FAILED",
            tuple(direct_errors + [error.code]),
        )
    matched, reasons = _details_match(movie, details, title_threshold)
    if not matched:
        return IdentityResolution(
            IDENTITY_REVIEW_REQUIRED,
            None,
            None,
            "RECOVERED_DETAILS_MISMATCH",
            tuple(direct_errors + list(reasons)),
        )
    return IdentityResolution(
        IDENTITY_VERIFIED,
        details,
        candidate_id,
        "RECOVERED_BY_IMDB",
        tuple(direct_errors),
    )


def _external_ids(
    movie: MovieLensMovie, resolution: IdentityResolution, now: datetime
) -> list[dict[str, Any]]:
    verified_at = now.isoformat() if resolution.status == IDENTITY_VERIFIED else None
    result = [
        {
            "source": "MOVIELENS",
            "externalId": str(movie.movie_lens_id),
            "verificationStatus": "VERIFIED",
            "verifiedAt": verified_at,
        }
    ]
    if movie.imdb_id:
        result.append(
            {
                "source": "IMDB",
                "externalId": movie.imdb_id,
                "verificationStatus": "VERIFIED" if verified_at else "UNVERIFIED",
                "verifiedAt": verified_at,
            }
        )
    if movie.tmdb_id is not None:
        is_current = movie.tmdb_id == resolution.resolved_tmdb_id
        result.append(
            {
                "source": "TMDB",
                "externalId": str(movie.tmdb_id),
                "verificationStatus": "VERIFIED" if verified_at and is_current else "UNVERIFIED",
                "verifiedAt": verified_at if is_current else None,
            }
        )
    if resolution.resolved_tmdb_id is not None and resolution.resolved_tmdb_id != movie.tmdb_id:
        result.append(
            {
                "source": "TMDB",
                "externalId": str(resolution.resolved_tmdb_id),
                "verificationStatus": "RECOVERED",
                "verifiedAt": verified_at,
            }
        )
    return result


def _identity_record(
    movie: MovieLensMovie,
    identity: IdentityEntry,
    resolution: IdentityResolution,
    now: datetime,
) -> dict[str, Any]:
    return record(
        "movieIdentity",
        {
            "movieId": identity.movie_id,
            "createdAt": identity.created_at,
            "identityStatus": resolution.status,
            "externalIds": _external_ids(movie, resolution, now),
            "provenance": {
                "movielensTitle": movie.title,
                "movielensReleaseYear": movie.release_year,
                "resolutionMethod": resolution.resolution_method,
                "previousTmdbId": movie.tmdb_id
                if movie.tmdb_id != resolution.resolved_tmdb_id
                else None,
            },
        },
    )


def _locale(translation: dict[str, Any]) -> str | None:
    language = _text(translation.get("iso_639_1"))
    if not language:
        return None
    language = language.lower()
    country = _text(translation.get("iso_3166_1"))
    if not country:
        country = {"ko": "KR", "en": "US"}.get(language)
    return f"{language}-{country.upper()}" if country else language


def _localizations(
    movie_id: str,
    details: dict[str, Any],
    translations_payload: dict[str, Any],
    now: datetime,
) -> tuple[list[dict[str, Any]], str | None, str | None]:
    values: dict[str, dict[str, str | None]] = {}
    english = {
        "title": _text(details.get("title")),
        "overview": _text(details.get("overview")),
    }
    if english["title"] or english["overview"]:
        values["en-US"] = english
    for translation in translations_payload.get("translations") or []:
        locale = _locale(translation)
        data = translation.get("data") or {}
        if not locale:
            continue
        title = _text(data.get("title"))
        overview = _text(data.get("overview"))
        if not title and not overview:
            continue
        existing = values.setdefault(locale, {"title": None, "overview": None})
        if title:
            existing["title"] = title
        if overview:
            existing["overview"] = overview
    original_language = _text(details.get("original_language"))
    original_title = _text(details.get("original_title"))
    if original_language and original_title:
        locale = original_language.lower()
        existing = values.setdefault(locale, {"title": None, "overview": None})
        existing["title"] = existing["title"] or original_title

    records = [
        record(
            "localization",
            {
                "movieId": movie_id,
                "locale": locale,
                "title": value["title"],
                "overview": value["overview"],
                "source": "TMDB",
                "fetchedAt": now.isoformat(),
            },
        )
        for locale, value in sorted(values.items())
    ]
    title_candidates = [
        values.get("ko-KR", {}).get("title"),
        values.get("en-US", {}).get("title"),
        original_title,
    ]
    overview_candidates = [
        values.get("ko-KR", {}).get("overview"),
        values.get("en-US", {}).get("overview"),
        _text(details.get("overview")),
    ]
    display_title = next((value for value in title_candidates if value), None)
    display_overview = next((value for value in overview_candidates if value), None)
    return records, display_title, display_overview


def _slug(value: str) -> str:
    text = unicodedata.normalize("NFKD", value).upper()
    slug = re.sub(r"[^A-Z0-9]+", "_", text).strip("_")
    return slug or "UNKNOWN"


def _genres(
    movie_id: str, details: dict[str, Any], movielens_genres: tuple[str, ...]
) -> list[dict[str, Any]]:
    tmdb_genres = details.get("genres") or []
    if tmdb_genres:
        return [
            record(
                "genre",
                {
                    "movieId": movie_id,
                    "code": f"TMDB_{item['id']}",
                    "displayName": _text(item.get("name")) or f"TMDB {item['id']}",
                    "source": "TMDB",
                    "sourceId": str(item["id"]),
                    "displayOrder": index,
                },
            )
            for index, item in enumerate(tmdb_genres)
            if isinstance(item.get("id"), int)
        ]
    return [
        record(
            "genre",
            {
                "movieId": movie_id,
                "code": f"MOVIELENS_{_slug(name)}",
                "displayName": name,
                "source": "MOVIELENS",
                "sourceId": name,
                "displayOrder": index,
            },
        )
        for index, name in enumerate(movielens_genres)
    ]


def _credits(movie_id: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for order, person in enumerate(payload.get("crew") or []):
        if person.get("job") != "Director" or not isinstance(person.get("id"), int):
            continue
        result.append(
            record(
                "credit",
                {
                    "movieId": movie_id,
                    "creditType": "DIRECTOR",
                    "job": "Director",
                    "tmdbPersonId": person["id"],
                    "displayName": _text(person.get("name")) or "Unknown",
                    "profilePath": _text(person.get("profile_path")),
                    "characterName": "",
                    "creditOrder": order,
                },
            )
        )
    for fallback_order, person in enumerate(payload.get("cast") or []):
        if not isinstance(person.get("id"), int):
            continue
        source_order = person.get("order")
        result.append(
            record(
                "credit",
                {
                    "movieId": movie_id,
                    "creditType": "CAST",
                    "job": "Actor",
                    "tmdbPersonId": person["id"],
                    "displayName": _text(person.get("name")) or "Unknown",
                    "profilePath": _text(person.get("profile_path")),
                    "characterName": _text(person.get("character")) or "",
                    "creditOrder": source_order if isinstance(source_order, int) else fallback_order,
                },
            )
        )
    return result


def _availability(
    movie_id: str,
    payload: dict[str, Any] | None,
    now: datetime,
    snapshot_uuid_factory: Callable[[], uuid.UUID],
    failure_code: str | None,
) -> list[dict[str, Any]]:
    snapshot_id = str(snapshot_uuid_factory())
    kr = ((payload or {}).get("results") or {}).get("KR") or {}
    offers: list[tuple[str, dict[str, Any]]] = []
    if payload is not None:
        for source_name, normalized_type in MONETIZATION_TYPES.items():
            for provider in kr.get(source_name) or []:
                offers.append((normalized_type, provider))
    fetch_status = "FAILED" if payload is None else ("SUCCESS_LISTED" if offers else "SUCCESS_EMPTY")
    records: list[dict[str, Any]] = []
    providers_seen: set[int] = set()
    offers_seen: set[tuple[int, str]] = set()
    for monetization_type, provider in offers:
        provider_id = provider.get("provider_id")
        if not isinstance(provider_id, int):
            continue
        if provider_id not in providers_seen:
            providers_seen.add(provider_id)
            records.append(
                record(
                    "provider",
                    {
                        "tmdbProviderId": provider_id,
                        "providerCode": f"TMDB_{provider_id}",
                        "displayName": _text(provider.get("provider_name")) or f"Provider {provider_id}",
                        "logoPath": _text(provider.get("logo_path")),
                        "displayPriority": provider.get("display_priority")
                        if isinstance(provider.get("display_priority"), int)
                        else 9999,
                    },
                )
            )
        offer_key = (provider_id, monetization_type)
        if offer_key in offers_seen:
            continue
        offers_seen.add(offer_key)
        records.append(
            record(
                "ottOffer",
                {
                    "snapshotId": snapshot_id,
                    "movieId": movie_id,
                    "tmdbProviderId": provider_id,
                    "monetizationType": monetization_type,
                    "linkType": "AGGREGATOR",
                    "landingUrl": _text(kr.get("link")),
                    "sourceDisplayPriority": provider.get("display_priority")
                    if isinstance(provider.get("display_priority"), int)
                    else 9999,
                },
            )
        )
    snapshot = record(
        "availabilitySnapshot",
        {
            "snapshotId": snapshot_id,
            "movieId": movie_id,
            "region": "KR",
            "fetchStatus": fetch_status,
            "source": "TMDB_JUSTWATCH",
            "aggregatorUrl": _text(kr.get("link")),
            "fetchedAt": now.isoformat(),
            "freshUntil": (now + timedelta(hours=24)).isoformat(),
            "serveUntil": (now + timedelta(days=7)).isoformat(),
            "failureCode": failure_code if fetch_status == "FAILED" else None,
        },
    )
    provider_records = [item for item in records if item["recordType"] == "provider"]
    offer_records = [item for item in records if item["recordType"] == "ottOffer"]
    return provider_records + [snapshot] + offer_records


def normalize_movie(
    movie: MovieLensMovie,
    identity: IdentityEntry,
    gateway: TmdbGateway,
    now: datetime,
    *,
    title_threshold: float = 0.65,
    snapshot_uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> NormalizedMovie:
    resolution = resolve_identity(movie, gateway, title_threshold)
    records: list[dict[str, Any]] = [_identity_record(movie, identity, resolution, now)]
    safe_errors = list(resolution.errors)
    if resolution.status != IDENTITY_VERIFIED or resolution.details is None:
        return NormalizedMovie(
            movie,
            identity,
            resolution.status,
            None,
            resolution.resolved_tmdb_id,
            tuple(records),
            tuple(safe_errors),
            False,
        )

    details = resolution.details
    tmdb_id = int(resolution.resolved_tmdb_id)
    try:
        translations = gateway.translations(tmdb_id)
    except TmdbAuthenticationError:
        raise
    except TmdbError as error:
        translations = {}
        safe_errors.append(error.code)
    try:
        credits_payload = gateway.credits(tmdb_id)
    except TmdbAuthenticationError:
        raise
    except TmdbError as error:
        credits_payload = {}
        safe_errors.append(error.code)
    provider_failure: str | None = None
    try:
        providers_payload: dict[str, Any] | None = gateway.watch_providers(tmdb_id)
    except TmdbAuthenticationError:
        raise
    except TmdbError as error:
        providers_payload = None
        provider_failure = error.code
        safe_errors.append(error.code)

    localization_records, display_title, display_overview = _localizations(
        identity.movie_id, details, translations, now
    )
    genre_records = _genres(identity.movie_id, details, movie.genres)
    credit_records = _credits(identity.movie_id, credits_payload)
    directors = [
        item for item in credit_records if item["payload"]["creditType"] == "DIRECTOR"
    ]
    catalog_visible = bool(display_title and display_overview and genre_records)
    ui_ready = bool(
        catalog_visible
        and _text(details.get("poster_path"))
        and isinstance(details.get("runtime"), int)
        and details["runtime"] > 0
        and directors
    )
    visibility_status = "UI_READY" if ui_ready else (
        "CATALOG_VISIBLE" if catalog_visible else "UI_INCOMPLETE"
    )
    runtime = details.get("runtime")
    vote_average = details.get("vote_average")
    vote_count = details.get("vote_count")
    records.append(
        record(
            "movieProjection",
            {
                "movieId": identity.movie_id,
                "mediaType": "MOVIE",
                "identityStatus": IDENTITY_VERIFIED,
                "visibilityStatus": visibility_status,
                "originalTitle": _text(details.get("original_title")) or display_title,
                "originalLanguage": _text(details.get("original_language")) or "und",
                "releaseDate": _text(details.get("release_date")),
                "runtimeMinutes": runtime if isinstance(runtime, int) and runtime > 0 else None,
                "posterPath": _text(details.get("poster_path")),
                "backdropPath": _text(details.get("backdrop_path")),
                "tmdbVoteAverage": float(vote_average)
                if isinstance(vote_average, (int, float)) and 0 <= float(vote_average) <= 10
                else None,
                "tmdbVoteCount": int(vote_count)
                if isinstance(vote_count, int) and vote_count >= 0
                else 0,
                "metadataFetchedAt": now.isoformat(),
                "deleted": False,
            },
        )
    )
    records.extend(localization_records)
    records.extend(genre_records)
    for order, country in enumerate(details.get("production_countries") or []):
        code = _text(country.get("iso_3166_1"))
        if code:
            records.append(
                record(
                    "country",
                    {
                        "movieId": identity.movie_id,
                        "countryCode": code.upper(),
                        "displayName": _text(country.get("name")) or code.upper(),
                        "displayOrder": order,
                    },
                )
            )
    records.extend(credit_records)
    records.extend(
        _availability(
            identity.movie_id,
            providers_payload,
            now,
            snapshot_uuid_factory,
            provider_failure,
        )
    )
    recovered = resolution.resolution_method == "RECOVERED_BY_IMDB"
    return NormalizedMovie(
        movie,
        identity,
        resolution.status,
        visibility_status,
        resolution.resolved_tmdb_id,
        tuple(records),
        tuple(safe_errors),
        recovered,
    )
