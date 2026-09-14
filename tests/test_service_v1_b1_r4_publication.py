from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock
import uuid


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "service_v1_b1_r4_publication.py"
MODULE_NAME = "service_v1_b1_r4_publication"


def _load_module():
    existing = sys.modules.get(MODULE_NAME)
    if existing is not None:
        return existing
    specification = importlib.util.spec_from_file_location(MODULE_NAME, MODULE_PATH)
    if specification is None or specification.loader is None:
        raise RuntimeError("cannot load publication module")
    module = importlib.util.module_from_spec(specification)
    sys.modules[MODULE_NAME] = module
    specification.loader.exec_module(module)
    return module


publication = _load_module()


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _race_worker(parent_text: str, barrier, results) -> None:
    module = _load_module()
    parent = Path(parent_text)
    final = parent / "race-result-review.json"
    failure = parent / "race-result-review-failure.json"
    fingerprint = "3" * 64
    try:
        barrier.wait(timeout=30)
        lease = module.acquire_publication(
            "REVIEWER",
            "implementation-review",
            final,
            failure,
            module.ExpectedNamespace((), fingerprint),
        )
        payload = {
            "schemaVersion": "race/1",
            "status": "PASS",
            "publication": lease.publication_evidence,
        }
        with open(lease.temp_path, "xb") as stream:
            stream.write(_json_bytes(payload))
            stream.flush()
            os.fsync(stream.fileno())
        pin = module.publish_success(lease, lease.temp_path, fingerprint, lambda: fingerprint)
        module.release_verified_claim(lease, pin, fingerprint)
        results.put(("OWNER", pin.sha256))
    except BaseException as error:
        results.put((getattr(error, "code", type(error).__name__), str(error)))


