from __future__ import annotations

import ast
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import uuid
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_service_v1_b1_r4_server_receipt as receipt


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json_bytes(value))


def prior_publication(role: str, final_path: Path, publisher_path: Path) -> dict[str, object]:
    failure_path = final_path.with_name(final_path.stem + "-failure.json")
    publisher_raw = publisher_path.read_bytes()
    return {"schemaVersion": "feelm-service-v1-b1-r4-publication-evidence/1",
            "mode": "LINUX_RENAME_NOREPLACE", "role": role, "finalPath": str(final_path),
            "failurePath": str(failure_path), "claimPath": str(final_path.with_name(".claim")),
            "tempPath": str(final_path.with_name(".tmp")), "token": "prior-token",
            "claimStDev": 1, "claimStIno": 2, "filesystemType": "ext2/ext3",
            "publisher": {"path": str(publisher_path.resolve()), "bytes": len(publisher_raw),
                          "sha256": hashlib.sha256(publisher_raw).hexdigest()},
            "dependencyFingerprintAtAcquire": "8" * 64,
            "dependencyFingerprintBeforeRename": "8" * 64, "renameNoReplaceProbe": True,
            "requiredPostconditions": {"publishedBytesRehashRequired": True,
                                       "fileAndParentFsyncRequired": True,
                                       "dependencyFingerprintStableRequired": True,
                                       "claimIdentityMatchRequired": True,
                                       "claimRemovalRequired": True}}


def record(source_root: str, logical: str, destination: str, raw: bytes,
           kind: str = "regular-file") -> dict[str, object]:
    return {"recordId": receipt.sha256_bytes(source_root.encode() + b"\0" + logical.encode()),
            "logicalPath": logical, "sourceRoot": source_root, "destinationRelativePath": destination,
            "bytes": len(raw), "sha256": receipt.sha256_bytes(raw), "kind": kind}


class FakePublication:
    def __init__(self) -> None:
        self.acquire_calls: list[tuple[object, ...]] = []
        self.success_calls = 0
        self.failure_calls = 0

    def acquire_publication(self, role, phase, final, failure, expected):
        self.acquire_calls.append((role, phase, Path(final), Path(failure), expected))
        publisher_path = next(
            (ancestor / receipt.PUBLICATION_RELATIVE for ancestor in Path(final).parents
             if (ancestor / receipt.PUBLICATION_RELATIVE).is_file()),
            ROOT / receipt.PUBLICATION_RELATIVE,
        )
        publisher_raw = publisher_path.read_bytes()
        evidence = {"schemaVersion": "feelm-service-v1-b1-r4-publication-evidence/1",
                    "mode": "LINUX_RENAME_NOREPLACE", "role": role, "finalPath": str(final),
                    "failurePath": str(failure), "claimPath": str(Path(final).with_name(".claim")),
                    "tempPath": str(Path(final).with_name("." + Path(final).name + ".tmp-test")),
                    "token": "test-token", "claimStDev": 1, "claimStIno": 2,
                    "filesystemType": "ext2/ext3",
                    "publisher": {"path": str(publisher_path.resolve()), "bytes": len(publisher_raw),
                                  "sha256": hashlib.sha256(publisher_raw).hexdigest()},
                    "dependencyFingerprintAtAcquire": expected["dependencyFingerprint"],
                    "dependencyFingerprintBeforeRename": expected["dependencyFingerprint"],
                    "renameNoReplaceProbe": True,
                    "requiredPostconditions": {"publishedBytesRehashRequired": True,
                                               "fileAndParentFsyncRequired": True,
                                               "dependencyFingerprintStableRequired": True,
                                               "claimIdentityMatchRequired": True,
                                               "claimRemovalRequired": True}}
        return SimpleNamespace(final_path=Path(final), failure_path=Path(failure),
                               temp_path=Path(evidence["tempPath"]), publication_evidence=evidence)

    def publish_success(self, lease, stage, fingerprint, callback):
        self.success_calls += 1
        if callback() != fingerprint:
            raise RuntimeError("drift")
        if lease.final_path.exists():
            raise FileExistsError(lease.final_path)
        os.rename(stage, lease.final_path)
        published_path = lease.final_path / "manifest.json" if lease.final_path.is_dir() else lease.final_path
        return SimpleNamespace(record=receipt.pin_file(published_path))

    def publish_handled_failure(self, lease, payload, fingerprint, callback):
        self.failure_calls += 1
        if callback() != fingerprint:
            raise RuntimeError("drift")
        if lease.temp_path.exists():
            shutil.rmtree(lease.temp_path)
        write_json(lease.failure_path, payload)
        return SimpleNamespace(record=receipt.pin_file(lease.failure_path))

    def release_verified_claim(self, lease, pin, fingerprint):
        return None


