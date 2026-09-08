"""Adapter gating tests using only fabricated data."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

import rec_ev_042_classified as runner
from test_feelm_classified_evaluation import fixture


class AdapterTests(unittest.TestCase):
    def test_unsealed_labels_decoder_not_called(self):
        c, _ = fixture()
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            (out / "run").mkdir()
            runner.core.write_json(
                out / "run/selection-seal.json", {"status": "BROKEN"}
            )
            with patch.object(
                runner.pd, "read_parquet", side_effect=AssertionError("decoder called")
            ):
                with self.assertRaisesRegex(ValueError, "UNSEALED"):
                    runner.load_labels_after_seal({}, c, out)

    def test_stale_review_prevents_data_access(self):
        with patch.object(runner, "read_json", side_effect=[{}, {"status": "PENDING"}]):
            with patch.object(
                runner, "verify_sources", side_effect=AssertionError("source opened")
            ):
                with self.assertRaisesRegex(ValueError, "STALE"):
                    runner.main()

    def test_resource_budget_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            for seconds, memory in [(-1, 10**15), (1000, 1)]:
                b = runner.Budget(Path(directory), seconds, memory)
                with self.assertRaisesRegex(ValueError, "RESOURCE_BUDGET"):
                    b.check()

    def test_label_reordering_after_seal(self):
        c, labels = fixture()
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            np.savez(out / "candidates.npz", **c)
            runner.core.sealed_select(out / "candidates.npz", out / "run")
            np.savez(
                out / "hist.npz",
                user_keys=labels["user_keys"],
                histograms=labels["histograms"],
            )
            frame = pd.DataFrame(
                dict(
                    user_key=np.repeat(c["user_keys"], 6),
                    movie_id=c["movie_ids"],
                    rating_raw=labels["rating_raw"],
                    q=labels["source_q"],
                )
            ).iloc[::-1]
            frame.to_parquet(out / "labels.parquet", index=False)
            config = {
                "inputs": {
                    "labels": {"path": str(out / "labels.parquet")},
                    "histograms": {"path": str(out / "hist.npz")},
                }
            }
            actual = runner.load_labels_after_seal(config, c, out)
            for name, value in labels.items():
                np.testing.assert_array_equal(actual[name], value)


if __name__ == "__main__":
    unittest.main()
