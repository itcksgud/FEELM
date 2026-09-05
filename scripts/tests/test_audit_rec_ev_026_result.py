from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "scripts/audit_rec_ev_026_result.py"
SPEC = importlib.util.spec_from_file_location("audit_rec_ev_026_result", PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class ResultAuditTests(unittest.TestCase):
    def test_default_amendment(self) -> None:
        module.validate_amendment(module.load(module.DEFAULT))

    def test_final_phase_advance_is_no_write(self) -> None:
        with mock.patch.object(module.base, "current_phase", return_value="METRICS_BOOTSTRAP_RESULT_SEAL"), mock.patch.object(module.base, "atomic_write_json") as writer:
            module.no_write_advance({}, "MAPPER_FIT_GATE")
            writer.assert_not_called()

    def test_nonfinal_phase_is_rejected(self) -> None:
        with mock.patch.object(module.base, "current_phase", return_value="ALL_HEAD_RANK_SEAL"):
            with self.assertRaises(module.base.ResumeError):
                module.no_write_advance({}, "MAPPER_FIT_GATE")


if __name__ == "__main__":
    unittest.main()
