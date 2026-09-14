from __future__ import annotations
import ast
import copy
from dataclasses import asdict
import json
import io
import os
import sys
from types import SimpleNamespace
from pathlib import Path
import tempfile
import unittest
from unittest import mock

TESTS_ROOT = Path(__file__).resolve().parent
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))

from test_evaluate_service_v1_b1_r4 import ROOT, Fixture, dump, ev, load, resource

audit = load("audit_service_v1_b1_evaluation_outputs_r4", ROOT / "scripts/audit_service_v1_b1_evaluation_outputs_r4.py")


def seal(fx, bundle, result):
    dump(bundle / "evaluator-resource.json", resource())
    manifest = {"schemaVersion": "feelm-service-v1-b1-r4-evaluation/1", "runId": ev.RUN_ID,
        "profileId": ev.PROFILE_ID, "phase": "SELECTION", "status": "B1_SELECTION_COMPLETE_AUDIT_PENDING",
        "files": {name: ev.pin(bundle / name) for name in sorted(ev._bundle_inventory(bundle))
                  if name != "manifest.json"}, "evaluator": ev.path_pin(fx.evaluator),
        "truthCensus": result["truthCensus"], "evaluationSourceSetSha256": result["dependencyFingerprint"],
        "publication": {"schemaVersion": "feelm-service-v1-b1-r4-publication-evidence/1",
            "mode": "LINUX_RENAME_NOREPLACE", "role": "PRODUCER", "finalPath": str(bundle),
            "failurePath": str(bundle.with_name(bundle.name + "-failure.json")),
            "claimPath": str(bundle.with_name("." + bundle.name + ".claim")),
            "tempPath": str(bundle.with_name("." + bundle.name + ".tmp-" + "a" * 32)),
            "token": "a" * 32, "claimStDev": 1, "claimStIno": 1, "filesystemType": "ext2/ext3",
            "publisher": ev.path_pin(ROOT / "scripts/service_v1_b1_r4_publication.py"),
            "dependencyFingerprintAtAcquire": result["dependencyFingerprint"],
            "dependencyFingerprintBeforeRename": result["dependencyFingerprint"], "renameNoReplaceProbe": True,
            "requiredPostconditions": {k: True for k in ("publishedBytesRehashRequired", "fileAndParentFsyncRequired",
                "dependencyFingerprintStableRequired", "claimIdentityMatchRequired", "claimRemovalRequired")}},
        "readyForService": False, "confirmationAuthorized": False,
        "deploymentAuthorized": False, "requiredSelectionGate": result["requiredGate"]}
    dump(bundle / "manifest.json", manifest)


def seed_review_namespace(parent, phase, *, skip=None):
    parent.mkdir(parents=True, exist_ok=True)
    for name in audit.review_predecessor_children(phase):
        child = parent / name
        if child == skip:
            continue
        if name.endswith(".json"):
            dump(child, {"synthetic": True})
        else:
            child.mkdir(exist_ok=True)


class MemoryPublication:
    """Local test double; real Linux atomicity is the shared primitive's suite."""
    def __init__(self, fail_success=False):
        self.fail_success = fail_success
        self.acquired = []
        self.released = []
        self.failures = []

    def acquire_publication(self, role, phase, final, failure, expected):
        claim = final.with_name("." + final.name + ".claim")
        temp = final.with_name("." + final.name + ".tmp-" + "b" * 32)
        if claim.exists() or final.exists() or failure.exists():
            raise audit.ContractError("publication collision")
        dump(claim, {"token": "b" * 32, "role": role, "finalPath": str(final), "pid": os.getpid()})
        metadata = claim.stat()
        evidence = {"claimStDev": metadata.st_dev, "claimStIno": metadata.st_ino, "token": "b" * 32,
                    "dependencyFingerprintAtAcquire": expected["dependencyFingerprint"]}
        lease = SimpleNamespace(final_path=final, failure_path=failure, claim_path=claim, temp_path=temp,
                                publication_evidence=evidence, owner_pid=os.getpid())
        self.acquired.append(lease)
        return lease

    def publish_success(self, lease, stage, fingerprint, rehash):
        if self.fail_success:
            raise OSError("synthetic failure before rename")
        if rehash() != fingerprint:
            raise audit.ContractError("dependency drift")
        stage.replace(lease.final_path)
        return ev.path_pin(lease.final_path)

    def publish_handled_failure(self, lease, payload, fingerprint, rehash):
        if lease.temp_path.exists() or lease.final_path.exists() or rehash() != fingerprint:
            raise audit.ContractError("failure namespace/dependency drift")
        dump(lease.failure_path, payload)
        self.failures.append(payload)
        return ev.path_pin(lease.failure_path)

    def release_verified_claim(self, lease, published, post):
        if post != lease.publication_evidence["dependencyFingerprintAtAcquire"]:
            raise audit.ContractError("post dependency drift")
        self.released.append(lease)
        lease.claim_path.unlink()


