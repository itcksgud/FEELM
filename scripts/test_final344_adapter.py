"""Synthetic-only checks; no real ratings, model fits, or data-file access."""
import os
for key in ["OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"]:
    os.environ[key] = "4"

import unittest
import numpy as np
import pandas as pd
from cold_item_features import Features as OriginalFeatures
from foundation340_features import Features as BatchFeatures, RATING_COLUMNS
from text339_relations import CappedRelations
from final344_adapter import MetadataAdapter, PRIOR_MEAN, PRIOR_MASS


def movie(movie_id, **overrides):
    row = dict(movie_id=movie_id, genre_ids=[18], keyword_ids=[41, 42],
               production_country_codes=["KR"], original_language="ko",
               director_ids=[900001], top5_cast_ids=[900011, 900012],
               production_company_ids=[123], collection_ids=[], release_year=2020,
               runtime_minutes=100, tmdb_vote_average=7.0, tmdb_vote_count=20,
               tmdb_popularity=3.0)
    row.update(overrides)
    return row


def batch_reference(frame, history, stars, candidates):
    # Reuse the independent, sealed vectorized RH formula. Its normal constructor
    # estimates a prior from 44,920 real training movies; this synthetic fixture
    # instead supplies the already fixed prior without opening those data.
    ref = object.__new__(BatchFeatures)
    ref.original = OriginalFeatures(frame)
    ref.original.base = CappedRelations(frame)
    ref.base = ref.original.base
    ref.shrunk = ref.original.crowd.copy()
    valid = ref.original.valid[:, 0]
    votes = frame.tmdb_vote_count.to_numpy(float)
    ref.shrunk[valid, 0] = (votes[valid] * ref.shrunk[valid, 0] + 48 * 6.173088067675869 / 10) / (votes[valid] + 48)
    lookup = {mid: i for i, mid in enumerate(frame.movie_id)}
    n, h = len(candidates), len(history)
    oi = np.tile(np.pad([lookup[mid] for mid in history], (0, 30-h)), (n, 1)).astype(int)
    r = np.tile(np.pad(stars, (0, 30-h)), (n, 1))
    mask = np.tile(np.arange(30) < h, (n, 1))
    ei = np.array([lookup[mid] for mid in candidates])
    x = ref.batch(oi, r, mask, ei)
    x[:, RATING_COLUMNS] = ref.crowd(oi, r, mask, ei, True)[:, np.array(RATING_COLUMNS)-200]
    return x


