"""Physically isolate FM-v6 feature input from VALIDATION labels."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import shutil


def file_pin(path: Path) -> dict[str, int | str]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def episode_id(row: dict) -> str:
    return ":".join(map(str, (row["role"], row["uid"], row["target_movie_id"], row["prediction_at"], row["n"])))


def write_jsonl(source: Path, feature_path: Path, label_path: Path, kind: str) -> tuple[int, int]:
    rows = labels = 0
    with source.open("r", encoding="utf-8") as incoming, \
            feature_path.open("w", encoding="utf-8", newline="\n") as features, \
            label_path.open("w", encoding="utf-8", newline="\n") as label_output:
        for line in incoming:
            row = json.loads(line)
            if row["role"] == "FINAL_TEST":
                raise RuntimeError("FINAL_TEST row present in source handoff")
            if kind == "episode":
                if row["role"] == "VALIDATION":
                    label = {key: row[key] for key in ("uid", "target_movie_id", "prediction_at", "n")}
                    label.update({"episode_id": episode_id(row), "observed_rating": row.pop("target_rating")})
                    label_output.write(json.dumps(label, sort_keys=True, separators=(",", ":")) + "\n")
                    labels += 1
            else:
                label = {key: row[key] for key in ("uid", "target_movie_id", "prediction_at",
                                                    "candidate_movie_id", "candidate_rank")}
                for key in ("label_state", "observed_rating", "judgment_position",
                            "sampling_probability", "importance_weight"):
                    label[key] = row.pop(key)
                label_output.write(json.dumps(label, sort_keys=True, separators=(",", ":")) + "\n")
                labels += 1
            features.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
            rows += 1
    return rows, labels


def isolate(args: argparse.Namespace) -> dict:
    for path in (args.output_root, args.labels_root):
        if not path.is_dir() or any(path.iterdir()):
            raise FileExistsError("isolation outputs must be existing empty directories")
    source_manifest_path = args.source_root / "input/manifest.train-validation.json"
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    if source_manifest.get("schema_version") != 6 or source_manifest.get("status") != "PASS":
        raise RuntimeError("source prefix manifest is not schema-v6 PASS")
    if file_pin(source_manifest_path) != args.expected_manifest:
        raise RuntimeError("source prefix manifest pin mismatch")
    (args.output_root / "input").mkdir()
    (args.output_root / "catalog").mkdir()
    (args.output_root / "metadata").mkdir()
    episode_rows, target_labels = write_jsonl(
        args.source_root / "input/episodes.train-validation.jsonl",
        args.output_root / "input/episodes.features.jsonl",
        args.labels_root / "validation-target-labels.jsonl", "episode",
    )
    candidate_rows, candidate_labels = write_jsonl(
        args.source_root / "input/candidates.train-validation.jsonl",
        args.output_root / "input/candidates.features.jsonl",
        args.labels_root / "validation-candidate-labels-base.jsonl", "candidate",
    )
    for relative in ("catalog/TRAIN.catalog.jsonl", "catalog/VALIDATION.catalog.jsonl",
                     "catalog/catalog-snapshots.json", "input/split-distribution-report.train-validation.json",
                     "metadata/FINAL_TEST-SEAL.json"):
        destination = args.output_root / relative
        shutil.copyfile(args.source_root / relative, destination)
    feature_files = {str(path.relative_to(args.output_root)).replace("\\", "/"): file_pin(path)
                     for path in sorted(args.output_root.rglob("*")) if path.is_file()}
    label_files = {path.name: file_pin(path) for path in sorted(args.labels_root.glob("*.jsonl"))}
    manifest = {
        "schema_version": 6, "status": "PASS", "created_at": datetime.now(timezone.utc).isoformat(),
        "source_manifest": file_pin(source_manifest_path), "source_input": source_manifest,
        "rows": {"episodes": episode_rows, "candidates": candidate_rows,
                 "target_labels": target_labels, "candidate_base_labels": candidate_labels},
        "files": feature_files, "validation_labels": "PHYSICALLY_SEPARATE_UNMOUNTED_ROOT",
        "final_test": "SEALED_NOT_EXPORTED", "final_test_opened": False,
    }
    manifest_path = args.output_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    labels_manifest = {
        "schema_version": 1, "status": "SEALED_VALIDATION_LABELS",
        "source_feature_manifest": file_pin(manifest_path), "files": label_files,
        "rows": {"target_labels": target_labels, "candidate_base_labels": candidate_labels},
        "final_test": "NOT_PRESENT", "final_test_opened": False,
    }
    (args.labels_root / "manifest.json").write_text(
        json.dumps(labels_manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"feature_manifest": file_pin(manifest_path), "labels": labels_manifest}, indent=2))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--labels-root", type=Path, required=True)
    parser.add_argument("--expected-manifest-bytes", type=int, required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    args = parser.parse_args()
    args.expected_manifest = {"bytes": args.expected_manifest_bytes,
                              "sha256": args.expected_manifest_sha256}
    isolate(args)


if __name__ == "__main__":
    main()
