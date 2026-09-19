"""Run the configured pairwise GBT objective in the pinned local image."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE_CONFIG = ROOT / "experiments" / "gbt-zero-n" / "config.json"
COMPLETION_CONFIG = ROOT / "experiments" / "gbt-zero-n" / "completion-config.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    base = json.loads(BASE_CONFIG.read_text(encoding="utf-8"))
    prepared = json.loads((args.prepared_root / "manifest.json").read_text(encoding="utf-8"))
    if prepared["status"] != "PASS" or prepared["final_test"] != "NOT_WRITTEN":
        raise RuntimeError("prepared input failed or opened FINAL_TEST")
    if args.output_root.exists():
        raise FileExistsError(f"output already exists: {args.output_root}")
    image_id = subprocess.check_output(
        ["docker", "image", "inspect", base["runtime"]["docker_image"], "--format", "{{.Id}}"], text=True
    ).strip()
    if image_id != base["runtime"]["image_id"]:
        raise RuntimeError("Docker image digest mismatch")
    args.output_root.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "docker", "run", "--rm", "--name", "s15p21e106-622-pairwise-rank",
        "--network", "none", "--hostname", "gbt622", "--add-host", "gbt622:127.0.0.1",
        "-e", "SPARK_LOCAL_IP=127.0.0.1", "--cpus", "4",
        "--memory", base["runtime"]["container_memory"],
        "--memory-swap", base["runtime"]["container_memory"],
        "--mount", f"type=bind,source={ROOT / 'scripts'},target=/scripts,readonly",
        "--mount", f"type=bind,source={args.prepared_root.resolve()},target=/prepared,readonly",
        "--mount", f"type=bind,source={args.output_root.parent.resolve()},target=/output",
        "--mount", f"type=bind,source={BASE_CONFIG.parent.resolve()},target=/config,readonly",
        base["runtime"]["docker_image"], "/opt/spark/bin/spark-submit",
        "--master", base["runtime"]["master"], "--driver-memory", base["runtime"]["driver_memory"],
        "--conf", "spark.sql.shuffle.partitions=8", "--conf", "spark.ui.enabled=false",
        "/scripts/gbt_zero_n_rank_worker.py", "--prepared-root", "/prepared",
        "--output-root", f"/output/{args.output_root.name}", "--base-config", "/config/config.json",
        "--completion-config", "/config/completion-config.json",
    ]
    log_path = args.output_root.parent / f"{args.output_root.name}.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                 timeout=base["runtime"]["timeout_seconds"], check=False)
    if process.returncode != 0:
        raise RuntimeError(f"pairwise rank run failed; see {log_path}")
    print((args.output_root / "metrics.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
