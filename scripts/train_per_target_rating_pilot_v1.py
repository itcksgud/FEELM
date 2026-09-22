"""Fit GBT/FM star regressors on exact per-target strict-past histories.

This is a user/target-sampled desktop pilot from 31.9M verified ML32 ratings.
It uses current TMDB/KOBIS metadata retrospectively, never unrated negatives.
Disjoint test rows are read for alignment checks and content construction,
but their labels are not used for fitting, model selection, or metrics here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time
from typing import Callable

import numpy as np
import pandas as pd
from scipy import sparse
import xgboost as xgb

from model_specific_features_v5 import FACTOR_FIELD_BUCKETS, build_sparse_content
from train_evidence_rank_models_v1 import numeric_features
from train_pointwise_rating_pilot_v1 import _adam, _rating_metrics, digest


SEED = 625
STYLE_NAMES = ("history_rating_mean", "history_rating_std",
               "history_positive_share", "history_negative_share")


def features(rows: pd.DataFrame, *, history_style: bool) -> tuple[np.ndarray, list[str]]:
    matrix, names, _ = numeric_features(rows)
    if history_style:
        extra = rows.loc[:, STYLE_NAMES].to_numpy(np.float32)
        if not np.isfinite(extra).all():
            raise ValueError("strict-past history style features must be finite")
        matrix = np.column_stack((matrix, extra)).astype(np.float32)
        names = [*names, *STYLE_NAMES]
    return matrix, names


def _tokens(value) -> list[str]:
    if value is None or value is pd.NA or (isinstance(value, float) and np.isnan(value)):
        return []
    if isinstance(value, (list, tuple, set, np.ndarray)):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def content_matrix(bridge: Path, catalog: Path) -> tuple[np.ndarray, sparse.csr_matrix]:
    metadata = pd.read_parquet(bridge).sort_values("movie_id", kind="stable").reset_index(drop=True)
    extra = pd.read_parquet(catalog, columns=["tmdb_id", "original_language", "production_company_ids"])
    metadata = metadata.merge(extra, on="tmdb_id", how="left", validate="one_to_one")
    joint_values = []
    for row in metadata.itertuples(index=False):
        record = row._asdict()
        countries = _tokens(record.get("origin_country_codes")) or _tokens(record.get("production_country_codes"))
        genres = _tokens(record.get("genre_ids"))
        joint_values.append([f"{country}|{genre}" for country in countries for genre in genres])
    metadata["joint_country_genre_tokens"] = joint_values
    content = build_sparse_content(metadata, fields=FACTOR_FIELD_BUCKETS +
                                   (("country_genre", "joint_country_genre_tokens", 2048),))
    return metadata.movie_id.to_numpy(np.int64), content


def load_data(dataset: Path, bridge: Path, catalog: Path):
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("version") != "ml32-strict-per-target-star-pilot-v1":
        raise ValueError("expected strict per-target pilot")
    for name, path in (("metadata", bridge), ("catalog", catalog)):
        if digest(path) != manifest["sources"][name]["sha256"]:
            raise ValueError(f"source pin mismatch: {name}")
    for name in ("rows.parquet", "history_selector.npz"):
        if digest(dataset / name) != manifest["artifacts"][name]["sha256"]:
            raise ValueError(f"artifact pin mismatch: {name}")
    rows = pd.read_parquet(dataset / "rows.parquet")
    if not np.array_equal(rows.selector_row.to_numpy(np.int64), np.arange(len(rows))):
        raise ValueError("row/selector alignment mismatch")
    train = rows.split.eq("train").to_numpy()
    valid = rows.split.eq("valid").to_numpy()
    test = rows.split.eq("test").to_numpy()
    if not train.any() or not valid.any():
        raise ValueError("train and validation targets required")
    if not (train | valid | test).all():
        raise ValueError("unknown split")
    uid_split = rows.groupby("uid").split.nunique()
    if not uid_split.eq(1).all():
        raise ValueError("user appears in multiple splits")
    if not (rows.candidate_timestamp > rows.history_cutoff).all():
        raise ValueError("target not strictly later than profile")
    stars = rows.rating.to_numpy(np.float32)
    if not np.isfinite(stars).all() or ((stars < .5) | (stars > 5)).any():
        raise ValueError("invalid rating scale")
    selector = sparse.load_npz(dataset / "history_selector.npz")
    if selector.shape[0] != len(rows):
        raise ValueError("selector row count mismatch")
    if not np.array_equal(np.diff(selector.indptr), rows.k.to_numpy(np.int64)):
        raise ValueError("selector history length differs from K")
    return rows, train, valid, selector, manifest


def _user_macro_mae(rows: pd.DataFrame, prediction: np.ndarray) -> float:
    error = np.abs(np.clip(prediction, .5, 5) - rows.rating.to_numpy(np.float32))
    return float(pd.DataFrame({"uid": rows.uid.to_numpy(), "error": error}).groupby("uid").error.mean().mean())


def fit_gbt(rows: pd.DataFrame, train: np.ndarray, valid: np.ndarray,
            output: Path, *, trees: int, threads: int, history_style: bool,
            feature_builder: Callable[[pd.DataFrame], tuple[np.ndarray, list[str]]] | None = None) -> dict:
    matrix, names = (feature_builder(rows) if feature_builder is not None
                     else features(rows, history_style=history_style))
    stars = rows.rating.to_numpy(np.float32)
    model = xgb.XGBRegressor(
        objective="reg:squarederror", eval_metric="mae", n_estimators=trees,
        learning_rate=.05, max_depth=4, min_child_weight=10, subsample=.8,
        colsample_bytree=.8, reg_lambda=2, tree_method="hist", random_state=SEED,
        n_jobs=threads,
    )
    model.fit(matrix[train], stars[train], eval_set=[(matrix[valid], stars[valid])], verbose=False)
    prediction = model.predict(matrix[valid])
    model.save_model(output / "gbt-per-target.json")
    return {"family": "GBT", "features": names, "trees": trees,
            "train_rows": int(train.sum()), "valid_rows": int(valid.sum()),
            "validation": _rating_metrics(rows.loc[valid], prediction)}


def _fm_scores(x: np.ndarray, candidate: sparse.csr_matrix,
               profile: sparse.csr_matrix, linear: np.ndarray, factor: np.ndarray,
               bias: float, batch_size: int = 4096) -> np.ndarray:
    result = np.empty(len(x), np.float32)
    for start in range(0, len(x), batch_size):
        stop = min(start + batch_size, len(x))
        cf = np.asarray(candidate[start:stop] @ factor)
        pf = np.asarray(profile[start:stop] @ factor)
        result[start:stop] = bias + x[start:stop] @ linear + np.sum(cf * pf, axis=1)
    return result


def fit_fm(rows: pd.DataFrame, train: np.ndarray, valid: np.ndarray,
           selector: sparse.csr_matrix, bridge: Path, catalog: Path,
           output: Path, *, epochs: int, batch_size: int, factors: int,
           history_style: bool, expected_axis_sha256: str,
           feature_builder: Callable[[pd.DataFrame], tuple[np.ndarray, list[str]]] | None = None) -> dict:
    movie_axis, content = content_matrix(bridge, catalog)
    movie_hash = hashlib.sha256(movie_axis.tobytes()).hexdigest()
    if movie_hash != expected_axis_sha256:
        raise ValueError("selector movie axis identity mismatch")
    if selector.shape[1] != len(movie_axis):
        raise ValueError("selector movie axis width mismatch")
    ids = rows.movie_id.to_numpy(np.int64)
    positions = np.searchsorted(movie_axis, ids)
    if (positions >= len(movie_axis)).any() or not np.array_equal(movie_axis[positions], ids):
        raise ValueError("candidate movie missing content")
    candidate = content[positions].tocsr()
    # The selector represents every strict-past rating, normalized with its
    # signed utility; this sparse product avoids materializing repeated lists.
    profile = (selector @ content).tocsr()
    x, names = (feature_builder(rows) if feature_builder is not None
                else features(rows, history_style=history_style))
    y = rows.rating.to_numpy(np.float32)
    train_index = np.flatnonzero(train)
    valid_index = np.flatnonzero(valid)
    rng = np.random.default_rng(SEED)
    linear = np.zeros(x.shape[1], np.float32)
    factor = rng.normal(0, .01, (content.shape[1], factors)).astype(np.float32)
    bias = np.asarray([float(y[train].mean())], np.float32)
    linear_m, linear_v = np.zeros_like(linear), np.zeros_like(linear)
    factor_m, factor_v = np.zeros_like(factor), np.zeros_like(factor)
    bias_m, bias_v = np.zeros_like(bias), np.zeros_like(bias)
    best, best_mae, trace, step = None, np.inf, [], 0
    for epoch in range(1, epochs + 1):
        order = rng.permutation(train_index)
        mse_sum = 0.0
        for offset in range(0, len(order), batch_size):
            take = order[offset:offset + batch_size]
            c, p = candidate[take], profile[take]
            cf, pf = np.asarray(c @ factor), np.asarray(p @ factor)
            residual = bias[0] + x[take] @ linear + np.sum(cf * pf, axis=1) - y[take]
            if not np.isfinite(residual).all():
                raise FloatingPointError("nonfinite FM residual")
            scale = (2.0 / len(take)) * residual
            grad_linear = x[take].T @ scale + 2e-4 * linear
            grad_factor = np.asarray(c.T @ (scale[:, None] * pf) +
                                     p.T @ (scale[:, None] * cf)) + 2e-4 * factor
            if not (np.isfinite(grad_linear).all() and np.isfinite(grad_factor).all()):
                raise FloatingPointError("nonfinite FM gradient")
            step += 1
            _adam(bias, np.asarray([float(scale.sum())], np.float32), bias_m, bias_v, step, .01)
            _adam(linear, grad_linear.astype(np.float32), linear_m, linear_v, step, .01)
            _adam(factor, grad_factor.astype(np.float32), factor_m, factor_v, step, .01)
            if not (np.isfinite(bias).all() and np.isfinite(linear).all() and np.isfinite(factor).all()):
                raise FloatingPointError("nonfinite FM parameter")
            mse_sum += float(np.sum(residual ** 2))
        prediction = _fm_scores(x[valid_index], candidate[valid_index], profile[valid_index],
                                linear, factor, float(bias[0]))
        mae = _user_macro_mae(rows.loc[valid], prediction)
        trace.append({"epoch": epoch, "train_mse": mse_sum / len(train_index),
                      "valid_user_macro_mae": mae})
        print(json.dumps({"family": "FM", **trace[-1]}), flush=True)
        if mae < best_mae:
            best_mae = mae
            best = (linear.copy(), factor.copy(), float(bias[0]), prediction.copy(), epoch)
    if best is None:
        raise ValueError("FM fit did not complete an epoch")
    linear, factor, chosen_bias, prediction, chosen_epoch = best
    np.savez_compressed(output / "fm-per-target.npz", linear=linear, factor=factor,
                        bias=np.asarray([chosen_bias], np.float32),
                        feature_names=np.asarray(names), movie_axis_sha256=movie_hash)
    return {"family": "FM", "features": names, "epochs": epochs,
            "best_epoch": chosen_epoch, "factor_count": factors,
            "factor_width": content.shape[1], "train_rows": len(train_index),
            "valid_rows": len(valid_index), "trace": trace,
            "validation": _rating_metrics(rows.loc[valid], prediction)}


def train_model(dataset: Path, bridge: Path, catalog: Path, output: Path, *,
                family: str, history_style: bool = True, trees: int = 80,
                threads: int = 6, epochs: int = 3, batch_size: int = 1024,
                factors: int = 8) -> dict:
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    if family not in {"gbt", "fm"} or min(trees, threads, epochs, batch_size, factors) <= 0:
        raise ValueError("invalid model configuration")
    rows, train, valid, selector, manifest = load_data(dataset, bridge, catalog)
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    print(f"FIT_START {family.upper()} per_target train={train.sum()} valid={valid.sum()} style={history_style}", flush=True)
    global_prediction = np.full(int(valid.sum()), rows.rating[train].mean(), np.float32)
    history_prediction = rows.loc[valid, "history_rating_mean"].to_numpy(np.float32)
    baseline = {"global_train_mean": _rating_metrics(rows.loc[valid], global_prediction),
                "strict_past_user_mean": _rating_metrics(rows.loc[valid], history_prediction)}
    if family == "gbt":
        model = fit_gbt(rows, train, valid, output, trees=trees, threads=threads,
                        history_style=history_style)
        filename = "gbt-per-target.json"
    else:
        model = fit_fm(rows, train, valid, selector, bridge, catalog, output,
                       epochs=epochs, batch_size=batch_size, factors=factors,
                       history_style=history_style,
                       expected_axis_sha256=manifest["movie_axis_sha256"])
        filename = "fm-per-target.npz"
    result = {
        "experiment": "strict-per-target-raw-star-pilot-v1", "family": family,
        "dataset_manifest_sha256": digest(dataset / "manifest.json"),
        "dataset_rows_sha256": manifest["artifacts"]["rows.parquet"]["sha256"],
        "dataset_selector_sha256": manifest["artifacts"]["history_selector.npz"]["sha256"],
        "trainer_sha256": digest(Path(__file__).resolve()),
        "numeric_code_sha256": digest(Path(__file__).resolve().parent / "train_evidence_rank_models_v1.py"),
        "content_code_sha256": digest(Path(__file__).resolve().parent / "model_specific_features_v5.py"),
        "rating_semantics_code_sha256": digest(Path(__file__).resolve().parent / "rating_semantics_v7.py"),
        "model_sha256": digest(output / filename),
        "training_config": {"family": family, "history_style": history_style,
                            "trees": trees, "threads": threads, "epochs": epochs,
                            "batch_size": batch_size, "factors": factors, "seed": SEED},
        "test_split_used": False, "seconds": time.monotonic() - started,
        "baselines": baseline, "model": model,
        "limitations": ["Target-sampled from 31.9M, not all ratings fitted",
                        "Current public metadata may postdate historical ratings",
                        "Only actual rated movies have labels; test fixed-cutoff ranking separately"],
    }
    (output / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                          encoding="utf-8")
    print(json.dumps({"family": family, "validation": model["validation"],
                      "seconds": result["seconds"]}, ensure_ascii=False), flush=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--bridge", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--family", choices=("gbt", "fm"), required=True)
    parser.add_argument("--no-history-style", action="store_true")
    parser.add_argument("--trees", type=int, default=80)
    parser.add_argument("--threads", type=int, default=6)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=1024)
    parser.add_argument("--factors", type=int, default=8)
    args = parser.parse_args()
    train_model(args.dataset, args.bridge, args.catalog, args.output_dir,
                family=args.family, history_style=not args.no_history_style,
                trees=args.trees, threads=args.threads, epochs=args.epochs,
                batch_size=args.batch_size, factors=args.factors)


if __name__ == "__main__":
    main()
