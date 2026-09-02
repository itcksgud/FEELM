"""Leakage-safe real-input adapter for REC-EV-019C.

The public entry point is called only after the runner's authorization check.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from scipy import sparse

from recommendation_binary_onboarding_preflight import global_midrank_ecdf, sequential_binary_labels
from run_rec_ev_019c_validation import InputFirewall, sha256_file


IDENTITY_ALLOWLIST = {"ML_TMDB_VERIFIED", "RECOVERED_BY_IMDB"}


@dataclass
class PreparedInputs:
    candidate_core: pd.DataFrame
    candidate_ids: np.ndarray
    movie_position: dict[int, int]
    b0_rating_count: np.ndarray
    b0_rating_mean: np.ndarray
    base_user_keys: np.ndarray
    base_binary: sparse.csr_matrix
    validation_prefixes: pd.DataFrame
    validation_windows: pd.DataFrame
    structured_by_variant: dict[str, sparse.csr_matrix]
    structured_available: np.ndarray
    text_embeddings: np.ndarray
    text_available: np.ndarray
    input_checksums: dict[str, str]


def _manifest_artifacts(manifest_path: Path) -> dict[str, str]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {str(item["path"]): str(item["sha256"]) for item in manifest.get("artifacts", [])}


def verify_allowed_input_checksums(contract: Mapping[str, Any], *, root: Path) -> dict[str, str]:
    tracked: dict[str, str] = {}
    for key in ("cohort_manifest", "feature_manifest"):
        tracked.update(_manifest_artifacts(root / contract["source_contracts"][key]))
    firewall = InputFirewall.from_contract(contract, root=root)
    actual: dict[str, str] = {}
    for name, relative in contract["allowed_input_artifacts"].items():
        path = firewall.validate_path(relative)
        if relative not in tracked:
            raise RuntimeError(f"allowed input is not tracked by a source manifest: {name}")
        digest = sha256_file(path)
        if digest != tracked[relative]:
            raise RuntimeError(f"allowed input checksum drift: {name}")
        actual[name] = digest
    return actual


def build_final_candidate_core(provisional: pd.DataFrame, identity: pd.DataFrame) -> pd.DataFrame:
    verified = identity.loc[
        identity["identity_status"].isin(IDENTITY_ALLOWLIST),
        ["movie_id", "tmdb_id", "identity_status"],
    ].copy()
    if verified["movie_id"].duplicated().any():
        raise RuntimeError("identity allowlist has duplicate movie IDs")
    result = provisional.drop(columns=["tmdb_id", "identity_status"]).merge(
        verified,
        on="movie_id",
        how="inner",
        validate="one_to_one",
    )
    return result.sort_values("movie_id", kind="stable", ignore_index=True)


def build_base_binary(
    base: pd.DataFrame,
    candidate_ids: np.ndarray,
    *,
    shrinkage: float,
    like_min: float,
    dislike_max: float,
) -> tuple[np.ndarray, sparse.csr_matrix, np.ndarray, np.ndarray]:
    """Build signed labels from each user's chronological Base-only sequence."""
    required = ["user_key", "movie_id", "rating", "timestamp"]
    if list(base.columns[:4]) != required:
        base = base[required]
    base = base.sort_values(["user_key", "timestamp", "movie_id"], kind="stable", ignore_index=True)
    ratings = base["rating"].to_numpy(dtype=np.float64, copy=False)
    global_midrank = global_midrank_ecdf(ratings)
    candidate_ids = np.asarray(candidate_ids, dtype=np.int64)
    movie_positions = {int(movie_id): position for position, movie_id in enumerate(candidate_ids)}

    raw_keys = base["user_key"]
    keys = raw_keys.astype(str).to_numpy()
    boundaries = np.flatnonzero(np.r_[True, keys[1:] != keys[:-1], True])
    user_keys = keys[boundaries[:-1]]
    row_chunks: list[np.ndarray] = []
    col_chunks: list[np.ndarray] = []
    value_chunks: list[np.ndarray] = []
    movie_values = base["movie_id"].to_numpy(dtype=np.int64, copy=False)
    for user_position, (start, stop) in enumerate(zip(boundaries[:-1], boundaries[1:], strict=True)):
        labels = sequential_binary_labels(
            ratings[start:stop],
            global_midrank,
            shrinkage=shrinkage,
            like_min=like_min,
            dislike_max=dislike_max,
        )
        kept = [
            (movie_positions[int(movie_values[start + offset])], int(label))
            for offset, label, _ in labels
            if int(movie_values[start + offset]) in movie_positions
        ]
        if not kept:
            continue
        row_chunks.append(np.full(len(kept), user_position, dtype=np.int32))
        col_chunks.append(np.fromiter((item[0] for item in kept), dtype=np.int32, count=len(kept)))
        value_chunks.append(np.fromiter((item[1] for item in kept), dtype=np.int8, count=len(kept)))
    rows = np.concatenate(row_chunks) if row_chunks else np.empty(0, dtype=np.int32)
    columns = np.concatenate(col_chunks) if col_chunks else np.empty(0, dtype=np.int32)
    values = np.concatenate(value_chunks) if value_chunks else np.empty(0, dtype=np.int8)
    matrix = sparse.coo_matrix(
        (values, (rows, columns)),
        shape=(len(user_keys), len(candidate_ids)),
        dtype=np.int8,
    ).tocsr()
    matrix.sum_duplicates()
    if matrix.nnz and (matrix.data.min() < -1 or matrix.data.max() > 1):
        raise RuntimeError("duplicate base user/movie labels produced an invalid signed value")

    candidate_mask = base["movie_id"].isin(candidate_ids)
    grouped = base.loc[candidate_mask].groupby("movie_id", sort=True, observed=True)["rating"].agg(["size", "mean"])
    counts = grouped["size"].reindex(candidate_ids, fill_value=0).to_numpy(dtype=np.int64)
    means = grouped["mean"].reindex(candidate_ids).to_numpy(dtype=np.float64)
    if bool((counts <= 0).any()):
        raise RuntimeError("final candidate without a Base rating")
    return user_keys, matrix, counts, means


