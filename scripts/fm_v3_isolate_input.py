"""Materialize a physically isolated TRAIN+VALIDATION input for FM-v3."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path



def file_pin(path: Path) -> dict[str, int | str]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def serialized_role(line: str) -> str:
    match = re.search(r'"role"\s*:\s*"(TRAIN|VALIDATION|FINAL_TEST)"', line)
    if not match:
        raise RuntimeError("serialized row has no recognized role")
    return match.group(1)


class CanonicalWriter:
    def __init__(self, path: Path):
        self.stream = path.open("w", encoding="utf-8", newline="\n")
        self.digest = hashlib.sha256()
        self.rows = 0

    def write(self, row: dict) -> None:
        encoded = json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
        if self.rows:
            self.digest.update(b"\n")
        self.digest.update(encoded)
        self.stream.write(json.dumps(row, sort_keys=True) + "\n")
        self.rows += 1

    def close(self) -> dict:
        self.stream.close()
        return {"rows": self.rows, "sha256": self.digest.hexdigest()}


def isolate(args: argparse.Namespace) -> dict:
    if args.output_root.exists():
        raise FileExistsError(f"output already exists: {args.output_root}")
    source_manifest_path = args.source_root / "manifest.json"
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    expected_users = {"TRAIN": args.train_users, "VALIDATION": args.validation_users,
                      "FINAL_TEST": args.final_test_users}
    if source_manifest.get("status") != "PASS" or source_manifest.get("users") != expected_users:
        raise RuntimeError("source input is not the requested deterministic full-eligible contract")
    if int(source_manifest.get("seed", -1)) != args.seed:
        raise RuntimeError("source seed mismatch")
    census = source_manifest.get("eligible_census", {})
    expected_census = census.get("roles", {})
    if census.get("train_population_saturated") is not True:
        raise RuntimeError("source input does not prove TRAIN census saturation")
    if expected_census.get("TRAIN", {}).get("total") != args.train_users:
        raise RuntimeError("source eligible TRAIN census differs from selected users")

    args.output_root.mkdir(parents=True)
    episode_writer = CanonicalWriter(args.output_root / "episodes.jsonl")
    candidate_writer = CanonicalWriter(args.output_root / "candidates.jsonl")
    users: dict[str, set[int]] = {"TRAIN": set(), "VALIDATION": set()}
    targets: dict[str, set[tuple[int, int, int]]] = {"TRAIN": set(), "VALIDATION": set()}
    role_rows = Counter()
    rejected = Counter()

    with (args.source_root / "episodes.jsonl").open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            role = serialized_role(line)
            if role == "FINAL_TEST":
                rejected["FINAL_TEST_EPISODE_BEFORE_JSON_PARSE"] += 1
                continue
            row = json.loads(line)
            if row["role"] not in {"TRAIN", "VALIDATION"}:
                raise RuntimeError("unexpected episode role")
            episode_writer.write(row)
            users[row["role"]].add(int(row["uid"]))
            targets[row["role"]].add((int(row["uid"]), int(row["target_movie_id"]), int(row["prediction_at"])))
            role_rows[f"episodes:{row['role']}"] += 1

    with (args.source_root / "candidates.jsonl").open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            role = serialized_role(line)
            if role == "FINAL_TEST":
                rejected["FINAL_TEST_CANDIDATE_BEFORE_JSON_PARSE"] += 1
                continue
            if role == "TRAIN":
                rejected["UNNEEDED_TRAIN_CANDIDATE_BEFORE_JSON_PARSE"] += 1
                continue
            row = json.loads(line)
            if row["role"] != "VALIDATION":
                raise RuntimeError("unexpected candidate role")
            candidate_writer.write(row)
            role_rows["candidates:VALIDATION"] += 1

    episodes = episode_writer.close()
    candidates = candidate_writer.close()
    actual_users = {role: len(values) for role, values in users.items()}
    if actual_users != {"TRAIN": args.train_users, "VALIDATION": args.validation_users}:
        raise RuntimeError(f"isolated user counts differ: {actual_users}")
    manifest = {
        "schema_version": 1,
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "contract": "FM_V3_PHYSICALLY_ISOLATED_TRAIN_VALIDATION_V1",
        "seed": args.seed,
        "roles": ["TRAIN", "VALIDATION"],
        "users": actual_users,
        "targets": {role: len(values) for role, values in targets.items()},
        "episodes": episodes,
        "validation_candidates": candidates,
        "role_rows": dict(sorted(role_rows.items())),
        "source_manifest": file_pin(source_manifest_path),
        "source_snapshot": source_manifest["input"],
        "source_contract": {
            "schema_version": source_manifest["schema_version"],
            "status": source_manifest["status"],
            "seed": source_manifest["seed"],
            "cutoffs": source_manifest["cutoffs"],
            "positive_threshold": source_manifest["positive_threshold"],
            "users": source_manifest["users"],
            "role_user_digests": source_manifest["user_partition"]["role_user_digests"],
            "targets": source_manifest["targets"],
            "distribution_report": source_manifest["distribution_report"],
            "target_policy": source_manifest["target_policy"],
            "catalog_as_of_policy": source_manifest["catalog_as_of_policy"],
            "catalog": source_manifest["catalog"],
            "unknown_sampling": source_manifest["unknown_sampling"],
            "final_test_seal_id": source_manifest["final_test_seal_id"],
        },
        "eligible_census": census,
        "prior_validation_exclusion": source_manifest["prior_validation_exclusion"],
        "source_input_digests": {
            "episodes": source_manifest["episodes"],
            "candidates": source_manifest["candidates"],
        },
        "rejected_without_json_parse": dict(sorted(rejected.items())),
        "final_test_materialized": False,
        "final_test_label_accessed": False,
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
    parser.add_argument("--seed", type=int, default=3623)
    parser.add_argument("--train-users", type=int, default=21075)
    parser.add_argument("--validation-users", type=int, default=2500)
    parser.add_argument("--final-test-users", type=int, default=2000)
    isolate(parser.parse_args())


if __name__ == "__main__":
    main()
