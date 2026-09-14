from __future__ import annotations

import ast
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
AUDITOR_PATH = ROOT / "scripts/audit_service_v1_b1_evaluation_outputs.py"
EVALUATOR_TEST_PATH = ROOT / "tests/test_evaluate_service_v1_b1.py"


def load_module(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


audit = load_module("audit_service_v1_b1_evaluation_outputs", AUDITOR_PATH)
fixture_module = load_module("evaluation_fixture_for_independent_audit", EVALUATOR_TEST_PATH)
ev = fixture_module.ev


def dump_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                    encoding="utf-8", newline="\n")


class AuditFixture:
    def __init__(self, root: Path) -> None:
        self.fixture = fixture_module.SyntheticFixture(root)
        self.root = root
        self._relocate_evaluation_sources()
        self.roots = audit.Roots(
            root, self.fixture.team_root, evaluator_override=ev.SCRIPT,
            auditor_override=AUDITOR_PATH,
        )
        self.score_auditor = root / "scripts/audit_service_v1_b1_spark_outputs.py"
        shutil.copyfile(ROOT / "scripts/audit_service_v1_b1_spark_outputs.py",
                        self.score_auditor)
        self._upgrade_score_review()
        source_pins = {name: (ev.pin(getattr(self.fixture.paths, name))["bytes"],
                              ev.pin(getattr(self.fixture.paths, name))["sha256"])
                       for name in ev.SOURCE_PINS}
        original = self.fixture.spec
        self.spec = audit.AuditSpec(
            score_rows=original.score_rows, catalog_movies=original.catalog_movies,
            label_rows=original.label_rows, cap10_targets=original.cap10_targets,
            cap10_users=original.cap10_users, extra_labels=original.extra_labels,
            calibration_users=original.calibration_users,
            selection_users=original.selection_users,
            confirmation_users=original.confirmation_users, als_movies=original.als_movies,
            training_rows=original.training_rows, training_users=original.training_users,
            minimum_users=original.minimum_users, minimum_movies=original.minimum_movies,
            minimum_rows=original.minimum_rows, bootstrap_samples=original.bootstrap_samples,
            role_digest=original.role_digest, canonical=False,
        )
        self.source_pins = source_pins

    def _move(self, source: Path, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        return destination

    def _relocate_evaluation_sources(self) -> None:
        base = self.root / "outputs/recommendation-evidence"
        f = self.fixture
        f.contexts = self._move(f.contexts, base / "text339/contexts.json")
        f.catalog = self._move(f.catalog, base / "text339/catalog.parquet")
        f.labels = self._move(f.labels, base / "text339/labels.parquet")
        f.eval_seal = self._move(f.eval_seal, base / "text339/evaluation-seal.json")
        f.ratings = self._move(f.ratings, base / "text339/ratings.parquet")
        f.roles = self._move(f.roles, base / "final344/roles.csv")
        f.metadata = self._move(f.metadata, base / "rec-ev-045/metadata.parquet")
        f.b0 = self._move(f.b0, base / "final344/GBT120_s339/predictions.npy")
        f.b0_seal = self._move(f.b0_seal, base / "final344/GBT120_s339-seal.json")
        f.paths = replace(
            f.paths, contexts=f.contexts, catalog=f.catalog, labels=f.labels,
            evaluation_seal=f.eval_seal, ratings=f.ratings, roles=f.roles,
            metadata=f.metadata, b0_predictions=f.b0, b0_seal=f.b0_seal,
        )
        f.fixed_pins = {name: (ev.pin(getattr(f.paths, name))["bytes"],
                               ev.pin(getattr(f.paths, name))["sha256"])
                        for name in ev.SOURCE_PINS}

    def _upgrade_score_review(self) -> None:
        review = ev.read_json(self.fixture.score_review)
        review.update({
            "createdAt": "2026-09-13T00:00:00+00:00",
            "reviewer": {
                "implementation": audit.pin(self.score_auditor),
                "independentFromRunner": True,
            },
            "checks": {},
            "evaluationTargetsRead": False,
            "modelFitPerformed": False,
            "readyForService": False,
            "scope": (
                "Local immutable bundle integrity and specified numerical parity; "
                "no service acceptance or model quality verdict."
            ),
        })
        source_paths = self.roots.score_auditor_sources()
        output_parent = self.fixture.score_dir.parent
        fingerprint_files = [
            *source_paths.values(),
            output_parent / f"{audit.PREDECESSOR_RUN_ID}-preflight-failure.json",
            self.fixture.preflight_review,
            self.fixture.fit_review,
        ]
        review["dependencyFingerprint"] = {
            "files": {self.roots.logical(path): audit.pin(path)
                      for path in fingerprint_files},
            "bundleInventories": {
                self.roots.logical(self.fixture.preflight_dir): audit.inventory(self.fixture.preflight_dir),
                self.roots.logical(self.fixture.fit_dir): audit.inventory(self.fixture.fit_dir),
                self.roots.logical(self.fixture.score_dir): audit.inventory(self.fixture.score_dir),
            },
            "auditorImplementation": audit.pin(self.score_auditor),
            "dockerImageId": audit.SPARK_IMAGE_ID,
        }
        dump_json(self.fixture.score_review, review)

    def selection(self) -> Path:
        output = self.roots.bundle("selection")
        self.fixture.run_selection(output)
        return output

    def audit_selection(self) -> dict:
        manifest = self.roots.bundle("selection") / "manifest.json"
        return audit.audit_phase(
            self.roots, "selection", audit.file_sha256(manifest),
            audit.file_sha256(ev.SCRIPT), _spec=self.spec, _source_pins=self.source_pins)

    def publish_selection(self) -> Path:
        return audit.publish_review(
            self.roots, "selection", self.audit_selection(),
            _spec=self.spec, _source_pins=self.source_pins,
        )

    def confirmation(self) -> Path:
        selection = self.roots.bundle("selection")
        review = self.roots.review("selection")
        output = self.roots.bundle("confirmation")
        ev.run_confirmation(
            self.fixture.paths, output,
            expected_plan_sha256=ev.sha256_file(self.fixture.plan),
            expected_score_manifest_sha256=ev.sha256_file(self.fixture.score_manifest),
            expected_score_review_sha256=ev.sha256_file(self.fixture.score_review),
            selection_manifest_path=selection / "manifest.json", selection_review_path=review,
            expected_selection_manifest_sha256=ev.sha256_file(selection / "manifest.json"),
            expected_selection_review_sha256=ev.sha256_file(review),
            spec=self.fixture.spec, fixed_pins=self.fixture.fixed_pins, publish_failure=False,
        )
        return output

    def audit_confirmation(self) -> dict:
        manifest = self.roots.bundle("confirmation") / "manifest.json"
        return audit.audit_phase(
            self.roots, "confirmation", audit.file_sha256(manifest),
            audit.file_sha256(ev.SCRIPT), _spec=self.spec, _source_pins=self.source_pins)

    def repin_bundle_file(self, phase: str, relative: str) -> None:
        bundle = self.roots.bundle(phase)
        manifest_path = bundle / "manifest.json"
        manifest = audit.json_object(manifest_path)
        manifest["files"][relative] = {"path": relative, **audit.pin(bundle / relative)}
        dump_json(manifest_path, manifest)


class EvaluationOutputAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.case = AuditFixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_selection_is_independently_recomputed_and_review_published(self) -> None:
        self.case.selection()
        review = self.case.audit_selection()
        self.assertEqual(review["status"], "PASS")
        self.assertEqual(review["runId"], audit.RUN_ID)
        self.assertTrue(review["checks"]["metricsIndependentlyRecomputed"])
        self.assertTrue(review["decision"]["confirmationEligible"])
        published = audit.publish_review(
            self.case.roots, "selection", review,
            _spec=self.case.spec, _source_pins=self.case.source_pins,
        )
        self.assertEqual(published, self.case.roots.review("selection"))
        self.assertEqual(audit.json_object(published), review)

    def test_forged_metrics_and_gate_fail_even_when_manifest_is_repinned(self) -> None:
        selection = self.case.selection()
        for name, mutate, message in (
            ("metrics.json", lambda data: data["pointDeltas"]["ALL"].__setitem__("microMSE", 9.0),
             "metrics"),
            ("gates.json", lambda data: data.__setitem__("requiredGate", "FAIL"), "gates"),
        ):
            with self.subTest(name=name):
                original_file = (selection / name).read_bytes()
                original_manifest = (selection / "manifest.json").read_bytes()
                try:
                    payload = audit.json_object(selection / name)
                    mutate(payload)
                    dump_json(selection / name, payload)
                    self.case.repin_bundle_file("selection", name)
                    with self.assertRaisesRegex(audit.AuditError, message):
                        self.case.audit_selection()
                finally:
                    (selection / name).write_bytes(original_file)
                    (selection / "manifest.json").write_bytes(original_manifest)

    def test_output_extra_file_and_source_mutation_fail_closed(self) -> None:
        selection = self.case.selection()
        (selection / "unexpected.bin").write_bytes(b"extra")
        with self.assertRaisesRegex(audit.AuditError, "inventory"):
            self.case.audit_selection()
        (selection / "unexpected.bin").unlink()
        source = self.case.fixture.labels
        source.write_bytes(source.read_bytes() + b"mutated")
        with self.assertRaisesRegex(audit.AuditError, "pin mismatch"):
            self.case.audit_selection()

    def test_ancestor_bundle_extra_file_is_rejected(self) -> None:
        self.case.selection()
        (self.case.fixture.fit_dir / "unreviewed.bin").write_bytes(b"extra")
        with self.assertRaisesRegex(audit.AuditError, "dependency bundle .*drift"):
            self.case.audit_selection()

    def test_score_review_fingerprint_requires_exact_r2_closure(self) -> None:
        selection = self.case.selection()
        reference_path = selection / "score-reference.json"
        reference = audit.json_object(reference_path)
        chain_paths, _ = audit.verify_record_map(
            reference["verifiedChainFiles"], self.case.roots, reference_path,
            "verified score chain",
        )
        review_path = self.case.fixture.score_review
        review = audit.json_object(review_path)
        first_bundle = next(iter(review["dependencyFingerprint"]["bundleInventories"]))
        del review["dependencyFingerprint"]["bundleInventories"][first_bundle]
        with self.assertRaisesRegex(audit.AuditError, "dependency bundle closure drift"):
            audit.verify_fingerprint(review, review_path, self.case.roots, chain_paths)

    def test_ancestor_failure_sibling_is_rejected(self) -> None:
        self.case.selection()
        failure = self.case.fixture.fit_dir.with_name(
            self.case.fixture.fit_dir.name + "-failure.json"
        )
        failure.write_text("{}\n", encoding="utf-8", newline="\n")
        with self.assertRaisesRegex(audit.AuditError, "ancestor .* failure record"):
            self.case.audit_selection()

    def test_publish_rejects_forged_review_envelope(self) -> None:
        self.case.selection()
        review = self.case.audit_selection()
        review["decision"] = {"forged": True}
        review["checks"]["metricsIndependentlyRecomputed"] = False
        review["reviewer"]["independentFromEvaluator"] = False
        with self.assertRaisesRegex(audit.AuditError, "review submitted for publication"):
            audit.publish_review(
                self.case.roots, "selection", review,
                _spec=self.case.spec, _source_pins=self.case.source_pins,
            )
        self.assertFalse(self.case.roots.review("selection").exists())

    def test_publish_rehashes_dependencies_immediately_before_rename(self) -> None:
        self.case.selection()
        review = self.case.audit_selection()
        source = self.case.fixture.labels
        original_json = audit.json_object

        def mutate_after_temp_read(path: Path):
            result = original_json(path)
            if "result-review.json.tmp-" in path.name:
                source.write_bytes(source.read_bytes() + b"changed during publish")
            return result

        with mock.patch.object(audit, "json_object", side_effect=mutate_after_temp_read):
            with self.assertRaisesRegex(audit.AuditError, "dependency changed"):
                audit.publish_review(
                    self.case.roots, "selection", review,
                    _spec=self.case.spec, _source_pins=self.case.source_pins,
                )
        self.assertFalse(self.case.roots.review("selection").exists())
        self.assertFalse(list(self.case.roots.review("selection").parent.glob(
            ".*result-review.json.tmp-*")))

    def test_confirmation_rechecks_selection_and_reports_final_decision(self) -> None:
        self.case.selection()
        self.case.publish_selection()
        self.case.confirmation()
        review = self.case.audit_confirmation()
        self.assertEqual(review["status"], "PASS")
        self.assertTrue(review["decision"]["noninferiorityConclusionEligible"])
        self.assertEqual(review["decision"]["logicalState"], "B1_EVALUATED_NONINFERIOR")
        path = audit.publish_review(
            self.case.roots, "confirmation", review,
            _spec=self.case.spec, _source_pins=self.case.source_pins,
        )
        self.assertTrue(path.is_file())

    def test_confirmation_blocks_changed_selection_review(self) -> None:
        self.case.selection()
        self.case.publish_selection()
        self.case.confirmation()
        review_path = self.case.roots.review("selection")
        payload = audit.json_object(review_path)
        payload["decision"]["confirmationEligible"] = False
        dump_json(review_path, payload)
        with self.assertRaises(audit.AuditError):
            self.case.audit_confirmation()

    def test_runid_drift_in_score_ancestor_is_rejected(self) -> None:
        self.case.selection()
        score_manifest = self.case.fixture.score_manifest
        payload = audit.json_object(score_manifest)
        payload["runId"] = "wrong-run"
        dump_json(score_manifest, payload)
        with self.assertRaises(audit.AuditError):
            self.case.audit_selection()

    def test_atomic_rename_never_clobbers_existing_review(self) -> None:
        source = self.case.root / "pending-review.json"
        target = self.case.root / "published-review.json"
        source.write_bytes(b"new review\n")
        target.write_bytes(b"existing review\n")
        with self.assertRaises(OSError):
            audit.rename_no_replace(source, target)
        self.assertEqual(target.read_bytes(), b"existing review\n")
        self.assertEqual(source.read_bytes(), b"new review\n")

    def test_production_module_has_no_assert_and_does_not_import_evaluator(self) -> None:
        tree = ast.parse(AUDITOR_PATH.read_text(encoding="utf-8"))
        self.assertFalse(any(isinstance(node, ast.Assert) for node in ast.walk(tree)))
        imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        self.assertNotIn("evaluate_service_v1_b1", imports)
        self.assertNotIn("--bootstrap-samples", audit.parser().format_help())
        self.assertNotIn("--source-pins", audit.parser().format_help())
        self.assertEqual(audit.file_sha256(ev.SCRIPT), audit.REVIEWED_EVALUATOR_SHA256)
        self.assertEqual(audit.RUN_ID, ev.RUN_ID)
        self.assertEqual(audit.PLAN_SHA256, ev.PLAN_SHA256)
        self.assertEqual(audit.ROLE_DIGEST, ev.ROLE_DIGEST)
        self.assertEqual(audit.SOURCE_PINS, ev.SOURCE_PINS)


if __name__ == "__main__":
    unittest.main()
