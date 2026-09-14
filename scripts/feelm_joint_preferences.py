"""REC045 fixed joint residual probes; fitting has no target-rating argument."""

from __future__ import annotations

import hashlib
from collections import Counter

import numpy as np
import pandas as pd
from scipy import sparse
from scipy.sparse.linalg import lsmr

from feelm_preference_structure import require
from feelm_residual_taste import normalize

BLOCKS = (
    "CROWD",
    "GENRE",
    "KEYWORD",
    "COUNTRY_LANGUAGE",
    "ERA_RUNTIME",
    "DIRECTOR",
    "CAST",
    "COMPANY_COLLECTION",
)
SMALL_METHODS = ("BASE", "SHARED", "GENRE", "FULL")
METHODS = (*SMALL_METHODS, *("DROP_" + name for name in BLOCKS), "PLUS_PERSON_ERA")


def categorical(rows, min_df=1, idf=False):
    sets = [sorted(set(row)) for row in rows]
    df = Counter(value for row in sets for value in row)
    vocabulary = sorted(k for k, n in df.items() if n >= min_df)
    lookup = {k: i for i, k in enumerate(vocabulary)}
    ri, ci = [], []
    for i, row in enumerate(sets):
        for key in row:
            if key in lookup:
                ri.append(i)
                ci.append(lookup[key])
    weights = np.log((len(rows) + 1) / (1 + np.array([df[k] for k in vocabulary]))) + 1
    x = sparse.csr_matrix(
        (weights[ci] if idf else np.ones(len(ci)), (ri, ci)),
        shape=(len(rows), len(vocabulary)),
    )
    return normalize(x), {
        "columns": len(vocabulary),
        "nonempty": int((x.getnnz(axis=1) > 0).sum()),
        "min_df": min_df,
        "idf": idf,
    }


