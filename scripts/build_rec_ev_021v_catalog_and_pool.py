from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rec_ev_021v_pooled_judgment import (
    DEFAULT_CONTRACT,
    budget_guard,
    build_blind_pool,
    build_catalog,
    load_catalog_csv,
    load_contract,
    read_json,
    read_jsonl,
    validate_frozen_ranking_manifest,
    validate_participants_and_onboarding,
    validate_source_manifest,
    write_json,
    write_jsonl,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an external REC-EV-021V catalog and deterministic blind pool from approved local inputs.")
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--participants", type=Path, required=True)
    parser.add_argument("--onboarding-inputs", type=Path, required=True)
    parser.add_argument("--frozen-ranking-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--approved-budget-krw", type=int, required=True)
    parser.add_argument("--incentive-per-user-krw", type=int, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    try:
        contract = load_contract(args.contract.resolve())
        source_manifest = read_json(args.source_manifest.resolve())
        source_path = validate_source_manifest(source_manifest, contract, fixture_mode=False)
        catalog, catalog_audit = build_catalog(load_catalog_csv(source_path), source_manifest, contract)
        ranking_manifest = read_json(args.frozen_ranking_manifest.resolve())
        ranking_path = validate_frozen_ranking_manifest(ranking_manifest, source_manifest, contract, fixture_mode=False)
        participants = read_jsonl(args.participants.resolve())
        onboarding = read_jsonl(args.onboarding_inputs.resolve())
        rankings = read_jsonl(ranking_path)
        participant_audit = validate_participants_and_onboarding(participants, onboarding, contract)
        budget_audit = budget_guard(
            contract,
            fixture_mode=False,
            participant_count=participant_audit["participants"],
            approved_budget_krw=args.approved_budget_krw,
            incentive_per_user_krw=args.incentive_per_user_krw,
        )
        output_root = args.output_root.resolve()
        pool, sealed, pool_audit = build_blind_pool(
            catalog,
            onboarding,
            rankings,
            contract,
            checkpoint_root=output_root / "checkpoints" / "blind-pool",
            resume=args.resume,
        )
        write_jsonl(output_root / "normalized-catalog.jsonl", catalog)
        write_jsonl(output_root / "blind-pool.jsonl", pool)
        write_jsonl(output_root / "sealed-pool-source.jsonl", sealed)
        summary = {
            "schema_version": 1,
            "evidence_id": "REC-EV-021V-EXTERNAL-INPUT-PREFLIGHT",
            "status": "READY_FOR_SEPARATELY_AUTHORIZED_RECRUITMENT",
            "target_evidence_status": "NO_ACTUAL_TARGET_DOMAIN_EVIDENCE",
            "catalog_audit": catalog_audit,
            "participant_audit": participant_audit,
            "pool_audit": pool_audit,
            "budget_audit": budget_audit,
            "performed": {
                "public_data_download": False,
                "human_recruitment": False,
                "incentive_or_payment": False,
                "pii_storage": False,
                "locked_test_access": False,
                "product_policy_change": False,
            },
            "locked_test_used": False,
            "champion": None,
            "product_policy_updated": False,
        }
        write_json(output_root / "external-input-preflight.json", summary)
        print(json.dumps({"status": summary["status"], "participants": participant_audit["participants"], "pool_rows": pool_audit["pool_rows"]}, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as error:
        print(f"REC-EV-021V catalog/pool build failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
