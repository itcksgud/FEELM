import unittest
from collections import defaultdict

from fm_v4_features import (
    ExternalTagStore,
    Movie,
    build_feature_map,
    fit_genre_vocabulary,
    fit_global_rating_mean,
    fit_scaler,
    normalize_tag,
    schema_document,
    vectorize,
)
from fm_v4_prepare import _validate_episode, annotate_training_rows


FEATURE_CONFIG = {
    "positive_threshold_diagnostic_only": 4.0,
    "event_rank_half_life": 10.0,
    "user_center_prior_weight": 5.0,
    "profile_shrinkage": 2.0,
    "confidence_gate_prior": 5.0,
    "rating_range": 4.5,
}
SPLIT_CONFIG = {
    "inner_user_salt": "fm-v4-inner-4623",
    "inner_user_holdout_modulus": 5,
    "inner_user_holdout_bucket": 0,
}


def episode(uid, target, n, history, rating=4.0, target_key=None):
    return {
        "role": "TRAIN", "target_key": target_key or f"{uid}-{target}", "uid": uid,
        "target_movie_id": target, "target_event_at": 500, "prediction_at": 500,
        "n": n, "n_bucket": str(n), "total_history_count": len(history),
        "history": history, "target_rating": rating,
    }


class FMV4FeatureTest(unittest.TestCase):
    def setUp(self):
        self.movies = {
            1: Movie(1, 2000, ("Action", "Comedy")),
            2: Movie(2, 2005, ("Drama",)),
            3: Movie(3, 2010, ("Action",)),
            4: Movie(4, None, ()),
        }
        records = [
            {"userId": 90, "movieId": 1, "tag": "  Space   OPERA ", "timestamp": 100},
            {"userId": 90, "movieId": 1, "tag": "space opera", "timestamp": 200},
            {"userId": 91, "movieId": 1, "tag": "SPACE OPERA", "timestamp": 300},
            {"userId": 92, "movieId": 2, "tag": "slow", "timestamp": 150},
            {"userId": 1, "movieId": 3, "tag": "excluded", "timestamp": 100},
            {"userId": 93, "movieId": 3, "tag": "future", "timestamp": 900},
        ]
        self.tags = ExternalTagStore(records, {1})
        self.tag_vocabulary = self.tags.vocabulary(400, 1, 2)
        raw = [
            episode(1, 1, 2, [{"movie_id": 2, "rating": 1.0, "event_at": 100},
                              {"movie_id": 3, "rating": 5.0, "event_at": 200}], 4.5),
            episode(2, 2, 0, [], 3.0),
        ]
        self.fit_rows = annotate_training_rows(raw, SPLIT_CONFIG)
        # Unit-level preprocessing functions only require rows marked as FIT.
        for row in self.fit_rows:
            row["internal_split"] = "FIT"
        self.genres = fit_genre_vocabulary(self.fit_rows, self.movies)
        self.mean = fit_global_rating_mean(self.fit_rows)
        self.scaler = fit_scaler(self.fit_rows, self.movies, self.genres, self.tags,
                                 self.tag_vocabulary, self.mean["value"], FEATURE_CONFIG)
        self.schema = schema_document(self.genres, self.tag_vocabulary, self.scaler, self.mean,
                                      FEATURE_CONFIG, {"factor_rank": 4})

    def test_tag_normalization_dedup_exclusion_and_asof(self):
        self.assertEqual(normalize_tag("  ＳＰＡＣＥ   Opera  "), "space opera")
        self.assertEqual(self.tags.deduplicated_rows, 4)
        self.assertEqual(self.tag_vocabulary["values"], ["slow", "space opera"])
        before_second, status = self.tags.vector(1, 250, self.tag_vocabulary)
        self.assertEqual(status, "KNOWN")
        self.assertEqual(before_second, {"space opera": 1.0})
        future, status = self.tags.vector(3, 500, self.tag_vocabulary)
        self.assertEqual((future, status), ({}, "MISSING"))

    def test_signed_profile_and_n0_exact_interaction_zero(self):
        positive = self.fit_rows[0]
        features, _ = build_feature_map(
            positive, self.movies[1], self.movies, self.genres, self.tags, self.tag_vocabulary,
            self.mean["value"], self.scaler, FEATURE_CONFIG,
        )
        self.assertEqual(features["candidate.genre:Action"], 0.5)
        self.assertEqual(features["candidate.genre:Comedy"], 0.5)
        self.assertGreater(features["profile.genre.q:Action"], 0.0)
        self.assertLess(features["profile.genre.q:Drama"], 0.0)
        self.assertGreater(features["profile.genre.gate:value"], 0.0)

        zero = self.fit_rows[1]
        zero_features, _ = build_feature_map(
            zero, self.movies[2], self.movies, self.genres, self.tags, self.tag_vocabulary,
            self.mean["value"], self.scaler, FEATURE_CONFIG,
        )
        indices, _ = vectorize(zero_features, self.schema["ordered_names"])
        factor_indices = set()
        gates = set()
        for field in self.schema["interaction_fields"].values():
            factor_indices.update(field["q_indices"])
            gates.add(field["gate_index"])
        self.assertFalse((factor_indices | gates).intersection(indices))

    def test_schema_is_aligned_self_describing_and_identity_free(self):
        names = self.schema["ordered_names"]
        self.assertFalse(any("user_id" in name or "movie_id" in name for name in names))
        self.assertFalse(self.schema["user_id_embedding"])
        self.assertFalse(self.schema["candidate_movie_id_feature"])
        self.assertEqual(self.schema["profiles"]["matched_additive_v4"]["linear_indices"],
                         self.schema["profiles"]["signed_genre_tag_fm_v4"]["linear_indices"])
        for field, metadata in self.schema["interaction_fields"].items():
            self.assertEqual(len(metadata["values"]), len(metadata["candidate_indices"]))
            self.assertEqual(len(metadata["values"]), len(metadata["q_indices"]))
            for value, candidate_index, q_index in zip(metadata["values"], metadata["candidate_indices"],
                                                        metadata["q_indices"]):
                self.assertEqual(names[candidate_index], f"candidate.{field}:{value}")
                self.assertEqual(names[q_index], f"profile.{field}.q:{value}")

    def test_user_target_variant_weights(self):
        rows = []
        for uid in (10, 11):
            for target in (1, 2):
                for n in (0, 1, 2):
                    history = [{"movie_id": 3, "rating": 4.0, "event_at": 100 + index}
                               for index in range(n)]
                    rows.append(episode(uid, target, n, history, target_key=f"{uid}:{target}"))
        annotated = annotate_training_rows(rows, SPLIT_CONFIG)
        additive = defaultdict(float)
        interaction = defaultdict(float)
        splits = defaultdict(set)
        for row in annotated:
            additive[row["uid"]] += row["weight_additive"]
            interaction[row["uid"]] += row["weight_interaction"]
            splits[row["uid"]].add(row["internal_split"])
            if row["n"] == 0:
                self.assertEqual(row["weight_interaction"], 0.0)
        self.assertTrue(all(abs(value - 1.0) < 1e-12 for value in additive.values()))
        self.assertTrue(all(abs(value - 1.0) < 1e-12 for value in interaction.values()))
        self.assertTrue(all(len(value) == 1 for value in splits.values()))

    def test_validation_episode_rejects_any_label(self):
        row = episode(99, 1, 0, [])
        row["role"] = "VALIDATION"
        with self.assertRaisesRegex(RuntimeError, "exposes a label"):
            _validate_episode(row, "VALIDATION")
        row.pop("target_rating")
        accepted = _validate_episode(row, "VALIDATION")
        self.assertNotIn("target_rating", accepted)


if __name__ == "__main__":
    unittest.main()
