import argparse
import csv
import json
import tempfile
import unittest
from pathlib import Path

from fm_v6_build_prefix_input import K_VALUES, build, file_pin, role_for, stable
from fm_v6_isolate_input import isolate


class FmV6PrefixInputTest(unittest.TestCase):
    def test_exact_prefixes_share_ten_judgments_and_isolate_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ratings, movies = root / "ratings.csv", root / "movies.csv"
            users = {}
            for uid in range(1, 1000):
                users.setdefault(role_for(623, uid), uid)
                if {"TRAIN", "VALIDATION"}.issubset(users):
                    break
            with movies.open("w", encoding="utf-8", newline="") as output:
                writer = csv.writer(output)
                writer.writerow(("movieId", "title", "genres"))
                for movie_id in range(1, 102):
                    writer.writerow((movie_id, f"Movie {movie_id} (2000)", "Drama"))
            ordered = sorted(range(1, 102), key=lambda movie_id: (stable(623, "global-unknown", movie_id),
                                                                   movie_id))
            late_movie = ordered[0]
            prefix_movies = [movie_id for movie_id in range(1, 102) if movie_id != late_movie][:30]
            with ratings.open("w", encoding="utf-8", newline="") as output:
                writer = csv.writer(output)
                writer.writerow(("userId", "movieId", "rating", "timestamp"))
                for uid in sorted((users["TRAIN"], users["VALIDATION"])):
                    for offset, movie_id in enumerate(prefix_movies + [late_movie]):
                        writer.writerow((uid, movie_id, 5.0 if offset % 2 else 2.0, 1000 + offset))
            source = root / "source"
            source.mkdir()
            manifest = build(argparse.Namespace(ratings_path=ratings, movies_path=movies, output_root=source,
                                                seed=623, expected_eligible_users=2,
                                                source_revision="test-revision"))
            self.assertEqual(manifest["prefix"]["k_values"], K_VALUES)
            episodes = [json.loads(line) for line in
                        (source / "input/episodes.train-validation.jsonl").read_text(encoding="utf-8").splitlines()]
            validation = [row for row in episodes if row["role"] == "VALIDATION"]
            self.assertEqual([row["n"] for row in validation], list(K_VALUES))
            self.assertEqual(len({row["candidate_digest"] for row in validation}), 1)
            self.assertTrue(all(len(row["history"]) == row["n"] for row in validation))
            candidates = [json.loads(line) for line in
                          (source / "input/candidates.train-validation.jsonl").read_text(
                              encoding="utf-8").splitlines()]
            leaked_future = [row for row in candidates if row["candidate_movie_id"] == late_movie]
            self.assertEqual(len(leaked_future), 1)
            self.assertEqual(leaked_future[0]["label_state"], "UNKNOWN_SAMPLED")
            features, labels = root / "features", root / "labels"
            features.mkdir(); labels.mkdir()
            pin = file_pin(source / "input/manifest.train-validation.json")
            isolate(argparse.Namespace(source_root=source, output_root=features, labels_root=labels,
                                       expected_manifest=pin))
            candidate_text = (features / "input/candidates.features.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("observed_rating", candidate_text)
            self.assertNotIn("label_state", candidate_text)


if __name__ == "__main__":
    unittest.main()
