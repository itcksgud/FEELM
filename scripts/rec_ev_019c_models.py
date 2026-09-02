"""Model primitives used by the bounded REC-EV-019C runner."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable, Mapping, Sequence

import numpy as np
from scipy import sparse
from scipy.stats import rankdata

from rec_ev_019c_bounded_core import sample_observed_pairs


def bayesian_rating_scores(
    rating_counts: np.ndarray,
    rating_means: np.ndarray,
    *,
    global_mean: float,
    prior_strength: float,
) -> np.ndarray:
    counts = np.asarray(rating_counts, dtype=np.float64)
    means = np.asarray(rating_means, dtype=np.float64)
    if counts.shape != means.shape or bool((counts < 0).any()) or prior_strength <= 0:
        raise ValueError("invalid Bayesian rating inputs")
    sums = np.nan_to_num(means, nan=global_mean) * counts
    return (sums + float(prior_strength) * float(global_mean)) / (counts + float(prior_strength))


def percentile_scores(raw_scores: np.ndarray, available: np.ndarray) -> np.ndarray:
    raw = np.asarray(raw_scores, dtype=np.float64)
    mask = np.asarray(available, dtype=bool)
    if raw.shape != mask.shape:
        raise ValueError("score and availability shapes differ")
    output = np.full(raw.shape, np.nan, dtype=np.float64)
    finite = mask & np.isfinite(raw)
    if bool(finite.any()):
        output[finite] = (rankdata(raw[finite], method="average") - 0.5) / int(finite.sum())
    return output


def effective_with_b0_fallback(
    model_raw_scores: np.ndarray,
    model_available: np.ndarray,
    b0_percentiles: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    model_percentiles = percentile_scores(model_raw_scores, model_available)
    fallback = ~np.isfinite(model_percentiles)
    effective = model_percentiles.copy()
    effective[fallback] = np.asarray(b0_percentiles, dtype=np.float64)[fallback]
    return effective, fallback


def signed_sparse_profile_scores(
    item_features: sparse.csr_matrix,
    anchor_positions: Sequence[int],
    labels: Sequence[int],
) -> tuple[np.ndarray, np.ndarray, bool]:
    positions = np.asarray(anchor_positions, dtype=np.int64)
    signs = np.asarray(labels, dtype=np.int8)
    if len(positions) != len(signs) or not set(signs.tolist()) <= {-1, 1}:
        raise ValueError("invalid signed profile")
    likes = positions[signs == 1]
    dislikes = positions[signs == -1]
    available = np.asarray(item_features.getnnz(axis=1)).ravel() > 0
    likes = likes[available[likes]]
    dislikes = dislikes[available[dislikes]]
    if not len(likes) or not len(dislikes):
        return np.zeros(item_features.shape[0], dtype=np.float64), available, True
    profile = np.asarray(item_features[likes].mean(axis=0) - item_features[dislikes].mean(axis=0)).ravel()
    norm = float(np.linalg.norm(profile))
    if not np.isfinite(norm) or norm == 0:
        return np.zeros(item_features.shape[0], dtype=np.float64), available, True
    scores = np.asarray(item_features @ (profile / norm)).ravel().astype(np.float64, copy=False)
    return scores, available, False


def signed_dense_profile_scores(
    item_embeddings: np.ndarray,
    item_available: np.ndarray,
    anchor_positions: Sequence[int],
    labels: Sequence[int],
) -> tuple[np.ndarray, np.ndarray, bool]:
    matrix = np.asarray(item_embeddings, dtype=np.float32)
    available = np.asarray(item_available, dtype=bool)
    positions = np.asarray(anchor_positions, dtype=np.int64)
    signs = np.asarray(labels, dtype=np.int8)
    likes = positions[(signs == 1) & available[positions]]
    dislikes = positions[(signs == -1) & available[positions]]
    if not len(likes) or not len(dislikes):
        return np.zeros(matrix.shape[0], dtype=np.float64), available, True
    profile = matrix[likes].mean(axis=0, dtype=np.float64) - matrix[dislikes].mean(axis=0, dtype=np.float64)
    norm = float(np.linalg.norm(profile))
    if not np.isfinite(norm) or norm == 0:
        return np.zeros(matrix.shape[0], dtype=np.float64), available, True
    return (matrix @ (profile / norm)).astype(np.float64, copy=False), available, False


@dataclass
class NeighborColumn:
    candidate_positions: np.ndarray
    signed_cosine: np.ndarray
    cooccurrence: np.ndarray


def build_item_neighbor_columns(
    signed_user_item: sparse.csr_matrix,
    anchor_positions: Sequence[int],
    *,
    maximum_neighbors: int | None = None,
    shrink_values: Sequence[float] = (),
    anchor_batch_size: int = 128,
) -> dict[int, NeighborColumn]:
    """Compute sparse columns only for prefix items, optionally pruning exact trial top-N unions."""
    matrix = signed_user_item.astype(np.float32).tocsr()
    observed = matrix.copy()
    observed.data = np.ones_like(observed.data, dtype=np.float32)
    counts = np.asarray(observed.sum(axis=0)).ravel()
    result: dict[int, NeighborColumn] = {}
    anchors = np.asarray(
        [anchor for anchor in sorted(set(map(int, anchor_positions))) if 0 <= anchor < matrix.shape[1] and counts[anchor] > 0],
        dtype=np.int32,
    )
    for batch_start in range(0, len(anchors), anchor_batch_size):
        batch = anchors[batch_start : batch_start + anchor_batch_size]
        dot_batch = (matrix.T @ matrix[:, batch]).tocsc()
        cooc_batch = (observed.T @ observed[:, batch]).tocsc()
        for local, anchor in enumerate(batch.tolist()):
            dot_start, dot_stop = dot_batch.indptr[local : local + 2]
            candidate_positions = dot_batch.indices[dot_start:dot_stop].astype(np.int32, copy=True)
            dot_values = dot_batch.data[dot_start:dot_stop].astype(np.float32, copy=True)
            keep = (candidate_positions != anchor) & (dot_values > 0)
            candidate_positions = candidate_positions[keep]
            dot_values = dot_values[keep]
            if not len(candidate_positions):
                continue
            cooc_start, cooc_stop = cooc_batch.indptr[local : local + 2]
            cooc_lookup = {
                int(row): float(value)
                for row, value in zip(
                    cooc_batch.indices[cooc_start:cooc_stop],
                    cooc_batch.data[cooc_start:cooc_stop],
                    strict=True,
                )
            }
            cooccurrence = np.asarray([cooc_lookup[int(row)] for row in candidate_positions], dtype=np.float32)
            signed_cosine = dot_values / np.sqrt(counts[candidate_positions] * counts[anchor])
            if maximum_neighbors is not None and len(candidate_positions) > maximum_neighbors:
                chosen: set[int] = set()
                for shrink in shrink_values or (0.0,):
                    similarities = signed_cosine * (cooccurrence / (cooccurrence + float(shrink)))
                    keep_count = min(int(maximum_neighbors), len(similarities))
                    order = np.lexsort((candidate_positions, -similarities))[:keep_count]
                    chosen.update(map(int, order.tolist()))
                selected = np.asarray(sorted(chosen), dtype=np.int64)
                candidate_positions = candidate_positions[selected]
                signed_cosine = signed_cosine[selected]
                cooccurrence = cooccurrence[selected]
            order = np.argsort(candidate_positions, kind="stable")
            result[anchor] = NeighborColumn(
                candidate_positions[order], signed_cosine[order], cooccurrence[order]
            )
    return result


def item_knn_scores(
    neighbor_columns: Mapping[int, NeighborColumn],
    anchor_positions: Sequence[int],
    labels: Sequence[int],
    *,
    candidate_count: int,
    neighbors: int,
    shrink: float,
) -> tuple[np.ndarray, np.ndarray, bool]:
    numerator = np.zeros(candidate_count, dtype=np.float64)
    denominator = np.zeros(candidate_count, dtype=np.float64)
    used = 0
    for anchor, label in zip(anchor_positions, labels, strict=True):
        column = neighbor_columns.get(int(anchor))
        if column is None:
            continue
        similarities = column.signed_cosine * (column.cooccurrence / (column.cooccurrence + float(shrink)))
        positive = np.flatnonzero(similarities > 0)
        if not len(positive):
            continue
        keep = min(int(neighbors), len(positive))
        chosen = positive[
            np.lexsort((column.candidate_positions[positive], -similarities[positive]))[:keep]
        ]
        positions = column.candidate_positions[chosen]
        values = similarities[chosen].astype(np.float64)
        numerator[positions] += int(label) * values
        denominator[positions] += np.abs(values)
        used += 1
    available = denominator > 0
    scores = np.divide(numerator, denominator, out=np.zeros_like(numerator), where=available)
    return scores, available, used == 0


@dataclass
class BprFactors:
    user_factors: np.ndarray
    item_factors: np.ndarray


def epoch_pair_arrays(
    matrix: sparse.csr_matrix,
    user_keys: Sequence[str],
    *,
    seed: int,
    epoch: int,
    maximum_pairs: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    users: list[int] = []
    likes: list[int] = []
    dislikes: list[int] = []
    for user in range(matrix.shape[0]):
        start, stop = matrix.indptr[user : user + 2]
        positions = matrix.indices[start:stop]
        values = matrix.data[start:stop]
        pairs = sample_observed_pairs(
            positions[values == 1],
            positions[values == -1],
            user_key=str(user_keys[user]),
            model_seed=seed,
            epoch=epoch,
            maximum_pairs=maximum_pairs,
        )
        users.extend([user] * len(pairs))
        likes.extend(pair[0] for pair in pairs)
        dislikes.extend(pair[1] for pair in pairs)
    return (
        np.asarray(users, dtype=np.int32),
        np.asarray(likes, dtype=np.int32),
        np.asarray(dislikes, dtype=np.int32),
    )


def train_bpr_minibatch(
    matrix: sparse.csr_matrix,
    user_keys: Sequence[str],
    *,
    factors: int,
    regularization: float,
    epochs: int,
    learning_rate: float,
    seed: int,
    maximum_pairs_per_user_epoch: int,
    batch_size: int = 4096,
    pair_provider: Callable[[int], tuple[np.ndarray, np.ndarray, np.ndarray]] | None = None,
) -> BprFactors:
    """Deterministic averaged mini-batch BPR over observed LIKE>DISLIKE pairs."""
    rng = np.random.default_rng(seed)
    user_factors = rng.normal(0.0, 0.01, size=(matrix.shape[0], factors)).astype(np.float32)
    item_factors = rng.normal(0.0, 0.01, size=(matrix.shape[1], factors)).astype(np.float32)
    for epoch in range(int(epochs)):
        users, likes, dislikes = (
            pair_provider(epoch)
            if pair_provider is not None
            else epoch_pair_arrays(
                matrix,
                user_keys,
                seed=seed,
                epoch=epoch,
                maximum_pairs=maximum_pairs_per_user_epoch,
            )
        )
        for start in range(0, len(users), batch_size):
            stop = start + batch_size
            u_idx, i_idx, j_idx = users[start:stop], likes[start:stop], dislikes[start:stop]
            if not len(u_idx):
                continue
            u = user_factors[u_idx].astype(np.float64)
            i = item_factors[i_idx].astype(np.float64)
            j = item_factors[j_idx].astype(np.float64)
            margin = np.sum(u * (i - j), axis=1)
            coefficient = 1.0 / (1.0 + np.exp(np.clip(margin, -35.0, 35.0)))
            grad_u = coefficient[:, None] * (i - j) - regularization * u
            grad_i = coefficient[:, None] * u - regularization * i
            grad_j = -coefficient[:, None] * u - regularization * j

            for indices, gradients, target in (
                (u_idx, grad_u, user_factors),
                (i_idx, grad_i, item_factors),
                (j_idx, grad_j, item_factors),
            ):
                unique, inverse, counts = np.unique(indices, return_inverse=True, return_counts=True)
                accumulated = np.zeros((len(unique), factors), dtype=np.float64)
                np.add.at(accumulated, inverse, gradients)
                target[unique] += (learning_rate * accumulated / counts[:, None]).astype(np.float32)
    return BprFactors(user_factors=user_factors, item_factors=item_factors)


def fold_in_bpr_user(
    item_factors: np.ndarray,
    like_positions: Sequence[int],
    dislike_positions: Sequence[int],
    *,
    regularization: float,
    learning_rate: float,
    epochs: int = 50,
) -> tuple[np.ndarray, bool]:
    pairs = [(int(like), int(dislike)) for like in like_positions for dislike in dislike_positions]
    if not pairs:
        return np.zeros(item_factors.shape[1], dtype=np.float32), True
    user = np.zeros(item_factors.shape[1], dtype=np.float64)
    for _ in range(epochs):
        likes = item_factors[[pair[0] for pair in pairs]].astype(np.float64)
        dislikes = item_factors[[pair[1] for pair in pairs]].astype(np.float64)
        margin = (likes - dislikes) @ user
        coefficient = 1.0 / (1.0 + np.exp(np.clip(margin, -35.0, 35.0)))
        gradient = np.mean(coefficient[:, None] * (likes - dislikes), axis=0) - regularization * user
        user += learning_rate * gradient
    return user.astype(np.float32), False


def fold_in_logistic_user(
    item_biases: np.ndarray,
    item_factors: np.ndarray,
    observed_positions: Sequence[int],
    labels: Sequence[int],
    *,
    regularization: float,
    learning_rate: float,
    epochs: int = 80,
) -> tuple[float, np.ndarray, bool]:
    """Fit only a target user bias/vector against frozen signed-logistic items."""
    positions = np.asarray(observed_positions, dtype=np.int32)
    signs = np.asarray(labels, dtype=np.int8)
    if len(positions) != len(signs) or not len(positions) or not set(signs.tolist()) <= {-1, 1}:
        return 0.0, np.zeros(item_factors.shape[1], dtype=np.float32), True
    if not ({-1, 1} <= set(signs.tolist())):
        return 0.0, np.zeros(item_factors.shape[1], dtype=np.float32), True
    user_bias = 0.0
    user_vector = np.zeros(item_factors.shape[1], dtype=np.float64)
    frozen_biases = np.asarray(item_biases, dtype=np.float64)
    frozen_factors = np.asarray(item_factors, dtype=np.float64)
    for _ in range(int(epochs)):
        for position, label in zip(positions, signs, strict=True):
            item_vector = frozen_factors[int(position)]
            score = user_bias + frozen_biases[int(position)] + float(user_vector @ item_vector)
            signed_margin = float(label) * score
            if signed_margin >= 0:
                factor = -float(label) * math.exp(-signed_margin) / (1.0 + math.exp(-signed_margin))
            else:
                factor = -float(label) / (1.0 + math.exp(signed_margin))
            user_vector -= learning_rate * (factor * item_vector + regularization * user_vector)
            user_bias -= learning_rate * factor
    return user_bias, user_vector.astype(np.float32), False
