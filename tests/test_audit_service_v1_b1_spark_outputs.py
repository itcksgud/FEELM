"""Synthetic-only tests: never read production data or invoke Docker/Spark."""
from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit_service_v1_b1_spark_outputs.py"
SPEC = importlib.util.spec_from_file_location("independent_b1_auditor", SCRIPT)
audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")


def make_train(path, n=32, masked=False, rows_per_group=7):
    values = {"row_id": np.arange(n, dtype=np.int64), "uid": np.arange(n, dtype=np.int32) % 9,
              "label": np.full(n, 3.5, dtype=np.float64)}
    for i, name in enumerate(audit.FEATURES):
        values[name] = np.arange(n, dtype=np.float32) / n if i == 0 else np.full(n, i/1000, dtype=np.float32)
        if masked and i >= 200:
            values[name] = np.zeros(n, dtype=np.float32)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pydict(values, schema=audit.train_schema()), path, row_group_size=rows_per_group)


def make_model(root, trees=2, params=None):
    root.mkdir(parents=True)
    write_json(root / "metadata/part-00000", {"class": "org.apache.spark.ml.regression.GBTRegressionModel",
               "numFeatures": 230, "numTrees": trees, "paramMap": params or {}})
    nodes, weights = [], []
    for t in range(trees):
        weights.append({"_1": t, "_2": "metadata", "_3": 1.0 if t == 0 else .1})
        for index, left, right, prediction in ((0, 1, 2, 0.), (1, -1, -1, 1.), (2, -1, -1, 3.)):
            nodes.append({"treeID": t, "nodeData": {"id": index, "leftChild": left, "rightChild": right,
                "prediction": prediction, "split": {"featureIndex": 0 if index == 0 else -1,
                    "leftCategoriesOrThreshold": [.5] if index == 0 else [], "numCategories": -1}}})
    for name, data in (("data", nodes), ("treesMetadata", weights)):
        (root / name).mkdir()
        pq.write_table(pa.Table.from_pylist(data), root / name / "part-00000.parquet")


def make_threshold(path, trees=2):
    x = np.zeros((3*trees, 230), dtype=np.float64)
    x[:, 0] = np.tile([np.nextafter(.5, -np.inf), .5, np.nextafter(.5, np.inf)], trees)
    y = np.where(x[:, 0] <= .5, 1., 3.) * (1 + .1*(trees-1))
    np.savez(path, features=x, predictions=y.astype(np.float64), indices=np.arange(230, dtype=np.int32),
             split_features=np.zeros(3*trees, dtype=np.int32), split_thresholds=np.full(3*trees, .5, dtype=np.float64))


