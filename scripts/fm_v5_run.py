"""Run isolated FM-v5 prepare, fit, and validation evaluation containers."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "experiments/fm-v5/config.json"


def _image(config: dict) -> tuple[str, str]:
    image = config["runtime"]["docker_image"]
    actual = subprocess.run(["docker", "image", "inspect", image, "--format", "{{.Id}}"],
                            check=True, capture_output=True, text=True).stdout.strip()
    if actual != config["runtime"]["image_id"]:
        raise RuntimeError("Docker image ID differs from frozen config")
    return image, actual


def _empty(path: Path) -> None:
    if path.exists():
        raise FileExistsError(f"output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()


def _container(stage: str, outputs: list[tuple[Path, str]], mounts: list[tuple[Path, str]],
               command: list[str], config: dict) -> None:
    for path, _ in outputs:
        _empty(path)
    image, image_id = _image(config)
    name = f"fmv5-{stage.lower()}-{uuid.uuid4().hex[:10]}"
    docker = ["docker", "run", "--rm", "--name", name, "--network", "none",
              "--cpus", str(config["runtime"]["cpu_limit"]), "--memory", config["runtime"]["memory"],
              "--memory-swap", config["runtime"]["memory"], "-e", f"FM_RUNTIME_IMAGE_ID={image_id}"]
    docker += ["-v", f"{ROOT.resolve()}:/workspace:ro"]
    for source, target in mounts:
        docker += ["-v", f"{source.resolve()}:{target}:ro"]
    for source, target in outputs:
        docker += ["-v", f"{source.resolve()}:{target}:rw"]
    docker += ["-w", "/workspace", image, "python3", *command]
    started = time.monotonic()
    try:
        completed = subprocess.run(docker, capture_output=True, text=True,
                                   timeout=int(config["runtime"]["timeout_seconds"]))
        stdout, stderr, code = completed.stdout, completed.stderr, completed.returncode
    except subprocess.TimeoutExpired as exc:
        subprocess.run(["docker", "stop", "--time", "2", name], capture_output=True, text=True, timeout=30)
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        code = 124
    primary = outputs[0][0]
    logs = primary.parent / f"{primary.name}-logs"
    if logs.exists():
        raise FileExistsError(f"log output already exists: {logs}")
    logs.mkdir()
    (logs / "stdout.log").write_text(stdout, encoding="utf-8")
    (logs / "stderr.log").write_text(stderr, encoding="utf-8")
    (logs / "run.json").write_text(json.dumps({
        "stage": stage, "returncode": code, "elapsed_seconds": time.monotonic() - started,
        "runtime_image_id": image_id, "created_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2) + "\n", encoding="utf-8")
    if code:
        raise RuntimeError(f"FM-v5 {stage} failed; see {logs}")


def run(args: argparse.Namespace) -> None:
    config = json.loads(args.config.read_text(encoding="utf-8"))
    config_mount = "/workspace/experiments/fm-v5/config.json"
    if args.stage == "build-popularity":
        if not args.input_root or not args.ratings_path:
            raise RuntimeError("build-popularity requires input root and raw ratings")
        _container("POPULARITY", [(args.output_root, "/output")],
                   [(args.input_root, "/input"), (args.ratings_path, "/catalog/ratings.csv")],
                   ["scripts/fm_v5_build_popularity.py", "--input-root", "/input",
                    "--ratings-path", "/catalog/ratings.csv", "--output-root", "/output",
                    "--config", config_mount], config)
    elif args.stage == "prepare":
        if not args.input_root or not args.popularity_root or not args.labels_root:
            raise RuntimeError("prepare requires input, TRAIN-only popularity, and labels roots")
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                                  capture_output=True, text=True).stdout.strip()
        _container("PREPARE", [(args.output_root, "/output"), (args.labels_root, "/labels-output")],
                   [(args.input_root, "/input"), (args.popularity_root, "/popularity")],
                   ["scripts/fm_v5_prepare.py", "--input-root", "/input", "--popularity-root",
                    "/popularity", "--output-root", "/output", "--labels-root", "/labels-output",
                    "--config", config_mount, "--source-revision", revision], config)
    elif args.stage == "fit":
        if not args.prepared_root:
            raise RuntimeError("fit requires prepared root")
        _container("FIT", [(args.output_root, "/output")], [(args.prepared_root, "/prepared")],
                   ["scripts/fm_v5_train.py", "--prepared-root", "/prepared", "--output-root", "/output",
                    "--config", config_mount], config)
    elif args.stage == "evaluate":
        if not args.fits_root or not args.labels_root:
            raise RuntimeError("evaluate requires fits and labels roots")
        _container("EVALUATE", [(args.output_root, "/output")],
                   [(args.fits_root, "/fits"), (args.labels_root, "/labels")],
                   ["scripts/fm_v5_evaluate.py", "--fits-root", "/fits", "--labels-root", "/labels",
                    "--output-root", "/output", "--output", "/output/validation-report.json",
                    "--config", config_mount], config)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("build-popularity", "prepare", "fit", "evaluate"))
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--input-root", type=Path)
    parser.add_argument("--ratings-path", type=Path)
    parser.add_argument("--popularity-root", type=Path)
    parser.add_argument("--labels-root", type=Path)
    parser.add_argument("--prepared-root", type=Path)
    parser.add_argument("--fits-root", type=Path)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
