"""Run the pinned Docker FM-v3 full-eligible fit and retain immutable logs."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "experiments/fm-v3/config.json"


def run(args: argparse.Namespace) -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if args.output_root.exists():
        raise FileExistsError(f"output already exists: {args.output_root}")
    image = config["runtime"]["docker_image"]
    image_id = subprocess.run(
        ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    if image_id != config["runtime"]["image_id"]:
        raise RuntimeError("Docker image ID differs from pinned runtime")
    prepared = json.loads((args.prepared_root / "manifest.json").read_text(encoding="utf-8"))
    if prepared.get("status") != "PASS" or prepared.get("final_test") != "NOT_PRESENT_IN_INPUT_OR_PREPARED_ARTIFACTS":
        raise RuntimeError("prepared input is not promotion-eligible")
    args.output_root.parent.mkdir(parents=True, exist_ok=True)
    args.output_root.mkdir(parents=False, exist_ok=False)
    container_name = f"fmv3-{uuid.uuid4().hex[:10]}"
    command = [
        "docker", "run", "--rm", "--name", container_name, "--network", "none",
        "--cpus", str(config["runtime"]["cpu_limit"]), "--memory", config["runtime"]["memory"],
        "--memory-swap", config["runtime"]["memory"], "-e", f"FM_RUNTIME_IMAGE_ID={image_id}",
        "-e", "FM_OUTPUT_MOUNT_ISOLATED=true",
        "-v", f"{ROOT.resolve()}:/workspace:ro", "-v", f"{args.prepared_root.resolve()}:/prepared:ro",
        "-v", f"{args.output_root.resolve()}:/output", "-w", "/workspace", image, "python3",
        "scripts/fm_v3_train.py", "--prepared-root", "/prepared",
        "--output-root", "/output",
        "--config", "/workspace/experiments/fm-v3/config.json",
    ]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=int(config["runtime"]["timeout_seconds"])
        )
        stdout, stderr, returncode, stage = completed.stdout, completed.stderr, completed.returncode, "FIT_AND_PREDICT"
    except subprocess.TimeoutExpired as error:
        subprocess.run(["docker", "stop", "--time", "2", container_name],
                       capture_output=True, text=True, timeout=30)
        stdout = error.stdout.decode() if isinstance(error.stdout, bytes) else (error.stdout or "")
        stderr = error.stderr.decode() if isinstance(error.stderr, bytes) else (error.stderr or "")
        returncode, stage = 124, "TIMEOUT"
    log_root = args.output_root.parent / f"{args.output_root.name}-logs"
    log_root.mkdir(exist_ok=False)
    (log_root / "stdout.log").write_text(stdout, encoding="utf-8")
    (log_root / "stderr.log").write_text(stderr, encoding="utf-8")
    if returncode != 0:
        failure = {"status": "FAILED", "stage": stage, "returncode": returncode,
                   "elapsed_seconds": time.monotonic() - started, "docker_image_id": image_id,
                   "created_at": datetime.now(timezone.utc).isoformat()}
        (log_root / "failure.json").write_text(json.dumps(failure, indent=2) + "\n", encoding="utf-8")
        raise RuntimeError(f"FM-v3 run failed; see {log_root}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
