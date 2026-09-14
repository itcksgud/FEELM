from __future__ import annotations

import ast
import hashlib
import json
import io
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import audit_service_v1_b1_r4_implementation as audit


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def closure_fixture(marker: str = "a") -> dict[str, object]:
    def record(path: str, size: int) -> dict[str, object]:
        return {"path": path, "bytes": size, "sha256": marker * 64}

    target = {
        "plan": record("plan", 1),
        "profile": record("profile", 2),
        "sourceFiles": [record("source", 3)],
        "testFiles": [record("test", 4)],
        "runtimeFiles": [record("runtime", 5)],
    }
    return {"target": target, "sha256": audit.sha256_bytes(audit.canonical_json(target))}


def build_synthetic_oci(path: Path, *, omit_layer: bool = False, tamper_layer: bool = False,
                        extra_member: bool = False) -> str:
    blobs: dict[str, bytes] = {}

    def descriptor(media_type: str, raw: bytes, **extra: object) -> dict[str, object]:
        digest = audit.sha256_bytes(raw)
        blobs["blobs/sha256/" + digest] = raw
        return {"mediaType": media_type, "digest": "sha256:" + digest, "size": len(raw), **extra}

    layer_descriptors = [
        descriptor("application/vnd.oci.image.layer.v1.tar+gzip", f"layer-{index}".encode())
        for index in range(12)
    ]
    config = descriptor(
        "application/vnd.oci.image.config.v1+json",
        b'{"architecture":"amd64","os":"linux"}',
    )
    main_manifest_raw = json.dumps(
        {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json",
         "config": config, "layers": layer_descriptors},
        sort_keys=True, separators=(",", ":"),
    ).encode()
    main_manifest = descriptor(
        "application/vnd.oci.image.manifest.v1+json", main_manifest_raw,
        platform={"architecture": "amd64", "os": "linux"},
    )
    attestation_config = descriptor("application/vnd.oci.image.config.v1+json", b"{}")
    attestation_layer = descriptor("application/vnd.in-toto+json", b'{"predicateType":"fixture"}')
    attestation_manifest_raw = json.dumps(
        {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json",
         "config": attestation_config, "layers": [attestation_layer]},
        sort_keys=True, separators=(",", ":"),
    ).encode()
    attestation_manifest = descriptor(
        "application/vnd.oci.image.manifest.v1+json", attestation_manifest_raw,
        platform={"architecture": "unknown", "os": "unknown"},
        annotations={"vnd.docker.reference.type": "attestation-manifest",
                     "vnd.docker.reference.digest": main_manifest["digest"]},
    )
    nested_raw = json.dumps(
        {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json",
         "manifests": [main_manifest, attestation_manifest]},
        sort_keys=True, separators=(",", ":"),
    ).encode()
    outer = descriptor("application/vnd.oci.image.index.v1+json", nested_raw, annotations={})
    image_id = str(outer["digest"])
    index_raw = json.dumps(
        {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json",
         "manifests": [outer]},
        sort_keys=True, separators=(",", ":"),
    ).encode()
    config_path = "blobs/sha256/" + str(config["digest"]).removeprefix("sha256:")
    layer_paths = ["blobs/sha256/" + str(item["digest"]).removeprefix("sha256:")
                   for item in layer_descriptors]
    legacy_raw = json.dumps(
        [{"Config": config_path, "RepoTags": None, "Layers": layer_paths}],
        sort_keys=True, separators=(",", ":"),
    ).encode()
    if omit_layer:
        blobs.pop(layer_paths[0])
    if tamper_layer:
        blobs[layer_paths[0]] = b"tamper!"
    members: dict[str, bytes] = {
        "index.json": index_raw,
        "manifest.json": legacy_raw,
        "oci-layout": b'{"imageLayoutVersion":"1.0.0"}',
        **blobs,
    }
    if extra_member:
        members["unreachable"] = b"extra"
    with tarfile.open(path, "w:") as archive:
        for directory in ("blobs", "blobs/sha256"):
            info = tarfile.TarInfo(directory)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            archive.addfile(info)
        for name, raw in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(raw))
    return image_id


