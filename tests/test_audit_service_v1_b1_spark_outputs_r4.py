from __future__ import annotations

import ast
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

from scripts import audit_service_v1_b1_spark_outputs_r4 as audit

ROOT = Path(__file__).resolve().parents[1]
TEAM = ROOT.parent / "S15P21E106"


class ProfileAndInventoryTests(unittest.TestCase):
    def setUp(self):
        self.roots = audit.Roots(ROOT, TEAM)

    def test_reviewed_profile_is_exact_server_contract(self):
        profile, record = audit.audit_profile_contract(self.roots)
        self.assertEqual((record["bytes"], record["sha256"]), (6_516, audit.PROFILE_SHA256))
        self.assertEqual(profile["docker"]["cpus"], "5")
        self.assertEqual(profile["docker"]["memory"], "20g")
        self.assertEqual(profile["spark"]["master"], "local[5]")
        self.assertEqual(profile["spark"]["driverMemory"], "12g")
        self.assertEqual(profile["timeoutsSeconds"]["fit"], 28_800)

    def test_r4_bundle_inventories_include_server_evidence(self):
        self.assertEqual({"delivery-reference.json", "server-receipt-reference.json",
                          "host-gate-dry.json", "host-gate-full.json"},
                         set(audit.TARGETS["preflight"].values()) & {
                             "delivery-reference.json", "server-receipt-reference.json",
                             "host-gate-dry.json", "host-gate-full.json"})
        for phase in ("fit", "score"):
            self.assertTrue({"delivery-reference.json", "server-receipt-reference.json",
                             "host-gate.json"} <= set(audit.TARGETS[phase].values()))

    def test_execution_source_names_use_only_r4_runner_test(self):
        self.assertIn("execution/test_service_v1_b1_gbt_runner_r4.py", audit.EXECUTION_NAMES)
        self.assertNotIn("execution/test_service_v1_b1_gbt_runner_r3.py", audit.EXECUTION_NAMES)

    def test_r3_ancestry_inventory_and_pins_are_fixed(self):
        self.assertEqual(len(audit.R3_IMPLEMENTATION_PINS), 10)
        self.assertEqual(len(audit.R3_PREFLIGHT_INVENTORY), 8)
        self.assertEqual(audit.R3_FIT_FAILURE_SHA256,
                         "75ad1999a8e24d723c56e0b8ecd4f96a573c2086b86daf675d68ebf889940276")


class StrictInputTests(unittest.TestCase):
    def test_duplicate_nonfinite_and_crlf_json_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "x.json"
            path.write_bytes(b'{"a":1,"a":2}\n')
            with self.assertRaisesRegex(audit.AuditError, "duplicate"):
                audit.json_object(path)
            path.write_bytes(b'{"a":NaN}\n')
            with self.assertRaisesRegex(audit.AuditError, "nonfinite"):
                audit.json_object(path)
            path.write_bytes(b'{"a":1}\r\n')
            with self.assertRaisesRegex(audit.AuditError, "LF"):
                audit.json_object(path)

    def test_hard_link_alias_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "a"
            alias = Path(directory) / "b"
            path.write_bytes(b"x")
            try:
                os.link(path, alias)
            except OSError:
                self.skipTest("hard links unavailable")
            with self.assertRaisesRegex(audit.AuditError, "hard-link"):
                audit.pin(path)

    def test_record_digest_is_canonical_and_rejects_duplicates(self):
        a = {"path": "a", "bytes": 1, "sha256": "a" * 64}
        b = {"path": "b", "bytes": 2, "sha256": "b" * 64}
        self.assertEqual(audit.set_digest([a, b]), audit.set_digest([b, a]))
        with self.assertRaisesRegex(audit.AuditError, "duplicate"):
            audit.set_digest([a, a])

    def test_relative_paths_reject_traversal_and_evaluation_labels(self):
        for value in ("../x", "/x", "a\\b", "labels.parquet", "x/evaluation-seal.json"):
            with self.subTest(value=value), self.assertRaises(audit.AuditError):
                audit.relative_name(value)


