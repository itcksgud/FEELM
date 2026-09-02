from __future__ import annotations

import copy
import unittest

from validate_recommendation_vnext_readiness import (
    read_json,
    read_yaml,
    validate_019a_completion_manifest,
    validate_019b_completion_manifest,
    validate_019c_contract_readiness,
    validate_019c_dependency_smoke,
    validate_019c_synthetic_preflight,
    validate_artifact_contracts,
    validate_backlog,
    validate_protocol,
)


class RecommendationVnextReadinessValidatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protocol = read_json(
            "docs/recommendation/protocols/rec-eval-vnext.json"
        )
        self.backlog = read_yaml(
            "docs/tasks/recommendation-evidence-backlog.yaml"
        )

    def test_current_protocol_and_backlog_pass(self) -> None:
        validate_protocol(self.protocol)
        validate_artifact_contracts()
        validate_backlog(self.backlog)
        cohort_manifest = validate_019a_completion_manifest()
        self.assertEqual("PASS_COHORT_GATES", cohort_manifest["status"])
        manifest = validate_019b_completion_manifest()
        self.assertEqual("PASS_FULL_GATES", manifest["status"])
        contract = validate_019c_contract_readiness()
        self.assertEqual("PASS", contract["status"])
        synthetic = validate_019c_synthetic_preflight()
        self.assertEqual("PASS", synthetic["status"])
        dependency = validate_019c_dependency_smoke()
        self.assertEqual("PASS", dependency["status"])

    def test_rejects_smaller_test_split(self) -> None:
        mutated = copy.deepcopy(self.protocol)
        mutated["user_split"]["test_buckets"] = [90, 99]
        with self.assertRaisesRegex(RuntimeError, "split"):
            validate_protocol(mutated)

    def test_rejects_weaker_effect_gate(self) -> None:
        mutated = copy.deepcopy(self.protocol)
        mutated["statistics"]["ranking_sesoi_absolute_ndcg_at_10"] = 0.0
        with self.assertRaisesRegex(RuntimeError, "statistical"):
            validate_protocol(mutated)

    def test_rejects_invented_champion(self) -> None:
        mutated = copy.deepcopy(self.protocol)
        mutated["adoption"]["personal_champion"] = "UNTESTED_MODEL"
        with self.assertRaisesRegex(RuntimeError, "champion"):
            validate_protocol(mutated)

    def test_rejects_backlog_that_reopens_completed_cohort_task(self) -> None:
        mutated = copy.deepcopy(self.backlog)
        task = next(
            item for item in mutated["tasks"] if item["id"] == "TASK-REC-EV-019A"
        )
        task["status"] = "READY"
        with self.assertRaisesRegex(RuntimeError, "019A"):
            validate_backlog(mutated)

    def test_rejects_candidate_policy_that_drops_missing_features(self) -> None:
        mutated = copy.deepcopy(self.protocol)
        mutated["candidate"]["missing_model_artifact_policy"] = "DROP"
        with self.assertRaisesRegex(RuntimeError, "candidate"):
            validate_protocol(mutated)

    def test_rejects_ready_task_without_executable_commands(self) -> None:
        mutated = copy.deepcopy(self.backlog)
        task = next(
            item for item in mutated["tasks"] if item["id"] == "TASK-REC-EV-019B"
        )
        task["commands"].pop("verify")
        with self.assertRaisesRegex(RuntimeError, "commands"):
            validate_backlog(mutated)

    def test_rejects_019c_locked_test_authorization_in_backlog(self) -> None:
        mutated = copy.deepcopy(self.backlog)
        task = next(
            item for item in mutated["tasks"] if item["id"] == "TASK-REC-EV-019C"
        )
        task["current_authorization"] = "LOCKED_TEST"
        with self.assertRaisesRegex(RuntimeError, "019C"):
            validate_backlog(mutated)


if __name__ == "__main__":
    unittest.main()
