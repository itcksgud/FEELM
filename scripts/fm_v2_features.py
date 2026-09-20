"""Leakage-safe sparse features for the corrective cross-field FM experiment."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable


COUNT_FEATURES = {
    "candidate.genre_count",
    "history.total_count",
    "history.supported_count",
    "history.unsupported_count",
    "history.positive_count",
    "history.negative_count",
}


@dataclass(frozen=True)
class Movie:
    movie_id: int
    year: int | None
    genres: tuple[str, ...]


def parse_movie(movie_id: int, title: str, genres: str) -> Movie:
    match = re.search(r"\((\d{4})\)\s*$", title)
    values = tuple(sorted(value for value in genres.split("|") if value and value != "(no genres listed)"))
    return Movie(movie_id, int(match.group(1)) if match else None, values)


def _sha(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def fit_vocabulary(train_rows: Iterable[dict], movies: dict[int, Movie], positive_threshold: float) -> dict:
    values: dict[str, set[str]] = defaultdict(set)
    rows = 0
    for row in train_rows:
        rows += 1
        candidate = movies[int(row["target_movie_id"])]
        values["candidate.movie_id"].add(str(candidate.movie_id))
        values["candidate.genre"].update(candidate.genres)
        for event in row["history"]:
            movie = movies.get(int(event["movie_id"]))
            if movie is None:
                continue
            namespace = "history_positive.genre" if float(event["rating"]) >= positive_threshold else "history_negative.genre"
            values[namespace].update(movie.genres)
    if rows == 0:
        raise RuntimeError("TRAIN vocabulary fit received no rows")
    namespaces = {}
    for namespace in ("candidate.movie_id", "candidate.genre", "history_positive.genre", "history_negative.genre"):
        namespaces[namespace] = {value: index for index, value in enumerate(sorted(values.get(namespace, set())))}
    return {"fit_roles": ["TRAIN"], "fit_rows": rows, "namespaces": namespaces, "digest": _sha(namespaces)}


def raw_numeric(row: dict, candidate: Movie, movies: dict[int, Movie], positive_threshold: float) -> dict[str, float | None]:
    supported = [event for event in row["history"] if int(event["movie_id"]) in movies]
    positive = [event for event in supported if float(event["rating"]) >= positive_threshold]
    negative = [event for event in supported if float(event["rating"]) < positive_threshold]
    ratings = [float(event["rating"]) for event in supported]
    years = [movies[int(event["movie_id"])].year for event in supported]
    known_years = [float(value) for value in years if value is not None]
    candidate_year = float(candidate.year) if candidate.year is not None else None
    rating_mean = sum(ratings) / len(ratings) if ratings else None
    year_mean = sum(known_years) / len(known_years) if known_years else None
    return {
        "candidate.year": candidate_year,
        "candidate.genre_count": float(len(candidate.genres)),
        "history.total_count": float(row["total_history_count"]),
        "history.supported_count": float(row["supported_history_count"]),
        "history.unsupported_count": float(max(0, int(row["total_history_count"]) - int(row["supported_history_count"]))),
        "history.positive_count": float(len(positive)),
        "history.negative_count": float(len(negative)),
        "history.rating_mean": rating_mean,
        "history.rating_std": (math.sqrt(sum((value - rating_mean) ** 2 for value in ratings) / len(ratings))
                               if ratings and rating_mean is not None else None),
        "history.year_mean": year_mean,
        "relation.year_distance": (abs(candidate_year - year_mean)
                                   if candidate_year is not None and year_mean is not None else None),
    }


def _transform(name: str, value: float) -> float:
    return math.log1p(value) if name in COUNT_FEATURES else value


def fit_scaler(train_rows: Iterable[dict], movies: dict[int, Movie], positive_threshold: float) -> dict:
    stats: dict[str, list[float]] = defaultdict(list)
    rows = 0
    for row in train_rows:
        rows += 1
        candidate = movies[int(row["target_movie_id"])]
        for name, value in raw_numeric(row, candidate, movies, positive_threshold).items():
            if value is not None:
                stats[name].append(_transform(name, value))
    if rows == 0:
        raise RuntimeError("TRAIN scaler fit received no rows")
    fitted = {}
    for name in sorted(stats):
        values = stats[name]
        lower, upper = min(values), max(values)
        fitted[name] = {
            "minimum": lower,
            "maximum": upper,
            "scale": upper - lower if upper > lower else 1.0,
            "transform": "log1p_then_minmax_clip" if name in COUNT_FEATURES else "minmax_clip",
        }
    return {"fit_roles": ["TRAIN"], "fit_rows": rows, "range": [0.0, 1.0], "values": fitted, "digest": _sha(fitted)}


def ordered_feature_names(vocabulary: dict) -> list[str]:
    names: list[str] = []
    for namespace in ("candidate.movie_id", "candidate.genre", "history_positive.genre", "history_negative.genre"):
        names.extend(f"{namespace}:{value}" for value in vocabulary["namespaces"][namespace])
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


def feature_namespace(name: str) -> str:
    return name.split(":", 1)[0]


def _scale(name: str, value: float, scaler: dict) -> float:
    spec = scaler["values"][name]
    transformed = _transform(name, value)
    clipped = min(float(spec["maximum"]), max(float(spec["minimum"]), transformed))
    return (clipped - float(spec["minimum"])) / float(spec["scale"])


def build_feature_map(row: dict, candidate: Movie, movies: dict[int, Movie], vocabulary: dict,
                      scaler: dict, positive_threshold: float) -> tuple[dict[str, float], dict]:
    output: dict[str, float] = {}
    diagnostics = {"oov": Counter(), "missing": Counter(), "active": Counter(), "clipped": Counter()}

    def activate(name: str, value: float = 1.0) -> None:
        if not math.isfinite(value) or value < 0.0 or value > 1.0:
            raise RuntimeError(f"feature outside [0,1]: {name}={value}")
        if value == 0.0:
            return
        combined = output.get(name, 0.0) + float(value)
        if combined > 1.0:
            diagnostics["clipped"][feature_namespace(name)] += 1
        output[name] = min(1.0, combined)
        diagnostics["active"][feature_namespace(name)] += 1

    def categorical(namespace: str, raw_values: Iterable[str], denominator: int = 1,
                    missing_indicator: bool = True) -> set[str]:
        values = tuple(raw_values)
        known: set[str] = set()
        vocab = vocabulary["namespaces"][namespace]
        if not values and missing_indicator:
            activate(f"{namespace}:__MISSING__")
            diagnostics["missing"][namespace] += 1
        for value in values:
            if value in vocab:
                activate(f"{namespace}:{value}", 1.0 / max(1, denominator))
                known.add(value)
            else:
                activate(f"{namespace}:__OOV__", 1.0 / max(1, denominator))
                diagnostics["oov"][namespace] += 1
        return known

    categorical("candidate.movie_id", (str(candidate.movie_id),), missing_indicator=False)
    candidate_genres = categorical("candidate.genre", candidate.genres)
    supported = [event for event in row["history"] if int(event["movie_id"]) in movies]
    positive_events = [event for event in supported if float(event["rating"]) >= positive_threshold]
    negative_events = [event for event in supported if float(event["rating"]) < positive_threshold]

    def history_genres(events: list[dict], namespace: str) -> set[str]:
        genres: list[str] = []
        for event in events:
            genres.extend(movies[int(event["movie_id"])].genres)
        return categorical(namespace, genres, len(events))

    positive_genres = history_genres(positive_events, "history_positive.genre")
    negative_genres = history_genres(negative_events, "history_negative.genre")
    raw = raw_numeric(row, candidate, movies, positive_threshold)
    for name, value in raw.items():
        if value is None:
            activate(f"{name}:__MISSING__")
            diagnostics["missing"][name] += 1
            continue
        spec = scaler["values"][name]
        transformed = _transform(name, value)
        if transformed < float(spec["minimum"]) or transformed > float(spec["maximum"]):
            diagnostics["clipped"][name] += 1
        activate(f"{name}:scaled", _scale(name, value, scaler))
    if not supported:
        activate("history.empty:indicator")
    activate("relation.positive_genre_overlap:value",
             len(candidate_genres & positive_genres) / max(1, len(candidate_genres)))
    activate("relation.negative_genre_overlap:value",
             len(candidate_genres & negative_genres) / max(1, len(candidate_genres)))
    return output, {key: dict(value) for key, value in diagnostics.items()}


def vectorize(features: dict[str, float], names: list[str]) -> tuple[list[int], list[float]]:
    index = {name: value for value, name in enumerate(names)}
    unknown = sorted(set(features) - set(index))
    if unknown:
        raise RuntimeError(f"feature names absent from schema: {unknown[:3]}")
    pairs = sorted((index[name], float(value)) for name, value in features.items() if value != 0.0)
    return [item[0] for item in pairs], [item[1] for item in pairs]


def schema_document(vocabulary: dict, scaler: dict) -> dict:
    names = ordered_feature_names(vocabulary)
    namespaces: dict[str, list[int]] = defaultdict(list)
    for index, name in enumerate(names):
        namespaces[feature_namespace(name)].append(index)
    all_indices = list(range(len(names)))
    candidate_content = [index for index, name in enumerate(names)
                         if name.startswith("candidate.genre:") and not name.endswith(":__MISSING__")]
    candidate_content.extend(index for index, name in enumerate(names)
                             if name in {"candidate.year:scaled", "candidate.genre_count:scaled"})
    candidate_identity = [index for index, name in enumerate(names)
                          if name.startswith("candidate.movie_id:") and not name.endswith(":__MISSING__")]
    history_positive = [index for index, name in enumerate(names)
                        if name.startswith("history_positive.genre:") and not name.endswith(":__MISSING__")]
    history_negative = [index for index, name in enumerate(names)
                        if name.startswith("history_negative.genre:") and not name.endswith(":__MISSING__")]
    profiles = {
        "sparse_linear_v2": {"linear_indices": all_indices, "candidate_factor_indices": [],
                             "positive_factor_indices": [], "negative_factor_indices": []},
        "cross_content_fm_v2": {"linear_indices": all_indices, "candidate_factor_indices": sorted(candidate_content),
                                "positive_factor_indices": history_positive, "negative_factor_indices": history_negative},
        "cross_hybrid_fm_v2": {"linear_indices": all_indices,
                               "candidate_factor_indices": sorted(candidate_content + candidate_identity),
                               "positive_factor_indices": history_positive, "negative_factor_indices": history_negative},
    }
    payload = {
        "schema_version": 2,
        "model_type": "WEIGHTED_CROSS_FIELD_FACTORIZATION_MACHINE",
        "feature_profile_version": "fm-v2-cross-field-minmax-v1",
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
            "allowed": ["candidate_factor x history_positive.genre", "candidate_factor x history_negative.genre"],
            "forbidden": ["candidate x candidate", "history x history", "relation x any", "numeric_history x any"],
            "n0": "history factor fields contain no active values, therefore factor contribution is exactly zero",
        },
    }
    payload["ordered_names_sha256"] = hashlib.sha256("\n".join(names).encode()).hexdigest()
    payload["profile_digest"] = _sha(payload)
    return payload