class ReviewContractTests(unittest.TestCase):
    def review(self, phase="score"):
        checks = {"resource": {"stages": 1, "peakBytes": 1, "cleanupConfirmed": True,
                  "labelMountAbsent": True, "measurementIndependentlyObserved": False,
                  "measurementCheckedAgainstPinnedWorkerLog": True},
                  "recovery": {}, "parentChain": "PASS", "predictions": {}, "server": {}}
        return {"schemaVersion": "feelm-service-v1-b1-r4-spark-result-review/1",
                "runId": audit.RUN_ID, "profileId": audit.PROFILE_ID, "phase": phase,
                "status": "PASS", "createdAt": "2026-09-14T00:00:00Z", "target": {},
                "reviewer": {"kind": "INDEPENDENT_SESSION", "sessionId": "/root/reviewer",
                             "host": "host", "processId": 1},
                "dependencyFingerprint": "a" * 64, "checks": checks,
                "decision": {"sparkIntegrity": "PASS", "fitEligible": False,
                             "scoreEligible": False, "evaluationSelectionEligible": True,
                             "deploymentAuthorized": False}}

    def test_r4_review_draft_exact_shape_and_decision(self):
        review = self.review()
        audit.audit_review_shape(review, "score", {"bytes": 1, "sha256": "a" * 64},
                                 publication_required=False)
        bad = json.loads(json.dumps(review))
        bad["decision"]["deploymentAuthorized"] = True
        with self.assertRaisesRegex(audit.AuditError, "decision"):
            audit.audit_review_shape(bad, "score", {"bytes": 1, "sha256": "a" * 64},
                                     publication_required=False)

    def test_reviewer_identity_is_required(self):
        review = self.review()
        review["reviewer"].pop("sessionId")
        with self.assertRaisesRegex(audit.AuditError, "reviewer"):
            audit.audit_review_shape(review, "score", {"bytes": 1, "sha256": "a" * 64},
                                     publication_required=False)

    def test_review_namespace_progression(self):
        self.assertEqual(len(audit.expected_namespace_before_review("preflight")), 7)
        self.assertEqual(len(audit.expected_namespace_before_review("fit")), 9)
        self.assertEqual(len(audit.expected_namespace_before_review("score")), 11)

    def test_shared_publisher_gets_exact_namespace_and_dependency(self):
        roots = audit.Roots(ROOT, TEAM)
        review = self.review("preflight")
        review["checks"] = {"resource": {"stages": 2, "peakBytes": 1, "cleanupConfirmed": True,
             "labelMountAbsent": True, "measurementIndependentlyObserved": False,
             "measurementCheckedAgainstPinnedWorkerLog": True}, "recovery": {}, "identity": {}, "server": {}}
        review["decision"] = {"sparkIntegrity": "PASS", "fitEligible": True, "scoreEligible": False,
                              "evaluationSelectionEligible": False, "deploymentAuthorized": False}
        fingerprint = review["dependencyFingerprint"]
        with tempfile.TemporaryDirectory() as directory:
            final = Path(directory) / "review.json"
            temp = Path(directory) / ".review.json.tmp-token"
            evidence = {"schemaVersion": "feelm-service-v1-b1-r4-publication-evidence/1"}
            lease = types.SimpleNamespace(temp_path=temp, publication_evidence=evidence)
            calls = {}
            def acquire(role, phase, final_path, failure_path, expected_namespace):
                calls["acquire"] = (role, phase, final_path, failure_path, expected_namespace)
                return lease
            def publish(lease_arg, staging, digest, callback):
                self.assertEqual(callback(), fingerprint)
                os.rename(staging, final)
                return types.SimpleNamespace(path=final, bytes=final.stat().st_size,
                                             sha256=audit.digest(final))
            fake = types.SimpleNamespace(acquire_publication=acquire, publish_success=publish,
                                         release_verified_claim=lambda *args: calls.setdefault("released", True))
            with mock.patch.object(audit.Roots, "review", return_value=final), \
                 mock.patch.object(audit, "audit_review_shape"), \
                 mock.patch.object(audit, "target_records", return_value=review["target"]), \
                 mock.patch.object(audit, "dependency_fingerprint", return_value=fingerprint), \
                 mock.patch.object(audit, "expected_namespace_before_review", return_value=("a", "b")), \
                 mock.patch.object(audit, "verify_review_namespace", return_value=["a", "b"]), \
                 mock.patch.object(audit, "load_publication_module", return_value=fake):
                result = audit.publish_review(roots, "preflight", review)
            self.assertEqual(result, final)
            self.assertEqual(calls["acquire"][0:2], ("REVIEWER", "preflight-review"))
            self.assertEqual(calls["acquire"][4], {"children": ["a", "b"],
                                                    "dependencyFingerprint": fingerprint})
            self.assertTrue(calls["released"])


