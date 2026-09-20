"""Build an exact-pinned TRAIN-only popularity artifact for FM-v5.

This is the only FM-v5 stage allowed to mount the full MovieLens ratings file.
The resulting artifact contains aggregate movie statistics only; prepare/fit/evaluate
must never receive the raw ratings path.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def file_pin(path: Path) -> dict[str, int | str]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def ordered_train_users(episode_path: Path) -> list[int]:
    users: list[int] = []
    seen: set[int] = set()
    with episode_path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if "FINAL_TEST" in line:
                raise RuntimeError("FINAL_TEST token visible in TRAIN/VALIDATION handoff")
            if '"role": "TRAIN"' not in line:
                continue
            row = json.loads(line)
            uid = int(row["uid"])
            if uid not in seen:
                seen.add(uid)
                users.append(uid)
    return users


def build(args: argparse.Namespace) -> dict:
    if not args.output_root.is_dir() or any(args.output_root.iterdir()):
        raise FileExistsError("popularity output must be an existing empty isolated directory")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    input_manifest = json.loads((args.input_root / "input/manifest.train-validation.json").read_text(
        encoding="utf-8"
    ))
    ratings_pin = file_pin(args.ratings_path)
    expected_ratings = input_manifest["input"]["ratings_sha256"]
    if ratings_pin["sha256"] != expected_ratings:
        raise RuntimeError("raw ratings SHA-256 differs from v7 source manifest")
    episode_path = args.input_root / "input/episodes.train-validation.jsonl"
    expected_episode = config["execution_input"]["handoff"]["train_validation_episode_file_sha256"]
    if file_pin(episode_path)["sha256"] != expected_episode:
        raise RuntimeError("episode handoff hash mismatch")
    users = ordered_train_users(episode_path)
    digest = hashlib.sha256(",".join(map(str, users)).encode()).hexdigest()
    expected_user_digest = input_manifest["user_partition"]["role_user_digests"]["TRAIN"]
    if len(users) != int(input_manifest["users"]["TRAIN"]) or digest != expected_user_digest:
        raise RuntimeError("ordered TRAIN user population differs from v7 manifest")

    train_users = set(users)
    cutoff = int(input_manifest["cutoffs"]["TRAIN"]["target_end_inclusive"])
    counts: Counter[int] = Counter()
    totals: Counter[int] = Counter()
    global_sum = 0.0
    source_rows = 0
    with args.ratings_path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ["userId", "movieId", "rating", "timestamp"]:
            raise RuntimeError("unexpected MovieLens ratings header")
        for row in reader:
            uid = int(row["userId"])
            if uid not in train_users or int(row["timestamp"]) > cutoff:
                continue
            movie_id = int(row["movieId"])
            rating = float(row["rating"])
            counts[movie_id] += 1
            totals[movie_id] += rating
            global_sum += rating
            source_rows += 1
    if not source_rows:
        raise RuntimeError("TRAIN-only popularity source is empty")
    global_mean = global_sum / source_rows
    prior = float(args.prior_count)
    output_path = args.output_root / "popularity.train-only.jsonl"
    logical = hashlib.sha256()
    with output_path.open("w", encoding="utf-8", newline="\n") as output:
        for offset, movie_id in enumerate(sorted(counts)):
            item = {
                "movie_id": movie_id,
                "rating_count": int(counts[movie_id]),
                "rating_sum": float(totals[movie_id]),
                "bayes_score": (float(totals[movie_id]) + prior * global_mean) /
                               (float(counts[movie_id]) + prior),
            }
            encoded = json.dumps(item, sort_keys=True, separators=(",", ":"))
            if offset:
                logical.update(b"\n")
            logical.update(encoded.encode())
            output.write(encoded + "\n")
    artifact_pin = file_pin(output_path)
    manifest = {
        "schema_version": 1,
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "artifact_kind": "TRAIN_ONLY_POPULARITY_V1",
        "source_input_id": config["execution_input"]["run_id"],
        "source_ratings": ratings_pin,
        "expected_ratings_sha256": expected_ratings,
        "source_episode_export": file_pin(episode_path),
        "train_users": len(users),
        "ordered_train_user_digest": digest,
        "ordered_train_user_digest_algorithm": "SHA256_COMMA_JOINED_MANIFEST_ORDER_V1",
        "cutoff_inclusive": cutoff,
        "source_rows": source_rows,
        "movies": len(counts),
        "global_mean": global_mean,
        "prior_count": prior,
        "artifact": artifact_pin,
        "logical_sha256": logical.hexdigest(),
        "contains_user_ids": False,
        "contains_event_rows": False,
        "final_test_opened": False,
    }
    (args.output_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--ratings-path", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--prior-count", type=float, default=20.0)
    build(parser.parse_args())


if __name__ == "__main__":
    main()
