import unittest

from gbt_zero_n_build_input import Rating, choose_targets, history_variant_sizes, role_for_user


class GbtZeroNInputTest(unittest.TestCase):
    def test_role_assignment_is_stable(self):
        first = [role_for_user(622, uid) for uid in range(1, 100)]
        self.assertEqual(first, [role_for_user(622, uid) for uid in range(1, 100)])

    def test_target_selection_is_label_blind_and_bounded(self):
        rows = [
            Rating(1, 5.0, 10), Rating(2, 4.0, 20), Rating(3, 3.5, 30),
            Rating(4, 1.0, 40), Rating(5, 4.5, 50), Rating(6, 2.0, 60),
        ]
        selected = choose_targets(622, 1, rows, 4.0)
        changed_labels = [Rating(row.movie_id, 5.0 if row.rating < 4 else 1.0, row.timestamp) for row in rows]
        self.assertEqual([row.movie_id for row in selected],
                         [row.movie_id for row in choose_targets(622, 1, changed_labels, 4.0)])
        self.assertEqual(len(selected), 4)

    def test_single_class_user_is_kept(self):
        self.assertEqual(choose_targets(622, 1, [Rating(1, 5.0, 10)], 4.0), [Rating(1, 5.0, 10)])

    def test_arbitrary_available_n_is_always_included(self):
        self.assertEqual(history_variant_sizes(0), [0])
        self.assertEqual(history_variant_sizes(3), [0, 1, 2, 3])
        self.assertEqual(history_variant_sizes(41), [0, 1, 2, 4, 7, 15, 25, 40, 41])
        self.assertEqual(history_variant_sizes(50)[-1], 50)


if __name__ == "__main__":
    unittest.main()