@unittest.skipUnless(os.name == "posix", "canonical publication requires Linux")
class PublicationIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        home = Path.home()
        self.sandbox = Path(tempfile.mkdtemp(prefix=".feelm-r4-publication-test-", dir=home))

    def tearDown(self) -> None:
        if os.path.lexists(self.sandbox):
            shutil.rmtree(self.sandbox)

    def namespace(self, fingerprint: str = "1" * 64):
        return publication.ExpectedNamespace((), fingerprint)

    def write_staged_json(self, lease, **extra: object) -> None:
        payload = {"schemaVersion": "test/1", "publication": lease.publication_evidence}
        payload.update(extra)
        with open(lease.temp_path, "xb") as stream:
            stream.write(_json_bytes(payload))
            stream.flush()
            os.fsync(stream.fileno())

    def assert_claim_preserved(self, lease) -> None:
        self.assertTrue(lease.claim_path.is_file())
        claim = json.loads(lease.claim_path.read_text(encoding="utf-8"))
        self.assertEqual(claim["token"], lease.token)
        self.assertEqual((lease.claim_path.stat().st_dev, lease.claim_path.stat().st_ino),
                         (lease.claim_st_dev, lease.claim_st_ino))

    def test_delivery_build_accepts_only_the_plan_exact_terminal_pair(self) -> None:
        fingerprint = "d" * 64
        final = self.sandbox / (publication.RUN_ID + "-delivery-manifest.json")
        failure = self.sandbox / (publication.RUN_ID + "-delivery-failure.json")
        lease = publication.acquire_publication(
            "PRODUCER", "delivery-build", final, failure, self.namespace(fingerprint)
        )
        self.write_staged_json(lease, status="DELIVERY_AUDIT_PENDING")
        pin = publication.publish_success(lease, lease.temp_path, fingerprint, lambda: fingerprint)
        publication.release_verified_claim(lease, pin, fingerprint)
        self.assertTrue(final.is_file())
        self.assertFalse(failure.exists())

        other = Path(tempfile.mkdtemp(prefix=".feelm-r4-publication-test-", dir=Path.home()))
        try:
            wrong_final = other / (publication.RUN_ID + "-delivery-manifest.json")
            wrong_failure = other / (publication.RUN_ID + "-delivery-manifest-failure.json")
            with self.assertRaises(publication.PublicationError) as caught:
                publication.acquire_publication(
                    "PRODUCER", "delivery-build", wrong_final, wrong_failure,
                    publication.ExpectedNamespace((), fingerprint),
                )
            self.assertEqual(caught.exception.code, "BLOCKED_PATH")
            self.assertEqual(tuple(other.iterdir()), ())
        finally:
            shutil.rmtree(other)

    def test_file_success_uses_exact_evidence_and_keeps_claim_until_release(self) -> None:
        fingerprint = "1" * 64
        final = self.sandbox / "result-review.json"
        failure = self.sandbox / "result-review-failure.json"
        lease = publication.acquire_publication(
            "REVIEWER", "implementation-review", final, failure, self.namespace(fingerprint)
        )

        evidence = lease.publication_evidence
        evidence["mode"] = "tampered-copy"
        self.assertEqual(lease.publication_evidence["mode"], "LINUX_RENAME_NOREPLACE")
        evidence = lease.publication_evidence
        self.assertEqual(
            set(evidence),
            {
                "schemaVersion", "mode", "role", "finalPath", "failurePath",
                "claimPath", "tempPath", "token", "claimStDev", "claimStIno",
                "filesystemType", "publisher", "dependencyFingerprintAtAcquire",
                "dependencyFingerprintBeforeRename", "renameNoReplaceProbe",
                "requiredPostconditions",
            },
        )
        self.assertEqual(evidence["filesystemType"], "ext2/ext3")
        self.assertTrue(evidence["renameNoReplaceProbe"])
        claim = json.loads(lease.claim_path.read_text(encoding="utf-8"))
        self.assertEqual(
            set(claim),
            {
                "schemaVersion", "role", "runId", "profileId", "phase", "finalPath",
                "failurePath", "token", "pid", "hostname", "publisherPath",
                "publisherSha256", "startedAt",
            },
        )
        self.write_staged_json(lease, status="PASS")

        pin = publication.publish_success(
            lease, lease.temp_path, fingerprint, lambda: fingerprint
        )
        self.assertTrue(final.is_file())
        self.assertTrue(lease.claim_path.is_file())
        self.assertFalse(os.path.lexists(lease.temp_path))
        self.assertFalse(os.path.lexists(failure))
        self.assertEqual(pin.path, str(final))
        self.assertEqual(pin.bytes, final.stat().st_size)
        self.assertEqual(pin.sha256, hashlib.sha256(final.read_bytes()).hexdigest())

        publication.release_verified_claim(lease, pin, fingerprint)
        self.assertFalse(os.path.lexists(lease.claim_path))
        self.assertEqual(tuple(path.name for path in self.sandbox.iterdir()), (final.name,))

    def test_directory_success_returns_manifest_pin_and_fsyncs_tree(self) -> None:
        fingerprint = "2" * 64
        final = self.sandbox / "fit"
        failure = self.sandbox / "fit-failure.json"
        lease = publication.acquire_publication(
            "PRODUCER", "fit", final, failure, self.namespace(fingerprint)
        )
        lease.temp_path.mkdir()
        manifest = {
            "schemaVersion": "fit-manifest/1",
            "status": "PASS",
            "publication": lease.publication_evidence,
        }
        (lease.temp_path / "manifest.json").write_bytes(_json_bytes(manifest))
        nested = lease.temp_path / "model" / "native"
        nested.mkdir(parents=True)
        (nested / "part-00000").write_bytes(b"model-bytes\n")

        pin = publication.publish_success(
            lease, lease.temp_path, fingerprint, lambda: fingerprint
        )
        self.assertEqual(pin.path, str(final / "manifest.json"))
        self.assertTrue((final / "model" / "native" / "part-00000").is_file())
        self.assertTrue(lease.claim_path.is_file())
        publication.release_verified_claim(lease, pin, fingerprint)
        self.assertFalse(os.path.lexists(lease.claim_path))

    def test_handled_failure_publishes_only_failure(self) -> None:
        fingerprint = "4" * 64
        final = self.sandbox / "score"
        failure = self.sandbox / "score-failure.json"
        lease = publication.acquire_publication(
            "PRODUCER", "score", final, failure, self.namespace(fingerprint)
        )
        payload = {
            "schemaVersion": "failure/1",
            "status": "FAILED",
            "publication": lease.publication_evidence,
        }
        pin = publication.publish_handled_failure(
            lease, payload, fingerprint, lambda: fingerprint
        )
        self.assertFalse(os.path.lexists(final))
        self.assertTrue(failure.is_file())
        self.assertTrue(lease.claim_path.is_file())
        publication.release_verified_claim(lease, pin, fingerprint)
        self.assertFalse(os.path.lexists(lease.claim_path))

    def test_regular_file_replace_during_callback_is_blocked_with_claim_and_temp(self) -> None:
        fingerprint = "e" * 64
        final = self.sandbox / "result-review.json"
        failure = self.sandbox / "result-review-failure.json"
        lease = publication.acquire_publication(
            "REVIEWER", "implementation-review", final, failure, self.namespace(fingerprint)
        )
        self.write_staged_json(lease, status="PASS")
        attacker = self.sandbox.parent / f".feelm-r4-attacker-{uuid.uuid4().hex}.json"
        attacker.write_bytes(_json_bytes({
            "schemaVersion": "test/1",
            "status": "TAMPERED",
            "publication": lease.publication_evidence,
        }))
        try:
            def replace_staging() -> str:
                os.replace(attacker, lease.temp_path)
                return fingerprint

            with self.assertRaises(publication.PublicationError) as caught:
                publication.publish_success(
                    lease, lease.temp_path, fingerprint, replace_staging
                )
            self.assertEqual(caught.exception.code, "BLOCKED_STAGING_DRIFT")
            self.assert_claim_preserved(lease)
            self.assertTrue(lease.temp_path.is_file())
            self.assertFalse(os.path.lexists(final))
            self.assertFalse(os.path.lexists(failure))
        finally:
            if os.path.lexists(attacker):
                attacker.unlink()

    def test_regular_file_in_place_mutation_during_callback_is_blocked(self) -> None:
        fingerprint = "f" * 64
        final = self.sandbox / "result-review.json"
        failure = self.sandbox / "result-review-failure.json"
        lease = publication.acquire_publication(
            "REVIEWER", "implementation-review", final, failure, self.namespace(fingerprint)
        )
        self.write_staged_json(lease, status="PASS")
        tampered = _json_bytes({
            "schemaVersion": "test/1",
            "status": "EVIL",
            "publication": lease.publication_evidence,
        })

        def mutate_staging() -> str:
            with lease.temp_path.open("r+b") as stream:
                stream.seek(0)
                stream.write(tampered)
                stream.truncate()
                stream.flush()
                os.fsync(stream.fileno())
            return fingerprint

        with self.assertRaises(publication.PublicationError) as caught:
            publication.publish_success(lease, lease.temp_path, fingerprint, mutate_staging)
        self.assertEqual(caught.exception.code, "BLOCKED_STAGING_DRIFT")
        self.assert_claim_preserved(lease)
        self.assertTrue(lease.temp_path.is_file())
        self.assertFalse(os.path.lexists(final))
        self.assertFalse(os.path.lexists(failure))

    def test_directory_swap_during_callback_is_blocked(self) -> None:
        fingerprint = "8" * 64
        final = self.sandbox / "fit"
        failure = self.sandbox / "fit-failure.json"
        lease = publication.acquire_publication(
            "PRODUCER", "fit", final, failure, self.namespace(fingerprint)
        )
        lease.temp_path.mkdir()
        (lease.temp_path / "manifest.json").write_bytes(_json_bytes({
            "schemaVersion": "fit/1", "publication": lease.publication_evidence,
        }))
        (lease.temp_path / "model").mkdir()
        (lease.temp_path / "model" / "part").write_bytes(b"ORIGINAL\n")
        attacker = self.sandbox.parent / f".feelm-r4-attacker-{uuid.uuid4().hex}"
        saved = self.sandbox.parent / f".feelm-r4-saved-{uuid.uuid4().hex}"
        attacker.mkdir()
        (attacker / "manifest.json").write_bytes(_json_bytes({
            "schemaVersion": "fit/1", "publication": lease.publication_evidence,
        }))
        (attacker / "model").mkdir()
        (attacker / "model" / "part").write_bytes(b"TAMPERED\n")
        try:
            def swap_directory() -> str:
                os.rename(lease.temp_path, saved)
                os.rename(attacker, lease.temp_path)
                return fingerprint

            with self.assertRaises(publication.PublicationError) as caught:
                publication.publish_success(
                    lease, lease.temp_path, fingerprint, swap_directory
                )
            self.assertEqual(caught.exception.code, "BLOCKED_STAGING_DRIFT")
            self.assert_claim_preserved(lease)
            self.assertTrue(lease.temp_path.is_dir())
            self.assertFalse(os.path.lexists(final))
        finally:
            for path in (attacker, saved):
                if os.path.lexists(path):
                    shutil.rmtree(path)

    def test_nested_file_in_place_mutation_during_callback_is_blocked(self) -> None:
        fingerprint = "b" * 64
        final = self.sandbox / "fit"
        failure = self.sandbox / "fit-failure.json"
        lease = publication.acquire_publication(
            "PRODUCER", "fit", final, failure, self.namespace(fingerprint)
        )
        lease.temp_path.mkdir()
        (lease.temp_path / "manifest.json").write_bytes(_json_bytes({
            "schemaVersion": "fit/1", "publication": lease.publication_evidence,
        }))
        nested = lease.temp_path / "model" / "native"
        nested.mkdir(parents=True)
        artifact = nested / "part-00000"
        artifact.write_bytes(b"ORIGINAL")

        def mutate_nested() -> str:
            with artifact.open("r+b") as stream:
                stream.write(b"TAMPERED")
                stream.flush()
                os.fsync(stream.fileno())
            return fingerprint

        with self.assertRaises(publication.PublicationError) as caught:
            publication.publish_success(lease, lease.temp_path, fingerprint, mutate_nested)
        self.assertEqual(caught.exception.code, "BLOCKED_STAGING_DRIFT")
        self.assert_claim_preserved(lease)
        self.assertTrue(lease.temp_path.is_dir())
        self.assertFalse(os.path.lexists(final))

    def test_handled_failure_replace_during_callback_is_blocked(self) -> None:
        fingerprint = "6" * 64
        final = self.sandbox / "score"
        failure = self.sandbox / "score-failure.json"
        lease = publication.acquire_publication(
            "PRODUCER", "score", final, failure, self.namespace(fingerprint)
        )
        payload = {
            "schemaVersion": "failure/1",
            "status": "FAILED",
            "publication": lease.publication_evidence,
        }
        attacker = self.sandbox.parent / f".feelm-r4-attacker-{uuid.uuid4().hex}.json"
        attacker.write_bytes(_json_bytes({
            **payload,
            "status": "TAMPERED",
        }))
        try:
            def replace_failure_staging() -> str:
                os.replace(attacker, lease.temp_path)
                return fingerprint

            with self.assertRaises(publication.PublicationError) as caught:
                publication.publish_handled_failure(
                    lease, payload, fingerprint, replace_failure_staging
                )
            self.assertEqual(caught.exception.code, "BLOCKED_STAGING_DRIFT")
            self.assert_claim_preserved(lease)
            self.assertTrue(lease.temp_path.is_file())
            self.assertFalse(os.path.lexists(final))
            self.assertFalse(os.path.lexists(failure))
        finally:
            if os.path.lexists(attacker):
                attacker.unlink()

    def test_post_rename_file_mutation_is_blocked_and_claim_is_preserved(self) -> None:
        fingerprint = "0" * 64
        final = self.sandbox / "result-review.json"
        failure = self.sandbox / "result-review-failure.json"
        lease = publication.acquire_publication(
            "REVIEWER", "implementation-review", final, failure, self.namespace(fingerprint)
        )
        self.write_staged_json(lease, status="PASS")
        original_rename = publication._rename_no_replace

        def rename_then_mutate(*arguments) -> None:
            original_rename(*arguments)
            final.write_bytes(_json_bytes({
                "schemaVersion": "test/1",
                "status": "EVIL",
                "publication": lease.publication_evidence,
            }))

        with mock.patch.object(publication, "_rename_no_replace", rename_then_mutate):
            with self.assertRaises(publication.PublicationError) as caught:
                publication.publish_success(
                    lease, lease.temp_path, fingerprint, lambda: fingerprint
                )
        self.assertEqual(caught.exception.code, "BLOCKED_STAGING_DRIFT")
        self.assert_claim_preserved(lease)
        self.assertTrue(final.is_file())
        self.assertFalse(os.path.lexists(lease.temp_path))

    def test_post_rename_nested_mutation_is_blocked_and_claim_is_preserved(self) -> None:
        fingerprint = "2" * 64
        final = self.sandbox / "fit"
        failure = self.sandbox / "fit-failure.json"
        lease = publication.acquire_publication(
            "PRODUCER", "fit", final, failure, self.namespace(fingerprint)
        )
        lease.temp_path.mkdir()
        (lease.temp_path / "manifest.json").write_bytes(_json_bytes({
            "schemaVersion": "fit/1", "publication": lease.publication_evidence,
        }))
        nested = lease.temp_path / "model"
        nested.mkdir()
        (nested / "part").write_bytes(b"ORIGINAL")
        original_rename = publication._rename_no_replace

        def rename_then_mutate(*arguments) -> None:
            original_rename(*arguments)
            (final / "model" / "part").write_bytes(b"TAMPERED")

        with mock.patch.object(publication, "_rename_no_replace", rename_then_mutate):
            with self.assertRaises(publication.PublicationError) as caught:
                publication.publish_success(
                    lease, lease.temp_path, fingerprint, lambda: fingerprint
                )
        self.assertEqual(caught.exception.code, "BLOCKED_STAGING_DRIFT")
        self.assert_claim_preserved(lease)
        self.assertTrue(final.is_dir())
        self.assertFalse(os.path.lexists(lease.temp_path))

    def test_dependency_drift_preserves_claim_and_temp_without_publication(self) -> None:
        fingerprint = "5" * 64
        final = self.sandbox / "selection"
        failure = self.sandbox / "selection-failure.json"
        lease = publication.acquire_publication(
            "PRODUCER", "calibrate-select", final, failure, self.namespace(fingerprint)
        )
        self.write_staged_json(lease, status="PASS")

        with self.assertRaises(publication.PublicationError) as caught:
            publication.publish_success(
                lease, lease.temp_path, fingerprint, lambda: "6" * 64
            )
        self.assertEqual(caught.exception.code, "BLOCKED_DEPENDENCY")
        self.assertTrue(lease.claim_path.is_file())
        self.assertTrue(lease.temp_path.is_file())
        self.assertFalse(os.path.lexists(final))
        self.assertFalse(os.path.lexists(failure))

    def test_hard_link_staging_is_blocked_and_preserved_for_forensics(self) -> None:
        fingerprint = "a" * 64
        final = self.sandbox / "result-review.json"
        failure = self.sandbox / "result-review-failure.json"
        lease = publication.acquire_publication(
            "REVIEWER", "implementation-review", final, failure, self.namespace(fingerprint)
        )
        source_parent = self.sandbox.parent / f".feelm-r4-hardlink-source-{uuid.uuid4().hex}"
        try:
            payload = {
                "schemaVersion": "test/1",
                "publication": lease.publication_evidence,
            }
            source_parent.write_bytes(_json_bytes(payload))
            os.link(source_parent, lease.temp_path)
            with self.assertRaises(publication.PublicationError) as caught:
                publication.publish_success(
                    lease, lease.temp_path, fingerprint, lambda: fingerprint
                )
            self.assertEqual(caught.exception.code, "BLOCKED_HARDLINK")
            self.assertTrue(lease.claim_path.is_file())
            self.assertTrue(lease.temp_path.is_file())
            self.assertFalse(os.path.lexists(final))
        finally:
            if os.path.lexists(source_parent):
                source_parent.unlink()

    def test_post_publication_dependency_drift_keeps_final_and_claim(self) -> None:
        fingerprint = "c" * 64
        final = self.sandbox / "result-review.json"
        failure = self.sandbox / "result-review-failure.json"
        lease = publication.acquire_publication(
            "REVIEWER", "implementation-review", final, failure, self.namespace(fingerprint)
        )
        self.write_staged_json(lease, status="PASS")
        pin = publication.publish_success(
            lease, lease.temp_path, fingerprint, lambda: fingerprint
        )

        with self.assertRaises(publication.PublicationError) as caught:
            publication.release_verified_claim(lease, pin, "d" * 64)
        self.assertEqual(caught.exception.code, "BLOCKED_DEPENDENCY")
        self.assertTrue(final.is_file())
        self.assertTrue(lease.claim_path.is_file())
        self.assertFalse(os.path.lexists(lease.temp_path))

    def test_nested_mutation_before_claim_release_is_blocked_by_published_seal(self) -> None:
        fingerprint = "3" * 64
        final = self.sandbox / "fit"
        failure = self.sandbox / "fit-failure.json"
        lease = publication.acquire_publication(
            "PRODUCER", "fit", final, failure, self.namespace(fingerprint)
        )
        lease.temp_path.mkdir()
        (lease.temp_path / "manifest.json").write_bytes(_json_bytes({
            "schemaVersion": "fit/1", "publication": lease.publication_evidence,
        }))
        model = lease.temp_path / "model"
        model.mkdir()
        (model / "part").write_bytes(b"ORIGINAL")
        pin = publication.publish_success(
            lease, lease.temp_path, fingerprint, lambda: fingerprint
        )
        (final / "model" / "part").write_bytes(b"TAMPERED")

        with self.assertRaises(publication.PublicationError) as caught:
            publication.release_verified_claim(lease, pin, fingerprint)
        self.assertEqual(caught.exception.code, "BLOCKED_PIN")
        self.assert_claim_preserved(lease)
        self.assertTrue(final.is_dir())

    def test_claim_inode_replacement_blocks_release_and_preserves_evidence(self) -> None:
        fingerprint = "7" * 64
        final = self.sandbox / "confirmation"
        failure = self.sandbox / "confirmation-failure.json"
        lease = publication.acquire_publication(
            "PRODUCER", "confirmation", final, failure, self.namespace(fingerprint)
        )
        self.write_staged_json(lease, status="PASS")
        pin = publication.publish_success(
            lease, lease.temp_path, fingerprint, lambda: fingerprint
        )
        original_claim = lease.claim_path.with_name("original-claim")
        os.rename(lease.claim_path, original_claim)
        lease.claim_path.write_bytes(original_claim.read_bytes())

        with self.assertRaises(publication.PublicationError) as caught:
            publication.release_verified_claim(lease, pin, fingerprint)
        self.assertEqual(caught.exception.code, "BLOCKED_CLAIM_OWNER")
        self.assertTrue(final.is_file())
        self.assertTrue(lease.claim_path.is_file())
        self.assertTrue(original_claim.is_file())

    def test_publisher_source_mutation_blocks_before_rename(self) -> None:
        source_directory = self.sandbox / "source"
        publication_parent = self.sandbox / "evidence"
        source_directory.mkdir()
        publication_parent.mkdir()
        copied_source = source_directory / "publication_copy.py"
        shutil.copyfile(MODULE_PATH, copied_source)
        copied_name = f"service_v1_b1_r4_publication_copy_{uuid.uuid4().hex}"
        specification = importlib.util.spec_from_file_location(copied_name, copied_source)
        self.assertIsNotNone(specification)
        self.assertIsNotNone(specification.loader)
        copied_module = importlib.util.module_from_spec(specification)
        sys.modules[copied_name] = copied_module
        specification.loader.exec_module(copied_module)
        try:
            fingerprint = "9" * 64
            final = publication_parent / "result-review.json"
            failure = publication_parent / "result-review-failure.json"
            lease = copied_module.acquire_publication(
                "REVIEWER",
                "implementation-review",
                final,
                failure,
                copied_module.ExpectedNamespace((), fingerprint),
            )
            payload = {
                "schemaVersion": "test/1",
                "publication": lease.publication_evidence,
            }
            with open(lease.temp_path, "xb") as stream:
                stream.write(_json_bytes(payload))
                stream.flush()
                os.fsync(stream.fileno())
            with copied_source.open("ab") as stream:
                stream.write(b"\n")
                stream.flush()
                os.fsync(stream.fileno())

            with self.assertRaises(copied_module.PublicationError) as caught:
                copied_module.publish_success(
                    lease, lease.temp_path, fingerprint, lambda: fingerprint
                )
            self.assertEqual(caught.exception.code, "BLOCKED_PUBLISHER")
            self.assertTrue(lease.claim_path.is_file())
            self.assertTrue(lease.temp_path.is_file())
            self.assertFalse(os.path.lexists(final))
        finally:
            sys.modules.pop(copied_name, None)

    def test_thirty_two_processes_produce_one_owner_and_no_loser_temp(self) -> None:
        context = multiprocessing.get_context("fork")
        barrier = context.Barrier(32)
        results = context.Queue()
        processes = [
            context.Process(target=_race_worker, args=(str(self.sandbox), barrier, results))
            for _ in range(32)
        ]
        for process in processes:
            process.start()
        outcomes = [results.get(timeout=60) for _ in processes]
        for process in processes:
            process.join(timeout=60)
            self.assertEqual(process.exitcode, 0)

        owners = [item for item in outcomes if item[0] == "OWNER"]
        self.assertEqual(len(owners), 1, outcomes)
        self.assertEqual(
            sum(1 for item in outcomes if item[0] in {"BLOCKED_NO_WRITE", "BLOCKED_NAMESPACE"}),
            31,
            outcomes,
        )
        self.assertEqual(
            tuple(sorted(path.name for path in self.sandbox.iterdir())),
            ("race-result-review.json",),
        )


