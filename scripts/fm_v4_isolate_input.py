"""Expose only label-blind TRAIN and VALIDATION episodes to FM-v4 preparation."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from fm_v4_build_input import (
    JsonlWriter,
    file_pin,
    history_policy_contract,
    load_config,
    recent_n_values,
    serialized_role,
)


def verify_pin(path: Path, expected: dict) -> None:
    actual = file_pin(path)
    if actual != {"bytes": int(expected["bytes"]), "sha256": expected["sha256"]}:
        raise RuntimeError(f"source file pin mismatch: {path.name}")


def isolate(args: argparse.Namespace) -> dict:
    if args.output_root.exists():
        raise FileExistsError(f"output already exists: {args.output_root}")
    source_manifest_path = args.source_root / "manifest.json"
    config = load_config(args.config)
    expected_source_manifest = config.get("pins", {}).get("source_manifest")
    if not expected_source_manifest:
        raise RuntimeError("config source_manifest pin is not frozen")
    verify_pin(source_manifest_path, expected_source_manifest)
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    if source_manifest.get("status") != "PASS":
        raise RuntimeError("source input status is not PASS")
    if source_manifest.get("contract") != "FM_V4_LABEL_SEPARATED_ROLLING_RECENT_N_SOURCE_V1":
        raise RuntimeError("source input contract mismatch")
    seed = int(config["split"]["seed"])
    if int(source_manifest.get("seed", -1)) != seed:
        raise RuntimeError("source seed mismatch")
    if source_manifest.get("input") != {
        name: {"bytes": int(config["source"][name]["bytes"]), "sha256": config["source"][name]["sha256"]}
        for name in ("ratings", "movies", "tags")
    }:
        raise RuntimeError("source raw input pins differ from config")
    if source_manifest.get("config_contract", {}).get("expected_census") != config["expected_census"]:
        raise RuntimeError("source census contract differs from config")
    boundary = source_manifest.get("label_boundary", {})
    if boundary.get("validation_target_rating_in_episode") is not False:
        raise RuntimeError("source does not assert a label-blind validation episode contract")
    if boundary.get("final_test_materialized") is not False:
        raise RuntimeError("source materialized FINAL_TEST")
    expected_history_policy = history_policy_contract()
    if source_manifest.get("history_policy") != expected_history_policy:
        raise RuntimeError("source rolling recent-N history policy mismatch")

    train_source = args.source_root / "train-episodes.jsonl"
    validation_source = args.source_root / "validation-episodes.jsonl"
    verify_pin(train_source, source_manifest["files"]["train_episodes"])
    verify_pin(validation_source, source_manifest["files"]["validation_episodes"])
    # Deliberately do not open, parse, stat, or hash validation-labels.jsonl here.

    args.output_root.mkdir(parents=True)
    writers = {
        "TRAIN": JsonlWriter(args.output_root / "train-episodes.jsonl"),
        "VALIDATION": JsonlWriter(args.output_root / "validation-episodes.jsonl"),
    }
    users = {role: set() for role in writers}
    targets = {role: set() for role in writers}
    rows = Counter()
    episode_ids: set[str] = set()
    target_n_values: dict[tuple[str, str], set[int]] = {}
    target_history_counts: dict[tuple[str, str], int] = {}

    try:
        for expected_role, source_path in (("TRAIN", train_source), ("VALIDATION", validation_source)):
            with source_path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    role = serialized_role(line)
                    if role != expected_role:
                        raise RuntimeError(f"{source_path.name} contains role {role} before JSON parse")
                    if expected_role == "VALIDATION" and re.search(r'"target_rating"\s*:', line):
                        raise RuntimeError("validation episode exposes target_rating before JSON parse")
                    row = json.loads(line)
                    if row.get("role") != expected_role:
                        raise RuntimeError("serialized role differs after JSON parsing")
                    required = {
                        "uid", "target_key", "episode_id", "prediction_at", "target_event_at", "n",
                        "total_history_count", "supported_history_count", "history"
                    }
                    if not required <= row.keys():
                        raise RuntimeError("episode is missing required fields")
                    if expected_role == "TRAIN" and "target_rating" not in row:
                        raise RuntimeError("TRAIN episode has no target_rating")
                    n = int(row["n"])
                    total_history_count = int(row["total_history_count"])
                    if total_history_count < 0:
                        raise RuntimeError("negative total_history_count")
                    if n < 0 or n > min(total_history_count, 50):
                        raise RuntimeError("episode N exceeds the capped available target history")
                    if int(row["supported_history_count"]) != n or len(row["history"]) != n:
                        raise RuntimeError("episode N differs from serialized recent history length")
                    if row["episode_id"] != f"{row['target_key']}:{n}":
                        raise RuntimeError("episode_id does not match target_key and n")
                    target_identity = expected_role, str(row["target_key"])
                    prior_history_count = target_history_counts.setdefault(target_identity, total_history_count)
                    if prior_history_count != total_history_count:
                        raise RuntimeError("target variants disagree on total_history_count")
                    n_values = target_n_values.setdefault(target_identity, set())
                    if n in n_values:
                        raise RuntimeError("duplicate N variant for target")
                    n_values.add(n)
                    if row["episode_id"] in episode_ids:
                        raise RuntimeError("duplicate episode_id")
                    episode_ids.add(row["episode_id"])
                    writers[expected_role].write(row)
                    users[expected_role].add(int(row["uid"]))
                    targets[expected_role].add(str(row["target_key"]))
                    rows[expected_role] += 1
        for target_identity, actual_n_values in sorted(target_n_values.items()):
            expected_n_values = set(recent_n_values(target_history_counts[target_identity]))
            if actual_n_values != expected_n_values:
                raise RuntimeError(
                    "target N variant set mismatch: "
                    f"target={target_identity[1]} actual={sorted(actual_n_values)} "
                    f"expected={sorted(expected_n_values)}"
                )
    except Exception:
        for writer in writers.values():
            writer.abort()
        raise

    output_files = {role: writer.close() for role, writer in writers.items()}
    expected_users = source_manifest["users"]
    actual_users = {role: len(users[role]) for role in writers}
    if actual_users != {role: int(expected_users[role]) for role in writers}:
        raise RuntimeError(f"isolated user counts differ: {actual_users}")
    actual_targets = {role: len(targets[role]) for role in writers}
    if actual_targets != {role: int(source_manifest["targets"][role]) for role in writers}:
        raise RuntimeError(f"isolated target counts differ: {actual_targets}")

    manifest = {
        "schema_version": 1,
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "contract": "FM_V4_PHYSICALLY_ISOLATED_LABEL_BLIND_TRAIN_VALIDATION_V1",
        "seed": seed,
        "roles": ["TRAIN", "VALIDATION"],
        "users": actual_users,
        "targets": actual_targets,
        "episodes": dict(rows),
        "files": {
            "train-episodes.jsonl": output_files["TRAIN"],
            "validation-episodes.jsonl": output_files["VALIDATION"],
        },
        "source_manifest": file_pin(source_manifest_path),
        "source_input": source_manifest["input"],
        "source_episode_files": {
            "train_episodes": source_manifest["files"]["train_episodes"],
            "validation_episodes": source_manifest["files"]["validation_episodes"],
        },
        "sealed_validation_labels": source_manifest["files"]["validation_labels"],
        "prior_validation_exclusion": source_manifest["prior_validation_exclusion"],
        "eligible_census": source_manifest["eligible_census"],
        "target_policy": source_manifest["target_policy"],
        "prediction_at_policy": source_manifest["prediction_at_policy"],
        "history_policy": expected_history_policy,
        "validation_labels_opened": False,
        "validation_labels_materialized": False,
        "final_test_opened": False,
        "final_test_materialized": False,
        "candidate_ranking_files_materialized": False,
    }
    (args.output_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    isolate(parser.parse_args())


if __name__ == "__main__":
    main()