class CanonicalPrimitiveTests(unittest.TestCase):
    def test_record_set_digest_is_order_independent_and_rejects_duplicates(self) -> None:
        first = {"path": "b", "bytes": 2, "sha256": "b" * 64}
        second = {"path": "a", "bytes": 1, "sha256": "a" * 64}
        expected = hashlib.sha256(("a\x001\x00" + "a" * 64 + "\nb\x002\x00" + "b" * 64 + "\n").encode()).hexdigest()
        self.assertEqual(audit.record_set_digest([first, second]), expected)
        with self.assertRaisesRegex(audit.ImplementationAuditError, "duplicate"):
            audit.record_set_digest([first, first])

    def test_duplicate_key_bom_crlf_and_nonfinite_are_rejected(self) -> None:
        cases = (b'{"a":1,"a":2}\n', b'\xef\xbb\xbf{}\n', b'{}\r\n', b'{"a":NaN}\n')
        for raw in cases:
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "x.json"
                path.write_bytes(raw)
                with self.assertRaises(audit.ImplementationAuditError):
                    audit.duplicate_safe_json(path)

    def test_record_test_run_preserves_output_and_failure_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "normal.json"
            command = audit.expected_test_argv("normal", audit.LOCAL_TEST_INTERPRETER)
            completed = subprocess.CompletedProcess(command, 0, "out\n", "err\n")
            closure = closure_fixture()
            with mock.patch.object(audit, "test_closure_snapshot", return_value=closure), mock.patch.object(
                audit.subprocess, "run", return_value=completed
            ) as invoked:
                self.assertEqual(audit.record_test_run("normal", destination, command, ROOT), 0)
            invoked.assert_called_once()
            payload, summary = audit.read_test_run(
                destination, "normal", current_root=ROOT, current_closure=closure
            )
            self.assertEqual(payload["stdout"], "out\n")
            self.assertEqual(payload["stderr"], "err\n")
            self.assertEqual(summary["exitCode"], 0)
            self.assertEqual(
                set(summary), {"argv", "exitCode", "stdoutSha256", "stderrSha256", "evidence"}
            )
            self.assertEqual(payload["closureBefore"], payload["closureAfter"])
            self.assertEqual(payload["interpreterBefore"], payload["interpreterAfter"])
            self.assertEqual(payload["process"]["returnCode"], 0)
            with self.assertRaisesRegex(audit.ImplementationAuditError, "current review target"):
                audit.read_test_run(
                    destination, "normal", current_root=ROOT, current_closure=closure_fixture("b")
                )
            with self.assertRaisesRegex(audit.ImplementationAuditError, "already exists"):
                with mock.patch.object(audit, "test_closure_snapshot", return_value=closure):
                    audit.record_test_run("normal", destination, command, ROOT)

    def test_record_test_run_rejects_source_closure_drift_without_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "normal.json"
            command = audit.expected_test_argv("normal", audit.LOCAL_TEST_INTERPRETER)
            completed = subprocess.CompletedProcess(command, 0, "", "Ran 120 tests in 1.0s\n\nOK\n")
            with mock.patch.object(
                audit, "test_closure_snapshot", side_effect=[closure_fixture("a"), closure_fixture("b")]
            ), mock.patch.object(audit.subprocess, "run", return_value=completed):
                with self.assertRaisesRegex(audit.ImplementationAuditError, "closure changed"):
                    audit.record_test_run("normal", destination, command, ROOT)
            self.assertFalse(destination.exists())

    def test_legacy_forged_test_evidence_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "normal.json"
            stdout, stderr = "", "Ran 113 tests in 0.001s\n\nOK\n"
            write_json(path, {
                "schemaVersion": "feelm-service-v1-b1-r4-test-run/1",
                "kind": "normal",
                "argv": audit.expected_test_argv("normal", audit.LOCAL_TEST_INTERPRETER),
                "exitCode": 0,
                "stdout": stdout,
                "stderr": stderr,
                "stdoutSha256": audit.sha256_bytes(stdout.encode()),
                "stderrSha256": audit.sha256_bytes(stderr.encode()),
                "createdAt": "2026-09-14T00:00:00Z",
            })
            with mock.patch.object(audit, "_validate_private_evidence_permissions"):
                with self.assertRaisesRegex(audit.ImplementationAuditError, "schema drift"):
                    audit.read_test_run(
                        path, "normal", current_root=ROOT, current_closure=closure_fixture()
                    )

    def test_noop_or_extra_test_commands_are_rejected(self) -> None:
        no_op = [audit.LOCAL_TEST_INTERPRETER, "-c", "pass", *audit.TEST_FILES]
        with self.assertRaisesRegex(audit.ImplementationAuditError, "exact reviewed invocation"):
            audit.validate_test_argv("normal", no_op)
        exact = audit.expected_test_argv("normal", audit.LOCAL_TEST_INTERPRETER)
        with self.assertRaisesRegex(audit.ImplementationAuditError, "exact reviewed invocation"):
            audit.validate_test_argv("normal", [*exact, "--unexpected"])

    def test_bound_pin_rejects_metadata_mutation_during_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "value.bin"
            path.write_bytes(b"stable")
            original = audit._hash_descriptor

            def mutate_after_hash(descriptor: int) -> tuple[int, str]:
                result = original(descriptor)
                path.write_bytes(b"changed-value")
                return result

            with mock.patch.object(audit, "_hash_descriptor", side_effect=mutate_after_hash):
                with self.assertRaisesRegex(audit.ImplementationAuditError, "changed while reading"):
                    audit.pin(path, "value.bin")

    def test_ast_reads_bound_bytes_and_rejects_unpinned_import(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "scripts/plain.py"
            source.parent.mkdir()
            source.write_text("import os\n", encoding="utf-8")
            record = audit.pin(source, "scripts/plain.py")
            with mock.patch.object(Path, "read_text", side_effect=AssertionError("path read forbidden")):
                audit.audit_ast([record], root)
            source.write_text("import helper\n", encoding="utf-8")
            record = audit.pin(source, "scripts/plain.py")
            with self.assertRaisesRegex(audit.ImplementationAuditError, "unpinned import"):
                audit.audit_ast([record], root)

    def test_unreviewed_dynamic_loader_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "scripts/plain.py"
            source.parent.mkdir()
            source.write_text("import importlib\nimportlib.import_module('helper')\n", encoding="utf-8")
            with self.assertRaisesRegex(audit.ImplementationAuditError, "dynamic import/load surface"):
                audit.audit_ast([audit.pin(source, "scripts/plain.py")], root)

    def test_repo_local_module_cannot_shadow_an_allowed_stdlib_import(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / "scripts"
            scripts.mkdir()
            source = scripts / "plain.py"
            source.write_text("import json\n", encoding="utf-8")
            (scripts / "json.py").write_text("VALUE = 'unreviewed'\n", encoding="utf-8")
            with self.assertRaisesRegex(audit.ImplementationAuditError, "repo-local import shadow"):
                audit.audit_ast([audit.pin(source, "scripts/plain.py")], root)


class FrozenContractTests(unittest.TestCase):
    def test_real_profile_and_all_r3_ancestry_pins(self) -> None:
        audit.audit_profile(ROOT / audit.PROFILE_RELATIVE)
        audit.audit_r3_ancestry(ROOT)

    def test_profile_exact_schema_rejects_extra_and_missing_nested_fields(self) -> None:
        original = json.loads((ROOT / audit.PROFILE_RELATIVE).read_text(encoding="utf-8"))
        cases = []
        extra = dict(original)
        extra["unexpected"] = True
        cases.append(extra)
        missing = json.loads(json.dumps(original))
        del missing["hostRuntime"]["pythonVersion"]
        cases.append(missing)
        for value in cases:
            with self.subTest(keys=set(value)), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "profile.json"
                write_json(path, value)
                with self.assertRaisesRegex(audit.ImplementationAuditError, "exact schema or value"):
                    audit.audit_profile(path)

    def test_real_runtime_wheelhouse_is_complete_and_hashed(self) -> None:
        records = audit.wheel_runtime_records(ROOT)
        self.assertGreaterEqual(len(records), 2)
        self.assertEqual(records[0]["path"], "runtime/service-v1-b1-r4-wheelhouse-manifest.json")
        audit.audit_docker_archive(ROOT / "runtime/feelm-rec046-spark-local.tar")

    def test_oci_descriptor_graph_rejects_missing_tampered_and_unreachable_blobs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid = root / "valid.tar"
            image_id = build_synthetic_oci(valid)
            with mock.patch.object(audit, "DOCKER_IMAGE_ID", image_id):
                audit.audit_docker_archive(valid)
                for name, options, message in (
                    ("missing.tar", {"omit_layer": True}, "missing"),
                    ("tampered.tar", {"tamper_layer": True}, "digest drift"),
                    ("extra.tar", {"extra_member": True}, "unreachable"),
                ):
                    with self.subTest(name=name):
                        candidate = root / name
                        candidate_id = build_synthetic_oci(candidate, **options)
                        with mock.patch.object(audit, "DOCKER_IMAGE_ID", candidate_id):
                            with self.assertRaisesRegex(audit.ImplementationAuditError, message):
                                audit.audit_docker_archive(candidate)

    def test_current_source_import_closure_and_r3_combination_pin(self) -> None:
        records = [audit.pin(ROOT / relative, relative) for relative in audit.SOURCE_FILES]
        audit.audit_ast(records, ROOT)
        audit.audit_combination340_dependency(ROOT)

    def test_r4_source_inventory_rejects_an_extra_implementation_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            (root / "tests").mkdir()
            for relative in (*audit.SOURCE_FILES, *audit.TEST_FILES):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("\n", encoding="utf-8")
            audit.audit_r4_source_inventory(root)
            (root / "scripts/service_v1_b1_extra_r4.py").write_text("\n", encoding="utf-8")
            with self.assertRaisesRegex(audit.ImplementationAuditError, "missing or extra"):
                audit.audit_r4_source_inventory(root)

    def test_mutual_pin_contract_accepts_exact_pair_and_rejects_stale_pin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / "scripts"
            scripts.mkdir()
            auditor = scripts / "audit_service_v1_b1_evaluation_outputs_r4.py"
            evaluator = scripts / "evaluate_service_v1_b1_r4.py"
            placeholder_auditor = 'REVIEWED_R4_EVALUATOR_SHA256 = "' + "0" * 64 + '"\n'
            auditor.write_text(placeholder_auditor, encoding="utf-8")
            core = audit.normalized_auditor_core(auditor)
            evaluator.write_text('REVIEWED_R4_EVALUATION_AUDITOR_CORE_SHA256 = "' + core + '"\n', encoding="utf-8")
            evaluator_sha = audit.sha256_file(evaluator)
            auditor.write_text('REVIEWED_R4_EVALUATOR_SHA256 = "' + evaluator_sha + '"\n', encoding="utf-8")
            audit.audit_mutual_pins(root)
            evaluator.write_text('REVIEWED_R4_EVALUATION_AUDITOR_CORE_SHA256 = "' + "f" * 64 + '"\n', encoding="utf-8")
            with self.assertRaisesRegex(audit.ImplementationAuditError, "normalized auditor pin"):
                audit.audit_mutual_pins(root)

    def test_production_auditor_contains_no_assert_statement(self) -> None:
        tree = ast.parse((ROOT / "scripts/audit_service_v1_b1_r4_implementation.py").read_text(encoding="utf-8"))
        self.assertFalse(any(isinstance(node, ast.Assert) for node in ast.walk(tree)))

    def test_optimized_mode_keeps_contract_failures_active(self) -> None:
        code = "import sys; from pathlib import Path; sys.path.insert(0,str(Path.cwd()/'scripts')); import audit_service_v1_b1_r4_implementation as a; a.need(False,'active')"
        result = subprocess.run([sys.executable, "-O", "-c", code], cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("active", result.stderr)


if __name__ == "__main__":
    unittest.main()
