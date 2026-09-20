import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fm_zero_n_prepare import choose_training_rows, selection_digest_and_rows


class TrainingVariantTest(unittest.TestCase):
    def test_one_deterministic_variant_per_target(self):
        rows = []
        for uid in (1, 2):
            for target in (10, 20):
                for n in (0, 1, 2, 7):
                    rows.append({"role": "TRAIN", "uid": uid, "target_movie_id": target,
                                 "prediction_at": 123, "n": n, "n_bucket": str(n)})
        first = choose_training_rows(rows, 622)
        second = choose_training_rows(list(reversed(rows)), 622)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 4)
        self.assertEqual(len({(row["uid"], row["target_movie_id"]) for row in first}), 4)

    def test_validation_never_enters_training(self):
        rows = [{"role": "VALIDATION", "uid": 1, "target_movie_id": 2,
                 "prediction_at": 3, "n": 0, "n_bucket": "0"}]
        self.assertEqual(choose_training_rows(rows, 622), [])

    def test_final_test_is_rejected_before_json_parse(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "rows.jsonl"
            path.write_text(
                '{"role":"FINAL_TEST","target_rating":THIS_MUST_NEVER_BE_PARSED}\n'
                '{"role":"TRAIN","uid":1}\n', encoding="utf-8"
            )
            count, _, rows = selection_digest_and_rows(path, True)
            self.assertEqual(count, 1)
            self.assertEqual(rows, [{"role": "TRAIN", "uid": 1}])


if __name__ == "__main__":
    unittest.main()
