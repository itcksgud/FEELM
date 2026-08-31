from __future__ import annotations

import argparse
import json
import sys
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from recommendation_protocol_v4 import cold_fold, density_bucket, item_bucket, sha256_file, user_bucket


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARCHIVE = Path(r"C:\higher\projects\MM\data\raw\ml-32m.zip")
MASK_SEEDS = [1701, 2903, 4001]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _member(archive: zipfile.ZipFile, suffix: str) -> str:
    matches = [name for name in archive.namelist() if name.endswith(suffix)]
    if len(matches) != 1:
        raise RuntimeError(f"expected one {suffix} member, found {matches}")
    return matches[0]


def _item_role(bucket: int) -> str:
    if bucket <= 59:
        return "ITEM_TRAIN"
    if bucket <= 79:
        return "ITEM_VALIDATION"
    return "ITEM_LOCKED_TEST"


def _density_role(bucket: int) -> str:
    if bucket <= 59:
        return "DENSITY_TRAIN"
    if bucket <= 79:
        return "DENSITY_VALIDATION"
    return "DENSITY_LOCKED_TEST"


def _load_catalog(archive_path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(archive_path) as archive:
        with archive.open(_member(archive, "movies.csv")) as handle:
            movies = pd.read_csv(handle, usecols=["movieId", "title"])
        with archive.open(_member(archive, "links.csv")) as handle:
            links = pd.read_csv(handle, usecols=["movieId", "tmdbId"])
    catalog = movies.merge(links, on="movieId", how="left", validate="one_to_one")
    catalog["movieId"] = catalog["movieId"].astype("int32")
    catalog["tmdb_identity_present"] = pd.to_numeric(catalog["tmdbId"], errors="coerce").notna()
    catalog["item_bucket"] = catalog["movieId"].map(lambda value: item_bucket(int(value))).astype("uint8")
    catalog["density_bucket"] = catalog["movieId"].map(lambda value: density_bucket(int(value))).astype("uint8")
    catalog["item_role"] = catalog["item_bucket"].map(_item_role)
    catalog["density_role"] = np.where(
        catalog["item_role"] == "ITEM_TRAIN",
        catalog["density_bucket"].map(_density_role),
        "DENSITY_OUT_OF_SCOPE",
    )
    catalog["fold"] = catalog["movieId"].map(lambda value: cold_fold(int(value))).astype("uint8")
    return catalog


def _safe_base_counts(archive_path: Path, safe_movie_ids: set[int], chunksize: int) -> Counter[int]:
    counts: Counter[int] = Counter()
    max_user_id = 300_000
    user_buckets = np.empty(max_user_id + 1, dtype=np.uint8)
    for user_id in range(max_user_id + 1):
        user_buckets[user_id] = user_bucket(user_id)
    with zipfile.ZipFile(archive_path) as archive:
        with archive.open(_member(archive, "ratings.csv")) as handle:
            reader = pd.read_csv(handle, usecols=["userId", "movieId"], dtype={"userId": "int32", "movieId": "int32"}, chunksize=chunksize)
            for chunk in reader:
                users = chunk["userId"].to_numpy(dtype=np.int64, copy=False)
                base_mask = user_buckets[users] <= 39
                safe_mask = chunk["movieId"].isin(safe_movie_ids).to_numpy()
                selected = chunk.loc[base_mask & safe_mask, "movieId"]
                if not selected.empty:
                    value_counts = selected.value_counts()
                    counts.update({int(key): int(value) for key, value in value_counts.items()})
    return counts


def _q_bin(value: int) -> str:
    if value == 0:
        return "Q0"
    if value <= 4:
        return "Q1_4"
    if value <= 19:
        return "Q5_19"
    if value <= 99:
        return "Q20_99"
    return "Q100P"


def build_preflight(protocol_path: Path, archive_path: Path, output_root: Path, role: str, chunksize: int) -> dict[str, Any]:
    if role.lower() != "validation":
        raise ValueError("this runner is intentionally limited to Validation")
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    actual_archive_sha = sha256_file(archive_path)
    if actual_archive_sha != protocol["source"]["movielens_archive_sha256"]:
        raise RuntimeError("MovieLens archive checksum mismatch")
    catalog = _load_catalog(archive_path)
    output_root.mkdir(parents=True, exist_ok=True)

    role_matrix = pd.crosstab(catalog["item_role"], catalog["density_role"]).to_dict()
    role_matrix_json = {
        density_role: {item_role: int(count) for item_role, count in item_roles.items()}
        for density_role, item_roles in role_matrix.items()
    }
    protected_collision = catalog[
        (catalog["density_role"] == "DENSITY_VALIDATION")
        & (catalog["item_role"].isin(["ITEM_VALIDATION", "ITEM_LOCKED_TEST"]))
    ]
    safe_targets = catalog[
        (catalog["density_role"] == "DENSITY_VALIDATION")
        & (catalog["item_role"] == "ITEM_TRAIN")
        & catalog["tmdb_identity_present"]
    ].copy()
    counts = _safe_base_counts(archive_path, set(safe_targets["movieId"].astype(int)), chunksize)
    safe_targets["base_train_q"] = safe_targets["movieId"].map(lambda value: counts.get(int(value), 0)).astype("int64")
    safe_targets["q_bin"] = safe_targets["base_train_q"].map(_q_bin)

    panel_rules = protocol["density_panels"]
    panel_rows: list[dict[str, Any]] = []
    panel_summary: dict[str, Any] = {}
    for panel_name, rule in panel_rules.items():
        if not panel_name.startswith("PANEL_"):
            continue
        minimum = int(rule["minimum_original_base_train_q"])
        panel = safe_targets[safe_targets["base_train_q"] >= minimum]
        fold_counts = {str(fold): int((panel["fold"] == fold).sum()) for fold in range(5)}
        panel_summary[panel_name] = {
            "minimum_original_base_train_q": minimum,
            "safe_target_items": int(len(panel)),
            "fold_counts": fold_counts,
            "train_q": rule["train_q"],
            "control_q": rule["control_q"],
        }
        for row in panel.itertuples(index=False):
            panel_rows.append(
                {
                    "panel": panel_name,
                    "movie_id": int(row.movieId),
                    "base_train_q": int(row.base_train_q),
                    "q_bin": row.q_bin,
                    "fold": int(row.fold),
                    "tmdb_identity_present": bool(row.tmdb_identity_present),
                    "firewall_scope": "SAFE_INTERSECTION_ITEM_TRAIN_X_DENSITY_VALIDATION",
                }
            )
    panel_frame = pd.DataFrame(panel_rows).sort_values(["panel", "fold", "movie_id"])
    panel_frame.to_parquet(output_root / "panel-sample-summary.parquet", index=False, compression="zstd")

    tmdb_manifest = REPO_ROOT / "docs/recommendation/evidence/manifests/rec-ev-019b.json"
    blocker_reasons = []
    if len(protected_collision):
        blocker_reasons.append("NESTED_DENSITY_PARENT_SCOPE_FAILED")
    if not tmdb_manifest.is_file():
        blocker_reasons.append("REC_EV_019B_TMDB_FEATURE_MANIFEST_MISSING")
    status = "BLOCKED" if blocker_reasons else "PASS"

    audit = {
        "schema_version": 1,
        "evidence_id": "REC-EV-021P",
        "protocol_version": protocol["protocol_version"],
        "role": "VALIDATION",
        "status": status,
        "firewall_status": "FAIL" if len(protected_collision) else "PASS",
        "model_run_status": "BLOCKED_MISSING_REC_EV_019B" if not tmdb_manifest.is_file() else "READY_FOR_VALIDATION_PILOT",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_checksums": {"movielens_archive_sha256": actual_archive_sha},
        "catalog_items": int(len(catalog)),
        "item_role_counts": {key: int(value) for key, value in catalog["item_role"].value_counts().sort_index().items()},
        "density_role_counts": {key: int(value) for key, value in catalog["density_role"].value_counts().sort_index().items()},
        "cross_role_matrix": role_matrix_json,
        "protected_density_validation_collisions": int(len(protected_collision)),
        "safe_density_validation_targets_with_tmdb_identity": int(len(safe_targets)),
        "strict_item_validation_identity_items": int(
            ((catalog["item_role"] == "ITEM_VALIDATION") & catalog["tmdb_identity_present"]).sum()
        ),
        "strict_item_locked_test_interactions_read": False,
        "strict_item_validation_interactions_read": False,
        "validation_labels_read": False,
        "target_selection_used_locked_test_rating_value": False,
        "blocker_reasons": blocker_reasons,
        "required_resolution": (
            "Density roles are nested inside strict ITEM_TRAIN. Keep parent-scope assignment in one function and checksum it before model execution."
        ),
        "locked_test_opened": False,
        "adoption_decision": "NO_PRODUCT_POLICY_CHANGE",
    }
    _write_json(output_root / "item-firewall-audit.json", audit)

    density_state_count = 0
    for panel_name, rule in panel_rules.items():
        if not panel_name.startswith("PANEL_"):
            continue
        density_state_count += len(rule["train_q"])
        if panel_name != "PANEL_100P":
            density_state_count += 1
    compute_plan = {
        "schema_version": 1,
        "status": "ESTIMATE_ONLY_BLOCKED_BEFORE_MODEL_RUN" if blocker_reasons else "READY_FOR_PILOT",
        "mask_seeds": MASK_SEEDS,
        "folds": int(protocol["item_cross_fit"]["folds"]),
        "panel_density_states_including_controls": density_state_count,
        "training_configurations_per_collaborative_model": density_state_count * 5 * len(MASK_SEEDS),
        "example_five_collaborative_models_total_fits": density_state_count * 5 * len(MASK_SEEDS) * 5,
        "note": "The exact total remains unlocked until the model-applicability matrix says which collaborative models are valid at each q.",
        "recommended_first_run": "One Validation panel, one mask seed, ALS plus content baseline; measure time and memory before authorizing the full grid.",
        "panels": panel_summary,
    }
    _write_json(output_root / "compute-plan.json", compute_plan)

    contract_path = REPO_ROOT / "docs/recommendation/contracts/rec-ev-021p-artifacts.json"
    protocol_lock = {
        "schema_version": 1,
        "protocol_version": protocol["protocol_version"],
        "protocol_sha256": sha256_file(protocol_path),
        "contract_sha256": sha256_file(contract_path) if contract_path.is_file() else None,
        "source_checksums": {"movielens_archive_sha256": actual_archive_sha},
        "locked_test_opened": False,
        "preflight_status": status,
        "mask_seeds": MASK_SEEDS,
    }
    _write_json(output_root / "protocol-lock.json", protocol_lock)
    return audit


def _manifest(output_root: Path, protocol_path: Path) -> dict[str, Any]:
    audit = json.loads((output_root / "item-firewall-audit.json").read_text(encoding="utf-8"))
    artifacts = []
    for path in sorted(output_root.glob("*")):
        if path.is_file():
            artifacts.append({
                "path": path.relative_to(REPO_ROOT).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            })
    return {
        "schema_version": 1,
        "evidence_id": "REC-EV-021P",
        "status": audit["status"],
        "protocol": protocol_path.relative_to(REPO_ROOT).as_posix(),
        "protocol_sha256": sha256_file(protocol_path),
        "locked_test_opened": False,
        "artifacts": artifacts,
        "validation": {
            "firewall": audit["firewall_status"],
            "model_run": audit["model_run_status"],
            "blocker_reasons": audit["blocker_reasons"],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, default=REPO_ROOT / "docs/recommendation/protocols/rec-eval-content-cold-v2.json")
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--role", default="validation")
    parser.add_argument("--output-root", type=Path, default=REPO_ROOT / "outputs/recommendation-evidence/rec-ev-021p")
    parser.add_argument("--chunksize", type=int, default=1_000_000)
    args = parser.parse_args()
    try:
        build_preflight(args.protocol.resolve(), args.archive.resolve(), args.output_root.resolve(), args.role, args.chunksize)
        manifest = _manifest(args.output_root.resolve(), args.protocol.resolve())
        manifest_path = REPO_ROOT / "docs/recommendation/evidence/manifests/rec-ev-021p.json"
        _write_json(manifest_path, manifest)
        print(json.dumps({"status": manifest["status"], "manifest": str(manifest_path)}, ensure_ascii=False))
        return 0
    except Exception as error:
        print(f"preflight failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