class DockerRunner:
    def __init__(self, *, fail_load: bool = False) -> None:
        self.loaded = False
        self.fail_load = fail_load
        self.calls: list[list[str]] = []

    def __call__(self, argv, **_kwargs):
        argv = list(argv)
        self.calls.append(argv)
        if argv[:3] == ["docker", "image", "ls"]:
            line = json.dumps({"ID": receipt.EXPECTED_IMAGE_ID}) + "\n" if self.loaded else ""
            return SimpleNamespace(returncode=0, stdout=line, stderr="")
        if argv[:3] == ["docker", "image", "inspect"]:
            if not self.loaded:
                return SimpleNamespace(returncode=1, stdout="", stderr="not found")
            payload = [{"Id": receipt.EXPECTED_IMAGE_ID, "RepoTags": ["feelm:test"],
                        "RepoDigests": ["feelm@sha256:" + "a" * 64],
                        "Size": receipt.EXPECTED_IMAGE_UNPACKED_BYTES}]
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
        if argv[:2] == ["docker", "load"]:
            if self.fail_load:
                return SimpleNamespace(returncode=1, stdout="partial\n", stderr="load failed\n")
            self.loaded = True
            return SimpleNamespace(returncode=0, stdout="Loaded image\n", stderr="")
        raise AssertionError(argv)


class ReceiptFixture:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.common = Path(self.temporary.name)
        self.standalone = self.common / "FEELM-standalone"
        self.team = self.common / "S15P21E106"
        self.delivery = self.common / receipt.RUN_ID
        self.output = self.standalone / receipt.OUTPUT_RELATIVE
        self.scratch = self.common / "scratch"
        for path in (self.output, self.team, self.delivery, self.scratch,
                     self.standalone / "scripts", self.standalone / receipt.PROFILE_RELATIVE.parent):
            path.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / receipt.PROFILE_RELATIVE, self.standalone / receipt.PROFILE_RELATIVE)
        shutil.copyfile(ROOT / receipt.PRODUCER_RELATIVE, self.standalone / receipt.PRODUCER_RELATIVE)
        shutil.copyfile(ROOT / receipt.PUBLICATION_RELATIVE, self.standalone / receipt.PUBLICATION_RELATIVE)
        self.label_raw = b"opaque parquet bytes\n"
        self.archive_raw = b"small docker archive fixture\n"
        label = self.delivery / "evaluation/labels.parquet"
        archive = self.delivery / "runtime/feelm-rec046-spark-local.tar"
        label.parent.mkdir(parents=True)
        archive.parent.mkdir(parents=True)
        label.write_bytes(self.label_raw)
        archive.write_bytes(self.archive_raw)
        virtual_raw = json_bytes({"imageId": receipt.EXPECTED_IMAGE_ID,
                                  "imageUnpackedSizeBytes": receipt.EXPECTED_IMAGE_UNPACKED_BYTES,
                                  "mode": "ARCHIVE"})
        self.records = [
            record("wslDelivery", "evaluation/labels.parquet", "evaluation/labels.parquet", self.label_raw),
            record("wslDelivery", "runtime/feelm-rec046-spark-local.tar",
                   "runtime/feelm-rec046-spark-local.tar", self.archive_raw),
            record("virtual", "runtime/docker-image-contract.json", "runtime/docker-image-contract.json",
                   virtual_raw, "docker-image-contract"),
        ]
        delivery_set = receipt.canonical_record_set_sha256(self.records, path_key="logicalPath")
        self.manifest = {"schemaVersion": "feelm-service-v1-b1-r4-server-delivery/2",
                         "status": "DELIVERY_AUDIT_PENDING", "runId": receipt.RUN_ID,
                         "profileId": receipt.PROFILE_ID, "createdAt": "2026-09-14T00:00:00Z",
                         "producer": {"recordId": "producer"},
                         "sourceRoots": {"standalone": "/s", "team": "/t", "wslEvidence": "/e",
                                         "wslDelivery": "/d"},
                         "destinationLayout": {"siblingRootsRequired": True,
                                               "standaloneDirectoryName": "FEELM-standalone",
                                               "teamDirectoryName": "S15P21E106",
                                               "deliveryDirectoryName": receipt.RUN_ID,
                                               "preserveRelativePaths": True},
                         "implementationReview": {"recordId": "review"},
                         "modelInputs": [self.records[0]], "scoreInputs": [], "workerRuntime": [],
                         "evaluationInputs": [], "controlAndImplementation": self.records[1:],
                         "ancestorEvidence": {"schemaVersion": "fixture", "records": [],
                                              "semanticFacts": {}, "recordSetSha256": "0" * 64,
                                              "semanticFactsSha256": "0" * 64, "closureSha256": "0" * 64},
                         "crossGroupReferences": [], "crossGroupReferencesSha256": "1" * 64,
                         "recordGroupDigests": {key: "2" * 64 for key in
                                                ("modelInputs", "scoreInputs", "workerRuntime", "evaluationInputs",
                                                 "controlAndImplementation", "ancestorEvidence")},
                         "deliverySetSha256": delivery_set, "hostRequirements": {},
                         "authorization": {}, "publication": {}}
        self.manifest_path = self.output / receipt.DELIVERY_MANIFEST_NAME
        self.manifest["publication"] = prior_publication(
            "PRODUCER", self.manifest_path, self.standalone / receipt.PUBLICATION_RELATIVE)
        write_json(self.manifest_path, self.manifest)
        manifest_pin = receipt.pin_file(self.manifest_path)
        write_json(self.output / receipt.DESIGN_REVIEW_NAME,
                   {"status": "PASS", "decision": {"designIntegrity": "PASS"}})
        write_json(self.output / receipt.IMPLEMENTATION_REVIEW_NAME,
                   {"status": "PASS", "decision": {"implementationIntegrity": "PASS"}})
        implementation_pin = receipt.pin_file(self.output / receipt.IMPLEMENTATION_REVIEW_NAME)
        self.review = {"schemaVersion": "feelm-service-v1-b1-r4-server-delivery-review/1",
                       "status": "PASS", "runId": receipt.RUN_ID, "profileId": receipt.PROFILE_ID,
                       "createdAt": "2026-09-14T00:00:01Z",
                       "reviewer": {"kind": "INDEPENDENT_SERVER_DELIVERY_REVIEWER",
                                    "sessionId": "fixture", "host": "fixture-host", "processId": 1},
                       "target": {"manifest": manifest_pin, "implementationReview": implementation_pin,
                                  "deliverySetSha256": delivery_set,
                                  "crossGroupReferencesSha256": "1" * 64},
                       "checks": {key: True for key in
                                  ("schemaValid", "implementationReviewPass", "groupUnionDisjoint",
                                   "crossReferencesValid", "allFilesRehashed", "imageArchiveVerified",
                                   "ancestryVerified", "authorizationValid", "namespaceValid")},
                       "decision": {"deliveryIntegrity": "PASS", "serverTransferEligible": True,
                                    "publicPreflightEligible": False, "dockerLoadEligible": True,
                                    "runtimeInstallEligible": True, "deploymentAuthorized": False},
                       "dependencyFingerprint": "3" * 64, "publication": {}}
        self.review_path = self.output / receipt.DELIVERY_REVIEW_NAME
        self.review["publication"] = prior_publication(
            "REVIEWER", self.review_path, self.standalone / receipt.PUBLICATION_RELATIVE)
        write_json(self.review_path, self.review)
        self.reservations: dict[str, Path] = {}
        start = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        for key, (phase, filename, seconds) in receipt.RESERVATION_FILES.items():
            path = self.scratch / "reservations" / filename
            value = {"schemaVersion": "feelm-service-v1-b1-r4-maintenance-reservation/2", "status": "ACTIVE",
                     "reservationId": str(uuid.uuid4()), "hostIdentity": "fixture-host", "phase": phase,
                     "startsAt": start.isoformat().replace("+00:00", "Z"),
                     "endsAt": (start + dt.timedelta(seconds=seconds + 60)).isoformat().replace("+00:00", "Z"),
                     "dockerRestartScheduled": False, "hostRebootScheduled": False,
                     "issuedBy": "fixture", "createdAt": start.isoformat().replace("+00:00", "Z")}
            write_json(path, value)
            self.reservations[key] = path
        self.roots = receipt.ReceiptRoots.create(self.standalone, self.team, self.delivery,
                                                 self.output, self.scratch)

    def close(self) -> None:
        self.temporary.cleanup()


