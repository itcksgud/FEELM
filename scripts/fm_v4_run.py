"""Run the isolated FM-v4 prepare, fit, and label-opening evaluation stages."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "experiments/fm-v4/config.json"


def image_contract(config: dict) -> tuple[str, str]:
    image = config["runtime"]["docker_image"]
    actual = subprocess.run(
        ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    expected = config["runtime"]["image_id"]
    if actual != expected:
        raise RuntimeError(f"Docker image ID differs: {actual} != {expected}")
    return image, actual


def empty_output(path: Path) -> None:
    if path.exists():
        raise FileExistsError(f"output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()


def run_container(stage: str, output_root: Path, config: dict, mounts: list[tuple[Path, str, str]],
                  environment: dict[str, str], command: list[str]) -> None:
    empty_output(output_root)
    image, image_id = image_contract(config)
    container_name = f"fmv4-{stage.lower()}-{uuid.uuid4().hex[:10]}"
    docker = [
        "docker", "run", "--rm", "--name", container_name, "--network", "none",
        "--cpus", str(config["runtime"]["cpu_limit"]),
        "--memory", config["runtime"]["memory"], "--memory-swap", config["runtime"]["memory"],
    ]
    for name, value in environment.items():
        docker.extend(("-e", f"{name}={value}"))
    for source, target, mode in mounts:
        docker.extend(("-v", f"{source.resolve()}:{target}:{mode}"))
    docker.extend(("-v", f"{output_root.resolve()}:/output:rw", "-w", "/workspace", image, "python3"))
    docker.extend(command)
    started = time.monotonic()
    timeout = int(config["runtime"]["timeout_seconds"])
    try:
        completed = subprocess.run(docker, capture_output=True, text=True, timeout=timeout)
        stdout, stderr, returncode = completed.stdout, completed.stderr, completed.returncode
    except subprocess.TimeoutExpired as error:
        subprocess.run(["docker", "stop", "--time", "2", container_name],
                       capture_output=True, text=True, timeout=30)
        stdout = error.stdout.decode() if isinstance(error.stdout, bytes) else (error.stdout or "")
        stderr = error.stderr.decode() if isinstance(error.stderr, bytes) else (error.stderr or "")
        returncode = 124
    log_root = output_root.parent / f"{output_root.name}-logs"
    if log_root.exists():
        raise FileExistsError(f"log output already exists: {log_root}")
    log_root.mkdir()
    (log_root / "stdout.log").write_text(stdout, encoding="utf-8")
    (log_root / "stderr.log").write_text(stderr, encoding="utf-8")
    run_meta = {
        "stage": stage, "returncode": returncode, "elapsed_seconds": time.monotonic() - started,
        "runtime_image_id": image_id, "created_at": datetime.now(timezone.utc).isoformat(),
    }
    (log_root / "run.json").write_text(json.dumps(run_meta, indent=2) + "\n", encoding="utf-8")
    if returncode:
        raise RuntimeError(f"FM-v4 {stage} failed; see {log_root}")


def run(args: argparse.Namespace) -> None:
    config = json.loads(args.config.read_text(encoding="utf-8"))
    image_id = config["runtime"]["image_id"]
    workspace = (ROOT, "/workspace", "ro")
    config_path = "/workspace/experiments/fm-v4/config.json"
    if args.stage == "prepare":
        if not args.input_root or not args.movies_path or not args.tags_path:
            raise RuntimeError("prepare requires --input-root, --movies-path, and --tags-path")
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True,
        ).stdout.strip()
        run_container("PREPARE", args.output_root, config, [
            workspace, (args.input_root, "/input", "ro"),
            (args.movies_path, "/catalog/movies.csv", "ro"),
            (args.tags_path, "/catalog/tags.csv", "ro"),
        ], {"FM_V4_PREP_OUTPUT_MOUNT_ISOLATED": "true", "FM_RUNTIME_IMAGE_ID": image_id}, [
            "scripts/fm_v4_prepare.py", "--input-root", "/input",
            "--movies-path", "/catalog/movies.csv", "--tags-path", "/catalog/tags.csv",
            "--output-root", "/output", "--config", config_path, "--source-revision", revision,
        ])
    elif args.stage == "fit":
        if not args.prepared_root:
            raise RuntimeError("fit requires --prepared-root")
        run_container("FIT", args.output_root, config, [
            workspace, (args.prepared_root, "/prepared", "ro"),
        ], {"FM_OUTPUT_MOUNT_ISOLATED": "true", "FM_RUNTIME_IMAGE_ID": image_id}, [
            "scripts/fm_v4_train.py", "--prepared-root", "/prepared",
            "--output-root", "/output", "--config", config_path,
        ])
    elif args.stage == "evaluate":
        if not args.fits_root or not args.validation_labels:
            raise RuntimeError("evaluate requires --fits-root and --validation-labels")
        run_container("EVALUATE", args.output_root, config, [
            workspace, (args.fits_root, "/fits", "ro"),
            (args.validation_labels, "/labels/validation-labels.jsonl", "ro"),
        ], {"FM_RUNTIME_IMAGE_ID": image_id}, [
            "scripts/fm_v4_evaluate.py", "--fits-root", "/fits",
            "--validation-labels", "/labels/validation-labels.jsonl",
            "--config", config_path, "--output", "/output/validation-report.json",
        ])
    else:  # pragma: no cover
        raise RuntimeError(f"unknown stage: {args.stage}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("prepare", "fit", "evaluate"))
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--input-root", type=Path)
    parser.add_argument("--movies-path", type=Path)
    parser.add_argument("--tags-path", type=Path)
    parser.add_argument("--prepared-root", type=Path)
    parser.add_argument("--fits-root", type=Path)
    parser.add_argument("--validation-labels", type=Path)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
