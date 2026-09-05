from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import run_rec_ev_024_anchor_policy as runner  # noqa: E402
from run_rec_ev_024_anchor_policy import (  # noqa: E402
    ENDPOINTS,
    POLICIES,
    _verify_input_label_disjoint,
    build_policy_metrics,
    build_user_contrasts,
    compute_bootstrap_arrays,
    contrast_metadata,
    decision_from_intervals,
    open_evaluation_after_rank_seal,
    open_input_ratings_after_membership_seal,
    policy_profile,
    poisson_cutoffs,
    poisson_user_weight,
    progress_value,
    reconcile_progress,
    sealed_group_state,
    strict_policy_order,
)
from validate_rec_ev_024_anchor_contract import validate_contract  # noqa: E402


CONTRACTS = {
    "REC-EV-024A": ROOT / "docs/recommendation/contracts/rec-ev-024a-korean-anchor-policy.json",
    "REC-EV-024B": ROOT / "docs/recommendation/contracts/rec-ev-024b-recent-anchor-policy.json",
}


def load(evidence_id: str = "REC-EV-024A") -> dict:
    return json.loads(CONTRACTS[evidence_id].read_text(encoding="utf-8"))


def synthetic_policy_metrics(contract: dict, *, target_effect: float = 0.03, control_effect: float = 0.0) -> pd.DataFrame:
    rows = []
    for panel in range(4):
        for cell in contract["cells"]:
            for domain, effect in (("TARGET", target_effect), ("CONTROL", control_effect)):
                for policy in POLICIES:
                    mixed = policy == "TARGET2_MIXED"
                    rows.append({
                        "user_key": "a" * 64, "panel": panel, "domain": domain,
                        "encoding": cell["encoding"], "k": cell["k"], "policy": policy,
                        "active": True,
                        "model_utility": 0.5 + (effect if mixed else 0.0),
                        "model_loss": 0.5 - (effect if mixed else 0.0),
                        "random_utility": 0.5, "random_loss": 0.5,
                        "utility_minus_random": effect if mixed else 0.0,
                        "safety_minus_random": effect if mixed else 0.0,
                    })
    return pd.DataFrame(rows)


class ContractTests(unittest.TestCase):
    def test_exact_contracts(self) -> None:
        validate_contract(load("REC-EV-024A"))
        validate_contract(load("REC-EV-024B"))

    def test_security_mutations_fail(self) -> None:
        base = load()
        mutations = []
        for path, value in (
            (("authorization", "final_reserve_access"), True),
            (("cohort", "minimum_target_ratings"), 2),
            (("decision", "target_margin"), -1.0),
            (("claim_boundary", "allowed"), "PRODUCT_POLICY"),
            (("outputs", "rank"), "../rec-ev-023e/score-rank.parquet"),
            (("scoring", "tie_payload"), "POLICY_INCLUDED"),
        ):
            changed = copy.deepcopy(base)
            changed[path[0]][path[1]] = value
            mutations.append(changed)
        for changed in mutations:
            with self.assertRaises(ValueError):
                validate_contract(changed)


class PolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = load()
        self.value = SimpleNamespace(
            profile_movie_ids=list(range(1, 15)), profile_rating_idx=list(range(10)) + [0, 1, 2, 3],
            anchor_movie_ids=[101, 102], anchor_rating_idx=[8, 9],
        )

    def test_policy_profiles_have_same_k_and_shared_prefix(self) -> None:
        for k in (6, 8, 14):
            source_movies, source_ratings = policy_profile(self.value, "SOURCE_ONLY", k)
            mixed_movies, mixed_ratings = policy_profile(self.value, "TARGET2_MIXED", k)
            self.assertEqual(len(source_movies), k)
            self.assertEqual(len(mixed_movies), k)
            self.assertEqual(source_movies[:k - 2], mixed_movies[:k - 2])
            self.assertEqual(mixed_movies[-2:], [101, 102])
            self.assertEqual(len(source_ratings), len(mixed_ratings))

    def test_tie_order_is_policy_independent_and_deterministic(self) -> None:
        movies = [9, 3, 7, 5]
        scores = [0.0] * 4
        first = strict_policy_order(self.contract, "b" * 64, 2, "TARGET", "BINARY_SIGN", 6, movies, scores)
        second = strict_policy_order(self.contract, "b" * 64, 2, "TARGET", "BINARY_SIGN", 6, list(reversed(movies)), list(reversed(scores)))
        self.assertEqual(first, second)
        self.assertEqual(set(first), set(movies))

    def test_label_overlap_fails(self) -> None:
        frame = pd.DataFrame([{
            "profile_movie_ids": [1, 2], "anchor_movie_ids": [3, 4],
            "target_movie_ids": [4, 5], "control_movie_ids": [6, 7],
        }])
        with self.assertRaises(RuntimeError):
            _verify_input_label_disjoint(frame)

    def test_input_reader_cannot_run_before_membership_integrity(self) -> None:
        frame = pd.DataFrame([{"user_key": "a" * 64, "panel": 0}])
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(runner, "output_path", side_effect=lambda contract, name: Path(directory) / contract["outputs"][name]), \
                mock.patch.object(runner, "_profile_rating_pass") as rating_pass:
            with self.assertRaises(runner.ResumeError):
                open_input_ratings_after_membership_seal(self.contract, {}, frame, {}, "sig")
            rating_pass.assert_not_called()

    def test_evaluation_reader_cannot_run_before_rank_integrity(self) -> None:
        frame = pd.DataFrame([{
            "profile_movie_ids": [1], "anchor_movie_ids": [2],
            "target_movie_ids": [3], "control_movie_ids": [4],
        }])
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(runner, "output_path", side_effect=lambda contract, name: Path(directory) / contract["outputs"][name]), \
                mock.patch.object(runner, "_evaluation_label_pass") as label_pass:
            with self.assertRaises(runner.ResumeError):
                open_evaluation_after_rank_seal(self.contract, frame, "sig")
            label_pass.assert_not_called()


class MetricTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = load()

    def test_inactive_arm_uses_exact_random(self) -> None:
        ranks = pd.DataFrame([{
            "user_key": "c" * 64, "panel": 0, "domain": "TARGET", "encoding": "BINARY_SIGN",
            "k": 6, "policy": policy, "active": False, "ranked_movie_ids": [],
        } for policy in POLICIES])
        labels = pd.DataFrame([{
            "user_key": "c" * 64, "panel": 0, "domain": "TARGET",
            "movie_ids": list(range(10)), "q": [value / 10 for value in range(10)],
        }])
        metrics = build_policy_metrics(ranks, labels)
        self.assertTrue(np.allclose(metrics["model_utility"], metrics["random_utility"]))
        self.assertTrue(np.allclose(metrics["model_loss"], metrics["random_loss"]))

    def test_contrast_directions_and_family_order(self) -> None:
        metrics = synthetic_policy_metrics(self.contract, target_effect=0.03, control_effect=-0.01)
        contrasts = build_user_contrasts(metrics, self.contract)
        self.assertEqual(len(contrast_metadata(self.contract)), 24)
        self.assertEqual(len(contrasts), 24)
        target = contrasts.loc[contrasts["domain"] == "TARGET", "value"]
        control = contrasts.loc[contrasts["domain"] == "CONTROL", "value"]
        self.assertTrue(np.allclose(target, 0.03))
        self.assertTrue(np.allclose(control, -0.01))
        self.assertEqual(set(contrasts["endpoint"]), set(ENDPOINTS))

    def test_robust_and_precision_precedence(self) -> None:
        metrics = synthetic_policy_metrics(self.contract, target_effect=0.03, control_effect=0.0)
        raw = []
        for meta in contrast_metadata(self.contract):
            is_target = meta["domain"] == "TARGET"
            raw.append({
                "contrast_index": meta["contrast_index"], "mean": 0.03 if is_target else 0.0,
                "se": 0.001, "estimable": True, "half_width": 0.005,
                "low": 0.025 if is_target else -0.005, "high": 0.035 if is_target else 0.005,
            })
        decision = decision_from_intervals(self.contract, raw, metrics)
        self.assertEqual(decision["status"], "ROBUST_INPUT_REMEDY")
        raw[0] = {**raw[0], "half_width": 0.051, "low": -0.021}
        decision = decision_from_intervals(self.contract, raw, metrics)
        self.assertEqual(decision["status"], "INCONCLUSIVE_PRECISION_OR_NONESTIMABLE")


