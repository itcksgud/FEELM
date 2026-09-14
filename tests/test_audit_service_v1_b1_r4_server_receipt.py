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
from types import SimpleNamespace
import unittest
from unittest import mock
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import audit_service_v1_b1_r4_server_receipt as audit
import build_service_v1_b1_r4_server_receipt as builder
from tests.test_build_service_v1_b1_r4_server_receipt import (
    DockerRunner,
    FakePublication,
    ReceiptFixture,
    host_probe,
    record,
    write_json,
)


def publication_evidence(role: str, final_path: Path, failure_path: Path,
                         fingerprint: str = "9" * 64) -> dict[str, object]:
    return {"schemaVersion": "feelm-service-v1-b1-r4-publication-evidence/1",
            "mode": "LINUX_RENAME_NOREPLACE", "role": role, "finalPath": str(final_path),
            "failurePath": str(failure_path), "claimPath": str(final_path.with_name(".claim")),
            "tempPath": str(final_path.with_name(".tmp")), "token": "fixture-token",
            "claimStDev": 1, "claimStIno": 2, "filesystemType": "ext2/ext3",
            "publisher": {"path": "/publisher", "bytes": 1, "sha256": "a" * 64},
            "dependencyFingerprintAtAcquire": fingerprint,
            "dependencyFingerprintBeforeRename": fingerprint, "renameNoReplaceProbe": True,
            "requiredPostconditions": {"publishedBytesRehashRequired": True,
                                       "fileAndParentFsyncRequired": True,
                                       "dependencyFingerprintStableRequired": True,
                                       "claimIdentityMatchRequired": True,
                                       "claimRemovalRequired": True}}


def make_read_only_tree(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda value: len(value.parts), reverse=True):
        os.chmod(path, stat.S_IMODE(os.lstat(path).st_mode) & ~0o222)
    os.chmod(root, stat.S_IMODE(os.lstat(root).st_mode) & ~0o222)


def restore_writable_tree(root: Path) -> None:
    if not root.exists():
        return
    for path in [root, *root.rglob("*")]:
        try:
            os.chmod(path, stat.S_IMODE(os.lstat(path).st_mode) | 0o700)
        except OSError:
            pass


class AuditRunner:
    def __init__(self, docker: DockerRunner, import_stdout: str) -> None:
        self.docker = docker
        self.import_stdout = import_stdout
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append(argv)
        if argv[:3] == ["docker", "image", "inspect"]:
            return self.docker(argv, **kwargs)
        if len(argv) >= 3 and argv[1:3] == ["-I", "-c"]:
            return SimpleNamespace(returncode=0, stdout=self.import_stdout, stderr="")
        raise AssertionError(argv)


