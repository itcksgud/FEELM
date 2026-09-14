"""REC044: fixed residual ridge probes; no access to hidden ratings during fit."""

from __future__ import annotations

from collections import Counter

import numpy as np
from scipy import sparse

from feelm_preference_structure import donor_mapping, require, validate_input

METHODS = ("RULE8", "KMEANS8", "GENRES", "KEYWORDS", "GENRES_KEYWORDS")
# Indices into MSE columns: baseline=0, probes=1..5. Positive = right improves.
CONTRASTS = tuple((f"BASE_vs_{m}", 0, i + 1) for i, m in enumerate(METHODS)) + (
    ("GENRES_vs_COMBINED", 3, 5),
    ("KEYWORDS_vs_COMBINED", 4, 5),
    ("RULE8_vs_GENRES", 1, 3),
    ("KMEANS8_vs_GENRES", 2, 3),
)


def normalize(x):
    x = sparse.csr_matrix(x, dtype=np.float64)
    norm = np.sqrt(np.asarray(x.multiply(x).sum(axis=1)).ravel())
    return (
        sparse.diags(np.divide(1, norm, out=np.zeros_like(norm), where=norm > 0)) @ x
    ).tocsr()


def features(data, keyword_lists, min_df=5):
    require(len(keyword_lists) == len(data["movie_ids"]), "keyword axis")
    sets = [sorted(set(map(int, row))) for row in keyword_lists]
    counts = Counter(k for row in sets for k in row)
    vocabulary = sorted(k for k, count in counts.items() if count >= min_df)
    require(bool(vocabulary), "empty keyword vocabulary")
    lookup = {k: j for j, k in enumerate(vocabulary)}
    rows, cols = [], []
    for i, keys in enumerate(sets):
        for k in keys:
            if k in lookup:
                rows.append(i)
                cols.append(lookup[k])
    idf = np.log((1 + len(sets)) / (1 + np.array([counts[k] for k in vocabulary]))) + 1
    keywords = sparse.csr_matrix(
        (idf[cols], (rows, cols)), shape=(len(sets), len(vocabulary))
    )
    rule, km, genre = [
        normalize(data["membership"][:, part])
        for part in (slice(0, 8), slice(8, 16), slice(16, 34))
    ]
    keywords = normalize(keywords)
    combined = sparse.hstack([genre, keywords], format="csr") / np.sqrt(2)
    return [rule, km, genre, keywords, combined.tocsr()], {
        "keyword_ids": vocabulary,
        "keyword_df": [counts[k] for k in vocabulary],
        "keyword_idf": idf.tolist(),
        "catalog_movies": len(sets),
        "raw_keyword_vocabulary": len(counts),
        "raw_keyword_missing": sum(not row for row in sets),
        "filtered_keyword_missing": int((keywords.getnnz(axis=1) == 0).sum()),
        "keyword_nnz": keywords.nnz,
    }


def ridge_alpha(xo, residual, strength):
    require(strength > 0 and np.isfinite(strength), "ridge strength")
    gram = (xo @ xo.T).toarray()
    return np.linalg.solve(gram + strength * np.eye(len(residual)), residual)