class StaticIndependenceTests(unittest.TestCase):
    def test_production_is_assert_free_and_does_not_import_runner(self):
        source = (ROOT / "scripts/audit_service_v1_b1_spark_outputs_r4.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertFalse([node for node in ast.walk(tree) if isinstance(node, ast.Assert)])
        self.assertNotIn("run_service_v1_b1_gbt_r4 import", source)
        self.assertNotIn("from scripts import run_service", source)

    def test_auditor_contains_server_limits_and_shared_publication(self):
        source = (ROOT / "scripts/audit_service_v1_b1_spark_outputs_r4.py").read_text(encoding="utf-8")
        for value in ('"--cpus", "5"', '"--memory", "20g"', '"--master", "local[5]"',
                      '"--driver-memory", "12g"', "acquire_publication", "publish_success"):
            self.assertIn(value, source)

    def test_cli_requires_independent_session_id(self):
        parser = audit.parser()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["--standalone-root", str(ROOT), "--team-repo", str(TEAM), "preflight",
                               "--expected-outer-runner-sha256", "a" * 64,
                               "--expected-spark-worker-sha256", "b" * 64])

    def test_review_failure_schema_rejects_unknown_fields(self):
        with self.assertRaisesRegex(audit.AuditError, "top-level"):
            audit.validate_review_failure_payload({"unknown": True}, "score")

    def test_dynamic_gate_uses_shared_filesystem_literal(self):
        source = (ROOT / "scripts/audit_service_v1_b1_spark_outputs_r4.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(node for node in ast.walk(tree)
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "_audit_recorded_dynamic_host_gate")
        text = ast.get_source_segment(source, function)
        self.assertIn('"ext2/ext3"', text)
        self.assertNotIn('{"ext4", "xfs"}', text)

    def test_acquire_failure_is_not_retried_inside_review_publication(self):
        roots = audit.Roots(ROOT, TEAM)
        review = ReviewContractTests().review("preflight")
        review["checks"] = {"resource": {"stages": 2, "peakBytes": 1, "cleanupConfirmed": True,
             "labelMountAbsent": True, "measurementIndependentlyObserved": False,
             "measurementCheckedAgainstPinnedWorkerLog": True}, "recovery": {}, "identity": {}, "server": {}}
        review["decision"] = {"sparkIntegrity": "PASS", "fitEligible": True, "scoreEligible": False,
                              "evaluationSelectionEligible": False, "deploymentAuthorized": False}
        acquire = mock.Mock(side_effect=RuntimeError("uncertain acquire"))
        fake = types.SimpleNamespace(acquire_publication=acquire)
        with mock.patch.object(audit, "audit_review_shape"), \
             mock.patch.object(audit, "target_records", return_value=review["target"]), \
             mock.patch.object(audit, "dependency_fingerprint", return_value="a" * 64), \
             mock.patch.object(audit, "verify_review_namespace", return_value=["a"]), \
             mock.patch.object(audit, "load_publication_module", return_value=fake):
            with self.assertRaises(audit.ReviewPublicationBlocked):
                audit.publish_review(roots, "preflight", review)
        self.assertEqual(acquire.call_count, 1)


if __name__ == "__main__":
    unittest.main()

