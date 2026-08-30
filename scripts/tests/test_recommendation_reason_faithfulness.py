import unittest

from recommendation_reason_faithfulness import classify_reason


class ReasonFaithfulnessTest(unittest.TestCase):
    def test_emits_only_with_active_positive_rank_effect_and_valid_provenance(self):
        self.assertEqual(
            classify_reason(feature_active=True, contribution=0.1, rank_effect=True, provenance_valid=True),
            ("EMITTABLE_CANDIDATE", "FAITHFUL_SCORE_AND_RANK_EFFECT"),
        )

    def test_blocks_inactive_zero_no_effect_invalid_and_sensitive(self):
        cases = [
            ({"feature_active": False, "contribution": 1.0, "rank_effect": True, "provenance_valid": True},
             "FEATURE_NOT_IN_ACTIVE_POLICY"),
            ({"feature_active": True, "contribution": 0.0, "rank_effect": True, "provenance_valid": True},
             "NON_POSITIVE_CONTRIBUTION"),
            ({"feature_active": True, "contribution": 1.0, "rank_effect": False, "provenance_valid": True},
             "NO_RANK_EFFECT"),
            ({"feature_active": True, "contribution": 1.0, "rank_effect": True, "provenance_valid": False},
             "PROVENANCE_INVALID"),
            ({"feature_active": True, "contribution": 1.0, "rank_effect": True, "provenance_valid": True,
              "policy_version_match": False}, "POLICY_VERSION_MISMATCH"),
            ({"feature_active": True, "contribution": 1.0, "rank_effect": True, "provenance_valid": True,
              "sensitive_evidence": True}, "SENSITIVE_EVIDENCE"),
        ]
        for inputs, code in cases:
            with self.subTest(code=code):
                self.assertEqual(classify_reason(**inputs), ("BLOCKED", code))


if __name__ == "__main__":
    unittest.main()
