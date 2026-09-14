from __future__ import annotations

import argparse
import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_service_v1_b1_gbt as host
import service_v1_b1_spark_worker as worker


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def worker_result(name: str, status: str, logical_rows: int | None = None) -> host.ContainerResult:
    payload = {
        "schemaVersion": worker.SCHEMA_VERSION,
        "status": status,
        "resourceObservation": {
            "peakBytes": 1_024,
            "peakSource": "/sys/fs/cgroup/memory.peak",
            "resourceStatus": "PASS",
        },
        "runtimeVersions": {
            "sparkVersion": "4.1.3",
            "javaVersion": "21.0.11",
            "pythonVersion": "3.12.10",
        },
    }
    if logical_rows is not None:
        payload["logicalRows"] = logical_rows
    return host.ContainerResult(
        container_name=name,
        command=["docker", "create"],
        output=json.dumps(payload),
        worker=payload,
        docker_state={"Running": False, "ExitCode": 0, "OOMKilled": False},
        resource={
            "resourceStatus": "PASS",
            "peakBytes": 1_024,
            "peakSource": "/sys/fs/cgroup/memory.peak",
            "limitBytes": host.MAX_MEMORY_BYTES,
            "oomKilled": False,
            "exitCode": 0,
            "timedOut": False,
        },
        elapsed_seconds=0.25,
        cleanup={"confirmedStopped": True, "removed": True, "attempts": []},
    )


class CanonicalDigestTests(unittest.TestCase):
    def test_reviewed_plan_pin_matches_current_bytes(self) -> None:
        paths = host.ExecutionPaths.from_roots(ROOT, ROOT)
        self.assertEqual(host.verify_plan(paths)["sha256"], host.PLAN_SHA256)

    def test_predecessor_failure_is_the_reviewed_wiring_failure(self) -> None:
        paths = host.ExecutionPaths.from_roots(ROOT, ROOT.parent / "S15P21E106")
        record = host.predecessor_failure_record(paths)
        self.assertEqual(record["bytes"], host.PREDECESSOR_FAILURE_BYTES)
        self.assertEqual(record["sha256"], host.PREDECESSOR_FAILURE_SHA256)

    def test_record_order_does_not_change_digest(self) -> None:
        records = [
            {"path": "z", "bytes": 2, "sha256": "b" * 64},
            {"path": "a", "bytes": 1, "sha256": "a" * 64},
        ]
        self.assertEqual(
            host.canonical_record_set_sha256(records),
            host.canonical_record_set_sha256(list(reversed(records))),
        )

    def test_duplicate_record_path_is_rejected(self) -> None:
        duplicate = [
            {"path": "a", "bytes": 1, "sha256": "a" * 64},
            {"path": "a", "bytes": 2, "sha256": "b" * 64},
        ]
        with self.assertRaisesRegex(ValueError, "duplicate"):
            host.canonical_record_set_sha256(duplicate)

    def test_training_source_and_phase_control_digests_are_separate(self) -> None:
        source = [{"path": "source", "bytes": 1, "sha256": "a" * 64}]
        control = [{"path": "review", "bytes": 2, "sha256": "b" * 64}]
        preflight = host.make_phase_lock("preflight", source, [])
        fit = host.make_phase_lock("fit", source, control)
        self.assertEqual(preflight["trainingSourceSetSha256"], fit["trainingSourceSetSha256"])
        self.assertNotEqual(preflight["inputSetSha256"], fit["inputSetSha256"])
        self.assertEqual(fit["controlReferences"], control)

    def test_score_source_and_control_sets_are_separate(self) -> None:
        source = [{"path": "score", "bytes": 3, "sha256": "c" * 64}]
        control = [{"path": "fit-review", "bytes": 4, "sha256": "d" * 64}]
        lock = host.make_score_lock(source, control)
        self.assertEqual(lock["scoreSourceSetSha256"], host.canonical_record_set_sha256(source))
        self.assertEqual(lock["controlReferenceSetSha256"], host.canonical_record_set_sha256(control))
        self.assertNotEqual(lock["scoreSourceSetSha256"], lock["inputSetSha256"])

    def test_input_byte_or_hash_mutation_changes_lock_and_record_equality(self) -> None:
        original = [{"path": "source", "bytes": 3, "sha256": "a" * 64}]
        mutated = [{"path": "source", "bytes": 3, "sha256": "b" * 64}]
        self.assertFalse(host.records_equal(original, mutated))
        self.assertNotEqual(
            host.canonical_record_set_sha256(original),
            host.canonical_record_set_sha256(mutated),
        )


