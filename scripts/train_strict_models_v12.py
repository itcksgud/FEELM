"""Retrain weighted GBT/FM with identical r3e direct/public training and serving features."""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd
from scipy import sparse
import xgboost as xgb

from public_evidence_v2 import PublicCalibration, user_vote_context
from public_evidence_v4 import build_cohort_support, numeric_features_v4
from strict_evidence_v12 import augment
from train_per_target_public_v3 import project_public_linear
from train_per_target_rating_pilot_v1 import content_matrix, load_data, _fm_scores, _user_macro_mae
from train_pointwise_rating_pilot_v1 import _adam, _rating_metrics, digest


def weighted_fm(rows, train, valid, matrix, names, selector, content, positions, weights,
                output, axis_sha, epochs=3, factors=8, batch_size=1024):
    candidate = content[positions].tocsr()
    profile = (selector @ content).tocsr()
    y = rows.rating.to_numpy(np.float32)
    ti, vi = np.flatnonzero(train), np.flatnonzero(valid)
    rng = np.random.default_rng(625)
    linear = np.zeros(matrix.shape[1], np.float32)
    factor = rng.normal(0, .01, (content.shape[1], factors)).astype(np.float32)
    bias = np.asarray([np.average(y[train], weights=weights[train])], np.float32)
    state = [(np.zeros_like(v), np.zeros_like(v)) for v in (bias, linear, factor)]
    step, best, trace = 0, None, []
    for epoch in range(1, epochs + 1):
        order = rng.permutation(ti)
        for start in range(0, len(order), batch_size):
            take = order[start:start + batch_size]
            c, p = candidate[take], profile[take]
            cf, pf = np.asarray(c @ factor), np.asarray(p @ factor)
            residual = bias[0] + matrix[take] @ linear + np.sum(cf * pf, axis=1) - y[take]
            # Globally mean-normalized inverse selection weights preserve the
            # same weighted objective across minibatches (no per-batch reweight).
            scale = 2 * weights[take] * residual / len(take)
            gradients = [np.asarray([scale.sum()], np.float32),
                         matrix[take].T @ scale + 2e-4 * linear,
                         np.asarray(c.T @ (scale[:, None] * pf) + p.T @ (scale[:, None] * cf)) + 2e-4 * factor]
            if not all(np.isfinite(g).all() for g in gradients):
                raise FloatingPointError("nonfinite weighted FM gradient")
            step += 1
            for parameter, gradient, (m, v) in zip((bias, linear, factor), gradients, state):
                _adam(parameter, gradient.astype(np.float32), m, v, step, .01)
            project_public_linear(linear, names)
        prediction = _fm_scores(matrix[vi], candidate[vi], profile[vi], linear, factor, float(bias[0]))
        mae = _user_macro_mae(rows.loc[valid], prediction)
        trace.append({"epoch": epoch, "valid_user_macro_mae": mae})
        print(json.dumps({"family": "fm", **trace[-1]}), flush=True)
        if best is None or mae < best[0]:
            best = (mae, linear.copy(), factor.copy(), float(bias[0]), prediction.copy(), epoch)
    _, linear, factor, chosen_bias, prediction, epoch = best
    np.savez_compressed(output / "fm-per-target.npz", linear=linear, factor=factor,
        bias=np.asarray([chosen_bias], np.float32), feature_names=np.asarray(names), movie_axis_sha256=axis_sha)
    return prediction, {"trace": trace, "best_epoch": epoch, "factor_width": content.shape[1], "factor_count": factors}, candidate, profile


def paired_mae(rows, new, old):
    data = pd.DataFrame({"uid": rows.uid.to_numpy(),
        "delta": np.abs(np.clip(new, .5, 5) - rows.rating.to_numpy()) - np.abs(np.clip(old, .5, 5) - rows.rating.to_numpy())})
    delta = data.groupby("uid").delta.mean().to_numpy()
    rng = np.random.default_rng(625)
    values = np.asarray([rng.choice(delta, len(delta), replace=True).mean() for _ in range(1000)])
    return {"new_minus_v9_user_mae": float(delta.mean()), "user_bootstrap_95": np.quantile(values, [.025, .975]).tolist(),
            "scope": "previously viewed development users; not fresh holdout evidence"}


