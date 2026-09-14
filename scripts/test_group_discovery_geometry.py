"""Small counterexamples protect geometry semantics; no research inputs."""
import json
import unittest

import numpy as np
from scipy import sparse

from group_discovery_geometry import (anchor_distance, append_new_movies, assign_frozen, compare_user_groups, geometry,
                                      midrank, movie_vectors, reference_percentile,
                                      squared_distances, transform_movies)


class GeometryTests(unittest.TestCase):
    def test_blocks_and_missing(self):
        x, valid, vocab = movie_vectors([[12], [12], []], [[12], [], []], min_df=1)
        np.testing.assert_allclose(x.toarray(), [[2**-.5, 2**-.5], [1, 0], [0, 0]])
        self.assertEqual(valid.tolist(), [True, True, False])
        self.assertEqual(vocab["genre_ids"], vocab["keyword_ids"])

    def test_nearest_positive_not_mean(self):
        x = sparse.csr_matrix([[1., 0.]])
        p = sparse.eye(2, format="csr")
        self.assertEqual(anchor_distance(x, p)[0], 0)

    def test_midrank_ties(self):
        np.testing.assert_allclose(midrank([0, 1, 1, 3]), [.125, .5, .5, .875])
        np.testing.assert_allclose(midrank([1, 1, 1]), [.5, .5, .5])
        self.assertEqual(midrank([0, 0, 1, 2, 3])[0], .2)
        np.testing.assert_allclose(reference_percentile([0, 1, 2, 3, 4], [0, 1, 1, 3]), [.125, .5, .75, .875, 1])

    def test_append_is_transform_only(self):
        genres, keywords = [[10], [20]], [[100], [200]]
        ref, valid, vocab = movie_vectors(genres, keywords, min_df=1)
        centers = ref.toarray().copy()
        labels = np.array([0, 1])
        radius, boundary, _, _ = geometry(ref, centers, labels)
        before = json.dumps(vocab, sort_keys=True)
        x, ok, coverage = transform_movies(genres + [[999]], keywords + [[999]], vocab)
        np.testing.assert_allclose(x[:2].toarray(), ref.toarray())
        self.assertEqual(ok.tolist(), [True, True, False])
        self.assertEqual(coverage["oov_genres"][-1], 1)
        assigned = assign_frozen(x, ok, centers, labels, radius, boundary)
        self.assertEqual(assigned[0].tolist(), [0, 1, -1])
        np.testing.assert_allclose(assigned[1][:2], radius)
        np.testing.assert_allclose(assigned[2][:2], boundary)
        np.testing.assert_allclose(assigned[3][:2], [.5, .5])
        self.assertEqual(before, json.dumps(vocab, sort_keys=True))
        for order in [[2, 1, 0], [1, 0, 2]]:
            again = assign_frozen(x[order], ok[order], centers, labels, radius, boundary)
            np.testing.assert_equal(again[0], assigned[0][order])

    def test_append_identity_batch_and_oov(self):
        ref, valid, vocab = movie_vectors([[10], [20]], [[100], [200]], min_df=1)
        centers = ref.toarray().copy()
        labels = np.array([0, 1])
        radius, boundary, _, _ = geometry(ref, centers, labels)
        reference = {"movie_ids": np.array([10, 20]), "catalog_movie_ids": np.array([10, 20, 99]),
                     "labels": labels, "radius": radius, "boundary": boundary}
        registry = reference["catalog_movie_ids"]
        snapshot = [a.copy() for a in [centers, labels, radius, boundary]]
        added = append_new_movies([1, 30], [[10], [10, 999]], [[100], [100, 999]], vocab, centers, reference, registry)
        np.testing.assert_array_equal(added[0].toarray(), np.repeat(ref[0].toarray(), 2, axis=0))
        self.assertEqual(added[2][0].tolist(), [0, 0])
        for pos, movie_id in enumerate([1, 30]):
            single = append_new_movies([movie_id], [[10]], [[100]], vocab, centers, reference, registry)
            for batch_value, single_value in zip(added[2], single[2]):
                np.testing.assert_array_equal(batch_value[pos:pos+1], single_value)
        for saved, current in zip(snapshot, [centers, labels, radius, boundary]):
            np.testing.assert_array_equal(saved, current)
        with self.assertRaises(ValueError):
            append_new_movies([10], [[20]], [[200]], vocab, centers, reference, registry)
        with self.assertRaises(ValueError):
            append_new_movies([30], [[20]], [[200]], vocab, centers, reference, added[4])
        with self.assertRaises(ValueError):
            append_new_movies([99], [[20]], [[200]], vocab, centers, reference, registry)

    def test_anchor_reference_is_append_invariant(self):
        ref = sparse.csr_matrix([[1., 0.], [0., 1.], [2**-.5, 2**-.5]])
        positive = ref[[0]]
        distances = anchor_distance(ref, positive)
        old_ranks = midrank(distances)
        new = sparse.csr_matrix([[0., 1.], [0., 1.]])
        new_ranks = reference_percentile(anchor_distance(new, positive), distances)
        np.testing.assert_array_equal(old_ranks, midrank(distances))
        np.testing.assert_array_equal(new_ranks, [old_ranks[1], old_ranks[1]])
        # New feedback changes personal comparison, not the underlying movie space.
        changed = anchor_distance(ref, ref[[0, 1]])
        self.assertEqual(changed[1], 0)

    def test_boundary_not_second_center(self):
        # Supply one row per group so group ranks have nonempty support.
        centers = np.array([[.7, .3], [.65, .4], [.8, .5]])
        x = sparse.csr_matrix([[1., 0.], centers[1], centers[2]])
        labels = np.argmin(squared_distances(x, centers), axis=1)
        _, b, _, _ = geometry(x, centers, labels)
        self.assertAlmostEqual(b[0], .11 / (2 * np.sqrt(.05)), places=10)

    def test_centroid_not_unit_normalized(self):
        x = sparse.csr_matrix([[np.sqrt(.5), np.sqrt(.5)]])
        centers = np.array([[.5, .5], [.6, .78]])
        self.assertEqual(np.argmin(squared_distances(x, centers)), 1)
        unit = centers / np.linalg.norm(centers, axis=1)[:, None]
        self.assertEqual(np.argmin(squared_distances(x, unit)), 0)

    def test_viewed_counts_and_positive_scope(self):
        x = sparse.csr_matrix([[1., 0.], [1., 0.], [0., 1.], [0., 0.]])
        centers = np.eye(2)
        labels = np.array([0, 0, 1, -1])
        r = compare_user_groups(x, centers, labels, [0, 1, 2], [0, 2], [4, 2])
        self.assertEqual(r["experience"].tolist(), [2, 1])
        self.assertEqual(r["positives"].tolist(), [0])
        self.assertEqual(r["selected_m1"].tolist(), [])
        self.assertEqual(r["selected_m2a"].tolist(), [0])
        r = compare_user_groups(x, centers, labels, [0, 3], [0], [4])
        self.assertFalse(r["eligible_m1"].any())
        self.assertEqual(r["selected_m2a"].tolist(), [0])

    def test_no_positives(self):
        x = sparse.eye(2, format="csr")
        r = compare_user_groups(x, np.eye(2), np.arange(2), [0], [0], [2])
        self.assertEqual(len(r["selected_m1"]), 0)
        self.assertEqual(len(r["selected_m2a"]), 0)

    def test_duplicate_centers_rejected(self):
        with self.assertRaises(ValueError):
            geometry(sparse.csr_matrix([[1., 0.]]), np.array([[1., 0.], [1., 0.]]), np.array([0]))


if __name__ == "__main__":
    unittest.main()
