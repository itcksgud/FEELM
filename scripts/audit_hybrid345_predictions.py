"""Independent, label-free audit of hybrid345 mapper and prediction artifacts.

This audit intentionally never opens the configured evaluation-label file.  It
reimplements the numerical checks rather than importing hybrid345_models.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/recommendation/experiments/hybrid345"
OUT = ROOT / "outputs/recommendation-evidence/hybrid345"
EXPECTED_NAMES = [
    "ALS", "STRUCTURED_DIRECT", "E5_DIRECT", "QWEN_DIRECT",
    "FM150_s339", "FM150_s344", "FM150_s345",
    "GBT120_s339", "GBT120_s344", "GBT120_s345", "ALS_C2F",
]
STRATA_NAMES = ["SUPPORT_1_9", "SUPPORT_10_49", "SUPPORT_50_PLUS"]


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def pin(path):
    path = Path(path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def split_independent(movie_ids, support, salt, fraction):
    strata = np.where(support < 10, 0, np.where(support < 50, 1, 2))
    train = np.zeros(len(movie_ids), dtype=bool)
    for code in range(3):
        positions = np.flatnonzero(strata == code)
        ranked = sorted(
            positions.tolist(),
            key=lambda pos: (
                hashlib.sha256((salt + str(int(movie_ids[pos]))).encode("utf-8")).digest(),
                int(movie_ids[pos]),
            ),
        )
        count = int(np.floor(fraction * len(positions)))
        train[np.asarray(ranked[:count], dtype=np.int64)] = True
    return train, ~train, strata


def fit_ridge_independent(x, y, alpha):
    x_mean = x.mean(axis=0)
    y_mean = y.mean(axis=0)
    xc = x - x_mean
    yc = y - y_mean
    coefficient = np.linalg.solve(
        xc.T @ xc + float(alpha) * np.eye(x.shape[1], dtype=np.float64),
        xc.T @ yc,
    )
    return coefficient, y_mean - x_mean @ coefficient


def reconstruction_independent(target, predicted):
    target_norm = np.linalg.norm(target, axis=1)
    predicted_norm = np.linalg.norm(predicted, axis=1)
    valid = (target_norm > 1e-12) & (predicted_norm > 1e-12)
    cosine = np.sum(target[valid] * predicted[valid], axis=1) / (
        target_norm[valid] * predicted_norm[valid]
    )
    return {
        "items": int(len(target)),
        "cosine_items": int(valid.sum()),
        "mean_cosine": float(cosine.mean()),
        "rmse": float(np.sqrt(np.mean((predicted - target) ** 2))),
        "mean_target_norm": float(target_norm.mean()),
        "mean_prediction_norm": float(predicted_norm.mean()),
    }


def weights_independent(stars, prior):
    stars = np.asarray(stars, dtype=np.float64)
    indices = np.rint(stars * 2 - 1).astype(np.int64)
    check(np.allclose(stars, (indices + 1) / 2, rtol=0, atol=1e-12), "non-half-star history")
    histogram = np.bincount(indices, minlength=10)
    below = np.cumsum(histogram) - histogram
    quantile = (below[indices] + 0.5 * histogram[indices] + 5.0 * prior[indices]) / (
        len(indices) + 5.0
    )
    return 2.0 * quantile - 1.0


def dense_independent(matrix, history, stars, candidates, prior):
    if not len(history):
        return np.full(len(candidates), np.nan), False
    weights = weights_independent(stars, prior)
    denominator = float(np.abs(weights).sum())
    if denominator <= 1e-12:
        return np.full(len(candidates), np.nan), False
    profile = np.asarray(matrix[history], dtype=np.float64).T @ (weights / denominator)
    if np.linalg.norm(profile) <= 1e-12:
        return np.full(len(candidates), np.nan), False
    return np.asarray(matrix[candidates], dtype=np.float64) @ profile, True


def sparse_independent(matrix, history, stars, candidates, prior):
    if not len(history):
        return np.full(len(candidates), np.nan), False
    weights = weights_independent(stars, prior)
    denominator = float(np.abs(weights).sum())
    if denominator <= 1e-12:
        return np.full(len(candidates), np.nan), False
    profile = np.asarray(matrix[history].T @ (weights / denominator)).ravel()
    if np.linalg.norm(profile) <= 1e-12:
        return np.full(len(candidates), np.nan), False
    return np.asarray(matrix[candidates] @ profile).ravel().astype(np.float64), True


def fold_in_independent(history_factors, stars, candidate_factors):
    if not len(history_factors):
        return np.full(len(candidate_factors), np.nan), False
    user = np.linalg.solve(
        history_factors.T @ history_factors
        + 0.1 * len(history_factors) * np.eye(history_factors.shape[1]),
        history_factors.T @ stars,
    )
    return candidate_factors @ user, True


def assert_metrics_close(actual, expected, prefix):
    for key in ("items", "cosine_items"):
        check(actual[key] == expected[key], f"{prefix} {key}")
    for key in ("mean_cosine", "rmse", "mean_target_norm", "mean_prediction_norm"):
        check(np.isclose(actual[key], expected[key], rtol=1e-12, atol=1e-12), f"{prefix} {key}")


def main():
    cfg = json.loads((DOC / "config.json").read_text(encoding="utf-8"))
    label_relative = cfg["sources"]["evaluation_labels"]

    # Import only the recursive verifier. Its reviewed contract skips labels.
    sys.path.insert(0, str(ROOT / "scripts"))
    from hybrid345_score import verify_predictions

    producer = verify_predictions()

    source_pin_count = 0
    for relative, expected in cfg["source_pins"].items():
        if relative == label_relative:
            continue
        check(pin(ROOT / relative) == expected, "configured source pin drift: " + relative)
        source_pin_count += 1
    check(source_pin_count == 36, "unexpected non-label source pin count")

    catalog = pd.read_parquet(ROOT / cfg["sources"]["catalog"])
    movie_ids = catalog.movie_id.to_numpy(np.int64)
    support_mask = (~catalog.blocked.to_numpy(bool)) & (catalog.train_count.to_numpy(np.int64) > 0)
    check(len(movie_ids) == 85517 and np.all(np.diff(movie_ids) > 0), "catalog movie axis")
    check(int(support_mask.sum()) == 45074, "ALS teacher population")

    factor_frame = pd.read_parquet(ROOT / cfg["sources"]["als_factors"])
    factor_ids = factor_frame.id.to_numpy(np.int64)
    factor_values = np.vstack(factor_frame.features).astype(np.float64)
    factor_order = np.argsort(factor_ids)
    factor_ids = factor_ids[factor_order]
    factor_values = factor_values[factor_order]
    check(np.array_equal(factor_ids, movie_ids[support_mask]), "factor identity axis")
    check(factor_values.shape == (45074, 32) and np.isfinite(factor_values).all(), "factor values")

    fit_seal = json.loads((ROOT / cfg["sources"]["combination_fit_seal"]).read_text(encoding="utf-8"))
    expected_factor_files = {
        name: value for name, value in fit_seal["files"].items()
        if name.startswith("ALS/item-factors/")
    }
    factor_dir = ROOT / cfg["sources"]["als_factors"]
    actual_factor_files = {
        "ALS/item-factors/" + path.relative_to(factor_dir).as_posix(): pin(path)
        for path in factor_dir.rglob("*") if path.is_file()
    }
    check(actual_factor_files == expected_factor_files, "factor directory differs from fit seal")

    qwen = np.load(OUT / "qwen-embeddings.npy", mmap_mode="r")
    mapper = np.load(OUT / "mapper.npz", allow_pickle=False)
    mapped = np.load(OUT / "mapped-factors.npy", mmap_mode="r")
    check(qwen.shape == (85517, 1024) and qwen.dtype == np.float32, "Qwen axis")
    check(mapped.shape == (85517, 32) and mapped.dtype == np.float32, "mapped factor axis")
    check(np.isfinite(mapped).all(), "mapped factors finite")

    support = catalog.train_count.to_numpy(np.int64)[support_mask]
    train, validation, strata = split_independent(
        movie_ids[support_mask], support, cfg["mapper"]["split_salt"], cfg["mapper"]["train_fraction"]
    )
    check(np.array_equal(train, mapper["train_mask"]), "independent train split")
    check(np.array_equal(validation, mapper["validation_mask"]), "independent validation split")
    check(np.array_equal(strata, mapper["strata"]), "independent strata")
    check(np.array_equal(movie_ids[support_mask], mapper["teacher_movie_ids"]), "mapper teacher IDs")

    x_teacher = np.asarray(qwen[support_mask], dtype=np.float64)
    selection = json.loads((OUT / "mapper-selection.json").read_text(encoding="utf-8"))
    independent_candidates = []
    for alpha in cfg["mapper"]["alphas"]:
        coefficient, intercept = fit_ridge_independent(x_teacher[train], factor_values[train], alpha)
        predicted = x_teacher[validation] @ coefficient + intercept
        metrics = {"ALL": reconstruction_independent(factor_values[validation], predicted)}
        for code, name in enumerate(STRATA_NAMES):
            mask = validation & (strata == code)
            metrics[name] = reconstruction_independent(factor_values[mask], (x_teacher[mask] @ coefficient + intercept))
        independent_candidates.append({"alpha": float(alpha), "validation": metrics})
    for actual, expected in zip(independent_candidates, selection["candidates"]):
        check(actual["alpha"] == expected["alpha"], "mapper alpha order")
        for name in ["ALL", *STRATA_NAMES]:
            assert_metrics_close(actual["validation"][name], expected["validation"][name], f"mapper {actual['alpha']} {name}")
    independent_selected = min(
        independent_candidates,
        key=lambda item: (-item["validation"]["ALL"]["mean_cosine"], item["validation"]["ALL"]["rmse"], item["alpha"]),
    )
    check(independent_selected["alpha"] == selection["selected_alpha"] == float(mapper["alpha"]), "alpha selection")

    final_coefficient, final_intercept = fit_ridge_independent(
        x_teacher, factor_values, independent_selected["alpha"]
    )
    check(np.allclose(final_coefficient, mapper["coefficient"], rtol=1e-12, atol=1e-12), "final coefficient")
    check(np.allclose(final_intercept, mapper["intercept"], rtol=1e-12, atol=1e-12), "final intercept")
    mapped_max_difference = 0.0
    for start in range(0, len(movie_ids), 4096):
        stop = min(start + 4096, len(movie_ids))
        expected = (np.asarray(qwen[start:stop], dtype=np.float64) @ final_coefficient + final_intercept).astype(np.float32)
        mapped_max_difference = max(mapped_max_difference, float(np.max(np.abs(expected - mapped[start:stop]))))
    check(mapped_max_difference == 0.0, "mapped factor values")

    bundle = np.load(OUT / "predictions.npz", allow_pickle=False)
    names = bundle["names"].tolist()
    predictions = bundle["predictions"]
    availability = bundle["availability"]
    check(names == EXPECTED_NAMES, "prediction names")
    check(predictions.shape == (93230, 11), "prediction shape")
    check(availability.shape == predictions.shape and availability.dtype == np.bool_, "availability shape")
    check(np.array_equal(np.isfinite(predictions), availability), "availability equals finiteness")

    old_als = np.load(ROOT / cfg["sources"]["als_predictions"], allow_pickle=False)
    old_als_column = old_als["names"].tolist().index("ACTUAL_ALS")
    old_als_values = old_als["predictions"][:, old_als_column]
    check(np.array_equal(predictions[:, 0], old_als_values, equal_nan=True), "ALS exact reuse")
    check(np.array_equal(availability[:, 0], old_als["actual_direct"].astype(bool)), "ALS availability reuse")

    fixed_reuse = {}
    for family, source_key in (("FM150", "fm_predictions_by_seed"), ("GBT120", "gbt_predictions_by_seed")):
        for seed, relative in cfg["sources"][source_key].items():
            name = f"{family}_s{seed}"
            source = np.load(ROOT / relative, allow_pickle=False)
            column = names.index(name)
            check(np.array_equal(predictions[:, column], source), name + " exact reuse")
            check(availability[:, column].all(), name + " availability")
            seal_relative = cfg["sources"][("fm" if family == "FM150" else "gbt") + "_seals_by_seed"][seed]
            model_seal = json.loads((ROOT / seal_relative).read_text(encoding="utf-8"))
            sealed_key = f"{name}/predictions.npy"
            check(model_seal["files"][sealed_key] == pin(ROOT / relative), name + " model seal prediction pin")
            fixed_reuse[name] = pin(ROOT / relative)

    contexts = json.loads((ROOT / cfg["sources"]["contexts"]).read_text(encoding="utf-8"))
    check(len(contexts) == 1350, "context count")
    check(contexts[0]["start"] == 0 and contexts[-1]["stop"] == 93230, "context outer row axis")
    previous_stop = 0
    for context in contexts:
        check(context["start"] == previous_stop, "contiguous context slices")
        check(context["stop"] - context["start"] == len(context["ei"]), "context target length")
        check(len(context["oi"]) == len(context["stars"]) == context["h"], "context history length")
        previous_stop = context["stop"]

    prior = np.load(OUT / "current-prior.npz", allow_pickle=False)["g0_mid"].astype(np.float64)
    structured = sparse.load_npz(ROOT / cfg["sources"]["structured"]).tocsr()
    e5 = np.load(ROOT / cfg["sources"]["e5"], mmap_mode="r")
    factor_catalog = np.full((len(movie_ids), 32), np.nan, dtype=np.float64)
    factor_catalog[support_mask] = factor_values

    # Deterministic audit set covers every cap plus active/inactive and varying histories.
    audit_indices = sorted(set(
        [0, 1, 269, 270, 271, 539, 540, 541, 809, 810, 811, 1079, 1080, 1081, 1349]
        + [int(v) for v in np.linspace(0, len(contexts) - 1, 36)]
    ))
    max_differences = {name: 0.0 for name in ("STRUCTURED_DIRECT", "E5_DIRECT", "QWEN_DIRECT", "ALS_C2F")}
    availability_table = pd.read_csv(OUT / "availability.csv")
    check(len(availability_table) == len(contexts) * 4, "availability table row count")
    check(
        availability_table.columns.tolist()
        == ["context_index", "uid", "cap", "h", "model", "available", "target_rows", "actual_als_history"],
        "availability table columns",
    )
    availability_lookup = {
        (int(row.context_index), row.model): bool(row.available)
        for row in availability_table.itertuples(index=False)
    }
    actual_history_counts = {
        (int(row.context_index), row.model): int(row.actual_als_history)
        for row in availability_table.itertuples(index=False)
    }

    for index in audit_indices:
        context = contexts[index]
        history = np.asarray(context["oi"], dtype=np.int64)
        stars = np.asarray(context["stars"], dtype=np.float64)
        candidates = np.asarray(context["ei"], dtype=np.int64)
        row_slice = slice(int(context["start"]), int(context["stop"]))
        supported_history = support_mask[history]
        recalculated = {
            "STRUCTURED_DIRECT": sparse_independent(structured, history, stars, candidates, prior),
            "E5_DIRECT": dense_independent(e5, history, stars, candidates, prior),
            "QWEN_DIRECT": dense_independent(qwen, history, stars, candidates, prior),
            "ALS_C2F": fold_in_independent(
                factor_catalog[history[supported_history]], stars[supported_history],
                np.asarray(mapped[candidates], dtype=np.float64),
            ),
        }
        for name, (expected_values, expected_active) in recalculated.items():
            column = names.index(name)
            actual_values = predictions[row_slice, column]
            actual_active = bool(availability[row_slice, column].all())
            check(actual_active == expected_active, f"{name} active state context {index}")
            check(availability_lookup[(index, name)] == expected_active, f"{name} CSV state context {index}")
            check(actual_history_counts[(index, name)] == int(supported_history.sum()), f"history support context {index}")
            if expected_active:
                difference = float(np.max(np.abs(actual_values - expected_values)))
                max_differences[name] = max(max_differences[name], difference)
                check(np.allclose(actual_values, expected_values, rtol=1e-11, atol=1e-11), f"{name} values context {index}")
            else:
                check(np.isnan(actual_values).all(), f"{name} inactive N/A context {index}")

    # Every context must have one uniform generated-column availability state.
    for index, context in enumerate(contexts):
        row_slice = slice(int(context["start"]), int(context["stop"]))
        history = np.asarray(context["oi"], dtype=np.int64)
        stars = np.asarray(context["stars"], dtype=np.float64)
        check(((history >= 0) & (history < len(movie_ids))).all(), f"history bounds context {index}")
        candidates = np.asarray(context["ei"], dtype=np.int64)
        check(((candidates >= 0) & (candidates < len(movie_ids))).all(), f"candidate bounds context {index}")
        check(not np.intersect1d(history, candidates).size, f"history/target overlap context {index}")
        rows_for_context = availability_table[availability_table.context_index == index]
        check(len(rows_for_context) == 4, f"availability rows context {index}")
        check(set(rows_for_context.model) == {"STRUCTURED_DIRECT", "E5_DIRECT", "QWEN_DIRECT", "ALS_C2F"}, f"availability models context {index}")
        check((rows_for_context.uid == int(context["uid"])).all(), f"availability uid context {index}")
        check((rows_for_context.cap == int(context["cap"])).all(), f"availability cap context {index}")
        check((rows_for_context.h == int(context["h"])).all(), f"availability h context {index}")
        check((rows_for_context.target_rows == len(candidates)).all(), f"availability target rows context {index}")
        check((rows_for_context.actual_als_history == int(support_mask[history].sum())).all(), f"availability history support context {index}")

        expected_content_active = {}
        if len(history) == 0:
            expected_content_active = {
                "STRUCTURED_DIRECT": False,
                "E5_DIRECT": False,
                "QWEN_DIRECT": False,
            }
        else:
            context_weights = weights_independent(stars, prior)
            denominator = float(np.abs(context_weights).sum())
            if denominator <= 1e-12:
                expected_content_active = {
                    "STRUCTURED_DIRECT": False,
                    "E5_DIRECT": False,
                    "QWEN_DIRECT": False,
                }
            else:
                normalized_weights = context_weights / denominator
                expected_content_active = {
                    "STRUCTURED_DIRECT": bool(np.linalg.norm(np.asarray(structured[history].T @ normalized_weights).ravel()) > 1e-12),
                    "E5_DIRECT": bool(np.linalg.norm(np.asarray(e5[history], dtype=np.float64).T @ normalized_weights) > 1e-12),
                    "QWEN_DIRECT": bool(np.linalg.norm(np.asarray(qwen[history], dtype=np.float64).T @ normalized_weights) > 1e-12),
                }
        for name in ("STRUCTURED_DIRECT", "E5_DIRECT", "QWEN_DIRECT", "ALS_C2F"):
            column = names.index(name)
            column_state = availability[row_slice, column]
            check(bool(column_state.all()) or bool((~column_state).all()), f"mixed availability {name} context {index}")
            check(bool(column_state.all()) == availability_lookup[(index, name)], f"CSV/bundle availability {name} context {index}")
            if name in expected_content_active:
                check(availability_lookup[(index, name)] == expected_content_active[name], f"content N/A rule {name} context {index}")
        expected_c2f = bool(support_mask[history].sum())
        check(availability_lookup[(index, "ALS_C2F")] == expected_c2f, f"ALS_C2F N/A rule context {index}")

    mapper_resources = selection["resources"]
    score_resources = producer["resources"]
    for name, resource in (("mapper", mapper_resources), ("scoring", score_resources)):
        check(resource["seconds"] <= resource["seconds_limit"], name + " time budget")
        check(resource["process_peak_rss_bytes"] <= resource["host_memory_bytes_limit"], name + " RSS budget")

    result = {
        "schema_version": 1,
        "experiment": "hybrid345",
        "date": date.today().isoformat(),
        "kind": "independent_label_free_prediction_artifact_audit",
        "status": "PASS",
        "claim_scope": cfg["claim_scope"],
        "labels_opened": False,
        "recursive_producer_verification": "PASS",
        "non_label_source_pins_verified": source_pin_count,
        "mapper": {
            "teacher_items": int(len(factor_values)),
            "train_items": int(train.sum()),
            "validation_items": int(validation.sum()),
            "strata": {
                name: {
                    "all": int((strata == code).sum()),
                    "train": int((train & (strata == code)).sum()),
                    "validation": int((validation & (strata == code)).sum()),
                }
                for code, name in enumerate(STRATA_NAMES)
            },
            "selected_alpha": float(independent_selected["alpha"]),
            "held_out": independent_selected["validation"],
            "coefficient_max_absolute_difference": float(np.max(np.abs(final_coefficient - mapper["coefficient"]))),
            "intercept_max_absolute_difference": float(np.max(np.abs(final_intercept - mapper["intercept"]))),
            "mapped_factor_shape": list(mapped.shape),
            "mapped_factor_all_finite": bool(np.isfinite(mapped).all()),
            "mapped_factor_max_absolute_difference": mapped_max_difference,
            "factor_files_verified_against_fit_seal": len(actual_factor_files),
        },
        "predictions": {
            "shape": list(predictions.shape),
            "names": names,
            "availability_equals_finiteness": True,
            "als_exact_reuse": True,
            "fixed_fm_gbt_exact_reuse": sorted(fixed_reuse),
            "independently_recomputed_contexts": len(audit_indices),
            "independent_context_indices": audit_indices,
            "maximum_absolute_differences": max_differences,
            "all_context_na_rules_checked": True,
            "all_availability_csv_metadata_checked": True,
            "context_slices_contiguous": True,
        },
        "resources": {
            "mapper": mapper_resources,
            "scoring": score_resources,
            "within_frozen_limits": True,
        },
        "producer_seals": {
            "prior-seal.json": pin(OUT / "prior-seal.json"),
            "mapper-seal.json": pin(OUT / "mapper-seal.json"),
            "prediction-seal.json": pin(OUT / "prediction-seal.json"),
            "qwen-embedding-seal.json": pin(OUT / "qwen-embedding-seal.json"),
        },
        "producer_implementation": producer["implementation"],
        "producer_prelabel_review": producer["independent_review"],
        "audit_code": pin(Path(__file__)),
        "blocking_findings": [],
        "not_claimed": [
            "Any recommendation-quality result.",
            "Any evaluation-label access.",
            "Any product adoption or deployment decision.",
        ],
    }
    destination = DOC / "prediction-review.json"
    destination.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