def train(dataset: Path, bridge: Path, catalog: Path, references: Path, baseline: Path, output: Path):
    if output.exists():
        raise FileExistsError(output)
    started = time.monotonic()
    rows, training, valid, selector, source = load_data(dataset, bridge, catalog)
    if source.get("variant") != "v12-public-route-direct-stratified-observed" or rows.split.eq("test").any():
        raise ValueError("wrong or contaminated v12 training dataset")
    if digest(references) != source["public_references"]["sha256"] or digest(dataset / "strict_direct.npy") != source["strict_direct_sha256"]:
        raise ValueError("strict feature/public source mismatch")
    direct = np.load(dataset / "strict_direct.npy", allow_pickle=False)
    refs = pd.read_parquet(references).set_index("tmdb_id")
    cf = pd.read_parquet(catalog)
    bf = pd.read_parquet(bridge).sort_values("movie_id", kind="stable")
    rows["tmdb_id"] = rows.movie_id.map(bf.set_index("movie_id").tmdb_id)
    if rows.tmdb_id.isna().any():
        raise ValueError("training target missing exact TMDB bridge")
    public = refs.loc[rows.tmdb_id].reset_index(drop=True)
    weights = 1 / rows.sampling_probability.to_numpy(np.float32)
    weights /= float(weights[training].mean())
    calibration = PublicCalibration.from_catalog(cf)
    counts = rows.tmdb_vote_count.to_numpy(float)
    observed = training & np.isfinite(counts) & (counts > 0)
    order = np.argsort(counts[observed], kind="stable")
    observed_weights = weights[observed][order]
    median = counts[observed][order][np.searchsorted(np.cumsum(observed_weights), observed_weights.sum() / 2)]
    calibration = replace(calibration, prior_votes=float(np.sqrt(calibration.prior_votes * median)))
    context = user_vote_context(selector, bf.tmdb_vote_count.to_numpy(np.float32))
    tmdb_axis, support, _ = build_cohort_support(cf)
    base, base_names = numeric_features_v4(rows, calibration, vote_context=context, tmdb_axis=tmdb_axis, cohort_support=support)
    matrix, names = augment(base, base_names, direct, public)
    movie_axis, content = content_matrix(bridge, catalog)
    if hashlib.sha256(movie_axis.tobytes()).hexdigest() != source["movie_axis_sha256"]:
        raise ValueError("training selector/content movie axis mismatch")
    positions = np.searchsorted(movie_axis, rows.movie_id.to_numpy(np.int64))
    if not np.array_equal(movie_axis[positions], rows.movie_id.to_numpy(np.int64)):
        raise ValueError("FM target identity mismatch")
    output.mkdir(parents=True)
    np.save(output / "valid_matrix.npy", matrix[valid], allow_pickle=False)
    reports = {}
    model = xgb.XGBRegressor(objective="reg:squarederror", eval_metric="mae", n_estimators=80,
        learning_rate=.05, max_depth=4, min_child_weight=10, subsample=.8, colsample_bytree=.8,
        reg_lambda=2, tree_method="hist", random_state=625, n_jobs=6)
    model.fit(matrix[training], rows.rating.to_numpy()[training], sample_weight=weights[training],
              eval_set=[(matrix[valid], rows.rating.to_numpy()[valid])], verbose=False)
    model.save_model(output / "gbt-per-target.json")
    predictions = {"gbt": model.predict(matrix[valid])}
    print(json.dumps({"family": "gbt", "validation": _rating_metrics(rows.loc[valid], predictions["gbt"])}), flush=True)
    predictions["fm"], fm_config, candidate, profile = weighted_fm(rows, training, valid, matrix, names, selector,
        content, positions, weights, output, source["movie_axis_sha256"])
    old_predictions = {}
    for family in ("gbt", "fm"):
        old_dir = baseline / f"ml32-per-target-public-v9-kr-cohort-{family}-30000-20260922"
        old_metrics = json.loads((old_dir / "metrics.json").read_text(encoding="utf-8"))
        filename = "gbt-per-target.json" if family == "gbt" else "fm-per-target.npz"
        if (digest(old_dir / filename) != old_metrics["model_sha256"]
                or old_metrics["sources"]["catalog"] != digest(catalog)
                or old_metrics["sources"]["bridge"] != digest(bridge)):
            raise ValueError("frozen v9 baseline mismatch")
        old_cal = PublicCalibration.from_dict(old_metrics["calibration"])
        old_matrix, old_names = numeric_features_v4(rows.loc[valid].copy(), old_cal,
            vote_context={name: value[valid] for name, value in context.items()}, tmdb_axis=tmdb_axis, cohort_support=support)
        if old_names != old_metrics["model"]["features"]:
            raise ValueError("v9 baseline ordered feature schema mismatch")
        if family == "gbt":
            old_model = xgb.Booster()
            old_model.load_model(old_dir / filename)
            old_predictions[family] = old_model.predict(xgb.DMatrix(old_matrix))
        else:
            with np.load(old_dir / filename) as state:
                if str(state["movie_axis_sha256"]) != source["movie_axis_sha256"]:
                    raise ValueError("v9 FM movie axis mismatch")
                if old_names != list(state["feature_names"]):
                    raise ValueError("v9 FM feature schema differs")
                old_predictions[family] = _fm_scores(old_matrix, candidate[valid], profile[valid],
                    state["linear"], state["factor"], float(state["bias"][0]))
        reports[family] = {"validation": _rating_metrics(rows.loc[valid], predictions[family]),
            "baseline_v9_same_targets": _rating_metrics(rows.loc[valid], old_predictions[family]),
            "paired_development_comparison": paired_mae(rows.loc[valid], predictions[family], old_predictions[family]),
            "model_sha256": digest(output / filename)}
    values = rows.loc[valid, ["uid", "movie_id", "tmdb_id", "rating", "k", "candidate_timestamp", "sampling_probability"]].copy()
    for family in predictions:
        values[family] = predictions[family]
        values[f"v9_{family}"] = old_predictions[family]
    values["strict_positive_units"] = direct[valid, 5]
    values["strict_negative_units"] = direct[valid, 12]
    values["required"] = public.required.to_numpy()[valid]
    values.to_parquet(output / "valid_predictions.parquet", index=False)
    report = {"experiment": "v12-weighted-observed-strict-direct", "features": names,
        "models": reports, "fm": fm_config, "calibration": calibration.to_dict(),
        "weighted_train_vote_median": float(median), "train_rows": int(training.sum()), "valid_rows": int(valid.sum()),
        "movie_axis_sha256": source["movie_axis_sha256"],
        "configuration": {"gbt_trees": 80, "depth": 4, "fm_epochs": 3, "fm_factors": 8, "seed": 625},
        "dataset_manifest_sha256": digest(dataset / "manifest.json"), "sources": {"bridge": digest(bridge), "catalog": digest(catalog)},
        "public_reference_sha256": digest(references), "trainer_sha256": digest(Path(__file__)),
        "evidence_code_sha256": digest(Path(__file__).parent / "strict_evidence_v12.py"),
        "test_split_used": False, "new_holdout_used": False, "seconds": time.monotonic() - started}
    (output / "metrics.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("dataset", "bridge", "catalog", "references", "baseline", "output"):
        parser.add_argument(f"--{arg}", type=Path, required=True)
    a = parser.parse_args()
    report = train(a.dataset, a.bridge, a.catalog, a.references, a.baseline, a.output)
    print(json.dumps({"train_rows": report["train_rows"], "valid_rows": report["valid_rows"], "seconds": report["seconds"]}), flush=True)
