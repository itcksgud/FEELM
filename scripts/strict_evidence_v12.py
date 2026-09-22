"""Shared training/serving evidence for observed ratings and the r3e policy."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import math

import numpy as np
import pandas as pd

from audit_log_direct_user8_top100 import ids
from audit_strict_multiple_links_policy import required_links


SIDE_NAMES = ("series", "director", "cast_sources", "cast_people", "cast_units", "units", "anchor")
DIRECT_NAMES = [f"strict_{sign}_{name}" for sign in ("positive", "negative") for name in SIDE_NAMES]
EXTRA_NAMES = DIRECT_NAMES + ["strict_tmdb_gap", "strict_kobis_gap", "strict_kobis_reference_known",
    "strict_public_gap", "strict_required_log1p", "strict_positive_x_gap", "strict_negative_x_gap",
    "strict_evidence_margin", "strict_public_qualification"]


@dataclass(frozen=True)
class Movie:
    movie_id: int
    collection: frozenset
    director: frozenset
    cast: frozenset
    countries: frozenset
    genres: frozenset
    source: tuple


def metadata(frame: pd.DataFrame) -> dict[int, Movie]:
    if frame.movie_id.duplicated().any() or frame.tmdb_id.duplicated().any():
        raise ValueError("movie/TMDB identities must be unique within their namespace")
    out = {}
    for row in frame.itertuples(index=False):
        collection = ids(row.collection_ids)
        source = ("collection", next(iter(collection))) if len(collection) == 1 else ("movie", int(row.movie_id))
        out[int(row.movie_id)] = Movie(int(row.movie_id), collection, ids(row.director_ids),
            ids(row.top5_cast_ids), ids(row.origin_country_codes), ids(row.genre_ids), source)
    return out


class DirectIndex:
    def __init__(self, movies: dict[int, Movie]):
        self.movies = movies
        self.scores = {}
        self.lookup = {sign: {axis: defaultdict(set) for axis in ("collection", "director", "cast")}
                       for sign in ("positive", "negative")}

    def add(self, movie_id: int, score: float) -> None:
        if movie_id in self.scores:
            raise ValueError("duplicate history movie; resolve actual rating precedence first")
        movie = self.movies[movie_id]
        self.scores[movie_id] = float(score)
        sign = "positive" if score >= 4 else "negative" if score <= 2.5 else None
        if sign is not None:
            for axis in ("collection", "director", "cast"):
                for token in getattr(movie, axis):
                    self.lookup[sign][axis][token].add(movie_id)

    def query(self, candidate: Movie, *, explain: bool = False) -> tuple[np.ndarray, dict]:
        values, details = [], {}
        for sign in ("positive", "negative"):
            linked = self.lookup[sign]
            matches = {axis: set().union(*(linked[axis].get(token, set()) for token in getattr(candidate, axis)))
                       for axis in ("collection", "director", "cast")}
            series = matches["collection"]
            series_sources = {self.movies[mid].source for mid in series}
            director_sources = defaultdict(list)
            cast_sources = defaultdict(list)
            cast_people = defaultdict(set)
            for mid in matches["director"] - series:
                movie = self.movies[mid]
                if movie.source not in series_sources:
                    director_sources[movie.source].append(mid)
            for mid in matches["cast"] - series:
                movie = self.movies[mid]
                if movie.source in series_sources or movie.source in director_sources:
                    continue
                if not (movie.countries & candidate.countries and movie.genres & candidate.genres
                        and ((16 in movie.genres) == (16 in candidate.genres))):
                    continue
                cast_sources[movie.source].append(mid)
                cast_people[movie.source].update(movie.cast & candidate.cast)
            people = set().union(*cast_people.values()) if cast_people else set()
            cast_units = min(2, len(cast_sources), len(people))
            units = len(series) + len(director_sources) + cast_units
            anchor = bool(series or director_sources or (len(cast_sources) >= 2 and len(people) >= 2))
            values.extend((len(series), len(director_sources), len(cast_sources), len(people), cast_units, units, anchor))
            if explain:
                contributions = []
                for axis, groups in (("collection", {("movie", mid): [mid] for mid in sorted(series)}),
                                     ("director", director_sources), ("cast", cast_sources)):
                    for source, mids in sorted(groups.items()):
                        shared = set().union(*(getattr(self.movies[mid], axis) & getattr(candidate, axis) for mid in mids))
                        contributions.append({"relation": axis, "source_key": [source[0], int(source[1])],
                            "movies": [{"movie_id": mid, "score": self.scores[mid]} for mid in sorted(mids)],
                            "shared_ids": sorted(int(x) for x in shared)})
                details[sign] = {**dict(zip(SIDE_NAMES, values[-7:])), "contributions": contributions}
        return np.asarray(values, np.float32), details


def qualifications(direct: np.ndarray, required: np.ndarray) -> np.ndarray:
    return (required == 0) | ((direct[:, 5] >= required + 2 * direct[:, 12]) & (direct[:, 6] > 0))


def augment(base: np.ndarray, names: list[str], direct: np.ndarray, public: pd.DataFrame):
    if direct.shape != (len(base), len(DIRECT_NAMES)):
        raise ValueError("strict direct row axis mismatch")
    required = public.required.to_numpy(np.float32)
    gap = public.gap.to_numpy(np.float32)
    known = public.d_kobis.notna().to_numpy(np.float32)
    values = np.log1p(direct)
    for i in (6, 13):
        values[:, i] = direct[:, i]
    matrix = np.column_stack((base, values, public.d_tmdb.to_numpy(np.float32),
        public.d_kobis.fillna(0).to_numpy(np.float32), known, gap, np.log1p(required),
        np.log1p(direct[:, 5]) * gap, np.log1p(direct[:, 12]) * gap,
        np.clip(direct[:, 5] - 2 * direct[:, 12] - required, -10, 10),
        qualifications(direct, required))).astype(np.float32)
    if not np.isfinite(matrix).all():
        raise ValueError("nonfinite strict features")
    return matrix, [*names, *EXTRA_NAMES]


def public_references(catalog: pd.DataFrame, reference_votes: float,
                      date: str = "2026-09-21") -> pd.DataFrame:
    """Cache candidate-excluded country/form/genre references over released films."""
    frame = catalog.sort_values("tmdb_id", kind="stable").reset_index(drop=True)
    if frame.tmdb_id.duplicated().any():
        raise ValueError("duplicate TMDB public identity")
    eligible = (pd.to_datetime(frame.release_date, errors="coerce").le(pd.Timestamp(date))
                & frame.status.eq("Released") & ~frame.adult.fillna(True)).to_numpy()
    votes = pd.to_numeric(frame.tmdb_vote_count, errors="coerce").to_numpy(float)
    avg = pd.to_numeric(frame.tmdb_vote_average, errors="coerce").to_numpy(float)
    valid = np.isfinite(votes) & (votes > 0) & (votes == np.floor(votes)) & np.isfinite(avg) & (avg > 0) & (avg <= 10)
    safe_votes = np.where(valid, votes, 0)
    scores = pd.to_numeric(frame.tmdb_public_score, errors="coerce").to_numpy(float)
    audience = pd.to_numeric(frame.kobis_audience_cumulative, errors="coerce").to_numpy(float)
    verified = (frame.kobis_link_status.fillna("").str.startswith("VERIFIED").to_numpy()
                & frame.kobis_value_valid.fillna(False).to_numpy(bool)
                & np.isfinite(audience) & (audience > 0))
    countries = [ids(v) or frozenset(["UNKNOWN"]) for v in frame.origin_country_codes]
    genres = [ids(v) or frozenset([-1]) for v in frame.genre_ids]
    groups = defaultdict(list)
    for pos in np.flatnonzero(eligible):
        a = 16 in genres[pos]
        groups[(None, None, None)].append(pos)
        for c in countries[pos]:
            groups[(c, None, None)].append(pos)
            groups[(c, a, None)].append(pos)
            for g in genres[pos]:
                groups[(c, a, g)].append(pos)
    tmdb_groups, kobis_groups = {}, {}
    for key, values in groups.items():
        idx = np.asarray(values, np.int64)
        t = idx[valid[idx] & np.isfinite(scores[idx])]
        order = np.lexsort((frame.tmdb_id.to_numpy()[t], -scores[t]))
        tmdb_groups[key] = (len(t), t[order[:11]])
        k = idx[verified[idx]]
        kobis_groups[key] = (set(k.tolist()), k)
    result = []
    for pos, tmdb in enumerate(frame.tmdb_id):
        a = 16 in genres[pos]
        t_refs, k_refs = [], []
        for c in countries[pos]:
            for g in genres[pos]:
                keys = [(c, a, g), (c, a, None), (c, None, None), (None, None, None)]
                for key in keys:
                    n, top = tmdb_groups.get(key, (0, np.empty(0, np.int64)))
                    own_valid = bool(eligible[pos] and valid[pos] and np.isfinite(scores[pos]))
                    if n - own_valid >= 30:
                        use = top[top != pos][:10]
                        t_refs.append(float(np.median(safe_votes[use])))
                        break
                if verified[pos]:
                    for key in keys:
                        members, idx = kobis_groups.get(key, (set(), np.empty(0, np.int64)))
                        if len(idx) - (pos in members) >= 30:
                            k_refs.append(float(np.quantile(audience[idx[idx != pos]], .9)))
                            break
        if not t_refs:
            raise ValueError(f"public reference unavailable for TMDB {tmdb}")
        t_ref = max(t_refs)
        k_ref = max(k_refs) if k_refs else None
        d_t = max(0., math.log10((t_ref + 1) / (safe_votes[pos] + 1)))
        d_k = max(0., math.log10((k_ref + 1) / (audience[pos] + 1))) if k_ref is not None else None
        gap, required = required_links(safe_votes[pos], reference_votes, d_t, d_k)
        result.append((int(tmdb), bool(eligible[pos]), float(safe_votes[pos]), t_ref, k_ref, d_t, d_k, gap, required))
    return pd.DataFrame(result, columns=["tmdb_id", "eligible_movie", "votes", "tmdb_reference",
        "kobis_reference", "d_tmdb", "d_kobis", "gap", "required"])
