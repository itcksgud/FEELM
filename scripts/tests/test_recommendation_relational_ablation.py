from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from recommendation_relational_ablation import (
    association_for_anchor,
    choose_anchors,
    normalize_tag,
)


class RelationalAblationTest(unittest.TestCase):
    def test_normalize_tag_preserves_phrase_but_removes_punctuation(self) -> None:
        self.assertEqual("visually appealing", normalize_tag(" Visually-Appealing!! "))

    def test_anchor_selection_requires_personal_positive_residual_and_support(self) -> None:
        history = pd.DataFrame({
            "movie_id": [1, 2, 3, 4],
            "rating": [5.0, 4.5, 4.0, 5.0],
            "timestamp": [1, 2, 3, 4],
        })
        counts = np.asarray([0, 200, 300, 400, 20])
        anchors = choose_anchors(history, 3.5, counts)
        self.assertEqual([1, 2, 3], anchors["movie_id"].tolist())

    def test_association_uses_personal_mean_not_raw_four_star_threshold(self) -> None:
        train = pd.DataFrame({
            "user_id": [1, 1, 2, 2, 3, 3],
            "movie_id": [1, 2, 1, 2, 1, 2],
            "rating": [3.5, 4.0, 4.5, 5.0, 2.0, 1.0],
        })
        means = np.asarray([0.0, 3.0, 4.0, 2.5])
        baseline = np.asarray([0.0, 2 / 3, 2 / 3])
        counts = np.asarray([0, 3, 3])
        result = association_for_anchor(train, 1, means, baseline, counts)
        self.assertEqual(2, result["anchor_likers"])
        self.assertEqual(2, int(result["support"][2]))
        self.assertEqual(1.0, float(result["conditional_rate"][2]))


if __name__ == "__main__":
    unittest.main()
