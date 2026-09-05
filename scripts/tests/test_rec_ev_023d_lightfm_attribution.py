from __future__ import annotations

import copy
import json
import math
import tempfile
import unittest

import numpy as np

from scripts.rec_ev_022a_core import RATING_VALUES, encoding_weights
from pathlib import Path

from scripts.run_rec_ev_023d_lightfm_attribution import (
    ROOT as RUN_ROOT,
    _sign_table,
    _stable_sigmoid,
    artifact_state,
    atomic_write_json,
    exact_regular_children,
    fit_seed_state,
    fold_in_batch,
    point_margin_pass,
    result_status,
    seal_or_reuse_analysis,
)
from scripts.validate_rec_ev_023d_contract import CONTRACT, validate


class RecEv023dContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = json.loads(CONTRACT.read_text(encoding="utf-8"))

    def test_contract_passes_without_file_reads(self) -> None:
        validate(self.contract, verify_files=False)

    def test_identity_feature_or_seed_selection_drift_fails(self) -> None:
        for mutate in (
            lambda value: value["feature_support"].__setitem__("identity_features", True),
            lambda value: value["lightfm"].__setitem__("seed_selection", True),
        ):
            changed = copy.deepcopy(self.contract)
            mutate(changed)
            with self.assertRaises(AssertionError):
                validate(changed, verify_files=False)

    def test_family_size_and_carry_boundary_drift_fail(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["statistics"]["expected_contrasts"] = 72
        with self.assertRaises(AssertionError):
            validate(changed, verify_files=False)

    def test_critical_literal_mutations_fail(self) -> None:
        mutations = (
            lambda value: value["train_reader"].__setitem__("read_order", "RATING_FIRST"),
            lambda value: value["target_fold_in"].__setitem__("lipschitz_guard", "DISABLED"),
            lambda value: value["decision"].__setitem__("embedding_mechanism_signal", "ALWAYS_TRUE"),
            lambda value: value["scoring"].__setitem__("head_tie_payload", "CHANGED"),
        )
        for mutation in mutations:
            changed = copy.deepcopy(self.contract)
            mutation(changed)
            with self.assertRaises(AssertionError):
                validate(changed, verify_files=False)
        changed = copy.deepcopy(self.contract)
        changed["carry_forward_equivalence"]["required_equal_to_rec_ev_023c"].append("INTERVAL")
        with self.assertRaises(AssertionError):
            validate(changed, verify_files=False)


class RecEv023dMathTests(unittest.TestCase):
    def test_sign_table_matches_binary_encoding(self) -> None:
        hist = np.zeros((3, 10), dtype=np.uint32)
        hist[1] = np.asarray([1, 0, 2, 0, 1, 3, 0, 2, 0, 4])
        g0 = np.linspace(0.05, 0.95, 10)
        table = _sign_table(hist, g0, tau=5.0)
        expanded = np.repeat(RATING_VALUES, hist[1])
        expected = encoding_weights("BINARY_SIGN", expanded, g0, tau=5.0)
        for rating_index in np.flatnonzero(hist[1]):
            actual_values = expected[expanded == RATING_VALUES[rating_index]]
            self.assertTrue(np.all(actual_values == table[1, rating_index]))

    def test_stable_sigmoid_matches_scalar_formula(self) -> None:
        values = np.asarray([-1000.0, -2.0, 0.0, 2.0, 1000.0])
        observed = _stable_sigmoid(values)
        expected = np.asarray([
            math.exp(value) / (1.0 + math.exp(value)) if value < 0 else 1.0 / (1.0 + math.exp(-value))
            for value in values
        ])
        np.testing.assert_allclose(observed, expected, rtol=0, atol=0)
        self.assertTrue(np.isfinite(observed).all())

    def test_fold_in_batch_matches_scalar_reference_and_allows_one_class(self) -> None:
        biases = np.asarray([0.1, -0.2, 0.05, 0.3], dtype=np.float64)
        factors = np.asarray([[1.0, 0.0], [0.0, 1.0], [0.5, 0.5], [-0.4, 0.2]], dtype=np.float64)
        profile = np.asarray([[0, 1], [1, 2]], dtype=np.int64)
        targets = np.asarray([[2, 3], [0, 3]], dtype=np.int64)
        weights = np.asarray([[1.0, -0.25], [0.2, 0.8]], dtype=np.float64)
        observed, active = fold_in_batch(
            biases, factors, profile, weights, targets, steps=5, learning_rate=0.05, regularization=1e-6,
        )
        expected = []
        for row in range(2):
            w = weights[row]
            confidence = np.abs(w) / np.abs(w).mean()
            labels = np.sign(w)
            user = np.zeros(2, dtype=np.float64)
            for _ in range(5):
                gradient = np.zeros(2, dtype=np.float64)
                for index, item in enumerate(profile[row]):
                    z = biases[item] + user @ factors[item]
                    sig = 1.0 / (1.0 + math.exp(labels[index] * z))
                    gradient -= confidence[index] * labels[index] * sig * factors[item]
                gradient /= confidence.sum()
                gradient += 1e-6 * user
                user -= 0.05 * gradient
            expected.append(biases[targets[row]] + factors[targets[row]] @ user)
        np.testing.assert_allclose(observed, np.asarray(expected), rtol=1e-13, atol=1e-13)
        self.assertEqual(active.tolist(), [True, True])

    def test_zero_weight_batch_falls_back_before_division(self) -> None:
        biases = np.zeros(3)
        factors = np.eye(3)
        scores, active = fold_in_batch(
            biases, factors, np.asarray([[0, 1]]), np.zeros((1, 2)), np.asarray([[1, 2]]), steps=2,
        )
        self.assertFalse(bool(active[0]))
        self.assertTrue(np.isfinite(scores).all())

    def test_q_and_stability_loss_boundaries_are_intentionally_different(self) -> None:
        self.assertFalse(point_margin_pass(0.005, 0.010, utility_margin=0.005, loss_margin=0.010))
        self.assertTrue(point_margin_pass(0.005, 0.009999999, utility_margin=0.005, loss_margin=0.010))

    def test_artifact_state_distinguishes_none_all_and_partial(self) -> None:
        paths = [Path("missing-a"), Path("missing-b")]
        self.assertEqual(artifact_state(paths), "NONE")
        original = Path.exists
        try:
            Path.exists = lambda self: self.name in {"missing-a", "missing-b"}  # type: ignore[method-assign]
            self.assertEqual(artifact_state(paths), "ALL")
            Path.exists = lambda self: self.name == "missing-a"  # type: ignore[method-assign]
            self.assertEqual(artifact_state(paths), "PARTIAL")
        finally:
            Path.exists = original  # type: ignore[method-assign]

    def test_fit_state_and_exact_children_reject_partial_and_unknown(self) -> None:
        with tempfile.TemporaryDirectory(dir=RUN_ROOT / ".codex-tmp") as raw:
            directory = Path(raw) / "S17"
            directory.mkdir()
            self.assertEqual(fit_seed_state(directory), "NONE")
            (directory / "config.json").write_text("{}", encoding="utf-8")
            self.assertEqual(fit_seed_state(directory), "PARTIAL")
            (directory / "result.npz").write_bytes(b"x")
            (directory / "integrity.json").write_text("{}", encoding="utf-8")
            expected = {directory / "config.json", directory / "result.npz", directory / "integrity.json"}
            self.assertEqual(fit_seed_state(directory), "ALL")
            self.assertTrue(exact_regular_children(directory, expected))
            (directory / "junk.parquet").write_bytes(b"x")
            self.assertEqual(fit_seed_state(directory), "PARTIAL")
            self.assertFalse(exact_regular_children(directory, expected))

    def test_status_has_three_noncontradictory_states(self) -> None:
        base = {"embedding_mechanism_signal": False, "lightfm_full_forward": False, "rrf_forward": False}
        self.assertEqual(result_status([base]), "NO_INCREMENTAL_SIGNAL")
        mechanism = {**base, "embedding_mechanism_signal": True}
        self.assertEqual(result_status([mechanism]), "EMBEDDING_MECHANISM_ONLY_SIGNAL")
        full = {**mechanism, "lightfm_full_forward": True}
        self.assertEqual(result_status([full]), "END_TO_END_INCREMENTAL_SIGNAL")

    def test_final_analysis_seal_reuses_without_mtime_change_and_rejects_partial(self) -> None:
        with tempfile.TemporaryDirectory(dir=RUN_ROOT / ".codex-tmp") as raw:
            directory = Path(raw)
            selection_path = directory / "selection.json"
            result_path = directory / "result.json"
            integrity_path = directory / "integrity.json"
            selection = {"status": "NO_INCREMENTAL_SIGNAL", "forward_set": []}
            result = {"status": "NO_INCREMENTAL_SIGNAL"}
            self.assertFalse(seal_or_reuse_analysis(
                selection, result, selection_path=selection_path, result_path=result_path,
                integrity_path=integrity_path, signature="a" * 64,
            ))
            before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in (selection_path, result_path, integrity_path)}
            self.assertTrue(seal_or_reuse_analysis(
                selection, result, selection_path=selection_path, result_path=result_path,
                integrity_path=integrity_path, signature="a" * 64,
            ))
            after = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in (selection_path, result_path, integrity_path)}
            self.assertEqual(before, after)
        with tempfile.TemporaryDirectory(dir=RUN_ROOT / ".codex-tmp") as raw:
            directory = Path(raw)
            selection_path = directory / "selection.json"
            result_path = directory / "result.json"
            integrity_path = directory / "integrity.json"
            atomic_write_json(selection_path, {"status": "partial"})
            with self.assertRaises(RuntimeError):
                seal_or_reuse_analysis(
                    {"status": "NO_INCREMENTAL_SIGNAL", "forward_set": []},
                    {"status": "NO_INCREMENTAL_SIGNAL"},
                    selection_path=selection_path, result_path=result_path,
                    integrity_path=integrity_path, signature="a" * 64,
                )


if __name__ == "__main__":
    unittest.main()
