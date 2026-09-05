from __future__ import annotations

import hashlib
import unittest

import numpy as np
from scipy import sparse

from scripts.rec_ev_022a_core import (
    deterministic_rank,
    encoding_weights,
    full_history_mid_percentiles,
    itemknn_pair_similarity,
    old_user_bucket,
    order_key,
    pair1_metrics,
    pairwise_concordance,
    score_judged_targets,
    simultaneous_max_t,
    user_equal_prior,
    user_key,
    user_role_bucket,
)


class RecEv022aCoreTests(unittest.TestCase):
    def test_hash_contract_uses_exact_big_endian_algorithms(self) -> None:
        expected_old = int.from_bytes(hashlib.sha256(b"feelm-rec-vnext-user-split-v1|17").digest()[:8], "big") % 100
        expected_role = int.from_bytes(hashlib.sha256(b"rec-ev-022a-user-role-v1|17").digest(), "big") % 10_000
        self.assertEqual(expected_old, old_user_bucket(17))
        self.assertEqual(expected_role, user_role_bucket(17))
        anonymous = hashlib.sha256(b"rec-ev-022a-user-key-v1|17").hexdigest()
        self.assertEqual(anonymous, user_key(17))
        expected_order = hashlib.sha256(f"salt|{anonymous}|1".encode()).digest()
        self.assertEqual((expected_order, 1), order_key("salt", anonymous, 1))

    def test_user_equal_prior_and_midrank_labels_are_tie_aware(self) -> None:
        hist = np.zeros((2, 10), dtype=np.int64)
        hist[0, [0, 9]] = [1, 1]
        hist[1, 4] = 2
        pi0, g0 = user_equal_prior(hist)
        self.assertAlmostEqual(1.0, float(pi0.sum()))
        self.assertTrue(np.all(np.diff(g0) >= 0))
        np.testing.assert_allclose(full_history_mid_percentiles([1.0, 1.0, 5.0]), [1 / 3, 1 / 3, 5 / 6])

    def test_three_encoding_contracts_include_k1_boundary(self) -> None:
        hist = np.ones((1, 10), dtype=np.int64)
        _, g0 = user_equal_prior(hist)
        self.assertEqual((1,), encoding_weights("BINARY_SIGN", [5.0], g0).shape)
        self.assertEqual((1,), encoding_weights("PERCENTILE_MAGNITUDE", [5.0], g0).shape)
        np.testing.assert_allclose(encoding_weights("ORDINAL_RANK", [5.0], g0), [0.0])

    def test_itemknn_clips_negative_and_low_support(self) -> None:
        z = sparse.csc_matrix(np.asarray([[1.0, 1.0, -1.0], [1.0, 1.0, -1.0]], dtype=np.float32))
        observed = z.copy()
        observed.data = np.ones_like(observed.data)
        norms = np.sqrt(np.asarray(z.multiply(z).sum(axis=0)).ravel())
        actual = itemknn_pair_similarity(z, observed, norms, [0], [1, 2], shrinkage=0, minimum_support=2)
        self.assertGreater(actual[0, 0], 0)
        self.assertEqual(0.0, actual[1, 0])

    def test_scoring_fallback_and_deterministic_ranking(self) -> None:
        sim = np.asarray([[0.8, 0.1], [0.2, 0.9]])
        scores, fallback = score_judged_targets(sim, [1.0, -1.0])
        self.assertFalse(fallback)
        self.assertGreater(scores[0], scores[1])
        order = deterministic_rank([2, 1], scores, [3.0, 4.0], fallback=False)
        self.assertEqual([0, 1], order.tolist())
        zeros, fallback = score_judged_targets(sim[:, :0], [])
        self.assertTrue(fallback)
        self.assertEqual([0.0, 0.0], zeros.tolist())

    def test_pair_metrics_and_max_t_are_deterministic(self) -> None:
        self.assertEqual((0.7, 0.4), pair1_metrics([0.8, 0.6, 0.1]))
        self.assertEqual(0.5, pairwise_concordance([2.0, 2.0, 1.0], [3.0, 1.0, 2.0]))
        values = np.asarray([[0.1, -0.1], [0.0, 0.2], [0.2, 0.1], [0.1, 0.0]])
        left = simultaneous_max_t(values, repeats=100, seed=20260924)
        right = simultaneous_max_t(values, repeats=100, seed=20260924)
        self.assertEqual(left["critical"], right["critical"])
        np.testing.assert_array_equal(left["low"], right["low"])


if __name__ == "__main__":
    unittest.main()
