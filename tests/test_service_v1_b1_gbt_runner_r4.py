from __future__ import annotations

import ast
import contextlib
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts import run_service_v1_b1_gbt_r4 as runner

ROOT = Path(__file__).resolve().parents[1]
TEAM = ROOT.parent / "S15P21E106"


class ReviewedContractTests(unittest.TestCase):
    def setUp(self):
        self.paths = runner.ExecutionPaths.from_roots(ROOT, TEAM)

    def test_plan_and_profile_are_exact_reviewed_bytes(self):
        plan = runner.verify_plan(self.paths)
        profile, profile_pin = runner.execution_profile(self.paths)
        self.assertEqual((plan["bytes"], plan["sha256"]), (82_061, runner.RECOVERY_PLAN_SHA256))
        self.assertEqual((profile_pin["bytes"], profile_pin["sha256"]), (6_516, runner.PROFILE_SHA256))
        self.assertEqual(profile["profileId"], runner.PROFILE_ID)
        self.assertEqual(profile["docker"]["cpus"], "5")
        self.assertEqual(profile["docker"]["memory"], "20g")
        self.assertEqual(profile["spark"], {"master": "local[5]", "driverMemory": "12g",
                                            "shufflePartitions": 8, "adaptiveExecution": False})
        self.assertEqual(profile["timeoutsSeconds"]["fit"], 28_800)
        self.assertFalse(profile["automaticRetry"])

    def test_execution_paths_are_r4_canonical(self):
        self.assertEqual(self.paths.bundle("calibrate-select").name, runner.RUN_ID + "-selection")
        self.assertEqual(self.paths.bundle("confirmation").name, runner.RUN_ID + "-confirmation")
        self.assertEqual(self.paths.host_runtime_lock.name, "service-v1-b1-r4-host-runtime-lock.json")
        self.assertEqual(self.paths.tests.name, "test_service_v1_b1_gbt_runner_r4.py")

    def test_r3_inventory_and_implementation_pins_are_complete(self):
        self.assertEqual(len(runner.R3_IMPLEMENTATION_PINS), 10)
        self.assertEqual(set(runner.R3_PREFLIGHT_INVENTORY), {
            "command.json", "execution-profile.json", "input-lock.json", "manifest.json",
            "partition-identity.json", "recovery-reference.json", "resource.json", "run.log"})
        self.assertEqual(runner.R3_FIT_FAILURE_BYTES, 100_384)

    def test_spark_command_has_server_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "x"
            source.write_text("x", encoding="utf-8")
            command = runner.spark_container_command(
                "feelm-test", [runner.Mount(source, "/input/x")], ["fit"])
        self.assertEqual(command[command.index("--cpus") + 1], "5")
        self.assertEqual(command[command.index("--memory") + 1], "20g")
        self.assertEqual(command[command.index("--memory-swap") + 1], "20g")
        self.assertEqual(command[command.index("--master") + 1], "local[5]")
        self.assertEqual(command[command.index("--driver-memory") + 1], "12g")
        self.assertIn("spark.sql.adaptive.enabled=false", command)


