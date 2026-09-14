import unittest

from party_ranking import calculate_party_score


class CalculatePartyScoreTest(unittest.TestCase):
    def test_balanced_example_is_explainable(self) -> None:
        result = calculate_party_score([4.5, 4.0, 3.5, 4.0])

        self.assertEqual(result.fit_score, 74)
        self.assertEqual(result.mean_predicted_star, 4.0)
        self.assertEqual(result.minimum_predicted_star, 3.5)
        self.assertEqual(result.preference_gap, 1.0)
        self.assertTrue(result.eligible)
        self.assertIsNone(result.fallback_reason)

    def test_same_average_with_lower_minimum_gets_lower_score(self) -> None:
        balanced = calculate_party_score([4.0, 4.0, 4.0, 4.0])
        divided = calculate_party_score([5.0, 5.0, 3.0, 3.0])

        self.assertEqual(balanced.mean_predicted_star, divided.mean_predicted_star)
        self.assertGreater(balanced.fit_score, divided.fit_score)

    def test_candidate_below_threshold_is_marked_as_fallback(self) -> None:
        result = calculate_party_score([4.8, 4.2, 2.9])

        self.assertFalse(result.eligible)
        self.assertEqual(result.fallback_reason, "MEMBER_BELOW_MINIMUM_THRESHOLD")

    def test_rejects_missing_members(self) -> None:
        with self.assertRaisesRegex(ValueError, "한 명 이상"):
            calculate_party_score([])

    def test_rejects_star_outside_rating_scale(self) -> None:
        with self.assertRaisesRegex(ValueError, "1.0~5.0"):
            calculate_party_score([4.0, 5.1])

    def test_rejects_threshold_outside_rating_scale(self) -> None:
        with self.assertRaisesRegex(ValueError, "최저 별점 기준"):
            calculate_party_score([4.0, 4.5], minimum_threshold=0.0)


if __name__ == "__main__":
    unittest.main()
