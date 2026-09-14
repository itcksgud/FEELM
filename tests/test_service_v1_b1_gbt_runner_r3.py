"""Contract tests for the r3 host runner. Docker/Spark public phases are never run."""
from __future__ import annotations

import argparse
import ast
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
import sys
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_service_v1_b1_gbt_r3 as host


def record(path: str, marker: str) -> dict[str, object]:
    return {"path": path, "bytes": 1, "sha256": hashlib.sha256(marker.encode()).hexdigest()}


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


class IdentityAndLockTests(unittest.TestCase):
    def test_reviewed_plan_profile_and_real_r2_ancestry(self) -> None:
        paths = host.ExecutionPaths.from_roots(ROOT, ROOT.parent / "S15P21E106")
        self.assertEqual(host.verify_plan(paths)["sha256"], host.RECOVERY_PLAN_SHA256)
        profile, profile_pin = host.execution_profile(paths)
        self.assertEqual(profile["runId"], host.RUN_ID)
        self.assertEqual(profile_pin["path"], "execution/local4c12g-t14400-profile.json")
        ancestry = host.validate_r2_ancestry(paths)
        self.assertEqual(ancestry["r2FitFailure"]["sha256"], host.R2_FIT_FAILURE_SHA256)
        self.assertEqual(ancestry["r2Plan"]["path"], "ancestor/service-v1-b1-spark-runner.md")

    def test_legacy_alias_is_exact_and_traversal_is_rejected(self) -> None:
        paths = host.ExecutionPaths.from_roots(ROOT, ROOT.parent / "S15P21E106")
        self.assertEqual(host.resolve_logical_path(paths, "ancestor/service-v1-b1-spark-runner.md"), paths.r2_plan)
        for value in ("ancestor/other.md", "ancestor/../scripts/x.py", "ancestor\\service-v1-b1-spark-runner.md",
                      "standalone/../outside", "unknown/file"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                host.resolve_logical_path(paths, value)

    def test_score_keeps_nine_model_records_and_separate_single_score_record(self) -> None:
        models = [record(f"model/{index}", f"m{index}") for index in range(9)]
        scores = [record("source/natural-score.parquet", "score")]
        runtime = [record("runtime/worker", "worker")]
        execution = [record("execution/runner", "runner")]
        controls = [record("control/fit", "fit")]
        model_digest = host.canonical_record_set_sha256(models)
        with mock.patch.object(host, "MODEL_INPUT_SET_SHA256", model_digest):
            lock = host.make_score_lock(models, scores, runtime, execution, controls)
        self.assertEqual(len(lock["modelInputRecords"]), 9)
        self.assertEqual(lock["scoreInputRecords"], scores)
        self.assertEqual(lock["modelInputSetSha256"], model_digest)
        self.assertEqual(lock["scoreInputSetSha256"], host.canonical_record_set_sha256(scores))
        self.assertEqual(lock["inputSetSha256"], host.canonical_record_set_sha256(models + scores + runtime + execution + controls))

    def test_score_group_overlap_is_rejected(self) -> None:
        models = [record(f"model/{index}", f"m{index}") for index in range(9)]
        with mock.patch.object(host, "MODEL_INPUT_SET_SHA256", host.canonical_record_set_sha256(models)):
            with self.assertRaisesRegex(ValueError, "overlapping"):
                host.make_score_lock(models, [copy.deepcopy(models[0])], [], [], [])

    def test_phase_lock_separates_all_digests(self) -> None:
        groups = [[record(f"g{index}/x", str(index))] for index in range(4)]
        lock = host.make_phase_lock("fit", *groups)
        self.assertEqual(set(lock), {"schemaVersion", "phase", "runId", "profileId", "modelInputRecords",
            "workerRuntimeRecords", "executionRecords", "controlReferences", "modelInputSetSha256",
            "workerRuntimeSetSha256", "executionSetSha256", "controlReferenceSetSha256", "inputSetSha256",
            "r2TrainingSourceSetSha256", "evaluationTargetsRead"})
        self.assertEqual(len({lock[key] for key in ("modelInputSetSha256", "workerRuntimeSetSha256",
            "executionSetSha256", "controlReferenceSetSha256", "inputSetSha256")}), 5)

    def test_recovery_comparison_contains_recipe_rows_and_legacy_ancestry(self) -> None:
        paths = host.ExecutionPaths.from_roots(ROOT, ROOT.parent / "S15P21E106")
        ancestry = host.validate_r2_ancestry(paths)
        outer, worker, _ = host.implementation_pins(paths)
        _, profile_pin = host.execution_profile(paths)
        execution_digest = host.canonical_record_set_sha256(host.execution_records(paths))
        recovery = host.recovery_reference(paths, ancestry, outer, worker, profile_pin, execution_digest)
        comparison = recovery["digestComparison"]
        self.assertEqual(comparison["sourceRows"], host.SOURCE_ROWS)
        self.assertEqual(comparison["logicalRows"], host.LOGICAL_ROWS)
        self.assertEqual(comparison["trainingRecipe"]["path"], "contract/training-recipe.v1.json")
        self.assertEqual(recovery["r2Plan"]["path"], "ancestor/service-v1-b1-spark-runner.md")


class PublicationAndCleanupTests(unittest.TestCase):
    def test_sibling_publication_race_preserves_competing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "review.json"
            original = host.rename_no_replace
            def race(source: Path, destination: Path) -> None:
                destination.write_bytes(b"winner")
                original(source, destination)
            with mock.patch.object(host, "rename_no_replace", side_effect=race):
                with self.assertRaises(OSError):
                    host.atomic_write_sibling(target, {"loser": True})
            self.assertEqual(target.read_bytes(), b"winner")
            self.assertFalse(any(path.name.startswith(".review.json.tmp-") for path in target.parent.iterdir()))

    def test_directory_publication_race_preserves_competing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage, final = root / ".bundle.tmp-x", root / "bundle"
            stage.mkdir(); (stage / "ours").write_bytes(b"ours")
            original = host.rename_no_replace
            def race(source: Path, destination: Path) -> None:
                destination.mkdir(); (destination / "winner").write_bytes(b"winner")
                original(source, destination)
            with mock.patch.object(host, "rename_no_replace", side_effect=race):
                with self.assertRaises(OSError):
                    host.atomic_publish_directory(stage, final)
            self.assertEqual((final / "winner").read_bytes(), b"winner")
            self.assertEqual((stage / "ours").read_bytes(), b"ours")

    def test_cleanup_and_phase_clear_see_dangling_links_and_all_temp_prefixes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); paths = host.ExecutionPaths.from_roots(root, root / "team")
            paths.output_parent.mkdir(parents=True); final = paths.bundle("fit")
            stale_names = ["." + final.name + ".tmp-x", "." + paths.review("fit").name + ".tmp-x",
                           "." + paths.failure("fit").name + ".tmp-x", "." + final.name + ".fit-scratch-x"]
            for name in stale_names:
                stale = final.parent / name; stale.write_bytes(b"x")
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, "stale"):
                    host.ensure_phase_clear(paths, "fit")
                stale.unlink()
            link = paths.failure("fit")
            try:
                link.symlink_to(root / "missing-target")
            except OSError:
                self.skipTest("symlink creation unavailable")
            with self.assertRaisesRegex(ValueError, "failure"):
                host.ensure_phase_clear(paths, "fit")
            host.cleanup_path(link); self.assertFalse(os.path.lexists(link))

    def test_phase_filesystem_mutations_are_inside_failure_guard(self) -> None:
        tree = ast.parse((SCRIPTS / "run_service_v1_b1_gbt_r3.py").read_text(encoding="utf-8"))
        functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        for name in ("preflight", "fit", "score"):
            guarded_calls: set[str] = set()
            for node in functions[name].body:
                if isinstance(node, ast.Try) and node.handlers:
                    for child in ast.walk(ast.Module(body=node.body, type_ignores=[])):
                        if isinstance(child, ast.Call):
                            if isinstance(child.func, ast.Name): guarded_calls.add(child.func.id)
                            elif isinstance(child.func, ast.Attribute): guarded_calls.add(child.func.attr)
            self.assertTrue({"make_stage", "mkdir", "validate_mounts"} <= guarded_calls, name)


