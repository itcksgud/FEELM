from __future__ import annotations

import bisect
import copy
import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from decimal import Decimal, localcontext
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import run_rec_ev_025ab_preflight as module  # noqa: E402
from run_rec_ev_023ef_preflight import allowed_role, movie_id_only_rows  # noqa: E402
from validate_rec_ev_025ab_contract import validate_contract  # noqa: E402


CONTRACT_PATH = ROOT / "docs/recommendation/contracts/rec-ev-025ab-feature-transfer-design.json"


def load() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


class ContractTests(unittest.TestCase):
    def test_exact_contract(self) -> None:
        validate_contract(load())

    def test_security_mutations_fail(self) -> None:
        base = load()
        changes = []
        for section, key, value in (
            ("authorization", "final_reserve_access", True),
            ("common_support", "e5_dimension", 768),
            ("execution_statistics", "joint_family_each_experiment", 1),
            ("decision", "absolute_target_margin", -1),
            ("claim_boundary", "allowed", "PRODUCT_POLICY"),
            ("outputs", "protocol_lock", "../../rec-ev-023e/protocol-lock.json"),
        ):
            changed = copy.deepcopy(base)
            changed[section][key] = value
            changes.append(changed)
        for changed in changes:
            with self.assertRaises(ValueError):
                validate_contract(changed)


class PartitionTests(unittest.TestCase):
    def test_global_profile_is_disjoint_from_every_control_panel(self) -> None:
        spec = load()["experiments"]["REC-EV-025A"]
        selected = module.partition_user("a" * 64, list(range(1, 60)), list(range(100, 140)), spec)
        profile = set(selected["profile"])
        controls = {movie for panel in selected["panels"] for movie in panel["control"]}
        self.assertFalse(profile & controls)
        self.assertEqual(len(profile), 14)

    def test_partition_order_is_input_order_independent(self) -> None:
        spec = load()["experiments"]["REC-EV-025A"]
        left = module.partition_user("b" * 64, list(range(1, 60)), list(range(100, 140)), spec)
        right = module.partition_user("b" * 64, list(reversed(range(1, 60))), list(reversed(range(100, 140))), spec)
        self.assertEqual(left, right)

    def test_short_pool_fails(self) -> None:
        spec = load()["experiments"]["REC-EV-025A"]
        with self.assertRaises(RuntimeError):
            module.partition_user("c" * 64, list(range(1, 23)), list(range(100, 109)), spec)


class ReaderTests(unittest.TestCase):
    def test_outcome_suffix_is_not_parsed(self) -> None:
        maximum = 300000
        allowed = next(value for value in range(1, maximum + 1) if allowed_role(value, maximum))
        excluded = next(value for value in range(1, maximum + 1) if not allowed_role(value, maximum))
        rows = {
            allowed: f"{allowed},123,BROKEN_RATING,BROKEN_TIMESTAMP\n",
            excluded: f"{excluded},BROKEN_WITHOUT_SECOND_COMMA\n",
        }
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "ratings.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("ml-32m/ratings.csv", "userId,movieId,rating,timestamp\n" + "".join(rows[key] for key in sorted(rows)))
            self.assertEqual(list(movie_id_only_rows(archive, "ml-32m/ratings.csv", maximum)), [(allowed, 123)])


