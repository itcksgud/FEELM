"""Leakage-safe signed genre/tag features for the FM-v4 mechanism test.

The interaction fields deliberately contain raw signed Q values.  Scalar numeric
features are FIT-only min/max scaled, while candidate distributions, bounded A
profiles, and gates are naturally in [0, 1].
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass
from typing import Iterable, Iterator, Mapping, Sequence


@dataclass(frozen=True)
class Movie:
    movie_id: int
    year: int | None
    genres: tuple[str, ...]


def parse_movie(movie_id: int, title: str, genres: str) -> Movie:
    match = re.search(r"\((\d{4})\)\s*$", title)
    values = tuple(sorted(value for value in genres.split("|")
                          if value and value != "(no genres listed)"))
    return Movie(movie_id, int(match.group(1)) if match else None, values)


def normalize_tag(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).lower().strip().split())


def _sha(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


class ExternalTagStore:
    """Earliest-observation store after contributor/movie/tag de-duplication."""

    def __init__(self, records: Iterable[Mapping[str, object]], excluded_uids: set[int]):
        earliest: dict[tuple[int, int, str], int] = {}
        raw_rows = normalized_rows = 0
        for record in records:
            raw_rows += 1
            uid = int(record.get("userId", record.get("uid", 0)))
            if uid in excluded_uids:
                continue
            tag = normalize_tag(str(record.get("tag", "")))
            if not tag:
                continue
            normalized_rows += 1
            movie_id = int(record.get("movieId", record.get("movie_id", 0)))
            timestamp = int(record.get("timestamp", record.get("event_at", 0)))
            key = uid, movie_id, tag
            if key not in earliest or timestamp < earliest[key]:
                earliest[key] = timestamp
        by_movie: dict[int, list[tuple[str, int, int]]] = defaultdict(list)
        for (uid, movie_id, tag), timestamp in earliest.items():
            by_movie[movie_id].append((tag, uid, timestamp))
        self.by_movie = {movie_id: tuple(sorted(values, key=lambda item: (item[2], item[0], item[1])))
                         for movie_id, values in by_movie.items()}
        self.raw_rows = raw_rows
        self.normalized_rows = normalized_rows
        self.deduplicated_rows = len(earliest)
        self.excluded_contributors = len(excluded_uids)
        self._allowed_by_digest: dict[str, set[str]] = {}
        self._vector_cache: OrderedDict[tuple[str, int, int], tuple[dict[str, float], str]] = OrderedDict()
        self._vector_cache_size = 100_000

    def vocabulary(self, timestamp_exclusive: int, min_movie_df: int, maximum_size: int) -> dict:
        documents: dict[str, set[int]] = defaultdict(set)
        fit_movies: set[int] = set()
        for movie_id, values in self.by_movie.items():
            tags = {tag for tag, _, timestamp in values if timestamp < timestamp_exclusive}
            if tags:
                fit_movies.add(movie_id)
            for tag in tags:
                documents[tag].add(movie_id)
        ranked = sorted(((tag, len(movie_ids)) for tag, movie_ids in documents.items()
                         if len(movie_ids) >= min_movie_df), key=lambda item: (-item[1], item[0]))
        selected = ranked[:maximum_size]
        movie_count = len(fit_movies)
        idf = {tag: math.log((1.0 + movie_count) / (1.0 + df)) + 1.0 for tag, df in selected}
        payload = {
            "values": [tag for tag, _ in selected],
            "document_frequency": {tag: df for tag, df in selected},
            "idf": idf,
            "fit_movie_count": movie_count,
            "minimum_movie_document_frequency": int(min_movie_df),
            "maximum_size": int(maximum_size),
            "timestamp_exclusive": int(timestamp_exclusive),
            "ranking": "MOVIE_DF_DESC_TAG_ASC",
            "deduplication": "EARLIEST_UNIQUE_CONTRIBUTOR_MOVIE_NORMALIZED_TAG",
            "fit_corpus": "EXTERNAL_USERS_ONLY",
        }
        payload["digest"] = _sha(payload)
        return payload

    def vector(self, movie_id: int, prediction_at: int, vocabulary: dict) -> tuple[dict[str, float], str]:
        digest = str(vocabulary["digest"])
        cache_key = digest, int(movie_id), int(prediction_at)
        cached = self._vector_cache.get(cache_key)
        if cached is not None:
            self._vector_cache.move_to_end(cache_key)
            return cached
        values = self.by_movie.get(int(movie_id), ())
        visible = []
        for tag, uid, timestamp in values:
            if timestamp >= int(prediction_at):
                break
            visible.append((tag, uid))
        if not visible:
            result = ({}, "MISSING")
            self._cache_vector(cache_key, result)
            return result
        contributors: dict[str, set[int]] = defaultdict(set)
        allowed = self._allowed_by_digest.setdefault(digest, set(vocabulary["values"]))
        for tag, uid in visible:
            if tag in allowed:
                contributors[tag].add(uid)
        raw = {tag: math.log1p(len(uids)) * float(vocabulary["idf"][tag])
               for tag, uids in contributors.items()}
        total = sum(raw.values())
        if total <= 0.0:
            result = ({}, "OOV")
            self._cache_vector(cache_key, result)
            return result
        result = ({tag: value / total for tag, value in raw.items()}, "KNOWN")
        self._cache_vector(cache_key, result)
        return result

    def _cache_vector(self, key: tuple[str, int, int], value: tuple[dict[str, float], str]) -> None:
        self._vector_cache[key] = value
        self._vector_cache.move_to_end(key)
        if len(self._vector_cache) > self._vector_cache_size:
            self._vector_cache.popitem(last=False)


def fit_genre_vocabulary(fit_rows: Iterable[dict], movies: Mapping[int, Movie]) -> dict:
    values: set[str] = set()
    rows = 0
    for row in fit_rows:
        rows += 1
        movie_ids = [int(row["target_movie_id"])] + [int(event["movie_id"]) for event in row["history"]]
        for movie_id in movie_ids:
            movie = movies.get(movie_id)
            if movie is not None:
                values.update(movie.genres)
    if not rows:
        raise RuntimeError("genre vocabulary FIT rows are empty")
    payload = {"values": sorted(values), "fit_roles": ["TRAIN"],
               "fit_internal_splits": ["FIT"], "fit_rows": rows}
    payload["digest"] = _sha(payload)
    return payload


def fit_global_rating_mean(fit_rows: Iterable[dict]) -> dict:
    numerator = denominator = 0.0
    rows = 0
    for row in fit_rows:
        weight = float(row["weight_additive"])
        numerator += weight * float(row["target_rating"])
        denominator += weight
        rows += 1
    if rows == 0 or denominator <= 0.0:
        raise RuntimeError("global rating mean FIT rows are empty")
    payload = {"value": numerator / denominator, "weight_sum": denominator, "fit_rows": rows,
               "fit_roles": ["TRAIN"], "fit_internal_splits": ["FIT"],
               "weighting": "EQUAL_USER_TARGET_VARIANT"}
    payload["digest"] = _sha(payload)
    return payload


def _genre_vector(movie: Movie | None, allowed: set[str]) -> tuple[dict[str, float], str]:
    if movie is None or not movie.genres:
        return {}, "MISSING"
    known = [genre for genre in movie.genres if genre in allowed]
    if not known:
        return {}, "OOV"
    # The denominator is all known catalog genres, so OOV mass is not silently reassigned.
    return {genre: 1.0 / len(movie.genres) for genre in known}, "KNOWN"


def _profile(row: dict, movies: Mapping[int, Movie], genre_values: Sequence[str],
             tag_store: ExternalTagStore, tag_vocabulary: dict, global_mean: float,
             feature_config: dict) -> dict:
    history = list(row["history"])
    prediction_at = int(row["prediction_at"])
    half_life = float(feature_config["event_rank_half_life"])
    prior = float(feature_config["user_center_prior_weight"])
    shrinkage = float(feature_config["profile_shrinkage"])
    gate_prior = float(feature_config["confidence_gate_prior"])
    rating_range = float(feature_config["rating_range"])
    # Input is chronological; ranks are defined newest first.
    newest = list(reversed(history))
    weights = [2.0 ** (-rank / half_life) for rank in range(len(newest))]
    weight_sum = sum(weights)
    center = ((prior * global_mean + sum(weight * float(event["rating"])
                                         for weight, event in zip(weights, newest))) /
              (prior + weight_sum))
    signed = [(float(event["rating"]) - center) / rating_range for event in newest]

    fields: dict[str, dict] = {}
    for field in ("genre", "tag"):
        a: dict[str, float] = defaultdict(float)
        numerator: dict[str, float] = defaultdict(float)
        metadata_events = 0
        for weight, signal, event in zip(weights, signed, newest):
            movie = movies.get(int(event["movie_id"]))
            if field == "genre":
                vector, status = _genre_vector(movie, set(genre_values))
            else:
                vector, status = tag_store.vector(int(event["movie_id"]), prediction_at, tag_vocabulary)
            if status == "KNOWN":
                metadata_events += 1
            for name, value in vector.items():
                contribution = weight * value
                a[name] += contribution
                numerator[name] += contribution * signal
        q = {name: numerator[name] / (shrinkage + value) for name, value in a.items()}
        support = sum(a.values())
        gate = math.sqrt(support / (support + gate_prior)) if support > 0.0 else 0.0
        fields[field] = {"a": dict(a), "q": q, "gate": gate,
                         "support": support, "metadata_events": metadata_events}

    ratings = [float(event["rating"]) for event in newest]
    spread = (math.sqrt(sum(weight * (rating - center) ** 2 for weight, rating in zip(weights, ratings)) /
                        weight_sum) if weight_sum else None)
    ages = [max(0.0, (prediction_at - int(event["event_at"])) / 86400.0) for event in newest]
    years = [movies[int(event["movie_id"])].year for event in newest
             if int(event["movie_id"]) in movies and movies[int(event["movie_id"])].year is not None]
    numeric = {
        "history.selected_count": float(len(history)),
        "history.total_count": float(row["total_history_count"]),
        "history.genre_supported_count": float(fields["genre"]["metadata_events"]),
        "history.tag_supported_count": float(fields["tag"]["metadata_events"]),
        "history.rating_center": center if history else None,
        "history.rating_spread": spread,
        "history.latest_age_days": min(ages) if ages else None,
        "history.weighted_age_days": (sum(weight * age for weight, age in zip(weights, ages)) / weight_sum
                                      if weight_sum else None),
        "history.year_mean": (sum(years) / len(years)) if years else None,
    }
    return {"fields": fields, "numeric": numeric, "center": center}


def raw_numeric(row: dict, candidate: Movie, movies: Mapping[int, Movie], genre_vocabulary: dict,
                tag_store: ExternalTagStore, tag_vocabulary: dict, global_mean: float,
                feature_config: dict) -> tuple[dict[str, float | None], dict]:
    profile = _profile(row, movies, genre_vocabulary["values"], tag_store, tag_vocabulary,
                       global_mean, feature_config)
    prediction_at = int(row["prediction_at"])
    candidate_tags, _ = tag_store.vector(candidate.movie_id, prediction_at, tag_vocabulary)
    values = dict(profile["numeric"])
    values.update({
        "candidate.year": float(candidate.year) if candidate.year is not None else None,
        "candidate.genre_count": float(len(candidate.genres)),
        "candidate.tag_count": float(len(candidate_tags)),
        "relation.year_distance": (abs(float(candidate.year) - float(values["history.year_mean"]))
                                   if candidate.year is not None and values["history.year_mean"] is not None else None),
    })
    return values, profile


def fit_scaler(fit_rows: Iterable[dict], movies: Mapping[int, Movie], genre_vocabulary: dict,
               tag_store: ExternalTagStore, tag_vocabulary: dict, global_mean: float,
               feature_config: dict) -> dict:
    values: dict[str, list[float]] = defaultdict(list)
    rows = 0
    for row in fit_rows:
        rows += 1
        candidate = movies[int(row["target_movie_id"])]
        raw, _ = raw_numeric(row, candidate, movies, genre_vocabulary, tag_store, tag_vocabulary,
                             global_mean, feature_config)
        for name, value in raw.items():
            if value is not None:
                values[name].append(float(value))
    if not rows:
        raise RuntimeError("numeric scaler FIT rows are empty")
    fitted = {name: {"minimum": min(observed), "maximum": max(observed),
                     "scale": max(observed) - min(observed) if max(observed) > min(observed) else 1.0,
                     "transform": "MINMAX_CLIP"}
              for name, observed in sorted(values.items())}
    payload = {"fit_roles": ["TRAIN"], "fit_internal_splits": ["FIT"], "fit_rows": rows,
               "range": [0.0, 1.0], "values": fitted}
    payload["digest"] = _sha(payload)
    return payload


def _scaled(name: str, value: float, scaler: dict) -> tuple[float, bool]:
    spec = scaler["values"][name]
    clipped = min(float(spec["maximum"]), max(float(spec["minimum"]), value))
    return (clipped - float(spec["minimum"])) / float(spec["scale"]), clipped != value


def ordered_feature_names(genre_vocabulary: dict, tag_vocabulary: dict, scaler: dict) -> list[str]:
    names: list[str] = []
    for field, vocabulary in (("genre", genre_vocabulary), ("tag", tag_vocabulary)):
        values = vocabulary["values"]
        names.extend(f"candidate.{field}:{value}" for value in values)
        names.extend((f"candidate.{field}:__OOV__", f"candidate.{field}:__MISSING__"))
        names.extend(f"profile.{field}.a:{value}" for value in values)
        names.extend(f"profile.{field}.q:{value}" for value in values)
        names.extend((f"profile.{field}:__MISSING__", f"profile.{field}.gate:value"))
    for name in scaler["values"]:
        names.extend((f"{name}:scaled", f"{name}:__MISSING__"))
    names.extend((
        "history.empty:indicator",
        "relation.positive_genre_overlap:value",
        "relation.negative_genre_overlap:value",
    ))
    return names


def feature_namespace(name: str) -> str:
    return name.split(":", 1)[0]


def build_feature_map(row: dict, candidate: Movie, movies: Mapping[int, Movie],
                      genre_vocabulary: dict, tag_store: ExternalTagStore, tag_vocabulary: dict,
                      global_mean: float, scaler: dict, feature_config: dict) -> tuple[dict[str, float], dict]:
    output: dict[str, float] = {}
    diagnostics = {name: Counter() for name in ("active", "missing", "oov", "clipped")}

    def activate(name: str, value: float = 1.0) -> None:
        if not math.isfinite(value) or value < -1.0 or value > 1.0:
            raise RuntimeError(f"feature outside [-1,1]: {name}={value}")
        if value != 0.0:
            output[name] = float(value)
            diagnostics["active"][feature_namespace(name)] += 1

    genre_allowed = set(genre_vocabulary["values"])
    candidate_genres, genre_status = _genre_vector(candidate, genre_allowed)
    candidate_tags, tag_status = tag_store.vector(candidate.movie_id, int(row["prediction_at"]), tag_vocabulary)
    for field, vector, status in (("genre", candidate_genres, genre_status),
                                  ("tag", candidate_tags, tag_status)):
        for name, value in vector.items():
            activate(f"candidate.{field}:{name}", value)
        if field == "genre" and candidate.genres:
            unknown_mass = sum(genre not in genre_allowed for genre in candidate.genres) / len(candidate.genres)
            if unknown_mass:
                activate("candidate.genre:__OOV__", unknown_mass)
                diagnostics["oov"]["candidate.genre"] += 1
        if status == "MISSING":
            activate(f"candidate.{field}:__MISSING__")
            diagnostics["missing"][f"candidate.{field}"] += 1
        elif status == "OOV" and field != "genre":
            activate(f"candidate.{field}:__OOV__")
            diagnostics["oov"][f"candidate.{field}"] += 1

    raw, profile = raw_numeric(row, candidate, movies, genre_vocabulary, tag_store, tag_vocabulary,
                               global_mean, feature_config)
    for field in ("genre", "tag"):
        values = profile["fields"][field]
        if values["gate"] == 0.0:
            activate(f"profile.{field}:__MISSING__")
            diagnostics["missing"][f"profile.{field}"] += 1
        else:
            activate(f"profile.{field}.gate:value", float(values["gate"]))
        for name, value in values["a"].items():
            activate(f"profile.{field}.a:{name}", value / (1.0 + value))
        for name, value in values["q"].items():
            activate(f"profile.{field}.q:{name}", value)

    for name, value in raw.items():
        if value is None:
            activate(f"{name}:__MISSING__")
            diagnostics["missing"][name] += 1
        else:
            scaled, clipped = _scaled(name, float(value), scaler)
            activate(f"{name}:scaled", scaled)
            if clipped:
                diagnostics["clipped"][name] += 1
    if not row["history"]:
        activate("history.empty:indicator")

    threshold = float(feature_config["positive_threshold_diagnostic_only"])
    positive: set[str] = set()
    negative: set[str] = set()
    for event in row["history"]:
        movie = movies.get(int(event["movie_id"]))
        if movie is None:
            continue
        target = positive if float(event["rating"]) >= threshold else negative
        target.update(movie.genres)
    denominator = max(1, len(candidate.genres))
    activate("relation.positive_genre_overlap:value", len(set(candidate.genres) & positive) / denominator)
    activate("relation.negative_genre_overlap:value", len(set(candidate.genres) & negative) / denominator)
    return output, {name: dict(values) for name, values in diagnostics.items()}


def vectorize(features: Mapping[str, float], names: Sequence[str]) -> tuple[list[int], list[float]]:
    indices = {name: index for index, name in enumerate(names)}
    unknown = sorted(set(features) - set(indices))
    if unknown:
        raise RuntimeError(f"feature names absent from schema: {unknown[:3]}")
    pairs = sorted((indices[name], float(value)) for name, value in features.items() if value != 0.0)
    return [index for index, _ in pairs], [value for _, value in pairs]


def schema_document(genre_vocabulary: dict, tag_vocabulary: dict, scaler: dict,
                    global_mean: dict, feature_config: dict, model_config: dict) -> dict:
    names = ordered_feature_names(genre_vocabulary, tag_vocabulary, scaler)
    index = {name: offset for offset, name in enumerate(names)}
    namespaces: dict[str, list[int]] = defaultdict(list)
    for offset, name in enumerate(names):
        namespaces[feature_namespace(name)].append(offset)
    interaction_fields = {}
    for field, vocabulary in (("genre", genre_vocabulary), ("tag", tag_vocabulary)):
        values = list(vocabulary["values"])
        interaction_fields[field] = {
            "values": values,
            "candidate_indices": [index[f"candidate.{field}:{value}"] for value in values],
            "q_indices": [index[f"profile.{field}.q:{value}"] for value in values],
            "gate_index": index[f"profile.{field}.gate:value"],
        }
    all_indices = list(range(len(names)))
    profiles = {
        "matched_additive_v4": {"linear_indices": all_indices, "interaction_fields": []},
        "signed_genre_tag_fm_v4": {"linear_indices": all_indices,
                                    "interaction_fields": ["genre", "tag"]},
    }
    diagnostics = {
        "signed_genre_diagonal_v4": {"linear_indices": all_indices,
                                      "fields": {"genre": ["diagonal"]}},
        "signed_genre_diag_rank4_v4": {"linear_indices": all_indices,
                                        "fields": {"genre": ["diagonal", "off_diagonal_rank4"]}},
    }
    payload = {
        "schema_version": 4,
        "model_type": "MATCHED_ADDITIVE_PLUS_FROZEN_SIGNED_FIELD_AWARE_INTERACTIONS",
        "feature_profile_version": "fm-v4-rolling-recent-signed-genre-tag-v1",
        "dtype": "float64_sparse",
        "value_range": [-1.0, 1.0],
        "ordered_names": names,
        "namespaces": dict(sorted(namespaces.items())),
        "profiles": profiles,
        "diagnostic_profiles": diagnostics,
        "interaction_fields": interaction_fields,
        "genre_vocabulary": genre_vocabulary,
        "tag_vocabulary": tag_vocabulary,
        "scaler": scaler,
        "global_rating_mean": global_mean,
        "constants": {name: feature_config[name] for name in (
            "event_rank_half_life", "user_center_prior_weight", "profile_shrinkage",
            "confidence_gate_prior", "rating_range", "positive_threshold_diagnostic_only")},
        "additive_transforms": {
            "profile_a": "A/(1+A)",
            "profile_q": "RAW_SIGNED_Q",
            "scalar_numeric": "TRAIN_INNER_FIT_MINMAX_CLIP",
        },
        "factor_rank": int(model_config["factor_rank"]),
        "user_id_embedding": False,
        "candidate_movie_id_feature": False,
        "interaction_contract": {
            "formula": "gate*(diagonal(candidate,Q)+masked_asymmetric_off_diagonal_rank4(candidate,Q))",
            "allowed": ["candidate.genre x profile.genre.q", "candidate.tag x profile.tag.q"],
            "forbidden": ["genre x tag", "candidate x candidate", "history x history",
                          "relation x factor", "user identity", "movie identity"],
            "additive_parameters_during_factor_fit": "FROZEN",
            "n0": "both Q fields and both gates are exact zero",
        },
    }
    payload["ordered_names_sha256"] = hashlib.sha256("\n".join(names).encode()).hexdigest()
    payload["profile_digest"] = _sha(payload)
    return payload


__all__ = [
    "ExternalTagStore", "Movie", "build_feature_map", "fit_genre_vocabulary",
    "fit_global_rating_mean", "fit_scaler", "normalize_tag", "parse_movie",
    "raw_numeric", "schema_document", "vectorize",
]
