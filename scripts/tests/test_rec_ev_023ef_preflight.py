from __future__ import annotations

import importlib.util
import io
import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


validator = load("validate_rec_ev_023ef_contract_test", "scripts/validate_rec_ev_023ef_contract.py")
runner = load("run_rec_ev_023ef_preflight_test", "scripts/run_rec_ev_023ef_preflight.py")


class JointContractTests(unittest.TestCase):
    def test_contract_validates(self):
        contract = json.loads(validator.CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual(validator.validate_contract(contract)["status"], "PASS_REC_EV_023EF_JOINT_CONTRACT")

    def test_contract_mutation_fails(self):
        contract = json.loads(validator.CONTRACT.read_text(encoding="utf-8"))
        contract["experiments"]["REC-EV-023E"]["minimum_users"] = 1
        with self.assertRaises(RuntimeError):
            validator.validate_contract(contract)


class ReaderFirewallTests(unittest.TestCase):
    def make_archive(self, rows: list[bytes]) -> tuple[tempfile.TemporaryDirectory, Path]:
        temporary = tempfile.TemporaryDirectory()
        path = Path(temporary.name) / "ratings.zip"
        with zipfile.ZipFile(path, "w") as bundle:
            bundle.writestr("ml-32m/ratings.csv", b"userId,movieId,rating,timestamp\n" + b"".join(rows))
        return temporary, path

    def test_excluded_row_tail_is_never_parsed(self):
        temporary, path = self.make_archive([b"1,NOT_A_MOVIE,NOT_A_RATING,NOT_A_TIMESTAMP\n"])
        self.addCleanup(temporary.cleanup)
        with mock.patch.object(runner, "allowed_role", return_value=False):
            self.assertEqual(list(runner.movie_id_only_rows(path, "ml-32m/ratings.csv", 300000)), [])

    def test_allowed_reader_stops_at_second_comma(self):
        temporary, path = self.make_archive([b"1,123,NOT_A_RATING,NOT_A_TIMESTAMP\n"])
        self.addCleanup(temporary.cleanup)
        with mock.patch.object(runner, "allowed_role", return_value=True):
            self.assertEqual(list(runner.movie_id_only_rows(path, "ml-32m/ratings.csv", 300000)), [(1, 123)])

    def test_duplicate_allowed_user_movie_fails(self):
        temporary, path = self.make_archive([
            b"1,123,4.0,1\n",
            b"1,123,5.0,2\n",
        ])
        self.addCleanup(temporary.cleanup)
        with mock.patch.object(runner, "allowed_role", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "duplicate allowed user-movie"):
                list(runner.movie_id_only_rows(path, "ml-32m/ratings.csv", 300000))

    def test_role_boundaries(self):
        with mock.patch.object(runner, "old_user_bucket", return_value=60):
            self.assertFalse(runner.allowed_role(1, 300000))

    def test_real_role_ids_with_malicious_tails(self):
        categories = {}
        for raw_user in range(1, 300001):
            old = runner.old_user_bucket(raw_user)
            role = runner.user_role_bucket(raw_user)
            if old > 59 and "locked" not in categories:
                categories["locked"] = raw_user
            elif old <= 59 and role < 6000 and "train" not in categories:
                categories["train"] = raw_user
            elif old <= 59 and 6000 <= role <= 9199 and "allowed" not in categories:
                categories["allowed"] = raw_user
            elif old <= 59 and role >= 9200 and "final" not in categories:
                categories["final"] = raw_user
            if len(categories) == 4:
                break
        payloads = [(categories[name], f"{categories[name]},NO_SECOND_COMMA\n".encode()) for name in ("locked", "train", "final")]
        payloads.append((categories["allowed"], f"{categories['allowed']},123,NOT_A_RATING,NOT_A_TIMESTAMP\n".encode()))
        rows = [payload for _, payload in sorted(payloads)]
        temporary, path = self.make_archive(rows)
        self.addCleanup(temporary.cleanup)
        self.assertEqual(list(runner.movie_id_only_rows(path, "ml-32m/ratings.csv", 300000)), [(categories["allowed"], 123)])
        with mock.patch.object(runner, "old_user_bucket", return_value=0), mock.patch.object(runner, "user_role_bucket", return_value=5999):
            self.assertFalse(runner.allowed_role(1, 300000))
        with mock.patch.object(runner, "old_user_bucket", return_value=0), mock.patch.object(runner, "user_role_bucket", return_value=6000):
            self.assertTrue(runner.allowed_role(1, 300000))
        with mock.patch.object(runner, "old_user_bucket", return_value=0), mock.patch.object(runner, "user_role_bucket", return_value=9199):
            self.assertTrue(runner.allowed_role(1, 300000))
        with mock.patch.object(runner, "old_user_bucket", return_value=0), mock.patch.object(runner, "user_role_bucket", return_value=9200):
            self.assertFalse(runner.allowed_role(1, 300000))

    def test_degree_summary(self):
        import numpy as np
        self.assertEqual(runner.degree_summary(np.asarray([0, 2, 3], dtype=np.int32))["unique_target_items"], 2)

    def test_pinned_universe_smoke(self):
        contract = json.loads(validator.CONTRACT.read_text(encoding="utf-8"))
        movie_ids, years, korean, summary = runner.build_universe(contract)
        self.assertEqual(summary, {
            "structured_rows": 68674,
            "universe_items": 68201,
            "korean_origin_items": 924,
            "recent_2020_2023_items": 5866,
            "pre_2020_items": 62304,
        })
        self.assertEqual((len(movie_ids), len(years), int(korean.sum())), (68201, 68201, 924))

    def test_sanitized_projection_has_no_outcome_fields(self):
        contract = json.loads(validator.CONTRACT.read_text(encoding="utf-8"))
        projection = runner.read_json(runner.resolve_input(contract["allowed_input_artifacts"]["korean_movie_id_projection"]))
        self.assertEqual(set(projection), {"artifact_id", "claim", "count", "movie_ids", "projection_rule", "schema_version", "source_artifacts"})
        self.assertTrue({"ratings", "rating_count", "popularity", "timestamp"}.isdisjoint(projection))

    def test_projection_reconstructs_exact_source_union(self):
        contract = json.loads(validator.CONTRACT.read_text(encoding="utf-8"))
        projection = runner.read_json(runner.resolve_input(contract["allowed_input_artifacts"]["korean_movie_id_projection"]))
        first = runner.read_json(ROOT / projection["source_artifacts"][0]["path"])
        second = runner.read_json(ROOT / projection["source_artifacts"][1]["path"])
        expected = {int(row["movie_id"]) for row in first["movielens"]["matched_items"]}
        expected.update(int(row["movie_id"]) for row in second["head_items"] if row.get("status") == "OK" and row.get("is_korean_origin"))
        self.assertEqual(projection["movie_ids"], sorted(expected))

    def test_panel_order_is_deterministic_and_salt_specific(self):
        movies = [30, 10, 20, 40]
        first = runner.panel_order("a" * 64, movies, "salt-a")
        self.assertEqual(first, runner.panel_order("a" * 64, list(reversed(movies)), "salt-a"))
        self.assertNotEqual(first, runner.panel_order("a" * 64, movies, "salt-b"))


class ResumeStateTests(unittest.TestCase):
    def contract_in(self, directory: Path):
        value = json.loads(validator.CONTRACT.read_text(encoding="utf-8"))
        value["output_root"] = str(directory)
        return value

    def test_partial_preflight_state_fails_before_reuse(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract = self.contract_in(root)
            (root / "preflight.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(runner.ResumeError):
                runner.preflight(contract, resume=True)

    def test_complete_state_requires_resume(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract = self.contract_in(root)
            for name in ("preflight.json", "preflight.integrity.json", "run-progress.json"):
                (root / name).write_text("{}", encoding="utf-8")
            with mock.patch.object(runner, "run_signature", return_value="x"):
                with self.assertRaises(runner.ResumeError):
                    runner.preflight(contract, resume=False)

    def test_forged_complete_state_fails_recomputation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract = self.contract_in(root)
            signature = "s" * 64
            forged = {"status": "FORGED"}
            expected = {"status": "EXPECTED"}
            preflight_path = root / "preflight.json"
            progress_path = root / "run-progress.json"
            preflight_path.write_text(json.dumps(forged), encoding="utf-8")
            progress_path.write_text(json.dumps({"phase": "PREFLIGHT_COMPLETE"}), encoding="utf-8")
            integrity = {
                "run_signature": signature,
                "artifacts": {
                    "preflight": {"bytes": preflight_path.stat().st_size, "sha256": runner.sha256_file(preflight_path)},
                    "progress": {"bytes": progress_path.stat().st_size, "sha256": runner.sha256_file(progress_path)},
                },
            }
            (root / "preflight.integrity.json").write_text(json.dumps(integrity), encoding="utf-8")
            with mock.patch.object(runner, "run_signature", return_value=signature), mock.patch.object(runner, "compute_preflight_result", return_value=expected):
                with self.assertRaises(runner.ResumeError):
                    runner.preflight(contract, resume=True)

    def test_integrity_semantic_mutation_fails_exact_comparison(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract = self.contract_in(root)
            signature = "s" * 64
            result = {"status": "PREFLIGHT_COMPLETE", "experiments": {}}
            progress = {"schema_version": 1, "phase": "PREFLIGHT_COMPLETE", "experiments": {}, "run_signature": signature}
            runner.atomic_write_json(root / "preflight.json", result)
            runner.atomic_write_json(root / "run-progress.json", progress)
            with mock.patch.object(runner, "ROOT", root):
                integrity = runner.expected_preflight_integrity(contract, result, progress, signature=signature)
                integrity["metadata"]["status"] = "FORGED"
                runner.atomic_write_json(root / "preflight.integrity.json", integrity)
                with mock.patch.object(runner, "run_signature", return_value=signature), mock.patch.object(runner, "compute_preflight_result", return_value=result):
                    with self.assertRaisesRegex(runner.ResumeError, "canonical semantic drift"):
                        runner.preflight(contract, resume=True)

    def test_lock_none_partial_all_and_manifest_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract = self.contract_in(root)
            contract_path = validator.CONTRACT
            with self.assertRaises(runner.ResumeError):
                runner.create_or_verify_lock(contract, contract_path, resume=True)
            runner.create_or_verify_lock(contract, contract_path, resume=False)
            self.assertEqual(runner.create_or_verify_lock(contract, contract_path, resume=True)["status"], "APPROVED_FOR_PRELABEL_FIREWALLED_FEASIBILITY")
            manifest_path = root / "source-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["evaluation_labels_opened"] = True
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(runner.ResumeError):
                runner.create_or_verify_lock(contract, contract_path, resume=True)

    def test_poisson_golden_fixtures(self):
        import bisect
        from decimal import Decimal, localcontext
        from scripts.run_rec_ev_023c_crossed_sensitivity import poisson_cutoffs, poisson_weight
        contract = json.loads(validator.CONTRACT.read_text(encoding="utf-8"))
        cutoffs = poisson_cutoffs(precision=80)
        for row in contract["statistics"]["poisson_golden_fixtures"]:
            payload = f"feelm-bootstrap-v1|rec-ev-023ef-user-bootstrap-v1|{row['evidence_id']}|{row['attempt']}|user|{row['user_key']}".encode("utf-8")
            literal_value = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big", signed=False)
            literal_weight = bisect.bisect_left(cutoffs, literal_value)
            self.assertEqual((literal_value, literal_weight), (row["uint64"], row["weight"]))
            weight, value = poisson_weight(
                "rec-ev-023ef-user-bootstrap-v1|" + row["evidence_id"], row["attempt"], "user", row["user_key"], cutoffs,
            )
            self.assertEqual((value, weight), (row["uint64"], row["weight"]))


if __name__ == "__main__":
    unittest.main()
