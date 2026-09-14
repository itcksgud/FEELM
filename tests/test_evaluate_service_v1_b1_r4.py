from __future__ import annotations
import ast
import copy
from dataclasses import asdict, replace
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ev = load("evaluate_service_v1_b1_r4", ROOT / "scripts/evaluate_service_v1_b1_r4.py")
old = load("r4_reused_r3_fixture", ROOT / "tests/test_evaluate_service_v1_b1_r3.py")


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def resource(phase="selection"):
    unit = "feelm-b1-r4-server5c20g-t28800-" + phase
    events = {k: 0 for k in ("low", "high", "max", "oom", "oomKill", "oomGroupKill")}
    pid = {"pid": 22, "ppid": 1, "startTimeTicks": 100, "cmdlineSha256": "a" * 64}
    stamp = "2026-09-14T10:00:00Z"
    return {"schemaVersion": "feelm-service-v1-b1-r4-evaluator-resource/1", "status": "PASS",
        "runId": ev.RUN_ID, "profileId": ev.PROFILE_ID,
        "phase": "calibrate-select" if phase == "selection" else phase,
        "unitName": unit + ".service", "monitorUnitName": unit + "-monitor.service",
        "controlGroup": "/system.slice/" + unit + ".service", "mainPid": 22, "initialPidTree": [pid],
        "limits": {"cpuQuotaPercent": 500, "memoryMaxBytes": 21474836480, "memorySwapMaxBytes": 0,
                   "tasksMax": 4096, "killMode": "control-group", "runtimeMaxSeconds": 14400,
                   "timeoutStopSeconds": 900}, "sampleIntervalSeconds": 2.0,
        "samples": [{"observedAt": stamp, "monotonicNanos": 10, "memoryCurrentBytes": 100,
                     "memoryPeakBytes": 200, "memoryEvents": events, "cpuUsageUsec": 30,
                     "cpuUserUsec": 20, "cpuSystemUsec": 10, "pidsCurrent": 1, "pidTree": [pid]}],
        "peaks": {"memoryPeakBytes": 200, "maxPidsCurrent": 1, "lastSampleAt": stamp},
        "finalEvents": events,
        "termination": {"timedOut": False, "oomKilled": False, "exitCode": 0, "signal": None,
                        "stopRequestedAt": None, "finalSampleAt": stamp},
        "cleanup": {"targetInactive": True, "monitorExitCode": 0, "unitCollected": True,
                    "pidTreeEmpty": True, "errors": [], "complete": True}}


class Fixture:
    def __init__(self, root):
        self.old = old.SyntheticFixture(root)
        values = asdict(self.old.paths)
        self.evaluator = self.old.root / "scripts/evaluate_service_v1_b1_r4.py"
        self.auditor = self.old.root / "scripts/audit_service_v1_b1_evaluation_outputs_r4.py"
        shutil.copyfile(ev.SCRIPT, self.evaluator)
        shutil.copyfile(ROOT / "scripts/audit_service_v1_b1_evaluation_outputs_r4.py", self.auditor)
        values["evaluation_auditor"] = self.auditor
        self.paths = ev.EvaluationPaths(**values)
        self.spec = ev.EvaluationSpec(**asdict(self.old.spec))
        self.pins = self.old.fixed_pins
        control_root = self.old.root / "controls"
        control_root.mkdir()
        self.controls = ev.ControlPaths(**{name: control_root / (name + ".json") for name in ev.CONTROL_NAMES})
        basic = {"runId": ev.RUN_ID, "profileId": ev.PROFILE_ID}
        for name in ev.CONTROL_NAMES:
            dump(getattr(self.controls, name), basic)
        dump(self.controls.score_manifest, {**basic, "evaluationAuthorized": False,
            "evaluationTargetsRead": False, "labelProjected": False, "readyForService": False})
        for name, schema, gate, target in (
            ("score_review", "feelm-service-v1-b1-r4-result-review/1", "evaluationSelectionEligible", "score_manifest"),
            ("delivery_review", "feelm-service-v1-b1-r4-server-delivery-review/1", "serverTransferEligible", "delivery_manifest"),
            ("server_receipt_review", "feelm-service-v1-b1-r4-server-receipt-review/1", "publicPreflightEligible", "server_receipt_manifest")):
            dump(getattr(self.controls, name), {**basic, "schemaVersion": schema, "status": "PASS",
                "decision": {gate: True}, "target": {"manifest": ev.path_pin(getattr(self.controls, target))}})
        dump(self.controls.host_runtime_lock, {"schemaVersion": "feelm-service-v1-b1-r4-host-runtime-lock/1"})
        self.expected = {name: ev.sha256_file(getattr(self.controls, name)) for name in ev.CONTROL_NAMES}
        self.host = {"schemaVersion": "feelm-service-v1-b1-r4-dynamic-host-gate/2", "status": "PASS",
                     **basic, "phase": "calibrate-select", "unknownFields": []}

    def state(self):
        return ev.verify_score_review_gate(self.controls, self.expected)

    def truth(self):
        inputs, ids, roles, digest = ev.source_state(self.paths, self.spec, self.pins)
        prediction = ev.load_predictions(self.old.predictions, self.paths.score_axis, self.spec)
        return ev.prepare_truth(self.paths, self.spec, roles, ids, prediction)[0]

    def prepare(self, stage):
        stage.mkdir()
        with mock.patch.object(ev, "SCRIPT", self.evaluator):
            return ev.prepare_phase(self.paths, stage, "selection", control_state=self.state(),
                host_gate=self.host, predictions=self.old.predictions, dependency_records=[],
                _spec=self.spec, _fixed_pins=self.pins)


