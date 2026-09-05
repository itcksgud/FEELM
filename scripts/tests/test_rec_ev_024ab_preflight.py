from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from decimal import Decimal, localcontext
from pathlib import Path
import bisect
from unittest import mock

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import run_rec_ev_024ab_preflight as preflight_module  # noqa: E402
from run_rec_ev_023ef_preflight import allowed_role, movie_id_only_rows  # noqa: E402
from run_rec_ev_024ab_preflight import eligibility_masks, partition_user  # noqa: E402
from validate_rec_ev_024ab_contract import validate_contract  # noqa: E402


CONTRACT_PATH = ROOT / "docs/recommendation/contracts/rec-ev-024ab-anchor-policy-design.json"


class ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    def test_contract_valid(self) -> None:
        validate_contract(self.contract)

    def test_cell_mutation_fails(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["common_design"]["cells"][0]["k"] = 4
        with self.assertRaises(ValueError):
            validate_contract(changed)

    def test_floor_mutation_fails(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["experiments"]["REC-EV-024A"]["minimum_users"] = 1
        with self.assertRaises(ValueError):
            validate_contract(changed)

    def test_claim_widening_fails_schema(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["authorization"]["final_reserve_access"] = True
        with self.assertRaises(ValueError):
            validate_contract(changed)

    def test_every_security_boundary_mutation_fails(self) -> None:
        mutations = []
        changed = copy.deepcopy(self.contract)
        changed["allowed_input_artifacts"]["locked"] = {"path": "outputs/recommendation-evidence/global-time-v1/test.parquet", "bytes": 1, "sha256": "0" * 64}
        mutations.append(changed)
        changed = copy.deepcopy(self.contract)
        changed["implementation_artifacts"].remove("scripts/validate_rec_ev_023ef_contract.py")
        mutations.append(changed)
        changed = copy.deepcopy(self.contract)
        changed["outputs"]["protocol_lock"] = "../../rec-ev-023e/protocol-lock.json"
        mutations.append(changed)
        changed = copy.deepcopy(self.contract)
        changed["claim_boundary"]["allowed"] = "PRODUCT_POLICY"
        mutations.append(changed)
        changed = copy.deepcopy(self.contract)
        changed["experiments"]["REC-EV-024A"]["source_domain"] = "KOREAN_ORIGIN_STRUCTURED"
        mutations.append(changed)
        changed = copy.deepcopy(self.contract)
        changed["serialization"]["exposure_records"] = "RAW_USER_PIPE_RATING_PIPE_TIMESTAMP"
        mutations.append(changed)
        for value in mutations:
            with self.assertRaises(ValueError):
                validate_contract(value)


class PartitionTests(unittest.TestCase):
    def setUp(self) -> None:
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        self.spec = contract["experiments"]["REC-EV-024A"]
        self.user = "1" * 64

    def test_global_roles_are_cross_panel_disjoint(self) -> None:
        roles = partition_user(self.user, list(range(1, 60)), list(range(100, 140)), self.spec)
        inputs = set(roles["profile"]) | set(roles["anchors"])
        labels = {movie for panel in roles["panels"] for movie in panel["evaluation"] + panel["control"]}
        self.assertFalse(inputs & labels)
        self.assertEqual(len(roles["profile"]), 14)
        self.assertEqual(len(roles["anchors"]), 2)
        self.assertTrue(all(len(panel["evaluation"]) == 10 for panel in roles["panels"]))
        self.assertTrue(all(len(panel["control"]) == 10 for panel in roles["panels"]))

    def test_partition_is_deterministic(self) -> None:
        left = partition_user(self.user, list(range(1, 60)), list(range(100, 140)), self.spec)
        right = partition_user(self.user, list(reversed(range(1, 60))), list(reversed(range(100, 140))), self.spec)
        self.assertEqual(left, right)

    def test_short_pool_fails(self) -> None:
        with self.assertRaises(RuntimeError):
            partition_user(self.user, list(range(1, 24)), list(range(100, 112)), self.spec)

    def test_eligibility_exact_boundaries(self) -> None:
        experiments = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))["experiments"]
        masks = eligibility_masks(
            korean_count=np.asarray([11, 12, 12, 0, 0, 0]),
            non_korean_count=np.asarray([24, 23, 24, 0, 0, 0]),
            recent_count=np.asarray([0, 0, 0, 21, 22, 22]),
            old_count=np.asarray([0, 0, 0, 34, 33, 34]),
            experiments=experiments,
        )
        self.assertEqual(masks["REC-EV-024A"].tolist(), [False, False, True, False, False, False])
        self.assertEqual(masks["REC-EV-024B"].tolist(), [False, False, False, False, False, True])


class ReaderFirewallTests(unittest.TestCase):
    def test_excluded_suffix_and_allowed_outcome_are_not_parsed(self) -> None:
        maximum = 300000
        allowed = next(value for value in range(1, maximum + 1) if allowed_role(value, maximum))
        excluded = next(value for value in range(1, maximum + 1) if not allowed_role(value, maximum))
        rows = {
            allowed: f"{allowed},123,NOT_A_RATING,NOT_A_TIMESTAMP\n",
            excluded: f"{excluded},THIS_SUFFIX_HAS_NO_SECOND_COMMA\n",
        }
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "ratings.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                body = "userId,movieId,rating,timestamp\n" + "".join(rows[key] for key in sorted(rows))
                bundle.writestr("ml-32m/ratings.csv", body)
            observed = list(movie_id_only_rows(archive, "ml-32m/ratings.csv", maximum))
        self.assertEqual(observed, [(allowed, 123)])


class ResumeIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    def _temporary_output(self, root: Path):
        return mock.patch.object(
            preflight_module,
            "output_path",
            side_effect=lambda contract, name: root / str(contract["outputs"][name]),
        )

    def test_missing_resume_fails_before_compute(self) -> None:
        with mock.patch.object(preflight_module, "compute_preflight_result") as compute:
            with self.assertRaises(preflight_module.ResumeError):
                preflight_module.preflight(self.contract, resume=False)
            compute.assert_not_called()

    def test_exact_resume_and_forged_result_fail_closed(self) -> None:
        temp_parent = ROOT / ".codex-tmp"
        temp_parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_parent) as directory:
            output_root = Path(directory)
            result = {"schema_version": 1, "status": "PREFLIGHT_COMPLETE", "experiments": {"A": {"status": "FEASIBLE_PRELABEL"}}}
            with self._temporary_output(output_root), \
                    mock.patch.object(preflight_module, "run_signature", return_value="sig"), \
                    mock.patch.object(preflight_module, "compute_preflight_result", return_value=result):
                first = preflight_module.preflight(self.contract, resume=True)
                second = preflight_module.preflight(self.contract, resume=True)
                self.assertEqual(first["status"], "WROTE_PREFLIGHT")
                self.assertEqual(second["status"], "REUSED_EXACT_PREFLIGHT")
                result_path = output_root / self.contract["outputs"]["preflight"]
                forged = json.loads(result_path.read_text(encoding="utf-8"))
                forged["status"] = "FORGED"
                result_path.write_text(json.dumps(forged), encoding="utf-8")
                with self.assertRaises(preflight_module.ResumeError):
                    preflight_module.preflight(self.contract, resume=True)

    def test_partial_result_fails_closed(self) -> None:
        temp_parent = ROOT / ".codex-tmp"
        temp_parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_parent) as directory:
            output_root = Path(directory)
            (output_root / self.contract["outputs"]["preflight"]).write_text("{}", encoding="utf-8")
            with self._temporary_output(output_root):
                with self.assertRaises(preflight_module.ResumeError):
                    preflight_module.preflight(self.contract, resume=True)

    def test_lock_exact_reuse_and_forgery_fails(self) -> None:
        temp_parent = ROOT / ".codex-tmp"
        temp_parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_parent) as directory:
            output_root = Path(directory)
            with self._temporary_output(output_root):
                preflight_module.create_or_verify_lock(self.contract, resume=False)
                preflight_module.create_or_verify_lock(self.contract, resume=True)
                lock_path = output_root / self.contract["outputs"]["protocol_lock"]
                forged = json.loads(lock_path.read_text(encoding="utf-8"))
                forged["contract_sha256"] = "0" * 64
                lock_path.write_text(json.dumps(forged), encoding="utf-8")
                with self.assertRaises(preflight_module.ResumeError):
                    preflight_module.create_or_verify_lock(self.contract, resume=True)


