"""Run isolated FM-v6 prefix prepare, fit, evaluate, and verify stages."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "experiments/fm-v6/config.json"


def image(config: dict) -> tuple[str, str]:
    name = config["runtime"]["docker_image"]
    actual = subprocess.run(["docker", "image", "inspect", name, "--format", "{{.Id}}"],
                            check=True, capture_output=True, text=True).stdout.strip()
    if actual != config["runtime"]["image_id"]:
        raise RuntimeError("Docker image differs from frozen config")
    return name, actual


def empty(path: Path) -> None:
    if path.exists():
        raise FileExistsError(f"output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()


def container(stage: str, output: Path, mounts: list[tuple[Path, str]], command: list[str], config: dict) -> None:
    empty(output)
    image_name, image_id = image(config)
    name = f"fmv6-{stage.lower()}-{uuid.uuid4().hex[:8]}"
    docker = ["docker", "run", "--rm", "--name", name, "--network", "none",
              "--cpus", str(config["runtime"]["cpu_limit"]), "--memory", config["runtime"]["memory"],
              "--memory-swap", config["runtime"]["memory"], "-e", f"FM_RUNTIME_IMAGE_ID={image_id}",
              "-v", f"{ROOT.resolve()}:/workspace:ro"]
    for source, target in mounts:
        docker += ["-v", f"{source.resolve()}:{target}:ro"]
    docker += ["-v", f"{output.resolve()}:/output:rw", "-w", "/workspace", image_name, "python3", *command]
    started = time.monotonic()
    try:
        completed = subprocess.run(docker, capture_output=True, text=True,
                                   timeout=int(config["runtime"]["timeout_seconds"]))
        stdout, stderr, code = completed.stdout, completed.stderr, completed.returncode
    except subprocess.TimeoutExpired as error:
        subprocess.run(["docker", "stop", "--time", "2", name], capture_output=True, text=True, timeout=30)
        stdout = error.stdout.decode() if isinstance(error.stdout, bytes) else (error.stdout or "")
        stderr = error.stderr.decode() if isinstance(error.stderr, bytes) else (error.stderr or "")
        code = 124
    logs = output.parent / f"{output.name}-logs"
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
        raise RuntimeError(f"FM-v6 {stage} failed; see {logs}")


def run(args: argparse.Namespace) -> None:
    config = json.loads(args.config.read_text(encoding="utf-8"))
    config_path = "/workspace/experiments/fm-v6/config.json"
    if args.stage == "prepare":
        if not args.input_root or not args.popularity_root:
            raise RuntimeError("prepare requires isolated input and popularity roots")
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                                  capture_output=True, text=True).stdout.strip()
        command = ["scripts/fm_v6_prepare.py", "--input-root", "/input", "--popularity-root", "/popularity",
                   "--output-root", "/output", "--config", config_path, "--source-revision", revision,
                   "--user-modulus", str(args.user_modulus), "--user-bucket", str(args.user_bucket)]
        container("PREPARE", args.output_root,
                  [(args.input_root, "/input"), (args.popularity_root, "/popularity")], command, config)
    elif args.stage == "fit":
        if not args.prepared_root:
            raise RuntimeError("fit requires prepared root")
        command = ["scripts/fm_v6_train.py", "--prepared-root", "/prepared", "--output-root", "/output",
                   "--config", config_path]
        if args.preflight:
            command.append("--preflight")
        container("FIT", args.output_root, [(args.prepared_root, "/prepared")], command, config)
    elif args.stage == "evaluate":
        if not args.prepared_root or not args.fits_root or not args.labels_root:
            raise RuntimeError("evaluate requires prepared, fit, and labels roots")
        container("EVALUATE", args.output_root,
                  [(args.prepared_root, "/prepared"), (args.fits_root, "/fits"),
                   (args.labels_root, "/labels")],
                  ["scripts/fm_v6_evaluate.py", "--prepared-root", "/prepared", "--fits-root", "/fits",
                   "--labels-root", "/labels", "--output-root", "/output",
                   "--output", "/output/validation-report.json", "--config", config_path], config)
    elif args.stage == "verify":
        if not args.input_root or not args.prepared_root or not args.fits_root or not args.labels_root or not args.evaluation_root:
            raise RuntimeError("verify requires input, labels, prepared, fit, and evaluation roots")
        container("VERIFY", args.output_root,
                  [(args.input_root, "/input"), (args.labels_root, "/labels"),
                   (args.prepared_root, "/prepared"), (args.fits_root, "/fits"),
                   (args.evaluation_root, "/evaluation")],
                  ["scripts/fm_v6_verify.py", "--input-root", "/input", "--labels-root", "/labels",
                   "--prepared-root", "/prepared", "--fits-root", "/fits",
                   "--evaluation-root", "/evaluation", "--config", config_path,
                   "--output", "/output/verification.json"], config)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("prepare", "fit", "evaluate", "verify"))
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--input-root", type=Path)
    parser.add_argument("--popularity-root", type=Path)
    parser.add_argument("--prepared-root", type=Path)
    parser.add_argument("--fits-root", type=Path)
    parser.add_argument("--labels-root", type=Path)
    parser.add_argument("--evaluation-root", type=Path)
    parser.add_argument("--user-modulus", type=int, default=1)
    parser.add_argument("--user-bucket", type=int, default=0)
    parser.add_argument("--preflight", action="store_true")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
