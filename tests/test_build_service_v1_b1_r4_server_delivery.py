from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
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


builder = load_module("delivery_builder_under_test", "scripts/build_service_v1_b1_r4_server_delivery.py")


def write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def write_json(path: Path, value: object) -> None:
    write(path, json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode() + b"\n")


def pin(path: Path, logical: str) -> dict[str, object]:
    data = path.read_bytes()
    return {"path": logical, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def make_image_archive(path: Path, image_id: str = builder.IMAGE_ID) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps({"manifests": [{"digest": image_id}]}, separators=(",", ":")).encode()
    with tarfile.open(path, "w") as archive:
        info = tarfile.TarInfo("index.json")
        info.size = len(raw)
        archive.addfile(info, io.BytesIO(raw))


class DeliveryFixture:
    def __init__(self) -> None:
        if os.name == "nt":
            self._temporary = tempfile.TemporaryDirectory()
        else:
            self._temporary = tempfile.TemporaryDirectory(dir="/home/kingc")
        self.base = Path(self._temporary.name).resolve()
        workspace = self.base / "workspace"
        self.roots = builder.Roots(
            workspace / "FEELM-standalone", workspace / "S15P21E106",
            self.base / "evidence", self.base / "delivery"
        )
        for root in self.roots.mapping.values():
            root.mkdir(parents=True)
        self.profile_path = self.roots.standalone / builder.PROFILE_PATH
        self.plan_path = self.roots.standalone / builder.PLAN_PATH
        self.implementation_path = self.roots.wsl_evidence / builder.IMPLEMENTATION_REVIEW_NAME
        self.manifest_path = self.roots.wsl_evidence / builder.DELIVERY_NAME
        self.review_path = self.roots.wsl_evidence / (builder.RUN_ID + "-delivery-manifest-result-review.json")
        self._make_profile_and_review()
        self.contract = self._make_contract()

    def close(self) -> None:
        self._temporary.cleanup()

    def _make_profile_and_review(self) -> None:
        profile: dict[str, object] = {
            "schemaVersion": "feelm-service-v1-b1-execution-profile/2",
            "status": "DRAFT_REQUIRES_INDEPENDENT_REVIEW",
            "runId": builder.RUN_ID,
            "profileId": builder.PROFILE_ID,
        }
        for field in (
            "serverPreconditions", "hostRuntime", "runnerSupervisor", "evaluationSupervisor",
            "docker", "spark", "timeoutsSeconds", "maintenanceWindowsSeconds",
        ):
            profile[field] = {"fixture": field}
        write_json(self.profile_path, profile)
        write(self.plan_path, b"fixture plan\n")
        design_pin = {"path": "evidence/" + builder.DESIGN_REVIEW_NAME,
                      "bytes": 1, "sha256": "1" * 64}

        source_pins = []
        for index, relative in enumerate(sorted(builder.SOURCE_FILE_SET)):
            path = self.roots.standalone / relative
            write(path, f"source-{index}\n".encode())
            source_pins.append(pin(path, relative))
        test_pins = []
        for index, relative in enumerate(sorted(builder.TEST_FILE_SET)):
            path = self.roots.standalone / relative
            write(path, f"test-{index}\n".encode())
            test_pins.append(pin(path, relative))

        runtime_paths = [
            "requirements/service-v1-b1-r4-host-runtime.lock",
            "runtime/service-v1-b1-r4-wheelhouse-manifest.json",
            "runtime/feelm-rec046-spark-local.tar",
            *(f"runtime/service-v1-b1-r4-wheelhouse/wheel-{index}.whl" for index in range(7)),
        ]
        runtime_pins = []
        for index, relative in enumerate(runtime_paths):
            path = self.roots.standalone / relative
            if relative.endswith(".tar"):
                make_image_archive(path)
            else:
                write(path, f"runtime-{index}\n".encode())
            runtime_pins.append(pin(path, relative))

        evidence_dir = self.roots.wsl_delivery / ".implementation-test-evidence"
        evidence_dir.mkdir()
        test_runs: dict[str, object] = {}
        for kind in builder.TEST_RUN_KINDS:
            evidence = evidence_dir / f"{kind}.json"
            write_json(evidence, {"kind": kind})
            test_runs[kind] = {
                "argv": ["python", kind], "exitCode": 0, "stdoutSha256": "2" * 64,
                "stderrSha256": "3" * 64, "evidence": pin(evidence, evidence.as_posix()),
            }
        review = {
            "schemaVersion": builder.IMPLEMENTATION_REVIEW_SCHEMA,
            "status": "PASS", "runId": builder.RUN_ID, "profileId": builder.PROFILE_ID,
            "createdAt": "2026-09-14T00:00:00.000000Z",
            "reviewer": {"kind": "INDEPENDENT_IMPLEMENTATION_REVIEWER", "sessionId": "fixture",
                         "host": "fixture", "processId": 1},
            "target": {
                "designReview": design_pin, "plan": pin(self.plan_path, builder.PLAN_PATH),
                "profile": pin(self.profile_path, builder.PROFILE_PATH),
                "sourceFiles": source_pins, "testFiles": test_pins, "runtimeFiles": runtime_pins,
            },
            "checks": {key: True for key in builder.IMPLEMENTATION_CHECK_KEYS},
            "testRuns": test_runs,
            "decision": dict(builder.IMPLEMENTATION_DECISION),
            "dependencyFingerprint": "4" * 64,
            "publication": {},
        }
        write_json(self.implementation_path, review)

    def spec(self, group: str, logical: str, source_root: str, relative: str,
             *, kind: str = "regular-file", virtual: bytes = b""):
        if kind == "docker-image-contract":
            size, digest = len(virtual), hashlib.sha256(virtual).hexdigest()
        else:
            data = self.roots.mapping[source_root].joinpath(*Path(relative).parts).read_bytes()
            size, digest = len(data), hashlib.sha256(data).hexdigest()
        destination = (
            "FEELM-standalone/" + relative if source_root == "standalone" else
            "S15P21E106/" + relative if source_root == "team" else
            builder.RUN_ID + "/evidence/" + Path(relative).name if source_root == "wslEvidence" else
            builder.RUN_ID + "/" + (logical if source_root == "virtual" else relative)
        )
        return builder.SourceSpec(group, logical, source_root, relative, destination, size, digest, kind, virtual)

    def _make_contract(self):
        paths = {
            "team": ("model.bin", b"model\n"),
            "standalone": ("score.bin", b"score\n"),
        }
        for root_name, (relative, data) in paths.items():
            write(self.roots.mapping[root_name] / relative, data)
        write(self.roots.standalone / "worker.py", b"worker\n")
        write(self.roots.standalone / "evaluation.bin", b"evaluation\n")
        write(self.roots.standalone / "ancestor.json", b"{}\n")
        specs = (
            self.spec("modelInputs", "contract/service-v1.json", "team", "model.bin"),
            self.spec("scoreInputs", "source/natural-score.parquet", "standalone", "score.bin"),
            self.spec("workerRuntime", "implementation/worker.py", "standalone", "worker.py"),
            self.spec("evaluationInputs", "evaluation/input.bin", "standalone", "evaluation.bin"),
            self.spec("controlAndImplementation", "control/scripts/build_service_v1_b1_r4_server_delivery.py",
                      "standalone", "scripts/build_service_v1_b1_r4_server_delivery.py"),
            self.spec("controlAndImplementation", "control/evidence/" + builder.IMPLEMENTATION_REVIEW_NAME,
                      "wslEvidence", builder.IMPLEMENTATION_REVIEW_NAME),
            self.spec("controlAndImplementation", "runtime/feelm-rec046-spark-local.tar", "standalone",
                      "runtime/feelm-rec046-spark-local.tar"),
            self.spec("ancestorEvidence", "ancestor/fixture.json", "standalone", "ancestor.json"),
        )
        return builder.DeliveryContract(
            specs, (), dict(builder.SEMANTIC_FACTS),
            "control/scripts/build_service_v1_b1_r4_server_delivery.py",
            "control/evidence/" + builder.IMPLEMENTATION_REVIEW_NAME,
        )

    @property
    def implementation_sha(self) -> str:
        return hashlib.sha256(self.implementation_path.read_bytes()).hexdigest()

    def publication_evidence(self, fingerprint: str = "5" * 64) -> dict[str, object]:
        final = self.manifest_path.as_posix()
        return {
            "schemaVersion": "feelm-service-v1-b1-r4-publication-evidence/1",
            "mode": "LINUX_RENAME_NOREPLACE", "role": "PRODUCER", "finalPath": final,
            "failurePath": (self.manifest_path.parent / builder.DELIVERY_FAILURE_NAME).as_posix(),
            "claimPath": (self.manifest_path.parent / ("." + self.manifest_path.name + ".claim")).as_posix(),
            "tempPath": (self.manifest_path.parent / ("." + self.manifest_path.name + ".tmp-fixture")).as_posix(),
            "token": "0" * 32, "claimStDev": 1, "claimStIno": 1,
            "filesystemType": "ext2/ext3",
            "publisher": pin(REPO / "scripts/service_v1_b1_r4_publication.py",
                             "scripts/service_v1_b1_r4_publication.py"),
            "dependencyFingerprintAtAcquire": fingerprint,
            "dependencyFingerprintBeforeRename": fingerprint,
            "renameNoReplaceProbe": True,
            "requiredPostconditions": {
                "publishedBytesRehashRequired": True, "fileAndParentFsyncRequired": True,
                "dependencyFingerprintStableRequired": True, "claimIdentityMatchRequired": True,
                "claimRemovalRequired": True,
            },
        }

    def compose(self, contract=None):
        chosen = contract or self.contract
        _preflight, fingerprint = builder.compose_manifest(
            self.roots, chosen, self.profile_path, self.implementation_path,
            self.implementation_sha, self.publication_evidence(), created_at="2026-09-14T00:00:00.000000Z",
        )
        return builder.compose_manifest(
            self.roots, chosen, self.profile_path, self.implementation_path,
            self.implementation_sha, self.publication_evidence(fingerprint),
            created_at="2026-09-14T00:00:00.000000Z",
        )


class DeliveryBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = DeliveryFixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def test_manifest_has_exact_groups_references_digests_and_closed_authorization(self) -> None:
        manifest, fingerprint = self.fixture.compose()
        self.assertEqual(set(manifest), builder.TOP_LEVEL_KEYS)
        self.assertEqual(manifest["deliverySetSha256"], fingerprint)
        self.assertEqual(manifest["authorization"]["state"], "DELIVERY_AUDIT_PENDING")
        self.assertFalse(any(value is True for key, value in manifest["authorization"].items()
                             if key != "state"))
        self.assertEqual([item["referenceId"] for item in manifest["crossGroupReferences"]],
                         ["evaluation.artifact_manifest", "evaluation.score_axis"])
        owned = [record for group in builder.GROUPS for record in
                 (manifest["ancestorEvidence"]["records"] if group == "ancestorEvidence" else manifest[group])]
        self.assertEqual(len({record["recordId"] for record in owned}), len(owned))
        self.assertEqual(len({record["destinationRelativePath"] for record in owned}), len(owned))

    def test_missing_extra_moved_digest_overlap_alias_and_special_fail_closed(self) -> None:
        cases = []
        missing_contract = copy.deepcopy(self.fixture.contract)
        cases.append(("missing", builder.DeliveryContract(
            missing_contract.specs, (builder.ExactDirectory("standalone", "exact", ("required",)),),
            missing_contract.semantic_facts, missing_contract.producer_logical_path,
            missing_contract.implementation_review_logical_path), "BLOCKED"))
        (self.fixture.roots.standalone / "exact").mkdir()

        first = self.fixture.contract.specs[0]
        moved = copy.copy(first)
        object.__setattr__(moved, "destination_relative_path", "elsewhere/model.bin")
        cases.append(("moved", builder.DeliveryContract(
            (moved, *self.fixture.contract.specs[1:]), (), self.fixture.contract.semantic_facts,
            self.fixture.contract.producer_logical_path, self.fixture.contract.implementation_review_logical_path), "BLOCKED_MOVED"))

        bad_digest = copy.copy(first)
        object.__setattr__(bad_digest, "expected_sha256", "f" * 64)
        cases.append(("digest", builder.DeliveryContract(
            (bad_digest, *self.fixture.contract.specs[1:]), (), self.fixture.contract.semantic_facts,
            self.fixture.contract.producer_logical_path, self.fixture.contract.implementation_review_logical_path), "BLOCKED"))

        cases.append(("overlap", builder.DeliveryContract(
            (*self.fixture.contract.specs, first), (), self.fixture.contract.semantic_facts,
            self.fixture.contract.producer_logical_path, self.fixture.contract.implementation_review_logical_path), "BLOCKED"))

        for name, contract, expected_error in cases:
            with self.subTest(name=name):
                with self.assertRaisesRegex(builder.DeliveryError, expected_error):
                    self.fixture.compose(contract)

        source = self.fixture.roots.standalone / "score.bin"
        alias = self.fixture.roots.standalone / "score-alias.bin"
        os.link(source, alias)
        with self.assertRaisesRegex(builder.DeliveryError, "BLOCKED_ALIAS"):
            self.fixture.compose()
        alias.unlink()

        if hasattr(os, "mkfifo"):
            exact = self.fixture.roots.standalone / "fifo-dir"
            exact.mkdir()
            os.mkfifo(exact / "item")
            contract = builder.DeliveryContract(
                self.fixture.contract.specs, (builder.ExactDirectory("standalone", "fifo-dir", ("item",)),),
                self.fixture.contract.semantic_facts, self.fixture.contract.producer_logical_path,
                self.fixture.contract.implementation_review_logical_path)
            with self.assertRaisesRegex(builder.DeliveryError, "BLOCKED_SPECIAL_FILE"):
                self.fixture.compose(contract)

    def test_bad_ancestry_shape_and_implementation_authorization_are_rejected(self) -> None:
        facts = dict(self.fixture.contract.semantic_facts)
        facts["extra"] = True
        contract = builder.DeliveryContract(
            self.fixture.contract.specs, (), facts, self.fixture.contract.producer_logical_path,
            self.fixture.contract.implementation_review_logical_path)
        with self.assertRaisesRegex(builder.DeliveryError, "BLOCKED_ANCESTRY"):
            self.fixture.compose(contract)

        review = json.loads(self.fixture.implementation_path.read_text())
        review["decision"]["deliveryBuildEligible"] = False
        write_json(self.fixture.implementation_path, review)
        with self.assertRaisesRegex(builder.DeliveryError, "BLOCKED_GATE"):
            self.fixture.compose()

    def test_docker_archive_identity_is_checked(self) -> None:
        archive = self.fixture.roots.standalone / "runtime/feelm-rec046-spark-local.tar"
        make_image_archive(archive, "sha256:" + "9" * 64)
        specs = list(self.fixture.contract.specs)
        index = next(i for i, item in enumerate(specs) if item.logical_path == "runtime/feelm-rec046-spark-local.tar")
        data = archive.read_bytes()
        specs[index] = copy.copy(specs[index])
        object.__setattr__(specs[index], "expected_bytes", len(data))
        object.__setattr__(specs[index], "expected_sha256", hashlib.sha256(data).hexdigest())
        contract = builder.DeliveryContract(tuple(specs), (), self.fixture.contract.semantic_facts,
                                            self.fixture.contract.producer_logical_path,
                                            self.fixture.contract.implementation_review_logical_path)
        with self.assertRaisesRegex(builder.DeliveryError, "BLOCKED_IMAGE"):
            self.fixture.compose(contract)

    @unittest.skipIf(os.name == "nt", "uses Linux directory fsync flags")
    def test_staged_docker_image_id_is_the_exact_71_bytes(self) -> None:
        builder._prepare_docker_id(self.fixture.roots.wsl_delivery)
        path = self.fixture.roots.wsl_delivery / "runtime/docker-image-id"
        self.assertEqual(path.read_bytes(), builder.IMAGE_ID.encode("ascii"))
        self.assertEqual(path.stat().st_size, 71)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                         "07666208de67cba550e28294fc88c22981071e2026894da726c12ea42c806137")

    @unittest.skipIf(os.name == "nt", "requires Linux ext4 renameat2")
    def test_builder_publishes_plan_exact_manifest_without_failure_or_residue(self) -> None:
        write_json(
            self.fixture.roots.wsl_evidence / builder.DESIGN_REVIEW_NAME,
            {"status": "PASS", "decision": {"implementationEligible": True}},
        )
        result, payload = builder.build_and_publish(
            self.fixture.roots, self.fixture.contract, self.fixture.profile_path,
            self.fixture.implementation_path, self.fixture.implementation_sha,
            self.fixture.manifest_path, builder.DELIVERY_COMPLETED_CHILDREN,
        )
        self.assertEqual(result.path, self.fixture.manifest_path.as_posix())
        self.assertEqual(result.sha256, hashlib.sha256(self.fixture.manifest_path.read_bytes()).hexdigest())
        self.assertEqual(payload["publication"]["failurePath"],
                         (self.fixture.roots.wsl_evidence / builder.DELIVERY_FAILURE_NAME).as_posix())
        self.assertFalse((self.fixture.roots.wsl_evidence / builder.DELIVERY_FAILURE_NAME).exists())
        hidden = [path.name for path in self.fixture.roots.wsl_evidence.iterdir()
                  if path.name.startswith("." + builder.DELIVERY_NAME)]
        self.assertEqual(hidden, [])

    @unittest.skipIf(os.name == "nt", "requires Linux ext4 renameat2")
    def test_invalid_implementation_review_publishes_exact_failure_before_staging_mutation(self) -> None:
        write_json(
            self.fixture.roots.wsl_evidence / builder.DESIGN_REVIEW_NAME,
            {"status": "PASS", "decision": {"implementationEligible": True}},
        )
        review = json.loads(self.fixture.implementation_path.read_text(encoding="utf-8"))
        review["decision"]["deliveryBuildEligible"] = False
        write_json(self.fixture.implementation_path, review)
        current_sha = hashlib.sha256(self.fixture.implementation_path.read_bytes()).hexdigest()
        arguments = [
            "--standalone-root", str(self.fixture.roots.standalone),
            "--team-root", str(self.fixture.roots.team),
            "--evidence-root", str(self.fixture.roots.wsl_evidence),
            "--delivery-root", str(self.fixture.roots.wsl_delivery),
            "--implementation-review", str(self.fixture.implementation_path),
            "--expected-implementation-review-sha256", current_sha,
        ]
        with self.assertRaisesRegex(builder.DeliveryError, "FAILED_PUBLISHED"):
            builder.main(arguments)
        self.assertFalse((self.fixture.roots.wsl_delivery / "runtime").exists())
        self.assertFalse(self.fixture.manifest_path.exists())
        failure_path = self.fixture.roots.wsl_evidence / builder.DELIVERY_FAILURE_NAME
        self.assertTrue(failure_path.is_file())
        failure = json.loads(failure_path.read_text(encoding="utf-8"))
        self.assertEqual(set(failure), builder.PHASE_FAILURE_KEYS)
        self.assertEqual(failure["schemaVersion"], builder.PHASE_FAILURE_SCHEMA)
        self.assertEqual(failure["failureStage"], "PRE_CONTAINER")
        self.assertEqual(failure["failureKind"], "AUDIT")
        self.assertTrue(failure["command"]["intendedArgv"])
        self.assertEqual(failure["command"]["actualArgv"], [])
        self.assertEqual(failure["command"]["dockerArgv"], [])
        self.assertEqual(failure["command"]["sparkSubmitArgv"], [])
        self.assertEqual([item["kind"] for item in failure["authorizationEvidence"]],
                         ["DESIGN_REVIEW"])
        self.assertTrue(failure["outputState"]["nextPhaseBlocked"])
        self.assertFalse(failure["readyForService"])
        self.assertFalse(failure["deploymentAuthorized"])
        self.assertFalse(any(path.name.startswith("." + builder.DELIVERY_NAME)
                             for path in self.fixture.roots.wsl_evidence.iterdir()))

    @unittest.skipIf(os.name == "nt", "requires Linux ext4 renameat2")
    def test_main_pre_container_failure_records_no_executed_command(self) -> None:
        write_json(
            self.fixture.roots.wsl_evidence / builder.DESIGN_REVIEW_NAME,
            {"status": "PASS", "decision": {"implementationEligible": True}},
        )
        arguments = [
            "--standalone-root", str(self.fixture.roots.standalone),
            "--team-root", str(self.fixture.roots.team),
            "--evidence-root", str(self.fixture.roots.wsl_evidence),
            "--delivery-root", str(self.fixture.roots.wsl_delivery),
            "--implementation-review", str(self.fixture.implementation_path),
            "--expected-implementation-review-sha256", self.fixture.implementation_sha,
        ]
        with mock.patch.object(builder, "default_contract", return_value=self.fixture.contract), \
             mock.patch.object(
                 builder,
                 "_prepare_docker_id",
                 side_effect=builder.DeliveryError("BLOCKED_IO", "forced pre-container failure"),
             ):
            with self.assertRaisesRegex(builder.DeliveryError, "FAILED_PUBLISHED"):
                builder.main(arguments)
        failure = json.loads(
            (self.fixture.roots.wsl_evidence / builder.DELIVERY_FAILURE_NAME).read_text(encoding="utf-8")
        )
        self.assertEqual(failure["failureStage"], "PRE_CONTAINER")
        self.assertEqual(failure["failureKind"], "IO")
        self.assertTrue(failure["command"]["intendedArgv"])
        self.assertEqual(failure["command"]["actualArgv"], [])
        self.assertEqual(failure["command"]["dockerArgv"], [])
        self.assertEqual(failure["command"]["sparkSubmitArgv"], [])
        self.assertFalse((self.fixture.roots.wsl_delivery / "runtime").exists())
        self.assertFalse(self.fixture.manifest_path.exists())
        self.assertFalse(any(
            path.name.startswith("." + builder.DELIVERY_NAME)
            for path in self.fixture.roots.wsl_evidence.iterdir()
        ))

    @unittest.skipIf(os.name == "nt", "requires Linux ext4 renameat2")
    def test_missing_reviewed_dependency_uses_hashable_failure_state_without_staging(self) -> None:
        write_json(
            self.fixture.roots.wsl_evidence / builder.DESIGN_REVIEW_NAME,
            {"status": "PASS", "decision": {"implementationEligible": True}},
        )
        runtime_lock = self.fixture.roots.standalone / builder.RUNTIME_LOCK_PATH
        runtime_lock.unlink()
        arguments = [
            "--standalone-root", str(self.fixture.roots.standalone),
            "--team-root", str(self.fixture.roots.team),
            "--evidence-root", str(self.fixture.roots.wsl_evidence),
            "--delivery-root", str(self.fixture.roots.wsl_delivery),
            "--implementation-review", str(self.fixture.implementation_path),
            "--expected-implementation-review-sha256", self.fixture.implementation_sha,
        ]
        with self.assertRaisesRegex(builder.DeliveryError, "FAILED_PUBLISHED"):
            builder.main(arguments)
        self.assertFalse((self.fixture.roots.wsl_delivery / "runtime").exists())
        failure_path = self.fixture.roots.wsl_evidence / builder.DELIVERY_FAILURE_NAME
        failure = json.loads(failure_path.read_text(encoding="utf-8"))
        runtime_states = [
            item for item in failure["phaseInputLock"]["dependencyState"]["sourceStates"]
            if item["logicalPath"] == builder.RUNTIME_LOCK_PATH
        ]
        self.assertEqual(len(runtime_states), 1)
        self.assertEqual(runtime_states[0]["state"], "UNAVAILABLE")
        self.assertIsNone(failure["delivery"]["runtimeLock"])
        self.assertEqual(
            set(failure["phaseInputLock"]["groups"]),
            set(builder.GROUPS),
        )
        self.assertEqual(
            failure["phaseInputLock"]["resolution"]["state"],
            "REVIEWED_EXPECTATIONS",
        )
        locked_runtime = {
            item["sourceRelativePath"]
            for item in failure["phaseInputLock"]["groups"]["controlAndImplementation"]
        }
        self.assertIn(builder.RUNTIME_LOCK_PATH, locked_runtime)
        self.assertFalse(any(path.name.startswith("." + builder.DELIVERY_NAME)
                             for path in self.fixture.roots.wsl_evidence.iterdir()))

    @unittest.skipIf(os.name == "nt", "requires Linux ext4 renameat2")
    def test_post_acquire_pre_temp_error_publishes_failure_with_owned_claim(self) -> None:
        write_json(
            self.fixture.roots.wsl_evidence / builder.DESIGN_REVIEW_NAME,
            {"status": "PASS", "decision": {"implementationEligible": True}},
        )
        original_compose = builder.compose_manifest
        calls = 0

        def fail_second_compose(*arguments, **keywords):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise builder.DeliveryError("BLOCKED_CONTRACT", "second composition failed")
            return original_compose(*arguments, **keywords)

        with mock.patch.object(builder, "compose_manifest", fail_second_compose):
            with self.assertRaisesRegex(builder.DeliveryError, "FAILED_PUBLISHED"):
                builder.build_and_publish(
                    self.fixture.roots, self.fixture.contract, self.fixture.profile_path,
                    self.fixture.implementation_path, self.fixture.implementation_sha,
                    self.fixture.manifest_path, builder.DELIVERY_COMPLETED_CHILDREN,
                )
        failure_path = self.fixture.roots.wsl_evidence / builder.DELIVERY_FAILURE_NAME
        self.assertTrue(failure_path.is_file())
        failure = json.loads(failure_path.read_text(encoding="utf-8"))
        self.assertEqual(failure["failureStage"], "INPUT_LOCK")
        self.assertEqual(failure["failureKind"], "CONTRACT")
        self.assertFalse(self.fixture.manifest_path.exists())
        self.assertFalse(any(path.name.startswith("." + builder.DELIVERY_NAME)
                             for path in self.fixture.roots.wsl_evidence.iterdir()))

    @unittest.skipIf(os.name == "nt", "requires Linux ext4 renameat2")
    def test_completed_child_cli_cannot_override_frozen_predecessors(self) -> None:
        with self.assertRaisesRegex(builder.DeliveryError, "BLOCKED_NAMESPACE"):
            builder.build_and_publish(
                self.fixture.roots, self.fixture.contract, self.fixture.profile_path,
                self.fixture.implementation_path, self.fixture.implementation_sha,
                self.fixture.manifest_path, [builder.IMPLEMENTATION_REVIEW_NAME],
            )
        self.assertFalse(self.fixture.manifest_path.exists())
        self.assertFalse((self.fixture.roots.wsl_evidence / builder.DELIVERY_FAILURE_NAME).exists())


if __name__ == "__main__":
    unittest.main()
