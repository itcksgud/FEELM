from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

from rec_ev_021v_pooled_judgment import (
    DEFAULT_CONTRACT,
    REPO_ROOT,
    analyze_judgments,
    artifact_records,
    budget_guard,
    build_blind_pool,
    build_catalog,
    expected_frozen_system_provenance,
    import_judgments,
    load_contract,
    sha256_file,
    synthetic_fixture,
    synthetic_judgments,
    validate_frozen_ranking_manifest,
    validate_source_manifest,
    write_json,
    write_jsonl,
)


DEFAULT_OUTPUT = REPO_ROOT / "outputs/recommendation-evidence/rec-ev-021v-preflight"
DEFAULT_MANIFEST = REPO_ROOT / "docs/recommendation/evidence/manifests/rec-ev-021v-preflight.json"
DEFAULT_RESULT = REPO_ROOT / "docs/recommendation/evidence/results/rec-ev-021v-preflight.json"
SCHEMA_PATHS = [
    REPO_ROOT / "docs/recommendation/schemas/rec-ev-021v/catalog-source-manifest.schema.json",
    REPO_ROOT / "docs/recommendation/schemas/rec-ev-021v/frozen-ranking-manifest.schema.json",
    REPO_ROOT / "docs/recommendation/schemas/rec-ev-021v/participant.schema.json",
    REPO_ROOT / "docs/recommendation/schemas/rec-ev-021v/onboarding-input.schema.json",
    REPO_ROOT / "docs/recommendation/schemas/rec-ev-021v/judgment.schema.json",
]
SOURCE_PATHS = [
    REPO_ROOT / "scripts/rec_ev_021v_pooled_judgment.py",
    REPO_ROOT / "scripts/run_rec_ev_021v_preflight.py",
    REPO_ROOT / "scripts/build_rec_ev_021v_catalog_and_pool.py",
    REPO_ROOT / "scripts/import_rec_ev_021v_judgments.py",
    REPO_ROOT / "scripts/analyze_rec_ev_021v_judgments.py",
    REPO_ROOT / "scripts/verify_rec_ev_021v_preflight.py",
]