class EvaluationR4Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fx = Fixture(Path(self.temp.name))

    def test_normal_fixed_axis_metrics_and_staging(self):
        stage = self.fx.old.root / "stage"
        result = self.fx.prepare(stage)
        self.assertEqual(result["truthCensus"]["joinedRows"], 28)
        self.assertFalse((stage / "manifest.json").exists())
        self.assertFalse((stage / "evaluator-resource.json").exists())
        self.assertEqual(ev._bundle_inventory(stage), ev.SELECTION_FILES - {"manifest.json", "evaluator-resource.json"})
        metrics = ev.read_json(stage / "metrics.json")
        self.assertEqual(set(metrics["models"]["B1"]["ALL"]["ranking"]), {"2", "4", "6", "10"})
        self.assertEqual(metrics["models"]["B1"]["ALL"]["ranking"]["10"]["ndcgValidUsers"], 0)
        self.assertIsNone(metrics["models"]["B1"]["ALL"]["ranking"]["10"]["ndcg"])
        self.assertEqual(ev.read_json(stage / "gates.json")["serviceActivationAuthorized"], False)

    def test_denied_score_has_no_evaluation_filesystem_access(self):
        payload = ev.read_json(self.fx.controls.score_review)
        payload["decision"]["evaluationSelectionEligible"] = False
        dump(self.fx.controls.score_review, payload)
        self.fx.expected["score_review"] = ev.sha256_file(self.fx.controls.score_review)
        forbidden = {str(getattr(self.fx.paths, n)) for n in ev.SOURCE_PINS}
        old_stat, old_open, old_resolve = Path.stat, Path.open, Path.resolve
        def check(path):
            self.assertNotIn(str(path), forbidden)
        def guarded_stat(path, *a, **k):
            check(path); return old_stat(path, *a, **k)
        def guarded_open(path, *a, **k):
            check(path); return old_open(path, *a, **k)
        def guarded_resolve(path, *a, **k):
            check(path); return old_resolve(path, *a, **k)
        with mock.patch.object(Path, "stat", guarded_stat), mock.patch.object(Path, "open", guarded_open), \
             mock.patch.object(Path, "resolve", guarded_resolve):
            with self.assertRaisesRegex(ev.ContractError, "does not authorize"):
                self.fx.state()

    def test_review_target_requires_same_canonical_file(self):
        target = self.fx.controls.score_manifest
        item = {"target": {"manifest": ev.path_pin(target)}}
        ev._review_manifest_target(item, target)
        with mock.patch.object(ev, "ROOT", self.fx.old.root):
            item["target"]["manifest"]["path"] = "standalone/" + target.relative_to(self.fx.old.root).as_posix()
            ev._review_manifest_target(item, target)
        item["target"]["manifest"]["path"] = "somewhere/" + target.name
        with self.assertRaises(ev.ContractError):
            ev._review_manifest_target(item, target)

    def test_stale_score_hash_blocks(self):
        self.fx.controls.score_review.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ev.ContractError, "SHA drift"):
            self.fx.state()

    def test_duplicate_json_key_and_nonfinite_rejected(self):
        p = Path(self.temp.name) / "bad.json"
        for value in ('{"a":1,"a":2}', '{"a":NaN}'):
            p.write_text(value, encoding="utf-8")
            with self.assertRaises(ev.ContractError):
                ev.read_json(p)

    def test_prediction_order_uid_and_nan_rejected(self):
        original = pq.read_table(self.fx.old.predictions).to_pandas()
        for mutation in ("order", "uid", "nan"):
            frame = original.copy()
            if mutation == "order": frame = frame.iloc[::-1]
            elif mutation == "uid": frame.loc[0, "uid"] = 999
            else: frame.loc[0, "prediction"] = np.nan
            p = Path(self.temp.name) / (mutation + ".parquet")
            pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), p)
            with self.assertRaises(ev.ContractError):
                ev.load_predictions(p, self.fx.paths.score_axis, self.fx.spec)

    def test_missing_truth_and_non_half_stars_rejected(self):
        truth = self.fx.truth()
        bad = truth.copy(); bad.loc[0, "rating"] = 3.7
        with self.assertRaises(ev.ContractError): ev.compute_phase_outputs("selection", bad, self.fx.spec)
        bad = truth.copy(); bad.loc[0, "rating"] = np.nan
        with self.assertRaises(ev.ContractError): ev.compute_phase_outputs("selection", bad, self.fx.spec)
        bad = pd.concat([truth, truth.iloc[[0]]], ignore_index=True)
        with self.assertRaises(ev.ContractError): ev.compute_phase_outputs("selection", bad, self.fx.spec)

    def test_affine_uses_only_calibration_and_confirmation_uses_frozen_fit(self):
        truth = self.fx.truth()
        first = ev.compute_phase_outputs("selection", truth, self.fx.spec)
        altered = truth.copy()
        altered.loc[altered.role.ne("CALIBRATION"), "rating"] = .5
        second = ev.compute_phase_outputs("selection", altered, self.fx.spec)
        self.assertEqual(first[4], second[4])
        confirmed = ev.compute_phase_outputs("confirmation", truth, self.fx.spec, first[4])
        self.assertIsNone(confirmed[4])
        with self.assertRaises(ev.ContractError): ev.compute_phase_outputs("confirmation", truth, self.fx.spec)

    def test_constant_calibration_is_insufficient_not_fallback(self):
        truth = self.fx.truth()
        truth.loc[truth.role.eq("CALIBRATION"), "b1_raw"] = 3
        out = ev.compute_phase_outputs("selection", truth, self.fx.spec)
        self.assertEqual(out[3]["requiredGate"], "INSUFFICIENT")
        self.assertEqual(out[4]["models"]["B1"]["state"], "CALIBRATION_UNAVAILABLE")

    def test_metric_ties_halfstars_and_shortage(self):
        self.assertEqual(ev.ndcg_at(np.array([.5, .5]), np.array([1, 1]), np.array([1, 2]), 2), None)
        self.assertEqual(ev.ndcg_at(np.array([5, 1]), np.array([1, 1]), np.array([1, 2]), 2), 1.0)
        self.assertIsNone(ev.ndcg_at(np.array([5]), np.array([1]), np.array([1]), 2))
        self.assertTrue(ev.valid_half_stars(np.arange(.5, 5.1, .5)))

    def test_bootstrap_reproducible_and_zero_denominator(self):
        x = np.array([0., 0., 0.])
        self.assertEqual(ev.relative_mse_bootstrap(x, x, samples=80, minimum=1)["ciHigh"], 0.)
        a = ev.paired_bootstrap(np.arange(5.), np.arange(5.) + .2, samples=80, minimum=1)
        self.assertEqual(a, ev.paired_bootstrap(np.arange(5.), np.arange(5.) + .2, samples=80, minimum=1))

    def test_external_resource_requires_complete_realistic_census(self):
        good = resource(); ev.validate_evaluator_resource(good, "selection")
        mutations = [lambda v: v.update(samples=[]), lambda v: v["peaks"].update(memoryPeakBytes=None),
            lambda v: v["termination"].update(timedOut=True), lambda v: v["cleanup"].update(complete=False),
            lambda v: v["cleanup"].update(monitorExitCode=1), lambda v: v["limits"].update(memoryMaxBytes=True),
            lambda v: v["samples"][0]["memoryEvents"].update(oomKill=1),
            lambda v: v["samples"][0]["pidTree"].append({"pid": 23, "ppid": 99, "startTimeTicks": 1,
                                                       "cmdlineSha256": "b" * 64})]
        for mutation in mutations:
            value = copy.deepcopy(good); mutation(value)
            with self.subTest(value=value):
                with self.assertRaises(ev.ContractError): ev.validate_evaluator_resource(value, "selection")

    def test_ast_has_no_assert_and_pins_exact_one_literal(self):
        for name in ("evaluate_service_v1_b1_r4.py", "audit_service_v1_b1_evaluation_outputs_r4.py"):
            tree = ast.parse((ROOT / "scripts" / name).read_text(encoding="utf-8"))
            self.assertFalse(any(isinstance(n, ast.Assert) for n in ast.walk(tree)))
        core, pin = ev.evaluation_auditor_contract(ROOT / "scripts/audit_service_v1_b1_evaluation_outputs_r4.py")
        self.assertEqual(len(core), 64)
        self.assertEqual(len(pin), 64)

    def test_worker_request_has_no_contract_or_bootstrap_override(self):
        request = {"controls": {k: str(getattr(self.fx.controls, k)) for k in ev.CONTROL_NAMES},
                   "expectedSha256": self.fx.expected, "stagingPath": str(self.fx.old.root / "stage"),
                   "hostGate": self.fx.host, "dependencyRecords": [], "selectionReference": None}
        self.assertEqual(ev.parse_worker_request(json.dumps(request)), request)
        for key in ("spec", "bootstrap_samples", "fixed_pins", "force", "skip_gate"):
            changed = {**request, key: True}
            with self.assertRaises(ev.ContractError): ev.parse_worker_request(json.dumps(changed))

    def test_gate_denial_precedes_input_inventory(self):
        with mock.patch.object(ev, "source_state", side_effect=RuntimeError("data touched")) as touched:
            with self.assertRaisesRegex(ev.ContractError, "supervised host gate"):
                ev.prepare_phase(self.fx.paths, self.fx.old.root / "stage", "selection",
                    control_state=self.fx.state(), host_gate={**self.fx.host, "status": "BLOCK"},
                    predictions=self.fx.old.predictions, dependency_records=[])
            touched.assert_not_called()

    def test_training_user_overlap_is_rejected(self):
        table = pa.table({"uid": pa.array([11, 11, 11, 1002, 1002, 1002], type=pa.int32())})
        pq.write_table(table, self.fx.paths.ratings)
        roles, _ = ev.split_roles(pd.read_csv(self.fx.paths.roles), self.fx.spec)
        with self.assertRaisesRegex(ev.ContractError, "overlap"):
            ev.validate_training_user_disjointness(self.fx.paths.ratings, roles, self.fx.spec)

    def test_auditor_normalization_rejects_zero_or_two_assignments(self):
        source = (ROOT / "scripts/audit_service_v1_b1_evaluation_outputs_r4.py").read_text(encoding="utf-8")
        path = Path(self.temp.name) / "auditor.py"
        for changed in (source.replace("REVIEWED_R4_EVALUATOR_SHA256:", "OTHER_PIN:"),
                        source + '\nREVIEWED_R4_EVALUATOR_SHA256: str = "' + 'a' * 64 + '"\n'):
            path.write_text(changed, encoding="utf-8")
            with self.assertRaises(ev.ContractError): ev.evaluation_auditor_contract(path)


if __name__ == "__main__": unittest.main()