def static_features(frame):
    def tokens(values, prefix):
        return [prefix + str(value) for value in values]

    rows = {name: [] for name in (*BLOCKS[1:], "PERSON_ERA")}
    for r in frame.itertuples(index=False):
        era = str(int(r.release_year) // 10 * 10) if pd.notna(r.release_year) else ""
        runtime = (
            str(int(r.runtime_minutes) // 30 * 30)
            if pd.notna(r.runtime_minutes) and r.runtime_minutes > 0
            else ""
        )
        rows["GENRE"].append(tokens([v for v in r.genre_ids if v != 10770], "g:"))
        rows["KEYWORD"].append(tokens(r.keyword_ids, "k:"))
        rows["COUNTRY_LANGUAGE"].append(
            tokens(r.production_country_codes, "c:")
            + (["l:" + r.original_language] if r.original_language else [])
        )
        rows["ERA_RUNTIME"].append(
            (["e:" + era] if era else []) + (["r:" + runtime] if runtime else [])
        )
        rows["DIRECTOR"].append(tokens(r.director_ids, "d:"))
        rows["CAST"].append(tokens(r.top5_cast_ids, "a:"))
        rows["COMPANY_COLLECTION"].append(
            tokens(r.production_company_ids, "p:") + tokens(r.collection_ids, "s:")
        )
        rows["PERSON_ERA"].append(
            [
                v + "@" + era
                for v in tokens(r.director_ids, "d:") + tokens(r.top5_cast_ids, "a:")
            ]
            if era
            else []
        )
    matrices, info = {}, {}
    for name, values in rows.items():
        matrices[name], info[name] = categorical(
            values, min_df=5 if name == "KEYWORD" else 1, idf=name == "KEYWORD"
        )
    return matrices, info


def training_statistics(train, movie_ids, allowed_users, forbidden_users):
    users = train.user_id.to_numpy(dtype=np.int64)
    movies = train.movie_id.to_numpy(dtype=np.int64)
    ratings = train.rating.to_numpy(dtype=np.float64)
    require(
        np.isin(users, allowed_users).all()
        and not np.isin(users, forbidden_users).any(),
        "training roles",
    )
    require(np.isin(ratings, np.arange(1, 11) / 2).all(), "training half stars")
    source_rows = len(ratings)
    inside = np.isin(movies, movie_ids)
    users, movies, ratings = users[inside], movies[inside], ratings[inside]
    require(len(ratings) > 0, "no catalog training ratings")
    lookup = {int(v): i for i, v in enumerate(movie_ids)}
    idx = np.fromiter(
        (lookup[int(v)] for v in movies), dtype=np.int64, count=len(movies)
    )
    n = np.bincount(idx, minlength=len(movie_ids)).astype(float)
    sums = np.bincount(idx, weights=ratings, minlength=len(movie_ids))
    squares = np.bincount(idx, weights=ratings**2, minlength=len(movie_ids))
    mu, variance = ratings.mean(), ratings.var()
    bayes = (sums + 50 * mu) / (n + 50)
    means = np.divide(sums, n, out=np.full(len(n), mu), where=n > 0)
    raw_variance = np.maximum(
        np.divide(squares, n, out=np.full(len(n), variance + mu**2), where=n > 0)
        - means**2,
        0,
    )
    shrunk_variance = (n * raw_variance + 50 * variance) / (n + 50)
    residual = ratings - bayes[idx]
    un = np.bincount(users)
    ur = np.bincount(users, weights=residual)
    uoffset = np.divide(ur, un, out=np.zeros_like(ur), where=un > 0)
    item_residual_sum = np.bincount(
        idx, weights=residual - uoffset[users], minlength=len(movie_ids)
    )
    raw_crowd = np.column_stack([bayes, np.log1p(n), shrunk_variance])
    center = raw_crowd[n > 0].mean(axis=0)
    scale = raw_crowd[n > 0].std(axis=0)
    scale[scale < 1e-12] = 1
    crowd = normalize(np.clip((raw_crowd - center) / scale, -3, 3))
    stats = dict(
        bayes=bayes,
        counts=n,
        sums=sums,
        squares=squares,
        shrunk_variance=shrunk_variance,
        global_mean=np.array(mu),
        global_variance=np.array(variance),
        crowd_center=center,
        crowd_scale=scale,
        item_residual_sum=item_residual_sum,
        training_rows=np.array(len(ratings)),
        source_training_rows=np.array(source_rows),
        outside_catalog_rows=np.array(source_rows - len(ratings)),
        training_users=np.array(len(np.unique(users))),
    )
    return stats, crowd


def shared_fit(matrices, stats):
    x = sparse.hstack([matrices[name] for name in BLOCKS], format="csr")
    n = stats["counts"]
    target = np.divide(stats["item_residual_sum"], n, out=np.zeros(len(n)), where=n > 0)
    a = sparse.diags(np.sqrt(n)) @ x
    result = lsmr(
        a,
        target * np.sqrt(n),
        damp=np.sqrt(50),
        atol=1e-7,
        btol=1e-7,
        conlim=1e8,
        maxiter=1000,
    )
    require(result[1] in (0, 1, 2, 4, 5), "global ridge did not converge")
    correction = np.asarray(x @ result[0]).ravel()
    require(np.isfinite(correction).all(), "global correction finite")
    return correction, {
        "stop": int(result[1]),
        "iterations": int(result[2]),
        "residual_norm": float(result[3]),
        "normal_residual_norm": float(result[4]),
        "lambda": 50.0,
    }


def input_selection(key, movies, k, draw):
    if k == 30:
        return np.arange(30)
    order = sorted(
        range(30),
        key=lambda j: hashlib.sha256(
            f"rec045-input-v1|{key}|{draw}|{int(movies[j])}".encode()
        ).digest(),
    )
    return np.array(order[:k])


def predict_user(matrices, bayes, shared, oi, ratings, ei, full=False):
    """Ridge after selecting inputs; all offsets use only passed ratings."""
    methods = METHODS if full else SMALL_METHODS
    require(
        len(oi) == len(ratings) and not np.intersect1d(oi, ei).size, "O/E separation"
    )
    require(np.isin(ratings, np.arange(1, 11) / 2).all(), "input half stars")
    baseline = bayes[ei] + np.mean(ratings - bayes[oi])
    g = bayes + shared
    offset = np.mean(ratings - g[oi])
    common = g[ei] + offset
    residual = ratings - g[oi] - offset
    grams, kernels = {}, {}
    for name in (*BLOCKS, "PERSON_ERA"):
        if name == "PERSON_ERA" and not full:
            continue
        xo, xe = matrices[name][oi], matrices[name][ei]
        grams[name] = (xo @ xo.T).toarray()
        kernels[name] = (xe @ xo.T).toarray()
    gram = sum(grams[name] for name in BLOCKS)
    kernel = sum(kernels[name] for name in BLOCKS)

    def fitted(a, b):
        return common + b @ np.linalg.solve(a + 5 * np.eye(len(oi)), residual)

    predictions = [
        baseline,
        common,
        fitted(grams["GENRE"], kernels["GENRE"]),
        fitted(gram, kernel),
    ]
    if full:
        predictions.extend(
            fitted(gram - grams[name], kernel - kernels[name]) for name in BLOCKS
        )
        predictions.append(
            fitted(gram + grams["PERSON_ERA"], kernel + kernels["PERSON_ERA"])
        )
    result = np.column_stack(predictions)
    require(
        result.shape == (len(ei), len(methods)) and np.isfinite(result).all(),
        "predictions",
    )
    # Numeric crowd is signed, so categorical support is tracked separately.
    support = np.column_stack([(kernels[name] > 0).sum(axis=1) for name in BLOCKS[1:]])
    return result, support


def unique_user_means(keys, values):
    unique, inverse = np.unique(keys, return_inverse=True)
    counts = np.bincount(inverse)
    aggregate = np.zeros((len(unique), values.shape[1]))
    np.add.at(aggregate, inverse, values)
    return unique, aggregate / counts[:, None]


def intervals(values, family=13, repeats=20000, seed=20260911):
    require(np.isfinite(values).all() and len(values) >= 2, "bootstrap inputs")
    rng = np.random.Generator(np.random.PCG64(seed))
    draws = np.empty((repeats, values.shape[1]))
    for start in range(0, repeats, 64):
        count = min(64, repeats - start)
        draws[start : start + count] = values[
            rng.integers(len(values), size=(count, len(values)))
        ].mean(axis=1)
    return np.quantile(draws, [0.05 / (2 * family), 1 - 0.05 / (2 * family)], axis=0).T
