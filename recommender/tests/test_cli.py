from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from feelm_recommender.cli import main


MOVIE_A = "00000000-0000-0000-0000-000000000001"


class CliTest(unittest.TestCase):
    def test_inspect_reports_validated_metadata_without_payload_contents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            payload = directory / "payload.npz"
            payload.write_bytes(b"test-payload")
            metadata = directory / "metadata.json"
            metadata.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "artifact_kind": "regularized-bias-v1",
                        "model_version": "bias-test",
                        "model_status": "VALIDATED_BASELINE",
                        "evidence_id": "REC-EV-002",
                        "run_id": "EXP-TEST",
                        "compatibility_id": "test-family",
                        "id_space": "movielens-int-v1",
                        "payload_sha256": hashlib.sha256(b"test-payload").hexdigest(),
                        "parameters": {
                            "reg_user": 10,
                            "reg_item": 25,
                            "iterations": 10,
                            "popularity_prior_count": 50,
                        },
                    }
                ),
                encoding="utf-8",
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = main(
                    ["inspect", "--metadata", str(metadata), "--payload", str(payload)]
                )
            self.assertEqual(result, 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["model_version"], "bias-test")

    def test_export_catalog_mapping_cli_reports_input_scoped_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            catalog = directory / "catalog.jsonl"
            catalog.write_text(
                "\n".join(
                    json.dumps(record)
                    for record in (
                        {
                            "recordType": "artifactHeader",
                            "schemaVersion": 1,
                            "catalogVersion": "catalog-cli-v1",
                            "sourceChecksums": {
                                "movielensArchiveSha256": "a" * 64
                            },
                            "sources": [],
                        },
                        {
                            "recordType": "movieIdentity",
                            "payload": {
                                "movieId": MOVIE_A,
                                "identityStatus": "IDENTITY_VERIFIED",
                                "externalIds": [
                                    {
                                        "source": "MOVIELENS",
                                        "externalId": "1",
                                        "verificationStatus": "VERIFIED",
                                    }
                                ],
                            },
                        },
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            mapping = directory / "mapping.json"
            metadata = directory / "mapping.metadata.json"
            quarantine = directory / "mapping.quarantine.json"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = main(
                    [
                        "export-catalog-mapping",
                        "--catalog",
                        str(catalog),
                        "--mapping",
                        str(mapping),
                        "--metadata",
                        str(metadata),
                        "--quarantine",
                        str(quarantine),
                        "--compatibility-id",
                        "test-family-v1",
                    ]
                )
            self.assertEqual(result, 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["accepted_records"], 1)
            self.assertEqual(
                report["coverage_scope"], "INPUT_CATALOG_ONLY_NOT_PRODUCTION_COVERAGE"
            )

    def test_export_product_scale_validation_cli_is_deidentified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "source.json"
            base = {
                "model_scale_prediction": 3.25,
                "actual_c1_rating": 4,
                "k": 10,
                "model_version": "candidate-not-champion-v1",
                "artifact_set_version": "artifact-set-v1",
                "policy_version": "cold-start-dual-head-blend-v1",
            }
            source.write_text(
                json.dumps(
                    [
                        {
                            **base,
                            "prediction_id": "00000000-0000-0000-0000-000000000001",
                            "predicted_at": "2026-01-01T00:00:00Z",
                            "rated_at": "2026-01-02T00:00:00Z",
                            "split": "CALIBRATION",
                        },
                        {
                            **base,
                            "prediction_id": "00000000-0000-0000-0000-000000000002",
                            "predicted_at": "2026-02-01T00:00:00Z",
                            "rated_at": "2026-02-02T00:00:00Z",
                            "split": "VALIDATION",
                        },
                    ]
                ),
                encoding="utf-8",
            )
            payload = directory / "pairs.json"
            metadata = directory / "pairs.metadata.json"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = main(
                    [
                        "export-product-scale-validation",
                        "--source",
                        str(source),
                        "--payload",
                        str(payload),
                        "--metadata",
                        str(metadata),
                        "--dataset-version",
                        "c1-scale-test-v1",
                    ]
                )
            self.assertEqual(result, 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report["status"], "PASS")
            self.assertFalse(report["contains_user_or_movie_ids"])


if __name__ == "__main__":
    unittest.main()
