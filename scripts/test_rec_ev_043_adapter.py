import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import rec_ev_043_structure as runner


class AdapterTests(unittest.TestCase):
    def test_stale_review_prevents_sources(self):
        with patch.object(runner, "read_json", side_effect=[{}, {"status": "PENDING"}]):
            with patch.object(
                runner, "verify_inputs", side_effect=AssertionError("opened")
            ):
                with self.assertRaisesRegex(ValueError, "STALE_REVIEW"):
                    runner.main()

    def test_missing_prediction_seal_blocks_label_decoder(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                runner.pd, "read_parquet", side_effect=AssertionError("decoded")
            ):
                with self.assertRaises(FileNotFoundError):
                    runner.labels_after_seal({}, {}, Path(directory))

    def test_wall_and_memory_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            for seconds, memory in [(-1, 10**15), (1000, 1)]:
                b = runner.Budget(Path(directory), seconds, memory)
                with self.assertRaisesRegex(ValueError, "RESOURCE_BUDGET"):
                    b.check()


if __name__ == "__main__":
    unittest.main()
