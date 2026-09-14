from __future__ import annotations

import ast
from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
AUDITOR_PATH = ROOT / "scripts/audit_service_v1_b1_evaluation_outputs_r3.py"
EVALUATOR_TEST_PATH = ROOT / "tests/test_evaluate_service_v1_b1_r3.py"


def load_module(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


audit = load_module("audit_service_v1_b1_evaluation_outputs_r3", AUDITOR_PATH)
fixture_module = load_module("evaluation_fixture_for_independent_audit", EVALUATOR_TEST_PATH)
ev = fixture_module.ev


def dump_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                    encoding="utf-8", newline="\n")


class AuditFixture:
    def __init__(self, root: Path) -> None:
        self.fixture = fixture_module.SyntheticFixture(root)
        self.root = self.fixture.root
        self._relocate_evaluation_sources()
        self.roots = audit.Roots(
            self.root, self.fixture.team_root, evaluator_override=ev.SCRIPT,
            auditor_override=AUDITOR_PATH,
            score_auditor_override=self.fixture.spark_auditor,
        )
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
            audit.verify_fingerprint(
                review, review_path, self.case.roots, chain_paths,
                phase="score", canonical=False,
            )

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

    def test_broken_failure_success_and_review_entries_fail_closed(self) -> None:
        selection = self.case.selection()
        failure = selection.with_name(selection.name + "-failure.json")
        real_lexists = os.path.lexists

        def failure_exists(path: Path) -> bool:
            return Path(path) == failure or real_lexists(path)

        with mock.patch.object(audit.os.path, "lexists", side_effect=failure_exists):
            with self.assertRaisesRegex(audit.AuditError, "failure record exists"):
                audit.no_transient_bundle(selection, "selection")

        stale_review = selection.parent / (
            "." + selection.name + "-result-review.json.tmp-broken")
        stale_review.write_text("stale\n", encoding="utf-8", newline="\n")
        with self.assertRaisesRegex(audit.AuditError, "stale staging"):
            audit.no_transient_bundle(selection, "selection")
        stale_review.unlink()

        broken_success = self.case.root / "broken-success"

        def success_exists(path: Path) -> bool:
            return Path(path) == broken_success or real_lexists(path)

        real_detector = audit.is_link_or_reparse

        def success_link(path: Path) -> bool:
            return Path(path) == broken_success or real_detector(path)

        with mock.patch.object(audit.os.path, "lexists", side_effect=success_exists), \
                mock.patch.object(audit, "is_link_or_reparse", side_effect=success_link):
            with self.assertRaisesRegex(audit.AuditError, "linked/reparse"):
                audit.safe_existing_path(broken_success, directory=True)

        review = self.case.audit_selection()
        destination = self.case.roots.review("selection")

        def review_exists(path: Path) -> bool:
            return Path(path) == destination or real_lexists(path)

        with mock.patch.object(audit.os.path, "lexists", side_effect=review_exists):
            with self.assertRaisesRegex(audit.AuditError,
                                        "immutable evaluation result review already exists"):
                audit.publish_review(
                    self.case.roots, "selection", review,
                    _spec=self.case.spec, _source_pins=self.case.source_pins)

        claim = selection.with_name("." + selection.name + ".publication-claim")
        claim.write_text("stale\n", encoding="utf-8", newline="\n")
        with self.assertRaisesRegex(audit.AuditError, "stale publication claim"):
            audit.no_transient_bundle(selection, "selection")

    def test_spark_review_nested_evidence_is_exact_for_every_phase(self) -> None:
        fixture = self.case.fixture
        cases = (
            ("preflight", fixture.preflight_review, "identity"),
            ("fit", fixture.fit_review, "fit"),
            ("score", fixture.score_review, "predictions"),
        )
        for phase, review_path, nested_key in cases:
            with self.subTest(phase=phase, nested_key=nested_key):
                review = audit.json_object(review_path)
                review["checks"][nested_key] = {}
                with self.assertRaisesRegex(audit.AuditError, "evidence drift"):
                    audit.verify_score_review_envelope(
                        review, review_path, self.case.roots, phase,
                        canonical=False, source_rows=self.case.spec.training_rows,
                        score_rows=self.case.spec.score_rows)

        review = audit.json_object(fixture.score_review)
        review["checks"]["predictions"]["maxAbsError"] = 1.0
        with self.assertRaisesRegex(audit.AuditError, "prediction evidence drift"):
            audit.verify_score_review_envelope(
                review, fixture.score_review, self.case.roots, "score",
                canonical=False, source_rows=self.case.spec.training_rows,
                score_rows=self.case.spec.score_rows)

    def test_public_roots_reject_a_link_before_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            standalone = base / "standalone"
            team = base / "team"
            linked = base / "standalone-link"
            standalone.mkdir()
            team.mkdir()
            linked_created = False
            try:
                os.symlink(standalone, linked, target_is_directory=True)
                linked_created = True
            except OSError:
                linked.mkdir()

            with mock.patch.object(audit, "ROOT", standalone), \
                    mock.patch.object(audit, "TEAM_ROOT", team):
                if linked_created:
                    with self.assertRaisesRegex(audit.AuditError, "linked/reparse"):
                        audit.public_roots(linked, team)
                else:
                    detector = audit.is_link_or_reparse

                    def linked_detector(path: Path) -> bool:
                        return Path(path) == linked or detector(path)

                    with mock.patch.object(audit, "is_link_or_reparse",
                                           side_effect=linked_detector):
                        with self.assertRaisesRegex(audit.AuditError, "linked/reparse"):
                            audit.public_roots(linked, team)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO unsupported on this platform")
    def test_bundle_inventory_rejects_special_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            bundle.mkdir()
            fifo = bundle / "unexpected.fifo"
            os.mkfifo(fifo)
            with self.assertRaisesRegex(audit.AuditError, "special bundle member forbidden"):
                audit.inventory(bundle)

    def test_linked_score_prediction_member_is_rejected_before_metric_read(self) -> None:
        self.case.selection()
        prediction = self.case.fixture.predictions
        real_detector = audit.is_link_or_reparse

        def detector(path: Path) -> bool:
            return Path(path) == prediction or real_detector(path)

        with mock.patch.object(audit, "is_link_or_reparse", side_effect=detector):
            with self.assertRaisesRegex(audit.AuditError, "linked"):
                self.case.audit_selection()

    def test_review_publish_race_preserves_peer_and_leaves_no_temp(self) -> None:
        self.case.selection()
        review = self.case.audit_selection()
        destination = self.case.roots.review("selection")

        def peer_wins(_source: Path, target: Path) -> None:
            target.write_text("peer review\n", encoding="utf-8", newline="\n")
            raise FileExistsError("peer won")

        with mock.patch.object(audit, "rename_no_replace", side_effect=peer_wins):
            with self.assertRaises(FileExistsError):
                audit.publish_review(
                    self.case.roots, "selection", review,
                    _spec=self.case.spec, _source_pins=self.case.source_pins)
        self.assertEqual(destination.read_text(encoding="utf-8"), "peer review\n")
        self.assertFalse(list(destination.parent.glob("." + destination.name + ".tmp-*")))
        self.assertFalse(os.path.lexists(self.case.roots.bundle("selection").with_name(
            "." + self.case.roots.bundle("selection").name + ".publication-claim")))

    def test_concurrent_review_publishers_have_one_terminal_winner_and_no_residue(self) -> None:
        self.case.selection()
        review = self.case.audit_selection()
        barrier = threading.Barrier(2)
        outcomes: list[Path | BaseException] = []

        def publish() -> None:
            barrier.wait()
            try:
                outcomes.append(audit.publish_review(
                    self.case.roots, "selection", review,
                    _spec=self.case.spec, _source_pins=self.case.source_pins))
            except BaseException as error:
                outcomes.append(error)

        threads = [threading.Thread(target=publish) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)
            self.assertFalse(thread.is_alive(), "review publication race deadlocked")

        destination = self.case.roots.review("selection")
        self.assertEqual(sum(isinstance(value, Path) for value in outcomes), 1)
        self.assertEqual(sum(isinstance(value, BaseException) for value in outcomes), 1)
        self.assertEqual(audit.json_object(destination), review)
        self.assertFalse(list(destination.parent.glob("." + destination.name + ".tmp-*")))
        claim = self.case.roots.bundle("selection").with_name(
            "." + self.case.roots.bundle("selection").name + ".publication-claim")
        self.assertFalse(os.path.lexists(claim))

    def test_peer_review_temp_injected_after_fingerprint_scan_blocks_publish(self) -> None:
        self.case.selection()
        review = self.case.audit_selection()
        destination = self.case.roots.review("selection")
        peer = destination.with_name("." + destination.name + ".tmp-peer")
        original = audit.dependency_fingerprint
        injected = False

        def inject_after_scan(roots, phase, *, lease=None, own_temp=False):
            nonlocal injected
            result = original(roots, phase, lease=lease, own_temp=own_temp)
            if own_temp and not injected:
                peer.write_text("peer-owned\n", encoding="utf-8", newline="\n")
                injected = True
            return result

        with mock.patch.object(audit, "dependency_fingerprint",
                               side_effect=inject_after_scan):
            with self.assertRaisesRegex(audit.AuditError, "stale staging"):
                audit.publish_review(
                    self.case.roots, "selection", review,
                    _spec=self.case.spec, _source_pins=self.case.source_pins)
        self.assertTrue(injected)
        self.assertEqual(peer.read_text(encoding="utf-8"), "peer-owned\n")
        self.assertFalse(os.path.lexists(destination))
        own_temps = [path for path in destination.parent.glob(
            "." + destination.name + ".tmp-*") if path != peer]
        self.assertFalse(own_temps)
        claim = self.case.roots.bundle("selection").with_name(
            "." + self.case.roots.bundle("selection").name + ".publication-claim")
        self.assertFalse(os.path.lexists(claim))

    def test_peer_temp_cannot_be_hidden_by_a_forged_review_lease(self) -> None:
        self.case.selection()
        bundle = self.case.roots.bundle("selection")
        destination = self.case.roots.review("selection")
        token = "a" * 32
        claim = bundle.with_name("." + bundle.name + ".publication-claim")
        claim.write_text(token + "\n", encoding="ascii", newline="\n")
        peer = destination.with_name("." + destination.name + ".tmp-peer")
        peer.write_text("peer-owned\n", encoding="utf-8", newline="\n")
        forged = audit.ReviewPublicationLease(
            bundle, destination, claim, peer, token)
        with self.assertRaisesRegex(audit.AuditError, "lease identity drift"):
            audit.no_transient_bundle(
                bundle, "selection", lease=forged, own_temp=True)
        self.assertEqual(peer.read_text(encoding="utf-8"), "peer-owned\n")
        self.assertEqual(claim.read_text(encoding="ascii"), token + "\n")

    def test_partial_review_temp_io_failures_are_cleaned_under_the_claim(self) -> None:
        self.case.selection()
        original_open = Path.open

        class FailingStream:
            def __init__(self, path: Path, stage: str) -> None:
                self.stream = original_open(path, "xb")
                self.stage = stage

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.stream.close()

            def write(self, value: bytes):
                written = self.stream.write(value if self.stage != "write" else value[:1])
                if self.stage == "write":
                    raise OSError("injected write failure")
                return written

            def flush(self):
                self.stream.flush()
                if self.stage == "flush":
                    raise OSError("injected flush failure")

            def fileno(self):
                return self.stream.fileno()

        for stage in ("write", "flush", "fsync"):
            with self.subTest(stage=stage):
                lease = audit._acquire_review_lease(self.case.roots, "selection")

                def open_path(path: Path, *args, **kwargs):
                    if Path(path) == lease.temporary:
                        return FailingStream(lease.temporary, stage)
                    return original_open(path, *args, **kwargs)

                try:
                    patches = [mock.patch.object(Path, "open", new=open_path)]
                    if stage == "fsync":
                        patches.append(mock.patch.object(
                            audit.os, "fsync", side_effect=OSError("injected fsync failure")))
                    with patches[0]:
                        if len(patches) == 2:
                            with patches[1]:
                                with self.assertRaisesRegex(OSError, "injected fsync"):
                                    audit._create_review_temporary(lease, b"partial-review\n")
                        else:
                            with self.assertRaisesRegex(OSError, f"injected {stage}"):
                                audit._create_review_temporary(lease, b"partial-review\n")
                    self.assertFalse(os.path.lexists(lease.temporary))
                    self.assertFalse(os.path.lexists(lease.destination))
                finally:
                    if os.path.lexists(lease.claim):
                        audit._release_review_lease(lease)
                self.assertFalse(os.path.lexists(lease.claim))

    def test_production_module_has_no_assert_and_does_not_import_evaluator(self) -> None:
        tree = ast.parse(AUDITOR_PATH.read_text(encoding="utf-8"))
        self.assertFalse(any(isinstance(node, ast.Assert) for node in ast.walk(tree)))
        imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        self.assertNotIn("evaluate_service_v1_b1", imports)
        self.assertNotIn("--bootstrap-samples", audit.parser().format_help())
        self.assertNotIn("--source-pins", audit.parser().format_help())
        if (audit.REVIEWED_R3_EVALUATOR_SHA256 is None
                or audit.REVIEWED_R3_EVALUATOR_SHA256 == "0" * 64):
            with self.assertRaisesRegex(audit.AuditError,
                                        "PUBLIC EVALUATION AUDIT BLOCKED"):
                audit.require_public_r3_contract_sealed()
        else:
            self.assertEqual(audit.file_sha256(ev.SCRIPT),
                             audit.REVIEWED_R3_EVALUATOR_SHA256)
        self.assertEqual(audit.RUN_ID, ev.RUN_ID)
        self.assertEqual(audit.PLAN_SHA256, ev.PLAN_SHA256)
        self.assertEqual(audit.ROLE_DIGEST, ev.ROLE_DIGEST)
        self.assertEqual(audit.SOURCE_PINS, ev.SOURCE_PINS)


if __name__ == "__main__":
    unittest.main()