class JsonAndFilesystemTests(unittest.TestCase):
    def test_duplicate_and_nonfinite_json_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "value.json"
            path.write_text('{"a":1,"a":2}\n', encoding="utf-8", newline="")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                runner.load_json(path)
            path.write_text('{"a":NaN}\n', encoding="utf-8", newline="")
            with self.assertRaisesRegex(ValueError, "non-finite"):
                runner.load_json(path)

    def test_json_null_can_be_forbidden(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "value.json"
            path.write_text('{"a":null}\n', encoding="utf-8", newline="")
            with self.assertRaisesRegex(ValueError, "null"):
                runner.load_json(path, allow_null=False)

    def test_hard_link_alias_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "original"
            alias = Path(directory) / "alias"
            original.write_bytes(b"x")
            try:
                os.link(original, alias)
            except OSError:
                self.skipTest("hard links unavailable")
            with self.assertRaisesRegex(ValueError, "hard-link"):
                runner.pin_file(original)

    def test_record_digest_is_order_independent_and_collision_rejected(self):
        a = {"path": "a", "bytes": 1, "sha256": "1" * 64}
        b = {"path": "b", "bytes": 2, "sha256": "2" * 64}
        self.assertEqual(runner.canonical_record_set_sha256([a, b]),
                         runner.canonical_record_set_sha256([b, a]))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            runner.canonical_record_set_sha256([a, a])


class CliAndSupervisionTests(unittest.TestCase):
    def test_public_preflight_requires_all_five_control_pin_pairs(self):
        parser = runner.parser()
        base = ["--standalone-root", str(ROOT), "--team-repo", str(TEAM), "preflight"]
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(base)
        args = list(base)
        for stem in ("server-receipt-manifest", "server-receipt-review", "host-runtime-lock",
                     "delivery-manifest", "delivery-review"):
            args += ["--" + stem, str(ROOT / (stem + ".json")),
                     "--expected-" + stem + "-sha256", "a" * 64]
        parsed = parser.parse_args(args)
        self.assertEqual(parsed.action, "preflight")
        self.assertEqual(parsed.expected_delivery_review_sha256, "a" * 64)

    def test_cli_rejects_uppercase_sha(self):
        parser = runner.parser()
        args = ["--standalone-root", str(ROOT), "--team-repo", str(TEAM), "preflight"]
        for stem in ("server-receipt-manifest", "server-receipt-review", "host-runtime-lock",
                     "delivery-manifest", "delivery-review"):
            args += ["--" + stem, str(ROOT / (stem + ".json")),
                     "--expected-" + stem + "-sha256", "A" * 64]
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(args)

    def test_evaluator_request_control_mapping_stays_seven_keys(self):
        controls = {name: ROOT / (name + ".json") for name in (
            "score_manifest", "score_review", "delivery_manifest", "delivery_review",
            "server_receipt_manifest", "server_receipt_review", "host_runtime_lock")}
        expected = {name: "a" * 64 for name in controls}
        mapped, hashes = runner._evaluation_control_maps(controls, expected, confirmation=False)
        self.assertEqual(len(mapped), 7)
        self.assertEqual(len(hashes), 7)
        with self.assertRaisesRegex(ValueError, "missing"):
            runner._evaluation_control_maps(controls, expected, confirmation=True)

    def test_evaluation_cli_exposes_exact_reviewed_actions_and_hashes(self):
        parser = runner.parser()
        common = ["--standalone-root", str(ROOT), "--team-repo", str(TEAM)]
        stems = ("server-receipt-manifest", "server-receipt-review", "host-runtime-lock",
                 "delivery-manifest", "delivery-review", "score-manifest", "score-review")
        selection = [*common, "calibrate-select"]
        for stem in stems:
            selection += ["--" + stem, str(ROOT / (stem + ".json")),
                          "--expected-" + stem + "-sha256", "a" * 64]
        self.assertEqual(parser.parse_args(selection).action, "calibrate-select")
        confirmation = [*common, "confirmation", *selection[len(common) + 1:]]
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(confirmation)
        for stem in ("selection-manifest", "selection-review"):
            confirmation += ["--" + stem, str(ROOT / (stem + ".json")),
                             "--expected-" + stem + "-sha256", "b" * 64]
        parsed = parser.parse_args(confirmation)
        self.assertEqual(parsed.action, "confirmation")
        self.assertEqual(parsed.expected_selection_review_sha256, "b" * 64)
        forged = list(selection)
        forged[-1] = "A" * 64
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(forged)

    def test_sealed_python_argv_places_B_before_I(self):
        script = ROOT / "scripts/evaluate_service_v1_b1_r4.py"
        with mock.patch.object(runner, "_sealed_runtime_environment",
                               return_value=(Path("/sealed/python3"), ["env", "-i", "TZ=UTC"])), \
             mock.patch.object(runner, "require_unlinked_path"):
            argv = runner.sealed_python_argv(
                runner.ExecutionPaths.from_roots(ROOT, TEAM), script, ["calibrate-select"])
        self.assertEqual(argv[-4:], ["-B", "-I", str(script), "calibrate-select"])

    def test_evaluation_systemd_has_exact_cgroup_limits(self):
        profile, _ = runner.execution_profile(runner.ExecutionPaths.from_roots(ROOT, TEAM))
        target, monitor = runner._evaluation_systemd_commands(
            profile, "calibrate-select", ["env", "python", "-B", "-I"], ["env", "monitor"])
        joined = " ".join(target)
        self.assertIn("CPUQuota=500%", joined)
        self.assertIn("MemoryMax=21474836480", joined)
        self.assertIn("RuntimeMaxSec=14400", joined)
        self.assertIn("TimeoutStopSec=900", joined)
        monitor_joined = " ".join(monitor)
        self.assertIn("CPUQuota=25%", monitor_joined)
        self.assertIn("MemoryMax=268435456", monitor_joined)

    def test_namespace_progression_is_exact(self):
        self.assertEqual(len(runner.expected_namespace_before("preflight")), 6)
        self.assertEqual(len(runner.expected_namespace_before("fit")), 8)
        self.assertEqual(len(runner.expected_namespace_before("score")), 10)
        self.assertEqual(len(runner.expected_namespace_before("calibrate-select")), 12)
        self.assertEqual(len(runner.expected_namespace_before("confirmation")), 14)

    def test_dynamic_host_gate_rejects_forged_nested_controls(self):
        checked = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        ends = checked + dt.timedelta(seconds=20_000)
        gate = {
            "schemaVersion": "feelm-service-v1-b1-r4-dynamic-host-gate/2", "status": "PASS",
            "runId": runner.RUN_ID, "profileId": runner.PROFILE_ID,
            "phase": "calibrate-select", "checkedAt": checked.isoformat().replace("+00:00", "Z"),
            "maintenanceReservationId": "12345678-1234-4234-8234-123456789abc",
            "reservationEndsAt": ends.isoformat().replace("+00:00", "Z"),
            "maintenanceWindowRemainingSeconds": 20_000, "requiredWindowSeconds": 16_200,
            "architecture": "x86_64", "logicalCpu": 8,
            "memory": {"memTotalBytes": 32_000_000_000, "memAvailableBytes": 25_769_803_776,
                       "source": "/proc/meminfo-kib-times-1024"},
            "filesystem": {"scratchFreeBytes": 85_899_345_920, "outputFreeBytes": 21_474_836_480,
                           "scratchFreeInodes": 100_000, "outputFreeInodes": 100_000,
                           "scratchDevice": 1, "outputDevice": 2, "stageDevice": 2, "finalDevice": 2,
                           "filesystemType": "ext2/ext3", "renameNoReplaceProbe": True},
            "docker": {"serverVersion": "1", "daemonId": "daemon", "imageId": runner.IMAGE_ID,
                       "imageUnpackedSizeBytes": 936_463_936, "imageInspectSha256": "a" * 64,
                       "runningB1Containers": [], "restartPolicy": "no"},
            "cgroup": {"version": "v2", "controllers": ["cpu", "memory", "pids"],
                       "probeContainerId": "container", "probeInitPid": 12,
                       "cgroupPath": "/system.slice/probe", "readableFiles":
                       ["cpu.stat", "memory.current", "memory.events", "memory.peak", "pids.current"],
                       "probeStatus": "PASS"},
            "supervisor": {"kind": "systemd-transient-service",
                           "phaseUnitName": "feelm-b1-r4-server5c20g-t28800-selection.service",
                           "probeUnitName": "feelm-b1-r4-server5c20g-t28800-selection-probe.service",
                           "phaseUnitExistsBefore": False, "probeStartCommand": [],
                           "probeControlGroup": "/system.slice/feelm-b1-r4-server5c20g-t28800-selection-probe.service",
                           "probeMainPid": 12, "probePidTree": [12], "cpuQuotaPercent": 500,
                           "memoryMaxBytes": 21_474_836_480, "memorySwapMaxBytes": 0,
                           "tasksMax": 4096, "killMode": "control-group", "timeoutStopSeconds": 900,
                           "status": "PASS"},
            "competingProcessCensus": [], "dockerRestartScheduled": False,
            "hostRebootScheduled": False,
            "namespaceCensus": {name: [] for name in ("completed", "failures", "reviews", "claims",
                                                          "temps", "scratches", "containers", "processes")},
            "runtime": {"lock": {"path": "runtime", "bytes": 1, "sha256": "b" * 64},
                        "interpreterSha256": "c" * 64, "venvRecordSetSha256": "d" * 64,
                        "environment": dict(runner.HOST_RUNTIME_ENVIRONMENT), "importFixtureStatus": "PASS"},
            "probeCommands": [["uname", "-m"]], "probeSetSha256": "e" * 64, "unknownFields": [],
        }
        gate["supervisor"]["probeStartCommand"] = [
            "systemd-run", "--unit", gate["supervisor"]["probeUnitName"], "--collect", "--wait", "--pipe",
            "--property", "CPUQuota=500%", "--property", "MemoryMax=21474836480",
            "--property", "MemorySwapMax=0", "--property", "TasksMax=4096",
            "--property", "KillMode=control-group", "--property", "TimeoutStopSec=900",
            "/usr/bin/sleep", "2"]
        self.assertEqual(runner.verify_dynamic_host_gate(gate, phase="calibrate-select")["status"], "PASS")
        for mutation in (lambda value: value.update(maintenanceReservationId="not-a-uuid"),
                         lambda value: value["filesystem"].update(filesystemType="ext4"),
                         lambda value: value["supervisor"].update(cpuQuotaPercent=499),
                         lambda value: value["runtime"]["environment"].update(isolatedMode=False)):
            forged = json.loads(json.dumps(gate))
            mutation(forged)
            with self.assertRaises(ValueError):
                runner.verify_dynamic_host_gate(forged, phase="calibrate-select")

    def test_current_reservation_and_runtime_binding_reject_forgery(self):
        paths = runner.ExecutionPaths.from_roots(ROOT, TEAM)
        reservation_path = (ROOT / ".fixture/maintenance-reservations/fit.json").absolute()
        inventory_path = (ROOT / ".fixture/runtime/venv-file-inventory.json").absolute()
        interpreter = Path(runner.sys.executable).resolve()
        reservation_pin = {"path": str(reservation_path), "bytes": 10, "sha256": "a" * 64}
        runtime_pin = {"path": "standalone/service-v1-b1-r4-host-runtime-lock.json",
                       "bytes": 20, "sha256": "b" * 64}
        inventory_pin = {"path": str(inventory_path), "bytes": 30, "sha256": "c" * 64}
        interpreter_pin = {"path": str(interpreter), "bytes": 1, "sha256": "9" * 64}
        reservation = {"schemaVersion": "feelm-service-v1-b1-r4-maintenance-reservation/2",
            "status": "ACTIVE", "reservationId": "12345678-1234-4234-8234-123456789abc",
            "hostIdentity": "host", "phase": "fit", "startsAt": "2026-09-14T00:00:00Z",
            "endsAt": "2026-09-14T10:00:00Z", "dockerRestartScheduled": False,
            "hostRebootScheduled": False, "issuedBy": "operator", "createdAt": "2026-09-13T23:00:00Z"}
        runtime_lock = {"schemaVersion": "feelm-service-v1-b1-r4-host-runtime-lock/1",
            "createdAt": "2026-09-13T23:00:00Z", "bootstrapInterpreter": {},
            "absoluteInterpreter": str(interpreter), "interpreterBytes": interpreter_pin["bytes"],
            "interpreterSha256": interpreter_pin["sha256"], "pythonImplementation": "CPython",
            "pythonVersion": "3", "environment": dict(runner.HOST_RUNTIME_ENVIRONMENT),
            "requirementsLock": {}, "wheelhouseManifest": {}, "venvInventory": inventory_pin,
            "packages": [], "imports": [], "evaluationFixture": {"status": "PASS"},
            "runtimeSetSha256": "d" * 64}
        inventory = {"schemaVersion": "feelm-service-v1-b1-r4-venv-inventory/1",
                     "recordSetSha256": "e" * 64}
        controls = {"payloads": {"serverReceiptManifest": {"maintenanceReservations":
                    {"fit": reservation_pin}}, "hostRuntimeLock": runtime_lock},
                    "records": [runtime_pin]}
        gate = {"checkedAt": "2026-09-14T01:00:00Z",
                "maintenanceReservationId": reservation["reservationId"],
                "reservationEndsAt": reservation["endsAt"],
                "runtime": {"lock": runtime_pin, "interpreterSha256": interpreter_pin["sha256"],
                            "venvRecordSetSha256": "e" * 64,
                            "environment": dict(runner.HOST_RUNTIME_ENVIRONMENT),
                            "importFixtureStatus": "PASS"}}
        def load(path, **_kwargs):
            return reservation if path == reservation_path else inventory
        def record(path):
            return {str(reservation_path): reservation_pin, str(inventory_path): inventory_pin,
                    str(interpreter): interpreter_pin}[str(path)]
        with mock.patch.object(runner, "_reservation_path", return_value=reservation_path), \
             mock.patch.object(runner, "load_json", side_effect=load), \
             mock.patch.object(runner, "_absolute_record", side_effect=record):
            runner._validate_current_gate_bindings(paths, controls, gate, "fit")
            forged = json.loads(json.dumps(gate))
            forged["maintenanceReservationId"] = "22345678-1234-4234-8234-123456789abc"
            with self.assertRaisesRegex(ValueError, "not bound"):
                runner._validate_current_gate_bindings(paths, controls, forged, "fit")
            bad_controls = json.loads(json.dumps(controls))
            bad_controls["payloads"]["serverReceiptManifest"]["maintenanceReservations"]["fit"]["sha256"] = "f" * 64
            with self.assertRaisesRegex(ValueError, "pin drift"):
                runner._validate_current_gate_bindings(paths, bad_controls, gate, "fit")

    def test_failure_resource_preserves_all_samples_peak_last_and_events(self):
        samples = [{"observedAt": f"2026-09-14T00:00:{index:02d}Z", "source": "cgroup-v2",
                    "currentBytes": index, "peakBytes": index + 10, "cpuUsageUsec": index * 2,
                    "memoryEvents": {"oom": 0, "oom_kill": 0}, "oom": False, "oomKill": False}
                   for index in range(10)]
        host = {"pollIntervalSeconds": 2.0, "samples": samples, "peakBytes": 19,
                "lastBytes": 9, "events": {"oom": 0, "oom_kill": 0},
                "status": "OBSERVED", "peakSource": "cgroup-v2"}
        resource = runner._failure_resource({"hostCgroupObservation": host}, {})
        self.assertEqual(len(resource["samples"]), 10)
        self.assertEqual(resource["peakMemoryBytes"], 19)
        self.assertEqual(resource["lastMemoryBytes"], 9)
        self.assertEqual(resource["events"], host["events"])
        fallback = runner._failure_resource(
            {"evaluationResource": {}, "hostCgroupObservation": host}, {})
        self.assertEqual(fallback, resource)

    def test_owned_path_inode_replacement_is_never_cleaned(self):
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory) / "stage"
            displaced = Path(directory) / "displaced"
            stage.mkdir()
            owned = runner.capture_owned_path(stage)
            stage.rename(displaced)
            stage.mkdir()
            with self.assertRaisesRegex(ValueError, "identity drift"):
                runner.cleanup_owned_path(owned)
            self.assertTrue(stage.is_dir())

    def test_failure_schema_and_enums_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "top-level"):
            runner.validate_phase_failure_payload({}, "fit")
        self.assertEqual(runner._failure_classification(
            ValueError("x"), {"failureStage": "HOST_GATE", "failureKind": "RESOURCE"},
            timed_out=False, oom_killed=False, created=False, cleanup_complete=True),
            ("HOST_GATE", "RESOURCE"))


