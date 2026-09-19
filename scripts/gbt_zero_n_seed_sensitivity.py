"""Run bounded alternate seeds for the selected GBT profile and summarize stability."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path

import pandas as pd

from gbt_zero_n_evaluate import nested_history_curve


ROOT = Path(__file__).resolve().parents[1]
BASE_CONFIG = ROOT / "experiments" / "gbt-zero-n" / "config.json"
COMPLETION_CONFIG = ROOT / "experiments" / "gbt-zero-n" / "completion-config.json"


def file_pin(path: Path) -> dict[str, int | str]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def summarize(path: Path, seed: int) -> dict:
    frame = pd.read_parquet(path)
    frame["prediction"] = frame.prediction.clip(0.5, 5)
    frame["squared_error"] = (frame.prediction - frame.label) ** 2
    frame["absolute_error"] = (frame.prediction - frame.label).abs()
    user = frame.groupby("uid", sort=True)[["squared_error", "absolute_error"]].mean()
    curve = nested_history_curve(frame, seed)
    return {
        "seed": seed,
        "rows": int(len(frame)),
        "users": int(frame.uid.nunique()),
        "user_macro_mse": float(user.squared_error.mean()),
        "user_macro_mae": float(user.absolute_error.mean()),
        "nested_history_paired_vs_n0": curve,
        "all_n_buckets_non_worse_point_estimate": all(
            row["user_macro_mse_delta_vs_n0"] <= 0 for row in curve
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--base-fits-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    base = json.loads(BASE_CONFIG.read_text(encoding="utf-8"))
    completion = json.loads(COMPLETION_CONFIG.read_text(encoding="utf-8"))
    profile = completion["sensitivity_profile"]
    if args.output_root.exists():
        raise FileExistsError(f"output already exists: {args.output_root}")
    image_id = subprocess.check_output(
        ["docker", "image", "inspect", base["runtime"]["docker_image"], "--format", "{{.Id}}"], text=True
    ).strip()
    if image_id != base["runtime"]["image_id"]:
        raise RuntimeError("Docker image digest mismatch")
    args.output_root.mkdir(parents=True)
    logs = args.output_root / "logs"
    logs.mkdir()
    for seed in completion["sensitivity_seeds"]:
        target = args.output_root / f"seed-{seed}"
        command = [
            "docker", "run", "--rm", "--name", f"s15p21e106-622-seed-{seed}",
            "--network", "none", "--hostname", "gbt622", "--add-host", "gbt622:127.0.0.1",
            "-e", "SPARK_LOCAL_IP=127.0.0.1", "--cpus", "4",
            "--memory", base["runtime"]["container_memory"],
            "--memory-swap", base["runtime"]["container_memory"],
            "--mount", f"type=bind,source={ROOT / 'scripts'},target=/scripts,readonly",
            "--mount", f"type=bind,source={args.prepared_root.resolve()},target=/prepared,readonly",
            "--mount", f"type=bind,source={args.output_root.resolve()},target=/output",
            "--mount", f"type=bind,source={BASE_CONFIG.parent.resolve()},target=/config,readonly",
            base["runtime"]["docker_image"], "/opt/spark/bin/spark-submit",
            "--master", base["runtime"]["master"], "--driver-memory", base["runtime"]["driver_memory"],
            "--conf", "spark.sql.shuffle.partitions=8", "--conf", "spark.ui.enabled=false",
            "/scripts/gbt_zero_n_sensitivity_worker.py", "--prepared-root", "/prepared",
            "--output-root", f"/output/seed-{seed}", "--config", "/config/config.json",
            "--profile", profile, "--seed", str(seed),
        ]
        with (logs / f"seed-{seed}.log").open("w", encoding="utf-8") as log:
            process = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                     timeout=base["runtime"]["timeout_seconds"], check=False)
        if process.returncode != 0:
            raise RuntimeError(f"seed {seed} failed; see {logs / f'seed-{seed}.log'}")

    runs = [summarize(
        args.base_fits_root / profile / "validation-target-predictions.parquet", base["seed"]
    )]
    for seed in completion["sensitivity_seeds"]:
        runs.append(summarize(
            args.output_root / f"seed-{seed}" / "validation-target-predictions.parquet", seed
        ))
    mse_values = [run["user_macro_mse"] for run in runs]
    report = {
        "status": "PASS",
        "profile": profile,
        "seeds": runs,
        "user_macro_mse_range": [min(mse_values), max(mse_values)],
        "user_macro_mse_span": max(mse_values) - min(mse_values),
        "all_seeds_pass_nested_point_estimate_gate": all(
            run["all_n_buckets_non_worse_point_estimate"] for run in runs
        ),
        "prepared_manifest": file_pin(args.prepared_root / "manifest.json"),
        "base_config": file_pin(BASE_CONFIG),
        "completion_config": file_pin(COMPLETION_CONFIG),
        "worker": file_pin(ROOT / "scripts" / "gbt_zero_n_sensitivity_worker.py"),
        "created_unix": time.time(),
        "final_test_opened": False,
    }
    (args.output_root / "sensitivity-report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
