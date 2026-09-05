from __future__ import annotations

import copy
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from scripts import run_rec_ev_023d_a1_lightfm_attribution as a1
from scripts.run_rec_ev_023d_a1_lightfm_attribution import (
    DEFAULT_CONTRACT,
    _fold_components,
    amend_analysis_payload,
    create_or_verify_lock,
    create_or_verify_schedule,
    fold_in_batch,
    learning_rate_schedule,
    load_effective_contract,
    materialize_metrics,
    output_path,
    rank_preflight,
    seal_amended_analysis,
    verify_predecessor_fit,
    verify_predecessor_layout,
    verify_predecessor,
    verify_rank_set_with_schedule,
)
from scripts.validate_rec_ev_023d_a1_contract import validate


class RecEv023dA1ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = json.loads(DEFAULT_CONTRACT.read_text(encoding="utf-8"))

    def test_contract_and_effective_carry_forward_pass(self) -> None:
        validate(self.contract)
        effective, _ = load_effective_contract()
        self.assertEqual(effective["evidence_id"], "REC-EV-023D-A1")
        self.assertEqual(effective["statistics"]["expected_contrasts"], 156)
        self.assertEqual(effective["decision"]["utility_margin"], 0.005)
        self.assertEqual(effective["target_fold_in"]["learning_rate"], 0.05)

    def test_critical_mutations_fail(self) -> None:
        mutations = (
            lambda value: value["predecessor"].__setitem__("evaluation_labels_opened", True),
            lambda value: value["predecessor"].__setitem__("rank_root_required_absent", False),
            lambda value: value["target_fold_in"].__setitem__("safety_factor", 1.0),
            lambda value: value["target_fold_in"].__setitem__("base_learning_rate", 0.01),
            lambda value: value["target_fold_in"].__setitem__("step_application", "EARLY_STOP"),
            lambda value: value["resume"].__setitem__("foldin_schedule_sealed_before_rank", False),
            lambda value: value["predecessor"]["artifacts"]["predecessor_s17_result"].__setitem__("sha256", "0" * 64),
            lambda value: value["target_fold_in"].__setitem__("lipschitz_bound", "DISABLED"),
            lambda value: value["target_fold_in"].__setitem__("guard", "DISABLED"),
            lambda value: value["target_fold_in"].__setitem__("item_representation_frozen_sha_before_after", False),
            lambda value: value["target_fold_in"].__setitem__("unexpected", "ALLOWED"),
        )
        for mutation in mutations:
            changed = copy.deepcopy(self.contract)
            mutation(changed)
            with self.assertRaises(ValueError):
                validate(changed)

    def test_predecessor_exact_fit_envelope_passes(self) -> None:
        effective, _ = load_effective_contract()
        observed = verify_predecessor(effective)
        self.assertEqual(observed["status"], "PREDECESSOR_FITS_VERIFIED")
        self.assertEqual(observed["seeds"], [17, 42, 73, 101, 211])

    def test_statistics_decision_tie_and_claim_are_exactly_inherited(self) -> None:
        effective, overlay = load_effective_contract()
        inherited = json.loads(Path(overlay["base_contract"]["path"]).read_text(encoding="utf-8"))
        self.assertEqual(effective["statistics"], inherited["statistics"])
        self.assertEqual(effective["decision"], inherited["decision"])
        self.assertEqual(effective["scoring"], inherited["scoring"])
        self.assertEqual(effective["claim_boundary"], inherited["claim_boundary"])

    def test_old_fit_is_verified_with_old_signature_without_reseal(self) -> None:
        effective, _ = load_effective_contract()
        integrity_path = output_path(effective, "prepared_integrity").parent / "lightfm-seeds" / "S17" / "integrity.json"
        before = integrity_path.stat().st_mtime_ns
        with mock.patch.object(a1, "_ORIGINAL_VERIFY_INTEGRITY", wraps=a1._ORIGINAL_VERIFY_INTEGRITY) as verifier:
            verify_predecessor_fit(effective, 17, signature="f" * 64)
        self.assertEqual(verifier.call_args.kwargs["signature"], effective["predecessor"]["run_signature"])
        self.assertEqual(before, integrity_path.stat().st_mtime_ns)

    def test_old_manifest_implementation_mismatch_is_a_blocker(self) -> None:
        effective, _ = load_effective_contract()
        original = a1._verify_entry

        def reject_runner(entry, label):
            if label == "predecessor implementation scripts/run_rec_ev_023d_lightfm_attribution.py":
                raise a1.ResumeError("simulated old runner drift")
            return original(entry, label)

        with mock.patch.object(a1, "_verify_entry", side_effect=reject_runner):
            with self.assertRaisesRegex(a1.ResumeError, "simulated old runner drift"):
                verify_predecessor(effective)

    def test_predecessor_cache_layout_rejects_unknown_child(self) -> None:
        with tempfile.TemporaryDirectory(dir=a1.ROOT / ".codex-tmp") as raw:
            cache = Path(raw)
            for name in (
                "feature-mask.npy", "prepared.integrity.json", "structured-matched.npz",
                "train-interactions.npz", "train-user-keys.npy",
            ):
                (cache / name).write_bytes(b"x")
            seed_root = cache / "lightfm-seeds" / "S17"
            seed_root.mkdir(parents=True)
            for name in ("config.json", "result.npz", "integrity.json"):
                (seed_root / name).write_bytes(b"x")
            verify_predecessor_layout(cache, [17])
            (cache / "unknown.bin").write_bytes(b"x")
            with self.assertRaises(a1.ResumeError):
                verify_predecessor_layout(cache, [17])

    def test_output_routing_separates_old_prepared_from_new_outputs(self) -> None:
        effective, _ = load_effective_contract()
        for name in ("interactions", "train_users", "feature_mask", "structured_matched", "prepared_integrity"):
            self.assertIn("rec-ev-023d", output_path(effective, name).as_posix())
            self.assertNotIn("rec-ev-023d-a1", output_path(effective, name).as_posix())
        for name in effective["outputs"]:
            self.assertIn("rec-ev-023d-a1", output_path(effective, name).as_posix())

    def test_partial_lock_fails_without_overwrite(self) -> None:
        effective, overlay = load_effective_contract()
        with tempfile.TemporaryDirectory(dir=a1.ROOT / ".codex-tmp") as raw:
            effective["output_root"] = raw
            manifest = Path(raw) / effective["outputs"]["source_manifest"]
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_bytes(b"partial")
            before = manifest.read_bytes()
            with (
                mock.patch.object(a1, "verify_predecessor"),
                mock.patch.object(a1.base, "verify_sources", return_value=[]),
                mock.patch.object(a1.base, "verify_implementation", return_value=[]),
                mock.patch.object(a1.base, "verify_upstream"),
            ):
                with self.assertRaises(a1.ResumeError):
                    create_or_verify_lock(effective, overlay, DEFAULT_CONTRACT, resume=False)
            self.assertEqual(before, manifest.read_bytes())

    def test_rank_before_missing_schedule_fails_without_schedule_write(self) -> None:
        effective, _ = load_effective_contract()
        with tempfile.TemporaryDirectory(dir=a1.ROOT / ".codex-tmp") as raw:
            effective["output_root"] = raw
            rank_root = Path(raw) / effective["outputs"]["rank_root"]
            rank_root.mkdir(parents=True)
            with mock.patch.object(a1, "_schedule_arrays") as compute:
                with self.assertRaises(a1.ResumeError):
                    create_or_verify_schedule(effective, resume=True)
            compute.assert_not_called()

    def test_rank_unknown_child_fails_preflight_without_writes(self) -> None:
        effective, _ = load_effective_contract()
        with tempfile.TemporaryDirectory(dir=a1.ROOT / ".codex-tmp") as raw:
            effective["output_root"] = raw
            seed_root = Path(raw) / effective["outputs"]["rank_root"] / "S17"
            seed_root.mkdir(parents=True)
            junk = seed_root / "junk.bin"
            junk.write_bytes(b"x")
            before = junk.stat().st_mtime_ns
            with mock.patch.object(a1, "run_signature", return_value="a" * 64):
                with self.assertRaises(a1.ResumeError):
                    rank_preflight(effective)
            self.assertEqual(before, junk.stat().st_mtime_ns)

    def test_rank_partial_pair_fails_preflight_without_writes(self) -> None:
        effective, _ = load_effective_contract()
        with tempfile.TemporaryDirectory(dir=a1.ROOT / ".codex-tmp") as raw:
            effective["output_root"] = raw
            seed_root = Path(raw) / effective["outputs"]["rank_root"] / "S17"
            seed_root.mkdir(parents=True)
            part = a1.base._part_path(seed_root, 0, 200)
            part.write_bytes(b"partial")
            before = part.stat().st_mtime_ns
            with mock.patch.object(a1, "run_signature", return_value="a" * 64):
                with self.assertRaises(a1.ResumeError):
                    rank_preflight(effective)
            self.assertEqual(before, part.stat().st_mtime_ns)

    def test_missing_schedule_blocks_metrics_before_label_open(self) -> None:
        effective, _ = load_effective_contract()
        with tempfile.TemporaryDirectory(dir=a1.ROOT / ".codex-tmp") as raw:
            effective["output_root"] = raw
            with self.assertRaises((FileNotFoundError, RuntimeError)):
                materialize_metrics(effective)

    def test_rank_set_schedule_digest_mismatch_fails(self) -> None:
        effective, _ = load_effective_contract()
        with tempfile.TemporaryDirectory(dir=a1.ROOT / ".codex-tmp") as raw:
            effective["output_root"] = raw
            schedule_integrity_path = Path(raw) / effective["outputs"]["foldin_schedule_integrity"]
            schedule_integrity_path.parent.mkdir(parents=True)
            schedule_integrity_path.write_bytes(b"integrity")
            fake_schedule = {
                "artifacts": {"foldin_schedule": {"sha256": "a" * 64}},
                "metadata": {},
            }
            fake_rank = {"metadata": {
                "foldin_schedule_sha256": "b" * 64,
                "foldin_schedule_integrity_sha256": a1.base.sha256_file(schedule_integrity_path),
                "foldin_schedule_run_signature": "c" * 64,
                "evaluation_labels_opened": False,
            }}
            with (
                mock.patch.object(a1, "verify_schedule", return_value=fake_schedule),
                mock.patch.object(a1, "_ORIGINAL_VERIFY_RANK_SET", return_value=fake_rank),
                mock.patch.object(a1, "run_signature", return_value="c" * 64),
            ):
                with self.assertRaises(a1.ResumeError):
                    verify_rank_set_with_schedule(effective, signature="c" * 64)

    def test_analysis_payload_sets_all_a1_ids_and_schedule_provenance(self) -> None:
        effective, _ = load_effective_contract()
        schedule = {"artifacts": {"foldin_schedule": {"sha256": "a" * 64}}, "metadata": {"users": 9520}}
        rank = {"metadata": {"foldin_schedule_sha256": "a" * 64}}
        with mock.patch.object(a1.base, "sha256_file", side_effect=["b" * 64, "c" * 64]):
            selection, result = amend_analysis_payload(
                effective,
                {"evidence_id": "REC-EV-023D", "status": "NO_INCREMENTAL_SIGNAL"},
                {"evidence_id": "REC-EV-023D", "selection": {"evidence_id": "REC-EV-023D"}},
                schedule_integrity=schedule,
                rank_integrity=rank,
            )
        self.assertEqual(selection["evidence_id"], "REC-EV-023D-A1")
        self.assertEqual(result["evidence_id"], "REC-EV-023D-A1")
        self.assertEqual(result["selection"]["evidence_id"], "REC-EV-023D-A1")
        self.assertEqual(result["foldin_schedule"]["rank_set_schedule_sha256"], "a" * 64)

    def test_a1_final_seal_first_write_resume_no_write_and_partial_fail(self) -> None:
        effective, _ = load_effective_contract()
        schedule = {"artifacts": {"foldin_schedule": {"sha256": "a" * 64}}, "metadata": {"users": 9520}}
        rank = {"metadata": {"foldin_schedule_sha256": "a" * 64}}
        selection = {"evidence_id": "REC-EV-023D", "status": "NO_INCREMENTAL_SIGNAL", "forward_set": []}
        result = {"evidence_id": "REC-EV-023D", "selection": selection}
        with tempfile.TemporaryDirectory(dir=a1.ROOT / ".codex-tmp") as raw:
            effective["output_root"] = raw
            schedule_integrity_path = Path(raw) / effective["outputs"]["foldin_schedule_integrity"]
            rank_integrity_path = Path(raw) / effective["outputs"]["rank_set_integrity"]
            schedule_integrity_path.parent.mkdir(parents=True)
            schedule_integrity_path.write_bytes(b"schedule-integrity")
            rank_integrity_path.write_bytes(b"rank-integrity")
            selection_path = Path(raw) / "selection.json"
            result_path = Path(raw) / "result.json"
            integrity_path = Path(raw) / "analysis.integrity.json"
            self.assertFalse(seal_amended_analysis(
                effective, selection, result, schedule_integrity=schedule, rank_integrity=rank,
                selection_path=selection_path, result_path=result_path, integrity_path=integrity_path,
                signature="d" * 64,
            ))
            before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in (selection_path, result_path, integrity_path)}
            self.assertTrue(seal_amended_analysis(
                effective, selection, result, schedule_integrity=schedule, rank_integrity=rank,
                selection_path=selection_path, result_path=result_path, integrity_path=integrity_path,
                signature="d" * 64,
            ))
            after = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in (selection_path, result_path, integrity_path)}
            self.assertEqual(before, after)
        with tempfile.TemporaryDirectory(dir=a1.ROOT / ".codex-tmp") as raw:
            effective["output_root"] = raw
            schedule_integrity_path = Path(raw) / effective["outputs"]["foldin_schedule_integrity"]
            rank_integrity_path = Path(raw) / effective["outputs"]["rank_set_integrity"]
            schedule_integrity_path.parent.mkdir(parents=True)
            schedule_integrity_path.write_bytes(b"schedule-integrity")
            rank_integrity_path.write_bytes(b"rank-integrity")
            selection_path = Path(raw) / "selection.json"
            selection_path.write_text("{}", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                seal_amended_analysis(
                    effective, selection, result, schedule_integrity=schedule, rank_integrity=rank,
                    selection_path=selection_path, result_path=Path(raw) / "result.json",
                    integrity_path=Path(raw) / "analysis.integrity.json", signature="d" * 64,
                )


class RecEv023dA1MathTests(unittest.TestCase):
    def test_schedule_covers_low_high_and_inactive_rows(self) -> None:
        factors = np.asarray([
            [1.0, 0.0], [0.0, 1.0],
            [10.0, 0.0], [0.0, 10.0],
        ], dtype=np.float64)
        positions = np.asarray([[0, 1], [2, 3], [0, 1]], dtype=np.int64)
        weights = np.asarray([[1.0, -1.0], [1.0, -1.0], [0.0, 0.0]], dtype=np.float64)
        l_bound, eta, active = learning_rate_schedule(factors, positions, weights)
        self.assertEqual(active.tolist(), [True, True, False])
        self.assertAlmostEqual(float(eta[0]), 0.05)
        self.assertAlmostEqual(float(eta[1]), 0.9 / float(l_bound[1]))
        self.assertEqual(float(eta[2]), 0.0)
        self.assertLessEqual(float(eta[1] * l_bound[1]), 0.9 + 32 * np.finfo(np.float64).eps)

    def test_adaptive_fold_in_matches_scalar_reference_for_exact_80_steps(self) -> None:
        biases = np.asarray([0.1, -0.2, 0.05, 0.3], dtype=np.float64)
        factors = np.asarray([[10.0, 0.0], [0.0, 10.0], [0.5, 0.5], [-0.4, 0.2]], dtype=np.float64)
        profile = np.asarray([[0, 1]], dtype=np.int64)
        targets = np.asarray([[2, 3]], dtype=np.int64)
        weights = np.asarray([[1.0, -0.25]], dtype=np.float64)
        observed, active = fold_in_batch(
            biases, factors, profile, weights, targets, steps=80, learning_rate=0.05, regularization=1e-6,
        )
        _, confidence, labels, total, l_bound, eta = _fold_components(
            factors, profile, weights, regularization=1e-6, base_learning_rate=0.05, safety_factor=0.9,
        )
        user = np.zeros(2, dtype=np.float64)
        for _ in range(80):
            gradient = np.zeros(2, dtype=np.float64)
            for index, item in enumerate(profile[0]):
                z = biases[item] + user @ factors[item]
                sigmoid = 1.0 / (1.0 + math.exp(labels[0, index] * z))
                gradient -= confidence[0, index] * labels[0, index] * sigmoid * factors[item]
            gradient /= total[0]
            gradient += 1e-6 * user
            user -= eta[0] * gradient
        expected = biases[targets[0]] + factors[targets[0]] @ user
        np.testing.assert_allclose(observed[0], expected, rtol=1e-13, atol=1e-13)
        self.assertTrue(bool(active[0]))
        self.assertGreater(float(l_bound[0] * 0.05), 1.0)
        self.assertLessEqual(float(eta[0] * l_bound[0]), 0.9 + 32 * np.finfo(np.float64).eps)

    def test_schedule_is_batch_order_deterministic(self) -> None:
        factors = np.asarray([[1.0, 2.0], [2.0, 0.0], [0.0, 3.0]], dtype=np.float64)
        positions = np.asarray([[0, 1], [1, 2], [2, 0]], dtype=np.int64)
        weights = np.asarray([[1.0, -0.4], [0.3, 0.8], [-0.2, -1.0]], dtype=np.float64)
        expected = learning_rate_schedule(factors, positions, weights)
        permutation = np.asarray([2, 0, 1])
        observed = learning_rate_schedule(factors, positions[permutation], weights[permutation])
        inverse = np.argsort(permutation)
        for before, after in zip(expected, observed):
            np.testing.assert_array_equal(before, after[inverse])

    def test_step_schedule_has_no_target_or_bias_input(self) -> None:
        factors = np.asarray([[1.0, 0.0], [0.0, 2.0], [9.0, 9.0]], dtype=np.float64)
        profile = np.asarray([[0, 1]], dtype=np.int64)
        weights = np.asarray([[1.0, -1.0]], dtype=np.float64)
        first = learning_rate_schedule(factors, profile, weights)
        changed_only_unused_target = factors.copy()
        changed_only_unused_target[2] = np.asarray([-1000.0, 500.0])
        second = learning_rate_schedule(changed_only_unused_target, profile, weights)
        for before, after in zip(first, second):
            np.testing.assert_array_equal(before, after)

    def test_inactive_row_remains_zero_update_and_finite(self) -> None:
        biases = np.zeros(3, dtype=np.float64)
        factors = np.eye(3, dtype=np.float64)
        scores, active = fold_in_batch(
            biases, factors, np.asarray([[0, 1]]), np.zeros((1, 2)), np.asarray([[1, 2]]), steps=80,
        )
        self.assertFalse(bool(active[0]))
        np.testing.assert_array_equal(scores, np.zeros((1, 2)))


if __name__ == "__main__":
    unittest.main()