def host_probe(_roots, _runner):
    return {"unameMachine": "x86_64", "logicalCpu": 8, "memTotalBytes": 32_000_000_000,
            "memAvailableBytes": 25_769_803_776, "dockerServerVersion": "27.0",
            "cgroupVersion": "v2", "cgroupControllers": ["cpu", "memory", "pids"],
            "cgroupPeakReadable": True, "scratchFreeBytes": 85_899_345_920,
            "outputFreeBytes": 21_474_836_480, "scratchFreeInodes": 100_000,
            "outputFreeInodes": 100_000, "outputDevice": 1, "scratchDevice": 2,
            "renameNoReplaceProbe": True, "supervisorKind": "systemd", "observedAt": receipt.utc_now()}


def runtime_stub(_roots, _runner):
    inventory = {"schemaVersion": "feelm-service-v1-b1-r4-venv-inventory/1", "root": "/venv",
                 "records": [{"path": "bin/python3", "bytes": 1, "sha256": "a" * 64,
                              "mode": 0o555, "stDev": 1, "stIno": 2, "nlink": 1}],
                 "regularFileCount": 1, "symlinkCount": 0, "specialFileCount": 0,
                 "hardLinkAliasCount": 0, "recordSetSha256": "b" * 64}
    lock = {"schemaVersion": "feelm-service-v1-b1-r4-host-runtime-lock/1"}
    return inventory, lock


class BuilderContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = ReceiptFixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def test_normal_receipt_uses_one_load_six_reservations_and_inline_byte_evidence(self) -> None:
        docker = DockerRunner()
        publication = FakePublication()
        archive_sha = receipt.sha256_bytes(self.fixture.archive_raw)
        with mock.patch.object(receipt, "EXPECTED_ARCHIVE_SHA256", archive_sha):
            published = receipt.produce_receipt(
                roots=self.fixture.roots, delivery_manifest_path=self.fixture.manifest_path,
                expected_delivery_manifest_sha256=receipt.sha256_file(self.fixture.manifest_path),
                delivery_review_path=self.fixture.review_path,
                expected_delivery_review_sha256=receipt.sha256_file(self.fixture.review_path),
                reservations=self.fixture.reservations, runner=docker,
                host_probe_collector=host_probe, runtime_builder=runtime_stub, publication=publication)
        self.assertEqual(publication.success_calls, 1)
        self.assertEqual(publication.failure_calls, 0)
        self.assertEqual(sum(call[:2] == ["docker", "load"] for call in docker.calls), 1)
        self.assertEqual(publication.acquire_calls[0][:2], ("PRODUCER", "receipt-build"))
        manifest, _raw = receipt.load_json(self.fixture.roots.receipt / "manifest.json")
        self.assertEqual(set(manifest["destinationInventory"]), set(receipt.build_destination_inventory(
            self.fixture.manifest, self.fixture.roots)))
        self.assertEqual(set(manifest["hostProbe"]), set(receipt.HOST_PROBE_KEYS))
        self.assertEqual(set(manifest["maintenanceReservations"]), set(receipt.RESERVATION_FILES))
        self.assertEqual(manifest["rehashSummary"]["labelBytesHashed"], True)
        self.assertIs(manifest["rehashSummary"]["labelRowsRead"], False)
        self.assertIs(manifest["rehashSummary"]["evaluationTargetsRead"], False)
        self.assertEqual(published["sha256"], receipt.sha256_file(self.fixture.roots.receipt / "manifest.json"))

    def test_docker_failure_publishes_failure_and_never_publishes_receipt(self) -> None:
        docker = DockerRunner(fail_load=True)
        publication = FakePublication()
        with mock.patch.object(receipt, "EXPECTED_ARCHIVE_SHA256", receipt.sha256_bytes(self.fixture.archive_raw)):
            with self.assertRaisesRegex(receipt.ReceiptBuildError, "immutable failure published"):
                receipt.produce_receipt(
                    roots=self.fixture.roots, delivery_manifest_path=self.fixture.manifest_path,
                    expected_delivery_manifest_sha256=receipt.sha256_file(self.fixture.manifest_path),
                    delivery_review_path=self.fixture.review_path,
                    expected_delivery_review_sha256=receipt.sha256_file(self.fixture.review_path),
                    reservations=self.fixture.reservations, runner=docker,
                    host_probe_collector=host_probe, runtime_builder=runtime_stub, publication=publication)
        self.assertFalse(self.fixture.roots.receipt.exists())
        self.assertTrue(self.fixture.roots.receipt_failure.is_file())
        self.assertEqual(publication.failure_calls, 1)
        self.assertEqual(sum(call[:2] == ["docker", "load"] for call in docker.calls), 1)
        failure, _raw = receipt.load_json(self.fixture.roots.receipt_failure)
        expected_load = ["docker", "load", "--input",
                         str(self.fixture.delivery / "runtime/feelm-rec046-spark-local.tar")]
        self.assertEqual(failure["command"]["intendedArgv"], expected_load)
        self.assertEqual(failure["command"]["actualArgv"], expected_load)
        self.assertEqual(failure["command"]["dockerArgv"], expected_load)
        self.assertEqual(failure["hostGate"]["status"], "PASS")

    def test_unambiguous_success_publication_error_becomes_exact_failure(self) -> None:
        class RejectBeforeRename(FakePublication):
            def publish_success(self, lease, stage, fingerprint, callback):
                self.success_calls += 1
                if callback() != fingerprint:
                    raise RuntimeError("drift")
                raise RuntimeError("deterministic pre-rename rejection")

        docker = DockerRunner()
        publication = RejectBeforeRename()
        with mock.patch.object(receipt, "EXPECTED_ARCHIVE_SHA256",
                               receipt.sha256_bytes(self.fixture.archive_raw)):
            with self.assertRaisesRegex(receipt.ReceiptBuildError, "immutable failure published"):
                receipt.produce_receipt(
                    roots=self.fixture.roots, delivery_manifest_path=self.fixture.manifest_path,
                    expected_delivery_manifest_sha256=receipt.sha256_file(self.fixture.manifest_path),
                    delivery_review_path=self.fixture.review_path,
                    expected_delivery_review_sha256=receipt.sha256_file(self.fixture.review_path),
                    reservations=self.fixture.reservations, runner=docker,
                    host_probe_collector=host_probe, runtime_builder=runtime_stub,
                    publication=publication)
        self.assertEqual(publication.success_calls, 1)
        self.assertEqual(publication.failure_calls, 1)
        self.assertFalse(self.fixture.roots.receipt.exists())
        failure, _raw = receipt.load_json(self.fixture.roots.receipt_failure)
        self.assertEqual(failure["failureStage"], "PUBLICATION")

    def test_post_rename_publication_error_preserves_ambiguous_terminal_state(self) -> None:
        class RaiseAfterRename(FakePublication):
            def publish_success(self, lease, stage, fingerprint, callback):
                self.success_calls += 1
                if callback() != fingerprint:
                    raise RuntimeError("drift")
                os.rename(stage, lease.final_path)
                raise RuntimeError("post-rename evidence failure")

        docker = DockerRunner()
        publication = RaiseAfterRename()
        with mock.patch.object(receipt, "EXPECTED_ARCHIVE_SHA256",
                               receipt.sha256_bytes(self.fixture.archive_raw)):
            with self.assertRaisesRegex(receipt.ReceiptBuildError, "ambiguous or drifted; claim preserved"):
                receipt.produce_receipt(
                    roots=self.fixture.roots, delivery_manifest_path=self.fixture.manifest_path,
                    expected_delivery_manifest_sha256=receipt.sha256_file(self.fixture.manifest_path),
                    delivery_review_path=self.fixture.review_path,
                    expected_delivery_review_sha256=receipt.sha256_file(self.fixture.review_path),
                    reservations=self.fixture.reservations, runner=docker,
                    host_probe_collector=host_probe, runtime_builder=runtime_stub,
                    publication=publication)
        self.assertEqual(publication.failure_calls, 0)
        self.assertTrue(self.fixture.roots.receipt.is_dir())
        self.assertFalse(self.fixture.roots.receipt_failure.exists())

    def test_destination_contamination_missing_and_alias_fail_closed(self) -> None:
        label = self.fixture.delivery / "evaluation/labels.parquet"
        label.write_bytes(b"tampered\n")
        with self.assertRaisesRegex(receipt.ReceiptBuildError, "destination byte drift"):
            receipt.build_destination_inventory(self.fixture.manifest, self.fixture.roots)
        label.write_bytes(self.fixture.label_raw)
        label.unlink()
        with self.assertRaises(receipt.ReceiptBuildError):
            receipt.build_destination_inventory(self.fixture.manifest, self.fixture.roots)
        label.write_bytes(self.fixture.label_raw)
        archive = self.fixture.delivery / "runtime/feelm-rec046-spark-local.tar"
        archive.unlink()
        os.link(label, archive)
        aliased_manifest = dict(self.fixture.manifest)
        aliased_records = [dict(value) for value in self.fixture.records]
        aliased_records[1]["bytes"] = len(self.fixture.label_raw)
        aliased_records[1]["sha256"] = receipt.sha256_bytes(self.fixture.label_raw)
        aliased_manifest["modelInputs"] = [aliased_records[0]]
        aliased_manifest["controlAndImplementation"] = aliased_records[1:]
        aliased_manifest["deliverySetSha256"] = receipt.canonical_record_set_sha256(
            aliased_records, path_key="logicalPath")
        with self.assertRaisesRegex(receipt.ReceiptBuildError, "hard-link alias"):
            receipt.build_destination_inventory(aliased_manifest, self.fixture.roots)

    def test_reservation_missing_and_path_alias_are_rejected(self) -> None:
        missing = dict(self.fixture.reservations)
        missing.pop("fit")
        with self.assertRaisesRegex(receipt.ReceiptBuildError, "six distinct"):
            receipt.read_reservations(missing)
        aliases = dict(self.fixture.reservations)
        aliases["fit"] = aliases["score"]
        with self.assertRaisesRegex(receipt.ReceiptBuildError, "must be distinct"):
            receipt.read_reservations(aliases)

    def test_stale_expected_sha_is_rejected_before_any_command(self) -> None:
        docker = DockerRunner()
        publication = FakePublication()
        runtime_lock = self.fixture.delivery / receipt.HOST_RUNTIME_REQUIREMENTS_RELATIVE
        runtime_lock.parent.mkdir(parents=True, exist_ok=True)
        runtime_lock.write_bytes(b"sealed-runtime-lock\n")
        with self.assertRaisesRegex(receipt.ReceiptBuildError, "SHA-256 drift"):
            receipt.produce_receipt(
                roots=self.fixture.roots, delivery_manifest_path=self.fixture.manifest_path,
                expected_delivery_manifest_sha256="f" * 64, delivery_review_path=self.fixture.review_path,
                expected_delivery_review_sha256=receipt.sha256_file(self.fixture.review_path),
                reservations=self.fixture.reservations, runner=docker,
                host_probe_collector=host_probe, runtime_builder=runtime_stub,
                publication=publication)
        self.assertEqual(docker.calls, [])
        self.assertEqual(publication.failure_calls, 1)
        failure, _raw = receipt.load_json(self.fixture.roots.receipt_failure)
        self.assertEqual(set(failure), set(receipt.PHASE_FAILURE_KEYS))
        self.assertEqual(failure["schemaVersion"], "feelm-service-v1-b1-r4-phase-failure/2")
        self.assertEqual(failure["failureStage"], "PRE_CONTAINER")
        self.assertEqual(failure["command"]["actualArgv"], [])
        self.assertIsNotNone(failure["delivery"]["runtimeLock"])
        self.assertIn("dependencySnapshot", failure["phaseInputLock"])
        self.assertEqual(failure["phaseInputLock"]["status"], "INTENDED")
        self.assertEqual(failure["hostGate"]["status"], "NOT_STARTED")
        self.assertEqual(failure["hostGate"]["unknownFields"], sorted(receipt.HOST_PROBE_KEYS))
        self.assertEqual([row["kind"] for row in failure["authorizationEvidence"]],
                         ["DESIGN_REVIEW", "IMPLEMENTATION_REVIEW", "DELIVERY_REVIEW"])
        for evidence in failure["authorizationEvidence"]:
            source = self.fixture.output / {
                "DESIGN_REVIEW": receipt.DESIGN_REVIEW_NAME,
                "IMPLEMENTATION_REVIEW": receipt.IMPLEMENTATION_REVIEW_NAME,
                "DELIVERY_REVIEW": receipt.DELIVERY_REVIEW_NAME,
            }[evidence["kind"]]
            self.assertEqual(evidence["pin"], receipt.pin_file(source, str(source.absolute())))

    def test_host_gate_failure_records_failed_gate_without_running_docker(self) -> None:
        docker = DockerRunner()
        publication = FakePublication()

        def invalid_host(roots: receipt.ReceiptRoots, runner: object) -> dict[str, object]:
            value = host_probe(roots, runner)
            value["logicalCpu"] = 1
            return value

        with self.assertRaisesRegex(receipt.ReceiptBuildError, "immutable failure published"):
            receipt.produce_receipt(
                roots=self.fixture.roots, delivery_manifest_path=self.fixture.manifest_path,
                expected_delivery_manifest_sha256=receipt.sha256_file(self.fixture.manifest_path),
                delivery_review_path=self.fixture.review_path,
                expected_delivery_review_sha256=receipt.sha256_file(self.fixture.review_path),
                reservations=self.fixture.reservations, runner=docker,
                host_probe_collector=invalid_host, runtime_builder=runtime_stub,
                publication=publication)
        self.assertEqual(docker.calls, [])
        failure, _raw = receipt.load_json(self.fixture.roots.receipt_failure)
        self.assertEqual(failure["failureStage"], "HOST_GATE")
        self.assertEqual(failure["hostGate"]["status"], "FAILED")
        self.assertEqual(failure["hostGate"]["unknownFields"], [])
        self.assertEqual(failure["command"]["actualArgv"], [])

    def test_exact_predecessor_namespace_rejects_hidden_or_extra_without_claim(self) -> None:
        for name in ("." + receipt.RUN_ID + "-stale.claim", receipt.RUN_ID + "-unexpected.json"):
            fixture = ReceiptFixture()
            try:
                (fixture.output / name).write_bytes(b"blocked\n")
                docker = DockerRunner()
                publication = FakePublication()
                with self.assertRaisesRegex(receipt.ReceiptBuildError, "predecessor namespace drift"):
                    receipt.produce_receipt(
                        roots=fixture.roots, delivery_manifest_path=fixture.manifest_path,
                        expected_delivery_manifest_sha256=receipt.sha256_file(fixture.manifest_path),
                        delivery_review_path=fixture.review_path,
                        expected_delivery_review_sha256=receipt.sha256_file(fixture.review_path),
                        reservations=fixture.reservations, runner=docker,
                        host_probe_collector=host_probe, runtime_builder=runtime_stub,
                        publication=publication)
                self.assertEqual(publication.acquire_calls, [])
                self.assertEqual(docker.calls, [])
                self.assertFalse(fixture.roots.receipt_failure.exists())
            finally:
                fixture.close()

    def test_missing_special_and_extra_destination_publish_pre_action_failure(self) -> None:
        mutations = ("missing", "special", "extra")
        for mutation in mutations:
            fixture = ReceiptFixture()
            try:
                label = fixture.delivery / "evaluation/labels.parquet"
                if mutation == "missing":
                    label.unlink()
                elif mutation == "special":
                    label.unlink()
                    label.mkdir()
                else:
                    (fixture.delivery / "unexpected.bin").write_bytes(b"extra\n")
                docker = DockerRunner()
                publication = FakePublication()
                with self.assertRaisesRegex(receipt.ReceiptBuildError, "immutable failure published"):
                    receipt.produce_receipt(
                        roots=fixture.roots, delivery_manifest_path=fixture.manifest_path,
                        expected_delivery_manifest_sha256=receipt.sha256_file(fixture.manifest_path),
                        delivery_review_path=fixture.review_path,
                        expected_delivery_review_sha256=receipt.sha256_file(fixture.review_path),
                        reservations=fixture.reservations, runner=docker,
                        host_probe_collector=host_probe, runtime_builder=runtime_stub,
                        publication=publication)
                self.assertEqual(publication.failure_calls, 1)
                self.assertEqual(docker.calls, [])
                self.assertTrue(fixture.roots.receipt_failure.is_file())
            finally:
                fixture.close()

    def test_destination_component_symlink_is_not_canonicalized_away(self) -> None:
        evaluation = self.fixture.delivery / "evaluation"
        outside = self.fixture.common / "outside-evaluation"
        evaluation.rename(outside)
        try:
            os.symlink(outside, evaluation, target_is_directory=True)
        except OSError as error:
            outside.rename(evaluation)
            self.skipTest(f"directory symlink unavailable: {error}")
        with self.assertRaisesRegex(receipt.ReceiptBuildError, "lexical path is not canonically bound"):
            receipt.build_destination_inventory(self.fixture.manifest, self.fixture.roots)

    def test_binary_pin_streams_fixed_chunks_without_path_read_bytes(self) -> None:
        path = self.fixture.delivery / "large-stream.bin"
        payload = (b"0123456789abcdef" * ((receipt.READ_CHUNK_BYTES * 3 + 31) // 16 + 1))[
            :receipt.READ_CHUNK_BYTES * 3 + 31]
        path.write_bytes(payload)
        original_read = os.read
        requests: list[int] = []

        def guarded_read(descriptor: int, count: int) -> bytes:
            requests.append(count)
            if count > receipt.READ_CHUNK_BYTES:
                raise AssertionError("read-all request")
            return original_read(descriptor, count)

        with mock.patch.object(receipt.os, "read", side_effect=guarded_read), \
             mock.patch.object(Path, "read_bytes", side_effect=AssertionError("Path.read_bytes forbidden")):
            pin = receipt.pin_file(path)
        self.assertEqual(pin["bytes"], len(payload))
        self.assertEqual(pin["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertGreaterEqual(len(requests), 4)
        self.assertLessEqual(max(requests), receipt.READ_CHUNK_BYTES)


class RuntimeCommandTests(unittest.TestCase):
    def test_offline_hashed_install_and_complete_read_only_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            common = Path(directory)
            standalone = common / "FEELM-standalone"
            team = common / "S15P21E106"
            delivery = common / receipt.RUN_ID
            output = standalone / receipt.OUTPUT_RELATIVE
            scratch = common / "scratch"
            for path in (output, team, delivery, scratch):
                path.mkdir(parents=True, exist_ok=True)
            wheel_records = []
            distributions = [("numpy", "1.26.4"), ("pandas", "2.2.3"), ("pyarrow", "19.0.1"),
                             ("python-dateutil", "2.9.0.post0"), ("pytz", "2026.3.post1"),
                             ("six", "1.17.0"), ("tzdata", "2026.4")]
            for name, version in distributions:
                filename = name.replace("-", "_") + f"-{version}-py3-none-any.whl"
                relative = f"runtime/service-v1-b1-r4-wheelhouse/{filename}"
                path = delivery / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(path, "w") as archive:
                    archive.writestr(f"{name.replace('-', '_')}-{version}.dist-info/METADATA",
                                     f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
                raw = path.read_bytes()
                wheel_records.append({"path": relative, "bytes": len(raw),
                                      "sha256": receipt.sha256_bytes(raw)})
            wheel_set = receipt.canonical_record_set_sha256(wheel_records)
            write_json(delivery / "runtime/service-v1-b1-r4-wheelhouse-manifest.json",
                       {"schemaVersion": "feelm-service-v1-b1-r4-wheelhouse/1", "pythonVersion": "3.12.3",
                        "interpreterTag": "cp312", "abiTag": "cp312", "platformTag": "manylinux_2_17_x86_64",
                        "files": wheel_records, "wheelhouseSetSha256": wheel_set})
            lock_lines = [f"{name}=={version} --hash=sha256:{wheel_records[index]['sha256']}"
                          for index, (name, version) in enumerate(distributions)]
            requirements = delivery / "requirements/service-v1-b1-r4-host-runtime.lock"
            requirements.parent.mkdir(parents=True)
            requirements.write_text("\n".join(lock_lines) + "\n", encoding="utf-8", newline="\n")
            roots = receipt.ReceiptRoots.create(standalone, team, delivery, output, scratch)
            calls: list[tuple[list[str], dict[str, str]]] = []
            bootstrap_executable = common / "bootstrap-python.exe"
            physical_python = Path(sys.base_prefix) / "python.exe" if os.name == "nt" else Path(sys.executable)
            shutil.copyfile(physical_python, bootstrap_executable)
            os.chmod(bootstrap_executable, 0o755)

            def runner(argv, **kwargs):
                argv = list(argv)
                calls.append((argv, dict(kwargs.get("env") or {})))
                if argv[0] == "python3" and argv[1:3] == ["-I", "-c"]:
                    payload = {"executable": str(bootstrap_executable), "implementation": "CPython",
                               "version": "3.12.3"}
                    return SimpleNamespace(returncode=0, stdout=json.dumps(payload) + "\n", stderr="")
                if argv[:5] == ["python3", "-I", "-m", "venv", "--copies"]:
                    venv = Path(argv[5])
                    (venv / "bin").mkdir(parents=True)
                    shutil.copyfile(physical_python, venv / "bin/python3")
                    os.chmod(venv / "bin/python3", 0o755)
                    (venv / "pyvenv.cfg").write_text("fixture\n", encoding="utf-8")
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                if "pip" in argv:
                    site = roots.runtime_root / "venv/lib/python3.12/site-packages"
                    for name, version in {"numpy": "1.26.4", "pandas": "2.2.3", "pyarrow": "19.0.1"}.items():
                        module = site / name / "__init__.py"
                        module.parent.mkdir(parents=True, exist_ok=True)
                        module.write_text(name + "\n", encoding="utf-8")
                        info = site / f"{name}-{version}.dist-info"
                        info.mkdir()
                        record_path = info / "RECORD"
                        record_path.write_text(f"{name}/__init__.py,,\n{name}-{version}.dist-info/RECORD,,\n",
                                               encoding="utf-8")
                    return SimpleNamespace(returncode=0, stdout="installed\n", stderr="")
                if argv[0].endswith("python3") and argv[1:3] == ["-I", "-c"]:
                    site = roots.runtime_root / "venv/lib/python3.12/site-packages"
                    packages = {}
                    for name, version in {"numpy": "1.26.4", "pandas": "2.2.3", "pyarrow": "19.0.1"}.items():
                        packages[name] = {"version": version, "modulePath": str((site / name / "__init__.py").resolve()),
                                          "distributionFiles": sorted(str(path.resolve()) for path in
                                              ((site / name / "__init__.py"),
                                               (site / f"{name}-{version}.dist-info/RECORD")))}
                    input_raw = receipt.canonical_json({"a": [1, 2, 3], "b": [0.5, 1.5, 2.5]})
                    output_raw = receipt.canonical_json({"sum": 4.5, "rows": 3, "columns": ["a", "b"]})
                    payload = {"executable": str((roots.runtime_root / "venv/bin/python3").resolve()),
                               "implementation": "CPython", "version": "3.12.3", "packages": packages,
                               "fixture": {"inputSha256": receipt.sha256_bytes(input_raw),
                                           "outputSha256": receipt.sha256_bytes(output_raw)}}
                    return SimpleNamespace(returncode=0, stdout=json.dumps(payload, sort_keys=True) + "\n", stderr="")
                raise AssertionError(argv)

            with mock.patch.object(receipt, "WHEELHOUSE_SET_SHA256", wheel_set):
                inventory, runtime_lock = receipt.build_host_runtime(roots, runner)
            self.assertEqual(runtime_lock["pythonVersion"], "3.12.3")
            self.assertEqual(set(runtime_lock["packages"]), {"numpy", "pandas", "pyarrow"})
            self.assertEqual(inventory["regularFileCount"], len(inventory["records"]))
            self.assertTrue(all(row["mode"] & 0o222 == 0 for row in inventory["records"]))
            pip_calls = [argv for argv, _env in calls if "pip" in argv]
            self.assertEqual(len(pip_calls), 1)
            self.assertIn("--no-index", pip_calls[0])
            self.assertIn("--require-hashes", pip_calls[0])
            self.assertIn("--only-binary=:all:", pip_calls[0])
            self.assertTrue(all("PYTHONPATH" not in env for _argv, env in calls))


class OptimizationTests(unittest.TestCase):
    def test_production_has_no_assert_and_optimized_failures_remain_active(self) -> None:
        tree = ast.parse((ROOT / receipt.PRODUCER_RELATIVE).read_text(encoding="utf-8"))
        self.assertFalse(any(isinstance(node, ast.Assert) for node in ast.walk(tree)))
        code = ("import sys;from pathlib import Path;sys.path.insert(0,str(Path.cwd()/'scripts'));"
                "import build_service_v1_b1_r4_server_receipt as r;r.require(False,'active')")
        result = subprocess.run([sys.executable, "-O", "-c", code], cwd=ROOT,
                                capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("active", result.stderr)


if __name__ == "__main__":
    unittest.main()