class AuditEvaluationR4Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fx = Fixture(Path(self.temp.name))
        self.bundle = self.fx.old.root / "canonical-r4-evidence" / (ev.RUN_ID + "-selection")
        seed_review_namespace(self.bundle.parent, "selection", skip=self.bundle)
        self.result = self.fx.prepare(self.bundle)
        seal(self.fx, self.bundle, self.result)
        self.spec = audit.AuditSpec(**asdict(self.fx.spec), canonical=False)

    def run_audit(self):
        with mock.patch.object(audit, "SCRIPT", self.fx.auditor):
            return audit.audit_phase(self.fx.paths, self.bundle, "selection",
                expected_manifest_sha256=ev.sha256_file(self.bundle / "manifest.json"),
                expected_evaluator_sha256=ev.sha256_file(self.fx.evaluator),
                control_state=self.fx.state(), expected_dependency_records=[],
                _spec=self.spec, _source_pins=self.fx.pins)

    def rehash_output(self, name):
        manifest = ev.read_json(self.bundle / "manifest.json")
        manifest["files"][name] = ev.pin(self.bundle / name)
        dump(self.bundle / "manifest.json", manifest)

    def test_recorded_host_gate_uses_target_start_and_rejects_stale_or_wrong_limits(self):
        observed = resource()
        limits = observed["limits"]
        gate = {"schemaVersion": "feelm-service-v1-b1-r4-dynamic-host-gate/2", "status": "PASS",
            "runId": ev.RUN_ID, "profileId": ev.PROFILE_ID, "phase": "calibrate-select",
            "checkedAt": "2026-09-14T09:59:30Z", "maintenanceReservationId": "fixture",
            "reservationEndsAt": "2026-09-14T15:00:00Z", "maintenanceWindowRemainingSeconds": 18030,
            "requiredWindowSeconds": 16200, "architecture": "x86_64", "logicalCpu": 8,
            "memory": {"memTotalBytes": 34_000_000_000, "memAvailableBytes": 30_000_000_000,
                       "source": "/proc/meminfo-kib-times-1024"},
            "filesystem": {"scratchFreeBytes": 100_000_000_000, "outputFreeBytes": 30_000_000_000,
                "scratchFreeInodes": 100000, "outputFreeInodes": 100000, "scratchDevice": 1,
                "outputDevice": 1, "stageDevice": 1, "finalDevice": 1, "filesystemType": "xfs",
                "renameNoReplaceProbe": True},
            "docker": {"serverVersion": "fixture", "daemonId": "fixture",
                "imageId": "sha256:de611d62db982bb833b0737a7ddd72a7cd49ab1a7dc9128669cdabae39f46df8",
                "imageUnpackedSizeBytes": 936463936, "imageInspectSha256": "a" * 64,
                "runningB1Containers": [], "restartPolicy": "no"},
            "cgroup": {"version": "v2", "controllers": ["cpu", "memory", "pids"],
                "probeContainerId": "fixture", "probeInitPid": 22, "cgroupPath": "/fixture",
                "readableFiles": ["memory.current", "memory.peak", "memory.events", "cpu.stat", "pids.current"],
                "probeStatus": "PASS"},
            "supervisor": {"kind": "systemd-transient-service", "phaseUnitName": observed["unitName"],
                "probeUnitName": observed["unitName"][:-8] + "-probe.service", "phaseUnitExistsBefore": False,
                "probeStartCommand": ["/usr/bin/sleep", "2"], "probeControlGroup": "/fixture",
                "probeMainPid": 22, "probePidTree": observed["initialPidTree"],
                **{k: limits[k] for k in ("cpuQuotaPercent", "memoryMaxBytes", "memorySwapMaxBytes", "tasksMax",
                                         "killMode", "timeoutStopSeconds")}, "status": "PASS"},
            "competingProcessCensus": [], "dockerRestartScheduled": False, "hostRebootScheduled": False,
            "namespaceCensus": {k: [] for k in ("completed", "failures", "reviews", "claims", "temps",
                                                "scratches", "containers", "processes")},
            "runtime": {"lock": {"path": "/fixture/runtime-lock.json", "bytes": 10, "sha256": "a" * 64},
                "interpreterSha256": "a" * 64, "venvRecordSetSha256": "a" * 64,
                "environment": {"pythonPath": "UNSET", "pythonNoUserSite": True, "isolatedMode": True,
                    "pythonDontWriteBytecode": True, "locale": "C.UTF-8", "timezone": "UTC"},
                "importFixtureStatus": "PASS"},
            "probeCommands": [["/usr/bin/sleep", "2"]], "probeSetSha256": "a" * 64, "unknownFields": []}
        audit.verify_recorded_host_gate(gate, phase="calibrate-select", resource=observed)
        wrong = copy.deepcopy(gate)
        wrong["supervisor"]["memoryMaxBytes"] += 1
        with self.assertRaises(audit.ContractError):
            audit.verify_recorded_host_gate(wrong, phase="calibrate-select", resource=observed)
        wrong = copy.deepcopy(gate)
        wrong["checkedAt"] = "2026-09-14T09:54:00Z"
        with self.assertRaises(audit.ContractError):
            audit.verify_recorded_host_gate(wrong, phase="calibrate-select", resource=observed)

    def invoke_publishing(self, publisher):
        with mock.patch.object(audit, "load_publication_module", return_value=publisher), \
                mock.patch.object(audit, "SCRIPT", self.fx.auditor):
            return audit.publish_review(self.fx.paths, self.bundle, "selection",
                expected_manifest_sha256=ev.sha256_file(self.bundle / "manifest.json"),
                expected_evaluator_sha256=ev.sha256_file(self.fx.evaluator),
                control_state=self.fx.state(), expected_dependency_records=[],
                expected_namespace=audit.review_predecessor_children("selection"))

    def check_failure(self, publisher):
        self.assertEqual(len(publisher.acquired), 1)
        self.assertEqual(len(publisher.released), 1)
        self.assertEqual(len(publisher.failures), 1)
        payload = publisher.failures[0]
        self.assertEqual(set(payload), {"schemaVersion", "status", "runId", "profileId", "phase", "createdAt",
            "auditor", "target", "error", "dependencyFingerprintBefore", "dependencyFingerprintAfter",
            "namespaceCensus", "passReviewPublished", "deploymentAuthorized", "publication"})
        self.assertEqual(payload["schemaVersion"], "feelm-service-v1-b1-r4-review-failure/1")
        self.assertEqual(set(payload["auditor"]), {"kind", "sessionId", "host", "processId"})
        self.assertEqual(set(payload["error"]), {"type", "message", "traceback"})
        self.assertEqual(payload["dependencyFingerprintBefore"], payload["dependencyFingerprintAfter"])
        self.assertFalse(payload["passReviewPublished"])
        self.assertFalse(payload["deploymentAuthorized"])
        self.assertFalse(publisher.acquired[0].claim_path.exists())
        self.assertFalse(publisher.acquired[0].temp_path.exists())
        self.assertTrue(publisher.acquired[0].failure_path.exists())
        return payload

    def test_pre_acquire_audit_failure_publishes_immutable_review_failure(self):
        publisher = MemoryPublication()
        with mock.patch.object(audit, "audit_phase", side_effect=audit.ContractError("synthetic audit failure")):
            with self.assertRaises(audit.ReviewFailurePublished):
                self.invoke_publishing(publisher)
        payload = self.check_failure(publisher)
        self.assertIn("synthetic audit failure", payload["error"]["traceback"])

    def test_acquired_audit_failure_reuses_same_lease(self):
        publisher = MemoryPublication()
        with mock.patch.object(audit, "audit_phase", side_effect=[{"createdAt": "fixed"}, audit.ContractError("second audit")]):
            with self.assertRaises(audit.ReviewFailurePublished):
                self.invoke_publishing(publisher)
        self.check_failure(publisher)

    def test_publication_failure_cleans_only_own_verified_temp_then_publishes_failure(self):
        publisher = MemoryPublication(fail_success=True)
        with mock.patch.object(audit, "audit_phase", side_effect=[{"createdAt": "fixed"}, {"createdAt": "fixed"}]):
            with self.assertRaises(audit.ReviewFailurePublished):
                self.invoke_publishing(publisher)
        payload = self.check_failure(publisher)
        self.assertEqual(payload["error"]["type"], "OSError")

    def test_dependency_drift_keeps_claim_and_does_not_publish_false_failure(self):
        publisher = MemoryPublication()
        def changed(*args, **kwargs):
            (self.bundle / "run.log").write_text("changed", encoding="utf-8")
            raise audit.ContractError("changed dependency")
        count = [0]
        def audits(*args, **kwargs):
            count[0] += 1
            if count[0] == 1:
                return {"createdAt": "fixed"}
            return changed()
        with mock.patch.object(audit, "audit_phase", side_effect=audits):
            with self.assertRaisesRegex(audit.ContractError, "changed before failure"):
                self.invoke_publishing(publisher)
        self.assertEqual(len(publisher.released), 0)
        self.assertEqual(len(publisher.failures), 0)
        self.assertTrue(publisher.acquired[0].claim_path.exists())

    def test_cli_denied_gate_publishes_failure_without_reading_evaluation_inputs(self):
        publisher = MemoryPublication()
        cli_root = self.fx.old.root / "isolated-cli-root"
        pub_source = cli_root / "scripts/service_v1_b1_r4_publication.py"
        pub_source.parent.mkdir(parents=True)
        pub_source.write_text("# synthetic publisher source", encoding="utf-8")
        output_root = cli_root / "outputs/recommendation-evidence/service-v1-pretraining-20260913"
        seed_review_namespace(output_root, "selection")
        request = {"controls": {k: str(v) for k, v in audit.canonical_control_paths(cli_root).items()},
                   "expectedSha256": {k: "a" * 64 for k in ev.CONTROL_NAMES},
                   "dependencyRecords": [], "expectedNamespace": list(audit.review_predecessor_children("selection"))}
        stderr = io.StringIO()
        with mock.patch.object(audit, "ROOT", cli_root), mock.patch.object(audit, "SCRIPT", self.fx.auditor), \
                mock.patch.object(audit, "verify_score_review_gate", side_effect=audit.ContractError("score denied")), \
                mock.patch.object(audit, "load_publication_module", return_value=publisher), \
                mock.patch.object(audit, "review_candidates", wraps=audit.review_candidates) as candidates, \
                mock.patch.object(sys, "stdin", io.StringIO(json.dumps(request))), mock.patch.object(sys, "stderr", stderr):
            with self.assertRaises(SystemExit):
                audit.main(["selection", "--expected-manifest-sha256", "a" * 64,
                            "--expected-evaluator-sha256", "b" * 64, "--publish-review"])
        self.assertFalse(candidates.call_args.kwargs["allow_bundle"])
        self.assertIsNone(json.loads(stderr.getvalue())["failurePublicationError"])
        self.check_failure(publisher)
        self.assertIsNotNone(json.loads(stderr.getvalue())["reviewFailure"])

    def test_frozen_predecessor_censuses_and_confirmation_review(self):
        selected = audit.review_predecessor_children("selection")
        confirmed = audit.review_predecessor_children("confirmation")
        self.assertEqual(len(selected), 13)
        self.assertEqual(len(confirmed), 15)
        self.assertEqual(set(confirmed) - set(selected), {
            ev.RUN_ID + "-selection-result-review.json", ev.RUN_ID + "-confirmation"})
        self.assertEqual(audit.verify_review_predecessor_namespace(self.bundle, "selection", selected), list(selected))
        other = self.fx.old.root / "confirmation-canonical" / (ev.RUN_ID + "-confirmation")
        seed_review_namespace(other.parent, "confirmation")
        self.assertEqual(audit.verify_review_predecessor_namespace(other, "confirmation", confirmed), list(confirmed))
        with self.assertRaises(audit.ContractError):
            audit.verify_review_predecessor_namespace(other, "confirmation", selected)

    def test_extra_regular_directory_hidden_and_stale_failure_rejected_before_claim(self):
        additions = [("unexpected.txt", False), ("unrelated-dir", True),
                     (ev.RUN_ID + "-unexpected.json", False),
                     (".unrelated-hidden", False),
                     ("." + ev.RUN_ID + "-selection-result-review.json.claim", False),
                     ("." + ev.RUN_ID + "-selection-result-review.json.tmp-old", False),
                     (ev.RUN_ID + "-fit-failure.json", False),
                     (ev.RUN_ID + "-selection-result-review-failure.json", False)]
        for name, directory in additions:
            with self.subTest(name=name):
                extra = self.bundle.parent / name
                if directory:
                    extra.mkdir()
                else:
                    extra.write_text("injected", encoding="utf-8")
                publisher = MemoryPublication()
                try:
                    with self.assertRaises(audit.ContractError):
                        self.invoke_publishing(publisher)
                    self.assertEqual(publisher.acquired, [])
                    self.assertEqual(publisher.failures, [])
                    self.assertTrue(extra.exists())
                finally:
                    extra.rmdir() if directory else extra.unlink()

    def test_caller_cannot_authorize_extra_by_expanding_expected_namespace(self):
        extra = self.bundle.parent / "caller-approved.txt"
        extra.write_text("injected", encoding="utf-8")
        supplied = sorted(path.name for path in self.bundle.parent.iterdir())
        with self.assertRaisesRegex(audit.ContractError, "caller namespace differs"):
            audit.verify_review_predecessor_namespace(self.bundle, "selection", supplied)
        publisher = MemoryPublication()
        with self.assertRaises(audit.ContractError):
            audit.publish_review_failure(self.bundle, "selection", audit.ContractError("audit denied"),
                candidates=[self.fx.auditor], expected_namespace=supplied, publisher=publisher)
        self.assertEqual(publisher.acquired, [])

    def test_missing_predecessor_and_wrong_type_block_failure_claim(self):
        member = self.bundle.parent / (ev.RUN_ID + "-design-result-review.json")
        raw = member.read_bytes()
        member.unlink()
        with self.assertRaises(audit.ContractError):
            audit.verify_review_predecessor_namespace(self.bundle, "selection")
        member.mkdir()
        with self.assertRaises(audit.ContractError):
            audit.verify_review_predecessor_namespace(self.bundle, "selection")
        member.rmdir()
        member.write_bytes(raw)

    def test_acquired_foreign_hidden_entry_preserves_owned_claim_and_temp(self):
        publisher = MemoryPublication(fail_success=True)
        old = publisher.publish_success
        foreign = self.bundle.parent / ".foreign-injection"
        def injected(*args):
            foreign.write_text("ambiguous", encoding="utf-8")
            return old(*args)
        publisher.publish_success = injected
        with mock.patch.object(audit, "audit_phase", side_effect=[{"createdAt": "fixed"}, {"createdAt": "fixed"}]):
            with self.assertRaises(audit.ReviewPublicationBlocked):
                self.invoke_publishing(publisher)
        self.assertTrue(publisher.acquired[0].claim_path.exists())
        self.assertTrue(publisher.acquired[0].temp_path.exists())
        self.assertTrue(foreign.exists())
        self.assertEqual(publisher.released, [])
        self.assertEqual(publisher.failures, [])

    def test_independent_exact_recomputation(self):
        review = self.run_audit()
        self.assertEqual(review["status"], "PASS")
        self.assertTrue(review["reviewer"]["independentFromEvaluator"])
        self.assertFalse(review["readyForService"])

    def test_formula_matches_but_uses_separate_source(self):
        truth = self.fx.truth()
        expected = ev.compute_phase_outputs("selection", truth, self.fx.spec)
        got = audit.compute_expected_outputs("selection", truth, self.spec)
        audit.json_equal(list(got), list(expected), "independent numeric parity")
        src = (ROOT / "scripts/audit_service_v1_b1_evaluation_outputs_r4.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        imported = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
        self.assertFalse(any("evaluate_service_v1" in ast.unparse(n) for n in imported))

    def test_numeric_tampering_rejected_after_manifest_rehash(self):
        metrics = ev.read_json(self.bundle / "metrics.json")
        metrics["models"]["B1"]["ALL"]["userMacroMSE"] += .01
        dump(self.bundle / "metrics.json", metrics)
        self.rehash_output("metrics.json")
        with self.assertRaisesRegex(audit.ContractError, "numeric drift"):
            self.run_audit()

    def test_bootstrap_tampering_rejected_after_manifest_rehash(self):
        item = ev.read_json(self.bundle / "bootstrap-summary.json")
        item["userBlock"]["ALL"]["mseDelta"]["ciHigh"] += .01
        dump(self.bundle / "bootstrap-summary.json", item)
        self.rehash_output("bootstrap-summary.json")
        with self.assertRaises(audit.ContractError): self.run_audit()

    def test_gate_tampering_rejected(self):
        item = ev.read_json(self.bundle / "gates.json")
        item["requiredGate"] = "FAIL" if item["requiredGate"] != "FAIL" else "PASS"
        dump(self.bundle / "gates.json", item)
        self.rehash_output("gates.json")
        with self.assertRaises(audit.ContractError): self.run_audit()

    def test_extra_output_and_missing_output_rejected(self):
        extra = self.bundle / "unexpected.json"
        extra.write_text("{}")
        with self.assertRaisesRegex(audit.ContractError, "exact evaluation bundle inventory"):
            self.run_audit()
        extra.unlink()
        (self.bundle / "role-membership.csv").unlink()
        with self.assertRaisesRegex(audit.ContractError, "exact evaluation bundle inventory"):
            self.run_audit()

    def test_extra_input_dependency_cannot_be_declared_and_rehashed(self):
        p = self.fx.old.root / "extra.txt"; p.write_text("extra")
        lock = ev.read_json(self.bundle / "evaluation-input-lock.json")
        lock["dependencies"].append(ev.path_pin(p))
        lock["evaluationSourceSetSha256"] = ev.canonical_record_set_digest(lock["dependencies"])
        dump(self.bundle / "evaluation-input-lock.json", lock)
        self.rehash_output("evaluation-input-lock.json")
        manifest = ev.read_json(self.bundle / "manifest.json")
        manifest["evaluationSourceSetSha256"] = lock["evaluationSourceSetSha256"]
        dump(self.bundle / "manifest.json", manifest)
        with self.assertRaisesRegex(audit.ContractError, "exact dependency closure"):
            self.run_audit()

    def test_role_membership_bytes_tampering_rejected(self):
        (self.bundle / "role-membership.csv").write_text("uid,role\n11,CONFIRMATION\n", encoding="utf-8")
        self.rehash_output("role-membership.csv")
        with self.assertRaisesRegex(audit.ContractError, "role membership"):
            self.run_audit()

    def test_resource_unknown_or_monitor_failure_blocks_rehashed_output(self):
        value = resource(); value["cleanup"]["monitorExitCode"] = 1
        dump(self.bundle / "evaluator-resource.json", value)
        self.rehash_output("evaluator-resource.json")
        with self.assertRaisesRegex(audit.ContractError, "cleanup incomplete"):
            self.run_audit()

    def test_evaluator_source_mutation_blocks(self):
        self.fx.evaluator.write_text(self.fx.evaluator.read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8")
        with self.assertRaisesRegex(audit.ContractError, "bytes/SHA drift"):
            self.run_audit()

    def test_incomplete_publication_and_claim_block_consumption(self):
        claim = self.bundle.with_name("." + self.bundle.name + ".claim")
        claim.write_text("crashed owner")
        with self.assertRaisesRegex(audit.ContractError, "unfinished claim"):
            self.run_audit()
        claim.unlink()
        manifest = ev.read_json(self.bundle / "manifest.json")
        manifest["publication"]["requiredPostconditions"]["claimRemovalRequired"] = False
        dump(self.bundle / "manifest.json", manifest)
        with self.assertRaisesRegex(audit.ContractError, "required postconditions"):
            self.run_audit()


if __name__ == "__main__": unittest.main()
