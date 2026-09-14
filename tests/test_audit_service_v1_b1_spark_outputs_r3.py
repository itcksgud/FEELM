"""Synthetic/read-only contract tests for the independent r3 Spark auditor."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import audit_service_v1_b1_spark_outputs_r3 as audit
import run_service_v1_b1_gbt_r3 as host


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


class ProfileAndAncestryTests(unittest.TestCase):
    def test_real_profile_r2_ancestry_and_recovery_contract(self) -> None:
        roots = audit.Roots(ROOT, ROOT.parent / "S15P21E106")
        profile, profile_pin = audit.audit_profile_contract(roots)
        self.assertEqual(profile["profileId"], audit.PROFILE_ID)
        ancestry = audit.audit_r2_ancestry(roots, audit.Limits())
        self.assertEqual(ancestry["r2Plan"]["path"], "ancestor/service-v1-b1-spark-runner.md")
        sources = roots.sources()
        outer = audit.pin(sources["execution/run_service_v1_b1_gbt_r3.py"], "execution/run_service_v1_b1_gbt_r3.py")
        worker = audit.pin(sources["implementation/service_v1_b1_spark_worker.py"], "implementation/service_v1_b1_spark_worker.py")
        execution = [audit.pin(sources[name], name) for name in sorted(audit.EXECUTION_NAMES)]
        recovery = audit.expected_recovery_reference(roots, audit.Limits(), ancestry, outer, worker, profile_pin, audit.set_digest(execution))
        comparison = recovery["digestComparison"]
        self.assertEqual(comparison["sourceRows"], 4_997_069)
        self.assertEqual(comparison["logicalRows"], 19_988_276)
        self.assertEqual(comparison["trainingRecipe"]["path"], "contract/training-recipe.v1.json")

    def test_profile_model_contract_rejects_extra_and_missing_keys(self) -> None:
        for mutation in ("extra", "missing"):
            with tempfile.TemporaryDirectory() as directory:
                roots = audit.Roots(Path(directory) / "standalone", Path(directory) / "team")
                destination = roots.profile_contract(); destination.parent.mkdir(parents=True)
                profile = json.loads((ROOT / "docs/recommendation/plans/service-v1-b1-r3-local4c12g-t14400-profile.json").read_text(encoding="utf-8"))
                if mutation == "extra": profile["modelContract"]["unexpected"] = True
                else: del profile["modelContract"]["trees"]
                write_json(destination, profile)
                with self.subTest(mutation=mutation), self.assertRaisesRegex(audit.AuditError, "model contract"):
                    audit.audit_profile_contract(roots)

    def test_legacy_alias_is_single_allowlisted_path(self) -> None:
        roots = audit.Roots(ROOT, ROOT.parent / "S15P21E106")
        expected = ROOT / "docs/recommendation/plans/service-v1-b1-spark-runner.md"
        self.assertEqual(roots.resolve_logical("ancestor/service-v1-b1-spark-runner.md"), expected)
        for value in ("ancestor/other", "ancestor/../x", "ancestor\\service-v1-b1-spark-runner.md"):
            with self.subTest(value=value), self.assertRaises(audit.AuditError):
                roots.resolve_logical(value)

    def test_each_phase_rejects_authority_escalation(self) -> None:
        common = {"schemaVersion", "runId", "profileId", "status", "createdAt", "files", "outerRunnerSha256",
            "sparkWorkerSha256", "implementationSetSha256", "modelInputSetSha256", "workerRuntimeSetSha256",
            "executionSetSha256", "controlReferenceSetSha256", "inputSetSha256", "r2FitFailureSha256",
            "recoveryReferenceSha256", "executionProfileSha256", "resourceStatus", "runtimeVersions"}
        phase_keys = {
            "preflight": {"fitAuthorized", "modelFitPerformed", "scorePerformed", "readyForService", "sourceRows",
                "logicalRows", "partitionCount", "r2TrainingSourceSetSha256", "r2PreflightManifestSha256",
                "r2PreflightReviewSha256", "dryRunPrecedesFullMaterialization", "dryRunLogicalRows",
                "partitionIdentitySha256", "identity"},
            "fit": {"scoringAuthorized", "readyForService", "modelFitPerformed", "scorePerformed", "sourceRows",
                "logicalRows", "partitionCount", "treeCount", "preflightManifestSha256", "preflightReviewSha256",
                "partitionIdentitySha256", "modelInventorySha256", "modelFileSetSha256", "thresholdFixtureSha256",
                "portableParity"},
            "score": {"evaluationAuthorized", "readyForService", "modelFitPerformed", "scorePerformed", "rows",
                "scoreInputSetSha256", "fitManifestSha256", "fitReviewSha256", "modelInventorySha256",
                "modelFileSetSha256", "predictionSha256", "scoreCensus", "labelProjected"},
        }
        escalation = {"preflight": "readyForService", "fit": "scoringAuthorized", "score": "evaluationAuthorized"}
        valid_flags = {"preflight": {"fitAuthorized": False, "modelFitPerformed": False, "scorePerformed": False, "readyForService": False},
            "fit": {"scoringAuthorized": False, "readyForService": False, "modelFitPerformed": True, "scorePerformed": False},
            "score": {"evaluationAuthorized": False, "readyForService": False, "modelFitPerformed": False, "scorePerformed": True}}
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "bundle"; bundle.mkdir()
            for phase in ("preflight", "fit", "score"):
                before = {"manifest.json": {"bytes": 1, "sha256": "0" * 64}}
                before.update({name: {"bytes": 1, "sha256": hashlib.sha256(name.encode()).hexdigest()}
                               for name in audit.TARGETS[phase].values()})
                if phase == "fit": before["model/native/part"] = {"bytes": 1, "sha256": "1" * 64}
                manifest = {key: None for key in common | phase_keys[phase]}
                manifest.update({"schemaVersion": f"feelm-service-v1-b1-{phase}-manifest/2", "runId": audit.RUN_ID,
                    "profileId": audit.PROFILE_ID, "status": audit.STATUSES[phase], "createdAt": "2026-09-14T00:00:00+00:00",
                    "files": {key: value for key, value in before.items() if key != "manifest.json"}, **valid_flags[phase]})
                manifest[escalation[phase]] = True
                with self.subTest(phase=phase), mock.patch.object(audit, "inventory", return_value=before), \
                     mock.patch.object(audit, "json_object", return_value=manifest), self.assertRaisesRegex(audit.AuditError, "authorization"):
                    audit.audit_inventory(bundle, phase, audit.Limits(canonical=False))


class LockAndPublicationTests(unittest.TestCase):
    def test_score_lock_audits_nine_plus_one_disjoint_groups(self) -> None:
        roots = audit.Roots(ROOT, ROOT.parent / "S15P21E106")
        paths = host.ExecutionPaths.from_roots(ROOT, ROOT.parent / "S15P21E106")
        models = host.model_input_records(paths)
        score_inputs = host.score_input_records(paths)
        runtime = host.worker_runtime_records(paths, audit.IMAGE_ID)
        execution = host.execution_records(paths)
        lock = host.make_score_lock(models, score_inputs, runtime, execution, [])
        outer = next(row for row in execution if row["path"] == "execution/run_service_v1_b1_gbt_r3.py")
        worker = next(row for row in runtime if row["path"] == "implementation/service_v1_b1_spark_worker.py")
        manifest = {key: lock[key] for key in ("modelInputSetSha256", "scoreInputSetSha256", "workerRuntimeSetSha256",
            "executionSetSha256", "controlReferenceSetSha256", "inputSetSha256")}
        manifest.update({"outerRunnerSha256": outer["sha256"], "sparkWorkerSha256": worker["sha256"],
                         "implementationSetSha256": audit.set_digest([outer, worker])})
        with mock.patch.object(audit, "controls_for", return_value=[]):
            pins = audit.audit_lock(roots, "score", lock, manifest, outer["sha256"], worker["sha256"], audit.Limits())
        self.assertEqual(len(pins["model"]), 9)
        self.assertEqual([row["path"] for row in pins["score"]], ["source/natural-score.parquet"])
        forged = copy.deepcopy(lock); forged["scoreInputRecords"] = [copy.deepcopy(models[0])]
        with mock.patch.object(audit, "controls_for", return_value=[]):
            with self.assertRaises(audit.AuditError):
                audit.audit_lock(roots, "score", forged, manifest, outer["sha256"], worker["sha256"], audit.Limits())

    def _dependency_fixture(self):
        temporary = tempfile.TemporaryDirectory(); root = Path(temporary.name)
        roots = audit.Roots(root / "standalone", root / "team")
        roots.r2_preflight().mkdir(parents=True)
        roots.bundle("preflight").mkdir(parents=True)
        source_paths = {}
        for index, name in enumerate(sorted(audit.MODEL_INPUT_NAMES | audit.WORKER_RUNTIME_NAMES | audit.EXECUTION_NAMES)):
            if name == "runtime/docker-image-id": continue
            path = roots.standalone / "fixture-sources" / f"{index:02d}.bin"
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(name.encode()); source_paths[name] = path
        return temporary, roots, source_paths

    def test_dependency_fingerprint_ignores_only_its_exact_review_temp(self) -> None:
        temporary, roots, sources = self._dependency_fixture(); self.addCleanup(temporary.cleanup)
        phase = "preflight"; bundle = roots.bundle(phase)
        own = bundle.parent / ("." + roots.review(phase).name + ".tmp-own")
        own.write_bytes(b"review")
        with mock.patch.object(audit.Roots, "sources", return_value=sources), \
             mock.patch.object(audit, "audit_r2_ancestry", return_value={}), \
             mock.patch.object(audit, "r2_control_records", return_value=[]):
            result = audit.dependency_fingerprint(roots, phase, audit.Limits(canonical=False), ignored_temporary=own)
            self.assertEqual(result["runId"], audit.RUN_ID)
            with self.assertRaisesRegex(audit.AuditError, "stale"):
                audit.dependency_fingerprint(roots, phase, audit.Limits(canonical=False))
            other = bundle.parent / ("." + roots.review(phase).name + ".tmp-other")
            other.write_bytes(b"other")
            with self.assertRaisesRegex(audit.AuditError, "stale"):
                audit.dependency_fingerprint(roots, phase, audit.Limits(canonical=False), ignored_temporary=own)

    def test_publish_review_passes_exact_self_temp_and_preserves_race_winner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            roots = audit.Roots(Path(directory) / "standalone", Path(directory) / "team")
            roots.bundle("preflight").mkdir(parents=True)
            dependency = {"files": {}, "bundleInventories": {}, "auditorImplementation": {"bytes": 1, "sha256": "a" * 64},
                          "dockerImageId": audit.IMAGE_ID, "runId": audit.RUN_ID, "profileId": audit.PROFILE_ID}
            review = {"schemaVersion": "feelm-service-v1-b1-r3-result-review/1", "runId": audit.RUN_ID,
                      "profileId": audit.PROFILE_ID, "phase": "preflight", "status": "PASS",
                      "createdAt": "2026-09-14T00:00:00+00:00", "target": {},
                      "reviewer": {"implementation": audit.pin(Path(audit.__file__).resolve()), "independentFromRunner": True},
                      "dependencyFingerprint": dependency,
                      "checks": {"resource": {"stages": 2, "peakBytes": 100, "cleanupConfirmed": True,
                          "labelMountAbsent": True, "measurementIndependentlyObserved": False,
                          "measurementCheckedAgainstPinnedWorkerLog": True}, "recovery": {}, "identity": {}},
                      "evaluationTargetsRead": False, "modelFitPerformed": False, "readyForService": False,
                      "scope": "Local immutable bundle integrity and specified numerical parity; no service acceptance or model quality verdict."}
            ignored = []
            def fingerprint(_roots, _phase, _limits, *, ignored_temporary=None):
                ignored.append(ignored_temporary); return dependency
            with mock.patch.object(audit, "target_records", return_value={}), \
                 mock.patch.object(audit, "dependency_fingerprint", side_effect=fingerprint):
                destination = audit.publish_review(roots, "preflight", review)
            self.assertTrue(destination.is_file())
            self.assertIsNone(ignored[0]); self.assertIsNotNone(ignored[-1])
            self.assertTrue(ignored[-1].name.startswith("." + destination.name + ".tmp-"))

        with tempfile.TemporaryDirectory() as directory:
            roots = audit.Roots(Path(directory) / "standalone", Path(directory) / "team")
            roots.bundle("preflight").mkdir(parents=True)
            destination = roots.review("preflight")
            original = audit.rename_no_replace
            def race(source, target):
                target.write_bytes(b"winner"); original(source, target)
            with mock.patch.object(audit, "target_records", return_value={}), \
                 mock.patch.object(audit, "dependency_fingerprint", return_value=dependency), \
                 mock.patch.object(audit, "rename_no_replace", side_effect=race):
                with self.assertRaises(OSError):
                    audit.publish_review(roots, "preflight", review)
            self.assertEqual(destination.read_bytes(), b"winner")


class CrossImplementationIntegrationTests(unittest.TestCase):
    def test_synthetic_preflight_audit_publish_is_accepted_by_fit_gate_subprocess(self) -> None:
        code = textwrap.dedent(r'''
            import datetime as dt, hashlib, json, tempfile
            from pathlib import Path
            from unittest import mock
            import sys
            root_repo=Path.cwd(); sys.path.insert(0,str(root_repo/'scripts'))
            import audit_service_v1_b1_spark_outputs_r3 as a
            import run_service_v1_b1_gbt_r3 as h
            def w(path,value): path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(value)+'\n',encoding='utf-8')
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory); ar=a.Roots(root/'standalone',root/'team'); hp=h.ExecutionPaths.from_roots(ar.standalone,ar.team)
                b=ar.bundle('preflight'); b.mkdir(parents=True)
                dig={k:hashlib.sha256(k.encode()).hexdigest() for k in ('modelInputSetSha256','workerRuntimeSetSha256','executionSetSha256','controlReferenceSetSha256','inputSetSha256')}
                lock={'runId':h.RUN_ID,'profileId':h.PROFILE_ID,**dig}; w(b/'input-lock.json',lock)
                outer={'path':'execution/run_service_v1_b1_gbt_r3.py','bytes':1,'sha256':'a'*64}
                worker={'path':'implementation/service_v1_b1_spark_worker.py','bytes':1,'sha256':'b'*64}
                manifest={'status':'B1_FULL_PREFLIGHT_COMPLETE_AWAITING_REVIEW','resourceStatus':'PASS','runId':h.RUN_ID,'profileId':h.PROFILE_ID,
                  'sourceRows':h.SOURCE_ROWS,'logicalRows':h.LOGICAL_ROWS,'partitionCount':8,'fitAuthorized':False,'modelFitPerformed':False,
                  'scorePerformed':False,'readyForService':False,'outerRunnerSha256':outer['sha256'],'sparkWorkerSha256':worker['sha256'],
                  'implementationSetSha256':h.implementation_set_sha256(outer,worker),'files':{},**dig}; w(b/'manifest.json',manifest)
                (ar.standalone/'scripts').mkdir(parents=True); (ar.standalone/'scripts/audit_service_v1_b1_spark_outputs_r3.py').write_bytes((root_repo/'scripts/audit_service_v1_b1_spark_outputs_r3.py').read_bytes())
                auditor=a.pin(root_repo/'scripts/audit_service_v1_b1_spark_outputs_r3.py')
                dependency={'files':{},'bundleInventories':{},'auditorImplementation':auditor,'dockerImageId':a.IMAGE_ID,'runId':a.RUN_ID,'profileId':a.PROFILE_ID}
                target={'input_lock':{'path':'standalone/lock.json','bytes':1,'sha256':'e'*64,**dig}}
                resource={'stages':2,'peakBytes':100,'cleanupConfirmed':True,'labelMountAbsent':True,'measurementIndependentlyObserved':False,'measurementCheckedAgainstPinnedWorkerLog':True}
                envelope={'manifest':manifest,'inventory':{}}
                pins={'outer':outer,'worker':worker,'executionDigest':dig['executionSetSha256']}
                identity={'sourceRows':h.SOURCE_ROWS,'logicalRows':h.LOGICAL_ROWS,'partitions':[]}
                w(b/'partition-identity.json',identity)
                with mock.patch.object(a,'dependency_fingerprint',return_value=dependency),mock.patch.object(a,'audit_inventory',return_value=envelope),\
                     mock.patch.object(a,'audit_lock',return_value=pins),mock.patch.object(a,'audit_resource_and_commands',return_value=resource),\
                     mock.patch.object(a,'audit_recovery_reference',return_value={'reference':'PASS'}),mock.patch.object(a,'recalculate_identity',return_value=identity),\
                     mock.patch.object(a,'inventory',return_value={}),mock.patch.object(a,'target_records',return_value=target):
                    review=a.audit_phase(ar,'preflight',outer['sha256'],worker['sha256'],_limits=a.Limits(source_rows=h.SOURCE_ROWS,canonical=False))
                    a.publish_review(ar,'preflight',review)
                with mock.patch.object(h,'expected_review_dependency',return_value=dependency),mock.patch.object(h,'review_contract',return_value={'target':target}),\
                     mock.patch.object(h,'relative_inventory',return_value={}),mock.patch.object(h,'pin_file',return_value=auditor),\
                     mock.patch.object(h,'implementation_pins',return_value=(outer,worker,h.implementation_set_sha256(outer,worker))):
                    accepted=h.validate_preflight_review(hp)
                if accepted['review']['status']!='PASS': raise SystemExit(2)
                print('SYNTHETIC_PREFLIGHT_REVIEW_ACCEPTED_BY_FIT_GATE')
        ''')
        command = [sys.executable]
        if sys.flags.optimize:
            command.append("-O")
        command += ["-c", code]
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("SYNTHETIC_PREFLIGHT_REVIEW_ACCEPTED_BY_FIT_GATE", result.stdout)


class StaticContractTests(unittest.TestCase):
    def test_no_assert_and_targeted_call_arities(self) -> None:
        tree = ast.parse((SCRIPTS / "audit_service_v1_b1_spark_outputs_r3.py").read_text(encoding="utf-8"))
        self.assertEqual(sum(isinstance(node, ast.Assert) for node in ast.walk(tree)), 0)
        expected = {"audit_recovery_reference": 5, "audit_reference": 7, "audit_lock": 7,
                    "audit_resource_and_commands": 4}
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            if call.func.id in expected: self.assertEqual(len(call.args), expected[call.func.id], (call.lineno, call.func.id))


if __name__ == "__main__":
    unittest.main()
