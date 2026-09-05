from __future__ import annotations

from decimal import Decimal, localcontext
import bisect
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "scripts/run_rec_ev_026_experiment.py"
SPEC = importlib.util.spec_from_file_location("run_rec_ev_026_experiment", PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def contract() -> dict:
    return module.load_contract(module.DEFAULT)


class ContractAndStatisticsTests(unittest.TestCase):
    def test_default_contract_and_312_contrasts(self) -> None:
        value = contract()
        rows = module.contrast_metadata(module.design(value))
        self.assertEqual((len(rows), [row["index"] for row in rows]), (312, list(range(312))))

    def test_poisson_cutoffs_match_decimal80_inverse_cdf(self) -> None:
        cutoffs = module.poisson_cutoffs()
        fixtures = module.design(contract())["statistics"]["bootstrap_golden_fixtures"]
        for row in fixtures:
            value = module.bootstrap_uint64(row["attempt"], row["user_key022"])
            self.assertEqual((value, bisect.bisect_left(cutoffs, value)), (row["uint64"], row["weight"]))
        with localcontext() as context:
            context.prec = 80
            for attempt in range(20):
                key = f"{attempt:064x}"
                integer = module.bootstrap_uint64(attempt, key)
                uniform = Decimal(integer) / Decimal(2**64)
                term = (-Decimal(1)).exp()
                cdf = term
                expected = 0
                while uniform > cdf:
                    expected += 1
                    term /= Decimal(expected)
                    cdf += term
                self.assertEqual(bisect.bisect_left(cutoffs, integer), expected)

    def test_analytic_random_top2_and_zero_normalization(self) -> None:
        utility, loss = module.analytic_random([0.0, 0.25, 0.75, 1.0])
        pairs = [(a, b) for i, a in enumerate([0.0, 0.25, 0.75, 1.0]) for b in [0.0, 0.25, 0.75, 1.0][i + 1:]]
        self.assertAlmostEqual(utility, np.mean([(a + b) / 2 for a, b in pairs]))
        self.assertAlmostEqual(loss, np.mean([1 - min(a, b) for a, b in pairs]))
        vector, active = module._normalize(np.zeros(3))
        self.assertFalse(active)
        np.testing.assert_array_equal(vector, np.zeros(3))

    def test_candidate_prediction_gate_is_pre_rating_and_fail_closed(self) -> None:
        with mock.patch.object(module, "scan_ratings") as reader:
            with self.assertRaisesRegex(RuntimeError, "INFEASIBLE_MAPPER_FIT_PRELABEL"):
                module.candidate_prediction_gate(np.zeros((4, 384)), np.ones((384, 128)))
            with self.assertRaisesRegex(RuntimeError, "INFEASIBLE_MAPPER_FIT_PRELABEL"):
                module.candidate_prediction_gate(np.ones((4, 384)), np.full((384, 128), np.nan))
            reader.assert_not_called()

    def test_forged_mapper_npz_is_rejected_semantically(self) -> None:
        expected = {"experiments": {evidence: {"coefficient_sha256": {str(seed): "f" * 64 for seed in module.SEEDS}} for evidence in ("REC-EV-026A", "REC-EV-026B")}}
        arrays = {f"{evidence}_S{seed}": np.zeros((384, 128), dtype=np.float64) for evidence in ("REC-EV-026A", "REC-EV-026B") for seed in module.SEEDS}
        with tempfile.TemporaryDirectory(dir=ROOT / ".codex-tmp") as temp:
            path = Path(temp) / "forged.npz"
            np.savez_compressed(path, **arrays)
            with self.assertRaisesRegex(module.ResumeError, "semantic drift"):
                module.verify_cached_mapper_npz(path, expected)


class ReaderAndPhaseTests(unittest.TestCase):
    def test_rating_reader_parses_only_allowlisted_rating_and_never_timestamp(self) -> None:
        value = contract()
        key = "a" * 64
        with tempfile.TemporaryDirectory(dir=ROOT / ".codex-tmp") as temp:
            archive = Path(temp) / "fixture.zip"
            with zipfile.ZipFile(archive, "w") as target:
                target.writestr("ml-32m/ratings.csv", "userId,movieId,rating,timestamp\n1,7,5.0,BROKEN_TIMESTAMP\n1,8,BROKEN_RATING,BROKEN_TIMESTAMP\n2,BROKEN_MOVIE,BROKEN_RATING,BROKEN_TIMESTAMP\n")
            value["allowed_input_artifacts"]["movielens_archive"]["path"] = str(archive)
            value["allowed_input_artifacts"]["movielens_archive"]["member"] = "ml-32m/ratings.csv"
            rows = module.scan_ratings(value, {1: key}, {(key, 7)})
        self.assertEqual(rows, {key: [(7, 5.0)]})

    def test_profile_cannot_open_before_mapper_gate(self) -> None:
        value = contract()
        with tempfile.TemporaryDirectory(dir=ROOT / ".codex-tmp") as temp:
            root = Path(temp)
            mapping = {key: root / value["outputs"][key] for key in value["outputs"]}
            mapping["progress"].parent.mkdir(parents=True, exist_ok=True)
            mapping["progress"].write_text(json.dumps({"phase": "PROTOCOL_LOCK"}), encoding="utf-8")
            with mock.patch.object(module, "output_path", side_effect=lambda _c, key: mapping[key]), mock.patch.object(module, "signature", return_value="sig"), mock.patch.object(module, "read_json", return_value={"phase": "PROTOCOL_LOCK"}), mock.patch.object(module, "scan_ratings") as reader, mock.patch.object(module, "atomic_parquet") as writer:
                with self.assertRaisesRegex(module.ResumeError, "progress"):
                    module.profile_phase(value)
                reader.assert_not_called()
                writer.assert_not_called()

    def test_resume_without_lock_and_downstream_without_lock_fail_closed(self) -> None:
        value = contract()
        with tempfile.TemporaryDirectory(dir=ROOT / ".codex-tmp") as temp:
            root = Path(temp)
            value["output_root"] = str(root)
            with mock.patch.object(module, "expected_lock") as expected:
                with self.assertRaisesRegex(module.ResumeError, "resume before"):
                    module.create_or_verify_lock(value, True)
                expected.assert_not_called()
            (root / "orphan.parquet").write_bytes(b"orphan")
            with mock.patch.object(module, "expected_lock") as expected:
                with self.assertRaisesRegex(module.ResumeError, "without lock"):
                    module.create_or_verify_lock(value, False)
                expected.assert_not_called()

    def test_rank_code_forbids_surviving_seed_average(self) -> None:
        source = PATH.read_text(encoding="utf-8")
        self.assertIn("bpr_active = all(seed_profiles[seed][1] and np.isfinite(seed_scores[seed]).all() for seed in SEEDS)", source)
        self.assertIn('"inactive_metric": "EXACT_ANALYTIC_RANDOM_TOP2"', module.DEFAULT.read_text(encoding="utf-8"))
        self.assertIn('args.phase in {"mapper", "profile", "rank", "evaluation", "analyze", "run"}', source)
        self.assertIn('args.phase in {"evaluation", "analyze", "run"}', source)


if __name__ == "__main__":
    unittest.main()