class SourceContractTests(unittest.TestCase):
    def test_production_source_has_no_assert_statement(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        self.assertEqual([node for node in ast.walk(tree) if isinstance(node, ast.Assert)], [])

    def test_public_api_has_exact_four_functions(self) -> None:
        self.assertTrue(callable(publication.acquire_publication))
        self.assertTrue(callable(publication.publish_success))
        self.assertTrue(callable(publication.publish_handled_failure))
        self.assertTrue(callable(publication.release_verified_claim))
        self.assertEqual(
            [name for name in publication.__all__ if callable(getattr(publication, name))
             and name[0].islower()],
            [
                "acquire_publication",
                "publish_success",
                "publish_handled_failure",
                "release_verified_claim",
            ],
        )

    @unittest.skipUnless(os.name == "posix", "canonical publication requires Linux")
    def test_mapping_namespace_rejects_extra_fields_before_write(self) -> None:
        sandbox = Path(tempfile.mkdtemp(prefix=".feelm-r4-publication-test-", dir=Path.home()))
        try:
            final = sandbox / "result.json"
            failure = sandbox / "result-failure.json"
            with self.assertRaises(publication.PublicationError) as caught:
                publication.acquire_publication(
                    "REVIEWER",
                    "implementation-review",
                    final,
                    failure,
                    {"children": [], "dependencyFingerprint": "8" * 64, "extra": True},
                )
            self.assertEqual(caught.exception.code, "BLOCKED_NAMESPACE")
            self.assertEqual(tuple(sandbox.iterdir()), ())
        finally:
            shutil.rmtree(sandbox)

    @unittest.skipUnless(
        os.name == "posix" and Path("/mnt/c/Users/kingc/AppData/Local/Temp").is_dir(),
        "v9fs probe requires WSL",
    )
    def test_v9fs_parent_is_rejected_without_canonical_bytes(self) -> None:
        sandbox = Path(
            tempfile.mkdtemp(
                prefix="feelm-r4-v9fs-test-",
                dir="/mnt/c/Users/kingc/AppData/Local/Temp",
            )
        )
        try:
            final = sandbox / "result-review.json"
            failure = sandbox / "result-review-failure.json"
            with self.assertRaises(publication.PublicationError) as caught:
                publication.acquire_publication(
                    "REVIEWER",
                    "implementation-review",
                    final,
                    failure,
                    publication.ExpectedNamespace((), "b" * 64),
                )
            self.assertEqual(caught.exception.code, "BLOCKED_UNSUPPORTED_FILESYSTEM")
            self.assertEqual(tuple(sandbox.iterdir()), ())
        finally:
            shutil.rmtree(sandbox)


if __name__ == "__main__":
    unittest.main(verbosity=2)
