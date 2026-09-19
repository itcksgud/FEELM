"""Deterministic dense GBT features for an arbitrary 0-N rating history."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass


GENRES = (
    "Action", "Adventure", "Animation", "Children", "Comedy", "Crime",
    "Documentary", "Drama", "Fantasy", "Film-Noir", "Horror", "IMAX",
    "Musical", "Mystery", "Romance", "Sci-Fi", "Thriller", "War",
    "Western", "(no genres listed)",
)


@dataclass(frozen=True)
class Movie:
    movie_id: int
    genres: frozenset[str]
    year: int | None


def parse_movie(movie_id: int, title: str, genres: str) -> Movie:
    match = re.search(r"\((\d{4})\)\s*$", title)
    year = int(match.group(1)) if match else None
    values = frozenset(genres.split("|")) if genres else frozenset({"(no genres listed)"})
    return Movie(movie_id, values, year)


def feature_names() -> list[str]:
    names = [f"shared.candidate.genre_{genre}" for genre in GENRES]
    names += ["shared.candidate.year_scaled", "shared.candidate.year_missing"]
    names += ["gbt.model_input.candidate_genre_count_scaled"]
    names += [
        "shared.history_response.present", "shared.history_response.n_log",
        "shared.history_response.total_log", "shared.history_response.supported_fraction",
        "shared.history_response.rating_mean", "shared.history_response.rating_std",
        "shared.history_response.rating_min", "shared.history_response.rating_max",
        "shared.history_response.positive_fraction", "shared.history_response.negative_fraction",
        "shared.history_response.recent5_mean", "shared.history_response.span_days_log",
    ]
    names += [
        "gbt.model_input.relation_shared_genre_fraction",
        "gbt.model_input.relation_positive_shared_fraction",
        "gbt.model_input.relation_negative_shared_fraction",
        "gbt.model_input.relation_shared_rating_mean",
        "gbt.model_input.relation_year_distance_mean",
        "gbt.model_input.relation_year_distance_min",
        "gbt.model_input.relation_year_missing_fraction",
    ]
    return names


def profile_names(profile: str) -> list[str]:
    names = feature_names()
    if profile == "movie_only":
        return [name for name in names if name.startswith("shared.candidate.") or
                name == "gbt.model_input.candidate_genre_count_scaled"]
    if profile == "history_aggregate":
        return [name for name in names if ".relation_" not in name]
    if profile == "response_relation":
        return names
    raise ValueError(f"unknown profile: {profile}")


def build_features(
    candidate: Movie,
    history: list[dict[str, float | int | str]],
    movies: dict[int, Movie],
    total_history_count: int,
    supported_history_count: int,
) -> dict[str, float]:
    result = {f"shared.candidate.genre_{genre}": float(genre in candidate.genres) for genre in GENRES}
    result.update({
        "shared.candidate.year_scaled": ((candidate.year - 1900) / 150) if candidate.year else 0.0,
        "shared.candidate.year_missing": float(candidate.year is None),
        "gbt.model_input.candidate_genre_count_scaled": min(len(candidate.genres), 10) / 10,
    })
    ratings = [float(row["rating"]) for row in history]
    stamps = [int(row["event_at"]) for row in history]
    count = len(ratings)
    mean = sum(ratings) / count if count else 0.0
    variance = sum((rating - mean) ** 2 for rating in ratings) / count if count else 0.0
    result.update({
        "shared.history_response.present": float(bool(count)),
        "shared.history_response.n_log": math.log1p(count) / math.log(51),
        "shared.history_response.total_log": math.log1p(total_history_count) / math.log(1001),
        "shared.history_response.supported_fraction": supported_history_count / count if count else 0.0,
        "shared.history_response.rating_mean": mean / 5,
        "shared.history_response.rating_std": math.sqrt(variance) / 2.5,
        "shared.history_response.rating_min": min(ratings) / 5 if count else 0.0,
        "shared.history_response.rating_max": max(ratings) / 5 if count else 0.0,
        "shared.history_response.positive_fraction": sum(rating >= 4 for rating in ratings) / count if count else 0.0,
        "shared.history_response.negative_fraction": sum(rating <= 2 for rating in ratings) / count if count else 0.0,
        "shared.history_response.recent5_mean": sum(ratings[-5:]) / (5 * min(count, 5)) if count else 0.0,
        "shared.history_response.span_days_log": math.log1p((max(stamps) - min(stamps)) / 86400) / math.log(3651)
        if count > 1 else 0.0,
    })
    shared = positive_shared = negative_shared = 0
    shared_ratings: list[float] = []
    year_distances: list[int] = []
    year_missing = 0
    for item in history:
        movie_id = int(item["movie_id"])
        rating = float(item["rating"])
        movie = movies.get(int(movie_id))
        if movie is None:
            year_missing += 1
            continue
        if candidate.genres & movie.genres:
            shared += 1
            shared_ratings.append(float(rating))
            positive_shared += float(rating) >= 4
            negative_shared += float(rating) <= 2
        if candidate.year is None or movie.year is None:
            year_missing += 1
        else:
            year_distances.append(abs(candidate.year - movie.year))
    result.update({
        "gbt.model_input.relation_shared_genre_fraction": shared / count if count else 0.0,
        "gbt.model_input.relation_positive_shared_fraction": positive_shared / count if count else 0.0,
        "gbt.model_input.relation_negative_shared_fraction": negative_shared / count if count else 0.0,
        "gbt.model_input.relation_shared_rating_mean": sum(shared_ratings) / (5 * len(shared_ratings))
        if shared_ratings else 0.0,
        "gbt.model_input.relation_year_distance_mean": min(sum(year_distances) / (150 * len(year_distances)), 1.0)
        if year_distances else 0.0,
        "gbt.model_input.relation_year_distance_min": min(min(year_distances) / 150, 1.0)
        if year_distances else 0.0,
        "gbt.model_input.relation_year_missing_fraction": year_missing / count if count else 0.0,
    })
    if set(result) != set(feature_names()) or not all(math.isfinite(value) for value in result.values()):
        raise ValueError("feature schema or finite-value invariant failed")
    return result
