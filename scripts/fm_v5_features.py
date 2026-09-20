"""Sparse categorical FM-v5 features for the byte-identical GBT-v7 contract."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from typing import Iterable

import fm_v2_features as base


Movie = base.Movie
parse_movie = base.parse_movie
fit_scaler = base.fit_scaler


_VECTOR_INDEX_CACHE: dict[int, tuple[list[str], dict[str, int]]] = {}


def vectorize(features: dict[str, float], names: list[str]) -> tuple[list[int], list[float]]:
    """Vectorize identically to fm_v2, without rebuilding a large index for every row."""
    cache_key = id(names)
    cached = _VECTOR_INDEX_CACHE.get(cache_key)
    if cached is None or cached[0] is not names:
        cached = (names, {name: index for index, name in enumerate(names)})
        _VECTOR_INDEX_CACHE[cache_key] = cached
    index = cached[1]
    unknown = sorted(name for name in features if name not in index)
    if unknown:
        raise RuntimeError(f"feature names absent from schema: {unknown[:3]}")
    pairs = sorted((index[name], float(value)) for name, value in features.items() if value != 0.0)
    return [item[0] for item in pairs], [item[1] for item in pairs]


def _sha(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def fit_vocabulary(train_rows: Iterable[dict], movies: dict[int, Movie], positive_threshold: float) -> dict:
    rows = list(train_rows)
    document = base.fit_vocabulary(rows, movies, positive_threshold)
    values: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        for event in row["history"]:
            movie_id = int(event["movie_id"])
            namespace = ("history_positive.movie_id" if float(event["rating"]) >= positive_threshold
                         else "history_negative.movie_id")
            if movie_id in movies:
                values[namespace].add(str(movie_id))
    for namespace in ("history_positive.movie_id", "history_negative.movie_id"):
        document["namespaces"][namespace] = {
            value: index for index, value in enumerate(sorted(values.get(namespace, set()), key=int))
        }
    document["digest"] = _sha(document["namespaces"])
    return document


def ordered_feature_names(vocabulary: dict) -> list[str]:
    names: list[str] = []
    namespaces = (
        "candidate.movie_id", "candidate.genre",
        "history_positive.movie_id", "history_positive.genre",
        "history_negative.movie_id", "history_negative.genre",
    )
    for namespace in namespaces:
        values = vocabulary["namespaces"][namespace]
        names.extend(f"{namespace}:{value}" for value in values)
        names.extend((f"{namespace}:__OOV__", f"{namespace}:__MISSING__"))
    names.extend((
        "candidate.year:scaled", "candidate.year:__MISSING__", "candidate.genre_count:scaled",
        "history.total_count:scaled", "history.supported_count:scaled", "history.unsupported_count:scaled",
        "history.positive_count:scaled", "history.negative_count:scaled",
        "history.rating_mean:scaled", "history.rating_mean:__MISSING__",
        "history.rating_std:scaled", "history.rating_std:__MISSING__",
        "history.year_mean:scaled", "history.year_mean:__MISSING__", "history.empty:indicator",
        "relation.positive_genre_overlap:value", "relation.negative_genre_overlap:value",
        "relation.year_distance:scaled", "relation.year_distance:__MISSING__",
    ))
    return names


def build_feature_map(row: dict, candidate: Movie, movies: dict[int, Movie], vocabulary: dict,
                      scaler: dict, positive_threshold: float) -> tuple[dict[str, float], dict]:
    output, observed = base.build_feature_map(row, candidate, movies, vocabulary, scaler, positive_threshold)
    counters = {name: Counter(values) for name, values in observed.items()}

    def activate(name: str, value: float) -> None:
        if not math.isfinite(value) or value < 0.0 or value > 1.0:
            raise RuntimeError(f"feature outside [0,1]: {name}={value}")
        if value:
            output[name] = min(1.0, output.get(name, 0.0) + value)
            counters["active"][name.split(":", 1)[0]] += 1

    supported = [event for event in row["history"] if int(event["movie_id"]) in movies]
    groups = {
        "history_positive.movie_id": [event for event in supported
                                      if float(event["rating"]) >= positive_threshold],
        "history_negative.movie_id": [event for event in supported
                                      if float(event["rating"]) < positive_threshold],
    }
    for namespace, events in groups.items():
        if not events:
            activate(f"{namespace}:__MISSING__", 1.0)
            counters["missing"][namespace] += 1
            continue
        denominator = len(events)
        vocab = vocabulary["namespaces"][namespace]
        for event in events:
            value = str(int(event["movie_id"]))
            if value in vocab:
                activate(f"{namespace}:{value}", 1.0 / denominator)
            else:
                activate(f"{namespace}:__OOV__", 1.0 / denominator)
                counters["oov"][namespace] += 1
    return output, {name: dict(values) for name, values in counters.items()}


def schema_document(vocabulary: dict, scaler: dict, *, schema_version: int = 5,
                    feature_profile_version: str = "fm-v5-v7-sparse-categorical-v1") -> dict:
    names = ordered_feature_names(vocabulary)
    all_indices = list(range(len(names)))

    def indices(prefixes: tuple[str, ...], *, exclude_missing: bool = True) -> list[int]:
        return [index for index, name in enumerate(names)
                if name.startswith(prefixes) and (not exclude_missing or not name.endswith(":__MISSING__"))]

    candidate_id = indices(("candidate.movie_id:",))
    candidate_content = indices(("candidate.genre:",))
    candidate_content += [index for index, name in enumerate(names)
                          if name in {"candidate.year:scaled", "candidate.genre_count:scaled"}]
    positive_id = indices(("history_positive.movie_id:",))
    negative_id = indices(("history_negative.movie_id:",))
    positive_content = indices(("history_positive.genre:",))
    negative_content = indices(("history_negative.genre:",))
    content_linear = [index for index, name in enumerate(names)
                      if name.startswith("candidate.genre:") or name.startswith("candidate.year:") or
                      name == "candidate.genre_count:scaled"]
    aggregate_linear = [index for index, name in enumerate(names)
                        if (name.startswith("candidate.year:") or
                            name == "candidate.genre_count:scaled" or
                            name.startswith("history.") or name.startswith("relation."))]
    legacy_candidate = [index for index, name in enumerate(names)
                        if name in {"candidate.year:scaled", "candidate.genre_count:scaled"}]
    # Only exposed-history numeric values participate. Full-history counters and
    # missing indicators are deliberately excluded so N=0 has zero interaction.
    legacy_history = [index for index, name in enumerate(names)
                      if name in {
                          "history.positive_count:scaled", "history.negative_count:scaled",
                          "history.rating_mean:scaled", "history.rating_std:scaled",
                          "history.year_mean:scaled", "relation.positive_genre_overlap:value",
                          "relation.negative_genre_overlap:value", "relation.year_distance:scaled",
                      }]
    profiles = {
        "content_linear_v5": {"linear_indices": sorted(set(content_linear)),
                              "candidate_factor_indices": [], "positive_factor_indices": [],
                              "negative_factor_indices": []},
        "sparse_linear_v5": {"linear_indices": all_indices, "candidate_factor_indices": [],
                             "positive_factor_indices": [], "negative_factor_indices": []},
        "aggregate_linear_v5": {"linear_indices": sorted(set(aggregate_linear)),
                                "candidate_factor_indices": [], "positive_factor_indices": [],
                                "negative_factor_indices": []},
        "legacy_aggregate_fm_v5": {
            "linear_indices": sorted(set(aggregate_linear)),
            "candidate_factor_indices": legacy_candidate,
            "positive_factor_indices": legacy_history,
            "negative_factor_indices": [],
        },
        "sparse_history_fm_v5": {
            "linear_indices": all_indices, "candidate_factor_indices": candidate_id,
            "positive_factor_indices": positive_id, "negative_factor_indices": negative_id,
        },
        "sparse_history_content_fm_v5": {
            "linear_indices": all_indices,
            "candidate_factor_indices": sorted(set(candidate_id + candidate_content)),
            "positive_factor_indices": sorted(set(positive_id + positive_content)),
            "negative_factor_indices": sorted(set(negative_id + negative_content)),
        },
    }
    namespaces: dict[str, list[int]] = defaultdict(list)
    for index, name in enumerate(names):
        namespaces[name.split(":", 1)[0]].append(index)
    payload = {
        "schema_version": schema_version,
        "model_type": "WEIGHTED_CROSS_FIELD_FACTORIZATION_MACHINE",
        "feature_profile_version": feature_profile_version,
        "dtype": "float64_sparse",
        "value_range": [0.0, 1.0],
        "ordered_names": names,
        "profiles": profiles,
        "namespaces": dict(sorted(namespaces.items())),
        "oov_indices": {name: index for index, name in enumerate(names) if name.endswith(":__OOV__")},
        "vocabulary": vocabulary,
        "scaler": scaler,
        "user_id_embedding": False,
        "interaction_contract": {
            "allowed": [
                "legacy candidate aggregates x exposed-history aggregates",
                "candidate.movie_id x positive/negative history.movie_id",
                "candidate.movie_id/genre/year x positive/negative history.movie_id/genre",
            ],
            "forbidden": ["candidate x candidate", "history x history", "user_id x any"],
            "n0": "history factor fields contain no non-missing values; factor contribution is zero",
        },
        "unavailable_namespaces": ["director", "actor", "keyword", "verified_tmdb", "verified_kobis"],
    }
    payload["ordered_names_sha256"] = hashlib.sha256("\n".join(names).encode()).hexdigest()
    payload["profile_digest"] = _sha(payload)
    return payload
