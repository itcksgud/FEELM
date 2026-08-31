from __future__ import annotations

import argparse
import json
from pathlib import Path

from recommendation_evidence_paths import artifact_matches, repository_path


REPO_ROOT = Path(__file__).resolve().parents[1]


def validate(manifest_path: Path) -> list[str]:
    errors: list[str] = []
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("locked_test_opened") is not False:
        errors.append("Locked Test must remain unopened")
    for record in manifest.get("artifacts", []):
        path = REPO_ROOT / repository_path(record["path"])
        if not artifact_matches(path, record):
            errors.append(f"artifact checksum mismatch: {record['path']}")
    root = REPO_ROOT / "outputs/recommendation-evidence/rec-ev-020p"
    summary = json.loads((root / "validation-cohort-summary.json").read_text(encoding="utf-8"))
    if summary.get("status") != "PASS":
        errors.append("cohort preflight did not pass")
    if summary.get("raw_user_ids_stored") is not False:
        errors.append("raw user IDs must not be stored")
    if summary.get("duplicate_user_movie_rows") != 0:
        errors.append("duplicate user/movie rows must be zero")
    if summary.get("seed_count") != 20:
        errors.append("exactly 20 locked seeds are required")
    if summary.get("slate_size_sensitivity") != [10, 20, 30]:
        errors.append("slate-size sensitivity must be 10, 20, 30")
    sensitivity = __import__("pandas").read_parquet(root / "slate-size-seed-sensitivity.parquet")
    forbidden = {"userId", "user_id", "raw_user_id"}.intersection(sensitivity.columns)
    if forbidden:
        errors.append(f"raw user ID columns found: {sorted(forbidden)}")
    if sorted(sensitivity["slate_size"].unique().tolist()) != [10, 20, 30]:
        errors.append("sensitivity artifact slate sizes mismatch")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=REPO_ROOT / "docs/recommendation/evidence/manifests/rec-ev-020p.json")
    args = parser.parse_args()
    errors = validate(args.manifest.resolve())
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print("PASS: REC-EV-020P-A artifacts, checksums, privacy, seeds, and slate sizes are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
