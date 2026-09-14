"""Post-result population audit. No fitting, selection, or new raw rating decoding."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
import time
import zipfile

import numpy as np
import pandas as pd
from scipy import sparse

from rec046_common import pair_accuracy, digest, BLOCKS

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "outputs/recommendation-evidence/rec-ev-046"
OUT = ROOT / "outputs/recommendation-evidence/rec-ev-046-data-audit"
DOC = ROOT / "docs/recommendation/experiments/rec-ev-046/data-audit"
ARCHIVE = ROOT.parent / "MM/data/raw/ml-32m.zip"
CUTOFF = 1546300800
MAX_USER = 200948
ACTIVITY = ["0", "1_9", "10_29", "30_99", "100_299", "300_PLUS"]
ITEM = ["0", "1_9", "10_49", "50_PLUS"]
METHODS = ["ALS", "FM", "ALS_FM", "RIDGE", "GBT"]
KS = [0, 1, 5, 10, 30]
METRICS = ["mse", "mae", "pair_accuracy", "direct_rate"]


def pin(path):
    path = Path(path)
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(8 * 1024**2), b""):
            h.update(b)
    return {"bytes": path.stat().st_size, "sha256": h.hexdigest()}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    Path(path).write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def activity_bin(n):
    return np.asarray(ACTIVITY)[np.searchsorted([1, 10, 30, 100, 300], n, side="right")]


def item_bin(n):
    return np.asarray(ITEM)[np.searchsorted([1, 10, 50], n, side="right")]


def summarize(values):
    a = np.asarray(values)
    if not len(a):
        return {k: None for k in ["mean", "min", "p25", "median", "p75", "p95", "max"]}
    vals = [a.mean(), *np.quantile(a, [0, 0.25, 0.5, 0.75, 0.95, 1])]
    return dict(
        zip(["mean", "min", "p25", "median", "p75", "p95", "max"], map(float, vals))
    )


def bootstrap_mean(a, key, repeats=2000):
    a = np.asarray(a, dtype=float)
    a = a[np.isfinite(a)]
    if len(a) < 30:
        return np.nan, np.nan
    seed = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big")
    rng = np.random.default_rng(seed)
    result = np.empty(repeats)
    for start in range(0, repeats, 128):
        count = min(128, repeats - start)
        result[start : start + count] = a[
            rng.integers(len(a), size=(count, len(a)))
        ].mean(axis=1)
    return tuple(np.quantile(result, [0.025, 0.975]))


def check_sources():
    review = read(DOC / "pre-review.json")
    assert review["status"] == "PASS"
    for path, expected in review["fingerprint"].items():
        assert pin(ROOT / path) == expected, path
    pins = read(DOC / "input-pins.json")
    for path, expected in pins.items():
        assert pin(ROOT / path) == expected, path
    assert (
        pin(SOURCE / "completion-seal.json")["sha256"]
        == "4391b4407c0e33f53d38d62d0a902d89ed9613ec7cca7c3709ed1c3657db5ea1"
    )
    return review, pins


def scan_counts(roles, ids, held, contexts):
    """Load only uid, movie_id, timestamp. Selected target/input timestamps are count metadata."""
    width = MAX_USER + 1
    all_pre = np.zeros(width, np.int64)
    cat_pre = np.zeros(width, np.int64)
    nonheld_pre = np.zeros(width, np.int64)
    cat_post = np.zeros(width, np.int64)
    all_total = np.zeros(width, np.int64)
    full_item = np.zeros((3, len(ids)), np.int64)
    population_item = np.zeros(len(ids), np.int64)
    train_users = np.zeros((3, width), bool)
    for r, entries in enumerate(roles["selections"]):
        train_users[r, [x["uid"] for x in entries]] = True
    # Only 600 previously authorized validation/evaluation users need pair timestamps.
    special = set(roles["validation"]) | set(roles["evaluation_union"])
    needed = set()
    for cs in contexts:
        for c in cs:
            if c["k"] == 30:
                needed.update((c["uid"], int(ids[i])) for i in c["ei"] + c["oi"])
    stamp_map = {}
    rows = 0
    raw_min = np.iinfo(np.int64).max
    raw_max = 0
    with zipfile.ZipFile(ARCHIVE) as z, z.open("ml-32m/ratings.csv") as f:
        for t in pd.read_csv(
            f,
            usecols=["userId", "movieId", "timestamp"],
            chunksize=1_000_000,
            dtype={"userId": "int64", "movieId": "int64", "timestamp": "int64"},
        ):
            u = t.userId.to_numpy()
            m = t.movieId.to_numpy()
            st = t.timestamp.to_numpy()
            assert u.min() >= 1 and u.max() <= MAX_USER
            rows += len(t)
            raw_min = min(raw_min, int(st.min()))
            raw_max = max(raw_max, int(st.max()))
            all_total += np.bincount(u, minlength=width)
            pre = st < CUTOFF
            all_pre += np.bincount(u[pre], minlength=width)
            ix = np.searchsorted(ids, m)
            cat = (ix < len(ids)) & (ids[np.minimum(ix, len(ids) - 1)] == m)
            q = cat & pre
            population_item += np.bincount(ix[q], minlength=len(ids))
            cat_pre += np.bincount(u[q], minlength=width)
            nh = q.copy()
            nh[q] &= ~held[ix[q]]
            nonheld_pre += np.bincount(u[nh], minlength=width)
            cat_post += np.bincount(u[cat & ~pre], minlength=width)
            for r in range(3):
                take = nh & train_users[r, u]
                full_item[r] += np.bincount(ix[take], minlength=len(ids))
            subset = t[t.userId.isin(special)]
            for uid, mid, ts in subset.itertuples(index=False, name=None):
                if (uid, mid) in needed:
                    assert (uid, mid) not in stamp_map
                    stamp_map[uid, mid] = ts
    assert rows == 32000204
    assert len(stamp_map) == len(needed)
    assert all_total[1:].min() >= 20
    table = pd.DataFrame(
        {
            "uid": np.arange(1, width),
            "pre_all": all_pre[1:],
            "pre_catalog": cat_pre[1:],
            "pre_nonheld": nonheld_pre[1:],
            "post_catalog": cat_post[1:],
            "lifetime_count": all_total[1:],
        }
    )
    table["activity"] = activity_bin(table.pre_all.to_numpy())
    table.to_parquet(OUT / "user-activity.parquet", index=False)
    np.save(OUT / "same-cohort-full-item-counts.npy", full_item)
    np.save(OUT / "full-population-item-counts.npy", population_item)
    pd.DataFrame(
        [(u, m, t) for (u, m), t in stamp_map.items()],
        columns=["uid", "movie_id", "timestamp"],
    ).to_parquet(OUT / "authorized-timestamps.parquet", index=False)
    write(
        OUT / "scan.json",
        {
            "rows": rows,
            "raw_timestamp_min": raw_min,
            "raw_timestamp_max": raw_max,
            "loaded_columns": ["userId", "movieId", "timestamp"],
            "raw_rating_values_loaded": 0,
            "timestamp_pairs": len(stamp_map),
        },
    )
    return table.set_index("uid"), full_item, stamp_map


def cohort_tables(activity, roles, splits):
    cohorts = [
        ("RAW_ALL", -1, activity.index.to_numpy()),
        (
            "DEVELOPMENT_ALLOWED",
            -1,
            np.unique(
                np.concatenate(
                    [*splits["training_user_ids"], *splits["evaluation_user_ids"]]
                )
            ),
        ),
        ("EVALUATION_POOL", -1, roles["all_evaluation_pool"]),
        ("VALIDATION", -1, roles["validation"]),
        ("EVALUATION", -1, roles["evaluation_union"]),
    ]
    cohorts += [
        ("TRAIN_SELECTED", r, [x["uid"] for x in entries])
        for r, entries in enumerate(roles["selections"])
    ]
    flow = []
    for r, original in enumerate(splits["training_user_ids"]):
        legal = (
            set(map(int, original))
            - set(roles["all_evaluation_pool"])
            - set(roles["validation"])
        )
        assigned = {
            u: KS[int.from_bytes(digest("rec046-k", r, u)[:8], "big") % len(KS)]
            for u in legal
        }
        eligible = {
            u for u in legal if activity.loc[u, "pre_nonheld"] >= assigned[u] + 8
        }
        visited = []
        replay = []
        skipped = 0
        for u in sorted(legal, key=lambda u: digest("rec046-train", r, u)):
            visited.append(u)
            if u not in eligible:
                skipped += 1
                continue
            replay.append(u)
            if len(replay) == 12000:
                break
        assert replay == [a["uid"] for a in roles["selections"][r]]
        readiness = read(SOURCE / "readiness.json")["rounds"][r]
        assert skipped == readiness["skipped_insufficient"]
        cohorts += [
            ("TRAIN_ORIGINAL_ROLE_POOL", r, original),
            ("TRAIN_LEGAL_POOL", r, sorted(legal)),
            ("TRAIN_ELIGIBLE_POOL", r, sorted(eligible)),
        ]
        for stage, values in [
            ("LEGAL_POOL", legal),
            ("ELIGIBLE_POOL", eligible),
            ("VISITED_PREFIX", visited),
            ("SELECTED", replay),
        ]:
            for b in ACTIVITY:
                for k in KS:
                    members = [
                        u
                        for u in values
                        if assigned[u] == k and activity.loc[u, "activity"] == b
                    ]
                    flow.append(
                        {
                            "round": r,
                            "stage": stage,
                            "activity": b,
                            "k": k,
                            "users": len(members),
                            "insufficient_users": sum(
                                u not in eligible for u in members
                            ),
                        }
                    )
    pd.DataFrame(flow).to_csv(OUT / "training-selection.csv", index=False)
    summaries, bins = [], []
    for role, r, users in cohorts:
        a = activity.loc[users]
        for measure in ["pre_all", "pre_catalog", "pre_nonheld", "post_catalog"]:
            summaries.append(
                {
                    "role": role,
                    "round": r,
                    "measure": measure,
                    "users": len(a),
                    **summarize(a[measure]),
                }
            )
        for b in ACTIVITY:
            q = a[a.activity == b]
            bins.append(
                {
                    "role": role,
                    "round": r,
                    "activity": b,
                    "users": len(q),
                    "user_share": len(q) / len(a),
                    "pre_rating_rows": int(q.pre_all.sum()),
                    "pre_rating_share": q.pre_all.sum() / a.pre_all.sum()
                    if a.pre_all.sum()
                    else np.nan,
                    "post_catalog_2plus_users": int((q.post_catalog >= 2).sum()),
                    "pre_catalog_30plus_users": int((q.pre_catalog >= 30).sum()),
                    "original_temporal_eligible_users": int(
                        ((q.pre_catalog >= 30) & (q.post_catalog >= 2)).sum()
                    ),
                }
            )
    pd.DataFrame(summaries).to_csv(OUT / "user-distributions.csv", index=False)
    pd.DataFrame(bins).to_csv(OUT / "user-strata.csv", index=False)
    retention = []
    for r, entries in enumerate(roles["selections"]):
        a = activity.loc[[x["uid"] for x in entries]]
        n = sum(len(x["rows"]) for x in entries)
        raw, cat, nh = map(
            int, [a.pre_all.sum(), a.pre_catalog.sum(), a.pre_nonheld.sum()]
        )
        retention.append(
            {
                "round": r,
                "users": len(a),
                "raw_pre_rows": raw,
                "catalog_pre_rows": cat,
                "nonheld_pre_rows": nh,
                "actual_R_rows": n,
                "supervised_target_rows": len(a) * 8,
                "supervised_input_rows": n - len(a) * 8,
                "R_of_nonheld_fraction": n / nh,
                "R_of_raw_fraction": n / raw,
            }
        )
    pd.DataFrame(retention).to_csv(OUT / "retention.csv", index=False)


def distribution_distances():
    """Descriptive total variation; no hypothesis threshold or representativeness claim."""
    rows = []

    def record(r, axis, name_a, name_b, a, b):
        a, b = a.align(b, fill_value=0)
        assert np.isclose(a.sum(), 1) and np.isclose(b.sum(), 1)
        rows.append(
            {
                "round": r,
                "axis": axis,
                "a": name_a,
                "b": name_b,
                "total_variation": float(np.abs(a - b).sum() / 2),
                "max_bin_share_difference": float(np.abs(a - b).max()),
            }
        )

    users = pd.read_csv(OUT / "user-strata.csv", dtype={"activity": str})
    for r in range(3):
        u = {
            role: users[
                (users.role == role)
                & (users["round"] == (r if role == "TRAIN_SELECTED" else -1))
            ]
            .set_index("activity")
            .user_share
            for role in ["TRAIN_SELECTED", "VALIDATION", "EVALUATION"]
        }
        for a, b in [
            ("TRAIN_SELECTED", "VALIDATION"),
            ("TRAIN_SELECTED", "EVALUATION"),
            ("VALIDATION", "EVALUATION"),
        ]:
            record(r, "user_activity", a, b, u[a], u[b])
    movie = pd.read_csv(OUT / "movie-distributions.csv", dtype={"group": str})
    stars = pd.read_csv(OUT / "rating-distributions.csv")
    for r in range(3):
        for axis in ["support", "year", "activity", "rating"]:
            source = stars if axis == "rating" else movie[movie.axis == axis]
            index, share = (
                ("rating", "share") if axis == "rating" else ("group", "row_share")
            )
            x = {
                role: source[(source["round"] == r) & (source.role == role)].set_index(
                    index
                )[share]
                for role in ["TRAIN_TARGET", "VALIDATION_TARGET", "EVALUATION_TARGET"]
            }
            for a, b in [
                ("TRAIN_TARGET", "VALIDATION_TARGET"),
                ("TRAIN_TARGET", "EVALUATION_TARGET"),
                ("VALIDATION_TARGET", "EVALUATION_TARGET"),
            ]:
                record(r, "rating_row_" + axis, a, b, x[a], x[b])
    pd.DataFrame(rows).to_csv(OUT / "distribution-distances.csv", index=False)


def role_data(roles, r, contexts, ids, timestamps):
    train = pd.read_csv(SOURCE / f"r{r}/als.csv", names=["uid", "movie_id", "rating"])
    rows = [
        (a["uid"], mid, ts, "TRAIN_INPUT" if i < a["k"] else "TRAIN_TARGET", a["k"])
        for a in roles["selections"][r]
        for i, (ts, mid) in enumerate(a["rows"])
    ]
    details = pd.DataFrame(rows, columns=["uid", "movie_id", "timestamp", "part", "k"])
    train = train.merge(details, on=["uid", "movie_id"], validate="one_to_one")
    assert len(train) == sum(len(x["rows"]) for x in roles["selections"][r])
    data = {
        "TRAIN_R": train,
        "TRAIN_TARGET": train[train.part == "TRAIN_TARGET"],
        "TRAIN_INPUT": train[train.part == "TRAIN_INPUT"],
    }
    for role in ["validation", "evaluation"]:
        cs = [c for c in contexts if c["role"] == role and c["k"] == 30]
        target = pd.DataFrame(
            [(c["uid"], int(ids[i])) for c in cs for i in c["ei"]],
            columns=["uid", "movie_id"],
        )
        labels = pd.read_parquet(SOURCE / f"{role}-labels.parquet")
        target = target.merge(labels, on=["uid", "movie_id"], validate="one_to_one")
        target["timestamp"] = [
            timestamps[u, m]
            for u, m in target[["uid", "movie_id"]].itertuples(index=False, name=None)
        ]
        assert (target.timestamp >= CUTOFF).all()
        data[role.upper() + "_TARGET"] = target
        inp = pd.DataFrame(
            [
                (c["uid"], int(ids[i]), star, timestamps[c["uid"], int(ids[i])])
                for c in cs
                for i, star in zip(c["oi"], c["stars"])
            ],
            columns=["uid", "movie_id", "rating", "timestamp"],
        )
        assert (inp.timestamp < CUTOFF).all()
        data[role.upper() + "_INPUT30"] = inp
    return data


def distributions(roles, contexts, catalog, activity, full_item, timestamps):
    ids, held, years = [catalog[k] for k in ["movie_ids", "held", "years"]]
    rows, stars, dates, features, exposure, profiles = [], [], [], [], [], []
    for r, cs in enumerate(contexts):
        n = np.load(SOURCE / f"r{r}/train-counts.npy")
        data = role_data(roles, r, cs, ids, timestamps)
        actual = np.bincount(
            np.searchsorted(ids, data["TRAIN_R"].movie_id.to_numpy()),
            minlength=len(ids),
        )
        assert np.array_equal(actual, n)
        assert (n[held] == 0).all() and (n <= full_item[r]).all()
        x = sparse.load_npz(SOURCE / f"r{r}/movie-features.npz")
        vocabulary = read(SOURCE / f"r{r}/feature-info.json")["vocabulary"]
        assert list(vocabulary) == list(BLOCKS)
        for role, t in data.items():
            ix = np.searchsorted(ids, t.movie_id.to_numpy())
            groups = item_bin(n[ix])
            yr = years[ix]
            year_group = np.select(
                [yr <= 0, yr < 2000, yr < 2010, yr < 2019],
                ["UNKNOWN", "BEFORE_2000", "2000_2009", "2010_2018"],
                default="2019_PLUS",
            )
            for axis, values, levels in [
                ("support", groups, ITEM),
                (
                    "year",
                    year_group,
                    ["UNKNOWN", "BEFORE_2000", "2000_2009", "2010_2018", "2019_PLUS"],
                ),
                ("activity", activity.loc[t.uid, "activity"].to_numpy(), ACTIVITY),
            ]:
                for level in levels:
                    q = t[values == level]
                    rows.append(
                        {
                            "round": r,
                            "role": role,
                            "axis": axis,
                            "group": level,
                            "rating_rows": len(q),
                            "row_share": len(q) / len(t) if len(t) else np.nan,
                            "unique_users": q.uid.nunique(),
                            "unique_movies": q.movie_id.nunique(),
                        }
                    )
            for value in np.arange(0.5, 5.1, 0.5):
                stars.append(
                    {
                        "round": r,
                        "role": role,
                        "rating": value,
                        "rows": int((t.rating == value).sum()),
                        "share": float((t.rating == value).mean()),
                    }
                )
            dates.append(
                {
                    "round": r,
                    "role": role,
                    "users": t.uid.nunique(),
                    "movies": t.movie_id.nunique(),
                    "rows": len(t),
                    "row_mean_rating": t.rating.mean(),
                    "user_mean_rating": t.groupby("uid").rating.mean().mean(),
                    **{"timestamp_" + k: v for k, v in summarize(t.timestamp).items()},
                }
            )
            exposure.append(
                {
                    "round": r,
                    "role": role,
                    "row_fraction_actual_supported": float((n[ix] > 0).mean()),
                    "row_fraction_same_users_full_supported": float(
                        (full_item[r, ix] > 0).mean()
                    ),
                    "row_fraction_lost_by_R_sampling": float(
                        ((n[ix] == 0) & (full_item[r, ix] > 0)).mean()
                    ),
                    "row_fraction_forced_heldout": float(held[ix].mean()),
                }
            )
            offset = 0
            for block, words in vocabulary.items():
                known = (
                    np.asarray(x[:, offset : offset + len(words)].getnnz(axis=1)) > 0
                )
                oov = x[:, offset + len(words)].toarray().ravel() > 0
                for label, mask in [
                    ("empty_feature_block", ~known & ~oov),
                    ("oov_only", ~known & oov),
                    ("known_and_oov", known & oov),
                    ("known_only", known & ~oov),
                ]:
                    features.append(
                        {
                            "round": r,
                            "role": role,
                            "block": block,
                            "state": label,
                            "row_share": float(mask[ix].mean()),
                            "unique_movie_share": float(mask[np.unique(ix)].mean()),
                        }
                    )
                offset += len(words) + 1
            assert offset == x.shape[1]
        for role in ["TRAIN", "validation", "evaluation"]:
            for k in KS:
                if role == "TRAIN":
                    selected = [a for a in roles["selections"][r] if a["k"] == k]
                    lens = [len(a["rows"][:k]) for a in selected]
                    ages = [
                        (CUTOFF - ts) / 86400
                        for a in selected
                        for ts, _ in a["rows"][:k]
                    ]
                    users = [a["uid"] for a in selected]
                    support = [k] * len(selected)
                else:
                    selected = [c for c in cs if c["role"] == role and c["k"] == k]
                    users = [c["uid"] for c in selected]
                    lens = [len(c["oi"]) for c in selected]
                    support = [c["input_supported"] for c in selected]
                    ages = [
                        (CUTOFF - timestamps[c["uid"], int(ids[i])]) / 86400
                        for c in selected
                        for i in c["oi"]
                    ]
                profiles.append(
                    {
                        "round": r,
                        "role": role,
                        "k": k,
                        "users": len(users),
                        "input_rows": sum(lens),
                        "no_supported_input_users": sum(v == 0 for v in support)
                        if k
                        else 0,
                        "no_input_users": len(users) if k == 0 else 0,
                        "mean_supported_inputs": np.mean(support),
                        "mean_pre_activity": activity.loc[users, "pre_all"].mean(),
                        **{
                            "input_age_days_" + a: b for a, b in summarize(ages).items()
                        },
                    }
                )
    for name, records in [
        ("movie-distributions", rows),
        ("rating-distributions", stars),
        ("role-summaries", dates),
        ("feature-coverage", features),
        ("sampling-exposure", exposure),
        ("input-profiles", profiles),
    ]:
        pd.DataFrame(records).to_csv(OUT / f"{name}.csv", index=False)


def make_metrics(contexts, catalog, activity):
    ids = catalog["movie_ids"]
    records = []
    counts = defaultdict(
        lambda: {"users": set(), "movies": set(), "pairs": set(), "instances": 0}
    )
    labels = {
        role: pd.read_parquet(SOURCE / f"{role}-labels.parquet")
        .set_index(["uid", "movie_id"])
        .rating.to_dict()
        for role in ["validation", "evaluation"]
    }
    for r, cs in enumerate(contexts):
        n = np.load(SOURCE / f"r{r}/train-counts.npy")
        z = np.load(SOURCE / f"r{r}/selected.npz")
        assert list(z["methods"]) == METHODS
        for c in cs:
            ix = np.asarray(c["ei"])
            y = np.array([labels[c["role"]][c["uid"], int(ids[i])] for i in ix])
            pred = z["predictions"][c["start"] : c["stop"]]
            direct = z["direct_als"][c["start"] : c["stop"]]
            assert len(y) == len(pred) == len(direct) and np.isfinite(pred).all()
            a = activity.loc[c["uid"], "activity"]
            groups = item_bin(n[ix])
            for ib in ["ALL", *ITEM]:
                mask = np.ones(len(y), bool) if ib == "ALL" else groups == ib
                if not mask.any():
                    continue
                mids = ids[ix[mask]]
                key = (c["role"], c["k"], a, ib)
                q = counts[key]
                q["users"].add(c["uid"])
                q["movies"].update(map(int, mids))
                q["pairs"].update((c["uid"], int(m)) for m in mids)
                q["instances"] += len(mids)
                for j, method in enumerate(METHODS):
                    err = np.clip(pred[mask, j], 0.5, 5) - y[mask]
                    records.append(
                        {
                            "role": c["role"],
                            "round": r,
                            "uid": c["uid"],
                            "k": c["k"],
                            "activity": a,
                            "item_support": ib,
                            "method": method,
                            "ratings": len(mids),
                            "mse": float(np.mean(err**2)),
                            "mae": float(np.mean(np.abs(err))),
                            "pair_accuracy": pair_accuracy(y[mask], pred[mask, j]),
                            "direct_rate": float(direct[mask].mean()),
                        }
                    )
        print(f"Metrics round {r} complete", flush=True)
    t = pd.DataFrame(records)
    t.to_parquet(OUT / "user-round-metrics.parquet", index=False)
    count_rows = [
        {
            "role": role,
            "k": k,
            "activity": a,
            "item_support": ib,
            "users": len(v["users"]),
            "movies": len(v["movies"]),
            "rating_pairs": len(v["pairs"]),
            "round_rating_instances": v["instances"],
        }
        for (role, k, a, ib), v in counts.items()
    ]
    counts_frame = pd.DataFrame(count_rows)
    counts_frame.to_csv(OUT / "cell-counts.csv", index=False)
    return t, counts_frame


def aggregate(t, counts):
    keys = ["role", "k", "activity", "item_support", "method"]
    users = t.groupby(keys + ["uid"], as_index=False)[METRICS].mean()
    users.to_parquet(OUT / "user-metrics.parquet", index=False)
    rows = []
    for key, a in users.groupby(keys):
        d = dict(zip(keys, key))
        d["users"] = len(a)
        for metric in METRICS:
            finite = a[metric].dropna()
            lo, hi = bootstrap_mean(finite, "|".join(map(str, key)) + metric)
            d[metric] = finite.mean()
            d[metric + "_users"] = len(finite)
            d[metric + "_low"] = lo
            d[metric + "_high"] = hi
        rows.append(d)
    table = pd.DataFrame(rows).merge(
        counts, on=keys[:-1], validate="many_to_one", suffixes=("", "_count")
    )
    assert (table.users == table.users_count).all()
    table = table.drop(columns="users_count")
    grid = pd.MultiIndex.from_product(
        [["validation", "evaluation"], KS, ACTIVITY, ["ALL", *ITEM], METHODS],
        names=keys,
    ).to_frame(index=False)
    table = grid.merge(table, on=keys, how="left", validate="one_to_one")
    for column in [
        "users",
        "movies",
        "rating_pairs",
        "round_rating_instances",
        *[x + "_users" for x in METRICS],
    ]:
        table[column] = table[column].fillna(0).astype(int)
    table["status"] = np.select(
        [table.users == 0, table.users < 30],
        ["NO_DATA", "UNDER_30_USERS"],
        default="DESCRIPTIVE_ONLY",
    )
    for metric in METRICS:
        table[metric + "_status"] = np.select(
            [table[metric + "_users"] == 0, table[metric + "_users"] < 30],
            ["NO_DATA", "UNDER_30_USERS"],
            default="DESCRIPTIVE_ONLY",
        )
    table.to_csv(OUT / "cell-metrics.csv", index=False)
    # Check exact existing macro-user ALL metric, not an average of cells.
    original = pd.read_csv(SOURCE / "summary.csv")
    comparison = (
        users[(users.role == "evaluation") & (users.item_support == "ALL")]
        .groupby(["k", "method"])[METRICS]
        .mean()
    )
    for metric in METRICS[:3]:
        expected = original[original.group == "ALL"].set_index(["k", "method"])[metric]
        assert np.allclose(
            comparison[metric].reindex(expected.index), expected, atol=1e-12, rtol=0
        ), metric
    summary = []
    for (role, k, method), data in users.groupby(["role", "k", "method"]):
        for metric in METRICS:
            full = data[data.item_support == "ALL"][metric].dropna()
            cells = table[
                (table.role == role)
                & (table.k == k)
                & (table.method == method)
                & (table.item_support != "ALL")
            ]
            valid = cells[cells[metric + "_users"] > 0]
            adequate = cells[cells[metric + "_users"] >= 30]
            summary.append(
                {
                    "role": role,
                    "k": k,
                    "method": method,
                    "metric": metric,
                    "original_user_macro": full.mean(),
                    "observed_cell_equal_mean": valid[metric].mean(),
                    "adequate_cell_equal_mean": adequate[metric].mean(),
                    "observed_cells": len(valid),
                    "adequate_cells": len(adequate),
                    "intended_cells": 24,
                }
            )
    pd.DataFrame(summary).to_csv(OUT / "weighting-diagnostic.csv", index=False)


def main():
    review, pins = check_sources()
    assert not OUT.exists(), "Preserve existing audit outputs; do not overwrite"
    OUT.mkdir(parents=True)
    start = time.monotonic()
    roles = read(SOURCE / "roles.json")
    val, ev = set(roles["validation"]), set(roles["evaluation_union"])
    assert not val & ev
    for entries in roles["selections"]:
        assert not (
            {x["uid"] for x in entries} & (val | set(roles["all_evaluation_pool"]))
        )
    catalog = dict(np.load(SOURCE / "catalog.npz"))
    ids = catalog["movie_ids"]
    assert np.all(np.diff(ids) > 0)
    contexts = [read(SOURCE / f"r{r}/contexts.json") for r in range(3)]
    activity, full_item, timestamps = scan_counts(roles, ids, catalog["held"], contexts)
    assert (activity.loc[list(val | ev), "pre_catalog"] >= 30).all()
    print("Three-column count scan complete", flush=True)
    splits = np.load(
        ROOT / "outputs/recommendation-evidence/rec-ev-032/user-resplits/splits.npz"
    )
    cohort_tables(activity, roles, splits)
    distributions(roles, contexts, catalog, activity, full_item, timestamps)
    distribution_distances()
    metrics, counts = make_metrics(contexts, catalog, activity)
    aggregate(metrics, counts)
    for path, expected in pins.items():
        assert pin(ROOT / path) == expected, path
    write(
        OUT / "completion.json",
        {
            "status": "CALCULATED_PENDING_INDEPENDENT_REVIEW",
            "seconds": time.monotonic() - start,
            "pre_review": pin(DOC / "pre-review.json"),
            "fingerprint": review["fingerprint"],
            "files": {p.name: pin(p) for p in sorted(OUT.iterdir()) if p.is_file()},
        },
    )
    print("Population audit calculated; independent review pending", flush=True)


if __name__ == "__main__":
    main()
