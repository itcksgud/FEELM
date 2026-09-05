from __future__ import annotations

import bisect
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from decimal import Decimal, localcontext
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
from scipy import sparse


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import run_rec_ev_025_feature_transfer as module  # noqa: E402
from validate_rec_ev_025_feature_transfer_contract import validate_contract  # noqa: E402


CONTRACT_PATH = ROOT / "docs/recommendation/contracts/rec-ev-025ab-feature-transfer-execution.json"


def load() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


class ContractTests(unittest.TestCase):
    def test_exact_contract(self) -> None:
        validate_contract(load())

    def test_mutations_fail(self) -> None:
        base = load()
        mutations = []
        for section, key, value in (
            ("authorization", "final_reserve_access", True),
            ("common_support", "e5_dimension", 768),
            ("statistics", "joint_family_each_experiment", 72),
            ("decision", "incremental_target_safety_margin", 0.0),
            ("output_roots", "REC-EV-025A", "outputs/recommendation-evidence/rec-ev-023e"),
        ):
            changed = copy.deepcopy(base)
            changed[section][key] = value
            mutations.append(changed)
        for changed in mutations:
            with self.assertRaises(ValueError):
                validate_contract(changed)


class ContrastTests(unittest.TestCase):
    def test_exact_216_enumeration(self) -> None:
        rows = module.contrast_metadata(load())
        self.assertEqual(len(rows), 216)
        self.assertEqual([row["contrast_index"] for row in rows], list(range(216)))
        self.assertEqual(rows[0], {"contrast_index": 0, "family": "ABSOLUTE", "head": "GENRE_ONLY", "challenger": "", "encoding": "BINARY_SIGN", "k": 6, "class": "TARGET_IMPROVEMENT", "domain": "", "endpoint": "UTILITY_IMPROVEMENT_MODEL_MINUS_RANDOM"})
        self.assertEqual(rows[143]["head"], "CURRENT_FULL")
        self.assertEqual(rows[144], {"contrast_index": 144, "family": "INCREMENTAL", "head": "", "challenger": "GENRE_ONLY", "encoding": "BINARY_SIGN", "k": 6, "class": "", "domain": "TARGET", "endpoint": "UTILITY_CHALLENGER_MINUS_CURRENT"})
        self.assertEqual(rows[-1]["challenger"], "E5")
        self.assertEqual(rows[-1]["endpoint"], "SAFETY_CHALLENGER_MINUS_CURRENT")

    def _metrics(self) -> pd.DataFrame:
        rows = []
        for panel in range(4):
            for domain in module.DOMAINS:
                for head_i, head in enumerate(module.HEADS):
                    for cell in load()["cells"]:
                        utility = 0.50 + 0.01 * head_i + (0.02 if domain == "TARGET" else 0.0)
                        loss = 0.50 - 0.005 * head_i
                        rows.append({"user_key": "u", "panel": panel, "domain": domain, "head": head, "encoding": cell["encoding"], "k": cell["k"], "active": True, "model_utility": utility, "model_loss": loss, "random_utility": 0.40, "random_loss": 0.60, "utility_minus_random": utility - 0.40, "safety_minus_random": 0.60 - loss})
        return pd.DataFrame(rows)

    def test_estimand_directions_and_user_panel_mean(self) -> None:
        result = module.build_user_contrasts(self._metrics(), load()).set_index("contrast_index")
        self.assertAlmostEqual(float(result.loc[0, "value"]), 0.12)
        self.assertAlmostEqual(float(result.loc[4, "value"]), 0.02)
        self.assertAlmostEqual(float(result.loc[144, "value"]), -0.03)
        self.assertAlmostEqual(float(result.loc[145, "value"]), -0.015)


class RankingTests(unittest.TestCase):
    def test_tie_is_head_omitted_and_deterministic(self) -> None:
        contract = load()
        left = module.strict_head_order(contract, "REC-EV-025A", "a" * 64, 0, "TARGET", "BINARY_SIGN", 6, [3, 2, 1], [0.0, 0.0, 0.0])
        right = module.strict_head_order(contract, "REC-EV-025A", "a" * 64, 0, "TARGET", "BINARY_SIGN", 6, [1, 3, 2], [0.0, 0.0, 0.0])
        self.assertEqual(left, right)

    def test_e5_negative_cosine_is_preserved(self) -> None:
        contract = load()
        row = pd.DataFrame([{"user_key": "b" * 64, "panel": 0, "profile_movie_ids": list(range(1, 15)), "profile_rating_idx": [9, 8, 7, 6, 5, 4, 3, 2, 1, 0, 9, 7, 5, 3], "target_movie_ids": [15, 16], "control_movie_ids": [17, 18]}])
        ids = list(range(1, 19))
        lookup = {movie: index for index, movie in enumerate(ids)}
        dense = np.zeros((18, 2), dtype=np.float32)
        dense[:7, 0] = 1.0
        dense[7:14, 0] = -1.0
        dense[14] = [1.0, 0.0]
        dense[15] = [-1.0, 0.0]
        dense[16] = [0.5, 0.0]
        dense[17] = [-0.5, 0.0]
        identity = sparse.csr_matrix(dense)
        matrices = {"GENRE_ONLY": identity, "TRANSFER_NO_CONTEXT": identity, "E5": dense, "CURRENT_FULL": identity}
        prior = np.full(10, 0.5, dtype=np.float64)
        ranks = module.build_rank_frame(contract, "REC-EV-025A", row, lookup, matrices, prior)
        e5 = ranks.loc[(ranks["head"] == "E5") & (ranks["domain"] == "TARGET")]
        active = e5.loc[e5["active"]]
        self.assertGreater(len(active), 0)
        binary8 = e5.loc[(e5["encoding"] == "BINARY_SIGN") & (e5["k"] == 8)].iloc[0]
        percentile6 = e5.loc[(e5["encoding"] == "PERCENTILE_MAGNITUDE") & (e5["k"] == 6)].iloc[0]
        self.assertEqual(list(binary8.ranked_movie_ids), [15, 16])
        self.assertEqual(list(percentile6.ranked_movie_ids), [16, 15])


