"""Synthetic contract tests for the GKT staging exporter; no full source export."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_gkt_staging_bundle_v1 as gkt


class GktStagingBundleTests(unittest.TestCase):
    def test_id_normalization_rejects_bool_fraction_and_nonpositive(self):
        values, invalid, reason = gkt.normalize_positive_ids([3, 3, True, 2.0, -1, np.int64(7), "9"])
        self.assertEqual(values, [3, 7])
        self.assertEqual(invalid, 4)
        self.assertIsNone(reason)
        self.assertEqual(gkt.normalize_positive_ids("3"), ([], 0, "INVALID_TYPE"))
        self.assertEqual(gkt.normalize_positive_ids(None), ([], 0, None))

    def test_overview_contract_only_strips_outer_whitespace(self):
        self.assertEqual(gkt.normalize_overview("  A  B \n"), ("A  B", None))
        self.assertEqual(gkt.normalize_overview(None), ("", None))
        self.assertEqual(gkt.normalize_overview(["text"]), ("", "INVALID_TYPE"))

    def test_l2_zero_and_threshold_boundary(self):
        rows = np.array([[0.0, 0.0], [3.0, 4.0], [1e-13, 0.0]])
        actual = gkt.l2_rows(rows)
        np.testing.assert_array_equal(actual[0], [0.0, 0.0])
        np.testing.assert_allclose(actual[1], [0.6, 0.8], atol=0, rtol=0)
        np.testing.assert_array_equal(actual[2], [0.0, 0.0])

    def test_fixed_axis_nearest_uses_lowest_id_and_is_batch_invariant(self):
        centers = np.array([[-1.0, 0.0], [1.0, 0.0], [5.0, 0.0]])
        points = np.array([[0.0, 0.0], [4.5, 0.0], [-0.9, 0.0], [1.1, 0.0]])
        one = gkt.fixed_axis_nearest(points, centers, batch_size=1)[0]
        many = gkt.fixed_axis_nearest(points, centers, batch_size=20)[0]
        np.testing.assert_array_equal(one, many)
        self.assertEqual(int(one[0]), 0)

    def test_assignment_formula_keeps_top_and_child_axes(self):
        top_centers = np.array([[0.0, 0.0], [10.0, 0.0]])
        child_centers = np.array([
            [[0.0, 0.0], [0.0, 2.0]],
            [[10.0, 0.0], [10.0, 2.0]],
        ])
        top = np.array([[0.0, 0.0], [10.0, 0.0]])
        content = np.array([[0.0, 1.9], [10.0, 0.1]])
        taste, child, group = gkt.assign_chunk(
            top, content, top_centers, child_centers, batch_size=1
        )
        np.testing.assert_array_equal(taste, [0, 1])
        np.testing.assert_array_equal(child, [1, 0])
        np.testing.assert_array_equal(group, [1, 2])

    def test_staging_mapping_never_claims_current_service_ids(self):
        index = gkt.staging_index(
            np.array([11, 12]),
            np.array([101, 102]),
            np.array([0, 1]),
            np.array([3, 4]),
            np.array([3, 132]),
            np.array([True, False]),
            np.array([True, True]),
        )
        self.assertEqual(tuple(index.columns), gkt.ALLOWED_INDEX_COLUMNS)
        self.assertEqual(index.referenceGroupMapped.tolist(), [True, False])
        self.assertEqual(index.serviceIdMapped.tolist(), [False, False])
        self.assertEqual(index.groupMapped.tolist(), [False, False])

    def test_raw_group_mean_is_not_normalized_by_helpers(self):
        means = np.array([[2.0, 0.0], [0.0, 0.]])
        preserved = np.asarray(means, dtype=np.float64)
        np.testing.assert_array_equal(preserved, means)
        self.assertNotEqual(float(np.linalg.norm(preserved[0])), 1.0)

    def test_review_gate_binds_exact_script_and_recipe_fingerprint(self):
        fingerprint = {"scriptSha256": "a", "groupRecipeSha256": "b", "similarRecipeSha256": "c"}
        review = {
            "schemaVersion": gkt.REVIEW_SCHEMA_VERSION,
            "status": "PASS",
            "scope": "FULL_REFERENCE_EXPORT",
            "reviewer": "independent-session",
            "reviewedFingerprint": fingerprint,
        }
        gkt.validate_review(review, fingerprint)
        changed = dict(fingerprint, scriptSha256="changed")
        with self.assertRaises(gkt.ContractError):
            gkt.validate_review(review, changed)

    def test_pair_score_cast_audit_is_bounded_and_does_not_materialize_pairs(self):
        source = gkt.l2_rows(np.array([[1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]))
        exported = source.astype(np.float32)
        result = gkt._pair_score_cast_audit(source, exported, sample_size=3)
        self.assertEqual(result["samplePairs"], 3)
        self.assertLessEqual(result["maximumCosineDifference"], 1e-6)
        self.assertNotIn("neighbors", json.dumps(result).lower())

    def test_memmap_is_released_before_directory_publish(self):
        with tempfile.TemporaryDirectory() as root_value:
            root = Path(root_value)
            temporary = root / ".bundle.tmp"
            output = root / "bundle"
            temporary.mkdir()
            mapped = np.lib.format.open_memmap(
                temporary / "vectors.npy", mode="w+", dtype=np.float32, shape=(2, 2)
            )
            mapped[:] = [[1.0, 0.0], [0.0, 1.0]]
            gkt._publish_staged_directory(temporary, output, (mapped,))
            self.assertFalse(temporary.exists())
            self.assertTrue((output / "vectors.npy").is_file())
            np.testing.assert_array_equal(
                np.load(output / "vectors.npy", allow_pickle=False),
                np.eye(2, dtype=np.float32),
            )

    def test_memmap_is_released_before_failed_run_cleanup(self):
        with tempfile.TemporaryDirectory() as root_value:
            root = Path(root_value)
            temporary = root / ".bundle.tmp"
            output = root / "bundle"
            temporary.mkdir()
            mapped = np.lib.format.open_memmap(
                temporary / "vectors.npy", mode="w+", dtype=np.float32, shape=(1, 1)
            )
            mapped[0, 0] = 1.0
            gkt._cleanup_staged_directory(temporary, (mapped,))
            self.assertFalse(temporary.exists())
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