def fit(data, matrices, strength=5.0, guard=lambda: None):
    validate_input(data)
    require(len(matrices) == len(METHODS), "methods")
    for x in matrices:
        require(
            x.shape[0] == len(data["movie_ids"]) and np.isfinite(x.data).all(),
            "features",
        )
    n, total = len(data["user_keys"]), len(data["e_index"])
    residual = data["o_ratings"] - data["bayes"][data["o_index"]]
    offset = residual.mean(axis=1)
    residual = residual - offset[:, None]
    alpha = np.empty((n, 5, 30))
    for u in range(n):
        for m, x in enumerate(matrices):
            alpha[u, m] = ridge_alpha(x[data["o_index"][u]], residual[u], strength)
        guard()
    donors = donor_mapping(data["user_keys"])
    baseline, own, donor = np.empty(total), np.empty((total, 5)), np.empty((total, 5))
    simple = np.empty((total, 2))
    coverage = np.zeros((5, 4), dtype=np.int64)
    keyword_state = np.empty(total, dtype=np.int8)
    donor_overlap = 0
    for u in range(n):
        rows = slice(data["e_offsets"][u], data["e_offsets"][u + 1])
        oi, ei = data["o_index"][u], data["e_index"][rows]
        di = data["o_index"][donors[u]]
        donor_overlap += int(np.isin(ei, di).sum())
        baseline[rows] = data["bayes"][ei] + offset[u]
        simple[rows, 0] = data["o_ratings"][u].mean()
        simple[rows, 1] = data["bayes"][ei]
        for m, x in enumerate(matrices):
            kernel = (x[ei] @ x[oi].T).toarray()
            own[rows, m] = baseline[rows] + kernel @ alpha[u, m]
            donor[rows, m] = baseline[rows] + (x[ei] @ x[di].T) @ alpha[donors[u], m]
            coverage[m] += [
                int((x[oi].getnnz(axis=1) == 0).sum()),
                int((x[ei].getnnz(axis=1) == 0).sum()),
                int((np.abs(kernel).sum(axis=1) == 0).sum()),
                len(ei),
            ]
            if m == 3:
                keyword_state[rows] = np.where(
                    x[ei].getnnz(axis=1) == 0,
                    0,
                    np.where(np.abs(kernel).sum(axis=1) == 0, 1, 2),
                )
        guard()
    return dict(
        offset=offset,
        alpha=alpha,
        donors=donors,
        baseline=baseline,
        own=own,
        donor=donor,
        simple=simple,
        coverage=coverage,
        keyword_state=keyword_state,
        donor_O_recipient_E_overlap=np.array(donor_overlap),
    )


def bootstrap(delta, repeats=20000, seed=20260910, guard=lambda: None):
    require(
        delta.ndim == 2 and delta.shape[0] >= 2 and delta.shape[1] == 9,
        "bootstrap axis",
    )
    require(np.isfinite(delta).all(), "bootstrap finite")
    rng = np.random.Generator(np.random.PCG64(seed))
    draws = np.empty((repeats, 9))
    for start in range(0, repeats, 128):
        count = min(128, repeats - start)
        indices = rng.integers(len(delta), size=(count, len(delta)), dtype=np.int64)
        draws[start : start + count] = delta[indices].mean(axis=1)
        guard()
    return draws, np.quantile(
        draws, [0.05 / 18, 1 - 0.05 / 18], axis=0, method="linear"
    ).T


def genre_repetition(data, raw):
    """Descriptive O/E genre deviations; independently center each side."""
    x = data["membership"][:, 16:].astype(float)
    x /= np.maximum(x.sum(axis=1, keepdims=True), 1)
    records = [[] for _ in range(18)]
    for u in range(len(data["user_keys"])):
        rows = slice(data["e_offsets"][u], data["e_offsets"][u + 1])
        oi, ei = data["o_index"][u], data["e_index"][rows]
        ro = data["o_ratings"][u] - data["bayes"][oi]
        re = raw[rows] - data["bayes"][ei]
        ro, re = ro - ro.mean(), re - re.mean()
        mo, me = x[oi].sum(axis=0), x[ei].sum(axis=0)
        a = np.divide(x[oi].T @ ro, mo, out=np.zeros(18), where=mo > 0)
        b = np.divide(x[ei].T @ re, me, out=np.zeros(18), where=me > 0)
        for g in np.flatnonzero((mo >= 2 - 1e-12) & (me >= 5 - 1e-12)):
            records[g].append((float(a[g]), float(b[g])))
    result = []
    for g, records_g in enumerate(records):
        a = np.asarray(records_g).reshape(-1, 2)
        nonzero = (np.abs(a) > 1e-12).all(axis=1)
        pos, neg = a[:, 0] > 1e-12, a[:, 0] < -1e-12
        corr = (
            float(np.corrcoef(a.T)[0, 1])
            if len(a) > 1 and (a.std(axis=0) > 1e-12).all()
            else None
        )
        result.append(
            {
                "genre_code": str(data["group_codes"][16 + g]),
                "users": len(a),
                "O_E_pearson_descriptive": corr,
                "nonzero_users": int(nonzero.sum()),
                "same_sign_users": int(((a[nonzero, 0] * a[nonzero, 1]) > 0).sum()),
                "O_positive_users": int(pos.sum()),
                "O_negative_users": int(neg.sum()),
                "E_mean_for_O_positive": float(a[pos, 1].mean()) if pos.any() else None,
                "E_mean_for_O_negative": float(a[neg, 1].mean()) if neg.any() else None,
            }
        )
    return result


