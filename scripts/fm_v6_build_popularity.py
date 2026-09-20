"""Build popularity from the same TRAIN position-20 target contract as learned models."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = ("experiments/fm-v6/DESIGN.md", "scripts/fm_v6_build_popularity.py")


def file_pin(path: Path) -> dict[str, int | str]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build(args: argparse.Namespace) -> dict:
    if not args.output_root.is_dir() or any(args.output_root.iterdir()):
        raise FileExistsError("popularity output must be an existing empty directory")
    manifest_path = args.input_root / "input/manifest.train-validation.json"
    episode_path = args.input_root / "input/episodes.train-validation.jsonl"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 6 or manifest.get("status") != "PASS":
        raise RuntimeError("prefix input manifest is not schema-v6 PASS")
    episode_pin = {key: manifest["episodes"][key] for key in ("bytes", "sha256")}
    if file_pin(episode_path) != episode_pin:
        raise RuntimeError("prefix episode pin differs from manifest")

    counts: Counter[int] = Counter()
    totals: Counter[int] = Counter()
    ordered_users: list[int] = []
    seen: set[int] = set()
    total = 0.0
    with episode_path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if '"role":"TRAIN"' not in line or '"n":0' not in line:
                continue
            row = json.loads(line)
            if row["role"] != "TRAIN" or int(row["n"]) != 0:
                continue
            uid = int(row["uid"])
            if uid in seen:
                raise RuntimeError(f"duplicate TRAIN K=0 target for user {uid}")
            seen.add(uid)
            ordered_users.append(uid)
            movie_id, rating = int(row["target_movie_id"]), float(row["target_rating"])
            counts[movie_id] += 1
            totals[movie_id] += rating
            total += rating
    expected_users = int(manifest["population"]["users"]["TRAIN"])
    user_digest = hashlib.sha256(",".join(map(str, ordered_users)).encode()).hexdigest()
    if len(seen) != expected_users or user_digest != manifest["population"]["role_user_digests"]["TRAIN"]:
        raise RuntimeError("TRAIN K=0 target population mismatch")

    mean, prior = total / len(seen), float(args.prior_count)
    artifact = args.output_root / "popularity.train-only.jsonl"
    with artifact.open("w", encoding="utf-8", newline="\n") as output:
        for movie_id in sorted(counts):
            value = {"movie_id": movie_id, "rating_count": int(counts[movie_id]),
                     "rating_sum": float(totals[movie_id]),
                     "bayes_score": (float(totals[movie_id]) + prior * mean) / (counts[movie_id] + prior)}
            output.write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
    source_files = {name: file_pin(ROOT / name) for name in SOURCE_FILES}
    result = {
        "schema_version": 2, "status": "PASS", "artifact_kind": "PREFIX_TRAIN_TARGET_POPULARITY_V2",
        "created_at": datetime.now(timezone.utc).isoformat(), "source_input_manifest": file_pin(manifest_path),
        "source_input_episodes": file_pin(episode_path), "ordered_train_user_digest": user_digest,
        "training_contract": "ONE_POSITION_20_TARGET_PER_TRAIN_USER_MATCHED_TO_LEARNED_MODEL_LABELS",
        "train_users": len(seen), "source_rows": len(seen), "movies": len(counts),
        "global_mean": mean, "prior_count": prior, "artifact": file_pin(artifact),
        "source_bundle": {"algorithm": "ORDERED_FILE_PINS_V1", "files": source_files,
                          "digest": digest(source_files)},
        "contains_user_ids": False, "contains_event_rows": False, "final_test_opened": False,
    }
    (args.output_root / "manifest.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--prior-count", type=float, default=20.0)
    build(parser.parse_args())


if __name__ == "__main__":
    main()