def _group_matrix(token_rows: list[list[str]]) -> sparse.csr_matrix:
    vocabulary = {token: index for index, token in enumerate(sorted({value for row in token_rows for value in row}))}
    rows: list[int] = []
    columns: list[int] = []
    for row_index, tokens in enumerate(token_rows):
        for token in sorted(set(tokens)):
            rows.append(row_index)
            columns.append(vocabulary[token])
    data = np.ones(len(rows), dtype=np.float32)
    matrix = sparse.coo_matrix((data, (rows, columns)), shape=(len(token_rows), len(vocabulary))).tocsr()
    norms = np.sqrt(np.asarray(matrix.multiply(matrix).sum(axis=1)).ravel())
    inverse = np.divide(1.0, norms, out=np.zeros_like(norms), where=norms > 0)
    return sparse.diags(inverse.astype(np.float32)) @ matrix


def _list_values(value: Any) -> list[int]:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return []
    return [int(item) for item in value]


def build_structured_variants(
    structured: pd.DataFrame,
    candidate_ids: np.ndarray,
) -> tuple[dict[str, sparse.csr_matrix], np.ndarray]:
    indexed = structured.set_index("movie_id", verify_integrity=True).reindex(candidate_ids)
    available = indexed["feature_eligible"].astype("boolean").fillna(False).to_numpy(dtype=bool)
    genre = _group_matrix(
        [[f"genre:{value}" for value in _list_values(row)] if ok else [] for row, ok in zip(indexed["genre_ids"], available, strict=True)]
    )
    context_rows: list[list[str]] = []
    people_rows: list[list[str]] = []
    keyword_rows: list[list[str]] = []
    for row, ok in zip(indexed.itertuples(index=False), available, strict=True):
        if not ok:
            context_rows.append([])
            people_rows.append([])
            keyword_rows.append([])
            continue
        decade = int(row.release_year) // 10 * 10 if pd.notna(row.release_year) else None
        runtime_bucket = int(row.runtime_minutes) // 30 if pd.notna(row.runtime_minutes) else None
        context_rows.append(
            ([f"language:{row.original_language}"] if pd.notna(row.original_language) else [])
            + ([f"decade:{decade}"] if decade is not None else [])
            + ([f"runtime30:{runtime_bucket}"] if runtime_bucket is not None else [])
        )
        people_rows.append(
            [f"director:{value}" for value in _list_values(row.director_ids)]
            + [f"cast:{value}" for value in _list_values(row.top5_cast_ids)]
        )
        keyword_rows.append([f"keyword:{value}" for value in _list_values(row.keyword_ids)])
    groups = {
        "GENRE": genre,
        "CONTEXT": _group_matrix(context_rows),
        "PEOPLE": _group_matrix(people_rows),
        "KEYWORDS": _group_matrix(keyword_rows),
    }
    definitions = {
        "FULL": ["GENRE", "CONTEXT", "PEOPLE", "KEYWORDS"],
        "DROP_KEYWORDS": ["GENRE", "CONTEXT", "PEOPLE"],
        "DROP_PEOPLE": ["GENRE", "CONTEXT", "KEYWORDS"],
        "CORE_ONLY_GENRE_LANGUAGE_DECADE_RUNTIME": ["GENRE", "CONTEXT"],
    }
    variants: dict[str, sparse.csr_matrix] = {}
    for name, group_names in definitions.items():
        weighted = [groups[group] * (1.0 / len(group_names)) for group in group_names]
        matrix = sparse.hstack(weighted, format="csr", dtype=np.float32)
        norms = np.sqrt(np.asarray(matrix.multiply(matrix).sum(axis=1)).ravel())
        inverse = np.divide(1.0, norms, out=np.zeros_like(norms), where=norms > 0)
        variants[name] = sparse.diags(inverse.astype(np.float32)) @ matrix
    return variants, available


