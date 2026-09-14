"""REC047 exact relation formulas, with capped actual history lengths 0..30.

The original sealed module is unchanged. Only its exact-K guard is broadened.
"""
import numpy as np
from scipy import sparse
from rec047_features import Relations, GENRES
from rec046_common import require

class CappedRelations(Relations):
    def transform(self, oi, stars, ei):
        oi, ei = np.asarray(oi, int), np.asarray(ei, int)
        stars = np.asarray(stars, float)
        require(0 <= len(oi) <= 30, "capped actual history length")
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
