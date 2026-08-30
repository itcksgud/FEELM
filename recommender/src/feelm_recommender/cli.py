from __future__ import annotations

import argparse
import json
from pathlib import Path

from .artifact_set import assemble_artifact_set, export_fixture_artifact_set, load_artifact_set
from .catalog_mapping_export import export_catalog_mapping
from .candidate_export import LocalCandidateStore, export_candidate_artifacts
from .metadata import ArtifactMetadata
from .product_scale_validation import export_product_scale_validation_pairs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="feelm-recommender")
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser(
        "inspect", help="validate metadata and its payload checksum"
    )
    inspect_parser.add_argument("--metadata", type=Path, required=True)
    inspect_parser.add_argument("--payload", type=Path, required=True)
    export_parser = subparsers.add_parser(
        "export-catalog-mapping",
        help="export a deterministic MovieLens-to-service mapping from Catalog JSONL v1",
    )
    export_parser.add_argument("--catalog", type=Path, required=True)
    export_parser.add_argument("--mapping", type=Path, required=True)
    export_parser.add_argument("--metadata", type=Path, required=True)
    export_parser.add_argument("--quarantine", type=Path, required=True)
    export_parser.add_argument("--compatibility-id", required=True)
    scale_parser = subparsers.add_parser(
        "export-product-scale-validation",
        help="export de-identified paired C1 labels for product-scale calibration evidence",
    )
    scale_parser.add_argument("--source", type=Path, required=True)
    scale_parser.add_argument("--payload", type=Path, required=True)
    scale_parser.add_argument("--metadata", type=Path, required=True)
    scale_parser.add_argument("--dataset-version", required=True)
    fixture_parser = subparsers.add_parser(
        "export-serving-fixture",
        help="export a deterministic complete C2A fixture artifact set",
    )
    fixture_parser.add_argument("--output-dir", type=Path, required=True)
    fixture_parser.add_argument("--mapping", type=Path)
    fixture_parser.add_argument("--mapping-metadata", type=Path)
    assemble_parser = subparsers.add_parser(
        "assemble-serving-set",
        help="copy and validate four evidence-bounded serving artifacts",
    )
    assemble_parser.add_argument("--output-dir", type=Path, required=True)
    assemble_parser.add_argument("--catalog-version", required=True)
    assemble_parser.add_argument("--coverage", default="EVIDENCE_BOUNDED_ARTIFACT_SET_NOT_PRODUCTION_APPROVAL")
    for name in ("bias", "factors", "calibration", "mapping"):
        assemble_parser.add_argument(f"--{name}", type=Path, required=True)
        assemble_parser.add_argument(f"--{name}-metadata", type=Path, required=True)
    validate_parser = subparsers.add_parser(
        "validate-serving-set", help="validate a complete serving set and its dry-run"
    )
    validate_parser.add_argument("--manifest", type=Path, required=True)
    candidate_parser = subparsers.add_parser(
        "export-batch-candidates",
        help="export and optionally publish GLOBAL_VERIFIED_CATALOG_V1 candidates",
    )
    candidate_parser.add_argument("--catalog", type=Path, required=True)
    candidate_parser.add_argument("--mapping", type=Path, required=True)
    candidate_parser.add_argument("--mapping-metadata", type=Path, required=True)
    candidate_parser.add_argument("--serving-manifest", type=Path, required=True)
    candidate_parser.add_argument("--candidate", type=Path, required=True)
    candidate_parser.add_argument("--quarantine", type=Path, required=True)
    candidate_parser.add_argument("--store-dir", type=Path)
    store_parser = subparsers.add_parser(
        "inspect-candidate-store",
        help="validate and report the local store active candidate without movie IDs",
    )
    store_parser.add_argument("--store-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "export-catalog-mapping":
        result = export_catalog_mapping(
            catalog_path=args.catalog,
            mapping_path=args.mapping,
            metadata_path=args.metadata,
            quarantine_path=args.quarantine,
            compatibility_id=args.compatibility_id,
        )
        print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "export-product-scale-validation":
        result = export_product_scale_validation_pairs(
            source_path=args.source,
            payload_path=args.payload,
            metadata_path=args.metadata,
            dataset_version=args.dataset_version,
        )
        print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "export-serving-fixture":
        manifest = export_fixture_artifact_set(
            args.output_dir,
            mapping_payload=args.mapping,
            mapping_metadata_path=args.mapping_metadata,
        )
        loaded = load_artifact_set(manifest)
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "artifact_set_version": loaded.artifact_set_version,
                    "set_kind": loaded.set_kind,
                    "coverage": loaded.coverage,
                    "manifest": str(manifest),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "assemble-serving-set":
        manifest = assemble_artifact_set(
            args.output_dir,
            artifacts={
                name: (getattr(args, name), getattr(args, f"{name}_metadata"))
                for name in ("bias", "factors", "calibration", "mapping")
            },
            catalog_version=args.catalog_version,
            coverage=args.coverage,
        )
        loaded = load_artifact_set(manifest)
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "artifact_set_version": loaded.artifact_set_version,
                    "set_kind": loaded.set_kind,
                    "coverage": loaded.coverage,
                    "manifest": str(manifest),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "validate-serving-set":
        loaded = load_artifact_set(args.manifest)
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "artifact_set_version": loaded.artifact_set_version,
                    "set_kind": loaded.set_kind,
                    "coverage": loaded.coverage,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "export-batch-candidates":
        result = export_candidate_artifacts(
            catalog_path=args.catalog,
            mapping_payload_path=args.mapping,
            mapping_metadata_path=args.mapping_metadata,
            serving_manifest_path=args.serving_manifest,
            candidate_path=args.candidate,
            quarantine_path=args.quarantine,
            store_dir=args.store_dir,
        )
        print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "inspect-candidate-store":
        store = LocalCandidateStore(args.store_dir)
        active = store.active()
        payload = store.load_active()
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "candidate_set_version": payload["candidateSetVersion"],
                    "catalog_version": payload["catalogVersion"],
                    "accepted_records": len(payload["movieIds"]),
                    "payload_sha256": active["payloadSha256"],
                    "coverage_scope": "INPUT_CATALOG_ONLY_NOT_PRODUCTION_COVERAGE",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    metadata = ArtifactMetadata.load(args.metadata)
    metadata.verify_payload(args.payload)
    print(
        json.dumps(
            {
                "status": "PASS",
                "artifact_kind": metadata.artifact_kind.value,
                "model_version": metadata.model_version,
                "model_status": metadata.model_status.value,
                "evidence_id": metadata.evidence_id,
                "compatibility_id": metadata.compatibility_id,
                "id_space": metadata.id_space,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
