"""Run preregistered GBT profiles sequentially in the pinned local Docker image."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "experiments" / "gbt-zero-n" / "config.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if args.output_root.exists():
        raise FileExistsError(f"output already exists: {args.output_root}")
    prepared = json.loads((args.prepared_root / "manifest.json").read_text(encoding="utf-8"))
    if prepared["status"] != "PASS" or prepared["final_test"] != "NOT_WRITTEN":
        raise RuntimeError("prepared bundle failed or opened FINAL_TEST")
    image_id = subprocess.check_output(
        ["docker", "image", "inspect", config["runtime"]["docker_image"], "--format", "{{.Id}}"], text=True
    ).strip()
    if image_id != config["runtime"]["image_id"]:
        raise RuntimeError("Docker image digest mismatch")
    args.output_root.mkdir(parents=True)
    logs = args.output_root / "logs"
    logs.mkdir()
    for profile in config["profiles"]:
        target = args.output_root / profile
        name = f"s15p21e106-622-{args.input_root_id if hasattr(args, 'input_root_id') else 'local'}-{profile}".lower()
        command = [
            "docker", "run", "--rm", "--name", name, "--network", "none",
            "--hostname", "gbt622", "--add-host", "gbt622:127.0.0.1",
            "-e", "SPARK_LOCAL_IP=127.0.0.1", "--cpus", "4",
            "--memory", config["runtime"]["container_memory"],
            "--memory-swap", config["runtime"]["container_memory"],
            "--mount", f"type=bind,source={ROOT / 'scripts'},target=/scripts,readonly",
            "--mount", f"type=bind,source={args.prepared_root.resolve()},target=/prepared,readonly",
            "--mount", f"type=bind,source={args.output_root.resolve()},target=/output",
            "--mount", f"type=bind,source={CONFIG.parent.resolve()},target=/config,readonly",
            config["runtime"]["docker_image"], "/opt/spark/bin/spark-submit",
            "--master", config["runtime"]["master"],
            "--driver-memory", config["runtime"]["driver_memory"],
            "--conf", "spark.sql.shuffle.partitions=8", "--conf", "spark.ui.enabled=false",
            "/scripts/gbt_zero_n_worker.py", "--prepared-root", "/prepared",
            "--output-root", f"/output/{profile}", "--config", "/config/config.json",
            "--profile", profile,
        ]
        started = time.monotonic()
        with (logs / f"{profile}.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            try:
                code = process.wait(timeout=config["runtime"]["timeout_seconds"])
            except subprocess.TimeoutExpired:
                subprocess.run(["docker", "stop", "--time", "2", name], capture_output=True, timeout=20)
                if process.poll() is None:
                    process.kill()
                raise RuntimeError(f"profile timed out: {profile}")
        if code != 0:
            raise RuntimeError(f"profile failed: {profile}; see {logs / (profile + '.log')}")
        metrics = json.loads((target / "metrics.json").read_text(encoding="utf-8"))
        print(f"{profile}: fit={metrics['fit_seconds']:.3f}s total={time.monotonic()-started:.3f}s", flush=True)
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gbt_zero_n_evaluate.py"),
         "--fits-root", str(args.output_root), "--config", str(CONFIG)],
        check=True,
    )


if __name__ == "__main__":
    main()
