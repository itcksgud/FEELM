"""Run immutable Spark FM profiles in Docker and retain failure records."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "experiments/fm-zero-n/config.json"


def docker_path(path: Path) -> str:
    return str(path.resolve())


def run(args: argparse.Namespace) -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if args.output_root.exists() and not args.append:
        raise FileExistsError(f"output already exists: {args.output_root}")
    args.output_root.mkdir(parents=True, exist_ok=args.append)
    profiles = args.profiles or config["profiles"]
    unknown = sorted(set(profiles) - set(config["profiles"]))
    if unknown:
        raise RuntimeError(f"unknown profiles: {unknown}")
    image = config["runtime"]["docker_image"]
    image_id = subprocess.run(["docker", "image", "inspect", image, "--format", "{{.Id}}"], check=True, capture_output=True, text=True).stdout.strip()
    if image_id != config["runtime"]["image_id"]:
        raise RuntimeError("Docker image ID differs from the pinned runtime")
    for profile in profiles:
        output = args.output_root / f"{profile}-seed-{args.seed}"
        container_name = f"fm623-{args.seed}-{uuid.uuid4().hex[:10]}"
        command = [
            "docker", "run", "--rm", "--name", container_name, "--network", "none", "--hostname", "fm623", "--add-host", "fm623:127.0.0.1",
            "-e", "SPARK_LOCAL_IP=127.0.0.1", "-e", f"FM_RUNTIME_IMAGE_ID={image_id}",
            "--cpus", "6", "--memory", config["runtime"]["container_memory"],
            "--memory-swap", config["runtime"]["container_memory"],
            "-v", f"{docker_path(ROOT)}:/workspace:ro", "-v", f"{docker_path(args.prepared_root)}:/prepared:ro",
            "-v", f"{docker_path(args.output_root)}:/output", image, "/opt/spark/bin/spark-submit",
            "--master", config["runtime"]["master"], "--driver-memory", config["runtime"]["driver_memory"],
            "--conf", "spark.ui.enabled=false",
            "/workspace/scripts/fm_zero_n_worker.py", "--prepared-root", "/prepared", "--output-root", f"/output/{output.name}",
            "--profile", profile, "--seed", str(args.seed), "--config", "/workspace/experiments/fm-zero-n/config.json",
        ]
        started = time.monotonic()
        log_root = args.output_root / "logs"
        log_root.mkdir(exist_ok=True)
        try:
            completed = subprocess.run(command, capture_output=True, text=True,
                                       timeout=int(config["runtime"]["timeout_seconds_per_profile"]))
            stdout, stderr, returncode, stage = completed.stdout, completed.stderr, completed.returncode, "SPARK_FIT_OR_PREDICT"
        except subprocess.TimeoutExpired as error:
            subprocess.run(["docker", "stop", "--time", "2", container_name], capture_output=True, text=True, timeout=30)
            stdout = error.stdout.decode() if isinstance(error.stdout, bytes) else (error.stdout or "")
            stderr = error.stderr.decode() if isinstance(error.stderr, bytes) else (error.stderr or "")
            returncode, stage = 124, "TIMEOUT"
        (log_root / f"{output.name}.stdout.log").write_text(stdout, encoding="utf-8")
        (log_root / f"{output.name}.stderr.log").write_text(stderr, encoding="utf-8")
        if returncode != 0:
            failure = {
                "status": "FAILED", "profile": profile, "seed": args.seed, "stage": stage,
                "elapsed_seconds": time.monotonic() - started, "returncode": returncode,
                "docker_image": image, "docker_image_id": image_id, "peak_ram_bytes": "NOT_AVAILABLE_AFTER_CONTAINER_EXIT",
                "disk_spill_bytes": "NOT_EXPOSED", "created_at": datetime.now(timezone.utc).isoformat(),
            }
            (log_root / f"{output.name}.failure.json").write_text(json.dumps(failure, indent=2) + "\n", encoding="utf-8")
            raise RuntimeError(f"profile failed: {profile}; see {log_root}")
    runtime = {"docker_image": image, "docker_image_id": image_id, "spark_version": config["runtime"]["spark_version"],
               "gpu_training": False, "profiles": profiles, "seed": args.seed}
    (args.output_root / f"runtime-seed-{args.seed}.json").write_text(json.dumps(runtime, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--profiles", nargs="*")
    parser.add_argument("--append", action="store_true")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
