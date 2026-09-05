from __future__ import annotations

import copy
import hashlib
import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
from scipy import sparse


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from run_rec_ev_023ef_transfer import (  # noqa: E402
    ENDPOINTS,
    ReaderFirewallError,
    ResumeError,
    active_scores,
    analytic_random_top2,
    build_feature_heads,
    build_rank_frame,
    compute_bootstrap_arrays,
    contrast_metadata,
    decision_from_intervals,
    finalize_or_verify_result,
    _profile_rating_pass,
    iter_allowed_movie_lines,
    parse_allowed_movie_line,
    poisson_cutoffs,
    poisson_user_weight,
    require_same_frame,
    require_same_sparse,
    analyze,
    bootstrap,
    materialize_metrics,
    prepare,
    progress_update,
    score,
    validate_progress,
    strict_score_order,
    verify_integrity,
    write_integrity,
)
from rec_ev_022a_core import old_user_bucket, user_role_bucket  # noqa: E402
from validate_rec_ev_023e_contract import validate_contract as validate_e  # noqa: E402
from validate_rec_ev_023f_contract import validate_contract as validate_f  # noqa: E402


class RecEv023efTransferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.e = json.loads((ROOT / "docs/recommendation/contracts/rec-ev-023e-korean-origin-transfer.json").read_text(encoding="utf-8"))
        cls.f = json.loads((ROOT / "docs/recommendation/contracts/rec-ev-023f-recent-release-transfer.json").read_text(encoding="utf-8"))
        cls.joint = json.loads((ROOT / "docs/recommendation/contracts/rec-ev-023ef-joint-transfer-design.json").read_text(encoding="utf-8"))

    def test_contracts_and_mutations_fail_closed(self) -> None:
        self.assertEqual(validate_e(self.e)["primary_family"], 108)
        self.assertEqual(validate_f(self.f)["primary_family"], 108)
        for validator, contract in ((validate_e, self.e), (validate_f, self.f)):
            for mutate in (
                lambda value: value["decision"].__setitem__("maximum_half_width", 0.1),
                lambda value: value["scoring"].__setitem__("b0", "ALLOWED"),
                lambda value: value["reader"].__setitem__("final_reserve_forbidden", [9999, 9999]),
            ):
                changed = copy.deepcopy(contract)
                mutate(changed)
                with self.assertRaises(RuntimeError):
                    validator(changed)

    def test_excluded_row_is_discarded_before_movie_or_rating_parse(self) -> None:
        excluded = next(user for user in range(1, 10000) if old_user_bucket(user) > 59 or not 6000 <= user_role_bucket(user) <= 9199)
        self.assertIsNone(parse_allowed_movie_line(f"{excluded},NOT_A_MOVIE,NOT_A_RATING,SECRET".encode()))
        allowed = next(user for user in range(1, 10000) if old_user_bucket(user) <= 59 and 6000 <= user_role_bucket(user) <= 9199)
        parsed = parse_allowed_movie_line(f"{allowed},17,NOT_A_RATING,DO_NOT_PARSE".encode())
        self.assertEqual(parsed, (allowed, 17, len(str(allowed)) + 3))

    def test_reader_rejects_duplicate_and_out_of_order_allowed_rows(self) -> None:
        allowed = [user for user in range(1, 10000) if old_user_bucket(user) <= 59 and 6000 <= user_role_bucket(user) <= 9199]
        user = allowed[0]
        with self.assertRaises(ReaderFirewallError):
            list(iter_allowed_movie_lines([f"{user},9,4.0,x".encode(), f"{user},9,3.0,y".encode()]))
        later, earlier = allowed[1], allowed[0]
        if later < earlier:
            later, earlier = earlier, later
        with self.assertRaises(ReaderFirewallError):
            list(iter_allowed_movie_lines([f"{later},9,4.0,x".encode(), f"{earlier},10,3.0,y".encode()]))

    def test_score_input_pass_parses_only_selected_profile_rating(self) -> None:
        allowed = next(user for user in range(1, 10000) if old_user_bucket(user) <= 59 and 6000 <= user_role_bucket(user) <= 9199)
        profile_line = f"{allowed},11,4.5,DO_NOT_PARSE".encode()
        target_line = f"{allowed},12,NOT_A_RATING,DO_NOT_PARSE".encode()
        profile_second = profile_line.find(b",", profile_line.find(b",") + 1)
        target_second = target_line.find(b",", target_line.find(b",") + 1)
        fake_rows = [(allowed, 11, profile_line, profile_second), (allowed, 12, target_line, target_second)]
        with mock.patch("run_rec_ev_023ef_transfer.movie_lens_movie_rows", return_value=iter(fake_rows)):
            ratings, parsed = _profile_rating_pass(self.e, {allowed: {11}})
        self.assertEqual(ratings, {allowed: {11: 8}})
        self.assertEqual(parsed, 1)

    def test_fixed_group_normalization_and_head_shapes(self) -> None:
        frame = pd.DataFrame([
            {"movie_id": 1, "feature_eligible": True, "release_year": 2001.0, "runtime_minutes": 91.0,
             "original_language": "ko", "genre_ids": np.array([1, 2]), "director_ids": np.array([10]),
             "top5_cast_ids": np.array([20, 21]), "keyword_ids": np.array([30])},
            {"movie_id": 2, "feature_eligible": True, "release_year": 2022.0, "runtime_minutes": np.nan,
             "original_language": "en", "genre_ids": np.array([2]), "director_ids": np.array([], dtype=int),
             "top5_cast_ids": np.array([], dtype=int), "keyword_ids": np.array([], dtype=int)},
            {"movie_id": 3, "feature_eligible": False, "release_year": 2020.0, "runtime_minutes": 100.0,
             "original_language": "en", "genre_ids": np.array([3]), "director_ids": np.array([11]),
             "top5_cast_ids": np.array([22]), "keyword_ids": np.array([31])},
        ])
        movie_ids, heads, years, _ = build_feature_heads(frame)
        self.assertEqual(movie_ids.tolist(), [1, 2])
        self.assertEqual(years.tolist(), [2001, 2022])
        self.assertLess(heads["BASIC"].shape[1], heads["RELEASE_PROXY"].shape[1])
        self.assertLess(heads["RELEASE_PROXY"].shape[1], heads["FULL_CURRENT"].shape[1])
        for matrix in heads.values():
            norms = np.sqrt(np.asarray(matrix.multiply(matrix).sum(axis=1)).ravel())
            np.testing.assert_allclose(norms, np.ones(2), atol=1e-6)

    def test_score_inactive_rules_and_partial_tie(self) -> None:
        scores, active = active_scores(np.ones((3, 2)), np.zeros(2))
        self.assertFalse(active)
        np.testing.assert_array_equal(scores, np.zeros(3))
        scores, active = active_scores(np.ones((3, 2)), np.array([1.0, -1.0]))
        self.assertFalse(active)
        contract = self.e
        key = "a" * 64
        movies = [30, 10, 20]
        values = [0.5, 0.5, 0.1]
        observed = strict_score_order(contract, key, 2, "TARGET", "BASIC", "BINARY_SIGN", 6, movies, values)
        prefix = contract["scoring"]["tie_prefix"]
        tied = sorted([30, 10], key=lambda movie: (
            hashlib.sha256(f"{prefix}|{key}|2|TARGET|BASIC|BINARY_SIGN|6|{movie}".encode()).digest(), movie,
        ))
        self.assertEqual(observed, tied + [20])

    def test_exact_uniform_unordered_pair_expectation(self) -> None:
        q = np.array([0.1, 0.4, 0.9, 1.0])
        utility, loss = analytic_random_top2(q)
        pairs = [(left, right) for left in range(len(q) - 1) for right in range(left + 1, len(q))]
        self.assertAlmostEqual(utility, np.mean([(q[left] + q[right]) / 2 for left, right in pairs]))
        self.assertAlmostEqual(loss, np.mean([1 - min(q[left], q[right]) for left, right in pairs]))

    def test_contrast_family_order_and_poisson_goldens(self) -> None:
        for contract in (self.e, self.f):
            metadata = contrast_metadata(contract)
            self.assertEqual(len(metadata), 108)
            self.assertEqual([row["contrast_index"] for row in metadata], list(range(108)))
        cutoffs = poisson_cutoffs(precision=80)
        for row in self.joint["statistics"]["poisson_golden_fixtures"]:
            weight, x_value = poisson_user_weight(row["evidence_id"], row["attempt"], row["user_key"], cutoffs)
            self.assertEqual((weight, x_value), (row["weight"], row["uint64"]))

    def _passing_intervals(self, contract: dict) -> list[dict]:
        rows = []
        for meta in contrast_metadata(contract):
            if meta["class"] == "TARGET_IMPROVEMENT":
                mean = 0.04
            elif meta["class"] == "CONDITIONAL_GAP":
                mean = 0.0
            else:
                mean = 0.01
            rows.append({"contrast_index": meta["contrast_index"], "mean": mean, "se": 0.001,
                         "estimable": True, "half_width": 0.005, "low": mean - 0.005, "high": mean + 0.005})
        return rows

    def _passing_panel_metrics(self, contract: dict) -> pd.DataFrame:
        rows = []
        for panel in range(4):
            for head in contract["features"]["heads"]:
                for cell in contract["cells"]:
                    for domain, value in (("TARGET", 0.04), ("CONTROL", 0.04)):
                        rows.append({"user_key": "a" * 64, "panel": panel, "domain": domain, "head": head,
                                     "encoding": cell["encoding"], "k": cell["k"],
                                     "utility_improvement": value, "safety_improvement": value})
        return pd.DataFrame(rows)

    def test_decision_precedence_and_all_six_gate(self) -> None:
        intervals = self._passing_intervals(self.e)
        panels = self._passing_panel_metrics(self.e)
        decision = decision_from_intervals(self.e, intervals, panels)
        self.assertEqual(decision["status"], "TARGET_SIGNAL_AND_CONDITIONAL_NONINFERIOR")
        self.assertEqual(decision["first_hierarchical_joint_pass"], "BASIC")
        intervals[0] = {**intervals[0], "estimable": False, "half_width": None, "low": None, "high": None}
        decision = decision_from_intervals(self.e, intervals, panels)
        self.assertEqual(decision["status"], "INCONCLUSIVE_PRECISION_OR_NONESTIMABLE")

    def test_complete_result_rejects_metadata_and_semantic_forgery(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / ".codex-tmp") as directory:
            root = Path(directory)
            result_path = root / "result.json"
            selection_path = root / "selection.json"
            integrity_path = root / "result.integrity.json"
            result = {"status": "EXPECTED", "value": 1}
            selection = {"status": "EXPECTED", "champion": None}
            metadata = {"status": "EXPECTED", "users": 1, "contrasts": 108, "champion": None}
            signature = "a" * 64
            self.assertEqual(finalize_or_verify_result(
                result_path, selection_path, integrity_path, result=result, selection=selection,
                signature=signature, metadata=metadata,
            ), "WROTE_RESULT")
            manifest = json.loads(integrity_path.read_text(encoding="utf-8"))
            manifest["metadata"]["status"] = "FORGED"
            integrity_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
            with self.assertRaises(ResumeError):
                finalize_or_verify_result(
                    result_path, selection_path, integrity_path, result=result, selection=selection,
                    signature=signature, metadata=metadata,
                )
            write_integrity(
                integrity_path, {"result": result_path, "selection": selection_path},
                signature=signature, metadata=metadata,
            )
            result_path.write_text(json.dumps({"status": "FORGED", "value": 1}), encoding="utf-8")
            write_integrity(
                integrity_path, {"result": result_path, "selection": selection_path},
                signature=signature, metadata=metadata,
            )
            with self.assertRaises(ResumeError):
                finalize_or_verify_result(
                    result_path, selection_path, integrity_path, result=result, selection=selection,
                    signature=signature, metadata=metadata,
                )

    def test_panel_metric_artifact_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / ".codex-tmp") as directory:
            root = Path(directory)
            panel = root / "panel.parquet"
            integrity = root / "panel.integrity.json"
            panel.write_bytes(b"SEALED")
            write_integrity(integrity, {"panel_metrics": panel}, signature="b" * 64, metadata={"rows": 1})
            panel.write_bytes(b"DRIFTED")
            with self.assertRaises(ResumeError):
                verify_integrity(
                    integrity, {"panel_metrics": panel}, signature="b" * 64,
                    expected_metadata={"rows": 1},
                )

    def test_same_shape_forged_bootstrap_is_not_exact_recomputation(self) -> None:
        keys = [f"{index:064x}" for index in range(4)]
        values = np.arange(4 * 108, dtype=np.float64).reshape(4, 108) / 1000.0
        point, replicates, valid, invalid, metadata = compute_bootstrap_arrays(self.e, keys, values)
        self.assertEqual(replicates.shape, (4000, 108))
        self.assertEqual(metadata["valid_replicates"], 4000)
        forged = np.broadcast_to(point, replicates.shape).copy()
        self.assertFalse(np.array_equal(forged, replicates))
        again = compute_bootstrap_arrays(self.e, keys, values)
        for expected, actual in zip((point, replicates, valid, invalid), again[:4], strict=True):
            np.testing.assert_array_equal(expected, actual)

    def test_resealed_panel_or_contrast_semantic_change_is_rejected(self) -> None:
        expected = pd.DataFrame([{"user_key": "a" * 64, "value": 0.1}])
        forged = pd.DataFrame([{"user_key": "a" * 64, "value": 0.2}])
        with self.assertRaises(ResumeError):
            require_same_frame(expected, forged, "panel metrics")

    def test_resealed_prepared_matrix_and_rank_part_are_rejected(self) -> None:
        expected_matrix = sparse.eye(3, format="csr", dtype=np.float32)
        forged_matrix = expected_matrix.copy()
        forged_matrix.data[0] = np.float32(0.5)
        with self.assertRaises(ResumeError):
            require_same_sparse(expected_matrix, forged_matrix, "prepared feature")
        rng = np.random.default_rng(17)
        dense = rng.random((34, 5), dtype=np.float32)
        dense /= np.linalg.norm(dense, axis=1, keepdims=True)
        matrix = sparse.csr_matrix(dense)
        selected = pd.DataFrame([{
            "user_key": "a" * 64, "panel": 0,
            "profile_movie_ids": list(range(1, 15)),
            "target_movie_ids": list(range(15, 25)),
            "control_movie_ids": list(range(25, 35)),
            "profile_rating_idx": [index % 10 for index in range(14)],
        }])
        expected_rank = build_rank_frame(
            self.e, selected, {movie: movie - 1 for movie in range(1, 35)},
            {head: matrix for head in self.e["features"]["heads"]}, np.linspace(0.05, 0.95, 10),
        )
        forged_rank = expected_rank.copy(deep=True)
        forged_rank.loc[0, "active"] = not bool(forged_rank.loc[0, "active"])
        with self.assertRaises(ResumeError):
            require_same_frame(expected_rank, forged_rank, "rank part")

    def test_phase_chain_cannot_skip_upstream_recomputation(self) -> None:
        self.assertIn("prepare(contract)", inspect.getsource(score))
        self.assertIn("score(contract)", inspect.getsource(materialize_metrics))
        self.assertIn("materialize_metrics(contract)", inspect.getsource(bootstrap))
        self.assertIn("bootstrap(contract)", inspect.getsource(analyze))

    def test_progress_rejects_schema_unknown_safety_and_regression(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / ".codex-tmp") as directory:
            path = Path(directory) / "progress.json"
            with mock.patch("run_rec_ev_023ef_transfer.output_path", return_value=path), mock.patch(
                "run_rec_ev_023ef_transfer.run_signature", return_value="c" * 64,
            ):
                progress_update(self.e, "SCORING", users_complete=5, users_total=10)
                valid = json.loads(path.read_text(encoding="utf-8"))
                for mutation in (
                    lambda value: value.__setitem__("schema_version", 999),
                    lambda value: value.__setitem__("evidence_id", "WRONG"),
                    lambda value: value.__setitem__("unknown", True),
                    lambda value: value.__setitem__("final_reserve_opened", True),
                ):
                    forged = copy.deepcopy(valid)
                    mutation(forged)
                    path.write_text(json.dumps(forged), encoding="utf-8")
                    with self.assertRaises(ResumeError):
                        validate_progress(self.e)
                path.write_text(json.dumps(valid), encoding="utf-8")
                with self.assertRaises(ResumeError):
                    progress_update(self.e, "SCORING", users_complete=4, users_total=10)
                progress_update(self.e, "METRICS_SEALED", users=319, contrasts=108)
                with self.assertRaises(ResumeError):
                    progress_update(self.e, "SCORING", users_complete=10, users_total=10)


if __name__ == "__main__":
    unittest.main()
