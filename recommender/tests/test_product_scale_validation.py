from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from feelm_recommender import (
    ArtifactValidationError,
    export_product_scale_validation_pairs,
)


def pair(
    prediction_id: str,
    *,
    split: str,
    predicted_at: str,
    rated_at: str,
) -> dict:
    return {
        "prediction_id": prediction_id,
        "predicted_at": predicted_at,
        "rated_at": rated_at,
        "model_scale_prediction": 3.25,
        "actual_c1_rating": 4,
        "k": 10,
        "model_version": "candidate-not-champion-v1",
        "artifact_set_version": "artifact-set-v1",
        "policy_version": "cold-start-dual-head-blend-v1",
        "split": split,
    }


class ProductScaleValidationExportTest(unittest.TestCase):
    def _source(self, directory: Path, records: list[dict]) -> Path:
        source = directory / "source.json"
        source.write_text(json.dumps(records), encoding="utf-8")
        return source

    def test_export_is_deterministic_deidentified_and_temporally_separated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            records = [
                pair(
                    "00000000-0000-0000-0000-000000000002",
                    split="VALIDATION",
                    predicted_at="2026-02-01T00:00:00Z",
                    rated_at="2026-02-02T00:00:00Z",
                ),
                pair(
                    "00000000-0000-0000-0000-000000000001",
                    split="CALIBRATION",
                    predicted_at="2026-01-01T00:00:00Z",
                    rated_at="2026-01-02T00:00:00Z",
                ),
            ]
            source = self._source(directory, records)
            outputs: list[tuple[Path, Path]] = []
            for name in ("first", "second"):
                payload = directory / f"{name}.json"
                metadata = directory / f"{name}.metadata.json"
                result = export_product_scale_validation_pairs(
                    source_path=source,
                    payload_path=payload,
                    metadata_path=metadata,
                    dataset_version="c1-scale-test-v1",
                )
                self.assertEqual(result.calibration_records, 1)
                self.assertEqual(result.validation_records, 1)
                outputs.append((payload, metadata))
            self.assertEqual(outputs[0][0].read_bytes(), outputs[1][0].read_bytes())
            self.assertEqual(outputs[0][1].read_bytes(), outputs[1][1].read_bytes())
            artifact = json.loads(outputs[0][0].read_text(encoding="utf-8"))
            self.assertFalse(artifact["contains_user_or_movie_ids"])
            self.assertEqual(artifact["records"][0]["split"], "CALIBRATION")
            for record in artifact["records"]:
                self.assertNotIn("user_id", record)
                self.assertNotIn("movie_id", record)

    def test_export_rejects_extra_identity_fields_and_scale_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            valid = pair(
                "00000000-0000-0000-0000-000000000001",
                split="CALIBRATION",
                predicted_at="2026-01-01T00:00:00Z",
                rated_at="2026-01-02T00:00:00Z",
            )
            valid["user_id"] = "must-not-export"
            source = self._source(directory, [valid])
            with self.assertRaisesRegex(ArtifactValidationError, "allowlisted"):
                export_product_scale_validation_pairs(
                    source_path=source,
                    payload_path=directory / "payload.json",
                    metadata_path=directory / "metadata.json",
                    dataset_version="test-v1",
                )

    def test_export_rejects_leaking_calibration_into_validation_time(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = self._source(
                directory,
                [
                    pair(
                        "00000000-0000-0000-0000-000000000001",
                        split="CALIBRATION",
                        predicted_at="2026-02-01T00:00:00Z",
                        rated_at="2026-02-03T00:00:00Z",
                    ),
                    pair(
                        "00000000-0000-0000-0000-000000000002",
                        split="VALIDATION",
                        predicted_at="2026-02-02T00:00:00Z",
                        rated_at="2026-02-04T00:00:00Z",
                    ),
                ],
            )
            with self.assertRaisesRegex(ArtifactValidationError, "precede"):
                export_product_scale_validation_pairs(
                    source_path=source,
                    payload_path=directory / "payload.json",
                    metadata_path=directory / "metadata.json",
                    dataset_version="test-v1",
                )


if __name__ == "__main__":
    unittest.main()
