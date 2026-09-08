"""Fixed category preference probe. NumPy only; no ranking or recommendation policy."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

METHODS = ("RULE_GENRE_SET", "KM_GENRE", "TMDB_GENRES")
SLICES = (slice(0, 8), slice(8, 16), slice(16, 34))
STARS = np.arange(1, 11) / 2


def require(ok, message):
    if not ok:
        raise ValueError(message)


def pin(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"bytes": Path(path).stat().st_size, "sha256": digest.hexdigest()}


def write_json(path, value):
    Path(path).write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def load_npz(path):
    with np.load(path, allow_pickle=False) as source:
        return {name: source[name] for name in source.files}


def weights(membership, indices, part):
    x = membership[indices, part].astype(np.float64)
    mass = x.sum(axis=1)
    require((mass > 0).all(), "no common content classification")
    return x / mass[:, None]


def validate_input(data):
    d = data
    keys, ids = d["user_keys"], d["movie_ids"]
    n, size = len(keys), len(ids)
    require(n >= 2 and keys.tolist() == sorted(set(keys.tolist())), "user axis")
    require(
        ids.dtype.kind in "iu" and (ids > 0).all() and (np.diff(ids) > 0).all(),
        "catalog axis",
    )
    require(
        d["membership"].shape == (size, 34) and np.isin(d["membership"], [0, 1]).all(),
        "membership",
    )
    require(
        d["bayes"].shape == d["training_counts"].shape == (size,)
        and np.isfinite(d["bayes"]).all()
        and ((d["bayes"] >= 0.5) & (d["bayes"] <= 5)).all()
        and (d["training_counts"] >= 0).all(),
        "training baseline",
    )
    require(
        d["o_index"].shape == d["o_ratings"].shape == (n, 30)
        and np.isin(d["o_ratings"], STARS).all(),
        "O30 ratings/axis",
    )
    offsets, ei = d["e_offsets"], d["e_index"]
    require(
        offsets.shape == (n + 1,)
        and offsets.dtype.kind in "iu"
        and offsets[0] == 0
        and offsets[-1] == len(ei)
        and (np.diff(offsets) >= 2).all(),
        "E offsets",
    )
    for a in (d["o_index"], ei):
        require(
            a.dtype.kind in "iu" and (a >= 0).all() and (a < size).all(),
            "movie indices",
        )
    for u in range(n):
        eo = ei[offsets[u] : offsets[u + 1]]
        require(
            len(set(d["o_index"][u].tolist())) == 30
            and (np.diff(eo) > 0).all()
            and not np.intersect1d(eo, d["o_index"][u]).size,
            "O/E duplicate or overlap",
        )
    used = np.concatenate([d["o_index"].ravel(), ei])
    for part in SLICES:
        weights(d["membership"], used, part)
    require(
        (d["membership"][used, :8].sum(axis=1) == 1).all()
        and (d["membership"][used, 8:16].sum(axis=1) == 1).all(),
        "eight-group single assignment",
    )


def donor_mapping(keys):
    ordered = sorted(
        range(len(keys)),
        key=lambda i: (
            hashlib.sha256(("REC043-DONOR|" + str(keys[i])).encode()).hexdigest(),
            str(keys[i]),
        ),
    )
    donors = np.empty(len(keys), dtype=np.int64)
    donors[ordered] = np.roll(ordered, -1)
    require((donors != np.arange(len(keys))).all(), "self donor")
    return donors


def fit(data, guard=lambda: None):
    """Only O ratings, category membership and training-only movie baselines enter here."""
    validate_input(data)
    d = data
    n, total = len(d["user_keys"]), len(d["e_index"])
    effect, mass = np.zeros((n, 2, 34)), np.zeros((n, 34))
    centers = np.empty((n, 2))
    for u in range(n):
        oi = d["o_index"][u]
        raw = d["o_ratings"][u]
        residuals = np.column_stack([raw, raw - d["bayes"][oi]])
        centers[u] = residuals.mean(axis=0)
        residuals -= centers[u]
        for part in SLICES:
            x = weights(d["membership"], oi, part)
            mass[u, part] = x.sum(axis=0)
            coefficient = (x.T @ residuals / (mass[u, part] + 5)[:, None]).T
            coefficient[np.abs(coefficient) <= 1e-12] = 0
            effect[u, :, part] = coefficient
        guard()
    donors = donor_mapping(d["user_keys"])
    baseline, own, donor = (
        np.empty((total, 2)),
        np.empty((total, 2, 3)),
        np.empty((total, 3)),
    )
    for u in range(n):
        rows = slice(d["e_offsets"][u], d["e_offsets"][u + 1])
        ei = d["e_index"][rows]
        baseline[rows, 0] = centers[u, 0]
        baseline[rows, 1] = d["bayes"][ei] + centers[u, 1]
        for method, part in enumerate(SLICES):
            x = weights(d["membership"], ei, part)
            own[rows, :, method] = baseline[rows] + x @ effect[u, :, part].T
            donor[rows, method] = baseline[rows, 1] + x @ effect[donors[u], 1, part]
        guard()
    return {
        "effect": effect,
        "mass": mass,
        "centers": centers,
        "donors": donors,
        "baseline": baseline,
        "own": own,
        "donor": donor,
    }


def bootstrap(delta, guard=lambda: None):
    require(
        delta.ndim == 2
        and delta.shape[0] >= 2
        and delta.shape[1] == 3
        and np.isfinite(delta).all(),
        "bootstrap shape",
    )
    rng = np.random.Generator(np.random.PCG64(20260909))
    draws = np.empty((20000, 3))
    for start in range(0, 20000, 128):
        count = min(128, 20000 - start)
        idx = rng.integers(len(delta), size=(count, len(delta)), dtype=np.int64)
        draws[start : start + count] = delta[idx].mean(axis=1)
        guard()
    return draws, np.quantile(draws, [1 / 120, 119 / 120], axis=0, method="linear").T


def rate(count, total):
    return count / total if total else None


def describe(data, ratings, guard=lambda: None):
    """Post-seal observed O+E description, distinct from held-out validation."""
    d = data
    n = len(d["user_keys"])
    methods = []
    for method, part in enumerate(SLICES):
        g = part.stop - part.start
        bins = np.zeros((g, 10))
        o_mass, e_mass, participation = (
            np.zeros(g),
            np.zeros(g),
            np.zeros(g, dtype=np.int64),
        )
        eligible = np.zeros(g, dtype=np.int64)
        above, below, tied, mixed = (np.zeros(g, dtype=np.int64) for _ in range(4))
        relative_sum, std_sum = np.zeros(g), np.zeros(g)
        sparse_o = np.zeros((g, 2), dtype=np.int64)
        sparse_e = np.zeros((g, 2), dtype=np.int64)
        represented = np.zeros(g + 1, dtype=np.int64)
        unobserved_e, partial_e = 0, 0
        match, comparable, no_two, tie_cases = 0, 0, 0, 0
        all_sd, within_sd = [], []
        for u in range(n):
            rows = slice(d["e_offsets"][u], d["e_offsets"][u + 1])
            oi, ei = d["o_index"][u], d["e_index"][rows]
            xo, xe = (
                weights(d["membership"], oi, part),
                weights(d["membership"], ei, part),
            )
            oc, ec = (xo > 0).sum(axis=0), (xe > 0).sum(axis=0)
            sparse_o[:, 0] += oc == 0
            sparse_o[:, 1] += oc == 1
            sparse_e[:, 0] += ec == 0
            sparse_e[:, 1] += ec == 1
            represented[np.count_nonzero(oc)] += 1
            unseen_fraction = xe[:, oc == 0].sum(axis=1)
            unobserved_e += int((unseen_fraction >= 1 - 1e-12).sum())
            partial_e += int(
                ((unseen_fraction > 1e-12) & (unseen_fraction < 1 - 1e-12)).sum()
            )
            r = np.concatenate([d["o_ratings"][u], ratings[rows]])
            x = np.concatenate([xo, xe])
            m = x.sum(axis=0)
            mu = np.divide(x.T @ r, m, out=np.zeros(g), where=m > 0)
            variance = (
                np.divide(x.T @ (r * r), m, out=np.zeros(g), where=m > 0) - mu * mu
            )
            variance = np.maximum(variance, 0)
            o_mass += xo.sum(axis=0)
            e_mass += xe.sum(axis=0)
            participation += (x > 0).sum(axis=0)
            for k, star in enumerate(STARS):
                bins[:, k] += x[r == star].sum(axis=0)
            valid = m >= 5 - 1e-12
            delta = mu - r.mean()
            eligible += valid
            above += valid & (delta > 1e-12)
            below += valid & (delta < -1e-12)
            tied += valid & (np.abs(delta) <= 1e-12)
            mixed += (
                valid
                & (x[r > r.mean() + 1e-12].sum(axis=0) > 0)
                & (x[r < r.mean() - 1e-12].sum(axis=0) > 0)
            )
            relative_sum += np.where(valid, delta, 0)
            std_sum += np.where(valid, np.sqrt(variance), 0)
            all_sd.append(float(r.std()))
            within_sd.append(float(np.sqrt(np.sum(m * variance) / len(r))))
            vg = np.flatnonzero(valid)
            if len(vg) < 2:
                no_two += 1
            else:
                most = vg[np.isclose(m[vg], m[vg].max(), rtol=0, atol=1e-12)]
                favorite = vg[np.isclose(mu[vg], mu[vg].max(), rtol=0, atol=1e-12)]
                if len(most) != 1 or len(favorite) != 1:
                    tie_cases += 1
                else:
                    comparable += 1
                    match += int(most[0] == favorite[0])
            if u % 100 == 0:
                guard()
        groups = []
        for k in range(g):
            groups.append(
                {
                    "group_index": k,
                    "O_mass": float(o_mass[k]),
                    "E_mass": float(e_mass[k]),
                    "OE_participation_count": int(participation[k]),
                    "OE_rating_mass": bins[k].tolist(),
                    "O_zero_users": int(sparse_o[k, 0]),
                    "O_one_users": int(sparse_o[k, 1]),
                    "E_zero_users": int(sparse_e[k, 0]),
                    "E_one_users": int(sparse_e[k, 1]),
                    "eligible_users_mass_ge_5": int(eligible[k]),
                    "above_own_mean_users": int(above[k]),
                    "below_own_mean_users": int(below[k]),
                    "tied_own_mean_users": int(tied[k]),
                    "mixed_users": int(mixed[k]),
                    "mean_within_user_relative_stars": rate(
                        float(relative_sum[k]), int(eligible[k])
                    ),
                    "mean_within_group_star_sd": rate(
                        float(std_sum[k]), int(eligible[k])
                    ),
                }
            )
        methods.append(
            {
                "method": METHODS[method],
                "groups": groups,
                "O_represented_group_users": represented.tolist(),
                "E_no_group_seen_in_O": unobserved_e,
                "E_partial_group_seen_in_O": partial_e,
                "most_evaluated_vs_highest_rated": {
                    "same": match,
                    "comparable": comparable,
                    "same_fraction": rate(match, comparable),
                    "fewer_than_two_supported_groups": no_two,
                    "nonunique_maximum_users": tie_cases,
                },
                "mean_user_rating_sd": float(np.mean(all_sd)),
                "mean_user_pooled_within_group_sd": float(np.mean(within_sd)),
                "mixed_eligible_user_group_fraction": rate(
                    int(mixed.sum()), int(eligible.sum())
                ),
            }
        )
    return methods


def evaluate(data, fitted, labels, guard=lambda: None):
    d, f = data, fitted
    for key in ("user_keys", "e_offsets", "e_index"):
        require(np.array_equal(d[key], labels[key]), "label axis: " + key)
    raw = labels["rating_raw"]
    require(
        raw.shape == d["e_index"].shape and np.isin(raw, STARS).all(),
        "E half-star ratings",
    )
    n = len(d["user_keys"])
    baseline_mse, own_mse = np.empty((n, 2)), np.empty((n, 2, 3))
    donor_mse, covariance = np.empty((n, 3)), np.empty((n, 3))
    # method, ALL_UNSEEN/PARTIAL_SEEN/ALL_SEEN, pairs/users/sum user baseline MSE/sum user own MSE
    seen_mse = np.zeros((3, 3, 4))
    for u in range(n):
        rows = slice(d["e_offsets"][u], d["e_offsets"][u + 1])
        y = raw[rows]
        baseline_mse[u] = ((f["baseline"][rows] - y[:, None]) ** 2).mean(axis=0)
        own_mse[u] = ((f["own"][rows] - y[:, None, None]) ** 2).mean(axis=0)
        donor_mse[u] = ((f["donor"][rows] - y[:, None]) ** 2).mean(axis=0)
        yc = y - d["bayes"][d["e_index"][rows]]
        yc -= yc.mean()
        signal = f["own"][rows, 1] - f["baseline"][rows, 1, None]
        covariance[u] = ((signal - signal.mean(axis=0)) * yc[:, None]).mean(axis=0)
        for method, part in enumerate(SLICES):
            x = weights(d["membership"], d["e_index"][rows], part)
            seen_mass = x[:, f["mass"][u, part] > 0].sum(axis=1)
            states = np.where(
                seen_mass <= 1e-12, 0, np.where(seen_mass >= 1 - 1e-12, 2, 1)
            )
            for state in (0, 1, 2):
                mask = states == state
                if mask.any():
                    seen_mse[method, state] += [
                        int(mask.sum()),
                        1,
                        float(((f["baseline"][rows, 1][mask] - y[mask]) ** 2).mean()),
                        float(
                            ((f["own"][rows, 1, method][mask] - y[mask]) ** 2).mean()
                        ),
                    ]
        guard()
    primary = baseline_mse[:, 1, None] - own_mse[:, 1]
    # Numerical zeros are not evidence of a preference effect.
    primary[np.abs(primary) <= 1e-12] = 0
    draws, intervals = bootstrap(primary, guard)
    results = []
    for method, name in enumerate(METHODS):
        lo, hi = intervals[method].tolist()
        results.append(
            {
                "method": name,
                "users": n,
                "adjusted_baseline_mse": float(baseline_mse[:, 1].mean()),
                "adjusted_own_mse": float(own_mse[:, 1, method].mean()),
                "adjusted_gain": float(primary[:, method].mean()),
                "interval": [lo, hi],
                "direction": "IMPROVED"
                if lo > 0
                else "WORSENED"
                if hi < 0
                else "UNDECIDED",
                "width": hi - lo,
                "users_with_positive_gain": int((primary[:, method] > 0).sum()),
                "raw_baseline_mse": float(baseline_mse[:, 0].mean()),
                "raw_own_mse": float(own_mse[:, 0, method].mean()),
                "raw_gain_descriptive": float(
                    (baseline_mse[:, 0] - own_mse[:, 0, method]).mean()
                ),
                "adjusted_covariance_descriptive": float(covariance[:, method].mean()),
                "donor_mse_descriptive": float(donor_mse[:, method].mean()),
                "own_vs_donor_gain_descriptive": float(
                    (donor_mse[:, method] - own_mse[:, 1, method]).mean()
                ),
                "seen_conditioned": [
                    {
                        "state": label,
                        "pairs": int(a[0]),
                        "users": int(a[1]),
                        "baseline_mse": rate(float(a[2]), int(a[1])),
                        "own_mse": rate(float(a[3]), int(a[1])),
                        "gain": rate(float(a[2] - a[3]), int(a[1])),
                    }
                    for label, a in zip(
                        ("ALL_UNSEEN", "PARTIAL_SEEN", "ALL_SEEN"),
                        seen_mse[method],
                        strict=True,
                    )
                ],
            }
        )
    summary = {
        "experiment": "REC043",
        "scope": "EXPLORATORY_OBSERVED_RATINGS_FIXED_PROBE",
        "users": n,
        "O_pairs": int(d["o_index"].size),
        "E_pairs": len(raw),
        "methods": results,
        "descriptive": describe(d, raw, guard),
        "stars": STARS.tolist(),
        "no_training_rating_pairs": {
            "O": int((d["training_counts"][d["o_index"]] == 0).sum()),
            "E": int((d["training_counts"][d["e_index"]] == 0).sum()),
        },
        "bootstrap": {
            "repeats": 20000,
            "seed": 20260909,
            "family_size": 3,
            "tails": [1 / 120, 119 / 120],
        },
        "donor_inferential": False,
        "new_ALS_or_clustering_fit": False,
        "service_quality_measured": False,
    }
    return summary, {
        "baseline_mse": baseline_mse,
        "own_mse": own_mse,
        "donor_mse": donor_mse,
        "covariance": covariance,
        "primary_delta": primary,
        "bootstrap_means": draws,
    }


def sealed_fit(input_path, output):
    out = Path(output)
    require(
        not out.exists() or not any(out.iterdir()),
        "output must be empty; partial files preserved",
    )
    out.mkdir(parents=True, exist_ok=True)
    d = load_npz(input_path)
    f = fit(d)
    np.savez_compressed(out / "predictions.npz", **f)
    write_json(
        out / "prediction-seal.json",
        {
            "status": "PREDICTED_BEFORE_E_LABELS",
            "input": pin(input_path),
            "code": pin(__file__),
            "predictions": pin(out / "predictions.npz"),
            "E_values_opened": False,
        },
    )


def verify_prediction_seal(input_path, output):
    out = Path(output)
    seal = json.loads((out / "prediction-seal.json").read_text(encoding="utf-8"))
    require(
        seal["status"] == "PREDICTED_BEFORE_E_LABELS"
        and seal["E_values_opened"] is False
        and seal["input"] == pin(input_path)
        and seal["code"] == pin(__file__)
        and seal["predictions"] == pin(out / "predictions.npz"),
        "prediction seal mismatch",
    )


def sealed_evaluate(input_path, label_path, output, guard=lambda: None):
    out = Path(output)
    verify_prediction_seal(input_path, out)
    require(not (out / "summary.json").exists(), "already evaluated")
    d, f = load_npz(input_path), load_npz(out / "predictions.npz")
    labels = load_npz(label_path)
    summary, details = evaluate(d, f, labels, guard)
    write_json(out / "summary.json", summary)
    np.savez_compressed(out / "evaluation.npz", **details)
    write_json(
        out / "evaluation-seal.json",
        {
            "status": "EVALUATED",
            "prediction_seal": pin(out / "prediction-seal.json"),
            "labels": pin(label_path),
            "summary": pin(out / "summary.json"),
            "evaluation": pin(out / "evaluation.npz"),
        },
    )
    return summary
