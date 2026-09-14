"""Target-time training histories and the unchanged 230 structured feature formulas.

No model fitting or future label access. The sealed text339 implementation is the
reference; batching replaces repeated calls, not the definition of the features.
"""
from __future__ import annotations
import numpy as np
from cold_item_features import Features as Original
from text339_relations import CappedRelations
from rec046_common import require

RATING_COLUMNS = [200, 207, 208, 210, 211, 212]


class Features:
    def __init__(self, metadata, catalog):
        self.original = Original(metadata)
        self.original.base = CappedRelations(metadata)
        self.base = self.original.base
        self.names = self.original.names[168:398]
        fit = (catalog.train_count.to_numpy() > 0) & ~catalog.blocked.to_numpy() & self.original.valid[:, 0]
        self.prior_mean = float(metadata.tmdb_vote_average.to_numpy(float)[fit].mean())
        self.prior_mass = float(np.median(metadata.tmdb_vote_count.to_numpy(float)[fit]))
        self.prior_movies = int(fit.sum())
        require(self.prior_movies == 44920 and self.prior_mass == 48, 'fixed training metadata prior')
        self.shrunk = self.original.crowd.copy()
        votes = metadata.tmdb_vote_count.to_numpy(float)
        valid = self.original.valid[:, 0]
        self.shrunk[valid, 0] = (votes[valid] * self.original.crowd[valid, 0] + self.prior_mass * self.prior_mean / 10) / (votes[valid] + self.prior_mass)

    def reference(self, oi, stars, ei, shrink=False):
        previous = self.original.crowd
        if shrink:
            self.original.crowd = self.shrunk
        try:
            return self.original.transform(oi, stars, ei)[:, 168:398]
        finally:
            self.original.crowd = previous

    def crowd(self, oi, stars, mask, ei, shrink=False):
        """Return 30 crowd columns for one history per candidate row."""
        values = self.shrunk if shrink else self.original.crowd
        k = mask.sum(1)
        item, response = [], []
        for b in range(3):
            cv, cp = values[ei, b], self.original.valid[ei, b]
            item.extend([cv, cp])
            usable = self.original.valid[oi, b] & mask
            m = usable.sum(1)
            z = values[oi, b]
            center = (z * usable).sum(1) / np.maximum(m, 1)
            rmean = (stars * usable).sum(1) / np.maximum(m, 1) / 5
            variance = (((z - center[:, None]) ** 2) * usable).sum(1) / np.maximum(m, 1)
            cov = ((z - center[:, None]) * (stars / 5 - rmean[:, None]) * usable).sum(1) / (m + 5)
            delta = np.where(cp & (m > 0), cv - center, 0)
            response.extend([m / np.maximum(k, 1), center, variance, m / (m + 5), cov, delta, delta * cov, m > 0])
        return np.column_stack([*item, *response]).astype(np.float32)

    def batch(self, oi, stars, mask, ei):
        oi, ei = np.asarray(oi, int), np.asarray(ei, int)
        stars, mask = np.asarray(stars, float), np.asarray(mask, bool)
        n, width = oi.shape
        require(width == 30 and stars.shape == mask.shape == oi.shape and len(ei) == n, 'batch dimensions')
        k = mask.sum(1)
        require(not ((oi == ei[:, None]) & mask).any(), 'target excluded from own history')
        mean = (stars * mask).sum(1) / np.maximum(k, 1)
        std = np.sqrt((((stars - mean[:, None]) ** 2) * mask).sum(1) / np.maximum(k, 1))
        values, extra = [self.base.static[ei]], []
        rows, cols = np.nonzero(mask)
        for x, sizes in zip(self.base.blocks, self.base.counts):
            sim = np.zeros((n, width), np.float32)
            if len(rows):
                sim[rows, cols] = np.asarray(x[ei[rows]].multiply(x[oi[rows, cols]]).sum(1)).ravel()
            links = sim > 0
            mass, count = sim.sum(1), links.sum(1)
            weighted = (sim * stars).sum(1)
            relative = (sim * (stars - mean[:, None])).sum(1)
            linked = (links * stars).sum(1)
            values.append(np.column_stack([
                sizes[ei] > 0, np.minimum(np.log1p(sizes[ei]) / np.log(101), 1),
                ((sizes[oi] > 0) & mask).sum(1) / np.maximum(k, 1),
                count / np.maximum(k, 1), mass / np.maximum(k, 1), sim.max(1),
                np.where(mass > 0, (weighted + 5 * mean) / (mass + 5) / 5, 0),
                relative / (mass + 5) / 5,
                np.where(count > 0, (linked + 5 * mean) / (count + 5) / 5, 0), count == 0]))
            extra.extend([mass * mass / np.maximum((sim * sim).sum(1), 1e-12) / 30, mass / (mass + 5)])
        genres = self.base.genres[oi] * mask[:, :, None]
        counts = genres.sum(1)
        confidence = counts / (counts + 5)
        profile = counts / np.maximum(k, 1)[:, None]
        conditional = (genres * stars[:, :, None]).sum(1) / np.maximum(counts, 1)
        rated = profile * (mean[:, None] + confidence * (conditional - mean[:, None])) / 5
        values.extend([profile, rated, self.base.genres[ei] * profile, self.base.genres[ei] * rated,
                       np.column_stack([mean / 5, std / 5, k / 30, k > 0]),
                       np.column_stack(extra), confidence, self.crowd(oi, stars, mask, ei)])
        result = np.column_stack(values).astype(np.float32)
        require(result.shape == (n, 230) and np.isfinite(result).all(), 'finite structured features')
        return result


class Histories:
    def __init__(self, ratings, episodes, ids):
        self.uid = ratings.uid.to_numpy()
        self.ts = ratings.timestamp.to_numpy()
        self.movie = ratings.movie_id.to_numpy()
        self.stars = ratings.rating.to_numpy(float)
        self.ix = np.searchsorted(ids, self.movie)
        n = len(ratings)
        require(np.array_equal(ids[self.ix], self.movie), 'canonical movie axis')
        require(np.array_equal(np.lexsort((self.movie, self.ts, self.uid)), np.arange(n)), 'original target order')
        starts = np.r_[True, self.uid[1:] != self.uid[:-1]]
        self.user_start = np.maximum.accumulate(np.where(starts, np.arange(n), 0))
        tied = starts | np.r_[True, self.ts[1:] != self.ts[:-1]]
        self.strict_boundary = np.maximum.accumulate(np.where(tied, np.arange(n), 0))
        self.past_order = np.lexsort((-self.movie, self.ts, self.uid))
        self.cap = np.repeat(episodes.cap.to_numpy(), episodes.targets.to_numpy())
        self.old_boundary = self.user_start + np.repeat(episodes.pre_count.to_numpy(), episodes.targets.to_numpy())
        require(len(self.cap) == n and np.array_equal(np.repeat(episodes.uid.to_numpy(), episodes.targets.to_numpy()), self.uid), 'same episode targets')

    def take(self, rows, strict=True):
        rows = np.asarray(rows, int)
        boundary = (self.strict_boundary if strict else self.old_boundary)[rows]
        pos = boundary[:, None] - 1 - np.arange(30)
        mask = (pos >= self.user_start[rows, None]) & (np.arange(30) < self.cap[rows, None])
        source = self.past_order[np.maximum(pos, 0)]
        require((self.ts[source][mask] < self.ts[rows, None].repeat(30, axis=1)[mask]).all(), 'strict past inputs')
        require((self.uid[source][mask] == self.uid[rows, None].repeat(30, axis=1)[mask]).all(), 'same user inputs')
        return self.ix[source], self.stars[source], mask, self.ix[rows]
