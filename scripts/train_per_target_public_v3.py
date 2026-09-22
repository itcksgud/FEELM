"""Train v3 GBT/FM with absent-TMDB correction and constrained FM public arm."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd
from scipy import sparse

from public_evidence_v3 import numeric_features_v3, source_shift_calibration
from train_per_target_rating_pilot_v1 import (
    _fm_scores, _user_macro_mae, content_matrix, fit_gbt, load_data,
)
from train_pointwise_rating_pilot_v1 import _adam, _rating_metrics, digest


SEED = 625
NONNEGATIVE = (
    "tmdb_average_known", "tmdb_votes_known", "tmdb_votes_log1p",
    "kobis_known", "kobis_audience_log1p", "kobis_verified",
    "tmdb_eb_mean_0_1", "tmdb_eb_lcb_0_1", "tmdb_reliability",
    "kobis_strength", "either_public_strength",
)
NONPOSITIVE = ("tmdb_eb_uncertainty_0_1",)


def project_public_linear(linear: np.ndarray, names: list[str]) -> None:
    """In-place projection of the public main effects, never personal crosses."""
    positive_index = np.asarray([names.index(name) for name in NONNEGATIVE], np.int64)
    negative_index = np.asarray([names.index(name) for name in NONPOSITIVE], np.int64)
    linear[positive_index] = np.maximum(linear[positive_index], 0)
    linear[negative_index] = np.minimum(linear[negative_index], 0)


def fit_fm_v3(rows: pd.DataFrame, train: np.ndarray, valid: np.ndarray,
              selector: sparse.csr_matrix, bridge: Path, catalog: Path,
              output: Path, *, matrix: np.ndarray, names: list[str],
              axis_sha256: str, epochs: int, batch_size: int, factors: int) -> dict:
    movie_axis, content = content_matrix(bridge, catalog)
    movie_hash = hashlib.sha256(movie_axis.tobytes()).hexdigest()
    if movie_hash != axis_sha256 or selector.shape[1] != len(movie_axis):
        raise ValueError("selector/content axis mismatch")
    ids = rows.movie_id.to_numpy(np.int64)
    positions = np.searchsorted(movie_axis, ids)
    if (positions >= len(movie_axis)).any() or not np.array_equal(movie_axis[positions], ids):
        raise ValueError("candidate movie missing content")
    candidate = content[positions].tocsr()
    profile = (selector @ content).tocsr()
    positive_index = np.asarray([names.index(name) for name in NONNEGATIVE], np.int64)
    negative_index = np.asarray([names.index(name) for name in NONPOSITIVE], np.int64)
    stars = rows.rating.to_numpy(np.float32)
    train_index, valid_index = np.flatnonzero(train), np.flatnonzero(valid)
    rng = np.random.default_rng(SEED)
    linear = np.zeros(matrix.shape[1], np.float32)
    factor = rng.normal(0, .01, (content.shape[1], factors)).astype(np.float32)
    bias = np.asarray([float(stars[train].mean())], np.float32)
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
            residual = bias[0] + matrix[take] @ linear + np.sum(cf * pf, axis=1) - stars[take]
            if not np.isfinite(residual).all():
                raise FloatingPointError("nonfinite FM residual")
            scale = (2.0 / len(take)) * residual
            grad_linear = matrix[take].T @ scale + 2e-4 * linear
            grad_factor = np.asarray(c.T @ (scale[:, None] * pf) +
                                     p.T @ (scale[:, None] * cf)) + 2e-4 * factor
            if not (np.isfinite(grad_linear).all() and np.isfinite(grad_factor).all()):
                raise FloatingPointError("nonfinite FM gradient")
            step += 1
            _adam(bias, np.asarray([float(scale.sum())], np.float32), bias_m, bias_v, step, .01)
            _adam(linear, grad_linear.astype(np.float32), linear_m, linear_v, step, .01)
            _adam(factor, grad_factor.astype(np.float32), factor_m, factor_v, step, .01)
            # Explicit inductive safety bias: exposure evidence cannot be
            # *globally* punished by the linear arm. Personal crosses and
            # candidate/profile factors remain unconstrained.
            project_public_linear(linear, names)
            if not (np.isfinite(bias).all() and np.isfinite(linear).all() and np.isfinite(factor).all()):
                raise FloatingPointError("nonfinite FM parameter")
            mse_sum += float(np.sum(residual ** 2))
        prediction = _fm_scores(matrix[valid_index], candidate[valid_index], profile[valid_index],
                                linear, factor, float(bias[0]))
        mae = _user_macro_mae(rows.loc[valid], prediction)
        trace.append({"epoch": epoch, "train_mse": mse_sum / len(train_index),
                      "valid_user_macro_mae": mae})
        print(json.dumps({"family": "FM", **trace[-1]}), flush=True)
        if mae < best_mae:
            best_mae = mae
            best = (linear.copy(), factor.copy(), float(bias[0]), prediction.copy(), epoch)
    if best is None:
        raise ValueError("no completed FM epoch")
    linear, factor, chosen_bias, prediction, chosen_epoch = best
    if (linear[positive_index] < 0).any() or (linear[negative_index] > 0).any():
        raise ValueError("saved FM violates public evidence sign constraints")
    np.savez_compressed(output / "fm-per-target.npz", linear=linear, factor=factor,
                        bias=np.asarray([chosen_bias], np.float32),
                        feature_names=np.asarray(names), movie_axis_sha256=movie_hash)
    return {
        "family": "FM", "features": names, "epochs": epochs, "best_epoch": chosen_epoch,
        "factor_count": factors, "factor_width": content.shape[1],
        "train_rows": len(train_index), "valid_rows": len(valid_index),
        "trace": trace, "constrained_nonnegative": list(NONNEGATIVE),
        "constrained_nonpositive": list(NONPOSITIVE),
        "validation": _rating_metrics(rows.loc[valid], prediction),
    }


def train(dataset: Path, bridge: Path, catalog: Path, output: Path, *,
          family: str, trees: int = 80, threads: int = 6, epochs: int = 3,
          batch_size: int = 1024, factors: int = 8) -> dict:
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    if family not in {"gbt", "fm"} or min(trees, threads, epochs, batch_size, factors) <= 0:
        raise ValueError("invalid training options")
    started = time.monotonic()
    rows, training, valid, selector, source = load_data(dataset, bridge, catalog)
    catalog_frame = pd.read_parquet(catalog, columns=["tmdb_vote_average", "tmdb_vote_count",
                                                      "kobis_audience_cumulative", "kobis_link_status"])
    calibration, shift = source_shift_calibration(catalog_frame, rows.loc[training])
    bridge_frame = pd.read_parquet(bridge, columns=["movie_id", "tmdb_vote_count"])
    bridge_frame = bridge_frame.sort_values("movie_id", kind="stable")
    axis = bridge_frame.movie_id.to_numpy(np.int64)
    if hashlib.sha256(axis.tobytes()).hexdigest() != source["movie_axis_sha256"]:
        raise ValueError("vote history axis mismatch")
    from public_evidence_v2 import user_vote_context
    context = user_vote_context(selector, bridge_frame.tmdb_vote_count.to_numpy(np.float32))

    def make_features(frame: pd.DataFrame) -> tuple[np.ndarray, list[str]]:
        if frame is not rows:
            raise ValueError("validated full row axis required")
        return numeric_features_v3(frame, calibration, vote_context=context)

    matrix, names = make_features(rows)
    output.mkdir(parents=True, exist_ok=False)
    print(f"FIT_START public_v3 {family.upper()} train={training.sum()} valid={valid.sum()} "
          f"m={calibration.prior_votes:.4f}", flush=True)
    if family == "gbt":
        model = fit_gbt(rows, training, valid, output, trees=trees, threads=threads,
                        history_style=True, feature_builder=make_features)
        filename = "gbt-per-target.json"
    else:
        model = fit_fm_v3(rows, training, valid, selector, bridge, catalog, output,
                          matrix=matrix, names=names, axis_sha256=source["movie_axis_sha256"],
                          epochs=epochs, batch_size=batch_size, factors=factors)
        filename = "fm-per-target.npz"
    result = {
        "experiment": "strict-per-target-public-evidence-v3-exploratory",
        "family": family, "dataset_manifest_sha256": digest(dataset / "manifest.json"),
        "dataset_rows_sha256": source["artifacts"]["rows.parquet"]["sha256"],
        "dataset_selector_sha256": source["artifacts"]["history_selector.npz"]["sha256"],
        "trainer_sha256": digest(Path(__file__).resolve()),
        "public_v3_sha256": digest(Path(__file__).resolve().parent / "public_evidence_v3.py"),
        "public_v2_sha256": digest(Path(__file__).resolve().parent / "public_evidence_v2.py"),
        "base_trainer_sha256": digest(Path(__file__).resolve().parent / "train_per_target_rating_pilot_v1.py"),
        "sources": {"bridge": digest(bridge), "catalog": digest(catalog)},
        "calibration": calibration.to_dict(), "shift_calibration": shift,
        "model_sha256": digest(output / filename),
        "configuration": {"trees": trees, "threads": threads, "epochs": epochs,
                          "batch_size": batch_size, "factors": factors, "seed": SEED},
        "test_split_used": False, "seconds": time.monotonic() - started,
        "model": model,
        "limitations": ["Only actually rated MovieLens targets have labels",
                        "Current public metadata retrospectively attached to historic ratings",
                        "Conservative source shift and FM signs are inductive biases, not verified satisfaction labels"],
    }
    (output / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                          encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "bridge", "catalog", "output_dir"):
        parser.add_argument(f"--{name.replace('_', '-')}", type=Path, required=True)
    parser.add_argument("--family", choices=("gbt", "fm"), required=True)
    parser.add_argument("--trees", type=int, default=80)
    parser.add_argument("--threads", type=int, default=6)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=1024)
    parser.add_argument("--factors", type=int, default=8)
    args = parser.parse_args()
    result = train(args.dataset, args.bridge, args.catalog, args.output_dir,
                   family=args.family, trees=args.trees, threads=args.threads,
                   epochs=args.epochs, batch_size=args.batch_size, factors=args.factors)
    print(json.dumps({"family": args.family, "validation": result["model"]["validation"],
                      "seconds": result["seconds"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