class CommonSupportTests(unittest.TestCase):
    def _structured(self) -> pd.DataFrame:
        return pd.DataFrame({
            "movie_id": np.asarray([1, 2], dtype=np.int32),
            "feature_eligible": [True, True],
            "release_year": [2020.0, np.nan],
            "runtime_minutes": [100.0, np.nan],
            "genre_ids": [np.asarray([18]), np.asarray([], dtype=np.int64)],
            "original_language": ["ko", None],
            "director_ids": [np.asarray([], dtype=np.int64), np.asarray([], dtype=np.int64)],
            "top5_cast_ids": [np.asarray([], dtype=np.int64), np.asarray([], dtype=np.int64)],
            "keyword_ids": [np.asarray([], dtype=np.int64), np.asarray([], dtype=np.int64)],
        })

    def _e5_table(self, *, model_id: str | None = "intfloat/multilingual-e5-small", embedding_type: pa.DataType | None = None) -> pa.Table:
        dimension = 384
        value_dimension = int(embedding_type.list_size) if embedding_type is not None and pa.types.is_fixed_size_list(embedding_type) else dimension
        values = np.zeros((2, value_dimension), dtype=np.float32)
        values[:, 0] = 1.0
        arrays = {
            "movie_id": pa.array([1, 2], type=pa.int32()),
            "model_revision": pa.array(["614241f622f53c4eeff9890bdc4f31cfecc418b3"] * 2, type=pa.string()),
            "embedding": pa.array(values.tolist(), type=embedding_type or pa.list_(pa.float32(), dimension)),
            "feature_eligible": pa.array([True, True], type=pa.bool_()),
        }
        if model_id is not None:
            arrays["model_id"] = pa.array([model_id] * 2, type=pa.string())
        return pa.table(arrays)

    def _run(self, table: pa.Table) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
        contract = load()
        with tempfile.TemporaryDirectory(dir=ROOT / ".codex-tmp") as directory:
            root = Path(directory)
            structured_path = root / "structured.parquet"
            text_path = root / "text.parquet"
            projection_path = root / "projection.json"
            self._structured().to_parquet(structured_path, index=False)
            pq.write_table(table, text_path)
            projection_path.write_text(json.dumps({"artifact_id": "KOREAN_ORIGIN_MOVIELENS_MOVIE_ID_PROJECTION_V1", "movie_ids": list(range(1, 1079))}), encoding="utf-8")
            paths = {
                str(contract["allowed_input_artifacts"]["structured_features"]["path"]): structured_path,
                str(contract["allowed_input_artifacts"]["text_embeddings"]["path"]): text_path,
                str(contract["allowed_input_artifacts"]["korean_movie_id_projection"]["path"]): projection_path,
            }
            with mock.patch.object(module, "resolve_input", side_effect=lambda artifact: paths[str(artifact["path"])]):
                return module.build_common_support(contract)

    def test_exact_e5_schema_identity_and_structured_filter(self) -> None:
        movie_ids, _, _, summary = self._run(self._e5_table())
        self.assertEqual(movie_ids.tolist(), [1])
        self.assertEqual(summary["structured_eligible_rows"], 1)

    def test_e5_model_and_schema_drift_fail_closed(self) -> None:
        cases = [
            self._e5_table(model_id=None),
            self._e5_table(embedding_type=pa.list_(pa.float32())),
            self._e5_table(embedding_type=pa.list_(pa.float64(), 384)),
            self._e5_table(embedding_type=pa.list_(pa.float32(), 383)),
        ]
        for table in cases:
            with self.subTest(schema=table.schema):
                with self.assertRaises((RuntimeError, pa.ArrowInvalid, pa.ArrowTypeError)):
                    self._run(table)
        movie_ids, _, _, summary = self._run(self._e5_table(model_id="wrong/model"))
        self.assertEqual(movie_ids.tolist(), [])
        self.assertEqual(summary["e5_eligible_exact_rows"], 0)

    def test_e5_revision_nonfinite_norm_and_null_fail_closed(self) -> None:
        base = self._e5_table()
        bad_revision = base.set_column(base.schema.get_field_index("model_revision"), "model_revision", pa.array(["wrong", "wrong"], type=pa.string()))
        bad_values = np.zeros((2, 384), dtype=np.float32)
        bad_values[:, 0] = [np.nan, 2.0]
        bad_vectors = base.set_column(base.schema.get_field_index("embedding"), "embedding", pa.array(bad_values.tolist(), type=pa.list_(pa.float32(), 384)))
        for table in (bad_revision, bad_vectors):
            movie_ids, _, _, summary = self._run(table)
            self.assertEqual(movie_ids.tolist(), [])
            self.assertEqual(summary["e5_eligible_exact_rows"], 0)
        with_null = base.set_column(base.schema.get_field_index("model_id"), "model_id", pa.array([None, "intfloat/multilingual-e5-small"], type=pa.string()))
        with self.assertRaises(RuntimeError):
            self._run(with_null)


