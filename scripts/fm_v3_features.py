"""Content-only sparse feature schema for the matched FM-v3 attribution test."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Iterable

from fm_v2_features import (
    Movie,
    build_feature_map as build_v2_feature_map,
    feature_namespace,
    fit_scaler as fit_v2_scaler,
    fit_vocabulary as fit_v2_vocabulary,
    ordered_feature_names as ordered_v2_feature_names,
    parse_movie,
    vectorize,
)


def _sha(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def fit_vocabulary(train_fit_rows: Iterable[dict], movies: dict[int, Movie], positive_threshold: float) -> dict:
    vocabulary = fit_v2_vocabulary(train_fit_rows, movies, positive_threshold)
    vocabulary["namespaces"].pop("candidate.movie_id", None)
    vocabulary["digest"] = _sha(vocabulary["namespaces"])
    vocabulary["fit_roles"] = ["TRAIN"]
    vocabulary["fit_internal_splits"] = ["FIT"]
    return vocabulary


def fit_scaler(train_fit_rows: Iterable[dict], movies: dict[int, Movie], positive_threshold: float) -> dict:
    scaler = fit_v2_scaler(train_fit_rows, movies, positive_threshold)
    scaler["fit_roles"] = ["TRAIN"]
    scaler["fit_internal_splits"] = ["FIT"]
    return scaler


def build_feature_map(row: dict, candidate: Movie, movies: dict[int, Movie], vocabulary: dict,
                      scaler: dict, positive_threshold: float) -> tuple[dict[str, float], dict]:
    working_vocabulary = {**vocabulary, "namespaces": {**vocabulary["namespaces"], "candidate.movie_id": {}}}
    features, diagnostics = build_v2_feature_map(
        row, candidate, movies, working_vocabulary, scaler, positive_threshold
    )
    # Candidate identity was poorly supported in FM-v2 and is intentionally absent from both arms.
    features = {name: value for name, value in features.items() if not name.startswith("candidate.movie_id:")}
    return features, diagnostics


def ordered_feature_names(vocabulary: dict) -> list[str]:
    working_vocabulary = {**vocabulary, "namespaces": {**vocabulary["namespaces"], "candidate.movie_id": {}}}
    return [name for name in ordered_v2_feature_names(working_vocabulary)
            if not name.startswith("candidate.movie_id:")]


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
    history_positive = [index for index, name in enumerate(names)
                        if name.startswith("history_positive.genre:") and not name.endswith(":__MISSING__")]
    history_negative = [index for index, name in enumerate(names)
                        if name.startswith("history_negative.genre:") and not name.endswith(":__MISSING__")]
    profiles = {
        "matched_additive_v3": {
            "linear_indices": all_indices,
            "candidate_factor_indices": [],
            "positive_factor_indices": [],
            "negative_factor_indices": [],
        },
        "content_cross_factor_v3": {
            "linear_indices": all_indices,
            "candidate_factor_indices": sorted(candidate_content),
            "positive_factor_indices": history_positive,
            "negative_factor_indices": history_negative,
        },
    }
    payload = {
        "schema_version": 3,
        "model_type": "MATCHED_ADDITIVE_PLUS_FROZEN_LINEAR_CROSS_FIELD_FM",
        "feature_profile_version": "fm-v3-content-only-internal-fit-v1",
        "dtype": "float64_sparse",
        "value_range": [0.0, 1.0],
        "ordered_names": names,
        "profiles": profiles,
        "namespaces": dict(sorted(namespaces.items())),
        "oov_indices": {name: index for index, name in enumerate(names) if name.endswith(":__OOV__")},
        "vocabulary": vocabulary,
        "scaler": scaler,
        "user_id_embedding": False,
        "candidate_movie_id_feature": False,
        "interaction_contract": {
            "allowed": ["candidate.content x history_positive.genre",
                        "candidate.content x history_negative.genre"],
            "forbidden": ["candidate identity", "candidate x candidate", "history x history",
                          "relation x any", "numeric_history x any"],
            "additive_parameters_during_factor_fit": "FROZEN",
            "n0": "history factor fields contain no active values, so factor contribution is exactly zero",
        },
    }
    payload["ordered_names_sha256"] = hashlib.sha256("\n".join(names).encode()).hexdigest()
    payload["profile_digest"] = _sha(payload)
    return payload


__all__ = [
    "Movie", "build_feature_map", "fit_scaler", "fit_vocabulary", "parse_movie",
    "schema_document", "vectorize",
]