class StaticSafetyTests(unittest.TestCase):
    def test_production_has_no_assert_or_ambient_evaluator_import(self):
        source = (ROOT / "scripts/run_service_v1_b1_gbt_r4.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertFalse([node for node in ast.walk(tree) if isinstance(node, ast.Assert)])
        self.assertNotIn("importlib.import_module", source)
        self.assertIn('"-B", "-I"', source)

    def test_evaluation_gate_precedes_dependency_and_publication(self):
        source = ast.get_source_segment(
            (ROOT / "scripts/run_service_v1_b1_gbt_r4.py").read_text(encoding="utf-8"),
            next(node for node in ast.walk(ast.parse((ROOT / "scripts/run_service_v1_b1_gbt_r4.py").read_text(encoding="utf-8")))
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_run_evaluation_supervised_impl"))
        self.assertLess(source.index("verify_server_evaluation_gate("), source.index("_pin_absolute_records("))
        self.assertLess(source.index("_pin_absolute_records("), source.index("acquire_phase_publication("))

    def test_venv_closure_is_in_evaluation_dependency_builder(self):
        source = (ROOT / "scripts/run_service_v1_b1_gbt_r4.py").read_text(encoding="utf-8")
        self.assertIn('for key in ("requirementsLock", "wheelhouseManifest", "venvInventory")', source)
        self.assertIn('venv_root.rglob("*")', source)
        self.assertIn('"hard-link alias forbidden', source)


if __name__ == "__main__":
    unittest.main()
