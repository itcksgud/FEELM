"""TRAIN-fitted sparse namespaces for the S15P21E106-623 FM experiment."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable


UNAVAILABLE_BLOCKS = ("director", "actor", "keyword")
PROFILE_ORDER = (
    "movie_only_fm",
    "sparse_linear_only",
    "legacy_aggregate_fm",
    "sparse_pos_neg_history_fm",
    "sparse_history_content_fm",
    "unified_history_ablation_fm",
    "explicit_relation_block_removed_ablation_fm",
    "genre_content_block_removed_ablation_fm",
)


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
        values["candidate.genre"].update(candidate.genres)
        for event in row["history"]:
            movie = movies.get(int(event["movie_id"]))
            if movie is None:
                continue
            namespace = "history_positive.genre" if float(event["rating"]) >= positive_threshold else "history_negative.genre"
            values[namespace].update(movie.genres)
            values["history_all.genre"].update(movie.genres)
    if rows == 0:
        raise RuntimeError("TRAIN vocabulary fit received no rows")
    vocab = {
        namespace: {value: index for index, value in enumerate(sorted(values.get(namespace, set())))}
        for namespace in ("candidate.genre", "history_positive.genre", "history_negative.genre", "history_all.genre")
    }
    return {
        "fit_roles": ["TRAIN"],
        "fit_rows": rows,
        "namespaces": vocab,
        "digest": _sha(vocab),
    }


def raw_numeric(row: dict, candidate: Movie, movies: dict[int, Movie], positive_threshold: float) -> dict[str, float]:
    history = row["history"]
    supported = [event for event in history if int(event["movie_id"]) in movies]
    positive = [event for event in supported if float(event["rating"]) >= positive_threshold]
    negative = [event for event in supported if float(event["rating"]) < positive_threshold]
    ratings = [float(event["rating"]) for event in supported]
    candidate_year = float(candidate.year or 1900)
    history_years = [movies[int(event["movie_id"])].year for event in supported]
    known_years = [float(value) for value in history_years if value is not None]
    return {
        "candidate.year": candidate_year,
        "candidate.genre_count": float(len(candidate.genres)),
        "history.total_count": float(row["total_history_count"]),
        "history.supported_count": float(row["supported_history_count"]),
        "history.unsupported_count": float(max(0, int(row["total_history_count"]) - int(row["supported_history_count"]))),
        "history.positive_count": float(len(positive)),
        "history.negative_count": float(len(negative)),
        "history.rating_mean": sum(ratings) / len(ratings) if ratings else 0.0,
        "history.rating_std": math.sqrt(sum((value - sum(ratings) / len(ratings)) ** 2 for value in ratings) / len(ratings)) if ratings else 0.0,
        "history.year_mean": sum(known_years) / len(known_years) if known_years else candidate_year,
        "relation.year_distance": abs(candidate_year - (sum(known_years) / len(known_years))) if known_years else 0.0,
    }


def fit_normalizer(train_rows: Iterable[dict], movies: dict[int, Movie], positive_threshold: float) -> dict:
    stats: dict[str, list[float]] = defaultdict(list)
    rows = 0
    for row in train_rows:
        rows += 1
        candidate = movies[int(row["target_movie_id"])]
        for name, value in raw_numeric(row, candidate, movies, positive_threshold).items():
            stats[name].append(math.log1p(value) if name.endswith("_count") else value)
    if rows == 0:
        raise RuntimeError("TRAIN normalizer fit received no rows")
    fitted = {}
    for name in sorted(stats):
        values = stats[name]
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        fitted[name] = {"mean": mean, "scale": math.sqrt(variance) or 1.0, "transform": "log1p_then_zscore" if name.endswith("_count") else "zscore"}
    return {"fit_roles": ["TRAIN"], "fit_rows": rows, "values": fitted, "digest": _sha(fitted)}


def ordered_feature_names(vocabulary: dict) -> list[str]:
    names: list[str] = []
    for namespace in ("candidate.genre", "history_positive.genre", "history_negative.genre", "history_all.genre"):
        for value in vocabulary["namespaces"][namespace]:
            names.append(f"{namespace}:{value}")
        names.extend((f"{namespace}:__OOV__", f"{namespace}:__MISSING__"))
    names.extend((
        "candidate.year:z", "candidate.year:__MISSING__", "candidate.genre_count:z",
        "history.total_count:z", "history.supported_count:z", "history.positive_count:z",
        "history.negative_count:z", "history.rating_mean:z", "history.rating_std:z",
        "history.year_mean:z", "history.empty:indicator", "history.unsupported_count:z",
        "relation.positive_genre_overlap:value", "relation.negative_genre_overlap:value",
        "relation.unified_genre_overlap:value", "relation.year_distance:z",
    ))
    for scope in ("candidate", "history_positive", "history_negative", "history_all"):
        for block in UNAVAILABLE_BLOCKS:
            names.extend((f"{scope}.{block}:__OOV__", f"{scope}.{block}:__MISSING__"))
    return names


def feature_namespace(name: str) -> str:
    return name.split(":", 1)[0]


def _profile_predicates() -> dict[str, callable]:
    candidate_genre = lambda n: n.startswith("candidate.genre:")
    candidate_year = lambda n: n.startswith("candidate.year:")
    candidate_struct = lambda n: candidate_year(n) or n.startswith("candidate.genre_count:")
    posneg = lambda n: n.startswith("history_positive.genre:") or n.startswith("history_negative.genre:")
    unified = lambda n: n.startswith("history_all.genre:")
    history_numeric = lambda n: n.startswith("history.")
    relation = lambda n: n.startswith("relation.")
    full = lambda n: candidate_genre(n) or candidate_struct(n) or posneg(n) or history_numeric(n) or relation(n)
    return {
        "movie_only_fm": lambda n: candidate_genre(n) or candidate_struct(n),
        "sparse_linear_only": full,
        "legacy_aggregate_fm": lambda n: candidate_struct(n) or history_numeric(n) or n in {"relation.year_distance:z", "relation.positive_genre_overlap:value", "relation.negative_genre_overlap:value"},
        "sparse_pos_neg_history_fm": lambda n: candidate_genre(n) or candidate_struct(n) or posneg(n) or history_numeric(n),
        "sparse_history_content_fm": full,
        "unified_history_ablation_fm": lambda n: candidate_genre(n) or candidate_struct(n) or unified(n) or history_numeric(n) or n in {"relation.unified_genre_overlap:value", "relation.year_distance:z"},
        "explicit_relation_block_removed_ablation_fm": lambda n: full(n) and not relation(n),
        "genre_content_block_removed_ablation_fm": lambda n: candidate_year(n) or history_numeric(n) or n == "relation.year_distance:z",
    }


def profile_indices(names: list[str]) -> dict[str, list[int]]:
    predicates = _profile_predicates()
    result = {profile: [index for index, name in enumerate(names) if predicates[profile](name)] for profile in PROFILE_ORDER}
    if any(not indices for indices in result.values()):
        raise RuntimeError("an FM profile has no features")
    return result


def _z(name: str, value: float, normalizer: dict) -> float:
    spec = normalizer["values"][name]
    transformed = math.log1p(value) if spec["transform"] == "log1p_then_zscore" else value
    return (transformed - float(spec["mean"])) / float(spec["scale"])


def build_feature_map(row: dict, candidate: Movie, movies: dict[int, Movie], vocabulary: dict,
                      normalizer: dict, positive_threshold: float) -> tuple[dict[str, float], dict]:
    output: dict[str, float] = {}
    diagnostics = {"oov": Counter(), "missing": Counter(), "active": Counter()}

    def activate(name: str, value: float = 1.0) -> None:
        if value == 0.0:
            return
        output[name] = output.get(name, 0.0) + float(value)
        diagnostics["active"][feature_namespace(name)] += 1

    def categorical(namespace: str, raw_values: Iterable[str], denominator: int = 1) -> set[str]:
        values = tuple(raw_values)
        known: set[str] = set()
        vocab = vocabulary["namespaces"][namespace]
        if not values:
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

    candidate_genres = categorical("candidate.genre", candidate.genres)
    history = row["history"]
    positive_events = [event for event in history if float(event["rating"]) >= positive_threshold]
    negative_events = [event for event in history if float(event["rating"]) < positive_threshold]

    def history_genres(events: list[dict], namespace: str) -> set[str]:
        genres: list[str] = []
        for event in events:
            movie = movies.get(int(event["movie_id"]))
            if movie:
                genres.extend(movie.genres)
        return categorical(namespace, genres, len(events))

    positive_genres = history_genres(positive_events, "history_positive.genre")
    negative_genres = history_genres(negative_events, "history_negative.genre")
    all_genres = history_genres(history, "history_all.genre")
    raw = raw_numeric(row, candidate, movies, positive_threshold)
    for name in raw:
        activate(f"{name}:z", _z(name, raw[name], normalizer))
    if candidate.year is None:
        activate("candidate.year:__MISSING__")
        diagnostics["missing"]["candidate.year"] += 1
    if not history:
        activate("history.empty:indicator")
    activate("relation.positive_genre_overlap:value", len(candidate_genres & positive_genres) / max(1, len(candidate_genres)))
    activate("relation.negative_genre_overlap:value", len(candidate_genres & negative_genres) / max(1, len(candidate_genres)))
    activate("relation.unified_genre_overlap:value", len(candidate_genres & all_genres) / max(1, len(candidate_genres)))
    # Unavailable metadata namespaces stay in the schema with dedicated OOV/missing indexes,
    # but are intentionally inactive so constant missing features cannot create spurious FM interactions.
    return output, {key: dict(value) for key, value in diagnostics.items()}


def vectorize(features: dict[str, float], names: list[str]) -> tuple[list[int], list[float]]:
    index = {name: value for value, name in enumerate(names)}
    unknown = sorted(set(features) - set(index))
    if unknown:
        raise RuntimeError(f"feature names absent from schema: {unknown[:3]}")
    pairs = sorted((index[name], float(value)) for name, value in features.items() if value != 0.0)
    return [item[0] for item in pairs], [item[1] for item in pairs]


def schema_document(vocabulary: dict, normalizer: dict) -> dict:
    names = ordered_feature_names(vocabulary)
    profiles = profile_indices(names)
    namespaces: dict[str, list[int]] = defaultdict(list)
    for index, name in enumerate(names):
        namespaces[feature_namespace(name)].append(index)
    oov_indices = {name: index for index, name in enumerate(names) if name.endswith(":__OOV__")}
    if len(set(oov_indices.values())) != len(oov_indices):
        raise RuntimeError("namespace OOV index collision")
    payload = {
        "schema_version": 1,
        "model_type": "SPARK_FM_REGRESSOR_AND_LINEAR_BASELINE",
        "feature_profile_version": "fm-zero-n-sparse-v1",
        "dtype": "float64_sparse",
        "ordered_names": names,
        "profiles": {profile: {"indices": indices, "ordered_names": [names[index] for index in indices]} for profile, indices in profiles.items()},
        "namespaces": dict(sorted(namespaces.items())),
        "oov_indices": oov_indices,
        "vocabulary": vocabulary,
        "normalizer": normalizer,
        "unavailable_blocks": {
            block: {"status": "UNAVAILABLE", "reason": "MovieLens 32M movies.csv does not contain a verified director/actor/keyword snapshot"}
            for block in UNAVAILABLE_BLOCKS
        },
        "user_id_embedding": False,
        "interaction_contract": {
            "factor_profiles": "Spark FM pairwise interactions enabled",
            "sparse_linear_only": "same sparse vector, no factor interactions",
            "explicit_relation_block_removed_ablation_fm": "explicit relation features removed; latent candidate-history FM interactions remain and are not claimed removed",
            "genre_content_block_removed_ablation_fm": "all available genre features and genre count removed; year and response structure retained",
        },
    }
    payload["ordered_names_sha256"] = hashlib.sha256("\n".join(names).encode()).hexdigest()
    payload["profile_digest"] = _sha({key: value for key, value in payload.items() if key not in {"profile_digest"}})
    return payload
