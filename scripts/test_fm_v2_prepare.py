import unittest
from collections import defaultdict

from fm_v2_prepare import annotate_training_rows, target_key


class FMV2TrainingRowsTest(unittest.TestCase):
    def test_all_variants_have_target_sum_one_and_stay_in_one_split(self):
        rows = []
        for uid in (1, 2):
            for target, event_at in ((10, 100), (20, 200)):
                for n in (0, 1, 7):
                    rows.append({"role": "TRAIN", "uid": uid, "target_movie_id": target,
                                 "prediction_at": event_at - 1, "target_event_at": event_at,
                                 "n": n, "n_bucket": str(n)})
        annotated = annotate_training_rows(rows)
        self.assertEqual(len(annotated), len(rows))
        weights = defaultdict(float)
        splits = defaultdict(set)
        for row in annotated:
            weights[target_key(row)] += row["sample_weight"]
            splits[target_key(row)].add(row["internal_split"])
        self.assertTrue(all(abs(value - 1.0) < 1e-12 for value in weights.values()))
        self.assertTrue(all(len(value) == 1 for value in splits.values()))
        self.assertTrue(all(next(iter(value)) == ("HOLDOUT" if key[1] == 20 else "FIT")
                            for key, value in splits.items()))

    def test_validation_never_enters_training(self):
        rows = [{"role": "VALIDATION", "uid": 1, "target_movie_id": 2,
                 "prediction_at": 3, "target_event_at": 4, "n": 0, "n_bucket": "0"}]
        self.assertEqual(annotate_training_rows(rows), [])


if __name__ == "__main__":
    unittest.main()
