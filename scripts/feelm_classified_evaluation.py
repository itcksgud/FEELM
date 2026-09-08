"""NumPy-only replay of a fixed observed-candidate recommendation comparison."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

METHODS = ("ALS_ONLY", "RULE_GENRE_SET", "KM_GENRE")
METRICS = ("MEAN_Q", "MIN_Q", "ANY_LOW_Q")
PAIRS = ((1, 0), (2, 0), (2, 1))
MISSING, GENERAL, TASTE, DISCOVERY = 0, 1, 2, 3


def require(ok, message):
    if not ok:
        raise ValueError(message)


def pin(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"bytes": Path(path).stat().st_size, "sha256": digest.hexdigest()}


def write_json(path, value):
    Path(path).write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def select(candidates):
    """No label/histogram input; indices address the immutable flat candidate rows."""
    c = candidates
    keys, offsets, ids = c["user_keys"], c["offsets"], c["movie_ids"]
    rank, score = c["global_ranks"], c["scores"]
    supported, format_only = c["factor_supported"], c["format_only"]
    taste, discovery, profiles = c["taste"], c["discovery"], c["profile_movie_ids"]
    n, total = len(keys), len(ids)
    require(
        n > 0 and len(set(keys.tolist())) == n and keys.tolist() == sorted(keys),
        "user axis",
    )
    require(
        offsets.shape == (n + 1,)
        and offsets.dtype.kind in "iu"
        and offsets[0] == 0
        and offsets[-1] == total
        and (np.diff(offsets) >= 0).all(),
        "offsets",
    )
    require(
        ids.dtype.kind in "iu"
        and (ids > 0).all()
        and rank.dtype.kind in "iu"
        and rank.shape
        == score.shape
        == supported.shape
        == format_only.shape
        == (total,),
        "row axes",
    )
    require(supported.dtype == format_only.dtype == np.dtype(bool), "support dtypes")
    require(
        taste.shape == discovery.shape == (total, 2)
        and taste.dtype == discovery.dtype == np.dtype(bool)
        and not (taste & discovery).any(),
        "role masks",
    )
    require(
        profiles.ndim == 2 and profiles.shape[0] == n and profiles.dtype.kind in "iu",
        "profiles",
    )
    require(
        np.array_equal(rank > 0, supported)
        and np.isfinite(score[supported]).all()
        and np.isneginf(score[~supported]).all(),
        "rank support/scores",
    )
    chosen = np.full((n, 3, 3), -1, dtype=np.int64)
    types = np.zeros_like(chosen, dtype=np.int8)
    counts = np.zeros((n, 3), dtype=np.int64)
    roles = np.zeros((n, 2, 2), dtype=np.int64)
    pool_counts = np.zeros(n, dtype=np.int64)
    statuses = np.full((n, 3), "", dtype="U24")
    for u in range(n):
        row = np.arange(offsets[u], offsets[u + 1], dtype=np.int64)
        require(
            len(np.unique(ids[row])) == len(row) and (np.diff(ids[row]) > 0).all(),
            "duplicate/unsorted E",
        )
        require(
            len(np.unique(profiles[u])) == profiles.shape[1]
            and not np.intersect1d(profiles[u], ids[row]).size,
            "O/E overlap",
        )
        ordered = row[supported[row]]
        ordered = ordered[np.argsort(rank[ordered], kind="stable")]
        require(len(np.unique(rank[ordered])) == len(ordered), "duplicate global ranks")
        require(
            np.array_equal(
                ordered, ordered[np.lexsort((ids[ordered], -score[ordered]))]
            ),
            "rank/score order",
        )
        ordered = ordered[~format_only[ordered]]
        pool_counts[u] = len(ordered)
        for method in range(3):
            if method == 0:
                selected = ordered[:3]
                kinds = [GENERAL] * len(selected)
                status = "GENERAL3" if len(selected) == 3 else "INSUFFICIENT_GENERAL"
            else:
                t = ordered[taste[ordered, method - 1]]
                d = ordered[discovery[ordered, method - 1]]
                roles[u, method - 1] = [len(t), len(d)]
                if len(t) < 2:
                    selected, kinds, status = (
                        t[:2],
                        [TASTE] * len(t),
                        "INSUFFICIENT_TASTE",
                    )
                elif len(d):
                    selected = np.concatenate([t[:2], d[:1]])
                    kinds, status = [TASTE, TASTE, DISCOVERY], "T2_D1"
                else:
                    selected, kinds = t[:3], [TASTE] * min(3, len(t))
                    status = "NO_DISCOVERY_T3" if len(t) >= 3 else "NO_DISCOVERY_NO_T3"
            length = len(selected)
            chosen[u, method, :length], types[u, method, :length] = selected, kinds
            counts[u, method], statuses[u, method] = length, status
    return {
        "user_keys": keys,
        "selected_indices": chosen,
        "types": types,
        "counts": counts,
        "role_counts": roles,
        "pool_counts": pool_counts,
        "statuses": statuses,
    }


def validate_labels(candidates, labels):
    for name in ("user_keys", "offsets", "movie_ids"):
        require(np.array_equal(candidates[name], labels[name]), "label axis: " + name)
    raw, h = labels["rating_raw"], labels["histograms"]
    require(
        labels["source_q"].shape == raw.shape and np.isfinite(labels["source_q"]).all(),
        "source Q axis/finite",
    )
    n = len(candidates["user_keys"])
    require(
        raw.shape == candidates["movie_ids"].shape
        and np.isin(raw, np.arange(1, 11) / 2).all(),
        "half-star ratings",
    )
    require(
        h.shape == (n, 10)
        and h.dtype.kind in "iu"
        and (h >= 0).all()
        and (h <= 10**9).all()
        and (h.sum(axis=1) > 0).all(),
        "histograms",
    )
    h = h.astype(np.int64)
    denominators = 2 * h.sum(axis=1)
    numerator = np.empty(len(raw), dtype=np.int64)
    index = (2 * raw - 1).astype(np.int64)
    for u in range(n):
        part = slice(labels["offsets"][u], labels["offsets"][u + 1])
        require(
            (np.bincount(index[part], minlength=10) <= h[u]).all(),
            "E exceeds histogram",
        )
        below = np.cumsum(h[u]) - h[u]
        numerator[part] = 2 * below[index[part]] + h[u, index[part]]
        require(
            np.allclose(
                numerator[part] / denominators[u],
                labels["source_q"][part],
                atol=1e-12,
                rtol=0,
            ),
            "source Q differs",
        )
    return numerator, denominators


def panel_numerators(selected, numerator, denominator, size):
    """Return integer metric numerators and explicit availability; no missing-slot imputation."""
    valid = selected["counts"] >= size
    result = np.zeros((*valid.shape, 3), dtype=np.int64)
    for u, method in zip(*np.nonzero(valid), strict=False):
        values = numerator[selected["selected_indices"][u, method, :size]]
        result[u, method] = [
            values.sum(),
            values.min(),
            int((5 * values <= denominator[u]).any()),
        ]
    scale = np.column_stack(
        [size * denominator, denominator, np.ones(len(denominator), dtype=np.int64)]
    )
    return result, scale, valid


def paired_deltas(metric_numerators, scale, valid):
    common = valid.all(axis=1)
    require(int(common.sum()) >= 2, "primary common users < 2")
    parts = [
        (metric_numerators[common, a] - metric_numerators[common, b]) / scale[common]
        for a, b in PAIRS
    ]
    return np.concatenate(parts, axis=1), common


def bootstrap(deltas, repeats=20000, seed=20260908, guard=lambda: None):
    require(
        deltas.ndim == 2
        and deltas.shape[0] >= 2
        and deltas.shape[1] == 9
        and np.isfinite(deltas).all()
        and repeats >= 2,
        "bootstrap inputs",
    )
    rng = np.random.Generator(np.random.PCG64(seed))
    draws = np.empty((repeats, 9), dtype=np.float64)
    # Row-major int64 draws shared by all nine comparisons; chunk size does not change RNG stream.
    for start in range(0, repeats, 128):
        count = min(128, repeats - start)
        indices = rng.integers(
            0, len(deltas), size=(count, len(deltas)), dtype=np.int64
        )
        draws[start : start + count] = deltas[indices].mean(axis=1)
        guard()
    interval = np.quantile(draws, [1 / 360, 359 / 360], axis=0, method="linear").T
    return draws, interval


def evaluate(candidates, selected, labels, guard=lambda: None):
    numerator, den = validate_labels(candidates, labels)
    n = len(den)
    top2, scale2, valid2 = panel_numerators(selected, numerator, den, 2)
    all3, scale3, valid3 = panel_numerators(selected, numerator, den, 3)
    delta, common2 = paired_deltas(top2, scale2, valid2)
    draws, ci = bootstrap(delta, guard=guard)
    primary = []
    for pair, (a, b) in enumerate(PAIRS):
        for k, metric in enumerate(METRICS):
            i = pair * 3 + k
            lo, hi = map(float, ci[i])
            direction = "UNDECIDED"
            if lo > 0:
                direction = "BETTER" if k < 2 else "WORSE"
            elif hi < 0:
                direction = "WORSE" if k < 2 else "BETTER"
            primary.append(
                {
                    "a": METHODS[a],
                    "b": METHODS[b],
                    "metric": metric,
                    "users": int(common2.sum()),
                    "delta": float(delta[:, i].mean()),
                    "interval": [lo, hi],
                    "width": hi - lo,
                    "direction": direction,
                    "different_users": int(np.count_nonzero(delta[:, i])),
                    "excluded_users": n - int(common2.sum()),
                }
            )
    methods = []
    for method, name in enumerate(METHODS):
        rec = {"method": name, "requests": n, "status_counts": {}, "panels": {}}
        status, counts = np.unique(selected["statuses"][:, method], return_counts=True)
        rec["status_counts"] = dict(zip(status.tolist(), counts.astype(int).tolist(), strict=False))
        for label, nums, scale, available in [
            ("FIRST_TWO", top2, scale2, valid2),
            ("FULL_THREE", all3, scale3, valid3),
        ]:
            common = available.all(axis=1)
            metric_values = nums[common, method] / scale[common]
            values = numerator[
                selected["selected_indices"][
                    common, method, : 2 if label == "FIRST_TWO" else 3
                ]
            ]
            raw = labels["rating_raw"][
                selected["selected_indices"][
                    common, method, : 2 if label == "FIRST_TWO" else 3
                ]
            ]
            rec["panels"][label] = {
                "common_users": int(common.sum()),
                "available_users": int(available[:, method].sum()),
                "excluded_users": int((~common).sum()),
                "metrics": dict(zip(METRICS, metric_values.mean(axis=0).tolist(), strict=False))
                if len(values)
                else None,
                "raw_histogram": {
                    str(x / 2): int((raw == x / 2).sum()) for x in range(1, 11)
                },
            }
        methods.append(rec)
    secondary = []
    dmask = selected["types"][:, :, 2] == DISCOVERY

    def third_comparison(a, b, mask, label):
        mask = mask & (selected["counts"][:, a] >= 3) & (selected["counts"][:, b] >= 3)
        u = np.flatnonzero(mask)
        if not len(u):
            return {"comparison": label, "users": 0, "delta_q": None, "delta_low": None}
        an = numerator[selected["selected_indices"][u, a, 2]]
        bn = numerator[selected["selected_indices"][u, b, 2]]
        return {
            "comparison": label,
            "users": len(u),
            "delta_q": float(((an - bn) / den[u]).mean()),
            "delta_low": float(
                ((5 * an <= den[u]).astype(int) - (5 * bn <= den[u]).astype(int)).mean()
            ),
        }

    for method in (1, 2):
        u = np.flatnonzero(dmask[:, method])
        nums = numerator[selected["selected_indices"][u, method, 2]]
        methods[method]["discovery"] = {
            "provided": len(u),
            "requests": n,
            "rate": len(u) / n,
            "mean_q": float((nums / den[u]).mean()) if len(u) else None,
            "low_fraction": float((5 * nums <= den[u]).mean()) if len(u) else None,
            "low_per_request": int((5 * nums <= den[u]).sum()) / n,
            "raw_histogram": {
                str(x / 2): int(
                    (
                        labels["rating_raw"][selected["selected_indices"][u, method, 2]]
                        == x / 2
                    ).sum()
                )
                for x in range(1, 11)
            },
        }
        secondary.append(
            third_comparison(
                method, 0, dmask[:, method], METHODS[method] + "_D_MINUS_GENERAL3"
            )
        )
    secondary.append(
        third_comparison(2, 1, dmask[:, 1] & dmask[:, 2], "KM_D_MINUS_RULE_D_COMMON")
    )
    summary = {
        "scope": "EXPLORATORY_OBSERVED_CANDIDATE_CONDITIONAL",
        "users": n,
        "methods": methods,
        "primary": primary,
        "secondary_descriptive": secondary,
        "common_first_two_users": int(common2.sum()),
        "common_full_three_users": int(valid3.all(axis=1).sum()),
        "common_discovery_users": int((dmask[:, 1] & dmask[:, 2]).sum()),
        "bootstrap": {
            "repeats": 20000,
            "seed": 20260908,
            "rng": "PCG64",
            "draw_dtype": "int64",
            "quantile_method": "linear",
            "family_size": 9,
            "tail": 1 / 360,
        },
        "new_fit": False,
        "new_fold_in": False,
        "service_quality_measured": False,
        "subjective_novelty_measured": False,
        "product_winner_selected": False,
    }
    details = {
        "q_numerator": numerator,
        "q_denominator": den,
        "first_two_numerators": top2,
        "first_two_scale": scale2,
        "first_two_available": valid2,
        "full_three_numerators": all3,
        "full_three_scale": scale3,
        "full_three_available": valid3,
        "primary_common": common2,
        "primary_deltas": delta,
        "bootstrap_means": draws,
    }
    return summary, details


def sealed_select(candidate_file, out_dir):
    """Write a fresh selection seal; the label path is deliberately not accepted here."""
    out = Path(out_dir)
    require(
        not out.exists() or not any(out.iterdir()),
        "output must be empty; partial output preserved",
    )
    out.mkdir(parents=True, exist_ok=True)
    with np.load(candidate_file, allow_pickle=False) as source:
        candidates = {k: source[k] for k in source.files}
    selected = select(candidates)
    np.savez_compressed(out / "selection.npz", **selected)
    write_json(
        out / "selection-seal.json",
        {
            "status": "SELECTED_BEFORE_LABELS",
            "candidate": pin(candidate_file),
            "code": pin(__file__),
            "selection": pin(out / "selection.npz"),
            "label_payload_opened": False,
        },
    )
    return candidates, selected


def sealed_evaluate(candidate_file, label_file, out_dir, guard=lambda: None):
    out = Path(out_dir)
    seal = json.loads((out / "selection-seal.json").read_text(encoding="utf-8"))
    require(
        seal["status"] == "SELECTED_BEFORE_LABELS"
        and seal["label_payload_opened"] is False
        and seal["candidate"] == pin(candidate_file)
        and seal["code"] == pin(__file__)
        and seal["selection"] == pin(out / "selection.npz"),
        "selection seal mismatch",
    )
    require(not (out / "summary.json").exists(), "evaluation already exists")
    with np.load(candidate_file, allow_pickle=False) as source:
        candidates = {k: source[k] for k in source.files}
    with np.load(out / "selection.npz", allow_pickle=False) as source:
        selected = {k: source[k] for k in source.files}
    # First label decoding in the replay path occurs after all seal checks above.
    with np.load(label_file, allow_pickle=False) as source:
        labels = {k: source[k] for k in source.files}
    summary, detail = evaluate(candidates, selected, labels, guard)
    write_json(out / "summary.json", summary)
    np.savez_compressed(out / "evaluation.npz", **detail)
    write_json(
        out / "evaluation-seal.json",
        {
            "status": "EVALUATED",
            "selection_seal": pin(out / "selection-seal.json"),
            "labels": pin(label_file),
            "summary": pin(out / "summary.json"),
            "evaluation": pin(out / "evaluation.npz"),
        },
    )
    return summary
