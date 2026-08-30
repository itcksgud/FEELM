from __future__ import annotations

import hashlib
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from feelm_recommender import (
    ArtifactMetadata,
    ArtifactValidationError,
    ItemIdMapping,
    LocalCandidateStore,
    MappingQuarantine,
    build_candidate_artifacts,
    export_candidate_artifacts,
    export_catalog_mapping,
    export_fixture_artifact_set,
    load_artifact_set,
    validate_candidate_payload,
)
from feelm_recommender.cli import main


MOVIE_A = "00000000-0000-0000-0000-000000000001"
MOVIE_B = "00000000-0000-0000-0000-000000000002"
MOVIE_C = "00000000-0000-0000-0000-000000000003"
MOVIE_D = "00000000-0000-0000-0000-000000000004"
MOVIE_E = "00000000-0000-0000-0000-000000000005"
MOVIE_F = "00000000-0000-0000-0000-000000000006"
MOVIE_G = "00000000-0000-0000-0000-000000000007"


def header(version: str = "catalog-candidate-test-v1") -> dict:
    return {
        "recordType": "artifactHeader",
        "schemaVersion": 1,
        "catalogVersion": version,
        "sourceChecksums": {"movielensArchiveSha256": "a" * 64},
        "sources": [],
    }


def identity(movie_id: str, item_id: int | None) -> dict:
    external_ids = [] if item_id is None else [{
        "source": "MOVIELENS",
        "externalId": str(item_id),
        "verificationStatus": "VERIFIED",
    }]
    return {
        "recordType": "movieIdentity",
        "payload": {
            "movieId": movie_id,
            "identityStatus": "IDENTITY_VERIFIED",
            "externalIds": external_ids,
        },
    }


def projection(
    movie_id: str,
    *,
    visibility: str = "UI_READY",
    identity_status: str = "IDENTITY_VERIFIED",
    media_type: str = "MOVIE",
    deleted: bool = False,
) -> dict:
    return {
        "recordType": "movieProjection",
        "payload": {
            "movieId": movie_id,
            "mediaType": media_type,
            "identityStatus": identity_status,
            "visibilityStatus": visibility,
            "deleted": deleted,
        },
    }


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(item, sort_keys=True) for item in records) + "\n",
        encoding="utf-8",
    )