class FirewallAndCliTests(unittest.TestCase):
    def test_evaluation_mounts_and_broad_text339_mount_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            labels = root / "labels.parquet"
            labels.write_bytes(b"x")
            with self.assertRaisesRegex(ValueError, "evaluation path"):
                host.validate_mounts(
                    [host.Mount(labels, "/input/labels.parquet")],
                    allowed_readonly=[labels],
                    allowed_write=[],
                )
            broad = root / "text339"
            broad.mkdir()
            with self.assertRaisesRegex(ValueError, "evaluation path"):
                host.validate_mounts(
                    [host.Mount(broad, "/input/text")],
                    allowed_readonly=[broad],
                    allowed_write=[],
                )

    def test_readonly_mount_must_be_exactly_allowlisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = root / "safe.bin"
            child.write_bytes(b"x")
            with self.assertRaisesRegex(ValueError, "exact allowlist"):
                host.validate_mounts(
                    [host.Mount(root, "/input")],
                    allowed_readonly=[child],
                    allowed_write=[],
                )

    def test_public_cli_has_no_dry_run_or_raw_full_preflight_action(self) -> None:
        parser = host.parser()
        choices: set[str] = set()
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                choices.update(action.choices)
        self.assertEqual(choices, {"preflight", "fit", "score"})
        worker_parser = worker.parser()
        worker_choices: set[str] = set()
        for action in worker_parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                worker_choices.update(action.choices)
        self.assertEqual(worker_choices, {"dry-run-first-row-group", "preflight", "fit", "score"})

    def test_container_command_forces_complete_analyzed_plan_rendering(self) -> None:
        command = host.spark_container_command("feelm-b1-test", [], ["score"])
        self.assertIn("spark.sql.debug.maxToStringFields=1000", command)

    def test_training_mounts_keep_reviewed_masked_bundle_directory_exact(self) -> None:
        paths = host.ExecutionPaths.from_roots(Path("C:/standalone"), Path("C:/team"))
        with mock.patch.object(host, "verify_masked_bundle_directory"), mock.patch.object(host, "validate_mounts"):
            mounts = host.training_mounts(paths, Path("C:/scratch"), None)
        targets = {mount.target for mount in mounts}
        self.assertIn("/masked", targets)
        self.assertFalse(any(target.startswith("/masked/") for target in targets))
        self.assertIn("/input/natural-train.parquet", targets)
        self.assertIn("/review/masked-result-review.json", targets)
        self.assertNotIn("/input/tmdb-masked-train.parquet", targets)
        arguments = host.common_training_worker_arguments(
            "dry-run-first-row-group", include_recipe=False, include_output=False
        )
        self.assertIn("/masked/tmdb-masked-rh230.parquet", arguments)
        self.assertIn("/review/masked-result-review.json", arguments)

    def test_worker_rejects_any_fourth_file_in_masked_bundle_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            masked_root = root / "masked"
            masked_root.mkdir()
            masked = masked_root / "tmdb-masked-rh230.parquet"
            manifest = masked_root / "manifest.json"
            views = masked_root / "views-manifest.json"
            extra = masked_root / "unreviewed.bin"
            natural = root / "natural-train.parquet"
            review = root / "masked-result-review.json"
            for path in (masked, manifest, views, extra, natural, review):
                path.write_bytes(b"fixture")
            with mock.patch.object(worker, "verify_pin"):
                with self.assertRaisesRegex(ValueError, "exactly the reviewed three files"):
                    worker.verify_masked_contract(natural, masked, manifest, views, review)

    def test_worker_requires_one_masked_parent_and_external_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            masked_root = root / "masked"
            other = root / "other"
            masked_root.mkdir()
            other.mkdir()
            natural = root / "natural-train.parquet"
            masked = masked_root / "tmdb-masked-rh230.parquet"
            manifest = masked_root / "manifest.json"
            views_elsewhere = other / "views-manifest.json"
            review = root / "masked-result-review.json"
            for path in (natural, masked, manifest, views_elsewhere, review):
                path.write_bytes(b"fixture")
            with mock.patch.object(worker, "verify_pin"):
                with self.assertRaisesRegex(ValueError, "do not share one directory"):
                    worker.verify_masked_contract(natural, masked, manifest, views_elsewhere, review)
            views = masked_root / "views-manifest.json"
            views.write_bytes(b"fixture")
            review_inside = masked_root / "masked-result-review.json"
            review_inside.write_bytes(b"fixture")
            with mock.patch.object(worker, "verify_pin"):
                with self.assertRaisesRegex(ValueError, "review shares"):
                    worker.verify_masked_contract(natural, masked, manifest, views, review_inside)

    def test_host_rechecks_exact_masked_directory_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = host.ExecutionPaths.from_roots(root / "standalone", root / "team")
            paths.masked_root.mkdir(parents=True)
            for name in ("manifest.json", "tmdb-masked-rh230.parquet", "views-manifest.json"):
                (paths.masked_root / name).write_bytes(b"fixture")
            host.verify_masked_bundle_directory(paths)
            (paths.masked_root / "unreviewed.bin").write_bytes(b"extra")
            with self.assertRaisesRegex(ValueError, "exactly the reviewed three files"):
                host.verify_masked_bundle_directory(paths)

    def test_validate_preflight_recovery_rejects_control_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = host.ExecutionPaths.from_roots(root / "standalone", root / "team")
            bundle = paths.bundle("preflight")
            bundle.mkdir(parents=True)
            predecessor = {"path": "standalone/prior-failure.json", "bytes": 7, "sha256": "a" * 64}
            outer = {"path": "implementation/outer", "bytes": 8, "sha256": "b" * 64}
            spark_worker = {"path": "implementation/worker", "bytes": 9, "sha256": "c" * 64}
            write_json(bundle / "recovery-reference.json", host.recovery_reference(predecessor, outer, spark_worker))
            sources = [{"path": "source", "bytes": 1, "sha256": "d" * 64}]
            lock = host.make_phase_lock("preflight", sources, [predecessor])
            manifest = {
                "controlReferenceSetSha256": lock["controlReferenceSetSha256"],
                "inputSetSha256": lock["inputSetSha256"],
                "predecessorFailureSha256": predecessor["sha256"],
                "recoveryReferenceSha256": host.sha256_file(bundle / "recovery-reference.json"),
            }
            audited = {"inputLock": lock, "manifest": manifest}
            host.validate_preflight_recovery(paths, audited, predecessor, outer, spark_worker)
            audited["inputLock"]["controlReferences"] = []
            with self.assertRaisesRegex(ValueError, "predecessor control drift"):
                host.validate_preflight_recovery(paths, audited, predecessor, outer, spark_worker)

    def test_no_production_assert_or_evaluation_cli_option(self) -> None:
        for path in (host.Path(__file__).resolve().parents[1] / "scripts/run_service_v1_b1_gbt.py", ROOT / "scripts/service_v1_b1_spark_worker.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            self.assertFalse(any(isinstance(node, ast.Assert) for node in ast.walk(tree)))
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("--labels", source)
            self.assertNotIn("--evaluation-seal", source)


class ScoreIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source_path = self.root / "score.parquet"
        self.prediction_path = self.root / "predictions.parquet"
        self.row_ids = np.arange(4, dtype=np.int64)
        self.uids = np.asarray([9, 7, 7, 3], dtype=np.int32)
        source = pa.table(
            {
                "row_id": pa.array(self.row_ids, type=pa.int64()),
                "uid": pa.array(self.uids, type=pa.int32()),
            }
        )
        pq.write_table(source, self.source_path)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_predictions(
        self,
        row_ids: np.ndarray | None = None,
        uids: np.ndarray | None = None,
        values: np.ndarray | None = None,
    ) -> None:
        table = pa.Table.from_arrays(
            [
                pa.array(self.row_ids if row_ids is None else row_ids, type=pa.int64()),
                pa.array(self.uids if uids is None else uids, type=pa.int32()),
                pa.array(np.asarray([1.0, 2.0, 3.0, 4.0]) if values is None else values, type=pa.float64()),
            ],
            schema=pa.schema(
                [("row_id", pa.int64()), ("uid", pa.int32()), ("prediction", pa.float64())]
            ),
        )
        pq.write_table(table, self.prediction_path)

    def test_exact_score_axis_passes(self) -> None:
        self.write_predictions()
        with mock.patch.object(host, "SCORE_ROWS", 4):
            census = host.validate_score_predictions(self.source_path, self.prediction_path)
        self.assertTrue(census["uidAxisEqual"])
        self.assertTrue(census["singlePhysicalParquetFile"])

    def test_duplicate_missing_row_id_is_rejected_even_when_count_matches(self) -> None:
        self.write_predictions(row_ids=np.asarray([0, 1, 1, 3], dtype=np.int64))
        with mock.patch.object(host, "SCORE_ROWS", 4):
            with self.assertRaisesRegex(ValueError, "duplicate/missing/order"):
                host.validate_score_predictions(self.source_path, self.prediction_path)

    def test_uid_swap_is_rejected(self) -> None:
        self.write_predictions(uids=np.asarray([9, 7, 3, 7], dtype=np.int32))
        with mock.patch.object(host, "SCORE_ROWS", 4):
            with self.assertRaisesRegex(ValueError, "uid axis"):
                host.validate_score_predictions(self.source_path, self.prediction_path)

    def test_nonfinite_prediction_is_rejected(self) -> None:
        self.write_predictions(values=np.asarray([1.0, 2.0, np.nan, 4.0]))
        with mock.patch.object(host, "SCORE_ROWS", 4):
            with self.assertRaisesRegex(ValueError, "non-finite"):
                host.validate_score_predictions(self.source_path, self.prediction_path)

    def test_worker_revalidates_written_identity(self) -> None:
        self.write_predictions()
        summary = worker.validate_prediction_file(self.prediction_path, self.row_ids, self.uids)
        self.assertEqual(summary["rows"], 4)
        swapped = np.asarray([7, 9, 7, 3], dtype=np.int32)
        with self.assertRaisesRegex(ValueError, "UID swap"):
            worker.validate_prediction_file(self.prediction_path, self.row_ids, swapped)

    def test_analyzed_plan_requires_all_230_features_and_no_label(self) -> None:
        plan = self.root / "analyzed.txt"
        plan.write_text("row_id uid features " + " ".join(f"x{index:03d}" for index in range(230)), encoding="utf-8")
        host.validate_analyzed_score_plan(plan)
        plan.write_text("row_id uid features x000 x001", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "omitted feature"):
            host.validate_analyzed_score_plan(plan)
        plan.write_text("label " + " ".join(f"x{index:03d}" for index in range(230)), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "exposes label"):
            host.validate_analyzed_score_plan(plan)


class ResourceGateTests(unittest.TestCase):
    def test_unknown_peak_blocks(self) -> None:
        resource = host.resource_gate(
            {"resourceObservation": {"peakBytes": None, "peakSource": None, "resourceStatus": "UNKNOWN"}},
            {"ExitCode": 0, "OOMKilled": False},
            False,
        )
        self.assertEqual(resource["resourceStatus"], "UNKNOWN")

    def test_timeout_and_oom_are_resource_stop(self) -> None:
        observation = {
            "resourceObservation": {
                "peakBytes": 1,
                "peakSource": "/sys/fs/cgroup/memory.peak",
                "resourceStatus": "PASS",
            }
        }
        self.assertEqual(
            host.resource_gate(observation, {"ExitCode": 137, "OOMKilled": True}, False)["resourceStatus"],
            "RESOURCE_STOP",
        )
        self.assertEqual(
            host.resource_gate(observation, {"ExitCode": 0, "OOMKilled": False}, True)["resourceStatus"],
            "RESOURCE_STOP",
        )

    def test_peak_over_exact_limit_is_resource_stop(self) -> None:
        value = {
            "resourceObservation": {
                "peakBytes": host.MAX_MEMORY_BYTES + 1,
                "peakSource": "/sys/fs/cgroup/memory.peak",
                "resourceStatus": "RESOURCE_STOP",
            }
        }
        result = host.resource_gate(value, {"ExitCode": 0, "OOMKilled": False}, False)
        self.assertEqual(result["resourceStatus"], "RESOURCE_STOP")

    def test_runtime_versions_are_required_and_pinned(self) -> None:
        valid = {
            "runtimeVersions": {
                "sparkVersion": "4.1.3",
                "javaVersion": "21.0.11",
                "pythonVersion": "3.12.10",
            }
        }
        self.assertEqual(host.validate_runtime_versions(valid)["javaVersion"], "21.0.11")
        with self.assertRaisesRegex(ValueError, "missing"):
            host.validate_runtime_versions({})
        drift = json.loads(json.dumps(valid))
        drift["runtimeVersions"]["sparkVersion"] = "4.1.4"
        with self.assertRaisesRegex(ValueError, "Spark runtime drift"):
            host.validate_runtime_versions(drift)
        drift = json.loads(json.dumps(valid))
        drift["runtimeVersions"]["javaVersion"] = "17.0.1"
        with self.assertRaisesRegex(ValueError, "Java runtime drift"):
            host.validate_runtime_versions(drift)


class ContainerFailureTests(unittest.TestCase):
    @staticmethod
    def successful_command(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    @staticmethod
    def terminal_output(status: str = "B1_FULL_PREFLIGHT_WORKER_COMPLETE") -> str:
        return json.dumps(worker_result("feelm-b1-test", status).worker)

    def test_log_write_oserror_cannot_skip_process_or_running_container_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = mock.Mock()
            process.communicate.return_value = (self.terminal_output(), None)
            process.poll.return_value = None
            process.wait.return_value = 0
            terminal_state = {"Running": True, "ExitCode": 0, "OOMKilled": False}
            stopped_state = {"Running": False, "ExitCode": 0, "OOMKilled": False}
            termination = {"confirmedStopped": True, "attempts": [{"action": ["stop"], "returncode": 0}]}
            with mock.patch.object(host, "running_container_names", return_value=[]), mock.patch.object(
                host.subprocess, "run", side_effect=self.successful_command
            ) as run, mock.patch.object(host.subprocess, "Popen", return_value=process), mock.patch.object(
                host, "inspect_container_state", side_effect=[terminal_state, stopped_state]
            ), mock.patch.object(host, "terminate_exact_container", return_value=termination) as terminate, mock.patch.object(
                Path, "open", side_effect=OSError("disk unavailable")
            ):
                with self.assertRaises(host.ContainerExecutionError) as raised:
                    host.run_container(
                        "feelm-b1-test",
                        ["docker", "create", "--name", "feelm-b1-test"],
                        root / "run.log",
                        expected_worker_status="B1_FULL_PREFLIGHT_WORKER_COMPLETE",
                    )
            process.kill.assert_called_once_with()
            process.wait.assert_called_once_with(timeout=30)
            terminate.assert_called_once_with("feelm-b1-test")
            self.assertTrue(any(call.args[0][:2] == ["docker", "rm"] for call in run.call_args_list))
            evidence = raised.exception.evidence
            self.assertTrue(evidence["containerCleanup"]["removed"])
            self.assertEqual(evidence["log"]["writeStatus"], "FAILED")
            self.assertEqual(evidence["log"]["errorType"], "OSError")

    def test_empty_terminal_output_oom_preserves_state_resource_command_and_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            standalone = root / "standalone"
            team = root / "team"
            standalone.mkdir()
            team.mkdir()
            paths = host.ExecutionPaths.from_roots(standalone, team)
            process = mock.Mock()
            process.communicate.return_value = ("", None)
            process.poll.return_value = 0
            oom_state = {"Running": False, "ExitCode": 137, "OOMKilled": True}
            command = ["docker", "create", "--name", "feelm-b1-oom"]
            with mock.patch.object(host, "running_container_names", return_value=[]), mock.patch.object(
                host.subprocess, "run", side_effect=self.successful_command
            ), mock.patch.object(host.subprocess, "Popen", return_value=process), mock.patch.object(
                host, "inspect_container_state", side_effect=[oom_state, oom_state]
            ):
                with self.assertRaises(host.ContainerExecutionError) as raised:
                    host.run_container(
                        "feelm-b1-oom",
                        command,
                        root / "stage" / "run.log",
                        expected_worker_status="B1_MODEL_FIT_WORKER_COMPLETE",
                    )
            evidence = raised.exception.evidence
            self.assertFalse(evidence["workerTerminalResultPresent"])
            self.assertEqual(evidence["dockerState"], oom_state)
            self.assertEqual(evidence["resource"]["resourceStatus"], "RESOURCE_STOP")
            self.assertIsNone(evidence["resource"]["peakBytes"])
            self.assertIsNone(evidence["resource"]["peakSource"])
            self.assertEqual(evidence["command"], command)
            self.assertEqual(evidence["stdout"], "")
            self.assertEqual(evidence["stdoutSha256"], host.sha256_bytes(b""))
            self.assertTrue(evidence["containerCleanup"]["removed"])

            host.write_failure(paths, "fit", raised.exception, cleanup_ok=True)
            failure = host.load_json(paths.failure("fit"))
            self.assertEqual(failure["status"], "FAILED")
            self.assertEqual(failure["containerRun"]["dockerState"], oom_state)
            self.assertEqual(failure["containerRun"]["resource"]["resourceStatus"], "RESOURCE_STOP")
            self.assertFalse(failure["containerRun"]["workerTerminalResultPresent"])

    def test_timeout_without_terminal_json_preserves_partial_stdout_and_resource_stop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = mock.Mock()
            process.communicate.side_effect = [
                host.subprocess.TimeoutExpired("docker start", 9, output="partial spark log\n"),
                ("partial spark log\n", None),
            ]
            process.poll.return_value = 0
            stopped_state = {"Running": False, "ExitCode": 143, "OOMKilled": False}
            termination = {"confirmedStopped": True, "attempts": [{"action": ["stop"], "returncode": 0}]}
            with mock.patch.object(host, "running_container_names", return_value=[]), mock.patch.object(
                host.subprocess, "run", side_effect=self.successful_command
            ), mock.patch.object(host.subprocess, "Popen", return_value=process), mock.patch.object(
                host, "inspect_container_state", side_effect=[stopped_state, stopped_state]
            ), mock.patch.object(host, "terminate_exact_container", return_value=termination) as terminate:
                with self.assertRaises(host.ContainerExecutionError) as raised:
                    host.run_container(
                        "feelm-b1-timeout",
                        ["docker", "create", "--name", "feelm-b1-timeout"],
                        root / "run.log",
                        expected_worker_status="B1_MODEL_FIT_WORKER_COMPLETE",
                        timeout_seconds=9,
                    )
            terminate.assert_called_once_with("feelm-b1-timeout")
            evidence = raised.exception.evidence
            self.assertTrue(evidence["timedOut"])
            self.assertEqual(evidence["resource"]["resourceStatus"], "RESOURCE_STOP")
            self.assertEqual(evidence["resource"]["exitCode"], 143)
            self.assertEqual(evidence["stdout"], "partial spark log\n")
            self.assertFalse(evidence["workerTerminalResultPresent"])
            self.assertTrue(evidence["containerCleanup"]["removed"])


class LogicalViewIdentityTests(unittest.TestCase):
    @staticmethod
    def rows(weight: float = 0.25, omit_last: bool = False) -> list[SimpleNamespace]:
        values: list[SimpleNamespace] = []
        for row_id in (3, 8):
            natural = np.full(230, row_id, dtype=np.float64)
            masked = natural.copy()
            masked[200:] = 0.0
            for view_id, features in ((0, natural), (1, masked), (2, natural), (3, masked)):
                values.append(
                    SimpleNamespace(
                        row_id=row_id,
                        uid=17,
                        label=3.5,
                        view_id=view_id,
                        view_weight=weight,
                        features=SimpleNamespace(toArray=lambda current=features: current),
                    )
                )
        return values[:-1] if omit_last else values

    def test_four_views_and_little_endian_identity_pass(self) -> None:
        record = list(worker._partition_identity(0, iter(self.rows())))[0]
        digest = worker.hashlib.sha256()
        digest.update((3).to_bytes(8, "little", signed=True))
        digest.update((8).to_bytes(8, "little", signed=True))
        self.assertEqual(record["sourceRows"], 2)
        self.assertEqual(record["logicalRows"], 8)
        self.assertEqual(record["view0RowIdSha256"], digest.hexdigest())

    def test_missing_view_and_weight_drift_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "four views"):
            list(worker._partition_identity(0, iter(self.rows(omit_last=True))))
        with self.assertRaisesRegex(ValueError, "view weight"):
            list(worker._partition_identity(0, iter(self.rows(weight=0.5))))


class ThresholdFixtureTests(unittest.TestCase):
    def test_tree_split_walk_uses_spark_413_node_classes_without_isleaf(self) -> None:
        class FakeJavaClass:
            def __init__(self, name: str) -> None:
                self.name = name

            def getName(self) -> str:
                return self.name

        class FakeSplit:
            def featureIndex(self) -> int:
                return 7

            def threshold(self) -> float:
                return 0.25

        class FakeNode:
            def __init__(self, class_name: str, left: object | None = None, right: object | None = None) -> None:
                self.class_name = class_name
                self.left = left
                self.right = right

            def getClass(self) -> FakeJavaClass:
                return FakeJavaClass(self.class_name)

            def split(self) -> FakeSplit:
                return FakeSplit()

            def leftChild(self) -> object:
                return self.left

            def rightChild(self) -> object:
                return self.right

        leaf = FakeNode("org.apache.spark.ml.tree.LeafNode")
        internal = FakeNode("org.apache.spark.ml.tree.InternalNode", leaf, leaf)
        java_tree = SimpleNamespace(rootNode=lambda: internal)
        model = SimpleNamespace(trees=[SimpleNamespace(_java_obj=java_tree)])
        self.assertEqual(worker._tree_splits(model), [(7, 0.25)])
        unknown_tree = SimpleNamespace(
            rootNode=lambda: FakeNode("org.apache.spark.ml.tree.FutureNode")
        )
        with self.assertRaisesRegex(ValueError, "unsupported Spark tree node class"):
            worker._tree_splits(SimpleNamespace(trees=[SimpleNamespace(_java_obj=unknown_tree)]))

    def test_exact_below_equal_above_triplet(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixtures.npz"
            threshold = 0.25
            matrix = np.zeros((3, 230), dtype=np.float64)
            matrix[:, 7] = [
                np.nextafter(threshold, -np.inf),
                threshold,
                np.nextafter(threshold, np.inf),
            ]
            np.savez_compressed(
                path,
                features=matrix,
                predictions=np.ones(3),
                indices=np.arange(230, dtype=np.int32),
                split_features=np.asarray([7, 7, 7], dtype=np.int32),
                split_thresholds=np.asarray([threshold, threshold, threshold]),
            )
            features, predictions = host._validate_threshold_fixtures(path)
            self.assertEqual(features.shape, (3, 230))
            self.assertEqual(predictions.shape, (3,))


class PreflightSequenceTests(unittest.TestCase):
    def make_paths(self, root: Path) -> host.ExecutionPaths:
        standalone = root / "standalone"
        team = root / "team"
        standalone.mkdir()
        team.mkdir()
        return host.ExecutionPaths.from_roots(standalone, team)

    def identity(self) -> dict[str, object]:
        return {
            "schemaVersion": "feelm-service-v1-b1-partition-identity/1",
            "sourceRows": host.SOURCE_ROWS,
            "logicalRows": host.LOGICAL_ROWS,
            "partitions": [{"partition": index} for index in range(host.PARTITIONS)],
        }

    def test_preflight_runs_dry_first_and_publishes_only_after_full(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self.make_paths(Path(directory))
            record = {"path": "source", "bytes": 1, "sha256": "a" * 64}
            implementation = (
                {"path": "implementation/outer", "bytes": 1, "sha256": "b" * 64},
                {"path": "implementation/worker", "bytes": 1, "sha256": "c" * 64},
                "d" * 64,
            )
            calls: list[str] = []

            def fake_run(name: str, command: list[str], log_path: Path, *, expected_worker_status: str, timeout_seconds: int = host.TIMEOUT_SECONDS) -> host.ContainerResult:
                calls.append(expected_worker_status)
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.write_text("dry\n" if len(calls) == 1 else "dry\nfull\n", encoding="utf-8")
                if expected_worker_status == "B1_FULL_PREFLIGHT_WORKER_COMPLETE":
                    write_json(log_path.parent / "partition-identity.json", self.identity())
                    result = worker_result(name, expected_worker_status)
                    result.worker["modelFitPerformed"] = False
                    result.worker["scorePerformed"] = False
                    return result
                return worker_result(name, expected_worker_status, 16_384)

            patches = (
                mock.patch.object(host, "running_container_names", return_value=[]),
                mock.patch.object(host, "verify_team_commit"),
                mock.patch.object(host, "inspect_image_id", return_value=host.IMAGE_ID),
                mock.patch.object(host, "training_source_records", return_value=[record]),
                mock.patch.object(host, "predecessor_failure_record", return_value={"path": "failure", "bytes": 1, "sha256": "e" * 64}),
                mock.patch.object(host, "implementation_pins", return_value=implementation),
                mock.patch.object(host, "training_mounts", return_value=[]),
                mock.patch.object(host, "spark_container_command", side_effect=lambda name, mounts, args: [name, args[0]]),
                mock.patch.object(host, "run_container", side_effect=fake_run),
            )
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
                output = host.preflight(paths)
            self.assertEqual(
                calls,
                ["DRY_RUN_ONLY_NOT_PUBLISHED", "B1_FULL_PREFLIGHT_WORKER_COMPLETE"],
            )
            self.assertTrue((output / "manifest.json").is_file())
            command = host.load_json(output / "command.json")
            self.assertTrue(command["dryRunPrecedesFullMaterialization"])
            self.assertEqual([row["order"] for row in command["sequence"]], [0, 1])

    def test_dry_failure_prevents_full_materialization_and_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self.make_paths(Path(directory))
            record = {"path": "source", "bytes": 1, "sha256": "a" * 64}
            implementation = (
                {"path": "implementation/outer", "bytes": 1, "sha256": "b" * 64},
                {"path": "implementation/worker", "bytes": 1, "sha256": "c" * 64},
                "d" * 64,
            )
            run = mock.Mock(side_effect=ValueError("dry failed"))
            with mock.patch.object(host, "running_container_names", return_value=[]), mock.patch.object(
                host, "verify_team_commit"
            ), mock.patch.object(host, "inspect_image_id", return_value=host.IMAGE_ID), mock.patch.object(
                host, "training_source_records", return_value=[record]
            ), mock.patch.object(host, "predecessor_failure_record", return_value={"path": "failure", "bytes": 1, "sha256": "e" * 64}), mock.patch.object(
                host, "implementation_pins", return_value=implementation
            ), mock.patch.object(
                host, "training_mounts", return_value=[]
            ), mock.patch.object(host, "spark_container_command", return_value=["docker"]), mock.patch.object(
                host, "run_container", run
            ):
                with self.assertRaisesRegex(ValueError, "dry failed"):
                    host.preflight(paths)
            self.assertEqual(run.call_count, 1)
            self.assertFalse(paths.bundle("preflight").exists())
            self.assertTrue(paths.failure("preflight").is_file())

    def test_atomic_publish_refuses_existing_final(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "stage"
            final = root / "final"
            stage.mkdir()
            final.mkdir()
            (stage / "manifest.json").write_bytes(b"x")
            with self.assertRaisesRegex(ValueError, "already exists"):
                host.atomic_publish_directory(stage, final)
            self.assertTrue((stage / "manifest.json").is_file())


class ReviewContractTests(unittest.TestCase):
    def create_phase(self, paths: host.ExecutionPaths, phase: str) -> Path:
        bundle = paths.bundle(phase)
        bundle.mkdir(parents=True)
        target_map = {
            "preflight": host.PREFLIGHT_REVIEW_TARGETS,
            "fit": host.FIT_REVIEW_TARGETS,
            "score": host.SCORE_REVIEW_TARGETS,
        }[phase]
        for relative in target_map.values():
            path = bundle / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.name != "manifest.json":
                if path.name.endswith("input-lock.json"):
                    write_json(
                        path,
                        {
                            "trainingSourceSetSha256": "a" * 64,
                            "inputSetSha256": "b" * 64,
                        },
                    )
                else:
                    path.write_bytes(b"fixture")
        outer, spark_worker, implementation = host.implementation_pins(paths)
        status = {
            "preflight": "B1_FULL_PREFLIGHT_COMPLETE_AWAITING_REVIEW",
            "fit": "B1_MODEL_FIT_COMPLETE_AUDIT_PENDING",
            "score": "B1_NATURAL_SCORE_COMPLETE_AUDIT_PENDING",
        }[phase]
        lock_name = "score-input-lock.json" if phase == "score" else "input-lock.json"
        lock = host.load_json(bundle / lock_name)
        manifest = {
            "status": status,
            "outerRunnerSha256": outer["sha256"],
            "sparkWorkerSha256": spark_worker["sha256"],
            "implementationSetSha256": implementation,
            "inputSetSha256": lock["inputSetSha256"],
        }
        if phase != "score":
            manifest["trainingSourceSetSha256"] = lock["trainingSourceSetSha256"]
        manifest["files"] = host.relative_inventory(bundle, exclude=("manifest.json",))
        write_json(bundle / "manifest.json", manifest)
        contract = host.review_contract(paths, phase)
        review = {"status": "PASS", "phase": phase, "target": contract["target"]}
        write_json(paths.review(phase), review)
        return paths.review(phase)

    def test_score_review_uses_score_input_lock_key_and_pins_both_implementations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = host.ExecutionPaths.from_roots(root / "standalone", root / "team")
            paths.standalone.mkdir()
            paths.team.mkdir()
            paths.outer_runner.parent.mkdir(parents=True)
            paths.outer_runner.write_bytes(b"outer")
            paths.spark_worker.write_bytes(b"worker")
            review = self.create_phase(paths, "score")
            result = host.validate_score_review(paths, review)
            self.assertEqual(result["review"]["status"], "PASS")
            self.assertIn("outer_runner", result["review"]["target"])
            self.assertIn("spark_worker", result["review"]["target"])

    def test_review_target_tamper_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = host.ExecutionPaths.from_roots(root / "standalone", root / "team")
            paths.standalone.mkdir()
            paths.team.mkdir()
            paths.outer_runner.parent.mkdir(parents=True)
            paths.outer_runner.write_bytes(b"outer")
            paths.spark_worker.write_bytes(b"worker")
            review_path = self.create_phase(paths, "preflight")
            review = host.load_json(review_path)
            review["target"]["spark_worker"]["sha256"] = "0" * 64
            review_path.write_text(json.dumps(review), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "spark_worker sha256 drift"):
                host.validate_preflight_review(paths, review_path)


if __name__ == "__main__":
    unittest.main()