class BootstrapTests(unittest.TestCase):
    def test_new_namespace_goldens(self) -> None:
        cutoffs = poisson_cutoffs(precision=80)
        for evidence_id in CONTRACTS:
            joint = json.loads((ROOT / "docs/recommendation/contracts/rec-ev-024ab-anchor-policy-design.json").read_text(encoding="utf-8"))
            for row in [value for value in joint["bootstrap"]["golden_fixtures"] if value["evidence_id"] == evidence_id]:
                weight, uint64 = poisson_user_weight(evidence_id, row["attempt"], row["user_key"], cutoffs)
                self.assertEqual((weight, uint64), (row["weight"], row["uint64"]))

    def test_bootstrap_is_deterministic(self) -> None:
        contract = load()
        keys = [f"{value:064x}" for value in range(4)]
        values = np.arange(96, dtype=np.float64).reshape(4, 24) / 100.0
        left = compute_bootstrap_arrays(contract, keys, values)
        right = compute_bootstrap_arrays(contract, keys, values)
        for l_value, r_value in zip(left[:4], right[:4], strict=True):
            self.assertTrue(np.array_equal(l_value, r_value))
        self.assertEqual(left[1].shape, (4000, 24))


class ProgressRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = load()

    def _patch_output(self, root: Path):
        return mock.patch.object(
            runner, "output_path", side_effect=lambda contract, name: root / str(contract["outputs"][name]),
        )

    def test_exact_artifacts_with_behind_progress_recover(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left, right = root / "a", root / "b"
            left.write_bytes(b"a")
            right.write_bytes(b"b")
            with self._patch_output(root), mock.patch.object(runner, "run_signature", return_value="sig"):
                runner.atomic_write_json(root / self.contract["outputs"]["progress"], progress_value(self.contract, "MEMBERSHIP_SEALED", 4))
                self.assertTrue(sealed_group_state(self.contract, "SCORE_INPUT_OPEN", [left, right]))
                reconcile_progress(self.contract, "SCORE_INPUT_OPEN", 8)
                self.assertEqual(runner.read_json(root / self.contract["outputs"]["progress"])["phase"], "SCORE_INPUT_OPEN")

    def test_ahead_progress_with_missing_artifacts_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self._patch_output(root), mock.patch.object(runner, "run_signature", return_value="sig"):
                runner.atomic_write_json(root / self.contract["outputs"]["progress"], progress_value(self.contract, "COMPLETE", 231))
                with self.assertRaises(runner.ResumeError):
                    sealed_group_state(self.contract, "SCORE_INPUT_OPEN", [root / "missing-a", root / "missing-b"])
                with self.assertRaises(runner.ResumeError):
                    sealed_group_state(self.contract, "RANK_SEALED", [root / "missing-rank", root / "missing-rank-integrity"])
                with self.assertRaises(runner.ResumeError):
                    sealed_group_state(self.contract, "RANK_SEALED", [root / "missing-part", root / "missing-part-integrity"])
                with self.assertRaises(runner.ResumeError):
                    sealed_group_state(self.contract, "COMPLETE", [root / "missing-result", root / "missing-selection", root / "missing-integrity"])

    def test_partial_pair_always_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            present, missing = root / "present", root / "missing"
            present.write_bytes(b"x")
            with self._patch_output(root), mock.patch.object(runner, "run_signature", return_value="sig"):
                with self.assertRaises(runner.ResumeError):
                    sealed_group_state(self.contract, "RANK_SEALED", [present, missing])


if __name__ == "__main__":
    unittest.main()