class ResourceAndFailureTests(unittest.TestCase):
    def test_resource_requires_periodic_host_peak_and_uses_maximum(self) -> None:
        worker = {"resourceObservation": {"peakBytes": 100, "peakSource": "worker", "resourceStatus": "PASS"}}
        state = {"OOMKilled": False, "ExitCode": 0}
        self.assertEqual(host.resource_gate(worker, state, False, {"status": "UNKNOWN", "sampleCount": 0})["resourceStatus"], "UNKNOWN")
        passed = host.resource_gate(worker, state, False, {"status": "OBSERVED", "sampleCount": 2, "peakBytes": 200})
        self.assertEqual((passed["resourceStatus"], passed["peakBytes"]), ("PASS", 200))

    def test_monitor_refuses_to_return_while_sampler_is_alive(self) -> None:
        monitor = host.CgroupPeakMonitor("x"); thread = mock.Mock(); thread.is_alive.return_value = True; monitor._thread = thread
        with mock.patch.object(host.time, "monotonic", side_effect=[0.0, 21.0]):
            with self.assertRaisesRegex(ValueError, "did not stop"):
                monitor.stop()

    def test_timeout_samples_and_stops_monitor_then_inspects_before_termination(self) -> None:
        events: list[str] = []
        process = mock.Mock()
        process.communicate.side_effect = [subprocess.TimeoutExpired(["docker"], 1, output="partial"), ("partial", "")]
        process.poll.return_value = 0
        monitor = mock.Mock()
        monitor.start.side_effect = lambda: events.append("monitor-start")
        monitor.sample_before_stop.side_effect = lambda: events.append("sample-before-stop")
        monitor.stop.side_effect = lambda: (events.append("monitor-stop") or {
            "status": "OBSERVED", "sampleCount": 2, "peakBytes": 100, "peakSource": "cgroup-v2"})
        states = [{"Running": True, "ExitCode": None, "OOMKilled": False},
                  {"Running": False, "ExitCode": 143, "OOMKilled": False}]
        def inspect(_name):
            events.append("inspect"); return states.pop(0)
        def terminate(_name):
            events.append("terminate"); return {"confirmedStopped": True, "removed": False, "attempts": []}
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(host, "running_container_names", return_value=[]), \
             mock.patch.object(host.subprocess, "run", return_value=mock.Mock(returncode=0, stderr="")), \
             mock.patch.object(host.subprocess, "Popen", return_value=process), \
             mock.patch.object(host, "CgroupPeakMonitor", return_value=monitor), \
             mock.patch.object(host, "inspect_container_state", side_effect=inspect), \
             mock.patch.object(host, "terminate_exact_container", side_effect=terminate), \
             mock.patch.object(host, "remove_stopped_container"):
            with self.assertRaises(host.ContainerExecutionError) as raised:
                host.run_container("feelm-b1-timeout-fixture", ["docker", "create"], Path(directory) / "run.log",
                                   expected_worker_status="never", timeout_seconds=1)
        evidence = raised.exception.evidence
        self.assertTrue(evidence["timedOut"])
        self.assertEqual(evidence["preStopDockerState"]["Running"], True)
        self.assertLess(events.index("sample-before-stop"), events.index("monitor-stop"))
        self.assertLess(events.index("monitor-stop"), events.index("inspect"))
        self.assertLess(events.index("inspect"), events.index("terminate"))

    def _post_container_failure(self, phase: str) -> dict[str, object]:
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        root = Path(temporary.name); paths = host.ExecutionPaths.from_roots(root, root / "team"); paths.output_parent.mkdir(parents=True)
        result = host.ContainerResult(container_name="feelm-b1-fixture", command=["docker", "create"], output="complete log",
            worker={"status": "COMPLETE"}, docker_state={"Running": False, "ExitCode": 0, "OOMKilled": False},
            resource={"resourceStatus": "PASS", "peakBytes": 10}, elapsed_seconds=1.0,
            cleanup={"confirmedStopped": True, "removed": True},
            host_observation={"status": "OBSERVED", "sampleCount": 2, "peakBytes": 10})
        completed = host.completed_container_evidence(result, phase)
        context = {"phase": phase, "phaseInputLock": {"phase": phase}, "recoveryReference": {"status": "R2_ANCESTRY_VERIFIED"},
            "phaseReference": {"parent": "complete"}, "executionProfile": {"profileId": host.PROFILE_ID},
            "commandDocument": {"phase": phase, "sequence": [{"command": result.command}]}, "implementation": {},
            "completedStages": [completed]}
        stage = paths.bundle(phase).with_name(".attempt.tmp-fixture"); stage.mkdir(); (stage / "run.log").write_text("complete log", encoding="utf-8")
        material = host.capture_failure_material(stage, context, ValueError("host validation failed")); host.cleanup_path(stage)
        with mock.patch.object(host, "running_container_names", return_value=[]):
            host.write_failure(paths, phase, ValueError("host validation failed"), True, material, [stage])
        return json.loads(paths.failure(phase).read_text(encoding="utf-8"))

    def _assert_closed_failure(self, payload: dict[str, object], phase: str) -> None:
        self.assertEqual(payload["schemaVersion"], "feelm-service-v1-b1-r3-failure/1")
        self.assertEqual((payload["runId"], payload["profileId"], payload["phase"]), (host.RUN_ID, host.PROFILE_ID, phase))
        for key in ("phaseInputLock", "recoveryReference", "phaseReference", "executionProfile", "commandDocument"):
            envelope = payload[key]
            self.assertEqual(envelope, host.embedded_payload(envelope["payload"]), key)
        self.assertEqual(payload["phaseInputLock"]["payload"]["phase"], phase)
        self.assertEqual(payload["recoveryReference"]["payload"]["status"], "R2_ANCESTRY_VERIFIED")
        self.assertEqual(payload["phaseReference"]["payload"]["parent"], "complete")
        self.assertEqual(payload["executionProfile"]["payload"]["profileId"], host.PROFILE_ID)
        self.assertEqual(payload["commandDocument"]["payload"]["sequence"][0]["command"], ["docker", "create"])
        self.assertEqual(payload["runLog"]["bytes"], len(b"complete log"))
        self.assertEqual(payload["runLog"]["sha256"], hashlib.sha256(b"complete log").hexdigest())
        self.assertEqual(payload["runLog"]["content"], "complete log")
        container = payload["containerRun"]
        self.assertEqual(container["command"], ["docker", "create"])
        self.assertEqual(container["dockerState"], {"Running": False, "ExitCode": 0, "OOMKilled": False})
        self.assertEqual(container["resource"]["resourceStatus"], "PASS")
        self.assertEqual(container["hostCgroupObservation"]["status"], "OBSERVED")
        self.assertTrue(container["workerTerminalResultPresent"])
        self.assertEqual(container["workerTerminalResult"]["status"], "COMPLETE")
        self.assertEqual(container["cleanup"], {"confirmedStopped": True, "removed": True})
        self.assertFalse(container["timedOut"])
        self.assertTrue(payload["cleanupComplete"])
        self.assertFalse(payload["successPathCensus"]["bundle"]["lexists"])
        self.assertFalse(payload["successPathCensus"]["review"]["lexists"])
        self.assertFalse(payload["successPathCensus"]["transientPaths"][0]["lexistsAfterCleanup"])

    def test_fit_post_container_failure_is_self_contained(self) -> None:
        payload = self._post_container_failure("fit")
        self._assert_closed_failure(payload, "fit")

    def test_score_post_container_failure_is_self_contained(self) -> None:
        payload = self._post_container_failure("score")
        self._assert_closed_failure(payload, "score")
        self.assertEqual(payload["completedStages"][0]["workerAction"], "score")
        self.assertEqual(payload["resourceEvidence"], {"resourceStatus": "PASS", "peakBytes": 10})


