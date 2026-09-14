from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import queue
import sys
import unittest
from unittest import mock


REPO = Path(__file__).resolve().parents[1]


def load_module(name: str, relative: str):
    path = REPO / relative
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


fixture_module = load_module(
    "delivery_builder_fixture", "tests/test_build_service_v1_b1_r4_server_delivery.py"
)
auditor = load_module("delivery_auditor_under_test", "scripts/audit_service_v1_b1_r4_server_delivery.py")


def write_json(path: Path, value: object) -> None:
    path.write_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode() + b"\n")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def convert_contract(contract) -> object:
    sources = tuple(
        auditor.AuditSource(
            item.group, item.logical_path, item.source_root, item.source_relative_path,
            item.kind, item.virtual_bytes, item.expected_bytes, item.expected_sha256,
        )
        for item in contract.specs
    )
    return auditor.AuditContract(
        sources=sources,
        semantic_facts=dict(contract.semantic_facts),
        producer_logical_path=contract.producer_logical_path,
        implementation_review_logical_path=contract.implementation_review_logical_path,
    )


def review_worker(start, result, roots, manifest_path, manifest_sha, implementation_path,
                  implementation_sha, review_path, contract, index) -> None:
    start.wait()
    try:
        pin, _ = auditor.audit_and_publish(
            roots, manifest_path, manifest_sha, implementation_path, implementation_sha,
            review_path, auditor.DELIVERY_REVIEW_COMPLETED_CHILDREN,
            f"race-{index}", contract,
        )
        result.put(("PASS", pin.sha256))
    except BaseException as error:
        result.put(("BLOCK", type(error).__name__, str(error)))


class DeliveryAuditorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = fixture_module.DeliveryFixture()
        self.contract = convert_contract(self.fixture.contract)
        manifest, _ = self.fixture.compose()
        write_json(self.fixture.manifest_path, manifest)
        write_json(
            self.fixture.roots.wsl_evidence / auditor.DESIGN_REVIEW_NAME,
            {"status": "PASS", "decision": {"implementationEligible": True}},
        )
        self.manifest_sha = sha(self.fixture.manifest_path)

    def tearDown(self) -> None:
        self.fixture.close()

    def audit(self):
        return auditor.audit_manifest(
            auditor.Roots(*self.fixture.roots.mapping.values()),
            self.fixture.manifest_path, sha(self.fixture.manifest_path),
            self.fixture.implementation_path, sha(self.fixture.implementation_path), self.contract,
        )

    def test_independent_rehash_accepts_exact_manifest_and_returns_closed_review_target(self) -> None:
        target, fingerprint = self.audit()
        self.assertEqual(target["manifest"]["sha256"], self.manifest_sha)
        self.assertEqual(target["deliverySetSha256"],
                         json.loads(self.fixture.manifest_path.read_text())["deliverySetSha256"])
        self.assertRegex(fingerprint, r"^[0-9a-f]{64}$")
        review = auditor.compose_review(target, fingerprint, self.fixture.publication_evidence(), "independent")
        self.assertEqual(review["decision"], auditor.DECISION)
        self.assertFalse(review["decision"]["deploymentAuthorized"])

    def test_missing_extra_moved_digest_group_overlap_ancestry_and_authorization_are_rejected(self) -> None:
        original = json.loads(self.fixture.manifest_path.read_text())

        mutations = {}
        value = copy.deepcopy(original)
        value["modelInputs"].pop()
        mutations["missing"] = value
        value = copy.deepcopy(original)
        value["modelInputs"].append(dict(value["modelInputs"][0], logicalPath="contract/extra.json"))
        mutations["extra"] = value
        value = copy.deepcopy(original)
        value["modelInputs"][0]["sourceRoot"] = "standalone"
        value["modelInputs"][0]["recordId"] = "a" * 64
        mutations["moved"] = value
        value = copy.deepcopy(original)
        value["modelInputs"][0]["sha256"] = "b" * 64
        mutations["digest"] = value
        value = copy.deepcopy(original)
        value["scoreInputs"].append(copy.deepcopy(value["modelInputs"][0]))
        mutations["group-overlap"] = value
        value = copy.deepcopy(original)
        value["ancestorEvidence"]["semanticFacts"]["r3TimedOut"] = False
        mutations["ancestry"] = value
        value = copy.deepcopy(original)
        value["authorization"]["publicPreflightEligible"] = True
        mutations["authorization"] = value

        for name, payload in mutations.items():
            with self.subTest(name=name):
                write_json(self.fixture.manifest_path, payload)
                with self.assertRaises(auditor.DeliveryAuditError):
                    self.audit()
        write_json(self.fixture.manifest_path, original)

    def test_current_source_alias_special_file_and_implementation_gate_are_rejected(self) -> None:
        source = self.fixture.roots.standalone / "score.bin"
        alias = self.fixture.roots.standalone / "score-alias.bin"
        os.link(source, alias)
        with self.assertRaisesRegex(auditor.DeliveryAuditError, "BLOCKED_ALIAS"):
            self.audit()
        alias.unlink()

        if hasattr(os, "mkfifo"):
            data = source.read_bytes()
            source.unlink()
            os.mkfifo(source)
            with self.assertRaisesRegex(auditor.DeliveryAuditError, "BLOCKED_SPECIAL_FILE"):
                self.audit()
            source.unlink()
            source.write_bytes(data)

        implementation = json.loads(self.fixture.implementation_path.read_text())
        implementation["decision"]["deliveryBuildEligible"] = False
        write_json(self.fixture.implementation_path, implementation)
        with self.assertRaisesRegex(auditor.DeliveryAuditError, "BLOCKED_GATE"):
            self.audit()

    @unittest.skipIf(os.name == "nt", "requires Linux ext4 renameat2")
    def test_audit_failure_publishes_exact_review_failure_with_readable_targets_only(self) -> None:
        self.fixture.manifest_path.write_bytes(b"{not-json}\n")
        corrupt_sha = sha(self.fixture.manifest_path)
        roots = auditor.Roots(*self.fixture.roots.mapping.values())
        with self.assertRaisesRegex(auditor.DeliveryAuditError, "FAILED_PUBLISHED"):
            auditor.audit_and_publish(
                roots,
                self.fixture.manifest_path,
                corrupt_sha,
                self.fixture.implementation_path,
                self.fixture.implementation_sha,
                self.fixture.review_path,
                auditor.DELIVERY_REVIEW_COMPLETED_CHILDREN,
                "failure-reviewer",
                self.contract,
            )
        failure_path = self.fixture.roots.wsl_evidence / auditor.REVIEW_FAILURE_NAME
        self.assertTrue(failure_path.is_file())
        failure = json.loads(failure_path.read_text(encoding="utf-8"))
        self.assertEqual(set(failure), auditor.REVIEW_FAILURE_KEYS)
        self.assertEqual(failure["schemaVersion"], auditor.REVIEW_FAILURE_SCHEMA)
        self.assertEqual(failure["status"], "FAILED")
        self.assertEqual(failure["phase"], "delivery-review")
        self.assertFalse(failure["passReviewPublished"])
        self.assertFalse(failure["deploymentAuthorized"])
        self.assertEqual(
            [item["path"] for item in failure["target"]],
            sorted(item["path"] for item in failure["target"]),
        )
        self.assertFalse(self.fixture.review_path.exists())
        self.assertFalse(any(
            path.name.startswith("." + auditor.REVIEW_NAME)
            for path in self.fixture.roots.wsl_evidence.iterdir()
        ))

    @unittest.skipIf(os.name == "nt", "requires Linux ext4 renameat2")
    def test_missing_dependency_is_state_fingerprinted_but_not_fabricated_as_target_pin(self) -> None:
        missing = self.fixture.roots.standalone / "score.bin"
        missing.unlink()
        roots = auditor.Roots(*self.fixture.roots.mapping.values())
        target, state, fingerprint = auditor._capture_review_failure_state(
            roots,
            self.fixture.manifest_path,
            self.manifest_sha,
            self.fixture.implementation_path,
            self.fixture.implementation_sha,
            self.contract,
        )
        self.assertNotIn("source/natural-score.parquet", {item["path"] for item in target})
        missing_states = [
            item for item in state["sourceStates"]
            if item["logicalPath"] == "source/natural-score.parquet"
        ]
        self.assertEqual(len(missing_states), 1)
        self.assertEqual(missing_states[0]["state"], "UNAVAILABLE")
        self.assertEqual(fingerprint, hashlib.sha256(auditor._jcs(state)).hexdigest())
        with self.assertRaisesRegex(auditor.DeliveryAuditError, "FAILED_PUBLISHED"):
            auditor.audit_and_publish(
                roots,
                self.fixture.manifest_path,
                self.manifest_sha,
                self.fixture.implementation_path,
                self.fixture.implementation_sha,
                self.fixture.review_path,
                None,
                "missing-source-reviewer",
                self.contract,
            )
        failure = json.loads(
            (self.fixture.roots.wsl_evidence / auditor.REVIEW_FAILURE_NAME).read_text(encoding="utf-8")
        )
        self.assertNotIn("source/natural-score.parquet", {item["path"] for item in failure["target"]})

    @unittest.skipIf(os.name == "nt", "requires Linux ext4 renameat2")
    def test_actual_namespace_cannot_be_extended_by_cli_or_stale_hidden_children(self) -> None:
        roots = auditor.Roots(*self.fixture.roots.mapping.values())
        cases = (
            (auditor.RUN_ID + "-extra.json", [
                *auditor.DELIVERY_REVIEW_COMPLETED_CHILDREN,
                auditor.RUN_ID + "-extra.json",
            ]),
            ("." + auditor.IMPLEMENTATION_REVIEW_NAME + ".claim", None),
            (auditor.IMPLEMENTATION_REVIEW_FAILURE_NAME, None),
        )
        for name, supplied in cases:
            with self.subTest(name=name):
                path = self.fixture.roots.wsl_evidence / name
                path.write_text("stale\n", encoding="utf-8")
                with self.assertRaises(Exception):
                    auditor.audit_and_publish(
                        roots,
                        self.fixture.manifest_path,
                        self.manifest_sha,
                        self.fixture.implementation_path,
                        self.fixture.implementation_sha,
                        self.fixture.review_path,
                        supplied,
                        "namespace-reviewer",
                        self.contract,
                    )
                self.assertFalse(self.fixture.review_path.exists())
                self.assertFalse((self.fixture.roots.wsl_evidence / auditor.REVIEW_FAILURE_NAME).exists())
                self.assertFalse((self.fixture.roots.wsl_evidence / ("." + auditor.REVIEW_NAME + ".claim")).exists())
                path.unlink()

    @unittest.skipIf(os.name == "nt", "requires Linux ext4 renameat2")
    def test_owned_claim_pre_temp_exception_closes_as_review_failure(self) -> None:
        roots = auditor.Roots(*self.fixture.roots.mapping.values())
        with mock.patch.object(
            auditor,
            "compose_review",
            side_effect=auditor.DeliveryAuditError("BLOCKED_SCHEMA", "forced post-acquire failure"),
        ):
            with self.assertRaisesRegex(auditor.DeliveryAuditError, "FAILED_PUBLISHED"):
                auditor.audit_and_publish(
                    roots,
                    self.fixture.manifest_path,
                    self.manifest_sha,
                    self.fixture.implementation_path,
                    self.fixture.implementation_sha,
                    self.fixture.review_path,
                    None,
                    "owned-lease-reviewer",
                    self.contract,
                )
        failure_path = self.fixture.roots.wsl_evidence / auditor.REVIEW_FAILURE_NAME
        self.assertTrue(failure_path.is_file())
        failure = json.loads(failure_path.read_text(encoding="utf-8"))
        self.assertEqual(failure["error"]["type"], "DeliveryAuditError")
        self.assertEqual(failure["publication"]["role"], "REVIEWER")
        self.assertFalse(self.fixture.review_path.exists())
        self.assertFalse(any(
            path.name.startswith("." + auditor.REVIEW_NAME)
            for path in self.fixture.roots.wsl_evidence.iterdir()
        ))

    @unittest.skipIf(os.name == "nt", "requires Linux ext4 renameat2 and multiprocessing")
    def test_thirty_two_reviewers_publish_exactly_one_review_without_residue(self) -> None:
        roots = self.fixture.roots
        manifest_sha = sha(self.fixture.manifest_path)
        context = multiprocessing.get_context("fork")
        start = context.Event()
        result = context.Queue()
        audit_roots = auditor.Roots(*roots.mapping.values())
        processes = [
            context.Process(
                target=review_worker,
                args=(start, result, audit_roots, self.fixture.manifest_path, manifest_sha,
                      self.fixture.implementation_path, self.fixture.implementation_sha,
                      self.fixture.review_path, self.contract, index),
            )
            for index in range(32)
        ]
        for process in processes:
            process.start()
        start.set()
        for process in processes:
            process.join(30)
            self.assertFalse(process.is_alive())
            self.assertEqual(process.exitcode, 0)
        outcomes = []
        while True:
            try:
                outcomes.append(result.get_nowait())
            except queue.Empty:
                break
        self.assertEqual(len(outcomes), 32)
        self.assertEqual(sum(item[0] == "PASS" for item in outcomes), 1)
        self.assertTrue(self.fixture.review_path.is_file())
        hidden = [path.name for path in self.fixture.roots.wsl_evidence.iterdir()
                  if path.name.startswith("." + auditor.REVIEW_NAME)]
        self.assertEqual(hidden, [])
        review = json.loads(self.fixture.review_path.read_text())
        self.assertEqual(review["schemaVersion"], auditor.REVIEW_SCHEMA)
        self.assertEqual(review["decision"], auditor.DECISION)


if __name__ == "__main__":
    unittest.main()
