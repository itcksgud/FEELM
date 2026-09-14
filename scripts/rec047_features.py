"""Exact metadata identity overlap; no fitted vocabulary or target statistics."""

from __future__ import annotations
import numpy as np
from scipy import sparse
from rec046_common import movie_tokens, require, BLOCKS

GENRES = [
    28,
    12,
    16,
    35,
    80,
    99,
    18,
    10751,
    14,
    36,
    27,
    10402,
    9648,
    10749,
    878,
    53,
    10752,
    37,
]


class Relations:
    def __init__(self, frame):
        tokens = movie_tokens(frame)
        self.blocks = []
        self.counts = []
        self.info = {}
        for b, name in enumerate(BLOCKS):
            vocab = sorted({t for row in tokens for t in row[b]})
            lookup = {t: i for i, t in enumerate(vocab)}
            sizes = np.asarray([len(row[b]) for row in tokens])
            indices = [lookup[t] for row in tokens for t in sorted(row[b])]
            pointers = np.r_[0, np.cumsum(sizes)]
            data = np.repeat(1 / np.sqrt(np.maximum(sizes, 1)), sizes)
            x = sparse.csr_matrix(
                (data, indices, pointers),
                shape=(len(tokens), len(vocab)),
                dtype=np.float32,
            )
            self.blocks.append(x)
            self.counts.append(sizes)
            self.info[name] = {
                "identity_columns": len(vocab),
                "edges": len(indices),
                "missing_movies": int((sizes == 0).sum()),
            }
        self.genres = np.asarray(
            [[float(g in row.genre_ids) for g in GENRES] for row in frame.itertuples()],
            dtype=np.float32,
        )
        year = frame.release_year.fillna(0).to_numpy(float)
        runtime = frame.runtime_minutes.fillna(0).to_numpy(float)
        self.static = np.column_stack(
            [
                self.genres,
                np.clip((year - 1900) / 150, 0, 1),
                (year > 0),
                np.clip(runtime / 300, 0, 1),
                (runtime > 0),
            ]
        ).astype(np.float32)
        self.names = [f"candidate_genre_{g}" for g in GENRES] + [
            "year_scaled",
            "year_present",
            "runtime_scaled",
            "runtime_present",
        ]
        for name in BLOCKS:
            self.names += [
                f"{name}_{v}"
                for v in [
                    "candidate_present",
                    "candidate_size",
                    "input_usable_fraction",
                    "linked_fraction",
                    "similarity_mean",
                    "similarity_max",
                    "weighted_stars",
                    "weighted_relative_stars",
                    "linked_stars",
                    "no_link",
                ]
            ]
        self.names += [
            f"{block}_{g}"
            for block in [
                "genre_profile",
                "genre_rated_profile",
                "genre_shared_profile",
                "genre_shared_rated_profile",
            ]
            for g in GENRES
        ]
        self.names += ["input_mean", "input_std", "input_k", "has_input"]

    def transform(self, oi, stars, ei):
        oi, ei = np.asarray(oi, int), np.asarray(ei, int)
        stars = np.asarray(stars, float)
        require(len(oi) in [0, 1, 5, 10, 30], "allowed K")
        for axis in [oi, ei]:
            require(
                axis.ndim == 1 and len(np.unique(axis)) == len(axis),
                "unique movie axis",
            )
            require(
                ((axis >= 0) & (axis < len(self.static))).all(), "movie index range"
            )
        require(
            len(oi) == len(stars) and not np.intersect1d(oi, ei).size,
            "disjoint input/target",
        )
        require(np.isin(stars, np.arange(1, 11) / 2).all(), "half stars")
        n, k = len(ei), len(oi)
        mean = stars.mean() if k else 0.0
        values = [self.static[ei]]
        for x, sizes in zip(self.blocks, self.counts):
            sim = (x[ei] @ x[oi].T).toarray() if k else np.zeros((n, 0))
            links = sim > 0
            simsum = sim.sum(axis=1)
            linked = links.sum(axis=1)
            weighted = sim @ stars / np.maximum(simsum, 1e-12) if k else np.zeros(n)
            relative = (
                sim @ (stars - mean) / np.maximum(simsum, 1e-12) if k else np.zeros(n)
            )
            linked_mean = links @ stars / np.maximum(linked, 1) if k else np.zeros(n)
            values.append(
                np.column_stack(
                    [
                        sizes[ei] > 0,
                        np.minimum(np.log1p(sizes[ei]) / np.log(101), 1),
                        np.full(n, np.count_nonzero(sizes[oi]) / max(k, 1)),
                        linked / max(k, 1),
                        simsum / max(k, 1),
                        sim.max(axis=1) if k else np.zeros(n),
                        weighted / 5,
                        relative / 5,
                        linked_mean / 5,
                        linked == 0,
                    ]
                )
            )
        p = self.genres[oi].mean(axis=0) if k else np.zeros(len(GENRES))
        q = (
            (self.genres[oi] * (stars / 5)[:, None]).mean(axis=0)
            if k
            else np.zeros(len(GENRES))
        )
        values += [
            np.tile(p, (n, 1)),
            np.tile(q, (n, 1)),
            self.genres[ei] * p,
            self.genres[ei] * q,
        ]
        values.append(
            np.tile([mean / 5, stars.std() / 5 if k else 0, k / 30, int(k > 0)], (n, 1))
        )
        result = np.column_stack(values).astype(np.float32)
        require(
            result.shape == (n, len(self.names)) and np.isfinite(result).all(),
            "feature axis/finite",
        )
        return sparse.csr_matrix(result)