class BootstrapTests(unittest.TestCase):
    def test_golden_fixtures(self) -> None:
        with localcontext() as context:
            context.prec = 80
            p0 = (-Decimal(1)).exp()
            term, cumulative = Decimal(1), Decimal(1)
            cutoffs = []
            for k in range(64):
                cutoffs.append(min(int(((p0 * cumulative * Decimal(2**65)) - Decimal(1)) // Decimal(2)), 2**64 - 1))
                if cutoffs[-1] >= 2**64 - 1:
                    break
                term /= Decimal(k + 1)
                cumulative += term
        for row in load()["bootstrap_golden_fixtures"]:
            weight, value = module.poisson_user_weight(row["evidence_id"], row["attempt"], row["user_key"], cutoffs)
            self.assertEqual((value, weight), (row["uint64"], row["weight"]))

    def test_joint_interval_shape_is_required(self) -> None:
        with self.assertRaises(ValueError):
            module.simultaneous_intervals(np.zeros(72), np.zeros((4000, 72)))


class DecisionTests(unittest.TestCase):
    def _intervals(self, mean: float = 0.03, width: float = 0.001) -> list[dict]:
        return [{"contrast_index": index, "mean": mean, "se": 0.001, "estimable": True, "half_width": width, "low": mean - width, "high": mean + width} for index in range(216)]

    def _panel_metrics(self) -> pd.DataFrame:
        rows = []
        for user in ("a", "b"):
            for panel in range(4):
                for domain in module.DOMAINS:
                    for head_i, head in enumerate(module.HEADS):
                        for cell in load()["cells"]:
                            rows.append({"user_key": user, "panel": panel, "domain": domain, "head": head, "encoding": cell["encoding"], "k": cell["k"], "model_utility": 0.50 + 0.01 * (3 - head_i), "model_loss": 0.50 - 0.02 * (3 - head_i), "utility_minus_random": 0.03, "safety_minus_random": 0.03})
        return pd.DataFrame(rows)

    def test_any_nonestimable_precedes_signal(self) -> None:
        intervals = self._intervals()
        intervals[215]["estimable"] = False
        intervals[215]["half_width"] = None
        intervals[215]["low"] = None
        decision = module.decision_from_intervals(load(), "REC-EV-025A", intervals, self._panel_metrics())
        self.assertEqual(decision["status"], "INCONCLUSIVE_PRECISION_OR_NONESTIMABLE")

    def test_strict_width_boundary_is_inconclusive(self) -> None:
        decision = module.decision_from_intervals(load(), "REC-EV-025A", self._intervals(width=0.05), self._panel_metrics())
        self.assertEqual(decision["status"], "INCONCLUSIVE_PRECISION_OR_NONESTIMABLE")

    def test_nonfinite_fields_and_nonpositive_se_are_inconclusive(self) -> None:
        cases = [("mean", np.nan), ("se", np.inf), ("low", np.nan), ("high", np.inf), ("half_width", np.nan), ("se", 0.0), ("se", -0.001)]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                intervals = self._intervals()
                intervals[215][field] = value
                decision = module.decision_from_intervals(load(), "REC-EV-025A", intervals, self._panel_metrics())
                self.assertEqual(decision["status"], "INCONCLUSIVE_PRECISION_OR_NONESTIMABLE")
                self.assertTrue(decision["precision_or_estimability_failure"])


class ResumeAndFirewallTests(unittest.TestCase):
    def test_absent_lock_resume_fails_before_hashing(self) -> None:
        contract = load()
        with tempfile.TemporaryDirectory(dir=ROOT / ".codex-tmp") as directory:
            root = Path(directory)
            with mock.patch.object(module, "output_path", side_effect=lambda c, e, name: root / e / c["outputs"][name]), mock.patch.object(module, "expected_lock_state") as expected:
                with self.assertRaises(module.ResumeError):
                    module.create_or_verify_lock(contract, "REC-EV-025A", resume=True)
                expected.assert_not_called()

    def test_partial_sealed_group_fails(self) -> None:
        contract = load()
        with tempfile.TemporaryDirectory(dir=ROOT / ".codex-tmp") as directory:
            root = Path(directory)
            one, two = root / "one", root / "two"
            one.write_text("x", encoding="utf-8")
            with self.assertRaises(module.ResumeError):
                module.sealed_group_state(contract, "REC-EV-025A", "RANK_SEALED", [one, two])

    def test_evaluation_requires_rank_integrity_before_label_reader(self) -> None:
        contract = load()
        with mock.patch.object(module, "score"), mock.patch.object(module, "run_signature", return_value="sig"), mock.patch.object(module, "verify_integrity", side_effect=module.ResumeError("bad rank")), mock.patch.object(module, "_evaluation_label_pass") as labels:
            with self.assertRaises(module.ResumeError):
                module.evaluation(contract, "REC-EV-025A")
            labels.assert_not_called()


if __name__ == "__main__":
    unittest.main()
