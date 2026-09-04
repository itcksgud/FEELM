from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from rec_ev_021v_pooled_judgment import (
    DEFAULT_CONTRACT,
    REPO_ROOT,
    SYSTEMS,
    STRATA,
    analyze_judgments,
    build_blind_pool,
    build_catalog,
    canonical_json_bytes,
    import_judgments,
    load_catalog_csv,
    load_contract,
    read_json,
    read_jsonl,
    sha256_file,
    validate_source_manifest,
    validate_frozen_ranking_manifest,
)


DEFAULT_MANIFEST = REPO_ROOT / "docs/recommendation/evidence/manifests/rec-ev-021v-preflight.json"
DEFAULT_OUTPUT = REPO_ROOT / "outputs/recommendation-evidence/rec-ev-021v-preflight"
EXPECTED_SCHEMAS = {
    "docs/recommendation/schemas/rec-ev-021v/catalog-source-manifest.schema.json",
    "docs/recommendation/schemas/rec-ev-021v/frozen-ranking-manifest.schema.json",
    "docs/recommendation/schemas/rec-ev-021v/participant.schema.json",
    "docs/recommendation/schemas/rec-ev-021v/onboarding-input.schema.json",
    "docs/recommendation/schemas/rec-ev-021v/judgment.schema.json",
}
EXPECTED_SOURCE = {
    "scripts/rec_ev_021v_pooled_judgment.py",
    "scripts/run_rec_ev_021v_preflight.py",
    "scripts/build_rec_ev_021v_catalog_and_pool.py",
    "scripts/import_rec_ev_021v_judgments.py",
    "scripts/analyze_rec_ev_021v_judgments.py",
    "scripts/verify_rec_ev_021v_preflight.py",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _verify_records(records: list[dict[str, Any]]) -> None:
    for record in records:
        path = REPO_ROOT / record["path"]
        require(path.is_file(), f"manifest artifact is absent: {record['path']}")
        require(path.stat().st_size == int(record["bytes"]), f"manifest artifact size drift: {record['path']}")
        require(sha256_file(path) == record["sha256"], f"manifest artifact checksum drift: {record['path']}")


def verify(manifest_path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    require(manifest_path.resolve() == DEFAULT_MANIFEST.resolve(), "unexpected REC-EV-021V manifest path")
    contract = load_contract(DEFAULT_CONTRACT)
    manifest = read_json(manifest_path)
    require(manifest["evidence_id"] == "REC-EV-021V-PREFLIGHT", "manifest identity drift")
    require(manifest["status"] == "PASS_INFRASTRUCTURE_READY", "infrastructure preflight did not pass")
    require(manifest["evidence_mode"] == "SYNTHETIC_FIXTURE", "tracked preflight must be synthetic")
    require(manifest["infrastructure_status"] == "READY_FOR_APPROVED_EXTERNAL_INPUTS_AND_RECRUITMENT", "infrastructure status drift")
    require(manifest["target_evidence_status"] == "NO_ACTUAL_TARGET_DOMAIN_EVIDENCE", "target evidence was overstated")
    require(manifest["contract_sha256"] == sha256_file(DEFAULT_CONTRACT), "contract checksum drift")
    require({record["path"] for record in manifest["schemas"]} == EXPECTED_SCHEMAS, "schema inventory drift")
    require({record["path"] for record in manifest["source_code"]} == EXPECTED_SOURCE, "source inventory drift")
    _verify_records(manifest["schemas"])
    _verify_records(manifest["source_code"])
    _verify_records(manifest["artifacts"])
    for schema_record in manifest["schemas"]:
        schema = read_json(REPO_ROOT / schema_record["path"])
        require(schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema", f"schema dialect drift: {schema_record['path']}")
        require(schema.get("additionalProperties") is False, f"schema permits uncontracted fields: {schema_record['path']}")

    fixture_root = DEFAULT_OUTPUT / "fixture-inputs"
    source_manifest = read_json(fixture_root / "catalog-source-manifest.json")
    source_path = validate_source_manifest(source_manifest, contract, fixture_mode=True)
    raw_catalog = load_catalog_csv(source_path)
    catalog, catalog_audit = build_catalog(raw_catalog, source_manifest, contract)
    persisted_catalog = read_jsonl(DEFAULT_OUTPUT / "normalized-catalog.jsonl")
    require(canonical_json_bytes(catalog) == canonical_json_bytes(persisted_catalog), "normalized catalog reconstruction drift")
    participants = read_jsonl(fixture_root / "participants.jsonl")
    onboarding = read_jsonl(fixture_root / "onboarding-inputs.jsonl")
    ranking_manifest = read_json(fixture_root / "frozen-ranking-manifest.json")
    ranking_path = validate_frozen_ranking_manifest(ranking_manifest, source_manifest, contract, fixture_mode=True)
    rankings = read_jsonl(ranking_path)
    judgments = read_jsonl(fixture_root / "judgments.jsonl")
    reconstructed_pool, reconstructed_sealed, pool_audit = build_blind_pool(catalog, onboarding, rankings, contract)
    persisted_pool = read_jsonl(DEFAULT_OUTPUT / "blind-pool.jsonl")
    persisted_sealed = read_jsonl(DEFAULT_OUTPUT / "sealed-pool-source.jsonl")
    require(canonical_json_bytes(reconstructed_pool) == canonical_json_bytes(persisted_pool), "blind pool reconstruction drift")
    require(canonical_json_bytes(reconstructed_sealed) == canonical_json_bytes(persisted_sealed), "sealed pool-source reconstruction drift")
    visible_forbidden = {"model_id", "rank", "effective_score", "selection_source_model", "selection_source_rank", "model_ranks"}
    require(not any(visible_forbidden.intersection(row) for row in persisted_pool), "participant-visible pool leaks model provenance")
    per_user = {}
    for row in persisted_pool:
        per_user.setdefault(row["participant_id"], []).append(row)
    for participant, rows in per_user.items():
        require(len(rows) == 48, f"participant pool size drift: {participant}")
        counts = {stratum: sum(row["stratum"] == stratum for row in rows) for stratum in STRATA}
        require(counts == {stratum: 12 for stratum in STRATA}, f"participant stratum quota drift: {participant}")
    source_counts = {model: sum(row["selection_source_model"] == model for row in persisted_sealed) for model in SYSTEMS}
    require(source_counts == {model: len(participants) * 12 for model in SYSTEMS}, "pool source balance drift")
    checkpoint_files = sorted((DEFAULT_OUTPUT / "checkpoints" / "blind-pool").glob("p_*.json"))
    require(len(checkpoint_files) == len(participants), "checkpoint coverage drift")

    normalized, import_summary = import_judgments(participants, onboarding, judgments, persisted_pool, contract)
    require(canonical_json_bytes(normalized) == canonical_json_bytes(read_jsonl(DEFAULT_OUTPUT / "normalized-judgments.jsonl")), "judgment import reconstruction drift")
    require(canonical_json_bytes(import_summary) == canonical_json_bytes(read_json(DEFAULT_OUTPUT / "import-summary.json")), "import summary reconstruction drift")
    analysis = analyze_judgments(normalized, persisted_sealed, import_summary, contract, evidence_mode="SYNTHETIC_FIXTURE")
    persisted_analysis = read_json(DEFAULT_OUTPUT / "analysis.json")
    require(canonical_json_bytes(analysis) == canonical_json_bytes(persisted_analysis), "analysis reconstruction drift")
    require(analysis["status"] == "INSUFFICIENT_TARGET_DOMAIN_EVIDENCE", "fixture must remain insufficient target evidence")
    require(analysis["actual_target_domain_evidence"] is False, "fixture was mislabeled as target evidence")
    require(analysis["actual_watch_14d_used_in_primary"] is False, "14-day outcome leaked into primary")
    result = read_json(REPO_ROOT / "docs/recommendation/evidence/results/rec-ev-021v-preflight.json")
    require(result["status"] == "PASS_INFRASTRUCTURE_READY", "tracked result infrastructure status drift")
    require(result["target_evidence_status"] == "NO_ACTUAL_TARGET_DOMAIN_EVIDENCE", "tracked result overstates target evidence")
    require(result["analysis_status"] == "INSUFFICIENT_TARGET_DOMAIN_EVIDENCE", "tracked result analysis boundary drift")
    require(result["fixture_is_target_evidence"] is False, "tracked result treats fixture as evidence")
    require(result["checkpoint_resume"]["exact_reconstruction"] is True, "resume dry-run was not verified")
    require(result["performed"] == {
        "consent_collection": False,
        "human_recruitment": False,
        "incentive_or_payment": False,
        "locked_test_access": False,
        "pii_storage": False,
        "product_policy_change": False,
        "public_data_download": False,
    }, "external-action firewall drift")
    for payload in (manifest, analysis, result):
        require(payload.get("locked_test_used") is False, "Locked Test flag drift")
        require(payload.get("champion") is None, "champion must remain null")
        require(payload.get("product_policy_updated") is False, "product policy flag drift")
    return {
        "status": "PASS_REC_EV_021V_PREFLIGHT_VERIFICATION",
        "catalog_items": len(catalog),
        "catalog_mapping_and_dedup_rate": catalog_audit["mapping_and_dedup_rate"],
        "participants": len(participants),
        "pool_rows": pool_audit["pool_rows"],
        "accepted_judgments": import_summary["accepted_unique_judgments"],
        "analysis_status": analysis["status"],
        "infrastructure_status": result["infrastructure_status"],
        "target_evidence_status": result["target_evidence_status"],
        "locked_test_used": False,
        "champion": None,
        "product_policy_updated": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Independently reconstruct the REC-EV-021V synthetic preflight.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.manifest.resolve()), ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as error:
        print(f"REC-EV-021V verification failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
