"""Fixed metadata relationships, evidence shrinkage, and snapshot crowd reactions."""
from __future__ import annotations
import numpy as np
from rec047_features import Relations, GENRES
from rec046_common import require, BLOCKS

class Features:
    def __init__(self, metadata, strength=5.0):
        self.base = Relations(metadata)
        self.strength = strength
        self.base_names = list(self.base.names)
        extra = [f'{b}_{s}' for b in BLOCKS for s in ['effective_n', 'mass_reliability']]
        extra += [f'genre_reliability_{g}' for g in GENRES]
        self.support_names = ['support_' + n for n in self.base_names] + extra
        self.crowd_names = ['rating', 'votes', 'popularity']
        raw = metadata[['tmdb_vote_average', 'tmdb_vote_count', 'tmdb_popularity']].to_numpy(float)
        valid = np.isfinite(raw) & (raw >= 0)
        valid[:, 0] &= (raw[:, 0] > 0) & (raw[:, 0] <= 10) & (raw[:, 1] > 0)
        values = np.where(valid, raw, 0)
        values[:, 0] /= 10
        values[:, 1] = np.minimum(np.log1p(values[:, 1]) / np.log1p(100000), 1)
        values[:, 2] = np.minimum(np.log1p(values[:, 2]) / np.log1p(1000), 1)
        self.crowd = values
        self.valid = valid
        self.item_names = [f'crowd_{n}_{s}' for n in self.crowd_names for s in ['value', 'present']]
        stats = ['input_fraction', 'input_mean', 'input_variance', 'input_reliability',
                 'rating_covariance', 'candidate_delta', 'response_cross', 'input_present']
        self.response_names = [f'crowd_{n}_{s}' for n in self.crowd_names for s in stats]
        self.names = self.base_names + self.support_names + self.item_names + self.response_names
        self.indices = {
            'BASE': list(range(168)), 'SUPPORT': list(range(168, 368)),
            'CROWD_ITEM': list(range(168, 374)), 'CROWD_RESPONSE': list(range(168, 398)),
        }
        require(len(self.base_names) == 168 and len(self.support_names) == 200 and len(self.names) == 398, 'feature dimensions')

    def transform(self, oi, stars, ei):
        oi, ei, stars = np.asarray(oi, int), np.asarray(ei, int), np.asarray(stars, float)
        base = self.base.transform(oi, stars, ei).toarray()
        support = base.copy()
        n, k = len(ei), len(oi)
        mean = stars.mean() if k else 0.0
        extra = []
        for b, x in enumerate(self.base.blocks):
            sim = (x[ei] @ x[oi].T).toarray() if k else np.zeros((n, 0))
            links = sim > 0
            mass = sim.sum(axis=1)
            count = links.sum(axis=1)
            square_mass = (sim * sim).sum(axis=1)
            effective = mass * mass / np.maximum(square_mass, 1e-12)
            reliability = mass / (mass + self.strength)
            offset = 22 + b * 10
            if k:
                support[:, offset + 6] = np.where(mass > 0, (sim @ stars + self.strength * mean) / (mass + self.strength) / 5, 0)
                support[:, offset + 7] = (sim @ (stars - mean)) / (mass + self.strength) / 5
                support[:, offset + 8] = np.where(count > 0, (links @ stars + self.strength * mean) / (count + self.strength) / 5, 0)
            extra.extend([effective / 30, reliability])
        genres = self.base.genres
        counts = genres[oi].sum(axis=0) if k else np.zeros(len(GENRES))
        confidence = counts / (counts + self.strength)
        if k:
            conditional = (genres[oi] * stars[:, None]).sum(axis=0) / np.maximum(counts, 1)
            shrunk = (counts / k) * (mean + confidence * (conditional - mean)) / 5
            support[:, 110:128] = shrunk
            support[:, 146:164] = genres[ei] * shrunk
        extra.extend([np.full(n, v) for v in confidence])
        support = np.column_stack([support, *extra])
        item, response = [], []
        for b in range(3):
            cv, cp = self.crowd[ei, b], self.valid[ei, b]
            item.extend([cv, cp])
            usable = self.valid[oi, b]
            m = int(usable.sum())
            if m:
                z, r = self.crowd[oi[usable], b], stars[usable] / 5
                center = float(z.mean())
                variance = float(np.mean((z - center)**2))
                cov = float(np.sum((z - center) * (r - r.mean())) / (m + self.strength))
                delta = np.where(cp, cv - center, 0)
            else:
                center = variance = cov = 0.0
                delta = np.zeros(n)
            response.extend([np.full(n, m / max(k, 1)), np.full(n, center),
                             np.full(n, variance), np.full(n, m / (m + self.strength)),
                             np.full(n, cov), delta, delta * cov, np.full(n, m > 0)])
        result = np.column_stack([base, support, *item, *response]).astype(np.float32)
        require(result.shape == (n, 398) and np.isfinite(result).all(), 'finite features')
        return result
