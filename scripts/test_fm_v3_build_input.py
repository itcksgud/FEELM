import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fm_v3_build_input import load_prior_validation_users, select_cases_excluding_prior_validation


class FMV3BuildInputTest(unittest.TestCase):
    def test_prior_validation_loader_skips_final_before_json_parse(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            users = [2, 4]
            digest = hashlib.sha256(",".join(map(str, users)).encode()).hexdigest()
            manifest = {"users": {"VALIDATION": 2}, "seed": 622,
                        "user_partition": {"role_user_digests": {"VALIDATION": digest}}}
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            rows = [
                json.dumps({"role": "TRAIN", "uid": 1}),
                json.dumps({"role": "VALIDATION", "uid": 2}),
                json.dumps({"role": "VALIDATION", "uid": 4}),
                '{"role":"FINAL_TEST", BROKEN}',
            ]
            (root / "episodes.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
            actual, report = load_prior_validation_users(root)
            self.assertEqual(actual, {2, 4})
            self.assertEqual(report["user_digest"], digest)

    def test_selector_excludes_prior_validation_user(self):
        with tempfile.TemporaryDirectory() as directory:
            ratings = Path(directory) / "ratings.csv"
            with ratings.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=["userId", "movieId", "rating", "timestamp"])
                writer.writeheader()
                writer.writerow({"userId": 1, "movieId": 10, "rating": 5.0, "timestamp": 150})
                writer.writerow({"userId": 2, "movieId": 10, "rating": 4.0, "timestamp": 150})
            with patch("fm_v3_build_input.base.role_for_user", return_value="VALIDATION"), \
                 patch("fm_v3_build_input.base.role_window", return_value=(100, 200)):
                selected, _ = select_cases_excluding_prior_validation(
                    ratings, 3623, {"TRAIN": 0, "VALIDATION": 1, "FINAL_TEST": 0},
                    4.0, {10: 1970}, {1},
                )
            self.assertEqual([case.uid for case in selected["VALIDATION"]], [2])


if __name__ == "__main__":
    unittest.main()