class AuditableReceiptFixture:
    def __init__(self) -> None:
        self.base = ReceiptFixture()
        self.archive_patch = mock.patch.object(audit, "EXPECTED_ARCHIVE_SHA256",
                                              builder.sha256_bytes(self.base.archive_raw))
        self.archive_patch.start()
        shutil.copyfile(ROOT / audit.AUDITOR_RELATIVE, self.base.standalone / audit.AUDITOR_RELATIVE)
        self.venv_root = self.base.standalone / ".runtime/service-v1-b1-r4/venv"
        self.runtime_home = self.base.standalone / ".runtime/service-v1-b1-r4/home"
        self.runtime_home.mkdir(parents=True)
        (self.venv_root / "bin").mkdir(parents=True)
        physical_python = Path(sys.base_prefix) / "python.exe" if os.name == "nt" else Path(sys.executable)
        shutil.copyfile(physical_python, self.venv_root / "bin/python3")
        self.bootstrap = self.base.common / ("bootstrap-python.exe" if os.name == "nt" else "bootstrap-python")
        shutil.copyfile(physical_python, self.bootstrap)
        self.modules: dict[str, Path] = {}
        versions = {"numpy": "1.26.4", "pandas": "2.2.3", "pyarrow": "19.0.1"}
        site = self.venv_root / "lib/python3.12/site-packages"
        for name, version in versions.items():
            module = site / name / "__init__.py"
            module.parent.mkdir(parents=True, exist_ok=True)
            module.write_text(name + "\n", encoding="utf-8")
            info = site / f"{name}-{version}.dist-info"
            info.mkdir()
            (info / "RECORD").write_text(
                f"{name}/__init__.py,,\n{name}-{version}.dist-info/RECORD,,\n",
                encoding="utf-8", newline="\n")
            self.modules[name] = module
        distributions = [("numpy", "1.26.4"), ("pandas", "2.2.3"), ("pyarrow", "19.0.1"),
                         ("python-dateutil", "2.9.0.post0"), ("pytz", "2026.3.post1"),
                         ("six", "1.17.0"), ("tzdata", "2026.4")]
        wheel_records = []
        for name, version in distributions:
            relative = ("runtime/service-v1-b1-r4-wheelhouse/" + name.replace("-", "_") +
                        f"-{version}-py3-none-any.whl")
            wheel = self.base.delivery / relative
            wheel.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(f"{name.replace('-', '_')}-{version}.dist-info/METADATA",
                                 f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
            raw = wheel.read_bytes()
            wheel_records.append({"path": relative, "bytes": len(raw), "sha256": audit.sha256_bytes(raw)})
        wheel_set = audit.canonical_record_set_sha256(wheel_records)
        self.wheelhouse_patch = mock.patch.object(audit, "WHEELHOUSE_SET_SHA256", wheel_set)
        self.wheelhouse_patch.start()
        requirements = self.base.delivery / "requirements/service-v1-b1-r4-host-runtime.lock"
        requirements.parent.mkdir(parents=True, exist_ok=True)
        requirements.write_text("\n".join(
            f"{name}=={version} --hash=sha256:{wheel_records[index]['sha256']}"
            for index, (name, version) in enumerate(distributions)) + "\n", encoding="utf-8", newline="\n")
        wheelhouse = self.base.delivery / "runtime/service-v1-b1-r4-wheelhouse-manifest.json"
        write_json(wheelhouse, {"schemaVersion": "feelm-service-v1-b1-r4-wheelhouse/1",
                                "pythonVersion": "3.12.3", "interpreterTag": "cp312", "abiTag": "cp312",
                                "platformTag": "manylinux_2_17_x86_64", "files": wheel_records,
                                "wheelhouseSetSha256": wheel_set})
        added_records = [
            record("wslDelivery", "requirements/service-v1-b1-r4-host-runtime.lock",
                   "requirements/service-v1-b1-r4-host-runtime.lock", requirements.read_bytes()),
            record("wslDelivery", "runtime/service-v1-b1-r4-wheelhouse-manifest.json",
                   "runtime/service-v1-b1-r4-wheelhouse-manifest.json", wheelhouse.read_bytes()),
            *(record("wslDelivery", value["path"], value["path"],
                     (self.base.delivery / value["path"]).read_bytes()) for value in wheel_records),
        ]
        self.base.records.extend(added_records)
        self.base.manifest["controlAndImplementation"] = [*self.base.records[1:]]
        self.base.manifest["deliverySetSha256"] = builder.canonical_record_set_sha256(
            self.base.records, path_key="logicalPath")
        write_json(self.base.manifest_path, self.base.manifest)
        make_read_only_tree(self.venv_root)
        self.inventory = audit.inventory_venv(self.venv_root)
        packages = {}
        for name, version in versions.items():
            module_pin = audit.pin_file(self.modules[name], str(self.modules[name].resolve()))
            packages[name] = {"version": version, "modulePath": str(self.modules[name].resolve()),
                              "moduleBytes": module_pin["bytes"], "moduleSha256": module_pin["sha256"],
                              "distributionRecordSetSha256":
                                  audit._distribution_record_digest(self.venv_root, name, version)}
        self.import_stdout = "runtime-ok\n"
        interpreter = self.venv_root / "bin/python3"
        interpreter_pin = audit.pin_file(interpreter, str(interpreter.resolve()))
        requirements_pin = audit.pin_file(requirements, str(requirements.resolve()))
        wheelhouse_pin = audit.pin_file(wheelhouse, str(wheelhouse.resolve()))
        inventory_path = self.base.roots.receipt / "runtime/venv-file-inventory.json"
        inventory_raw = audit.canonical_json(self.inventory)
        input_raw = audit.canonical_json({"a": [1, 2, 3], "b": [0.5, 1.5, 2.5]})
        output_raw = audit.canonical_json({"sum": 4.5, "rows": 3, "columns": ["a", "b"]})
        fixture = {"inputSha256": audit.sha256_bytes(input_raw),
                   "outputSha256": audit.sha256_bytes(output_raw), "status": "PASS"}
        runtime_records = [interpreter_pin, requirements_pin, wheelhouse_pin,
                           {"path": str(inventory_path), "bytes": len(inventory_raw),
                            "sha256": audit.sha256_bytes(inventory_raw)},
                           {"path": "runtime/evaluation-fixture-input", "bytes": len(input_raw),
                            "sha256": fixture["inputSha256"]},
                           {"path": "runtime/evaluation-fixture-output", "bytes": len(output_raw),
                            "sha256": fixture["outputSha256"]}]
        bootstrap_pin = audit.pin_file(self.bootstrap, str(self.bootstrap.resolve()))
        self.runtime_lock = {
            "schemaVersion": "feelm-service-v1-b1-r4-host-runtime-lock/1", "createdAt": audit.utc_now(),
            "bootstrapInterpreter": {"command": "python3", "absolutePath": str(self.bootstrap.resolve()),
                                     "bytes": bootstrap_pin["bytes"], "sha256": bootstrap_pin["sha256"],
                                     "version": "3.12.3"},
            "absoluteInterpreter": str(interpreter.resolve()), "interpreterBytes": interpreter_pin["bytes"],
            "interpreterSha256": interpreter_pin["sha256"], "pythonImplementation": "CPython",
            "pythonVersion": "3.12.3",
            "environment": {"pythonPath": "UNSET", "pythonNoUserSite": True, "isolatedMode": True,
                            "pythonDontWriteBytecode": True, "locale": "C.UTF-8", "timezone": "UTC"},
            "requirementsLock": requirements_pin, "wheelhouseManifest": wheelhouse_pin,
            "venvInventory": {"path": str(inventory_path), "bytes": len(inventory_raw),
                              "sha256": audit.sha256_bytes(inventory_raw)},
            "packages": packages,
            "imports": {"command": [str(interpreter.resolve()), "-I", "-c", builder.RUNTIME_PROBE_SCRIPT],
                        "exitCode": 0,
                        "stdoutSha256": audit.sha256_bytes(self.import_stdout.encode()),
                        "stderrSha256": audit.sha256_bytes(b""), "status": "PASS"},
            "evaluationFixture": fixture,
            "runtimeSetSha256": audit.canonical_record_set_sha256(runtime_records),
        }
        delivery_review_final = self.base.review_path
        self.base.review["publication"] = publication_evidence(
            "REVIEWER", delivery_review_final,
            delivery_review_final.with_name(delivery_review_final.stem + "-failure.json"))
        self.base.review["target"]["manifest"] = builder.pin_file(self.base.manifest_path)
        self.base.review["target"]["deliverySetSha256"] = self.base.manifest["deliverySetSha256"]
        write_json(self.base.review_path, self.base.review)
        self.docker = DockerRunner()
        self.publication = FakePublication()
        with mock.patch.object(builder, "EXPECTED_ARCHIVE_SHA256", builder.sha256_bytes(self.base.archive_raw)):
            builder.produce_receipt(
                roots=self.base.roots, delivery_manifest_path=self.base.manifest_path,
                expected_delivery_manifest_sha256=builder.sha256_file(self.base.manifest_path),
                delivery_review_path=self.base.review_path,
                expected_delivery_review_sha256=builder.sha256_file(self.base.review_path),
                reservations=self.base.reservations, runner=self.docker,
                host_probe_collector=host_probe,
                runtime_builder=lambda _roots, _runner: (self.inventory, self.runtime_lock),
                publication=self.publication)
        self.manifest_path = self.base.roots.receipt / "manifest.json"
        self.runner = AuditRunner(self.docker, self.import_stdout)

    def audit_arguments(self) -> dict[str, object]:
        return {"standalone": self.base.standalone, "team": self.base.team, "delivery": self.base.delivery,
                "output_root": self.base.output, "scratch_root": self.base.scratch,
                "receipt_manifest": self.manifest_path,
                "expected_receipt_manifest_sha256": audit.pin_file(self.manifest_path)["sha256"],
                "reviewer_session": "fixture-review", "runner": self.runner,
                "probe_live_image": True, "probe_imports": True, "enforce_current_interpreter": False}

    def close(self) -> None:
        restore_writable_tree(self.venv_root)
        self.wheelhouse_patch.stop()
        self.archive_patch.stop()
        self.base.close()


class AuditorContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = AuditableReceiptFixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def test_normal_audit_rehashes_everything_and_publishes_exact_review(self) -> None:
        publication = FakePublication()
        pin = audit.publish_review(**self.fixture.audit_arguments(), publication=publication)
        self.assertEqual(publication.success_calls, 1)
        review_path = self.fixture.base.output / audit.RECEIPT_REVIEW_NAME
        review, _raw = audit.load_json(review_path)
        self.assertEqual(set(review), set(audit.REVIEW_KEYS))
        self.assertTrue(all(review["checks"].values()))
        self.assertEqual(review["decision"], {"receiptIntegrity": "PASS", "publicPreflightEligible": True,
                                              "fitEligible": False, "deploymentAuthorized": False})
        self.assertEqual(set(review["target"]["maintenanceReservations"]), set(audit.RESERVATION_FILES))
        self.assertEqual(pin["sha256"], audit.pin_file(review_path)["sha256"])
        forbidden = [call for call in self.fixture.runner.calls
                     if call[:2] == ["docker", "load"] or "pip" in call or "venv" in call]
        self.assertEqual(forbidden, [])

    def test_handled_audit_failure_is_no_replace_published_after_claim(self) -> None:
        manifest, _raw = audit.load_json(self.fixture.manifest_path)
        manifest["authorization"]["publicPreflightEligible"] = True
        self.fixture.manifest_path.write_bytes(audit.canonical_json(manifest))
        publication = FakePublication()
        arguments = self.fixture.audit_arguments()
        with self.assertRaisesRegex(audit.ReceiptAuditError, "immutable failure published"):
            audit.publish_review(**arguments, publication=publication)
        self.assertEqual(publication.failure_calls, 1)
        self.assertFalse((self.fixture.base.output / audit.RECEIPT_REVIEW_NAME).exists())
        failure_path = self.fixture.base.output / audit.RECEIPT_REVIEW_FAILURE_NAME
        failure, _failure_raw = audit.load_json(failure_path)
        self.assertEqual(failure["schemaVersion"], "feelm-service-v1-b1-r4-review-failure/1")
        self.assertFalse(failure["passReviewPublished"])

    def test_unambiguous_review_publication_error_becomes_review_failure(self) -> None:
        class RejectBeforeRename(FakePublication):
            def publish_success(self, lease, stage, fingerprint, callback):
                self.success_calls += 1
                if callback() != fingerprint:
                    raise RuntimeError("drift")
                raise RuntimeError("deterministic pre-rename rejection")

        publication = RejectBeforeRename()
        with self.assertRaisesRegex(audit.ReceiptAuditError, "immutable failure published"):
            audit.publish_review(**self.fixture.audit_arguments(), publication=publication)
        self.assertEqual(publication.success_calls, 1)
        self.assertEqual(publication.failure_calls, 1)
        self.assertFalse((self.fixture.base.output / audit.RECEIPT_REVIEW_NAME).exists())
        failure, _raw = audit.load_json(
            self.fixture.base.output / audit.RECEIPT_REVIEW_FAILURE_NAME)
        self.assertEqual(set(failure), set(audit.REVIEW_FAILURE_KEYS))

    def test_post_rename_review_error_preserves_ambiguous_terminal_state(self) -> None:
        class RaiseAfterRename(FakePublication):
            def publish_success(self, lease, stage, fingerprint, callback):
                self.success_calls += 1
                if callback() != fingerprint:
                    raise RuntimeError("drift")
                os.rename(stage, lease.final_path)
                raise RuntimeError("post-rename evidence failure")

        publication = RaiseAfterRename()
        with self.assertRaisesRegex(audit.ReceiptAuditError, "ambiguous or drifted; claim preserved"):
            audit.publish_review(**self.fixture.audit_arguments(), publication=publication)
        self.assertEqual(publication.failure_calls, 0)
        self.assertTrue((self.fixture.base.output / audit.RECEIPT_REVIEW_NAME).is_file())
        self.assertFalse((self.fixture.base.output / audit.RECEIPT_REVIEW_FAILURE_NAME).exists())

    def test_publication_loser_runs_no_live_probe(self) -> None:
        class RejectPublication:
            def acquire_publication(self, *args, **kwargs):
                raise RuntimeError("BLOCKED_NO_WRITE")

        self.fixture.runner.calls.clear()
        with self.assertRaisesRegex(RuntimeError, "BLOCKED_NO_WRITE"):
            audit.publish_review(**self.fixture.audit_arguments(), publication=RejectPublication())
        self.assertEqual(self.fixture.runner.calls, [])

    def test_stale_expected_sha_publishes_review_failure_before_live_probe(self) -> None:
        arguments = self.fixture.audit_arguments()
        arguments["expected_receipt_manifest_sha256"] = "f" * 64
        publication = FakePublication()
        self.fixture.runner.calls.clear()
        with self.assertRaisesRegex(audit.ReceiptAuditError, "immutable failure published"):
            audit.publish_review(**arguments, publication=publication)
        self.assertEqual(publication.failure_calls, 1)
        self.assertEqual(self.fixture.runner.calls, [])
        failure_path = self.fixture.base.output / audit.RECEIPT_REVIEW_FAILURE_NAME
        failure, _raw = audit.load_json(failure_path)
        self.assertEqual(set(failure), set(audit.REVIEW_FAILURE_KEYS))
        self.assertEqual(failure["schemaVersion"], "feelm-service-v1-b1-r4-review-failure/1")
        self.assertFalse(failure["passReviewPublished"])

    def test_exact_predecessor_namespace_rejects_hidden_or_extra_without_claim(self) -> None:
        for name in ("." + audit.RUN_ID + "-stale.tmp-old", audit.RUN_ID + "-unexpected.json"):
            path = self.fixture.base.output / name
            path.write_bytes(b"blocked\n")
            publication = FakePublication()
            self.fixture.runner.calls.clear()
            try:
                with self.assertRaisesRegex(audit.ReceiptAuditError, "predecessor namespace drift"):
                    audit.publish_review(**self.fixture.audit_arguments(), publication=publication)
                self.assertEqual(publication.acquire_calls, [])
                self.assertEqual(self.fixture.runner.calls, [])
                self.assertFalse((self.fixture.base.output / audit.RECEIPT_REVIEW_FAILURE_NAME).exists())
            finally:
                path.unlink()

    def test_missing_special_and_extra_receipt_child_publish_review_failure(self) -> None:
        for mutation in ("missing", "special", "extra"):
            fixture = AuditableReceiptFixture()
            try:
                host = fixture.base.roots.receipt / "host-probe.json"
                if mutation == "missing":
                    host.unlink()
                elif mutation == "special":
                    host.unlink()
                    host.mkdir()
                else:
                    (fixture.base.roots.receipt / "unexpected.bin").write_bytes(b"extra\n")
                publication = FakePublication()
                fixture.runner.calls.clear()
                with self.assertRaisesRegex(audit.ReceiptAuditError, "immutable failure published"):
                    audit.publish_review(**fixture.audit_arguments(), publication=publication)
                self.assertEqual(publication.failure_calls, 1)
                self.assertEqual(fixture.runner.calls, [])
                self.assertTrue((fixture.base.output / audit.RECEIPT_REVIEW_FAILURE_NAME).is_file())
            finally:
                fixture.close()

    def test_destination_contamination_blocks_review(self) -> None:
        label = self.fixture.base.delivery / "evaluation/labels.parquet"
        label.chmod(0o600)
        label.write_bytes(b"changed\n")
        with self.assertRaisesRegex(audit.ReceiptAuditError, "destination current rehash drift"):
            audit.audit_receipt(**self.fixture.audit_arguments())

    def test_missing_child_and_hardlink_alias_are_blocked(self) -> None:
        host = self.fixture.base.roots.receipt / "host-probe.json"
        host.unlink()
        with self.assertRaisesRegex(audit.ReceiptAuditError, "receipt file inventory drift"):
            audit.audit_receipt(**self.fixture.audit_arguments())
        manifest, _raw = audit.load_json(self.fixture.manifest_path)
        host.write_bytes(audit.canonical_json(manifest["hostProbe"]))
        destination = self.fixture.base.roots.receipt / "destination-inventory.json"
        destination.unlink()
        os.link(host, destination)
        with self.assertRaisesRegex(audit.ReceiptAuditError, "hard-link alias"):
            audit.audit_receipt(**self.fixture.audit_arguments())

    def test_stale_manifest_pin_and_extra_schema_field_fail_closed_before_probes(self) -> None:
        arguments = self.fixture.audit_arguments()
        arguments["expected_receipt_manifest_sha256"] = "f" * 64
        self.fixture.runner.calls.clear()
        with self.assertRaisesRegex(audit.ReceiptAuditError, "expected SHA drift"):
            audit.audit_receipt(**arguments)
        self.assertEqual(self.fixture.runner.calls, [])
        manifest, _raw = audit.load_json(self.fixture.manifest_path)
        manifest["unexpected"] = True
        self.fixture.manifest_path.write_bytes(audit.canonical_json(manifest))
        arguments = self.fixture.audit_arguments()
        with self.assertRaisesRegex(audit.ReceiptAuditError, "shape/identity/status"):
            audit.audit_receipt(**arguments)

    def test_reservation_alias_and_runtime_inventory_drift_are_blocked(self) -> None:
        manifest, _raw = audit.load_json(self.fixture.manifest_path)
        manifest["maintenanceReservations"]["fit"] = manifest["maintenanceReservations"]["score"]
        self.fixture.manifest_path.write_bytes(audit.canonical_json(manifest))
        with self.assertRaises(audit.ReceiptAuditError):
            audit.audit_receipt(**self.fixture.audit_arguments())
        manifest["maintenanceReservations"]["fit"] = builder._child_pin(
            self.fixture.base.roots.receipt / "maintenance-reservations/fit.json",
            (self.fixture.base.roots.receipt / "maintenance-reservations/fit.json").read_bytes())
        self.fixture.manifest_path.write_bytes(audit.canonical_json(manifest))
        module = self.fixture.modules["numpy"]
        module.chmod(0o600)
        module.write_text("tampered\n", encoding="utf-8")
        with self.assertRaises(audit.ReceiptAuditError):
            audit.audit_receipt(**self.fixture.audit_arguments())

    def test_destination_component_symlink_is_rejected_by_independent_binding(self) -> None:
        evaluation = self.fixture.base.delivery / "evaluation"
        outside = self.fixture.base.common / "outside-evaluation"
        evaluation.rename(outside)
        try:
            os.symlink(outside, evaluation, target_is_directory=True)
        except OSError as error:
            outside.rename(evaluation)
            self.skipTest(f"directory symlink unavailable: {error}")
        with self.assertRaisesRegex(audit.ReceiptAuditError, "lexical symlink binding drift"):
            audit.audit_receipt(**self.fixture.audit_arguments())

    def test_binary_pin_streams_fixed_chunks_without_path_read_bytes(self) -> None:
        path = self.fixture.base.delivery / "large-stream.bin"
        payload = (b"fedcba9876543210" * ((audit.READ_CHUNK_BYTES * 3 + 29) // 16 + 1))[
            :audit.READ_CHUNK_BYTES * 3 + 29]
        path.write_bytes(payload)
        original_read = os.read
        requests: list[int] = []

        def guarded_read(descriptor: int, count: int) -> bytes:
            requests.append(count)
            if count > audit.READ_CHUNK_BYTES:
                raise AssertionError("read-all request")
            return original_read(descriptor, count)

        with mock.patch.object(audit.os, "read", side_effect=guarded_read), \
             mock.patch.object(Path, "read_bytes", side_effect=AssertionError("Path.read_bytes forbidden")):
            pin = audit.pin_file(path)
        self.assertEqual(pin["bytes"], len(payload))
        self.assertEqual(pin["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertGreaterEqual(len(requests), 4)
        self.assertLessEqual(max(requests), audit.READ_CHUNK_BYTES)


class OptimizationTests(unittest.TestCase):
    def test_auditor_has_no_assert_and_optimized_contract_failure_is_active(self) -> None:
        tree = ast.parse((ROOT / "scripts/audit_service_v1_b1_r4_server_receipt.py").read_text(encoding="utf-8"))
        self.assertFalse(any(isinstance(node, ast.Assert) for node in ast.walk(tree)))
        code = ("import sys;from pathlib import Path;sys.path.insert(0,str(Path.cwd()/'scripts'));"
                "import audit_service_v1_b1_r4_server_receipt as a;a.require(False,'active')")
        result = subprocess.run([sys.executable, "-O", "-c", code], cwd=ROOT,
                                capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("active", result.stderr)


if __name__ == "__main__":
    unittest.main()
