"""Same-data objective/label pilot: predict observed MovieLens stars with GBT or FM.

The input is the *same* variable-K observed-candidate table used by the
pairwise ranking pilot. Up to 20 subsequent ratings share a cutoff profile,
so this is not one strict-before-history context per target rating. Nor is it
a full-catalogue or as-of-public validation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd
import xgboost as xgb

from train_evidence_rank_models_v1 import (
    _fm_matrices, _fm_score, _ordered, evaluate, numeric_features,
)


SEED = 625


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def verify_sources(manifest: dict, bridge: Path, catalog: Path) -> None:
    for name, path in (("metadata", bridge), ("catalog", catalog)):
        if digest(path) != manifest["sources"][name]["sha256"]:
            raise ValueError(f"{name} differs from the dataset source pin")


def load_data(dataset: Path, bridge: Path, catalog: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    manifest_path = dataset / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version") != "variable-k-observed-rank-v1-pilot":
        raise ValueError("expected variable-K observed rating pilot")
    for name in ("rows", "contexts"):
        if digest(dataset / f"{name}.parquet") != manifest["artifacts"][name]["sha256"]:
            raise ValueError(f"{name} artifact hash mismatch")
    verify_sources(manifest, bridge, catalog)
    # Read only train and validation rows; the held-out test is not used by fit.
    rows = pd.read_parquet(dataset / "rows.parquet", filters=[("split", "in", ["train", "valid"])])
    train, valid = (_ordered(rows.loc[rows.split.eq(split)].copy()) for split in ("train", "valid"))
    if train.empty or valid.empty:
        raise ValueError("nonempty train and validation required")
    if set(train.uid).intersection(valid.uid) or set(train.qid).intersection(valid.qid):
        raise ValueError("train/validation user or query overlap")
    for part in (train, valid):
        stars = part.rating.to_numpy(np.float32)
        if not np.isfinite(stars).all() or ((stars < .5) | (stars > 5)).any():
            raise ValueError("invalid star label")
    return train, valid, rows, manifest


def _rating_metrics(rows: pd.DataFrame, prediction: np.ndarray) -> dict:
    stars = rows.rating.to_numpy(np.float32)
    prediction = np.asarray(prediction, np.float32)
    if len(stars) != len(prediction) or not np.isfinite(prediction).all():
        raise ValueError("finite prediction required per row")
    clipped = np.clip(prediction, .5, 5)
    error = clipped - stars
    by_user = pd.DataFrame({"uid": rows.uid.to_numpy(), "abs_error": np.abs(error),
                            "squared_error": error ** 2}).groupby("uid", sort=False).mean()
    bins = np.arange(.5, 5.51, .5)
    calibration = []
    for left, right in zip(bins[:-1], bins[1:], strict=True):
        mask = (clipped >= left) & ((clipped < right) if right < 5.5 else (clipped <= right))
        if mask.any():
            calibration.append({"predicted_bin": f"[{left:.1f},{right:.1f}{')' if right < 5.5 else ']'}",
                                "rows": int(mask.sum()), "mean_prediction": float(clipped[mask].mean()),
                                "mean_rating": float(stars[mask].mean())})
    return {
        "rows": len(rows), "users": int(rows.uid.nunique()),
        "metric_prediction": "raw model prediction clipped to [0.5,5.0]",
        "mae": float(np.mean(np.abs(error))), "rmse": float(np.sqrt(np.mean(error ** 2))),
        "user_macro_mae": float(by_user.abs_error.mean()),
        "user_macro_rmse": float(np.sqrt(by_user.squared_error).mean()),
        "raw_out_of_range_fraction": float(((prediction < .5) | (prediction > 5)).mean()),
        "calibration": calibration,
    }


def _slices(rows: pd.DataFrame, prediction: np.ndarray) -> list[dict]:
    clipped = np.clip(np.asarray(prediction, np.float32), .5, 5)
    error = np.abs(clipped - rows.rating.to_numpy(np.float32))
    votes = pd.to_numeric(rows.tmdb_vote_count, errors="coerce")
    flags = {
        "tmdb_votes_missing_or_lt_100_kobis_missing": (votes.isna() | votes.lt(100)) & rows.kobis_audience_cumulative.isna(),
        "public_missing": rows.public_evidence_state.eq("NONE"),
    }
    frame = pd.DataFrame({"uid": rows.uid.to_numpy(), "abs_error": error,
                          "public": rows.public_evidence_state.to_numpy(),
                          "relation": rows.relation_evidence_state.to_numpy(),
                          "k": rows.k.to_numpy()})
    frame["k_bucket"] = pd.cut(frame.k, bins=[0, 1, 4, 9, 19, 49, 99, 199, 499, np.inf],
                               labels=["1", "2-4", "5-9", "10-19", "20-49", "50-99",
                                       "100-199", "200-499", "500+"], include_lowest=True)
    output = []
    for axis in ("public", "relation", "k_bucket"):
        for value, group in frame.groupby(axis, observed=True, sort=True):
            output.append({"axis": axis, "value": str(value), "rows": len(group),
                           "users": int(group.uid.nunique()),
                           "mae": float(group.abs_error.mean()),
                           "user_macro_mae": float(group.groupby("uid").abs_error.mean().mean())})
    for name, mask in flags.items():
        group = frame.loc[np.asarray(mask, bool)]
        output.append({"axis": "risk", "value": name, "rows": len(group),
                       "users": int(group.uid.nunique()),
                       "mae": float(group.abs_error.mean()) if len(group) else None,
                       "user_macro_mae": (float(group.groupby("uid").abs_error.mean().mean())
                                          if len(group) else None)})
    return output


def _evaluate(rows: pd.DataFrame, prediction: np.ndarray) -> dict:
    return {"rating": _rating_metrics(rows, prediction),
            "ranking_prediction": "raw unbounded model score",
            "observed_candidate_ranking": evaluate(rows, prediction),
            "slices": _slices(rows, prediction)}


def _history_mean(valid: pd.DataFrame, contexts: pd.DataFrame, global_mean: float) -> np.ndarray:
    means = {}
    for row in contexts.itertuples(index=False):
        stars = np.asarray(row.history_ratings, np.float32)
        means[int(row.qid)] = float(stars.mean()) if len(stars) else float(global_mean)
    values = valid.qid.map(means).to_numpy(np.float32)
    if not np.isfinite(values).all():
        raise ValueError("validation context missing history mean")
    return values


def fit_gbt(train: pd.DataFrame, valid: pd.DataFrame, output: Path, *, trees: int, threads: int) -> dict:
    xt, names, masks = numeric_features(train)
    xv, names_valid, masks_valid = numeric_features(valid)
    if names != names_valid or not np.array_equal(masks["combined"], masks_valid["combined"]):
        raise ValueError("feature schema changed across splits")
    model = xgb.XGBRegressor(
        objective="reg:squarederror", eval_metric="mae", n_estimators=trees,
        learning_rate=.05, max_depth=4, min_child_weight=10, subsample=.8,
        colsample_bytree=.8, reg_lambda=2, tree_method="hist", random_state=SEED,
        n_jobs=threads,
    )
    model.fit(xt, train.rating.to_numpy(np.float32), eval_set=[(xv, valid.rating.to_numpy(np.float32))],
              verbose=False)
    prediction = model.predict(xv)
    model.save_model(output / "gbt-pointwise.json")
    return {"family": "GBT", "objective": "pointwise-raw-star-squared-error", "trees": trees,
            "features": names, "train_rows": len(train), "train_users": int(train.uid.nunique()),
            "validation": _evaluate(valid, prediction)}


def _adam(parameter: np.ndarray, gradient: np.ndarray, first: np.ndarray,
          second: np.ndarray, step: int, rate: float) -> None:
    first *= .9
    first += .1 * gradient
    second *= .999
    second += .001 * (gradient ** 2)
    parameter -= rate * (first / (1 - .9 ** step)) / (np.sqrt(second / (1 - .999 ** step)) + 1e-8)


def fit_fm(train: pd.DataFrame, valid: pd.DataFrame, contexts: pd.DataFrame,
           metadata: pd.DataFrame, output: Path, *, epochs: int, batch_size: int,
           factors_count: int, learning_rate: float = .01) -> dict:
    xt, names, _ = numeric_features(train)
    xv, names_valid, _ = numeric_features(valid)
    if names != names_valid:
        raise ValueError("feature schema changed across splits")
    train_c, train_p, train_pidx = _fm_matrices(train, contexts, metadata)
    valid_c, valid_p, valid_pidx = _fm_matrices(valid, contexts, metadata)
    if train_c.shape[1] != valid_c.shape[1]:
        raise ValueError("content factor width changed")
    target = train.rating.to_numpy(np.float32)
    rng = np.random.default_rng(SEED)
    linear = np.zeros(xt.shape[1], np.float32)
    factor = rng.normal(0, .01, (train_c.shape[1], factors_count)).astype(np.float32)
    bias = np.asarray([float(target.mean())], np.float32)
    linear_m, linear_v = np.zeros_like(linear), np.zeros_like(linear)
    factor_m, factor_v = np.zeros_like(factor), np.zeros_like(factor)
    bias_m, bias_v = np.zeros_like(bias), np.zeros_like(bias)
    best, best_mae, trace, step = None, np.inf, [], 0
    for epoch in range(1, epochs + 1):
        order = rng.permutation(len(train))
        loss_sum = 0.0
        for offset in range(0, len(order), batch_size):
            indices = order[offset:offset + batch_size]
            c = train_c[indices]
            p = train_p[train_pidx[indices]]
            cf, pf = np.asarray(c @ factor), np.asarray(p @ factor)
            prediction = bias[0] + xt[indices] @ linear + np.sum(cf * pf, axis=1)
            residual = prediction - target[indices]
            if not np.isfinite(residual).all():
                raise FloatingPointError(f"FM nonfinite residual at epoch {epoch}, offset {offset}")
            scale = (2.0 / len(indices)) * residual
            grad_linear = xt[indices].T @ scale + 2e-4 * linear
            grad_factor = np.asarray(c.T @ (scale[:, None] * pf) +
                                     p.T @ (scale[:, None] * cf)) + 2e-4 * factor
            if not (np.isfinite(grad_linear).all() and np.isfinite(grad_factor).all()
                    and np.isfinite(scale).all()):
                raise FloatingPointError(f"FM nonfinite gradient at epoch {epoch}, offset {offset}")
            step += 1
            _adam(bias, np.asarray([float(scale.sum())], np.float32), bias_m, bias_v, step, learning_rate)
            _adam(linear, grad_linear.astype(np.float32), linear_m, linear_v, step, learning_rate)
            _adam(factor, grad_factor.astype(np.float32), factor_m, factor_v, step, learning_rate)
            if not (np.isfinite(bias).all() and np.isfinite(linear).all() and np.isfinite(factor).all()):
                raise FloatingPointError(f"FM nonfinite parameter at epoch {epoch}, offset {offset}")
            loss_sum += float(np.sum(residual ** 2))
        valid_prediction = bias[0] + _fm_score(xv, valid_c, valid_p, valid_pidx, linear, factor)
        validation = _evaluate(valid, valid_prediction)
        mae = validation["rating"]["user_macro_mae"]
        trace.append({"epoch": epoch, "train_mse": loss_sum / len(train),
                      "valid_user_macro_mae": mae,
                      "valid_observed_ndcg_at_10": validation["observed_candidate_ranking"]["user_macro_observed_ndcg_at_10"]})
        print(json.dumps({"family": "FM", **trace[-1]}), flush=True)
        if mae < best_mae:
            best_mae = mae
            best = (linear.copy(), factor.copy(), float(bias[0]), validation, epoch)
    if best is None:
        raise ValueError("no FM epoch completed")
    linear, factor, selected_bias, validation, best_epoch = best
    np.savez_compressed(output / "fm-pointwise.npz", linear=linear, factor=factor,
                        bias=np.asarray([selected_bias], np.float32),
                        feature_names=np.asarray(names))
    return {"family": "FM", "objective": "pointwise-raw-star-squared-error",
            "epochs": epochs, "best_epoch": best_epoch, "factor_count": factors_count,
            "factor_width": train_c.shape[1], "train_rows": len(train),
            "train_users": int(train.uid.nunique()), "trace": trace, "validation": validation}


def train(dataset: Path, bridge: Path, catalog: Path, output: Path, *,
          family: str, trees: int = 40, threads: int = 6, epochs: int = 2,
          batch_size: int = 1024, factors: int = 8) -> dict:
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    if family not in {"gbt", "fm"} or min(trees, threads, epochs, batch_size, factors) <= 0:
        raise ValueError("invalid pilot configuration")
    train_rows, valid_rows, _, manifest = load_data(dataset, bridge, catalog)
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    contexts = pd.read_parquet(dataset / "contexts.parquet")
    train_mean = float(train_rows.rating.mean())
    baseline = _evaluate(valid_rows, np.full(len(valid_rows), train_mean, np.float32))
    history_baseline = _evaluate(valid_rows, _history_mean(valid_rows, contexts, train_mean))
    print(f"FIT_START {family.upper()} pointwise train_rows={len(train_rows)} valid_rows={len(valid_rows)}", flush=True)
    if family == "gbt":
        model = fit_gbt(train_rows, valid_rows, output, trees=trees, threads=threads)
    else:
        metadata = pd.read_parquet(bridge)
        extra = pd.read_parquet(catalog, columns=["tmdb_id", "original_language", "production_company_ids"])
        metadata = metadata.merge(extra, on="tmdb_id", how="left", validate="one_to_one")
        model = fit_fm(train_rows, valid_rows, contexts, metadata, output,
                       epochs=epochs, batch_size=batch_size, factors_count=factors)
    report = {
        "experiment": "variable-k-same-input-pointwise-objective-pilot-v1",
        "family": family, "dataset_manifest_sha256": digest(dataset / "manifest.json"),
        "dataset_rows_sha256": manifest["artifacts"]["rows"]["sha256"],
        "trainer_sha256": digest(Path(__file__).resolve()),
        "feature_code_sha256": digest(Path(__file__).resolve().parent / "train_evidence_rank_models_v1.py"),
        "bridge_sha256": digest(bridge), "catalog_sha256": digest(catalog),
        "content_code_sha256": digest(Path(__file__).resolve().parent / "model_specific_features_v5.py"),
        "rating_semantics_code_sha256": digest(Path(__file__).resolve().parent / "rating_semantics_v7.py"),
        "training_config": {"family": family, "trees": trees, "threads": threads,
                            "epochs": epochs, "batch_size": batch_size,
                            "factors": factors, "seed": SEED, "fm_learning_rate": .01},
        "saved_model_sha256": digest(output / ("gbt-pointwise.json" if family == "gbt" else "fm-pointwise.npz")),
        "test_split_used": False, "seconds": time.monotonic() - started,
        "baseline_global_train_mean": baseline,
        "baseline_cutoff_history_mean": history_baseline,
        "model": model,
        "limitations": ["Selected variable-K cutoffs, not all 31.9M rating targets",
                        "Only observed future ratings have labels; no exposure propensity",
                        "Current public/content snapshot may postdate ratings",
                        "Observed-candidate ranking does not validate full-catalog Top-10"],
    }
    (output / "metrics.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                          encoding="utf-8")
    print(json.dumps({"family": family, "validation_rating": model["validation"]["rating"],
                      "validation_ranking": model["validation"]["observed_candidate_ranking"],
                      "seconds": report["seconds"]}, ensure_ascii=False), flush=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--bridge", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--family", choices=("gbt", "fm"), required=True)
    parser.add_argument("--trees", type=int, default=40)
    parser.add_argument("--threads", type=int, default=6)
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=1024)
    parser.add_argument("--factors", type=int, default=8)
    args = parser.parse_args()
    train(args.dataset, args.bridge, args.catalog, args.output_dir,
          family=args.family, trees=args.trees, threads=args.threads,
          epochs=args.epochs, batch_size=args.batch_size, factors=args.factors)


if __name__ == "__main__":
    main()