class BootstrapGoldenTests(unittest.TestCase):
    def test_golden_fixtures(self) -> None:
        with localcontext() as context:
            context.prec = 80
            p0 = (-Decimal(1)).exp()
            term = Decimal(1)
            cumulative = term
            cutoffs = []
            for k in range(64):
                cutoffs.append(min(int(((p0 * cumulative * Decimal(2**65)) - Decimal(1)) // Decimal(2)), 2**64 - 1))
                if cutoffs[-1] >= 2**64 - 1:
                    break
                term /= Decimal(k + 1)
                cumulative += term
        for row in load()["bootstrap"]["golden_fixtures"]:
            payload = f"feelm-bootstrap-v1|rec-ev-025ab-feature-transfer-bootstrap-v1|{row['evidence_id']}|{row['attempt']}|user|{row['user_key']}".encode()
            value = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
            self.assertEqual((value, bisect.bisect_left(cutoffs, value)), (row["uint64"], row["weight"]))


class ResumeTests(unittest.TestCase):
    def test_missing_resume_fails_before_compute(self) -> None:
        contract = load()
        with mock.patch.object(module, "compute_result") as compute:
            with self.assertRaises(module.ResumeError):
                module.preflight(contract, resume=False)
            compute.assert_not_called()

    def test_partial_result_fails(self) -> None:
        contract = load()
        with tempfile.TemporaryDirectory(dir=ROOT / ".codex-tmp") as directory:
            root = Path(directory)
            with mock.patch.object(module, "output_path", side_effect=lambda c, name: root / c["outputs"][name]):
                module.create_or_verify_lock(contract, resume=False)
                (root / contract["outputs"]["preflight"]).write_text("{}", encoding="utf-8")
                with self.assertRaises(module.ResumeError):
                    module.preflight(contract, resume=True)

    def test_absent_lock_resume_fails_before_source_hash_or_compute(self) -> None:
        contract = load()
        with tempfile.TemporaryDirectory(dir=ROOT / ".codex-tmp") as directory:
            root = Path(directory)
            with (
                mock.patch.object(module, "output_path", side_effect=lambda c, name: root / c["outputs"][name]),
                mock.patch.object(module, "expected_lock_state") as expected,
                mock.patch.object(module, "compute_result") as compute,
            ):
                with self.assertRaises(module.ResumeError):
                    module.preflight(contract, resume=True)
                expected.assert_not_called()
                compute.assert_not_called()

    def test_forged_complete_trio_fails_and_exact_reuse_does_not_write(self) -> None:
        contract = load()
        result = {"experiments": {"REC-EV-025A": {}, "REC-EV-025B": {}}}
        with tempfile.TemporaryDirectory(dir=ROOT / ".codex-tmp") as directory:
            root = Path(directory)
            output = lambda c, name: root / c["outputs"][name]
            with mock.patch.object(module, "output_path", side_effect=output):
                module.create_or_verify_lock(contract, resume=False)
                signature = module.run_signature(contract)
                progress = {"schema_version": 1, "phase": "PREFLIGHT_COMPLETE", "run_signature": signature, "experiments": result["experiments"]}
                module.atomic_write_json(output(contract, "preflight"), result)
                module.atomic_write_json(output(contract, "progress"), progress)
                module.atomic_write_json(output(contract, "preflight_integrity"), {"forged": True})
                with mock.patch.object(module, "compute_result", return_value=result):
                    with self.assertRaises(module.ResumeError):
                        module.preflight(contract, resume=True)
                module.atomic_write_json(output(contract, "preflight_integrity"), module.expected_integrity(contract, result, progress, signature))
                paths = [output(contract, name) for name in ("preflight", "progress", "preflight_integrity")]
                before = [(path.stat().st_mtime_ns, module.sha256_file(path)) for path in paths]
                with (
                    mock.patch.object(module, "compute_result", return_value=result),
                    mock.patch.object(module, "atomic_write_json") as writer,
                ):
                    reused = module.preflight(contract, resume=True)
                self.assertEqual(reused["status"], "REUSED_EXACT_PREFLIGHT")
                writer.assert_not_called()
                self.assertEqual(before, [(path.stat().st_mtime_ns, module.sha256_file(path)) for path in paths])


if __name__ == "__main__":
    unittest.main()