class AdapterTests(unittest.TestCase):
    def test_full_vector_matches_independent_sealed_batch_formula(self):
        frame = pd.DataFrame([movie(1000+i, genre_ids=[18, 35] if i % 2 else [18],
                                   director_ids=[900001+i % 3], tmdb_vote_count=1+i,
                                   tmdb_vote_average=5+i % 6) for i in range(34)])
        adapter = MetadataAdapter(frame)
        for h in [0, 1, 2, 7, 30]:
            history = list(frame.movie_id[:h]); stars = (np.arange(h) % 10 + 1) / 2
            candidates = list(frame.movie_id[30:])
            actual = adapter.transform(history, stars, candidates)
            np.testing.assert_allclose(actual, batch_reference(frame, history, stars, candidates), rtol=0, atol=2e-6)
            self.assertEqual(actual.shape, (4, 230))
            self.assertEqual(actual.dtype, np.float32)

    def test_outside_id_clone_and_existing_features_are_invariant(self):
        original = pd.DataFrame([movie(1), movie(2, director_ids=[900002]), movie(3)])
        old = MetadataAdapter(original).transform([1, 2], [5, 1], [3])
        extended = pd.concat([original, pd.DataFrame([movie(900000003), movie(900000004, director_ids=[987654321])])], ignore_index=True)
        actual = MetadataAdapter(extended.sample(frac=1, random_state=2)).transform([1, 2], [5, 1], [900000003, 3])
        np.testing.assert_array_equal(actual[0], old[0])
        np.testing.assert_array_equal(actual[1], old[0])

    def test_new_person_identity_and_personal_reaction_have_expected_values(self):
        frame = pd.DataFrame([movie(1, director_ids=[987654321], top5_cast_ids=[987650001]),
                              movie(2, director_ids=[987654322], top5_cast_ids=[987650002]),
                              movie(900000003, director_ids=[987654321], top5_cast_ids=[987650001]),
                              movie(900000004, director_ids=[987654322], top5_cast_ids=[987650002]),
                              movie(900000005, director_ids=[987654323], top5_cast_ids=[987650003])])
        adapter = MetadataAdapter(frame); x = adapter.transform([1, 2], [5, 1], [900000003, 900000004, 900000005])
        for group in ["DIRECTOR", "CAST"]:
            col = lambda suffix: adapter.names.index(f"support_{group}_{suffix}")
            np.testing.assert_allclose(x[:, col("linked_fraction")], [.5, .5, 0])
            np.testing.assert_allclose(x[:, col("weighted_stars")], [2/3, 8/15, 0], rtol=0, atol=1e-7)
            np.testing.assert_array_equal(x[:, col("no_link")], [0, 0, 1])
            np.testing.assert_allclose(x[:, adapter.names.index(f"{group}_mass_reliability")], [1/6, 1/6, 0], rtol=0, atol=1e-7)

    def test_missing_metadata_is_not_a_negative_rating(self):
        frame = pd.DataFrame([movie(1), {"movie_id": 900000003}])
        adapter = MetadataAdapter(frame); x = adapter.transform([1], [4.5], [900000003])[0]
        for name in ["support_year_present", "support_runtime_present", "crowd_rating_present", "crowd_votes_present", "crowd_popularity_present", "crowd_rating_candidate_delta", "crowd_rating_response_cross"]:
            self.assertEqual(x[adapter.names.index(name)], 0, name)
        self.assertEqual(x[adapter.names.index("support_input_mean")], np.float32(.9))
        self.assertEqual(x[adapter.names.index("support_DIRECTOR_no_link")], 1)
        self.assertTrue(np.isfinite(x).all())

    def test_zero_input_preserves_candidate_information(self):
        adapter = MetadataAdapter(pd.DataFrame([movie(900000003)])); x = adapter.transform([], [], [900000003])[0]
        for name in ["support_input_mean", "support_input_std", "support_input_k", "support_has_input", "crowd_rating_input_present", "crowd_rating_response_cross"]:
            self.assertEqual(x[adapter.names.index(name)], 0)
        self.assertEqual(x[adapter.names.index("support_candidate_genre_18")], 1)
        self.assertGreater(x[adapter.names.index("crowd_rating_value")], 0)

    def test_prior_is_fixed_and_only_six_rating_paths_change(self):
        frame = pd.DataFrame([movie(1, tmdb_vote_average=2, tmdb_vote_count=1), movie(2, tmdb_vote_average=10, tmdb_vote_count=1)])
        adapter = MetadataAdapter(frame); actual = adapter.transform([1], [4], [2])
        raw = OriginalFeatures(frame); raw.base = CappedRelations(frame)
        baseline = raw.transform([0], [4], [1])[:, 168:398]
        unchanged = np.setdiff1d(np.arange(230), RATING_COLUMNS)
        np.testing.assert_array_equal(actual[:, unchanged], baseline[:, unchanged])
        self.assertAlmostEqual(float(actual[0, 200]), (10+48*6.173088067675869)/49/10, places=7)
        self.assertAlmostEqual(float(actual[0, 207]), (2+48*6.173088067675869)/49/10, places=7)
        extended = pd.concat([frame, pd.DataFrame([movie(999999, tmdb_vote_average=10, tmdb_vote_count=100000000)])], ignore_index=True)
        np.testing.assert_array_equal(MetadataAdapter(extended).transform([1], [4], [2]), actual)
        self.assertEqual((PRIOR_MEAN, PRIOR_MASS), (6.173088067675869, 48.0))

    def test_empty_candidates_and_candidate_order(self):
        adapter = MetadataAdapter(pd.DataFrame([movie(1), movie(2, genre_ids=[35]), movie(3, genre_ids=[18])]))
        self.assertEqual(adapter.transform([1], [3], []).shape, (0, 230))
        forward = adapter.transform([1], [3], [2, 3])
        np.testing.assert_array_equal(adapter.transform([1], [3], [3, 2]), forward[::-1])

    def test_rejects_invalid_or_unrepresented_requests(self):
        adapter = MetadataAdapter(pd.DataFrame([movie(1), movie(2)]))
        bad = [([1], [3], [1]), ([1, 1], [3, 3], [2]), ([1], [3], [2, 2]),
               ([1], [3.1], [2]), ([1], [np.nan], [2]), ([1], [], [2]),
               ([1], [3], [999]), ([1.1], [3], [2]), ([True], [3], [2]),
               (list(range(31)), [3]*31, [999])]
        for args in bad:
            with self.subTest(args=args), self.assertRaises(ValueError): adapter.transform(*args)
        with self.assertRaises(ValueError): MetadataAdapter(pd.DataFrame([movie(1), movie(1)]))
        for name in ['release_year', 'runtime_minutes', 'tmdb_vote_average', 'tmdb_vote_count', 'tmdb_popularity']:
            for value in [np.inf, -np.inf]:
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    MetadataAdapter(pd.DataFrame([movie(1, **{name: value})]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