def _write_catalog_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = ["movie_key", "display_title", "release_date", "origin_country_codes", "popularity_value", "mapping_status"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def run_fixture(output_root: Path, manifest_path: Path, result_path: Path, *, resume: bool) -> dict[str, Any]:
    contract = load_contract(DEFAULT_CONTRACT)
    fixture = synthetic_fixture(contract)
    fixture_root = output_root / "fixture-inputs"
    catalog_source_path = fixture_root / "catalog.csv"
    _write_catalog_csv(catalog_source_path, fixture["catalog_rows"])
    source_manifest = {
        "schema_version": 1,
        "source_id": "REC_EV_021V_SYNTHETIC_CATALOG",
        "snapshot_version": "synthetic-v1",
        "retrieved_at_utc": "2026-09-05T00:00:00+00:00",
        "catalog_as_of_date": "2026-09-05",
        "license": {
            "license_id": "SYNTHETIC_FIXTURE_ONLY",
            "license_url": "https://example.invalid/rec-ev-021v-synthetic-fixture",
            "research_use_status": "APPROVED",
            "redistribution_status": "ALLOWED",
            "attribution": "Generated deterministic fixture; not a public or target-domain dataset."
        },
        "local_artifact": {
            "path": str(catalog_source_path.resolve()),
            "bytes": catalog_source_path.stat().st_size,
            "sha256": sha256_file(catalog_source_path),
        },
        "popularity_rule": {
            "field": "popularity_value",
            "low_pop_max_inclusive": 25.0,
            "popular_min_inclusive": 75.0,
            "frozen_before_judgments": True,
        },
        "synthetic_fixture": True,
    }
    source_manifest_path = fixture_root / "catalog-source-manifest.json"
    write_json(source_manifest_path, source_manifest)
    validate_source_manifest(source_manifest, contract, fixture_mode=True)
    catalog, catalog_audit = build_catalog(fixture["catalog_rows"], source_manifest, contract)
    participants_path = fixture_root / "participants.jsonl"
    onboarding_path = fixture_root / "onboarding-inputs.jsonl"
    rankings_path = fixture_root / "frozen-rankings.jsonl"
    write_jsonl(participants_path, fixture["participants"])
    write_jsonl(onboarding_path, fixture["onboarding"])
    write_jsonl(rankings_path, fixture["rankings"])
    ranking_manifest = {
        "schema_version": 1,
        "created_at_utc": "2026-09-05T00:00:00+00:00",
        "catalog_source_sha256": source_manifest["local_artifact"]["sha256"],
        "rankings_artifact": {
            "path": str(rankings_path.resolve()),
            "bytes": rankings_path.stat().st_size,
            "sha256": sha256_file(rankings_path),
        },
        "systems": expected_frozen_system_provenance(contract),
        "selected_before_judgments": True,
        "fit_or_refit_performed": False,
        "synthetic_fixture": True,
    }
    ranking_manifest_path = fixture_root / "frozen-ranking-manifest.json"
    write_json(ranking_manifest_path, ranking_manifest)
    validate_frozen_ranking_manifest(ranking_manifest, source_manifest, contract, fixture_mode=True)
    budget = budget_guard(contract, fixture_mode=True, participant_count=len(fixture["participants"]))
    checkpoint_root = output_root / "checkpoints" / "blind-pool"
    pool, sealed, pool_audit = build_blind_pool(
        catalog,
        fixture["onboarding"],
        fixture["rankings"],
        contract,
        checkpoint_root=checkpoint_root,
        resume=resume,
    )
    # The second pass proves that checkpoints are sufficient to reconstruct the exact pool.
    resumed_pool, resumed_sealed, resume_audit = build_blind_pool(
        catalog,
        fixture["onboarding"],
        fixture["rankings"],
        contract,
        checkpoint_root=checkpoint_root,
        resume=True,
    )
    if pool != resumed_pool or sealed != resumed_sealed:
        raise RuntimeError("blind-pool resume reconstruction drift")
    catalog_path = output_root / "normalized-catalog.jsonl"
    pool_path = output_root / "blind-pool.jsonl"
    sealed_path = output_root / "sealed-pool-source.jsonl"
    write_jsonl(catalog_path, catalog)
    write_jsonl(pool_path, pool)
    write_jsonl(sealed_path, sealed)
    judgments = synthetic_judgments(pool)
    judgments_path = fixture_root / "judgments.jsonl"
    write_jsonl(judgments_path, judgments)
    normalized, import_summary = import_judgments(
        fixture["participants"], fixture["onboarding"], judgments, pool, contract
    )
    normalized_path = output_root / "normalized-judgments.jsonl"
    import_summary_path = output_root / "import-summary.json"
    write_jsonl(normalized_path, normalized)
    write_json(import_summary_path, import_summary)
    analysis = analyze_judgments(normalized, sealed, import_summary, contract, evidence_mode="SYNTHETIC_FIXTURE")
    analysis_path = output_root / "analysis.json"
    write_json(analysis_path, analysis)
    preflight = {
        "schema_version": 1,
        "evidence_id": "REC-EV-021V-PREFLIGHT",
        "status": "PASS_INFRASTRUCTURE_READY",
        "infrastructure_status": "READY_FOR_APPROVED_EXTERNAL_INPUTS_AND_RECRUITMENT",
        "target_evidence_status": "NO_ACTUAL_TARGET_DOMAIN_EVIDENCE",
        "analysis_status": analysis["status"],
        "fixture_is_target_evidence": False,
        "mode": "SYNTHETIC_FIXTURE",
        "catalog_audit": catalog_audit,
        "pool_audit": pool_audit,
        "checkpoint_resume": {
            "checkpoint_after_each_participant": True,
            "resumed_participants": resume_audit["resumed_participants"],
            "exact_reconstruction": True,
        },
        "import_summary": import_summary,
        "budget_guard": budget,
        "required_before_external_collection": [
            "Owner-approved public catalog source, local snapshot, checksum, license, attribution, and frozen popularity rule",
            "Owner-approved consent text/version, ethics/privacy review, retention and deletion procedure",
            "Explicit recruitment authorization, incentive amount, and approved KRW budget cap",
            "Locally supplied deidentified KR-resident participants and K10 inputs",
            "Frozen B0/B7-E5/B8-LightFM/B9-RRF rankings for the approved catalog snapshot",
            "A separate collection system that never exports name, email, phone, address, IP, device, or payment identifiers",
        ],
        "performed": {
            "public_data_download": False,
            "human_recruitment": False,
            "consent_collection": False,
            "incentive_or_payment": False,
            "pii_storage": False,
            "locked_test_access": False,
            "product_policy_change": False,
        },
        "locked_test_used": False,
        "champion": None,
        "product_policy_updated": False,
    }
    preflight_path = output_root / "preflight-summary.json"
    write_json(preflight_path, preflight)
    write_json(result_path, preflight)
    key_artifacts = [
        source_manifest_path,
        catalog_source_path,
        participants_path,
        onboarding_path,
        rankings_path,
        ranking_manifest_path,
        judgments_path,
        catalog_path,
        pool_path,
        sealed_path,
        normalized_path,
        import_summary_path,
        analysis_path,
        preflight_path,
        result_path,
    ]
    manifest = {
        "schema_version": 1,
        "evidence_id": "REC-EV-021V-PREFLIGHT",
        "status": "PASS_INFRASTRUCTURE_READY",
        "evidence_mode": "SYNTHETIC_FIXTURE",
        "infrastructure_status": preflight["infrastructure_status"],
        "target_evidence_status": preflight["target_evidence_status"],
        "contract": DEFAULT_CONTRACT.relative_to(REPO_ROOT).as_posix(),
        "contract_sha256": sha256_file(DEFAULT_CONTRACT),
        "schemas": artifact_records(SCHEMA_PATHS),
        "source_code": artifact_records(SOURCE_PATHS),
        "artifacts": artifact_records(key_artifacts),
        "locked_test_used": False,
        "champion": None,
        "product_policy_updated": False,
    }
    write_json(manifest_path, manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the fail-closed REC-EV-021V recruitment preflight.")
    parser.add_argument("--mode", choices=["fixture", "external"], default="fixture")
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    try:
        if args.contract.resolve() != DEFAULT_CONTRACT.resolve():
            raise RuntimeError("unexpected REC-EV-021V contract path")
        if args.mode == "external":
            raise RuntimeError(
                "external mode is fail-closed in this command: supply approved local inputs to the dedicated importer/analyzer after catalog-license, consent, privacy, recruitment, and budget approvals"
            )
        manifest = run_fixture(args.output_root.resolve(), args.manifest.resolve(), args.result.resolve(), resume=args.resume)
        print(json.dumps({
            "status": manifest["status"],
            "infrastructure_status": manifest["infrastructure_status"],
            "target_evidence_status": manifest["target_evidence_status"],
            "manifest": str(args.manifest.resolve()),
        }, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as error:
        print(f"REC-EV-021V preflight failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
