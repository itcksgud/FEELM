from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from run_rec_ev_019c_validation import (
    AuthorizationError,
    InputFirewall,
    InputFirewallError,
    ResumeSignatureError,
    build_parser,
    checkpoint_write_or_resume,
    effective_percentile_scores,
    expand_trials,
    reciprocal_rank_fusion,
    run_real_validation,
    run_synthetic_preflight,
    stream_top_n,
    write_synthetic_evidence,
)
from verify_rec_ev_019c_validation import verify_manifest


CONTRACT_PATH = ROOT / "docs/recommendation/contracts/rec-ev-019c-validation-artifacts.json"


def contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


class RecEv019CRunnerTest(unittest.TestCase):
    def test_trial_expansion_matches_declared_counts_and_order(self) -> None:
        spec = contract()
        trials = expand_trials(spec)
        self.assertEqual(spec["trial_execution"]["model_order"], list(trials))
        for model_id, rows in trials.items():
            self.assertEqual(spec["models"][model_id]["trial_count"], len(rows))
            self.assertEqual(f"{model_id}-T001", rows[0]["trial_id"])
        self.assertEqual({"prior_strength": 25}, trials["B0_MOVIELENS_BAYESIAN_RATING"][0]["parameters"])
        self.assertEqual(
            {"neighbors": 50, "shrink": 10},
            trials["B2_ITEM_KNN"][0]["parameters"],
        )

    def test_firewall_rejects_before_opener_runs(self) -> None:
        spec = contract()
        firewall = InputFirewall.from_contract(spec, root=ROOT)
        opened: list[Path] = []

        def opener(path: Path) -> bytes:
            opened.append(path)
            return b"ok"

        allowed = next(iter(spec["allowed_input_artifacts"].values()))
        self.assertEqual(b"ok", firewall.read_bytes(allowed, opener=opener))
        self.assertEqual(1, len(opened))
        with self.assertRaises(InputFirewallError):
            firewall.read_bytes(spec["forbidden_input_artifacts"][0], opener=opener)
        with self.assertRaises(InputFirewallError):
            firewall.read_bytes("outputs/not-declared.parquet", opener=opener)
        with self.assertRaises(InputFirewallError):
            firewall.read_bytes(ROOT.parent / "outside.parquet", opener=opener)
        self.assertEqual(1, len(opened))

    def test_streaming_rank_keeps_candidates_uses_fallback_and_tie_break(self) -> None:
        candidates = [1, 2, 3, 4, 5]
        b0 = {1: 0.1, 2: 0.4, 3: 0.9, 4: 0.3, 5: 0.2}
        model = {1: 0.1, 2: 0.8, 4: 0.8, 5: 0.2}
        effective, fallback = effective_percentile_scores(candidates, model, model, b0)
        first = stream_top_n(
            candidates,
            effective,
            fallback,
            seen_movie_ids={1},
            top_n=5,
            candidate_block_size=2,
        )
        second = stream_top_n(
            candidates,
            effective,
            fallback,
            seen_movie_ids={1},
            top_n=5,
            candidate_block_size=5,
        )
        self.assertEqual(first, second)
        self.assertEqual({2, 3, 4, 5}, {row["movie_id"] for row in first})
        self.assertNotIn(1, [row["movie_id"] for row in first])
        positions = {row["movie_id"]: row["rank"] for row in first}
        self.assertLess(positions[2], positions[4])
        self.assertEqual({3}, fallback)
        self.assertTrue(next(row for row in first if row["movie_id"] == 3)["fallback_used"])

    def test_rrf_consumes_ranked_ids_and_is_deterministic(self) -> None:
        first = reciprocal_rank_fusion([[3, 1, 2], [1, 3, 2]], c=10)
        second = reciprocal_rank_fusion([[3, 1, 2], [1, 3, 2]], c=10)
        self.assertEqual(first, second)
        self.assertEqual([1, 3, 2], [row["movie_id"] for row in first])

    def test_checkpoint_resume_is_byte_equivalent_and_mismatch_preserves_old(self) -> None:
        rows = [{"movie_id": 1, "rank": 1}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.json"
            state_a, bytes_a = checkpoint_write_or_resume(path, resume_signature="same", selected_rows=rows)
            state_b, bytes_b = checkpoint_write_or_resume(path, resume_signature="same", selected_rows=rows)
            before = path.read_bytes()
            with self.assertRaises(ResumeSignatureError):
                checkpoint_write_or_resume(path, resume_signature="different", selected_rows=rows)
            self.assertEqual(before, path.read_bytes())
        self.assertEqual("CREATED", state_a)
        self.assertEqual("REUSED", state_b)
        self.assertEqual(bytes_a, bytes_b)

    def test_real_validation_is_blocked(self) -> None:
        spec = contract()
        spec["current_authorization"]["real_validation_fit_or_score"] = False
        with self.assertRaises(AuthorizationError):
            run_real_validation(spec)

    def test_cli_has_no_test_role(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                build_parser().parse_args(["--mode", "synthetic-preflight", "--role", "test"])

    def test_synthetic_preflight_and_manifest_verify_in_isolated_root(self) -> None:
        spec = contract()
        result = run_synthetic_preflight(spec, root=ROOT)
        self.assertTrue(all(result["checks"].values()))
        self.assertFalse(result["real_validation_executed"])
        self.assertFalse(result["locked_test_opened"])

        with tempfile.TemporaryDirectory() as directory:
            isolated = Path(directory)
            contract_target = isolated / CONTRACT_PATH.relative_to(ROOT)
            contract_target.parent.mkdir(parents=True)
            contract_target.write_bytes(CONTRACT_PATH.read_bytes())
            result_copy = dict(result)
            result_copy["contract_sha256"] = __import__("hashlib").sha256(contract_target.read_bytes()).hexdigest()
            _, manifest_path = write_synthetic_evidence(spec, result_copy, root=isolated)
            verified = verify_manifest(manifest_path, root=isolated)
        self.assertEqual("PASS", verified["status"])
        self.assertEqual(15, verified["checks"])


if __name__ == "__main__":
    unittest.main()