def align_text_embeddings(
    text: pd.DataFrame,
    candidate_ids: np.ndarray,
    *,
    dimension: int,
) -> tuple[np.ndarray, np.ndarray]:
    indexed = text.set_index("movie_id", verify_integrity=True).reindex(candidate_ids)
    available = indexed["feature_eligible"].astype("boolean").fillna(False).to_numpy(dtype=bool)
    matrix = np.zeros((len(candidate_ids), dimension), dtype=np.float32)
    for position, (value, ok) in enumerate(zip(indexed["embedding"], available, strict=True)):
        if not ok:
            continue
        vector = np.asarray(value, dtype=np.float32)
        if vector.shape != (dimension,) or not np.isfinite(vector).all():
            raise RuntimeError("invalid text embedding")
        matrix[position] = vector
    return matrix, available


def load_prepared_inputs(contract: Mapping[str, Any], *, root: Path) -> PreparedInputs:
    checksums = verify_allowed_input_checksums(contract, root=root)
    firewall = InputFirewall.from_contract(contract, root=root)

    def frame(name: str) -> pd.DataFrame:
        path = firewall.validate_path(contract["allowed_input_artifacts"][name])
        return pq.read_table(path).to_pandas()

    provisional = frame("candidate_core_provisional")
    identity = frame("movie_identity")
    candidate_core = build_final_candidate_core(provisional, identity)
    if len(candidate_core) != int(contract["candidate_and_ranking"]["core_movie_count"]):
        raise RuntimeError("final candidate count drift")
    candidate_ids = candidate_core["movie_id"].to_numpy(dtype=np.int64)
    base = frame("base_train_ratings")
    user_keys, base_binary, counts, means = build_base_binary(
        base,
        candidate_ids,
        shrinkage=10.0,
        like_min=0.15,
        dislike_max=-0.15,
    )
    if len(user_keys) != int(contract["base_training_semantics"]["base_train_users"]):
        raise RuntimeError("Base Train user count drift")
    prefixes = frame("validation_prefixes")
    windows = frame("validation_windows")
    if set(prefixes["role"]) != {"VALIDATION"} or set(windows["role"]) != {"VALIDATION"}:
        raise RuntimeError("role-specific Validation artifact contains another role")
    structured_by_variant, structured_available = build_structured_variants(frame("structured_features"), candidate_ids)
    text_embeddings, text_available = align_text_embeddings(
        frame("text_embeddings"),
        candidate_ids,
        dimension=int(contract["models"]["B7_TMDB_TEXT_CONTENT"]["embedding_dimension"]),
    )
    return PreparedInputs(
        candidate_core=candidate_core,
        candidate_ids=candidate_ids,
        movie_position={int(movie_id): index for index, movie_id in enumerate(candidate_ids)},
        b0_rating_count=counts,
        b0_rating_mean=means,
        base_user_keys=user_keys,
        base_binary=base_binary,
        validation_prefixes=prefixes,
        validation_windows=windows,
        structured_by_variant=structured_by_variant,
        structured_available=structured_available,
        text_embeddings=text_embeddings,
        text_available=text_available,
        input_checksums=checksums,
    )