class BootstrapGoldenTests(unittest.TestCase):
    @staticmethod
    def cutoffs() -> list[int]:
        with localcontext() as context:
            context.prec = 80
            probability_zero = (-Decimal(1)).exp()
            term = Decimal(1)
            cumulative = term
            values: list[int] = []
            denominator = Decimal(2**65)
            for k in range(64):
                cutoff = int(((probability_zero * cumulative * denominator) - Decimal(1)) // Decimal(2))
                values.append(min(cutoff, 2**64 - 1))
                if values[-1] >= 2**64 - 1:
                    return values
                term /= Decimal(k + 1)
                cumulative += term
        raise AssertionError("cutoff did not converge")

    def test_golden_fixtures(self) -> None:
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        cutoffs = self.cutoffs()
        prefix = "feelm-bootstrap-v1|rec-ev-024ab-anchor-user-bootstrap-v1"
        for row in contract["bootstrap"]["golden_fixtures"]:
            payload = f"{prefix}|{row['evidence_id']}|{row['attempt']}|user|{row['user_key']}".encode("utf-8")
            value = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big", signed=False)
            self.assertEqual(value, row["uint64"])
            self.assertEqual(bisect.bisect_left(cutoffs, value), row["weight"])


if __name__ == "__main__":
    unittest.main()