def evaluate(data, fitted, labels, guard=lambda: None):
    for key in ("user_keys", "e_offsets", "e_index"):
        require(np.array_equal(data[key], labels[key]), "label axis: " + key)
    raw = labels["rating_raw"]
    require(
        raw.shape == data["e_index"].shape and np.isin(raw, np.arange(1, 11) / 2).all(),
        "E half-star values",
    )
    n = len(data["user_keys"])
    prediction = np.column_stack([fitted["baseline"], fitted["own"]])
    mse, mae, donor_mse, simple_mse = (
        np.empty((n, 6)),
        np.empty((n, 6)),
        np.empty((n, 5)),
        np.empty((n, 2)),
    )
    strata = np.zeros((3, 5))
    for u in range(n):
        rows = slice(data["e_offsets"][u], data["e_offsets"][u + 1])
        error = prediction[rows] - raw[rows, None]
        mse[u], mae[u] = (error**2).mean(axis=0), np.abs(error).mean(axis=0)
        donor_mse[u] = ((fitted["donor"][rows] - raw[rows, None]) ** 2).mean(axis=0)
        simple_mse[u] = ((fitted["simple"][rows] - raw[rows, None]) ** 2).mean(axis=0)
        for s in range(3):
            mask = fitted["keyword_state"][rows] == s
            if mask.any():
                strata[s] += [
                    int(mask.sum()),
                    1,
                    *np.mean(error[mask][:, [0, 4, 5]] ** 2, axis=0),
                ]
    delta = np.column_stack([mse[:, a] - mse[:, b] for _, a, b in CONTRASTS])
    delta[np.abs(delta) <= 1e-12] = 0
    draws, intervals = bootstrap(delta, guard=guard)
    comparisons = []
    for j, (name, _, _) in enumerate(CONTRASTS):
        lo, hi = intervals[j]
        comparisons.append(
            dict(
                name=name,
                gain=float(delta[:, j].mean()),
                interval=[float(lo), float(hi)],
                direction="IMPROVED"
                if lo > 0
                else "WORSENED"
                if hi < 0
                else "UNDECIDED",
            )
        )
    summary = dict(
        experiment="REC044",
        scope="EXPLORATORY_REUSED_OBSERVED_COHORT",
        users=n,
        O_pairs=int(data["o_index"].size),
        E_pairs=len(raw),
        methods=["BASE", *METHODS],
        mse=mse.mean(axis=0).tolist(),
        mae=mae.mean(axis=0).tolist(),
        comparisons=comparisons,
        donor_mse_descriptive=donor_mse.mean(axis=0).tolist(),
        own_vs_donor_gain_descriptive=(donor_mse - mse[:, 1:]).mean(axis=0).tolist(),
        simple_baselines_descriptive={
            "user_O_mean_mse": float(simple_mse[:, 0].mean()),
            "movie_train_mean_mse": float(simple_mse[:, 1].mean()),
        },
        coverage_columns=[
            "O_zero_features",
            "E_zero_features",
            "E_no_O_feature_overlap",
            "E_pairs",
        ],
        coverage=fitted["coverage"].tolist(),
        donor_O_recipient_E_overlap=int(fitted["donor_O_recipient_E_overlap"]),
        keyword_strata_descriptive=[
            dict(
                state=label,
                pairs=int(row[0]),
                users=int(row[1]),
                mse_BASE_KEYWORDS_COMBINED=(row[2:] / row[1]).tolist()
                if row[1]
                else None,
            )
            for label, row in zip(
                ("NO_KEYWORDS", "NO_O_OVERLAP", "O_OVERLAP"), strata, strict=True
            )
        ],
        genre_repetition_descriptive=genre_repetition(data, raw),
        bootstrap={
            "repeats": 20000,
            "seed": 20260910,
            "family": 9,
            "tails": [0.05 / 18, 1 - 0.05 / 18],
        },
        no_training_rating_pairs={
            "O": int((data["training_counts"][data["o_index"]] == 0).sum()),
            "E": int((data["training_counts"][data["e_index"]] == 0).sum()),
        },
        service_quality_measured=False,
        independent_confirmation=False,
    )
    return summary, dict(
        mse=mse,
        mae=mae,
        donor_mse=donor_mse,
        simple_mse=simple_mse,
        delta=delta,
        bootstrap_means=draws,
    )