def make_environment(root: Path, records: list[dict]) -> tuple[Path, Path, Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    catalog = root / "catalog.jsonl"
    write_jsonl(catalog, records)
    mapping = root / "mapping.json"
    metadata = root / "mapping.metadata.json"
    mapping_quarantine = root / "mapping.quarantine.json"
    export_catalog_mapping(
        catalog_path=catalog,
        mapping_path=mapping,
        metadata_path=metadata,
        quarantine_path=mapping_quarantine,
        compatibility_id="candidate-test-family-v1",
    )
    serving_manifest = export_fixture_artifact_set(
        root / "serving",
        mapping_payload=mapping,
        mapping_metadata_path=metadata,
    )
    return catalog, mapping, metadata, serving_manifest


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def store_artifacts(movie_ids: list[str], name: str, root: Path) -> tuple[Path, Path, str]:
    seed = {
        "schemaVersion": 1,
        "candidateSetVersion": "",
        "catalogVersion": f"catalog-{name}",
        "mappingPayloadSha256": "b" * 64,
        "compatibilityId": "store-test-family-v1",
        "producerPolicy": "GLOBAL_VERIFIED_CATALOG_V1",
        "movieIds": sorted(movie_ids),
    }
    version = f"sha256:{hashlib.sha256(canonical(seed)).hexdigest()}"
    payload = {**seed, "candidateSetVersion": version}
    payload_bytes = canonical(payload)
    checksum = hashlib.sha256(payload_bytes).hexdigest()
    quarantine = {
        "schemaVersion": 1,
        "candidateSetVersion": version,
        "candidatePayloadSha256": checksum,
        "catalogVersion": f"catalog-{name}",
        "producerPolicy": "GLOBAL_VERIFIED_CATALOG_V1",
        "sourceRecords": len(movie_ids),
        "acceptedRecords": len(movie_ids),
        "quarantinedRecords": 0,
        "coverageScope": "INPUT_CATALOG_ONLY_NOT_PRODUCTION_COVERAGE",
        "reasonCounts": {},
    }
    payload_path = root / f"{name}.json"
    quarantine_path = root / f"{name}.quarantine.json"
    payload_path.write_bytes(payload_bytes)
    quarantine_path.write_bytes(canonical(quarantine))
    return payload_path, quarantine_path, checksum


class CandidateExportTest(unittest.TestCase):
    def test_all_catalog_mapping_and_model_gates_have_safe_count_only_quarantine(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            records = [header()]
            records.extend(
                identity(movie_id, item_id)
                for movie_id, item_id in (
                    (MOVIE_A, 1), (MOVIE_B, 2), (MOVIE_C, 3), (MOVIE_D, None),
                    (MOVIE_E, 5), (MOVIE_F, 6), (MOVIE_G, None),
                )
            )
            records.extend(
                [
                    projection(MOVIE_A),
                    projection(MOVIE_B),
                    projection(MOVIE_C, visibility="CATALOG_VISIBLE"),
                    projection(MOVIE_D),
                    projection(MOVIE_E),
                    projection(MOVIE_F),
                    projection(MOVIE_F),
                    projection(MOVIE_G),
                ]
            )
            catalog, mapping_path, metadata_path, serving_manifest = make_environment(
                root, records
            )
            mapping_metadata = ArtifactMetadata.load(metadata_path)
            mapping = ItemIdMapping.load(mapping_path, mapping_metadata)
            mapping = ItemIdMapping(
                mapping.mapping_version,
                mapping.source_id_space,
                mapping.target_id_space,
                mapping.by_service_id,
                mapping.by_movielens_id,
                (*mapping.quarantined, MappingQuarantine("SOURCE_ID_CONFLICT", 7, MOVIE_G)),
            )
            active = load_artifact_set(serving_manifest)
            active.core.bias_model.item_counts[5] = 0
            first = build_candidate_artifacts(
                catalog_bytes=catalog.read_bytes(),
                mapping=mapping,
                mapping_metadata=mapping_metadata,
                active_serving_set=active,
            )
            second = build_candidate_artifacts(
                catalog_bytes=catalog.read_bytes(),
                mapping=mapping,
                mapping_metadata=mapping_metadata,
                active_serving_set=active,
            )
            self.assertEqual(first, second)
            payload = validate_candidate_payload(first[0])
            self.assertEqual(payload["movieIds"], [MOVIE_A, MOVIE_B])
            self.assertEqual(
                first[2].reason_counts,
                {
                    "DUPLICATE": 2,
                    "MAPPING_CONFLICT": 1,
                    "MODEL_ITEM_MISSING": 1,
                    "NOT_MAPPED": 1,
                    "NOT_UI_READY": 1,
                },
            )
            report = json.loads(first[1])
            self.assertEqual(report["sourceRecords"], 8)
            self.assertEqual(report["acceptedRecords"], 2)
            report_text = first[1].decode()
            for movie_id in (MOVIE_A, MOVIE_C, MOVIE_D, MOVIE_E, MOVIE_F, MOVIE_G):
                self.assertNotIn(movie_id, report_text)
            self.assertNotIn("movielens", first[0].decode().lower())
            self.assertNotIn(str(root), first[1].decode())

    def test_export_is_byte_identical_and_publishes_validated_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            records = [
                header(), identity(MOVIE_A, 1), identity(MOVIE_B, 2), identity(MOVIE_D, None),
                projection(MOVIE_A), projection(MOVIE_B), projection(MOVIE_D),
            ]
            catalog, mapping, metadata, serving = make_environment(root, records)
            store = root / "store"
            outputs: list[tuple[Path, Path]] = []
            results = []
            for name in ("first", "second"):
                candidate = root / f"{name}.candidate.json"
                quarantine = root / f"{name}.quarantine.json"
                results.append(
                    export_candidate_artifacts(
                        catalog_path=catalog,
                        mapping_payload_path=mapping,
                        mapping_metadata_path=metadata,
                        serving_manifest_path=serving,
                        candidate_path=candidate,
                        quarantine_path=quarantine,
                        store_dir=store,
                    )
                )
                outputs.append((candidate, quarantine))
            self.assertEqual(outputs[0][0].read_bytes(), outputs[1][0].read_bytes())
            self.assertEqual(outputs[0][1].read_bytes(), outputs[1][1].read_bytes())
            self.assertEqual(results[0].candidate_set_version, results[1].candidate_set_version)
            self.assertTrue(results[0].published)
            self.assertEqual(LocalCandidateStore(store).load_active()["movieIds"], [MOVIE_A, MOVIE_B])

    def test_zero_accepted_and_mapping_checksum_failure_do_not_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            records = [header(), identity(MOVIE_A, 1), projection(MOVIE_A)]
            catalog, mapping, metadata, serving = make_environment(root, records)
            candidate = root / "candidate.json"
            quarantine = root / "quarantine.json"
            store = root / "store"
            export_candidate_artifacts(
                catalog_path=catalog,
                mapping_payload_path=mapping,
                mapping_metadata_path=metadata,
                serving_manifest_path=serving,
                candidate_path=candidate,
                quarantine_path=quarantine,
                store_dir=store,
            )
            active_before = (store / "active.json").read_bytes()

            mapping.write_bytes(mapping.read_bytes() + b"x")
            with self.assertRaises(ArtifactValidationError):
                export_candidate_artifacts(
                    catalog_path=catalog,
                    mapping_payload_path=mapping,
                    mapping_metadata_path=metadata,
                    serving_manifest_path=serving,
                    candidate_path=candidate,
                    quarantine_path=quarantine,
                    store_dir=store,
                )
            self.assertEqual((store / "active.json").read_bytes(), active_before)

            zero_root = root / "zero"
            zero_records = [
                header("catalog-zero-v1"),
                identity(MOVIE_A, 1),
                projection(MOVIE_A, visibility="UI_INCOMPLETE"),
            ]
            zero_catalog, zero_mapping, zero_metadata, zero_serving = make_environment(
                zero_root, zero_records
            )
            with self.assertRaisesRegex(ArtifactValidationError, "no accepted"):
                export_candidate_artifacts(
                    catalog_path=zero_catalog,
                    mapping_payload_path=zero_mapping,
                    mapping_metadata_path=zero_metadata,
                    serving_manifest_path=zero_serving,
                    candidate_path=zero_root / "candidate.json",
                    quarantine_path=zero_root / "quarantine.json",
                    store_dir=store,
                )
            self.assertEqual((store / "active.json").read_bytes(), active_before)

    def test_cli_exports_and_inspects_local_store_without_ids_in_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            records = [header(), identity(MOVIE_A, 1), projection(MOVIE_A)]
            catalog, mapping, metadata, serving = make_environment(root, records)
            candidate = root / "candidate.json"
            quarantine = root / "quarantine.json"
            store = root / "store"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main(
                    [
                        "export-batch-candidates",
                        "--catalog", str(catalog),
                        "--mapping", str(mapping),
                        "--mapping-metadata", str(metadata),
                        "--serving-manifest", str(serving),
                        "--candidate", str(candidate),
                        "--quarantine", str(quarantine),
                        "--store-dir", str(store),
                    ]
                )
            self.assertEqual(status, 0)
            report = json.loads(output.getvalue())
            self.assertTrue(report["published"])
            self.assertEqual(report["accepted_records"], 1)
            self.assertNotIn(MOVIE_A, output.getvalue())
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main(["inspect-candidate-store", "--store-dir", str(store)])
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(output.getvalue())["accepted_records"], 1)
            self.assertNotIn(MOVIE_A, output.getvalue())

class LocalCandidateStoreTest(unittest.TestCase):
    def test_active_and_previous_rollback_are_retained_without_ttl(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = LocalCandidateStore(root / "store")
            first = store_artifacts([MOVIE_A, MOVIE_B], "first", root)
            second = store_artifacts([MOVIE_A], "second", root)
            first_pointer = store.publish(first[0], first[1], expected_payload_sha256=first[2])
            self.assertIsNone(store.rollback())
            second_pointer = store.publish(second[0], second[1], expected_payload_sha256=second[2])
            self.assertEqual(store.active(), second_pointer)
            self.assertEqual(store.rollback(), first_pointer)
            self.assertEqual(len(list((root / "store" / "versions").glob("*.json"))), 2)

    def test_publish_checksum_failure_and_active_tamper_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = LocalCandidateStore(root / "store")
            first = store_artifacts([MOVIE_A], "first", root)
            store.publish(first[0], first[1], expected_payload_sha256=first[2])
            active_before = (root / "store" / "active.json").read_bytes()
            with self.assertRaisesRegex(ArtifactValidationError, "checksum"):
                store.publish(first[0], first[1], expected_payload_sha256="0" * 64)
            self.assertEqual((root / "store" / "active.json").read_bytes(), active_before)
            pointer = store.active()
            payload_path = root / "store" / pointer["payload"]
            payload_path.write_bytes(payload_path.read_bytes() + b"x")
            with self.assertRaisesRegex(ArtifactValidationError, "checksum"):
                store.load_active()


if __name__ == "__main__":
    unittest.main()
