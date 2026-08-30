import unittest

import numpy as np
import pandas as pd

from recommendation_exploration_pareto import (
    genre_diversity,
    greedy_exploration_order,
    pareto_front,
    select_for_budgets,
)


class ExplorationParetoTest(unittest.TestCase):
    def test_genre_diversity_is_zero_for_identical_and_one_for_orthogonal(self):
        matrix = np.asarray([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
        self.assertAlmostEqual(genre_diversity([0, 1], matrix), 0.0)
        self.assertAlmostEqual(genre_diversity([0, 2], matrix), 1.0)

    def test_greedy_order_is_deterministic_and_uses_movie_id_tie_break(self):
        frame = pd.DataFrame({
            "user_id": [7, 7, 7], "movie_id": [30, 10, 20],
            "base": [0.5, 0.5, 0.5], "novelty_pct": [0.5, 0.5, 0.5],
        })
        genres = np.zeros((31, 2), dtype=float)
        genres[10] = [1, 0]
        genres[20] = [0, 1]
        genres[30] = [1, 0]
        first = greedy_exploration_order(frame, "base", 0.2, genres)
        second = greedy_exploration_order(frame.sample(frac=1, random_state=4), "base", 0.2, genres)
        self.assertEqual(first, second)
        self.assertEqual(first[7][0], 10)
        self.assertEqual(first[7][1], 20)

    def test_pareto_and_loss_budget_selection_do_not_change_baseline(self):
        common = {"recall_at_10": 0.5, "user_coverage": 1.0, "users": 10,
                  "genre_calibration_distance": 0.2}
        metrics = {
            "POPULARITY": {**common, "ndcg_at_10": 0.5, "novelty_bits": 10.0,
                           "intra_list_diversity": 0.4, "catalog_coverage": 0.1, "long_tail_exposure": 0.2},
            "SAFE": {**common, "ndcg_at_10": 0.49, "novelty_bits": 11.0,
                     "intra_list_diversity": 0.5, "catalog_coverage": 0.2, "long_tail_exposure": 0.3},
            "DOMINATED": {**common, "ndcg_at_10": 0.48, "novelty_bits": 9.0,
                          "intra_list_diversity": 0.3, "catalog_coverage": 0.05, "long_tail_exposure": 0.1},
        }
        front = pareto_front(metrics)
        self.assertNotIn("DOMINATED", front)
        selected = select_for_budgets(metrics, front)
        self.assertEqual(selected["0%"]["policy"], "POPULARITY")
        self.assertEqual(selected["3%"]["policy"], "SAFE")


if __name__ == "__main__":
    unittest.main()