class ReviewGateTests(unittest.TestCase):
    def _fixture(self, phase: str = "preflight"):
        temporary = tempfile.TemporaryDirectory(); root = Path(temporary.name)
        paths = host.ExecutionPaths.from_roots(root, root / "team"); bundle = paths.bundle(phase); bundle.mkdir(parents=True)
        lock_name = "score-input-lock.json" if phase == "score" else "input-lock.json"
        digests = {key: hashlib.sha256(key.encode()).hexdigest() for key in ("modelInputSetSha256", "workerRuntimeSetSha256",
            "executionSetSha256", "controlReferenceSetSha256", "inputSetSha256")}
        if phase == "score": digests["scoreInputSetSha256"] = hashlib.sha256(b"score").hexdigest()
        write_json(bundle / lock_name, {"runId": host.RUN_ID, "profileId": host.PROFILE_ID, **digests})
        lock_key = "score_input_lock" if phase == "score" else "input_lock"
        flags = {"preflight": {"sourceRows": host.SOURCE_ROWS, "logicalRows": host.LOGICAL_ROWS, "partitionCount": 8,
                    "fitAuthorized": False, "modelFitPerformed": False, "scorePerformed": False, "readyForService": False},
            "fit": {"sourceRows": host.SOURCE_ROWS, "logicalRows": host.LOGICAL_ROWS, "partitionCount": 8,
                    "scoringAuthorized": False, "modelFitPerformed": True, "scorePerformed": False, "readyForService": False},
            "score": {"rows": host.SCORE_ROWS, "evaluationAuthorized": False, "modelFitPerformed": False,
                    "scorePerformed": True, "readyForService": False}}[phase]
        status = {"preflight": "B1_FULL_PREFLIGHT_COMPLETE_AWAITING_REVIEW", "fit": "B1_MODEL_FIT_COMPLETE_AUDIT_PENDING",
                  "score": "B1_NATURAL_SCORE_COMPLETE_AUDIT_PENDING"}[phase]
        outer = record("execution/run_service_v1_b1_gbt_r3.py", "outer"); worker = record("implementation/service_v1_b1_spark_worker.py", "worker")
        manifest = {"status": status, "resourceStatus": "PASS", "runId": host.RUN_ID, "profileId": host.PROFILE_ID,
            "outerRunnerSha256": outer["sha256"], "sparkWorkerSha256": worker["sha256"],
            "implementationSetSha256": host.implementation_set_sha256(outer, worker), "files": {}, **digests, **flags}
        write_json(bundle / "manifest.json", manifest)
        auditor = {"bytes": 2, "sha256": "c" * 64}
        (root / "dependency.bin").write_bytes(b"xx")
        (root / "dependency-bundle").mkdir()
        dependency = {"files": {"standalone/dependency.bin": auditor},
            "bundleInventories": {"standalone/dependency-bundle": {}},
            "auditorImplementation": auditor, "dockerImageId": host.IMAGE_ID, "runId": host.RUN_ID, "profileId": host.PROFILE_ID}
        resource = {"stages": 2 if phase == "preflight" else 1, "peakBytes": 100, "cleanupConfirmed": True,
            "labelMountAbsent": True, "measurementIndependentlyObserved": False, "measurementCheckedAgainstPinnedWorkerLog": True}
        checks = {"resource": resource, "recovery": {"reference": "PASS"}}
        if phase in {"preflight", "fit"}: checks["identity"] = {"sourceRows": host.SOURCE_ROWS}
        if phase != "preflight": checks["parentChain"] = "PASS"
        if phase == "fit": checks["fit"] = {"treeCount": 120}
        if phase == "score": checks["predictions"] = {"rows": host.SCORE_ROWS}
        target = {lock_key: {"path": "standalone/lock.json", "bytes": 1, "sha256": "e" * 64, **digests}}
        review = {"schemaVersion": "feelm-service-v1-b1-r3-result-review/1", "runId": host.RUN_ID, "profileId": host.PROFILE_ID,
            "phase": phase, "status": "PASS", "createdAt": dt.datetime.now(dt.timezone.utc).isoformat(), "target": target,
            "reviewer": {"implementation": auditor, "independentFromRunner": True}, "dependencyFingerprint": dependency,
            "checks": checks, "evaluationTargetsRead": False, "modelFitPerformed": False, "readyForService": False,
            "scope": "Local immutable bundle integrity and specified numerical parity; no service acceptance or model quality verdict."}
        write_json(paths.review(phase), review)
        return temporary, paths, review, {"dependency": dependency, "auditor": auditor, "outer": outer, "worker": worker, "target": target}

    def _validate(self, paths, phase, values):
        with mock.patch.object(host, "expected_review_dependency", return_value=values["dependency"]), \
             mock.patch.object(host, "review_contract", return_value={"target": values["target"]}), \
             mock.patch.object(host, "relative_inventory", return_value={}), \
             mock.patch.object(host, "require_unlinked_path"), \
             mock.patch.object(host, "pin_file", return_value=values["auditor"]), \
             mock.patch.object(host, "implementation_pins", return_value=(values["outer"], values["worker"], host.implementation_set_sha256(values["outer"], values["worker"]))):
            return host.validate_review(paths, phase)

    def test_complete_synthetic_reviews_are_accepted_for_all_phases(self) -> None:
        for phase in ("preflight", "fit", "score"):
            temporary, paths, _, values = self._fixture(phase); self.addCleanup(temporary.cleanup)
            self.assertEqual(self._validate(paths, phase, values)["review"]["status"], "PASS")

    def test_empty_partial_extra_dependency_reviews_are_rejected(self) -> None:
        for mutation in ("empty", "partial", "extra"):
            temporary, paths, review, values = self._fixture(); self.addCleanup(temporary.cleanup); changed = copy.deepcopy(review)
            if mutation == "empty": changed["dependencyFingerprint"]["files"] = {}; changed["dependencyFingerprint"]["bundleInventories"] = {}
            elif mutation == "partial": changed["dependencyFingerprint"]["files"] = {}
            else: changed["dependencyFingerprint"]["files"]["extra"] = {"bytes": 0, "sha256": "0" * 64}
            write_json(paths.review("preflight"), changed)
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, "closure"):
                self._validate(paths, "preflight", values)

    def test_truncated_checks_are_rejected(self) -> None:
        temporary, paths, review, values = self._fixture("fit"); self.addCleanup(temporary.cleanup)
        del review["checks"]["fit"]; write_json(paths.review("fit"), review)
        with self.assertRaisesRegex(ValueError, "check set"):
            self._validate(paths, "fit", values)


