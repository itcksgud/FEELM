"""Deterministic proof that the custom cross-field FM can recover a non-linear cross effect."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy import sparse

from fm_v2_train import file_pin, fit_fm, fit_linear, initialize_fm, weighted_mse


ROOT = Path(__file__).resolve().parents[1]
CALCULATOR_FILES = ("scripts/fm_v2_synthetic.py", "scripts/fm_v2_train.py")


def calculator_bundle() -> dict:
    files = {name: file_pin(ROOT / name) for name in CALCULATOR_FILES}
    digest = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"algorithm": "ORDERED_FILE_PINS_V1", "files": files, "digest": digest}


def calculate(config_path: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    seed = int(config["model_seed"])
    rng = np.random.default_rng(seed)
    rows = []
    labels = []
    # A candidate/history match effect is impossible for additive one-hot linear terms.
    for repeat in range(80):
        for candidate in range(4):
            for history in range(4):
                rows.append((candidate, history))
                labels.append(3.0 + (0.9 if candidate == history else -0.3) + rng.normal(0.0, 0.02))
    row_index = np.repeat(np.arange(len(rows)), 2)
    column_index = np.fromiter((value for candidate, history in rows for value in (candidate, 4 + history)),
                               dtype=np.int32, count=len(rows) * 2)
    X = sparse.csr_matrix((np.ones(len(rows) * 2), (row_index, column_index)), shape=(len(rows), 8))
    y = np.asarray(labels, dtype=np.float64)
    weight = np.ones(len(rows), dtype=np.float64)
    holdout = np.asarray([(index % 5) == 0 for index in range(len(rows))])
    intercept, linear, solver = fit_linear(X[~holdout], y[~holdout], weight[~holdout], 1e-4)
    linear_mse = weighted_mse(y[holdout], intercept + X[holdout] @ linear, weight[holdout])
    profile = {"candidate_factor_indices": list(range(4)), "positive_factor_indices": list(range(4, 8)),
               "negative_factor_indices": []}
    initial = initialize_fm(intercept, linear, profile, 4, seed)
    optimizer = {"max_epochs": 250, "patience": 40, "beta1": 0.9, "beta2": 0.999,
                 "epsilon": 1e-8, "gradient_clip_norm": 10.0}
    model, trace = fit_fm(X[~holdout], y[~holdout], weight[~holdout], X[holdout], y[holdout], weight[holdout],
                          initial, 0.03, 1e-4, optimizer)
    fm_mse = weighted_mse(y[holdout], model.predict(X[holdout]), weight[holdout])
    relative_mse = fm_mse / linear_mse
    return {
        "schema_version": 1,
        "status": "PASS" if relative_mse <= float(config["fixture_gate"]["synthetic_relative_mse_max"]) else "FAIL",
        "config": file_pin(config_path), "calculator": calculator_bundle(),
        "rows": len(rows), "linear_holdout_mse": linear_mse, "fm_holdout_mse": fm_mse,
        "relative_mse": relative_mse, "best_epoch": trace["best_epoch"],
        "linear_solver": solver, "seed": seed,
    }


def run(output: Path, config_path: Path) -> dict:
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    report = calculate(config_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if report["status"] != "PASS":
        raise RuntimeError("synthetic cross recovery gate failed")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    run(args.output, args.config)


if __name__ == "__main__":
    main()
