from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts/evaluate_service_v1_b1.py"
SPEC = importlib.util.spec_from_file_location("evaluate_service_v1_b1", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load evaluate_service_v1_b1")
ev = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ev
SPEC.loader.exec_module(ev)


def dump_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8", newline="\n")


def root_pin(path: Path, root: Path) -> dict:
    return ev.path_pin(path, root)


def role_digest(frame: pd.DataFrame, calibration_users: int) -> str:
    calibration = frame.loc[frame.role.eq("calibration"), "uid"].astype(int).tolist()
    ordered = sorted(calibration, key=lambda uid: (
        hashlib.sha256(f"cal-select-v1:{uid}".encode()).digest(), uid))
    roles = {uid: "CALIBRATION" if index < calibration_users else "SELECTION"
             for index, uid in enumerate(ordered)}
    roles.update({int(uid): "CONFIRMATION"
                  for uid in frame.loc[frame.role.eq("comparison"), "uid"]})
    raw = "".join(f"{uid},{roles[uid]}\n" for uid in sorted(roles)).encode()
    return hashlib.sha256(raw).hexdigest()


class SyntheticFixture:
    def __init__(self, base: Path) -> None:
        self.root = base
        self.score_rows = 28
        self.catalog_movies = 8
        self.users = list(range(11, 18))
        self.root.mkdir(parents=True, exist_ok=True)
        self.team_root = self.root / "team"
        self.plan = self._text("docs/recommendation/plans/service-v1-b1-spark-runner.md",
                               "reviewed synthetic B1 plan\n")
        self.contract = self._text("team/pipeline/docs/service-v1/EVALUATION.md",
                                   "synthetic evaluation contract\n")
        self.outer = self._text("scripts/run_service_v1_b1_gbt.py", "# outer\n")
        self.worker = self._text("scripts/service_v1_b1_spark_worker.py", "# worker\n")
        self.runner_test = self._text("tests/test_service_v1_b1_gbt_runner.py", "# tests\n")
        self.portable_reader = self._text("scripts/combination340_models.py", "# reader\n")
        self.portable_dependency = self._text("scripts/rec046_common.py", "# dependency\n")

        self.roles = self.root / "inputs/roles.csv"
        role_frame = pd.DataFrame({
            "uid": self.users,
            "h10": [1] * 7,
            "role": ["calibration"] * 4 + ["comparison"] * 3,
        })
        self.roles.parent.mkdir(parents=True, exist_ok=True)
        role_frame.to_csv(self.roles, index=False, lineterminator="\n")
        digest = role_digest(role_frame, 2)
        self.spec = ev.EvaluationSpec(
            score_rows=28, catalog_movies=8, label_rows=29, cap10_targets=28,
            cap10_users=7, extra_labels=1, calibration_users=2,
            selection_users=2, confirmation_users=3, als_movies=2,
            training_rows=6, training_users=2,
            minimum_users=1, minimum_movies=1, minimum_rows=1,
            bootstrap_samples=80, role_digest=digest,
        )

        self.catalog = self.root / "inputs/catalog.parquet"
        ids = np.arange(100, 108, dtype=np.int64)
        pq.write_table(pa.table({"movie_id": pa.array(ids, type=pa.int64()),
                                 "blocked": pa.array([False] * 8),
                                 "released_at_origin": pa.array([True] * 8),
                                 "train_count": pa.array([1] * 8, type=pa.int64()),
                                 "reference_count": pa.array([1] * 8, type=pa.int64()),
                                 "support": pa.array(["W"] * 8)}), self.catalog)
        self.metadata = self.root / "inputs/metadata.parquet"
        pq.write_table(pa.table({"movie_id": pa.array(ids, type=pa.int64())}), self.metadata)

        contexts = []
        score_uids = []
        for index, uid in enumerate(self.users):
            start = index * 4
            contexts.append({"uid": uid, "cap": 10, "h": 1, "start": start,
                             "stop": start + 4, "ei": [0, 1, 2, 3], "oi": [4]})
            score_uids.extend([uid] * 4)
        self.contexts = self.root / "inputs/contexts.json"
        dump_json(self.contexts, contexts)

        self.score_axis = self.root / "outputs/recommendation-evidence/foundation340/RH/score.parquet"
        self.score_axis.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.table({
            "row_id": pa.array(np.arange(28), type=pa.int64()),
            "uid": pa.array(score_uids, type=pa.int32()),
            "label": pa.array(np.zeros(28), type=pa.float64()),
        }), self.score_axis)

        ratings = np.tile(np.array([5.0, 4.0, 2.0, 1.0]), 7)
        label_rows = []
        for uid in self.users:
            for movie, rating in zip(ids[:4], [5.0, 4.0, 2.0, 1.0]):
                label_rows.append((uid, int(movie), rating))
        label_rows.append((999, int(ids[5]), 3.0))
        self.labels = self.root / "inputs/labels.parquet"
        pq.write_table(pa.table({
            "uid": pa.array([r[0] for r in label_rows], type=pa.int64()),
            "movie_id": pa.array([r[1] for r in label_rows], type=pa.int64()),
            "rating": pa.array([r[2] for r in label_rows], type=pa.float64()),
        }), self.labels)
        self.eval_seal = self.root / "inputs/evaluation-seal.json"
        dump_json(self.eval_seal, {"labels_previously_opened": True,
                                   "files": {"labels.parquet": ev.pin(self.labels)}})
        self.ratings = self.root / "inputs/ratings.parquet"
        pq.write_table(pa.table({
            "uid": pa.array([1001, 1001, 1001, 1002, 1002, 1002], type=pa.int32()),
            "movie_id": pa.array([1, 2, 3, 1, 2, 3], type=pa.int32()),
            "rating": pa.array([3.0, 4.0, 2.0, 3.5, 4.5, 1.5], type=pa.float64()),
        }), self.ratings)

        b0 = ratings + np.tile(np.array([.45, -.25, .35, -.4]), 7)
        self.b0 = self.root / "inputs/b0.npy"
        np.save(self.b0, b0.astype(np.float64), allow_pickle=False)
        self.b0_seal = self.root / "inputs/b0-seal.json"
        dump_json(self.b0_seal, {"files": {"GBT120_s339/predictions.npy": ev.pin(self.b0)}})

        self.als_dir = self.root / "outputs/recommendation-evidence/combination340/ALS/item-factors"
        self.als_dir.mkdir(parents=True)
        factor_part = self.als_dir / "part-00000.parquet"
        pq.write_table(pa.table({"id": pa.array(ids[:2], type=pa.int32()),
                                 "features": pa.array([[.1], [.2]], type=pa.list_(pa.float32()))}), factor_part)
        self.artifact_manifest = self.root / "team/pipeline/artifacts/service-v1.json"
        relative = "outputs/recommendation-evidence/combination340/ALS/item-factors/part-00000.parquet"
        dump_json(self.artifact_manifest, {"artifacts": [{"source_path": relative,
                                                           **ev.pin(factor_part)}]})

        training_recipe = self._text("team/pipeline/configs/service-v1/training-recipe.v1.json",
                                     "synthetic recipe\n")
        models_contract = self._text("team/pipeline/docs/service-v1/MODELS.md",
                                     "synthetic model contract\n")
        feature_contract = self._text("team/pipeline/configs/service-v1/feature-schema.v1.json",
                                      "synthetic feature contract\n")
        natural_train = self._text(
            "outputs/recommendation-evidence/foundation340/RH/train.parquet",
            "synthetic natural training rows\n")
        masked_root = self.root / (
            "outputs/recommendation-evidence/service-v1-pretraining-20260913/"
            "b1-masked-views-v1")
        masked_train = self._text_at_return(masked_root / "tmdb-masked-rh230.parquet",
                                            "synthetic masked rows\n")
        masked_manifest = masked_root / "manifest.json"
        dump_json(masked_manifest, {"status": "PASS"})
        views_manifest = masked_root / "views-manifest.json"
        dump_json(views_manifest, {"views": 4})
        masked_review = masked_root.with_name(masked_root.name + "-result-review.json")
        dump_json(masked_review, {"status": "PASS"})

        source_paths = {
            "contract/training-recipe.v1.json": training_recipe,
            "contract/service-v1.json": self.artifact_manifest,
            "contract/MODELS.md": models_contract,
            "contract/feature-schema.v1.json": feature_contract,
            "source/natural-train.parquet": natural_train,
            "source/tmdb-masked-train.parquet": masked_train,
            "source/masked-manifest.json": masked_manifest,
            "source/views-manifest.json": views_manifest,
            "source/masked-review.json": masked_review,
            "implementation/service-v1-b1-spark-runner.md": self.plan,
            "implementation/run_service_v1_b1_gbt.py": self.outer,
            "implementation/service_v1_b1_spark_worker.py": self.worker,
            "implementation/test_service_v1_b1_gbt_runner.py": self.runner_test,
            "implementation/combination340_models.py": self.portable_reader,
            "implementation/rec046_common.py": self.portable_dependency,
            "source/natural-score.parquet": self.score_axis,
        }

        def source_record(logical: str) -> dict:
            if logical == "runtime/docker-image-id":
                raw = ev.IMAGE_ID.encode("utf-8")
                return {"path": logical, "bytes": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest(), "value": ev.IMAGE_ID}
            return {"path": logical, **ev.pin(source_paths[logical])}

        fit_sources = [source_record(name) for name in sorted(ev.TRAINING_SOURCE_PATHS)]
        score_sources = [source_record(name) for name in sorted(ev.SCORE_SOURCE_PATHS)]
        outer_alias = source_record("implementation/run_service_v1_b1_gbt.py")
        worker_alias = source_record("implementation/service_v1_b1_spark_worker.py")
        implementation_digest = ev.canonical_record_set_digest([outer_alias, worker_alias])
        fit_source_digest = ev.canonical_record_set_digest(fit_sources)

        output_parent = self.root / "outputs/recommendation-evidence/service-v1-pretraining-20260913"
        predecessor_failure = output_parent / (ev.PREDECESSOR_RUN_ID + "-preflight-failure.json")
        dump_json(predecessor_failure, {
            "schemaVersion": "feelm-service-v1-b1-failure/1",
            "phase": "preflight", "status": "FAILED", "cleanupComplete": True,
            "containerRun": {
                "dockerState": {"ExitCode": 1, "OOMKilled": False},
                "timedOut": False,
                "stdout": "masked bundle file set is not exactly the reviewed three files",
            },
        })
        predecessor_record = root_pin(predecessor_failure, self.root)

        self.preflight_dir = output_parent / (ev.RUN_ID + "-preflight")
        self.preflight_dir.mkdir(parents=True)
        preflight_control = [predecessor_record]
        preflight_control_digest = ev.canonical_record_set_digest(preflight_control)
        preflight_input_digest = ev.canonical_record_set_digest([*fit_sources, *preflight_control])
        dump_json(self.preflight_dir / "input-lock.json", {
            "schemaVersion": "feelm-service-v1-b1-input-lock/1", "phase": "preflight",
            "trainingSourceRecords": fit_sources, "controlReferences": preflight_control,
            "inputSetSha256": preflight_input_digest,
            "trainingSourceSetSha256": fit_source_digest,
            "controlReferenceSetSha256": preflight_control_digest,
            "evaluationTargetsRead": False,
        })
        recovery = {
            "schemaVersion": "feelm-service-v1-b1-recovery-reference/1",
            "status": "PREDECESSOR_FAILURE_VERIFIED",
            "predecessorRunId": ev.PREDECESSOR_RUN_ID, "runId": ev.RUN_ID,
            "predecessorFailure": predecessor_record,
            "predecessorOuterRunnerSha256": ev.PREDECESSOR_OUTER_RUNNER_SHA256,
            "predecessorSparkWorkerSha256": ev.PREDECESSOR_SPARK_WORKER_SHA256,
            "outerRunnerSha256": outer_alias["sha256"],
            "sparkWorkerSha256": worker_alias["sha256"],
            "failureClass": "MASKED_BUNDLE_CONTAINER_MOUNT_WIRING",
            "modelFitPerformed": False, "fullPreflightPerformed": False,
        }
        dump_json(self.preflight_dir / "recovery-reference.json", recovery)
        dump_json(self.preflight_dir / "partition-identity.json", {"status": "PASS"})
        dump_json(self.preflight_dir / "command.json", {"phase": "preflight"})
        dump_json(self.preflight_dir / "resource.json", {"status": "PASS"})
        self._text_at(self.preflight_dir / "run.log", "preflight complete\n")
        preflight_required = {
            "input-lock.json", "recovery-reference.json", "partition-identity.json",
            "command.json", "resource.json", "run.log",
        }
        self.preflight_manifest = self.preflight_dir / "manifest.json"
        dump_json(self.preflight_manifest, {
            "schemaVersion": "feelm-service-v1-b1-preflight-manifest/1",
            "runId": ev.RUN_ID,
            "status": "B1_FULL_PREFLIGHT_COMPLETE_AWAITING_REVIEW",
            "fitAuthorized": False, "modelFitPerformed": False, "scorePerformed": False,
            "inputSetSha256": preflight_input_digest,
            "trainingSourceSetSha256": fit_source_digest,
            "controlReferenceSetSha256": preflight_control_digest,
            "predecessorFailureSha256": predecessor_record["sha256"],
            "recoveryReferenceSha256": ev.sha256_file(
                self.preflight_dir / "recovery-reference.json"),
            "outerRunnerSha256": outer_alias["sha256"],
            "sparkWorkerSha256": worker_alias["sha256"],
            "implementationSetSha256": implementation_digest,
            "files": {name: ev.path_pin(self.preflight_dir / name, self.preflight_dir)
                      for name in sorted(preflight_required)},
        })
        self.preflight_review = output_parent / (ev.RUN_ID + "-preflight-result-review.json")
        preflight_target_paths = {
            "manifest": self.preflight_manifest,
            "input_lock": self.preflight_dir / "input-lock.json",
            "recovery_reference": self.preflight_dir / "recovery-reference.json",
            "partition_identity": self.preflight_dir / "partition-identity.json",
            "command": self.preflight_dir / "command.json",
            "resource": self.preflight_dir / "resource.json",
            "run_log": self.preflight_dir / "run.log",
            "outer_runner": self.outer,
            "spark_worker": self.worker,
        }
        preflight_target = {name: root_pin(path, self.root)
                            for name, path in preflight_target_paths.items()}
        preflight_target["input_lock"].update({
            "inputSetSha256": preflight_input_digest,
            "trainingSourceSetSha256": fit_source_digest,
        })
        dump_json(self.preflight_review, {
            "schemaVersion": "feelm-service-v1-b1-result-review/1",
            "status": "PASS", "phase": "preflight", "target": preflight_target,
        })

        preflight_controls = [root_pin(path, self.root) for path in sorted(
            (path for path in self.preflight_dir.rglob("*") if path.is_file()),
            key=lambda path: path.as_posix())]
        preflight_controls.append(root_pin(self.preflight_review, self.root))
        fit_control_digest = ev.canonical_record_set_digest(preflight_controls)
        fit_input_digest = ev.canonical_record_set_digest([*fit_sources, *preflight_controls])

        self.fit_dir = output_parent / (ev.RUN_ID + "-fit")
        (self.fit_dir / "model/native/data").mkdir(parents=True)
        model_file = self._text_at_return(self.fit_dir / "model/native/data/part-00000",
                                          "tree model\n")
        model_records = [{"path": "data/part-00000", **ev.pin(model_file)}]
        model_digest = ev.canonical_record_set_digest(model_records)
        model_inventory = self.fit_dir / "model-file-inventory.json"
        dump_json(model_inventory, {
            "schemaVersion": "feelm-service-v1-b1-model-inventory/1",
            "files": model_records,
            "inventorySha256": model_digest,
        })
        dump_json(self.fit_dir / "input-lock.json", {
            "schemaVersion": "feelm-service-v1-b1-input-lock/1", "phase": "fit",
            "trainingSourceRecords": fit_sources, "controlReferences": preflight_controls,
            "inputSetSha256": fit_input_digest,
            "trainingSourceSetSha256": fit_source_digest,
            "controlReferenceSetSha256": fit_control_digest,
            "evaluationTargetsRead": False,
        })
        dump_json(self.fit_dir / "preflight-reference.json", {
            "schemaVersion": "feelm-service-v1-b1-preflight-reference/1",
            "preflightManifest": root_pin(self.preflight_manifest, self.root),
            "preflightReview": root_pin(self.preflight_review, self.root),
            "reviewedArtifacts": preflight_controls,
            "outerRunner": outer_alias, "sparkWorker": worker_alias,
            "implementationSetSha256": implementation_digest,
            "trainingSourceSetSha256": fit_source_digest,
            "fitInputSetSha256": fit_input_digest,
        })
        dump_json(self.fit_dir / "command.json", {"phase": "fit"})
        dump_json(self.fit_dir / "partition-identity.json", {"status": "PASS"})
        dump_json(self.fit_dir / "resolved-estimator.json", {"maxIter": 120})
        np.savez(self.fit_dir / "threshold-fixtures.npz", value=np.array([1.0]))
        dump_json(self.fit_dir / "fit-metrics.json", {"treeCount": 120})
        dump_json(self.fit_dir / "resource.json", {"status": "PASS"})
        self._text_at(self.fit_dir / "run.log", "fit complete\n")
        self.fit_manifest = self.fit_dir / "manifest.json"
        fit_required = {
            "input-lock.json", "preflight-reference.json", "command.json",
            "partition-identity.json", "resolved-estimator.json", "model-file-inventory.json",
            "threshold-fixtures.npz", "fit-metrics.json", "resource.json", "run.log",
            "model/native/data/part-00000",
        }
        dump_json(self.fit_manifest, {
            "schemaVersion": "feelm-service-v1-b1-fit-manifest/1",
            "runId": ev.RUN_ID,
            "status": "B1_MODEL_FIT_COMPLETE_AUDIT_PENDING",
            "scoringAuthorized": False, "readyForService": False,
            "modelFitPerformed": True, "scorePerformed": False,
            "inputSetSha256": fit_input_digest,
            "trainingSourceSetSha256": fit_source_digest,
            "controlReferenceSetSha256": fit_control_digest,
            "preflightManifestSha256": ev.sha256_file(self.preflight_manifest),
            "preflightReviewSha256": ev.sha256_file(self.preflight_review),
            "outerRunnerSha256": outer_alias["sha256"],
            "sparkWorkerSha256": worker_alias["sha256"],
            "implementationSetSha256": implementation_digest,
            "modelInventorySha256": ev.sha256_file(model_inventory),
            "modelFileSetSha256": model_digest,
            "files": {name: ev.path_pin(self.fit_dir / name, self.fit_dir)
                      for name in sorted(fit_required)},
        })
        self.fit_review = output_parent / (ev.RUN_ID + "-fit-result-review.json")
        fit_target_paths = {
            "manifest": self.fit_manifest,
            "input_lock": self.fit_dir / "input-lock.json",
            "preflight_reference": self.fit_dir / "preflight-reference.json",
            "command": self.fit_dir / "command.json",
            "partition_identity": self.fit_dir / "partition-identity.json",
            "resolved_estimator": self.fit_dir / "resolved-estimator.json",
            "model_file_inventory": model_inventory,
            "threshold_fixtures": self.fit_dir / "threshold-fixtures.npz",
            "fit_metrics": self.fit_dir / "fit-metrics.json",
            "resource": self.fit_dir / "resource.json",
            "run_log": self.fit_dir / "run.log",
            "outer_runner": self.outer,
            "spark_worker": self.worker,
        }
        fit_target = {name: root_pin(path, self.root) for name, path in fit_target_paths.items()}
        fit_target["input_lock"].update({"inputSetSha256": fit_input_digest,
                                         "trainingSourceSetSha256": fit_source_digest})
        dump_json(self.fit_review, {
            "schemaVersion": "feelm-service-v1-b1-result-review/1",
            "status": "PASS", "phase": "fit", "target": fit_target,
        })

        fit_controls = [root_pin(path, self.root) for path in sorted(
            (path for path in self.fit_dir.rglob("*") if path.is_file()),
            key=lambda path: path.as_posix())]
        fit_controls.append(root_pin(self.fit_review, self.root))

        self.score_dir = output_parent / (ev.RUN_ID + "-score")
        (self.score_dir / "score").mkdir(parents=True)
        self.predictions = self.score_dir / "score/predictions.parquet"
        b1 = ratings + np.tile(np.array([.02, -.01, .01, -.02]), 7)
        pq.write_table(pa.table({
            "row_id": pa.array(np.arange(28), type=pa.int64()),
            "uid": pa.array(score_uids, type=pa.int32()),
            "prediction": pa.array(b1, type=pa.float64()),
        }), self.predictions)
        dump_json(self.score_dir / "fit-reference.json", {
            "schemaVersion": "feelm-service-v1-b1-fit-reference/1",
            "fitManifest": root_pin(self.fit_manifest, self.root),
            "fitReview": root_pin(self.fit_review, self.root),
            "modelInventory": root_pin(model_inventory, self.root),
            "modelFileSetSha256": model_digest,
            "modelFiles": model_records,
            "outerRunner": outer_alias,
            "sparkWorker": worker_alias,
            "implementationSetSha256": implementation_digest,
        })
        score_source_digest = ev.canonical_record_set_digest(score_sources)
        score_control_digest = ev.canonical_record_set_digest(fit_controls)
        score_input_digest = ev.canonical_record_set_digest([*score_sources, *fit_controls])
        dump_json(self.score_dir / "score-input-lock.json", {
            "schemaVersion": "feelm-service-v1-b1-score-input-lock/1", "phase": "score",
            "scoreSourceRecords": score_sources, "controlReferences": fit_controls,
            "scoreSourceSetSha256": score_source_digest,
            "controlReferenceSetSha256": score_control_digest,
            "inputSetSha256": score_input_digest,
            "evaluationTargetsRead": False,
        })
        dump_json(self.score_dir / "command.json", {"action": "score"})
        self._text_at(self.score_dir / "score/analyzed-plan.txt", "Project row_id,uid,prediction\n")
        dump_json(self.score_dir / "resource.json", {"status": "PASS"})
        self._text_at(self.score_dir / "run.log", "score complete\n")
        required = {
            "fit-reference.json", "score-input-lock.json", "command.json",
            "score/analyzed-plan.txt", "score/predictions.parquet", "resource.json", "run.log",
        }
        self.score_manifest = self.score_dir / "manifest.json"
        dump_json(self.score_manifest, {
            "schemaVersion": "feelm-service-v1-b1-score-manifest/1",
            "runId": ev.RUN_ID,
            "status": "B1_NATURAL_SCORE_COMPLETE_AUDIT_PENDING",
            "evaluationAuthorized": False, "readyForService": False,
            "modelFitPerformed": False, "scorePerformed": True,
            "inputSetSha256": score_input_digest,
            "scoreSourceSetSha256": score_source_digest,
            "controlReferenceSetSha256": score_control_digest,
            "fitManifestSha256": ev.sha256_file(self.fit_manifest),
            "fitReviewSha256": ev.sha256_file(self.fit_review),
            "modelInventorySha256": ev.sha256_file(model_inventory),
            "modelFileSetSha256": model_digest,
            "outerRunnerSha256": outer_alias["sha256"],
            "sparkWorkerSha256": worker_alias["sha256"],
            "implementationSetSha256": implementation_digest,
            "predictionSha256": ev.sha256_file(self.predictions),
            "labelProjected": False,
            "scoreCensus": {"rows": 28, "rowIdMinimum": 0, "rowIdMaximum": 27,
                            "rowIdsUnique": True, "uidAxisEqual": True,
                            "predictionsFinite": True, "singlePhysicalParquetFile": True},
            "files": {name: ev.path_pin(self.score_dir / name, self.score_dir)
                      for name in sorted(required)},
        })
        self.score_review = output_parent / (ev.RUN_ID + "-score-result-review.json")
        target = {
            "manifest": root_pin(self.score_manifest, self.root),
            "fit_reference": root_pin(self.score_dir / "fit-reference.json", self.root),
            "score_input_lock": root_pin(self.score_dir / "score-input-lock.json", self.root),
            "command": root_pin(self.score_dir / "command.json", self.root),
            "analyzed_plan": root_pin(self.score_dir / "score/analyzed-plan.txt", self.root),
            "predictions": root_pin(self.predictions, self.root),
            "resource": root_pin(self.score_dir / "resource.json", self.root),
            "run_log": root_pin(self.score_dir / "run.log", self.root),
            "outer_runner": root_pin(self.outer, self.root),
            "spark_worker": root_pin(self.worker, self.root),
        }
        target["score_input_lock"]["inputSetSha256"] = score_input_digest
        target["score_input_lock"]["scoreSourceSetSha256"] = score_source_digest
        dump_json(self.score_review, {
            "schemaVersion": "feelm-service-v1-b1-result-review/1",
            "status": "PASS", "phase": "score", "target": target,
        })

        self.paths = ev.EvaluationPaths(
            root=self.root, team_root=self.team_root, plan=self.plan,
            artifact_manifest=self.artifact_manifest,
            evaluation_contract=self.contract, contexts=self.contexts, catalog=self.catalog,
            labels=self.labels, evaluation_seal=self.eval_seal, roles=self.roles,
            metadata=self.metadata, score_axis=self.score_axis, ratings=self.ratings,
            b0_predictions=self.b0, b0_seal=self.b0_seal,
            als_factor_dir=self.als_dir, score_manifest=self.score_manifest,
            score_review=self.score_review,
        )
        self.fixed_pins = {name: (ev.pin(getattr(self.paths, name))["bytes"],
                                  ev.pin(getattr(self.paths, name))["sha256"])
                           for name in ev.SOURCE_PINS}

    def _text(self, relative: str, content: str) -> Path:
        path = self.root / relative
        self._text_at(path, content)
        return path

    @staticmethod
    def _text_at(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")

    @classmethod
    def _text_at_return(cls, path: Path, content: str) -> Path:
        cls._text_at(path, content)
        return path

    def run_selection(self, output: Path) -> dict:
        return ev.run_selection(
            self.paths, output,
            expected_plan_sha256=ev.sha256_file(self.plan),
            expected_score_manifest_sha256=ev.sha256_file(self.score_manifest),
            expected_score_review_sha256=ev.sha256_file(self.score_review),
            spec=self.spec, fixed_pins=self.fixed_pins, publish_failure=False)

    def review_selection(self, output: Path) -> Path:
        manifest = output / "manifest.json"
        review_path = output.parent / (output.name + "-result-review.json")
        target = {
            "manifest": root_pin(manifest, self.root),
            "evaluation_input_lock": root_pin(output / "evaluation-input-lock.json", self.root),
            "truth_integrity": root_pin(output / "truth-integrity.json", self.root),
            "score_reference": root_pin(output / "score-reference.json", self.root),
            "command": root_pin(output / "command.json", self.root),
            "role_membership": root_pin(output / "role-membership.csv", self.root),
            "affine": root_pin(output / "affine.json", self.root),
            "metrics": root_pin(output / "metrics.json", self.root),
            "bootstrap_summary": root_pin(output / "bootstrap-summary.json", self.root),
            "strata_census": root_pin(output / "strata-census.json", self.root),
            "gates": root_pin(output / "gates.json", self.root),
            "run_log": root_pin(output / "run.log", self.root),
            "evaluator": root_pin(ev.SCRIPT, self.root),
        }
        dump_json(review_path, {"status": "PASS", "phase": "selection", "target": target})
        return review_path


class EvaluationUnitTests(unittest.TestCase):
    def test_role_split_is_hash_deterministic(self) -> None:
        frame = pd.DataFrame({"uid": [1, 2, 3, 4], "h10": [1] * 4,
                              "role": ["calibration", "calibration", "comparison", "comparison"]})
        digest = role_digest(frame, 1)
        spec = ev.EvaluationSpec(cap10_users=4, calibration_users=1, selection_users=1,
                                 confirmation_users=2, role_digest=digest)
        roles, actual = ev.split_roles(frame, spec)
        self.assertEqual(actual, digest)
        self.assertEqual(sorted(roles.values()), ["CALIBRATION", "CONFIRMATION",
                                                  "CONFIRMATION", "SELECTION"])

    def test_affine_uses_equal_user_weight(self) -> None:
        frame = pd.DataFrame({"uid": [1, 1, 1, 2], "movie_id": [1, 2, 3, 4],
                              "raw": [1.0, 2.0, 3.0, 4.0], "rating": [1.0, 2.0, 3.0, 5.0]})
        fit = ev.fit_affine_user_equal(frame, "raw")
        self.assertEqual(fit["state"], "AFFINE")
        self.assertGreaterEqual(fit["b"], 0)
        # Duplicating user 1 internally does not give that user three times user 2's weight.
        weights = 1 / frame.groupby("uid").uid.transform("size").to_numpy(float)
        weights /= weights.sum()
        self.assertAlmostEqual(weights[:3].sum(), weights[3:].sum())

    def test_affine_rejects_constant_raw(self) -> None:
        frame = pd.DataFrame({"uid": [1, 2], "movie_id": [1, 2],
                              "raw": [3.0, 3.0], "rating": [2.0, 4.0]})
        fit = ev.fit_affine_user_equal(frame, "raw")
        self.assertEqual(fit["state"], "CALIBRATION_UNAVAILABLE")
        self.assertIsNone(fit["a"])

    def test_ndcg_uses_exponential_gain_and_id_tie_break(self) -> None:
        rating = np.array([5.0, 1.0, 4.0])
        ids = np.array([20, 10, 30])
        perfect = ev.ndcg_at(rating, rating, ids, 2)
        reversed_score = np.array([0.0, 9.0, 1.0])
        bad = ev.ndcg_at(rating, reversed_score, ids, 2)
        self.assertAlmostEqual(perfect, 1.0)
        self.assertLess(bad, perfect)

    def test_bootstraps_and_holm_are_deterministic(self) -> None:
        before = np.linspace(.2, 1.0, 35)
        after = before * .8
        first = ev.relative_mse_bootstrap(before, after, samples=100)
        second = ev.relative_mse_bootstrap(before, after, samples=100)
        self.assertEqual(first, second)
        self.assertLess(first["ciHigh"], 0)
        family = ev.sign_flip_holm(before, after, samples=100)
        self.assertEqual(family["rawP"]["B2_MINUS_B0"], 1.0)
        self.assertEqual(family["rawP"]["B3_MINUS_B0"], 1.0)

    def test_bootstrap_uses_exact_seed_and_linear_interval(self) -> None:
        before = np.array([1.0, 2.0, 4.0, 8.0])
        after = np.array([.9, 2.2, 3.5, 7.0])
        samples = 301
        actual = ev.relative_mse_bootstrap(before, after, samples=samples, minimum=1)
        rng = np.random.Generator(np.random.PCG64(ev.USER_BOOTSTRAP_SEED))
        indices = rng.integers(0, len(before), size=(samples, len(before)))
        b = before[indices].mean(axis=1)
        a = after[indices].mean(axis=1)
        draws = (a - b) / b
        expected = np.quantile(draws, [.025, .975], method="linear")
        self.assertEqual(actual["seed"], 339)
        self.assertEqual(actual["samples"], samples)
        self.assertEqual(actual["ciLow"], float(expected[0]))
        self.assertEqual(actual["ciHigh"], float(expected[1]))

    def test_movie_bootstrap_uses_exact_seed_and_linear_interval(self) -> None:
        rows = []
        for uid in (1, 2, 3):
            for movie_id in (10, 20, 30):
                rating = float((uid + movie_id // 10) % 5 + 1)
                rows.append((uid, movie_id, rating, rating + .4, rating + .1))
        frame = pd.DataFrame(rows, columns=["uid", "movie_id", "rating", "b0_cal", "b1_cal"])
        samples = 23
        actual = ev.movie_block_bootstrap(frame, samples=samples, minimum=1)
        users = np.sort(frame.uid.unique())
        movies = np.sort(frame.movie_id.unique())
        ui = np.searchsorted(users, frame.uid.to_numpy())
        mi = np.searchsorted(movies, frame.movie_id.to_numpy())
        y = frame.rating.to_numpy(float)
        se0 = np.square(np.clip(frame.b0_cal.to_numpy(float), .5, 5) - y)
        se1 = np.square(np.clip(frame.b1_cal.to_numpy(float), .5, 5) - y)
        rng = np.random.Generator(np.random.PCG64(ev.MOVIE_BOOTSTRAP_SEED))
        absolute = []
        relative = []
        for _ in range(samples):
            picked = rng.integers(0, len(movies), size=len(movies))
            multiplicity = np.bincount(picked, minlength=len(movies)).astype(float)
            weights = multiplicity[mi]
            denominator = np.bincount(ui, weights=weights, minlength=len(users))
            valid = denominator > 0
            m0 = np.bincount(ui, weights=weights * se0, minlength=len(users))[valid] / denominator[valid]
            m1 = np.bincount(ui, weights=weights * se1, minlength=len(users))[valid] / denominator[valid]
            b0, b1 = float(m0.mean()), float(m1.mean())
            absolute.append(b1 - b0)
            relative.append((b1 - b0) / b0)
        absolute_ci = np.quantile(absolute, [.025, .975], method="linear")
        relative_ci = np.quantile(relative, [.025, .975], method="linear")
        self.assertEqual(actual["seed"], 340)
        self.assertEqual([actual["ciLow"], actual["ciHigh"]], list(map(float, absolute_ci)))
        self.assertEqual([actual["relativeCiLow"], actual["relativeCiHigh"]],
                         list(map(float, relative_ci)))

    def test_sign_flip_holm_has_exact_p_adjustment_and_rejection(self) -> None:
        before = np.linspace(.5, 2.0, 35)
        after = before - .4
        samples = 301
        result = ev.sign_flip_holm(before, after, samples=samples)
        delta = after - before
        rng = np.random.Generator(np.random.PCG64(ev.SIGN_FLIP_SEED))
        signs = rng.integers(0, 2, size=(samples, len(delta)), dtype=np.int8) * 2 - 1
        count = int(((signs * delta).mean(axis=1) <= delta.mean()).sum())
        raw = (1 + count) / (samples + 1)
        self.assertEqual(result["rawP"]["B1_MINUS_B0"], raw)
        self.assertEqual(result["holmAdjustedP"]["B1_MINUS_B0"], min(1.0, 3 * raw))
        self.assertEqual(result["holmAdjustedP"]["B2_MINUS_B0"], 1.0)
        self.assertTrue(result["holmRejectAtAlpha05"]["B1_MINUS_B0"])
        self.assertFalse(result["holmRejectAtAlpha05"]["B2_MINUS_B0"])
        self.assertFalse(result["holmRejectAtAlpha05"]["B3_MINUS_B0"])

    @staticmethod
    def _gate_payload() -> tuple[dict, dict, dict]:
        census = {"strata": {name: {"users": 30, "movies": 30, "rows": 200,
                                     "status": "COMPLETE"} for name in ev.STRATA}}
        user_block = {name: {"relativeMse": {"status": "COMPLETE", "ciHigh": .05}}
                      for name in ev.STRATA}
        user_block["ALL"].update({
            "ndcg2Delta": {"status": "COMPLETE", "ciLow": -.01},
            "low2Delta": {"status": "COMPLETE", "ciHigh": .02},
        })
        affine = {name: {"state": "AFFINE"} for name in ev.MODELS}
        return census, {"userBlock": user_block}, affine

    def test_safety_gate_boundaries_are_inclusive_and_exact(self) -> None:
        census, bootstrap, affine = self._gate_payload()
        self.assertEqual(ev.decide_gates(census, bootstrap, affine)["requiredGate"], "PASS")
        cases = [
            ("relativeMse", np.nextafter(.05, np.inf)),
            ("ndcg2", np.nextafter(-.01, -np.inf)),
            ("low2", np.nextafter(.02, np.inf)),
        ]
        for name, value in cases:
            census, bootstrap, affine = self._gate_payload()
            if name == "relativeMse":
                bootstrap["userBlock"]["ALL"]["relativeMse"]["ciHigh"] = value
            elif name == "ndcg2":
                bootstrap["userBlock"]["ALL"]["ndcg2Delta"]["ciLow"] = value
            else:
                bootstrap["userBlock"]["ALL"]["low2Delta"]["ciHigh"] = value
            with self.subTest(name=name):
                self.assertEqual(ev.decide_gates(census, bootstrap, affine)["requiredGate"], "FAIL")

    def test_each_required_stratum_shortage_is_insufficient(self) -> None:
        for stratum in ev.STRATA:
            census, bootstrap, affine = self._gate_payload()
            census["strata"][stratum]["status"] = "INSUFFICIENT"
            with self.subTest(stratum=stratum):
                result = ev.decide_gates(census, bootstrap, affine)
                self.assertEqual(result["requiredGate"], "INSUFFICIENT")
                self.assertEqual(result["items"][f"support:{stratum}"]["status"], "INSUFFICIENT")

    def test_empty_supported_partition_is_reported_as_insufficient(self) -> None:
        frame = pd.DataFrame({
            "uid": [1] * 10,
            "movie_id": np.arange(10),
            "rating": np.arange(5.0, 0.0, -.5),
            "b0_raw": np.arange(5.0, 0.0, -.5) + .2,
            "b1_raw": np.arange(5.0, 0.0, -.5) + .1,
            "als_supported": [False] * 10,
        })
        affine = {model: {"state": "AFFINE", "a": 0.0, "b": 1.0}
                  for model in ev.MODELS}
        spec = ev.EvaluationSpec(minimum_users=1, minimum_movies=1, minimum_rows=1,
                                 bootstrap_samples=11)
        metrics, _, census, gates = ev.evaluate_phase(frame, affine, spec)
        self.assertEqual(census["strata"]["ALS_TARGET_SUPPORTED"]["status"], "INSUFFICIENT")
        self.assertIsNone(metrics["models"]["B0"]["ALS_TARGET_SUPPORTED"]["userMacroMSE"])
        self.assertEqual(gates["requiredGate"], "INSUFFICIENT")

    def test_top_k_and_all_five_two_item_pages_are_reported(self) -> None:
        frame = pd.DataFrame({
            "uid": [1] * 10,
            "movie_id": np.arange(100, 110),
            "rating": np.arange(5.0, 0.0, -.5),
            "score": np.arange(10.0, 0.0, -1.0),
        })
        summary, users = ev.summarize_model(frame, "score")
        self.assertEqual(set(summary["ranking"]), {"2", "4", "6", "10"})
        self.assertEqual(set(summary["rounds"]), {"1-2", "3-4", "5-6", "7-8", "9-10"})
        self.assertTrue(all(summary["ranking"][str(k)]["ndcgValidUsers"] == 1
                            for k in ev.TOP_K))
        self.assertEqual(users.loc[0, "round1_2_actual"], 4.75)
        self.assertEqual(users.loc[0, "round9_10_actual"], .75)


class EvaluationEndToEndTests(unittest.TestCase):
    def test_selection_then_reviewed_confirmation_are_separate_immutable_bundles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SyntheticFixture(Path(temporary))
            selection = fixture.root / "outputs/selection"
            selection_manifest = fixture.run_selection(selection)
            self.assertEqual(selection_manifest["requiredSelectionGate"], "PASS")
            self.assertEqual(ev._bundle_inventory(selection), ev._selection_files() | {"manifest.json"})
            review = fixture.review_selection(selection)
            confirmation = fixture.root / "outputs/confirmation"
            original_fit = ev.fit_affine_user_equal
            ev.fit_affine_user_equal = lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("confirmation must not refit or feed back labels"))
            try:
                result = ev.run_confirmation(
                    fixture.paths, confirmation,
                    expected_plan_sha256=ev.sha256_file(fixture.plan),
                    expected_score_manifest_sha256=ev.sha256_file(fixture.score_manifest),
                    expected_score_review_sha256=ev.sha256_file(fixture.score_review),
                    selection_manifest_path=selection / "manifest.json",
                    selection_review_path=review,
                    expected_selection_manifest_sha256=ev.sha256_file(selection / "manifest.json"),
                    expected_selection_review_sha256=ev.sha256_file(review),
                    spec=fixture.spec, fixed_pins=fixture.fixed_pins, publish_failure=False)
            finally:
                ev.fit_affine_user_equal = original_fit
            self.assertEqual(result["status"], "B1_CONFIRMATION_COMPLETE_AUDIT_PENDING")
            self.assertEqual(ev._bundle_inventory(confirmation),
                             ev._confirmation_files() | {"manifest.json"})
            selection_lock = ev.read_json(selection / "evaluation-input-lock.json")
            confirmation_lock = ev.read_json(confirmation / "evaluation-input-lock.json")
            self.assertEqual(selection_lock["evaluationSourceSetSha256"],
                             confirmation_lock["evaluationSourceSetSha256"])
            self.assertNotEqual(selection_lock["inputSetSha256"], confirmation_lock["inputSetSha256"])
            source_names = set(selection_lock["files"])
            self.assertIn("inputs/ratings.parquet", source_names)
            self.assertIn("scripts/run_service_v1_b1_gbt.py", source_names)
            self.assertIn("scripts/service_v1_b1_spark_worker.py", source_names)
            evidence_root = "outputs/recommendation-evidence/service-v1-pretraining-20260913/"
            self.assertIn(evidence_root + ev.PREDECESSOR_RUN_ID + "-preflight-failure.json",
                          source_names)
            self.assertIn(evidence_root + ev.RUN_ID + "-preflight/command.json", source_names)
            self.assertIn(evidence_root + ev.RUN_ID
                          + "-fit/model/native/data/part-00000", source_names)
            self.assertIn(evidence_root + ev.RUN_ID + "-fit-result-review.json", source_names)
            truth = ev.read_json(selection / "truth-integrity.json")
            self.assertEqual(truth["trainingUserIsolation"]["projectedColumns"], ["uid"])
            self.assertEqual(truth["trainingUserIsolation"]["trainingEvaluationUidIntersection"], 0)

    def test_confirmation_is_blocked_before_any_source_read_when_review_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SyntheticFixture(Path(temporary))
            selection = fixture.root / "outputs/selection"
            fixture.run_selection(selection)
            review = fixture.review_selection(selection)
            payload = ev.read_json(review)
            payload["status"] = "FAIL"
            dump_json(review, payload)
            # Removing the labels demonstrates the selection-review gate fails first.
            fixture.labels.unlink()
            with self.assertRaisesRegex(ValueError, "selection independent review must PASS"):
                ev.run_confirmation(
                    fixture.paths, fixture.root / "outputs/confirmation",
                    expected_plan_sha256=ev.sha256_file(fixture.plan),
                    expected_score_manifest_sha256=ev.sha256_file(fixture.score_manifest),
                    expected_score_review_sha256=ev.sha256_file(fixture.score_review),
                    selection_manifest_path=selection / "manifest.json",
                    selection_review_path=review,
                    expected_selection_manifest_sha256=ev.sha256_file(selection / "manifest.json"),
                    expected_selection_review_sha256=ev.sha256_file(review),
                    spec=fixture.spec, fixed_pins=fixture.fixed_pins, publish_failure=False)

    def test_full_score_uid_identity_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SyntheticFixture(Path(temporary))
            table = pq.read_table(fixture.predictions)
            uid = np.asarray(table.column("uid").to_numpy(), dtype=np.int32).copy()
            uid[-1] += 1
            pq.write_table(pa.table({"row_id": table.column("row_id"),
                                     "uid": pa.array(uid, type=pa.int32()),
                                     "prediction": table.column("prediction")}), fixture.predictions)
            with self.assertRaisesRegex(ValueError, "natural score axis"):
                ev.load_predictions(fixture.predictions, fixture.score_axis, fixture.spec)

    def test_duplicate_missing_score_row_ids_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SyntheticFixture(Path(temporary))
            table = pq.read_table(fixture.predictions)
            row_id = np.asarray(table.column("row_id").to_numpy(), dtype=np.int64).copy()
            row_id[-1] = row_id[-2]
            pq.write_table(pa.table({
                "row_id": pa.array(row_id, type=pa.int64()),
                "uid": table.column("uid"),
                "prediction": table.column("prediction"),
            }), fixture.predictions)
            with self.assertRaisesRegex(ValueError, "row identity"):
                ev.load_predictions(fixture.predictions, fixture.score_axis, fixture.spec)

    def test_b0_is_indexed_by_validated_row_id_position(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SyntheticFixture(Path(temporary))
            original = np.arange(fixture.spec.score_rows, dtype=np.float64) + .125
            np.save(fixture.b0, original, allow_pickle=False)
            dump_json(fixture.b0_seal,
                      {"files": {"GBT120_s339/predictions.npy": ev.pin(fixture.b0)}})
            roles_frame = pd.read_csv(fixture.roles)
            roles, _ = ev.split_roles(roles_frame, fixture.spec)
            b1 = ev.load_predictions(fixture.predictions, fixture.score_axis, fixture.spec)
            truth, _ = ev.prepare_truth(fixture.paths, fixture.spec, roles, {100, 101}, b1)
            expected = original[truth.row_id.to_numpy(np.int64)]
            np.testing.assert_array_equal(truth.b0_raw.to_numpy(), expected)

    def test_training_ratings_user_overlap_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SyntheticFixture(Path(temporary))
            pq.write_table(pa.table({
                "uid": pa.array([11, 11, 11, 1002, 1002, 1002], type=pa.int32()),
                "movie_id": pa.array([1, 2, 3, 1, 2, 3], type=pa.int32()),
                "rating": pa.array([3.0] * 6, type=pa.float64()),
            }), fixture.ratings)
            rating_pin = ev.pin(fixture.ratings)
            fixture.fixed_pins["ratings"] = (rating_pin["bytes"], rating_pin["sha256"])
            with self.assertRaisesRegex(ValueError, "training/evaluation user overlap"):
                fixture.run_selection(fixture.root / "outputs/selection")

    def test_unrelated_fit_pass_json_cannot_satisfy_score_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SyntheticFixture(Path(temporary))
            dump_json(fixture.fit_review, {"status": "PASS"})
            fit_reference_path = fixture.score_dir / "fit-reference.json"
            reference = ev.read_json(fit_reference_path)
            reference["fitReview"] = root_pin(fixture.fit_review, fixture.root)
            score_manifest = ev.read_json(fixture.score_manifest)
            score_manifest["fitReviewSha256"] = ev.sha256_file(fixture.fit_review)
            score_target = ev.read_json(fixture.score_review)["target"]
            with self.assertRaisesRegex(ValueError, "fit independent review schema drift"):
                ev.verify_fit_reference(fixture.paths, fit_reference_path, reference,
                                        score_manifest, score_target)

    def test_ancestor_file_mutation_is_rehashed_before_labels_are_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SyntheticFixture(Path(temporary))
            with (fixture.preflight_dir / "command.json").open("a", encoding="utf-8") as handle:
                handle.write(" ")
            fixture.labels.unlink()
            with self.assertRaisesRegex(ValueError, "manifest file command.json byte drift"):
                fixture.run_selection(fixture.root / "outputs/selection")

    def test_ancestor_toctou_mutation_is_rehashed_before_selection_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SyntheticFixture(Path(temporary))
            ancestor = fixture.preflight_dir / "command.json"
            original_evaluate = ev.evaluate_phase

            def mutate_after_initial_gate(*args, **kwargs):
                result = original_evaluate(*args, **kwargs)
                with ancestor.open("a", encoding="utf-8") as handle:
                    handle.write(" ")
                return result

            ev.evaluate_phase = mutate_after_initial_gate
            try:
                with self.assertRaisesRegex(ValueError, "source recheck .*command.json byte drift"):
                    fixture.run_selection(fixture.root / "outputs/selection")
            finally:
                ev.evaluate_phase = original_evaluate
            self.assertFalse((fixture.root / "outputs/selection").exists())

    def test_ancestor_extra_file_toctou_blocks_selection_for_every_phase_tree(self) -> None:
        cases = (
            ("preflight", lambda fixture: fixture.preflight_dir / "UNDECLARED.tmp"),
            ("fit-native", lambda fixture: fixture.fit_dir / "model/native/UNDECLARED.tmp"),
            ("score", lambda fixture: fixture.score_dir / "UNDECLARED.tmp"),
        )
        for name, target in cases:
            with self.subTest(phase=name), tempfile.TemporaryDirectory() as temporary:
                fixture = SyntheticFixture(Path(temporary))
                undeclared = target(fixture)
                output = fixture.root / "outputs/selection"
                original_evaluate = ev.evaluate_phase

                def inject_extra_file(*args, **kwargs):
                    result = original_evaluate(*args, **kwargs)
                    fixture._text_at(undeclared, "must block publication\n")
                    return result

                ev.evaluate_phase = inject_extra_file
                try:
                    with self.assertRaisesRegex(ValueError, "bundle physical inventory drift"):
                        fixture.run_selection(output)
                finally:
                    ev.evaluate_phase = original_evaluate
                self.assertFalse(output.exists())

    def test_ancestor_extra_file_toctou_blocks_confirmation_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SyntheticFixture(Path(temporary))
            selection = fixture.root / "outputs/selection"
            fixture.run_selection(selection)
            review = fixture.review_selection(selection)
            confirmation = fixture.root / "outputs/confirmation"
            original_evaluate = ev.evaluate_phase

            def inject_extra_file(*args, **kwargs):
                result = original_evaluate(*args, **kwargs)
                fixture._text_at(fixture.fit_dir / "model/native/UNDECLARED.tmp",
                                 "must block confirmation\n")
                return result

            ev.evaluate_phase = inject_extra_file
            try:
                with self.assertRaisesRegex(ValueError, "bundle physical inventory drift"):
                    ev.run_confirmation(
                        fixture.paths, confirmation,
                        expected_plan_sha256=ev.sha256_file(fixture.plan),
                        expected_score_manifest_sha256=ev.sha256_file(fixture.score_manifest),
                        expected_score_review_sha256=ev.sha256_file(fixture.score_review),
                        selection_manifest_path=selection / "manifest.json",
                        selection_review_path=review,
                        expected_selection_manifest_sha256=ev.sha256_file(
                            selection / "manifest.json"),
                        expected_selection_review_sha256=ev.sha256_file(review),
                        spec=fixture.spec, fixed_pins=fixture.fixed_pins,
                        publish_failure=False)
            finally:
                ev.evaluate_phase = original_evaluate
            self.assertFalse(confirmation.exists())

    def test_score_manifest_run_id_and_schema_are_exact(self) -> None:
        for field, value, message in (
            ("runId", "b1-gbt120-s339-v1-r3", "score manifest run ID drift"),
            ("schemaVersion", "feelm-service-v1-b1-score-manifest/2",
             "score manifest schema drift"),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                fixture = SyntheticFixture(Path(temporary))
                manifest = ev.read_json(fixture.score_manifest)
                manifest[field] = value
                dump_json(fixture.score_manifest, manifest)
                review = ev.read_json(fixture.score_review)
                review["target"]["manifest"] = root_pin(fixture.score_manifest, fixture.root)
                dump_json(fixture.score_review, review)
                with self.assertRaisesRegex(ValueError, message):
                    fixture.run_selection(fixture.root / "outputs/selection")

    def test_control_set_rejects_missing_extra_duplicate_and_rehashed_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SyntheticFixture(Path(temporary))
            lock_path = fixture.score_dir / "score-input-lock.json"
            controls = ev.read_json(lock_path)["controlReferences"]
            fit_files = sorted((path for path in fixture.fit_dir.rglob("*") if path.is_file()),
                               key=lambda path: path.as_posix()) + [fixture.fit_review]

            with self.subTest(case="missing"):
                with self.assertRaisesRegex(ValueError, "file set drift"):
                    ev._verify_control_records(controls[:-1], fit_files,
                                               paths=fixture.paths, owner=lock_path,
                                               label="score fit controls")

            with self.subTest(case="extra"):
                extra_path = fixture._text("inputs/unrelated-control.json", "{}\n")
                with self.assertRaisesRegex(ValueError, "file set drift"):
                    ev._verify_control_records(
                        [*controls, root_pin(extra_path, fixture.root)], fit_files,
                        paths=fixture.paths, owner=lock_path,
                        label="score fit controls")

            with self.subTest(case="duplicate"):
                with self.assertRaisesRegex(ValueError, "duplicate canonical record path"):
                    ev._verify_control_records(
                        [*controls, dict(controls[0])], fit_files,
                        paths=fixture.paths, owner=lock_path,
                        label="score fit controls")

            with self.subTest(case="pin-mismatch"):
                mismatched = [dict(record) for record in controls]
                mismatched[0]["sha256"] = "0" * 64
                with self.assertRaisesRegex(ValueError, "SHA-256 drift"):
                    ev._verify_control_records(mismatched, fit_files,
                                               paths=fixture.paths, owner=lock_path,
                                               label="score fit controls")

    def test_input_set_digest_and_preflight_reference_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SyntheticFixture(Path(temporary))
            lock = ev.read_json(fixture.score_dir / "score-input-lock.json")
            lock["inputSetSha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "score input lock input-set digest drift"):
                ev.verify_record_set_lock(lock, source_key="scoreSourceRecords",
                                          source_digest_key="scoreSourceSetSha256",
                                          label="score input lock")

            preflight_reference_path = fixture.fit_dir / "preflight-reference.json"
            preflight_reference = ev.read_json(preflight_reference_path)
            preflight_reference["fitInputSetSha256"] = "0" * 64
            fit_reference = ev.read_json(fixture.score_dir / "fit-reference.json")
            fit_manifest = ev.read_json(fixture.fit_manifest)
            fit_lock = ev.read_json(fixture.fit_dir / "input-lock.json")
            fit_sources, fit_controls = ev.verify_record_set_lock(
                fit_lock, source_key="trainingSourceRecords",
                source_digest_key="trainingSourceSetSha256", label="fit input lock")
            with self.assertRaisesRegex(ValueError, "preflight reference fit lock digest drift"):
                ev._verify_preflight_ancestry(
                    fixture.paths,
                    preflight_reference_path=preflight_reference_path,
                    preflight_reference=preflight_reference,
                    fit_manifest=fit_manifest,
                    fit_sources=fit_sources,
                    fit_controls=fit_controls,
                    fit_lock=fit_lock,
                    outer_path=fixture.outer,
                    worker_path=fixture.worker,
                    outer_record=fit_reference["outerRunner"],
                    worker_record=fit_reference["sparkWorker"],
                )

    def test_confirmation_rehashes_every_reviewed_selection_file_before_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SyntheticFixture(Path(temporary))
            selection = fixture.root / "outputs/selection"
            fixture.run_selection(selection)
            review = fixture.review_selection(selection)
            manifest_sha = ev.sha256_file(selection / "manifest.json")
            review_sha = ev.sha256_file(review)
            original_evaluate = ev.evaluate_phase

            def mutate_after_initial_gate(*args, **kwargs):
                result = original_evaluate(*args, **kwargs)
                with (selection / "strata-census.json").open("a", encoding="utf-8") as handle:
                    handle.write(" ")
                return result

            ev.evaluate_phase = mutate_after_initial_gate
            try:
                with self.assertRaisesRegex(ValueError, "manifest file strata-census.json byte drift"):
                    ev.run_confirmation(
                        fixture.paths, fixture.root / "outputs/confirmation",
                        expected_plan_sha256=ev.sha256_file(fixture.plan),
                        expected_score_manifest_sha256=ev.sha256_file(fixture.score_manifest),
                        expected_score_review_sha256=ev.sha256_file(fixture.score_review),
                        selection_manifest_path=selection / "manifest.json",
                        selection_review_path=review,
                        expected_selection_manifest_sha256=manifest_sha,
                        expected_selection_review_sha256=review_sha,
                        spec=fixture.spec, fixed_pins=fixture.fixed_pins,
                        publish_failure=False)
            finally:
                ev.evaluate_phase = original_evaluate
            self.assertFalse((fixture.root / "outputs/confirmation").exists())

    def test_truth_rejects_missing_label_and_never_silently_changes_denominator(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SyntheticFixture(Path(temporary))
            labels = pq.read_table(fixture.labels).slice(0, 28)
            pq.write_table(labels, fixture.labels)
            with self.assertRaises(ValueError):
                fixture.run_selection(fixture.root / "outputs/selection")


if __name__ == "__main__":
    unittest.main()
