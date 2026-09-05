from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np

from scripts.run_rec_ev_022a_stage1 import (
    ROOT, ResumeError, _rating_chunks, build_parser, verify_integrity, write_integrity,
)
from scripts.validate_rec_ev_022a_contract import CONTRACT, validate_contract


class RecEv022aContractTests(unittest.TestCase):
    def test_committed_contract_is_valid(self) -> None:
        result = validate_contract(json.loads(CONTRACT.read_text(encoding="utf-8")))
        self.assertEqual("PASS_REC_EV_022A_CONTRACT", result["status"])
        self.assertFalse(result["locked_test_access"])

    def test_runner_has_no_test_stage_and_requires_explicit_phase(self) -> None:
        parser = build_parser()
        phase = next(action for action in parser._actions if action.dest == "phase")
        self.assertEqual(("lock", "prepare", "score", "analyze", "run"), tuple(phase.choices))
        self.assertNotIn("test", tuple(phase.choices))

    def test_prefilter_does_not_parse_excluded_row_payload(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            archive = Path(directory) / "sentinel.zip"
            member = "ml/ratings.csv"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr(
                    member,
                    "userId,movieId,rating,timestamp\n"
                    "1,NOT_A_MOVIE,NOT_A_RATING,NOT_A_TIME\n"
                    "2,10,4.5,123\n",
                )
            allowed = np.zeros(3, dtype=bool)
            allowed[2] = True
            frames = list(_rating_chunks(
                archive, member, allowed_user_mask=allowed, include_timestamp=False,
            ))
            self.assertEqual([2], frames[0]["userId"].tolist())
            self.assertEqual([10], frames[0]["movieId"].tolist())

    def test_integrity_manifest_fails_closed_after_artifact_tamper(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            artifact = root / "artifact.bin"
            integrity = root / "artifact.integrity.json"
            artifact.write_bytes(b"before")
            write_integrity(integrity, {"artifact": artifact}, signature="test-run", metadata={"rows": 1})
            verify_integrity(integrity, {"artifact": artifact}, signature="test-run")
            artifact.write_bytes(b"after")
            with self.assertRaises(ResumeError):
                verify_integrity(integrity, {"artifact": artifact}, signature="test-run")


if __name__ == "__main__":
    unittest.main()