class CliAndStaticTests(unittest.TestCase):
    def test_public_cli_is_exact_and_has_no_resource_override(self) -> None:
        parser = host.parser(); choices = {}
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction): choices.update(action.choices)
        self.assertEqual(set(choices), {"preflight", "fit", "score"})
        for phase_parser in choices.values():
            self.assertFalse({action.dest for action in phase_parser._actions} & {"cpus", "memory", "driver_memory", "timeout", "run_id", "profile_id"})

    def test_no_production_assert_and_auditor_call_arity(self) -> None:
        for path in (SCRIPTS / "run_service_v1_b1_gbt_r3.py", SCRIPTS / "audit_service_v1_b1_spark_outputs_r3.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            self.assertEqual(sum(isinstance(node, ast.Assert) for node in ast.walk(tree)), 0)
        tree = ast.parse((SCRIPTS / "audit_service_v1_b1_spark_outputs_r3.py").read_text(encoding="utf-8"))
        expected = {"audit_recovery_reference": 5, "audit_reference": 7, "audit_lock": 7,
                    "audit_resource_and_commands": 4, "dependency_fingerprint": 3}
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            if call.func.id in expected: self.assertEqual(len(call.args), expected[call.func.id], (call.lineno, call.func.id))


if __name__ == "__main__":
    unittest.main()