class BundleFixture:
    def __init__(self, root):
        self.roots = audit.Roots(root / "standalone", root / "team")
        self.limits = audit.Limits(source_rows=32, score_rows=12, trees=2, canonical=False)
        predecessor = self.roots.predecessor_failure()
        predecessor.parent.mkdir(parents=True, exist_ok=True)
        write_json(predecessor, {
            "schemaVersion": "feelm-service-v1-b1-failure/1",
            "phase": "preflight",
            "status": "FAILED",
            "cleanupComplete": True,
            "containerRun": {
                "dockerState": {"ExitCode": 1, "OOMKilled": False},
                "timedOut": False,
                "stdout": "masked bundle file set is not exactly the reviewed three files",
            },
        })
        self.sources = self.roots.sources()
        for name, path in self.sources.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix != ".parquet":
                path.write_text("fixture " + name, encoding="utf-8")
        make_train(self.sources["source/natural-train.parquet"])
        make_train(self.sources["source/tmdb-masked-train.parquet"], masked=True, rows_per_group=3)
        make_train(self.sources["source/natural-score.parquet"], 12)
        self.params = {"maxIter": 2, "maxDepth": 1, "seed": 339, "weightCol": "weight"}
        write_json(self.sources["contract/training-recipe.v1.json"], {"models": {"GBT": {"parameters": self.params}}})
        self.outer = audit.digest(self.sources["implementation/run_service_v1_b1_gbt.py"])
        self.worker = audit.digest(self.sources["implementation/service_v1_b1_spark_worker.py"])

    def source_records(self, phase):
        records = []
        for name in sorted(audit.SCORE_NAMES if phase == "score" else audit.TRAIN_NAMES):
            if name == "runtime/docker-image-id":
                raw = audit.IMAGE_ID.encode()
                records.append({"path": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "value": audit.IMAGE_ID})
            else:
                records.append(audit.pin(self.sources[name], name))
        return records

    def command_resource(self, phase, manifest):
        b = self.roots.bundle(phase)
        actions = ["dry-run-first-row-group", "preflight"] if phase == "preflight" else [phase]
        sequence, stages, logs = [], [], []
        allowed = set(audit.readonly_mount_map(self.roots, phase).values())
        if phase == "score":
            allowed = {self.sources[k] for k in ("implementation/run_service_v1_b1_gbt.py", "implementation/service_v1_b1_spark_worker.py",
                       "implementation/service-v1-b1-spark-runner.md", "contract/feature-schema.v1.json", "source/natural-score.parquet")}
            allowed |= {self.roots.bundle("fit"), self.roots.review("fit")}
        statuses = {"dry-run-first-row-group": "DRY_RUN_ONLY_NOT_PUBLISHED", "preflight": "B1_FULL_PREFLIGHT_WORKER_COMPLETE",
                    "fit": "B1_MODEL_FIT_WORKER_COMPLETE", "score": "B1_NATURAL_SCORE_WORKER_COMPLETE"}
        for i, action in enumerate(actions):
            name = "feelm-b1-" + phase + "-" + str(i)
            args = ["docker", "create", "--name", name, "--network", "none", "--hostname", "service-v1-b1",
                    "--add-host", "service-v1-b1:127.0.0.1", "-e", "SPARK_LOCAL_IP=127.0.0.1", "--cpus", "4", "--memory", "12g", "--memory-swap", "12g"]
            targets = audit.readonly_mount_map(self.roots, phase)
            if phase == "score":
                # Independently reproduce the real producer's full-fit mount;
                # do not derive this fixture's contract from the consumer map.
                targets = {
                    "/app/run_service_v1_b1_gbt.py": self.sources["implementation/run_service_v1_b1_gbt.py"],
                    "/app/service_v1_b1_spark_worker.py": self.sources["implementation/service_v1_b1_spark_worker.py"],
                    "/contract/service-v1-b1-spark-runner.md": self.sources["implementation/service-v1-b1-spark-runner.md"],
                    "/contract/feature-schema.v1.json": self.sources["contract/feature-schema.v1.json"],
                    "/input/natural-score.parquet": self.sources["source/natural-score.parquet"],
                    "/fit": self.roots.bundle("fit"),
                    "/control/fit-review.json": self.roots.review("fit"),
                }
            self_target_paths = set(targets.values())
            if self_target_paths != {p.resolve() for p in allowed}:
                raise ValueError("fixture source inventory mismatch")
            for target, path in sorted(targets.items()):
                args += ["--mount", f"type=bind,source={path},target={target},readonly"]
            args += ["--mount", f"type=bind,source={b.parent / ('.'+b.name+'.scratch-'+str(i))},target=/scratch"]
            if action != "dry-run-first-row-group":
                args += ["--mount", f"type=bind,source={b.parent / ('.'+b.name+'.tmp-fixture')},target=/output"]
            args += ["feelm-rec046-spark:local", "/opt/spark/bin/spark-submit", "--master", "local[4]", "--driver-memory", "8g",
                     "--conf", "spark.sql.shuffle.partitions=8", "--conf", "spark.sql.adaptive.enabled=false", "--conf", "spark.ui.enabled=false", "--conf", "spark.sql.debug.maxToStringFields=1000",
                     "--conf", "spark.local.dir=/scratch/spark-local", "/app/service_v1_b1_spark_worker.py", action]
            if phase == "score":
                args += ["--score-input", "/input/natural-score.parquet", "--model-dir", "/fit/model/native", "--output", "/output", "--scratch", "/scratch"]
            else:
                args += ["--natural", "/input/natural-train.parquet", "--masked", "/masked/tmdb-masked-rh230.parquet",
                         "--masked-manifest", "/masked/manifest.json", "--views-manifest", "/masked/views-manifest.json",
                         "--masked-review", "/review/masked-result-review.json", "--scratch", "/scratch"]
                if action != "dry-run-first-row-group":
                    args += ["--training-recipe", "/contract/training-recipe.v1.json", "--output", "/output"]
            sequence.append({"order": i, "workerAction": action, "command": args})
            measurement = {"resourceStatus": "PASS", "peakBytes": 1024, "peakSource": "/sys/fs/cgroup/memory.peak",
                           "limitBytes": audit.MAX_MEMORY_BYTES, "oomKilled": False, "exitCode": 0, "timedOut": False}
            stages.append({"workerAction": action, "containerName": name, "workerStatus": statuses[action],
                "startedAt": f"2026-09-13T00:00:0{2*i}Z", "completedAt": f"2026-09-13T00:00:0{2*i+1}Z", "elapsedSeconds": 1,
                "resource": measurement, "cleanup": {"confirmedStopped": True, "removed": True}})
            logs.append({"schemaVersion": "feelm-service-v1-b1-spark-worker/1", "status": statuses[action],
                         "runtimeVersions": {"sparkVersion": "4.1.3", "javaVersion": "21.0.5", "pythonVersion": "3.11.2"},
                         "modelFitPerformed": action == "fit", "scorePerformed": action == "score", "resourceObservation": measurement})
        write_json(b / "command.json", {"schemaVersion": "feelm-service-v1-b1-command/1", "phase": phase,
                   "createdAt": "2026-09-13T00:00:00Z", "dockerImage": "feelm-rec046-spark:local", "dockerImageId": audit.IMAGE_ID,
                   "sequence": sequence, **({"dryRunPrecedesFullMaterialization": True} if phase == "preflight" else {})})
        write_json(b / "resource.json", {"schemaVersion": "feelm-service-v1-b1-resource/1", "phase": phase,
                   "status": "PASS", "limitBytes": audit.MAX_MEMORY_BYTES, "stages": stages})
        (b / "run.log").write_text("\n".join(json.dumps(x) for x in logs), encoding="utf-8")

    def build(self, phase):
        roots, b = self.roots, self.roots.bundle(phase)
        b.mkdir(parents=True)
        sources, controls = self.source_records(phase), audit.controls_for(roots, phase)
        is_score = phase == "score"
        source_key = "scoreSourceRecords" if is_score else "trainingSourceRecords"
        set_key = "scoreSourceSetSha256" if is_score else "trainingSourceSetSha256"
        lock = {"schemaVersion": "feelm-service-v1-b1-" + ("score-input-lock/1" if is_score else "input-lock/1"), "phase": phase,
                source_key: sources, "controlReferences": controls, set_key: audit.set_digest(sources),
                "controlReferenceSetSha256": audit.set_digest(controls), "inputSetSha256": audit.set_digest(sources+controls), "evaluationTargetsRead": False}
        write_json(b / ("score-input-lock.json" if is_score else "input-lock.json"), lock)
        smap = {x["path"]: x for x in sources}
        outer, worker = smap["implementation/run_service_v1_b1_gbt.py"], smap["implementation/service_v1_b1_spark_worker.py"]
        implementation = audit.set_digest([outer, worker])
        manifest = {"schemaVersion": f"feelm-service-v1-b1-{phase}-manifest/1", "status": audit.STATUSES[phase],
                    "runId": audit.RUN_ID,
                    "runtimeVersions": {"sparkVersion": "4.1.3", "javaVersion": "21.0.5", "pythonVersion": "3.11.2"},
                    "modelFitPerformed": phase == "fit", "scorePerformed": is_score, "outerRunnerSha256": self.outer,
                    "sparkWorkerSha256": self.worker, "implementationSetSha256": implementation, set_key: lock[set_key],
                    "controlReferenceSetSha256": lock["controlReferenceSetSha256"], "inputSetSha256": lock["inputSetSha256"], "resourceStatus": "PASS"}
        if not is_score:
            identity = audit.recalculate_identity(self.sources["source/natural-train.parquet"], self.sources["source/tmdb-masked-train.parquet"], self.limits)
            write_json(b / "partition-identity.json", identity)
            manifest.update(sourceRows=32, logicalRows=128, partitionCount=8, partitionIdentitySha256=audit.digest(b / "partition-identity.json"))
        if phase == "preflight":
            recovery = {
                "schemaVersion": "feelm-service-v1-b1-recovery-reference/1",
                "status": "PREDECESSOR_FAILURE_VERIFIED",
                "predecessorRunId": audit.PREDECESSOR_RUN_ID,
                "runId": audit.RUN_ID,
                "predecessorFailure": controls[0],
                "predecessorOuterRunnerSha256": audit.PREDECESSOR_OUTER_RUNNER_SHA256,
                "predecessorSparkWorkerSha256": audit.PREDECESSOR_SPARK_WORKER_SHA256,
                "outerRunnerSha256": self.outer,
                "sparkWorkerSha256": self.worker,
                "failureClass": "MASKED_BUNDLE_CONTAINER_MOUNT_WIRING",
                "modelFitPerformed": False,
                "fullPreflightPerformed": False,
            }
            write_json(b / "recovery-reference.json", recovery)
            manifest.update(
                fitAuthorized=False,
                dryRunPrecedesFullMaterialization=True,
                predecessorFailureSha256=controls[0]["sha256"],
                recoveryReferenceSha256=audit.digest(b / "recovery-reference.json"),
            )
        else:
            previous = "preflight" if phase == "fit" else "fit"
            previous_manifest = roots.bundle(previous) / "manifest.json"
            reference = {"schemaVersion": f"feelm-service-v1-b1-{previous}-reference/1",
                         previous+"Manifest": audit.pin(previous_manifest, roots.logical(previous_manifest)),
                         previous+"Review": audit.pin(roots.review(previous), roots.logical(roots.review(previous))),
                         "outerRunner": outer, "sparkWorker": worker, "implementationSetSha256": implementation}
            manifest.update({previous+"ManifestSha256": audit.digest(previous_manifest), previous+"ReviewSha256": audit.digest(roots.review(previous)), "readyForService": False})
            if phase == "fit":
                reference.update(reviewedArtifacts=controls, trainingSourceSetSha256=lock[set_key], fitInputSetSha256=lock["inputSetSha256"])
                manifest.update(scoringAuthorized=False, treeCount=2)
                make_model(b / "model/native", params=self.params)
                native = audit.inventory(b / "model/native")
                files = [{"path": name, **p} for name, p in sorted(native.items())]
                model_inventory = {"schemaVersion": "feelm-service-v1-b1-model-inventory/1", "files": files, "inventorySha256": audit.set_digest(files)}
                write_json(b / "model-file-inventory.json", model_inventory)
                make_threshold(b / "threshold-fixtures.npz")
                write_json(b / "resolved-estimator.json", self.params)
                parity = {"status": "PASS", "maximumAbsoluteError": 0., "tolerance": 1e-6, "fixtureRows": 6, "nonLeafSplits": 2}
                write_json(b / "fit-metrics.json", {"schemaVersion": "feelm-service-v1-b1-fit-metrics/1", "model": "GBT120_s339_B1", "sourceRows": 32,
                           "logicalRows": 128, "viewWeight": .25, "treeCount": 2, "nonLeafSplitCount": 2, "thresholdFixtureRows": 6,
                           "weightedRawRmse": 1., "fitSeconds": 1., "workerSeconds": 1., "portableParity": parity})
                manifest.update(modelInventorySha256=audit.digest(b / "model-file-inventory.json"), modelFileSetSha256=model_inventory["inventorySha256"],
                                thresholdFixtureSha256=audit.digest(b / "threshold-fixtures.npz"), portableParity=parity)
            else:
                parent = roots.bundle("fit")
                model_path = parent / "model-file-inventory.json"
                model_inventory = audit.json_object(model_path)
                reference.update(modelInventory=audit.pin(model_path, roots.logical(model_path)), modelFileSetSha256=model_inventory["inventorySha256"], modelFiles=model_inventory["files"])
                (b / "score").mkdir()
                (b / "score/analyzed-plan.txt").write_text("features " + " ".join(["row_id", "uid", *audit.FEATURES]), encoding="utf-8")
                natural = pq.read_table(self.sources["source/natural-score.parquet"], columns=["row_id", "uid", "x000"])
                y = np.where(natural["x000"].to_numpy() <= .5, 1.1, 3.3)
                pq.write_table(pa.table({"row_id": natural["row_id"], "uid": natural["uid"], "prediction": pa.array(y, type=pa.float64())}), b / "score/predictions.parquet")
                manifest.update(evaluationAuthorized=False, rows=12, labelProjected=False, modelInventorySha256=audit.digest(model_path),
                    modelFileSetSha256=model_inventory["inventorySha256"], predictionSha256=audit.digest(b / "score/predictions.parquet"),
                    scoreCensus={"rows": 12, "rowIdMinimum": 0, "rowIdMaximum": 11, "rowIdsUnique": True, "uidAxisEqual": True,
                                 "predictionsFinite": True, "singlePhysicalParquetFile": True})
            write_json(b / (previous+"-reference.json"), reference)
        self.command_resource(phase, manifest)
        manifest["files"] = audit.inventory(b)
        write_json(b / "manifest.json", manifest)

    def run(self, phase):
        return audit.audit_phase(self.roots, phase, self.outer, self.worker, _limits=self.limits)

    def publish(self, phase):
        return audit.publish_review(self.roots, phase, self.run(phase))

    def repin_manifest(self, phase):
        b = self.roots.bundle(phase)
        value = audit.json_object(b / "manifest.json")
        value["files"] = {k: v for k, v in audit.inventory(b).items() if k != "manifest.json"}
        write_json(b / "manifest.json", value)


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="b1-auditor-synthetic-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def fixture(self, through="preflight"):
        f = BundleFixture(self.root)
        for phase in ("preflight", "fit", "score"):
            f.build(phase)
            if phase == through:
                break
            f.publish(phase)
        return f

    def test_full_synthetic_chain_and_immutable_publication(self):
        f = self.fixture("score")
        before = audit.inventory(f.roots.bundle("score"))
        review = f.run("score")
        self.assertFalse(f.roots.review("score").exists())
        self.assertTrue(review["checks"]["predictions"]["uidParity"])
        path = audit.publish_review(f.roots, "score", review)
        original = path.read_bytes()
        with self.assertRaises(audit.AuditError):
            audit.publish_review(f.roots, "score", review)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(audit.inventory(f.roots.bundle("score")), before)

    def test_recomputed_identity_matches_independent_binary_encoding(self):
        f = BundleFixture(self.root)
        identity = audit.recalculate_identity(f.sources["source/natural-train.parquet"], f.sources["source/tmdb-masked-train.parquet"], f.limits)
        partitions = audit.spark_long_partitions(np.arange(32, dtype=np.int64))
        for p, record in enumerate(identity["partitions"]):
            rows = [r for r in range(32) if partitions[r] == p]
            expected = b"".join(struct.pack("<qiidd", r, v, r % 9, 3.5, .25) for r in rows for v in range(4))
            self.assertEqual(record["logicalIdentitySha256"], hashlib.sha256(expected).hexdigest())

    def test_canonical_spark_partition_pins_from_synthetic_integer_axis(self):
        ids = np.arange(4_997_069, dtype=np.int64)
        partition = audit.spark_long_partitions(ids)
        self.assertEqual(tuple(np.bincount(partition, minlength=8)), audit.PARTITION_COUNTS)
        self.assertEqual(tuple(hashlib.sha256(ids[partition == p].astype("<i8").tobytes()).hexdigest() for p in range(8)), audit.PARTITION_PINS)

    def test_missing_extra_and_mutated_sibling_fail(self):
        f = self.fixture()
        (f.roots.bundle("preflight") / "unexpected.txt").write_text("extra")
        with self.assertRaisesRegex(audit.AuditError, "inventory"):
            f.run("preflight")

    def test_worker_pin_swap_fails(self):
        f = self.fixture()
        with self.assertRaisesRegex(audit.AuditError, "implementation pins"):
            audit.audit_phase(f.roots, "preflight", f.worker, f.outer, _limits=f.limits)

    def test_forged_control_digest_fails(self):
        f = self.fixture("fit")
        path = f.roots.bundle("fit") / "input-lock.json"
        data = audit.json_object(path)
        data["controlReferences"] = []
        write_json(path, data)
        f.repin_manifest("fit")
        with self.assertRaisesRegex(audit.AuditError, "control references"):
            f.run("fit")

    def test_unknown_peak_and_missing_cleanup_cannot_pass(self):
        for change in ("peak", "cleanup"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as folder:
                f = BundleFixture(Path(folder))
                f.build("preflight")
                path = f.roots.bundle("preflight") / "resource.json"
                data = audit.json_object(path)
                if change == "peak":
                    data["stages"][0]["resource"]["peakBytes"] = None
                else:
                    data["stages"][0]["cleanup"]["confirmedStopped"] = False
                write_json(path, data)
                f.repin_manifest("preflight")
                with self.assertRaises(audit.AuditError):
                    f.run("preflight")
                self.assertFalse(f.roots.review("preflight").exists())

    def test_dry_run_bypass_and_label_mount_rejected(self):
        f = self.fixture()
        path = f.roots.bundle("preflight") / "command.json"
        data = audit.json_object(path)
        args = data["sequence"][0]["command"]
        index = args.index("feelm-rec046-spark:local")
        args[index:index] = ["--mount", f"type=bind,source={self.root / 'labels.parquet'},target=/labels,readonly"]
        write_json(path, data)
        f.repin_manifest("preflight")
        with self.assertRaisesRegex(audit.AuditError, "evaluation mount"):
            f.run("preflight")

    def test_duplicate_and_path_traversal_records_rejected(self):
        base = {"path": "safe/file", "bytes": 0, "sha256": "0"*64}
        with self.assertRaises(audit.AuditError):
            audit.set_digest([base, base])
        for name in ("../secret", "/root", "a\\b", "a/labels.parquet", "a/./b", "C:/data"):
            with self.subTest(name=name), self.assertRaises(audit.AuditError):
                audit.set_digest([{**base, "path": name}])

    def test_json_duplicate_keys_rejected(self):
        path = self.root / "duplicate.json"
        path.write_text('{"status":"BLOCK","status":"PASS"}')
        with self.assertRaisesRegex(audit.AuditError, "duplicate JSON"):
            audit.json_object(path)

    def test_native_120_trees_and_threshold_rounding(self):
        make_model(self.root / "model", 120)
        make_threshold(self.root / "threshold.npz", 120)
        model = audit.NativeTrees(self.root / "model", 120)
        result = audit.audit_thresholds(self.root / "threshold.npz", model)
        self.assertEqual(result["rows"], 360)
        with np.load(self.root / "threshold.npz") as z:
            data = {k: z[k].copy() for k in z.files}
        data["features"][2, 0] = .5
        np.savez(self.root / "threshold.npz", **data)
        with self.assertRaisesRegex(audit.AuditError, "below/equal/above"):
            audit.audit_thresholds(self.root / "threshold.npz", model)

    def test_native_cycle_or_tree_count_rejected(self):
        make_model(self.root / "model")
        with self.assertRaises(audit.AuditError):
            audit.NativeTrees(self.root / "model", 120)
        path = self.root / "model/data/part-00000.parquet"
        rows = pq.read_table(path).to_pylist()
        rows[0]["nodeData"]["leftChild"] = 0
        pq.write_table(pa.Table.from_pylist(rows), path)
        with self.assertRaisesRegex(audit.AuditError, "cyclic"):
            audit.NativeTrees(self.root / "model", 2)

    def test_score_label_not_selected_and_uid_swap_rejected(self):
        f = self.fixture("score")
        model = audit.NativeTrees(f.roots.bundle("fit") / "model/native", 2)
        source = f.sources["source/natural-score.parquet"]
        output = f.roots.bundle("score") / "score/predictions.parquet"
        original_read = pq.read_table
        selected = []
        def spy(path, *args, **kwargs):
            if Path(path) == source:
                selected.append(kwargs.get("columns"))
            return original_read(path, *args, **kwargs)
        with mock.patch.object(audit.pq, "read_table", side_effect=spy):
            audit.audit_predictions(source, output, model, f.limits)
        self.assertEqual(selected, [["row_id", "uid", *audit.FEATURES]])
        frame = original_read(output)
        bad = frame.set_column(1, "uid", pa.array(np.roll(frame["uid"].to_numpy(), 1), type=pa.int32()))
        pq.write_table(bad, output)
        with self.assertRaisesRegex(audit.AuditError, "UID swap"):
            audit.audit_predictions(source, output, model, f.limits)

    def test_score_wrong_values_and_label_plan_rejected(self):
        f = self.fixture("score")
        path = f.roots.bundle("score") / "score/analyzed-plan.txt"
        path.write_text(path.read_text() + " label")
        f.repin_manifest("score")
        with self.assertRaisesRegex(audit.AuditError, "contains label"):
            f.run("score")

    def test_publish_race_keeps_existing_review(self):
        f = self.fixture()
        review = f.run("preflight")
        real = audit.rename_no_replace
        def racing(source, target):
            target.write_text("racing writer")
            real(source, target)
        with mock.patch.object(audit, "rename_no_replace", side_effect=racing), self.assertRaises(OSError):
            audit.publish_review(f.roots, "preflight", review)
        self.assertEqual(f.roots.review("preflight").read_text(), "racing writer")
        self.assertFalse(list(f.roots.review("preflight").parent.glob(".*.tmp-*")))

    def test_assert_free_and_no_runner_import(self):
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        self.assertEqual(sum(isinstance(n, ast.Assert) for n in ast.walk(tree)), 0)
        imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        self.assertFalse(any(x in imports for x in ("run_service_v1_b1_gbt", "service_v1_b1_spark_worker", "combination340_models")))
        options = audit.parser().format_help()
        self.assertNotIn("--limits", options)
        self.assertNotIn("dry-run-first-row-group", options)

    def test_score_row_order_and_numerical_forgery_fail(self):
        f = self.fixture("score")
        model = audit.NativeTrees(f.roots.bundle("fit") / "model/native", 2)
        source = f.sources["source/natural-score.parquet"]
        output = f.roots.bundle("score") / "score/predictions.parquet"
        original = pq.read_table(output)
        swapped = original.take(pa.array([1, 0, *range(2, 12)]))
        pq.write_table(swapped, output)
        with self.assertRaisesRegex(audit.AuditError, "out-of-order"):
            audit.audit_predictions(source, output, model, f.limits)
        wrong = original.set_column(2, "prediction", pa.array(np.full(12, 4.), type=pa.float64()))
        pq.write_table(wrong, output)
        with self.assertRaisesRegex(audit.AuditError, "portable parity"):
            audit.audit_predictions(source, output, model, f.limits)

    def test_preflight_cannot_omit_dry_run(self):
        f = self.fixture()
        path = f.roots.bundle("preflight") / "command.json"
        data = audit.json_object(path)
        data["sequence"] = data["sequence"][1:]
        write_json(path, data)
        f.repin_manifest("preflight")
        with self.assertRaisesRegex(audit.AuditError, "stage count"):
            f.run("preflight")

    def test_repointed_mount_fails_even_when_every_source_is_present(self):
        f = self.fixture()
        path = f.roots.bundle("preflight") / "command.json"
        data = audit.json_object(path)
        args = data["sequence"][0]["command"]
        index = next(i for i, value in enumerate(args) if "target=/input/natural-train.parquet" in value)
        args[index] = args[index].replace("target=/input/natural-train.parquet", "target=/input/wrong.parquet")
        write_json(path, data)
        f.repin_manifest("preflight")
        with self.assertRaisesRegex(audit.AuditError, "source/target swapped"):
            f.run("preflight")

    def test_old_shared_input_masked_mount_layout_is_rejected(self):
        f = self.fixture()
        path = f.roots.bundle("preflight") / "command.json"
        data = audit.json_object(path)
        args = data["sequence"][0]["command"]
        index = next(i for i, value in enumerate(args) if "target=/masked,readonly" in value)
        args[index] = args[index].replace(
            "target=/masked,readonly", "target=/input/tmdb-masked-train.parquet,readonly"
        )
        write_json(path, data)
        f.repin_manifest("preflight")
        with self.assertRaisesRegex(audit.AuditError, "source/target swapped"):
            f.run("preflight")

    def test_parent_review_change_blocks_next_phase(self):
        f = self.fixture("fit")
        path = f.roots.review("preflight")
        data = audit.json_object(path)
        data["target"]["outer_runner"]["sha256"] = "a"*64
        write_json(path, data)
        with self.assertRaises(audit.AuditError):
            f.run("fit")

    def test_masked_row_and_star_bits_are_checked(self):
        f = BundleFixture(self.root)
        path = f.sources["source/tmdb-masked-train.parquet"]
        frame = pq.read_table(path)
        values = frame["label"].to_numpy().copy()
        values[0] = 3.25
        pq.write_table(frame.set_column(2, "label", pa.array(values, type=pa.float64())), path)
        with self.assertRaisesRegex(audit.AuditError, "label bit parity"):
            audit.recalculate_identity(f.sources["source/natural-train.parquet"], path, f.limits)

    def test_masked_bundle_directory_extra_file_blocks_review(self):
        f = self.fixture()
        extra = f.sources["source/tmdb-masked-train.parquet"].parent / "unreviewed.bin"
        extra.write_bytes(b"not reviewed")
        with self.assertRaisesRegex(audit.AuditError, "exactly the reviewed three files"):
            f.run("preflight")

    def test_oom_timeout_over_limit_and_wrong_runtime_fail(self):
        f = self.fixture()
        path = f.roots.bundle("preflight") / "resource.json"
        original = audit.json_object(path)
        for key, value in (("oomKilled", True), ("timedOut", True), ("peakBytes", audit.MAX_MEMORY_BYTES+1), ("resourceStatus", "UNKNOWN")):
            with self.subTest(key=key):
                data = copy.deepcopy(original)
                data["stages"][0]["resource"][key] = value
                write_json(path, data)
                f.repin_manifest("preflight")
                with self.assertRaisesRegex(audit.AuditError, "resource unknown"):
                    f.run("preflight")

    def test_float32_midpoint_is_compared_as_binary64(self):
        make_model(self.root / "model", 1)
        # Choose adjacent float32 values whose midpoint rounds up when cast to
        # float32. The upper value must go right under Spark's double comparison.
        lower = np.nextafter(np.float32(.5), np.float32(np.inf))
        upper = np.nextafter(lower, np.float32(np.inf))
        midpoint = (float(lower) + float(upper)) / 2
        self.assertEqual(np.float32(midpoint), upper)
        path = self.root / "model/data/part-00000.parquet"
        rows = pq.read_table(path).to_pylist()
        rows[0]["nodeData"]["split"]["leftCategoriesOrThreshold"] = [midpoint]
        pq.write_table(pa.Table.from_pylist(rows), path)
        model = audit.NativeTrees(self.root / "model", 1)
        x = np.zeros((2, 230), dtype=np.float32)
        x[:, 0] = [lower, upper]
        self.assertEqual(model.predict(x).tolist(), [1., 3.])
        self.assertEqual(model.predict(x.astype(np.float64)).tolist(), [1., 3.])

    def test_forged_manifest_cannot_authorize_other_docker_mount_syntax(self):
        f = self.fixture()
        path = f.roots.bundle("preflight") / "command.json"
        original = audit.json_object(path)
        hidden = str(self.root / "text339/labels.parquet") + ":/leaked:ro"
        for extra in (["--volume", hidden], ["-v", hidden], ["--volume=" + hidden], ["-v" + hidden],
                      ["--volumes-from", "outside"], ["--env-file", str(self.root / "other.env")], ["--privileged"],
                      ["--mount", "type=bind,source=" + str(self.root / "text339") + ",target=/leaked,readonly"]):
            with self.subTest(extra=extra):
                data = copy.deepcopy(original)
                args = data["sequence"][0]["command"]
                index = args.index("feelm-rec046-spark:local")
                args[index:index] = extra
                write_json(path, data)
                f.repin_manifest("preflight")  # Consistent hashes must not make forbidden argv valid.
                with self.assertRaises(audit.AuditError):
                    f.run("preflight")
                self.assertFalse(f.roots.review("preflight").exists())

    def test_review_publication_rehashes_source_control_native_and_score(self):
        f = self.fixture("score")
        review = f.run("score")
        changed_paths = [f.sources["source/natural-train.parquet"], f.sources["source/natural-score.parquet"],
                         f.roots.bundle("preflight") / "run.log", f.roots.review("fit"),
                         f.roots.bundle("fit") / "model/native/data/part-00000.parquet",
                         f.roots.bundle("score") / "score/predictions.parquet"]
        for path in changed_paths:
            with self.subTest(path=path):
                original = path.read_bytes()
                try:
                    path.write_bytes(original + b"mutated after audit")
                    with self.assertRaises(audit.AuditError):
                        audit.publish_review(f.roots, "score", review)
                    self.assertFalse(f.roots.review("score").exists())
                finally:
                    path.write_bytes(original)
        added = f.roots.bundle("fit") / "model/native/unexpected.dat"
        added.write_text("added after audit")
        with self.assertRaisesRegex(audit.AuditError, "dependency changed"):
            audit.publish_review(f.roots, "score", review)
        self.assertFalse(f.roots.review("score").exists())

    def test_source_mutation_while_serializing_review_blocks_atomic_rename(self):
        f = self.fixture()
        review = f.run("preflight")
        source = f.sources["source/natural-train.parquet"]
        original_json = audit.json_object
        def mutate_after_write(path):
            result = original_json(path)
            if "result-review.json.tmp-" in path.name:
                source.write_bytes(source.read_bytes() + b"changed while writing review")
            return result
        with mock.patch.object(audit, "json_object", side_effect=mutate_after_write):
            with self.assertRaisesRegex(audit.AuditError, "immediately before publication"):
                audit.publish_review(f.roots, "preflight", review)
        self.assertFalse(f.roots.review("preflight").exists())
        self.assertFalse(list(f.roots.bundle("preflight").parent.glob(".*result-review.json.tmp-*")))

    def test_predecessor_failure_mutation_blocks_review_publication(self):
        f = self.fixture()
        review = f.run("preflight")
        predecessor = f.roots.predecessor_failure()
        predecessor.write_bytes(predecessor.read_bytes() + b"mutated")
        with self.assertRaisesRegex(audit.AuditError, "dependency changed"):
            audit.publish_review(f.roots, "preflight", review)
        self.assertFalse(f.roots.review("preflight").exists())

    def test_wall_clock_and_monotonic_elapsed_must_agree(self):
        f = self.fixture()
        path = f.roots.bundle("preflight") / "resource.json"
        original = audit.json_object(path)
        cases = [("2026-09-13T02:00:02Z", 1), ("2026-09-13T00:01:02Z", 1),
                 ("2026-09-13T00:00:01Z", 1), ("2026-09-13T01:30:03Z", 5401)]
        for end, elapsed in cases:
            with self.subTest(end=end, elapsed=elapsed):
                data = copy.deepcopy(original)
                data["stages"][-1]["completedAt"] = end
                data["stages"][-1]["elapsedSeconds"] = elapsed
                write_json(path, data)
                f.repin_manifest("preflight")
                with self.assertRaises(audit.AuditError):
                    f.run("preflight")
        data = copy.deepcopy(original)
        data["stages"][-1]["completedAt"] = "2026-09-13T00:00:04Z"
        write_json(path, data)
        f.repin_manifest("preflight")
        self.assertEqual(f.run("preflight")["status"], "PASS")

    def test_real_score_producer_full_fit_mount_contract(self):
        # Read the producer's AST only: no producer import, Spark, Docker or
        # production inputs. This catches producer/fixture contract divergence.
        producer_path = SCRIPT.parent / "run_service_v1_b1_gbt.py"
        tree = ast.parse(producer_path.read_text(encoding="utf-8"))
        score = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "score")
        assignments = {node.targets[0].id: node.value for node in ast.walk(score)
                       if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)}
        expected_mounts = {
            "/app/run_service_v1_b1_gbt.py": "paths.outer_runner",
            "/app/service_v1_b1_spark_worker.py": "paths.spark_worker",
            "/contract/service-v1-b1-spark-runner.md": "paths.plan",
            "/contract/feature-schema.v1.json": "paths.feature_contract",
            "/input/natural-score.parquet": "paths.natural_score",
            "/fit": "paths.bundle('fit')", "/control/fit-review.json": "fit_review",
            "/scratch": "scratch", "/output": "stage",
        }
        mounts = assignments["mounts"]
        self.assertIsInstance(mounts, ast.List)
        self.assertEqual(len(mounts.elts), len(expected_mounts))
        for call in mounts.elts:
            self.assertIsInstance(call, ast.Call)
            self.assertEqual(call.func.id, "Mount")
            target = ast.literal_eval(call.args[1])
            self.assertIn(target, expected_mounts)
            self.assertEqual(ast.dump(call.args[0]), ast.dump(ast.parse(expected_mounts[target], mode="eval").body))
            flags = {item.arg: ast.literal_eval(item.value) for item in call.keywords}
            self.assertEqual(flags, {"readonly": False} if target in ("/scratch", "/output") else {})
        expected_args = ["score", "--score-input", "/input/natural-score.parquet", "--model-dir", "/fit/model/native",
                         "--output", "/output", "--scratch", "/scratch"]
        self.assertEqual(ast.literal_eval(assignments["worker_args"]), expected_args)
        f = self.fixture("score")
        self.assertEqual(f.run("score")["status"], "PASS")
        command_path = f.roots.bundle("score") / "command.json"
        command = audit.json_object(command_path)
        args = command["sequence"][0]["command"]
        args[args.index("--model-dir")+1] = "/model"
        write_json(command_path, command)
        f.repin_manifest("score")
        with self.assertRaisesRegex(audit.AuditError, "argv drift"):
            f.run("score")

    def test_real_fit_producer_recovery_control_order_matches_auditor(self):
        producer_path = SCRIPT.parent / "run_service_v1_b1_gbt.py"
        tree = ast.parse(producer_path.read_text(encoding="utf-8"))
        assignment = next(
            node for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "PREFLIGHT_REVIEW_TARGETS" for target in node.targets)
        )
        producer_targets = ast.literal_eval(assignment.value)
        self.assertEqual(
            list(producer_targets.values()),
            [
                "manifest.json",
                "input-lock.json",
                "recovery-reference.json",
                "partition-identity.json",
                "command.json",
                "resource.json",
                "run.log",
            ],
        )
        f = BundleFixture(self.root)
        mounts = audit.readonly_mount_map(f.roots, "fit")
        controls = {target: source.name for target, source in mounts.items() if target.startswith("/control/preflight/")}
        expected_names = [*producer_targets.values(), f.roots.review("preflight").name]
        self.assertEqual(
            controls,
            {f"/control/preflight/{index:02d}-{name}": name for index, name in enumerate(expected_names)},
        )


if __name__ == "__main__":
    unittest.main()
